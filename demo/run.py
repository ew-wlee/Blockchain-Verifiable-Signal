"""Run from repository root: python -m demo.run --scenario success."""
import argparse
import json
import socket
from pathlib import Path
from unittest.mock import patch
from retrieval.bundle import pack_bundle, trace_signal, unpack_bundle
from retrieval.engine import Environment, Response, prepare, run_round

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=['success', 'insufficient', 'deadline', 'replication'], default='success')
    parser.add_argument('--output', type=Path, help='Optional JSON trace; timings are simulated milliseconds')
    parser.add_argument('--recover-to', type=Path, help='Write recovered six files to a new directory')
    args = parser.parse_args(argv)
    # A runnable assertion of the offline requirement; accidental sockets fail.
    with patch.object(socket.socket, 'connect', side_effect=RuntimeError('Demo must remain offline')):
        anchor = json.loads((ROOT / 'deployments/sepolia.json').read_text())
        payload = pack_bundle(ROOT / 'artifacts/step2/bundle')
        scheme = 'replication' if args.scenario == 'replication' else 'rs'
        prepared = prepare(payload, anchor, scheme)
        responses = []
        for i, shard in enumerate(prepared.fragments):
            if i == 0:
                continue  # silent node
            data = bytes([shard[0] ^ 1]) + shard[1:] if i == 1 and scheme == 'rs' else shard
            delay = 20.0 + i * 10
            if args.scenario == 'insufficient' and i >= 4:
                continue
            if args.scenario == 'deadline' and i >= 4:
                delay = 500.0
            responses.append(Response(i, prepared.object_id, i, data, delay))
        result, recovered = run_round(prepared, Environment(), responses=responses, run_id='offline-demo')
        result['registration_tx'] = anchor['registration_tx']
        result['contract_address'] = anchor['contract_address']
        result['explorer'] = anchor['explorer']
        result['silent_nodes'] = [0]
        if recovered:
            result['trace'] = trace_signal(recovered)
            if args.recover_to:
                args.recover_to.mkdir(parents=True, exist_ok=False)
                for name, content in unpack_bundle(recovered).items():
                    (args.recover_to / name).write_bytes(content)
        text = json.dumps(result, indent=2, sort_keys=True)
        print(text)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + '\n')
    expected = args.scenario in ('success', 'replication')
    return 0 if result['accepted'] == expected else 1


if __name__ == '__main__':
    raise SystemExit(main())
