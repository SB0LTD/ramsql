"""Qualify stock plain and custom archives containing PostgreSQL large objects."""
import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

import psycopg

from qualify_postgres import REFERENCES
from test_checkpoint_runtime import CheckpointServer, IDENTITY, selector
from test_postgres_compat import docker, native_postgres
from test_persistence import DurableServer
from test_server import packet
from test_stock_postgres_tools import Tools, ramsql_settings


OID = 55000
# Use PostgreSQL's maximum 63-byte identifier length on the default build.
OWNER = "lo_owner_" + "a" * 54
READER = "lo_reader_" + "b" * 53
FINAL_OWNER = "lo_final_" + "f" * 54
DELEGATE = "LO_delegate_" + "d" * 51
DELEGATE_SQL = f'"{DELEGATE}"'
SECOND = "lo_second_" + "s" * 53
OUTSIDER = "lo_outside_" + "x" * 52
EXTRA_READERS = tuple(f"lo_extra_{i}_" + "e" * 52 for i in range(4))
COMMENT = "archive byte stream with holes"
PREFIX = bytes(range(256)) * 24
TAIL_OFFSET = 32768
TAIL = b"\x00\xfe\xab\xff"
CONTENTS = PREFIX + bytes(TAIL_OFFSET - len(PREFIX)) + TAIL
STORAGE = ("--workers", "1", "--large-object-pages", "128")
FORMATS = (("plain", "sql"), ("custom", "dump"))


def roles(endpoint):
    with psycopg.connect(**endpoint) as db:
        for role in (OWNER, READER, FINAL_OWNER, DELEGATE, SECOND, OUTSIDER, *EXTRA_READERS):
            assert len(role.encode()) == 63, role
            db.execute(f'CREATE ROLE "{role}"')


def expect_sqlstate(db, sql, wanted, parameters=None):
    try:
        with db.transaction():
            db.execute(sql, parameters)
    except psycopg.Error as error:
        assert error.sqlstate == wanted, (sql, error.sqlstate, wanted)
    else:
        raise AssertionError(f"{sql} succeeded, expected SQLSTATE {wanted}")


def create_metadata(endpoint):
    roles(endpoint)
    with psycopg.connect(**endpoint) as db:
        assert db.execute("SELECT pg_catalog.lo_create(%s::oid)", (OID,)).fetchone() == (OID,)
        db.execute(f"ALTER LARGE OBJECT {OID} OWNER TO {OWNER}")
        db.execute(f"SET ROLE {OWNER}")
        db.execute(f"COMMENT ON LARGE OBJECT {OID} IS '{COMMENT}'")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {OID} TO {READER}")
        with db.transaction(force_rollback=True):
            db.execute(f"REVOKE SELECT ON LARGE OBJECT {OID} FROM {READER}")
            assert db.execute(
                "SELECT lomacl::text FROM pg_largeobject_metadata WHERE oid=%s::oid", (OID,)
            ).fetchone() == (f"{{{OWNER}=rw/{OWNER}}}",)


def verify_metadata(endpoint):
    with psycopg.connect(**endpoint) as db:
        record = db.execute(
            "SELECT pg_get_userbyid(lomowner),lomacl::text,acldefault('L',lomowner)::text,"
            "obj_description(oid,'pg_largeobject') FROM pg_largeobject_metadata WHERE oid=%s::oid",
            (OID,),
        ).fetchone()
        assert record == (OWNER, f"{{{OWNER}=rw/{OWNER},{READER}=r/{OWNER}}}",
                          f"{{{OWNER}=rw/{OWNER}}}", COMMENT), record
        expect_sqlstate(db, f"SELECT lo_create({OID})", "23505")
        db.execute(f"SET ROLE {READER}")
        expect_sqlstate(db, f"SELECT lo_unlink({OID})", "42501")


SMALL_OID = 55001
SMALL_PAYLOAD = b"ram\x00\xffsql"


def create_page_payload(endpoint):
    with psycopg.connect(**endpoint) as db:
        db.execute(f"SET ROLE {OWNER}")
        assert db.execute("SELECT lo_from_bytea(%s::oid,%s::bytea)", (SMALL_OID, SMALL_PAYLOAD)).fetchone() == (SMALL_OID,)
        db.execute(f"GRANT SELECT ON LARGE OBJECT {SMALL_OID} TO {READER}")
        with db.transaction(force_rollback=True):
            assert db.execute("SELECT lo_unlink(%s::oid)", (SMALL_OID,)).fetchone() == (1,)
            expect_sqlstate(db, f"SELECT lo_get({SMALL_OID})", "42704")


