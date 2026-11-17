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
`CREATE DATABASE name` creates a fresh Store. Connect to that name to use it.
`DROP DATABASE [IF EXISTS] name` requires the target to have no connections or
prepared transactions. The initial database is retained. Both commands require
an idle transaction state. The server accepts only the configured
`--auth-user` over verified SCRAM or explicit loopback trust, and that account
owns all databases in this bounded mode. Capacity is four live databases
including `postgres`, with the configured table and workspace budget applied
to each Store independently.

Prepared transactions use a per-database identifier namespace and grant in
this beta. Resolve each prepared transaction through its owning database.

The cluster journal records an OID reservation before file provisioning. An
acknowledged CREATE means the database WAL's cluster/OID identity and the
journal publication record are durable. DROP commits a tombstone before
reclaiming the Store and attempting file removal. A failed or interrupted
provisioning may leave an unreferenced WAL. OID reservations keep these files
separate from subsequent databases. Recovery requires matching identity
records on every published Store before the listener starts.

Cluster SQL currently excludes creation options, prepared database utility
statements, database-level ACL/role DDL, and forced drops. The `pg_database`
catalogue exposes the live registry's `oid`, `datname`, and `datdba` columns.
Cluster mode accepts one SCRAM verifier and runs the PostgreSQL listener
with one reactor.
