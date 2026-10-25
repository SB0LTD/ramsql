# Allocator-free database infrastructure: research and engineering decisions

Research cutoff: **25 October 2026**. Status: **research and proposed design**;
the changes described below are not implemented by this document. No new runtime
performance result or production-readiness claim is made here.

Code inspected: RAMSQL `93b5b7d5b01026330a69e02607e19a9414a80410`,
ZPM `20dea8b148d68cb5894c32dc9cb2d48f09fba00b`. These are the alpha.4
development candidate and its pinned dependency, not the published alpha.3.
This document supplements the [gap-closing plan](GAP_CLOSING_PLAN.md),
[execution contract](EXECUTION_CONTROL.md), and
[measured IRDB comparison](../benchmarks/irdb-review-20261025/REPORT.md).

## 1. Decision

Build a **budgeted, resumable execution engine over compact, versioned storage**.
Prioritize eliminating unnecessary work and memory traffic before replacing
individual algorithms with theoretical breakthroughs. Use different physical
operators for point reads, ordered ranges, scans and joins. Make their selection
observable and keep a correct general path for unsupported fast-path shapes.

The first implementation should be streaming scalar/hash aggregation in ZPM,
followed by ordered access paths and compact typed storage. Develop durable
commit scheduling alongside that work, with independent crash qualification.
Concurrency and compressed execution follow explicit ownership contracts.

The objective is to exceed competing engines on a growing, published workload
suite under equivalent semantics and durability. There is no evidence here that
one design can be fastest for every dataset, machine and workload. Optimize the
throughput/latency/memory trade-off with measured decisions; keep compatibility
and correctness as gates rather than variables to trade away.

## 2. What the current evidence actually says

The existing comparison used five trials, ten warmups and independently checked
results on an i9-14900HX under WSL2. Native and wire measurements are separate.
IRDB was pinned to `InMemory.Net.irdb 1.10.821` with its compiled table and
clustered `id` index. See the linked report and its raw JSON for provenance.

| One-million-row workload | RAMSQL native ops/s | IRDB native ops/s | Infrastructure implication |
| --- | ---: | ---: | --- |
| Hot primary-key lookup | 1,014,507 | 3,226 | Preserve the working direct lookup; diversify beyond one hot key. |
| Filter plus ordered LIMIT | 42 | 190 | Add ordered/secondary access and avoid full scan when justified. |
| Top-16 ordering | 17 | 55 | Reduce scan traffic and expression cost; the bounded heap alone is insufficient. |
| COUNT and SUM | 32 | 491 | Fuse visibility/filter/aggregate in a streaming scan. |
| 64-group COUNT and SUM | WorkspaceCapacity | 435 | Stop materializing keys for every input row. |

The wire filter case is 32 ops/s for RAMSQL versus 4,423 for PostgreSQL 16.
This is a particularly strong signal to inspect access paths, not just hash
probe speed. These selected tests do not reproduce the unavailable reviewer
suite and are not a complete DBMS benchmark.

Code inspection explains several costs:

- `zpm/src/core/sql/exec.sig`, `Query.sourceRows`, creates a handle for each
  visible source row. `Query.join` materializes tuple handles, even for simple
  single-source general plans. Fixing only group-key storage still leaves this
  input-sized intermediate.
- `Query.group` currently evaluates `N * key_count` full Cells, sorts row
  ordinals and then aggregates equal-key runs. A million rows with 64 groups
  still requires a million key Cells. Increasing the workspace postpones the
  failure without fixing its cause.
- `types.sig` stores each value in a tagged union containing inline Text,
  JSONB and numeric variants. `storage.sig` embeds a configured-width Cell
  array in every version. An integer therefore pays the union's largest-payload
  size and unused columns/version capacity consume backing memory too.
- `ramsql/src/server.sig`, `openStorage`, requests equal per-table backing and
  input-sized reference capacity. Runtime mapping removes one compiled row
  ceiling; it does not provide shared compact storage or bounded streaming.
- The reactor still executes a query synchronously. Cancellation polling does
  not make other queries run while a scan owns the workspace.

The native million-row benchmark records **537,165,824 backing bytes plus
407,888 metadata bytes** for its one-table, four-column profile, including two
million version slots and reference workspace. The input has three SQL INT
columns and a five-byte string per row: 17,000,000 logical payload bytes before
any database metadata, alignment, string lengths or MVCC. That comparison
identifies a memory-layout opportunity; it is not an apples-to-apples resident
memory ratio against IRDB. Reservation, committed pages, working set and live
payload must all be reported separately. The hosted eight-table profile is a
different budget and must be measured separately.

## 3. Meaning of allocator-free

Allocator-free does not mean every SQL field or table must have a small compiled
array limit. It means the runtime does not ask a general heap allocator to
manufacture arbitrary objects as queries execute.

The proposed contract is:

1. The embedder supplies typed buffers or a page region with explicit ownership.
   Hosted products may obtain that region through ZPM's OS mapping adapter at
   startup. Native SB0 supplies equivalent memory capabilities through its ABI.
