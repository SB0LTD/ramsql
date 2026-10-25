"""Require storage/recovery beyond the compiled row profile (psycopg client)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.sig-cache/client-deps'))
from test_postgres_compat import running_ramsql

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary',type=Path,default=ROOT/'sig-out/bin'/('ramsql.exe' if os.name=='nt' else 'ramsql'))
    args=parser.parse_args()
    import psycopg
    binary=args.binary.resolve(strict=True)
    folder=ROOT/'.sig-cache'/('runtime-capacity-'+uuid.uuid4().hex);folder.mkdir()
    wal=folder/'large.wal';backup=folder/'backup.wal'
    extra=('--rows','4096')
    with running_ramsql(binary,extra_arguments=extra,data_path=wal) as conn:
        with psycopg.connect(**conn) as db:
            db.execute('CREATE TABLE large (id INT PRIMARY KEY, value INT)')
            for start in range(0,2048,32):
                cursor=db.execute('INSERT INTO large VALUES '+','.join(f'({i},{2048-i})' for i in range(start,start+32)))
                assert cursor.rowcount==32
            assert db.execute('SELECT COUNT(*) FROM large').fetchone()==(2048,)
            assert db.execute('SELECT id FROM large ORDER BY value LIMIT 16').fetchall()==[(2047-i,) for i in range(16)]
            assert db.execute('SELECT id % 64, COUNT(*), SUM(value) FROM large GROUP BY id % 64 ORDER BY 1').fetchall()==[(g,32,33792-32*g) for g in range(64)]
            db.execute('UPDATE large SET value = -1 WHERE id = 2047')
    with running_ramsql(binary,extra_arguments=extra,data_path=wal) as conn:
        with psycopg.connect(**conn) as db:
            assert db.execute('SELECT COUNT(*) FROM large').fetchone()==(2048,)
            assert db.execute('SELECT value FROM large WHERE id = 2047').fetchone()==(-1,)
            assert db.execute('SELECT id % 64, COUNT(*), SUM(value) FROM large GROUP BY id % 64 ORDER BY 1').fetchall()==[(g,32,33792-32*g-(2 if g==63 else 0)) for g in range(64)]
    before=hashlib.sha256(wal.read_bytes()).hexdigest()
    try:
        with running_ramsql(binary,extra_arguments=('--rows','512'),data_path=wal):
            raise AssertionError('inadequate recovery budget accepted')
    except RuntimeError:
        pass
    assert hashlib.sha256(wal.read_bytes()).hexdigest()==before,'failed recovery modified source WAL'
    subprocess.run([str(binary),'--rows','4096','--data',str(wal),'--backup-to',str(backup)],check=True,capture_output=True)
    with running_ramsql(binary,extra_arguments=extra,data_path=backup) as conn:
        with psycopg.connect(**conn) as db:
            assert db.execute('SELECT COUNT(*) FROM large').fetchone()==(2048,)
            assert db.execute('SELECT value FROM large WHERE id = 2047').fetchone()==(-1,)
    print(json.dumps({'suite':'runtime-capacity','pass':True,'rows':2048,'durable_restart':True,'offline_restore':True,'small_budget_fail_closed':True,'artifacts':str(folder)}))

if __name__=='__main__':main()
