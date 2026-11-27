# Changelog

Release changes are separated from aspirations. See each tag's exact qualification
and benchmark bundle; development measurements do not automatically apply to a
later binary.

## Unreleased

- Add a candidate RSBASE10 compact image for live functions/procedures, delegated
  routine ACLs, triggers and rules in pure Sig in ZPM. Extend the checkpoint
  fixture with live generation publication, replacement WAL and reopen; require
  stock PG16/18 archives in both directions before qualification. Other catalog
  classes and full catalog snapshot parity remain open.
  The expanded archive fixture exposed missing routine owner changes and rule
  definitions; the candidate adds those in ZPM, wide definition output and
  qualified trigger callbacks, and reduces wide-cell evaluation stack usage.
  Default ACL construction also uses caller-owned storage for full 63-byte role
  names; native outbound archives remain required qualification.

- Add schema-scoped routine default privileges in pure Sig in ZPM, qualified with
  stock PG16/18 plain/custom archives in both directions, future routine ACL
  probes, concurrent default visibility and crash/WAL recovery. Global
  grants/options combine with per-schema additions;
  arbitrary routine namespaces and full catalog snapshots remain open.

- Support global ALTER DEFAULT PRIVILEGES ON FUNCTIONS/ROUTINES in ZPM,
  including PUBLIC, grant options, rollback and future function/procedure ACLs.
  Verify committed-default visibility during concurrent edits, own-transaction
  creation, WAL recovery and stock PG16/18 archives. RSBASE09 preserves default
  templates for future routines; live routine catalog snapshots remain open.

- Qualify all 106 PostgreSQL inventory smoke programs against frozen PG18.3
  in CI, retaining the unchanged strict comparator. Run database lifecycle in
  the documented cluster profile and record every scope's actual configuration.
  Full feature-inventory closure and current native MySQL/T-SQL lanes remain open.
- Support delegated EXECUTE ACLs for SQL functions/procedures, ON ROUTINE
  lists, public-qualified expression/CALL/DROP, empty-path lookup rules and
  immutable routine window inputs in pure Sig in ZPM. Verify qualified functions,
  aggregate/window composition, volatile sequence arguments, privileges and WAL
  recovery through stock PG16/18 archives in both directions.

- Support function/grantee lists and `GRANTED BY` current-user validation
  for EXECUTE grants in ZPM. Qualify duplicate and quoted recipients,
  atomic list revocation, independent delegation chains, WAL recovery and
  stock PG16/18 ACL archive round trips in both directions.

- Add persistent EXECUTE grants for `lo_import(text)`, `lo_import(text, oid)`
  and `lo_export(oid, text)` through ZPM. Test delegated and inherited access,
  grant-option cascades, forced-kill WAL recovery and stock PG16/18 plain and
  custom archive round trips with ACLs enabled.

- Expose PostgreSQL's pristine `template0` and initialize the writable
  `template1` on its first connection. Support durable database copies
  with independent WAL identity and validate stock cluster archive restore.

- Build hosted archives with 32 tables, 9 stored columns per table and
  200-byte text values. Verify these capacities with a client and WAL reopen
  on the exact release binary.

- Preserve standalone sequence definitions and their WAL reservation
  boundary across checkpoint publication and crash recovery. Existing
  table-only images retain their original format.
- Add PostgreSQL float8 degree trigonometry with `sind` and `cosd`.

- Add opt-in live checkpoint rotation for a single durable PostgreSQL
  database. Bootstrap from an offline WAL, then publish sealed generations
  and switch the active WAL under the stable writer lock. Release tests
  kill and reopen the server during checkpoint publication and WAL handoff.

- Add PostgreSQL TLS 1.3 with SCRAM channel binding, encrypted cancellation
  and explicit native workers. Linux x86-64 and ARM64 tests exercise real
  PostgreSQL clients, COPY, prepared SQL, TLS key updates and WAL reopen.
