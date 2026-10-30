# Module: `tds` (TDS 7.2-7.4 endpoint)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/tds.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 959 lines, 47 KB |
| Owner lane | B2 |
| Status | beta (local trust only) |
| Formatting (`sig fmt --check`) | `tds.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`tds.sig` serves Microsoft TDS 7.2, 7.3 and 7.4 clients. It implements:

- PRELOGIN, which always answers "encryption not supported";
- LOGIN7, with empty-password local trust;
- SQL_BATCH;
- `sp_executesql` RPC with integer, bit, NVARCHAR and NULL parameters;
- transaction-manager requests (begin, commit, rollback);
- typed COLMETADATA, ROW and DONE responses, with reassembly of multi-packet
  requests.

Not supported: TLS, TDS 8, integrated or federated authentication, MARS, bulk
copy, other RPCs and prepared handles (1-8).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Session(DB)` | type fn | Per-connection state: phase, negotiated packet size, and four 64 KiB buffers (message, query, RPC values, RPC expansion) (252-267). |
| `Session.begin(out)` | fn | Writes nothing; the client speaks first (288-290). |
| `Session.handle(db, input, out)` | fn | Handles one TDS packet and reassembles until EOM. Returns `Reply{consumed, written, close, blocked}`. Parked work returns `blocked` (893-957). |
| `Session.responseCapacity` / `responseCapacityFor` / `requiredCapacity` | fn | `3 × result storage + 4096`, covering UTF-16 growth and packet headers (269-286). |
| `Reply`, `Error`, `max_request_bytes` 65536, `max_parameters` 32 | type / const | (10-16) |

## 3. Dependencies and layering

- Imports: none. `DB` is a comptime parameter.
- Imported by:
  - RAMSQL `src/server.sig:27`, only with `--insecure-local`
    (`server.sig:906-908`), listening on loopback (`:947`);
  - ZPM `tests/test_tds.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-TDS-1** (phase order):
  - the session moves `prelogin` (type `0x12`) → `login` (`0x10`) → `ready`;
  - a wrong packet type in the first two phases gives `InvalidState`;
  - batches, RPC and transaction-manager requests run only in `ready`
    (924-943).
- **INV-TDS-2** (reassembly):
  - `message_len ≤ 65536` (903);
  - continuation packets must keep the first packet's type and use
    consecutive ids modulo 256 (904, 913);
  - a non-final empty packet is rejected (906);
  - status bits other than EOM are rejected (899).
  FV-17.
- **INV-TDS-3** (parked request restore): if the final packet's request
  parks, `message_len` and `next_packet_id` return to their values before
  the call, and the earlier packets stay buffered for redelivery (907-908,
  944-952).
- **INV-TDS-4** (output packets): every output packet has type 4 and a
  length field equal to its bytes, at most `packet_size`. Only the last
  packet carries EOM (153-178, 953).
- **INV-TDS-5** (substitution safety): `expand` replaces each `@name` outside
  `'…'`, `"…"`, `[…]`, `--` and nested `/* */` with exactly one literal token
  whose value equals the parameter (720-780). **False today** (NET-01). FV-03.
- **INV-TDS-6** (no silent plaintext downgrade): if the client asks for
  encryption ON (1) or REQ (3), the server answers ENCRYPT_NOT_SUP and closes
  before LOGIN7 (316-321).

## 5. Current status and evidence

- python-tds works end to end (`tools/test_clients.py`): batches, RPC
  parameters including quotes and Hebrew text, transactions and rollback.
- Unlike the PostgreSQL scanner, RPC expansion honors brackets and nested
  block comments (725-759), so NET-02 does not apply here.
- All LOGIN7 offset and length pairs are bounds-checked before any field is
  read (324-330, 343). TDS versions other than 7.2-7.4 get error 18456 (335-341).
- Error mapping in batch and RPC (523-530, 845-852): 208, 207, 2627, 515,
  1222; anything else becomes 102.

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`.
Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-01 | critical | security | `literal` renders integer parameters as bare decimal text with a leading `-` (`tds.sig:673-689`). `expand` splices them at `@name` (760-773). The adjacency check at 761 rejects only a name byte before `@`. So `SELECT 1-@p` with `@p = -1` becomes `SELECT 1--1`, a comment. | Emit `(-1)` now; typed binding later (see `postgres.md`). | B2 |
| NET-05 | medium | security | PRELOGIN always answers ENCRYPT_NOT_SUP (318). LOGIN7 accepts any username with an empty password (344-349). Safety rests on `--insecure-local` and loopback (RAMSQL `server.sig:906-908`). | Authenticate over TLS (TDS 8 / strict), or refuse outside loopback. | B3 (TLS), B2 (credentials) |
| NET-07 | medium | robustness | `results` hits `else => unreachable` for cells that `textify` did not convert (502). `textify` skips integer, integer32, boolean and text columns without checking their cells (ZPM `sql.sig:297-313`). Undefined behavior in ReleaseFast. Reachability is unverified. | Return error 245 instead. | B2 |
| NET-11 | low | security | `@errorName` appears in client messages (512, 518, 521, 530, 836, 840-841, 852, 871, 886, 889). | Fixed messages. | B1 |
| NET-13 | low | correctness | `results` rejects stored text that is not valid UTF-8 (441-447, through `utf8Next` 28-46). A row written with invalid bytes over PostgreSQL, MySQL or COPY cannot be read back over TDS. | Validate on input in every codec. | B1, B2 |
| NET-14 | low | conformance | LOGIN7 with fExtension or fChangePassword is refused (344). Modern Microsoft drivers set fExtension (inferred; only python-tds is tested). Status bits RESETCONNECTION, RESETCONNECTIONSKIPTRAN and IGNORE are rejected (899), which breaks pooled clients. NVARCHAR columns declare a 65534-byte maximum (469); the non-PLP limit is 8000. BEGIN's isolation level 0-4 is accepted and ignored (865-867). ATTENTION only answers DONE_ATTN (938) and cannot cancel a parked request, because the parked request is redelivered first (inferred). | Implement minimal FEATUREEXT negotiation and connection reset. Use PLP NVARCHAR(MAX) for long text. Reject unsupported isolation levels. Handle attention out of band. | B2 |
| NET-18 | medium | testing | The fuzz loop only sends random PRELOGIN payloads (`test_tds.sig:462-476`). LOGIN7, RPC and reassembly are not fuzzed. | Fuzz targets in section 8. | B1, B2 |

