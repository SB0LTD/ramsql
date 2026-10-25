# RAMSQL Agent Guide

RAMSQL is a pure Sig, allocator-free in-memory database server and library.

- Reusable SQL parsing, storage, validation, and execution belong in ZPM
  `src/core/sql.sig` (Layer 0, `@zpm/sql`). Keep this repository's library a thin
  public facade. Shared transactions and wire codecs live in ZPM `src/db/`;
  native sockets in ZPM `src/platform/tcp_server.sig`. Server listeners, capacity
  profiles, reactor policy, integration tests, benchmarks and examples live here.
- Sig owns language/compiler/build/standard I/O primitives. Do not add product
  behavior to the compiler. Lower layers never import RAMSQL.
- Use fixed-capacity or caller-owned storage. Keep the complete runtime in `.sig`;
  do not add SQLite, C libraries, heap allocators, or language runtime dependencies.
- Resolve ZPM only through the explicit `build.sig.zon` dependency. Published
  revisions use a commit archive URL and verified SHA-256. No implicit sibling paths.
- Preserve statement atomicity and ownership on every error path. Keep SQL scope
  and capacity limits documented. Do not imply full SQL-standard compatibility.
- Validate with `sig build`, `sig build test`, `sig build run`,
  `python tools/test_cli.py`, `python tools/test_server.py`, and
  `python tools/test_clients.py`, `python tools/test_auth.py`, and
  `python tools/test_persistence.py`. Default run requires PostgreSQL SCRAM credentials and durable WAL; use
  `ramsql --demo` for a terminating example. Tests must execute assertions.
- Preserve flush-before-publication, exclusive writer locking and fail-closed
  recovery. Offline backups validate existing source data and never overwrite
  destinations. Connections share MVCC storage; do not reintroduce per-client
  database snapshots. Scratch/result ranges must be initialized before reading;
  versions remain owned until no active snapshot can observe them.
- Verify protocol changes with independent clients; wire framing compatibility
  does not imply complete SQL dialect, catalog, authentication or extension support.
- Benchmark ReleaseFast with correctness checks and recorded environment. Report
  native engine and network/client costs separately; do not invent comparisons.
- Keep existing caches and generated files unless the user requests cleanup.
