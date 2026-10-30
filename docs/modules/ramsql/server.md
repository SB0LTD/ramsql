# Module: `server`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/server.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL |
| Size | 1074 lines, 55.5 KB |
| Owner lane | `A6` (decomposition, testability); `B5`, `B6`, `B9`, `C5` for listed findings |
| Status | beta |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here). Lines 137-162 and 266-314 put several statements on one line |

> **Wave 0 update (2026-10-30).** SRV-01 is fixed on RAMSQL branch `b5/srv01-create-data`, which needs the ZPM `db_file` change in the same pin bump. Durable startup calls `persistence.initExisting`. A missing file without `--create-data` exits with `DataFileMissing` and a message naming the absolute path, before any listener opens. `--create-data` creates the file. The opened path is printed as `(opened)` or `(created)`. Follow-up: `absolutePath` calls `ntdll` and `getcwd` directly from product code; this should move to ZPM. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.

## 1. Purpose and scope

This is the hosted server policy layer. It binds loopback listeners, admits
connections through a bounded queue, runs one or more socket reactors, and
dispatches PostgreSQL, MySQL and TDS sessions (codecs in ZPM) against one shared
`Store`. It also loads the SCRAM verifier, opens the WAL through `persistence`,
charges page regions to a byte ledger, prints `--capabilities`, and runs offline
backup. It does not parse SQL, implement MVCC or encode wire messages; those
belong to ZPM. It is not built for native SB0 (see `platform-sb0.md`).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Protocol` (37) | enum | `postgres`, `mysql`, `mssql` |
| `table_capacity` (38) | const | compiled table slots (`profile.tables`) |
| `Options` (39-67) | struct | parsed runtime options; defaults include `data = "ramsql.wal"`, `idle_ms = 30000`, `workers = 1` |
| `default_timezone_dir` (68) | const | `/usr/share/zoneinfo` on Linux/macOS, else null |
| `describe` (822) | fn | validates options, prints the capabilities JSON; opens no files or listeners |
| `describeMachine` (851) | fn | prints the machine plan through `capacity.describe` |
| `run` (869) | fn | full server lifecycle; returns only on error |
| `backup` (1061) | fn | offline: replay `options.data`, then `backup.create` |

## 3. Dependencies and layering

- Imports: `sig`, `std`, `profile` (generated), ZPM `sql`, `db_store`, `postgres`,
  `mysql`, `tds`, `scram`, `entropy`, `tcp_server`, `wall_clock`, `page_region`,
  `worker_thread`, `resource_budget`, `budgeted_region`; local `persistence.sig`,
  `timezones.sig`, `capacity.sig`, `backup.sig`, and `ramsql` (version string only).
- Imported by: `src/main.sig` only.
- Layer rule: no violation found. ZPM does not import RAMSQL.

## 4. Invariants

- **INV-SRV-1** Slot state machine: only the acceptor moves a slot 0→1→2
  (`configureClient`, 621, 669). Only the owning reactor moves 2→1 under
  `lockStore` and then to 3 (parallel) or 0 (`Client.close`, 194, 207), and 3→0 (717).
  A slot is never reused while it holds a socket or a page region.
- **INV-SRV-2** Lock order is `data_gate` (write) then `store_mutex` (372). The
  only other acquisition of `store_mutex` is `tryLock` (719). `memory_mutex` is
  never held together with either.
- **INV-SRV-3** No detached read overlaps a mutation, publication, reclamation or
  zone-cache eviction. Every serialized dispatch and every `disconnect` holds the
  gate for writing; detached reads hold it for reading (286).
- **INV-SRV-4** Buffers: `input_start + input_len <= input.len` and
  `output_sent <= output_len <= output.len`, enforced by the `InvalidReply` checks
  (250, 253, 310).
- **INV-SRV-5** A cancel affects only the slot whose current `cancel_key` equals
  `secret << 1 | 1` (455, 668). The key is reset to 0 before a slot is reused (622).
- **INV-SRV-6** In durable mode, recovery completes before any listener exists
  (925 before 938).
- **INV-SRV-7** MySQL and TDS listeners exist only with `--insecure-local` (908).
  `--insecure-local` together with a verifier file is rejected (923).

## 5. Current status and evidence

The module works for the documented beta. There are no Sig tests for it. All
evidence is black-box Python against the built binary:

- `tools/test_server.py`: framing, SSL refusal, half-close, fatal frames, pool
  exhaustion (53300), idle expiry, running-query cancel.
- `tools/test_auth.py`: live SCRAM and fail-closed startup
  (`AuthenticationRequired`, `InvalidVerifier`, `--insecure-local` with a verifier).
- `tools/test_workers.py`: cancel during work, parallel reads with the writer gate,
  concurrent prepared reads, worker scratch reuse.
- `tools/test_machine_capacity.py`: 53200 under `--memory-budget`.
- `tools/test_clients.py`: psycopg, PyMySQL, python-tds.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-01 | high | robustness | `server.sig:48` default `"ramsql.wal"` relative to cwd; `server.sig:925` calls `persistence.init`, which creates a missing file (ZPM `db_file.sig:49-53`) | open existing by default; require an explicit create flag | B5 |
| SRV-02 | high | testing | 28 module-level `var`s (104, 356-379, 442, 469-472, 559, 674-677); no Sig test imports this file | wrap state in a `Server` struct, inject socket and clock, add contracts | A6 |
| SRV-03 | medium | robustness | two reactor loops: `run` 966-1057 and `Reactor.run` 700-774. The parallel loop wakes parked clients and delivers cancels only if `store_mutex.tryLock()` succeeds (719), with a fixed 10 ms poll (754). Delay under write contention is unbounded (size unverified) | one reactor for both modes; bounded wake scan | A6 |
| SRV-05 | medium | maintainability | `capacity = 60` (130), `worker_budget_each` 5 MiB (380) vs 4 MiB stacks (ZPM `worker_thread.sig:44,46`), `tcp.max_clients = 63` (ZPM `tcp_server.sig:15`), `transactions = 62` (`build.sig:129`) | one source constant plus `comptime` asserts | A6 |
| SRV-06 | medium | security (DoS) | every PG socket enters a 64-entry queue (559, 602-603) with a deadline of up to 1 s (887); a full queue closes new sockets silently | per-source limits, shorter deadline, counter and log | B6 |
| SRV-07 | medium | robustness | `serve` closes on any dispatch or flush error with no diagnostic (342-351); `InvalidReply` (250, 253, 310) looks like a hangup; failed SCRAM is not logged | bounded stderr diagnostics for invariant breaches and auth failures | B6 |
| SRV-10 | medium | maintainability | `describe` JSON is one 1,295-char format string with 50+ positional arguments (830-831) | struct-driven JSON writer | A6 |
| SRV-11 | low | security | verifier opened from cwd with no mode or owner check (909); `secureZero` (922) is skipped by `try` at 915 and 921 and by the return at 919 | `defer secureZero`; refuse group- or world-readable files | B5 |
| SRV-12 | low | maintainability | dead cancel paths: `pollInterrupts` scan 583-594, `dispatch` 214-218, postgres branch at 612. In single-thread mode 594 would call `disconnect()` while another statement runs | delete | A6 |
| SRV-13 | low | maintainability | option checks repeated at 823, 852, 870; `describeMachine` skips `idle_ms`; `run` reserves storage (888-893) before the auth check (907) | one `validate(Options)` run first | A6 |
| SRV-15 | low | completeness | refs = `max_rows * 4` (815); ZPM `ReadContext.backingBytesFor` rejects refs > 0x7fffffff (`sql.sig:386`), so above 536,870,911 rows `--capabilities` fails and readers retry every second (285) | cap `--rows` or size refs separately; fix CAPACITY.md | C5 |
| SRV-17 | low | completeness | ledger does not charge the overflow queue (559, about 4.2 MiB), `recovery_buffer` (361), replay scratch, zone cache (104) | document, or charge them at startup | C5 |
| SRV-18 | low | perf | zone index and TZif reads run under the global write lock (`lockStore` 220) | preload or read outside the gate | A6 |
| SRV-21 | low | robustness | 16 `@panic` sites on region-release or join failure (147-199, 432-434, 784, 865-893, 1066-1069) | keep fail-stop, but document it and count it in tests | A6 |
| NET-04 | medium | security (DoS) | any received byte refreshes `touched` (769, 1053); send progress refreshes it too (327); parked clients are exempt (742, 998); no authentication deadline after hand-off | auth deadline; idle-in-transaction limit | B6 |
| NET-09 | low | security | pid = slot + 1 (647); 32-bit secret (645-646); `cancelBackend` has no rate limit (450-463) | rate-limit cancels per source; accept protocol 3.2 longer keys when available | B6 |

SRV-04, SRV-08, SRV-09, SRV-14, SRV-16, SRV-19 and SRV-20 are on the
persistence, backup, platform, CLI, capacity and build-graph pages.

## 7. Security considerations

- Trust boundary: loopback only (938-947). Any local process can connect.
- Attacker-controlled bytes: every socket. This module itself parses startup
  length, SSLRequest/GSSENCRequest codes and CancelRequest in `OverflowCancel.poll`
  (512-557). Everything else goes to the ZPM codecs.
- Secrets: verifier bytes (SRV-11), per-slot cancel secrets, SCRAM nonces
  (24 random bytes, 650).
- DoS surface: 60 slots, admission queue (SRV-06), idle rules (NET-04), cancel
  spraying (NET-09), gate starvation (SRV-03), file I/O under lock (SRV-18).
- MySQL and TDS have no authentication and are gated only by `--insecure-local`.
  No Python test asserts `AuthenticationUnsupportedForProtocol` (908).

## 8. Tests

- **Existing:** none in Sig. Python: see section 5.
- **Missing:**
  - [ ] A6: in-process contract for the slot state machine with a fake socket layer (INV-SRV-1, INV-SRV-5).
  - [ ] A6: fuzz `Client.dispatch` with a fake session returning arbitrary `Reply` values (INV-SRV-4).
  - [ ] B5: startup refuses MySQL/TDS without `--insecure-local` (908).
  - [ ] B6: slowloris, a full admission queue, and a cancel storm against a live binary.
  - [ ] C2: worker run that records gate events for FV-16 trace validation.
  - [ ] B6: parked client's `lock_timeout` fires under sustained writes with `--workers 4` (SRV-03).

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| FV-16 | TLA+ `RwGate` | INV-SRV-2 and INV-SRV-3; every waiting reader and writer eventually proceeds. The `tryLock` wake path (SRV-03) must be modeled. | todo |
| FV-09 | TLA+ `AdmissionLedger` + property | grants never exceed the limit; every failed admission returns its grant | todo |
| FV-10 | Lean + enumeration | no SQL before authentication. The server must not bypass the codec; `dispatchRead` calls the same `session.handle` (309) | todo |
| INV-SRV-1 | TLA+ | slot ownership is exclusive and reuse is safe, with N reactors plus the acceptor | todo |
| INV-SRV-4 | fuzz | buffer bounds under arbitrary replies | todo |

## 10. Benchmarks

S3 (wire), S4 (durable writes) and S5 (1-64 clients, 1-8 workers) exercise this
module. Metrics: connect latency (the queue adds up to about 10 ms per round trip
in parallel mode, 798), p99 under contention, CPU per query by worker count.
Thresholds follow `docs/benchmarks/METHODOLOGY.md`.

## 11. Open questions

- Should the parallel reactor block on `store_mutex` for the wake scan, or should
  parked clients move to a store-owned wake queue?
- Is fail-stop on region-release failure (SRV-21) the policy for production?

### Proposed split (lane A6)

| New file | Contents (current lines) |
| --- | --- |
| `server/options.sig` | `Options`, one `validate`, `storagePlan` (39-67, 679-682, 803-820) |
| `server/capabilities.sig` | `describe`, `describeMachine` with a struct-based JSON writer (822-860) |
| `server/memory.sig` | ledger state, `initializeMemory`, `refreshMemoryBudget`, `clientMemoryAvailable`, `responseBytes` (374-426) |
| `server/client.sig` | `Client`, `Reply`, `Session`, dispatch, `dispatchRead`, flush, serve (133-355) |
| `server/admission.sig` | `OverflowCancel`, accept, `configureClient`, `provisionClient`, `cancelBackend` (428-671) |
| `server/reactor.sig` | one `Reactor` for 1-16 workers, plus `runParallel` (673-801 and 966-1057) |
| `server.sig` | `Server` struct; `run` and `backup` orchestration only |

Move `zoneInUse` and `refersTo` (106-120) next to `timezones.sig`.
