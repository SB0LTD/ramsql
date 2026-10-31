# Module: `binder` and `deparse` (name resolution, typing, plans, canonical DEFAULT text)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/binder.sig`, `zpm/src/core/sql/deparse.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | binder 3198 lines, 170 KB; deparse 258 lines, 12 KB |
| Owner lane | B7 (feature additions: B11; fast-path agreement: B10) |
| Status | beta |
| Formatting (`sig fmt --check`) | `binder.sig` dirty; `deparse.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

The binder resolves names against the catalog visible to the statement
snapshot. It types every expression with PostgreSQL's implicit-cast rules for
the 16 supported types. It builds query plans for joins, grouping, ROLLUP,
windows, CTEs, subqueries and set operations. It validates stored generated
columns, folding immutable constants without running volatile functions. It
never executes the statement, so `describe()` is side-effect free.
`deparse.sig` prints the canonical text of DEFAULT and generation expressions
for `pg_get_expr` and `information_schema.columns`. Neither module optimizes
plans; `exec.sig` chooses access paths.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Binder(config).bind(context, statement)` | fn | Returns a `Bound` statement, or an error with a PostgreSQL SQLSTATE. Mutates only the statement pool and plans (`binder.sig:2983-3002`). |
| `Binder.validateGenerated(context, index)` | fn | Re-binds stored generated columns of a candidate table before its CREATE publishes (`3004-3010`). |
| `Binder.bindCopy(context, copy, with_defaults)` | fn | Column mapping and defaults for COPY (`3168`). |
| `Binder.Bound`, `Context`, `Plans`, `Plan`, `Window`, `Select`, `Insert`, `Update`, `Delete`, `Conflict` | types | Bound statement and plan shapes (`42-290`). |
| `Binder.command`, `dataType`, `tableIndex` | fn | Command tag, type mapping, catalog lookup under a snapshot (`291-337`). |
| `Deparse(config).column(pool, source, id, data_type, out)` | fn | Canonical text for the supported subset; otherwise the raw source text (`deparse.sig:38-46`). |

## 3. Dependencies and layering

- binder imports `ast`, `catalog`, `expr`, `lexer`, `parser`, `sequence`,
  `storage`, `types` and `value`. deparse imports `ast`, `lexer` and `types`.
- binder is imported by `exec.sig` and `sql.sig`; deparse by `sql.sig`.
- The binder calls `expr.Eval` to fold constants (`simplifyGenerated` at
  `binder.sig:1023`, `validateDiscardedConstants` at `1323`). The same
  evaluator therefore runs at bind time and at run time. That is intended,
  but evaluation errors can surface from `describe()`.

## 4. Invariants

- **INV-BINDER-1**: `bind` never changes database state and never evaluates
  clocks, sequences, aggregates or subqueries. Constant folding is limited to
  `immutableFunction` and `canRepeatFunction` forms (`ast.sig:48-91`,
  `binder.sig:1304-1318`).
- **INV-BINDER-2**: After binding, both operands of every comparison have
  the same cell tag, or both are integers of any width. Every arithmetic
  node has one of the operand pairs `Io.arithmetic` handles: the same
  numeric tag, date with integer, or datetime with interval. The binder
  inserts the implicit casts. `V.compare` and `Io.arithmetic` depend on
  this (SQLF-08).
- **INV-BINDER-3**: Comparisons use `value.comparisonType`
  (`binder.sig:419`, `2205`). When REAL meets an integer or NUMERIC, both are
  compared as float8, as PostgreSQL's `float48`/`float84` operators do.
  CASE, COALESCE, IN lists, GREATEST/LEAST and UNION use
  `select_common_type` (`value.common`).
- **INV-BINDER-4**: An unqualified column must resolve to exactly one
  relation at the nearest scope level that has it, otherwise 42702. Outer
  references mark the plans in between as correlated (`2149-2203`).
- **INV-BINDER-5**: Untyped literals are parsed against their target type at
  bind time, so `id = 'x'` raises 22P02 even on an empty table
  (`coerce`, `355-380`).
- **INV-BINDER-6**: Aggregates cannot nest (42803). Window calls cannot be
  nested or misplaced (42P20) (`630-724`).
- **INV-BINDER-7**: A stored generated expression is immutable, has no
  subquery, and does not reference another generated column. Otherwise 42P17
  or 0A000 (`900-1022`).
