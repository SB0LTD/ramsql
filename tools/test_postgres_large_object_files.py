"""Exercise server-side large-object files against stock PostgreSQL."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

import psycopg

from qualify_postgres import REFERENCES
from test_auth import make_verifier
from test_postgres_compat import docker, native_postgres
from test_postgres_large_object_archives import expect_sqlstate
from test_persistence import DurableServer
from test_stock_postgres_tools import Tools, ramsql_settings


OID = 57000
PAYLOAD = bytes(range(256)) * 257 + b"\x00end\xff"
STORAGE = ("--workers", "1", "--large-object-pages", "256")


class Files:
    def __init__(self, root, container=None):
        self.root = root
        self.container = container
        self.remote = "/tmp/ramsql-lo-files-" + secrets.token_hex(8)
        root.mkdir()
        if container:
            docker("exec", "--user", "root", container, "mkdir", "-m", "777", self.remote)

    def path(self, name):
        return self.remote + "/" + name if self.container else str(self.root / name)

    def put(self, name, contents, mode=0o644):
        local = self.root / name
        local.write_bytes(contents)
        local.chmod(0o644 if self.container else mode)
        if self.container:
            docker("cp", str(local), self.container + ":" + self.path(name))
            docker("exec", "--user", "root", self.container, "chown", "postgres:postgres", self.path(name))
            docker("exec", "--user", "root", self.container, "chmod", oct(mode)[2:], self.path(name))
        return self.path(name)

    def get(self, name):
        if self.container:
            docker("cp", self.container + ":" + self.path(name), str(self.root / name))
        return (self.root / name).read_bytes()

    def mode(self, name):
        if self.container:
            return int(docker("exec", self.container, "stat", "-c", "%a", self.path(name)).strip(), 8)
        return stat.S_IMODE((self.root / name).stat().st_mode)


def verify_payload(endpoint):
    with psycopg.connect(**endpoint) as db:
        for start in range(0, len(PAYLOAD), 97):
            assert db.execute("SELECT lo_get(%s::oid,%s::bigint,97)", (OID, start)).fetchone() == (PAYLOAD[start:start + 97],)


LIST_FUNCTIONS = "pg_catalog.lo_import(text),lo_import(text,oid),lo_export(oid,text)"
LIST_READER = 'List "reader'
FILE_ROLES = ("file_delegate", "file_leaf", "file_inherited", "file_revoked",
              "list_actor", "list_peer", "list_leaf", "list_revoked", LIST_READER)


def file_roles(endpoint):
    with psycopg.connect(**endpoint) as db:
        for role in FILE_ROLES:
            db.execute(psycopg.sql.SQL("CREATE ROLE {}").format(psycopg.sql.Identifier(role)))


def execute_lists(endpoint, files):
    with psycopg.connect(**dict(endpoint, autocommit=True)) as db:
        db.execute(f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS},pg_catalog.lo_import(text) "
                   "TO list_actor,GROUP list_peer,list_actor WITH GRANT OPTION GRANTED BY CURRENT_ROLE")
        db.execute(f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS} "
                   "TO CURRENT_USER,CURRENT_ROLE,SESSION_USER GRANTED BY SESSION_USER")
        db.execute("SET ROLE list_actor")
        db.execute(f'GRANT ALL PRIVILEGES ON FUNCTION {LIST_FUNCTIONS} '
                   'TO list_leaf,"List ""reader" GRANTED BY CURRENT_USER')
        expect_sqlstate(db, f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS} TO list_peer GRANTED BY SESSION_USER", "0A000")
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_peer")
        db.execute("GRANT EXECUTE ON FUNCTION lo_import(text,oid) TO list_revoked GRANTED BY list_peer")
        db.execute("RESET ROLE")
        expect_sqlstate(db, "REVOKE EXECUTE ON FUNCTION lo_import(text),lo_import(text,oid) "
                        "FROM list_peer GRANTED BY CURRENT_ROLE RESTRICT", "2BP01")
        db.execute("SET ROLE list_peer")
        expect_sqlstate(db, "SELECT lo_import(%s)", "58P01", (files.path("absent"),))
        db.execute("RESET ROLE")
        db.execute(f"REVOKE ALL ON FUNCTION {LIST_FUNCTIONS} FROM list_peer,list_peer GRANTED BY CURRENT_USER CASCADE")
        for statement, state in (
            ("GRANT EXECUTE ON FUNCTION lo_import(text),missing_function() TO list_peer", "42883"),
            (f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS} TO list_peer,missing_role", "42704"),
            (f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS} TO list_peer GRANTED BY list_actor", "0A000"),
            (f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS} TO list_peer GRANTED BY PUBLIC", "42704"),
            (f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS} TO list_peer,PUBLIC WITH GRANT OPTION", "0LP01"),
        ):
            expect_sqlstate(db, statement, state)
        db.execute(f'GRANT SELECT ON LARGE OBJECT {OID} TO list_leaf,"List ""reader"')
        db.execute("SET ROLE list_leaf")
        assert db.execute("SELECT lo_import(%s,57011::oid)", (files.path("source"),)).fetchone() == (57011,)
        assert db.execute("SELECT pg_get_userbyid(lomowner) FROM pg_largeobject_metadata WHERE oid=57011").fetchone() == ("list_leaf",)
        assert db.execute("SELECT lo_get(57011::oid,0,97)").fetchone() == (PAYLOAD[:97],)
        db.execute("SELECT lo_unlink(57011::oid)")
        db.execute("RESET ROLE")
        with db.transaction(force_rollback=True):
            db.execute(f"REVOKE EXECUTE ON FUNCTION {LIST_FUNCTIONS} FROM list_actor GRANTED BY CURRENT_USER CASCADE")
            db.execute("SET ROLE list_leaf")
            expect_sqlstate(db, "SELECT lo_export(%s::oid,%s)", "42501", (OID, files.path("rollback-list")))
    verify_execute_lists(endpoint, files)


def verify_execute_lists(endpoint, files):
    with psycopg.connect(**dict(endpoint, autocommit=True)) as db:
        for oid in (764, 767, 765):
            acl = db.execute("SELECT proacl FROM pg_proc WHERE oid=%s::oid", (oid,)).fetchone()[0]
            assert sum(value.startswith("list_actor=X*/") for value in acl) == 1, (oid, acl)
            assert acl.count("list_leaf=X/list_actor") == 1, (oid, acl)
        db.execute("SET ROLE list_leaf")
        expect_sqlstate(db, "SELECT lo_import(%s)", "58P01", (files.path("absent"),))
        expect_sqlstate(db, "SELECT lo_import(%s,0::oid)", "58P01", (files.path("absent"),))
        assert db.execute("SELECT lo_export(%s::oid,%s)", (OID, files.path("list-export"))).fetchone() == (1,)
        assert files.get("list-export") == PAYLOAD
        db.execute("RESET ROLE")
        db.execute(psycopg.sql.SQL("SET ROLE {}").format(psycopg.sql.Identifier(LIST_READER)))
        assert db.execute("SELECT lo_export(%s::oid,%s)", (OID, files.path("quoted-export"))).fetchone() == (1,)
        assert files.get("quoted-export") == PAYLOAD
        db.execute("RESET ROLE")
        for role in ("list_peer", "list_revoked"):
            db.execute(f"SET ROLE {role}")
            expect_sqlstate(db, "SELECT lo_import(%s)", "42501", (files.path("absent"),))
            expect_sqlstate(db, "SELECT lo_import(%s,0::oid)", "42501", (files.path("absent"),))
            expect_sqlstate(db, "SELECT lo_export(%s::oid,%s)", "42501", (OID, files.path("denied-list")))
            db.execute("RESET ROLE")


def delegated_access(endpoint, files):
    file_roles(endpoint)
    with psycopg.connect(**dict(endpoint, autocommit=True)) as db:
        db.execute("GRANT EXECUTE ON FUNCTION pg_catalog.lo_import(text) TO file_delegate WITH GRANT OPTION")
        db.execute("GRANT file_delegate TO file_inherited")
        db.execute("SET ROLE file_inherited")
        assert db.execute("SELECT lo_import(NULL::text)").fetchone() == (None,)
        expect_sqlstate(db, "SELECT lo_import(%s,0::oid)", "42501", (files.path("absent"),))
        expect_sqlstate(db, "SELECT lo_import(%s)", "58P01", (files.path("absent"),))
        db.execute("RESET ROLE")
        db.execute("SET ROLE file_delegate")
        imported = db.execute("SELECT lo_import(%s)", (files.path("source"),)).fetchone()[0]
        assert db.execute("SELECT pg_get_userbyid(lomowner) FROM pg_largeobject_metadata WHERE oid=%s::oid", (imported,)).fetchone() == ("file_delegate",)
        assert db.execute("SELECT lo_get(%s::oid,0,97)", (imported,)).fetchone() == (PAYLOAD[:97],)
        db.execute("SELECT lo_unlink(%s::oid)", (imported,))
        db.execute("GRANT EXECUTE ON FUNCTION pg_catalog.lo_import(text) TO file_leaf WITH GRANT OPTION")
        db.execute("RESET ROLE")
        db.execute("SET ROLE file_leaf")
        db.execute("GRANT EXECUTE ON FUNCTION lo_import(text) TO file_revoked")
        expect_sqlstate(db, "GRANT EXECUTE ON FUNCTION lo_import(text) TO file_delegate WITH GRANT OPTION", "0LP01")
        db.execute("RESET ROLE")
        expect_sqlstate(db, "REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION lo_import(text) FROM file_delegate", "2BP01")
        db.execute("SET ROLE file_delegate")
        db.execute("REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION lo_import(text) FROM file_leaf CASCADE")
        db.execute("RESET ROLE")
        db.execute("SET ROLE file_revoked")
        expect_sqlstate(db, "SELECT lo_import(%s)", "42501", (files.path("absent"),))
        db.execute("RESET ROLE")
        db.execute("GRANT EXECUTE ON FUNCTION lo_import(text,oid) TO PUBLIC")
        db.execute("SET ROLE file_guest")
        assert db.execute("SELECT lo_import(NULL::text,0::oid)").fetchone() == (None,)
        db.execute("RESET ROLE")
        db.execute("REVOKE ALL ON FUNCTION lo_import(text,oid) FROM PUBLIC")
        db.execute("GRANT ALL PRIVILEGES ON FUNCTION pg_catalog.lo_import(text,oid) TO file_leaf")
        db.execute("GRANT EXECUTE ON FUNCTION pg_catalog.lo_export(oid,text) TO file_leaf")
        db.execute("SET ROLE file_leaf")
        expect_sqlstate(db, "SELECT lo_export(%s::oid,%s)", "42501", (OID, files.path("delegated")))
        db.execute("RESET ROLE")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {OID} TO file_leaf")
        with db.transaction(force_rollback=True):
            db.execute("REVOKE EXECUTE ON FUNCTION lo_export(oid,text) FROM file_leaf")
        expect_sqlstate(db, "DROP ROLE file_leaf", "2BP01")
    verify_delegation(endpoint, files)


def verify_delegation(endpoint, files):
    with psycopg.connect(**dict(endpoint, autocommit=True)) as db:
        for oid, item in ((764, "file_leaf=X/file_delegate"), (767, "file_leaf=X/"), (765, "file_leaf=X/")):
            acl = db.execute("SELECT proacl FROM pg_proc WHERE oid=%s::oid", (oid,)).fetchone()[0]
            assert any(value == item if oid == 764 else value.startswith(item) for value in acl), (oid, acl)
        db.execute("SET ROLE file_leaf")
        assert db.execute("SELECT lo_import(NULL::text)").fetchone() == (None,)
        assert db.execute("SELECT lo_import(NULL::text,0::oid)").fetchone() == (None,)
        assert db.execute("SELECT lo_export(%s::oid,%s)", (OID, files.path("delegated"))).fetchone() == (1,)
        assert files.get("delegated") == PAYLOAD
        db.execute("RESET ROLE")
        for role in ("file_guest", "file_revoked"):
            db.execute(f"SET ROLE {role}")
            expect_sqlstate(db, "SELECT lo_import(%s)", "42501", (files.path("absent"),))
            expect_sqlstate(db, "SELECT lo_import(%s,0::oid)", "42501", (files.path("absent"),))
            expect_sqlstate(db, "SELECT lo_export(%s::oid,%s)", "42501", (OID, files.path("denied-export")))
            db.execute("RESET ROLE")


def restore_roles(endpoint):
    file_roles(endpoint)
    with psycopg.connect(**endpoint) as db:
        db.execute("CREATE ROLE file_guest")


USER_ROUTINES = "public.user_exec(integer),public.user_proc()"


def routine_execute_access(endpoint):
    with psycopg.connect(**dict(endpoint, autocommit=True)) as db:
        db.execute("CREATE FUNCTION public.user_exec(x integer) RETURNS integer LANGUAGE SQL AS 'SELECT x+9'")
        db.execute("CREATE PROCEDURE public.user_proc() LANGUAGE SQL AS 'SELECT 1'")
        db.execute("CREATE FUNCTION public.abs(x integer) RETURNS integer LANGUAGE SQL AS 'SELECT x+10'")
        db.execute("CREATE FUNCTION public.count(x integer) RETURNS integer LANGUAGE SQL AS 'SELECT x+20'")
        db.execute('CREATE FUNCTION public."MiX"(x integer) RETURNS integer LANGUAGE SQL AS \'SELECT x+30\'')
        assert db.execute('SELECT public.abs(-1),pg_catalog.abs(-1),abs(-1),public.count(5),public."MiX"(1)').fetchone() == (9, 1, 1, 25, 31)
        db.execute("SELECT pg_catalog.set_config('search_path','',false)")
        assert db.execute('SELECT public.abs(1),public.count(1),public."MiX"(1)').fetchone() == (11, 21, 31)
        db.execute("CALL public.user_proc()")
        expect_sqlstate(db, "CALL user_proc()", "42883")
        expect_sqlstate(db, "DROP PROCEDURE user_proc()", "42883")
        expect_sqlstate(db, 'GRANT EXECUTE ON FUNCTION "MiX"(integer) TO PUBLIC', "42883")
        expect_sqlstate(db, "CREATE FUNCTION no_path() RETURNS integer LANGUAGE SQL AS 'SELECT 1'", "3F000")
        db.execute('DROP FUNCTION public."MiX"(integer)')
        db.execute('DROP FUNCTION IF EXISTS public."MiX"(integer)')
        db.execute("RESET search_path")
        with db.transaction(force_rollback=True):
            db.execute("SET TRANSACTION READ ONLY")
            assert db.execute("SELECT user_exec(1)").fetchone() == (10,)
            db.execute("CALL public.user_proc()")
            expect_sqlstate(db, f"GRANT EXECUTE ON ROUTINE {USER_ROUTINES} TO list_peer", "25006")
            expect_sqlstate(db, f"REVOKE EXECUTE ON ROUTINE {USER_ROUTINES} FROM PUBLIC", "25006")
        db.execute(f"REVOKE EXECUTE ON ROUTINE {USER_ROUTINES} FROM PUBLIC")
        db.execute(f"GRANT EXECUTE ON ROUTINE {USER_ROUTINES} TO list_actor WITH GRANT OPTION")
        db.execute("SET ROLE list_actor")
        db.execute("GRANT EXECUTE ON FUNCTION public.user_exec(integer) TO PUBLIC")
        db.execute("RESET ROLE")
        db.execute("REVOKE EXECUTE ON FUNCTION public.user_exec(integer) FROM PUBLIC")
        db.execute("SET ROLE list_peer")
        assert db.execute("SELECT user_exec(1)").fetchone() == (10,)
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_actor")
        db.execute("REVOKE EXECUTE ON FUNCTION public.user_exec(integer) FROM PUBLIC")
        db.execute(f"GRANT EXECUTE ON ROUTINE {USER_ROUTINES} TO list_leaf WITH GRANT OPTION GRANTED BY CURRENT_ROLE")
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_leaf")
        db.execute(f"GRANT EXECUTE ON ROUTINE {USER_ROUTINES} TO list_revoked")
        expect_sqlstate(db, "GRANT EXECUTE ON FUNCTION user_exec(integer) TO list_actor WITH GRANT OPTION", "0LP01")
        db.execute("RESET ROLE")
        expect_sqlstate(db, "REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION user_exec(integer) FROM list_actor RESTRICT", "2BP01")
        db.execute("GRANT EXECUTE ON FUNCTION user_exec(integer) TO list_revoked")
        db.execute("REVOKE GRANT OPTION FOR EXECUTE ON FUNCTION user_exec(integer) FROM list_actor CASCADE")
        db.execute("SET ROLE list_revoked")
        assert db.execute("SELECT user_exec(1)").fetchone() == (10,)
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_leaf")
        expect_sqlstate(db, "SELECT user_exec(1)", "42501")
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_actor")
        db.execute("GRANT EXECUTE ON FUNCTION user_exec(integer) TO list_peer")
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_peer")
        expect_sqlstate(db, "SELECT user_exec(1)", "42501")
        db.execute("RESET ROLE")
        db.execute("GRANT EXECUTE ON FUNCTION user_exec(integer) TO list_actor WITH GRANT OPTION")
        db.execute("SET ROLE list_actor")
        db.execute("GRANT EXECUTE ON FUNCTION user_exec(integer) TO list_leaf WITH GRANT OPTION")
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_leaf")
        db.execute("GRANT EXECUTE ON FUNCTION user_exec(integer) TO list_revoked")
        db.execute("RESET ROLE")
        with db.transaction(force_rollback=True):
            db.execute(f"REVOKE EXECUTE ON ROUTINE {USER_ROUTINES} FROM list_actor CASCADE")
            db.execute("SET ROLE list_leaf")
            expect_sqlstate(db, "SELECT user_exec(1)", "42501")
            expect_sqlstate(db, "CALL user_proc()", "42501")
    verify_routine_access(endpoint)


def verify_routine_access(endpoint):
    with psycopg.connect(**dict(endpoint, autocommit=True)) as db:
        assert db.execute("SELECT public.abs(%s::integer),public.count(%s::integer),pg_catalog.abs(-1)", (-1, 5)).fetchone() == (9, 25, 1)
        for name in ("user_exec", "user_proc"):
            acl = db.execute("SELECT proacl FROM pg_proc WHERE proname=%s", (name,)).fetchone()[0]
            assert "list_leaf=X*/list_actor" in acl, (name, acl)
            assert "list_revoked=X/list_leaf" in acl, (name, acl)
        db.execute("SET ROLE list_leaf")
        assert db.execute("SELECT public.user_exec(%s::integer)", (1,)).fetchone() == (10,)
        db.execute("CALL public.user_proc()")
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_revoked")
        assert db.execute("SELECT user_exec(2)").fetchone() == (11,)
        db.execute("CALL public.user_proc()")
        db.execute("RESET ROLE")
        db.execute("SET ROLE list_peer")
        expect_sqlstate(db, "SELECT user_exec(1)", "42501")
        expect_sqlstate(db, "CALL user_proc()", "42501")
        db.execute("RESET ROLE")


def exercise(endpoint, files):
    try:
        _exercise(endpoint, files)
    finally:
        denied = files.root / "denied"
        if denied.exists():
            denied.chmod(0o600)


def _exercise(endpoint, files):
    source = files.put("source", PAYLOAD)
    denied = files.put("denied", b"private", 0)
    target = files.path("exported")
    with psycopg.connect(**dict(endpoint, autocommit=True)) as db:
        db.execute("CREATE ROLE file_guest")
        db.execute("SET ROLE file_guest")
        expect_sqlstate(db, "SELECT lo_import(%s)", "42501", (source,))
        expect_sqlstate(db, "SELECT lo_export(%s::oid,%s)", "42501", (OID, target))
        db.execute("RESET ROLE")
        assert db.execute("SELECT lo_import(NULL::text)").fetchone() == (None,)
        assert db.execute("SELECT lo_import(NULL::text,0::oid)").fetchone() == (None,)
        assert db.execute("SELECT lo_export(NULL::oid,NULL::text)").fetchone() == (None,)
        expect_sqlstate(db, "SELECT lo_import(%s)", "58P01", (files.path("absent"),))
        expect_sqlstate(db, "SELECT lo_import(%s)", "42501", (denied,))
        expect_sqlstate(db, "SELECT lo_import(%s)", "42809", (files.path("."),))
        assert db.execute("SELECT lo_import(%s,%s::oid)", (source, OID)).fetchone() == (OID,)
        expect_sqlstate(db, "SELECT lo_import(%s,%s::oid)", "23505", (source, OID))
        assert db.execute("SELECT lo_export(%s::oid,%s)", (OID, target)).fetchone() == (1,)
        assert files.get("exported") == PAYLOAD
        assert files.mode("exported") == 0o644
        files.put("exported", b"z" * (len(PAYLOAD) + 500), 0o640)
        assert db.execute("SELECT lo_export(%s::oid,%s)", (OID, target)).fetchone() == (1,)
        assert files.get("exported") == PAYLOAD
        assert files.mode("exported") == 0o640
        expect_sqlstate(db, "SELECT lo_export(%s::oid,%s)", "42501", (OID, denied))
        expect_sqlstate(db, "SELECT lo_export(99999::oid,%s)", "42704", (target,))
        assert files.get("exported") == PAYLOAD
        db.execute("BEGIN READ ONLY")
        expect_sqlstate(db, "SELECT lo_import(%s)", "25006", (source,))
        db.execute("ROLLBACK")
        db.execute("BEGIN READ ONLY")
        assert db.execute("SELECT lo_export(%s::oid,%s)", (OID, target)).fetchone() == (1,)
        db.execute("ROLLBACK")
        with db.transaction(force_rollback=True):
            assert db.execute("SELECT lo_import(%s,57001::oid)", (source,)).fetchone() == (57001,)
        assert db.execute("SELECT COUNT(*) FROM pg_largeobject_metadata WHERE oid=57001").fetchone() == (0,)
        # Filesystem output survives SQL rollback, matching PostgreSQL.
        with db.transaction(force_rollback=True):
            db.execute("SELECT lo_export(%s::oid,%s)", (OID, files.path("rolled-back-export")))
        assert files.get("rolled-back-export") == PAYLOAD
        empty = files.put("empty", b"")
        generated = db.execute("SELECT lo_import(%s)", (empty,)).fetchone()[0]
        assert generated > 0
        assert db.execute("SELECT lo_get(%s::oid)", (generated,)).fetchone() == (b"",)
        db.execute("SELECT lo_unlink(%s::oid)", (generated,))
    verify_payload(endpoint)


@contextmanager
def candidate(binary, root, wal, create):
    verifier = root / "verifier"
    password = "large-object-file-test"
    if not verifier.exists():
        verifier.write_bytes(make_verifier(password.encode(), secrets.token_bytes(32)) + b"\n")
        verifier.chmod(0o600)
    previous = os.umask(0o077)
    class FileServer(DurableServer):
        def command(self):
            command = super().command()
            command.remove("--insecure-local")
            return [*command, "--auth-user", "ramsql", "--auth-verifier-file", str(verifier)]
    try:
        with FileServer(binary, wal, create=create, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            endpoint["user"] = "ramsql"
            endpoint["password"] = password
            yield endpoint
        assert server.process.returncode != 0, "recovery fixture must kill the server"
    finally:
        os.umask(previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--reference", choices=REFERENCES, required=True)
    parser.add_argument("--postgres-bin", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.work_dir.resolve()
    root.mkdir(parents=True)
    binary = args.binary.resolve(strict=True)
    image = REFERENCES[args.reference]
    if not args.postgres_bin:
        docker("image", "inspect", image)
    tools = Tools(root, args.postgres_bin, image)
    checks = {}
    wal = root / "candidate.wal"
    files = Files(root / "candidate-files")
    with candidate(binary, root, wal, True) as endpoint:
        exercise(endpoint, files)
        delegated_access(endpoint, files)
        execute_lists(endpoint, files)
        routine_execute_access(endpoint)
        too_large = files.put("too-large", b"x" * (257 * 2048))
        with psycopg.connect(**endpoint) as db:
            expect_sqlstate(db, "SELECT lo_import(%s,57003::oid)", "54000", (too_large,))
            assert db.execute("SELECT COUNT(*) FROM pg_largeobject_metadata WHERE oid=57003").fetchone() == (0,)
        verify_payload(endpoint)
        pending = psycopg.connect(**dict(endpoint, autocommit=False))
        pending.execute("SELECT lo_import(%s,57002::oid)", (files.path("source"),))
        pending.execute("REVOKE EXECUTE ON FUNCTION lo_export(oid,text) FROM file_leaf")
        pending.execute(f"REVOKE EXECUTE ON FUNCTION {LIST_FUNCTIONS} FROM list_actor GRANTED BY CURRENT_USER CASCADE")
        pending.execute(f"GRANT EXECUTE ON FUNCTION {LIST_FUNCTIONS} TO list_peer,list_revoked GRANTED BY SESSION_USER")
        pending.execute(f"REVOKE EXECUTE ON ROUTINE {USER_ROUTINES} FROM list_actor CASCADE")
    pending.close()
    # The server is killed on leaving candidate. The input disappears
    # before restart, so recovery must use committed page bytes from the WAL.
    (files.root / "source").unlink()
    (files.root / "exported").unlink()
    with candidate(binary, root, wal, False) as endpoint:
        verify_payload(endpoint)
        verify_delegation(endpoint, files)
        verify_execute_lists(endpoint, files)
        verify_routine_access(endpoint)
        checks["user_routine_execute_acl_and_wal_recovery"] = True
        checks["public_qualified_routines_and_wal_recovery"] = True
        checks["execute_lists_granted_by_and_atomic_wal_recovery"] = True
        checks["delegated_execute_and_revocation_wal_recovery"] = True
        with psycopg.connect(**endpoint) as db:
            assert db.execute("SELECT COUNT(*) FROM pg_largeobject_metadata WHERE oid=57002").fetchone() == (0,)
            assert db.execute("SELECT lo_export(%s::oid,%s)", (OID, files.path("recovered"))).fetchone() == (1,)
        assert files.get("recovered") == PAYLOAD
        assert not (files.root / "exported").exists()
        checks["permissions_and_wal_recovery"] = True
        checks["capacity_failure_and_uncommitted_import"] = True
        with native_postgres(image, psycopg, binary_directory=args.postgres_bin, work_directory=root) as (native, metadata):
            native_files = Files(root / "native-files", metadata.get("container_id"))
            exercise(native, native_files)
            delegated_access(native, native_files)
            execute_lists(native, native_files)
            routine_execute_access(native)
            checks["native_file_semantics"] = True
            for label, fmt, suffix in (("plain", "p", "sql"), ("custom", "c", "dump")):
                tools.run(f"native-{label}-dump", "pg_dump", native, "--no-owner", f"--format={fmt}", f"--file=native-{label}.{suffix}")
                with candidate(binary, root, root / f"inbound-{label}.wal", True) as dest:
                    restore_roles(dest)
                    if fmt == "p":
                        tools.run(f"inbound-{label}-restore", "psql", dest, "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--single-transaction", f"--file=native-{label}.{suffix}")
                    else:
                        tools.run(f"inbound-{label}-restore", "pg_restore", dest, "--exit-on-error", "--single-transaction", "--no-owner", f"native-{label}.{suffix}")
                    verify_payload(dest)
                    restored_files = Files(root / f"inbound-{label}-files")
                    verify_delegation(dest, restored_files)
                    verify_execute_lists(dest, restored_files)
                    verify_routine_access(dest)
                checks[f"native_to_ramsql_{label}"] = True
                tools.run(f"ramsql-{label}-dump", "pg_dump", endpoint, "--no-owner", f"--format={fmt}", f"--file=ramsql-{label}.{suffix}")
                with native_postgres(image, psycopg, binary_directory=args.postgres_bin, work_directory=root) as (dest, dest_metadata):
                    restore_roles(dest)
                    if fmt == "p":
                        tools.run(f"outbound-{label}-restore", "psql", dest, "--no-psqlrc", "--set=ON_ERROR_STOP=1", "--single-transaction", f"--file=ramsql-{label}.{suffix}")
                    else:
                        tools.run(f"outbound-{label}-restore", "pg_restore", dest, "--exit-on-error", "--single-transaction", "--no-owner", f"ramsql-{label}.{suffix}")
                    verify_payload(dest)
                    restored_files = Files(root / f"outbound-{label}-files", dest_metadata.get("container_id"))
                    verify_delegation(dest, restored_files)
                    verify_execute_lists(dest, restored_files)
                    verify_routine_access(dest)
                checks[f"ramsql_to_native_{label}"] = True
    report = {"passed": True, "reference": args.reference, "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(), "checks": checks}
    (root / "large-object-files.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
