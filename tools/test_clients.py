"""Independent DB-API clients against all three live RAMSQL listeners."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.sig-cache' / 'client-deps'))

@contextmanager
def running_server(binary):
    process = subprocess.Popen([str(binary), '--serve', '--memory', '--insecure-local', '--port', '0', '--mysql-port', '0', '--mssql-port', '0'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    lines = queue.Queue()
    def read_ready():
        for _ in range(3): lines.put(process.stdout.readline())
    threading.Thread(target=read_ready, daemon=True).start()
    try:
        ports = {}
        for _ in range(3):
            line = lines.get(timeout=15)
            assert line.startswith('RAMSQL '), (line, process.poll())
            _, protocol, endpoint = line.split()
            ports[protocol] = int(endpoint.rsplit(':', 1)[1])
        yield ports
        assert process.poll() is None, 'server crashed during client testing'
    finally:
        process.terminate()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        errors = process.stderr.read()
        assert not errors, errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT/'sig-out'/'bin'/('ramsql.exe' if os.name=='nt' else 'ramsql'))
    args = parser.parse_args()
    import psycopg
    import pymysql
    import pytds
    with running_server(args.binary.resolve()) as ports:
        pg = psycopg.connect(host='127.0.0.1',port=ports['postgres'],user='ramsql',dbname='ramsql',password='',sslmode='disable',autocommit=True,connect_timeout=5)
        with pg.cursor() as cur:
            cur.execute('CREATE TABLE clients (id INTEGER PRIMARY KEY, active BOOLEAN, label TEXT)')
            cur.execute('INSERT INTO clients VALUES (%s, %s, %s)',(1,True,'0123'),prepare=True)
            cur.execute('SELECT id, active, label FROM clients WHERE id = %s',(1,),prepare=True)
            assert cur.fetchone()==(1,True,'0123')
            for _ in range(4):
                cur.execute('SELECT label FROM clients WHERE id = %s',(1,),prepare=True)
                assert cur.fetchone()==('0123',)
        with pg.cursor(binary=True) as cur:
            cur.execute('SELECT id, active, label FROM clients WHERE id = %s',(1,))
            assert cur.fetchone()==(1,True,'0123')
        pg.autocommit=False
        with pg.cursor() as cur:
            cur.execute("UPDATE clients SET label = 'rolled back' WHERE id = 1")
        pg.rollback()
        with pg.cursor() as cur:
            cur.execute('SELECT label FROM clients WHERE id = 1')
            assert cur.fetchone()==('0123',)
        pg.commit(); pg.autocommit=True
        mysql = pymysql.connect(host='127.0.0.1',port=ports['mysql'],user='ramsql',password='',database='ramsql',autocommit=True,charset='utf8mb4',connect_timeout=5,read_timeout=5,write_timeout=5)
        with mysql.cursor() as cur:
            cur.execute('SELECT id, label FROM clients WHERE id = 1')
            assert cur.fetchone()==(1,'0123')
            cur.execute('INSERT INTO clients VALUES (%s, %s, %s)',(2,False,'MySQL'))
            assert cur.rowcount==1
            cur.execute('SELECT id FROM clients WHERE id = 999')
            assert cur.fetchall()==()
            try: cur.execute('SELECT unknown FROM clients')
            except pymysql.MySQLError: pass
            else: raise AssertionError('MySQL SQL error accepted')
            cur.execute('SELECT id FROM clients WHERE id = 2')
            assert cur.fetchone()==(2,)
        mysql.begin()
        with mysql.cursor() as cur: cur.execute("UPDATE clients SET label = 'rollback' WHERE id = 2")
        mysql.rollback()
        tds = pytds.connect(server='127.0.0.1',port=ports['mssql'],user='ramsql',password='',database='ramsql',autocommit=True,timeout=5,login_timeout=5,use_mars=False)
        with tds.cursor() as cur:
            cur.execute('SELECT id, active, label FROM clients ORDER BY id')
            assert cur.fetchall()==[(1,True,'0123'),(2,False,'MySQL')]
            cur.execute("INSERT INTO clients VALUES (3, TRUE, 'SQL Server')")
            cur.execute('SELECT id, label FROM clients WHERE id = 3')
            assert cur.fetchone()==(3,'SQL Server')
            cur.execute('SELECT label FROM clients WHERE id = %s',(3,))
            assert cur.fetchone()==('SQL Server',)
            cur.execute('INSERT INTO clients VALUES (%s, %s, %s)',(4,True,"RPC 'quoted' שלום"))
            cur.execute('SELECT id, active, label FROM clients WHERE id = %s',(4,))
            assert cur.fetchone()==(4,True,"RPC 'quoted' שלום")
            cur.execute('DELETE FROM clients WHERE id = 4')
        tds.autocommit=False
        with tds.cursor() as cur: cur.execute("UPDATE clients SET label = 'rollback' WHERE id = 3")
        tds.rollback()
        tds.autocommit=True
        with tds.cursor() as cur:
            cur.execute('SELECT label FROM clients WHERE id = 3')
            assert cur.fetchone()==('SQL Server',)
        with pg.cursor() as cur:
            cur.execute('SELECT id FROM clients ORDER BY id')
            assert cur.fetchall()==[(1,),(2,),(3,)]
            cur.execute("INSERT INTO clients VALUES (-9223372036854775808, TRUE, 'שלום')")
            cur.execute('SELECT id, active, label FROM clients WHERE id = -9223372036854775808')
            assert cur.fetchone()==(-9223372036854775808,True,'שלום')
        for connection in (tds,mysql,pg): connection.close()
    print('PASS independent clients: psycopg, PyMySQL, python-tds; shared store, prepared/binary PG, transactions and types')

if __name__=='__main__': main()
