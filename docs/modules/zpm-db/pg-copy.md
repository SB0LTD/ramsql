# Module: `pg_copy` (PostgreSQL COPY data formats)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/pg_copy.sig` (plus the COPY state machine in `zpm/src/db/postgres.sig:673-1056`) |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 330 lines, 13 KB |
| Owner lane | B1 |
| Status | beta |
| Formatting (`sig fmt --check`) | `pg_copy.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`pg_copy.sig` implements the three COPY data formats, following PostgreSQL's
`copyfromparse.c` and `copyto.c`:

- **Row framing**: `lineEnd`, `binaryHeader`, `tupleEnd`.
- **Field splitting and unescaping**: `textFields`, `csvFields`, `tupleFields`.
- **Output escaping**: `appendText`, `appendCsv`.

Field slices point into the row or into a caller scratch buffer. The module
allocates nothing and holds no state.

It does not see protocol messages. CopyData, CopyDone and CopyFail handling,
the carry buffer for rows split across messages, and row parking all live in
`postgres.sig` (`copyHandle`, `copyFeed`, `copyRows`, `copyLine` and
`copyFinish` at 798-1056). They are documented here because they define how
this module is driven.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Options`, `Format`, `Eol` | struct / enum | format, delimiter, NULL string, quote, escape; line-ending style fixed by the first line (21-27, 18, 135) |
| `signature`, `binary_header_bytes` | const | `PGCOPY\n\xff\r\n\0` and 19 (29-31) |
| `lineEnd(options, bytes)` | fn | Length of the first complete text or CSV row including `\n`, or null for "need more". A text backslash escapes the next byte; CSV quotes span lines (44-74). |
| `binaryHeader(bytes)` | fn | Header length (19 + extension) or null. Signature mismatch, the OID flag, unknown critical flags and a negative extension give `BadCopyFormat` (78-92). |
| `tupleEnd(bytes, expected)` | fn | Tuple length or null. 2 for the `-1` trailer. Wrong field count or a field length below -1 gives `BadCopyFormat` (97-114). |
| `tupleFields(tuple, out)` | fn | Splits a tuple **already validated by `tupleEnd`**. It does no bounds checks of its own (117-132). |
| `stripLine(line, eol, terminated)` | fn | Removes `\n` or `\r\n`. A terminator different from the first line's gives `BadCopyFormat` (139-148). |
| `textFields` / `csvFields` | fn | Decode one row into `scratch`. More than `out.len` fields gives `BadCopyFormat`; scratch overflow gives `TextCapacity`; an unterminated CSV quote gives `BadCopyFormat` (162-217, 221-265). |
| `Sink`, `appendText`, `appendCsv` | struct / fn | Bounded output with PostgreSQL's escaping rules (273-330). |
| `Error` | error set | `BadCopyFormat`, `TextCapacity` (16) |

## 3. Dependencies and layering

- Imports: none.
- Imported by: `postgres.sig:8`, re-exported as `postgres.copy_format` (10).
  ZPM tests: `test_pg_copy.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-COPY-1** (framing never overreads): `lineEnd`, `binaryHeader` and
  `tupleEnd` return an error, null, or a length `n ≤ bytes.len`. They read
  only `bytes[0..n]`, or the whole input when they return null (49-51, 79-91,
  98-113). FV-17.
- **INV-COPY-2** (bounded decode): `textFields` and `csvFields` write at most
  `scratch.len` bytes and at most `out.len` fields (167, 206, 226, 236, 246,
  253).
- **INV-COPY-3** (precondition): `tupleFields` is called only on bytes for
  which `tupleEnd(bytes, count)` returned that length, with
  `out.len ≥ count`. Today it is called only from `postgres.sig:991`, after
  `rowLength` (953-960) and `copyRows` (967-969).
- **INV-COPY-4** (escape round trip, to be tested):
  - text: for every value `v`, `textFields(appendText(v))` yields `v`, and
    never NULL unless the raw field equals the NULL string;
  - CSV: likewise for `appendCsv` and `csvFields` under the same options.
  Unverified; no test asserts it beyond fixed examples.
- **INV-COPY-5** (chunking invariance, in `postgres.sig`):
  - the sequence of `copyRow` calls depends only on the concatenation of
    CopyData bodies, not on how they are split into messages or input slices;
  - `carry_len ≤ line_bytes` always holds;
  - `copyFeed` terminates, because each loop iteration consumes input or
    returns (917-951).

## 5. Current status and evidence

- Text, CSV (HEADER, HEADER MATCH, QUOTE, ESCAPE, NULL) and binary formats work
  in both directions. COPY works over the simple query protocol only;
  extended-protocol COPY is 0A000 (ZPM `docs/database-server.md:49-50`).
- CopyData bodies are streamed. `copyHandle` accepts tag `d` of any 32-bit
  size before the `max_packet` check (`postgres.sig:829-833`). A single row is
  still limited by `copy_line_bytes` (968), giving 54000.
- After an error the rest of the COPY stream is discarded, as PostgreSQL does
  (`copyError` 902-909).
- Conformance gaps without a finding id:
  - `\.` ends the data only in text format (`postgres.sig:1002`). PostgreSQL
    also honors it in CSV.
  - A bare `\r` line ending is not recognized (`lineEnd` looks only for `\n`).

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`.
Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-13 | low | correctness | `textFields` turns `\000` and `\x00` into NUL bytes (`pg_copy.sig:177-196`). No row or field is UTF-8 validated. PostgreSQL rejects both. Rows with invalid UTF-8 cannot be read back over TDS (`tds.sig:441-447`). | Validate UTF-8 and reject NUL with 22021 before `copyRow`. | B1, B2 |
| NET-16 | low | correctness | COPY error paths in `postgres.sig:806` and `:811-812` return `written > 0, consumed = 0`. `stream_server.sig:25` rejects that, so a stream-hosted COPY error ends the session instead of returning ErrorResponse. | See `postgres.md`. | B1 |
| NET-18 | medium | testing | No fuzzing reaches the COPY decoders. The PostgreSQL fuzz loop never enters COPY mode (`test_postgres.sig:653-676`). | Fuzz targets in section 8. | B1, B2 |

