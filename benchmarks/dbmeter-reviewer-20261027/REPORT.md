# Reviewer dbMeter reproduction and optimization campaign

The reviewer supplied access to `benor1470/dbMeter`; it is cloned as a sibling
of RAMSQL at commit `630fbb13be21bd4bbdf7afe4b438ce771a8b3fc0`. Its original
SQL, expected CSVs, generators and workload weights are used. Generated tracked
files differ only in Windows line endings; the harness checks normalized bytes
against Git HEAD before each run. Private upstream source and vendor binaries
are not redistributed in this report.

## Measurement boundaries

The primary comparisons use native Windows servers on the same host, Java
21.0.12, PostgreSQL JDBC 42.7.4, and equal explicit client heap limits. Native
PostgreSQL is the reviewer's 18.3 version in a new isolated cluster. IRDB uses
the official .NET 10 server distribution, assembly version 1.10.821.0, with the
reviewer's supplied `jirdb-2025-02-02.jar` and `irdb.ini` (`nocache=true`). Only
local data/log paths and the test port are relocated. The initial WSL
PostgreSQL 16 experiment is retained separately and excluded from native claims.

All size-scaling runs first execute the original PK/filter/top-N/last-row CSV
checks. RAMSQL additionally checks actual COUNT/SUM after loading. Every load
report retains errors and consumes all result rows. No local build or profiler
runs during throughput timing. Sampler runs are separate, sample owned server
threads only, and are wall-time instruction-pointer observations, not PMU events.

## Initial native 100k-row screening results

These are **single process trials**, with five measured seconds after two seconds
of warmup, following the reviewer's isolated-query procedure. They identify the
tested source/binary hashes in each report. They are screening data, not repeated
trial confidence estimates or universal benchmark claims.

| Clients / query | RAMSQL after short-read changes | PostgreSQL 18.3 | IRDB 1.10.821 |
| --- | ---: | ---: | ---: |
| 1 / PK lookup | 31,089 ops/s | 26,911 | 2,612 |
| 8 / PK lookup | 153,401 ops/s | 140,515 | 7,275 |
| 1 / filtered scan | 8,870 ops/s | 362 | 3,039 |
| 8 / filtered scan | 37,171 ops/s | 2,300 | 8,367 |
| 1 / top-N sort | 750 ops/s | 177 | 205 |
| 8 / top-N sort | 2,705 ops/s | 1,094 | 953 |

Sources: `../dbmeter-short-bind-20261027`, `../dbmeter-postgres-native-20261027`
and `../dbmeter-irdb-native-20261027`. RAMSQL uses ZPM c8b121e in this table.
The 512-row cases are retained in those same reports. PK's lead over PostgreSQL
is modest; this does **not** satisfy a goal of significant wins in every case.

The matched earlier 100k baseline used the original scalar filter: 183 ops/s
with one client and 204 with eight. Projected filtering first raised those to
2,705/2,980; extended-query scheduling raised eight-client throughput to 10,853;
exact batched charging raised it further. Query workspace grants differ in the
last screening run (4,096 vs 100,000 rows), so its complete improvement is not
attributed to one isolated code change. Raw intermediate runs are retained.

## Actual million-row sales subset

The original six-table, one-million-sales-row dataset is generated and loaded,
including 50,000 customers. All **7/7 original CSV checks pass** on RAMSQL with
both Linux and native Windows explicit text-32 profiles. Windows also passes at
65,536 workspace rows and 1,024 result rows. Unequal banks in creation order are
5 / 200 / 20 / 1,000 / 50,000 / 1,000,000 / 1 / 1 rows, with twice that many
version slots. This is not the default text-128 profile or 100M-row acceptance.

The original category COUNT(DISTINCT customer_id) check initially failed 54000
at one million rows. Exact caller-backed distinct bitmaps close that failure.
This is actual SQL, not a packed-column microbenchmark. All seven checks also
pass on PostgreSQL; the identical tables are copied to IRDB through the
reviewer's own CopyToIrdb client. A documented date-literal overlay is needed
for IRDB's subset query, just as its original full-suite dialect needs changes.

