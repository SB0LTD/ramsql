# Reviewer wire PK investigation (Windows, 2026-10-27)

This is a checked RAMSQL-to-RAMSQL comparison, not a PostgreSQL or IRDB win.
Each connection has a separate Python process, 8,192 rows and 20,000
nonpipelined simple SELECTs per trial. Every returned id/value is checked.
Three trials per case; no local builds or profilers overlap the measurement.
All binaries are ReleaseFast. Raw timings, server kernel/user CPU, memory,
binary hashes and driver snapshots accompany this document.

Final blocking-lock candidate medians:

| Clients | Socket reactors | Checked queries/s | Server CPU cores used | Approximate server CPU/query |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1 | 26,712 | 0.48 | 18 microseconds |
| 4 | 1 | 67,453 | 0.99 | 15 microseconds |
| 4 | 4 | 91,210 | 1.94 | 21 microseconds |
| 8 | 1 | 69,397 | 0.98 | 14 microseconds |
| 8 | 4 | 126,598 | 3.41 | 27 microseconds |

The extra reactors raise eight-client throughput about **1.82x**, but cost
about **1.9x the server CPU per query**. This does not meet the efficiency goal.
`--workers 1` remains the default; additional reactors are experimental. SQL
execution remains serialized, with one database/workspace and no per-client
snapshots. These results justify further work, not a production/scaling claim.
Kernel CPU dominates this wire boundary; native-engine wins cannot be used to
explain away a wire/client regression.

`old-vs-runtime` compares beta.1 source with the runtime-capacity fixes before
workers. Both reach roughly 70k/s at 4–8 clients; the capacity fixes did not cure
the single-core bottleneck. The reviewer's 84k/52k/35k historical results cannot
be reproduced exactly without their scripts, compiler flags and dataset.

`experimental-busy-retry` retains the first worker attempt (eight-client median
132,876/s) rather than hiding it. Replacing repeated failed store-lock retries
with blocking acquisition did not materially improve CPU efficiency. The final
candidate avoids that busy retry, but still incurs substantial socket/locking
cost. No tail-latency distribution or comparative PostgreSQL measurement is
included; do not infer either from these totals.

`environment.json` identifies the final pre-commit binary, compiler, ZPM pin,
source hashes and base revision. `runtime-source.patch` reconstructs its runtime
changes on that base. Earlier developmental binary identities are in their raw
reports. These are development measurements, not qualification of a later
published artifact. The benchmark's `workers` array is client-process timing
records; `reactors` is the actual server socket-worker count.
