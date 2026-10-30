# Module: `storage` (versioned table storage)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/storage.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | 803 lines, 40 KB |
| Owner lane | B8 (visibility, reclamation), C9 (compact storage) |
| Status | beta |
| Formatting (`sig fmt --check`) | `storage.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`Storage(config)` stores one table's rows as multi-version chains. A table has:

- a row directory `{rowid, generation, newest}`;
- a rowid → slot hash index;
- a version arena;
- a primary-key hash index from key to version.

It also maintains derived data that read routes use:

- **Committed state**, maintained at publish:
  - exact summaries and per-column rollups;
  - a column-major projection of integer and date values (`scan_*`);
  - block presence filters (new at 2f060c2).
- **Hints**, maintained at insert:
  - integer bounds and column order proofs;
  - a dense primary-key directory.

The module decides visibility for a snapshot, and allocates, reclaims and
compacts slots on demand. It does not decide *when* reclamation is safe. The
caller passes the horizon (`sql.sig` `horizon()`).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `TxRef`, `Snapshot`, `committed_view` (46-61) | struct / const | `{slot, epoch, sub}`; snapshot `{csn, slot, epoch, cid}`; epoch 0 means no own transaction. |
| `Version` (92), `RowSlot`, `RowRef`, `Table` (183) | struct | `row == 0` marks a free arena slot; `newest == 0` marks a free directory slot. |
| `Backing.valid` (134-143) | fn | Index sizes ≥ 2× rows or versions, powers of two, projection sizes checked with overflow detection. |
| `created`, `deleted`, `visibleAt`, `visible`, `successor` (362-398) | fn | The visibility rule (section 4). |
| `findRow`, `lookupVersionAt`, `lookupKeyAt`, `lookupKey` (427, 622, 652, 658) | fn | Hash probes. The PK probe validates every candidate against `visibleAt`. |
| `allocVersion`, `allocRow` (667, 685) | fn | No capacity check: callers call `ensureVersions` and `ensureRowSlot` first. |
| `reclaimVersion` (705), `vacuum` (721), `ensureVersions` (772), `ensureRowSlot` (779) | fn | Unlink and free; full-arena vacuum at a horizon; compaction when the directory high-water mark is full. |
| `summarize`, `summarizeUpdate`, `initSummary`, `scanPublish` (453-490, 344) | fn | Called only at publish or replay. |
| `blockMayContain` (321) | fn | `false` is definitive: no live committed row in the 64-row block holds the value. |
| `indexKey` (542) | fn | Updates bounds, order proof and directory hint, then indexes the key. |
| `liveRows`, `stats` (786, 794) | fn | O(row_high) and O(version_high) scans. |

## 3. Dependencies and layering

- Imports: `types.sig` only.
- Imported by: `sql.sig`, `mvcc.sig`, `catalog.sig` (its four relations are
  `Storage` tables), `change_log.sig`, `exec.sig`, `dml.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-STOR-1** (visibility, 362-382): a version is visible to snapshot `s`
  iff `created(v, s)` and not `deleted(v, s)`, where:
  - `created(v, s)`: either `xmin_csn ≠ 0` and `xmin_csn ≤ s.csn`, or the
    version is own and `xmin_cid < s.cid`;
  - `deleted` is the same rule on `xmax`, with `xmax.epoch == 0` meaning
    never deleted.

  A statement never sees its own writes from the same statement. Another
  transaction's uncommitted version is never visible. Aborted versions are
  freed at abort, so they are never observed. FV-07.
- **INV-STOR-2** (chains): `rows[s].newest` points to the newest version.
  Each `older` link points to an older version of the same row. Every version
  in a chain has `row == s + 1`. `reclaimVersion` keeps the order of the
  remaining versions (705-718).
- **INV-STOR-3** (indexes):
  - every slot with `newest ≠ 0` is in `row_index` exactly once;
  - every allocated version of a PK table is in `key_index` exactly once;
  - backward-shift deletion keeps probing tombstone-free (`removeAt`).

  FV-21 for the key index.
- **INV-STOR-4** (load factor): `row_index.len ≥ 2·rows` and
  `key_index.len ≥ 2·versions` (`Backing.valid`; comptime `pow2` for inline
  storage), so every probe loop terminates.
- **INV-STOR-5** (reclamation): `vacuum(h)` frees exactly the versions with
  `xmax_csn ≠ 0 ∧ xmax_csn ≤ h` (721-730). This is safe iff `h ≤ s.csn` for
  every live snapshot `s`. That is the caller's obligation, and it is
  violated today (STO-02, `facade.md`). FV-08.
- **INV-STOR-6** (committed projection): for every slot below `scan_high`,
  `scan_live`, `scan_nonnull` and `scan_values` equal the committed view as of
  `summary.csn`. Only `scanPublish` (publish or replay) and `compact` change
  them. Private and aborted writes never do.
- **INV-STOR-7** (exact summaries): when `summary.valid`, `summary.rows` and
  each column's `nonnull`/`total` equal the aggregate over committed live
  rows. When `rollup.valid`, each group record is exact.
