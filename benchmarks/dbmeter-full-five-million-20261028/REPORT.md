# Actual five-million-row reviewer dataset

This campaign uses dbMeter `630fbb13be21bd4bbdf7afe4b438ce771a8b3fc0`,
the supplied IRDB Java driver and the original query/expected CSV files.
PostgreSQL 18.3 first executes the original schema generator and passes all
seven original checks. Six actual tables are exported in primary-key order
and transferred unchanged to RAMSQL in 50-row INSERT batches. IRDB receives
the same tables through the reviewer's unmodified CopyToIrdb utility.
Both reference engines pass **7/7** checks. No sample data is scored.

## RAMSQL acceptance and remaining gaps

The default text-128 build, using ZPM `4e9d357a606b184ce156617b7a83efa234d35aff`,
loads **5,000,000 facts**, 50,000 customers and the four other dimension tables.
Private commit after loading is about 7.06 GB in the four-worker run.
The explicit runtime banks are 5 / 200 / 20 / 1,000 / 50,000 / 5,000,000 / 1 / 1;
versions are 10 / 400 / 40 / 2,000 / 100,000 / 5,065,536 / 2 / 2. Workspace
is 65,536 rows and final result capacity is 1,024 rows. These are memory-only
read tests, not five-million-row durability or update-churn acceptance.

Three original checks pass: totals, q5 high-value customers and q6 selective
store/category joins. Four still fail: date formatting (q1), window expressions
(q2/q3) and ROLLUP (q4). **The complete suite is not supported.** Neither this
campaign nor the packed-column unit tests qualify a 100M-row SQL server.

The reviewer's `sql-ramsql/schema.sql` is an alpha.2 sample with a few rows.
It is deliberately bypassed. After the exact full data transfer, original
dbMeter commands use `--skip-setup`, preserving the original query text,
parameters and expected CSVs. The PostgreSQL-specific schema-generator SQL
itself remains outside RAMSQL's qualified scope.

## ROUND and bounded aggregation

The full q5 query exposed two separate issues. ROUND was undefined; the SQL
binder now provides PostgreSQL numeric/float8 overloads using the existing
allocator-free primitives. The type contract passes 4,491 assertions, including
independent decimal models, float ties/signed zero, overload types, extreme
scales, aggregate expressions and error publication. Ten measured PostgreSQL
fixtures are retained in ZPM's `tests/evidence/round-postgres-windows.json`.

After binding worked, any function in the shared expression pool still disabled
streaming aggregation. Q5 attempted to materialize five million facts and failed
WorkspaceCapacity. An eightfold workspace increase did not solve that path.
Conservative deterministic-function classification restores streaming with the
original 65k workspace. A 100k-row regression fails before the fix and passes
after it with the same 1,024 references; a wrapped sequence remains excluded.
Windows/Linux ReleaseSafe and legacy Sig 0.5.9 Debug pass 1,702 assertions.

## Performance measurement boundaries

`screening/` retains exploratory 15-second trials after five warmup seconds,
with one/eight clients. Those trials leave the other reference services idle
and resident; IRDB's full-data load changes available memory before the later
RAMSQL eight-worker trial. They therefore **do not establish a matched worker
scaling comparison**. A diagnostic repeat includes server CPU and machine
availability. Retain the negative results and memory conditions.

The screening data show substantial deficits, particularly q6: RAMSQL with four
workers/eight clients reaches 6.87 queries/s, PostgreSQL 20.79 and IRDB 1,629.06.
These are not repeated confidence estimates or isolated final comparisons.
Fresh tests with only one database active use the original five-second ramp,
15-second warmup and 60-second measurement. Their results follow below.
No benchmark leadership, full production readiness or new release is claimed.

## Isolated original-duration comparisons

The reference services are stopped before each RAMSQL run. PostgreSQL then runs
alone and shuts down cleanly before IRDB starts. Both reference engines pass all
seven original checks again; RAMSQL passes the same three supported checks, with
the other four failures retained. Eight JDBC clients run each query in a fresh
JVM. All measured runs have zero SQL errors. These are single trials, not repeated
confidence estimates. QPS uses the actual recorded measurement window, including
queries finishing after the requested 60-second boundary.

