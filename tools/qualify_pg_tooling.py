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


def describe_queries(result):
    return re.findall(r"^/\*+ QUERY \*+/\s*\n(.*?)\n/\*{5,}/$", result.stdout + "\n" + result.stderr, re.M | re.S)


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
    lookup, relation_sql, *_ = describe_queries(native_describe)
    assert "pg_table_is_visible" in lookup and "OPERATOR(pg_catalog.~)" in lookup and "COLLATE pg_catalog.default" in lookup
    assert "pg_catalog.pg_am" in relation_sql and "::pg_catalog.regtype::pg_catalog.text" in relation_sql
    (EVIDENCE / "discovery.sql").write_text(lookup + "\n")
    native_rows = psql(native, native_conn, "native-discovery", "-At", "-c", lookup)
    check(native_rows)
    assert re.fullmatch(r"[1-9]\d*\|public\|tooling\n", native_rows.stdout), native_rows.stdout
    native_relation = psql(native, native_conn, "native-relation", "-At", "-c", relation_sql)
    check(native_relation)
    assert native_relation.stdout == "0|r|t|f|f|f|f|f|f||0||p|d|heap\n", native_relation.stdout
    Server.test_workers = 2
    with Server(Path(sys.argv[1]).resolve()) as server:
        client = ["docker", "run", "--rm", "--network", "host", IMAGE]
        conn = ["-h", "127.0.0.1", "-p", str(server.port), "-U", "ramsql", "-d", "ramsql"]
        check(psql(client, conn, "ramsql-setup", "-c", SETUP))
        actual = psql(client, conn, "ramsql-describe", "-E", "-c", r"\d tooling")
        actual_lookup, actual_relation_sql, *_ = describe_queries(actual)
        assert actual_lookup == lookup
        rows = psql(client, conn, "ramsql-discovery", "-At", "-c", lookup)
        check(rows)
        assert re.fullmatch(r"[1-9]\d*\|public\|tooling\n", rows.stdout), rows.stdout
        old_oid = native_rows.stdout.split("|", 1)[0]
        new_oid = rows.stdout.split("|", 1)[0]
        assert actual_relation_sql == relation_sql.replace(f"c.oid = '{old_oid}'", f"c.oid = '{new_oid}'")
        actual_relation = psql(client, conn, "ramsql-relation", "-At", "-c", actual_relation_sql)
        check(actual_relation)
        assert actual_relation.stdout == native_relation.stdout, (native_relation.stdout, actual_relation.stdout)
        (EVIDENCE / "relation-metadata.sql").write_text(actual_relation_sql + "\n")
        print("PASS native psql 18.3 discovery SQL on both engines")
        print("PASS native psql 18.3 relation-metadata SQL and values on both engines")
        print(f"psql describe exit status: native={native_describe.returncode} RAMSQL={actual.returncode}")
        print(actual.stderr, end="")
        if not actual.returncode:
            assert "tooling" in actual.stdout
            assert "integer" in actual.stdout
            assert "label" in actual.stdout


if __name__ == "__main__":
    main()
