## Live sales-subset — qualification

Evidence: https://github.com/benor1470/dbMeter/actions/runs/40982373535/attempts/1

**NOT QUALIFIED — keep the PR unmerged; no README performance claim.**

All rates are queries/transactions per second at the original JDBC boundary. Bold ratios highlight at least 2× measured leads, including limited leads that do not pass the full gate.

| Workload / clients | RAMSQL | PostgreSQL | IRDB | RAMSQL / fastest reference |
| --- | ---: | ---: | ---: | ---: |
| mixed/1 | 61.98 | 4.53 | 228.31 | 0.27× |
| mixed/8 | 155.35 | 7.82 | 322.24 | 0.48× |

Gate: all original checks, all declared cases, all three engines, three 60-second trials; at least 2× the faster reference in each case, separated trial ranges, and median p99 no more than 10% worse than either reference. Smoke results never qualify.

