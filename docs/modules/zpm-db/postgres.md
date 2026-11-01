# Module: `postgres` (PostgreSQL v3 wire codec)

**Parameter-lexing candidate, 1 November 2026:** NET-01 is merged and qualified.
The NET-02 candidate uses the public L0 `sql.Tokenizer` for parameter counting,
interpolation, inference and DEALLOCATE spans. The module now explicitly imports
`sql` as well as `scram`; source ownership remains with the named SQL module.
Comments/quoted strings/quoted identifiers and dollars in identifiers never create
parameter references. Untouched source bytes remain exact; fixed capacities and
numeric boundary rendering remain. Lexical errors propagate before replacing
statements/portals. Values still render into SQL text: typed binding B1.1 and
broader type inference remain open. ZPM docs/PG_PARAMETER_LEXING.md records scope
and tests; independent review and hosted qualification are required before closure.
The baseline line numbers and wave-zero audit below are historical.

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/postgres.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 1676 lines, 81 KB |
| Owner lane | B1 |
| Status | beta |
| Formatting (`sig fmt --check`) | `postgres.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

> **Wave 0 update (2026-10-30).** NET-01 is fixed on ZPM branch `b1/net01-param-splice`. Negative int4 and int8 parameters render as `(-n)`. `splice()` inserts a space wherever a neighbouring byte could merge with the value (word, number, `.`, `$`, quote doubling, `--`, `/*`, and the `e±` exponent sign). FV-03 now holds for every template that lexes, except templates hit by NET-02, which is still open: templates with `$n` or an apostrophe inside a nested `/* */`. Typed binding (B1.1) remains the long-term fix. Postgres suite assertions went from 15,807 to 17,225. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.

## 1. Purpose and scope

`postgres.sig` decodes PostgreSQL protocol 3.0 bytes into calls on a
caller-supplied `DB` type. It writes the response bytes into a caller buffer.
It covers:

- startup and SSL/GSS refusal;
- the SCRAM message flow (the cryptography is delegated to `scram`);
- simple and extended query, named statements and portals;
- COPY FROM STDIN and COPY TO STDOUT framing (row formats are in `pg_copy`);
- CancelRequest parsing and SQLSTATE mapping.

It owns no socket, allocator, RNG or clock. The caller supplies the verifier,
the server nonce, the backend key and the transport.

It is not a SQL engine. Bind parameters are rendered as SQL text and spliced
into the statement (`interpolate`, line 1580; `scan`, lines 1538-1576). That
design is the root cause of NET-01 and NET-02.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Session(DB)` | type fn | per-connection state, caller-owned. Over 1 MiB: 8 statements and 8 portals each embed a 64 KiB SQL buffer (211-243). |
| `Session.handle(db, input, output)` | fn | Handles at most one frame, or one chunk of a COPY body. Returns `Reply`. `consumed = 0` means "need more". Fails with `BufferTooSmall` when `output.len < requiredCapacity()` (342-399). |
| `Session.configureAuthentication(verifier, nonce)` | fn | Must be called before any startup byte. Copies the verifier. Fails with `AuthenticationState` once startup has begun (261-264). |
| `Session.configureBackendKey(pid, secret)` | fn | Sets the values sent in BackendKeyData. The defaults are pid 1 and secret 0 (254-255, 401-404). |
| `Session.responseCapacity` / `responseCapacityFor` / `requiredCapacity` | fn | Output size the caller must provide: `2 × result storage + 4096` (267-282). |
| `Session.detachedCandidate(input)`, `DetachedCandidate` | fn / union | Proposes a read-only scheduling class for RAMSQL parallel reads. It never authorizes execution (284-329). |
| `Reply`, `Cancel` | struct | `{consumed, written, close, blocked, cancel}` and `{pid, secret}` (17, 35) |
| `isCancelHeader`, `parseCancel` | fn | Parse a 16-byte CancelRequest. Never look up keys (20-27). |
| `connectionLimitError`, `memoryLimitError` | fn | Write FATAL 53300 or 53200 frames for the admission path (1433-1442). |
| `knownSqlstate(err)` | fn | Maps an error name to a SQLSTATE, or returns null (1463). |
| `max_packet` 65535, `sql_capacity` 65536, `statement_capacity` 8, `portal_capacity` 8, `parameter_capacity` 32, `copy_format` | const | limits (10-15) |

