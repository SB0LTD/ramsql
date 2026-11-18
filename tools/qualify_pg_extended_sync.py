"""Qualify real PostgreSQL extended transactions and durable recovery."""

import argparse
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import socket

import psycopg

from test_postgres_compat import local_postgres, running_ramsql
from test_server import PG, errors, packet, rows


def connect(port):
    backend = PG(socket.create_connection(('127.0.0.1', port), timeout=5))
    backend.startup()
    return backend


def extended(backend, statement, command_tag=None, sqlstate=None):
    wire = (packet(b'P', b'\0' + statement.encode() + b'\0\0\0')
            + packet(b'B', b'\0\0\0\0\0\0\0\0')
            + packet(b'E', b'\0\0\0\0\0') + packet(b'H'))
    backend.sock.sendall(wire)
    responses = [backend.message() for _ in range(3)]
    assert [tag for tag, _ in responses[:2]] == [b'1', b'2'], responses
    if sqlstate:
        assert [error['C'] for error in errors(responses)] == [sqlstate], responses
    else:
        assert responses[2] == (b'C', command_tag + b'\0'), responses


def synchronize(backend, *, status=b'I', sqlstate=None, body=b''):
    backend.sock.sendall(packet(b'S', body))
    response = backend.ready()
    assert response[-1] == (b'Z', status), response
    assert [error['C'] for error in errors(response)] == ([sqlstate] if sqlstate else []), response


def ids(backend):
    return [int(row[0]) for row in rows(backend.ok('SELECT id FROM sync_fixture ORDER BY id'))]


def probe(port):
    writer, observer = connect(port), connect(port)
    try:
        observer.ok('CREATE TABLE sync_fixture (id INT PRIMARY KEY)')
        extended(writer, 'INSERT INTO sync_fixture VALUES (1)', b'INSERT 0 1')
        result = {'before_sync': ids(observer)}
        synchronize(writer)
        result['after_sync'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (2)', b'INSERT 0 1')
        extended(writer, 'INSERT INTO sync_fixture VALUES (1)', sqlstate='23505')
        synchronize(writer)
        result['after_group_error'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (3)', b'INSERT 0 1')
        synchronize(writer, sqlstate='08P01', body=b'nonempty')
        result['after_malformed_sync'] = ids(observer)
        synchronize(writer)
        result['after_valid_sync'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (4)', b'INSERT 0 1')
        extended(writer, 'BEGIN', b'BEGIN')
        synchronize(writer, status=b'T')
        result['explicit_before_commit'] = ids(observer)
        writer.ok('COMMIT')
        result['explicit_after_commit'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (5)', b'INSERT 0 1')
        extended(writer, 'BEGIN ISOLATION LEVEL SERIALIZABLE', sqlstate='25001')
        synchronize(writer)
        result['failed_begin_promotion'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (7)', b'INSERT 0 1')
        response = writer.query('SELECT 1/0')
        assert [error['C'] for error in errors(response)] == ['22012'], response
        assert response[-1] == (b'Z', b'I'), response
        result['after_simple_error'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (20)', b'INSERT 0 1')
        assert ids(observer) == [1, 4]
        writer.ok('SELECT 20', [['20']])
        result['after_single_simple_boundary'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (21)', b'INSERT 0 1')
        frames = writer.query('SELECT 21; INSERT INTO sync_fixture VALUES (22); SELECT 22')
        assert [tag for tag, _ in frames] == [b'T', b'D', b'C', b'C', b'T', b'D', b'C', b'Z'], frames
        assert rows(frames) == [['21'], ['22']] and frames[-1] == (b'Z', b'I'), frames
        result['after_simple_batch_boundary'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (23)', b'INSERT 0 1')
        frames = writer.query('INSERT INTO sync_fixture VALUES (24); SELECT 1/0')
        assert [error['C'] for error in errors(frames)] == ['22012'] and frames[-1] == (b'Z', b'I'), frames
        result['after_cross_protocol_batch_error'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (25)', b'INSERT 0 1')
        frames = writer.query('COMMIT; INSERT INTO sync_fixture VALUES (26); SELECT 1/0')
        assert [error['C'] for error in errors(frames)] == ['22012'] and frames[-1] == (b'Z', b'I'), frames
        result['after_cross_protocol_commit_and_error'] = ids(observer)

        extended(writer, 'INSERT INTO sync_fixture VALUES (8)', b'INSERT 0 1')
        writer.close()
        result['after_disconnect'] = ids(observer)
        return result
    finally:
        observer.close()
        if writer.sock.fileno() != -1:
            writer.close()


EXPECTED = {
    'before_sync': [], 'after_sync': [1], 'after_group_error': [1],
    'after_malformed_sync': [1], 'after_valid_sync': [1], 'explicit_before_commit': [1],
    'explicit_after_commit': [1, 4], 'failed_begin_promotion': [1, 4],
    'after_simple_error': [1, 4], 'after_single_simple_boundary': [1, 4, 20],
    'after_simple_batch_boundary': [1, 4, 20, 21, 22],
    'after_cross_protocol_batch_error': [1, 4, 20, 21, 22],
    'after_cross_protocol_commit_and_error': [1, 4, 20, 21, 22, 25],
    'after_disconnect': [1, 4, 20, 21, 22, 25],
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    native = parser.add_mutually_exclusive_group(required=True)
    native.add_argument('--postgres-bin', type=Path)
    native.add_argument('--postgres-port', type=int, help='loopback port of the isolated ramsql PostgreSQL test database')
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    directory = args.report.resolve().parent
    directory.mkdir(parents=True, exist_ok=True)
    report = {'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'candidate': {}}
    reference = local_postgres(args.postgres_bin, psycopg, directory) if args.postgres_bin else nullcontext((
        dict(host='127.0.0.1', port=args.postgres_port, user='ramsql', dbname='ramsql', sslmode='disable'), {},
    ))
    try:
        with reference as (parameters, _metadata):
            with psycopg.connect(**parameters) as db:
                report['postgres'] = db.execute('SELECT version()').fetchone()[0]
            report['native'] = probe(parameters['port'])
            assert report['native'] == EXPECTED, report['native']
        for workers in (1, 4):
            options = ['--workers', str(workers)]
            with running_ramsql(binary, extra_arguments=options) as parameters:
                observed = probe(parameters['port'])
                assert observed == EXPECTED, observed
                report['candidate'][f'memory-{workers}'] = observed
            wal = directory / f'extended-{workers}.wal'
            with running_ramsql(binary, extra_arguments=options, data_path=wal, create_data=True) as parameters:
                observed = probe(parameters['port'])
                assert observed == EXPECTED, observed
                report['candidate'][f'durable-{workers}'] = observed
            with running_ramsql(binary, extra_arguments=options, data_path=wal) as parameters:
                reader = connect(parameters['port'])
                try:
                    recovered = ids(reader)
                    assert recovered == EXPECTED['after_disconnect'], recovered
                    report['candidate'][f'reopened-{workers}'] = recovered
                finally:
                    reader.close()
    finally:
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS extended Sync, error, disconnect and WAL recovery on one and four workers')


if __name__ == '__main__':
    main()
