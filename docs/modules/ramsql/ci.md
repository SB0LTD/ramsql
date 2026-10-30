# Module: CI workflows (`ci.yml`, `postgres.yml`)

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/.github/workflows/ci.yml` (181 lines), `postgres.yml` (84 lines) |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon:7`) / RAMSQL `6d677ee` (origin/main) |
| Layer | RAMSQL delivery infrastructure (GitHub Actions) |
| Size | 265 lines, 12 KB |
| Owner lane | `A4` (the only lane that edits `.github/workflows/*`, GIT_WORKFLOW §4) |
| Status | runs on every PR to `main`; **not required for merge** |
| Formatting | not applicable; no `actionlint` or `zizmor` run |

## 1. Purpose and scope

`ci.yml` builds RAMSQL with a checksum-verified Sig release on four hosted
runners, runs the Sig contract suites and the Python integration tests, and
calls `postgres.yml` for PostgreSQL 16/18 differential qualification.
`release.yml` reuses both files (see [release-pipeline.md](release-pipeline.md)).
CI does not publish anything and has no write permissions.

## 2. Public surface

| Item | Where | Contract |
| --- | --- | --- |
| Triggers | `ci.yml:3-13` | push to `main`, `pull_request` to `main`, `workflow_dispatch`, `workflow_call(skip_differential)` |
| Job `postgres` | `ci.yml:19-21` | calls `postgres.yml` unless `skip_differential` is true |
| Job `native` ("Native Sig (os)") | `ci.yml:23-181` | matrix `ubuntu-latest`, `ubuntu-24.04-arm`, `macos-latest`, `windows-latest`; 20 min timeout |
| `postgres.yml` trigger | `postgres.yml:3-12` | `workflow_call` only; inputs `artifact_name` (exact release artifact) and `revision` |
| Job `differential` ("PG<ref> <mode>") | `postgres.yml:18-84` | matrix PG `16`, `18` × `memory`, `durable-scram`; `ubuntu-latest`; 45 min timeout; uploads `qualification/**` for 30 days |

Steps of `native`, in order: install Sig (`:44-76`), prepare the runtime
(`:78-79`), 5 tooling unit tests (`:81-92`), build ReleaseSafe (`:94-99`),
`sig build test` ReleaseSafe (`:101-106`), `--demo` (`:108-111`), 12 integration
scripts (`:113-147`), benchmark smoke ReleaseFast (`:149-154`), production
replay (`:156-164`), `profile-reads` build (`:166-171`), and a wide-profile
libpq test (`:173-181`).

## 3. Dependencies and layering

- Sig compiler: asset and SHA-256 come from `tools/release_matrix.py
  --compiler-host` and are downloaded from `SB0LTD/sig` releases
  (`ci.yml:46-55`, `postgres.yml:53-58`).
- Runtime and stdlib: `tools/prepare_sig_runtime.py`, pinned by
  `toolchains/sig-runtime.json`.
- Python clients: `tools/requirements-test.txt`, version pins without hashes.
- Actions: `actions/checkout@v4`, `setup-python@v5`, `upload-artifact@v4`,
  `download-artifact@v4`. All are tag pins.
- PostgreSQL reference: Docker images pinned by digest and pulled with
  `--pull-reference` (`postgres.yml:75-77`, `qualify_postgres.py:20-21`).
- Callers: `release.yml:82-87` (`ci.yml` with `skip_differential: true`) and
  `release.yml:89-95` (`postgres.yml` on the exact Linux artifact).

## 4. Invariants

- **INV-CI-1**: the token is read-only (`ci.yml:15-16`, `postgres.yml:14-15`).
  PRs use `pull_request`, not `pull_request_target`, so fork PRs get no secrets.
- **INV-CI-2**: the compiler archive is checked against its SHA-256 before it
  runs (`ci.yml:52-55`, `postgres.yml:58`).
- **INV-CI-3**: multi-command PowerShell steps check `$LASTEXITCODE` after each
  native command (for example `ci.yml:83-92`). Single-command steps rely on the
  runner's PowerShell wrapper, which exits with `$LASTEXITCODE`. That wrapper
  behavior is GitHub's and was not re-verified here.
- **INV-CI-4**: differential runs check out the revision under test
  (`postgres.yml:28-33`), and the qualification report records the binary SHA-256.

## 5. Current status and evidence

- `gh run list` (2026-10-30) shows CI on every PR and on pushes to `main`. PRs
  #31-#35 merged with 32 passing and 2 skipped checks. PRs #26-#28 (license and
  site changes) ran only CI's 8 checks because `release.yml`'s path filter did
  not match.
- `gh api repos/SB0LTD/ramsql/branches/main/protection` returned "Branch not
  protected", and the rulesets list is `[]`. Green CI is a convention, not a
  gate.
- All integration steps run in one job. The first failure stops later steps,
  so a single failing test hides the results of the rest.
- Builds are ReleaseSafe only. No Debug build, no `sig fmt`, no schedule, no
  cache and no `concurrency` group in CI.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| REPO-01 | high | ci | `main` has no protection or ruleset (gh api, 2026-10-30); public repo | Ruleset on `main` and `int/*`: the required checks below, no force-push, no deletion | A4 |
