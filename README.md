# Verifiable Signal Data Service — Step 1

This repository contains **Member 1, Step 1** of the course project: a fixed,
auditable, and reproducible application-input pipeline. It fetches a frozen
Binance Spot window, pins an immutable Chronos checkpoint, generates next-hour
price predictions, freezes a validation-only threshold `tau`, and emits
`buy` / `hold` / `sell` signals with evidence.

Step 1 deliberately stops before backtesting, the six-file bundle, manifest,
Solidity contracts, Sepolia registration, Reed-Solomon retrieval, or any
experiments. Those belong to later steps.

## Fixed inputs

| Item | Frozen value |
| --- | --- |
| Source | Binance Spot public klines |
| Symbols | `BTCUSDT`, `ETHUSDT` |
| Interval | `1h`, UTC |
| Window | `[2024-01-01T00:00:00Z, 2025-01-01T00:00:00Z)` |
| Validation | `[2024-07-01T00:00:00Z, 2024-10-01T00:00:00Z)` |
| Test | `[2024-10-01T00:00:00Z, 2025-01-01T00:00:00Z)` |
| History for context | `2024-01-01` through `2024-06-30` |
| Model | `amazon/chronos-t5-small` |
| Revision | Immutable commit resolved once and recorded at runtime |
| Context / horizon | `64` hours / `1` hour |
| Inference | CPU, `float32`, eval/no-grad, fixed seed and batch order |
| Prediction | Median of `20` sampled next-hour closes |
| Return | `predicted_median_close / current_close - 1` |
| `tau` | `median(abs(validation predicted_return))`, both symbols pooled |
| Signal rule | `> tau` buy, `< -tau` sell, otherwise hold (equality holds) |

The model registry's `main` alias is resolved to a full commit SHA only on the
first run. The resolved SHA is cached under `.cache/` and recorded in
`artifacts/step1/config.json` and `model_reference.json`; later runs reuse it.
No `latest` data window is used.

## Setup

Python 3.11 is required.

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
```

Run the tests:

```bash
.venv/bin/pytest
```

## Commands

All commands are run from this directory. A formal run requires a committed
code version with no tracked changes, so that `code_commit` identifies the
exact source used to generate the artifacts.

Cache and validate the fixed Binance window without producing formal signals:

```bash
.venv/bin/signal-pipeline prepare-data
```

Generate the formal Step 1 artifacts exactly once:

```bash
.venv/bin/signal-pipeline run
```

`run` refuses to overwrite a non-empty `artifacts/step1`. If it fails, it writes
`artifacts/step1/reports/run_record.json` with the real failed stage and error;
remove that incomplete attempt before retrying.

Perform the required isolated offline reproduction:

```bash
.venv/bin/signal-pipeline reproduce
```

This reruns the whole pipeline into `artifacts/replay/step1` with sockets blocked
and Hugging Face offline mode enabled. It compares every deterministic artifact
byte hash with the formal run. The formal run is not overwritten.

Verify the formal artifact hash manifest:

```bash
.venv/bin/signal-pipeline verify
```

## Outputs

| Path | Meaning |
| --- | --- |
| `artifacts/step1/dataset.csv` | Cleaned fixed-window hourly BTC/ETH dataset |
| `artifacts/step1/predictions.csv` | Next-hour predictions for validation and test, intermediate evidence |
| `artifacts/step1/signals.csv` | Final time-aligned `buy` / `hold` / `sell` signals |
| `artifacts/step1/config.json` | Frozen window, splits, model revision, inference parameters, and `tau` |
| `artifacts/step1/model_reference.json` | Checkpoint file hashes, commitment, revision, and inference parameters |
| `artifacts/step1/artifact_hashes.json` | SHA256 for every deterministic formal artifact |
| `artifacts/step1/reports/data_quality.json` | Raw/clean row counts, duplicate/gap handling, cache hashes |
| `artifacts/step1/reports/tau_freeze.json` | Validation-only formula, counts, and frozen `tau` |
| `artifacts/step1/reports/validation.json` | Independent data/model/alignment/threshold checks |
| `artifacts/step1/reports/reproducibility.json` | Offline replay and per-file hash comparison |
| `artifacts/step1/reports/run_record.json` | Code commit, environment, timings, cache use, and final status |

`data/raw/` stores the original monthly Binance responses and metadata.
`.cache/huggingface/` stores the pinned model snapshot. Both are large,
reproducible inputs and are excluded from Git; their exact hashes are recorded
in the formal evidence.

## Data and reproducibility rules

- Data is cached in fixed monthly chunks. A complete cache is reused without
  any network request; `--offline` forbids socket connections entirely.
- Exact duplicate rows are removed and counted. Conflicting duplicates, missing
  hours, out-of-window rows, invalid OHLC values, and unsupported precision
  block the pipeline. There is no interpolation.
- A prediction at time `t` uses the 64 closes ending at `t - 1h`. Its signal
  timestamp is `t`; the context end is stored separately and checked.
- `tau` is computed from a validation-only data frame. Test predictions are not
  passed to the threshold function and do not affect `tau`.
- CSV and JSON use fixed columns/key order, fixed numeric precision, LF line
  endings, and SHA256 over final file bytes.
- Dynamic run times and network counts live only in `run_record.json`, not in
  deterministic artifacts.

## Evidence boundary

The hashes demonstrate that the recorded version of the data, configuration,
model reference, predictions, and signals is internally consistent. They do not
prove that the market data is true, that the model is correct, or that the
signals are profitable. No backtest or on-chain evidence is produced here.
