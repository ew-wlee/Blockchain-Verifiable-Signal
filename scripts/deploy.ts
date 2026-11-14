import fs from "node:fs";

import { artifacts, ethers, network } from "hardhat";
import { formatEther } from "ethers";

import {
  DEPLOYMENT_REPORT_PATH,
  EXPLORER_BASE,
  FINAL_DEPLOYMENT_PATH,
  PREFLIGHT_PATH,
  SEPOLIA_CHAIN_ID,
  STEP3_REPORTS,
  ensureDir,
  explorerAddress,
  explorerTx,
  gitCommit,
  loadExpectedRegistration,
  loadFrozenStep2,
  readJson,
  safeError,
  sha256Text,
  writeBlockedRunRecord,
  writeCanonicalAbi,
  writeJson,
} from "./lib";

let stage = "checks";

async function main(): Promise<void> {
  if (!fs.existsSync(PREFLIGHT_PATH)) {
    throw new Error("preflight report is missing; run npm run preflight");
  }
  const preflight = readJson<Record<string, any>>(PREFLIGHT_PATH);
  if (preflight.status !== "ready" || preflight.passed !== true) {
    throw new Error("preflight is not ready");
  }
  const frozen = loadFrozenStep2();
  const expected = loadExpectedRegistration();
  if (expected.step3CodeCommit !== gitCommit()) {
    throw new Error("Step 3 code commit changed after expected registration was frozen");
  }
  if (fs.existsSync(DEPLOYMENT_REPORT_PATH) || fs.existsSync(FINAL_DEPLOYMENT_PATH)) {
    throw new Error("a deployment record already exists; refusing to redeploy");
  }

  const [signer] = await ethers.getSigners();
  if (!signer) {
    throw new Error("no deployer account is configured");
  }
  const providerAddress = await signer.getAddress();
  if (providerAddress.toLowerCase() !== expected.provider.toLowerCase()) {
    throw new Error("configured signer does not match expected provider");
  }
  const provider = ethers.provider;
  const chainId = Number((await provider.getNetwork()).chainId);
  if (network.name !== "sepolia" || chainId !== SEPOLIA_CHAIN_ID) {
    throw new Error(`unexpected network/chainId: ${network.name}/${chainId}`);
  }
  const balance = await provider.getBalance(providerAddress);
  if (balance < BigInt(preflight.required_balance_wei)) {
    throw new Error(
      `insufficient SepoliaETH: ${formatEther(balance)} < ${preflight.required_balance_eth}`
    );
  }

  const artifact = await artifacts.readArtifact("SignalRegistry");
  const abiSha256 = writeCanonicalAbi(artifact.abi);

  stage = "deploy";
  const factory = await ethers.getContractFactory("SignalRegistry");
  const registry = await factory.deploy();
  const deploymentTx = registry.deploymentTransaction();
  if (!deploymentTx) {
    throw new Error("deployment transaction was not created");
  }
  await registry.waitForDeployment();
  const receipt = await deploymentTx.wait();
  if (!receipt || receipt.status !== 1) {
    throw new Error("deployment transaction did not confirm successfully");
  }
  const contractAddress = await registry.getAddress();
  const code = await provider.getCode(contractAddress);
  if (!code || code === "0x") {
    throw new Error("deployed contract has no bytecode");
  }

  const report = {
    schema_version: "step3-deployment-v1",
    status: "confirmed",
    network: "sepolia",
    chain_id: chainId,
    contract_address: contractAddress,
    deployer_address: providerAddress,
    provider: expected.provider,
    step2_code_commit: frozen.step2CodeCommit,
    step3_code_commit: expected.step3CodeCommit,
    object_id: frozen.objectId,
    data_hash: frozen.dataHash,
    model_hash: frozen.modelHash,
    model_hash_source: "checkpoint_commitment_sha256",
    signal_hash: frozen.signalHash,
    manifest_hash: frozen.manifestHash,
    config_hash: frozen.configHash,
    deployment_tx: receipt.hash,
    deployment_block_number: receipt.blockNumber,
    deployment_gas_used: receipt.gasUsed.toString(),
    contract_code_sha256: sha256Text(code),
    abi_sha256: abiSha256,
    abi_path: "artifacts/step3/SignalRegistry.abi.json",
    explorer: {
      contract: explorerAddress(contractAddress),
      deployment_tx: explorerTx(receipt.hash),
    },
    confirmed_at_utc: new Date().toISOString(),
  };
  ensureDir(STEP3_REPORTS);
  writeJson(DEPLOYMENT_REPORT_PATH, report);

  console.log(`contract_address=${contractAddress}`);
  console.log(`deployment_tx=${receipt.hash}`);
  console.log(`etherscan=${explorerAddress(contractAddress)}`);
  console.log("next=npm run register:sepolia");
}

main().catch((error: unknown) => {
  const message = safeError(error);
  try {
    ensureDir(STEP3_REPORTS);
    writeJson(`${STEP3_REPORTS}/deployment_failure.json`, {
      schema_version: "step3-deployment-failure-v1",
      status: "failed",
      stage,
      code_commit: gitCommit(),
      failed_at_utc: new Date().toISOString(),
      error: message,
    });
    writeBlockedRunRecord(`deployment failed: ${message}`, { stage }, "failed");
  } catch {
    // Preserve the original error even if failure evidence cannot be written.
  }
  console.error(`deployment_failed: ${message}`);
  process.exitCode = 1;
});
