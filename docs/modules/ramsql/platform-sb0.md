# Module: `platform-sb0`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/platform/sb0_app.sig`, `ramsql/src/platform/sb0k.ld` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL (native entry) |
| Size | 80 + 27 lines, 3.0 + 0.9 KB |
| Owner lane | `A6` (SRV-09); `A4` for CI; native SB0 product work is outside PLAN.md lanes |
| Status | prototype (memory-only, trusted transport) |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here) for `sb0_app.sig`; not applicable to `.ld` |

## 1. Purpose and scope

This is the native AArch64 SB0 entry point. It serves one PostgreSQL wire
session over a trusted transport, using the same ZPM SQL engine, `Store` and
PostgreSQL codec as the hosted server. There are two formats, chosen at build
time by the generated `sb0_options.kernel`:

- **SB0X** is an EL0 process that uses the delegated `native.Channel` and exits
  with `processExit`.
- **SB0K** is an EL1 image on the QEMU `virt` UART. It clears BSS itself,
  enables FP/SIMD, and parks on exit.

There is no TCP, WAL, SCRAM, admission control, idle timeout or worker
reactor. The hosted `server.sig` is not used. This is not a production database
image.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `_start` (53) | naked export | SB0K: set `sp` to `__stack_top`, write `cpacr_el1` (FP enable), call `nativeMain`. SB0X: call `nativeMain`, then exit 1 through `svc #0` if it ever returns |
| `nativeMain` (24) | export, `noreturn` | prints a READY marker, runs `Stream.run` to completion, disconnects, prints EXIT or ERROR, then parks or exits |
| `sb0k.ld` | linker script | 64-byte `SB0K` v1 header at `0x40200000` (5-11); `.text`, `.rodata`, `.data` are file-backed; `.bss` and a 16 MiB `.stack` are NOLOAD (20-25) |

The fixed engine profile is 8 tables, 8 columns, 512 rows, 63-byte names,
128-byte text and int32 (11). It uses non-external storage, so all tables live
in BSS. The stream buffer is 65,536 bytes (14).

## 3. Dependencies and layering

- Imports: ZPM `sql`, `db_store`, `postgres`, `stream_server`, `sb0_stream`, and
  the generated `sb0_options`. `tools/build_sb0.py` also passes `scram`.
- Imported by: nothing. It is the root module of `tools/build_sb0.py`, which
  runs `sig build-exe -target aarch64-sb0` with `--stack 16777216` and, for SB0K,
  `--script src/platform/sb0k.ld`. **`build.sig` never compiles it** (SRV-09);
  only the release workflow does.
- Layer rule: no violation.

## 4. Invariants

- **INV-SB0-1** SB0K: BSS is zeroed (26-28) before any BSS global is read. The
  only globals with nonzero initial values (`connection.store = &store`, 17)
  live in `.data`, outside the cleared range.
- **INV-SB0-2** SB0K: `.stack` lies after `.bss` (`sb0k.ld:20-25`), so clearing
  BSS cannot overwrite the active stack. `__stack_top` matches the build's
  `--stack 16777216` (`0x1000000`, `sb0k.ld:25`).
- **INV-SB0-3** The header fields agree with the image: magic `0x4b304253` is
  `"SB0K"` little-endian; the entry offset is `_start - __sb0_image_start`;
  the file size is `__sb0_file_end - __sb0_image_start` (`sb0k.ld:8-10`).
  `build_sb0.py` checks the first four bytes, and `test_sb0.py` checks
  version 1, header 64 and boot ABI 1.
- **INV-SB0-4** Every exit path calls `connection.disconnect()` before parking or
  exiting (33, 37, 44, 48), so an open transaction is aborted.

## 5. Current status and evidence

`tools/test_sb0.py` builds the image, boots it under QEMU (`virt`,
`cortex-a57`, 512 MiB), and rejects three mutated images (bad magic, entry
outside the image, segment out of bounds). It then runs a PostgreSQL startup
and checked queries, including CREATE, INSERT, NULL and BIGINT range probes at
the int64 limits. It needs a QEMU and loader setup. Only the release workflow
builds and runs it, for both formats (`.github/workflows/release.yml:290-291`).
Neither `sig build test` nor PR CI (`ci.yml`) does.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-09 | medium | testing | `build.sig` has no compile step for `src/platform/sb0_app.sig`; only `tools/build_sb0.py:79-93` builds it, and only `release.yml:290` calls that script. `sig build`, `sig build test` and PR CI never type-check it, so a break appears first at release time | add a cross-compile check step (`-target aarch64-sb0`) to the gate, at least as a compile-only step | A6 |

## 7. Security considerations

- Trust boundary: the transport (UART or delegated channel) is trusted by
  definition. There is no authentication. Anyone who can write to the channel
  has full SQL access.
- `scram` is passed to the build but SCRAM is not configured: no entropy
  source, no verifier. `build_sb0.py` metadata records `"scram": false`.
- Memory-only: the data is lost on exit, and nothing reaches storage.
- A wire-codec bug has the same impact as on hosted builds. Fuzzing the ZPM
  PostgreSQL codec (B1) covers it.

## 8. Tests

- **Existing:** `tools/test_sb0.py` (section 5); `tools/build_sb0.py` checks
  the image magic.
- **Missing:**
  - [ ] A6: compile-only `sb0_app.sig` step in `build.sig` for SB0X and SB0K (SRV-09).
  - [ ] A4: run `test_sb0.py` nightly on a pinned QEMU, not only at release.
  - [ ] A3: link-map check that the `.data` and `.bss` boundaries match INV-SB0-1 and INV-SB0-2.

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| FV-10 | Lean + enumeration | applies through the shared ZPM PostgreSQL session. Note that the native build configures no authentication | todo |
| INV-SB0-3 | property | header fields match the linked image for every build | todo |

## 10. Benchmarks

None. Native SB0 performance is not in suites S1 to S10.

## 11. Open questions

- Should native SB0 share the hosted engine `Config` (factorized aggregates and
  external storage), or keep its separate fixed profile (11)?
- What is the plan for an authenticated or durable SB0 profile, and which lane
  owns it?
