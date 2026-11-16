# Member 2 Retrieval Verification and Baseline

## Result and scope

The frozen Member 1 six-file bundle is encoded, retrieved, actually reconstructed and independently checked against the saved Sepolia registration. Both RS(2,4) and RS(3,6), a two-copy baseline, three honest validators and a two-of-three acceptance rule are implemented. This is a local simulator with an offline registry snapshot, not a new consensus protocol or a complete Proof of Retrievability.

Python 3.11+ and the standard library are sufficient for Part 2. Run commands from the repository root. No model download, wallet, RPC connection or editable installation is needed for retrieval. Full Member 1 inference has its own heavier dependencies.

## Run and inspect

```bash
python -m unittest discover -s tests_part2 -v
python -m demo.run --scenario success
python -m demo.run --scenario success --output /tmp/signal-success.json --recover-to /tmp/recovered-signal-bundle
python -m demo.run --scenario insufficient
python -m demo.run --scenario deadline
python -m demo.run --scenario replication
python -m tools.smoke_retrieval --output /tmp/retrieval-pilot.json
```

`--recover-to` must name a new directory. Its six recovered files match the frozen files byte for byte. The failure demo commands exit successfully when they demonstrate the expected failure. Machine-readable JSON contains reasons, indices, hashes, votes and an original signal trace. Nothing is written by default. The success demo excludes node 0 (silent), corrupts node 1, then recovers with indices 2, 3 and 4. Both independent process runs produce identical JSON.

The program completes in much less than the PDF's 30–45 second presentation slot on this machine. Use that slot to explain the output; no artificial sleep is added. `completion_time=61 ms` is simulated service time, not CLI runtime, chain latency or a real network measurement.

## Source and hash boundaries

Input: `artifacts/step2/bundle/`. Anchor: `deployments/sepolia.json`.

A deterministic, uncompressed ZIP envelope stores the six original files with fixed timestamps, file order and permissions. Zero to five trailing zero bytes make the common envelope length divisible by six. Both RS configurations and the baseline encode/store the **same padded envelope**, giving exactly two times its size. This transport envelope does not alter any frozen file or on-chain hash. Do not compare its SHA256 directly with manifestHash.

Validation independently checks:

1. Exact six-file set and bounded, uncompressed ZIP entries; no path extraction from untrusted names.
2. Canonical manifest, every content-file size and SHA256, sorted file list, recomputed payload commitment and objectId using Member 1's standalone verifier.
3. SHA256 of recovered manifest bytes against the recorded manifest_hash.
4. Data, signal and model commitment bindings against the saved registry record.
5. Reconstructed envelope SHA256 against the transport catalog.

The fragment catalog is created only after validating the source bundle against the frozen anchor. It is trusted simulation setup data, not a new on-chain fragment proof. Catalog hashes alone are insufficient: the final anchored bundle checks still reject forged fragment metadata. The local anchor is trusted as an offline snapshot; authenticating a newly supplied snapshot requires independently querying the registry or verifying chain evidence. The demo never pretends to do a live RPC query.

## RS codec

`retrieval/codec.py` implements systematic Reed–Solomon erasure coding over GF(256), polynomial 0x11d. A Vandermonde generator is normalized by the inverse of its first k rows. Each byte position is an independent stripe. The first k fragments are systematic data shards; any k distinct rows can reconstruct. Parameters in this project are written (k,n), not (n,k).

Corrupt shards are rejected by expected SHA256 before erasure decoding. The codec is not an unknown-error correction protocol. Tests cover every minimum-size subset for both configurations, including the real frozen bundle, k−1 failure, known field vectors, padding and invalid indices/sizes. The self-contained implementation avoids a new runtime package dependency.

## Node and network assumptions

- `failure` and `invalid` are mutually exclusive probabilities in a categorical draw; honest probability is `1-failure-invalid`.
- Latency is lognormal in milliseconds: median 50/100 ms, sigma 0.5. The PDF specifies medians, not sigma.
- Deadline 250 ms and a fixed parallel validator budget of 1 ms are explicit implementation assumptions. They are configurable, not experimentally measured constants.
- A per-seed/per-round/per-node random stream is shared across schemes where node indices overlap. Repeated calls and call-order changes preserve reproducibility.
- All nodes are requested in parallel at time zero. Each node stores one RS fragment, or one full replica for the baseline.
- A request is made once, no retries. Stop at the kth valid arrival or deadline. Invalid and duplicate arrivals before stopping consume bytes but do not advance recovery. Arrivals after stopping do not consume counted bytes under this cancellation model.
- Responses must match objectId, node/index assignment, unique index, size and expected SHA256. Exact deadline equality is allowed.
- No correlated failures, real bandwidth bottleneck, Sybil behavior, malicious validator or economic incentives are modeled.

