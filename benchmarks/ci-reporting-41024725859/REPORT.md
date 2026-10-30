# Original reporting correctness: 7/7, performance gate still fails

Source: [dbMeter CI run 41024725859, attempt 1](https://github.com/benor1470/dbMeter/actions/runs/41024725859/attempts/1).
This bundle retains all three live suite artifacts and the final all-suite
decision: 639 raw files and nine exact historical validator/configuration files.
All benchmark execution jobs succeeded; the final significant-advantage gate
failed. Diagnostic and paired artifacts remain separate from this bundle.

Both original full-5M and subset suites pass **7/7 correctness checks for all
three engines**, including the previously failing temporal formatting, category
rank, window shares and ROLLUP reports. Original Java, SQL, expected CSVs and data
generation were unchanged. Each campaign records original input hashes.

| Full-data mixed case | RAMSQL QPS | PostgreSQL QPS | IRDB QPS |
| --- | ---: | ---: | ---: |
| 1 client | 0.93 | invalid trial; unscored | 34.96 |
| 8 clients | 1.96 | 0.72 | 44.91 |

PostgreSQL trial 1 at one client did not successfully execute the declared q5
scenario. Its raw JSON, timeline and logs are retained; missing evidence is not
counted as a win. RAMSQL remains far behind IRDB on this full mixed workload.
All positive/negative live trials, histograms, correctness XMLs, environment,
commands, original input hashes, machine-capacity and provenance are retained.

The dbMeter input commit is `2b04159dcc5f1c54bbec13fb5d357f4bbebf97ec`.
Candidate **and control** intentionally use RAMSQL
`ec93758a931e5fb0a6261388e255ff9307a362c6`, with the same immutable archive.
This is correctness plus unchanged-build variability, **not an optimization A/B**.
The reporting engine pins ZPM `2f060c23f1cc6c11b6cc57dc989941b44b5c6442`.
Later bounded-frame candidates cannot inherit these measurements automatically.

Size-scaling keeps the genuine read gains and remaining gaps. At one million
rows, top-N throughput is 2.35x / 5.12x the fastest reference at 1 / 8 clients;
PK throughput is 1.08x / 1.14x. At 100k rows, filter throughput is 3.37x / 4.79x
and top-N is 5.94x / 7.14x. These rate ratios do not independently establish the
separate p99/range policy or the all-suite gate. Every original size correctness
check passes, but the stock RAMSQL capability probes fail explicitly:

| Original capability probe | Failure |
| --- | --- |
| 200-byte text | TextCapacity / 54000 |
| 9 columns | ColumnCapacity / 54000 |
| 20 additional tables | TableCapacity / 54000 |

These limits remain open work. Configurable hosted profiles tested elsewhere do
not make the stock benchmark profile elastic; do not raise only the benchmark
profile, remove probes, or call the capability gate closed.

`manifest.json` hashes all 648 raw/validator files byte-for-byte; Git attributes
disable text conversion for them. `verify.py` checks those hashes and replays the
exact historical validator against all three campaigns. The regenerated
summaries and final all-suite decision must equal the retained originals and
remain `qualified: false`, `merge_allowed: false`.

```sh
python benchmarks/ci-reporting-41024725859/verify.py
```

The unchanged all-engine gate still requires every declared case and capability,
three trials, at least 2x the faster reference with separated ranges, and p99
within policy. dbMeter PR 1 remains unmerged; no README benchmark-lead claim is
authorized by this bundle. No local throughput measurements were performed.