| Query | RAMSQL before fix, 4 workers | RAMSQL before fix, 8 workers | PostgreSQL 18.3 | IRDB 1.10.821 |
| --- | ---: | ---: | ---: | ---: |
| q5 high-value customers | 4.05 | 6.65 | 5.42 | 210.04 |
| q6 selective store/category join | 6.89 | 5.13 | 20.37 | 1,531.01 |

The eight-worker RAMSQL run uses an average of 7.67 server CPU cores for q5,
but only 2.68 for q6. Four workers average 3.90 cores for either query. CPU
averages include the ramp/warmup and JVM command wall time; they are diagnostic,
not per-query cycle measurements. IRDB remains substantially faster in both
cases. These results do not establish a RAMSQL win across the full suite.

[`isolated/summary.json`](isolated/summary.json) links each original raw report;
the per-engine folders retain command provenance, checks and logs. Raw CSV input,
bulk SQL, private driver binaries and the reviewer's source remain excluded.

## Workspace-admission diagnosis

Separate instrumented runs are diagnostic and are excluded from the throughput
table. Most q6 Execute frames take the parallel shared path. An all-frame trace
also captures `BudgetExceeded` while provisioning a private read arena, followed
by the one-second retry delay and serialized fallback. The trace records about
8.51 GB of charged regions, only 163–189 MB of remaining grant allowance, and a
231 MB scratch request. That fallback takes the exclusive database gate and
therefore can stall otherwise independent readers. No named-zone or unsupported
query fallback appears in this trace.

The accounting investigation distinguishes Windows committed-memory limits from
resident-memory limits. A committed but untouched arena must remain owned when
computing remaining commit capacity. The previous ZPM budget used residency for
both scope types, subtracting the nonresident commitment again. ZPM
`ca3ccc622f51df8a46e39cbba75f55381ad3113d` distinguishes scope charges and uses
the process-private commitment counter for ownership. App/shared usage remains
charged to the app scope; physical capacity, headroom and operator caps still
constrain new grants. A native untouched-64-MB regression fails before the
adapter correction and passes afterward. The independent private counter reads
68,390,912 bytes where the old ownership observation reports 154,333,184 bytes.
The native contracts cover real job limits, pressure and grant rollback too.

## Repeated workspace-accounting correction

The corrected binary uses the same default text-128 layout, explicit table banks,
65k workspace, eight workers and eight clients. The real dataset is reloaded into
a fresh server for each run. All three supported original checks pass before
timing and every timed query succeeds. There are three trials per binary; the
later pairs alternate corrected/baseline and baseline/corrected order. Other
database services are stopped throughout. These single-host medians and ranges
are not statistical confidence intervals.

| q6 selective join | Median queries/s | Minimum | Maximum | Average server cores, including warmup |
| --- | ---: | ---: | ---: | ---: |
| Before correction | 3.30 | 3.26 | 5.13 | 1.67–2.68 |
| After correction | 11.90 | 11.66 | 12.42 | 7.67–7.70 |

The median improves **3.61x**. The original single q5 comparison stays essentially
unchanged: 6.65 before and 6.64 after. This fixes a workspace-admission/concurrency
loss, not the remaining fact-scan execution cost. PostgreSQL's isolated q6 result
is still faster, and IRDB remains far ahead. Four full-suite feature gaps and
the 100M-row SQL-server capacity gap remain open.

[`isolated/commit-scope-repeats.json`](isolated/commit-scope-repeats.json) records
every trial, actual measurement window, CPU average and binary hash. All 125
tracked reviewer inputs still match the pinned checkout after CRLF-to-LF-only
normalization; [`reviewer-input-integrity.json`](evidence/reviewer-input-integrity.json)
records their hashes. The original binaries, rejected trials and unsupported
queries remain visible alongside the improvement.

## Reproduction and evidence

`evidence/transfer-provenance.json` records each real table's row count and CSV
hash, plus the generated bulk SQL hash. Raw data and the 233 MB SQL load file
remain in the local ignored `.sig-cache/dbmeter-full/`; they are not distributed.
Driver/server provenance is linked from the central
[dbMeter report](../dbmeter-reviewer-20261027/REPORT.md).

The helpers in `evidence/` record the actual native Windows experiment and
require the sibling private dbMeter checkout, its configured drivers, an
isolated PostgreSQL cluster and owned local IRDB service. They are experiment
recipes, not production installers. Never run the original PostgreSQL setup
against an unrelated application schema. Original failures and the rejected
larger-workspace attempt are retained alongside successful checks.
