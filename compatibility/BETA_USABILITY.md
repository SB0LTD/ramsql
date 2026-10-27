# Beta usability and implementation audit

## Reviewer follow-up (beta.2 candidate)

The beta.1 review reproduced important limitations. Runtime workspace/results,
unequal table banks, early safe inner-join predicates, larger parser/PG buffers,
recoverable oversized SQL, admission errors and runtime PK index masks are now
implemented. Native socket reactors are experimental; SQL execution is still
serialized. See [the detailed follow-up](REVIEWER_BETA_2.md) for tests, remaining
limits, benchmark boundaries and publication status. The sections below retain
the original beta.1 audit; their old numeric defaults are historical. Use the
current [capacity inventory](../CAPACITY.md) and exact binary `--capabilities`.

## Original beta.1 source audit

Scope: RAMSQL product sources, pinned ZPM SQL/store/protocol/platform modules,
CLI/build profiles, independent-client tests, persistence/backup, package and
publication gates. This is an engineering audit of a bounded implementation,
not a proof that every SQL feature or production failure mode has been covered.

## Real implementations and explicit fixtures

| Path | Finding | Evidence / caveat |
| --- | --- | --- |
| SQL execution | Real lexer/parser/binder/executor over typed row versions and PK indexes in ZPM; no delegated database process | SQL contracts and independent PG differential/isolation tests; subset only |
| Shared sessions | All listeners use one store, statement snapshots and real locks/savepoints | Independent clients, lock waits, deadlocks, transactions; one reactor, READ COMMITTED only |
| Persistence | Real typed WAL, native synchronous file flush, exclusive writer, validated recovery | Kill/reopen, malformed/corrupt WAL, short-write/sync failure tests; physical power loss not proven |
| Authentication | Real PostgreSQL SCRAM proofs and entropy-backed server nonces | Independent libpq login; helper-generated credential smoke; no roles or TLS |
| MySQL/TDS | Real framed protocols invoking the shared store | PyMySQL/python-tds; empty-password local trust, shared SQL subset, partial native metadata |
| Catalog/server metadata | Finite compatibility views and reported PG version/settings | These are deliberate compatibility metadata, not a real PostgreSQL installation or complete catalog; do not infer version parity |
| TDS ATTENTION | Protocol acknowledgement when dispatched, not general asynchronous execution cancellation | Do not advertise active-query cancellation parity; PG has independent cancellation tests |
| SB0 images | Real native SB0X/SB0K output and loader/UART/channel execution | QEMU tests exercise SQL/protocol/transactions; not hosted TCP, WAL, SCRAM or full SB0S distribution integration |
| Release unit tests | Explicit synthetic executable headers and patched fixture environments | Validate rejection/packaging only; never accepted as runtime evidence for production artifacts |
| Published release qualification | Executes exact artifacts; checks source/binary SHA and OS/architecture | Six hosted runners, native QEMU, independent clients, PG16/18 and durable benchmark gates |
| Performance reports | Real result-consuming runs and independent models | Raw failures/timeouts retained; finite native single-worker wins, not universal superiority |

No production query-result mock or alternate database backend was found in the
audited product path. This statement does not prohibit mocks in unit tests or
imply exhaustive correctness. Protocol compatibility constants, trust-mode login
and empty-database selection do not implement roles or a multi-database catalog.

## Beta blockers addressed

- Startup previously required a verifier without an included onboarding path:
  `create_verifier.py` now creates one without argv secrets/overwrites; libpq
  validates the resulting credential against the actual server.
- Binary limits were scattered through sources: `--capabilities` now exposes
  the active profile and prospective runtime storage backing before startup.
- Server columns/results/parser/lock budgets were partly hard-coded: explicit
  build options now reach the shared SQL Config. Real client tests cover values
  beyond the former 8-column, 512-result and 128-byte text defaults.
- README mixed old alpha status and superseded performance findings: the new
  overview links versioned evidence and distinguishes ambitions from support.
- Packages lacked a complete reviewer/operator guide: changelog, roadmap,
  capacity, SQL and operations documents accompany the executable.

## Production blockers deliberately still open

The [capacity inventory](../CAPACITY.md) is part of the beta contract: default
512-row results, fixed packet/prepared statement sizes, inline bounded values,
schema/workspace/write-set/lock/version limits and 60 maximum compiled clients
can reject normal production workloads. Runtime `--rows` does not erase them.
This beta therefore cannot honestly certify 'no limits prevent production use'.

Other blockers are TLS/channel binding, role authorization, authenticated
non-PG protocols, full SQL/dialect/catalog/application parity, checkpoint/WAL
compaction, online backup/upgrade policy, multi-worker scheduling, operational
telemetry and physical power-loss qualification. The stable release gate remains
closed. [ROADMAP.md](../ROADMAP.md) assigns layers and executable exit criteria.

Beta acceptance means the documented supported paths execute and the package is
reviewable, with real tests and visible failures. It does not relabel the open
production requirements as complete. Reviewers should use disposable data and
record their exact profile, client, schema, durability and workload assumptions.
