"""Stock PostgreSQL oracle for COPY issued through Parse/Bind/Execute."""
import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path

import psycopg

from qualify_pg_extended_sync import connect, extended, synchronize
from test_postgres_compat import local_postgres, running_ramsql
from test_server import errors, packet, query_packet


def portal(backend, source):
    parse = packet(b'P', b'\0' + source.encode() + b'\0\0\0')
    bind = packet(b'B', b'\0\0\0\0\0\0\0\0')
    execute = packet(b'E', b'\0\0\0\0\0')
    backend.sock.sendall(parse + bind + execute + packet(b'H'))
    assert backend.message() == (b'1', b'')
    assert backend.message() == (b'2', b'')
    return backend.message()


def consume(backend, command):
    message = backend.message()
    assert message == (b'C', command + b'\0'), message


def err(backend, state):
    message = backend.message()
    assert [error['C'] for error in errors([message])] == [state], message


def probe(port):
    writer, observer = connect(port), connect(port)
    try:
        observer.ok('CREATE TABLE copy_group (id INTEGER PRIMARY KEY)')
        def seen(*ids):
            observer.ok('SELECT id FROM copy_group ORDER BY id', [[str(value)] for value in ids])
        source = 'COPY copy_group FROM STDIN'
        completed = []
        assert portal(writer, source)[0] == b'G'
        writer.sock.sendall(packet(b'S') + packet(b'd', b'1\n2\n') + packet(b'c') + packet(b'H'))
        consume(writer, b'COPY 2')
        seen()
        synchronize(writer)
        seen(1, 2)
        completed.append('standalone-copy-and-sync')

        assert portal(writer, source)[0] == b'G'
        writer.sock.sendall(packet(b'd', b'3\n1\n') + packet(b'c') + packet(b'H'))
        err(writer, '23505')
        synchronize(writer)
        seen(1, 2)
        completed.append('copy-row-error')

        assert portal(writer, source)[0] == b'G'
        writer.sock.sendall(packet(b'd', b'3\n') + packet(b'f', b'client aborted\0') + packet(b'H'))
        err(writer, '57014')
        synchronize(writer)
        seen(1, 2)
        completed.append('copy-fail-rollback')

        writer.ok('BEGIN', status=b'T')
        assert portal(writer, source)[0] == b'G'
        writer.sock.sendall(packet(b'd', b'3\n') + packet(b'c') + packet(b'H'))
        consume(writer, b'COPY 1')
        synchronize(writer, status=b'T')
        seen(1, 2)
        writer.ok('COMMIT')
        seen(1, 2, 3)
        completed.append('explicit-copy-and-sync')

        assert portal(writer, 'COPY (SELECT id FROM copy_group ORDER BY id) TO STDOUT')[0] == b'H'
        for value in (b'1\n', b'2\n', b'3\n'):
            assert writer.message() == (b'd', value)
        assert writer.message() == (b'c', b'')
        consume(writer, b'COPY 3')
        synchronize(writer)
        completed.append('copy-out-results')

        # A single simple COPY participates in an unsynchronized Execute group.
        extended(writer, 'INSERT INTO copy_group VALUES (10)', b'INSERT 0 1')
        writer.sock.sendall(query_packet(source))
        assert writer.message()[0] == b'G'
        writer.sock.sendall(packet(b'd', b'11\n') + packet(b'c'))
        consume(writer, b'COPY 1')
        assert writer.message() == (b'Z', b'T')
        seen(1, 2, 3)
        writer.ok('SELECT 30; SELECT 31', [['30'], ['31']])
        seen(1, 2, 3, 10, 11)
        completed.append('simple-copy-in-unsynchronized-extended-group')

        extended(writer, 'INSERT INTO copy_group VALUES (12)', b'INSERT 0 1')
        writer.sock.sendall(query_packet(source))
        assert writer.message()[0] == b'G'
        writer.sock.sendall(packet(b'd', b'13\n') + packet(b'f', b'client aborted\0'))
        err(writer, '57014')
        assert writer.message() == (b'Z', b'I')
        seen(1, 2, 3, 10, 11)
        completed.append('simple-copy-fail-aborts-extended-write')
        return completed
    finally:
        observer.close()
        writer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    native = parser.add_mutually_exclusive_group(required=True)
    native.add_argument('--postgres-bin', type=Path)
    native.add_argument('--postgres-port', type=int)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    directory = args.report.resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    reference = local_postgres(args.postgres_bin, psycopg, directory) if args.postgres_bin else nullcontext((
        dict(host='127.0.0.1', port=args.postgres_port, user='ramsql', dbname='ramsql', sslmode='disable'), {},
    ))
    proof = {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'candidate': {}}
    try:
        with reference as (settings, _metadata):
            with psycopg.connect(**settings) as db:
                proof['postgres'] = db.execute('SELECT version()').fetchone()[0]
            proof['native'] = probe(settings['port'])
            assert len(proof['native']) == 7
        for workers in (1, 4):
            options = ['--workers', str(workers)]
            with running_ramsql(binary, extra_arguments=options) as endpoint:
                completed = probe(endpoint['port'])
                assert completed == proof['native']
                proof['candidate'][f'memory-{workers}'] = completed
            wal = directory / f'extended-copy-{workers}.wal'
            with running_ramsql(binary, extra_arguments=options, data_path=wal, create_data=True) as endpoint:
                completed = probe(endpoint['port'])
                assert completed == proof['native']
                proof['candidate'][f'durable-{workers}'] = completed
            with running_ramsql(binary, extra_arguments=options, data_path=wal) as endpoint:
                session = connect(endpoint['port'])
                try:
                    session.ok('SELECT id FROM copy_group ORDER BY id', [['1'], ['2'], ['3'], ['10'], ['11']])
                    proof['candidate'][f'reopened-{workers}'] = True
                finally:
                    session.close()
    finally:
        args.report.write_text(json.dumps(proof, indent=2) + '\n', encoding='utf-8')
    print('PASS native extended COPY input, rollback and output, with durable reopen at one and four workers')


if __name__ == '__main__':
    main()