- **INV-DEPARSE-1**: For the supported subset, the output parses and binds
  to the same expression. Anything else falls back to the source text
  (`deparse.sig:38-46`).

## 5. Current status and evidence

- **Changed since `9606877`:**
  - Window typing and lowering (`697-724`, `2890-2957`).
  - ROLLUP and GROUPING.
  - Quantified and row predicates (`1266-1428`).
  - BETWEEN rewritten into comparisons (`1430-1460`).
  - LIKE ESCAPE typing.
  - `to_char`, GREATEST and LEAST.
  - NULLIF result typing.
  - Generated-column restrictions.
- **SQLF-03 is closed.** The REAL comparison fix is in `value.sig:131-134`.
  It is covered by `test_sql_quantified.sig:75-81` (`16777217 =
  16777216::real` is false, `0.1::numeric = ALL(SELECT 0.1::real)` is
  false), `test_sql_between.sig:50-51` and
  `test_sql_row_membership.sig:55`.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-01 | high | robustness | `date ± x` is accepted for any integer type, including int8 (`binder.sig:510-517`). PostgreSQL has only `date ± integer` (int4). This passes int8 extremes into the unchecked add in `temporal.datePlusDays`; see [temporal](temporal.md). | Accept only int2 and int4 (anything else is 42883). Also fix the add. | B7 |
| SQLF-03 | closed | correctness | Fixed at 2f060c2 (see section 5). | Keep the tests. | B7 |
| SQLF-04 | medium | correctness | `\|\|` forces an untyped operand to text (`binder.sig:1509-1514`). So `'{"a":1}'::jsonb \|\| '{"b":2}'` returns the text `{"a": 1}{"b":2}`. PostgreSQL resolves `jsonb \|\| jsonb` and returns `{"a": 1, "b": 2}`. | When one side is jsonb and the other is untyped, resolve to jsonb concatenation, or reject with 42883 until it exists. | B7 |
| SQLF-08 | medium | maintainability | The executor's arithmetic and comparisons rely on INV-BINDER-2 without checking it. `Io.arithmetic` ignores `op` for `integer + date` and `date - date` (`value.sig:494`, `530`), and NULLIF uses `std.debug.assert` (`expr.sig:813`). The simple single-table path (`exec.sig:3090-3170`) and the general engine both consume bound plans. | Check `(op, types)` explicitly in `Io.arithmetic`. Add a route-equivalence differential (section 8). | B10 |
| SQLF-10 | low | correctness | Invalid SQL is accepted. `FROM t, t` has no 42712 check (`binder.sig:2786`), so `t.x` binds to the first `t`. An ambiguous ORDER BY output name takes the first match instead of 42702 (`2418`, set operations `2392`). An ON clause can reference earlier comma-joined items (`2791`). | Reject duplicate relation names, ambiguous output names and out-of-scope ON references as PostgreSQL does. | B7 |
| SQLF-11 | low | correctness | deparse prints identifier spans raw. A quoted `"MyCol"` prints as `MyCol`, and unquoted mixed case is not folded (`deparse.sig:205-207`). Casts drop their typmod (`deparse.sig:252`). This affects catalog display only. | Quote and fold identifiers as `quote_ident` does, and print typmods. | B7 |
| SQLF-12 | low | maintainability | `typeExpr` is 276 lines (`binder.sig:1462-1737`). The header still lists four types (`binder.sig:5-6`). 51 compressed-style lines (window code). At 170 KB this is the largest front-end file. | Run `sig fmt` (A4). Split typing per node kind. Update the header. | A4 + B7 |
| SQLF-14 | high | completeness | Functions are a fixed table of 21 names (`binder.sig:581-606`) plus GREATEST/LEAST. Aggregates are COUNT, SUM, AVG, MIN, MAX, BOOL_AND/EVERY and BOOL_OR (`608-620`). Window functions are RANK, DENSE_RANK, ROW_NUMBER and those aggregates (`697-724`). See [FEATURE_GAPS](../../compatibility/FEATURE_GAPS.md). | Move to a table-driven function catalog and fill it in client-probe order. | B11 |

## 7. Security considerations

- Input: syntax trees built from attacker-controlled SQL. Name lookups read
  only the snapshot-visible catalog.