`DB` contract: `execute(source, *Result)` is required. The following optional
hooks are detected with `@hasDecl`:

- `startupParameter`;
- `takeParameterChanges`, `reportedParameters`, `parameterValue`;
- `transactionStatus`, `beginTransaction`, `commitTransaction`, `rollbackTransaction`, `failTransaction`;
- `describe`;
- `copyDescribe`, `copyBegin`, `copyRow`, `copyEnd`, `copyFail`, `copyBlocked`, `copyResume`;
- `valueSession`, `takeRollbackTo`, `savepointLevel`.

## 3. Dependencies and layering

- Imports: `scram` (6), `pg_types.sig` (7), `pg_copy.sig` (8). Nothing else.
- Imported by:
  - RAMSQL `src/server.sig:25` (the TCP reactors);
  - RAMSQL `src/platform/sb0_app.sig:7` (the SB0 native trusted stream; no SCRAM there);
  - the ZPM tests `test_postgres.sig`, `test_pg_copy.sig` and `test_pg_stream.sig`.
- Layer rule: no violation found. The codec reaches storage only through the `DB` the caller passes in.

## 4. Invariants

- **INV-PG-1** (auth gating): `dispatch` and every COPY path run only when
  `startup ∧ (authentication = null ∨ authentication.state = complete)`. This
  covers Query, Parse, Bind, Describe, Execute, Close, Sync and Flush. It holds
  today by code review:
  - while auth is pending, 377-387 route every message to `authenticate`;
  - `copy.mode` becomes non-`none` only inside `dispatch` (704);
  - the oversize drain requires completed auth (355).
  Maps to FV-10.
- **INV-PG-2** (failure is absorbing): once `authentication.state = failed`,
  every `handle` call returns `close = true, written = 0` (345). FV-10.
- **INV-PG-3** (SASL phase): while authentication is pending, only `'p'`
  messages are accepted. Any other tag fails authentication with FATAL 28P01
  (442, 378-383). FV-10.
- **INV-PG-4** (extended error recovery): after an error in any message other
  than Query, `failed = true`. Every later message except Sync and Terminate
  is consumed with no effect until Sync clears the flag (386, 394, 625). FV-10.
- **INV-PG-5** (single suspended portal): at most one portal is active. While
  `active_portal ≠ null`, `self.result` is written only by that portal's
  Execute (515, 543, 581-583, 1148). A simple-query COMMIT or ROLLBACK breaks
  this: it clears `active_portal` but leaves the named portal live (NET-03). FV-10.
- **INV-PG-6** (substitution safety): for every accepted statement S and
  values v, `tokens(interpolate(S, render(v))) = tokens(S)`, with each `$i`
  replaced by exactly one literal token whose value is `v[i]`. **False today**
  (NET-01, NET-02). FV-03.
- **INV-PG-7** (framing):
  - every `handle` result has `consumed ≤ input.len` and `written ≤ output.len`;
  - output is a sequence of `tag, u32 length, body` frames whose length equals body length + 4;
  - input frames with `size < 4` or `size > max_packet` are rejected and the
    connection is closed. The one exception is Query, Parse or Bind up to
    16 MiB after auth, which is drained and answered with 54000 (351-363).
  FV-17.
- **INV-PG-8** (cancel):
  - a CancelRequest is recognized only as the first message of a connection (368);
  - the codec only reports the key;
  - RAMSQL acts on it only when pid and secret match the live slot (`src/server.sig:450-463`).
  Not in the FV catalog yet (proposed for C4).

## 5. Current status and evidence

