# Repository layout: current and target

This page fixes where every kind of file lives in RAMSQL, and how to get there
from today's tree. It merges the docs tree that already exists, the test levels
in [STRATEGY](../verification/STRATEGY.md), the `bench/` and evidence rules in
[METHODOLOGY](../benchmarks/METHODOLOGY.md), the `formal/` tree in
[FORMAL](../verification/FORMAL.md), and the lane and scratch conventions in
[GIT_WORKFLOW](../process/GIT_WORKFLOW.md).

Rules for every move:

- One concern, one home. Code that other scripts import never lives in a
  `test_*` module.
- Raw evidence and scratch output never enter git (GIT_WORKFLOW §7).
- A move updates every path reference in the same PR (see "References to
  update" below), and CI must pass before and after.
- **Nothing is deleted, and no history is rewritten, without maintainer
  approval.** "Delete" below means "candidate for deletion".
- Shared files follow GIT_WORKFLOW §4: `build.sig.zon` and `zpm.pkg.zon` are
  edited only by the integrator, `.github/workflows/*` only by A4, and
  `README.md` and `ROADMAP.md` only by A5.

## 1. Current tree (origin/main `6d677ee`)

| Path | Tracked files | Bytes | Note |
| --- | ---: | ---: | --- |
| `benchmarks/` | 2,530 | 11.8 MB | 57 dated folders plus 18 loose files; about 102 MB more of ignored `*.trace` files in the main checkout |
| `ramsql.com/` | 39 | 6.6 MB | Firebase marketing site |
| `tools/` | 57 | 612 KB | tests, benchmarks, profiling, release and setup scripts in one flat folder |
| `compatibility/` | 14 | 328 KB | plans, research, progress notes, release notes, gate JSON and PG reports |
| `src/` | 13 | 119 KB | the whole server; the engine lives in ZPM |
| `tests/` | 6 | 40 KB | 3 Sig suites, 2 fixtures, 1 Markdown file |
| root docs | 8 | 73 KB | README, CHANGELOG, ROADMAP, CAPACITY, OPERATIONS, SQL_REFERENCE, LICENSE, AGENTS |

Untracked in the main checkout (all git-ignored or targeted by REPO-15/22):
`.sig-cache/` 6.6 GB, including ad hoc scripts, PR bodies, tarballs and logs;
`.worktrees/` 1.5 GB; `sig-out/` 303 MB; `.agents/` 247 MB in 1,043 files.

## 2. Target tree

```
ramsql/
  .github/  workflows/ (ci, postgres, release, nightly)  ISSUE_TEMPLATE/
            PULL_REQUEST_TEMPLATE.md  CODEOWNERS  dependabot.yml
  src/                      server, facade, persistence (A6 decomposes server.sig)
  tests/
    contracts/              L1 Sig suites + fixtures/
    property/               L2
    model/                  L3
    fuzz/                   L4 targets and seed corpora
    integration/            L5 pytest: CLI, lifecycle, auth, persistence, workers, capacity
    clients/                L6 driver suites
    differential/           L7 PG16/18 semantics, COPY, isolation schedules; qualify runner
    simulation/             L8
    crash/                  L9
    soak/                   L10
    native_sb0/             QEMU SB0X/SB0K runtime tests and loader kernel (no STRATEGY level yet)
    tooling/                unit tests of release and bench tooling
    conftest.py
  python/ramsql_testkit/    wire client, server fixtures, binary lookup, Docker reference, tzdata
  bench/                    L11 (C6, C7)
    harness/                drivers and comparisons
    workloads/<suite>/<name>.json
    adapters/irdb/          C# IRDB adapter
    profiling/              profilers and PROFILING.md
  formal/                   README.md  lean/  tla/  vectors/   (FORMAL §1)
  tools/
    ci/                     L0 lint and consistency scripts
    release/                matrix, packaging, gate, native SB0 build
    toolchain/              prepare_sig_runtime.py
    create_verifier.py      shipped in release archives
  toolchains/sig-runtime.json
  examples/
  changes/unreleased/       changelog fragments (GIT_WORKFLOW §2)
  docs/
    architecture/           OVERVIEW, THREAT_MODEL, REPO_LAYOUT, adr/
    modules/                ramsql/, zpm/ module pages
    verification/           STRATEGY, FORMAL
    benchmarks/             METHODOLOGY, results/<yyyy-mm-dd>-<slug>.md + manifest.json
    process/                GIT_WORKFLOW, DEFINITION_OF_DONE
    compatibility/          release-notes source, VALIDATION, BETA_USABILITY,
                            REVIEWER_BETA_2, release-gates.json, postgres{16,18}-report.json
    plans/                  active plans
    archive/                superseded plans, research and migration notes
  README.md CHANGELOG.md ROADMAP.md PLAN.md AGENTS.md LICENSE NOTICE
  SECURITY.md CONTRIBUTING.md CODE_OF_CONDUCT.md
  OPERATIONS.md CAPACITY.md SQL_REFERENCE.md   user guides; packaged; stay at root
  build.sig build.sig.zon zpm.pkg.zon pyproject.toml
  .editorconfig .gitattributes .gitignore
```

Ignored (`.gitignore`): `.scratch/`, `.agents/`, `.worktrees/`, `.sig-cache/`,
`sig-out/`, `.zig-cache/`, `zig-out/`, `__pycache__/`, `node_modules/`,
`*.trace`, and the existing WAL and verifier patterns. Add the explicit
exception `!tests/contracts/fixtures/*.wal`, because today
`tests/fixtures/alpha1.wal` is tracked even though `*.wal` is ignored.

Moves out of the repository: `ramsql.com/` goes to its own repository
([website.md](../modules/ramsql/website.md)); raw benchmark evidence goes to the
evidence store ([benchmark-tooling.md](../modules/ramsql/benchmark-tooling.md)).

## 3. Migration map

| Current path | Target path | Lane | Note |
| --- | --- | --- | --- |
| `tools/test_{cli,server,auth,persistence,workers,reviewer_runtime,streamed_joins,machine_capacity,capacity,large_runtime,production_replay}.py` | `tests/integration/` | A3 | pytest; fixtures move to the testkit |
| `tools/test_{clients,beta}.py` | `tests/clients/` | A3, B7 | |
| `tools/test_postgres_{compat,semantics,copy,isolation}.py`, `test_production_postgres.py`, `qualify_postgres.py` | `tests/differential/` | A3, B7 | update `qualify_postgres.py:143` and `postgres.yml:75` |
| `tools/test_postgres_crash_compat.py` | `tests/crash/` | B9 | |
| `tools/test_sb0.py`, `tools/test_sb0_loader.sig` | `tests/native_sb0/` | A3 | update `release.yml:290-291` |
| `tools/test_{release,qualify_postgres,prepare_sig_runtime,benchmark_review,production_workloads}.py` | `tests/tooling/` | A3 | update `ci.yml:83-91`, `release.yml:55` |
| helpers in `test_server.py`, `test_persistence.py`, `test_postgres_compat.py`; `tools/fetch_tzdata.py` | `python/ramsql_testkit/` | A3 | ends the test↔bench↔release import tangle |
| `tools/requirements-test.txt` | `pyproject.toml` + hashed lock file | A4 | REPO-18 |
| `tests/{api,backup,persistence}.sig`, `tests/fixtures/` | `tests/contracts/` | A3 | `build.sig:199,211,223`; integrator merges registration |
| `tests/MIGRATION-store-api.md` | `docs/archive/` | A5 | |
| `tools/benchmark*.py`, `compare_*.py`, `production_workloads.py` | `bench/harness/` | C6 | |
| `tools/irdb_bench/` | `bench/adapters/irdb/` | C6 | keep `packages.lock.json` |
| `tools/profile_*.py`, `tools/PROFILING.md` | `bench/profiling/` | C7 | |
| `tools/{release_matrix,package_release,release_gate,build_sb0}.py` | `tools/release/` | A4 | update `ci.yml:46`, `release.yml` |
| `tools/prepare_sig_runtime.py` | `tools/toolchain/` | A4 | used by all three workflows |
| `tools/create_verifier.py` | unchanged | — | path is packaged (`package_release.py:219`) |
| `benchmarks/<name>-<date>/REPORT.md` | `docs/benchmarks/results/<date>-<slug>.md` + `manifest.json` | A2 | METHODOLOGY §7 |
| `benchmarks/**` raw logs, CSV, JSONL, `source/` copies | evidence store | A2 | `git rm` only with maintainer approval |
| `benchmarks/README.md` | `docs/benchmarks/results/README.md` (index) | A2 | |
| `compatibility/{RELEASE,VALIDATION,BETA_USABILITY,REVIEWER_BETA_2}.md`, `*.json` | `docs/compatibility/` | A5, A4 | `release_gate.py:9-10`, `package_release.py:40-42,212-214`, `release.yml:551` |
| `compatibility/{MACHINE_CAPACITY_AND_100M_PLAN,EXECUTION_CONTROL,IMPLEMENTATION,REPORTING_SQL_PROGRESS}.md` | `docs/plans/` | A5 | archive when closed |
| `compatibility/{GAP_CLOSING_PLAN,DEEP_INFRASTRUCTURE_RESEARCH,INFRASTRUCTURE_PROGRESS}.md` | `docs/archive/` | A5 | REPO-13: stale headers (`GAP_CLOSING_PLAN.md:15-16`) |
| `ramsql.com/` | `SB0LTD/ramsql.com` (new repo) | A1 | `git subtree split`; REPO-11 |
| none | `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `NOTICE`, `CODEOWNERS`, templates, `.editorconfig`, root `.gitattributes` | A5, A4 | REPO-14 |
| untracked empty `tests/test_db_store.sig`, `tests/test_sql_storage.sig` (main checkout) | delete | A1 | maintainer approval; REPO-16 |
| 34 local and 35 remote branches merged into `main`; `.worktrees/*`; `.agents/`; scratch in `.sig-cache/` | prune, archive or delete | A1 | maintainer approval only; REPO-15, REPO-22 |

## 4. References to update with each move

`build.sig:199,211,223` (test sources) · `build.sig.zon:15` `.paths` (drop
`benchmarks`, `tools`, `compatibility`, `AGENTS.md`; integrator only) ·
`package_release.py:38-42,208-219` (packaged docs) · `release_gate.py:9-10` ·
`qualify_postgres.py:143` · every `python tools/...` line in `ci.yml`,
`postgres.yml` and `release.yml` · `test_release.py:158` (benchmark link
check) · the 15 `python tools/` lines in `AGENTS.md` and `README.md` ·
the 9 `benchmarks/` links in `README.md` · `ramsql.com/public/app.js:30`.

## 5. Findings addressed

| Id | Sev | Summary | Lane |
| --- | --- | --- | --- |
| REPO-10 | medium | evidence dominates the repo; copied sources; no manifests | A2 |
| REPO-11 | medium | website binaries in the database repo | A1 |
| REPO-12 | medium | `build.sig.zon:15` packages evidence and tooling; `fingerprint = 0x0` (`:4`) | A1 |
| REPO-13 | medium | stale and overlapping plan documents in `compatibility/` | A5 |
| REPO-14 | medium | missing SECURITY, CONTRIBUTING, CODEOWNERS, templates, editorconfig | A5 |
| REPO-15 | medium | stale branches and worktrees; `.agents/` and `.worktrees/` not ignored | A1 |
| REPO-16 | low | misplaced test kernel, doc in `tests/`, empty untracked files | A1 |
| REPO-22 | medium | no scratch convention; about 600 ad hoc files in `.sig-cache/` (coordinator count; this audit counted 427 top-level `.py/.md/.log/.tar*/.txt` files) | A1 |

## 6. Order

1. A1: `.gitignore` additions and `.scratch/` convention. No moves yet.
2. A3: create `python/ramsql_testkit/` and pytest config while the scripts stay
   in place, so CI keeps passing.
3. A3, A4: move the tests and tooling in one PR per group, updating workflow
   paths in the same PR.
4. A2: write manifests, upload evidence, convert reports; `git rm` after approval.
5. A1, A5: extract the website; move `compatibility/` docs.
6. Integrator: `build.sig.zon` `.paths` and fingerprint.
