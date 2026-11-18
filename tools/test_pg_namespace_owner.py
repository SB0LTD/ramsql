"""Compare durable PostgreSQL namespace ownership through native wire clients."""

import argparse
import json
from pathlib import Path
import tempfile

from test_pg_catalog_tableoid import row
from test_pg_idle_transaction import connect, scalar, sql
from test_postgres_compat import running_ramsql


OWNER_QUERY = (
    "SELECT n.tableoid, n.nspowner, r.rolname FROM pg_namespace n "
    "JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname='owned_namespace'"
)


def ownership(pg, expected):
    fields, values = row(pg, OWNER_QUERY)
    assert fields == [("tableoid", 26), ("nspowner", 26), ("rolname", 19)]
    catalog_oid, owner_oid, role = values
    assert catalog_oid == "2615" and int(owner_oid) > 16383 and role == expected
    return owner_oid


def seed(port, user, database):
    with connect(port, user, database) as owner, connect(port, user, database) as witness:
        sql(owner, "CREATE ROLE schema_owner SUPERUSER")
        sql(owner, "CREATE ROLE namespace_stranger")
        sql(owner, "BEGIN", b"T")
        sql(owner, "SET LOCAL ROLE schema_owner", b"T")
        sql(owner, "CREATE SCHEMA abandoned_namespace", b"T")
        count = "SELECT count(*) FROM pg_namespace WHERE nspname='abandoned_namespace'"
        assert scalar(owner, count, b"T") == "1" and scalar(witness, count) == "0"
        sql(owner, "ROLLBACK")
        assert scalar(owner, count) == "0"
        sql(owner, "SET ROLE schema_owner")
        sql(owner, "CREATE SCHEMA owned_namespace")
        sql(owner, "RESET ROLE")
        oid = ownership(witness, "schema_owner")
        sql(witness, "DROP ROLE schema_owner", error=b"2BP01")
        sql(witness, "ALTER ROLE schema_owner RENAME TO renamed_owner")
        sql(witness, "ALTER ROLE renamed_owner NOSUPERUSER")
        assert ownership(owner, "renamed_owner") == oid
        sql(owner, "SET ROLE namespace_stranger")
        sql(owner, "DROP SCHEMA owned_namespace", error=b"42501")
        sql(owner, "RESET ROLE")
        return oid


def verify_existing(port, user, database, oid):
    with connect(port, user, database) as pg:
        assert ownership(pg, "renamed_owner") == oid
        sql(pg, "DROP ROLE renamed_owner", error=b"2BP01")
        sql(pg, "SET ROLE renamed_owner")
        sql(pg, "COMMENT ON SCHEMA owned_namespace IS 'owner comment'")
        sql(pg, "DROP SCHEMA owned_namespace")
        sql(pg, "RESET ROLE")
        sql(pg, "DROP ROLE renamed_owner")
        sql(pg, "DROP ROLE namespace_stranger")
        return {"owner_rename": "same OID", "dependent_drop": "2BP01", "other_drop": "42501"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--binary", type=Path, required=True)
    ap.add_argument("--reference-port", type=int, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    native_oid = seed(args.reference_port, "postgres", "postgres")
    native = verify_existing(args.reference_port, "postgres", "postgres", native_oid)
    observations = []
    for workers in (1, 4):
        directory = Path(tempfile.mkdtemp(prefix=f"namespace-owner-w{workers}-"))
        wal = directory / "namespace.wal"
        extra = ("--workers", str(workers))
        with running_ramsql(args.binary.resolve(), extra_arguments=extra, data_path=wal, create_data=True) as instance:
            oid = seed(instance["port"], "ramsql", "ramsql")
        with running_ramsql(args.binary.resolve(), extra_arguments=extra, data_path=wal) as instance:
            got = verify_existing(instance["port"], "ramsql", "ramsql", oid)
        assert wal.stat().st_size > 0 and got == native
        observations.append({"workers": workers, "reopened": got})
    report = {"pass": True, "native": native, "ramsql": observations}
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
