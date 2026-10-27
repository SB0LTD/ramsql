"""Independent live SCRAM authentication and fail-closed startup contracts.

Python derives PostgreSQL verifiers and SCRAM proofs independently of Sig.
psycopg verifies real libpq interoperability. This suite deliberately requests
unencrypted loopback connections; TLS remains a separate production gate.
"""

import argparse
import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import re
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
test_workers = 1
sys.path.insert(0, str(ROOT / ".sig-cache" / "client-deps"))
U32 = struct.Struct("!I")
assertions = 0


def check(ok, message="authentication contract failed"):
    global assertions
    assertions += 1
    if not ok:
        raise AssertionError(message)


def frame(tag, payload=b""):
    return tag + U32.pack(len(payload) + 4) + payload


def exact(sock, size):
    data = bytearray()
    while len(data) < size:
        received = sock.recv(size - len(data))
        check(bool(received), "connection ended before the complete frame")
        data += received
    return bytes(data)


def receive(sock):
    header = exact(sock, 5)
    length = U32.unpack(header[1:])[0]
    check(4 <= length <= 65536, "invalid authentication frame length")
    return header[:1], exact(sock, length - 4)


def startup(user="postgres"):
    payload = U32.pack(196608) + b"user\0" + user.encode() + b"\0database\0postgres\0client_encoding\0UTF8\0\0"
    return U32.pack(len(payload) + 4) + payload


def proof_keys(password, salt, rounds):
    salted = hashlib.pbkdf2_hmac("sha256", password, salt, rounds)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    return client_key, hashlib.sha256(client_key).digest(), hmac.digest(salted, b"Server Key", "sha256")


def make_verifier(password, salt, rounds=4096):
    _, stored_key, server_key = proof_keys(password, salt, rounds)
    b64 = base64.b64encode
    return b"SCRAM-SHA-256$" + str(rounds).encode() + b":" + b64(salt) + b"$" + b64(stored_key) + b":" + b64(server_key)


