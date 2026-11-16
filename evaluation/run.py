"""Run actual retrieval rounds: python -m evaluation.run --profile pilot --output NEW_DIR."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import os
import platform
import sys

from retrieval.bundle import digest
from retrieval.engine import run_round
from .common import ROOT, checksums, fingerprints, inputs, new_directory, offline, write_csv, write_json

FIELDS = ('run_id config_id seed round k n fragment_count requested_fragments storage_bytes '
          'storage_overhead recovered accepted hash_match votes valid_fragments completion_time '
          'failure_reason deadline_ms request_bytes response_bytes evidence_bytes communication_bytes '
          'independent_corrupted_acceptance payload_sha256 reconstructed_sha256 '
          'reconstructed_manifest_hash payload_commitment valid_indices').split()


def run_configuration(config_id, prepared, environment, seeds, rounds, payload_hash, run_id):
    rows = []
    with offline():
        for seed in seeds:
            for number in range(rounds):
                record, recovered = run_round(prepared, environment, seed, number,
                                              run_id=run_id, config_id=config_id)
                valid = recovered is not None and digest(recovered) == payload_hash
                corrupted = int(record['accepted'] and not valid)
                if corrupted or record['recovered'] != valid:
                    raise ValueError(f'Independent reconstructed-byte check failed: {config_id}/{seed}/{number}')
                row = {name: record[name] for name in FIELDS if name != 'independent_corrupted_acceptance'}
                row['independent_corrupted_acceptance'] = corrupted
                for name in ('votes', 'valid_indices'):
                    row[name] = json.dumps(row[name], separators=(',', ':'))
                rows.append(row)
    return rows


def run(profile, output):
    if sys.version_info[:2] != (3, 12):
        raise ValueError('Use Python 3.12 to reproduce the published random streams')
    before = fingerprints()
    with offline():
        evaluation, retrieval, anchor, payload, configurations = inputs()
        payload_hash = digest(payload)
        settings = evaluation['profiles'][profile]
        seeds = settings['seeds']
        rounds = settings['rounds_per_seed']
        if len(seeds) != len(set(seeds)) or rounds <= 0:
            raise ValueError('Invalid seed/round coverage')
        out = new_directory(output)
        run_id = f'member3-{profile}'
        groups = {}
        with ProcessPoolExecutor(max_workers=min(6, os.cpu_count() or 1)) as pool:
            futures = {pool.submit(run_configuration, key, prepared, environment, seeds,
                                   rounds, payload_hash, run_id): key
                       for key, (prepared, environment) in configurations.items()}
            for future in as_completed(futures):
                key = futures[future]
                groups[key] = future.result()
                print(f'{key}: {len(groups[key])} actual rounds complete', flush=True)
        rows = [row for key in configurations for row in groups[key]]
        if fingerprints() != before:
            raise ValueError('Inputs or source code changed during execution')
        write_csv(out / 'rounds.csv', rows, FIELDS)
        write_json(out / 'run.json', {
            'schema_version': 'member3-run-v1', 'run_id': run_id, 'profile': profile,
            'python': platform.python_version(), 'seeds': seeds, 'rounds_per_seed': rounds,
            'round_count': len(rows), 'network_blocked': True, 'source_hashes': before,
            'retrieval_configuration': retrieval, 'object_id': anchor['object_id'],
            'manifest_hash': anchor['manifest_hash'], 'payload_sha256': payload_hash,
            'method': 'Actual RS/replication reconstruction and independent returned-byte checks on every round',
        })
        write_json(out / 'checksums.json', checksums(out))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=('smoke', 'pilot', 'formal'), default='formal')
    parser.add_argument('--output', type=str, required=True)
    args = parser.parse_args()
    try:
        run(args.profile, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
