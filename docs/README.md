# RAMSQL documentation

Start with [`PLAN.md`](../PLAN.md) for the current hardening program, then use
this index. User-facing documents (README, SQL reference, capacity, operations)
stay at the repository root until lane A5 moves them under `docs/`.

For the current merged-source status, read the
[consolidation record](../compatibility/PR_CONSOLIDATION_20261030.md),
[temporal correctness follow-up](../compatibility/TEMPORAL_CORRECTNESS_20261030.md) and
[original SQL TDD inventory](https://github.com/SB0LTD/zpm/blob/main/docs/SQL_CONFORMANCE_TDD_STATUS.md).
Module/audit baseline tables retain their stated historical revision.

## Architecture

| Document | What it answers |
| --- | --- |
| [architecture/OVERVIEW.md](architecture/OVERVIEW.md) | How the layers, request path, durability path and memory model fit together |
| [architecture/THREAT_MODEL.md](architecture/THREAT_MODEL.md) | Assets, actors, entry points, open threats and deployment requirements |
| [architecture/REPO_LAYOUT.md](architecture/REPO_LAYOUT.md) | The target repository tree and the migration map from today's layout |

## Modules

[modules/README.md](modules/README.md) lists every module page with status,
owner lane and open findings. New pages start from
[modules/_TEMPLATE.md](modules/_TEMPLATE.md).

| Group | Pages |
| --- | --- |
| RAMSQL product | `modules/ramsql/*.md`: server, CLI, persistence, backup, capacity, time zones, SB0 platform, library facade, benchmark binaries, build graph, tools and integration tests, CI, release pipeline, benchmark tooling, website |
| ZPM SQL engine | `modules/zpm-sql/*.md`: facade, lexer, parser and AST, binder, types and values, numeric, temporal, JSONB, expressions, executor, storage, MVCC, locks, catalog, change log, sequences/DML/sort |
| ZPM database layer | `modules/zpm-db/*.md`: store, durable log, PostgreSQL, pg_types, pg_copy, SCRAM, MySQL, TDS |
| ZPM platform | `modules/zpm-platform/*.md`: TCP and stream server, entropy and clocks, files and pages |

## Compatibility

| Document | What it answers |
| --- | --- |
| [compatibility/FEATURE_GAPS.md](compatibility/FEATURE_GAPS.md) | PostgreSQL features and divergences that block applications, prioritized P0 to P3 with owner lanes |

## Verification

| Document | What it answers |
| --- | --- |
| [verification/STRATEGY.md](verification/STRATEGY.md) | Test levels L0 to L12, oracles, deterministic simulation, coverage and flakiness policy |
| [verification/FORMAL.md](verification/FORMAL.md) | Lean 4 and TLA+ program, theorem catalog FV-01 to FV-22, and how models link to code |

## Benchmarks

| Document | What it answers |
| --- | --- |
| [benchmarks/METHODOLOGY.md](benchmarks/METHODOLOGY.md) | Boundaries, suites S1 to S10, environment capture, statistics, regression gates, evidence storage |

## Process

| Document | What it answers |
| --- | --- |
| [process/GIT_WORKFLOW.md](process/GIT_WORKFLOW.md) | Lanes, branches, worktrees, cross-repo pins, shared files, commits, PR gates |
| [process/DEFINITION_OF_DONE.md](process/DEFINITION_OF_DONE.md) | The checklist every PR and lane must satisfy |

## Conventions

- Finding ids: `NET-` wire protocols, `SRV-` RAMSQL host layer, `SQLF-` SQL
  front end and types, `STO-` storage, MVCC and WAL, `REPO-` repository,
  tooling and CI, `TST-` test gate, `INT-` integration debt.
- Invariant ids: `INV-<MODULE>-n`, defined on the module page.
- Formal ids: `FV-nn`, defined in the theorem catalog.
- Lane ids: `A0`–`A6`, `B1`–`B10`, `C1`–`C9`, defined in `PLAN.md`.