@contextmanager
def running_server(binary, verifier):
    process = subprocess.Popen(
        [str(binary), "--memory", "--auth-user", "postgres", "--auth-verifier-file", str(verifier), "--port", "0", "--workers", str(test_workers)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    lines = queue.Queue()
    threading.Thread(target=lambda: lines.put(process.stdout.readline()), daemon=True).start()
    errors = []

    def collect_errors():
        for line in process.stderr:
            errors.append(line)

    error_reader = threading.Thread(target=collect_errors, daemon=True)
    error_reader.start()
    try:
        line = lines.get(timeout=15)
        match = re.fullmatch(r"RAMSQL postgres 127\.0\.0\.1:(\d+)\n", line)
        check(match is not None, "authenticated server did not start")
        yield int(match.group(1))
        check(process.poll() is None, "authenticated server crashed")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stdout.close()
        error_reader.join(timeout=2)
        process.stderr.close()
        check(not errors, "server logged an unexpected error during authentication")


def connect_raw(port, username="postgres"):
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.settimeout(5)
    sock.sendall(startup(username))
    tag, body = receive(sock)
    check(tag == b"R" and body == U32.pack(10) + b"SCRAM-SHA-256\0\0", "server did not require SCRAM-SHA-256")
    return sock


def challenge(sock):
    # Reusing the exact same client nonce reveals server nonce reuse.
    client_nonce = b"fixed-client-nonce-for-uniqueness-test"
    bare = b"n=ignored-by-postgres,r=" + client_nonce
    first = b"n,," + bare
    sock.sendall(frame(b"p", b"SCRAM-SHA-256\0" + U32.pack(len(first)) + first))
    tag, body = receive(sock)
    check(tag == b"R" and body[:4] == U32.pack(11), "missing SCRAM server-first challenge")
    server_first = body[4:]
    fields = dict(part.split(b"=", 1) for part in server_first.split(b","))
    check(set(fields) == {b"r", b"s", b"i"})
    check(fields[b"r"].startswith(client_nonce))
    server_nonce = fields[b"r"][len(client_nonce):]
    check(len(base64.b64decode(server_nonce, validate=True)) >= 18, "insufficient server nonce size")
    return bare, server_first, fields, server_nonce


def raw_final(bare, server_first, fields, keys):
    client_key, stored_key, server_key = keys
    final_without_proof = b"c=biws,r=" + fields[b"r"]
    auth_message = bare + b"," + server_first + b"," + final_without_proof
    client_signature = hmac.digest(stored_key, auth_message, "sha256")
    proof = bytes(a ^ b for a, b in zip(client_key, client_signature))
    return final_without_proof + b",p=" + base64.b64encode(proof), b"v=" + base64.b64encode(hmac.digest(server_key, auth_message, "sha256"))


def finish(sock, final, wanted_signature):
    sock.sendall(frame(b"p", final))
    check(receive(sock) == (b"R", U32.pack(12) + wanted_signature), "SCRAM server signature mismatch")
    check(receive(sock) == (b"R", U32.pack(0)), "missing AuthenticationOk after proof verification")
    while True:
        tag, body = receive(sock)
        check(tag in (b"S", b"K", b"Z"), "unexpected post-authentication frame")
        if tag == b"Z":
            check(body == b"I")
            break


def rejected(sock):
    tag, body = receive(sock)
    check(tag == b"E" and b"C28P01\0" in body and b"SFATAL\0" in body, "authentication did not fail with FATAL 28P01")
    check(sock.recv(1) == b"", "failed authentication left the connection open")


def raw_contracts(port, password, salt):
    keys = proof_keys(password, salt, 4096)
    nonces = set()
    for _ in range(128):
        with connect_raw(port) as sock:
            bare, first, fields, nonce = challenge(sock)
            check(base64.b64decode(fields[b"s"], validate=True) == salt and int(fields[b"i"]) == 4096)
            check(nonce not in nonces, "server reused a SCRAM nonce across connections")
            nonces.add(nonce)
            final, wanted_signature = raw_final(bare, first, fields, keys)
            finish(sock, final, wanted_signature)
            sock.sendall(frame(b"X"))
    # A captured, valid proof must fail against the next connection's challenge.
    with connect_raw(port) as sock:
        _, _, _, fresh_nonce = challenge(sock)
        check(fresh_nonce not in nonces)
        sock.sendall(frame(b"p", final))
        rejected(sock)
    # SQL never reaches execution while the authentication state is incomplete.
    with connect_raw(port) as sock:
        sock.sendall(frame(b"Q", b"DROP TABLE auth_contract\0"))
        rejected(sock)
    with connect_raw(port) as sock:
        sock.sendall(frame(b"p", b"PLAIN\0" + U32.pack(0)))
        rejected(sock)


def psycopg_contracts(port, password):
    import psycopg

    options = dict(host="127.0.0.1", port=port, user="postgres", dbname="postgres", password=password.decode(), sslmode="disable", connect_timeout=5, autocommit=True)
    with psycopg.connect(**options) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TABLE auth_contract (id INTEGER PRIMARY KEY, label TEXT)")
            cursor.execute("INSERT INTO auth_contract VALUES (%s, %s)", (7, "verified"))
            for _ in range(8):
                cursor.execute("SELECT id, label FROM auth_contract WHERE id = %s", (7,), prepare=True)
                check(cursor.fetchone() == (7, "verified"))
    for changed in ({"password": "wrong-password"}, {"user": "unknown-user"}, {"password": ""}):
        try:
            connection = psycopg.connect(**(options | changed))
        except psycopg.OperationalError:
            check(True)
        else:
            connection.close()
            raise AssertionError("psycopg accepted invalid credentials")
    # This explicitly exposes the missing TLS gate instead of advertising it.
    try:
        connection = psycopg.connect(**(options | {"sslmode": "require"}))
    except psycopg.OperationalError:
        check(True)
    else:
        connection.close()
        raise AssertionError("TLS-required client unexpectedly connected")


def startup_contracts(binary, directory):
    def fails(arguments, wanted):
        result = subprocess.run([str(binary), "--memory", "--port", "0", *arguments], capture_output=True, timeout=10)
        check(result.returncode != 0 and not result.stdout and wanted in result.stderr, "unsafe server startup was accepted")

    fails([], b"AuthenticationRequired")
    fails(["--auth-user", "postgres"], b"AuthenticationRequired")
    invalid = directory / "invalid.verifier"
    for content in (b"", b"not-a-postgresql-verifier", b"SCRAM-SHA-256$4096:bad$bad:bad", b"x" * 513):
        invalid.write_bytes(content)
        fails(["--auth-verifier-file", str(invalid)], b"InvalidVerifier")
    invalid.write_bytes(make_verifier(b"test-only-password", b"test-salt-16byte!", 4095))
    fails(["--auth-verifier-file", str(invalid)], b"InvalidVerifier")
    fails(["--insecure-local", "--auth-verifier-file", str(invalid)], b"InvalidServerOptions")


def main():
    global test_workers
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--workers",type=int,default=1)
    args = parser.parse_args()
    test_workers=args.workers
    binary = args.binary.resolve()
    with tempfile.TemporaryDirectory(prefix="ramsql-auth-") as temporary:
        directory = Path(temporary)
        password = secrets.token_urlsafe(32).encode()
        salt = secrets.token_bytes(32)
        verifier = directory / "postgres.verifier"
        verifier.write_bytes(make_verifier(password, salt) + b"\n")
        verifier.chmod(0o600)
        startup_contracts(binary, directory)
        with running_server(binary, verifier) as port:
            psycopg_contracts(port, password)
            raw_contracts(port, password, salt)
    print(json.dumps({"suite": "auth-live", "pass": True, "assertions": assertions, "unique_authenticated_nonces": 128, "clients": ["psycopg", "independent Python SCRAM"], "tls_supported": False}))


if __name__ == "__main__":
    main()
