# Verification strategy

How RAMSQL proves that it works. Each level below has an owner lane in
[`PLAN.md`](../../PLAN.md), a location in the target tree and an oracle (what
decides pass or fail). A test without an oracle is not a test.

## 1. Principles

1. **Silent wrong results are the worst failure.** A database that errors is
   annoying; one that returns a plausible wrong answer is dangerous. Oracles
   that catch wrong answers (differential, metamorphic, model-based) get
   priority over oracles that only catch crashes.
2. **Every claim has a test id.** README, `SQL_REFERENCE.md` and module docs
   link each supported feature to the test that proves it.
3. **Tests execute assertions.** Each contract binary prints the number of
   assertions it executed, and the runner fails if the count is below the
   recorded minimum. This prevents a refactor from turning a suite into a no-op.
4. **Deterministic first.** All randomized tests take a seed, print it, and
   reproduce from it. CI uses fixed seeds plus one fresh seed per run, and the
   fresh seed is logged.
5. **ReleaseSafe everywhere tests run.** Sig's bounds and overflow checks act as
   the sanitizer. Fuzzers run in ReleaseSafe and Debug.
6. **Fast paths are tested against a reference path.** Every specialized
   executor (streamed joins, dense groups, typed factors, synopses, compact
   scans) must agree with the general executor on the same input.

## 2. Levels

| Level | What | Oracle | Location (target tree) | Lane |
| --- | --- | --- | --- | --- |
| L0 static | `sig fmt --check`, `sig ast-check`, forbidden-pattern lint (heap allocators, `catch unreachable` on untrusted input, `@panic` in server paths), doc presence, license headers | tool exit code | `tools/ci/` | A4 |
| L1 contract | per-module Sig executables | assertions plus minimum assertion count | `tests/contracts/` | all |
| L2 property | generated inputs from a seeded PRNG, with shrinking of operation sequences | algebraic laws (round trip, commutativity, idempotence) | `tests/property/` | B7, C1 |
| L3 model-based | the same operation sequence on the implementation and on an executable model | model state equals implementation state | `tests/model/`, `formal/lean/` | C1–C5 |
| L4 fuzz | coverage-guided and structure-aware fuzzing of every parser of untrusted bytes | no crash, no safety-check trap, round trip where defined | `tests/fuzz/` | B1, B2 |
| L5 integration | real binary over sockets: CLI, lifecycle, auth, persistence, workers | pytest assertions | `tests/integration/` | A3 |
| L6 client compatibility | psql/libpq, psycopg 3, asyncpg, JDBC, pgx, node-postgres, SQLAlchemy, Django; PyMySQL; python-tds, pyodbc | driver-level assertions | `tests/clients/` | B7 |
| L7 semantic differential | sqllogictest corpus, PostgreSQL regression subset, SQLancer-style TLP and NoREC oracles | result equals PostgreSQL 16 and 18, or a documented, tested divergence | `tests/differential/` | B7 |
| L8 transactional | deterministic simulation of many clients against `Store`, plus Elle-style history checks | no READ COMMITTED anomaly (G0, G1a, G1b, G1c); no lost update where PostgreSQL would block | `tests/simulation/` | B8 |
| L9 crash and fault | crash at every WAL write boundary; injected EIO, ENOSPC, short write, fsync failure; torn writes; LazyFS power loss on Linux | recovered state is a committed prefix; fail closed on corruption | `tests/crash/` | B9 |
| L10 soak | 24 h mixed churn under a memory budget, cancel storms, client kills | no leak in the byte ledger, no growth in latency tails, no crash | `tests/soak/` | B9, C7 |
| L11 performance | suites S1–S10 | thresholds in the benchmark methodology | `bench/` | C6, C7 |
| L12 formal | Lean 4 proofs, TLA+ model checking, trace validation | proof checks; TLC finds no violation; recorded traces refine the spec | `formal/` | C1–C5 |

## 3. Deterministic simulation (L8 and L9 together)

RAMSQL is a good fit for FoundationDB-style deterministic simulation: the
codecs are socket-independent `Session.handle(input, output)` state machines,
`Store` takes `now_ns` from its caller, and storage is caller-owned. Lane B8
builds a single-process simulator that:

- runs N simulated clients, each a seeded script of SQL statements, through the
  real PostgreSQL codec and the real `Store`;
- chooses the next client to step with a seeded scheduler, including
  `WouldBlock` parking and wake-ups;
- drives a simulated clock (lock timeouts, statement timeouts, deadlock checks);
- writes to a simulated durable file that can lose unflushed bytes, tear the
  last write, or fail with EIO or ENOSPC at a chosen point;
- records a history (invocations, results, commit order) and checks it against
  the transactional oracle and against the Lean or TLA+ model.

A failing seed is a complete reproduction. Store the seed and the minimized
history as a regression test.

## 4. Coverage and test adequacy

- Line and branch coverage of contract and property suites, collected on Linux
  with kcov, reported per module. Targets: 90% for parsers of untrusted
  input, SCRAM, durable log, MVCC and locks; 75% elsewhere.
- Mutation testing on the security- and durability-critical modules: a script
  flips comparisons, off-by-ones and boolean conditions one at a time. Each
  surviving mutant is either killed by a new test or justified in the module
  doc.

## 5. Flaky tests

A flaky test is a bug. Quarantine it within one day with a tracking issue,
and fix or delete it within one week. No retry loops in test code to hide
flakiness. Integration tests take ports from the OS (bind to port 0), never
use fixed sleeps, and wait on readiness signals with explicit timeouts.
