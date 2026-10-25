# Streaming aggregation: RAMSQL / IRDB / PostgreSQL

25 October 2026. **Development evidence; production and universal performance targets remain unmet.**

Sig 0.6.4 ReleaseFast; ZPM `de897ae55a0e5678c7d8811b161457fefc2f7cc9`. IRDB `InMemory.Net.irdb 1.10.821` was rechecked against the live NuGet feed, with the same pinned package hash, compiled table and clustered id index. PostgreSQL is native version 16. Every selected case now passes its independent correctness model; this is a finite SQL/workload set, not full database compatibility.

Five trials per case and ten warmups; values are median SQL operations/second. Compare only within each table. The native tier excludes client/network costs; the wire tier uses the same psycopg simple-query client. Volatile-memory comparisons only: PostgreSQL uses UNLOGGED and synchronous_commit=off. Durable correctness is tested separately; these timings do not include synchronous commit barriers.

## 512 rows

### Native public API

| Workload | RAMSQL | IRDB |
| --- | ---: | ---: |
| primary_key_hot | 1,547,726 | 2,674 |
| filtered_limit | 66,550 | 2,840 |
| sort_limit | 32,326 | 14,390 |
| aggregate | 148,718 | 1,454 |
| group_aggregate | 40,923 | 1,189 |
| mixed | 80,108 | 3,137 |

### Server/client boundary

| Workload | RAMSQL | PostgreSQL 16 |
| --- | ---: | ---: |
| primary_key_hot | 8,632 | 9,422 |
| filtered_limit | 5,807 | 6,351 |
| sort_limit | 4,619 | 4,657 |
| aggregate | 8,295 | 7,836 |
| group_aggregate | 1,895 | 1,662 |
| mixed | 8,278 | 4,914 |

## 100,000 rows

### Native public API

| Workload | RAMSQL | IRDB |
| --- | ---: | ---: |
| primary_key_hot | 1,883,878 | 2,949 |
| filtered_limit | 462 | 796 |
| sort_limit | 169 | 517 |
| aggregate | 844 | 1,141 |
| group_aggregate | 328 | 865 |
| mixed | 501 | 655 |

### Server/client boundary

| Workload | RAMSQL | PostgreSQL 16 |
| --- | ---: | ---: |
| primary_key_hot | 10,577 | 9,456 |
| filtered_limit | 290 | 4,998 |
| sort_limit | 114 | 92 |
| aggregate | 482 | 278 |
| group_aggregate | 182 | 130 |
| mixed | 348 | 358 |

## 1,000,000 rows

### Native public API

| Workload | RAMSQL | IRDB |
| --- | ---: | ---: |
| primary_key_hot | 1,211,681 | 4,578 |
| filtered_limit | 48 | 209 |
| sort_limit | 18 | 53 |
| aggregate | 78 | 488 |
| group_aggregate | 35 | 436 |
| mixed | 52 | 53 |

### Server/client boundary

| Workload | RAMSQL | PostgreSQL 16 |
| --- | ---: | ---: |
| primary_key_hot | 12,230 | 7,479 |
| filtered_limit | 30 | 4,118 |
| sort_limit | 11 | 23 |
| aggregate | 36 | 59 |
| group_aggregate | 20 | 22 |
| mixed | 33 | 92 |

## Fresh native before/after

The preserved pre-change binary was rerun immediately before this comparison, and its SHA-256 was checked against the earlier review pack. Source/binary identity, environment and raw samples are retained in before/. These sequential exploratory runs are not randomized repetitions or a statistical significance claim. Unchanged cases also vary; small differences should not be attributed to this patch.

| Rows | Scalar aggregate before | After | 64-group aggregate before | After |
| ---: | ---: | ---: | --- | ---: |
| 512 | 87,036 | 148,718 | 29,441 | 40,923 |
| 100,000 | 402 | 844 | WorkspaceCapacity | 328 |
| 1,000,000 | 36 | 78 | WorkspaceCapacity | 35 |

## Memory and remaining bottlenecks

- At every tested input size, the native 64-group query records **512 group-state Cells and 1,460 group reference slots**, while examined rows grow from 512 to 1,000,000. Cells are 48 bytes in this benchmark profile. These counters exclude projected rows, final sorting, bounded local temporaries and general database/session storage; they are not total statement/process memory.
- The million-row native backing remains **537,165,824 bytes**, with **407,928 inline database metadata bytes**. Row versions still use wide tagged Cells; this patch does not establish compact-storage or resident-memory superiority.
- Million-row native scalar aggregation improves about 2.2x in this fresh comparison, but IRDB still leads (488 versus 78 ops/s). Grouped aggregation is supported now, but IRDB leads 436 versus 35 ops/s. Removing the capacity failure is not the complete efficiency target.
- PostgreSQL still leads the million-row filtered ordered read by a large margin (4,118 versus 30 wire ops/s). Ordered/composite indexes and physical planning remain open. Large sorting also remains slower.
- The 512-row wire results include losses and near ties; these measurements do not support an all-workload superiority claim. Mixed workloads, random/missing keys, skew, concurrency, durability and larger SQL coverage need broader qualification.
- DISTINCT aggregates, joins/derived sources and some unordered lazy LIMIT plans still materialize input. Text/schema/session ceilings, compact variable storage, resumable scheduling and durable commit/checkpoint infrastructure remain open.

## Provenance and reproduction

- [Raw results and checksums](results.json), [native/IRDB stdout](native/), [fresh pre-change native run](before/results.json), and [source/dependency state](SOURCE_STATE.json). Original failed grouped measurements remain in the earlier review pack.
- [Runner and adapter instructions](../../tools/irdb_bench/README.md); use tools/benchmark_review.py with the exact immutable dependency source path. Run each engine sequentially without builds or other benchmark load.
- Hardware: i9-14900HX under WSL2, as recorded in results.json. Reserved backing, physical residency and vendor process working set are different measurements.
- [Implementation and qualification status](../../compatibility/INFRASTRUCTURE_PROGRESS.md). Full production gates remain blocked.
