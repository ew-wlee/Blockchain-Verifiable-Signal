"""Independent Member 1 audit. Offline by default; --rpc-url enables read-only receipt checks.

Run from repository root: python -m tools.audit_member1 --output artifacts/part2/member1_audit.json
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
from tools.verify_bundle import canonical_bytes, verify

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rpc-url')
    args = parser.parse_args()
    bundle = ROOT / 'artifacts/step2/bundle'
    anchor = json.loads((ROOT / 'deployments/sepolia.json').read_text())
    manifest = json.loads((bundle / 'manifest.json').read_text())
    model = json.loads((bundle / 'model_reference.json').read_text())
    checks = {}
    checks['bundle_verification'] = verify(bundle, anchor['manifest_hash'][2:])['passed']
    checks['anchor_object_id'] = anchor['object_id'] == manifest['object_id']
    for field, name in [('data_hash','dataset.csv'), ('signal_hash','signals.csv'), ('config_hash','config.json')]:
        checks[field] = anchor[field] == '0x' + hashlib.sha256((bundle / name).read_bytes()).hexdigest()
    checks['model_hash'] = anchor['model_hash'] == '0x' + model['checkpoint_commitment_sha256']
    checks['abi_hash'] = hashlib.sha256((ROOT / anchor['abi_path']).read_bytes()).hexdigest() == anchor['abi_sha256']
    rows = list(csv.DictReader((bundle / 'signals.csv').open()))
    values = sorted(abs(Decimal(r['predicted_return'])) for r in rows if r['split']=='validation')
    mid = len(values)//2
    tau = ((values[mid - 1] + values[mid])/2 if len(values)%2==0 else values[mid]).quantize(Decimal('0.000000000001'))
    checks['validation_tau_recomputed'] = all(Decimal(r['tau'])==tau for r in rows)
    checks['signal_classification'] = all(r['signal'] == ('buy' if Decimal(r['predicted_return'])>tau else 'sell' if Decimal(r['predicted_return']) < -tau else 'hold') for r in rows)
    checks['next_hour_alignment'] = all(datetime.fromisoformat(r['signal_timestamp_utc']) - datetime.fromisoformat(r['context_end_timestamp_utc']) == timedelta(hours=1) for r in rows)
    receipt = None
    if args.rpc_url:
        request = {'jsonrpc':'2.0','id':1,'method':'eth_getTransactionReceipt','params':[anchor['registration_tx']]}
        raw = subprocess.check_output(['curl','--fail','--max-time','30','-sS', args.rpc_url, '-H','Content-Type: application/json','--data',json.dumps(request)], text=True)
        receipt = json.loads(raw)['result']
        checks['rpc_receipt_success'] = receipt is not None and receipt['status']=='0x1'
        checks['rpc_receipt_address'] = receipt['to'].lower()==anchor['contract_address'].lower()
        checks['rpc_receipt_block'] = int(receipt['blockNumber'],16)==anchor['registration_block_number']
        events = [e for e in receipt['logs'] if e['address'].lower()==anchor['contract_address'].lower() and len(e['topics'])==3 and e['topics'][1]==anchor['object_id']]
        expected_data = ''.join(anchor[k][2:] for k in ['data_hash','model_hash','signal_hash','manifest_hash']) + f"{int(anchor['registration_timestamp']):064x}"
        checks['rpc_registration_event'] = any(e['data']=='0x'+expected_data and e['topics'][2][-40:].lower()==anchor['provider'][2:].lower() for e in events)
    report = {
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'checks': checks, 'passed': all(checks.values()), 'signals':len(rows), 'tau':str(tau),
        'receipt':receipt, 'rpc_url':args.rpc_url,
        'limitations': [
            'Step 1 artifacts, raw cache, model weights and model pin are absent from clone; full inference replay was not performed.',
            'configs/step1.yaml revision is null; a fresh clone resolves main rather than necessarily the frozen revision.',
            'Receipt/event verification does not independently rerun getSignal or compare deployed runtime bytecode.',
            'Test suite results are recorded separately in the audit note; frozen historical reports were not modified.'
        ]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    print(json.dumps({'passed':report['passed'],'checks':checks},indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
