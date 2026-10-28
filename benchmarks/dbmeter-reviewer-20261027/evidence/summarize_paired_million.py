import json,pathlib,statistics
root=pathlib.Path('benchmarks')
out=dict(schema=1,method='Three alternating pairs, native Windows, four workers/eight JDBC clients, default text128, original seven checks then original six-query weights; 5s ramp/5s warmup/30s measured. Single host, no confidence claim.',experiments=[])
for prefix in ['ordered','directory']:
 experiment=dict(name=prefix,variants=[])
 for variant in ['before','after']:
  trials=[]
  for trial in [1,2,3]:
   folder=root/f'dbmeter-{prefix}-million-{trial}-{variant}-20261028'
   meta=json.loads((folder/'results.json').read_text());server=json.loads((folder/'server.json').read_text())
   assert meta['checks_passed'] and len(meta['runs'])==1 and all(r['passed'] for r in meta['runs'])
   data=json.loads((folder/'ramsql-1000000-8-mixed/local.json').read_text())
   assert len(data['scenarios'])==6 and all(s['errors']==0 and s['count']>0 for s in data['scenarios'].values())
   assert all(n['settings']['duration']=='PT30S' and n['settings']['warmup']=='PT5S' for n in data['nodes'])
   seconds=(data['measuredEnd']-data['measuredStart'])/1000
   trials.append(dict(path=str(folder),ops_per_second=sum(s['count'] for s in data['scenarios'].values())/seconds,binary_sha256=server['sha256']))
  assert len(set(t['binary_sha256'] for t in trials))==1
  rates=[t['ops_per_second'] for t in trials]
  experiment['variants'].append(dict(name=variant,trials=trials,median=statistics.median(rates),minimum=min(rates),maximum=max(rates)))
 out['experiments'].append(experiment)
 print(prefix,[(v['name'],round(v['median'],2)) for v in experiment['variants']])
(root/'dbmeter-reviewer-20261027/evidence/paired-million-summary.json').write_text(json.dumps(out,indent=2)+'\n')
