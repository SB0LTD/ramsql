"""Compare one PostgreSQL simple Query frame at a time with native PostgreSQL."""
import argparse
from contextlib import contextmanager, nullcontext
import hashlib
import json
from pathlib import Path
import socket
import time

from test_server import PG, Server, errors, packet, query_packet, rows
from test_persistence import DurableServer


@contextmanager
def session(port):
    # Negotiate TCP receive-window scaling before the handshake. A small
    # post-connect window makes the later bulk drain dependent on tiny window
    # updates and can outlast a valid idle-in-transaction deadline.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 128 * 1024)
        sock.settimeout(8)
        sock.connect(('127.0.0.1', port))
        client = PG(sock)
        client.startup()
        yield client
    finally:
        sock.close()


def result(frames):
    notices = []
    for tag, body in frames:
        if tag == b'N':
            notices.extend(field[1:].decode() for field in body.split(b'\0') if field[:1] == b'C')
    return {
        'frames': b''.join(tag for tag, _ in frames).decode(),
        'status': frames[-1][1].decode(),
        'completion': [body[:-1].decode() for tag, body in frames if tag == b'C'],
        'rows': rows(frames),
        'errors': [item['C'] for item in errors(frames)],
        'notices': notices,
    }


def scenarios(port, workers):
    table = f'batch_rows_{workers}'
    out = {}
    with session(port) as client, session(port) as peer:
        def query(name, source, connection=client):
            out[name] = result(connection.query(source))

        client.ok(f'CREATE TABLE {table} (id INTEGER)')
        query('two_sets_of_rows', "SELECT ';literal';; SELECT $$dollar;body$$;")
        query('empty_comments', '/* first; /* nested; */ */ ; -- tail;')
        query('cr_line_comments', 'SELECT 4; -- outside;\r SELECT 5;')
        query('only_one_command_with_empty_neighbors', '; SELECT 10;; -- tail;')
        query('cursor_within_implicit_frame', 'DECLARE c CURSOR FOR SELECT 12; FETCH ALL FROM c;')
        query('implicit_transaction_modes', 'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ; SHOW transaction_isolation')
        query('rejected_promotion_leaves_session_idle', 'SELECT 1; BEGIN ISOLATION LEVEL REPEATABLE READ; SHOW transaction_isolation')
        prefix, suffix = 'SELECT 4; /*', '*/ SELECT 5;'
        query('maximum_accepted_frame', prefix + ' ' * (65530 - len(prefix) - len(suffix)) + suffix)
        query('raw_parse_error_before_any_effect', f'INSERT INTO {table} VALUES(7); COMMIT; SELECT 1 +;')
        query('proof_of_no_raw_parse_effects', f'SELECT COUNT(*) FROM {table}', peer)
        query('implicit_failure', f'INSERT INTO {table} VALUES(1); SELECT 1/0; INSERT INTO {table} VALUES(2);')
        query('implicit_failure_after', f'SELECT COUNT(*) FROM {table}', peer)
        query('explicit_commit_boundary', f'BEGIN; INSERT INTO {table} VALUES(1); COMMIT; INSERT INTO {table} VALUES(2); SELECT 1/0')
        query('explicit_commit_proof', f'SELECT id FROM {table} ORDER BY id', peer)
        query('runtime_resolution_after_explicit_commit', f'BEGIN; INSERT INTO {table} VALUES(9); COMMIT; SELECT does_not_exist(1)')
        query('runtime_resolution_did_not_undo_commit', f'SELECT id FROM {table} ORDER BY id', peer)
        query('promote_implicit', f'INSERT INTO {table} VALUES(3); BEGIN; INSERT INTO {table} VALUES(4);')
        query('peer_cannot_see_promoted_writes', f'SELECT id FROM {table} ORDER BY id', peer)
        query('explicit_transaction_stops_before_rollback', 'SELECT 1/0; ROLLBACK;')
        query('explicit_failed_state', 'SELECT 1')
        query('explicit_recovery', 'ROLLBACK')
        query('implicit_savepoint_prohibited', f'INSERT INTO {table} VALUES(5); SAVEPOINT blocked; SELECT 1;')
        query('implicit_rollback_boundary', f'INSERT INTO {table} VALUES(6); ROLLBACK; INSERT INTO {table} VALUES(8)')
        query('implicit_rollback_boundary_proof', f'SELECT id FROM {table} ORDER BY id', peer)
        query('implicit_set_local', "SET application_name='batch_base'; SET LOCAL application_name='batch_local'; SELECT current_setting('application_name');")
        query('implicit_set_local_expired', "SELECT current_setting('application_name')")
        client.ok('BEGIN', status=b'T')
        query('syntax_failure_fails_existing_block', f'INSERT INTO {table} VALUES(11); SELECT 2 +;')
        query('existing_block_remains_failed', 'SELECT 1')
        query('existing_block_rollback', 'ROLLBACK')

    assert out['two_sets_of_rows']['frames'] == 'TDCTDCZ'
    assert out['empty_comments']['frames'] == 'IZ'
    assert out['cursor_within_implicit_frame']['rows'] == [['12']]
    assert out['implicit_transaction_modes']['rows'] == [['repeatable read']]
    assert out['rejected_promotion_leaves_session_idle']['errors'] == ['25001']
    assert out['rejected_promotion_leaves_session_idle']['status'] == 'I'
    assert out['maximum_accepted_frame']['rows'] == [['4'], ['5']]
    assert out['raw_parse_error_before_any_effect']['frames'] == 'EZ'
    assert out['proof_of_no_raw_parse_effects']['rows'] == [['0']]
    assert out['implicit_failure']['errors'] == ['22012'] and out['implicit_failure_after']['rows'] == [['0']]
    assert out['explicit_commit_proof']['rows'] == [['1']]
    assert out['runtime_resolution_after_explicit_commit']['errors'] == ['42883']
    assert out['runtime_resolution_did_not_undo_commit']['rows'] == [['1'], ['9']]
    assert out['promote_implicit']['status'] == 'T'
    assert out['peer_cannot_see_promoted_writes']['rows'] == [['1'], ['9']]
    assert out['explicit_transaction_stops_before_rollback']['status'] == 'E'
    assert out['explicit_failed_state']['errors'] == ['25P02']
    assert out['implicit_savepoint_prohibited']['errors'] == ['25P01']
    assert out['implicit_rollback_boundary']['notices'] == ['25P01']
    assert out['implicit_rollback_boundary_proof']['rows'] == [['1'], ['8'], ['9']]
    assert out['implicit_set_local']['rows'] == [['batch_local']]
    assert out['implicit_set_local_expired']['rows'] == [['batch_base']]
    assert out['syntax_failure_fails_existing_block']['status'] == 'E'
    return out