- Bind-time evaluation (constant folding, literal parsing) runs on attacker
  input. It inherits the evaluator's overflow findings. For example, a
  literal `'294277-06-01'::timestamp` already overflows at bind time. That
  is a trap in Debug/ReleaseSafe and undefined behavior in ReleaseFast
  (SQLF-01, [temporal](temporal.md)).
- Recursion in `typeExpr`, `cloneTree`, `sameExpr` and the generated-column
  walks follows tree depth, which INV-PARSER-2 bounds. `sameExpr` inside
  `groupify` is O(nodes x keys), bounded by `expr_nodes` and `columns`.

## 8. Tests

- **Existing:**
  - `test_sql_expr.sig`: error classes, output names, assignment rules per dialect.
  - `test_sql_query.sig`: joins, grouping, CTEs, set operations, correlation.
  - `test_sql_catalog.sig:107-109`: `column_default` and `pg_get_expr` deparse.
  - `test_sql_nullif_types.sig`, `test_sql_quantified.sig`, `test_sql_between.sig`, `test_sql_row_*.sig`, `test_sql_minmax.sig` and `test_sql_distinct.sig`, each checked against PostgreSQL 18 oracles in `tests/sql_conformance/*_oracle.json`.
  - `test_sql_windows.sig`: 66 captured PostgreSQL queries, per `docs/SQL_REPORTING_GAPS.md`.
  - `test_sql_grouping_rollup.sig`.
  - `test_sql_generated_restrictions.sig` and `test_sql_expression_sources.sig`.
- **Missing:**
  - [ ] B7: operator-resolution differential. For every pair of the 16
    types and every operator and function (including untyped-literal
    sides), compare the result type and SQLSTATE with PostgreSQL 16 and 18
    (`pg_typeof` on the PostgreSQL side). This would have caught SQLF-03 and
    catches SQLF-04.
  - [ ] B10: route-equivalence oracle. Run each generated single-table
    query as written (simple path) and with `OFFSET 0` added, which forces
    the general engine (`parser.sig:977-978`). Compare the result bags.
  - [ ] B7: SQLancer TLP (`Q` = `Q WHERE p` ∪ `Q WHERE NOT p` ∪ `Q WHERE p
    IS NULL`) and NoREC over generated schemas (L7).
  - [ ] B7: name-resolution negatives (42712, 42702 for ORDER BY, ON scope)
    against PostgreSQL.
  - [ ] B7: deparse round trip: `deparse(bind(parse(e)))` parses and binds
    to the same tree, and matches PostgreSQL's `pg_get_expr` text for the
    supported subset.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-01 | Lean + vectors | The rewrites keep three-valued semantics: BETWEEN [NOT] [SYMMETRIC] into comparison AND/OR trees (`1430-1460`), row IN into an OR of row comparisons (`1363-1393`), quantified ALL into NOT ANY. | todo |
| FV-02 | vectors | Integer result types: int2 op int2 is int2, int2 op int4 is int4, any int8 gives int8, overflow is checked in the result type. | todo |
| FV-13 | oracle | After INV-BINDER-2 and -3, every comparison the binder produces is a strict weak order on its operand type, including NULLS FIRST/LAST. | todo |
| FV-20 | oracle | For GROUP BY, DISTINCT and IN subqueries, `comparisonType`/`common` equality agrees with `V.hash` on the converted values. | todo |

## 10. Benchmarks

S1: bind instruction counts for a statement mix. S2 and S3: bind runs for
every statement because there is no plan cache and parameters are re-spliced.
S7 dbMeter: window, ROLLUP and to_char reporting queries. Gates: S1 +3% in
PR CI; S2 and S3 median +5% nightly.

## 11. Open questions

- Should operator and function resolution become table-driven, like a
  `pg_operator`/`pg_proc` subset? That would replace the per-node-kind code
  in `typeExpr`.
- Should `describe()` report evaluation errors from bind-time constant
  folding, or defer them to execution?
- Should SQLF-10 be fixed for PostgreSQL parity, or only documented? Each
  case accepts SQL that PostgreSQL rejects.

### SQLF-04 inventory follow-up

The JSON inventory candidate resolves JSONB concatenation with an unknown operand
as JSONB; typed TEXT continues to select text concatenation. Shared core fixtures
check both value and result type. SQLF-04 has a reproduced implementation fix;
full JSON function/type family qualification remains incomplete.
