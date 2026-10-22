# Production release requirements

The accepted target is a more efficient drop-in replacement for native PostgreSQL,
including the normal PostgreSQL application and operational behavior. The current
code is development work and does not meet that target. No production tag or
production-ready claim is authorized by the evidence in this repository.

Run `python tools/test_postgres_compat.py` against the actual native reference.
The checked-in finite report currently finds 25 gaps in 37 probes. Passing this
small suite would still require PostgreSQL's upstream regression suites,
independent application/ORM/tooling suites, concurrency verification, long-running
load, and deployment fault tests. A successful subset test is not PostgreSQL parity.

Run `python tools/release_gate.py` before preparing any production release. It
returns nonzero while any requirement in `release-gates.json` remains failed,
partial, unverified or unproven. Review evidence before changing a gate; do not
change statuses merely to make a release command pass.

## Known blocking work

- PostgreSQL types and ranges, OIDs, NULL ordering, expressions, joins, aggregates,
  CTEs, catalogs, COPY, savepoints, schema features and extension/tooling behavior.
- Default READ COMMITTED, row-level write concurrency/MVCC, scalable storage and
  streamed results. Dense configurable fixed profiles remain a capacity limit.
- TLS/channel binding, roles/authorization, authenticated MySQL/TDS listeners.
- Checkpoints/WAL compaction, online backup, upgrade/migration policy, monitoring,
  operational cancellation and documented recovery under full storage failures.
- Matched efficiency evidence on equivalent semantics, operating systems and
  storage; complete supported-platform CI and real hardware power-loss testing.

## Current durability contract

An acknowledged committed batch has been fully written and synchronously flushed
through native Windows/Linux file APIs before in-memory publication. Recovery
validates checksums, framing, record versions and SQL replay before listening.
Only a physically incomplete final record is repairable. Complete corruption and
semantic failure abort startup. A storage error rejects subsequent writes until
restart/recovery; an unsuccessful COMMIT can still have reached durable storage,
as with other database connection/storage failures, so clients must reconcile it.

The WAL is versioned but contains original SQL rather than native struct images.
Do not change parser/type semantics across upgrades without preserving old replay
semantics or adding a format migration. Restore needs the same version and enough
capacity for historical peak states and transactions. The WAL is never truncated
for compaction in this version. A whole-WAL offline backup preserves its history.

Process-kill and injected short-write/sync-failure tests are distinct from physical
power-loss testing. OS flush guarantees also depend on the filesystem and device.

References: [PostgreSQL regression tests](https://www.postgresql.org/docs/current/regress.html),
[transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html),
and [WAL reliability](https://www.postgresql.org/docs/current/wal-reliability.html).
