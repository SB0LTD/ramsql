"""Run the reviewer's dbMeter SQL, checks, workload weights and JDBC drivers.

Build dbMeter and install its configured driver before running. PostgreSQL must
be an isolated test cluster: its original setup drops the dbmeter_size schema.
Never point this tool at an existing application database.
"""
import argparse
from contextlib import nullcontext, contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time

from benchmark import cpu_name, process_memory
from benchmark_pk_scaling import cpu_times
from test_server import Server, PG


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dbmeter', type=Path, required=True)
    p.add_argument('--binary', type=Path)
    p.add_argument('--postgres-url')
    p.add_argument('--irdb-url')
    p.add_argument('--ramsql-url',help='reuse an already loaded RAMSQL server')
    p.add_argument('--suite',choices=['size-scaling','sales-subset','point'],default='size-scaling')
    p.add_argument('--config',type=Path,help='explicit dialect overlay; original tracked inputs must still match HEAD')
    p.add_argument('--scenarios',nargs='+',default=['mixed'])
    p.add_argument('--postgres-user', default='sb')
    p.add_argument('--sizes', type=int, nargs='+', default=[512])
    p.add_argument('--threads', type=int, nargs='+', default=[1, 8])
    p.add_argument('--workers', type=int, default=4)
    p.add_argument('--work-rows', type=int)
    p.add_argument('--kinds', nargs='+', choices=['pk', 'filter', 'topn', 'fetch_all', 'mixed'], default=['pk', 'filter', 'topn', 'mixed'])
    p.add_argument('--duration', default='5s')
    p.add_argument('--warmup', default='2s')
    p.add_argument('--java-max-heap', default='256m')
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if sum(map(bool,[a.binary,a.postgres_url,a.irdb_url,a.ramsql_url]))!=1:
        p.error('supply exactly one binary or engine URL')
    if a.binary and a.suite=='sales-subset':p.error('prepare the sales dataset separately and use --ramsql-url')
    root = a.dbmeter.resolve(strict=True)
    # Generators may change Windows line endings. Reject actual source/config
    # changes so the report cannot silently call a modified workload original.
    tracked=subprocess.check_output(['git','-C',str(root),'ls-files'],text=True).splitlines()
    changed=[];source_hashes={}
    for name in tracked:
        actual=(root/name).read_bytes().replace(b'\r\n',b'\n')
        expected=subprocess.check_output(['git','-C',str(root),'show','HEAD:'+name]).replace(b'\r\n',b'\n')
        if actual!=expected:changed.append(name)
        source_hashes[name]=hashlib.sha256(actual).hexdigest()
    if changed:p.error('dbMeter tracked inputs differ from HEAD: '+', '.join(changed))
    jar = root/'build/dbmeter.jar'
    driver = root/'drivers/postgresql-42.7.4.jar'
    output = a.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    engine = 'ramsql' if a.binary or a.ramsql_url else 'irdb' if a.irdb_url else 'postgres'
    suite_folder={'size-scaling':'size-scaling','sales-subset':'sales-agg-subset','point':'ramsql'}[a.suite]
    config = a.config.resolve(strict=True) if a.config else root/'examples'/suite_folder/('ramsql.properties' if engine=='ramsql' or a.suite=='point' else 'irdb.properties' if engine=='irdb' else 'pg.properties')
    if not config.exists():p.error('this suite requires an explicit --config dialect overlay')
    kinds=a.kinds if a.suite=='size-scaling' else a.scenarios
    sizes=a.sizes if a.suite=='size-scaling' else [1000000 if a.suite=='sales-subset' else 500]
    artifacts=[jar,driver,config]
    if a.irdb_url:artifacts += [root/'build/irdb-jdbc.jar',root/'drivers/jirdb-2025-02-02.jar',root/'drivers/fastutil-8.5.15.jar']
    report = dict(schema=1, engine=engine, started_unix=time.time(),
        platform=platform.platform(), cpu=cpu_name(), logical_cpus=os.cpu_count(),
        dbmeter_revision=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'], text=True).strip(),
        dbmeter_dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain'],text=True).strip(),
        files={str(f.relative_to(root)) if f.is_relative_to(root) else str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in artifacts},
        source_normalized_sha256=source_hashes,normalized_source_matches_head=True,
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        sql_changes=bool(a.config),suite=a.suite,threads=a.threads,kinds=kinds,sizes=sizes,
        duration=a.duration, warmup=a.warmup, java_max_heap=a.java_max_heap,
        workers=a.workers if a.binary else None,
        runs=[], commands=[])
    if a.binary:
        a.binary = a.binary.resolve(strict=True)
        report['binary'] = dict(path=str(a.binary), sha256=hashlib.sha256(a.binary.read_bytes()).hexdigest())
        caps=json.loads(subprocess.check_output([str(a.binary),'--capabilities']))
        report['capabilities']=caps
        if len(a.sizes)>caps['tables']:p.error('one table bank required per size')
        banks=a.sizes+[1]*(caps['tables']-len(a.sizes))
        result_rows=max(a.sizes) if any(k in a.kinds for k in ('fetch_all','mixed')) else min(max(a.sizes),max(1024,max(a.sizes)//500))
        args=['--workers',str(a.workers),'--table-rows',','.join(map(str,banks)),
              '--table-versions',','.join(str(max(2,n*2)) for n in banks),
              '--work-rows',str(a.work_rows or max(a.sizes)), '--result-rows',str(result_rows)]
        report['server_arguments']=args
        manager=Server(a.binary, idle_ms=300000, max_clients=max(a.threads)+2,extra_arguments=args)
    else:
        manager=nullcontext(None)
    def save():
        (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    def invoke(name, command):
        report['commands'].append(dict(name=name, argv=command));save()
        with (output/(name+'.log')).open('w') as log:
            result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT,timeout=600)
        print(name,result.returncode,flush=True)
        return result.returncode
    @contextmanager
    def recorded_manager():
        save()
        try:
            with manager as server:yield server
        except BaseException as error:
            report['error']=type(error).__name__+': '+str(error)
            report['finished_unix']=time.time();save();raise
    with recorded_manager() as server:
        url=f'jdbc:postgresql://127.0.0.1:{server.port}/ramsql' if server else a.postgres_url or a.irdb_url or a.ramsql_url
        common=['-c',str(config),'--set','jdbc.url='+url,
                '--set','run.databaseLabel='+engine]
        if not a.irdb_url:common+=['--set','jdbc.user='+('postgres' if engine=='ramsql' else a.postgres_user),'--set','jdbc.password=']
        if a.suite=='point':
            common+=['--set','check.z_gap_aggregates.enabled=false']
            report['obsolete_absence_probe_disabled']='alpha.2 expected COUNT(*) to fail; actual COUNT is verified separately'
        def command(verb,extra):return ['java','-Xms32m','-Xmx'+a.java_max_heap,'-jar',str(jar),verb,*common,*extra]
        checks=','.join(f's{n}_{kind}' for n in a.sizes for kind in ['a_pk','b_filter','c_topn','d_last'])
        setup_ok=True
        if server and a.suite=='size-scaling':
            for n in a.sizes:
                setup_ok &= invoke(f'load-{n}',command('check',['--only',f's{n}_d_last',
                    '--set',f'check.setup.file=../../build/size-scaling/ramsql_load_t_{n}.sql',
                    '--set','check.setup.continueOnError=true']))==0
        report['checks_passed']=invoke('checks',command('check',(['--only',checks] if a.suite=='size-scaling' else [])+
            ['--junit',str(output/'checks.xml')]))==0 and setup_ok
        if not report['checks_passed']:
            report['finished_unix']=time.time();save();return 1
        if server:
            pg=PG(server.raw());pg.startup()
            try:
                if a.suite=='size-scaling':
                    for n in a.sizes:
                        pg.ok(f'SELECT COUNT(*),SUM(val) FROM t_{n}',[[str(n),str(sum((i*7919)%1000003 for i in range(1,n+1)))]] )
                else:pg.ok('SELECT COUNT(*) FROM items',[['500']])
            finally:pg.close()
        for n in sizes:
            for threads in a.threads:
                for kind in kinds:
                    name=f'{engine}-{n}-{threads}-{kind}'
                    if a.suite=='size-scaling':only=','.join(f's{n}_{k}' for k in ['pk','filter','topn','fetch_all']) if kind=='mixed' else f's{n}_{kind}'
                    elif a.suite=='sales-subset':only=','.join(['s1_region','s2_category','s3_top_products','s4_segment_discount','s5_high_value_customers','s6_store_category_mix']) if kind=='mixed' else kind
                    else:only='pk_lookup,filtered_scan,update_qty' if kind=='mixed' else kind
                    before=cpu_times(server.process) if server else None
                    began=time.time()
                    code=invoke(name,command('load',['--only',only,'--set',f'load.threads={threads}',
                        '--set','load.warmup='+a.warmup,'--set','load.duration='+a.duration,
                        '--run-id',name,'--node','local','--out',str(output/name)]))
                    entry=dict(name=name,size=n,threads=threads,kind=kind,returncode=code,wall_seconds=time.time()-began)
                    data_path=output/name/'local.json'
                    entry['passed']=False
                    if data_path.exists():
                        data=json.loads(data_path.read_text())
                        entry['passed']=code==0 and bool(data['scenarios']) and all(s['errors']==0 and s['count']>0 for s in data['scenarios'].values())
                    if server:
                        after=cpu_times(server.process)
                        entry['server_cpu_seconds_including_warmup']=sum(after[k]-before[k] for k in before)
                        entry['server_memory']=process_memory(server.process)
                    report['runs'].append(entry);save()
        report['finished_unix']=time.time();save()
    return 0 if report['checks_passed'] and all(r['passed'] for r in report['runs']) else 1


if __name__=='__main__':raise SystemExit(main())
