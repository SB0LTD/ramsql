# Module: `change_log` (kind-3 change records)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/change_log.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Identical to `9606877`. |
| Layer | ZPM core (L0) |
| Size | 961 lines, 47 KB |
| Owner lane | B9 (fixes, crash linking), C3 (models), C8 (checkpoint format) |
| Status | beta |
| Formatting (`sig fmt --check`) | `change_log.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

This module defines the logical redo record that each committing transaction
writes to the WAL as a kind-3 payload. A record is little-endian:

- a header: version 1, flags 0, op count;
- ops: create/drop table, insert, update (full row), delete, catalog
  counters, create/drop sequence, and sequence log-ahead value (3-29).

`Writer` encodes. The facade's `encodeWrites` (`sql.sig:2344-2375`) walks the
write set in order.

`apply` replays one record in two phases:

1. `validate` decodes and checks the whole record against a bounded overlay
   (`Scratch`) without changing the database;
2. the record is applied with no further expected failure (868-959).

Unknown versions or ops fail closed with `UnsupportedLogVersion`, malformed
bytes with `InvalidChangeRecord`, and conflicts or capacity with
`ChangeRecordConflict` (31-37).

Framing, checksums and sequence numbers belong to `db/durable_log.sig`.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `format_version`, `header_size`, `first_oid`, `Op` (46-51) | const / enum | Version 1, 8-byte header, first user OID 16384. |
| `opCount` (77-81) | fn | Header check; a non-1 version or non-zero flags gives `UnsupportedLogVersion`. |
| `Writer` (85-135) | struct | Appends to `buffer[used..]`, writing a header when empty. Overflow gives `TransactionLogCapacity`. `finish` updates the count with overflow detection. |
| `encodeValue`, `encodeRow`, `encodeDelete`, `encodeCreate`, `encodeDrop`, `encodeCreateSequence`, `encodeDropSequence`, `encodeSequenceValue`, `encodeCounters` (172-304) | fn | One op each. |
| `Scratch` (343-349) | struct | Caller-owned overlay for rows, keys, tables and sequences. Reset is O(1) by epoch. |
| `apply(db, record, scratch)` (868) | fn | `validate`, then apply. Advances `db.csn` and `db.epoch` once per record (871-874, 958). |

## 3. Dependencies and layering

- Imports: `types`, `storage`, `numeric`, `temporal`, `jsonb`, `sequence`
  (38-43).
- Imported by:
  - `sql.sig` (`C`, `encodeWrites`, `applyChangeRecord`);
  - RAMSQL `persistence.sig` through `applyChangeRecord`;
  - `test_sql_change_log.sig`.
- `db` is `anytype`. `apply` calls back into the facade's `replayCreate`,
  `replayDrop` and the other replay hooks (886, 893, 939, 946).

## 4. Invariants

- **INV-CLOG-1** (round trip): decode after encode is the identity for every
  op and value tag. Every value is type-checked against its column before
  anything applies (`decodeRow`, 632-642).
- **INV-CLOG-2** (atomic validation): if `validate` fails, the database is
  unchanged. This is tested for every truncation and every single-byte flip
  in `test_sql_change_log.sig` `corruption`.
- **INV-CLOG-3** (validate ⇒ apply succeeds): after `validate` succeeds,
  `apply` cannot fail (comment at 870). **Not fully established:**
  - apply calls fallible `ensureVersions` and `ensureRowSlot` (847, 905-906)
    while validation checks live rows only (746);
  - validate and apply choose the slot for a new table by different rules
    (715-719 versus 883-889);
  - apply's create loop silently does nothing when no slot is free (883-889).

  These coincide during startup replay; they need not coincide on a live
  database (STO-13).
- **INV-CLOG-4** (replay refinement): replaying, in commit order, the records
  produced by `encodeWrites` yields a database equal to the published source
  (`sameDatabase` in `test_sql_change_log.sig:42-59`). **False today** for
  concurrent DDL committed out of OID order (STO-01) and for sequence values
  drawn from another transaction's log-ahead (STO-04). FV-05b.
- **INV-CLOG-5** (fail closed): a record with trailing bytes (831), an
  unknown op or version, a duplicate rowid, or a key conflict is rejected
  whole.
- **INV-CLOG-6** (bounded overlay): every record a transaction can encode
  fits the overlay. The overlay capacity is ≥ 4 × the possible touched rows
  (161-168), and probing stops at half capacity (406-418).

## 5. Current status and evidence

- `test_sql_change_log.sig` (876 assertions):
  - a 16-statement workload round-trips per statement and as one combined
    record;
  - counters round-trip;
  - applying the same record twice gives `ChangeRecordConflict` with no
    change;
  - every truncation and every byte flip is rejected or decodes to another
    valid record;
  - unknown version, flags and ops are rejected;
  - 12 atomicity cases (rowid and key conflicts, unknown OIDs, type and range
    checks, sequence ops) leave the database unchanged.
- All tests are serial. None interleaves two transactions.
- `test_sql_dml.sig` `durability` covers carried sequence values. RAMSQL
  `tools/test_persistence.py` covers restart.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-01 | critical | durability | `validate` requires each create's OID to be ≥ `next_oid` (713 tables, 805 sequences). `next_oid` is then set from that create (728, 817). OIDs are assigned at statement time in the facade, so two concurrent creates that commit in reverse order give `ChangeRecordConflict`, and startup fails. `apply` also sets `oid_counter` from the last create, not the maximum (887, 942). | Validate only that the OID is ≥ `first_oid`, below `0xfffffffe`, and not used by any live table or sequence in the overlay. In apply, set `oid_counter = max(oid_counter, oid + width − first_oid)`. Add the out-of-order test in section 8. | B9 |
| STO-06 | high | perf | `validate` recomputes `S.liveRows` (a full slot scan) for every committed table on every record (689-701). Recovery is O(records × rows). | Use an incremental live-row count: `summary.rows` when valid, otherwise a dedicated counter maintained by insert, delete and abort. | B9 (counter), C8 (checkpoint bounds history) |
| STO-13 | medium | robustness | See INV-CLOG-3. | Share one slot-selection function between validate and apply. Validate version capacity as well as row capacity. Make the create loop's "no slot" case unreachable by construction, or an error. The facade refuses replay with active transactions (`facade.md`). | B9 |
| STO-16 | low | robustness | `encodeCreate` writes the expression length as `@intCast` to u16 (241). Only RAMSQL bounds `text_bytes ≤ 65535` (`build.sig:126`). The u8 casts for names, column counts and table counts (119, 234, 281, 295-298) are guarded by the facade's comptime check (`sql.sig:194`). | Add `text_bytes ≤ 65535` to the facade's comptime checks, or encode a u32 length under a new `expr_format_version`. | B9 |

## 7. Security considerations

- **Records are trusted only after the WAL checksum.** A malicious WAL
  writer is out of scope; a corrupt or hostile record must still fail closed.
  `decodeValue` (501-548) bounds every length by `text_bytes`, validates JSONB,
  numerics, dates and timestamps, and `Reader.take` checks bounds (141-146).
- The overlay probe limit prevents unbounded probing (406-418, 436-444).
- STO-01 lets two authenticated users make the next restart fail.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B9: two transactions create tables (and identity sequences) concurrently
  and commit in reverse OID order. Replay the WAL into a fresh database and
  require `sameDatabase` (STO-01).
- [ ] B9: the FV-05b random-schedule harness. The B8 simulator runs random
  interleavings of DDL, DML, `nextval`, savepoints and aborts, captures each
  record through `commit_fn`, replays into a fresh database and compares with
  `sameDatabase` after every commit.
- [ ] B9: recovery time against row count. Record S10 numbers for 10k, 100k
  and 1M-row tables with 10k records, and fail if the growth is superlinear
  (STO-06).
- [ ] B9: a structure-aware fuzzer for `apply`. Mutate valid records op by
  op. Oracles: no trap; either an error with the database unchanged, or
  success with all invariants holding.
- [ ] B9: `applyChangeRecord` with a pending CREATE in another transaction:
  the call must be refused (STO-13).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-05b | Lean model + TLA+ `WalCommit` | INV-CLOG-4, replay refinement for every concurrent schedule (counterexamples: STO-01, STO-04) | todo |
| FV-05 | Lean `Wal/Recovery` | recovered state equals `apply` over the record prefix chosen by `durable_log` recovery | todo |
| FV-22 | TLA+ `WalCommit` extension | sequence ops make `nextval` values unique across crashes (with `sequence-and-dml.md`) | todo |
| (none) | property + fuzz | INV-CLOG-1 round trip and INV-CLOG-2 atomicity over generated records | todo |

## 10. Benchmarks

- S10 (recovery: WAL validate and replay, 10 MB to 10 GB) is the primary
  suite. STO-06 must be fixed before S10 numbers mean anything at scale.
- S4: encode cost per commit.

## 11. Open questions

- STO-01: should replay accept any unused OID, or should OIDs be assigned at
  publish (`catalog.md`)?
- C8 checkpoint: will a snapshot reuse this record format (a create plus
  inserts per table) or get its own format version? Either choice needs
  `format_version` handling and a reader for version N-1 (C8.4).
