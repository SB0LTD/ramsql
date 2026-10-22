"""Deterministic multi-connection isolation schedules against native PostgreSQL.

Every actor owns one connection and one thread. Steps `(actor, sql, expect)`
are submitted in order; after each submission the runner waits 300 ms (1.5 s for
schedules tagged `deadlock`) and records blocked = not yet completed. Blocked
steps keep running and their eventual outcome is collected before teardown.
Per step the outcome (rows, columns, command tag, SQLSTATE), transaction status
and blocked flag are compared with the reference. `expect` (None, "ok",
"blocked" or a SQLSTATE) validates the schedule against the reference itself.
After the actors disconnect, a fresh session with statement_timeout=1000 updates
every row to prove that no lock leaked. Timing assumes an unloaded host: re-run
flaky schedules, never loosen the waits.

    python tools/test_postgres_isolation.py --schedule trivial_two_actor
    python tools/test_postgres_isolation.py --schedule trivial_two_actor --durable --scram

Exit codes: 0 all schedules match; 1 differences; 2 harness error (including a
reference that contradicts a schedule's `expect`).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

from test_postgres_compat import (BASE, ROOT, candidate_mode, differences, execute, native_postgres,
                                  running_ramsql)

BLOCK_WAIT = 0.3
DEADLOCK_WAIT = 1.5
DRAIN_SECONDS = 10.0


@dataclass(frozen=True)
class Step:
    actor: str
    sql: str
    expect: str | None = None


@dataclass(frozen=True)
class Schedule:
    id: str
    steps: tuple[Step, ...]
    setup: tuple[str, ...] = BASE
    tags: tuple[str, ...] = ()
    tables: tuple[str, ...] = ("aux", "compat")
    lock_probe: tuple[str, ...] = ("UPDATE compat SET label = 'lock-probe'",)


# Schedules are added by later FEATs. The trivial schedule checks the runner.
SCHEDULES: dict[str, Schedule] = {schedule.id: schedule for schedule in (
    Schedule("trivial_two_actor", steps=(
        Step("a", "INSERT INTO compat VALUES (4, FALSE, 'four')", "ok"),
        Step("b", "SELECT label FROM compat WHERE id = 4", "ok"),
        Step("b", "UPDATE compat SET label = 'bee' WHERE id = 2", "ok"),
        Step("a", "SELECT label FROM compat WHERE id = 2", "ok"),
    )),
)}


class Actor:
    """One connection driven by one thread; statements run strictly in order."""

    def __init__(self, psycopg, arguments):
        self.connection = psycopg.connect(**arguments)
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while True:
            job = self.jobs.get()
            if job is None:
                return
            sql, record = job
            record["outcome"] = execute(self.connection, sql)
            record["transaction_status"] = self.connection.info.transaction_status.name
            record["event"].set()

    def submit(self, sql):
        record = {"event": threading.Event()}
        self.jobs.put((sql, record))
        return record

    def close(self):
        self.jobs.put(None)
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            # A still-blocked statement: cancel it so the connection can close.
            try:
                self.connection.cancel_safe(timeout=3)
            except Exception:
                pass
            self.thread.join(timeout=5)
        self.connection.close()


def drop_tables(psycopg, arguments, tables):
    with psycopg.connect(**arguments) as connection:
        execute(connection, "ROLLBACK")
        for name in tables:
            execute(connection, "DROP TABLE " + name)


def step_result(step, record):
    return {"actor": step.actor, "sql": step.sql, "blocked": record["blocked"],
            "completed": record["event"].is_set(), "outcome": record.get("outcome"),
            "transaction_status": record.get("transaction_status")}


def run_schedule(psycopg, arguments, schedule):
    wait = DEADLOCK_WAIT if "deadlock" in schedule.tags else BLOCK_WAIT
    drop_tables(psycopg, arguments, schedule.tables)
    try:
        with psycopg.connect(**arguments) as connection:
            for index, statement in enumerate(schedule.setup):
                outcome = execute(connection, statement)
                if outcome["sqlstate"] != "00000":
                    return {"stage": "setup", "index": index, "sql": statement, "outcome": outcome}
        actors = {}
        records = []
        try:
            for step in schedule.steps:
                if step.actor not in actors:
                    actors[step.actor] = Actor(psycopg, arguments)
                record = actors[step.actor].submit(step.sql)
                record["blocked"] = not record["event"].wait(wait)
                records.append((step, record))
            deadline = time.monotonic() + DRAIN_SECONDS
            for _, record in records:
                record["event"].wait(max(0.0, deadline - time.monotonic()))
            results = [step_result(step, record) for step, record in records]
        finally:
            for actor in actors.values():
                actor.close()
        probe_arguments = dict(arguments, options="-c statement_timeout=1000")
        probe = Actor(psycopg, probe_arguments)
        try:
            probe_records = []
            for sql in schedule.lock_probe:
                record = probe.submit(sql)
                record["blocked"] = not record["event"].wait(DEADLOCK_WAIT)
                probe_records.append((Step("lock_probe", sql), record))
        finally:
            probe.close()
        return {"stage": "steps", "steps": results,
                "lock_probe": [step_result(step, record) for step, record in probe_records]}
    finally:
        drop_tables(psycopg, arguments, schedule.tables)


def expectation_failures(schedule, reference):
    if reference.get("stage") != "steps":
        return [{"step": None, "reason": "reference setup failed"}]
    failures = []
    for index, (step, result) in enumerate(zip(schedule.steps, reference["steps"])):
        outcome = result["outcome"] or {}
        if step.expect is None:
            continue
        if step.expect == "blocked":
            ok = result["blocked"]
        elif step.expect == "ok":
            ok = not result["blocked"] and outcome.get("sqlstate") == "00000"
        else:
            ok = outcome.get("sqlstate") == step.expect
        if not ok:
            failures.append({"step": index, "expect": step.expect, "blocked": result["blocked"],
                             "sqlstate": outcome.get("sqlstate")})
    for result in reference["lock_probe"]:
        if result["blocked"] or (result["outcome"] or {}).get("sqlstate") != "00000":
            failures.append({"step": "lock_probe", "reason": "reference lock probe did not complete"})
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, help="comma-separated schedule ids: " + ",".join(SCHEDULES))
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--image", default="postgres:18-alpine", help="locally available immutable reference image (no automatic pull)")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".sig-cache" / "postgres-compat")
    parser.add_argument("--report", type=Path, default=ROOT / ".sig-cache" / "postgres-compat" / "isolation-report.json")
    parser.add_argument("--durable", action="store_true", help="run RAMSQL on a fresh durable WAL instead of --memory")
    parser.add_argument("--scram", action="store_true", help="run RAMSQL with a generated SCRAM-SHA-256 verifier")
    args = parser.parse_args()
    ids = [name.strip() for name in args.schedule.split(",") if name.strip()]
    unknown = [name for name in ids if name not in SCHEDULES]
    if not ids or unknown:
        parser.error("unknown schedule(s): " + ",".join(unknown or ["<none>"]))
    import psycopg

    binary = args.binary.resolve()
    report = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "finite deterministic isolation schedules; matching does not establish full PostgreSQL parity",
              "block_wait_seconds": BLOCK_WAIT, "deadlock_wait_seconds": DEADLOCK_WAIT,
              "ramsql_binary": str(binary), "ramsql_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "client": {"name": "psycopg", "version": psycopg.__version__}, "schedules": []}
    try:
        with native_postgres(args.image, psycopg, work_directory=args.work_dir) as (native, metadata), \
                candidate_mode(args.work_dir, durable=args.durable, scram=args.scram) as (mode_arguments, mode):
            report["reference"] = metadata
            report["mode"] = mode
            report["reference_image"] = {"id": metadata.get("image_id"), "repo_digests": metadata.get("image_digests")}
            with running_ramsql(binary, **mode_arguments) as candidate:
                for schedule_id in ids:
                    schedule = SCHEDULES[schedule_id]
                    reference_result = run_schedule(psycopg, native, schedule)
                    failures = expectation_failures(schedule, reference_result)
                    if failures:
                        raise RuntimeError(f"reference contradicts schedule {schedule_id}: {failures}")
                    candidate_result = run_schedule(psycopg, candidate, schedule)
                    gaps = differences(reference_result, candidate_result)
                    report["schedules"].append({"id": schedule_id, "tags": list(schedule.tags), "match": not gaps,
                                                "differences": gaps, "postgres": reference_result,
                                                "ramsql": candidate_result})
                    print(("PASS" if not gaps else "GAP ") + " " + schedule_id + ("" if not gaps else ": " + gaps[0]["path"]))
        mismatching = sum(not entry["match"] for entry in report["schedules"])
        report["summary"] = {"schedules": len(report["schedules"]), "matching": len(report["schedules"]) - mismatching,
                             "mismatching": mismatching, "passed": mismatching == 0}
        result = 1 if mismatching else 0
    except Exception as error:
        report["harness_error"] = {"class": type(error).__name__, "message": str(error)}
        report["summary"] = {"schedules": len(report["schedules"]), "passed": False, "incomplete": True}
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
