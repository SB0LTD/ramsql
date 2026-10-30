# Module: `library-facade`

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/root.sig` (module `ramsql`); contract `ramsql/tests/api.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL (public facade over ZPM L0 `sql`) |
| Size | 16 lines, 0.4 KB (`root.sig`); 20 lines, 1.1 KB (`api.sig`) |
| Owner lane | `A6` (code); `A5` (version and license metadata) |
| Status | beta |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here) |

## 1. Purpose and scope

`root.sig` is the embeddable library surface named `ramsql`. It re-exports the
ZPM SQL engine (`Config`, `Error`, `Database`) and defines the package version
and one small default profile, `DefaultDatabase`. Embedded callers get the full
engine with caller-owned storage and serialized calls. They do not get the
server, wire protocols, persistence or the multi-client `Store`. AGENTS.md
requires this facade to stay thin: SQL behavior belongs in ZPM.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `version` (4) | const | `"0.3.0-beta.2"`; must equal `build.sig.zon` `.version` (checked by `tools/release_matrix.py:60-65`) |
| `Config` (5) | type | alias of ZPM `sql.Config` |
| `Error` (6) | type | alias of ZPM `sql.Error` |
| `Database` (7) | fn → type | alias of ZPM `sql.Database(config)`; storage is inline unless `external_*` is set |
| `DefaultDatabase` (8-16) | type | 4 tables, 8 columns, 128 rows, 32-byte names, 128-byte text, int32, `nulls_first_ascending = false` |

## 3. Dependencies and layering

- Imports: ZPM `sql` only.
- Imported by: `src/main.sig:3` (demo and `--stdin`, and `version`);
  `src/server.sig:831` (`version` in `--capabilities`); `tests/api.sig:2`;
  `build.sig:141-142` registers it as module `ramsql`.
- Layer rule: no violation. `zpm.pkg.zon` declares `.layer = 0` and a
  dependency on `@zpm/sql@0.1.0`. REPO-12 records that the declared dependency
  and layer are wrong.

## 4. Invariants

- **INV-LIB-1** The facade adds no behavior: every exported type is the ZPM
  type, so ZPM contracts cover it.
- **INV-LIB-2** `version` equals `build.sig.zon` `.version`
  (`tools/release_matrix.py:64` raises otherwise). `zpm.pkg.zon` `.version` is
  not checked.
- **INV-LIB-3** `DefaultDatabase` has inline storage. It is a static in any
  binary that declares a global of it (for example `main.sig:10`), sized at
  compile time.

## 5. Current status and evidence

`tests/api.sig` (contract `contract-ramsql`, run by `sig build test`) builds a
one-table `Database`. It checks CREATE, an INSERT affecting 1 row, a SELECT
returning 1 row and 1 column with text `"ramsql"`, a DELETE affecting 1 row,
and DROP. It also checks that `@sizeOf(DefaultDatabase) != 0`.
`tools/test_cli.py` exercises `DefaultDatabase` through `--demo` and `--stdin`.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| REPO-02 | high | docs / legal | `zpm.pkg.zon:12` `.license = "MIT"` and `README.md:343` "MIT License" contradict `LICENSE:5` (proprietary) | one license statement everywhere | A5 |
| REPO-12 | medium | completeness | `zpm.pkg.zon:6, 9` layer and dependency; `build.sig.zon:4` fingerprint `0x0` | correct the manifests (with A0) | A1 |

No SRV finding applies to this facade.

## 7. Security considerations

None specific. The facade exposes the engine to in-process callers, who are
fully trusted. `DefaultDatabase` has no authentication, persistence or
concurrency control beyond serialized calls.

## 8. Tests

- **Existing:** `tests/api.sig` (section 5). It returns errors on failure but
  has no assertion counter or minimum count, unlike `tests/backup.sig` and
  `tests/persistence.sig`.
- **Missing:**
  - [ ] A3: give `api.sig` an assertion count and a minimum, per STRATEGY L1.
  - [ ] A3: contract that `ram.version`, `build.sig.zon` and `zpm.pkg.zon` agree (INV-LIB-2).
  - [ ] A3: compile-only check that the facade still exposes `Config`, `Error`, `Database` and `DefaultDatabase` with the documented profile.
  - [ ] B7: an embedded transaction example. The README promises interleaved READ COMMITTED transactions for embedded Sig, but `api.sig` only covers autocommit.

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| none | — | The facade has no logic. Engine obligations (FV-01, FV-02, FV-07 and others) attach to ZPM `sql` and cover this surface through INV-LIB-1. | n/a |

## 10. Benchmarks

None directly. The S1 and S2 native binaries call ZPM `sql` directly, not this
facade (`benchmark-binaries.md`).

## 11. Open questions

- Should `DefaultDatabase` stay public, given that the server binary carries a
  static instance only for `--demo` and `--stdin`?
- Should the facade export a stable, versioned subset rather than the whole
  ZPM `sql` namespace, so ZPM changes do not silently change the public API?
