"""Audit every round, aggregate by seed and compute 95% Student t intervals."""
import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import statistics

from retrieval.bundle import digest
from .common import (ROOT, checksums, fingerprints, inputs, new_directory, offline,
                     read_json, verify_checksums, write_csv, write_json)

# Two-sided t(.975, n-1) for the fixed smoke/pilot (3) and formal (30) seed designs.
T_CRITICAL = {3: 4.302652729696142, 30: 2.045229642132703}
METRICS = ('recovery_probability', 'acceptance_probability', 'communication_bytes', 'completion_time_ms')
INTEGER_FIELDS = ('seed round k n fragment_count requested_fragments storage_bytes valid_fragments '
                  'request_bytes response_bytes evidence_bytes communication_bytes independent_corrupted_acceptance').split()


def execution_fingerprints(hashes):
    # Analysis/rendering edits do not change already executed retrieval rounds.
    excluded = {'evaluation/analyze.py', 'evaluation/plot.py', 'evaluation/smoke.py'}
    return {name: value for name, value in hashes.items() if name not in excluded}


def interval(values):
    values = list(values)
    n = len(values)
    if n not in T_CRITICAL or not all(math.isfinite(v) for v in values):
        raise ValueError('CI requires 3 or 30 finite seed means')
    mean = statistics.mean(values)
    sem = statistics.stdev(values) / math.sqrt(n)
    half = T_CRITICAL[n] * sem
    return mean, mean - half, mean + half


def parse_rounds(path):
    with Path(path).open(encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle)
        required = set(INTEGER_FIELDS) | set(('run_id config_id storage_overhead recovered accepted '
            'hash_match votes completion_time failure_reason payload_sha256 reconstructed_sha256 reconstructed_manifest_hash').split())
        if not required <= set(reader.fieldnames or []):
            raise ValueError(f'Missing CSV fields: {sorted(required - set(reader.fieldnames or []))}')
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError('Duplicate CSV headers')
        rows = []
        for raw in reader:
            if None in raw or any(value is None for value in raw.values()):
                raise ValueError('Malformed CSV row')
            row = dict(raw)
            for key in INTEGER_FIELDS:
                row[key] = int(raw[key])
            for key in ('recovered', 'accepted', 'hash_match'):
                if raw[key] not in ('True', 'False'):
                    raise ValueError(f'Invalid boolean: {key}')
                row[key] = raw[key] == 'True'
            row['votes'] = json.loads(raw['votes'])
            row['storage_overhead'] = float(raw['storage_overhead'])
            row['completion_time'] = float(raw['completion_time']) if raw['completion_time'] else None
            rows.append(row)
    return rows


