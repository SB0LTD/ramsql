"""PostgreSQL cluster routing, WAL isolation and process restart contract."""

import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import zlib

from test_persistence import DurableServer
from test_server import PG, errors
import test_auth as scram


SMALL_PROFILE = ("--database-cluster", "--rows", "32", "--versions", "64",
                 "--work-rows", "64", "--result-rows", "16")


@contextmanager
def connect(server, database, expected=None, user="postgres"):
    with socket.create_connection(("127.0.0.1", server.port), timeout=8) as sock:
        pg = PG(sock)
        parameters = f"user\0{user}\0database\0{database}\0\0".encode()
        pg.fragments(struct.pack("!II", len(parameters) + 8, 196608) + parameters)
        if expected is not None:
            message = pg.message()
            assert errors([message])[0]["C"] == expected, message
            assert sock.recv(1) == b"", "failed admission did not close"
            yield None
        else:
            messages = pg.ready()
            assert not errors(messages)
            assert (b"R", b"\0\0\0\0") in messages
            yield pg
            pg.close()


def error_code(pg, statement):
    found = errors(pg.query(statement))
    return found[0]["C"] if found else None


def command(pg, statement, tag):
    assert (b"C", tag.encode() + b"\0") in pg.ok(statement), statement


def prove(binary, base):
    def server(create=False):
        return DurableServer(binary, base, create=create, storage_options=SMALL_PROFILE)

    with server(create=True) as live, connect(live, "") as primary:
        primary.ok("SELECT current_database()", [["postgres"]])
        primary.ok("SELECT oid, datistemplate, datallowconn FROM pg_database WHERE datname='template0'",
                   [["4", "t", "f"]])
        assert error_code(primary, "DROP DATABASE template0") == "42809"
        assert error_code(primary, "CREATE DATABASE template0") == "42P04"
        with connect(live, "template0", "55000"):
            pass
        command(primary, "CREATE DATABASE ramsql_tdd_child", "CREATE DATABASE")
        primary.ok("SELECT datname FROM pg_database WHERE datname='ramsql_tdd_child'", [["ramsql_tdd_child"]])
        command(primary, "DROP DATABASE ramsql_tdd_child", "DROP DATABASE")
        with connect(live, "ghost", "3D000"):
            pass
        with connect(live, "postgres", "42501", user="unauthorized"):
            pass
        concurrent = subprocess.run(server().command(), capture_output=True, text=True, timeout=20)
        assert concurrent.returncode != 0 and not concurrent.stdout, concurrent
        assert "DataDirectoryBusy" in concurrent.stderr, concurrent.stderr
        target = base.with_suffix(".backup")
        backup = subprocess.run([str(binary), "--database-cluster", "--data", str(base),
                                 "--backup-to", str(target)], capture_output=True, text=True, timeout=15)
        assert backup.returncode != 0 and "InvalidServerOptions" in backup.stderr and not target.exists(), backup
        primary.ok("CREATE TABLE shared (id INT PRIMARY KEY)")
        primary.ok("INSERT INTO shared VALUES (10)")
        primary.ok("BEGIN", status=b"T")
        assert error_code(primary, "CREATE DATABASE forbidden") == "25001"
        primary.ok("ROLLBACK")
        assert error_code(primary, """CREATE DATABASE unsupported_locale WITH TEMPLATE = template0 LOCALE = 'C.UTF-8'""") == "0A000"
        primary.ok("SELECT datname FROM pg_database WHERE datname='unsupported_locale'", [])
        command(primary, """CREATE DATABASE "Isolated DB" WITH TEMPLATE = template0 ENCODING = 'UTF8' LOCALE_PROVIDER = libc LOCALE = 'C'""",
                "CREATE DATABASE")
        assert error_code(primary, 'CREATE DATABASE "Isolated DB"') == "42P04"
        command(primary, 'ALTER DATABASE "Isolated DB" OWNER TO "ramsql"', "ALTER DATABASE")
        assert error_code(primary, 'ALTER DATABASE "Isolated DB" OWNER TO another_owner') == "0A000"
        with connect(live, "Isolated DB") as secondary:
            secondary.ok("SELECT current_database()", [["Isolated DB"]])
            assert error_code(secondary, "SELECT * FROM shared") == "42P01"
            secondary.ok("CREATE TABLE shared (id INT PRIMARY KEY)")
            secondary.ok("INSERT INTO shared VALUES (20)")
            assert error_code(secondary, 'DROP DATABASE "Isolated DB"') == "55006"
            assert error_code(primary, 'DROP DATABASE "Isolated DB"') == "55006"
            primary.ok("SELECT * FROM shared", [["10"]])
        # DurableServer exits by killing the process.
    assert not base.exists(), "cluster mode reused the single-database path"
    primary_wal = Path(str(base) + ".db-5.wal")
    secondary_wal = Path(str(base) + ".db-7.wal")
    with server() as live, connect(live, "postgres") as primary:
        primary.ok("SELECT * FROM shared", [["10"]])
        with connect(live, "Isolated DB") as secondary:
            secondary.ok("SELECT * FROM shared", [["20"]])
        deadline = time.monotonic() + 8
        while True:
            found = error_code(primary, 'DROP DATABASE "Isolated DB"')
            if found is None:
                break
            assert found == "55006" and time.monotonic() < deadline
            time.sleep(0.02)
        with connect(live, "Isolated DB", "3D000"):
            pass
        primary.ok("SELECT datname FROM pg_catalog.pg_database ORDER BY datname", [["postgres"], ["template0"]])
        command(primary, 'DROP DATABASE IF EXISTS "Isolated DB"', "DROP DATABASE")
        command(primary, 'CREATE DATABASE "Isolated DB"', "CREATE DATABASE")
        with connect(live, "Isolated DB") as secondary:
            assert error_code(secondary, "SELECT * FROM shared") == "42P01"
    assert not secondary_wal.exists()
    with server() as live, connect(live, "postgres") as primary:
        primary.ok("SELECT * FROM shared", [["10"]])
        with connect(live, "Isolated DB") as secondary:
            assert error_code(secondary, "SELECT * FROM shared") == "42P01"

    # A real, parseable database WAL from this cluster is insufficient when
    # it belongs to a different database OID.
    recreated_wal = Path(str(base) + ".db-8.wal")
    recreated_wal.rename(recreated_wal.with_suffix(".original"))
    shutil.copyfile(primary_wal, recreated_wal)
    result = subprocess.run(server().command(), capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and not result.stdout, result
    assert "InvalidDatabaseIdentity" in result.stderr, result.stderr


def prove_legacy_template_name(binary, base):
    def server(create=False):
        return DurableServer(binary, base, create=create, storage_options=SMALL_PROFILE)

    with server(create=True) as live, connect(live, "postgres") as primary:
        command(primary, "CREATE DATABASE legacy_db", "CREATE DATABASE")
        with connect(live, "legacy_db") as peer:
            peer.ok("CREATE TABLE existing_data(id integer)")
            peer.ok("INSERT INTO existing_data VALUES (7)")

    # Older journals allow a user database called template0. Its WAL binds
    # to cluster identity and OID. The cluster journal carries the name.
    catalog = Path(str(base) + ".cluster")
    journal = bytearray(catalog.read_bytes())
    trailer_size, payload_size = 16, 18
    payload = len(journal) - trailer_size - payload_size
    trailer = payload + payload_size
    assert journal[payload] == 2 and journal[payload + 9:trailer] == b"legacy_db"
    journal[payload + 9:trailer] = b"template0"
    struct.pack_into("<I", journal, trailer, zlib.crc32(journal[payload:trailer]))
    catalog.write_bytes(journal)

    with server() as live, connect(live, "postgres") as primary:
        primary.ok("SELECT oid, datistemplate, datallowconn FROM pg_database WHERE datname='template0'",
                   [["6", "f", "t"]])
        with connect(live, "template0") as peer:
            peer.ok("SELECT id FROM existing_data", [["7"]])
        assert error_code(primary, "CREATE DATABASE ambiguous TEMPLATE template0") == "0A000"
        primary.ok("SELECT datname FROM pg_database WHERE datname='ambiguous'", [])


def prove_named_template(binary, base):
    def server(create=False):
        return DurableServer(binary, base, create=create, prepared=2, storage_options=SMALL_PROFILE)

    with server(create=True) as live, connect(live, "postgres") as primary:
        assert error_code(primary, "CREATE DATABASE absent_copy TEMPLATE ghost") == "3D000"
        command(primary, 'CREATE DATABASE "Sales Source"', "CREATE DATABASE")
        with connect(live, "Sales Source") as source:
            source.ok("CREATE TABLE sales(id integer PRIMARY KEY)")
            source.ok("CREATE VIEW sale_ids AS SELECT id FROM sales")
            source.ok("INSERT INTO sales VALUES (1)")
            source.ok("BEGIN", status=b"T")
            source.ok("INSERT INTO sales VALUES (2)", status=b"T")
            command(source, "PREPARE TRANSACTION 'template_commit'", "PREPARE TRANSACTION")
            assert error_code(primary, '''CREATE DATABASE "Sales Copy" TEMPLATE "Sales Source"''') == "55006"
        assert error_code(primary, '''CREATE DATABASE "Sales Copy" TEMPLATE "Sales Source"''') == "55006"
        with connect(live, "Sales Source") as source:
            command(source, "COMMIT PREPARED 'template_commit'", "COMMIT PREPARED")
            source.ok("BEGIN", status=b"T")
            source.ok("INSERT INTO sales VALUES (3)", status=b"T")
            command(source, "PREPARE TRANSACTION 'template_abort'", "PREPARE TRANSACTION")
            command(source, "ROLLBACK PREPARED 'template_abort'", "ROLLBACK PREPARED")
            command(source, '''CREATE DATABASE "Sales Copy" TEMPLATE "Sales Source"''', "CREATE DATABASE")
            source.ok("INSERT INTO sales VALUES (4)")
        with connect(live, "Sales Copy") as copied:
            copied.ok("SELECT oid FROM pg_database WHERE datname='Sales Copy'", [["7"]])
            copied.ok("SELECT id FROM sale_ids ORDER BY id", [["1"], ["2"]])
            copied.ok("INSERT INTO sales VALUES (5)")

    with server() as live, connect(live, "Sales Source") as source, connect(live, "Sales Copy") as copied:
        source.ok("SELECT id FROM sale_ids ORDER BY id", [["1"], ["2"], ["4"]])
        copied.ok("SELECT id FROM sale_ids ORDER BY id", [["1"], ["2"], ["5"]])


def prove_interrupted_create(binary, base, clone=False):
    def server(create=False):
        return DurableServer(binary, base, create=create, storage_options=SMALL_PROFILE)

    catalog = Path(str(base) + ".cluster")
    creation = "CREATE DATABASE orphan" + (" TEMPLATE postgres" if clone else "")
    with server(create=True) as live, connect(live, "postgres") as primary:
        if clone:
            primary.ok("CREATE TABLE snapshot_rows(id integer PRIMARY KEY)")
            primary.ok("INSERT INTO snapshot_rows VALUES (1)")
        command(primary, creation, "CREATE DATABASE")
    # Model a power cut just before publication after the reservation and
    # identity WAL have reached stable storage. No acknowledgement survives.
    raw = catalog.read_bytes()
    header_size, trailer_size = 32, 16
    offset = 0
    last = 0
    while offset < len(raw):
        last = offset
        payload_length = struct.unpack_from("<I", raw, offset + 12)[0]
        offset += header_size + payload_length + trailer_size
    assert offset == len(raw)
    assert raw[last + header_size] == 2, "final journal record must be CREATE"
    catalog.write_bytes(raw[:last + header_size + 2])
    assert Path(str(base) + ".db-6.wal").exists()
    with server() as live, connect(live, "postgres") as primary:
        with connect(live, "orphan", "3D000"):
            pass
        if clone:
            primary.ok("INSERT INTO snapshot_rows VALUES (2)")
        command(primary, creation, "CREATE DATABASE")
        primary.ok("SELECT oid FROM pg_database WHERE datname='orphan'", [["7"]])
        if clone:
            with connect(live, "orphan") as copied:
                copied.ok("SELECT id FROM snapshot_rows ORDER BY id", [["1"], ["2"]])
    assert Path(str(base) + ".db-6.wal").exists()
    assert Path(str(base) + ".db-7.wal").exists()


def prove_scram(binary, base):
    verifier = base.with_suffix(".verifier")
    password, salt = b"owned-test-password", b"owned-test-salt!"
    verifier.write_bytes(scram.make_verifier(password, salt))

    class ScramServer(DurableServer):
        def command(self):
            args = super().command()
            args.remove("--insecure-local")
            return args + ["--auth-verifier-file", str(verifier)]

    def login(server, database, supplied, expected=None):
        with socket.create_connection(("127.0.0.1", server.port), timeout=8) as sock:
            body = ("user\0postgres\0database\0" + database + "\0\0").encode()
            sock.sendall(struct.pack("!II", len(body) + 8, 196608) + body)
            assert scram.receive(sock) == (b"R", struct.pack("!I", 10) + b"SCRAM-SHA-256\0\0")
            bare, first, fields, _ = scram.challenge(sock)
            proof, signature = scram.raw_final(bare, first, fields, scram.proof_keys(supplied, salt, 4096))
            if expected is None:
                scram.finish(sock, proof, signature)
                PG(sock).ok("SELECT current_database()", [[database]])
            else:
                sock.sendall(scram.frame(b"p", proof))
                assert errors([scram.receive(sock)])[0]["C"] == expected
                assert sock.recv(1) == b"", "rejected SCRAM route stayed open"

    with ScramServer(binary, base, create=True, storage_options=SMALL_PROFILE) as live:
        login(live, "missing", b"wrong", "28P01")
        login(live, "missing", password, "3D000")
        login(live, "postgres", password)


def prove_prepared_scope(binary, base):
    def server(create=False):
        return DurableServer(binary, base, create=create, prepared=2, storage_options=SMALL_PROFILE)

    def prepare(session, key, gid, expected=None):
        session.ok("BEGIN", status=b"T")
        session.ok(f"INSERT INTO t VALUES ({key})", status=b"T")
        source = f"PREPARE TRANSACTION '{gid}'"
        if expected is None:
            command(session, source, "PREPARE TRANSACTION")
        else:
            messages = session.query(source)
            assert errors(messages)[0]["C"] == expected, messages
            assert (b"Z", b"I") in messages, messages

    with server(create=True) as live, connect(live, "postgres") as primary:
        command(primary, "CREATE DATABASE peer", "CREATE DATABASE")
        with connect(live, "peer") as peer:
            primary.ok("CREATE TABLE t(id INT PRIMARY KEY)")
            peer.ok("CREATE TABLE t(id INT PRIMARY KEY)")
            prepare(primary, 1, "shared_gid")
            prepare(peer, 2, "shared_gid", "42710")
            prepare(peer, 3, "second_gid")
            prepare(primary, 4, "third_gid", "53200")
            assert error_code(peer, "COMMIT PREPARED 'shared_gid'") == "0A000"
            assert error_code(primary, "ROLLBACK PREPARED 'second_gid'") == "0A000"

    too_small = DurableServer(binary, base, prepared=1, storage_options=SMALL_PROFILE)
    denied = subprocess.run(too_small.command(), capture_output=True, text=True, timeout=20)
    assert denied.returncode and not denied.stdout and "TransactionCapacity" in denied.stderr, denied

    with server() as live, connect(live, "postgres") as primary, connect(live, "peer") as peer:
        primary.ok("SELECT id FROM t", [])
        peer.ok("SELECT id FROM t", [])
        assert error_code(peer, "COMMIT PREPARED 'shared_gid'") == "0A000"
        command(primary, "ROLLBACK PREPARED 'shared_gid'", "ROLLBACK PREPARED")
        command(peer, "COMMIT PREPARED 'second_gid'", "COMMIT PREPARED")
        prepare(peer, 5, "shared_gid")
        command(peer, "COMMIT PREPARED 'shared_gid'", "COMMIT PREPARED")
        primary.ok("SELECT id FROM t", [])
        peer.ok("SELECT id FROM t ORDER BY id", [["3"], ["5"]])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    binary = root / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql")
    parser.add_argument("binary", type=Path, nargs="?", default=binary)
    parser.add_argument("--output", type=Path, default=Path(".scratch/database-cluster"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix="restart-", dir=args.output.resolve()))
    prove(args.binary.resolve(), case / "production.wal")
    prove_legacy_template_name(args.binary.resolve(), case / "legacy-template.wal")
    prove_named_template(args.binary.resolve(), case / "named-template.wal")
    prove_interrupted_create(args.binary.resolve(), case / "interrupted.wal")
    prove_interrupted_create(args.binary.resolve(), case / "interrupted-clone.wal", clone=True)
    prove_scram(args.binary.resolve(), case / "scram.wal")
    prove_prepared_scope(args.binary.resolve(), case / "prepared.wal")
    print(f"PASS database routing, independent stores, leases and WAL recovery in {case}")


if __name__ == "__main__":
    main()
