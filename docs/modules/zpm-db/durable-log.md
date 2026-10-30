# Module: `durable_log` (WAL framing and recovery)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/db/durable_log.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Identical to `9606877`. |
| Layer | ZPM platform-protocol (L1) |
| Size | 151 lines, 7 KB |
| Owner lane | B9 (fault injection, crash harness), C3 (models), C8 (checkpoint and segments) |
| Status | beta |
| Formatting (`sig fmt --check`) | `durable_log.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

The module frames committed transactions in an append-only log and recovers
them. Each record is:

- a **32-byte header**: magic `RAMSQLW1`, version u16 = 1, kind u16, payload
  length u32, sequence u64, header CRC-32, and 4 reserved zero bytes;
- the **payload**;
- a **16-byte trailer**: payload CRC-32, `CMIT`, and the sequence again.

Integers are little-endian. Native structs are never serialized (1-14, 51-80).

`append` returns success only after the backend `sync` succeeds (80). Any
append error must poison the writer (header 6-7; enforced by `db_file`).
Recovery replays records in order through a callback. It truncates only a
physically incomplete final record, and fails closed on any complete record
that is corrupt (3-4, 114-151).

The payload is opaque here. RAMSQL writes kind 3 (change records) and accepts
legacy kinds 1 or 2 (`persistence.sig:19-25`).

Out of scope: checkpoints, segments, compaction (none exist: STO-07); file
locking and fsync semantics (`files-and-pages.md`).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `header_size`, `trailer_size`, `overhead` (9-11) | const | 32, 16, 48 |
| `Recovery` (14) | struct | `{sequence, valid_bytes, truncated_tail}` |
| `checksum` (39-43) | fn | CRC-32 (IEEE, reflected 0xEDB88320) |
| `append(file, seq, payload)` (45-47) | fn | `appendKind` with kind 1 |
| `appendKind(file, seq, payload, kind)` (51-81) | fn | Rejects kind 0, sequence 0, and empty or over-4-GiB payloads. Payloads up to 4048 bytes are one write; larger ones are three writes (69-79). Then `sync`. |
| `recover`, `recoverKind`, `recoverKinds` (95-112) | fn | Replay with one accepted kind, or with a kind mask (bits 1..31). |
| backend (`anytype`) | duck type | `length`, `readAt`, `appendAll`, `truncate`, `sync`. Implemented by `db_file.File` and by the test mock. |

## 3. Dependencies and layering

- Imports: none.
- Imported by:
  - RAMSQL `src/persistence.sig:15` (and through it `backup.sig`);
  - ZPM `tests/test_durable_log.sig`, `tests/test_db_file.sig`.
- The file is `anytype`, so a faulting fake can be passed without changes
  here. B9.1's fault seam is needed in `db_file`/`persistence`, not in this
  file.
- Layer rule: no violation found.

## 4. Invariants

- **INV-WAL-1** (format): every record is exactly
  `header ‖ payload ‖ trailer`, and every field is written little-endian at
  fixed offsets (55-65).
- **INV-WAL-2** (durable acknowledgement): `appendKind` returns success only
  if every write and the final `sync` succeeded (74-80). FV-06 relies on this.
- **INV-WAL-3** (sequence): recovery accepts only records numbered 1, 2, 3, …
  with no gap. Removing a middle record fails with `InvalidSequence` (132).
- **INV-WAL-4** (truncate only the incomplete tail): recovery truncates only
  when the remaining bytes are shorter than a header (119), or a valid header
  declares more bytes than remain (134). Any complete record with a bad
  header CRC, magic, reserved bytes, payload CRC, trailer magic or trailer
  sequence fails closed (124, 127, 139-140).
- **INV-WAL-5** (kind gate): an unaccepted version or kind fails with
  `UnsupportedLogVersion` before its payload is replayed (126). The WAL is
  left untouched.
- **INV-WAL-6** (replay order): the callback sees each accepted record exactly
  once, in sequence order. Replay errors propagate with no repair (141).
- **INV-WAL-7** (idempotence): running recovery on its own output changes
  nothing and returns the same sequence and length.
- **INV-WAL-8** (prefix property, the target for FV-05): a crash damages only
  the unsynced suffix, which is at most the one in-flight record. Recovery
  then yields exactly the acknowledged records, plus the in-flight record
  either whole or not at all, and never refuses to start. **False today**
  for a full-length torn record (STO-05).

## 5. Current status and evidence

`test_durable_log.sig` (4,834 assertions) uses a mock file with `stable` and
`len` and covers:

- exact staging;
- CRCs and replayed payloads;
- truncation at every byte with correct `truncated_tail`;
- a bit flip anywhere in a complete log never accepted;
- disk-full or short write at every append boundary, then recovery;
- fsync failure: no acknowledgement, and the uncertain record is recovered at
  restart;
- read, replay, truncate and repair-sync failures;
- a missing middle record and a sequence gap;
- premature EOF;
- kind and version gating, and mixed kinds.

`test_db_file.sig` (27 assertions) runs recovery over the native file.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-05 | high | robustness | Every complete-length record with a checksum mismatch fails startup (124, 139-140). Payloads over 4048 bytes are written as three writes (76-78). After power loss, page write-back order allows a full-length final record with stale or zeroed payload sectors, even though that record was never acknowledged (only the last append can be unsynced, 80). The server then refuses to start, which RAMSQL documents (`OPERATIONS.md`, failure diagnosis). | Option A: if a record fails a checksum and ends exactly at EOF with no bytes after it, treat it as torn, truncate it and report `truncated_tail`. Option B: write header + payload, `sync`, then the trailer, `sync`, so a valid trailer proves the payload is durable. Either way, mid-log corruption keeps failing closed. Prove with FV-05. | B9 |
| STO-07 | medium | operability | No checkpoint, segment or compaction exists. Recovery replays the full history (RAMSQL `persistence.sig:4-5`, `CAPACITY.md`). Replay capacity must cover historical peaks. | C8: snapshot + WAL-segment design with a crash-safe protocol; extend the FV-05 model first (C8.2). | C8 |
| STO-09 | medium | durability | An fsync failure after a successful write leaves a recoverable record (tested, as above), so the caller's in-memory abort diverges from disk until restart (`store.md`). | See `store.md`. | B9 |

SRV-04 (offline backup recovery truncates the source's torn tail through
145-148) is tracked on `ramsql/backup.md` and `ramsql/persistence.md`,
lane B5. CRC-32 over payloads up to the 1 MiB default transaction size is
adequate to detect torn writes. A stronger hash (CRC-32C or a 64-bit hash)
could be adopted with a future format version (C8.4). This is an observation,
not a finding.

## 7. Security considerations

- **Trust:** the WAL is local and written only by the locked writer
  (`files-and-pages.md`). A hostile WAL can at most make recovery fail closed:
  - lengths are checked against the caller's scratch (130);
  - reads are bounded by `readExact` (83-90);
  - payload semantics are validated by `change_log` before apply.
- **Denial of service:** STO-05 turns an ordinary power loss during a large
  commit into a manual-repair outage. OPERATIONS.md forbids editing the WAL.
- **Data loss:** a full-length tail that was acknowledged but is later
  damaged by media corruption is indistinguishable from STO-05's torn case
  under option A. That is why option B exists.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B9.2: crash at every write boundary and at random offsets inside frames
  of a scripted workload, using the multi-write path (payload over 4048
  bytes). Compare with the Lean recovery function.
- [ ] B9.4: power-loss simulation with LazyFS or dm-log-writes: drop or
  reorder unsynced sectors of the last record. Recovery must succeed
  (STO-05, after the fix).
- [ ] B9: a full-length final record with a zeroed middle sector. Expected:
  truncate today fails (documents STO-05), and succeeds after the fix. A
  corrupt record followed by a valid record must still fail closed.
- [ ] B9: idempotence: run recovery twice and assert the file is
  byte-identical (INV-WAL-7).
- [ ] C3: generate frames from the Lean model (vectors) and check that
  `recoverKinds` accepts or rejects each one as the model predicts.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-05 | Lean `Wal/Frame`, `Wal/Recovery` + crash replay | INV-WAL-3, 4, 7, 8: prefix property, fail closed on synced damage, idempotence (STO-05 is the counterexample today) | todo |
| FV-06 | TLA+ `WalCommit` | INV-WAL-2 with the store: no publication without a durable append; no publication after a failure until recovery | todo |
| FV-22 | TLA+ `WalCommit` extension | sequence values are unique across crashes (records carry the watermark; see `sequence-and-dml.md`) | todo |
| (none) | fuzz | `recoverKinds` over arbitrary byte files: no trap, and every result is either an error or a valid prefix | todo |

## 10. Benchmarks

- S4: durable write latency, which `appendKind` plus one sync dominates.
- S10: WAL validate and replay, 10 MB to 10 GB (with `change-log.md`
  STO-06).
- Option B for STO-05 adds a second sync per large commit. Measure it in S4
  before choosing.

## 11. Open questions

- STO-05: option A (accept a torn final record) or option B (two syncs)? Or
  B for large records only, with single-write small records protected by
  sector atomicity? The answer depends on which storage devices RAMSQL
  supports, and needs an ADR.
- C8: should the segment format raise the header version to 2, and keep a
  version-1 reader (C8.4)?
