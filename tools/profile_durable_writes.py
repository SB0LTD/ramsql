"""Profile a verified Linux RAMSQL binary on an owned synthetic database."""

from __future__ import annotations

import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import gzip
import json
import os
from pathlib import Path
import platform
import re
from threading import Barrier
import time
import uuid

from benchmark import cpu_name
from benchmark_postgres import command, execute, percentile, require, sha256, source_fingerprint
from test_persistence import last_sequence
from test_postgres_compat import running_ramsql


ROWS = 512
WRITER = "update"
READER = "read"
TRACE_CALLS = ("lseek", "write", "fsync", "fdatasync")
TRACE_LINE = re.compile(
    r"^\s*(?:\d+\s+)?(?P<start>\d+\.\d+)\s+"
    r"(?P<call>lseek|write|fsync|fdatasync)\(\d+<(?P<path>[^>]+)>.*\)\s+"
    r"=\s+(?P<result>-?\d+)(?:\s+.*?)?\s+<(?P<duration>\d+\.\d+)>$"
)
TRACE_SIGNAL = re.compile(r"^\s*(?:\d+\s+)?\d+\.\d+\s+(?:---|\+\+\+)")


def nanos(seconds: str) -> int:
    whole, rest = seconds.split(".")
    return int(whole) * 1_000_000_000 + int(rest[:9].ljust(9, "0"))


def distribution(samples: list[int]) -> dict | None:
    if not samples:
        return None
    return {"count": len(samples), "sum_ns": sum(samples), "p50_ns": percentile(samples, 0.50),
            "p95_ns": percentile(samples, 0.95), "p99_ns": percentile(samples, 0.99), "max_ns": max(samples)}


def parse_trace(lines, wal: Path, from_ns: int, until_ns: int) -> list[tuple[int, int, str, int]]:
    calls = []
    for line in lines:
        match = TRACE_LINE.fullmatch(line.strip())
        if not match:
            require(not line.strip() or TRACE_SIGNAL.match(line), f"unexpected WAL trace line: {line!r}")
            continue
        require(match["path"] == str(wal), f"unexpected WAL trace file: {match['path']}")
        start = nanos(match["start"])
        if from_ns <= start <= until_ns:
            duration = nanos(match["duration"])
            result = int(match["result"])
            require(result >= 0 and start + duration <= until_ns, f"failed or out-of-window WAL call: {line!r}")
            calls.append((start, duration, match["call"], result))
    return sorted(calls)


def trace_summary(calls, commits: int, wal_growth: int, samples: dict, clock_delta: int) -> dict:
    syncs = [(start, start + duration) for start, duration, call, _ in calls if call in ("fsync", "fdatasync")]
    require(syncs and calls[-1][2] in ("fsync", "fdatasync"), "acknowledged work lacks a final WAL sync")
    require(sum(result for _, _, call, result in calls if call == "write") == wal_growth,
            "WAL write trace byte count differs from file growth")
    require(all(end <= following for (_, end), (following, _) in zip(syncs, syncs[1:])), "overlapping WAL sync calls")
    starts = [start for start, _ in syncs]
    overlap = None
    # Both sides are on the same host and timestamps use CLOCK_REALTIME.
    # Leave arrival association unavailable if the phase clocks diverged.
    if abs(clock_delta) <= 100_000:
        overlap = {}
        for role, timings in samples.items():
            inside, outside = [], []
            for issued, duration in timings:
                index = bisect_right(starts, issued) - 1
                target = inside if index >= 0 and issued < syncs[index][1] else outside
                target.append(duration)
            overlap[role] = {"issued_during_sync": distribution(inside), "others": distribution(outside)}
    return {"wal_syscalls": {call: distribution([duration for _, duration, name, _ in calls if name == call])
                            for call in TRACE_CALLS},
            "commits_per_sync": commits / len(syncs), "wal_sync_calls": len(syncs),
            "phase_wall_minus_monotonic_ns": clock_delta, "client_issue_overlap": overlap}


