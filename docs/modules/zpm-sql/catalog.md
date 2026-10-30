# Module: `catalog` (versioned catalog and system views)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/catalog.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee`. Identical to `9606877`. |
| Layer | ZPM core (L0) |
| Size | 344 lines, 18 KB |
| Owner lane | B9 for STO-01 (OID order); B4, B11, C9 for catalog features |
| Status | beta |
| Formatting (`sig fmt --check`) | `catalog.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

> **Wave 0 update (2026-10-30).** INV-CAT-5 changes with the STO-01 fix (ZPM branch `b9/sto01-replay-order`). Replay no longer requires creates in OID order; it requires unique OIDs ≥ `first_oid`, with the counter at the maximum. The fix is reviewed, but it is not merged yet; see `PLAN.md` §18. Until it merges, the rest of this page describes the `2f060c2` baseline.

## 1. Purpose and scope

The catalog stores table and sequence definitions as ordinary MVCC data. Name
lookup follows the same snapshot rules as rows, so DDL is transactional.

There are four base relations, each a `Storage` table with 6 columns and two
versions per row (101-112):

- `cat_relations` (oid, name, relkind, namespace, slot);
- `cat_attributes`;
- `cat_attrdef`;
- `cat_sequences`.

Catalog rowids are derived from slots: `slot + 1` for a table's relation row,
`slot·columns + i + 1` for its attributes, and `tables + slot + 1` for a
sequence.

The module also defines the system views (`pg_class`, `pg_attribute`,
`information_schema.columns` and others, 45-98). The facade fills them under
the statement snapshot (`sql.sig:1592` `fillViewRows`).

Out of scope: OID allocation, which is in the facade (`sql.sig:1801-1805`,
`1361-1366`). Table storage reuse is in `sql.sig` `reclaimTables`
(1323-1336).

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Relation`, `namespaces`, `View`, `viewColumns`, `findView` (26-98) | enum / const / fn | Static schema of the base relations and views. pg_catalog views resolve unqualified; information_schema views only when qualified. |
| `Catalog(config)` (100) | fn → type | Four relations sized `max(tables·columns, tables + sequences)` rows (101). |
| `create`, `createSequence` (228-241, 153-165) | fn | Add relation and attribute (or sequence) rows owned by `xmin`. On error, every version created is freed (`errdefer`). |
| `drop`, `dropSequence` (249-266, 167-182) | fn | Mark the rows visible to `snap` with `xmax`. |
| `stamp`, `undo` (306-321) | fn | Set the csn at publish; free or unmark at abort. |
| `find`, `lookup`, `visibleRelations`, `attributeCount` (188-209, 279-304) | fn | Snapshot lookups by name or slot. |
| `creator(name, snap)` (286-294) | fn | Another live transaction's uncommitted relation row **with this name**. |
| `vacuum`, `versions`, `pending` (324-341) | fn | Reclaim at a horizon; leak accounting. |

## 3. Dependencies and layering

- Imports: `types`, `storage` (22-23).
- Imported by:
  - `sql.sig` (DDL, views, `reclaimTables`);
  - `mvcc.sig` through `db.catalog` (stamp and undo);
  - `binder.sig` (name resolution);
  - `exec.sig` (view enumeration).
- Layer rule: no violation found.

## 4. Invariants

- **INV-CAT-1** (name uniqueness per snapshot): for any snapshot, at most one
  visible `cat_relations` row has a given name. Enforcement:
  - the binder rejects a visible name with `TableExists`
    (`binder.sig:3017`, `3037`, `3056`; for sequences also `sql.sig:1349`);
  - the facade waits for `creator()` when another transaction is creating the
    same name (`sql.sig:1782-1787`).
- **INV-CAT-2** (DDL atomicity): a table's catalog rows are created or marked
  in the same write set as its `create_table`/`drop_table` entry, so they are
  stamped and undone with it (`mvcc.sig:144-145`, `206-207`).
