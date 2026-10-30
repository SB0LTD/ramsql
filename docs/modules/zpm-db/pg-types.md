# Module: `pg_types` (PostgreSQL value representations)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/pg_types.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 251 lines, 11 KB |
| Owner lane | B1 |
| Status | beta |
| Formatting (`sig fmt --check`) | `pg_types.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

> **Wave 0 update (2026-10-30).** int2 parameters were already quoted (`'-5'::int2`) and serve as the control case in the NET-01 tests. This module is unchanged by the fix. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.

## 1. Purpose and scope

`pg_types.sig` converts between engine cells and PostgreSQL wire values. It is
used in three places:

- sending result cells in text and binary format;
- rendering Bind parameters as typed SQL literals (`'…'::type`);
- decoding binary COPY fields into the text the engine's input functions accept.

The binary formats follow PostgreSQL's `*send` and `*recv` functions.

It does not render int4, int8, boolean or text parameters. `postgres.sig`
renders those itself (`postgres.sig:1658-1676`), and that is where NET-01
lives. int2 (OID 21) *is* handled here: `bind` routes every OID for which
`handles` is true to this module (`postgres.sig:1262-1264`), so an int2 value
is sent as the quoted literal `'-5'::int2`. It has no state and no I/O. All
output goes into caller slices.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Types(Result)` | type fn | Binds the helpers to an engine `Result.ValueIo` (45-50). |
| `Types.Session` | type | `Io.SessionValues` (TimeZone and related settings used by text output) (51). |
| `Types.oid(t)` / `Types.length(t)` | fn | RowDescription type OID and `typlen`; interval is 16 (53-63). |
| `Types.sendText(cell, session, out)` | fn | Engine text output into `out`. Returns bytes written, or `BufferTooSmall` (65-71). |
| `Types.sendBinary(cell, t, session, out)` | fn | Binary send per PostgreSQL. Big-endian ints, IEEE floats, NUMERIC base-10000, DATE/TIMESTAMP relative to 2000-01-01, INTERVAL, JSONB version 1 (73-114). |
| `Types.handles(oid)` | fn | True for the OIDs this module renders as `'…'::name`: every type except text, int8, int4 and boolean (117-129). |
| `Types.literal(bytes, oid, format, out)` | fn | Text format: quotes `bytes`. Binary format: decodes, renders in UTC, then quotes. Appends `::name`. Rejects NUL (131-157). |
| `Types.receiveText(bytes, t, out)` | fn | A binary COPY field becomes text. Text-like values are returned in place; others are rendered into `out`. Wrong lengths give `ProtocolViolation` (163-193). |
| `Error` | error set | `BufferTooSmall`, `ProtocolViolation`, `UnsupportedBinaryType` (14) |

## 3. Dependencies and layering

- Imports: none. `Result.ValueIo` is supplied by the caller at comptime.
- Imported by: `postgres.sig:7` only. It is reached through `Session.rows`,
  `copyOut`, `bind` and `copyLine`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-TYPES-1** (literal shape): for every `bytes` without NUL and every
  handled `oid`, `literal` returns `'q'::name`. Here `q` is `bytes` with each
  `'` doubled (131-139, 143-156). Under the ZPM lexer (`''` is the only
  escape; there are no E-strings) this is one string token followed by a cast.
  It is part of FV-03.
- **INV-TYPES-2** (exact lengths): `decodeBinary` and `receiveText` reject
  every fixed-width value whose length is not the type's width. Widths are 2,
  4, 8 or 16 bytes, and 1 for boolean (171-184, 196-203). NUMERIC requires
  `len = 8 + 2·ndigits` with `ndigits ≤ 40` (213-219).
- **INV-TYPES-3** (no overrun): `sendBinary`, `sendText` and `literal` never
  write past `out`. Every write goes through `put`, which checks
  `bytes.len > out.len - at` (16-20).
- **INV-TYPES-4** (round trip, to be tested): for every cell `c` of type `t`
  that `sendBinary` accepts, `decodeBinary(sendBinary(c, t), t)` formats to
  the same text as `c`. This is unverified; no test asserts it.

## 5. Current status and evidence

- There is no dedicated test suite (NET-19). The module is exercised only
  through other suites:
  - `test_postgres.sig` `extended` (366): binary int8 parameter and int4 result.
  - `test_postgres.sig` `inference` (418): binary int2 and boolean parameters.
  - `test_pg_copy.sig` `protocol` (225): binary COPY header, a tuple and the
    trailer; an int4 field of 2 bytes gives 22P03.
  - RAMSQL `tools/test_clients.py` (psycopg binary cursor) and
    `tools/test_postgres_copy.py` (`case_binary`).
