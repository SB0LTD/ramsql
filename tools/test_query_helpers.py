"""Live read equivalence and native participation for bounded query helpers."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from test_persistence import DurableServer


ROWS = 65536 + 13
QUERY = "SELECT k,COUNT(*),SUM(a),SUM(b) FROM fact GROUP BY k ORDER BY k"


def invoke(binary, *args, **process_options):
    return subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=30, **process_options)


def successful_json(binary, *args):
    result = invoke(binary, *args)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def rejected(binary, args, error="InvalidServerOptions", **process_options):
    result = invoke(binary, *args, **process_options)
    assert result.returncode != 0 and not result.stdout, result
    assert error in result.stderr, result.stderr


def linux_ticks(pid, tid):
    stat = Path(f"/proc/{pid}/task/{tid}/stat").read_text()
    fields = stat[stat.rfind(")") + 2 :].split()
    return int(fields[11]) + int(fields[12])


def expected():
    buckets = [[key - 5, 0, 0, 0] for key in range(11)]
    for index in range(ROWS):
        row = buckets[index % 11]
        row[1] += 1
        row[2] += 2 * ((index % 31) - 15)
        row[3] += (index % 17) - 8
    return [[str(value) for value in row] for row in buckets]


def check_live(binary, base, options, prove_linux):
    rows = expected()
    with DurableServer(binary, base, create=True, storage_options=options) as server:
        client = server.connect()
        try:
            client.ok("CREATE TABLE fact (id INT PRIMARY KEY,k BIGINT,a BIGINT,b INT)")
            for start in range(0, ROWS, 128):
                end = min(start + 128, ROWS) - 1
                client.ok("INSERT INTO fact SELECT n,(n % 11)-5,2*((n % 31)-15),(n % 17)-8 "
                          f"FROM generate_series({start},{end}) AS g(n)")
            client.ok(QUERY, rows)
        finally:
            client.close()

    with DurableServer(binary, base, storage_options=options + ("--query-helpers", "1")) as server:
        client = server.connect()
        try:
            if prove_linux:
                pid = server.process.pid
                tasks = sorted(int(entry.name) for entry in Path(f"/proc/{pid}/task").iterdir())
                assert len(tasks) >= 4 and tasks[0] == pid, tasks
                # The product starts one helper before its reactor team.
                helper = tasks[1]
                before = linux_ticks(pid, helper)
            client.ok(QUERY, rows)
            if prove_linux:
                for repeat in range(256):
                    # Distinct input prevents a result cache from swallowing
                    # the work we need to see on the native helper.
                    client.ok(f"{QUERY} /* participation {repeat} */", rows)
                    if linux_ticks(pid, helper) > before:
                        print(f"PASS native query work on Linux helper task {helper}")
                        break
                else:
                    raise AssertionError("the reserved native helper recorded no query work")

            client.ok("UPDATE fact SET a=NULL WHERE id=0")
            rows[0][2] = str(int(rows[0][2]) + 30)
            client.ok(QUERY, rows)  # The nullable measure uses the serial typed path.
        finally:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--prove-linux-helpers", action="store_true")
    parser.add_argument("--output", type=Path, default=Path(".scratch/query-helpers"))
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    default = successful_json(binary, "--capabilities")
    assert default["query_helpers"] == 0 and default["query_helper_reserved_bytes"] == 0
    assert default["query_helper_ceiling"] == 3
    rejected(binary, ("--workers", "2", "--query-helpers", "4"))
    rejected(binary, ("--workers", "1", "--query-helpers", "1"))
    rejected(binary, ("--database-cluster", "--workers", "1", "--query-helpers", "1"))
    rejected(binary, ("--protocol", "mysql", "--workers", "2", "--query-helpers", "1"))

    table_rows = [ROWS + 64] + [32] * (default["tables"] - 1)
    csv = lambda numbers: ",".join(map(str, numbers))
    options = ("--workers", "2", "--table-rows", csv(table_rows),
               "--table-versions", csv([count * 2 for count in table_rows]),
               "--work-rows", "512", "--result-rows", "32")
    active = ("--query-helpers", "1")
    enabled = successful_json(binary, "--capabilities", *options, *active)
    assert enabled["query_helpers"] == 1 and enabled["query_helper_reserved_bytes"] > 0
    base = successful_json(binary, "--machine-capacity", *options)
    expanded = successful_json(binary, "--machine-capacity", *options, *active)
    assert expanded["startup_region_bytes"] - base["startup_region_bytes"] == enabled["query_helper_reserved_bytes"]
    if args.prove_linux_helpers:
        core = min(os.sched_getaffinity(0))
        rejected(binary, ("--memory", "--insecure-local", *options, *active),
                 "QueryHelpersExceedCpuCapacity", preexec_fn=lambda: os.sched_setaffinity(0, {core}))

    args.output.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(dir=args.output.resolve(), prefix="native-"))
    check_live(binary, work / "data.wal", options, args.prove_linux_helpers)
    print(f"PASS bounded query-helper admission and live result equivalence in {work}")


if __name__ == "__main__":
    main()