def pressure(port, *, idle_timeout=False, native=False):
    commands = 896 if idle_timeout else 68
    row_count = 350
    idle_seconds = 6
    pause_seconds = idle_seconds + 0.25 if idle_timeout else 0
    query = f"SELECT repeat('x', 96) FROM generate_series(1,{row_count});"
    assert len(query_packet(query * commands)) < 65536
    with session(port) as client, (session(port) if native and idle_timeout else nullcontext()) as observer:
        if idle_timeout:
            pid = int(rows(client.ok('SELECT pg_backend_pid()'))[0][0])
            client.ok(f'SET idle_in_transaction_session_timeout = {idle_seconds * 1000}')
            client.ok('BEGIN', status=b'T')
        client.sock.sendall(query_packet(query * commands) + query_packet('SELECT 999'))
        assert client.message()[0] == b'T'
        if idle_timeout:
            # More than 32 MiB of output exceeds the negotiated receive
            # window and the hosted Linux sender buffers. The independent
            # observer below must prove ClientWrite after a whole idle
            # timeout has elapsed; a completed Query is not backpressure.
            time.sleep(pause_seconds)
            if observer is not None:
                observer.ok('SELECT pg_stat_clear_snapshot()')
                state = rows(observer.ok(
                    f'SELECT state, wait_event FROM pg_stat_activity WHERE pid={pid}'))
                assert state == [['active', 'ClientWrite']], state
        draining = time.monotonic()
        for index in range(commands):
            if index != 0:
                assert client.message()[0] == b'T'
            for _ in range(row_count):
                data_row = client.message()
                assert rows([data_row]) == [['x' * 96]]
            assert client.message() == (b'C', f'SELECT {row_count}\0'.encode())
        status = b'T' if idle_timeout else b'I'
        assert client.message() == (b'Z', status)
        next_frame = client.ready()
        assert rows(next_frame) == [['999']] and next_frame[-1] == (b'Z', status)
        ready_at = time.monotonic()
        drain_seconds = ready_at - draining
        if idle_timeout:
            # Output may finish before the client reads ReadyForQuery.
            # Complete the drain well inside the new idle period.
            assert drain_seconds < idle_seconds / 2, drain_seconds
            kind, payload = client.message()
            assert kind == b'E', (kind, payload)
            fatal, = errors([(kind, payload)])
            assert fatal['C'] == '25P03' and fatal['V'] == 'FATAL', fatal
            fatal_seconds = time.monotonic() - ready_at
            assert idle_seconds - drain_seconds - 0.25 < fatal_seconds < idle_seconds + 2, fatal_seconds
            try:
                client.message()
            except (EOFError, ConnectionResetError):
                pass
            else:
                raise AssertionError('backend returned data after idle timeout FATAL')
        else:
            fatal_seconds = None
    return {'commands': commands, 'data_rows': commands * row_count, 'queued_next_frame_intact': True,
            'idle_expired_after_ready': idle_timeout,
            'observation': {'read_pause_seconds': pause_seconds, 'drain_seconds': drain_seconds,
                            'fatal_after_ready_seconds': fatal_seconds}}


