# Module catalog

Every module RAMSQL ships has a page here. Pages follow
[`_TEMPLATE.md`](_TEMPLATE.md). Status values:

- **prototype**: works for demos; no hardening.
- **beta**: tested on the happy path; known gaps.
- **hardened**: findings closed; fuzzed or simulated; invariants tested.
- **verified**: hardened, plus its formal model is linked.

Baseline: RAMSQL `origin/main` 6d677ee, ZPM `2f060c2`. Status reflects the
2026-10-30 audit. Owner lanes refer to [`PLAN.md`](../../PLAN.md).

## RAMSQL product (`ramsql/src`, `ramsql/tools`, `ramsql/.github`)

| Page | Code | Status | Owner lanes | Worst open findings |
| --- | --- | --- | --- | --- |
| [server](ramsql/server.md) | `src/server.sig` | beta | A6, B5, B6 | SRV-02, SRV-03, SRV-06, NET-04 |
| [cli](ramsql/cli.md) | `src/main.sig` | beta | A6 | SRV-14, SRV-10 |
| [persistence](ramsql/persistence.md) | `src/persistence.sig` | beta | B5, B9, C8 | SRV-01, SRV-08 |
| [backup](ramsql/backup.md) | `src/backup.sig` | beta | B5, B9, C8 | SRV-04 |
| [capacity](ramsql/capacity.md) | `src/capacity.sig`, admission ledger | beta | C5, A6 | SRV-15, SRV-17 |
| [timezones](ramsql/timezones.md) | `src/timezones.sig` | beta | A6 | SRV-18 |
| [platform-sb0](ramsql/platform-sb0.md) | `src/platform/sb0_app.sig`, `sb0k.ld` | prototype | A6 | SRV-09 |
| [library-facade](ramsql/library-facade.md) | `src/root.sig` | beta | A6 | none |
| [benchmark-binaries](ramsql/benchmark-binaries.md) | `src/*benchmark*.sig`, `read_profile.sig` | beta | C6 | none |
| [build-graph](ramsql/build-graph.md) | `build.sig`, `build.sig.zon`, `zpm.pkg.zon`, `toolchains/` | beta | A3, A0, A1 | TST-01, TST-02, REPO-12 |
| [tools-integration-tests](ramsql/tools-integration-tests.md) | `tools/test_*.py` | beta | A3 | REPO-08, REPO-19 |
| [ci](ramsql/ci.md) | `.github/workflows/ci.yml`, `postgres.yml` | beta | A4 | REPO-01, REPO-05, REPO-07 |
| [release-pipeline](ramsql/release-pipeline.md) | `release.yml`, `tools/package_release.py` and related | beta | A4, A5 | REPO-04, REPO-06 |
| [benchmark-tooling](ramsql/benchmark-tooling.md) | `tools/benchmark_*.py`, `benchmarks/` | beta | A2, C6 | REPO-10, REPO-09 |
| [website](ramsql/website.md) | `ramsql.com/` | prototype | A1, A5 | REPO-03, REPO-11 |

## ZPM SQL engine (`zpm/src/core/sql*`)

