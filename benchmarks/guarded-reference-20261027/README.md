# Guarded-input IRDB comparison: initial cases

Checked native SQL API replay at 100,000 rows, 1,024 categories and nullable
96-byte payloads. This is a volatile, single-worker experiment on Windows with
32 logical CPUs; only one worker is used. The ZPM dependency is the immutable
`19e0ce28fe4a85383c57b23858818a2bcca6c3d2` archive in the retained manifest.
IRDB is `InMemory.Net.irdb` 1.10.821 on .NET 10.0.14, with compiled import and a
built clustered primary-key index. Its public SQL API consumes the identical trace.
Both adapters submit SQL strings on each call; this is not a prepared-query
comparison. IRDB exposes `prepareQuery`, and that separate path still needs a
matched experiment before interpreting the large short-query differences.

| Case | RAMSQL median ops/s | IRDB median ops/s | Ratio |
| --- | ---: | ---: | ---: |
| Filtered COUNT/SUM | 40,950 | 1,098 | 37.3x |
| Ordered grouped COUNT/SUM | 5,350 | 567 | 9.4x |
| 95% point reads / 5% updates | 1,453,970 | 1,012 | 1,436x |

The very large mixed-workload gap warrants independent review. The trace really
changes values, checks the changed row, and restores the original value; it is
not a repeated no-op update. It does not establish transaction or WAL parity.
No claim is made about why the reference engine is slow without profiling that
engine. These results cover these SQL requests and adapters, not every engine
configuration or production workload.

Two process cycles alternate engine order. Each process warms up for at least
one second, then records five trials lasting at least 200 ms. Every timed result
is consumed and checked against an independent model. Reported medians use all
ten trials per engine. Latency fields are histogram bucket upper bounds, not
exact quantiles. No local builds or profilers ran during measurement.

`results.json` retains fixture hashes, commands, environment, correctness,
individual trials, source hashes and compiler/binary identities. `source/`
retains the actual replay/model sources and manifest; raw process logs are here.
The live official NuGet version response is retained in `irdb-version-check.json`.
Trace files remain local and are reproducible from the seed and source model;
they are excluded from Git to avoid repeating the same 10 MB fixture per case.

Reproduce from the repository root with built binaries (use a fresh output path):

```sh
python tools/benchmark_production.py \
  --native /path/to/ramsql-production-bench \
  --dotnet /path/to/dotnet --irdb /path/to/IrdbBench.dll \
  --compiler /path/to/sig \
  --rows 100000 --groups 1024 --payload-bytes 96 --requests 128 \
  --minimum-ms 200 --cycles 2 \
  --case filtered_sum --case grouped --case mixed_95_5 \
  --output /path/to/fresh/results
```

The [remaining cases](../guarded-reference-remaining-20261027/README.md) complete
this harness's ten-case matrix at the same scale. Neither experiment qualifies
100M-row tables, multiple large tables, joins, network throughput, all-core
scaling or durable performance. Those remain separate acceptance requirements in
the [capacity plan](../../compatibility/MACHINE_CAPACITY_AND_100M_PLAN.md).
