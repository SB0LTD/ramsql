# Prepared lookup transport experiments

## Linux worker placement

In the unchanged one-million-row JDBC lookup, RamSQL emitted about one
successful send and one TCP data segment per operation at one and eight
clients. There were no additional non-data segments during either sample.
At one client, 40.2% of direct JDBC-to-server wakeups crossed physical cores.
Their median wake-to-run delay was 8.31 µs, compared with 1.48 µs across two
logical CPUs in the same physical core. At eight clients, the respective
p99s were 102.8 and 62.0 µs. The sole JDBC connection spent only 53.7% of
its receive-CPU observations on its most frequent physical core.
[Trace and unchanged JDBC reports](https://github.com/benor1470/dbMeter/actions/runs/44275023609)

The placement experiment used one ReleaseFast binary per runner. The
candidate pinned each worker inside its effective CPU mask and preferred
an eligible sibling of the socket's startup receive CPU. The live
qualification checked ordinary masks, actual pinning, same-core admission,
idle sleep, cancellation and socket fairness. Each case ran three original
60-second control/candidate pairs in AB/BA/AB order with the same original
JDBC queries, inputs and 256 MiB cache setting. All setup correctness and
capacity checks passed.
[Paired campaign and every original report](https://github.com/benor1470/dbMeter/actions/runs/44278879378)

| Original case | Control queries/s | Candidate queries/s | Median change | Median p99 µs | Paired candidate/control ratios |
| --- | ---: | ---: | ---: | --- | --- |
| 100K rows, 1 client | 40,520.30 | 37,891.12 | −6.49% | 50 → 51 | 0.935, 1.020, 0.916 |
| 100K rows, 8 clients | 209,233.82 | 212,568.18 | +1.59% | 94 → 88 | 1.043, 1.033, 0.987 |
| 1M rows, 1 client | 39,789.55 | 40,475.78 | +1.72% | 49 → 50 | 0.965, 1.021, 1.034 |
| 1M rows, 8 clients | 146,358.24 | 150,129.70 | +2.58% | 129 → 141 | 1.015, 1.041, 1.020 |

The 100K single-client case regressed and the 1M eight-client case had a
9.3% worse median p99. Worker placement remains an isolated experiment.

| Original case | Raw artifact | Archive SHA-256 |
| --- | --- | --- |
| 100K rows, 1 client | [14416762868](https://github.com/benor1470/dbMeter/actions/runs/44278879378/artifacts/14416762868) | `e7d78e056d6aaf1cb21ad1ecf79f8a07e158ad2b8187090d1b5da0c18b50f6e5` |
| 100K rows, 8 clients | [14416906301](https://github.com/benor1470/dbMeter/actions/runs/44278879378/artifacts/14416906301) | `2aa8a9898d14cdbe0570ce44863ff73f4bd9f3537bfbeb23b0a417a32169257d` |
| 1M rows, 1 client | [14415664310](https://github.com/benor1470/dbMeter/actions/runs/44278879378/artifacts/14415664310) | `506158c60019127d070171f7648d4e7dc78bd55c69a3591ab2438f4ece933971` |
| 1M rows, 8 clients | [14416568472](https://github.com/benor1470/dbMeter/actions/runs/44278879378/artifacts/14416568472) | `f27d74f461072fd8e3ea7ababbf487a89fd28742bb950481995b8f5f260fe641` |

## Direct receive

The current Linux reactor waits in poll, then calls recv to copy available
bytes. The opt-in direct receive experiment lets a worker with one idle,
authenticated PostgreSQL connection wait and read in the same kernel call.
The wait has a five-millisecond requested socket timeout, subject to kernel
timer granularity. Workers with multiple live sockets continue to poll.
Flush and all other reads remain nonblocking. The experiment targets the
extra readiness syscall and still incurs the TCP scheduler wakeup.
