# Module: `store` (shared transactional store and connections)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/store.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Identical to `9606877`. |
| Layer | ZPM platform-protocol (L1) |
| Size | 1331 lines, 65 KB |
| Owner lane | B8 (simulation, trace hooks, waits); B9 for the commit-failure policy |
| Status | beta |
| Formatting (`sig fmt --check`) | `store.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`Store(Raw)` wraps one shared `sql.Database` (`Raw`) and gives each client a
`Connection`. The store owns:

- **Transaction-block state:** autocommit, BEGIN/COMMIT/ROLLBACK, savepoints,
  and the failed-block state 25P02.
- **The commit path, the same for autocommit and COMMIT:**
  - a read-only transaction publishes directly;
  - otherwise it runs `prepareCommit` → `commit_fn(payload)` →
    `publishCommit` (header 15-24, `commit` 371-397).
- **Parking:** a statement that returns `WouldBlock` is kept pending. The
  caller re-delivers the same bytes when `wakeReady()` reports progress. Then
  lock_timeout (55P03), statement_timeout and cancel (57014) and a one-time
  deadlock check (40P01) after `deadlock_timeout_ms = 1000` (215) are applied.
- **Session settings:** SET, SHOW, RESET and SET LOCAL, transactional as in
  PostgreSQL; TimeZone resolution.
- **COPY FROM orchestration** and the opt-in detached read route.

It does not do file I/O. Callers provide `commit_fn`, and RAMSQL's
`persistence.sig` installs it. It does not decide visibility (the facade
does). Callers serialize all default methods (header 1-4).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Store(Raw)` (282) | fn → type | Fields: `database`, `commit_ctx`/`commit_fn`, `encode_buffer`, `durability_required`, `storage_failed`, `now_ns`, clocks, zone provider (302-330). |
| `CommitFn` (294) | type | `fn (ctx, payload) anyerror!void`. Success must mean the payload is durable. |
| `Pending` (399-418) | struct | parked statement: fingerprint, implicit flag, wake epoch, timers, waits, COPY flag |
| `Connection` (423) | struct | per-client session; `tx` slot, `in_transaction`, `failed`, settings stacks, `read_context` |
| `execute` (1257), `describe` (843) | fn | Run or describe one statement or control command. A pending statement must be re-delivered with the same bytes (1105). |
| `beginTransaction`, `commitTransaction`, `rollbackTransaction`, `failTransaction`, `disconnect` (559-651) | fn | Block lifecycle. A COMMIT that fails ends the transaction (594-597). |
| `blocked`, `wakeReady`, `nextDeadline`, `waitStart`, `requestCancel` (653-688) | fn | Parking API for the reactor. |
| `copyDescribe`, `copyBegin`, `copyRow`, `copyResume`, `copyEnd`, `copyFail`, `copyBlocked` (1119-1255) | fn | COPY FROM over a statement subtransaction. |
| `detachedReadEligible`, `detachedReadSession` (515-525) | fn | Gate for the parallel ReadContext route. |
| `fingerprint`, `BatchIterator` (74-100) | fn / struct | FNV-1a of the statement bytes; legacy kind-1/2 batch framing. |

## 3. Dependencies and layering

- Imports: none at file level. `Raw` is a comptime parameter, the
  `sql.Database` type or a test double (282-291).
- Imported by:
  - RAMSQL `src/server.sig:28, 100` (`Store(Raw)`);
  - RAMSQL `src/persistence.sig:16` (`BatchIterator` for legacy replay);
  - ZPM `tests/test_db_store.sig`.

  The codecs (`postgres.sig:342`, `mysql.sig:352`, `tds.sig:893`) are generic
  over the connection type passed as `db`; they do not import this file.
- Layer rule: L1 depends on L0 only through `Raw`. No violation found.

## 4. Invariants

- **INV-STORE-1** (flush before publish): for a transaction with writes,
  `publishCommit` runs only after `commit_fn` returned success (386-396).
  Nothing becomes visible to another snapshot before that. FV-06.
- **INV-STORE-2** (poisoning): after `commit_fn` fails, `storage_failed` stays
  true until the owner reopens storage. Every later writing statement fails
  with `StorageUnavailable`: `run` passes `writes = false` (1053) and maps
  `ReadOnlyTransaction` (1063); COPY checks at 1164-1167. Reads continue.
- **INV-STORE-3** (re-delivery identity): a pending statement resumes only if
  the re-delivered bytes have the same fingerprint and no cancel is
  requested. Otherwise it is aborted with 57014 (1102-1113).
- **INV-STORE-4** (one deadlock check per wait): each distinct wait-for set
  starts a new wait with a fresh deadlock check and lock_timeout (1015-1026).
  The check runs at most once per wait (1089-1092).
- **INV-STORE-5** (terminal error policy, 983-995):
  - in autocommit, the implicit transaction is aborted;
  - in a block, `failScope` undoes back to the innermost savepoint, the block
    is marked failed (25P02) until ROLLBACK or ROLLBACK TO, and settings
    revert.
- **INV-STORE-6** (detached reads): the read route is used only with no
  transaction, no failure, autocommit on, nothing pending and no running
  statement (515-518, 845-846, 1260-1262). The caller holds the gate for the
  whole call.
