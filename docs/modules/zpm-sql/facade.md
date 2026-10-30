# Module: `sql` facade (`Database`)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | 2643 lines, 138 KB |
| Owner lane | B8 (transactions, resume, horizon), B9 (commit encoding, replay entry), B10 (route flag), C9 (decomposition) |
| Status | beta |
| Formatting (`sig fmt --check`) | `sql.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

> **Wave 0 update (2026-10-30).** The three lock-grant call sites in `sql.sig` (`lockTable`, `RowLocker.lock`, `conflictUpdate`) now go through `M.recordLock` (STO-03, branch `b8/sto03-lock-list`). STO-02 and the other facade findings are still open. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.

## 1. Purpose and scope

`Database(config)` is the SQL engine's single entry point. It owns all engine
state in one caller-owned value:

- tables, catalog and sequences;
- the transaction table and lock table;
- the commit sequence number `csn`, the epoch counter and the shared statement
  workspace.

It runs each statement as a statement subtransaction under a fresh READ
COMMITTED snapshot. It exposes the transaction primitives that `db/store.sig`
drives: begin, statement, prepare/publish commit, abort, savepoints and
park/resume. It also implements COPY FROM, encodes change records and is the
replay entry point.

`ReadContext` runs detached SELECTs on other threads over a shared
`*const Database`.

Out of scope:

- wire protocols;
- file I/O and WAL framing (`db/durable_log.sig`);
- thread safety. Callers serialize every default method (`sql.sig:3-7`), and
  detached reads require the caller to exclude all mutation.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Database(config)` (188) | fn → type | Comptime checks: non-zero capacities, `tables`, `columns` and `name_bytes` ≤ 255 (189-194). |
| `StatementOptions` (177-186) | struct | `resume`, `writes`, `mode`, `hold_result` |
| `bindMemory`, `bindMemoryFor`, `bindStorage`, `bindWorkspace` (646-715) | fn | Bind caller-owned backing for external configs. Overlap is rejected. Only fresh state may be bound. |
| `ReadContext` (369) | struct | `prepare` (441), `run` (465), `execute` (523), `describeResult` (482): SELECT only; no row locks, no sequence functions (447-464). |
| `execute`, `executeLegacy`, `executeWithCommit`, `executeChanges` (835-891) | fn | Single-statement autocommit. Commit hook or record sink runs before `publishCommit` (895-913). |
| `begin` (934) | fn | Free slot, new nonzero epoch; `TransactionCapacity` when full. |
| `readOnly` (962) | fn | No writes, no own or carried sequence log-ahead. |
| `prepareCommit` (969) / `publishCommit` (977) | fn | Encode the record (fallible, no state change), then publish (infallible): stamp, `csn += 1`, release locks, reclaim. |
| `abort` (1000), `failScope` (1025), `abortStatement` (1042) | fn | Undo the whole transaction, the innermost savepoint scope, or the parked statement. |
| `parked`, `waitingFor`, `deadlocked`, `waitCycle` (1063-1086) | fn | Wait-for graph over parked transactions. |
| `savepoint`, `release`, `rollbackTo` (1090-1129) | fn | Nested subtransactions bounded by `config.savepoints`. |
| `statement` (1141) | fn | Run one statement; `WouldBlock` parks it with its work kept. |
| `lastOutcome`, `lastOutcomeInto` (1192-1200) | fn | Held mutation result written after commit. |
| `copyInfo`, `copyBegin`, `copyBuild`, `copyInsert`, `copyEnd`, `copyActive` (2063-2238) | fn | COPY FROM as one open statement subtransaction. |
| `control` (2443), `describe`, `describeIn` (2492-2496) | fn | Parse control commands; bind without executing. |
| `applyChangeRecord` (2541) and replay hooks (2550-2576) | fn | Validate, then apply one kind-3 record. |
| `changeRecordOps` (2579), `encodeCatalogCounters` (2584) | fn | Record header check; counters op. |
| `storageStats`, `rowRef`, `resolveRowRef` (2594-2614) | fn | Introspection. |
| `vacuum` (2618), `usage` (2632) | fn | Full reclamation at the horizon; leak accounting. |

## 3. Dependencies and layering

- Imports: every `sql/*.sig` module (85-105). The only std symbols allowed
  are the two used by `float_text.sig` (81-84).
- Imported by:
  - ZPM `db/store.sig` (as `Raw`);
  - RAMSQL `src/server.sig`, `persistence.sig` and benchmarks;
  - ZPM SQL tests.
- Layer rule: L0 with no platform imports. No violation found.

## 4. Invariants

- **INV-FACADE-1** (statement atomicity): a statement that fails with any
  error except `WouldBlock` leaves the write set, lock list, versions and
  catalog as they were before it began. `dropStatement` undoes the statement
  subtransaction slice (1172-1180, 1048-1061). By design these are not
  undone: sequence values, the `next_rowid` tail (`mvcc.sig:128`), storage
  hints, and physical compaction.
