import fs from "node:fs";
import path from "node:path";

import { ethers, network } from "hardhat";

import {
  ABI_PATH,
  CHAIN_COMPARISON_PATH,
  EXPECTED_REGISTRATION_PATH,
  FINAL_DEPLOYMENT_PATH,
  REGISTRATION_REPORT_PATH,
  RUN_RECORD_PATH,
  SEPOLIA_CHAIN_ID,
  STEP3_REPORTS,
  explorerAddress,
  explorerTx,
  gitBranch,
  loadExpectedRegistration,
  loadFrozenStep2,
  readJson,
  safeError,
  sha256File,
  writeBlockedRunRecord,
  writeJson,
} from "./lib";

const HANDOFF_DIR = path.join(STEP3_REPORTS, "..", "handoff");

function sameHex(left: string, right: string): boolean {
  return left.toLowerCase() === right.toLowerCase();
}

async function main(): Promise<void> {
  const checks: Array<{ name: string; passed: boolean; details?: unknown }> = [];
  const addCheck = (name: string, passed: boolean, details?: unknown) => {
    checks.push({ name, passed, details });
  };

  const frozen = loadFrozenStep2();
  const expected = loadExpectedRegistration();
  const deployment = readJson<Record<string, any>>(FINAL_DEPLOYMENT_PATH);
  const registration = readJson<Record<string, any>>(REGISTRATION_REPORT_PATH);
  const chainId = Number((await ethers.provider.getNetwork()).chainId);
  addCheck(
    "network_is_sepolia",
    network.name === "sepolia" && chainId === SEPOLIA_CHAIN_ID,
    { network: network.name, chain_id: chainId }
  );

  const contractAddress = String(deployment.contract_address);
  const code = await ethers.provider.getCode(contractAddress);
  addCheck("contract_code_exists", Boolean(code) && code !== "0x", {
    contract_address: contractAddress,
  });

  const registry = await ethers.getContractAt("SignalRegistry", contractAddress);
  const record = await registry.getSignal(frozen.objectId);
  const onChain = {
    provider: String(record.provider),
    data_hash: String(record.dataHash),
    model_hash: String(record.modelHash),
    signal_hash: String(record.signalHash),
    manifest_hash: String(record.manifestHash),
    timestamp: String(record.timestamp),
  };
  addCheck("provider_matches", sameHex(onChain.provider, expected.provider), {
    actual: onChain.provider,
    expected: expected.provider,
  });
  addCheck("data_hash_matches", sameHex(onChain.data_hash, frozen.dataHash));
  addCheck("model_hash_matches", sameHex(onChain.model_hash, frozen.modelHash));
  addCheck("signal_hash_matches", sameHex(onChain.signal_hash, frozen.signalHash));
  addCheck("manifest_hash_matches", sameHex(onChain.manifest_hash, frozen.manifestHash));
  addCheck(
    "timestamp_is_nonzero",
    BigInt(onChain.timestamp) > 0n,
    { timestamp: onChain.timestamp }
  );

  const deploymentTx = String(deployment.deployment_tx);
  const registrationTx = String(deployment.registration_tx);
  const deploymentReceipt =
    await ethers.provider.getTransactionReceipt(deploymentTx);
  const registrationReceipt =
    await ethers.provider.getTransactionReceipt(registrationTx);
  addCheck(
    "deployment_receipt_confirmed",
    deploymentReceipt !== null &&
      deploymentReceipt.status === 1 &&
      sameHex(String(deploymentReceipt.contractAddress), contractAddress)
  );
  addCheck(
    "registration_receipt_confirmed",
    registrationReceipt !== null &&
      registrationReceipt.status === 1 &&
      sameHex(String(registrationReceipt.to), contractAddress)
  );

  let event: any = undefined;
  let eventIndex = -1;
  registrationReceipt?.logs.forEach((log, index) => {
    try {
      const parsed = registry.interface.parseLog(log);
      if (
        parsed?.name === "SignalRegistered" &&
        sameHex(String(parsed.args.objectId), frozen.objectId)
      ) {
        event = parsed;
        eventIndex = index;
      }
    } catch {
      // Ignore unrelated logs.
    }
  });
  addCheck("registration_event_present", Boolean(event), {
    event_index: eventIndex,
  });
  if (event) {
    addCheck(
      "event_fields_match",
      sameHex(String(event.args.objectId), frozen.objectId) &&
        sameHex(String(event.args.provider), expected.provider) &&
        sameHex(String(event.args.dataHash), frozen.dataHash) &&
        sameHex(String(event.args.modelHash), frozen.modelHash) &&
        sameHex(String(event.args.signalHash), frozen.signalHash) &&
        sameHex(String(event.args.manifestHash), frozen.manifestHash) &&
        String(event.args.timestamp) === onChain.timestamp
    );
  } else {
    addCheck("event_fields_match", false);
  }

  const registrationBlock = registrationReceipt
    ? await ethers.provider.getBlock(registrationReceipt.blockNumber)
    : null;
  addCheck(
    "timestamp_matches_registration_block",
    registrationBlock !== null &&
      BigInt(registrationBlock.timestamp) === BigInt(onChain.timestamp),
    {
      block_timestamp: registrationBlock
        ? String(registrationBlock.timestamp)
        : null,
    }
  );

  const abiSha256 = sha256File(ABI_PATH);
  addCheck("abi_hash_matches_record", abiSha256 === deployment.abi_sha256, {
    abi_sha256: abiSha256,
  });
  addCheck(
    "frozen_config_hash_matches_bundle",
    sameHex(String(deployment.config_hash), frozen.configHash)
  );
  addCheck(
    "deployment_code_commit_matches",
    String(deployment.step3_code_commit) === expected.step3CodeCommit
  );
  addCheck(
    "registration_code_commit_matches",
    String(registration.registration_code_commit) ===
      String(deployment.registration_code_commit)
  );
  addCheck(
    "registration_report_matches",
    sameHex(
      String(registration.registration_tx),
      registrationTx
    ) &&
      String(registration.registration_timestamp) === onChain.timestamp
  );

  const passed = checks.every((check) => check.passed);
  const comparison = {
    schema_version: "step3-chain-comparison-v1",
    passed,
    network: "sepolia",
    chain_id: chainId,
    contract_address: contractAddress,
    provider: expected.provider,
    object_id: frozen.objectId,
    expected: {
      provider: expected.provider,
      data_hash: frozen.dataHash,
      model_hash: frozen.modelHash,
      signal_hash: frozen.signalHash,
      manifest_hash: frozen.manifestHash,
      config_hash: frozen.configHash,
    },
    on_chain: onChain,
    deployment_tx: deploymentTx,
    deployment_block_number: deploymentReceipt
      ? deploymentReceipt.blockNumber
      : null,
    registration_tx: registrationTx,
    registration_block_number: registrationReceipt
      ? registrationReceipt.blockNumber
      : null,
    registration_event_index: eventIndex,
    abi_sha256: abiSha256,
    checks,
    explorer: {
      contract: explorerAddress(contractAddress),
      deployment_tx: explorerTx(deploymentTx),
      registration_tx: explorerTx(registrationTx),
    },
    checked_at_utc: new Date().toISOString(),
  };
  writeJson(CHAIN_COMPARISON_PATH, comparison);
  if (!passed) {
    writeBlockedRunRecord(
      "chain comparison failed",
      { failed_checks: checks.filter((check) => !check.passed).map((check) => check.name) },
      "failed"
    );
    throw new Error("one or more on-chain comparison checks failed");
  }

  const handoffDir = path.resolve(HANDOFF_DIR);
  fs.mkdirSync(handoffDir, { recursive: true });
  const member2 = {
    schema_version: "step3-member2-handoff-v1",
    status: "pending",
    bundle_dir: frozen.bundlePath,
    manifest_path: frozen.manifestPath,
    manifest_hash: frozen.manifestHash,
    object_id: frozen.objectId,
    contract_address: contractAddress,
    abi_path: "artifacts/step3/SignalRegistry.abi.json",
    abi_sha256: abiSha256,
    registration_tx: registrationTx,
    get_signal_query:
      "SignalRegistry.getSignal(bytes32 objectId) returns (address,bytes32,bytes32,bytes32,bytes32,uint64)",
    explorer: {
      contract: explorerAddress(contractAddress),
      registration_tx: explorerTx(registrationTx),
    },
  };
  const member3 = {
    schema_version: "step3-member3-handoff-v1",
    status: "pending",
    step1_code_commit: frozen.step1CodeCommit,
    step2_code_commit: frozen.step2CodeCommit,
    step3_code_commit: expected.step3CodeCommit,
    registration_code_commit: String(deployment.registration_code_commit),
    config_hash: frozen.configHash,
    dataset_hash: frozen.dataHash,
    model_hash: frozen.modelHash,
    signal_hash: frozen.signalHash,
    manifest_hash: frozen.manifestHash,
    object_id: frozen.objectId,
    contract_address: contractAddress,
    deployment_tx: deploymentTx,
    registration_tx: registrationTx,
    abi_path: "artifacts/step3/SignalRegistry.abi.json",
    abi_sha256: abiSha256,
    evidence_path: "artifacts/step3/reports/chain_comparison.json",
  };
  const handoffStatus = {
    schema_version: "step3-handoff-status-v1",
    handoff_files_generated: true,
    member2_status: "pending",
    member3_status: "pending",
    external_acknowledgement: false,
    note:
      "The handoff files are generated. Receipt by Member 2 and Member 3 is not claimed.",
    generated_at_utc: new Date().toISOString(),
  };
  writeJson(path.join(handoffDir, "member2_handoff.json"), member2);
  writeJson(path.join(handoffDir, "member3_handoff.json"), member3);
  writeJson(path.join(handoffDir, "handoff_status.json"), handoffStatus);
  writeJson(path.join(STEP3_REPORTS, "handoff_status.json"), handoffStatus);

  writeJson(RUN_RECORD_PATH, {
    schema_version: "step3-run-record-v1",
    status: "on_chain_verified_handoff_pending",
    step3_member1_completed: true,
    step3_completed: false,
    handoff_pending: true,
    external_handoff_acknowledgement: false,
    branch: gitBranch(),
    code_commit: String(deployment.registration_code_commit),
    deployment_code_commit: expected.step3CodeCommit,
    step2_code_commit: frozen.step2CodeCommit,
    object_id: frozen.objectId,
    contract_address: contractAddress,
    deployment_tx: deploymentTx,
    registration_tx: registrationTx,
    manifest_hash: frozen.manifestHash,
    checks: checks.map((check) => ({
      name: check.name,
      passed: check.passed,
    })),
    blockers: [],
    checked_at_utc: new Date().toISOString(),
  });

  console.log(`chain_comparison=passed`);
  console.log(`contract_address=${contractAddress}`);
  console.log(`object_id=${frozen.objectId}`);
  console.log(`handoff_status=pending`);
}

main().catch((error: unknown) => {
  const message = safeError(error);
  try {
    writeJson(`${STEP3_REPORTS}/chain_comparison_failure.json`, {
      schema_version: "step3-chain-comparison-failure-v1",
      status: "failed",
      failed_at_utc: new Date().toISOString(),
      error: message,
    });
    writeBlockedRunRecord(`on-chain verification failed: ${message}`, {}, "failed");
  } catch {
    // Keep the original error.
  }
  console.error(`onchain_verification_failed: ${message}`);
  process.exitCode = 1;
});
