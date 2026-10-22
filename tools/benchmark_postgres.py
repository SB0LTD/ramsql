"""Matched durable SQL-subset experiments against native PostgreSQL.

Build ReleaseFast first. Both engines receive identical nonpipelined psycopg SQL,
data and operation counts; every result is checked. Native PostgreSQL must report
fsync=on, synchronous_commit=on and full_page_writes=on. RAMSQL uses its durable
WAL path (no --memory). Windows native RAMSQL versus Docker Linux PostgreSQL is
not an equivalent storage deployment: ratios apply only to this exact experiment.
This is not evidence of a faster PostgreSQL drop-in: the differential parity gate
must pass separately and currently has material gaps.

    python tools/benchmark_postgres.py --operations 1000 --trials 3 --clients 1 4
    python tools/benchmark_postgres.py --postgres-bin /usr/lib/postgresql/16/bin --work-dir /var/tmp/ramsql-benchmark

--postgres-bin uses isolated native processes with sibling data directories;
the reference cluster is preserved after its owned process stops. Its loopback
trust authentication matches RAMSQL's benchmark-only --insecure-local mode.

Exit 0 means measurement/correctness checks passed; it is not a speed threshold.
Generated RAMSQL WALs remain under .sig-cache/postgres-benchmark for inspection.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid

from test_postgres_compat import native_postgres, running_ramsql
from benchmark import cpu_name

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".sig-cache" / "client-deps"))
WORKLOADS = ("schema_create_drop", "insert", "point_update", "point_select", "scan_limit_16")


def command(arguments, cwd=ROOT):
    try:
        return subprocess.check_output(arguments, cwd=cwd, stderr=subprocess.DEVNULL, text=True, timeout=15).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_fingerprint(root):
    """Fingerprint source bytes, including new owned files before a commit.

    Git identifies tracked/untracked nonignored files; explicit generated-directory
    exclusions also protect against an accidentally tracked build output.
    """
    listing = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                                     cwd=root, timeout=15).decode("utf-8")
    excluded = {".git", ".sig-cache", ".zig-cache", "sig-out", "zig-out", "__pycache__"}
    names = sorted({name for name in listing.split("\0") if name and
                    not excluded.intersection(Path(name).parts) and
                    Path(name).suffix in (".sig", ".py", ".zon") and (root / name).is_file()})
    digest = hashlib.sha256()
    for name in names:
        path_bytes = name.replace("\\", "/").encode("utf-8")
        source = (root / name).read_bytes()
        digest.update(len(path_bytes).to_bytes(8, "little"))
        digest.update(path_bytes)
        digest.update(len(source).to_bytes(8, "little"))
        digest.update(source)
    dirty = command(["git", "status", "--porcelain"], cwd=root)
    return {"git_head": command(["git", "rev-parse", "HEAD"], cwd=root),
            "git_is_dirty": None if dirty is None else bool(dirty),
            "source_tree_digest": digest.hexdigest(), "source_tree_files": len(names),
            "source_tree_hash_format": "SHA256 of sorted relative UTF-8 paths and source bytes, each prefixed by LE64 length; .sig/.py/.zon only, excludes caches/outputs"}


def dependency_provenance():
    manifest = ROOT / "build.sig.zon"
    source = manifest.read_text(encoding="utf-8")
    result = {"manifest": "build.sig.zon", "manifest_sha256": sha256(manifest)}
    for key in ("url", "hash", "path"):
        match = re.search(r"\." + key + r'\s*=\s*"([^\"]+)"', source)
        if match:
            result[key] = match[1]
    if "path" in result:
        local = (ROOT / result["path"]).resolve()
        result["local_override"] = True
        result["local_source"] = source_fingerprint(local)
    else:
        result["local_override"] = False
    return result


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def percentile(samples, quantile):
    ordered = sorted(samples)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def execute(connection, statement):
    # No parameters, automatic preparation, pipelining or transaction batching.
    # This sends the same simple-query text and one request at a time to both.
    return connection.execute(statement, prepare=False)


def initialize(psycopg, arguments, rows):
    with psycopg.connect(**arguments) as connection:
        execute(connection, "CREATE TABLE bench (id INTEGER PRIMARY KEY, value INTEGER, label TEXT)")
        for identity in range(rows):
            result = execute(connection, f"INSERT INTO bench VALUES ({identity}, {identity}, 'v{identity}')")
            require(result.rowcount == 1, "setup INSERT affected an unexpected number of rows")
        result = execute(connection, "SELECT id, label FROM bench ORDER BY id").fetchall()
        require(result == [(identity, "v" + str(identity)) for identity in range(rows)], "setup rows differ")


def worker(connection, workload, worker_id, count, offset, clients, rows):
    latencies = []
    statements = 0
    with connection.cursor() as cursor:
        def request(statement, expected_rows=None, expected_count=None):
            nonlocal statements
            started = time.perf_counter_ns()
            cursor.execute(statement, prepare=False)
            actual = cursor.fetchall() if cursor.description else None
            elapsed = time.perf_counter_ns() - started
            if expected_rows is not None:
                require(actual == expected_rows, f"incorrect {workload} result: {actual!r}")
            if expected_count is not None:
                require(cursor.rowcount == expected_count, f"incorrect {workload} affected count: {cursor.rowcount}")
            latencies.append(elapsed)
            statements += 1

        for index in range(count):
            sequence = offset + index
            identity = (worker_id + sequence * clients) % rows
            if workload == "schema_create_drop":
                request(f"CREATE TABLE bc{worker_id} (id INTEGER PRIMARY KEY, value INTEGER)")
                request(f"DROP TABLE bc{worker_id}")
            elif workload == "insert":
                request(f"INSERT INTO bi{worker_id} VALUES ({index}, {sequence})", expected_count=1)
            elif workload == "point_update":
                request(f"UPDATE bench SET value = {sequence % 1000000} WHERE id = {identity}", expected_count=1)
            elif workload == "point_select":
                request(f"SELECT id, label FROM bench WHERE id = {identity}", expected_rows=[(identity, "v" + str(identity))])
            elif workload == "scan_limit_16":
                request("SELECT id, label FROM bench ORDER BY id LIMIT 16", expected_rows=[(item, "v" + str(item)) for item in range(16)])
            else:
                raise ValueError(workload)
    return latencies, statements


def measure(psycopg, arguments, workload, clients, operations, rows, trial):
    connections = [psycopg.connect(**arguments, prepare_threshold=None) for _ in range(clients)]
    maintenance_ns = 0
    setup_ns = 0
    samples = []
    elapsed_ns = 0
    totals = [operations // clients + (index < operations % clients) for index in range(clients)]
    offsets = [0] * clients
    try:
        started = time.perf_counter_ns()
        if workload == "insert":
            for index, connection in enumerate(connections):
                execute(connection, f"CREATE TABLE bi{index} (id INTEGER PRIMARY KEY, value INTEGER)")
        for connection in connections:
            for _ in range(5):
                require(execute(connection, "SELECT label FROM bench WHERE id = 0").fetchone() == ("v0",), "warmup returned bad data")
        setup_ns += time.perf_counter_ns() - started
        with ThreadPoolExecutor(max_workers=clients) as pool:
            while any(offset < total for offset, total in zip(offsets, totals)):
                # A single bounded INSERT phase fits the same 512-row capacity on
                # each engine. Reset maintenance is outside timed request phases.
                counts = [min(rows if workload == "insert" else total, total - offset)
                          for offset, total in zip(offsets, totals)]
                started = time.perf_counter_ns()
                futures = [pool.submit(worker, connection, workload, index, count, offsets[index], clients, rows)
                           for index, (connection, count) in enumerate(zip(connections, counts)) if count]
                results = [future.result(timeout=180) for future in futures]
                elapsed_ns += time.perf_counter_ns() - started
                for latencies, _ in results:
                    samples.extend(latencies)
                for index, count in enumerate(counts):
                    offsets[index] += count
                if workload == "insert":
                    started = time.perf_counter_ns()
                    for index, (connection, count) in enumerate(zip(connections, counts)):
                        if not count:
                            continue
                        actual = execute(connection, f"SELECT id, value FROM bi{index} ORDER BY id").fetchall()
                        expected = [(item, offsets[index] - count + item) for item in range(count)]
                        require(actual == expected, "INSERT batch did not retain exactly the acknowledged rows")
                        execute(connection, f"DELETE FROM bi{index}")
                    maintenance_ns += time.perf_counter_ns() - started
        require(len(samples) == operations * (2 if workload == "schema_create_drop" else 1), "incorrect timed request count")
        # All point updates are disjoint by connection index when rows is a
        # multiple of concurrency. Verify the final acknowledged value per key.
        if workload == "point_update":
            expected = {}
            for worker_id, total in enumerate(totals):
                for sequence in range(total):
                    expected[(worker_id + sequence * clients) % rows] = sequence % 1000000
            actual = dict(execute(connections[0], "SELECT id, value FROM bench").fetchall())
            require(all(actual[key] == value for key, value in expected.items()), "point UPDATE lost an acknowledged value")
        return {
            "trial": trial, "workload": workload, "clients": clients,
            "logical_operations": operations, "timed_requests": len(samples),
            "timed_request_phase_ns": elapsed_ns,
            "requests_per_second": len(samples) * 1e9 / elapsed_ns,
            "logical_operations_per_second": operations * 1e9 / elapsed_ns,
            "latency_us": {"p50": percentile(samples, 0.50) / 1000,
                           "p95": percentile(samples, 0.95) / 1000,
                           "p99": percentile(samples, 0.99) / 1000,
                           "mean": statistics.mean(samples) / 1000,
                           "max": max(samples) / 1000},
            "setup_warmup_ns_excluded": setup_ns,
            "insert_validation_reset_ns_excluded": maintenance_ns,
            "correctness": "all timed result rows/affected counts checked; INSERT retained rows and UPDATE final values verified",
        }
    finally:
        if workload == "insert":
            for index, connection in enumerate(connections):
                try:
                    execute(connection, f"DROP TABLE bi{index}")
                except Exception:
                    pass
        for connection in connections:
            connection.close()


def summarize(measurements):
    output = []
    for workload in WORKLOADS:
        for clients in sorted({item["clients"] for item in measurements}):
            matching = [item for item in measurements if item["workload"] == workload and item["clients"] == clients]
            engines = {}
            for name in ("postgres", "ramsql"):
                trials = [item for item in matching if item["engine"] == name]
                if trials:
                    engines[name] = {"median_requests_per_second": statistics.median(item["requests_per_second"] for item in trials),
                                     "min_requests_per_second": min(item["requests_per_second"] for item in trials),
                                     "max_requests_per_second": max(item["requests_per_second"] for item in trials),
                                     "median_p50_us": statistics.median(item["latency_us"]["p50"] for item in trials),
                                     "median_p95_us": statistics.median(item["latency_us"]["p95"] for item in trials),
                                     "median_p99_us": statistics.median(item["latency_us"]["p99"] for item in trials)}
            if len(engines) == 2:
                output.append({"workload": workload, "clients": clients, "engines": engines,
                               "selected_subset_ramsql_over_postgres_throughput": engines["ramsql"]["median_requests_per_second"] / engines["postgres"]["median_requests_per_second"]})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--image", default="postgres:18-alpine")
    parser.add_argument("--postgres-bin", type=Path, help="use an isolated native PostgreSQL installation instead of Docker")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".sig-cache" / "postgres-benchmark", help="parent for both engines' isolated data directories")
    parser.add_argument("--rows", type=int, default=512)
    parser.add_argument("--operations", type=int, default=1000, help="total logical operations per trial, shared evenly across clients")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--clients", type=int, nargs="+", default=[1, 4])
    parser.add_argument("--build-mode", default="ReleaseFast", help="record the mode used by the caller; the harness does not build")
    parser.add_argument("--report", type=Path, default=ROOT / "benchmarks" / "postgres-comparison.json")
    args = parser.parse_args()
    require(args.rows >= 16 and args.operations > 0 and args.trials > 0, "invalid workload sizes")
    require(all(0 < clients <= 4 and args.rows % clients == 0 and clients <= args.operations for clients in args.clients), "concurrency must be 1..4 and divide row count")
    import psycopg

    started = datetime.now(timezone.utc).isoformat()
    data_directory = args.work_dir.resolve() / uuid.uuid4().hex
    data_directory.mkdir(parents=True)
    data_path = data_directory / "ramsql.wal"
    binary = args.binary.resolve()
    usage = shutil.disk_usage(data_directory)
    source = source_fingerprint(ROOT)
    report = {
        "schema": 1, "started_utc": started,
        "scope": "durable selected SQL subset; no PostgreSQL drop-in performance conclusion; see compatibility/postgres-report.json",
        "environment": {"platform": platform.platform(), "python": platform.python_version(),
                        "cpu": cpu_name(), "logical_cpus": os.cpu_count(),
                        "client": {"name": "psycopg", "version": psycopg.__version__},
                        "ramsql_binary": str(binary), "ramsql_sha256": sha256(binary),
                        "ramsql_version": command([str(binary), "--version"]), "build_mode_declared": args.build_mode,
                        **source, "zpm_dependency": dependency_provenance(),
                        "data_path": str(data_path), "volume": data_path.anchor,
                        "volume_bytes_total": usage.total, "volume_bytes_free": usage.free,
                        "docker_storage_context": None if args.postgres_bin else command(["docker", "info", "--format", "driver={{.Driver}} os={{.OperatingSystem}} arch={{.Architecture}} cpus={{.NCPU}} memory={{.MemTotal}}"]),
                        "deployment_caveat": ("Both engines are native processes on the same host with sibling data directories. OS synchronization is required for both; physical power-loss equivalence is not established. RAMSQL implements a SQL subset and uses conservative transaction snapshots."
                                              if args.postgres_bin else "RAMSQL is a native host process; PostgreSQL is a Docker Linux container with an anonymous data volume. Networking, filesystem, VM and container costs are included and can differ materially. No equivalence of physical storage barriers is claimed.")},
        "workload": {"rows": args.rows, "operations_per_trial": args.operations, "trials": args.trials,
                     "concurrent_clients": args.clients, "protocol": "identical psycopg simple queries, autocommit, prepare=False, no pipeline",
                     "schema_create_drop": "each logical operation is one CREATE and one DROP; both request latencies are sampled",
                     "insert": "bounded per-client INSERT batches, retained row contents verified before DELETE reset; validation/reset phases excluded from request throughput and recorded separately",
                     "timing": "individual request latency includes execute and fetch/decode; active phase throughput includes Python thread orchestration; setup and warmup excluded"},
        "durability": {"ramsql": "durable --data WAL, synchronous stable-storage hook before commit publication; no --memory",
                       "limits": "OS synchronization guarantees only; this experiment does not simulate hardware power loss"},
        "measurements": [],
    }
    result = 0
    try:
        with native_postgres(args.image, psycopg, binary_directory=args.postgres_bin, work_directory=data_directory) as (native, metadata):
            report["reference"] = metadata
            with psycopg.connect(**native) as connection:
                settings = {name: execute(connection, "SHOW " + name).fetchone()[0]
                            for name in ("fsync", "synchronous_commit", "full_page_writes")}
            require(all(value == "on" for value in settings.values()), "native durability settings are not all on: " + str(settings))
            report["durability"]["postgres"] = settings
            with running_ramsql(binary, data_path=data_path) as candidate:
                initialize(psycopg, native, args.rows)
                initialize(psycopg, candidate, args.rows)
                arguments = {"postgres": native, "ramsql": candidate}
                for clients in args.clients:
                    for workload in WORKLOADS:
                        for trial in range(1, args.trials + 1):
                            # Alternate order to expose, rather than systematically
                            # favor one engine through, host cache/load drift.
                            engines = ("postgres", "ramsql") if trial % 2 else ("ramsql", "postgres")
                            for engine in engines:
                                measurement = measure(psycopg, arguments[engine], workload, clients, args.operations, args.rows, trial)
                                measurement["engine"] = engine
                                report["measurements"].append(measurement)
                                print(f"{engine} {workload} clients={clients} trial={trial}: "
                                      f"{measurement['requests_per_second']:.0f} requests/s, p99={measurement['latency_us']['p99']:.1f} us", flush=True)
            require(data_path.exists() and data_path.stat().st_size > 0, "durable RAMSQL produced no WAL")
            # The server was terminated without a graceful checkpoint. Reopen the
            # exact WAL and verify that all acknowledged dataset rows survived.
            recovery_started = time.perf_counter_ns()
            with running_ramsql(binary, data_path=data_path, startup_timeout=180) as recovered:
                with psycopg.connect(**recovered) as connection:
                    actual = execute(connection, "SELECT id, label FROM bench ORDER BY id").fetchall()
                require(actual == [(identity, "v" + str(identity)) for identity in range(args.rows)], "durable recovery lost acknowledged rows")
            report["durability"]["ramsql_reopen_row_check"] = "passed after abrupt process termination"
            report["durability"]["ramsql_reopen_and_validation_ms"] = (time.perf_counter_ns() - recovery_started) / 1e6
            report["durability"]["ramsql_wal_bytes"] = data_path.stat().st_size
        report["summary"] = summarize(report["measurements"])
    except Exception as error:
        report["harness_error"] = {"class": type(error).__name__, "message": str(error)}
        report["summary"] = summarize(report["measurements"])
        print("BENCHMARK ERROR: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        result = 2
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    report["measurement_checks_passed"] = result == 0
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Report: " + str(args.report.resolve()))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
