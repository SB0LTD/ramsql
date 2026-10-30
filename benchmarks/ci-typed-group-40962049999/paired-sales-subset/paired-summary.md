# Same-runner RAMSQL A/B diagnostic

**Not eligible for the all-engine advantage gate or a general performance claim.**

https://github.com/benor1470/dbMeter/actions/runs/40962049999/attempts/1

| Original case / clients | Baseline QPS median | Candidate QPS median | Paired candidate/baseline ratios |
| --- | ---: | ---: | --- |
| mixed/1 | 11.76 | 15.40 | 1.578, 1.079, 1.669 |
| mixed/8 | 39.48 | 48.26 | 1.358, 1.012, 1.338 |
| s3_top_products/1 | 6.42 | 117.27 | 18.217, 18.486, 18.296 |
| s3_top_products/8 | 28.18 | 261.65 | 9.281, 9.343, 9.230 |

AB / BA / AB, same runner and library/toolchain, fresh RAMSQL load and original full checks per variant/pair, fresh JVM per case, original 5s ramp / 15s warmup / 60s measure. Every trial, p99, source/binary checksum and host pressure snapshot is retained. No post-hoc subset is substituted.

