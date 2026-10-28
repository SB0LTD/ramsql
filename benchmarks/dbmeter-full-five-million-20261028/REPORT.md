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
15-second warmup and 60-second measurement; results will be recorded separately.
No benchmark leadership, full production readiness or new release is claimed.

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
