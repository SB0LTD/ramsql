# Module: `lexer` (SQL tokenizer)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/lexer.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | 575 lines, 18 KB |
| Owner lane | B7 (B1 for the parameter-splicing boundary) |
| Status | beta |
| Formatting (`sig fmt --check`) | `lexer.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`lexer.sig` holds two lexers. `Tokenizer` turns one SQL statement into tokens
for the parser. It handles words, double-quoted identifiers, `'...'` strings
with `''` escaping, integer and decimal literals, `$n` placeholders, the
operators `= <> != < <= > >= + - * / % || :: -> ->> #> #>> @> <@ ?`,
punctuation, whitespace, `--` comments and nested `/* */` comments. `Lexer` is
the frozen byte lexer of the alpha grammar. Only `alpha.sig` uses it, and it
must never grow.

It does not handle `E'...'`, dollar quoting, `U&'...'`, `B'...'`/`X'...'`,
non-ASCII unquoted identifiers or encoding validation. Those inputs fail with
`Syntax` (42601), sometimes later in the parser. It does not substitute
parameters. The PostgreSQL wire layer does that before the lexer runs (see
section 7).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Quoted` | struct | Raw span of a quoted form plus its decoded length (16-23). |
| `Kind`, `Keyword`, `Symbol` | enums | Token classes (25), 166 keywords (29-191), operators and punctuation (233-260). |
| `reserved(keyword)` | fn | The 70 keywords that cannot be unquoted identifiers (194-201). |
| `keywordOf(text)`, `classify(word)` | fn | Comptime and runtime ASCII case-insensitive lookup. Only words of 2..12 bytes can be keywords (204-231). |
| `Token` | struct | Kind, symbol, keyword, span, decoded length, integer value. Has `raw`, `quoted` and `isWord` (262-285). |
| `Tokenizer.next()` | fn | Next token, or `Syntax`, `TextCapacity` (string over `text_limit`) or `NameCapacity` (quoted identifier over 255 bytes) (`Tokenizer` 291-469, `next` from 375). |
| `Lexer` | struct | Frozen alpha lexer: `space`, `take`, `expect`, `word`, `takeWord`, `expectWord`, `unsigned`, `quoted`, `finish` (473-559). |
| `decodeQuotedWith`, `decodeQuoted` | fn | Copy a quoted literal's decoded bytes into a caller buffer of at least `len` bytes (563-575). |

## 3. Dependencies and layering

- Imports: `types.sig` only.
- Imported by: `alpha.sig`, `ast.sig`, `binder.sig`, `deparse.sig`, `expr.sig`, `parser.sig`, `sql.sig`.
- Layer rule: no violation. The lexer has no state outside the caller's `Tokenizer` value.

## 4. Invariants

- **INV-LEXER-1**: `next()` reads only inside `source`. Every returned span
  lies inside `source`. Each non-`end` token consumes at least one byte.
- **INV-LEXER-2**: An unquoted word starts with an ASCII letter or `_` and
  continues with letters, digits, `_` or `$`. Any other byte outside a literal
  or comment that is not a listed operator gives `Syntax`. This covers
  non-ASCII bytes, `~ ^ & !` (except `!=`), a single `|`, `:` and `#`.
- **INV-LEXER-3**: A string literal's decoded length is at most `text_limit`,
  otherwise `TextCapacity` (54000). A quoted identifier is 1..255 bytes,
  otherwise `Syntax` or `NameCapacity`. An unterminated string, quoted
  identifier or block comment is `Syntax`.
- **INV-LEXER-4**: An integer token's magnitude is exact. A magnitude above
  `u64` becomes a `.decimal` token and is never wrapped (341-373). A letter
  directly after a number is `Syntax`, so `0x1F`, `1_000` and `1e` are
  rejected.
- **INV-LEXER-5**: A `$n` placeholder has 1 <= n <= 65535. `$0`, `$65536`
  and `$1a` are `Syntax` (405-408).
- **INV-LEXER-6**: Keyword classification is ASCII case-insensitive. Quoted
  words are never keywords.
- **INV-LEXER-7**: `Lexer` (the alpha lexer) keeps its alpha-release behavior.

## 5. Current status and evidence

