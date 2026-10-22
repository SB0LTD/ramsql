"""Differential PostgreSQL drop-in gate against an isolated native PostgreSQL.

This is deliberately broader than RAMSQL's documented SQL subset. A nonzero exit
for compatibility differences is an expected, useful result while gaps remain.
Passing these probes is necessary, but is not a proof of full PostgreSQL parity.
The native reference uses a locally available Docker image, an OS-chosen loopback
port and a generated password passed through the environment. The owned container
and its anonymous volume are removed on exit; other containers are never touched.

    python tools/test_postgres_compat.py
    python tools/test_postgres_compat.py --legacy-cli  # previous 0.2 binary

Exit codes: 0 all probes match; 1 compatibility gaps; 2 harness/infrastructure error.
"""

from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".sig-cache" / "client-deps"))

CREATE = "CREATE TABLE compat (id INTEGER PRIMARY KEY, flag BOOLEAN, label TEXT)"
BASE = (
    CREATE,
    "INSERT INTO compat VALUES (1, TRUE, 'one')",
    "INSERT INTO compat VALUES (2, FALSE, NULL)",
    "INSERT INTO compat VALUES (3, NULL, 'three')",
)


@dataclass(frozen=True)
class Case:
    name: str
    feature: str
    setup: tuple[str, ...] = BASE
    statements: tuple[str, ...] = ()
    operation: str = "sql"


CASES = (
    Case("boolean_text", "typed result metadata", statements=("SELECT flag, label FROM compat WHERE id = 1",)),
    Case("integer_oid", "INTEGER wire OID", statements=("SELECT id FROM compat ORDER BY id",)),
    Case("point_select", "primary key lookup", statements=("SELECT label FROM compat WHERE id = 3",)),
    Case("filter_limit", "filters, order and limit", statements=("SELECT label FROM compat WHERE id > 1 AND id < 4 ORDER BY id DESC LIMIT 1",)),
    Case("crud", "CRUD command results", statements=(
        "INSERT INTO compat VALUES (4, FALSE, 'four')",
        "UPDATE compat SET label = 'changed' WHERE id = 4",
        "SELECT label FROM compat WHERE id = 4",
        "DELETE FROM compat WHERE id = 4",
        "SELECT label FROM compat WHERE id = 4",
    )),
    Case("duplicate_key", "SQLSTATE 23505", statements=("INSERT INTO compat VALUES (1, FALSE, 'duplicate')",)),
    Case("missing_table", "SQLSTATE 42P01", statements=("SELECT * FROM absent",)),
    Case("missing_column", "SQLSTATE 42703", statements=("SELECT missing FROM compat",)),
    Case("not_null", "SQLSTATE 23502", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, label TEXT NOT NULL)",), statements=("INSERT INTO compat VALUES (1, NULL)",)),
    Case("integer_positive_overflow", "32-bit INTEGER range", setup=(CREATE,), statements=("INSERT INTO compat VALUES (2147483648, TRUE, 'overflow')",)),
    Case("integer_negative_overflow", "32-bit INTEGER range", setup=(CREATE,), statements=("INSERT INTO compat VALUES (-2147483649, TRUE, 'overflow')",)),
    Case("bigint", "64-bit BIGINT type", setup=("CREATE TABLE compat (id BIGINT PRIMARY KEY, label TEXT)",), statements=("INSERT INTO compat VALUES (9223372036854775807, 'max')", "SELECT id FROM compat")),
    Case("numeric", "exact NUMERIC type", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, amount NUMERIC(18,4))",), statements=("INSERT INTO compat VALUES (1, 12.3456)", "SELECT amount FROM compat")),
    Case("timestamp", "TIMESTAMPTZ type", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, happened TIMESTAMPTZ)",), statements=("INSERT INTO compat VALUES (1, '2026-10-22T12:00:00Z')", "SELECT happened FROM compat")),
    Case("jsonb", "JSONB type and operators", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, payload JSONB)",), statements=("INSERT INTO compat VALUES (1, '{\"kind\":\"event\"}')", "SELECT payload ->> 'kind' FROM compat")),
    Case("null_ascending", "default NULLS LAST for ASC", statements=("SELECT label FROM compat ORDER BY label ASC",)),
    Case("null_descending", "default NULLS FIRST for DESC", statements=("SELECT label FROM compat ORDER BY label DESC",)),
    Case("explicit_null_order", "NULLS FIRST syntax", statements=("SELECT label FROM compat ORDER BY label ASC NULLS FIRST",)),
    Case("aggregate", "COUNT and SUM aggregates", statements=("SELECT COUNT(*), SUM(id) FROM compat",)),
    Case("group_by", "GROUP BY aggregation", statements=("SELECT flag, COUNT(*) FROM compat GROUP BY flag ORDER BY flag",)),
    Case("join", "equi-join", setup=BASE + ("CREATE TABLE aux (id INTEGER PRIMARY KEY, label TEXT)", "INSERT INTO aux VALUES (1, 'joined')"), statements=("SELECT compat.label, aux.label FROM compat JOIN aux ON compat.id = aux.id",)),
    Case("cte", "common table expressions", statements=("WITH selected AS (SELECT label FROM compat WHERE id = 1) SELECT label FROM selected",)),
    Case("or_predicate", "OR predicates", statements=("SELECT label FROM compat WHERE id = 1 OR id = 3 ORDER BY id",)),
    Case("distinct", "DISTINCT", statements=("SELECT DISTINCT flag FROM compat ORDER BY flag",)),
    Case("returning", "INSERT RETURNING", statements=("INSERT INTO compat VALUES (4, FALSE, 'four') RETURNING label",)),
    Case("upsert", "ON CONFLICT", statements=("INSERT INTO compat VALUES (1, FALSE, 'upsert') ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label RETURNING label",)),
    Case("defaults", "DEFAULT values", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, label TEXT DEFAULT 'default')",), statements=("INSERT INTO compat (id) VALUES (1)", "SELECT label FROM compat")),
    Case("identity", "identity columns", setup=("CREATE TABLE compat (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label TEXT)",), statements=("INSERT INTO compat (label) VALUES ('generated') RETURNING id",)),
    Case("pg_type", "pg_catalog type discovery", setup=(), statements=("SELECT oid, typname FROM pg_catalog.pg_type WHERE typname = 'int4'",)),
    Case("information_schema", "information_schema discovery", statements=("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'compat' ORDER BY table_name",)),
    Case("read_your_writes", "transaction visibility", statements=("BEGIN", "UPDATE compat SET label = 'private' WHERE id = 1", "SELECT label FROM compat WHERE id = 1", "ROLLBACK", "SELECT label FROM compat WHERE id = 1")),
    Case("failed_transaction", "failed transaction SQLSTATE 25P02", operation="failed_transaction"),
    Case("savepoint", "SAVEPOINT and ROLLBACK TO", statements=("BEGIN", "SAVEPOINT item", "UPDATE compat SET label = 'temporary' WHERE id = 1", "ROLLBACK TO SAVEPOINT item", "SELECT label FROM compat WHERE id = 1", "COMMIT")),
    Case("copy", "COPY FROM STDIN", operation="copy"),
    Case("prepared", "extended query parameters", operation="prepared"),
    Case("binary", "binary result formats", operation="binary"),
    Case("read_committed", "default READ COMMITTED statement snapshots", operation="read_committed"),
)


