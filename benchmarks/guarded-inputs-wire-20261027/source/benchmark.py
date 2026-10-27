"""Reproducible native-engine and PostgreSQL-wire RAMSQL experiments.

Build optimized binaries with ``sig build benchmark -Doptimize=ReleaseFast``
and ``sig build -Doptimize=ReleaseFast`` before running this script. No speed
threshold is a test assertion. Every timed operation must return correct data.
Only the Python standard library is required.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import queue
import shutil
import statistics
import subprocess
import sys
import threading
import time

import test_server as wire


ROOT = Path(__file__).resolve().parents[1]
NATIVE_EXPECTED = {
    "primary_key_select": (50000, 25600000),
    "scan_limit_16": (5000, 80000),
    "point_update": (5000, 5000),
}
QUERY = "SELECT id, value FROM wire_bench WHERE id = 511"
EXPECTED_ROW = [["511", "511"]]
CHECKSUM_PER_OPERATION = 1022


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def command_text(arguments, cwd=ROOT):
    try:
        return subprocess.check_output(arguments, cwd=cwd, stderr=subprocess.STDOUT, text=True, timeout=10).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def fingerprint(path):
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}


def cpu_name():
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    if Path("/proc/cpuinfo").exists():
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    return platform.processor() or "unavailable"


def git_state(path):
    dirty = command_text(["git", "status", "--porcelain"], path)
    return {"head": command_text(["git", "rev-parse", "HEAD"], path),
            "dirty": bool(dirty) if dirty is not None else None,
            "status": dirty.splitlines() if dirty else []}


def process_memory(process):
    """OS process accounting, not a heap-allocation measurement."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("page_faults", wintypes.DWORD),
                            *[(name, ctypes.c_size_t) for name in (
                                "peak_rss", "rss", "peak_paged_pool", "paged_pool",
                                "peak_nonpaged_pool", "nonpaged_pool", "commit", "peak_commit", "private")]]

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x1010, False, process.pid)
            if not handle:
                return None
            try:
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return None
                return {"rss_bytes": counters.rss, "peak_rss_bytes": counters.peak_rss,
                        "private_commit_bytes": counters.private, "source": "GetProcessMemoryInfo"}
            finally:
                kernel.CloseHandle(handle)
        except (OSError, AttributeError):
            return None
    try:
        values = {}
        for line in Path(f"/proc/{process.pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in ("VmRSS", "VmHWM", "VmSize"):
                values[key] = int(value.split()[0]) * 1024
        return {"rss_bytes": values.get("VmRSS"), "peak_rss_bytes": values.get("VmHWM"),
                "virtual_bytes": values.get("VmSize"), "source": "/proc/PID/status"}
    except (OSError, ValueError):
        return None


def percentile(values, percent):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percent / 100) - 1)]


def validate_native(lines):
    records = [json.loads(line) for line in lines.splitlines() if line.strip()]
    require(len(records) == len(NATIVE_EXPECTED), "native benchmark returned the wrong number of cases")
    require({record.get("case") for record in records} == set(NATIVE_EXPECTED), "native benchmark cases differ from expected workloads")
    for record in records:
        iterations, checksum = NATIVE_EXPECTED[record["case"]]
        require(record.get("iterations") == iterations and record.get("checksum") == checksum,
                f"native benchmark correctness mismatch: {record!r}")
        require(record.get("rows") == 512 and record.get("elapsed_ns", 0) > 0,
                f"native benchmark produced invalid measurement: {record!r}")
        require(record.get("database_bytes", 0) > 0 and record.get("result_bytes", 0) > 0,
                "native benchmark omitted static storage sizes")
    return records


def native_benchmark(binary, trials):
    runs = []
    for _ in range(trials):
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30, check=True)
        require(not result.stderr, f"native benchmark stderr: {result.stderr!r}")
        runs.append(validate_native(result.stdout))
    summaries = []
    for name, (iterations, _) in NATIVE_EXPECTED.items():
        records = [next(record for record in run if record["case"] == name) for run in runs]
        elapsed = [record["elapsed_ns"] for record in records]
        rates = [iterations * 1e9 / duration for duration in elapsed]
        summaries.append({"case": name, "iterations_per_trial": iterations,
                          "median_ns_per_op": statistics.median(elapsed) / iterations,
                          "min_ns_per_op": min(elapsed) / iterations,
                          "median_ops_per_second": statistics.median(rates),
                          "min_ops_per_second": min(rates),
                          "database_bytes": records[0]["database_bytes"],
                          "result_bytes": records[0]["result_bytes"],
                          "checksum_per_trial": records[0]["checksum"]})
    return {"warmup_operations_per_case_per_trial": 1000,
            "includes": ["SQL parsing", "lookup or scan", "result materialization"],
            "trials": runs, "summary": summaries}