def verify_page_payload(endpoint):
    with psycopg.connect(**endpoint) as db:
        assert db.execute("SELECT data FROM pg_largeobject WHERE loid=%s::oid", (SMALL_OID,)).fetchone() == (SMALL_PAYLOAD,)
        db.execute(f"SET ROLE {READER}")
        assert db.execute("SELECT lo_get(%s::oid)", (SMALL_OID,)).fetchone() == (SMALL_PAYLOAD,)
        assert db.execute("SELECT lo_get(%s::oid,3,3)", (SMALL_OID,)).fetchone() == (SMALL_PAYLOAD[3:6],)
        expect_sqlstate(db, f"SELECT lo_unlink({SMALL_OID})", "42501")
        assert db.execute("SELECT lo_get(NULL::oid)").fetchone() == (None,)
        with db.transaction(force_rollback=True):
            for query, wanted in (("SELECT lo_get(0::oid)", "42704"),
                                  ("SELECT lo_get(NULL::oid)", "25P02")):
                try:
                    db.execute(query)
                except psycopg.Error as error:
                    assert error.sqlstate == wanted, (query, error.sqlstate, wanted)
                else:
                    raise AssertionError(f"{query} succeeded, expected SQLSTATE {wanted}")


TRUNCATE_OID = 55002
LARGE_EOF = (1 << 31) + 8192


def lo_scalar(db, function, arguments, casts):
    values = ",".join(f"%s::{cast}" for cast in casts)
    return db.execute(f"SELECT pg_catalog.{function}({values})", arguments).fetchone()[0]


def seed_truncate(endpoint):
    with psycopg.connect(**endpoint) as db, db.transaction():
        assert lo_scalar(db, "lo_from_bytea", (TRUNCATE_OID, b"abcdef"), ("oid", "bytea")) == TRUNCATE_OID
        fd = lo_scalar(db, "lo_open", (TRUNCATE_OID, 131072), ("oid", "integer"))
        reader = lo_scalar(db, "lo_open", (TRUNCATE_OID, 262144), ("oid", "integer"))
        assert lo_scalar(db, "lo_lseek", (fd, 5, 0), ("integer",) * 3) == 5
        with db.transaction(force_rollback=True):
            assert lo_scalar(db, "lo_truncate", (fd, 3), ("integer", "integer")) == 0
            assert lo_scalar(db, "lo_truncate64", (fd, 6149), ("integer", "bigint")) == 0
            assert lo_scalar(db, "lo_tell", (fd,), ("integer",)) == 5
            assert lo_scalar(db, "lo_get", (TRUNCATE_OID, 6145, 8), ("oid", "bigint", "integer")) == bytes(4)
            assert lo_scalar(db, "loread", (reader, 6), ("integer", "integer")) == b"abcdef"
        assert lo_scalar(db, "lo_get", (TRUNCATE_OID,), ("oid",)) == b"abcdef"
        expect_sqlstate(db, "SELECT lo_truncate64(%s::integer,-1::bigint)", "22023", (fd,))
        expect_sqlstate(db, "SELECT lo_truncate(%s::integer,0)", "55000", (reader,))
        expect_sqlstate(db, "SELECT lo_truncate64(-1,0::bigint)", "42704")
    with psycopg.connect(**endpoint) as db:
        db.execute("BEGIN READ ONLY")
        expect_sqlstate(db, "SELECT lo_truncate(0,0)", "25006")
        db.execute("ROLLBACK")


def fastpath_int4(pg, function_oid, *arguments):
    frame = struct.pack("!IHHH", function_oid, 1, 1, len(arguments))
    frame += b"".join(struct.pack("!I", len(argument)) + argument for argument in arguments)
    pg.sock.sendall(packet(b"F", frame + struct.pack("!H", 1)))
    response = pg.ready()
    assert response[-1] == (b"Z", b"T"), response
    values = [body for tag, body in response if tag == b"V"]
    assert len(values) == 1 and len(values[0]) == 8, response
    size, value = struct.unpack("!Ii", values[0])
    assert size == 4, response
    return value


