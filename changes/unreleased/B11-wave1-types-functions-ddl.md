Pins ZPM 12abe24, which brings in three wave-1 conformance merges: ZPM #109, #110 and #111.

- New column types: VARCHAR(n), CHAR(n), UUID, BYTEA, TIME, TIMETZ, BIT, VARBIT and MONEY. Each has wire send/receive and is replayed from the WAL.
- New literal syntax: E'', dollar-quoted and U&'' literals.
- New functions and operators: PostgreSQL string, numeric, temporal and formatting functions (including to_date and to_number), integer bitwise operators, OVERLAPS, normalize(), and statistical, bitwise and interval aggregates.
- New DDL and DML: TRUNCATE, transactional ALTER TABLE, schemas, CHECK, UNIQUE, composite and named primary keys, SERIAL and sequence options, UPDATE ... FROM, DELETE ... USING and tuple assignment. Each of these writes durable change-log records.

The core PostgreSQL conformance driver moves from 12 to 39 of 118 programs passing.
