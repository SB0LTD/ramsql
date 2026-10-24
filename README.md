# RAMSQL

An allocator-free in-memory database **server**, written entirely in Sig.
PostgreSQL is the default wire protocol; optional MySQL and SQL Server/TDS
listeners use the same bounded database.

**Development version. Production PostgreSQL drop-in compatibility is a release
requirement and is currently blocked.** See [release gates](compatibility/RELEASE.md)
and the [native PostgreSQL differential report](compatibility/postgres-report.json).
The `0.3.0-alpha.2` subset has synchronous persistence and PostgreSQL SCRAM authentication;
it does not provide complete PostgreSQL behavior.
The [detailed gap-closing plan](compatibility/GAP_CLOSING_PLAN.md) explains the
remaining work, its practical impact, implementation layers and closing tests.

## Run

Use the complete [Sig 0.6.3 release](https://github.com/SB0LTD/sig/releases/tag/sig-0.6.3-zig0.17.0.ac9b716).
The source and SHA-256 for ZPM are pinned in `build.sig.zon` and fetched by Sig.
Current-source ZPM delegates `zpm build` / `zpm run` to the same native build.

```sh
sig build -Doptimize=ReleaseFast
./sig-out/bin/ramsql --data ramsql.wal --auth-user postgres --auth-verifier-file postgres.verifier
# PostgreSQL on 127.0.0.1:5432; Windows executable has .exe suffix.
```

```sh
./sig-out/bin/ramsql --memory --insecure-local --port 5432 --mysql-port 3306 --mssql-port 1433
./sig-out/bin/ramsql --memory --insecure-local --protocol mysql --port 3306
./sig-out/bin/ramsql --memory --insecure-local --protocol mssql --port 1433
```

Each process has one shared store. By default the server requires a PostgreSQL
SCRAM verifier and persists committed mutations in `ramsql.wal`. The verifier file
contains PostgreSQL's `SCRAM-SHA-256$iterations:salt$stored-key:server-key` format
for `--auth-user` (default `postgres`), optionally followed by a newline. Provision
it through a trusted credential-management process; protect it as a credential.
Fresh server nonces come from native cryptographic entropy on every connection.
TLS and channel binding are not implemented, so listeners remain loopback only.
MySQL/TDS require the explicit `--insecure-local` testing mode.

`--memory` explicitly disables persistence. `--data PATH` selects another WAL.
Recovery validates record versions, checksums, contiguous sequences, commit markers
and SQL replay before opening any listener. It repairs a physically incomplete
final record; complete corruption or semantic replay failure aborts startup.
The process holds an exclusive writer lock. A failed append/flush disables further
writes until recovery. A transaction is acknowledged only after the complete
commit record reaches the operating system's synchronous flush API.

This does not establish power-loss durability on every storage device. There is
no checkpoint/compaction or automatic upgrade migration yet. WAL grows with all
committed changes; recovery needs capacity for historical states and transactions.
Back up using the offline command below, and test restoration before relying on it.

`--port 0` chooses an ephemeral port and prints the actual listener address.
`--max-clients N` selects active connections within the compiled profile;
`--idle-ms N` controls expiry (30,000 ms default). The default profile contains
16 clients, 8 tables, 8 columns, 512 rows per table, 63-byte names, 128-byte text
cells, and 1 MiB of change-record storage per transaction. Configure an explicit profile:

```sh
sig build -Doptimize=ReleaseFast -Dclients=32 -Drows=4096 -Dtables=16 -Dtext-bytes=512 -Dtransaction-bytes=4194304
```

`-Dtx-writes=N` (rows changed per transaction, default 4096) and `-Dversions=N`
(row-version slots per table, default 2 x rows) size the transaction engine.
`-Dtimezones=N` (default 8) sizes the named time-zone cache (16 KiB each).

`--timezone-dir PATH` names a zoneinfo (TZif) directory for named time zones
(`SET TIME ZONE 'America/New_York'`, `... AT TIME ZONE 'Europe/Paris'`); the
default is `/usr/share/zoneinfo` on Linux and macOS and none on Windows and
native SB0, where only `UTC`, numeric offsets and POSIX TZ strings (`UTC+3`)
work. Zone names must match `[A-Za-z0-9_+\-/]{1,64}`. Case-insensitive names
(`america/new_york`, reported as `America/New_York`) need a
`ramsql-zones.idx` index in that directory; `python tools/fetch_tzdata.py
--image postgres:18-alpine` copies a reference image's zoneinfo with its index
into `.sig-cache/tzdata/<image-id>`. Without an index, a case-mismatched name is
`22023` where PostgreSQL accepts it.
Storage, results, version arenas and transaction tables are dense and preallocated, so larger
profiles increase memory significantly. These configurable bounds still fall short
of PostgreSQL's capacity and concurrency model; they are tracked as a release gap.
The Windows reactor currently permits at most 60 compiled client slots.

`--demo` runs an example and exits. `--stdin` retains the one-statement-per-line
CLI, with tab-separated escaped output and a 4,096-byte line bound.

## Client compatibility

Compatibility is tested with real independent drivers, not only our packet tests.
The implemented SQL subset is shared across protocols; this is not full server
or dialect parity with the original database engines.

| Wire protocol | Tested independent client | Implemented path |
| --- | --- | --- |
| PostgreSQL v3 | psycopg 3 / libpq | Startup, SCRAM-SHA-256, SSL/GSS refusal, simple queries, Parse/Bind/Describe/Execute/Sync, named prepared statements, text/binary values, portal suspension, Close/DEALLOCATE |
| MySQL protocol 10 | PyMySQL | Native-password empty-password login, COM_QUERY, COM_INIT_DB, PING, text result sets and errors |
| SQL Server TDS 7.2–7.4 | python-tds | PRELOGIN, LOGIN7, UTF-16 SQL batches, typed results, packet fragmentation, transaction-manager messages and parameterized sp_executesql RPC |

For authenticated PostgreSQL, use `host=127.0.0.1 port=5432 user=postgres dbname=ramsql sslmode=disable` and supply the matching password through the client credential mechanism.
PyMySQL can use `host='127.0.0.1', port=3306, user='ramsql', password='',
database='ramsql', autocommit=True, charset='utf8mb4'`.
For python-tds use `server='127.0.0.1', port=1433, user='ramsql', password='',
database='ramsql', autocommit=True, use_mars=False`.

Not implemented: TLS, roles/authorization, system catalogs, replication,
COPY/bulk loading, stored procedures, ORM-specific discovery, and complete SQL
dialects. MySQL's binary prepared-statement protocol is not implemented;
PyMySQL parameters use its normal safe client-side encoding. Unsupported
operations return errors; they do not silently succeed. SQL Server RPC supports bounded integer, boolean, NVARCHAR and NULL parameters
for sp_executesql; other procedures and parameter types are rejected.

## SQL and transactions

CREATE/DROP TABLE; single-row INSERT with optional columns and expression
values; SELECT expressions or `*` from at most one table (or no table); UPDATE
with expression SET; DELETE. Types are SMALLINT/INT2, INTEGER/INT/INT4,
BIGINT/INT8, NUMERIC/DECIMAL[(p[,s])], REAL/FLOAT4, DOUBLE PRECISION/FLOAT8,
BOOLEAN, TEXT, NAME, OID, DATE, TIMESTAMP[(p)] [WITHOUT TIME ZONE],
TIMESTAMPTZ[(p)]/TIMESTAMP WITH TIME ZONE, INTERVAL, JSON and JSONB, plus NULL.
NOT NULL and one single-column PRIMARY KEY are supported.

Values follow PostgreSQL where implemented: NUMERIC rounds half away from
zero, uses PostgreSQL's division/AVG scales and NUMERIC(p,s) typmods (negative
scales allowed, `22003` on overflow), and supports NaN/Infinity (NaN sorts
last); REAL/DOUBLE accept the measured `float8in` grammar (decimal, `0x..p`
hex, Infinity/inf/NaN) and print shortest round-trip digits like `float8out`;
float-to-integer casts round half to even, numeric-to-integer half away.
Temporal input is ISO 8601 (`T` or space, fractions, `Z`/`+-HH[:MM[:SS]]`, a
trailing zone name, `BC`), `infinity`/`-infinity`/`epoch`, with PostgreSQL's
range limits (`22008`) and `22007` for malformed text; output uses DateStyle
ISO and IntervalStyle postgres. `+ -` on dates, timestamps and intervals,
`AT TIME ZONE`, `now()`/`CURRENT_TIMESTAMP`/`transaction_timestamp()` (the
transaction start), `statement_timestamp()`, `clock_timestamp()` and
`CURRENT_DATE` are supported; nonexistent and ambiguous local times resolve as
PostgreSQL's Appendix B.1 describes. `SET TIME ZONE` accepts UTC, a number of
hours, zone names and POSIX TZ strings, is transactional (ROLLBACK, ROLLBACK
TO, SET LOCAL, reverted by an error in a block) and is reported through
ParameterStatus with DateStyle, IntervalStyle and application_name. JSON keeps
its validated text; JSONB is canonical (sorted keys, last duplicate wins,
NUMERIC numbers, PostgreSQL output spacing) with `-> ->> #> #>> @> <@ ?`,
equality and btree ordering, `jsonb_typeof` and `jsonb_array_length`.
PostgreSQL binary results and parameters are supported for every type.

