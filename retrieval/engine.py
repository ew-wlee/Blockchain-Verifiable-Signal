"""Reusable single-round API for Member 3. No network calls or wall-clock sleeps."""
from dataclasses import dataclass, asdict
import math
import random
from .codec import encode, decode
from .bundle import canonical_bytes, digest, validate_payload


@dataclass(frozen=True)
class Environment:
    failure: float = 0.1
    invalid: float = 0.1
    latency_median_ms: float = 50.0
    latency_sigma: float = 0.5
    deadline_ms: float = 250.0
    validator_ms: float = 1.0

    def __post_init__(self):
        values = asdict(self)
        if any(not math.isfinite(v) for v in values.values()):
            raise ValueError('non-finite environment parameter')
        if min(self.failure, self.invalid) < 0 or self.failure + self.invalid > 1:
            raise ValueError('failure and invalid are mutually exclusive probabilities')
        if self.latency_median_ms <= 0 or min(self.latency_sigma, self.deadline_ms, self.validator_ms) < 0:
            raise ValueError('invalid timing parameter')


@dataclass(frozen=True)
class Response:
    node: int
    object_id: str
    index: int
    data: bytes
    latency_ms: float


@dataclass(frozen=True)
class Prepared:
    scheme: str
    k: int
    n: int
    original_size: int
    object_id: str
    fragments: tuple[bytes, ...]
    fragment_hashes: tuple[str, ...]
    payload_sha256: str
    anchor: dict

    @property
    def storage_overhead(self):
        return sum(map(len, self.fragments)) / self.original_size


def prepare(payload, anchor, scheme='rs', k=3, n=6):
    if not validate_payload(payload, anchor)['passed']:
        raise ValueError('bundle does not match frozen registration anchor')
    if scheme == 'replication':
        k, n = 1, 2
        shards = (payload, payload)
    elif scheme == 'rs' and (k, n) in ((2, 4), (3, 6)):
        shards = encode(payload, k, n)
    else:
        raise ValueError('supported schemes: rs(2,4), rs(3,6), replication r=2')
    return Prepared(scheme, k, n, len(payload), anchor['object_id'], shards,
                    tuple(digest(s) for s in shards), digest(payload), dict(anchor))


def simulate_responses(prepared, env, seed, round_number):
    responses, states = [], []
    for i, shard in enumerate(prepared.fragments):
        # Same seed/round/node streams across schemes, no dependence on call order.
        rng = random.Random(f'{seed}:{round_number}:{i}')
        u = rng.random()
        state = 'silent' if u < env.failure else 'invalid' if u < env.failure + env.invalid else 'honest'
        latency = rng.lognormvariate(math.log(env.latency_median_ms), env.latency_sigma)
        states.append({'node': i, 'state': state, 'latency_ms': latency})
        if state != 'silent':
            data = bytes([shard[0] ^ 1]) + shard[1:] if state == 'invalid' else shard
            responses.append(Response(i, prepared.object_id, i, data, latency))
    return responses, states


def validator_vote(payload, anchor):
    return 'ACCEPT' if validate_payload(payload, anchor)['passed'] else 'REJECT'


