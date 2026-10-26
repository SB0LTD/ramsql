# Checked production workload experiments

This suite compares the native SQL APIs of RAMSQL and InMemory.Net.irdb
1.10.821. It does not compare a RAMSQL server with an embedded reference.
Durability, concurrent clients and full PostgreSQL compatibility require
separate qualification; a pass here establishes none of those claims.

## Workloads and correctness

`tools/production_workloads.py` creates versioned `RAMTRC01` binary traces from
an independent Python model. Keys are shuffled before loading; values are
non-monotonic; payloads include NULLs. Each request records its expected shape
and a typed digest of every returned value, or its expected affected-row count.
Both adapters validate every request inside the timed interval. A computed
predicate forces a separate wide-row scan before/after warm-up and after every
trial. No query result cache is used.

| Case | Purpose |
| --- | --- |
| uniform_get | Random primary-key reads including payload consumption |
| hotspot_get | 90% of requests target the first 1% of keys |
| range_page | Random lower bounds with ORDER BY id LIMIT 32 |
| sort_score | ORDER BY non-monotonic value,id LIMIT 32 |
| filtered_sum | COUNT/SUM over randomized category filters |
| grouped | 1,024 groups, beyond the 64-group maintained summary budget |
| filtered_group | Random category thresholds followed by grouped COUNT/SUM |
| mixed_95_5 | 95% reads and 5% genuine value-changing writes |
| mixed_50_50 | Equal reads and genuine value-changing writes |
| churn | Delete, observe absence, reinsert and observe restored value |

Write cycles restore the initial state, allowing repeated execution without
turning updates into no-ops. Workload request counts are finite and reported;
repeated cycles represent that working set, not an unbounded random stream.
The grouped and sort cases intentionally repeat their SQL. Numeric data is
INT; the payloads are ASCII. JOINs, Unicode, skewed high-cardinality joins,
numeric NULL-heavy scans and multi-statement transaction contention remain
additional work.

## Measurement

Use ReleaseFast. Each engine has a fresh process and at least one second of
warm-up, then five duration-based trials. Two process cycles alternate engine
order. IRDB uses compiled import, a clustered primary-key index, and public
typed/null-aware integer accessors to avoid adapter-induced boxing. Neither
import nor the post-trial physical scan is included in throughput.

Reported p50/p95/p99 values are power-of-two histogram upper bounds, including
result validation. The optional `--require-wins` gate requires every case to
pass correctness, at least a 5% median lead, and RAMSQL's slowest observed
trial to exceed IRDB's fastest. This finite screening rule is not a statistical
confidence claim. Failures and slower results are retained and fail the gate.

The driver preserves raw logs, fixture hashes, source snapshots, binary hashes,
compiler identity, engine metadata and JSON summaries. Recreate large fixtures
from their recorded parameters; do not check generated datasets into Git.

```powershell
sig build ramsql-production-bench -Doptimize=ReleaseFast -Dwork-rows=2048
python tools/benchmark_production.py `
  --native sig-out/bin/ramsql-production-bench.exe `
  --dotnet PATH/TO/dotnet.exe `
  --irdb tools/irdb_bench/bin/Release/net10.0/IrdbBench.dll `
  --rows 10000 100000 1000000 --requests 1024 `
  --minimum-ms 300 --cycles 2 --require-wins --output RESULTS_DIRECTORY
```

Build the reference adapter with .NET 10 using
`dotnet build tools/irdb_bench/IrdbBench.csproj -c Release --locked-mode`.
For 512-byte payload tests build a separate native profile with
`-Dtext-bytes=512` and pass `--payload-bytes 512` to the driver.

## Infrastructure and tradeoffs

ZPM maintains a caller-backed committed integer projection. Publication occurs
after successful durable commit or validated replay. Queries with own writes,
old snapshots or unsupported expressions retain the ordinary MVCC executor.
Bounded 64-row predicate masks use LLVM vector comparisons where supported
and scalar comparisons on other backends. Dedicated grouped COUNT/SUM kernels
avoid padded Cell scans and redundant presence writes. No heap allocator is
introduced.

This is a memory/maintenance tradeoff: four projected columns need approximately
41 additional backing bytes per row, plus alignment. The benchmark reports
reserved backing size; that is not process RSS or an IRDB memory comparison.
Result/workspace and payload capacities remain explicit build/profile choices.
The benchmark uses 2,048 result/workspace rows, 128-byte text capacity and a
64-group summary budget. Those are declared harness budgets, not a claim of
unlimited capacity.

## Validation commands

```text
python tools/test_production_workloads.py
python tools/test_production_replay.py --binary PATH/TO/ramsql-production-bench
python tools/test_production_postgres.py --postgres-bin PATH/TO/pg/bin --output NEW_DIRECTORY
```

The PostgreSQL oracle owns a temporary cluster and checks every workload twice.
The native replay covers all ten shapes plus invalid magic, truncation,
trailing bytes and a deliberately incorrect expected digest. CI runs the
model contracts and native replay on every hosted OS in the existing matrix.
