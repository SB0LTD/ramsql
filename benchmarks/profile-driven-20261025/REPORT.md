# Profile-driven execution work

This pack records measured optimization iterations, including losses. It does
not establish production readiness, complete PostgreSQL compatibility, or
superiority across all workloads. The reviewer scripts remain unavailable.

## Method and source identity

Intel Core i9-14900HX, Ubuntu under WSL2 Linux 6.6.87.2, Sig 0.6.4 with its
matched Zig 0.17.0 compiler (90a898d), ReleaseFast. These are single-client,
in-process public-API measurements with every returned value consumed and
checked against an independent model. Network, authentication and durable flush
costs are outside these native timings. No throughput measurements ran alongside
our builds or profiling. CPU frequency/affinity and unrelated host activity were
not controlled. WSL restarted between the first ordered and permuted series;
each comparison retains both revisions and alternating invocation order.

The alpha.4 engine baseline is ZPM `d99db721beb3ca4f43d7941685a6973f0e608884`.
The benchmark harness adds case isolation and a profiler barrier without
changing the SQL or result model. The first two stages used the harness at
RAMSQL `5deb4d6767dd1f154d6efbf25ff12cff8ef6321f`.

| Stage | ZPM revision | Change |
| --- | --- | --- |
| Word hash | `5f889708f92a93fb2e0b63579b8cbdbaa7bfcea1` | Fixed-word avalanche hashing for primitive keys; existing canonical variable-type hashes retained. |
| Cached top-K | `5b3b5ef62c92526c0bccbb14ea80dc3eff36ec6f` | Bounded caller/stack scratch stores normalized integer keys and heap slot IDs. |
| Physical order proof | `7e565e7b5fea1355ddce70f3d6e7322cd98c464b` | Strict non-NULL leading-column order permits early ordered LIMIT; ties, disorder and changed keys invalidate it. |
| Lazy top-K rejection | `c3a00ab039259e9834e01f04dc91c3107a0484fe` | Losing candidates stop at the decisive key; later columns remain unread. Zero LIMIT returns before heap access. |

`compare_native.py` calibrates each binary/case independently to a minimum trial
duration, then fixes its iteration count. Calibration is retained but excluded
from summaries. The full comparisons have two alternating cycles and five
trials each, with 100 ms ordered / 150 ms permuted calibration targets. All
measured trials exceeded 100 ms. These exploratory samples do not provide
confidence intervals or establish significance for small differences.

## CPU sampling

`profiles/` contains checked target output, compact flat self-CPU reports,
profiler diagnostics and available binary/source metadata. Full perf data and
callgraphs remain under `/home/sb/.cache/ramsql-profiling/`. Each selected
workload was sampled after an acknowledged perf-control barrier; seeding is
excluded except for the import profile. The event is software `cpu-clock:u` at
499 Hz. Hardware cycles/instructions/cache-miss events were unsupported on this
host. No memory-bandwidth or cache-miss measurements are claimed. Recorded
profiles report zero lost samples; several thousand samples per long case are
diagnostic, with inline work attributed to its containing symbol. Profiler
timings are not used as comparative throughput.

Baseline hot spots:

| Case | Main self-CPU attribution |
| --- | --- |
| Import | insertProgress 27%, execute 14%, lexer 12%, DML store 10% |
| Primary key | database body 20%, lexer 16%, parser expression 8%, memcpy 6% |
| Filter | predicate truth 54%, database body 36% |
| Sort | sorter comparison 54%, database body 27%, value comparison 19% |
| Scalar aggregate | query execution 64%, aggregate dispatch 28%, accumulation 8% |
| Group aggregate | query execution 50%, hashing 31%, value comparison 9% |
| Mixed | sorter comparison 38%, database body 30%, predicate truth 16% |

