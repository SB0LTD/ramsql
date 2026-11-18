"""Exercise unmodified PostgreSQL dump and restore in both directions."""

from pathlib import Path
import subprocess
import sys

from qualify_pg_tooling import EVIDENCE, IMAGE, check, psql
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
    result = subprocess.run([*client, tool, *arguments], input=data, capture_output=True,
                            text=True, timeout=90)
    (EVIDENCE / f'{label}.out').write_text(result.stdout)
    (EVIDENCE / f'{label}.err').write_text(result.stderr)
    return result


def seed(client, connection, label):
    for index, statement in enumerate(FIXTURE):
        check(psql(client, connection, f'{label}-seed-{index}', '-c', statement))
    require_data(client, connection, label + '-before')


def require_data(client, connection, label):
    result = psql(client, connection, label + '-data', '-At', '-c', DATA_SQL)
    check(result)
    assert result.stdout == EXPECTED_DATA, result.stdout


def dump(client, connection, label):
    result = execute(client, 'pg_dump', label, [*connection, '--schema=dump_fixture',
                     '--no-owner', '--no-privileges', '--verbose'])
    check(result)
    assert 'COPY dump_fixture.child' in result.stdout, result.stdout
    assert 'FOREIGN KEY (parent_id)' in result.stdout, result.stdout
    return result.stdout


def restore(client, connection, label, data):
    result = execute(client, 'psql', label, ['-X', '-v', 'ON_ERROR_STOP=1', *connection], data)
    check(result)
    require_data(client, connection, label)


def main():
    candidate = Path(sys.argv[1]).resolve()
    native = ['docker', 'exec', '-i', 'tooling-native']
    native_conn = ['-U', 'postgres', '-d', 'postgres']
    remote_client = ['docker', 'run', '--rm', '-i', '--network', 'host', IMAGE]
    check(execute(native, 'pg_dump', 'dump-client-version', ['--version']))
    seed(native, native_conn, 'dump-native')
    reference = dump(native, native_conn, 'dump-native')
    for database in ('dump_reference_restore', 'dump_candidate_restore'):
        check(psql(native, native_conn, f'create-{database}', '-c', f'CREATE DATABASE {database}'))
    restore(native, ['-U', 'postgres', '-d', 'dump_reference_restore'], 'restore-native-native', reference)

    failures = []
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
    if failures:
        sys.exit('\n'.join(failures))
    print('PASS stock PostgreSQL data and schema dump and restore in both directions')


if __name__ == '__main__':
    main()
