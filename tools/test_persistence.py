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
    def __init__(self, binary, wal, create=False, cwd=None):
        self.binary = binary
        self.wal = wal
        self.create = create
        self.cwd = cwd
        self.process = None
        self.errors = deque(maxlen=40)
        self.port = None

    def command(self):
        return [str(self.binary), "--serve", "--insecure-local", "--data", str(self.wal),
                *(["--create-data"] if self.create else []),
                "--port", "0", "--idle-ms", "30000", "--max-clients", "8"]

    def __enter__(self):
        self.process = subprocess.Popen(self.command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, bufsize=1, cwd=self.cwd)
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


def same_path(reported, expected):
    check(os.path.isabs(reported), f"data file path is not absolute: {reported!r}")
    check(os.path.normcase(os.path.realpath(reported)) == os.path.normcase(os.path.realpath(expected)),
          f"data file path {reported!r} is not {str(expected)!r}")


def data_notice(server, kind):
    """Return the absolute path from the startup notice ('opened' or 'created')."""
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        for line in list(server.errors):
            match = re.fullmatch(r"ramsql: data file (.+) \((opened|created)\)", line)
            if match:
                check(match.group(2) == kind, f"expected a {kind} data file: {line!r}")
                return match.group(1)
        time.sleep(0.01)
    check(False, f"no data file notice: {list(server.errors)!r}")


def refuse_missing(command, missing, cwd=None):
    """Without --create-data a missing data file fails before any listener."""
    global cases
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15, cwd=cwd)
    check(completed.returncode != 0, f"missing data file started a database: {completed.stdout!r}")
    check("RAMSQL postgres" not in completed.stdout, "listener bound for a missing data file")
    check("DataFileMissing" in completed.stderr and "--create-data" in completed.stderr,
          f"unclear missing data file refusal: {completed.stderr!r}")
    match = re.search(r"ramsql: data file (.+) does not exist", completed.stderr)
    check(match is not None, f"refusal does not name the data file: {completed.stderr!r}")
    same_path(match.group(1), missing)
    check(not Path(missing).exists(), "refused startup created the data file")
    cases += 1


def data_file_creation(binary, artifacts):
    """SRV-01: only --create-data creates a durable data file."""
    global cases
    # A typo in the file or directory name never becomes a new database.
    typo = artifacts / "typo-database.wal"
    refuse_missing(DurableServer(binary, typo).command(), typo)
    absent_directory = artifacts / "no-such-directory" / "database.wal"
    refuse_missing(DurableServer(binary, absent_directory).command(), absent_directory)
    check(not absent_directory.parent.exists(), "refused startup created a directory")
    # The default relative ramsql.wal in a wrong working directory is refused,
    # reported as the absolute path it resolved to, and not created.
    elsewhere = artifacts / "wrong-cwd"
    elsewhere.mkdir()
    default_data = [str(binary), "--serve", "--insecure-local", "--port", "0", "--max-clients", "8"]
    refuse_missing(default_data, elsewhere / "ramsql.wal", cwd=elsewhere)
    check(not any(elsewhere.iterdir()), "default data path created a file")
    # An existing path that cannot be opened as a WAL (a directory, or a FIFO
    # on POSIX) fails promptly, is never called missing and is left untouched.
    unusable = [artifacts / "directory.wal"]
    unusable[0].mkdir()
    if hasattr(os, "mkfifo"):
        unusable.append(artifacts / "fifo.wal")
        os.mkfifo(unusable[1])
    for path in unusable:
        for create in (False, True):
            completed = subprocess.run(DurableServer(binary, path, create=create).command(),
                                       capture_output=True, text=True, timeout=15)
            check(completed.returncode != 0 and "RAMSQL postgres" not in completed.stdout,
                  f"unusable data path started a database: {path}")
            check("does not exist" not in completed.stderr and "DataFileMissing" not in completed.stderr
                  and "(created)" not in completed.stderr, f"unusable data path called missing: {completed.stderr!r}")
        cases += 1
    check(not any(unusable[0].iterdir()), "directory data path was modified")
    # --create-data creates the missing file, which accepts a durable write.
    created = artifacts / "created.wal"
    with DurableServer(binary, created, create=True) as server:
        same_path(data_notice(server, "created"), created)
        pg = server.connect()
        try:
            pg.ok("CREATE TABLE durable (id INTEGER PRIMARY KEY, value TEXT)")
            pg.ok("INSERT INTO durable VALUES (1, 'created')")
        finally:
            pg.close()
    check(created.is_file() and created.stat().st_size > 0, "created data file holds no commit")
    cases += 1
    # A restart without the flag reopens that file with its data intact.
    with DurableServer(binary, created) as server:
        same_path(data_notice(server, "opened"), created)
        assert_database(server, [["1", "created"]])
    cases += 1
    # --create-data on an existing file opens it and never recreates it.
    with DurableServer(binary, created, create=True) as server:
        same_path(data_notice(server, "opened"), created)
        assert_database(server, [["1", "created"]])
    cases += 1
    # A relative --data resolves against the server's working directory.
    workdir = artifacts / "relative-cwd"
    (workdir / "nested").mkdir(parents=True)
    relative = os.path.join("nested", "relative.wal")
    refuse_missing(DurableServer(binary, relative).command(), workdir / relative, cwd=workdir)
    with DurableServer(binary, relative, create=True, cwd=workdir) as server:
        same_path(data_notice(server, "created"), workdir / relative)
    check((workdir / relative).is_file(), "relative data file was not created in the server directory")
    with DurableServer(binary, relative, cwd=workdir) as server:
        same_path(data_notice(server, "opened"), workdir / relative)
    cases += 1


