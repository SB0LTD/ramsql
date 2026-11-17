from pathlib import Path
import re
import subprocess
import sys

from test_server import Server

IMAGE = "postgres:18.3-bookworm@sha256:80630f83606d8db77d30b3851b16a9f78be2d0d4dda6f7b82a1fdca5ebe3acba"
EVIDENCE = Path("evidence")
SETUP = "CREATE TABLE tooling(id INT PRIMARY KEY, label TEXT)"


def psql(container_args, connection, label, *args):
    command = [*container_args, "psql", "-X", "-v", "ON_ERROR_STOP=1", *connection, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=90)
    (EVIDENCE / f"{label}.out").write_text(result.stdout)
    (EVIDENCE / f"{label}.err").write_text(result.stderr)
    return result


def discovery(result):
    blocks = re.findall(r"\*+ QUERY \*+\s*(.*?)\n\*{5,}", result.stdout + "\n" + result.stderr, re.S)
    return next(sql for sql in blocks if "pg_table_is_visible" in sql
                and "OPERATOR(pg_catalog.~)" in sql and "COLLATE pg_catalog.default" in sql)


def check(result):
    if result.returncode:
        raise AssertionError(result.stderr)


def main():
    native = ["docker", "exec", "tooling-native"]
    native_conn = ["-U", "postgres", "-d", "postgres"]
    check(psql(native, native_conn, "native-version", "--version"))
    check(psql(native, native_conn, "native-setup", "-c", SETUP))
    native_describe = psql(native, native_conn, "native-describe", "-E", "-c", r"\d tooling")
    check(native_describe)
    lookup = discovery(native_describe)
    (EVIDENCE / "discovery.sql").write_text(lookup + "\n")
    native_rows = psql(native, native_conn, "native-discovery", "-At", "-c", lookup)
    check(native_rows)
    assert re.fullmatch(r"[1-9]\d*\|public\|tooling\n", native_rows.stdout), native_rows.stdout
    Server.test_workers = 2
    with Server(Path(sys.argv[1]).resolve()) as server:
        client = ["docker", "run", "--rm", "--network", "host", IMAGE]
        conn = ["-h", "127.0.0.1", "-p", str(server.port), "-U", "ramsql", "-d", "ramsql"]
        check(psql(client, conn, "ramsql-setup", "-c", SETUP))
        actual = psql(client, conn, "ramsql-describe", "-E", "-c", r"\d tooling")
        assert discovery(actual) == lookup
        rows = psql(client, conn, "ramsql-discovery", "-At", "-c", lookup)
        check(rows)
        assert re.fullmatch(r"[1-9]\d*\|public\|tooling\n", rows.stdout), rows.stdout
        print("PASS native psql 18.3 discovery SQL on both engines")
        print(f"psql describe exit status: native={native_describe.returncode} RAMSQL={actual.returncode}")
        print(actual.stderr, end="")
        if not actual.returncode:
            assert "tooling" in actual.stdout
            assert "integer" in actual.stdout
            assert "label" in actual.stdout


if __name__ == "__main__":
    main()
