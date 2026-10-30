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
from test_server import PG, Server, connection, errors, expect_closed, expect_error, query_packet, packet, rows
from benchmark import process_memory

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


def extended_packets(source,values=()):
    """Actual Parse/Bind/Describe/Execute/Sync, including typed parameters."""
    parse=b'\0'+source.encode()+b'\0'+struct.pack('!H',len(values))+b''.join(struct.pack('!I',23) for _ in values)
    bind=b'\0\0'+struct.pack('!HH',0,len(values))
    for value in values:
        raw=str(value).encode();bind+=struct.pack('!I',len(raw))+raw
    bind+=struct.pack('!H',0)
    return packet(b'P',parse)+packet(b'B',bind)+packet(b'D',b'P\0')+packet(b'E',b'\0'+struct.pack('!I',0))+packet(b'S')


def parallel_read_and_writer_gate(binary,extended=False):
    """Prove wire-level read overlap and publication exclusion, without timing throughput."""
    with Server(binary,max_clients=16,extra_arguments=('--workers','4')) as server:
        clients=[PG(server.raw()) for _ in range(3)]
        try:
            startup=[pg.startup() for pg in clients]
            slow,fast,writer=clients
            slow.ok('CREATE TABLE overlap(id INT PRIMARY KEY,value INT)')
            for start in range(0,256,64):
                slow.ok('INSERT INTO overlap VALUES '+','.join(f'({i},{i})' for i in range(start,start+64)))
            query=('/* reviewer /* nested */ comment */\n-- JDBC source header\nSELECT a.id FROM overlap a WHERE '
                   '(SELECT COUNT(*) FROM overlap b WHERE b.id < '
                   '(SELECT COUNT(*) FROM overlap c WHERE c.id < a.id)) < 0')
            def send_read(pg,source,prepare_first=False):
                encoded=extended_packets(source) if extended else query_packet(source)
                if extended and prepare_first:
                    # Drain Parse/Bind/Describe acknowledgments explicitly;
                    # pending metadata must not be mistaken for query completion.
                    pg.sock.sendall(encoded[:-15]+packet(b'H'))
                    for expected_tag in (b'1',b'2',b'T'):
                        tag,_=pg.message();assert tag==expected_tag,(tag,expected_tag)
                    encoded=encoded[-15:]
                pg.sock.sendall(encoded)
            def read(pg,source,expected):
                send_read(pg,source);messages=pg.ready();assert not errors(messages),errors(messages)
                assert rows(messages)==expected,(source,rows(messages),expected)
            send_read(slow,query,prepare_first=True)
            time.sleep(0.04)
            read(fast,'SELECT value FROM overlap WHERE id=1',[['1']])
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
            read(fast,'SELECT value FROM overlap WHERE id=1',[['99']])
            slow.ok('SELECT COUNT(*) FROM overlap',[['256']])
            writer.ok('BEGIN',status=b'T')
            writer.ok('UPDATE overlap SET value=100 WHERE id=1',status=b'T')
            read(fast,'SELECT value FROM overlap WHERE id=1',[['99']])
            writer.ok('ROLLBACK')
            read(fast,'SELECT value FROM overlap WHERE id=1',[['99']])
            fast.ok('SET statement_timeout=20')
            send_read(fast,query);expect_error(fast.ready(),'57014')
            fast.ok('SET statement_timeout=0')
            read(fast,'SELECT value FROM overlap WHERE id=1',[['99']])
        finally:
            for pg in clients:pg.close()


