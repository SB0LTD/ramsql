import json,pathlib,statistics
root=pathlib.Path('benchmarks');groups={};sources=[]
for trial in (1,2,3):
 for engine in ('ramsql','postgres','irdb'):
  folder=root/f'dbmeter-repeat-{trial}-{engine}-20261028'
  report=json.loads((folder/'results.json').read_text())
  assert report['checks_passed'] and report['normalized_source_matches_head']
  assert report['duration']=='10s' and report['warmup']=='3s' and report['java_max_heap']=='256m'
  assert len(report['runs'])==6 and all(r['passed'] for r in report['runs'])
  sources.append(dict(path=str(folder/'results.json'),engine=engine,trial=trial,binary=report.get('binary'),revision=report['dbmeter_revision']))
  for run in report['runs']:
   data=json.loads((folder/run['name']/'local.json').read_text())
   scenarios=list(data['scenarios'].values());assert scenarios and all(s['errors']==0 and s['count']>0 for s in scenarios)
   # dbMeter schedules duration with a monotonic clock but emits millisecond
   # wall-clock endpoints; one 10s trial has a 9,999ms reported window.
   assert all(node['settings']['duration']=='PT10S' for node in data['nodes'])
   seconds=(data['measuredEnd']-data['measuredStart'])/1000;assert seconds>=9.999
   count=sum(s['count'] for s in scenarios)
   key=(engine,run['size'],run['threads'],run['kind'])
   groups.setdefault(key,[]).append(count/seconds)
out=dict(schema=1,method='Three independent JVMs per case, rotating engine order, 3s warmup / 10s measured, original reviewer checks; median/min/max, no confidence claim',sources=sources,groups=[])
for key,rates in sorted(groups.items()):
 assert len(rates)==3
 e,n,t,k=key;entry=dict(engine=e,rows=n,clients=t,query=k,trials=rates,median=statistics.median(rates),minimum=min(rates),maximum=max(rates))
 out['groups'].append(entry)
 print(e,t,k,*(round(v,1) for v in [entry['median'],entry['minimum'],entry['maximum']]))
target=root/'dbmeter-reviewer-20261027/evidence/repeated-summary.json'
target.write_text(json.dumps(out,indent=2)+'\n')
