# Reproduce the Member 3 evaluation

Run from the repository root with Python 3.12.14 (the version used for both the original and independent formal runs). This environment is separate from Member 1's Python 3.11 inference environment. Do not install the full project or download the model to reproduce retrieval/evaluation.

## Environment

```bash
python3.12 -m venv .venv-part3
# Linux/macOS:
source .venv-part3/bin/activate
# Windows PowerShell: .\.venv-part3\Scripts\Activate.ps1
python -m pip install -r requirements-part3.lock
```

Only plotting requires Matplotlib. The runner, audit, aggregation, confidence intervals, tests and smoke use the standard library. A dependency lock accompanies the plotting requirements. On a computer that blocks virtualenv launcher executables, use an allowed Python 3.12 executable with the same installed dependencies.

In this Windows workspace, application control also blocked newly downloaded NumPy DLLs. The figures were therefore rendered with the already installed Python 3.10.14, Matplotlib 3.10.9 and NumPy 2.2.6. Plotting only reads the audited CSV; all experiment execution and statistical aggregation use Python 3.12.14. The actual renderer versions and input CSV hash are recorded in `artifacts/part3/figures/render_environment.json`. Plot images are not claimed to be byte-identical across operating systems or font installations.

The root `.gitattributes` prevents Git from changing any frozen artifact/deployment bytes on checkout. The transport ZIP fixes its creator platform, timestamps, file order and permissions. Do not rewrite CSV/JSON through a spreadsheet or text editor before verification.

## Verify and analyze existing evidence

```bash
python -m unittest discover -s tests_part2 -v
python -m unittest discover -s tests_part3 -v
python -m evaluation.analyze --source artifacts/part2-validation-20261115 --output artifacts/replay/part3-published-analysis
python -m evaluation.plot --input artifacts/replay/part3-published-analysis --output artifacts/replay/part3-published-figures
```

The first analysis verifies every file in the source checksum manifest, audits all 18,000 CSV rows and cross-checks per-seed and configuration totals. Figure outputs are two PNG and two SVG files. Use a new destination for every command; existing evidence is protected.

## Run independent experiments

```bash
python -m evaluation.run --profile pilot --output artifacts/replay/part3-pilot
python -m evaluation.analyze --profile pilot --source artifacts/replay/part3-pilot --output artifacts/replay/part3-pilot-analysis

python -m evaluation.run --profile formal --output artifacts/replay/part3-formal
python -m evaluation.analyze --source artifacts/replay/part3-formal --output artifacts/replay/part3-formal-analysis
python -m tools.compare_part3_records --current artifacts/replay/part3-formal --output artifacts/replay/part3-formal-comparison.json
```

The pilot has 6 x 3 x 30 = 540 attempts. The formal run has 6 x 30 x 100 = 18,000 attempts and can take several minutes. It actually reconstructs and validates returned bytes on every successful attempt. All attempts, including failures, remain in the CSV. The comparison checks every original published field; the independent run_id is the only ignored identity label. Non-time fields must match exactly. Simulated completion times allow an absolute 1e-12 ms tolerance for Windows/macOS math-library rounding; the report records the count and maximum observed difference.

## Clean offline smoke

```bash
python -m evaluation.smoke --output artifacts/replay/part3-smoke.json
```

This makes a temporary source-only copy with the frozen bundle and anchor, excludes local environments/Git/model files, blocks network connections inside both runners, and performs two independent 36-round runs and analyses. It requires byte-identical CSV/statistical outputs and exact agreement with the corresponding published records. It tests the complete runner-to-statistics path, not a mock simulator. Plotting is a separate check because it needs Matplotlib.

## Evidence map

| Path under artifacts/part3 | Meaning |
|---|---|
| `formal/rounds.csv` | Independent actual 18,000-round execution |
| `formal/run.json` | Python, profiles, source/input hashes and shared retrieval configuration |
| `analysis/seed_metrics.csv` | 180 seed aggregates; statistical sample units |
| `analysis/summary.csv` | 24 rows: four metrics x six configurations, with seed-level 95% intervals |
| `analysis/audit.json` | Coverage, invariants, counts and source checksum verification |
| `analysis/provenance.json` | Canonical input and analysis definitions |
| `figures/` | Two main figures in PNG and SVG; CSV-derived |
| `smoke.json` | Two clean offline executions and published-record agreement |
| `full_replay.json` | Comparison of all 18,000 records with Part 2 |
| `tests_part2.txt`, `tests_part3.txt` | Actual regression/unit test logs |
| `handoff.json`, `checksums.json` | Delivery index and checksums |
| `reproduction.zip` | Source, frozen inputs, results and current template decks; no secrets or model cache |

The English analysis is `report/04-member3-evaluation.md`; the integrated course report is `report/final-report.md`. The current decks are `report/presentations/midterm.pptx` and `final.pptx`, using the supplied HKUST template. The four-slide midterm combines the title and system overview, then presents the offline demo and both result charts. Final slides 8-9 are backups and are excluded from the 5-minute main talk.

## Rebuild the PowerPoint files

`scripts/build-evaluation-deck.mjs` uses `@oai/artifact-tool` supplied by the Codex artifact runtime. Set `RUNTIME_NODE_MODULES` to that runtime's module directory, `RUNTIME_PYTHON` to its Python executable, and `PRESENTATION_SKILL_DIR` to the installed Presentations skill. Run with the runtime Node executable from the repository root. Native charts/tables remain editable; accompanying scientific PNG/SVG figures retain the full confidence-interval display. The deck builder records the source summary in speaker notes and validates package structure and layout before delivery.

```bash
node scripts/build-evaluation-deck.mjs artifacts/replay/part3-presentations-new /path/to/PPT_template_14.potx
```

The original supplied POTX is needed only for programmatic deck authoring. The editable decks contain their used masters, layouts and campus images. Unused template layouts and unreferenced media are removed before package/layout validation. This optional authoring environment and the POTX are not needed to rerun experiments or rebuild the scientific figures. Template name, original checksum and retained design elements are recorded in `report/presentations/template_source.json`.
