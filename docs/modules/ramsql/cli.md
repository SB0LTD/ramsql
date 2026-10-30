# Module: `cli`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/main.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL |
| Size | 256 lines, 13.5 KB |
| Owner lane | `A6` (testability); `B5` for data-path policy |
| Status | beta |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here). For example, two statements per line at 142 and 147 |

## 1. Purpose and scope

This is the process entry point of the `ramsql` executable. It parses argv
into `server.Options`, picks one mode (`--serve`, the default, plus `--demo`,
`--stdin`, `--help`, `--version`, `--capabilities`, `--machine-capacity`), and
maps errors to exit codes. `--demo` and `--stdin` run a small embedded
`ram.DefaultDatabase` (4 tables, 128 rows) with TSV output. The module does not
validate capacity limits itself; `server.sig` does that. It is not a SQL shell
for a running server.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `main(init)` (250) | fn | runs `run`; exit code 2 for `UnknownArgument`, `InvalidCharacter`, `Overflow`, `InvalidServerOptions`, 1 for other errors (253) |
| CLI flags (169-228) | interface | one mode flag at most; each option takes one value; `--memory` and `--data` are mutually exclusive |
| `--stdin` protocol (84-125) | interface | one statement per line, at most 4096 bytes; `OK n` or TSV rows on stdout; `error: Name` on stderr; exit 1 if any statement failed |

Internal helpers: `budgets` (140) parses `n,n,...` into exactly
`server.table_capacity` values. `text` (28) escapes tab, CR, LF and backslash.

## 3. Dependencies and layering

- Imports: `std`, `sig`, `ramsql` (`root.sig`), `server.sig`.
- Imported by: nothing; this is the build root of the `ramsql` executable
  (`build.sig:144`).
- Layer rule: no violation. The demo database comes through the public facade.

## 4. Invariants

- **INV-CLI-1** Every argv either ends in an error with a nonzero exit code or
  yields `Options` that are then validated by `server.run`, `describe`,
  `describeMachine` or `backup`.
- **INV-CLI-2** No flag accepts a password or other secret. Only a verifier
  path (`--auth-verifier-file`) and a user name (`--auth-user`, 1-64 bytes) are
  taken (197-211).
- **INV-CLI-3** `--memory` and `--data` are rejected together in either order
  (174, 192).
- **INV-CLI-4** At most one mode flag (170). Server options with `--demo`,
  `--stdin`, `--help` or `--version` are rejected (231).
- **INV-CLI-5** `--backup-to` runs only in serve mode and never with `--memory` (233).
- **INV-CLI-6** A `--stdin` line longer than 4096 bytes is discarded whole and
  reported as `StatementTooLong`. A truncated prefix is never executed (107-117).
- **INV-CLI-7** Path and user arguments are copied into fixed buffers (158-162)
  and rejected when empty or too long. The argv scratch is 8192 bytes (154).

## 5. Current status and evidence

`tools/test_cli.py` runs the built binary. It checks `--version` and `--help`
output; `--capabilities` JSON fields; that `--capabilities --data` does not create
a WAL; exit code 2 for unknown and invalid options (port 65536, protocol
`oracle`, max-clients 0 and 61, idle-ms 0, and more); `--demo` output; and exact
`--stdin` output including error continuation, UTF-8, TSV escaping and oversized
lines. `tools/test_auth.py` checks that startup fails closed without a verifier.
There is no Sig test of the parser.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-01 | high | robustness | serve mode needs no `--data`; the default `"ramsql.wal"` (`server.sig:48`) is created in cwd if missing (`server.sig:925`) | require `--data` plus an explicit create flag for a new WAL | B5 |
| SRV-02 | high | testing | parsing is inline in `run` (153-230) and writes straight to stdout, so it cannot be tested in-process | extract a pure `parseArgs(argv) !Command` | A6 |
| SRV-10 | medium | maintainability | the flag-to-enum `if/else` chain is one line of about 600 chars (182); the help text is one 1,301-char line (239) | comptime flag table; multi-line help literal | A6 |
| SRV-14 | low | robustness | repeated flags silently override earlier values (212-227); `--backup-to` silently ignores `--insecure-local`, `--port` and the auth flags (232-236) | reject duplicates; reject flags the chosen mode ignores | A6 |
| SRV-19 | low | docs | help says `--workers` is "SQL serialized" (239), but parallel reads exist (`server.sig:272-314`) | update help, OPERATIONS.md and README | A5 |

## 7. Security considerations

- Trust boundary: argv and stdin come from the operator, not the network.
- Secrets: none on argv, by design (INV-CLI-2). The verifier is read by `server.sig`.
- `report` prints only `@errorName` to stderr (17-21), so no paths or data
  values leak.
- Integer options use `fmt.parseInt` with the target width (`u16` ports,
  `u32` idle, `u64` budgets, `usize` rows). Overflow is an error (exit 2).
  Range checks are in `server.sig` (FV-09 applies there).
- The demo database (10-11) is static, compiled into the production binary,
  and not charged to the byte ledger (see SRV-17).

## 8. Tests

- **Existing:** `tools/test_cli.py` (section 5); `tools/test_auth.py` startup
  failures.
- **Missing:**
  - [ ] A6: table-driven Sig contract for `parseArgs` once extracted (INV-CLI-1 to INV-CLI-5).
  - [ ] A6: fuzz `parseArgs` and `budgets` with random argv (no crash; exit class is stable).
  - [ ] A3: `--table-rows` with too few, too many and empty entries (`budgets`, 140-151).
  - [ ] B5: `--backup-to` combined with server-only flags is rejected after SRV-14.
  - [ ] A3: two mode flags (170) and server options with `--demo` (231) are rejected.

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| FV-09 | property | for every value the CLI accepts, the ledger and plan arithmetic in `server.sig` returns an error or the exact unwrapped result | todo |
| INV-CLI-1 | fuzz | total function: argv to command or error, never a crash | todo |
| INV-CLI-6 | property | for any byte stream, executed statements equal the lines of at most 4096 bytes, in order | todo |

## 10. Benchmarks

None. Startup time is not a tracked metric.

## 11. Open questions

- Should `--insecure-local` be refused when a persistent `--data` path is
  given (THREAT_MODEL §3.4)? This needs a B5 decision.
- Should `--demo` and `--stdin` move to a separate binary so the server
  executable carries no embedded demo database?
