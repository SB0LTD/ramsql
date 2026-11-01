# Numeric function integration candidate

The candidate integrates ZPM's allocator-free Layer 0 implementation of ABS,
CEIL/CEILING, FLOOR, TRUNC and SIGN. Runtime code stays in ZPM. The shared core
contract is registered in RAMSQL; the product dependency pins the verified
immutable archive `29bd16cd17c960822b4ab57157d19ed93f6f559e` (ZPM PR94). No new runtime library is introduced.

The native differential harness adds 17 decimal matrix programs, one lifecycle
program and a typed-parameter/binary-result program, plus a durable restart
case for default/generated expression recovery and sequence state. Matrix expectations were
independently checked with decimal arithmetic and PostgreSQL 16.15; hosted
qualification will still execute native PostgreSQL 16 and 18 as live references.
Lifecycle queries retain their order to check sequence effects, strict NULLs,
CASE/grouping/window semantics, defaults, generated values and statement
atomicity. The fixture records the originating core contract SHA256.

These tests do not close PG.FUNC.NUMERIC: sqrt, power, logs, trigonometry and
many overloads remain missing. Existing numeric coefficient/display capacities
still apply. General unused derived-output pruning and broader PostgreSQL
planner error timing remain separate gaps. No new performance or production
readiness claim is made by this slice.

Qualification observed locally:

- Harness unit tests: 12 pass.
- Native core oracle: 320 checks pass; ReleaseSafe core: 1,266 assertions pass
  on Windows and Linux, independently reviewed.
- RAMSQL ReleaseSafe build resolves and verifies the explicit pinned archive.
- Real PostgreSQL wire: 328 statements pass, including the 320 oracle checks,
  six setup statements and two typed-parameter/binary-result queries.
- Durable SCRAM: the eight-statement restart case passes, including four queries
  after killing/reopening the same WAL. Default NULL calls preserve an uncalled
  sequence; generation expressions remain valid after recovery.
- All eight restart-program statements also match native PostgreSQL 16.15 in a
  rollback-only temporary-object session. This local native probe does not
  restart the shared PostgreSQL instance.

Hosted PostgreSQL 16/18 memory/durable+SCRAM, OS/release and shared contract
qualification remain required before merge. See the input/output hash manifest
in [numeric-functions-evidence.json](../compatibility/numeric-functions-evidence.json).
No release is published by opening this qualification PR.