def concurrent_prepared_reads(binary):
    """Named statements with changing typed parameters and binary results."""
    import psycopg
    with Server(binary,max_clients=16,extra_arguments=('--workers','4')) as server:
        with psycopg.connect(host='127.0.0.1',port=server.port,user='postgres',dbname='ramsql',autocommit=True) as seed:
            seed.execute('CREATE TABLE prepared_reads(id INT PRIMARY KEY,g INT NOT NULL,value INT NOT NULL,label TEXT)')
            for start in range(0,512,64):
                seed.execute('INSERT INTO prepared_reads VALUES '+','.join(f"({i},{i%16},{i*7-9},'row-{i}')" for i in range(start,start+64)))
        def reader(identity):
            with psycopg.connect(host='127.0.0.1',port=server.port,user='postgres',dbname='ramsql',autocommit=True,prepare_threshold=0) as db:
                with db.cursor(binary=True) as cursor:
                    for trial in range(40):
                        key=(identity*31+trial*17)%512
                        cursor.execute('SELECT id,value,label FROM prepared_reads WHERE id=%s',(key,),prepare=True)
                        assert cursor.fetchall()==[(key,key*7-9,f'row-{key}')]
                        group=(identity+trial)%16
                        cursor.execute('SELECT id,value FROM prepared_reads WHERE g=%s AND value>0',(group,),prepare=True)
                        assert cursor.fetchall()==[(i,i*7-9) for i in range(512) if i%16==group and i*7-9>0]
            return identity
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sorted(pool.map(reader,range(8)))==list(range(8))


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


def worker_scratch_reuse(binary):
    caps=json.loads(subprocess.check_output([str(binary),'--capabilities','--workers','4']))
    with Server(binary,max_clients=8,extra_arguments=('--workers','4')) as server:
        clients=[PG(server.raw()) for _ in range(8)]
        try:
            for pg in clients:pg.startup()
            clients[0].ok('CREATE TABLE clock(t TIMESTAMPTZ)')
            clients[0].ok("INSERT INTO clock VALUES('2024-01-02 00:00:00+00')")
            # Every reactor warms one context; the next four clients reuse it.
            for pg in clients[:4]:pg.ok('SELECT 1',[['1']])
            before=process_memory(server.process)
            zones=[('UTC','2024-01-02 00:00:00+00'),('UTC+2','2024-01-01 22:00:00-02'),
                   ('UTC-3','2024-01-02 03:00:00+03')]
            for identity,pg in enumerate(clients):pg.ok(f"SET TIME ZONE '{zones[identity%3][0]}'")
            for _ in range(8):
                for identity,pg in enumerate(clients):
                    pg.ok('SELECT t FROM clock',[[zones[identity%3][1]]])
            after=process_memory(server.process)
            assert before and after,(before,after)
            metric='private_commit_bytes' if os.name=='nt' else 'virtual_bytes'
            growth=after[metric]-before[metric]
            # Page rounding and OS bookkeeping may vary, but another four full
            # contexts must not be reserved when the second set of clients reads.
            allowance=max(1024*1024,caps['read_context_payload_bytes_per_worker']//2)
            assert growth<allowance,(metric,growth,allowance,before,after)
            return dict(metric=metric,growth_bytes=growth,payload_bytes_per_worker=caps['read_context_payload_bytes_per_worker'],
                        before=before,after=after,clients=8,workers=4)
        finally:
            for pg in clients:pg.close()


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
    assert parallel_caps['parallel_extended_reads']
    assert parallel_caps['exclusive_sql_workers']==1
    # Reuse the complete framing/fairness/cleanup contracts with native reactors.
    subprocess.run([sys.executable,str(ROOT/'tools/test_server.py'),'--binary',str(binary),'--workers','4'],check=True)
    for suite in ('test_clients.py','test_auth.py'):
        subprocess.run([sys.executable,str(ROOT/'tools'/suite),'--binary',str(binary),'--workers','4'],check=True)
    with tempfile.TemporaryDirectory(prefix='ramsql-worker-restart-') as directory:
        wal=Path(directory)/'database.wal'
        options=('--workers','4','--max-clients','32')
        with running_ramsql(binary,extra_arguments=options,data_path=wal,create_data=True) as conn:
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
    parallel_read_and_writer_gate(binary,extended=True)
    concurrent_prepared_reads(binary)
    concurrent_join_and_aggregate_reads(binary)
    scratch=worker_scratch_reuse(binary)
    print(json.dumps(dict(suite='workers',passed=True,reactors=4,shared_counter_updates=800,durable_restart=True,running_cancel=True,
                         concurrent_reads=True,writer_exclusion=True,uncommitted_visibility=True,three_way_join_and_cte_queries=192,
                         worker_scratch_reuse=True,reader_session_isolation=True,scratch_reuse=scratch,
                         extended_read_overlap=True,extended_writer_exclusion=True,extended_cancel_timeout=True,named_prepared_binary_queries=640)))


if __name__=='__main__':main()
