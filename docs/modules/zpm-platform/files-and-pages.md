# Module: `db_file` and `page_region` (durable files and OS page reservations)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/platform/db_file.sig`, `zpm/src/platform/page_region.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Both identical to `9606877`. |
| Layer | ZPM platform-protocol (L1) |
| Size | `db_file.sig` 406 lines, 20 KB; `page_region.sig` 55 lines, 3 KB |
| Owner lane | B9 (fault seam, wave 1), C8 (checkpoint files) |
| Status | beta |
| Formatting (`sig fmt --check`) | `db_file.sig` clean; `page_region.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

> **Wave 0 update (2026-10-30).** `db_file.Error` gains `FileNotFound` (ZPM branch `b5/srv01-not-found`). `openExisting` returns it only for a missing file or directory component (ENOENT/ENOTDIR; Windows errors 2/3). Every other failure stays `FileOpenFailed`. RAMSQL uses this to refuse a missing data file without a separate probe. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.

## 1. Purpose and scope

**`db_file.sig`** is the native file backend for the WAL and for offline
backups, on Windows, Linux and macOS, with no heap. It provides:

- open with an exclusive writer lock: Windows share mode, POSIX
  `flock(LOCK_EX|LOCK_NB)`;
- positional reads;
- append with short-write and EINTR handling;
- `sync`, which is a full device flush: `FlushFileBuffers`, `fsync`, or
  `F_FULLFSYNC`;
- `truncate`;
- atomic file publication (`replaceAtomic`, `publishNew`) and `remove`.

Every append, sync or truncate failure **poisons** the handle until it is
closed and recovered (header 1-4, 184-256).

**`page_region.sig`** reserves one OS page region (`VirtualAlloc`,
anonymous `mmap`) and hands out typed, bounds- and alignment-checked views. It
has no allocator and no growth.

Not covered here: the platform `file.sig`, a Win32 config-file helper that is
unrelated to durability; WAL framing (`zpm-db/durable-log.md`); and capacity
budgeting (`budgeted_region.sig` and RAMSQL `capacity.sig`).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Error` (`db_file.sig:12`) | error set | includes `DataDirectoryBusy` and `WriterPoisoned` |
| `File.open`, `openExisting`, `createExclusive` (49-59) | fn | Read-write, locked. `open` creates when missing; `openExisting` never creates; `createExclusive` never truncates an existing file. Mode 0o600 and `O_NOFOLLOW` on POSIX (101, 112). The parent directory is synced before returning (124). |
| `File.openReadOnly` (63-87) | fn | Shared, unlocked, never creates (zone data). |
| `File.length`, `readAt` (156-183) | fn | `readAt` uses `pread` on POSIX. On Windows it is seek plus `ReadFile`, which moves the shared file pointer. |
| `File.appendAll` (184-213) | fn | Seek to end, then write in a loop. Zero progress is an error. Poisons on any error (186). |
| `File.sync` (214-235) | fn | `FlushFileBuffers`, `fsync` (not `fdatasync`), or `F_FULLFSYNC`; fails closed if unsupported. Poisons on error. |
| `File.truncate` (236-256) | fn | Poisons on error. |
| `replaceAtomic(tmp, target)` (264-290) | fn | Rename over the target, then sync both parent directories. The target must never be the active WAL (comment 259-263). |
| `publishNew(tmp, target)` (296-340) | fn | Never replaces an existing target: `RENAME_NOREPLACE`, `renameatx_np(EXCL)`, or a `linkat` fallback. Directory syncs follow. |
| `remove(path)` (390-406) | fn | Unlink, then sync the parent. |
| `Region.view(T, offset, count)` (`page_region.sig:17-23`) | fn | `InvalidCapacity` when out of bounds or misaligned. |
| `Region.tryClose`, `close` (24-35) | fn | Idempotent. `close` panics if the OS release fails. |
| `reserve(bytes)` (41-55), `pageSize` (39) | fn | Windows `MEM_COMMIT|MEM_RESERVE`, read-write; Linux and macOS private anonymous `mmap`. |

## 3. Dependencies and layering

- Imports: `std` and `builtin` (OS bindings only); `kernel32` externs on
  Windows (13-24).
- Imported by:
  - RAMSQL `src/persistence.sig:14`, `src/backup.sig:2`;
  - RAMSQL `src/capacity.sig:5`, `src/server.sig:31`,
    `src/production_benchmark.sig:5` (`page_region`);
  - ZPM `budgeted_region.sig`;
  - tests `test_db_file.sig`, `test_page_region.sig`,
    `test_packed_integer_100m.sig`.
- Layer rule: no violation found. `File` is a concrete type with no interface,
  which is SRV-08's root cause (no fault seam).

## 4. Invariants

- **INV-FILE-1** (single writer): while one process holds a handle from
  `open`, `openExisting` or `createExclusive`, a second such open fails with
  `DataDirectoryBusy`. On Windows: share mode `FILE_SHARE_READ` and error 32
  (93-96). On POSIX: `flock` (105, 116). The POSIX lock is advisory and may
  not work on network filesystems; RAMSQL `OPERATIONS.md` warns about this.