## 7. Security considerations

- **Trust boundary:** every byte is attacker-controlled; any local process
  can connect with `--insecure-local`. There are no roles (B4).
- **Bounds:**
  - all packet, LOGIN7, ALL_HEADERS, RPC and transaction-manager fields are
    checked before reading (`Cursor.take` 124-129; 305; 328; 389-404; 862-878);
  - the PLP chunk loop is bounded by the 64 KiB expansion buffer (586-596);
  - UTF-16 decoding rejects unpaired surrogates (58-95).
- **Gaps observed, no finding id:**
  - the declared NVARCHAR(n) length is parsed (635-643) but not enforced
    against the value;
  - U+0000 is accepted inside text and passed into SQL text (unverified
    whether the lexer handles it).
- **Denial of service:**
  - each session embeds four 64 KiB buffers plus a `DB.Result`;
  - the request size is capped at 64 KiB;
  - idle handling belongs to RAMSQL (NET-04).
- No secrets are handled.

## 8. Tests

**Existing.** ZPM `tests/test_tds.sig` (489 lines):

- `loginAndFragmentation` (132): every PRELOGIN truncation returns "need
  more"; a batch split mid UTF-16 code unit across two packets; ATTENTION
  answers DONE_ATTN.
- `typedRowsAndErrors` (156): INTN, BITN and NVARCHAR metadata; the i64
  minimum; Hebrew and emoji text; errors 2627 and 208.
- `outputFragmentation` (196): a large result splits into packets of at most
  512 bytes.
- `transactionManagerAndPreflight` (220): begin, commit and rollback
  ENVCHANGE tokens; a short output buffer is rejected before execution.
- `concurrentCommittedVisibility` (254): a parked writer consumes and writes
  nothing.
- `rpcParameters` (358):
  - the string `x'); DROP TABLE binds; -- 😀 @P1` is stored literally;
  - out-of-order named parameters are accepted;
  - `'@P2'` inside a literal is not substituted;
  - duplicate, missing and unsupported parameters are rejected;
  - every truncation of the RPC stream is rejected with nothing executed.
- `malformedAndFuzz` (433): requested encryption closes; a password and an
  unknown database are refused; an unpaired surrogate, a zero header length
  and a sequence gap are rejected; 1024 random PRELOGIN payloads.

**Existing, elsewhere:** RAMSQL `tools/test_clients.py` (python-tds section).

**Missing:**

- [ ] B2: regression test for NET-01: `x-@p` with negative SMALLINT, INT and
  BIGINT values. TINYINT is unsigned (555).
- [ ] B2: `expand`-versus-lexer property (the FV-03 oracle) with random
  templates containing quotes, brackets, comments and `-` or `@` adjacency.
- [ ] B2: coverage-guided fuzzing of `handle` from each phase, with
  structure-aware LOGIN7 (offset table) and RPC (types, PLP chunks)
  generators. Oracles: no safety trap, INV-TDS-2 and INV-TDS-4, and a
  parseable response token stream.
- [ ] B2: reassembly invariance: every split of one message into packets
  gives the same response.
- [ ] B2: a parked request spanning several packets is restored exactly
  (INV-TDS-3).
- [ ] B7: client matrix: pyodbc with msodbcsql 18, Microsoft.Data.SqlClient
  and mssql-jdbc. Expect the NET-14 failures until they are fixed.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-03 | Lean + oracle | INV-TDS-5 | false today (NET-01) |
| FV-17 | Lean `Proto/Framing` + fuzz | INV-TDS-2 and INV-TDS-4 | todo |
| (none) | enumeration | INV-TDS-1 and INV-TDS-6 over the packet types {0x12, 0x10, 1, 3, 6, 0x0e, other}, depth 4 | todo |
| (none) | property | INV-TDS-3 parked restore | todo |

## 10. Benchmarks

- No suite measures TDS. It is a compatibility surface. Add a python-tds
  smoke throughput case to S3 only after NET-01 and NET-14 are fixed, as an
  informational case with no gate.

## 11. Open questions

- Keep TDS in 1.0, and if so, add TDS 8 (TLS first) in B3 or stop at 7.4
  with loopback only?
- Isolation levels: reject anything other than READ COMMITTED, or map
  SNAPSHOT and SERIALIZABLE once B8 provides them?
- Attention handling: should RAMSQL scan the input for ATTENTION while a
  request is parked, as it does for PostgreSQL CancelRequest?
