"""Read-only Part 2 validation against Member 1's existing frozen artifact."""
import csv
import hashlib
import itertools
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import time
from unittest.mock import patch

ROOT=Path(os.environ.get('PART2_REPO', str(Path.cwd()))).resolve()
OUT=Path(os.environ.get('PART2_REPORT_DIR', str(ROOT/'artifacts/replay/part2-validation'))).resolve()
sys.path.insert(0,str(ROOT))
from retrieval.bundle import pack_bundle,unpack_bundle,validate_payload
from retrieval.engine import prepare,Environment,Response,run_round
from tools.verify_bundle import verify


def sha(data): return hashlib.sha256(data).hexdigest()
def dump(name,data): (OUT/name).write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')

def main():
    started=time.perf_counter()
    if OUT.exists() and any(OUT.iterdir()):
        raise SystemExit('Choose a new empty PART2_REPORT_DIR; refusing to overwrite evidence.')
    OUT.mkdir(parents=True,exist_ok=True)
    source=ROOT/'artifacts/step2/bundle'
    anchor_path=ROOT/'deployments/sepolia.json'
    before={p.name:sha(p.read_bytes()) for p in source.iterdir() if p.is_file()}
    anchor_before=sha(anchor_path.read_bytes())
    anchor=json.loads(anchor_path.read_text())
    assert anchor['object_id']=='0x1143b163d9a56e29e5714b7cd7ca41c6d05353ceb903704316219060915a4d07'
    assert anchor['manifest_hash']=='0x3f3fa32d9c98ba929c9e002f0d9e6c35ec46b42a36344c64bdb8426257870ca6'
    report=verify(source,anchor['manifest_hash'][2:]);assert report['passed']
    dump('bundle_verification.json',report)
    payload=pack_bundle(source)
    expected_files={name:(source/name).read_bytes() for name in before}
    assert unpack_bundle(payload)==expected_files
    assert validate_payload(payload,anchor)['passed']
    envvars={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    completed=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests_part2','-v'],cwd=ROOT,capture_output=True,text=True,env=envvars)
    (OUT/'unit_tests.txt').write_text(completed.stdout+completed.stderr)
    assert completed.returncode==0
    print('Unit suite passed; frozen bundle validated.',flush=True)
    demos={}
    for scenario in ('success','insufficient','deadline','replication'):
        cmd=[sys.executable,'-m','demo.run','--scenario',scenario]
        if scenario=='success': cmd+=['--recover-to',str(OUT/'recovered_bundle')]
        first=subprocess.run(cmd,cwd=ROOT,capture_output=True,check=True,env=envvars)
        second=subprocess.run([sys.executable,'-m','demo.run','--scenario',scenario],cwd=ROOT,capture_output=True,check=True,env=envvars)
        assert first.stdout==second.stdout
        data=json.loads(first.stdout);dump('demo_'+scenario+'.json',data)
        assert data['accepted']==(scenario in ('success','replication'))
        demos[scenario]={'accepted':data['accepted'],'recovered':data['recovered'],'hash_match':data['hash_match'],
                         'failure_reason':data['failure_reason'],'completion_time_ms':data['completion_time'],
                         'two_runs_identical':True}
    assert {p.name:sha(p.read_bytes()) for p in (OUT/'recovered_bundle').iterdir()}==before
    dump('recovered_files_comparison.json',{'passed':True,'source_hashes':before,
         'recovered_hashes':{p.name:sha(p.read_bytes()) for p in (OUT/'recovered_bundle').iterdir()}})
    print('Four demos are deterministic; exported recovered files match byte for byte.',flush=True)
    config_raw=(ROOT/'configs/retrieval.json').read_bytes();config=json.loads(config_raw)
    prepared=[prepare(payload,anchor,**s) for s in config['schemes']]
    expected_digest=sha(payload)
    exhaustive={}
    # Test every categorical node assignment with all replies before deadline.
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden in validation')):
        for p in prepared:
            key=f'{p.scheme}-{p.k}-{p.n}';count=0
            for states in itertools.product(('honest','silent','invalid'),repeat=p.n):
                responses=[]
                for i,state in enumerate(states):
                    if state=='silent': continue
                    shard=p.fragments[i]
                    if state=='invalid': shard=bytes([shard[0]^1])+shard[1:]
                    responses.append(Response(i,p.object_id,i,shard,10+i))
                row,recovered=run_round(p,Environment(),responses=responses)
                expected=states.count('honest')>=p.k
                assert row['accepted']==expected,(key,states)
                assert row['recovered']==expected
                if expected: assert recovered==payload
                else: assert recovered is None
                count+=1
            exhaustive[key]={'assignments':count,'passed':True}
            print(f'Exhaustive {key}: {count} node-state assignments passed.',flush=True)
        fields=['run_id','config_id','seed','round','k','n','fragment_count','requested_fragments','storage_bytes','storage_overhead','recovered','accepted','hash_match','votes',
                'valid_fragments','completion_time','failure_reason','request_bytes','response_bytes','evidence_bytes',
                'communication_bytes','independent_corrupted_acceptance','payload_sha256','reconstructed_sha256','reconstructed_manifest_hash']
        aggregates={};total=0;errors=0;seed_stats={}
        with (OUT/'rounds.csv').open('w',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=fields);writer.writeheader()
            for env_name,parameters in config['environments'].items():
                env=Environment(**parameters)
                for p in prepared:
                    key=f'{env_name}-{p.scheme}-{p.k}-{p.n}'
                    agg={'rounds':0,'recovered':0,'accepted':0,'independent_corrupted_acceptance':0,
                         'communication_bytes_total':0,'failure_reasons':{},'storage_overhead':p.storage_overhead}
                    assert p.storage_overhead==2
                    for seed in range(30):
                        seed_agg={'seed':seed,'rounds':0,'recovered':0,'accepted':0,'communication_bytes':0}
                        for round_number in range(100):
                            row,recovered=run_round(p,env,seed,round_number,run_id='part2-full-validation',config_id=key)
                            valid_bytes=recovered is not None and sha(recovered)==expected_digest
                            # Independent of engine's own corruption counter.
                            corrupted=int(row['accepted'] and not valid_bytes)
                            assert row['recovered']==valid_bytes
                            assert not row['accepted'] or (valid_bytes and row['hash_match'] and row['votes'].count('ACCEPT')>=2 and row['completion_time']<=env.deadline_ms)
                            assert not row['recovered'] or row['reconstructed_manifest_hash']==anchor['manifest_hash']
                            assert row['communication_bytes']==sum(row[t] for t in ('request_bytes','response_bytes','evidence_bytes'))
                            assert row['request_bytes']>0 and row['communication_bytes']>=row['request_bytes']
                            assert corrupted==0
                            writer.writerow({f:(json.dumps(row[f],separators=(',',':')) if f=='votes' else corrupted if f=='independent_corrupted_acceptance' else row.get(f)) for f in fields})
                            total+=1;errors+=corrupted;agg['rounds']+=1;agg['recovered']+=int(row['recovered']);agg['accepted']+=int(row['accepted'])
                            agg['communication_bytes_total']+=row['communication_bytes']
                            reason=row['failure_reason']
                            if reason: agg['failure_reasons'][reason]=agg['failure_reasons'].get(reason,0)+1
                            seed_agg['rounds']+=1;seed_agg['recovered']+=int(row['recovered']);seed_agg['accepted']+=int(row['accepted']);seed_agg['communication_bytes']+=row['communication_bytes']
                        seed_stats.setdefault(key,[]).append(seed_agg)
                    agg['recovery_probability']=agg['recovered']/agg['rounds'];agg['acceptance_probability']=agg['accepted']/agg['rounds']
                    agg['mean_communication_bytes']=agg['communication_bytes_total']/agg['rounds'];aggregates[key]=agg
                    print(f'{key}: {agg["rounds"]} rounds, recovery={agg["recovery_probability"]:.4f}, acceptance={agg["acceptance_probability"]:.4f}',flush=True)
    assert total==18000
    after={p.name:sha(p.read_bytes()) for p in source.iterdir() if p.is_file()}
    assert before==after and sha(anchor_path.read_bytes())==anchor_before
    sources={str(p.relative_to(ROOT)):sha(p.read_bytes()) for folder in ('retrieval','demo','tests_part2') for p in (ROOT/folder).glob('*.py')}
    dump('seed_totals.json',seed_stats)
    summary={'passed':True,'python':platform.python_version(),'platform':platform.platform(),
             'elapsed_seconds':round(time.perf_counter()-started,3),'network':'retrieval and exhaustive rounds blocked; demos enforce offline themselves',
             'anchor_mode':'existing offline snapshot; no new chain query or transaction',
             'object_id':anchor['object_id'],'manifest_hash':anchor['manifest_hash'],'registration_tx':anchor['registration_tx'],
             'payload_sha256':expected_digest,'config_sha256':sha(config_raw),'source_hashes':sources,
             'frozen_inputs_unchanged':True,'unit_suite_passed':True,'unit_test_groups':13,
             'demos':demos,'exhaustive':exhaustive,'full_rounds':total,'round_record_fields':fields,'independent_corrupted_acceptance':errors,
             'configurations':aggregates,
             'limitations':['Simulated independent nodes and delays, no real network benchmark.',
                            'Honest online validators and trusted setup fragment catalog.',
                            'No replay of Part 1 inference or live registry getSignal.',
                            'Descriptive validation results only; Member 3 still owns CI analysis, charts and final report.']}
    dump('summary.json',summary)
    checksums={p.name:sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file() and p.name!='checksums.json'}
    dump('checksums.json',checksums)
    print(f'ALL PASSED: {total} seeded rounds + {sum(v["assignments"] for v in exhaustive.values())} exhaustive node assignments; {summary["elapsed_seconds"]}s.',flush=True)


if __name__=='__main__': main()
