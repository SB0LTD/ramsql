# Same-runner RAMSQL A/B diagnostic

**Not eligible for the all-engine advantage gate or a general performance claim.**

https://github.com/benor1470/dbMeter/actions/runs/40982373535/attempts/1

| Original case / clients | Baseline QPS median | Candidate QPS median | Paired candidate/baseline ratios |
| --- | ---: | ---: | --- |
| mixed/1 | 25.44 | 61.69 | 2.334, 2.328, 2.591 |
| mixed/8 | 72.13 | 153.54 | 2.138, 2.095, 2.065 |
| s4_segment_discount/1 | 7.09 | 89.95 | 12.610, 12.700, 13.290 |
| s4_segment_discount/8 | 21.94 | 215.16 | 9.710, 9.932, 9.853 |

AB / BA / AB, same runner and library/toolchain, fresh RAMSQL load and original full checks per variant/pair, fresh JVM per case, original 5s ramp / 15s warmup / 60s measure. Every trial, p99, source/binary checksum and host pressure snapshot is retained. No post-hoc subset is substituted.

