Pins ZPM dd1f7a1, which brings in three wave-2 conformance merges: ZPM #113, #114 and #115.

- New types: arrays over every scalar type, the six built-in range types, inet, cidr and macaddr, point and box, xml, pg_lsn and xid8. `pg_type` now lists 78 built-in types.
- New schema objects: foreign keys with referential actions, deferrable constraints and SET CONSTRAINTS, EXCLUDE constraints, ON CONFLICT on any unique key, ordinary and materialized views, CREATE TABLE AS, LIKE and TEMP tables, virtual generated columns, table inheritance and declarative partitioning. MERGE is also new.
- New query forms: NATURAL joins and parenthesized join trees, standalone VALUES and TABLE, expressions in LIMIT and OFFSET, FETCH ... WITH TIES, GROUPING SETS and CUBE, LATERAL, generate_series and other functions in FROM, recursive SEARCH and CYCLE, data-modifying WITH, and TABLESAMPLE.

The core PostgreSQL conformance driver moves from 39 to 71 of 118 programs passing.

Known limit: the server uses external storage, and ZPM does not yet keep secondary index entries there. CREATE INDEX therefore fails with 54000 (IndexCapacity) in this release, where it failed with 42601 before.
