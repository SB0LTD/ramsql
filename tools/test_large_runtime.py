"""Optional real bulk-load/memory qualification; not a throughput benchmark.

Reserves an explicit large first-table bank and one row for each other bank.
Use a host with enough available commit/RAM for --capabilities backing costs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from benchmark import process_memory
from test_server import Server

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.sig-cache/client-deps'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'sig-out/bin'/('ramsql.exe' if os.name=='nt' else 'ramsql'))
    parser.add_argument('--rows',type=int,default=1000000)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    import psycopg
    binary=args.binary.resolve(strict=True)
    assert 10000<=args.rows<=100000000
    profile=json.loads(subprocess.check_output([str(binary),'--capabilities']))
    banks=[args.rows]+[1]*(profile['tables']-1)
    options=('--table-rows',','.join(map(str,banks)),'--work-rows','8192','--result-rows','1024')
    capabilities=json.loads(subprocess.check_output([str(binary),'--capabilities',*options,'--max-clients','4']))
    report=dict(suite='large-runtime',passed=False,rows=args.rows,capabilities=capabilities,
        binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),durability='memory',
        scope='Actual COPY load and independent checks; no performance comparison or durable-load claim')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    # Refuse to overwrite earlier qualification evidence.
    with args.report.open('x',encoding='utf-8') as output:json.dump(report,output,indent=2)
    began=time.monotonic()
    try:
        with Server(binary,max_clients=4,extra_arguments=options) as server:
            report['memory_before_load']=process_memory(server.process)
            with psycopg.connect(host='127.0.0.1',port=server.port,user='ramsql',dbname='ramsql',
                    sslmode='disable',autocommit=True,connect_timeout=5,prepare_threshold=None) as db:
                db.execute('CREATE TABLE large_data(id INT PRIMARY KEY,value INT)',prepare=False)
                for start in range(0,args.rows,1000):
                    end=min(start+1000,args.rows)
                    data=''.join(f'{i}\t{i%10007}\n' for i in range(start,end)).encode('ascii')
                    with db.cursor() as cursor:
                        with cursor.copy('COPY large_data FROM STDIN') as copy:copy.write(data)
                    if end%100000==0:print(f'checked bulk input: {end} rows',flush=True)
                cycles,remainder=divmod(args.rows,10007)
                expected_sum=cycles*10007*10006//2+remainder*(remainder-1)//2
                assert db.execute('SELECT COUNT(*),SUM(value),MIN(id),MAX(id) FROM large_data',prepare=False).fetchone()==(args.rows,expected_sum,0,args.rows-1)
                for probe in range(1024):
                    key=(probe*48271)%args.rows
                    assert db.execute(f'SELECT id,value FROM large_data WHERE id={key}',prepare=False).fetchone()==(key,key%10007)
                for key in (0,args.rows//2,args.rows-1):
                    try:
                        db.execute(f'INSERT INTO large_data VALUES({key},0)',prepare=False)
                        raise AssertionError('large runtime index admitted duplicate')
                    except psycopg.Error as error:assert error.sqlstate=='23505',error
                assert db.execute('SELECT COUNT(*) FROM large_data',prepare=False).fetchone()==(args.rows,)
            report['memory_after_checks']=process_memory(server.process)
            assert server.process.poll() is None
        report.update(passed=True,checked_primary_keys=1024,checked_duplicate_keys=3,
            load_seconds=round(time.monotonic()-began,3))
    except BaseException as error:
        report['error']=type(error).__name__+': '+str(error)
        raise
    finally:args.report.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':main()
