# Module: `locks` (table locks, row locks, wait-for graph)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/locks.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Identical to `9606877`. |
| Layer | ZPM core (L0) |
| Size | 235 lines, 10 KB |
| Owner lane | B8 (with C2 for `LockManager.tla`) |
| Status | beta |
| Formatting (`sig fmt --check`) | `locks.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`Locks(config)` holds the engine's explicit locks.

- **Table locks:** one 3-bit mode mask per (table, transaction slot). The modes
  are AccessShare (SELECT), RowExclusive (DML) and AccessExclusive (DROP), with
  PostgreSQL's conflict matrix restricted to those three (80-85). They are held
  to transaction end, or released earlier by a subtransaction rollback.
- **Row locks** for `FOR SHARE` and `FOR UPDATE`: a fixed table of
  `config.row_locks` entries keyed by (table, rowid), each with up to
  `config.share_holders` holders in acquisition order.

UPDATE and DELETE row locks are implicit. They are the in-progress `xmax` on a
version, checked by the facade's `target()` and never stored here.

The module also provides the transaction-slot bitset `Set` and the
deadlock-cycle check `cycle`.

Waiting, parking, FIFO ordering of table-lock waiters, and when to run the
deadlock check all live in the facade (`sql.sig:1686-1720`) and the store
(`store.sig:1079-1100`).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `TableMode`, `RowMode` (20-23) | enum | `access_share = 1`, `row_exclusive = 2`, `access_exclusive = 4`; `share`, `update`, `write` |
| comptime checks (26-28) | — | `transactions` in 1..65534; `share_holders` in 1..255; `row_locks` fits u16 |
| `list_capacity` (32) | const | `tables·3 + row_locks`: the per-transaction lock-list size (see STO-03) |
| `Set` (35-59) | struct | Bitset over transaction slots: `add`, `remove`, `has`, `any`, `first`. |
| `tableHolders`, `grantTable`, `releaseTable`, `holdsTable(Mode)` (87-112) | fn | Conflict query, idempotent grant, release by mask. |
| `findRow` (114-119) | fn | Linear scan of the row-lock table. |
| `rowHolders` (132-141) | fn | Adds only the **first** conflicting holder (PostgreSQL MultiXactIdWait order). |
| `holdsRow` (143-150) | fn | True if the slot holds `mode`, or holds `update` (which implies share). |
| `grantRow` (154-171) | fn | Adds a holder; returns the entry index, null if already held, or `LockCapacity`. |
| `releaseRow`, `release` (173-194) | fn | Removes one holder, keeping order; frees the entry when empty. |
| `rowLocksUsed`, `tableLocksHeld` (196-210) | fn | Leak accounting. |
| `cycle(waits, start)` (214-233) | fn | Iterative DFS with fixed stack and visited set; true iff `start` reaches itself. |

## 3. Dependencies and layering

- Imports: `types.sig` only.
- Imported by: `mvcc.sig` (lock-list entries), `sql.sig` (locking and waits).
- Layer rule: no violation found.

## 4. Invariants

- **INV-LOCK-1** (table compatibility): for any table and any two different
  slots, the granted masks never conflict under `tableConflict` (80-85).
- **INV-LOCK-2** (row compatibility): among different slots holding the same
  row entry, an `update` holder is the only holder. `share` holders may
  coexist.
- **INV-LOCK-3** (one transaction, two holders): one slot can appear twice in
  a row entry, as `share` and then `update`. `holdsRow` treats `update` as
  covering `share` (147), but not the reverse. This is the source of STO-03.
- **INV-LOCK-4** (entry well-formed): `used ⇔ count > 0` and
  `count ≤ share_holders`. The `entry.count == 0` branch at 164-166 is
  unreachable.
- **INV-LOCK-5** (accounting): every successful `grantTable` that returns true
  and every non-null `grantRow` is appended once to the owner's lock list
  (`sql.sig:1718`, `1767-1769`, `1902-1904`). Releasing by list returns the
  lock table to its state before the grants.
- **INV-LOCK-6** (cycle check): `cycle(w, s)` returns true iff there is a path
  s → … → s in the graph `w` (FV-15). It terminates: each node is pushed at
  most once, onto a stack of `config.transactions` entries.
- **INV-LOCK-7** (FIFO for table locks, facade): a requester that does not
  yet hold the table also waits for every earlier parked waiter whose
  requested mode conflicts (`sql.sig:1702-1709`).

## 5. Current status and evidence

- `test_sql_mvcc.sig` `deadlocks`:
  - `waitCycle` on multi-holder, AccessExclusive-versus-many-readers and long
    chains;
  - an engine-level two-transaction row deadlock;
  - a FOR SHARE multi-holder deadlock with one-at-a-time waiting;
  - `LockCapacity` with 8 entries rolls back.
- `errorPaths`: NOWAIT (55P03) and SKIP LOCKED.
- RAMSQL `tools/test_postgres_isolation.py` LK schedules (part of the 44/44
  isolation suite).

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-03 | high | robustness (memory safety) | `list_capacity` (32) assumes one list entry per row lock. `grantRow` adds a second holder for the same slot when `update` follows `share` (143-150, 154-171). The overflowing write is in `mvcc.sig:94-98`. | Upgrade the holder in place, or double the capacity, and check the push (see `mvcc.md`). | B8 |
| STO-14 | low | concurrency | A `share` request conflicts only with granted `update` holders (121-126, 132-141). It ignores queued writers, so a steady stream of `FOR SHARE` can starve a waiting `FOR UPDATE` or UPDATE. Table locks are FIFO (INV-LOCK-7); row locks are not. | Record row-lock waiters, as the facade does with `table_wait`, and make new share requests wait behind an earlier conflicting waiter. | B8 |

Also noted, without a finding id: `findRow` and the free-entry search are
linear in `row_locks` (114-119, 157). That is fine at the default 256, and
quadratic if `row_locks` is raised for C9 scale.

## 7. Security considerations

- STO-03 is a crash or memory-corruption denial of service by any
  authenticated session (`mvcc.md`).
- STO-14 lets one session delay writers indefinitely within `lock_timeout`.
  With `lock_timeout = 0`, the wait is unbounded. A cancel request still ends
  it.
- Exhausting `row_locks` returns `LockCapacity` (53200) to the requester and
  rolls back its statement. It does not block others.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B8: the STO-03 regression (`mvcc.md`).
- [ ] B8: a starvation test. Sessions A and B alternate `FOR SHARE` on one row
  while C waits for `FOR UPDATE`; C must acquire within a bounded number of
  rounds (STO-14).
- [ ] B8: a property test for INV-LOCK-1, 2 and 4 after every grant and
  release in random sequences across transactions and savepoints.
- [ ] B8: a property test of `cycle` against a reference reachability check
  over random graphs up to `transactions = 64` (INV-LOCK-6).
- [ ] B8: in the simulator, assert that a deadlock victim is chosen at most
  once per cycle, and that the survivor completes after the victim aborts.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-15 | TLA+ `LockManager` + Lean `Txn/Locks` | detector reports a cycle iff one exists (INV-LOCK-6); granted locks pairwise compatible (INV-LOCK-1, 2); releasing a victim unblocks at least one waiter | todo |
| (none) | TLA+ liveness | under weak fairness and FIFO, every row-lock waiter eventually acquires (fails today: STO-14) | todo |
| (none) | property | INV-LOCK-5: lock-list release restores the lock table | todo |

## 10. Benchmarks

- S5: concurrency scaling with lock contention.
- S9: the TPC-C-derived write mix, which is row-lock heavy.
- No module gate today. If `row_locks` grows for C9, add an S1 instruction
  count for `grantRow` and `findRow`.

## 11. Open questions

- Should `FOR UPDATE` take RowShare instead of AccessShare on the table, as
  PostgreSQL does? Today `body` takes `access_share` for every SELECT
  (`sql.sig:1224`). This only matters if more table modes are added.
- Should the deadlock check repeat periodically rather than once per wait
  (`store.sig:1089-1092`)? Unverified whether a cycle that forms after the
  single check is always caught by another participant's check.
