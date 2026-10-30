# Module: `timezones`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/timezones.sig`; wiring in `ramsql/src/server.sig:102-120, 903-905` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL |
| Size | 166 lines, 7.9 KB |
| Owner lane | `A6` (code); `B7` (PostgreSQL time-zone semantics) |
| Status | beta |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here) |

## 1. Purpose and scope

This module is the store's zone provider. It maps a client-supplied zone name
to validated TZif bytes from an operator-chosen zoneinfo directory and caches up
to `profile.timezones` zones (default 8, `build.sig:122`) of at most 16 KiB each
in caller-owned storage. Lookups are case-insensitive through an optional
`ramsql-zones.idx` written by `tools/fetch_tzdata.py`. ZPM `sql.tzif` decodes and
validates the bytes. This module only decides where bytes come from and when a
cache slot may be reused. Without a directory (the Windows and native SB0
default) only UTC, numeric offsets and POSIX TZ strings work. Those are handled
in ZPM and never reach this module.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `file_capacity` (23) | const | 16384 bytes per cached zone |
| `index_name` (24) | const | `"ramsql-zones.idx"` |
| `validName(name)` (36) | fn | true only for `[A-Za-z0-9_+\-/]{1,64}` without a leading `/` |
| `Timezones(Zone, capacity)` (45) | fn → type | the cache |
| `.directory` (55), `.in_use` (57) | fields | set by the server (`server.sig:903-904`) |
| `.lookup(context, name, zone, canonical)` (122) | fn | name to zone plus canonical spelling; errors `InvalidZoneName`, `ZoneUnavailable`, `UnknownZone`, `TimeZoneCapacity` |

## 3. Dependencies and layering

- Imports: ZPM `sql` (`tzif.validate`), `db_file` (`openReadOnly`, `readAt`).
- Imported by: `src/server.sig:30` (`Zones`, 102; global `zones`, 104; installed as `store.zone_provider`, 905).
- Layer rule: no violation.

## 4. Invariants

- **INV-TZ-1** Path confinement: a name reaches the filesystem only if
  `validName` holds (124, and 131 for index spellings). It has no `.` and no
  leading `/`, so the joined path `dir/name` cannot leave `dir` except through
  symlinks inside `dir`.
- **INV-TZ-2** Every cache entry with `name_len > 0` holds bytes that passed
  `sql.tzif.validate` (158-161). A reused slot is invalidated before loading
  (145-146), so a failed load leaves no stale entry.
- **INV-TZ-3** A full cache evicts only an entry for which `in_use` returns
  false (134-138). The server's `zoneInUse` (`server.sig:112-120`) scans
  `settings`, `base`, `tx_start`, `startup` and the savepoint copies of every
  state-2 client.
- **INV-TZ-4** A file larger than 16 KiB is rejected, not truncated (153-157).
- **INV-TZ-5** In parallel mode, lookups and eviction run only on the exclusive
  path. A detached read that needs zone resolution falls back to it
  (`server.sig:305`).

## 5. Current status and evidence

`tools/test_postgres_semantics.py` passes the reference image's tzdata through
`--timezone-dir` and checks `SET TIME ZONE`, `SHOW TimeZone`, transaction
rollback of the zone setting, and timestamptz rendering against PostgreSQL. An
unknown zone (`Mars/Base`) is checked too. There is no Sig test of this module,
and cache eviction has no direct test.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-18 | low | perf | each cache miss reads the whole index (71-106) and the TZif file (140-157) with blocking I/O while the server holds the global write lock (`server.sig:220`) | preload the index; load zones outside the gate, or at startup | A6 |

## 7. Security considerations

- Attacker input: the zone name in `SET TIME ZONE` and in literals, from any
  authenticated session. It is limited to 64 bytes with a restricted alphabet
  (INV-TZ-1). The operator controls the directory and the index, and the index
  spelling is re-validated (131).
- No content oracle: an invalid or non-TZif file and a missing file both give
  `UnknownZone`. The bytes are never returned to the client.
- DoS: one client can cycle through many distinct zones and force file reads
  under the global lock (SRV-18). When all 8 slots are referenced, a new zone
  fails with `TimeZoneCapacity`, which is bounded and intended. A FIFO or slow
  network filesystem in the zone directory would block the reactor
  (operator-controlled; unverified in practice).
- Unverified: whether any holder of zone bytes outside the `Settings` copies
  that `zoneInUse` scans (for example the installed `database.session` during a
  statement) can survive an eviction. ZPM `store.sig:440-444` lists the scanned
  copies.

## 8. Tests

- **Existing:** `tools/test_postgres_semantics.py` zone cases (section 5).
- **Missing:**
  - [ ] A6: Sig contract with a temporary zone directory. Cover exact and
    case-insensitive lookup through the index, an index spelling that fails
    `validName`, a file over 16 KiB, an invalid TZif file, and reuse of a slot
    after a failed load (INV-TZ-2, INV-TZ-4).
  - [ ] A6: fill all slots with referenced zones and check `TimeZoneCapacity`.
    Release one reference and check that eviction reuses only that slot (INV-TZ-3).
  - [ ] B7: fuzz `validName` against a reference regex (INV-TZ-1).
  - [ ] C2: worker test with parallel readers holding zone settings while
    another session forces eviction (INV-TZ-5).

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| INV-TZ-1 | property | for every accepted name, the normalized path has `dir` as a prefix and contains no `..` component | todo |
| INV-TZ-3 | model-based | no slot whose bytes are referenced by a live session is overwritten | todo |
| FV-12 | Lean (ZPM) | civil-day conversions; covers the temporal arithmetic that consumes these bytes, not this cache | todo |

## 10. Benchmarks

None. A cold `SET TIME ZONE` costs one index scan plus one file read. This
should be measured once SRV-18 is addressed.

## 11. Open questions

- Should zones be loaded at startup from a configured list, removing runtime
  file I/O completely?
- PostgreSQL accepts case-mismatched names without an index. RAMSQL returns
  22023 (divergence F3, doc comment 12-13). Should the index become mandatory
  when `--timezone-dir` is given?
