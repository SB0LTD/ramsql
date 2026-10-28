# Profile-driven streaming execution: actual five-million-row data

These are local development measurements of the reviewer's unchanged dbMeter
queries and expected results, not a passed all-workload advantage gate. The
same six tables contain five million sales and 50,000 customers. Default
text-128 ReleaseFast binaries run with eight native workers and eight JDBC
clients. Each case uses the original 5-second ramp, 15-second warmup and
60-second measurement. Builds, profilers and other database services are
stopped during throughput measurements. Each timed case first passes its
original independent CSV check; every timed case below has zero SQL errors.

## Execution changes

All runtime changes are allocator-free Sig in ZPM's shared SQL layer:

1. A safe leading integral predicate selects 64-slot blocks before dimension
   probes, while full residual evaluation remains authoritative. Conservative
   ON/probe guards preserve errors that a later false WHERE must not hide.
2. Fact projection discovers dependencies from the current bound plan rather
   than confusing relation indices in other CTEs/query blocks. Committed proofs
   avoid redundant visibility/null reads; sparse flags use bounded SIMD masks.
3. Committed projection preserves exact DATE day counts and reconstructs typed
   DATE Cells. Old snapshots/private writes retain canonical MVCC evaluation.
   Durable publication, replay, copies, NULLs and infinities are checked.
4. Bounded integer-key COUNT/SUM/AVG grouping uses exact i128 totals in the
   caller's existing query arena. Admission considers the full key domain,
   output and finish handles; sparse/widened domains retain the general engine.
   There is no fixed 4096-key cutoff for this path, input-sized handle list,
   query-answer cache or larger compiled workspace array.

The source design and primary research links are in ZPM's
[`SQL_SELECTIVE_STREAMING.md`](https://github.com/SB0LTD/zpm/blob/c3b0c93fdb65837bba4738841a8e29aea3a6c3d9/docs/SQL_SELECTIVE_STREAMING.md).

## Original-duration screening results

| Binary phase | Customer aggregation q5, queries/s | Selective three-way join q6, queries/s |
| --- | ---: | ---: |
| Prior committed-workspace fix | 5.2 | 8.1 |
| Leading predicate selection | 5.5 | 328.3 |
| Plan-local projection and visibility masks | 11.0 | 464.9 |
| Typed DATE projection | 10.4 | 635.8 |
| Caller-backed wide grouping | 136.9 | 617.5 |
| Follow-up numeric/projection experiment | 136.4 | 554.6 |

Each row is one sequential trial pair, not a repeated confidence estimate or
an isolated attribution of every component's gain. Host variation remains
visible. A fresh prior-binary recheck is retained when complete. The final
numeric/projection experiment has not established a throughput gain and is
**not the integrated dependency**; its lower result is preserved rather than
discarded. Do not select the highest figure as a production guarantee.

Earlier isolated original-duration reference measurements were PostgreSQL
18.3 at 5.42/20.37 q5/q6 queries/s and IRDB 1.10.821 at 210.04/1531.01.
Those are contextual results from the
[preceding campaign](../dbmeter-full-five-million-20261028/REPORT.md), not a
new repeated matched comparison. They still identify IRDB as the remaining
target; this report does not establish superiority over it.

## Correctness and measurement evidence

`provenance.json` identifies every ZPM source and binary hash, the original
bulk-input hash and dbMeter revision. Phase folders retain original JSON
histograms/timelines/summaries, full-suite JUnit failures, supported-check
results and command/CPU/memory records. No private reviewer SQL/source, raw
data CSV, vendor JAR or credentials are redistributed here.

The full SQL build passes after the integrated changes. The selective-join
contract passes 460 assertions across Windows/Linux ReleaseSafe and legacy
Linux Debug, including typed dates, NULL/error ordering, old snapshots,
private writes, tombstones, failed flush and replay. The wide-group contract
passes 295,278 independent i128-model/canonical assertions on a 12,011-row,
5003-key fixture, including totals beyond i64, exact averages, NULL groups,
extreme keys and work-limit publication. Native database CI passes on Windows,
Linux and macOS. The broad legacy graph still has its same four pre-existing
non-SQL failures; those are not represented as a clean all-repository run.

Profiles in `profiles/` sample instruction pointers of owned reactor/runtime
threads at 10ms intervals. They include waiting/ramp/warmup and are diagnostic
wall-time samples, not hardware cycle counters or throughput measurements.
The successive profiles identify dimension probes, canonical fact borrowing,
group comparisons/dispatch, then bucket arithmetic and typed-value overhead.

To repeat after preparing the same original full data export and reviewer JAR:

```powershell
python tools/benchmark_dbmeter_full_ramsql.py --binary <ReleaseFast-ramsql.exe> --out <new-evidence-folder> --trials 3
```

The harness refuses to overwrite evidence, retains all original check failures,
records the actual input/binary hashes and aborts scoring on any timed SQL
error. Run each engine alone. A schema setup using the reviewer's old alpha.2
RAMSQL sample does not reproduce this dataset.

## Remaining blockers and next experiments

- Only **3/7 original full-data checks pass**. TO_CHAR, the two window-query
  shapes and ROLLUP remain unsupported. Partial throughput is not full parity.
- The default five-million-row server still commits roughly 7.06GB. These
  read-only in-memory trials do not establish 100M-row SQL capacity, durable
  write/churn performance or recovery at that scale.
- The selective path still scans the store column for each query. Evaluate an
  optional caller-owned byte fingerprint or exact low-cardinality selection
  structure with canonical equality verification, publication/replay/compaction
  tests, and measured write/memory overhead. No such index is implemented here.
- Profiled CTE/aggregate materialization still uses padded Cells. Evaluate
  typed intermediate representation and active-payload copying with explicit
  ownership/poisoning tests before replacing the existing layout.
- Compact typed base storage with sparse MVCC deltas remains the capacity
  priority; tested packed-column primitives alone do not supply this integration.
- The dbMeter PR remains unmerged. Its full correctness, repeated latency and
  significant-advantage gate is unchanged; no README leadership claim is made.
