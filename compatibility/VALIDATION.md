# Validation evidence and reproducible qualification

Review: 25 October 2026. Source and published releases are different artifacts:
the latest published version is `v0.3.0-alpha.3`; current development is
`0.3.0-alpha.4`. Historical sections below retain their original candidate
context. Publication requires the checks for the exact new revision.

Current infrastructure work and scoped validation:
[streaming aggregation progress](INFRASTRUCTURE_PROGRESS.md).

## Established transactional-engine baseline

RAMSQL PR 5 and ZPM PR 21 are merged. ZPM is pinned at
`dba76e14c98ad62cd74cf72d3916e4005b616578`, SHA-256
`6bb8f4b6b6999c08da2e1b92d22a5a3561d96fc2197e75d312656ea10e11add4`.

- [Release matrix 40174568211](https://github.com/SB0LTD/ramsql/actions/runs/40174568211)
  passed all eight targets, exact hosted runtime tests, native SB0X/SB0K QEMU,
  the exact-artifact durable benchmark and bundle verification. Hosted compiler:
  Sig 0.6.3; native compiler source: merged Sig 0.6.4 revision `90a898dc`.
- [Main CI 40175410496](https://github.com/SB0LTD/ramsql/actions/runs/40175410496)
  passed Linux x86_64/AArch64, Windows x86_64 and macOS AArch64.
- Checked-in PG18/PG16 reports: 37/37 probes match. Separate gap-closing runs
  matched 141 semantic cases in memory / 151 durable, 44 isolation schedules,
  11 COPY cases and two durable crash cases, on both references. The default
  5,000-row COPY capacity divergence is recorded separately and is not parity.
- Reassessment downloaded and verified all eight packages from the release run;
  their source tree equals merged main. The exact Windows binary passed CLI,
  TCP (3,513 assertions), independent psycopg/PyMySQL/python-tds, SCRAM (8,506
  assertions and 128 distinct authenticated nonces), and persistence (58 cases,
  1,757 assertions, five post-acknowledgement kills). The exact Linux binary
  matched 37/37 on native PostgreSQL 16.15 in memory and durable+SCRAM modes,
  with both sessions using UTC. Reassessment reports are retained locally
  under `.sig-cache/reassessment-20261025`.
- Native QEMU: SB0X 599 and SB0K 585 assertions. This is public ABI-fixture,
  memory-only transport evidence; native TCP, file durability and SCRAM remain absent.

## Required CI and release qualification

Hosted toolchains now come from one checksum catalog in `tools/release_matrix.py`,
pinning Sig 0.6.4 and its matched library. Native compiler source remains pinned
to the full reviewed Sig 0.6.4 commit. Qualification of the new pins is determined
by the current PR/main checks, not inferred from the earlier 0.6.3 baseline.

`.github/workflows/postgres.yml` runs the shared `tools/qualify_postgres.py` runner:

| Matrix | Required evidence per cell |
| --- | --- |
| PG16 / PG18 × memory | 37 probes, 141 semantic cases, 44 isolation schedules, 11 COPY cases; ten restart-only semantic cases explicitly excluded. |
| PG16 / PG18 × durable+SCRAM | 37 probes, 151 semantic cases, 44 isolation schedules, 11 COPY cases, two crash cases; no semantic skips. |

Reference images are immutable digests recorded in the runner. Every suite must
use the requested image, binary hash, persistence/authentication mode and complete
case catalog. A successful summary cannot hide failed raw cases, missing/duplicate
cases, new skips or unknown divergences. Raw JSON and command logs are uploaded
even on failure. The single documented COPY capacity divergence remains visible;
it does not establish production capacity.

CI builds ReleaseSafe on the checked-out revision. Releases test the exact
checksum-verified Linux ReleaseFast package, not a separately rebuilt executable.
Assembly validates all four qualification entries against the release source and
Linux binary SHA-256, and publishes full reports in `postgres-qualification.json`,
covered by checksums and attestations. Existing hosted/client/auth/crash and native
QEMU checks remain required.

Reproduce one cell (Docker image download is explicit):

```sh
python tools/qualify_postgres.py run --reference 18 --mode durable-scram \
  --binary sig-out/bin/ramsql --revision "$(git rev-parse HEAD)" \
  --output .sig-cache/qualification-local-pg18 --pull-reference
```

Use a fresh output directory for each run. `--mode memory` runs the other mode;
`--reference 16` selects the other pinned reference. No reference image is pulled
without `--pull-reference`. Only test-owned containers/directories are modified.

Run `python tools/test_qualify_postgres.py` and `python tools/test_release.py` for
evidence-completeness, identity, matrix and deterministic packaging contracts.

## Remaining qualification gaps

Finite suites are not PostgreSQL conformance, real-application qualification or
proof of production scaling. TLS, roles, authenticated MySQL/TDS, compact storage,
resumable query scheduling, checkpoint/online-backup lifecycle, operational
qualification and physical power-loss testing remain open. Production gates stay
blocked. ZPM's unrelated legacy Sig 0.5.9 CI failures remain separately reported;
green database-platform checks do not erase those failures.

Historical performance JSONs in `benchmarks/` describe earlier binaries. New
release benchmarks use their exact Linux image and retain all trials, durability
settings, correctness/recovery checks and provenance. The latest assessed
transactional-engine CI benchmark still favors PostgreSQL for durable
INSERT/UPDATE; no universal speed or efficiency claim is supported.

## Running statement controls (25 October 2026)

`tools/test_server.py` now requires CPU-bound statement timeout and cancellation,
fragmented CancelRequest handling with a full SQL client pool, rejection of an
invalid key, preservation of a new ordinary startup during CPU work, work-budget
errors, data rollback and failed-transaction recovery. Run the same command as
the lifecycle suite; these cases are part of every hosted CI/release-artifact run.
ZPM store tests also cancel after private versions have been created and use a
fake live clock to make deadline/rollback checks deterministic. See
[execution controls](EXECUTION_CONTROL.md) for limits.

## Alpha.4 runtime capacity and IRDB comparison candidate

ZPM commit `20dea8b148d68cb5894c32dc9cb2d48f09fba00b` adds optional external
row/version/index buffers, independent query reference storage, checked binding
and copying, actual-capacity recovery validation and bounded stable top-K.
Linux SQL/database contracts pass; focused Windows contracts cover 100,000 rows,
copy/index masks, invalid layouts and replay beyond the compiled inline profile.
Database-platform CI passed on Linux, macOS and Windows. The unrelated legacy
Sig 0.5.9 root CI still fails the same four historical targets.

The hosted candidate exposes `--rows` / `--versions`. Required
`tools/test_capacity.py` inserts 2,048 rows, verifies ordering, reopens durable
state, rejects too-small recovery capacity without modifying the source WAL,
and verifies an offline restore. It is included in hosted CI and exact-artifact
runtime evidence. Windows execution passed locally; full RAMSQL candidate matrix
qualification remains required before release.

The [IRDB comparison pack](../benchmarks/irdb-review-20261025/REPORT.md) uses
IRDB 1.10.821 with its public id clustered index and records one million stored
rows in the candidate. The aggregate support/correctness flag is false because
large grouped queries hit WorkspaceCapacity. Native-API and server/client
boundaries are separate. No production, all-workload or universal efficiency
claim follows from the selected small-data wins.
