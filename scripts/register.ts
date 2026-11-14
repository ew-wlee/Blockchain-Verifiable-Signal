import fs from "node:fs";

import { ethers, network } from "hardhat";

import {
  ABI_PATH,
  DEPLOYMENT_REPORT_PATH,
  EXPECTED_REGISTRATION_PATH,
  FINAL_DEPLOYMENT_PATH,
  REGISTRATION_REPORT_PATH,
  SEPOLIA_CHAIN_ID,
  STEP3_REPORTS,
  explorerAddress,
  explorerTx,
  gitCommit,
  loadExpectedRegistration,
  loadFrozenStep2,
  readJson,
  safeError,
  sha256File,
  writeBlockedRunRecord,
  writeJson,
} from "./lib";

let stage = "checks";

function sameHex(left: string, right: string): boolean {
  return left.toLowerCase() === right.toLowerCase();
}

async function main(): Promise<void> {
  if (!fs.existsSync(DEPLOYMENT_REPORT_PATH)) {
    throw new Error("deployment report is missing; run npm run deploy:sepolia");
  }
  if (fs.existsSync(REGISTRATION_REPORT_PATH) || fs.existsSync(FINAL_DEPLOYMENT_PATH)) {
    throw new Error("a registration record already exists; refusing to register twice");
  }
  const frozen = loadFrozenStep2();
  const expected = loadExpectedRegistration();
  const deployment = readJson<Record<string, any>>(DEPLOYMENT_REPORT_PATH);
  if (deployment.status !== "confirmed") {
    throw new Error("deployment is not confirmed");
  }
  if (expected.step3CodeCommit !== gitCommit()) {
    throw new Error("Step 3 code commit changed after deployment");
  }

  const [signer] = await ethers.getSigners();
  if (!signer) {
    throw new Error("no registration account is configured");
  }
  const signerAddress = await signer.getAddress();
  if (!sameHex(signerAddress, expected.provider)) {
    throw new Error("signer does not match expected provider");
  }
  const chainId = Number((await ethers.provider.getNetwork()).chainId);
  if (network.name !== "sepolia" || chainId !== SEPOLIA_CHAIN_ID) {
    throw new Error(`unexpected network/chainId: ${network.name}/${chainId}`);
  }
  const contractAddress = String(deployment.contract_address);
  const code = await ethers.provider.getCode(contractAddress);
  if (!code || code === "0x") {
    throw new Error("contract bytecode is missing at the recorded address");
  }
  if (!fs.existsSync(ABI_PATH)) {
    throw new Error("canonical ABI file is missing");
  }
  const abiSha256 = sha256File(ABI_PATH);
  if (abiSha256 !== deployment.abi_sha256) {
    throw new Error("ABI file changed after deployment");
  }

  const registry = await ethers.getContractAt("SignalRegistry", contractAddress);
  let existingProvider = ethers.ZeroAddress;
  try {
    const existing = await registry.getSignal(frozen.objectId);
    existingProvider = String(existing.provider);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (!message.includes("SignalNotFound")) {
      throw error;
    }
  }
  if (!sameHex(existingProvider, ethers.ZeroAddress)) {
    throw new Error("objectId is already registered; use verify-onchain instead");
  }

  stage = "register";
  const tx = await registry.registerSignal(
    frozen.objectId,
    frozen.dataHash,
    frozen.modelHash,
    frozen.signalHash,
    frozen.manifestHash
  );
  const receipt = await tx.wait();
  if (!receipt || receipt.status !== 1) {
    throw new Error("registration transaction did not confirm successfully");
  }

  let event: any = undefined;
  let eventIndex = -1;
  receipt.logs.forEach((log: any, index: number) => {
    try {
      const parsed: any = registry.interface.parseLog(log);
      if (
        parsed?.name === "SignalRegistered" &&
        sameHex(String(parsed.args.objectId), frozen.objectId)
      ) {
        event = parsed;
        eventIndex = index;
      }
    } catch {
      // Ignore logs from other contracts.
    }
  });
  if (!event) {
    throw new Error("SignalRegistered event was not found in the receipt");
  }
  const eventChecks = [
    sameHex(String(event.args.objectId), frozen.objectId),
    sameHex(String(event.args.provider), expected.provider),
    sameHex(String(event.args.dataHash), frozen.dataHash),
    sameHex(String(event.args.modelHash), frozen.modelHash),
    sameHex(String(event.args.signalHash), frozen.signalHash),
    sameHex(String(event.args.manifestHash), frozen.manifestHash),
  ];
  if (!eventChecks.every(Boolean)) {
    throw new Error("SignalRegistered event fields do not match the frozen values");
  }

  const record = await registry.getSignal(frozen.objectId);
  const recordChecks = [
    sameHex(String(record.provider), expected.provider),
    sameHex(String(record.dataHash), frozen.dataHash),
    sameHex(String(record.modelHash), frozen.modelHash),
    sameHex(String(record.signalHash), frozen.signalHash),
    sameHex(String(record.manifestHash), frozen.manifestHash),
    BigInt(record.timestamp) === BigInt(event.args.timestamp),
  ];
  if (!recordChecks.every(Boolean)) {
    throw new Error("on-chain getSignal does not match expected registration");
  }

  const registration = {
    schema_version: "step3-registration-v1",
    status: "confirmed",
    network: "sepolia",
    chain_id: chainId,
    contract_address: contractAddress,
    provider: expected.provider,
    object_id: frozen.objectId,
    data_hash: frozen.dataHash,
    model_hash: frozen.modelHash,
    model_hash_source: "checkpoint_commitment_sha256",
    signal_hash: frozen.signalHash,
    manifest_hash: frozen.manifestHash,
    config_hash: frozen.configHash,
    registration_tx: receipt.hash,
    registration_block_number: receipt.blockNumber,
    registration_gas_used: receipt.gasUsed.toString(),
    registration_event_index: eventIndex,
    registration_timestamp: String(event.args.timestamp),
    event_fields_match: true,
    get_signal_matches: true,
    checked_at_utc: new Date().toISOString(),
  };
  writeJson(REGISTRATION_REPORT_PATH, registration);

  const finalRecord = {
    schema_version: "step3-sepolia-deployment-v1",
    network: "sepolia",
    chain_id: chainId,
    contract_address: contractAddress,
    deployer_address: String(deployment.deployer_address),
    provider: expected.provider,
    step2_code_commit: frozen.step2CodeCommit,
    step3_code_commit: expected.step3CodeCommit,
    bundle_manifest_path: "artifacts/step2/bundle/manifest.json",
    expected_registration_path: "artifacts/step3/expected_registration.json",
    expected_registration_sha256: sha256File(EXPECTED_REGISTRATION_PATH),
    object_id: frozen.objectId,
    data_hash: frozen.dataHash,
    model_hash: frozen.modelHash,
    model_hash_source: "checkpoint_commitment_sha256",
    signal_hash: frozen.signalHash,
    manifest_hash: frozen.manifestHash,
    config_hash: frozen.configHash,
    abi_sha256: abiSha256,
    abi_path: "artifacts/step3/SignalRegistry.abi.json",
    deployment_tx: String(deployment.deployment_tx),
    deployment_block_number: Number(deployment.deployment_block_number),
    deployment_gas_used: String(deployment.deployment_gas_used),
    registration_tx: receipt.hash,
    registration_block_number: receipt.blockNumber,
    registration_gas_used: receipt.gasUsed.toString(),
    registration_event_index: eventIndex,
    registration_timestamp: String(event.args.timestamp),
    contract_code_sha256: String(deployment.contract_code_sha256),
    explorer: {
      contract: explorerAddress(contractAddress),
      deployment_tx: explorerTx(String(deployment.deployment_tx)),
      registration_tx: explorerTx(receipt.hash),
    },
    registered_at_utc: new Date().toISOString(),
  };
  writeJson(FINAL_DEPLOYMENT_PATH, finalRecord);

  console.log(`object_id=${frozen.objectId}`);
  console.log(`registration_tx=${receipt.hash}`);
  console.log(`etherscan=${explorerAddress(contractAddress)}`);
  console.log("next=npm run verify:sepolia");
}

main().catch((error: unknown) => {
  const message = safeError(error);
  try {
    writeJson(`${STEP3_REPORTS}/registration_failure.json`, {
      schema_version: "step3-registration-failure-v1",
      status: "failed",
      stage,
      code_commit: gitCommit(),
      failed_at_utc: new Date().toISOString(),
      error: message,
    });
    writeBlockedRunRecord(`registration failed: ${message}`, { stage }, "failed");
  } catch {
    // Preserve the original error even if failure evidence cannot be written.
  }
  console.error(`registration_failed: ${message}`);
  process.exitCode = 1;
});
