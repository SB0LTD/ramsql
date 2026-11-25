"""Qualify stock plain and custom archives containing PostgreSQL large objects."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import psycopg

from qualify_postgres import REFERENCES
from test_checkpoint_runtime import CheckpointServer, IDENTITY, selector
from test_postgres_compat import docker, native_postgres
from test_persistence import DurableServer
from test_stock_postgres_tools import Tools, ramsql_settings


OID = 55000
OWNER = "archive_blob_owner"
READER = "archive_blob_reader"
COMMENT = "archive byte stream with holes"
PREFIX = bytes(range(256)) * 24
TAIL_OFFSET = 32768
TAIL = b"\x00\xfe\xab\xff"
CONTENTS = PREFIX + bytes(TAIL_OFFSET - len(PREFIX)) + TAIL
STORAGE = ("--workers", "1", "--large-object-pages", "128")
FORMATS = (("plain", "sql"), ("custom", "dump"))


def roles(endpoint):
    with psycopg.connect(**endpoint) as db:
        db.execute(f"CREATE ROLE {OWNER}")
        db.execute(f"CREATE ROLE {READER}")


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


def populate(endpoint):
    roles(endpoint)
    with psycopg.connect(**endpoint) as db:
        db.execute(f"SET ROLE {OWNER}")
        assert db.execute("SELECT lo_from_bytea(%s::oid, %s::bytea)", (OID, PREFIX)).fetchone() == (OID,)
        db.execute("SELECT lo_put(%s::oid, %s::bigint, %s::bytea)", (OID, TAIL_OFFSET, TAIL))
        db.execute(f"COMMENT ON LARGE OBJECT {OID} IS '{COMMENT}'")
        db.execute(f"GRANT SELECT ON LARGE OBJECT {OID} TO {READER}")


def contents(db):
    return db.execute("SELECT lo_get(%s::oid)", (OID,)).fetchone()[0]


def verify(endpoint):
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


def dump(tools, endpoint, fmt, artifact):
    tools.run(f"dump-{artifact}", "pg_dump", endpoint, "--large-objects",
              f"--format={fmt}", f"--file={artifact}")


def restore(tools, endpoint, fmt, artifact):
    roles(endpoint)
    if fmt == "plain":
        tools.run(f"restore-{artifact}", "psql", endpoint, "--no-psqlrc", "--single-transaction",
                  "--set=ON_ERROR_STOP=1", f"--file={artifact}")
    else:
        tools.run(f"restore-{artifact}", "pg_restore", endpoint, "--single-transaction", "--exit-on-error", artifact)
    verify(endpoint)


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

    def ramsql_checkpoint():
        wal = root / "large-object-checkpoint.wal"
        stem = root / "object-checkpoint"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            populate(ramsql_settings(server))
        collapsed = subprocess.run(
            [str(binary), "--data", str(wal), "--bootstrap-checkpoint", "--checkpoint-stem", str(stem),
             "--checkpoint-id", IDENTITY, *STORAGE],
            capture_output=True, text=True, timeout=40,
        )
        assert collapsed.returncode == 0, collapsed.stderr
        assert selector(stem) == 1
        with CheckpointServer(binary, stem, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            verify(endpoint)
            with psycopg.connect(**endpoint) as db:
                assert db.execute("SELECT lo_unlink(%s::oid)", (OID,)).fetchone() == (1,)
        with CheckpointServer(binary, stem, storage_options=STORAGE) as server:
            with psycopg.connect(**ramsql_settings(server)) as db:
                expect_sqlstate(db, f"SELECT lo_get({OID})", "42704")

    def native_restore(fmt, artifact):
        with native_postgres(image, psycopg, binary_directory=args.postgres_bin, work_directory=root) as (endpoint, _):
            restore(tools, endpoint, fmt, artifact)

    def ramsql_restore(fmt, artifact):
        wal = root / f"inbound-{fmt}.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            restore(tools, ramsql_settings(server), fmt, artifact)
        with DurableServer(binary, wal, storage_options=STORAGE) as reopened:
            verify(ramsql_settings(reopened))

    def ramsql_export(fmt, suffix):
        artifact = f"candidate-{fmt}.{suffix}"
        wal = root / f"outbound-{fmt}.wal"
        with DurableServer(binary, wal, create=True, storage_options=STORAGE) as server:
            endpoint = ramsql_settings(server)
            populate(endpoint)
            verify(endpoint)
            dump(tools, endpoint, fmt, artifact)
        native_restore(fmt, artifact)

    record("native_metadata" if binary is None else "ramsql_metadata_crash_recovery",
           native_metadata if binary is None else ramsql_metadata_recovery)
    if binary is not None:
        record("ramsql_payload_crash_recovery", ramsql_page_recovery)
        record("ramsql_compact_checkpoint_recovery", ramsql_checkpoint)

    with native_postgres(image, psycopg, binary_directory=args.postgres_bin, work_directory=root) as (source, metadata):
        assert int(metadata["server_version_num"]) // 10000 == int(args.reference), metadata["server_version"]
        populate(source)
        verify(source)
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
    report = {"postgres": metadata["server_version"], "binary_sha256": binary_hash, "passed": passed, "checks": results}
    (root / "large-object-archives.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Report directory {root}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
