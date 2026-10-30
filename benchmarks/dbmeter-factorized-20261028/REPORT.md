# Factorized execution qualification

These are development candidates, not a production or universal-leadership
qualification. The existing dbMeter advantage gate remains unchanged and its PR
must remain unmerged until that gate passes.

## Method

Each phase contains six fresh full-data server campaigns, ordered on/off,
off/on, on/off. Each campaign loads the original five-million-sale dataset and
runs all seven original correctness checks, retaining failures. The three
supported checks must pass before timing the original two analytical queries.
Every timed case uses a fresh JVM, eight JDBC clients/native read workers and
the original 5s ramp, 15s warmup and 60s measurement. Default text capacity is
128. No SQL, expected results, data sizes or durations are changed.

Within each phase, both binaries use identical immutable SQL/server sources;
only the named compile-time execution switch differs. The first three phases
toggle `factorized_aggregates`, the fourth toggles `block_presence_filters`, and
the last two toggle `sparse_factor_predicates`. Build provenance
records both source revisions, archive checksum, exact configuration patch,
command and binary SHA-256. Query results, measurement windows, histograms and
full/supported JUnit checks are retained for every campaign. There are no local
builds or profilers initiated by this benchmark task during throughput measurement.
The final two phases inventory competing Windows process images; their records
show overlaps with compiler/database images, as detailed below. Earlier phases
have no equivalent inventory and cannot establish host isolation.

The host is Windows 11, Intel i9-14900HX, 24 physical cores/32 logical CPUs,
32 GB RAM. No affinity or power-plan settings were changed. Earlier unchanged
controls show material host variation; interleaving reduces drift exposure but
does not establish a controlled dedicated-hardware laboratory.

`machine_after_load` in each campaign report is a separate invocation of the
binary's machine-capacity command, not live worker telemetry. The recorded server
arguments establish eight workers; that standalone command's default-worker
fields must not be interpreted as the running server's configuration.

## Aggregate before dimension joins

ZPM source `796719704a952f11a35a10d98cfbba2273f64560` folds safe selected fact
measures by the first integral join key, probes each active key's visible
dimension chain once, then merges final groups. It borrows initialized query
scratch and preserves canonical execution for unsupported predicates, domains,
arithmetic, snapshots, joins or budgets. The actual SQL contract passes 1,824
assertions in Windows/Linux ReleaseSafe and legacy Linux Debug. Native full-SQL
CI passes on Windows, Linux and macOS.

| Case | On QPS median (range) | Off QPS median (range) | Paired on/off ratios |
| --- | ---: | ---: | --- |
| Nested customer totals, untargeted in this phase | 127.52 (126.95–182.68) | 165.48 (134.07–175.12) | 1.043, 0.767, 0.951 |
| Store/category mix | 1285.14 (1236.37–1592.59) | 1169.00 (1139.95–1412.76) | 1.127, 1.085, 1.099 |

The store/category case improves in all three pairs, by about 8–13%. Untargeted
customer-total throughput varies materially and its enabled median is worse.
This phase does not establish non-regression or a significant all-case advantage.
It remains a draft candidate, not an accepted default or release claim.

## Typed producer/consumer aggregation

ZPM source `b8e7ee8ccc7db8f19ec61ae47c25ad097c379e8f` additionally retains typed
COUNT/SUM producer state through eligible single-consumer CTE/derived queries.
It applies safe producer-output predicates, probes visible dimensions and
finalizes only the final groups. Output projection, AVG/ROUND, HAVING and sorting
retain the ordinary SQL implementation. It does not match query names or text.

The actual SQL contract passes 1,141 assertions on Windows/Linux ReleaseSafe and
legacy Linux Debug, comparing enabled execution with the disabled executor and
an independent scalar model. It covers signed thresholds, NULL/missing keys,
reordered outputs, derived tables, shared CTE fallback, computed/throwing
expressions, private writes, retained fact/dimension snapshots, failed flush,
copy/replay, poisoned scratch, constrained reference budgets, work limits,
cancellation, nullable/wide producer fallback and empty inputs.

| Case | On QPS median (range) | Off QPS median (range) | Paired on/off ratios |
| --- | ---: | ---: | --- |
| Nested customer totals | 496.04 (117.27–588.80) | 138.75 (138.07–159.19) | 4.244, 3.116, 0.849 |
| Store/category mix | 248.87 (230.07–1226.89) | 1327.46 (1010.88–1351.55) | 0.924, 0.246, 0.170 |

