import fs from "node:fs";
import path from "node:path";

import { Wallet } from "ethers";

import {
  PROJECT_ROOT,
  STEP3_ROOT,
  canonicalJson,
  ensureDir,
  writeText,
} from "./lib";

const ENV_PATH = path.join(PROJECT_ROOT, ".env");
const DEFAULT_RPC = "https://ethereum-sepolia-rpc.publicnode.com";
const WALLET_RECORD = path.join(STEP3_ROOT, "wallet.json");

function upsertEnv(content: string, key: string, value: string): string {
  const pattern = new RegExp(`^${key}=.*$`, "m");
  if (pattern.test(content)) {
    return content.replace(pattern, `${key}=${value}`);
  }
  const separator = content.length > 0 && !content.endsWith("\n") ? "\n" : "";
  return `${content}${separator}${key}=${value}\n`;
}

async function main(): Promise<void> {
  const existing = process.env.SEPOLIA_PRIVATE_KEY?.trim();
  const wallet = existing ? new Wallet(existing) : Wallet.createRandom();
  const address = await wallet.getAddress();

  let privateKeyStorage = "environment variable";
  if (!existing) {
    const current = fs.existsSync(ENV_PATH)
      ? fs.readFileSync(ENV_PATH, "utf8")
      : "";
    let updated = upsertEnv(current, "SEPOLIA_RPC_URL", DEFAULT_RPC);
    updated = upsertEnv(
      updated,
      "SEPOLIA_PRIVATE_KEY",
      wallet.privateKey
    );
    writeText(ENV_PATH, updated, 0o600);
    privateKeyStorage = ".env (gitignored, mode 600)";
  } else if (fs.existsSync(ENV_PATH)) {
    fs.chmodSync(ENV_PATH, 0o600);
  }

  ensureDir(STEP3_ROOT);
  const payload = {
    schema_version: "step3-test-wallet-v1",
    network: "sepolia",
    chain_id: 11155111,
    address,
    private_key_storage: privateKeyStorage,
    private_key_output: "never printed",
    sharing_rule: "share only through a password manager; never Git or chat",
    created_at_utc: new Date().toISOString(),
  };
  fs.writeFileSync(WALLET_RECORD, canonicalJson(payload), "utf8");

  console.log(`wallet_address=${address}`);
  console.log("private_key=REDACTED");
  console.log("funding_required=SepoliaETH");
  console.log("next=fund this address, then run npm run preflight");
}

main().catch((error: unknown) => {
  console.error("wallet setup failed");
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
