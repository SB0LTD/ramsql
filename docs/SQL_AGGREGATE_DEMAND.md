# Aggregate-demand integration candidate

Reusable runtime implementation remains in allocator-free ZPM L0 SQL. RAMSQL pins
the immutable commit archive with a verified SHA256 and adds the shared core
contract to its existing test graph. The product contains no sibling dependency
or second SQL implementation.

The native q01 differential catalog adds53 aggregate
programs, each with its own setup and cleanup, plus a bound-parameter/binary-result
case. Values, column metadata, SQLSTATE and sequence effects are compared with
live PostgreSQL. Existing memory/durable-SCRAM, PG16/18 and exact-release jobs
execute these cases without adding skips. The fixture contains SQL programs only;
expected native outcomes are never substituted for actual server execution.

The ZPM engine tracks fixed-capacity aggregate-state demand. It skips unused
transition arguments/FILTERs, initializes suppressed state slots to NULL, preserves
group cardinality and required HAVING/order/deduplication dependencies, and falls
back to bounded generic streaming for unsupported partial specialized layouts.
Its narrow pure COUNT(*) producer-presence helper preserves existing fused paths.
LIMIT0 returns before runtime input scans while retaining binding diagnostics and
a cancellation checkpoint.

This is a candidate, not a qualified release. Independent integration review,
native wire/parameter comparison, full hosted CI and exact release qualification
remain required. The initial ZPM50-case core contract passed after a25-gap red
baseline; full CI caught a missing dependency for aggregates inside unused named
WINDOW definitions. Three native16-backed programs supplement that corpus, with
a50pass/3gap red checkpoint and corrected53/53 result. Final-source qualification
remains required. Physical tests include100000rows with1024query handles and failure/reuse
controls. This evidence makes no throughput claim.

Open: existing right-hand boolean absorbers (aggregate AND FALSE / OR TRUE),
numeric-literal capacity, DML-root demand, scalar-subquery pruning, windows,
correlation and broader SQL parity. The original118-program inventory remains
12smoke passes /89failures /17unexecuted. Configured capacities remain applicable;
this change does not establish production drop-in compatibility.
