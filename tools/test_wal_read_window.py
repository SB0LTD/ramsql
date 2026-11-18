"""Check the WAL read window during actual delayed Linux file synchronization."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import select
import socket
import struct

from test_group_commit import TracedServer, cancel, commit, commit_error, commit_ok, open_client
from test_group_commit import quiet, replay, wal_sync_results
from test_persistence import DurableServer
import test_server as wire


class ReadWindowServer(TracedServer):
    def __init__(self, binary, wal, tracer, trace, *, enabled, failed):
        injected = "error=EIO:delay_enter=250ms" if failed else "delay_enter=250ms"
        super().__init__(binary, wal, tracer, trace, workers=3, inject=injected)
        self.enabled = enabled

    def command(self):
        return [*super().command(), *(["--group-commit-read-window"] if self.enabled else [])]


def seed(binary, wal):
    with DurableServer(binary, wal, create=True) as server, ExitStack() as stack:
        client, _ = open_client(stack, server)
        client.ok("CREATE TABLE left_rows (id INT PRIMARY KEY, value INT)")
        client.ok("CREATE TABLE right_rows (id INT PRIMARY KEY, value INT)")
        client.ok("CREATE TABLE control_rows (id INT PRIMARY KEY, value INT)")


def prepare(client):
    source = b"key\0SELECT value FROM left_rows WHERE id=$1\0" + struct.pack("!HI", 1, 23)
    client.sock.sendall(wire.packet(b"P", source) + wire.packet(b"S"))
    packets = client.ready()
    assert not wire.errors(packets) and (b"1", b"") in packets, packets


def key_read(client):
    bind = b"\0key\0" + struct.pack("!HHI", 0, 1, 1) + b"1\0\0"
    execute = b"\0" + struct.pack("!I", 0)
    client.sock.sendall(wire.packet(b"B", bind) + wire.packet(b"E", execute) + wire.packet(b"S"))


def key_result(client, expected):
    packets = client.ready()
    assert not wire.errors(packets) and packets[-1] == (b"Z", b"I"), packets
    assert wire.rows(packets) == expected, packets
    assert [body for tag, body in packets if tag == b"C"] == [f"SELECT {len(expected)}\0".encode()], packets


def clients(stack, server, serializable, exclusive):
    a, key_a = open_client(stack, server)
    b, key_b = open_client(stack, server)
    reader, key_reader = open_client(stack, server)
    assert len({key_a[0] % 3, key_b[0] % 3, key_reader[0] % 3}) == 3, "expected three native reactors"
    prepare(reader)
    key_read(reader)
    key_result(reader, [])
    if serializable:
        reader.ok("SET default_transaction_isolation = 'serializable'")
        reader.ok("SHOW default_transaction_isolation", [["serializable"]])
    if exclusive:
        locker, _ = open_client(stack, server)
        locker.ok("BEGIN", status=b"T")
        locker.ok("LOCK TABLE control_rows IN ACCESS EXCLUSIVE MODE", status=b"T")
    a.ok("BEGIN", status=b"T")
    b.ok("BEGIN", status=b"T")
    a.ok("INSERT INTO left_rows VALUES (1, 100)", status=b"T")
    b.ok("INSERT INTO right_rows VALUES (2, 200)", status=b"T")
    return a, b, reader, key_a


def exercise(args, name, *, enabled=True, failed=False, serializable=False, exclusive=False, disconnect=False):
    folder = args.work_dir / name
    folder.mkdir(parents=True)
    wal = folder / "data.wal"
    trace = args.output / f"{name}-fsync.log"
    seed(args.binary, wal)
    with ReadWindowServer(args.binary, wal, args.strace, trace, enabled=enabled, failed=failed) as server:
        with ExitStack() as stack:
            a, b, reader, key_a = clients(stack, server, serializable, exclusive)
            commit(a)
            commit(b)
            quiet((a, b), 0.070)
            key_read(reader)
            read_before_commit = enabled and not serializable and not exclusive
            if read_before_commit:
                readable, _, _ = select.select((a.sock, b.sock, reader.sock), [], [], 0.120)
                assert readable == [reader.sock], "reader failed to complete while both writers awaited sync"
                key_result(reader, [])
                quiet((a, b), 0)
                if disconnect:
                    a.sock.shutdown(socket.SHUT_RDWR)
                    a.sock.close()
                elif not failed:
                    cancel(server, key_a)
                    quiet((a, b), 0)
            else:
                quiet((a, b, reader), 0.105)
            if failed:
                commit_error(a, "58030", b"I")
                commit_error(b, "58030", b"I")
            else:
                if not disconnect:
                    commit_ok(a)
                    a.ok("SELECT 42", [["42"]])
                commit_ok(b)
            if read_before_commit:
                key_read(reader)
            key_result(reader, [] if failed else [["100"]])
            if failed:
                wire.expect_error(reader.query("INSERT INTO left_rows VALUES (3, 300)"), "58030")
    calls = wal_sync_results(trace.read_text(), wal)
    assert len(calls) == 1, (name, calls)
    [sync] = calls
    assert "(DELAYED)" in sync and float(sync.rsplit("<", 1)[1].rstrip(">")) >= 0.24, calls
    if failed:
        assert "EIO" in sync and "INJECTED" in sync, calls
    else:
        assert "= 0" in sync, calls
    # A process-only crash preserves complete bytes from an unacknowledged
    # WAL append after a reported sync failure. Reopen still verifies replay.
    replay(args.binary, wal, [["1", "100"]], [["2", "200"]])
    return {"case": name, "enabled": enabled, "read_before_commit_checked": read_before_commit,
            "injected_sync_failure": failed, "wal_fsyncs": len(calls), "process_crash_replay_checked": True,
            "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--strace", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True)
    cases = [exercise(args, "disabled", enabled=False), exercise(args, "read-during-sync"),
             exercise(args, "read-during-failed-sync", failed=True), exercise(args, "disconnect", disconnect=True),
             exercise(args, "serializable-fallback", serializable=True), exercise(args, "access-exclusive-fallback", exclusive=True)]
    report = {"schema": 1, "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(), "cases": cases}
    (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"{len(cases)} Linux socket and WAL replay cases passed")


if __name__ == "__main__":
    main()
