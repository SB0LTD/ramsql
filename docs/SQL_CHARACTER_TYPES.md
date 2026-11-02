# VARCHAR integration candidate

Reusable implementation stays in allocator-free ZPM L0 SQL, with shared PostgreSQL
codecs in ZPM. RAMSQL pins an immutable full commit archive and verified SHA-256;
its changes register shared contracts and exercise actual listeners and recovery.

The common live comparator now records raw RowDescription typmods for every
result column, in addition to names and OIDs; mismatches fail the existing gate.
The live q01 catalog adds 37 native-reference programs and a prepared-parameter /
binary-result case. It compares values, column types/modifiers, SQLSTATE and
follow-up mutation state with native PostgreSQL, rather than substituting frozen
outcomes for candidate execution. A p01 case crash-restarts the server and checks
VARCHAR schema modifiers, default/generated expressions, Unicode values and failed
update atomicity. Memory mode explicitly skips that durable-only case; durable
SCRAM qualification must execute it. The existing skip-count assertion increases
from 18 to 19 for this new restart case; no pre-existing check is removed.

Shared tests cover UTF-8 scalar lengths, explicit truncation versus assignment
errors, trailing ASCII spaces, NUL/malformed UTF-8, catalogs, relational coercion,
metadata, binary codecs, statement rollback, cancellation and replay validation.
Declarations do not allocate storage or override caller-owned resource budgets.
The existing configured value, expression, table and result capacities still apply.

This remains a development candidate pending full hosted and exact-release gates.
The shared candidate locally passes 37 programs, 321 storage assertions and the
final protocol/type/catalog/planner/aggregate regression suites, including
21,044 PostgreSQL protocol assertions. Independent
runtime/parser review found no introduced blocker. Local and hosted product wire,
PG16/18 memory/durable-SCRAM, all supported hosts, native SB0 targets and exact
release qualification must finish before merge/release claims.

CHAR/BPCHAR padding and comparison, general collations, full IN planner equivalence,
permissive VARYING bare aliases, remaining scalar types, constraints and application
catalog/security compatibility remain open. Follow the dependency-ordered
[application plan](https://github.com/SB0LTD/zpm/blob/main/docs/SQL_APPLICATION_COMPATIBILITY_PLAN.md).
This slice makes no production/drop-in or performance claim.

The stronger wire comparator caught a real simple-SELECT Describe metadata bug:
the first 77-case run matched 75, with both parameter cases failing on typmods.
The shared correction copies bound modifiers into each active result slot. Its
new Parse/Describe protocol contract fails before the fix and passes afterward;
poisoned/reused result metadata is also covered. The original failing reports are
preserved as evidence.

The corrected immutable pin now matches 77/77 live native16 comparisons: 38 in
memory and 39 in durable SCRAM, including the crash-restart case. Values, names,
OIDs, raw typmods, command results and SQLSTATE participate. These local results
do not replace the mandatory hosted PostgreSQL16/18 and exact-release matrix.
See [hashed candidate evidence](sql-character-candidate-evidence.json).
