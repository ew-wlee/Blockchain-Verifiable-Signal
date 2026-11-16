# Full Part 2 validation evidence

This is the canonical, complete 18,000-round dataset. Inputs are Member 1's existing frozen bundle and saved Sepolia registration. No Part 1 inference or chain write was performed.

## Coverage

- 13 Part 2 test groups passed.
- All 6 RS(2,4) and 20 RS(3,6) minimum-threshold subsets recovered the real bundle.
- All 819 honest/silent/invalid node-state assignments matched their expected outcomes.
- 16 extra integrity/boundary checks passed.
- Four CLI scenarios were deterministic across two independent runs; exported recovered files matched source bytes.
- Six configurations × 30 seeds × 100 rounds = 18,000 unique round records. Zero incorrect objects were accepted.
- Missing export fields were obtained by actual deterministic replay. Every pre-existing field matched the original compact CSV exactly; they were not inferred or filled from success flags.

## Files

| File | Content |
|---|---|
| `rounds.csv` | Canonical complete records, including run_id, fragment_count, requested_fragments and votes |
| `summary.json` | Results, assumptions, environment, source/config fingerprints and completion audit reference |
| `seed_totals.json` | Per-seed counts and byte totals for Member 3 |
| `unit_tests.txt` | Actual unit-test execution log |
| `demo_*.json` | Success, insufficient, deadline and replication traces |
| `bundle_verification.json` | Independent bundle checks; its directory locator is normalized to a repository-relative path |
| `recovered_files_comparison.json` | SHA256 comparison of source and reconstructed files |
| `additional_edge_checks.json` | Results of the 16 extra checks |
| `csv_audit.json` | Unique rows, seed coverage, summary agreement and required-field checks |
| `record_completion_audit.json` | Actual replay provenance and old/new CSV hashes; per-configuration temporary CSV hashes are retained as execution metadata |
| `checksums.json` | SHA256 for each published file in this directory except the checksum file itself |

Superseded compact CSVs, script snapshots and personal Chinese study notes were retained in the maintainer's local archive, not deleted. They are not required to consume this canonical dataset. The original compact CSV hash remains in the completion audit. Historical full-validation source fingerprints may reference the old artifact-directory script layout; current runnable tools are under `tools/`.

## Round fields

- Identity: run_id, config_id, seed, round.
- Encoding: k, n, fragment_count, storage_bytes, storage_overhead.
- Retrieval: requested_fragments, valid_fragments, failure_reason.
- Integrity: payload_sha256, reconstructed_sha256, reconstructed_manifest_hash, hash_match.
- Acceptance: votes, recovered, accepted, completion_time.
- Communication: request_bytes, response_bytes, evidence_bytes, communication_bytes.
- Independent safety assertion: independent_corrupted_acceptance.

Time is milliseconds. Empty CSV cells represent unavailable values, such as a completion time after failed reconstruction. Boolean CSV values are `True`/`False`. `votes` is a JSON array stored inside a quoted CSV cell; use a CSV parser and JSON decoder, not string splitting.

## Reading results

| Scheme | Default recovery | Stress recovery |
|---|---:|---:|
| RS(2,4) | 97.67% | 51.17% |
| RS(3,6) | 98.47% | 42.37% |
| Two replicas | 95.50% | 62.77% |

Acceptance equaled recovery in this finite sample. A separate boundary test demonstrates recovery before the deadline followed by validation completion after it. Test success means expected behavior, including valid rejection, not 100% retrieval availability. See the root README and retrieval design for assumptions and metric definitions. These are descriptive simulation results, not measured Sepolia/network performance; seed-level confidence intervals and final figures remain Member 3's responsibility.

## Repeat

From the repository root:

```bash
PART2_REPORT_DIR=/tmp/part2-validation-new python3 -m tools.validate_part2
PART2_REPORT_DIR=/tmp/part2-validation-new python3 -m tools.validate_part2_edges
```

Always choose a new output directory. Optional historical-field replay, if a prior compact CSV is available:

```bash
python3 -m tools.replay_part2_records --repo . --original /path/to/previous-compact.csv --output /tmp/part2-records-recheck
```

The replay compares all old fields and obtains four missing fields by actual execution. It does not rewrite the original CSV. The original validation used Python 3.12 and took about 7 minutes; parallel completion replay took about 3 minutes.
