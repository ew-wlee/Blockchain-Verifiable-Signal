import { execFileSync, spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const PROJECT_ROOT = path.resolve(__dirname, "..");
export const STEP2_BUNDLE = path.join(PROJECT_ROOT, "artifacts", "step2", "bundle");
export const STEP2_REPORTS = path.join(PROJECT_ROOT, "artifacts", "step2", "reports");
export const STEP3_ROOT = path.join(PROJECT_ROOT, "artifacts", "step3");
export const STEP3_REPORTS = path.join(STEP3_ROOT, "reports");
export const DEPLOYMENTS_DIR = path.join(PROJECT_ROOT, "deployments");
export const ABI_PATH = path.join(STEP3_ROOT, "SignalRegistry.abi.json");
export const EXPECTED_REGISTRATION_PATH = path.join(
  STEP3_ROOT,
  "expected_registration.json"
);
export const RUN_RECORD_PATH = path.join(STEP3_REPORTS, "run_record.json");
export const PREFLIGHT_PATH = path.join(STEP3_REPORTS, "preflight.json");
export const DEPLOYMENT_REPORT_PATH = path.join(
  STEP3_REPORTS,
  "deployment.json"
);
export const REGISTRATION_REPORT_PATH = path.join(
  STEP3_REPORTS,
  "registration.json"
);
export const CHAIN_COMPARISON_PATH = path.join(
  STEP3_REPORTS,
  "chain_comparison.json"
);
export const FINAL_DEPLOYMENT_PATH = path.join(
  DEPLOYMENTS_DIR,
  "sepolia.json"
);
export const SEPOLIA_CHAIN_ID = 11155111;
export const EXPLORER_BASE = "https://sepolia.etherscan.io";

export const EXPECTED_STEP2 = {
  objectId:
    "0x1143b163d9a56e29e5714b7cd7ca41c6d05353ceb903704316219060915a4d07",
  dataHash:
    "0xafcbcf10e2a437685b6ea24b79d7f0081140972ab7b81bf01d87a9761cb7b268",
  modelHash:
    "0xb469a7fd68c96558e6498dd01a0d13856b9e91ae2d6e08c8cfab78fcb21d30d2",
  signalHash:
    "0xe37544ef6b88c44577bf8efffc2d956740f513a4c6c4697bd677029db3d6f4fa",
  manifestHash:
    "0x3f3fa32d9c98ba929c9e002f0d9e6c35ec46b42a36344c64bdb8426257870ca6",
  configHash:
    "0xfa70f2fb68fd6463fca5a921f8ce47e54d5b331e197658d751e0a1cb6de9379e",
  payloadCommitment:
    "cf22e40015c603ab2eb904d5bdd85e3604a9972f44523704e4c7108e366ddc6c",
  step2CodeCommit: "dd9223a6c9f516d94355d5e997b1765820889924",
  step1CodeCommit: "183facb43dc83f94f7f6ca4830499a3662ab4c25",
};

export interface FrozenStep2 {
  objectId: string;
  dataHash: string;
  modelHash: string;
  signalHash: string;
  manifestHash: string;
  configHash: string;
  payloadCommitment: string;
  step1CodeCommit: string;
  step2CodeCommit: string;
  manifestPath: string;
  bundlePath: string;
}

export interface ExpectedRegistration extends FrozenStep2 {
  schemaVersion: string;
  provider: string;
  chainId: number;
  step3CodeCommit: string;
  modelHashSource: string;
}

function sortedValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(sortedValue);
  }
  if (value !== null && typeof value === "object") {
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value as Record<string, unknown>).sort()) {
      result[key] = sortedValue((value as Record<string, unknown>)[key]);
    }
    return result;
  }
  return value;
}

export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortedValue(value)) + "\n";
}

export function sha256Text(value: string): string {
  return crypto.createHash("sha256").update(value, "utf8").digest("hex");
}

export function sha256File(filePath: string): string {
  return crypto
    .createHash("sha256")
    .update(fs.readFileSync(filePath))
    .digest("hex");
}

export function ensureDir(directory: string): void {
  fs.mkdirSync(directory, { recursive: true });
}

