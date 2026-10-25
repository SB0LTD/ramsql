# RAMSQL 0.3.0-alpha.3 development candidate

This is an explicitly scoped development prerelease, with six hosted builds
(x86_64/AArch64 Linux, macOS and Windows), plus native SB0X and SB0K images.
Hosted binaries retain synchronous WAL and PostgreSQL SCRAM. SB0 images expose
the PostgreSQL wire subset over trusted memory-only channels/UART; they have no
TCP, durable files or SCRAM. Every exact image must pass its runtime tests.

This candidate is not the published alpha.2 binary. It includes the merged
transactional SQL engine and requires qualification on checksum-verified hosted
Sig 0.6.4. Production gates remain blocked.

Changes since the published alpha.2:

- Shared MVCC storage, READ COMMITTED, row/table lock waits, deadlock handling,
  savepoints and transactional DDL, without per-connection database copies.
- Typed expressions and numeric/temporal/JSON types, joins, aggregates, CTEs,
  DISTINCT/pagination, RETURNING, upserts, defaults, identity/sequences, a catalog
  subset and streamed COPY. Documented capacity/feature limits still apply.
- Kind-3 typed change records with flush-before-publication and frozen-grammar
  replay of legacy alpha SQL logs.
- Required PG16/PG18 × memory/durable+SCRAM qualification of the exact Linux
  release image. Every probe, semantic case, isolation schedule, COPY case and
  durable crash case is checked, with raw reports and logs retained. Missing,
  duplicated or failing cases block release assembly. The documented default
  COPY capacity divergence remains explicit and is not counted as parity.
- Checksummed, attested release artifacts and matched durable benchmarks of the
  exact Linux release image against an isolated native PostgreSQL process.

The local Linux experiment in `benchmarks/durable-boundary.json` checks 60
measurements (five workloads, 1/4 clients, three trials, two engines), all results,
and abrupt-stop recovery. The published alpha.1 comparison is retained in
`benchmarks/durable-boundary-baseline.json`. Results include host/storage
variability; PostgreSQL still wins multiple durable-write workloads. Reopening
and validating the same 2.17 MB WAL took 698 ms for alpha.1 and 27 ms for alpha.2
on this host. These are small SQL-subset experiments, not a production
PostgreSQL replacement or a general superiority claim. The release additionally
ships `benchmark-postgres.json`, measured from its exact Linux artifact in CI.

**WAL compatibility:** this version uses framing version 1, payload kind 2 for
32-bit INTEGER semantics. It refuses earlier development kind-1 logs without
modifying them. Preserve the old binary and backup; no automatic migration is
provided. This alpha retains alpha.1's SQL interpretation and kind-2 framing;
the release tests reopen alpha.1 WALs with the updated binary. Restore requires
compatible schema semantics and adequate capacity.

## Production release requirements

The accepted target is a more efficient drop-in replacement for native PostgreSQL,
including the normal PostgreSQL application and operational behavior. The current
code is development work and does not meet that target. No production tag or
production-ready claim is authorized by the evidence in this repository.

Run `python tools/test_postgres_compat.py` against the actual native reference.
The checked-in finite reports match all 37 probes against PostgreSQL 18 and 16.
This small suite does not replace PostgreSQL's upstream regression suites,
independent application/ORM/tooling suites, concurrency verification, long-running
load, and deployment fault tests. A successful subset test is not PostgreSQL parity.

Run `python tools/release_gate.py` before preparing any production release. It
returns nonzero while any requirement in `release-gates.json` remains failed,
partial, unverified or unproven. Review evidence before changing a gate; do not
change statuses merely to make a release command pass.

## Known blocking work

The [detailed gap-closing plan](GAP_CLOSING_PLAN.md) breaks these requirements
into individual review items, implementation tasks and closing evidence.

- Remaining PostgreSQL types/functions, complete catalogs/schema constraints,
  window functions, broader COPY/dialect behavior and real application/tool compatibility.
- Compact page/value storage, streamed results, resumable execution, additional
  isolation/lock modes and scalable worker scheduling. Fixed profiles remain a limit.
- TLS/channel binding, roles/authorization, authenticated MySQL/TDS listeners.
- Checkpoints/WAL compaction, online backup, upgrade/migration policy, monitoring,
  operational cancellation and documented recovery under full storage failures.
- Matched efficiency evidence on equivalent semantics, operating systems and
  storage; complete supported-platform CI and real hardware power-loss testing.

## Current durability contract

An acknowledged committed batch has been fully written and synchronously flushed
through native Windows/Linux/macOS file APIs before in-memory publication. Recovery
validates checksums, framing, record versions and typed changes before listening.
Only a physically incomplete final record is repairable. Complete corruption and
semantic failure abort startup. A storage error rejects subsequent writes until
restart/recovery; an unsuccessful COMMIT can still have reached durable storage,
as with other database connection/storage failures, so clients must reconcile it.

New records contain versioned typed changes, not SQL or native struct images.
Legacy SQL records use only the frozen alpha grammar. Older alpha binaries cannot
read kind 3; a general release upgrade/downgrade matrix is still missing.
Restore needs compatible format/schema semantics and enough
capacity for historical peak states and transactions. The WAL is never truncated
for compaction in this version. A whole-WAL offline backup preserves its history.

Process-kill and injected short-write/sync-failure tests are distinct from physical
power-loss testing. OS flush guarantees also depend on the filesystem and device.

References: [PostgreSQL regression tests](https://www.postgresql.org/docs/current/regress.html),
[transaction isolation](https://www.postgresql.org/docs/current/transaction-iso.html),
and [WAL reliability](https://www.postgresql.org/docs/current/wal-reliability.html).
