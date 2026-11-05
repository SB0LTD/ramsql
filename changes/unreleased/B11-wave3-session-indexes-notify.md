Pins ZPM adc3258, which brings in the first B11 wave-3 merges and the ZPM #127 fix for Debug-only contract failures.

- CREATE INDEX now works in the server. ZPM #118 binds secondary index storage in the external storage region, and the server sets `external_indexes = 4`. That costs 4 × pow2(2 × versions) bytes per table per index: 64 KiB at the default 8,192 versions. Indexes serve equality probes, and unique indexes enforce 23505.
- Transactions and sessions (ZPM #120):
  - REPEATABLE READ, and SERIALIZABLE as relation-level SSI (sound, but it raises more 40001 errors than PostgreSQL's tuple-level SSI);
  - SET TRANSACTION, READ ONLY and DEFERRABLE;
  - LOCK TABLE in all eight modes, row lock strengths, and try-advisory locks;
  - cursors, SQL PREPARE and EXECUTE, and transactional SET, SHOW and RESET;
  - LISTEN, NOTIFY and UNLISTEN.
- Idle LISTEN clients now receive NOTIFY without sending a query, as with PostgreSQL. The server records ZPM's notification sequence (#121) after each statement. An idle PostgreSQL client takes the store lock to drain its queue only when that sequence has moved.
- INSERT without a column list accepts fewer values than columns and fills the rest with defaults. `ON CONFLICT ON CONSTRAINT` no longer accepts the name of a plain unique index (42704). Both are from ZPM #119.

The core PostgreSQL conformance driver moves from 71 to 79 of 118 programs.
