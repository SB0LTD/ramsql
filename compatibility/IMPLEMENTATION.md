# PostgreSQL replacement implementation sequence

The production target is PostgreSQL application compatibility with durable,
allocator-free execution. The alpha is the starting point. Passing the finite
differential suite does not establish complete compatibility. Keep stable
publication blocked until the production gates have real evidence.

## 1. Validated durable execution

Reusable code belongs in ZPM SQL/store; replay policy belongs in RAMSQL.

- Validate each statement completely before the persistence boundary.
- Persist exactly one framed autocommit statement before any public mutation.
- Apply validated changes without another fallible operation or database copy.
- Preserve database/result on validation or persistence failure; poison a failed
  writer until recovery. Keep generic adapters' candidate fallback.
- Replay verified single-statement WAL records without database copies, while
  keeping multi-statement replay atomic and rejecting malformed framing first.
- Check every mutation, failure injection, independent clients, process crashes,
  all hosted platforms and both native formats. Compare durable ReleaseFast
  measurements with the released binary on the same host and storage.

Explicit transactions still use snapshots and generation conflicts. Removing
autocommit copies does not establish MVCC or PostgreSQL READ COMMITTED.

## 2. Scalable transaction and storage architecture

Add caller-owned pages, typed variable-length value storage, stable row
identities, row versions, snapshot visibility and reclaimable version chains in
ZPM. Integrate them into SQL, rather than shipping unused parallel primitives.
Define exhaustion and rollback behavior before changing the dense representation.
Add row write ownership and statement snapshots, including concurrent updates,
predicate reevaluation, unique-key races, transaction abort and disconnect.
Verify READ COMMITTED schedules against PostgreSQL before changing the default.
Then remove full transaction copies and global-generation write conflicts.

## 3. Durable lifecycle

Version the new record format and schema semantics explicitly. Add crash-safe
checkpoint publication and WAL rotation with sequence anchors, restart-safe
directory durability, migration, backup/restore and bounded recovery time.
Test injected short writes, sync failures, every crash boundary, corrupt records,
out-of-space behavior and offline/online backup consistency. Native SB0 needs
actual filesystem and durable-flush ABI capabilities before durable operation.

## 4. PostgreSQL semantics and application compatibility

Extend the typed expression/execution engine in ZPM with PostgreSQL numeric,
temporal and structured types; boolean expressions; joins, aggregates and CTEs;
defaults, identity, constraints, RETURNING, upsert and COPY. Add schema/catalog
queries, transaction settings/savepoints, notifications and prepared-statement
behavior. Test migrations and real applications with independent drivers and
PostgreSQL tools. Track unsupported behavior explicitly; avoid canned catalog
answers or accepting statements without implementing their effects.

## 5. Secure, concurrent operation

Use shared ZPM transport primitives for TLS and secure randomness, and implement
certificate/authentication policy in the product. Add roles/privileges, secure
remote listeners and authenticated MySQL/TDS. Introduce worker scheduling only
after ownership and lock/wait invariants are tested. Add cancellation, timeouts,
resource limits, observability, graceful shutdown, recovery procedures and soak
tests. Native SB0 uses the consolidated ABI and real OS capabilities.

## 6. Qualification and release

Require complete release assets, real runtime tests across the OS/ISA matrix,
checksums/provenance and exact-artifact durable benchmarks. Measure throughput,
tail latency, resident memory, recovery and saturation across realistic data sizes
and concurrency. Retain regressions as failures; do not convert missing features
into passing skips. Publish stable only when compatibility, security, durability,
operations and performance gates pass with reproducible evidence.
