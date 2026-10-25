# Infrastructure implementation evidence

25 October 2026. Target: production PostgreSQL compatibility with allocator-free,
RAM-resident execution and durable commits. **The complete target remains open.**
This record tracks implemented scope separately from the
[research/design plan](DEEP_INFRASTRUCTURE_RESEARCH.md) and the
[broader production gaps](GAP_CLOSING_PLAN.md).

## Profile-driven follow-up (25–26 October)

The [profile report and raw evidence](../benchmarks/profile-driven-20261025/REPORT.md)
record four allocator-free ZPM iterations: primitive-word hashing, cached integer
top-K, conservative physical-order proofs, and lazy rejection of losing top-K
candidates. ZPM PR 26 passes database-platform CI on Linux, Windows and macOS.
These are development candidates; alpha.4 remains the published reviewer release.

Longer alternating native trials show point reads effectively tied and grouped
aggregation improving from about 35 to 40 ops/s at one million rows. Proven
ordered layouts produce much larger filtered/sort gains; permuted data retains
full scans and is reported separately. Fresh PostgreSQL 16.15 wire comparisons
still lose aggregate and mixed workloads. Latest IRDB 1.10.821 remains far ahead
on scalar/group aggregation (643/518 ops/s versus native RAMSQL about 72/40).

Actual server CPU probes and durable reopen checks were added, with low sample
counts explicitly retained. Off-CPU attribution, compact typed storage, maintained
ordered access, concurrent scaling and the remaining compatibility/capacity gaps
are still open. The legacy ZPM root CI run 40237517617 retains the four historical
non-database failures; it is not a green repository-wide run.

## Merge and qualification

