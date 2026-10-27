# Concurrent-read qualification measurements

Native Windows ReleaseFast, Intel Core i9-14900HX (32 logical CPUs). All trials use 8,192 rows in memory, nonpipelined PostgreSQL simple queries, a separate client process per connection, and checked results. These compare reactor settings of the same binary; PostgreSQL and IRDB are not measured here.

Median of three trials; eight clients in every row below. CPU time is server process kernel plus user time per completed query.

| Workload | Workers | Queries/s | Server CPU µs/query |
| --- | ---: | ---: | ---: |
| PK lookup | 1 | 67,482 | 14.45 |
| PK lookup | 4 | 129,976 | 26.95 |
| PK lookup | 8 | 131,497 | 29.69 |
| Expression aggregate | 1 | 2,787 | 349.61 |
| Expression aggregate | 4 | 9,417 | 384.77 |
| Expression aggregate | 8 | 16,764 | 394.53 |
| Filtered aggregate | 1 | 2,165 | 439.45 |
| Filtered aggregate | 4 | 8,162 | 453.12 |
| Filtered aggregate | 8 | 13,786 | 492.19 |

Expression aggregate: `COUNT(*), SUM(value*value)`. Filtered aggregate: `COUNT(*), SUM(value) WHERE id % 7=3`. These require evaluation beyond the maintained whole-table scalar summaries. This dataset is small and cache-resident; it does not model a 100M-row database or prove a production reference-engine win.

PK trials use 5,000 operations/client and the default 4,096 workspace rows. Aggregate trials use 1,000 operations/client and explicit 8,192 workspace rows. Harness copies, binary SHA-256, individual trials and memory snapshots are retained in each sibling directory. The benchmark binary uses the initial per-client scratch ownership. The subsequent worker-owned scratch revision has its own memory evidence; these timing results must not be attributed to an unmeasured later binary.

## Native phase probe

The checked hot-key probe runs 10,000 queries/trial. Median legacy raw execution is about 0.59 µs/query; independent preparation and execution phases are about 0.70 and 0.34 µs/query. Phase clocks add overhead; the legacy batch includes result checks while phase intervals exclude them. This is a localization aid, not an exact overhead ratio or a substitute for a sampling profile. It excludes sockets, scheduling, concurrency and client work, and uses a repeated key on 512 seeded rows.

`native-phases.json` retains all three Windows trials plus binary/source hashes. The wire cost greatly exceeds this hot native lookup; future optimization must measure transport/scheduler time as well as parser/binder/executor time. More workers improve throughput here, but increase PK CPU/query.

## Correctness scope

Separate server tests prove simultaneous read execution, writer exclusion, cancellation, statement-timeout recovery and uncommitted visibility. Eight clients also check 192 three-way-join/CTE aggregate queries. Those are assertions, not benchmark results. Extended-protocol reads and transactions still use exclusive execution.

## Worker-owned scratch revision

`worker-scratch-reuse.json` measures the later ReleaseSafe server: all eight clients are connected, the first four warm every worker context, then the next four issue reads while all eight alternate time zones on typed timestamp output. Private commit growth must remain below half of one full context payload (with OS-bookkeeping allowance), rather than reserving another four contexts. This is a checked ownership/session-isolation test, not a throughput measurement. Contexts are released when their reactor has no active clients or stops.

The later ReleaseFast worker-owned revision has separate checked PK trials in [worker-scratch-wire-20261027](../worker-scratch-wire-20261027/results.json). Eight clients, 5,000 requests/client, median of three trials:

| Workers | Queries/s | Server CPU us/query |
| --- | ---: | ---: |
| 1 | 69,737 | 14.06 |
| 4 | 122,211 | 28.52 |
| 8 | 136,436 | 29.69 |
