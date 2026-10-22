# RAMSQL

An allocator-free in-memory database **server**, written entirely in Sig.
PostgreSQL is the default wire protocol; optional MySQL and SQL Server/TDS
listeners use the same bounded database.

## Run

Use the complete [Sig 0.6.3 release](https://github.com/SB0LTD/sig/releases/tag/sig-0.6.3-zig0.17.0.ac9b716).
The source and SHA-256 for ZPM are pinned in `build.sig.zon` and fetched by Sig.
Current-source ZPM delegates `zpm build` / `zpm run` to the same native build.

```sh
sig build -Doptimize=ReleaseFast
./sig-out/bin/ramsql
# PostgreSQL on 127.0.0.1:5432; Windows executable has .exe suffix.
```

```sh
./sig-out/bin/ramsql --port 5432 --mysql-port 3306 --mssql-port 1433
./sig-out/bin/ramsql --protocol mysql --port 3306
./sig-out/bin/ramsql --protocol mssql --port 1433
```

Each process has one shared in-memory store. Extra listeners in the same process
share tables, constraints and transactions. Binding is deliberately loopback-only:
this version uses empty-password/trust sessions and does not implement TLS or
production authentication. It must not be exposed through a public proxy.

`--port 0` chooses an ephemeral port. Startup prints each listener's actual
address. `--max-clients N` selects 1..16 active connections; `--idle-ms N` controls
idle expiry (30,000 ms default). Defaults are 8 tables, 8 columns, 512 rows per
table, 32-byte names and 128-byte text cells. These are explicit compile-time
bounds in the server profile, rather than unbounded runtime growth.

`--demo` runs an example and exits. `--stdin` retains the one-statement-per-line
CLI, with tab-separated escaped output and a 4,096-byte line bound.

## Client compatibility

Compatibility is tested with real independent drivers, not only our packet tests.
The implemented SQL subset is shared across protocols; this is not full server
or dialect parity with the original database engines.

| Wire protocol | Tested independent client | Implemented path |
| --- | --- | --- |
| PostgreSQL v3 | psycopg 3 / libpq | Startup, SSL/GSS refusal, simple queries, Parse/Bind/Describe/Execute/Sync, named prepared statements, text/binary values, portal suspension, Close/DEALLOCATE |
| MySQL protocol 10 | PyMySQL | Native-password empty-password login, COM_QUERY, COM_INIT_DB, PING, text result sets and errors |
| SQL Server TDS 7.2–7.4 | python-tds | PRELOGIN, LOGIN7, UTF-16 SQL batches, typed results, packet fragmentation, transaction-manager messages and parameterized sp_executesql RPC |

For PostgreSQL, use `host=127.0.0.1 port=5432 user=ramsql dbname=ramsql sslmode=disable`.
PyMySQL can use `host='127.0.0.1', port=3306, user='ramsql', password='',
database='ramsql', autocommit=True, charset='utf8mb4'`.
For python-tds use `server='127.0.0.1', port=1433, user='ramsql', password='',
database='ramsql', autocommit=True, use_mars=False`.

Not implemented: TLS, password authentication, system catalogs, replication,
COPY/bulk loading, stored procedures, ORM-specific discovery, and complete SQL
dialects. MySQL's binary prepared-statement protocol is not implemented;
PyMySQL parameters use its normal safe client-side encoding. Unsupported
operations return errors; they do not silently succeed. SQL Server RPC supports bounded integer, boolean, NVARCHAR and NULL parameters
for sp_executesql; other procedures and parameter types are rejected.

## SQL and transactions

CREATE/DROP TABLE; single-row INSERT with optional columns; SELECT projection
or `*`; UPDATE literal assignments; DELETE. Types are signed 64-bit INTEGER,
BOOLEAN and TEXT, plus NULL. NOT NULL and one single-column PRIMARY KEY are
supported. WHERE supports typed comparisons, bounded AND conjunctions and
IS [NOT] NULL. SELECT supports one ORDER BY column and LIMIT. Names are ASCII
and case insensitive; text is bytewise and text bounds count bytes. Apostrophes
are doubled in SQL text. Each execute call accepts one statement.

The server additionally accepts boolean integer literals 0/1 for common
MySQL/BIT client encodings. Raw library configurations remain strictly typed
unless this option is enabled.

BEGIN/COMMIT/ROLLBACK use one fixed shared snapshot. Transactions are serialized:
a transaction owns the store, other connections receive DatabaseBusy rather
than seeing uncommitted changes. Snapshot copying occurs on transaction begin,
not ordinary statements. PostgreSQL errors mark an active transaction failed
until rollback; disconnect and idle expiry roll back abandoned transactions.
This is a bounded isolation mechanism, not MVCC or concurrent write transactions.

Joins, aggregates, general expressions, foreign keys, secondary indexes,
persistence and complete SQL-standard semantics are future work.

## Memory and execution

The database, primary-key hash indexes, transaction snapshot, connection slots,
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
are valid. LIMIT without ORDER BY stops scanning once enough rows match.
Deletion compaction and primary-key changes rebuild index references. General
filters remain scans, and ordering currently uses bounded insertion sorting.

## Layers

| Layer | Responsibilities |
| --- | --- |
| Sig | Compiler, native build, formatting, process and standard I/O primitives |
| ZPM Layer 0 | `sql`: parser/storage/indexes; `db-store`: shared state and isolated snapshots |
| ZPM Layer 1 | PostgreSQL/MySQL/TDS wire sessions; nonblocking native TCP primitives |
| RAMSQL | Public facade, listener defaults, reactor policy, capacity profile, integration tests and benchmarks |

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
results. The network benchmark includes TCP, protocol handling and Python
client validation overhead; sequential and pipelined costs are reported
separately. Results include machine/build metadata and workload bounds.
They are measurements of this implementation, not comparisons to PostgreSQL,
MySQL or SQL Server. See the checked-in report for the measured environment.

MIT License — SB0 LTD.