- **INV-STORE-7** (read-only commits never persist): `commit` calls
  `commit_fn` only when `readOnly(slot)` is false (373-376).

## 5. Current status and evidence

`test_db_store.sig` (3,154 assertions) covers:

- `transactions` (READ COMMITTED, EvalPlanQual re-check);
- `durability`:
  - the COMMIT record carries all statements;
  - read-only statements never call the hook;
  - a hook error leaves the database and result untouched and poisons
    writes;
  - reads continue;
  - a failed COMMIT rolls back;
  - encode-buffer overflow gives 53200;
- `interleavings`, `framing`, `parking`;
- `timers`: lock_timeout 55P03, NOWAIT, a deadlock victim 40P01;
- `savepoints`, `runningControl`.

RAMSQL's isolation suite passes 44/44 (PLAN §1.3).

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-09 | medium | durability | When `commit_fn` fails, `commit` aborts the transaction in memory and sets `storage_failed` (390-394). But the failure may come after the record reached the file: write succeeded, fsync failed. `test_durable_log.sig` shows such a record is recovered at restart ("an uncertain commit is resolved by restart"). Until restart, readers see a database without that transaction, and after restart they see it. RAMSQL `OPERATIONS.md` documents that an unsuccessful COMMIT may have reached storage. | Treat a persist failure as "outcome unknown". Return a distinct SQLSTATE, stop serving reads as well as writes until restart, or re-read the WAL tail to decide. Record the choice in an ADR. | B9 |
| STO-02 | high | correctness | The store resumes parked statements and COPY openings (1112, 1141) and re-inserts parked COPY rows (1227). The engine then runs them with a snapshot that `horizon()` does not cover (`facade.md`). | Fix in the facade. In the store, keep the pending wait's snapshot visible to the engine until the statement finishes. | B8 |
| STO-14 | low | concurrency | Wait ordering for row locks comes from `locks.sig` (`locks.md`). The store re-delivers parked statements in caller order, with no fairness of its own. | See `locks.md`. | B8 |

Also noted, without a finding id: the deadlock check is one-time per wait
(1089-1092), matching PostgreSQL. Whether a cycle formed after both
participants' checks is always detected is unverified (C2.3 should check this
under weak fairness).

## 7. Security considerations

- **Input:** authenticated statements, COPY rows, and SET values.
  `parseMilliseconds` caps values at `0x7fffffff` (236-252). Zone names are
  capped at 64 bytes (335).
- **Denial of service:**
  - a parked statement pins a snapshot and holds locks until its timeout or
    cancel; `lock_timeout` defaults to 0 (unbounded);
  - `max_query_work` bounds CPU per statement when set (320).
- **Reentrancy:** hooks must not reenter the store (header 28;
  `interrupt_poll` 322-324). This is not enforced. Unverified whether RAMSQL
  hooks comply.
- **No secrets** are held here.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B8.1: the deterministic simulator. N connections drive the real
  `Store` (and the PostgreSQL codec) with a seeded scheduler and a simulated
  `now_ns`, covering parking, timeouts, cancels and deadlocks.
- [ ] B8.2: an Elle-style history checker (G0, G1a, G1b, G1c) plus a
  lost-update check against EvalPlanQual. This catches STO-02 when the arena
  is kept small.
- [ ] B8.3: trace hooks (`-Dtrace`) at `commit`, `park`, `stillBlocked`,
  `terminal` and `finish` for TLA+ trace validation.
- [ ] B9: a `commit_fn` failure injected (a) before any write, (b) after the
  write and before fsync, and (c) after fsync. Assert the client result, the
  `storage_failed` state, the reads served, and the post-restart state for
  each (STO-09).
- [ ] B8: a deadlock that forms after both participants' one-time checks.
  Expect detection or a documented timeout (INV-STORE-4).
- [ ] B8: COPY rows parked on a key wait across a concurrent commit, a vacuum
  and a deadlock.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-06 | TLA+ `WalCommit` + trace validation | INV-STORE-1, INV-STORE-2: publish only after a durable append; no publish after a failure until recovery | todo |
| FV-07 | TLA+ `MvccReadCommitted` + simulation | store-level histories are READ COMMITTED with EvalPlanQual | todo |
| FV-15 | TLA+ `LockManager` | deadlock check plus re-wait (INV-STORE-4) finds every persistent cycle | todo |
| FV-16 | TLA+ `RwGate` | INV-STORE-6 with RAMSQL's `data_gate` (`src/server.sig:286`, 371-373) | todo |
| (none) | TLA+ liveness | a parked statement is woken after every release that could unblock it (no lost wake-up via `wake_epoch`) | todo |

## 10. Benchmarks

- S3: wire simple and extended protocol (store overhead per statement).
- S4: durable commit latency (commit path).
- S5: 1 to 64 clients with parking.
- S9: TPC-C-derived mix.
- Trace hooks must be zero cost when off (B8.3 size and instruction-count
  test).

## 11. Open questions

- STO-09: should a persist failure stop reads too, or return a distinct
  "commit outcome unknown" error and keep serving reads?
- Should `lock_timeout` have a non-zero server default to bound how long a
  parked statement can pin a snapshot?
