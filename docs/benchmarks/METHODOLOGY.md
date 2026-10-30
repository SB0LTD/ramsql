# Benchmark methodology

RAMSQL already keeps a lot of evidence: about 2,530 tracked files (11.8 MB tracked, plus about 100 MB of ignored traces on disk)
under `benchmarks/`. This document sets the rules every future measurement
follows so that results are comparable, reproducible and honest, and so that
raw evidence stops growing the git history. Lane **C6** in
[`PLAN.md`](../../PLAN.md) implements it.

## 1. Measurement boundaries

Never compare numbers across boundaries. Every result names exactly one.

| Boundary | What is timed | Typical tool |
| --- | --- | --- |
| `micro` | one ZPM primitive (lex, parse, bind, eval, hash probe, sort) | Sig bench binary |
| `native` | SQL string to result inside the process, no socket | `ramsql-bench` / `production_benchmark.sig` |
| `wire` | client driver to server over loopback, autocommit | psycopg 3, JDBC 42.7.x |
| `durable` | `wire` with synchronous commit and fsync on both engines | `benchmark_postgres.py` |
| `concurrent` | `wire` or `durable` with N clients and W workers | dbMeter, pgbench-style driver |
| `scale` | load time, bytes/row, RSS and query latency at 1M, 10M and 100M rows | capacity harness |
| `recovery` | WAL validation and replay time per MB, restart to ready | persistence harness |

## 2. Suites

| Id | Suite | Sizes | Reference engines | Gate |
| --- | --- | --- | --- | --- |
| S1 | micro primitives | n/a | previous RAMSQL commit | CI, instruction counts |
| S2 | native SQL: point, filter, top-k, group, 3-way join | 10k, 100k, 1M | previous commit, IRDB | nightly |
| S3 | wire simple and extended, prepared | 512, 100k | PostgreSQL 16 and 18 | nightly |
| S4 | durable writes: insert, point update, DDL | 512, 100k | PostgreSQL 16 and 18, same durability | nightly |
| S5 | concurrency scaling, 1 to 64 clients, 1 to 8 workers | 100k | PostgreSQL 16 and 18 | weekly |
| S6 | scale: bytes/row, load rate, RSS | 1M, 10M, 100M | PostgreSQL | per release |
| S7 | dbMeter reviewer suite (pinned `630fbb1`) | 512 to 5M | PostgreSQL, IRDB | per release |
| S8 | TPC-H-derived read queries (not an official TPC result) | SF 0.1, 1 | PostgreSQL | per release |
| S9 | TPC-C-derived write mix (not an official TPC result) | 1 to 10 warehouses | PostgreSQL | per release |
| S10 | recovery: WAL validate and replay | 10 MB to 10 GB WAL | n/a | per release |

S8 and S9 exist so that claims can be compared with workloads the field
recognizes. Label them "derived"; they are not audited TPC results.

## 3. Workload definitions

- Each workload is a versioned file in `bench/workloads/<suite>/<name>.json`:
  schema DDL, deterministic generator (seed, distributions, null ratio,
  key skew), queries, expected-result checksums and parameters.
- The generator is deterministic for a given seed and records the seed.
- Every timed query result is checked. A wrong or failed result is reported as
  a failure and is never timed as a success.
- Both engines receive identical SQL text, identical driver modes (simple or
  extended) and the same durability settings.

## 4. Environment capture

Every run writes `environment.json`:

- CPU model, physical and logical cores, frequency policy or Windows power
  plan, SMT state, NUMA layout, and CPU affinity mask used.
- RAM total and free at start, swap or pagefile, OS name and build, filesystem
  and device for durable runs.
- RAMSQL commit, ZPM pin, Sig version, build flags (`-Doptimize`, profile
  options), binary SHA-256.
- Reference engine version, full configuration (for PostgreSQL:
  `shared_buffers`, `synchronous_commit`, `fsync`, `full_page_writes`,
  `max_connections`, `work_mem`) and its binary hash.
- Driver versions and JVM heap flags.
- Load observed on the host before and after: other processes above 5% CPU.

A run whose environment file is missing is not evidence.

## 5. Statistics

- Warm up until throughput over three consecutive windows varies by less than 2%,
  or 30 s, whichever comes first.
- At least 10 trials per configuration. For A/B comparisons interleave trials
  (A B A B ...) to cancel drift.
- Report median, p50, p95, p99, p99.9 latency, throughput, and the
  inter-quartile range.
- For a ratio claim (A faster than B), report a bootstrap 95% confidence
  interval of the ratio of medians (10,000 resamples). Claim a change only if
  the interval excludes 1.0 **and** the effect is larger than the suite's
  recorded noise floor (measured by an A/A run).
- Never drop outliers silently. If a trial is excluded, record why.

## 6. Regression gates

| Where | What | Threshold |
| --- | --- | --- |
| PR CI (shared runners) | S1 instruction counts (deterministic), S2 at 10k rows correctness only | instruction count +3% fails; any wrong result fails |
| Nightly (dedicated host) | S2 to S4 wall clock | median regression with CI excluding 1.0 and above 5% fails |
| Release | S2 to S10 full matrix | published, including losses; no gate on beating references |

Shared CI runners are too noisy for wall-clock gates. Use instruction or cycle
counts there, and wall clock only on a dedicated, recorded host.

## 7. Evidence storage

- Raw outputs (logs, CSV timelines, profiles, per-trial JSON) go to the
  evidence store: release assets for release runs, and a separate evidence
  repository or object store for development runs. They are not committed to
  this repository.
- The repository keeps `docs/benchmarks/results/<yyyy-mm-dd>-<slug>.md`: at most
  about 200 lines with the question, method, environment summary, result
  table, losses and failures, and a `manifest.json` listing every raw artifact
  with SHA-256 and location.
- The current `benchmarks/<name>-<date>/` folders migrate this way under lane
  **A2**. Their `REPORT.md` files become result summaries, and their copied
  `source/` scripts are replaced by the commit hash that contains them.

## 8. Reporting rules

- Publish losses, failures and unsupported queries next to wins.
- One binary per comparison. The binary hash appears in the summary.
- Do not shorten one engine's work, change its semantics or drop its errors.
- Separate native engine cost from network and client cost.
- State the boundary, suite id, sizes and hardware in the first paragraph.
- A claim in `README.md` links to exactly one result summary.
