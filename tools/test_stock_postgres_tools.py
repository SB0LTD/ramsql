"""Exercise unmodified stock PostgreSQL dump files and custom archives."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

import psycopg

from qualify_postgres import REFERENCES
from test_postgres_compat import docker, native_postgres, running_ramsql
from test_persistence import DurableServer


SCHEMA = (
    "CREATE TABLE accounts (id INTEGER PRIMARY KEY, label TEXT NOT NULL DEFAULT 'ready')",
    "CREATE TABLE ledger (id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES accounts(id), "
    "amount INTEGER CHECK (amount > 0))",
)
DATA = ("INSERT INTO accounts (id) VALUES (1),(2)", "INSERT INTO ledger VALUES (4,1,7),(5,2,9)")
QUERY = ("SELECT l.id, a.label, l.amount FROM public.ledger l "
         "JOIN public.accounts a ON a.id = l.account_id ORDER BY l.id")
EXPECTED = [(4, "ready", 7), (5, "ready", 9)]
TABLES = ("--table=public.accounts", "--table=public.ledger")


def seed(arguments):
    with psycopg.connect(**arguments) as db:
        for statement in SCHEMA + DATA:
            db.execute(statement)


def settings(arguments):
    return ["--host", arguments["host"], "--port", str(arguments["port"]),
            "--username", arguments["user"], "--dbname", arguments["dbname"]]


def ramsql_settings(server):
    return dict(host="127.0.0.1", port=server.port, user="postgres", dbname="ramsql",
                autocommit=True, connect_timeout=5)


class Tools:
    def __init__(self, root, native_bin, image):
        self.root = root
        self.native_bin = native_bin
        self.image = image

    def run(self, label, command, arguments, *options):
        env = os.environ.copy()
        env.pop("PGPASSWORD", None)
        if password := arguments.get("password"):
            env["PGPASSWORD"] = password
        if self.native_bin:
            launch = [str(self.native_bin / command)]
        else:
            launch = ["docker", "run", "--rm", "--network=host",
                      "--volume", f"{self.root}:/work", "--workdir", "/work",
                      "--user", f"{self.root.stat().st_uid}:{self.root.stat().st_gid}",
                      "--env", "PGPASSWORD", self.image, command]
        completed = subprocess.run([*launch, *settings(arguments), *options], cwd=self.root,
                                   capture_output=True, text=True, timeout=90, env=env)
        (self.root / f"{label}.stdout").write_text(completed.stdout, encoding="utf-8")
        (self.root / f"{label}.stderr").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"{label} exited {completed.returncode}\n{completed.stderr}")


def verify(arguments):
    with psycopg.connect(**arguments) as db:
        assert db.execute(QUERY).fetchall() == EXPECTED
        for statement, sqlstate in (
            ("INSERT INTO public.accounts(id) VALUES (1)", "23505"),
            ("INSERT INTO public.ledger VALUES (6,99,7)", "23503"),
            ("INSERT INTO public.ledger VALUES (6,1,0)", "23514"),
            ("INSERT INTO public.accounts VALUES (3,NULL)", "23502"),
        ):
            try:
                db.execute(statement)
            except psycopg.Error as error:
                assert error.sqlstate == sqlstate, (statement, error.sqlstate)
                db.rollback()
            else:
                raise AssertionError(f"restored constraint accepted {statement}")
        assert db.execute(QUERY).fetchall() == EXPECTED


def namespace_acl(native, candidate):
    statements = (
        "CREATE ROLE dump_reader",
        "CREATE SCHEMA dump_acl",
        "GRANT USAGE ON SCHEMA dump_acl TO dump_reader",
        "GRANT USAGE ON SCHEMA dump_acl TO PUBLIC",
        "REVOKE USAGE ON SCHEMA dump_acl FROM ramsql",
    )
    queries = (
        "SELECT nspacl::text, acldefault('n',nspowner)::text "
        "FROM pg_namespace WHERE nspname='dump_acl'",
        "SELECT nspacl FROM pg_namespace WHERE nspname='dump_acl'",
    )
    with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
        for statement in statements:
            pg.execute(statement)
            ram.execute(statement)
        native_rows = pg.execute(queries[0]).fetchall()
        assert native_rows == ram.execute(queries[0]).fetchall(), native_rows
        assert len(native_rows) == 1 and "dump_reader=U/ramsql" in native_rows[0][0]
        assert pg.execute(queries[1]).description[0].type_code == 1034
        assert ram.execute(queries[1]).description[0].type_code == 1034


def import_dumps(binary, tools, native):
    seed(native)
    for label, fmt, suffix in (("plain", "p", "sql"), ("custom", "c", "dump")):
        artifact = f"inbound-{label}.{suffix}"
        tools.run(f"{label}-dump", "pg_dump", native, *TABLES,
                  "--no-owner", "--no-acl", f"--format={fmt}", f"--file={artifact}")
        wal = tools.root / f"{label}.wal"
        storage = ("--workers", "1")
        with DurableServer(binary, wal, create=True, storage_options=storage) as server:
            dest = ramsql_settings(server)
            if fmt == "p":
                tools.run(f"{label}-restore", "psql", dest, "--no-psqlrc", "--single-transaction",
                          "--set=ON_ERROR_STOP=1", f"--file={artifact}")
            else:
                tools.run(f"{label}-restore", "pg_restore", dest, "--exit-on-error",
                          "--single-transaction", "--no-owner", "--no-acl", artifact)
            verify(dest)
        with DurableServer(binary, wal, storage_options=storage) as reopened:
            verify(ramsql_settings(reopened))


def export_archive(binary, tools, native):
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        seed(candidate)
        tools.run("outbound-dump", "pg_dump", candidate, *TABLES, "--no-owner", "--no-acl",
                  "--format=custom", "--file=outbound.dump")
    tools.run("outbound-restore", "pg_restore", native, "--exit-on-error",
              "--single-transaction", "--no-owner", "--no-acl", "outbound.dump")
    verify(native)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--reference", choices=REFERENCES, required=True)
    parser.add_argument("--postgres-bin", type=Path)
    args = parser.parse_args()
    root = args.work_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    binary = args.binary.resolve()
    image = REFERENCES[args.reference]
    if not args.postgres_bin:
        try:
            docker("image", "inspect", image)
        except subprocess.CalledProcessError:
            docker("pull", image)
    tools = Tools(root, args.postgres_bin, image)
    result = {}
    for name, check in (
        ("namespace_acl", lambda db: check_namespaces(binary, db)),
        ("relation_discovery", lambda db: check_relation_discovery(binary, db)),
        ("function_discovery", lambda db: check_function_discovery(binary, db)),
        ("native_dump_to_ramsql", lambda db: import_dumps(binary, tools, db)),
        ("ramsql_dump_to_native", lambda db: export_archive(binary, tools, db)),
    ):
        try:
            with native_postgres(image, psycopg, binary_directory=args.postgres_bin,
                                 work_directory=root / name) as (native, _):
                check(native)
            result[name] = {"passed": True}
        except (AssertionError, RuntimeError, OSError, psycopg.Error, subprocess.SubprocessError) as error:
            result[name] = {"passed": False, "error": str(error)}
    passed = all(item["passed"] for item in result.values())
    with binary.open("rb") as source:
        binary_hash = hashlib.file_digest(source, "sha256").hexdigest()
    report = {"reference": args.reference, "binary_sha256": binary_hash, "passed": passed, "checks": result}
    text = json.dumps(report, indent=2)
    print(text)
    (root / "stock-tooling.json").write_text(text + "\n", encoding="utf-8")
    return 0 if passed else 1


def check_namespaces(binary, native):
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        namespace_acl(native, candidate)


def check_relation_discovery(binary, native):
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        seed(candidate)
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            spaces = "SELECT oid, spcname, spcowner, spcacl IS NULL, spcoptions IS NULL FROM pg_tablespace ORDER BY oid"
            assert pg.execute(spaces).fetchall() == ram.execute(spaces).fetchall()
            foreign = "SELECT ftrelid, ftserver, ftoptions FROM pg_foreign_table"
            ref, observed = pg.execute(foreign), ram.execute(foreign)
            assert ref.fetchall() == observed.fetchall() == []
            assert [c.type_code for c in ref.description] == [c.type_code for c in observed.description]
            query = (
                "SELECT c.relname, c.relowner, c.relpages, c.reltuples, c.relallvisible, c.relallfrozen, "
                "c.relfrozenxid, c.relminmxid, c.relacl, c.reltype, "
                "array_remove(c.reloptions, 'check_option=local'), tsp.spcname "
                "FROM pg_class c LEFT JOIN pg_tablespace tsp ON tsp.oid=c.reltablespace "
                "WHERE c.relname='accounts'"
            )
            cur = ram.execute(query)
            assert cur.fetchall() == [("accounts", 10, 0, -1, 0, 0, "0", "0", None, 0, None, None)]
            assert [c.type_code for c in cur.description] == [19, 26, 23, 700, 23, 23, 28, 28, 1034, 26, 1009, 19]


def check_function_discovery(binary, native):
    query = """
        SELECT p.tableoid, p.proname, p.prolang, p.pronargs, p.proargtypes::text,
               p.prorettype, p.proacl, acldefault('f',p.proowner)::text,
               p.pronamespace, p.proowner
        FROM pg_proc p
        LEFT JOIN pg_init_privs pip ON (p.oid = pip.objoid
            AND pip.classoid = 'pg_proc'::regclass AND pip.objsubid = 0)
        WHERE p.proname = 'dump_probe' AND p.prokind <> 'a'
          AND NOT EXISTS (SELECT 1 FROM pg_depend WHERE classid = 'pg_proc'::regclass
              AND objid = p.oid AND deptype = 'i')
          AND (pronamespace != (SELECT oid FROM pg_namespace WHERE nspname = 'pg_catalog')
              OR EXISTS (SELECT 1 FROM pg_cast WHERE pg_cast.oid > 16383 AND p.oid = pg_cast.castfunc)
              OR EXISTS (SELECT 1 FROM pg_transform WHERE pg_transform.oid > 16383
                  AND (p.oid = pg_transform.trffromsql OR p.oid = pg_transform.trftosql))
              OR p.proacl IS DISTINCT FROM pip.initprivs)
    """
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for conn in (pg, ram):
                conn.execute("CREATE FUNCTION dump_probe(x integer) RETURNS integer LANGUAGE SQL AS 'SELECT x + 1'")
            ref, observed = pg.execute(query), ram.execute(query)
            ref_rows = ref.fetchall()
            assert len(ref_rows) == 1 and ref_rows == observed.fetchall(), ref_rows
            assert [c.type_code for c in ref.description] == [c.type_code for c in observed.description]
            for conn in (pg, ram):
                conn.execute("CREATE ROLE dump_executor")
                conn.execute("REVOKE EXECUTE ON FUNCTION dump_probe(integer) FROM PUBLIC")
                conn.execute("GRANT EXECUTE ON FUNCTION dump_probe(integer) TO dump_executor")
            acl = "SELECT proacl::text FROM pg_proc WHERE proname='dump_probe'"
            assert pg.execute(acl).fetchall() == ram.execute(acl).fetchall()

            cast = "SELECT castsource,casttarget,castfunc,castcontext,castmethod FROM pg_cast WHERE castsource=1043 AND casttarget=25"
            assert pg.execute(cast).fetchall() == ram.execute(cast).fetchall()
            transforms = "SELECT oid,trftype,trflang,trffromsql::oid,trftosql::oid FROM pg_transform"
            ref, observed = pg.execute(transforms), ram.execute(transforms)
            assert ref.fetchall() == observed.fetchall() == []
            assert [c.type_code for c in ref.description] == [c.type_code for c in observed.description]


if __name__ == "__main__":
    raise SystemExit(main())
