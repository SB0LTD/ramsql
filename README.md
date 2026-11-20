# RAMSQL

**An in-memory SQL server built entirely in allocator-free Sig. PostgreSQL first.**

RAMSQL aims to be a fast, predictable RAM database with familiar SQL clients and
multiple dialects. Its engine, shared transactions, persistence and wire codecs
are real implementations. There is no SQLite backend, database subprocess,
query-result mock, heap allocator or garbage collector in the runtime.

**0.3.0-beta.3 is for controlled beta testing, benchmarking and review. It is not
a production PostgreSQL drop-in replacement.** PostgreSQL is the default protocol
and semantic reference. MySQL and SQL Server/TDS are experimental adapters over
the shared SQL subset. Supporting all common SQL dialects is the
[roadmap](ROADMAP.md), not a current capability claim.

## Quick start

Download a hosted archive from [Releases](https://github.com/SB0LTD/ramsql/releases)
and inspect its bundled provenance and `--capabilities` output. Each CI
prerelease identifies its exact source and qualified runtime features.
Inspect the machine budget before opening a listener.
```sh
./ramsql --machine-capacity
./ramsql --machine-capacity --memory-budget 8589934592 --os-headroom 1073741824
```

The report shows available physical/commit/address-space memory, observed CPU
constraints, headroom and proposed storage/execution shares. It creates no WAL
or listener. `startup_region_bytes` includes page-rounded storage, query workspace
and client metadata, plus any reactor stack allowance charged at startup.
`startup_fits` compares that charge with the sampled `new_region_bytes` available
to `--serve`. `legacy_startup_bytes` retains the previous unrounded estimate.
`pressure` indicates existing process commitment above the planner budget.
The shared byte ledger charges client result and response buffers when clients
connect and worker read contexts when first used. The server refreshes its
observations at admission, stops new grants under pressure and keeps live data
and buffers owned. PostgreSQL startup returns `53200` when the memory
budget cannot admit another client, and `53300` for the configured connection
limit. Failed page commitments return their grants.

Detection respects visible Linux cgroup-v2 ancestors and address limits, Windows
CPU sets and immediate job limits, and a macOS free/file-cache memory estimate.
Legacy/hybrid cgroup-v1 and native SB0 resource probing are not implemented;
hidden ancestor constraints and some OS scheduling partitions remain observation
gaps. The report exposes coverage instead of inventing unrestricted resources.
SQL row and schema capacities remain explicit. On supported hosted systems,
multiple workers can serve concurrent PostgreSQL simple and extended autocommit reads.

Download your platform archive from [Releases](https://github.com/SB0LTD/ramsql/releases),
check it against the release's `SHA256SUMS.txt` and extract it. Packages include
the executable, documentation and provenance. Python is only needed for optional
setup/test tools; the database executable requires neither Python nor .NET.
On Windows replace `./ramsql` below with `.\ramsql.exe`.
These commands apply to hosted archives; native SB0 transport differences are listed below.

```sh
./ramsql --version
./ramsql --capabilities
./ramsql --demo
./ramsql --memory --insecure-local --port 5432 --rows 4096
```

The last command starts a disposable, unauthenticated loopback-only server.
Connect from another terminal using PostgreSQL's `psql`:

```sh
psql "host=127.0.0.1 port=5432 user=postgres dbname=ramsql sslmode=disable"
```

```sql
CREATE TABLE readings (id INT PRIMARY KEY, value INT, label TEXT);
INSERT INTO readings VALUES (1, 42, 'sensor-a'), (2, 17, 'sensor-b');
SELECT * FROM readings ORDER BY value DESC LIMIT 10;
BEGIN;
UPDATE readings SET value = value + 1 WHERE id = 1;
COMMIT;
```

This is actual SQL over TCP. All clients in a process share the store.
`--memory` loses data on exit. Basic `psql` connection/query use is supported;
full discovery commands, ORM discovery and PostgreSQL utilities are not generally
compatible. The default database name is `ramsql`.

## Authenticated persistent beta

Create a verifier with the packaged helper, which prompts twice and refuses
overwrites. Passwords never enter argv. The helper currently accepts printable
ASCII passwords; provision other passwords with PostgreSQL tooling. Protect the
verifier with filesystem permissions/Windows ACLs.

```sh
python create_verifier.py --output postgres.verifier
./ramsql --data ramsql.wal --create-data --rows 4096 --versions 8192 \
  --auth-user postgres --auth-verifier-file postgres.verifier
```

`--create-data` is for the first start only. Later starts omit it, so a typo in
`--data` or a wrong working directory fails with `DataFileMissing` and the
absolute path instead of creating a new, empty database. Startup prints the
absolute data file path to stderr.

```sh
psql "host=127.0.0.1 port=5432 user=postgres dbname=ramsql sslmode=disable" -W
```

From source, use `tools/create_verifier.py` and `sig-out/bin/ramsql` (add `.exe`
on Windows). Default startup requires SCRAM and persists to an existing
`ramsql.wal`.
Recovery validates the WAL before listening. Commits flush synchronously before
publication/acknowledgement; complete corruption fails closed. A failed write
or flush disables writes until recovery.

For PostgreSQL transaction coordinators, start durable mode with
`--max-prepared-transactions N` to enable `PREPARE TRANSACTION`,
`COMMIT PREPARED` and `ROLLBACK PREPARED` for permanent-table DML. The default
is zero. Prepared changes and locks survive a restart. Keep the limit at or
above the number of unresolved prepares when reopening or backing up the WAL.
Prepared DDL, serializable transactions and prepared-transaction catalog
inspection remain outside this beta scope.

Use `--database-cluster` to serve independent PostgreSQL databases. Start with
`--create-data` and a new data path prefix, then connect to `dbname=postgres`
with the configured SCRAM login. The mode supports `CREATE DATABASE`,
`DROP DATABASE` and discovery through `pg_database`. Each database has its own
WAL and storage budget. This beta mode supports four databases and one reactor.
The single-WAL offline backup command is unavailable in cluster mode.
[PostgreSQL cluster operations](POSTGRES_CLUSTER.md)

All listeners bind to 127.0.0.1. The PostgreSQL TLS preview supports TLS 1.3,
SCRAM-SHA-256-PLUS and explicit native workers. Supply a DER P-256 server
leaf and its 32-byte private scalar together with the SCRAM verifier.

```sh
./ramsql --data ramsql.wal --auth-user postgres --auth-verifier-file postgres.verifier \
  --tls-cert-der server.der --tls-key-raw server.scalar --workers 4
psql "host=localhost hostaddr=127.0.0.1 user=postgres dbname=ramsql \
  sslmode=verify-full sslrootcert=issuer.pem gssencmode=disable channel_binding=require" -W
```

The certificate must cover the client hostname. Protect the private scalar
with filesystem permissions. TLS auto-selection uses one worker. Encrypted
cancellation needs a free client slot. The preview currently excludes the
shared read cache, cluster mode and secondary protocol listeners. See the
[TLS configuration and tested scope](docs/POSTGRES_REQUIRED_TLS.md).

MySQL and TDS require explicit insecure-local mode. Durable single-database
PostgreSQL mode supports opt-in checkpoint publication and WAL rotation.
[Checkpoint setup, scope and recovery](OPERATIONS.md#live-checkpoint-generations)

Retired generations require offline cleanup. Online backup, replication and
upgrade migration remain open.

To run the PostgreSQL listener under a catalog role, create a `LOGIN` role and
grant its privileges using the bootstrap connection. Stop the server, then
restart it on the same WAL with `--auth-catalog-role`, the exact role name in
`--auth-user`, and that user's SCRAM verifier in `--auth-verifier-file`.
A user-created role must exist, allow login, and have no finite connection limit.
The built-in `ramsql` role is also eligible and retains bootstrap privileges.
For multiple roles, initialize the same WAL and create each login role using
the bootstrap connection. Create a verifier for each role, then combine them:

```sh
python create_verifier.py --output roles.credentials \
  --credential reporting reporting.verifier --credential ingest ingest.verifier
./ramsql --data ramsql.wal --auth-credentials-file roles.credentials
```

The list uses exact role names and requires a common SCRAM salt length and
iteration count. The helper generates a key in the credential file for stable
failed-authentication challenges across restarts. Protect the whole file like
the individual verifiers. Changes to the file take effect after restart.
See [operations](OPERATIONS.md) for backup, restoration and failure handling.

## Client and SQL compatibility

| Interface | Independently tested client | Current scope |
| --- | --- | --- |
| PostgreSQL v3, default | psycopg 3/libpq | SCRAM; simple/extended queries, prepared statements, portals, text/binary values, COPY subset and cancellation |
| MySQL protocol 10 | PyMySQL | Empty-password local trust; COM_QUERY, INIT_DB, PING; text results; no binary prepared statements |
| TDS 7.2–7.4 | python-tds | Empty-password local trust; SQL batches, typed results, transaction manager and bounded sp_executesql RPC |
| Embedded Sig | `Database(Config)` | Caller-owned results/storage; serialized calls and interleaved READ COMMITTED transactions |

```sh
./ramsql --memory --insecure-local --mysql-port 3306 --mssql-port 1433
```

This serves PostgreSQL:5432, MySQL:3306 and TDS:1433 from one store. MySQL/TDS
use database `ramsql` and an empty password; usernames do not create roles.
There is no Oracle, SQLite, DB2 or other dialect endpoint in this beta.

Implemented SQL includes typed CRUD/RETURNING, primary keys, defaults/generated
columns, sequences, joins, aggregates, CTEs/set operations, savepoints and
transactional DDL. Development adds PostgreSQL ROUND over numeric and float8
values, including aggregate expressions. The reporting candidate adds temporal
`to_char`, ranking/aggregate windows and flat `ROLLUP`/`GROUPING`. The follow-up
candidate adds bounded ROWS/GROUPS, integral-key RANGE, exclusions and aggregate
FILTER; named windows and the remaining built-in window functions stay open. See the
[reporting qualification record](compatibility/REPORTING_SQL_PROGRESS.md) for
supported forms, remaining gaps and pending original dbMeter validation.
Isolation is READ COMMITTED only. Secondary/foreign-key
indexes, complete window coverage, complete catalogs, stored procedures/extensions and
full dialect semantics are missing. Some MySQL/TDS types are rendered as text.
Read the [SQL reference](SQL_REFERENCE.md) and [usability audit](compatibility/BETA_USABILITY.md)
before importing a schema; wire compatibility does not establish dialect parity.

## Concurrent clients

Eligible PostgreSQL listeners size their read-worker pool from the process's
native CPU affinity or quota, the maximum admitted clients and the startup
memory grant. The pool can grow to the configured connection ceiling. The estimate
includes a stack, query scratch and one client response per selected worker.
`--workers N` fixes the count for controlled deployments or benchmarks.

```sh
./ramsql --memory --insecure-local --max-clients 32 --workers 4
```

In beta.3, idle autocommit PostgreSQL simple and extended-protocol `SELECT`/`WITH`
queries can execute concurrently using
parser/execution storage owned per worker and results owned per client. Writes,
transactions, COPY, suspended portals and dynamic time-zone resolution retain exclusive execution.
A reader/writer gate prevents publication or reclamation while readers execute.
Worker read storage is reused between clients and released when its reactor
becomes idle or stops. It is charged to the machine budget; insufficient headroom
falls back to exclusive execution. macOS/native SB0 still use one worker.
Check `parallel_simple_reads`, `parallel_extended_reads`, `sql_execution_workers` and
`native_workers_available` in `--capabilities`. This is inter-query concurrency;
one query does not yet distribute its work across cores. Benchmark improvements
must be measured for the actual workload.

## Bounded joined aggregates in development

Grouped/scalar aggregates over a physical fact table and primary-key inner joins
now stream into group states instead of materializing every intermediate tuple.
A checked live test loads 100,000 fact rows and two dimensions and runs with
`--work-rows 32 --result-rows 16`, using one and four workers. Run
`python tools/test_streamed_joins.py --binary /path/to/ramsql` to reproduce it.

The [matched revision comparison](benchmarks/streamed-join-wire-20261027/README.md)
measures 34.6 ms/query before and 16.1 ms/query after on one Windows host.
Further [profile-guided input changes](benchmarks/final-joined-read-20261027/README.md)
reduce the final matched median to about 7.0 ms while preserving work-budget
checks. Current committed integral inputs gather referenced columns; old
snapshots, own writes and referenced non-integral types retain versioned rows.
Exact integral `COUNT(DISTINCT column)` can also stream into caller-backed
per-group bitmaps, with general exact fallback for unsupported or unaffordable
domains. The reviewer's million-row sales subset now passes all seven checks,
including distinct-customer counts, CTEs and selective three-way joins. These
checks pass with the default text-128 build on Windows and an explicit text-32
build on Linux, using unequal runtime table banks. This is not 100M-row capacity.

This compares RAMSQL revisions. Outer/non-key joins, other DISTINCT shapes and
high-cardinality groups retain their existing bounded paths; physical SQL storage
is still inline. Neither this test nor the packed-column tests demonstrate a
100M-row SQL server or a PostgreSQL/IRDB win.

## Inspect capacity before loading

Allocator-free means explicit owned storage, not unlimited RAM. **Stored rows
are not permanently limited to 512:** hosted `--rows`/`--versions` bind larger
OS page buffers; checked benchmarks store one million rows. The stock binary
defaults to **4,096 stored rows/table, 8,192 returned rows/statement, 4,096 workspace
rows, 8 columns, 128-byte text and 16 clients**. Use `--work-rows`, `--result-rows`
and `--max-clients` (up to 60) without rebuilding. `--table-rows n,n,...` and
`--table-versions n,n,...` assign independent budgets to table slots in creation
order; supply one value for each compiled table slot. `--rows` is the uniform
fallback. Text/schema/parser limits still require a build profile. There is no automatic
disk spill or arbitrarily large result stream.

```sh
./ramsql --capabilities --table-rows 1000000,10000,64,1,1,1,1,1 \
  --work-rows 8192 --result-rows 4096 --max-clients 32
```

This reports effective storage/backing bytes and compiled SQL/protocol budgets
without allocating the database, touching a WAL or opening listeners. Backing
bytes are not total RSS. See [CAPACITY.md](CAPACITY.md) for defaults, hard bounds,
error behavior and tuning. A larger result/schema profile can be built with:

```sh
sig build -Doptimize=ReleaseFast -Dclients=4 -Dtables=4 -Drows=1024 \
  -Dcolumns=16 -Dresult-rows=1024 -Dwork-rows=1024 -Dtext-bytes=256
./sig-out/bin/ramsql --capabilities
```

That profile is tested with a 12-column table, a 600-row result and 200-byte text.
Inline cells, workspaces and per-client result/response buffers grow with the
profile. The stock hosted connection ceiling is 60. An optional source build
with `-Dhosted-client-ceiling=128` supports `--max-clients 128` for PostgreSQL.
Its budget includes transaction slots and peak client memory. Cluster mode,
secondary listeners and prepared transactions have separate admission limits.

### Experimental typed bank memory profile

Hosted release binaries include integral bank support. For a source build, use
`-Dintegral-version-banks=true`. Set each typed slot's word width with
`--table-integral-words`. Add `--typed-bank-memory-profile` to omit the
committed column projection and secondary hash regions from those typed slots.
Queries use exact committed summaries or ordinary MVCC execution. Materialized
joins, sorts and other general execution can exhaust the supplied workspace.
The profile derives its reference count from `--work-rows`. Set `--query-refs N`
with the profile to choose an explicit positive reference budget.

`--capabilities` and `--machine-capacity` show the effective `table_storage`,
`query_refs`, backing bytes and lazy worker payload. `--machine-capacity` also
shows the page-rounded startup charge and whether it fits the sampled grant.
Compare these values with the same table, version, workspace and worker options
planned for the actual run. The profile is experimental. Packed tables use
restricted integral/DATE schemas and the current packed implementation gates
ALTER-family operations. The [100M-row qualification plan](compatibility/MACHINE_CAPACITY_AND_100M_PLAN.md)
tracks real load and query gates separately from capacity arithmetic.

## Measured performance

The reviewer suite is available as sibling `dbMeter`. CI retains its
original JDBC drivers, SQL, generated data, correctness checks and reports.
The current integration campaign passed every correctness and capacity
check, led median throughput in all 16 cases, and met the complete throughput,
trial-separation and p99 criteria in **12 of 16 cases**. All four prepared-key
cases missed the performance criteria, with 1.08x to 1.54x the PostgreSQL rate.
The 100K-row eight-client lookup had 94 microseconds p99 against PostgreSQL at
61 microseconds. This comparison used the explicit 256 MiB RAMSQL read cache.
Every result applies to the source and binary hashes in the run.
[Full CI campaign](https://github.com/benor1470/dbMeter/actions/runs/44347340190)

On the isolated packed-filter candidate, the same 256 MiB budget improved
the original million-row filter from 810 to 8,840 queries/s at one client
and from 2,524 to 15,649 at eight. It retained the original correctness
and capacity checks. Both candidate and control ran three original-duration
trials on the same runner. [Paired CI](https://github.com/benor1470/dbMeter/actions/runs/44259236510)

The optional 32-byte version headers saved 1.60 GB on the durable 100M-row
integral fact-table fixture with two dimensions. Both layouts recovered
the same 6,771,074,198 WAL bytes and reproduced the query results.
[Capacity and recovery proof](https://github.com/benor1470/dbMeter/actions/runs/44259619802)

Earlier reports record the baseline limitations and the changes that led
to these results. The [reviewer baseline](benchmarks/dbmeter-reviewer-20261027/REPORT.md)
and [five-million-row baseline](benchmarks/dbmeter-full-five-million-20261028/REPORT.md)
retain their original failures and losses.
To reproduce size-scaling runs after building dbMeter, installing its configured
drivers and running its original generator:

```sh
python tools/benchmark_dbmeter.py --dbmeter ../dbMeter \
  --binary /path/to/ReleaseFast/ramsql --sizes 512 100000 \
  --threads 1 8 --workers 4 --work-rows 4096 --kinds pk filter topn \
  --output /path/to/fresh/results
```

The harness rejects changes to dbMeter's tracked sources/configuration, checks
its original CSV expectations, and retains errors instead of scoring failed
loads as wins. Use an isolated PostgreSQL test cluster: its original setup drops
the test schema. JVMs use the same explicit heap limit across engines. Reference
servers must be prepared separately; `--postgres-url` and `--irdb-url` reuse their
original configurations and SQL. No local builds/profilers run during timing.

The current development executor's [matched joined-read experiment](benchmarks/final-joined-read-20261027/README.md)
reduces median latency from 33.98 to 6.95 ms (4.89x) for a checked 100k-row
three-table aggregate. A fresh [IRDB comparison](benchmarks/guarded-reference-20261027/README.md)
and its [remaining cases](benchmarks/guarded-reference-remaining-20261027/README.md)
pass correctness and the finite speed screen in all ten native API cases at
100k rows. Both engines receive SQL strings in those earlier reports. The
dbMeter report above covers JDBC prepared-query comparisons, and the
[qualification plan](compatibility/GAP_CLOSING_PLAN.md) tracks the current
all-case gate and the exact configurations considered there.

The [production workload report](benchmarks/production-20261026/REPORT.md)
compares checked native SQL calls against IRDB 1.10.821 with shuffled keys,
nullable payloads, grouping beyond summary capacity and genuine writes:

- All 21 read comparisons passed the stated screen at 10k/100k/1M rows.
- At 1M rows, median leads were 2.76x for top-k sorting, 4.99x for filtered SUM,
  1.99x for grouping and 1.37x for filtered grouping.
- Both mixed-write ratios passed at every size. All ten additional 512-byte
  payload/2,048-group comparisons passed.
- The original long million-row churn test timed out in IRDB: that matrix is
  **29/30**, not a complete pass. A shorter separately labeled trace passed.

These measurements predate beta.1 and identify exact source/binary hashes. They
are volatile, single-worker native comparisons, not network/durability or
universal superiority claims. The pipeline separately benchmarks the **exact
Linux release binary** against native PostgreSQL with durable/client workloads
and ships raw results, including losses. PostgreSQL has led durable/concurrent
writes in earlier runs. Read the evidence for the actual release you evaluate.
See [reproduction commands](benchmarks/production-20261026/README.md) and
[profiling tools](tools/PROFILING.md).

## Build, test and review

Use the complete [Sig 0.6.5 toolchain](https://github.com/SB0LTD/sig/releases/tag/sig-0.6.5-zig0.17.0.3c72687).
`sig build` resolves ZPM through its immutable archive and SHA-256 pin.
Prepare the pinned public runtime/build support from
[`toolchains/sig-runtime.json`](toolchains/sig-runtime.json) first. This verifies
the released standard-library source files byte-for-byte and leaves the installed
compiler/toolchain untouched. Only the selected library and build-support trees
are extracted; compiler repository test fixtures are not package dependencies.

```sh
python tools/prepare_sig_runtime.py --installed-lib /path/to/sig-0.6.5/lib \
  --output .sig-cache/prepared-sdk
export SIG_LIB_DIR="$PWD/.sig-cache/prepared-sdk/lib"
```

In PowerShell use `$env:SIG_LIB_DIR = "$PWD/.sig-cache/prepared-sdk/lib"`.
Choose a new output directory when preparing a different SDK. Windows/Linux
default to no libc; macOS source builds require `-Dlink-libc=true`. Release builds
select explicit portable CPU targets, rather than the build machine's native ISA.

```sh
sig build -Doptimize=ReleaseSafe
sig build test -Doptimize=ReleaseSafe
sig build run                       # terminating example
python -m pip install -r tools/requirements-test.txt
python tools/test_cli.py
python tools/test_server.py
python tools/test_clients.py
python tools/test_auth.py
python tools/test_persistence.py
python tools/test_capacity.py
python tools/test_beta.py
python tools/test_reviewer_runtime.py
python tools/test_workers.py
```

Six hosted targets: x86_64/AArch64 Linux GNU, macOS and Windows. Native AArch64
SB0X (EL0 process channel) and SB0K (EL1 UART) use trusted, memory-only transport:
**no hosted TCP, WAL or SCRAM**. QEMU ABI/transaction tests are distinct from
testing a complete SB0S distribution.

Publication waits for hosted tests, exact packaged binary execution on all six
hosted targets, PostgreSQL 16/18 qualification, native SB0X/SB0K execution and
bundle checksum/provenance attestation. Synthetic unit-test fixtures never count
as executable release validation. See [release notes](compatibility/RELEASE.md),
[changelog](CHANGELOG.md), [roadmap](ROADMAP.md) and [audit](compatibility/BETA_USABILITY.md).

| Layer | Responsibility |
| --- | --- |
| Sig | Compiler, build and standard primitives |
| ZPM core | SQL, storage/MVCC, locks, summaries, compact scans and change records |
| ZPM platform/protocol | Wire codecs, files, TCP, pages, entropy and clocks |
| RAMSQL | Server policy, persistence lifecycle, profiles, integration tests and releases |

Report bugs with the tag/target, `--capabilities`, client version, minimal SQL
and expected/actual results. Include hashes, profile, seed, size, durability,
concurrency, raw trials and correctness checks in performance reports. Never
include passwords, verifiers or private data.

MIT License — SB0 LTD.

## License

RamSQL is proprietary, source-available software owned by SB0 LTD. It is free for personal use and for research and teaching. Any business, production, hosted, embedded or other commercial use requires a written license from SB0 LTD, the only party that can grant one (licensing@ramsql.com). See [LICENSE](LICENSE). It is not open-source software.
