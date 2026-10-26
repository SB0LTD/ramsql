"""Beta onboarding and optional expanded-profile test using independent libpq."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from test_auth import running_server

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, default=ROOT/'sig-out/bin'/('ramsql.exe' if os.name == 'nt' else 'ramsql'))
    parser.add_argument('--wide', action='store_true')
    args = parser.parse_args()
    import psycopg
    binary = args.binary.resolve(strict=True)
    caps = json.loads(subprocess.check_output([str(binary), '--capabilities']))
    with tempfile.TemporaryDirectory(prefix='ramsql-beta-') as directory:
        verifier = Path(directory)/'postgres.verifier'
        command = [sys.executable, str(ROOT/'tools/create_verifier.py'), '--output', str(verifier), '--password-stdin']
        password = 'beta-test-only-passphrase'
        created = subprocess.run(command, input=password+'\n', text=True, capture_output=True)
        assert created.returncode == 0, created.stderr
        original = verifier.read_bytes()
        refused = subprocess.run(command, input='different\n', text=True, capture_output=True)
        assert refused.returncode != 0 and verifier.read_bytes() == original
        with running_server(binary, verifier) as port:
            with psycopg.connect(host='127.0.0.1', port=port, user='postgres', password=password,
                                 dbname='ramsql', sslmode='disable', autocommit=True) as db:
                assert db.execute('SELECT 42').fetchone() == (42,)
                if args.wide:
                    assert caps['columns'] >= 16 and caps['result_rows'] >= 1024
                    db.execute('CREATE TABLE wide ('+','.join(f'c{i} INT' for i in range(12))+')')
                    for i in range(600):
                        db.execute('INSERT INTO wide VALUES ('+','.join(str(i+j) for j in range(12))+')')
                    assert db.execute('SELECT * FROM wide ORDER BY c0').fetchall() == [tuple(i+j for j in range(12)) for i in range(600)]
                    long_text = 'x'*200
                    db.execute('CREATE TABLE payload (value TEXT)')
                    db.execute('INSERT INTO payload VALUES (%s)', (long_text,))
                    assert db.execute('SELECT value FROM payload').fetchone() == (long_text,)
    print(json.dumps(dict(suite='beta-onboarding', passed=True, expanded_profile=args.wide)))


if __name__ == '__main__':
    main()
