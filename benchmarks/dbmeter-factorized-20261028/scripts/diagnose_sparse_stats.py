"""Original full-data JDBC correctness checks with kernel counters; no timings claimed."""
import hashlib,json,os,pathlib,subprocess,sys,time
sys.path.insert(0,'tools')
from test_server import Server
mode=sys.argv[1];assert mode in ('on','off')
root=pathlib.Path.cwd();reviewer=(root/'../dbMeter').resolve()
base=root/f'.sig-cache/sparse-stats-{mode}-diagnostic';base.mkdir(exist_ok=False)
binary=root/f'.sig-cache/dbmeter-sparse-stats-{mode}-fast/bin/ramsql.exe'
result=dict(diagnostic_only=True,throughput_qualified=False,mode=mode,
            binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),checks=[])
args=['--workers','8','--table-rows','5,200,20,1000,50000,5000000,1,1',
      '--table-versions','10,400,40,2000,100000,5065536,2,2','--work-rows','65536','--result-rows','1024']
with Server(binary,max_clients=12,idle_ms=300000,extra_arguments=args) as server:
    with (base/'load.log').open('w') as log:
        subprocess.run([str(reviewer/'tools/postgres18/pgsql/bin/psql.exe'),'-h','127.0.0.1','-p',str(server.port),'-U','postgres','-d','ramsql','-q','-v','ON_ERROR_STOP=1','-f',str(root/'.sig-cache/dbmeter-full/ramsql_load.sql')],stdout=log,stderr=subprocess.STDOUT,check=True,env=dict(os.environ,PGSSLMODE='disable'))
    for case in ('q5_high_value_customers','q6_store_category_mix'):
        server.errors.clear()
        command=['java','-Xms32m','-Xmx256m','-jar',str(reviewer/'build/dbmeter.jar'),'check','--skip-setup','-c',str(reviewer/'examples/sales-agg/ramsql.properties'),'--set',f'jdbc.url=jdbc:postgresql://127.0.0.1:{server.port}/ramsql','--only',case]
        with (base/(case+'.log')).open('w') as log:subprocess.run(command,cwd=reviewer,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=180)
        # The child is idle after its client exits. This delay only drains stderr;
        # no benchmark measurement or throughput comparison takes place here.
        time.sleep(.1)
        lines=[line for line in server.errors if line.startswith('DIAGNOSTIC ')]
        assert lines, 'The original query produced no factor-path counter record'
        result['checks'].append(dict(case=case,passed=True,records=lines))
        print(case,json.dumps(lines),flush=True)
(base/'counters.json').write_text(json.dumps(result,indent=2)+'\n')