## 7. Security considerations

- **Attacker-controlled input:** every CopyData byte after an authenticated
  `COPY … FROM STDIN`.
- **Bounds:**
  - every decoder bounds its writes by `scratch` (`2 × line_bytes`,
    `postgres.sig:187`) and its field count by `field_capacity`;
  - binary field lengths are i32 values checked for `< -1` (108), so the
    i32-to-usize cast at 109 sees only non-negative values;
  - a huge declared length or header extension makes `tupleEnd` and
    `binaryHeader` return null until the carry fills, then `TextCapacity` (948).
- **Hazard:** `tupleFields` is public and unchecked (INV-COPY-3). A new caller
  that skips `tupleEnd` gets out-of-bounds reads.
- **Denial of service:**
  - an authenticated client can stream up to 4 GiB in one CopyData message.
    The bytes are consumed, not stored. Abuse is bounded by the RAMSQL idle
    timeout only (NET-04);
  - a COPY row parked on a key wait consumes no CPU while blocked.

## 8. Tests

**Existing:**

- ZPM `tests/test_pg_copy.sig` (475 lines):
  - `formats` (172): `\N` is NULL; escapes decode; `\<delimiter>` is data;
    extra fields give 22P04; CSV quotes, doubled quotes, ESCAPE and an
    unterminated quote; text and CSV line framing; CRLF consistency; binary
    header, the OID flag, and tuple and trailer framing; output escaping.
  - `protocol` (225): a column subset with defaults; a row split across two
    CopyData messages; CSV HEADER; a binary round trip; wrong field count
    (22P04) and a 2-byte int4 (22P03); malformed rows roll back and later
    messages are dropped; CopyFail (57014); option errors; COPY inside a
    block; a large CopyData message fed in small slices; a row over
    `copy_line_bytes` (54000); write-set capacity (53200); COPY TO in text,
    CSV and binary; disconnect mid-COPY rolls back; a parked COPY row, then
    canceled.
- RAMSQL `tools/test_postgres_copy.py` uses psycopg for text escapes, CSV,
  binary, defaults, errors, CopyFail, a killed socket, transactions, COPY TO,
  a large message, capacity, and restart.

**Missing:**

- [ ] B1: coverage-guided fuzzing of `lineEnd`, `binaryHeader`, `tupleEnd`,
  `textFields` and `csvFields` with random options. Oracles: no safety trap,
  INV-COPY-1 and INV-COPY-2.
- [ ] B1: round-trip property for INV-COPY-4, including the delimiter, quote,
  escape, NULL string, `\.`, CR and LF inside values.
- [ ] B1: chunking-invariance property for INV-COPY-5. Feed one generated
  COPY stream through `Session.handle` with random message and slice
  boundaries; the `copyRow` call sequences must be identical.
- [ ] B7: differential test against PostgreSQL 16 `COPY FROM`: the same
  bytes give the same rows, or the same SQLSTATE class.
- [ ] B1: tests for `\000`, `\x00` and invalid UTF-8 rejection (NET-13), and
  for `\.` in CSV.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-17 | Lean `Proto/Framing` + fuzz | INV-COPY-1 and INV-COPY-2 | todo |
| (none) | property | INV-COPY-4 escape round trip | todo |
| (none) | property | INV-COPY-5 chunking invariance | todo |
| (none) | differential | rows decoded equal PostgreSQL's for the same COPY bytes | todo |

## 10. Benchmarks

- No suite in `docs/benchmarks/METHODOLOGY.md` measures COPY.
  `tools/test_postgres_copy.py` records `peak_commit` (line 329) but sets no
  gate.
- Proposed: add a COPY load case to S6 (load rate and bytes/row at 1M rows),
  with the S6 per-release reporting rule.

## 11. Open questions

- Should `tupleFields` become private, or validate its input itself, so
  INV-COPY-3 cannot be broken by a new caller?
- Should COPY be allowed in the extended protocol? Today it is 0A000.
- Should one CopyData message have a size cap, for example the roughly
  1 GiB limit PostgreSQL applies?