2. Storage uses fixed page formats and bounded directories; operators borrow
   declared ranges; sessions use bounded slot/ring structures. No `Allocator`
   interface, hidden `malloc`, moving object heap or general-purpose arena is
   introduced under a new name.
3. A storage page directory may transfer whole fixed pages between owners.
   This is domain-specific storage accounting, not permission to add an
   arbitrary-size object allocator. Page size, generations, ownership states,
   cancellation cleanup and worst-case metadata are part of its public contract.
4. Every capacity is a byte budget or an explicitly documented representational
   bound. Exhaustion yields admission waiting or a defined error; no silent
   truncation, eviction of visible versions, or automatic query spill to disk.
5. Initial implementations operate entirely within supplied capacity. Online
   capacity expansion is a separate operation requiring stable handles, a
   validated added-region directory and an atomic admission protocol.

Finite physical RAM necessarily imposes a capacity. The engineering objective
is to remove arbitrary tiny per-value/per-schema/per-session ceilings and use
available memory predictably, rather than claim unlimited in-memory storage.

### Memory accounting

Use disjoint ownership categories and checked arithmetic:

```text
M_total >= M_directory + M_live_payload + M_retained_versions + M_indexes
         + M_catalog + M_sessions + M_operator_leases + M_WAL_rings
         + M_checkpoint_scratch + M_emergency_reserve

M_operator_leases = sum(active operator reservations)
M_sessions       = sum(admitted session state + borrowed I/O buffers)
```

Metadata is included, not hidden outside the configured budget. Report reserved,
committed, live and reclaimable bytes separately. Reject invalid budgets before
binding or mutating storage. Keep an emergency reserve for abort/disconnect,
commit completion and recovery bookkeeping so exhaustion cannot make cleanup
itself require unavailable memory.

Long snapshots retain versions. If updates produce `u` retained bytes/second
and the oldest snapshot lags by `t` seconds, approximately `u*t` additional bytes
may be needed before metadata and compaction overhead. This is a sizing model,
not a hard bound: enforce measured retention budgets and an explicit transaction
timeout/admission policy. Never reclaim a version merely to satisfy the budget.

## 4. Research findings and decisions

Each item distinguishes the source's result from our proposed use. Published
paper speedups are not RAMSQL predictions. Links point to original authors,
institutions, proceedings or official documentation. Older work is included
where it provides a stronger practical baseline than newer proposals.

### R1. Purpose-built group tables, rather than a general concurrent map

