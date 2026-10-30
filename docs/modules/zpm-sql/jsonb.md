# Module: `jsonb` (JSON validation, canonical JSONB, operators)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/jsonb.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | 863 lines, 39 KB |
| Owner lane | B7 |
| Status | beta |
| Formatting (`sig fmt --check`) | `jsonb.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`jsonb.sig` validates JSON text for the JSON type, which keeps the input
verbatim. It converts text to a canonical JSONB binary held inside a text
cell. Object keys are ordered by (length, bytes), the last duplicate key
wins, numbers are NUMERIC, and `\u0000` gives 22P05. It renders JSONB with
PostgreSQL's spacing. It implements `-> ->> #> #>> @> <@ ?`, btree ordering
following PostgreSQL's `compareJsonbContainers`, hashing, `jsonb_typeof`
and `jsonb_array_length`. It validates untrusted binaries during
change-record replay. It is unrelated to `src/core/json.sig`. There is no
jsonpath, no JSONB construction or modification function, and no operator
on the plain JSON type.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Tag`, `sizeAt`, `tagOf`, `numberAt`, `stringAt`, `countAt`, `Members` | types/fn | Binary layout: tag byte, then the body; containers carry count and size as u16 (`jsonb.sig:27-149`). |
| `validBinary(bytes, depth, digits)` | fn | Full structural check of untrusted JSONB: bounds, tags, number shape, key order, counts (`55-102`). |
| `Jsonb(capacity, depth, digits).parse(text, out)` | fn | Text to canonical JSONB. Gives 22P02, 22P05, 54000 (size) or 54001 (depth) (`411-419`). |
| `Jsonb.validateJson(text)` | fn | Validates text for the JSON type without producing output (`403-408`). |
| `Jsonb.render`, `renderText` | fn | Output text. `->>` returns strings unquoted and JSON null as SQL NULL (`503-541`). |
| `Jsonb.field`, `element`, `path`, `parsePath`, `exists`, `contains` | fn | The operators. A top-level scalar behaves as a one-element array, as in PostgreSQL (`545-730`). |
| `Jsonb.compare`, `hash`, `typeName`, `arrayLength`, `copyValue` | fn | Ordering, hash consistent with it, and functions (`804-863`). |

## 3. Dependencies and layering

- Imports `numeric` and `types`. Imported by `change_log`, `types` and
  `value`.
- `Jsonb(...)` asserts `capacity <= 65535` at compile time (`157`), because
  container sizes are u16.
- Layer rule: no violation. The `types` and `jsonb` import cycle is
  recorded on [types-and-values](types-and-values.md).

## 4. Invariants

- **INV-JSONB-1**: An encoded value is at most `capacity` (`text_bytes`)
  bytes, otherwise 54000. Nesting is at most `json_depth`, otherwise 54001
  (`319`).
- **INV-JSONB-2**: In canonical form every object's keys are strictly
  ascending by (length, bytes) and contain no duplicates (`422-462`).
- **INV-JSONB-3**: `validBinary` accepts exactly the well-formed canonical
  encodings within depth and digit limits (`55-102`).
- **INV-JSONB-4**: `compare` is a total preorder that matches PostgreSQL,
  including the anomaly where `[]` sorts below `null` (`768-806`).
  `compare == 0` implies equal `hash` (numbers hash with scale ignored).
- **INV-JSONB-5**: For a canonical x, `parse(render(x)) == x`.
- **INV-JSONB-6**: Recursion in `parseValue`, `canonical`, `render`,
  `compareContainer`, `deepContains` and `hash` is bounded by the stored
  depth, which INV-JSONB-1 bounds.

## 5. Current status and evidence

Unchanged since ZPM `9606877`. `tests/test_sql_types.sig` `json` covers:

- canonical key order and last-wins duplicates against verbatim JSON;
- `-> ->> #> #>>` including negative indexes and a missing path;
- containment;
- equality across scale (`1 = 1.0`) and the `[] < null` ordering;
- surrogate pairs, `\u0000` rejection, depth 70 rejected with 54001;
- replay rejecting a flipped byte or a truncated binary.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-04 | medium | correctness | `jsonb \|\| '{...}'` concatenates text instead of merging JSONB (binder typing, `binder.sig:1509-1514`). See [binder](binder.md). | See binder. | B7 |
| SQLF-06 | medium | robustness | `canonical` puts `[capacity/3 + 1]u16` on the stack in every recursive frame (`jsonb.sig:436`). At `text_bytes = 65535` that is about 43 KB per level, times `json_depth` 64, which is about 2.8 MB. `parse` also keeps a `capacity`-byte raw buffer on the stack (`412`). This can overflow a 1 MB (Windows default) or 8 MB thread stack (unverified: exact frame size). | Canonicalize iteratively with caller-owned scratch, or cap `json_depth x capacity` at compile time. | B7 |
| SQLF-07 | medium | robustness | Object keys are sorted by insertion sort, O(k^2) per object, with no `ExecutionControl` checkpoint (`jsonb.sig:443-446`). A large `text_bytes` document with about 10^4 keys runs for seconds, and statement_timeout and cancel cannot interrupt it. The LIKE half of this finding was fixed at 2f060c2 (`expr.sig:191`, `200`). | Merge sort over caller scratch, plus a checkpoint every N comparisons. | B7 |
| SQLF-09 | medium | robustness | A stored value can be impossible to read back. A number with dscale 120, such as `1e-120`, takes 7 bytes in binary but renders as at least 122 characters (`jsonb.sig:508-512`). Output buffers are `text_bytes*8+64` (`value.sig:183`), and `->>` text is `text_bytes` (`expr.sig:694`). With the default 128-byte cell, `'[1e-120, ...]'` of 16 elements inserts fine, but text-format output of it then fails with 54000. | Check the rendered length at input, or size the output path for the maximum expansion. | B7 |
| SQLF-11 | low | correctness | JSONB strings and keys compare bytewise (`750`, `782`). PostgreSQL uses the database collation, so results agree only for ASCII or the C collation (documented in the header, `15-16`). `#>` array steps accept only `[-]digits` (`572-593`). PostgreSQL's `strtol` also takes a leading `+` and whitespace (unverified). | Document, or match `strtol`. | B7 |
| SQLF-14 | high | completeness | No `jsonb \|\|`, `-`, `#-`, `?\|`, `?&`, `jsonb_set`, `jsonb_build_*`, `jsonb_agg`, `jsonb_each`, `to_jsonb`, jsonpath, or operators on plain JSON. See [FEATURE_GAPS](../../compatibility/FEATURE_GAPS.md). | In client-probe order. | B11 |

