# Infrastructure implementation evidence

25 October 2026. Target: production PostgreSQL compatibility with allocator-free,
RAM-resident execution and durable commits. **The complete target remains open.**
This record tracks implemented scope separately from the
[research/design plan](DEEP_INFRASTRUCTURE_RESEARCH.md) and the
[broader production gaps](GAP_CLOSING_PLAN.md).

## Streaming aggregation candidate

Reusable implementation: [ZPM PR 24](https://github.com/SB0LTD/zpm/pull/24),
commit `de897ae55a0e5678c7d8811b161457fefc2f7cc9`.
RAMSQL consumes its immutable commit archive with SHA-256
`b6bc534017220d0a48f7bf26cd7c56380f3dda4b659569fbf84e34142f779387`.

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

### Evidence

- New ZPM contract: 100,000 caller-backed rows with **1,024 query-reference
  slots**, independently modeled 64-group COUNT/SUM at 512 and 100,000 rows,
  and equal measured group scratch at both scales. The final focused run executes
  **844 assertions**. Real collisions exercise full-key verification.
- Tests include scalar empty input, empty groups, all-filtered input, composite
  integer/text keys, FILTER/HAVING, ordered LIMIT/OFFSET, scalar LIMIT, numeric
  scale equivalence, signed float zero/NaN, equivalent intervals and JSONB.
- Error tests cover too many groups, integer expression overflow, cancellation
  after work begins, work-budget exhaustion, poisoned scratch and unchanged prior
  results. Transaction tests distinguish another writer's pending version from
  the writer's own view and verify abort restores subsequent aggregates.
- Initial full Linux SQL/database suites passed, including 63,417 query,
  239,606 index and 3,154 store assertions. Windows focused SQL and all database
  contracts passed; RAMSQL's complete Windows contract build also passed.
  After column-borrowing/empty-reservation refinement, Linux query/type/store/new
  aggregate suites and Windows query/new aggregate suites passed again.
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

Platform/release CI and final benchmark evidence are recorded separately when
available. The legacy ZPM Sig 0.5.9 root workflow continues to fail the same four
historical non-database targets (`test-sabr-loop`, `test-manifest-sabr`,
`test-tls-client`, `test-tts-gpu`). Do not describe repository-wide CI as green.

## Milestone status

| Plan step | Status | Remaining evidence/work |
| --- | --- | --- |
| D0: observability | Partial | Streaming counters and benchmark Cell/version/backing sizes added; full plan, residency and physical-operator accounting still open. |
| D1: streaming aggregates | Candidate subset | Final-pin live/matrix/benchmark qualification; DISTINCT, joins/derived sources and remaining lazy LIMIT shapes still need bounded paths. |
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
