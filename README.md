# RAMSQL

An in-memory SQL database written entirely in allocator-free Sig, started with
`zpm init --template package --name ramsql --layer 0`.

The reusable engine is ZPM's **Layer 0 `@zpm/sql`** package. RAMSQL exposes its
public API, a bounded command-line interface, examples, and integration tests.
Database and result storage belong to the caller. Names, text values, and result
rows are copied inline; SQL input can be reused immediately after execution.

## Build and run

Tested with the complete [Sig 0.6.3 release](https://github.com/SB0LTD/sig/releases/tag/sig-0.6.3-zig0.17.0.ac9b716)
and current-source ZPM 0.3.0. `build.sig.zon` pins the exact ZPM commit archive
and its SHA-256; Sig fetches and verifies it automatically.

```sh
sig version
sig build
sig build test
sig build run
python tools/test_cli.py
```

Current-source `zpm build`, `zpm build test`, and `zpm run` forward to Sig.
The old published ZPM v0.3.0 binary only prints build/run commands; build its CLI
from the same pinned ZPM source with `sig build zpm` in `cli/` if needed.

By default the executable runs a short demonstration. For a database session,
send **one statement per line** through stdin. All lines share one database;
the database lasts until the process exits.

```powershell
Get-Content examples/quickstart.sql | ./sig-out/bin/ramsql.exe --stdin
```

```sh
./sig-out/bin/ramsql --stdin < examples/quickstart.sql
```

The CLI prints tab-separated SELECT headers and rows, with `TRUE`, `FALSE`, and
`NULL` values. Text escapes backslash, tab, carriage return, and newline as `\\`,
`\t`, `\r`, and `\n`. Mutations print `OK <affected_rows>`; CREATE and DROP print
`OK 0`. Errors go to stderr. Invalid statements leave the database unchanged;
processing continues, and any failed statement gives a final exit status of 1.
Unknown options return 2. Lines longer than 4,096 bytes are discarded in full.
The final line executes without a trailing newline. `--help` and `--version`
are also available.

## Library API

Wire the `ramsql` module from `src/root.sig` and its named `sql` import from the
resolved ZPM source tree, as shown in `build.sig`.

```zig
const ram = @import("ramsql");
const DB = ram.Database(.{
    .tables = 2,
    .columns = 4,
    .rows_per_table = 64,
    .name_bytes = 32,
    .text_bytes = 128,
    .predicates = 8,
});

var db: DB = .{};
var result: DB.Result = .{};
try db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)", &result);
try db.execute("INSERT INTO notes VALUES (1, 'hello')", &result);
try db.execute("SELECT body FROM notes WHERE id = 1", &result);
const body = result.rows[0].cells[0].text.slice();
```

`DB.storageBytes()` reports database storage; `@sizeOf(DB.Result)` reports the
separate result storage. Large profiles can live in caller-owned static storage.
The default profile has 4 tables, 8 columns, 128 rows per table, 32-byte names,
128-byte text values, and 8 AND predicates. No allocator parameter is accepted.
Callers serialize access. An error preserves both the database and the previous
result. Results own their data and remain valid until the caller overwrites them.

## SQL supported in 0.1.0

- `CREATE TABLE` / `DROP TABLE`, with `INTEGER` (`INT`), `BOOLEAN` (`BOOL`), and `TEXT`.
- `NOT NULL` and a single-column `PRIMARY KEY`, which also implies NOT NULL.
- Single-row `INSERT INTO ... [(columns)] VALUES (...)`; omitted columns become NULL.
- `SELECT *` or named columns, `WHERE` comparisons joined by `AND`, `IS [NOT] NULL`,
  one `ORDER BY ... [ASC|DESC]`, and `LIMIT`.
- `UPDATE ... SET column = literal [, ...] [WHERE ...]` and `DELETE FROM ... [WHERE ...]`.
- Signed 64-bit integer, boolean, text, and NULL literals; doubled apostrophes
  in text, optional trailing semicolon, and `--` line comments.

Identifiers are ASCII and case insensitive. Text comparison is bytewise and
text bounds count bytes. Ordinary NULL comparisons do not match; use IS NULL.
Sorting is stable, with NULL first in ascending order and last in descending
order. Capacity, type, syntax, overflow, and constraint failures are explicit
errors. Exactly one statement is accepted by each `execute` call.

This initial version uses table scans and bounded insertion sorting. It does
not yet provide joins, aggregates, indexes, transactions, parameters, persistence,
network serving, or full SQL-standard compatibility.

## Layers and validation

| Layer | Responsibility |
| --- | --- |
| Sig | Compiler, native build system, freestanding I/O, formatting, process primitives |
| ZPM Layer 0 | SQL parser, schema, typed values, bounded storage, execution, constraints |
| RAMSQL | Public facade, capacity profile, CLI policy/output, examples, consumer contracts |

`sig build test` executes the pinned ZPM SQL contract and RAMSQL consumer
contract. Engine coverage includes CRUD, ownership, NULL semantics, constraints,
all capacities, malformed statements, integer boundaries, and deterministic
parser fuzzing. Tests execute real assertions, including in release modes.
`tools/test_cli.py` exercises the executable through subprocesses. CI checks
Windows and Linux using checksum-verified matched Sig releases.

MIT License — SB0 LTD.

## Run

```
zpm run
```
