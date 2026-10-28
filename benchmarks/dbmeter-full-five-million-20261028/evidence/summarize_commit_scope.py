import json,pathlib,statistics
root=pathlib.Path('.sig-cache');dest=pathlib.Path('benchmarks/dbmeter-full-five-million-20261028')
report=dict(schema=1,method='Three eight-worker/eight-client trials per binary, original 5s ramp / 15s warmup / 60s measurement, real 5M data reloaded into a fresh RAMSQL process. Trial 1 separated by isolated reference engine runs; later pairs alternate after/before, then before/after. Single-host medians/ranges, not confidence intervals. Other database services stopped.',groups={})
for group,labels in [('before',['ramsql-w8','ramsql-before-repeat2','ramsql-before-repeat3']),('after',['ramsql-commit-w8','ramsql-commit-repeat2','ramsql-commit-repeat3'])]:
 runs=[]
 for label in labels:
  folder=root/('dbmeter-isolated-full-'+label)
  p=folder/'ramsql-5m-8-q6_store_category_mix/local.json'
  raw=json.loads(p.read_text());provenance=json.loads((folder/'ramsql-full-results.json').read_text())
  assert provenance['supported_checks_passed'] and 'error' not in provenance
  assert raw['nodes'][0]['settings']==dict(threads=8,duration='PT1M',warmup='PT15S',rampUp='PT5S')
  scenario=raw['scenarios']['q6_store_category_mix'];assert scenario['errors']==0 and scenario['count']>0
  run=next(r for r in provenance['runs'] if r['name']=='ramsql-5m-8-q6_store_category_mix')
  seconds=(raw['measuredEnd']-raw['measuredStart'])/1000
  runs.append(dict(label=label,binary_sha256=provenance['binary_sha256'],qps=scenario['count']/seconds,count=scenario['count'],measured_seconds=seconds,errors=scenario['errors'],average_server_cores_including_warmup=run['cpu_seconds_including_warmup']/run['wall_seconds']))
 assert len({r['binary_sha256'] for r in runs})==1
 qps=[r['qps'] for r in runs]
 report['groups'][group]=dict(median_qps=statistics.median(qps),min_qps=min(qps),max_qps=max(qps),runs=runs)
report['median_ratio']=report['groups']['after']['median_qps']/report['groups']['before']['median_qps']
(dest/'isolated/commit-scope-repeats.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
