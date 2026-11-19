"""Live differential for actual PostgreSQL transaction timeout boundaries."""

import argparse
import json
from pathlib import Path
import time

from test_pg_idle_transaction import connect, expect_fatal, no_messages, ready_after, scalar, sql
from test_server import Server, packet, query_packet


def run(port, user, database):
    def client(options=None):
        return connect(port, user, database, options)

    facts = {}
    with client() as pg:
        assert scalar(pg, "SHOW transaction_timeout") == "0"
        sql(pg, "SET transaction_timeout = '1.25s'")
        assert scalar(pg, "SHOW transaction_timeout") == "1250ms"
        sql(pg, "SET transaction_timeout = -1", error=b"22023")
        sql(pg, "RESET transaction_timeout")
        sql(pg, "CREATE TABLE transaction_timer (id INT PRIMARY KEY, value INT)")
        sql(pg, "INSERT INTO transaction_timer VALUES (1,10)")

    with client("-c transaction_timeout=600") as pg:
        started = ready_after(pg, "BEGIN", b"T")
        no_messages(pg, 0.25)
        sql(pg, "SET transaction_timeout = '2s'", b"T")
        assert scalar(pg, "SELECT current_setting('transaction_timeout')", b"T") == "2s"
        facts["active_value_change"] = expect_fatal(pg, started, b"25P04", maximum=1.5)

    with client() as pg:
        sql(pg, "SET transaction_timeout = 400")
        sql(pg, "SET idle_in_transaction_session_timeout = 400")
        started = ready_after(pg, "BEGIN", b"T")
        facts["equal_idle"] = expect_fatal(pg, started, b"25P04")

    with client() as pg:
        sql(pg, "SET transaction_timeout = 2000")
        sql(pg, "SET idle_in_transaction_session_timeout = 400")
        started = ready_after(pg, "BEGIN", b"T")
        facts["earlier_idle"] = expect_fatal(pg, started)

    with client() as pg:
        sql(pg, "SET transaction_timeout = 400")
        sql(pg, "BEGIN", b"T")
        sql(pg, "SET LOCAL transaction_timeout = 0", b"T")
        no_messages(pg, 0.55)
        assert scalar(pg, "SHOW transaction_timeout", b"T") == "0"
        started = ready_after(pg, "SET LOCAL transaction_timeout = 400", b"T")
        facts["rearmed"] = expect_fatal(pg, started, b"25P04")

    with client() as pg:
        sql(pg, "SET transaction_timeout = 400")
        sql(pg, "BEGIN", b"T")
        sql(pg, "SELECT 1/0", b"E", error=b"22012")
        no_messages(pg, 0.55)
        sql(pg, "ROLLBACK")
        assert scalar(pg, "SELECT 1") == "1"
        facts["aborted_block"] = "survived"

    with client() as holder, client() as waiting:
        sql(holder, "BEGIN", b"T")
        sql(holder, "UPDATE transaction_timer SET value=20 WHERE id=1", b"T")
        sql(waiting, "SET transaction_timeout = 400")
        started = time.monotonic()
        waiting.sock.sendall(query_packet("UPDATE transaction_timer SET value=21 WHERE id=1"))
        facts["implicit_lock_wait"] = expect_fatal(waiting, started, b"25P04")
        sql(holder, "ROLLBACK")

    with client() as holder, client() as waiting:
        sql(holder, "BEGIN", b"T")
        sql(holder, "UPDATE transaction_timer SET value=20 WHERE id=1", b"T")
        sql(waiting, "SET transaction_timeout = 550")
        sql(waiting, "SET statement_timeout = 150")
        sql(waiting, "BEGIN", b"T")
        sql(waiting, "UPDATE transaction_timer SET value=21 WHERE id=1", b"E", error=b"57014")
        no_messages(waiting, 0.55)
        sql(waiting, "ROLLBACK")
        sql(waiting, "SET statement_timeout = 0")
        sql(waiting, "SET lock_timeout = 150")
        sql(waiting, "UPDATE transaction_timer SET value=21 WHERE id=1", error=b"55P03")
        sql(holder, "ROLLBACK")
        facts["earlier_statement_and_lock"] = ["57014", "55P03"]

    with client() as pg:
        sql(pg, "SET transaction_timeout = 400")
        started = time.monotonic()
        pg.sock.sendall(query_packet("COPY transaction_timer FROM STDIN"))
        assert pg.message()[0] == b"G"
        no_messages(pg, 0.52)
        pg.sock.sendall(packet(b"d", b"2\t30\n") + packet(b"c"))
        facts["implicit_copy"] = expect_fatal(pg, started, b"25P04")

    with client() as pg:
        assert scalar(pg, "SELECT value FROM transaction_timer") == "10"
        sql(pg, "DROP TABLE transaction_timer")
    return facts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--reference-port", type=int)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    native = run(args.reference_port, "postgres", "postgres") if args.reference_port else None
    results = []
    for workers in args.workers:
        Server.test_workers = workers
        with Server(args.binary.resolve(), max_clients=8) as server:
            observed = run(server.port, "ramsql", "ramsql")
        if native is not None:
            assert observed == native, (workers, observed, native)
        results.append({"workers": workers, "observed": observed})
    report = {"pass": True, "postgres_reference": native, "ramsql": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