Expressions follow PostgreSQL precedence and typing for that type set: AND/OR/NOT
with three-valued logic, comparisons, IS [NOT] NULL/TRUE/FALSE/UNKNOWN,
[NOT] IN lists, [NOT] BETWEEN, [NOT] LIKE/ILIKE, `||`, checked integer
`+ - * / %`, CASE, COALESCE, NULLIF and `::`/CAST, with PostgreSQL SQLSTATEs
(42804 for `WHERE id`, 42883 for `label + 1`, 22P02, 22003, 22012). A statement
that fails after changing many rows changes none. ORDER BY accepts expressions,
output positions and names with ASC/DESC and NULLS FIRST/LAST, then LIMIT
[ALL], OFFSET and FETCH FIRST|NEXT n ROW|ROWS ONLY (negative counts give
`2201W`/`2201X`). Queries support SELECT DISTINCT and DISTINCT ON (NULLs equal;
PostgreSQL's `42P10` rules); COUNT(*), COUNT, SUM, AVG, MIN, MAX, BOOL_AND,
BOOL_OR/EVERY with DISTINCT and FILTER (WHERE), GROUP BY (columns, expressions,
positions, output names) and HAVING, with PostgreSQL result types (SUM of
int2/int4 is int8, SUM(int8) and AVG of integers are NUMERIC, COUNT is int8;
`42803` for ungrouped columns); INNER, LEFT, RIGHT, FULL and CROSS joins with
ON or USING, comma joins, aliases and self-joins (`42702` for an ambiguous
column, `42P01` for an unknown qualifier); scalar subqueries (`21000` for more
than one row), [NOT] IN and [NOT] EXISTS subqueries (correlated or not) and
derived tables; WITH CTEs (at most 4 per statement), WITH RECURSIVE over
UNION [ALL] to a fixpoint, and UNION, INTERSECT and EXCEPT [ALL]. AVG(interval),
LATERAL, NATURAL joins, parenthesized join trees, window functions (flag F6)
and data-modifying CTEs (flag F7) are not provided.
Default ordering follows PostgreSQL: ASC NULLS LAST, DESC NULLS FIRST. A WHERE
holds at most 8 leaf predicates and a statement at most 512 expression nodes
nested 64 deep. Unquoted names are ASCII and case insensitive, quoted names keep
case; text is bytewise and text bounds count bytes (including `||` results).
Apostrophes are doubled in SQL text. Each execute call accepts one statement.
Other functions (including `pg_typeof`), window functions,
arrays, JSON (non-B) operators, jsonpath, zone abbreviations (`EST`),
non-ISO DateStyle and non-postgres IntervalStyle are not supported. MySQL and
TDS clients receive NUMERIC, REAL/DOUBLE, temporal and JSON values as text
columns (no native DECIMAL/DOUBLE/DATETIME metadata).

MySQL and TDS sessions additionally accept integer 0/1 when assigning BOOLEAN
values (INSERT/UPDATE), for common BIT client encodings; expressions are typed
identically on every listener. Raw library configurations retain the ZPM
defaults (ASC NULLS FIRST, 64-bit INTEGER outside the PostgreSQL dialect);
select `integer_bits = 32` and `nulls_first_ascending = false` for PostgreSQL
scalar semantics.

Transactions are READ COMMITTED only, over row versions in one shared store
(no per-connection copies). Every statement takes a new snapshot, so it sees
everything committed before it started plus its own transaction's earlier
writes. A write that meets a row being changed by another live transaction
waits for it, then re-checks the row's newest committed version
(PostgreSQL's EvalPlanQual), so concurrent `v = v + 1` updates are never lost;
an INSERT waits for a live inserter or deleter of the same key, then gets
`23505` or proceeds. `SELECT ... FOR UPDATE|SHARE [NOWAIT|SKIP LOCKED]` takes
row locks; DROP TABLE takes an exclusive table lock that waits for readers and
writers (and that readers then wait for). Deadlocks are detected once per wait
after 1 s (`40P01`, the detecting waiter is the victim); `lock_timeout` gives
`55P03`, `statement_timeout` and PostgreSQL cancel requests give `57014`.
Waiting never blocks the reactor; other sessions keep running.
`BEGIN`/`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ|SERIALIZABLE` is
rejected with `0A000` instead of silently giving READ COMMITTED;
`SHOW transaction_isolation` reports `read committed`. DDL is transactional:
an uncommitted CREATE TABLE is invisible to other sessions and ROLLBACK of a
DROP TABLE restores the table and its rows. SAVEPOINT, RELEASE [SAVEPOINT] and
ROLLBACK TO [SAVEPOINT] nest (a repeated name refers to the newest; `3B001` for
an unknown name, `25P01` outside a block); ROLLBACK TO undoes rows, DDL, row
locks and SET values after the savepoint and recovers a failed block. An error
inside a block discards the work since the innermost savepoint, releases its
locks at once and fails the block (`25P02`) until ROLLBACK or ROLLBACK TO, as
PostgreSQL does. SET [SESSION|LOCAL], SHOW and RESET [ALL] support
`lock_timeout`, `statement_timeout`, `application_name` and read-only views of
the reported session defaults; startup `options` accept `-c name=value`.
Failed transactions and disconnected sessions cannot publish their changes.
Cursors and two-phase commit do not exist.

Foreign keys, secondary indexes, window functions and complete SQL-standard
semantics remain release blockers. The committed finite differential gate report
matches 19/37 probes against PostgreSQL 16 and 18, including integer OIDs/ranges,
binary formats, and NULL ordering; development builds additionally match
`or_predicate`, `read_committed`, `savepoint`, `numeric`, `timestamp`,
`jsonb`, `distinct`, `aggregate`, `group_by`, `join` and `cte` (30/37), and the
committed reports are refreshed at release.
Catalogs and broader SQL behavior remain incompatible.

## Memory and execution

The database, primary-key hash indexes, version arenas, the transaction and lock tables, connection slots,
prepared statements, receive buffers and response buffers all have fixed owners
and bounds. Runtime source is `.sig`; there is no SQLite/C database dependency,
heap allocator API, language runtime or garbage collector.

One nonblocking reactor services all listeners. It handles partial reads/writes,
limits work per connection, stops reading backpressured sockets, and expires
idle sessions. Pipelined responses are coalesced within a bounded queue, and
input is compacted once per socket read rather than once per packet.
OS socket buffers and native platform services remain OS-owned.
The current implementation uses one reactor thread, not multiple CPU workers.

Primary-key equality queries and duplicate checks use a bounded hash index.
Unused result capacity is not cleared for each statement; only active ranges
are valid. Rows have stable rowids and versions in a fixed arena per table
(`-Dversions`, default 2 x rows); superseded versions are reclaimed once no
waiting statement's snapshot can see them. Uncommitted changes of every open
transaction share that arena, so a transaction or statement that cannot fit
its new versions fails with `VersionCapacity` (53200) where PostgreSQL would
succeed. Transaction capacity limits, each `53200` with the statement rolled
back: `clients + 2` concurrent transactions, `-Dtx-writes` (default 4096) rows
changed per transaction (including every COPY row later), 256 FOR UPDATE/SHARE
locked rows server-wide with at most 8 FOR SHARE holders per row, 32
savepoints per transaction, and `-Dtransaction-bytes` per commit record.
Statements beyond 512 expression nodes or depth 64 fail with
`StatementTooComplex` (54001). Value capacity limits: a NUMERIC holds at most 64
significant decimal digits and a display scale of at most 120 (declared
precision above 64, results needing more digits, and literals such as `1e100`
fail with `54000`; `1e100::float8` is parsed as a float); a TEXT, JSON or
encoded JSONB value is at most `-Dtext-bytes` bytes and JSONB nests at most 64
deep (`54001`); a statement returns at most 8 columns. TIMESTAMP(p) above 6 is
clamped to 6 without PostgreSQL's warning.
LIMIT without ORDER BY stops scanning once enough rows match. A top-level
`pk = constant` conjunct uses the index; other filters remain scans, and
ordering uses a stable merge sort. Sorting, DISTINCT, grouping, joins, CTEs,
derived tables and set operations are bounded by one statement-scoped query
workspace of `-Dwork-rows` (default: rows) rows of up to 16 cells; a query that
needs more (for example a large non-equi or cross join, or a WITH RECURSIVE
that does not terminate) fails with `WorkspaceCapacity` (`54000`) before
returning anything, where PostgreSQL would spill to disk. Joins are nested
loops; an ON conjunct `inner.pk = expression` of an INNER or LEFT join probes
the inner table's primary-key index. A statement returns at most rows-per-table
rows (`54000` beyond). Each query references at most 4 FROM items per block and
16 query blocks (subqueries, CTE bodies, set-operation branches).

The WAL stores versioned kind-3 change records (row and DDL ops with explicit
type OIDs). Logs written by alpha.1/alpha.2 (SQL-text kind 2) still recover through
an alpha-grammar-only replay mode and continue with kind-3 records; a legacy record
containing anything outside the alpha grammar, or appearing after a kind-3 record,
stops recovery with `UnsupportedLogVersion` before any listener binds. Older
binaries cannot read kind-3 records. The per-transaction log bound
(`-Dtransaction-bytes`) also sizes the recovery buffer.

## Layers

| Layer | Responsibilities |
| --- | --- |
| Sig | Compiler, native build, formatting, process and standard I/O primitives |
| ZPM Layer 0 | `sql`, `db-store`, `durable-log` and `scram`: parsing, storage, transactions, WAL framing and authentication |
| ZPM Layer 1 | PostgreSQL/MySQL/TDS wire sessions; native TCP, durable files and cryptographic entropy |
| RAMSQL | Public facade, persistence/replay lifecycle, offline backups, listeners, reactor policy, capacity profiles and integration tests |

The public library remains `@import("ramsql").Database(Config)` with separate
caller-owned `DB.Result` storage. `DB.storageBytes()` and `@sizeOf(DB.Result)`
report the profile's bounded memory. `Result.column_types` describes even empty
SELECT results. Names, text, and results are owned copies. Failing SQL preserves
the database and previous result. Callers serialize raw library access.

## Tests

```sh
sig build test -Doptimize=ReleaseSafe
python tools/test_cli.py
python tools/test_server.py
python -m pip install -r tools/requirements-test.txt
python tools/test_clients.py
python tools/test_auth.py
python tools/test_persistence.py
# Separate release gates; currently return nonzero for known compatibility gaps
python tools/test_postgres_compat.py
python tools/release_gate.py
```

Client dependencies are test-only. Local tests may install into
`.sig-cache/client-deps`; the harness finds that directory automatically.
Protocol contracts cover malformed/fragmented packets, lengths, parameters,
UTF-16, typed NULLs, output capacity, errors and recovery, plus deterministic
fuzzing. TCP integration covers shared state, pipelining, half-close, connection
exhaustion/reuse, idle expiry, transactions, and slow-reader fairness. Independent
drivers exercise actual handshakes and data round trips. CI runs on Windows and
Linux with checksum-verified matched Sig releases.

## Benchmarks

```sh
sig build benchmark -Doptimize=ReleaseFast
python tools/benchmark.py --build-mode ReleaseFast --output benchmarks/local.json
```

The native benchmark times parsing, lookup and materialization with checked
results. The recorded Linux experiment in [benchmarks/local.json](benchmarks/local.json)
has median native primary-key lookup of 5.12 million operations/s (195 ns/op),
and 174,854 PostgreSQL simple queries/s over loopback with one connection and
64-query batches. These use a 512-row in-memory table, with no WAL, TLS or
authentication; they are not durable production throughput claims.

The [durable PostgreSQL comparison](benchmarks/postgres-comparison.json) records
every trial and correctness/recovery check on native Linux processes. RAMSQL
leads some single-client reads, while PostgreSQL leads durable inserts/updates
and concurrent writes. All results remain visible. Every release also runs this
comparison against its exact checksummed Linux binary and ships the report.

The network benchmark includes TCP, protocol handling and Python
client validation overhead; sequential and pipelined costs are reported
separately. Results include machine/build metadata and workload bounds.
They are measurements of this implementation, not comparisons to PostgreSQL,
MySQL or SQL Server. See the checked-in report for the measured environment.

MIT License — SB0 LTD.

## Offline backup and restore

Stop the server before backing up; the writer lock prevents a concurrent copy.
The backup command validates and replays the source before publishing a flushed,
complete WAL copy. It refuses to overwrite an existing destination.

```sh
./sig-out/bin/ramsql --data ramsql.wal --backup-to backup-2026-10-22.wal
./sig-out/bin/ramsql --data backup-2026-10-22.wal --auth-verifier-file postgres.verifier
```

Keep backups on separate storage. Restore with the same version and a profile
large enough for every historical state. This is a whole-WAL backup, not a
compacted checkpoint or PostgreSQL pg_dump/pg_basebackup compatibility.

## Release matrix

`.github/workflows/release.yml` follows SLS's six hosted targets: x86_64 and
AArch64 on Linux GNU, macOS and Windows. It also defines distinct SB0X userspace
and SB0K kernel artifacts, both built directly for the consolidated
`aarch64-sb0` target. Native SB0 images are never renamed ELF/PE executables.

Hosted artifacts use the same TCP server, synchronous WAL and SCRAM paths. Their
exact packaged binaries are tested on matching OS/architecture runners. Native
SB0X currently uses a delegated process channel; SB0K uses privileged PL011 UART.
These native profiles run the shared PostgreSQL/SQL/store implementation over
trusted, memory-only streams. The current SB0 device ABI does not provide the
TCP listener, file durability barrier and secure entropy interfaces required for
the hosted profile. Artifact capability manifests preserve this distinction.

A release requires all eight builds, hosted contract and client tests, six exact
artifact runtime checks, native QEMU protocol/transaction checks, and a matched
durable benchmark of the exact Linux artifact against native PostgreSQL. The
benchmark verifies results and crash recovery; its ratios do not assert full
PostgreSQL parity or universally superior speed. Packages
include deterministic archives, content hashes, source/toolchain/dependency
provenance and capability manifests. A complete release bundle also includes
SHA256SUMS and GitHub provenance attestations.

Run the workflow manually with `publish=false` to validate and retain CI
artifacts. Set `publish=true` to publish after every gate passes. A matching
`v<source-version>` tag also publishes after validation. Development versions
are marked prerelease; stable versions additionally require the production
release gate. Adding the pipeline does not change the current production status.

SB0X tests use a self-contained, public SB0 ABI loader harness under QEMU. It
loads the native segments, initializes process state, enters EL0 and serves the
delegated channel traps used by the PostgreSQL tests. This validates the SB0S
image and process ABI without private repository credentials. It is separate
from integration testing a complete SB0S kernel distribution. SB0K tests execute
the privileged image directly. Failed native compilation or execution blocks
publication.

```sh
python tools/test_release.py
python tools/build_sb0.py --format sb0x --output sig-out/bin/ramsql.sb0x
python tools/build_sb0.py --format sb0k --output sig-out/bin/ramsql.sb0k
# Kernel image, using qemu-system-aarch64:
python tools/test_sb0.py --format sb0k --image sig-out/bin/ramsql.sb0k
```

The native build script verifies the same immutable ZPM archive as `sig build`.
Hosted builds use published Sig 0.6.3. Native jobs bootstrap the exact Sig source
revision pinned in `tools/release_matrix.py`, including the AArch64 aggregate,
error-return and exception-return fixes needed by the complete engine. The
published compiler builds this toolchain; its standard library comes from the
same pinned source. Executable compiler regressions and the PostgreSQL QEMU
sessions must pass before packaging. Provenance records both compiler identities
and the source revision. For local native builds, set `SIG` and `SIG_LIB_DIR` to
that compiler and its matching library before running the commands above.
