# RAMSQL gap-closing plan

## Stock migration acceptance

The separate whole-database follow-up, RAMSQL `f051b2e`, passed all
27 stock checks on PostgreSQL 16 and 18. Its fixture preserves schema
ownership, grants, a table comment, rows and defaults in both directions,
and repeats the import assertions after WAL reopen. Large-object creation,
data, ownership and privilege restoration remain open.
[Whole-database qualification](https://github.com/SB0LTD/ramsql/actions/runs/44958574825)

The scoped identity follow-up passed 28 checks on PG16 and PG18. It compares
sequence names across schemas, starting values, catalog OIDs, and recovery
after reopening the WAL. It checks that dropping one schema leaves the other
sequence intact. The next candidate exposes live sequence state and supports
adding an identity to an existing NOT NULL integer column. Its local change
log suite passed 1,109 assertions, covering rollback, existing rows and WAL
replay. Local stock PG16 passed all 30 checks at RAMSQL `453d4d1`, including
identity archives in both directions, primary-key enforcement and the next
generated value after WAL reopen. PG16 and PG18 CI must qualify that fixture.
[Scoped identity qualification](https://github.com/SB0LTD/ramsql/actions/runs/44958119185)

Scoped standalone sequence DDL now has separate WAL coverage for equal
names in different schemas, cached values, ALTER, explicit DROP and
schema CASCADE. Local stock PG16 passed all 32 checks with ZPM
`2efbbcc3`. The added tests verify standalone sequence values after WAL
reopen and exercise stored generated columns in both directions.

Local stock PG16 passed 33 checks at `5a60637`. The additional view fixture
restores chained views after their base tables and verifies their rows
after WAL reopen. The next candidate expands that fixture to public
relations originally referenced without a schema and to CTE names
that shadow a public relation. General view and rewrite-rule deparsing
remain open. Stock PG16 and PG18 both exposed the 128-byte stored view
definition limit when PostgreSQL added formatting around a short CTE.
The compact-view candidate passed that original fixture locally on both
versions by reducing lexical whitespace and comments when needed. It
preserves string and identifier spelling. Definitions whose SQL tokens
exceed the budget still need variable-length storage and wire output
without enlarging every stored data cell.

The view-column follow-up preserves quoted and computed output names from a
view's column list when stock tools serialize supported simple projections.
The released artifacts passed 39 stock checks in both directions in
PostgreSQL 16 and 18 CI. The dependent-view fixture checks column names,
values and WAL reopen. The qualified source is on both main branches.
General view deparsing and CREATE OR REPLACE VIEW parity remain open.
[Exact release qualification](https://github.com/SB0LTD/ramsql/actions/runs/45051553539)

The released materialized-view fix permits definitions over unpopulated
sources during stock restore. The dependent view becomes readable after
the archive refreshes its source. All 40 stock checks passed on PostgreSQL
16 and 18 in hosted CI. The archive fixture preserves rows and deliberately
unpopulated views in both directions, including after reopening the WAL.
[Materialized-view release qualification](https://github.com/SB0LTD/ramsql/actions/runs/45085219524)

The global-role follow-up exercises unmodified `pg_dumpall --roles-only
--no-role-passwords` output with stock `psql`. The fixture checks quoted
role names, login attributes, grantor names and per-membership ADMIN,
INHERIT and SET options in both directions and after reopening the RAMSQL
WAL. Full cluster archives, credential export, role settings and parameter
grants still need qualification or implementation.

The quoted-archive follow-up runs both stock tools with
`--quote-all-identifiers`. It checks the SQL emitted by `pg_restore` and
restores a schema with an embedded double quote in a column name, an
index, a foreign key, and text data. The fixture checks constraint
enforcement after restoring and after reopening the RAMSQL WAL.
Complete deparsing of stored views and expressions, large objects, and
cluster-wide migration remain open.
[Quoted archive qualification on PostgreSQL 16 and 18](https://github.com/SB0LTD/ramsql/actions/runs/45282665508)

The role-default follow-up extends stock `pg_dumpall` archives with all six
supported boolean settings, including `quote_all_identifiers` and
`check_function_bodies`. It verifies the saved
values on the restored server and after WAL reopen. The SCRAM integration
tests check role-specific login, client override precedence and RESET.
Database-specific and non-boolean role settings remain open.
[PG16 and PG18 role-default qualification](https://github.com/SB0LTD/ramsql/actions/runs/45382103314)

The globals follow-up runs stock `pg_dumpall --globals-only` both ways
with built-in tablespaces, quoted names, passwordless roles, memberships
and defaults. Both stock versions passed all 43 checks. User-defined
tablespaces and full cluster archives remain open.
[PG16 and PG18 globals qualification](https://github.com/SB0LTD/ramsql/actions/runs/45383277235)

The original PG16 and PG18 full-cluster restore stopped at its first
connection to `template1`. The database-DDL follow-up qualified the archive's
UTF-8, C-locale database creation form and the existing owner declaration
against the durable registry. Both stock versions passed all 44 checks,
including verification of the restored child after WAL reopen.
[PG16 and PG18 database declaration qualification](https://github.com/SB0LTD/ramsql/actions/runs/45384730882)

The cluster now exposes the pristine UTF-8, C-locale `template0` as a
non-connectable template in `pg_database`. CREATE DATABASE explicitly
selecting the template provisions an independent database without storing
or replaying an empty template WAL. Existing user databases named
`template0` keep their original OID, data and admission. Explicit template
selection fails if that legacy name is occupied. A separate candidate
clones a named, quiescent database using its verified WAL. The destination
has a distinct identity and becomes visible after the new WAL is flushed
and replayed. Existing prepared decisions keep their history and live
prepared transactions fence the copy. PG16 and PG18 each passed all 45
checks with that candidate.
[Named-template qualification](https://github.com/SB0LTD/ramsql/actions/runs/45390681198)

The writable `template1` candidate stages its WAL identity on first
use and recovers a torn catalog publication without accepting an
unrelated WAL. Stock PostgreSQL 16 and 18 CI each passed 47 checks,
including unmodified three-database `pg_dumpall` archives both ways and
data in both built-in connectable databases. The native fixture excludes
its extra harness database and checks the single expected duplicate
bootstrap-role error.
Cross-database roles, broader locale and tablespace support, and
large-object archives remain open.
[Writable-template qualification](https://github.com/SB0LTD/ramsql/actions/runs/45450269339)

The legacy column-option fixture replays unmodified plain and custom
archives written by stock `pg_dump` 18.0 against PostgreSQL 18.7.
The old client includes the backend-owned `pg_attver` option.
The new restore path accepts that reserved option with the usual ALTER
permissions, locking and errors. The fixture checks data, primary keys
and the absence of a portable option after reopening the WAL.
PG16 `psql` reads the plain archive. The custom archive uses format
1.16, which requires the PG18 `pg_restore` lane.
Configurable options such as `n_distinct` remain open.
[PostgreSQL attribute catalog](https://www.postgresql.org/docs/18/catalog-pg-attribute.html)

The composite-key follow-up passed all 34 checks on PG16 and PG18 CI.
Stock restore applies the composite primary key after COPY in the same
transaction. The fixture checks row equality and rejection of duplicate
and NULL key values after WAL reopen. General late composite-key changes
to existing tables remain open.
[Stock PG16 and PG18 composite-key qualification](https://github.com/SB0LTD/ramsql/actions/runs/44962030582)

The next candidate adds a late composite key to an already populated
dense table in a separate transaction. It validates existing rows with
an empty index bank and checks equals on hash collisions. Both PG16 and
PG18 CI passed all 35 checks, covering duplicate rejection during the
alter, successful retry, enforcement and WAL recovery. Integral-bank
rewrites and indexed composite-key lookups at production scale remain open.
[Late composite-key qualification](https://github.com/SB0LTD/ramsql/actions/runs/44962874305)

The multiple-identity candidate passed all 36 stock checks on PG16 and
PG18. The new fixture restores a table with ALWAYS and BY DEFAULT
identity columns in both directions. It verifies the rows and both
owned sequences' values and called flags, including after WAL reopen.
[Multiple-identity qualification](https://github.com/SB0LTD/ramsql/actions/runs/45018672015)

Separate SERIAL fixtures passed both directions with local stock PG16 and
PG18 in `public` and in named schemas. Each fixture restores two owned
sequences, their column defaults and state, and repeats those checks after
WAL reopen. The fixtures exposed missing transactional sequence ownership
and missing catalog dependencies from a default to its sequence. The
candidate passed all 38 stock checks in hosted PG16 and PG18.
[SERIAL archive qualification](https://github.com/SB0LTD/ramsql/actions/runs/45044007672)

In two separate hosted profiles of one-million-row prepared lookups,
the eight-client CPU sample share for buffer-overlap validation fell
from 15.49% to 0.31% after avoiding table-span scans for disjoint
buffers. The hosted samples recorded 199,358 and 227,063 queries/s
with eight clients. The parallel-read contract passed 144 assertions
and 2,400 concurrent queries, including real buffer alias rejection.
The separate runs are diagnostic. The repeated three-engine gate
still determines whether performance qualifies.
[Baseline CPU profile](https://github.com/benor1470/dbMeter/actions/runs/45014975747) ·
[Candidate CPU profile](https://github.com/benor1470/dbMeter/actions/runs/45015989385)

The separate 118-program smoke audit matched all 106 PostgreSQL programs
against native PG18.3 with XML support. Database lifecycle, prepared
transactions and maintenance ran on separate disposable instances. The
audit compares allocated type identities through the live catalog and
preserves raw OIDs in its evidence. All six MySQL programs matched
native MySQL 8.0.36. All six T-SQL programs returned the expected rows
through python-tds. Native SQL Server comparison and the broader
755-record PostgreSQL inventory remain open.

The first full release run passed COPY, native SB0X and SB0K, all hosted
builds and the PostgreSQL release-artifact lanes. Its ARM macOS wire test
failed while filling the connection pool. The revised suite tests the full
slot limit with a smaller per-connection result grant. The separate
machine-capacity test still exercises live SQLSTATE 53200 memory rejection.
[Release matrix](https://github.com/SB0LTD/ramsql/actions/runs/44926870854)

The combined candidate `ac6bd49` passed all 27 stock checks on
PostgreSQL 16 and 18. ARM macOS then passed the wire suite and failed the
separate 32-client reviewer test when a full slot pool and memory pressure
coincided. The revised admission reports SQLSTATE 53300 for a full pool
and SQLSTATE 53200 when a free slot lacks memory for its buffers.
[Combined stock CI](https://github.com/SB0LTD/ramsql/actions/runs/44957490888) ·
[ARM macOS release check](https://github.com/SB0LTD/ramsql/actions/runs/44957495974)

The stock tooling workflow verifies schema ACL metadata, plain and custom
imports in one transaction, constraints after WAL reopen, and outbound
custom dumps restored by native PostgreSQL. Both PostgreSQL 16 and 18 lanes
must pass before merging the integration. The candidate includes a separate
48-column SELECT projection limit, built-in tablespace rows, a typed
foreign-table catalog, and the relation metadata needed by table discovery.

RAMSQL `d1ddbe5` with ZPM `ed00a0a1` passed all 24 stock tooling
checks on both PostgreSQL 16 and 18. It imported native plain and custom
dumps and restored its own custom archive on native PostgreSQL. Each
direction verified rows, defaults, primary keys, foreign keys, CHECK
and NOT NULL. Imports passed the same checks after reopening the WAL.
The selected two-table fixture uses `--no-owner --no-acl`. Whole-database
archive coverage, ACL and ownership restore, and application conformance
remain open.
[PG16 and PG18 stock round trips](https://github.com/SB0LTD/ramsql/actions/runs/44926381895)

On RAMSQL `c175b9f` with ZPM `c8b764ab`, both CI lanes pass
column defaults, stored generated expressions, CHECK and index metadata,
and range and list partition discovery. Plain and custom imports pass
post-reopen row, default and constraint checks on the two-table fixture.
Both outbound dumps passed partition discovery. RAMSQL `ea70fda` then
passed the stock subscription query and reached the operator-family
dependency queries. Its separate subscription probe caught a standalone
database name mismatch with connection aliases. The follow-up fixes that
name mapping and adds the dependency catalogs and custom archive metadata.
On RAMSQL `af7af9a`, outbound dump progressed to target-list
`unnest(setconfig)` for database settings. The next candidate adds
typed row expansion after filtering.
[Partition CI](https://github.com/SB0LTD/ramsql/actions/runs/44924845762) ·
[Subscription CI](https://github.com/SB0LTD/ramsql/actions/runs/44925395409) ·
[Archive metadata CI](https://github.com/SB0LTD/ramsql/actions/runs/44925969411)

## 24 November 2026 million-row lookup qualification

The same-host Linux socket-map experiment passes both original
one-million-row dbMeter lookup cases in the same hosted run. The opt-in
transport caches bounded server reply redirects and revalidates every
client request. It uses ZPM `385d6708`, RAMSQL `6c947808`, and the
original JDBC inputs, timing, correctness and latency checks. IRDB
1.10.821 uses its clustered key index.

| Clients | RAMSQL queries/s | PostgreSQL queries/s | IRDB queries/s | RAMSQL / fastest reference |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 70,894.85 | 33,270.05 | 989.17 | 2.13× |
| 8 | 358,236.55 | 175,375.87 | 1,445.96 | 2.04× |

Both cases passed three 60-second trials per engine, the original trial
separation check, and the p99 threshold. A companion job passed six
stock TLS test runs with 16 immediate reconnects per run and no retries.
The benchmark used an experimental build and opt-in read cache.
[Original dbMeter cases and TLS qualification](https://github.com/benor1470/dbMeter/actions/runs/45382392847)

The published Linux image with the same reply cache passed TLS, with
throughput ratios of 2.03× at one client and 1.75× at eight. Both shards
failed the original capacity probes for 200-byte TEXT, nine columns and
20 additional tables. The hosted packaging candidate adopts the
32-table, nine-column, 200-byte profile and requires a client to verify
those capacities after WAL reopen on the exact release image. Dynamic
schema storage and production performance remain open.
[Published-image qualification](https://github.com/benor1470/dbMeter/actions/runs/45386009603)

The wider source candidate passed both unchanged hosted cases at 2.22×
and 2.88× the fastest reference. The exact package has its own benchmark
run, and the full six-job reviewer workload matrix is tracked separately.
[Wider candidate CI](https://github.com/benor1470/dbMeter/actions/runs/45389122488) ·
[Exact published lookup CI](https://github.com/benor1470/dbMeter/actions/runs/45448387443) ·
[Full reviewer matrix](https://github.com/benor1470/dbMeter/actions/runs/45449777771)

The wider downloadable image passed the three capacity probes and both
original mixed sales workloads. The five-million-row mix reached 21.94×
and 27.43× IRDB at one and eight clients. This run used the opt-in
256 MiB application read cache. The earlier separate-runner lookup
attempt reached 2.26× at one client and 1.84× at eight. The paired
eight-client rerun reached 2.06× PostgreSQL with the source-built image
and 2.04× with the downloaded image, both using the experimental Linux
loopback reply cache. The median of trial p99 values was 52 µs for
each RAMSQL image and 66 µs for each PostgreSQL reference. The
unchanged whole-suite performance gate remains open.
[Paired lookup trial sets](https://github.com/benor1470/dbMeter/actions/runs/45451776681) ·
[Hosted verification of both artifact pins](https://github.com/benor1470/dbMeter/actions/runs/45457930323)

## 20 November 2026 shipped checkpoint

The [beta.3 prerelease](https://github.com/SB0LTD/ramsql/releases/tag/ci-v0.3.0-beta.3-run.44655295188.1) passed the [complete release matrix](https://github.com/SB0LTD/ramsql/actions/runs/44655295188)
and matches the merged source tree. The new checkpoint lane verifies an
acknowledged WAL suffix across manifest publication and the handoff, including
writer fencing on selector rename error. Native SB0X/SB0K and COPY qualification
now pass. The [production gates](release-gates.json) track the remaining scope
for PostgreSQL, security, capacity, backup, operations and physical storage faults.
The cross-engine 118-program smoke result and the 16-case reviewer performance
decision retain their original revision and scope.

## 19 November 2026 integration and benchmark checkpoint

The latest full reviewer dbMeter run passed all original correctness and
capacity checks and scored every one of the 16 cases. RAMSQL led median
throughput in every case and passed the complete significant-advantage gate
in 12. The four prepared primary-key cases remain short of the required 2x
lead over the fastest reference, at 1.08x to 1.54x over PostgreSQL; the
100K/eight-client case also misses the p99 requirement. This run used the
explicit 256 MiB read cache. Its final performance decision is correctly red;
the dbMeter workflow PR remains unmerged.
[Full trials and decision](https://github.com/benor1470/dbMeter/actions/runs/44347340190)

The combined product candidate in PR #87 passed Linux x86-64 and ARM64
integration with real libpq clients: simple/extended queries, 128-client
admission, idle/absolute transaction deadlines, durable replay and TLS
with two and four native workers. TLS includes verify-full and SCRAM-PLUS,
prepared statements, COPY, encrypted cancellation and reciprocal KeyUpdate.
It remains a preview with explicit TLS worker selection, a spare-slot
requirement for cancellation and the documented exclusions for shared cache,
cluster mode and secondary listeners.
[Combined exact-source proof](https://github.com/SB0LTD/ramsql/actions/runs/44346300626) |
[Integration PR](https://github.com/SB0LTD/ramsql/pull/87)

The full release attempt passed its hosted build, artifact execution and
PostgreSQL 16/18 lanes, then failed native SB0 compilation in a new decimal
leading-zero-count operation. It did not publish. Portable scalar numeric
and UTF-8 compatibility fixes are being qualified with actual SB0X/SB0K
images. The existing downloadable prereleases below remain valid for their
own source revisions; they are not this consolidated candidate.
[Full release attempt](https://github.com/SB0LTD/ramsql/actions/runs/44346305282)

ZPM main now contains schema ownership/USAGE/CREATE and ACL mutation checks,
PostgreSQL internal-byte `"char"` metadata, ordinary multi-query batches and
extended Sync/COPY, exact numeric comparisons/hashing, and reusable TLS
primitives. Current schema CREATE tests cover owner and inherited-owner
authority, correct table creator identity and PostgreSQL's distinct behavior
for plain versus inline PRIMARY KEY/UNIQUE creation without schema USAGE.
Broader CREATE variants and delegated CREATE remain gaps.
[Native schema evidence](https://github.com/SB0LTD/ramsql/actions/runs/44347345269) |
[SQL TDD status](https://github.com/SB0LTD/zpm/blob/main/docs/SQL_CONFORMANCE_TDD_STATUS.md)

Immediate acceptance work:

| Gap | Required evidence before closing |
| --- | --- |
| Consolidated release | Portable current-core numeric/UTF-8 proof, complete hosted and native matrix, published prerelease and independent public-asset checksum audit |
| Interactive simple-query COPY | Original COPY IN/OUT cases, transaction/error-tail semantics, proven slow-reader boundary and actual SB0 stream execution on the exact composed source |
| Typed bounded text | Current-main composition of PR #270, malformed UTF-8/NUL rejection, raw internal-char casts retained, dense/typed copy and WAL replay |
| Checkpoint startup | Current table, primary-index and constraint identities/counters preserved; native generation selection and existing-writer-lock recovery; explicit rejection of unsupported catalog state |
| Stock PostgreSQL migration | Expand the qualified fixtures to large objects, general late/composite keys, dependent schema objects, and full database and cluster migration |
| Prepared-key performance | Four unchanged reviewer cases pass all trial separation, 2x and p99 requirements in CI, with correctness still green |

The production gate remains open for sustained wide-schema/mixed-write scale,
bounded online checkpoint/rotation, upgrade and power-loss evidence, HA and
complete application/dialect compatibility. The last recorded cross-engine
smoke inventory is 118 programs, not the entire 755-record PostgreSQL feature
inventory. Each historical checkpoint below retains its original source scope.

## 18 November 2026 qualification checkpoint

The COMMIT ownership fix is available in
`ci-v0.3.0-beta.3-run.44252147423.1`. The prerelease passed all eight package
targets, both native SB0 boot tests, and all four exact Linux PostgreSQL 16
and 18 qualification lanes. An independent download matched all 29 public
assets to the assembly archive and recomputed all 28 listed checksums. Main
merge `b33762c6` differs from the release source only in two planning
documents. The prerelease manifest has `production_ready: false`.
[Current beta.3 downloads](https://github.com/SB0LTD/ramsql/releases/tag/ci-v0.3.0-beta.3-run.44252147423.1) ·
[Release matrix](https://github.com/SB0LTD/ramsql/actions/runs/44252147423) ·
[RAMSQL integration](https://github.com/SB0LTD/ramsql/pull/79)

Beta.3 prerelease `ci-v0.3.0-beta.3-run.44202780430.1` published automatically
after the complete matrix passed. The source tree matches RAMSQL main merge
`3d1b5a0e58c4b3e5a2b60f0c1e768d960f3e5726`. All 29 release assets were
downloaded and the 28 listed checksums recomputed. The bundle includes six
hosted Linux, macOS and Windows artifacts and actual SB0X and SB0K boot images.
The exact packaged Linux binary passed the four PostgreSQL 16 and 18 suites
in memory and durable SCRAM modes. The separate release smoke test found a
1.44× single-client point-read lead against durable PostgreSQL and a 0.73×
relative insert rate. The package is available for beta testing. The production
gate and the formal dbMeter performance gate remain open.
[Beta.3 downloads](https://github.com/SB0LTD/ramsql/releases/tag/ci-v0.3.0-beta.3-run.44202780430.1) ·
[Exact release evidence](https://github.com/SB0LTD/ramsql/actions/runs/44202780430)

The consolidated `1b1a3e747dc4fb1c093ee93911ce453a380c33d8` revision matched all
118 native inventory programs again and all 331 PostgreSQL 18.3 durable SCRAM
cases. The Windows packed build passed 16 two-worker wire cases, 8,598
authentication assertions and 83 persistence cases. The Linux build recovered
the same 100M-row fixture from its 6.77 GB synchronous WAL in 139.066 seconds.
It reproduced the original results before and after restart and held peak RSS
to 13.384 GB during recovery. [SQL Server evidence](https://github.com/benor1470/dbMeter/actions/runs/44196211165) ·
[100M evidence](https://github.com/benor1470/dbMeter/actions/runs/44196210578) ·
[Consolidated matrix](https://github.com/SB0LTD/ramsql/actions/runs/44195561261)

Hosted isolation exposed two reads that bypassed the lock for an uncommitted
DROP TABLE. `da0b68f1cf3cd6f64d2de2464f9d645b68fb1c31` routes reads through
the lock queue when an AccessExclusive owner or waiter is present. The Windows
build matched all 46 native PostgreSQL 18.3 durable isolation schedules and
passed 17 two-worker wire cases with 4,539 assertions. The test covers waiting
behind queued DDL, rollback restoring visibility and prepared reads returning
`42P01` after committed DROP. The same revision matched all 118 native
inventory and 331 durable SCRAM semantic cases. Its exact native SB0X and SB0K
images passed 874 and 860 assertions. The Linux 100M fixture again recovered
the exact 6.77 GB WAL and reproduced every result in 140.389 seconds.
[Table-lock change](https://github.com/SB0LTD/zpm/pull/223) ·
[Replacement release matrix](https://github.com/SB0LTD/ramsql/actions/runs/44202780430) ·
[100M fixture](https://github.com/benor1470/dbMeter/actions/runs/44203961680)

RAMSQL `0af167b420edd605597bf5bb7c25dc0fc9483136` with ZPM
`aa0250509ebb1befb9d9864bd98581f7e695f970` matched all 118 original wire
inventory programs. PostgreSQL 18.3 matched all 106 with durable cluster mode
and prepared transactions enabled. MySQL Community 8.4.13 and SQL Server 2022
CU28 matched all six programs each. Windows x64 was used for PostgreSQL and
MySQL and Linux x64 for SQL Server. [Release candidate](https://github.com/SB0LTD/ramsql/pull/70) ·
[Native SQL Server comparison](https://github.com/benor1470/dbMeter/actions/runs/44185665967)

The Linux x64 build of that revision also recovered a 6,771,074,198-byte
synchronous WAL in 114.628 seconds after the process was killed. The fixture
contains 100M six-column integral fact rows and two dimension tables. Recovery
verified full and selective grouped three-way joins, ordered results, concurrent
key probes and the absence of an uncommitted update.
[100M recovery](https://github.com/benor1470/dbMeter/actions/runs/44185666188)

The separate PostgreSQL 18 durable SCRAM suite matched 327 of 331 cases on that
revision. Four `regclass` catalog cases failed when admitted to a detached
reader. The replacement `5a51a6e1f5304a4c6228210d7538440d77f177df` routes
statements that need catalog and session hooks to the owning session. Its
Windows build matched all 331 unchanged cases against native PostgreSQL 18.3
with durable SCRAM on RAMSQL and immediate-mode reference restarts. It passed
15 two-worker packed-enabled wire cases with 4,286 assertions. In a separate
full CI run, an old workspace contract still expected optimized projected joins
to fail. The corrected contract verifies eight surviving rows and retains
the workspace-exhaustion check on the general outer-join path. The latest
combined revision `1b1a3e747dc4fb1c093ee93911ce453a380c33d8` includes that fix,
the temporal resolver routing and cluster-wide prepared-transaction semantics.
[Failing qualification](https://github.com/SB0LTD/ramsql/actions/runs/44185459464) ·
[Combined release matrix](https://github.com/SB0LTD/ramsql/actions/runs/44195561261)

The original full and subset sales mixes both matched their correctness and
capacity gates on `0af167b`. RAMSQL reached 1.75× and 1.70× IRDB throughput
at eight clients and was slightly slower at one. The completed campaign passed
six of sixteen performance cases. An independent same-binary helper comparison lost
throughput in every Q3 trial with a dedicated fourth CPU and one query helper.
[Sales suites](https://github.com/benor1470/dbMeter/actions/runs/44187617545) ·
[Query helper comparison](https://github.com/benor1470/dbMeter/actions/runs/44187730700)

With a 256 MiB request for the opt-in result cache, a separate same-binary A/B
passed the original 5M-row sales correctness checks in both modes. The repeated
read-only full sales mix moved from 52.45 to 17,936.73 queries/s at one client
and from 138.01 to 17,633.45 queries/s at eight. Eight-client p99 moved from
174.079 ms to 8.575 ms. The subsequent all-engine campaign on `75becd84`
passed every original correctness and capacity check and led median throughput
in 15 of 16 cases. It cleared all performance criteria in ten cases. Four
prepared primary-key cases and the million-row filter and Top-N with one
client missed the gate. These results use the explicit cache budget and the
original repeated, read-only sales scenarios.
[Paired cache benchmark](https://github.com/benor1470/dbMeter/actions/runs/44188527666) ·
[All-engine cache comparison](https://github.com/benor1470/dbMeter/actions/runs/44196542437)

One-pass shared-storage range validation improved prepared point lookups in
each of 24 original JDBC trials across 100K and 1M rows. Paired throughput
gains were 6.7–9.1% at one client and 15.1–16.5% at eight. Median p99 improved
in all four cases. [Lookup A/B](https://github.com/benor1470/dbMeter/actions/runs/44192112939)

Rejecting disjoint address ranges before individual buffer comparisons
improved median prepared PK throughput another 4.3–6.0% across those four
cases. Scanning only the surviving projection lanes for sparse conjunctions
increased the million-row filter from 745.77 to 982.23 queries/s at one client
and from 2,070.53 to 2,622.45 at eight. Median p99 fell from 1.71 to 1.30 ms
and from 6.85 to 5.82 ms. Every paired trial improved throughput.
[Address-range A/B](https://github.com/benor1470/dbMeter/actions/runs/44202984568) ·
[Sparse filter A/B](https://github.com/benor1470/dbMeter/actions/runs/44205131087)

The committed integral projection moved the unchanged million-row Top-N
query from 24.45 to 631.81 queries/s at one client, with median p99 falling
from 46.59 to 1.81 ms. The same-runner A/B exercised both RAMSQL builds
with identical data, correctness checks and capacity.
[Top-N A/B](https://github.com/benor1470/dbMeter/actions/runs/44203326892) ·
[Executor change](https://github.com/SB0LTD/zpm/pull/227)

The combined exact build `bbaca185a88e250f8be7d3b97bdbbc87ab499032` with the
explicit 256 MiB cache budget passed all original result and capacity checks
and produced 144 valid timing reports. It led median throughput on all 16
cases and met the full rate, trial separation and p99 criteria on 11. The
four prepared primary-key cases ran at 1.11× to 1.53× PostgreSQL throughput.
The million-row single-client filter ran at 1.23× IRDB throughput. The two
eight-client primary-key cases also merit tail-latency work, especially the
100K case at 94 µs p99 versus PostgreSQL's 62 µs. All four Top-N cases cleared
the gate at 22.81× to 31.45× the faster reference. The repeated read-only
sales workloads cleared their gates. The dbMeter workflow and README claim
remain gated on a passing all-case run.
[Combined full-duration campaign](https://github.com/benor1470/dbMeter/actions/runs/44211327722)

An isolated medium-result cache with the same requested budget raised the
original million-row filter from 1,027 to 9,136 queries/s at one client and
from 2,746 to 17,122 at eight in a paired test. Median p99 fell from 1,231
to 149 µs and 5,439 to 1,903 µs respectively. This path serves repeated
eligible reads after observing the result size and invalidates on relevant
writes. A separate three-engine diagnostic found median throughput of 8,982
and 21,279 queries/s at one and eight clients. These were 10.0× and 15.8× the
fastest reference, with median p99 of 137 µs and 2,655 µs. Each engine ran
three original 60-second trials. Full-workload qualification is tracked
independently. A separate sampling run of
the exact combined build found that the active single-client server thread
spent about 64% of its recorded CPU time in the kernel. `poll` and `sendto`
were the largest inclusive syscall stacks. The Linux completion experiment
passed native contracts for partial sends, EOF, cancellation and buffer
lifetime. The linked completion experiment reduced median throughput by
0.67–4.58% across all four original prepared primary-key cases and remains
isolated.
[Filter A/B](https://github.com/benor1470/dbMeter/actions/runs/44217568675) ·
[Three-engine filter diagnostic](https://github.com/benor1470/dbMeter/actions/runs/44226874148) ·
[CPU profile](https://github.com/benor1470/dbMeter/actions/runs/44217950460) ·
[Linked transport A/B](https://github.com/benor1470/dbMeter/actions/runs/44229499260)

The later packed-cache campaign captured all 144 original JDBC reports and
passed every original correctness and capacity check. Fourteen of the
fifteen scorable cases led median throughput. Eleven of the sixteen scheduled
cases met all performance thresholds. Filters ran 3.22–30.55× the fastest
reference and Top-N ran 23.92–32.94× under the explicit 256 MiB cache budget.
All four prepared primary-key cases missed the advantage target, at 0.98–1.28×
the fastest reference. The full one-client sales case lacked successful
execution of `q5_high_value_customers` in two PostgreSQL trials. The workflow
retained the raw reports, left the case unscored, and blocked the dbMeter
merge and a general RAMSQL speed claim.
[Full packed-cache qualification](https://github.com/benor1470/dbMeter/actions/runs/44231254167)

The exact published x86-64 Linux binary ran a separate durability profile on
512 rows with one primary key and one integer payload. Each writer repeatedly
updated its own row. In three 512-operation trials per phase, median durable
throughput was 2,782, 3,569, 3,507 and 3,517 updates/s at one, four, eight and
sixteen writers. The corresponding memory rates were 8,385, 7,637, 6,287 and
6,547. Separate tracing observed 10,752 `fsync` calls for 10,752 commits and
median flush durations of 213–224 µs. At eight writers plus eight readers,
median read p95 was 5.329 ms durable, 4.692 ms in memory and 2.447 ms with
eight readers alone. The 51 durable reopen checks matched the acknowledged
state. These results quantify the write-path investigation on this workload.
[Exact durable write profile](https://github.com/SB0LTD/ramsql/actions/runs/44225804971)

An opt-in Linux group-commit candidate paired ordinary explicit PostgreSQL
transactions on distinct tables across two workers. Both clients received
their success after one stable WAL flush. The live tests also covered an
injected flush error, ordinary same-table commits, expiry, cancellation and
disconnect before selection or during a flush, and recovery of acknowledged
commits. Other statement paths still follow the ordinary commit route.
The same-binary timing run halved the number of flushes at two clients while
reducing throughput from 2,244 to 1,930 transactions/s. The four-client
distinct-table lane rose from 2,380 to 2,681 transactions/s, short of its
repeated advantage requirement. A later candidate woke the awaiting reactor
on Linux and skipped the wait for lone or same-table writers. Its latency
checks passed, while median distinct-table throughput at four and eight
clients fell by about 8% and 2% against the same binary with grouping off.
Windows passed the finite-poll fallback and replay tests. Both versions
remain opt-in experiments.
[Native socket and WAL proof](https://github.com/SB0LTD/ramsql/actions/runs/44233435009) ·
[Initial durability timings](https://github.com/SB0LTD/ramsql/actions/runs/44235108533) ·
[Linux wakeup and durability timings](https://github.com/SB0LTD/ramsql/actions/runs/44239121066) ·
[Windows fallback](https://github.com/SB0LTD/ramsql/actions/runs/44240480201)

A separate 16-column tooling build completed all twelve stock `psql \d`
queries on a table with a real user trigger, incoming foreign key, policies,
named statistics and publications. Forty live row-security observations
matched PostgreSQL 18.3 with one and four workers, including prepared queries,
COPY, savepoints, FORCE and BYPASSRLS. The native idle and transaction timeout
fixtures also matched on one and four workers.
[Populated stock describe](https://github.com/SB0LTD/ramsql/actions/runs/44238734316) ·
[Native idle timeout](https://github.com/SB0LTD/ramsql/actions/runs/44238347427) ·
[Native transaction timeout](https://github.com/SB0LTD/ramsql/actions/runs/44240230736)

The unchanged PostgreSQL 18.3 plain dump restores the two-table fixture in
RAMSQL in memory and with synchronous WAL at one and four workers. Both
primary keys, the foreign key, the check constraint, the NOT NULL column and
the omitted default behaved as expected after forced restart and WAL replay.
The new late-key path covers a standalone single-column key on an ordinary
table. General late keys and `pg_restore --single-transaction` remain open.

Export with stock `pg_dump` passes live settings, the relation guard and
extension and dependency discovery. Its first unhandled query reads
`pg_namespace.nspowner`, `nspacl`, and `acldefault`. The captured PostgreSQL
fixture emitted 67 statements. Export needs real schema privileges and ACL
types, the later catalog projections, and a RAMSQL-to-PostgreSQL restore
round trip before it meets the migration gate.
[Stock restore and exact export failure](https://github.com/SB0LTD/ramsql/actions/runs/44255933360) ·
[Integrated dependency and replay contracts](https://github.com/SB0LTD/zpm/actions/runs/44255868781)

The extended Sync and COPY candidate passed 23,416 codec checks. Linux CI
matched ten transaction and five COPY observations against native PostgreSQL
18.3 in memory and synchronous WAL with one and four workers. Both durable
processes were forced to stop and their WALs reopened. The separate simple
query batch candidate matched 58 wire observations, streamed 23,800 rows and
reopened its WAL. Its SB0X and SB0K images passed 913 and 899 boot assertions.
Both candidates need joint qualification. COPY inside multi-command simple
queries remains open.
[Extended Sync and COPY evidence](https://github.com/SB0LTD/ramsql/actions/runs/44256400851) ·
[Simple batches and native boots](https://github.com/SB0LTD/ramsql/actions/runs/44256475598)

## 17 November 2026 qualification checkpoint

The combined RAMSQL candidate `b90354c8911b22a340fb68d15383205cd2ccf4b5`, with
ZPM `7c56722bb354e1e44d418c6351edc08ee4539218`, matches **117 of 118** original
inventory programs. PostgreSQL 18.3 matches 105 of 106 with prepared
transactions enabled, including the complete original two-phase commit probe.
`PG.DDL.DATABASE` is the remaining mismatch. Native MySQL 8.4.13 and SQL Server
2022 CU28 each match all six cases.
[Native SQL Server evidence](https://github.com/benor1470/dbMeter/actions/runs/44175056901)

The Windows binary SHA-256 is
`f8c9696f7e5173393ee194375f18163ea71160e22461de774b61cd9c97160f8e`.
It passed 83 persistence cases and 3,372 assertions, including prepared
update, delete, insert, lock and decision recovery on a lean packed table
without a committed scan projection or secondary hashes. The Linux artifact
recovered the 100M-row workload in 141.301 seconds. The dbMeter size-scaling
comparison continues on this same revision.
[100M recovery](https://github.com/benor1470/dbMeter/actions/runs/44175057270) ·
[dbMeter qualification](https://github.com/benor1470/dbMeter/actions/runs/44175066222)

Both original sales suites passed their live correctness and capacity checks.
At one client, RAMSQL achieved 53.38 versus 56.40 queries per second on the
full mix and 233.66 versus 243.85 on the subset, with IRDB leading both.
At eight clients, RAMSQL achieved 134.76 versus 78.67 on the full mix
and 581.58 versus 375.19 on the subset. These are 1.71× and 1.55× the fastest
reference, respectively. Both suites remain below the required 2× throughput.
[Same-run evidence](https://github.com/benor1470/dbMeter/actions/runs/44175066222)

The separate Windows cluster candidate `bfb9db3a205455e003b4121766d02970b9744fe8`
matches all 106 original PostgreSQL 18.3 programs on durable storage with
prepared transactions enabled. This includes CREATE DATABASE, discovery in
`pg_database` and DROP DATABASE. Focused tests also verified SCRAM admission,
independent tables, rejection of a mismatched WAL and interrupted database
creation. Cluster mode admits four databases on one reactor with one
configured login. Broader cluster service and the cross-engine audit for the
integrated release remain open. [Cluster qualification](https://github.com/SB0LTD/ramsql/actions/runs/44180335244)

### Published prerelease baseline

The exact Windows x64 binary from the published CI prerelease passed 93 of 103
executed probes against native PostgreSQL 18.3 and left 15 scopes unexecuted.
The release also passed the platform matrix and the separate PostgreSQL 16 and
18 suites in memory and durable SCRAM modes. [CI prerelease](https://github.com/SB0LTD/ramsql/releases/tag/ci-v0.3.0-beta.2-run.44099014318.1)

The following seven probes agreed on result rows and types and differed in the
PostgreSQL command-completion message.

| Inventory probe | Expected completion observed on PostgreSQL |
| --- | --- |
| `PG.DDL.CREATE_VARIANTS` | `SELECT 4` for populated `CREATE TABLE AS` |
| `PG.CONSTRAINT.DEFERRED` | `SET CONSTRAINTS` |
| `PG.INDEX.SECONDARY` and `PG.INDEX.ADVANCED` | `CREATE INDEX` |
| `PG.DML.MERGE` | `MERGE 3` |
| `PG.VIEW.ORDINARY` | `CREATE VIEW` |
| `PG.VIEW.MATERIALIZED` | `SELECT 2` for populated creation and `REFRESH MATERIALIZED VIEW` on refresh |

`PG.TYPE.ENUM_DOMAIN_COMPOSITE` stopped at the first `CREATE TYPE ... AS ENUM`
with SQLSTATE `42601`. Test enum, domain and composite definitions independently
before extending the bundled probe. Qualify type OIDs, casts, comparison,
validation, schema dependencies and WAL replay as each type family is added.

Two newly executed probes found SQLSTATE `0A000` for `EXPLAIN (FORMAT JSON,
ANALYZE)` and `COPY ... FORCE_QUOTE`. The first compares plan structure and
observed final cardinality. The second compares the COPY output format and
bytes. Scope work still covers database lifecycle, two-phase transactions and
maintenance, plus six MySQL and six T-SQL probes requiring native oracle runs.
Keep the production gate open until those scopes and the broader SQL, wire,
recovery and application contracts have closing evidence.

Inventory SHA-256 `1feee91259a68605cd6d50c3a1863b79f3882e0d5c424126912ba3036520f47c`.
Expanded differential report SHA-256 `769d2d3a8bc6356168b9c0e4c70a3eae58eb683575a2257991dce17168ad50cc`.

### Windows candidate with the eight wire fixes

The ReleaseSafe candidate with ZPM `0e2b0a041bd4cc462e5d505d11c5ce2e44738296`
passed **101 probes** on the same manifest and native PostgreSQL 18.3 server.
The remaining executed failures were `PG.TYPE.ENUM_DOMAIN_COMPOSITE` and
`PG.UTILITY.EXPLAIN`. All 15 previously unexecuted scopes remain open. The
candidate also passed 3,944 wire assertions each at one and two workers,
2,394 persistence assertions across 71 cases, 8,516 authentication assertions
and the independent psycopg, PyMySQL and python-tds client suite.

Candidate binary SHA-256 `5578075963b21dcc46dfb24ce85e480a4c1019ab44d0e128c4d090c8d6f1e54a`.
Candidate differential report SHA-256 `13c894ffabe3cb5eb1b5d67fbad515467c8d2e16993da40da7a2c4ab8dd78ced`.
[Command completion](https://github.com/SB0LTD/zpm/pull/199) ·
[COPY FORCE_QUOTE](https://github.com/SB0LTD/zpm/pull/200)

With simple SELECT JSON EXPLAIN added in ZPM `8a82fc7898dd0648413dbeac8ff51c30e0066351`,
the same live audit passed **102 of 103 executed probes**. The remaining mismatch
is the combined user-defined enum, domain and composite case. The 15 unexecuted
scopes and broader EXPLAIN formats, options and plan types remain open. The
earlier eight-fix binary separately matched all 37 compatibility probes in both
memory and durable SCRAM modes with both servers using UTC defaults.

EXPLAIN candidate binary SHA-256 `fea2f28e6ee0197be070d13fc47a10cb7f978a17e213d2f07788add3eaeaf71a`.
EXPLAIN differential report SHA-256 `55e678cfaaf67d71a2483dde2d903834e90891e2601a915f5cd422d9f8e0562a`.
[Simple SELECT EXPLAIN](https://github.com/SB0LTD/zpm/pull/203)

The disposable cluster audit executed three additional PostgreSQL probes using
unique database, schema and prepared-transaction names. PostgreSQL 18.3 used
`max_prepared_transactions=8` for this run. The same binary matched 102 of 106
PostgreSQL cases, with failures at `CREATE DATABASE`, `CREATE TYPE ... AS ENUM`,
`PREPARE TRANSACTION` and `VACUUM`. The 12 native MySQL and T-SQL cases remain
unexecuted. The audit now resolves dynamic wire type OIDs to schema, name and
type kind while retaining the raw OIDs and other metadata.

Expanded administrative audit SHA-256 `83fd508a7b64f2218c1d29fe0ee044efea3a1ed02b8639d1ff2733911defa279`.

The separate named-relation `VACUUM` candidate passed 21,505 wire and store
assertions. The new cases verify that real reclamation waits for a repeatable
read snapshot to release old versions and creates no logical WAL record.
This evidence covers the basic VACUUM command within the maintenance probe.
[Named-relation VACUUM](https://github.com/SB0LTD/zpm/pull/205)

The integer-domain candidate with ZPM `699753159d84fc33a4015e2e8a8ac8723b1dd6c7`
matched 16 focused native wire probes and replayed a durable domain definition
through an actual RAMSQL restart. It covers SMALLINT, INTEGER and BIGINT domains
with bounded comparisons, optional NOT NULL and standalone casts. The bundled
type case still requires enum and composite DDL and values. The full inventory
counts above also held on this candidate. [Integer domain implementation](https://github.com/SB0LTD/zpm/pull/204)

Focused integer-domain audit SHA-256 `0693e431a2e6cc3a5421232fb4d5aeb9b378ce9b76799a0edb3397e1e567fff5`.

The integrated maintenance candidate, with ZPM `7f16e85e01b7e154fd5b8083ec7d6d2bb0fa953d`,
matched **103 of 106** native PostgreSQL probes. The complete maintenance case
passed. `ANALYZE` refreshes committed table summaries and preserves the prior
summary on interruption. Only database lifecycle, user-defined enum and
composite types, and two-phase commit fail in the PostgreSQL inventory.
The candidate also passed the one- and two-worker wire suites, crash/backup
suite, authentication checks, independent clients and machine admission tests.
[RAMSQL qualification](https://github.com/SB0LTD/ramsql/pull/64) ·
[Named-relation ANALYZE](https://github.com/SB0LTD/zpm/pull/206)

Maintenance candidate binary SHA-256 `c90a4818940d26d7eeb0436d68505631426e33abf3331c2cb8f69bb572caa827`.
Maintenance differential report SHA-256 `96616cc6c21b555b9e7622ad2392d2c2e8022aeff9837d4ae7ac49344980c027`.

The same executable matched all 37 PostgreSQL probes in memory and durable
SCRAM modes. It also matched 313 native PostgreSQL semantics cases, 46
isolation schedules and 11 COPY cases in the memory profile. The explicit
512-row control returned `54000` and left the table empty. The memory
semantics run carried 18 explicit durable-restart skips.

Native MySQL Community 8.4.13 executed the six unchanged MySQL inventory
cases. RAMSQL passed every setup statement and rejected the first feature
statement in each case with MySQL error 1064. The gaps cover backtick
identifiers, unsigned BIGINT, AUTO_INCREMENT, REPLACE, comma LIMIT and
native scalar functions. All six native SQL Server cases still require
the reference-engine comparison.

MySQL differential report SHA-256 `fe944d6f3d13073289e4410d54f212bef5f77678d4be1a98ccf1bc53b305a6a0`.

The follow-up with native MySQL parsing passes the unchanged comma LIMIT case.
The backtick query now returns the expected alias and row. Native MySQL reports
the signed integer literal as LONGLONG and the candidate reports LONG. The
strict comparator keeps that case open. [MySQL parser PR](https://github.com/SB0LTD/zpm/pull/207)

The integrated enum and dialect candidate reached composite DDL in the grouped
type probe, after native enum and integer domain creation. An independent
PostgreSQL 18.3 probe matched enum labels and order, catalog ownership and OID
links, and text and binary result metadata. The individual server identities
and results survived restarts of both engines. The candidate's main PostgreSQL
inventory still matches 103 of 106 cases.

The python-tds client reproduced both expected candidate result sets for
T-SQL bracket identifiers and `SELECT TOP`. The other four original T-SQL
cases failed on feature SQL. The native SQL Server comparator supplies the
reference results for a strict comparison. [T-SQL parser PR](https://github.com/SB0LTD/zpm/pull/208) ·
[SQL Server comparison](https://github.com/SB0LTD/zpm/actions/runs/44146301734)

Enum and dialect candidate binary SHA-256 `f89763b8d1e22ebc03d5c9a948c523a23deb0f89d95e402c6eedd035fca8385d`.
Enum focused report SHA-256 `2766c75935007b9cac4968d154634420b5ad1491b50d760171e3c7a719bf6d56`.
T-SQL candidate-only report SHA-256 `1dae63a9213c436f7eef6e609a1fb474ee44990135fd8e79d6b40a1de488df00`.

The next exact build matched two of six native MySQL cases after fixing signed
integer literal metadata. It also matched the supported PostgreSQL composite
catalog fields, typmods, array and relation links, rollback and restart. The
stock build rejected a ten-column projection with `54000`, and the extended
catalog comparison found that `pg_class.reltype` is still unavailable. The
combined PostgreSQL type case now reaches the composite value cast. An
independent python-tds client verified native DATE results on protocol 7.4 and
the existing text representation on 7.2.

Composite catalog report SHA-256 `8cd14577a9fc8009502ffb1b9ef7641d6eb78f07148d3ad39e3563b5ea34050c`.
Native MySQL report SHA-256 `f1a255e8e6605d82f617a803ac6e7f14724ee17017eb2958f6297182a4a06c00`.

RAMSQL `f6c6867a1d776b96657c3ad3405b4927e48cfc9b` with ZPM
`df5e8da5f85c1f4ff70dfec735e722cc3d625ac2` matches **104 of 106** original
PostgreSQL cases. The exact composite projection matched native PostgreSQL
text and binary wire results on both sides of a durable restart. The two
remaining PostgreSQL cases start with database creation and prepared
transaction lifecycle. Native MySQL matches three of six cases after `IFNULL`
and `DATE_ADD` were added. Unsigned integers, AUTO_INCREMENT and REPLACE are
the remaining native MySQL mismatches in this executable.

Native SQL Server 2022 CU28 compared all six original T-SQL cases against
the Linux artifact from the same source. Three produce the expected row
values and fail on wire metadata. SQL Server reports the tested
non-null INT result columns as fixed-length with nullable unset. Its unnamed
function result columns have empty names. OUTPUT, APPLY and procedural SQL
are rejected by the candidate. [Native SQL Server comparison](https://github.com/benor1470/dbMeter/actions/runs/44157665404)

PostgreSQL inventory report SHA-256 `297ebd021df3147fd63bf77c0e4f320568fb484831bb6bc312b3d4c2f84a08cf`.
Native MySQL report SHA-256 `7a2b058d7c418d2fbd0e675a46226d57ee72c09546b043c278bd7b323be908b2`.

RAMSQL `13294c5246dce4ef6ab23af55c1679b7d189af42` with ZPM
`5f674e9e561d208bed7f29338b462d257e482165` preserves the 104 PostgreSQL matches
and reaches **4 of 6** MySQL and **3 of 6** strict SQL Server matches. The
MySQL trace compares replacement counts, defaults, repeated keys, rollback,
concurrent writers and WAL reopening with MySQL 8.4.13. Unsigned integers and
AUTO_INCREMENT remain open. SQL Server's exact metadata and values match
for IDENTIFIERS, TOP and FUNCTIONS. Its remaining inventory cases are OUTPUT,
APPLY and procedural SQL. [Native SQL Server run](https://github.com/benor1470/dbMeter/actions/runs/44161329548)

RAMSQL `1a8dbb6846a5eb1df1ab96f141184731af904332` with ZPM
`e3b7c13c07fafe4a3951b581c3ce203b81281d9a` matches all six original native
SQL Server smoke cases and all 24 setup observations. The added cases cover
direct `OUTPUT` images, derived-query `CROSS APPLY` and batch-local `INT`
declarations. Full procedural control flow, OUTPUT sinks, APPLY table-valued
functions and additional wire behaviors remain in the broader dialect scope.
[Strict SQL Server comparison](https://github.com/benor1470/dbMeter/actions/runs/44165836087)

The combined RAMSQL `deb1ed41c32e65b7cdf2a86d9f2820569ed1e286` candidate with
ZPM `f208559a76a2e03f026221ac86c929a00da57c32` matches **116 of 118** original
programs across the three native reference engines. The two PostgreSQL cases
still require database lifecycle and prepared transaction support. MySQL
adds full-range `BIGINT UNSIGNED` storage and signed-primary-key
`AUTO_INCREMENT` with mixed inserts, native insert IDs and rollback. Broader
MySQL arithmetic, unsigned automatic counters, schema and wire behavior
remain outside these probes. [Combined candidate](https://github.com/SB0LTD/ramsql/pull/69) · [Native SQL Server run](https://github.com/benor1470/dbMeter/actions/runs/44169351782)

## Earlier checkpoints

Reporting follow-up, 30 October 2026: the unchanged original dbMeter full 5M
and subset suites now pass 7/7 correctness checks for RAMSQL, PostgreSQL and
IRDB in [run 41024725859](https://github.com/benor1470/dbMeter/actions/runs/41024725859).
Temporal `to_char`, ranking/aggregate windows and flat ROLLUP/GROUPING merged in
ZPM PR 80 and RAMSQL PR 35 after all 32 active hosted/native/release and PG16/18
memory/durable+SCRAM checks passed (runs 41024653323/41024653363). The four concrete
reviewer query failures are addressed; the broad Q03/Q06 families stay partial.
The consolidated follow-up adds bounded ROWS/GROUPS, integral RANGE, exclusions
and FILTER. It is now merged and passes all 32 active checks in runs
41114733707/41114733789, with the three-OS ZPM matrix passing in 41114412805.
The original SQL TDD map is refreshed to 8 passes, 93 failures and 17 unexecuted
scopes/comparators; its broader feature families remain incomplete. See the
[reporting implementation and closing checklist](REPORTING_SQL_PROGRESS.md).
Preserve original SQL, expected CSVs, negative timings and invalid trials.
The all-engine performance gate has not passed.

Current review: 25 October 2026, development candidate `0.3.0-alpha.4` (unpublished).
Latest published prerelease: `0.3.0-alpha.3`.
Original review baseline: 22 October 2026, `0.3.0-alpha.2`.
Status update: 24 October 2026, gap-closing run (RAMSQL branch
`gap-closing-measured-probes`, ZPM `dba76e1`, [ZPM PR 21](https://github.com/SB0LTD/zpm/pull/21));
see [Gap-closing run evidence](#gap-closing-run-evidence-24-october-2026).
The measured probes are now passing; the work is focused on scalable storage,
interruptible execution, durable lifecycle and production qualification. Broad
feature families remain partial even when their measured examples pass.

This document explains what still separates RAMSQL from the requested product:
a production database server that keeps its working data in RAM, persists commits,
uses pure allocator-free Sig, and can replace PostgreSQL for applications and
operations. PostgreSQL is the default compatibility target; MySQL and SQL Server
also need their own compatible, authenticated server paths. Linux, Windows,
macOS, native SB0S applications and native SB0K images are part of the platform goal.

The plan is intended for a gap-by-gap discussion. The order below reflects
dependencies, not a proposal to lower the final target. Every unchecked item
remains open until its implementation and closing evidence exist.

For the 25 October infrastructure investigation, see
[allocator-free infrastructure research and engineering decisions](DEEP_INFRASTRUCTURE_RESEARCH.md).
It connects the million-row IRDB comparison to streaming aggregation, ordered
indexes, compact storage, scheduling and durable commits, with recent primary
research, explicit memory contracts and implementation acceptance gates. Its
proposed algorithms remain open work; the research document does not close gaps.
The [implementation progress record](INFRASTRUCTURE_PROGRESS.md) tracks the
streaming aggregation candidate and the still-open infrastructure milestones.

## How to use this plan

For each numbered gap, review four things: the practical problem, where the fix
belongs, the implementation tasks, and the evidence needed to close it. Mark a
gap complete only after the evidence is linked. Parsing a statement, passing a
handshake, or building a binary is not enough to establish the feature's behavior.

Status meanings:

| Status | Meaning |
| --- | --- |
| Missing | The current implementation does not provide the capability. |
| Partial | Useful behavior exists, but important required behavior is absent. |
| Unverified | Current tests do not establish the required behavior. |
| Candidate addressed | Code and focused tests exist; final platform/release evidence must still be checked. |
| Closed | The gap's closing criterion is met with linked differential evidence against PostgreSQL 18 and 16, in memory and durable+SCRAM modes, for the scope stated with the gap. It does not change any release gate by itself. |

At the review baseline there were **18 known failures in 37 differential probes**,
against both PostgreSQL 16 and 18; after the gap-closing run all 37 match on
both, in memory and durable+SCRAM modes. These are examples of incompatibility, not the complete backlog and
not a percentage of PostgreSQL implemented. Larger feature families and untested
behavior appear below. Upstream regression and real-application testing must
continue discovering gaps even after all 37 probes pass.

Primary evidence:

- [PostgreSQL 18 differential report](postgres-report.json) and [PostgreSQL 16 report](postgres16-report.json).
- [Machine-readable production gates](release-gates.json) and [release requirements](RELEASE.md).
- [Implementation sequence](IMPLEMENTATION.md), [validation record](VALIDATION.md), and [benchmark results](../benchmarks/README.md).
- [ZPM SQL/store changes, PR 20](https://github.com/SB0LTD/zpm/pull/20), merged to ZPM `main`.
- [RAMSQL integration, PR 4](https://github.com/SB0LTD/ramsql/pull/4); consult its current status for qualification and merge state.

### Windows and migration verification

The exact published beta.3 Windows x64 binary passed the CLI and real-driver
checks, 5,246 wire assertions with four workers, 8,598 SCRAM assertions and
3,372 persistence assertions across 83 cases and five crash cycles.
[Published Windows verification](https://github.com/SB0LTD/ramsql/actions/runs/44267034004)

The merged ZPM migration foundation imported a stock PostgreSQL 18.3 plain
dump with stock psql on Windows and Linux. Memory and synchronous WAL runs
at one and four workers verified primary keys, foreign keys, CHECK and NOT NULL
constraints, defaults, and persistence across WAL reopens. The RAMSQL product
integration is in release qualification. Stock pg_dump export and general
single-transaction pg_restore still require implementation and testing.
[ZPM foundation](https://github.com/SB0LTD/zpm/pull/257) ·
[Stock restore CI](https://github.com/SB0LTD/ramsql/actions/runs/44265114896) ·
[RAMSQL integration](https://github.com/SB0LTD/ramsql/pull/84)

## What already works, and must keep working

The current hosted server has a typed parser/binder/executor pipeline behind
PostgreSQL, MySQL and TDS listeners. It supports expressions, numeric/temporal/JSON
types, joins, grouping/aggregates, CTEs, DISTINCT/pagination, multi-row DML,
RETURNING, upserts, defaults, identity/sequences, a catalog-view subset and
streamed COPY. The detailed feature limits are in the individual gaps and README.
PostgreSQL has tested simple/extended queries, text/binary values, prepared
statements, failed-transaction handling and SCRAM.

The durable subset has synchronous WAL flushing before acknowledgement, exclusive
writer locking, checksummed records, incomplete-tail recovery, fail-closed corrupt
recovery and validated offline whole-WAL backups. These are useful foundations;
they do not establish complete PostgreSQL durability or operational compatibility.

Connections now share one MVCC database with statement snapshots and private
version/write sets. Per-connection database copies and global-generation write
conflicts are gone. READ COMMITTED, savepoints, row/table waits, deadlock handling
and transactional DDL have differential tests. Commits prepare kind-3 typed change
records, synchronously persist them, then publish. Legacy kind-1/2 records replay
with the frozen alpha grammar; multi-statement recovery does not copy a database.

Live Windows/Linux tests include independent psycopg, PyMySQL and python-tds
clients, SCRAM and 58 crash/backup cases. The updated eight-target release
qualification passed in run 40174568211, including exact hosted artifact tests
and native SB0X/SB0K QEMU. The new reusable PostgreSQL qualification workflow runs
PG16/PG18 × memory/durable+SCRAM, retains raw reports/logs and requires all suites
against the exact Linux release artifact before assembly. Hosted Sig 0.6.4 baseline qualification passed in
[run 40188819921](https://github.com/SB0LTD/ramsql/actions/runs/40188819921),
including all eight targets, exact artifact tests and all four PostgreSQL cells;
[CI 40188819914](https://github.com/SB0LTD/ramsql/actions/runs/40188819914) also
passed. Baseline changes merged as `b8551d4`. Further engine changes require a
fresh qualification before publishing alpha.3; production gates remain unchanged.

### Current limits that applications will encounter

These are the limits of the published beta.3 hosted profile. Inspect the exact
binary and proposed runtime options with `--capabilities` and `--machine-capacity`.

| Resource | Published hosted default or codec bound | Effect |
| --- | --- | --- |
| Connections | 16 by default, adjustable at startup through 60 | Full PostgreSQL SQL pools return 53300 while the separate startup queue has room. Larger pools need a qualified build with a higher ceiling. |
| Tables and columns | 8 tables and 8 columns per table | Larger schemas need a build profile. |
| Rows and versions | 4,096 rows and 8,192 versions per table by default | OS-backed storage budgets can be set independently per table at startup. Reservations remain fixed for the life of the process. |
| Text | 128 bytes per value by default | Larger values need a build profile. Inline cells grow with its configured width. |
| Identifiers | 63 bytes | PostgreSQL naming and collation coverage remains incomplete. |
| Encoded transaction change records | 1 MiB and 4,096 changes by default | Larger write transactions need a larger build profile and sufficient version capacity. |
| PostgreSQL query and packet | 65,530 bytes of simple Query SQL, 65,535 bytes including the length field | Authenticated oversized Q/P/B messages through 16 MiB are drained and return 54000. Larger or malformed frames can terminate the connection. |
| PostgreSQL prepared state | 8 statements, 8 portals and 32 parameters | Applications with a larger prepared working set can hit these bounds. |
| MySQL and TDS requests | 65,536 bytes including framing constraints, with 32 TDS RPC parameters | Bulk and generated requests need a streaming path. |
| Results and query workspace | 8,192 result rows and 4,096 workspace rows by default | Both budgets can be raised at startup. General materialized plans can still exhaust memory without disk spill. |
| Execution | Multiple PostgreSQL socket and read workers on supported Linux and Windows builds | Individual queries still run on one worker. Writes share the exclusive SQL execution lane. Hosted macOS and native SB0 use one reactor. |

Larger budgets increase the machine memory grant the server needs. Sizing a
general workload still involves table shape, version churn, connection count,
and the largest live execution state. The limits and memory charges are
described in [Capacity and admission planning](../CAPACITY.md).

## Layer rules for every gap

| Owner | Put these responsibilities here |
| --- | --- |
| Sig | Language/compiler correctness, strict allocation checks, ABI/code generation, build system and genuinely general standard I/O primitives. |
| ZPM core / database | SQL syntax and typed expressions, schemas and values, page/row/index storage, transactions, visibility, locking, WAL framing, reusable recovery/backup algorithms, authentication primitives and query execution. |
| ZPM platform / transport | Native sockets, polling, threads, files, durable barriers, secure entropy, TLS transport and consolidated SB0 ABI adapters. |
| RAMSQL | Server configuration, listeners and scheduling policy, credentials and access policy, persistence/checkpoint/backup orchestration, resource profiles, diagnostics, integration tests, benchmarks and release qualification. |

Reusable work should move to ZPM whenever it has a real consumer and a clear
contract. Lower layers must not import RAMSQL. Keep runtime code in `.sig` and
use caller-owned pages, fixed pools, bounded scratch storage or equivalent
allocation-free ownership. Do not hide a general heap allocator behind an arena
name or pull another database engine into the runtime.

## 1. Compatibility contract and discovery

### C01 — Define and continuously test the replacement contract

**Status:** Partial. **Owner:** RAMSQL qualification; reusable conformance support in ZPM.

“A PostgreSQL client connects” is much narrower than “an existing application can
replace PostgreSQL.” The latter includes SQL results, errors, transactions,
metadata discovery, session settings, migrations, administration and recovery.

- [ ] Record the PostgreSQL major versions and protocol variants to support; retain PostgreSQL 18 as the current stable reference and 16 as an additional tested reference at this review date.
- [ ] Inventory application frameworks, drivers, migration tools and extensions that the replacement must run. Absence from the present tests must not silently become an exclusion.
- [ ] Distinguish application/protocol compatibility from PostgreSQL's internal data-directory format, native extension binary ABI and physical replication format. These need explicit designs if they are part of the replacement contract.
- [ ] Add missing behavior to an executable compatibility inventory with expected rows, types/OIDs, command tags, SQLSTATEs and transaction effects.

**Closed when:** the target is explicit and its acceptance suite runs against both
RAMSQL and the native reference. Any proposed exclusion is recorded for review,
not treated as an automatic reduction of the user's target.

### C02 — Replace the small probe list with broad discovery

**Status:** Partial. The 118 original programs and 331-case differential suite
have native evidence under the profiles above. **Owner:** RAMSQL tests.

Passing today's probes could still leave most applications incompatible. The
tests currently contain only one example of many large feature families.

- [ ] Run applicable PostgreSQL SQL regression and isolation cases, preserving every failure in a categorized report.
- [ ] Add generated/property-based SQL tests, malformed protocol tests and state-machine fuzzing.
- [ ] Test real schema creation, migrations, representative applications and administration clients.
- [ ] Make crashes, hangs, incomplete runs and hidden skips visible separately from ordinary semantic differences.

**Closed when:** compatibility is supported by a maintained coverage inventory,
upstream-derived tests and application evidence. A green 37-probe result alone
cannot close C02 or the production compatibility gate.

## 2. Storage and transaction foundations

### S01 — Replace dense database-shaped storage and transaction copies

**Status:** Partial. Stable row IDs, version reclamation and caller-owned
row, version, index and query backing serve one shared store. Hosted RAMSQL
accepts per-table row and version budgets. The 100M fixture uses compact
integral banks for its six-column fact table. General text values still occupy
fixed-size cells, and table and session capacities are granted up front.
**Owner:** ZPM storage/SQL/store; RAMSQL configures resource budgets.

An isolated A/B verified that 32-byte version headers save 1,601,085,600 bytes
of backing at 100M fact rows with the same durable correctness and WAL bytes
as the 48-byte baseline. Each lane ran on its own CI host. The fixture verifies
one large fact table, two small dimensions and two workers. Multiple 100M-row
tables and wider variable-length data still need independent qualification.
[48-byte and 32-byte runs](https://github.com/benor1470/dbMeter/actions/runs/44186893882)

- [ ] Add a bounded variable-length value store and qualify mixed-width and text-heavy tables.
- [ ] Adapt physical grants to observed demand and qualify multiple large tables per database.
- [ ] Qualify physical compaction with stable row identities under active readers and writers.
- [ ] Specify ownership, reuse, stale-reference protection, rollback and out-of-memory behavior before replacing the current representation.
- [ ] Keep active data in RAM; persistence must not silently turn the design into an on-demand disk database.

**Closed when:** memory use follows actual stored data and private changes rather
than `connections × maximum database size`; substantially larger datasets run
within a configured RAM budget, with no runtime heap allocation or atomicity regressions.

### S02 — Add useful indexes and scalable execution algorithms

**Status:** Partial. The 100M qualification exercises bounded top-K, ordered
range access, typed dimension probes and streaming three-way grouped joins.
Unsupported shapes take the general executor, where joins, sorts and grouping
can still exhaust configured statement workspace. Index and fast-path coverage
need broader SQL and concurrent-mutation comparisons.
**Owner:** ZPM SQL/planner/index packages.

- [ ] Add secondary, composite and ordered indexes, including uniqueness and NULL rules.
- [ ] Introduce planning that chooses indexed lookup, range scan, scan, sort and join strategies from actual query requirements.
- [ ] Support bounded sorting/grouping workspace, appropriate algorithms and explicit exhaustion/cancellation behavior.
- [ ] Maintain indexes through updates, rollback, recovery and row-version reclamation.

**Closed when:** indexed and scanned plans agree with independent models, survive
adversarial keys and concurrent changes, and scale without quadratic behavior
where a suitable algorithm or index should avoid it.

### S03 — Stream requests/results and budget session memory

**Status:** Missing at production scale. **Owner:** ZPM execution/codecs; RAMSQL backpressure policy.

Materializing complete results and reserving large buffers per connection makes
connection count and result size expensive even when clients read slowly.

- [ ] Add resumable execution and result iterators with bounded batches.
- [ ] Stream large values, COPY data and protocol messages without one enormous contiguous buffer.
- [ ] Replace small arbitrary prepared-statement/portal/parameter limits with explicit budgeted storage.
- [ ] Preserve result/portal ownership across suspension, client disconnect, transaction end and schema changes.

**Closed when:** a large result can be read by a slow client while other clients
make progress, memory stays within budget, and cancellation/disconnect releases
every held page, version, cursor and session resource.

### T01 — Implement PostgreSQL READ COMMITTED visibility

**Status:** Closed for READ COMMITTED. `read_committed` probe `match: true (PG18+PG16, memory and durable+SCRAM)`; isolation schedules RC1–RC14 match (statement snapshots, own writes, concurrent update/delete with EvalPlanQual, unique-key races, aborts, disconnects). [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM transaction/storage/execution.

This gap was closed for the supported READ COMMITTED feature set: each statement
has a fresh committed snapshot plus the transaction's own earlier writes. Writers
wait and recheck conflicting rows. Preserve RC1–RC14 and expand their coverage as
new query/storage features are added; do not regress to whole-database snapshots.

- [ ] Implement row versions and statement snapshots, including transaction-own writes.
- [ ] Add visibility rules for insert, update, delete, rollback and commit.
- [ ] Reclaim old versions only after all readers that can see them finish.
- [ ] Remove global-generation rejection as the normal write-conflict mechanism.

**Closed when:** repeated SELECTs, concurrent updates/deletes, unique-key races,
aborts and disconnects match native PostgreSQL in deterministic multi-connection
schedules. Merely replaying old SQL over a newer snapshot is insufficient: its
predicates can select different rows and change the transaction's meaning.

### T02 — Add row ownership, lock waits and deadlock handling

**Status:** Candidate addressed (flag F18). Row/table locks, waits that never block the reactor, FOR UPDATE/SHARE [NOWAIT|SKIP LOCKED], deadlock detection (40P01), lock_timeout and cancel; schedules LK1–LK9, UP1–UP4 and CP1 match. FOR NO KEY UPDATE/KEY SHARE and multi-core fair scheduling (O01) remain. [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM lock/transaction engine; RAMSQL schedules suspended work.

Independent writers now progress without a global-generation conflict. Row/table
lock waits park a client without blocking the reactor; deadlocks, cancellation and
lock deadlines are tested. Remaining work includes additional PostgreSQL lock
modes and fair scheduling of running work across cores.

- [ ] Add row/key/schema lock ownership and compatible wait queues.
- [ ] Implement waiting writers, predicate reevaluation where required, SELECT FOR UPDATE/SHARE and related lock modes.
- [ ] Detect deadlocks, choose a victim, report the appropriate error and release locks on every exit path.
- [ ] Connect lock waits to cancellation, deadlines and fair scheduling.

**Closed when:** independent writers progress together; conflicting writers and
deadlocks match reference outcomes without lost updates, leaked locks or a
blocked reactor. Requires S01 and T01.

### T03 — Implement savepoints and transaction-local rollback

**Status:** Closed for RAMSQL's feature set (cursors do not exist). `savepoint` probe `match: true (PG18+PG16, memory and durable+SCRAM)`; SP1–SP9 and SP8b match (nesting, repeated names, failures, release, rows/DDL/locks/settings, commit and durable restart). [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM transaction engine and shared SQL commands.

Frameworks use savepoints to undo one operation while keeping the surrounding
transaction alive. Full ROLLBACK cannot substitute for ROLLBACK TO SAVEPOINT.

- [ ] Implement SAVEPOINT, ROLLBACK TO and RELEASE, including repeated names and nesting.
- [ ] Restore data, indexes, resources and the correct failed-transaction state.
- [ ] Specify interactions with locks, cursors, schema changes and statement errors.

**Closed when:** reference schedules cover nested savepoints, failures, released
names, commit/restart and disconnect without retaining rolled-back changes.

### T04 — Complete isolation and transaction-control behavior

**Status:** Partial. ZPM #120 adds transaction modes:

- REPEATABLE READ: one snapshot per transaction, with 40001 on concurrent updates.
- SERIALIZABLE: relation-level SSI. It is sound, but coarser than PostgreSQL's tuple-level SSI, so it raises more 40001 errors.
- Transaction characteristics: SET TRANSACTION, SET SESSION CHARACTERISTICS, READ ONLY (25006) and DEFERRABLE.

RR1 and RR2 compare transaction snapshots and stale-write rejection against
native PostgreSQL. Both matched PostgreSQL 16.15 in memory and durable SCRAM
on Wave 3 revision 665f897, including persisted rows after restart. The
Serializable two-actor write-skew probe remains a known gap. In memory,
PostgreSQL commits the first writer and rejects the second with 40001.
RAMSQL raises 40001 on the second write and again on the first commit, so
neither write survives. Keep that case open until error timing and final
state match the reference.

**Owner:** ZPM transactions. The codecs expose the behavior.

BEGIN/COMMIT/ROLLBACK working for the subset does not establish all transaction
modes. Applications can request isolation levels, read-only transactions and
other transaction characteristics.

- [x] Implement SET TRANSACTION and default/session transaction characteristics with real state (ZPM #120).
- [ ] Complete PostgreSQL Serializable qualification, including write-skew commit ordering. The latest memory candidate matches all 46 selected isolation schedules; the coarse Serializable conflict case above still needs its own closing evidence.
- [ ] Cover transactional DDL, aborted commits, serialization retries and durability acknowledgements under concurrency.

**Closed when:** each advertised mode has reference-tested semantics, including
anomaly prevention and errors. Do not describe the present conservative snapshot
mechanism as PostgreSQL Serializable or complete Repeatable Read.

### T05 — Recoverable two-phase transactions

**Status**

Qualified on a separate Windows candidate for opt-in permanent-table DML.
RAMSQL `20cd383b8d4ce30c0820ec105766ccda2630fd6f` pins ZPM
`0c8a939559a077a35421875f09870229e45bb70d`. Enable a positive
`--max-prepared-transactions` limit in durable mode. The default limit is
zero. Reopen a WAL with unresolved work using a grant that covers every
pending prepared transaction. ZPM owns the transaction state and record
format. RAMSQL owns the WAL and startup recovery. [Exact RAMSQL candidate](https://github.com/SB0LTD/ramsql/commit/20cd383b8d4ce30c0820ec105766ccda2630fd6f) · [Exact ZPM implementation](https://github.com/SB0LTD/zpm/commit/0c8a939559a077a35421875f09870229e45bb70d)

The unchanged PostgreSQL 18.3 inventory matched 105 of 106 executed cases
on this candidate. `PG.TX.TWO_PHASE` matched the `PREPARE TRANSACTION` and
`COMMIT PREPARED` tags, results and idle transaction status. The remaining
executed mismatch was database creation. A separate Windows crash test
verified prepared INSERT, UPDATE and DELETE, with an ordinary commit
interleaved between prepare and resolution. It verified implicit and
explicit row locks after restart, owner enforcement, rollback, rejection of
a mismatched decision, and torn-decision recovery. The persistence suite
passed 77 cases and 2,877 assertions. [Exact crash test](https://github.com/SB0LTD/ramsql/blob/20cd383b8d4ce30c0820ec105766ccda2630fd6f/tools/test_persistence.py)

Windows binary SHA-256 `9c7763a195abb684a945be6f7ca7e35d6171a21076d44424e545c15e5989c511`.
Native inventory report SHA-256 `51faf199ec315130028b28d4fb71d566f80f84d0977afe1b83d49313dc4d61e7`.

- [x] Reserve a budgeted entry keyed by a case-sensitive GID, role OID and
  store identity. Detach on successful prepare while retaining pending row
  versions and locks.
- [x] Flush prepare and resolution records before changing their visible state.
  Recover unresolved DML and its locks before opening listeners, and check
  each decision against its exact prepare record.
- [x] Verify `COMMIT PREPARED` and `ROLLBACK PREPARED`, duplicate GIDs,
  disconnect, owner authorization, lock conflicts and crash recovery for the
  admitted DML scope.
- [ ] Add `pg_prepared_xacts` inspection and qualify prepared DDL, deferred
  constraint work, advisory locks, sequence changes and Serializable
  transactions. Qualify the combined native inventory and supported release
  matrix with two-phase commit enabled.

**Closed when:** a write transaction can stay prepared across disconnect and
process restart, then be resolved by an eligible connection with the same
visibility, conflict and durability outcomes as the reference.

## 3. SQL, types and schema behavior

### Q01 — Build typed expressions and complete boolean predicates

**Status:** Closed for the implemented type set. `or_predicate` probe `match: true (PG18+PG16, memory and durable+SCRAM)`; suite q01 matches (precedence, three-valued logic, typing and SQLSTATEs, numeric promotion). [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM parser/type checker/executor.

Typed expressions now provide arithmetic, OR/NOT, parentheses, casts,
parameters, CASE, IN, BETWEEN, LIKE and expressions in projections/updates for
the supported type set. Keep their precedence, NULL and failure-atomicity tests
as shared contracts for every later executor/storage change.

- [x] Add a bounded typed expression representation with correct precedence and binding.
- [x] Implement PostgreSQL's true/false/unknown NULL logic, casts, overflow and evaluation errors for supported types.
- [x] Share expression machinery across filters, projections, defaults and assignments. CHECK constraints remain Q11 work.

**Closed when:** reference cases include mixed AND/OR/NOT, nested parentheses,
NULLs, invalid types, overflow and failures after many matching rows, with no
partial mutations. Other SQL families below should use this engine.

### Q02 — Exact numeric and floating-point types

**Status:** Partial (flag F-M). NUMERIC/SMALLINT/REAL/DOUBLE implemented, `numeric` probe `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q02 matches; MySQL/TDS return these types as text-typed columns, and NUMERIC holds at most 64 significant digits. [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM values/arithmetic/SQL; protocol codecs encode types.

Money and scientific workloads need more than signed INTEGER/BIGINT. Decimal
precision, scale, rounding and overflow must be compatible rather than using a
floating-point approximation for NUMERIC.

- [ ] Add NUMERIC/DECIMAL with precision/scale rules and exact arithmetic.
- [ ] Add SMALLINT and floating-point types with casts, special values and comparison rules.
- [ ] Implement text/binary formats, PostgreSQL OIDs and corresponding MySQL/TDS metadata.

**Closed when:** boundary values, rounding, arithmetic, aggregates, prepared
parameters and persisted round trips match the native reference.

### Q03 — Dates, times, timestamps, time zones and intervals

**Status:** Partial (flag F3). DATE/TIMESTAMP/TIMESTAMPTZ/INTERVAL with TZif zones, `timestamp` probe `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q03 matches; no zone abbreviations, non-ISO DateStyle/IntervalStyle variants, and named zones need a TZif directory (none by default on Windows/SB0). [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM temporal values/execution; RAMSQL configures required data/resources.

An event timestamp is not ordinary text. Parsing, time-zone conversion, ordering,
arithmetic and client binary formats must agree with PostgreSQL.

- [ ] Implement DATE, TIME, TIMESTAMP, TIMESTAMPTZ and INTERVAL behavior.
- [ ] Add real session time-zone/date-format state and required time-zone data handling.
- [ ] Cover daylight-saving changes, ambiguous/nonexistent local times, infinity, precision and range errors.

**Closed when:** the same input, session settings and arithmetic return the same
typed values and errors through text/binary clients and after recovery.

### Q04 — JSON/JSONB and structured-value operations

**Status:** Partial (flag F4). JSON/JSONB with the core operators, `jsonb` probe `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q04 matches; values are bounded by `text_bytes`, no jsonpath and most JSON functions absent. [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM values, SQL expressions and reusable JSON support.

Applications store documents and query individual fields. Accepting JSON text
without JSONB validation, comparison and operators would not satisfy this gap.

- [ ] Add JSON/JSONB storage, validation and compatible canonical/value semantics.
- [ ] Add field/path operators, SQL functions, casts and parameter/result formats.
- [ ] Integrate large documents with S01/S03 rather than the current 128-byte text slot.

**Closed when:** nested objects/arrays, duplicate keys, JSON null versus SQL NULL,
Unicode, invalid documents, operators and recovery match reference behavior.

### Q05 — Remaining PostgreSQL type families

**Status:** Missing/unverified. **Owner:** ZPM type system and codecs.

UUID, BYTEA, VARCHAR/CHAR, arrays, enums, domains, composite and range types,
network/geometric types and other advertised built-ins need an explicit inventory.
The existing numeric, floating, temporal and JSON families do not provide these
additional type families.

- [ ] Record required types, casts, operators, functions, OIDs and binary formats.
- [ ] Implement shared storage/expression behavior before exposing a type through a codec.
- [ ] Cover type modifiers, NULLs, array dimensions, constraints and round trips.

**Closed when:** each type claimed in the compatibility contract has complete
behavioral and persistence evidence. Do not mark the family closed after adding
only one commonly used type.

### Q06 — Aggregates, grouping, HAVING and window functions

**Status:** Partial (flag F6). Aggregates, GROUP BY and HAVING implemented, `aggregate` and `group_by` probes `match: true (PG18+PG16, memory and durable+SCRAM)`. Ranking/aggregate windows and flat ROLLUP/GROUPING passed final combined server and original dbMeter correctness qualification. Bounded ROWS/GROUPS, integral RANGE, exclusions and FILTER are now merged and pass final server/platform/native qualification on the consolidated source. Named windows, remaining built-in window functions, numeric/float/temporal RANGE offsets, volatile moving inputs, general grouping sets and AVG(interval) remain gaps. DISTINCT window aggregates are unsupported in PostgreSQL too. See the [reporting record](REPORTING_SQL_PROGRESS.md) and [earlier evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM planner/executor.

Reports and application counts require COUNT/SUM and grouping. Window functions
compute values over related rows without collapsing the result into groups.

- [ ] Implement aggregate state, result types, NULL/empty-input behavior and overflow.
- [ ] Add GROUP BY, HAVING, aggregate DISTINCT/filtering and grouped expressions.
- [ ] Add window partitions, ordering and frame semantics with budgeted workspace.

**Closed when:** grouped and ungrouped reference queries, empty sets, NULLs,
overflow, large groups and window boundaries match, including wire metadata.

### Q07 — Joins, subqueries, CTEs and set operations

**Status:** Partial (flag F7). Joins, subqueries, CTEs (incl. recursive) and set operations, `join` and `cte` probes `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q07 matches; no data-modifying CTEs, LATERAL, NATURAL or parenthesized join trees; nested-loop joins bounded by the statement workspace. [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM binder/planner/executor.

Real schemas split data across related tables. Joining those tables is a normal
application operation, not an optional reporting feature.

- [ ] Add qualified names, aliases and name binding across multiple relations.
- [ ] Implement inner/outer/cross joins, correlated/uncorrelated subqueries, EXISTS and set operations.
- [ ] Implement WITH/CTEs, including recursive and modifying forms required by the target contract.
- [ ] Select appropriate join plans and honor cancellation/workspace budgets.

**Closed when:** null-extended outer rows, duplicate rows, correlated visibility,
recursive termination and transactional behavior match the reference at realistic sizes.

### Q08 — DISTINCT, ordering, pagination and query shape

**Status:** Candidate addressed (flag F8). DISTINCT [ON], multi-key ORDER BY with NULLS FIRST/LAST, OFFSET/FETCH, `distinct` probe `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q08 matches; sorts larger than the statement workspace fail with 54000 (S02/S03). [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM SQL/planner/executor.

Multi-key ordering, duplicate elimination, OFFSET and expression ordering are
implemented. Preserve their typed/NULL/tie behavior while closing remaining
pagination forms and scalable sorting/result ownership.

- [ ] Implement DISTINCT/DISTINCT ON with correct NULL and ordering rules.
- [ ] Add multiple ordering keys, ordinal/expression ordering, OFFSET and applicable FETCH forms.
- [ ] Validate which ORDER BY expressions are legal for DISTINCT/grouped queries.

**Closed when:** duplicates, NULLs, tie cases, pagination boundaries and invalid
queries match PostgreSQL, using bounded algorithms suitable for larger datasets.

### Q09 — General DML, RETURNING and conflict handling

**Status:** Partial (flag F9). Multi-row VALUES, INSERT ... SELECT, RETURNING and ON CONFLICT on the primary key, `returning` and `upsert` probes `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q09 and UP1–UP4 match; no UPDATE ... FROM, DELETE ... USING, RETURNING OLD/NEW or non-PK arbiters. [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM SQL/storage/transactions; codecs serialize results.

Applications commonly insert several rows, return generated values, insert from
a SELECT, update using expressions and perform an upsert when a key already exists.

- [ ] Add multi-row INSERT, INSERT SELECT, expression updates and required UPDATE FROM/DELETE USING forms.
- [ ] Implement RETURNING for supported insert/update/delete forms.
- [ ] Implement ON CONFLICT targets, DO NOTHING/DO UPDATE and EXCLUDED semantics.
- [ ] Coordinate uniqueness and concurrency with T01/T02; preserve whole-statement atomicity.

**Closed when:** command counts, returned rows/types, duplicate races, affected-row
visibility, errors and durable replay agree with the reference.

### Q10 — Defaults, identity and sequences

**Status:** Partial (flag F10). Stock PG16 and PG18 archives round-trip ordinary
defaults, stored generated columns, scoped identity and standalone sequences.
Stock archives also exercise multiple identity columns on one table.
The replay suite exercises a cached scoped sequence with `CACHE 2`. Stock
PG16 and PG18 CI covers common `SERIAL` and `BIGSERIAL` archives with two owned
sequences. Broader sequence DDL and privilege parity and clean-shutdown
sequence continuity remain open. **Owner:** ZPM
schema, execution and transactions. RAMSQL persists lifecycle state.

- [x] Store and evaluate defaults and stored generated-column expressions in the qualified subset.
- [x] Implement identity definitions and sequence reads, with persisted start and current values.
- [ ] Complete sequence privilege, lifecycle and concurrent-allocation parity across supported deployments.

[Stock migration qualification](https://github.com/SB0LTD/ramsql/actions/runs/45018672015)

**Closed when:** omitted/explicit DEFAULT values, ALWAYS/BY DEFAULT identity,
rollback gaps, session sequence state and crash/restart match native PostgreSQL.

### Q11 — Schema lifecycle, constraints and transactional DDL

**Status:** Partial. **Owner:** ZPM schemas/catalog/storage; RAMSQL migration integration.

Stock PG16 and PG18 restore schema ownership, table definitions and
constraints in one transaction. The fixtures verify supported check and
foreign-key behavior, and include composite primary keys on newly
restored tables.

- [x] Implement the qualified subset of schemas, ALTER TABLE, rename, TRUNCATE and qualification.
- [x] Enforce the tested primary key, UNIQUE, CHECK and foreign key forms.
- [ ] Extend physical index and referential-action parity, including additional deferrability and integral-bank DDL.
- [ ] Complete transactional DDL parity, schema locks and prepared-plan invalidation.
- [ ] Version persisted schema definitions and support validated schema changes during recovery/upgrades.

**Closed when:** representative migrations run forward/backward, concurrent DDL
and queries behave correctly, and failure cannot leave half-applied schema/index state.

#### Database lifecycle and isolation

The hosted release matrix verifies separate stores selected by the
PostgreSQL startup database, isolation and restart. The cluster registry
is persisted for the implemented create and drop paths.

- [x] Route the qualified startup-database connections to separate stores.
- [x] Recover the qualified registry and stores from WAL after restart.
- [ ] Close cluster-wide backup and restore, catalog privileges, and routing
  parity for cursors, notifications and lock waits.
- [ ] Implement ownership, transaction-block rejection, an initial template
  policy and in-use checks for DROP. Cover identifier reuse, reconnect races,
  cross-database isolation, prior data recovery and create/drop crash edges.

### Q12 — Names, text, encodings, collations and dialect separation

**Status:** Partial (flag F-M). Quoted identifiers keep case; INTEGER is int4 on PostgreSQL sessions and boolean 0/1 assignment is a per-session MySQL/TDS rule, but the parser is shared by all listeners and collations/encodings are unchanged. **Owner:** ZPM SQL/types/codecs; RAMSQL selects session policy.

Names currently use ASCII case-insensitive rules and text compares as bytes.
PostgreSQL quoted identifiers, schemas/search_path, Unicode and collation behavior
need more than raising a string bound. The shared server configuration also enables
integer-to-boolean coercion for all protocols to accommodate MySQL/TDS clients;
that must not silently change PostgreSQL SQL typing.

- [ ] Implement quoted identifiers, qualification and PostgreSQL name resolution.
- [ ] Validate/enforce advertised encodings and define character versus byte length rules.
- [ ] Implement required collations, text operations and their version/persistence policy.
- [ ] Move dialect-specific coercion/normalization into an explicit typed session/dialect path.

**Closed when:** case, quoting, accents, Unicode, invalid encodings, text lengths
and coercion tests match each engine's contract. One global permissive dialect
must not substitute for three compatible dialects.

### Q13 — Functions, views, triggers and extensions

**Status:** Partial. **Owner:** ZPM language and execution. RAMSQL selects
supported facilities. Ordinary dependent views round-trip in the qualified
stock archive fixture. Routine and rule tests cover supported execution
and WAL replay. Extensions and arbitrary stored definitions require
further implementation and qualification.

Applications call built-in functions and can rely on views, triggers, procedures
and extensions. Protocol support alone cannot execute that application logic.

- [ ] Inventory and implement required built-in functions/operators and volatility rules.
- [ ] Add views/materialized views, triggers, user-defined functions/procedures and required procedural language behavior.
- [ ] Review extension requirements individually, including catalogs, SQL API and persistence. A PostgreSQL native C extension cannot be assumed compatible with a pure-Sig replacement.

**Closed when:** required application logic and selected extension behavior pass
reference tests and operational tests. Any unsupported extension or native binary
ABI requirement remains explicitly open for review.

## 4. PostgreSQL ecosystem and protocol behavior

### P01 — Real system catalogs and information_schema

**Status:** Partial (flag F14). Versioned catalog with a pg_catalog/information_schema view subset, `pg_type` and `information_schema` probes `match: true (PG18+PG16, memory and durable+SCRAM)`, suite p01 and CAT1 match; migration tools and `psql \d` need many more catalogs and functions. [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM schema/catalog/query engine.

Drivers and migration tools ask the database what tables, columns, types, indexes
and permissions exist. Hard-coded replies to the two failing discovery queries
would pass those probes without making tools work.

- [ ] Model catalogs as real views of schema/type/index/role/session metadata, with stable identifiers and transactional visibility.
- [ ] Implement required pg_catalog tables/views, information_schema and discovery functions.
- [ ] Track dependencies, search_path, object ownership and DDL invalidation.

**Closed when:** actual client discovery and migrations observe correct metadata
before/after DDL, commit, rollback and restart. Catalog values must reflect real state.

### P02 — Finish PostgreSQL session and wire semantics

**Status:** Partial. **Owner:** ZPM PostgreSQL codec and session/execution state.

Prepared/binary queries work for the subset, but small fixed slots and a single
materialized active result do not establish the full protocol. The session
currently advertises `server_version = 16.0`, accepts only a small set of settings,
and lacks broad setting behavior.

- [ ] Audit simple-query batches, unnamed/named statement replacement, portal lifetime, suspension, pipelining and error/Sync recovery.
- [ ] Add real session/transaction-local settings, SHOW/RESET and correct ParameterStatus changes; settings must not merely return success.
- [ ] Complete parameter inference, OIDs/typmods, schema invalidation, binary formats, notices, asynchronous events and required large-object/function paths.
- [ ] Make advertised server identity/version consistent with the tested compatibility contract, rather than implying unsupported behavior through a hard-coded version.
- [ ] Assess required socket types, IPv6 and connection startup/negotiation variants.

**Closed when:** independent drivers, poolers and protocol tests cover these state
transitions, not just successful CRUD. Preserve the preflight rule that insufficient
output capacity cannot cause an unreportable mutation.

### P03 — COPY and bulk movement

**Status:** Partial (flag F17). COPY FROM STDIN / TO STDOUT in text, csv and binary over the simple query protocol, `copy` probe `match: true (PG18+PG16, memory and durable+SCRAM)`, COPY suite 11/11 and CP1 match; one COPY is bounded by `-Drows`, `-Dtx-writes` and `-Dtransaction-bytes` (a 5 000-row COPY at the default profile is 54000, recorded as a divergence), no extended-protocol COPY, files, FORCE_*, ON_ERROR or WHERE. [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM COPY codecs and executor; RAMSQL I/O/resource policy.

Imports, exports and PostgreSQL tools rely on COPY. A sequence of single-row
inserts is not compatible with COPY's streaming protocol or error behavior.

- [ ] Add COPY FROM/TO with text/CSV/binary formats, column selection and type conversion.
- [ ] Handle CopyData/CopyDone/CopyFail, fragmentation, errors, cancellation and backpressure.
- [ ] Preserve statement/transaction atomicity on bad rows, capacity exhaustion and connection loss.

**Closed when:** large imports/exports round-trip through libpq/psycopg and native
PostgreSQL, with deliberately malformed rows and interrupted transfers.

### P04 — Prove real application, driver and tool compatibility

**Status:** Partial. **Owner:** RAMSQL integration/qualification.

The 18.3 `psql` client read a real table on the two-worker beta.3 Windows
build. A subsequent Windows candidate with `-Dcolumns=16` matched its first
three `\d` queries, including the exact 15-column relation projection and the
column query that correlates `pg_attribute`, `pg_type` and `pg_collation`. A
separate native differential matched eight relation rows covering a table,
indexes, sequence, partition, view and materialized view. The stock width is
eight. The next `\d` query needs `pg_constraint`, index definitions and
constraint definitions. Constraint identities must remain stable across typed WAL replay and
support the foreign-key and index references used by tooling. Native
`pg_dump --schema-only` still requires empty search-path and catalog behavior.
[Qualified discovery](https://github.com/SB0LTD/zpm/pull/229) ·
[Relation and column metadata](https://github.com/SB0LTD/zpm/pull/234) ·
[Native metadata comparison](https://github.com/SB0LTD/ramsql/actions/runs/44215906350)

- [ ] Add versioned suites for libpq/psycopg, JDBC, Npgsql, Go and Node clients as required by the compatibility contract.
- [ ] Run representative ORM and migration workflows against identical schemas/data on both engines.
- [ ] Exercise psql discovery/help operations, dump/restore and bulk tools; keep physical PostgreSQL backup/replication format support distinct from logical SQL compatibility.

**Closed when:** real workflows complete without RAMSQL-specific application
rewrites, and failures are represented in the gap inventory. Requires broad Q/P work.

## 5. Persistence, backup and recovery

### D01 — Version durable semantics and support upgrades

**Status:** Partial (flag F-D01). New WALs hold versioned kind-3 typed change records; alpha.1/alpha.2 kind 1/2 logs recover through a frozen alpha-grammar replay; older binaries cannot read kind 3. No release upgrade/downgrade matrix yet. **Owner:** ZPM canonical record/schema formats; RAMSQL upgrade/replay policy.

New WAL records contain typed changes rather than executable SQL text. Legacy
SQL logs are interpreted only by the frozen alpha grammar. The remaining gap is
a supported release upgrade/restore matrix and crash-safe migration procedures;
the typed format does not by itself prove future upgrade compatibility.

- [ ] Design a versioned, architecture-independent representation for committed schema/data changes, including explicit sequence and compatibility rules.
- [ ] Keep old readers/migrations until supported upgrade paths are verified; refuse incompatible formats without modifying source data.
- [ ] Add release-generated fixtures and upgrade/downgrade matrices, including interrupted migration and rollback procedures.

**Closed when:** every supported old release can be upgraded or explicitly refused
without silent reinterpretation/data loss. The alpha.1 fixture proves only its
specific compatible path, not general upgrade compatibility.

### D02 — Crash-safe checkpoints and WAL rotation

**Status:** Primitives in qualification. **Owner:** ZPM reusable checkpoint/log
primitives; RAMSQL orchestration.

The WAL grows with all historical changes, and restart replays that history.
Deleted historical states can still require capacity during replay.

An allocator-free log primitive validates a complete anchor frame and requires
the caller to verify its snapshot binding before replaying a contiguous suffix.
A separate writer-lock contract holds database ownership across two WAL
generations and atomic pointer publication. Product recovery still opens the
existing WAL format. [Anchor PR](https://github.com/SB0LTD/zpm/pull/219) ·
[Writer-lock tests](https://github.com/SB0LTD/zpm/actions/runs/44191670373)

The streaming envelope binds length, database and generation identity, WAL
sequence and SQL commit number to a BLAKE3 seal. The one-table logical image
restores committed OIDs, RowIDs, allocation counters and SQL commit number
from dense or packed storage. A normal WAL update/delete/insert suffix
continued after both restores on Windows and Linux. Expand the codec to
multiple tables and the full catalog, then prove generation publication and
retirement under injected failures.
[Snapshot envelope](https://github.com/SB0LTD/zpm/pull/230) ·
[Single-table image and suffix](https://github.com/SB0LTD/zpm/pull/232)

The isolated RAMSQL startup path now acquires the real writer lock, checks
the selected generation and seal, loads a caller-owned two-table image, and
applies the WAL suffix through the production persistence layer. Linux,
Windows and macOS each passed 16 checkpoint, 119 ordinary persistence and
11 backup assertions. The public startup path still needs a durable database
identity for standalone files, a common lock across the legacy and generation
layouts, and interruption-safe publication and retirement of generations.
[Hosted checkpoint startup proof](https://github.com/SB0LTD/ramsql/actions/runs/44234918499)

The owned-sequence candidate checkpoints supported public tables with
ordinary defaults, identity and SERIAL columns. The Sig image contract
passed 136 assertions, including malformed ownership and valid WAL
suffix replay. The local Linux server retained the sequence ownership
and allocation state through rotation, a process kill and recovery of
new writes in the selected WAL. Full CI and catalog images for named
schemas remain open.

The next checkpoint candidate includes supported tables and sequences
in named schemas owned by the bootstrap role. The local Sig suite passed
147 assertions and the Linux server recovered committed writes after a
process kill. Schema and relation OIDs, same-named sequences, defaults
and ownership are preserved. The full hosted release matrix passed and
the qualified source merged. Catalog images for roles, grants, views
and other schema objects remain open.
[Named-schema checkpoint qualification](https://github.com/SB0LTD/ramsql/actions/runs/45050702452)

- [ ] Write a complete validated snapshot with a durable sequence anchor.
- [ ] Publish it atomically, persist directory changes, then retire only log segments the checkpoint safely covers.
- [ ] Retain a separate database lock across file replacement and define recovery for every interrupted transition.
- [ ] Bound checkpoint resource use and recovery time without evicting active data from RAM.

**Closed when:** crash injection before/after every write, flush, rename and
retirement step always recovers a valid committed state without losing an
acknowledged commit or accepting a mismatched snapshot/log pair.

### D03 — Online backup, restore and point-in-time recovery

**Status:** Partial. **Owner:** ZPM reusable consistency/format algorithms; RAMSQL operational commands.

The current offline command validates and copies the whole WAL. It cannot provide
an online consistent snapshot, compact backup, WAL archive or point-in-time recovery.

- [ ] Add consistent online backup with a checkpoint/log retention boundary and integrity manifests.
- [ ] Implement restore validation, retained-log replay and target-time/transaction recovery where required.
- [ ] Support logical export/import compatibility separately from physical RAMSQL backups.
- [ ] Exercise scheduled backups, restore drills, corruption/missing-segment handling and backup interruption.

**Closed when:** a fresh deployment restores a documented recovery point under
ongoing write load, with proven completeness and an explicit recovery-time/recovery-point objective.

### D04 — Qualify device and power-loss failure behavior

**Status:** Unverified. **Owner:** ZPM file/barrier implementations; RAMSQL fault qualification.

Killing the server process tests a different failure from losing power or failing
a storage device. Successful fsync/FlushFileBuffers calls alone do not prove the
whole supported filesystem/device stack preserves acknowledged data.

- [ ] Inject short writes, torn/corrupt writes, sync errors, read errors, full storage and failed directory publication.
- [ ] Run VM/block-device fault tests and controlled power-loss tests on deployment-relevant storage.
- [ ] Verify uncertain-commit handling, fail-closed startup and operator recovery procedures.

**Closed when:** the supported durability contract has reproducible fault evidence
on each deployment storage class. Keep process-crash, simulated-I/O and physical
power-loss evidence labeled separately.

### D05 — Replication, failover and availability

**Status:** Missing/unverified. **Owner:** Reusable replication/state machinery in ZPM; RAMSQL cluster policy.

Normal PostgreSQL deployments can depend on replicas and failover. A durable
single process does not replace those operational arrangements.

- [ ] Review logical/physical PostgreSQL replication expectations and RAMSQL replica design separately.
- [ ] Define commit acknowledgement, ordering, retention, resynchronization and failover consistency.
- [ ] Add fencing/split-brain prevention, replica lag visibility and failure drills.

**Closed when:** the agreed deployment/replication contract works through network
partitions, process/node loss, promotion and rejoin without violating its durability
promises. Do not imply PostgreSQL physical replication compatibility automatically.

## 6. Authentication, authorization and transport security

### A01 — TLS and channel binding

**Status:** Server transport primitives in qualification. **Owner:** ZPM reusable TLS/transport; RAMSQL certificate and listener policy.

Hosted listeners currently bind loopback and PostgreSQL clients use sslmode=disable.
SCRAM authenticates credentials but does not encrypt query text or result data.
The private pure-Sig TLS 1.3 exchange matched RFC test vectors and completed a
certificate- and hostname-verified OpenSSL session on Linux. The native TLS
socket adapter has separate Windows loopback coverage. The listener still needs
the record channel, handshake admission, encrypted cancel and SCRAM channel
binding before exposing a TLS endpoint.
[Server exchange draft](https://github.com/SB0LTD/zpm/pull/228)

- [ ] Implement a tested native TLS server transport with certificate/key loading and safe lifecycle handling.
- [ ] Add PostgreSQL SSL negotiation and required SCRAM channel binding; integrate MySQL/TDS TLS negotiation separately.
- [ ] Handle certificate rotation, invalid/expired credentials, handshake resource limits and protocol downgrade tests.
- [ ] Enable configurable remote endpoints only with the corresponding authentication/access policy.

**Closed when:** independent clients verify encryption, peer identity and required
channel binding; malformed handshakes fail safely and cannot starve other sessions.

### A02 — Roles, permissions and database isolation

**Status:** Partial. **Owner:** ZPM roles/catalog/authorization and RAMSQL provisioning policy.

Beta.3 has an opt-in PostgreSQL SCRAM catalog credential file, role grants and
durable database-level admission. Catalog login checks the SCRAM-verified
username against the recovered role catalog before the session becomes ready.
Live WAL tests with one and two workers exercise prepared reads under
row-level security, denied writes and role escalation, and startup rejection
for a NOLOGIN role. The audited cluster mode has four databases on one reactor
with one configured login. Multi-user cluster routing, application privilege
workflows, complete metadata and COPY privileges, and MySQL/TDS authorization
remain open.

- [ ] Add role/database/schema ownership, grants/revokes, membership and required row-level/security-definer behavior.
- [ ] Enforce privileges at binding/execution and metadata discovery, including prepared statements and COPY.
- [ ] Define cross-database isolation, administrative capabilities and audit requirements.

**Closed when:** adversarial multi-role tests cannot read, modify or discover
objects outside their rights through any supported protocol or maintenance command.

### A03 — Credential and authentication lifecycle

**Status:** Partial. **Owner:** ZPM authentication primitives; RAMSQL credential/access policy.

PostgreSQL SCRAM, verifier parsing and fresh nonces are tested. Credential changes,
multiple accounts, access rules and authenticated MySQL/TDS remain open.

- [ ] Implement protected credential provisioning/rotation, account changes and host/database access rules.
- [ ] Test failed-login throttling/resource use, secret erasure, secure randomness failures and credential-file handling.
- [ ] Inventory additional PostgreSQL authentication methods required by target deployments without silently advertising unsupported methods.

**Closed when:** normal account lifecycle and denied-access cases work securely,
including restart, concurrent login/rotation and native-platform entropy failure.

## 7. MySQL and SQL Server compatibility

### M01 — MySQL server compatibility

**Status:** Partial (flag F-M); local text-query subset only, new value types are text-typed columns. **Owner:** ZPM MySQL codec/dialect/types; RAMSQL authenticated listener policy.

PyMySQL text queries work, but empty-password local trust, no TLS, no binary
prepared statements and shared PostgreSQL-shaped SQL do not replace MySQL.

- [ ] Add required MySQL authentication plugins, TLS, capability negotiation and session variables.
- [ ] Implement COM_STMT prepare/execute/fetch/close, binary rows, multi-results and bulk/client behaviors required by the target contract.
- [ ] Add MySQL dialect, coercion, generated identifiers, transaction and error behavior without relaxing PostgreSQL semantics globally.
- [ ] Differential-test against a pinned native MySQL server and multiple independent clients.

**Closed when:** the explicitly reviewed MySQL version/application matrix passes
authenticated functional, operational and performance tests. The current PyMySQL
success remains subset evidence only.

### M02 — SQL Server/TDS server compatibility

**Status:** Partial (flag F-M); trusted TDS 7.2–7.4 subset only, new value types are text-typed columns. **Owner:** ZPM TDS codec/dialect/types; RAMSQL listener/access policy.

PRELOGIN/LOGIN7, SQL batches and bounded sp_executesql work. Other prepared/RPC
paths, bulk copy, MARS, integrated authentication, TLS and TDS 8 are unsupported.

- [ ] Define SQL Server/TDS versions and required client/tool compatibility.
- [ ] Implement authenticated/encrypted sessions, required RPC/prepared/cursor/bulk paths and negotiated capabilities.
- [ ] Add corresponding T-SQL, types, metadata, transaction and error semantics.
- [ ] Differential-test with a native SQL Server reference and required .NET/ODBC/JDBC clients.

**Closed when:** the reviewed SQL Server application matrix works with the same
security and durability standards as the PostgreSQL path. A successful python-tds
handshake/CRUD test does not close it.

## 8. Native SB0S/SB0K deployment

### N01 — Provide actual native network, file and entropy capabilities

**Status:** Missing in the current native RAMSQL integration. **Owner:** SB0 OS capability providers; ZPM consolidated-ABI adapters; Sig only for real ABI/compiler defects.

Native images currently run the shared engine over trusted delegated channels
or UART. They do not provide the hosted TCP listeners, durable storage or SCRAM
entropy path. A valid native image is therefore not yet the full native server.

- [ ] Define/test OS capabilities for TCP accept/read/write/readiness, durable files/locks/barriers and secure randomness.
- [ ] Implement reusable ZPM adapters against the consolidated `aarch64-sb0` ABI.
- [ ] Wire the real RAMSQL server to those capabilities with explicit startup failure when a required capability is absent.

**Closed when:** actual native clients connect over the network, authenticate,
commit, crash/restart and restore data using real OS-backed services. Fake flushes,
deterministic entropy and renamed Linux binaries cannot satisfy this gap.

### N02 — Test the full OS integration, not just an ABI fixture

**Status:** Partial. The gap-closing run regressed it (native images stopped building with Sig `8b4908bb` once the typed-expression change landed), and the Sig 0.6.4 native-backend fix (`90a898dc`) restored it: both images build on the current engine and their QEMU loader tests pass again. This is still the ABI-fixture evidence described below, not a complete SB0S distribution or hardware deployment. See [Native build blocker](#native-build-blocker-resolved). **Owner:** RAMSQL/SB0 integration and release qualification.

SB0X EL0 and SB0K EL1 QEMU tests pass. The SB0X loader is a public ABI conformance
fixture, not proof of a complete supported SB0S distribution or hardware deployment.

- [ ] Run both image formats in the actual supported SB0 environment, preserving one consolidated ABI.
- [ ] Verify process/capability lifetime, scheduling, isolation, image validation, memory permissions, stacks and fault recovery.
- [ ] Exercise native network, durable storage, authentication, load and restart after N01 exists.
- [ ] Retain fixture tests as fast regressions, and add hardware/boot evidence separately.

**Closed when:** release evidence identifies the actual OS/kernel/build/hardware
and demonstrates the full server lifecycle. No private Nexus token is assumed or
required by this plan; unavailable distribution integration remains an explicit gap.

### N03 — Native resource and performance qualification

**Status:** Unverified beyond the memory-only QEMU functional workload. **Owner:** RAMSQL benchmarks; ZPM primitives; Sig for measured compiler/code-generation defects.

QEMU functional assertions do not establish native throughput, latency, memory
scaling or durable I/O performance.

- [ ] Measure native stack/page/CPU use and enforce budgets for both image formats.
- [ ] Benchmark actual native storage/network paths, including backpressure, interrupts, long transactions and saturation.
- [ ] Compare generated code for measured hotspots without moving database behavior into Sig.

**Closed when:** actual native deployment meets the reviewed efficiency and
reliability targets with reproducible artifacts and environment details.

## 9. Scheduling, operations and production control

### O01 — Concurrent scheduling and connection scale

**Status:** Partial. **Owner:** ZPM reusable thread/poll/ownership primitives; RAMSQL reactor/worker policy.

Independent reads use multiple workers on qualified Windows and Linux
profiles. A costly query or synchronous flush on the serial store path can
still hold up unrelated clients. The hosted experimental 128-session build
passed admission, cancellation, deadlock and slot reuse. macOS passed its
single-worker lane and rejects unsupported additional workers.
[Windows and Linux qualification](https://github.com/SB0LTD/ramsql/actions/runs/44228649454) ·
[macOS qualification](https://github.com/SB0LTD/ramsql/actions/runs/44231434730)

- [ ] Introduce resumable query/lock/I/O work and fair scheduling.
- [ ] Add worker ownership/coordination after storage and transaction invariants are established.
- [ ] Replace the small descriptor/connection ceiling with scalable platform readiness mechanisms and budgeted session state.
- [ ] Qualify opt-in group commit for throughput and tail latency. Extend the supported transaction and platform scope only with tests for acknowledgement, failure and recovery.

**Closed when:** load across cores/connections makes useful progress with bounded
tail latency and no races/lost updates. Group commit must never weaken the current
flush-before-publication/acknowledgement guarantee by accident.

### O02 — Cancellation, deadlines and resource control

**Status:** Partial (flag F-O02). Running SQL now has caller-owned execution
accounting, live statement deadlines and cancellation checkpoints in ZPM. RAMSQL
polls bounded startup/cancel I/O without reentering SQL; valid cancel keys work
with a full connection pool. Work-budget exhaustion is `54000`; cancellation and
statement timeout are `57014`. Local Linux/Windows live tests and core rollback
contracts pass; exact release qualification of this change remains required.
**Owner:** ZPM interruptible execution/lock waits; RAMSQL request/session policy.

- [x] Implement cancellation keys and a safe connection-to-query lookup.
- [x] Interrupt running evaluation and parked lock waits; retain the original statement deadline through waits.
- [x] Add configurable execution-work budgets and prove that errors undo private versions and preserve prior results.
- [ ] Add transaction and idle-in-transaction deadlines, with compatible errors.
- [ ] Add resumable/fair scheduling, streaming results and comprehensive session/result budgets.
- [ ] Interrupt or safely bound remaining lifecycle/COPY/disconnect paths; durable publication and rollback currently finish synchronously.

See [execution-control contract and limits](EXECUTION_CONTROL.md). This is an
interruption milestone; O01's scheduling and S01's compact storage remain open.

**Closed when:** an expensive query or lock wait can be interrupted promptly,
one client cannot cancel another without valid credentials, and aborted work
cannot publish partial data or leak versions/locks/buffers.

### O03 — Monitoring and actionable diagnostics

**Status:** Missing for production operation. **Owner:** ZPM exposes counters/state; RAMSQL diagnostics/export policy.

Operators need to explain slow queries, lock waits, memory pressure, storage
failures and recovery time. “The process is running” is not a sufficient health check.

- [ ] Add structured errors/logs, query/transaction/session statistics, lock/wait visibility and WAL/checkpoint/backup metrics.
- [ ] Distinguish liveness, readiness, degraded read-only state and failed storage.
- [ ] Add EXPLAIN and relevant PostgreSQL statistics views as real execution/catalog features.
- [ ] Protect diagnostics from leaking credentials, unauthorized data or unbounded logs.

**Closed when:** an operator can diagnose controlled slow-query, lock, full-storage,
failed-authentication and recovery incidents using documented interfaces.

### O04 — Deployment, maintenance and recovery procedures

**Status:** Partial. **Owner:** RAMSQL product/release; ZPM reusable process/config/I/O support.

Production use also needs predictable startup, shutdown, configuration changes,
service integration, data location/permissions and repair/recovery procedures.

- [ ] Implement graceful draining/shutdown, configuration validation/reload and supported service/container deployment paths.
- [ ] Provide documented upgrade, rollback, credential rotation, backup, restore and corrupt/full-storage runbooks.
- [ ] Test interrupted startup, signals, repeated restart, unavailable dependencies and operator mistakes without overwriting user data.

**Closed when:** fresh-install and failure-recovery drills succeed from written
procedures on each supported deployment platform, without developer-only intervention.

## 10. Efficiency and release proof

### B01 — Establish representative performance superiority

**Status:** Unproven. **Owner:** RAMSQL reproducible benchmarks; optimizations belong in the layer containing the measured cost.

The complete CI dbMeter campaign ran unchanged JDBC cases for 100K and 1M
rows plus the original full and subset sales workloads. Correctness and capacity
passed across all three engines. Six of twelve size cases passed the performance
gate. Point lookups were close to the fastest reference, and the mixed sales
workloads did not reach 2×. The separate 100M recovery fixture verifies correctness
and resource use. It does not compare 100M throughput across engines.
[Completed dbMeter campaign](https://github.com/benor1470/dbMeter/actions/runs/44159631870)

- [ ] Define equivalent supported workloads and the native PostgreSQL deployment envelope to replace: data size, schema shape, connection count, transaction mix and storage.
- [ ] Match query semantics, durability, authentication/encryption, hardware, storage and client method before comparing speed.
- [ ] Measure throughput, p50/p95/p99 latency, CPU, resident/committed memory, bytes per row/client, recovery, checkpoint and backup costs.
- [ ] Include large values, joins/aggregates, contention, long transactions, bulk loading, slow readers, sustained load and saturation.
- [ ] Retain every trial/regression, investigate variability and define pass/fail budgets before calling results excellent.

**Closed when:** equivalent, representative workloads reproducibly meet the
reviewed efficiency targets across supported deployments. In-memory lookups must
not be compared with another server's durable encrypted end-to-end transactions.

### V01 — Verify allocation freedom, ownership and architecture continuously

**Status:** Partial; the current runtime is pure Sig with fixed/caller-owned storage.
**Owner:** Sig enforcement; ZPM contracts; RAMSQL integration/release policy.

New page storage, version reclamation, codecs and concurrency can accidentally
introduce hidden allocation, dangling references, unbounded recursion or imports
in the wrong direction.

- [ ] Keep strict `.sig` compilation and dependency/layer checks on all supported targets.
- [ ] Test exhaustion, reuse, stale handles, result ownership and every rollback/error path.
- [ ] Document total/peak memory and stack bounds, including per-session and temporary workspace.
- [ ] Audit OS-owned resources separately from runtime heap allocation; RSS alone does not prove allocation freedom.

**Closed when:** every runtime path and dependency complies with the ownership/
allocation contract, with tests and compiler evidence rather than terminology alone.

### V02 — Qualify exact artifacts and keep release gates honest

**Status:** Partial; the eight-target pipeline exists, full production qualification does not.
**Owner:** RAMSQL release; ZPM/Sig maintain their reusable/toolchain qualification.

The matrix contains x86_64/AArch64 Linux, Windows and macOS, plus aarch64-sb0
SB0X/SB0K. Build success, subset runtime success and production readiness are
different claims.

- [ ] Run functional/security/durability/application/soak suites on exact release binaries for every supported target.
- [ ] Keep immutable ZPM/compiler pins, source identity, checksums, runtime evidence and artifact attestations.
- [ ] Resolve/report broader ZPM CI health separately: its legacy Sig 0.5.9 workflow has existing SABR/manifest/TLS/GPU failures, even when database-platform CI is green.
- [ ] Require every production gate and a reviewed readiness declaration to pass before stable publication.
- [ ] Add artifact download/restore/upgrade drills, not only builds of the source tree.

**Closed when:** the published assets are the qualified assets and the production
claims match all evidence. No failed gate may be changed to passed merely to
enable publication; prereleases must remain explicitly identified as development work.

## The 18 measured gaps, mapped to the plan

The "What breaks" column describes the review baseline. Every probe now
records `match: true (PG18+PG16, memory and durable+SCRAM)`; a matching probe
does not close its gap, whose status is on the gap's **Status** line.

| Probe | What breaks in ordinary use | Primary closing work | Probe after the gap-closing run |
| --- | --- | --- | --- |
| `numeric` | Exact decimal columns such as monetary amounts cannot be created/used. | Q02 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q02 Partial, F-M |
| `timestamp` | Typed event times/time-zone behavior cannot be represented. | Q03 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q03 Partial, F3 |
| `jsonb` | Document columns and field operators fail. | Q04 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q04 Partial, F4 |
| `aggregate` | COUNT/SUM queries fail. | Q06 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q06 Partial, F6 |
| `group_by` | Grouped reports and grouped application queries fail. | Q06 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q06 Partial, F6 |
| `join` | Queries combining related tables fail. | Q07 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q07 Partial, F7 |
| `cte` | WITH queries used by applications/migrations fail. | Q07 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q07 Partial, F7 |
| `or_predicate` | A normal WHERE condition containing OR fails. | Q01 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q01 Closed |
| `distinct` | Duplicate elimination fails. | Q08 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q08 Candidate addressed, F8 |
| `returning` | INSERT cannot return the inserted/generated value. | Q09 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q09 Partial, F9 |
| `upsert` | ON CONFLICT insert-or-update workflows fail. | Q09, T02 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q09 Partial, T02 Candidate addressed |
| `defaults` | Omitted values cannot use column defaults. | Q10 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q10 Partial, F10 |
| `identity` | Generated identity columns fail. | Q10 | `match: true` (PG18+PG16, memory and durable+SCRAM); Q10 Partial, F10 |
| `pg_type` | PostgreSQL type discovery fails. | P01 | `match: true` (PG18+PG16, memory and durable+SCRAM); P01 Partial, F14 |
| `information_schema` | Standard schema discovery fails. | P01 | `match: true` (PG18+PG16, memory and durable+SCRAM); P01 Partial, F14 |
| `savepoint` | A framework cannot roll back one nested operation. | T03 | `match: true` (PG18+PG16, memory and durable+SCRAM); T03 Closed |
| `copy` | Streaming bulk import fails. | P03 | `match: true` (PG18+PG16, memory and durable+SCRAM); P03 Partial, F17 |
| `read_committed` | A new statement inside a transaction does not see the expected new committed data. | T01, T02 | `match: true` (PG18+PG16, memory and durable+SCRAM); T01 Closed, T02 Candidate addressed |

Closing these rows is necessary. It does not close the other gaps above, including
security, scaling, remaining types/SQL, tooling, operations and native services.

## Gap-closing run evidence (24 October 2026)

Builds: RAMSQL `gap-closing-measured-probes` with `build.sig.zon` pinned to ZPM
archive `dba76e14c98ad62cd74cf72d3916e4005b616578` (SHA-256
`6bb8f4b6b6999c08da2e1b92d22a5a3561d96fc2197e75d312656ea10e11add4`), debug
server build. References: `postgres:18-alpine` (PostgreSQL 18.3, image
`sha256:4da1a4828be1…`) and `postgres:16-alpine` (PostgreSQL 16.15, image
`sha256:f4610795587…`). Memory mode is `--insecure-local`; durable+SCRAM uses a
fresh WAL (`--data`) and a SCRAM-SHA-256 password login. Every count below is
differential parity with the reference (same rows, types, tags, SQLSTATEs,
transaction status and blocking), never a non-error reply.

| Suite (harness) | PG18 memory | PG18 durable+SCRAM | PG16 memory | PG16 durable+SCRAM |
| --- | --- | --- | --- | --- |
| 37-probe gate ([`test_postgres_compat.py`](../tools/test_postgres_compat.py)) | 37/37 ([report](postgres-report.json)) | 37/37 | 37/37 ([report](postgres16-report.json)) | 37/37 |
| Semantics q01–q04, q06–q10, p01 at step 5.1 ([`test_postgres_semantics.py`](../tools/test_postgres_semantics.py)) | 141/141 (10 restart cases need durable) | 151/151 | 141/141 | 151/151 |
| Isolation CP1, RC1–RC14, LK1–LK9, SP1–SP9, SP8b, UP1–UP4, CAT1, DDL1–DDL4, SQ1 ([`test_postgres_isolation.py`](../tools/test_postgres_isolation.py)) | 44/44 | 44/44 | 44/44 | 44/44 |
| COPY ([`test_postgres_copy.py`](../tools/test_postgres_copy.py)) | 11/11, 1 documented divergence | 11/11, 1 | 11/11, 1 | 11/11, 1 |
| Crash CR1, CR2 ([`test_postgres_crash_compat.py`](../tools/test_postgres_crash_compat.py), durable+SCRAM) | — | 2/2 | — | 2/2 |

The COPY divergence is the default-profile capacity limit (a 5 000-row COPY
gets 54000 where PostgreSQL loads it); it is reported separately and never
counted as parity. A 30 000-row COPY import/export, including a durable kill
and restart, matched both references in memory and durable+SCRAM modes on the
large profile documented in the README, built (ReleaseFast) against the pinned
archive. Only the two gate reports are committed here; the per-suite JSON
reports are regenerated with the harness commands above (`--report`).
`release-gates.json` is unchanged: `postgres_compatibility` and
`capacity_concurrency` stay false, because 37/37 is necessary, not sufficient.

### Native build blocker (resolved)

During the gap-closing run the native build broke. With the previously pinned
native compiler (Sig `8b4908bb`), `main` (c57fe72) and the storage/WAL step built
a 12.6 MB SB0X image, but later steps failed: `unimplemented mul_with_overflow`
(`sql/expr.sig`), then `unimplemented tag_name` (`db/store.sig`) and an oversized
aggregate load (`sql/catalog.sig`), and from the value-types step on the compiler
itself overflowed its stack. These were native backend defects, so they were
fixed in Sig rather than worked around in ZPM or RAMSQL
([Sig PR 34](https://github.com/SB0LTD/sig/pull/34), released as Sig 0.6.4).

`NATIVE_SIG_REVISION` in `tools/release_matrix.py` is now `90a898dc` (the merged
Sig 0.6.4 release commit). On that source, with ZPM `dba76e1`, both images build
from the verified archive and pass `tools/test_sb0.py` under QEMU 8.2.2 (WSL):
SB0X 15,223,806 bytes, 599 assertions; SB0K 15,223,956 bytes, 585 assertions.
This is the same ABI-fixture evidence as before, over the UART delegated channel:
there is still no native TCP, durable storage or SCRAM, so N01 stays open.

Subsequent evidence supersedes the local-only limitation: release-matrix run
[40174568211](https://github.com/SB0LTD/ramsql/actions/runs/40174568211) passed all
eight targets and main CI run 40175410496 passed. The prior hosted compiler was
0.6.3; alpha.3 now pins checksum-verified hosted Sig 0.6.4 and requires the full
matrix and PostgreSQL qualification before release. Native full OS TCP, storage
and entropy integration remains open regardless of compiler/QEMU qualification.

## Proposed delivery order and exit criteria

This is a dependency plan, not a date estimate. Detailed estimates need the design
and acceptance scope for each wave; assigning dates now would imply precision
that the present architecture and test coverage do not support.

| Wave | Deliverable | Exit evidence |
| --- | --- | --- |
| 0 — Baseline | Preserve working durability/auth/protocol behavior; qualify the zero-copy autocommit/replay foundation. | Existing contracts, crash/upgrade fixtures, eight-target runtime evidence and retained baseline reports. |
| 1 — Contracts and memory | C01/C02 inventory, S01 resource/page/value design, stable row IDs, type/record version strategy and ownership contracts. | Larger caller-budgeted datasets, exhaustion/reuse tests, no per-connection database copy and a reviewed migration design. |
| 2 — Transactions | T01/T02 statement visibility and locking, then T03/T04 transaction behavior. | PostgreSQL isolation schedules, independent writer progress, deadlock/cancellation/abort tests and durable concurrent recovery. |
| 3 — SQL and schema | Q01 shared expressions, Q02–Q05 values, Q06–Q13 execution/schema; P01 real catalogs. | The 18 finite gaps close plus expanding upstream-derived suites and real schema/migration tests. |
| 4 — Durable lifecycle | D01/D02 formats/checkpoints/rotation and D03 backup/restore; continue D04 fault qualification. | Every crash boundary, supported-version upgrades, online backup/restore drills and bounded recovery. |
| 5 — Secure client compatibility | A01–A03, P02–P04, M01/M02 and required application matrices. | Authenticated/encrypted independent clients, privilege tests, COPY/tools/framework workflows and accurate advertised capabilities. |
| 6 — Concurrent/native operation | O01–O04 plus N01–N03 actual SB0 service integration and scheduling. | Supported deployment runbooks, load/cancellation/monitoring, native TCP/durable restart and OS/hardware evidence. |
| 7 — Production qualification | B01, V01/V02, representative soak/fault/application suites and release-gate review. | Reviewed efficiency targets achieved; all gates passed; exact published artifacts verified. |

Some work can proceed independently once its interfaces are agreed: protocol
tests, type semantics, fault harnesses, catalogs, security primitives and native
OS capabilities. Do not let that create duplicate storage engines or incompatible
transaction/WAL designs. Integration and closing evidence are part of every wave.

## Review worksheet

For each gap we discuss, fill in this record and link it to an issue/PR when work starts:

```markdown
Gap ID and title:
Practical application/operational example:
Confirmed current behavior and evidence:
Required compatible behavior:
Implementation layer and code owner:
Prerequisites / affected formats and APIs:
Implementation tasks:
Reference, failure, concurrency and platform tests:
Performance/memory budget:
Evidence needed to close:
Open decisions:
Status / linked issue / linked PR / verification report:
```

Decisions to settle during review, without silently weakening the target:

- Which PostgreSQL majors, real applications, extensions and operational topologies must be in the acceptance matrix?
- Does replacement include existing PostgreSQL physical files/native extension binaries, or compatible application/SQL/tool interfaces with migration? Each requires explicit engineering, not an implied claim.
- Which native SB0 environment/hardware and OS capabilities define the full server deployment?
- What native-PostgreSQL workload envelope and relative CPU/memory/latency/throughput targets establish “more efficient”?
- What recovery-time/recovery-point, availability, backup-retention and upgrade guarantees are required?
- What complete MySQL and SQL Server version/client/dialect matrices are required alongside the PostgreSQL default?

## Evidence required to change the production gates

| Gate in `release-gates.json` | Required closing evidence |
| --- | --- |
| `postgres_compatibility` | C/Q/T/P coverage, upstream-derived regressions/isolation tests and real application/tool workflows; not only 37 probes. |
| `implemented_subset_durability` | Preserve its current pass while expanding atomicity, crash recovery and concurrent durability tests with each new feature. |
| `authentication` | A02/A03 plus authenticated MySQL/TDS and adversarial role/access tests. |
| `transport_security` | A01 encrypted independent-client tests, peer verification and required channel binding. |
| `capacity_concurrency` | S01–S03/T01–T04/O01 realistic datasets/connections and compatible concurrent progress. |
| `backup_restore` | D01–D03 upgrade/checkpoint/online backup/restore evidence. |
| `matched_performance` | B01 equivalent-semantics, equivalent-durability representative measurements meeting reviewed targets. |
| `operations` | O02–O04/P04 supported deployment/runbook/monitoring/tooling evidence and full platform qualification. |
| `storage_power_failure` | D04 controlled storage/power-loss evidence on supported deployment stacks. |
| `release_allowed` declaration | A final review of all evidence above; only then change the declaration to true. |

## Reference specifications

These define behavior to test against; they are not evidence that RAMSQL already
implements it. References use PostgreSQL 18 so the plan is anchored to a named
version rather than an unversioned moving target.

- [PostgreSQL transaction isolation](https://www.postgresql.org/docs/18/transaction-iso.html): statement visibility, concurrent updates and isolation guarantees.
- [PostgreSQL frontend/backend protocol](https://www.postgresql.org/docs/18/protocol.html): messages, session flow, authentication, COPY and cancellation.
- [PostgreSQL backup and restore](https://www.postgresql.org/docs/18/backup.html): logical/file backup, archiving and point-in-time recovery expectations.
- [PostgreSQL regression tests](https://www.postgresql.org/docs/18/regress.html): broader SQL/TAP coverage and evaluation discipline.
- [PostgreSQL pgbench](https://www.postgresql.org/docs/18/pgbench.html): reference benchmark tooling; running it successfully is itself a compatibility requirement to test.

## Read optimization follow-up — 26 October 2026

The [read-path evidence](../benchmarks/read-20261026/REPORT.md) supersedes the
earlier aggregate and filtered-read numbers below. Exact committed summaries
accelerate eligible unfiltered aggregates; bounded ordered hash probes accelerate
dense integral primary-key LIMIT reads, including shuffled physical layouts.
Old snapshots, own writes, wide/sparse key domains and unsupported aggregate
shapes retain ordinary execution. These are specialized paths, not general
secondary or composite indexes. Metadata costs and import work are recorded.

Current priorities are general ordered access/compact scans for changed-key and
mixed workloads, followed by the unchanged capacity, streaming, durable-write,
concurrency and PostgreSQL compatibility gates. A favorable read microbenchmark
does not close any of those production gates. Published alpha.4 predates this
work; follow the report's exact source identities.

## Earlier comparative evidence — 25 October 2026, alpha.4 candidate

[IRDB 1.10.821 comparison pack](../benchmarks/irdb-review-20261025/REPORT.md)
contains three dataset sizes through one million rows, raw trials, verified result
digests and exact package/binary/source identities. IRDB's table is compiled and
its id clustered index is enabled. Native API and server/client tables are
separate. The candidate does not pass the overall workload-support gate.

The measured blockers determine the next engineering work:

1. **Streaming/hash aggregation:** grouped aggregation with only 64 groups fails
   at 100,000 and 1,000,000 input rows. Workspace must follow group state rather
   than materializing one key/cell set per input row.
2. **Ordered/secondary indexes and planning:** the filtered ordered read scans
   the entire RAMSQL table while PostgreSQL can use an ordered access path.
   Index maintenance must preserve MVCC, rollback and recovery correctness.
3. **Compact page/value storage and vectorized scans:** million-row aggregate
   and sort costs remain behind IRDB. External typed buffers remove the compiled
   row-array ceiling but retain inline cells and equal per-table reservations.
4. **Streaming results and byte-based session/resource budgets:** fixed text,
   schema, packet, result and connection limits are still production blockers.
5. **Scheduling and safe durable batching:** volatile-memory comparisons do not
   close the earlier concurrent durable-write gap or lifecycle requirements.

Do not mark production or efficiency gates passed merely because a million rows
can now be inserted or because the selected 512-row workloads favor RAMSQL.
