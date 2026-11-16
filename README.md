# Blockchain Verifiable Signal

A reproducible BTC/ETH signal bundle with a Sepolia version registry and an offline retrieval prototype. The retrieval layer implements systematic Reed–Solomon RS(2,4)/RS(3,6), corrupted-fragment filtering, reconstruction, three honest validators with a two-of-three acceptance rule, and a two-copy replication baseline.

This is a course prototype, not a new consensus protocol or a live trading system. On-chain hashes bind versions; they do not establish source truth or trading profitability.

## Quick start

Run from the repository root with Python 3.11 or newer. Part 2 uses only the standard library; no model, wallet, network or package installation is required.

```bash
python3 -m unittest discover -s tests_part2 -v
python3 -m demo.run --scenario success
python3 -m demo.run --scenario insufficient
python3 -m demo.run --scenario deadline
python3 -m demo.run --scenario replication
```

Export the six actually reconstructed files to a **new** directory:

```bash
python3 -m demo.run --scenario success --recover-to /tmp/recovered-signal-bundle
```

`recovered` means the correct bundle was reconstructed; `accepted` additionally requires at least two ACCEPT votes and completion within the deadline. Failure scenarios return exit code 0 when they demonstrate the expected rejection. Simulated milliseconds are not measured chain or CLI latency. The demo verifies a saved registry snapshot and never performs a live RPC query.

## Delivery status

| Component | Status and evidence |
|---|---|
| Member 1 application and registry | Frozen six-file bundle and Sepolia deployment/registration records are included. Local hashes, signals, backtest and a read-only registration receipt/event query were independently checked; see [review](report/03-member1-review.md). |
| Member 2 retrieval | Implemented; 14 current test groups including a cross-platform envelope regression. Historical evidence also covers all 26 minimum-threshold real-bundle subsets, 819 node-state assignments and 16 additional integrity/boundary checks. |
| Full retrieval matrix | Six configurations × 30 seeds × 100 rounds = 18,000 rounds. Actual replay filled every required round field; pre-existing values remained exactly identical. |
| Member 3 evaluation | Complete runner, independent 18,000-round execution, full-record replay comparison, seed-level 95% confidence intervals, two CSV-derived figures, audit, clean offline smoke, English report and editable presentation decks. See [reproduction guide](docs/part3-reproduction.md). |

## Member 3 evaluation

Use Python 3.12 (formal evidence: 3.12.14). Only plotting needs an extra package; inference dependencies are not required.

```bash
python -m pip install -r requirements-part3.lock
python -m unittest discover -s tests_part3 -v
python -m evaluation.smoke --output artifacts/replay/part3-smoke-new.json
python -m evaluation.analyze --source artifacts/part3/formal --output artifacts/replay/part3-analysis-new
python -m evaluation.plot --input artifacts/replay/part3-analysis-new --output artifacts/replay/part3-figures-new
```

To execute a new matrix, use `python -m evaluation.run --profile formal --output artifacts/replay/part3-formal-new`; `pilot` runs 540 attempts. All runs use actual reconstruction, include failed attempts, block network connections and refuse non-empty destinations. Statistical intervals use the 30 seed means, not 3,000 independent rounds. Completion time is conditional on recovery and never fills failures with zero.

- [Experiment methods, results and limitations](report/04-member3-evaluation.md)
- [Integrated English report](report/final-report.md)
- [Recovery and acceptance figure](artifacts/part3/figures/figure1_recovery_acceptance.png)
- [Communication figure](artifacts/part3/figures/figure2_communication.png)
- [Exact seed-level means and 95% intervals](artifacts/part3/analysis/summary.csv)
- [Part 3 delivery and checksums](artifacts/part3/handoff.json)
- [Four-slide midterm PowerPoint with both result charts](report/presentations/midterm.pptx)
- [Final PowerPoint, seven main slides plus two backups](report/presentations/final.pptx)
- [English scripts and questions](report/presentations/speaking-notes.md)
- [Supplied HKUST template provenance](report/presentations/template_source.json)
- [Standalone reproduction package with the template decks](artifacts/part3/reproduction.zip)

Git preserves literal bytes under `artifacts/` and `deployments/`. Source files use LF. The transport ZIP explicitly fixes its creator platform to preserve the original Unix-generated envelope on Windows; frozen files and registered hashes are unchanged.

