# Reporting SQL gap-closing record — 30 October 2026

Follow [Q03/Q06 in the gap-closing plan](GAP_CLOSING_PLAN.md). Reusable parsing,
binding, values and execution belong in ZPM; independent server qualification
belongs here. Preserve the reviewer's original SQL, data and expected CSVs.

## Qualified reporting milestone

ZPM PR 80 and RAMSQL PR 35 merged after qualification of ZPM source
`2f060c23f1cc6c11b6cc57dc989941b44b5c6442` and RAMSQL source
`ec93758a931e5fb0a6261388e255ff9307a362c6`.

| Original failing query | Implementation | Closing evidence |
| --- | --- | --- |
| q1 region/month | Temporal to_char / Q03 | PG16/18 and original full-data check pass |
| q2 category rank | RANK over grouped totals / Q06 | PG16/18 and original full-data check pass |
| q3 product shares | SUM window over grouped CTE / Q06 | PG16/18 and original full-data check pass |
| q4 segment/discount | ROLLUP subtotal levels / Q06 | PG16/18 and original full-data check pass |

- [ZPM database matrix 41024574517](https://github.com/SB0LTD/zpm/actions/runs/41024574517):
  Linux, macOS and Windows succeed. Broad legacy run 41024574545 has 296
  successes, four known unrelated SABR/TLS/TTS failures and 22 skips; those four
  failures are not counted as passing.
- [RAMSQL release matrix 41024653323](https://github.com/SB0LTD/ramsql/actions/runs/41024653323)
  and [CI 41024653363](https://github.com/SB0LTD/ramsql/actions/runs/41024653363):
  all 32 active checks succeed, with two explicit policy skips. Includes six
  hosted exact artifacts, native SB0X/SB0K build/QEMU and independent PostgreSQL
  16/18 memory/durable+SCRAM qualification of the exact Linux release artifact.
- [Original dbMeter run 41024725859](https://github.com/benor1470/dbMeter/actions/runs/41024725859):
  full 5M and subset correctness each pass **7/7 for RAMSQL, PostgreSQL and IRDB**.
  The prior 3/7 result in run 40994795421 is superseded for these concrete SQL
  failures. No original query/expected CSV was rewritten to achieve this.

The final source also fixes a server-discovered SQLSTATE mismatch: an ordinary
aggregate containing a window reports 42803; misplaced/nested windows report
42P20. The earlier 148/149 result is retained as a failed intermediate run.

## Performance remains an open gate

The corrected dbMeter run deliberately pins candidate and baseline to the same
RAMSQL source. It measures correctness and unchanged-build variability, not a
causally attributed optimization. Full-data mixed 1-client throughput was 0.93
QPS for RAMSQL and 34.96 for IRDB; PostgreSQL is unscored because an original q5
trial did not complete. Full-data mixed 8-client throughput was RAMSQL 1.96,
PostgreSQL 0.72 and IRDB 44.91 QPS. Retain raw reports and the invalid trial; do
not hide them or use this result for a benchmark-lead claim.

Other workflow scenarios may still be running; inspect the linked run for its
final status. These completed suite results do not imply a passing all-workload
advantage gate. dbMeter PR 1 remains draft/unmerged.

## Follow-up candidate: bounded frames and FILTER

ZPM now implements bounded ROWS/GROUPS, integral-key RANGE offsets, all frame
exclusions and ordinary aggregate FILTER. Caller-owned exact prefix state
accelerates integral SUM/AVG, COUNT and Boolean aggregates; ordinal segment trees
serve MIN/MAX. Complex/deferred inputs preserve frame/filter evaluation order;
optional-state capacity uses a charged scalar fallback. No heap or storage/WAL
format change is introduced. Materialization and expression/schema budgets still
apply, and numeric/complex moving expressions may scan each frame.

The new library contract matches 397 captured PostgreSQL 16.15 queries and has
106,873 assertions, including every-checkpoint work/cancellation sweeps, private
writes/snapshots, poisoned scratch, constrained grants, integer extrema, sequence
effects and operation-count checks. The existing window, grouping, query, types,
formatter and 100k-row streaming contracts are regression gates. No local
throughput was measured. Eight new PG16/18 semantic cases exercise frames,
exclusions, FILTER, SQLSTATEs, parameters/binary results, projection and sequence
evaluation. Native SB0X/SB0K QEMU also exercises prefix/tree/FILTER/GROUPS/RANGE,
exclusions and maximum offsets. Final independent server/platform qualification
of the revised source is pending.

### Initial CI findings retained

[Initial run 41039729179](https://github.com/SB0LTD/ramsql/actions/runs/41039729179)
matched all 157 memory / 167 durable semantic cases on PG16. PG18 matched
156/157 and 166/167: its sole mismatch was final projection error timing, not
a frame/FILTER row or metadata failure. For `SELECT id,10/(id-1),SUM(v) OVER ()
FROM compat ORDER BY id DESC LIMIT 1` over ids 1..3, PG16 returned the selected
row while PG18 raised 22012 on a discarded row. Exact extracted cases and
reference/binary/report identities from all four cells are retained in
[the disagreement evidence](evidence/window-projection-reference-disagreement-41039729179.json).

The revised engine retains eager evaluation of complex deterministic targets
and defers unsorted volatile targets until their rows are returned. The concrete
unselected-error probe consequently remains a documented PG16/PG18 timing
difference; both native versions cannot be matched simultaneously for that SQL.
The portable projection case now filters out the invalid denominator and also
tests an invalid **selected** row, with its justification beside the SQL. This
changes only a newly introduced semantic probe, never the reviewer's dbMeter
SQL, dataset, expectations or performance policy. The former probe/result is
retained above, not represented as passing both versions.

The initial native release build also rejected an i128 min/max intrinsic in the
frame clamp. Explicit widened comparisons replace that intrinsic; hosted and
legacy frame reruns pass, while native build/QEMU reruns must qualify the final
archive. No compiler change, disabled native feature or relaxed release gate
is used to resolve it.

## Remaining scope

See [SQL_REFERENCE.md](../SQL_REFERENCE.md) for supported forms. Numeric/interval/
localized/ISO-week/timezone formatting; named windows and remaining built-in
window functions; numeric/float/temporal RANGE offsets; volatile moving inputs;
correlated subqueries in the same window target; composite/multiple ROLLUP;
CUBE/general GROUPING SETS and nonintegral/computed DISTINCT membership remain
gaps. DISTINCT window aggregates are unsupported in PostgreSQL too.

Q03/Q06 stay partial. This work does not qualify compact 100M-row storage, full
PostgreSQL compatibility, production readiness or the all-engine performance gate.