- Add multi-command PostgreSQL simple-query handling and extended
  Parse/Bind/Execute transactions committed at Sync. Extended COPY shares
  that transaction boundary. COPY inside a simple batch is tracked separately.
- Integrate stock PostgreSQL plain-dump import, session settings and
  catalog relation identities. Export and general pg_restore remain open.
- Add optional 32-byte MVCC headers and a packed tier for eligible cached
  projected reads, with explicit memory charges and invalidation.
- Add the optional 128-client hosted PostgreSQL build and caller-provided
  readiness storage. Keep the configured connection default at 16.
- Begin idle-in-transaction timeout accounting after the response drains.


- Add the opt-in PostgreSQL database cluster with separate database storage
  and WALs, authenticated database routing, CREATE/DROP DATABASE and
  `pg_database` discovery. This beta profile supports four databases, one
  reactor and one configured login.
- Add opt-in durable PostgreSQL two-phase DML, including recovery of unresolved
  transactions, lock ownership and commit/rollback decisions after restart.
  Cluster mode shares the prepared identifier namespace and capacity grant
  across database stores.
- Accept a SCRAM credential list for multiple login roles on the single-database
  listener. Bind authenticated clients to the privileges of their catalog roles.
- Size the default PostgreSQL read-reactor pool from native CPU limits,
  available memory and the configured connection ceiling. `--workers 1` fixes
  one reactor. One execution lane owns writes.
- Include typed integral banks in hosted release binaries. Support opt-in
  lean banks, bounded joined Top-N, and linear replay validation for durable
  100M-row fact tables.
- Return PostgreSQL admission errors when the connection budget is exhausted.
- Keep notification cursors on the serialized path and align TRUNCATE
  visibility with WAL replay when an older snapshot is open.
- Use the owning PostgreSQL session for catalog lookups, session functions,
  and temporal conversions that need a named-zone resolver.
- Route reads through PostgreSQL table locks while exclusive DDL holds a lock
  or waits ahead of them.
- Add PostgreSQL enum and composite catalog definitions with transactional
  WAL replay. Support standalone enum literal casts and flat, typed `ROW(...)`
  composite projections with PostgreSQL text and binary wire values.
- Parse MySQL backtick identifiers and two-argument `LIMIT`. Support signed
  integer literal result metadata, signed integer `IFNULL`, and `DATE_ADD`
  with day and month intervals for dates and canonical ISO date strings.
- Parse T-SQL bracket identifiers and literal `SELECT TOP`. Support signed
  integer `ISNULL`, calendar `DATEADD` on typed dates, and native TDS 7.3+
  DATE result framing. See the compatibility plan for remaining dialect scope.
- Support named-relation `VACUUM` and `ANALYZE` for ordinary and materialized
  tables without descendants. Reclaim MVCC versions beyond the live snapshot
  horizon and rebuild committed row, column and primary-key summaries.
- Run the PostgreSQL differential suite against an isolated native Windows
  PostgreSQL installation using `tools/test_postgres_compat.py --postgres-bin`.
- Support transactional SMALLINT, INTEGER and BIGINT domains with one simple
  CHECK comparison, optional NOT NULL and standalone explicit casts. Preserve
  PostgreSQL base-type wire metadata and recover domain definitions from WAL.
- Support PostgreSQL JSON EXPLAIN for simple SELECTs. ANALYZE reports the
  observed root operation and its actual output cardinality. Plan-only mode
  binds the query without evaluating it.
- Match PostgreSQL command completion for CREATE AS, SET CONSTRAINTS, indexes,
  MERGE, views and materialized view refresh. Support CSV `FORCE_QUOTE` on
  `COPY TO STDOUT`, including named output columns. The Windows candidate
  passed all eight associated native PostgreSQL 18.3 wire probes.
- Retain running and COPY statement snapshots during reclamation, including
  resume under version pressure. Exhaustion returns 53200 with statement
  rollback instead of silently skipping rows (STO-02).
