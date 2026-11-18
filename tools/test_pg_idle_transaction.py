"""Compare PostgreSQL's idle-in-transaction setting on live wire sessions."""

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import select
import socket
import struct
import time

from test_server import PG, Server, U32, packet, query_packet


SETTING = "idle_in_transaction_session_timeout"
INTERVAL_MS = 400
INTERVAL = INTERVAL_MS / 1000


@contextmanager
def connect(port, user, database, options=None):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        pg = PG(sock)
        body = U32.pack(196608) + f"user\0{user}\0database\0{database}\0".encode()
        if options:
            body += f"options\0{options}\0".encode()
        body += b"\0"
        sock.sendall(U32.pack(len(body) + 4) + body)
        messages = pg.ready()
        assert (b"R", b"\0" * 4) in messages, messages
        yield pg


def errors(messages):
    return [dict((field[0:1], field[1:]) for field in body.split(b"\0") if field)
            for tag, body in messages if tag == b"E"]


def sql(pg, source, status=b"I", error=None):
    pg.sock.sendall(query_packet(source))
    reply = pg.ready()
    assert reply[-1] == (b"Z", status), (source, reply)
    assert [fields[b"C"] for fields in errors(reply)] == ([error] if error else []), (source, reply)
    return reply


def scalar(pg, source, status=b"I"):
    row, = (body for tag, body in sql(pg, source, status) if tag == b"D")
    count, size = struct.unpack("!HI", row[:6])
    assert count == 1 and size == len(row) - 6, row
    return row[6:].decode()


def expect_fatal(pg, started):
    message = pg.message()
    error, = errors([message])
    assert error[b"C"] == b"25P03" and error[b"V"] == b"FATAL", error
    assert error[b"M"] == b"terminating connection due to idle-in-transaction timeout", error
    elapsed = time.monotonic() - started
    assert INTERVAL / 2 < elapsed < 5, elapsed
    try:
        pg.message()
    except (EOFError, ConnectionResetError):
        pass
    else:
        raise AssertionError("server returned another message after FATAL")
    return error[b"C"].decode()


def no_messages(pg, seconds):
    assert not pg.buffer and not select.select([pg.sock], [], [], seconds)[0], "unexpected ready socket"


def ready_after(pg, query, status):
    started = time.monotonic()
    sql(pg, query, status)
    return started


