# Read-path optimization evidence — 26 October 2026

This is a measured development iteration, not a production-readiness or universal-performance claim. Published alpha.4 predates these changes. The reviewer scripts remain unavailable.

## Source and method

ReleaseFast Sig 0.6.4 / matched compiler 90a898d on an Intel Core i9-14900HX, Ubuntu/WSL2 Linux 6.6.87.2. One client; deterministic four-column fixture. Every result is consumed and independently checked. No throughput run overlapped our builds or profiling. CPU affinity/frequency and unrelated host activity were not controlled. Small differences and overlapping ranges do not establish significance.

The preserved native baseline uses ZPM `c3a00ab039259e9834e01f04dc91c3107a0484fe` (the preceding merged optimization implementation). The recorded throughput/profile integration pins `28036d8e173b7f5971da5905257e0cced4cff551`, archive SHA-256 `7a04a0b93f676af19a887cad572dd8c89487534349bcdeffe78fd9fac67ca8e4`. `SOURCE_STATE.json` records current source hashes; raw results identify every binary hash.

| Stage | ZPM revision | Change |
| --- | --- | --- |
| Scalar summaries | `9c392c1de4a356c65bf9f0bc109078046930ecf6` | Exact committed counts/non-NULL counts/integer totals. |
| Dense grouping | `9747ec571ec6af65d82ef936c7d65e4acb0e2340` | Bounded-domain direct grouping and prepared integral transitions. |
| Group summaries | `812afc7a5edd223b0e68426691bc25a39780843e` | Optional caller-budgeted exact group totals with owned representatives. |
| Ordered probes | `65be16457568ce058995f471dab607e3c760ac68` | Dense committed PK proof, bounded ordered hash probes, non-NULL sort proof and direct visible-version lookup. |
| Clock/reactor | `28036d8e173b7f5971da5905257e0cced4cff551` plus RAMSQL reactor | POSIX wall clock/vDSO; readiness-gated accepts. |

## What the fast paths guarantee

Committed totals are maintained at publication after the durable hook, and reconstructed by WAL replay. Metadata never includes pending changes. Old snapshots, transactions with own writes, filters/computed aggregate inputs, unsupported representative types and exhausted summary budgets fall back to execution. Scalar/group COUNT, integral SUM and AVG have explicit eligibility rules; this is maintained aggregate state, not a million-row scan at millions of scans/second. The default RAMSQL group budget is 64 per column; `-Dsummary-groups=0` disables it. It does not limit stored rows or query group cardinality.

Dense integral grouping uses conservative all-version bounds; wide domains use existing fallback. Ordered probes use exact committed count and conservative committed PK bounds plus uniqueness to prove a gap-free integer interval. They query the existing hash index in order, cap speculative work and restart the ordinary path on budget exhaustion. Sparse domains, own writes, old snapshots, locking and unsupported expression shapes retain normal execution. A residual-predicate heuristic avoids probing an entire small table. `key_probes` counts ordered key lookup requests, not hash bucket/memory accesses.

## Native before/after and latest IRDB

`native-final-*` uses the final integration and a physical seed scan outside the recorded import timer. Both revisions are calibrated independently per case, with two alternating cycles and five trials each; calibration is retained/excluded. Earlier stage series use the saved `harness-before-physical-seed.sig`. `probes-1000000-permuted` uses stage 65be, before the server-only clock/reactor changes. All series remain visible, including slower cases.

