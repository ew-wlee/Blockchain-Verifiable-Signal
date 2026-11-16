"""Run a clean-directory offline replay twice and compare real records with the formal dataset."""
import argparse
import csv
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from .common import ROOT, fingerprints, read_json, write_json


def smoke():
    if sys.version_info[:2] != (3, 12):
        raise ValueError('Use Python 3.12 for the published replay')
    before = fingerprints()
    with tempfile.TemporaryDirectory(prefix='signal-evaluation-clean-') as temp:
        clean = Path(temp) / 'repo'
        clean.mkdir()
        # Deliberately omit Git, virtualenvs, caches, model files and installed packages.
        for folder in ('evaluation', 'retrieval', 'demo'):
            shutil.copytree(ROOT / folder, clean / folder, ignore=shutil.ignore_patterns('__pycache__'))
        for name in ('tools/verify_bundle.py', 'configs/evaluation.json', 'configs/retrieval.json',
                     'deployments/sepolia.json'):
            destination = clean / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        shutil.copytree(ROOT / 'artifacts/step2/bundle', clean / 'artifacts/step2/bundle')
        env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME')}
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        for number in (1, 2):
            for module, arguments in (
                ('evaluation.run', ['--profile', 'smoke', '--output', f'run{number}']),
                ('evaluation.analyze', ['--profile', 'smoke', '--source', f'run{number}', '--output', f'analysis{number}']),
            ):
                result = subprocess.run([sys.executable, '-B', '-m', module, *arguments],
                                        cwd=clean, env=env, capture_output=True, text=True)
                if result.returncode:
                    raise ValueError(f'Clean smoke failed: {module}\n{result.stdout}\n{result.stderr}')
        compared = []
        for name in ('rounds.csv',):
            if (clean / 'run1' / name).read_bytes() != (clean / 'run2' / name).read_bytes():
                raise ValueError(f'Non-deterministic runner output: {name}')
            compared.append(name)
        for name in ('seed_metrics.csv', 'summary.csv', 'audit.json', 'provenance.json', 'checksums.json'):
            if (clean / 'analysis1' / name).read_bytes() != (clean / 'analysis2' / name).read_bytes():
                raise ValueError(f'Non-deterministic analysis: {name}')
            compared.append(name)
        evaluation = read_json(ROOT / 'configs/evaluation.json')
        with (ROOT / evaluation['formal_evidence'] / 'rounds.csv').open(encoding='utf-8', newline='') as handle:
            original = {(r['config_id'], r['seed'], r['round']): r for r in csv.DictReader(handle)}
        with (clean / 'run1/rounds.csv').open(encoding='utf-8', newline='') as handle:
            replay = list(csv.DictReader(handle))
        compared_fields = set()
        for row in replay:
            old = original[row['config_id'], row['seed'], row['round']]
            fields = (set(old) & set(row)) - {'run_id'}
            for key in fields:
                if row[key] != old[key]:
                    raise ValueError(f'Actual replay differs from published record: {row["config_id"]}/{row["seed"]}/{row["round"]}/{key}')
            compared_fields.update(fields)
        if fingerprints() != before:
            raise ValueError('Smoke changed source or frozen inputs')
        return {'schema_version': 'member3-smoke-v1', 'passed': True,
                'python': platform.python_version(), 'clean_directory': True,
                'no_model_wallet_git_or_third_party_packages_required': True,
                'network_blocked_in_both_runs': True, 'independent_runs': 2,
                'actual_rounds_per_run': len(replay), 'published_rounds_matched': len(replay),
                'published_fields_compared': sorted(compared_fields), 'byte_identical_outputs': compared,
                'frozen_inputs_unchanged': True, 'source_hashes': before}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error('Refusing to overwrite an existing smoke record')
    try:
        report = smoke()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, report)
        print(f'PASS: two clean offline runs; {report["actual_rounds_per_run"]} rounds each match published records')
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