def docker(*arguments, env=None):
    completed = subprocess.run(["docker", *arguments], check=True, capture_output=True, text=True, env=env, timeout=90)
    return completed.stdout.strip()


@contextmanager
def native_postgres(image, psycopg):
    info = json.loads(docker("image", "inspect", image))[0]
    name = "ramsql-pg-compat-" + uuid.uuid4().hex[:12]
    password = secrets.token_urlsafe(32)
    environment = os.environ.copy()
    environment["POSTGRES_PASSWORD"] = password
    container = None
    try:
        # Pass only the environment variable NAME on the command line, so the
        # generated password never appears in process arguments or the report.
        container = docker(
            "run", "--detach", "--rm", "--name", name,
            "--publish", "127.0.0.1::5432",
            "--env", "POSTGRES_PASSWORD", "--env", "POSTGRES_USER=ramsql",
            "--env", "POSTGRES_DB=ramsql",
            "--env", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
            info["Id"], env=environment,
        )
        endpoint = docker("port", container, "5432/tcp")
        port = int(endpoint.rsplit(":", 1)[1])
        connection = dict(host="127.0.0.1", port=port, user="ramsql", dbname="ramsql", password=password,
                          sslmode="disable", connect_timeout=3, autocommit=True,
                          application_name="ramsql-postgres-compat", options="-c statement_timeout=4000")
        deadline = time.monotonic() + 40
        while True:
            try:
                with psycopg.connect(**connection) as reference:
                    version = reference.execute("SELECT version()").fetchone()[0]
                    version_number = reference.execute("SHOW server_version_num").fetchone()[0]
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("native PostgreSQL did not become ready within 40 seconds") from None
                time.sleep(0.15)
        metadata = {"image_requested": image, "image_id": info["Id"], "image_digests": info.get("RepoDigests", []),
                    "platform": info["Os"] + "/" + info["Architecture"],
                    "server_version": version, "server_version_num": version_number}
        yield connection, metadata
    finally:
        environment.pop("POSTGRES_PASSWORD", None)
        if container is not None:
            # This exact ID came from the owned `docker run`, never enumeration.
            subprocess.run(["docker", "rm", "--force", "--volumes", container], capture_output=True, text=True, timeout=30)


