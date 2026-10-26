"""Checked native API comparison using identical cyclic workload traces."""
import argparse
import hashlib
import json
import math
import shutil
from pathlib import Path
import platform
import statistics
import subprocess
import time

from production_workloads import CASES, write_fixture


def validate(records, fixture, minimum_ms):
    assert len(records) == 6, 'expected metadata plus five complete trials'
    metadata, *samples = records
    assert metadata.get('metadata') is True and metadata['rows'] == fixture['rows']
    assert metadata['engine'] in ('ramsql-native','InMemory.Net.irdb')
    assert metadata['requests'] == fixture['requests']
    assert metadata['warmup_ns'] >= 1000000000 and metadata['warmup_cycles'] >= 1
    if metadata['engine'] == 'InMemory.Net.irdb':
        assert metadata['package_version'] == '1.10.821' and metadata['import_compiled'] and metadata['id_clustered_index']
    for i, sample in enumerate(samples):
        assert sample.get('passed') is True and sample['trial'] == i
        assert type(sample['cycles']) is int and sample['cycles'] >= 1
        assert sample['operations'] == sample['cycles'] * fixture['requests']
        assert math.isfinite(sample['elapsed_ns']) and sample['elapsed_ns'] >= minimum_ms*1000000
        assert 0 < sample['p50_upper_ns'] <= sample['p95_upper_ns'] <= sample['p99_upper_ns']
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', type=Path, required=True)
    parser.add_argument('--dotnet', type=Path, required=True)
    parser.add_argument('--irdb', type=Path, required=True)
    parser.add_argument('--compiler',type=Path,help='record the actual compiler version and executable hash')
    parser.add_argument('--rows', type=int, nargs='+', default=[10000, 100000, 1000000])
    parser.add_argument('--groups', type=int, default=1024)
    parser.add_argument('--payload-bytes', type=int, default=96)
    parser.add_argument('--requests', type=int, default=1024)
    parser.add_argument('--minimum-ms', type=int, default=200)
    parser.add_argument('--cycles', type=int, default=2)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--case', action='append', choices=CASES)
    parser.add_argument('--seed', type=int, default=20261026)
    parser.add_argument('--require-wins', action='store_true',help='exit 2 unless all correct cases exceed the speedup margin with non-overlapping observed ranges')
    parser.add_argument('--minimum-speedup',type=float,default=1.05)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.cycles < 2 or not 1 <= args.minimum_ms <= 10000 or args.timeout < 1 or not math.isfinite(args.minimum_speedup) or args.minimum_speedup<1:
        parser.error('require >=2 alternating cycles, 1..10000ms, positive timeout')
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    report = dict(schema=1, started_unix=time.time(), os=platform.platform(),
        cpu=platform.processor(),logical_cpus=__import__('os').cpu_count(),
        method='Native SQL public API, volatile memory, single worker; same trace distribution; result checks and histogram included. No durability or server/concurrency superiority claim.',
        latency='power-of-two nanosecond bucket upper bounds, including result consumption/validation; not exact quantiles',
        fixture_policy='shuffled keys, randomized integer values, nullable variable payloads, genuine cyclic writes; fixed 64-group RAMSQL summary budget',
        minimum_speedup=args.minimum_speedup, range_rule='all observed RAMSQL rates exceed all observed IRDB rates; this is a finite screening rule, not a statistical confidence claim',
        source_sha256={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in ['build.sig.zon','src/production_benchmark.sig','tools/production_workloads.py','tools/benchmark_production.py','tools/irdb_bench/Program.cs','tools/irdb_bench/ProductionTrace.cs']},
        binaries={name:dict(path=str(path.resolve()),sha256=hashlib.sha256(path.read_bytes()).hexdigest()) for name,path in [('native',args.native),('dotnet_host',args.dotnet),('irdb',args.irdb),('irdb_library',args.irdb.parent/'irdb.dll')]},
        runs=[], summary=[], all_correct=False, all_ramsql_medians_faster=False)
    for name in report['source_sha256']:
        target=folder/'source'/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(name,target)
    if args.compiler:
        report['compiler']=dict(path=str(args.compiler.resolve()),version=subprocess.check_output([str(args.compiler.resolve()),'version'],text=True).strip(),sha256=hashlib.sha256(args.compiler.read_bytes()).hexdigest())
    def save():
        (folder/'results.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    save()
    for n in args.rows:
        for case in args.case or CASES:
            trace=folder/f'{n}-{case}.trace'
            fixture=write_fixture(trace,n=n,groups=min(n,args.groups),width=args.payload_bytes,
                                  seed=args.seed,case=case,count=args.requests)
            for cycle in range(args.cycles):
                order=['ramsql','irdb'] if cycle%2==0 else ['irdb','ramsql']
                for engine in order:
                    command=([str(args.native.resolve())] if engine=='ramsql' else
                             [str(args.dotnet.resolve()),str(args.irdb.resolve()),'--trace'])
                    command += [str(trace),str(args.minimum_ms)]
                    log=folder/f'{n}-{case}-{cycle}-{engine}.log'
                    run=dict(engine=engine,cycle=cycle,fixture=fixture,command=command,passed=False)
                    try:
                        with log.open('w') as output:
                            result=subprocess.run(command,stdout=output,stderr=subprocess.STDOUT,
                                                  text=True,timeout=args.timeout)
                        records=[json.loads(line) for line in log.read_text().splitlines() if line.startswith('{')]
                        run['returncode']=result.returncode
                        run['records']=records
                        assert result.returncode==0, 'process failed; retained log'
                        run['samples']=validate(records,fixture,args.minimum_ms)
                        run['passed']=True
                    except Exception as error:
                        run['error']=type(error).__name__+': '+str(error)
                    report['runs'].append(run);save()
                    print(n,case,cycle,engine,'PASS' if run['passed'] else run['error'],flush=True)
            summary=dict(rows=n,case=case,engines={})
            for engine in ('ramsql','irdb'):
                runs=[r for r in report['runs'] if r['engine']==engine and r['fixture']['rows']==n and r['fixture']['case']==case]
                if len(runs)==args.cycles and all(r['passed'] for r in runs):
                    samples=[s for r in runs for s in r['samples']]
                    rates=[s['operations']*1e9/s['elapsed_ns'] for s in samples]
                    summary['engines'][engine]=dict(median_ops_s=statistics.median(rates),minimum_ops_s=min(rates),maximum_ops_s=max(rates),
                        median_p99_upper_ns=statistics.median(s['p99_upper_ns'] for s in samples),trials=len(samples))
            summary['ramsql_median_faster']=(len(summary['engines'])==2 and summary['engines']['ramsql']['median_ops_s']>summary['engines']['irdb']['median_ops_s'])
            summary['win_gate_passed']=False
            if len(summary['engines'])==2:
                a,b=summary['engines']['ramsql'],summary['engines']['irdb']
                summary['win_gate_passed']=a['median_ops_s']>=b['median_ops_s']*args.minimum_speedup and a['minimum_ops_s']>b['maximum_ops_s']
            report['summary'].append(summary);save()
    report['all_correct']=all(r['passed'] for r in report['runs'])
    report['all_ramsql_medians_faster']=report['all_correct'] and all(s['ramsql_median_faster'] for s in report['summary'])
    report['performance_gate_passed']=report['all_correct'] and all(s['win_gate_passed'] for s in report['summary'])
    report['finished_unix']=time.time();save()
    return 1 if not report['all_correct'] else 2 if args.require_wins and not report['performance_gate_passed'] else 0


if __name__=='__main__':
    raise SystemExit(main())
