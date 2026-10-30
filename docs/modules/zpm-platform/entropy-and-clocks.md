# Module: `entropy` and `wall_clock` (OS randomness and time)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/platform/entropy.sig`, `zpm/src/platform/wall_clock.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 52 + 45 lines, 2.5 KB + 2.2 KB |
| Owner lane | B6 (with B5 for credential use) |
| Status | beta |
| Formatting (`sig fmt --check`) | `entropy.sig` clean; `wall_clock.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

**`entropy.sig`** fills a caller buffer from the operating system CSPRNG:

- Windows: `BCryptGenRandom` with the system-preferred RNG;
- Linux: `getrandom` with no flags;
- macOS: `getentropy`.

It fails closed. There is no userspace PRNG, no clock seed and no fallback
(1-5).

**`wall_clock.sig`** returns real time in microseconds since 1970-01-01 UTC:

- Windows: `GetSystemTimePreciseAsFileTime`;
- Linux and macOS: `clock_gettime(CLOCK_REALTIME)`.

Other targets get `ClockUnavailable`, so SQL `now()` fails instead of
inventing a time (1-7).

Neither module is used for timeouts. Idle, statement and admission deadlines
use the monotonic clock `sig.os.clockMonotonicNs` in RAMSQL
(`src/server.sig:465`), which is outside this page.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `entropy.fill(output)` | fn | Fills every byte, or returns an error with every byte zeroed. An empty buffer always succeeds (18-52). |
| `entropy.Error` | error set | `UnsupportedPlatform`, `EntropyUnavailable` (16) |
| `wall_clock.nowUs()` | fn | i64 microseconds since the Unix epoch, or `ClockUnavailable` (28-45). |
| `wall_clock.available` | const | true on Windows, Linux and macOS (22) |
| `wall_clock.Error` | error set | `ClockUnavailable` (19) |

## 3. Dependencies and layering

- Imports: `std` and `builtin`. External symbols: `bcrypt.BCryptGenRandom`,
  libSystem `getentropy`, `kernel32.GetSystemTimePreciseAsFileTime`, and libc
  `clock_gettime` (entropy 8-15; wall_clock 11-17).
- Imported by:
  - RAMSQL `src/server.sig:21` (`entropy`) and `:29` (`wall_clock`);
  - ZPM `src/db/store.sig` takes the clock only as an injected function
    pointer (`store.sig:327`, `:362`); RAMSQL installs it at `server.sig:898`;
  - ZPM tests `test_entropy.sig` and `test_wall_clock.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-ENT-1** (fail closed): `fill` returns success only after every output
  byte came from the OS CSPRNG. On any error, `errdefer` zeroes the whole
  buffer (21).
- **INV-ENT-2** (no weak fallback): on any target other than Windows, Linux
  or macOS, `fill` returns `UnsupportedPlatform` (50). The SB0 native build
  therefore has no SCRAM and runs in trust mode over a trusted channel
  (RAMSQL `sb0_app.sig:1-4`).
- **INV-ENT-3** (progress): each loop iteration either adds between 1 and
  `count` bytes to `done`, or returns an error. The only exception is an
  EINTR retry.
  - Linux: a return value of 0 or above `count` is treated as an error (37).
  - Windows: requests are clamped to 2³²−1 bytes per call (25).
  - Linux and macOS: requests are at most 256 bytes per call (33, 44).
- **INV-CLOCK-1** (no invented time): `nowUs` returns either a real-time
  reading or `ClockUnavailable`. On Windows, a FILETIME before 1970 is
  `ClockUnavailable` (32). Unsupported targets always fail (44).
- **INV-CLOCK-2**: `available` is true exactly for the targets where `nowUs`
  can succeed (22, 29-44). RAMSQL installs the store clock only when it is
  true (`server.sig:898`).

## 5. Current status and evidence

