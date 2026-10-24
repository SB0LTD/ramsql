"""Deterministic multi-connection isolation schedules against native PostgreSQL.

Every actor owns one connection and one thread. Steps `(actor, sql, expect)`
are submitted in order; after each submission the runner waits 300 ms (1.5 s for
schedules tagged `deadlock`) and records blocked = not yet completed. Blocked
steps keep running and their eventual outcome is collected before teardown.
Per step the outcome (rows, columns, command tag, SQLSTATE), transaction status
and blocked flag are compared with the reference. `expect` (None, "ok",
"blocked" or a SQLSTATE) validates the schedule against the reference itself.
Steps may instead be runner actions: `cancel` (psycopg cancel of the actor's
running statement), `disconnect` (close the actor's connection) and `timed`
(a statement whose completion under 100 ms is part of the outcome).
`final` statements (by default the table's rows) run on a fresh connection once
every step has drained, so a waiter resuming right after its blocker commits
never races the final read.
Schedules with `after_restart` statements crash-restart both engines after the
actors disconnect (--durable only; in memory mode that part is reported as
skipped) and compare those statements on a fresh connection.
After the actors disconnect, a fresh session with statement_timeout=1000 runs
the lock probe to prove that no lock leaked. Timing assumes an unloaded host:
re-run flaky schedules, never loosen the waits.

    python tools/test_postgres_isolation.py --schedule trivial_two_actor
    python tools/test_postgres_isolation.py --schedule RC1-RC14,LK1-LK9 --durable --scram

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
import re
import sys
import threading
import time

from test_postgres_compat import (BASE, ROOT, candidate_mode, differences, execute, native_postgres,
                                  restart_native)
from test_postgres_semantics import Candidate

BLOCK_WAIT = 0.3
DEADLOCK_WAIT = 1.5
DRAIN_SECONDS = 10.0
FAST_SECONDS = 0.1
# Actors may wait several deadlock timeouts; both engines apply this.
ACTOR_OPTIONS = "-c statement_timeout=20000"


@dataclass(frozen=True)
class Step:
    actor: str
    sql: str
    expect: str | None = None
    action: str = "sql"


@dataclass(frozen=True)
class Schedule:
    id: str
    steps: tuple[Step, ...]
    setup: tuple[str, ...] = BASE
    tags: tuple[str, ...] = ()
    tables: tuple[str, ...] = ("aux", "compat")
    lock_probe: tuple[str, ...] = ("UPDATE compat SET label = 'lock-probe'",)
    after_restart: tuple[str, ...] = ()
    # Read on a fresh connection after every step drained (no race with a
    # waiter that resumes just after the step that released it).
    final: tuple[str, ...] = ()


T = ("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)",
     "INSERT INTO t VALUES (1, 0)", "INSERT INTO t VALUES (2, 0)", "INSERT INTO t VALUES (3, 0)")
T_TABLES = ("t", "sp8", "ddl1", "ddl2", "ddl4")
T_PROBE = ("UPDATE t SET v = v",)
ROWS = "SELECT id, v FROM t ORDER BY id"


def s(actor, sql, expect=None):
    return Step(actor, sql, expect)


def t_schedule(id, *steps, tags=(), after_restart=(), setup=T, lock_probe=T_PROBE, final=(ROWS,)):
    return Schedule(id, steps=tuple(steps), setup=setup, tags=tags, tables=T_TABLES,
                    lock_probe=lock_probe, after_restart=after_restart, final=final)


SCHEDULES: dict[str, Schedule] = {schedule.id: schedule for schedule in (
    Schedule("trivial_two_actor", steps=(
        Step("a", "INSERT INTO compat VALUES (4, FALSE, 'four')", "ok"),
        Step("b", "SELECT label FROM compat WHERE id = 4", "ok"),
        Step("b", "UPDATE compat SET label = 'bee' WHERE id = 2", "ok"),
        Step("a", "SELECT label FROM compat WHERE id = 2", "ok"),
    )),
    # ---- T01/T02 READ COMMITTED (design §5.18) ----
    Schedule("RC1", steps=(
        s("a", "BEGIN", "ok"), s("a", "SELECT label FROM compat WHERE id = 1", "ok"),
        s("b", "UPDATE compat SET label = 'visible' WHERE id = 1", "ok"),
        s("a", "SELECT label FROM compat WHERE id = 1", "ok"), s("a", "COMMIT", "ok"),
    )),
    t_schedule("RC2", s("a", "BEGIN"), s("a", "SELECT id FROM t ORDER BY id"),
               s("b", "INSERT INTO t VALUES (4, 0)", "ok"), s("a", "SELECT id FROM t ORDER BY id", "ok"),
               s("b", "DELETE FROM t WHERE id = 1", "ok"), s("a", "SELECT id FROM t ORDER BY id", "ok"),
               s("a", "COMMIT", "ok")),
    t_schedule("RC3", s("a", "BEGIN"), s("a", "INSERT INTO t VALUES (4, 4)"),
               s("b", "UPDATE t SET v = 5 WHERE id = 2", "ok"), s("a", ROWS, "ok"),
               s("a", "COMMIT", "ok"), s("b", ROWS, "ok")),
    t_schedule("RC4", s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 1", "blocked"), s("a", "COMMIT", "ok")),
    t_schedule("RC5", s("a", "BEGIN"), s("a", "UPDATE t SET v = 10 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = v + 1 WHERE id = 1 AND v = 0", "blocked"), s("a", "COMMIT", "ok")),
    t_schedule("RC6", s("a", "BEGIN"), s("a", "DELETE FROM t WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 9 WHERE id = 1", "blocked"), s("a", "COMMIT", "ok")),
    t_schedule("RC7", s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = v + 5 WHERE id = 1", "blocked"), s("a", "ROLLBACK", "ok")),
    t_schedule("RC8", s("a", "BEGIN"), s("a", "INSERT INTO t VALUES (5, 0)", "ok"),
               s("b", "INSERT INTO t VALUES (5, 1)", "blocked"), s("a", "COMMIT", "ok"),
               s("a", "BEGIN"), s("a", "INSERT INTO t VALUES (6, 0)", "ok"),
               s("b", "INSERT INTO t VALUES (6, 1)", "blocked"), s("a", "ROLLBACK", "ok")),
    t_schedule("RC9", s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 1", "blocked"), Step("a", "", action="disconnect")),
    t_schedule("RC10", s("a", "BEGIN"), s("b", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 2", "ok"), s("a", "COMMIT", "ok"), s("b", "COMMIT", "ok"),
               s("c", ROWS, "ok")),
    t_schedule("RC11", s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = v + 100", "blocked"), s("c", "INSERT INTO t VALUES (4, 0)", "ok"),
               s("a", "COMMIT", "ok")),
    t_schedule("RC12", s("a", "BEGIN"), s("a", "UPDATE t SET v = v + 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = v + 1 WHERE id = 1", "blocked"), s("a", "COMMIT", "ok"),
               final=("SELECT v FROM t WHERE id = 1",)),
    t_schedule("RC13", s("a", "BEGIN"), s("a", "UPDATE t SET v = 10 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = v + 1 WHERE v < 5", "blocked"),
               s("c", "UPDATE t SET v = 7 WHERE id = 3", "ok"), s("c", "UPDATE t SET v = 1 WHERE id = 2", "ok"),
               s("a", "COMMIT", "ok")),
    # RC14, RETURNING form (step 3.6): rows 1..3 in order, values and UPDATE 3.
    t_schedule("RC14", s("a", "BEGIN"), s("a", "UPDATE t SET v = 9 WHERE id = 2", "ok"),
               s("b", "UPDATE t SET v = v + 1 RETURNING id, v", "blocked"), s("a", "COMMIT", "ok")),
    # ---- T02 locks, waits and deadlocks ----
    t_schedule("LK1", s("a", "BEGIN"), s("b", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 2", "ok"), s("a", "UPDATE t SET v = 1 WHERE id = 2", "blocked"),
               s("b", "UPDATE t SET v = 2 WHERE id = 1", "40P01"), s("a", "COMMIT", "ok"), s("b", "ROLLBACK", "ok"),
               tags=("deadlock",)),
    t_schedule("LK2", s("a", "BEGIN"), s("b", "BEGIN"), s("c", "BEGIN"),
               s("a", "UPDATE t SET v = 1 WHERE id = 1"), s("b", "UPDATE t SET v = 2 WHERE id = 2"),
               s("c", "UPDATE t SET v = 3 WHERE id = 3"), s("a", "UPDATE t SET v = 1 WHERE id = 2", "blocked"),
               s("b", "UPDATE t SET v = 2 WHERE id = 3", "blocked"), s("c", "UPDATE t SET v = 3 WHERE id = 1", "40P01"),
               s("b", "COMMIT", "ok"), s("a", "COMMIT", "ok"), s("c", "ROLLBACK", "ok"),
               tags=("deadlock",)),
    t_schedule("LK3", s("a", "BEGIN"), s("a", "SELECT id FROM t WHERE id = 1 FOR SHARE", "ok"),
               s("b", "BEGIN"), s("b", "SELECT id FROM t WHERE id = 1 FOR SHARE", "ok"),
               s("c", "SELECT id FROM t WHERE id = 1 FOR UPDATE", "blocked"), s("a", "COMMIT", "ok"),
               s("d", "UPDATE t SET v = 4 WHERE id = 1", "blocked"), s("b", "COMMIT", "ok")),
    t_schedule("LK4", s("a", "BEGIN"), s("a", "SELECT id FROM t WHERE id = 1 FOR UPDATE", "ok"),
               s("b", "SELECT id FROM t WHERE id = 1 FOR UPDATE NOWAIT", "55P03"),
               s("b", "SELECT id FROM t ORDER BY id FOR UPDATE SKIP LOCKED", "ok"),
               s("b", "SELECT id FROM t ORDER BY id FOR SHARE SKIP LOCKED", "ok"), s("a", "COMMIT", "ok")),
    t_schedule("LK5", s("b", "SET lock_timeout = '200ms'", "ok"), s("b", "SHOW lock_timeout", "ok"),
               s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 1", "55P03"), s("b", "SET lock_timeout = 0", "ok"),
               s("b", "SET statement_timeout = '200ms'", "ok"), s("b", "SHOW statement_timeout", "ok"),
               s("b", "UPDATE t SET v = 3 WHERE id = 1", "57014"), s("b", "RESET statement_timeout", "ok"),
               s("a", "COMMIT", "ok"), s("b", ROWS, "ok")),
    t_schedule("LK6", s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 1", "blocked"), Step("b", "", action="cancel"),
               s("a", "COMMIT", "ok")),
    t_schedule("LK7", s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 1", "blocked"),
               Step("c", "SELECT id, v FROM t WHERE id = 2", "ok", action="timed"),
               s("a", "COMMIT", "ok")),
    t_schedule("LK8", s("a", "BEGIN"), s("b", "BEGIN"), s("c", "BEGIN"),
               s("a", "SELECT id FROM t WHERE id = 1 FOR SHARE", "ok"),
               s("b", "SELECT id FROM t WHERE id = 1 FOR SHARE", "ok"),
               s("c", "SELECT id FROM t WHERE id = 2 FOR UPDATE", "ok"),
               s("c", "UPDATE t SET v = 3 WHERE id = 1", "blocked"),
               s("b", "UPDATE t SET v = 2 WHERE id = 2"), s("a", "COMMIT", "ok"),
               s("c", "COMMIT"), s("b", "ROLLBACK"), tags=("deadlock",)),
    t_schedule("LK9", s("a", "BEGIN"), s("a", "SELECT id FROM t WHERE id = 2 FOR UPDATE", "ok"),
               s("b", "BEGIN"), s("b", "SELECT id FROM t ORDER BY id LIMIT 2 FOR UPDATE", "blocked"),
               s("a", "COMMIT", "ok"), s("c", "SELECT id FROM t WHERE id = 3 FOR UPDATE NOWAIT", "ok"),
               s("c", "SELECT id FROM t WHERE id = 2 FOR UPDATE NOWAIT", "55P03"), s("b", "COMMIT", "ok")),
    # ---- T03 savepoints (design §5.16) ----
    t_schedule("SP1", s("a", "BEGIN"), s("a", "SAVEPOINT s1", "ok"), s("a", "UPDATE t SET v = 1 WHERE id = 1"),
               s("a", "SAVEPOINT s2", "ok"), s("a", "UPDATE t SET v = 2 WHERE id = 2"),
               s("a", "ROLLBACK TO SAVEPOINT s1", "ok"), s("a", ROWS, "ok"), s("a", "UPDATE t SET v = 3 WHERE id = 3"),
               s("a", "COMMIT", "ok"), s("b", ROWS, "ok")),
    t_schedule("SP2", s("a", "BEGIN"), s("a", "SAVEPOINT s"), s("a", "UPDATE t SET v = 1 WHERE id = 1"),
               s("a", "SAVEPOINT s"), s("a", "UPDATE t SET v = 2 WHERE id = 1"), s("a", "ROLLBACK TO s", "ok"),
               s("a", "SELECT v FROM t WHERE id = 1", "ok"), s("a", "ROLLBACK TO s", "ok"),
               s("a", "RELEASE s", "ok"), s("a", "ROLLBACK TO s", "ok"), s("a", "SELECT v FROM t WHERE id = 1", "ok"),
               s("a", "COMMIT", "ok")),
    t_schedule("SP3", s("a", "BEGIN"), s("a", "SAVEPOINT s"), s("a", "RELEASE SAVEPOINT s", "ok"),
               s("a", "ROLLBACK TO s", "3B001"), s("a", "SELECT 1", "25P02"), s("a", "ROLLBACK", "ok"),
               s("a", "BEGIN"), s("a", "RELEASE missing", "3B001"), s("a", "ROLLBACK", "ok")),
    t_schedule("SP4", s("a", "BEGIN"), s("a", "UPDATE t SET v = 1 WHERE id = 1"), s("a", "SAVEPOINT s"),
               s("a", "UPDATE t SET v = 2 WHERE id = 1"), s("a", "INSERT INTO t VALUES (1, 0)", "23505"),
               s("a", "SELECT v FROM t WHERE id = 1", "25P02"), s("a", "ROLLBACK TO s", "ok"),
               s("a", "SELECT v FROM t WHERE id = 1", "ok"), s("a", "COMMIT", "ok"), s("b", ROWS, "ok")),
    t_schedule("SP5", s("a", "SAVEPOINT s", "25P01"), s("a", "RELEASE SAVEPOINT s", "25P01"),
               s("a", "ROLLBACK TO SAVEPOINT s", "25P01"), s("a", ROWS, "ok")),
    t_schedule("SP6", s("a", "BEGIN"), s("a", "INSERT INTO t VALUES (4, 4)"), s("a", "SAVEPOINT s"),
               s("a", "INSERT INTO t VALUES (5, 5)"), s("a", "ROLLBACK TO s", "ok"), s("a", "SAVEPOINT r"),
               s("a", "INSERT INTO t VALUES (6, 6)"), s("a", "UPDATE t SET v = 7 WHERE id = 1"),
               s("a", "RELEASE r", "ok"), s("a", "COMMIT", "ok"), s("b", ROWS, "ok"),
               after_restart=(ROWS,)),
    t_schedule("SP7", s("a", "BEGIN"), s("a", "SAVEPOINT s"), s("a", "UPDATE t SET v = 7 WHERE id = 1"),
               s("a", "SAVEPOINT r"), s("a", "INSERT INTO t VALUES (4, 4)"), Step("a", "", action="disconnect"),
               s("b", ROWS, "ok"), s("b", "UPDATE t SET v = 8 WHERE id = 1", "ok"), s("b", ROWS, "ok")),
    t_schedule("SP8", s("a", "BEGIN"), s("a", "SAVEPOINT s"), s("a", "CREATE TABLE sp8 (id INTEGER PRIMARY KEY)", "ok"),
               s("a", "INSERT INTO sp8 VALUES (1)", "ok"), s("a", "ROLLBACK TO s", "ok"),
               s("a", "SELECT id FROM sp8", "42P01"), s("a", "ROLLBACK", "ok"), s("b", "SELECT id FROM sp8", "42P01")),
    t_schedule("SP9", s("a", "BEGIN"), s("a", "SAVEPOINT s"), s("a", "UPDATE t SET v = 1 WHERE id = 1", "ok"),
               s("b", "UPDATE t SET v = 2 WHERE id = 1", "blocked"), s("a", "ROLLBACK TO s", "ok"),
               s("a", "UPDATE t SET v = 5 WHERE id = 2", "ok"), s("a", "COMMIT", "ok")),
    # SP8b (step 3.7): nextval inside a rolled-back savepoint is not rolled back.
    t_schedule("SP8b", s("a", "CREATE SEQUENCE sp8b_seq", "ok"), s("a", "BEGIN"), s("a", "SAVEPOINT s"),
               s("a", "SELECT nextval('sp8b_seq')", "ok"), s("a", "ROLLBACK TO s", "ok"),
               s("a", "SELECT nextval('sp8b_seq')", "ok"), s("a", "ROLLBACK", "ok"),
               s("b", "SELECT nextval('sp8b_seq')", "ok"), s("b", "DROP SEQUENCE sp8b_seq", "ok")),
    # SQ1 (step 3.7): two sessions interleave nextval; currval is per session.
    t_schedule("SQ1", s("a", "CREATE SEQUENCE sq1_seq", "ok"), s("a", "SELECT nextval('sq1_seq')", "ok"),
               s("b", "SELECT currval('sq1_seq')", "55000"), s("b", "SELECT nextval('sq1_seq')", "ok"),
               s("a", "SELECT nextval('sq1_seq'), currval('sq1_seq')", "ok"), s("b", "SELECT currval('sq1_seq'), lastval()", "ok"),
               s("a", "BEGIN"), s("a", "SELECT nextval('sq1_seq')", "ok"), s("b", "SELECT nextval('sq1_seq')", "ok"),
               s("a", "ROLLBACK", "ok"), s("b", "DROP SEQUENCE sq1_seq", "ok")),
    # ---- M2 transactional DDL (each followed by a durable restart) ----
    t_schedule("DDL1", s("a", "BEGIN"), s("a", "CREATE TABLE ddl1 (id INTEGER PRIMARY KEY)", "ok"),
               s("a", "INSERT INTO ddl1 VALUES (1)", "ok"), s("b", "SELECT id FROM ddl1", "42P01"),
               s("a", "COMMIT", "ok"), s("b", "SELECT id FROM ddl1", "ok"),
               after_restart=("SELECT id FROM ddl1",)),
    t_schedule("DDL2", s("a", "BEGIN"), s("a", "CREATE TABLE ddl2 (id INTEGER PRIMARY KEY)", "ok"),
               s("a", "INSERT INTO ddl2 VALUES (1)", "ok"), s("a", "ROLLBACK", "ok"),
               s("b", "SELECT id FROM ddl2", "42P01"), s("b", "CREATE TABLE ddl2 (id INTEGER)", "ok"),
               s("b", "SELECT id FROM ddl2", "ok"), after_restart=("SELECT id FROM ddl2",)),
    t_schedule("DDL3", s("a", "BEGIN"), s("a", "DROP TABLE t", "ok"), s("b", ROWS, "blocked"),
               s("a", "ROLLBACK", "ok"), s("c", ROWS, "ok"), after_restart=(ROWS,)),
    t_schedule("DDL4", s("a", "BEGIN"), s("a", "DROP TABLE ddl4", "ok"), s("b", "SELECT id FROM ddl4", "blocked"),
               s("a", "COMMIT", "ok"), s("c", "SELECT id FROM ddl4", "42P01"),
               after_restart=("SELECT id FROM ddl4", ROWS),
               setup=T + ("CREATE TABLE ddl4 (id INTEGER PRIMARY KEY)", "INSERT INTO ddl4 VALUES (1)")),
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
            started = time.monotonic()
            record["outcome"] = execute(self.connection, sql)
            record["elapsed"] = time.monotonic() - started
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
    result = {"actor": step.actor, "sql": step.sql, "action": step.action, "blocked": record["blocked"],
              "completed": record["event"].is_set(), "outcome": record.get("outcome"),
              "transaction_status": record.get("transaction_status")}
    if step.action == "timed":
        result["fast"] = record.get("elapsed", 1e9) < FAST_SECONDS
    return result


def action_record():
    record = {"event": threading.Event(), "blocked": False, "outcome": None, "transaction_status": None}
    record["event"].set()
    return record


def run_schedule(psycopg, arguments, schedule, restart=None):
    wait = DEADLOCK_WAIT if "deadlock" in schedule.tags else BLOCK_WAIT
    actor_arguments = dict(arguments, options=ACTOR_OPTIONS)
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
                if step.action == "cancel":
                    actors[step.actor].connection.cancel_safe(timeout=3)
                    record = action_record()
                    time.sleep(wait)
                elif step.action == "disconnect":
                    actors.pop(step.actor).close()
                    record = action_record()
                    time.sleep(wait)
                else:
                    if step.actor not in actors:
                        actors[step.actor] = Actor(psycopg, actor_arguments)
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
        result = {"stage": "steps", "steps": results}
        if schedule.final:
            with psycopg.connect(**arguments) as connection:
                result["final"] = [{"sql": sql, "outcome": execute(connection, sql)} for sql in schedule.final]
        if schedule.after_restart:
            if restart is None:
                result["after_restart"] = "skipped: memory mode (requires --durable)"
            else:
                restart()
                with psycopg.connect(**arguments) as connection:
                    result["after_restart"] = [{"sql": sql, "outcome": execute(connection, sql)} for sql in schedule.after_restart]
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
        result["lock_probe"] = [step_result(step, record) for step, record in probe_records]
        return result
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
            if step.action == "timed":
                ok = ok and result.get("fast", False)
        else:
            ok = outcome.get("sqlstate") == step.expect
        if not ok:
            failures.append({"step": index, "expect": step.expect, "blocked": result["blocked"],
                             "sqlstate": outcome.get("sqlstate")})
    for result in reference["lock_probe"]:
        if result["blocked"] or (result["outcome"] or {}).get("sqlstate") != "00000":
            failures.append({"step": "lock_probe", "reason": "reference lock probe did not complete"})
    return failures


def expand(selection):
    """Comma-separated ids; `RC1-RC14` expands a numbered range."""
    ids = []
    for item in (part.strip() for part in selection.split(",")):
        if not item:
            continue
        match = re.fullmatch(r"([A-Z]+)(\d+)-\1(\d+)", item)
        if match:
            prefix, first, last = match[1], int(match[2]), int(match[3])
            ids += [f"{prefix}{number}" for number in range(first, last + 1)]
        else:
            ids.append(item)
    return ids


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule", required=True, help="comma-separated schedule ids or ranges (RC1-RC14): " + ",".join(SCHEDULES))
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--image", default="postgres:18-alpine", help="locally available immutable reference image (no automatic pull)")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".sig-cache" / "postgres-compat")
    parser.add_argument("--report", type=Path, default=ROOT / ".sig-cache" / "postgres-compat" / "isolation-report.json")
    parser.add_argument("--durable", action="store_true", help="run RAMSQL on a fresh durable WAL instead of --memory")
    parser.add_argument("--scram", action="store_true", help="run RAMSQL with a generated SCRAM-SHA-256 verifier")
    args = parser.parse_args()
    ids = expand(args.schedule)
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
            candidate = Candidate(binary, mode_arguments)
            candidate.start()
            try:
                for schedule_id in ids:
                    schedule = SCHEDULES[schedule_id]
                    reference_restart = (lambda: restart_native(native, metadata, psycopg)) if args.durable else None
                    candidate_restart = candidate.restart if args.durable else None
                    reference_result = run_schedule(psycopg, native, schedule, reference_restart)
                    failures = expectation_failures(schedule, reference_result)
                    if failures:
                        raise RuntimeError(f"reference contradicts schedule {schedule_id}: {failures}")
                    candidate_result = run_schedule(psycopg, candidate.connection, schedule, candidate_restart)
                    gaps = differences(reference_result, candidate_result)
                    report["schedules"].append({"id": schedule_id, "tags": list(schedule.tags), "match": not gaps,
                                                "differences": gaps, "postgres": reference_result,
                                                "ramsql": candidate_result})
                    print(("PASS" if not gaps else "GAP ") + " " + schedule_id + ("" if not gaps else ": " + gaps[0]["path"]), flush=True)
            finally:
                candidate.stop()
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
