# Temporal correctness follow-up — 30 October 2026

This is a correctness checkpoint, not a full SQL-compatibility or performance
release. Shared allocator-free implementation is in ZPM; RAMSQL pins its
immutable commit archive and supplies independent server/native qualification.

## Changes

- SQLF-01: check timestamp day/time/zone construction, DATE conversion,
  calendar reconstruction, day offsets, fractional interval accumulation and
  `ago` negation. Unsupported BIGINT date offsets return 42883.
- SQLF-05: subtracting DATEs with either infinite operand returns 22008.
- Preserve native interval input (22015) versus interval arithmetic (22008)
  error classes and the representable negative i64 interval boundary.
- Preserve native PostgreSQL precision rounding across its documented finite
  timestamp upper boundary; the audit's proposed rejection was incorrect.

ZPM [PR 86](https://github.com/SB0LTD/zpm/pull/86) source is
`33015265a367264d7afe8ea72534512b2fa15d80`, archive SHA-256
`37d5b99740af49c8bf8be7d640c5f4ce75c723da2af68fbfc653622d30abd9de`.
RAMSQL [PR 37](https://github.com/SB0LTD/ramsql/pull/37) integrates it at
`fe774887b5ea17831f603c140dd27017af15e085`.

## Observed qualification

The [evidence manifest](evidence/temporal-boundaries-20261030.json) records
workflow/job identities, artifact/report hashes and finite probe summaries.
Raw workflow reports stay in Actions artifacts and retained local caches.

- [ZPM three-OS database matrix](https://github.com/SB0LTD/zpm/actions/runs/41122510202):
  Linux, macOS and Windows SQL, wire, storage and concurrency contracts pass.
- [RAMSQL CI](https://github.com/SB0LTD/ramsql/actions/runs/41122569233) and
  [release matrix](https://github.com/SB0LTD/ramsql/actions/runs/41122569270):
  every active check passes. The six hosted exact artifacts and PG16/PG18
  memory/durable+SCRAM matrices are qualified; documented policy skips remain
  explicit. No release tag or publishing was requested by this checkpoint.
- Both native SB0 formats pass QEMU. SB0X executes 874 assertions and SB0K 860,
  including the new temporal SQLSTATE/session-survival probe. These are public
  ABI conformance fixtures using memory-only channel/UART transport; they do
  not establish full SB0S distribution integration, hosted TCP, durability,
  SCRAM, or MySQL/TDS support on the native images.
- All 28 added server boundary statements match PG16/PG18 in both storage
  modes. Exact Linux release qualification repeats that matrix.
- The core `sql-types` executable checks every candidate timestamp year against
  wide arithmetic. Local Debug and ReleaseFast runs pass 901,500 assertions;
  the final additional zone-overflow assertion passes ReleaseSafe with 901,501.
  Independent review repeats the ReleaseSafe run and all 28 UTC reference
  fixtures against native PostgreSQL 16.15, exit 0, and approves the code.
- `python tools/test_qualify_postgres.py`: 8/8 pass. Python harness compilation
  and scoped `git diff --check` pass. Red-before-green ReleaseSafe executions
  reproduced independent timestamp and interval-input process panics before
  the corresponding checked-arithmetic changes.

[Broad legacy ZPM CI](https://github.com/SB0LTD/zpm/actions/runs/41122508371)
retains the same four unrelated baseline failures: `test-sabr-loop`,
`test-manifest-sabr`, `test-tls-client`, `test-tts-gpu`; 300 tests pass and 22
skip. This run is not green and is not substituted for the passing dedicated
three-OS database matrix.

## Remaining work

FV-12's formal theorem remains open; generated range tests are not a proof of
all temporal inputs. SQL-standard interval forms/qualifiers and duplicate-field
rules, fractional half-even rounding, zone abbreviation policy, out-of-range
DATE comparison semantics, JSONB concatenation/render/cancellation/stack work,
ambiguous binding and the broader schema/type/function/window backlog remain
tracked in [PLAN.md](../PLAN.md) and [FEATURE_GAPS](../docs/compatibility/FEATURE_GAPS.md).
The earlier 118-program TDD feature checkpoint remains distinct from this
boundary-regression batch. Standard CI/release performance checks ran as part
of qualification; this batch makes no speedup claim.
