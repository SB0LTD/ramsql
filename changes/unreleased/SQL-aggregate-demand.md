Avoid evaluating unused aggregate arguments and FILTERs when their outputs are
not required. Keep group cardinality and dependencies from HAVING, ordering,
deduplication and named WINDOW clauses. Unsupported partial specialized layouts
use bounded streaming. LIMIT0 skips runtime input work after binding diagnostics.

Add53 native-reference aggregate programs and binary parameter checks to the
PostgreSQL differential gate, plus the shared allocator-free core contract.
Existing optimizer, SQL-feature and configured-capacity gaps remain documented;
this change makes no general PostgreSQL parity or benchmark superiority claim.
