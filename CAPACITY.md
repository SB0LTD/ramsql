# Capacity and admission planning

Run `ramsql --capabilities` for the binary you actually deploy. Add `--rows N
--versions N` to inspect prospective hosted backing storage without allocating it,
creating/opening a WAL or listening. This is a configuration report, not a full
query planner, RSS forecast or admission guarantee for every SQL statement.

## Independently sized budgets

| Resource | Stock default | Control / behavior |
| --- | ---: | --- |
| Stored rows per table | 4,096 | `--rows`, or per-slot `--table-rows n,n,...` |
| Versions per table | 8,192 | `--versions` or `--table-versions n,n,...`; must exceed that slot's rows |
| Returned rows per statement | 8,192 | Runtime `--result-rows`; separately page-backed per accepted client |
| Query workspace rows | 4,096 | Runtime `--work-rows`; shared execution scratch, not disk-spilled or embedded in executable |
| Tables | 8 | Build `-Dtables` (SQL catalog maximum 255) |
| Columns per table/result | 8 | Build `-Dcolumns` (maximum 255); larger schemas are not silently truncated |
| TEXT/JSON/encoded JSONB bytes | 128 | Build `-Dtext-bytes` (1..65,535); bytes, not Unicode characters |
| Identifier bytes | 63 | Fixed server policy |
| Client slots | 16 | Runtime `--max-clients` 1..60; build `-Dclients` changes the default |
| Transaction slots | 62 | Maximum hosted clients + 2 |
| Socket reactors | 1 | Experimental `--workers` 1..16 on Windows/no-libc Linux; <= clients; shared SQL remains serialized |
| Changes per transaction | 4,096 | Build `-Dtx-writes`; version/mark/catalog entries also consume budget |
| Encoded change/recovery bytes | 1 MiB | Build `-Dtransaction-bytes`; minimum 65,540 |
| Explicit row locks, server-wide | 256 | Build `-Drow-locks`; 8 SHARE holders per row remain fixed |
| WHERE boolean leaf predicates | 8 | Build `-Dpredicates` |
| Statement expression nodes | 8,192 | Build `-Dexpr-nodes`; parser depth remains 64 |
| FROM items per query block | 4 | Build `-Drelations` |
| Query blocks / CTEs per statement | 16 / 4 | Current SQL Config defaults, not server CLI controls |
| Savepoints per transaction | 32 | Current SQL Config default |
| Sequences | 16 | Current SQL Config default |
| NUMERIC significant digits | 64 | Current SQL Config default; not PostgreSQL arbitrary precision |
| JSON nesting | 64 | Current SQL Config default |
| Named timezone cache | 8 x 16 KiB | Build `-Dtimezones` 1..256; active references prevent eviction |
| Exact group synopsis entries/column | 64 | Build `-Dsummary-groups`; overflow falls back, not a group-count limit |
| Compact integer projection | enabled | Build `-Dcompact-integral-scans=false` to disable; current external-storage implementation requires <=64 columns |

`-Drows` sets storage and workspace defaults unless workspace is supplied
explicitly; the result default is independent. Hosted `--rows` changes stored rows only.
Other SQL Config fields can be selected by embedded callers; editing a library
Config is not a runtime tuning mechanism for a published server binary.

## Fixed protocol and input bounds

| Resource | Bound |
| --- | ---: |
| Reactor input buffer per connection | 65,536 bytes |
| PostgreSQL normally buffered packet | 65,535 bytes including length; framing overhead reduces SQL payload |
| PostgreSQL prepared SQL source | 65,536 bytes; packet/framing bound applies first |
| Named prepared statements / portals | 8 / 8 per PG session |
| PG bound parameters / TDS RPC parameters | 32 / 32 |
| MySQL/TDS assembled requests | 65,536 bytes, including their framing constraints |
| Store statement buffer | 65,536 bytes; stricter codec bound can apply first |
| COPY partial data row | 8,192 bytes |
| `--stdin` line | 4,096 bytes; separate 128-row/8-column demo library profile |
| User/zone label storage | 64 bytes; auth user and zone lookup have additional validation |
| Process argument scratch / path buffers | 8,192 / 4,096 bytes |
| Idle timeout | 30,000 ms default; `--idle-ms` must be positive |
| Execution checkpoints | `--max-query-work`, 0 disables; not a latency guarantee |

Response buffers are computed from the selected result/column/value profile;
`output_bytes_per_client` reports the actual bound. Whole statements/results are
bounded; portal suspension does not turn execution into an unbounded result
stream. Authenticated SQL Q/P/B frames exceeding the buffer are drained with
bounded storage up to 16 MiB, then return SQLSTATE 54000; simple queries remain
usable and extended queries recover at Sync. Larger, malformed or unauthenticated
frames can close with a protocol error. A full SQL pool returns PostgreSQL FATAL
53300 through a separate bounded startup/cancel queue; saturating that queue can
still cause connection rejection. Budget errors generally use SQLSTATE 54000/54001/53200; overlong
typed values may use their type-specific state. Statement rollback and ownership
must remain intact on SQL errors. These limits can reject valid native-PostgreSQL
workloads and are production blockers, not only documentation details.

## Runtime storage and memory

Hosted rows must be positive and <=1,073,741,823; version/reference counts must
fit <=2,147,483,647. Arithmetic, OS mappings and available memory impose lower
practical ceilings. No tested capacity near these representational ceilings is
claimed. All slots reserve their configured capacity. Per-table lists avoid
giving every table the largest reservation, but this is not automatic elasticity.
Lists must contain exactly one entry per compiled table slot; slot assignment
follows CREATE TABLE order and should remain identical for WAL recovery.
In asymmetric mode `rows_per_table`/`versions_per_table` report maxima; inspect
`table_rows`/`table_versions` arrays for the actual layout.

Cells currently carry inline value storage. Versions, index arrays, per-client
results/protocol buffers, transaction buffers and workspace can dominate memory.
`backing_bytes`, `query_workspace_bytes`, `result_bytes_per_client`,
`output_bytes_per_client` and `client_metadata_bytes` report separate costs.
Result/response pages are mapped only for accepted clients, then released.
Query scratch is one separate reservation; a million-row workspace no longer
adds gigabytes to the executable. OS reservation/commit policy still applies.
Increasing columns/text/results/clients together multiplies costs. Fixed process
storage, stack, code, OS socket buffers and page alignment are **in addition** to
the reported backing bytes. Debug/large profiles can also exceed platform stack
or address-space budgets. Measure the exact profile with realistic queries.

The four-column compact integer projection adds about 41 backing bytes per row
plus alignment. It improves narrow scans by trading memory and publication work
for locality. It is not compact general-purpose text/JSON storage. A group
summary overflow falls back to scanning; workspace/result exhaustion still fails.

COPY FROM is one transaction: row capacity, versions, write-set entries and
encoded transaction bytes must all suffice. Long-held snapshots delay version
reclamation. Recovery/backup replay needs enough capacity for historical peaks,
not merely the final row count. WAL grows without checkpoint/compaction.

For the production scaling plan and tests needed to replace fixed-budget failure
with bounded streaming/admission control, see [ROADMAP.md](ROADMAP.md).
