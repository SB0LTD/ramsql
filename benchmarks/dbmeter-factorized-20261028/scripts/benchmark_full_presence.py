"""Run unchanged reviewer full-data checks and isolated RAMSQL JDBC trials.

Windows diagnostic harness, not part of the Sig server. Requires the reviewer's
sibling dbMeter checkout/JAR and the original five-million-row PostgreSQL export
prepared in --data. Does not create reduced data or modify reviewer SQL/config.
Stop other database services, compilers and profilers before running.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from benchmark import process_memory
from benchmark_pk_scaling import cpu_times
from test_server import Server


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dbmeter", type=Path, default=Path("../dbMeter"))
    parser.add_argument("--data", type=Path, default=Path(".sig-cache/dbmeter-full"))
    parser.add_argument("--trials", type=int, default=3)
    options = parser.parse_args()
    if options.trials < 1:
        parser.error("--trials must be positive")
    binary, base, reviewer, dataset = (
        p.resolve() for p in (options.binary, options.out, options.dbmeter, options.data)
    )
    # Refuse accidental evidence replacement. Existing caches are preserved.
    base.mkdir(parents=True, exist_ok=False)
    config = reviewer / "examples/sales-agg/ramsql.properties"
    load = dataset / "ramsql_load.sql"
    psql = reviewer / "tools/postgres18/pgsql/bin/psql.exe"
    arguments = ["--workers", "8", "--table-rows", "5,200,20,1000,50000,5000000,1,1",
                 "--table-versions", "10,400,40,2000,100000,5065536,2,2",
                 "--work-rows", "65536", "--result-rows", "1024"]
    report = dict(schema=1, method="Original full5M checks and query files; in-memory RAMSQL, "
                  "eight workers/clients, 5s ramp/15s warm/60s measured. Failed full-suite "
                  "checks retained; only independently passing cases timed. No reference "
                  "engine throughput or universal advantage is established by this file.",
                  binary_sha256=sha(binary), input_sha256=sha(load),
                  config_sha256=sha(config), server_arguments=arguments,
                  dbmeter_revision=subprocess.check_output(
                      ["git", "-C", str(reviewer), "rev-parse", "HEAD"], text=True).strip(), runs=[])

    def save():
        (base / "ramsql-full-results.json").write_text(json.dumps(report, indent=2) + "\n")

    save()
    try:
        with Server(binary, max_clients=12, idle_ms=300000, extra_arguments=arguments) as server:
            report.update(pid=server.process.pid, port=server.port)
            load_began=time.monotonic(); load_before_cpu=cpu_times(server.process)
            with (base / "ramsql-load.log").open("w") as log:
                subprocess.run([str(psql), "-h", "127.0.0.1", "-p", str(server.port),
                                "-U", "postgres", "-d", "ramsql", "-q", "-v", "ON_ERROR_STOP=1",
                                "-f", str(load)], stdout=log, stderr=subprocess.STDOUT,
                               check=True, timeout=600, env=dict(os.environ, PGSSLMODE="disable"))
            load_after_cpu=cpu_times(server.process)
            report.update(load_wall_seconds=time.monotonic()-load_began, load_cpu_seconds=sum(load_after_cpu[k]-load_before_cpu[k] for k in load_before_cpu))
            report.update(loaded=True, after_load_memory=process_memory(server.process),
                          machine_after_load=json.loads(subprocess.check_output([str(binary), "--machine-capacity"])))
            save()
            java = ["java", "-Xms32m", "-Xmx256m", "-jar", str(reviewer / "build/dbmeter.jar")]
            common = ["--skip-setup", "-c", str(config), "--set",
                      f"jdbc.url=jdbc:postgresql://127.0.0.1:{server.port}/ramsql",
                      "--set", "run.databaseLabel=RAMSQL full5M"]

            def invoke(name, tail):
                command = java + tail
                before, began = cpu_times(server.process), time.monotonic()
                with (base / (name + ".log")).open("w") as log:
                    code = subprocess.run(command, cwd=reviewer, stdout=log,
                                          stderr=subprocess.STDOUT, timeout=600).returncode
                after = cpu_times(server.process)
                report["runs"].append(dict(name=name, argv=command, returncode=code,
                                          wall_seconds=time.monotonic() - began,
                                          cpu_seconds_including_warmup=sum(after[k] - before[k] for k in before)))
                save()
                print(name, code, flush=True)
                return code

            report["all_checks_returncode"] = invoke("ramsql-all-checks", ["check", *common, "--junit", str(base / "ramsql-all-checks.xml")])
            code = invoke("ramsql-supported-checks", ["check", *common, "--only",
                          "a_totals,q5_high_value_customers,q6_store_category_mix",
                          "--junit", str(base / "ramsql-supported-checks.xml")])
            report["supported_checks_passed"] = code == 0
            save()
            if code:
                raise RuntimeError("Original supported checks failed; throughput must not be scored")
            for trial in range(1, options.trials + 1):
                for query in ("q5_high_value_customers", "q6_store_category_mix"):
                    name = f"ramsql-5m-8-{query}-trial-{trial}"
                    code = invoke(name, ["load", *common, "--only", query, "--set", "load.threads=8",
                                         "--set", "load.rampUp=5s", "--set", "load.warmup=15s",
                                         "--set", "load.duration=60s", "--node", "local",
                                         "--run-id", name, "--out", str(base / name)])
                    if code:
                        raise RuntimeError("Measured load command failed")
                    scenarios = json.loads((base / name / "local.json").read_text())["scenarios"]
                    if not scenarios or any(s["errors"] or s["count"] == 0 for s in scenarios.values()):
                        raise RuntimeError("Measured SQL errors or missing scenario; do not score trial")
            report["after_queries_memory"] = process_memory(server.process)
            save()
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        save()
        raise


if __name__ == "__main__":
    main()
