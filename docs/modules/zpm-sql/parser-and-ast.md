# Module: `parser` and `ast` (statement grammar and syntax trees)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/parser.sig`, `zpm/src/core/sql/ast.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | parser 1810 lines, 87 KB; ast 518 lines, 19 KB |
| Owner lane | B7 (grammar additions: B11) |
| Status | beta |
| Formatting (`sig fmt --check`) | `parser.sig` dirty; `ast.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`parser.sig` is a recursive-descent parser. It turns exactly one SQL statement
into an `Ast.Statement` whose expression nodes live in a caller-owned `Pool`.
Mode `legacy_alpha` first runs the frozen alpha recognizer (`alpha.sig`).
`ast.sig` defines the node, pool and statement types and enforces the pool's
size and depth limits. Neither module resolves names, assigns types or touches
the database. The binder does that.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Mode` | enum | `standard` or `legacy_alpha` (`parser.sig:35`). |
| `Parser(config).parse(source, pool, mode)` | fn | Resets the pool and parses one statement with an optional trailing `;`. Errors: `Syntax`, `UnsupportedStatement`, `StatementTooComplex`, capacity errors (`parser.sig:54-61`). |
| `Parser(config).parseExpression(source, pool)` | fn | Parses a DEFAULT or generation expression into the existing pool without resetting it (`parser.sig:168-174`). |
| `ast.none`, `Op`, `Func`, `NodeKind`, `Node` | consts/types | Node vocabulary. `none = 0xffff` (`ast.sig:13-171`). |
| `canRepeatFunction`, `immutableFunction`, `strictFunction` | fn | Function metadata used for retries, generated columns and NULL folding (`ast.sig:48-91`). |
| `Ast(config).Pool` | struct | `add` (checks node count and tree depth), `raise` (list depth), `newQuery`, `reset`. Also holds `queries[subqueries]` and `windows[columns*subqueries]` (`ast.sig:197-250`). |
| `Select`, `Insert`, `Update`, `Delete`, `Copy`, `Statement`, `Window`, `Control` | types | Parsed statement shapes (`ast.sig:252-518`). |

Grammar accepted at 2f060c2:
- CREATE/DROP TABLE and SEQUENCE.
- INSERT with VALUES, SELECT or DEFAULT VALUES, plus OVERRIDING, ON CONFLICT and RETURNING.
- UPDATE and DELETE, each with RETURNING.
- SELECT with WITH [RECURSIVE], DISTINCT [ON], joins (INNER, LEFT, RIGHT, FULL, CROSS, comma), WHERE, GROUP BY with `()` or ROLLUP, HAVING, UNION/INTERSECT/EXCEPT [ALL], ORDER BY, LIMIT, OFFSET, FETCH, and FOR UPDATE/SHARE.
- COPY.
- Transaction and session commands.

Expression forms added since ZPM `9606877`:
- IS [NOT] DISTINCT FROM.
- `op ANY/SOME/ALL (subquery)`.
- BETWEEN SYMMETRIC.
- LIKE ... ESCAPE.
- Row constructors.
- GREATEST and LEAST.
- `OVER (PARTITION BY ... ORDER BY ... [ROWS UNBOUNDED PRECEDING ...])`.

## 3. Dependencies and layering

- `parser.sig` imports `alpha`, `ast`, `lexer`, `numeric` (typmod encoding),
  `sequence` and `types`. `ast.sig` imports `lexer`, `sequence` and `types`.
- `parser.sig` is imported by `binder.sig` and `sql.sig`. `ast.sig` is
  imported by `binder`, `deparse`, `dml`, `exec`, `expr`, `parser`, `value`
  and `sql.sig`.
- Layer rule: no violation. `ast.sig` importing `sequence.sig` (for sequence
  option structs) couples syntax to a DML-level module. That is acceptable
  but worth noting.

## 4. Invariants

- **INV-PARSER-1**: Parser recursion depth is at most `expr_depth`. `enter()`
  runs in `expression`, `NOT`, unary minus and `query`, and returns
  `StatementTooComplex` (54001) past the limit (`parser.sig:120-127`).
- **INV-PARSER-2**: Every node's `depth` is 1 plus its deepest child, and is
  at most `expr_depth`. List members raise the owner's depth
  (`ast.sig:230-250`). So binder and evaluator recursion over one tree is
  bounded, including left-deep chains like `1+1+...+1`.
- **INV-PARSER-3**: The node count is at most `expr_nodes` and below
  `0xffff`. Query blocks are at most `subqueries`, CTEs at most `ctes`, FROM
  items at most `relations`, and windows at most `columns*subqueries`.
- **INV-PARSER-4**: `parse` either consumes the whole input (`finish()`,
  `parser.sig:115-118`) or fails. One call is exactly one statement.
- **INV-PARSER-5**: `parse` writes only to the pool. A failure leaves the
  database unchanged.
- **INV-PARSER-6**: Precedence follows PostgreSQL, from lowest: OR, AND,
  NOT, IS, comparison (non-associative), BETWEEN/IN/LIKE, `||` and the JSON
  operators, `+ -`, `* / %`, AT TIME ZONE, unary minus, `::`. A quantified
  sublink can be followed by higher-precedence tails
  (`parser.sig:1326-1358`).
- **INV-PARSER-7**: `x op ALL (S)` is stored as `NOT (x op' ANY (S))`, where
  `op'` is the opposite comparison (`parser.sig:1341`, `1360-1366`).

## 5. Current status and evidence

