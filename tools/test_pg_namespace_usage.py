"""Compare private namespace USAGE and durable replay against PostgreSQL."""
import argparse
import json
from pathlib import Path
import tempfile
from test_pg_idle_transaction import connect, scalar, sql
from test_postgres_compat import running_ramsql


def seed(port, user, database):
    with connect(port, user, database) as pg:
        for query in (
            "CREATE ROLE namespace_guest",
            "CREATE SCHEMA private_scope",
            "CREATE TABLE private_scope.items (id INT PRIMARY KEY)",
            "INSERT INTO private_scope.items VALUES (31)",
            "GRANT SELECT, INSERT, UPDATE, DELETE ON private_scope.items TO namespace_guest",
            "CREATE VIEW usage_gate AS SELECT id FROM private_scope.items",
            "GRANT SELECT ON usage_gate TO namespace_guest",
        ):
            sql(pg, query)


def probe(port, user, database):
    with connect(port, user, database) as pg:
        assert scalar(pg, "SELECT id FROM private_scope.items") == "31"
        sql(pg, "SET ROLE namespace_guest")
        for query in (
            "SELECT id FROM private_scope.items",
            "SELECT id FROM private_scope.missing",
            "INSERT INTO private_scope.items VALUES (32)",
            "UPDATE private_scope.items SET id=32",
            "DELETE FROM private_scope.items",
            "COPY private_scope.items TO STDOUT",
            "COPY private_scope.items FROM STDIN",
            "CREATE TABLE private_scope.denied(id INT)",
            "SELECT to_regclass('private_scope.items')",
        ):
            sql(pg, query, error=b"42501")
        sql(pg, "BEGIN", b"T")
        sql(pg, "LOCK TABLE private_scope.items", b"E", error=b"42501")
        sql(pg, "ROLLBACK")
        assert scalar(pg, "SELECT id FROM usage_gate") == "31"
        sql(pg, "RESET ROLE")
        assert scalar(pg, "SELECT id FROM private_scope.items") == "31"
    return {"usage": "42501", "authorized_view": "31", "owner_read": "31"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--reference-port", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    seed(args.reference_port, "postgres", "postgres")
    native = probe(args.reference_port, "postgres", "postgres")
    verified = []
    for workers in (1, 4):
        directory = Path(tempfile.mkdtemp(prefix=f"private-namespace-w{workers}-"))
        wal = directory / "namespace.wal"
        options = ("--workers", str(workers))
        with running_ramsql(args.binary.resolve(), extra_arguments=options, data_path=wal, create_data=True) as instance:
            seed(instance["port"], "ramsql", "ramsql")
            first = probe(instance["port"], "ramsql", "ramsql")
        with running_ramsql(args.binary.resolve(), extra_arguments=options, data_path=wal) as instance:
            reopened = probe(instance["port"], "ramsql", "ramsql")
        assert first == native == reopened and wal.stat().st_size > 0
        verified.append({"workers": workers, "live": first, "reopened": reopened})
    result = {"pass": True, "native": native, "ramsql": verified}
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
