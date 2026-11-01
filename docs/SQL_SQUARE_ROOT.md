# SQRT integration candidate

**Qualified checkpoint, 1 November 2026:** PR48 merged as2756280 after independent
review, CI41311411830 and release41311411851. The release retried one RC2 timing
observation unchanged; the original failure remains recorded. The original exact
bundle was published as CI prerelease run41311411851.2. PR49 then qualified the
automatic non-latest publication policy on CI41315141760/release41315141691 and
merged as0815419. Pending-gate text below is historical candidate-stage evidence;
it does not imply that these qualification gates are still outstanding.

Runtime SQRT implementation remains in allocator-free ZPM Layer 0 SQL, using the
existing Sig integer-root primitive. RAMSQL pins the verified immutable archive
8d8e5c92a5af8a4fa0ce3139f014d082912d90ad (ZPM PR95), SHA256
024869f32dd23597565fc84a357b3acb42f24ce5c9aba9694f1b40b1c2e5877b.
No implicit sibling dependency or new runtime library is introduced.

The native differential runner adds 13 frozen programs (130 SQL statements),
one typed-parameter/binary program (4 statements) and one durable restart case
(7 statements). All remain required in the dynamic PostgreSQL 16/18 catalog.
Memory mode explicitly skips the restart case, bringing its total restart-only
skips to 18. Error statements continue under autocommit so later queries verify
that failed mutations did not change stored values.

Observed locally:

- Matched ReleaseSafe build succeeds through the explicit pinned archive.
- 134 real-wire statements pass, including 123 native-checked probes and four
  binary-parameter statements (positive values, NULL/NaN,2201F and signed zero).
  Negative-zero sign is checked explicitly in the local binary result; a text
  projection also makes the sign visible in hosted native comparisons.
- Seven durable/SCRAM statements pass, including four after reopening the WAL.
  Recovered defaults/generation expressions still work; a negative-input update
  raises 2201F and leaves both rows unchanged.
- All seven recovery-program statements also match native PostgreSQL 16.15 in
  a rollback-only temporary-table session. That local native probe does not
  restart the shared PostgreSQL instance.
- Qualification harness unit tests 12/12 and release-policy tests 13/13 pass.

Configured numeric coefficient/display capacities remain. Wider math and
planner gaps remain open; notably SQRT(-1::numeric) under WHERE FALSE currently
returns zero rows where native PostgreSQL raises 2201F. The core docs record this
explicit divergence; it is not a passing parity test. This slice does not
establish full PostgreSQL compatibility, unlimited capacity, production readiness
or benchmark superiority.

Independent integration review and hosted PG16/18 memory/durable+SCRAM,
native OS and exact-artifact release qualification remain required before merge.
Local source/output hashes are in
[square-root-evidence.json](../compatibility/square-root-evidence.json).

Independent review reproduced the build, 134 wire statements, seven durable
statements, native fixtures/recovery program and unit suites. The manifest
identifies the tested executable and exact local build command. The previous
numeric-only executable independently returns 42883 for sqrt(1::numeric), so
the feature is confirmed to fail before the dependency change. Hosted gates
remain pending.

The pin includes the reviewed ZPM build-graph registration-order correction.
No runtime math, contract or native fixture changed in that follow-up.
The independent rebuild identifies the initial implementation archive in the
manifest. The follow-up pin changes only ZPM build-graph registration order,
documentation and evidence; the parent rebuilt that pin and repeated all wire
and recovery checks. Current executable identity is recorded separately.