## Validation results

All configurations use the same frozen payload and twice its storage size. Network/validator assumptions are in [configs/retrieval.json](configs/retrieval.json).

| Scheme | Default recovery | Stress recovery |
|---|---:|---:|
| RS(2,4) | 97.67% | 51.17% |
| RS(3,6) | 98.47% | 42.37% |
| Two full replicas | 95.50% | 62.77% |

Acceptance equaled recovery in this sample; deadline-boundary tests separately cover correct recovery followed by late acceptance. No incorrect object was accepted in the tested rounds. Under stress, replication achieved both higher recovery and lower mean communication cost; RS is not universally better.

- [Validation evidence and field definitions](artifacts/part2-validation-20261115/README.md)
- [18,000 complete round records](artifacts/part2-validation-20261115/rounds.csv)
- [Seed totals](artifacts/part2-validation-20261115/seed_totals.json)
- [Retrieval design and Member 3 API handoff](report/02-member2-retrieval.md)

## Reproduce validation

A small integration pilot:

```bash
python3 -m tools.smoke_retrieval --output /tmp/retrieval-pilot.json
```

The complete validation matrix takes several minutes. Use a new output directory; existing evidence is never overwritten:

```bash
PART2_REPORT_DIR=/tmp/part2-validation-new python3 -m tools.validate_part2
PART2_REPORT_DIR=/tmp/part2-validation-new python3 -m tools.validate_part2_edges
```

The main command includes the unit suite, four deterministic demos, byte-identical recovered-file checks, all 819 node-state assignments, and the 18,000-round matrix. The second command adds 16 boundary/integrity checks. The matrix output stores votes as JSON inside CSV cells: parse with `csv.DictReader`, then `json.loads(row["votes"])`. The main command's checksum list predates the extra edge-check file; regenerate a directory manifest if publishing a new combined evidence set.

## Repository map

| Path | Purpose |
|---|---|
| `src/signal_pipeline/` | Member 1 data, model, signals, backtest and bundle pipeline |
| `contracts/`, `scripts/`, `test/` | Registry contract, Hardhat scripts and tests |
| `retrieval/` | Part 2 codec, bundle validation and single-round simulator |
| `evaluation/`, `tests_part3/` | Part 3 actual experiment runner, audit, seed statistics, plotting and clean replay |
| `demo/` | Offline CLI scenarios |
| `tests/`, `tests_part2/` | Member 1 pytest suite and independent Part 2 unittest suite |
| `configs/` | Frozen application and retrieval configuration |
| `tools/` | Independent verification, pilot, full validation and record-replay utilities |
| `deployments/` | Existing Sepolia registration snapshot |
| `artifacts/step2/`, `artifacts/step3/` | Frozen Member 1 evidence; left unchanged |
| `artifacts/part2/` | Initial integration pilot, demos and Member 1 audit |
| `artifacts/part2-validation-20261115/` | Canonical full validation evidence |
| `artifacts/part3/` | Independent formal execution, evaluation, figures, audit and reproduction package |
| `docs/`, `report/` | Pipeline reference, design and review notes |

## Part 1 generation and limitations

For Member 1's Python 3.11 environment, model dependencies and Hardhat commands, see [pipeline reference](docs/member1-pipeline.md). Those dependencies are not needed for the Part 2 quick start.

A fresh clone does not contain `artifacts/step1`, raw data caches, model weights or the model pin file. The default Step 1 configuration resolves `main` in a fresh model cache rather than necessarily the revision recorded in the frozen bundle. Full from-scratch inference replay has not been independently established here. Formal generation requires a clean tracked Git tree; frozen outputs and deployment scripts refuse overwrite/re-registration. A newly generated bundle cannot silently reuse the old registry hashes.

The retrieval model assumes independent node states, a trusted initialization fragment catalog, an authentic offline registry snapshot, and three honest online validators. It does not implement malicious-validator BFT, incentives, full Proof of Retrievability or a real distributed-network benchmark. Request/response/evidence byte counts exclude transport framing, validator traffic and registry reads.

Private keys, raw/model caches and generated build outputs must remain outside commits. Personal local teaching pages are not part of the shared deliverable.
