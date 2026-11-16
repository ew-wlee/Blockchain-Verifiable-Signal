// Apply the supplied POTX using its original masters, layouts, photographs and logo.
import fs from 'node:fs/promises';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {spawnSync} from 'node:child_process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
const root=process.cwd(), modules=process.env.RUNTIME_NODE_MODULES, skill=process.env.PRESENTATION_SKILL_DIR, python=process.env.RUNTIME_PYTHON;
const template=process.argv[3] || process.env.PRESENTATION_TEMPLATE;
if (!modules || !skill || !python || !template) throw new Error('Set artifact runtime paths and pass OUTPUT_DIR TEMPLATE.potx');
const req=createRequire(path.join(modules,'__runtime__.cjs'));
const {PresentationFile,FileBlob}=await import(pathToFileURL(req.resolve('@oai/artifact-tool')).href);
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
const output=path.resolve(root,process.argv[2] || 'report/presentations');
const build=path.join(root,'artifacts/replay/part3-deck-build',path.basename(output));
await fs.mkdir(build,{recursive:true}); await fs.mkdir(output,{recursive:true});
const source=await fs.readFile(template), templateHash=createHash('sha256').update(source).digest('hex');
// The font inspector reads the same OOXML package through a PPTX reference path.
const reference=path.join(build,'template-reference.pptx'); await fs.writeFile(reference,source);
const lines=(await fs.readFile('artifacts/part3/analysis/summary.csv','utf8')).trim().split(/\r?\n/), header=lines.shift().split(',');
const data=new Map(lines.map(line=>{
 const cells=line.split(','); if(cells.length!==header.length || line.includes('"')) throw new Error('Summary CSV schema changed');
 const r=Object.fromEntries(header.map((key,i)=>[key,cells[i]])); return [`${r.config_id}/${r.metric}`,r];
}));
const demo=JSON.parse(await fs.readFile('artifacts/part3/demo_success.json','utf8'));
const keys=['default-rs-2-4','default-rs-3-6','default-replication-1-2','stress-rs-2-4','stress-rs-3-6','stress-replication-1-2'];
const categories=['Default\nRS(2,4)','Default\nRS(3,6)','Default\n2 replicas','Stress\nRS(2,4)','Stress\nRS(3,6)','Stress\n2 replicas'];
const paper='#E3E2D5',green='#006600',ink='#003E00',orange='#EA8542';
const sourceNote='Sources: artifacts/part3/analysis/summary.csv, artifacts/part3/formal/rounds.csv and configs/retrieval.json. Thirty seed means, 95% Student t intervals with 29 degrees of freedom. Times and traffic are simulated. Public registration: https://sepolia.etherscan.io/tx/0x742eecdd2cffcbd74fe32b61ec0ee3fff48c29d4163d998afe86af8bff5c5e51';
function ph(slide,index,type='body') {
 const s=slide.shapes.items.find(s=>s.placeholder.type===type && s.placeholder.index===index);
 if(!s) throw new Error(`Missing source placeholder ${type}/${index}`); return s;
}
function fill(s,value,size,color=ink,bold=false) {s.text=value;s.text.style={fontFamily:'Arial',fontSize:size,color,bold};return s;}
function text(slide,value,x,y,width,height,size=32,bold=false,color=ink) {
 return fill(slide.shapes.add({geometry:'textbox',position:{left:x,top:y,width,height},fill:'none',line:{fill:'none',width:0}}),value,size,color,bold);
}
function notes(s,value) {s.speakerNotes.text=`${value}\n\n${sourceNote}`;}
function page(source,title,subheading,note) {
 const s=source.duplicate();
 for(const shape of s.shapes.items) if(!shape.placeholder.type) shape.delete();
 fill(ph(s,10),title,85.3467,green,true);fill(ph(s,11),subheading,34.68,ink,true);notes(s,note);return s;
}
function chart(s,probability) {
 const defs=probability ? [{name:'Recovered',metric:'recovery_probability',fill:green},{name:'Accepted',metric:'acceptance_probability',fill:orange}]
  : [{name:'Communication',metric:'communication_bytes',fill:green}];
 s.charts.add('bar',{
  position:{left:175,top:430,width:1445,height:585},categories,
  series:defs.map(x=>({name:x.name,fill:x.fill,values:keys.map(k=>Number((Number(data.get(`${k}/${x.metric}`).mean)/(probability?1:1e6)).toFixed(8)))})),
  barOptions:{direction:'column',grouping:'clustered',gapWidth:70},hasLegend:probability,
  legend:{position:'top',textStyle:{typeface:'Arial',fontSize:30}},
  xAxis:{textStyle:{typeface:'Arial',fontSize:29},majorGridlines:null},
  yAxis:{min:0,max:probability?1.1:4.6,numberFormatCode:probability?'0%':'0.0',
   title:{text:probability?'Probability per attempt':'Communication per attempt (MB)',textStyle:{typeface:'Arial',fontSize:29}},
   textStyle:{typeface:'Arial',fontSize:28},majorGridlines:{fill:'#C8CABC',width:1,style:'solid'}},
  dataLabels:{showValue:!probability,position:'outEnd',numberFormatCode:'0.00',textStyle:{typeface:'Arial',fontSize:29}},chartFill:paper,plotAreaFill:paper
 });
 text(s,probability?'Stress favors two replicas. Recovery and acceptance coincide in this sample.':'Stress favors two replicas in both recovery and communication cost.',195,1040,1425,65,31,true);
 text(s,probability?'Means over 30 seeds. Seed-level 95% CIs are in the accompanying figures.':'1 MB = 1,000,000 bytes. Failed attempts count. Transport and validator traffic are excluded.',195,1120,1425,48,25);
}
for(const mode of ['midterm','final']) {
 const deck=await PresentationFile.importPptx(await FileBlob.load(template));
 if(deck.slides.items.length!==9) throw new Error('Expected the supplied nine-slide HKUST template');
 const originals=deck.slides.items, cover=originals[0], system=originals[1], divider=originals[5], ribbons=originals[8], chosen=[cover];
 fill(ph(cover,0,'title'),'Verifiable\nSignal Retrieval',104.0133,'#6B3964',true);
 fill(ph(cover,12),['BTC/ETH signals and a Sepolia registry','Reconstruction with RS or two replicas','6 configurations and 18,000 attempts',mode==='midterm'?'Midterm review - 21 November 2026':'Blockchain Group Project'].join('\n'),42.67,ink);
 notes(cover,mode==='midterm'?'0:00-0:40. Introduce the question and the three layers: fixed signals, public fingerprints and verified recovery. Explain honest validators and an offline registry snapshot.':'0:00-0:30. Can a real signal file be recovered under node faults and checked against its public version fingerprint?');
 if(mode==='final') {
  for(const [index,value] of [[10,'Three system layers'],[20,'Application'],[14,'Fixed BTC/ETH history\nPinned checkpoint and a six-file bundle'],[21,'Sepolia registry'],[12,'Public version fingerprints\nImmutable objectId record'],[22,'Retrieval'],[18,'RS fragments or two replicas\nRebuild, verify and apply 2/3 acceptance']])
   fill(ph(system,index),value,index===10?85.3467:[20,21,22].includes(index)?34.68:32,index===10?green:ink,[10,20,21,22].includes(index));
  notes(system,'0:30-1:10. Explain the application, provenance and retrieval layers. Hashes establish version consistency, not source truth or trading profitability.');chosen.push(system);
 }
 const d=page(ribbons,'Offline recovery demo','Actual reconstruction',mode==='midterm'?'0:40-1:25. Run python -m demo.run --scenario success. Explain the silent node, corrupt fragment and three distinct valid indices.':'1:10-1:55. Run the offline success command. Explain independent checks and the original BTC signal trace. Completion time is simulated.');
 ['python -m demo.run --scenario success','Node 0 silent. Node 1 returns corrupt bytes.',`Distinct valid indices: ${demo.valid_indices.join(', ')}`,'Manifest hash matches the Sepolia snapshot',`Votes: ${demo.votes.join(' / ')}`,`Accepted: ${demo.accepted}. Completion: ${demo.completion_time} ms (simulated)`].forEach((line,i)=>text(d,line,399,470+88*i,1190,72,i===0?37:35,i===0));
 text(d,'Trace: BTCUSDT, 2024-07-01 00:00 UTC, hold. No network, wallet or model download.',195,1120,1425,48,25);chosen.push(d);
 const r=page(ribbons,'Recovery and acceptance','18,000 attempts',mode==='midterm'?'1:25-2:15. Six configurations share 2x storage. Default recovery: 97.67%, 98.47%, 95.50%. Stress: 51.17%, 42.37%, 62.77%. Explain seed-level uncertainty and retain failed attempts.':'1:55-2:30. Explain the ranking reversal under stress. Recovery and acceptance coincide in this sample. Boundary tests cover late validation.');chart(r,true);chosen.push(r);
 const c=page(ribbons,'Communication cost','Same 2x storage',mode==='midterm'?'2:15-3:00. Count request, received response and evidence bytes, including failures. Default RS recovery is higher with a communication cost. Stress favors replicas on both metrics. Zero corrupt acceptances.':'2:30-3:00. Default means: 3.673, 3.700, 3.589 MB. Stress: 3.960, 4.116, 3.641 MB. Transport, registry reads and validator traffic are excluded.');chart(c,false);chosen.push(c);
 if(mode==='final') {
  fill(ph(ribbons,10),'Experimental design',85.3467,green,true);fill(ph(ribbons,11),'Thirty seed means',34.68,ink,true);
  const values=[['Six configurations','Two environments','Three storage schemes'],['30 seed means','95% Student t intervals','29 degrees of freedom'],['18,000 attempts','100 rounds per seed','All failed attempts retained'],['Zero corrupt acceptances','Independent byte checks','Clean offline reproduction']];
  const labels=ribbons.shapes.items.filter(s=>s.text.toString().startsWith('Lorem ipsum '));
  if(labels.length!==4) throw new Error('Expected the source four-step diagram');
  labels.forEach((s,i)=>{s.text.replace(`Lorem ipsum ${i+1}`,values[i][0]);s.text.replace('LOREM IPSUM DOLOR',values[i][1]);s.text.replace('SIT AMET',values[i][2]);s.text.style={color:'#FFFFFF'};s.position={left:439.9,top:[432.32,616.76,791.09,969.21][i],width:800,height:130};});
  notes(ribbons,'3:00-3:45. The seed is the statistical unit. Compute each seed metric, then a Student t interval across thirty seeds. Audit covers coverage, storage, fingerprints, votes, deadline and bytes.');chosen.push(ribbons);
  fill(ph(divider,12),'Conclusions and limits',64,'#FFFFFF',true);
  fill(ph(divider,11),'Public fingerprints identify a registered version.\n\nRecovery depends on faults, threshold and deadline.\n\nUnder stress, two replicas offer the better tradeoff.\n\nIndependent simulated nodes and three honest validators.\n\nNo real-network or trading-profit claim.',32,'#FFFFFF');
  notes(divider,'3:45-5:00. At 40% honest nodes, requiring three distinct valid fragments can be harder than finding one full replica. Limit conclusions to the simulation, trusted catalog and authentic offline anchor. No complete BFT or universal security guarantee.');chosen.push(divider);
  const b=page(ribbons,'Confidence intervals','Backup','Exact 95% Student t intervals across thirty seed means. Completion time is conditional on recovery.');
  const format=(key,metric,scale,digits)=>{const x=data.get(`${key}/${metric}`);return `${(x.mean/scale).toFixed(digits)} [${(x.ci_low/scale).toFixed(digits)}, ${(x.ci_high/scale).toFixed(digits)}]`;};
  const table=b.tables.add({rows:7,columns:4,left:175,top:445,width:1445,height:550,columnWidths:[300,400,355,390],values:[['Configuration','Recovery / acceptance (%)','Communication (MB)','Completion (ms)'],...keys.map((key,i)=>[categories[i].replace('\n',' '),format(key,'recovery_probability',.01,2),format(key,'communication_bytes',1e6,3),format(key,'completion_time_ms',1,2)])]});
  for(let row=0;row<7;row++)for(let col=0;col<4;col++){const cell=table.getCell(row,col);cell.fill=row===0?green:row%2?'#F2F1E9':paper;cell.text.style={fontFamily:'Arial',fontSize:28,bold:row===0,color:row===0?'#FFFFFF':ink};}
  table.borders.assign({fill:'#C7C9B9',width:1,style:'solid'});text(b,'Completion is conditional on recovery. Probabilities and communication include every attempt.',195,1080,1425,75,27);chosen.push(b);
  const f=page(ribbons,'Failure cases','Backup','Prepare threshold, duplicate-index and deadline examples. The full replay and clean offline smoke are in artifacts/part3.');
  ['Fewer than k distinct valid fragments: reject','Repeated indices do not advance recovery','Recovery followed by validation after 250 ms: reject','40% honest nodes: a higher threshold can lose availability'].forEach((line,i)=>text(f,line,399,480+135*i,1190,105,36));
  text(f,'Evidence: formal CSV, seed aggregates, audit, full replay and clean offline smoke.',195,1120,1425,48,25);chosen.push(f);
 }
 deck.slides.keep(chosen.map(s=>s.id));deck.slides.reorder(chosen.map(s=>s.id));
 if(deck.slides.items.length!==(mode==='midterm'?4:9))throw new Error('Wrong output slide count');
 for(const [i,s]of deck.slides.items.entries()){const png=await deck.export({slide:s,format:'png',scale:.65});await fs.writeFile(path.join(build,`${mode}-${i+1}.png`),new Uint8Array(await png.arrayBuffer()));}
 const candidate=path.join(build,`${mode}-candidate.pptx`);await(await PresentationFile.exportPptx(deck)).save(candidate);
 const optimized=path.join(build,`${mode}-optimized.pptx`);
 const pruning=spawnSync(python,['-B',path.join(root,'tools/prune_presentation_assets.py'),candidate,optimized],{encoding:'utf8'});
 if(pruning.status!==0)throw new Error(pruning.stdout+'\n'+pruning.stderr);
 console.log(pruning.stdout.trim());
 await finalizePresentation({workspaceDir:root,candidatePath:optimized,finalPath:path.join(output,`${mode}.pptx`),pythonExecutable:python,
  integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','20105688,11309350','--validate-heading-fit',...(mode==='final'?['--require-native-table-slide','8']:[])],
  explicitTotalSlideCount:mode==='final'?9:4,requiredNativeChartOwnerSlides:mode==='final'?[4,5]:[3,4],requiredNativeTableOwnerSlides:mode==='final'?[8]:[],
  materializeLiteralChartWorkbooks:true,fontPolicy:{basis:'reference',families:['Arial','DIN Black'],referencePath:reference,referenceSha256:templateHash},verifyArtifactToolImport:true,
  receiptPath:path.join(build,`${mode}-validation.json`)});
 console.log(`${mode}: ${deck.slides.items.length} template-based editable slides`);
}
await fs.writeFile(path.join(output,'template_source.json'),JSON.stringify({schema_version:'member3-presentation-template-v1',source_name:path.basename(template),source_sha256:templateHash,source_bytes:source.length,dimensions_emu:[20105688,11309350],template_imported:true,original_template_unchanged:true,retained_elements:['Original used masters and layouts','Campus photographs and crops','HKUST cover logo','Arial typography','Green titles and beige backgrounds'],midterm_structure:['Project and system overview','Offline recovery demo','Recovery and acceptance chart','Communication chart and conclusion']},null,2)+'\n');