@contextmanager
def running_ramsql(binary, legacy_cli=False, extra_arguments=(), *, data_path=None):
    command = [str(binary), "--serve", "--port", "0"]
    if data_path is not None:
        command += ["--data", str(data_path), "--insecure-local"]
    elif not legacy_cli:
        command += ["--memory", "--insecure-local"]
    command += extra_arguments
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    output = queue.Queue()
    errors = deque(maxlen=30)

    def read_output():
        for line in process.stdout:
            output.put(line)
        output.put(None)

    def read_errors():
        for line in process.stderr:
            errors.append(line.rstrip())

    threading.Thread(target=read_output, daemon=True).start()
    threading.Thread(target=read_errors, daemon=True).start()
    try:
        deadline = time.monotonic() + 20
        port = None
        while port is None:
            line = output.get(timeout=max(0.01, deadline - time.monotonic()))
            if line is None:
                raise RuntimeError("RAMSQL exited before startup: " + "; ".join(errors))
            match = re.fullmatch(r"RAMSQL postgres 127\.0\.0\.1:(\d+)\s*", line)
            if match:
                port = int(match[1])
            elif time.monotonic() >= deadline:
                raise RuntimeError("RAMSQL did not report a PostgreSQL loopback port")
        yield dict(host="127.0.0.1", port=port, user="ramsql", dbname="ramsql", password="",
                   sslmode="disable", connect_timeout=3, autocommit=True,
                   application_name="ramsql-postgres-compat", options="-c statement_timeout=4000")
        if process.poll() is not None:
            raise RuntimeError("RAMSQL exited during differential probes: " + "; ".join(errors))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"hex": bytes(value).hex()}
    return str(value)


def error_result(error):
    return {"sqlstate": getattr(error, "sqlstate", None) or "CLIENT_ERROR",
            "error_class": type(error).__name__, "message": str(error).splitlines()[0]}


def execute(connection, statement, params=None, *, binary=False, prepare=None):
    try:
        with connection.cursor(binary=binary) as cursor:
            cursor.execute(statement, params, prepare=prepare)
            columns = [{"name": column.name, "oid": column.type_code} for column in (cursor.description or ())]
            rows = json_value(cursor.fetchall()) if cursor.description else []
            return {"sqlstate": "00000", "columns": columns, "rows": rows,
                    "rowcount": cursor.rowcount, "status": cursor.statusmessage}
    except Exception as error:
        return error_result(error)


def cleanup(connection):
    # Use syntax supported by both engines; optional IF EXISTS is itself a
    # separate compatibility feature. These tables belong only to this harness.
    execute(connection, "ROLLBACK")
    for name in ("aux", "compat"):
        execute(connection, "DROP TABLE " + name)


def run_case(psycopg, connection_arguments, case):
    events = []
    with psycopg.connect(**connection_arguments) as connection:
        cleanup(connection)
        try:
            for index, statement in enumerate(case.setup):
                outcome = execute(connection, statement)
                if outcome["sqlstate"] != "00000":
                    return {"stage": "setup", "index": index, "sql": statement, "outcome": outcome}
            if case.operation == "sql":
                for statement in case.statements:
                    outcome = execute(connection, statement)
                    events.append({"sql": statement, "outcome": outcome})
                    if outcome["sqlstate"] != "00000":
                        break
            elif case.operation == "failed_transaction":
                for statement in ("BEGIN", "SELECT missing FROM compat", "SELECT label FROM compat", "ROLLBACK"):
                    events.append({"sql": statement, "outcome": execute(connection, statement)})
            elif case.operation == "copy":
                try:
                    with connection.cursor() as cursor:
                        with cursor.copy("COPY compat (id, label) FROM STDIN") as copier:
                            copier.write_row((4, "copied"))
                    outcome = execute(connection, "SELECT label FROM compat WHERE id = 4")
                except Exception as error:
                    outcome = error_result(error)
                events.append({"sql": "COPY compat (id, label) FROM STDIN", "outcome": outcome})
            elif case.operation in ("prepared", "binary"):
                for identity in (1, 3):
                    statement = "SELECT flag, label FROM compat WHERE id = %s"
                    outcome = execute(connection, statement, (identity,), binary=case.operation == "binary", prepare=True)
                    events.append({"sql": statement, "parameters": [identity], "outcome": outcome})
                    if outcome["sqlstate"] != "00000":
                        break
            elif case.operation == "read_committed":
                # The selected column is TEXT on both sides, so this isolates
                # visibility and progress rather than the separate INTEGER OID gap.
                with psycopg.connect(**connection_arguments) as writer:
                    for actor, statement in (
                        (connection, "BEGIN"),
                        (connection, "SELECT label FROM compat WHERE id = 1"),
                        (writer, "UPDATE compat SET label = 'visible' WHERE id = 1"),
                        (connection, "SELECT label FROM compat WHERE id = 1"),
                        (connection, "COMMIT"),
                    ):
                        events.append({"sql": statement, "actor": "writer" if actor is writer else "reader",
                                       "outcome": execute(actor, statement)})
            else:
                raise ValueError("unknown probe operation " + case.operation)
            return {"stage": "statements", "events": events}
        finally:
            cleanup(connection)


