"""Compare plain table CREATE authority and replayed ownership with PostgreSQL."""
import argparse
import json
from pathlib import Path
import tempfile

from test_pg_idle_transaction import connect, scalar, sql
from test_postgres_compat import running_ramsql


OWNER_READ = "SELECT id FROM create_scope.owner_data"
MEMBER_READ = "SELECT id FROM create_scope.member_data"


def seed(port, user, database):
    with connect(port, user, database) as pg:
        for query in (
            "CREATE ROLE create_owner SUPERUSER",
            "CREATE ROLE create_member",
            "CREATE ROLE usage_only",
            "SET ROLE create_owner",
            "CREATE SCHEMA create_scope",
            "RESET ROLE",
            "ALTER ROLE create_owner NOSUPERUSER",
            "GRANT create_owner TO create_member",
            "GRANT USAGE ON SCHEMA create_scope TO usage_only",
        ):
            sql(pg, query)


def live(port, user, database):
    with connect(port, user, database) as owner, connect(port, user, database) as member, connect(port, user, database) as usage:
        sql(owner, "SET ROLE create_owner")
        sql(owner, "REVOKE USAGE ON SCHEMA create_scope FROM CURRENT_ROLE")
        sql(owner, "CREATE TABLE create_scope.owner_data (id INT NOT NULL)")
        sql(owner, "CREATE TABLE IF NOT EXISTS create_scope.owner_data (id INT PRIMARY KEY)")
        sql(owner, "CREATE TABLE create_scope.denied_pk (id INT PRIMARY KEY)", error=b"42501")
        sql(owner, "CREATE TABLE create_scope.denied_unique (id INT UNIQUE)", error=b"42501")
        sql(owner, OWNER_READ, error=b"42501")
        sql(owner, "GRANT USAGE ON SCHEMA create_scope TO CURRENT_ROLE")
        sql(owner, "INSERT INTO create_scope.owner_data VALUES (5)")
        assert scalar(owner, OWNER_READ) == "5"

        sql(usage, "SET ROLE usage_only")
        sql(usage, "CREATE TABLE create_scope.denied (id INT)", error=b"42501")
        sql(usage, "CREATE TABLE IF NOT EXISTS create_scope.owner_data (id INT)", error=b"42501")
        sql(usage, "CREATE TABLE public.denied (id INT)", error=b"42501")

        sql(member, "SET ROLE create_member")
        sql(member, "CREATE TABLE create_scope.member_data (id INT PRIMARY KEY)")
        sql(member, "INSERT INTO create_scope.member_data VALUES (9)")
        assert scalar(member, MEMBER_READ) == "9"
        sql(owner, MEMBER_READ, error=b"42501")
    return {"owner_without_usage_read": "42501", "primary_without_usage": "42501", "unique_without_usage": "42501", "usage_only_create": "42501", "owner_value": "5", "member_value": "9"}


def reopened(port, user, database):
    with connect(port, user, database) as root, connect(port, user, database) as member:
        owner = scalar(root, "SELECT tableowner FROM pg_tables WHERE schemaname='create_scope' AND tablename='owner_data'")
        creator = scalar(root, "SELECT tableowner FROM pg_tables WHERE schemaname='create_scope' AND tablename='member_data'")
        assert owner == "create_owner" and creator == "create_member"
        sql(root, "DROP ROLE create_member", error=b"2BP01")
        sql(member, "SET ROLE create_member")
        assert scalar(member, MEMBER_READ) == "9"
        sql(member, "DROP TABLE create_scope.member_data")
        sql(member, "RESET ROLE")
        sql(root, "DROP ROLE create_member")
    return {"owner": owner, "creator": creator, "role_dependency": "2BP01"}



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
        directory = Path(tempfile.mkdtemp(prefix=f"namespace-create-w{workers}-"))
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