def verify_response(messages):
    # Keep correctness checks inside the measured interval. Their Python cost,
    # framing and socket overhead are therefore part of every wire result.
    require(not wire.errors(messages), f"wire benchmark query failed: {wire.errors(messages)!r}")
    require(messages[-1] == (b"Z", b"I"), "wire query returned non-idle status")
    require(wire.rows(messages) == EXPECTED_ROW, "wire query did not return the expected existing row")
    commands = [body for tag, body in messages if tag == b"C"]
    require(commands == [b"SELECT 1\0"], "wire query returned the wrong command/row count")
    return CHECKSUM_PER_OPERATION


def query_batches(pg, operations, batch):
    request = wire.query_packet(QUERY)
    durations = []
    amortized = []
    checksum = 0
    completed = 0
    while completed < operations:
        count = min(batch, operations - completed)
        start = time.perf_counter_ns()
        pg.sock.sendall(request * count)
        for _ in range(count):
            checksum += verify_response(pg.ready())
        elapsed = time.perf_counter_ns() - start
        durations.append(elapsed)
        amortized.append(elapsed / count)
        completed += count
    return {"operations": completed, "checksum": checksum,
            "batch_completion_ns": durations, "amortized_batch_ns_per_op": amortized}


def setup_wire_database(server):
    with wire.connection(server) as pg:
        pg.ok("CREATE TABLE wire_bench (id INTEGER PRIMARY KEY, value INTEGER, label TEXT)")
        for begin in range(0, 512, 64):
            requests = [wire.query_packet(f"INSERT INTO wire_bench VALUES ({number}, {number}, 'ramsql')")
                        for number in range(begin, begin + 64)]
            pg.sock.sendall(b"".join(requests))
            for _ in requests:
                messages = pg.ready()
                require(not wire.errors(messages), "benchmark dataset insertion failed")
                require([body for tag, body in messages if tag == b"C"] == [b"INSERT 0 1\0"], "benchmark row was not inserted")
        verify_response(pg.query(QUERY))