export function readJson<T>(filePath: string): T {
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
}

export function writeJson(
  filePath: string,
  payload: unknown,
  options: { overwrite?: boolean } = {}
): void {
  const overwrite = options.overwrite ?? true;
  ensureDir(path.dirname(filePath));
  const content = canonicalJson(payload);
  if (!overwrite && fs.existsSync(filePath)) {
    if (fs.readFileSync(filePath, "utf8") !== content) {
      throw new Error(`refusing to overwrite a different frozen file: ${filePath}`);
    }
    return;
  }
  fs.writeFileSync(filePath, content, "utf8");
}

export function writeText(
  filePath: string,
  content: string,
  mode?: number
): void {
  ensureDir(path.dirname(filePath));
  fs.writeFileSync(filePath, content, { encoding: "utf8", mode });
  if (mode !== undefined) {
    fs.chmodSync(filePath, mode);
  }
}

export function toBytes32(hash: string): string {
  const normalized = hash.startsWith("0x") ? hash.slice(2) : hash;
  if (!/^[0-9a-f]{64}$/.test(normalized)) {
    throw new Error(`expected a 32-byte lowercase hash, got ${hash}`);
  }
  return `0x${normalized}`;
}

export function objectIdFromManifest(manifest: Record<string, unknown>): string {
  const identity = { ...manifest };
  delete identity.object_id;
  return `0x${sha256Text(canonicalJson(identity))}`;
}

export function runPythonVerifier(
  manifestHash: string
): Record<string, unknown> {
  const venvPython = path.join(PROJECT_ROOT, ".venv", "bin", "python");
  const python = fs.existsSync(venvPython) ? venvPython : "python3";
  const verifier = path.join(PROJECT_ROOT, "tools", "verify_bundle.py");
  const completed = spawnSync(
    python,
    [
      "-I",
      "-B",
      verifier,
      "--bundle",
      STEP2_BUNDLE,
      "--expected-manifest-hash",
      manifestHash,
    ],
    { cwd: PROJECT_ROOT, encoding: "utf8" }
  );
  if (completed.status !== 0) {
    throw new Error(
      `Step 2 standalone bundle verifier failed: ${completed.stdout || completed.stderr}`
    );
  }
  const report = JSON.parse(completed.stdout) as Record<string, unknown>;
  if (report.passed !== true) {
    throw new Error("Step 2 standalone bundle verifier did not pass");
  }
  return report;
}

export function assertEqual(
  actual: unknown,
  expected: unknown,
  field: string
): void {
  if (actual !== expected) {
    throw new Error(`${field} mismatch: ${String(actual)} != ${String(expected)}`);
  }
}

export function loadFrozenStep2(): FrozenStep2 {
  const manifestPath = path.join(STEP2_BUNDLE, "manifest.json");
  const commitmentPath = path.join(STEP2_REPORTS, "manifest_commitment.json");
  const frozenPath = path.join(STEP2_REPORTS, "bundle_frozen.json");
  const step2RunPath = path.join(STEP2_REPORTS, "run_record.json");
  for (const filePath of [manifestPath, commitmentPath, frozenPath, step2RunPath]) {
    if (!fs.existsSync(filePath)) {
      throw new Error(`required Step 2 artifact is missing: ${filePath}`);
    }
  }

  const manifest = readJson<Record<string, any>>(manifestPath);
  const commitment = readJson<Record<string, any>>(commitmentPath);
  const frozen = readJson<Record<string, any>>(frozenPath);
  const step2Run = readJson<Record<string, any>>(step2RunPath);
  if (frozen.frozen !== true) {
    throw new Error("Step 2 bundle is not frozen");
  }
  if (step2Run.step2_completed !== true || step2Run.status !== "completed") {
    throw new Error("Step 2 run record is not complete");
  }

  const manifestHash = sha256File(manifestPath);
  assertEqual(manifestHash, commitment.manifest_sha256, "manifestHash");
  assertEqual(manifest.object_id, commitment.object_id, "compact objectId");
  assertEqual(objectIdFromManifest(manifest), manifest.object_id, "recomputed objectId");
  runPythonVerifier(manifestHash);

  const result: FrozenStep2 = {
    objectId: toBytes32(String(manifest.object_id)),
    dataHash: toBytes32(String(manifest.dataset_sha256)),
    modelHash: toBytes32(
      String(manifest.model.checkpoint_commitment_sha256)
    ),
    signalHash: toBytes32(String(manifest.signal_sha256)),
    manifestHash: toBytes32(manifestHash),
    configHash: toBytes32(String(manifest.config_sha256)),
    payloadCommitment: String(manifest.payload_commitment),
    step1CodeCommit: String(manifest.step1.code_commit),
    step2CodeCommit: String(manifest.code_commit),
    manifestPath: path.relative(PROJECT_ROOT, manifestPath),
    bundlePath: path.relative(PROJECT_ROOT, STEP2_BUNDLE),
  };
  for (const [field, expected] of Object.entries(EXPECTED_STEP2)) {
    if (field === "payloadCommitment" || field.endsWith("CodeCommit")) {
      continue;
    }
    assertEqual((result as unknown as Record<string, unknown>)[field], expected, field);
  }
  assertEqual(result.payloadCommitment, EXPECTED_STEP2.payloadCommitment, "payloadCommitment");
  assertEqual(result.step1CodeCommit, EXPECTED_STEP2.step1CodeCommit, "step1CodeCommit");
  assertEqual(result.step2CodeCommit, EXPECTED_STEP2.step2CodeCommit, "step2CodeCommit");
  return result;
}

