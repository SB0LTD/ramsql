"""Run adversarial PostgreSQL wire and TCP lifecycle contracts (stdlib only).

Build RAMSQL first, then run ``python tools/test_server.py``. The server is
started on an OS-selected loopback port and terminated after every test run.
This checks RAMSQL's documented SQL subset, not full PostgreSQL SQL semantics.
"""

import argparse
from collections import deque
from contextlib import contextmanager
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


U32 = struct.Struct("!I")
assertions = 0
cases = 0


def check(condition, message="contract failed"):
    global assertions
    assertions += 1
    if not condition:
        raise AssertionError(message)


def packet(tag, body=b""):
    return tag + U32.pack(len(body) + 4) + body


def query_packet(sql):
    return packet(b"Q", sql.encode("utf-8") + b"\0")


def startup_packet(user="ramsql"):
    body = U32.pack(196608) + b"user\0" + user.encode() + b"\0database\0ramsql\0client_encoding\0UTF8\0\0"
    return U32.pack(len(body) + 4) + body


class Server:
    test_workers = 1
    def __init__(self, binary, idle_ms=30000, max_clients=16, max_query_work=0, extra_arguments=()):
        self.binary = binary
        self.idle_ms = idle_ms
        self.max_clients = max_clients
        self.max_query_work = max_query_work
        self.extra_arguments = tuple(extra_arguments)
        self.errors = deque(maxlen=100)
        self.process = None
        self.port = None

    def __enter__(self):
        self.process = subprocess.Popen(
            [str(self.binary), "--serve", "--memory", "--insecure-local", "--protocol", "postgres", "--port", "0",
             "--idle-ms", str(self.idle_ms), "--max-clients", str(self.max_clients),
             *(["--workers", str(min(self.test_workers,self.max_clients))] if self.test_workers > 1 else []),
             *(["--max-query-work", str(self.max_query_work)] if self.max_query_work else []), *self.extra_arguments],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        lines = queue.Queue()
        threading.Thread(target=lambda: lines.put(self.process.stdout.readline()), daemon=True).start()

        def collect_errors():
            for line in self.process.stderr:
                self.errors.append(line.rstrip())

        self.error_reader = threading.Thread(target=collect_errors, daemon=True)
        self.error_reader.start()
        try:
            line = lines.get(timeout=15)
            match = re.fullmatch(r"RAMSQL postgres 127\.0\.0\.1:(\d+)\n", line)
            check(match is not None, f"bad server startup {line!r}; stderr={list(self.errors)!r}")
            self.port = int(match.group(1))
            check(0 < self.port <= 65535, "server did not report an actual ephemeral port")
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process.stdout.close()
            self.error_reader.join(timeout=1)
            self.process.stderr.close()

    def raw(self, recv_buffer=None):
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(3)
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if recv_buffer:
            client.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, recv_buffer)
        client.connect(("127.0.0.1", self.port))
        return client