- Persist globally pending sequence log-ahead watermarks when a consumer
  commits while the owner remains live (STO-04). Preserve subsequent cache-hit
  commits without an extra record. Add ascending/descending replay, process-kill
  and PostgreSQL crash differential regressions.

- Prevent timestamp literal, DATE conversion, calendar arithmetic and interval
  accumulation/`ago` overflow from terminating the server. Reject unsupported
  date offsets typed BIGINT; reject subtraction of infinite dates. Preserve
  native PostgreSQL precision rounding and signed interval boundaries. Add
  full-year-range core properties, PG16/PG18 server boundary cases and native
  SB0X/SB0K SQLSTATE/session-survival probes.

- Extend ZPM window execution with bounded ROWS/GROUPS, integral-key RANGE
  offsets, frame exclusions and aggregate FILTER. Use caller-owned exact prefix
  states and MIN/MAX ordinal trees, with charged scalar fallback. Add eight
  independent PostgreSQL semantic cases for frames, errors, binary parameters,
  deferred expressions and volatile evaluation. The combined source passes the
  three-OS SQL matrix and all 32 active server/release/native checks; broader
  window functions and language coverage remain incomplete.

- Qualify the prior reporting pin: all seven original full-5M and subset dbMeter
  correctness checks pass for RAMSQL, PostgreSQL and IRDB in run 41024725859.
  PG16/PG18 memory/durable+SCRAM and all 32 active hosted/native/release checks
  passed in runs 41024653323/41024653363. The full-data timings retain a substantial
  IRDB lead; this is correctness progress, not an all-engine performance claim.

- **Behavior change:** durable startup no longer creates a missing data file.
  Pass `--create-data` on the first start; otherwise a missing `--data` path
  (default `ramsql.wal`, relative to the working directory) fails with
  `DataFileMissing` and its absolute path before any listener opens. Startup
  prints the absolute data file path to stderr (SRV-01).

- Add ranking and aggregate windows after grouping/HAVING, including partition
  and peer ordering, total shares before LIMIT, and unbounded prefix/full ROWS
  frames. Add flat ROLLUP/GROUPING with exact independent subtotal state and
  NULL-level separation. Both use caller-owned ZPM query storage; remaining
  forms and qualification requirements are documented explicitly.

- Add temporal `to_char` through ZPM for timestamp/date report expressions,
  including session-zone formatting and bounded caller-owned output. Add Q03
  PostgreSQL differential cases and a three-way region/month report. Numeric,
  interval, localized and ISO-week formatting remain documented gaps.

- Fold exact grouped customer membership over contracted unique dimension
  chains using caller-owned bitmaps and typed COUNT/SUM/AVG state. Repeated
  values across different keys sharing one label count once; unsupported
  expressions, visibility and domains retain canonical SQL. Performance and
  release qualification remain explicit CI gates.

- Precontract eligible unique-key dimensions into compact group IDs before
  folding mixed dimension/fact grouping keys over committed columns. Dictionary
  and aggregate state borrow the existing query arena, with canonical SQL
  fallback for unsupported expressions, visibility and budgets. Qualification
  and performance measurement remain separate CI gates.

- Keep multiple direct COUNT/SUM/AVG channels in typed caller-owned group state
  for eligible committed column scans. This covers two-sum grouped CTEs while
  preserving existing specialized paths and ordinary SQL for unsupported
  expressions, snapshots and budgets. No local throughput measurement or
  all-workload speedup is claimed; qualify the candidate in CI.

- Validate single-character LIKE escapes through the scalar UTF-8 decoder.
  Preserve malformed-input rejection while supporting native SB0 toolchains
  without the whole-buffer validator's vector reduction.

