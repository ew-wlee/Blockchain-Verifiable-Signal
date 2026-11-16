import itertools
import json
import random
import socket
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from retrieval.codec import encode, decode, mul
from retrieval.bundle import pack_bundle, unpack_bundle, validate_payload
from retrieval.engine import Environment, Response, prepare, run_round, validator_vote

ROOT = Path(__file__).resolve().parents[1]


class CodecTests(unittest.TestCase):
    def test_all_threshold_subsets(self):
        for k, n in [(2, 4), (3, 6)]:
            for size in [0, 1, 2, 7, 256, 1025]:
                data = random.Random(size).randbytes(size)
                shards = encode(data, k, n)
                for indices in itertools.combinations(range(n), k):
                    self.assertEqual(decode({i: shards[i] for i in indices}, k, n, size), data)
                with self.assertRaises(ValueError):
                    decode({i: shards[i] for i in range(k - 1)}, k, n, size)

    def test_known_gf_vector(self):
        # For k=2, rows at x=1,2,3,4 interpolate a degree-1 polynomial.
        self.assertEqual(encode(bytes([1, 2]), 2, 4), (b'\x01', b'\x02', b'\x03', b'\x04'))
        self.assertEqual(mul(0x80, 2), 0x1d)

    def test_bad_parameters(self):
        for k, n in [(0, 4), (3, 2), (2, 256)]:
            with self.assertRaises(ValueError): encode(b'a', k, n)
        with self.assertRaises(ValueError): decode({0: b'a', 6: b'a', 1: b'a'}, 3, 6, 3)
        with self.assertRaises(ValueError): decode({0: b'a', 1: b'ab'}, 2, 4, 2)


class RetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anchor = json.loads((ROOT / 'deployments/sepolia.json').read_text())
        cls.payload = pack_bundle(ROOT / 'artifacts/step2/bundle')
        cls.rs = prepare(cls.payload, cls.anchor)
        cls.env = Environment()

    def responses(self, prepared=None, indices=None, delay=10):
        p = prepared or self.rs
        return [Response(i, p.object_id, i, p.fragments[i], delay + i) for i in (range(p.n) if indices is None else indices)]

    def run_responses(self, responses, env=None, prepared=None):
        return run_round(prepared or self.rs, env or self.env, responses=responses)

    def test_actual_bundle_every_threshold_subset(self):
        for k, n in [(2, 4), (3, 6)]:
            p = prepare(self.payload, self.anchor, 'rs', k, n)
            for indices in itertools.combinations(range(n), k):
                result, data = self.run_responses(self.responses(p, indices), prepared=p)
                self.assertTrue(result['accepted'], indices)
                self.assertEqual(data, self.payload)
                self.assertEqual(result['reconstructed_manifest_hash'], self.anchor['manifest_hash'])

    def test_insufficient_and_duplicate(self):
        responses = self.responses(indices=[0, 1])
        responses += [responses[0]] * 5
        result, data = self.run_responses(responses)
        self.assertFalse(result['accepted'])
        self.assertIsNone(data)
        self.assertEqual(result['valid_fragments'], 2)
        self.assertIn('duplicate_index', [e['reason'] for e in result['events']])

    def test_wrong_object_index_corrupt_and_silent(self):
        responses = self.responses()
        responses[0] = replace(responses[0], object_id='other')
        responses[1] = replace(responses[1], data=b'bad')
        responses[2] = replace(responses[2], index=99)
        result, _ = self.run_responses(responses)
        self.assertTrue(result['accepted'])
        reasons = {e['reason'] for e in result['events']}
        self.assertTrue({'wrong_object', 'fragment_hash_mismatch', 'invalid_index'} <= reasons)
        result, _ = self.run_responses([])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['communication_bytes'], result['request_bytes'])

    def test_deadline_and_validation_budget(self):
        result, _ = self.run_responses(self.responses(delay=500))
        self.assertEqual(result['failure_reason'], 'deadline')
        self.assertFalse(result['recovered'])
        result, _ = self.run_responses(self.responses(indices=[0, 1, 2], delay=248))
        self.assertTrue(result['recovered'])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['completion_time'], 251)
        # Equality at deadline is allowed for the whole operation.
        result, _ = self.run_responses(self.responses(indices=[0, 1, 2], delay=247))
        self.assertTrue(result['accepted'])

    def test_forged_hash_metadata_cannot_bypass_anchor(self):
        other = self.payload.replace(b'hold', b'sell', 1)
        forged = replace(self.rs, fragments=encode(other, 3, 6))
        from retrieval.bundle import digest
        forged = replace(forged, fragment_hashes=tuple(map(digest, forged.fragments)))
        result, _ = self.run_responses(self.responses(forged), prepared=forged)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['failure_reason'], 'reconstruction_hash_mismatch')
        self.assertEqual(result['corrupted_data_acceptance_count'], 0)

    def test_anchor_and_archive_tampering(self):
        self.assertFalse(validate_payload(self.payload, {**self.anchor, 'manifest_hash': '0x' + '0' * 64})['passed'])
        self.assertFalse(validate_payload(self.payload, {**self.anchor, 'data_hash': '0x' + '0' * 64})['passed'])
        self.assertEqual(validator_vote(b'not an archive', self.anchor), 'REJECT')
        import io, zipfile
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as z: z.writestr('../evil', b'bad')
        self.assertFalse(validate_payload(out.getvalue(), self.anchor)['passed'])
        files = unpack_bundle(self.payload)
        for name in files:
            self.assertEqual(files[name], (ROOT / 'artifacts/step2/bundle' / name).read_bytes())

    def test_two_of_three_and_independent_checks(self):
        with patch('retrieval.engine.validator_vote', side_effect=['ACCEPT','REJECT','ACCEPT']) as vote:
            result, _ = self.run_responses(self.responses())
            self.assertTrue(result['accepted'])
            self.assertEqual(vote.call_count, 3)
        with patch('retrieval.engine.validator_vote', side_effect=['REJECT','REJECT','ACCEPT']):
            result, _ = self.run_responses(self.responses())
            self.assertFalse(result['accepted'])

    def test_replication_and_storage(self):
        for scheme, k, n in [('rs',2,4), ('rs',3,6), ('replication',1,2)]:
            p = prepare(self.payload, self.anchor, scheme, k, n)
            self.assertEqual(p.storage_overhead, 2)
            indices = [1] if scheme == 'replication' else range(k)
            result, data = self.run_responses(self.responses(p, indices), prepared=p)
            self.assertTrue(result['accepted'])
            self.assertEqual(data, self.payload)
            self.assertEqual(result['communication_bytes'], sum(result[x] for x in ['request_bytes','response_bytes','evidence_bytes']))

    def test_seeded_round_and_offline(self):
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('network')):
            a, _ = run_round(self.rs, self.env, 42, 7)
            b, _ = run_round(self.rs, self.env, 42, 7)
        self.assertEqual(a, b)
        for env in [Environment(failure=1,invalid=0), Environment(failure=0,invalid=1)]:
            result, _ = run_round(self.rs, env)
            self.assertFalse(result['accepted'])
        with self.assertRaises(ValueError): Environment(failure=.8,invalid=.8)

    def test_demo_deterministic(self):
        for scenario in ['success','insufficient','deadline','replication']:
            cmd = [sys.executable, '-m', 'demo.run', '--scenario', scenario]
            a = subprocess.check_output(cmd, cwd=ROOT)
            b = subprocess.check_output(cmd, cwd=ROOT)
            self.assertEqual(a, b)
            result = json.loads(a)
            self.assertEqual(result['accepted'], scenario in ['success','replication'])


if __name__ == '__main__': unittest.main()
