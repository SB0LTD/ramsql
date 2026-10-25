# RAMSQL 0.3.0-alpha.4 development candidate

This is an explicitly scoped development prerelease, with six hosted builds
(x86_64/AArch64 Linux, macOS and Windows), plus native SB0X and SB0K images.
Hosted binaries retain synchronous WAL and PostgreSQL SCRAM. SB0 images expose
the PostgreSQL wire subset over trusted memory-only channels/UART; they have no
TCP, durable files or SCRAM. Every exact image must pass its runtime tests.

This candidate follows published alpha.3 and requires fresh qualification on
checksum-verified hosted Sig 0.6.4. Production gates remain blocked.

Changes since alpha.3:

- Hosted row/version/index storage can use caller-owned page regions with runtime
  `--rows` / `--versions` budgets. Recovery checks the actual bound capacity;
  result/workspace/text/schema/session capacities are still separate and bounded.
- Stable bounded top-K selection for eligible column ORDER BY/LIMIT and direct
  cell comparisons. This does not add ordered/secondary indexes or scalable
  streaming/vectorized aggregation.
- A reproducible comparison adapter pinned to current IRDB 1.10.821, with separate
  native-API and PostgreSQL-wire tiers, correctness checks and visible failures.
- Required larger-than-inline-profile durable restart/restore tests in hosted
  CI and exact release-artifact qualification.

Inherited alpha.3 features include shared MVCC/READ COMMITTED storage, row/table
lock waits, savepoints, transactional DDL, typed values/expressions, joins,
aggregates/CTEs, COPY, typed change records and running-query interruption.
Full PostgreSQL behavior and production scale remain open.

This candidate has no published performance-superiority claim. Review actual
comparative results and every blocked case before deciding the next release.
IRDB native-API timings and server/client timings have different call boundaries.
Durable benchmark results remain separate from volatile-memory comparisons.

**WAL compatibility:** framing remains version 1; new commits use payload kind 3
with typed row/catalog changes. Legacy kind-1/2 SQL-text records recover through
the frozen alpha grammar, then subsequent commits use kind 3. A legacy record
following a kind-3 record, or unsupported legacy syntax, fails closed. Older alpha
binaries cannot read kind 3. Preserve the old binary and backup before upgrading;
a general release upgrade/downgrade matrix remains open. Reopen and restore must
have sufficient runtime row/version budgets; insufficient capacity fails closed.

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
