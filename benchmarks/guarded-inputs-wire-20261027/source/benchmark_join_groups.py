"""Checked, matched wire comparison of old/new unique-key joined aggregation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import time
from benchmark import cpu_name,process_memory
from benchmark_pk_scaling import cpu_times
from test_server import Server
from test_streamed_joins import QUERY,arguments,model,seed


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,action='append',required=True)
    parser.add_argument('--rows',type=int,default=100000)
    parser.add_argument('--work-rows',type=int,default=32768)
    parser.add_argument('--iterations',type=int,default=100)
    parser.add_argument('--trials',type=int,default=3)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.rows<512 or args.iterations<10 or args.trials<2:parser.error('rows >=512, iterations >=10 and trials >=2 required')
    binaries=[p.resolve(strict=True) for p in args.binary]
    args.output.mkdir(parents=True,exist_ok=False)
    source=args.output/'source';source.mkdir()
    for name in ('benchmark_join_groups.py','benchmark_pk_scaling.py','benchmark.py','test_streamed_joins.py','test_server.py'):
        shutil.copy2(Path(__file__).with_name(name),source/name)
    report=dict(schema=1,scope='single-client memory-only PostgreSQL simple-query joined aggregate comparison',
                query=QUERY,rows=args.rows,work_rows=args.work_rows,iterations=args.iterations,
                environment=dict(cpu=cpu_name(),logical_cpus=os.cpu_count(),platform=platform.platform(),python=platform.python_version()),
                binaries=[dict(path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in binaries],trials=[])
    expected=model(args.rows)
    def save():(args.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    for trial in range(args.trials):
        for binary in binaries[::(-1 if trial%2 else 1)]:
            caps=json.loads(subprocess.check_output([str(binary),'--capabilities']))
            entry=dict(binary=str(binary),trial=trial,passed=False)
            try:
                with Server(binary,max_clients=1,extra_arguments=arguments(caps,args.rows,args.work_rows,1)) as server:
                    pg=seed(server,args.rows,caps)
                    try:
                        for _ in range(3):pg.ok(QUERY,expected)
                        before=cpu_times(server.process);memory_before=process_memory(server.process)
                        began=time.perf_counter_ns()
                        for _ in range(args.iterations):pg.ok(QUERY,expected)
                        elapsed=time.perf_counter_ns()-began
                        after=cpu_times(server.process)
                        entry.update(passed=True,operations=args.iterations,elapsed_ns=elapsed,
                                     server_cpu_seconds=sum(after[k]-before[k] for k in before),
                                     memory_before=memory_before,memory_after=process_memory(server.process))
                    finally:pg.close()
            except Exception as error:entry['error']=type(error).__name__+': '+str(error)
            report['trials'].append(entry);save()
            print(binary.parent.parent.name,trial,entry['passed'],round(entry.get('elapsed_ns',0)/1e6,2),entry.get('error',''),flush=True)
    return 0 if all(x['passed'] for x in report['trials']) else 1


if __name__=='__main__':raise SystemExit(main())