- **Working and contract-tested:**
  - startup, SCRAM login, simple query, extended query with text and binary
    parameters and results, statement lifecycle and DEALLOCATE;
  - portal suspension, ParameterStatus, BackendKeyData, CancelRequest parsing;
  - oversize-frame draining, COPY, and SQLSTATE mapping for every engine error.
  See section 8.
- **Checked by review only, not by tests:** INV-PG-1 to INV-PG-4. They are
  candidates for the FV-10 enumeration test.
- **Interoperability:** RAMSQL `tools/test_auth.py` uses libpq through
  psycopg. `tools/test_clients.py` uses psycopg prepared and binary cursors.
- **Documentation drift:** ZPM `docs/database-server.md:29-30` says packets are
  limited to 16 KiB and SQL to 4 KiB. The code allows 65535 and 65536 (11-12).

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`.
Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-01 | critical | security | `postgres.sig:1666-1672`: `literal` renders int4 and int8 values (OIDs 23 and 20) bare, in both text and binary format. `:156`: `Writer.number` emits a leading `-`. `:1567`: the raw splice. `lexer.sig:303` treats `--` as a comment. int2 is not affected: `bind` sends it to `pg_types.literal` (`:1262-1264`), which quotes it. Inferred parameters take the column's OID (`:1212`, `:1222`), so int4 and int8 columns are exposed even when the client sends OID 0. Reproduced at 2f060c2: `UPDATE a SET bal=bal-$1 WHERE id=$2` with `$1=-5` runs as `UPDATE a SET bal=bal` and updates every row. | Render integers as `(-5)` now. Then bind typed values into the parsed statement instead of splicing text. | B1 (TDS side B2) |
| NET-02 | medium | security | `scan` (`:1538-1576`) skips quotes and `--` but not nested `/* */`, which the lexer honors (`lexer.sig:306-320`). An apostrophe inside a block comment inverts the scanner's quote state. | Find placeholders with the ZPM `Tokenizer`. | B1 |
| NET-03 | medium | correctness | `:515-522` clear `active_portal` on a simple-query COMMIT or ROLLBACK but keep named portals live. `:588-596` re-execute an incomplete portal from row 0, repeating rows and DML side effects. PostgreSQL returns 34000. | Close non-holdable portals at transaction end. | B1 |
| NET-05 | medium | security | SSLRequest and GSSENCRequest are answered `N` (`:409-412`). SCRAM runs with no channel binding. With no verifier configured the session trusts any user (`:439`). | TLS 1.3 plus SCRAM-SHA-256-PLUS. | B3 |
| NET-06 | medium | correctness | `:589` executes the portal before `:597` checks the result-format count. DML may already have run when the client gets 08P01. Whether autocommit has already committed at that point is unverified. | Validate at Bind, as PostgreSQL does, or before executing. | B1 |
| NET-08 | medium | conformance | `:1141-1165`: Parse with any oid 0 fails with 0A000 unless the statement is INSERT, UPDATE, SELECT…FROM or DELETE on a plain table. `SELECT $1` fails. Impact on drivers that send unknown types is unverified. | Default unresolved parameters to text instead of failing. | B1 |
| NET-09 | low | security | pid is slot + 1 (RAMSQL `server.sig:647`); the secret is 32 bits (`:645-646`); there is no rate limit on cancel connections (`:450-463`). | Rate-limit cancels; longer keys with protocol 3.2. | B6 |
| NET-10 | low | conformance | `:424` accepts only `UTF8` or `UTF-8`, case-sensitively. The store compares case-insensitively (`store.sig:741`). | Compare case-insensitively. | B1 |
| NET-11 | low | security | `:374` and `:392` send `@errorName` to the client, including before authentication. `:1457-1458` map unknown errors to 54000. | Fixed messages per SQLSTATE; log the internal names server-side. | B1 |
| NET-13 | low | correctness | No UTF-8 validation of Query, Parse or Bind text in the codec. None found in the engine either (grep only; unverified as complete). | Validate at the codec boundary (22021). | B1, B2 |
| NET-15 | low | conformance | No NegotiateProtocolVersion (`:414`). Startup errors use severity ERROR, not FATAL (`:372-375`). Only one suspended portal (`:581-583`). Query during suspension gives PortalBusy (`:515`). NoticeResponse is never sent. A completed portal replays its original tag (`:584-586`). Any database name is accepted (`store.sig:691-695`). | Fix item by item. | B1 |
| NET-16 | low | correctness | `copyHandle` returns `written > 0, consumed = 0` (`:806`, `:811-812`). `stream_server.sig:25` rejects that reply shape. | Change the reply shape or accept it in `stream_server`. | B1 |
| NET-18 | medium | testing | `test_postgres.sig:653-676`: fixed LCG seed, 4096 cases of at most 127 bytes, starting after startup with no SCRAM and no COPY. | Coverage-guided, structure-aware fuzzing (section 8). | B1, B2 |

NET-04 (slowloris and admission) is enforced in RAMSQL `src/server.sig`. See
[`../zpm-platform/tcp-and-stream-server.md`](../zpm-platform/tcp-and-stream-server.md).

## 7. Security considerations

- **Trust boundary.** Every input byte is attacker-controlled. The listener
  binds to 127.0.0.1 (RAMSQL `server.sig:938`), so today the attacker is any
  local process.
- **Code reachable before authentication:**
  - `handshake` (406-440);
  - `authenticate` (441-465);
  - CancelRequest parsing (368);
  - the store's `startupParameter`, called for every startup key before SCRAM
    runs (425). This includes the `options` parser at `store.sig:699`.
- **Reader bounds.** `Reader.take` checks `n > len - at` (80-85), so no length
  field can drive a read past the frame. No out-of-bounds read was found.
- **Secrets.** The verifier is copied into the session (263). The backend
  secret comes from the caller.
- **Denial of service.**
  - Session memory is fixed.
  - After auth, a single message may be up to 16 MiB; it is drained, not
    buffered (355-359).
  - Large stack frames:
    - Bind: about 130 KiB (`literals` at 1253, `next: Portal` at 1268);
    - Parse plus `infer`: about 128 KiB (1113, 1166);
    - Describe statement: 64 KiB (550);
    - COPY: 65 KiB (696).
    Whether host thread stacks are large enough is unverified.

## 8. Tests

**Existing.** ZPM `tests/test_postgres.sig` (980 lines):

- `returningOnce` (257): INSERT…RETURNING executes exactly once across Describe and two Executes.
- `normal` (325):
  - simple-query tags; 23505, 42P01, and 25P02 inside a failed block;
  - a multi-statement query is rejected with 0A000; Terminate closes.
- `extended` (366):
  - a binary int8 parameter and binary result;
  - the text value `a'b; -- $1` is stored literally, not executed;
  - a negative integer inside a VALUES list;
  - portal suspension (`Ds`);
  - `'$9'` inside quotes and `-- $7` in a comment are not placeholders;
  - 42P05, 26000 and 34000.
