# Changelog

Release changes are separated from aspirations. See each tag's exact qualification
and benchmark bundle; development measurements do not automatically apply to a
later binary.

## Unreleased

- Discover effective machine resources on Windows, Linux and macOS, and charge
  server page regions and worker allowances to a shared admission ledger. Add
  `--machine-capacity`, `--memory-budget` and `--os-headroom` diagnostics/options.
- Allow concurrent PostgreSQL simple autocommit reads with multiple native
  workers. Execution contexts are owned per worker and reused between clients, sharing
  table storage under a reader/writer
  gate; mutations and reclamation remain exclusive. Read memory is budgeted,
  with exclusive fallback when private buffers cannot be admitted.
- Check concurrent wire execution, cancellation/timeouts, writer exclusion and
  uncommitted visibility. Add `profile-reads` for checked native phase timings.
- Retain checked wire measurements: eight clients/four workers reach about
  130k PK queries/s versus 67k with one worker on this Windows host, while CPU
  cost rises from 14 to 27 microseconds/query. This is an outstanding efficiency
  cost, not evidence of PostgreSQL/IRDB superiority or 100M-row SQL readiness.

## 0.3.0-beta.2 — 2026-10-27

- Bind query scratch and per-client result/response storage from caller-owned OS
  page regions. Runtime `--work-rows` and `--result-rows` no longer require rebuilds
  or put giant arrays in the executable. Default rows/workspace 4,096, results
  8,192, parser expressions 8,192; runtime clients up to 60 (default 16).
- Per-slot `--table-rows`/`--table-versions` avoid uniform largest-table budgets.
  Reservations remain explicit and bounded; inline values still cost memory.
- Push safe available predicates into all-inner joins before intermediate emit.
  Outer/volatile/correlated cases retain conservative evaluation.
- Fix uniqueness checks using a compiled mask against runtime-sized PK indexes.
- Increase prepared-source storage and return recoverable 54000 errors after
  bounded draining of oversized authenticated SQL frames. Return PostgreSQL
  FATAL 53300 on SQL pool exhaustion through a separate bounded admission queue.
- Experimental Windows/no-libc Linux `--workers` for socket/protocol reactors,
  caller-owned native thread controls, serialized shared SQL, generation-bound
  cancellation and joined cleanup. Default one worker; macOS/SB0 remain one.
- Native Linux release builds use Sig's pinned no-libc public runtime I/O patch
  and ZPM's native vDSO wall clock. Compiler/matching standard library remain
  Sig 0.6.4; public runtime archive identity is recorded and checked separately.
- Independent reviewer regressions cover 5,000 rows, 500-row/four-value INSERTs,
  seven aggregates, 2,000 returned rows, selective three-way joins with tiny
  scratch, 32 transactions, unequal-bank restart and duplicate-key rejection.
- Checked wire PK measurements show improvement with four reactors at eight
  clients on one Windows host. This is not evidence of parallel SQL execution,
  PostgreSQL parity or universal superiority; see the reviewer follow-up report.

The beta.1 source review was not a published release: its PR workflow validated
artifacts but skipped publication by design. Beta.2 is
[published with assets](https://github.com/SB0LTD/ramsql/releases/tag/v0.3.0-beta.2)
from `defacb76de8f2c95bdf4f80b2575fbfde791d8cf`. The explicit publication
[workflow](https://github.com/SB0LTD/ramsql/actions/runs/40452582057) passed.
All 28 checksummed files and eight target packages were independently verified
after download; the Windows package also executed `--version`/`--capabilities`.

## 0.3.0-beta.1 (source review; unpublished)

### Added

- `--capabilities`: effective runtime row/version/backing budgets and compiled
  SQL/protocol/security limits, without starting a server or touching the WAL.
- Independent build budgets for columns, result rows, row locks, WHERE predicates,
  expression nodes and FROM relations. A 16-column/1,024-result profile is tested
  through libpq with 12-column data, 600 returned rows and 200-byte text.
- A packaged, exclusive-create PostgreSQL SCRAM verifier helper and live
  onboarding test. No password-in-argv option; printable ASCII helper passwords.
- README, capacity guide, operations, implemented SQL reference, audit and staged
  roadmap. Release packages include the guides and credential helper.
- Checked production trace/replay tooling, independent PostgreSQL model checks,
  corruption rejection and retained IRDB comparison evidence.

### Performance and implementation since alpha.4

- ZPM exact committed scalar/group summaries, dense-key ordered probes,
  caller-backed compact integer columns, bounded vector/scalar predicate masks
  and specialized COUNT/SUM scans. Pending changes and old snapshots retain MVCC.
- Readiness-gated accepts and native wall-clock improvements; optimized bounded
  grouping/top-k paths. All remain allocator-free with explicit memory costs.
- All 21 finite read comparisons against IRDB 1.10.821 passed at 10k/100k/1M
  rows; 10 additional wide-profile cases passed. Original long million-row churn
  timed out in IRDB, so its original matrix remains 29/30. See raw evidence.

### Corrected

- Removed stale alpha candidate/large-aggregation-loss wording from the current
  overview; historical reports are retained with their actual source identities.
- Distinguished stored-row capacity from returned-row/workspace/schema limits,
  dialect adapters from full dialects, and SB0 memory transports from hosted TCP/WAL.

### Still limited

No TLS/roles, complete PostgreSQL/dialect parity, online backup/checkpoints/WAL
compaction, multi-worker engine or unbounded result streaming. Stock profile
defaults remain small and visible. This is a scoped prerelease, not production
graduation. No WAL format change or general downgrade guarantee is introduced.

## 0.3.0-alpha.4

- Hosted runtime row/version buffers, exact native SB0X/SB0K release artifacts,
  checksum/provenance and executable hosted/native release validation.
- PostgreSQL-first shared MVCC server, synchronous typed WAL/recovery, SCRAM,
  offline validated backup and independently tested MySQL/TDS adapters.
- Initial scale/reference benchmarks. Later optimization evidence identifies
  newer source revisions and must not be attributed to the published alpha.4 binary.

Earlier alpha history is preserved in Git tags and historical compatibility and
benchmark documents; it is not evidence of full production compatibility.