def wire_trial(server, operations, warmup, batch, concurrency):
    clients = []
    try:
        wire.live_probe(server)
        for _ in range(concurrency):
            pg = wire.PG(server.raw())
            clients.append(pg)
            pg.startup()
            query_batches(pg, warmup, batch)
        counts = [operations // concurrency + (index < operations % concurrency) for index in range(concurrency)]
        ready = queue.Queue()
        start_event = threading.Event()

        def worker(pg, count):
            ready.put(True)
            require(start_event.wait(timeout=10), "benchmark workers failed to start")
            return query_batches(pg, count, batch)

        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = [pool.submit(worker, pg, count) for pg, count in zip(clients, counts)]
            for _ in futures:
                ready.get(timeout=10)
            start = time.perf_counter_ns()
            start_event.set()
            results = [future.result(timeout=60) for future in futures]
            elapsed = time.perf_counter_ns() - start
        require(sum(result["operations"] for result in results) == operations, "wire benchmark lost operations")
        checksum = sum(result["checksum"] for result in results)
        require(checksum == operations * CHECKSUM_PER_OPERATION, "wire benchmark checksum mismatch")
        batches = [duration for result in results for duration in result["batch_completion_ns"]]
        amortized = [duration for result in results for duration in result["amortized_batch_ns_per_op"]]
        return {"operations": operations, "checksum": checksum, "elapsed_ns": elapsed,
                "ops_per_second": operations * 1e9 / elapsed, "batches": len(batches),
                "batch_completion_us_p50": percentile(batches, 50) / 1000,
                "batch_completion_us_p95": percentile(batches, 95) / 1000,
                "amortized_batch_completion_us_per_op_p50": percentile(amortized, 50) / 1000,
                "amortized_batch_completion_us_per_op_p95": percentile(amortized, 95) / 1000}
    finally:
        for pg in clients:
            pg.close()


def wire_benchmark(binary, args):
    cases = []
    with wire.Server(binary) as server:
        setup_wire_database(server)
        initial_memory = process_memory(server.process)
        for concurrency in args.concurrency:
            for batch in args.batches:
                runs = [wire_trial(server, args.operations, args.warmup, batch, concurrency)
                        for _ in range(args.trials)]
                rates = [run["ops_per_second"] for run in runs]
                case = {"case": "postgres_primary_key_select", "protocol": "PostgreSQL v3 simple query, text results",
                        "connections": concurrency, "pipeline_batch": batch, "rows": 512,
                        "operations_per_trial": args.operations,
                        "warmup_operations_per_connection_per_trial": args.warmup,
                        "median_ops_per_second": statistics.median(rates),
                        "min_ops_per_second": min(rates), "trials": runs}
                for metric in ("batch_completion_us_p50", "batch_completion_us_p95",
                               "amortized_batch_completion_us_per_op_p50", "amortized_batch_completion_us_per_op_p95"):
                    case["median_trial_" + metric] = statistics.median(run[metric] for run in runs)
                cases.append(case)
        final_memory = process_memory(server.process)
        require(server.process.poll() is None, f"server exited during benchmarking: {list(server.errors)!r}")
    return {"query": QUERY, "expected_rows_per_operation": 1, "expected_cells": EXPECTED_ROW,
            "checksum_per_operation": CHECKSUM_PER_OPERATION,
            "server_memory_after_setup": initial_memory, "server_memory_after_trials": final_memory,
            "cases": cases}


def environment(args):
    sources = {}
    for name in ("src/main.sig", "src/server.sig", "src/benchmark.sig", "build.sig", "build.sig.zon",
                 "tools/benchmark.py", "tools/test_server.py"):
        path = ROOT / name
        if path.exists():
            sources["ramsql/" + name] = digest(path)
    zpm = args.zpm_source.resolve() if args.zpm_source else None
    if zpm and zpm.exists():
        for name in ("src/core/sql.sig", "src/db/postgres.sig", "src/db/store.sig", "src/platform/tcp_server.sig"):
            path = zpm / name
            if path.exists():
                sources["zpm/" + name] = digest(path)
    compiler = shutil.which(args.compiler)
    return {"recorded_at_utc": datetime.now(timezone.utc).isoformat(), "os": platform.platform(),
            "machine": platform.machine(), "cpu": cpu_name(), "logical_cpus": os.cpu_count(),
            "python": platform.python_version(), "compiler_path": compiler,
            "compiler_version": command_text([args.compiler, "version"]),
            "build_mode_operator_declared": args.build_mode, "ramsql_git": git_state(ROOT),
            "zpm_git": git_state(zpm) if zpm and zpm.exists() else None,
            "source_sha256": sources,
            "server_binary": fingerprint(args.binary), "native_binary": fingerprint(args.native_binary)}


def integers(value):
    values = [int(part) for part in value.split(",")]
    if not values or any(number < 1 for number in values):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    suffix = ".exe" if os.name == "nt" else ""
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql" + suffix))
    parser.add_argument("--native-binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql-bench" + suffix))
    parser.add_argument("--baseline", type=Path, default=ROOT / ".sig-cache" / "benchmark-baseline.jsonl")
    parser.add_argument("--zpm-source", type=Path, help="explicit optional source checkout for metadata hashing")
    parser.add_argument("--compiler", default="sig")
    parser.add_argument("--build-mode", default="unspecified", help="metadata declaration; this script does not build binaries")
    parser.add_argument("--operations", type=int, default=5000, help="total timed operations per wire trial, split across connections")
    parser.add_argument("--warmup", type=int, default=500, help="untimed operations per connection and trial")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--batches", type=integers, default=[1, 16, 64])
    parser.add_argument("--concurrency", type=integers, default=[1, 8])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(args.operations > 0 and args.warmup >= 0 and args.trials > 0, "operation/trial counts must be positive; warmup may be zero")
    require(max(args.concurrency) <= 16 and max(args.concurrency) <= args.operations,
            "concurrency must fit the 16-client pool and operation count")
    require(max(args.batches) <= 1024, "pipeline batch exceeds this experiment's bounded request budget")
    args.binary = args.binary.resolve(strict=True)
    args.native_binary = args.native_binary.resolve(strict=True)
    began = time.monotonic()
    report = {"schema_version": 1, "experiment": "RAMSQL local native and loopback wire",
              "invocation": [sys.executable, *sys.argv],
              "configuration": {"operations_per_wire_trial": args.operations,
                                "warmup_per_connection_per_wire_trial": args.warmup,
                                "trials": args.trials, "pipeline_batches": args.batches,
                                "connections": args.concurrency},
              "environment": environment(args),
              "scope": [
                  "Single-host local experiment with a 512-row fixed-capacity in-memory table; no persistence, network hop, TLS or authentication cost.",
                  "Native measurements include parsing, execution and result materialization; native warmup is excluded.",
                  "Wire measurements include loopback TCP, Python client framing, Python threading/GIL, response decoding and correctness checks; connection setup and warmup are excluded.",
                  "Amortized batch completion time divides a batch's total completion time by its operation count. It is not an individual query latency distribution.",
                  "p50/p95 use nearest-rank batch observations; summary values are medians across trials, not pooled percentiles.",
                  "Process RSS/commit includes fixed buffers, mapped images and OS accounting; it does not prove absence of heap allocations and excludes kernel socket buffers.",
                  "The historical native baseline is a separate local capture without full environment metadata and is not a controlled comparative result.",
                  "No performance comparison with PostgreSQL, MySQL or SQL Server is made; no speed threshold determines success.",
              ]}
    if args.baseline.exists():
        report["historical_native_baseline"] = {"source": fingerprint(args.baseline),
                                                 "records": validate_native(args.baseline.read_text())}
    report["native"] = native_benchmark(args.native_binary, args.trials)
    report["wire"] = wire_benchmark(args.binary, args)
    report["experiment_seconds"] = round(time.monotonic() - began, 3)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.output.resolve()), "native_cases": len(report["native"]["summary"]),
                      "wire_cases": len(report["wire"]["cases"]), "seconds": report["experiment_seconds"]}))


if __name__ == "__main__":
    main()
