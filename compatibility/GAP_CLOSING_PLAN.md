# RAMSQL gap-closing plan

Current review: 25 October 2026, development candidate `0.3.0-alpha.3` (unpublished).
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

| Resource | Current hosted default or codec bound | Why it matters |
| --- | --- | --- |
| Connection slots | 16 by default; compiled hosted profile currently capped at 60 | Ordinary connection pools can exhaust the server. |
| Tables / columns | 8 tables; 8 columns per table in the server | Normal schemas exceed this immediately. |
| Rows | Published alpha.3 defaults to 512 per table; alpha.4 candidate adds runtime `--rows` / `--versions` backed by caller-owned buffers | The candidate removes the compiled row-array ceiling, but compact values, shared page budgets and streaming remain open. |
| Text | 128 bytes per value by default | Common names, documents and JSON exceed it. |
| Identifiers | 63 bytes in the server, ASCII and case-insensitive | Quoted names, schemas and name semantics are incomplete. |
| Transaction SQL log | 1 MiB per connection by default | Larger write transactions fail. |
| PostgreSQL SQL / packet | 4,096 bytes of SQL; 16,384-byte packet bound | Generated SQL and bulk operations exceed the codec's bounds even if the store allows more. |
| PostgreSQL prepared state | 8 statements, 8 portals, 32 parameters | Frameworks can exceed these small fixed session limits. |
| MySQL / TDS request | 65,536 bytes; TDS parameters bounded at 32 | Bulk and generated requests need a scalable path. |
| Results | Materialized into capacity-sized buffers | Large results consume substantial memory and cannot stream freely. |
| Execution | One reactor thread | A slow query or flush delays other clients and CPU capacity is underused. |

Some capacities can be changed at build time. Increasing those constants does
not close the scale gap: the dense storage, full transaction copies, protocol
buffers and scheduling model also need to change. Allocator-free execution still
needs an explicit resource budget; it must fail predictably when that budget is
exhausted rather than promising unlimited memory.

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

**Status:** Unverified beyond the existing subset. **Owner:** RAMSQL tests.

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

