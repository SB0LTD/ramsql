# SQRT integration candidate

Runtime SQRT implementation remains in allocator-free ZPM Layer0 SQL, using the
existing Sig integer-root primitive. RAMSQL pins the verified immutable archive
9e8b2c840e4ef4e66b23cde348cf7c2978cfe235 (ZPM PR95), SHA256
5b1352cf963a1f7aee862cc07985b6be59c2831b45cd5982919783b79b61c31d.
No implicit sibling dependency or new runtime library is introduced.

The native differential runner adds13 frozen programs (130 SQL statements),
one typed-parameter/binary program (4 statements) and one durable restart case
(7 statements). All remain required in the dynamic PostgreSQL16/18 catalog.
Memory mode explicitly skips the restart case, bringing its total restart-only
skips to18. Error statements continue under autocommit so later queries verify
that failed mutations did not change stored values.

Observed locally:

- Matched ReleaseSafe build succeeds through the explicit pinned archive.
- 134 real-wire statements pass, including123 native-checked probes and four
  binary-parameter statements (positive values, NULL/NaN,2201F and signed zero).
  Negative-zero sign is checked explicitly in the local binary result; a text
  projection also makes the sign visible in hosted native comparisons.
- Seven durable/SCRAM statements pass, including four after reopening the WAL.
  Recovered defaults/generation expressions still work; a negative-input update
  raises2201F and leaves both rows unchanged.
- All seven recovery-program statements also match native PostgreSQL16.15 in
  a rollback-only temporary-table session. That local native probe does not
  restart the shared PostgreSQL instance.
- Qualification harness unit tests12/12 and release-policy tests13/13 pass.

Configured numeric coefficient/display capacities remain. Wider math and
planner gaps remain open; notably SQRT(-1::numeric) under WHERE FALSE currently
returns zero rows where native PostgreSQL raises2201F. The core docs record this
explicit divergence; it is not a passing parity test. This slice does not
establish full PostgreSQL compatibility, unlimited capacity, production readiness
or benchmark superiority.

Independent integration review and hosted PG16/18 memory/durable+SCRAM,
native OS and exact-artifact release qualification remain required before merge.
Local source/output hashes are in
[square-root-evidence.json](../compatibility/square-root-evidence.json).

Independent review reproduced the build,134 wire statements, seven durable
statements, native fixtures/recovery program and unit suites. The manifest
identifies the tested executable and exact local build command. The previous
numeric-only executable independently returns42883 for sqrt(1::numeric), so
the feature is confirmed to fail before the dependency change. Hosted gates
remain pending.