- Enable ZPM's guarded factorized aggregate operators: aggregate fact measures
  before unique-key dimension joins, and carry typed COUNT/SUM producer state
  through eligible grouped CTE/derived consumers. Both borrow initialized query
  scratch and retain canonical SQL for unsupported expressions, snapshots,
  nullable/wide producer totals or insufficient workspace. Runtime measurements
  and full integration qualification are tracked in the candidate PR; no new
  benchmark leadership, SQL parity or 100M-row capacity is claimed here.
- Add conservative block-presence summaries to the committed scan projection.
  Equality can skip a block only when the value's residue bit is absent; every
  surviving block retains exact value/null/live comparisons. Metadata borrows
  charged caller backing and follows durable publication, replay, copy and slot
  compaction. Detailed repeated timings and the retained earlier regressions are
  tracked with the candidate; full-suite benchmark leadership remains unproven.
- Reuse binder-validated DATE payloads, refine sparse predicate survivors before
  join probes, and prove when grouped aggregates can use 16-byte state in ZPM.
  Three original five-million-row trials measure median q5/q6 throughput of
  151.00/1369.80 queries/s, with zero timed errors and 3/7 original checks passing.
  Preserve rejected fingerprint/cancellation experiments and host-variation
  controls in the streaming report. IRDB remains ahead; the live advantage gate
  has not passed and dbMeter remains unmerged.
- Select safe leading predicates before streaming dimension probes; preserve
  throwing ON/probe behavior and complete residual evaluation. Discover fact
  columns per query block, batch visibility masks, preserve typed DATE values
  in committed projections, and fold bounded wide integral groups in existing
  caller-owned scratch. Retain original five-million-row checks, profiles and
  positive/negative timing results in the
  [streaming campaign](benchmarks/dbmeter-streaming-20261028/REPORT.md).
  IRDB leadership, full SQL parity and 100M-row SQL capacity remain unproven.
- Correct Windows workspace admission in ZPM: distinguish commitment from
  residency when applying app/job limits, and count process-private ownership
  separately from app/shared usage. Qualify an untouched native mapping against
  independent OS counters and retain isolated five-million-row JDBC comparisons
  with PostgreSQL and IRDB, including the remaining query and performance gaps.
  Three eight-worker selective-join trials improve median throughput from 3.30
  to 11.90 queries/s; this remains slower than either reference on that query.
- Add PostgreSQL ROUND numeric/float8 overloads, preserve NULL/type/signed-zero
  behavior, and guard extreme-scale arithmetic. Keep pure ROUND expressions on
  bounded streaming aggregate paths while retaining sequence/subquery guards.
  Load the reviewer's actual five-million-row data with the default text build;
  totals and two original query checks pass. Four full-suite SQL feature gaps
  remain. Retain failures and exploratory performance deficits explicitly.
- Validate direct row-directory candidates for dense integer primary keys using
  canonical snapshot visibility and exact key checks, with ordinary hash/MVCC
  fallback. A fixed per-table hint adds no query workspace or per-row index.
  Three matched million-row JDBC trials improve median throughput from 29.1 to
  32.8 queries/s; IRDB remains substantially faster on the analytic mix.
- Reproduce the reviewer's dbMeter JDBC suite with its original SQL/expected
  results and supplied IRDB Java client. Add a benchmark driver with immutable
  source checks, per-engine provenance, bounded JVM heaps and retained failures.
- Execute eligible PostgreSQL extended autocommit reads concurrently, including
  private Describe metadata, bound portals and named prepared binary queries.
  Keep transactions, COPY and suspended portals exclusive. Check actual extended
  overlap, publication exclusion, cancellation, timeout recovery and 640 binary
  prepared queries alongside the existing live worker contracts.
- Filter committed non-null integral predicates before fetching full MVCC rows;
  preserve exact scalar short-circuit work counts and fallbacks. Batch work
  charging between observable polls with 76,801 equivalence assertions. Avoid
  clearing/copying full prepared SQL capacity for short Parse/Bind messages.