- NUMERIC validity depends on `Numerics.fromBase10000` in ZPM `sql`. That
  function rejects bad digit groups, weights and scales, but this audit did
  not review it (unverified).
- In binary format, parameters are rendered with a default UTC session (150).
  A TIMESTAMPTZ parameter is therefore sent as UTC text with an offset. Not
  reviewed against every TimeZone setting (unverified).

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`.
Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-07 | medium | robustness | `sendBinary` uses `@intCast` from i64 to i16, i32 and u32 for smallint, int4 and oid cells (`pg_types.sig:78-80`). An out-of-range cell traps in ReleaseSafe and is undefined behavior in ReleaseFast. Reachability depends on the engine's range enforcement (unverified). | Use checked conversion and return 22003 instead. | B2 (shared with B1) |
| NET-13 | low | correctness | `receiveText` returns text, name and json bytes unchanged (165), and jsonb after only the version byte (166-169). There is no UTF-8 validation. PostgreSQL's `textrecv` verifies the encoding. | Validate UTF-8 and reject with 22021. | B1, B2 |
| NET-19 | low | testing | No suite imports `pg_types` directly. The vector and round-trip coverage above does not exist. | Add `tests/contracts/pg_types` with per-type vectors and the round-trip property. | B1 |

## 7. Security considerations

- **Attacker-controlled inputs:**
  - Bind parameter bytes, through `literal`;
  - binary COPY field bytes, through `receiveText`;
  - result cells, indirectly, when they were stored from client input.
- **Quoting.** It is safe under the current lexer because `''` is the only
  escape (`lexer.sig:6`). If backslash escapes or E-strings are ever added to
  the lexer, `quote` must change with it. FV-03 is the guard.
- **Numeric decoding is bounded.** `groups` is a fixed `[40]u16` and `ndigits`
  is checked before the loop (219-228).
- **JSONB, name and json decoding** copy into a fixed `Io.Text`, with a length
  check first (235, 242).
- No secrets.
- **Denial of service:** a single `Io.format_bytes` stack buffer per call
  (66, 148, 187).

## 8. Tests

- **Existing:** indirect only, as listed in section 5.
- **Missing:**
  - [ ] B1: vectors per type for `sendBinary` and `receiveText` produced by
    PostgreSQL 16, covering int2/int4/int8 bounds, float specials, NUMERIC
    (NaN, ±Infinity, scale 0 and 1000, maximum digits), date/timestamp
    infinities and boundaries, and interval sign mixes.
  - [ ] B1: round-trip property for INV-TYPES-4 over seeded random cells.
  - [ ] B1: `literal` property: the output lexes as exactly one string token
    plus `::name` for random bytes, including `'`, `\`, `$`, `--` and `/*`
    (FV-03 oracle).
  - [ ] B1: fuzz `receiveText` and `decodeBinary` (through `literal` with
    format 1) for every DataType. Oracles: no safety trap, `ProtocolViolation`
    for every wrong length, output length ≤ `out.len`.
  - [ ] B1/B2: out-of-range smallint, int4 and oid cells must produce an
    error, not a trap (NET-07).
  - [ ] B7: differential test against PostgreSQL binary results through
    psycopg binary cursors for every supported type.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-03 | Lean + oracle | INV-TYPES-1 is the quoting part of parameter substitution safety | todo |
| FV-17 | fuzz | INV-TYPES-2 and INV-TYPES-3 are the decoder part of wire framing | todo |
| FV-11 / FV-12 | Lean + vectors | NUMERIC and temporal values agree with the models used by `fromBase10000` and the temporal functions | todo |
| (none) | property | INV-TYPES-4 round trip | todo |

## 10. Benchmarks

- Suite S3 measures binary-cursor and prepared-statement traffic. No
  micro-benchmark (S1) exists for `sendBinary` or `literal`.
- The NET-01 fix may move integer rendering into this module or replace it
  with typed binding. Add an S1 micro case for Bind with 32 parameters before
  that change.
- Gate (METHODOLOGY §6): S1 instruction count +3% fails.

## 11. Open questions

- After typed binding (NET-01), should `literal` survive at all? Binary values
  could be decoded straight into engine cells, removing the text round trip.
  Owners: B1 and C1.
- Should binary parameters be rendered in the session TimeZone rather than
  UTC (150)? Needs a differential check against PostgreSQL first.
