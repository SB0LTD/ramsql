# IRDB 1.10.821 / RAMSQL candidate / PostgreSQL benchmark

25 October 2026. **RAMSQL has not met the production or all-benchmark performance target.** This is an unreleased alpha.4 candidate, not the published alpha.3 binary.

IRDB 1.10.821 was selected from the live NuGet feed and pinned by package hash. Its table is compiled and its public clustered `id` index is built before timing. RAMSQL uses its primary-key hash index; PostgreSQL uses its primary-key B-tree. All timed results are consumed and checked against an independent model.

Five trials per case, ten warmups. Numbers are median SQL operations/second. The mixed workload executes one point lookup, one filtered ordered read, one top-K sort and one update per cycle. It counts four SQL operations per cycle. These are new, explicit workloads; the reviewer's unavailable scripts were not reproduced.

**Compare only within each table.** Native public-API timings exclude client/network costs. The server tables use the same psycopg simple-query client and include loopback, fetch and result-checking costs. Native timings cannot be compared directly to server timings.

## 512 rows

### Native public API

| Workload | RAMSQL ops/s | IRDB 1.10.821 ops/s |
|---|---:|---:|
| primary_key_hot | 1,642,589 | 2,432 |
| filtered_limit | 72,587 | 2,576 |
| sort_limit | 29,787 | 10,168 |
| aggregate | 82,726 | 1,346 |
| group_aggregate | 26,109 | 1,149 |
| mixed | 74,430 | 2,892 |

### Server/client boundary

| Workload | RAMSQL ops/s | PostgreSQL 16 ops/s |
|---|---:|---:|
| primary_key_hot | 12,032 | 9,701 |
| filtered_limit | 8,249 | 6,706 |
| sort_limit | 5,929 | 4,912 |
| aggregate | 11,016 | 8,819 |
| group_aggregate | 2,154 | 1,996 |
| mixed | 8,968 | 7,321 |

## 100,000 rows

### Native public API

| Workload | RAMSQL ops/s | IRDB 1.10.821 ops/s |
|---|---:|---:|
| primary_key_hot | 1,511,944 | 2,286 |
| filtered_limit | 448 | 809 |
| sort_limit | 172 | 466 |
| aggregate | 390 | 1,076 |
| group_aggregate | **BLOCKED: WorkspaceCapacity** | 885 |
| mixed | 505 | 597 |

### Server/client boundary

| Workload | RAMSQL ops/s | PostgreSQL 16 ops/s |
|---|---:|---:|
| primary_key_hot | 13,671 | 9,459 |
| filtered_limit | 286 | 4,281 |
| sort_limit | 101 | 86 |
| aggregate | 182 | 254 |
| group_aggregate | **BLOCKED: WorkspaceCapacity** | 121 |
| mixed | 271 | 335 |

## 1,000,000 rows

### Native public API

| Workload | RAMSQL ops/s | IRDB 1.10.821 ops/s |
|---|---:|---:|
| primary_key_hot | 1,014,507 | 3,226 |
| filtered_limit | 42 | 190 |
| sort_limit | 17 | 55 |
| aggregate | 32 | 491 |
| group_aggregate | **BLOCKED: WorkspaceCapacity** | 435 |
| mixed | 51 | 48 |

### Server/client boundary

| Workload | RAMSQL ops/s | PostgreSQL 16 ops/s |
|---|---:|---:|
| primary_key_hot | 12,747 | 7,442 |
| filtered_limit | 32 | 4,423 |
| sort_limit | 12 | 24 |
| aggregate | 21 | 57 |
| group_aggregate | **BLOCKED: WorkspaceCapacity** | 33 |
| mixed | 34 | 93 |

## Findings and next work

- Both native and server candidates stored and queried one million rows with runtime buffers. This removes the compiled 512-row storage ceiling from the new hosted path; it does not remove every capacity limit or prove production scale.
- These selected 512-row cases favor the candidate. They do not reproduce or invalidate the alpha.2 reviewer's measurements.
- At one million rows, IRDB remains faster for the filtered ordered read, top-K sort and aggregate. The native mixed result is close and is not evidence of a general superiority claim.
- Grouped aggregation fails with WorkspaceCapacity at 100,000 and 1,000,000 rows even though there are only 64 groups. This is a blocking executor/workspace design gap, not a successful fast result.
- PostgreSQL's ordered/indexed access path scales much better for the filtered ordered read. Secondary/ordered indexes and planning are needed; a primary-key hash alone does not replace a B-tree.
- Next implementation priorities are streaming/hash aggregation, ordered/indexed LIMIT execution, compact page/variable-length values, result streaming and scalable session/scheduling budgets. The remaining text/column/connection limits are not closed by this patch.

## Scope and reproducibility

- Volatile-memory tier only: RAMSQL --memory; IRDB in-memory API; PostgreSQL UNLOGGED table with synchronous_commit=off. No per-commit durable-flush equivalence is claimed for IRDB. Durable comparisons remain separate.
- A separate Windows test verifies 2,048-row durable reopen and offline restore, including fail-closed recovery with an inadequate row budget. This does not establish million-row durability or operational qualification.
- Process memory includes different drivers/import mechanisms; reserved backing bytes and process working set are different measurements. No normalized memory-efficiency claim is made.
- Hardware, SQL, binary/source hashes, exact versions, checksums and every trial are in [results.json](results.json). Raw native/IRDB stdout is retained alongside it.
- Reproduction: [adapter and commands](../../tools/irdb_bench/README.md), [comparison runner](../../tools/benchmark_review.py), [native benchmark](../../src/review_benchmark.sig).
- The candidate uses Sig 0.6.4 ReleaseFast and ZPM commit 20dea8b148d68cb5894c32dc9cb2d48f09fba00b. The benchmark adapter uses .NET SDK 10.0.402. The source was a working candidate; the report does not claim it is a production release.