- **How RAMSQL uses entropy** (`src/server.sig`):
  - a 4-byte PostgreSQL cancel secret per connection (:643-647);
  - a 24-byte SCRAM server nonce, base64-encoded to 32 printable bytes
    (:648-654). That is 192 bits, above the ≥144 bits that `scram.Server.init`
    expects;
  - if either call fails, `configureClient` fails and the connection is
    refused (:640).
- **How RAMSQL uses the clock:** statement and transaction timestamps, through
  `wallClock` (`server.sig:123-126`). Authentication uses no clock; this is
  why there is no authentication deadline (NET-04).
- The tests in section 8 pass on the hosted targets, per their own
  contracts. They were not run in this audit.

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`
and RAMSQL `6d677ee`. Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-09 | low | security | Entropy quality is fine, but only 32 bits are drawn for the cancel secret (RAMSQL `server.sig:644-646`), and the pid is the slot number plus 1 (`:647`). Brute force is limited only by connection rate. | Rate-limit CancelRequests. Draw 256-bit keys when protocol 3.2 is supported. | B6 |

No finding is owned by `entropy.sig` or `wall_clock.sig` themselves.

## 7. Security considerations

- **Inputs:** no attacker-controlled input reaches these modules.
- **Blocking:** `getrandom` with no flags blocks until the kernel pool is
  initialized. In single-threaded RAMSQL, `configureClient` runs on the
  reactor thread, so an early-boot block would stall every client.
  Practically this happens only right after boot (unverified).
- **Output handling:**
  - SCRAM nonces never repeat across connections, as observed by
    `test_entropy.sig` and RAMSQL `tools/test_auth.py`;
  - the cancel secret is compared with XOR on the single-reactor path
    (`server.sig:444-447`, `:461`). The parallel path compares the 64-bit key
    with `!=` (`:455`), which is not constant-time. The leak over a socket is
    negligible.
- **Wall-clock steps:** time can move backward after an NTP adjustment.
  `now()` can therefore go backward across statements. This is acceptable for
  PostgreSQL compatibility, but must never be used for ordering or deadlines.

## 8. Tests

**Existing:**

- ZPM `tests/test_entropy.sig` (51 lines):
  - an empty buffer succeeds;
  - 1024 successive 32-byte fills are all distinct;
  - no write outside the slice (guard bytes);
  - at least 200 of 256 byte values appear in 4096 bytes;
  - sizes around the 256-byte chunk boundary work;
  - an unsupported platform returns `UnsupportedPlatform` with zeroed output.
- ZPM `tests/test_wall_clock.sig` (36 lines):
  - values fall between 2024 and 2100;
  - 2000 reads never step back by more than one second, and time advances;
  - an unavailable target returns `ClockUnavailable`.
- RAMSQL `tools/test_auth.py`: no SCRAM nonce is reused across connections.

**Missing:**

- [ ] B6: fault-injection test proving that an entropy failure refuses the
  connection with no session state left behind. This needs a test seam in
  `configureClient`.
- [ ] B6: cancel-key test after the NET-09 fix: rate limit enforced, and the
  new key length.
- [ ] B5: lint (STRATEGY L0) that forbids any non-`entropy` randomness source
  in credential paths.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| (none) | contract | INV-ENT-1 to INV-ENT-3 (`test_entropy.sig`) | partial (no fault injection) |
| (none) | contract | INV-CLOCK-1 and INV-CLOCK-2 (`test_wall_clock.sig`) | done for hosted targets |
| FV-12 | Lean + vectors | Civil-date conversion of `nowUs` values. This is in the temporal module, not here. | todo |

## 10. Benchmarks

- None needed. Each call is one syscall per connection (entropy) or per
  statement (clock).
- If profiling (C7) shows `nowUs` in statement hot paths, record it under S1
  micro.

## 11. Open questions

- Should the server read entropy once at startup, so it fails fast, and then
  derive per-connection values from a vetted DRBG? Or keep one syscall per
  connection, which is simpler and the status quo? Owner: B5.
- Native SB0: what is the entropy source when SB0 needs SCRAM? Out of scope
  for hosted 1.0.
