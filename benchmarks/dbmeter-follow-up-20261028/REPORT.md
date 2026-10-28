# Follow-up profiling and unchanged-build controls

These development experiments do **not** establish universal benchmark
leadership. All three experiments remain draft ZPM PRs and are excluded from
the accepted RAMSQL dependency. The accepted DATE, sparse-filter and narrow-group
changes were merged in [RAMSQL PR 25](https://github.com/SB0LTD/ramsql/pull/25)
after 32 active checks passed, including native SB0S/SB0K qualification.

## Same original workload, retained negative results

Every campaign uses the original five-million-sale, 50,000-customer dataset,
default text-128 ReleaseFast, eight native workers and eight JDBC clients. Each
case uses the original 5s ramp, 15s warmup and 60s measured duration, with a fresh
JVM per case. Builds, profilers and other database services are stopped during
throughput measurement. Every timed operation succeeds and each timed query
first passes its independent original CSV check. All full-suite JUnit failures
are retained: RAMSQL still passes **3/7** original checks, not seven.

| Candidate | q5 three-trial range/s | q5 median/s | q6 three-trial range/s | q6 median/s |
| --- | ---: | ---: | ---: | ---: |
| Projection destination experiment | 119.10–121.06 | 120.01 | 1004.92–1097.17 | 1082.28 |
| Operator specialization experiment | 104.86–121.95 | 119.39 | 1024.08–1069.16 | 1054.96 |
| Exact byte-domain experiment | 124.64–182.90 | 180.85 | 1433.59–1820.93 | 1643.29 |
| Subsequent unchanged accepted build | 125.36–174.21 | 140.43 | 1170.65–1554.51 | 1541.94 |

Each row is a separate sequential campaign; trials are not dropped, pooled or
randomized. The preceding unchanged single-pair control measured 119.24/1083.12,
and its earlier three-trial campaign measured 151.00/1369.80. This variation is
material. The byte experiment's q6 range overlaps the subsequent unchanged
control's range, while untargeted q5 also changes substantially. These results
do not isolate a stable causal speedup or justify adopting additional backing
memory based on a peak result.

The [preceding repeated references](../dbmeter-streaming-20261028/REPORT.md)
measured IRDB 1.10.821 at median 184.21/1443.32 q5/q6 queries/s and PostgreSQL
18.3 at 4.28/13.43, with all seven independent checks passing for each reference.
The byte candidate's q5 median remains below IRDB's and its q6 range overlaps
IRDB's. This is not a significant all-case advantage, a mixed-workload comparison
or a reason to merge [dbMeter PR 1](https://github.com/benor1470/dbMeter/pull/1).
The live workflow's unchanged correctness/repetition/latency/2x gate remains
unpassed; hosted full-data capacity admission is still a separate blocker.

## What was tested

- [ZPM PR 59](https://github.com/SB0LTD/zpm/pull/59) borrows column values only
  until copying them into a newly reserved private projection destination.
  Computed expressions use that destination as scratch. The query suite,
  including its new ownership checks, passes 63,434 assertions on
  Windows/Linux ReleaseSafe and Linux Debug,
  including CTEs, repeated maximum-length text/numeric columns, NULLs, poisoned
  workspace and failed-query result preservation. No useful gain was established.
- [ZPM PR 60](https://github.com/SB0LTD/zpm/pull/60) moves comparison-operator
  dispatch outside sparse survivor loops. Its 513-assertion model passes in the
  same three modes; all nine instrumented work totals match the accepted source.
  The instruction-level change did not produce a repeatable throughput benefit.
- [ZPM PR 61](https://github.com/SB0LTD/zpm/pull/61) includes optional caller-backed
  fingerprint lanes from earlier held experiments, then proves exact equality
  when conservative bounds [L,H] satisfy H-L<=255. Distinct values in that
  interval cannot share a residue modulo 256. Outside literals reject all rows;
  NULLs are checked separately; unknown/wider domains retain full-width checks.
  Pending/aborted extrema only widen bounds. Its explicit-backing independent
  model passes 4,152 assertions in all three modes, covering signed limits,
  zero wrap, NULLs, 257-element collision domains, aborted extrema, updates and
  deletion. The inherited streaming/publication contracts pass 15,751 assertions;
  all nine work totals match the accepted source. Standard compact backing adds
  one byte per column/row, about 40MB here. Write-cost qualification is incomplete.

All three PRs pass native Windows/Linux/macOS database/full-SQL CI. Broad legacy
CI retains four known non-SQL failures, not a clean repository-wide result.
For the byte branch it reports 240 successful steps, four failed and 22 skipped.
No allocator, foreign database engine or query-answer cache was added to RAMSQL.

## Attribution limits and next work

`profiles/accepted-q6-addresses.json` retains diagnostic 10ms instruction-pointer
samples and module/symbol offsets from the accepted binary. Its hot sparse-loop
address follows a memory comparison, so samples alone do not distinguish memory
stalls from dispatch cost. They include ramp/warmup/runtime waiting and are not
PMU cycles or throughput measurements. Do not interpret the largest sampled
instruction as a proven optimization opportunity without a measured control.

The read-only Windows topology snapshot reports two efficiency classes, each
with 16 logical CPUs, and an unrestricted 32-CPU affinity mask. Microsoft
documents [efficiency classes and their interpretation](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-system_cpu_set_information).
This snapshot does not identify which cores executed previous trials or prove
the cause of variation. No power plan or process/thread affinity was changed.
Controlled, interleaved repetitions are needed before a stronger attribution.

The larger infrastructure priorities remain compact typed base storage with
sparse MVCC deltas and typed intermediates, followed by the unsupported SQL
features. The [2026 FastLanes thesis](https://ir.cwi.nl/pub/36173) motivates
SIMD-friendly encodings and partial decoding; its reported results are not
RAMSQL results. Current inline version cells still dominate capacity. These
read-only trials establish neither 100M-row SQL capacity nor durable write,
churn or recovery performance at that scale.

Each phase retains complete JSON histograms, timelines, summaries, binary/data
hashes, command records, real server memory/CPU counters and independent check
results. Machine-capacity snapshots come from a separate CLI probe; server
memory comes from the owned server PID. No private reviewer source/SQL, raw
input CSV, vendor JAR or credentials are published in this bundle. Reproduce
with `tools/benchmark_dbmeter_full_ramsql.py --binary <binary> --out <fresh-path>
--trials 3` after preparing the original data and reviewer driver.
