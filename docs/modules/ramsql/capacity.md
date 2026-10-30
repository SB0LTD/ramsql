# Module: `capacity` (machine planning and the server byte ledger)

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/capacity.sig`; ledger use in `ramsql/src/server.sig:374-440, 803-897`; ZPM `src/core/resource_budget.sig`, `src/platform/budgeted_region.sig`, `src/platform/machine_resources.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | RAMSQL (policy) over ZPM core and platform |
| Size | 38 lines, 3.6 KB (`capacity.sig`) |
| Owner lane | `C5` (capacity and ledger verification); `A6` for code structure |
| Status | beta (development feature, not in published beta.2) |
| Formatting (`sig fmt --check`) | dirty (REPO-21; not re-run here). For example, `const std=@import("std");` at 3 |

## 1. Purpose and scope

`capacity.sig` samples the machine (CPUs, physical, commit and address-space
memory, cgroup v2 and Windows job limits, in ZPM `machine_resources`), turns
the sample into a byte plan with RAMSQL's headroom policy (ZPM
`resource_budget.plan`), and prints `--machine-capacity`. `server.sig` binds
one `resource_budget.Ledger` to the sampled limit. It charges every runtime
page region to that ledger: table storage, query workspace, client metadata,
per-client result and response pages, per-reactor read contexts, and a 5 MiB
allowance per worker. The ledger refuses new grants under pressure and never
revokes existing ones. It does not size SQL row capacity (`elastic_sql_storage`
is false) or limit RSS.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Sample` (8) | struct | `observation`, `plan`, `new_region_bytes = plan.process_bytes - max(committed, resident)` (saturating) |
| `sample(hard, headroom, owned)` (9) | fn | probes with a page-backed scratch that doubles on `BufferTooSmall`; retries `ChangedDuringProbe` up to 3 times |
| `describe(hard, headroom, legacy_bytes, read_workers)` (26) | fn | prints the machine JSON to stdout |
| `server.initializeMemory` (`server.sig:383`) | fn | binds the ledger with the startup `new_region_bytes` |
| `server.refreshMemoryBudget` (`server.sig:391`) | fn | at most once per second, sets limit = `used + new_region_bytes`; on probe failure sets limit = `used` |
| `server.clientMemoryAvailable` (`server.sig:401`) | fn | page-rounded result plus response bytes fit in `free` |

## 3. Dependencies and layering

- Imports: `std`, `sig`, ZPM `page_region`, `machine_resources`, `resource_budget`.
- Imported by: `src/server.sig:35` (as `machine_capacity`).
- Layer rule: no violation. Ledger mechanics stay in ZPM; RAMSQL only chooses policy.

## 4. Invariants

- **INV-CAP-1** `ledger.used` equals the sum of live grant bytes. A grant is
  issued only if `used + bytes <= limit` at that moment (ZPM
  `resource_budget.Ledger.acquire`).
- **INV-CAP-2** `refreshMemoryBudget` never sets the limit below `used`
  (`server.sig:394-399`), so existing grants stay valid.
- **INV-CAP-3** Every `Region.reserve` that fails after acquiring a grant
  releases it (ZPM `budgeted_region.sig:12-22`). Every `Client.close` and
  `Reader.close` releases both of its regions (`server.sig:155-161, 196-201`).
- **INV-CAP-4** With all clients closed and all readers idle, `used` equals the
  startup baseline: storage + workspace + client metadata + worker allowance.
- **INV-CAP-5** All plan arithmetic on CLI-accepted inputs either errors or is
  exact (checked adds in `server.sig:397-398, 407-410, 423-424, 826-827, 857-858`).

## 5. Current status and evidence

`tools/test_machine_capacity.py` checks report consistency (storage plus
execution equals the shared pool), `--memory-budget` and `--os-headroom` caps,
startup failure with `BudgetExceeded` and no WAL created, rejection of budgets
0, -1 and 2^64, and PostgreSQL `53200` FATAL at the byte cap with 1 and N
workers. ZPM has `test_resource_budget.sig`, `test_budgeted_region.sig` and
`test_machine_resources.sig`, but RAMSQL's gate does not run them (TST-01).

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SRV-05 | medium | maintainability | the 5 MiB allowance per worker (`server.sig:380`) assumes ZPM 4 MiB thread stacks (`worker_thread.sig:44,46`); nothing ties the two | export the stack size from ZPM; `comptime` assert | A6 |
| SRV-15 | low | completeness | refs = `max_rows * 4` (`server.sig:815`) exceeds ZPM's `0x7fffffff` read-context bound (`sql.sig:386`) above 536,870,911 rows; CAPACITY.md claims 1,073,741,823 | cap rows or size refs independently; fix the doc | C5 |
| SRV-16 | low | robustness | `sample` doubles its scratch on `BufferTooSmall` with no upper bound (10-15) | cap the scratch size and return `ProbeFailed` | A6 |
| SRV-17 | low | completeness | not charged to the ledger: overflow queue (`server.sig:559`, about 4.2 MiB), `recovery_buffer` (361), `Persistence.replay_scratch`, zone cache (104), demo DB (`main.sig:10-11`). They are seen only indirectly through `process_committed` | document as a fixed overhead, or charge it at bind time | C5 |

## 7. Security considerations

- Inputs: `--memory-budget` (nonzero `u64`) and `--os-headroom` (`u64`) from
  argv; OS probe files (`/proc/self/cgroup` and others), which are trusted.
- The admission DoS surface is intended: a client can be refused with 53200
  when memory is short. An unsampled host (cgroup v1, native SB0) is reported,
  not guessed (README).
- A probe failure during admission (`refreshMemoryBudget` error) closes the
  PostgreSQL socket without a 53200 message, because only `BudgetExceeded`,
  `TokenCapacity` and `MemoryReservationFailed` map to it (`server.sig:497-504`).
- Ledger lock: the ledger has its own mutex (ZPM). `memory_mutex` in the server
  also serializes sampling and region ownership.

## 8. Tests

- **Existing:** `tools/test_machine_capacity.py` (section 5);
  `tools/test_capacity.py` (storage beyond the compiled row profile);
  `tools/test_large_runtime.py` (`--table-rows` banks).
- **Missing:**
  - [ ] A3: run ZPM `resource_budget`, `budgeted_region`, `machine_resources` and `page_region` suites in RAMSQL's gate (TST-01).
  - [ ] C5: property test of `storagePlan` and `responseBytes` at the boundaries 0, 1, 0x1fffffff, 0x20000000, 0x3fffffff, 0x40000000 and maxInt for rows, versions, work and result rows (INV-CAP-5).
  - [ ] C5: model-based test with random accept, provision failure, close and reader idle sequences; check `used` returns to baseline (INV-CAP-4).
  - [ ] B6: a failed probe during admission gives 53200, not a silent close.
  - [ ] C5: soak test with the ledger snapshot compared before and after 24 h (STRATEGY L10).

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| FV-09 | Lean (arithmetic) + TLA+ `AdmissionLedger` | INV-CAP-1, INV-CAP-2, INV-CAP-3; no overflow for inputs the CLI accepts | todo |
| INV-CAP-4 | property / soak | no grant leaks across connection churn | todo |
| INV-CAP-5 | property | exact or error for all plan arithmetic | todo |

## 10. Benchmarks

S5 (concurrency scaling) and S6 (scale: bytes per row, RSS) report ledger
`used` against measured RSS. Metric: the gap between `backing_bytes` plus fixed
overhead and RSS. A regression is any unexplained growth in that gap.

## 11. Open questions

- Should the ledger include static process storage so that `--memory-budget`
  is a whole-process bound?
- Should admission use a cached sample, so a slow probe never sits on the
  accept path?