## 7. Security considerations

- Inputs: JSON text from SQL literals, parameters and COPY. JSONB binaries
  from change records during replay are untrusted and go through
  `validBinary`.
- String bytes are not checked for valid UTF-8 beyond rejecting control
  characters (`241-248`). Whether the wire layer validates the encoding of
  query text is not known here (unverified).
- Denial of service: SQLF-06 (stack) and SQLF-07 (CPU). Both grow with
  `text_bytes`, so they are small at the default 128-byte profile and
  significant at 65535.

## 8. Tests

- **Existing:** `test_sql_types.sig` `json` and `changeLog` (section 5).
  `test_sql_change_log.sig` covers change-record round trips.
- **Missing:**
  - [ ] B7 + C1: property tests that canonicalization is idempotent
    (`parse(render(parse(s))) == parse(s)`), that `compare` is a total
    preorder, and that `compare == 0` implies equal hashes over generated
    documents with duplicate keys, scale variants and nesting.
  - [ ] B7: fuzz `validBinary` with mutated canonical binaries and `parse`
    with random JSON-like text. Oracle: no trap; any accepted binary
    satisfies INV-JSONB-2.
  - [ ] B7: operator and ordering differential against PostgreSQL 16/18 on
    generated documents (ASCII keys, C collation): `-> ->> #> #>> @> <@ ?`,
    `ORDER BY` and `=`.
  - [ ] B7: stress at the largest profile: depth 64 with wide objects at
    `text_bytes = 65535`. Measure stack use and check cancellation.
  - [ ] B7: a regression for SQLF-09 (insert, then select, a value that
    expands on rendering).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-13 | oracle | `Jsonb.compare` is a strict weak order equal to PostgreSQL's `compareJsonbContainers` on documents with ASCII strings. | todo |
| FV-20 | oracle | `compare(a, b) == 0` implies `hash(a) == hash(b)`, so DISTINCT, GROUP BY and hash joins over jsonb agree with `=`. | todo |
| none | property | INV-JSONB-5 (round trip) and containment laws: `a @> a`; `a @> b` and `b @> c` imply `a @> c`. | todo |
| none | fuzz | INV-JSONB-3: `validBinary` never traps, and anything it accepts renders without error. | todo |

## 10. Benchmarks

S1: parse, canonicalize, render and `->` on fixed documents, as instruction
counts, gated at +3% in PR CI. S2: jsonb filter and project queries, where
present. There is no dedicated JSON suite yet.

## 11. Open questions

- Variable-length values (F-S01) would lift the `text_bytes` limit on JSONB.
  Until then, should RAMSQL document a JSONB size and depth profile
  separately from `text_bytes`?
- Which JSONB functions do client and ORM probes need first? `jsonb_set`
  and `||` are common in application code.