ZPM PRs 24 and 25 merged to `main` (latest merge `b6039cf`); RAMSQL
[PR 9](https://github.com/SB0LTD/ramsql/pull/9) merged as `95c688c`.
The qualified RAMSQL head is `311c671aecee6b4774cd6e6d977a9bc0a33413d8`;
the merge retains that implementation and pinned dependency.

- [Final RAMSQL CI](https://github.com/SB0LTD/ramsql/actions/runs/40218084723)
  passed all four hosted runners and PG16/PG18 memory/durable+SCRAM cells.
- [Final eight-target qualification](https://github.com/SB0LTD/ramsql/actions/runs/40218084772)
  passed all cross-builds, six exact hosted artifacts, SB0X/SB0K ABI/QEMU
  fixtures, exact Linux artifact PG16/PG18 qualification, durable benchmark,
  bundle verification and provenance attestation. Native fixtures do not
  establish deployment on a complete SB0 OS with real TCP/durable storage.
- [ZPM budget-safety platform CI](https://github.com/SB0LTD/zpm/actions/runs/40217079153)
  passed Linux, macOS and Windows database contracts.

Source remains a development alpha.4 candidate. This qualification establishes
the tested scope, not complete PostgreSQL compatibility, all-benchmark superiority
or production readiness. Production release gates remain blocked.

## Streaming aggregation candidate

Reusable implementation: [ZPM PR 24](https://github.com/SB0LTD/zpm/pull/24),
with [budget-safety follow-up PR 25](https://github.com/SB0LTD/zpm/pull/25).
The current dependency commit is `d99db721beb3ca4f43d7941685a6973f0e608884`.
RAMSQL consumes its immutable commit archive with SHA-256
`b3d47bf983deaa1de01d8bb5a007f39f0d3de35e27759578d7b11b532c3db013`.

Eligible single-base-table aggregates now walk visible versions directly. Scalar
aggregates keep bounded local state. Hash grouping owns one key and aggregate
record per group in the existing caller-owned workspace. It no longer builds
source-row, tuple, per-input key or sorting-ordinal arrays on this path. Simple
column arguments/keys are borrowed for the immediate synchronous step; computed
and correlated expressions retain the existing evaluator. Only group handles
are sorted. No heap allocator, general arena, OS call or foreign dependency is
introduced into ZPM core.

The path reuses SQL aggregate arithmetic and finish rules, canonical typed
hashing/full equality, snapshot visibility, FILTER/HAVING, projection and final
ordering. Aggregate state uses explicitly compatible alignment within reserved
Cell slots. NULL keys do not call the primary-key hash's non-NULL interface.
Every state/bucket is initialized before reading, and all output stays private
until query success. Empty grouped input does not reserve a hash table.

Group capacity comes from remaining Cell/reference space including projection
and ordering overhead. All-unique input can exhaust this budget: the error
preserves the previous Result. DISTINCT aggregates, derived/join sources and
finite unordered grouped LIMIT retain their existing materializing paths. Scalar
positive LIMIT and ordered grouped LIMIT can use streaming. Execution still runs
synchronously; cursor yielding, multicore and protocol result streaming are
separate milestones.

At high cardinality, several aggregate states per group can exceed the hash
budget even though the previous sort operator fits. Pure queries release private
hash scratch and retry that operator on WorkspaceCapacity, retaining all charged
execution work. Plans with function/subquery nodes conservatively use the existing
path immediately so volatile evaluation cannot repeat. The 350-group/seven-
aggregate regression and a nextval WHERE test preserve old capacity and prove
one sequence evaluation per source row. The contract now has **1,197 assertions**.
All six Linux/Windows live suites and local PG16 durable+SCRAM 37/37 probes also
pass against this hardened pin. Full Linux SQL/database revalidation also passes,
including the expanded 1,197-assertion contract. The
[hardened-pin benchmark pack](../benchmarks/streaming-budget-20261025/REPORT.md)
retains its own fresh baseline, raw native/IRDB trials, exact source hashes and
same-binary PG16 durable report. Earlier results below remain identified with
their original streaming pin.

### Evidence

- New ZPM contract: 100,000 caller-backed rows with **1,024 query-reference
  slots**, independently modeled 64-group COUNT/SUM at 512 and 100,000 rows,
  and equal measured group scratch at both scales. The final contract executes
  **1,197 assertions** including the capacity/volatile safety regressions.
  Real collisions exercise full-key verification.
- Tests include scalar empty input, empty groups, all-filtered input, composite
  integer/text keys, FILTER/HAVING, ordered LIMIT/OFFSET, scalar LIMIT, numeric
  scale equivalence, signed float zero/NaN, equivalent intervals and JSONB.
- Error tests cover too many groups, integer expression overflow, cancellation
  after work begins, work-budget exhaustion, poisoned scratch and unchanged prior
  results. Transaction tests distinguish another writer's pending version from
  the writer's own view and verify abort restores subsequent aggregates.
- Full Linux SQL/database suites passed, including 63,417 query, 239,606 index
  and 3,154 store assertions. The complete final-pin Windows RAMSQL contract
  build passed, including the 1,197-assertion stream contract. Optimized Linux
  ReleaseFast query and stream contracts also passed. Platform CI is linked above.
- Live Linux and Windows CLI/server/independent-client/SCRAM/persistence/capacity
  suites passed for the initial pin: 3,996 server assertions, 8,506 auth
  assertions, 58 persistence cases with 1,757 assertions and five forced kills.
  Capacity checks now verify 64-group values before and after durable restart.
  All six live suites passed again on Linux and Windows against the final pin.
- Local native PostgreSQL 16 differential probes match **37/37** in memory and
  durable+SCRAM modes for the initial pin. This finite suite does not prove full
  PostgreSQL compatibility. The final pin also passed the local PG16
  durable+SCRAM probes 37/37. Final artifact PG16/PG18 qualification remains required.
- A default-small-profile compile-time overflow in WAL scratch size inference was
  discovered by the new contract and fixed by making the intermediate a `usize`.

### Current measured benchmark

The hardened pin passes all selected cases at all three sizes. Its fresh
million-row native scalar aggregate rises from **39 to 72 ops/s**, while the
64-group query changes from WorkspaceCapacity to **31 ops/s**. Group scratch
remains 512 Cells/1,460 references. IRDB leads at 542/478 scalar/group ops/s;
PostgreSQL leads the filtered ordered wire read at 3,721 versus 31 ops/s.
These final-pin results supersede earlier numbers for the current integration;
all captures remain available. Performance variation on unchanged workloads
prevents attributing small differences to this patch without more controlled runs.

### Earlier streaming benchmark

The [initial streaming benchmark pack](../benchmarks/streaming-aggregation-20261025/REPORT.md),
using dependency `de897ae55a0e5678c7d8811b161457fefc2f7cc9`,
retains raw results, stream counters, binary/source hashes and a fresh rerun of
the preserved pre-change native binary. All six selected cases now produce
correct results at 512, 100,000 and 1,000,000 rows in each engine/boundary tier.
The native million-row COUNT/SUM case rises from 36 to 78 ops/s in that fresh
comparison. The 64-group case changes from WorkspaceCapacity to 35 ops/s.
Recorded group scratch stays at 512 Cells and 1,460 reference slots for all
three sizes; these are partial operator counters, not total process memory.

IRDB 1.10.821 still leads million-row scalar/group aggregates at 488/436 ops/s.
PostgreSQL still leads the filtered ordered wire read at 4,118 versus 30 ops/s.
The million-row native backing remains 537,165,824 bytes. These results support
the scoped capacity fix and scalar improvement; ordered access and compact
typed storage remain necessary. Five sequential exploratory trials do not
establish statistical significance for small differences or all-workload wins.

One earlier RAMSQL macOS CI attempt passed build/contracts/CLI/server, then failed installing
independent test clients because `files.pythonhosted.org` DNS resolution failed;
the skipped tests were not counted as passed. Final macOS CI and exact-artifact
qualification subsequently passed. The legacy ZPM Sig 0.5.9 root
workflow continues to fail the same four
historical non-database targets (`test-sabr-loop`, `test-manifest-sabr`,
`test-tls-client`, `test-tts-gpu`). Do not describe repository-wide CI as green.

## Milestone status

| Plan step | Status | Remaining evidence/work |
| --- | --- | --- |
| D0: observability | Partial | Streaming counters and benchmark Cell/version/backing sizes added; full plan, residency and physical-operator accounting still open. |
| D1: streaming aggregates | Implemented eligible subset | Final-pin live/benchmark/full matrix evidence passes. DISTINCT, function/subquery, joins/derived sources and remaining lazy LIMIT shapes still need bounded paths. |
| D2: ordered/composite indexes | Open | Ordered index, maintenance/rollback/replay, physical planning and measured access reduction. |
| D3: compact variable storage | Open | Typed pages, variable values/wider schemas, retention-safe compaction, format migration and peak-memory evidence. |
| D4: resumable/session/result budgets | Open | Yield/resume ownership, fairness, portals, backpressure and byte-budgeted sessions. |
| D5: durable scheduling/checkpoints | Open | Bounded commit queue, flush/publication state machine, checkpoint publication and WAL retirement. |
| D6: multicore execution | Open | Worker ownership, safe queues, reclamation and real x86_64/AArch64 scaling. |
| D7–D8: adaptive/specialized algorithms | Open | Baselines, applicability proofs and end-to-end results. |

The runtime row-budget foundation merged in RAMSQL PR 8 after its full matrix
passed. Published alpha.3 and historical benchmark packs remain separate from
this candidate. Production release gates stay blocked; this change does not
remove text/schema/session bounds or establish superiority over IRDB.
