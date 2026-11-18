"""Qualify the opt-in WAL rendezvous on owned Linux PostgreSQL sockets."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import select
import socket
import struct
import subprocess

from test_persistence import DurableServer
import test_server as wire


WINDOW_MS = 50


class TracedServer(DurableServer):
    def __init__(self, binary, wal, tracer, trace, *, window=WINDOW_MS, workers=2, inject=None):
        super().__init__(binary, wal, storage_options=("--workers", str(workers), "--group-commit-wait-ms", str(window)))
        self.tracer = tracer
        self.trace = trace
        self.inject = inject

    def command(self):
        prefix = [str(self.tracer), "--daemonize=grandchild", "--kill-on-exit", "--seccomp-bpf",
                  "-fqq", "-ttt", "-T", "-y", "-e", "trace=fsync", "-P", str(self.wal), "-o", str(self.trace)]
        if self.inject:
            prefix += ["-e", f"inject=fsync:{self.inject}:when=1"]
        return [*prefix, "--", *super().command()]


def open_client(stack, server):
    sock = stack.enter_context(socket.create_connection(("127.0.0.1", server.port), timeout=5))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    client = wire.PG(sock)
    [key] = [struct.unpack("!II", body) for tag, body in client.startup() if tag == b"K"]
    return client, key


def actors(stack, server, same_table=False):
    a, key_a = open_client(stack, server)
    b, key_b = open_client(stack, server)
    assert key_a[0] % 2 != key_b[0] % 2, "fixture actors must belong to different reactors"
    a.ok("BEGIN", status=b"T")
    b.ok("BEGIN", status=b"T")
    a.ok("INSERT INTO left_rows VALUES (1, 100)", status=b"T")
    table = "left_rows" if same_table else "right_rows"
    b.ok(f"INSERT INTO {table} VALUES (2, 200)", status=b"T")
    return a, b, key_a


def commit_ok(client):
    packets = client.ready()
    assert not wire.errors(packets) and packets[-1] == (b"Z", b"I"), packets
    assert [body for tag, body in packets if tag == b"C"] == [b"COMMIT\0"], packets


def commit_error(client, state, status):
    packets = client.ready()
    wire.expect_error(packets, state)
    assert packets[-1] == (b"Z", status) and all(tag != b"C" for tag, _ in packets), packets


def commit(client):
    client.sock.sendall(wire.query_packet("COMMIT"))


def quiet(clients, seconds):
    sockets = [client.sock for client in clients]
    assert not select.select(sockets, [], [], seconds)[0], "client received a premature commit response"


def cancel(server, key):
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as sock:
        sock.sendall(struct.pack("!IIII", 16, 80877102, *key))
        assert sock.recv(1) == b"", "CancelRequest must close without a reply"


def seed(binary, wal):
    with DurableServer(binary, wal, create=True) as server, ExitStack() as stack:
        client, _ = open_client(stack, server)
        client.ok("CREATE TABLE left_rows (id INT PRIMARY KEY, value INT)")
        client.ok("CREATE TABLE right_rows (id INT PRIMARY KEY, value INT)")


def verify_rows(client, left, right):
    client.ok("SELECT * FROM left_rows ORDER BY id", left)
    client.ok("SELECT * FROM right_rows ORDER BY id", right)


def replay(binary, wal, left, right):
    with DurableServer(binary, wal) as reopened, ExitStack() as stack:
        client, _ = open_client(stack, reopened)
        verify_rows(client, left, right)


def exercise(args, name, expected_syncs, action, *, delay=False, failure=False, window=WINDOW_MS):
    folder = args.output / name
    folder.mkdir()
    wal = args.work_dir / name / "data.wal"
    wal.parent.mkdir()
    seed(args.binary, wal)
    trace = folder / "fsync.log"
    injected = "error=EIO" if failure else ("delay_enter=250ms" if delay else None)
    with TracedServer(args.binary, wal, args.strace, trace, window=window, inject=injected) as server:
        expected = action(server)
    calls = [line for line in trace.read_text().splitlines() if "fsync(" in line]
    assert len(calls) == expected_syncs, (name, calls)
    if failure:
        assert "EIO" in calls[0] and "INJECTED" in calls[0], calls
    else:
        assert all("= 0" in line for line in calls), calls
    if delay:
        assert "(DELAYED)" in calls[0] and float(calls[0].rsplit("<", 1)[1].rstrip(">")) >= 0.24, calls
    replay(args.binary, wal, *expected)
    return {"case": name, "wal_fsyncs": len(calls), "trace_sha256": hashlib.sha256(trace.read_bytes()).hexdigest(),
            "process_crash_replay_checked": True}


def paired(server, *, same_table=False, delay=False, disconnect=False):
    with ExitStack() as stack:
        a, b, key_a = actors(stack, server, same_table=same_table)
        commit(a)
        commit(b)
        if delay:
            quiet((a, b), 0.075)
            if disconnect:
                a.sock.shutdown(socket.SHUT_RDWR)
                a.sock.close()
            else:
                # A cancel during native stable I/O cannot replace its outcome.
                cancel(server, key_a)
        if not disconnect:
            commit_ok(a)
        commit_ok(b)
    if same_table:
        return [["1", "100"], ["2", "200"]], []
    return [["1", "100"]], [["2", "200"]]


def failed_flush(server):
    with ExitStack() as stack:
        a, b, _ = actors(stack, server)
        commit(a)
        commit(b)
        commit_error(a, "58030", b"I")
        commit_error(b, "58030", b"I")
        reader, _ = open_client(stack, server)
        verify_rows(reader, [], [])
        wire.expect_error(reader.query("INSERT INTO left_rows VALUES (3, 300)"), "58030")
    # The injection fails fsync after the complete write. A same-kernel
    # process crash retains these unacknowledged bytes in the page cache.
    return [["1", "100"]], [["2", "200"]]


def solitary(server, *, cancel_request=False, disconnect=False):
    with ExitStack() as stack:
        client, key = open_client(stack, server)
        reader, _ = open_client(stack, server)
        client.ok("BEGIN", status=b"T")
        client.ok("INSERT INTO left_rows VALUES (1, 100)", status=b"T")
        commit(client)
        if cancel_request or disconnect:
            quiet((client,), 0.010)
            if cancel_request:
                cancel(server, key)
                commit_error(client, "57014", b"E")
                client.ok("ROLLBACK")
            else:
                client.sock.shutdown(socket.SHUT_RDWR)
                client.sock.close()
                # A later backend must inherit neither the old parked offer
                # nor a delayed CancelRequest for the old generation.
                replacement, replacement_key = open_client(stack, server)
                if replacement_key[0] == key[0]:
                    assert replacement_key != key
                cancel(server, key)
                replacement.ok("SELECT 7", [["7"]])
            verify_rows(reader, [], [])
            return [], []
        commit_ok(client)
        verify_rows(reader, [["1", "100"]], [])
        return [["1", "100"]], []


def check_option(binary):
    command = [str(binary), "--capabilities", "--group-commit-wait-ms", "10"]
    for flags in (("--workers", "1"), ("--workers", "2", "--memory"), ("--workers", "2", "--protocol", "mysql")):
        failed = subprocess.run([*command, *flags], capture_output=True, text=True, timeout=10)
        assert failed.returncode != 0 and "InvalidServerOptions" in failed.stderr, failed
    capacity = [str(binary), "--machine-capacity", "--workers", "2"]
    normal = json.loads(subprocess.check_output(capacity, text=True, timeout=10))
    grouped = json.loads(subprocess.check_output([*capacity, "--group-commit-wait-ms", "10"], text=True, timeout=10))
    scratch = 2 * 65536 + 2 * 48
    assert grouped["legacy_startup_bytes"] - normal["legacy_startup_bytes"] == scratch
    assert grouped["startup_region_bytes"] - normal["startup_region_bytes"] >= scratch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--strace", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.binary = args.binary.resolve(strict=True)
    args.strace = args.strace.resolve(strict=True)
    args.work_dir = args.work_dir.resolve()
    args.work_dir.mkdir(parents=True)
    args.output.mkdir(parents=True)
    report = {"schema": 1, "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(),
              "window_ms": WINDOW_MS, "passed": False, "cases": []}
    try:
        check_option(args.binary)
        for name, syncs, action, options in (
            ("default_singular", 2, paired, {"window": 0}),
            ("cross_reactor_ack_after_sync", 1, lambda server: paired(server, delay=True), {"delay": True}),
            ("cross_reactor_flush_error", 1, failed_flush, {"failure": True}),
            ("same_table_ordinary_commit", 2, lambda server: paired(server, same_table=True), {}),
            ("lone_commit_deadline", 1, solitary, {}),
            ("cancel_before_claim", 0, lambda server: solitary(server, cancel_request=True), {}),
            ("disconnect_before_claim", 0, lambda server: solitary(server, disconnect=True), {}),
            ("disconnect_during_flush", 1, lambda server: paired(server, delay=True, disconnect=True), {"delay": True}),
        ):
            print(name, flush=True)
            report["cases"].append(exercise(args, name, syncs, action, **options))
        report["passed"] = True
    except Exception as error:
        report["failure"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        (args.output / "group-commit-contract.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
