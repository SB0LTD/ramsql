# Module: `build-graph`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/build.sig`, `build.sig.zon`, `zpm.pkg.zon`, `toolchains/sig-runtime.json` |
| Pinned revision | ZPM `2f060c2` (`build.sig.zon:7-8`) / RAMSQL `6d677ee` |
| Layer | RAMSQL build and packaging |
| Size | 280 + 17 + 18 + 8 lines; 18.9 + 0.6 + 0.5 + 0.4 KB |
| Owner lane | `A3` (test harness: TST-01, TST-02, SRV-20); `A0` for the `.zon` files; `A6` for SRV-05 and SRV-09 |
| Status | beta |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here) |

## 1. Purpose and scope

`build.sig` builds the `ramsql` executable, the RAMSQL contracts, a subset of
ZPM's contract suites, and the benchmark binaries. It generates the `profile`
module from `-D` options. `build.sig.zon` pins ZPM to a commit archive and its
SHA-256. `zpm.pkg.zon` is the package manifest for the ZPM registry.
`toolchains/sig-runtime.json` pins the Sig runtime and build-support sources,
which `tools/prepare_sig_runtime.py` verifies before every CI build
(`ci.yml:78-79`). The build graph does not build native SB0 images
(`tools/build_sb0.py`) or run the Python tests.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `sig build` / `install` (172) | step | builds `ramsql` from `src/main.sig` with the imports at 149-169 |
| `run` (174) | step | runs `ramsql --demo` (31) |
| `test` (188) | step | 35 run steps: `contract-sql` (ZPM `tests/test_sql.sig`), `contract-backup`, `contract-persistence`, `contract-ramsql`, and 31 ZPM suites (265) |
| `test-<name>` (189, 207, 219, 231, 276) | step | runs one contract alone, for example `test-postgres` |
| `benchmark`, `benchmark-review`, `profile-reads` (255, 248, 262) | step | build and run one benchmark binary |
| `-Dclients -Drows -Dtables -Dcolumns -Dresult-rows -Drow-locks -Dpredicates -Dexpr-nodes -Drelations -Dtext-bytes -Dtransaction-bytes -Dtx-writes -Dversions -Dtimezones -Dwork-rows -Dsummary-groups -Dcompact-integral-scans` (109-125) | options | validated at 126-127 and written to `.sig-cache/profile-<wyhash>.sig` (134-140) |
| `-Dlink-libc` (49-50) | option | defaults to `false` for Linux and Windows targets |

## 3. Dependencies and layering

- Resolves ZPM only through `ctx.getDependency("zpm")` (51), which comes from
  `build.sig.zon:6-9`. No sibling paths are used (AGENTS.md).
- Uses ZPM modules by path: `src/core/sql.sig`, `src/db/*.sig`,
  `src/platform/*.sig`, `src/core/resource_*.sig` (55-106).
- Layer rule: no violation. RAMSQL modules are never handed to ZPM tests.

## 4. Invariants

- **INV-BLD-1** ZPM is fetched from a commit archive URL and verified against a
  fixed hash (`build.sig.zon:7-8`). The Sig runtime is verified byte-for-byte
  against `sig-runtime.json` before building.
- **INV-BLD-2** The generated profile is content-addressed: the same `-D`
  values give the same path and the same bytes (135).
- **INV-BLD-3** (unchecked) `clients <= 60` (126) equals `server.sig` `capacity = 60`
  (`server.sig:130`), and `transactions = 62` (129) equals capacity + 2.
  Nothing asserts either equality (SRV-05).
- **INV-BLD-4** `sig build test` succeeds only if every contract binary exits 0
  (37). Each contract counts its own assertions; the build does not check a minimum.
- **INV-BLD-5** No benchmark or test code is linked into `ramsql`. The app
  compile step lists only runtime modules (149-169).

## 5. Current status and evidence

PR CI runs `sig build`, `sig build test`, `sig build benchmark`,
`sig build ramsql-production-bench` and `sig build profile-reads` on Linux,
macOS and Windows (`ci.yml:94-171`). It also runs one wide-profile build
(`ci.yml:173-181`). `tools/test_prepare_sig_runtime.py` tests the runtime pin
check. The two empty `tests/test_db_store.sig` and `tests/test_sql_storage.sig`
files seen in a developer checkout are untracked, not part of `6d677ee`, and
not referenced: line 266 resolves suites through `zpm.modulePath`. REPO-16
tracks them.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| TST-01 | high | testing | the gate runs 32 of the 83 ZPM `tests/test_*.sig` suites at `2f060c2`: the 31 names at 265 plus `test_sql` (180). The 51 others never run. 36 of those test code RAMSQL ships or links (list below). The flags `server.sig:94-97` enables are exercised only by never-run suites | run every relevant suite. Add a gate script that fails when a ZPM suite is neither run nor on an explicit exclusion list | A3 |
| TST-02 | medium | testing | `test` depends on 35 run steps, each with its own compile of the whole SQL engine (178-279). One failed compile skips every run step. On the dev host a parallel compile hit "LLVM ERROR: out of memory" with 0.9 GB free and 46 run steps were reported skipped (dev-host log; not reproduced here) | document a job limit per host memory (`sig build` flag name unverified); in CI, run each `test-<name>` as its own step | A3 |
| SRV-05 | medium | maintainability | `clients <= 60` (126) and `transactions = 62` (129) duplicate `server.sig:130`; ZPM `tcp_server.max_clients = 63` | generate them from one constant and assert at compile time | A6 |
| SRV-09 | medium | testing | no compile step for `src/platform/sb0_app.sig`; only `release.yml:290` builds it | add a compile-only `aarch64-sb0` step | A6 |
| SRV-20 | low | maintainability | `runBinary` returns `error.BufferTooSmall` for a failed test (37), and invalid `-D` values also return it (126-127); `persistence` and `backup` `addModule` calls (192-197) look unused because tests import by path (unverified); the production-bench compile step has no named step (250-254); `versions = rows * 2` is unchecked (121) | distinct errors; drop unused modules; name the step; checked multiply | A3 |
| REPO-12 | medium | completeness | `build.sig.zon:4` fingerprint `0x0`; `.paths` (11-15) packages `benchmarks`, `compatibility`, `tools`; `zpm.pkg.zon:6, 9` layer and dependency | fix the manifests | A1 |
| REPO-02 | high | docs / legal | `zpm.pkg.zon:12` `.license = "MIT"` against the proprietary `LICENSE` | one license statement | A5 |

