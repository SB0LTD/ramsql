"""Compare PostgreSQL policy decisions and session boundaries over the real wire."""

import argparse
from contextlib import nullcontext
from pathlib import Path
import hashlib
import json

import psycopg

from test_postgres_compat import local_postgres
from test_server import Server


def probe(parameters):
    observations = []
    with psycopg.connect(**parameters) as db:
        def sql(statement, expected=None, *, values=None, prepare=None, code='00000'):
            try:
                result = db.execute(statement, values, prepare=prepare)
                rows = result.fetchall() if result.description else None
                observed_code = '00000'
            except psycopg.Error as error:
                rows = None
                observed_code = error.sqlstate
            assert observed_code == code and rows == expected, (statement, rows, expected, observed_code, code)
            observations.append((statement, observed_code, rows))

        def copy(command, code, input_row=None, expected=None):
            observed_code = '00000'
            output = None
            try:
                with db.cursor() as cursor:
                    with cursor.copy(command) as stream:
                        if input_row is None:
                            output = b''.join(bytes(chunk) for chunk in stream).decode()
                        else:
                            stream.write_row(input_row)
            except psycopg.Error as error:
                observed_code = error.sqlstate
            assert observed_code == code and output == expected, (command, observed_code, output, code, expected)
            observations.append((command, observed_code, output))

        sql('CREATE TABLE rls_live (id INTEGER PRIMARY KEY, value TEXT)')
        sql("INSERT INTO rls_live VALUES (1,'first'),(2,'second')")
        sql('CREATE ROLE rls_live_reader')
        sql('CREATE ROLE rls_live_bypass BYPASSRLS')
        sql('GRANT SELECT, INSERT, UPDATE, DELETE ON rls_live TO rls_live_reader, rls_live_bypass')
        sql('ALTER TABLE rls_live ENABLE ROW LEVEL SECURITY')
        sql('CREATE POLICY visible ON rls_live USING (id > 1)')
        sql('SET ROLE rls_live_reader')
        sql('SELECT id FROM rls_live ORDER BY id', [(2,)])
        prepared = 'SELECT value FROM rls_live WHERE id = %s'
        sql(prepared, [('second',)], values=(2,), prepare=True)
        copy('COPY rls_live TO STDOUT', '00000', expected='2\tsecond\n')
        copy('COPY rls_live FROM STDIN', '0A000', (3, 'blocked'))
        sql('BEGIN')
        sql('SAVEPOINT keep')
        sql('SET LOCAL row_security=off')
        sql('SHOW row_security', [('off',)])
        sql(prepared, values=(2,), prepare=True, code='42501')
        sql('ROLLBACK TO keep')
        sql('SHOW row_security', [('on',)])
        sql(prepared, [('second',)], values=(2,), prepare=True)
        sql('COMMIT')
        sql('SET row_security=off')
        sql('SELECT id FROM rls_live WHERE false', code='42501')
        sql("INSERT INTO rls_live VALUES (3,'blocked')", code='42501')
        sql('UPDATE rls_live SET id=9 WHERE false', code='42501')
        sql('DELETE FROM rls_live WHERE false', code='42501')
        copy('COPY rls_live TO STDOUT', '42501')
        copy('COPY rls_live FROM STDIN', '42501', (3, 'blocked'))
        sql('RESET ROLE')
        sql('SELECT id FROM rls_live ORDER BY id', [(1,), (2,)])
        sql('SET ROLE rls_live_bypass')
        sql('SELECT id FROM rls_live ORDER BY id', [(1,), (2,)])
        sql('RESET ROLE')
        sql('ALTER TABLE rls_live OWNER TO rls_live_reader')
        sql('SET ROLE rls_live_reader')
        sql('SELECT id FROM rls_live ORDER BY id', [(1,), (2,)])
        sql('ALTER TABLE rls_live FORCE ROW LEVEL SECURITY')
        sql('SELECT id FROM rls_live ORDER BY id', code='42501')
        sql('RESET row_security')
        sql('SELECT id FROM rls_live ORDER BY id', [(2,)])
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    native = parser.add_mutually_exclusive_group(required=True)
    native.add_argument('--postgres-bin', type=Path, help='binaries for a new native PostgreSQL test cluster')
    native.add_argument('--postgres-port', type=int, help='loopback port of an isolated PostgreSQL test cluster')
    args = parser.parse_args()
    out = args.report.resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    binary = args.binary.resolve(strict=True)
    settings = dict(host='127.0.0.1', connect_timeout=3, sslmode='disable', autocommit=True)
    reference = local_postgres(args.postgres_bin, psycopg, out) if args.postgres_bin else nullcontext((
        dict(settings, port=args.postgres_port, user='postgres', dbname='postgres'), {},
    ))
    report = {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'workers': {}}
    try:
        with reference as (native_args, _metadata):
            with psycopg.connect(**native_args) as connection:
                report['postgres'] = connection.execute('SELECT version()').fetchone()[0]
            report['native'] = probe(native_args)
        for workers in (1, 4):
            Server.test_workers = workers
            with Server(binary) as server:
                options = dict(settings, port=server.port, user='ramsql', dbname='ramsql')
                observed = probe(options)
                assert observed == report['native']
                report['workers'][workers] = observed
    finally:
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f"PASS {len(report['native'])} native PostgreSQL row-security observations at one and four workers")


if __name__ == '__main__':
    main()