def truncate_fastpath(server):
    with closing(server.connect()) as pg:
        pg.ok("BEGIN", status=b"T")
        fd = fastpath_int4(pg, 952, struct.pack("!I", TRUNCATE_OID), struct.pack("!i", 131072))
        assert fastpath_int4(pg, 1004, struct.pack("!i", fd), struct.pack("!i", 4096)) == 0
        assert fastpath_int4(pg, 3172, struct.pack("!i", fd), struct.pack("!q", LARGE_EOF)) == 0
        pg.ok(f"SELECT lo_tell64({fd})", [["0"]], status=b"T")
        pg.ok("COMMIT")


def verify_truncate_replay(endpoint):
    with psycopg.connect(**endpoint) as db, db.transaction():
        fd = lo_scalar(db, "lo_open", (TRUNCATE_OID, 131072), ("oid", "integer"))
        assert lo_scalar(db, "lo_lseek64", (fd, 0, 2), ("integer", "bigint", "integer")) == LARGE_EOF
        assert lo_scalar(db, "lo_get", (TRUNCATE_OID, LARGE_EOF - 4, 8), ("oid", "bigint", "integer")) == bytes(4)
        assert db.execute(f"SELECT pageno FROM pg_largeobject WHERE loid={TRUNCATE_OID} ORDER BY pageno").fetchall() == [
            (0,), (2,), (LARGE_EOF // 2048,),
        ]
        assert lo_scalar(db, "lo_truncate", (fd, 3), ("integer", "integer")) == 0
        assert lo_scalar(db, "lo_truncate64", (fd, 12), ("integer", "bigint")) == 0


ACL_OID = 55003
ACL_BYTES = b"large-object ACL"


def lo_acl(db):
    owner, acl, typed = db.execute(
        "SELECT pg_get_userbyid(lomowner),lomacl::text,lomacl FROM pg_largeobject_metadata "
        "WHERE oid=%s::oid ORDER BY lomowner,lomacl::text,oid",
        (ACL_OID,),
    ).fetchone()
    assert (None if acl is None else acl[1:-1].split(",")) == typed, (acl, typed)
    return owner, None if typed is None else set(typed)


def seed_privileges(endpoint):
    with psycopg.connect(**endpoint) as db, db.transaction():
        db.execute(f"SET ROLE {OWNER}")
        assert lo_scalar(db, "lo_from_bytea", (ACL_OID, ACL_BYTES), ("oid", "bytea")) == ACL_OID
        assert lo_acl(db) == (OWNER, None)
        db.execute(f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO {DELEGATE_SQL}, {FINAL_OWNER} WITH GRANT OPTION")
        db.execute(f"GRANT SELECT, UPDATE ON LARGE OBJECT {ACL_OID} TO {READER}")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO {','.join(EXTRA_READERS)}")
        expect_sqlstate(db, f"GRANT UPDATE ON LARGE OBJECT {ACL_OID} TO PUBLIC WITH GRANT OPTION", "0LP01")
        db.execute(f"SET SESSION AUTHORIZATION {DELEGATE_SQL}")
        assert db.execute("SELECT current_user,session_user").fetchone() == (DELEGATE, DELEGATE)
        db.execute(f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO {SECOND} WITH GRANT OPTION")
        db.execute("RESET SESSION AUTHORIZATION")
        db.execute(f"SET ROLE {SECOND}")
        expect_sqlstate(db, f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO {DELEGATE_SQL} WITH GRANT OPTION", "0LP01")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO {READER}")
        db.execute(f"SET ROLE {FINAL_OWNER}")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO {READER} GRANTED BY CURRENT_USER")
        db.execute(f"SET ROLE {OWNER}")
        expect_sqlstate(db, f"REVOKE GRANT OPTION FOR SELECT ON LARGE OBJECT {ACL_OID} FROM {DELEGATE_SQL} RESTRICT", "2BP01")
        with db.transaction(force_rollback=True):
            db.execute(f"REVOKE GRANT OPTION FOR SELECT ON LARGE OBJECT {ACL_OID} FROM {DELEGATE_SQL} CASCADE")
            assert all(SECOND not in entry for entry in lo_acl(db)[1])
            assert f"{DELEGATE}=r/{OWNER}" in lo_acl(db)[1]
            db.execute(f"SET ROLE {SECOND}")
            expect_sqlstate(db, f"SELECT lo_get({ACL_OID})", "42501")
        with db.transaction(force_rollback=True):
            # A second path to a grantor preserves that grantor's dependents.
            db.execute(f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO {SECOND} WITH GRANT OPTION")
            db.execute(f"REVOKE SELECT ON LARGE OBJECT {ACL_OID} FROM {DELEGATE_SQL} CASCADE")
            assert f"{READER}=r/{SECOND}" in lo_acl(db)[1]
        expect_sqlstate(db, f"ALTER LARGE OBJECT {ACL_OID} OWNER TO {FINAL_OWNER}", "42501")
        db.execute("RESET ROLE")
        db.execute(f"ALTER LARGE OBJECT {ACL_OID} OWNER TO {FINAL_OWNER}")


def verify_privileges(endpoint):
    with psycopg.connect(**endpoint) as db, db.transaction():
        assert lo_acl(db) == (FINAL_OWNER, {
            f"{FINAL_OWNER}=r*w/{FINAL_OWNER}", f"{DELEGATE}=r*/{FINAL_OWNER}",
            f"{SECOND}=r*/{DELEGATE}", f"{READER}=r/{SECOND}", f"{READER}=rw/{FINAL_OWNER}",
            *(f"{role}=r/{FINAL_OWNER}" for role in EXTRA_READERS),
        }), lo_acl(db)
        projection = f"SELECT lomacl::text FROM pg_largeobject_metadata WHERE oid={ACL_OID}"
        text = db.execute(projection).fetchone()[0]
        # Beyond both the inline cell and the ordinary 128-byte profile's
        # 1,088-byte value formatting scratch.
        assert len(text.encode()) > 1088, len(text)
        item = db.execute(f"SELECT lomacl[1] FROM pg_largeobject_metadata WHERE oid={ACL_OID}").fetchone()[0]
        assert item == f"{FINAL_OWNER}=r*w/{FINAL_OWNER}" and len(item.encode()) > 128
        with db.cursor(binary=True) as binary:
            assert binary.execute(projection).fetchone() == (text,)
        db.execute(f"DECLARE acl_first CURSOR FOR {projection}")
        db.execute(f"DECLARE acl_second CURSOR FOR {projection}")
        db.execute("CLOSE acl_first")
        assert db.execute("FETCH ALL FROM acl_second").fetchall() == [(text,)]
        db.execute("CLOSE acl_second")
        for role in (OWNER, OUTSIDER):
            db.execute(f"SET ROLE {role}")
            expect_sqlstate(db, f"SELECT lo_get({ACL_OID})", "42501")
            expect_sqlstate(db, f"SELECT lo_open({ACL_OID},262144)", "42501")
            expect_sqlstate(db, f"SELECT lo_unlink({ACL_OID})", "42501")
            expect_sqlstate(db, f"ALTER LARGE OBJECT {ACL_OID} OWNER TO CURRENT_USER", "42501")
        db.execute(f"SET ROLE {SECOND}")
        assert lo_scalar(db, "lo_get", (ACL_OID,), ("oid",)) == ACL_BYTES
        expect_sqlstate(db, f"SELECT lo_open({ACL_OID},131072)", "42501")
        expect_sqlstate(db, f"SELECT lo_put({ACL_OID},0,'x'::bytea)", "42501")
        db.execute(f"SET ROLE {READER}")
        with db.transaction(force_rollback=True):
            fd = lo_scalar(db, "lo_open", (ACL_OID, 131072), ("oid", "integer"))
            assert lo_scalar(db, "lo_truncate64", (fd, 2), ("integer", "bigint")) == 0
            assert lo_scalar(db, "lo_get", (ACL_OID,), ("oid",)) == ACL_BYTES[:2]
        with db.transaction(force_rollback=True):
            warnings = []
            db.add_notice_handler(lambda notice: warnings.append(notice.sqlstate))
            db.execute(f"GRANT UPDATE ON LARGE OBJECT {ACL_OID} TO {OUTSIDER}")
            db.execute(f"REVOKE UPDATE ON LARGE OBJECT {ACL_OID} FROM {OUTSIDER}")
            assert warnings == ["01007", "01006"], warnings
            db.execute(f"SET SESSION AUTHORIZATION {FINAL_OWNER}")
            expect_sqlstate(db, f"SET ROLE {OWNER}", "42501")
            db.execute(f"GRANT UPDATE ON LARGE OBJECT {ACL_OID} TO {OUTSIDER}")
            db.execute("RESET SESSION AUTHORIZATION")
            db.execute(f"SET ROLE {OUTSIDER}")
            expect_sqlstate(db, f"SELECT lo_open({ACL_OID},131072)", "42501")
            expect_sqlstate(db, f"SELECT lo_put({ACL_OID},0,'x'::bytea)", "42501")
            db.execute(f"SET ROLE {FINAL_OWNER}")
            db.execute(f"GRANT SELECT ON LARGE OBJECT {ACL_OID} TO PUBLIC")
            db.execute("RESET SESSION AUTHORIZATION")
            db.execute(f"SET ROLE {OUTSIDER}")
            assert lo_scalar(db, "lo_get", (ACL_OID,), ("oid",)) == ACL_BYTES
            db.execute("RESET ROLE")
            db.execute(f"GRANT {OUTSIDER} TO {FINAL_OWNER} WITH INHERIT FALSE, SET TRUE")
            db.execute(f"SET SESSION AUTHORIZATION {FINAL_OWNER}")
            db.execute(f"ALTER LARGE OBJECT {ACL_OID} OWNER TO {OUTSIDER}")
            assert lo_acl(db)[0] == OUTSIDER
        assert lo_acl(db)[0] == FINAL_OWNER


def revoke_privileges(endpoint):
    with psycopg.connect(**endpoint) as db, db.transaction():
        db.execute(f"SET ROLE {FINAL_OWNER}")
        db.execute(f"REVOKE GRANT OPTION FOR SELECT ON LARGE OBJECT {ACL_OID} FROM {DELEGATE_SQL} CASCADE")


def verify_revoked_privileges(endpoint):
    with psycopg.connect(**endpoint) as db, db.transaction():
        assert lo_acl(db) == (FINAL_OWNER, {
            f"{FINAL_OWNER}=r*w/{FINAL_OWNER}", f"{DELEGATE}=r/{FINAL_OWNER}",
            f"{READER}=rw/{FINAL_OWNER}",
            *(f"{role}=r/{FINAL_OWNER}" for role in EXTRA_READERS),
        }), lo_acl(db)
        db.execute(f"SET ROLE {SECOND}")
        expect_sqlstate(db, f"SELECT lo_get({ACL_OID})", "42501")


DEFAULT_OIDS = (55010, 55011, 55012, 55013)
DEFAULT_PAYLOAD = b"copied creation privileges"


def default_acl(db):
    return db.execute(
        "SELECT tableoid,defaclnamespace,defaclobjtype::text,defaclacl FROM pg_default_acl "
        "WHERE pg_get_userbyid(defaclrole)=%s", (FINAL_OWNER,),
    ).fetchall()


def seed_default_acl(endpoint):
    with psycopg.connect(**endpoint) as db, db.transaction():
        db.execute(f"SET ROLE {READER}")
        expect_sqlstate(db, f"ALTER DEFAULT PRIVILEGES FOR ROLE {FINAL_OWNER} GRANT SELECT ON LARGE OBJECTS TO PUBLIC", "42501")
        db.execute(f"SET ROLE {FINAL_OWNER}")
        expect_sqlstate(db, "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON LARGE OBJECTS TO PUBLIC", "0LP01")
        assert default_acl(db) == []
        commands = (
            None,
            f"GRANT SELECT ON LARGE OBJECTS TO {READER} WITH GRANT OPTION",
            f"REVOKE GRANT OPTION FOR SELECT ON LARGE OBJECTS FROM {READER} RESTRICT",
            f"REVOKE SELECT ON LARGE OBJECTS FROM {READER} CASCADE",
        )
        for oid, command in zip(DEFAULT_OIDS, commands, strict=True):
            if command:
                db.execute(f"ALTER DEFAULT PRIVILEGES {command}")
            assert lo_scalar(db, "lo_from_bytea", (oid, DEFAULT_PAYLOAD), ("oid", "bytea")) == oid
        assert default_acl(db) == [], "a template equal to the native default should disappear"
        db.execute("RESET ROLE")
        db.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {FINAL_OWNER} GRANT SELECT ON LARGE OBJECTS TO {READER}")
        with db.transaction(force_rollback=True):
            db.execute(f"ALTER DEFAULT PRIVILEGES FOR USER {FINAL_OWNER} GRANT UPDATE ON LARGE OBJECTS TO PUBLIC")
            assert "=w/" + FINAL_OWNER in default_acl(db)[0][3]


def seed_materialized_acl(endpoint):
    """Produce the same object ACLs with native PG16's per-object GRANT syntax."""
    with psycopg.connect(**endpoint) as db, db.transaction():
        db.execute(f"SET ROLE {FINAL_OWNER}")
        for oid in DEFAULT_OIDS:
            assert lo_scalar(db, "lo_from_bytea", (oid, DEFAULT_PAYLOAD), ("oid", "bytea")) == oid
        db.execute(f"GRANT SELECT ON LARGE OBJECT {DEFAULT_OIDS[1]} TO {READER} WITH GRANT OPTION")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {DEFAULT_OIDS[2]} TO {READER}")


def verify_default_acl(endpoint, *, active=True):
    with psycopg.connect(**endpoint) as db, db.transaction():
        expected = [(826, 0, "L", {f"{FINAL_OWNER}=rw/{FINAL_OWNER}", f"{READER}=r/{FINAL_OWNER}"})]
        actual = [(table, schema, kind, set(acl)) for table, schema, kind, acl in default_acl(db)]
        assert actual == (expected if active else []), actual
        query = db.execute(
            "SELECT oid,lomacl FROM pg_largeobject_metadata WHERE oid BETWEEN 55010 AND 55013 ORDER BY oid"
        ).fetchall()
        rows = [(oid, None if acl is None else set(acl)) for oid, acl in query]
        assert rows == [
            (DEFAULT_OIDS[0], None),
            (DEFAULT_OIDS[1], {f"{FINAL_OWNER}=rw/{FINAL_OWNER}", f"{READER}=r*/{FINAL_OWNER}"}),
            (DEFAULT_OIDS[2], {f"{FINAL_OWNER}=rw/{FINAL_OWNER}", f"{READER}=r/{FINAL_OWNER}"}),
            (DEFAULT_OIDS[3], None),
        ], rows
        db.execute(f"SET ROLE {READER}")
        for oid in (DEFAULT_OIDS[0], DEFAULT_OIDS[3]):
            expect_sqlstate(db, f"SELECT lo_get({oid})", "42501")
        for oid in DEFAULT_OIDS[1:3]:
            assert lo_scalar(db, "lo_get", (oid,), ("oid",)) == DEFAULT_PAYLOAD
            expect_sqlstate(db, f"SELECT lo_put({oid},0,'x'::bytea)", "42501")
        db.execute(f"SET ROLE {FINAL_OWNER}")
        with db.transaction(force_rollback=True):
            oid = lo_scalar(db, "lo_creat", (-1,), ("integer",))
            db.execute(f"SET ROLE {READER}")
            if active:
                assert lo_scalar(db, "lo_get", (oid,), ("oid",)) == b""
            else:
                expect_sqlstate(db, f"SELECT lo_get({oid})", "42501")


def revoke_default_acl(endpoint):
    with psycopg.connect(**endpoint) as db:
        db.execute(f"ALTER DEFAULT PRIVILEGES FOR ROLE {FINAL_OWNER} REVOKE SELECT ON LARGE OBJECTS FROM {READER}")


def populate(endpoint, *, defaults=False):
    roles(endpoint)
    with psycopg.connect(**endpoint) as db:
        db.execute(f"SET ROLE {OWNER}")
        assert db.execute("SELECT lo_from_bytea(%s::oid, %s::bytea)", (OID, PREFIX)).fetchone() == (OID,)
        db.execute("SELECT lo_put(%s::oid, %s::bigint, %s::bytea)", (OID, TAIL_OFFSET, TAIL))
        with db.transaction():
            fd = lo_scalar(db, "lo_open", (OID, 131072), ("oid", "integer"))
            assert lo_scalar(db, "lo_truncate64", (fd, len(CONTENTS) + 16), ("integer", "bigint")) == 0
            assert lo_scalar(db, "lo_truncate", (fd, len(CONTENTS)), ("integer", "integer")) == 0
        db.execute(f"COMMENT ON LARGE OBJECT {OID} IS '{COMMENT}'")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {OID} TO {READER}")
    seed_privileges(endpoint)
    if defaults:
        seed_default_acl(endpoint)


def contents(db):
    return db.execute("SELECT lo_get(%s::oid)", (OID,)).fetchone()[0]


def verify(endpoint, *, defaults=False):
    with psycopg.connect(**endpoint) as db:
        record = db.execute(
            "SELECT pg_get_userbyid(lomowner),obj_description(oid,'pg_largeobject') "
            "FROM pg_largeobject_metadata WHERE oid=%s::oid",
            (OID,),
        ).fetchone()
        assert record == (OWNER, COMMENT), record
        assert contents(db) == CONTENTS, "large-object payload changed"
        db.execute(f"SET ROLE {READER}")
        assert db.execute("SELECT lo_get(%s::oid,%s::bigint,6)", (OID, TAIL_OFFSET - 2)).fetchone() == (
            b"\x00\x00" + TAIL,
        )
        expect_sqlstate(db, "SELECT lo_put(%s::oid,0,'changed'::bytea)", "42501", (OID,))
        db.execute(f"SET ROLE {OWNER}")
        with db.transaction(force_rollback=True):
            db.execute("SELECT lo_put(%s::oid,2047,'abc'::bytea)", (OID,))
            assert db.execute("SELECT lo_get(%s::oid,2047,3)", (OID,)).fetchone() == (b"abc",)
        assert contents(db) == CONTENTS, "rolled-back write survived"
    verify_privileges(endpoint)
    if defaults:
        verify_default_acl(endpoint)


def dump(tools, endpoint, fmt, artifact):
    tools.run(f"dump-{artifact}", "pg_dump", endpoint, "--large-objects",
              f"--format={fmt}", f"--file={artifact}")


def restore(tools, endpoint, fmt, artifact, *, defaults=False):
    roles(endpoint)
    if fmt == "plain":
        tools.run(f"restore-{artifact}", "psql", endpoint, "--no-psqlrc", "--single-transaction",
                  "--set=ON_ERROR_STOP=1", f"--file={artifact}")
    else:
        tools.run(f"restore-{artifact}", "pg_restore", endpoint, "--single-transaction", "--exit-on-error", artifact)
    verify(endpoint)
    verify_default_acl(endpoint, active=defaults)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--binary", type=Path)
    mode.add_argument("--native-only", action="store_true", help="check the fixture against stock PostgreSQL")
    parser.add_argument("--reference", choices=REFERENCES, required=True)
    parser.add_argument("--postgres-bin", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="large-object-archives-", dir=args.work_dir.resolve()))
    image = REFERENCES[args.reference]
    if args.postgres_bin is None:
        try:
            docker("image", "inspect", image)
        except subprocess.CalledProcessError:
            docker("pull", image)
    tools = Tools(root, args.postgres_bin, image)
    binary = args.binary.resolve(strict=True) if args.binary else None
    archive_defaults = args.reference == "18"
    results = {}

    def record(name, check):
        try:
            check()
        except (AssertionError, OSError, RuntimeError, psycopg.Error, subprocess.SubprocessError) as error:
            results[name] = {"passed": False, "error": str(error)}
        else:
            results[name] = {"passed": True}

    def native_metadata():
        with native_postgres(image, psycopg, binary_directory=args.postgres_bin, work_directory=root) as (endpoint, _):
            create_metadata(endpoint)
            verify_metadata(endpoint)
            create_page_payload(endpoint)
            verify_page_payload(endpoint)
            seed_truncate(endpoint)
            if archive_defaults:
                seed_default_acl(endpoint)
                verify_default_acl(endpoint)
            else:
                with psycopg.connect(**endpoint) as db:
                    expect_sqlstate(db, "ALTER DEFAULT PRIVILEGES GRANT SELECT ON LARGE OBJECTS TO PUBLIC", "42601")

    def ramsql_metadata_recovery():
        wal = root / "large-object-metadata.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            create_metadata(endpoint)
            verify_metadata(endpoint)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            verify_metadata(ramsql_settings(server))

    def ramsql_page_recovery():
        wal = root / "large-object-pages.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            roles(endpoint)
            create_page_payload(endpoint)
            verify_page_payload(endpoint)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            verify_page_payload(endpoint)
            with psycopg.connect(**endpoint) as db:
                assert db.execute("SELECT lo_unlink(%s::oid)", (SMALL_OID,)).fetchone() == (1,)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            with psycopg.connect(**ramsql_settings(server)) as db:
                expect_sqlstate(db, f"SELECT lo_get({SMALL_OID})", "42704")
                assert db.execute("SELECT COUNT(*) FROM pg_largeobject WHERE loid=%s::oid", (SMALL_OID,)).fetchone() == (0,)

    def ramsql_truncate():
        wal = root / "large-object-truncate.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            seed_truncate(ramsql_settings(server))
            truncate_fastpath(server)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            verify_truncate_replay(ramsql_settings(server))
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            with psycopg.connect(**ramsql_settings(server)) as db:
                assert lo_scalar(db, "lo_get", (TRUNCATE_OID,), ("oid",)) == b"abc" + bytes(9)
                assert db.execute(f"SELECT pageno FROM pg_largeobject WHERE loid={TRUNCATE_OID}").fetchall() == [(0,)]

    def ramsql_acl_recovery():
        wal = root / "large-object-acl.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            roles(endpoint)
            seed_privileges(endpoint)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            verify_privileges(endpoint)
            revoke_privileges(endpoint)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            verify_revoked_privileges(ramsql_settings(server))

    def ramsql_default_acl_recovery():
        wal = root / "large-object-default-acl.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            roles(endpoint)
            seed_default_acl(endpoint)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            verify_default_acl(endpoint)
            revoke_default_acl(endpoint)
        with DurableServer(binary, wal, storage_options=STORAGE) as server:
            verify_default_acl(ramsql_settings(server), active=False)

    def ramsql_checkpoint():
        wal = root / "large-object-checkpoint.wal"
        stem = root / "object-checkpoint"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            populate(ramsql_settings(server), defaults=True)
        collapsed = subprocess.run(
            [str(binary), "--data", str(wal), "--bootstrap-checkpoint", "--checkpoint-stem", str(stem),
             "--checkpoint-id", IDENTITY, *STORAGE],
            capture_output=True, text=True, timeout=40,
        )
        assert collapsed.returncode == 0, collapsed.stderr
        assert selector(stem) == 1
        with CheckpointServer(binary, stem, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            verify(endpoint, defaults=True)
            revoke_privileges(endpoint)
            revoke_default_acl(endpoint)
            with psycopg.connect(**endpoint) as db:
                assert db.execute("SELECT lo_unlink(%s::oid)", (OID,)).fetchone() == (1,)
        with CheckpointServer(binary, stem, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            verify_revoked_privileges(endpoint)
            verify_default_acl(endpoint, active=False)
            with psycopg.connect(**endpoint) as db:
                expect_sqlstate(db, f"SELECT lo_get({OID})", "42704")

    def native_restore(fmt, artifact):
        with native_postgres(image, psycopg, binary_directory=args.postgres_bin, work_directory=root) as (endpoint, _):
            restore(tools, endpoint, fmt, artifact, defaults=archive_defaults)

    def ramsql_restore(fmt, artifact):
        wal = root / f"inbound-{fmt}.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            restore(tools, ramsql_settings(server), fmt, artifact, defaults=archive_defaults)
        with DurableServer(binary, wal, storage_options=STORAGE) as reopened:
            verify(ramsql_settings(reopened))
            verify_default_acl(ramsql_settings(reopened), active=archive_defaults)

    def populate_archive(endpoint, *, native):
        populate(endpoint, defaults=archive_defaults or not native)
        if not archive_defaults:
            if native:
                seed_materialized_acl(endpoint)
            else:
                revoke_default_acl(endpoint)
        verify(endpoint)
        verify_default_acl(endpoint, active=archive_defaults)

    def ramsql_export(fmt, suffix):
        artifact = f"candidate-{fmt}.{suffix}"
        wal = root / f"outbound-{fmt}.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            populate_archive(endpoint, native=False)
            dump(tools, endpoint, fmt, artifact)
        native_restore(fmt, artifact)

    record("native_metadata" if binary is None else "ramsql_metadata_crash_recovery",
           native_metadata if binary is None else ramsql_metadata_recovery)
    if binary is not None:
        record("ramsql_payload_crash_recovery", ramsql_page_recovery)
        record("ramsql_compact_checkpoint_recovery", ramsql_checkpoint)
        record("ramsql_truncate_crash_recovery", ramsql_truncate)
        record("ramsql_acl_crash_recovery", ramsql_acl_recovery)
        record("ramsql_default_acl_crash_recovery", ramsql_default_acl_recovery)

    with native_postgres(image, psycopg, binary_directory=args.postgres_bin, work_directory=root) as (source, metadata):
        assert int(metadata["server_version_num"]) // 10000 == int(args.reference), metadata["server_version"]
        populate_archive(source, native=True)
        for fmt, suffix in FORMATS:
            artifact = f"native-{fmt}.{suffix}"
            dump(tools, source, fmt, artifact)
            if binary is None:
                record(f"native_to_native_{fmt}", lambda: native_restore(fmt, artifact))
            else:
                record(f"native_to_ramsql_{fmt}", lambda: ramsql_restore(fmt, artifact))
                record(f"ramsql_to_native_{fmt}", lambda: ramsql_export(fmt, suffix))
    binary_hash = None
    if binary:
        with binary.open("rb") as stream:
            binary_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    passed = all(result["passed"] for result in results.values())
    report = {"postgres": metadata["server_version"], "binary_sha256": binary_hash,
              "archives_include_large_object_defaults": archive_defaults, "passed": passed, "checks": results}
    (root / "large-object-archives.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Report directory {root}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
