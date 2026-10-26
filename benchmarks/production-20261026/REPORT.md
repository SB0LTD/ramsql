# Production workload evidence — 26 October 2026

The original 30-case matrix passed correctness and the performance screen in
29 comparisons. The remaining comparison, a 1,024-operation million-row churn
trace, exceeded IRDB's 180-second process budget in both process cycles. The
original matrix therefore **fails its overall gate**. Timeouts are retained;
they are not scored as victories.

A separately labeled 64-operation million-row churn trace completed all trials
for both engines and passed the same gate. RAMSQL measured 522 operations/s
against IRDB's 9.67 operations/s. This shorter working set does not erase the
incomplete longer test.

All 21 read comparisons passed: seven read workloads at 10,000, 100,000 and
1,000,000 rows. At one million rows, the median leads were 2.76x for non-monotonic
top-k sorting, 4.99x for filtered SUM, 1.99x for grouping and 1.37x for filtered
grouping. Both mixed-write ratios also passed at all three sizes. These are
finite single-worker native SQL API comparisons, not a universal or production
readiness claim.

## Environment and method

Intel Core i9-14900HX, 24 cores/32 logical CPUs, approximately 32 GiB RAM,
Windows 11 build 26300. Sig 0.6.4, ReleaseFast. IRDB is latest stable
InMemory.Net.irdb **1.10.821**, verified against the live NuGet version feed;
the reference runs on official .NET 10.0.14. See the retained host/runtime/
version JSON records. Affinity, frequency and unrelated host activity were not
controlled. Local builds/profiles did not overlap throughput runs.

Each engine starts a fresh process, imports shuffled data, warms for at least
one second, and executes five complete-cycle trials of at least 300 ms. Two
process cycles alternate engine order, giving ten observed trials per engine.
Long trace cycles can substantially exceed the minimum duration. The independent
Python model specifies every returned value through typed digests. Every timed
request and every post-trial physical check must pass. IRDB compiled import and
the clustered primary-key index are enabled; typed integer accessors avoid
boxing results in the adapter.

The performance screen requires a median lead of at least 5% and RAMSQL's
slowest observed trial to exceed IRDB's fastest. This is a finite screening
rule, not statistical confidence. See [the methodology and commands](README.md).

## Files and source identities

- `production-matrix/`: original 30-case, 96-byte payload, 1,024-group matrix;
  nominal 1,024 requests, with actual case counts recorded in fixture metadata.
- `churn-bounded/`: separate 64-request million-row churn comparison.
- `wide-profile/`: separate seed, 512-byte payload and 2,048-group boundary profile.
- `analytic-million/`: earlier 500 ms analytical-only confirmation, all three
  cases passed before the larger matrix.
- `postgres-oracle.json`: 2,038 checks against an owned PostgreSQL 16.15 cluster.

Each run directory contains raw logs, JSON results and byte-preserved source
snapshots. Large fixtures and runtime binaries are not committed; their hashes
and generation parameters are retained. ZPM revision
`27745fa2b14ffc0e4ea793f50272eb82b6adf51c` has verified archive SHA-256
`dfbe4f46337f36e6bf3790a8d17f28d3e7a6009fc21e7343c562316cf09b4cf5`.
It is included in ZPM main through merge `5b44c30e88f74f8d20bf863a985688f6b7a745e5`.

The original matrix and analytical confirmation predate two harness corrections:
portable file reads now use ZPM's existing adapter, and each request clears
aggregate telemetry before execution. Flat reads/DML previously retained the
preceding physical post-check's aggregate counters. Consequently, do **not**
interpret original non-aggregate `streamed_wide_rows` counters as actual scans.
Their checked outputs and recorded elapsed timings remain valid. Aggregate
plans reset their own workspace, so the original compact scan counts are valid.
The bounded churn and wide profile use the corrected harness. Source snapshots
make this distinction explicit.

## Infrastructure, verification and remaining work

The optimization is reusable allocator-free ZPM code: committed column-major
integer projections, bounded vector/scalar predicate masks and specialized
grouped COUNT/SUM kernels. It publishes after successful durable commit or
validated replay. Old snapshots, own writes and unsupported expressions retain
the MVCC path. The extra projection costs approximately 41 backing bytes per
row for four columns, plus alignment; no process-memory superiority is claimed.

The compact scan contract passed **23,371 assertions**, covering poisoned caller
buffers, nullable inputs, mutations, snapshots, savepoint/abort, failed durable
hooks, replay/copy/compaction, cancellation/work budgets, signed-key extremes and
aliased input columns. Native replay passed all ten shapes and four corrupt
inputs. The independent PostgreSQL model replay passed all ten cases twice.