def run(port, user, database):
    def client(options=None):
        return connect(port, user, database, options)

    facts = {}
    show = "SHOW " + SETTING
    limit = f"SET {SETTING} = {INTERVAL_MS}"
    with client() as pg, client() as separate:
        assert scalar(pg, show) == scalar(separate, show) == "0"
        sql(pg, f"SET {SETTING} = '1.25s'")
        facts["fractional"] = scalar(pg, show)
        assert facts["fractional"] == "1250ms"
        sql(pg, f"SET {SETTING} = '1500us'")
        assert scalar(pg, show) == "2ms"
        sql(pg, f"SET {SETTING} = -1", error=b"22023")
        sql(pg, f"SET {SETTING} = '2147483648'", error=b"22023")
        sql(pg, limit)
        no_messages(pg, INTERVAL * 1.3)
        assert scalar(pg, show) == f"{INTERVAL_MS}ms" and scalar(separate, show) == "0"
        sql(pg, f"SET {SETTING} = 0")
        sql(pg, "BEGIN", b"T")
        no_messages(pg, INTERVAL * 1.3)
        sql(pg, "COMMIT")
        sql(pg, "BEGIN", b"T")
        sql(pg, f"SET LOCAL {SETTING} = '10s'", b"T")
        sql(pg, "SAVEPOINT timer_scope", b"T")
        assert scalar(pg, f"SELECT set_config('{SETTING}', '12s', false)", b"T") == "12s"
        assert scalar(pg, f"SELECT current_setting('{SETTING}')", b"T") == "12s"
        sql(pg, "ROLLBACK TO timer_scope", b"T")
        assert scalar(pg, show, b"T") == "10s"
        sql(pg, "COMMIT")
        assert scalar(pg, show) == "0"
        sql(pg, "BEGIN", b"T")
        sql(pg, f"SET {SETTING} = '10s'", b"T")
        sql(pg, "ROLLBACK")
        assert scalar(pg, show) == "0"
        sql(pg, "CREATE TABLE idle_timer_rows (id INT PRIMARY KEY, value INT)")
        sql(pg, "INSERT INTO idle_timer_rows VALUES (1,10)")

    with client(f"-c {SETTING}=2s") as pg:
        assert scalar(pg, show) == "2s"
        sql(pg, f"SET {SETTING} = 0")
        sql(pg, f"RESET {SETTING}")
        facts["startup_reset"] = scalar(pg, show)
        assert facts["startup_reset"] == "2s"

    with client() as holder, client() as waiter:
        sql(holder, limit)
        sql(holder, "BEGIN", b"T")
        started = ready_after(holder, "UPDATE idle_timer_rows SET value = value + 100 WHERE id=1", b"T")
        waiter.sock.sendall(query_packet("UPDATE idle_timer_rows SET value = value + 1 WHERE id=1"))
        no_messages(waiter, 0.05)
        facts["held_lock"] = expect_fatal(holder, started)
        assert (b"C", b"UPDATE 1\0") in waiter.ready()
        facts["final_value"] = scalar(waiter, "SELECT value FROM idle_timer_rows WHERE id=1")
        assert facts["final_value"] == "11"

    with client() as holder, client() as waiting:
        sql(holder, "BEGIN", b"T")
        sql(holder, "UPDATE idle_timer_rows SET value=20 WHERE id=1", b"T")
        sql(waiting, limit)
        sql(waiting, "BEGIN", b"T")
        waiting.sock.sendall(query_packet("UPDATE idle_timer_rows SET value=21 WHERE id=1"))
        no_messages(waiting, INTERVAL * 1.3)
        started = time.monotonic()
        sql(holder, "ROLLBACK")
        assert (b"C", b"UPDATE 1\0") in waiting.ready()
        facts["parked_statement"] = expect_fatal(waiting, started)
        assert scalar(holder, "SELECT value FROM idle_timer_rows WHERE id=1") == "11"

    with client() as pg:
        sql(pg, limit)
        sql(pg, "BEGIN", b"T")
        started = time.monotonic()
        sql(pg, "SELECT 1/0", b"E", error=b"22012")
        facts["failed_block"] = expect_fatal(pg, started)

    with client() as pg:
        sql(pg, limit)
        sql(pg, "BEGIN", b"T")
        pg.sock.sendall(packet(b"P", b"\0SELECT 1\0\0\0") + packet(b"H"))
        assert pg.message() == (b"1", b"")
        no_messages(pg, INTERVAL * 1.3)
        started = time.monotonic()
        pg.sock.sendall(packet(b"S"))
        assert pg.ready()[-1] == (b"Z", b"T")
        facts["extended_sync"] = expect_fatal(pg, started)

    with client() as pg:
        sql(pg, limit)
        started = ready_after(pg, "BEGIN", b"T")
        pg.sock.sendall(query_packet("SELECT 1")[:2])
        facts["partial_command"] = expect_fatal(pg, started)

    with client() as pg:
        sql(pg, limit)
        sql(pg, "BEGIN", b"T")
        pg.sock.sendall(query_packet("COPY idle_timer_rows FROM STDIN"))
        assert pg.message()[0] == b"G"
        no_messages(pg, INTERVAL * 1.3)
        started = time.monotonic()
        pg.sock.sendall(packet(b"d", b"2\t30\n") + packet(b"c"))
        assert pg.ready()[-1] == (b"Z", b"T")
        facts["copy_input"] = expect_fatal(pg, started)

    with client() as pg:
        assert scalar(pg, "SELECT count(*) FROM idle_timer_rows") == "1"
        sql(pg, "DROP TABLE idle_timer_rows")
    return facts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--reference-port", type=int)
    ap.add_argument("--reference-user", default="postgres")
    ap.add_argument("--reference-database", default="postgres")
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    reference = run(args.reference_port, args.reference_user, args.reference_database) if args.reference_port else None
    modes = []
    for workers in args.workers:
        Server.test_workers = workers
        with Server(args.binary.resolve(), max_clients=8) as server:
            result = run(server.port, "ramsql", "ramsql")
        if reference is not None:
            assert result == reference, (workers, result, reference)
        modes.append({"workers": workers, "observed": result})
    report = {"pass": True, "postgres_reference": reference, "ramsql": modes}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
