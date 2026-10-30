# Threat model

Scope: RAMSQL `origin/main` 6d677ee with ZPM `2f060c2`, hosted targets
(Windows, Linux, macOS). Native SB0 builds use a trusted memory channel and
are out of scope except where noted. Findings ids refer to the register in
[`PLAN.md`](../../PLAN.md) §3. Lane **B6** keeps this document current.

## 1. Assets

| Asset | Why it matters |
| --- | --- |
| Table data in memory and in the WAL | confidentiality and integrity of user data |
| Committed-transaction durability | an acknowledged commit must survive a crash |
| SCRAM verifier file | offline guessing target if leaked |
| Availability of the server | 60-slot connection ceiling, shared memory budget |
| Host integrity | the server must not let a client read or write host files or memory |

## 2. Actors

| Actor | Capability today |
| --- | --- |
| Remote network attacker | none while listeners bind only to 127.0.0.1. Becomes the main actor once TLS lands and remote binding is allowed. |
| Local unprivileged process | can connect to every loopback listener. With `--insecure-local`, has full SQL access on PostgreSQL, MySQL and TDS. Without it, must pass SCRAM on PostgreSQL. |
| Browser or SSRF-capable local app | can reach loopback ports through a vulnerable local service. |
| Authenticated but untrusted SQL client | every authenticated user is all-powerful: there are no roles or privileges. |
| Application passing user input as bind parameters | controls parameter **values** only, which should be safe. Today it is not (NET-01). |
| Someone with filesystem access | can read or replace the WAL and verifier, or fill the disk. |

## 3. Entry points and threats (STRIDE)

### 3.1 PostgreSQL listener

| Threat | Status | Finding / lane |
| --- | --- | --- |
| **Tampering**: bind-parameter values change statement structure. Integers are spliced as bare text, so `x-$1` with a negative value becomes a `--` comment and drops the rest of the statement, including `WHERE`. | **open, critical** | NET-01 / B1 |
| **Tampering**: the placeholder scanner ignores nested `/* */` comments while the lexer honors them, so substitution targets the wrong positions | open | NET-02 / B1 |
| **Information disclosure**: no TLS. Credentials exchange (SCRAM) and all data are cleartext. No channel binding. | open | NET-05 / B3 |
| **Spoofing**: weak cancel keys (predictable pid, 32-bit secret, no rate limit) let a local process cancel others' queries | open, low | NET-09 / B6 |
| **Denial of service**: slowloris (idle timer refreshed by any byte, no authentication deadline), parked lock holders exempt from idle timeout, 60-slot ceiling, 64-entry admission queue | open | NET-04 / B6 |
| **Information disclosure**: internal error names (`@errorName`) sent to clients before authentication | open, low | NET-11 / B1 |
| **Elevation of privilege**: no roles, so any authenticated user can run DDL and read every table | open | A02 gap / B4 |
| Execution before authentication | verified closed by code review (dispatch, COPY and oversize drain all require completed auth). Must be locked in by FV-10 and the enumeration test. | C4 |
| SCRAM correctness | verified: constant-time proof check, canonical base64, nonce rules, bounded iterations, no username oracle. Keep with RFC vectors and FV-19. | C4 |

### 3.2 MySQL and TDS listeners

| Threat | Status | Finding / lane |
| --- | --- | --- |
| **Spoofing**: no authentication at all; any username accepted; static MySQL scramble | open by design, gated by `--insecure-local` | NET-05, NET-12 / B2 |
| **Tampering**: TDS `sp_executesql` substitution splices integers like PostgreSQL | open | NET-01 / B1, B2 |
| **Denial of service**: `unreachable` on unexpected cell variants is undefined behavior in ReleaseFast | open | NET-07 / B2 |
| Encryption | PRELOGIN replies ENCRYPT_NOT_SUP; MySQL rejects the SSL capability | B3 |

### 3.3 Filesystem

| Threat | Status | Finding / lane |
| --- | --- | --- |
| **Tampering**: corrupt or truncated WAL | recovery validates and fails closed. Needs exhaustive crash-point and torn-write tests. | B9, FV-05 |
| **Denial of service** by any authenticated user: two concurrent `CREATE TABLE` transactions committed out of OID order produce a WAL that replay rejects, so the next restart fails | **open, critical** | STO-01 / B9 |
| **Denial of service**: `FOR SHARE` then `FOR UPDATE` on many rows overflows the lock list (panic, or memory corruption in ReleaseFast) | open, high | STO-03 / B8 |
| A torn final record of full length (power loss during a multi-write append) refuses startup | open, high | STO-05 / B9 |
| **Information disclosure**: verifier file readable by other users | the server does not check file permissions | SRV finding / B5 |
| **Denial of service**: disk full or failing fsync | writes disable after a failure; needs injected-fault tests and an operator-visible health signal | B9 |
| Unbounded WAL growth (no checkpoint) makes recovery time and disk use grow without limit | open | STO finding / C8 |

### 3.4 Command line and configuration

| Threat | Status | Finding / lane |
| --- | --- | --- |
| Budget arithmetic overflow from large `--rows`, `--table-rows`, `--memory-budget` | to be property-tested | FV-09 / C5 |
| `--insecure-local` used outside a disposable environment | the flag name warns, but nothing stops it combining with persistent data. Decide policy. | B5 |

## 4. Security requirements before any non-loopback deployment

1. NET-01 and NET-02 fixed, with FV-03 proved and linked.
2. TLS 1.3 on PostgreSQL with SCRAM-SHA-256-PLUS channel binding (lane B3).
3. Roles and privileges, at least login roles, table-level `SELECT`/`INSERT`/
   `UPDATE`/`DELETE`, and an owner model (lane B4).
4. Authentication deadline, idle-in-transaction timeout, per-source connection
   limits and reserved administrative slots (lane B6).
5. MySQL and TDS either authenticated over TLS or refused outside loopback
   (lane B2).
6. Fuzzing of every network parser running nightly with no open findings
   (lanes B1, B2).
7. `SECURITY.md` disclosure process published (lane A5).
