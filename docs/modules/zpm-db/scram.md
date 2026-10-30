# Module: `scram` (SCRAM-SHA-256 server)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/scram.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 251 lines, 14 KB |
| Owner lane | B3 (channel binding), C4 (FV-19) |
| Status | beta |
| Formatting (`sig fmt --check`) | `scram.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`scram.sig` is a bounded SCRAM-SHA-256 server (RFC 5802 and RFC 7677). It:

- derives or imports a verifier: StoredKey, ServerKey, salt and iteration count;
- runs the two server steps over caller buffers.

It does not:

- frame PostgreSQL messages (`postgres.sig` does);
- generate nonces (the caller passes one from a CSPRNG);
- provide TLS or channel binding. SCRAM-SHA-256-PLUS is deliberately not
  advertised (4).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `mechanism`, `max_message` 1024, `min_iterations` 4096, `max_iterations` 10,000,000 | const | (9-12) |
| `Error` | error set | `InvalidVerifier`, `InvalidNonce`, `AuthenticationFailed`, `AuthenticationState`, `BufferTooSmall`, `PasswordNormalizationUnsupported` (13) |
| `Verifier` | struct | username ≤ 64 bytes, salt ≤ 64 bytes, iterations, `stored_key`, `server_key` (15-22) |
| `Verifier.fromPassword(user, password, salt, iterations)` | fn | Runs PBKDF2-HMAC-SHA-256. Accepts printable ASCII passwords of 1 to 1024 bytes only, so that SASLprep is the identity. Intermediate keys are zeroed (27-41). |
| `Verifier.parse(user, text)` | fn | Imports PostgreSQL's `SCRAM-SHA-256$iter:salt$stored:server`. Input ≤ 512 bytes; base64 must be canonical; both keys must be 32 bytes (45-65). |
| `Verifier.format(out)` / `validate()` | fn | Produces the PostgreSQL text form. `validate` checks username 1-64 bytes with no NUL, salt 16-64 bytes, and iterations 4096 to 10M (67-81). |
| `Server.init(verifier, nonce)` | fn | Copies the verifier. The nonce must be 24 to 128 printable bytes with no comma (140-147). |
| `Server.setUsername(name)` | fn | Records whether the PostgreSQL startup user equals the verifier's user (151-153). |
| `Server.start(client_first, out)` | fn | Parses the client-first message and returns `r=…,s=…,i=…`. Moves `initial` to `challenge`, or to `failed` (155-188). |
| `Server.finish(client_final, out)` | fn | Verifies the proof and returns `v=…` (46 bytes). Moves `challenge` to `complete`, or to `failed` (190-222). |
| `Server.State` | enum | `initial`, `challenge`, `complete`, `failed` (124) |

## 3. Dependencies and layering

- Imports: `std` only. It uses SHA-256, HMAC-SHA-256, PBKDF2, base64,
  `timing_safe.eql` and `secureZero` (5-8).
- Imported by:
  - `postgres.sig:6`;
  - RAMSQL `src/server.sig:20`, which loads the verifier file and calls
    `Verifier.parse` (RAMSQL `server.sig:906-922`);
  - the ZPM tests `test_scram.sig` and `test_postgres.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-SCRAM-1** (proof check): `finish` succeeds if and only if all of the
  following hold.
  - The message parses with at least 3 fields, in the order `c`, `r`, …, `p`.
  - `c` is `biws` for a client-first message that began with `n` (no channel
    binding), and `eSws` for one that began with `y` (client supports channel
    binding).
  - `r` equals the combined nonce.
  - `p` decodes canonically to 32 bytes.
  - `SHA256(p ⊕ HMAC(StoredKey, AuthMessage)) = StoredKey`, compared in
    constant time. Here `AuthMessage = client-first-bare "," server-first ","
    client-final-without-proof`.
  - `identity_matches` is true.
  (194-213.) Maps to FV-19.
- **INV-SCRAM-2** (nonce): the combined nonce is the client nonce followed by
  the server nonce. The client nonce is 1-256 bytes and the server nonce
  24-128 bytes, both printable `0x21`-`0x7e` without `,` (142, 173, 176-178,
  236-239). FV-19.
- **INV-SCRAM-3** (state machine):
  - `start` requires `initial` and `finish` requires `challenge`;
  - an error after that check sets `failed`;
  - `complete` and `failed` are absorbing;
  - a too-small output buffer is reported before the state check and leaves
    the state unchanged (156-158, 191-193, 186, 220).
- **INV-SCRAM-4** (canonical encoding): `decode` accepts a base64 string only
  if re-encoding the decoded bytes reproduces it. Every accepted proof and
  key therefore has exactly one encoding (241-251).
- **INV-SCRAM-5** (bounded transcript):
  - client-first-bare ≤ 512 bytes, combined nonce ≤ 384 bytes, server-first
    ≤ 512 bytes;
  - every copy and `bufPrint` fits its fixed field (128-135, 159, 173-184).
- **INV-SCRAM-6** (verifier validity): every `Verifier` that `Server.init`
  accepts satisfies `validate` (141, 78-81).

## 5. Current status and evidence

- **Verified by code review:**
  - constant-time proof comparison (212); canonical base64 only (246-249);
  - `m=` extensions (110), duplicate attributes (106-111) and control
    characters (112) are rejected;
  - authzid and `p=` channel binding are rejected; the GS2 header must be
    `n,,` or `y,,` (159);
  - no PBKDF2 at login, so the iteration count cannot drive CPU denial of
    service;
  - a wrong startup user fails only after the full proof computation (213),
    so there is no username oracle by response or timing.