- **INV-STOR-8** (conservative hints):
  - `integer_bounds` contains every integer ever inserted, including pending
    and aborted ones;
  - `column_order` never claims an order that some slot's versions violate;
  - a `primary_directory` hit is accepted only after `visibleAt` and an exact
    key check (629-637).
- **INV-STOR-9** (presence superset): `blockMayContain(..) == false` implies
  no committed live row in the block holds the value. Bits are only added at
  publish and rebuilt at compaction (315-341, 356, 766).
- **INV-STOR-10** (stale references): a `RowRef` resolves only while the
  slot's generation is unchanged. This holds until the u32 generation wraps
  (STO-15).

## 5. Current status and evidence

- `test_sql_storage.sig` (619 assertions) covers:
  - rowid directory and compaction;
  - arena reuse;
  - two full-table updates under the 64-version reclaim budget;
  - stale `RowRef`s after compaction and a forged generation;
  - statement undo for INSERT, UPDATE, DELETE and DDL.
- `test_sql_index.sig` (239,627 assertions) includes `randomModel`, a seeded
  model-based test of PK lookups over inserts, updates, deletes and aborts.
  This is a good base for FV-21.
- `test_sql_block_presence.sig` compares enabled and disabled presence
  filters across publication, snapshot, copy and compaction boundaries.
- `test_sql_summary.sig`, `test_sql_rollup.sig` and
  `test_sql_layout_order.sig` check the derived committed state through SQL.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-02 | high | correctness | `vacuum` and `ensureVersions` trust the horizon they receive (721-730, 772-776). The facade passes a horizon that ignores resumed and COPY snapshots. | Fix in the facade (`facade.md`). Add a debug-build check in `vacuum` against a caller-supplied list of live snapshot csns. | B8 |
| STO-06 | high | perf | `liveRows` scans every slot (786-792), and replay calls it for every table on every record (`change_log.sig:689-701`). | Keep an incremental live-row counter, or use `summary.rows` when valid. | B9 |
| STO-08 | medium | perf | Reclamation is lazy: `reclaim_budget = 64` per publish (70). When the arena is short, `ensureVersions` runs a full `vacuum` over `version_high` (772-776, 721-730) on every allocation. `ensureRowSlot` → `compact` rewrites every slot and rebuilds `row_index` and the presence filters inside one INSERT (779-784, 732-769). Near capacity this repeats: latency cliffs. | Incremental vacuum over a dead-version list. Compaction thresholds that free at least a fixed fraction. Move both off the statement path where possible. | C9 |
| STO-15 | low | correctness | `row_generation +%= 1` (689, 699, 758) wraps at 2^32, so a stale `RowRef` could resolve again. | Widen to u64, or fail at wrap. | B8 |

## 7. Security considerations

- **Inputs** are values from authenticated SQL or validated change records.
  Probing uses `mix` and `V.hash`, which are not keyed. Adversarial keys can
  cluster probes. The 50% load bound limits the damage, and
  `test_sql_index.sig` already uses adversarial keys (`hashKey`).
- **Memory safety** depends on the callers keeping the capacity preconditions
  of `allocVersion` and `allocRow`.
- **Denial of service:** an attacker who keeps a snapshot pinned (a parked
  statement) stops reclamation. Version capacity then runs out (53200) for
  every writer of that table. This is documented in RAMSQL `CAPACITY.md`.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B8: a property test of INV-STOR-5. Generate random version chains and a
  set of live snapshots; check that `vacuum(min csn)` leaves `visibleAt`
  unchanged for every snapshot and slot.
- [ ] B8: an invariant checker callable after every publish and replay. It
  verifies INV-STOR-2, 3, 6, 7 and 9 against a committed MVCC scan. Run it in
  the B8 simulator after every step.
- [ ] C9: `ensureVersions` and `ensureRowSlot` at 95-100% occupancy with a
  pinned snapshot. Record allocations per second and worst-case latency
  (STO-08).
- [ ] B8: a generation wrap test using a forced starting generation near
  `0xffffffff` (STO-15).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-07 | Lean `Txn/Mvcc` + TLA+ | INV-STOR-1: the visibility rule gives READ COMMITTED with own-write and Halloween semantics | todo |
| FV-08 | TLA+ `Reclamation` + Lean `Txn/Reclaim` | INV-STOR-5: if `h ≤` every live snapshot csn, `vacuum(h)` and `reclaimVersion` preserve `visibleAt` | todo |
| FV-21 | Lean model + simulation | INV-STOR-3: key lookup returns exactly the visible row with that key after any operation sequence (extend `randomModel`) | todo |
| (none) | property | INV-STOR-6, 7 and 9 equal a recomputation from the committed view | todo |

## 10. Benchmarks

- S1: probe and visibility instruction counts.
- S2: point and filter queries.
- S6: bytes per row and load rate. The arena and indexes dominate.
- S4 and S5 under churn for STO-08: track p99 insert latency at high arena
  occupancy.

## 11. Open questions

- Should reclamation become eager (a dead-version queue drained after each
  publish), or stay on-demand with a cheaper scan?
- C9 compact storage will replace the row-major `cells` per version. Which
  invariants above carry over unchanged? The ADR should say.
