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