- **INV-FACADE-2** (snapshot per statement):
  - a new statement takes `csn = db.csn` and `cid = ++tx.cid` (1161-1168);
  - a resume reuses the pinned snapshot, or re-snapshots only when `fresh`,
    meaning the statement parked before reading data (1152-1159).
- **INV-FACADE-3** (publication): user-transaction `csn` advances only in
  `publishCommit` (977-996). Every caller persists the record first:
  `autocommit` runs its sink at 906 and publishes at 910, and `Store.commit`
  persists at `store.sig:390` before publishing at 396.
- **INV-FACADE-4** (horizon): `horizon()` must be ≤ the csn of every snapshot
  that can still read, whether pinned, running, resumed or an open COPY.
  **False today**: only pins count (1315-1321), and a resume clears the pin
  before the body runs (1158) (STO-02).
- **INV-FACADE-5** (WouldBlock keeps exactly its slice): a parked statement
  keeps its statement-subtransaction writes and locks. `abortStatement`
  removes exactly that slice (1042-1061).
- **INV-FACADE-6** (read-only commit): `publishCommit` without persistence is
  correct only when `readOnly` holds (962-965). STO-04 shows that "no own
  log-ahead" is not sufficient for sequences.
- **INV-FACADE-7** (detached reads never mutate): `ReadContext` takes
  `*const Self` and rejects locking reads and sequence functions (447-464).
  `validate` rejects buffers that overlap database storage (421-438).
- **INV-FACADE-8** (replay precondition): `applyChangeRecord` runs only
  before any transaction begins. This is documented in `change_log.sig` and
  the comment at 2540, but not enforced (STO-13).
- **INV-FACADE-9** (quiescence): with no active transaction, `usage()`
  reports 0 transactions, 0 row locks, 0 table locks and 0 pending versions
  (2632-2641).

## 5. Current status and evidence

- READ COMMITTED with EvalPlanQual re-checks (`target`, 1726-1743).
  Statement subtransactions, savepoints, transactional DDL, COPY and
  RETURNING across parks all work.
  Evidence: `test_sql_mvcc.sig` (visibility, statement subtransactions,
  savepoints, DDL, deadlocks, error paths); `test_sql_dml.sig` (`acrossParks`,
  `durability`); RAMSQL `tools/test_postgres_isolation.py` (44/44, PLAN §1.3).
- Parallel detached reads: `test_sql_parallel_read.sig` runs 8 threads with no
  concurrent writer. RAMSQL holds `data_gate.lockRead` across `prepare` and
  `run` (`src/server.sig:286`); `lockStore` takes the write side (371-373).
  Whether every store mutation passes through `lockStore` is unverified.
- 2f060c2 adds `B.validateGenerated` after CREATE TABLE (1811-1812). A failure
  there rolls back through INV-FACADE-1.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-01 | critical | durability | OIDs are assigned at statement time (`sql.sig:1801-1805` tables; `1361-1366` sequences), but replay requires them ascending in commit order (`change_log.sig:713`). See `change-log.md`. | Accept any unused OID in replay, and keep the counter at the maximum; see `change-log.md`. | B9 |
| STO-02 | high | correctness | `horizon()` counts only `tx.pin` (1315-1321). A resume clears the pin before the body (1158). `appendRow` and `updateOne` then call `ensureVersions(horizon())` (1939-1941, 2294), which can free versions the resumed snapshot still sees. A resumed UPDATE then skips the row (`visibleAt` returns null, 2263) instead of re-checking it: a lost update. A resumed INSERT ... SELECT re-runs the SELECT and skips by position (1844-1852). The open COPY snapshot `copy_snap` (2152, 2202) is also outside the horizon. | Add a per-transaction running snapshot, set for the statement body and the whole COPY, and include it in `horizon()`. Add a debug check that `vacuum` never frees a version visible to a live snapshot. | B8 |
| STO-04 | high | durability | nextval marks a sequence dirty only when it logs (1513-1517). The record carries only own and carried sequences (2367-2374). `readOnly` ignores cached values (962-965). See `sequence-and-dml.md`. | Global durable watermark per sequence. | B9 |
| STO-10 | medium | correctness | Resume re-executes: INSERT ... SELECT re-runs `queryRows` (1846), so `nextval`, `random()` and `clock_timestamp()` are evaluated again. Rows not yet handled by UPDATE/DELETE re-evaluate volatile WHERE clauses. | Materialize the source once per statement into statement-private storage, or reject volatile functions in parkable DML. | B8 |
| STO-11 | medium | concurrency | `body` takes `row_exclusive` on the DML target only (1252-1262). Sources of INSERT ... SELECT and subqueries take no AccessShare lock, so a concurrent DROP can commit mid-transaction. | Lock every plan source with AccessShare, as the `.query` path does (1233-1235). | B8 |
| STO-13 | medium | robustness | `applyChangeRecord` (2541-2547) does not check for active transactions or parked statements. Slot-choice mismatch: see `change-log.md`. | Refuse with an error when any transaction is active; move replay to a replay-only type. | B9 |
| STO-15 | low | correctness | `tx.cid +%= 1` (1163) wraps after 2^32 statements in one transaction, breaking `xmin_cid < cid`. | Return a capacity error at wrap. | B8 |
| STO-19 | low | maintainability | One 2.6k-line file mixes the facade, ReadContext, DML, COPY, sequences, system views, transactions and replay. ReadContext uses a dense one-line style (380-527). | Split into `txn`, `dml`, `copy`, `views` and `replay` files after B8/B10 coverage exists. | B10, C9 |
| STO-20 | medium | testing | `test_sql_mvcc.sig` executes 122 assertions (PLAN §1.2). There are no capacity failpoints and no test of the resumed-statement horizon. | B8.4 checklist below. | B8 |

