"""Shared-store correctness under native socket reactors; no scaling assertion."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
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
    print(json.dumps(dict(suite='workers',passed=True,reactors=4,shared_counter_updates=800,durable_restart=True,running_cancel=True)))


if __name__=='__main__':main()