- Stream exact integral COUNT(DISTINCT) states from caller-owned bitmap storage.
  The reviewer's million-row sales subset passes 7/7 checks on Windows with the
  default text-128 build and Linux with an explicit text-32 profile, using unequal
  runtime table banks. Sparse/wide unsupported domains retain exact general
  fallback; this does not provide 100M-row physical SQL storage.
- Retain a fresh ten-case IRDB 1.10.821 native SQL-string replay at 100k rows.
  Every result passes and every case wins the finite speed screen, including
  sort, range, grouped, mixed and churn cases. Prepared-query comparisons,
  multiple large tables and 100M-row SQL capacity remain unqualified.
- Borrow arithmetic and PK-probe operands, reuse visible key hits, and gather
  current committed integral fact inputs from the existing column projection.
  Preserve checkpoint charges, NULL/error evaluation and MVCC fallback. In a
  corrected matched 100k joined-read experiment, median latency is 7.0 ms versus
  16.4 ms for streamed versions and 34.0 ms for materialized joins. Raw profiles,
  source/binary hashes and all checked trials are retained; reference-engine
  superiority and 100M-row physical SQL capacity remain unproven.

- Stream scalar/group aggregates over primary-key inner joins without building
  input-sized intermediate tuples. Checked live queries load 100,000 fact rows
  and two dimensions and succeed with 32 workspace rows; the preceding binary
  returns 54000 on the same fixture. Outer/non-key/DISTINCT shapes retain their
  existing bounded behavior. The matched single-client memory-only benchmark
  reduces median latency from 34.6 ms to 16.1 ms on one Windows host; this is a
  RAMSQL revision comparison, not a PostgreSQL/IRDB or 100M-row capacity claim.
- Discover effective machine resources on Windows, Linux and macOS, and charge
  server page regions and worker allowances to a shared admission ledger. Add
  `--machine-capacity`, `--memory-budget` and `--os-headroom` diagnostics/options.
- Allow concurrent PostgreSQL simple autocommit reads with multiple native
  workers. Execution contexts are owned per worker and reused between clients, sharing
  table storage under a reader/writer
  gate; mutations and reclamation remain exclusive. Read memory is budgeted,
  with exclusive fallback when private buffers cannot be admitted.
- Check concurrent wire execution, cancellation/timeouts, writer exclusion and
  uncommitted visibility. Add `profile-reads` for checked native phase timings.
- Retain checked wire measurements: eight clients/four workers reach about
  130k PK queries/s versus 67k with one worker on this Windows host, while CPU
  cost rises from 14 to 27 microseconds/query. This is an outstanding efficiency
  cost, not evidence of PostgreSQL/IRDB superiority or 100M-row SQL readiness.

## 0.3.0-beta.2 â€” 2026-10-27

- Bind query scratch and per-client result/response storage from caller-owned OS
  page regions. Runtime `--work-rows` and `--result-rows` no longer require rebuilds
  or put giant arrays in the executable. Default rows/workspace 4,096, results
  8,192, parser expressions 8,192; runtime clients up to 60 (default 16).
- Per-slot `--table-rows`/`--table-versions` avoid uniform largest-table budgets.
  Reservations remain explicit and bounded; inline values still cost memory.
- Push safe available predicates into all-inner joins before intermediate emit.
  Outer/volatile/correlated cases retain conservative evaluation.
- Fix uniqueness checks using a compiled mask against runtime-sized PK indexes.
- Increase prepared-source storage and return recoverable 54000 errors after
  bounded draining of oversized authenticated SQL frames. Return PostgreSQL
  FATAL 53300 on SQL pool exhaustion through a separate bounded admission queue.
- Experimental Windows/no-libc Linux `--workers` for socket/protocol reactors,
  caller-owned native thread controls, serialized shared SQL, generation-bound
  cancellation and joined cleanup. Default one worker; macOS/SB0 remain one.