- `inference` (418): unknown parameter types are inferred for INSERT, SELECT and UPDATE.
- `statementLifecycle` (445): 24 statements, DEALLOCATE and DEALLOCATE ALL.
- `malformed` (472):
  - every truncation of a frame returns "need more";
  - a too-small output buffer is rejected;
  - a missing NUL gives 08P01; an unknown tag gives 0A000;
  - `1; DROP TABLE t` as an int8 parameter gives 42804;
  - an Execute row limit of `0xffffffff` gives 08P01;
  - frame lengths of 3 and `0x7fffffff` close the connection.
- `authenticationContracts` (562):
  - login with the RFC vector;
  - reconfiguring auth is refused;
  - an unknown user gets 28P01;
  - none of Q, P, S, X or `?` gets past pending auth;
  - bad SASL lengths and a bad proof are rejected.
- `backendErrorStates` (627).
- `fuzz` (653).
- `errorStates` (677): every engine error has an explicit SQLSTATE.
- `storeContracts` (753): BackendKeyData, CancelRequest parsing, ParameterStatus, a parked Execute, ROLLBACK TO closing newer portals.
- `oversizedFrames` (868): an 80 KB Query is drained, then 54000 is returned and the session stays usable.
- `detachedCandidates` (894).
- `initializedProtocolPrefixes` (933): stale statement and portal tails are never observable.

