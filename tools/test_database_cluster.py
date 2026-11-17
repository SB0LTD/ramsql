"""PostgreSQL cluster routing, WAL isolation and process restart contract."""

import argparse
from contextlib import contextmanager
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import time

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
        primary.ok("CREATE TABLE shared (id INT PRIMARY KEY)")
        primary.ok("INSERT INTO shared VALUES (10)")
        primary.ok("BEGIN", status=b"T")
        assert error_code(primary, "CREATE DATABASE forbidden") == "25001"
        primary.ok("ROLLBACK")
        command(primary, 'CREATE DATABASE "Isolated DB"', "CREATE DATABASE")
        assert error_code(primary, 'CREATE DATABASE "Isolated DB"') == "42P04"
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
        primary.ok("SELECT datname FROM pg_catalog.pg_database ORDER BY datname", [["postgres"]])
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


def prove_interrupted_create(binary, base):
    def server(create=False):
        return DurableServer(binary, base, create=create, storage_options=SMALL_PROFILE)

    catalog = Path(str(base) + ".cluster")
    with server(create=True) as live, connect(live, "postgres") as primary:
        command(primary, "CREATE DATABASE orphan", "CREATE DATABASE")
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
        command(primary, "CREATE DATABASE orphan", "CREATE DATABASE")
        primary.ok("SELECT oid FROM pg_database WHERE datname='orphan'", [["7"]])
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".scratch/database-cluster"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix="restart-", dir=args.output.resolve()))
    prove(args.binary.resolve(), case / "production.wal")
    prove_interrupted_create(args.binary.resolve(), case / "interrupted.wal")
    prove_scram(args.binary.resolve(), case / "scram.wal")
    print(f"PASS database routing, independent stores, leases and WAL recovery in {case}")


if __name__ == "__main__":
    main()
