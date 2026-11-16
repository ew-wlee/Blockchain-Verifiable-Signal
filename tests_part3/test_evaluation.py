import copy
import math
from pathlib import Path
import tempfile
import unittest

from evaluation.analyze import aggregate, audit_rows, execution_fingerprints, interval, parse_rounds
from evaluation.common import checksums, inputs, new_directory, verify_checksums, write_csv, write_json
from retrieval.engine import Response, run_round
from tools.compare_part3_records import equal_field


class StatisticsTests(unittest.TestCase):
    def test_student_t_interval_uses_seed_sample_size(self):
        mean, low, high = interval([1, 2, 3])
        self.assertEqual(mean, 2)
        self.assertAlmostEqual(low, -0.484137711719546, places=10)
        self.assertAlmostEqual(high, 4.484137711719546, places=10)
        self.assertEqual(interval([7] * 30), (7, 7, 7))
        with self.assertRaises(ValueError):
            interval([1, math.nan, 3])

    def test_aggregate_weights_seeds_and_includes_failed_attempt_bytes(self):
        rows = []
        for seed, count, recovered, cost in ((0, 1, True, 100), (1, 9, False, 10), (2, 9, False, 10)):
            rows.extend({'run_id': 'test', 'config_id': 'default-rs-2-4', 'seed': seed,
                         'recovered': recovered, 'accepted': recovered, 'communication_bytes': cost,
                         'completion_time': 60 if recovered else None} for _ in range(count))
        seeds, summary = aggregate(rows)
        metrics = {r['metric']: r for r in summary}
        self.assertAlmostEqual(metrics['recovery_probability']['mean'], 1 / 3)
        self.assertEqual(metrics['communication_bytes']['mean'], 40)
        self.assertEqual(metrics['completion_time_ms']['mean'], 60)
        self.assertEqual(metrics['completion_time_ms']['seed_count'], 1)
        self.assertIsNone(metrics['completion_time_ms']['ci_low'])
        self.assertIsNone(seeds[1]['completion_time_ms'])


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, _, cls.anchor, cls.payload, cls.configurations = inputs()
        cls.settings = {'seeds': [0], 'rounds_per_seed': 1}
        cls.rows = []
        for key, (prepared, env) in cls.configurations.items():
            responses = [Response(i, prepared.object_id, i, shard, 10 + i)
                         for i, shard in enumerate(prepared.fragments)]
            row, recovered = run_round(prepared, env, responses=responses, run_id='test', config_id=key)
            if recovered != cls.payload:
                raise AssertionError('Fixture must actually reconstruct the frozen bundle')
            row['failure_reason'] = row['failure_reason'] or ''
            row['independent_corrupted_acceptance'] = 0
            cls.rows.append(row)

    def audit(self, rows):
        return audit_rows(rows, self.configurations, self.settings, self.anchor, self.payload)

    def test_real_records_pass(self):
        self.assertTrue(self.audit(self.rows)['passed'])

    def test_duplicate_or_missing_round_rejected(self):
        for rows in (self.rows + [self.rows[0]], self.rows[:-1]):
            with self.assertRaises(ValueError):
                self.audit(rows)

    def test_safety_accounting_and_fairness_mutations_rejected(self):
        mutations = {
            'communication_bytes': 0, 'storage_overhead': 3, 'storage_bytes': 1,
            'requested_fragments': 1, 'fragment_count': 99, 'valid_fragments': 0,
            'reconstructed_sha256': '0' * 64, 'hash_match': False, 'payload_sha256': 'wrong',
            'votes': ['ACCEPT', 'REJECT', 'REJECT'], 'completion_time': float('nan'),
            'independent_corrupted_acceptance': 1, 'failure_reason': 'deadline',
        }
        for key, value in mutations.items():
            with self.subTest(key=key):
                rows = copy.deepcopy(self.rows)
                rows[0][key] = value
                with self.assertRaises(ValueError):
                    self.audit(rows)

    def test_recovery_before_deadline_but_acceptance_after_deadline(self):
        key = 'default-rs-3-6'
        prepared, env = self.configurations[key]
        responses = [Response(i, prepared.object_id, i, prepared.fragments[i], 250) for i in range(3)]
        row, _ = run_round(prepared, env, responses=responses, run_id='test', config_id=key)
        row['independent_corrupted_acceptance'] = 0
        rows = [row if r['config_id'] == key else r for r in self.rows]
        audit = self.audit(rows)
        self.assertEqual(audit['recovered_not_accepted'], 1)
        self.assertTrue(row['recovered'])
        self.assertFalse(row['accepted'])

    def test_strict_csv_boolean_and_nan_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'rounds.csv'
            row = dict(self.rows[0])
            row['votes'] = '["ACCEPT","ACCEPT","ACCEPT"]'
            row['accepted'] = 'yes'
            write_csv(path, [row])
            with self.assertRaises(ValueError):
                parse_rounds(path)


class ArtifactTests(unittest.TestCase):
    def test_checksums_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / 'value.json', {'test': 1})
            write_json(root / 'checksums.json', checksums(root))
            self.assertEqual(verify_checksums(root), 1)
            with self.assertRaises(ValueError):
                new_directory(root)
            write_json(root / 'value.json', {'test': 2})
            with self.assertRaises(ValueError):
                verify_checksums(root)


class ReplayTests(unittest.TestCase):
    def test_plot_edits_preserve_run_provenance_but_engine_edits_do_not(self):
        original = {'retrieval/engine.py': 'old', 'evaluation/run.py': 'old', 'evaluation/plot.py': 'old'}
        self.assertEqual(execution_fingerprints(original), execution_fingerprints({**original, 'evaluation/plot.py': 'new'}))
        self.assertNotEqual(execution_fingerprints(original), execution_fingerprints({**original, 'retrieval/engine.py': 'new'}))

    def test_only_subpicosecond_completion_tail_differences_are_allowed(self):
        self.assertTrue(equal_field('completion_time', '58.14447478892882', '58.144474788928825'))
        self.assertFalse(equal_field('completion_time', '58.14447479', '58.144474788928825'))
        self.assertFalse(equal_field('completion_time', 'nan', '58.1'))
        self.assertFalse(equal_field('completion_time', '', '0'))
        self.assertFalse(equal_field('communication_bytes', '100', '101'))


if __name__ == '__main__':
    unittest.main()
