"""Compare every published field against an actual Member 3 formal rerun."""
import argparse
import csv
import math
from pathlib import Path

from evaluation.common import read_json, verify_checksums, write_json
from retrieval.bundle import digest


def equal_field(field, left, right):
    if left == right:
        return True
    if field != 'completion_time' or not left or not right:
        return False
    try:
        a, b = float(left), float(right)
    except ValueError:
        return False
    # macOS and Windows libm can differ by one ULP in lognormal completion times.
    return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=0, abs_tol=1e-12)


def compare(current, published):
    def records(directory):
        verify_checksums(directory)
        with (directory / 'rounds.csv').open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        indexed = {(r['config_id'], r['seed'], r['round']): r for r in rows}
        if len(indexed) != 18000 or len(rows) != 18000:
            raise ValueError('Expected 18,000 unique actual round records')
        return indexed
    actual, original = records(current), records(published)
    if actual.keys() != original.keys():
        raise ValueError('Round identities differ')
    fields = set(next(iter(original.values()))) - {'run_id'}
    time_differences = []
    for key, old in original.items():
        for field in fields:
            value = actual[key].get(field)
            if not equal_field(field, value, old[field]):
                raise ValueError(f'Replay mismatch: {key}/{field}')
            if field == 'completion_time' and value != old[field]:
                time_differences.append(abs(float(value) - float(old[field])))
    return {'schema_version': 'member3-full-replay-v1', 'passed': True, 'rounds_compared': len(actual),
            'fields_compared': sorted(fields), 'ignored_field': 'run_id identifies the independent execution',
            'time_comparison_absolute_tolerance_ms': 1e-12, 'all_other_fields_exact': True,
            'completion_time_tail_differences': len(time_differences),
            'maximum_completion_time_difference_ms': max(time_differences, default=0),
            'published_csv_sha256': digest((published / 'rounds.csv').read_bytes()),
            'current_csv_sha256': digest((current / 'rounds.csv').read_bytes()),
            'current_python': read_json(current / 'run.json')['python'],
            'published_python': read_json(published / 'summary.json')['python'],
            'comparison_script_sha256': digest(Path(__file__).read_bytes())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--current', type=Path, default=Path('artifacts/part3/formal'))
    parser.add_argument('--published', type=Path, default=Path('artifacts/part2-validation-20261115'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to overwrite a replay record')
    try:
        report = compare(args.current, args.published)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, report)
        print('PASS: 18,000 rounds; non-time fields exact, completion times within 1e-12 ms; run_id identifies the new execution')
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