class PG:
    def __init__(self, sock, read_chunk=16384):
        self.sock = sock
        self.read_chunk = read_chunk
        self.buffer = bytearray()

    def exact(self, length):
        while len(self.buffer) < length:
            data = self.sock.recv(min(self.read_chunk, max(4096, length - len(self.buffer))))
            if not data:
                raise EOFError("server closed a PostgreSQL connection")
            self.buffer.extend(data)
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def message(self):
        header = self.exact(5)
        length = U32.unpack(header[1:])[0]
        check(4 <= length <= 4 * 1024 * 1024, f"invalid backend message length: {length}")
        return header[:1], self.exact(length - 4)

    def ready(self):
        messages = []
        for _ in range(10000):
            tag, body = self.message()
            messages.append((tag, body))
            if tag == b"Z":
                check(body in (b"I", b"T", b"E"), f"invalid transaction status {body!r}")
                return messages
        raise AssertionError("backend never returned ReadyForQuery")

    def startup(self, chunks=None):
        data = startup_packet()
        if chunks is None:
            self.sock.sendall(data)
        else:
            self.fragments(data, chunks)
        messages = self.ready()
        check((b"R", b"\0\0\0\0") in messages, "expected AuthenticationOk for trust login")
        check(not errors(messages), f"startup returned error: {errors(messages)!r}")
        params = dict(body[:-1].split(b"\0", 1) for tag, body in messages if tag == b"S")
        check(params.get(b"client_encoding") == b"UTF8", "missing UTF8 ParameterStatus")
        check(messages[-1] == (b"Z", b"I"), "new connection must be idle")
        return messages

    def fragments(self, data, chunks=(1, 2, 3, 5, 8)):
        at = 0
        turn = 0
        while at < len(data):
            size = chunks[turn % len(chunks)]
            self.sock.sendall(data[at:at + size])
            at += size
            turn += 1
            # Introduce actual gaps around frame headers rather than assuming
            # a particular relationship between send() and TCP recv().
            if turn <= 8:
                time.sleep(0.002)

    def query(self, sql):
        self.sock.sendall(query_packet(sql))
        return self.ready()

    def ok(self, sql, expected_rows=None, status=b"I"):
        messages = self.query(sql)
        check(not errors(messages), f"{sql!r}: {errors(messages)!r}")
        check(messages[-1] == (b"Z", status), f"{sql!r}: wrong transaction status {messages[-1]!r}")
        if expected_rows is not None:
            check(rows(messages) == expected_rows, f"{sql!r}: got {rows(messages)!r}, expected {expected_rows!r}")
        return messages

    def close(self, terminate=True):
        if terminate:
            try:
                self.sock.sendall(packet(b"X"))
            except OSError:
                pass
        self.sock.close()


def errors(messages):
    found = []
    for tag, body in messages:
        if tag != b"E":
            continue
        fields = {}
        at = 0
        while at < len(body) and body[at]:
            key = chr(body[at])
            end = body.find(b"\0", at + 1)
            check(end >= 0, "unterminated ErrorResponse field")
            fields[key] = body[at + 1:end].decode("utf-8")
            at = end + 1
        check(at == len(body) - 1 and body[at] == 0, "malformed ErrorResponse terminator")
        check(len(fields.get("C", "")) == 5, "ErrorResponse must contain a SQLSTATE")
        found.append(fields)
    return found


def rows(messages):
    result = []
    for tag, body in messages:
        if tag != b"D":
            continue
        count = struct.unpack_from("!H", body)[0]
        at = 2
        row = []
        for _ in range(count):
            length = struct.unpack_from("!i", body, at)[0]
            at += 4
            if length == -1:
                row.append(None)
            else:
                check(0 <= length <= len(body) - at, "invalid DataRow cell length")
                row.append(body[at:at + length].decode("utf-8"))
                at += length
        check(at == len(body), "unexpected trailing DataRow bytes")
        result.append(row)
    return result


def expect_error(messages, state):
    found = errors(messages)
    check(len(found) == 1 and found[0]["C"] == state, f"expected SQLSTATE {state}, got {found!r}")


@contextmanager
def connection(server, **options):
    pg = PG(server.raw(**options))
    try:
        pg.startup()
        yield pg
    finally:
        pg.close()


