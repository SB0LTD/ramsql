# Changelog

Release changes are separated from aspirations. See each tag's exact qualification
and benchmark bundle; development measurements do not automatically apply to a
later binary.

## 0.3.0-beta.1

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
