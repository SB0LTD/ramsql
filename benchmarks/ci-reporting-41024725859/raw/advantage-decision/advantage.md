# Advantage gate failed — do not merge or claim a win

## Live size-scaling — qualification

Evidence: https://github.com/benor1470/dbMeter/actions/runs/41024725859/attempts/1

**NOT QUALIFIED — keep the PR unmerged; no README performance claim.**

All rates are queries/transactions per second at the original JDBC boundary. Bold ratios highlight at least 2× measured leads, including limited leads that do not pass the full gate.

| Workload / clients | RAMSQL | PostgreSQL | IRDB | RAMSQL / fastest reference |
| --- | ---: | ---: | ---: | ---: |
| s1000000_filter/1 | 1,142.08 | 51.47 | 930.67 | 1.23× |
| s1000000_filter/8 | 2,246.52 | 71.16 | 1,387.85 | 1.62× |
| s1000000_pk/1 | 31,456.41 | 29,119.00 | 1,120.01 | 1.08× |
| s1000000_pk/8 | 152,893.50 | 133,904.90 | 1,757.09 | 1.14× |
| s1000000_topn/1 | 62.24 | 26.51 | 15.98 | **2.35×** |
| s1000000_topn/8 | 231.94 | 35.45 | 45.30 | **5.12×** |
| s100000_filter/1 | 9,207.98 | 471.78 | 2,731.03 | **3.37×** |
| s100000_filter/8 | 25,840.25 | 1,014.38 | 5,390.36 | **4.79×** |
| s100000_pk/1 | 31,195.92 | 30,284.62 | 2,452.33 | 1.03× |
| s100000_pk/8 | 149,341.45 | 141,442.38 | 4,691.71 | 1.06× |
| s100000_topn/1 | 1,146.85 | 190.79 | 193.15 | **5.94×** |
| s100000_topn/8 | 3,606.95 | 422.72 | 505.52 | **7.14×** |

Gate: all original checks, all declared cases, all three engines, three 60-second trials; at least 2× the faster reference in each case, separated trial ranges, and median p99 no more than 10% worse than either reference. Smoke results never qualify.

Gaps and invalid/missing evidence:

- Original RAMSQL capability checks did not pass
- Original RAMSQL text/column/table capability probes failed; valid read timings do not close those gaps
- RAMSQL capability evidence: Original correctness cases failed or were skipped

## Live sales-subset — qualification

Evidence: https://github.com/benor1470/dbMeter/actions/runs/41024725859/attempts/1

**NOT QUALIFIED — keep the PR unmerged; no README performance claim.**

All rates are queries/transactions per second at the original JDBC boundary. Bold ratios highlight at least 2× measured leads, including limited leads that do not pass the full gate.

| Workload / clients | RAMSQL | PostgreSQL | IRDB | RAMSQL / fastest reference |
| --- | ---: | ---: | ---: | ---: |
| mixed/1 | 46.78 | 3.56 | 175.01 | 0.27× |
| mixed/8 | 116.24 | 5.92 | 242.91 | 0.48× |

Gate: all original checks, all declared cases, all three engines, three 60-second trials; at least 2× the faster reference in each case, separated trial ranges, and median p99 no more than 10% worse than either reference. Smoke results never qualify.


## Live sales-full — qualification

Evidence: https://github.com/benor1470/dbMeter/actions/runs/41024725859/attempts/1

**NOT QUALIFIED — keep the PR unmerged; no README performance claim.**

All rates are queries/transactions per second at the original JDBC boundary. Bold ratios highlight at least 2× measured leads, including limited leads that do not pass the full gate.

| Workload / clients | RAMSQL | PostgreSQL | IRDB | RAMSQL / fastest reference |
| --- | ---: | ---: | ---: | ---: |
| mixed/1 | 0.93 | not scored | 34.96 | unqualified |
| mixed/8 | 1.96 | 0.72 | 44.91 | 0.04× |

Gate: all original checks, all declared cases, all three engines, three 60-second trials; at least 2× the faster reference in each case, separated trial ranges, and median p99 no more than 10% worse than either reference. Smoke results never qualify.

Gaps and invalid/missing evidence:

- ('postgres', 'mixed/1', 1): No successful execution of declared scenario q5_high_value_customers
- mixed/1: missing valid postgres trials

