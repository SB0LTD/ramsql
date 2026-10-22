# RAMSQL

An allocator-free in-memory database **server**, written entirely in Sig.
PostgreSQL is the default wire protocol; optional MySQL and SQL Server/TDS
listeners use the same bounded database.

**Development version. Production PostgreSQL drop-in compatibility is a release
requirement and is currently blocked.** See [release gates](compatibility/RELEASE.md)
and the [native PostgreSQL differential report](compatibility/postgres-report.json).
The `0.3.0-alpha.2` subset has synchronous persistence and PostgreSQL SCRAM authentication;
it does not provide complete PostgreSQL behavior.
The [implementation sequence](compatibility/IMPLEMENTATION.md) tracks the storage,
transaction, compatibility, security and qualification work needed for that target.

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
cells, and 1 MiB of SQL log storage per transaction. Configure an explicit profile:

```sh
sig build -Doptimize=ReleaseFast -Dclients=32 -Drows=4096 -Dtables=16 -Dtext-bytes=512 -Dtransaction-bytes=4194304
```

Storage, results and transaction workspaces are dense and preallocated, so larger
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

CREATE/DROP TABLE; single-row INSERT with optional columns; SELECT projection
or `*`; UPDATE literal assignments; DELETE. Server/default CLI types are signed
32-bit INTEGER/INT, signed 64-bit BIGINT/INT8,
BOOLEAN and TEXT, plus NULL. NOT NULL and one single-column PRIMARY KEY are
supported. WHERE supports typed comparisons, bounded AND conjunctions and
IS [NOT] NULL. SELECT supports one ORDER BY column, NULLS FIRST/LAST and LIMIT.
Default ordering follows PostgreSQL: ASC NULLS LAST, DESC NULLS FIRST. Names are ASCII
and case insensitive; text is bytewise and text bounds count bytes. Apostrophes
are doubled in SQL text. Each execute call accepts one statement.

The server additionally accepts boolean integer literals 0/1 for common
MySQL/BIT client encodings. Raw library configurations remain strictly typed
unless this option is enabled. Raw library configurations retain the ZPM defaults
(64-bit INTEGER and ASC NULLS FIRST); select `integer_bits = 32` and
`nulls_first_ascending = false` for PostgreSQL scalar semantics.

BEGIN/COMMIT/ROLLBACK use separate caller-owned transaction snapshots. Readers
continue to see committed data while other connections hold private changes.
A writing transaction commits only if the global committed generation has not
changed since BEGIN; otherwise it receives PostgreSQL SQLSTATE `40001` and must
roll back/retry. Read-only commits do not conflict. Failed transactions and
disconnected sessions cannot publish their changes. This is conservative snapshot
isolation and differs from PostgreSQL's default READ COMMITTED behavior.

Joins, aggregates, general expressions, foreign keys, secondary indexes and
complete SQL-standard semantics remain release blockers. The finite differential
gate now matches 19/37 probes against PostgreSQL 16 and 18, including integer
OIDs/ranges, binary formats, and NULL ordering. Catalogs and broader SQL behavior
remain incompatible.

## Memory and execution

The database, primary-key hash indexes, per-connection transaction snapshots, connection slots,
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
are valid. Durable candidates, transaction snapshots and recovery copy live
schemas/rows/indices rather than reserved database capacity. CREATE/DROP also
avoid clearing inactive row payloads. LIMIT without ORDER BY stops scanning once enough rows match.
Deletion compaction and primary-key changes rebuild index references. General
filters remain scans, and ordering currently uses bounded insertion sorting.

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