def seed(connection) -> None:
    execute(connection, "CREATE TABLE profile_rows (id INT PRIMARY KEY, value INT NOT NULL)")
    for first in range(0, ROWS, 64):
        values = ",".join(f"({i},{i})" for i in range(first, first + 64))
        require(execute(connection, "INSERT INTO profile_rows VALUES " + values).rowcount == 64,
                "seed affected an unexpected number of rows")


def validate_rows(connection, expected) -> None:
    rows = execute(connection, "SELECT id, value FROM profile_rows ORDER BY id").fetchall()
    require(rows == expected, "complete row check differs from the independent model")


def request_batch(connection, role, index, requests, ready):
    statement = f"UPDATE profile_rows SET value = value + 1 WHERE id = {index}"
    timings = []
    ready.wait()
    for request in range(requests):
        key = ROWS // 2 + (index * 31 + request * 17) % (ROWS // 2)
        read = f"SELECT id, value FROM profile_rows WHERE id = {key}"
        began = time.perf_counter_ns()
        issued = time.time_ns()
        result = execute(connection, statement if role == WRITER else read)
        actual = result.rowcount if role == WRITER else result.fetchall()
        timings.append((issued, time.perf_counter_ns() - began))
        require(actual == (1 if role == WRITER else [(key, key)]), f"{role} returned {actual!r}")
    return timings


