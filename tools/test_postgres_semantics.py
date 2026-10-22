"""Step-aware differential semantics suites against native PostgreSQL.

Each case carries a mandatory float `step` (the plan step that closes it).
`--step N` executes every selected case with case.step <= N; every executed
case must match the reference. Cases above N are listed as skipped in the
report and on stdout, never silently dropped. Cases with `restart` statements
are durable-restart variants: after `statements`, RAMSQL is killed and restarted
on the same --data WAL, the reference container is crash-restarted, and the
`restart` statements are compared. They require --durable and are otherwise
listed as skipped. Matching is necessary evidence, not proof of full parity.

    python tools/test_postgres_semantics.py --suite selfcheck --step 1.4
    python tools/test_postgres_semantics.py --suite q01,q02 --step 2 --durable --scram

Exit codes: 0 every executed case matches; 1 differences; 2 harness error or
no case executed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

from test_postgres_compat import (BASE, ROOT, candidate_mode, differences, execute, native_postgres,
                                  restart_native, running_ramsql)


@dataclass(frozen=True)
class Case:
    name: str
    suite: str
    step: float
    setup: tuple = BASE
    statements: tuple = ()
    restart: tuple = ()
    tables: tuple[str, ...] = ("aux", "compat")
    feature: str = ""

    def __post_init__(self):
        # M3: the step is mandatory and must be a float so ordering is explicit.
        if not isinstance(self.step, float):
            raise TypeError(f"case {self.name}: step must be a float, got {self.step!r}")
        for statement in self.setup + self.statements + self.restart:
            if not (isinstance(statement, str) or (isinstance(statement, tuple) and len(statement) == 2)):
                raise TypeError(f"case {self.name}: statement must be SQL or (SQL, parameters)")


# Seed suites; each later FEAT fills its own suite. `selfcheck` verifies the
# runner itself (step filtering and a trivially matching case).
SUITES: dict[str, tuple[Case, ...]] = {name: () for name in
                                       ("q01", "q02", "q03", "q04", "q06", "q07", "q08", "q09", "q10", "p01")}
SUITES["selfcheck"] = (
    Case("selfcheck_point_select", "selfcheck", 0.0, statements=("SELECT label FROM compat WHERE id = 3",)),
    Case("selfcheck_future_step", "selfcheck", 99.0, statements=("SELECT label FROM compat WHERE id = 1",)),
    Case("selfcheck_restart", "selfcheck", 0.0,
         statements=("INSERT INTO compat VALUES (4, FALSE, 'durable')",),
         restart=("SELECT label FROM compat WHERE id = 4",)),
)


def run_statements(connection, statements):
    events = []
    for statement in statements:
        sql, parameters = (statement, None) if isinstance(statement, str) else statement
        events.append({"sql": sql, "parameters": parameters, "outcome": execute(connection, sql, parameters)})
    return events


def drop_tables(psycopg, arguments, tables):
    with psycopg.connect(**arguments) as connection:
        execute(connection, "ROLLBACK")
        for name in tables:
            execute(connection, "DROP TABLE " + name)


def run_semantic_case(psycopg, arguments, case, restart):
    """Run one case on one side; `restart()` crash-restarts that side in place."""
    drop_tables(psycopg, arguments, case.tables)
    try:
        with psycopg.connect(**arguments) as connection:
            for index, statement in enumerate(case.setup):
                sql, parameters = (statement, None) if isinstance(statement, str) else statement
                outcome = execute(connection, sql, parameters)
                if outcome["sqlstate"] != "00000":
                    return {"stage": "setup", "index": index, "sql": sql, "outcome": outcome}
            result = {"stage": "statements", "events": run_statements(connection, case.statements)}
        if case.restart:
            restart()
            with psycopg.connect(**arguments) as connection:
                result["after_restart"] = run_statements(connection, case.restart)
        return result
    finally:
        drop_tables(psycopg, arguments, case.tables)


class Candidate:
    """A restartable RAMSQL process; restart reuses the same --data WAL."""

    def __init__(self, binary, arguments):
        self.binary, self.arguments, self.manager, self.connection = binary, arguments, None, None

    def start(self):
        self.manager = running_ramsql(self.binary, **self.arguments)
        started = self.manager.__enter__()
        if self.connection is None:
            self.connection = started
        else:
            self.connection.clear()
            self.connection.update(started)

    def stop(self):
        manager, self.manager = self.manager, None
        if manager is not None:
            manager.__exit__(None, None, None)

    def restart(self):
        self.stop()
        self.start()


def select_cases(suites, step, durable):
    executed, skipped = [], []
    for suite in suites:
        for case in SUITES[suite]:
            if case.step > step:
                skipped.append({"name": case.name, "suite": suite, "step": case.step, "reason": f"step {case.step} > {step}"})
            elif case.restart and not durable:
                skipped.append({"name": case.name, "suite": suite, "step": case.step, "reason": "durable-restart case requires --durable"})
            else:
                executed.append(case)
    return executed, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="comma-separated suite names: " + ",".join(SUITES))
    parser.add_argument("--step", type=float, required=True, help="execute cases with case.step <= N")
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--image", default="postgres:18-alpine", help="locally available immutable reference image (no automatic pull)")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".sig-cache" / "postgres-compat")
    parser.add_argument("--report", type=Path, default=ROOT / ".sig-cache" / "postgres-compat" / "semantics-report.json")
    parser.add_argument("--durable", action="store_true", help="run RAMSQL on a fresh durable WAL instead of --memory")
    parser.add_argument("--scram", action="store_true", help="run RAMSQL with a generated SCRAM-SHA-256 verifier")
    args = parser.parse_args()
    suites = [name.strip() for name in args.suite.split(",") if name.strip()]
    unknown = [name for name in suites if name not in SUITES]
    if not suites or unknown:
        parser.error("unknown suite(s): " + ",".join(unknown or ["<none>"]))
    import psycopg

    executed, skipped = select_cases(suites, args.step, args.durable)
    binary = args.binary.resolve()
    report = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "finite differential semantics suites; matching does not establish full PostgreSQL parity",
              "suites": suites, "step": args.step, "ramsql_binary": str(binary),
              "ramsql_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "client": {"name": "psycopg", "version": psycopg.__version__},
              "cases": [], "skipped": skipped}
    for entry in skipped:
        print(f"SKIP {entry['suite']}/{entry['name']}: {entry['reason']}")
    try:
        if not executed:
            raise RuntimeError("no case executed for the selected suites and step")
        with native_postgres(args.image, psycopg, work_directory=args.work_dir) as (native, metadata), \
                candidate_mode(args.work_dir, durable=args.durable, scram=args.scram) as (mode_arguments, mode):
            report["reference"] = metadata
            report["mode"] = mode
            report["reference_image"] = {"id": metadata.get("image_id"), "repo_digests": metadata.get("image_digests")}
            candidate = Candidate(binary, mode_arguments)
            candidate.start()
            try:
                for case in executed:
                    reference_result = run_semantic_case(psycopg, native, case,
                                                         lambda: restart_native(native, metadata, psycopg))
                    candidate_result = run_semantic_case(psycopg, candidate.connection, case, candidate.restart)
                    gaps = differences(reference_result, candidate_result)
                    report["cases"].append({"name": case.name, "suite": case.suite, "step": case.step,
                                            "feature": case.feature, "match": not gaps, "differences": gaps,
                                            "postgres": reference_result, "ramsql": candidate_result})
                    print(("PASS" if not gaps else "GAP ") + f" {case.suite}/{case.name}" + ("" if not gaps else ": " + gaps[0]["path"]))
            finally:
                candidate.stop()
        mismatching = sum(not case["match"] for case in report["cases"])
        report["summary"] = {"executed": len(report["cases"]), "matching": len(report["cases"]) - mismatching,
                             "mismatching": mismatching, "skipped": len(skipped), "passed": mismatching == 0}
        result = 1 if mismatching else 0
    except Exception as error:
        report["harness_error"] = {"class": type(error).__name__, "message": str(error)}
        report["summary"] = {"executed": len(report["cases"]), "skipped": len(skipped), "passed": False, "incomplete": True}
        print("HARNESS ERROR: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        result = 2
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    print("Report: " + str(args.report.resolve()))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
