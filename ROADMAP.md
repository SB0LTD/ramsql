# RAMSQL roadmap

The destination is an allocator-free RAM database with PostgreSQL as the default
interface, broad SQL dialect support and strong measured performance. Completion
requires executable evidence, not a version label. No dates are promised here.

| Stage | Work and owner | Exit evidence |
| --- | --- | --- |
| Beta.1: reviewable baseline | RAMSQL: real server, SCRAM/WAL, transparent profiles, onboarding, docs and releases; ZPM: MVCC/typed SQL/compact scans | Every supported artifact executes; independent clients and crash/recovery tests pass; limits and benchmark losses are published |
| Beta operations | ZPM: TLS/channel binding, protocol credentials and privilege/session primitives; RAMSQL: roles policy, lifecycle/health/metrics, safe service configuration | Authenticated encrypted clients on every hosted OS; failed credentials/privileges cannot mutate data; drain/restart/cancel tests |
| Storage and sustained load | ZPM: caller-budgeted page/value storage, version reclamation, row-slot reuse, bounded result/query streaming, checkpoint/change-log primitives; RAMSQL: admission and checkpoint/backup lifecycle | Long-running mixed/churn/large-result tests under memory budgets; snapshot correctness and atomic failure; bounded WAL/recovery time; crash-safe checkpoint/restore |
| PostgreSQL application compatibility | ZPM: constraints/indexes, types/functions/catalogs, planner/execution, isolation/locks; RAMSQL: real app/ORM/utility qualification | Published supported-version matrix; upstream regression coverage and independent application suites; no silent success for unsupported features |
| MySQL dialect | ZPM: native type metadata, grammar/semantics, binary prepared protocol, auth and transaction behavior; RAMSQL: credential/deployment policy | Independent MySQL clients, server differential tests, schema migration and transaction/error compatibility |
| SQL Server dialect | ZPM: broader T-SQL/types/RPC/prepared/bulk/TDS behavior and cancellation; RAMSQL: deployment policy | Independent SQL Server clients, true type/error metadata, cancellation and transaction differential tests |
| Additional common dialects | Define explicit targets (SQLite SQL, Oracle SQL, others), semantics and interfaces before implementation; shared grammar/type features stay in ZPM | Each advertised dialect has an exact feature/client/test matrix; no generic 'all SQL supported' badge |
| Multi-core and efficiency | ZPM: allocator-free partition/index/vector/aggregate/join primitives with ownership rules; RAMSQL: scheduling/admission/backpressure | Equivalent-semantics, durability and concurrency measurements; raw data, tails, memory/import/write costs, failures and reference tuning retained |
| Native SB0 hosted-feature parity | Sig/platform ABI primitives, then ZPM TCP/files/entropy/barriers, then RAMSQL integration | Actual SB0S system integration, TCP, authenticated sessions, durable recovery and faults on the real target; memory-channel QEMU tests alone are insufficient |

## Production graduation gates

Beta testing is not production acceptance. Before a stable production release:

1. Close the machine-checked `compatibility/release-gates.json` requirements with
   linked evidence. Keep `tools/release_gate.py` fail-closed until then.
2. Publish an exact SQL/dialect/client/version compatibility matrix, migration
   policy and tested operational envelope. Meet real application requirements.
3. Remove production-blocking fixed-profile behavior through bounded streaming,
   admission and owned storage—not hidden allocation. Explicit budgets remain
   legitimate, but ordinary large queries must not fail at demo-scale defaults.
4. Provide TLS, roles/authorization and authenticated non-PG adapters before
   network deployment beyond trusted local testing.
5. Qualify sustained concurrency, version pressure, OOM/disk-full/sync failure,
   process loss, physical power loss where applicable, restore and upgrade.
6. Demonstrate performance on identical semantics and deployment boundaries.
   Beat reference workloads without dropping errors, shortening only one engine's
   work or hiding memory/write/recovery costs. No universal speed claim follows
   from the current finite read benchmark wins.

The [usability audit](compatibility/BETA_USABILITY.md) identifies current blockers;
the older [gap-closing plan](compatibility/GAP_CLOSING_PLAN.md) retains design
history. Update evidence/status when a gate is actually closed, not to bypass a
release command. Fixed capacities and production gaps remain explicitly open.
