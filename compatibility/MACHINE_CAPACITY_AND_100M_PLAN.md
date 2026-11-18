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

The full-sales fact schema needs seven integral cells and one date per row.
Each MVCC version currently reserves nine full-width cells in the widened build.
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

### First actual 100M-row CI gate

Build the experimental product and run the existing wire-load contract on a
public hosted Linux runner. A prior public sample provided 14,347,884,288
new-region bytes from 16,760,344,576 bytes of physical RAM. Capture the live
sample because the runnable grant depends on other processes and headroom.

```sh
sig build -Doptimize=ReleaseSafe -Dintegral-version-banks=true
python tools/test_cli.py --expect-integral-version-banks
python -m pip install -r tools/requirements-test.txt
python tools/test_large_runtime.py --typed-bank-memory-profile --rows 100000000 \
  --memory-budget 14347884288 --report evidence/typed-bank-100m.json
```

This first gate uses a single two-word integer fact table, a small spare MVCC
budget and one server worker. It loads every row via real PostgreSQL COPY, then
checks counts, sums, bounds, selective grouping, sampled primary-key probes and
duplicate rejection against independent formulas. Preserve the artifact's hash,
sampled capacity and process memory observations. Surface a refused grant,
timeout or failed query as a failed run. Qualify durable replay, wider schemas,
concurrent workers and multi-table analytic benchmarks in their own runs.


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

## Opt-in hosted admission for wide schemas

The experimental build combines caller-owned packed table banks, 32-byte
external MVCC version headers, and a hosted PostgreSQL ceiling of 128 clients.
The regular build ceiling remains 60. The runtime defaults to 16 admitted
clients and a 50% maximum hash load. Use an explicit build profile and
runtime flags to request the measured layout.

```sh
sig build -Doptimize=ReleaseSafe -Dintegral-version-banks=true -Dcompact-version-stamps=true -Dhosted-client-ceiling=128
python tools/test_wider_admission.py --binary sig-out/bin/ramsql --report wider-native-plans.json
```

For a supported packed schema, count the bytes for all declared columns even
when rows contain NULL. A version stores one bitmap bit per column and the
fixed-width payload. Add `ceil(columns / 8)` bytes to each column's width,
then round the total up to eight bytes. Packed SMALLINT needs two bytes,
INTEGER, OID and DATE need four, and BIGINT needs eight. Declare the resulting
number of words with `--table-integral-words`. A bank needs a dense payload
when its schema contains TEXT or NUMERIC. Bank assignment is first-fit by
schema capability, so put large compatible banks before small spare ones.
The fixtures insert and read each actual schema before testing the adjacent
smaller packed budget where applicable.

A table's packed backing includes 16 bytes per row slot, the 32-byte header
and requested payload per provisioned version, and two mandatory arrays of
32-bit hash slots. Each array's slot count is the next power of two that admits
its complete row or version budget at the chosen load. At 75%, the slot count
must satisfy `slots / 2 + slots / 4 >= capacity` using integer division.
`--typed-bank-memory-profile` omits packed banks' duplicate scan projections
and optional secondary hashes. Dense banks in the same process still reserve
their configured dense payload, scan projection and secondary hashes.

For the eight-column ledger shape in the fixture the physical payload consumes
six words. This command asks the native planner about one 100M-row ledger at
128 active PostgreSQL clients with 1,024 result rows and 8,192 workspace rows.
It opens no database or listeners.

```sh
sig-out/bin/ramsql --machine-capacity --max-clients 128 --workers 2 \
  --table-rows 100000000,1,1,1,1,1,1,1 --table-versions 100065536,2,2,2,2,2,2,2 \
  --table-integral-words 6,0,0,0,0,0,0,0 --typed-bank-memory-profile \
  --packed-hash-load-75 --work-rows 8192 --result-rows 1024
```

