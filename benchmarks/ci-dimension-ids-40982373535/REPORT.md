# Dimension group IDs: CI evidence

Source: [dbMeter run 40982373535, attempt 1](https://github.com/benor1470/dbMeter/actions/runs/40982373535/attempts/1).
These are downloaded CI results, not local throughput measurements.

## Matched comparison

Same runner, AB / BA / AB, fresh original 1M data and all seven checks per variant/pair.
Each original case retains its 5s ramp, 15s warmup and 60s measured window with a fresh JVM.
The historical validator checks every one of the 24 raw reports, six check XMLs,
binary provenance, shared dependency/toolchain/build arguments and scheduled order.

- dbMeter revision: `18a7a66c4a5de68d952da77e68097d278f06a1ab`.
- Baseline: `37035e65d36ace6d538544ac0b308b347caf1637`; binary SHA-256 `8a93f84eae8a450ce35e63da152e37bdd258d781fbbe37fe1efc937f5d23e856`.
- Candidate: `0ba7c35faaa2f2a30701769f49929a1542e316e6`; binary SHA-256 `59cb6ebec251e3133315c29a4dd523ea77b0639aa2b1701002fc92c8e9a095bf`.

| Original case / clients | Baseline median QPS | Candidate median QPS | Paired candidate/baseline ratios |
| --- | ---: | ---: | --- |
| mixed/1 | 25.44 | 61.69 | 2.334, 2.328, 2.591 |
| mixed/8 | 72.13 | 153.54 | 2.138, 2.095, 2.065 |
| s4_segment_discount/1 | 7.09 | 89.95 | 12.610, 12.700, 13.290 |
| s4_segment_discount/8 | 21.94 | 215.16 | 9.710, 9.932, 9.853 |

## Reference comparison remains behind IRDB

A separate workflow job runs the candidate and both reference engines. Its
runner is separate from the A/B job; these figures must not be pooled with it.
All original subset checks pass and all 18 timed cases are retained.

| Clients | RAMSQL median QPS | PostgreSQL median QPS | IRDB median QPS |
| --- | ---: | ---: | ---: |
| 1 | 61.98 | 4.53 | 228.31 |
| 8 | 155.35 | 7.82 | 322.24 |

RAMSQL remains slower than IRDB on this measured mixed workload. See the complete
[reference summary](live-sales-subset/summary.md) and raw individual trials.

## Limits of this evidence

These measurements qualify a bounded execution improvement on the recorded workload.
They do not establish full SQL compatibility, production readiness, 100M-row capacity,
all-workload leadership or the all-engine dbMeter merge gate. Do not pool runners or
attribute differences between separate workflow runs to a source change.

The original full 5M suite still has four SQL-feature failures (3/7 checks pass).
The dbMeter benchmark PR remains unmerged; no README advantage claim follows.

All downloaded JSON/XML/CSV reports in each available campaign are retained, including
individual trials, host observations and negative reference comparisons. Exact historical
validation scripts and pins accompany the results. No vendor driver, executable, loader
or source dataset is copied. `sha256.json` covers every retained file except itself.
