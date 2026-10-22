"""Crash/restart, exclusive writer and corrupt-WAL server contracts (stdlib only).

Build RAMSQL, then run python tools/test_persistence.py. Every process uses
--insecure-local on loopback with a NEW test-owned WAL under sig-out. A crash is
an unconditional process kill after PostgreSQL acknowledged a durable commit.
Artifacts are retained for inspection; existing user databases are never opened.
"""

import argparse
from collections import deque
import json
import os
from pathlib import Path
import queue
import re
import socket
import struct
import subprocess
import threading
import time
import zlib

import test_server as wire


assertions = 0
cases = 0


def check(condition, message="persistence contract failed"):
    global assertions
    assertions += 1
    if not condition:
        raise AssertionError(message)


class DurableServer:
    def __init__(self, binary, wal):
        self.binary = binary
        self.wal = wal
        self.process = None
        self.errors = deque(maxlen=40)
        self.port = None

    def command(self):
        return [str(self.binary), "--serve", "--insecure-local", "--data", str(self.wal),
                "--port", "0", "--idle-ms", "30000", "--max-clients", "8"]

    def __enter__(self):
        self.process = subprocess.Popen(self.command(), stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, text=True, bufsize=1)
        output = queue.Queue()
        self.output_reader = threading.Thread(
            target=lambda: output.put(self.process.stdout.readline()), daemon=True)
        self.output_reader.start()

        def collect_errors():
            for line in self.process.stderr:
                self.errors.append(line.rstrip())

        self.error_reader = threading.Thread(target=collect_errors, daemon=True)
        self.error_reader.start()
        try:
            line = output.get(timeout=30)
            match = re.fullmatch(r"RAMSQL postgres 127\.0\.0\.1:(\d+)\n", line)
            check(match is not None, f"startup failed: {line!r}; errors={list(self.errors)!r}")
            self.port = int(match.group(1))
            check(0 < self.port <= 65535, "no ephemeral PostgreSQL listener")
            return self
        except BaseException:
            self.crash()
            raise

    def connect(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        pg = wire.PG(sock)
        try:
            pg.startup()
            return pg
        except BaseException:
            sock.close()
            raise

    def crash(self):
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.kill()  # SIGKILL on POSIX; TerminateProcess on Windows.
        self.process.wait(timeout=10)
        self.output_reader.join(timeout=2)
        self.error_reader.join(timeout=2)
        self.process.stdout.close()
        self.process.stderr.close()

    def __exit__(self, *_):
        self.crash()


def refuse(binary, wal, expected):
    global cases
    completed = subprocess.run(DurableServer(binary, wal).command(), capture_output=True,
                               text=True, timeout=15)
    check(completed.returncode != 0, "unsafe WAL startup succeeded")
    check("RAMSQL postgres" not in completed.stdout, "listener bound before WAL validation")
    check(any(name in completed.stderr for name in expected),
          f"wrong refusal reason: {completed.stderr!r}")
    cases += 1


def offline_backup(binary, source, target, expected_error=None):
    global cases
    completed = subprocess.run([str(binary), "--data", str(source), "--backup-to", str(target)],
                               capture_output=True, text=True, timeout=20)
    check("RAMSQL postgres" not in completed.stdout, "offline backup opened a listener")
    if expected_error is None:
        check(completed.returncode == 0, f"backup failed: {completed.stderr!r}")
        check(target.is_file(), "successful backup did not publish a destination")
    else:
        check(completed.returncode != 0, "unsafe offline backup succeeded")
        check(any(name in completed.stderr for name in expected_error),
              f"wrong backup refusal: {completed.stderr!r}")
    cases += 1


def assert_database(server, expected):
    pg = server.connect()
    try:
        pg.ok("SELECT * FROM durable ORDER BY id", expected)
    finally:
        pg.close()


def record(sequence, sql):
    statement = sql.encode("utf-8")
    payload = struct.pack("<I", len(statement)) + statement
    header = bytearray(struct.pack("<8sHHIQII", b"RAMSQLW1", 1, 1, len(payload), sequence, 0, 0))
    struct.pack_into("<I", header, 24, zlib.crc32(header))
    trailer = struct.pack("<I4sQ", zlib.crc32(payload), b"CMIT", sequence)
    return bytes(header) + payload + trailer


def last_sequence(data):
    position = 0
    sequence = 0
    while position < len(data):
        check(data[position:position + 8] == b"RAMSQLW1", "unknown WAL magic")
        length, sequence = struct.unpack_from("<IQ", data, position + 12)
        position += 48 + length
    check(position == len(data), "unexpected WAL framing")
    return sequence


def main():
    global cases
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--binary", type=Path, default=root / "sig-out" / "bin" /
                        ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--artifacts", type=Path)
    options = parser.parse_args()
    check(options.binary.is_file(), f"missing RAMSQL binary: {options.binary}")
    check(1 <= options.cycles <= 100, "cycles must be between 1 and 100")
    binary = options.binary.resolve()
    artifacts = options.artifacts or root / "sig-out" / f"persistence-crash-{time.time_ns()}"
    artifacts.mkdir(parents=True, exist_ok=False)
    wal = artifacts / "database.wal"
    expected = [["1", "autocommit"], ["2", "transaction"], ["3", "literal; newline\nkept"]]
    with DurableServer(binary, wal) as server:
        pg = server.connect()
        try:
            pg.ok("CREATE TABLE durable (id INTEGER PRIMARY KEY, value TEXT)")
            pg.ok("INSERT INTO durable VALUES (1, 'autocommit')")
            pg.ok("BEGIN", status=b"T")
            pg.ok("INSERT INTO durable VALUES (2, 'transaction')", status=b"T")
            pg.ok("INSERT INTO durable VALUES (3, 'literal; newline\nkept')", status=b"T")
            pg.ok("COMMIT")
            pg.ok("BEGIN", status=b"T")
            pg.ok("INSERT INTO durable VALUES (999, 'rollback')", status=b"T")
            pg.ok("ROLLBACK")
            refuse(binary, wal, ("DataDirectoryBusy",))
            blocked_backup = artifacts / "blocked-live-backup.wal"
            offline_backup(binary, wal, blocked_backup, ("DataDirectoryBusy",))
            check(not blocked_backup.exists(), "backup of an active writer published data")
            pg.ok("BEGIN", status=b"T")
            pg.ok("INSERT INTO durable VALUES (777, 'uncommitted crash')", status=b"T")
            server.crash()
        finally:
            pg.close(terminate=False)
    cases += 1
    for cycle in range(options.cycles):
        with DurableServer(binary, wal) as server:
            assert_database(server, expected)
            pg = server.connect()
            try:
                row = [str(10 + cycle), f"crash {cycle}"]
                pg.ok(f"INSERT INTO durable VALUES ({row[0]}, '{row[1]}')")
                expected.append(row)
                server.crash()  # Kill immediately after ReadyForQuery acknowledgement.
            finally:
                pg.close(terminate=False)
        cases += 1
    with DurableServer(binary, wal) as server:
        assert_database(server, expected)
    cases += 1
    original = wal.read_bytes()
    sequence = last_sequence(original)
    backup_path = artifacts / "restorable-backup.wal"
    offline_backup(binary, wal, backup_path)
    check(backup_path.read_bytes() == original, "backup is not the complete original WAL")
    offline_backup(binary, wal, backup_path, ("FileRenameFailed",))
    check(backup_path.read_bytes() == original, "existing backup was overwritten")
    with DurableServer(binary, backup_path) as server:
        assert_database(server, expected)
        pg = server.connect()
        try:
            pg.ok("INSERT INTO durable VALUES (101, 'restored independent database')")
        finally:
            pg.close()
    check(wal.read_bytes() == original, "writing restored backup changed original database")
    cases += 1
    absent_source = artifacts / "missing-source.wal"
    absent_target = artifacts / "missing-source-backup.wal"
    offline_backup(binary, absent_source, absent_target, ("FileOpenFailed",))
    check(not absent_source.exists() and not absent_target.exists(), "missing backup source became an empty database")
    occupied_target = artifacts / "occupied-temp-backup.wal"
    occupied_temporary = Path(str(occupied_target) + ".tmp")
    occupied_temporary.write_bytes(b"pre-existing artifact")
    offline_backup(binary, wal, occupied_target, ("FileOpenFailed",))
    check(occupied_temporary.read_bytes() == b"pre-existing artifact" and not occupied_target.exists(),
          "backup overwrote a pre-existing temporary")
    partial = record(sequence + 1, "INSERT INTO durable VALUES (1000, 'torn append')")
    # Incomplete next commits must be repaired at every framing region while
    # preserving every already acknowledged row and exact committed WAL bytes.
    cuts = sorted({1, 7, 16, 31, 32, 33, len(partial) // 2, len(partial) - 17,
                   len(partial) - 16, len(partial) - 8, len(partial) - 1})
    for cut in cuts:
        candidate = artifacts / f"torn-{cut}.wal"
        candidate.write_bytes(original + partial[:cut])
        with DurableServer(binary, candidate) as server:
            assert_database(server, expected)
            check(candidate.read_bytes() == original, "tail repair modified committed records")
        cases += 1
    for offset in (0, 24, 33, len(original) - 1):
        candidate = artifacts / f"corrupt-{offset}.wal"
        corrupt = bytearray(original)
        corrupt[offset] ^= 1
        candidate.write_bytes(corrupt)
        refuse(binary, candidate, ("CorruptLogHeader", "CorruptLogRecord"))
        check(candidate.read_bytes() == corrupt, "corrupt WAL was silently rewritten")
        corrupt_backup = artifacts / f"corrupt-{offset}-backup.wal"
        offline_backup(binary, candidate, corrupt_backup, ("CorruptLogHeader", "CorruptLogRecord"))
        check(not corrupt_backup.exists(), "corrupt WAL backup was published")
    semantic = artifacts / "semantic-corruption.wal"
    bad_semantic = original + record(sequence + 1, "INSERT INTO missing VALUES (1)")
    semantic.write_bytes(bad_semantic)
    refuse(binary, semantic, ("TableNotFound", "UnknownTable"))
    check(semantic.read_bytes() == bad_semantic, "semantic replay failure rewrote WAL")
    semantic_backup = artifacts / "semantic-backup.wal"
    offline_backup(binary, semantic, semantic_backup, ("TableNotFound", "UnknownTable"))
    check(not semantic_backup.exists(), "semantically invalid backup was published")
    check(wal.read_bytes() == original, "test modified its baseline database unexpectedly")
    print(json.dumps({"suite": "persistence_crash", "pass": True, "cases": cases,
                      "assertions": assertions + wire.assertions, "crash_cycles": options.cycles,
                      "artifacts": str(artifacts.resolve())}))


if __name__ == "__main__":
    main()
