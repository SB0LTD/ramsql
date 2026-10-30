# Module: `benchmark-binaries`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/benchmark.sig`, `src/production_benchmark.sig`, `src/review_benchmark.sig`, `src/read_profile.sig`; build steps `build.sig:234-263` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL (measurement executables) |
| Size | 35 + 134 + 165 + 51 lines; 2.0 + 8.7 + 9.8 + 3.0 KB |
| Owner lane | `C6` (benchmark harness); `A3` for build steps (SRV-20) |
| Status | prototype |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here) |

## 1. Purpose and scope

These are four standalone executables that time the ZPM SQL engine through its
public `Database` API in one process. They use no sockets, WAL or server
reactor. Each one checks its results while timing. None is imported by
`main.sig` or `server.sig`, so **no benchmark code is compiled into the
`ramsql` server binary**. The Python harnesses that drive them, and the stored
evidence, are covered in `benchmark-tooling.md`.

| Binary (output name) | Source | What it times | Correctness oracle |
| --- | --- | --- | --- |
| `ramsql-bench` | `benchmark.sig` | 512-row PK select, `LIMIT 16` scan, point update (32-34) | only a nonzero checksum (19) |
| `ramsql-review-bench` | `review_benchmark.sig` | six cases (PK, filter, top-k, aggregate, group, mixed) at n rows, ordered or `--permuted` | closed-form `model` digest per case (42-73); an independent physical scan before timing (153-154) |
| `ramsql-production-bench` | `production_benchmark.sig` | replay of a binary `RAMTRC01` trace (76), five trials with a power-of-two latency histogram | per-request digest or affected-row count (30-50); check queries before and after each trial |
| `ramsql-read-profile` | `read_profile.sig` | legacy `execute` against `ReadContext.prepare` and `run` phases, 10,000 iterations | exact row check on every iteration (16-20) |

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `ramsql-bench` | CLI | no arguments; one JSON line per case |
| `ramsql-review-bench [n] [iterations] [--case C] [--profile-ready] [--load-only] [--permuted]` | CLI | 64 <= n <= 10,000,000 (117); JSON per trial; failures reported as `"passed":false` |
| `ramsql-production-bench TRACE [min_ms]` | CLI | trace length 36 B to 1 GiB (67); `min_ms` from 1 to 10,000 |
| `ramsql-read-profile` | CLI | no arguments; one JSON line |

## 3. Dependencies and layering

- Imports: ZPM `sql`, `sig`; plus `page_region`, `profile` (review, production,
  read profile) and `db_file` (production trace input).
- Built by: `build.sig` steps `benchmark` (255), `benchmark-review` (248) and
  `profile-reads` (262). The production compile step (250-254) has no run step.
- Layer rule: no violation. They do not import RAMSQL modules.

## 4. Invariants

- **INV-BEN-1** No benchmark reports a timing for a case whose result check
  failed. Every timed call is followed by a check, except `benchmark.sig`
  (a nonzero checksum only).
- **INV-BEN-2** Trace input is untrusted file data. Every field is bounds-checked
  before use (`production_benchmark.sig:17-27, 79-90`), and trailing bytes are
  rejected (98).
- **INV-BEN-3** `--permuted` uses `stride` coprime to n (`review_benchmark.sig:145-148`).
  It is a bijection, so the answers are identical to the ordered layout.

## 5. Current status and evidence

The binaries produced the native evidence cited in README "Measured
performance" (`benchmarks/production-20261026/`, `benchmarks/final-joined-read-20261027/`,
and others). `tools/benchmark.py` runs `ramsql-bench`.
`tools/benchmark_review.py` and `tools/profile_cpu.py` hash
`src/review_benchmark.sig` into their reports, and `tools/benchmark_production.py`
hashes `src/production_benchmark.sig`. CI (`.github/workflows/ci.yml`) runs
three of them on every PR:

- `sig build benchmark` builds and runs `ramsql-bench` (150-155).
- `sig build ramsql-production-bench` builds the trace binary through its
  compile-step name. `tools/test_production_replay.py` then checks replay and
  rejection of corrupt input (156-164).
- `sig build profile-reads` builds and runs `ramsql-read-profile` (166-171).

`ramsql-review-bench` is not run in CI.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-20 | low | maintainability | the `ramsql-production-bench` compile step has no named step (`build.sig:250-254`); CI reaches it only by its output name (`ci.yml:158`). `runBinary` reports a failing benchmark as `error.BufferTooSmall` (`build.sig:37`) | add a named step; return a distinct error | A3 |

## 7. Security considerations

Operator tools only. The trace reader is the only parser of external bytes. It
validates lengths, counts, widths and text bytes (printable ASCII, no `'`) before
building SQL by string formatting (`production_benchmark.sig:90-92`). This is
safe only because of that check. Do not reuse the pattern for real input.

## 8. Tests

- **Existing:** the self-checks in section 1.
  - `tools/test_production_replay.py` runs in CI and covers trace replay and
    corrupt-input rejection.
  - `tools/test_benchmark_review.py` covers the review harness (Python, see
    `benchmark-tooling.md`).
- **Missing:**
  - [ ] C6: give `benchmark.sig` an exact expected result per case. Today the
    CI smoke run passes on any nonzero checksum (INV-BEN-1).
  - [ ] C6: smoke-run `ramsql-review-bench` at n = 64 in CI so a wrong model
    digest fails a PR rather than a benchmark campaign.
  - [ ] B1: fuzz the trace reader with truncated and oversized fields (INV-BEN-2).

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| FV-14 | differential (B10) | grouped and joined aggregates agree with the reference path. `review_benchmark` `model` digests are a small instance of this oracle | todo |
| INV-BEN-3 | property | the permuted layout yields digests identical to the ordered one for every n | todo |

## 10. Benchmarks

These binaries are the S1 and S2 native harness inputs
(`docs/benchmarks/METHODOLOGY.md`). Thresholds from there: S1 instruction count
+3% fails in PR CI; S2 nightly median regression above 5% with a confidence
interval excluding 1.0 fails. The latency histogram in `production_benchmark.sig`
reports power-of-two upper bounds only (52-56), which is too coarse for p99
regression gates.

## 11. Open questions

- The benchmark engine `Config`s differ from the shipped server `Config`. For
  example, `review_benchmark.sig:7` uses one table and does not set
  `factorized_aggregates`, `block_presence_filters`, `typed_grouped_aggregates`
  or `dimension_group_ids`, which `server.sig:94-97` enables. Should S2 build
  from the server's exact `Config` so it measures what ships?
- Should `read_profile` move under lane C7 profiling rather than stay a build step?
