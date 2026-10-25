# Capacity-safe streaming aggregation: final pinned comparison

25 October 2026. **Development evidence; the complete production/performance target remains open.**

This is the budget-safe follow-up using ZPM `d99db721beb3ca4f43d7941685a6973f0e608884` and Sig 0.6.4 ReleaseFast. It adds safe fallback for high-cardinality state pressure and conservatively avoids retrying function/subquery plans. The 1,197-assertion contract includes a 350-group/seven-aggregate capacity regression and a sequence predicate proving one volatile evaluation per source row.

IRDB remains pinned to InMemory.Net.irdb 1.10.821 (compiled table and clustered id index); PostgreSQL is native 16. Five trials and ten warmups per case; every selected answer is checked against an independent model. Numbers are median SQL operations/second. **Compare only within each table.** Native excludes client/network costs; wire includes psycopg simple-query/fetch/checking. These are volatile-memory tiers, not synchronous durable-commit comparisons.

## 512 rows

### Native public API

| Workload | RAMSQL | IRDB |
| --- | ---: | ---: |
| primary_key_hot | 1,813,702 | 3,116 |
| filtered_limit | 80,884 | 3,679 |
| sort_limit | 33,888 | 18,068 |
| aggregate | 144,242 | 1,338 |
| group_aggregate | 45,968 | 1,245 |
| mixed | 93,192 | 3,481 |

### Server/client boundary

| Workload | RAMSQL | PostgreSQL 16 |
| --- | ---: | ---: |
| primary_key_hot | 11,727 | 8,353 |
| filtered_limit | 7,593 | 5,785 |
| sort_limit | 6,031 | 4,307 |
| aggregate | 11,049 | 7,991 |
| group_aggregate | 2,266 | 1,990 |
| mixed | 7,993 | 6,645 |

## 100,000 rows

### Native public API

| Workload | RAMSQL | IRDB |
| --- | ---: | ---: |
| primary_key_hot | 1,728,310 | 2,990 |
| filtered_limit | 428 | 993 |
| sort_limit | 166 | 527 |
| aggregate | 816 | 1,252 |
| group_aggregate | 325 | 932 |
| mixed | 533 | 633 |

### Server/client boundary

| Workload | RAMSQL | PostgreSQL 16 |
| --- | ---: | ---: |
| primary_key_hot | 11,872 | 10,028 |
| filtered_limit | 228 | 4,592 |
| sort_limit | 104 | 96 |
| aggregate | 422 | 267 |
| group_aggregate | 236 | 126 |
| mixed | 352 | 349 |

## 1,000,000 rows

### Native public API

| Workload | RAMSQL | IRDB |
| --- | ---: | ---: |
| primary_key_hot | 937,119 | 4,627 |
| filtered_limit | 35 | 155 |
| sort_limit | 16 | 59 |
| aggregate | 72 | 542 |
| group_aggregate | 31 | 478 |
| mixed | 47 | 80 |

### Server/client boundary

| Workload | RAMSQL | PostgreSQL 16 |
| --- | ---: | ---: |
| primary_key_hot | 12,157 | 6,301 |
| filtered_limit | 31 | 3,721 |
| sort_limit | 13 | 23 |
| aggregate | 34 | 41 |
| group_aggregate | 23 | 30 |
| mixed | 36 | 84 |

## Fresh native before/after

The preserved pre-streaming binary was freshly rerun immediately before this comparison and checked against its archived SHA-256. All measurements remain available, including the [initial streaming capture](../streaming-aggregation-20261025/REPORT.md). Sequential exploratory runs are not randomized trials or proof of statistical significance; unchanged cases also vary. Do not select the highest run as a guaranteed speedup.

| Rows | Scalar aggregate before | After | 64-group aggregate before | After |
| ---: | ---: | ---: | --- | ---: |
| 512 | 78,104 | 144,242 | 23,292 | 45,968 |
| 100,000 | 390 | 816 | WorkspaceCapacity | 325 |
| 1,000,000 | 39 | 72 | WorkspaceCapacity | 31 |

## What this establishes

- All six selected cases pass at 512/100K/1M rows in every tested tier. Large grouped aggregation no longer fails solely because input keys/handles are materialized per row on its eligible path.
- The million-row native COUNT/SUM case rises from 39 to 72 ops/s (about 1.85x in this capture). The 64-group case changes from WorkspaceCapacity to 31 ops/s. Earlier captures show different rates and remain retained.
- Group scratch is **512 Cells and 1,460 reference slots** at all three input sizes. These counters exclude projected output, final sorting and bounded local temporaries. The benchmark Cell is 48 bytes; the million-row backing is still **537,165,824 bytes**, plus **407,928 inline database metadata bytes**. This does not establish compact-storage or resident-memory superiority.
- IRDB still leads million-row scalar/group aggregation at **542/478 ops/s** versus **72/31**. PostgreSQL still leads filtered ordered wire reads at **3,721** versus **31** ops/s and sorting at **23** versus **13**. Production and all-benchmark efficiency requirements are unmet.
- Many SQL shapes still materialize: DISTINCT aggregates, function/subquery plans, derived/join inputs and some unordered lazy grouped LIMIT. Arbitrary value/schema/session bounds, compact typed pages, ordered indexes, resumable scheduling, scalable sessions and durable commit/checkpoint infrastructure remain open.

## Evidence and scope

- [Complete results/source hashes](results.json), [native/IRDB stdout](native/), [fresh old-binary baseline](before/results.json), [source/archive state](SOURCE_STATE.json), and [same-binary PG16 durable+SCRAM differential report](qualification/postgres16-durable.json).
- [Implementation/qualification status](../../compatibility/INFRASTRUCTURE_PROGRESS.md), [reproduction commands](../../tools/irdb_bench/README.md). Preserve the immutable dependency source path when recording hashes.
- Same i9-14900HX/WSL2 environment as recorded in results.json. Engines run sequentially without local builds or other validation. The workload uses a fixed hot key and deterministic data; random/missing keys, skew, wide values, concurrent clients and durable performance need broader experiments.
- PostgreSQL uses UNLOGGED and synchronous_commit=off for these timings. Native API and wire are separate boundaries. This is not the unavailable alpha.2 reviewer suite, full PostgreSQL equivalence, real SB0 OS deployment evidence, or a production-ready release.
