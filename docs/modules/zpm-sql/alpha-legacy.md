# Module: `alpha` (frozen alpha grammar for legacy WAL replay)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/alpha.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | 189 lines, 8 KB |
| Owner lane | B7 (replay correctness), with B9 for recovery |
| Status | frozen |
| Formatting (`sig fmt --check`) | `alpha.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`alpha.sig` recognizes exactly the grammar that RAMSQL alpha releases
executed. That grammar covers:

- CREATE/DROP TABLE with INTEGER, INT, BIGINT, INT8, BOOLEAN, BOOL and TEXT
  columns;
- single-row INSERT of literals;
- `SELECT */columns FROM t [WHERE column op literal AND ...] [ORDER BY]
  [LIMIT]`;
- UPDATE ... SET column = literal;
- DELETE.

`Database.executeLegacy` (`sql.sig:838-845`) replays SQL-text WAL records of
kinds 1 and 2. It first runs this recognizer, then the standard
parser/binder/executor in `legacy_alpha` mode. That mode turns integer 0/1
into BOOLEAN and uses the configured INTEGER width. A `Syntax` or
`UnsupportedStatement` error in this mode becomes `UnsupportedLogVersion`
(`sql.sig:1202-1205`). The module must never grow.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Recognizer(config).check(source)` | fn | Accepts an alpha statement or returns `Syntax`, `UnsupportedStatement`, `NameCapacity`, `TextCapacity` or `ColumnCapacity` (`alpha.sig:16-189`). |

## 3. Dependencies and layering

- Imports `lexer` (the frozen `Lexer`) and `types`. Imported only by
  `parser.sig:56`.
- Layer rule: no violation.

## 4. Invariants

- **INV-ALPHA-1**: The accepted language is the alpha-release grammar and
  never changes.
- **INV-ALPHA-2**: Every statement `check` accepts is also accepted by the
  standard parser and binder in `legacy_alpha` mode. Otherwise a valid
  alpha WAL cannot be replayed.
- **INV-ALPHA-3**: A replayed statement has the same effect as it had in
  the alpha release: integer booleans, INTEGER width, and atomicity.

## 5. Current status and evidence

The file is unchanged since ZPM `9606877`. When `any` and `some` became
reserved, the parser added an exception so that alpha identifiers named
`any` or `some` still replay (`parser.sig:135-136`). Tests:

- `tests/test_sql_storage.sig:261-283` (`legacy`): boolean integers in
  replay and not outside it, and rejection of `OR`, aggregates, `VACUUM` and
  truncated statements with `UnsupportedLogVersion`.
- `tests/test_sql_expr.sig:317`: legacy comparisons.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-15 | medium | correctness | INV-ALPHA-2 is violated for identifiers. `alpha.sig` rejects only its own 26 reserved words (`alpha.sig:14`, `40-45`). `parser.identCurrent` rejects every `lexer.reserved` keyword except `any`/`some` in legacy mode (`parser.sig:135-136`). `symmetric` and `asymmetric` became reserved after `9606877` (`lexer.sig:199`). An alpha WAL that names a table or column `symmetric` replayed at `9606877` but now fails with `UnsupportedLogVersion`. Words that were already reserved (`left`, `group`, `default`, `on`, `all`, `with`, ...) have the same problem. Whether alpha releases ever wrote them is unverified. | In `legacy_alpha` mode, accept as an identifier any word the alpha recognizer accepts. Add a replay test over the full reserved list. | B7 (parser fix), B9 (replay fixtures) |
| SQLF-13 | medium | testing | No test replays alpha statements whose identifiers are reserved in the standard grammar. | See section 8. | B7 |

## 7. Security considerations

The input is WAL records written by an earlier RAMSQL. They are trusted for
content but must fail closed when malformed, and they do
(`UnsupportedLogVersion`). A replay failure stops recovery, so INV-ALPHA-2
is an availability property.

## 8. Tests

- **Existing:** section 5.
- **Missing:**
  - [ ] B7: for every keyword in `lexer.reserved`, replay `CREATE TABLE
    <kw> (<kw> INTEGER)`, an INSERT, a SELECT with WHERE, an UPDATE and a
    DELETE through `executeLegacy`. Each must succeed whenever
    `alpha.Recognizer.check` accepts it.
  - [ ] B7 + B9: enumerate alpha statements up to a small size from the
    alpha grammar and check that `check` accepts them, that legacy replay
    succeeds, and that the resulting state matches a reference replay
    recorded with the last alpha release.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| none (catalog entry needed) | enumeration | INV-ALPHA-2: for all statements up to size k, `alpha.check(s)` accepting implies that `executeLegacy(s)` does not fail with `UnsupportedLogVersion`. | todo |

## 10. Benchmarks

S10 (recovery: WAL validate and replay) covers legacy records only if the
corpus contains kind 1/2 records. None is known to (unverified).

## 11. Open questions

- When can kind 1/2 records be retired? For example: refuse to open
  alpha-era data directories after a documented offline upgrade, and then
  delete this module.
- Is there a corpus of real alpha-release WAL files to replay in CI?
