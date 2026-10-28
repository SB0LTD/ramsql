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
5. Binding retains validated DATE literal payloads, avoiding repeated text/date
   parsing. A before/after work-accounting fixture has identical counts for all
   nine cases; computed casts and invalid-literal errors retain their behavior.
6. Sparse leading AND predicates refine surviving slots before fact gathering.
   Unsupported/computed/nullable predicates stop the optimization; the complete
   WHERE still determines the answer and throwing joins retain their guards.
7. A conservative i128 proof admits 16-byte grouped state when every possible
   per-group prefix sum fits i64 and the input is globally non-null. Other
   groups retain exact 32-byte wide state. This changes scratch representation,
   not SQL types, memory ownership or write-time storage.

The source design and primary research links are in ZPM's
[`SQL_SELECTIVE_STREAMING.md`](https://github.com/SB0LTD/zpm/blob/9606877fcd05157af52e2965c5240388d68cc4a9/docs/SQL_SELECTIVE_STREAMING.md).

## Original-duration screening results

| Binary phase | Customer aggregation q5, queries/s | Selective three-way join q6, queries/s |
| --- | ---: | ---: |
| Prior committed-workspace fix | 5.2 | 8.1 |
| Leading predicate selection | 5.5 | 328.3 |
| Plan-local projection and visibility masks | 11.0 | 464.9 |
| Typed DATE projection | 10.4 | 635.8 |
| Caller-backed wide grouping | 136.9 | 617.5 |
| Follow-up numeric/projection experiment | 136.4 | 554.6 |
| Reused bound DATE literals | 144.7 | 799.3 |
| Sparse predicate cascade | 121.1 | 1270.0 |

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

## Repeated qualification and rejected experiments

The accepted narrow-group candidate uses ZPM
`9606877fcd05157af52e2965c5240388d68cc4a9`. Three original-duration trials
produced q5 throughput **150.10, 151.00, 178.37** queries/s and q6 throughput
**1255.93, 1369.80, 1498.33** queries/s: medians **151.00 / 1369.80**.
All timed operations succeeded and the same three original supported CSV checks
passed. The remaining four failures are retained. The timing-validity record
confirms that the next compilation started after the campaign completed.

Two further experiments were measured and excluded from integration:

| Experiment | Trials | Median q5/s | Median q6/s | Decision |
| --- | ---: | ---: | ---: | --- |
| Narrow groups, accepted candidate | 3 | 151.00 | 1369.80 | Integrate SQL changes |
| Candidate plus byte fingerprints | 3 | 119.84 | 1162.67 | Hold ZPM PR 58 |
| Candidate plus cancellation load-before-swap | 3 | 117.50 | 1084.06 | Revert server experiment |
| Fresh unchanged candidate control | 1 | 119.24 | 1083.12 | Retain host-variation evidence |

The fresh unchanged control overlaps the cancellation experiment's performance.
Thus the measurements establish no repeatable benefit from the server change;
they do **not** isolate it as the cause of a regression. Fingerprint and numeric
experiments likewise remain draft PRs, despite passing functional CI. Their
negative/inconclusive results, binary hashes and the unintegrated server patch
are retained. Trials ran sequentially on the same laptop, whose heterogeneous
core scheduling was uncontrolled; comparisons across phases are screening
evidence, not a randomized experiment or a production guarantee.

A fresh original-duration IRDB 1.10.821 single-pair recheck measured **201.7 /
1561.6** q5/q6 queries/s, with all seven independent checks passing. Its vendor
archive hash matches the latest downloadable archive checked on October 28.
That reference still exceeds the accepted RAMSQL three-trial medians; mixed
trial counts and host variation preclude a confidence claim. PostgreSQL and
IRDB repeated comparisons subsequently completed, each running alone with
fresh JVMs and the same original durations/data/checks:

| Engine | q5 three-trial range/s | q5 median/s | q6 three-trial range/s | q6 median/s | Original checks |
| --- | ---: | ---: | ---: | ---: | ---: |
| RAMSQL accepted candidate | 150.10–178.37 | 151.00 | 1255.93–1498.33 | 1369.80 | 3/7 |
| IRDB 1.10.821 | 160.51–198.43 | 184.21 | 1379.97–1483.48 | 1443.32 | 7/7 |
| PostgreSQL 18.3 | 4.01–4.43 | 4.28 | 13.28–13.58 | 13.43 | 7/7 |

Every timed operation succeeded. The earlier unchanged RAMSQL control and
changing reference results show why these sequential campaigns do not prove
a stable speedup ratio. These two analytical cases also do not establish
lookup, mixed-write, durable, full-suite or 100M-row leadership. Per-engine
repeat folders retain every trial, independent check and exact throughput
calculation; there is no dropped slow trial or substituted workload.

The live workflow [40681800172](https://github.com/benor1470/dbMeter/actions/runs/40681800172)
completed build and all three collection jobs, then correctly failed the
advantage gate (`errors=[]`, `qualified=false`, `merge_allowed=false`). Its
actual hosted memory budget rejects the full-data RAMSQL configuration, and
stock text/column/table capability probes still fail. No reduced data or
compiled text size substitutes for the original workload. The dbMeter PR
remains unmerged and no benchmark leadership claim is authorized by this run.

The newer workflow [40703594713](https://github.com/benor1470/dbMeter/actions/runs/40703594713)
tests the accepted DATE/cascade/narrow-group candidate, with the same outcome:
build and all three collection jobs succeed; advantage qualification fails,
without gate-format errors. Stock capability gaps and actual full-data memory
admission remain blockers. A short smoke mixed run also misses a declared
scenario, so it supplies no valid mixed-workload comparison.

## Correctness and measurement evidence

`provenance.json` identifies every ZPM source and binary hash, the original
bulk-input hash and dbMeter revision. Phase folders retain original JSON
histograms/timelines/summaries, full-suite JUnit failures, supported-check
results and command/CPU/memory records. No private reviewer SQL/source, raw
data CSV, vendor JAR or credentials are redistributed here.

The full SQL build passes after the integrated changes. The selective-join
contract passes 513 assertions across Windows/Linux ReleaseSafe and legacy
Linux Debug, including typed dates, NULL/error ordering, old snapshots,
private writes, tombstones, failed flush and replay. The wide-group contract
passes 485,432 independent i128-model/canonical assertions on a 12,011-row,
5003-key fixture, including totals beyond i64, exact averages, NULL groups,
extreme keys and work-limit publication. Native database CI passes on Windows,
Linux and macOS. The broad legacy graph still has its same four pre-existing
non-SQL failures; those are not represented as a clean all-repository run.

Profiles in `profiles/` sample instruction pointers of owned reactor/runtime
threads at 10ms intervals. They include waiting/ramp/warmup and are diagnostic
wall-time samples, not hardware cycle counters or throughput measurements.
The successive profiles identify dimension probes, canonical fact borrowing,
group comparisons/dispatch, then bucket arithmetic and typed-value overhead.
The accepted candidate's `profiles/narrow-q5.json` still samples column-value
returns, numeric comparisons/conversions and group scanning. Draft ZPM PR 59
tests using private projection destinations and immediate borrowed-column
copies; it is not included in the accepted candidate or its measurements.

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
- The selective path still scans the store column for each query. The optional
  byte-fingerprint experiment added about 40MB for this dataset without a
  repeatable benefit and remains excluded. A future exact low-cardinality
  selection structure needs canonical equality verification, publication,
  replay/compaction tests and measured write/memory costs.
- Profiled CTE/aggregate materialization still uses padded Cells. Evaluate
  typed intermediate representation and active-payload copying with explicit
  ownership/poisoning tests before replacing the existing layout.
- Compact typed base storage with sparse MVCC deltas remains the capacity
  priority; tested packed-column primitives alone do not supply this integration.
- The dbMeter PR remains unmerged. Its full correctness, repeated latency and
  significant-advantage gate is unchanged; no README leadership claim is made.
