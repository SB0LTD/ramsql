# Module: release pipeline

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/.github/workflows/release.yml`; `tools/{package_release,release_matrix,release_gate,prepare_sig_runtime,fetch_tzdata,build_sb0,create_verifier}.py`; `compatibility/release-gates.json` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon:7`) / RAMSQL `6d677ee` (origin/main) |
| Layer | RAMSQL delivery infrastructure |
| Size | 1,593 lines, 81 KB (`release.yml` 551, `package_release.py` 486) |
| Owner lane | `A4` (workflow, supply chain), `A5` (release notes, license text) |
| Status | beta: v0.3.0-beta.2 published as a prerelease with 29 assets (`gh release view`) |
| Formatting | not applicable (Python, YAML); no linter configured |

## 1. Purpose and scope

Builds eight targets from one immutable revision, tests the exact packaged
bytes, qualifies the Linux image against PostgreSQL, attests the bundle and
publishes a GitHub release. The eight targets are six hosted platforms
(x86_64/aarch64 × Linux, macOS, Windows) and two native AArch64 SB0 images
(SB0X, SB0K). `release_gate.py` blocks stable (non-prerelease) versions until
`compatibility/release-gates.json` records production evidence. Out of scope:
publishing to a package registry, deployment, and installers.

## 2. Public surface

| Item | Where | Contract |
| --- | --- | --- |
| Triggers | `release.yml:3-23` | `v*` tag push; PR to `main`/`master` when workflows, tools, src or build files change; `workflow_dispatch(tag, publish)` |
| `metadata` | `release.yml:33-78` | packaging contract tests, revision and epoch, matrix; runs the gate for stable versions |
| `native-tests`, `postgres-qualification` | `release.yml:82-95` | reuse `ci.yml` and `postgres.yml` (on the exact Linux artifact) |
| `build` | `release.yml:97-174` | ReleaseFast with explicit portable target triples; deterministic archive |
| `native-toolchain` | `release.yml:176-315` | builds the native compiler from `NATIVE_SIG_REVISION`, checks codegen under QEMU, packages SB0X/SB0K |
| `hosted-runtime` | `release.yml:317-390` | verifies the downloaded archive, then runs 11 integration scripts on that binary |
| `benchmark` | `release.yml:392-436` | durable comparison against native PostgreSQL, `--operations 512 --trials 3` |
| `assemble` | `release.yml:438-501` | merges qualification, requires every target, checks checksums, `attest-build-provenance` |
| `publish` | `release.yml:503-551` | creates and pushes the tag if missing, checks it resolves to the revision, `gh release create` |
| `package_release.py` | `:453-478` | subcommands `package`, `assemble`, `verify`, `runtime-report` |
| `release_matrix.py` | `:15-45`, `:60-80`, `:91-93` | Sig tag and hashes, `TARGETS`, `HOSTED_RUNNERS`, `source_version`, `metadata`; CLI flags `--tag`, `--github-output`, `--compiler-host` |
| `release_gate.py` | `:8-23` | exit 1 unless every gate passed, `release_allowed` is true and every PG probe matches |
| `prepare_sig_runtime.py` | `:75-78` | verifies the runtime archive SHA-256 (`:29`) and checks the installed stdlib byte for byte |
| `fetch_tzdata.py` | `:91` | copies zoneinfo from a local image, never pulls; default is a tag, not a digest |
| `build_sb0.py` | `:16-25` | refuses a ZPM URL that does not pin a full commit and SHA-256 |
| `create_verifier.py` | `:13-44` | SCRAM-SHA-256 verifier; password never in argv; `O_EXCL`, mode `0600`, fsync |

## 3. Dependencies and layering

- `package_release.py:23-24` imports `release_matrix` and `qualify_postgres`
  (test tooling). `prepare_sig_runtime.py:18` imports
  `package_release.runtime_provenance`.
- Packaged inputs: `README.md`, `LICENSE`, `compatibility/RELEASE.md`,
  `release-gates.json`, `postgres-report.json`, the `DOCUMENTS` list and
  `tools/create_verifier.py` (`package_release.py:38-42`, `:208-219`).
- External: `SB0LTD/sig` release assets and source (`release.yml:199-204`),
  unpinned apt packages `qemu-system-arm`, `ipxe-qemu` (`:246`) and
  `postgresql` (`:421`), the `gh` CLI, tag-pinned actions.
- macOS and Windows hosted targets are cross-compiled on `ubuntu-latest`
  (`release_matrix.py:78`). They run natively only in `hosted-runtime`.
- `fetch_tzdata.py` is not called by any workflow. Its only caller is the
  differential harness (`test_postgres_compat.py:260`), so it belongs with
  the test kit, not with release tooling. `prepare_sig_runtime.py` and
  `release_matrix.py --compiler-host` are also used by CI (`ci.yml:46`, `:79`).

## 4. Invariants

- **INV-REL-1**: tag is `v` + the source version, and `build.sig.zon` and
  `src/root.sig` versions agree (`release_matrix.py:60-75`). `zpm.pkg.zon` is
  not checked.
- **INV-REL-2**: every job checks out `metadata.outputs.revision`
  (`release.yml:56-62`, for example `:118`).
- **INV-REL-3**: compiler archives are checked against SHA-256 before use
  (`release.yml:131`, `:216`), and the native compiler hash is re-checked
  before packaging (`:283-284`).
- **INV-REL-4**: archives use `SOURCE_DATE_EPOCH` (`release.yml:166`).
  Reproducibility across two independent builders is not tested (unverified).
- **INV-REL-5**: runtime tests run on the verified downloaded binary
  (`release.yml:347-351`) and are bound to its hash (`:379-384`).
