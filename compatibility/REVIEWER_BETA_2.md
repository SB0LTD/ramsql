# Reviewer follow-up: beta.2 candidate

The beta.1 reviewer was right about material production blockers. Its release
was also not published: PR validation built/tested artifacts but intentionally
skipped the workflow's publish job. A successful validation is not publication.
This document tracks implementation and evidence, not production certification.

| Finding | Implemented response | Evidence and remaining work |
| --- | --- | --- |
| One reactor plateaus with clients | Experimental `--workers 2..16` native socket reactors on Windows/no-libc Linux; one shared store, mutex-serialized SQL, caller-owned native thread controls | Four-worker framing/fairness, three independent protocol clients, SCRAM, shared counter contention, restart and running cancel tests. True parallel SQL/MVCC workspaces remain open. macOS/SB0 remain one worker. |
| PK regression between releases | Separate-process ReleaseFast wire benchmark, correct results consumed on every operation; kernel/user CPU retained | Same-host old beta.1 vs runtime-buffer version both plateau around 70k/s at 4–8 clients. No unexplained historical speedup is claimed; reviewer source/scripts are unavailable. Worker measurements are separate. |
| Selective three-way join exhausts scratch | ZPM filters safe predicates as soon as all referenced sources are available in all-inner joins | 5,000 sales, 16 products, 40 shops: a 125-row selection succeeds with workspace 128; literal and prepared predicates checked. Outer joins and unavailable OR predicates retain conservative semantics. General intermediate streaming/hash joins remain open. |
| Workspace embedded in executable | ZPM caller-bound query cells/references; RAMSQL maps workspace pages at startup | Million-row build workspace produces a ~12.8 MB Windows executable, rather than a multi-GB image. Runtime `--work-rows` is independently selectable; query capacity still bounded. |
| Results fixed at 512 | Per-session caller-bound result rows and response pages | 2,000 rows returned through libpq; runtime `--result-rows 4096`; stock default 8,192. No unbounded result stream/spill. |
| Uniform table reservations waste RAM | `--table-rows` and `--table-versions` lists assign table-slot budgets | A 5M-row bank plus 100k/64/small banks calculates about 12.1 GiB backing instead of eight 5M banks. This is sizing math, not a 5M-row load/RSS measurement. A smaller uneven layout passes actual load and durable restart. Inline cells/versions still cost considerable RAM; pooled compact values remain open. |
| INSERT parser ~100 rows | Stock expression budget 512 → 8,192; remains a visible build option | 500-row/four-value INSERTs load 5,000 rows. This increases capacity, not unlimited statements. COPY has separate row/transaction budgets. |
| Large SQL disconnects | PG buffering/source budgets enlarged; authenticated oversized Q/P/B drained up to 16 MiB then SQLSTATE 54000 | 20k prepared source succeeds; 80k SQL fails then SELECT succeeds on the same connection. Malformed/unauthenticated/over-16MiB input can still close. |
| Connections over 16 reset | Runtime slots up to 60; separate bounded startup/cancel admission queue returns FATAL 53300 | 32 active transactions; ordinary/SSL/GSS overflow error checked independently. Admission-queue saturation can still reject sockets; this is not unlimited connections. |
| Runtime PK index mismatch (additional audit finding) | ZPM uniqueness probes use actual bound index length | Duplicate IDs rejected for larger and smaller external indexes; no automatic repair of existing data is claimed. |

## Reproduce the checked paths

```sh
sig build -Doptimize=ReleaseSafe
python tools/test_reviewer_runtime.py
python tools/test_workers.py

sig build -Doptimize=ReleaseFast
python tools/benchmark_pk_scaling.py --binary sig-out/bin/ramsql \
  --clients 1 4 8 --workers 1 4 --operations 20000 --trials 3 \
  --output /path/to/new-results-directory
```

Windows binary paths end in `.exe`. The benchmark uses a separate process per
client, no pipelining, 8,192 rows and 20,000 checked lookups per client. Binaries
are hashed; driver sources and all trials/errors are retained. Run on an idle
host without concurrent builds/profilers. `--capabilities` records build mode and
effective budgets. Single-host wire numbers include OS/client costs and are
not the same boundary as previous native IRDB comparisons.

Use `--workers 1` for the default baseline. More socket workers do not imply
parallel SQL or lower CPU/query. Report throughput, server CPU, memory, tails
and failures together. Do not infer PostgreSQL superiority from a RAMSQL-only
comparison. No new same-boundary PostgreSQL/IRDB victory is established here.

The retained [Windows wire investigation](../benchmarks/reviewer-wire-20261027/README.md)
measures an eight-client median of 69,397 queries/s with one reactor versus
126,598 with four. Server CPU/query rises from about 14 to 27 microseconds.
This throughput/CPU tradeoff does not meet the efficiency goal; more reactors
remain experimental. Both the busy-retry attempt and revised blocking-lock
attempt are retained, together with the earlier old-binary comparison.

Sig 0.6.4's explicit cross-target build path forces libc linkage, and its public
I/O module originally used libc for basic file/clock calls. The no-libc file and
monotonic-clock backend is fixed in Sig; the wall-clock backend is fixed in ZPM.
The product consumes the public Sig runtime through a separate immutable source
archive, based on the same 0.6.4 revision with runtime I/O and explicit no-libc
build-option fixes; the
compiler and its matching standard library remain the verified release.
The SDK preparation helper verifies every installed stdlib source against the
pin and extracts only libraries/build support into a new directory. The source
policy lives in `toolchains/sig-runtime.json`, separate from the ZPM package.
Releases select explicit portable CPU targets with libc disabled on Windows/Linux,
preserving the direct OS stack/TLS thread contract. Native-CPU artifacts that
failed on other x86 runners were rejected by the release gates. Capabilities report
`native_workers_available`; Windows/Linux artifact tests require it. macOS tests
verify that requesting unsupported workers fails explicitly.
`libc_linked` and `cpu_model` record build decisions separately from actual
machine resource discovery.

## Remaining release/production work

Candidate fixes require final hosted/native artifact CI and an actual published
release with checksum-verified assets. Until a release page exists, downloading
alpha.4 does not obtain these fixes. Stable production gates remain closed:
TLS/roles, full dialect/catalog/application compatibility, checkpoint/compaction,
streaming large results/intermediates, compact elastic value storage, sustained
multi-core SQL and operational failure qualification are not completed by this
patch. Schema/text/parser/transaction/version/connection bounds remain explicit
in [CAPACITY.md](../CAPACITY.md).

## Actual million-row capacity check

`tools/test_large_runtime.py` imports one million rows through independent
psycopg COPY in 1,000-row transactions. It checks count/sum/extremes, 1,024 PK
probes and duplicate rejection. The retained
[capacity report](../benchmarks/reviewer-wire-20261027/million-row-capacity.json)
uses a ReleaseSafe Windows binary, one million rows for the first table and
one row for each unused bank. After loading, resident memory was 1,323,704,320
bytes (~1.23 GiB), private commit 2,580,021,248 bytes (~2.40 GiB). This is a memory
load/correctness check, not durable-load evidence or a comparative benchmark.
The previously stated 5M-row layout remains preflight sizing only.
