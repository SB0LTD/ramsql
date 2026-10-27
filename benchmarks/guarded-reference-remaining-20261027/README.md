# Guarded-input IRDB comparison: remaining cases

This run completes the ten-case, 100,000-row native API matrix alongside the
[initial three cases](../guarded-reference-20261027/README.md). Same binaries,
schema, shuffled-key model, seed, nullable 96-byte payloads, 1,024 categories,
single worker, volatile execution, checked results and alternating engine order.
All seven cases passed correctness in both engines across ten trials each.

| Case | RAMSQL median ops/s | IRDB median ops/s | Ratio |
| --- | ---: | ---: | ---: |
| Uniform primary-key reads | 1,492,100 | 2,064 | 722.9x |
| Hotspot primary-key reads | 1,473,770 | 2,022 | 729.0x |
| Primary-key range, ordered LIMIT 32 | 356,793 | 537 | 664.3x |
| Value/id ORDER BY LIMIT 32 | 652 | 222 | 2.94x |
| Filtered grouped COUNT/SUM | 6,246 | 610 | 10.24x |
| 50% point reads / 50% updates | 1,435,260 | 325 | 4,411x |
| Delete / missing read / reinsert / present read | 8,634 | 87 | 99.3x |

These large point/mutation ratios need independent reference-adapter review.
Both adapters execute SQL strings; prepared queries are not compared. IRDB
uses its compiled imported table and explicitly built clustered index. The
experiment does not identify the cause of IRDB's per-call cost or establish
the fastest available IRDB configuration. The sorting ratio is the smallest
observed win in this finite matrix; it is not a confidence bound.

`results.json` retains individual trials, fixture/binary/compiler/source hashes,
commands and correctness records. Raw logs and source copies are retained.
Trace files remain local; regenerate them from the retained model and seed.
Timing includes result consumption and validation. Latency histogram values
are upper bounds, not exact quantiles. No builds or profilers ran during timing.

Reproduce using the initial run's command, replacing the cases and output:

```sh
  --case uniform_get --case hotspot_get --case range_page --case sort_score \
  --case filtered_group --case mixed_50_50 --case churn \
  --output /path/to/another/fresh/results
```

The complete ten-case matrix passes the harness's correctness and finite
speed screening rules. It does not qualify joins, PostgreSQL wire throughput,
durability, multiple clients, 100M-row physical SQL storage or all-core use.
Those remain open in the [capacity plan](../../compatibility/MACHINE_CAPACITY_AND_100M_PLAN.md).
