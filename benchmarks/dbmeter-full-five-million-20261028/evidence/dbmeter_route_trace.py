import hashlib,json,os,pathlib,subprocess,sys,time
from collections import deque,Counter
sys.path.insert(0,'tools')
from test_server import Server,PG
from benchmark import process_memory
from benchmark_pk_scaling import cpu_times
base=pathlib.Path('.sig-cache/dbmeter-route-trace-workers8').resolve();base.mkdir(exist_ok=True)
binary=pathlib.Path('.sig-cache/dbmeter-routing-diagnostic/bin/ramsql.exe').resolve()
args=['--workers','8','--table-rows','5,200,20,1000,50000,5000000,1,1',
 '--table-versions','10,400,40,2000,100000,5065536,2,2','--work-rows','65536','--result-rows','1024']
psql=pathlib.Path('../dbMeter/tools/postgres18/pgsql/bin/psql.exe').resolve()
dbmeter=pathlib.Path('../dbMeter').resolve()
report=dict(diagnostic_only=True,schema=1,method='Actual 5M data exported from original PostgreSQL schema after 7/7 CSV checks. No alpha.2 sample schema. Original full query files and expected CSVs; all-suite failures retained. Only successful original checks may be timed.',binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),server_arguments=args,dbmeter_revision=subprocess.check_output(['git','-C',str(dbmeter),'rev-parse','HEAD'],text=True).strip(),runs=[])
def save(): (base/'ramsql-full-results.json').write_text(json.dumps(report,indent=2)+'\n')
save()
try:
 with Server(binary,max_clients=12,idle_ms=300000,extra_arguments=args) as server:
  server.errors=deque(maxlen=100000)
  report.update(pid=server.process.pid,port=server.port);save()
  with (base/'ramsql-load.log').open('w') as log:
   subprocess.run([str(psql),'-h','127.0.0.1','-p',str(server.port),'-U','postgres','-d','ramsql','-q','-v','ON_ERROR_STOP=1','-f',str(pathlib.Path('.sig-cache/dbmeter-full/ramsql_load.sql').resolve())],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=600,env=dict(os.environ,PGSSLMODE='disable'))
  report['machine_after_load']=json.loads(subprocess.check_output([str(binary),'--machine-capacity']));report['loaded']=True;report['after_load_memory']=process_memory(server.process);save();print('loaded actual 5000000 facts',flush=True)
  common=['--skip-setup','-c',str(dbmeter/'examples/sales-agg/ramsql.properties'),'--set',f'jdbc.url=jdbc:postgresql://127.0.0.1:{server.port}/ramsql','--set','run.databaseLabel=RAMSQL full5M']
  java=['java','-Xms32m','-Xmx256m','-jar',str(dbmeter/'build/dbmeter.jar')]
  def invoke(name,tail):
   command=java+tail
   clocks_before=cpu_times(server.process);began=time.monotonic()
   with (base/(name+'.log')).open('w') as log:code=subprocess.run(command,cwd=dbmeter,stdout=log,stderr=subprocess.STDOUT,timeout=600).returncode
   report['runs'].append(dict(name=name,argv=command,returncode=code,wall_seconds=time.monotonic()-began,cpu_seconds=sum(cpu_times(server.process)[k]-clocks_before[k] for k in clocks_before)));save();print(name,code,flush=True);return code
  invoke('ramsql-all-checks',['check',*common,'--junit',str(base/'ramsql-all-checks.xml')])
  code=invoke('ramsql-supported-checks',['check',*common,'--only','a_totals,q5_high_value_customers,q6_store_category_mix','--junit',str(base/'ramsql-supported-checks.xml')])
  report['supported_checks_passed']=code==0;save()
  if code:raise RuntimeError('Full-data supported checks failed; do not score throughput')
  server.errors.clear()
  for clients in [8]:
   for query in ['q6_store_category_mix']:
    name=f'ramsql-5m-{clients}-{query}'
    code=invoke(name,['load',*common,'--only',query,'--set',f'load.threads={clients}','--set','load.warmup=2s','--set','load.duration=10s','--node','local','--run-id',name,'--out',str(base/name)])
    if code:raise RuntimeError('Load command failed')
    data=json.loads((base/name/'local.json').read_text())
    if not data['scenarios'] or any(s['errors'] or s['count']==0 for s in data['scenarios'].values()):raise RuntimeError('Measured SQL errors; do not score run')
  events=list(server.errors)
  (base/'route-events.log').write_text('\n'.join(events)+'\n')
  report['route_counts']=dict(Counter(events))
  report['after_queries_memory']=process_memory(server.process);save()
except BaseException as error:
 report['error']=type(error).__name__+': '+str(error);save();raise
