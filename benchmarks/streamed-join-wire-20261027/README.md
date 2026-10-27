# Checked streamed-join comparison

One client, in-memory server, 100,000 fact rows, 16 shops and 8 products. The query returns five grouped results, including NULL grouping; the data includes missing keys and NULL keys/values. Every response is checked against an independent Python integer model.

Both Windows binaries use the same RAMSQL runtime source and portable ReleaseFast options, with different immutable ZPM dependencies. Both receive identical SQL and `--work-rows 32768 --result-rows 16` plus unequal table-bank capacities. Setup/import and three warmups are excluded from timing. Three trials alternate engine order; each trial checks 100 queries. Source harnesses, binary hashes, CPU, memory and all trials are retained in `results.json`.

| Executor | Median wire ms/query | Median server CPU ms/query |
| --- | ---: | ---: |
| Previous materialized joins | 34.55 | 34.06 |
| Streamed primary-key joins | 16.07 | 15.47 |

The candidate is 2.15x faster in this RAMSQL-to-RAMSQL experiment and uses roughly half the CPU time. PostgreSQL and IRDB are not compared here. This does not establish a 100M-row capacity, durability, production-scale or universal performance claim.

A separate live regression loads the identical fixture with only 32 workspace rows and checks both one and four workers. The previous binary returns PostgreSQL 54000; the candidate returns all correct groups and remains usable. The reusable ZPM contract also binds just 512 query handles and 1,024 workspace cells at 100,000 rows, verifying scratch cardinality at 512 and 100,000 input rows.

Unsupported shapes still use bounded materialization. High group cardinality can exhaust caller storage. Stored data remains in the inline SQL layout; this change does not integrate packed base columns or growable physical storage.
