"""Replay actual retrieval rounds to recover omitted fields; never infer votes."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import csv
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time
from unittest.mock import patch

NEW_FIELDS=['run_id','fragment_count','requested_fragments','votes']

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()

def replay_group(root_text,out_text,config_id,old_rows,headers):
    root=Path(root_text);out=Path(out_text)
    sys.path.insert(0,str(root))
    from retrieval.bundle import pack_bundle,digest
    from retrieval.engine import Environment,prepare,run_round
    config=json.loads((root/'configs/retrieval.json').read_text())
    env_name,scheme,k,n=config_id.split('-');k,n=int(k),int(n)
    env=Environment(**config['environments'][env_name])
    anchor=json.loads((root/'deployments/sepolia.json').read_text())
    payload=pack_bundle(root/'artifacts/step2/bundle')
    prepared=prepare(payload,anchor,scheme,k,n)
    path=out/(config_id+'.csv');accepted=recovered_count=0
    vote_counts={}
    with patch.object(socket.socket,'connect',side_effect=AssertionError('Offline replay')):
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader()
            for old in old_rows:
                row,data=run_round(prepared,env,int(old['seed']),int(old['round']),run_id='part2-full-validation',config_id=config_id)
                independent=int(row['accepted'] and (data is None or digest(data)!=digest(payload)))
                assert independent==0
                row['independent_corrupted_acceptance']=independent
                for field,value in old.items():
                    actual='' if row.get(field) is None else str(row[field])
                    if actual!=value:
                        raise AssertionError(f'{config_id}/{old["seed"]}/{old["round"]}: {field}: {value!r} != {actual!r}')
                assert len(row['votes'])==3
                assert row['fragment_count']==row['n']
                assert row['requested_fragments']==row['n']
                assert row['run_id']=='part2-full-validation'
                key=','.join(row['votes']);vote_counts[key]=vote_counts.get(key,0)+1
                complete={field:row[field] for field in headers}
                complete['votes']=json.dumps(row['votes'],separators=(',',':'))
                writer.writerow(complete)
                accepted+=int(row['accepted']);recovered_count+=int(row['recovered'])
    return {'config_id':config_id,'rows':len(old_rows),'all_original_fields_identical':True,
            'accepted':accepted,'recovered':recovered_count,'vote_counts':vote_counts,'file':path.name,'sha256':sha(path)}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--original',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=min(6,os.cpu_count() or 1))
    a=parser.parse_args();root=a.repo.resolve();out=a.output.resolve()
    out.mkdir(parents=True,exist_ok=True)
    if (out/'rounds.csv').exists(): raise SystemExit('Refusing to overwrite an existing replay')
    before={str(p.relative_to(root)):sha(p) for folder in ['retrieval','artifacts/step2/bundle','deployments'] for p in (root/folder).glob('*') if p.is_file()}
    original_sha=sha(a.original)
    with a.original.open() as f:
        reader=csv.DictReader(f);old_headers=reader.fieldnames;rows=list(reader)
    assert len(rows)==18000
    assert all(f not in old_headers for f in NEW_FIELDS)
    headers=['run_id']+old_headers+['fragment_count','requested_fragments','votes']
    groups={}
    for row in rows: groups.setdefault(row['config_id'],[]).append(row)
    assert len(groups)==6
    started=time.perf_counter();results={}
    with ProcessPoolExecutor(max_workers=max(1,min(6,a.workers))) as pool:
        futures={pool.submit(replay_group,str(root),str(out),key,values,headers):key for key,values in groups.items()}
        for future in as_completed(futures):
            r=future.result();results[r['config_id']]=r
            print(f"{r['config_id']}: {r['rows']} replayed; all old fields identical; votes recorded.",flush=True)
    with (out/'rounds.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=headers);writer.writeheader()
        for key in groups:
            with (out/(key+'.csv')).open() as source:
                writer.writerows(csv.DictReader(source))
    with (out/'rounds.csv').open() as f: complete=list(csv.DictReader(f))
    assert len(complete)==len(rows)
    for old,new in zip(rows,complete):
        assert all(new[k]==v for k,v in old.items())
        assert len(json.loads(new['votes']))==3
        assert all(new[k]!='' for k in NEW_FIELDS)
    after={str(p.relative_to(root)):sha(p) for folder in ['retrieval','artifacts/step2/bundle','deployments'] for p in (root/folder).glob('*') if p.is_file()}
    assert before==after
    assert sha(a.original)==original_sha
    report={'passed':True,'rows':len(complete),'added_fields':NEW_FIELDS,'original_fields':old_headers,
            'all_original_fields_identical':True,'original_csv_sha256':original_sha,
            'complete_csv_sha256':sha(out/'rounds.csv'),'inputs_and_retrieval_code_unchanged':True,
            'config_sha256':sha(root/'configs/retrieval.json'),'source_hashes':before,
            'elapsed_seconds':round(time.perf_counter()-started,3),'configurations':results,
            'method':'Actual deterministic rerun; vote lists taken from run_round output, JSON-serialized. Original CSV not modified.'}
    (out/'record_completion_audit.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(f'COMPLETE: 18,000 actual rounds; old fields match exactly; {report["elapsed_seconds"]}s.',flush=True)

if __name__=='__main__':main()