The two full replicas are on two nodes; RS uses four or six. Equal storage does not mean equal node count, metadata cost or failure correlations. Conclusions must stay within this model.

## Validators and metrics

Three separate validator calls each re-open the recovered bytes and run all bundle/anchor checks. At least two ACCEPT votes are required. Validators are assumed honest and online. The 1 ms budget is simulated service time for their parallel work; actual Python verification and decoding time is not added to simulated network time.

`recovered`: a correct anchored bundle was rebuilt from responses within the retrieval deadline.

`accepted`: recovered, at least two validators accept, and arrival plus validation budget is within deadline. Therefore recovery may succeed while acceptance fails near the deadline. A response arriving after the retrieval deadline is not used even if it could eventually enable recovery.

`communication_bytes`: all request JSON bytes plus received fragment body bytes plus response metadata JSON bytes before stopping. Metadata contains objectId, index and SHA256. Framing is canonical UTF-8 JSON with newline. Failed attempts are included. TCP/TLS, registry queries, validator traffic, and disk/CPU costs are excluded. Use this same accounting for every scheme.

`corrupted_data_acceptance_count`: detects accepted bytes not passing the trusted expected-bundle checks. A zero count is an implementation invariant in this model, not a claim of universal adversarial security.

## Member 3 API and output contract

```python
import json
from pathlib import Path
from retrieval.bundle import pack_bundle
from retrieval.engine import Environment, prepare, run_round

anchor = json.loads(Path('deployments/sepolia.json').read_text())
payload = pack_bundle('artifacts/step2/bundle')
prepared = prepare(payload, anchor, scheme='rs', k=3, n=6)
env = Environment(failure=.1, invalid=.1, latency_median_ms=50,
                  latency_sigma=.5, deadline_ms=250, validator_ms=1)
record, recovered_bytes = run_round(prepared, env, seed=0, round_number=0,
                                    run_id='final-run', config_id='default-rs-3-6')
```

Use `prepare` once per scheme and reuse it. For the baseline use `scheme='replication'`; k/n become 1/2. `configs/retrieval.json` freezes the six configurations and the extra timing assumptions. The full 30 seeds × 100 rounds × 6 configurations have now been executed and replay-checked. Member 3 can use `artifacts/part2-validation-20261115/rounds.csv` and `seed_totals.json`; seed-level 95% CIs, figures and final interpretation remain their work.

Each round contains run_id, config_id, seed, round, scheme, k/n, object_id, fragment_count, storage_bytes/overhead, requested/valid_fragments, valid_indices, recovered, hash_match, reconstructed hashes, payload_commitment, votes, accepted, failure_reason, completion_time, deadline, request/response/evidence/total bytes, environment, node states and rejection events. Time is always milliseconds. Completion time is null if reconstruction fails. Avoid treating failure completion times as zero when reporting conditional means.

Serialize votes/events/states as JSON strings when exporting to CSV. The API does actual reconstruction and three full checks on every successful round; it does not substitute an analytical probability calculation. All random outputs are deterministic for a fixed Python implementation, payload and configuration. Pin the Python version for final reproducibility.

## Verification evidence

- `artifacts/part2/success.json`: successful real-bundle trace.
- `artifacts/part2/insufficient.json`, `deadline.json`, `replication.json`: controlled scenarios.
- `artifacts/part2/pilot.json`: 6 configurations × 3 seeds × 30 rounds = 540 integration rounds; zero corrupted acceptances. Not final evaluation and not selected for favorable outcomes.
- `artifacts/part2/member1_audit.json`: independently recomputed local checks and a fresh read-only Sepolia receipt/event query.
- `artifacts/part2/backtest_recheck.json`: frozen backtest results recomputed with Member 1's implementation.
- `artifacts/part2/tests.txt`: test execution record.

The complete evidence set is indexed in `artifacts/part2-validation-20261115/README.md`. Full and edge validation commands are `python -m tools.validate_part2` and `python -m tools.validate_part2_edges` with a new `PART2_REPORT_DIR`. No frozen Step 1/2/3 evidence, contract, registration or historical handoff files were overwritten.