| Page | Code | Status | Owner lanes | Worst open findings |
| --- | --- | --- | --- | --- |
| [facade](zpm-sql/facade.md) | `sql.sig` | beta | B8, B9, C9 | STO-02, STO-13 |
| [lexer](zpm-sql/lexer.md) | `sql/lexer.sig` | beta | B7, B1 | NET-01 (boundary), SQLF-11 |
| [parser-and-ast](zpm-sql/parser-and-ast.md) | `sql/parser.sig`, `sql/ast.sig` | beta | B7, B11 | SQLF-02, SQLF-10 |
| [binder](zpm-sql/binder.md) | `sql/binder.sig`, `sql/deparse.sig` | beta | B7, B11, B4 | SQLF-04, SQLF-10, SQLF-12 |
| [types-and-values](zpm-sql/types-and-values.md) | `sql/types.sig`, `sql/value.sig` | beta | B7 | SQLF-05, SQLF-01 (`date ± int8`) |
| [numeric-and-float](zpm-sql/numeric-and-float.md) | `sql/numeric.sig`, `sql/float_text.sig` | beta | B7, C1 | SQLF-11 |
| [temporal](zpm-sql/temporal.md) | `sql/temporal.sig`, `sql/tzif.sig`, `sql/datetime_format.sig` | beta | B7, C1 | **SQLF-01**, **SQLF-02** |
| [jsonb](zpm-sql/jsonb.md) | `sql/jsonb.sig` | beta | B7 | SQLF-09, SQLF-07, SQLF-06 |
| [expr](zpm-sql/expr.md) | `sql/expr.sig` | beta | B7, C1 | SQLF-06, SQLF-08 (NULLIF assert), SQLF-12 |
| [exec](zpm-sql/exec.md) | `sql/exec.sig`, `sql/sort.sig` | beta | B10, C9 | SQLF-08, STO-12, STO-17 |
| [storage](zpm-sql/storage.md) | `sql/storage.sig` | beta | B8, C9 | STO-08, STO-06 |
| [mvcc](zpm-sql/mvcc.md) | `sql/mvcc.sig` | beta | B8, C2 | **STO-03**, STO-15 |
| [locks](zpm-sql/locks.md) | `sql/locks.sig` | beta | B8, C2 | STO-03, STO-14 |
| [catalog](zpm-sql/catalog.md) | `sql/catalog.sig` | beta | B4, B11, C9 | STO-01 (OID allocation) |
| [change-log](zpm-sql/change-log.md) | `sql/change_log.sig` | beta | B9, C3, C8 | **STO-01**, STO-06, STO-16 |
| [sequence-and-dml](zpm-sql/sequence-and-dml.md) | `sql/sequence.sig`, `sql/dml.sig` | beta | B9, B8 | **STO-04** |
| [alpha-legacy](zpm-sql/alpha-legacy.md) | `sql/alpha.sig` | frozen | B9, C8 | SQLF-15 |

## ZPM database layer (`zpm/src/db`)

| Page | Code | Status | Owner lanes | Worst open findings |
| --- | --- | --- | --- | --- |
| [store](zpm-db/store.md) | `db/store.sig` | beta | B8 | STO-09, STO-02 |
| [durable-log](zpm-db/durable-log.md) | `db/durable_log.sig` | beta | B9, C3, C8 | **STO-05**, STO-07 |
| [postgres](zpm-db/postgres.md) | `db/postgres.sig` | beta | B1, B3, B4, C4 | **NET-01**, NET-02, NET-03 |
| [pg-types](zpm-db/pg-types.md) | `db/pg_types.sig` | beta | B1 | NET-01, NET-19 |
| [pg-copy](zpm-db/pg-copy.md) | `db/pg_copy.sig` | beta | B1 | NET-13, NET-16 |
| [scram](zpm-db/scram.md) | `db/scram.sig` | hardened (no TLS binding) | B5, B3, C4 | NET-05 |
| [mysql](zpm-db/mysql.md) | `db/mysql.sig` | prototype (no auth) | B2 | NET-05, NET-07, NET-12 |
| [tds](zpm-db/tds.md) | `db/tds.sig` | prototype (no auth) | B2 | **NET-01** (`expand`), NET-07, NET-14 |

## ZPM platform (`zpm/src/platform`)

| Page | Code | Status | Owner lanes | Worst open findings |
| --- | --- | --- | --- | --- |
| [tcp-and-stream-server](zpm-platform/tcp-and-stream-server.md) | `platform/tcp_server.sig`, `db/stream_server.sig` | beta | B6, B1 | NET-17, NET-16 |
| [entropy-and-clocks](zpm-platform/entropy-and-clocks.md) | `platform/entropy.sig`, `platform/wall_clock.sig` | hardened | B5 | none |
| [files-and-pages](zpm-platform/files-and-pages.md) | `platform/db_file.sig`, `platform/page_region.sig` | beta | B9, C8 | SRV-08 (no fault seam), STO-18 |

## Keeping this catalog current

- A PR that adds a module adds its page and a row here.
- A PR that closes a finding updates the module page and, when the worst
  finding changes, this table.
- The `docs-consistency` CI job (lane A4) fails if a `.sig` file under `src/`
  in RAMSQL, or under the shipped ZPM paths, has no page listed here.
