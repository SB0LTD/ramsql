# IRDB / RAMSQL / PostgreSQL benchmark

Native API and server/client costs are separate tiers. All values are median operations/second over five trials.

## 512 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,813,702 | 3,116 | 11,727 | 8,353 |
| filtered_limit | 80,884 | 3,679 | 7,593 | 5,785 |
| sort_limit | 33,888 | 18,068 | 6,031 | 4,307 |
| aggregate | 144,242 | 1,338 | 11,049 | 7,991 |
| group_aggregate | 45,968 | 1,245 | 2,266 | 1,990 |
| mixed | 93,192 | 3,481 | 7,993 | 6,645 |

## 100,000 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,728,310 | 2,990 | 11,872 | 10,028 |
| filtered_limit | 428 | 993 | 228 | 4,592 |
| sort_limit | 166 | 527 | 104 | 96 |
| aggregate | 816 | 1,252 | 422 | 267 |
| group_aggregate | 325 | 932 | 236 | 126 |
| mixed | 533 | 633 | 352 | 349 |

## 1,000,000 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 937,119 | 4,627 | 12,157 | 6,301 |
| filtered_limit | 35 | 155 | 31 | 3,721 |
| sort_limit | 16 | 59 | 13 | 23 |
| aggregate | 72 | 542 | 34 | 41 |
| group_aggregate | 31 | 478 | 23 | 30 |
| mixed | 47 | 80 | 36 | 84 |

## Scope and reproducibility

- Fixed hot-key and deterministic SQL-subset workloads; this does not reproduce the unavailable reviewer scripts.
- Native API and server/client boundaries are separate comparisons. No cross-boundary winner is claimed.
- Single client, volatile memory tier. PostgreSQL uses an unlogged table and synchronous_commit=off. Durable benchmarks remain a separate experiment.
- Failed or capacity-blocked cases remain visible and have no throughput score.
- Five trials, ten untimed warmups per case; result values consumed and checked against an independent model.
- No performance threshold determines correctness. These results do not establish production readiness or comprehensive PostgreSQL parity.
