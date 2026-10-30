import json,pathlib,statistics,subprocess,sys,time
root=pathlib.Path.cwd();base=root/'.sig-cache/dbmeter-factor-paired';base.mkdir(exist_ok=False)
report=dict(schema=1,method='Three paired original-duration full5M campaigns, AB/BA/AB order. Same immutable SQL source; only factorized_aggregates toggles. Fresh server/data load and JVM per case; default text128, eight workers/clients. No builds, profilers or reference database services concurrently.',runs=[])
def save(): (base/'pairs.json').write_text(json.dumps(report,indent=2)+'\n')
try:
 for pair,order in enumerate([('on','off'),('off','on'),('on','off')],1):
  for mode in order:
   name=f'{mode}-pair-{pair}';dest=base/name
   binary=root/f'.sig-cache/dbmeter-factor-{mode}-fast/bin/ramsql.exe'
   command=[sys.executable,'tools/benchmark_dbmeter_full_ramsql.py','--binary',str(binary),'--out',str(dest),'--trials','1']
   print('START',name,flush=True);began=time.monotonic()
   with (base/(name+'.log')).open('w') as log:subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=1200)
   result=json.loads((dest/'ramsql-full-results.json').read_text());assert result['supported_checks_passed'] and 'after_queries_memory' in result
   rates={}
   for path in dest.glob('*/local.json'):
    data=json.loads(path.read_text())
    for scenario,metrics in data['scenarios'].items():
     assert metrics['count']>0 and metrics['errors']==0
     rates[scenario]=metrics['count']*1000/(data['measuredEnd']-data['measuredStart'])
   assert len(rates)==2
   report['runs'].append(dict(pair=pair,mode=mode,directory=name,binary_sha256=result['binary_sha256'],qps=rates,wall_seconds=time.monotonic()-began));save();print('DONE',name,json.dumps(rates),flush=True)
 report['summary']={mode:{case:dict(trials=(values:=[run['qps'][case] for run in report['runs'] if run['mode']==mode]),median=statistics.median(values),minimum=min(values),maximum=max(values)) for case in report['runs'][0]['qps']} for mode in ('on','off')};save()
except BaseException as error:
 report['error']=type(error).__name__+': '+str(error);save();raise
