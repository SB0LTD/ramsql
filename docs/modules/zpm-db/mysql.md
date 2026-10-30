# Module: `mysql` (MySQL protocol 10 text endpoint)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/mysql.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 402 lines, 17 KB |
| Owner lane | B2 |
| Status | beta (local trust only) |
| Formatting (`sig fmt --check`) | `mysql.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`mysql.sig` speaks MySQL client/server protocol 10 with 4.1 handshake
responses. It is meant for explicitly trusted local clients.

- **Login:** accepts only an empty password; the username is ignored.
- **Commands:** COM_QUERY, COM_INIT_DB (database `ramsql` only), COM_PING and
  COM_QUIT.
- **Results:** text result sets only.
- **Not supported:** TLS, password authentication, prepared statements
  (COM_STMT_*), compression, multiple result sets and replication (1-8).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Session(DB)` | type fn | per-connection state: `authenticated`, `greeted`, `closed`, `result` (162-168) |
| `Session.begin(out)` | fn | Writes the initial greeting once. Fails with `InvalidState` if already greeted or closed (190-209). |
| `Session.handle(db, input, out)` | fn | Handles one packet: the handshake response, then commands. Returns `Reply{consumed, written, close, blocked}`. `consumed = 0` means "need more" (352-400). |
| `Session.responseCapacity` / `responseCapacityFor` / `requiredCapacity` | fn | Output needed before a request runs: `2 × result storage + 4096` (173-188). |
| `Reply`, `Error`, `max_request_bytes` 65536 | type / const | `Error` = `BufferTooSmall`, `MalformedPacket`, `RequestTooLarge`, `InvalidSequence`, `InvalidState` (10-14) |

## 3. Dependencies and layering

- Imports: none. `DB` and `DB.Result` are comptime parameters.
- Imported by:
  - RAMSQL `src/server.sig:26`, only when `--insecure-local` is set; without
    it, RAMSQL refuses MySQL and TDS ports (`server.sig:906-908`);
  - ZPM `tests/test_mysql.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-MYSQL-1** (no command before login):
  - the first packet after the greeting is always parsed as a handshake
    response (365-367);
  - before the greeting, or after close, every call fails with `InvalidState`
    (355).
- **INV-MYSQL-2** (framing):
  - a payload length above 65532 gives `RequestTooLarge` (358);
  - a reply consumes either 0 bytes or exactly `length + 4` (359, 364);
  - the sequence id must be 1 before login and 0 after (360);
  - responses start at sequence 2 or 1 (362).
  FV-17.
- **INV-MYSQL-3** (closed is absorbing): `closed` is set whenever a reply
  closes (398). Later calls fail with `InvalidState` (355).
- **INV-MYSQL-4** (handshake bounds): every NUL-terminated and length-encoded
  field of the handshake response is bounds-checked before it is read.
  `readLength` rejects `0xfb`, `0xff` and values above 65536. Trailing bytes
  give `MalformedPacket` (35-60, 227-274).
- **INV-MYSQL-5** (output preflight): the output capacity is checked before
  COM_QUERY executes (361), so a mutation never fails afterwards for lack of
  output space.

## 5. Current status and evidence

- PyMySQL works end to end against RAMSQL (`tools/test_clients.py`): login,
  CRUD, transactions and error codes.
- Error mapping (281-290):
  - 1146 / 42S02, 1050, 1054, 1062 / 23000, 1048, 1264, 1205;
  - every other engine error becomes 1064 / 42000.
- Column metadata uses native types only for BIGINT, INT and BOOLEAN. All
  other types travel as VAR_STRING text (316-330).
- Conformance gaps without a finding id:
  - no CLIENT_DEPRECATE_EOF, so EOF packets are used;
  - COM_STMT_*, COM_RESET_CONNECTION, COM_CHANGE_USER and COM_SET_OPTION
    return 1047 (394);
  - the connection id is fixed at 1 (196), so `KILL` cannot target a session;
  - CLIENT_MULTI_STATEMENTS is not advertised. Behavior with several
    statements in one COM_QUERY depends on the engine (unverified).

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`.
Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-05 | medium | security | There is no authentication. The username is skipped (`mysql.sig:228`); any non-empty password is refused (243-246); a client asking for SSL or compression is refused (223). Safety rests on `--insecure-local` plus loopback binding (RAMSQL `server.sig:906-908`, `:942`). | Authenticate over TLS, or refuse outside loopback. | B3 (TLS), B2 (credentials) |
| NET-07 | medium | robustness | `results` hits `else => unreachable` for any cell that is not null, integer, boolean or text (345). It relies on `Result.textify` (ZPM `sql.sig:297-313`), which skips integer, integer32, boolean and text columns without checking their cells. A mismatch traps in ReleaseSafe and is undefined behavior in ReleaseFast. Reachability is unverified. | Return an error (1105 / HY000) instead of `unreachable`. | B2 |
| NET-11 | low | security | Error text is `@errorName(err)` (291), exposing internal error names. | Fixed messages per error code. | B1 |
| NET-12 | low | security | The greeting's auth-plugin data is the constant `RAMSQL01` + `RAMSQLLOCAL1` (197, 204). It is harmless while passwords are refused, but must be random before any password support. Also: there is no AuthSwitchRequest, and requests over 64 KiB close the connection without an ERR packet (358). | Random 20-byte scramble per connection from `entropy`. Send ERR 1153 before closing. | B2 |
| NET-13 | low | correctness | COM_QUERY text goes to `db.execute` without UTF-8 validation (378). | Validate at the codec boundary. | B1, B2 |
| NET-18 | medium | testing | The fuzz loop only sends command bytes ≥ `0x80` after login (`test_mysql.sig:196-209`). The handshake response is never fuzzed. | Fuzz targets in section 8. | B1, B2 |