- Native Linux release builds use Sig's pinned no-libc public runtime I/O patch
  and ZPM's native vDSO wall clock. Compiler/matching standard library remain
  Sig 0.6.4; public runtime archive identity is recorded and checked separately.
- Independent reviewer regressions cover 5,000 rows, 500-row/four-value INSERTs,
  seven aggregates, 2,000 returned rows, selective three-way joins with tiny
  scratch, 32 transactions, unequal-bank restart and duplicate-key rejection.
- Checked wire PK measurements show improvement with four reactors at eight
  clients on one Windows host. This is not evidence of parallel SQL execution,
  PostgreSQL parity or universal superiority; see the reviewer follow-up report.

The beta.1 source review was not a published release: its PR workflow validated
artifacts but skipped publication by design. Beta.2 is
[published with assets](https://github.com/SB0LTD/ramsql/releases/tag/v0.3.0-beta.2)
from `defacb76de8f2c95bdf4f80b2575fbfde791d8cf`. The explicit publication
[workflow](https://github.com/SB0LTD/ramsql/actions/runs/40452582057) passed.
All 28 checksummed files and eight target packages were independently verified
after download; the Windows package also executed `--version`/`--capabilities`.

## 0.3.0-beta.1 (source review; unpublished)

### Added

- `--capabilities`: effective runtime row/version/backing budgets and compiled
  SQL/protocol/security limits, without starting a server or touching the WAL.
- Independent build budgets for columns, result rows, row locks, WHERE predicates,
  expression nodes and FROM relations. A 16-column/1,024-result profile is tested
  through libpq with 12-column data, 600 returned rows and 200-byte text.
- A packaged, exclusive-create PostgreSQL SCRAM verifier helper and live
  onboarding test. No password-in-argv option; printable ASCII helper passwords.
- README, capacity guide, operations, implemented SQL reference, audit and staged
  roadmap. Release packages include the guides and credential helper.
- Checked production trace/replay tooling, independent PostgreSQL model checks,
  corruption rejection and retained IRDB comparison evidence.

### Performance and implementation since alpha.4

- ZPM exact committed scalar/group summaries, dense-key ordered probes,
  caller-backed compact integer columns, bounded vector/scalar predicate masks
  and specialized COUNT/SUM scans. Pending changes and old snapshots retain MVCC.
- Readiness-gated accepts and native wall-clock improvements; optimized bounded
  grouping/top-k paths. All remain allocator-free with explicit memory costs.
- All 21 finite read comparisons against IRDB 1.10.821 passed at 10k/100k/1M
  rows; 10 additional wide-profile cases passed. Original long million-row churn
  timed out in IRDB, so its original matrix remains 29/30. See raw evidence.

### Corrected

- Removed stale alpha candidate/large-aggregation-loss wording from the current
  overview; historical reports are retained with their actual source identities.
- Distinguished stored-row capacity from returned-row/workspace/schema limits,
  dialect adapters from full dialects, and SB0 memory transports from hosted TCP/WAL.

### Still limited

No TLS/roles, complete PostgreSQL/dialect parity, online backup/checkpoints/WAL
compaction, multi-worker engine or unbounded result streaming. Stock profile
defaults remain small and visible. This is a scoped prerelease, not production
graduation. No WAL format change or general downgrade guarantee is introduced.

## 0.3.0-alpha.4

- Hosted runtime row/version buffers, exact native SB0X/SB0K release artifacts,
  checksum/provenance and executable hosted/native release validation.
- PostgreSQL-first shared MVCC server, synchronous typed WAL/recovery, SCRAM,
  offline validated backup and independently tested MySQL/TDS adapters.
- Initial scale/reference benchmarks. Later optimization evidence identifies
  newer source revisions and must not be attributed to the published alpha.4 binary.

Earlier alpha history is preserved in Git tags and historical compatibility and
benchmark documents; it is not evidence of full production compatibility.
