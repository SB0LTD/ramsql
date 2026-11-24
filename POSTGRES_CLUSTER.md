# PostgreSQL durable database cluster

`--database-cluster` selects an isolated PostgreSQL-only cluster with one
reactor. Use `--create-data` to initialize it, then reopen it with the same
`--data` base path and without `--create-data`. For example:

```sh
ramsql --database-cluster --data ./example --create-data --insecure-local
ramsql --database-cluster --data ./example --insecure-local
```

The base path names an `example.cluster` journal and an
`example.db-<oid>.wal` per database. Leave the journal and its database WALs
together when stopping or moving the server. The ordinary single-database
WAL path retains its established behavior. Offline backup is currently
single-database only.

Connect to the initial `postgres` database. A simple Query frame containing
`CREATE DATABASE name` creates a database from `template1`. Open a connection
to `template1` to add objects that new databases should inherit. Its storage
is provisioned on the first connection. `CREATE DATABASE name TEMPLATE template0`
uses the pristine UTF-8, C-locale template. An existing database may also be
named explicitly as the source. The source must have no other connections
or outstanding prepared transactions. The destination is published after
its WAL has been flushed and replayed.
`DROP DATABASE [IF EXISTS] name` requires the target to have no connections or
prepared transactions. The initial database is retained. Both commands require
an idle transaction state. The server accepts only the configured
`--auth-user` over verified SCRAM or explicit loopback trust, and that account
owns all databases in this mode. Capacity is four ordinary databases
including `postgres`, plus the writable template. Each open Store has its
own configured table and workspace budget. The protected `template0`
requires no Store or WAL.

Prepared transaction identifiers and `--max-prepared-transactions` apply to
the entire cluster. Resolve each prepared transaction through its owning
database. Opening the cluster validates the recovered identifiers and grant
before admitting connections. Check for outstanding prepared transactions
before lowering the grant. The `pg_prepared_xacts` administrative view is
still pending.

The cluster journal records an OID reservation before file provisioning. An
acknowledged CREATE means the database WAL's cluster/OID identity and the
journal publication record are durable. DROP commits a tombstone before
reclaiming the Store and attempting file removal. A failed or interrupted
provisioning may leave an unreferenced WAL. OID reservations keep these files
separate from subsequent databases. Recovery requires matching identity
records on every published Store before the listener starts.

Database creation supports UTF-8, the C locale, the libc locale provider
and the default tablespace. Prepared database utility statements,
database-level ACLs and forced drops remain open. The `pg_database`
catalog exposes ordinary database identities, ownership and both
template flags. Older clusters with an ordinary database named
`template0` or `template1` retain that database's identity and contents.
Cluster mode accepts one SCRAM verifier and runs the PostgreSQL listener
with one reactor.
