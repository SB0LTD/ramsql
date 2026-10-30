# Module: `persistence`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/persistence.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL |
| Size | 135 lines, 7.1 KB |
| Owner lane | `B9` (durability, fault injection); `B5` for data-path policy |
| Status | beta |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here) |

## 1. Purpose and scope

This module owns the server's single WAL for the process lifetime. It recovers
the WAL into a newly initialized `Store` before any listener opens, installs
the commit hook that appends and syncs each change record before publication,
and detaches the hook on close. ZPM owns framing and checksums (`durable_log`),
native file I/O and the writer lock (`db_file`), and change-record encoding and
apply (`sql`). This module does not checkpoint, compact or truncate a WAL,
except that ZPM recovery truncates an incomplete final record.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `change_kind` (19) | const | `3`, the only record kind written |
| `Persistence(Raw, Store)` (21) | fn → type | product policy over one `disk.File` |
| `.accepted_kinds` (25) | const | bit for kind 3, plus kind 2 (int32 configs) or kind 1 (int64 configs) |
| `.init(path, store, recovery_buffer)` (44) | fn | opens or creates `path`, takes the exclusive writer lock, replays, installs the commit hook |
| `.initExisting(...)` (50) | fn | same, but a missing file is an error (offline backup) |
| `.close()` (121) | fn | detaches the hook, sets `store.storage_failed`, releases the file |
| `.sequence` (33) | field | WAL sequence of the last durable record |

`recovery_buffer` must stay at a stable address until `close`. After recovery it
becomes `store.encode_buffer`, so every record the server writes fits in recovery.

## 3. Dependencies and layering

- Imports: ZPM `db_file`, `durable_log`, `db_store` (only `BatchIterator`), `std`.
- Imported by: `src/server.sig:22`; `tests/persistence.sig` and `tests/backup.sig`
  through the build graph.
- Layer rule: no violation.

## 4. Invariants

- **INV-PER-1** Fail closed on open: `store.durability_required` and
  `store.storage_failed` are set before the file is opened (59-60). Only a
  complete, successful recovery clears `storage_failed` (75).
- **INV-PER-2** Flush before publication: `commit` returns success only after
  `log.appendKind`, which ends with `file.sync()` (ZPM `durable_log.sig:80`).
  The store publishes only after the hook returns (ZPM `store.sig`; not
  re-proved here).
- **INV-PER-3** After any append or sync error the file is poisoned (ZPM
  `db_file.sig:185-186, 215-216`) and `commit` returns `StorageUnavailable` until
  a new `init` (111).
- **INV-PER-4** In durable mode, after each successful commit,
  `persistence.sequence == store.database.csn`. This is asserted by tests, not
  enforced in code.
- **INV-PER-5** A legacy record replays as one MVCC transaction. It publishes
  only if every statement succeeds, and its inner framing is checked in full
  before anything applies (92-104).
- **INV-PER-6** A legacy record after any kind-3 record fails closed with
  `UnsupportedLogVersion` (89).
- **INV-PER-7** Only one writer: `disk.File.open` takes `flock(LOCK_EX|LOCK_NB)`
  on POSIX or an exclusive share mode on Windows; a second open fails with
  `DataDirectoryBusy` (ZPM `db_file.sig:95, 105, 116-119`).

## 5. Current status and evidence

`tests/persistence.sig` (contract `contract-persistence`) passes in
`sig build test`. `tools/test_persistence.py` checks crash and restart,
`DataDirectoryBusy`, offline backup and single-bit corruption of a
server-written WAL against the real binary.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-01 | high | robustness | `init` opens with create (61, ZPM `db_file.sig:49-50`); the server calls it with a cwd-relative default path (`server.sig:48, 925`) | server uses `initExisting` unless an explicit create flag is given | B5 |
| SRV-04 | medium | correctness | `initExisting` runs the same recovery, which truncates and syncs a torn tail (ZPM `durable_log.sig:145-147`), so offline backup can modify its source | add a read-only recovery mode for backup, or document it | B5 |
| SRV-08 | medium | testing | the file type is the concrete `disk.File` (26); the only injected fault is a closed handle (`tests/persistence.sig:439-441`); one assertion accepts any error (`tests/persistence.sig:509`) | make the file type generic and add a faulting fake (EIO, ENOSPC, short write, sync failure after a full write) | B9 |

## 7. Security considerations

- The WAL is created with mode 0600 on POSIX and its parent directory is synced
  (ZPM `db_file.sig:102, 113, 124`). On Windows it inherits directory ACLs.
- Attacker model: someone with filesystem access can tamper with the WAL.
  Header and payload CRCs (ZPM `durable_log.sig:124, 140`), sequence continuity
  (132) and semantic validation of kind-3 records reject tampering that is not
  re-checksummed. CRC32 is not a MAC: a deliberate attacker can forge records.
- DoS: a WAL that only grows (no checkpoint) makes recovery time grow without
  limit (C8). `commit` re-validates each record with `Raw.changeRecordOps` (114),
  which costs CPU on every commit.

## 8. Tests

- **Existing:** `tests/persistence.sig`:
  - empty open; restart replay with `sequence == csn` (391-450);
  - second writer gets `DataDirectoryBusy` (421-425);
  - write after `close` gets `StorageUnavailable` (429);
  - torn tail truncated to the committed length (433);
  - closed-handle write failure sets `storage_failed` and never publishes (439-444);
  - recovery-capacity limit gives `TransactionLogCapacity` (526);
  - published alpha.1 WAL fixture (`publishedAlpha1`, 33);
  - kind-3 records and fail-closed corruption (`changeRecords`, 109; 179, 200);
  - kind 1 to kind 3 continuation (`wideContinuation`, 254);
  - typed values (`typedValues`, 292); sequences (`sequences`, 346);
  - malformed legacy framing never publishes (490);
  - partial legacy batch never publishes (509-510).
- **Missing:**
  - [ ] B9: sync fails after a complete write; then reopen and check that the record is present (OPERATIONS "reconcile outcomes").
  - [ ] B9: short write, ENOSPC, EIO at every append boundary.
  - [ ] B9: zero-filled or garbage tail after a crash. Today this gives `CorruptLogHeader` and refuses startup (ZPM `durable_log.sig:124`). Decide whether that is correct.
  - [ ] B9: `PersistenceAlreadyOpen`, `PersistenceRequiresEmptyStore`, `InvalidWorkspace` (55-57).
  - [ ] B9: `GenerationExhausted` at sequence `2^64-1` (112).
  - [ ] B9: recovery is idempotent: two recoveries give the same state, and the second does not change the file.

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| FV-05 | Lean + crash replay | recovered state = apply(longest valid prefix); fail closed on a complete bad record; idempotent | todo |
| FV-06 | TLA+ `WalCommit` | INV-PER-2 and INV-PER-3 in every reachable state | todo |
| FV-22 | TLA+ extension + crash replay | `nextval` values are never repeated across crash and recovery (`tests/persistence.sig:346-389` covers one case) | todo |
| INV-PER-4 | trace validation | `sequence == csn` after every acknowledged commit | todo |

## 10. Benchmarks

S4 (durable writes: insert, point update, DDL) and S10 (WAL validate and replay,
10 MB to 10 GB) cover this module. Metrics: commit latency (one sync per commit),
replay rate in MB/s, and recovery time against WAL size.

## 11. Open questions

- Should a zero-filled or garbage extension at the end of the file count as a
  torn tail? The answer depends on filesystem behavior that lane B9 must measure.
- Should a keyed MAC replace or supplement CRC32 for WAL tamper evidence?