- **INV-REL-6**: stable versions must pass `release_gate.py` in `metadata`
  (`:76-78`) and again in `publish` (`:531`).
- **INV-REL-7**: the tag must resolve to the validated revision before
  publication (`release.yml:534-545`). Bundle checksums are re-checked (`:530`).

## 5. Current status and evidence

- `compatibility/release-gates.json:4` is `"release_allowed": false`. Gates:
  postgres_compatibility **failed**, implemented_subset_durability passed,
  authentication partial, transport_security **failed**, capacity_concurrency
  **failed**, backup_restore partial, matched_performance unproven, operations
  **failed**, storage_power_failure unverified. Stable releases are blocked as
  designed. Prereleases do not run the gate.
- The `v0.3.0-beta.2` release body starts with "# RAMSQL 0.3.0-beta.1"
  (`gh release view`), because `compatibility/RELEASE.md:1` is the notes file
  (`release.yml:551`).
- Tags `v0.3.0-alpha.1` through `v0.3.0-beta.2` ship an MIT `LICENSE`. The
  proprietary license landed after beta.2.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| REPO-04 | medium | release | `compatibility/RELEASE.md:1` says beta.1 and is passed as `--notes-file` at `release.yml:551` | Generate notes from the CHANGELOG section or `changes/unreleased/` fragments; `test_release.py` asserts the heading equals the version | A5 |
| REPO-06 | medium | release | `publish` (`release.yml:503-551`) has no `environment:`; `workflow_dispatch` with `publish: true` works from any ref (`:13-23`, `:505`); a `v*` tag can come from any branch | Protected `release` environment with required reviewers; tag ruleset limited to `v*` on `main` | A4 |
| REPO-12 | medium | release | `release_matrix.py:60-68` ignores `zpm.pkg.zon`, whose `.license = "MIT"` (`zpm.pkg.zon:12`) disagrees with `LICENSE:1-7`; `build.sig.zon:4` `.fingerprint = 0x0` | Check all three manifests in `source_version`; fix the license field; set a real fingerprint (whether Sig enforces it is unverified) | A1 |
| REPO-02 | high | license | Packages bundle `LICENSE` (`package_release.py:211`) next to a README that still says "MIT License" (`README.md:343`) | Fix README and `zpm.pkg.zon`; add a NOTICE describing the MIT tags | A5 |
| REPO-20 | low | release | `release.yml:7` lists a nonexistent `master`; `release_gate.py:10` reads only the PG18 report and ignores `postgres16-report.json` | Drop `master`; gate on both reports | A4 |
| REPO-18 | low | supply-chain | apt installs unpinned (`release.yml:246`, `:421`); `fetch_tzdata.py:91` defaults to a tag | Pin package versions or use digest-pinned images | A4 |
| REPO-05 | medium | supply-chain | `attest-build-provenance@v2` (`release.yml:493`) and tag-pinned actions in the `contents: write` job (`:508-509`, `:516-525`) | Pin SHAs | A4 |

## 7. Security considerations

- `publish` holds `contents: write` and `GH_TOKEN`. Checkout keeps its default
  persisted credentials so it can push the tag (`release.yml:516-518`). Every
  action in that job must be SHA-pinned (REPO-05), and the job should sit
  behind a protected environment (REPO-06).
- `id-token: write` and `attestations: write` are scoped to `assemble`
  (`release.yml:442-445`). The native Sig source checkout disables persisted
  credentials (`:204`).
- `create_verifier.py` is shipped to users. It never takes the password in
  argv, restricts input to printable ASCII, and creates the file exclusively
  with mode `0600`. On Windows the POSIX mode does not set an ACL (unverified),
  so OPERATIONS should tell Windows users to restrict the file.
- No SBOM is published. The attestation covers bundle bytes, not dependency
  inventory.

## 8. Tests

- **Existing:** `test_release.py` (261 lines, 41 `self.assert*` call sites: matrix,
  packaging, verification), `test_qualify_postgres.py` (113),
  `test_prepare_sig_runtime.py` (52). All run in CI (`ci.yml:83-87`), and
  `test_release.py` also runs in `metadata` (`release.yml:55`).
- **Missing:**
  - [ ] release-notes heading equals the version (A5)
  - [ ] `zpm.pkg.zon` version and license agree with `build.sig.zon` and LICENSE (A1)
  - [ ] a second-builder reproducibility check that compares archive SHA-256s (A4)
  - [ ] a dry-run publish on a fork or with `--draft` in the merge queue (A4)

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| VO-REL-1 | test | every published asset is listed in `SHA256SUMS.txt` and attested | covered by `assemble` |
| VO-REL-2 | test | no stable tag publishes while any gate is not `passed` | covered (`release_gate.py`) |
| VO-REL-3 | review | every published asset carries the license in effect for its revision | open (REPO-02) |

## 10. Benchmarks

The `benchmark` job (`release.yml:392-436`) runs `benchmark_postgres.py` with
3 trials of 512 operations. `validate_benchmark` (`package_release.py:366`)
binds the result to the image hash. The data does not meet METHODOLOGY §5
(at least 10 trials) and is release evidence only, not a gate. See
[benchmark-tooling.md](benchmark-tooling.md).

## 11. Open questions

- Should a failed SB0X/SB0K native job block the hosted release, or should
  hosted and native ship as separate releases?
- Where do `release-gates.json` and the PG reports live after the layout move?
  `docs/compatibility/` is proposed, which requires edits to
  `release_gate.py:9-10`, `package_release.py:40-42,212-214` and `release.yml:551`.
- Should the earlier MIT tags be documented in NOTICE, and with what wording?
  This needs maintainer and legal input.
