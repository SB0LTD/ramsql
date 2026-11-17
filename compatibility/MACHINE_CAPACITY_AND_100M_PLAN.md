# Machine-driven capacity and 100M-row workload target

Status: accepted engineering target, not an achieved benchmark or production
claim. The user's requirement is automatic use of the available machine:
allocator-free code, all usable cores and available RAM, multiple tables with
about 100M rows each, complex joins, sums and aggregations. A larger fixed row
constant or a larger executable does not satisfy this requirement.

## Current evidence and blockers

The original dbMeter workload passed 38 result checks each for RAMSQL,
PostgreSQL and IRDB in [CI run 44103107594](https://github.com/benor1470/dbMeter/actions/runs/44103107594).
That includes all seven full sales checks on the six-table dataset with five
million fact rows. The RAMSQL build at `2da350be` used 32 table slots, nine
columns per table and 200 bytes per text value. All three original capacity
probes also passed with those build options. The four-worker full-sales plan
reported 11,587,103,312 legacy startup bytes on the hosted runner. The exact
published build in [CI run 44100105604](https://github.com/benor1470/dbMeter/actions/runs/44100105604)
passed the result checks and returned `54000` on the three capacity probes.

The widened build's three-second timing smoke left both full-sales mixed cases
without enough valid samples. Primary-key throughput ranged from 0.86× to
1.11× the faster reference across the four scored size-scaling cases. The
[full-duration campaign](https://github.com/benor1470/dbMeter/actions/runs/44104864502)
retains three 60-second trials per case, result and capacity checks, and raw
latency data. Keep the performance gate open until the repeated rate and tail
latency requirements pass on every original case.

The full-duration mixed workload measured 1.38 and 3.21 queries/s for RAMSQL
and 56.26 and 77.02 for IRDB, respectively at one and eight clients. The same
runner comparison for the date-formatting optimization improved the original
region/month query 4.77× at one client and 5.13× at eight versus the RAMSQL
control. Those runs used the original SQL and expected results.
[Full-duration comparison](https://github.com/benor1470/dbMeter/actions/runs/44104864502) ·
[Date optimization](https://github.com/benor1470/dbMeter/actions/runs/44111073448)

The five-million-row packed-bank experiment passed all 114 original result
checks across the three engines and all six capacity probes. RAMSQL's
reported startup reservation fell from 11,587,103,312 to 1,982,847,056 bytes
with the same executable and capacity profile, an 82.9% reduction. The
experiment changes the six-word payload for the integral and date sales fact
table. This measures planned startup bytes. The opt-in layout still needs
heterogeneous schema, DDL, replay, reclamation and multi-table 100M-row tests.
[Packed-bank CI evidence](https://github.com/benor1470/dbMeter/actions/runs/44121640322)

The two completed typed ROLLUP trial pairs measured roughly 30–34× the
RAMSQL control's throughput for the original segment/discount query. The
third pair hit the runner's memory budget. Retry on matched packed-bank builds
to obtain a complete score. Keep the gate open for the unchanged mixed workload.
[Incomplete ROLLUP campaign](https://github.com/benor1470/dbMeter/actions/runs/44117394260)

The full-sales fact schema needs seven integral cells and one date per row.
The dense MVCC layout reserves nine full-width cells in the widened build.
Compact typed MVCC payloads are the storage priority. Establish that inserts,
updates, ordered and unique indexes, chained snapshots, reclamation and logical
WAL replay agree with dense storage before using a compact layout in the server.
Measure exact bytes per version and resident memory on the original five-million
row load, then qualify multiple 100-million-row tables with concurrent queries
and durable restart on hardware with sufficient admitted memory.

The full-sales region/month check took 2.84 seconds on RAMSQL, 1.07 on PostgreSQL
and 0.20 on IRDB in the smoke job's result check. The segment/discount rollup
took 1.89, 1.33 and 0.32 seconds respectively. Profile formatting and grouping
in the first query, and repeated prefix updates in the second. Preserve date
and time-zone semantics, null groups, aggregate counts and numeric precision.
Use isolated original queries to locate CPU and queue costs, then require the
unchanged mixed workload to pass the full gate.

### Earlier capacity evidence

The [actual five-million-row campaign](../benchmarks/dbmeter-full-five-million-20261028/REPORT.md)
loads the original full data into the default text-128 build using unequal
runtime banks, 65k spare fact versions, 65k workspace rows and 1,024 final result
rows. It uses about 7.06 GB private commit after loading. Totals and two original
queries pass; four queries still require missing formatting/window/ROLLUP
features. This is memory-only read acceptance with explicit budgets, not
automatic capacity growth, full-suite support or 100M-row durability acceptance.
Pure ROUND classification fixes a false input-materialization failure; simply
increasing workspace eightfold did not fix that operator selection.

The [reviewer follow-up](REVIEWER_BETA_2.md) records actual million-row loading,
runtime query/result budgets, unequal table banks, protocol corrections and
experimental socket reactors. The current inline row/version layout still
requires roughly 253 GB of backing for one 100M-row bank before other costs.
Uniformly reserving that capacity for every table is worse. This layout must be
replaced; successful 1M-row tests cannot be extrapolated into 100M-row claims.

Published beta.2 serializes SQL. Development now supports concurrent simple
autocommit PostgreSQL simple and eligible extended reads; transactions, suspended
portals and mutations remain exclusive. The reviewer's actual dbMeter JDBC suite
now reproduces concurrency and passes 7/7 million-row sales-subset checks with the
default Windows text-128 build and an explicit Linux text-32 profile, using
unequal runtime banks. Eight-client analytics reach 28.5 / 43.6 queries/s for
Windows text-128 / text-32; IRDB reaches 1,053.6 in the matched reference trial.
Exact integral COUNT(DISTINCT) uses bounded bitmap states. Schema/value/parser/connection bounds,
fully materialized intermediates, per-client response reservations, absent
checkpoints and incomplete dialect support remain visible blockers.

## Resource ownership and planning

Infrastructure progress on 2026-10-27:

- [ZPM resource-budget/grant core](https://github.com/SB0LTD/zpm/pull/32) is merged.
  It calculates a shared byte budget from supplied physical/commit/scope/CPU
  observations, with checked arithmetic and generation-checked concurrent grants.
  Native OS discovery and RAMSQL admission integration are merged in
  [ZPM #35](https://github.com/SB0LTD/zpm/pull/35) and
  [RAMSQL #16](https://github.com/SB0LTD/ramsql/pull/16). Windows/Linux/macOS
  native tests and all six hosted artifact executions passed.
  [ZPM #36](https://github.com/SB0LTD/zpm/pull/36) corrects macOS reclaimable-cache
  observation; hidden scope ancestors and native SB0 discovery remain gaps.
- [Effective-capacity headroom correction](https://github.com/SB0LTD/zpm/pull/34)
  derives defaults from container/operator capacity rather than large host totals.
  Windows validation passes 25,042 assertions and 80,000 concurrent grant cycles;
  the three-OS database platform matrix passed.
- [Packed integer storage](https://github.com/SB0LTD/zpm/pull/33) has 72,320
  Windows/Linux correctness assertions. An explicit Windows ReleaseSafe test
  generated, encoded, parsed and decoded every one of 100M actual values in each
  of two cases. Frames occupy 337,500,080 bytes without NULLs and 350,000,080 bytes
  with mixed NULLs; exact sums and every value passed. The retained
  [evidence](https://github.com/SB0LTD/zpm/blob/d0500cf1ca499d9f68758ed662d54d0dc1229be3/tests/evidence/packed-integer-100m-windows.json)
  records source/binary hashes. The 100M-value checks also passed on native Linux and macOS. These are physical-column tests, not SQL, durability or comparative
  benchmarks; the RAMSQL release does not yet use this primitive.

- [Independent read contexts](https://github.com/SB0LTD/zpm/pull/37) pass hosted
  contracts on Windows, Linux and macOS. Eight native Windows/Linux workers
  execute 2,000 checked SQL queries with forced overlap, and the shared gate
  passes 12,000 reads / 4,000 exclusive publications. Server integration checks
  actual wire overlap, cancellation, timeout recovery and writer exclusion.
  [Retained wire trials](../benchmarks/parallel-read-wire-20261027/results.json)
  show higher throughput and higher CPU/query; they do not establish a win over
  PostgreSQL or IRDB. Compact SQL storage, intra-query parallelism, automatic
  worker sizing and removal of schema/connection caps remain open.

These changes are outside the published beta.2 dependency pin. Do not attribute
their behavior or physical-column sizes to the release's inline SQL layout.

1. **Observe effective resources (ZPM platform).** Read CPU affinity, processor
   groups/NUMA topology and process/container CPU quotas. Read available physical
   RAM, process residency/commit, system commit limits and job/cgroup memory
   limits. Do not mistake host totals for resources available to a container.
   Unsupported observations must be reported, not replaced with invented values.
2. **Calculate a shared budget (ZPM core).** Use checked byte arithmetic and a
   configurable OS/service headroom. A memory-only database must consider both
   physical availability and commit limits. Account for memory already owned by
   the process without double-counting it. Reduce new grants under pressure;
   never revoke a live query's memory or discard committed data.
3. **Own memory explicitly (ZPM storage).** Caller-owned OS page regions, typed
   spans and generation-checked page/grant records replace heap allocation.
   Reserve address space separately from committing/touching pages. Tables draw
   pages from one database budget as they grow; unused tables do not receive the
   largest table's reservation. Reuse pages only after snapshot ownership ends.
4. **Schedule from that budget (RAMSQL).** Schema widths, resident data, active
   snapshots, WAL/recovery needs and live query grants determine admission and
   parallelism. The scheduler can use every usable core across the workload;
   one bandwidth-bound query need not create one busy thread per logical CPU.
   Keep short requests responsive beside long joins/scans and account for SMT
   and NUMA rather than merely creating more threads around a global mutex.
5. **Expose decisions.** Report detected limits, headroom, committed/resident
   pages, table occupancy, execution grants, effective workers and pressure.
   Explicit operator budgets remain available. Legacy --rows/--versions bounds
   must not remain the default storage model or be presented as automatic planning.

## Compact storage first

Reusable typed column/page primitives belong in ZPM. Fixed-width values use
their physical widths and separate null bitmaps; an integer must not reserve a
128-byte text payload. Variable values use caller-backed segmented byte storage
and checked offsets, with dictionary/compression paths where beneficial.

Published immutable segments form the large base; sparse version/delta storage
records changes. Do not reserve two full inline copies of every row just because
MVCC exists. Snapshot pins, publication ordering and reclamation must remain
correct under concurrent readers, updates, deletes, rollback and restart.
Late materialization constructs protocol values only for selected/output rows.

Exit checks: exact type/NULL/overflow semantics; bounds and alias rejection;
failed writes leave data unchanged; compressed/dictionary and scalar paths agree;
measured bytes per row; schema-dependent preflight; real SQL load/recovery. A
standalone packed-array benchmark is not evidence that the server uses it.

## Bounded execution and parallel reads

Development progress: [ZPM #38](https://github.com/SB0LTD/zpm/pull/38)
streams scalar/group aggregates over primary-key inner joins. Windows and no-libc
Linux check 100,000 actual SQL-loaded fact rows with 512 handles/1,024 cells,
including pending dimension snapshots and cancellation. Live server tests use
32 workspace rows with one/four workers. The [matched revision benchmark](../benchmarks/streamed-join-wire-20261027/README.md)
reduces median latency from 34.6 to 16.1 ms at 100,000 rows. These are execution
and RAMSQL-revision results, not reference-engine or 100M-row storage acceptance.
[ZPM #39](https://github.com/SB0LTD/zpm/pull/39) adds borrowed operands and
snapshot-guarded integral gathers. The [corrected three-revision comparison](../benchmarks/final-joined-read-20261027/README.md)
measures about 34.0 / 16.4 / 7.0 ms, with work-budget regression checks preserved.
This reuses an existing integer projection; stored rows/versions are still inline.
Non-key/outer joins and general projections still materialize; per-worker query
reference reservations still include a legacy term proportional to maximum table
capacity. Automatic core sizing, per-query grants and compact physical SQL storage
remain necessary before claiming effective whole-machine capacity planning.

- Filter/project before joining when semantics permit. Stream vectors between
  operators; do not build a full sales/products intermediate before a selective
  predicate can run. Preserve outer joins, NULLs, correlated and volatile semantics.
- Implement budgeted hash/radix, merge and indexed joins with measured cardinality
  estimates and skew handling. Partition/repartition within grants; do not hide
  disk spill in an advertised in-memory run. Retain failures and outliers.
- Aggregate into bounded local states with checked exact arithmetic and correct
  NULL/DISTINCT behavior. Combine partition results correctly; bound state for
  high-cardinality groups. Top-k and full ordering need separate budgeted paths.
- Move parser, binder, execution scratch and mutable caches into owned worker/query
  contexts. Immutable base segments plus snapshot/version ownership permit
  concurrent reads. A global SQL mutex is not the final design. Publication stays
  serialized where required, with durable flush before visible commits.
- Use portable baseline binaries and safe runtime SIMD dispatch. The earlier
  native-CPU release attempt failed on other x86 runners and was rejected.

Exit checks include random differential queries, concurrent mutation/snapshots,
cancel/timeout at operator checkpoints, no starvation, memory pressure, process
loss, corruption and durable restore. Measure CPU/query as well as throughput.

## Benchmark matrix and acceptance

Use the actual reviewer repository `benor1470/dbMeter`, immutable source revision
`630fbb13be21bd4bbdf7afe4b438ce771a8b3fc0`, as a separate JDBC tier. Record driver
and supplied IRDB client hashes and preserve its original SQL/CSV expectations.
The [reviewer report](../benchmarks/dbmeter-reviewer-20261027/REPORT.md) retains
failures and native-platform comparisons. Do not conflate these results with the
native API tier below. The full 5M-row analytic suite, stock-profile capacity,
100M-row multi-table storage and universal/significant wins remain open targets.

The fresh [guarded-input reference run](../benchmarks/guarded-reference-20261027/README.md)
and [remaining cases](../benchmarks/guarded-reference-remaining-20261027/README.md)
pass correctness and the finite speed screen in all ten cases at 100k rows.
These are single-worker native SQL-string calls; no joins or durable server
comparison is included. IRDB's public `prepareQuery` path needs a separate
matched experiment, especially before interpreting the large point/mutation
ratios. Retain both SQL-string and prepared tiers instead of substituting one
for the other. Broad reference-engine superiority remains an unachieved target.

Scale steps: 1M, 10M and 100M rows **per table**, with 2- and 4-large-table
variants. Include narrow integers, decimal amounts, nullable/dictionary strings,
wide/high-entropy payloads, uniform/shuffled/skewed keys and realistic selectivity.
Retain deterministic generators, independent expected values and exact hashes.

| Family | Required cases |
| --- | --- |
| Reads | PK hits/misses, point/range filters, sparse/dense predicates, narrow/wide projections |
| Aggregates | COUNT/SUM/AVG/MIN/MAX, filtered sums, NULL/DISTINCT, low through very high cardinality GROUP BY/HAVING |
| Joins | 2/3/4-way, chain/star/large-to-large, inner/outer/semi/anti where supported, selective predicates, skew and mismatched keys |
| Ordering | ORDER BY LIMIT 10/1k, multi-key order, full ordered streaming and large results |
| Mixed work | Concurrent short reads and large scans/joins; 5%/20% writes; update/delete churn; snapshot/version pressure |
| Operations | Bulk import, indexing/compression cost, checkpoint/recovery/restore, durable commit and crash correctness |

Concurrency spans one client through the machine's usable core budget. Retain
median and tail latency, throughput, server CPU/query, RSS/commit/peak workspace,
load/index cost, errors and timeouts. Warm/cold state, schema, data, isolation and
durability must match. Compare native APIs separately from PostgreSQL wire
clients. No winner may be inferred across those boundaries.

Working performance target: at least 2x the fastest applicable reference per
declared case, across repeated trials, with correctness and resource checks
passing and no material tail-latency regression. This is an objective, not a
claim. Unsupported SQL, OOM, timeouts and incomplete runs are failures/gaps,
never omitted or converted into wins. A finite matrix cannot prove superiority
for every possible query.

Reference versions were checked on 2026-10-27: the official
[IRDB NuGet page](https://www.nuget.org/packages/InMemory.Net.irdb) lists 1.10.821;
[PostgreSQL's version policy](https://www.postgresql.org/support/versioning/)
lists 18.6 as the current supported 18 release. Pin actual packages/images and
checksums for every run and recheck versions before a new comparison campaign.

## Implementation order

1. Portable beta artifact gates and actual beta.2 publication are complete; keep
   its limitations explicit.
2. Add pure resource-budget/grant contracts, then real platform observations.
3. Add compact typed pages and sparse-version ownership; integrate into SQL,
   persistence and recovery before making server capacity claims.
4. Add bounded join/aggregate operators and per-query execution contexts.
5. Add parallel reads and budget-aware scheduling across all usable cores.
6. Run the matrix, profile the largest remaining costs, optimize and repeat.
   Preserve old results, regressions and counterexamples throughout.
