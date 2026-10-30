# Module: `tcp_server` and `stream_server` (transport)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/platform/tcp_server.sig`, `zpm/src/db/stream_server.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM platform-protocol (L1) |
| Size | 339 + 47 lines, 16 KB + 3 KB |
| Owner lane | B6 |
| Status | beta |
| Formatting (`sig fmt --check`) | `tcp_server.sig` clean; `stream_server.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

**`tcp_server.sig`** is a thin, non-blocking IPv4 TCP layer for
single-threaded reactors:

- listen, accept, recv, send and close;
- a readiness wait over at most 63 client sockets plus one listener.

It uses Winsock `select` on Windows, direct `poll` syscalls on Linux and
libSystem `poll` on macOS. All storage is caller-owned (1-4).

**`stream_server.sig`** runs one protocol session over any byte stream that
offers `read` and `writeAll`. Examples are a UART, a delegated channel, or an
accepted socket. It promises no listening, no authentication and no
durability (1-5).

Neither module enforces timeouts, connection limits or admission. RAMSQL
`src/server.sig` does that. NET-04 is recorded here because this page is the
transport page of the plan.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Socket`, `invalid_socket`, `max_clients` (63), `Error` | type / const | (12-29) |
| `init()` / `deinit()` | fn | Paired WSAStartup and WSACleanup on Windows; checks the platform elsewhere (32-41). |
| `listen(address, port)` | fn | Non-blocking listener, backlog 128. Port 0 picks an ephemeral port. SO_REUSEADDR on Linux and macOS only (45-79). |
| `localPort(fd)` | fn | Bound port (81-98). |
| `accept(listener)` | fn | Returns null when the queue is empty. Accepted sockets are non-blocking with TCP_NODELAY. Retries on EINTR and ECONNABORTED (102-147). |
| `recv(fd, buf)` / `send(fd, buf)` | fn | Return null on would-block. `recv` returns 0 for an orderly EOF. Retry on EINTR. Linux uses MSG_NOSIGNAL; macOS uses SO_NOSIGPIPE (151-212, 323). |
| `close(fd)` | fn | Never retried, so a reused descriptor is never closed (214-225). |
| `wait(...)` / `waitMasked(listener, clients, readable, writable, timeout_ms)` | fn | Readiness with caller masks. `InvalidSet` when the slices disagree or exceed 63 entries. EINTR returns no readiness (232-310). |
| `stream_server.Server(Database, Protocol, input_capacity)` | type fn | Fixed input buffer plus `Protocol.responseCapacity()` output buffer (6-13). |
| `Server.run(io, database, session)` | fn | Loops: handle → write → compact → read, until close, EOF or error (16-45). |

## 3. Dependencies and layering

- `tcp_server` imports `std`, `builtin` and ZPM `win32.sig` (5-7).
- `stream_server` imports nothing; `Database` and `Protocol` are comptime
  parameters.
