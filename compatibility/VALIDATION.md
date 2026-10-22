# Validation evidence

Development implementation tested with Sig 0.6.3, ZPM archive
`2b70acb4c817c1121f0c0275335bc5509629dc92`, SHA-256
`c96c00db1ce6f4a3581247f2c208ecd4fb4895422c692987627f55acc18afbed`.

- RAMSQL `sig build test -Doptimize=ReleaseSafe`: 29 successful build/run steps,
  over 294,000 executed contract assertions across SQL/index/store/protocol/auth/
  entropy/files/WAL/product replay/backup suites.
- Windows live tests: CLI, raw TCP (3,349 assertions), psycopg/PyMySQL/python-tds,
  SCRAM (7,482 assertions, 128 authenticated unique nonces), crash/backup
  (35 cases, 1,614 assertions and five post-acknowledgement process kills).
- Actual Linux under WSL: native WAL/file/SCRAM/entropy/protocol/replay/backup
  contracts and CLI/raw TCP/crash-backup integration. Windows-cross-built Linux
  server uses the matching Sig library and `-lc` required by Sig's standard I/O.
- ZPM scoped SQL/database contracts: pass; CLI: all 13 test suites pass.
- Native PostgreSQL differential: **12 matches / 37 probes; 25 gaps**.
  This finite suite is a blocker, not a substitute for upstream PostgreSQL
  regressions, application suites and operational qualification.
- Durable performance: 60 matched-workload measurements, full result verification
  and abrupt-stop/WAL-reopen validation. Deployment/storage caveats remain.

ZPM's broader existing CI is **not green**. Run
[39729399386](https://github.com/SB0LTD/zpm/actions/runs/39729399386)
uses Sig 0.5.9 and reports unrelated failures in `test-sabr-loop`,
`test-manifest-sabr`, `test-tls-client` and `test-tts-gpu`; the new SQL/database
contracts pass in that run. Those unchanged packages were not hidden or disabled.
RAMSQL has its own Windows/Linux CI on the latest stable Sig 0.6.3.

No production release was created. `tools/release_gate.py` returns failure while
PostgreSQL parity, transport security, scale, operations and physical storage
qualification remain incomplete.
