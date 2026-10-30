# Module: `expr` (expression evaluation, three-valued logic, LIKE)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/expr.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | 902 lines, 45 KB |
| Owner lane | B7 (models: C1) |
| Status | beta |
| Formatting (`sig fmt --check`) | `expr.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`expr.sig` evaluates bound expression trees for one row, or one tuple of
FROM items, at a time. `truth` returns TRUE, FALSE or NULL with SQL
three-valued logic. `eval` returns a cell. The file holds the checked
integer arithmetic, the `int4in`/`int8in` and `boolin` input functions, the
LIKE/ILIKE matcher with ESCAPE, and the built-in functions (`round`, clocks,
`to_char`, GREATEST/LEAST, GROUPING, the jsonb functions, sequence and
catalog functions). Aggregates, windows, sorting and subquery execution are
in `exec.sig`. Subqueries are reached through `Context.hook`. Non-integer
operators are in `value.sig`.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `parseInteger(text, ty)` | fn | `int4in`/`int8in`: whitespace, sign, `0x`/`0o`/`0b`, `_` between digits. Gives 22P02, or 22003 on overflow (`expr.sig:41-87`). |
| `parseBoolean(text)` | fn | `boolin` with unique prefixes, case-insensitive (`89-106`). |
| `checkRange`, `arithmetic(op, ty, l, r)` | fn | Checked `+ - * / %` in i64, then a range check in `ty`. Division by zero is 22012, `minInt / -1` overflows, and `x % -1 = 0` (`108-147`). |
| `like`, `likeEscape`, `validateLikeEscape` | fn | LIKE/ILIKE. The escape is empty or exactly one UTF-8 scalar, else 22025. A trailing escape that the match reaches is 22025 (`157-243`). |
| `Eval(config).Context`, `Hook` | types | Row or tuple binding, session, outer context for correlation, subquery hook (`257-277`). |
| `Eval.truth(ctx, id)` | fn | Three-valued result for boolean nodes (`547-680`). |
| `Eval.eval(ctx, id)` | fn | Cell result. Calls `session.checkpoint()` once per node (`708-902`). |
| `Eval.compareOp`, `evalRef`, `columnRef`, `cast`, `toText`, `literalText` | fn | Comparison of binder-compatible operands, borrowed operands, casts through `value.Io` (`279-501`). |

## 3. Dependencies and layering

- Imports `ast`, `datetime_format`, `float_text`, `lexer`, `temporal`,
  `types`, `value` and `std` (`std.unicode`, `std.mem.eql`,
  `std.debug.assert`).
- Imported by `binder` (constant folding), `dml`, `exec`, `value` and
  `sql.sig`.
- Layer rule: the `value` and `expr` import cycle (see
  [types-and-values](types-and-values.md)). The std use breaks the
  `sql.sig:81-84` contract that only `std.fmt.parseFloat` and
  `std.fmt.float.render` may be used (SQLF-12).

## 4. Invariants

- **INV-EXPR-1**: `truth` implements Kleene logic.
  - AND is FALSE if either side is FALSE, NULL if either is NULL, otherwise TRUE. OR is the dual. NOT maps NULL to NULL.
  - `IN` is TRUE on a match, NULL if there is no match but the left side or any element is NULL, otherwise FALSE.
  - IS [NOT] DISTINCT FROM never returns NULL.
  - A row comparison is decided by the first unequal pair. A NULL pair before that gives NULL (`rowTruth`, `509-545`).
  - WHERE keeps a row only when `truth` is TRUE.
- **INV-EXPR-2**: `arithmetic` returns the exact mathematical result or an
  error. It never wraps. Division truncates toward zero, and `%` takes the
  sign of the dividend (`114-147`).
- **INV-EXPR-3**: `likeMatch(t, p, e)` is TRUE exactly when t is in the
  language of p: `%` matches any string and `_` one UTF-8 sequence, sized
  by its lead byte (bytes below 0xC0 count as one). An escaped character
  matches itself. With ILIKE, ASCII letters match without regard to case.
  Time is O(len(t)·len(p)), with an `ExecutionControl` checkpoint per step
  (`185-243`).
- **INV-EXPR-4**: `eval` recursion depth is at most the node depth, which is
  at most `expr_depth`. Subqueries add at most `subqueries` hook levels.
- **INV-EXPR-5**: Strict operators and functions return NULL for any NULL
  argument. CASE and COALESCE evaluate arms lazily. IN lists evaluate every
  element. A NULLIF comparison cast evaluates the first argument once
  (`808-820`).

## 5. Current status and evidence

**Since ZPM `9606877`:**
- LIKE now supports ESCAPE, matches multi-byte literals, and checks
  cancellation (`157-243`). This closes the LIKE half of SQLF-07.
- New: IS [NOT] DISTINCT FROM, row comparisons and row IN, GREATEST/LEAST,
  GROUPING, `to_char` (`843-873`) and NULLIF with comparison-only casts.

**Evidence:**
- `test_sql_expr.sig`: `precedence`, `truthTables` (every AND/OR/NOT/IS
  combination), `nullLogic`, `errors`, `atomicity`, `borrowedOperands`.
- `test_sql_like_escape.sig`: compares the matcher with a constant-space
  reference DP over generated UTF-8 patterns and escapes.
- `test_sql_like_scalar_validation.sig`: exhaustive valid escape scalars,
  plus short-byte inputs.
- `test_sql_distinct.sig`, `test_sql_quantified.sig`, `test_sql_row_*.sig`,
  `test_sql_minmax.sig`, `test_sql_to_char.sig`: each against PostgreSQL 18
  oracles in `tests/sql_conformance/`.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-06 | medium | robustness | `eval`, `truth` and `rowTruth` hold several full `V.Cell` values per frame: `left_value` and `right_value` (`522`, `611`, `760`) plus per-arm temporaries. A cell is `text_bytes` plus a tag, so at 65535 a frame can be hundreds of KB, multiplied by depth 64 (unverified: frame layout is compiler-dependent). | Pass cells by reference into caller scratch, and measure the stack at the largest profile. | B7 |
| SQLF-07 | medium | robustness | The LIKE half is closed at 2f060c2 (`191`, `200`, `239`). The JSONB half remains open in [jsonb](jsonb.md). | (jsonb) | B7 |
| SQLF-08 | medium | maintainability | NULLIF relies on the binder having inserted a cast, through `std.debug.assert` (`813`). That assert is undefined behavior if violated in ReleaseFast. See [binder](binder.md). | Return an internal error instead of asserting. | B10 |
| SQLF-11 | low | correctness | ILIKE folds only ASCII letters. Multi-byte characters compare as exact bytes (`222-224`). PostgreSQL folds them with the database locale. | Document it, or add simple Unicode case folding. | B7 |
| SQLF-12 | low | maintainability | Using `std.unicode`, `std.mem.eql` and `std.debug.assert` (`168`, `175`, `224`, `813`) contradicts the `sql.sig:81-84` contract. `space` and `trim` are duplicated (`20-29`). `digitsCell` duplicates the integer writers. | Update the contract or vendor the helpers. Shared helpers. | B7 |
| SQLF-13 | medium | testing | The truth tables and LIKE reference exist but are not generated from a model (FV-01, FV-04). The integer arithmetic boundaries are covered only by examples. | Add Lean vectors and an oracle (section 8). | B7 + C1 |

## 7. Security considerations

- Every operand value is attacker-controlled. Errors are typed SQLSTATEs.
  The panics reachable from here are in `temporal.sig` (SQLF-01).
- Cancellation: `eval` checkpoints per node and LIKE per step.
  `datetime_format` and JSONB canonicalization do not checkpoint (bounded
  by `text_bytes`).
- Stack: SQLF-06.

## 8. Tests

- **Existing:** see section 5.
- **Missing:**
  - [ ] C1: FV-01 vectors from the Lean model. Full truth tables for
    AND/OR/NOT, IS tests, IN with NULLs, BETWEEN [SYMMETRIC], row
    comparisons and quantified ANY/ALL, replayed through `Eval.truth`.
  - [ ] C1: FV-02 vectors for int2, int4 and int8 at min, max, -1, 0 and 1
    for `+ - * / %` and unary minus. Note that `abs` does not exist (FEATURE_GAPS).
  - [ ] C1: FV-04 oracle. The Lean matcher against `likeEscape` on random
    UTF-8 text, patterns and escapes, including malformed UTF-8 and
    trailing escapes.
  - [ ] B7: SQLancer-style TLP and NoREC on generated tables against
    PostgreSQL 16/18 (L7). Every finding goes into a contract.
  - [ ] B7: a typed literal-expression generator (`SELECT expr` over
    boundary literals of every type) compared with PostgreSQL text output
    and SQLSTATE.
  - [ ] B7: stack test at `text_bytes = 65535` with 64-deep `||`, CASE and
    arithmetic trees.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-01 | Lean + vectors | `truth` satisfies the Kleene laws including De Morgan. WHERE keeps exactly the TRUE rows. `x = NULL` is NULL for every x. IS [NOT] DISTINCT FROM is total. | todo |
| FV-02 | Lean + vectors + oracle | `arithmetic` (and unary minus via `Io.negate`) returns the exact result in range, else 22003. Division by zero is 22012. Division truncates. `%` takes the dividend's sign. | todo |
| FV-04 | Lean + oracle | `likeEscape(t, p, e)` agrees with the declarative pattern language and errors on a trailing escape that is reached. Running time is O(len(t)·len(p)). | todo (reference DP exists in `test_sql_like_escape.sig`) |
| FV-13 | oracle | `compareOp` orders operands as `Values.compare` does for every comparison operator. | todo |
| FV-20 | oracle | IS NOT DISTINCT FROM equality agrees with `Values.hash`, which DISTINCT and GROUP BY use. | todo |

## 10. Benchmarks

S2 (filter, top-k, group) is dominated by `truth` and `eval`. Track
instructions per row in S1. Gates: S1 +3% in PR CI; S2 median +5% nightly.

## 11. Open questions

- Should ILIKE do Unicode case folding without a locale dependency (a
  simple case-folding table)?
- Should the `sql.sig` std-usage contract be widened to `std.unicode` and
  `std.mem`, or should those helpers be vendored into ZPM?