An initial weighted RAMSQL run remained about 12 queries/s with one/eight clients.
The source files begin with comments, exposing a scheduling bug in the old
SELECT/WITH prefix hint. Keep this counterexample: it is not a scaling win.
With the tokenizer-based routing fix, the same four-worker, eight-client mix
reaches 43.6 queries/s, with zero errors and all seven checks passing. Observed
server CPU averages 3.63 cores across checks, warmup and measurement. This
demonstrates concurrent execution, not whole-machine scaling.

The native reference runs use the same original weights, five-second ramp,
15-second warmup and 60-second measurement, and each passes all seven checks:

| Engine | One client, queries/s | Eight clients, queries/s |
| --- | ---: | ---: |
| RAMSQL before comment-routing fix | 12.1 | 11.9 |
| RAMSQL after comment-routing fix | not measured | 43.6 |
| PostgreSQL 18.3 | 5.7 | 27.1 |
| IRDB 1.10.821 | 426.3 | 1,053.6 |

These are single trials. IRDB is about **24 times faster** than current RAMSQL
with eight clients on this analytic mix. The earlier size-scaling wins do not
generalize to this workload. IRDB's original configuration disables its query
cache; the literal overlay above is recorded in `evidence/irdb-subset-changes.json`.
Raw reports live in `../dbmeter-native-million-{irdb,postgres}-20261028` and
`../dbmeter-comment-balanced-million-20261028`.

Next optimization priorities are fact-scan cost, repeated dimension lookups and
aggregate/group transitions. Profile these paths independently, preserve exact
COUNT DISTINCT and integer-overflow behavior, and rerun original checks before
scoring any change. No precomputed result substitutions or query-specific answers
are acceptable.

## Implemented infrastructure and safeguards

- Projected non-null integral filters fetch full MVCC rows only for survivors.
- Independent contexts describe and execute eligible extended autocommit reads.
  Live tests cover actual overlap, writer exclusion, timeout/cancel recovery,
  session/scratch reuse and 640 named prepared queries with binary results.
- Parse/Bind publish initialized SQL prefixes instead of copying 64 KB tails.
- Batched work charging preserves scalar error/state and callback positions in
  76,801 independent comparisons, including saturation and callback mutations.
- Exact integral distinct counts stream into caller-owned bitmaps; 100k rows,
  a 50k-value domain and 20 groups use 512 handles / 125,120 bitmap bytes.
- Scheduling candidates use the SQL tokenizer, including nested/line comments;
  read-only binding remains mandatory before shared execution.

The broad legacy Sig 0.5.9 Debug job exposed a direct optional-error comparison
problem in the new test oracle. Explicit tag/payload comparison passes the exact
legacy configuration and Sig 0.6.4 Debug/ReleaseSafe/ReleaseFast. Runtime charging
was unchanged. The broad graph still has four pre-existing unrelated failures;
the added work-control contract now passes. Retain the original failing evidence.

## Remaining acceptance work

Run repeated, alternating comparisons and the original weighted point workload,
all six size-scaling sizes (including full-result fetching), 32 clients, and the
full five-million-row analytic suite. Unsupported SQL/capacity errors count as
gaps, never wins. Large-result buffering, default-profile memory use, automatic
core sizing, compact physical SQL/MVCC storage, broader join/aggregate paths and
full dialect parity remain open. The 100M-row multi-table requirement is not met.

Reproduce size/point/analytic runs with `tools/benchmark_dbmeter.py`; source and
driver identities, commands, original JUnit checks, histograms, timelines and
errors are retained. The historical alpha.2 aggregate-absence check in the point
suite must be labeled obsolete, not treated as a current SQL failure. No new
release is implied by these development results.
