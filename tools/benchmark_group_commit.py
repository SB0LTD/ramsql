"""Compare identical durable explicit transactions on an owned hosted runner."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
import itertools
import json
import os
from pathlib import Path
import platform
import statistics
import threading
import time
import uuid

from benchmark import cpu_name
from benchmark_postgres import command, dependency_provenance, execute, percentile, require, sha256, source_fingerprint
from test_group_commit import TracedServer
from test_persistence import DurableServer
from test_postgres_compat import native_postgres


ENGINES = ("postgres", "ramsql_disabled", "ramsql_enabled")
SHAPES = ("distinct_tables", "same_table")


def connection_args(server):
    return {"host": "127.0.0.1", "port": server.port, "user": "ramsql", "dbname": "ramsql",
            "autocommit": True, "sslmode": "disable", "connect_timeout": 4, "prepare_threshold": None,
            "options": "-c statement_timeout=4000"}


def table_plan(shape, client_count):
    return [f"group_rows_{client if shape == 'distinct_tables' else 0}" for client in range(client_count)]


def seed(psycopg, connection, tables):
    with psycopg.connect(**connection) as db:
        for table in sorted(set(tables)):
            execute(db, f"CREATE TABLE {table} (id INT PRIMARY KEY, value INT NOT NULL)")
        for client, table in enumerate(tables):
            require(execute(db, f"INSERT INTO {table} VALUES ({client}, 0)").rowcount == 1, "invalid seed row count")


def verify(psycopg, connection, tables, counts):
    with psycopg.connect(**connection) as db:
        for table in sorted(set(tables)):
            expected = [(index, counts[index]) for index, name in enumerate(tables) if table == name]
            actual = execute(db, f"SELECT id, value FROM {table} ORDER BY id").fetchall()
            require(actual == expected, f"{table} has incorrect durable rows {actual!r}")


def transactions(db, table, key, count, start):
    update = f"UPDATE {table} SET value = value + 1 WHERE id = {key}"
    transaction_ns, commit_ns = [], []
    start.wait()
    for _ in range(count):
        opened = time.perf_counter_ns()
        require(execute(db, "BEGIN").statusmessage == "BEGIN", "invalid BEGIN completion")
        require(execute(db, update).rowcount == 1, "UPDATE changed an unexpected number of rows")
        committing = time.perf_counter_ns()
        committed = execute(db, "COMMIT")
        ended = time.perf_counter_ns()
        require(committed.statusmessage == "COMMIT", f"unexpected acknowledgement {committed.statusmessage!r}")
        transaction_ns.append(ended - opened)
        commit_ns.append(ended - committing)
    return transaction_ns, commit_ns


def percentile_us(samples):
    return {"p50": percentile(samples, 0.50) / 1000, "p95": percentile(samples, 0.95) / 1000,
            "p99": percentile(samples, 0.99) / 1000}


def drive(psycopg, connection, tables, counts):
    size = len(tables)
    start = threading.Barrier(size + 1, timeout=30)
    with ExitStack() as clients, ThreadPoolExecutor(max_workers=size) as pool:
        peers = [clients.enter_context(psycopg.connect(**connection)) for _ in tables]
        for key, (db, table) in enumerate(zip(peers, tables)):
            require(execute(db, f"SELECT value FROM {table} WHERE id = {key}").fetchone() == (0,), "invalid warmup")
        jobs = [pool.submit(transactions, db, table, key, count, start)
                for key, (db, table, count) in enumerate(zip(peers, tables, counts))]
        began = time.perf_counter_ns()
        start.wait()
        samples = [job.result(timeout=180) for job in jobs]
        elapsed = time.perf_counter_ns() - began
    txn = list(itertools.chain.from_iterable(sampleset[0] for sampleset in samples))
    commit = list(itertools.chain.from_iterable(sampleset[1] for sampleset in samples))
    require(len(txn) == len(commit) == sum(counts), "timed acknowledgement count differs")
    verify(psycopg, connection, tables, counts)
    return {"acknowledged": len(txn), "elapsed_ns": elapsed, "transactions_per_second": len(txn) * 1e9 / elapsed,
            "transaction_us": percentile_us(txn), "commit_us": percentile_us(commit)}, {"transaction": txn, "commit": commit}


def trial(psycopg, args, engine, native, folder, tables, counts):
    if engine == "postgres":
        seed(psycopg, native, tables)
        result, samples = drive(psycopg, native, tables, counts)
        with psycopg.connect(**native) as db:
            for table in sorted(set(tables)):
                execute(db, f"DROP TABLE {table}")
        return result, samples

    folder.mkdir()
    path = folder / "benchmark.wal"
    window = args.window_ms if engine == "ramsql_enabled" else 0
    options = ("--workers", str(args.workers), "--group-commit-wait-ms", str(window), "--max-clients", "16")
    with DurableServer(args.binary, path, create=True, storage_options=options) as server:
        connection = connection_args(server)
        seed(psycopg, connection, tables)
        result, samples = drive(psycopg, connection, tables, counts)
    with DurableServer(args.binary, path, storage_options=options) as reopened:
        verify(psycopg, connection_args(reopened), tables, counts)
    result["process_crash_replay_checked"] = True
    return result, samples


def observe_syncs(psycopg, args, folder, shape, client_count):
    tables = table_plan(shape, client_count)
    counts = [args.operations // client_count] * client_count
    folder.mkdir()
    path = folder / "diagnostic.wal"
    trace = folder / "fsync.log"
    with DurableServer(args.binary, path, create=True) as server:
        seed(psycopg, connection_args(server), tables)
    traced = TracedServer(args.binary, path, args.strace, trace, window=args.window_ms, workers=args.workers)
    traced.storage_options += ("--max-clients", "16")
    with traced as server:
        drive(psycopg, connection_args(server), tables, counts)
    raw = trace.read_bytes()
    calls = [line for line in raw.splitlines() if b"fsync(" in line]
    require(0 < len(calls) <= args.operations and all(b"= 0" in line for line in calls), "invalid WAL sync trace")
    if shape == "same_table":
        require(len(calls) == args.operations, "same-table workload unexpectedly grouped")
    with DurableServer(args.binary, path) as reopened:
        verify(psycopg, connection_args(reopened), tables, counts)
    copy = args.output.parent / f"fsync-{shape}-{client_count}.log.gz"
    copy.write_bytes(gzip.compress(raw, mtime=0))
    return {"shape": shape, "clients": client_count, "verified_commits": args.operations,
            "wal_fsyncs": len(calls), "commits_per_fsync": args.operations / len(calls),
            "trace_file": copy.name, "trace_sha256": sha256(copy)}


def summarize(measurements, args):
    output = []
    window_us = args.window_ms * 1000
    for shape in SHAPES:
        for clients in args.clients:
            matching = [item for item in measurements if item["shape"] == shape and item["clients"] == clients]
            if len(matching) != args.trials * len(ENGINES):
                continue
            engines = {engine: sorted((item for item in matching if item["engine"] == engine), key=lambda item: item["trial"])
                       for engine in ENGINES}
            enabled, disabled, postgres = (engines[key] for key in ("ramsql_enabled", "ramsql_disabled", "postgres"))
            observed = {engine: {"median_transactions_per_second": statistics.median(item["transactions_per_second"] for item in results),
                                 "median_commit_p95_us": statistics.median(item["commit_us"]["p95"] for item in results),
                                 "median_commit_p99_us": statistics.median(item["commit_us"]["p99"] for item in results),
                                 "median_transaction_p95_us": statistics.median(item["transaction_us"]["p95"] for item in results)}
                        for engine, results in engines.items()}
            enabled_vs_disabled = [on["transactions_per_second"] / off["transactions_per_second"] for on, off in zip(enabled, disabled)]
            enabled_vs_postgres = [on["transactions_per_second"] / pg["transactions_per_second"] for on, pg in zip(enabled, postgres)]
            on = observed["ramsql_enabled"]
            off = observed["ramsql_disabled"]
            targets = {"commit_p95_within_baseline_plus_window": on["median_commit_p95_us"] <= off["median_commit_p95_us"] + window_us,
                       "commit_p99_within_twice_baseline_plus_window": on["median_commit_p99_us"] <= 2 * off["median_commit_p99_us"] + window_us}
            if shape == "same_table":
                targets["every_trial_at_least_95_percent_of_disabled"] = min(enabled_vs_disabled) >= 0.95
            elif clients >= 4:
                targets["every_trial_at_least_15_percent_faster_than_disabled"] = min(enabled_vs_disabled) >= 1.15
                targets["every_trial_at_least_15_percent_faster_than_native_postgres"] = min(enabled_vs_postgres) >= 1.15
            output.append({"shape": shape, "clients": clients, "engines": observed,
                           "enabled_to_disabled_rate_ratios": enabled_vs_disabled,
                           "enabled_to_postgres_rate_ratios": enabled_vs_postgres,
                           "provisional_targets": targets})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--postgres-bin", type=Path, required=True)
    parser.add_argument("--strace", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--clients", type=int, nargs="+", default=(1, 2, 4, 8))
    parser.add_argument("--operations", type=int, default=1024)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--window-ms", type=int, default=1)
    args = parser.parse_args()
    require(os.environ.get("GITHUB_ACTIONS") == "true" and platform.system() == "Linux", "benchmark requires hosted Linux Actions")
    require(1 <= args.trials <= 10 and 2 <= args.workers <= 16 and 1 <= args.window_ms <= 50, "invalid trial, worker or window options")
    require(0 < args.operations <= 8192 and all(1 <= n <= 8 and args.operations % n == 0 for n in args.clients)
            and len(set(args.clients)) == len(args.clients), "invalid dataset or client counts")
    import psycopg

    args.binary = args.binary.resolve(strict=True)
    scratch = args.work_dir.resolve() / uuid.uuid4().hex
    scratch.mkdir(parents=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(), "passed": False,
              "environment": {"runner": os.environ.get("ImageVersion"), "platform": platform.platform(), "cpu": cpu_name(),
                              "affinity_cpus": len(os.sched_getaffinity(0)), "filesystem": command(["stat", "-f", "-c", "%T", str(scratch)]),
                              "psycopg": psycopg.__version__, "binary_sha256": sha256(args.binary),
                              "ramsql_version": command([str(args.binary), "--version"]),
                              "source": source_fingerprint(Path(__file__).resolve().parents[1]), "zpm": dependency_provenance()},
              "workload": {"transactions_per_phase": args.operations, "trials": args.trials, "clients": args.clients,
                           "ramsql_workers": args.workers, "enabled_window_ms": args.window_ms,
                           "protocol": "simple BEGIN, one point UPDATE, COMMIT on each client's unshared key",
                           "timing": "untraced localhost execute through verified COMMIT, warmed connections, setup and replay excluded",
                           "isolation": "native PostgreSQL and owned RAMSQL processes on the runner filesystem",
                           "limits": "One row per writer, distinct or shared tables, no general database performance claim"},
              "measurements": [], "traced_sync_diagnostic": []}
    raw_samples = {}
    try:
        capabilities = json.loads(command([str(args.binary), "--capabilities", "--workers", str(args.workers)]))
        require(capabilities["build_mode"] == "ReleaseFast" and capabilities["tables"] >= max(args.clients),
                "binary lacks exact ReleaseFast mode or required table slots")
        with native_postgres(None, psycopg, binary_directory=args.postgres_bin, work_directory=scratch) as (native, reference):
            report["reference"] = reference
            with psycopg.connect(**native) as connection:
                settings = {name: execute(connection, f"SHOW {name}").fetchone()[0]
                            for name in ("fsync", "synchronous_commit", "full_page_writes", "commit_delay", "wal_sync_method")}
            require(all(settings[key] == "on" for key in ("fsync", "synchronous_commit", "full_page_writes")),
                    "native PostgreSQL durability settings differ")
            report["reference"]["settings"] = settings
            for repeat in range(args.trials):
                for shape in SHAPES:
                    for clients in args.clients:
                        tables = table_plan(shape, clients)
                        counts = [args.operations // clients] * clients
                        rotation = repeat % len(ENGINES)
                        for engine in ENGINES[rotation:] + ENGINES[:rotation]:
                            label = f"{repeat + 1}-{shape}-{clients}-{engine}"
                            result, samples = trial(psycopg, args, engine, native, scratch / label, tables, counts)
                            raw_samples[label] = samples
                            report["measurements"].append({"trial": repeat + 1, "shape": shape, "clients": clients,
                                                           "engine": engine, "sample_key": label, **result})
                            print(f"{label}: {result['transactions_per_second']:.1f} tx/s, COMMIT p95={result['commit_us']['p95']:.0f}us", flush=True)
        for shape in SHAPES:
            for clients in args.clients:
                if clients > 1:
                    folder = scratch / f"observe-{shape}-{clients}"
                    report["traced_sync_diagnostic"].append(observe_syncs(psycopg, args, folder, shape, clients))
        report["passed"] = True
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        report["summary"] = summarize(report["measurements"], args)
        report["provisional_targets_passed"] = report["passed"] and all(
            all(item["provisional_targets"].values()) for item in report["summary"])
        sample_path = args.output.with_name("transaction-latency-ns.json.gz")
        sample_path.write_bytes(gzip.compress(json.dumps(raw_samples, separators=(",", ":")).encode(), mtime=0))
        report["raw_latency_samples"] = {"file": sample_path.name, "sha256": sha256(sample_path)}
        report["finished_utc"] = datetime.now(timezone.utc).isoformat()
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
