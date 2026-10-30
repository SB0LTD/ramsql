# Module: benchmark and profiling tooling, `benchmarks/` evidence

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/tools/{benchmark*,compare_*,production_workloads,profile_*}.py`, `tools/PROFILING.md`, `tools/irdb_bench/`, `benchmarks/` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon:7`) / RAMSQL `6d677ee` (origin/main) |
| Layer | RAMSQL measurement tooling (Python 3.12, C# adapter) |
| Size | tooling 2,654 lines, 164 KB; evidence 2,530 tracked files, 11.8 MB of blobs (60% of the repo's 19.7 MB) |
| Owner lane | `C6` (harness), `A2` (evidence relocation), `C7` (profiling, regression) |
| Status | prototype: checked results, but harnesses differ per campaign |
| Formatting | not applicable; no linter configured |

## 1. Purpose and scope

These scripts measure RAMSQL against earlier RAMSQL revisions, IRDB 1.10.821
and PostgreSQL 16/18, at the native, wire, durable and concurrent boundaries
defined in [METHODOLOGY](../../benchmarks/METHODOLOGY.md) §1. The profiling
scripts attribute CPU and syscall cost and are never used for speed claims.
`benchmarks/` holds the retained evidence. This page does not cover the Sig
benchmark binaries (`src/benchmark.sig`, `review_benchmark.sig`,
`production_benchmark.sig`, `read_profile.sig`, wired in `build.sig:235-262`)
beyond how the scripts call them.

## 2. Public surface

| Script | LOC | Boundary / suite | Reference | Output | Trials default |
| --- | --- | --- | --- | --- | --- |
| `benchmark.py` | 375 | native + wire, S2/S3 | RAMSQL revisions | `--output` required (`:336`) | 3 (`:333`) |
| `benchmark_postgres.py` | 367 | durable, S4 | PostgreSQL | **default `benchmarks/postgres-comparison.json`** (`:280`), work dir `.sig-cache/` (`:274`) | 3 (`:277`) |
| `benchmark_review.py` | 293 | native + wire, S2/S3 | IRDB, PostgreSQL | `--output` (`:248`) | per case |
| `benchmark_dbmeter.py` | 166 | concurrent JDBC, S7 | external dbMeter (`--dbmeter` required) | `--output` (`:41`) | n/a |
| `benchmark_dbmeter_full_ramsql.py` | 122 | concurrent, S7 at 5M rows | external dbMeter | `--out` (`:32`) | 3 (`:35`) |
| `benchmark_pk_scaling.py` | 136 | wire, S3/S5 | revisions | `--output` (`:109`) | 3 (`:108`) |
| `benchmark_production.py` + `production_workloads.py` | 122 + 168 | native trace replay, S2 | IRDB via `irdb_bench` | `--output` (`:51`) | per trace |
| `benchmark_join_groups.py` | 63 | wire join, S2/S3 | revisions | `--output` (`:24`) | 3 (`:23`) |
| `compare_native.py` / `compare_wire.py` | 115 / 70 | native A/B, wire | revisions / PostgreSQL | `--output` | calibrated |
| `irdb_bench/` (C#) | 214 | native, S2 | IRDB 1.10.821 (NuGet, `packages.lock.json`) | stdout JSON | per run |
| `profile_cpu.py`, `profile_server_cpu.py`, `profile_server_syscalls.py`, `profile_reports.py` | 110, 97, 71, 31 | attribution only (C7) | none | `--output` | n/a |

## 3. Dependencies and layering

- The scripts import test modules: `test_server` (`benchmark.py:26`,
  `benchmark_dbmeter.py:19`, `benchmark_pk_scaling.py:19`) and
  `test_postgres_compat` (`benchmark_postgres.py:41`, `benchmark_review.py:22`,
  `compare_wire.py:13`). They also import each other, for example `cpu_name`
  and `process_memory` from `benchmark.py`, and `cpu_times` from
  `benchmark_pk_scaling.py`.
- dbMeter and its JDBC drivers are external. They are pinned only by commit
  in README prose and in METHODOLOGY S7 (`630fbb1`). `irdb_bench/README.md`
  forbids committing IRDB assemblies; `irdb_bench/.gitignore` excludes `bin/`
  and `obj/`.
- `profile_cpu.py` needs Linux `perf` (`--perf`, `:27`).

## 4. Invariants

- **INV-BENCH-1**: every timed result is checked against an independent model
  or expected digest. A failed check is reported as a failure and never timed.
- **INV-BENCH-2**: a result names exactly one boundary and one binary hash
  (METHODOLOGY §1, §8).
- **INV-BENCH-3**: raw output lands outside the git tree, in `.scratch/<lane>/`
  or the evidence store (GIT_WORKFLOW §7). This is violated by
  `benchmark_postgres.py:274,280`.
- **INV-BENCH-4**: every run writes `environment.json` (METHODOLOGY §4). Today
  nine scripts record some host data (CPU name or platform), but none writes
  that schema; only one tracked evidence file is named `environment.json` or
  `manifest.json`.

## 5. Current status and evidence

`benchmarks/` at origin/main:

- 57 dated directories plus 18 loose top-level files (9 `*.campaign.log`,
  7 JSON baselines, `README.md`, `.gitattributes`).
- File types: 641 `jsonl`, 567 `log`, 499 `json`, 289 `txt`, 266 `csv`,
  107 `xml`, 82 `py`.
- 19 MB on disk in a clean checkout. The main checkout holds 120 MB because
  of about 102 MB of git-ignored `*.trace` files. That explains the "120 MB"
  figure in METHODOLOGY.
- 109 source-like files (`.py`, `.sig`, `.zon`, `.cs`, `.patch`) sit inside
  evidence folders. `production_workloads.py` exists as the identical blob
  `67b1b6b` in 6 folders and in `tools/`. 18 Python scripts under
  `dbmeter-full-five-million-20261028/evidence/` have no file of the same name
  in `tools/`.
- Only 20 of the 57 folders have a `REPORT.md` or `README.md`.
  `benchmarks/README.md` (85 lines) links 3 of them.
- Six nested `.gitignore`/`.gitattributes` files apply per-folder rules.
- Folder names repeat a run index or a before/after state instead of using
  one folder per campaign: `dbmeter-repeat-{1,2,3}-*` (9),
  `dbmeter-{ordered,directory}-million-{1,2,3}-{before,after}` (12).

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| REPO-10 | medium | hygiene | Section 5 counts; `benchmark_postgres.py:280` writes into `benchmarks/` by default; trial defaults of 3 (`benchmark.py:333`, `benchmark_postgres.py:277`) are below METHODOLOGY §5's minimum of 10 | Migrate as below; require `--output`; set the default to 10 interleaved trials | A2, C6 |
| REPO-09 | medium | testing | No workflow runs any `benchmark_*`, `compare_*` or `profile_*` script; only `ci.yml:149-171` builds the Sig benchmark binaries | `bench-smoke` job: run each script on tiny inputs with `--trials 1` | A3, C6 |
| REPO-22 | medium | hygiene | `benchmark_postgres.py:274` uses `.sig-cache/` (the compiler cache) as its work directory | Default to `.scratch/<lane>/` | A1 |
| REPO-03 | high | docs | The site headlines numbers from `production-20261026/REPORT.md:126` without README's caveats; see [website.md](website.md) | Each public claim links one result summary (METHODOLOGY §8) | A5 |

## 7. Security considerations

- `benchmark_dbmeter.py` (docstring) runs dbMeter's setup, which drops a
  schema. Point it only at an isolated PostgreSQL cluster, never at an
  application database.
- IRDB is a third-party commercial package. Its assemblies must not enter git
  or release assets.
- The profilers need `perf` or syscall tracing privileges. Run them on a
  dedicated host, not on shared CI with elevated rights.
- Evidence logs could contain connection strings. Spot checks found no
  secrets, but the whole tree was not audited (unverified).

## 8. Tests

- **Existing:** `test_benchmark_review.py` (69 lines), `test_production_workloads.py`
  (63) and `test_production_replay.py` (41) check validators, the trace format
  and the rejection of corrupt input. They run in CI (`ci.yml:89-91`, `:163`).
- **Missing:**
  - [ ] smoke run of every script (C6)
  - [ ] `environment.json` writer and schema test (C6)
  - [ ] S1 instruction-count gate (C7)
  - [ ] workload files under `bench/workloads/<suite>/` with expected checksums (C6)

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| VO-BENCH-1 | property | a benchmark exits non-zero if any timed result fails its check | partial (per script) |
| VO-BENCH-2 | review | every README and site number links one `docs/benchmarks/results/` summary | open (REPO-03) |

## 10. Benchmarks

This module is the benchmark suite. Map of scripts to suites: S2 (`benchmark.py`,
`benchmark_review.py`, `benchmark_production.py`, `compare_native.py`),
S3 (`benchmark.py`, `compare_wire.py`, `benchmark_pk_scaling.py`), S4
(`benchmark_postgres.py`), S5 (`benchmark_pk_scaling.py`), S7 (`benchmark_dbmeter*.py`).
S1, S6 and S8-S10 have no harness yet.

Evidence migration plan (A2, following METHODOLOGY §7; nothing is deleted
without maintainer approval):

1. Generate `manifest.json` per folder: path, SHA-256, bytes, git blob id.
2. Upload raw files to the evidence store: a release asset per campaign, or a
   separate `ramsql-evidence` repository. This is a maintainer decision.
3. Turn each `REPORT.md` into `docs/benchmarks/results/<yyyy-mm-dd>-<slug>.md`
   (at most about 200 lines) plus the manifest. Folders with no report get a
   one-paragraph "raw only" summary.
4. Replace copied `source/` scripts with the commit that contains them. Move
   still-used experiment scripts into `bench/harness/`.
5. Update the 9 `README.md` lines that link `benchmarks/`, the link check at
   `test_release.py:158`, and the site comment in `ramsql.com/public/app.js:30`.
6. Remove `benchmarks` from `build.sig.zon:15` `.paths` (integrator only),
   then `git rm` with maintainer approval. History stays as it is unless a
   rewrite is approved separately.

## 11. Open questions

- Evidence store: release assets, a separate repository, or object storage?
  Who pays for retention?
- Is dbMeter vendored, pinned by URL and commit, or kept external?
- Which dedicated host runs the nightly S2-S4 wall-clock gates?