After word hashing, grouped-query hash attribution falls to about 15%; query
execution becomes 62%. This is a shift in sampled proportions, not a separately
measured absolute time for each function. On the permuted candidate sort,
integerTopK accounts for 99.8% of sampled CPU. Scalar aggregate dispatch remains
about 31%, with query execution about 60%.

## Measured iterations at one million rows

The first independent alternating runs show grouped throughput rising from
33 to 39 ops/s with word hashing; point lookup remains effectively tied. Cached
top-K improves the descending-value fixture's sort from 17 to 42 ops/s and mixed
from 49 to 85 ops/s. The raw trials are in `hash-comparison/` and
`topk-comparison/`.

The physical-order comparison uses the same row set and SQL, first with
ascending-ID insertion and then with a deterministic coprime modular
permutation. Its latest harness preserves the answer model and adds insertion
layout selection. Each revision in a pair uses identical harness source.

| Case | Ordered baseline | Ordered candidate | Permuted baseline | Permuted candidate |
| --- | ---: | ---: | ---: | ---: |
| Primary-key lookup | 2,186,381 | 2,133,675 | 2,205,587 | 2,040,936 |
| Filter + ordered LIMIT | 51 | 55,406 | 45 | 48 |
| Sort + LIMIT | 18 | 1,533,133 | 70 | 71 |
| Scalar aggregate | 75 | 62 | 75 | 83 |
| Group aggregate | 35 | 42 | 32 | 42 |
| Mixed (four statements per iteration) | 55 | 194 | 120 | 112 |

Numbers are median operations per second. Million-row import medians across
the measured invocations rise from 688 to 706 ms ordered and 666 to 681 ms
permuted. Import is outside query timing. The allocated backing remains
537,165,824 bytes; database metadata rises from 407,928 to 409,496 bytes. These
are configured storage/metadata sizes, not comparative resident-memory results.

The ordered sort reads only its first 16 qualifying rows in reverse physical
order. That large win is valid for this proven layout and must not be
generalized to arbitrary tables. In mixed queries, the update invalidates value
ordering, so sorting uses top-K. The permuted results retain the point-read and
mixed slowdowns and the variation in scalar aggregation. They motivated the
next rejection-path iteration and longer rechecks.

The final lazy-rejection iteration (`lazy-comparison/`, two alternating cycles,
200 ms calibration) improves permuted sort from 68.8 to 84.9 ops/s compared with
the preceding candidate; mixed moves from 117.3 to 121.7. This is a large sort
gain and a small mixed difference; scalar and grouped timings vary downward in
that series and remain in the data.

`long-recheck/` then compares the original baseline with the final candidate
using four alternating cycles, five trials each and a 500 ms calibration target:

| Case | Baseline ops/s | Final candidate ops/s |
| --- | ---: | ---: |
| Primary key | 2,024,032 | 2,034,603 |
| Scalar aggregate | 70.3 | 72.2 |
| Group aggregate | 34.6 | 40.1 |

Every trial in that recheck lasts at least 512 ms. Point reads and scalar
aggregation are effectively unchanged within the observed spread; the earlier
scalar slowdown does not reproduce as a consistent loss. Grouping shows a
repeatable gain but remains far behind IRDB.

## Fresh external comparisons

`wire-comparison/` compares the final server with PostgreSQL **16.15**, with
fresh ascending-ID fixtures and alternating engine order across two cycles.
Ten checked trials per engine/case include simple-query request, fetch and
digest. Every measured wire trial lasts at least 153 ms. PostgreSQL uses an
unlogged table with synchronous_commit off; RAMSQL uses --memory. These are
not durable-write comparisons.

| Case | RAMSQL wire ops/s | PostgreSQL wire ops/s |
| --- | ---: | ---: |
| Primary key | 13,525 | 11,029 |
| Filter + ordered LIMIT | 6,836 | 5,089 |
| Sort + LIMIT | 7,632 | 24 |
| Scalar aggregate | 36.5 | 62.9 |
| Group aggregate | 24.8 | 34.7 |
| Mixed | 71.9 | 100.0 |

