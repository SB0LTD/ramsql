# RAMSQL Agent Guide

RAMSQL is a pure Sig, allocator-free in-memory database package and CLI.

- Reusable SQL parsing, storage, validation, and execution belong in ZPM
  `src/core/sql.sig` (Layer 0, `@zpm/sql`). Keep this repository's library a thin
  public facade. CLI policy, presentation, integration tests, and examples live here.
- Sig owns language/compiler/build/standard I/O primitives. Do not add product
  behavior to the compiler. Lower layers never import RAMSQL.
- Use fixed-capacity or caller-owned storage. Keep the complete runtime in `.sig`;
  do not add SQLite, C libraries, heap allocators, or language runtime dependencies.
- Resolve ZPM only through the explicit `build.sig.zon` dependency. Published
  revisions use a commit archive URL and verified SHA-256. No implicit sibling paths.
- Preserve statement atomicity and ownership on every error path. Keep SQL scope
  and capacity limits documented. Do not imply full SQL-standard compatibility.
- Validate with `sig build`, `sig build test`, `sig build run`,
  and `python tools/test_cli.py`. Tests must execute assertions and fail on errors.
- Keep existing caches and generated files unless the user requests cleanup.
