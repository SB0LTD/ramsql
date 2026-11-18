"""Qualify optional Linux worker placement through real sockets and OS affinity."""
import argparse
import json
import os
from pathlib import Path
import struct
import subprocess
import time

from test_server import (PG, Server, test_detached_relation_locks, test_fragmented_and_coalesced,
                         test_half_close_and_partial_reads, test_parameterized_primary_key,
                         test_pool_exhaustion, test_slow_reader_fairness, test_transactions_and_disconnect)
from test_workers import cancel_during_work


FEATURE = '--linux-worker-locality'
WORKERS = 4


def affinities(server):
    parent = server.process.pid
    tasks = Path(f'/proc/{parent}/task')
    workers = sorted(int(task.name) for task in tasks.iterdir() if int(task.name) != parent)
    return [sorted(os.sched_getaffinity(worker)) for worker in workers]


def cpu_ticks(pid):
    return sum(sum(map(int, (thread / 'stat').read_text().rsplit(')', 1)[1].split()[11:13]))
               for thread in Path(f'/proc/{pid}/task').iterdir())


def startup_lane(server):
    pg = PG(server.raw())
    try:
        frames = pg.startup()
        keys = [struct.unpack('!II', body) for tag, body in frames if tag == b'K']
        assert len(keys) == 1, frames
        pg.ok('SELECT 1', [['1']])
        return (keys[0][0] - 1) % WORKERS
    finally:
        pg.close()


def core(cpu):
    return (Path(f'/sys/devices/system/cpu/cpu{cpu}/topology') / 'thread_siblings_list').read_text().strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    allowed = os.sched_getaffinity(0)
    assert len(allowed) >= WORKERS and len({core(cpu) for cpu in allowed}) > 1
    base = ('--workers', str(WORKERS))
    result = {}
    with Server(binary, extra_arguments=base) as vanilla:
        assert startup_lane(vanilla) == 0
        masks = affinities(vanilla)
        assert masks == [sorted(allowed)] * WORKERS, masks
        result['default_worker_masks'] = masks
    with Server(binary, extra_arguments=(*base, FEATURE)) as pinned:
        masks = []
        for _ in range(300):
            masks = affinities(pinned)
            if len(masks) == WORKERS and all(len(mask) == 1 for mask in masks):
                break
            time.sleep(.01)
        assert len(masks) == WORKERS and all(len(mask) == 1 for mask in masks), masks
        chosen = [mask[0] for mask in masks]
        assert len(set(chosen)) == WORKERS and set(chosen) <= allowed, masks
        assert core(chosen[0]) != core(chosen[1]), chosen
        result['pinned_worker_masks'] = masks
        try:
            os.sched_setaffinity(0, {chosen[1]})
            lane = startup_lane(pinned)
        finally:
            os.sched_setaffinity(0, allowed)
        assert lane != 0 and core(chosen[lane]) == core(chosen[1]), (lane, chosen)
        result['same_core_startup_lane'] = lane
        pg = PG(pinned.raw())
        try:
            pg.startup()
            before = cpu_ticks(pinned.process.pid)
            time.sleep(.3)
            idle_ticks = cpu_ticks(pinned.process.pid) - before
            assert idle_ticks <= max(2, int(os.sysconf('SC_CLK_TCK') * .3 * .20)), idle_ticks
            pg.ok('SELECT 1', [['1']])
        finally:
            pg.close()
        result['idle_cpu_ticks'] = idle_ticks
        covered = []
        for check in (test_parameterized_primary_key, test_detached_relation_locks,
                      test_fragmented_and_coalesced, test_half_close_and_partial_reads,
                      test_transactions_and_disconnect, test_slow_reader_fairness, test_pool_exhaustion):
            check(pinned)
            covered.append(check.__name__)
        result['opt_in_socket_cases'] = covered
    cancel_during_work(binary, (FEATURE,))
    result['opt_in_running_cancel'] = True
    bad = subprocess.run(['taskset', '-c', str(min(allowed)), str(binary), '--serve',
                          '--memory', '--insecure-local', '--workers', '2', FEATURE],
                         capture_output=True, text=True, timeout=8)
    assert bad.returncode and 'WorkerAffinityUnavailable' in bad.stderr and 'RAMSQL postgres' not in bad.stdout, bad
    result['disallowed_affinity_failed_before_listen'] = True
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    print('PASS Linux worker affinity, startup locality, idle sleep and ordinary PostgreSQL socket contracts')


if __name__ == '__main__':
    main()
