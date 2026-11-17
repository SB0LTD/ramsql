"""Create a PostgreSQL SCRAM verifier or combine existing verifiers for RAMSQL."""
import argparse
import base64
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sys


def verifier_profile(verifier):
    match = re.fullmatch(rb'SCRAM-SHA-256\$([0-9]{1,8}):([^:$]+)\$([^:$]+):([^:$]+)', verifier)
    if not match:
        raise ValueError('invalid verifier format')
    rounds = int(match[1])
    encoded = match.groups()[1:]
    salt, stored, server = (base64.b64decode(value, validate=True) for value in encoded)
    if tuple(base64.b64encode(value) for value in (salt, stored, server)) != encoded:
        raise ValueError('noncanonical verifier encoding')
    if not 4096 <= rounds <= 10_000_000 or not 16 <= len(salt) <= 64 or len(stored) != 32 or len(server) != 32:
        raise ValueError('verifier parameter out of range')
    return rounds, len(salt)


def credential_list(entries, parser):
    records = [b'RAMSQL-SCRAM-V1\t' + secrets.token_hex(32).encode('ascii')]
    seen = set()
    profile = None
    for user, filename in entries:
        identity = user.encode('utf-8')
        if not 1 <= len(identity) <= 63 or any(c in identity for c in b'\0\t\r\n') or identity in seen:
            parser.error('credentials require distinct PostgreSQL usernames of 1..63 UTF-8 bytes')
        seen.add(identity)
        with Path(filename).open('rb') as file:
            raw = file.read(513)
        verifier = raw.rstrip(b'\r\n')
        try:
            if len(raw) > 512:
                raise ValueError('verifier file exceeds 512 bytes')
            entry_profile = verifier_profile(verifier)
        except ValueError:
            parser.error('each input file must contain one PostgreSQL SCRAM verifier')
        if profile is not None and entry_profile != profile:
            parser.error('all verifiers must have the same salt length and iteration count')
        profile = entry_profile
        records.append(identity + b'\t' + verifier)
    return b'\n'.join(records) + b'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--password-stdin', action='store_true', help='explicitly read one password line from stdin for automation')
    mode.add_argument('--credential', action='append', nargs=2, metavar=('USER', 'VERIFIER_FILE'),
                      help='combine existing verifiers into a catalog credential file; repeat per role')
    args = parser.parse_args()
    if args.credential:
        contents = credential_list(args.credential, parser)
    else:
        contents = create_verifier(args, parser)
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error('output already exists; choose a new path')
    with os.fdopen(descriptor, 'wb') as file:
        file.write(contents)
        file.flush()
        os.fsync(file.fileno())
    print(('Credentials file' if args.credential else 'Verifier') + ' created. Protect this file with filesystem permissions/ACLs.')


def create_verifier(args, parser):
    if args.password_stdin:
        password = sys.stdin.readline().rstrip('\r\n')
    else:
        if not sys.stdin.isatty():
            parser.error('interactive terminal required; automation must explicitly use --password-stdin')
        password = getpass.getpass('New database password: ')
        if password != getpass.getpass('Confirm password: '):
            parser.error('passwords do not match')
    # Restrict this helper to unambiguous SASLprep input. Existing PostgreSQL
    # verifiers may be provisioned separately for other password encodings.
    if not password or len(password) > 1024 or any(ord(c) < 32 or ord(c) > 126 for c in password):
        parser.error('use 1..1024 printable ASCII characters; provision other passwords with PostgreSQL tooling')
    salt = secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac('sha256', password.encode('ascii'), salt, 4096)
    stored = hashlib.sha256(hmac.digest(salted, b'Client Key', 'sha256')).digest()
    server = hmac.digest(salted, b'Server Key', 'sha256')
    b64 = lambda value: base64.b64encode(value).decode('ascii')
    return f'SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}\n'.encode('ascii')


if __name__ == '__main__':
    main()
