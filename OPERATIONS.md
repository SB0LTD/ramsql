# Beta operations

This beta is for controlled evaluation on trusted loopback hosts. It has one
reactor, one shared database, READ COMMITTED transactions and synchronous WAL.
It is not a supported public service or replacement for all PostgreSQL tooling.

PostgreSQL simple Query messages can carry multiple supported SQL commands.
The server checks the entire frame before running its first command and
streams one command's response at a time. Implicit segments commit at the end
of the frame or at an explicit transaction boundary. A raw syntax error
rejects the whole frame. A runtime error stops execution at that command and
rolls back the current implicit segment. Earlier explicit commits persist.
Send `COPY` in its own Query message. A batch containing `COPY` or unsupported
raw grammar returns `0A000` before running any command.
`PREPARE TRANSACTION` in a batch requires an explicit transaction block.

Extended Parse/Bind/Execute groups retain implicit writes until Sync or a
successful multi-command simple Query completes on the same connection.
Flush and a single ordinary simple Query leave the transaction open.
Extended COPY FROM and COPY TO run as portals and share the same transaction
boundary. PostgreSQL 18.3 can report idle before publishing writes if Sync
immediately follows a single simple Query in a mixed-protocol group. RAMSQL
commits the group before reporting idle.

## Installation and startup

Verify the release checksums and inspect `provenance.json`, `RELEASE.md` and
`ramsql --capabilities`. Hosted archives provide a native executable. The
packaged `create_verifier.py` uses Python's standard library only; the server
needs no scripting runtime. From source the helper is under `tools/`.

```sh
python create_verifier.py --output postgres.verifier
./ramsql --capabilities --rows 4096 --versions 8192
./ramsql --data ramsql.wal --create-data --rows 4096 --versions 8192 \
  --auth-user postgres --auth-verifier-file postgres.verifier --port 5432
```

Pass `--create-data` only on the first start. Without it a missing data file
(`--data`, default `ramsql.wal` relative to the working directory) fails with
`DataFileMissing` before any listener opens and nothing is created. Startup
prints `ramsql: data file <absolute path> (opened)` or `(created)` to stderr.

