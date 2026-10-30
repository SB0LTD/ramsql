# Benchmarks

Latest CI-only matched comparisons:
[dimension group IDs](ci-dimension-ids-40982373535/REPORT.md) and
[typed aggregation on the main configuration](ci-typed-main-40976650837/REPORT.md).
Each preserves all 24 A/B cases and six fresh correctness checks with its exact
historical validator. The dimension experiment improves mixed throughput in every
pair, but its separate three-engine comparison still trails IRDB. The unchanged
full-suite gate remains unqualified. Older local measurements below are historical.

Latest repeated screening and unchanged-build controls:
[projection, operator specialization and exact byte-domain experiments](dbmeter-follow-up-20261028/REPORT.md).
All three remain draft experiments; their measured ranges do not establish
the required significant advantage. Complete positive/negative trials are retained.

Latest full-data profiling campaign:
[streaming execution on the reviewer's actual five million rows](dbmeter-streaming-20261028/REPORT.md).
It retains the remaining unsupported checks and a slower experimental result;
the complete dbMeter advantage gate has not passed.

Latest infrastructure experiment:
[capacity-safe streaming aggregation, IRDB 1.10.821 and PostgreSQL](streaming-budget-20261025/REPORT.md).
It includes separate native/wire tiers, raw correctness-checked trials, fixed
group-scratch evidence and a fresh preserved-binary baseline. Production and
all-workload performance targets remain open.

## Durable comparison with native PostgreSQL

[Current durable measurements](durable-boundary.json) compare the selected shared SQL
subset using identical psycopg simple queries, 512 rows, synchronous commits,
1/4 clients and three trials. Individual request p50/p95/p99 timings, correctness
checks, durability settings and binary/source provenance are recorded.

Both engines run as native Linux processes under WSL2 with sibling data
directories on the same ext4 filesystem. PostgreSQL reports fsync,
synchronous_commit and full_page_writes all on; RAMSQL synchronizes every commit.
The [baseline](durable-boundary-baseline.json) uses the actual published alpha.1
Linux binary, whose archive provenance and binary hash are retained in the report.
All results and post-kill WAL recovery are checked.

| Workload | Clients | Alpha.1 requests/s | Alpha.2 requests/s | Alpha.2 / PostgreSQL |
| --- | ---: | ---: | ---: | ---: |
| CREATE/DROP | 1 | 999 | 1,223 | 1.52 |
| CREATE/DROP | 4 | 958 | 906 | 0.49 |
| INSERT | 1 | 988 | 1,095 | 0.52 |
| INSERT | 4 | 698 | 1,370 | 0.34 |
| Point UPDATE | 1 | 503 | 1,115 | 0.53 |
| Point UPDATE | 4 | 1,063 | 1,234 | 0.30 |
| Point SELECT | 1 | 10,581 | 11,146 | 1.20 |
| Point SELECT | 4 | 1,231 | 1,114 | 1.00 |
| Scan LIMIT 16 | 1 | 7,105 | 6,628 | 1.10 |
| Scan LIMIT 16 | 4 | 546 | 527 | 1.03 |

These are medians of three trials of 1,000 operations, not guarantees. The two
full experiments ran sequentially and exhibit host/storage variability, visible
in the retained individual timings and reference-engine results. They do not
isolate a causal speedup for every workload. Reopening and validating the same
2,169,648-byte WAL took 698 ms for alpha.1 and 27 ms for alpha.2 on this host.
PostgreSQL still leads durable writes; the compatibility gate also still fails.
These measurements do not establish production PostgreSQL replacement performance.

```sh
sig build -Doptimize=ReleaseFast
python tools/benchmark_postgres.py --postgres-bin /usr/lib/postgresql/16/bin --work-dir /var/tmp/ramsql-benchmark --operations 1000 --trials 3 --clients 1 4
```

## Historical in-memory baseline (0.2.0)

Measured with Sig 0.6.3 / ReleaseFast on Intel(R) Core(TM) i9-14900HX, Windows, using a 512-row fixed-capacity table. Each wire configuration ran 3 trials of 10,000 checked queries after warmup. Native cases use 50,000 point SELECTs or 5,000 scans/updates per trial.

| Workload | Median throughput |
| --- | --- |
| Native primary_key_select | 5,274,818 ops/s |
| Native scan_limit_16 | 6,202,704 ops/s |
| Native point_update | 2,724,350 ops/s |
| PostgreSQL wire, 1 connections, pipeline 1 | 23,392 queries/s |
| PostgreSQL wire, 1 connections, pipeline 16 | 112,909 queries/s |
| PostgreSQL wire, 1 connections, pipeline 64 | 144,875 queries/s |
| PostgreSQL wire, 8 connections, pipeline 1 | 36,996 queries/s |
| PostgreSQL wire, 8 connections, pipeline 16 | 148,688 queries/s |
| PostgreSQL wire, 8 connections, pipeline 64 | 166,203 queries/s |

The wire figures include loopback TCP, Python framing/decoding, and correctness checks. Batch-amortized times are not individual-query latency percentiles. Process memory accounting is in the JSON report. These are small single-host measurements, not comparisons with PostgreSQL/MySQL/SQL Server or a claim of production-scale throughput.

Reproduce after building both binaries with ReleaseFast:

```sh
sig build -Doptimize=ReleaseFast
sig build benchmark -Doptimize=ReleaseFast
python tools/benchmark.py --operations 10000 --trials 3 --build-mode ReleaseFast --output benchmarks/local.json
```

[Full machine-readable report](windows-local-512rows.json) includes binary/source hashes, compiler version, checksums, trial rates and latency distributions. The historical pre-index capture lacks full environment metadata and is labelled separately.