Read `backing_bytes` for exact table and reference storage,
`startup_region_bytes` for caller-owned startup regions and worker stack
charges, and `active_admission_bytes` for the conservative full-admission
bound. The latter adds independently rounded output and result regions for
every client, context metadata and execution regions for each parallel read
worker, and a fixed engine and admission queue allowance. The fixed allowance
can include pages already counted as resident. Compare the requested bound to
`new_region_bytes` in the same native observation. The program enforces the
full bound before listening above 60 clients. Memory pressure after startup
can still reduce future grants. A fixed 65,536 version reserve is a test shape.
Size actual version headroom by write concurrency and the lifetime of pinned
snapshots.

Expanded admission currently covers a plain hosted PostgreSQL listener with
zero prepared transactions. Hosted Windows and no-libc Linux passed the 128
session contract with one and four workers. macOS passed at one worker and
explicitly refuses parallel workers. SB0 keeps its existing native listener
capacity. [Windows and Linux runs](https://github.com/SB0LTD/ramsql/actions/runs/44228649454),
[macOS run](https://github.com/SB0LTD/ramsql/actions/runs/44231434730).

Two actual 100M-row tables using one packed word each passed the separate
synchronous WAL and abrupt-restart run. All 16 join-and-sum groups and 20
ordered-range results matched independently checked values before and after
restart. The 75% profile reduced the planned active regions from
15,597,510,656 to 13,450,027,008 bytes. Recovery read the same 6,411,200,215-byte
WAL after the first process had exited. Recovery took 231.086 seconds on that
runner and peaked at 13,374,668,800 bytes of resident memory. The wider
admission matrix below is an independent capacity check. [Durable two-table run](https://github.com/benor1470/dbMeter/actions/runs/44226187387).

### Verified native admission matrix

The public Ubuntu runner granted 14,305,768,448–14,307,988,480 bytes across
33 capacity probes. All figures below are active admission bytes for 100M
rows per listed table, with two workers, 8,192 workspace rows, 1,024 result
rows and 100,065,536 versions per table. The CI fixture also ran all five
schemas over PostgreSQL with small real tables and checked that an undersized
packed bank preserved atomicity when the table fell back to a one-row dense
bank. It repeated the 128-session contract on the combined binary.
[Native plans and SQL evidence](https://github.com/SB0LTD/ramsql/actions/runs/44232687102).

| Tables at 100M rows each | Packed words per table | 4 clients | 60 clients | 128 clients | Fits sampled grant |
| --- | ---: | ---: | ---: | ---: | --- |
| One narrow INT/SMALLINT table | 1 | 6,798,659,584 | 7,136,940,032 | 7,547,711,488 | All three |
| Two narrow tables | 1 each | 13,475,020,800 | 13,813,301,248 | 14,224,072,704 | All three |
| One six-INT fact table | 4 | 9,200,234,496 | 9,538,514,944 | 9,949,286,400 | All three |
| Two six-INT fact tables | 4 each | 18,278,166,528 | 18,616,446,976 | 19,027,218,432 | None |
| One eight-column INT/DATE ledger | 6 | 10,801,283,072 | 11,139,563,520 | 11,550,334,976 | All three |
| Two INT/DATE ledgers | 6 each | 21,480,263,680 | 21,818,544,128 | 22,229,315,584 | None |
| One eight-BIGINT table | 9 | 13,202,853,888 | 13,541,134,336 | 13,951,905,792 | All three |
| Two eight-BIGINT tables | 9 each | 26,283,409,408 | 26,621,689,856 | 27,032,461,312 | None |
| One TEXT/NUMERIC invoice table | Dense | 134,342,340,608 | 134,680,621,056 | 135,091,392,512 | None |
| Two TEXT/NUMERIC invoice tables | Dense | 268,562,386,944 | 268,900,667,392 | 269,311,438,848 | None |
| One ledger and one invoice table | 6 and dense | 145,021,325,312 | 145,359,605,760 | 145,770,377,216 | None |

The 128-client two-narrow-table plan fit by about 82 MB on this runner. The
real durable two-table test used four clients and its own exact binary.
An admitted byte plan establishes backing and response headroom for the
declared profile. Wider 100M loads, two large dense banks, sustained write
churn, and simultaneous 128-client operation at 200M live rows require
separate qualification.

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
wider multi-table 100M execution and significant wins across the benchmark
matrix remain open targets.

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
