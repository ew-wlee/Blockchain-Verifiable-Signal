# Blockchain Verifiable Signal

[中文](#中文) | [English](#english)

---

## 中文

### 这是什么

用固定的 BTC/ETH 历史数据和一个固定 revision 的开源模型（`chronos-t5-small`）生成 buy/hold/sell 信号，把 bundle 里 data、model、signal、manifest 的哈希登记到 Sepolia 的 `SignalRegistry`，让任何人都能离线重算哈希并与链上记录逐项对照。

**边界**：这是一个可验证的数据检索原型，不是新共识协议，也不是交易系统。链上只存哈希，不存行情数据、不存模型权重、不跑推理、不碰资金。

### 当前进度（截至 2026-11-14）

| 阶段 | 状态 | 证据位置 |
| --- | --- | --- |
| Step 1 固定数据、模型与信号 | 已完成；离线重放逐字节一致 | `artifacts/step1/reports/run_record.json`、`reproducibility.json`、`validation.json` |
| Step 2 无泄漏回测与六件 bundle | 已完成并冻结；正向校验、11 个篡改用例、第二环境复核全部通过 | `artifacts/step2/reports/bundle_frozen.json` |
| Step 3 合约、Sepolia 部署与登记 | 已完成；链上链下 18 项核对全部通过 | `deployments/sepolia.json`、`artifacts/step3/reports/chain_comparison.json` |
| 交接 | 待确认；`member2_status`、`member3_status` 仍为 `pending` | `artifacts/step3/handoff/` |

也就是说：应用侧（数据 → 模型 → 信号 → bundle → 链上登记）这条链已经真正跑通并留有证据；RS 纠删码检索恢复、实验统计评估尚未包含在本仓库。

### 冻结标识

| 项 | 值 |
| --- | --- |
| 网络 / chainId | Sepolia / `11155111` |
| 合约地址 | `0x7BA4b62c97Ce9567075736f7cffAEFafAC8C4E41` |
| `objectId` | `0x1143b163d9a56e29e5714b7cd7ca41c6d05353ceb903704316219060915a4d07` |
| `dataHash` | `0xafcbcf10e2a437685b6ea24b79d7f0081140972ab7b81bf01d87a9761cb7b268` |
| `modelHash` | `0xb469a7fd68c96558e6498dd01a0d13856b9e91ae2d6e08c8cfab78fcb21d30d2` |
| `signalHash` | `0xe37544ef6b88c44577bf8efffc2d956740f513a4c6c4697bd677029db3d6f4fa` |
| `manifestHash` | `0x3f3fa32d9c98ba929c9e002f0d9e6c35ec46b42a36344c64bdb8426257870ca6` |
| `configHash` | `0xfa70f2fb68fd6463fca5a921f8ce47e54d5b331e197658d751e0a1cb6de9379e` |
| 部署交易 | `0x4a05fefe27d1d431113318f94f0fa080ebc8c674226bcd3ae449adad589a5f90`（block 12132043，gas 2182442） |
| 登记交易 | `0x742eecdd2cffcbd74fe32b61ec0ee3fff48c29d4163d998afe86af8bff5c5e51`（block 12132049，gas 792291） |
| `provider` | `0xe124651fEA7740D57FCf5962d257528579812e83` |
| 代码版本 | Step 1 `183facb4…`、Step 2 `dd9223a6…`、合约 `d238074f…`、登记脚本 `4e7fe699…` |

其中 `configHash` 与 `payloadCommitment` 不上链，随本地 manifest 与部署记录一起冻结。区块浏览器：<https://sepolia.etherscan.io/address/0x7BA4b62c97Ce9567075736f7cffAEFafAC8C4E41>

### 固定口径

- 数据：Binance Spot，BTCUSDT / ETHUSDT，1h，UTC 窗口 `2024-01-01T00:00:00Z` → `2025-01-01T00:00:00Z`；validation `2024-07-01` → `2024-10-01`，test `2024-10-01` → `2025-01-01`。
- 模型：`amazon/chronos-t5-small`，revision `a971ba21945c4f1796b17a91fe69214b5f4ad472`，context 64，prediction 1，CPU float32，每点 20 个采样取中位数。
- 阈值 τ：只用 validation 的 `|predicted_return|` 中位数，按半数取偶（`ROUND_HALF_EVEN`）保留 12 位小数，冻结为 `0.001923089035`；test 从不参与选阈值。
- 信号：`predicted_return > τ` 为 buy，`< -τ` 为 sell，其余（含恰好等于边界）为 hold。
- 回测：信号在下一根 K 线开盘执行、收盘结算；buy 目标仓位 1，sell 目标仓位 0，hold 延续；只在换仓时收 10 bps；validation 与 test 各自独立从空仓开始。
- bundle：固定六件（`dataset.csv`、`signals.csv`、`backtest_summary.json`、`config.json`、`model_reference.json`、`manifest.json`），只含前五件的内容哈希。
- 链上：`registerSignal` 一次性写入、同一 `objectId` 不可覆盖，`getSignal` 只读查询，无 owner、无升级、无资金逻辑。

### 仓库组成

| 路径 | 内容 |
| --- | --- |
| `src/signal_pipeline/` | Step 1/2 的生成、校验与 CLI |
| `configs/` | `step1.yaml`、`step2.yaml` 固定输入 |
| `tools/verify_bundle.py` | 独立 bundle 校验器，只依赖标准库，不 import 生成器包 |
| `contracts/SignalRegistry.sol` | 链上登记合约 |
| `scripts/` | `preflight`、`deploy`、`register`、`verify-onchain` 等 Hardhat 脚本 |
| `test/` | 合约与冻结输入测试（Hardhat） |
| `tests/` | Step 1/2 的 Python 测试 |
| `artifacts/step1`、`step2`、`step3` | 冻结产物与各阶段报告 |
| `deployments/sepolia.json` | 最终部署与登记记录 |
| `data/raw`、`.cache/` | 原始行情缓存与模型快照（不入 Git） |
| `report/` | 组内思路、答辩与论文素材 |

### 复现

Python 侧（要求 Python 3.11）：

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"        # 完整解析记录见 requirements.lock
.venv/bin/signal-pipeline prepare-data   # 只抓取并校验固定窗口
.venv/bin/signal-pipeline run            # 写入 artifacts/step1，要求目标目录为空
.venv/bin/signal-pipeline reproduce      # 离线重跑到 artifacts/replay/step1 并逐字节比对
.venv/bin/signal-pipeline verify         # 复核产物哈希清单与校验报告

.venv/bin/signal-pipeline step2-build     # 冻结输入、跑回测、生成六件 bundle
.venv/bin/signal-pipeline step2-verify    # 正向校验 + 11 个篡改用例 + 第二环境复核
.venv/bin/signal-pipeline step2-reproduce # 重建到 artifacts/replay/step2 并逐字节比对
.venv/bin/signal-pipeline step2-status    # 只读查看当前状态
```

Node 侧（合约与 Sepolia，Node 22）：

```bash
npm install
npm run compile
npm test
cp .env.example .env   # 填 SEPOLIA_RPC_URL 与 SEPOLIA_PRIVATE_KEY
npm run preflight      # 记录预期登记值与余额检查
npm run deploy:sepolia
npm run register:sepolia
npm run verify:sepolia # 只读核对并生成交接文件
```

注意：`run`、`step2-build`、`step2-reproduce` 都拒绝写入非空目录，`deploy`、`register` 也拒绝在已有记录时重复执行。当前 `artifacts/` 与 `deployments/` 是冻结证据，重新生成前必须先移走旧目录，不能就地覆盖。

### 独立校验（不信任生成器）

```bash
tmp=$(mktemp -d)
cp -R artifacts/step2/bundle "$tmp/bundle"
python3 -I -B tools/verify_bundle.py \
  --bundle "$tmp/bundle" \
  --expected-manifest-hash 3f3fa32d9c98ba929c9e002f0d9e6c35ec46b42a36344c64bdb8426257870ca6
```

退出码 0 表示六件文件齐全、每个文件哈希、`payload_commitment`、`objectId`、`manifestHash` 全部重算一致；任何字节改动、删文件、改路径、改顺序、换 `objectId` 都会失败。

### 已知限制

1. `artifacts/step1/` 目前未纳入 Git，克隆后若要跑 `step2-build`，需先按上面的命令重建 Step 1，并核对得到相同的 `combined_sha256`。
2. `artifacts/step2/reports/bundle_frozen.json` 中的 `next_stage` 仍是 Step 2 冻结时刻的措辞（"Step 3 is intentionally not started"），Step 3 随后已完成；历史文件按原样保留。
3. `scripts/lib.ts` 的 `EXPECTED_STEP2` 硬编码了当前 objectId、五个哈希、`payloadCommitment` 与两个 commit。换 bundle 版本必须同步更新，否则脚本会直接报错——这是刻意的版本闸门。
4. `deployment.json` 的 `contract_code_sha256` 是对 RPC 返回的十六进制文本做 SHA256，不是对字节码 bytes，复算时需使用同一口径。
5. `artifacts/step3/reports/registration_failure.json` 保留了一次真实失败（`execution reverted`）。原因是登记脚本对 `SignalNotFound` 的判断方式不可靠，修复见 `4e7fe699…`；合约字节自 `d238074f…` 起未再改动。
6. 登记的 `provider` 是专用测试钱包，私钥只在 `.env`（不入 Git），仓库内只有地址。
7. 回测只用于检查信号与成本规则，不代表真实收益；链上记录只证明"某个地址在某时刻登记了这组哈希"，不证明数据来源真实、模型正确或策略有效。

---

## English

### What this is

A verifiable signal bundle built from a fixed BTC/ETH history window and a pinned revision of `chronos-t5-small`. The hashes of the bundle's data, model, signal, and manifest files are registered in `SignalRegistry` on Sepolia, so anyone can recompute them offline and compare them field by field with the on-chain record.

**Scope**: this is a data-retrieval prototype with on-chain provenance, not a new consensus protocol and not a trading system. Only hashes are stored on-chain: no market data, no model weights, no inference, no funds.

### Current status (2026-11-14)

| Stage | Status | Evidence |
| --- | --- | --- |
| Step 1 — frozen data, model, signals | Complete; offline replay is byte-identical | `artifacts/step1/reports/run_record.json`, `reproducibility.json`, `validation.json` |
| Step 2 — leakage-free backtest and six-file bundle | Complete and frozen; positive checks, 11 tamper cases, and a second-environment review all pass | `artifacts/step2/reports/bundle_frozen.json` |
| Step 3 — contract, Sepolia deployment and registration | Complete; 18 on-chain/off-chain checks pass | `deployments/sepolia.json`, `artifacts/step3/reports/chain_comparison.json` |
| Handoff | Awaiting acknowledgement; `member2_status` and `member3_status` are `pending` | `artifacts/step3/handoff/` |

In other words, the application-side path (data → model → signals → bundle → on-chain registration) is implemented end to end with evidence. Reed-Solomon retrieval and the experiment/statistics evaluation are not part of this repository yet.

### Frozen identifiers

| Item | Value |
| --- | --- |
| Network / chainId | Sepolia / `11155111` |
| Contract | `0x7BA4b62c97Ce9567075736f7cffAEFafAC8C4E41` |
| `objectId` | `0x1143b163d9a56e29e5714b7cd7ca41c6d05353ceb903704316219060915a4d07` |
| `dataHash` | `0xafcbcf10e2a437685b6ea24b79d7f0081140972ab7b81bf01d87a9761cb7b268` |
| `modelHash` | `0xb469a7fd68c96558e6498dd01a0d13856b9e91ae2d6e08c8cfab78fcb21d30d2` |
| `signalHash` | `0xe37544ef6b88c44577bf8efffc2d956740f513a4c6c4697bd677029db3d6f4fa` |
| `manifestHash` | `0x3f3fa32d9c98ba929c9e002f0d9e6c35ec46b42a36344c64bdb8426257870ca6` |
| `configHash` | `0xfa70f2fb68fd6463fca5a921f8ce47e54d5b331e197658d751e0a1cb6de9379e` |
| Deployment tx | `0x4a05fefe27d1d431113318f94f0fa080ebc8c674226bcd3ae449adad589a5f90` (block 12132043, gas 2182442) |
| Registration tx | `0x742eecdd2cffcbd74fe32b61ec0ee3fff48c29d4163d998afe86af8bff5c5e51` (block 12132049, gas 792291) |
| `provider` | `0xe124651fEA7740D57FCf5962d257528579812e83` |
| Code revisions | Step 1 `183facb4…`, Step 2 `dd9223a6…`, contract `d238074f…`, registration script `4e7fe699…` |

`configHash` and `payloadCommitment` stay off-chain and are frozen with the local manifest and deployment record. Explorer: <https://sepolia.etherscan.io/address/0x7BA4b62c97Ce9567075736f7cffAEFafAC8C4E41>

### Fixed rules

- Data: Binance Spot, BTCUSDT / ETHUSDT, 1h, UTC window `2024-01-01T00:00:00Z` → `2025-01-01T00:00:00Z`; validation `2024-07-01` → `2024-10-01`, test `2024-10-01` → `2025-01-01`.
- Model: `amazon/chronos-t5-small` at revision `a971ba21945c4f1796b17a91fe69214b5f4ad472`, context 64, prediction 1, CPU float32, median of 20 samples per step.
- Tau: median of `|predicted_return|` on validation only, rounded half-even to 12 decimals, frozen at `0.001923089035`. Test never influences tau.
- Signals: buy when `predicted_return > tau`, sell when `< -tau`, hold otherwise (including exact boundaries).
- Backtest: the signal executes at the next bar's open and settles at its close; buy targets position 1, sell targets 0, hold carries the previous position; 10 bps is charged only when the position changes; validation and test start flat independently.
- Bundle: exactly six files (`dataset.csv`, `signals.csv`, `backtest_summary.json`, `config.json`, `model_reference.json`, `manifest.json`); only the five content files are hashed into `files[]`.
- On-chain: `registerSignal` writes once per `objectId` and cannot be overwritten; `getSignal` is read-only; no owner, no upgrade hook, no funds.

### Repository layout

| Path | Contents |
| --- | --- |
| `src/signal_pipeline/` | Step 1/2 generation, verification, and CLI |
| `configs/` | Fixed inputs `step1.yaml` and `step2.yaml` |
| `tools/verify_bundle.py` | Standalone bundle verifier (stdlib only, no generator imports) |
| `contracts/SignalRegistry.sol` | On-chain registry contract |
| `scripts/` | Hardhat scripts for preflight, deploy, register, verify-onchain |
| `test/` | Contract and frozen-input tests (Hardhat) |
| `tests/` | Python tests for Step 1/2 |
| `artifacts/step1`, `step2`, `step3` | Frozen artifacts and per-stage reports |
| `deployments/sepolia.json` | Final deployment and registration record |
| `data/raw`, `.cache/` | Raw market cache and model snapshot (not in Git) |
| `report/` | Team notes, defence, and paper material |

### Reproducing

Python (requires 3.11):

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e ".[dev]"        # full resolved set is recorded in requirements.lock
.venv/bin/signal-pipeline prepare-data   # fetch and validate the fixed window only
.venv/bin/signal-pipeline run            # write artifacts/step1; target directory must be empty
.venv/bin/signal-pipeline reproduce      # replay offline into artifacts/replay/step1 and compare bytes
.venv/bin/signal-pipeline verify         # re-check the artifact manifest and validation report

.venv/bin/signal-pipeline step2-build     # freeze inputs, run the backtest, build the six-file bundle
.venv/bin/signal-pipeline step2-verify    # positive checks + 11 tamper cases + second environment
.venv/bin/signal-pipeline step2-reproduce # rebuild into artifacts/replay/step2 and compare bytes
.venv/bin/signal-pipeline step2-status    # read-only status
```

Node (contract and Sepolia, Node 22):

```bash
npm install
npm run compile
npm test
cp .env.example .env   # set SEPOLIA_RPC_URL and SEPOLIA_PRIVATE_KEY
npm run preflight      # record expected registration values and check balance
npm run deploy:sepolia
npm run register:sepolia
npm run verify:sepolia # read-only comparison, writes the handoff files
```

Note: `run`, `step2-build`, and `step2-reproduce` refuse to write into a non-empty directory, and `deploy`/`register` refuse to run when a record already exists. The current `artifacts/` and `deployments/` are frozen evidence; move the old directories aside instead of overwriting them.

### Independent verification (without trusting the generator)

```bash
tmp=$(mktemp -d)
cp -R artifacts/step2/bundle "$tmp/bundle"
python3 -I -B tools/verify_bundle.py \
  --bundle "$tmp/bundle" \
  --expected-manifest-hash 3f3fa32d9c98ba929c9e002f0d9e6c35ec46b42a36344c64bdb8426257870ca6
```

Exit code 0 means all six files are present and every file hash, the `payload_commitment`, the `objectId`, and the `manifestHash` recompute correctly. Any changed byte, deleted file, altered path or order, or replaced `objectId` makes it fail.

### Known limitations

1. `artifacts/step1/` is not tracked in Git. After cloning, running `step2-build` requires regenerating Step 1 first and confirming the same `combined_sha256`.
2. `artifacts/step2/reports/bundle_frozen.json` still carries the Step 2 freeze-time wording `"next_stage": "Step 3 is intentionally not started"`. Step 3 has since completed; the historical file is kept as written.
3. `EXPECTED_STEP2` in `scripts/lib.ts` hardcodes the current objectId, five hashes, the `payloadCommitment`, and two commits. A new bundle version must update them, otherwise the scripts fail fast — this is a deliberate version gate.
4. `contract_code_sha256` in `deployment.json` is the SHA256 of the hex string returned by the RPC, not of the bytecode bytes. Use the same convention when recomputing.
5. `artifacts/step3/reports/registration_failure.json` keeps a real failed attempt (`execution reverted`), caused by unreliable `SignalNotFound` detection in the registration script and fixed in `4e7fe699…`. The contract bytecode has not changed since `d238074f…`.
6. The registered `provider` is a dedicated test wallet. Its private key lives only in `.env` (not in Git); the repository holds the address only.
7. The backtest only exercises signal and cost rules; it is not a claim about returns. The on-chain record proves that some address registered this set of hashes at some time — it does not prove that the data source is honest, the model is correct, or the strategy is profitable.
