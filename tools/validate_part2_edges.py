import json,sys,io,zipfile,os
from pathlib import Path
from dataclasses import replace
ROOT=Path(os.environ.get('PART2_REPO', str(Path.cwd()))).resolve()
OUT=Path(os.environ.get('PART2_REPORT_DIR', str(ROOT/'artifacts/replay/part2-validation'))).resolve()
sys.path.insert(0,str(ROOT))
from retrieval.bundle import pack_bundle,unpack_bundle,validate_payload
from retrieval.engine import prepare,Environment,Response,run_round
anchor=json.loads((ROOT/'deployments/sepolia.json').read_text());payload=pack_bundle(ROOT/'artifacts/step2/bundle')
p=prepare(payload,anchor);checks={}
for field in ('object_id','data_hash','model_hash','signal_hash','manifest_hash'):
 checks['reject_wrong_anchor_'+field]=not validate_payload(payload,{**anchor,field:'0x'+'0'*64})['passed']
files=unpack_bundle(payload)
for name in files:
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:
  for key,value in files.items(): z.writestr(key,value+b'\n' if key==name else value)
 checks['reject_repacked_tampered_'+name]=not validate_payload(out.getvalue(),anchor)['passed']
for compression in (zipfile.ZIP_DEFLATED,):
 out=io.BytesIO()
 with zipfile.ZipFile(out,'w',compression=compression) as z:
  for key,value in files.items(): z.writestr(key,value)
 checks['reject_unsupported_compression']=not validate_payload(out.getvalue(),anchor)['passed']
base=[Response(i,p.object_id,i,p.fragments[i],10+i) for i in range(6)]
for delay in (-1,float('nan'),float('inf')):
 result,_=run_round(p,Environment(),responses=[replace(r,latency_ms=delay) for r in base])
 checks['reject_invalid_latency_'+str(delay)]=not result['accepted'] and result['valid_fragments']==0
wrong=[replace(r,node=r.node+10) for r in base]
r,_=run_round(p,Environment(),responses=wrong)
checks['reject_node_index_mismatch']=not r['accepted'] and all(e['reason']=='wrong_node_index' for e in r['events'])
checks['all_passed']=all(checks.values());assert checks['all_passed']
(OUT/'additional_edge_checks.json').write_text(json.dumps(checks,indent=2,sort_keys=True)+'\n')
print('Additional integrity/boundary checks passed:',len(checks)-1)