The sort advantage depends on the physical-order proof. Mixed updates invalidate
that value ordering and expose the remaining scan/top-K cost. The server's wider
Cell/storage profile also differs from the native microbenchmark; cross-boundary
throughput is not compared as if only the network changed.

`irdb-comparison/` reruns latest stable IRDB **1.10.821** twice with compiled
import, a clustered ID index, and a calibrated adapter that checks every result.
Ten trials per case give median native-API rates of 3,418 point lookups, 273
filtered LIMITs, 59 sorts, 643 scalar aggregates, 518 grouped aggregates and 95
mixed operations per second. Actual trial durations range down to 70 ms despite
a 150 ms calibration target (JIT/GC/runtime variation); the shorter trials remain
visible. IRDB runs are sequential exploratory comparisons, not interleaved paired
confidence estimates with RAMSQL. The large aggregation deficit is unambiguous;
small cross-run differences should not be treated as decisive.

## Server CPU and durability probes

`profiles/server/` adds actual server sampling, with a separate psycopg client,
64 seeded rows, 100,000 point reads and 3,000 synchronous durable updates.
Read requests take 8.14 profiled wall seconds with 3.15 coarse server user+system
CPU seconds. Updates take 2.66 wall seconds with 0.27 CPU seconds. These include
profiler overhead and are diagnostics, not comparative throughput. Wall minus
CPU includes client, networking, scheduling and waits; it is not a measurement
of fsync latency. Durable restart verifies all 3,000 acknowledged increments.

The user-CPU samples number only 305 for reads and 22 for durable updates, with
zero lost samples. Individual server-function percentages are too sparse for
reliable fine-grained ranking. Unknown instruction addresses and every printed
CPU row are retained; compact reports abbreviate nested type configurations and
the full flat reports are retained as `self-full.txt.gz`. Full perf data remains
on the profiling host. Longer sampling and syscall/off-CPU attribution remain
necessary before changing durability scheduling. This is process-crash recovery,
not a power-loss experiment.

## Correctness and integration

ZPM contracts cover SQL, index, storage, MVCC, change records, types, expression,
query, DML, catalog, external backing, streamed groups, integer top-K and physical
order. The final top-K model executes 3,140 assertions; order proofs execute 108,
including every returned ordered ID, NULL/tie fallback, unrelated/key updates,
abort, stable compaction, copied databases and replay. Database-platform CI run
40237517645 passes Linux, Windows and macOS on final ZPM c3a00ab.

The final RAMSQL ReleaseFast binary passes CLI, 12 TCP lifecycle cases with
3,996 assertions, independent psycopg/PyMySQL/python-tds clients, 8,506 SCRAM
assertions, 58 persistence cases with 1,757 assertions/five crash cycles, and
2,048-row durable reopen/offline restore with fail-closed small-budget recovery.
Permuted 64/512-row native fixtures pass the independent result model. Logs are
in `qualification/`. These checks cover the documented subset and do not certify
full PostgreSQL replacement semantics.

## Remaining work

Large aggregation and unordered filtering remain major gaps. The earlier
matched IRDB comparison remains in the streaming benchmark packs; it is not
silently substituted into this new table. The NuGet version index was checked
again: latest stable remains InMemory.Net.irdb 1.10.821; the response is retained
in `irdb-versions.json` ([primary endpoint](https://api.nuget.org/v3-flatcontainer/inmemory.net.irdb/index.json)).

Next structural targets are compact typed storage/projection, less per-row
aggregate dispatch, and maintained ordered/composite access paths. Physical
order metadata is a conservative shortcut, not a maintained ordered index.
Deeper server CPU and off-CPU waits, flush latency, concurrent clients, cold and
skewed keys, churn and memory residency need separate measurement. Existing
text/schema/session capacities and compatibility gaps are not removed here.
