# RAMSQL hardening and professionalization plan

**Consolidation update, 30 October 2026:** ZPM PRs 77, 81 and 82–85 and RAMSQL
PRs 30, 34 and 36 are merged. The current runtime pin `0f5af8d` includes NET-01,
STO-01, STO-03 and the SRV-01 file primitive; RAMSQL includes explicit
`--create-data`, typed DISTINCT and the window/FILTER extension. The combined
source passes the three-OS database matrix and all 32 active RAMSQL checks.
See [exact consolidation/evidence identities](compatibility/PR_CONSOLIDATION_20261030.md).
The baseline measurements/findings below describe the original audit revisions;
they are not all current code claims. NET-02, STO-02/04/05, the SQLF divergences
and the remaining production gaps are still open. The refreshed original SQL
TDD inventory has 8 smoke passes, 93 failures and 17 unexecuted cases; see
[the current checkpoint](https://github.com/SB0LTD/zpm/blob/main/docs/SQL_CONFORMANCE_TDD_STATUS.md).

| | |
| --- | --- |
| Status date | 2026-10-30 |
| Baseline | RAMSQL `origin/main` 6d677ee (PR #35), ZPM `2f060c2` (pinned), Sig 0.6.4 |
| Published release | v0.3.0-beta.2 (defacb7, 2026-10-27) |
| Scope | RAMSQL repository and the ZPM modules it ships (`src/core/sql*`, `src/db/*`, the platform modules it imports) |
| Method | Six parallel read-only audits (server, wire protocols, SQL front end, storage/MVCC/WAL, tooling/CI, roadmap/branches), spot-verification of every critical and high finding, and a full contract-suite run |

This plan explains where RAMSQL stands, what is missing, incomplete or unsafe,
and how three parallel workflows of agents working in git will turn it into a
professional, verifiable code base. Everybody working on it should be able to
explain every module, invariant and claim.

Companion documents (all under `docs/`):

| Document | Purpose |
| --- | --- |
| [docs/README.md](docs/README.md) | Index of everything below |
| [docs/architecture/OVERVIEW.md](docs/architecture/OVERVIEW.md) | Layers, request and durability paths, memory model |
| [docs/architecture/THREAT_MODEL.md](docs/architecture/THREAT_MODEL.md) | Assets, actors, threats, deployment requirements |
| [docs/architecture/REPO_LAYOUT.md](docs/architecture/REPO_LAYOUT.md) | Target tree and migration map |
| [docs/modules/](docs/modules/README.md) | One page per module: contract, invariants, findings, tests, verification |
| [docs/verification/STRATEGY.md](docs/verification/STRATEGY.md) | Test levels L0 to L12 and their oracles |
| [docs/verification/FORMAL.md](docs/verification/FORMAL.md) | Lean 4 and TLA+ program, theorem catalog FV-01 to FV-22 |
| [docs/benchmarks/METHODOLOGY.md](docs/benchmarks/METHODOLOGY.md) | How measurements are taken, judged and stored |
| [docs/process/GIT_WORKFLOW.md](docs/process/GIT_WORKFLOW.md) | Lanes, branches, worktrees, pins, PR gates |
| [docs/process/DEFINITION_OF_DONE.md](docs/process/DEFINITION_OF_DONE.md) | What "done" means for every PR |
| [docs/compatibility/FEATURE_GAPS.md](docs/compatibility/FEATURE_GAPS.md) | PostgreSQL features and divergences that block applications |

---

## 0. Summary

**What is strong.**
- The engine is real: there is no mock and no SQLite. It is allocator-free and
  has bounded parsers.
- SCRAM is done carefully: constant-time proof check, canonical base64, no
  username oracle.
- WAL flush-before-publication and fail-closed recovery are implemented.
- Benchmarks are honest, and losses are published.
- Release packaging is deterministic and provenance is attested.
- The PostgreSQL reference images are digest-pinned.
- The 34 contract suites that run pass, with 416,938 executed assertions.

**What is wrong, in order of urgency.**

1. **Critical: bind parameters can rewrite statements (NET-01).** Integer
   parameters are spliced into SQL text unparenthesized. With `$1 = -5`,
   `UPDATE a SET bal = bal-$1 WHERE id = $2` becomes `bal--5 ...`. The `--`
   starts a comment, the `WHERE` clause disappears, and every row is updated.
   This was verified in ZPM `2f060c2`. TDS `sp_executesql` uses the same
   approach. Fix it first (wave 0).
2. **Critical: concurrent `CREATE TABLE` can make the WAL unrecoverable
   (STO-01).** Two transactions that create tables and commit out of OID order
   write a log that replay rejects, so the server refuses to restart. Related
   high findings:
   - reclamation can drop versions that a resumed statement still reads, which
     silently loses updates (STO-02);
   - a lock list can overflow its array (STO-03);
   - sequence values can repeat after a crash (STO-04);
   - a torn final record blocks startup (STO-05).
3. **A typo in `--data` silently creates a new, empty database (SRV-01).**
4. **Date and time arithmetic overflows i64 on valid input (SQLF-01).** One
   `SELECT` panics in ReleaseSafe and is undefined behavior in ReleaseFast.
   SQL-standard interval literals also return silently wrong values (SQLF-02).
5. **RAMSQL's test gate runs 32 of the 83 ZPM suites at its pin (TST-01).**
   About 36 relevant suites, including window functions, parallel reads, work
   control, the reader/writer lock and worker threads, never run in RAMSQL CI.
   ZPM's own `native-sig` check fails routinely.
6. **Nothing forces CI before merge (REPO-01).** `main` has no branch
   protection or ruleset, actions are pinned to tags, and the publish job
   has no protected environment.
7. **Transport and identity are missing.** There is no TLS or channel binding,
   no roles, and no authentication on MySQL or TDS (`NET-05`, gates
   `transport_security` failed and `authentication` partial). There is no
   authentication deadline, and a slowloris client can hold slots (NET-04,
   SRV-06).
8. **Concurrency and durability are thinly verified.** The MVCC suite executes
   122 assertions. There is no deterministic simulation, no fault-injection
   seam for fsync failure or short writes (SRV-08), and no power-loss test.
   STO-01 to STO-04 are exactly the bugs those harnesses exist to find.
9. **The repository is hard to understand.**
   - Generated evidence makes up 59% of tracked bytes (2,530 benchmark files,
     109 copied scripts), and the website makes up 34%.
   - The license is stated three different ways (REPO-02).
   - The website claims "488x faster" and "snapshot isolation", which the
     README contradicts (REPO-03).
   - The tests are ad-hoc scripts that double as libraries (REPO-08).
   - Every RAMSQL `.sig` file fails `sig fmt --check` (REPO-21).
   - `server.sig` holds 28 globals and has no unit tests (SRV-02).
10. **Production gates are open.** Eight of nine gates in
   `compatibility/release-gates.json` are open. There is no checkpoint or WAL
   compaction, results are not streamed, and there are no secondary indexes.
   Rows are stored inline, so 100M rows needs about 253 GB per bank.

**What this plan does.** It organizes the work into three workflows and 27
lanes:

| Workflow | Goal | Lanes |
| --- | --- | --- |
| **A. Foundation** | make the repository legible, gated and honest | A0 to A6 |
| **B. Hardening** | close security and correctness gaps and build the harnesses that prove it | B1 to B11 |
| **C. Assurance and scale** | Lean and TLA+ models linked to the code, reproducible benchmarks, and the storage work toward 100M rows | C1 to C9 |

Each lane runs in its own git worktree with an implementer, a test author, an
adversarial reviewer and a verifier. Work moves through four waves, and each
ends at a checkpoint with measurable exit criteria (§10).

---

## 1. Baseline snapshot

### 1.1 Revisions

| Item | Value | Note |
| --- | --- | --- |
| RAMSQL `origin/main` | 6d677ee | 19 commits ahead of the stale `license/governing-law-tel-aviv` checkout |
| ZPM pin on main | `2f060c2` | effectively at ZPM main (1 commit behind on GitHub) |
| Stale pin in the old checkout | `9606877` | 47 commits behind; only `bench/factorized-execution-results` still uses it |
| Sig | 0.6.4 (`sig-0.6.4-zig0.17.0.90a898d`), runtime pin `0df3506` | `toolchains/sig-runtime.json` |
| Open RAMSQL PRs | #30 evidence, #34 typed DISTINCT (conflicting), #36 window frames | depend on ZPM #77 and #81 |

### 1.2 Contract suites (`sig build test -Doptimize=ReleaseSafe`)

The run uses Windows 11 on an i9 (32 logical cores, 31.7 GB RAM).

- **With the default job count:** one compile ran out of memory
  (`LLVM ERROR: out of memory`, only 0.9 GB free), and the remaining 46 steps were
  skipped, so no suite ran at all.
- **With `-j2`:** 71 of 81 steps succeeded and none failed, in 9 min 55 s.
  Counts are executed assertions from each suite's own report:

| Suite | Assertions | Suite | Assertions |
| --- | ---: | --- | ---: |
| sql (engine) | 8,431 | sql-index | 239,627 |
| postgres | 15,804 | sql-query | 63,417 |
| scram | 24,759 | sql-compact-scan | 23,371 |
| tds | 2,661 | sql-rollup | 6,409 |
| mysql | 1,168 | durable_log | 4,834 |
| store | 3,154 | sql-types | 4,491 |
| pg-copy / postgres-stream | 478 / 749 | sql-summary / sql-integer-topk | 4,136 / 3,140 |
| sql-change-log | 876 | sql-stream-group | 1,702 |
| sql-storage | 619 | sql-dense-group | 610 |
| sql-expr | 397 | sql-dml | 220 |
| **sql-mvcc** | **122** | runtime-workspace | 153 |
| **persistence** | **112** | **sql-catalog** | **36** |
| **tcp_server** | **42** | **db_file** | **27** |
| **backup** | **11** | entropy / wall_clock | 1,042 / 4,002 |

The table above is from the old checkout's pin `9606877`.

**At `origin/main` with `2f060c2`:**
- 71 of 81 steps succeeded and none failed, in 25 min 51 s at `-j2` on the
  same host while other work ran;
- the same 34 suites passed with 416,938 executed assertions in total;
- every count is identical except `postgres`, which went from 15,804 to 15,807.

The thin suites in bold sit exactly where correctness matters most: MVCC,
persistence, backup and files. None of the critical or high findings in §3 is
caught by these suites.

### 1.3 Integration and qualification results

These are the latest results recorded on `main` (CI and compatibility reports).

| Area | Result | Source |
| --- | --- | --- |
| PostgreSQL probes | 37/37 | `compatibility/postgres-report.json` |
| PostgreSQL semantics | 141/151 | same |
| Isolation (READ COMMITTED) | 44/44 | same |
| COPY | 11/11 | same |
| dbMeter reviewer checks | 7/7 correctness, but only on unmerged PR #30 | `bench/factorized-execution-results` |
| dbMeter full 5M rows | totals plus 2 of 6 queries pass and 4 are unsupported; mixed QPS 0.93 and 1.96, against IRDB's 34.96 and 44.91 | run 41024725859 |

### 1.4 Release gates (`compatibility/release-gates.json`, last changed 2026-10-22)

| Gate | Status | What closes it in this plan |
| --- | --- | --- |
| postgres_compatibility | failed | B7, B11, client matrix (L6), upstream regress subset |
| implemented_subset_durability | passed | keep: B9 crash-point and fault tests, FV-05 and FV-06 |
| authentication | partial | B4 roles, B2 MySQL/TDS credentials, B5 |
| transport_security | failed | B3 TLS 1.3 with SCRAM-SHA-256-PLUS |
| capacity_concurrency | failed | C9 streaming and compact storage, B8, B6 |
| backup_restore | partial | C8 checkpoint, online backup, upgrade migration |
| matched_performance | unproven | C6 and C7 under the benchmark methodology |
| operations | failed | B6 logging and health, C7 metrics, A5 runbooks |
| storage_power_failure | unverified | B9 power-loss qualification (LazyFS or dm-flakey on Linux) |

### 1.5 Roadmap stages

| Stage | Status | Remaining |
| --- | --- | --- |
| Beta.1 baseline | done (folded into beta.2) | `RELEASE.md` still titled beta.1 (REPO-04) |
| Beta.2 reviewer blockers | done, published 2026-10-27 | none |
| Beta operations (TLS, roles, metrics) | mostly not started | B3, B4, B6, C7 |
| Storage and sustained load | partial (runtime banks, byte ledger) | C8 checkpoints, C9 streaming, compact pages |
| PostgreSQL application compatibility | partial | B7, B11: foreign keys and secondary indexes, REPEATABLE READ and SERIALIZABLE (0A000 today), catalogs, ORM suites |
| MySQL and SQL Server dialects | partial, experimental | B2 |
| Additional dialects | not started | out of scope for this plan |
| Multi-core | partial (inter-query parallel reads) | C9 intra-query parallelism, B8 concurrent writes |
| Native SB0 parity | partial (QEMU memory channel only) | out of scope except keeping it compiling (A6) |
| 100M rows (`MACHINE_CAPACITY_AND_100M_PLAN.md`) | step 1 done, 2 mostly done, 3 not integrated, 4 and 5 partial | C9 |

### 1.6 Integration debt

| Id | Debt | Action (lane A0) |
| --- | --- | --- |
| INT-01 | RAMSQL PRs #30, #34 and #36 depend on ZPM PRs #77 and #81. No ZPM commit contains both. | merge order in §7 A0 |
| INT-02 | Docs contradict reality: dbMeter "3/7" against 7/7 evidence, `VALIDATION.md` says alpha.3, `INFRASTRUCTURE_PROGRESS.md` says alpha.4, `RELEASE.md` says beta.1 | A5 docs-truth PR |
| INT-03 | The sparse-factor feature is merged in RAMSQL history but not in ZPM, and the pin was moved back | maintainer decision (§17 D7) |
| INT-04 | 34 local and 35 remote merged branches; 3 in-repo and 4 sibling worktrees (1.5 GB); the ZPM checkout is on an unrelated branch with a modified README | cleanup only with approval |
| INT-05 | `release-gates.json` has not changed since 2026-10-22 although gates moved | A5 refresh with evidence links |

---

## 2. Assessment

### Strengths to preserve

- A clean layering idea: ZPM engine, RAMSQL product policy, no lower-to-higher
  imports.
- Bounded everything: parser depth limits on recursion and tree size, frame
  limits, admission errors with real SQLSTATEs.
- Durability discipline: an exclusive writer lock, a WAL created with mode 0600
  and its directory synced, poisoning after an I/O error, and fail-closed
  recovery that truncates only an incomplete final record.
- Honest evidence culture: losses and unsupported queries are published, and
  binary hashes are recorded.
- Contract suites already emit JSON with assertion counts, a good base for
  assertion-count gating.

### Weaknesses to remove

| Theme | Symptoms | Lanes |
| --- | --- | --- |
| Unsafe boundary | NET-01 and NET-02 parameter splicing; `unreachable` on wire-reachable paths (NET-07); i64 overflow in temporal code (SQLF-01) | B1, B2, B7 |
| Unverified concurrency and durability | STO-01 (WAL that cannot be replayed), STO-02 (lost update through reclamation), STO-03 (lock-list overflow), STO-04 (duplicate sequence values); 122 MVCC assertions, no simulation, no fault seam, no power-loss test, two divergent reactors (SRV-03) | B8, B9, A6, C2, C3 |
| Ungated quality | no branch protection, about 36 relevant ZPM suites never run by RAMSQL, unformatted code, shallow fuzzers | A3, A4, B1, B2 |
| Illegible repository | evidence and website dominate, ad-hoc scripts, stale and overlapping plans, three license statements | A1, A2, A5 |
| Monolithic product code | `server.sig` has 28 globals, lines up to 1,300 characters and 50-argument JSON writers; the CLI is a 600-character if/else | A6 |
| Missing production features | TLS, roles, checkpoints, streaming results, secondary indexes, common types and functions | B3, B4, C8, C9, B11 |

---

## 3. Findings register

Severity: **C** critical, **H** high, **M** medium, **L** low. The full evidence
(`file:line`, remedy, tests) is on each module page. Status starts as `open`;
lanes update only the status column of their own findings.

### 3.1 Wire protocols (ZPM `src/db`, `src/platform/tcp_server.sig`)

| Id | Sev | Finding | Lane | Status |
| --- | --- | --- | --- | --- |
| NET-01 | **C** | Integer bind parameters spliced as bare text: int4/int8 in PostgreSQL (`postgres.sig:1666-1672`; int2 is quoted and unaffected; inferred OID-0 parameters take the column's type, so they are exposed too), SMALLINT/INT/BIGINT in TDS `expand`. `-$1` with a negative value forms a `--` comment and truncates the statement | B1, B2 | **fixed** in wave 0 (ZPM `b1/net01-param-splice`; reviewed; unmerged) |
| NET-02 | M | The placeholder scanner ignores nested `/* */` comments that the lexer honors, so substitution can target the wrong text | B1 | open |
| NET-03 | M | A named suspended portal survives COMMIT or ROLLBACK and re-executes from row 0, repeating `DML ... RETURNING` side effects | B1 | open |
| NET-04 | M | Slowloris: any byte refreshes the idle timer, there is no authentication deadline, parked clients are exempt from the idle timeout | B6 | open |
| NET-05 | M | Plaintext only (SSLRequest, MySQL SSL and TDS encryption all refused); `--insecure-local` disables authentication on all three protocols | B3, B2 | open |
| NET-06 | M | Execute checks the result-format count after executing, so DML can run before the 08P01 error | B1 | open |
| NET-07 | M | `unreachable` and `@intCast` on cell variants in MySQL, TDS and `pg_types.sendBinary` are undefined behavior in ReleaseFast if an engine invariant breaks | B2 | open |
| NET-08 | M | Parse with unknown (oid 0) parameter types fails outside simple INSERT, UPDATE, SELECT and DELETE shapes; `SELECT $1` returns 0A000 | B1 | open |
| NET-09 | L | Cancel keys: predictable pid, 32-bit secret, no rate limit | B6 | open |
| NET-10 | L | `client_encoding` compared case-sensitively at startup | B1 | open |
| NET-11 | L | Internal `@errorName` text sent to clients, including before authentication | B1 | open |
| NET-12 | L | Static MySQL scramble (must be random before passwords exist) | B2 | open |
| NET-13 | L | No UTF-8 validation on SQL text and COPY; a NUL written over PostgreSQL blocks TDS reads | B1, B2 | open |
| NET-14 | L | TDS conformance: fExtension and RESETCONNECTION rejected, NVARCHAR length, isolation ignored, ATTENTION cannot cancel | B2 | open |
| NET-15 | L | PostgreSQL conformance: no NegotiateProtocolVersion, startup errors not FATAL, one suspended portal, no NoticeResponse | B1 | open |
| NET-16 | L | `stream_server` rejects `consumed == 0` with `written != 0`, which COPY error paths produce | B1 | open |
| NET-17 | L | Windows listener lacks `SO_EXCLUSIVEADDRUSE` | B6 | open |
| NET-18 | M | Fuzzers are shallow: fixed-seed loops of 1K to 8K inputs that never reach authentication or COPY; MySQL and TDS barely past the handshake | B1, B2 | open |
| NET-19 | L | `pg_types` has no dedicated suite | B1 | open |

### 3.2 RAMSQL host layer (`src/`, `build.sig`)

| Id | Sev | Finding | Lane | Status |
| --- | --- | --- | --- | --- |
| SRV-01 | **H** | The default `--data ramsql.wal` is relative and `File.open` creates it, so a wrong directory or a typo starts an empty, authoritative database | B5 | **fixed** in wave 0 (RAMSQL `b5/srv01-create-data` + ZPM `b5/srv01-not-found`; reviewed; unmerged) |
| SRV-02 | **H** | No unit or property tests for `server.sig`, `main.sig`, `timezones.sig` or `capacity.sig`; 28 module-level globals | A6 | open |
| SRV-03 | M | Two diverging reactors. The parallel one wakes parked clients only when `tryLock` succeeds, so timeouts and cancel can be delayed without bound under write contention | A6 | open |
| SRV-04 | M | `--backup-to` recovery can truncate a torn tail of the **source** WAL, which contradicts the documentation | B5 | open |
| SRV-05 | M | Hidden coupled constants (60, 62, 63 clients; 5 MiB charge against a 4 MiB stack) with no comptime asserts | A6 | open |
| SRV-06 | M | The 64-entry admission queue with a 1 s deadline is easy to fill, and closes logins silently | B6 | open |
| SRV-07 | M | Errors swallowed without logging; authentication failures neither logged nor throttled | B6 | open |
| SRV-08 | M | No I/O fault-injection seam; fsync failure, short write and backup-verification paths untested; two assertions accept any error | B9 | open |
| SRV-09 | M | `platform/sb0_app.sig` is never compiled by `sig build`, `sig build test` or PR CI; only the release workflow builds it, so a break surfaces at release time | A6 | open |
| SRV-10 | M | Unreadable formatting: lines up to 1,301 characters, 50-argument positional JSON | A6 | open |
| SRV-11 | L | No permission check on the verifier file; `secureZero` skipped on error paths | B5 | open |
| SRV-12 | L | Dead cancel paths, with a re-entrancy hazard in single-thread mode | A6 | open |
| SRV-13 | L | Validation duplicated and inconsistent; storage reserved before the authentication check fails | A6 | open |
| SRV-14 | L | Repeated flags override silently; `--backup-to` silently ignores incompatible flags | A6 | open |
| SRV-15 | L | The real `--rows` ceiling is 536,870,911, not the documented 1,073,741,823 | C5 | open |
| SRV-16 | L | `capacity.sample` doubles its buffer without a cap | A6 | open |
| SRV-17 | L | Static buffers (overflow queue, recovery buffer, zone cache, demo DB) are outside the byte ledger | C5 | open |
| SRV-18 | L | Blocking TZif file I/O under the global write lock | A6 | open |
| SRV-19 | L | Help text and `OPERATIONS.md` drift ("SQL serialized", "one reactor") | A5 | open |
| SRV-20 | L | `build.sig` hygiene: a failing test reports `error.BufferTooSmall`, unused modules, the review benchmark never runs in CI | A3 | open |
| SRV-21 | L | `@panic` on any region-release failure (16 sites) takes down every connection | A6 | open |
| TST-01 | **H** | The RAMSQL gate runs 32 of 83 ZPM suites at its pin (31 listed plus the engine suite); 36 relevant suites never run, including the ones that test the four engine flags the server enables | A3 | open |
| TST-02 | M | `sig build test` is all-or-nothing: one compile OOM skips every run step; there is no `-j` guidance | A3 | open |
| TST-03 | M | ZPM `native-sig` check fails on merged PRs, so red has become normal | A0, A4 | open |

### 3.3 SQL front end and types (ZPM `src/core/sql/`)

| Id | Sev | Finding | Lane | Status |
| --- | --- | --- | --- | --- |
| SQLF-01 | **H** | Unchecked i64 overflow in date and time arithmetic (`temporal.sig:355` and others): a panic in ReleaseSafe, undefined behavior in ReleaseFast. `date ± int8` is accepted where PostgreSQL rejects it. | B7 | open |
| SQLF-02 | **H** | Silent wrong results for SQL-standard interval forms (`'1-2'`, `'3 4:05:06'`); `INTERVAL '1' DAY` becomes 1 second aliased "day" | B7 | open |
| SQLF-03 | M | Comparisons between REAL and int or numeric happened in float4 (PostgreSQL uses float8) | B7 | **closed upstream** at `2f060c2` (`value.comparisonType`); keep the differential test |
| SQLF-04 | M | `jsonb \|\| '{...}'` resolves the unknown literal as text and concatenates strings | B7 | open |
| SQLF-05 | M | `date - date` ignores infinity and can return an out-of-range int4 | B7 | open |
| SQLF-06 | M | Possible stack exhaustion at large `text_bytes` (by-value cells in recursive evaluation, 43 KB of JSONB stack per nesting level) | B7 | open |
| SQLF-07 | M | JSONB key insertion sort (O(k²), `jsonb.sig:443`) ignores cancel and statement timeout (LIKE now checkpoints at `2f060c2`) | B7 | open |
| SQLF-08 | M | Specialized scan strategies duplicate the general engine; arithmetic dispatch relies on binder invariants | B10 | open |
| SQLF-09 | M | JSONB that can be stored but not read back (renders past the buffer, 54000) | B7 | open |
| SQLF-10 | L | Invalid SQL accepted: duplicate FROM aliases, ambiguous ORDER BY, comma mixed with JOIN, `x ISNULL` as an alias | B7 | open |
| SQLF-11 | L | Smaller PostgreSQL divergences: `key` reserved, `NaN/0`, `'1._5'`, UTF-8 name truncation, `deparse` quoting, ASCII-only ILIKE | B7 | open |
| SQLF-12 | L | Stale headers, compressed one-line patches, helpers copied five times | A4, B7 | open |
| SQLF-13 | M | Testing: byte-noise fuzzing only, no property tests for numeric, float or JSONB, few LIKE cases, no date-boundary tests | B7, C1 | open |
| SQLF-15 | M | Legacy alpha-format WAL replay fails when a statement names an identifier that later became reserved (`symmetric` and `asymmetric` after `9606877`), so recovery stops. Older reserved words may have the same gap. | B7 (parser fix), B9 (replay fixtures) | open |
| SQLF-14 | **H** | Application-blocking gaps: common types, scalar functions, DDL (indexes, constraints, ALTER), syntax; see [FEATURE_GAPS.md](docs/compatibility/FEATURE_GAPS.md) | B11 | open |

### 3.4 Storage, MVCC, locks, change log, WAL (ZPM `src/core/sql.sig`, `exec`, `storage`, `mvcc`, `locks`, `change_log`, `db/store`, `db/durable_log`)

| Id | Sev | Finding | Lane | Status |
| --- | --- | --- | --- | --- |
| STO-01 | **C** | Concurrent DDL committed out of OID order makes the WAL unrecoverable. OIDs are assigned when the statement runs; replay rejects any create whose OID is below the previous one (`change_log.sig:713`). Example: A `BEGIN; CREATE TABLE a`, B `BEGIN; CREATE TABLE b; COMMIT`, A `COMMIT`, restart → startup aborts. | B9 | **fixed** in wave 0 (ZPM `b9/sto01-replay-order`; reviewed; unmerged) |
| STO-02 | **H** | Reclamation can free versions that a resumed statement or COPY still sees. `horizon()` counts only pins (`sql.sig:1315`), and a resume clears the pin before the body runs. Result: a silent lost update on resumed UPDATE, and duplicate or missing rows on resumed INSERT ... SELECT. | B8 | open |
| STO-03 | **H** | Transaction lock list overflow. `FOR SHARE` then `FOR UPDATE` adds a second holder entry, and `pushLock` has no bounds check (`mvcc.sig:94`). From 140 rows at RAMSQL's build defaults (134 at ZPM defaults) it panics in safe builds and corrupts memory in ReleaseFast. | B8 | **fixed** in wave 0 (ZPM `b8/sto03-lock-list`; reviewed; unmerged) |
| STO-04 | **H** | Sequence values can be issued twice across a crash. Commits from the cached log-ahead range do not persist the watermark. | B9 | open |
| STO-05 | **H** | A full-length torn final record (multi-write append, power loss) is treated as corruption, so the server refuses to start | B9 | open |
| STO-06 | **H** | Recovery cost grows with records × rows: `liveRows` is recomputed with a full scan for every table on every record, and there is no checkpoint | B9, C8 | open |
| STO-07 | M | Unbounded WAL; replay capacity must cover historical peaks | C8 | open |
| STO-08 | M | O(N) `vacuum` and `compact` (row-index rebuild) inside INSERT near capacity: latency cliffs | C9 | open |
| STO-09 | M | A persist failure aborts in memory while the record may already be durable; memory and disk diverge until restart | B9 | open |
| STO-10 | M | Resume by re-execution re-evaluates `random()`, `nextval` and `clock_timestamp()` | B8 | open |
| STO-11 | M | DML locks only its target table; source tables of INSERT ... SELECT or subqueries can be dropped mid-transaction | B8 | open |
| STO-12 | M | 8 fast paths have no `Config` switch and so no reference comparison: summary states, streamed cursor, dense group, streamed hash grouping, PK-key speculation, layout order, integer top-k, top-k heap. The 5 switchable paths (factor join, factor CTE, dimension-group ids, typed multi-group, block presence) already have twin-database tests. Same root as SQLF-08. | B10 | open |
| STO-13 | M | The replay entry point is unguarded; validate and apply choose table slots differently; apply ignores a missing slot | B9 | open |
| STO-14 | L | Writer starvation: share requests ignore queued writers | B8 | open |
| STO-15 | L | `tx.cid` and the row-slot generation are u32 and can wrap | B8 | open |
| STO-16 | L | The u16 expression-length narrowing in change records (`change_log.sig:241`) relies on RAMSQL's `text_bytes ≤ 65535`; the u8 casts are already guarded by a comptime check (`sql.sig:194`) | B9 | open |
| STO-17 | L | `std_assert` → `unreachable` in `exec.sig` is undefined behavior in ReleaseFast | B10 | open |
| STO-18 | L | fsyncgate on Linux (replaying page-cache-only bytes after an fsync failure in the same boot); Windows parent-directory sync is a no-op | B9 | open |
| STO-19 | L | `sql.sig` (2.6k lines) is a catch-all; `exec.sig` has about 10 bespoke aggregation engines in dense one-line style | B10, C9 | open |
| STO-21 | L | `catalog_counters` validate rejects any counter below the running maximum (`change_log.sig` ~816). The live counter can decrease through abort rewinds, so a future checkpoint or commit path that writes this op from the live counter would make replay refuse to start. No production caller today. | C8 | open |
| STO-22 | L | The live engine can create a keyless table at OID `0xfffffffe` (`sql.sig` ~1799 guard), but replay validation rejects any table OID `>= 0xfffffffe`. | B9 | open |
| STO-20 | M | Thin tests: 122 executed MVCC assertions, no reference comparison for the 8 unswitchable fast paths, no metamorphic oracle for window functions and ROLLUP (`windowCore`, `groupingRollup`), no capacity failpoints | B8, B10 | open |

These were verified at `2f060c2`: STO-01 (`change_log.sig:713`), STO-02
(`sql.sig:1315-1321`) and STO-03 (`mvcc.sig:94-98`). The others come from the
audit's code reading; each module page records their evidence.

### 3.5 Repository, tooling, CI, release, docs

| Id | Sev | Finding | Lane | Status |
| --- | --- | --- | --- | --- |
| REPO-01 | **H** | No branch protection or ruleset on `main` of a public repository | A4 | open |
| REPO-02 | **H** | Three license statements: proprietary LICENSE, "MIT License" in README, `license = "MIT"` in `zpm.pkg.zon`; earlier tags shipped MIT | A5 | open |
| REPO-03 | **H** | ramsql.com claims "488x faster than IRDB" and "snapshot isolation"; README says READ COMMITTED and slower on analytics | A5 | open |
| REPO-04 | M | Release notes for beta.2 are headed "0.3.0-beta.1" | A5 | open |
| REPO-05 | M | All actions pinned to mutable tags, no dependabot | A4 | open |
| REPO-06 | M | Publish job has no protected environment; prereleases skip `release_gate.py` | A4 | open |
| REPO-07 | M | Duplicate CI runs on PRs, no concurrency cancellation, no caching | A4 | open |
| REPO-08 | M | No test framework: home-made `check()` in six files, test modules imported as libraries, binary lookup copied into 19 scripts | A3 | open |
| REPO-09 | M | 16 tools never run in CI (benchmark, compare, profile, large runtime) | A3 | open |
| REPO-10 | M | Evidence is about 59% of tracked bytes: 2,530 files in 57 folders, 109 copied sources, an external harness that cannot be reproduced | A2 | open |
| REPO-11 | M | The website (6.6 MB, a zip, filenames with spaces) lives in the code repo | A1 | open |
| REPO-12 | M | Package manifest: `.paths` includes evidence, fingerprint `0x0`, `zpm.pkg.zon` dependency and layer wrong | A1 | open |
| REPO-13 | M | Planning documents are stale and overlap (GAP_CLOSING_PLAN 1,105 lines, DEEP_INFRASTRUCTURE_RESEARCH 722) | A5 | open |
| REPO-14 | M | No SECURITY, CONTRIBUTING, CODE_OF_CONDUCT, CODEOWNERS, templates, ADRs or threat model | A5 | open |
| REPO-15 | M | Git state: 69 merged branches, 1.5 GB of worktrees, `.agents/` and `.worktrees/` not ignored, mixed commit style | A1 | open |
| REPO-16 | L | Stray files: two empty untracked `tests/*.sig`, a document in `tests/`, a test kernel in `tools/` | A1 | open |
| REPO-17 | L | Missing CI jobs: nightly, Debug, fuzz, link check, CodeQL; two shipped targets not in CI | A4 | open |
| REPO-18 | L | Requirements not hash-pinned, unpinned apt, one PostgreSQL image pinned by tag | A4 | open |
| REPO-19 | L | Timing-based assertions (sleeps of 5 to 650 ms) | A3 | open |
| REPO-20 | L | Release inconsistencies: a nonexistent `master` trigger, the gate reads only the PG18 report, naming | A4 | open |
| REPO-21 | M | `sig fmt --check` fails for all 16 RAMSQL `.sig` files on `main` and 24 of 39 shipped ZPM SQL, db and platform files at `2f060c2` | A4 | open |
| REPO-22 | M | Scratch sprawl: about 430 ad-hoc scripts, PR bodies, tarballs and logs in `.sig-cache/`, 247 MB in `.agents/`, no convention | A1 | open |

---

## 4. Quality bar

"Professional" is defined by measurable targets. CI or the checkpoint review
checks each one.

| Id | Target | Measure | By |
| --- | --- | --- | --- |
| Q1 | No open critical or high finding except features scheduled in C8, C9 and B11 | register | CP2 |
| Q2 | Every ZPM suite relevant to RAMSQL runs in RAMSQL CI, or is excluded with a written reason | manifest check | CP1 |
| Q3 | `main` protected; required checks per §11; publish needs approval | GitHub ruleset | CP0 |
| Q4 | `sig fmt --check` clean in both repos; ruff clean for Python | L0 gate | CP1 |
| Q5 | Every module has a page with invariants, tests and findings | doc-presence check | CP1 |
| Q6 | Every parser of untrusted input fuzzed nightly, with at least 1 CPU-hour per target per night and no open crash | fuzz dashboard | CP2 |
| Q7 | Tier-1 formal properties (FV-01 to FV-10) proved and linked | `formal/README.md` status | CP2 |
| Q8 | Line coverage at least 90% for parsers, SCRAM, durable log, MVCC and locks; at least 75% elsewhere | kcov on Linux | CP2 |
| Q9 | Differential: zero unexplained divergences on the PostgreSQL regress subset and sqllogictest corpus for supported features | B7 report | CP2 |
| Q10 | Crash and fault: every WAL write boundary crash-tested; EIO, ENOSPC, short write and fsync failure each tested | B9 report | CP2 |
| Q11 | No raw evidence in git; every README performance claim links one result summary | A2 check | CP1 |
| Q12 | One license statement everywhere, checked in CI | A4 check | CP0 |
| Q13 | No function over about 150 lines added; `server.sig` split | review, A6 | CP1 |
| Q14 | Benchmarks reproducible from tracked sources plus a pinned external harness hash | C6 | CP2 |

---

## 5. Target repository tree

The full migration map is in [docs/architecture/REPO_LAYOUT.md](docs/architecture/REPO_LAYOUT.md).
In short:

```
ramsql/
  src/                     product code; server.sig split into src/server/*.sig (A6)
  tests/
    contracts/             Sig contract executables + manifest of ZPM suites (A3)
    property/  model/      seeded property and model-based tests (B7, C1–C5)
    fuzz/                  fuzz drivers, small seed corpora (B1, B2)
    integration/           pytest suites driving the binary (A3)
    clients/               driver and ORM matrix (B7, B11)
    differential/          PostgreSQL regress subset, sqllogictest, generators (B7)
    simulation/  crash/  soak/   (B8, B9)
  python/ramsql_testkit/   shared server, wire, binary and reference helpers (A3)
  bench/                   harness, workloads, profiling (C6, C7)
  formal/                  lean/, tla/, vectors/ (C1–C5)
  release/                 packaging, release matrix, gate, runtime preparation (A1)
  tools/create_verifier.py user-facing helper only
  docs/                    architecture, modules, verification, benchmarks, process,
                           compatibility, plans, archive, adr
  changes/unreleased/      changelog fragments
  .github/                 workflows, CODEOWNERS, templates, dependabot
  README.md CHANGELOG.md ROADMAP.md LICENSE NOTICE SECURITY.md CONTRIBUTING.md
  CODE_OF_CONDUCT.md AGENTS.md PLAN.md pyproject.toml .editorconfig .gitattributes
```

Moved out of the repository: `ramsql.com/` (own repository) and raw benchmark
evidence (evidence store). Nothing is deleted without maintainer approval.

---

## 6. Execution model

### 6.1 Waves, workflows and lanes

```mermaid
flowchart LR
  subgraph W0["Wave 0: stop the bleeding"]
    B1h["B1 hotfix NET-01"]; B9h0["B9 hotfix STO-01"]; B8h0["B8 hotfix STO-03"]; A0a["A0 merge in-flight PRs"]; A4a["A4 protection + one-time fmt"]; B5a["B5 SRV-01"]
  end
  subgraph W1["Wave 1: foundation"]
    A1; A2; A3; A5; A6; B1; B2; B5; B7h["B7 differential harness"]; B9h["B9 fault seam"]; C1s["C1 state models"]; C6
  end
  subgraph W2["Wave 2: hardening"]
    B3; B6; B7; B8; B9; B10; C2; C3; C4; C5; C1p["C1 prove + link"]
  end
  subgraph W3["Wave 3: capability"]
    B4; B11; C7; C8; C9
  end
  W0 -->|CP0| W1 -->|CP1| W2 -->|CP2| W3 -->|CP3| RC["Release candidate"]
```

No dates are promised; a wave ends when its checkpoint criteria hold (§10).

### 6.2 Roles inside a lane

| Role | Count | Context | Responsibility |
| --- | --- | --- | --- |
| Lead implementer | 1 | lane brief, module pages, code | owns the branch, writes code, keeps the module page current |
| Test author | 1 | lane brief and module page only, **not** the implementation plan | writes failing tests from invariants and findings first |
| Specialist | 0–2 | as needed | fuzzing engineer, formal modeler, performance engineer, protocol expert |
| Adversarial reviewer | 1 | fresh context: diff and module page, never the implementer's summary | runs the checklist in §14.3 |
| Verifier | 1 | PR description only | re-runs every evidence command and attaches exit codes |
| Integrator (program-wide) | 1 | everything | owns `int/hardening`, pins, `build.sig.zon`, checkpoint runs |

A lane brief (§14.1) is the only context a fresh agent gets. Reviewers and
verifiers must never be the agent that wrote the code.

### 6.3 Parallelism budget

Running every lane at once would spend tokens faster than they can be
reviewed. Keep at most **10 lanes active** at a time and at most **4 agents
per lane**. Within a wave, start lanes in the order listed and start the next
lane when one finishes.

### 6.4 File ownership

Two lanes never write the same file in the same wave. "Handoff" means ownership
moves to the next lane at the wave boundary.

| Path | W0 | W1 | W2 | W3 |
| --- | --- | --- | --- | --- |
| RAMSQL `build.sig.zon`, `zpm.pkg.zon` | A0 | A0 | A0 | A0 |
| RAMSQL `build.sig` | A0 | A3 (append-only registration for others) | A3 | A3 |
| RAMSQL `.github/**` | A4 | A4 | A4 | A4 |
| RAMSQL root docs, `docs/compatibility|plans|archive`, LICENSE, NOTICE | A5 | A5 | A5 | A5 |
| RAMSQL `tools/**` → new homes | – | A1 moves first, then A3 (tests), C6 (bench), A1 (release) | same | same |
| RAMSQL `benchmarks/**` | – | A2 | A2 / C6 | C6 |
| RAMSQL `src/server.sig` → `src/server/*` | – | A6 | B5 (`options`, `auth`), B6 (`admission`, `reactor`, `log`) | B3 (`tls`), B4 (`roles`), C7 (`stats`) |
| RAMSQL `src/main.sig`, `capacity.sig`, `timezones.sig` | – | A6 | A6, C5 (capacity tests only) | – |
| RAMSQL `src/persistence.sig`, `backup.sig` | B5 | B5 | B9 | C8 |
| RAMSQL `formal/**` | – | C1 | C1–C5 by subdirectory | same |
| ZPM `src/db/postgres.sig`, `pg_types.sig`, `pg_copy.sig` | B1 | B1 | B3 (startup/TLS section) after B1 merges | B4 (startup role mapping) |
| ZPM `src/db/mysql.sig`, `tds.sig` | B2 (NET-01 TDS part) | B2 | B2 | B2 |
| ZPM `src/db/scram.sig` | – | B5 (SASLprep) | B3 (channel binding) | – |
| ZPM `src/db/store.sig`, `sql/mvcc.sig`, `sql/locks.sig` | B8 (STO-03 only) | – | B8 | B4 (session user), C9 |
| ZPM `src/db/durable_log.sig`, `platform/db_file.sig`, `sql/change_log.sig` | B9 (STO-01 only) | B9 (fault seam only) | B9 | C8 |
| ZPM front end (`lexer`, `parser`, `ast`, `binder`, `value`, `types`, `numeric`, `float_text`, `temporal`, `tzif`, `jsonb`, `expr`, `deparse`, `datetime_format`) | – | – | B7 | B11, B4 (`binder` privilege checks) |
| ZPM `sql/exec.sig`, `sort.sig`, `dml.sig` | – | – | B10 | C9 |
| ZPM `sql/storage.sig`, `sql.sig`, `catalog.sig`, `sequence.sig` | – | – | B10 (route flag only in `sql.sig`) | C9, B4 (`catalog`) |
| ZPM `platform/tcp_server.sig` | – | – | B6 | – |
| ZPM `tests/test_*.sig` | owner of the module under test | | | |

### 6.5 Orchestration recipe

Each wave can run as manual sessions, Kiro specs or a Claude Code workflow.
The recipe is the same in each case:

```
for each wave W:
  integrator: create/refresh int/hardening (both repos); record baseline
  for each lane L in W (respecting §6.3 budget), in parallel:
    create worktrees per GIT_WORKFLOW §2
    test-author(L)  → failing tests + invariants on L's branch
    implementer(L)  → code until tests pass; updates module page
    reviewer(L)     → adversarial checklist §14.3, fresh context
    verifier(L)     → re-run evidence commands, attach exit codes
    loop implementer/reviewer until approve, max 3 rounds; then escalate to maintainer
    integrator      → merge into int/hardening, re-pin ZPM, run full gate
  checkpoint CP(W): integrator runs §10 criteria; maintainer approves merge to main
```

When the orchestration is a script, each lane is one pipeline:
`test_author → implementer → (reviewer ∥ verifier) → integrator`. The lanes of a
wave run in parallel, with worktree isolation per agent.

### 6.6 Lane roster per wave

Agents are counted per lane as implementer + test author + specialists +
reviewer + verifier. "Waits for" lists hard dependencies inside the wave.

| Wave | Workflow | Lane | Agents | Repos | Waits for |
| --- | --- | --- | --- | --- | --- |
| 0 | A | A0 integrator (merge #30, ZPM #81 → #36, ZPM #77 → #34; baseline) | 2 | both | – |
| 0 | A | A4.1 rulesets, A4.2 one-time format commit | 2 | both | A0 merges (format last) |
| 0 | B | B1.0 NET-01 PostgreSQL hotfix | 4 | ZPM, RAMSQL | – |
| 0 | B | B2.0 NET-01 TDS hotfix | 3 | ZPM | – |
| 0 | B | B9.0 STO-01 replay hotfix | 4 | ZPM | – |
| 0 | B | B8.0 STO-03 lock-list hotfix | 4 | ZPM | – |
| 0 | B | B5.0 SRV-01 data-file creation | 3 | RAMSQL | – |
| 1 | A | A1 layout (move PR first, short freeze) | 3 | RAMSQL | CP0 |
| 1 | A | A2 evidence | 3 | RAMSQL, evidence store | A1.1, D2 |
| 1 | A | A3 test harness | 4 | RAMSQL | A1.1 |
| 1 | A | A4 remaining CI work | 3 | RAMSQL, ZPM | A1.1 |
| 1 | A | A5 docs and governance | 3 | RAMSQL | A1.1, D4 |
| 1 | A | A6 server split | 4 | RAMSQL | A4.2 |
| 1 | B | B1 PostgreSQL wire (B1.1 to B1.9) | 5 | ZPM, RAMSQL | B1.0 |
| 1 | B | B2 MySQL and TDS | 4 | ZPM | D5 |
| 1 | B | B5 credentials and SASLprep | 3 | RAMSQL, ZPM | A6.2 for `options.sig` |
| 1 | B | B7.1 and B7.2 differential harness and corpora | 4 | RAMSQL | A3.1 |
| 1 | B | B9.1 fault seam | 3 | ZPM | – |
| 1 | C | C1.1 to C1.3 Lean bootstrap, statements, vectors | 4 | RAMSQL | – |
| 1 | C | C6 benchmark harness | 3 | RAMSQL | A1.1, A2.1 |
| 2 | B | B3 TLS | 5 | ZPM, RAMSQL | B1 merged, D6 |
| 2 | B | B6 DoS, timeouts, logging | 4 | RAMSQL, ZPM | A6 |
| 2 | B | B7.3 to B7.10 semantic fixes and clients | 5 | ZPM, RAMSQL | B7.1 |
| 2 | B | B8 simulation and MVCC fixes | 5 | ZPM | B8.0 |
| 2 | B | B9.2 to B9.7 crash, power loss, durability fixes | 5 | ZPM, RAMSQL | B9.1 |
| 2 | B | B10 route equivalence | 4 | ZPM | – |
| 2 | C | C1.4 proofs | 3 | RAMSQL | C1.3 |
| 2 | C | C2 TLA+ concurrency | 3 | RAMSQL | B8.3 for linking |
| 2 | C | C3 durability models | 3 | RAMSQL | B9.2 for linking |
| 2 | C | C4 protocol models | 3 | RAMSQL, ZPM (test only) | – |
| 2 | C | C5 capacity and ledger | 3 | RAMSQL | A6 |
| 3 | B | B4 roles and authorization | 5 | ZPM, RAMSQL | CP2 |
| 3 | B | B11 application compatibility (sliced) | 4 per slice | ZPM, RAMSQL | B7 handover |
| 3 | C | C7 performance gates and observability | 3 | RAMSQL | B6 |
| 3 | C | C8 checkpoint and WAL lifecycle | 5 | ZPM, RAMSQL | B9 handover, C3 extension |
| 3 | C | C9 compact storage, streaming, indexes (sliced) | 5 per slice | ZPM | B10 handover |

Wave 1 lists 13 lanes, which is more than the parallelism budget allows. Start
A1, A6, B1, B9.1, C1, A3 and A4 first, then the rest as lanes finish. Wave 2
lists 11 lanes; start B8, B9, B7 and B10 first, because the C lanes link to
their harnesses.

---

## 7. Workflow A — Foundation

Goal: one source of truth, a legible tree, enforced gates, and a testable
server. Workflow A starts first. Its moves and formatting must land before
other lanes branch, or every lane will fight merge conflicts.

### A0 — Baseline and integration debt (integrator)

| | |
| --- | --- |
| Repos and branches | RAMSQL and ZPM: `int/hardening`, `int/ramsql-hardening`; the integrator merges PRs |
| Owns | `build.sig.zon`, `zpm.pkg.zon`, merge order, checkpoint runs, `docs/status/BASELINE.md` |
| Findings | INT-01 to INT-05, TST-03 |
| Agents | integrator, verifier |
| Size | M |

Tasks:
- [ ] A0.1 Create integration branches from `main` in both repos.
- [ ] A0.2 Merge order:
  1. RAMSQL #30 (evidence, add-only; or re-home its evidence per A2 first if
     A2 is ready).
  2. ZPM #81, then RAMSQL #36 (re-pin to the merge commit, rerun all checks).
  3. Rebase ZPM #77 on main plus #81 and resolve the `build.sig` contract list,
     then merge RAMSQL #34, regenerate the hash and requalify.
- [ ] A0.3 Decide sparse-factor (§17 D7). Then either integrate behind the
      route-equivalence gate (B10) or revert it from RAMSQL history with a
      clear commit.
- [ ] A0.4 Fix or quarantine ZPM `native-sig` with an issue and an owner. Red
      checks are never normal.
- [ ] A0.5 Record the baseline in `docs/status/BASELINE.md`:
  - contract suites with assertion counts
  - Python suites with pass/fail
  - PostgreSQL 16/18 qualification
  - dbMeter
  - binary hashes
- [ ] A0.6 After maintainer approval, prune merged branches, stale worktrees
      and the detached `native-fix-*` worktrees.

Acceptance: all three open PRs merged or closed with a reason; one ZPM commit
contains all merged SQL work; baseline file committed; no red check on `main`
of either repo.

### A1 — Repository layout and hygiene

| | |
| --- | --- |
| Branch | `a1/layout` |
| Owns | moves listed in REPO_LAYOUT.md, `.gitignore`, `.gitattributes`, `.editorconfig`, `release/**` |
| Findings | REPO-11, REPO-12 (with A0 for zon files), REPO-15, REPO-16, REPO-22 |
| Depends on | A0.2 done (avoid moving files under open PRs) |
| Agents | implementer, reviewer, verifier |
| Size | M |

Tasks:
- [ ] A1.1 Announce a short freeze on RAMSQL `tools/` and `tests/`. Land one
      move-only PR (`git mv`, no content edits) following the migration map.
      Update every path reference (`package_release.py` DOCUMENTS, CI paths,
      README commands) in a second PR.
- [ ] A1.2 Add `.agents/`, `.worktrees/`, `.scratch/` to `.gitignore`. Adopt the
      `.scratch/<lane>/` convention (GIT_WORKFLOW §7).
- [ ] A1.3 Extract `ramsql.com/` to its own repository with history
      (`git subtree split`), after maintainer approval (§17 D3). Remove it in
      one commit.
- [ ] A1.4 Narrow `build.sig.zon .paths` to build inputs (the integrator
      applies it). Set a real fingerprint. Correct `zpm.pkg.zon` (license id,
      layer, dependency spelling).
- [ ] A1.5 Delete the empty untracked `tests/test_db_store.sig` and
      `tests/test_sql_storage.sig`, with approval. Move `tests/MIGRATION-store-api.md`
      to `docs/archive/`, and `tools/test_sb0_loader.sig` to `tests/native_sb0/`.
- [ ] A1.6 Add `.editorconfig` and a root `.gitattributes` (LF for `.sig`,
      `.py`, `.md`; binary for fixtures).

Acceptance: tree matches REPO_LAYOUT.md; CI green after moves; `git status`
clean after a full local build and test run.

### A2 — Evidence relocation

| | |
| --- | --- |
| Branch | `a2/evidence` |
| Owns | `benchmarks/**` → `docs/benchmarks/results/**` and the evidence store |
| Findings | REPO-10 |
| Depends on | §17 D2 (evidence store location) |
| Size | M |

Tasks:
- [ ] A2.1 Build `bench/evidence/publish.py`. It uploads a campaign directory to
      the evidence store, writes `manifest.json` (path, SHA-256, size, URL), and
      verifies the upload by downloading it and comparing hashes.
- [ ] A2.2 For each of the 56 dated campaigns:
  - publish the raw files;
  - write `docs/benchmarks/results/<date>-<slug>.md` (at most about 200 lines,
    following METHODOLOGY §7) and keep `results.json`;
  - replace copied `source/` scripts with the source commit SHA.
- [ ] A2.3 Rewrite `benchmarks/README.md` as an index of result summaries,
      including losses.
- [ ] A2.4 Point every README performance claim to exactly one result summary.
- [ ] A2.5 Add a CI check that rejects new files over 200 KB, or any `*.log`,
      `*timeline.csv` or `*.jsonl`, under `docs/benchmarks/results/`.

Acceptance: `benchmarks/` gone or reduced to an index; every old link resolves;
manifests verify; repository working tree below 5 MB excluding history.

### A3 — Test harness unification

| | |
| --- | --- |
| Branch | `a3/test-harness` |
| Owns | `tests/integration/**`, `tests/contracts/manifest*`, `python/ramsql_testkit/**`, `pyproject.toml`, `build.sig` test registration |
| Findings | REPO-08, REPO-09, REPO-19, TST-01, TST-02, SRV-20 |
| Depends on | A1.1 |
| Agents | implementer, test author (migration parity), reviewer, verifier |
| Size | L |

Tasks:
- [ ] A3.1 Create `python/ramsql_testkit` with:
  - binary discovery (one function replacing 20 copies);
  - a `Server` context manager that binds to port 0, waits on a readiness
    line, captures logs and kills on teardown;
  - a minimal PostgreSQL wire client (moved from `test_server.py`);
  - the Docker reference-engine helper with digest pins;
  - assertion helpers.
- [ ] A3.2 Port the 13 integration scripts and 7 PostgreSQL differential
      scripts to pytest:
  - markers: `slow`, `durable`, `pg16`, `pg18`, `workers`, `native_sb0`;
  - JUnit XML output;
  - **parity check**: each ported test asserts at least as many conditions
    as the script it replaces. The test author records per-file counts
    before and after.
- [ ] A3.3 Replace fixed sleeps with readiness and condition waits with
      explicit timeouts (REPO-19).
- [ ] A3.4 Replace the hard-coded ZPM suite list in `build.sig`:
  - add a manifest `tests/contracts/zpm_suites.zon`, listing every ZPM
    suite as `run` or `excluded: <reason>`;
  - add a check that fails when the pinned ZPM adds a suite that is not
    listed;
  - start by running the 36 missing SQL and runtime suites (TST-01).
- [ ] A3.5 Normalize suite reports:
  - accept `pass` and `passed` and the plain-text form;
  - record minimum assertion counts per suite in the manifest;
  - fail when a suite reports fewer assertions than its minimum, or none.
- [ ] A3.6 Split `sig build test` into named groups (`test-engine`,
      `test-wire`, `test-durability`, `test-runtime`) so CI can shard them.
  - Document the `-j` setting per host memory (TST-02).
  - Make `runBinary` report the real exit status instead of
    `error.BufferTooSmall`.
- [ ] A3.7 Smoke-run every benchmark, compare and profile script on tiny
      inputs (`bench-smoke`, REPO-09).

Acceptance: `pytest -m "not slow"` passes locally and in CI. All relevant ZPM
suites run or are excluded with a reason. The assertion floor is enforced, and
no integration test uses fixed sleeps.

### A4 — CI/CD and supply chain

| | |
| --- | --- |
| Branch | `a4/ci` |
| Owns | `.github/**`, `tools/ci/**` |
| Findings | REPO-01, 05, 06, 07, 17, 18, 20, 21, SQLF-12 (formatting part), TST-03 (with A0) |
| Agents | implementer, reviewer (supply chain), verifier |
| Size | M |

Tasks:
- [ ] A4.1 Wave 0, needs a maintainer with admin rights:
  - add a ruleset on `main` and `int/*` in both repositories requiring the
    checks in §11;
  - block force-push and deletion;
  - require a linear history on `int/*`;
  - turn on `delete_branch_on_merge`.
- [ ] A4.2 Wave 0, right after A0.2: one formatting-only commit per repository
      (`sig fmt src tests build.sig`), plus `.git-blame-ignore-revs`. All lanes
      branch after it.
- [ ] A4.3 Pin every action by commit SHA (with the tag in a comment). Add
      dependabot for actions and pip, and `pip install --require-hashes`.
- [ ] A4.4 Add a `release` environment with required reviewers and tag
      protection. Run `release_gate.py` for prereleases too, with a
      "prerelease" gate profile that is recorded, not skipped. Remove the
      `master` trigger. Make the gate read both the PG16 and PG18 reports.
- [ ] A4.5 CI efficiency:
  - add `concurrency` cancellation;
  - cache the Sig archive by SHA-256 and the pip wheels;
  - stop `release.yml` from re-running `ci.yml` on PRs (run it in the merge
    queue or on a label instead).
- [ ] A4.6 New jobs, per §11:
  - `lint`: `sig fmt --check`, ruff, actionlint, zizmor;
  - `docs-consistency`: lychee link check; version agreement across
    `build.sig.zon`, `root.sig`, `zpm.pkg.zon`, the RELEASE heading and the
    CHANGELOG; license string matches LICENSE; every module page exists;
  - `supply-chain`: dependency-review;
  - `nightly`: Debug build, full benchmarks smoke, long fuzz, large runtime;
  - add `macos-15-intel` and `windows-11-arm` to the test matrix.
- [ ] A4.7 Forbidden-pattern lint (L0): it flags new uses of heap allocators,
      `catch unreachable`, and `@panic` in `src/server/**` and ZPM `src/db/**`.
      Today's uses are listed as a baseline, so only new ones fail.

Acceptance: a PR without green required checks cannot merge; every action
SHA-pinned; formatting check required; nightly workflow green.

### A5 — Documentation, governance and license

| | |
| --- | --- |
| Branch | `a5/docs-governance` |
| Owns | `README.md`, `ROADMAP.md`, `CHANGELOG.md` process (`changes/`), `LICENSE`, `NOTICE`, `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `.github/CODEOWNERS` and templates, `docs/compatibility|plans|archive|adr/**`, `SQL_REFERENCE.md`, `CAPACITY.md`, `OPERATIONS.md` |
| Findings | REPO-02, 03, 04, 13, 14, SRV-19, INT-02, INT-05 |
| Depends on | §17 D1 (NET-01 disclosure), D4 (license) |
| Agents | implementer, reviewer (accuracy against code), verifier |
| Size | M |

Tasks:
- [ ] A5.1 License: remove "MIT License — SB0 LTD." from README, set the
      `zpm.pkg.zon` license to the LICENSE identifier (through A0), and add a
      NOTICE explaining that earlier tags were MIT (wording approved by the
      maintainer).
- [ ] A5.2 `SECURITY.md`: a private disclosure address, supported versions and
      response targets. Publish NET-01 details only after the fix ships.
- [ ] A5.3 Add `CONTRIBUTING.md` (DCO or CLA per LICENSE §6, the Definition of
      Done, how to run tests), `CODE_OF_CONDUCT.md`, `CODEOWNERS` (lane owners
      per path), and PR and issue templates. The PR template embeds the DoD
      checklist.
- [ ] A5.4 Docs truth pass. Fix:
  - `RELEASE.md` heading (generated from the CHANGELOG section);
  - `VALIDATION.md` "alpha.3";
  - `INFRASTRUCTURE_PROGRESS.md` "alpha.4";
  - `GAP_CLOSING_PLAN.md` dbMeter status;
  - help text and `OPERATIONS.md` "SQL serialized" and "one reactor".

  Then refresh `release-gates.json` evidence links.
- [ ] A5.5 Consolidate `compatibility/*.md`:
  - **current plans** → `docs/plans/`: MACHINE_CAPACITY, EXECUTION_CONTROL,
    IMPLEMENTATION;
  - **history** → `docs/archive/`: GAP_CLOSING_PLAN, DEEP_INFRASTRUCTURE_RESEARCH,
    INFRASTRUCTURE_PROGRESS, REVIEWER_BETA_2, BETA_USABILITY;
  - **machine-read reports** → `docs/compatibility/`;
  - leave a one-line pointer at each old path for one release.
- [ ] A5.6 Shorten README to about 150 lines:
  - what it is, quick start, compatibility table, honest limits;
  - links to capacity, operations, performance results and this plan;
  - move the long measured-performance prose into result summaries.
- [ ] A5.7 Fix website claims in the extracted site repository: numbers come
      from a cited `results.json`, and the isolation wording matches README.
- [ ] A5.8 Add ADRs for decisions already taken:
  - 0001 engine in ZPM
  - 0002 allocator-free
  - 0003 READ COMMITTED first
  - 0004 WAL fail-closed
  - 0005 PostgreSQL as reference

  Add new ADRs as §17 decisions are made.
- [ ] A5.9 Move user-facing docs into `docs/`, keeping root `README.md`.
      Keep `docs/modules/README.md` current.

Acceptance: `docs-consistency` job green; one license statement; no document
claims a version or result that contradicts the code or evidence.

### A6 — Server decomposition and testability

| | |
| --- | --- |
| Branch | `a6/server-split` |
| Owns | `src/server.sig` → `src/server/{options,capabilities,memory,client,admission,reactor,auth}.sig` and `src/server.sig` (orchestration); `src/main.sig`; `src/capacity.sig`; `src/timezones.sig`; `tests/contracts/server_*.sig` |
| Findings | SRV-02, 03, 05, 09, 10, 12, 13, 14, 16, 18, 21 |
| Depends on | A4.2 (formatting commit) |
| Agents | implementer, test author, reviewer, verifier |
| Size | L |

Tasks:
- [ ] A6.1 **Pin behavior first.** Before any move, the test author extends the
      integration suite to cover the behaviors the split could break:
  - admission errors 53200 and 53300;
  - slot reuse with cancel keys;
  - idle timeout;
  - parked wake on commit, timeout and cancel;
  - worker fallback;
  - `--capabilities` JSON (golden file).
- [ ] A6.2 Move-only split into `src/server/*.sig`, with no behavior change.
      Gather the 28 globals into a `Server` struct passed explicitly.
- [ ] A6.3 Inject socket, clock and file operations through a small interface
      (comptime `anytype`), so in-process tests can drive the reactor with fake
      sockets and a fake clock.
- [ ] A6.4 One `Reactor` for 1 to 16 workers. Replace the `tryLock`-only wake
      scan with a bounded wait or a retry that guarantees deadline delivery
      (SRV-03). Add a test showing statement_timeout fires within 2x its value
      under sustained write contention.
- [ ] A6.5 Derive client, transaction and select limits from one constant with
      `comptime` asserts. Match the worker charge to the real stack size (SRV-05).
- [ ] A6.6 `parseArgs(argv) !Options`:
  - pure, table-driven and fuzzable;
  - rejects repeated flags and incompatible combinations (SRV-14);
  - one shared `validate` (SRV-13).
- [ ] A6.7 Struct-driven JSON writer for `--capabilities` and
      `--machine-capacity` (SRV-10), checked against the golden file.
- [ ] A6.8 Remove dead cancel paths (SRV-12). Cap `capacity.sample` (SRV-16).
      Move TZif loading outside the global write lock (SRV-18).
- [ ] A6.9 Add a `sig build check-sb0` step that cross-compiles
      `platform/sb0_app.sig` (SRV-09).
- [ ] A6.10 Write an ADR on `@panic` for region-release failures (SRV-21): keep
      fail-stop, but log it, and document it in OPERATIONS.

Acceptance:
- no file in `src/server/` over 400 lines, and no function over about 150 lines;
- in-process tests cover admission, slot lifecycle, ledger, cancel and
  timeouts;
- all integration tests and the golden capabilities file unchanged;
- `check-sb0` green.

---

## 8. Workflow B — Security and correctness hardening

Goal: no input from a client, a file or the command line can corrupt data,
crash the server or bypass authentication, and every semantic claim is
checked against PostgreSQL.

### B1 — PostgreSQL wire protocol and parameter binding

| | |
| --- | --- |
| Repos and branches | ZPM `b1/pg-wire`, RAMSQL `b1/pg-wire` |
| Owns | ZPM `src/db/postgres.sig`, `pg_types.sig`, `pg_copy.sig`, `tests/test_postgres*.sig`, `tests/test_pg_*.sig`, `tests/fuzz/pg_*`; RAMSQL `tests/integration/test_pg_*.py` |
| Findings | NET-01, 02, 03, 06, 08, 10, 11, 13, 15, 16, 18, 19 |
| Formal | FV-03, FV-10, FV-17 (with C1 and C4) |
| Agents | implementer, test author, fuzzing engineer, security reviewer, verifier |
| Size | L |

Tasks:
- [ ] **B1.0 Wave 0 hotfix for NET-01.**
  - Render every spliced literal as a self-delimiting token: parenthesize
    numeric literals and surround each substitution with spaces, so
    `bal-$1` becomes `bal- (-5) `.
  - Add regression tests for:
    - `-$1`, `$1--`, `$1/*` and `/* ' */ $1`, with integer, numeric and float
      parameters in text and binary formats;
    - the TDS `@p` equivalents (with B2).
  - Add a RAMSQL integration test through psycopg prepared statements and
    JDBC that proves `UPDATE ... SET x = x-$1 WHERE id = $2` changes one row.
  - The maintainer decides on a patch release (§17 D1).
- [ ] B1.1 Replace text substitution with typed binding. The parser produces
      parameter nodes, and Bind supplies typed values to the executor. The
      `literal()` text path remains only for logging. Then FV-03 becomes "the
      bound value equals the decoded parameter".
- [ ] B1.2 Find placeholders with the ZPM `Tokenizer` instead of the hand-written
      `scan` (NET-02).
- [ ] B1.3 Close non-holdable named portals at transaction end, returning 34000
      on reuse. Validate the result-format count at Bind, before any execution
      (NET-03, NET-06).
- [ ] B1.4 Support unknown (oid 0) parameter types through binder inference for
      `SELECT $1`, expressions and functions (NET-08).
- [ ] B1.5 Error hygiene. Map every internal error to a SQLSTATE and a fixed
      message. Never send `@errorName` (NET-11). Startup errors use FATAL.
      Add NegotiateProtocolVersion. Compare `client_encoding` case-insensitively
      (NET-10, NET-15).
- [ ] B1.6 Validate UTF-8 on SQL text, parameters and COPY input. Reject NUL in
      text (NET-13).
- [ ] B1.7 Make `stream_server` accept `consumed == 0` with `written > 0`, or
      change the COPY error path (NET-16).
- [ ] B1.8 Fuzzing (NET-18). Build structure-aware drivers for:
  - `Session.handle` in four phases (pre-startup, SCRAM pending,
    authenticated, COPY-in), with a mock DB that asserts nothing executes
    before authentication;
  - `pg_copy` and `pg_types` with round-trip oracles;
  - template substitution with a token-stream equivalence oracle.

  All drivers check chunking invariance (the same output however the input
  is split). Seed corpora come from real psql, psycopg and JDBC captures.
  Run 5 minutes per PR and 1 hour per target nightly.
- [ ] B1.9 Add a dedicated `test_pg_types.sig` with send/receive round trips
      for every OID (NET-19).

Acceptance:
- NET-01 regression tests pass in both repositories;
- the token-equivalence fuzz oracle runs nightly with no findings;
- every PostgreSQL client in the L6 matrix passes;
- `postgres` suite assertions only grow.

### B2 — MySQL and TDS hardening

| | |
| --- | --- |
| Branches | ZPM `b2/mysql-tds`, RAMSQL `b2/mysql-tds` |
| Owns | ZPM `src/db/mysql.sig`, `tds.sig`, their tests and fuzz drivers |
| Findings | NET-01 (TDS `expand`), NET-05 (credential part), NET-07, NET-12, NET-13, NET-14, NET-18 |
| Depends on | §17 D5 |
| Size | M (keep experimental) to L (authenticate) |

Tasks:
- [ ] B2.0 Wave 0: apply the NET-01 fix to TDS `expand` with tests.
- [ ] B2.1 Replace `unreachable` and unchecked `@intCast` on wire-reachable paths
      with errors (NET-07). Add a test with every cell variant.
- [ ] B2.2 Randomize the MySQL scramble from `entropy` (NET-12).
- [ ] B2.3 Choose a policy (D5). If authenticated:
  - MySQL `caching_sha2_password` over TLS;
  - TDS SQL authentication over TLS (after B3).

  Otherwise, refuse to start MySQL or TDS together with a durable data file,
  and print a warning banner.
- [ ] B2.4 TDS conformance (NET-14): accept fExtension and RESETCONNECTION,
      use correct NVARCHAR lengths, reject unsupported isolation levels
      instead of ignoring them, and make ATTENTION cancel parked statements.
- [ ] B2.5 Fuzz drivers covering:
  - LOGIN7 offsets, packet reassembly and PLP chunks for TDS;
  - handshake-response lenenc and attributes for MySQL.

  Clients: add `mysql-connector-python`, `pyodbc` (ODBC Driver 18), and Go
  `go-mssqldb` (L6).

Acceptance: no `unreachable` on wire-reachable paths; fuzzers nightly with no
findings; policy implemented and documented in THREAT_MODEL.

### B3 — TLS and channel binding

| | |
| --- | --- |
| Branches | ZPM `b3/tls`, RAMSQL `b3/tls` |
| Owns | a new ZPM module (`src/db/tls_session.sig` or reuse of the ZPM transport TLS 1.3 code after audit), the startup section of `postgres.sig` (after B1 merges), `scram.sig` channel binding; RAMSQL `src/server/tls.sig` and options |
| Findings | NET-05 (transport part); closes gate `transport_security` |
| Depends on | §17 D6, B1 wave-1 merge |
| Agents | implementer, cryptography reviewer, test author, verifier |
| Size | XL |

Tasks:
- [ ] B3.1 Audit the existing ZPM TLS 1.3 code (QUIC/mesh transport) for
      server-side use: allocator-free, constant-time primitives, X.509 chain
      handling, record layer. Write an ADR choosing reuse or a new minimal
      TLS 1.3 server.
- [ ] B3.2 Respond to PostgreSQL `SSLRequest` with S, then TLS 1.3 only
      (AES-GCM, ChaCha20-Poly1305), using a certificate and key from files
      with permission checks. Add `--tls-cert`, `--tls-key` and
      `--require-tls`. Default to `--require-tls` whenever the bind address is
      not loopback.
- [ ] B3.3 SCRAM-SHA-256-PLUS with `tls-server-end-point` channel binding.
      Advertise PLUS only when TLS is active.
- [ ] B3.4 Tests:
  - libpq `sslmode=verify-full` against a test CA;
  - psycopg, JDBC (`ssl=true`), pgx;
  - channel binding required (`channel_binding=require`);
  - downgrade attempts (a client requesting plaintext when the server
    requires TLS);
  - truncated records, oversized records, renegotiation attempts;
  - interop with `openssl s_client`.
- [ ] B3.5 Fuzz the record layer and handshake messages.
- [ ] B3.6 Allow binding a non-loopback address only with TLS required and
      SCRAM configured.

Acceptance: `release-gates.json` `transport_security` passes with linked
evidence on all hosted OSes; the threat model's §4 items 2 and 4 are closed.

### B4 — Roles and authorization

| | |
| --- | --- |
| Branches | ZPM `b4/roles`, RAMSQL `b4/roles` |
| Owns (wave 3) | ZPM `sql/catalog.sig` (roles, ownership), new `sql/privileges.sig`, privilege checks in `binder.sig`, session user in `store.sig`, startup role mapping in `postgres.sig`; RAMSQL role bootstrap and the verifier store for several users |
| Closes | gate `authentication` (with B2 and B5) |
| Size | XL |

Tasks:
- [ ] B4.1 ADR on the scope: login roles; `CREATE ROLE`, `ALTER ROLE ... PASSWORD`
      (SCRAM verifier); table ownership; `GRANT` and `REVOKE` for
      SELECT, INSERT, UPDATE, DELETE and TRUNCATE on tables; `CREATE` on the
      schema; superuser. Out of scope: column privileges, RLS, role
      inheritance beyond direct membership.
- [ ] B4.2 Make the catalog and the WAL durable for roles and grants, with
      recovery tests.
- [ ] B4.3 The binder checks privileges for every table reference, including
      CTEs, subqueries, `RETURNING`, `COPY` and sequences. Add a deny-by-default test generator: for random
      statements and random grant sets, the statement succeeds exactly when the
      model says it should.
- [ ] B4.4 Add an `hba`-like policy file (user, source, method), with tests.
- [ ] B4.5 Catalog views drivers need (`pg_roles`, `current_user`,
      `session_user`), with B11.

Acceptance: failed credentials or missing privileges cannot read or mutate
anything (generator plus model); gate `authentication` passes.

### B5 — Credential, configuration and data-path hardening

| | |
| --- | --- |
| Branches | RAMSQL `b5/config`, ZPM `b5/sasl` |
| Owns | RAMSQL `src/persistence.sig`, `backup.sig` (until W2), `src/server/{options,auth}.sig` (after A6), `tools/create_verifier.py`; ZPM `scram.sig` (wave 1 only) |
| Findings | SRV-01, SRV-04, SRV-11 |
| Size | S–M |

Tasks:
- [ ] B5.0 Wave 0, SRV-01:
  - starting with a missing data file fails;
  - `--create-data` (or an explicit `ramsql init --data path`) creates it;
  - the server logs the absolute path it opened;
  - tests cover a typo in the path and a wrong working directory.
- [ ] B5.1 Read-only recovery for `--backup-to`. The source is never modified;
      a torn tail is reported, not truncated. Test it with a hash of the source
      before and after (SRV-04).
- [ ] B5.2 Refuse a verifier file readable by group or others (POSIX mode
      checks, Windows ACL checks), unless `--allow-insecure-verifier-permissions`
      is given. Zero secrets on every path with `defer` (SRV-11).
- [ ] B5.3 SASLprep (RFC 4013) for non-ASCII passwords in `create_verifier.py`
      and `Verifier.fromPassword`, with RFC 4013 and RFC 7677 vectors.
- [ ] B5.4 Make `--insecure-local` and durable data mutually exclusive unless
      `--i-understand-insecure-durable` is given. Policy in THREAT_MODEL §3.4.

Acceptance: all three SRV findings closed with tests; SASLprep vectors pass.

### B6 — Resource exhaustion, timeouts and logging

| | |
| --- | --- |
| Branches | RAMSQL `b6/dos`, ZPM `b6/tcp` |
| Owns (wave 2) | RAMSQL `src/server/{admission,reactor,log}.sig`; ZPM `platform/tcp_server.sig` |
| Findings | NET-04, NET-09, NET-17, SRV-06, SRV-07 |
| Depends on | A6 |
| Size | M |

Tasks:
- [ ] B6.1 Add timeouts: an authentication deadline (default 10 s), a startup
      message deadline, a per-frame progress deadline (bytes alone do not
      refresh the idle timer), and `idle_in_transaction_session_timeout`.
      Parked clients are no longer exempt.
- [ ] B6.2 Connection limits: per-source limits, reserved administrative slots,
      and an admission queue that reports its state. A queue overflow sends an
      error frame before closing, and is counted.
- [ ] B6.3 Make the cancel key unpredictable: a random 32-bit pid and a 64-bit
      secret where the protocol allows. Rate-limit failed cancels (NET-09).
- [ ] B6.4 Structured logging to stderr with a bounded ring buffer and no
      allocation:
  - events: authentication success and failure (no secrets), admission
    rejections, invariant breaches (`InvalidReply`), WAL poisoning,
    region-release panics;
  - log levels set with `--log-level`.
- [ ] B6.5 Authentication throttling: exponential backoff per source after
      failures.
- [ ] B6.6 Set `SO_EXCLUSIVEADDRUSE` on Windows. Add a test that a second bind
      fails (NET-17).
- [ ] B6.7 Slowloris and flood harness (`tests/soak/dos_*.py`): trickle bytes,
      half-open connections, admission-queue flood, cancel flood. Oracle: a
      legitimate client still connects within 1 s.

Acceptance: the DoS harness passes on Windows and Linux; every security-relevant
event is logged; threat model §4 item 4 closed.

### B7 — SQL semantics and differential testing

| | |
| --- | --- |
| Branches | ZPM `b7/sql-semantics`, RAMSQL `b7/differential` |
| Owns (wave 2) | ZPM front-end files listed in §6.4 and their tests; RAMSQL `tests/differential/**`, `tests/clients/**` |
| Findings | SQLF-01, SQLF-02, SQLF-04 to SQLF-07, SQLF-09 to SQLF-11, SQLF-13, SQLF-15 (parser side), SQLF-12 (helper deduplication and the std-usage rule) |
| Formal | FV-01, 02, 04, 11, 12, 13, 18, 20 (with C1) |
| Agents | implementer, test author, differential engineer, reviewer, verifier |
| Size | XL |

Tasks:
- [ ] B7.1 Wave 1 harness, before any fix:
  - `tests/differential/` runs the same SQL against RAMSQL and digest-pinned
    PostgreSQL 16 and 18 in Docker;
  - session settings: `LC_COLLATE=C`, `TimeZone=UTC`, `DateStyle=ISO`,
    `IntervalStyle=postgres`, `extra_float_digits=1`;
  - compares text results and SQLSTATEs;
  - classifies each divergence as **bug**, **unsupported** (must error with
    0A000 or 42xxx, never return a value) or **documented difference**,
    recording every classification in `docs/compatibility/DIVERGENCES.md`.
- [ ] B7.2 Corpora:
  - **PostgreSQL regress subsets**, filtered to supported grammar: int2/4/8,
    numeric, float4/8, boolean, date, timestamp, interval, horology, jsonb,
    case, select, join, aggregates, union, subselect, with, window;
  - **sqllogictest** with a PostgreSQL adapter;
  - **generators**:
    - a typed literal-expression generator with boundary values;
    - SQLancer-style TLP and NoREC oracles over generated schemas;
    - a grammar fuzzer with two oracles: crash in ReleaseSafe, and
      "accepted here implies PostgreSQL accepts it with the same result".
- [ ] B7.3 Fix SQLF-01:
  - checked arithmetic (`@mulWithOverflow`, `@addWithOverflow`) on every
    temporal path;
  - range checks before multiplying;
  - restrict `date ± integer` to int2 and int4;
  - property test across the full year range;
  - the FV-12 no-overflow theorem.
- [ ] B7.4 Fix SQLF-02: implement PostgreSQL DecodeInterval for SQL-standard
      forms. Reject an unknown word after a typed literal instead of reading it
      as an alias.
- [ ] B7.5 Keep a differential regression test for SQLF-03, which is already fixed
      upstream. Fix SQLF-04 (jsonb operator resolution), SQLF-05 (22008 for infinite operands) and SQLF-09 (cap the
      stored JSONB render size, or render into a streaming buffer).
- [ ] B7.6 Add a cancellation checkpoint to the JSONB key sort, or replace it
      with an O(k log k) sort. Bound worst-case work with a test that a cancel
      arrives within 100 ms for both LIKE and JSONB (SQLF-07).
- [ ] B7.7 Measure the stack high-water mark per profile in a test. Pass cells
      by pointer in recursive evaluation. Add a comptime assert that stack use
      at `text_bytes = 65535` fits the worker stack (SQLF-06).
- [ ] B7.8 Reject the invalid SQL that is accepted today (SQLF-10). Fix the
      smaller divergences or record them as documented differences (SQLF-11).
- [ ] B7.9 Deduplicate helpers: whitespace, case and trim helpers, and integer
      writers, each kept in one module.
- [ ] B7.10 Client matrix (L6): psql 16 and 18, psycopg 3, asyncpg, JDBC 42.7,
      pgx v5, node-postgres, SQLAlchemy 2 core, Django ORM smoke, Prisma
      introspection (expected to fail, recorded as a B11 input).

Acceptance:
- zero `bug`-class divergences open on the corpora;
- every `unsupported` case errors instead of returning a value;
- SQLF findings closed;
- the semantics report moves from 141/151 to 151/151 with a larger corpus.

### B8 — Transactions and deterministic simulation

| | |
| --- | --- |
| Branches | ZPM `b8/simulation`, RAMSQL `b8/simulation` |
| Owns (wave 2) | ZPM `src/db/store.sig`, `sql/mvcc.sig`, `sql/locks.sig`, `tests/sim/**`, trace hooks behind `-Dtrace` |
| Findings | STO-02, STO-03, STO-10, STO-11, STO-14, STO-15, STO-20 (MVCC part) |
| Formal | FV-07, 08, 15, 16, 21 (with C2) |
| Agents | implementer, simulation engineer, test author, reviewer, verifier |
| Size | XL |

Tasks:
- [ ] **B8.0 Wave 0 hotfix for STO-03.**
  - Upgrade an existing holder's mode in place instead of adding a second
    entry.
  - Make `pushLock` checked so it returns `LockCapacity`.
  - Add a regression test: `FOR SHARE` then `FOR UPDATE` on more rows than
    `row_locks / 2`, in ReleaseSafe.
- [ ] B8.1 Deterministic simulator per STRATEGY §3:
  - N scripted clients drive the real PostgreSQL codec and `Store` through a
    seeded scheduler;
  - simulated clock and file;
  - parking and wake-ups, timeouts, cancels and deadlocks.
- [ ] B8.2 History recorder and checker:
  - Elle-style checks for G0, G1a, G1b and G1c under READ COMMITTED;
  - a lost-update check against PostgreSQL's EvalPlanQual behavior;
  - a cross-check of the same workload against PostgreSQL in Docker for
    blocking and outcome agreement.
- [ ] B8.3 Trace hooks (`-Dtrace=true`): snapshot taken, version read, write,
      lock wait, deadlock victim, commit publish, WAL flush, reclaim. Zero cost
      when off, checked by a size and instruction-count test.
- [ ] B8.4 Extend `test_sql_mvcc.sig` from 122 assertions with:
  - property tests for visibility (own writes, aborted versions, savepoint
    rollback, DDL in transactions);
  - reclamation while snapshots are open.
- [ ] B8.5 Nightly: 10,000 seeds with 8 to 32 clients. On failure, minimize
      the history and keep it as a regression test.
- [ ] B8.6 Fix STO-02 first:
  - keep a per-transaction `running_snap`, counted by `horizon()` for the
    whole statement and COPY lifetime;
  - write the simulator schedule that reproduces the lost update **before**
    the fix.

  Then fix:
  - STO-10: capture volatile values once per statement, or forbid resume for
    statements that contain them;
  - STO-11: take ACCESS SHARE locks on every source table;
  - STO-14: queue fairness;
  - STO-15: widen counters, or check and roll over safely.
- [ ] B8.7 Capacity failpoints. Fail the k-th call of `ensureVersions`,
      `ensureRowSlot`, `reserveWrites`, `grantRow`/`pushLock`,
      `catalog.addVersion` and `Writer.reserve`. Oracle:
  - `usage()` and `sameDatabase` are unchanged after terminal errors;
  - `WouldBlock` keeps exactly the statement's slice of work.

Acceptance:
- nightly simulation green with no anomaly;
- the trace output is consumed by C2's TLC trace check;
- MVCC suite above 5,000 assertions;
- all §3.4 concurrency findings closed.

### B9 — Durability and fault injection

| | |
| --- | --- |
| Branches | ZPM `b9/faults`, RAMSQL `b9/crash` |
| Owns | ZPM `platform/db_file.sig` (fault seam, wave 1), `db/durable_log.sig`, `sql/change_log.sig` (wave 2); RAMSQL `src/persistence.sig`, `backup.sig` (wave 2), `tests/crash/**`, `tests/soak/**` |
| Findings | SRV-08, STO-01, STO-04, STO-05, STO-06 (counter), STO-09, STO-13, STO-16, STO-18, SQLF-15 |
| Formal | FV-05, FV-05b, FV-06, FV-22 (with C3) |
| Size | L |

Tasks:
- [ ] **B9.0 Wave 0 hotfix for STO-01.** In `change_log` validate and apply:
  - accept any unused OID at or above `first_oid`;
  - set the counter to the maximum seen.

  Add a regression test that creates tables in two transactions, commits
  them out of OID order, restarts and verifies both tables. Also check that a
  WAL written by beta.2 still replays.
- [ ] B9.1 Wave 1: give `db_file` a fault seam, either a comptime file
      interface or an `anytype` file. A faulting fake injects EIO, ENOSPC,
      short write, write OK with fsync failure, and torn writes at byte
      offsets. Persistence and backup take the interface (SRV-08).
- [ ] B9.2 Crash-point enumeration:
  - for a scripted workload, crash (kill or truncate) at **every** WAL write
    boundary and at random byte offsets inside frames;
  - recover, then compare against the Lean recovery function (FV-05) and the
    set of acknowledged commits.
- [ ] B9.3 Make the two assertions that accept any error exact, and test
      `BackupSourceChanged` and `BackupVerificationFailed`.
- [ ] B9.4 Power loss (Linux): run under LazyFS (or dm-flakey/dm-log-writes)
      to drop unsynced data, with the same oracle. Feeds the
      `storage_power_failure` gate.
- [ ] B9.5 Soak test: 24 h of mixed churn under a memory budget, with client
      kills every few seconds. Oracles: byte-ledger baseline restored, WAL
      recoverable, latency tails stable.
- [ ] B9.6 Fix the durability findings from §3.4:
  - STO-04: a durable per-sequence watermark, with every commit persisting
    any log-ahead that is not yet durable;
  - STO-05: a bad record ending exactly at EOF is a torn tail and is
    truncated, while damage to synced bytes still fails closed (alternative:
    write header and payload, fsync, then trailer and fsync);
  - STO-06: an incremental live-row counter during replay;
  - STO-09: reconcile memory with disk after a persist failure, or mark the
    database read-only with a clear status;
  - STO-13: guard `applyChangeRecord`, use one slot-selection rule, and
    validate version capacity before apply;
  - STO-16: checked narrowing in ZPM, not only RAMSQL limits;
  - STO-18: on fsync failure, poison and refuse to replay in the same boot
    without operator action; verify NTFS creation durability;
  - SQLF-15: legacy replay quotes or whitelists identifiers that were valid
    when the WAL was written, with a fixture WAL per past release.
- [ ] B9.7 Replay refinement test. For random multi-connection schedules (DDL,
      DML, `nextval`, savepoints, aborts), the published state must equal
      `replay(WAL)` (compared with `sameDatabase`). This would have caught
      STO-01 and STO-04.

Acceptance:
- every boundary crash recovers to the predicted state;
- every injected fault is either survived or fails closed, with the expected
  SQLSTATE and log;
- the `storage_power_failure` gate gains linked evidence.

### B10 — Fast path against reference path

| | |
| --- | --- |
| Branch | ZPM `b10/route-equivalence` |
| Owns (wave 2) | ZPM `sql/exec.sig`, `sort.sig`, `dml.sig`; a route flag in `sql.sig`; `tests/test_sql_route_equivalence.sig` |
| Findings | SQLF-08, STO-12, STO-17, STO-19 (after route equivalence is in CI), STO-20 (fast-path and window/ROLLUP part) |
| Formal | FV-14 (with C1) |
| Size | L |

Tasks:
- [ ] B10.1 Add an execution option `route = .reference` that disables every
      specialized executor:
  - the 5 paths with existing `Config` switches (factor join, factor CTE,
    dimension-group ids, typed multi-group, block presence);
  - the 8 without one (summary states, streamed cursor, dense group,
    streamed hash grouping, PK-key speculation, layout order, integer top-k,
    top-k heap);
  - compact scans and projected filter.
- [ ] B10.1b Metamorphic oracles for the only implementations of window
      functions (`windowCore`) and ROLLUP (`groupingRollup`). For example:
  - a ROLLUP equals the UNION ALL of its grouping sets;
  - a window aggregate over `ROWS UNBOUNDED` equals a correlated subquery.
- [ ] B10.2 Run every query in every SQL suite and the B7 corpora twice, with
      the default and the reference route, and require identical results and
      errors.
- [ ] B10.3 Add a generator for the shapes each fast path claims (PK joins,
      dense keys, typed groups, NULL-heavy and skewed data), with property tests
      against the reference route.
- [ ] B10.4 Add a route-coverage report: for each fast path, the number of
      corpus queries that took it. A fast path with no coverage is either given
      tests or removed.
- [ ] B10.5 Make arithmetic dispatch use `op` rather than relying on binder
      invariants (SQLF-08).

Acceptance: route equivalence in CI on every PR that touches `exec.sig`; every
fast path has coverage at least 20 queries.

### B11 — Application-compatibility features (wave 3)

| | |
| --- | --- |
| Branches | ZPM `b11/app-compat`, RAMSQL `b11/app-compat` |
| Owns (wave 3) | ZPM front-end files (handed over from B7), catalog views with B4 |
| Findings | SQLF-14, gate `postgres_compatibility` |
| Input | `docs/compatibility/FEATURE_GAPS.md`, B7 client and ORM failures |
| Size | XL, sliced |

Tasks, one PR per slice, each with differential tests:
- [ ] B11.1 Types: `VARCHAR(n)`, `CHAR(n)`, `UUID`, `BYTEA`, `TIME`, `SERIAL`
      and `BIGSERIAL` (as sequences).
- [ ] B11.2 The most-used scalar functions, ranked by ORM and driver probe
      failures: `lower`, `upper`, `length`, `substr`, `trim`, `abs`, `floor`,
      `greatest`, `coalesce` variants, `date_trunc`, `extract`, `now`,
      `current_setting`, `version`.
- [ ] B11.3 DDL: `IF [NOT] EXISTS`, `ALTER TABLE ADD/DROP COLUMN`, `TRUNCATE`,
      `UNIQUE`, `CHECK`, composite primary keys.
- [ ] B11.4 Syntax: `E''` and `$$` strings, `IS [NOT] DISTINCT FROM`,
      `ANY`/`ALL` with arrays where supported, `UPDATE ... FROM`,
      `DELETE ... USING`, parameterized `LIMIT`/`OFFSET`, `EXPLAIN` (plan text).
- [ ] B11.5 Catalog views drivers query at connect time (`pg_type`, `pg_namespace`,
      `pg_class`, `pg_attribute`, `pg_settings`, `information_schema.tables`/`columns`).
      These are read-only and generated from the catalog.

Acceptance: every slice has a differential test and an entry in FEATURE_GAPS;
the L6 ORM smoke tests pass for SQLAlchemy core and Django basic CRUD.

---

## 9. Workflow C — Assurance, performance and scale

Goal: formal models connected to the code, reproducible performance claims, and
the storage lifecycle and layout needed for production scale.

### C1 — Lean 4 core semantics

| | |
| --- | --- |
| Branch | RAMSQL `c1/lean-core` |
| Owns | `formal/lean/**` (`Sql/*`, `Vectors`, `Oracle`), `formal/README.md`, `tests/contracts/vectors_*.sig`, `tests/property/oracle_*.py` |
| Theorems | FV-01, 02, 03, 04, 11, 12, 13, 14, 18, 20 |
| Agents | formal modeler, code-reading reviewer (model fidelity), proof reviewer, verifier |
| Size | L |

Tasks:
- [ ] C1.1 Bootstrap:
  - `lean-toolchain` pinned to an exact stable release;
  - `lakefile.lean` with Batteries;
  - CI job `formal-lean` that runs `lake build` and rejects `sorry`,
    `admit` and undeclared axioms;
  - Mathlib cache only if D9 allows it.
- [ ] C1.2 Wave 1: write the definitions and theorem statements for FV-01 to
      FV-04. The code-reading reviewer confirms model fidelity
      (FORMAL.md §4 step 1).
- [ ] C1.3 Vectors and oracle:
  - `lake exe vectors` and `lake exe oracle`;
  - Sig `vectors_*.sig` contracts;
  - a Python property driver comparing the oracle with `ramsql-oracle`.
  - Link FV-03 first: it reproduces NET-01 before the fix and guards the fix
    after.
- [ ] C1.4 Wave 2: prove FV-01 to FV-04. Then state, link and prove FV-11 to
      FV-14.
- [ ] C1.5 Wave 3: FV-18 and FV-20 (statement plus large-oracle link; full proof
      optional).

Acceptance: FV-01 to FV-04 and FV-11 to FV-14 `linked` and proved; vectors run
on every PR touching the modelled modules.

### C2 — TLA+ concurrency models

| | |
| --- | --- |
| Branch | RAMSQL `c2/tla-concurrency` |
| Owns | `formal/tla/{MvccReadCommitted,Reclamation,LockManager,RwGate,ServerSlots,TraceMvcc}.tla`, `formal/tla/tools.lock`, trace converter `formal/tla/trace/*.py` |
| Theorems | FV-07, 08, 15, 16, 21 and the server slot and cancel model |
| Depends on | B8.3 trace hooks for linking |
| Size | L |

Tasks:
- [ ] C2.1 Pin `tla2tools.jar` by hash. Add a CI job running TLC on the small
      configurations (each under 2 minutes) and nightly large configurations.
- [ ] C2.2 Specs:
  - READ COMMITTED with EvalPlanQual (3 transactions, 2 keys, 2 statements
    each);
  - reclamation with open snapshots;
  - lock manager with deadlock detection;
  - reader/writer gate with publication;
  - server slot state machine (0→1→2→3→0) with cancel keys and slot reuse.
- [ ] C2.3 Check safety invariants and liveness under weak fairness, including
      the parked-client wake that SRV-03 suspects can fail.
- [ ] C2.4 Trace validation: convert B8 simulator traces to TLA+ behaviors and
      check `TraceMvcc` with TLC on every nightly seed batch.

Acceptance: TLC finds no violation in the CI or nightly configurations; trace
validation runs nightly over at least 1,000 simulator traces.

### C3 — Durability models

| | |
| --- | --- |
| Branch | RAMSQL `c3/durability-models` |
| Owns | `formal/lean/Ramsql/Wal/**`, `formal/tla/WalCommit.tla` |
| Theorems | FV-05, FV-05b, FV-06, FV-22 and the backup invariant (published target equals source, or no target) |
| Depends on | B9.2 crash harness for linking |
| Size | M |

Tasks:
- [ ] C3.1 Model the WAL frame format exactly as `durable_log.sig` writes it
      (magic, version, length, kind, checksum). The executable Lean recovery
      function predicts the recovered state for any byte prefix and torn tail.
- [ ] C3.2 Prove the prefix property, fail-closed on a valid-after-invalid
      frame, and idempotence.
- [ ] C3.3 Model `WalCommit.tla`: flush-before-publish, poisoning, recovery,
      sequences. Check with TLC.
- [ ] C3.4 Link to B9.2: for every crash point, the real binary's recovered
      state equals the Lean prediction.

Acceptance: FV-05, FV-06 and FV-22 proved or model-checked and linked.

### C4 — Protocol models

| | |
| --- | --- |
| Branch | RAMSQL `c4/protocol-models`; ZPM `c4/enumeration` (test file only) |
| Owns | `formal/lean/Ramsql/Proto/**`; ZPM `tests/test_pg_enumeration.sig` |
| Theorems | FV-10, FV-17, FV-19 and the wire-audit invariants (authentication gating, failure is absorbing, extended-protocol error recovery, portal ownership, COPY chunking invariance, TDS reassembly) |
| Size | M |

Tasks:
- [ ] C4.1 Lean model of the PostgreSQL session phases and message alphabet.
      Prove: no execution before authentication completes; failure is
      absorbing; discard until Sync after an error; at most one suspended
      portal.
- [ ] C4.2 Enumeration test: all message sequences up to length 6 over the
      alphabet, with a mock DB that records calls. Every observable transition
      must match the model.
- [ ] C4.3 Framing model (FV-17), which serves as the oracle for B1 and B2
      fuzzers.
- [ ] C4.4 SCRAM model (FV-19) with RFC 7677 vectors and the `scramp`
      differential.

Acceptance: FV-10 enumeration green in CI; the framing oracle used by the
fuzzers.

### C5 — Capacity and admission ledger

| | |
| --- | --- |
| Branch | RAMSQL `c5/capacity` |
| Owns | `formal/lean/Ramsql/Capacity/**`, `formal/tla/AdmissionLedger.tla`, `tests/property/capacity_*.sig` (after A6) |
| Findings | SRV-15, SRV-17 |
| Theorems | FV-09 |
| Size | M |

Tasks:
- [ ] C5.1 Property tests for `storagePlan`, `responseBytes` and
      `clientMemoryAvailable`. Check each at the boundaries 0, 1, 0x1fffffff,
      0x20000000, 0x3fffffff, 0x40000000 and maxInt, and against a
      wide-integer model: each call returns either an error or the exact value.
- [ ] C5.2 Correct the documented `--rows` ceiling, or raise the real one
      (SRV-15).
- [ ] C5.3 Charge static buffers to the ledger, or document them as a fixed
      startup reservation reported by `--capabilities` (SRV-17).
- [ ] C5.4 Model the ledger with concurrent workers in TLA+: grants never
      exceed the limit; every failure refunds; all clients closed means back
      to baseline.

Acceptance: FV-09 linked; documented and real limits agree.

### C6 — Benchmark harness unification

| | |
| --- | --- |
| Branch | RAMSQL `c6/bench` |
| Owns | `bench/**` (harness, workloads, profiling), moved benchmark tools, `docs/benchmarks/results/**` (after A2) |
| Findings | REPO-09 (bench part), REPO-10 (reproducibility part), gate `matched_performance` |
| Size | L |

Tasks:
- [ ] C6.1 One CLI, `python -m bench run <suite> --sizes ... --engines ramsql,postgres16,postgres18,irdb`.
      Workload JSON files follow METHODOLOGY §3.
- [ ] C6.2 Environment capture, statistics (bootstrap confidence intervals,
      A/A noise floor), interleaved A/B runs, and correctness checks on every
      timed query.
- [ ] C6.3 Pin dbMeter by commit and verify its hash (already done in
      `benchmark_dbmeter.py`; keep it). Vendor nothing.
- [ ] C6.4 Add suites S8 (TPC-H-derived) and S9 (TPC-C-derived), labeled as
      derived and not as official TPC results.
- [ ] C6.5 Publish all results through A2's evidence pipeline.

Acceptance: a fresh clone plus pinned external harnesses reproduces any
published summary within its stated confidence interval on equivalent hardware.

### C7 — Performance regression and observability (wave 3)

| | |
| --- | --- |
| Branch | RAMSQL `c7/perf-obs` |
| Owns | `bench/ci/**`, `src/server/stats.sig`; supplies scripts that A4 wires into workflows |
| Closes | gate `operations` (with B6, A5) |
| Size | M |

Tasks:
- [ ] C7.1 PR gate: S1 micro-benchmark instruction counts on Linux (`perf
      stat` or valgrind `--tool=cachegrind`); more than 3% fails. Wall-clock
      S2 to S4 runs nightly on a dedicated, recorded host.
- [ ] C7.2 Runtime statistics: `ramsql_stat_activity` and `ramsql_stat_memory`
      views (or a `SHOW` command), plus `--stats-interval` log lines. Cover
      connections, parked statements, ledger use, WAL bytes and fsync latency,
      admission rejections and authentication failures.
- [ ] C7.3 Health: `--health` probe mode and a documented readiness signal.
- [ ] C7.4 Operations runbook in `docs/operations/`: start, stop, backup,
      restore, upgrade, failure triage.

Acceptance: perf gate catches a deliberately injected 5% regression; runbook
reviewed against a fault drill.

### C8 — Checkpoint and WAL lifecycle (wave 3)

| | |
| --- | --- |
| Branches | ZPM `c8/checkpoint`, RAMSQL `c8/checkpoint` |
| Owns | ZPM `durable_log.sig`, `change_log.sig`, `db_file.sig` (handed over from B9); RAMSQL `persistence.sig`, `backup.sig` |
| Closes | gate `backup_restore` (with upgrade migration) |
| Size | XL |

Tasks:
- [ ] C8.1 ADR: snapshot plus WAL-segment design, crash-safe checkpoint
      protocol (write the snapshot to a temporary file, fsync, rename, fsync
      the directory, then truncate WAL segments), and the on-disk format
      version.
- [ ] C8.2 Extend the C3 model with checkpointing **before** implementing it.
      Resolve STO-21 first: the checkpoint must not write `catalog_counters` from a
      live counter that abort rewinds can lower.
      The prefix property must hold across checkpoints.
- [ ] C8.3 Implementation: bounded recovery time (target under 5 s per GB of
      live data on a recorded host), WAL segment rotation, online backup that
      captures a consistent snapshot without stopping writers, and restore.
- [ ] C8.4 Upgrade migration: a format version in the header, a reader for
      version N-1, and a test that loads beta.2 WALs (existing
      `tests/fixtures/alpha1.wal` style).
- [ ] C8.5 Crash-point enumeration (B9 harness) over checkpoint boundaries.

Acceptance: recovery time bounded and measured (S10); online backup and restore
tested under load; gate `backup_restore` passes.

### C9 — Compact storage, streaming results, indexes (wave 3 onward)

| | |
| --- | --- |
| Branches | ZPM `c9/*` (one per slice) |
| Owns | ZPM `sql/storage.sig`, `sql.sig`, `exec.sig`, `catalog.sig` (handed over from B10) |
| Input | `docs/plans/MACHINE_CAPACITY_AND_100M_PLAN.md`, gate `capacity_concurrency` |
| Size | XL, multi-wave |

Tasks, each slice behind route-equivalence (B10) and simulation (B8):
- [ ] C9.1 ADR covering: compact typed column storage with variable-length
      text pages, how versions map onto it (sparse versions), WAL encoding,
      and the target bytes per row.
- [ ] C9.2 Stream results to the client instead of materializing them
      (removes the result-row ceiling for ordinary queries), with per-session
      byte budgets.
- [ ] C9.3 Secondary and ordered indexes (B-tree style, caller-budgeted pages),
      with a planner that uses them. Unique and foreign-key constraints build on
      them (with B11.3).
- [ ] C9.4 Integrate the packed-integer primitive into SQL storage (100M plan
      step 3).
- [ ] C9.5 Scale qualification (S6): 1M, 10M and 100M rows. Measure bytes per
      row and load rate, and run queries under the machine budget.

Acceptance: 100M rows across multiple tables load and query on a recorded host
within its memory budget; gate `capacity_concurrency` gains linked evidence.

---

## 10. Checkpoints

| Checkpoint | Criteria (all required) | Merge |
| --- | --- | --- |
| **CP0** (end of wave 0) | NET-01 fixed in both protocols with regression tests and a release decision; STO-01 (out-of-order DDL replay) and STO-03 (lock-list overflow) fixed with regression tests; SRV-01 fixed; in-flight PRs merged; branch protection on; one-time formatting commits landed; baseline recorded | `int/hardening` → `main`, patch release if D1 says so |
| **CP1** (end of wave 1) | Tree matches REPO_LAYOUT; evidence relocated; pytest harness with parity; every relevant ZPM suite runs or is excluded with a reason; required CI checks per §11; one license; docs-consistency green; server split with in-process tests; fuzzers running nightly for PostgreSQL, MySQL and TDS; FV-01 to FV-04 stated and linked by vectors; differential harness running | `int/hardening` → `main`, beta.3 candidate |
| **CP2** (end of wave 2) | No open critical or high finding outside B11, C8 and C9 scope; TLS with channel binding; timeouts and logging; simulation and crash-point harnesses nightly with no failures; Tier 1 FV proved and linked; route equivalence in CI; coverage targets (Q8) met | `int/hardening` → `main`, beta.4 "network-ready" candidate |
| **CP3** (end of wave 3) | Roles; checkpoint and online backup; streaming results; first compact storage slice; ORM smoke tests; performance gates; operations runbook; `release-gates.json` updated with evidence for every gate, either passed or explicitly scoped out | release candidate review |

At each checkpoint, the integrator runs the full gate on `int/hardening`
(§11 plus nightly jobs on demand). The maintainer approves the merge to `main`.

---

## 11. Target CI gate matrix

| Job | Trigger | Required | Proves |
| --- | --- | --- | --- |
| `lint` | PR, push | yes | `sig fmt --check`, ruff, actionlint, zizmor, forbidden-pattern lint |
| `docs-consistency` | PR | yes | links, versions, license, module pages present |
| `contracts` (Debug, ReleaseSafe) × 6 hosted OS | PR, push | yes | every manifest suite runs, assertion floors hold |
| `integration` × 6 OS | PR, push | yes | pytest integration suites |
| `postgres-differential` (16, 18) × (memory, durable) | PR, push | yes | semantics, isolation, COPY, crash parity, B7 corpus subset |
| `clients` | PR | yes | L6 driver matrix smoke |
| `formal-lean`, `formal-tla-small`, `vectors` | PR touching modelled code or `formal/` | yes | proofs build, TLC small configurations, vectors replay |
| `route-equivalence` | PR touching `exec`, `storage`, `sql.sig` | yes | fast path equals reference path |
| `fuzz-smoke` | PR | no (reports) | 5 minutes per parser target |
| `bench-smoke` | PR | no (reports) | every bench, compare and profile script runs on tiny inputs |
| `perf-instructions` | PR | yes after C7 | S1 instruction count regression ≤ 3% |
| `supply-chain` | PR | yes | dependency review, hashed requirements |
| `nightly` | schedule | n/a | fuzz 1 h per target, simulation 10k seeds, crash enumeration, TLC large, Debug build, full bench smoke, large runtime |
| `weekly` | schedule | n/a | 24 h soak (on the dedicated host), S5 concurrency scaling |
| `release` | tag on `main` only, `release` environment | n/a | exact artifact execution on all targets, attestation, gate |

---

## 12. Test and validation matrix

`●` exists today, `○` planned in this program, `–` not applicable.

| Area | L1 contract | L2 property | L3 model | L4 fuzz | L5 integ | L6 clients | L7 diff | L8 sim | L9 crash | L12 formal |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Lexer and parser | ● | ○ | – | ●→○ grammar | ● | – | ○ | – | – | FV-03 |
| Binder and types | ● | ○ | ○ | – | ● | – | ●→○ | – | – | FV-02, 11–13, 20 |
| Temporal | ● | ○ | ○ | ○ | – | – | ○ | – | – | FV-12 |
| Expressions, LIKE | ● | ○ | ○ | – | – | – | ○ | – | – | FV-01, 04 |
| Executor fast paths | ● | ○ | ○ route | – | ● | – | ○ | – | – | FV-14 |
| Storage and MVCC | ● (thin) | ○ | ○ | – | ● | – | ● isolation | ○ | – | FV-07, 08, 21 |
| Locks | ● | ○ | ○ | – | ● | – | ● | ○ | – | FV-15 |
| Change log and WAL | ● | ○ | ○ | ○ | ● | – | ● crash | ○ | ●→○ | FV-05, 06, 22 |
| PostgreSQL codec | ● | ○ | ○ enum | ●→○ | ● | ●→○ | ● | ○ | – | FV-10, 17 |
| SCRAM | ● | ● | ○ | ● | ● | ● | – | – | – | FV-19 |
| MySQL and TDS | ● | – | – | ●→○ | ● | ●→○ | – | – | – | FV-17 |
| TLS | – | – | – | ○ | ○ | ○ | – | – | – | – |
| Server reactor and admission | ○ | ○ | ○ | ○ CLI | ● | – | – | ○ | – | FV-09, slots |
| Persistence and backup | ● | ○ | ○ | – | ● | – | – | ○ | ●→○ | FV-05 |
| Capacity | – | ○ | ○ | ○ | ● | – | – | – | – | FV-09 |

---

## 13. Benchmarks

Follow [docs/benchmarks/METHODOLOGY.md](docs/benchmarks/METHODOLOGY.md).
Program-level rules:

1. No performance claim leaves the repository without a result summary that
   follows the methodology.
2. Every hardening PR that touches a hot path (`exec`, `storage`, codecs,
   reactor) runs the S1 instruction-count gate. A security fix that costs
   performance still merges. The cost is recorded, and C7 tracks the recovery.
3. Before CP2 and CP3, run S2 to S5 against PostgreSQL 16 and 18 and IRDB on
   the dedicated host. Publish wins and losses.

---

## 14. Agent templates

### 14.1 Lane brief (given to a fresh agent)

```
You are the <role> for lane <ID> <title> in the RAMSQL hardening plan.
Repos: RAMSQL <path>, ZPM <path>. Your worktree: <path>; branch <branch>.
Read first: AGENTS.md (both repos), PLAN.md §<lane>, docs/process/GIT_WORKFLOW.md,
docs/process/DEFINITION_OF_DONE.md, and these module pages: <list>.
You own exactly these paths: <list>. Do not edit anything else; file a request
to the integrator instead.
Findings to close: <ids>. Invariants to keep: <INV ids>. Formal links: <FV ids>.
Task list: <checkbox items>.
Validation you must run and report: <commands>.
Never weaken a test, never claim an unobserved result, never commit outside your
branch, never touch caches or other worktrees.
Finish with the report format in PLAN.md §14.2.
```

### 14.2 Report format (every agent ends with this)

```
Lane / role:
Branch and head commit:
Files changed (path: +added/-removed):
Findings closed (id: how, test that proves it):
Commands run (exact command → exit code → key output line):
New or changed invariants:
Module pages updated:
Open questions / blocked on:
Anything I could not verify:
```

### 14.3 Adversarial review checklist

The reviewer receives the diff, the lane brief and the module pages, and never
the implementer's summary.

- [ ] Does each test fail without the change? Revert the fix locally and run it.
- [ ] Hostile inputs: negative or huge lengths, zero, max, NUL, invalid UTF-8,
      split frames, reordered messages, repeated flags.
- [ ] Error paths: is statement atomicity preserved, are grants refunded, are
      secrets zeroed, are locks released?
- [ ] Concurrency: what if another worker runs between these two lines? Is the
      lock order respected (gate → store mutex)?
- [ ] Silent wrong results: compare with PostgreSQL for every changed semantic.
- [ ] Layering: no ZPM → RAMSQL import, no product policy in ZPM.
- [ ] Allocator-free: no hidden growth, new limits documented.
- [ ] Does the module page say what the code now does?
- [ ] Is any claim in the PR description unsupported by a command and output?

### 14.4 Verifier template

```
For each "Evidence" command in the PR: run it in a clean worktree at the PR head
on the stated OS. Record exit code and the line that proves the claim. Mark the
claim VERIFIED, FAILED or NOT REPRODUCIBLE. Do not fix anything.
```

### 14.5 Integrator checklist (per merge)

- [ ] Rebased on `int/hardening`; no provisional ZPM pin left.
- [ ] Required checks green; reviewer approved; verifier report attached.
- [ ] `build.sig.zon` pin and hash updated if ZPM moved; the full contract
      manifest re-run.
- [ ] Findings register statuses updated; changelog fragment present.
- [ ] Ownership table (§6.4) still respected for the next lane.

---

## 15. Risk register

| Id | Risk | Likelihood | Impact | Mitigation |
| --- | --- | --- | --- | --- |
| R1 | Agent-written code that nobody understands | high | high | test-first, independent reviewer and verifier, small PRs, module pages updated in the same PR, function-size limits |
| R2 | NET-01 details become public before the fix (public repository; this plan describes it) | medium | high | land B1.0 before pushing this plan to a public branch; SECURITY.md process (A5.2) |
| R3 | Merge conflicts from parallel lanes | high | medium | ownership table, wave handoffs, moves and formatting first (A1.1, A4.2), append-only registration |
| R4 | ZPM pin churn, or ZPM changes by other products (games, Solana, mesh) breaking RAMSQL | medium | high | `int/ramsql-hardening` in ZPM, integrator-only pins, full manifest run on every pin bump |
| R5 | Lean or TLA+ effort grows without bound | medium | medium | tiers, state-first review, link before proof, time-box each theorem; a theorem stays `stated` and linked if a proof stalls |
| R6 | CI time and cost (contracts take about 10 min at `-j2`; 83 suites; 6 OS) | high | medium | shard groups (A3.6), caching, nightly for long jobs, a dedicated host for wall-clock benchmarks |
| R7 | Development host memory pressure (LLVM out of memory with 0.9 GB free) | high | low | documented `-j`, run heavy campaigns on a dedicated host, never run benchmarks during builds |
| R8 | Feature lanes (B11, C9) crowd out hardening | medium | high | wave 3 only, after CP2 |
| R9 | Flaky integration tests erode trust in CI | medium | medium | readiness waits, port 0, quarantine policy (STRATEGY §5) |
| R10 | The TLS implementation itself introduces vulnerabilities | medium | high | ADR, cryptography reviewer, interop and fuzz tests, prefer reviewed code |
| R11 | Marketing claims drift again | medium | medium | site numbers generated from `results.json`; docs-consistency job |

---

## 16. Exit criteria and release mapping

| Release | Requires | Gate changes expected |
| --- | --- | --- |
| 0.3.0-beta.3 "hardened beta" | CP1 | durability stays passed; no gate newly passed, but critical and high findings in wire, host and front end closed |
| 0.3.0-beta.4 "network-ready beta" | CP2 | `transport_security` passed; `authentication` partial → passed only after B4 |
| 0.4.0 release candidate | CP3 | `authentication`, `backup_restore`, `operations` passed; `postgres_compatibility`, `capacity_concurrency`, `matched_performance`, `storage_power_failure` passed or explicitly scoped with published limits |

`tools/release_gate.py` stays fail-closed. Gates change only when evidence
linked in `release-gates.json` proves them.

---

## 17. Decisions needed from the maintainer

| Id | Decision | Options | Blocks |
| --- | --- | --- | --- |
| D1 | NET-01 response | patch release beta.3 now vs fold into CP1; advisory text | B1.0, A5.2 |
| D2 | Evidence store | separate `ramsql-evidence` repository, release assets, or object storage | A2 |
| D3 | Website | own repository (recommended) or `site/` in this repository | A1.3, A5.7 |
| D4 | License | NOTICE wording for earlier MIT tags; SPDX-style identifier for the custom license | A5.1 |
| D5 | MySQL and TDS | authenticate over TLS (L), or keep as loopback-only experimental and refuse durable mode (S) | B2.3 |
| D6 | TLS implementation | reuse the ZPM transport TLS 1.3 after audit, or write a minimal server-side TLS 1.3 | B3 |
| D7 | Sparse-factor feature | integrate behind route equivalence, or revert | A0.3 |
| D8 | Cleanup | approval to prune merged branches, stale worktrees, `.agents/`, scratch in `.sig-cache/` | A0.6, A1 |
| D9 | Formal tooling | Lean location (`formal/` in RAMSQL, recommended) and whether Mathlib is allowed | C1 |
| D10 | Process | the integration-branch model in GIT_WORKFLOW versus trunk-based development with merge queues | all |

---

## 18. Status log

### Wave 0 (2026-10-30): code fixes reviewed and integrated, not yet merged

Lanes ran in parallel, each in its own worktree with an implementer and an
independent reviewer/verifier. Changes are uncommitted on these branches:

| Lane | Branch (base) | Diff | Tests before → after | Review |
| --- | --- | --- | --- | --- |
| NET-01 (B1.0 + B2.0) | ZPM `b1/net01-param-splice` (`f0668cf`) | 5 files, +393/-6: `postgres.sig`, `tds.sig`, `parser.sig` (`countValue`, `FETCH FIRST` guard), tests | postgres 15,807 → 17,225; tds 2,661 → 2,733 | approved; LIMIT/OFFSET/FETCH SQLSTATE and the `e±` exponent edge fixed after review |
| STO-01 (B9.0) | ZPM `b9/sto01-replay-order` (`f0668cf`) | 2 files, +311/-9: `change_log.sig`, tests | sql-change-log 876 → 940 | approved; index-collision test added after a mutation showed the gap. STO-21 and STO-22 recorded; FV-05b counter rule restated |
| STO-03 (B8.0) | ZPM `b8/sto03-lock-list` (`f0668cf`) | 4 files, +247/-19: `locks.sig`, `mvcc.sig`, `sql.sig` (3 call sites), tests | sql-mvcc 122 → 325 | approved; holder-level tests added after a mutation showed the gap |
| SRV-01 (B5.0) | RAMSQL `b5/srv01-create-data` (`6d677ee`) + ZPM `b5/srv01-not-found` (`f0668cf`) | RAMSQL 18 files, +245/-25; ZPM `db_file.sig` + tests, +46/-4 | persistence contract 112 → 119; `test_persistence.py` 66 → 67 cases | approved with follow-ups; probe replaced by ZPM `FileNotFound` (fixes the libc misreport and the FIFO hang) |

**Every failing-before test was observed failing on the old code**, including:
- `v-$1` spliced as `v--5` and reporting `UPDATE 3`;
- the lock-list out-of-bounds panic;
- `ChangeRecordConflict` on replay;
- a silently created `typo-database.wal`.

**Integration.** The four ZPM diffs were combined on `int/ramsql-hardening` (`f0668cf`), archived, and pinned in a scratch copy of RAMSQL by `file://` URL and SHA-256. Nothing was pushed. All of the following passed:
- RAMSQL contracts: 71/71 steps; 34 reporting suites, 418,707 executed assertions.
- ZPM `test-sql` and `test-db`: 43 suites, 472,167 executed assertions.
- 11 Python integration suites (CLI, server, persistence crash cycles, auth, independent clients, workers, capacity, machine capacity, reviewer runtime, beta onboarding, streamed joins).
- The SRV-01 adversarial test (directory, file-as-directory, unreadable WAL).
- 11 end-to-end psycopg checks.

**End-to-end, unmodified `main` against the integrated build (psycopg 3.3.6):**

| Check | `main` 6d677ee | Integrated |
| --- | --- | --- |
| Missing `--data` file | created silently | refused, absolute path printed |
| `UPDATE ... bal-$1 WHERE id=$2`, int4 `-5` | `rowcount=3` (every row) | `rowcount=1` |
| Same, int8 binary | `rowcount=3` | `rowcount=1` |
| `SELECT $1::text`, int4 `-5` | 42883 | `'-5'` |
| `FOR SHARE` then `FOR UPDATE`, 200 rows | server process died | 200/200, server alive |
| Out-of-order `CREATE TABLE`, restart | not reached | both tables and data intact |

**Not done in wave 0 (needs maintainer action or later waves):**
- A0 merges of open PRs.
- A4.1 rulesets (admin rights needed).
- A4.2 one-time formatting commit.
- The D1 disclosure decision for NET-01.
- Committing the lane branches, opening ZPM and RAMSQL PRs, and the pin bump. SRV-01's RAMSQL change must land in the same pin bump as `b5/srv01-not-found`.

Follow-ups recorded:
- NET-02 (still exploitable when a template has `$n` or an apostrophe inside `/* */`);
- moving `absolutePath` into ZPM;
- STO-21 and STO-22;
- a captured beta.2 kind-3 WAL fixture.

---

## Appendix A. Module pages

See [docs/modules/README.md](docs/modules/README.md) for the catalog with
status and owner lane.

## Appendix B. Commands

```sh
# Contracts (Windows/Linux; set SIG_LIB_DIR to the prepared runtime first)
sig build test -Doptimize=ReleaseSafe -j2 --summary all

# Integration (after A3)
python -m pip install -r tools/requirements-test.txt
pytest tests/integration -m "not slow"

# Today's integration scripts
python tools/test_server.py
python tools/test_auth.py
python tools/test_persistence.py

# Formal (after C1/C2)
cd formal/lean && lake build && lake exe vectors all
java -jar formal/tla/tla2tools.jar -config MvccReadCommitted.cfg formal/tla/MvccReadCommitted.tla
```