The tokenizer is complete for the grammar the parser accepts. Since ZPM
`9606877` the only change is new keywords (`rollup`, `over`, `partition`,
`current`, `preceding`, `following`, `unbounded`, `range`, `groups`,
`greatest`, `least`, `any`, `some`, `symmetric`, `asymmetric`). Four of them
became reserved: `any`, `some`, `symmetric`, `asymmetric` (199). Evidence:
`tests/test_sql.sig` `lexerAndOwnership` covers mixed case, CR/LF/tab, `''`
escapes, and results that stay valid after the source buffer is overwritten.
`parserAndSortEdges` covers comments and `u64` LIMIT bounds.
`deterministicFuzz` covers prefix truncations of six statements and 1,024
random byte strings, and checks failure atomicity.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-11 | low | completeness | `key` is reserved here (`lexer.sig:196`). PostgreSQL treats KEY as unreserved, so a column named `key` must be quoted. | Remove `.key` from `reserved` and check the `PRIMARY KEY` parse path. | B7 |
| SQLF-12 | low | maintainability | The `space()` helper (`lexer.sig:287`) is duplicated in `numeric.sig:55`, `float_text.sig:18`, `temporal.sig:140` and `expr.sig:20`. | Use one shared helper in `types.sig`. | B7 |
| SQLF-13 | medium | testing | The only fuzzing is 1,024 random byte strings plus truncations (`tests/test_sql.sig:369-406`). Random bytes almost never form tokens past the first one. | Add a structure-aware token fuzzer and a token differential (section 8). | B7 + C1 |
| SQLF-14 | high | completeness | `E''`, `$$...$$`, `U&''`, `B''`/`X''`, non-ASCII identifiers, `~` and the regex operators, `^`, bitwise operators, and PostgreSQL 16 non-decimal integer literals and digit separators in SQL text all give 42601 (`lexer.sig:375-468`). See [FEATURE_GAPS](../../compatibility/FEATURE_GAPS.md). | Add these in priority order. | B11 |

## 7. Security considerations

- Trust boundary: every byte of SQL text is attacker-controlled once a client
  is authenticated. PostgreSQL extended-protocol parameters are spliced into
  the SQL text as literals before lexing (`zpm/src/db/postgres.sig`). That
  makes this lexer part of the injection boundary. NET-01 (an integer after
  `-` becomes a `--` comment) and NET-02 (the placeholder scanner ignores
  `/* */` comments while this lexer honors them) are tracked in lane B1 and
  theorem FV-03. They are not repeated here.
- Denial of service: lexing is linear. `peek()` re-lexes at most one token.
  Comment nesting depth is a `usize` counter. There is no recursion and no
  allocation.
- No secrets are handled.

## 8. Tests

- **Existing:** `tests/test_sql.sig` (`lexerAndOwnership`,
  `parserAndSortEdges`, `deterministicFuzz`) as described in section 5.
  `tests/test_sql_expr.sig` `precedence` checks operator tokens through
  parsing.
- **Missing:**
  - [ ] B7: structure-aware fuzz of `Tokenizer.next` built in ReleaseSafe.
    Oracle: no safety trap, and token spans plus the skipped whitespace and
    comments rebuild the input exactly.
  - [ ] B7: token differential against PostgreSQL's scanner on a corpus of
    the PostgreSQL regression SQL files. Every input PostgreSQL rejects must
    also be rejected, and accepted inputs must give the same token kinds.
  - [ ] B7: edge vectors for `$0`, `$65535`, `$65536`, `1e`, `.5`, `5.`,
    integers of 19, 20 and 21 digits, comment nesting 1000 deep, and
    unterminated forms.
  - [ ] B1 + C1: FV-03 oracle with random templates and values (owned by the
    NET-01 fix).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-03 | Lean (lexer token fragment) + oracle | For every accepted template S and values v, `tokens(interpolate(S, v)) = tokens(S)`, with each `$i` replaced by exactly one literal token. False today (NET-01). | todo |
| none (catalog entry needed) | fuzz | INV-LEXER-1 and INV-LEXER-2 for all byte strings up to 4 KiB: no trap, spans tile the input. | todo |
| none | property | `classify(w)` returns a keyword exactly when `lowercase(w)` names a `Keyword` field. Every word in `reserved` is reserved or type_func_name in PostgreSQL 18's keyword list, or the difference is recorded in FEATURE_GAPS. | todo |

## 10. Benchmarks

S1 (micro primitives) should time tokenizing a fixed statement mix, measured
in instruction counts. S3 (wire simple and extended) includes lexing for every
statement, because parameters are spliced in as text. Gate: S1 instruction
count +3% fails PR CI.

## 11. Open questions

- Should `E''` strings and dollar quoting be supported? `pg_dump` output and
  many migration tools emit them.
- Should the reserved set match PostgreSQL's list exactly (starting with
  `key`), or stay a documented subset?
- If NET-01 is fixed by binding typed values instead of splicing text, the
  lexer leaves the injection boundary. That decision belongs to B1.
