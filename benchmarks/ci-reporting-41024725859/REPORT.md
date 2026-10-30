# Original reporting correctness: 7/7, performance gate still fails

Source: [dbMeter CI run 41024725859, attempt 1](https://github.com/benor1470/dbMeter/actions/runs/41024725859/attempts/1).
This bundle retains every file from the completed full and subset live artifacts:
180 raw files, plus nine exact historical validator/configuration files. The
size-scaling/diagnostic/paired artifacts and final all-suite gate are outside this
bundle's completeness scope; inspect the run for their status.

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
All positive/negative trials, histograms, correctness XMLs, environment,
commands, original input hashes, machine-capacity and provenance are retained.

The dbMeter input commit is `2b04159dcc5f1c54bbec13fb5d357f4bbebf97ec`.
Candidate **and control** intentionally use RAMSQL
`ec93758a931e5fb0a6261388e255ff9307a362c6`, with the same immutable archive.
This is correctness plus unchanged-build variability, **not an optimization A/B**.
The reporting engine pins ZPM `2f060c23f1cc6c11b6cc57dc989941b44b5c6442`.
Later bounded-frame candidates cannot inherit these measurements automatically.

`manifest.json` hashes all 189 raw/validator files byte-for-byte; Git attributes
disable text conversion for them. `verify.py` checks those hashes and replays the
exact historical validator against both campaigns. The regenerated summaries
must equal the retained originals and remain `qualified: false`.

```sh
python benchmarks/ci-reporting-41024725859/verify.py
```

The unchanged all-engine gate still requires every declared case and capability,
three trials, at least 2x the faster reference with separated ranges, and p99
within policy. dbMeter PR 1 remains unmerged; no README benchmark-lead claim is
authorized by this bundle. No local throughput measurements were performed.
