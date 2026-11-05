Pins ZPM 36c95ba, which brings in all of B11 wave 3, plus three ZPM fixes: #127 for Debug-only contract failures, #123 for Debug worker stacks, and #132 for the native SB0 build.

- CREATE INDEX now works in the server. ZPM #118 binds secondary index storage in the external storage region, and the server sets `external_indexes = 4`. That costs 4 × pow2(2 × versions) bytes per table per index: 64 KiB at the default 8,192 versions. Indexes serve equality probes, and unique indexes enforce 23505.
- Transactions and sessions (ZPM #120):
  - REPEATABLE READ, and SERIALIZABLE as relation-level SSI (sound, but it raises more 40001 errors than PostgreSQL's tuple-level SSI);
  - SET TRANSACTION, READ ONLY and DEFERRABLE;
  - LOCK TABLE in all eight modes, row lock strengths, and try-advisory locks;
  - cursors, SQL PREPARE and EXECUTE, and transactional SET, SHOW and RESET;
  - LISTEN, NOTIFY and UNLISTEN.
- Idle LISTEN clients now receive NOTIFY without sending a query, as with PostgreSQL. The server records ZPM's notification sequence (#121) after each statement. An idle PostgreSQL client takes the store lock to drain its queue only when that sequence has moved.
- Routines (ZPM #124): SQL and PL/pgSQL functions, procedures, CALL and DO; BEFORE and AFTER row and statement triggers; and DO INSTEAD/ALSO NOTHING rules.
- JSON and aggregates (ZPM #125):
  - JSON aggregates, jsonb modification functions, SQL/JSON path and JSON_TABLE;
  - ORDER BY inside aggregates, `array_agg` and `string_agg`;
  - ordered-set and hypothetical-set aggregates.
- Roles and security (ZPM #128):
  - roles, GRANT and REVOKE with privilege checks (42501), SET ROLE;
  - row-level security policies, COMMENT ON and `pg_roles`;
  - catalog-only extension, foreign-data-wrapper, server and publication objects.

  `current_user` and `session_user` report the engine's bootstrap superuser, `ramsql`, whatever user name the client logs in with.
- Text (ZPM #131): POSIX regular expressions and SIMILAR TO, COLLATE, full-text search with the `simple` configuration, `pg_typeof` and `pg_get_serial_sequence`.
- INSERT without a column list accepts fewer values than columns and fills the rest with defaults. `ON CONFLICT ON CONSTRAINT` no longer accepts the name of a plain unique index (42704). Both are from ZPM #119.
- The server raises ZPM's database-wide session and catalog pools, which default to test sizes:
  - 64 cursors sharing 1,024 rows; 32 prepared statements per session;
  - 64 listening sessions, a 1,024-entry notification queue and 256 advisory locks;
  - 128 routines, 128 triggers, 32 rules and 512 catalog objects.

  These add about 2.5 MB to the database's fixed metadata.

The core PostgreSQL conformance driver moves from 71 to 100 of 118 programs.