- **INV-FILE-2** (poison): once `poisoned`, `appendAll`, `sync` and
  `truncate` return `WriterPoisoned` without touching the file (185, 215,
  237).
- **INV-FILE-3** (sync is a device flush): `sync` returns success only after
  the OS flush call succeeds. On macOS that is `F_FULLFSYNC`, not plain
  `fsync` (227-233).
- **INV-FILE-4** (directory durability): on POSIX, a newly created file's
  directory entry is synced before the handle is returned (124). Every
  rename or unlink syncs the affected parent directories. On Windows
  `syncParent` is a no-op (358).
- **INV-FILE-5** (no overwrite): `publishNew` and `createExclusive` never
  replace or truncate an existing path, including symlinks.
- **INV-PAGE-1** (views): `view` returns a slice only when
  `offset + count·@sizeOf(T) ≤ bytes.len`, computed without overflow, and the
  pointer is aligned (17-23).

## 5. Current status and evidence

- `test_db_file.sig` (27 assertions) covers:
  - exclusive create and private mode (0o077 bits clear);
  - a second writer is rejected (`DataDirectoryBusy`);
  - read-only sharing;
  - a torn tail is truncated on reopen;
  - `replaceAtomic` and `publishNew` semantics, including a symlink target;
  - native failed syscalls and poison guards on an invalid handle.
- `test_page_region.sig` covers mapping, bounds, alignment and idempotent
  release.
- No test injects a real EIO, ENOSPC, short write or fsync failure through
  the native backend. The fault tests in `test_durable_log.sig` use a mock.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-18 | low | durability | Linux: after a failed `fsync` the handle is poisoned (216), but a restart in the same boot re-reads the file through the page cache. Pages whose writeback failed may be clean in cache yet not on disk, so recovery may accept bytes that are not durable ("fsyncgate"). Windows: `syncParent` returns immediately (358). Directory-entry durability for a new WAL relies on NTFS metadata journaling plus `FlushFileBuffers` on the file; this is unverified. | Linux: document that an fsync failure requires a reboot or remount before recovery, or verify the tail with `O_DIRECT` reads during recovery. Windows: open the directory with `FILE_FLAG_BACKUP_SEMANTICS` and call `FlushFileBuffers` on it, or record the NTFS assumption in the axioms list (`FORMAL.md` §1). | B9 |

SRV-08 (no fault seam: `persistence.sig` and `backup.sig` take a concrete
`*disk.File`) is tracked on `ramsql/persistence.md` and `ramsql/backup.md`.
B9.1 fixes it by giving this module a comptime file interface or an `anytype`
file.

## 7. Security considerations

- **Paths** come from the operator CLI (`--data`, `--backup-to`). They are
  validated for NUL bytes and length (26-30). POSIX opens use `O_NOFOLLOW` and
  mode 0o600. Windows opens follow reparse points; this is unverified as a
  risk.
- **Concurrent writers** are excluded by INV-FILE-1. A non-cooperating process
  that ignores `flock` can still write the WAL on POSIX.
- **Denial of service:** a full disk poisons the writer (INV-FILE-2). The
  server then refuses writes until restart, by design.
- **page_region:** Windows commits the whole reservation up front, so
  reservation fails early. Linux `mmap` is lazily backed, so under
  overcommit a later page fault can trigger the OOM killer instead of an
  error. RAMSQL's admission ledger (FV-09) budgets bytes but cannot see
  physical commit; unverified at scale.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B9.1: a fault seam, as a comptime interface or `anytype`. Its faulting
  fake injects EIO, ENOSPC after k bytes, short write, write OK then fsync
  failure, and torn writes at byte offsets. Persistence and backup take the
  interface (SRV-08).
- [ ] B9.4: run the persistence suite under LazyFS (or dm-flakey or
  dm-log-writes) on Linux to drop unsynced data. Record this as evidence for
  the `storage_power_failure` gate.
- [ ] B9: a Linux test that an fsync failure leaves the handle poisoned and
  the server refusing writes until reopened (STO-18 documentation test).
- [ ] B9: a Windows test that creates a WAL, appends and syncs, then checks
  the directory entry survives a simulated crash. If this cannot be tested,
  state it as an axiom (STO-18).
- [ ] C5: `page_region.reserve` under a cgroup memory limit on Linux;
  document whether failure is an error or the OOM killer.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-05 | crash replay (B9.2, B9.4) | the axiom "a completed `sync` makes preceding writes durable" (`FORMAL.md` §1) holds on the tested filesystems | todo |
| FV-06 | TLA+ `WalCommit` | INV-FILE-2 is modeled: no append is acknowledged after a failure until recovery | todo |
| (none) | property | INV-PAGE-1 over random offsets, counts and types | todo |

## 10. Benchmarks

- S4: durable writes. `sync` dominates commit latency; report the filesystem
  and device with every S4 result.
- S10: recovery read throughput through `readAt`.

## 11. Open questions

- Should `sync` use `fdatasync` on Linux? It is cheaper, and correct for an
  append-only file only if the size update is also persisted. Linux
  guarantees that for size-changing writes.
- Which filesystems does RAMSQL support for `--data`? The answer sets the
  STO-05 and STO-18 remedies and the axioms in `FORMAL.md`.
