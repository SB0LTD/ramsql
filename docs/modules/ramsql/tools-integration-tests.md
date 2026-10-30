# Module: `tools/test_*.py` (integration, differential and tooling tests)

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/tools/test_*.py`, `tools/qualify_postgres.py`, `tools/requirements-test.txt` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon:7`) / RAMSQL `6d677ee` (origin/main) |
| Layer | RAMSQL test tooling (Python 3.12, outside the Sig layers) |
| Size | 25 `test_*.py` files plus `qualify_postgres.py`: 6,596 lines, 388 KB |
| Owner lane | `A3` (harness), `B7` (clients, differential), `B9` (crash) |
| Status | beta: real assertions, no test framework |
| Formatting | not applicable (Python); no formatter or linter is configured |

## 1. Purpose and scope

These scripts run the built `ramsql` binary as a separate process and check it
through the CLI, raw PostgreSQL wire packets, independent client drivers, and
a differential comparison with PostgreSQL 16/18 running in Docker. Five of them
are unit tests for the release and benchmark tooling. They do not test engine
internals; the Sig contract suites run by `sig build test` do that.

## 2. Public surface

| Entry point | Kind | Contract |
| --- | --- | --- |
| `python tools/test_<name>.py [--binary PATH]` | script | exit code 0 only if every check passed |
| `qualify_postgres.py run --reference 16\|18 --mode memory\|durable-scram` | script | runs 5 differential suites; writes `<suite>.json` and `.log` (`qualify_postgres.py:127-151`) |
| `qualify_postgres.py merge` | script | joins the qualification reports to one release image SHA-256 |
| `test_server.PG`, `Server`, `check`, `packet`, `startup_packet`, `expect_error`, `expect_closed`, `rows` | de facto library | raw PG wire client and server fixture; imported by 12 other scripts |
| `test_postgres_compat.native_postgres`, `running_ramsql`, `execute`, `differences`, `candidate_mode` | de facto library | Docker reference lifecycle and result comparison; imported by 13 other scripts |
| `test_persistence.DurableServer` | de facto library | durable server fixture; imported by `profile_server_cpu.py` |

## 3. Dependencies and layering

- External: `psycopg[binary]==3.3.6`, `PyMySQL==1.2.3`, `python-tds==1.17.1`
  (`requirements-test.txt:1-3`, version pins without hashes). Docker is used by
  `test_postgres_compat.py` and `test_postgres_crash_compat.py`. Reference
  images are pinned by digest in `qualify_postgres.py:20-21`, but the default
  for a direct run is the tag `postgres:18-alpine` (`test_postgres_compat.py:495`).
- psycopg is used by 13 test files, PyMySQL and pytds only by `test_clients.py`.
- Import tangle: benchmarks import tests (`benchmark.py:26`,
  `benchmark_pk_scaling.py:19`, `benchmark_postgres.py:41`), release code
  imports test code (`package_release.py:24` imports `qualify_postgres`;
  `test_release.py:16` imports `test_qualify_postgres.synthetic_bundle`), and
  tests import benchmarks (`test_workers.py:15`, `test_large_runtime.py:14`).
  Shared code should not live in modules named `test_*`.

## 4. Invariants

- **INV-ITEST-1**: every script exits non-zero when any check fails. Today 11
  scripts use bare `assert`, which `python -O` removes. CI does not use `-O`.
- **INV-ITEST-2**: every server under test listens on an OS-assigned port
  (`--port 0`, `test_server.py:62`) and is killed on exit (`test_server.py:93-104`).
- **INV-ITEST-3**: Docker cleanup removes only the container that the test
  started itself (`test_postgres_compat.py:222-223`).
- **INV-ITEST-4**: a missing dependency fails the run and is never skipped. I
  found no skip paths in the client, auth, beta, persistence, capacity or
  differential scripts.
- **INV-ITEST-5**: the same scripts run against the CI build and against the
  exact release artifact (`release.yml:356-378`).

## 5. Current status and evidence

- CI runs 12 binary-driving scripts on 4 OSes (`ci.yml:114-180`; `test_beta.py`
  runs twice, once on a wide build) and the
  5 tooling unit tests (`ci.yml:83-91`). `postgres.yml:75-77` runs the 5
  differential suites for PG16/18 × memory/durable-scram.
- `release.yml:356-378` re-runs 11 of them against each packaged hosted binary.
  `release.yml:291` runs `test_sb0.py` under QEMU.
- Not run by any workflow: `test_large_runtime.py` and `test_production_postgres.py`.
- PRs merged on 2026-10-30 had green checks (`gh pr list`), but nothing
  requires that (REPO-01, see [ci.md](ci.md)).

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| REPO-08 | medium | testing | No framework: only 5 of 25 files use `unittest` and none use pytest; 3 `check()` and 3 `require()` helpers (`test_server.py:28`, `test_auth.py:33`, `test_persistence.py:30`, `benchmark.py:40`, `benchmark_postgres.py:108`, `qualify_postgres.py:35`); binary lookup copied into 17 test files and 2 benchmark scripts (for example `test_clients.py:39`, `test_server.py:617`); 2 server fixtures (`test_server.py:48`, `test_persistence.py:37`) | Extract `ramsql_testkit` (wire client, fixtures, binary lookup, Docker reference) and move tests under pytest with markers and a minimum assertion count | A3 |
| REPO-09 | medium | testing | `test_large_runtime.py` and `test_production_postgres.py` appear in no workflow | Mark them `nightly` and run them on a schedule | A3 |
| REPO-19 | low | testing | Fixed sleeps used as synchronization: `test_server.py:457`, `:503`, `:513`; `test_workers.py:35`, `:38`, `:83`; `test_postgres_copy.py:215` | Wait on server readiness or state probes with explicit timeouts (STRATEGY §5) | A3 |
| REPO-16 | low | hygiene | Test kernel in `tools/test_sb0_loader.sig`; documentation in `tests/MIGRATION-store-api.md`; two empty untracked `tests/test_*.sig` files in the main checkout | Move per [REPO_LAYOUT](../../architecture/REPO_LAYOUT.md); delete only with maintainer approval | A1 |