- **INV-CAT-3** (slot reuse): a table or sequence slot is reused only after
  its `dead_csn ≤ horizon` (`sql.sig:1323-1336`). At that point its catalog
  versions are vacuumed. `addVersion` refuses a rowid that is still present
  after a vacuum (`CatalogCapacity`, 213-216).
- **INV-CAT-4** (sizing): user DDL hits `TableCapacity` before
  `CatalogCapacity` (header 15-18; `test_sql_mvcc.sig` `ddl`).
- **INV-CAT-5** (OID order, cross-module): the change log replays creates in
  commit order and requires their OIDs to be ascending. The catalog does not
  serialize DDL across names, so this does **not** hold (STO-01).

## 5. Current status and evidence

- `test_sql_catalog.sig` (36 assertions):
  - type views;
  - lifecycle (create, drop, re-create);
  - replicated and replayed catalogs.
- `test_sql_mvcc.sig` `ddl`:
  - uncommitted tables are invisible to others;
  - a same-name CREATE waits for the creator, then fails with `TableExists`;
  - DROP waits for every AccessShare holder;
  - catalog capacity.
- RAMSQL isolation schedules DDL1-DDL4, CAT1 and SP8, each followed by a
  durable restart.
- No test covers two different names created concurrently and committed in
  reverse order.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| STO-01 | critical | durability | `creator` matches by name only (286-294). Two transactions can each run CREATE TABLE with different names at once. Each takes its OID at statement time (`sql.sig:1801-1805`). If they commit in reverse OID order, replay rejects the second create (`change_log.sig:713`) and startup fails (RAMSQL `persistence.sig:84`). | Fix replay (`change-log.md`) rather than serializing DDL. If OIDs must stay commit-ordered, assign them at publish. That needs record encoding to happen after the OID is final. | B9 |

## 7. Security considerations

- **Name input:** identifiers from SQL; replayed names are re-validated with
  `validName` (`change_log.sig:567-571`).
- **Denial of service:** any session can fill `config.tables` or
  `config.sequences` (`TableCapacity`). Today every authenticated user can
  run DDL (no roles: B4). Combined with STO-01, two users can make the next
  restart fail.
- `creator()` scans every catalog version (288). That is bounded by the
  small catalog size.

## 8. Tests

**Existing:** section 5.

**Missing:**

- [ ] B9: A `BEGIN; CREATE TABLE a`, B `BEGIN; CREATE TABLE b; COMMIT`,
  A `COMMIT`, then restart. Expect recovery to succeed with both tables
  (STO-01). Repeat with identity columns (table plus sequence OIDs) and with
  CREATE SEQUENCE.
- [ ] B9: the same schedule with an abort in between (OID gaps), then replay.
- [ ] B11: a property test that every catalog view row set equals what the
  definitions imply, under random DDL with savepoints and rollbacks.
- [ ] B8: concurrent DROP and CREATE of the same name across savepoints:
  INV-CAT-1 holds for every snapshot taken in the simulator.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-05b | Lean + simulation | replay refinement with concurrent DDL (STO-01 is the known counterexample) | todo |
| FV-07 | TLA+ `MvccReadCommitted` | catalog visibility follows the same READ COMMITTED rule as rows (INV-CAT-1, INV-CAT-2) | todo |
| FV-08 | TLA+ `Reclamation` | INV-CAT-3: a slot is reused only when no snapshot can see its old definition | todo |

## 10. Benchmarks

- S4 includes DDL commit latency. Catalog cost per DDL is small and bounded;
  no separate gate.

## 11. Open questions

- Fix STO-01 in replay (accept any unused OID) or in allocation (assign at
  publish)? Replay is simpler and keeps today's OID numbering. Allocation at
  publish makes OIDs commit-ordered but changes `prepareCommit`.
- Should catalog views gain `pg_roles` and privileges with B4, and does that
  change the relation sizing rule?