def audit_rows(rows, configurations, settings, anchor, payload):
    expected = {(key, seed, number) for key in configurations for seed in settings['seeds']
                for number in range(settings['rounds_per_seed'])}
    seen, run_ids = set(), set()
    payload_hash = digest(payload)
    failures = Counter()
    for row in rows:
        identity = row['config_id'], row['seed'], row['round']
        def require(condition, message):
            if not condition:
                raise ValueError(f'{identity}: {message}')
        require(identity in expected and identity not in seen, 'missing, duplicate or unexpected round identity')
        seen.add(identity)
        run_ids.add(row['run_id'])
        prepared, env = configurations[row['config_id']]
        require(row['k'] == prepared.k and row['n'] == prepared.n, 'scheme parameters')
        require(row['storage_overhead'] == 2 and row['storage_bytes'] == 2 * len(payload), 'unequal storage')
        require(row['fragment_count'] == row['requested_fragments'] == prepared.n, 'fan-out/fragment count')
        require(0 <= row['valid_fragments'] <= prepared.k, 'valid fragment count')
        require(row['payload_sha256'] == payload_hash, 'different source payload')
        require(isinstance(row['votes'], list) and len(row['votes']) == 3
                and all(v in ('ACCEPT', 'REJECT') for v in row['votes']), 'validator votes')
        require(all(row[f] >= 0 for f in ('request_bytes', 'response_bytes', 'evidence_bytes')), 'negative bytes')
        require(row['request_bytes'] > 0 and row['communication_bytes'] == sum(row[f] for f in
                ('request_bytes', 'response_bytes', 'evidence_bytes')), 'communication accounting')
        require(row['independent_corrupted_acceptance'] == 0, 'corrupted object acceptance')
        valid_hash = (row['reconstructed_sha256'] == payload_hash
                      and row['reconstructed_manifest_hash'] == anchor['manifest_hash'])
        require(row['recovered'] == row['hash_match'] == valid_hash, 'recovery/hash inconsistency')
        completion = row['completion_time']
        if row['recovered']:
            require(row['valid_fragments'] == prepared.k, 'recovered below threshold')
            require(completion is not None and math.isfinite(completion)
                    and env.validator_ms <= completion <= env.deadline_ms + env.validator_ms, 'completion time')
        else:
            require(completion is None and row['votes'] == ['REJECT'] * 3, 'failure time/votes')
        accepted = row['recovered'] and row['votes'].count('ACCEPT') >= 2 and completion <= env.deadline_ms
        require(row['accepted'] == accepted, 'acceptance/deadline inconsistency')
        allowed = ('insufficient_valid_fragments', 'deadline', 'reconstruction_hash_mismatch', 'validator_rejected')
        require(row['failure_reason'] == '' if accepted else row['failure_reason'] in allowed, 'failure reason')
        if row.get('deadline_ms'):
            require(float(row['deadline_ms']) == env.deadline_ms, 'deadline changed')
        if not accepted:
            failures[row['failure_reason']] += 1
    if seen != expected or len(run_ids) != 1 or not next(iter(run_ids), ''):
        raise ValueError('Incomplete matrix or mixed/empty run_id')
    return {'passed': True, 'rows': len(rows), 'unique_rounds': len(seen), 'run_id': next(iter(run_ids)),
            'configurations': len(configurations), 'seeds_per_configuration': len(settings['seeds']),
            'rounds_per_seed': settings['rounds_per_seed'], 'corrupted_data_acceptance_count': 0,
            'failure_reasons': dict(sorted(failures.items())), 'storage_overhead': 2,
            'accepted_not_recovered': 0, 'recovered_not_accepted': sum(r['recovered'] and not r['accepted'] for r in rows)}


def aggregate(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row['config_id'], row['seed']].append(row)
    seeds = []
    for (key, seed), members in sorted(groups.items()):
        times = [r['completion_time'] for r in members if r['recovered']]
        seeds.append({'run_id': members[0]['run_id'], 'config_id': key, 'seed': seed, 'rounds': len(members),
            'recovered': sum(r['recovered'] for r in members), 'accepted': sum(r['accepted'] for r in members),
            'communication_bytes_total': sum(r['communication_bytes'] for r in members),
            'recovery_probability': statistics.mean(r['recovered'] for r in members),
            'acceptance_probability': statistics.mean(r['accepted'] for r in members),
            'communication_bytes': statistics.mean(r['communication_bytes'] for r in members),
            'completion_time_ms': statistics.mean(times) if times else None,
            'completion_observations': len(times)})
    summaries = []
    for key in sorted({s['config_id'] for s in seeds}):
        subset = [s for s in seeds if s['config_id'] == key]
        for metric in METRICS:
            available = [s[metric] for s in subset if s[metric] is not None]
            # Completion means are conditional on recovery, never zero-imputed for failures.
            mean, low, high = (interval(available) if len(available) in T_CRITICAL
                               else (statistics.mean(available) if available else None, None, None))
            summaries.append({'run_id': subset[0]['run_id'], 'config_id': key, 'metric': metric,
                'mean': mean, 'ci_low': low, 'ci_high': high, 'seed_count': len(available),
                'round_count': sum(s['rounds'] for s in subset),
                'ci_method': 'Student t over seed means; 95%; untruncated'})
    return seeds, summaries