- **Interoperability:** libpq through psycopg logs in (RAMSQL
  `tools/test_auth.py`).
- **RFC vector:** the RFC 7677 vector passes (`test_scram.sig:31-47`).

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`.
Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-05 | medium | security | SCRAM runs without TLS or channel binding. `postgres.sig:409-412` refuses SSL, and `scram.sig:4` and `:196` support only `n` and `y` GS2 flags. A passive local observer of a full exchange can run an offline dictionary attack on the password. An active relay is not detected. | TLS 1.3 plus SCRAM-SHA-256-PLUS with `tls-server-end-point`. | B3 |

No other finding is owned by this module.

## 7. Security considerations

- **Attacker-controlled inputs:** the client-first and client-final messages,
  both before authentication. The startup username is also attacker-chosen.
- **Information disclosed by design:** salt and iteration count go to any
  client that starts a login. RAMSQL has one verifier, so every username sees
  the same salt.
- **API hazard:** `identity_matches` defaults to `true` (127). A caller that
  never calls `setUsername` authenticates any startup user.
  `postgres.sig:430` always calls it.
- **Downgrade rule for B3:** once `-PLUS` is advertised, a client-first
  message with the `y` flag must be rejected (RFC 5802 §6). Today `y` is
  correctly accepted (159, 196).
- **Secrets:** the salted password, client key, proof, signatures and HMAC
  state are zeroed after use (33-36, 200-216, 226). The verifier copy inside
  `Server` is not; whether callers clear session memory on close is
  unverified.
- **SASLprep:** absent. `fromPassword` refuses passwords that are not
  printable ASCII (31). Verifiers made by PostgreSQL, which applies SASLprep,
  import correctly through `parse`.
- **Online guessing:** there is no attempt limit or delay. Each guess costs
  one TCP connection. This belongs to the B6 timeout work (see NET-04).

## 8. Tests

**Existing.** ZPM `tests/test_scram.sig` (217 lines):

- `rfcVector` (31): RFC 7677 vector, format/parse round trip, exact challenge
  and signature, no second `start` or `finish`.
- `positiveVariants` (71): `n=` variants, escaped names, the `y` flag,
  extensions, and the maximum nonce (256 + 128) and salt (64) sizes.
- `credentialFailures` (106): wrong startup user, every proof character
  flipped, replay against a fresh nonce, an edited `n=`, and `m=` (with
  `failed` absorbing).
- `invalidConfiguration` (138): nonce and verifier bounds, non-ASCII
  passwords, malformed verifier texts, and small buffers leaving the state
  unchanged.
- `malformed` (162): 15 bad client-first and 9 bad client-final messages,
  and 2 KiB inputs.
- `fuzz` (184): 8192 seeded inputs to `start` and `finish`; the state is
  `failed` after every rejection.

**Existing, elsewhere:**

- ZPM `test_postgres.sig` `authenticationContracts` (562), over the wire
  codec.
- RAMSQL `tools/test_auth.py`: Python-derived verifiers and proofs, unique
  nonces, replay rejected, no SQL before auth, libpq login, and startup
  refusing a missing or invalid verifier.

**Missing:**

- [ ] C4: Lean model of `finish` plus generated vectors (FV-19), replayed
  through `Server.start` and `Server.finish`.
- [ ] B3: differential against an independent SCRAM client (Python `scramp`
  or libpq) over random usernames, nonces, salts, iteration counts and
  extensions.
- [ ] B3: statistical timing test of `finish` for valid versus invalid proofs
  (FORMAL §5: timing is checked by review and statistics).
- [ ] B5: fuzz `Verifier.parse`, with the round trip
  `parse(format(v)) = v` as oracle.
- [ ] B3: channel-binding tests when `-PLUS` lands, including rejection of the
  `y` flag.
- [ ] B5: mutation testing of `start`, `finish` and `decode` against the 90%
  coverage target for SCRAM (STRATEGY §4).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-19 | Lean + vectors | INV-SCRAM-1 and INV-SCRAM-2; the RFC 7677 vectors pass | vectors pass today; model todo |
| FV-10 | Lean + enumeration | SASL messages are accepted only in the authentication phase (with `postgres.md` INV-PG-3) | todo |
| (none) | property | INV-SCRAM-4: `decode` is injective on accepted inputs | todo |
| (none) | fuzz | INV-SCRAM-3 and INV-SCRAM-5 under coverage-guided inputs | partial (LCG fuzz only) |

## 10. Benchmarks

- Login costs two HMACs and one SHA-256. PBKDF2 runs only in `fromPassword`,
  which RAMSQL does not call. RAMSQL imports verifiers made offline by the
  Python `tools/create_verifier.py`.
- No suite measures connection setup.
- Proposed: add login rate and p99 to S5 (concurrency) so that the TLS work in
  B3 can show its cost. There is no gate until a baseline exists.

## 11. Open questions

- When roles arrive (B4), unknown users need a deterministic fake salt and
  iteration count so that the salt does not reveal which users exist. Who
  owns that, B4 or B5?
- Implement SASLprep, or keep refusing non-ASCII passwords in `fromPassword`?
- Should session teardown zero the verifier copy held in `Server`?
- Online guessing limits: should there be a per-source delay or lockout after
  failed logins? Owner: B6.
