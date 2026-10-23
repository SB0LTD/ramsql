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
# Q01 (design §5.1, step 1.4): typed expressions and complete boolean
# predicates. BASE rows: (1, TRUE, 'one'), (2, FALSE, NULL), (3, NULL, 'three').
WIDE = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, v INTEGER)",) + tuple(
    f"INSERT INTO compat VALUES ({identity}, -1)" for identity in range(1, 401))
ATOMIC_UPDATE = "UPDATE compat SET v = 100 / (id - 399)"
ATOMIC_CHECK = ("SELECT id FROM compat WHERE v <> -1 ORDER BY id", "SELECT v FROM compat WHERE id = 398",
                "SELECT id, v FROM compat WHERE id >= 397 ORDER BY id")
SUITES["q01"] = (
    Case("q01_or_and_precedence", "q01", 1.4, feature="OR < AND precedence", statements=(
        "SELECT id FROM compat WHERE id = 1 OR id = 2 AND flag ORDER BY id",
        "SELECT id FROM compat WHERE id = 3 OR id = 2 AND NOT flag OR label = 'one' ORDER BY id",
        "SELECT TRUE OR FALSE AND FALSE, (TRUE OR FALSE) AND FALSE",
        "SELECT label FROM compat WHERE id = 1 OR id = 3 ORDER BY id")),
    Case("q01_nested_parentheses", "q01", 1.4, feature="nested parentheses", statements=(
        "SELECT id FROM compat WHERE ((id = 1) OR ((id = 3) AND (label = 'three'))) ORDER BY id",
        "SELECT id FROM compat WHERE (((id > 1)) AND ((NOT (id = 3)))) ORDER BY id",
        "SELECT ((1 + 2) * (3 - (4 - 5))) % 5")),
    Case("q01_not", "q01", 1.4, feature="NOT", statements=(
        "SELECT id FROM compat WHERE NOT id = 1 ORDER BY id",
        "SELECT id FROM compat WHERE NOT flag ORDER BY id",
        "SELECT id FROM compat WHERE NOT NOT flag ORDER BY id",
        "SELECT NOT NULL, NOT TRUE, NOT FALSE")),
    Case("q01_null_logic", "q01", 1.4, feature="three-valued AND/OR/NOT", statements=(
        "SELECT id, flag OR TRUE, flag AND FALSE, flag OR FALSE, flag AND TRUE, NOT flag FROM compat ORDER BY id",
        "SELECT NULL AND FALSE, NULL AND TRUE, NULL OR TRUE, NULL OR FALSE",
        "SELECT id FROM compat WHERE flag OR label = 'three' ORDER BY id",
        "SELECT id FROM compat WHERE NOT (flag AND label IS NULL) ORDER BY id")),
    Case("q01_null_in_between", "q01", 1.4, feature="NULL logic in IN and BETWEEN", statements=(
        "SELECT id, label IN ('one', NULL), label NOT IN ('one', NULL), id IN (1, 3), id NOT IN (1, 3) FROM compat ORDER BY id",
        "SELECT id, id BETWEEN 1 AND NULL, id NOT BETWEEN 2 AND 3, id BETWEEN 2 AND 3, NULL BETWEEN 1 AND 2 FROM compat ORDER BY id",
        "SELECT id FROM compat WHERE id IN (3, 1, NULL) ORDER BY id",
        "SELECT id FROM compat WHERE id NOT IN (2, NULL) ORDER BY id",
        "SELECT id FROM compat WHERE id NOT BETWEEN 2 AND 2 ORDER BY id")),
    Case("q01_is_tests", "q01", 1.4, feature="IS [NOT] TRUE|FALSE|UNKNOWN|NULL", statements=(
        "SELECT id, flag IS TRUE, flag IS NOT TRUE, flag IS FALSE, flag IS NOT FALSE FROM compat ORDER BY id",
        "SELECT id, flag IS UNKNOWN, flag IS NOT UNKNOWN, label IS NULL, label IS NOT NULL FROM compat ORDER BY id",
        "SELECT id FROM compat WHERE flag IS NOT TRUE ORDER BY id",
        "SELECT id FROM compat WHERE (id = 3 AND flag) IS UNKNOWN ORDER BY id",
        "SELECT NULL IS NULL, 1 IS NOT NULL")),
    Case("q01_where_integer", "q01", 1.4, feature="WHERE integer -> 42804", statements=(
        "SELECT id FROM compat WHERE id",
        "SELECT id FROM compat WHERE id AND flag",
        "SELECT 1 IS TRUE")),
    Case("q01_undefined_operator", "q01", 1.4, feature="text + integer -> 42883", statements=(
        "SELECT label + 1 FROM compat",
        "SELECT id FROM compat WHERE id = TRUE",
        "SELECT id || 1 FROM compat",
        "SELECT NULL + NULL")),
    Case("q01_invalid_text", "q01", 1.4, feature="'x'::integer -> 22P02", statements=(
        "SELECT 'x'::integer",
        "SELECT id FROM compat WHERE id = 'x'",
        "SELECT ' 42 '::integer, '-7'::bigint, 'yes'::boolean, 'off'::boolean",
        "SELECT 'maybe'::boolean",
        "SELECT label::integer FROM compat WHERE id = 1")),
    Case("q01_integer_overflow", "q01", 1.4, feature="2147483647 + 1 -> 22003", statements=(
        "SELECT 2147483647 + 1",
        "SELECT -2147483648 - 1",
        "SELECT 2147483647::bigint + 1, 2147483647 + 2147483648, -2147483648",
        "SELECT 9223372036854775807 + 1",
        "SELECT 2147483648::integer",
        "SELECT id * 2147483647 FROM compat ORDER BY id")),
    Case("q01_division_by_zero", "q01", 1.4, feature="division by zero -> 22012", statements=(
        "SELECT 1 / 0",
        "SELECT id % 0 FROM compat",
        "SELECT 7 / 2, -7 / 2, 7 % 3, -7 % 3, 7 % -3",
        "SELECT id FROM compat WHERE 10 / (id - 2) > 0 ORDER BY id",
        "SELECT NULL / 0")),
    Case("q01_case", "q01", 1.4, feature="CASE", statements=(
        "SELECT id, CASE WHEN flag THEN 'yes' WHEN NOT flag THEN 'no' ELSE 'unknown' END FROM compat ORDER BY id",
        "SELECT CASE id WHEN 1 THEN 'one' WHEN 2 THEN 'two' END AS spelled FROM compat ORDER BY id",
        "SELECT id, CASE WHEN id > 1 THEN id * 10 ELSE 2147483648 END FROM compat ORDER BY id",
        "SELECT CASE WHEN id = 1 THEN 'x' ELSE 1 END FROM compat",
        "SELECT CASE WHEN 1 THEN 1 END")),
    Case("q01_coalesce_nullif", "q01", 1.4, feature="COALESCE, NULLIF", statements=(
        "SELECT id, COALESCE(label, 'none'), NULLIF(id, 2), COALESCE(NULL, NULL, 3), NULLIF(label, 'one') FROM compat ORDER BY id",
        "SELECT COALESCE(flag, id = 3) FROM compat ORDER BY id",
        "SELECT COALESCE(label, 1) FROM compat")),
    Case("q01_like", "q01", 1.4, feature="LIKE, ILIKE", statements=(
        "SELECT id FROM compat WHERE label LIKE 't%' ORDER BY id",
        "SELECT id FROM compat WHERE label ILIKE 'ON_' ORDER BY id",
        "SELECT id FROM compat WHERE label NOT LIKE '%e' ORDER BY id",
        "SELECT id, label LIKE '%h%', label NOT ILIKE '%E' FROM compat ORDER BY id",
        "SELECT 'a%' LIKE 'a\\%', 'ab' LIKE 'a\\%', 'abc' LIKE '_b_', 'abc' LIKE 'a%c%'",
        "SELECT id LIKE '1' FROM compat")),
    Case("q01_concat", "q01", 1.4, feature="||", statements=(
        "SELECT label || '!', id || ':' || label, flag || 'x', 'a' || NULL FROM compat ORDER BY id",
        "SELECT 'x' || 'y' || 'z', NULL || NULL")),
    Case("q01_names_types", "q01", 1.4, feature="output column names and types", statements=(
        "SELECT id AS \"Ident\", label l, TRUE, 1::text, CAST(id AS bigint), id::text, 'lit', NULL FROM compat WHERE id = 1",
        "SELECT c.id FROM compat AS c WHERE compat.id = 1",
        "SELECT c.label FROM compat c WHERE c.id = 3",
        "SELECT compat.id FROM compat WHERE compat.id = 2",
        "SELECT id + 0 AS k, -id FROM compat ORDER BY k DESC")),
    Case("q01_order_by_expressions", "q01", 1.4, feature="ORDER BY expressions, positions and output names", statements=(
        "SELECT id, label FROM compat ORDER BY 2 DESC NULLS LAST, id",
        "SELECT id FROM compat ORDER BY id % 2, id DESC",
        "SELECT id AS k FROM compat ORDER BY k DESC",
        "SELECT label FROM compat ORDER BY COALESCE(label, 'a')",
        "SELECT id FROM compat ORDER BY 3")),
    Case("q01_unknown_literals", "q01", 1.4, feature="untyped literal resolution", statements=(
        "SELECT id FROM compat WHERE id = '2'",
        "SELECT id FROM compat WHERE flag = 't' ORDER BY id",
        "SELECT id FROM compat WHERE label = 'one' AND '1' = '1'",
        "SELECT '1' + '2'",
        "INSERT INTO compat VALUES ('4', 'yes', 44)",
        "SELECT id, flag, label FROM compat WHERE id = 4")),
    Case("q01_update_expressions", "q01", 1.4, feature="expression SET", statements=(
        "UPDATE compat SET label = label || '-x' WHERE id <> 2",
        "SELECT id, label FROM compat ORDER BY id",
        "UPDATE compat SET id = id + 10, flag = NOT flag WHERE id = 3",
        "SELECT id, flag FROM compat ORDER BY id",
        "UPDATE compat SET label = CASE WHEN flag THEN 'on' ELSE 'off' END",
        "SELECT id, label FROM compat ORDER BY id",
        "UPDATE compat SET id = 1 WHERE id = 2",
        "SELECT id FROM compat ORDER BY id")),
    Case("q01_atomic_failure", "q01", 1.4, feature="failure after many rows changes nothing",
         setup=WIDE, statements=(ATOMIC_UPDATE,) + ATOMIC_CHECK),
    Case("q01_atomic_failure_restart", "q01", 1.4, feature="failure after many rows survives restart",
         setup=WIDE, statements=(ATOMIC_UPDATE, "UPDATE compat SET v = 0 WHERE id = 1"), restart=ATOMIC_CHECK),
    Case("q01_numeric_promotion", "q01", 3.2, feature="mixed int2/int4/int8/numeric promotion (M3)", statements=(
        "SELECT 1::smallint + 2, 1::smallint + 2::bigint, 1 + 1.5, 2::bigint * 1.25",)),
)
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