The first two customer-total pairs improve substantially, but the third regresses.
Store/category throughput regresses in every pair, severely in two. Its enabled
p99 reaches 58.88 ms versus disabled 11.39 ms in pair two; the third customer-total
p99 is 117.76 ms versus 91.14 ms. All timed operations still succeed and all three
supported checks pass. A favorable median for one query does not excuse these
losses. The combined candidate fails qualification and remains draft.

Native full-SQL CI passes on Windows, Linux and macOS. Broad legacy CI reports
244 successful steps, four known unrelated SABR/TLS/TTS failures and 22 skips.
The initial RAMSQL integration had 32 successful active checks and two policy
skips, including native SB0S/SB0K, hosted systems and PostgreSQL 16/18
memory/durable-SCRAM. It remained draft because performance qualification failed.
The later block-filter dependency is being qualified separately. No cause is assigned
to the timing change: power/topology snapshots do not identify actual thread
placement, running frequencies or a causal hardware event.

A separate experiment restricts both frozen server binaries to the same mask
`0xffff`: eight physical performance cores/sixteen logical processors identified
by the recorded Windows CPU-set topology. The JVM remains unrestricted. This
changes only the server process's affinity, not global settings or production
defaults. All six campaigns are complete, retained under `pcores/`:

| Case | On QPS median (range) | Off QPS median (range) | Paired on/off ratios |
| --- | ---: | ---: | --- |
| Nested customer totals | 657.31 (139.56–703.41) | 151.51 (145.47–160.76) | 4.376, 4.338, 0.959 |
| Store/category mix | 959.83 (268.29–1586.75) | 1287.26 (1171.55–1369.85) | 1.158, 0.746, 0.229 |

The slowdown also occurs with a fixed P-core mask. Affinity alone is therefore
insufficient to qualify the candidate or explain the losses. None of these
trials replaces the unrestricted results.

These campaigns excluded compilers, reference database services and code
profilers, but repository edits/checkouts and ordinary desktop activity continued
on the same host. Those activities are a potential confound; no causal
attribution is claimed. The next throughput campaign must also suspend local
repository work during measurement. Its results must retain these failed
campaigns rather than silently superseding them.