def pressure_contract(report):
    return {key: value for key, value in report.items() if key != 'observation'}


def parked(port, workers, native=False):
    target = f'batch_locked_{workers}'
    audit = f'batch_commits_{workers}'
    with session(port) as client, session(port) as peer:
        client.ok(f'CREATE TABLE {target} (id INTEGER PRIMARY KEY, v INTEGER)')
        client.ok(f'CREATE TABLE {audit} (id INTEGER PRIMARY KEY)')
        client.ok(f'INSERT INTO {target} VALUES (1,10)')
        pid = int(rows(client.ok('SELECT pg_backend_pid()'))[0][0])
        peer.ok('BEGIN', status=b'T')
        peer.ok(f'LOCK TABLE {target} IN ACCESS EXCLUSIVE MODE', status=b'T')
        client.sock.sendall(query_packet(f'INSERT INTO {audit} VALUES (8); UPDATE {target} SET v=20 WHERE id=1; SELECT v FROM {target};'))
        first = []
        if native:
            # Native PostgreSQL can buffer the first CommandComplete until
            # the lock is released. Observe its actual backend wait instead.
            deadline = time.monotonic() + 5
            while True:
                peer.ok('SELECT pg_stat_clear_snapshot()', status=b'T')
                wait = rows(peer.ok(f'SELECT wait_event_type FROM pg_stat_activity WHERE pid={pid}', status=b'T'))
                if wait == [['Lock']]:
                    break
                assert time.monotonic() < deadline, wait
                time.sleep(0.005)
        else:
            first.append(client.message())
            assert first == [(b'C', b'INSERT 0 1\0')]
        peer.ok(f'SELECT COUNT(*) FROM {audit}', [['0']], status=b'T')
        peer.ok('COMMIT')
        final = result(first + client.ready())
        assert final['frames'] == 'CCTDCZ' and final['rows'] == [['20']], final
        peer.ok(f'SELECT id FROM {audit}', [['8']])
        if native:
            return {'resumed_once': True, 'invisible_until_commit': True}

        peer.ok('BEGIN', status=b'T')
        peer.ok(f'LOCK TABLE {target} IN ACCESS EXCLUSIVE MODE', status=b'T')
        with session(port) as abandoning:
            abandoning.sock.sendall(query_packet(f'INSERT INTO {audit} VALUES (80); UPDATE {target} SET v=99 WHERE id=1;'))
            assert abandoning.message() == (b'C', b'INSERT 0 1\0')
        # Acquire the abandoned key while the blocking relation lock still
        # prevents the abandoned batch from committing its first command.
        peer.ok(f'INSERT INTO {audit} VALUES (80)', status=b'T')
        peer.ok('ROLLBACK')
        peer.ok(f'SELECT id FROM {audit}', [['8']])
        peer.ok(f'SELECT v FROM {target}', [['20']])
    return {'resumed_once': True, 'invisible_until_commit': True, 'disconnect_rolled_back': True}