export function gitCommit(): string {
  return execFileSync("git", ["rev-parse", "HEAD"], {
    cwd: PROJECT_ROOT,
    encoding: "utf8",
  }).trim();
}

export function gitBranch(): string {
  return execFileSync("git", ["rev-parse", "--abbrev-ref", "HEAD"], {
    cwd: PROJECT_ROOT,
    encoding: "utf8",
  }).trim();
}

export function expectedRegistration(
  step2: FrozenStep2,
  provider: string
): ExpectedRegistration {
  return {
    schemaVersion: "step3-expected-registration-v1",
    ...step2,
    provider,
    chainId: SEPOLIA_CHAIN_ID,
    step3CodeCommit: gitCommit(),
    modelHashSource: "checkpoint_commitment_sha256",
  };
}

export function writeExpectedRegistration(payload: ExpectedRegistration): void {
  writeJson(EXPECTED_REGISTRATION_PATH, payload, { overwrite: false });
}

export function loadExpectedRegistration(): ExpectedRegistration {
  return readJson<ExpectedRegistration>(EXPECTED_REGISTRATION_PATH);
}

export function loadFinalDeployment(): Record<string, any> {
  if (!fs.existsSync(FINAL_DEPLOYMENT_PATH)) {
    throw new Error("deployments/sepolia.json is missing");
  }
  return readJson<Record<string, any>>(FINAL_DEPLOYMENT_PATH);
}

export function explorerAddress(address: string): string {
  return `${EXPLORER_BASE}/address/${address}`;
}

export function explorerTx(hash: string): string {
  return `${EXPLORER_BASE}/tx/${hash}`;
}

export function safeError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message
    .replace(/https?:\/\/\S+/g, "[redacted-rpc-url]")
    .replace(/0x[0-9a-fA-F]{64}/g, "[redacted-hex-secret-or-hash]");
}

export function writeBlockedRunRecord(
  blocker: string,
  details: Record<string, unknown>,
  status = "blocked"
): void {
  writeJson(RUN_RECORD_PATH, {
    schema_version: "step3-run-record-v1",
    status,
    step3_member1_completed: false,
    step3_completed: false,
    handoff_status: "not_started",
    checked_at_utc: new Date().toISOString(),
    branch: gitBranch(),
    code_commit: gitCommit(),
    blockers: [blocker],
    details,
  });
}

export function abiArtifactPath(): string {
  return path.join(
    PROJECT_ROOT,
    "build",
    "hardhat-artifacts",
    "contracts",
    "SignalRegistry.sol",
    "SignalRegistry.json"
  );
}

export function writeCanonicalAbi(abi: unknown): string {
  writeJson(ABI_PATH, abi);
  return sha256File(ABI_PATH);
}