The grammar list above is exercised by `tests/test_sql.sig`,
`test_sql_expr.sig` (`precedence`), `test_sql_query.sig`,
`test_sql_between.sig`, `test_sql_quantified.sig`, `test_sql_row_*.sig`,
`test_sql_windows.sig`, `test_sql_grouping_rollup.sig`,
`test_sql_like_escape.sig` and `test_sql_distinct.sig`. ZPM
`tests/sql_conformance/cases.json` defines 118 smoke programs. Its README
status table predates the window and ROLLUP work (it still lists them as
failing).

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-02 | high | correctness | `SELECT INTERVAL '1' DAY` parses the typed literal (`parser.sig:1747-1758`) and then takes `DAY` as a bare alias (`parser.sig:1090`, `158-163`). The result is `00:00:01` in a column named `day`; PostgreSQL returns `1 day`. The interval-text part is in [temporal](temporal.md). | Parse interval field qualifiers, or reject a non-keyword word right after a typed literal. | B7 |
| SQLF-10 | low | correctness | `SELECT x ISNULL FROM t` takes the fast bare-column path (`parser.sig:1250-1259`, `ends()` at `1234-1248`) and treats `ISNULL` as an alias. PostgreSQL returns a boolean. `NOTNULL` behaves the same way. | Add the ISNULL/NOTNULL postfix tests, or make them keywords so the alias path rejects them. | B7 |
| SQLF-11 | low | completeness | Identifiers longer than `name_bytes` (63 in RAMSQL, `src/server.sig:84`) fail with `NameCapacity` (`parser.sig:132`, `136`). PostgreSQL truncates them to 63 bytes. | Truncate at 63 bytes on a character boundary and send a NOTICE, as PostgreSQL does, or document the difference. | B7 |
| SQLF-12 | low | maintainability | The header still says "single-row INSERT" (`parser.sig:3`). 23 compressed-style lines (`parser.sig:1103-1117`, `1601-1643`). `query()` keeps a `[ctes]Ast.Cte` array on the stack at every nesting level (`parser.sig:920`). | Run `sig fmt` (A4). Update the header. Build CTEs directly in the pool block. | A4 + B7 |
| SQLF-14 | high | completeness | `typeWord` has no VARCHAR, CHAR, UUID, BYTEA, TIME, SERIAL or array types (`parser.sig:555-559`). LIMIT/OFFSET take only an integer literal (`1192-1204`). DDL is limited to CREATE/DROP TABLE/SEQUENCE (`176-204`). No IF [NOT] EXISTS, table constraints, UPDATE...FROM, DELETE...USING, NATURAL, LATERAL or array ANY (`1334-1335`). Window frames RANGE, GROUPS and bounded ROWS give 0A000 (`1633-1641`). See [FEATURE_GAPS](../../compatibility/FEATURE_GAPS.md). | Add in priority order, ranked by client and ORM probes. | B11 |

## 7. Security considerations

- Input: the full SQL text from any authenticated client, plus parameter
  values spliced in as text (NET-01, NET-02, lane B1).
- Denial of service: recursion is bounded by INV-PARSER-1, node count and
  depth by INV-PARSER-2 and -3, and parsing is linear with one-token
  lookahead. Memory is the caller-owned pool. Its window array grows as
  `columns * subqueries * (columns * ~8 bytes)`. That is about 10 KB at
  RAMSQL defaults and several MB at `columns = 255`. Size only, unverified
  for the largest profiles.
- No allocation, I/O or secrets.

## 8. Tests

- **Existing:** see section 5. Atomicity of parse failures is asserted by
  `test_sql.sig` `malformedDoesNotMutate` and `deterministicFuzz`.
- **Missing:**
  - [ ] B7: grammar-aware fuzzer generated from a PostgreSQL `gram.y`
    subset, built in ReleaseSafe. Two oracles: (a) no trap or stack
    overflow; (b) acceptance implies PostgreSQL 16/18 accepts the statement
    with the same result. Oracle (b) catches cases like SQLF-02 and SQLF-10.
  - [ ] B7: depth tests at the largest supported profile (`text_bytes =
    65535`, `columns = 255`). Use 64-deep parentheses, `NOT` chains, nested
    subqueries and set-operation parentheses. Expect 54001 and never a
    crash.
  - [ ] B7: regressions for `INTERVAL '1' DAY` and `x ISNULL`.
  - [ ] B7: a property test that DEFAULT text survives parse, deparse and
    parse again (with [binder](binder.md)).
  - [ ] B7: precedence differential. Generate random operator trees, print
    them with minimal parentheses, and compare the results with PostgreSQL
    (L7).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-01 (extension) | Lean | For every bag S that may contain NULLs and every comparison `op`, `x op ALL S` equals `NOT (x op' ANY S)` under Kleene logic, with `op'` the opposite comparison. Also covers the empty S case. | todo (`test_sql_quantified.sig` truth tables exist) |
| none | property | INV-PARSER-2 holds for every pool `parse` produces. | todo |
| none | differential | INV-PARSER-6: the precedence of each operator pair matches PostgreSQL 18. | todo |

## 10. Benchmarks

S1: parse instruction counts for a fixed statement mix, gated at +3% in PR
CI. S3: the wire layer splices parameters and reparses every execution, so
parse cost shows up in prepared-statement throughput.

## 11. Open questions

- Should interval qualifiers (`INTERVAL '1' DAY`, `YEAR TO MONTH`) be
  implemented or rejected? Rejecting is quicker and removes the
  silent-result risk.
- Should multi-statement query strings be split here or in the wire layer?
  Today `finish()` rejects them. Whether the PostgreSQL simple-query path
  splits them first is unverified.
- How big may the window pool be for large `columns` profiles?