def record(sequence, sql, kind=2):
    statement = sql.encode("utf-8")
    payload = struct.pack("<I", len(statement)) + statement
    return batch_record(sequence, payload, kind)


def batch_record(sequence, payload, kind=2):
    header = bytearray(struct.pack("<8sHHIQII", b"RAMSQLW1", 1, kind, len(payload), sequence, 0, 0))
    struct.pack_into("<I", header, 24, zlib.crc32(header))
    trailer = struct.pack("<I4sQ", zlib.crc32(payload), b"CMIT", sequence)
    return bytes(header) + payload + trailer


def change_payload(*ops):
    """Kind-3 change record: version 1, flags 0, op count, then op bytes."""
    return struct.pack("<HHI", 1, 0, len(ops)) + b"".join(ops)


def record_kinds(data):
    position = 0
    kinds = []
    while position < len(data):
        kind, length = struct.unpack_from("<HI", data, position + 10)
        kinds.append(kind)
        position += 48 + length
    return kinds


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
    data_file_creation(binary, artifacts)
    wal = artifacts / "database.wal"
    expected = [["1", "autocommit"], ["2", "transaction"], ["3", "literal; newline\nkept"]]
    with DurableServer(binary, wal, create=True) as server:
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
    offline_backup(binary, absent_source, absent_target, ("FileNotFound",))
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
    # The server writes only kind-3 change records.
    check(set(record_kinds(original)) == {3}, "server wrote a non-kind-3 WAL record")
    # Checksummed kind-3 records with semantic or structural damage, and any
    # legacy SQL record after a kind-3 record, fail closed before listeners
    # bind and are never published as backups.
    durable_oid = 16384
    bad_changes = {
        "semantic": (change_payload(struct.pack("<BIQ", 5, durable_oid, 99999)), ("ChangeRecordConflict",)),
        # Ops 7..9 are the sequence ops since step 3.7; 10 is unknown.
        "unknown-op": (change_payload(struct.pack("<BIQ", 10, durable_oid, 1)), ("UnsupportedLogVersion",)),
        "truncated-op": (change_payload(struct.pack("<BI", 3, durable_oid)), ("InvalidChangeRecord",)),
        "legacy-after-change": (struct.pack("<I", 30) + b"INSERT INTO durable VALUES (4)", ("UnsupportedLogVersion",)),
    }
    for name, (payload, errors) in bad_changes.items():
        candidate = artifacts / f"change-{name}.wal"
        kind = 2 if name == "legacy-after-change" else 3
        contents = original + batch_record(sequence + 1, payload, kind)
        candidate.write_bytes(contents)
        refuse(binary, candidate, errors)
        check(candidate.read_bytes() == contents, "rejected change record rewrote WAL")
        target = artifacts / f"change-{name}-backup.wal"
        offline_backup(binary, candidate, target, errors)
        check(not target.exists(), "invalid change record was backed up")
    # Legacy (alpha kind-2) SQL batches replay through the alpha grammar.
    # These records have valid outer CRCs/trailers. Recovery must reject their
    # inner framing/SQL before opening listeners or publishing a backup. A valid
    # first statement must never hide a malformed/rejected later statement.
    legacy_base = record(1, "CREATE TABLE durable (id INTEGER PRIMARY KEY, value TEXT)")
    semantic = artifacts / "semantic-corruption.wal"
    bad_semantic = legacy_base + record(2, "INSERT INTO missing VALUES (1)")
    semantic.write_bytes(bad_semantic)
    refuse(binary, semantic, ("TableNotFound", "UnknownTable"))
    check(semantic.read_bytes() == bad_semantic, "semantic replay failure rewrote WAL")
    semantic_backup = artifacts / "semantic-backup.wal"
    offline_backup(binary, semantic, semantic_backup, ("TableNotFound", "UnknownTable"))
    check(not semantic_backup.exists(), "semantically invalid backup was published")
    non_alpha = artifacts / "legacy-non-alpha.wal"
    non_alpha_bytes = legacy_base + record(2, "DELETE FROM durable WHERE id = 1 OR id = 2")
    non_alpha.write_bytes(non_alpha_bytes)
    refuse(binary, non_alpha, ("UnsupportedLogVersion",))
    check(non_alpha.read_bytes() == non_alpha_bytes, "non-alpha legacy record rewrote WAL")
    first = b"INSERT INTO durable VALUES (2000, 'first')"
    framed = struct.pack("<I", len(first)) + first
    second = b"INSERT INTO missing VALUES (1)"
    bad_batches = {
        "length-tail": (framed + b"\x01", ("InvalidCommitBatch",)),
        "zero-length": (framed + struct.pack("<I", 0), ("InvalidCommitBatch",)),
        "missing-body": (framed + struct.pack("<I", 1), ("InvalidCommitBatch",)),
        "third-frame": (framed + framed + b"\x01", ("PrimaryKeyViolation", "InvalidCommitBatch")),
        "later-sql": (framed + struct.pack("<I", len(second)) + second, ("TableNotFound",)),
        "empty": (b"", ("InvalidPayloadLength",)),
    }
    for name, (payload, errors) in bad_batches.items():
        candidate = artifacts / f"batch-{name}.wal"
        contents = legacy_base + batch_record(2, payload)
        candidate.write_bytes(contents)
        refuse(binary, candidate, errors)
        check(candidate.read_bytes() == contents, "invalid batch modified its WAL")
        target = artifacts / f"batch-{name}-backup.wal"
        offline_backup(binary, candidate, target, errors)
        check(not target.exists(), "invalid transaction batch was backed up")
    legacy = artifacts / "legacy-integer64.wal"
    legacy_bytes = record(1, "CREATE TABLE durable (id INTEGER PRIMARY KEY, value TEXT)", kind=1)
    legacy.write_bytes(legacy_bytes)
    refuse(binary, legacy, ("UnsupportedLogVersion",))
    check(legacy.read_bytes() == legacy_bytes, "incompatible INTEGER semantics rewrote legacy WAL")
    legacy_backup = artifacts / "legacy-integer64-backup.wal"
    offline_backup(binary, legacy, legacy_backup, ("UnsupportedLogVersion",))
    check(not legacy_backup.exists(), "incompatible legacy WAL was published as a new-version backup")
    check(wal.read_bytes() == original, "test modified its baseline database unexpectedly")
    print(json.dumps({"suite": "persistence_crash", "pass": True, "cases": cases,
                      "assertions": assertions + wire.assertions, "crash_cycles": options.cycles,
                      "artifacts": str(artifacts.resolve())}))


if __name__ == "__main__":
    main()
