"""Count syscalls in an owned Linux server; tracing timings are not throughput."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--strace', type=Path, required=True)
    parser.add_argument('--iterations', type=int, default=10000)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error('iterations must be positive')
    import psycopg
    binary = args.binary.resolve(strict=True)
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / 'stderr.log').open('w') as errors:
        process = subprocess.Popen(
            [str(args.strace.resolve(strict=True)), '-f', '--kill-on-exit', '-c', '-w',
             '-o', str(folder / 'summary.txt'), str(binary), '--serve', '--memory',
             '--insecure-local', '--port', '0'], stdout=subprocess.PIPE,
            stderr=errors, text=True, start_new_session=True)
        try:
            lines = queue.Queue()
            threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
            line = lines.get(timeout=20)
            match = re.fullmatch(r'RAMSQL postgres 127\.0\.0\.1:(\d+)\n', line)
            assert match, repr(line)
            children = Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()
            assert len(children) == 1, 'expected one owned tracee'
            child = int(children[0])
            with psycopg.connect(host='127.0.0.1', port=int(match[1]), user='ramsql',
                                  dbname='ramsql', autocommit=True, sslmode='disable') as db:
                db.execute('CREATE TABLE bench (id INT PRIMARY KEY, value INT)')
                db.execute('INSERT INTO bench VALUES ' + ','.join(f'({i},{64-i})' for i in range(64)))
                began = time.perf_counter_ns()
                for _ in range(args.iterations):
                    assert db.execute('SELECT id,value FROM bench WHERE id=31', prepare=False).fetchall() == [(31,33)]
                elapsed = time.perf_counter_ns() - began
            os.kill(child, signal.SIGTERM)
            process.wait(timeout=10)
            assert process.returncode in (0, 128 + signal.SIGTERM, -signal.SIGTERM), process.returncode
            (folder / 'metadata.json').write_text(json.dumps({
                'queries': args.iterations, 'profiled_wall_ns': elapsed,
                'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                'strace_sha256': hashlib.sha256(args.strace.read_bytes()).hexdigest(),
                'passed': True,
                'scope': 'Syscall totals include startup, 64-row seed, connection setup and shutdown. '
                         'Wall time contains tracing overhead; not comparative throughput.'}, indent=2) + '\n')
            print((folder / 'summary.txt').read_text(), flush=True)
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            process.stdout.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
