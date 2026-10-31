# Module: `mvcc` (transactions, write sets, subtransactions)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/mvcc.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Identical to `9606877`. |
| Layer | ZPM core (L0) |
| Size | 236 lines, 11 KB |
| Owner lane | B8 (with C2 for models) |
| Status | beta |
| Formatting (`sig fmt --check`) | `mvcc.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

> **Wave 0 update (2026-10-30).** STO-03 is fixed on ZPM branch `b8/sto03-lock-list`. `Tx.pushLock` is checked and returns `LockCapacity`. `Mvcc.recordLock` releases the grant if recording fails. INV-MVCC-6 `lock_len ≤ list_capacity` is now enforced. The MVCC suite went from 122 to 325 executed assertions, including holder-level checks that catch a second appended holder. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.


## Safety follow-up — 31 October 2026

STO-02's reported reclamation paths are fixed: the horizon includes a running
statement guard and open COPY snapshots, with parked/fresh-wait behavior
preserved. A forced version-arena exhaustion regression previously returned
success with two affected rows instead of three; it now returns 53200 and
rolls back the statement. COPY retains versions across messages until finish
or cancellation. Existing adequate-capacity EPQ schedules remain qualified.

STO-04's sequence watermark is now globally pending while its log-ahead owner
is live. Consumer commits persist it before publication; already-durable cache
hits keep their no-record path. Creator dirty bits retain pending-CREATE
ownership. Ascending/descending replay tests reproduced the old missing WAL
record; RAMSQL adds separate SELECT-ACK/INSERT-ACK process kills and live-owner
PostgreSQL crash differential cases. Implementation scope is documented in ZPM
`docs/SQL_SNAPSHOT_SEQUENCE_SAFETY.md`.

Historical audit locations below are preserved. These changes do not close
STO-05, volatile DML replay or the FV-05/FV-08 formal proof obligations.


## 1. Purpose and scope

`Mvcc(config)` defines the fixed transaction table entry `Tx` and the three
operations that change versions in bulk.

`Tx` holds:

- the epoch and command id;
- the write set (versions created or marked, plus catalog and sequence
  changes);
- a subtransaction stack: user savepoints plus one reserved statement entry;
- the lock list;
- the parked statement's pinned snapshot and wait-for set;
- sequence log-ahead flags;
- INSERT progress and the open COPY state.

The three operations:

- `abortSlice` undoes a suffix of the write set, newest first, and releases a
  suffix of the lock list;
- `stamp` publishes every write at one csn;
- `reclaim` frees, within a budget, superseded versions of a just-published
  transaction.

The facade (`sql.sig`) drives all of this. This module does not choose
snapshots, horizons or commit order.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `write_capacity` (32) | const | `config.tx_writes`, or `max(2·rows_per_table, 64)` when 0 |
| `sub_capacity` (33) | const | `config.savepoints + 1` (statement entry reserved) |
| `WriteKind`, `Write` (35-39) | enum / struct | `{kind, table u8, old u32, new u32}` |
| `Sub` (41-47) | struct | name, `write_start`, `lock_start`, `statement` flag |
| `Tx` (51-107) | struct | per-slot transaction state (section 1) |
| `Tx.push` (88-92) | fn | Bounded: `WriteSetCapacity` when full. |
| `Tx.pushLock` (94-98) | fn | **Unbounded.** Its comment assumes each mode and row lock appears once (STO-03). |
| `Tx.ref` (84-86), `Tx.userSavepoints` (100-106) | fn | `TxRef` for new versions; count of user savepoints |
| `abortSlice(db, slot, start, lock_start)` (116-158) | fn | Undo `writes[start..]` newest first, then `releaseLocks(lock_start)`. |
| `releaseLocks` (162-171) | fn | Release `lock_list[lock_start..]` newest first; bump `wake_epoch` if any were held. |
| `stamp(db, slot, csn)` (174-216) | fn | Set `xmin_csn`/`xmax_csn`; update summaries and the committed projection; clear pending DDL flags. |
| `reclaim(db, slot, horizon)` (220-233) | fn | Free up to 64 `old` versions of update/delete entries with `xmax_csn ≤ horizon`. |

## 3. Dependencies and layering

- Imports: `types`, `storage`, `locks`, `sequence` (20-23).
- Imported by: `sql.sig` only.
- `db` is `anytype`, the facade's `Database`, so the module reaches into
  `db.tables`, `db.catalog`, `db.seqs`, `db.locks`, `db.oid_counter` and
  `db.wake_epoch`. This is a hidden coupling, not a layering violation.

## 4. Invariants

- **INV-MVCC-1** (ownership): every write-set entry refers to a version whose
  `xmin` (insert/update `new`) or `xmax` (update/delete `old`) belongs to this
  transaction, and is uncommitted until `stamp`. So no other transaction's
  `vacuum` can free a version in an active write set: `vacuum` needs
  `xmax_csn ≠ 0`.
- **INV-MVCC-2** (watermarks): for every `i`,
  `subs[i].write_start ≤ subs[i+1].write_start ≤ write_len`, and the same
  holds for `lock_start` and `lock_len`.
- **INV-MVCC-3** (abort restores): after `abortSlice(start, ls)`, the
  versions, marks, catalog rows, table and sequence flags and locks equal
  their state when `write_len == start` and `lock_len == ls`. These
  exceptions are by design:
  - sequence values stay advanced;
  - `next_rowid` is restored only if it is the tail (128);
  - the OID counter is restored only if it is the tail (141, 150);
  - storage hints and compaction are not restored.
- **INV-MVCC-4** (stamp exactly once): `stamp` visits each entry once,
  in order. The summaries and projection change by exactly the net effect of
  the transaction, so insert-then-delete in one transaction nets to zero.
- **INV-MVCC-5** (reclaim safety): `reclaim` frees only update/delete `old`
  versions with `xmax_csn ≤ horizon` in live, undropped tables (225-230). It
  is safe iff `horizon` is (FV-08, STO-02).
- **INV-MVCC-6** (bounds): `write_len ≤ write_capacity` (enforced at 89);
  `lock_len ≤ list_capacity` (**not enforced**, STO-03);
  `sub_len ≤ sub_capacity` (enforced by the facade).

## 5. Current status and evidence

- `test_sql_mvcc.sig` (122 assertions):
  - `statementSubtransactions`: a park keeps the statement slice, and
    `abortStatement` restores exactly the prior pending versions;
  - `savepoints`: ROLLBACK TO releases catalog versions and row locks taken
    after the savepoint;
  - `errorPaths`: write-set and version capacity roll back only the failing
    statement.
- `test_sql_storage.sig` `statementUndo` checks undo for INSERT, UPDATE,
  DELETE and DDL.
- Abort is correct for the paths read. Newest-first undo handles
  insert-then-update and update-then-update chains in one transaction.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-03 | high | robustness (memory safety) | `pushLock` writes `lock_list[lock_len]` with no bound (94-98). `list_capacity = tables·3 + row_locks` (`locks.sig:32`), but one transaction can hold a `share` and an `update` holder on the same row (`locks.sig:143-171`). So `FOR SHARE` then `FOR UPDATE` on N rows needs 2N + 1 entries. That overflows at N ≥ 134 with ZPM defaults (4 tables, capacity 268) and N ≥ 140 with RAMSQL build defaults (8 tables, 280). Call sites: `sql.sig:1767-1769`, `1902-1904`, `1718`. The result is a panic in safe builds and memory corruption in ReleaseFast. | Upgrade the existing holder in place (share → update) so each row appears once, or size the list at `tables·3 + 2·row_locks`. Make `pushLock` return `LockCapacity` on overflow and roll the statement back. | B8 |
| STO-02 | high | correctness | Historical defect, fixed in the safety follow-up: `reclaim` (220-233) requires a correct horizon. The old horizon was wrong for resumed and COPY snapshots (`facade.md`). | See `facade.md`. | B8 |
| STO-15 | low | correctness | `Tx.cid` is u32 and the facade increments it with wraparound (`sql.sig:1163`). After 2^32 statements, `xmin_cid < cid` breaks own-write visibility. | Fail the transaction at wrap. | B8 |
| STO-20 | medium | testing | 122 executed assertions. No property test of INV-MVCC-3 and no lock-list bound test. | B8.4 items below. | B8 |

## 7. Security considerations

- STO-03 is reachable by any authenticated session with two SELECT
  statements in one transaction. It is a crash in safe builds and memory
  corruption in ReleaseFast. RAMSQL's threat model lists it
  (`docs/architecture/THREAT_MODEL.md`).
- No secrets. No input parsing.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B8: `BEGIN; SELECT … FOR SHARE; SELECT … FOR UPDATE` over more than
  `list_capacity / 2` rows. Expect `LockCapacity`, or success after the fix,
  never a trap (STO-03).
- [ ] B8: a property test for INV-MVCC-3. Run random statement sequences with
  savepoints, ROLLBACK TO and statement failures. After each abort, compare a
  full database snapshot (versions, marks, catalog, locks) with the snapshot
  taken at the matching watermark.
- [ ] B8: a property test for INV-MVCC-4. Random committed transactions
  followed by a recomputation of summaries and projections from the
  committed view.
- [ ] B8: assert INV-MVCC-2 and INV-MVCC-6 in a debug build after every
  facade call in the simulator.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-07 | TLA+ `MvccReadCommitted` | write sets plus stamp give READ COMMITTED: no G0, G1a or G1b | todo |
| FV-08 | TLA+ `Reclamation` | INV-MVCC-5 with the facade horizon | todo |
| FV-15 | TLA+ `LockManager` | lock-list accounting: release by list returns exactly the granted locks (with `locks.md`) | todo |
| (none) | property | INV-MVCC-3: abort restores the watermark state | todo |
| (none) | property | INV-MVCC-6: lock-list bound (fails today: STO-03) | todo |

## 10. Benchmarks

- S4: commit latency; `stamp` cost grows with write-set size.
- S5: concurrency; `reclaim` runs on every publish.
- No module-specific gate. The STO-03 fix should be checked with S1
  instruction counts on the row-lock path.

## 11. Open questions

- When `share → update` is upgraded in place, should a later ROLLBACK TO a
  savepoint taken between the two locks restore `share`? PostgreSQL keeps the
  stronger lock until the subtransaction aborts. The model in C2 should fix
  the intended semantics before B8 implements them.
