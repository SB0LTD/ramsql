Adds LAG/LEAD with typed offsets/defaults, lazy target/default evaluation,
partition navigation and bounded root-query demand. Corrects nullable partition
ordering and adds native PG16/PG18/binary/parameters/sequence/durable regressions.
Nested and uncertain demand for throwing/volatile arguments remain explicitly
unsupported; the complete SQL and window inventories remain open.