**Existing, elsewhere:**

- `tests/test_pg_stream.sig`: a full session over `stream_server` with chunk sizes from 1 to 2048 bytes.
- `tests/test_pg_copy.sig`: see `pg-copy.md`.
- RAMSQL `tools/test_auth.py`, `test_clients.py`, `test_postgres_copy.py` and `test_server.py`.

**Missing:**

- [ ] B1: regression test for NET-01. Statement `x-$1` with negative int4 and int8 values, in both text and binary format, both with explicit and with inferred OIDs. Use int2 as a control.
- [ ] B1: scanner-versus-lexer property test (the FV-03 oracle). Random templates containing `/* ' */`, nested comments, `--`, `$`, quotes and adjacent `-`.
- [ ] B1: Execute on a suspended named portal after a simple-query COMMIT must give 34000 (NET-03).
- [ ] B1: a DML portal with a format-count mismatch must leave no side effect (NET-06).
- [ ] B1: `SELECT $1` with oid 0 (NET-08); `client_encoding=utf8` (NET-10).
- [ ] B1: enumeration test to depth 6, linked to FV-10. Alphabet: startup, SSLRequest, `p`, `Q`, `P`, `B`, `D`, `E`, `C`, `S`, `H`, `X`, `d`, `c`, `f`, CancelRequest. Checks INV-PG-1 to INV-PG-5.
- [ ] B1: coverage-guided fuzzing of `Session.handle` from four start states: pre-startup, SCRAM pending, authenticated, and COPY-in. Oracles:
  - no safety trap in ReleaseSafe;
  - INV-PG-7 holds;
  - a mock `DB` asserts it is never called before auth;
  - every chunking of the same byte stream produces identical output.
- [ ] B1: fuzz `detachedCandidate` and `parseCancel` with the same framing oracle.
- [ ] B7: differential test against PostgreSQL 16 and 18 for extended-protocol sequences: errors, Sync recovery, suspension, and Close.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-10 | Lean `Proto/PgSession` + enumeration | INV-PG-1 to INV-PG-5 | todo |
| FV-03 | Lean + oracle | INV-PG-6, with `pg_types.literal` and TDS `expand` | false today (NET-01, NET-02) |
| FV-17 | Lean `Proto/Framing` + fuzz | INV-PG-7 | todo |
| (proposed) | TLA+ | INV-PG-8 together with reactor slot reuse (see `tcp-and-stream-server.md`) | not in catalog |
| FV-19 | Lean + vectors | SCRAM proof check; see `scram.md` | todo |

## 10. Benchmarks

- Suites:
  - S3: wire simple and extended, prepared;
  - S4: durable writes;
  - S5: concurrency.
- Gate (METHODOLOGY §6): nightly, a median regression above 5% whose
  confidence interval excludes 1.0 fails.
- Fixing NET-01 with typed binding changes the Bind and Execute cost. Measure
  S3 extended before and after.
- Existing evidence: `benchmarks/dbmeter-extended-20261027` and `dbmeter-short-bind-20261027`.

## 11. Open questions

- Typed binding needs a ZPM `sql` API that takes values. An interim option is
  a renderer verified against the lexer. Owners: B1 with C1.
- Should startup parameters be applied before authentication completes?
  Today they are (425).
- Should a simple-query COMMIT close named portals, as PostgreSQL does?
  Decide and document (NET-03).
- Should the codec support protocol 3.2, with its longer cancel keys (NET-09,
  NET-15)? Owners: B1 and B6.
- Stack budget for the 128-130 KiB frames on the Windows main thread. Needs
  measurement; unverified.
- Who updates the stale limits in ZPM `docs/database-server.md`? Lane A5 or B1.
