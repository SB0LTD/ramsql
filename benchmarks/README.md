# Benchmarks

## Durable comparison with native PostgreSQL

[Durable measurements](postgres-comparison.json) compare the selected shared SQL
subset using identical psycopg simple queries, 512 rows, synchronous commits,
1/4 clients and three trials. Individual request p50/p95/p99 timings, correctness
checks, durability settings and binary/source provenance are recorded.

The reference PostgreSQL runs inside Docker Linux; RAMSQL runs as a native Windows
process. Storage, VM and networking costs differ. These results do not establish
PostgreSQL drop-in performance: the compatibility gate still fails, and several
four-client write measurements favor PostgreSQL.

```sh
sig build -Doptimize=ReleaseFast
python tools/benchmark_postgres.py
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
