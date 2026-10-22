# Validation evidence

Development implementation tested with Sig 0.6.3, ZPM archive
`070a05820dedf59cafdd9e4a3ab6694e69d9f932`, SHA-256
`54e1848aff34c4c0992209c026d0dca83a9c45b73eb52324e879f95c08d29b41`.

- RAMSQL `sig build test -Doptimize=ReleaseSafe`: 33 successful build/run steps,
  over 298,000 executed contract assertions across SQL/index/store/protocol/auth/
  entropy/files/WAL/product replay/backup suites.
- Windows live tests: CLI, raw TCP (3,349 assertions), psycopg/PyMySQL/python-tds,
  SCRAM (7,482 assertions, 128 authenticated unique nonces), crash/backup
  (37 cases, 1,622 assertions and five post-acknowledgement process kills).
- Actual Linux under WSL: native WAL/file/SCRAM/entropy/protocol/replay/backup
  contracts and CLI/raw TCP/crash-backup integration. Windows-cross-built Linux
  server uses the matching Sig library and `-lc` required by Sig's standard I/O.
- ZPM scoped SQL/database contracts: pass; CLI: all 13 test suites pass.
- Native PostgreSQL 16 and 18 differential: **19 matches / 37 probes; 18 gaps**.
  This finite suite is a blocker, not a substitute for upstream PostgreSQL
  regressions, application suites and operational qualification.
- Durable performance: 60 matched-workload measurements, full result verification
  and abrupt-stop/WAL-reopen validation. Both native Linux engines use sibling
  data directories and synchronous commits. PostgreSQL wins multiple workloads;
  no general superiority claim follows. The baseline and updated reports retain
  binary/source/dependency hashes and all individual trials.
- Both updated native images execute SQL, transactions and extended binary
  PostgreSQL under QEMU: SB0K 583 assertions, SB0X 597 assertions, including
  malformed-image rejection and EL0 page permissions. These are memory-only
  transport profiles, not hosted TCP/durability profiles.
- Updated Windows and Linux ReleaseFast server binaries pass all five live
  CLI/server/independent-client/authentication/persistence suites. Integer-width
  tests check signed bounds, PostgreSQL binary/OIDs and MySQL/TDS decoding.
- WAL framing remains version 1, payload kind 2 distinguishes PostgreSQL INTEGER
  semantics. Recovery and backup refuse legacy kind-1 development WAL without
  modifying it. Generic ZPM kind-1 callers retain their original behavior.

ZPM's broader existing CI is **not green**. Run
[39729399386](https://github.com/SB0LTD/zpm/actions/runs/39729399386)
uses Sig 0.5.9 and reports unrelated failures in `test-sabr-loop`,
`test-manifest-sabr`, `test-tls-client` and `test-tts-gpu`; the new SQL/database
contracts pass in that run. Those unchanged packages were not hidden or disabled.
RAMSQL has its own Windows/Linux CI on the latest stable Sig 0.6.3.

No production release was created. `tools/release_gate.py` returns failure while
PostgreSQL parity, transport security, scale, operations and physical storage
qualification remain incomplete.

The client-workspace initialization fix reduces the ReleaseFast Windows image
from 139,028,992 to 1,905,152 bytes. All live ReleaseSafe suites were rerun after
this change. Scratch workspaces are fully overwritten before any read.

[RAMSQL Windows/Linux CI run 39729836950](https://github.com/SB0LTD/ramsql/actions/runs/39729836950)
passed for the initial durable implementation. The follow-up workspace-layout
change is checked separately on the pull request's latest commit.
