# Typed grouped aggregation on the main configuration: CI evidence

Source: [dbMeter run 40976650837, attempt 1](https://github.com/benor1470/dbMeter/actions/runs/40976650837/attempts/1).
These are downloaded CI results, not local throughput measurements.

## Matched comparison

Same runner, AB / BA / AB, fresh original 1M data and all seven checks per variant/pair.
Each original case retains its 5s ramp, 15s warmup and 60s measured window with a fresh JVM.
The historical validator checks every one of the 24 raw reports, six check XMLs,
binary provenance, shared dependency/toolchain/build arguments and scheduled order.

- dbMeter revision: `b5fed653f4ad26161502a1b92da6c6faaa06fc6b`.
- Baseline: `6c41b641a1f73bc6c5b47baec99af81196fb36fe`; binary SHA-256 `f7affe9a9f816f349669cce0db76f4078dc7d4537a77da96918a074aff1a9ac5`.
- Candidate: `82441c6ee45793a0865b1bc6b49780371371810f`; binary SHA-256 `0773ba6028de9ec7c812102863a68a8e70939f8a21fbe60e7b7e0626c4eda1d8`.

| Original case / clients | Baseline median QPS | Candidate median QPS | Paired candidate/baseline ratios |
| --- | ---: | ---: | --- |
| mixed/1 | 13.81 | 18.64 | 1.257, 1.579, 1.478 |
| mixed/8 | 40.25 | 59.39 | 1.479, 1.522, 1.442 |
| s3_top_products/1 | 7.40 | 116.81 | 15.158, 15.749, 16.272 |
| s3_top_products/8 | 28.66 | 283.93 | 9.773, 9.906, 9.892 |

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
