"""Real SQL-loaded star aggregates with a small caller-bounded workspace."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from test_server import PG, Server, expect_error

ROOT=Path(__file__).resolve().parents[1]
QUERY=('SELECT s.region,COUNT(*),SUM(f.amount*p.unit) FROM facts f '
       'JOIN shops s ON s.id=f.shop JOIN products p ON p.id=f.product '
       'GROUP BY s.region ORDER BY s.region NULLS LAST')


def arguments(caps,rows,work_rows,workers):
    assert caps['tables']>=3 and caps['columns']>=4,caps
    banks=[rows,16,8]+[1]*(caps['tables']-3)
    return ('--rows',str(rows),'--table-rows',','.join(map(str,banks)),
            '--work-rows',str(work_rows),'--result-rows','16','--workers',str(workers))


def model(rows):
    sizes=[0]*5;sums=[0]*5
    for identity in range(rows):
        if identity%23==0 or identity%17==0:continue
        group=4 if identity%16==7 else identity%4
        sizes[group]+=1
        if identity%19:sums[group]+=(identity%97-48)*(identity%8+1)
    return [[str(g) if g<4 else None,str(sizes[g]),str(sums[g])] for g in range(5)]


def seed(server,rows,caps):
    pg=PG(server.raw());pg.startup()
    try:
        pg.ok('CREATE TABLE facts(id INT PRIMARY KEY,shop INT,product INT,amount INT)')
        pg.ok('CREATE TABLE shops(id INT PRIMARY KEY,region INT)')
        pg.ok('CREATE TABLE products(id INT PRIMARY KEY,unit INT)')
        pg.ok('INSERT INTO shops VALUES '+','.join(f'({i},{"NULL" if i==7 else i%4})' for i in range(16)))
        pg.ok('INSERT INTO products VALUES '+','.join(f'({i},{i+1})' for i in range(8)))
        batch=min(128,max(1,(caps['expr_nodes']-32)//6))
        for start in range(0,rows,batch):
            tuples=[]
            for i in range(start,min(rows,start+batch)):
                tuples.append(f'({i},{999 if i%23==0 else i%16},{"NULL" if i%17==0 else i%8},{"NULL" if i%19==0 else i%97-48})')
            pg.ok('INSERT INTO facts VALUES '+','.join(tuples))
        pg.ok('SELECT COUNT(*) FROM facts',[[str(rows)]])
        return pg
    except BaseException:
        pg.close();raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'sig-out/bin'/('ramsql.exe' if os.name=='nt' else 'ramsql'))
    parser.add_argument('--rows',type=int,default=100000)
    parser.add_argument('--work-rows',type=int,default=32)
    parser.add_argument('--expect-capacity-error',action='store_true',help='reproduce the preceding executor on the identical fixture')
    args=parser.parse_args()
    if args.rows<512 or args.work_rows<8:parser.error('rows >=512 and workspace rows >=8 required')
    binary=args.binary.resolve(strict=True)
    caps=json.loads(subprocess.check_output([str(binary),'--capabilities']))
    workers=[1,4] if caps['native_workers_available'] else [1]
    expected=model(args.rows)
    for count in workers:
        with Server(binary,max_clients=8,extra_arguments=arguments(caps,args.rows,args.work_rows,count)) as server:
            pg=seed(server,args.rows,caps)
            try:
                if args.expect_capacity_error:
                    expect_error(pg.query(QUERY),'54000')
                else:
                    pg.ok(QUERY,expected)
                    pg.ok(QUERY+' LIMIT 2 OFFSET 1',expected[1:3])
                    pg.ok('SELECT COUNT(*),SUM(f.amount*p.unit) FROM facts f '
                          'JOIN shops s ON s.id=f.shop JOIN products p ON p.id=f.product WHERE f.id<0',
                          [['0',None]])
                pg.ok('SELECT COUNT(*) FROM facts',[[str(args.rows)]])
            finally:pg.close()
    print(json.dumps(dict(suite='streamed-joins',passed=True,loaded_fact_rows=args.rows,
                         work_rows=args.work_rows,result_rows=16,groups=5,workers=workers,
                         expected_old_capacity_error=args.expect_capacity_error)))


if __name__=='__main__':main()
