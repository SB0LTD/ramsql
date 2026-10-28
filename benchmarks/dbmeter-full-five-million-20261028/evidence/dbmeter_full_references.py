import json,os,pathlib,subprocess,time
dbmeter=pathlib.Path('../dbMeter').resolve();ledger=[]
for engine in ['postgres','irdb']:
 base=pathlib.Path('.sig-cache/dbmeter-full-'+engine).resolve();base.mkdir(exist_ok=True)
 url='jdbc:postgresql://127.0.0.1:55439/postgres' if engine=='postgres' else 'jdbc:irdb://127.0.0.1:55660/full_bench_20261028'
 common=['--skip-setup','-c',str(dbmeter/'examples/sales-agg'/(engine+'.properties')),'--set','jdbc.url='+url,'--set','run.databaseLabel='+engine+' full5M']
 if engine=='postgres':common+=['--set','jdbc.user=dbmeter','--set','jdbc.password=']
 env=dict(os.environ)
 if engine=='irdb':
  # Use the reviewer's original configured defaults, matching CopyToIrdb;
  # never serialize credentials into the command/provenance report.
  env.pop('IRDB_USER',None);env.pop('IRDB_PASSWORD',None)
 java=['java','-Xms32m','-Xmx256m','-jar',str(dbmeter/'build/dbmeter.jar')]
 report=dict(schema=1,method='Original full 5M data, queries and expected CSV. Record all-suite errors; score only isolated q5/q6 after their original CSV checks pass. 5s ramp, 5s warmup, 15s measurement; fresh JVM per case.',engine=engine,runs=[])
 def save(): (base/'results.json').write_text(json.dumps(report,indent=2)+'\n')
 def invoke(name,tail):
  command=java+tail
  with (base/(name+'.log')).open('w') as log:code=subprocess.run(command,cwd=dbmeter,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
  report['runs'].append(dict(name=name,argv=command,returncode=code));save();print(engine,name,code,flush=True);return code
 invoke('all-checks',['check',*common,'--junit',str(base/'all-checks.xml')])
 if invoke('supported-checks',['check',*common,'--only','a_totals,q5_high_value_customers,q6_store_category_mix','--junit',str(base/'supported-checks.xml')]):raise RuntimeError('Relevant checks failed; no throughput scoring')
 for clients in [1,8]:
  for query in ['q5_high_value_customers','q6_store_category_mix']:
   name=f'{engine}-5m-{clients}-{query}'
   if invoke(name,['load',*common,'--only',query,'--set',f'load.threads={clients}','--set','load.warmup=5s','--set','load.duration=15s','--node','local','--run-id',name,'--out',str(base/name)]):raise RuntimeError('Reference load failed')
   data=json.loads((base/name/'local.json').read_text())
   if not data['scenarios'] or any(s['errors'] or s['count']==0 for s in data['scenarios'].values()):raise RuntimeError('Reference measured errors')