def copy_scope(port, workers):
    table = f'batch_copy_{workers}'
    with session(port) as client, session(port) as peer:
        client.ok(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')

        def send(source):
            client.sock.sendall(query_packet(source))

        def complete_copy(data, following=b''):
            client.sock.sendall(packet(b'd', data) + packet(b'c') + following)

        def expect_copy_in():
            tag, body = client.message()
            assert tag == b'G' and body == b'\0\0\1\0\0', (tag, body)

        send(f"INSERT INTO {table} VALUES (1); COPY {table} FROM STDIN; SELECT SUM(id) FROM {table}; COPY {table} FROM STDIN; SELECT SUM(id) FROM {table}")
        assert client.message() == (b'C', b'INSERT 0 1\0')
        expect_copy_in()
        peer.ok(f'SELECT COUNT(*) FROM {table}', [['0']])
        client.sock.sendall(packet(b'd', b'4') + packet(b'd', b'\n') + packet(b'c'))
        assert client.message() == (b'C', b'COPY 1\0')
        first_result = [client.message() for _ in range(3)]
        assert rows(first_result) == [['5']] and first_result[-1] == (b'C', b'SELECT 1\0')
        expect_copy_in()
        peer.ok(f'SELECT COUNT(*) FROM {table}', [['0']])
        complete_copy(b'8\n', query_packet('SELECT 999'))
        final_copy = result(client.ready())
        assert final_copy['frames'] == 'CTDCZ' and final_copy['rows'] == [['13']], final_copy
        assert rows(client.ready()) == [['999']]
        peer.ok(f'SELECT id FROM {table} ORDER BY id', [['1'], ['4'], ['8']])

        send(f"INSERT INTO {table} VALUES (20); COPY {table} FROM STDIN; INSERT INTO {table} VALUES (21)")
        assert client.message() == (b'C', b'INSERT 0 1\0')
        expect_copy_in()
        client.sock.sendall(packet(b'f', b'reviewer stopped upload\0'))
        failed = result(client.ready())
        assert failed['errors'] == ['57014'] and failed['status'] == 'I', failed
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id >= 20', [['0']])

        send(f"INSERT INTO {table} VALUES (30); COPY {table} FROM STDIN; INSERT INTO {table} VALUES (31)")
        assert client.message() == (b'C', b'INSERT 0 1\0')
        expect_copy_in()
        complete_copy(b'invalid integer\n', query_packet('SELECT 123'))
        malformed = result(client.ready())
        assert malformed['errors'] == ['22P02'] and malformed['status'] == 'I', malformed
        assert rows(client.ready()) == [['123']]
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id >= 30', [['0']])

        send(f"BEGIN; INSERT INTO {table} VALUES (40); COPY {table} FROM STDIN")
        assert client.message() == (b'C', b'BEGIN\0')
        assert client.message() == (b'C', b'INSERT 0 1\0')
        expect_copy_in()
        complete_copy(b'41\n')
        explicit = result(client.ready())
        assert explicit['status'] == 'T' and explicit['completion'] == ['COPY 1'], explicit
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id >= 40', [['0']])
        client.ok('COMMIT')
        peer.ok(f'SELECT id FROM {table} WHERE id >= 40 ORDER BY id', [['40'], ['41']])

        syntax = result(client.query(f"INSERT INTO {table} VALUES (50); COPY {table} FROM STDIN; SELECT 1 +"))
        assert syntax['frames'] == 'EZ' and syntax['errors'] == ['42601'], syntax
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id = 50', [['0']])

        client.ok('SET idle_in_transaction_session_timeout = 600')
        send(f"BEGIN; COPY {table} FROM STDIN; SELECT id FROM {table} WHERE id=60")
        assert client.message() == (b'C', b'BEGIN\0')
        expect_copy_in()
        complete_copy(b'60\n')
        idle = result(client.ready())
        assert idle['status'] == 'T' and idle['rows'] == [['60']], idle
        timeout_tag, timeout_response = client.message()
        assert timeout_tag == b'E' and errors([(timeout_tag, timeout_response)])[0]['C'] == '25P03'
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id = 60', [['0']])

    return {'continued_twice': final_copy, 'copy_fail': failed, 'malformed_row': malformed,
            'explicit_final_copy': explicit, 'raw_syntax_failure': syntax, 'idle_armed_after_owned_tail': idle}


def copy_out_batch(port, workers):
    table = f'batch_copy_out_{workers}'
    with session(port) as client, session(port) as peer:
        client.ok(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY)')

        def exchange(sql):
            frames = client.query(sql)
            facts = result(frames)
            facts['copy_output'] = b''.join(body for tag, body in frames if tag == b'd').hex()
            facts['frames'] = facts['frames'].replace('d', '')
            return facts

        csv = exchange(f"INSERT INTO {table} VALUES(4); COPY (SELECT id, 'from'::text AS marker FROM {table}) TO STDOUT WITH (FORMAT csv, HEADER true); SELECT COUNT(*) FROM {table}")
        assert csv['frames'] == 'CHcCTDCZ' and csv['completion'] == ['INSERT 0 1', 'COPY 1', 'SELECT 1'], csv
        assert csv['rows'] == [['1']] and bytes.fromhex(csv['copy_output']) == b'id,marker\n4,from\n', csv
        peer.ok(f'SELECT id FROM {table}', [['4']])

        binary = exchange(f"INSERT INTO {table} VALUES(7); COPY (SELECT id FROM {table} ORDER BY id) TO STDOUT WITH (FORMAT binary)")
        assert binary['frames'] == 'CHcCZ' and binary['completion'] == ['INSERT 0 1', 'COPY 2'], binary
        assert bytes.fromhex(binary['copy_output']).startswith(b'PGCOPY\n\xff\r\n\x00'), binary
        peer.ok(f'SELECT id FROM {table} ORDER BY id', [['4'], ['7']])

        runtime_error = exchange(f"INSERT INTO {table} VALUES(8); COPY (SELECT id FROM {table} WHERE id=8) TO STDOUT; SELECT 1/0")
        assert runtime_error['frames'] == 'CHcCEZ' and runtime_error['errors'] == ['22012'], runtime_error
        assert bytes.fromhex(runtime_error['copy_output']) == b'8\n', runtime_error
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id=8', [['0']])

        syntax_error = exchange(f"INSERT INTO {table} VALUES(9); COPY {table} TO STDOUT; SELECT 1 +")
        assert syntax_error['frames'] == 'EZ' and syntax_error['errors'] == ['42601'], syntax_error
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id=9', [['0']])

        explicit = exchange(f"BEGIN; INSERT INTO {table} VALUES(10); COPY (SELECT id FROM {table} WHERE id=10) TO STDOUT; SELECT 1")
        assert explicit['status'] == 'T' and bytes.fromhex(explicit['copy_output']) == b'10\n', explicit
        peer.ok(f'SELECT COUNT(*) FROM {table} WHERE id=10', [['0']])
        client.ok('ROLLBACK')
        peer.ok(f'SELECT id FROM {table} ORDER BY id', [['4'], ['7']])
    return {'csv_in_middle': csv, 'binary_at_end': binary, 'runtime_failure': runtime_error,
            'raw_syntax_failure': syntax_error, 'explicit_rollback': explicit}


def durable(binary, evidence_dir, workers):
    wal = (evidence_dir / f'qualified-batch-{workers}.wal').resolve()
    assert not wal.exists(), wal
    runtime = ('--workers', str(workers))
    with DurableServer(binary, wal, create=True, storage_options=runtime) as server:
        with session(server.port) as client:
            client.ok('CREATE TABLE replay_batch (id INTEGER PRIMARY KEY)')
            implicit = result(client.query('INSERT INTO replay_batch VALUES(1); INSERT INTO replay_batch VALUES(2)'))
            assert implicit['frames'] == 'CCZ' and implicit['status'] == 'I', implicit
            split = result(client.query('BEGIN; INSERT INTO replay_batch VALUES(3); COMMIT; INSERT INTO replay_batch VALUES(4); SELECT 1/0'))
            assert split['errors'] == ['22012'] and split['status'] == 'I', split
            open_block = result(client.query('BEGIN; INSERT INTO replay_batch VALUES(5)'))
            assert open_block['status'] == 'T' and not open_block['errors'], open_block
            server.crash()
    with DurableServer(binary, wal, storage_options=runtime) as restarted:
        with session(restarted.port) as client:
            recovered = rows(client.ok('SELECT id FROM replay_batch ORDER BY id'))
            assert recovered == [['1'], ['2'], ['3']], recovered
    return {'recovered_values': recovered, 'wal_sha256': hashlib.sha256(wal.read_bytes()).hexdigest(),
            'wal_bytes': wal.stat().st_size, 'killed_with_open_transaction': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--postgres-port', type=int, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve()
    with session(args.postgres_port) as oracle:
        version = rows(oracle.query('SHOW server_version'))[0][0]
    assert version.startswith('18.3'), version
    evidence = {'reference_version': version, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'modes': []}
    for workers in (1, 4):
        native = scenarios(args.postgres_port, workers)
        copy_native = copy_scope(args.postgres_port, workers)
        native_copy_out = copy_out_batch(args.postgres_port, workers)
        native_park = parked(args.postgres_port, workers, native=True)
        native_pressure = pressure(args.postgres_port)
        native_idle_pressure = pressure(args.postgres_port, idle_timeout=True, native=True)
        Server.test_workers = workers
        with Server(binary, max_clients=8, extra_arguments=('--work-rows', '1024', '--result-rows', '512')) as candidate:
            actual = scenarios(candidate.port, workers)
            for name, expected in native.items():
                assert actual[name] == expected, (workers, name, actual[name], expected)
            copy_test = copy_scope(candidate.port, workers)
            assert copy_test == copy_native, (workers, copy_test, copy_native)
            actual_copy_out = copy_out_batch(candidate.port, workers)
            assert actual_copy_out == native_copy_out, (workers, actual_copy_out, native_copy_out)
            lock_test = parked(candidate.port, workers)
            assert all(lock_test[name] == expected for name, expected in native_park.items())
            pressure_test = pressure(candidate.port)
            idle_pressure = pressure(candidate.port, idle_timeout=True)
            assert pressure_contract(pressure_test) == pressure_contract(native_pressure)
            assert pressure_contract(idle_pressure) == pressure_contract(native_idle_pressure)
        evidence['modes'].append({'workers': workers, 'ordinary': actual, 'native_copy': copy_native,
                                  'candidate_copy': copy_test, 'copy_out_batch': actual_copy_out, 'lock_wait_and_disconnect': lock_test,
                                  'native_pressure': native_pressure, 'native_active_batch_idle_timeout': native_idle_pressure,
                                  'pressure': pressure_test, 'active_batch_idle_timeout': idle_pressure, 'durability': durable(binary, args.report.parent, workers)})
    args.report.write_text(json.dumps(evidence, indent=2) + '\n')
    print(f'PASS {sum(len(case["ordinary"]) for case in evidence["modes"])} frame differentials, wraparound I/O, COPY scope and blocked continuation')


if __name__ == '__main__':
    main()
