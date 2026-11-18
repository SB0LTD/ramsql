"""Stock PostgreSQL clients, TLS enforcement, SCRAM and WAL reopen."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import struct
import subprocess
import time

import psycopg
from test_auth import challenge, finish, frame, make_verifier, proof_keys, raw_final, receive
from test_persistence import DurableServer

SSL_REQUEST = bytes.fromhex("0000000804d2162f")
GSS_REQUEST = bytes.fromhex("0000000804d21630")
STARTUP_BODY = struct.pack("!I", 196608) + b"user\0ramsql\0database\0ramsql\0\0"
STARTUP = struct.pack("!I", 4 + len(STARTUP_BODY)) + STARTUP_BODY


def identity(w):
    def openssl(*args):
        return subprocess.run(["openssl", *args], check=True, capture_output=True, timeout=20).stdout
    ca, ca_key, leaf, leaf_key, csr = (w / name for name in ("ca.pem", "ca.key", "leaf.pem", "leaf.key", "leaf.csr"))
    cert, key, ext = (w / name for name in ("leaf.der", "leaf.scalar", "extensions"))
    curve = ("-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes")
    openssl("req", "-x509", *curve, "-days", "1", "-subj", "/CN=RAMSQL TLS CA", "-keyout", ca_key,
            "-out", ca, "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign")
    openssl("req", "-new", *curve, "-subj", "/CN=localhost", "-keyout", leaf_key, "-out", csr)
    ext.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                   "extendedKeyUsage=serverAuth\nsubjectAltName=DNS:localhost\n")
    openssl("x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key, "-set_serial", "1",
            "-days", "1", "-extfile", ext, "-out", leaf)
    openssl("x509", "-in", leaf, "-outform", "DER", "-out", cert)
    encoded = openssl("ec", "-in", leaf_key, "-noout", "-text")
    raw = bytes.fromhex(re.search(rb"priv:\s*([0-9a-f:\s]+?)\s*pub:", encoded, re.I)[1].decode().replace(":", " "))
    assert len(raw) == 32
    key.write_bytes(raw)
    key.chmod(0o600)
    return ca, cert, key


class TlsServer(DurableServer):
    def command(self):
        args = super().command()
        args.remove("--insecure-local")
        return args


def expect_close(port, request):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
        client.sendall(request)
        try:
            response = client.recv(1)
        except ConnectionResetError:
            response = b""
        assert response == b"", response


def fragmented_ssl_request(port, ca, gss_first=False):
    context = ssl.create_default_context(cafile=str(ca))
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        if gss_first:
            raw.sendall(GSS_REQUEST)
            assert raw.recv(1) == b"N"
        raw.sendall(SSL_REQUEST[:3])
        raw.settimeout(0.1)
        try:
            early = raw.recv(1)
        except TimeoutError:
            pass
        else:
            raise AssertionError(f"premature SSL response: {early!r}")
        finally:
            raw.settimeout(5)
        raw.sendall(SSL_REQUEST[3:])
        assert raw.recv(1) == b"S"
        with context.wrap_socket(raw, server_hostname="localhost") as encrypted:
            encrypted.sendall(STARTUP)
            tag, payload = receive(encrypted)
            assert tag == b"R" and struct.unpack("!I", payload[:4]) == (10,)
            assert payload[4:] == b"SCRAM-SHA-256-PLUS\0SCRAM-SHA-256\0\0", payload


def key_update_query(port, ca, password, salt):
    """Exercise a real OpenSSL client KeyUpdate on an authenticated worker socket."""
    lib = C.CDLL(ctypes.util.find_library("ssl"))
    ptr, int32, string = C.c_void_p, C.c_int, C.c_char_p
    signatures = {
        "TLS_client_method": (ptr,),
        "SSL_CTX_new": (ptr, ptr),
        "SSL_CTX_free": (None, ptr),
        "SSL_CTX_load_verify_locations": (int32, ptr, string, string),
        "SSL_CTX_set_verify": (None, ptr, int32, ptr),
        "SSL_new": (ptr, ptr),
        "SSL_free": (None, ptr),
        "SSL_set_fd": (int32, ptr, int32),
        "SSL_set1_host": (int32, ptr, string),
        "SSL_connect": (int32, ptr),
        "SSL_get_verify_result": (C.c_long, ptr),
        "SSL_read": (int32, ptr, ptr, int32),
        "SSL_write": (int32, ptr, string, int32),
        "SSL_key_update": (int32, ptr, int32),
        "SSL_do_handshake": (int32, ptr),
    }
    for name, (result, *parameters) in signatures.items():
        function = getattr(lib, name)
        function.restype = result
        function.argtypes = parameters

    class Encrypted:
        def sendall(self, data):
            assert lib.SSL_write(client, data, len(data)) == len(data)
        def recv(self, size):
            buffer = C.create_string_buffer(size)
            received = lib.SSL_read(client, buffer, size)
            assert received > 0, (size, received)
            return buffer.raw[:received]

    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        raw.sendall(SSL_REQUEST)
        assert raw.recv(1) == b"S"
        raw.setblocking(True)
        for option in (socket.SO_RCVTIMEO, socket.SO_SNDTIMEO):
            raw.setsockopt(socket.SOL_SOCKET, option, struct.pack("@ll", 5, 0))
        context = lib.SSL_CTX_new(lib.TLS_client_method())
        assert context
        try:
            assert lib.SSL_CTX_load_verify_locations(context, os.fsencode(ca), None) == 1
            lib.SSL_CTX_set_verify(context, 1, None)
            client = lib.SSL_new(context)
            assert client
            try:
                assert lib.SSL_set_fd(client, raw.fileno()) == 1
                assert lib.SSL_set1_host(client, b"localhost") == 1
                assert lib.SSL_connect(client) == 1 and lib.SSL_get_verify_result(client) == 0
                encrypted = Encrypted()
                encrypted.sendall(STARTUP)
                tag, mechanisms = receive(encrypted)
                assert tag == b"R" and b"SCRAM-SHA-256-PLUS" in mechanisms
                bare, server_first, fields, _ = challenge(encrypted)
                keys = proof_keys(password.encode(), salt, 4096)
                final, wanted_signature = raw_final(bare, server_first, fields, keys)
                finish(encrypted, final, wanted_signature)
                assert lib.SSL_key_update(client, 1) == 1
                assert lib.SSL_do_handshake(client) == 1
                encrypted.sendall(frame(b"Q", b"SELECT 27\0"))
                assert receive(encrypted)[0] == b"T"
                assert receive(encrypted) == (b"D", struct.pack("!HI", 1, 2) + b"27")
                assert receive(encrypted) == (b"C", b"SELECT 1\0")
                assert receive(encrypted) == (b"Z", b"I")
                encrypted.sendall(frame(b"X"))
            finally:
                lib.SSL_free(client)
        finally:
            lib.SSL_CTX_free(context)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "scratch", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=[2, 4], required=True)
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    work = args.scratch.resolve()
    work.mkdir(mode=0o700)
    ca, cert, key = identity(work)
    password = secrets.token_urlsafe(24)
    verifier = work / "ramsql.verifier"
    salt = secrets.token_bytes(32)
    verifier.write_bytes(make_verifier(password.encode(), salt) + b"\n")
    verifier.chmod(0o600)
    options = dict(cwd=work, storage_options=("--workers", str(args.workers), "--max-clients", "4", "--auth-user", "ramsql",
                   "--auth-verifier-file", str(verifier), "--tls-cert-der", str(cert), "--tls-key-raw", str(key)))
    wal = work / "database.wal"
    evidence = dict(source=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                    psql=subprocess.check_output(["psql", "--version"], text=True).strip(),
                    libpq=psycopg.pq.version(), workers=args.workers, checks=[])
    def record(check):
        evidence["checks"].append(check)
        args.report.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    def settings(port, **overrides):
        return dict(host="localhost", hostaddr="127.0.0.1", port=port, user="ramsql", dbname="ramsql",
                    sslmode="verify-full", sslrootcert=str(ca), gssencmode="disable",
                    channel_binding="require", connect_timeout=5) | overrides

    def psql(port, source, **overrides):
        command = ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-w", "-qAt", "--dbname",
                   psycopg.conninfo.make_conninfo(**settings(port, **overrides))]
        result = subprocess.run(command, input=source, text=True, capture_output=True, timeout=20,
                                env=dict(os.environ, PGPASSWORD=password))
        assert result.returncode == 0, result.stderr
        return result.stdout

    def rejected(port, passphrase, message, **overrides):
        try:
            with psycopg.connect(password=passphrase, **settings(port, **overrides)):
                pass
        except psycopg.OperationalError as exc:
            assert message in str(exc).lower(), str(exc)
        else:
            raise AssertionError("unexpected authentication")

    with TlsServer(binary, wal, create=True, **options) as server:
        port = server.port
        expect_close(port, STARTUP)
        expect_close(port, bytes.fromhex("0000001004d2162e0000000100000000"))
        expect_close(port, bytes.fromhex("1603010003010000"))
        fragmented_ssl_request(port, ca)
        fragmented_ssl_request(port, ca, gss_first=True)
        for forbidden in (STARTUP, GSS_REQUEST):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
                raw.sendall(GSS_REQUEST)
                assert raw.recv(1) == b"N"
                raw.sendall(forbidden)
                try:
                    observed = raw.recv(1)
                except ConnectionResetError:
                    observed = b""
                assert observed == b"", observed
        record("ssl_request_required_and_fragmented")
        record("gss_refusal_requires_ssl_and_cannot_repeat")
        rejected(port, password, "certificate", host="wrong.example")
        rejected(port, "wrong", "auth")
        assert psql(port, "SELECT 9;\n", channel_binding="disable") == "9\n"
        record("verify_full_hostname_scram_plus_required_and_scram_compatible")

        result = psql(port, "\\conninfo\nCREATE TABLE secure_data(id int PRIMARY KEY, v text);\n"
                           "COPY secure_data FROM STDIN;\n1\tfirst\n2\tsecond\n\\.\n"
                           "COPY (SELECT id,v FROM secure_data ORDER BY id) TO STDOUT;\n")
        assert "TLSv1.3" in result and result.endswith("1\tfirst\n2\tsecond\n"), result
        record("stock_psql_tls13_copy_both_directions")
        key_update_query(port, ca, password, salt)
        record("worker_tls_key_update_with_authenticated_sql")

        with psycopg.connect(password=password, autocommit=True, **settings(port)) as session:
            for identifier, row in ((2, "second"), (1, "first")):
                assert session.execute("SELECT v FROM secure_data WHERE id = %s", (identifier,),
                                       prepare=True).fetchone() == (row,)
            with psycopg.connect(password=password, autocommit=True, **settings(port)) as blocker:
                blocker.execute("BEGIN")
                blocker.execute("UPDATE secure_data SET v = 'held' WHERE id = 1")
                with ThreadPoolExecutor(max_workers=1) as executor:
                    parked = executor.submit(session.execute, "UPDATE secure_data SET v = 'canceled' WHERE id = 1")
                    try:
                        time.sleep(0.5)
                        assert not parked.done(), "the competing UPDATE did not wait"
                        session.cancel_safe(timeout=6)
                        try:
                            parked.result(timeout=8)
                        except psycopg.errors.QueryCanceled:
                            pass
                        else:
                            raise AssertionError("encrypted libpq cancellation did not cancel the blocked UPDATE")
                    finally:
                        blocker.execute("ROLLBACK")
            assert session.execute("SELECT 7").fetchone() == (7,)
        record("extended_prepared_sql_and_encrypted_libpq_cancel")

        with ExitStack() as stack:
            sessions = [stack.enter_context(psycopg.connect(password=password, **settings(port))) for _ in range(4)]
            pids = [session.info.backend_pid for session in sessions]
            assert len(set(pids)) == 4 and len({(pid - 1) % args.workers for pid in pids}) == args.workers, pids
            def query(session):
                return session.execute("SELECT id,v FROM secure_data ORDER BY id", prepare=True).fetchall()
            with ThreadPoolExecutor(max_workers=4) as executor:
                queries = [executor.submit(query, session) for session in sessions]
                assert all(task.result(timeout=5) == [(1, "first"), (2, "second")] for task in queries)
            record("simultaneous_prepared_round_trips_on_every_worker")
            expect_close(port, SSL_REQUEST)
        record("overload_closes_without_plaintext_response")

    evidence["wal_bytes"] = wal.stat().st_size
    assert evidence["wal_bytes"] > 0
    with TlsServer(binary, wal, **options) as reopened:
        assert psql(reopened.port, "SELECT id,v FROM secure_data ORDER BY id;\n") == "1|first\n2|second\n"
    record("durable_process_reopen")
    with DurableServer(binary, work / "plain.wal", create=True, cwd=work,
                       storage_options=("--workers", "1")) as plain:
        assert psql(plain.port, "SELECT 17;\n", sslmode="disable", channel_binding="disable") == "17\n"
    record("existing_plaintext_listener_when_unconfigured")
    evidence["passed"] = True
    args.report.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
