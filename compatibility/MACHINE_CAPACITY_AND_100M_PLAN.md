# Machine-driven capacity and 100M-row workload target

Status: accepted engineering target, not an achieved benchmark or production
claim. The user's requirement is automatic use of the available machine:
allocator-free code, all usable cores and available RAM, multiple tables with
about 100M rows each, complex joins, sums and aggregations. A larger fixed row
constant or a larger executable does not satisfy this requirement.

## Current evidence and blockers

The [reviewer follow-up](REVIEWER_BETA_2.md) records actual million-row loading,
runtime query/result budgets, unequal table banks, protocol corrections and
experimental socket reactors. The current inline row/version layout still
requires roughly 253 GB of backing for one 100M-row bank before other costs.
Uniformly reserving that capacity for every table is worse. This layout must be
replaced; successful 1M-row tests cannot be extrapolated into 100M-row claims.

SQL execution is still serialized. Socket workers increase throughput in one
measured workload but increase CPU/query. Schema/value/parser/connection bounds,
fully materialized intermediates, per-client response reservations, absent
checkpoints and incomplete dialect support remain visible blockers.

## Resource ownership and planning

Infrastructure progress on 2026-10-27:

- [ZPM resource-budget/grant core](https://github.com/SB0LTD/zpm/pull/32) is merged.
  It calculates a shared byte budget from supplied physical/commit/scope/CPU
  observations, with checked arithmetic and generation-checked concurrent grants.
  Native OS discovery and RAMSQL admission integration are still open.
- [Effective-capacity headroom correction](https://github.com/SB0LTD/zpm/pull/34)
  derives defaults from container/operator capacity rather than large host totals.
  Windows validation passes 25,042 assertions and 80,000 concurrent grant cycles;
  cross-platform qualification is pending.
- [Packed integer storage](https://github.com/SB0LTD/zpm/pull/33) has 72,320
  Windows/Linux correctness assertions. An explicit Windows ReleaseSafe test
  generated, encoded, parsed and decoded every one of 100M actual values in each
  of two cases. Frames occupy 337,500,080 bytes without NULLs and 350,000,080 bytes
  with mixed NULLs; exact sums and every value passed. The retained
  [evidence](https://github.com/SB0LTD/zpm/blob/d0500cf1ca499d9f68758ed662d54d0dc1229be3/tests/evidence/packed-integer-100m-windows.json)
  records source/binary hashes. Cross-platform large-column qualification is
  pending. These are physical-column tests, not SQL, durability or comparative
  benchmarks; the RAMSQL release does not yet use this primitive.

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
