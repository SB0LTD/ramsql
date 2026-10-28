import json,os,pathlib,socket,subprocess,sys,time
root=pathlib.Path.cwd(); dbmeter=(root/'../dbMeter').resolve()
engine=sys.argv[1]
assert engine in ('postgres','irdb')
base=root/('.sig-cache/dbmeter-isolated-full-'+engine);base.mkdir(exist_ok=False)
port=55439 if engine=='postgres' else 55660
def listening(port):
 with socket.socket() as s:
  s.settimeout(.2);return s.connect_ex(('127.0.0.1',port))==0
assert not any(listening(p) for p in (55439,55660)), 'Reference engines must be stopped before isolation'
url='jdbc:postgresql://127.0.0.1:55439/postgres' if engine=='postgres' else 'jdbc:irdb://127.0.0.1:55660/full_bench_20261028'
common=['--skip-setup','-c',str(dbmeter/'examples/sales-agg'/(engine+'.properties')),'--set','jdbc.url='+url,'--set','run.databaseLabel='+engine+' isolated full5M']
if engine=='postgres':common+=['--set','jdbc.user=dbmeter','--set','jdbc.password=']
env=dict(os.environ)
if engine=='irdb':
 env.pop('IRDB_USER',None);env.pop('IRDB_PASSWORD',None)
java=['java','-Xms32m','-Xmx256m','-jar',str(dbmeter/'build/dbmeter.jar')]
report=dict(schema=1,method='Original full 5M data, queries and expected CSV. One database active. Fresh JVM per case; 5s ramp, 15s warmup, 60s measurement. All seven CSV checks must pass before scoring.',engine=engine,runs=[])
def save(): (base/'results.json').write_text(json.dumps(report,indent=2)+'\n')
def invoke(name,tail):
 command=java+tail;start=time.monotonic()
 with (base/(name+'.log')).open('w') as log:code=subprocess.run(command,cwd=dbmeter,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
 report['runs'].append(dict(name=name,argv=command,returncode=code,wall_seconds=time.monotonic()-start));save();print(engine,name,code,flush=True);return code
pgctl=dbmeter/'tools/postgres18/pgsql/bin/pg_ctl.exe';pgdata=dbmeter/'tools/postgres18/data-dbmeter'
child=None;started=False
try:
 if engine=='postgres':
  subprocess.run([str(pgctl),'-D',str(pgdata),'-l',str(base/'server.log'),'-o','-h 127.0.0.1 -p 55439','-w','start'],check=True);started=True
 else:
  manifest=json.loads((dbmeter/'tools/irdb/process.json').read_text())
  with (base/'server.log').open('a') as log:child=subprocess.Popen(manifest['command'],stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
  manifest['pid']=child.pid;(dbmeter/'tools/irdb/process.json').write_text(json.dumps(manifest)+'\n');started=True
  until=time.monotonic()+60
  while not listening(port):
   if child.poll() is not None or time.monotonic()>until:raise RuntimeError('IRDB failed startup')
   time.sleep(.2)
 if invoke('all-checks',['check',*common,'--junit',str(base/'all-checks.xml')]):raise RuntimeError('Full-data correctness failed; no scoring')
 for query in ['q5_high_value_customers','q6_store_category_mix']:
  name=f'{engine}-5m-8-{query}'
  if invoke(name,['load',*common,'--only',query,'--set','load.threads=8','--set','load.warmup=15s','--set','load.duration=60s','--node','local','--run-id',name,'--out',str(base/name)]):raise RuntimeError('Reference load failed')
  data=json.loads((base/name/'local.json').read_text())
  if not data['scenarios'] or any(s['errors'] or s['count']==0 for s in data['scenarios'].values()):raise RuntimeError('Measured errors')
except BaseException as error:
 report['error']=type(error).__name__+': '+str(error);save();raise
finally:
 if started and engine=='postgres':subprocess.run([str(pgctl),'-D',str(pgdata),'-m','fast','-w','stop'],check=True)
 if child is not None and child.poll() is None:child.terminate();child.wait(timeout=15)