| REPO-05 | medium | supply-chain | Tag pins in `ci.yml:39-40` and `postgres.yml:31,34,39,78`; no `dependabot.yml` | Pin full SHAs; add Dependabot for `github-actions` and `pip` | A4 |
| REPO-07 | medium | ci | `release.yml:6-12` runs on PRs and calls `ci.yml` again (`release.yml:82-87`), so the 4-OS suite runs twice per PR; no `concurrency` in `ci.yml`; no cache | Move the release matrix to the merge queue or a label; add `concurrency: cancel-in-progress`; cache the Sig archive keyed by its SHA-256 | A4 |
| REPO-17 | low | ci | No schedule, Debug build, fuzz, link check or license check; `-latest` runner labels; CI lacks the `macos-15-intel` and `windows-11-arm` runners that release ships (`release_matrix.py:41-45`) | Add the nightly and PR jobs below; pin runner images | A4 |
| REPO-18 | low | supply-chain | `requirements-test.txt:1-3` has no hashes; the direct-run default image is a tag (`test_postgres_compat.py:495`) | `pip install --require-hashes`; use the digest by default | A4 |
| REPO-21 | medium | ci | No workflow runs `sig fmt`. The format audit reported that `sig fmt --check` fails for all 16 RAMSQL `.sig` files on main and for 24 of 39 shipped ZPM SQL, db and platform files at 2f060c2 (not re-run for this page) | One formatting commit per repo, then a required `lint` job | A4 |
| TST-03 | medium | ci | ZPM's `native-sig` check fails on merged ZPM PRs (#76, #80), so a red check on the dependency RAMSQL pins has become normal. RAMSQL CI does not consume ZPM's check status for the pinned commit. | Fix or quarantine `native-sig` with an owner (A0.4); make RAMSQL's contract manifest (A3.4) the authority for the pinned ZPM commit. | A0, A4 |

## 7. Security considerations

- A moved action tag runs arbitrary code inside a job. In CI the token is
  read-only, but the same actions upload the artifacts that `release.yml`
  assembles and attests, so a compromised action could poison a release.
  SHA pins (REPO-05) close this.
- `pip` installs from PyPI without hashes (REPO-18). Docker images are pinned
  by digest.
- Denial of service: per-job timeouts of 20 and 45 minutes bound runaway tests.

## 8. Tests

- **Existing:** see the step list in section 2 and the inventory in
  [tools-integration-tests.md](tools-integration-tests.md).
- **Missing:** see REPO-17, REPO-18 and REPO-21 and the proposed gates below.

Proposed gates. "Required" means a required status check in the `main` /
`int/*` ruleset.

| Job | Trigger | Proves | Required |
| --- | --- | --- | --- |
| `lint` | PR, push | `sig fmt --check`, `sig ast-check`, forbidden-pattern lint (STRATEGY L0), ruff, `actionlint` + `zizmor` (SHA pins, permissions) | yes |
| `tooling-unit` | PR, push | release, packaging and qualification contracts (`tests/tooling/`), Linux only | yes |
| `contracts (os × Debug, ReleaseSafe)` | PR, push | `sig build test` on all 6 hosted release runners | yes |
| `integration (os)` | PR, push | pytest L5/L6 against the build, JUnit report, minimum assertion counts | yes |
| `differential (PG16/18 × mode)` | PR, push | L7 parity with digest-pinned PostgreSQL | yes |
| `docs-consistency` | PR | link check; versions agree across `build.sig.zon`, `src/root.sig`, `zpm.pkg.zon`, the RELEASE heading and CHANGELOG; license string matches LICENSE | yes |
| `supply-chain` | PR | dependency review, `pip --require-hashes` | yes |
| `bench-smoke` | PR | every bench and profile script runs on tiny inputs; S1 instruction counts (METHODOLOGY §6) | no, until stable |
| `fuzz-smoke` | PR (5 min) | L4 targets build and run without a trap | no, until stable |
| `nightly` | schedule | `test_large_runtime`, `test_production_postgres`, long fuzz, S2-S4 on a dedicated host | no |
| `release-matrix` | merge queue, tags | exact-artifact execution ([release-pipeline.md](release-pipeline.md)) | merge queue only |

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| VO-CI-1 | review | the ruleset's required checks equal the "yes" rows above | not started |
| VO-CI-2 | lint | every `uses:` is a 40-hex SHA and every job declares least-privilege permissions | not started |
| VO-CI-3 | manifest | every contract suite in the pinned ZPM is run or excluded with a reason (OVERVIEW §6) | not started (A3) |

## 10. Benchmarks

`ci.yml:149-154` builds and runs `ramsql-bench` (ReleaseFast) as a smoke test
with no threshold. `ci.yml:156-164` validates replay of production traces. No
PR performance gate exists yet. METHODOLOGY §6 specifies instruction-count
gates for PRs and wall-clock gates only on a dedicated host.

## 11. Open questions

- Merge queue, or a required label, for running `release-matrix` before merge?
- Who owns a dedicated benchmark host for nightly wall-clock gates (C7)?
- Should the 5 tooling unit tests stay on all 4 OSes?