def crosscheck_published(seeds, evidence):
    published = read_json(evidence / 'seed_totals.json')
    totals = read_json(evidence / 'summary.json')['configurations']
    for row in seeds:
        original = next(s for s in published[row['config_id']] if s['seed'] == row['seed'])
        for field in ('rounds', 'recovered', 'accepted'):
            if row[field] != original[field]:
                raise ValueError(f'Published seed total mismatch: {row["config_id"]}/{row["seed"]}/{field}')
        if row['communication_bytes_total'] != original['communication_bytes']:
            raise ValueError('Published seed bytes disagree with CSV')
    for key, expected in totals.items():
        subset = [s for s in seeds if s['config_id'] == key]
        for field in ('rounds', 'recovered', 'accepted', 'communication_bytes_total'):
            if sum(s[field] for s in subset) != expected[field]:
                raise ValueError(f'Published configuration totals disagree: {key}/{field}')


def analyze(source, output, profile='formal'):
    source = Path(source)
    before = fingerprints()
    verified_files = verify_checksums(source)
    with offline():
        evaluation, retrieval, anchor, payload, configurations = inputs()
        rows = parse_rounds(source / 'rounds.csv')
        audit = audit_rows(rows, configurations, evaluation['profiles'][profile], anchor, payload)
        seeds, summaries = aggregate(rows)
        published = source.resolve() == (ROOT / evaluation['formal_evidence']).resolve()
        if published:
            crosscheck_published(seeds, source)
            published_run = read_json(source / 'summary.json')
            if published_run['config_sha256'] != digest((ROOT / evaluation['retrieval_config']).read_bytes()):
                raise ValueError('Published experiment configuration differs from current retrieval config')
        else:
            record = read_json(source / 'run.json')
            if (record['profile'] != profile
                    or execution_fingerprints(record['source_hashes']) != execution_fingerprints(before)
                    or record['retrieval_configuration'] != retrieval):
                raise ValueError('Runner provenance does not match current configuration/source')
        if fingerprints() != before:
            raise ValueError('Inputs changed during analysis')
        out = new_directory(output)
        write_csv(out / 'seed_metrics.csv', seeds)
        write_csv(out / 'summary.csv', summaries)
        audit.update({'source_csv_sha256': digest((source / 'rounds.csv').read_bytes()),
                      'source_checksums_verified': verified_files, 'published_totals_crosschecked': published})
        write_json(out / 'audit.json', audit)
        write_json(out / 'provenance.json', {
            'schema_version': 'member3-analysis-v1', 'profile': profile,
            'data_origin': 'Member 2 published actual reconstruction records' if published else 'Member 3 runner',
            'source_csv_sha256': audit['source_csv_sha256'], 'source_hashes': before,
            'object_id': anchor['object_id'], 'manifest_hash': anchor['manifest_hash'],
            'payload_sha256': digest(payload), 'registration_tx': anchor['registration_tx'],
            'retrieval_configuration': retrieval, 'evaluation_configuration': evaluation,
            'statistical_unit': 'seed', 'confidence_level': 0.95, 't_critical': T_CRITICAL[len(evaluation['profiles'][profile]['seeds'])],
            'timing': 'Conditional mean among recovered rounds per seed; missing seeds not zero-imputed',
            'intervals': 'Two-sided Student t, untruncated; descriptive marginal intervals, no multiple-comparison claim',
            'anchor_mode': 'offline_snapshot_not_live_rpc',
            'published_execution': ({k: published_run[k] for k in
                ('python', 'platform', 'config_sha256', 'payload_sha256', 'source_hashes')}
                if published else None),
            'portability_fix': 'ZIP creator platform pinned to Unix (3), preserving published envelope bytes on Windows',
            'provenance_policy': 'Runner inputs and execution-source hashes must match; analysis/plot/smoke revisions are recorded separately in this analysis fingerprint',
        })
        write_json(out / 'checksums.json', checksums(out))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'artifacts/part2-validation-20261115')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=('formal', 'pilot', 'smoke'), default='formal')
    args = parser.parse_args()
    try:
        analyze(args.source, args.output, args.profile)
        print(f'Audited and analyzed {args.profile} records into {args.output}')
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