def live_probe(server, timeout=3):
    """Allow asynchronous disconnect processing, with a hard recovery deadline."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with connection(server) as pg:
                pg.ok("SELECT 1", [["1"]])
            return
        except (EOFError, ConnectionError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


def test_crud_shared(server):
    with connection(server) as first, connection(server) as second:
        first.ok("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, active BOOLEAN)")
        first.ok("INSERT INTO items VALUES (1, 'café שלום 🌍', true)")
        second.ok("SELECT * FROM items", [["1", "café שלום 🌍", "t"]])
        second.ok("INSERT INTO items VALUES (2, 'other', NULL)")
        first.ok("SELECT id, active FROM items ORDER BY id", [["1", "t"], ["2", None]])
        expect_error(first.query("INSERT INTO items VALUES (1, 'duplicate', false)"), "23505")
        expect_error(first.query("INSERT INTO items VALUES (3, NULL, false)"), "23502")
        expect_error(first.query("SELECT * FROM absent"), "42P01")
        first.ok("UPDATE items SET name = 'updated' WHERE id = 2")
        second.ok("SELECT name FROM items WHERE id = 2", [["updated"]])
        second.ok("DELETE FROM items WHERE id = 2")
        first.ok("SELECT id FROM items", [["1"]])
        first.ok("DROP TABLE items")


def test_fragmented_and_coalesced(server):
    pg = PG(server.raw())
    try:
        pg.startup(chunks=(1,))
        pg.fragments(query_packet("SELECT 1"), chunks=(1, 3, 2))
        check(rows(pg.ready()) == [["1"]], "fragmented query response differs")
        pg.sock.sendall(query_packet("SELECT 1") * 64)
        for _ in range(64):
            check(rows(pg.ready()) == [["1"]], "coalesced query lost or reordered")
    finally:
        pg.close()
    pg = PG(server.raw())
    try:
        pg.sock.sendall(startup_packet() + query_packet("SELECT 1") + query_packet("SELECT 1"))
        check((b"R", b"\0\0\0\0") in pg.ready(), "coalesced startup failed")
        check(rows(pg.ready()) == [["1"]], "first query following coalesced startup lost")
        check(rows(pg.ready()) == [["1"]], "second query following coalesced startup lost")
    finally:
        pg.close()


def test_ssl_negotiation(server):
    for code in (80877103, 80877104):  # SSLRequest and GSSENCRequest
        pg = PG(server.raw())
        try:
            pg.sock.sendall(U32.pack(8) + U32.pack(code) + startup_packet() + query_packet("SELECT 1"))
            check(pg.exact(1) == b"N", "unsupported transport negotiation must reply N")
            check((b"R", b"\0\0\0\0") in pg.ready(), "startup after transport negotiation failed")
            check(rows(pg.ready()) == [["1"]], "query after transport negotiation failed")
        finally:
            pg.close()


def test_half_close_and_partial_reads(server):
    pg = PG(server.raw(), read_chunk=1)
    try:
        pg.startup()
        pg.ok("SELECT 1", [["1"]])
    finally:
        pg.close()
    pg = PG(server.raw())
    try:
        pg.sock.sendall(startup_packet() + query_packet("SELECT 1") * 3)
        pg.sock.shutdown(socket.SHUT_WR)
        check((b"R", b"\0\0\0\0") in pg.ready(), "half-close discarded startup")
        for _ in range(3):
            check(rows(pg.ready()) == [["1"]], "half-close discarded buffered request/response")
        expect_closed(pg)
    finally:
        pg.close(False)


def test_protocol_errors(server):
    with connection(server) as pg:
        for body in (b"SELECT 1", b"SELECT 1\0unexpected", b"\0\0"):
            pg.sock.sendall(packet(b"Q", body))
            expect_error(pg.ready(), "08P01")
            pg.ok("SELECT 1", [["1"]])
        # Extended protocol errors discard messages through Sync. A queued
        # query preceding Sync must not execute or produce another Ready.
        pg.sock.sendall(packet(b"?") + query_packet("SELECT 1") + packet(b"S"))
        expect_error(pg.ready(), "0A000")
        pg.ok("SELECT 1", [["1"]])
        for body in (b"", b"x", b"\0", b"\0\0", b"\0\0\0"):
            pg.sock.sendall(packet(b"P", body) + packet(b"S"))
            expect_error(pg.ready(), "08P01")
        pg.ok("SELECT 1", [["1"]])


def expect_closed(pg):
    try:
        check(pg.exact(1) == b"", "unexpected byte after fatal protocol error")
    except (EOFError, ConnectionResetError, ConnectionAbortedError):
        return
    raise AssertionError("expected server to close the connection")


def test_fatal_frames(server):
    for length in (0, 1, 2, 3, 65536, 0x7FFFFFFF, 0xFFFFFFFF):
        for logged_in in (False, True):
            if logged_in and length == 65536:
                # Oversized authenticated SQL is drained before a recoverable
                # 54000 response; a header alone is an incomplete frame.
                continue
            pg = PG(server.raw())
            try:
                if logged_in:
                    pg.startup()
                pg.sock.sendall((b"Q" if logged_in else b"") + U32.pack(length))
                tag, body = pg.message()
                check(tag == b"E", "invalid packet length must yield an error")
                expect_error([(tag, body)], "08P01")
                expect_closed(pg)
            finally:
                pg.close(False)
    for payload in (U32.pack(123456), U32.pack(196608) + b"user\0", U32.pack(196608) + b"\0"):
        pg = PG(server.raw())
        try:
            pg.sock.sendall(U32.pack(len(payload) + 4) + payload)
            tag, body = pg.message()
            check(tag == b"E" and bool(errors([(tag, body)])), "malformed startup accepted")
            expect_closed(pg)
        finally:
            pg.close(False)
    live_probe(server)


def test_truncated_disconnect(server):
    source = startup_packet()
    for length in (1, 2, 3, 4, 5, 7, 8, len(source) - 1):
        sock = server.raw()
        sock.sendall(source[:length])
        sock.close()
    live_probe(server)
    for _ in range(32):
        pg = PG(server.raw())
        try:
            pg.startup()
            pg.sock.sendall(query_packet("SELECT 1")[:7])
        finally:
            pg.close(False)
        # The next handshake proves the abandoned partial frame did not leave
        # a permanently occupied slot or contaminate a newly reused session.
    live_probe(server)


def test_transactions_and_disconnect(server):
    with connection(server) as first, connection(server) as second:
        first.ok("CREATE TABLE txn (id INTEGER PRIMARY KEY)")
        first.ok("INSERT INTO txn VALUES (1)")
        first.ok("BEGIN", status=b"T")
        first.ok("INSERT INTO txn VALUES (2)", status=b"T")
        second.ok("SELECT * FROM txn", [["1"]])
        expect_error(first.query("INSERT INTO txn VALUES (1)"), "23505")
        expect_error(first.query("SELECT * FROM txn"), "25P02")
        first.ok("ROLLBACK")
        second.ok("SELECT * FROM txn", [["1"]])
        first.ok("BEGIN", status=b"T")
        first.ok("INSERT INTO txn VALUES (3)", status=b"T")
        first.ok("COMMIT")
        second.ok("SELECT * FROM txn ORDER BY id", [["1"], ["3"]])
    abandoned = PG(server.raw())
    try:
        abandoned.startup()
        abandoned.ok("BEGIN", status=b"T")
        abandoned.ok("INSERT INTO txn VALUES (4)", status=b"T")
    finally:
        abandoned.close(False)
    with connection(server) as observer:
        deadline = time.monotonic() + 3
        while True:
            messages = observer.query("SELECT * FROM txn ORDER BY id")
            failures = errors(messages)
            if not failures:
                check(rows(messages) == [["1"], ["3"]], "disconnect did not roll back transaction")
                break
            check(failures[0]["C"] == "55P03" and time.monotonic() < deadline, "disconnected transaction lock was not released")
            time.sleep(0.01)
        observer.ok("DROP TABLE txn")


def test_slow_reader_fairness(server):
    payload = "x" * 120
    with connection(server) as setup:
        setup.ok("CREATE TABLE bulk (id INTEGER PRIMARY KEY, payload TEXT)")
        for number in range(128):
            setup.ok(f"INSERT INTO bulk VALUES ({number}, '{payload}')")
    slow = PG(server.raw(recv_buffer=1024))
    try:
        slow.startup()
        # Roughly 9 MiB of responses into a deliberately tiny peer receive
        # window. The server must retain partial output and stop reading this
        # client's pipeline while other clients remain responsive.
        slow.sock.sendall(query_packet("SELECT * FROM bulk") * 512)
        time.sleep(0.15)
        with connection(server) as fast:
            began = time.monotonic()
            for _ in range(16):
                fast.ok("SELECT 1", [["1"]])
            check(time.monotonic() - began < 3, "slow reader starved another connection")
    finally:
        slow.close(False)
    live_probe(server)
    with connection(server) as cleanup:
        cleanup.ok("DROP TABLE bulk")


def test_pool_exhaustion(server):
    clients = []
    try:
        for _ in range(server.max_clients):
            pg = PG(server.raw())
            clients.append(pg)
            pg.startup()
        # A bounded server must reject overflow without corrupting existing
        # sessions or accepting a connection it cannot service.
        overflow = PG(server.raw())
        try:
            overflow.sock.sendall(startup_packet())
            tag, body = overflow.message()
            check(tag == b"E", "pool overflow must return a PostgreSQL error")
            expect_error([(tag,body)], "53300")
            expect_closed(overflow)
        finally:
            overflow.close(False)
        clients[0].ok("SELECT 1", [["1"]])
        clients.pop().close()
        live_probe(server)
        clients[0].ok("SELECT 1", [["1"]])
    finally:
        for pg in clients:
            pg.close()
    live_probe(server)


def test_idle_expiry(binary):
    with Server(binary, idle_ms=250) as server:
        pg = PG(server.raw())
        try:
            pg.startup()
            time.sleep(0.65)
            expect_closed(pg)
        finally:
            pg.close(False)
        live_probe(server)
        # Incomplete startups are subject to the same timeout and cannot hold
        # the entire fixed connection pool indefinitely.
        truncated = server.raw()
        try:
            truncated.sendall(b"\0\0")
            time.sleep(0.65)
            expect_closed(PG(truncated))
        finally:
            truncated.close()
        live_probe(server)


def test_running_query_controls(binary):
    # Correlated aggregates do substantial CPU work with bounded intermediate
    # storage. This must be interruptible without waiting for a row lock.
    slow = ("SELECT a.id FROM running_work a WHERE "
            "(SELECT COUNT(*) FROM running_work b WHERE b.id < "
            "(SELECT COUNT(*) FROM running_work c WHERE c.id < a.id)) < 0")
    with Server(binary, max_clients=1) as server:
        pg = PG(server.raw())
        try:
            startup = pg.startup()
            keys = [struct.unpack("!II", body) for tag, body in startup if tag == b"K"]
            check(len(keys) == 1, "missing cancellation key")
            pg.ok("CREATE TABLE running_work (id INT PRIMARY KEY, value INT)")
            for start in range(0, 256, 64):
                values = ",".join(f"({i},{i})" for i in range(start, start + 64))
                pg.ok("INSERT INTO running_work VALUES " + values)
            pg.ok("SET statement_timeout = 5")
            started = time.monotonic()
            expect_error(pg.query(slow), "57014")
            check(time.monotonic() - started < 2, "CPU statement deadline was not enforced")
            pg.ok("SET statement_timeout = 0")
            pg.ok("SELECT COUNT(*) FROM running_work", [["256"]])

            # All SQL slots are occupied: cancellation uses the separate,
            # header-only pool and still accepts a fragmented CancelRequest.
            pg.sock.sendall(query_packet(slow))
            time.sleep(0.01)
            with server.raw() as invalid:
                invalid.sendall(struct.pack("!IIII", 16, 80877102, keys[0][0], keys[0][1] ^ 1))
                expect_closed(PG(invalid))
            pg.sock.settimeout(0.01)
            try:
                unexpected = pg.sock.recv(1)
                raise AssertionError(f"invalid key interrupted active CPU work: {unexpected!r}")
            except socket.timeout:
                pass
            finally:
                pg.sock.settimeout(3)
            with server.raw() as cancel:
                data = struct.pack("!IIII", 16, 80877102, *keys[0])
                cancel.sendall(data[:5])
                time.sleep(0.005)
                cancel.sendall(data[5:])
                expect_closed(PG(cancel))
            expect_error(pg.ready(), "57014")
            pg.ok("SELECT COUNT(*) FROM running_work", [["256"]])
            pg.ok("BEGIN", status=b"T")
            pg.ok("SET LOCAL statement_timeout = 5", status=b"T")
            messages = pg.query(slow)
            expect_error(messages, "57014")
            check(messages[-1] == (b"Z", b"E"), "interrupted transaction must enter failed state")
            pg.ok("ROLLBACK")
            pg.ok("SELECT COUNT(*) FROM running_work", [["256"]])
        finally:
            pg.close()

    # A normal startup accepted by the interrupt hook must retain its bytes
    # and resume in the outer reactor after the active statement is canceled.
    with Server(binary, max_clients=2) as server:
        with connection(server) as pg:
            pg.ok("CREATE TABLE running_work (id INT PRIMARY KEY, value INT)")
            for start in range(0, 256, 64):
                pg.ok("INSERT INTO running_work VALUES " + ",".join(f"({i},{i})" for i in range(start, start + 64)))
            # Deadline ends the CPU query; no SQL from the new socket may run
            # recursively while its workspace belongs to this statement.
            pg.ok("SET statement_timeout = 5")
            pg.sock.sendall(query_packet(slow))
            with server.raw() as sock:
                pending = PG(sock)
                data = startup_packet()
                sock.sendall(data[:11])
                time.sleep(0.001)
                sock.sendall(data[11:])
                expect_error(pg.ready(), "57014")
                ready = pending.ready()
                check((b"R", b"\0\0\0\0") in ready and ready[-1] == (b"Z", b"I"),
                      "ordinary startup was lost during interrupt polling")
                pending.ok("SELECT 1", [["1"]])

    with Server(binary, max_query_work=100) as server:
        with connection(server) as pg:
            pg.ok("CREATE TABLE budgeted (id INT PRIMARY KEY, value INT)")
            for i in range(32):
                pg.ok(f"INSERT INTO budgeted VALUES ({i},{i})")
            expect_error(pg.query("UPDATE budgeted SET value = value + 1"), "54000")
            pg.ok("SELECT value FROM budgeted WHERE id = 0", [["0"]])
            pg.ok("SELECT value FROM budgeted WHERE id = 31", [["31"]])
            pg.ok("BEGIN", status=b"T")
            expect_error(pg.query("DELETE FROM budgeted WHERE value + 1 > 0"), "54000")
            expect_error(pg.query("SELECT 1"), "25P02")
            pg.ok("ROLLBACK")
            pg.ok("SELECT value FROM budgeted WHERE id = 0", [["0"]])


def main():
    global cases
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path(__file__).resolve().parents[1] / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql")
    parser.add_argument("--binary", type=Path, default=default)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    Server.test_workers = args.workers
    binary = args.binary.resolve(strict=True)
    started = time.monotonic()
    with Server(binary) as server:
        for test in (test_crud_shared, test_fragmented_and_coalesced, test_ssl_negotiation,
                     test_half_close_and_partial_reads,
                     test_protocol_errors, test_fatal_frames, test_truncated_disconnect,
                     test_transactions_and_disconnect, test_slow_reader_fairness, test_pool_exhaustion):
            test(server)
            check(server.process.poll() is None, f"server exited after {test.__name__}: {list(server.errors)!r}")
            cases += 1
    test_idle_expiry(binary)
    cases += 1
    test_running_query_controls(binary)
    cases += 1
    print(json.dumps({"suite": "server", "pass": True, "cases": cases,
                      "assertions": assertions, "seconds": round(time.monotonic() - started, 3)}))


if __name__ == "__main__":
    main()
