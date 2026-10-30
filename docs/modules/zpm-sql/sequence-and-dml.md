# Module: `sequence` and `dml` (sequences, row construction, key checks)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/sequence.sig`, `zpm/src/core/sql/dml.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Both identical to `9606877`. |
| Layer | ZPM core (L0) |
| Size | `sequence.sig` 226 lines, 9 KB; `dml.sig` 159 lines, 8 KB |
| Owner lane | B9 (sequence durability), B8 (key checks under concurrency), B10 (`dml.sig` ownership in wave 2) |
| Status | beta |
| Formatting (`sig fmt --check`) | `sequence.sig` clean; `dml.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

**`sequence.sig`** implements PostgreSQL sequence arithmetic with CACHE 1:

- option resolution (`resolve`);
- `next` and `set`;
- per-session `currval`/`lastval`;
- a slot bitset.

Values are non-transactional. Durability follows PostgreSQL's log-ahead
scheme (`SEQ_LOG_VALS`): `next` logs a value 32 increments ahead
(`logged_value`), and later calls are served from that range until it is used
up. The logged value rides on a change record. The facade carries an aborted
transaction's logged value into the next record anyone writes (header 5-10).

**`dml.sig`** builds rows for INSERT, COPY and UPDATE:

- assignment casts;
- typmod coercion: NUMERIC(p,s) rounding and TIMESTAMP(p) rounding;
- NOT NULL and int2/int4 range checks;
- DEFAULT, identity and generated columns.

It also provides `keyCheck`, the primary-key uniqueness check against the
newest state (PostgreSQL's dirty snapshot).

Neither file writes versions or records. The facade does (`sql.sig`
`appendRow`, `updateOne`, `encodeWrites`).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `log_values` (`sequence.sig:15`) | const | 32 |
| `Options`, `Spec`, `resolve` (18-72) | struct / fn | PostgreSQL `init_params`: 22023 for a bad increment or bounds; 0A000 for CACHE ≠ 1. |
| `Seq` (79-98) | struct | live/pending flags, `last_value`, `is_called`, `log_cnt`, `logged_value`, `logged_called`, owner |
| `next(seq)` (106-154) | fn | Returns `{value, logged}`. `logged` is true only when this call wrote a new log-ahead value (110-114, 146-149). |
| `set`, `restore` (157-173) | fn | `setval` (always logged); replay of a sequence op. |
| `Session`, `Set` (176-224) | struct | per-session currval/lastval; slot bitset |
| `store(column, value, session)` (`dml.sig:25`) | fn | Assignment coercion and checks for one value. |
| `insertedRow`, `copiedRow`, `updatedRow` (80, 95, 108) | fn | Build a complete row. SET expressions see the old row only. |
| `keyCheck(table, key, own_slot, snap)` (129-157) | fn | `free`, `taken: version` or `wait: slot`. |

## 3. Dependencies and layering

- `sequence.sig` imports `types` only. It is imported by `mvcc.sig` (dirty
  set), `change_log.sig` (replay `init`/`restore`) and `sql.sig`.
- `dml.sig` imports `types`, `ast`, `storage`, `expr` and `value` (10-14). It is imported by
  `sql.sig`.
- Layer rule: no violation found.

## 4. Invariants

- **INV-SEQ-1** (arithmetic): within one process, the values returned by
  `next` follow PostgreSQL's increment, bounds and cycle rules, and never
  repeat unless `CYCLE`.
- **INV-SEQ-2** (durable watermark): at every acknowledgement, the last
  durable logged value of each sequence is ≥ every value already returned to
  any session. **False today** (STO-04). FV-22.
- **INV-SEQ-3** (setval): `set` always marks the transaction dirty
  (`sql.sig:1522`), so a `setval` is persisted by the next commit of that
  transaction.
- **INV-DML-1** (key check): with `k` the key value, `keyCheck` returns:
  - `taken` if a committed live version has `k`, or a version this
    transaction created and still holds has `k`;
  - `wait(s)` if another live transaction `s` inserted a version with `k` or
    has an uncommitted delete of one;
  - `free` otherwise.

  The row being updated (`own_slot`) is excluded (139-155). FV-21.
- **INV-DML-2** (SET semantics): every SET expression and generated column of
  an UPDATE is evaluated against the old row, never a partly updated one
  (108-119).
- **INV-DML-3** (validation before write): `store` applies typmod, NOT NULL
  and range checks before the facade allocates a version. A failing value
  changes nothing.

## 5. Current status and evidence

- `test_sql_dml.sig` (220 assertions):
  - `sequences`, `defaults`, `identity` (a rolled-back identity value is not
    reused);
  - `generated`;
  - `upsert`: ON CONFLICT with waits, deleters and cardinality errors;
  - `acrossParks`;
  - `durability`: a carried log-ahead value rides on the next record, even a
    plain SELECT's.
- `test_sql_index.sig` exercises `keyCheck` indirectly through PK updates and
  `randomModel`.
- No test has a second transaction draw from a log-ahead range that the
  first transaction logged but has not yet committed.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-04 | high | durability | Failing schedule: transaction A's `nextval` logs `v+32` (`sequence.sig:146-149`) and only A is marked dirty (`sql.sig:1515`). B's `nextval` returns `v+2` from the cached range with `logged = false`. B's commit then carries neither A's dirty set nor a carried value (`sql.sig:2367-2374`). If B only read, `readOnly` holds and nothing is persisted (`sql.sig:962-965`). A crash before A ends restores the sequence to the last durable value ≤ v, so `v+2` is returned again. With identity primary keys, this gives duplicate-key errors after restart; without a key, duplicate ids. | Keep a per-sequence `durable_value` in the database. Any commit, including one that called `nextval` without writing, must encode every sequence whose `logged_value` is ahead of `durable_value`, and then advance it. Alternatively, persist the log-ahead record immediately (PostgreSQL's approach). | B9 |
| STO-10 | medium | correctness | `nextval` inside INSERT ... SELECT is evaluated again when a parked statement resumes (`sql.sig:1846`), consuming extra values. Other volatile functions are re-evaluated too. | See `facade.md`. | B8 |

## 7. Security considerations

- Sequence names and `regclass` text come from SQL. `regclassName` bounds
  them by `name_bytes` (`sql.sig:1421-1437`).
- STO-04 is an integrity issue after a crash, not an attacker-controlled one.
  Applications that use identity columns as external identifiers could hand
  out the same id twice.
- `keyCheck` probes the key index without bounds beyond the 50% load factor
  (`storage.md` INV-STOR-4).

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B9: the STO-04 crash test. A takes `nextval` and stays open; B takes
  `nextval` and commits (read-only, and again with an insert); crash before
  A ends; recover; the next `nextval` must exceed every value returned
  before the crash.
- [ ] B9: the same with A aborting before the crash (carried path), and with
  A committing (A's record must carry the newest logged value).
- [ ] B9: a model check of FV-22 using the B9 crash harness: every `nextval`
  returned before any crash point is below the recovered next value.
- [ ] B8: a property test of INV-DML-1. Random concurrent
  insert/update/delete of a small key domain; `keyCheck` must agree with a
  reference that enumerates all versions.
- [ ] B7: differential tests of `store` coercions (NUMERIC rounding,
  TIMESTAMP(p), int2/int4 range) against PostgreSQL 16.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-22 | TLA+ `WalCommit` extension + crash replay | INV-SEQ-2: `nextval` values are never returned twice, including across crash and recovery; gaps allowed (fails today: STO-04) | todo |
| FV-21 | Lean model + simulation | INV-DML-1: unique-key decisions agree with the set of live and in-flight versions | todo |
| FV-11, FV-12 | Lean + vectors | `store` coercions for NUMERIC and TIMESTAMP typmods | todo |
| (none) | property | INV-SEQ-1 against a reference implementation of PostgreSQL `nextval_internal` | todo |

## 10. Benchmarks

- S4: insert with identity columns. The fix for STO-04 may add one sequence op
  to more records. Measure bytes per commit and commit latency before and
  after.

## 11. Open questions

- STO-04 fix: a durable watermark carried by every commit, or an immediate
  non-transactional sequence record with its own fsync? The first adds no
  fsyncs but enlarges records; the second matches PostgreSQL but costs a
  flush per 32 values.
