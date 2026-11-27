"""Exercise unmodified stock PostgreSQL dump files and custom archives."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import psycopg

from qualify_postgres import REFERENCES
from test_database_cluster import SMALL_PROFILE
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


def settings(arguments, command):
    database = arguments["dbname"]
    if command == "pg_dumpall":
        database = psycopg.conninfo.make_conninfo(dbname=database)
    return ["--host", arguments["host"], "--port", str(arguments["port"]),
            "--username", arguments["user"], "--dbname", database]


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
        if arguments is not None and (password := arguments.get("password")):
            env["PGPASSWORD"] = password
        if self.native_bin:
            launch = [str(self.native_bin / command)]
        else:
            launch = ["docker", "run", "--rm", "--network=host",
                      "--volume", f"{self.root}:/work", "--workdir", "/work",
                      "--user", f"{self.root.stat().st_uid}:{self.root.stat().st_gid}",
                      "--env", "PGPASSWORD", self.image, command]
        connection = [] if arguments is None else settings(arguments, command)
        completed = subprocess.run([*launch, *connection, *options], cwd=self.root,
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
        assert db.execute("INSERT INTO public.accounts(id) VALUES (3) RETURNING label").fetchone() == ("ready",)
        db.execute("DELETE FROM public.accounts WHERE id = 3")
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


def export_archive(binary, tools, native, table_options=TABLES, label="outbound"):
    artifact = f"{label}.dump"
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        seed(candidate)
        tools.run(f"{label}-dump", "pg_dump", candidate, *table_options, "--no-owner", "--no-acl",
                  "--format=custom", f"--file={artifact}")
    tools.run(f"{label}-restore", "pg_restore", native, "--exit-on-error",
              "--single-transaction", "--no-owner", "--no-acl", artifact)
    verify(native)


def check_legacy_attribute_archives(binary, tools, reference):
    """Restore raw archives emitted by pg_dump 18.0 against PostgreSQL 18.7."""
    def verify_restore(endpoint):
        with psycopg.connect(**endpoint) as db:
            rows = db.execute('SELECT id,"Payload" FROM public.legacy_attver ORDER BY id').fetchall()
            assert rows == [(1, "ready"), (2, "copied")], rows
            options = db.execute(
                "SELECT attoptions FROM pg_attribute "
                "WHERE attrelid='public.legacy_attver'::regclass AND attnum>0 ORDER BY attnum"
            ).fetchall()
            assert options == [(None,), (None,)], options
            with db.transaction(force_rollback=True):
                try:
                    db.execute("INSERT INTO public.legacy_attver VALUES (1, 'duplicate')")
                except psycopg.errors.UniqueViolation:
                    pass
                else:
                    raise AssertionError("restored primary key did not reject a duplicate")

    fixtures = Path(__file__).with_name("fixtures")
    formats = ("sql", "dump") if reference == "18" else ("sql",)
    storage = ("--workers", "1")
    for suffix in formats:
        artifact = f"postgres18-legacy-attver.{suffix}"
        shutil.copyfile(fixtures / artifact, tools.root / artifact)
        wal = tools.root / f"legacy-attribute-{suffix}.wal"
        with DurableServer(binary, wal, create=True, storage_options=storage) as server:
            candidate = ramsql_settings(server)
            if suffix == "sql":
                tools.run("legacy-attribute-psql", "psql", candidate, "--no-psqlrc",
                          "--single-transaction", "--set=ON_ERROR_STOP=1", f"--file={artifact}")
            else:
                tools.run("legacy-attribute-pg-restore", "pg_restore", candidate, "--single-transaction",
                          "--exit-on-error", "--no-owner", "--no-acl", artifact)
            verify_restore(candidate)
        with DurableServer(binary, wal, storage_options=storage) as reopened:
            verify_restore(ramsql_settings(reopened))


def whole_database_ownership(binary, tools, native, inbound):
    def roles(endpoint):
        with psycopg.connect(**endpoint) as db:
            db.execute("CREATE ROLE archive_owner")
            db.execute("CREATE ROLE archive_reader")

    def populate(endpoint):
        with psycopg.connect(**endpoint) as db:
            db.execute("CREATE SCHEMA migration")
            db.execute("CREATE TABLE migration.items (id int PRIMARY KEY, label text DEFAULT 'open')")
            db.execute("ALTER SCHEMA migration OWNER TO archive_owner")
            db.execute("GRANT USAGE ON SCHEMA migration TO archive_reader")
            db.execute("GRANT SELECT ON migration.items TO archive_reader")
            db.execute("COMMENT ON TABLE migration.items IS 'archive fixture'")
            db.execute("INSERT INTO migration.items(id) VALUES (1),(2)")

    def verify_archive(endpoint):
        with psycopg.connect(**endpoint) as db:
            assert db.execute(
                "SELECT r.rolname,n.nspacl::text FROM pg_namespace n "
                "JOIN pg_roles r ON r.oid=n.nspowner WHERE n.nspname='migration'"
            ).fetchone() == ("archive_owner", "{archive_owner=UC/archive_owner,archive_reader=U/archive_owner}")
            assert db.execute(
                "SELECT obj_description('migration.items'::regclass,'pg_class')"
            ).fetchone() == ("archive fixture",)
            db.execute("SET ROLE archive_reader")
            assert db.execute("SELECT * FROM migration.items ORDER BY id").fetchall() == [(1, "open"), (2, "open")]

    label = "owned-inbound" if inbound else "owned-outbound"
    archive = f"{label}.dump"
    roles(native)
    if inbound:
        populate(native)
        wal = tools.root / f"{label}.wal"
        storage = ("--workers", "1")
        with DurableServer(binary, wal, create=True, storage_options=storage) as server:
            candidate = ramsql_settings(server)
            roles(candidate)
            tools.run(f"{label}-dump", "pg_dump", native, "--format=custom", f"--file={archive}")
            tools.run(f"{label}-restore", "pg_restore", candidate, "--single-transaction", "--exit-on-error", archive)
            verify_archive(candidate)
        with DurableServer(binary, wal, storage_options=storage) as reopened:
            verify_archive(ramsql_settings(reopened))
    else:
        with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
            roles(candidate)
            populate(candidate)
            tools.run(f"{label}-dump", "pg_dump", candidate, "--format=custom", f"--file={archive}")
        tools.run(f"{label}-restore", "pg_restore", native, "--single-transaction", "--exit-on-error", archive)
        verify_archive(native)


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
        ("global_role_archives", lambda db: check_global_role_archives(binary, tools, db)),
        ("global_archives", lambda db: check_global_role_archives(binary, tools, db, scope="globals")),
        ("archive_database_ddl", lambda db: check_archive_database_ddl(binary, tools, db)),
        ("named_database_template", lambda db: check_named_database_template(binary, tools, db)),
        ("native_cluster_archive_to_ramsql", lambda db: check_cluster_archive(binary, tools, db, inbound=True)),
        ("ramsql_cluster_archive_to_native", lambda db: check_cluster_archive(binary, tools, db, inbound=False)),
        ("quote_all_identifier_archives", lambda db: check_quoted_archives(binary, tools, db)),
        ("relation_discovery", lambda db: check_relation_discovery(binary, db)),
        ("function_discovery", lambda db: check_function_discovery(binary, db)),
        ("type_discovery", lambda db: check_type_discovery(binary, db)),
        ("operator_discovery", lambda db: check_operator_discovery(binary, db)),
        ("access_method_discovery", lambda db: check_access_method_discovery(binary, db)),
        ("operator_class_discovery", lambda db: check_operator_class_discovery(binary, db, args.reference)),
        ("text_search_parser_discovery", lambda db: check_text_search_parser(binary, db)),
        ("text_search_template_discovery", lambda db: check_text_search_template(binary, db)),
        ("text_search_dictionary_discovery", lambda db: check_text_search_dictionary(binary, db)),
        ("text_search_configuration_discovery", lambda db: check_text_search_configuration(binary, db)),
        ("foreign_data_wrapper_discovery", lambda db: check_foreign_data_wrapper(binary, db)),
        ("default_acl_discovery", lambda db: check_default_acl(binary, db)),
        ("conversion_discovery", lambda db: check_conversion(binary, db)),
        ("range_discovery", lambda db: check_ranges(binary, db)),
        ("event_trigger_discovery", lambda db: check_event_triggers(binary, db)),
        ("column_metadata_discovery", lambda db: check_column_metadata(binary, db, args.reference)),
        ("legacy_attribute_archives", lambda _: check_legacy_attribute_archives(binary, tools, args.reference)),
        ("column_default_discovery", lambda db: check_column_defaults(binary, db)),
        ("constraint_and_index_discovery", lambda db: check_constraints_and_indexes(binary, db)),
        ("partition_discovery", lambda db: check_partitions(binary, db)),
        ("subscription_discovery", lambda db: check_subscriptions(binary, db)),
        ("archive_metadata", lambda db: check_archive_metadata(binary, db)),
        ("scoped_identity_sequences", lambda db: check_scoped_identities(binary, tools, db)),
        ("ramsql_identity_archive_to_native", lambda db: export_identity_archive(binary, tools, db)),
        ("native_identity_archive_to_ramsql", lambda db: import_identity_archive(binary, tools, db)),
        ("multiple_identity_archives", lambda db: check_multiple_identities(binary, tools, db)),
        ("serial_archives", lambda db: check_serial_archives(binary, tools, db)),
        ("public_serial_archives", lambda db: check_serial_archives(binary, tools, db, scoped=False)),
        ("scoped_sequence_archives", lambda db: check_sequence_archives(binary, tools, db)),
        ("stored_generated_archives", lambda db: check_generated_archives(binary, tools, db)),
        ("view_archives", lambda db: check_view_archives(binary, tools, db)),
        ("view_replacement_schema_privileges", lambda db: check_view_schema_privileges(binary, tools, db)),
        ("view_column_name_archives", lambda db: check_view_column_name_archives(binary, tools, db)),
        ("materialized_view_archives", lambda db: check_materialized_view_archives(binary, tools, db)),
        ("composite_primary_key_archives", lambda db: check_composite_key_archives(binary, tools, db)),
        ("late_composite_primary_key", lambda db: check_late_composite_key(binary, tools, db)),
        ("native_dump_to_ramsql", lambda db: import_dumps(binary, tools, db)),
        ("ramsql_dump_to_native", lambda db: export_archive(binary, tools, db)),
        ("ramsql_database_dump_to_native", lambda db: export_archive(binary, tools, db, table_options=(), label="whole-database")),
        ("native_database_with_acl_to_ramsql", lambda db: whole_database_ownership(binary, tools, db, inbound=True)),
        ("ramsql_database_with_acl_to_native", lambda db: whole_database_ownership(binary, tools, db, inbound=False)),
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


def check_global_role_archives(binary, tools, native, *, scope="roles"):
    """Exercise stock role discovery and passwordless pg_dumpall restore."""
    expected_defaults = {
        "check_function_bodies": "off",
        "default_transaction_deferrable": "on",
        "default_transaction_read_only": "off",
        "quote_all_identifiers": "on",
        "row_security": "on",
        "synchronize_seqscans": "off",
    }
    expected = sorted(f"{name}={value}" for name, value in expected_defaults.items())

    def populate(arguments, prefix):
        with psycopg.connect(**arguments) as db:
            db.execute(f'CREATE ROLE "{prefix} group" NOLOGIN')
            db.execute(f'CREATE ROLE "{prefix} login" LOGIN')
            db.execute(f'CREATE ROLE "{prefix} limited" LOGIN')
            db.execute(f'GRANT "{prefix} group" TO "{prefix} login" WITH ADMIN OPTION')
            db.execute(f'GRANT "{prefix} group" TO "{prefix} limited" WITH INHERIT FALSE, SET FALSE')
            for setting, value in expected_defaults.items():
                db.execute(f"""ALTER ROLE "{prefix} login" SET {setting} TO '{value}'""")

    def verify_roles(arguments, prefix):
        with psycopg.connect(**arguments) as db:
            assert db.execute(
                "SELECT rolname,rolcanlogin FROM pg_roles WHERE rolname IN (%s,%s,%s) ORDER BY rolname",
                (prefix + " group", prefix + " limited", prefix + " login"),
            ).fetchall() == [(prefix + " group", False), (prefix + " limited", True), (prefix + " login", True)]
            assert db.execute(
                "SELECT m.rolname,a.admin_option,a.inherit_option,a.set_option,pg_get_userbyid(a.grantor) "
                "FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid "
                "JOIN pg_roles m ON m.oid=a.member WHERE r.rolname=%s ORDER BY m.rolname",
                (prefix + " group",),
            ).fetchall() == [(prefix + " limited", False, False, False, "ramsql"),
                             (prefix + " login", True, True, True, "ramsql")]
            defaults = db.execute(
                "SELECT unnest(setconfig) FROM pg_db_role_setting WHERE setdatabase=0 "
                "AND setrole=(SELECT oid FROM pg_roles WHERE rolname=%s)",
                (prefix + " login",),
            ).fetchall()
            assert sorted(value for (value,) in defaults) == expected
            array = db.execute(
                "SELECT setconfig FROM pg_db_role_setting WHERE setdatabase=0 "
                "AND setrole=(SELECT oid FROM pg_roles WHERE rolname=%s)",
                (prefix + " login",),
            ).fetchone()
            assert sorted(array[0]) == expected

    def dump(label, arguments):
        archive = f"{label}.sql"
        tools.run(label, "pg_dumpall", arguments, f"--{scope}-only", "--no-role-passwords",
                  "--quote-all-identifiers", f"--file={archive}")
        source = (tools.root / archive).read_text(encoding="utf-8")
        assert 'CREATE ROLE "ramsql";' in source and '''SET "quote_all_identifiers" TO 'on';''' in source
        return archive

    def restore(label, arguments, archive):
        # Both new servers already have their bootstrap superuser. pg_dumpall
        # includes its CREATE ROLE and restores the rest of the archive.
        tools.run(label, "psql", arguments, "-X", "--set=ON_ERROR_STOP=off", "--set=VERBOSITY=verbose",
                  f"--file={archive}")
        stderr = (tools.root / f"{label}.stderr").read_text(encoding="utf-8")
        assert re.findall(r"\bERROR:\s+(\w{5}):", stderr) == ["42710"], stderr

    populate(native, "native archive")
    inbound = dump(f"{scope}-inbound-dump", native)
    wal = tools.root / f"{scope}-archive.wal"
    storage = ("--workers", "1")
    with DurableServer(binary, wal, create=True, storage_options=storage) as server:
        candidate = ramsql_settings(server)
        restore(f"{scope}-inbound-restore", candidate, inbound)
        verify_roles(candidate, "native archive")
        with psycopg.connect(**candidate) as db:
            db.execute("CREATE TABLE restored_role_defaults AS SELECT setconfig FROM pg_db_role_setting")
    with DurableServer(binary, wal, storage_options=storage) as reopened:
        candidate = ramsql_settings(reopened)
        verify_roles(candidate, "native archive")
        with psycopg.connect(**candidate) as db:
            assert sorted(db.execute("SELECT setconfig FROM restored_role_defaults").fetchone()[0]) == expected

    with running_ramsql(binary, extra_arguments=storage) as candidate:
        populate(candidate, "ramsql archive")
        outbound = dump(f"{scope}-outbound-dump", candidate)
    restore(f"{scope}-outbound-restore", native, outbound)
    verify_roles(native, "ramsql archive")


def check_archive_database_ddl(binary, tools, native):
    """Exercise stock database declarations separately from template connections."""
    pristine = "SELECT oid, datistemplate, datallowconn FROM pg_database WHERE datname='template0'"
    with psycopg.connect(**native) as db:
        reference_template = db.execute(pristine).fetchone()
        assert reference_template == (4, True, False)
        db.execute('''CREATE DATABASE "Archive Child" WITH TEMPLATE template0 ENCODING 'UTF8'
                      LOCALE_PROVIDER libc LOCALE 'C' ''')
    archive = "database-ddl.sql"
    tools.run("database-ddl-dump", "pg_dumpall", native, "--no-role-passwords",
              "--quote-all-identifiers", f"--file={archive}")
    sql = (tools.root / archive).read_text(encoding="utf-8")
    commands = [line for line in sql.splitlines()
                if line.startswith(('CREATE DATABASE "Archive Child"', 'ALTER DATABASE "Archive Child" OWNER'))]
    assert len(commands) == 2, commands

    cluster = tools.root / "database-ddl-cluster"
    with DurableServer(binary, cluster, create=True, storage_options=SMALL_PROFILE) as server:
        candidate = ramsql_settings(server) | {"dbname": "postgres"}
        with psycopg.connect(**candidate) as db:
            assert db.execute(pristine).fetchone() == reference_template
            for command in commands:
                db.execute(command)
            assert db.execute(
                "SELECT encoding,datlocprovider,datcollate,datctype FROM pg_database WHERE datname='Archive Child'"
            ).fetchone() == (6, "c", "C", "C")
        with psycopg.connect(**(candidate | {"dbname": "Archive Child"})) as child:
            child.execute("CREATE TABLE archive_rows(id integer PRIMARY KEY)")
            child.execute("INSERT INTO archive_rows VALUES (97)")
    with DurableServer(binary, cluster, storage_options=SMALL_PROFILE) as reopened:
        child = ramsql_settings(reopened) | {"dbname": "Archive Child"}
        with psycopg.connect(**child) as db:
            assert db.execute("SELECT id FROM archive_rows").fetchall() == [(97,)]


def check_named_database_template(binary, tools, native):
    def clone(settings):
        with psycopg.connect(**settings) as db:
            db.execute('CREATE DATABASE "Archive Seed" WITH TEMPLATE template0')
        with psycopg.connect(**(settings | {"dbname": "Archive Seed"})) as db:
            db.execute("CREATE TABLE template_rows(id integer PRIMARY KEY)")
            db.execute("INSERT INTO template_rows VALUES (4), (15)")
            db.execute('CREATE DATABASE "Archive Copy" WITH TEMPLATE "Archive Seed"')
            db.execute("DELETE FROM template_rows WHERE id = 4")

    def contents(settings):
        result = []
        for name in ("Archive Seed", "Archive Copy"):
            with psycopg.connect(**(settings | {"dbname": name})) as db:
                result.append(db.execute("SELECT id FROM template_rows ORDER BY id").fetchall())
        return result

    clone(native)
    expected = contents(native)
    assert expected == [[(15,)], [(4,), (15,)]]
    cluster = tools.root / "named-template-cluster"
    with DurableServer(binary, cluster, create=True, storage_options=SMALL_PROFILE) as server:
        candidate = ramsql_settings(server) | {"dbname": "postgres"}
        clone(candidate)
        assert contents(candidate) == expected
    with DurableServer(binary, cluster, storage_options=SMALL_PROFILE) as reopened:
        assert contents(ramsql_settings(reopened)) == expected


def check_cluster_archive(binary, tools, native, *, inbound):
    # The native test harness creates an additional ramsql database in the
    # container's OS locale. The fixture uses the built-in databases and an
    # additional UTF-8, C-locale database created explicitly below.
    databases = (("template1", 8), ("postgres", 15), ("Cluster Archive", 21))
    options = ("--database-cluster", "--rows", "32", "--versions", "64",
               "--work-rows", "4096", "--result-rows", "1024")

    def populate(settings):
        with psycopg.connect(**settings) as db:
            db.execute('''CREATE DATABASE "Cluster Archive" WITH TEMPLATE template0
                          ENCODING 'UTF8' LOCALE_PROVIDER libc LOCALE 'C' ''')
        for name, key in databases:
            with psycopg.connect(**(settings | {"dbname": name})) as db:
                db.execute("CREATE TABLE cluster_marker(id integer PRIMARY KEY)")
                db.execute("INSERT INTO cluster_marker VALUES (%s)", (key,))

    def proof(settings):
        for name, key in databases:
            with psycopg.connect(**(settings | {"dbname": name})) as db:
                assert db.execute("SELECT id FROM cluster_marker").fetchall() == [(key,)], name
                assert db.execute("SELECT current_database(), (SELECT count(*) FROM pg_database "
                                  "WHERE datname='template1' AND datistemplate AND datallowconn)").fetchone() == (name, 1)

    label = "cluster-inbound" if inbound else "cluster-outbound"
    archive = f"{label}.sql"

    def dump(settings, *extra):
        tools.run(f"{label}-dump", "pg_dumpall", settings, "--no-role-passwords",
                  "--quote-all-identifiers", *extra, f"--file={archive}")

    def restore(settings):
        tools.run(f"{label}-restore", "psql", settings, "-X",
                  "--set=ON_ERROR_STOP=off", "--set=VERBOSITY=verbose", f"--file={archive}")
        stderr = (tools.root / f"{label}-restore.stderr").read_text(encoding="utf-8")
        assert re.findall(r"\bERROR:\s+(\w{5}):", stderr) == ["42710"], stderr

    cluster = tools.root / label
    if inbound:
        populate(native)
        dump(native, "--exclude-database=ramsql")
        with DurableServer(binary, cluster, create=True, storage_options=options) as server:
            settings = ramsql_settings(server) | {"dbname": "postgres"}
            restore(settings)
            proof(settings)
        with DurableServer(binary, cluster, storage_options=options) as reopened:
            proof(ramsql_settings(reopened))
    else:
        with DurableServer(binary, cluster, create=True, storage_options=options) as server:
            settings = ramsql_settings(server) | {"dbname": "postgres"}
            populate(settings)
            dump(settings)
        restore(native | {"dbname": "postgres"})
        proof(native)


def check_quoted_archives(binary, tools, native):
    def populate(arguments, schema):
        with psycopg.connect(**arguments) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f'CREATE TABLE {schema}.items ("item""id" integer PRIMARY KEY, label text NOT NULL)')
            db.execute(f"CREATE INDEX label_idx ON {schema}.items (label)")
            db.execute(f'CREATE TABLE {schema}.lines (pid integer REFERENCES {schema}.items ("item""id"))')
            db.execute(f'INSERT INTO {schema}.items VALUES (7, %s)', ("it's data",))
            db.execute(f"INSERT INTO {schema}.lines VALUES (7)")

    def verify_schema(arguments, schema):
        with psycopg.connect(**arguments) as db:
            assert db.execute(
                f'SELECT i."item""id", i.label FROM {schema}.items i JOIN {schema}.lines l ON l.pid = i."item""id"'
            ).fetchall() == [(7, "it's data")]
            assert db.execute(
                "SELECT count(*) FROM pg_indexes WHERE schemaname=%s AND indexname='label_idx'", (schema,)
            ).fetchone() == (1,)
            try:
                db.execute(f"INSERT INTO {schema}.lines VALUES (99)")
            except psycopg.Error as error:
                assert error.sqlstate == "23503", error
            else:
                raise AssertionError("restored foreign key accepted a missing row")

    def dump(label, arguments, schema):
        archive = f"{label}.dump"
        tools.run(label, "pg_dump", arguments, "--quote-all-identifiers", "--format=custom",
                  f"--schema={schema}", f"--file={archive}")
        tools.run(f"{label}-sql", "pg_restore", None, "--file=-", archive)
        source = (tools.root / f"{label}-sql.stdout").read_text(encoding="utf-8")
        fragments = (
            '"item""id" integer NOT NULL',
            '"label" "text" NOT NULL',
            f'CREATE INDEX "label_idx" ON "{schema}"."items" USING "btree" ("label")',
            f'FOREIGN KEY ("pid") REFERENCES "{schema}"."items"("item""id")',
        )
        for fragment in fragments:
            assert fragment in source, f"{label} did not emit {fragment!r}"
        return archive

    def restore(label, arguments, archive):
        tools.run(label, "pg_restore", arguments, "--single-transaction", "--exit-on-error", archive)

    populate(native, "native_archive")
    inbound = dump("quote-inbound", native, "native_archive")
    wal = tools.root / "quoted-archives.wal"
    with DurableServer(binary, wal, create=True, storage_options=("--workers", "1")) as server:
        candidate = ramsql_settings(server)
        restore("quote-inbound-restore", candidate, inbound)
        verify_schema(candidate, "native_archive")
    with DurableServer(binary, wal, storage_options=("--workers", "1")) as reopened:
        verify_schema(ramsql_settings(reopened), "native_archive")

    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        populate(candidate, "ramsql_archive")
        outbound = dump("quote-outbound", candidate, "ramsql_archive")
    restore("quote-outbound-restore", native, outbound)
    verify_schema(native, "ramsql_archive")


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
            # Language discovery is the next stock query after type discovery.
            language_acl = "SELECT lanname, lanowner, lanacl, acldefault('l',lanowner)::text FROM pg_language WHERE lanispl AND lanname='plpgsql'"
            assert pg.execute(language_acl).fetchall() == ram.execute(language_acl).fetchall()


def check_type_discovery(binary, native):
    # Preserve the catalog joins and predicates used by pg_dump. Compare
    # link presence for user types whose OIDs differ across the two instances.
    query = """
        SELECT tableoid, typname, typnamespace, typacl, acldefault('T',typowner)::text,
               typowner, typelem <> 0, typrelid <> 0, typarray <> 0,
               CASE WHEN typrelid = 0 THEN ' '::"char"
                    ELSE (SELECT relkind FROM pg_class WHERE oid = typrelid) END,
               typtype, typisdefined,
               typname[0] = '_' AND typelem != 0
                 AND (SELECT typarray FROM pg_type te WHERE oid = pg_type.typelem) = oid AS isarray
        FROM pg_type
        WHERE typname IN ('int4','_int4','int4range','_int4range',
                          'dump_enum','_dump_enum','dump_domain','_dump_domain',
                          'dump_composite','_dump_composite')
        ORDER BY typname COLLATE "C"
    """
    subscripts = """
        SELECT ('abc'::name)[0], ('abc'::name)[2], ('abc'::name)[3],
               ('abc'::name)[63], ('abc'::name)[64], ('abc'::name)[-1],
               ('abc'::name)[NULL], ('abc'::name)[0][1],
               ('é'::name)[0]::int, ('é'::name)[1]::int
    """
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for conn in (pg, ram):
                conn.execute("CREATE TYPE dump_enum AS ENUM ('one','two')")
                conn.execute("CREATE DOMAIN dump_domain AS integer")
                conn.execute("CREATE TYPE dump_composite AS (value integer)")
            for sql, count in ((query, 10), (subscripts, 1)):
                ref, observed = pg.execute(sql), ram.execute(sql)
                expected, actual = ref.fetchall(), observed.fetchall()
                assert len(expected) == count and expected == actual, (expected, actual)
                assert [c.type_code for c in ref.description] == [c.type_code for c in observed.description]


OPERATOR_OPERANDS = {
    "int2": ("13", "3"), "int4": ("13", "3"), "int8": ("13", "3"),
    "float4": ("13.5", "2.5"), "float8": ("13.5", "2.5"), "numeric": ("13.25", "2.75"),
    "bool": ("true", "false"), "oid": ("1000", "26"), "char": ("a", "b"),
    "name": ("alpha", "beta"), "text": ("alpha", "beta"), "bpchar": ("alpha ", "beta"),
    "bytea": (r"\xAB12", r"\xBA11"),
    "uuid": ("00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"),
    "date": ("2022-01-03", "2021-12-20"), "timestamp": ("2022-01-03 01:02:03", "2021-12-20 15:01:08"),
    "timestamptz": ("2022-01-03 01:02:03+00", "2021-12-20 15:01:08+00"),
    "time": ("01:02:03", "00:01:08"), "timetz": ("01:02:03+00", "00:01:08+00"), "interval": ("3 days", "2 days"),
    "inet": ("10.0.0.0/24", "10.0.0.0/8"), "macaddr": ("00:00:00:00:00:02", "00:00:00:00:00:01"),
    "bit": ("10101", "11001"), "varbit": ("10101", "11001"),
    "json": ('{"alpha":13,"beta":2}', "[13,14,15]"), "jsonb": ('{"alpha":13,"beta":2}', '{"alpha":13}'),
    "tsvector": ("alpha beta", "alpha"), "tsquery": ("alpha", "beta"),
    "point": ("(1,2)", "(2,3)"), "box": ("(4,4),(1,1)", "(3,3),(2,2)"),
    "money": ("10.50", "2.25"), "pg_lsn": ("0/20", "0/10"),
    "_text": ("{alpha,beta}", "{alpha}"), "jsonpath": ("$.alpha", "$.alpha == 13"),
}
OPERATOR_TOKENS = frozenset(
    "= <> < <= > >= + - * / % & | # ~ << >> || @> <@ && <<= >>= <-> ~= -> ->> #> #>> ? ?| ?& @? @@ #- ~* !~ !~*".split()
)


def operator_operand(type_name, side, operator, other_type):
    value = OPERATOR_OPERANDS[type_name][side]
    if side == 1 and type_name in ("int2", "int4", "int8") and operator in ("<<", ">>", "->", "->>", "-"):
        value = "1"
    if side == 0 and type_name in ("json", "jsonb") and other_type == "int4" and operator in ("->", "->>"):
        value = "[11,12,13]"
    if side == 1 and type_name == "text" and operator in ("~", "~*", "!~", "!~*"):
        value = "^a"
    cast = {"char": '"char"', "_text": "text[]", "bit": "bit(5)"}.get(type_name, type_name)
    value = value.replace("'", "''")
    return f"('{value}'::{cast})"


def check_operator_discovery(binary, native):
    operator_fields = (
        "tableoid,oid,oprname,oprnamespace,oprowner,oprkind,"
        "oprleft,oprright,oprresult,oprcode::oid"
    )
    procedure_fields = "oid,proname,pronamespace,provolatile,proisstrict,pronargs,prorettype,proargtypes::text"
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            actual = ram.execute(f"SELECT {operator_fields} FROM pg_catalog.pg_operator ORDER BY oid")
            operators = actual.fetchall()
            assert len(operators) >= 400, len(operators)
            oids = [row[1] for row in operators]
            expected = pg.execute(
                f"SELECT {operator_fields} FROM pg_catalog.pg_operator WHERE oid=ANY(%s) ORDER BY oid", (oids,)
            )
            assert [col.type_code for col in actual.description] == [col.type_code for col in expected.description]
            assert operators == expected.fetchall()

            routine_oids = {row[-1] for row in operators}
            operator_routines = [
                row for row in ram.execute(f"SELECT {procedure_fields},prolang FROM pg_proc WHERE pronamespace=11 ORDER BY oid")
                if row[0] in routine_oids
            ]
            assert all(row[-1] == 12 for row in operator_routines)
            routines = [row[:-1] for row in operator_routines]
            procedure_oids = sorted(routine_oids)
            assert [row[0] for row in routines] == procedure_oids
            native_routines = pg.execute(
                f"SELECT {procedure_fields} FROM pg_proc WHERE oid=ANY(%s) ORDER BY oid", (procedure_oids,)
            ).fetchall()
            assert len(routines) == len(native_routines)
            for observed_proc, expected_proc in zip(routines, native_routines):
                assert observed_proc == expected_proc, (observed_proc, expected_proc)
            types = dict(ram.execute("SELECT oid,typname FROM pg_type").fetchall())
            for _, oid, symbol, _, _, _, left_oid, right_oid, result_oid, _ in operators:
                assert symbol in OPERATOR_TOKENS, symbol
                lhs_type, rhs_type = types.get(left_oid), types[right_oid]
                lhs = operator_operand(lhs_type, 0, symbol, rhs_type) if lhs_type else ""
                rhs = operator_operand(rhs_type, 1, symbol, lhs_type)
                query = f"SELECT {lhs} {symbol} {rhs}"
                ref, observed = pg.execute(query), ram.execute(query)
                assert ref.description[0].type_code == observed.description[0].type_code == result_oid, (oid, query)
                assert ref.fetchall() == observed.fetchall(), (oid, query)


def check_access_method_discovery(binary, native):
    class RegprocOid(int):
        pass

    class RegprocDumper(psycopg.adapt.Dumper):
        oid = 24
        format = psycopg.pq.Format.BINARY

        def dump(self, value):
            return value.to_bytes(4, "big")

    projection = """
        SELECT tableoid, oid, amname, amtype, amhandler::pg_catalog.regproc AS amhandler
        FROM pg_am WHERE amname IN ('heap','btree') ORDER BY oid
    """
    handlers = """
        SELECT a.amname,p.oid,p.proname,p.prolang,p.proretType,t.typname,p.proargtypes::text
        FROM pg_am a JOIN pg_proc p ON p.oid=a.amhandler::oid JOIN pg_type t ON t.oid=p.prorettype
        WHERE a.amname IN ('heap','btree') ORDER BY a.oid
    """
    aliases = """
        SELECT to_regproc(' PG_CATALOG . "bthandler" ')::oid,
               'heap_tableam_handler'::regproc::text, 330::regproc::text,
               '4000000000'::regproc::text, '-'::regproc::text,
               to_regproc('missing_access_probe'), pg_typeof(NULL::regproc)::text
    """
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for query in (projection, handlers, aliases):
                ref, actual = pg.execute(query), ram.execute(query)
                assert ref.fetchall() == actual.fetchall(), query
                assert [c.type_code for c in ref.description] == [c.type_code for c in actual.description], query
            assert ram.execute("SELECT COUNT(*) FROM pg_am").fetchone() == (2,)
            binary_query = "SELECT amhandler FROM pg_am WHERE amname IN ('heap','btree') ORDER BY oid"
            ref, actual = pg.execute(binary_query, binary=True), ram.execute(binary_query, binary=True)
            assert ref.description[0].type_code == actual.description[0].type_code == 24
            assert ref.fetchall() == actual.fetchall()
            for conn in (pg, ram):
                conn.adapters.register_dumper(RegprocOid, RegprocDumper)
                conn.execute("CREATE FUNCTION bthandler(integer) RETURNS integer LANGUAGE SQL AS 'SELECT 0'")
                assert conn.execute("SELECT to_regproc('bthandler')").fetchone() == (None,)
                assert conn.execute("SELECT %b::oid", (RegprocOid(330),)).fetchone() == (330,)
                conn.execute("CREATE FUNCTION access_probe(integer) RETURNS integer LANGUAGE SQL AS 'SELECT 1'")
                assert conn.execute("SELECT to_regproc('public.access_probe')::text").fetchone() == ("access_probe",)
                assert conn.execute("SELECT %s::regproc::text", ("public.access_probe",)).fetchone() == ("access_probe",)
                conn.execute("CREATE FUNCTION access_probe(text) RETURNS integer LANGUAGE SQL AS 'SELECT 2'")
                assert conn.execute("SELECT to_regproc('access_probe')").fetchone() == (None,)
                try:
                    conn.execute("SELECT 'access_probe'::regproc")
                except psycopg.Error as err:
                    assert err.sqlstate == "42725", err
                else:
                    raise AssertionError("regproc accepted an overloaded routine name")


def check_operator_class_discovery(binary, native, reference):
    fields = (
        "c.tableoid,c.oid,c.opcmethod,c.opcname,c.opcnamespace,c.opcowner,"
        "c.opcfamily,c.opcintype,c.opcdefault,c.opckeytype,"
        "a.amname,t.typname,f.opfname,f.opfmethod"
    )
    joins = (
        "FROM pg_opclass c LEFT JOIN pg_am a ON a.oid=c.opcmethod "
        "LEFT JOIN pg_type t ON t.oid=c.opcintype LEFT JOIN pg_opfamily f ON f.oid=c.opcfamily"
    )
    family_fields = "tableoid,oid,opfmethod,opfname,opfnamespace,opfowner"
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            actual = ram.execute(f"SELECT {fields} {joins}")
            classes = sorted(actual.fetchall(), key=lambda row: row[3])
            assert len(classes) == 26, len(classes)
            assert len({row[1] for row in classes}) == len(classes)
            names = [row[3] for row in classes]
            ref = pg.execute(f"SELECT {fields} {joins} WHERE c.opcmethod=403 AND c.opcname::text=ANY(%s)", (names,))
            assert [c.type_code for c in ref.description] == [c.type_code for c in actual.description]
            native_classes = sorted(ref.fetchall(), key=lambda row: row[3])
            assert len(native_classes) == len(classes)
            for observed, expected in zip(classes, native_classes):
                # PostgreSQL assigns most class OIDs during bootstrap. The
                # candidate advertises a PostgreSQL 16 server catalog.
                if reference == "16" or observed[1] < 10000:
                    assert observed[1] == expected[1], (observed, expected)
                assert observed[:1] + observed[2:] == expected[:1] + expected[2:], (observed, expected)

            family_oids = sorted({row[6] for row in classes})
            actual = ram.execute(f"SELECT {family_fields} FROM pg_opfamily ORDER BY oid")
            families = actual.fetchall()
            assert [row[1] for row in families] == family_oids
            ref = pg.execute(
                f"SELECT {family_fields} FROM pg_opfamily WHERE oid=ANY(%s) ORDER BY oid", (family_oids,)
            )
            assert [c.type_code for c in ref.description] == [c.type_code for c in actual.description]
            assert ref.fetchall() == families

            native_oids = {row[3]: row[1] for row in native_classes}
            for _, _, method, name, _, _, _, input_oid, default, key_oid, am, type_name, family, family_am in classes:
                assert am == "btree" and default and key_oid == 0 and family and method == family_am, name
                cast = {"char": '"char"', "bit": "bit(5)"}.get(type_name, type_name)
                first, second = (operator_operand(type_name, side, "=", type_name) for side in (0, 1))
                reads = []
                for conn in (pg, ram):
                    conn.execute(f"CREATE TABLE class_probe (v {cast})")
                    conn.execute("CREATE UNIQUE INDEX class_probe_uq ON class_probe USING btree (v)")
                    if conn is pg:
                        selected = pg.execute(
                            "SELECT indclass[0] FROM pg_index WHERE indexrelid='class_probe_uq'::regclass"
                        ).fetchone()
                        assert selected == (native_oids[name],), (name, selected)
                    conn.execute(f"INSERT INTO class_probe VALUES ({second}),({first})")
                    # Each iteration recreates the column with a new type.
                    ordered = conn.execute("SELECT v FROM class_probe ORDER BY v", prepare=False)
                    assert ordered.description[0].type_code == input_oid, name
                    matched = conn.execute(f"SELECT v FROM class_probe WHERE v={first}", prepare=False)
                    reads.append((ordered.fetchall(), matched.fetchall()))
                    try:
                        conn.execute(f"INSERT INTO class_probe VALUES ({first})")
                    except psycopg.Error as error:
                        assert error.sqlstate == "23505", (name, error)
                        conn.rollback()
                    else:
                        raise AssertionError(f"{name} unique index accepted a duplicate")
                    conn.execute("DROP TABLE class_probe")
                assert reads[0] == reads[1], (name, reads)


def check_text_search_parser(binary, native):
    stock_projection = (
        "SELECT tableoid,oid,prsname,prsnamespace,prsstart::oid,prstoken::oid,"
        "prsend::oid,prsheadline::oid,prslextype::oid FROM pg_ts_parser ORDER BY oid"
    )
    typed_callbacks = "SELECT prsstart,prstoken,prsend,prsheadline,prslextype FROM pg_ts_parser"
    implementation = (
        "SELECT oid,proname,pronamespace,prolang,provolatile,proisstrict,pronargs,"
        "prorettype,proargtypes::text FROM pg_proc WHERE oid IN (3717,3718,3719,3721) ORDER BY oid"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for query, absent_headline in ((stock_projection, 0), (typed_callbacks, "-")):
                ref, actual = pg.execute(query), ram.execute(query)
                assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description]
                expected, observed = ref.fetchone(), actual.fetchone()
                assert expected and observed and ref.fetchone() is None and actual.fetchone() is None
                # The Sig tokenizer has no headline generator.
                assert observed == expected[:-2] + (absent_headline, expected[-1]), (expected, observed)
            ref, actual = pg.execute(implementation), ram.execute(implementation)
            assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description]
            expected, observed = ref.fetchall(), actual.fetchall()
            assert len(expected) == 4 and observed == expected
            assert ram.execute("SELECT to_regproc('pg_catalog.prsd_end')::oid").fetchone() == (3719,)
            assert ram.execute("SELECT to_regproc('prsd_headline')").fetchone() == (None,)
            assert pg.execute("SELECT cfgparser FROM pg_ts_config WHERE cfgname='simple'").fetchone() == (3722,)

            for sample in ("foo-bar-beta1", "http://example.com/a", "<b>Foo</b> &amp; BAR", "foo@example.com -1234 8.3.0"):
                query = "SELECT to_tsvector('pg_catalog.simple',%s)::text"
                expected = pg.execute(query, (sample,)).fetchone()
                assert ram.execute(query, (sample,)).fetchone() == expected, (sample, expected)


def check_text_search_template(binary, native):
    stock_projection = (
        "SELECT tableoid,oid,tmplname,tmplnamespace,tmplinit::oid,tmpllexize::oid "
        "FROM pg_ts_template WHERE tmplname='simple'"
    )
    typed_callbacks = "SELECT tmplinit,tmpllexize FROM pg_ts_template WHERE tmplname='simple'"
    implementations = (
        "SELECT oid,proname,pronamespace,prolang,provolatile,proisstrict,pronargs,prorettype,proargtypes::text "
        "FROM pg_proc WHERE oid IN (3725,3726) ORDER BY oid"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for query, binary_output, count in (
                (stock_projection, False, 1), (typed_callbacks, False, 1),
                (typed_callbacks, True, 1), (implementations, False, 2),
            ):
                ref = pg.execute(query, binary=binary_output)
                actual = ram.execute(query, binary=binary_output)
                assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description]
                expected, observed = ref.fetchall(), actual.fetchall()
                assert len(expected) == count and observed == expected, (query, expected, observed)
            assert ram.execute("SELECT COUNT(*) FROM pg_ts_template").fetchone() == (1,)
            assert ram.execute(
                "SELECT to_regproc('pg_catalog.dsimple_init')::oid,to_regproc('dsimple_lexize')::oid"
            ).fetchone() == (3725, 3726)
            # The default simple dictionary folds ASCII and keeps words such
            # as "the" and "running" without a custom stop list or stemmer.
            for sample in ("The", "RUNNING", "MixEd123"):
                lexemes = pg.execute("SELECT ts_lexize('pg_catalog.simple',%s)", (sample,)).fetchone()[0]
                assert lexemes == [sample.lower()], (sample, lexemes)
                query = "SELECT to_tsvector('pg_catalog.simple',%s)::text,plainto_tsquery('pg_catalog.simple',%s)::text"
                args = (sample, sample)
                assert ram.execute(query, args).fetchone() == pg.execute(query, args).fetchone(), sample


def check_text_search_dictionary(binary, native):
    stock_projection = (
        "SELECT tableoid,oid,dictname,dictnamespace,dictowner,dicttemplate,dictinitoption "
        "FROM pg_ts_dict"
    )
    linked_objects = (
        "SELECT n.nspname,r.oid,t.tmplname,t.tmplinit::oid,t.tmpllexize::oid "
        "FROM pg_catalog.pg_ts_dict d JOIN pg_namespace n ON n.oid=d.dictnamespace "
        "JOIN pg_roles r ON r.oid=d.dictowner JOIN pg_ts_template t ON t.oid=d.dicttemplate "
        "WHERE d.dictname='simple'"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for binary_output in (False, True):
                ref = pg.execute(stock_projection, binary=binary_output)
                actual = ram.execute(stock_projection, binary=binary_output)
                assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description]
                expected = [row for row in ref.fetchall() if row[2] == "simple"]
                observed = actual.fetchall()
                assert expected == [(3600, 3765, "simple", 11, 10, 3727, None)]
                assert observed == expected, (expected, observed)
            assert ram.execute(linked_objects).fetchall() == pg.execute(linked_objects).fetchall() == [
                ("pg_catalog", 10, "simple", 3725, 3726)
            ]


def check_text_search_configuration(binary, native):
    stock_projection = "SELECT tableoid,oid,cfgname,cfgnamespace,cfgowner,cfgparser FROM pg_ts_config"
    linked_objects = (
        "SELECT n.nspname,r.oid,p.prsname,p.prsstart::oid "
        "FROM pg_catalog.pg_ts_config c JOIN pg_namespace n ON n.oid=c.cfgnamespace "
        "JOIN pg_roles r ON r.oid=c.cfgowner JOIN pg_ts_parser p ON p.oid=c.cfgparser "
        "WHERE c.cfgname='simple' AND n.nspname='pg_catalog'"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for binary_output in (False, True):
                ref = pg.execute(stock_projection, binary=binary_output)
                actual = ram.execute(stock_projection, binary=binary_output)
                assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description]
                expected = [row for row in ref.fetchall() if row[2:4] == ("simple", 11)]
                observed = actual.fetchall()
                assert expected == [(3602, 3748, "simple", 11, 10, 3722)]
                assert observed == expected, (expected, observed)
            assert ram.execute(linked_objects).fetchall() == pg.execute(linked_objects).fetchall() == [
                ("pg_catalog", 10, "default", 3717)
            ]


def check_foreign_data_wrapper(binary, native):
    format_options = (
        "array_to_string(ARRAY(SELECT quote_ident(option_name) || ' ' || quote_literal(option_value) "
        "FROM pg_options_to_table(fdwoptions) ORDER BY option_name), E',\\n    ')"
    )
    stock_query = (
        "SELECT tableoid, oid, fdwname, fdwowner, fdwhandler::pg_catalog.regproc, "
        "fdwvalidator::pg_catalog.regproc, fdwacl, acldefault('F', fdwowner) AS acldefault, "
        + format_options + " AS fdwoptions FROM pg_foreign_data_wrapper"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            def compare(query, params=(), *, binary_output=False, normalize=lambda rows: rows):
                try:
                    ref = pg.execute(query, params, binary=binary_output)
                    actual = ram.execute(query, params, binary=binary_output)
                except psycopg.Error as error:
                    raise AssertionError(f"{query} ({error.sqlstate}): {error}") from error
                expected_types = [col.type_code for col in ref.description]
                observed_types = [col.type_code for col in actual.description]
                assert expected_types == observed_types, (query, expected_types, observed_types)
                expected, observed = normalize(ref.fetchall()), normalize(actual.fetchall())
                assert observed == expected, (query, expected, observed)
                return expected

            rows = compare(stock_query)
            assert rows == [], rows
            for db in (pg, ram):
                db.execute("CREATE FOREIGN DATA WRAPPER dump_wrapper")
            # Object OIDs are allocated independently by the two engines.
            normalize_oid = lambda rows: [(row[0], *row[2:]) for row in rows]
            compare(stock_query, normalize=normalize_oid)
            text_query = (
                "SELECT tableoid,fdwname,fdwowner,fdwhandler::regproc::text,fdwvalidator::regproc::text,"
                "fdwacl IS NULL,acldefault('F',fdwowner)::text," + format_options + " FROM pg_foreign_data_wrapper"
            )
            rows = compare(text_query)
            assert rows == [(2328, "dump_wrapper", 10, "-", "-", True, "{ramsql=U/ramsql}", "")], rows

            options_query = "SELECT * FROM pg_catalog.pg_options_to_table(%s::text[]) WITH ORDINALITY"
            for binary_output in (False, True):
                sample = ["x=a=b", "empty=", "flag", "=value", "x=two"]
                rows = compare(options_query, (sample,), binary_output=binary_output)
                assert rows == [
                    ("x", "a=b", 1), ("empty", "", 2), ("flag", None, 3), ("", "value", 4), ("x", "two", 5),
                ], rows
                for empty in (None, []):
                    rows = compare(options_query, (empty,), binary_output=binary_output)
                    assert rows == [], rows
            for db in (pg, ram):
                try:
                    db.execute(options_query, ([None],)).fetchall()
                except psycopg.Error as error:
                    assert error.sqlstate == "22004", (options_query, error.sqlstate, str(error))
                else:
                    raise AssertionError("pg_options_to_table accepted a NULL array element")

            words = ("_bare", "select", "between", "authorization", "value", "A\"B", "a.b", "café", "", None)
            quoting = "SELECT pg_catalog.quote_ident(%s::text),pg_catalog.quote_literal(%s::text)"
            for word in words:
                compare(quoting, (word, word))
            compare(quoting, ("a dollar $", "O'Brien\\a\nline"))
            compare("SELECT quote_literal(42),quote_literal(true)")
            compare("SELECT id," + format_options
                    + " FROM (VALUES (1,%s::text[]),(2,%s::text[]),(3,NULL::text[])) AS f(id,fdwoptions) ORDER BY id",
                    (["Mixed Case=don't", "_plain=a=b"], ["select=\\path", "empty="]))


def check_default_acl(binary, native):
    baseline = (
        "CASE WHEN defaclnamespace = 0 THEN acldefault(CASE WHEN defaclobjtype = 'S' "
        "THEN 's'::\"char\" ELSE defaclobjtype END, defaclrole) ELSE '{}' END"
    )
    stock_query = (
        "SELECT oid, tableoid, defaclrole, defaclnamespace, defaclobjtype, defaclacl, "
        + baseline + " AS acldefault FROM pg_default_acl"
    )
    fixtures = (
        "SELECT (" + baseline + ")::text FROM (VALUES "
        "(0::oid,'S'::\"char\",10::oid),(0::oid,'f'::\"char\",10::oid),"
        "(0::oid,'T'::\"char\",10::oid),(0::oid,'L'::\"char\",10::oid),"
        "(0::oid,'n'::\"char\",10::oid),(11::oid,'f'::\"char\",10::oid)) "
        "AS entries(defaclnamespace,defaclobjtype,defaclrole)"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for db in (pg, ram):
                cursor = db.execute(stock_query)
                assert [col.type_code for col in cursor.description] == [26, 26, 26, 26, 18, 1034, 1034]
                assert cursor.fetchall() == []
                assert db.execute("SELECT 'pg_catalog.pg_default_acl'::regclass::oid").fetchone() == (826,)
            expected = pg.execute(fixtures).fetchall()
            assert expected[-1] == ("{}",)
            assert ram.execute(fixtures).fetchall() == expected
            for kind in "cdfLlnpstFST":
                query = "SELECT acldefault(%s::\"char\",10::oid)::text"
                expected = pg.execute(query, (kind,)).fetchone()
                assert ram.execute(query, (kind,)).fetchone() == expected, (kind, expected)
            relation = "SELECT acldefault('r',10::oid)::text"
            native_bits = "arwdDxtm" if pg.info.server_version >= 170000 else "arwdDxt"
            assert pg.execute(relation).fetchone() == (f"{{ramsql={native_bits}/ramsql}}",)
            assert ram.execute(relation).fetchone() == ("{ramsql=arwdDxt/ramsql}",)
            strict = "SELECT acldefault(NULL,10::oid) IS NULL, acldefault('n',NULL) IS NULL"
            assert ram.execute(strict).fetchone() == pg.execute(strict).fetchone() == (True, True)


def check_conversion(binary, native):
    stock_projection = "SELECT tableoid,oid,conname,connamespace,conowner FROM pg_conversion"
    full_projection = (
        "SELECT tableoid,oid,conname,connamespace,conowner,conforencoding,contoencoding,conproc,condefault "
        "FROM pg_catalog.pg_conversion"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for db in (pg, ram):
                assert db.execute("SELECT 'pg_catalog.pg_conversion'::regclass::oid").fetchone() == (2607,)
            for query, types in (
                (stock_projection, [26, 26, 19, 26, 26]),
                (full_projection, [26, 26, 19, 26, 26, 23, 23, 24, 16]),
            ):
                ref = pg.execute(query + " WHERE conforencoding = 6 AND contoencoding = 6")
                actual = ram.execute(query)
                assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description] == types
                assert ref.fetchall() == actual.fetchall() == []

            query = "SELECT %s::text, octet_length(%s::text)"
            sample = "café Ελληνικά 日本語 😀"
            for binary_output in (False, True):
                expected = pg.execute(query, (sample, sample), binary=binary_output).fetchone()
                assert expected == (sample, len(sample.encode("utf-8")))
                assert ram.execute(query, (sample, sample), binary=binary_output).fetchone() == expected
            try:
                ram.execute("SET client_encoding TO 'LATIN1'")
            except psycopg.Error as error:
                assert error.sqlstate == "22023", (error.sqlstate, str(error))
            else:
                raise AssertionError("RamSQL accepted an unsupported client encoding")
            assert ram.execute("SHOW client_encoding").fetchone() == ("UTF8",)
            assert ram.execute("SHOW server_encoding").fetchone() == ("UTF8",)


def check_ranges(binary, native):
    projection = (
        "SELECT r.tableoid,r.rngtypid,r.rngsubtype,r.rngmultitypid,r.rngcollation,c.opcname,c.opcintype,"
        "r.rngcanonical,r.rngsubdiff::oid FROM pg_range r JOIN pg_opclass c ON c.oid=r.rngsubopc "
        "ORDER BY r.rngtypid"
    )
    canonical = (
        "SELECT p.oid,p.proname,p.pronamespace,p.prolang,p.provolatile,p.proisstrict,p.pronargs,p.prorettype,p.proargtypes::text "
        "FROM pg_proc p JOIN pg_range r ON r.rngcanonical::oid=p.oid ORDER BY p.oid"
    )
    cast_columns = "SELECT tableoid, oid, castsource, casttarget, castfunc, castcontext, castmethod FROM pg_cast c"
    cast_filter = " WHERE NOT EXISTS (SELECT 1 FROM pg_range r WHERE c.castsource=r.rngtypid AND c.casttarget=r.rngmultitypid)"
    probe = (
        "SELECT c.castsource,c.casttarget FROM (VALUES (3904::oid,4451::oid),(3904::oid,4532::oid)) "
        "AS c(castsource,casttarget)" + cast_filter
    )
    values = (
        "SELECT '(1,4]'::int4range,'(4,8]'::int8range,'(1.5,2.5]'::numrange,"
        "'(2026-01-01,2026-01-03]'::daterange,"
        "'[2026-01-01 12:00:00,2026-01-02 01:00:00)'::tsrange,"
        "'[2026-01-01 12:00:00+00,2026-01-02 01:00:00+00)'::tstzrange"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for db in (pg, ram):
                assert db.execute("SELECT 'pg_catalog.pg_range'::regclass::oid").fetchone() == (3541,)
            for binary_output in (False, True):
                ref = pg.execute(projection, binary=binary_output)
                actual = ram.execute(projection, binary=binary_output)
                assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description]
                expected = [(*row[:-1], 0) for row in ref.fetchall()]
                assert len(expected) == 6 and actual.fetchall() == expected
                assert ram.execute(values, binary=binary_output).fetchone() == pg.execute(values, binary=binary_output).fetchone()
            expected = pg.execute(canonical).fetchall()
            assert len(expected) == 3 and ram.execute(canonical).fetchall() == expected
            assert ram.execute(probe).fetchall() == pg.execute(probe).fetchall() == [(3904, 4532)]
            filtered = ram.execute(cast_columns + cast_filter + " ORDER BY 3,4").fetchall()
            assert filtered and filtered == ram.execute(cast_columns + " ORDER BY 3,4").fetchall()


def check_event_triggers(binary, native):
    tags = "array_to_string(array(select quote_literal(x) from unnest(evttags) as t(x)), ', ')"
    stock_query = (
        "SELECT e.tableoid,e.oid,evtname,evtenabled,evtevent,evtowner," + tags
        + " as evttags,e.evtfoid::regproc as evtfname FROM pg_event_trigger e ORDER BY e.oid"
    )
    catalog = "SELECT oid,evtname,evtevent,evtowner,evtfoid,evtenabled,evttags FROM pg_catalog.pg_event_trigger"
    correlated = (
        "SELECT id," + tags + " FROM (VALUES (1,%s::text[]),(2,%s::text[]),(3,NULL::text[]),"
        "(4,ARRAY[]::text[])) AS e(id,evttags) ORDER BY id"
    )
    array_rows = "SELECT * FROM pg_catalog.unnest(%s::int[]) WITH ORDINALITY AS u(value,position)"
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for db in (pg, ram):
                assert db.execute("SELECT 'pg_catalog.pg_event_trigger'::regclass::oid").fetchone() == (3466,)
                for query, types in (
                    (stock_query, [26, 26, 19, 18, 19, 26, 25, 24]),
                    (catalog, [26, 19, 19, 26, 26, 18, 1009]),
                ):
                    cursor = db.execute(query)
                    assert [col.type_code for col in cursor.description] == types
                    assert cursor.fetchall() == []
            for binary_output in (False, True):
                def compare(query, params):
                    ref = pg.execute(query, params, binary=binary_output)
                    actual = ram.execute(query, params, binary=binary_output)
                    assert [col.type_code for col in ref.description] == [col.type_code for col in actual.description]
                    expected, observed = ref.fetchall(), actual.fetchall()
                    assert observed == expected, (query, expected, observed)
                    return observed

                assert compare(correlated, (["ALTER TABLE", "DROP TABLE"], ["can't", None])) == [
                    (1, "'ALTER TABLE', 'DROP TABLE'"), (2, "'can''t'"), (3, ""), (4, ""),
                ]
                assert compare(array_rows, ("[0:1][4:5]={{1,NULL},{3,4}}",)) == [
                    (1, 1), (None, 2), (3, 3), (4, 4),
                ]
                for empty in (None, "{}"):
                    assert compare(array_rows, (empty,)) == []
                assert compare("SELECT * FROM unnest(%s::text[]) WITH ORDINALITY", (["x", "x", None],)) == [
                    ("x", 1), ("x", 2), (None, 3),
                ]


def check_column_metadata(binary, native, version):
    notnull = "a.attnotnull,"
    pg18_notnull = (
        "CASE WHEN a.attnotnull THEN '' ELSE NULL END AS notnull_name,"
        "NULL AS notnull_comment,NULL AS notnull_invalidoid,false AS notnull_noinherit,"
        "CASE WHEN a.attislocal THEN true WHEN a.attnotnull AND NOT a.attislocal THEN true "
        "ELSE false END AS notnull_islocal,"
    )
    if version == "18":
        notnull = ""
    query = (
        "SELECT a.attrelid,a.attnum,a.attname,a.attstattarget,a.attstorage,t.typstorage,"
        + notnull
        + "a.atthasdef,a.attisdropped,a.attlen,a.attalign,a.attislocal,"
        "pg_catalog.format_type(t.oid,a.atttypmod) AS atttypname,"
        # Stock pg_dump removes the backend-owned attribute version from the
        # options it archives. Keep comparing all portable option values.
        "array_to_string(ARRAY(SELECT o FROM unnest(a.attoptions) o "
        "WHERE substr(o,1,10)<>'pg_attver='),', ') AS attoptions,"
        "CASE WHEN a.attcollation<>t.typcollation THEN a.attcollation ELSE 0 END AS attcollation,"
        "pg_catalog.array_to_string(ARRAY(SELECT pg_catalog.quote_ident(option_name) || ' ' || "
        "pg_catalog.quote_literal(option_value) FROM pg_catalog.pg_options_to_table(attfdwoptions) "
        "ORDER BY option_name),E',\\n    ') AS attfdwoptions,"
        + (pg18_notnull if version == "18" else "")
        + "a.attcompression,a.attidentity,CASE WHEN a.atthasmissing AND NOT a.attisdropped "
        "THEN a.attmissingval ELSE NULL END AS attmissingval,a.attgenerated,a.attacl "
        "FROM unnest('{%d}'::pg_catalog.oid[]) AS src(tbloid) "
        "JOIN pg_catalog.pg_attribute a ON src.tbloid=a.attrelid "
        "LEFT JOIN pg_catalog.pg_type t ON a.atttypid=t.oid "
        "WHERE a.attnum>0::pg_catalog.int2 ORDER BY a.attrelid,a.attnum"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            reports = []
            for db in (pg, ram):
                db.execute("CREATE TABLE attr_parent (id integer NOT NULL, amount numeric(10,2))")
                db.execute("CREATE TABLE attr_child (label varchar(12) DEFAULT 'x', tags text[]) INHERITS (attr_parent)")
                oid = db.execute("SELECT 'attr_child'::regclass::oid").fetchone()[0]
                cursor = db.execute(query % oid)
                codes = [col.type_code for col in cursor.description]
                assert codes[-3] == 2277 and codes[-1] == 1034, codes
                rows = [(row[1], row[2], -1 if row[3] is None else row[3], *row[4:]) for row in cursor.fetchall()]
                assert len(rows) == 4, rows
                assert [row[3:5] for row in rows] == [("p", "p"), ("m", "m"), ("x", "x"), ("x", "x")], rows
                reports.append((codes, rows))
            assert reports[0] == reports[1], reports


def check_column_defaults(binary, native):
    query = (
        "SELECT a.tableoid,a.oid,adrelid,adnum,pg_catalog.pg_get_expr(adbin,adrelid) AS adsrc "
        "FROM unnest('{%d}'::pg_catalog.oid[]) AS src(tbloid) "
        "JOIN pg_catalog.pg_attrdef a ON src.tbloid=a.adrelid ORDER BY a.adrelid,a.adnum"
    )
    dependencies = (
        "SELECT a.adnum,d.refobjsubid,d.deptype FROM pg_attrdef a JOIN pg_depend d "
        "ON d.classid=a.tableoid AND d.objid=a.oid WHERE a.adrelid='dump_defaults'::regclass "
        "ORDER BY a.adnum,d.refobjsubid"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            reports = []
            for db in (pg, ram):
                db.execute("CREATE TABLE dump_defaults (id integer DEFAULT 7, label text DEFAULT 'ready', "
                           "next_id integer GENERATED ALWAYS AS (id + 1) STORED)")
                oid = db.execute("SELECT 'dump_defaults'::regclass::oid").fetchone()[0]
                cursor = db.execute(query % oid)
                assert [col.type_code for col in cursor.description] == [26, 26, 26, 21, 25]
                rows = cursor.fetchall()
                assert len(rows) == 3 and len({row[1] for row in rows}) == 3, rows
                assert all(row[0] == 2604 and row[1] > 0 and row[2] == oid for row in rows), rows
                assert db.execute(dependencies).fetchall() == [(1, 1, "a"), (2, 2, "a"), (3, 1, "n"), (3, 3, "i")]
                reports.append([(row[3], row[4]) for row in rows])
                db.execute("ALTER TABLE dump_defaults RENAME COLUMN next_id TO computed_id")
                assert db.execute(query % oid).fetchall() == rows
                db.execute("INSERT INTO dump_defaults DEFAULT VALUES")
                assert db.execute("SELECT * FROM dump_defaults").fetchone() == (7, "ready", 8)
            assert reports[0] == reports[1], reports


def check_constraints_and_indexes(binary, native):
    checks = (
        "SELECT c.tableoid,c.oid,conrelid,conname,pg_catalog.pg_get_constraintdef(c.oid) AS consrc,"
        "conislocal,convalidated FROM unnest('{%d}'::pg_catalog.oid[]) AS src(tbloid) "
        "JOIN pg_catalog.pg_constraint c ON src.tbloid=c.conrelid WHERE contype='c' ORDER BY c.conrelid,c.conname"
    )
    indexes = (
        "SELECT c.relname,i.indkey::text,i.indisvalid,i.indisready,i.indnkeyatts,i.indnatts,"
        "i.indnullsnotdistinct,i.indexprs IS NULL FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid "
        "WHERE i.indrelid='dump_idx'::regclass ORDER BY i.indisprimary DESC"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            reports = []
            for db in (pg, ram):
                db.execute("CREATE TABLE dump_parent (id int, CONSTRAINT dump_parent_check CHECK (id > 0))")
                db.execute("CREATE TABLE dump_child (label text) INHERITS (dump_parent)")
                db.execute("CREATE TABLE dump_idx (id int PRIMARY KEY, lookup int, detail text)")
                db.execute("CREATE UNIQUE INDEX dump_idx_cover ON dump_idx (lookup) INCLUDE (detail) NULLS NOT DISTINCT")
                oid = db.execute("SELECT 'dump_child'::regclass::oid").fetchone()[0]
                rows = db.execute(checks % oid).fetchall()
                assert len(rows) == 1 and rows[0][0] == 2606 and rows[0][2] == oid, rows
                assert rows[0][3] == "dump_parent_check" and rows[0][5:] == (False, True), rows
                assert rows[0][4].startswith("CHECK"), rows
                reports.append(db.execute(indexes).fetchall())
            assert reports[0] == reports[1] == [
                ("dump_idx_pkey", "1", True, True, 1, 1, False, True),
                ("dump_idx_cover", "2 3", True, True, 1, 2, True, True),
            ], reports


def check_partitions(binary, native):
    enum_hash = (
        "SELECT partrelid FROM pg_partitioned_table WHERE "
        "(SELECT c.oid FROM pg_opclass c JOIN pg_am a ON c.opcmethod=a.oid "
        "WHERE opcname='enum_ops' AND opcnamespace='pg_catalog'::regnamespace AND amname='hash')=ANY(partclass)"
    )
    ddl = (
        "CREATE TABLE dump_parts (id int, bucket text) PARTITION BY LIST (bucket)",
        "CREATE TABLE dump_parts_other PARTITION OF dump_parts DEFAULT",
        "CREATE TABLE dump_ranges (id int) PARTITION BY RANGE (id)",
        "CREATE TABLE dump_range_low PARTITION OF dump_ranges FOR VALUES FROM (0) TO (10)",
    )
    fields = (
        "SELECT p.tableoid,t.relname,p.partstrat,p.partnatts, COALESCE(d.relname::text,''),"
        "p.partattrs::smallint[],p.partclass::oid[],p.partcollation::oid[],p.partexprs IS NULL "
        "FROM pg_partitioned_table p JOIN pg_class t ON t.oid=p.partrelid "
        "LEFT JOIN pg_class d ON d.oid=p.partdefid ORDER BY t.relname"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for db in (pg, ram):
                for statement in ddl:
                    db.execute(statement)
                assert db.execute(enum_hash).fetchall() == []
            expected = pg.execute(fields).fetchall()
            assert len(expected) == 2 and ram.execute(fields).fetchall() == expected, expected
            for db in (pg, ram):
                db.execute("DROP TABLE dump_parts_other")
                assert db.execute("SELECT partdefid FROM pg_partitioned_table WHERE partrelid='dump_parts'::regclass").fetchone() == (0,)


def check_subscriptions(binary, native):
    database_oid = "SELECT oid FROM pg_database WHERE datname = current_database()"
    count = f"SELECT count(*) FROM pg_subscription WHERE subdbid = ({database_oid})"
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            for db in (pg, ram):
                rows = db.execute(database_oid).fetchall()
                assert len(rows) == 1 and rows[0][0] > 0, rows
                assert db.execute(count).fetchone() == (0,)
                assert db.execute("SELECT 'pg_catalog.pg_subscription'::regclass::oid").fetchone() == (6100,)


def check_archive_metadata(binary, native):
    strategy = (
        "SELECT amopstrategy,amopopr FROM pg_amop "
        "WHERE amopfamily=1976 AND amoplefttype=23 AND amoprighttype=23 ORDER BY amopstrategy"
    )
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**native) as pg, psycopg.connect(**candidate) as ram:
            expected = pg.execute(strategy).fetchall()
            assert len(expected) == 5 and ram.execute(strategy).fetchall() == expected, expected
            for db in (pg, ram):
                assert db.execute("SELECT pg_encoding_to_char(6),pg_encoding_to_char(-1)").fetchone() == ("UTF8", "")
                assert db.execute("SELECT label,provider FROM pg_seclabels").fetchall() == []
                assert db.execute("SELECT unnest(setconfig) FROM pg_db_role_setting").fetchall() == []
                assert db.execute(
                    "SELECT v.id, pg_catalog.unnest(v.a) AS item FROM "
                    "(VALUES (1,ARRAY[3,NULL,2]),(2,ARRAY[]::integer[]),(3,NULL::integer[]),(4,ARRAY[7])) v(id,a) "
                    "WHERE v.id < 4 ORDER BY item NULLS LAST"
                ).fetchall() == [(1, 2), (1, 3), (1, None)]
                db.execute("SET search_path TO public,pg_catalog")
                assert db.execute("SELECT current_schemas(false)").fetchone() == (["public", "pg_catalog"],)
                db.execute("SELECT set_config('search_path','',false)")
                assert db.execute("SELECT current_schemas(false),current_schemas(true)").fetchone() == ([], ["pg_catalog"])
            assert ram.execute(
                "SELECT tableoid,datname,pg_encoding_to_char(encoding),datcollate,datctype,datlocprovider,dattablespace "
                "FROM pg_database WHERE datname=current_database()"
            ).fetchone() == (1262, candidate["dbname"], "UTF8", "C", "C", "c", 1663)


def check_scoped_identities(binary, tools, native):
    setup = (
        "CREATE SCHEMA early_orders",
        "CREATE SCHEMA later_orders",
        "CREATE TABLE early_orders.orders (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, note text)",
        "CREATE TABLE later_orders.orders (id int GENERATED BY DEFAULT AS IDENTITY (START 20) PRIMARY KEY, note text)",
    )
    qualified = ("early_orders.orders", "later_orders.orders")
    sequence_names = tuple(f"{table}_id_seq" for table in qualified)

    def sequence_metadata(db):
        rows = tuple(db.execute("SELECT pg_get_serial_sequence(%s, 'id')", (name,)).fetchone()[0] for name in qualified)
        assert rows == sequence_names, rows
        oids = tuple(db.execute("SELECT %s::regclass::oid", (name,)).fetchone()[0] for name in sequence_names)
        assert oids[0] != oids[1], oids
        return oids

    wal = tools.root / "scoped-identities.wal"
    storage = ("--workers", "1")
    with DurableServer(binary, wal, create=True, storage_options=storage) as server:
        with psycopg.connect(**native) as pg, psycopg.connect(**ramsql_settings(server)) as ram:
            for db in (pg, ram):
                for statement in setup:
                    db.execute(statement)
                for sequence, initial in zip(sequence_names, (1, 20), strict=True):
                    assert db.execute(f"SELECT last_value,is_called FROM {sequence}").fetchone() == (initial, False)
                for table, expected in zip(qualified, (1, 20), strict=True):
                    assert db.execute(f"INSERT INTO {table}(note) VALUES ('first') RETURNING id").fetchone() == (expected,)
                for sequence, current in zip(sequence_names, (1, 20), strict=True):
                    assert db.execute(f"SELECT last_value,is_called FROM {sequence}").fetchone() == (current, True)
                sequence_metadata(db)
            committed_oids = sequence_metadata(ram)

    with DurableServer(binary, wal, storage_options=storage) as reopened:
        with psycopg.connect(**ramsql_settings(reopened)) as ram:
            assert sequence_metadata(ram) == committed_oids
            for table, previous in zip(qualified, (1, 20), strict=True):
                assert ram.execute(f"SELECT id FROM {table}").fetchone() == (previous,)
                assert ram.execute(f"INSERT INTO {table}(note) VALUES ('after reopen') RETURNING id").fetchone()[0] > previous
            ram.execute("DROP SCHEMA early_orders CASCADE")
            assert ram.execute("SELECT to_regclass('early_orders.orders_id_seq') IS NULL").fetchone() == (True,)
            assert ram.execute("SELECT 'later_orders.orders_id_seq'::regclass::oid").fetchone() == (committed_oids[1],)


def export_identity_archive(binary, tools, native):
    archive = "identity-outbound.dump"
    with running_ramsql(binary, extra_arguments=("--workers", "1")) as candidate:
        with psycopg.connect(**candidate) as ram:
            ram.execute("CREATE SCHEMA migration")
            ram.execute("CREATE TABLE migration.items (id int GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label text)")
            assert ram.execute("INSERT INTO migration.items(label) VALUES ('first') RETURNING id").fetchone() == (1,)
        tools.run("identity-outbound-dump", "pg_dump", candidate, "--no-owner", "--no-acl",
                  "--format=custom", f"--file={archive}")
    tools.run("identity-outbound-restore", "pg_restore", native, "--single-transaction",
              "--exit-on-error", "--no-owner", "--no-acl", archive)
    with psycopg.connect(**native) as pg:
        assert pg.execute("SELECT id,label FROM migration.items").fetchall() == [(1, "first")]
        assert pg.execute("INSERT INTO migration.items(label) VALUES ('next') RETURNING id").fetchone() == (2,)


def import_identity_archive(binary, tools, native):
    label = "identity-inbound"
    archive = f"{label}.dump"
    wal = tools.root / f"{label}.wal"
    storage = ("--workers", "1")
    with psycopg.connect(**native) as pg:
        pg.execute("CREATE SCHEMA migration")
        pg.execute("CREATE TABLE migration.items (id int PRIMARY KEY, label text)")
        pg.execute("ALTER TABLE migration.items ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY "
                   "(SEQUENCE NAME migration.item_ids START WITH 20 INCREMENT BY 2)")
        assert pg.execute("INSERT INTO migration.items(label) VALUES ('first') RETURNING id").fetchone() == (20,)
    tools.run(f"{label}-dump", "pg_dump", native, "--no-owner", "--no-acl",
              "--format=custom", f"--file={archive}")
    with DurableServer(binary, wal, create=True, storage_options=storage) as server:
        candidate = ramsql_settings(server)
        tools.run(f"{label}-restore", "pg_restore", candidate, "--single-transaction",
                  "--exit-on-error", "--no-owner", "--no-acl", archive)
        with psycopg.connect(**candidate) as ram:
            assert ram.execute("SELECT id,label FROM migration.items").fetchall() == [(20, "first")]
            assert ram.execute("SELECT last_value,is_called FROM migration.item_ids").fetchone() == (20, True)
    with DurableServer(binary, wal, storage_options=storage) as reopened:
        with psycopg.connect(**ramsql_settings(reopened)) as ram:
            assert ram.execute("SELECT id,label FROM migration.items").fetchall() == [(20, "first")]
            assert ram.execute("INSERT INTO migration.items(label) VALUES ('next') RETURNING id").fetchone() == (22,)
            try:
                with ram.transaction():
                    ram.execute("INSERT INTO migration.items OVERRIDING SYSTEM VALUE VALUES (20, 'duplicate')")
            except psycopg.errors.UniqueViolation:
                pass
            else:
                raise AssertionError("restored primary key accepted a duplicate")


def check_multiple_identities(binary, tools, native):
    def populate(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(
                f"CREATE TABLE {schema}.items "
                "(id int GENERATED ALWAYS AS IDENTITY (START 13) PRIMARY KEY, "
                "ordinal int GENERATED BY DEFAULT AS IDENTITY (START 40 INCREMENT 3), label text)"
            )
            assert db.execute(f"INSERT INTO {schema}.items(label) VALUES ('default') RETURNING id,ordinal").fetchone() == (13, 40)
            db.execute(f"INSERT INTO {schema}.items(ordinal,label) VALUES (99,'explicit')")

    def verify(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            assert db.execute(f"SELECT * FROM {schema}.items ORDER BY id").fetchall() == [
                (13, 40, "default"), (14, 99, "explicit"),
            ]
            for column, current in (("id", 14), ("ordinal", 40)):
                assert db.execute(f"SELECT last_value,is_called FROM {schema}.items_{column}_seq").fetchone() == (current, True)

    check_schema_archives(binary, tools, native, "identities", populate, verify)


def check_serial_archives(binary, tools, native, *, scoped=True):
    def table_for(schema):
        return f"{schema}.items" if scoped else f"public.{schema}"

    def populate(endpoint, schema):
        table = table_for(schema)
        with psycopg.connect(**endpoint) as db:
            if scoped:
                db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f"CREATE TABLE {table} (id serial PRIMARY KEY, ordinal bigserial, label text)")
            assert db.execute(
                f"INSERT INTO {table}(label) VALUES ('initial') RETURNING id,ordinal"
            ).fetchone() == (1, 1)
            db.execute(f"SELECT setval('{table}_ordinal_seq',20,true)")

    def verify(endpoint, schema):
        table = table_for(schema)
        with psycopg.connect(**endpoint) as db:
            assert db.execute(f"SELECT * FROM {table} ORDER BY id").fetchall() == [(1, 1, "initial")]
            for column, value in (("id", 1), ("ordinal", 20)):
                sequence = f"{table}_{column}_seq"
                assert db.execute(
                    "SELECT pg_get_serial_sequence(%s,%s)", (table, column)
                ).fetchone() == (sequence,)
                assert db.execute(
                    f"SELECT last_value,is_called FROM {sequence}"
                ).fetchone() == (value, True)
            db.execute("SET search_path TO ''")
            assert db.execute(
                "SELECT pg_get_expr(adbin,adrelid) FROM pg_attrdef "
                "WHERE adrelid = to_regclass(%s) ORDER BY adnum", (table,)
            ).fetchall() == [
                (f"nextval('{table}_id_seq'::regclass)",),
                (f"nextval('{table}_ordinal_seq'::regclass)",),
            ]

    fixture = "serials" if scoped else "public_serials"
    check_schema_archives(binary, tools, native, fixture, populate, verify)


def check_sequence_archives(binary, tools, native):
    def populate(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f"CREATE SEQUENCE {schema}.ticket_ids AS integer INCREMENT BY 3 START WITH 17")
            db.execute(f"SELECT setval('{schema}.ticket_ids',20,true)")

    def verify(endpoint, schema, *, increment=False):
        with psycopg.connect(**endpoint) as db:
            assert db.execute(f"SELECT last_value,is_called FROM {schema}.ticket_ids").fetchone() == (20, True)
            if increment:
                assert db.execute(f"SELECT nextval('{schema}.ticket_ids')").fetchone() == (23,)

    inbound = "standalone-sequence-inbound.dump"
    outbound = "standalone-sequence-outbound.dump"
    wal = tools.root / "standalone-sequence.wal"
    storage = ("--workers", "1")
    populate(native, "native_side")
    tools.run("sequence-inbound-dump", "pg_dump", native, "--no-owner", "--no-acl",
              "--format=custom", f"--file={inbound}")
    with DurableServer(binary, wal, create=True, storage_options=storage) as server:
        candidate = ramsql_settings(server)
        tools.run("sequence-inbound-restore", "pg_restore", candidate, "--single-transaction",
                  "--exit-on-error", "--no-owner", "--no-acl", inbound)
        verify(candidate, "native_side")
    with DurableServer(binary, wal, storage_options=storage) as reopened:
        verify(ramsql_settings(reopened), "native_side", increment=True)

    with running_ramsql(binary, extra_arguments=storage) as candidate:
        populate(candidate, "ramsql_side")
        tools.run("sequence-outbound-dump", "pg_dump", candidate, "--no-owner", "--no-acl",
                  "--format=custom", f"--file={outbound}")
    tools.run("sequence-outbound-restore", "pg_restore", native, "--single-transaction",
              "--exit-on-error", "--no-owner", "--no-acl", outbound)
    verify(native, "ramsql_side", increment=True)


def check_generated_archives(binary, tools, native):
    def populate(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f"CREATE TABLE {schema}.invoice (net int NOT NULL, tax int DEFAULT 2, "
                       "total int GENERATED ALWAYS AS (net + tax) STORED)")
            db.execute(f"INSERT INTO {schema}.invoice(net) VALUES (17)")
            db.execute(f"CREATE TABLE {schema}.periods (id int PRIMARY KEY, starts time, ends time, "
                       "covered boolean GENERATED ALWAYS AS "
                       "((starts, ends) OVERLAPS (TIME '10:30', TIME '11:30')) STORED)")
            db.execute(f"INSERT INTO {schema}.periods(id,starts,ends) "
                       "VALUES (1,'09:00','11:00'),(2,'11:30','12:00'),(3,NULL,'09:30')")
            db.execute(f"CREATE TABLE {schema}.events (happened timestamp, "
                       "bucket timestamp GENERATED ALWAYS AS "
                       "(date_bin('15 minutes', happened, TIMESTAMP '2000-01-01')) STORED)")
            db.execute(f"INSERT INTO {schema}.events(happened) VALUES ('1999-12-31 23:44:59.999999'),(NULL)")

    def verify(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            assert db.execute(f"SELECT net,tax,total FROM {schema}.invoice").fetchall() == [(17, 2, 19)]
            assert db.execute(f"SELECT id,covered FROM {schema}.periods ORDER BY id").fetchall() == [
                (1, True), (2, False), (3, None),
            ]
            assert db.execute(f"SELECT bucket::text FROM {schema}.events ORDER BY happened").fetchall() == [
                ("1999-12-31 23:30:00",), (None,),
            ]
            with db.transaction(force_rollback=True):
                assert db.execute(f"INSERT INTO {schema}.invoice(net,tax) VALUES (6,3) RETURNING total").fetchone() == (9,)
                assert db.execute(f"UPDATE {schema}.invoice SET tax=4 WHERE net=17 RETURNING total").fetchone() == (21,)
                assert db.execute(f"UPDATE {schema}.periods SET ends='10:30' WHERE id=1 RETURNING covered").fetchone() == (False,)
                assert db.execute(
                    f"UPDATE {schema}.events SET happened='1999-12-31 23:45' "
                    "WHERE happened IS NOT NULL RETURNING bucket::text"
                ).fetchone() == ("1999-12-31 23:45:00",)

    check_schema_archives(binary, tools, native, "generated", populate, verify)


def verify_view_return_rules(db, schema, names, *, unpopulated=()):
    """Execute real deparsed _RETURN actions; whitespace is not an SQL oracle."""
    with db.transaction(force_rollback=True):
        db.execute("SET LOCAL search_path=''")
        for name in names:
            target = psycopg.sql.SQL("{}.{}").format(
                psycopg.sql.Identifier(schema), psycopg.sql.Identifier(name))
            for pretty in (False, True):
                result = db.execute(
                    "SELECT pg_catalog.pg_get_ruledef(r.oid,%s) "
                    "FROM pg_catalog.pg_rewrite r JOIN pg_catalog.pg_class c ON c.oid=r.ev_class "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=%s AND c.relname=%s AND r.rulename='_RETURN'",
                    (pretty, schema, name),
                )
                assert result.description[0].type_code == 25
                rows = result.fetchall()
                assert len(rows) == 1 and rows[0][0] is not None, (schema, name, rows)
                definition = rows[0][0]
                action = re.fullmatch(
                    r'CREATE RULE "_RETURN" AS\s+ON SELECT TO .+? DO INSTEAD\s+(.+)',
                    definition, flags=re.DOTALL,
                )
                assert action is not None, definition
                select = db.execute(action.group(1))
                actual = select.fetchall()
                columns = [(column.name, column.type_code) for column in select.description]
                if name not in unpopulated:
                    view = db.execute(psycopg.sql.SQL("SELECT * FROM {}").format(target))
                    assert sorted(actual, key=repr) == sorted(view.fetchall(), key=repr), definition
                    assert columns == [(column.name, column.type_code) for column in view.description]


def check_view_schema_privileges(binary, tools, native):
    def populate(endpoint):
        with psycopg.connect(**endpoint) as db:
            db.execute("CREATE ROLE view_schema_owner")
            db.execute("CREATE TABLE view_schema_source(id int)")
            db.execute("INSERT INTO view_schema_source VALUES (7)")
            db.execute("GRANT SELECT ON view_schema_source TO view_schema_owner")
            db.execute("CREATE VIEW view_public_owned AS SELECT id FROM public.view_schema_source")
            db.execute("ALTER TABLE view_public_owned OWNER TO view_schema_owner")
            with db.transaction():
                db.execute("SET LOCAL ROLE view_schema_owner")
                assert db.execute("SELECT current_user").fetchone() == ("view_schema_owner",)
                try:
                    with db.transaction():
                        db.execute("CREATE OR REPLACE VIEW public.view_public_owned AS "
                                   "SELECT id+1 AS id FROM public.view_schema_source")
                except psycopg.Error as error:
                    assert error.sqlstate == "42501", error
                else:
                    raise AssertionError("view ownership bypassed schema CREATE authority")
                assert db.execute("SELECT id FROM public.view_public_owned").fetchall() == [(7,)]
            db.execute("CREATE SCHEMA view_owner_ns AUTHORIZATION view_schema_owner")
            db.execute("CREATE VIEW view_owner_ns.owned AS SELECT id FROM public.view_schema_source")
            db.execute("ALTER TABLE view_owner_ns.owned OWNER TO view_schema_owner")
            with db.transaction():
                db.execute("SET LOCAL ROLE view_schema_owner")
                assert db.execute("SELECT current_user").fetchone() == ("view_schema_owner",)
                db.execute("CREATE OR REPLACE VIEW view_owner_ns.owned AS "
                           "SELECT id+10 AS id FROM public.view_schema_source")
                assert db.execute("SELECT id FROM view_owner_ns.owned").fetchall() == [(17,)]

    def verify(endpoint):
        with psycopg.connect(**endpoint) as db:
            assert db.execute("SELECT id FROM public.view_public_owned").fetchall() == [(7,)]
            assert db.execute("SELECT id FROM view_owner_ns.owned").fetchall() == [(17,)]
            assert db.execute("SELECT pg_get_userbyid(relowner) FROM pg_class "
                              "WHERE oid='view_owner_ns.owned'::regclass").fetchone() == ("view_schema_owner",)

    populate(native)
    verify(native)
    wal = tools.root / "view-schema-privileges.wal"
    storage = ("--workers", "1")
    with DurableServer(binary, wal, create=True, storage_options=storage) as server:
        endpoint = ramsql_settings(server)
        populate(endpoint)
        verify(endpoint)
    with DurableServer(binary, wal, storage_options=storage) as reopened:
        verify(ramsql_settings(reopened))


def check_view_archives(binary, tools, native):
    def verify_hooks(db, schema):
        db.execute(f"UPDATE {schema}.a_positive SET amount=13 WHERE id=1")
        assert db.execute(f"SELECT amount FROM {schema}.z_source WHERE id=1").fetchall() == [(13,)]
        assert db.execute(f"SELECT amount FROM {schema}.b_amounts").fetchall() == [(26,)]
        db.execute(f"DELETE FROM {schema}.a_positive WHERE id=1")
        assert db.execute(f"SELECT amount FROM {schema}.z_source WHERE id=1").fetchall() == [(13,)]
        db.execute(f"UPDATE {schema}.z_source SET amount=20 WHERE id=1")

    def populate(endpoint, schema):
        public_table = f"{schema}_public"
        with psycopg.connect(**endpoint) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f"CREATE TABLE {schema}.z_source (id int NOT NULL, amount int)")
            db.execute(f"CREATE VIEW {schema}.a_positive AS SELECT id,amount FROM {schema}.z_source WHERE amount > 0")
            db.execute(f"CREATE VIEW {schema}.b_amounts AS SELECT amount FROM {schema}.a_positive")
            db.execute(f"INSERT INTO {schema}.z_source VALUES (1,20),(2,-1)")
            oid = db.execute("SELECT %s::regclass::oid", (f"{schema}.a_positive",)).fetchone()[0]
            db.execute(f"CREATE OR REPLACE VIEW {schema}.a_positive AS SELECT id,amount*2 AS amount FROM {schema}.z_source WHERE amount > 0")
            assert db.execute("SELECT %s::regclass::oid", (f"{schema}.a_positive",)).fetchone()[0] == oid
            assert db.execute(f"SELECT amount FROM {schema}.b_amounts").fetchall() == [(40,)]
            db.execute(f"CREATE FUNCTION public.{schema}_view_update() RETURNS trigger LANGUAGE plpgsql "
                       f"AS $$BEGIN UPDATE {schema}.z_source SET amount=NEW.amount WHERE id=OLD.id; RETURN NEW; END$$")
            db.execute(f"CREATE TRIGGER keep_view_hook INSTEAD OF UPDATE ON {schema}.a_positive "
                       f"FOR EACH ROW EXECUTE FUNCTION public.{schema}_view_update()")
            db.execute(f"CREATE RULE keep_view_rows AS ON DELETE TO {schema}.a_positive DO INSTEAD NOTHING")
            # Replacing the SELECT must retain the hook/rule OID links too.
            db.execute(f"CREATE OR REPLACE VIEW {schema}.a_positive AS SELECT id,amount*2 AS amount "
                       f"FROM {schema}.z_source WHERE amount > 0")
            verify_hooks(db, schema)
            for select in ("id", "id,amount AS renamed", "id,amount::bigint AS amount"):
                try:
                    with db.transaction():
                        db.execute(f"CREATE OR REPLACE VIEW {schema}.a_positive AS SELECT {select} FROM {schema}.z_source")
                except psycopg.Error as error:
                    assert error.sqlstate == "42P16", (select, error.sqlstate)
                else:
                    raise AssertionError(f"incompatible replacement accepted: {select}")
            with db.transaction():
                db.execute("SAVEPOINT replacement_rollback")
                db.execute(f"CREATE OR REPLACE VIEW {schema}.a_positive AS SELECT id,amount*3 AS amount FROM {schema}.z_source WHERE amount > 0")
                assert db.execute(f"SELECT amount FROM {schema}.b_amounts").fetchall() == [(60,)]
                db.execute("ROLLBACK TO replacement_rollback")
                db.execute("RELEASE replacement_rollback")
            assert db.execute(f"SELECT amount FROM {schema}.b_amounts").fetchall() == [(40,)]
            db.execute(f'CREATE TABLE public."{public_table}" (id int)')
            db.execute(f'INSERT INTO public."{public_table}" VALUES (8)')
            db.execute(f'CREATE VIEW {schema}.c_public AS SELECT id FROM "{public_table}"')
            db.execute(
                f"CREATE VIEW {schema}.d_cte AS WITH {public_table} AS (SELECT 6 AS id) "
                f"SELECT c.id FROM {public_table} c CROSS JOIN public.{public_table} p"
            )
            verify_view_return_rules(db, schema, ("a_positive", "b_amounts", "c_public", "d_cte"))

    def verify(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            assert db.execute(f"SELECT id,amount FROM {schema}.a_positive").fetchall() == [(1, 40)]
            assert db.execute(f"SELECT amount FROM {schema}.b_amounts").fetchall() == [(40,)]
            assert db.execute(f"SELECT id FROM {schema}.c_public").fetchall() == [(8,)]
            assert db.execute(f"SELECT id FROM {schema}.d_cte").fetchall() == [(6,)]
            verify_hooks(db, schema)
            verify_view_return_rules(db, schema, ("a_positive", "b_amounts", "c_public", "d_cte"))

    check_schema_archives(binary, tools, native, "view", populate, verify, formats=("plain", "custom"))


def check_view_column_name_archives(binary, tools, native):
    def populate(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f"CREATE TABLE {schema}.t (id int, n int)")
            db.execute(f"INSERT INTO {schema}.t VALUES (1,20)")
            db.execute(
                f'CREATE VIEW {schema}.v ("Item", "a""b", total) AS '
                f'SELECT id, n AS "old""name", n+1 FROM {schema}.t'
            )
            db.execute(f'CREATE VIEW {schema}.w AS SELECT "Item", "a""b", total FROM {schema}.v')
            verify_view_return_rules(db, schema, ("v", "w"))

    def verify(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            for view in ("v", "w"):
                result = db.execute(f'SELECT "Item", "a""b", total FROM {schema}.{view}')
                assert result.fetchall() == [(1,20,21)]
                assert [column.name for column in result.description] == ["Item", 'a"b', "total"]
            verify_view_return_rules(db, schema, ("v", "w"))

    check_schema_archives(binary, tools, native, "viewnames", populate, verify, formats=("plain", "custom"))


def check_materialized_view_archives(binary, tools, native):
    def populate(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f"CREATE TABLE {schema}.t (region int, amount int)")
            db.execute(f"INSERT INTO {schema}.t VALUES (1,2),(1,3),(2,4)")
            query = f"SELECT region,sum(amount) AS total FROM {schema}.t GROUP BY region"
            db.execute(f"CREATE MATERIALIZED VIEW {schema}.totals AS {query}")
            db.execute(f"CREATE MATERIALIZED VIEW {schema}.pending AS {query} WITH NO DATA")
            db.execute(f"CREATE VIEW {schema}.report AS SELECT region,total FROM {schema}.totals")
            verify_view_return_rules(db, schema, ("totals", "pending", "report"), unpopulated=("pending",))

    def verify(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            assert db.execute(f"SELECT * FROM {schema}.report ORDER BY region").fetchall() == [(1, 5), (2, 4)]
            assert db.execute(
                "SELECT matviewname,ispopulated FROM pg_matviews WHERE schemaname=%s ORDER BY matviewname",
                (schema,),
            ).fetchall() == [("pending", False), ("totals", True)]
            try:
                with db.transaction():
                    db.execute(f"SELECT * FROM {schema}.pending")
            except psycopg.errors.ObjectNotInPrerequisiteState:
                pass
            else:
                raise AssertionError("unpopulated materialized view was readable")
            verify_view_return_rules(db, schema, ("totals", "pending", "report"), unpopulated=("pending",))

    check_schema_archives(binary, tools, native, "matviews", populate, verify, formats=("plain", "custom"))


def check_composite_key_archives(binary, tools, native):
    def populate(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            db.execute(f"CREATE SCHEMA {schema}")
            db.execute(f"CREATE TABLE {schema}.items (shard int, id int, label text, PRIMARY KEY (shard,id))")
            db.execute(f"INSERT INTO {schema}.items VALUES (1,2,'a'),(2,2,'b')")

    def verify(endpoint, schema):
        with psycopg.connect(**endpoint) as db:
            assert db.execute(f"SELECT * FROM {schema}.items ORDER BY shard").fetchall() == [(1, 2, "a"), (2, 2, "b")]
            for values, error_type in (("1,2,'duplicate'", psycopg.errors.UniqueViolation),
                                       ("NULL,3,'null'", psycopg.errors.NotNullViolation)):
                try:
                    with db.transaction():
                        db.execute(f"INSERT INTO {schema}.items VALUES ({values})")
                except error_type:
                    pass
                else:
                    raise AssertionError(f"restored primary key accepted {values}")

    check_schema_archives(binary, tools, native, "composite", populate, verify)


def check_late_composite_key(binary, tools, native):
    def apply(endpoint):
        with psycopg.connect(**endpoint) as db:
            db.execute("CREATE TABLE late_key (shard int, id int)")
            db.execute("INSERT INTO late_key VALUES (1,2),(2,2),(1,2)")
            try:
                with db.transaction():
                    db.execute("ALTER TABLE late_key ADD PRIMARY KEY (shard,id)")
            except psycopg.errors.UniqueViolation:
                pass
            else:
                raise AssertionError("late primary key accepted duplicate rows")
            db.execute("DELETE FROM late_key")
            db.execute("INSERT INTO late_key VALUES (1,2),(2,2)")
            db.execute("ALTER TABLE late_key ADD CONSTRAINT late_pk PRIMARY KEY (shard,id)")

    def report(endpoint):
        with psycopg.connect(**endpoint) as db:
            rows = db.execute("SELECT * FROM late_key ORDER BY shard,id").fetchall()
            definition = db.execute(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname='late_pk'"
            ).fetchone()
            try:
                with db.transaction():
                    db.execute("INSERT INTO late_key VALUES (1,2)")
            except psycopg.errors.UniqueViolation:
                pass
            else:
                raise AssertionError("late primary key accepted a duplicate insert")
            return rows, definition

    apply(native)
    expected = report(native)
    wal = tools.root / "late-composite.wal"
    storage = ("--workers", "1")
    with DurableServer(binary, wal, create=True, storage_options=storage) as server:
        candidate = ramsql_settings(server)
        apply(candidate)
        assert report(candidate) == expected
    with DurableServer(binary, wal, storage_options=storage) as reopened:
        assert report(ramsql_settings(reopened)) == expected


def check_schema_archives(binary, tools, native, fixture, populate, verify, *, formats=("custom",)):
    for archive_format in formats:
        assert archive_format in ("plain", "custom"), archive_format
        reference = native
        if len(formats) > 1:
            # Each format gets the same isolated workload. Otherwise a later
            # whole-database dump also includes earlier outbound restorations.
            database = f"archive_{fixture}_{archive_format}"
            with psycopg.connect(**dict(native, autocommit=True)) as control:
                control.execute(psycopg.sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(
                    psycopg.sql.Identifier(database)))
            reference = dict(native, dbname=database)
        suffix = f"_{archive_format}" if len(formats) > 1 else ""
        label = f"{fixture}-{archive_format}" if len(formats) > 1 else fixture
        native_schema = f"native_{fixture}{suffix}"
        ramsql_schema = f"ramsql_{fixture}{suffix}"
        extension = "sql" if archive_format == "plain" else "dump"
        inbound = f"{label}-inbound.{extension}"
        outbound = f"{label}-outbound.{extension}"
        wal = tools.root / f"{label}.wal"
        storage = ("--workers", "1")
        populate(reference, native_schema)
        tools.run(f"{label}-inbound-dump", "pg_dump", reference, "--no-owner", "--no-acl",
                  f"--format={archive_format}", f"--file={inbound}")
        with DurableServer(binary, wal, create=True, storage_options=storage) as server:
            candidate = ramsql_settings(server)
            restore_schema_archive(tools, f"{label}-inbound-restore", candidate, inbound, archive_format)
            verify(candidate, native_schema)
        with DurableServer(binary, wal, storage_options=storage) as reopened:
            verify(ramsql_settings(reopened), native_schema)

        with running_ramsql(binary, extra_arguments=storage) as candidate:
            populate(candidate, ramsql_schema)
            tools.run(f"{label}-outbound-dump", "pg_dump", candidate, "--no-owner", "--no-acl",
                      f"--format={archive_format}", f"--file={outbound}")
        restore_schema_archive(tools, f"{label}-outbound-restore", reference, outbound, archive_format)
        verify(reference, ramsql_schema)


def restore_schema_archive(tools, label, endpoint, archive, archive_format):
    if archive_format == "plain":
        tools.run(label, "psql", endpoint, "--no-psqlrc", "--single-transaction",
                  "--set=ON_ERROR_STOP=1", f"--file={archive}")
    else:
        tools.run(label, "pg_restore", endpoint, "--single-transaction",
                  "--exit-on-error", "--no-owner", "--no-acl", archive)


if __name__ == "__main__":
    raise SystemExit(main())
