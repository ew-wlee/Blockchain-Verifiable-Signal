import { ethers, network } from "hardhat";
import { formatEther, parseEther } from "ethers";

import {
  EXPECTED_REGISTRATION_PATH,
  PREFLIGHT_PATH,
  SEPOLIA_CHAIN_ID,
  expectedRegistration,
  loadFrozenStep2,
  safeError,
  writeBlockedRunRecord,
  writeExpectedRegistration,
  writeJson,
} from "./lib";

const DEFAULT_MINIMUM_ETH = "0.01";

async function main(): Promise<void> {
  const frozen = loadFrozenStep2();
  const signers = await ethers.getSigners();
  if (signers.length === 0) {
    throw new Error(
      "no Sepolia account configured; run npm run wallet:setup or set SEPOLIA_PRIVATE_KEY"
    );
  }
  const signer = signers[0];
  const provider = ethers.provider;
  const networkInfo = await provider.getNetwork();
  const chainId = Number(networkInfo.chainId);
  const providerAddress = await signer.getAddress();
  const balance = await provider.getBalance(providerAddress);
  const minimum = parseEther(
    process.env.SEPOLIA_MIN_BALANCE_ETH?.trim() || DEFAULT_MINIMUM_ETH
  );
  const feeData = await provider.getFeeData();
  const gasPrice =
    feeData.maxFeePerGas ?? feeData.gasPrice ?? 0n;
  const estimatedGasCeiling = 1_000_000n;
  const estimatedCost = gasPrice * estimatedGasCeiling;
  const requiredBalance = estimatedCost > minimum ? estimatedCost : minimum;
  const expected = expectedRegistration(frozen, providerAddress);

  const checks = [
    {
      name: "step2_bundle_frozen_and_verified",
      passed: true,
      details: { object_id: frozen.objectId, manifest_hash: frozen.manifestHash },
    },
    {
      name: "network_is_sepolia",
      passed: network.name === "sepolia" && chainId === SEPOLIA_CHAIN_ID,
      details: { network: network.name, chain_id: chainId },
    },
    {
      name: "account_present",
      passed: providerAddress !== ethers.ZeroAddress,
      details: { provider: providerAddress },
    },
    {
      name: "balance_covers_estimated_cost",
      passed: balance >= requiredBalance,
      details: {
        balance_wei: balance.toString(),
        required_wei: requiredBalance.toString(),
      },
    },
    {
      name: "expected_registration_frozen",
      passed: true,
      details: { path: "artifacts/step3/expected_registration.json" },
    },
  ];
  const passed = checks.every((check) => check.passed);
  const blockers = checks
    .filter((check) => !check.passed)
    .map((check) => check.name);

  if (checks[1].passed) {
    writeExpectedRegistration(expected);
  }

  const report = {
    schema_version: "step3-preflight-v1",
    status: passed ? "ready" : "blocked",
    checked_at_utc: new Date().toISOString(),
    network: network.name,
    chain_id: chainId,
    provider: providerAddress,
    balance_wei: balance.toString(),
    balance_eth: formatEther(balance),
    required_balance_wei: requiredBalance.toString(),
    required_balance_eth: formatEther(requiredBalance),
    gas_price_wei: gasPrice.toString(),
    estimated_gas_ceiling: estimatedGasCeiling.toString(),
    step2: {
      object_id: frozen.objectId,
      data_hash: frozen.dataHash,
      model_hash: frozen.modelHash,
      model_hash_source: "checkpoint_commitment_sha256",
      signal_hash: frozen.signalHash,
      manifest_hash: frozen.manifestHash,
      config_hash: frozen.configHash,
      step2_code_commit: frozen.step2CodeCommit,
    },
    checks,
    blockers,
    passed,
  };
  writeJson(PREFLIGHT_PATH, report);

  if (!passed) {
    writeBlockedRunRecord(
      `preflight blocked: ${blockers.join(", ")}`,
      {
        provider: providerAddress,
        balance_eth: formatEther(balance),
        required_balance_eth: formatEther(requiredBalance),
        chain_id: chainId,
      }
    );
    throw new Error(
      `preflight blocked; fund ${providerAddress} with SepoliaETH and rerun preflight`
    );
  }

  console.log(`preflight=ready`);
  console.log(`provider=${providerAddress}`);
  console.log(`balance_eth=${formatEther(balance)}`);
}

main().catch((error: unknown) => {
  console.error(`preflight_failed: ${safeError(error)}`);
  process.exitCode = 1;
});