The helper uses a random salt, PBKDF2-HMAC-SHA256 and exclusive file creation;
it never overwrites credentials. POSIX creation requests mode 0600. Windows
inherits directory ACLs: restrict them yourself. For automation only, explicit
`--password-stdin` reads one password line; do not put passwords in argv, logs
or source control. The helper accepts printable ASCII passwords; externally
provision PostgreSQL SCRAM verifiers for other encodings. With an existing WAL,
use `--auth-catalog-role` for one configured identity or `--auth-credentials-file`
with the [credential list setup](README.md#authenticated-persistent-beta) for
multiple identities. Create their SQL roles and privileges before startup.
Every configured role must resolve to an eligible login before the listener opens.
The listener accepts up to 512 credential records, subject to the available
SQL catalog capacity. Keep the `RAMSQL-SCRAM-V1` first line when editing the
list so mock salts remain stable, and protect backups of the credential file.

The server prints each actual listener address. `--port 0` selects an ephemeral
port. PostgreSQL defaults to 5432, MySQL to 3306, TDS to 1433 when selected by
`--protocol`. Extra `--mysql-port`/`--mssql-port` listeners share the same data.
Only `--insecure-local` permits unauthenticated adapters; it also disables PG
authentication. Bind addresses are loopback only; TLS is not available.

Use client connect/query timeouts. `--idle-ms` expires idle sessions, with special
treatment for parked lock waits. `--max-query-work` limits execution checkpoints;
it is distinct from SQL `statement_timeout`, `lock_timeout` and deadlock checks.
PostgreSQL `idle_in_transaction_session_timeout` is a session setting in
milliseconds. It defaults to zero and supports `SET LOCAL`, `SHOW` and `RESET`.
A nonzero value starts after `ReadyForQuery` in an open or failed transaction
block. Expiry rolls the transaction back, releases its locks, sends fatal
SQLSTATE `25P03`, and closes the connection. Running and lock-waiting statements
use their own limits. `--idle-ms` remains a separate server-side socket limit.

PostgreSQL `transaction_timeout` starts at the beginning of an explicit or
implicit engine transaction. It runs during execution, lock waits and time
between statements, and expires with fatal SQLSTATE `25P04`. Zero disables
the timer. A positive-to-positive change preserves an armed deadline. COPY
input checks a pending expiration when input resumes. The extended protocol
currently publishes autocommit at Execute. A shared implicit transaction
across Parse, Bind, Execute and Sync requires separate compatibility work.

PG cancellation is tested. TDS ATTENTION acknowledges an idle protocol request;
it is not a general asynchronous running-query cancellation implementation.

## Optional PostgreSQL read cache

`--read-cache-bytes BYTES` reserves a shared cache for eligible PostgreSQL
autocommit analytical reads on a server with at least two workers. Zero is
the default. The same option works with `--workers auto` when the machine
can support at least two workers after charging the cache's page-rounded
region. Cluster mode uses one worker and rejects a positive cache budget.

Both `--capabilities` and `--machine-capacity` show the number of entries,
admitted payload bytes and page-rounded cache bytes. Each entry accepts SQL
up to 1 KiB and at most 128 result rows. For large tables with a committed
integral scan projection, the same opt-in budget may reserve up to one eighth
for entries holding 129 to 2,048 rows of up to four direct non-null integral
columns. The query must repeat within the same snapshot before publication.
The cache reports the projected tier's payload bytes, entry count and row
capacity in both capacity commands. A result can be reused within its
committed database snapshot. Writes, including DDL, advance the snapshot and
invalidate earlier results. Restricted roles, nondefault session settings,
work limits and timeouts use ordinary query execution. A restart starts with
an empty cache. A new medium query key runs the complete scan for its first
two successful observations and pays the packing cost on the second. Writes
advance the database snapshot, so even an unchanged query must build
observations again. The projected tier shares the requested byte budget with
the small aggregate tier. Measure cold-start and write-heavy workloads with
their own traffic patterns and cache budgets.

A source build may opt into 32-byte external MVCC headers with
`sig build -Dcompact-version-stamps=true`. The default build keeps its
existing layout. In a build with `-Dintegral-version-banks=true`, the
flag saves 16 bytes per provisioned version slot. The WAL format is
shared between builds. Both `--capabilities` and `--machine-capacity`
report the selected layout and the size of each provisioned version.

## Stop, backup, restore and upgrade

There is no service-manager installer, graceful drain command or checkpoint
shutdown facility. Stop clients and the process using your OS service/process
tools. Acknowledged writes were already flushed; uncommitted transactions do not
publish. A process termination test is not proof of physical power-loss safety.

With the writer stopped:

```sh
./ramsql --data ramsql.wal --rows 4096 --versions 8192 --backup-to backup.wal
./ramsql --data backup.wal --rows 4096 --versions 8192 \
  --auth-user postgres --auth-verifier-file postgres.verifier
```

Backup acquires the writer lock, validates/replays the source and publishes a
flushed whole-WAL copy; existing destinations are refused. Keep copies on
separate storage and test restore. Do not edit/truncate a WAL to bypass errors.
There is no online backup, WAL compaction, pg_dump/pg_basebackup equivalence,
replication or point-in-time recovery facility.

Current commits use typed kind-3 records inside version-1 framing. Frozen legacy
kind-1/2 SQL logs have restricted recovery support; old binaries cannot read
kind 3. The beta does not introduce another WAL format, but a comprehensive
cross-version migration/downgrade matrix is still absent. Preserve the old
binary/WAL and test an unchanged copied WAL with this version before upgrading.
Never downgrade the only copy of data or assume a successful open proves every
future workload fits. Reopen/restore must cover historical capacity peaks.

## Failure diagnosis

- Startup without credentials fails rather than silently enabling trust mode.
- `DataFileMissing` names the absolute data path it looked for. Check the
  working directory and `--data` before using `--create-data`.
- A second writer is rejected. Do not use network filesystems without validating
  their locking and flush behavior.
- A physically incomplete final WAL record may be repaired on recovery. Complete
  framing/checksum/semantic corruption rejects startup before listeners open.
- Disk/append/flush failure poisons writes until recovery. An unsuccessful
  COMMIT may still have reached storage; reconcile outcomes before retrying.
- Row/result/value/workspace/write-set/version/lock exhaustion are independent.
  Preserve the exact SQLSTATE and `--capabilities` output when reporting a failure.
- There is no structured metrics endpoint or configurable listen address. Collect
  stdout/stderr with your supervisor and monitor host memory/disk externally.

Named time zones use `--timezone-dir`, default `/usr/share/zoneinfo` on Linux/macOS.
Windows/native SB0 default to UTC, offsets and POSIX TZ strings unless zone data
is supplied. Case-insensitive zone lookup needs a `ramsql-zones.idx` generated by
`tools/fetch_tzdata.py`; cache slots are fixed and active references prevent eviction.

## Review checklist

Use disposable data first. Exercise your real schema, driver, transaction pattern,
result sizes, cancellation, restart and restore before measuring speed. Run the
same semantics/durability/concurrency against references. Report raw samples and
failures. Full PostgreSQL application/ORM/catalog compatibility and production
deployment acceptance remain [open gates](ROADMAP.md#production-graduation-gates).
