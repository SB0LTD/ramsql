"""Duration-calibrated RAMSQL/PostgreSQL loopback comparison with checked rows."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import tempfile
import time

from benchmark import cpu_name
from benchmark_review import CASES, wire_measure
from test_postgres_compat import native_postgres, running_ramsql


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ramsql',type=Path,required=True)
    parser.add_argument('--postgres-bin',type=Path,required=True)
    parser.add_argument('--rows',type=int,default=1000000)
    parser.add_argument('--cycles',type=int,default=2)
    parser.add_argument('--minimum-ms',type=int,default=150)
    parser.add_argument('--permuted',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.rows<64 or args.cycles<2 or args.minimum_ms<50:
        parser.error('require >=64 rows, >=2 cycles and >=50ms trials')
    import psycopg
    binary=args.ramsql.resolve(strict=True)
    folder=args.output.resolve();folder.mkdir(parents=True,exist_ok=False)
    report={'rows':args.rows,'minimum_trial_ms':args.minimum_ms,'layout':'permuted' if args.permuted else 'ordered',
            'started_unix':time.time(),'cpu':cpu_name(),'os':platform.platform(),
            'psycopg':psycopg.__version__,'binary':str(binary),
            'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'scope':'Loopback simple-query protocol, fetch and independent digest included; recorded insertion layout; one client; RAMSQL volatile memory versus analyzed PostgreSQL unlogged table with synchronous_commit off; calibration excluded; alternating order.',
            'runs':[],'summary':{}}
    def save(): (folder/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    save()
    clusters=Path(tempfile.gettempdir())/'ramsql-profile-comparisons'/folder.name
    for cycle in range(args.cycles):
        order=['ramsql','postgres']
        if cycle%2: order.reverse()
        for engine in order:
            if engine=='ramsql':
                with running_ramsql(binary,extra_arguments=('--rows',str(args.rows))) as connection:
                    measured=wire_measure(psycopg,connection,args.rows,1,engine,minimum_ms=args.minimum_ms,permuted=args.permuted)
            else:
                with native_postgres('unused',psycopg,binary_directory=args.postgres_bin,
                                     work_directory=clusters/f'cycle-{cycle}') as (connection,metadata):
                    measured=wire_measure(psycopg,connection,args.rows,1,engine,unlogged=True,minimum_ms=args.minimum_ms,permuted=args.permuted)
                    measured['reference']=metadata
            measured['cycle']=cycle;report['runs'].append(measured);save()
            assert {case['name'] for case in measured['measurements']}==set(CASES)
            assert all(case['passed'] for case in measured['measurements']), 'unsupported/incorrect workload; see retained results'
            print(engine,cycle,'all cases checked',flush=True)
    for case in CASES:
        report['summary'][case]={}
        for engine in ('ramsql','postgres'):
            samples=[sample for run in report['runs'] if run['engine']==engine
                     for measurement in run['measurements'] if measurement['name']==case
                     for sample in measurement['samples']]
            rates=[s['operations']*1e9/s['elapsed_ns'] for s in samples]
            report['summary'][case][engine]={'median_ops_s':statistics.median(rates),
                'minimum_ops_s':min(rates),'maximum_ops_s':max(rates),
                'trials':len(samples),'minimum_trial_ms':min(s['elapsed_ns'] for s in samples)/1e6}
    save();print(json.dumps(report['summary']),flush=True)
    return 0


if __name__=='__main__': raise SystemExit(main())
