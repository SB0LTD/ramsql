# Same-runner RAMSQL A/B diagnostic

**Not eligible for the all-engine advantage gate or a general performance claim.**

https://github.com/benor1470/dbMeter/actions/runs/40976650837/attempts/1

| Original case / clients | Baseline QPS median | Candidate QPS median | Paired candidate/baseline ratios |
| --- | ---: | ---: | --- |
| mixed/1 | 13.81 | 18.64 | 1.257, 1.579, 1.478 |
| mixed/8 | 40.25 | 59.39 | 1.479, 1.522, 1.442 |
| s3_top_products/1 | 7.40 | 116.81 | 15.158, 15.749, 16.272 |
| s3_top_products/8 | 28.66 | 283.93 | 9.773, 9.906, 9.892 |

AB / BA / AB, same runner and library/toolchain, fresh RAMSQL load and original full checks per variant/pair, fresh JVM per case, original 5s ramp / 15s warmup / 60s measure. Every trial, p99, source/binary checksum and host pressure snapshot is retained. No post-hoc subset is substituted.

