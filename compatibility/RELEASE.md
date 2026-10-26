# RAMSQL 0.3.0-beta.1

A scoped prerelease for controlled beta testing, benchmarking and review.
PostgreSQL remains the default. The real shared SQL/MVCC/store/WAL/SCRAM paths
are implemented in allocator-free Sig; MySQL/TDS adapters remain experimental
over that subset. This is not production PostgreSQL or complete SQL dialect parity.

## What to try

Download your native archive, verify `SHA256SUMS.txt`, read `CAPACITY.md` and run
`ramsql --capabilities`. Follow the README's disposable SQL example, then use the
packaged `create_verifier.py` for authenticated WAL/restart/backup tests.
Use disposable copies of data and your actual client/schema/workload.

This release adds inspectable capacity/security/protocol budgets, configurable
column/result/lock/parser profiles, a live-tested SCRAM setup helper and complete
reviewer/operator documentation. The custom profile suite verifies 12 columns,
600 returned rows and 200-byte text. Hosted row/version storage is runtime-sized;
it is independent of fixed result/workspace/value/connection bounds.

Since alpha.4, committed summaries, ordered probes and compact/vector/scalar
integer scans materially improve the read path. The retained development IRDB
1.10.821 evidence passes all 21 read comparisons at 10k/100k/1M rows and all ten
wide-profile cases. The original long million-row churn comparison timed out in
IRDB and remains unscored; a shorter trace completed. These are historical native
single-worker measurements, not a claim about every beta binary or workload.
The release bundle separately includes exact-image PostgreSQL qualification and
durable benchmark results, including losses.

## Scope and limitations

- Stock defaults remain 512 stored/returned rows, 8 columns, 128-byte text and
  16 clients. Stored rows can be raised at runtime; other budgets need an
  appropriate compiled profile. All are documented; none is advertised as unlimited.
- Loopback only; no TLS/channel binding or role authorization. MySQL/TDS use
  empty-password trust only under explicit insecure-local mode.
- READ COMMITTED only, one reactor, incomplete catalogs/dialects and fixed
  protocol/query/result budgets; no arbitrary streaming or disk spill.
- No checkpoint/WAL compaction, online backup, replication or general migration
  guarantee. Synchronous flush/process-crash tests do not prove every device's
  physical power-loss behavior.
- Six hosted x86_64/AArch64 Linux/macOS/Windows targets have TCP/WAL/SCRAM.
  Native SB0X/SB0K targets use trusted memory-only process-channel/UART transport;
  no hosted TCP, durable files or SCRAM is claimed for them.

The README, CHANGELOG, ROADMAP, OPERATIONS, CAPACITY, SQL_REFERENCE and
BETA_USABILITY documents ship in each package. Beta publication requires real
exact-artifact execution and the complete platform qualification pipeline;
synthetic packaging fixtures do not qualify binaries. Stable production gates
remain closed and are not relaxed for this prerelease.

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
  complete lifecycle cancellation and documented recovery under full storage failures.
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
