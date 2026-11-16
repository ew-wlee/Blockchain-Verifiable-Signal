# English presentation scripts

These are target timings, not a record of completed human rehearsals. Run two timed rehearsals before submission. Test the saved decks and offline command on the presentation computer. Keep final slides 8-9 as backup for questions.

## Midterm 180 second script

### Slide 1 0:00 to 0:40

Our project asks a practical question: if storage nodes disappear or return bad data, how can we retrieve a quantitative signal file and check its publicly registered version? The application uses fixed BTC and ETH history and a pinned Chronos checkpoint to produce one six-file signal bundle. Sepolia records its fingerprints. The retrieval layer uses Reed-Solomon fragments or two full replicas, reconstructs the original bytes and applies a two-of-three rule with honest validators. We evaluate recovery and communication at the same storage cost.

### Slide 2 0:40 to 1:25

I will run the offline success scenario now.

*Run `python -m demo.run --scenario success` and explain the output within this 45 second slot.*

Node zero is silent and node one sends a corrupt fragment. The hash check rejects that response. Indices two, three and four reconstruct the actual bundle. Its manifest matches the saved Sepolia record, three validators independently accept, and the trace returns to the original BTC signal. The displayed milliseconds are simulated service time.

### Slide 3 1:25 to 2:15

The first chart compares recovery and acceptance across six configurations. We use the same bundle and twice its storage size, with thirty seeds and one hundred attempts per seed: eighteen thousand actual attempts. Confidence intervals use seed means, and failed attempts remain included. Default recovery is about ninety-eight percent for RS and ninety-six percent for replicas. Under stress, only forty percent of nodes are honest before delay. Recovery falls to fifty-one and forty-two percent for the RS schemes, while replicas reach sixty-three percent. The valid-fragment threshold explains this reversal.

### Slide 4 2:15 to 3:00

The second chart shows communication per attempt, including failures. We count requests, received fragments and evidence before collection stops. Default means are about three point seven megabytes for RS and three point six for replicas. Under stress, replicas have both higher recovery and lower communication. Equal storage therefore does not make RS universally better. No corrupt object was accepted in the tested attempts. Our conclusions concern independent simulated node faults and delays, trusted metadata and three honest validators. The code, exact confidence intervals, figures and reproduction package provide the audit trail.

## Final 5 minute script

### Slide 1 0:00 to 0:30

When storage nodes disappear or return bad data, a file may be unavailable or silently changed. Our project asks how to retrieve a BTC and ETH signal file and verify that it is the version publicly registered by its provider. We combine one frozen application bundle, a Sepolia registry and experiments comparing Reed-Solomon fragments with two full replicas.

### Slide 2 0:30 to 1:10

There are three layers. The application uses fixed hourly history and a pinned open-source checkpoint to produce buy, hold and sell signals. A canonical manifest identifies the bundle. The public contract records data, model, signal and manifest fingerprints; it does not store the original files or run inference. The retrieval layer rejects invalid fragments, reconstructs the object and checks its fingerprints against a saved registration snapshot. A two-of-three rule then determines acceptance. The on-chain record establishes version consistency, not source truth or trading profitability.

### Slide 3 1:10 to 1:55

We will demonstrate one success path without network access.

*Run `python -m demo.run --scenario success` and explain the output within this 45 second slot.*

Node zero is silent. Node one's corrupt fragment fails its hash check. Three different valid indices reconstruct the actual bundle. The manifest fingerprint matches the saved Sepolia record, and all three validators independently accept. Finally, the trace identifies the original BTC signal and model revision. The displayed sixty-one milliseconds are simulated completion time.

### Slide 4 1:55 to 2:30

This chart compares six configurations at equal storage. Under default faults, observed recovery is about ninety-eight percent for RS and ninety-six percent for replicas. Under stress, only forty percent of nodes are honest before considering delay. Recovery falls to fifty-one percent for RS two-of-four and forty-two percent for RS three-of-six, while two replicas reach sixty-three percent. Acceptance equals recovery in this sample, but a boundary test separately checks late validation.

### Slide 5 2:30 to 3:00

Communication counts requests, received bytes and evidence before collection stops. Failed attempts remain included. In default conditions, RS improves observed recovery with a modest communication cost. Under stress, replication has both higher recovery and lower communication. Equal storage therefore does not make RS universally better. Actual network framing, registry queries and validator messages are outside this logical-byte metric.

### Slide 6 3:00 to 3:45

We run thirty seeds with one hundred attempts for each configuration: eighteen thousand actual retrieval attempts. The statistical unit is the seed. We calculate each seed's metric, then use a Student t interval across the thirty seed means. We do not treat three thousand rounds as independent samples. The scientific figures and backup table show the exact ninety-five-percent intervals. Audit checks cover identities, coverage, storage equality, fingerprints, votes, deadlines and byte accounting. An independent check of returned bytes records zero corrupt acceptances. A clean offline smoke reproduces the runner and statistics twice.

### Slide 7 3:45 to 5:00

The evidence answers three questions. We can publicly anchor a real signal version and trace a recovered object back to it. Recovery changes with faults, delay and the valid-fragment threshold. At equal payload storage, reliability and communication depend on the environment. The stress result is informative: needing three valid fragments can be harder than finding one intact replica when honest nodes are scarce. We retain every seed and every failed attempt rather than selecting favorable results.

Our conclusions are limited to independent simulated nodes and delays, a trusted fragment catalog, an authentic offline registry snapshot and three honest online validators. We do not implement complete BFT, universal adversarial security or a real-network benchmark. Simulated milliseconds are not chain performance, and a fingerprint does not prove trading returns. The delivered code, full records, seed statistics, figures and reproduction package allow the evidence to be checked directly.

## Questions to prepare

- Why blockchain? A public, hard-to-alter version fingerprint that can be checked after retrieval; not proof of source truth.
- Why not just replicate? That is the baseline. At the same 2x storage, the observed ranking changes with fault conditions.
- Why thirty seeds? The confidence interval uses thirty seed-level observations, avoiding within-seed pseudoreplication.
- Why is RS(3,6) worse under stress? Honest probability is only 0.4 and three distinct valid responses must arrive in time.
- Is two-of-three consensus? It is a minimal acceptance rule under honest-validator assumptions, not complete BFT.
- Why can recovery and acceptance differ? Validation consumes the simulated service budget after reconstruction; completion can cross the deadline.
- What does zero corruption mean? No incorrect object was accepted in the tested attempts, not a guarantee against every malicious strategy.