def run_round(prepared, env, seed=0, round_number=0, *, responses=None, run_id='retrieval', config_id=None):
    """Parallel fan-out; stop at kth valid arrival or deadline, no retries.

    All n requests are counted. Responses arriving before stop are counted,
    including bad data. Later responses are cancelled/not counted. Transport,
    disk, CPU, validator traffic and registry reads are outside logical bytes.
    Recovery is limited to the deadline; validator_ms is a fixed simulated
    parallel validation service budget, not a measured chain/CPU latency.
    """
    if responses is None:
        responses, states = simulate_responses(prepared, env, seed, round_number)
    else:
        states = []
    ordered = sorted(responses, key=lambda r: (r.latency_ms, r.node))
    valid, seen_nodes, events = {}, set(), []
    request_bytes = sum(len(canonical_bytes({'object_id': prepared.object_id, 'index': i})) for i in range(prepared.n))
    response_bytes = evidence_bytes = 0
    recovery = False
    payload = None
    verification = {}
    failure = 'insufficient_valid_fragments'
    finished_at = env.deadline_ms
    for response in ordered:
        if not math.isfinite(response.latency_ms) or response.latency_ms < 0:
            events.append({'node': response.node, 'reason': 'invalid_latency'})
            continue
        if response.latency_ms > env.deadline_ms:
            events.append({'node': response.node, 'reason': 'deadline'})
            continue
        response_bytes += len(response.data)
        evidence_bytes += len(canonical_bytes({'object_id': response.object_id, 'index': response.index,
                                               'sha256': digest(response.data)}))
        reason = 'valid'
        if response.object_id != prepared.object_id:
            reason = 'wrong_object'
        elif type(response.index) is not int or not 0 <= response.index < prepared.n:
            reason = 'invalid_index'
        elif response.node != response.index:
            reason = 'wrong_node_index'
        elif response.index in valid or response.node in seen_nodes:
            reason = 'duplicate_index'
        elif len(response.data) != len(prepared.fragments[response.index]) or digest(response.data) != prepared.fragment_hashes[response.index]:
            reason = 'fragment_hash_mismatch'
        events.append({'node': response.node, 'index': response.index, 'latency_ms': response.latency_ms, 'reason': reason})
        if reason != 'valid':
            continue
        seen_nodes.add(response.node)
        valid[response.index] = response.data
        if len(valid) == prepared.k:
            finished_at = response.latency_ms
            payload = (next(iter(valid.values())) if prepared.scheme == 'replication' else
                       decode(valid, prepared.k, prepared.n, prepared.original_size))
            verification = validate_payload(payload, prepared.anchor)
            recovery = verification['passed'] and digest(payload) == prepared.payload_sha256
            failure = None if recovery else 'reconstruction_hash_mismatch'
            break
    if len(valid) < prepared.k and any(r.latency_ms > env.deadline_ms for r in ordered):
        failure = 'deadline'
    # Separate calls: each honest validator independently rechecks recovered bytes.
    votes = [validator_vote(payload, prepared.anchor) for _ in range(3)] if recovery else ['REJECT'] * 3
    completion = finished_at + env.validator_ms if recovery else None
    accepted = recovery and votes.count('ACCEPT') >= 2 and completion <= env.deadline_ms
    if recovery and not accepted:
        failure = 'deadline' if completion > env.deadline_ms else 'validator_rejected'
    result = {
        'run_id': run_id, 'config_id': config_id or f'{prepared.scheme}-{prepared.k}-{prepared.n}',
        'seed': seed, 'round': round_number, 'scheme': prepared.scheme, 'k': prepared.k, 'n': prepared.n,
        'fragment_count': prepared.n, 'object_id': prepared.object_id,
        'payload_sha256': prepared.payload_sha256, 'storage_bytes': sum(map(len, prepared.fragments)),
        'storage_overhead': prepared.storage_overhead, 'requested_fragments': prepared.n,
        'valid_fragments': len(valid), 'valid_indices': sorted(valid),
        'recovered': recovery, 'hash_match': verification.get('passed', False),
        'reconstructed_sha256': digest(payload) if payload is not None else None,
        'reconstructed_manifest_hash': verification.get('manifest_hash'),
        'payload_commitment': verification.get('payload_commitment'),
        'votes': votes, 'accepted': accepted, 'failure_reason': failure,
        'completion_time': completion, 'time_unit': 'ms', 'deadline_ms': env.deadline_ms,
        'request_bytes': request_bytes, 'response_bytes': response_bytes, 'evidence_bytes': evidence_bytes,
        'communication_bytes': request_bytes + response_bytes + evidence_bytes,
        'corrupted_data_acceptance_count': int(accepted and not verification.get('passed', False)),
        'anchor_mode': 'offline_snapshot_not_live_rpc', 'environment': asdict(env),
        'node_states': states, 'events': events,
    }
    return result, payload if recovery else None
