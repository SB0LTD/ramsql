"""Compare PostgreSQL diagnostics and filtering across real sessions."""

import argparse
import json
from pathlib import Path

from test_pg_idle_transaction import connect, expect_fatal, ready_after, scalar, sql
from test_server import Server, packet


def warnings(messages):
    result = []
    for index, (tag, body) in enumerate(messages):
        if tag != b"N":
            continue
        fields = {field[:1]: field[1:] for field in body.split(b"\0") if field}
        assert fields[b"S"] == fields[b"V"] == b"WARNING", fields
        assert any(item[0] == b"C" for item in messages[index + 1:]), messages
        result.append((fields[b"C"], fields[b"M"]))
    return result


NO_TRANSACTION = (b"25P01", b"there is no transaction in progress")
NESTED_TRANSACTION = (b"25001", b"there is already a transaction in progress")
LOCAL_OUTSIDE = (b"25P01", b"SET LOCAL can only be used in transaction blocks")
TRANSACTION_OUTSIDE = (b"25P01", b"SET TRANSACTION can only be used in transaction blocks")


def probe(port, user, database):
    def client(options=None):
        return connect(port, user, database, options)

    show = "SHOW client_min_messages"
    report = {}
    with client() as pg, client() as witness:
        assert scalar(pg, show) == scalar(witness, show) == "notice"
        assert warnings(sql(pg, "COMMIT")) == [NO_TRANSACTION]
        assert warnings(sql(pg, "SET LOCAL check_function_bodies = off")) == [LOCAL_OUTSIDE]
        assert scalar(pg, "SHOW check_function_bodies") == "on"
        assert warnings(sql(pg, "SET TRANSACTION READ ONLY")) == [TRANSACTION_OUTSIDE]

        sql(pg, "SET client_min_messages = 'warning'")
        assert warnings(sql(pg, "ROLLBACK")) == [NO_TRANSACTION]
        sql(pg, "SET client_min_messages = 'error'")
        assert scalar(pg, "SELECT current_setting('client_min_messages')") == "error"
        assert warnings(sql(pg, "COMMIT")) == []
        assert warnings(sql(pg, "ROLLBACK")) == []
        assert scalar(pg, show) == "error"
        assert warnings(sql(witness, "COMMIT")) == [NO_TRANSACTION]
        sql(pg, "SELECT 1/0", error=b"22012")
        sql(pg, "SET client_min_messages = 'fatal'", error=b"22023")
        sql(pg, "RESET client_min_messages")

        sql(pg, "BEGIN", b"T")
        assert warnings(sql(pg, "BEGIN", b"T")) == [NESTED_TRANSACTION]
        sql(pg, "SAVEPOINT diagnostics", b"T")
        sql(pg, "SET LOCAL client_min_messages = 'error'", b"T")
        assert warnings(sql(pg, "BEGIN", b"T")) == []
        sql(pg, "ROLLBACK TO diagnostics", b"T")
        assert warnings(sql(pg, "BEGIN", b"T")) == [NESTED_TRANSACTION]
        sql(pg, "ROLLBACK")
        assert scalar(pg, show) == "notice"

        pg.sock.sendall(packet(b"P", b"\0COMMIT\0\0\0") + packet(b"B", b"\0\0\0\0\0\0\0\0")
                        + packet(b"E", b"\0\0\0\0\0") + packet(b"S"))
        messages = pg.ready()
        assert warnings(messages) == [NO_TRANSACTION], messages
        report["simple_and_extended"] = "25P01"
        report["nested_begin"] = "25001"

        sql(pg, "SET client_min_messages = 'error'")
        sql(pg, "LISTEN notice_settings")
        sql(witness, "NOTIFY notice_settings, 'still delivered'")
        tag, body = pg.message()
        assert tag == b"A" and body[4:] == b"notice_settings\0still delivered\0", (tag, body)
        report["notification"] = "still delivered"

    with client("-c client_min_messages=error") as pg:
        assert scalar(pg, show) == "error"
        sql(pg, "SET client_min_messages = 'debug5'")
        assert scalar(pg, show) == "debug5"
        sql(pg, "RESET ALL")
        assert scalar(pg, show) == "error"
        sql(pg, "SET idle_in_transaction_session_timeout = 400")
        started = ready_after(pg, "BEGIN", b"T")
        report["fatal_at_error"] = expect_fatal(pg, started)
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--workers", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--reference-port", type=int)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    native = probe(args.reference_port, "postgres", "postgres") if args.reference_port else None
    modes = []
    for workers in args.workers:
        Server.test_workers = workers
        with Server(args.binary.resolve(), max_clients=8) as server:
            result = probe(server.port, "ramsql", "ramsql")
        if native is not None:
            assert result == native, (workers, result, native)
        modes.append({"workers": workers, "observed": result})
    report = {"pass": True, "postgres_reference": native, "ramsql": modes}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
