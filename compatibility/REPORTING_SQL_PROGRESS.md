# Reporting SQL gap-closing record — 30 October 2026

Follow [Q03/Q06 in the gap-closing plan](GAP_CLOSING_PLAN.md), with reusable
parser/binder/value/execution in ZPM and server qualification here. The original
dbMeter full 5M suite passes 3/7 in run 40994795421. Candidate implementation is
not a closed gate; preserve original SQL, data, expected CSVs and all failures.

| Original failing query | Missing feature / plan | Implementation | Closing evidence still required |
| --- | --- | --- | --- |
| q1 region/month | Temporal to_char / Q03 | ZPM PR 78 | Exact original full-data query on final pin |
| q2 category rank | RANK over grouped totals / Q06 | ZPM PR 79 | Final PG16/18 server and original full-data query |
| q3 product shares | SUM window over grouped CTE / Q06 | ZPM PR 79 | Final PG16/18 server and original full-data query |
| q4 segment/discount | ROLLUP subtotal levels / Q06 | ZPM PR 80 | Final PG16/18 server and original full-data query |

## Evidence so far

- Temporal formatting: 252 actual PostgreSQL 16.15 fixtures; 551 SQL assertions
  plus 9 primitive output-ownership assertions on Windows/Linux ReleaseSafe and
  legacy Linux Debug. A later lowercase unsupported-ISO rejection is also tested.
- Windows: 66 actual PostgreSQL fixtures; 8,844 assertions on those three modes,
  including every-checkpoint work-limit/cancellation sweeps. Hosted database
  matrices pass Windows/Linux/macOS for the standalone window candidate.
  The combined server run matched 148/149 semantic cases: its only mismatch was
  nested-window SQLSTATE (ordinary aggregates require 42803). The fix adds
  aggregate/FILTER/window-clause context tests, passing 8,882 Windows assertions;
  the final combined server/platform rerun is pending.
- ROLLUP: 31 actual PostgreSQL fixtures; 19,443 assertions on those three modes,
  including genuine NULL groups, empty totals, HAVING, exact AVG, independent
  membership, volatile-input evaluation once, snapshots/private writes, poisoned
  scratch, constrained capacity and every-checkpoint cancellation/work limits.
- Combined Windows regressions preserve query 63,417, type 4,491, existing
  group-synopsis 6,409 and streaming-group 1,702 assertions, including 100k actual
  SQL-loaded rows. No local throughput was measured.
- Temporal-only RAMSQL integration passes all 32 active hosted/native/release
  and PG16/PG18 memory/durable+SCRAM checks. The combined pin needs fresh checks.

ZPM's broad legacy graph retains its four known SABR/TLS/TTS failures. Those are
not counted as passing; dedicated hosted SQL and source contracts are separate
evidence. The full original dbMeter checks and final combined server run remain
necessary, even when every focused library fixture passes.

## Remaining scope

See [SQL_REFERENCE.md](../SQL_REFERENCE.md) for the concrete supported forms.
Numeric/interval/localized/ISO-week/timezone formatting; FILTER/DISTINCT window
aggregates; bounded/moving/explicit RANGE/GROUPS frames; named windows; correlated
subqueries in the same window target projection; composite/multiple ROLLUP,
CUBE/general GROUPING SETS and nonintegral/computed DISTINCT remain gaps.
Caller-owned memory still has finite budgets; exhaustion must be a documented
SQL error, never hidden allocation or silent wrong rows.

The broad Q03/Q06 families stay partial. This work does not qualify compact
100M-row storage, full PostgreSQL compatibility, production readiness or the
all-engine performance gate. dbMeter PR 1 remains unmerged until its unchanged
full qualification passes; no README benchmark-lead claim follows here.