def differences(native, candidate, path=""):
    # Error text and exception classes are vendor-specific; callers rely on the
    # SQLSTATE. Success compares row values, OIDs, column names and command tags.
    if isinstance(native, dict) and isinstance(candidate, dict):
        if "stage" in native and "stage" in candidate and native["stage"] != candidate["stage"]:
            result = [{"path": path + "/stage", "postgres": native["stage"], "ramsql": candidate["stage"]}]
            for side, value in (("postgres", native), ("ramsql", candidate)):
                if value["stage"] == "setup":
                    result.append({"path": path + "/" + side + "/setup", "sql": value["sql"],
                                   "sqlstate": value["outcome"]["sqlstate"]})
            return result
        if "sqlstate" in native and "sqlstate" in candidate:
            if native["sqlstate"] != candidate["sqlstate"]:
                return [{"path": path + "/sqlstate", "postgres": native["sqlstate"], "ramsql": candidate["sqlstate"]}]
            if native["sqlstate"] != "00000":
                return []
        result = []
        for key in sorted(set(native) | set(candidate)):
            if key in ("error_class", "message"):
                continue
            result += differences(native.get(key), candidate.get(key), path + "/" + key)
        return result
    if isinstance(native, list) and isinstance(candidate, list):
        result = []
        if len(native) != len(candidate):
            result.append({"path": path + "/length", "postgres": len(native), "ramsql": len(candidate)})
        for index, (left, right) in enumerate(zip(native, candidate)):
            result += differences(left, right, path + "/" + str(index))
        return result
    if type(native) is not type(candidate) or native != candidate:
        return [{"path": path, "postgres": native, "ramsql": candidate}]
    return []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--image", default="postgres:18-alpine", help="locally available immutable reference image (no automatic pull)")
    parser.add_argument("--report", type=Path, default=ROOT / "compatibility" / "postgres-report.json")
    parser.add_argument("--legacy-cli", action="store_true", help="run a pre-secure-default 0.2 binary for baseline discovery")
    parser.add_argument("--ramsql-arg", action="append", default=[], help="extra RAMSQL argument; use --ramsql-arg=--flag")
    args = parser.parse_args()
    import psycopg

    started = datetime.now(timezone.utc).isoformat()
    binary = args.binary.resolve()
    hasher = hashlib.sha256()
    with binary.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    report = {"schema": 1, "started_utc": started, "scope": "finite differential compatibility gate; passing does not establish full PostgreSQL parity",
              "ramsql_binary": str(binary), "ramsql_sha256": hasher.hexdigest(),
              "ramsql_legacy_cli": args.legacy_cli,
              "client": {"name": "psycopg", "version": psycopg.__version__}, "probes": []}
    try:
        with native_postgres(args.image, psycopg) as (native, metadata):
            report["reference"] = metadata
            with running_ramsql(args.binary.resolve(), args.legacy_cli, args.ramsql_arg) as candidate:
                for case in CASES:
                    reference_result = run_case(psycopg, native, case)
                    candidate_result = run_case(psycopg, candidate, case)
                    gaps = differences(reference_result, candidate_result)
                    report["probes"].append({"name": case.name, "feature": case.feature,
                                             "match": not gaps, "differences": gaps,
                                             "postgres": reference_result, "ramsql": candidate_result})
                    print(("PASS" if not gaps else "GAP ") + " " + case.name + ("" if not gaps else ": " + gaps[0]["path"]))
        mismatching = sum(not probe["match"] for probe in report["probes"])
        report["summary"] = {"probes": len(report["probes"]), "matching": len(report["probes"]) - mismatching,
                             "mismatching": mismatching, "drop_in_gate_passed": mismatching == 0}
        result = 1 if mismatching else 0
    except Exception as error:
        report["harness_error"] = {"class": type(error).__name__, "message": str(error)}
        report["summary"] = {"probes": len(report["probes"]), "drop_in_gate_passed": False, "incomplete": True}
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
