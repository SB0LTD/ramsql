# IRDB / RAMSQL / PostgreSQL benchmark

Native API and server/client costs are separate tiers. All values are median operations/second over five trials.

## 512 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,547,726 | 2,674 | 8,632 | 9,422 |
| filtered_limit | 66,550 | 2,840 | 5,807 | 6,351 |
| sort_limit | 32,326 | 14,390 | 4,619 | 4,657 |
| aggregate | 148,718 | 1,454 | 8,295 | 7,836 |
| group_aggregate | 40,923 | 1,189 | 1,895 | 1,662 |
| mixed | 80,108 | 3,137 | 8,278 | 4,914 |

## 100,000 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,883,878 | 2,949 | 10,577 | 9,456 |
| filtered_limit | 462 | 796 | 290 | 4,998 |
| sort_limit | 169 | 517 | 114 | 92 |
| aggregate | 844 | 1,141 | 482 | 278 |
| group_aggregate | 328 | 865 | 182 | 130 |
| mixed | 501 | 655 | 348 | 358 |

## 1,000,000 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,211,681 | 4,578 | 12,230 | 7,479 |
| filtered_limit | 48 | 209 | 30 | 4,118 |
| sort_limit | 18 | 53 | 11 | 23 |
| aggregate | 78 | 488 | 36 | 59 |
| group_aggregate | 35 | 436 | 20 | 22 |
| mixed | 52 | 53 | 33 | 92 |

## Scope and reproducibility

- Fixed hot-key and deterministic SQL-subset workloads; this does not reproduce the unavailable reviewer scripts.
- Native API and server/client boundaries are separate comparisons. No cross-boundary winner is claimed.
- Single client, volatile memory tier. PostgreSQL uses an unlogged table and synchronous_commit=off. Durable benchmarks remain a separate experiment.
- Failed or capacity-blocked cases remain visible and have no throughput score.
- Five trials, ten untimed warmups per case; result values consumed and checked against an independent model.
- No performance threshold determines correctness. These results do not establish production readiness or comprehensive PostgreSQL parity.
