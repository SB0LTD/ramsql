"""Compare private schema USAGE grants with PostgreSQL and stored WAL."""
import argparse
import json
from pathlib import Path
import tempfile

from test_pg_idle_transaction import connect, scalar, sql
from test_postgres_compat import running_ramsql


READ = "SELECT id FROM grant_scope.items"
GRANT = "GRANT USAGE ON SCHEMA grant_scope TO grant_guest"
REVOKE = "REVOKE USAGE ON SCHEMA grant_scope FROM grant_guest"


def seed(port, user, database):
    with connect(port, user, database) as pg:
        for query in (
            "CREATE ROLE grant_owner SUPERUSER",
            "CREATE ROLE grant_inherited_owner",
            "CREATE ROLE grant_guest",
            "CREATE ROLE grant_inherited_guest",
            "CREATE ROLE grant_acl_only",
            "SET ROLE grant_owner",
            "CREATE SCHEMA grant_scope",
            "RESET ROLE",
            "CREATE TABLE grant_scope.items (id INT PRIMARY KEY)",
            "INSERT INTO grant_scope.items VALUES (77)",
            "GRANT SELECT ON grant_scope.items TO grant_owner, grant_guest",
            "ALTER ROLE grant_owner NOSUPERUSER",
            "GRANT grant_owner TO grant_inherited_owner",
            "GRANT grant_guest TO grant_inherited_guest",
        ):
            sql(pg, query)


def live(port, user, database):
    with connect(port, user, database) as admin, connect(port, user, database) as guest, connect(port, user, database) as root:
        sql(admin, "SET ROLE grant_inherited_owner")
        sql(guest, "SET ROLE grant_inherited_guest")
        assert scalar(admin, READ) == "77"
        sql(guest, READ, error=b"42501")
        sql(admin, "BEGIN", b"T")
        sql(admin, GRANT, b"T")
        sql(guest, READ, error=b"42501")
        sql(admin, "SET LOCAL ROLE grant_guest", b"T")
        assert scalar(admin, READ, b"T") == "77"
        sql(admin, "ROLLBACK")
        sql(guest, READ, error=b"42501")

        sql(admin, GRANT)
        assert scalar(guest, READ) == "77"
        sql(guest, "BEGIN ISOLATION LEVEL REPEATABLE READ", b"T")
        assert scalar(guest, READ, b"T") == "77"
        sql(admin, REVOKE)
        sql(guest, READ, b"E", error=b"42501")
        sql(guest, "ROLLBACK")

        sql(admin, "SET ROLE grant_owner")
        sql(admin, "REVOKE USAGE ON SCHEMA grant_scope FROM CURRENT_ROLE")
        sql(admin, READ, error=b"42501")
        sql(admin, "GRANT USAGE ON SCHEMA grant_scope TO CURRENT_ROLE")
        assert scalar(admin, READ) == "77"
        sql(admin, "GRANT USAGE ON SCHEMA grant_scope TO PUBLIC, grant_acl_only")
        assert scalar(guest, READ) == "77"
        sql(root, "DROP ROLE grant_acl_only", error=b"2BP01")
    return {"transaction_own": "77", "repeatable_read_revoke": "42501", "public": "77"}


def reopened(port, user, database):
    with connect(port, user, database) as admin, connect(port, user, database) as guest, connect(port, user, database) as root:
        sql(guest, "SET ROLE grant_inherited_guest")
        assert scalar(guest, READ) == "77"
        sql(root, "DROP ROLE grant_acl_only", error=b"2BP01")
        sql(admin, "SET ROLE grant_inherited_owner")
        sql(admin, "REVOKE USAGE ON SCHEMA grant_scope FROM PUBLIC")
        sql(guest, READ, error=b"42501")
        sql(admin, "REVOKE USAGE ON SCHEMA grant_scope FROM grant_acl_only")
        sql(root, "DROP ROLE grant_acl_only")
    return {"durable_public": "77", "durable_role_dependency": "2BP01", "revoked": "42501"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--reference-port", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    seed(args.reference_port, "postgres", "postgres")
    native_live = live(args.reference_port, "postgres", "postgres")
    native_reopened = reopened(args.reference_port, "postgres", "postgres")
    observations = []
    for workers in (1, 4):
        directory = Path(tempfile.mkdtemp(prefix=f"namespace-grants-w{workers}-"))
        wal = directory / "namespace.wal"
        options = ("--workers", str(workers))
        with running_ramsql(args.binary.resolve(), extra_arguments=options, data_path=wal, create_data=True) as instance:
            seed(instance["port"], "ramsql", "ramsql")
            result_live = live(instance["port"], "ramsql", "ramsql")
        with running_ramsql(args.binary.resolve(), extra_arguments=options, data_path=wal) as instance:
            result_reopened = reopened(instance["port"], "ramsql", "ramsql")
        assert result_live == native_live and result_reopened == native_reopened and wal.stat().st_size > 0
        observations.append({"workers": workers, "live": result_live, "reopened": result_reopened})
    report = {"pass": True, "native": {"live": native_live, "reopened": native_reopened}, "ramsql": observations}
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