IRDB is latest stable **InMemory.Net.irdb 1.10.821**, freshly checked against [the NuGet feed](https://api.nuget.org/v3-flatcontainer/inmemory.net.irdb/index.json). Compiled import and clustered ID index are enabled. Two fresh runs/five trials each use a 200 ms calibration target, but JIT/runtime variation yields actual trials as short as 60 ms; retained raw samples are not filtered. IRDB runs are sequential, not paired with RAMSQL, and support exploratory comparisons rather than confidence estimates. Native API numbers are not compared to wire numbers.

### 512 rows, ordered native API — median ops/s

| Case | Prior RAMSQL | Current RAMSQL | IRDB |
| --- | ---: | ---: | ---: |
| primary_key_hot | 2,019,562.42 | 2,057,200.46 | 3,065.86 |
| filtered_limit | 100,329.19 | 104,835.81 | 2,912.20 |
| sort_limit | 1,656,991.26 | 1,579,226.24 | 20,534.08 |
| aggregate | 173,214.67 | 1,836,042.36 | 1,567.89 |
| group_aggregate | 59,973.21 | 185,463.60 | 1,255.23 |
| mixed | 181,811.70 | 169,206.27 | 3,453.76 |

### 1,000,000 rows, ordered native API — median ops/s

| Case | Prior RAMSQL | Current RAMSQL | IRDB |
| --- | ---: | ---: | ---: |
| primary_key_hot | 2,125,559.69 | 2,193,691.09 | 4,209.24 |
| filtered_limit | 54,534.63 | 55,576.02 | 371.71 |
| sort_limit | 1,687,708.34 | 1,617,375.71 | 62.00 |
| aggregate | 73.02 | 1,862,420.48 | 684.86 |
| group_aggregate | 42.04 | 199,029.54 | 514.15 |
| mixed | 154.30 | 157.23 | 104.95 |

### One million rows, permuted native before/after

| Case | Prior RAMSQL | Ordered-probe candidate |
| --- | ---: | ---: |
| primary_key_hot | 2,056,337.04 | 2,141,946.61 |
| filtered_limit | 47.53 | 23,304.91 |
| sort_limit | 62.60 | 74.19 |
| aggregate | 67.32 | 1,858,882.24 |
| group_aggregate | 41.88 | 189,463.13 |
| mixed | 108.75 | 275.50 |

The current ordered native sort is about 4% slower than the prior binary in this series; the 512-row mixed case is about 7% slower. Earlier small-case comparisons were closer/tied. These remain visible tradeoffs, not hidden by aggregate improvements. The permuted filter improves about 490×; scalar/group gains come from exact maintained totals. Neither claim covers arbitrary predicates, group keys, snapshots or workloads.

## PostgreSQL wire comparisons

PostgreSQL **16.15**, psycopg simple query, loopback request/fetch/independent digest included. PostgreSQL uses analyzed unlogged tables with synchronous_commit off; RAMSQL uses --memory. No TLS/SCRAM or durable flush cost is included. Every table has two alternating engine cycles and ten checked trials per case. Each case is calibrated with a 200 ms target; ranges are in raw JSON. These comparisons do not disable PostgreSQL parallelism or reproduce the unavailable reviewer harness.

### 512 rows, ordered — median wire ops/s

| Case | RAMSQL | PostgreSQL |
| --- | ---: | ---: |
| primary_key_hot | 15,134.06 | 12,147.80 |
| filtered_limit | 9,956.74 | 7,827.58 |
| sort_limit | 8,733.80 | 6,032.96 |
| aggregate | 14,953.15 | 10,589.64 |
| group_aggregate | 2,716.76 | 2,355.57 |
| mixed | 11,846.18 | 8,240.40 |

### 512 rows, permuted — median wire ops/s

| Case | RAMSQL | PostgreSQL |
| --- | ---: | ---: |
| primary_key_hot | 15,008.55 | 12,551.37 |
| filtered_limit | 9,904.27 | 8,044.00 |
| sort_limit | 8,218.59 | 6,112.68 |
| aggregate | 14,928.46 | 10,398.07 |
| group_aggregate | 2,670.52 | 2,351.32 |
| mixed | 11,467.14 | 8,784.51 |

### 1,000,000 rows, ordered — median wire ops/s

| Case | RAMSQL | PostgreSQL |
| --- | ---: | ---: |
| primary_key_hot | 15,469.78 | 12,199.88 |
| filtered_limit | 7,861.16 | 5,532.32 |
| sort_limit | 8,743.02 | 26.07 |
| aggregate | 14,879.21 | 65.80 |
| group_aggregate | 2,667.46 | 34.33 |
| mixed | 81.94 | 103.76 |

### 1,000,000 rows, permuted — median wire ops/s

| Case | RAMSQL | PostgreSQL |
| --- | ---: | ---: |
| primary_key_hot | 15,064.75 | 12,099.28 |
| filtered_limit | 5,430.61 | 3,534.39 |
| sort_limit | 43.00 | 40.42 |
| aggregate | 15,207.82 | 68.89 |
| group_aggregate | 2,733.99 | 35.85 |
| mixed | 172.11 | 151.23 |

All read-case medians favor RAMSQL in these four comparisons. This is finite-fixture evidence, not a guarantee. The million-row permuted sort (43 vs 40 ops/s) has overlapping ranges and is not a decisive win. The ordered million-row mixed read/write case still loses (82 vs 104 ops/s). Mixed counts four statements per operation cycle, as in prior packs. PostgreSQL versions, indexes and workloads beyond this fixture still require measurement.

## Costs and CPU attribution

In `probes-1000000-permuted`, median import time across 12 measured seed invocations rises from 690.3 to 734.3 ms (about 6.4%). This includes transaction publication/index/summary maintenance but no durable flush. The four-column native Database metadata grows from 409,496 to 463,408 bytes (+53,912); external backing is unchanged at 537,165,824 bytes for one million rows. These counters are not total process RSS and do not resolve compact-value or per-table reservation gaps. At 512 rows the same series family records 358.7 to 382.1 microseconds seed time and unchanged 331,776-byte backing.

Stage isolation: scalar summary changes about 70 to 1.64 million ops/s; dense grouping changes about 39 to 66 ops/s before group summaries; group summaries remove the data scan for eligible 64-group reads. Consult raw stage series for timings, load/metadata fields and counter evidence.

Checked native user-CPU profiles use software cpu-clock:u at 499 Hz, after an acknowledged seed barrier. Hardware counters were unavailable. No hardware-cache/bandwidth claim is made. Profiles retain zero lost samples, compact/full flat reports and compressed callgraphs; full perf.data remains under `/home/sb/.cache/ramsql-profiling/read-20261026/`. Scalar summaries now spend sampled CPU in parsing/binding/copies. Group summaries spend it projecting/sorting the 64 output groups. Permuted sort remains 99.83% in integerTopK; mixed is 98.04% in the same scan operator. Those sampled proportions include inlined work and are not separately measured function times. This identifies the next structural target: maintained general ordered access and compact scans.

The owned 10,000-point-query syscall trace includes startup, 64-row seed, connection and shutdown. Before: 53,312 total calls, 13,257 accept4, 10,002 clock_gettime. After: 32,108 total, 2,056 accept4, zero clock_gettime syscalls. Execution-time CancelRequest polling remains; reactor accepts follow readiness. The two trace wall times include tracing overhead and are not speed measurements. `profile_server_syscalls.py` reproduces the diagnostic without attaching to unrelated processes.

## Correctness and qualification

New ZPM ReleaseSafe independent models: scalar summaries 4,136 assertions; dense grouping 610; rollups 6,409; ordered probes 188. Tests cover NULLs/extremes, own/pending writes, older snapshots, same-snapshot resume, failed flush before publication, cancellation atomicity, WAL replay/copy, representative ownership, sparse keys, budget fallback, aborted outliers, and empty-table re-enable. Existing index/query/MVCC/type/change-log/stream/top-K/order contracts remain in the graph.

Final integration live Linux checks passed CLI; TCP lifecycle (3,996 assertions); psycopg/PyMySQL/python-tds; authentication (8,506 assertions); persistence (58 cases, 1,757 assertions, five crash cycles). Full local ReleaseSafe build/test passed (67 successful steps, zero failures). Runtime-capacity checks passed 2,048 rows, durable restart, offline restore and fail-closed small-budget recovery. Durable+SCRAM PostgreSQL 16 differential probes matched 37/37; all logs/results are retained. The finite differential suite does not establish full PostgreSQL parity.

[ZPM database platforms run 40325218516](https://github.com/SB0LTD/zpm/actions/runs/40325218516) passed Linux/macOS/Windows at 28036d8. Its legacy root workflow retains four historical unrelated failures: SABR loop, SABR manifest, TLS client and TTS GPU; repository-wide CI is not green. RAMSQL platform/release qualification is tracked on the linked PR checks. Native SB0X/SB0K ABI/QEMU fixtures remain distinct from real OS networking deployment claims.

`probes-512-permuted` retains a failed benchmark-validator attempt: the checker incorrectly rejected one proven non-NULL sort key on a fallback filtered scan. The checker was corrected and tested, and the successful rerun is `probes-512-permuted-v2`; no product correctness failure is concealed. All other superseded stage series and pre-I/O wire comparisons remain available.

## Next work

1. General secondary/composite ordered access with MVCC, rollback/replay and NULL/uniqueness rules. Current dense PK probes and physical-order proofs do not cover arbitrary orderings.
2. Compact typed/variable storage and block/column scan access; integerTopK still dominates shuffled/mutated-key scans.
3. Fair multi-client read/write and synchronous durable tests, p95/p99, larger datasets and independent benchmark review.
4. PostgreSQL semantic/catalog/extension compatibility, byte-budgeted results/sessions, compaction/checkpoints and real native OS deployment gates remain open.

## Reproducing an iteration

Build the pinned graph with `sig build ramsql ramsql-review-bench -Doptimize=ReleaseFast -j1` using the matched compiler/library. Use an owned PostgreSQL installation and the documented independent clients. Output directories must be fresh:

```sh
python tools/compare_native.py --binary baseline=/path/to/prior/ramsql-review-bench --binary candidate=/path/to/current/ramsql-review-bench --rows 1000000 --cycles 2 --minimum-ms 200 --permuted --output /path/to/new-native-results
python tools/compare_wire.py --ramsql /path/to/current/ramsql --postgres-bin /usr/lib/postgresql/16/bin --rows 1000000 --cycles 2 --minimum-ms 200 --permuted --output /path/to/new-wire-results
python tools/profile_server_syscalls.py --binary /path/to/current/ramsql --strace /path/to/strace --iterations 10000 --output /path/to/new-syscalls
```

Remove `--permuted` for ordered insertion; repeat at 512 rows. Run builds, CPU profiling and unprofiled throughput sequentially. IRDB adapter sources/locked package and the `irdb_measure` duration-calibrated driver are in `tools/`. Retain unsupported/incorrect results rather than excluding cases.

## Native portability correction after the throughput runs

The first RAMSQL release qualification (40328158997) found the pinned native AArch64 compiler rejecting 128-bit `@min/@max` in key interval restrictions (`too big min i128`). Hosted CI passed, but that release matrix failed; it is not counted as a qualified release. ZPM follow-up `af13ce23a975bfea3681bdec84000a0d3f19a5e9` expresses the same narrowing with explicit signed comparisons. The final integration archive SHA-256 is `e7e45587575c28675d6738771a50bb7dc05740b9fd7b752c899b0be1b9cedf11`.

The existing ReleaseSafe ordered-probe model still passes 188 assertions. Both SB0X and SB0K now build and pass the strengthened local QEMU protocol/SQL tests with the exact downloaded CI compiler (SHA-256 `742f9ea6e62ec02a1412c77357a48341dc98f84a4a9f23f0f31cefa73e913b32`, source 90a898d). New tests use shuffled dense BIGINT keys, ascending/descending and flipped constraints, and empty ranges beyond both i64 endpoints. Native reports, image metadata, toolchain provenance and the initial failed CI log are retained in `native-portability-fix/`.

The throughput tables above remain measurements of 28036d8, not retrospective measurements of the final corrected pin. The final integration must pass the rerun platform/release artifact checks on RAMSQL PR 11; no failed or pending native check is treated as passed. Native QEMU fixtures remain trusted, memory-only transports and do not prove native TCP, storage, authentication or full-OS deployment.
