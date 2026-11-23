"""Verify the real Linux wait syscall and ordinary PostgreSQL reactor behavior."""
import argparse
import json
from pathlib import Path
import platform
import struct
import time

from test_server import (PG, Server, test_detached_relation_locks, test_fragmented_and_coalesced,
                         test_half_close_and_partial_reads, test_parameterized_primary_key,
                         test_pool_exhaustion, test_slow_reader_fairness)
from test_workers import cancel_during_work


FEATURE = '--linux-direct-receive'
WORKERS = ('--workers', '4')
# Native kernel syscall numbers used by the x86-64 test runner.
POLL = {'7', '271'}
RECVFROM = {'45'}


def start(server):
    session = PG(server.raw())
    try:
        frames = session.startup()
        keys = [struct.unpack('!II', body) for tag, body in frames if tag == b'K']
        assert len(keys) == 1, frames
        return session, keys[0][0]
    except BaseException:
        session.close()
        raise


def verify_worker_wait(server, backend_key, expected):
    pid = server.process.pid
    tasks = Path(f'/proc/{pid}/task')
    workers = sorted(int(task.name) for task in tasks.iterdir() if int(task.name) != pid)
    assert len(workers) == 4, workers
    reactor = (backend_key - 1) % len(workers)
    for _ in range(200):
        fields = (tasks / str(workers[reactor]) / 'syscall').read_text().split()
        if fields and fields[0] in expected:
            return {'reactor': reactor, 'syscall': 'recvfrom' if fields[0] in RECVFROM else 'poll'}
        time.sleep(.002)
    raise AssertionError(f'Reactor {reactor} never slept in the expected kernel call')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    assert platform.machine() == 'x86_64'
    binary = args.binary.resolve(strict=True)
    report = {}
    with Server(binary, extra_arguments=WORKERS) as ordinary:
        client, pid = start(ordinary)
        try:
            report['ordinary_single_client'] = verify_worker_wait(ordinary, pid, POLL)
        finally:
            client.close()
    with Server(binary, extra_arguments=(*WORKERS, FEATURE)) as server:
        first, pid = start(server)
        clients = [first]
        try:
            first.ok('SELECT 1', [['1']])
            report['candidate_single_client'] = verify_worker_wait(server, pid, RECVFROM)
            first.ok('LISTEN direct_wake')
            notifier, _ = start(server)
            clients.append(notifier)
            verify_worker_wait(server, pid, POLL)
            notifier.ok("NOTIFY direct_wake, 'arrived'")
            tag, body = first.message()
            assert tag == b'A' and body[4:] == b'direct_wake\0arrived\0', (tag, body)
            report['asynchronous_notification'] = True
            first.ok('UNLISTEN *')
            verify_worker_wait(server, pid, RECVFROM)
            for _ in range(3):
                client, key = start(server)
                clients.append(client)
            assert (key - 1) % 4 == (pid - 1) % 4
            report['candidate_shared_reactor'] = verify_worker_wait(server, pid, POLL)
            clients[-1].ok('SELECT 2', [['2']])
            first.ok('SELECT 3', [['3']])
        finally:
            for connection in clients:
                connection.close()
    with Server(binary, extra_arguments=(*WORKERS, FEATURE)) as server:
        tests = (test_parameterized_primary_key, test_detached_relation_locks,
                 test_fragmented_and_coalesced, test_half_close_and_partial_reads,
                 test_slow_reader_fairness, test_pool_exhaustion)
        for case in tests:
            case(server)
        report['live_socket_cases'] = [case.__name__ for case in tests]
    cancel_during_work(binary, (FEATURE,))
    report['running_cancel'] = True
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('PASS real timed receive, shared-worker poll fallback, notification and cancellation')


if __name__ == '__main__':
    main()
