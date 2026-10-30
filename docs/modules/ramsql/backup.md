# Module: `backup`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/backup.sig` (driven by `server.backup`, `src/server.sig:1061-1074`) |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL |
| Size | 64 lines, 2.9 KB |
| Owner lane | `B5` (data-path policy); `B9` (fault injection) |
| Status | beta |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here) |

> **Wave 0 update (2026-10-30).** Since the SRV-01 ZPM change, a missing backup source reports `FileNotFound` instead of `FileOpenFailed`; `tests/backup.sig` is updated to match. SRV-04 (backup recovery can truncate the source's torn tail) is still open. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.

## 1. Purpose and scope

`backup.create` makes an offline, byte-for-byte copy of a WAL that the caller
has already locked and fully replayed. It writes to `target + ".tmp"` (created
exclusively), syncs it, re-reads source and copy and compares them, and then
publishes the copy at a new `target` that must not already exist. The
validation is done by the caller: `server.backup` replays the source through
`Persistence.initExisting` before copying. The module does not do online
backup, compaction, point-in-time recovery or incremental copies.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `create(source, target, scratch)` (21) | fn | `source` is locked and validated with no concurrent commits; `scratch` has at least 2 bytes; `target` is new; an existing target or `.tmp` is never overwritten |
| `exact` (4) | fn (private) | reads exactly `buffer.len` bytes or returns `BackupUnexpectedEnd` |

Errors: `WriterPoisoned`, `BackupBufferCapacity`, `InvalidPath`, `PathTooLong`,
`BackupSourceChanged`, `BackupVerificationFailed`, plus ZPM `db_file` errors such
as `FileRenameFailed` from `publishNew`.

## 3. Dependencies and layering

- Imports: ZPM `db_file` only.
- Imported by: `src/server.sig:1073` (`@import("backup.sig")`); `tests/backup.sig`.
- Layer rule: no violation.

## 4. Invariants

- **INV-BAK-1** Either `target` exists afterwards and is byte-equal to the
  source as it was at the call, or `target` does not exist. A failure before
  publication removes only the `.tmp` this call created (35-40).
- **INV-BAK-2** An existing `target` or existing `target.tmp` is never opened
  for writing or replaced (`createExclusive` at 32; ZPM `publishNew` "never
  replaces any existing destination", `db_file.sig:291-296`).
- **INV-BAK-3** The source length is checked before and after the copy (31, 49).
  A change gives `BackupSourceChanged`.
- **INV-BAK-4** The copy is synced (48) before verification and publication.
  `publishNew` syncs the parent directory (ZPM `db_file.sig:357`). A failure
  after rename has an uncertain outcome and the caller must not retry by
  overwriting (doc comment 19-20).
- **INV-BAK-5** Memory is bounded by `scratch`: the copy uses all of it and the
  compare uses two halves (41-60). The server passes its 1 MiB recovery buffer
  (`server.sig:1073`).

## 5. Current status and evidence

`tests/backup.sig` (`contract-backup`) and `tools/test_persistence.py`
(`offline_backup`, including `DataDirectoryBusy` while the server holds the lock)
pass. The restore path is "open the copy as `--data`". It is checked by
replaying the backup in `tests/backup.sig:76-85`.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-04 | medium | correctness | the doc comment says "The source remains unchanged" (15), but `server.backup` first calls `initExisting` (`server.sig:1071`), whose recovery truncates and syncs a torn tail (ZPM `durable_log.sig:145-147`) | read-only recovery for backup, or document that backup repairs the tail | B5 |
| SRV-08 | medium | testing | `create` takes a concrete `*disk.File` (21), so `BackupSourceChanged` (49) and `BackupVerificationFailed` (58) cannot be reached in tests; `tests/backup.sig:57` accepts any error for a pre-existing `.tmp` | make the file parameter generic; assert the exact error | B9 |

## 7. Security considerations

- `target` comes from the operator's argv. There is no path policy beyond
  length (26-27): any writable location works, including symlinked directories.
  `publishNew` refuses to replace an existing symlink at `target`.
- The `.tmp` is created with mode 0600 on POSIX (ZPM `createExclusive`). On
  Windows it inherits directory ACLs. The backup holds the same data as the WAL,
  so protect it the same way.
- Verification compares against the OS page cache. It proves the copy logic,
  not the media.
- Backup replays the whole WAL into a full-sized store
  (`server.sig:1063-1071`), so it needs memory for the historical peak, and the
  machine sampler must work.

## 8. Tests

- **Existing:** `tests/backup.sig`:
  - zero-byte scratch gives `BackupBufferCapacity` (51);
  - a pre-existing `.tmp` is preserved byte-for-byte (53-63);
  - a successful copy has the source length (64-67);
  - a second backup to the same target fails with `FileRenameFailed` and the
    source is unchanged (69-75);
  - the restored copy replays to `sequence == 3` with the expected rows (76-85);
  - `initExisting` on a missing file gives `FileOpenFailed` and creates nothing (88-94).

  `tools/test_persistence.py` runs the CLI path end to end.
- **Missing:**
  - [ ] B9: `BackupSourceChanged` and `BackupVerificationFailed` through a faulting file.
  - [ ] B9: crash between `publishNew`'s rename and its directory sync (uncertain outcome is documented, not tested).
  - [ ] B5: backup of a WAL with a torn tail; assert the source policy chosen for SRV-04.
  - [ ] A3: `target` longer than 4091 bytes gives `PathTooLong` (27).

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| INV-BAK-1 | property (faulting file) | the outcome is "published and equal" or "target absent", for an injected failure at every I/O call | todo |
| INV-BAK-2 | property | for any pre-existing target or `.tmp`, its bytes are unchanged after `create` | todo |
| FV-05 | Lean + crash replay | applies to the replay that validates the source before copying | todo |

## 10. Benchmarks

None tracked. Expected cost is one sequential read, one write and sync, then
two reads of the WAL size. S10 recovery numbers bound the replay step.

## 11. Open questions

- Should backup run without replay, by checking only framing and checksums?
  That would avoid the memory-peak requirement, but semantic validation would
  then be lost.
- Online backup needs a WAL lifecycle design (lane C8).