Hosted OS replay, server/client/authentication/persistence tests and PostgreSQL
16/18 memory/durable qualification run in the existing CI/release matrix.
The portable file reader fix was validated on macOS. ZPM's dedicated database
contracts passed Linux/macOS/Windows; its broad legacy CI still has four
unrelated SABR/manifest/TLS-client/TTS compilation failures and is not claimed
green.

Outstanding qualification includes comparable IRDB server concurrency,
durability/recovery performance, JOIN-heavy workloads, Unicode, nullable numeric
scan distributions, larger working sets and the original long churn trace with
a sufficiently large process budget. Capacities remain explicit caller/profile
budgets. The benchmarks do not establish unlimited capacity, full PostgreSQL
compatibility or production readiness. Published alpha.4 is unchanged.

## Original matrix medians

Operations/s include parsing, execution, result consumption and validation.
The failed churn comparison has no qualified IRDB score.

| Rows | Workload | RAMSQL ops/s | IRDB ops/s | Median ratio | Screen |
| ---: | --- | ---: | ---: | ---: | --- |
| 10000 | uniform_get | 1457155.3 | 1112.5 | 1309.82x | pass |
| 10000 | hotspot_get | 1571465.7 | 1138.8 | 1379.88x | pass |
| 10000 | range_page | 431615.3 | 5234.2 | 82.46x | pass |
| 10000 | sort_score | 10981.6 | 2259.5 | 4.86x | pass |
| 10000 | filtered_sum | 416774.8 | 375.0 | 1111.32x | pass |
| 10000 | grouped | 8283.8 | 438.9 | 18.88x | pass |
| 10000 | filtered_group | 13663.6 | 463.6 | 29.47x | pass |
| 10000 | mixed_95_5 | 1444820.8 | 1075.7 | 1343.17x | pass |
| 10000 | mixed_50_50 | 1405369.5 | 971.0 | 1447.39x | pass |
| 10000 | churn | 173888.9 | 1033.4 | 168.27x | pass |
| 100000 | uniform_get | 1421311.9 | 2099.7 | 676.91x | pass |
| 100000 | hotspot_get | 1499241.3 | 2063.3 | 726.64x | pass |
| 100000 | range_page | 272870.6 | 683.0 | 399.51x | pass |
| 100000 | sort_score | 847.0 | 223.6 | 3.79x | pass |
| 100000 | filtered_sum | 65748.6 | 1088.1 | 60.42x | pass |
| 100000 | grouped | 5364.8 | 567.9 | 9.45x | pass |
| 100000 | filtered_group | 6995.4 | 628.4 | 11.13x | pass |
| 100000 | mixed_95_5 | 1432893.9 | 1063.3 | 1347.65x | pass |
| 100000 | mixed_50_50 | 1468417.7 | 307.9 | 4769.75x | pass |
| 100000 | churn | 8944.9 | 69.7 | 128.42x | pass |
| 1000000 | uniform_get | 1425601.8 | 2920.3 | 488.17x | pass |
| 1000000 | hotspot_get | 1432479.4 | 2968.4 | 482.57x | pass |
| 1000000 | range_page | 166535.4 | 93.2 | 1786.09x | pass |
| 1000000 | sort_score | 50.7 | 18.4 | 2.76x | pass |
| 1000000 | filtered_sum | 5830.4 | 1167.4 | 4.99x | pass |
| 1000000 | grouped | 1251.4 | 628.0 | 1.99x | pass |
| 1000000 | filtered_group | 1248.5 | 911.8 | 1.37x | pass |
| 1000000 | mixed_95_5 | 1410997.7 | 1038.9 | 1358.14x | pass |
| 1000000 | mixed_50_50 | 1412210.8 | 148.5 | 9508.62x | pass |
| 1000000 | churn | 552.6 | timeout | unscored | FAIL |

## Wide profile medians

All ten cases passed correctness and the performance screen with 100,000 rows,
2,048 groups, 512-byte payloads, seed 20261027 and 64 nominal requests.
The profile reserves 4,096 workspace rows and 2,048 result rows.

| Workload | RAMSQL ops/s | IRDB ops/s | Median ratio |
| --- | ---: | ---: | ---: |
| uniform_get | 996643.0 | 3036.8 | 328.19x |
| hotspot_get | 1035122.2 | 3302.4 | 313.44x |
| range_page | 210653.1 | 633.8 | 332.34x |
| sort_score | 767.5 | 211.1 | 3.64x |
| filtered_sum | 69920.5 | 1439.0 | 48.59x |
| grouped | 1771.6 | 718.4 | 2.47x |
| filtered_group | 3785.1 | 852.4 | 4.44x |
| mixed_95_5 | 982144.9 | 1028.9 | 954.60x |
| mixed_50_50 | 1073126.7 | 286.7 | 3743.37x |
| churn | 7868.6 | 35.9 | 219.06x |
