"""Crash differential suite: both engines are killed, restarted and compared.

Design §5.12/§5.13(d) and §8. The reference runs in an owned Docker container
created without --rm; a crash is `docker kill` (SIGKILL, no shutdown
checkpoint) followed by `docker start`, and cleanup removes only the recorded
container id. RAMSQL runs on a fresh durable WAL with a generated SCRAM
verifier and is killed with Popen.kill (TerminateProcess on Windows, SIGKILL
elsewhere), then restarted on the same --data.

    CR1  three committed identity inserts, kill, restart, insert: the same id
         on both engines (34 = first value + SEQ_LOG_VALS + 1).
    CR2  CREATE SEQUENCE s; one autocommit SELECT nextval('s'); kill; restart;
         SELECT nextval('s'): the same value on both engines (the SELECT that
         advanced the sequence was durable before its reply).

    python tools/test_postgres_crash_compat.py --image postgres:18-alpine --report report.json

Exit codes: 0 every case matches; 1 differences; 2 harness error.
Matching is necessary evidence, not proof of full crash-recovery parity.
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
import secrets
import subprocess
import sys
import threading
import time
import uuid

from test_postgres_compat import ROOT, candidate_mode, differences, docker, execute


@dataclass(frozen=True)
class CrashCase:
    name: str
    feature: str
    setup: tuple[str, ...]
    before: tuple[str, ...]
    after: tuple[str, ...]
    tables: tuple[str, ...] = ()
    sequences: tuple[str, ...] = ()


CASES = (
    CrashCase("CR1", "identity log-ahead after a crash (SEQ_LOG_VALS)",
              setup=("CREATE TABLE crash_ident (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label TEXT)",),
              before=("INSERT INTO crash_ident (label) VALUES ('a') RETURNING id",
                      "INSERT INTO crash_ident (label) VALUES ('b') RETURNING id",
                      "INSERT INTO crash_ident (label) VALUES ('c') RETURNING id"),
              after=("INSERT INTO crash_ident (label) VALUES ('after') RETURNING id",
                     "SELECT id, label FROM crash_ident ORDER BY id"),
              tables=("crash_ident",)),
    CrashCase("CR2", "an autocommit SELECT nextval is durable before its reply",
              setup=("CREATE SEQUENCE crash_seq",),
              before=("SELECT nextval('crash_seq')",),
              after=("SELECT nextval('crash_seq')",),
              sequences=("crash_seq",)),
)


def wait_ready(psycopg, connection, what, seconds=40):
    deadline = time.monotonic() + seconds
    while True:
        try:
            with psycopg.connect(**connection) as session:
                session.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{what} did not become ready within {seconds} seconds") from None
            time.sleep(0.15)


class OwnedReference:
    """A PostgreSQL container this harness created (no --rm); only its id is
    ever killed, started or removed."""

    def __init__(self, image, psycopg):
        self.image, self.psycopg = image, psycopg
        self.container = None
        self.connection = None
        self.metadata = {}

    def start(self):
        info = json.loads(docker("image", "inspect", self.image))[0]
        password = secrets.token_urlsafe(32)
        environment = os.environ.copy()
        environment["POSTGRES_PASSWORD"] = password
        self.container = docker(
            "run", "--detach", "--name", "ramsql-pg-crash-" + uuid.uuid4().hex[:12],
            "--publish", "127.0.0.1::5432",
            "--env", "POSTGRES_PASSWORD", "--env", "POSTGRES_USER=ramsql", "--env", "POSTGRES_DB=ramsql",
            "--env", "POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256",
            info["Id"], env=environment)
        self.connection = dict(host="127.0.0.1", port=self.port(), user="ramsql", dbname="ramsql", password=password,
                               sslmode="disable", connect_timeout=3, autocommit=True,
                               application_name="ramsql-crash-compat", options="-c statement_timeout=4000")
        wait_ready(self.psycopg, self.connection, "native PostgreSQL")
        with self.psycopg.connect(**self.connection) as session:
            version = session.execute("SELECT version()").fetchone()[0]
        self.metadata = {"image_requested": self.image, "image_id": info["Id"], "image_digests": info.get("RepoDigests", []),
                         "container_id": self.container, "server_version": version}

    def port(self):
        return int(docker("port", self.container, "5432/tcp").splitlines()[0].rsplit(":", 1)[1])

    def crash(self):
        docker("kill", self.container)
        docker("start", self.container)
        self.connection["port"] = self.port()
        wait_ready(self.psycopg, self.connection, "native PostgreSQL after docker kill")

    def remove(self):
        if self.container is not None:
            subprocess.run(["docker", "rm", "--force", "--volumes", self.container], capture_output=True, text=True, timeout=30)
            self.container = None


class CrashableRamsql:
    """RAMSQL on one durable WAL; crash() kills the process and restarts it."""

    def __init__(self, binary, arguments, psycopg):
        self.binary, self.arguments, self.psycopg = binary, arguments, psycopg
        self.process = None
        self.connection = None

    def start(self):
        auth = self.arguments["auth"]
        command = [str(self.binary), "--serve", "--port", "0", "--data", str(self.arguments["data_path"]),
                   "--auth-user", auth[0], "--auth-verifier-file", str(auth[1])]
        command += list(self.arguments.get("extra_arguments", ()))
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
        lines, errors = queue.Queue(), []
        threading.Thread(target=lambda: [lines.put(line) for line in self.process.stdout] + [lines.put(None)], daemon=True).start()
        threading.Thread(target=lambda: [errors.append(line.rstrip()) for line in self.process.stderr], daemon=True).start()
        deadline = time.monotonic() + 20
        port = None
        while port is None:
            try:
                line = lines.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                raise RuntimeError("RAMSQL startup/recovery exceeded 20s: " + "; ".join(errors[-10:])) from None
            if line is None:
                raise RuntimeError("RAMSQL exited before startup: " + "; ".join(errors[-10:]))
            match = re.fullmatch(r"RAMSQL postgres 127\.0\.0\.1:(\d+)\s*", line)
            if match:
                port = int(match[1])
        self.connection = dict(host="127.0.0.1", port=port, user=auth[0], dbname="ramsql", password=auth[2],
                               sslmode="disable", connect_timeout=3, autocommit=True,
                               application_name="ramsql-crash-compat", options="-c statement_timeout=4000")

    def crash(self):
        self.process.kill()
        self.process.wait(timeout=10)
        self.start()

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)


def run_list(psycopg, connection, statements):
    with psycopg.connect(**connection) as session:
        return [{"sql": sql, "outcome": execute(session, sql)} for sql in statements]


def cleanup(psycopg, connection, case):
    with psycopg.connect(**connection) as session:
        for name in case.tables:
            execute(session, "DROP TABLE " + name)
        for name in case.sequences:
            execute(session, "DROP SEQUENCE " + name)


def run_side(psycopg, side, case):
    cleanup(psycopg, side.connection, case)
    result = {"setup": run_list(psycopg, side.connection, case.setup)}
    if any(event["outcome"]["sqlstate"] != "00000" for event in result["setup"]):
        result["stage"] = "setup"
        return result
    result["before"] = run_list(psycopg, side.connection, case.before)
    side.crash()
    result["after"] = run_list(psycopg, side.connection, case.after)
    cleanup(psycopg, side.connection, case)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--image", default="postgres:18-alpine", help="locally available reference image (no automatic pull)")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".sig-cache" / "postgres-compat")
    parser.add_argument("--report", type=Path, default=ROOT / ".sig-cache" / "postgres-compat" / "crash-report.json")
    parser.add_argument("--case", action="append", help="run only these case names (default: all)")
    args = parser.parse_args()
    import psycopg

    binary = args.binary.resolve()
    selected = [case for case in CASES if not args.case or case.name in args.case]
    report = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "finite crash differential cases; matching does not establish full crash-recovery parity",
              "ramsql_binary": str(binary), "ramsql_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "client": {"name": "psycopg", "version": psycopg.__version__},
              "crash": {"postgres": "docker kill + docker start (owned container, no --rm)",
                        "ramsql": "process kill (TerminateProcess on Windows) + restart on the same --data"},
              "cases": []}
    reference = OwnedReference(args.image, psycopg)
    candidate = None
    try:
        reference.start()
        report["reference"] = reference.metadata
        with candidate_mode(args.work_dir, durable=True, scram=True) as (arguments, mode):
            report["mode"] = mode
            candidate = CrashableRamsql(binary, arguments, psycopg)
            candidate.start()
            try:
                for case in selected:
                    postgres = run_side(psycopg, reference, case)
                    ramsql = run_side(psycopg, candidate, case)
                    gaps = differences(postgres, ramsql)
                    report["cases"].append({"name": case.name, "feature": case.feature, "match": not gaps,
                                            "differences": gaps, "postgres": postgres, "ramsql": ramsql})
                    print(("PASS" if not gaps else "GAP ") + " " + case.name + ("" if not gaps else ": " + gaps[0]["path"]))
            finally:
                candidate.stop()
        mismatching = sum(not case["match"] for case in report["cases"])
        report["summary"] = {"executed": len(report["cases"]), "matching": len(report["cases"]) - mismatching,
                             "mismatching": mismatching, "passed": mismatching == 0 and bool(report["cases"])}
        result = 0 if report["summary"]["passed"] else 1
    except Exception as error:
        report["harness_error"] = {"class": type(error).__name__, "message": str(error)}
        report["summary"] = {"executed": len(report["cases"]), "passed": False, "incomplete": True}
        print("HARNESS ERROR: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        result = 2
    finally:
        reference.remove()
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    print("Report: " + str(args.report.resolve()))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
