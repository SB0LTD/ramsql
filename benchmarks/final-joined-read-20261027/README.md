# Profile-guided joined-read comparison

100,000 SQL-loaded fact rows, 16 shops and 8 products; nullable/missing keys and values, five output groups. Every returned value is checked against an independent integer model. One client uses PostgreSQL simple queries against a memory-only server. All three executors receive identical SQL and capacities (32,768 workspace rows, 16 result rows, asymmetric table banks).

Three trials alternate executor order, with 100 checked queries per trial. Import and three warmups are excluded. Binaries use the same RAMSQL runtime source and portable ReleaseFast options; ZPM source revisions differ. Raw trials, binary hashes, environment, memory and source harnesses are retained in `results.json`.

| Executor | Median wire ms/query | Median server CPU ms/query |
| --- | ---: | ---: |
| Materialized joins | 33.976 | 33.594 |
| Streamed version inputs | 16.427 | 16.250 |
| Borrowed/gathered inputs, checkpoint accounting preserved | 6.951 | 6.562 |

The final executor is 4.89x faster than materialized joins and 2.36x faster than streamed version input in this experiment. PostgreSQL/IRDB are not compared here. These 100k-row memory-only results do not establish 100M-row storage, durability performance or universal superiority.

## Profiling and correctness

The development sampler reads instruction pointers from the owned server child main thread, briefly suspending/resuming it in a finally block. It samples wall time, not PMU CPU events or full stacks. It collects no global Windows trace. `profile-before.json` and `profile-after.json` retain method/sample counts and binary hashes; symbols require matching adjacent PDB files. The baseline had about 39% of samples in memcpy/memmove. The final profile shows these copies largely removed; remaining samples concentrate in join traversal, comparisons and hashing. Profiles and benchmark timing runs were separate.

Reproduce the sampler from the repository root with `python benchmarks/final-joined-read-20261027/source/profile_owned_server.py /absolute/path/ramsql.exe /absolute/path/new-profile.json`. It is an AMD64 Windows development fixture, fixed at 100k rows, 32 workspace rows and 400 checked queries. It does not become part of the Sig runtime.

The final live worker suite passes the existing work-budget UPDATE cancellation/rollback contract, authentication, concurrent read/write exclusion, 192 join/CTE checks, session isolation, 800 concurrent durable updates and restart. Core Windows/no-libc Linux contracts cover old snapshots, pending fact/dimension writes, deletion, NULLs, non-integral fallback and poisoned scratch.

Fact inputs use the existing caller-backed integer projection only under the published-CSN guard. Stored row/version data remains inline. Automatic worker sizing, compact physical SQL storage, per-query elastic grants, extended-protocol parallelism and broad SQL/dialect parity remain open.
