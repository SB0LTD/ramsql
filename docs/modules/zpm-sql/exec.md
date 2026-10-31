# Module: `exec` and `sort` (query execution)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/exec.sig`, `zpm/src/core/sql/sort.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | `exec.sig` 3240 lines, 205 KB; `sort.sig` 96 lines, 4 KB |
| Owner lane | B10 (route equivalence), C9 (compact storage, decomposition) |
| Status | beta |
| Formatting (`sig fmt --check`) | `exec.sig` dirty; `sort.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`Exec(config)` evaluates bound plans under a statement snapshot. It covers
scans, the primary-key probe, WHERE, projection, ORDER BY, LockRows, joins,
grouping, DISTINCT, subqueries, CTEs, set operations, window functions and
ROLLUP. All work happens in the caller-owned statement workspace.

`sort.sig` provides the stable bottom-up merge sort and the top-k heap that
the executor uses.

The module has one **reference route**, the general operators, and many
**specialized routes** that a plan takes when it matches their shape and the
data is in a state they can use. Route choice is automatic. There is no switch
that forces the reference route (STO-12). This page lists every route so B10
can attach a coverage counter and an equivalence test to each.

Out of scope: parsing and binding (`binder.sig`), expression semantics
(`expr.sig`), and row versioning (`storage.sig`).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `runQuery` (2725) | fn | Run a general plan into `result`. `RowCapacity` is checked before `result` is cleared (2731-2732). |
| `queryRows` (2747), `rowAt` (2754) | fn | INSERT ... SELECT rows left in the workspace until the next reset. |
| `select` (3090) | fn | Simple single-table SELECT with an optional row locker. Every lock wait and fallible evaluation finishes before `writeHeader` (3217). |
| `candidates` (2781) | fn | Top-level `pk = constant` probe under the snapshot (`storage.lookupVersionAt`). |
| `keep` (2773) | fn | WHERE keeps a row only when the predicate is TRUE. |
| `NoLock` (2791-2796) | struct | Locker for plain reads. |
| `StreamStats`, `ReadStats`, `Work` (51-122) | struct | Workspace, plus per-statement counters showing which route ran. |
| `mergeSort` (`sort.sig:51`) | fn | Stable. On a comparator error, `items` is still a complete permutation (69-72, 94). |
| `offerTopK`, `offerTopKSlot` (`sort.sig:11-48`) | fn | Bounded max-heap of the best k handles. |

## 3. Dependencies and layering

- Imports: `types`, `ast`, `storage`, `expr`, `binder`, `value`, `sort`,
  `catalog` (6-13).
- Imported by: `sql.sig` (the facade, `X`).
- Tables are taken as `*const`. `runQuery` and `select` cannot mutate
  database state, which `ReadContext` relies on.
- Layer rule: no violation found.

## 4. Invariants

- **INV-EXEC-1** (route equivalence): for every plan, snapshot and database
  state, every route returns the same multiset of rows as the reference route
  (the same sequence when ORDER BY fully determines it) and the same error
  class. FV-14 covers the factorized routes. This is unverified for the
  always-on routes in the table below.
- **INV-EXEC-2** (committed-projection gate): a route that reads `scan_*`
  arrays, `summary`, `rollups`, `integer_bounds` or presence filters runs only
  when all of the following hold:
  - `committed_only`;
  - `summary.valid`;
  - `summary.csn ≤ snap.csn`.
  Verified at 649, 960, 1213, 1365-1366, 1538/1557, 1703, 1799, 1873, 2919,
  3008 and 3046. Dimension tables are read through `lookupVersionAt(snap)`
  (1446, 1643, 1921).
- **INV-EXEC-3** (failure atomicity): a failing query leaves `result`
  untouched (header comment 3-5; 2731-2732; 3204-3217).
- **INV-EXEC-4** (fallback): `streamGroup`, `dimensionGroup` and
  `nestedFactorGroup` fall back to the reference route only on
  `WorkspaceCapacity` and only when the plan is repeatable. The workspace mark
  and stream stats are restored (2003-2008, 2488-2494, 2495-2508). Any other
  error propagates.
- **INV-EXEC-5** (read-only): no route writes through `tables`.
- **INV-EXEC-6** (sort): `mergeSort` returns a stable sorted permutation;
  `offerTopK` keeps exactly the first k of that order under the same tie rule
  (FV-13).
- **INV-EXEC-7** (accumulator storage): `Accumulator` alignment ≤ `Cell`
  alignment (comptime check at 516). Accumulator cells are never read as
  tagged `Cell`s.
- **INV-EXEC-8** (layout order): `layoutOrder` (2814) skips the sort only
  when the first ORDER BY key's column has a strict increasing or decreasing
  proof over every version in every slot (`storage.sig` `noteOrder`,
  574-601).

## 5. Current status and evidence

Routes at `2f060c2`. "Off switch" means a comptime `Config` field that removes
the route; RAMSQL's server enables all of them (`src/server.sig:92-97`).

| Route | Entry (`exec.sig`) | Off switch | Evidence today |
| --- | --- | --- | --- |
| **Reference**: `join` → `group` (sort-based) or `project` → `finish` | 334, 450, 408, 2308 | none needed | `test_sql_query.sig` (63,417 assertions) |
| Window functions | `windowCore` 2439 | none; this is the only implementation | `test_sql_windows.sig` (78 lines, example oracle) |
| ROLLUP | `groupingRollup` 2361 (own hash aggregation) | none; this is the only implementation | `test_sql_grouping_rollup.sig` (87 lines) |
| Nested factor (CTE producer → PK join) | `nestedFactorGroup` 1537 | `factorized_aggregates` | `test_sql_factor_cte.sig`: Fast/Slow twin configs |
| Dimension-ID grouping | `dimensionGroup` 1871 | `dimension_group_ids` | `test_sql_dimension_group_ids.sig`: twin configs |
| Factorized PK join | `factorGroup` 1364 | `factorized_aggregates` | `test_sql_factor_join.sig`: twin configs plus a scalar model |
| Rollup synopsis | `rollupGroup` 686 | `summary_groups = 0` | `test_sql_rollup.sig`: model oracle |
| Wide and typed dense groups | `wideDenseGroup` 1702 → `typedDenseGroup` 1727 | `compact_integral_scans` | `test_sql_wide_dense.sig`: model oracle |
| Compact scans with masks | `compactGroup` 959, `countSumDense`/`countSumMasked` 937/946 | `compact_integral_scans` | `test_sql_compact_scan.sig` |
| Block presence rejection | `comparisonMask` 870-872 | `block_presence_filters` | `test_sql_block_presence.sig`: twin configs plus a live-row model |
| Typed multi-group | `typedMultiGroup` 1798 | `typed_grouped_aggregates` | `test_sql_typed_multi_group.sig`: twin configs |
| Summary aggregates (no GROUP BY) | `summaryStates` 2043 | none | `test_sql_summary.sig`: model oracle |
| Streamed join cursor with prefix and cascade rejection | `prepareStreamCursor` 1207, `nextStreamTuple` 1264, 1075-1113, 1192 | none | `test_sql_stream_prefix.sig`, `test_sql_stream_group.sig` |
| Dense integer grouping | `denseDomain` 753 → `denseGroup` 766 (from 2070) | none | `test_sql_dense_group.sig`: one `g+0` comparison with the reference route |
| Streamed hash grouping | `streamGroup` 2060-2426 | none | `test_sql_stream_group.sig` |
| PK probe with dense-key speculation | `candidates` 2781 → `storage.sig:629-637` | none | `test_sql_index.sig` `randomModel`, `test_sql_ordered_probes.sig` |
| Layout order (skip sort) | `layoutOrder` 2814, used at 3119 | none | `test_sql_layout_order.sig` |
| Dense primary scan | `densePrimaryScan` 3007 | `ordered_probe_rows = 0` | `test_sql_ordered_probes.sig` |
| Projected filter scan | `projectedFilterScan` 3043 | `compact_integral_scans` | `test_sql_projected_filter.sig` |
| Integer top-k | `integerTopK` 2916, used at 3157 | none | `test_sql_integer_topk.sig` |
| Generic top-k heap | `offerTopK` at 3163 | none | `test_sql_query.sig` |

The newer config-gated routes are compared against a "Slow" twin database
built with the route disabled. That pattern is the model for B10.1. The eight
routes with no off switch are compared only against hand-built oracles, except
the single `g+0` comparison in `test_sql_dense_group.sig`.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-12 | medium | testing | No single switch forces the reference route. `core` (2485-2512) and `select` (3119-3163) dispatch automatically. Eight routes in section 5 have no off switch. This is the same root as SQLF-08 (PLAN §3.3), which B10 also owns. | Add `route = .reference` (B10.1). It disables every row of section 5 except the reference row; window functions and ROLLUP keep their only implementation. | B10 |
| STO-17 | low | robustness | `std_assert` is `if (!ok) unreachable` (2720-2722), used at 138, 189 and 279. It is undefined behavior in ReleaseFast, which the benchmarks use. | Replace with an explicit error return, or `@panic` with a message. | B10 |
| STO-19 | low | maintainability | 3240 lines in one generic type: about a dozen specialized aggregation engines plus window functions and ROLLUP, much of it dense one-line style (for example 1364-1530, 1798-2000). | Split by route behind the route flag, after B10 equivalence is in CI. | B10, C9 |
| STO-20 | medium | testing | Only the config-gated routes have twin-config comparisons. Always-on routes have model oracles only. There is no randomized route-equivalence generator. | B10.2 to B10.4 checklist below. | B10 |

## 7. Security considerations

- **Input:** bound plans from authenticated SQL.
- **Denial of service** is bounded by the workspace (54000) and
  `ExecutionControl` checkpoints. A fallback retry (INV-EXEC-4) is charged
  against the same work budget (2506-2507), so it cannot bypass limits.
- **Wrong results** are the main risk. A route that diverges from the
  reference silently returns plausible wrong answers (STRATEGY principle 1).
  INV-EXEC-2 matters most: a projection read under an old snapshot would
  return rows from the wrong point in time.
- **Memory safety:** accumulator cells reuse workspace `Cell` slots
  (`accumulators`, 514-520). STO-17 is undefined behavior in ReleaseFast.

## 8. Tests

**Existing:** the evidence column of section 5, plus
`test_sql_work_control.sig` (cancellation and work limits) and
`test_sql_runtime_workspace.sig` (153 assertions).

**Missing:**

- [ ] B10.1: route flag, with a test that every route counter in
  `StreamStats`/`ReadStats` stays zero under `.reference`.
- [ ] B10.2: run every query of every SQL suite twice (default and
  reference). Results and error classes must be identical.
- [ ] B10.3: a seeded generator for each route's shape, covering:
  - PK inner joins, dense and wide keys;
  - NULL-heavy and skewed data, i64 extremes;
  - DISTINCT counts, LIMIT without ORDER BY;
  - own pending writes and other transactions' pending writes;
  - an old (resumed) snapshot;
  - after abort (stale bounds and order proofs);
  - after compaction (speculation disabled);
  - `copyFromInto` copies;
  - inline versus external storage;
  - ReadContext versus the serialized path.
- [ ] B10.3: TLP and NoREC oracles for WHERE-heavy routes (projected filter,
  presence rejection, dense primary scan).
- [ ] B10.4: a route-coverage report; each route needs at least 20 corpus
  queries.
- [ ] B10: a metamorphic oracle for ROLLUP (equals UNION ALL of GROUP BY at
  each prefix) and for window functions (equal correlated-subquery forms, or
  PostgreSQL via B7).
- [ ] B10: the fallback path: force `WorkspaceCapacity` inside each streamed
  route and check that the retried result is identical. A non-repeatable plan
  must return the error rather than retry.
- [ ] B8: an invariant checker for INV-EXEC-2 inputs: after each publish,
  `scan_*`, `summary` and `rollups` must match a committed MVCC scan (see
  `storage.md`).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-14 | Lean `Sql/Aggregate` + differential | factorized aggregation over PK inner joins equals the join-then-aggregate form (factor, nested factor and dimension routes) | todo |
| FV-13 | Lean + oracle | INV-EXEC-6: comparator is a strict weak order; merge sort is stable; top-k equals the prefix of the full sort | todo |
| (none) | differential | INV-EXEC-1 for every route in section 5 (B10.2) | todo |
| (none) | property | INV-EXEC-2: no route reads projections unless the gate holds (assert the gate in debug builds) | todo |
| (none) | property | INV-EXEC-4: fallback after injected `WorkspaceCapacity` equals the reference route | todo |

## 10. Benchmarks

- S1: instruction counts per route. A route that loses its speed advantage is
  a candidate for removal.
- S2: native SQL point, filter, top-k, group and 3-way join at 10k to 1M rows.
- S7: dbMeter.
- S8: TPC-H-derived queries.
- Each fast path should record its speedup over `.reference` once B10.1
  exists. A route without a measured benefit and 20 covering queries should be
  removed (B10.4).

## 11. Open questions

- Should the route flag be comptime (zero cost, but two builds) or a runtime
  `ExecutionControl` field (one build, a branch per route)?
- Is `groupingRollup` meant to stay a separate hash engine, or become the
  reference grouping applied once per prefix?
- Which always-on routes (dense group, layout order, integer top-k, key
  speculation) should get `Config` switches like the newer ones?

## Offset-window inventory candidate � 31 October 2026

The candidate implements LAG/LEAD over deferred arguments and sorted partition
handles. Selected-row values and current-row offset/default expressions retain
PostgreSQL evaluation roles; defaults are lazy. Root single-window prefix demand
can stop at OFFSET+LIMIT when final order/projection maps to window order.
Non-total/nonrepeatable nested, multi-window or uncertain-demand combinations
return0A000 until the executor can propagate demand; this is not full closure.
Partition keys now sort ASC NULLS LAST, with sequence tests over NULL groups.
Internal window rows retain the existing2*columns target budget.

ZPM docs/SQL_WINDOW_OFFSETS.md records the supported scope,90 native PG16
fixtures and regressions found by independent review. Existing window/frame
contracts are retained. RAMSQL adds live PG16/18 matrix, parameters, binary,
sequences, NULL groups, demand and durable materialization/restart cases.
Hosted qualification is pending. Other window and SQL inventory families remain
open; no performance or complete compatibility claim is made.

## Ranking/distribution inventory candidate �31 October2026

The next candidate adds percent_rank, cume_dist and ntile. cume_dist scans peer
runs once; ntile uses constant per-partition state and retries leading NULL
counts before caching the first non-NULL positive count. The full partition
size remains its denominator even after leading NULLs. Invalid initial counts
raise22014 only when execution consumes them. Existing demand guards apply to
sensitive NTILE arguments. No allocator/new per-row storage is introduced.
Immutable frame errors fold during binding even for optimized ranking frames;
zero-argument ranking fn(*) and missing OVER error behavior match PostgreSQL.

ZPM docs/SQL_WINDOW_RANKING.md gives the exact scope. Core ranking3047assertions,
62native PG16 fixtures and existing offset/window/frame suites were independently
verified. RAMSQL adds live PG16/18 matrix, binary/parameter, lazy sequence and
durable materialized-distribution checks. Hosted qualification remains pending.
Named windows/value windows and broad SQL closure remain open.


### Named-window inventory candidate

WINDOW definitions support scoped names, ordered inheritance and PostgreSQL
framed-copy restrictions. Shared keys retain one evaluation, including scalar
subqueries and volatile expressions; mutable arguments follow sorted-row order.
Root/demand restrictions remain explicit0A000 for unsafe combinations. Total
COUNT/MIN/MAX/Boolean full unsorted windows retain per-row argument interleaving.
The shared named contract covers27 native fixtures, aggregate/frame properties,
work/cancel/capacity failure and recovery. q06 adds all27 programs, binary/parameter,
CTE scope, sequence-sharing and durable materialization/replay cases. Memory
qualification explicitly skips14 restart-only cases; durable qualification skips0.
Full SQL/window closure and throughput superiority remain unproved.
