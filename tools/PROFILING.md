# Native CPU profiling

Build `ramsql-review-bench` with matched Sig/compiler libraries in ReleaseFast.
The normal benchmark arguments and five-trial correctness checks are unchanged.
`--case NAME` selects one workload; `--load-only` selects import. The profiler
uses `--profile-ready`: the benchmark waits for a newline on stdin after seeding
(or before seeding for load), allowing perf to acknowledge recording first.

```sh
python tools/profile_cpu.py --binary /path/to/ramsql-review-bench \
  --perf /path/to/perf --rows 1000000 --iterations 50 \
  --case group_aggregate --output /path/to/new/profile-directory
```

Run cases sequentially without concurrent builds or other benchmarks. Profiles
include parse/bind, execution, result checking and transaction lifecycle within
the selected workload. Seeding is excluded except for `--case load`. Reports,
raw perf data, checked benchmark stdout and binary/source metadata are retained.
Control FIFOs use a retained native Linux temporary directory, allowing a Windows
workspace mounted through WSL. All processes/signals belong to this invocation.

This is a developer-side tool, not a runtime dependency. Use a native Linux perf
binary and its libraries; the WSL kernel version need not have an identically
named distro wrapper. On the current WSL host `cpu-clock:u` works, while cycles,
instructions and cache-miss events report unsupported. Thus these runs measure
sampled user CPU, not hardware cache misses or measured memory bandwidth.

Profiling overhead is included in target runtime. Never report profiler timings
as comparative throughput. Use separate unprofiled runs with identical SQL,
data, versions and correctness checks for before/after measurements. Low sample
counts, lost samples, missing frames and inline attribution must be reported.

Use the duration-calibrated comparison runner for revisions whose query times
differ substantially:

```sh
python tools/compare_native.py --binary baseline=/path/to/baseline \
  --binary candidate=/path/to/candidate --rows 1000000 --minimum-ms 100 \
  --cycles 3 --output /path/to/new/comparison-directory
```

It chooses iterations independently per binary/case, then keeps those counts
fixed across alternating-order cycles. Calibration records are retained and
excluded from the summary. Every query result is checked against the same
independent model. Reports include every trial and its actual duration; inspect
the minimum duration for timer noise. SQL/data remain identical. The fixture
inserts ascending IDs and descending values, with 64 repeating group keys;
ordered-layout wins must not be generalized to unordered data or other queries.
Pass `--permuted` to compare physical layouts without changing the row set or
SQL. Both binaries must use a benchmark harness that supports this option.

For a separate end-to-end comparison (with psycopg installed), use:

```sh
python tools/compare_wire.py --ramsql /path/to/ramsql \
  --postgres-bin /usr/lib/postgresql/16/bin --rows 1000000 \
  --minimum-ms 150 --cycles 2 --output /path/to/new/wire-directory
```

This owns fresh loopback servers/clusters, alternates their order, and measures
SQL request, fetch and digest together. RAMSQL uses volatile memory; PostgreSQL
uses an unlogged table with synchronous_commit off. Neither includes durable
commit guarantees. Native and wire throughput must be compared separately.
The IRDB adapter also accepts `--minimum-ms N` after its two positional arguments;
`benchmark_review.irdb_measure(..., minimum_ms=N)` validates each calibrated
case's actual iteration count. Calibration is excluded from the five trials.

`profile_server_cpu.py --binary ... --perf ... --mode read --iterations 100000
--output ...` samples an owned server after a 64-row seed. Mode `durable-update`
uses a WAL in the output directory and checks every acknowledged increment again
after process restart. Keep WAL/output on a native filesystem when investigating
storage behavior. These diagnostic timings include profiler overhead and client
waits. Inspect sample counts before interpreting percentages; user CPU does not
measure off-CPU flush or network waits. Full flat reports are gzip-retained beside
compact reports, which preserve every printed CPU row.
