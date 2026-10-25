# IRDB / RAMSQL / PostgreSQL benchmark

Native API and server/client costs are separate tiers. All values are median operations/second over five trials.

## 512 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,642,589 | 2,432 | 12,032 | 9,701 |
| filtered_limit | 72,587 | 2,576 | 8,249 | 6,706 |
| sort_limit | 29,787 | 10,168 | 5,929 | 4,912 |
| aggregate | 82,726 | 1,346 | 11,016 | 8,819 |
| group_aggregate | 26,109 | 1,149 | 2,154 | 1,996 |
| mixed | 74,430 | 2,892 | 8,968 | 7,321 |

## 100,000 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,511,944 | 2,286 | 13,671 | 9,459 |
| filtered_limit | 448 | 809 | 286 | 4,281 |
| sort_limit | 172 | 466 | 101 | 86 |
| aggregate | 390 | 1,076 | 182 | 254 |
| group_aggregate | FAILED: WorkspaceCapacity | 885 | FAILED: ProgramLimitExceeded: WorkspaceCapacity | 121 |
| mixed | 505 | 597 | 271 | 335 |

## 1,000,000 rows

| Workload | RAMSQL native API | IRDB native API | RAMSQL wire | PostgreSQL wire |
|---|---:|---:|---:|---:|
| primary_key_hot | 1,014,507 | 3,226 | 12,747 | 7,442 |
| filtered_limit | 42 | 190 | 32 | 4,423 |
| sort_limit | 17 | 55 | 12 | 24 |
| aggregate | 32 | 491 | 21 | 57 |
| group_aggregate | FAILED: WorkspaceCapacity | 435 | FAILED: ProgramLimitExceeded: WorkspaceCapacity | 33 |
| mixed | 51 | 48 | 34 | 93 |

## Scope and reproducibility

- Fixed hot-key and deterministic SQL-subset workloads; this does not reproduce the unavailable reviewer scripts.
- Native API and server/client boundaries are separate comparisons. No cross-boundary winner is claimed.
- Single client, volatile memory tier. PostgreSQL uses an unlogged table and synchronous_commit=off. Durable benchmarks remain a separate experiment.
- Failed or capacity-blocked cases remain visible and have no throughput score.
- Five trials, ten untimed warmups per case; result values consumed and checked against an independent model.
- No performance threshold determines correctness. These results do not establish production readiness or comprehensive PostgreSQL parity.