def measure(psycopg, args, folder, name, memory, traced, writers, readers):
    data_dir = folder / name
    data_dir.mkdir()
    wal = None if memory else data_dir / "database.wal"
    trace_path = data_dir / "wal.trace"
    prefix = ()
    if traced:
        prefix = (str(args.strace), "--seccomp-bpf", "--kill-on-exit", "-fqq", "-ttt", "-T", "-y", "-s", "0",
                  "-e", f"trace={','.join(TRACE_CALLS)}", "-P", str(wal), "-o", str(trace_path), "--")
    options = ("--workers", str(args.workers), "--max-clients", str(writers + readers + 1),
               "--result-rows", str(ROWS))
    expected = [(key, key) for key in range(ROWS)]
    samples = {role: [] for role, count in ((WRITER, writers), (READER, readers)) if count}
    with running_ramsql(args.binary, data_path=wal, create_data=not memory, extra_arguments=options,
                        command_prefix=prefix) as opened:
        with psycopg.connect(**opened, prepare_threshold=None) as connection:
            seed(connection)
            validate_rows(connection, expected)
        beginning_bytes = wal.stat().st_size if wal else 0
        beginning_sequence = last_sequence(wal.read_bytes()) if wal else 0
        with ExitStack() as connections, ThreadPoolExecutor(max_workers=writers + readers) as pool:
            ready = Barrier(writers + readers + 1, timeout=20)
            jobs = []
            for role, count in ((WRITER, writers), (READER, readers)):
                for index in range(count):
                    client = connections.enter_context(psycopg.connect(**opened, prepare_threshold=None))
                    requests = args.operations // count + int(index < args.operations % count)
                    if role == WRITER:
                        expected[index] = (index, index + requests)
                    jobs.append((role, pool.submit(request_batch, client, role, index, requests, ready)))
            began_mono = time.perf_counter_ns()
            began = time.time_ns()
            ready.wait()
            for role, job in jobs:
                samples[role].extend(job.result(timeout=120))
            ended = time.time_ns()
            elapsed = time.perf_counter_ns() - began_mono
        require(all(len(entries) == args.operations for entries in samples.values()), "unexpected request count")
        with psycopg.connect(**opened, prepare_threshold=None) as connection:
            validate_rows(connection, expected)
        wal_growth = wal.stat().st_size - beginning_bytes if wal else 0
        if wal:
            require(last_sequence(wal.read_bytes()) - beginning_sequence == (args.operations if writers else 0),
                    "unexpected WAL sequence growth")
            require((wal_growth > 0) == (writers > 0), "WAL grew without a write or write produced no WAL")
    if wal:
        with running_ramsql(args.binary, data_path=wal, extra_arguments=options) as reopened:
            with psycopg.connect(**reopened, prepare_threshold=None) as connection:
                validate_rows(connection, expected)
    result = {"storage": "memory" if memory else "synchronous_wal", "traced": traced,
              "writers": writers, "readers": readers, "phase_elapsed_ns": elapsed,
              "latency_ns": {role: distribution([duration for _, duration in timings])
                             for role, timings in samples.items()},
              "wal_growth_bytes": wal_growth, "all_rows_checked": True, "durable_reopen_checked": wal is not None}
    if traced:
        raw = trace_path.read_bytes()
        compressed = args.output / f"{name}.trace.gz"
        compressed.write_bytes(gzip.compress(raw, mtime=0))
        calls = parse_trace(raw.decode().splitlines(), wal, began, ended)
        result["trace"] = trace_summary(calls, args.operations, wal_growth, samples, ended - began - elapsed)
        result["trace"].update({"file": compressed.name, "sha256": sha256(compressed)})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--verification", type=Path, required=True, help="JSON from package_release.py verify")
    parser.add_argument("--strace", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--operations", type=int, default=512, help="total per active role in each phase")
    parser.add_argument("--clients", type=int, nargs="+", default=(1, 4, 8, 16), help="writer-only and total mixed client counts")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("requires a native Linux runner")
    if not 1 <= args.trials <= 10 or not 1 <= args.workers <= 16 or not 1 <= args.operations <= 8192:
        parser.error("trials must be 1..10, workers 1..16 and operations 1..8192")
    if len(set(args.clients)) != len(args.clients) or any(
            count < 1 or count > min(16, args.operations) or (count > 1 and count % 2) for count in args.clients):
        parser.error("client counts must be unique and 1 or even integers up to min(16, operations)")
    import psycopg

    args.binary = args.binary.resolve(strict=True)
    args.strace = args.strace.resolve(strict=True)
    verification = json.loads(args.verification.read_text(encoding="utf-8"))
    require(verification.get("passed") is True and verification.get("target") == "x86_64-linux"
            and sha256(args.binary) == verification.get("image_sha256"), "binary differs from verified Linux package")
    folder = args.work_dir.resolve() / uuid.uuid4().hex
    folder.mkdir(parents=True)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "binary_verification": verification,
              "environment": {"platform": platform.platform(), "cpu": cpu_name(), "logical_cpus": os.cpu_count(),
                              "affinity_cpu_count": len(os.sched_getaffinity(0)), "runner_image": os.environ.get("ImageVersion"),
                              "filesystem": command(["stat", "-f", "-c", "%T", str(folder)]),
                              "strace": command([str(args.strace), "--version"]), "psycopg": psycopg.__version__,
                              "profiler_source": source_fingerprint(Path(__file__).resolve().parents[1])},
              "workload": {"rows": ROWS, "trials": args.trials, "operations_per_active_role": args.operations,
                           "writer_only_and_mixed_clients": args.clients, "workers": args.workers,
                           "protocol": "loopback PostgreSQL, simple queries, autocommit, no pipelining",
                           "safety": "owned synthetic tables; durable phases reopen and verify all rows",
                           "limits": "Traced durations include ptrace effects. Issue overlap shows client-side timing. Gate wait and internal SQL/encode/publish time require source instrumentation."},
              "measurements": [], "passed": False}
    try:
        for trial in range(1, args.trials + 1):
            scenarios = [(count, 0) for count in args.clients]
            scenarios += [(count // 2, count // 2) for count in args.clients if count > 1]
            scenarios += [(0, count // 2) for count in args.clients if count > 1]
            storage = (False, True) if trial % 2 else (True, False)
            for writers, readers in scenarios:
                for memory, traced in [(memory, False) for memory in storage] + ([(False, True)] if writers else []):
                    mode = "trace" if traced else ("mem" if memory else "wal")
                    name = f"trial-{trial}-w{writers}-r{readers}-{mode}"
                    print(f"Measuring {name}", flush=True)
                    measurement = measure(psycopg, args, folder, name, memory, traced, writers, readers)
                    report["measurements"].append({"name": name, "trial": trial, **measurement})
        report["passed"] = True
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        (args.output / "durable-write-profile.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
