import pathlib,subprocess,sys,json,time,hashlib,os
sys.path.insert(0,'tools')
from test_server import Server
from benchmark import process_memory
from benchmark_pk_scaling import cpu_times
args=['--workers','4','--table-rows','5,200,20,1000,50000,1000000,1,1',
 '--table-versions','10,400,40,2000,100000,2000000,2,2','--work-rows','65536','--result-rows','1024']
psql=pathlib.Path('../dbMeter/tools/postgres18/pgsql/bin/psql.exe').resolve()
for trial,order in enumerate([['before','after'],['after','before'],['before','after']],1):
 for variant in order:
  folder=pathlib.Path(f'benchmarks/dbmeter-directory-million-{trial}-{variant}-20261028')
  binary=pathlib.Path('.sig-cache',{'before':'dbmeter-comment-fast','after':'dbmeter-directory-fast'}[variant],'bin/ramsql.exe').resolve()
  facts=dict(binary=str(binary),sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),arguments=args)
  cache=pathlib.Path(f'.sig-cache/directory-million-{trial}-{variant}');cache.mkdir(exist_ok=True)
  with Server(binary,max_clients=12,idle_ms=300000,extra_arguments=args) as server:
   facts.update(port=server.port,pid=server.process.pid)
   with (cache/'load.log').open('w') as log:
    subprocess.run([str(psql),'-h','127.0.0.1','-p',str(server.port),'-U','postgres','-d','ramsql','-q','-f',str(pathlib.Path('../dbMeter/build/sales-agg-subset/ramsql_load.sql').resolve())],stdout=log,stderr=subprocess.STDOUT,check=True,env=dict(os.environ,PGSSLMODE='disable'))
   before=cpu_times(server.process);began=time.monotonic()
   command=[sys.executable,'tools/benchmark_dbmeter.py','--dbmeter','../dbMeter',
    '--ramsql-url',f'jdbc:postgresql://127.0.0.1:{server.port}/ramsql','--suite','sales-subset',
    '--threads','8','--duration','30s','--warmup','5s','--output',str(folder)]
   with (cache/'campaign.log').open('w') as log:
    code=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT).returncode
   after=cpu_times(server.process);wall=time.monotonic()-began
   facts.update(returncode=code,command=command,server_cpu_seconds_including_checks_and_warmup=sum(after[k]-before[k] for k in before),wall_seconds=wall,memory=process_memory(server.process))
   (folder/'server.json').write_text(json.dumps(facts,indent=2))
   print(trial,variant,code,flush=True)
   if code:raise SystemExit(code)
