# IRDB comparison adapter

This is benchmark-only C# code. RAMSQL's runtime remains pure Sig and imports no
.NET or IRDB code. Do not commit or redistribute IRDB assemblies; obtain the
package from NuGet for your own licensed/evaluation use.

The live NuGet flat-container feed reported **InMemory.Net.irdb 1.10.821** as the
latest stable version on 25 October 2026. The web gallery cache still showed
1.10.705, so version selection used the live feed. The downloaded 1.10.821 nupkg
SHA-256 was `9c3b517efdb9a56e46c2c41aa3ea2da10cd67700b20cc845197554f2efb2f90b`.

- Feed: https://api.nuget.org/v3-flatcontainer/inmemory.net.irdb/index.json
- Package: https://api.nuget.org/v3-flatcontainer/inmemory.net.irdb/1.10.821/inmemory.net.irdb.1.10.821.nupkg
- Vendor API reference: https://www.interactivereporting.com/irdb/irdb_programming_reference.pdf
- Vendor platform notes: https://www.interactivereporting.com/irdb/core_notes10.txt

Build with a .NET 10 SDK in Release mode. Keep `packages.lock.json`; restore with
`dotnet restore --locked-mode`, then `dotnet build -c Release --no-restore`.
The initial run used checksum-verified SDK 10.0.402 in an isolated tool cache.
The vendor package currently produces NU1701 for Microsoft.SqlServer.Types;
that restore warning is retained in build logs, not suppressed.

The adapter imports the same integer/category/constant-label data as RAMSQL,
using `InMemoryTable(..., compileTable: true)`. The input DataTable declares the
`id` primary key, and the adapter explicitly creates and builds IRDB's public
clustered index on `id`. RAMSQL uses its primary-key hash index and PostgreSQL
its primary-key B-tree. It warms queries before timing,
uses the public cancellation-token SQL API, and reads the returned column values
directly. It does not inflate IRDB timings with `toDataTable()` conversion or
serialization. Every timed result is checked against an independent model.
SQL UPDATE is tested in memory; this does not establish transactional/WAL parity.

`tools/benchmark_review.py` runs the native RAMSQL and IRDB public-API tier,
then a separate psycopg server/client tier for RAMSQL and native PostgreSQL.
Never compare an in-process number to a server/client number as if the call
boundaries were identical. PostgreSQL's volatile tier uses an unlogged table and
`synchronous_commit=off`; durable tests remain separate.

Example (Linux, non-root, build native binaries first):

```sh
python tools/benchmark_review.py \
  --ramsql /path/to/ramsql --native /path/to/ramsql-review-bench \
  --irdb tools/irdb_bench/bin/Release/net10.0/IrdbBench.dll \
  --dotnet /path/to/dotnet --postgres-bin /usr/lib/postgresql/16/bin \
  --zpm-source /path/to/actual/zpm/source \
  --output /path/to/fresh/report-directory
```

Default sizes are 512, 100,000 and 1,000,000 rows, with five trials per case.
The report preserves capacity failures with no throughput score. PostgreSQL test
clusters are created on a native filesystem under the temp directory and retained
for diagnosis; do not put their data directories on a Windows-mounted WSL path.
The harness never changes an existing PostgreSQL service or database.

These workloads are explicit new experiments. The reviewer's original scripts
were unavailable, so the earlier numbers cannot be presented as directly
reproduced. Passing these cases does not establish production readiness or
universal superiority over IRDB/PostgreSQL.
