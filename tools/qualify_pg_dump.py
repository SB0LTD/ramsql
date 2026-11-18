"""Exercise unmodified PostgreSQL dump and restore in both directions."""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from qualify_pg_tooling import EVIDENCE, IMAGE, check, psql
from test_postgres_compat import running_ramsql
from test_server import Server


FIXTURE = (
    'CREATE SCHEMA dump_fixture',
    'CREATE TABLE dump_fixture.parent (id INTEGER PRIMARY KEY, label TEXT NOT NULL)',
    'CREATE TABLE dump_fixture.child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES dump_fixture.parent(id), count INTEGER NOT NULL DEFAULT 4 CHECK (count > 0))',
    "INSERT INTO dump_fixture.parent VALUES (1, 'north'), (2, 'south')",
    'INSERT INTO dump_fixture.child (id, parent_id) VALUES (11,2),(12,1)',
)
DATA_SQL = ('SELECT c.id, p.label, c.count FROM dump_fixture.child c '
            'JOIN dump_fixture.parent p ON p.id = c.parent_id ORDER BY c.id')
EXPECTED_DATA = '11|south|4\n12|north|4\n'


def execute(client, tool, label, arguments, data=None):
    result = subprocess.run([*client, tool, *arguments], input=data, capture_output=True, timeout=90)
    (EVIDENCE / f'{label}.out').write_bytes(result.stdout)
    (EVIDENCE / f'{label}.err').write_bytes(result.stderr)
    return result


def seed(client, connection, label):
    for index, statement in enumerate(FIXTURE):
        check(psql(client, connection, f'{label}-seed-{index}', '-c', statement))
    require_data(client, connection, label + '-before')


def require_data(client, connection, label):
    result = psql(client, connection, label + '-data', '-At', '-c', DATA_SQL)
    check(result)
    assert result.stdout == EXPECTED_DATA, result.stdout


def require_constraints(client, connection, label):
    failures = (
        ('parent-primary', "INSERT INTO dump_fixture.parent VALUES (1, 'duplicate')", '23505'),
        ('child-primary', 'INSERT INTO dump_fixture.child VALUES (11,2,4)', '23505'),
        ('child-foreign', 'INSERT INTO dump_fixture.child VALUES (13,99,4)', '23503'),
        ('child-check', 'INSERT INTO dump_fixture.child VALUES (13,1,0)', '23514'),
        ('child-not-null', 'INSERT INTO dump_fixture.child VALUES (13,1,NULL)', '23502'),
    )
    for name, statement, state in failures:
        result = psql(client, connection, f'{label}-{name}', '-v', 'VERBOSITY=verbose', '-c', statement)
        assert result.returncode != 0 and f'ERROR:  {state}:' in result.stderr, result.stderr
    default = psql(client, connection, f'{label}-child-default', '-qAt',
                   '-c', 'BEGIN',
                   '-c', 'INSERT INTO dump_fixture.child (id,parent_id) VALUES (13,1) RETURNING count',
                   '-c', 'ROLLBACK')
    check(default)
    assert default.stdout == '4\n', default.stdout
    require_data(client, connection, f'{label}-after-constraints')


def dump(client, connection, label):
    result = execute(client, 'pg_dump', label, [*connection, '--schema=dump_fixture',
                     '--no-owner', '--no-privileges', '--verbose'])
    check(result)
    assert b'COPY dump_fixture.child' in result.stdout, result.stdout
    assert b'FOREIGN KEY (parent_id)' in result.stdout, result.stdout
    return result.stdout


def restore(client, connection, label, data):
    result = execute(client, 'psql', label, ['-X', '-v', 'ON_ERROR_STOP=1', *connection], data)
    check(result)
    require_data(client, connection, label)
    require_constraints(client, connection, label)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--restore-only', action='store_true')
    args = parser.parse_args()
    candidate = args.binary.resolve(strict=True)
    native = ['docker', 'exec', '-i', 'tooling-native']
    native_conn = ['-U', 'postgres', '-d', 'postgres']
    remote_client = ['docker', 'run', '--rm', '-i', '--network', 'host', IMAGE]
    check(execute(native, 'pg_dump', 'dump-client-version', ['--version']))
    seed(native, native_conn, 'dump-native')
    reference = dump(native, native_conn, 'dump-native')
    destinations = ['dump_reference_restore']
    if not args.restore_only:
        destinations.append('dump_candidate_restore')
    for database in destinations:
        check(psql(native, native_conn, f'create-{database}', '-c', f'CREATE DATABASE {database}'))
    restore(native, ['-U', 'postgres', '-d', 'dump_reference_restore'], 'restore-native-native', reference)

    failures = []
    if not args.restore_only:
        with Server(candidate) as server:
            conn = ['-h', '127.0.0.1', '-p', str(server.port), '-U', 'ramsql', '-d', 'ramsql']
            seed(remote_client, conn, 'dump-ramsql')
            try:
                payload = dump(remote_client, conn, 'dump-ramsql')
                restore(native, ['-U', 'postgres', '-d', 'dump_candidate_restore'], 'restore-ramsql-native', payload)
            except AssertionError as error:
                failures.append(f'RAMSQL dump restored to native PostgreSQL failed\n{error}')
    with Server(candidate) as server:
        conn = ['-h', '127.0.0.1', '-p', str(server.port), '-U', 'ramsql', '-d', 'ramsql']
        try:
            restore(remote_client, conn, 'restore-native-ramsql', reference)
        except AssertionError as error:
            failures.append(f'Native PostgreSQL dump restored to RAMSQL failed\n{error}')
    for workers in (1, 4):
        label = f'restore-native-durable-{workers}'
        wal_directory = Path(tempfile.mkdtemp(prefix=f'{label}-', dir=EVIDENCE)).resolve()
        wal = wal_directory / 'database.wal'
        options = ('--workers', str(workers))
        try:
            with running_ramsql(candidate, extra_arguments=options, data_path=wal, create_data=True) as endpoint:
                conn = ['-h', endpoint['host'], '-p', str(endpoint['port']), '-U', 'ramsql', '-d', 'ramsql']
                restore(remote_client, conn, label, reference)
            with running_ramsql(candidate, extra_arguments=options, data_path=wal) as endpoint:
                conn = ['-h', endpoint['host'], '-p', str(endpoint['port']), '-U', 'ramsql', '-d', 'ramsql']
                require_constraints(remote_client, conn, label + '-reopened')
        except AssertionError as error:
            failures.append(f'Native PostgreSQL dump / WAL reopen failed at {workers} workers\n{error}')
    if failures:
        sys.exit('\n'.join(failures))
    if args.restore_only:
        print('PASS stock PostgreSQL plain restore in memory and durable WAL at one and four workers')
    else:
        print('PASS stock PostgreSQL data and schema dump and restore in both directions')


if __name__ == '__main__':
    main()