Core restriction is a diagnostic, not the production scheduling design. Intel's
[hybrid-architecture guidance](https://www.intel.com/content/www/us/en/developer/articles/guide/12th-gen-intel-core-processor-gamedev-guide.html)
describes OS scheduling hints/QoS, spillover onto other cores and the risks of
manual placement. Its
[2026 hybrid profiling documentation](https://www.intel.com/content/www/us/en/docs/vtune-profiler/user-guide/2026-0/hybrid-cpu-analysis.html)
recommends attribution by core type. Actual per-core execution must be observed
in a separate diagnostic before assigning the earlier slowdown to scheduling.
An eventual global task pool should adapt chunk sizes to observed worker rates
and granted memory, allowing useful work on all core classes without creating
a full-machine pool per client. No such scheduler is implemented by this PR.

## Remaining blockers and the next access-path change

### Subsequent quiet-host block-presence campaign

`presence/` records six further fresh full-data campaigns. Both binaries use ZPM
`efc9e232d7dbb2313b90930a82740b3779d691ee` with factorized aggregates enabled;
only block-presence filtering differs. No local builds, profilers, checkouts,
repository edits or source work run during measurement. Ordinary desktop
background activity remains uncontrolled. The JVM, queries, datasets, durations,
eight clients/workers and all correctness checks remain unchanged.

The filter maintains 256 conservative presence bits per column/64-row block in
caller-owned storage. Absent bits permit rejection; present bits always use exact
value/null/live comparisons. Residue collisions and retained old/deleted bits can
only cause extra comparisons. New blocks initialize before use; durable
publication, replay, copy across capacities and compaction maintain the invariant.

| Case | On QPS median (range) | Off QPS median (range) | Paired on/off ratios |
| --- | ---: | ---: | --- |
| Nested customer totals, untargeted by the filter | 747.81 (737.33–753.37) | 715.22 (108.27–727.26) | 1.036, 6.907, 1.031 |
| Store/category mix | 1673.94 (1639.16–1677.81) | 380.59 (341.25–902.67) | 4.409, 1.854, 4.803 |

Every timed operation succeeds; the three original supported checks pass, and the
four unsupported original checks remain retained. Enabled p99 is 13.44–13.95 ms
for customer totals and 6.08–6.46 ms for store/category. Its matching control p99
ranges are 14.98–128.00 and 45.57–58.88 ms. These are measurements of this
candidate and environment, not a claim about other workloads or reference engines.

The enabled runs are consistent and win every pair. Attribution still has a
material limitation: the untargeted control also varies sharply. The second
control takes 58.34s/38.88 CPU-seconds to load, versus 11.66–12.02s/8.08–8.84
CPU-seconds in its other controls, before timed SELECTs begin. The enabled load
trials take 10.84–12.86s/8.08–9.08 CPU-seconds. This prevents assigning the entire
QPS ratio or a loading speedup to the SQL filter. The earlier slowdowns remain
unexplained; neither affinity nor pausing repository work alone eliminates them.

Declared backing grows by exactly **20,206,340 bytes** with identical runtime
budgets; observed process-private commit increases by about 20.25 MB. See
`presence-backing-comparison.json`. The declared difference includes block rounding
for all table reservations and alignment. This memory is runtime backing, not an
input-sized executable array. No sustained update/churn benefit is established.

The new actual SQL contract passes **24,441 assertions** on Windows/Linux
ReleaseSafe and legacy Linux Debug, plus the existing 1,824-assertion factor-join
suite with metadata enabled. Native full-SQL CI passes on Windows, Linux and
macOS; broad legacy CI has 246 successful steps, the same four unrelated
SABR/TLS/TTS failures and 22 skips. The reusable code was merged through
[ZPM PR 65](https://github.com/SB0LTD/zpm/pull/65), preserving disabled library
defaults. RAMSQL's updated opt-in passed all 32 active integration/release checks
and was merged through PR 29 as `22c55a4268c506df83e31b88879c686d94c1d5b1`.

`profiles/` retains separate 10ms wall-time instruction-pointer diagnostics from
the two frozen binaries on the same original full-data query. These pause/resume
owned threads and include ramp/warmup/runtime waits; they are not PMU cycle
attribution or throughput comparisons. Both reproduce a fast factorized execution
path rather than the slow controls. They show comparison/factor-fold instructions
prominently, but cannot explain the intermittent slowdown or validate an all-case
speedup. Profiled throughput is not substituted for any unprofiled trial above.

## Sparse residual predicates: experimental, not accepted

ZPM source `00b4bb2737a870e960477faaa13a0c8d8b3e168e` evaluates later,
nonthrowing fact predicates only at surviving row positions when a 64-row mask
has at most eight candidates. Leading and dense masks retain vector execution.
It uses existing caller scratch, adds no persistent index, and preserves charged
work and cancellation behavior. Both binaries enable the already merged
factorization and block-presence paths; only the sparse switch differs.

The dedicated scalar/vector/model contract executes 1,537 assertions and the
snapshot, replay, fallback and failure-atomicity contract executes 1,838, each on
Windows/Linux ReleaseSafe and legacy Linux Debug. Native full-SQL CI passes on
Windows, Linux and macOS. Broad legacy CI reports 250 successful steps, 22 skips
and the same four SABR/TLS/TTS failures as before. RAMSQL's candidate integration
passes all 32 active checks, including the native SB0 targets and hosted release
artifacts. These checks establish tested behavior, not throughput qualification.

Two complete fixed three-pair campaigns are retained separately in `sparse/`
and `sparse-repeat/`. Together with the earlier four phases this report retains
**72 original timed cases**, including all slow trials and original check failures.

| Campaign | Case | On QPS median (range) | Off QPS median (range) | Paired on/off ratios |
| --- | --- | ---: | ---: | --- |
| First | Customer totals, untargeted | 738.87 (734.02–742.64) | 689.64 (108.69–715.56) | 1.064, 6.833, 1.033 |
| First | Store/category | 1771.03 (1643.10–1820.26) | 632.46 (599.18–989.10) | 2.598, 2.956, 1.840 |
| Repeat | Customer totals, untargeted | 174.91 (146.28–690.56) | 133.74 (122.75–169.52) | 5.626, 1.094, 1.032 |
| Repeat | Store/category | 402.48 (330.17–1521.04) | 284.28 (265.87–325.14) | 5.351, 1.242, 1.238 |

Each campaign includes a two-second Windows process-image inventory. The first
records Sig images in its first three runs and PostgreSQL images in its first
control. The repeat records Sig images in its first enabled run. Both campaigns
therefore have `timing_watch_clear=false`. These records invalidate a clean
host interpretation; they do not establish process ownership or CPU usage.
The benchmark task itself did not launch those competing jobs, and stopped or
reprioritized none of them.

The repeat's remaining five runs have no observed matching images or inventory
errors, yet both binaries still slow sharply. Background interference is thus
not an established complete explanation. Periodic image inventory can miss short
jobs, WSL builds and unrelated workloads; absence of observations does not prove
a dedicated host. The repeat remains separate from the first campaign, and no
post-hoc subset or pooled median is used to qualify the optimization.

All timed operations succeed and all three supported original checks pass in
both campaigns. Four original checks still fail. The sparse candidate remains
disabled in ZPM defaults and held in draft
[ZPM PR 66](https://github.com/SB0LTD/zpm/pull/66) and
[RAMSQL PR 31](https://github.com/SB0LTD/ramsql/pull/31).
No reference-engine superiority, all-workload speedup or release readiness is
claimed from these measurements. The next investigation must distinguish
execution-path selection and actual kernel work from host scheduling/frequency
effects before another throughput qualification attempt.

### Original-query path counters

Separate instrumented binaries execute the unchanged original JDBC correctness
checks on the same full dataset. They emit existing counters after a detached
execute, without emitting SQL text. Their binary hashes and complete patch
scripts are retained in `diagnostics/` and `scripts/`. These are correctness/path
diagnostics, not throughput trials, and do not replace any measurement above.

| Counter | Customer totals, either mode | Store/category, either mode |
| --- | ---: | ---: |
| Logical fact rows | 5,000,000 | 5,000,000 |
| Nested factor plans / producer keys | 1 / 50,000 | 0 / 0 |
| Join factor plans / participating keys | 0 / 0 | 1 / 1,000 |
| Selected fact rows in join factor | 0 | 6,292 |
| Presence-rejected blocks | 0 | 55,112 |
| Final groups | 4 | 20 |
| Final grouping hash probes | 23,866 | 1,000 |

Only the enabled store/category execution records sparse work: **44,328 masks
containing 47,987 candidate positions**, rather than full-width evaluation at
those later-predicate steps. These counts include positions considered at
different predicates and are not distinct fact rows. The disabled execution
records zero sparse masks/positions. Every other recorded counter agrees
between modes, and both original result checks pass. The customer query records
zero sparse work in both modes. This establishes actual path usage and a real
reduction in later-predicate candidate evaluation; it does not establish a
stable wall-clock gain or explain the untargeted query's timing variation.

### Unclosed scope

RAMSQL still passes 3/7 original checks. The missing features include date
formatting in grouping, window ranking/window aggregates and grouping rollups.
These failures are retained, not removed from a qualification gate. Two passing
query timings do not qualify mixed workloads, writes, durability, every dialect
or 100M-row SQL tables. Earlier reference timings are historical context, not a
new paired three-engine comparison.

Earlier implementation and functional qualification are tracked in
[ZPM PR 63](https://github.com/SB0LTD/zpm/pull/63),
[ZPM PR 64](https://github.com/SB0LTD/zpm/pull/64) and
[RAMSQL PR 29](https://github.com/SB0LTD/ramsql/pull/29). None is accepted by this
report. Their library changes were subsequently included by PR 65 with disabled
defaults; the positive later campaign does not erase their rejected measurements.

Factorization removes repeated relational work but still scans necessary fact
columns. For selective equality/range queries, the next structural change must
reduce input reads. With N rows and m matches, compare scan cost proportional to
N input bytes with an exact posting-list path proportional to index lookup plus
m row gathers. Choose using observed cardinality and granted bytes, and retain
exact residual predicates. A small result does not by itself prove random gathers
will beat a sequential scan.

A caller-owned immutable posting index can use D+1 offsets and N u32 row IDs:
about 4N+4(D+1) bytes, before metadata, NULL omissions and update deltas. At 100M
rows that is about 400 MB for one column; indexing every column is not free.
Sorted block-local u16 postings/adaptive containers can reduce this, but their
update, build and intersection costs require qualification. Persistent derived
structures must publish after durable commit, preserve old snapshots, rebuild
on recovery and fall back when their generation is stale. A read query cannot
silently mutate shared storage while holding a reader lock.

Current research reinforces making this choice workload-dependent. DuckDB's
[indexing documentation](https://duckdb.org/docs/guides/performance/indexing)
describes the read/write/memory tradeoffs and why unordered data can defeat
zonemaps. The authors of
[Index Intersection for High-Dimensional Range Queries](https://dbis.cs.tu-dortmund.de/en/publications/2025/index-intersection/)
describe lightweight multi-attribute candidate filtering, presented at VLDB 2026;
its high-dimensional setting is not evidence of a speedup for this workload.
[Roaring's publications](https://roaringbitmap.org/publications/) provide a
foundation for adaptive bitmap/posting representations. These are candidates
for implementation and testing, not borrowed performance claims.

The larger capacity blocker remains separate: default-width inline version
payloads cost about 120 GB for 100M version slots alone. Compact typed base
columns with snapshot-safe sparse MVCC deltas, bounded checkpoint/recovery,
global intra-query scheduling and the missing SQL operators remain necessary.
See ZPM's `docs/SQL_ALGEBRAIC_EXECUTION.md` for ownership and rejection criteria.
