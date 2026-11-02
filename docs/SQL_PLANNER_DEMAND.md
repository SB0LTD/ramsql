# Planner-demand integration candidate

Reusable binding, constant diagnostics and output demand remain in allocator-free
ZPM L0 SQL. RAMSQL adds the shared contract, the frozen native program and
independent libpq metadata/execution checks. The product must pin the immutable
ZPM archive with its verified SHA256; no runtime sibling dependency is allowed.

The q01 native differential catalog gains94 frozen execution statements plus5
bound/binary parameter statements. They cover unused plain projections, empty
inputs, immutable domain/overflow errors, semantic priority, CASE/COALESCE/boolean
reachability, joins, CTE materialization and sort/limit barriers, sequence effects
and failed DML. Existing PG16/18 memory/durable-SCRAM and exact-release jobs require
both new cases. No restart or memory skips are added.

Independent client tests use libpq Prepare, statement Describe and Execute as
separate operations. Metadata returns the native OIDs without triggering these
execution-planning errors; subsequent Execute must return2201F or22012. Semantic
missing-column errors remain errors. This is a scoped metadata guarantee, not
complete PostgreSQL Parse/Bind timing or generic prepared-plan parity.

The engine uses fixed caller-owned per-plan constant values and required-output
bits. It does not expand AST depth or change row widths/cardinality. Stable and
volatile functions remain outside immutable constant bindings; group/window/set,
correlation, nullable outer joins and shared/recursive CTE dependencies retain
conservative barriers. Configured capacities still apply.

The native core oracle has94 execution and4 raw Parse/Describe probes. The new
core contract adds describe/type, one-shot cancellation and depth-bound checks.
Final independent review, full supported-platform/PG16+18 qualification and exact
release publication are required before merge. Broader aggregate/window-state
liveness, scalar-subquery projection pruning and general planner parity remain
open; see the ZPM module doc and planner_demand_open.json.

The first ZPM hosted broad graph exposed a private ReadContext Describe regression.
The corrected pin separates metadata binding from executable preparation and
retains snapshot/cancellation ownership. Independent ZPM re-review rebuilt
444 planner assertions plus46 parallel-read assertions/2400queries/eight threads.
The corrected product build passes99 statements in memory and durable-SCRAM
against native16.15, and the four-worker regression suite including640 prepared
binary queries. Final hosted qualification remains mandatory. Initial failed and
corrected evidence is retained separately in the manifest.
