# Typed grouped aggregation: CI evidence

Source: [dbMeter run 40962049999, attempt 1](https://github.com/benor1470/dbMeter/actions/runs/40962049999/attempts/1).
These artifacts are downloaded CI results, not local throughput measurements.
They support a bounded execution improvement; they do not qualify every SQL
workload, production readiness, 100M-row tables or the dbMeter merge gate.

## Controlled comparison

The `paired-sales-subset` job runs both RAMSQL variants on one Linux runner,
with AB / BA / AB order. It loads fresh original 1M data and passes all seven
original checks per variant/pair before timing. The compiler, build arguments
and ZPM manifest hash match. The two server commits differ only by the typed
group opt-in and changelog. Every case uses a fresh JVM, the original 5s ramp,
15s warmup and 60s measured window. One RAMSQL server is active during timing.

- Baseline: `aeca7d4f3e99f372926b1704fa3a7216d12a5f9e`.
- Candidate: `f3ffb4bc0d701631a0309a9c02e7f1b579ad08d5`.
- Shared ZPM: `8495a3ff3308a2656249e4a817b9a0de93034583`.
- dbMeter: `6064636e13f274af1aad0763d288971ea6855b31`.

| Original case / clients | Baseline median QPS | Candidate median QPS | Paired candidate/baseline ratios |
| --- | ---: | ---: | --- |
| Mixed / 1 | 11.76 | 15.40 | 1.578, 1.079, 1.669 |
| Mixed / 8 | 39.48 | 48.26 | 1.358, 1.012, 1.338 |
| Top products / 1 | 6.42 | 117.27 | 18.217, 18.486, 18.296 |
| Top products / 8 | 28.18 | 261.65 | 9.281, 9.343, 9.230 |

The targeted gain is consistent across all three pairs. Mixed throughput
improves in each pair, but varies considerably; the smallest eight-client gain
is only 1.2%. Ratios of mixed medians are approximately 1.31 and 1.22. These
must not replace the retained individual trials or be presented as guaranteed
gains for another machine or workload. All 24 paired case reports and all six
fresh-check XMLs are validated against the historical orchestration/pins.

## Reference comparison remains behind IRDB

A separate `live-sales-subset` job on the same workflow compares the candidate
against the two reference engines. It passes all original subset checks and
retains 18 timed cases. Its numbers are not pooled with the A/B runner.

| Clients | RAMSQL median QPS | PostgreSQL median QPS | IRDB median QPS |
| --- | ---: | ---: | ---: |
| 1 | 15.86 | 3.84 | 176.43 |
| 8 | 53.70 | 6.01 | 249.50 |

RAMSQL still loses this measured mixed workload to IRDB. The original full
5M suite separately retains four SQL-feature failures. The unchanged dbMeter
all-engine advantage gate has not passed and its PR remains unmerged. No README
advantage claim follows from this report.

## Scope of adoption

[ZPM PR 73](https://github.com/SB0LTD/zpm/pull/73) contains the reusable typed
kernel and was merged with disabled library defaults. It passes 3,480 actual
SQL/scalar/canonical assertions on Windows/Linux ReleaseSafe and legacy Linux
Debug, and full hosted SQL CI on Windows/Linux/macOS. Broad legacy CI has the
same four pre-existing SABR/TLS/TTS failures.

The measured composition enables sparse predicates in both variants; this A/B
test therefore does not qualify that independent feature. RAMSQL's final
integration keeps the current main configuration without the sparse experiment.
It also fixes a separate single-scalar UTF-8 validation dependency that blocked
the SB0 native build. [RAMSQL PR 32](https://github.com/SB0LTD/ramsql/pull/32)
tracks the final native/release checks and an additional A/B run with that exact
configuration. Numbers above identify only the measured immutable binaries.

`sha256.json` hashes the retained reports, exact historical orchestration and
this report. No source data, SQL loaders, vendor driver or executable is copied.