**Status:** Partial (flag F-S01). Stable rowids, row versions in a fixed per-table arena, a multi-entry PK index and reclamation are implemented, and per-connection database copies are gone (one shared store). There is still no variable-length value store or caller-owned pages: cells stay inline at `text_bytes` and the version arena is preallocated at 2 × rows. [Evidence](#gap-closing-run-evidence-24-october-2026).
**Alpha.4 candidate progress:** optional external row/version/index buffers,
runtime index masks/capacities and separately bound reference workspace now exist
in ZPM. A compiled four-row inline profile passes a 100,000-row test. RAMSQL
binds hosted OS page regions through `--rows` / `--versions`; a 2,048-row Windows
WAL restart and offline restore pass. Recovery validates actual bound capacity and
sizes its per-record overlay from the transaction write budget. These changes
are under qualification; published alpha.3 is unchanged. Inline cell/text storage,
equal per-table reservations and materialized results still block efficient scale.

**Owner:** ZPM storage/SQL/store; RAMSQL configures resource budgets.

Per-connection database copies have been removed. Remaining storage uses fixed
per-table row/version arrays and inline text capacity, making integer-heavy rows
and large profiles expensive. Page-backed compact values and independently
budgeted result/session workspaces remain the scale bottleneck.

- [ ] Introduce caller-owned pages and compact typed values, with a bounded variable-length value store.
- [ ] Give rows stable identities so compaction, indexes and versions do not depend on a row's current array position.
- [ ] Store private transaction changes without copying the complete database.
- [ ] Specify ownership, reuse, stale-reference protection, rollback and out-of-memory behavior before replacing the current representation.
- [ ] Keep active data in RAM; persistence must not silently turn the design into an on-demand disk database.

**Closed when:** memory use follows actual stored data and private changes rather
than `connections × maximum database size`; substantially larger datasets run
within a configured RAM budget, with no runtime heap allocation or atomicity regressions.

### S02 — Add useful indexes and scalable execution algorithms

**Status:** Partial (flag F-S02). The alpha.4 candidate adds stable bounded top-K selection for eligible column ORDER BY/LIMIT and direct cell comparisons. Merge sort and full lock-row behavior remain where pruning is ineligible. Only the PK hash index exists; secondary/ordered indexes, hash joins and streaming/vectorized aggregates remain open. **Owner:** ZPM SQL/planner/index packages.

Only a single-column primary-key hash index exists. Other filters scan rows;
sorting now uses stable merge sort, joins use nested loops and general queries
materialize intermediate rows in a bounded workspace. Stable row identities and
version reclamation replace the previous dense-row compaction model.

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

**Status:** Partial. READ COMMITTED only; REPEATABLE READ/SERIALIZABLE are rejected with 0A000 instead of being emulated; transactional DDL is tested (DDL1–DDL4). **Owner:** ZPM transactions; codecs expose the behavior.

BEGIN/COMMIT/ROLLBACK working for the subset does not establish all transaction
modes. Applications can request isolation levels, read-only transactions and
other transaction characteristics.

- [ ] Implement SET TRANSACTION and default/session transaction characteristics with real state.
- [ ] Implement and verify Repeatable Read and Serializable semantics; investigate two-phase/prepared transactions as a separate required capability.
- [ ] Cover transactional DDL, aborted commits, serialization retries and durability acknowledgements under concurrency.

**Closed when:** each advertised mode has reference-tested semantics, including
anomaly prevention and errors. Do not describe the present conservative snapshot
mechanism as PostgreSQL Serializable or complete Repeatable Read.

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
They are not represented by the current four data types.

- [ ] Record required types, casts, operators, functions, OIDs and binary formats.
- [ ] Implement shared storage/expression behavior before exposing a type through a codec.
- [ ] Cover type modifiers, NULLs, array dimensions, constraints and round trips.

**Closed when:** each type claimed in the compatibility contract has complete
behavioral and persistence evidence. Do not mark the family closed after adding
only one commonly used type.

### Q06 — Aggregates, grouping, HAVING and window functions

**Status:** Partial (flag F6). Aggregates, GROUP BY and HAVING implemented, `aggregate` and `group_by` probes `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q06 matches; no window functions or AVG(interval). [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM planner/executor.

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

Only one ORDER BY column and LIMIT are implemented. Applications also depend on
duplicate elimination, several sort keys, OFFSET and expressions in ordering.

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

**Status:** Partial (flag F10). Defaults, identity, stored generated columns and sequences with PostgreSQL's log-ahead, `defaults` and `identity` probes `match: true (PG18+PG16, memory and durable+SCRAM)`, suite q10 and crash cases CR1/CR2 match; one identity column per table, CACHE > 1 and SERIAL unsupported, clean-shutdown continuity not comparable (no graceful shutdown, O04). [Evidence](#gap-closing-run-evidence-24-october-2026). **Owner:** ZPM schema/execution/transactions; RAMSQL persists lifecycle state.

Omitted values currently become NULL. Applications expect column defaults and
generated identifiers with particular concurrency and rollback behavior.

- [ ] Store/evaluate defaults and generated-column expressions.
- [ ] Implement identity definitions, sequence objects and nextval/currval/setval behavior.
- [ ] Define durable sequence state, restart behavior, exhaustion and concurrent allocation without assuming generated numbers roll back like ordinary rows.

**Closed when:** omitted/explicit DEFAULT values, ALWAYS/BY DEFAULT identity,
rollback gaps, session sequence state and crash/restart match native PostgreSQL.

### Q11 — Schema lifecycle, constraints and transactional DDL

**Status:** Partial. **Owner:** ZPM schemas/catalog/storage; RAMSQL migration integration.

CREATE/DROP plus one primary key and NOT NULL cannot run ordinary migrations.
Changing columns or enforcing relationships requires real schema management.

- [ ] Implement schemas, ALTER TABLE, rename, TRUNCATE and schema-qualified objects.
- [ ] Add composite keys, UNIQUE, CHECK, foreign keys, referential actions and required deferrable constraints.
- [ ] Make DDL transactional where PostgreSQL is transactional; coordinate schema locks and prepared-plan invalidation.
- [ ] Version persisted schema definitions and support validated schema changes during recovery/upgrades.

**Closed when:** representative migrations run forward/backward, concurrent DDL
and queries behave correctly, and failure cannot leave half-applied schema/index state.

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

**Status:** Missing/unverified. **Owner:** Reusable language/execution support in ZPM; RAMSQL selects supported facilities.

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

**Status:** Unverified beyond the selected independent-driver tests. **Owner:** RAMSQL integration/qualification.

One client library succeeding does not prove psql, pg_dump, pg_restore, pgbench,
connection poolers or framework migrations work.

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

**Status:** Missing. **Owner:** ZPM reusable checkpoint/log primitives; RAMSQL orchestration.

The WAL grows with all historical changes, and restart replays that history.
Deleted historical states can still require capacity during replay.

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

**Status:** Missing. **Owner:** ZPM reusable TLS/transport; RAMSQL certificate and listener policy.

Hosted listeners currently bind loopback and PostgreSQL clients use sslmode=disable.
SCRAM authenticates credentials but does not encrypt query text or result data.

- [ ] Implement a tested native TLS server transport with certificate/key loading and safe lifecycle handling.
- [ ] Add PostgreSQL SSL negotiation and required SCRAM channel binding; integrate MySQL/TDS TLS negotiation separately.
- [ ] Handle certificate rotation, invalid/expired credentials, handshake resource limits and protocol downgrade tests.
- [ ] Enable configurable remote endpoints only with the corresponding authentication/access policy.

**Closed when:** independent clients verify encryption, peer identity and required
channel binding; malformed handshakes fail safely and cannot starve other sessions.

### A02 — Roles, permissions and database isolation

**Status:** Missing beyond a configured PostgreSQL login. **Owner:** ZPM roles/catalog/authorization; RAMSQL provisioning policy.

One accepted username/verifier is not a multi-user authorization system. Users
need ownership and permissions enforced for reads, writes, schemas and administration.

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

One reactor handles queries and synchronous persistence. A costly query or flush
can hold up unrelated clients; increasing connection slots alone does not fix it.

- [ ] Introduce resumable query/lock/I/O work and fair scheduling.
- [ ] Add worker ownership/coordination after storage and transaction invariants are established.
- [ ] Replace the small descriptor/connection ceiling with scalable platform readiness mechanisms and budgeted session state.
- [ ] Evaluate safe group commit; acknowledge only after the required durable flush, and define publication/failure behavior before batching commits.

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

The current benchmark uses a 512-row subset, five workloads and 1/4 clients.
Alpha.2's local recovery/validation was 27 ms versus alpha.1's 698 ms for the same
2.17 MB WAL. Some write measurements improved; PostgreSQL still leads durable
writes. These results do not demonstrate the requested overall efficiency target.

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

## Latest comparative evidence — 25 October 2026, alpha.4 candidate

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
