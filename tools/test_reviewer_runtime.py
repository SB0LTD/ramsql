"""Independent client regressions for the beta reviewer’s concrete failures."""
import argparse
from contextlib import ExitStack
from decimal import Decimal
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
from test_postgres_compat import running_ramsql
from test_server import PG, startup_packet, expect_error

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.sig-cache/client-deps'))


def seed(db):
    db.execute('CREATE TABLE sales(id INT PRIMARY KEY,product INT,shop INT,amount INT)',prepare=False)
    db.execute('CREATE TABLE products(id INT PRIMARY KEY,price INT)',prepare=False)
    db.execute('CREATE TABLE shops(id INT PRIMARY KEY)',prepare=False)
    for start in range(0,5000,500):
        db.execute('INSERT INTO sales VALUES '+','.join(f'({i},{i%16},{i%40},{i*7%1000})' for i in range(start,start+500)),prepare=False)
    db.execute('INSERT INTO products VALUES '+','.join(f'({i},{10+i})' for i in range(16)),prepare=False)
    db.execute('INSERT INTO shops VALUES '+','.join(f'({i})' for i in range(40)),prepare=False)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'sig-out/bin'/('ramsql.exe' if os.name=='nt' else 'ramsql'))
    args=parser.parse_args()
    import psycopg
    binary=args.binary.resolve(strict=True)
    caps=json.loads(subprocess.check_output([str(binary),'--capabilities','--rows','6000',
        '--work-rows','1000000','--result-rows','4096','--max-clients','32']))
    assert caps['work_rows']==1000000 and caps['result_rows']==4096 and caps['clients']==32
    assert caps['query_workspace_bytes']>caps['result_bytes_per_client']
    assert binary.stat().st_size<100*1024*1024,'runtime budgets must not produce giant executable data'
    with running_ramsql(binary,extra_arguments=('--rows','6000','--work-rows','128','--result-rows','4096')) as conn:
        with psycopg.connect(**conn,prepare_threshold=None) as db:
            seed(db)
            query='SELECT COUNT(*),SUM(s.amount) FROM sales s JOIN products p ON p.id=s.product JOIN shops t ON t.id=s.shop WHERE s.shop=3'
            selected=[i*7%1000 for i in range(5000) if i%40==3]
            assert db.execute(query,prepare=False).fetchone()==(125,sum(selected))
    with running_ramsql(binary,extra_arguments=('--rows','6000','--work-rows','8192','--result-rows','4096','--max-clients','32')) as conn:
        with psycopg.connect(**conn,prepare_threshold=None) as db:
            seed(db)
            values=[i*7%1000 for i in range(5000)]
            expected=[(g,sum(i%40==g for i in range(5000)),sum(values[i] for i in range(5000) if i%40==g)) for g in range(40)]
            assert db.execute('SELECT shop,COUNT(*),SUM(amount) FROM sales GROUP BY shop ORDER BY shop',prepare=False).fetchall()==expected
            for sql,wanted in [
                ('SELECT COUNT(*) FROM sales',(5000,)),
                ('SELECT SUM(amount) FROM sales',(sum(values),)),
                ('SELECT AVG(amount) FROM sales',(Decimal(sum(values))/5000,)),
                ('SELECT MIN(amount),MAX(amount) FROM sales',(min(values),max(values))),
                ('SELECT COUNT(*) FROM sales WHERE amount>500',(sum(v>500 for v in values),)),
                ('SELECT COUNT(DISTINCT shop) FROM sales',(40,)),
            ]:
                assert db.execute(sql,prepare=False).fetchone()==wanted,sql
            assert db.execute('SELECT id FROM sales ORDER BY id LIMIT 2000',prepare=False).fetchall()==[(i,) for i in range(2000)]
            assert db.execute('SELECT 1'+' '*20000,prepare=True).fetchone()==(1,)
            try:
                db.execute('SELECT 1'+' '*80000,prepare=False)
                raise AssertionError('oversize frame accepted')
            except psycopg.Error as error:
                assert error.sqlstate=='54000',error
            assert db.execute('SELECT 7',prepare=False).fetchone()==(7,)
        with ExitStack() as stack:
            clients=[stack.enter_context(psycopg.connect(**conn)) for _ in range(32)]
            for db in clients:
                db.execute('BEGIN')
                assert db.execute('SELECT 1').fetchone()==(1,)
            for negotiate in (None,80877103,80877104):
                raw=PG(socket.create_connection((conn['host'],conn['port']),timeout=5))
                try:
                    if negotiate:
                        raw.sock.sendall(struct.pack('!II',8,negotiate))
                        assert raw.exact(1)==b'N'
                    raw.sock.sendall(startup_packet())
                    tag,body=raw.message()
                    assert tag==b'E'
                    expect_error([(tag,body)],'53300')
                finally:
                    raw.close(False)
    print(json.dumps(dict(suite='reviewer-runtime',passed=True,seed_rows=5000,
        runtime_result_rows=2000,small_workspace_join=True,bulk_rows_per_insert=500,
        large_prepared_sql=True,oversize_sql_recoverable=True,connections=32)))


if __name__=='__main__':main()
