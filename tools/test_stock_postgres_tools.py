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
        ("type_discovery", lambda db: check_type_discovery(binary, db)),
        ("operator_discovery", lambda db: check_operator_discovery(binary, db)),
        ("access_method_discovery", lambda db: check_access_method_discovery(binary, db)),
        ("operator_class_discovery", lambda db: check_operator_class_discovery(binary, db, args.reference)),
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
            routines = [
                row for row in ram.execute(f"SELECT {procedure_fields} FROM pg_proc WHERE pronamespace=11 ORDER BY oid")
                if row[0] in routine_oids
            ]
            procedure_oids = sorted(routine_oids)
            assert [row[0] for row in routines] == procedure_oids
            # The implementation is internal to Sig even for signatures
            # implemented by SQL wrappers in PostgreSQL.
            assert ram.execute("SELECT DISTINCT prolang FROM pg_proc WHERE pronamespace=11").fetchall() == [(12,)]
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


if __name__ == "__main__":
    raise SystemExit(main())