## 7. Security considerations

- The tests start servers with `--insecure-local` (11 occurrences) on loopback
  only. That is acceptable for tests, but the flag must never appear in a
  packaged service configuration.
- `test_auth.py:65-72` builds SCRAM verifiers from in-process test passwords.
  No real credentials are read.
- Docker access is root-equivalent on Linux hosts. Differential runs belong on
  CI runners or disposable hosts.
- `test_postgres_compat.py` may run a non-pinned tag locally, so local results
  are not qualification evidence unless the image digest is recorded
  (`test_postgres_compat.py:524` records it).

## 8. Tests

Inventory (origin/main):

| Script | LOC | Level | Framework | Binary lookup | Run by |
| --- | --- | --- | --- | --- | --- |
| test_cli | 165 | L5 | bare `assert` | `--binary`, default `sig-out/bin/ramsql[.exe]` (`:12-14`) | ci, release |
| test_server | 641 | L5 | `check()` | default (`:617`) | ci, release |
| test_persistence | 350 | L5, L9 | `check()` | default (`:177`) | ci, release |
| test_auth | 261 | L5 | `check()` | default (`:241`) | ci, release |
| test_workers | 252 | L5 | `assert` | default (`:201`) | ci, release |
| test_clients | 133 | L6 | `assert` | default (`:39`) | ci, release |
| test_reviewer_runtime | 123 | L5 | `assert` | default (`:32`) | ci, release |
| test_streamed_joins | 92 | L5 | `assert` | default (`:54`) | ci, release |
| test_machine_capacity | 85 | L5 | `assert` | default (`:15`) | ci, release |
| test_large_runtime | 76 | L5 | `assert` | default (`:23`) | none |
| test_capacity | 53 | L5 | `assert` | default (`:17`) | ci, release |
| test_beta | 49 | L6 | `assert` | default (`:16`); CI also passes a wide build | ci, release |
| test_production_replay | 41 | L5 | `assert` | `--binary` required (`:14`) | ci |
| test_postgres_semantics | 1296 | L7 | raise | default (`:1232`) | postgres.yml |
| test_postgres_compat | 553 | L7 | raise | default (`:494`) | postgres.yml |
| test_postgres_isolation | 528 | L7, L8 | raise | default (`:466`) | postgres.yml |
| test_postgres_copy | 457 | L7 | raise | default (`:359`) | postgres.yml |
| test_postgres_crash_compat | 257 | L9 | raise | default (`:201`) | postgres.yml |
| test_production_postgres | 43 | L7 | `assert` | none (PostgreSQL only) | none |
| qualify_postgres | 192 | runner | `require()` | `--binary` required (`:171`) | postgres.yml |
| test_sb0 | 391 | native | raise | `--image` | release (QEMU) |
| test_release, test_qualify_postgres, test_benchmark_review, test_production_workloads, test_prepare_sig_runtime | 558 | tooling | `unittest` | none | ci (4 OSes) |

Target layout (lane A3; full map in [REPO_LAYOUT](../../architecture/REPO_LAYOUT.md)):
`tests/integration/` (L5), `tests/clients/` (L6), `tests/differential/` (L7 and
the isolation schedules), `tests/crash/` (L9), `tests/tooling/` (unit tests of
release and benchmark tooling), shared code in `python/ramsql_testkit/`, and one
`pyproject.toml` with pytest markers `docker`, `nightly` and `release_artifact`.

Missing:

- [ ] pytest runner with JUnit output and a per-file minimum assertion count (A3)
- [ ] one binary lookup in the testkit: `RAMSQL_BINARY` env var, then `--binary`, then `sig-out/bin` (A3)
- [ ] a check that stops shared code from living in `test_*` modules (A3)
- [ ] client matrix beyond psycopg/PyMySQL/pytds: asyncpg, JDBC, pgx, node-postgres (B7)
- [ ] seeded randomized schedules with the seed printed (B8)

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| VO-ITEST-1 | property | every assertion counter is greater than or equal to its recorded minimum | not started |
| VO-ITEST-2 | differential | PG16 and PG18 results match, or the divergence is documented and tested | running in `postgres.yml` |
| VO-ITEST-3 | property | no fixed sleep is used as a synchronization point | open (REPO-19) |

## 10. Benchmarks

None. `test_production_replay.py` validates the benchmark trace format, and the
benchmark scripts reuse `test_server` fixtures; see
[benchmark-tooling.md](benchmark-tooling.md).

## 11. Open questions

- Should the testkit live in `python/ramsql_testkit/` (shared with `bench/`) or
  in `tests/testkit/`? An A3 decision, coordinated with C6.
- Should the 5 tooling unit tests keep running on 4 OSes, or only on Linux?
- Is the bare-`assert` style kept under pytest (with assertion rewriting), or
  replaced by `check()` everywhere?