## 7. Security considerations

- **Trust boundary:** every byte is attacker-controlled, and any local process
  can connect once `--insecure-local` is used. It then has full SQL access;
  there are no roles (B4).
- **Bounds:** all reads go through `readLe` on slices whose length was checked
  first (55-57, 222, 357). No out-of-bounds read was found.
- **Denial of service:**
  - request size is capped at 64 KiB;
  - the session holds one `DB.Result`;
  - idle and slowloris handling is RAMSQL's (NET-04).
- No secrets are handled.

## 8. Tests

**Existing.** ZPM `tests/test_mysql.sig` (262 lines):

- `handshakeAndFragmentation` (85):
  - a too-small greeting buffer is rejected;
  - every truncation of the login packet returns "need more";
  - PING, and QUIT closes.
- `typedResultsAndErrors` (109):
  - column type bytes;
  - the i64 minimum value and multibyte UTF-8 round-trip;
  - 1062 and 42S02;
  - COM_STMT_PREPARE rejected;
  - INIT_DB to another database rejected.
- `transactionsAndOutputPreflight` (154):
  - `SET AUTOCOMMIT = 0`, the in-transaction status flag, ROLLBACK;
  - a short output buffer is rejected before the mutation runs.
- `malformedAndFuzz` (176):
  - a password is refused; an unknown database is refused;
  - a malformed handshake, an oversized length, a sequence mismatch and an
    empty command are rejected;
  - 1024 random command bodies.
- `concurrentCommittedVisibility` (212): a blocked writer reports `blocked`
  with nothing consumed.

**Existing, elsewhere:** RAMSQL `tools/test_clients.py` (PyMySQL section).

**Missing:**

- [ ] B2: coverage-guided fuzzing of `handle` from the greeted state, using a
  structure-aware handshake-response generator (capabilities, lenenc
  lengths, attributes). Oracles:
  - no safety trap;
  - INV-MYSQL-2 and INV-MYSQL-4;
  - `authenticated` is set only for an empty password.
- [ ] B2: differential test of handshake-response parsing against MySQL 8.0
  for the same bytes (accepted or rejected).
- [ ] B2: a test that the scramble differs across connections, once NET-12
  is fixed.
- [ ] B2: a result containing every engine type is rendered without a trap
  (NET-07).
- [ ] B7: client matrix: PyMySQL plus `mysql` CLI and Connector/J, with
  documented failures.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-17 | fuzz with framing oracle | INV-MYSQL-2 and INV-MYSQL-4 | todo |
| (none) | enumeration | INV-MYSQL-1 and INV-MYSQL-3 over the alphabet {handshake, QUERY, INIT_DB, PING, QUIT, unknown}, depth 4 | todo |

## 10. Benchmarks

- No suite in `docs/benchmarks/METHODOLOGY.md` measures the MySQL endpoint,
  and none is proposed until NET-05 is resolved. The endpoint is a
  compatibility surface, not a performance target.

## 11. Open questions

- Should the MySQL endpoint remain in 1.0? If so, which login:
  `caching_sha2_password` over TLS, or `mysql_native_password` with a random
  scramble, loopback only? Owners: B2 and B3.
- Should the username be checked against a configured user before roles
  exist (B4)?
