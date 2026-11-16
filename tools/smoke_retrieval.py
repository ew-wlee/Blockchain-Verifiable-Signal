"""Integration pilot, not Member 3's final statistical evaluation.

python -m tools.smoke_retrieval --output artifacts/part2/pilot.json
"""
import argparse
import json
from pathlib import Path
from retrieval.bundle import pack_bundle, digest
from retrieval.engine import Environment, prepare, run_round

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    anchor = json.loads((ROOT / 'deployments/sepolia.json').read_text())
    payload = pack_bundle(ROOT / 'artifacts/step2/bundle')
    config_bytes = (ROOT / 'configs/retrieval.json').read_bytes()
    config = json.loads(config_bytes)
    prepared = [prepare(payload, anchor, **scheme) for scheme in config['schemes']]
    rows = []
    for label, parameters in config['environments'].items():
        env = Environment(**parameters)
        for p in prepared:
            for seed in range(3):
                for round_number in range(30):
                    result, _ = run_round(p, env, seed, round_number, run_id='member2-pilot', config_id=f'{label}-{p.scheme}-{p.k}-{p.n}')
                    if result['corrupted_data_acceptance_count']:
                        raise AssertionError('corrupted object accepted')
                    rows.append(result)
    report = {'purpose':'integration pilot; not final evaluation', 'rounds':len(rows),
              'corrupted_data_acceptance_count':sum(r['corrupted_data_acceptance_count'] for r in rows),
              'config_sha256':digest(config_bytes), 'payload_sha256':digest(payload),
              'configurations':sorted({r['config_id'] for r in rows}), 'results':rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, sort_keys=True, separators=(',',':'))+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='results'}, indent=2))


if __name__ == '__main__': main()