**Never-run ZPM suites relevant to RAMSQL (36):**

- Runtime and platform (9): `budgeted_region`, `datetime_format`,
  `machine_resources`, `packed_integer`, `packed_integer_100m`, `page_region`,
  `resource_budget`, `rwlock`, `worker_thread`.
- SQL engine (27): `sql_between`, `sql_block_presence`,
  `sql_dimension_group_ids`, `sql_distinct`, `sql_expression_sources`,
  `sql_external`, `sql_factor_algebra`, `sql_factor_cte`, `sql_factor_join`,
  `sql_generated_restrictions`, `sql_grouping_rollup`, `sql_like_escape`,
  `sql_like_scalar_validation`, `sql_minmax`, `sql_nullif_types`,
  `sql_parallel_read`, `sql_projected_filter`, `sql_quantified`,
  `sql_row_membership`, `sql_row_null_candidates`, `sql_row_predicates`,
  `sql_stream_prefix`, `sql_to_char`, `sql_typed_multi_group`,
  `sql_wide_dense`, `sql_windows`, `sql_work_control`.

The four engine flags the server sets (`factorized_aggregates`,
`block_presence_filters`, `typed_grouped_aggregates`, `dimension_group_ids`)
are tested only in `sql_block_presence`, `sql_factor_cte`, `sql_factor_join`,
`sql_typed_multi_group` and `sql_dimension_group_ids`.

The 15 never-run suites outside RAMSQL's scope are `asr_frontend_consumers`,
`asr_mel`, `device_control`, `ephemeral_scene`,
`inference_session_contract`, `ledger_game`, `now_voice_output`,
`qwen_executor`, `safetensors`, `solana_client`, `solana_core`, `solana_cpi`,
`solana_input`, `solana_runtime` and `speech_text`.

The 9 runtime suites need imports the shared list at 273 does not provide
(`page_region`, `budgeted_region`, `resource_budget`, `resource_observation`,
`worker_thread`, `machine_resources`, `packed_integer`, `datetime_format`).
`datetime_format` is ZPM `src/core/sql/datetime_format.sig`. The source path
of `packed_integer` was not in the extract (unverified).

## 7. Security considerations

- Supply chain: ZPM is pinned by archive hash. The Sig runtime is verified by
  `prepare_sig_runtime.py`. The compiler binary is trusted (FORMAL.md §5).
  Action pinning is REPO-05 (A4).
- `build.sig` writes `.sig-cache/profile-*.sig` into the working directory
  during configuration (136-139), a side effect of every build.

## 8. Tests

- **Existing:** CI build and test steps (section 5);
  `tools/test_prepare_sig_runtime.py`; `tools/release_matrix.py:60-65` checks
  that the `build.sig.zon` and `root.sig` versions agree.
- **Missing:**
  - [ ] A3: suite-inventory check (TST-01): `ls zpm/tests/test_*.sig` minus the run list minus the exclusions must be empty.
  - [ ] A3: CI runs `test-<name>` steps separately, or in memory-bounded batches (TST-02).
  - [ ] A3: invalid `-D` values give a named error and exit code (SRV-20).
  - [ ] A6: compile-time assertion for INV-BLD-3 (SRV-05).
  - [ ] A0: `zpm.pkg.zon` version equals `build.sig.zon` (REPO-12).

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| FORMAL.md §2 links | build | the vector, oracle and enumeration links need `tests/contracts/vectors_*.sig` steps in this graph before any FV entry can reach `linked` | todo |
| INV-BLD-3 | static | constants agree, by a `comptime` assert | todo |

## 10. Benchmarks

Build cost matters for TST-02: peak RSS and wall time of `sig build test` by
job count. This is not measured today. METHODOLOGY's S1 instruction-count gate
uses `sig build benchmark`.

## 11. Open questions

- Which ZPM suites are intentionally out of scope for RAMSQL, and where is that
  exclusion list kept?
- Should RAMSQL build ZPM suites at all, or require a green ZPM gate at the
  pinned revision as a merge precondition (with A0)?