- Imported by:
  - RAMSQL `src/server.sig:24` (`tcp_server`);
  - RAMSQL `src/platform/sb0_app.sig:8,14` (`stream_server` hosting the
    PostgreSQL codec on SB0);
  - ZPM tests `test_tcp_server.sig`, `test_db_stream.sig` and
    `test_pg_stream.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-TCP-1** (fd_set bound): `waitMasked` rejects sets with more than 63
  entries or with mismatched lengths (243). A Winsock `fd_set` holds 64
  entries (`win32.sig:606`), so the listener plus 63 clients always fits.
  `add` itself has no bounds check (327-330), so this check is load-bearing.
- **INV-TCP-2** (I/O results):
  - `recv` and `send` return null only for would-block;
  - `recv` returns 0 only for EOF with a non-empty buffer;
  - otherwise they return a count ≤ `buf.len`.
  The last part relies on the OS (unverified) (151-212).
- **INV-TCP-3** (no signals, no inheritance):
  - accepted sockets are non-blocking;
  - close-on-exec is set on Linux and macOS (56, 118, 321);
  - a send to a closed peer never raises SIGPIPE (195, 323).
- **INV-TCP-4** (slot lifecycle, enforced in RAMSQL `src/server.sig`):
  - each client slot moves 0 (free) → 1 (being configured, :621) → 2 (live,
    :669) → 3 (closed, parallel mode, :207) → 0 (:717);
  - no two live slots hold the same socket;
  - a cancel key reaches only the current generation of a slot.
  Not in the FV catalog (proposed for C2, together with `postgres.md`
  INV-PG-8).
- **INV-STREAM-1** (stream framing):
  - a reply is used only if `consumed ≤ used` and `written ≤ output.len` (21);
  - `consumed = 0` with `written ≠ 0` is an error (25);
  - unconsumed bytes are moved to the front in order (29-32);
  - a full buffer with no progress gives `RequestTooLarge` (36);
  - EOF with buffered bytes gives `UnexpectedEndOfStream` (39-41).
  FV-17.
- **INV-STREAM-2**: a `blocked` reply ends the stream session (24), because
  nothing else on a single stream can release the lock.

## 5. Current status and evidence

- Tested on loopback on all three hosted OSes (`test_tcp_server.sig`).
  RAMSQL's live suites `tools/test_server.py` and `tools/test_workers.py`
  drive the real reactors.
- RAMSQL policy built on these modules (`src/server.sig`):
  - loopback-only binding (:938, :942, :947);
  - at most 60 clients (:130);
  - idle timeout, default 30 s (:52);
  - a 64-entry PostgreSQL admission and cancel queue (:559) with a deadline
    of `min(idle_ms, 1000)` ms (:887).
- Only IPv4. No Unix-domain sockets, so no peer-credential check is possible.

## 6. Findings

Findings come from the 2026-10-30 audit, re-checked against ZPM `2f060c2`
and RAMSQL `6d677ee`. Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| NET-04 | medium | robustness | RAMSQL `server.sig:769` and `:1053` refresh `touched` on any received byte, and there is no authentication deadline. `:742` and `:998` exempt parked clients from the idle check (`:743`, `:999`), so a lock holder that trickles bytes parks other writers indefinitely. The admission queue has 64 entries (`:559`) with a 1 s deadline (`:887`), and new PostgreSQL connections are closed outright when it is full (`:603`). Slots are capped at 60 (`:130`). | Authentication deadline measured from accept; per-frame progress deadline; idle-in-transaction timeout; per-source limits; reserved admin slots. | B6 |
| NET-16 | low | correctness | `stream_server.sig:25` rejects `consumed = 0, written ≠ 0`. `postgres.sig:806` and `:811-812` produce that shape on COPY errors, so on SB0 (`sb0_app.sig:14`) a COPY error ends the session instead of returning ErrorResponse. | Accept output-only replies, or change the codec's reply shape. | B1 |
| NET-17 | low | security | The Windows listener does not set SO_EXCLUSIVEADDRUSE (`tcp_server.sig:46-54`). The test proves only that a plain duplicate bind fails (`test_tcp_server.sig:152-155`), not one with SO_REUSEADDR from another process. Whether port hijacking is possible is unverified. | Set SO_EXCLUSIVEADDRUSE on Windows and test it. | B6 |

## 7. Security considerations

- **Inputs:** remote bytes arrive only through `recv` into caller buffers;
  this layer never parses them.
- **Denial of service:** this layer has no accept-rate limit, keepalive or
  timeout. All limits are RAMSQL policy (NET-04).
- **Error handling:**
  - `accept` retries ECONNABORTED (109, 122, 136);
  - any other accept error returns `AcceptFailed`. RAMSQL breaks out of its
    accept loop on that error (`server.sig:601`).
- **Reactor wake-up:** a select or poll error other than EINTR returns
  `WaitFailed`. In RAMSQL that ends the reactor
  (`server.sig:1027` single-threaded; parallel reactors set `reactor_failed`).
  Whether that is the intended policy is an open question.

## 8. Tests

**Existing:**

- ZPM `tests/test_tcp_server.sig` (218 lines): a duplicate bind fails; an
  empty accept queue returns null; listener and client readiness;
  TCP_NODELAY; read-interest masking; bidirectional and empty I/O;
  mismatched and over-capacity sets are rejected; peer EOF; backpressure on
  one connection does not block accept (104-143).
- ZPM `tests/test_db_stream.sig` (82 lines): chunk sizes 1-16 give identical
  output; empty input; truncation (`UnexpectedEndOfStream`); overflow
  (`RequestTooLarge`); an invalid read count; read and write failures;
  buffers reusable after a failure.
- ZPM `tests/test_pg_stream.sig`: a full PostgreSQL session over the stream
  with chunk sizes 1 to 2048.
- RAMSQL `tools/test_server.py` and `tools/test_workers.py`: live reactors.

**Missing:**

- [ ] B6: slowloris test. A client sending one byte every `idle_ms - 1` ms
  before authentication must be closed by an authentication deadline
  (after the NET-04 fix).
- [ ] B6: admission-flood test. 64 or more silent connections must not delay a
  legitimate login beyond a stated bound.
- [ ] B6: a trickling lock holder must not park other writers forever (idle
  in transaction).
- [ ] B6: Windows test that a second socket using SO_REUSEADDR cannot bind
  the listener port (NET-17).
- [ ] B1: `stream_server` hosting a PostgreSQL COPY error returns
  ErrorResponse (NET-16).
- [ ] B6: property test of `Server.run` with a mock protocol returning random
  reply shapes and random read sizes. Oracle: INV-STREAM-1.
- [ ] C2 / B6: TLA+ model of slot lifecycle and cancel delivery (INV-TCP-4),
  with trace validation from `test_workers.py` runs.
- [ ] B9 / C7: L10 soak with connection churn, cancel storms and client kills.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-17 | Lean `Proto/Framing` + property | INV-STREAM-1 | todo |
| FV-09 | Lean + TLA+ `AdmissionLedger` | memory grants on RAMSQL's admission path (`provisionClient`) are returned on every failure. Related, owned by C5. | todo |
| (proposed) | TLA+ | INV-TCP-4 slot lifecycle and cancel generation | not in catalog |
| (none) | contract | INV-TCP-1 to INV-TCP-3 (partly covered by `test_tcp_server.sig`) | partial |

## 10. Benchmarks

- S3 (wire) and S5 (concurrency, 1-64 clients, 1-8 workers) include this
  layer's cost. METHODOLOGY §1 requires reporting network and native costs
  separately.
- Gate: nightly S2-S4, median regression above 5% (CI excluding 1.0) fails.
- NET-04 fixes add timers to the reactor loop. Compare S5 p99 before and
  after.

## 11. Open questions

- Raise the 63-client ceiling with epoll or IOCP, or keep select/poll and the
  60-slot cap for 1.0?
- Add Unix-domain sockets with peer-credential checks as the local-trust
  transport? This would narrow NET-05. Owners: B5 and B6.
- Should the transport layer own deadlines (an authentication deadline and a
  progress deadline), or only RAMSQL's reactor?
- Should `WaitFailed` stop the server, or only drop the offending socket?