**Source:** Daniel Xue and Ryan Marcus,
[Global Hash Tables Strike Back! An Analysis of Parallel GROUP BY Aggregation](https://www.vldb.org/pvldb/vol19/p523-marcus.pdf),
PVLDB 19(3), 2025; presented in the VLDB 2026 cycle. Sections 2–4 and Table 3.
The paper separates group ticket assignment from aggregate updates. Its
experiments show a shared table can compete with partitioned aggregation;
atomics favor high cardinality and lower contention, while thread-local state
handles skew at greater memory cost. It does not establish a universal winner.

**Our decision:** start with single-owner group tickets and typed aggregate
states. Later compare shared tickets plus local partials against radix
partitioning, using bounded worker slices. Do not introduce a concurrent map
before resumable scans and workspace ownership are correct. A global table
must publish a completely initialized key/state before another worker reads it.

### R2. SIMD hashing is useful, but architecture-dependent

**Source:** Böther et al.,
[Analyzing Vectorized Hash Tables Across CPU Architectures](https://www.vldb.org/pvldb/vol16/p2755-bother.pdf),
PVLDB 2023. The authors evaluate vectorized linear probing, fingerprinting and
bucket comparisons on x86, ARM and Power, including skew and different payloads.
Their results explicitly expose ISA-dependent performance and implementation
trade-offs.

**Our decision:** benchmark a scalar control-byte/fingerprint table first, then
Sig-vector implementations on x86_64 and AArch64. Fingerprints only reject
candidates: full SQL key equality remains mandatory. Use row/group IDs as table
payloads so bucket probing does not move or copy whole Cells. No AVX-512, BMI2
or SVE instruction may leak into a generic release target without dispatch or a
target guarantee. Test mask tails, unaligned inputs and page boundaries.

### R3. A genuine hashing breakthrough, with a narrow model

**Source:** Farach-Colton, Krapivin and Kuszmaul,
[Optimal Bounds for Open Addressing Without Reordering](https://arxiv.org/html/2501.02305v2),
arXiv revision February 2025. Theorems 1–2 study insertion sequences at load
`1-delta`, without relocating inserted entries, under their stated randomness
and parameter assumptions. Elastic hashing obtains constant amortized expected
successful-search probes and logarithmic worst-case expected probe complexity;
funnel hashing gives a squared-logarithmic greedy bound and refutes Yao's
conjecture in that model. Expected bounds are not deterministic latency bounds.
The paper explicitly explains why unrestricted insert/delete histories differ.

**Our decision:** experimental candidate for insert-only statement-local group
ticket tables or sealed-segment indexes. Do not replace the mutable MVCC primary
index on the strength of this theorem. Benchmark misses, construction, occupancy,
adversarial/skewed keys and cache traffic against ordinary bucket probing. At
our present primary-index load of at most 50%, this is lower priority than
removing entire scans and input-sized intermediates.

### R4. Ordered indexes: engineered B-trees remain a strong default

**Sources:** Müller, Benson and Leis,
[B-Trees Are Back: Engineering Fast and Pageable Node Layouts](https://www.cs.cit.tum.de/fileadmin/w00cfj/dis/papers/btrees-are-back.pdf),
SIGMOD 2025, studies optimized variable-sized records and adaptive node layouts.
Sun et al.,
[An Evaluation of B-tree Compression Techniques](https://link.springer.com/article/10.1007/s00778-025-00950-8),
published November 2025 in VLDB Journal volume 35 (2026), compares seven
compression techniques. Prefix/suffix compression performs well in their tests;
more aggressive compression can lose time to decoding and maintenance.

**Our decision:** implement an ordered page/slot index with variable/composite
keys, stable handles and prefix compression as the production baseline.
Compare uncompressed nodes first, then optimize node search/layout separately.
Index entries must carry enough version information for snapshot visibility.
Uniqueness, rollback and replay must use the same logical comparator as SQL.

### R5. SIMD-gapped and learned indexes are experiments, not defaults

**Sources:** Tsitsigkos et al.,
[BS-tree: A gapped data-parallel B-tree](https://arxiv.org/abs/2505.01180),
revision November 2025, announces ICDE 2026 acceptance; SIMD-sized gapped nodes
and frame-of-reference compression target search/update costs.
Bellomo et al.,
[A Comparative Study of Compressed, Learned, and Traditional Indexing Methods for Integer Data](https://drops.dagstuhl.de/storage/00lipics/lipics-vol338-sea2025/html/LIPIcs.SEA.2025.5/LIPIcs.SEA.2025.5.html),
SEA 2025, finds strong SIMD B-tree lookup results on static integer datasets.
Cheng et al.,
[DynaMind](https://doi.org/10.1016/j.knosys.2026.116357),
August 2026, proposes update-triggered incremental learning and unlearning for
dynamic learned indexes. Its publisher states data are available on request.

**Our decision:** compare BS-tree-style integer nodes as a specialized format.
Evaluate learned models only for stable, ordered numeric segments after a
correct ordered index exists. Include model building/retraining, inserts,
deletes, distribution drift, duplicate keys, misses and worst-case fallback
search. A model predicts a position; it cannot establish key equality or MVCC
visibility. Static lookup rankings do not establish mutable SQL performance.

### R6. Succinct indexes can save metadata, with platform and semantic caveats

**Source:** Faghfoor Maghrebi and Dayan,
[Sphinx: A Succinct Perfect Hash Index for x86](https://doi.org/10.14778/3749646.3749703),
PVLDB 2025;
[author manuscript](https://nivdayan.github.io/sphinx.pdf).
The design uses rank/select-friendly succinct tries and auxiliary metadata for
log-structured hash indexing; its x86 implementation uses bit-manipulation
instructions. A few bits of hash-disambiguation metadata per key is not the
total size of keys, rows, locations and versions. The manuscript has placeholder
venue metadata; use the proceedings DOI for publication details.

**Our decision:** investigate for sealed segment point indexes after compact
storage. Explicitly handle absent keys with exact verification. Budget all
auxiliary state and rebuild overlap. Require AArch64 results before considering
it a portable default; an x86 `PDEP` optimization has no automatic native-SB0
performance equivalence.

### R7. Compressed execution and small decode batches

**Source:** Afroozeh and Boncz,
[The FastLanes File Format](https://ir.cwi.nl/pub/35881/35881.pdf),
PVLDB 2025. Lightweight cascaded encodings, multi-column relationships and
partial decompression support cache-sized vector access. The paper's random
access experiment includes fetching the first row through different format APIs;
its large ratios must not be read as arbitrary mutable-row lookup ratios.

**Our decision:** use native typed columns, constant/dictionary vectors and
bounded decode scratch for sealed segments. Start with raw, constant and
frame-of-reference integer layouts. Add bit packing, run-length or string
compression only when end-to-end scan savings exceed decode/maintenance cost.
Keep an uncompressed update delta; compression cannot mutate snapshot-visible
bytes in place. Never import the C++ implementation as a runtime dependency.

### R8. Avoid moving almost-empty batches

**Source:** Qiao and Zhang,
[Data Chunk Compaction in Vectorized Execution](https://duckdb.org/library/data-chunk-compaction/),
SIGMOD 2025. The work studies when sparse intermediate batches should be
compacted and proposes adaptive thresholds and logical compaction for joins.

**Our decision:** initially preserve selection vectors through filters and
materialize only referenced columns. Benchmark occupancy thresholds before
adding an adaptive policy. Copying every survivor into a fresh full Cell row
would undo much of the intended gain. Bound selection and permutation buffers
inside the operator lease.

### R9. Factorized joins and aggregates

**Source:** Groß, ten Wolde and Boncz,
[Adaptive Factorization Using Linear-Chained Hash Tables](https://www.vldb.org/cidrdb/2025/adaptive-factorization-using-linear-chained-hash-tables.html),
CIDR 2025. The authors use collision-free chains and build-time statistics to
choose factorization and worst-case-optimal joins at runtime, including cheaper
heuristics as alternatives to learned decisions. Assessment here is based on
the official proceedings abstract; the PDF fetch timed out.

**Our decision:** later optimize aggregate-over-join shapes without enumerating
unnecessary duplicate combinations. First implement ordinary hash/merge joins
and physical-property tracking. Preserve SQL bag multiplicities, outer joins,
NULLs and overflow behavior. A multiplication of aggregate partials is only
valid for a proven query shape; never infer that rule from syntactic similarity.

### R10. Share memory budgets without silently changing in-memory semantics

**Source:** Kuiper et al.,
[Saving Private Hash Join](https://ir.cwi.nl/pub/35878/),
PVLDB 2025. The design combines a unified temporary/persistent buffer pool,
runtime compression and concurrent-operator memory management to reduce
larger-than-memory performance cliffs.

**Our decision:** borrow explicit operator budget arbitration and temporary
compression. RAMSQL's requested fully in-memory execution does not authorize
silent external spill. If a join cannot fit, choose a proven bounded-memory
alternative, wait before execution, or return a clear resource error. Disk
spilling would require a separately specified opt-in product mode.

### R11. Mathematical sorting advances do not remove the need for access paths

**Sources:** Xu and Chick,
[Near-Optimal In-Place Sorting](https://arxiv.org/abs/2607.27040),
July 2026, revised 11 October 2026, is a preprint claiming comparison-optimal
randomized sorting with linear moves; both presented algorithms are unstable.
Gila, Goodrich and Sridhar,
[How to Sort in a Refrigerator](https://arxiv.org/abs/2603.05676),
March 2026, reports LATIN 2026 acceptance and strictly constant-auxiliary-space,
run-entropy-sensitive comparison sorting. These are operation/space results,
not measured RAMSQL CPU speedups.

**Our decision:** retain the current bounded top-K heap for eligible small K;
prefer an ordered scan when it avoids sorting. Benchmark handle-only radix,
merge and in-place methods for full sorts. Integer radix sorts require correct
signed/NULL encodings; text/collation/numeric keys need exact comparators.
Preserve required tie behavior through an explicit ordinal or a stable method.
Allocator-free already permits caller-provided scratch; constant scratch is
not inherently faster than a cache-friendly buffered sort.

### R12. Durable commits: evolve beyond one blocking flush per statement

**Sources:** Nguyen et al.,
[Moving on From Group Commit](https://www.cs.cit.tum.de/fileadmin/w00cfj/dis/papers/latency.pdf),
SIGMOD 2025; and the extended
[Autonomous Commit and Commit Causality Clocks](https://www.cs.cit.tum.de/fileadmin/w00cfj/dis/papers/commitnc3.pdf),
TODS 2026, DOI `10.1145/3859374`. The work parallelizes small durable log writes
and dependency checking on modern NVMe. C3 combines inexpensive ordering during
execution with precise dependency tracking at commit. Its evaluated hardware
and decentralized logging assumptions matter.

**Our decision:** first implement a bounded asynchronous commit queue with one
ordered WAL and group flush, preserving flush-before-publication. Compare
latency at low load as well as saturated throughput. Autonomous per-worker logs
and causality clocks are a later experiment after worker ownership, dependency
recovery and durable I/O capabilities exist. Do not transplant that architecture
into today's single synchronous reactor or promise its NVMe results on all OSes.

### R13. Checkpoint write amplification matters even with RAM-resident data

**Source:** Lee, Ziegler and Leis,
[How to Write to SSDs](https://arxiv.org/abs/2603.09927),
2026 extended paper accepted to PVLDB. It evaluates out-of-place page writing
and SSD-aware optimizations, including flash write amplification under sustained
load. Its page-store workloads are not RAMSQL WAL benchmarks.

**Our decision:** favor immutable checkpoint generations with a durable manifest,
bounded incremental copying and explicit WAL anchors. Measure application bytes,
host bytes and available device-write counters separately. Do not add raw-device,
ZNS or FDP support before ordinary file/directory durability is correct on each
hosted OS. RAM-resident working data still needs bounded recovery and checkpoint
write costs.

### R14. Query specialization must remain an optional, verified optimization

**Source:** Balasingam et al.,
[JetStream: Generating Verified Query Accelerators for Existing Database Systems](https://arxiv.org/abs/2610.20527),
14 October 2026 preprint. It synthesizes accelerators for parameterized templates
over an existing DBMS, distinguishing differential-tested from machine-checked
variants and maintaining auxiliary state under updates. Its reported speedups
are for that specialization workload, not arbitrary ad-hoc SQL.

**Our decision:** later use typed plan specialization or Sig-generated kernels
with a generic executor fallback, schema/version guards and transactional
invalidation. Keep any generation/proof tool offline; no LLM, JIT heap or foreign
runtime enters the server. A proof only covers its trusted specification and
assumptions, so SQL translation, native code generation and crash integration
still require validation. Do not specialize the six benchmark strings to inflate
the comparison.

## 5. Proposed architecture and layer boundaries

Names in this section are proposed interfaces, not existing APIs. Integrate each
primitive into the live executor before describing its gap as closed.

### Cost models used to reject the wrong optimization

These are our engineering models, not new mathematical results or guarantees
from the cited papers. Let `N` be input rows, `G` groups, `K` requested top rows,
and `V` index entries actually visited after seek, including invisible and
residual-filtered entries.

| Strategy | Work model | Additional state, excluding output | When it helps |
| --- | --- | --- | --- |
| Current grouping sort | O(N log N) comparisons plus expressions | O(N * key width) | General baseline; poor fit for small G and large N. |
| Streaming hash group | Expected O(N), subject to hash/equality costs | O(G * key/state width) | Unordered input with admitted distinct-group budget. |
| Ordered group | O(N) plus expressions | One group, plus input batch | Input order is proven compatible with grouping equality. |
| Scan plus top-K heap | O(N log K), for K > 1 | O(K) handles plus keys if cached | Small K without a beneficial ordered index. |
| Ordered index LIMIT | O(log N + V), plus exact checks | Cursor/batch | Useful bounds/order and few visited entries. |
| Full integer radix sort | O(passes * N) | Depends on stable/in-place variant | Encodable keys; construction and scratch costs included. |

Do not simplify index cost to `log N + K`: a sparse predicate or old snapshot can
require visiting far more than K entries. Likewise, hash aggregation is not
constant-memory when G grows with N. Long variable keys add hashing/comparison
bytes that a row-count-only complexity model omits.

For a scan, a first lower-bound estimate is
`time >= max(bytes_transferred / sustainable_bandwidth, cycles / effective_CPU_rate)`.
Use measured bandwidth/counters, not advertised DRAM peak. More cores stop helping
when bandwidth or shared coordination saturates. Amdahl's law also matters: if an
operator accounts for fraction `f` of end-to-end time, speeding it up by `s` gives
at most `1 / ((1-f) + f/s)` overall under that simple fixed-work model. This is why
a huge native lookup improvement need not create the same wire-level gain.

For an initial serial compression model, compare
`raw_bytes / bandwidth` with
`compressed_bytes / bandwidth + decode_cycles / CPU_rate`. Real pipelines overlap
some of this work and compression changes cache behavior, so measure the complete
operator afterward. Include encoding, updates and peak old/new segment overlap.
A smaller file alone is not evidence of a faster in-memory database.

For a single non-overlapped ordered flush queue, a rough service ceiling is
`min(log_bandwidth / bytes_per_tx, batch_tx / flush_time, CPU_tx_rate)`.
Queueing latency can rise sharply near that ceiling. Smaller batches lower
waiting at low load; larger batches amortize barriers under load. The autonomous
commit research changes parallelism and dependency costs, not the obligation to
wait for durable completion.

| Component | Layer and proposed responsibility | Ownership invariant |
| --- | --- | --- |
| Typed page/slot and value views | ZPM core SQL/storage | Stable page/slot/generation handles; no borrowed pointer survives release. |
| Group ticket table and aggregate state | ZPM core SQL | Caller buffers; exact SQL equality; initialized before publication. |
| Batch scan, selection and resumable operators | ZPM core SQL | Explicit snapshot and lease retained across yield. |
| Ordered/composite indexes and physical properties | ZPM core SQL | Index visibility and table visibility agree at every snapshot. |
| Transaction/WAL queue state machine | ZPM db/store | No acknowledged/public commit precedes its durable dependency frontier. |
| Mapping, completion I/O, clock and poll adapters | ZPM platform | OS capabilities returned explicitly; no simulated successful flush. |
| Admission, defaults, SQLSTATE, metrics, release tests | RAMSQL | Product policy consumes reusable mechanisms and reports limits. |
| Missing general compiler/vector primitives | Sig, only if actually needed | No SQL/product behavior in compiler; general tests on both ISAs. |

### 5.1 Streaming operator contract

An operator owns state across calls and returns `batch`, `yield`, `blocked`,
`done` or `error`. Input/output buffers and a work allowance come from the caller.
Cancellation and CPU budget are checked inside expensive loops. Yield is distinct
from cancellation: it preserves progress; cancellation releases private state
and applies existing transaction-failure semantics.

Initially stream one base-table scan through predicate evaluation and scalar or
grouped aggregates. Do not first route it through `sourceRows`/`join` and then
call it streaming. A base-table cursor walks row slots, resolves visible
versions at the retained statement snapshot, and produces bounded batches.
Joins, CTE materialization and correlated subqueries need their own later paths.

Internal atomic results and wire streaming are separate contracts. The existing
public API promises that a failed SELECT leaves the previous result untouched.
Preserve that API. A future wire cursor may send rows and subsequently report a
query error, but must not send successful CommandComplete for a failed query;
its snapshot, cancellation, portal suspension and transaction behavior need
explicit PostgreSQL differential tests before enabling it.

### 5.2 First implementation: bounded streaming aggregation

Implement in `zpm/src/core/sql/exec.sig` with reusable table mechanics in core.

1. Prove fast-path eligibility from the bound plan: supported source shape,
   aggregate kinds, expressions, filters and grouping equality. Unsupported
   DISTINCT aggregates, grouping forms or subqueries retain their correct path
   until their separate algorithms exist. Do not silently change semantics.
2. Scalar aggregates require one initialized accumulator per aggregate. Consume
   visible rows directly and reuse existing `accumulate`/`finishAggregate` rules,
   including FILTER, NULL, integer overflow and float/numeric behavior.
3. For groups, hash the canonical typed key, probe fingerprints and confirm full
   equality. Assign one stable ticket per new group. Store key and typed state
   once per ticket. Do not create one Cell or ordinal per input row.
4. Finish aggregates, evaluate HAVING and project at group granularity. Sort only
   output group handles if required. Early LIMIT must not omit rows contributing
   to an existing group. Without ORDER BY, do not promise hash-bucket order.
5. Keep all mutable work private to the statement. Before each admission or
   state transition, check its remaining budget; no result publication follows
   a capacity, evaluation or cancellation failure.

Budget, for `G` admitted groups, `K` owned key bytes/group, `A` aggregate state
bytes/group, `H` bytes/hash slot, load threshold `alpha`, and batch scratch `B`:

```text
M_group ~= ceil(G / alpha) * H + G * (K + A + group_metadata) + B
```

Include alignment, variable key bytes, output and any partial-worker states in
the actual checked bound. It is O(groups), not universally constant: when every
row is a group, the result/state necessarily grows. Exact COUNT(DISTINCT) also
needs memory proportional to admitted distinct values or a separately proven
alternative; approximate sketches must never silently implement exact SQL.

Equality/hashing must agree for NULL grouping, signed zero, NaNs, numeric values
with differing scales, text/collation rules and every supported key type. Never
hash uninitialized union padding. Introduce canonical hashing only alongside
cross-type/equality tests, not by hashing raw Cell bytes.

Possible subsequent paths: direct-address aggregation for proven small integer
domains; ordered aggregation using already ordered input with one active group;
radix partitioning under a preapproved group budget. Parallel merging must
preserve aggregate error/type rules; floating-point regrouping can change
rounding and overflow, so define and differentially test that behavior.

### 5.3 Ordered reads and physical planning

The planner must describe predicate bounds, required ordering, NULL placement,
available key prefixes, projected columns, estimated cardinality and operator
memory. Plan choice needs measured constants and a conservative fallback.

For `WHERE category = ? ORDER BY id LIMIT 16`, compare:

- `(category, id)` index seek and bounded visible-row walk;
- ordered `id` walk with residual category filtering;
- full scan with bounded top-K.

The second path can win if matches occur early, or lose badly when matches are
rare/absent. The first adds maintenance/memory cost. Report index build time and
update amplification. Measure all-visible and old-snapshot cases; LIMIT applies
after visibility and residual predicates. A covering index cannot bypass
visibility checks unless its stored payload/version contract proves that safe.

Add EXPLAIN/operator counters for rows examined, visibility visits, predicate
evaluations, key comparisons, output rows and leased bytes. Verify operators do
less work rather than guessing from elapsed time alone. Correct statistics are
advisory: stale estimates may slow a plan but must not make its answer incorrect.

### 5.4 Compact storage, typed batches and variable-length values

Use a stable row directory pointing to version metadata and typed payloads.
Store fixed-width numeric columns densely; store NULL bits separately. Variable
values use validated offsets/lengths into owned payload pages, with an explicit
large-value chain representation when needed. Remove the 128-byte default value
ceiling through representation changes rather than multiplying every Cell's
inline Text array. Schema/catalog data likewise needs owned variable-width state.

Compare a compact row format and a PAX/column segment plus mutable delta using
the same workload. A universal column-only format may hurt point updates;
duplicated row+column storage consumes memory and adds maintenance. Initially
choose one authoritative version representation and derive bounded scan batches.

Batch representations may be raw, constant or dictionary-backed, with selection
vectors and visibility masks. Decode only referenced columns. Choose batch size
by experiment (for example 256, 1,024, 2,048 rows), not by treating a paper's
batch size as a hardware constant. Measure cycles, bytes touched and cache misses.

Compaction writes a new private segment and atomically publishes its directory
mapping. Old snapshots retain old pages; aborted writes never become reachable.
Budget peak overlap of old data, new data, indexes and concurrent changes.
Generation checks prevent stale-handle/ABA reuse. This representation change
requires WAL format/version migration and backup validation; it is not a local
union-size tweak.

### 5.5 Scheduling and multicore ownership

First add resumable single-worker operators so a long scan yields to point reads,
network backpressure and control work. Use fixed task slots and caller-owned
queues; do not create a thread or stack per connection. Separate dormant session
metadata from leased packet, SQL, plan and result buffers to raise connection
capacity without allocating maximum query state for every idle session.

Then introduce worker-owned scratch and explicit transfer/completion messages.
Define who may mutate each row version, index page, transaction slot and queue.
Avoid making today's shared workspace concurrently accessible by adding a lock
around only one entry point. Test acquire/release ordering on AArch64, false
sharing and worker loss. Epoch/horizon reclamation needs bounded cleanup work
and observable lag; an idle or disconnected reader cannot silently pin storage
forever without an enforceable policy.

NUMA placement and CPU affinity are later measured options. First-touch owned
pages, compare local/remote access and cap parallelism where bandwidth saturates.
GPU/PIM/FPGA execution is deferred: transfer, visibility, synchronization and
platform support costs need a separate justified workload.

### 5.6 Durable commit and checkpoint state machines

For the first ordered-WAL queue:

```text
private validated changes -> reserved commit slot -> framed append
  -> durable flush frontier -> infallible ordered publication -> acknowledgement
```

Reserve publication resources before crossing the durable boundary. Concurrent
queued transactions retain required ownership/dependencies until publication;
later validation cannot assume an earlier pending writer has aborted. After a
durable commit record exists, disconnect/cancellation cannot pretend that commit
was rolled back. Flush failures poison the affected writer path and require
defined recovery; short writes cannot advance the durable frontier.

Batch by bounded bytes/count and latency policy. Measure 1, 4, 16 and 64
committers, read/write mixtures, idle-to-active latency and p99/p99.9; throughput
alone can hide long waits. Do not disable fsync or acknowledge pre-flush to beat
PostgreSQL. For future decentralized logs, a transaction may acknowledge only
when its own log and all recovery-required dependencies are durable; the C3
paper motivates experiments, not skipping that proof.

Checkpoint at a defined commit sequence, retain necessary versions while copying
bounded chunks, checksum output, flush data and publish a durable generation
manifest. Only retire WAL after the new checkpoint and recovery anchors are
durable. Directory/rename behavior differs across hosted OSes and native SB0.
[PostgreSQL's WAL configuration documentation](https://www.postgresql.org/docs/18/wal-configuration.html)
explains its checkpoint/replay relationship; RAMSQL needs an independently
specified format and publication protocol rather than assuming identical files.

## 6. Implementation sequence and closure evidence

All rows below remain **open**. Research selection is not implementation status.
Each step needs a reviewable patch and evidence before its successor becomes a
default. Storage and durable-lifecycle investigations may proceed as separate
patch series, but do not mix unqualified format changes into a performance patch.

| Step | Deliverable | Acceptance evidence |
| --- | --- | --- |
| D0 | Operator and memory counters; reproducible baseline | Account live/reserved/retained/scratch bytes; record plan path and binary/source hashes; preserve raw existing pack. |
| D1 | Streaming scalar and bounded hash aggregates | Million-row/64-group query succeeds with scratch independent of input cardinality; all supported results/error behavior match the reference. |
| D2 | Ordered/composite index and planner path | Filtered LIMIT uses bounded visible candidates when applicable; maintenance, rollback, uniqueness and recovery pass; absent/skewed cases measured. |
| D3 | Compact typed/variable storage integrated into SQL | Values above 128 bytes and wider schemas round-trip; memory slope/peak improve on identical data; old-version visibility and migrations pass. |
| D4 | Resumable batches, result/portal streaming, session leases | Long scans no longer monopolize reactor; slow consumers obey byte budgets; cancellation and internal result atomicity remain correct. |
| D5 | Bounded durable commit queue and checkpoints | Every acknowledged transaction survives injected crashes; failed writes/syncs fail closed; recovery/backup/WAL retirement verified. |
| D6 | Multicore scan/join/group execution | Measured scaling under skew/cardinality variation; no ownership races on x86_64/AArch64; deterministic resource cleanup. |
| D7 | Adaptive compression/index/hash variants | Each selected variant beats its simpler baseline for its declared domain including maintenance and memory cost. |
| D8 | Specialization/factorization experiments | Guarded semantic applicability, transactional invalidation and broad workload wins; ad-hoc SQL remains correct. |

### D1 review checklist

- Empty input, no GROUP BY, all NULL, NULL keys, duplicate keys, multiple keys,
  text/numeric keys, FILTER, HAVING, ORDER BY, OFFSET/LIMIT, parameters and
  correlated expressions where supported.
- COUNT(*), COUNT(expr), SUM/AVG/MIN/MAX/boolean aggregates; exact result types,
  numeric scale, float specials and overflow SQLSTATE. DISTINCT either has a
  correct explicitly budgeted path or follows the existing implementation.
- Compare 64 groups at 512, 100,000 and 1,000,000 rows using the same group budget;
  assert no input-sized key or handle array on the eligible path. Test all-unique
  groups, deliberate collisions, skew and group-budget exhaustion separately.
- Exhaust before/after group insertion, during expression evaluation, HAVING and
  result projection. Poison scratch first. Previous public Result must survive
  every error. No versions, locks, leases or session state leak.
- Cancel/resume at every cursor boundary, including a retained snapshot while
  another connection updates/deletes/reuses slots. Do not hold a dangling pointer
  into mutable storage across a yield.
- Run existing SQL/store suites and live PostgreSQL differential tests. Benchmark
  only after correctness; store new results alongside the old report, never
  overwrite failed baseline samples with successes from a different binary.

## 7. Benchmark and experimental discipline

### Workload coverage

Keep the current explicit comparison as a regression set, then add:

| Dimension | Cases |
| --- | --- |
| Scale | 512, 100K, 1M, 10M rows when physical memory permits; live payload and total budgets reported. |
| Lookup locality | One hot key, uniform random hits, misses, Zipf skew; integer and variable/composite keys. |
| Aggregation | 1, 64, sqrt(N), N groups; uniform/heavy hitter; NULLs, exact distinct, numeric/float/string types. |
| Ordering | K=1/16/1K/full, ties, ascending/descending/mixed, ordered/reversed/random input, selective and absent predicates. |
| Updates | Inserts/deletes/key changes, small/large transactions, rollback, churn, old snapshots, compaction/checkpoint overlap. |
| Joins | 1:1, 1:many, many:many, skew, missing keys, NULL/outer joins, multiway joins; output cardinality reported. |
| Clients | 1/4/16/64+ as implemented; connection churn, pooled idle sessions, slow consumers, scan plus point-read fairness. |
| Durability | Volatile, synchronous durable, group commit, crash/recovery; never combine tiers in one ranking. |
| Platforms | Real hosted x86_64/AArch64 runs, plus actual SB0 capabilities; emulated ABI fixtures labeled separately. |

Add standard analytical and transactional workloads only for implemented SQL
semantics. Record every unsupported query as a failure/missing capability rather
than removing it from the denominator. TPC-like experiments are not certified
TPC results. Parameterize queries and datasets; avoid benchmark-string shortcuts.

### What to record

Retain exact engine/toolchain/dependency versions, binary/source hashes, SQL,
schema/index definitions, settings, seeds, dataset/checksums, load/build times,
raw samples, environment and workload correctness digests. Check the live IRDB
version feed again for a future comparison, then pin package hash and adapter.
Use PostgreSQL 18 for the main compatibility target and retain 16 as a historical
baseline; record exact builds. Other engines need their own compatible equivalent
query and index choices, not a slower convenience API chosen to favor RAMSQL.

Alternate engine order, isolate builds and other CPU load, document CPU power/
thermal state, and separate warm/cold experiments. Five trials suffice for the
current exploratory report, not a reliable tail-latency or small-difference
claim. Increase independent runs until uncertainty is reported meaningfully.
Use an open-loop load generator for latency-under-load experiments, recording
offered versus achieved rate so coordinated omission does not hide queueing.

Report median throughput plus p50/p95/p99/p99.9 latency where sample sizes support
those quantiles. Separate parse/bind, scan, expression, hash/sort, result encoding,
network and durable flush time. Hardware counters should explain cycles/row,
instructions, cache/TLB misses, branch misses and bandwidth where supported.
Do not infer a memory-bandwidth bottleneck from a slow scan without measuring.

Every optimization compares total end-to-end work as well as its kernel:
construction/training, maintenance, peak memory, cancellation, mixed reads/writes,
and recovery. Report regressions and unsupported shapes. New algorithms enter
the planner only for a measured applicability region; a single geomean is not
enough to claim all-benchmark superiority.

## 8. Decisions deliberately left experimental

- **Elastic/funnel hashing:** mathematically compelling; mutable deletion/MVCC
  and adversarial worst-case latency remain separate problems.
- **Learned indexes:** evaluate after an ordered baseline, with drift and rebuild
  costs included; no external training/runtime dependency.
- **Autonomous logging/C3:** promising for a future multicore NVMe path; requires
  a recovery dependency model and real durable completion adapters first.
- **Worst-case-optimal/factorized joins:** useful for suitable shapes; not a
  replacement for ordinary joins or their SQL multiplicity rules.
- **Query synthesis/proofs:** offline, guarded experiments; benchmark wins cannot
  substitute for general SQL behavior or a trusted-specification audit.
- **GPU/PIM/distributed execution:** no demonstrated fit to the current bottleneck
  or complete native OS matrix; revisit with a specific measured workload.
- **Approximate sketches:** useful for estimates and explicitly approximate SQL
  features; never silently replace exact query results.

The immediate engineering commitment is D0–D1: make the existing million-row
aggregation execute with a declared group-sized budget and establish why its
performance changes. That provides a trustworthy foundation for the more
advanced experiments above.
