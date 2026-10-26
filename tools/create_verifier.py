"""Create a PostgreSQL SCRAM verifier; never accept a password in argv."""
import argparse
import base64
import getpass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--password-stdin', action='store_true', help='explicitly read one password line from stdin for automation')
    args = parser.parse_args()
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
    verifier = f'SCRAM-SHA-256$4096:{b64(salt)}${b64(stored)}:{b64(server)}\n'
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        parser.error('output already exists; choose a new path')
    with os.fdopen(descriptor, 'w', encoding='ascii', newline='\n') as file:
        file.write(verifier)
        file.flush()
        os.fsync(file.fileno())
    print('Verifier created. Protect this file with filesystem permissions/ACLs.')


if __name__ == '__main__':
    main()
