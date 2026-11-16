# Member 1 Completion Review

## Conclusion

The delivered frozen bundle and registry implementation are usable for Part 2. Local integrity checks and a fresh Sepolia registration receipt/event check pass. Full from-scratch model inference reproducibility is not independently established by this clone.

## Independently checked

- Member 1 Python tests: 48 passed, 3 skipped. Skips: two require missing locally generated Step 1 artifacts; one avoids rebuilding over an already frozen Step 2 output.
- Hardhat: Solidity 0.8.24 compiled; 9 tests passed, covering registration, event/fields, duplicate rejection, isolation, absent IDs, sender binding and zero inputs.
- Frozen six-file bundle: standalone verifier passes all checks; objectId, manifestHash, file hashes, payload commitment and deployment bindings recompute.
- 8,832 signals: validation-only median recomputes to tau 0.001923089035; all buy/hold/sell labels and next-hour context alignment match.
- Frozen data/signals were supplied to the existing backtest implementation; all reported result fields match. This rechecks execution consistency, not model predictive quality or historical data authenticity.
- A new read-only public RPC query confirms successful registration receipt, destination, block, provider/objectId and all four hash fields in the registration event. Raw response and checks are saved in artifacts/part2/member1_audit.json. This is not merely trust in the repository's own chain_comparison report.

The Python test environment used Python 3.12 with available bundled pandas/numpy and temporary test/import dependencies, not the declared full Python 3.11 inference lock. No model weights were downloaded. Hardhat dependencies were installed with pnpm, not npm ci; direct versions match package.json, but transitive resolution was not certified against package-lock.json.

## Remaining gaps

1. **Fresh-clone model pin**: configs/step1.yaml has revision null. resolve_snapshot resolves main on a new cache, while the immutable pin lives in an excluded cache. The frozen model_reference does record revision a971ba21945c4f1796b17a91fe69214b5f4ad472, so the existing artifact is traceable, but the default clean-clone run is not guaranteed to select it. Ask Member 1 for a documented frozen-revision replay configuration or a tracked pin/bootstrap procedure. Do not silently mutate the existing frozen bundle/config hashes.
2. **Missing replay inputs/evidence**: artifacts/step1, raw cache, model snapshot and pinned_revision.json are absent. README references Step 1 evidence that is not included. Obtain them or a supported regeneration path before claiming a complete independent inference replay.
3. **Live registry scope**: this review confirms the receipt/event; it did not independently rerun getSignal or compare deployed runtime bytecode. Member 1's repository contains historical reports for those checks.
4. **Artifact freeze semantics**: old reports remain historical evidence. A successful test of their contents cannot prove how the original inference was executed or authenticate source data.

## Overall assessment

Application/bundle/contract work is substantially delivered, with a successful on-chain registration and usable handoff. “Part 1 completed” is supportable for integrating the delivered artifact. “Every stage can be independently reproduced from a fresh clone using the default commands” still has a documented gap. None of these gaps prevent Part 2 from recovering and validating the already frozen bundle.

To repeat the independent local/RPC audit:

```bash
python -m tools.audit_member1 --output /tmp/member1-audit.json
python -m tools.audit_member1 --output /tmp/member1-audit-live.json --rpc-url https://ethereum-sepolia-rpc.publicnode.com
```

The live command requires curl and network access but no private key. It sends no blockchain transaction.
