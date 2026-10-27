"""Shared-store correctness under native socket reactors; no scaling assertion."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import select
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
from test_postgres_compat import running_ramsql
from test_server import PG, Server, connection, errors, expect_closed, expect_error, query_packet

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.sig-cache/client-deps'))


def cancel_during_work(binary):
    with Server(binary,max_clients=16,extra_arguments=('--workers','4')) as server:
        pg=PG(server.raw())
        try:
            startup=pg.startup()
            pg.ok('CREATE TABLE work(id INT PRIMARY KEY,value INT)')
            for start in range(0,256,64):
                pg.ok('INSERT INTO work VALUES '+','.join(f'({i},{i})' for i in range(start,start+64)))
            keys=[struct.unpack('!II',body) for tag,body in startup if tag==b'K']
            assert len(keys)==1
            slow=('SELECT a.id FROM work a WHERE '
                  '(SELECT COUNT(*) FROM work b WHERE b.id < '
                  '(SELECT COUNT(*) FROM work c WHERE c.id < a.id)) < 0')
            pg.sock.sendall(query_packet(slow))
            time.sleep(0.02)
            with server.raw() as cancel:
                payload=struct.pack('!IIII',16,80877102,*keys[0])
                cancel.sendall(payload[:5]);time.sleep(0.005);cancel.sendall(payload[5:])
                expect_closed(PG(cancel))
            expect_error(pg.ready(),'57014')
            pg.ok('SELECT COUNT(*) FROM work',[['256']])
        finally:
            pg.close()


def parallel_read_and_writer_gate(binary):
    """Prove wire-level read overlap and publication exclusion, without timing throughput."""
    with Server(binary,max_clients=16,extra_arguments=('--workers','4')) as server:
        clients=[PG(server.raw()) for _ in range(3)]
        try:
            startup=[pg.startup() for pg in clients]
            slow,fast,writer=clients
            slow.ok('CREATE TABLE overlap(id INT PRIMARY KEY,value INT)')
            for start in range(0,256,64):
                slow.ok('INSERT INTO overlap VALUES '+','.join(f'({i},{i})' for i in range(start,start+64)))
            query=('SELECT a.id FROM overlap a WHERE '
                   '(SELECT COUNT(*) FROM overlap b WHERE b.id < '
                   '(SELECT COUNT(*) FROM overlap c WHERE c.id < a.id)) < 0')
            slow.sock.sendall(query_packet(query))
            time.sleep(0.04)
            fast.ok('SELECT value FROM overlap WHERE id=1',[['1']])
            assert not select.select([slow.sock],[],[],0)[0], 'long query ended before overlap was demonstrated'
            writer.sock.sendall(query_packet('UPDATE overlap SET value=99 WHERE id=1'))
            assert not select.select([writer.sock],[],[],0.04)[0], 'writer published during an active reader'
            keys=[struct.unpack('!II',body) for tag,body in startup[0] if tag==b'K']
            assert len(keys)==1
            with server.raw() as cancel:
                cancel.sendall(struct.pack('!IIII',16,80877102,*keys[0]))
                expect_closed(PG(cancel))
            expect_error(slow.ready(),'57014')
            assert not errors(writer.ready()), 'queued writer failed after reader cancellation'
            fast.ok('SELECT value FROM overlap WHERE id=1',[['99']])
            slow.ok('SELECT COUNT(*) FROM overlap',[['256']])
            writer.ok('BEGIN',status=b'T')
            writer.ok('UPDATE overlap SET value=100 WHERE id=1',status=b'T')
            fast.ok('SELECT value FROM overlap WHERE id=1',[['99']])
            writer.ok('ROLLBACK')
            fast.ok('SELECT value FROM overlap WHERE id=1',[['99']])
            fast.ok('SET statement_timeout=20')
            expect_error(fast.query(query),'57014')
            fast.ok('SET statement_timeout=0')
            fast.ok('SELECT value FROM overlap WHERE id=1',[['99']])
        finally:
            for pg in clients:pg.close()


def concurrent_join_and_aggregate_reads(binary):
    """Check full three-table results through simultaneous simple-query clients."""
    with Server(binary,max_clients=16,extra_arguments=('--workers','4','--work-rows','4096')) as server:
        seed=PG(server.raw());seed.startup()
        try:
            seed.ok('CREATE TABLE sales(id INT PRIMARY KEY,store INT,product INT,amount INT)')
            seed.ok('CREATE TABLE products(id INT PRIMARY KEY,multiplier INT)')
            seed.ok('CREATE TABLE stores(id INT PRIMARY KEY,region INT)')
            seed.ok('INSERT INTO products VALUES '+','.join(f'({i},{i+1})' for i in range(8)))
            seed.ok('INSERT INTO stores VALUES '+','.join(f'({i},{i%4})' for i in range(16)))
            for start in range(0,1024,64):
                seed.ok('INSERT INTO sales VALUES '+','.join(
                    f'({i},{i%16},{i%8},{i*17%101-50})' for i in range(start,start+64)))
        finally:seed.close()
        def read(identity):
            pg=PG(server.raw());pg.startup()
            try:
                for trial in range(12):
                    region=(identity+trial)%4
                    selected=[i for i in range(1024) if i%4==region]
                    expected=sum((i*17%101-50)*(i%8+1) for i in selected)
                    pg.ok('SELECT s.region,COUNT(*),SUM(f.amount*p.multiplier) FROM sales f '
                          'JOIN products p ON p.id=f.product JOIN stores s ON s.id=f.store '
                          f'WHERE s.region={region} GROUP BY s.region',[[str(region),str(len(selected)),str(expected)]])
                    store=(identity*3+trial)%16
                    selected=range(store,1024,16)
                    pg.ok('WITH chosen AS (SELECT amount FROM sales '
                          f'WHERE store={store}) SELECT COUNT(*),SUM(amount) FROM chosen',
                          [[str(len(selected)),str(sum(i*17%101-50 for i in selected))]])
                return identity
            finally:pg.close()
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sorted(pool.map(read,range(8)))==list(range(8))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'sig-out/bin'/('ramsql.exe' if os.name=='nt' else 'ramsql'))
    args=parser.parse_args();binary=args.binary.resolve(strict=True)
    import psycopg
    capabilities=json.loads(subprocess.check_output([str(binary),'--capabilities']))
    if sys.platform!='darwin':assert capabilities['libc_linked'] is False,'Windows/Linux runtime must not link allocator-backed CRT/pthreads'
    if not capabilities['native_workers_available']:
        assert sys.platform=='darwin','Windows/Linux release binaries must provide native no-heap workers'
        rejected=subprocess.run([str(binary),'--capabilities','--workers','2'],capture_output=True)
        assert rejected.returncode!=0 and b'WorkersUnsupportedOnPlatform' in rejected.stderr,rejected
        print(json.dumps(dict(suite='workers',passed=True,workers=1,worker_execution=False,
            unsupported_rejected=True,note='No native no-heap thread primitive for this binary; no multicore test/claim')))
        return
    parallel_caps=json.loads(subprocess.check_output([str(binary),'--capabilities','--workers','4']))
    assert parallel_caps['parallel_simple_reads'] and parallel_caps['sql_execution_workers']==4
    assert parallel_caps['exclusive_sql_workers']==1
    # Reuse the complete framing/fairness/cleanup contracts with native reactors.
    subprocess.run([sys.executable,str(ROOT/'tools/test_server.py'),'--binary',str(binary),'--workers','4'],check=True)
    for suite in ('test_clients.py','test_auth.py'):
        subprocess.run([sys.executable,str(ROOT/'tools'/suite),'--binary',str(binary),'--workers','4'],check=True)
    with tempfile.TemporaryDirectory(prefix='ramsql-worker-restart-') as directory:
        wal=Path(directory)/'database.wal'
        options=('--workers','4','--max-clients','32')
        with running_ramsql(binary,extra_arguments=options,data_path=wal) as conn:
            with psycopg.connect(**conn,prepare_threshold=None) as db:
                db.execute('CREATE TABLE counter(id INT PRIMARY KEY,value INT)')
                db.execute('INSERT INTO counter VALUES(1,0)')
            def increments(identity):
                with psycopg.connect(**conn,prepare_threshold=None) as db:
                    for _ in range(100):
                        db.execute('UPDATE counter SET value=value+1 WHERE id=1',prepare=False)
                    return identity
            with ThreadPoolExecutor(max_workers=8) as pool:
                assert sorted(pool.map(increments,range(8)))==list(range(8))
            with psycopg.connect(**conn) as db:
                assert db.execute('SELECT value FROM counter WHERE id=1').fetchone()==(800,)
        with running_ramsql(binary,extra_arguments=options,data_path=wal) as conn:
            with psycopg.connect(**conn) as db:
                assert db.execute('SELECT value FROM counter WHERE id=1').fetchone()==(800,)
    cancel_during_work(binary)
    parallel_read_and_writer_gate(binary)
    concurrent_join_and_aggregate_reads(binary)
    print(json.dumps(dict(suite='workers',passed=True,reactors=4,shared_counter_updates=800,durable_restart=True,running_cancel=True,
                         concurrent_reads=True,writer_exclusion=True,uncommitted_visibility=True,three_way_join_and_cte_queries=192)))


if __name__=='__main__':main()