## 7. Security considerations

- **Input:** authenticated SQL text from any session. There are no roles
  today, so every user can run DDL. That makes STO-01 a denial of service at
  the next restart for any authenticated user.
- **Denial of service** with bounded state: write-set, version, row, row-lock
  and workspace capacity errors (53200/54000) roll the statement back
  (INV-FACADE-1). Lock-list overflow (STO-03, `mvcc.md`) is the exception.
- **Concurrency contract:** default methods are not thread-safe. Detached
  reads are safe only under the caller's reader/writer gate (FV-16), because
  `horizon()` does not see ReadContext snapshots.
- **No secrets** are handled here.

## 8. Tests

**Existing:**

- `test_sql.sig` (8,431 assertions);
- `test_sql_mvcc.sig` (122);
- `test_sql_dml.sig` (220);
- `test_sql_change_log.sig` (876);
- `test_sql_catalog.sig` (36);
- `test_sql_runtime_workspace.sig` (153);
- `test_sql_parallel_read.sig`;
- `test_db_store.sig` (3,154).

Counts are from PLAN §1.2. They were measured at `9606877`; these test files
are byte-identical at `2f060c2`.

**Missing:**

- [ ] B8: a resumed UPDATE with a full version arena. A concurrent committed
  update of a later row must be re-checked, not skipped (STO-02).
- [ ] B8: a resumed INSERT ... SELECT whose source rows are reclaimed between
  parks: row count and contents must equal a single run (STO-02, STO-10).
- [ ] B8: COPY FROM spanning concurrent commits and a vacuum, with key checks
  (STO-02).
- [ ] B8: a counter-based failpoint on `reserveWrites`, `ensureVersions`,
  `ensureRowSlot`, `grantRow`, `catalog.addVersion` and `Writer.reserve`.
  Oracle: `usage()` and `sameDatabase` are unchanged after each terminal
  error (INV-FACADE-1).
- [ ] B8: `DROP TABLE` of an INSERT ... SELECT source inside another open
  transaction (STO-11).
- [ ] B9: `applyChangeRecord` with an active transaction must be refused
  (STO-13).
- [ ] B8: a ReadContext snapshot older than `db.csn`, run after the gate is
  released, must be rejected or proven impossible (FV-16).

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-06 | TLA+ `WalCommit` | INV-FACADE-3 with `Store.commit`: nothing is published before persist | todo |
| FV-07 | TLA+ `MvccReadCommitted` + simulation | INV-FACADE-2 and EvalPlanQual: no G0, G1a or G1b; no lost update | todo |
| FV-08 | TLA+ `Reclamation` | INV-FACADE-4 including running, resumed and COPY snapshots; counterexample expected until STO-02 is fixed | todo |
| FV-05b | Lean + simulation | published state equals `replay(WAL)` for random schedules | todo |
| FV-16 | TLA+ `RwGate` | no publish or reclaim while a ReadContext runs | todo |
| (none) | property | INV-FACADE-1 under capacity failpoints | todo |
| (none) | property | INV-FACADE-9 after every simulator step that ends all transactions | todo |

## 10. Benchmarks

- S1: instruction counts for the statement path (parse, bind, execute).
- S2: native SQL point, filter, group and join.
- S4: durable insert, point update and DDL commit latency.
- S5: concurrency scaling with parked waits.
- A fix for STO-02 adds one comparison per transaction slot in `horizon()`;
  S1 must stay within the +3% gate.

## 11. Open questions

- Resume by re-execution versus materializing the statement's input once
  (STO-10): which one does the maintainer want? Materializing changes
  workspace budgeting.
- Should DML hold AccessShare on every source relation as PostgreSQL does
  (STO-11), accepting more lock waits?
- Should replay move to a separate type, so `applyChangeRecord` cannot be
  called on a live database (STO-13)?
