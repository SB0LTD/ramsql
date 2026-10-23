# Store API migration (READ COMMITTED, FEAT-004 / design §6)

RAMSQL no longer allocates per-client `Raw` copies or an autocommit
workspace, and `Persistence.init/initExisting(path, store, recovery_buffer)`
dropped the workspace parameter. Line numbers refer to `main` (c57fe72).

| File, line(s) | Old assertion | New assertion |
| --- | --- | --- |
| tests/persistence.sig 41-44, 84-85, 266-272 and every `persistence.init(..., &workspace, ...)` | Distinct scratch `workspace`, `tx_workspace`, `tx_log` per connection | No workspace exists; connections are `.{ .store, .id }` |
| tests/persistence.sig 59, 89, 93, 95, 119, 124, 210 | Ordering via `store.generation == n` | `persistence.sequence == n` (the WAL sequence) and `store.database.csn == n` (the published commit sequence) |
| tests/persistence.sig (alpha.2 sentinel block) | Autocommit recovery does not touch the multi-statement scratch database (sentinel table in the workspace) | Legacy records replay as one MVCC transaction each with no copy: afterwards `tableCount() == 1`, `csn == 2` and zero transactions, locks and uncommitted versions |
| tests/persistence.sig (multi-statement legacy batches) | Bad second statement cannot publish the first (private candidate) | Same assertion; the record's transaction aborts instead of discarding a copy |
| tests/backup.sig 37-41 | `workspace`, `tx_workspace`, `tx_log` | Removed; the backup source and restored store use only the recovery buffer |

Per-contract assertion counts (`main` c57fe72 -> now):

| Contract | main | FEAT-004 |
| --- | --- | --- |
| contract-persistence | 41 | 71 |
| contract-backup | 10 | 11 |
