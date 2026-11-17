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
    parser.add_argument("--expect-integral-version-banks", action="store_true")
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
    assert b"--version" in help_result.stdout and b"--create-data" in help_result.stdout
    described = run(["--capabilities", "--rows", "1024", "--versions", "3072", "--workers", "1"])
    assert described.returncode == 0 and not described.stderr, described
    caps = json.loads(described.stdout)
    assert caps['version'] == source_version() and caps['production_ready'] is False
    assert caps['rows_per_table'] == 1024 and caps['versions_per_table'] == 3072
    assert caps['backing_bytes'] > 0 and caps['columns'] > 0 and caps['result_rows'] > 0
    assert caps['tls'] is False and caps['bind'] == '127.0.0.1'
    assert caps['sql_execution_workers']==1 and caps['exclusive_sql_workers']==1
    assert caps['parallel_simple_reads'] is False
    assert caps['integral_version_banks'] is args.expect_integral_version_banks, caps
    slots = caps['tables']
    assert caps['table_integral_words'] == [0] * slots, caps
    assert caps['typed_bank_memory_profile'] is False
    assert caps['query_reference_bytes'] == 4 * caps['query_refs']
    capacity_args = ['--rows', '1024', '--versions', '3072', '--workers', '1']
    def integral_args(words):
        return ['--table-integral-words', ','.join(map(str, words))]
    compact = [1] + [0] * (slots - 1)
    candidate = run(['--capabilities', *capacity_args, *integral_args(compact)])
    if args.expect_integral_version_banks:
        assert candidate.returncode == 0 and not candidate.stderr, candidate
        parsed = json.loads(candidate.stdout)
        assert parsed['table_integral_words'] == compact and parsed['integral_version_banks'] is True
        assert parsed['table_rows'] == caps['table_rows'] and parsed['table_versions'] == caps['table_versions']
        assert parsed['backing_bytes'] < caps['backing_bytes'], (parsed, caps)
        zeros = run(['--capabilities', *capacity_args, *integral_args([0] * slots)])
        assert zeros.returncode == 0 and json.loads(zeros.stdout) == caps, zeros
        with tempfile.TemporaryDirectory(prefix='ramsql-typed-capacity-') as directory:
            wal = Path(directory) / 'must-not-be-created.wal'
            large = ['--table-rows', ','.join(map(str, [100_000_000] + [1] * (slots - 1))),
                     '--table-versions', ','.join(map(str, [100_065_536] + [2] * (slots - 1))),
                     *integral_args([6] + [0] * (slots - 1)), '--work-rows', '65536',
                     '--max-clients', '4', '--data', str(wal), '--create-data']

            def facts(command, *arguments):
                inspected = run([command, *large, *arguments])
                assert inspected.returncode == 0 and not inspected.stderr, inspected
                assert not wal.exists()
                return json.loads(inspected.stdout)

            stock = facts('--capabilities')
            lean_flag = '--typed-bank-memory-profile'
            lean = facts('--capabilities', lean_flag)
            observed = facts('--machine-capacity', lean_flag, '--memory-budget', '1')
            assert observed['startup_fits'] is False and observed['new_region_bytes'] == 0
            assert observed['startup_region_bytes'] >= observed['legacy_startup_bytes']
            for key in ('backing_bytes', 'query_workspace_bytes', 'query_refs', 'query_reference_bytes',
                        'read_context_payload_bytes_per_worker', 'table_storage', 'typed_bank_memory_profile'):
                assert observed[key] == lean[key], (key, observed, lean)
            assert lean['typed_bank_memory_profile'] is True
            assert lean['query_refs'] == 65536 * 4 * (caps['relations'] + 4)
            assert stock['query_refs'] == max(400_000_000, lean['query_refs'])
            assert lean['table_storage'][0] == dict(rows=100_000_000, versions=100_065_536,
                integral_row_words=6, committed_scan_projection=False, secondary_hashes=0)
            assert lean['table_storage'][1:] == stock['table_storage'][1:]
            assert stock['table_storage'][0]['secondary_hashes'] > 0
            if caps['columns'] == 8 and stock['table_storage'][0]['committed_scan_projection']:
                hash_bytes = stock['table_storage'][0]['secondary_hashes'] * 268_435_456 * 4
                removed = 7_700_000_000 + hash_bytes + stock['query_reference_bytes'] - lean['query_reference_bytes']
                assert stock['backing_bytes'] - lean['backing_bytes'] == removed
                assert observed['startup_region_bytes'] < 14_347_884_288
            overridden = facts('--capabilities', lean_flag, '--query-refs', str(lean['query_refs'] + 123))
            assert overridden['query_refs'] == lean['query_refs'] + 123
            assert overridden['backing_bytes'] == lean['backing_bytes'] + 123 * 4
        for flags in ([lean_flag], [lean_flag, *integral_args([0] * slots)],
                      ['--query-refs', '1', *integral_args(compact)],
                      [lean_flag, '--query-refs', '0', *integral_args(compact)],
                      [lean_flag, '--query-refs', '2147483648', *integral_args(compact)]):
            rejected = run(['--machine-capacity', *flags])
            assert rejected.returncode == 2 and not rejected.stdout and b'InvalidServerOptions' in rejected.stderr, rejected
    else:
        assert candidate.returncode == 2 and b'InvalidServerOptions' in candidate.stderr, candidate
        rejected = run(['--machine-capacity', '--typed-bank-memory-profile', *integral_args(compact)])
        assert rejected.returncode == 2 and not rejected.stdout and b'InvalidServerOptions' in rejected.stderr, rejected
    for invalid in ([0] * (slots - 1), [0] * (slots + 1), [-1] + compact[1:],
                    [1 << 62] + compact[1:]):
        for command in ('--capabilities', '--machine-capacity'):
            rejected = run([command, *capacity_args, *integral_args(invalid)])
            assert rejected.returncode in (1, 2) and not rejected.stdout, rejected
            assert any(reason in rejected.stderr for reason in
                       (b'InvalidServerOptions', b'InvalidParameterValue', b'InvalidCharacter', b'Overflow')), rejected
    automatic = json.loads(run(["--capabilities", "--workers", "auto", "--max-clients", "2"]).stdout)
    assert 1 <= automatic['workers'] <= 2 and automatic['exclusive_sql_workers'] == 1
    assert automatic['parallel_simple_reads'] is (automatic['workers'] > 1)
    assert run(["--capabilities", "--workers", "0"]).returncode == 2
    with tempfile.TemporaryDirectory(prefix='ramsql-capabilities-') as directory:
        wal = Path(directory) / 'must-not-be-created.wal'
        inspected = subprocess.run([str(binary), '--capabilities', '--data', str(wal)],
                                  capture_output=True, timeout=15)
        assert inspected.returncode == 0 and not wal.exists(), inspected
        inspected = subprocess.run([str(binary), '--capabilities', '--create-data', '--data', str(wal)],
                                  capture_output=True, timeout=15)
        assert inspected.returncode == 0 and not wal.exists(), inspected
        # --create-data never combines with --memory or offline backup.
        for arguments in (['--memory', '--create-data'], ['--create-data', '--memory'],
                          ['--data', str(wal), '--create-data', '--backup-to', str(wal) + '.backup']):
            rejected = subprocess.run([str(binary), *arguments, '--insecure-local', '--port', '0'],
                                      capture_output=True, timeout=15)
            assert rejected.returncode == 2 and b'RAMSQL' not in rejected.stdout, (arguments, rejected)
            assert b'InvalidServerOptions' in rejected.stderr, (arguments, rejected.stderr)
            assert not wal.exists() and not Path(str(wal) + '.backup').exists(), arguments
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
