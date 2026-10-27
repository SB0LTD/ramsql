"""Exercise the built RAMSQL command line and its bounded stdin protocol."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from release_matrix import source_version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default_binary = Path(__file__).resolve().parents[1] / "sig-out" / "bin"
    default_binary /= "ramsql.exe" if os.name == "nt" else "ramsql"
    parser.add_argument("--binary", type=Path, default=default_binary)
    args = parser.parse_args()
    binary = args.binary.resolve()

    def run(arguments=(), source=None):
        return subprocess.run(
            [str(binary), *arguments],
            input=None if source is None else source.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            check=False,
        )

    def expect(label, result, status=0, stdout=b"", stderr=b""):
        actual = (result.returncode, result.stdout, result.stderr)
        expected = (status, stdout, stderr)
        assert actual == expected, f"{label}: expected {expected!r}, got {actual!r}"

    expect("version", run(["--version"]), stdout=("ramsql " + source_version() + "\n").encode())
    help_result = run(["--help"])
    assert help_result.returncode == 0 and not help_result.stderr, help_result
    assert b"ramsql" in help_result.stdout and b"--stdin" in help_result.stdout
    assert b"--version" in help_result.stdout
    described = run(["--capabilities", "--rows", "1024", "--versions", "3072"])
    assert described.returncode == 0 and not described.stderr, described
    caps = json.loads(described.stdout)
    assert caps['version'] == source_version() and caps['production_ready'] is False
    assert caps['rows_per_table'] == 1024 and caps['versions_per_table'] == 3072
    assert caps['backing_bytes'] > 0 and caps['columns'] > 0 and caps['result_rows'] > 0
    assert caps['tls'] is False and caps['bind'] == '127.0.0.1'
    assert caps['sql_execution_workers']==1 and caps['exclusive_sql_workers']==1
    assert caps['parallel_simple_reads'] is False
    with tempfile.TemporaryDirectory(prefix='ramsql-capabilities-') as directory:
        wal = Path(directory) / 'must-not-be-created.wal'
        inspected = subprocess.run([str(binary), '--capabilities', '--data', str(wal)],
                                  capture_output=True, timeout=15)
        assert inspected.returncode == 0 and not wal.exists(), inspected
    for arguments in (["--unknown"], ["--version", "extra"], ["--stdin", "--unknown"]):
        unknown = run(arguments, "")
        assert unknown.returncode == 2, (arguments, unknown.returncode, unknown.stderr)

    for invalid in (["--port"], ["--port", "65536"], ["--protocol", "oracle"], ["--max-clients", "0"], ["--max-clients", "61"], ["--idle-ms", "0"], ["--rows"], ["--rows", "0"], ["--rows", "18446744073709551615"], ["--versions"], ["--versions", "1"], ["--max-query-work"], ["--max-query-work", "-1"], ["--max-query-work", "18446744073709551616"], ["--demo", "--port", "0"]):
        bad = run(invalid)
        assert bad.returncode == 2, (invalid, bad.returncode, bad.stderr)
    demo = run(["--demo"])
    assert demo.returncode == 0 and demo.stdout and not demo.stderr, demo
    expect("explicit demo", run(["--demo"]), stdout=demo.stdout)
    expect("empty stream", run(["--stdin"], "\n \t\r\n\n"))

    statements = "\n".join([
        "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, active BOOLEAN);",
        "INSERT INTO items VALUES (2, 'beta', false);",
        "INSERT INTO items VALUES (1, 'alpha', true);",
        "SELECT id, name, active FROM items ORDER BY id;",
        "UPDATE items SET name = 'updated' WHERE id = 2;",
        "DELETE FROM items WHERE id = 1;",
        "SELECT id, name, active FROM items;",
        "DROP TABLE items;",
        "",
    ])
    expect(
        "database persists across statements",
        run(["--stdin"], statements),
        stdout=(
            b"OK 0\nOK 1\nOK 1\nid\tname\tactive\n"
            b"1\talpha\tTRUE\n2\tbeta\tFALSE\n"
            b"OK 1\nOK 1\nid\tname\tactive\n2\tupdated\tFALSE\nOK 0\n"
        ),
    )

    expect(
        "errors preserve rows and stream continues",
        run(["--stdin"], "\n".join([
            "CREATE TABLE items (id INTEGER PRIMARY KEY);",
            "INSERT INTO items VALUES (1);",
            "INSERT INTO items VALUES (1);",
            "SELECT * FROM missing;",
            "INSERT INTO items VALUES (2);",
            "SELECT * FROM items ORDER BY id;",
            "",
        ])),
        status=1,
        stdout=b"OK 0\nOK 1\nOK 1\nid\n1\n2\n",
        stderr=b"error: PrimaryKeyViolation\nerror: TableNotFound\n",
    )

    expect(
        "UTF-8, escaped quotes and NULL cells",
        run(["--stdin"], "\n".join([
            "CREATE TABLE notes (value TEXT, optional TEXT);",
            "INSERT INTO notes VALUES ('café שלום 🌍''s', NULL);",
            "SELECT * FROM notes;",
            "",
        ])),
        stdout="OK 0\nOK 1\nvalue\toptional\ncafé שלום 🌍's\tNULL\n".encode("utf-8"),
    )

    expect(
        "text cells escape TSV controls and backslashes",
        run(["--stdin"], "\n".join([
            "CREATE TABLE notes (value TEXT);",
            "INSERT INTO notes VALUES ('left\tmiddle\rright\\tail');",
            "SELECT * FROM notes;",
            "",
        ])),
        stdout=b"OK 0\nOK 1\nvalue\nleft\\tmiddle\\rright\\\\tail\n",
    )

    # A complete SQL prefix followed by excessive whitespace must never run.
    # The next line verifies both that the row was not inserted and that line
    # processing resumed after discarding the entire oversized statement.
    expect(
        "oversized line is discarded atomically",
        run(["--stdin"], "\n".join([
            "CREATE TABLE items (id INTEGER);",
            "INSERT INTO items VALUES (7);" + " " * 4096,
            "SELECT * FROM items;",
            "INSERT INTO items VALUES (8);",
            "SELECT * FROM items;",
            "",
        ])),
        status=1,
        stdout=b"OK 0\nid\nOK 1\nid\n8\n",
        stderr=b"error: StatementTooLong\n",
    )

    expect(
        "unterminated final statement",
        run(["--stdin"], "CREATE TABLE items (id INTEGER);\r\nSELECT * FROM items;"),
        stdout=b"OK 0\nid\n",
    )

    create = "CREATE TABLE items (id INTEGER);"
    expect(
        "line at the fixed capacity",
        run(["--stdin"], create.ljust(4096) + "\nSELECT * FROM items;\n"),
        stdout=b"OK 0\nid\n",
    )
    expect(
        "oversized unterminated line",
        run(["--stdin"], create.ljust(4097)),
        status=1,
        stderr=b"error: StatementTooLong\n",
    )
    print("PASS RAMSQL CLI: arguments, demo, SQL stream, error recovery, bounded lines and EOF")


if __name__ == "__main__":
    main()
