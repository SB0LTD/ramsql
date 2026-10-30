# Formal verification program (Lean 4 and TLA+)

Lanes **C1–C5** in [`PLAN.md`](../../PLAN.md) carry this out. The aim is not to
verify the whole Sig code base. No verification tool understands Sig source
today. The aim is to:

1. write precise mathematical models of the parts where a subtle bug means
   wrong answers, lost data or an authentication bypass;
2. prove the properties that matter on those models (Lean 4) or exhaustively
   model-check them on bounded instances (TLA+ with TLC);
3. **connect every model back to the real code** so a proof cannot silently
   drift from the implementation.

A proof about a model that nothing checks against the code is documentation,
not verification. Every catalog entry below says how it is connected.

## 1. Tools and layout

```
formal/
  README.md                 how to build, status table, axioms list
  lean/
    lean-toolchain          exact stable Lean 4 release, pinned at lane start
    lakefile.lean           depends on Batteries only; Mathlib only if a lane justifies it
    Ramsql/
      Sql/ThreeValued.lean  Sql/IntArith.lean  Sql/Numeric.lean  Sql/Like.lean
      Sql/Literal.lean      Sql/Temporal.lean  Sql/Order.lean    Sql/Aggregate.lean
      Txn/Mvcc.lean         Txn/Reclaim.lean   Txn/Locks.lean
      Wal/Frame.lean        Wal/Recovery.lean
      Proto/Framing.lean    Proto/PgSession.lean  Proto/Scram.lean
      Capacity/Ledger.lean
    Vectors/Main.lean       `lake exe vectors <family>` emits JSON test vectors
    Oracle/Main.lean        `lake exe oracle` reads JSON inputs on stdin, prints model outputs
  tla/
    MvccReadCommitted.tla   Reclamation.tla  RwGate.tla  WalCommit.tla
    AdmissionLedger.tla     LockManager.tla  TraceMvcc.tla  *.cfg
    tools.lock              tla2tools.jar version and SHA-256
  vectors/                  small generated vector files consumed by Sig contracts
```

- Lean: `lake build` must succeed with no `sorry`, `admit` or undeclared
  `axiom`. CI scans for them. The only allowed axioms are listed in
  `formal/README.md`, for example "a completed fsync makes the preceding writes
  durable". Each one has a one-paragraph justification.
- TLA+: TLC runs from a pinned `tla2tools.jar` (hash in `tools.lock`) on Java 21,
  which is already installed on the development host. Each spec has a small CI
  configuration (seconds) and a larger nightly one (minutes to an hour).
  Apalache is optional for inductive invariants.

## 2. Connecting models to code

| Mechanism | Used for | How it works |
| --- | --- | --- |
| **Vectors** | pure functions | `lake exe vectors <family>` emits input and expected-output pairs, including edge cases the proof identified (boundaries, overflow, NULLs). A Sig contract in `tests/contracts/vectors_<family>.sig` replays them through the real ZPM function. A vector mismatch means the model or the code is wrong. |
| **Oracle** | pure functions with large input spaces | a Python property driver generates random inputs, sends them to `lake exe oracle` and to a small Sig harness (`ramsql-oracle`) built from the same ZPM functions, and compares the outputs. Mismatches are shrunk and saved as vectors. |
| **Enumeration** | state machines | Sig tests enumerate all message sequences up to length k from the model's alphabet, drive the real codec, and check that each observable transition matches the model. |
| **Trace validation** | concurrency and durability | ZPM `Store` and `durable_log` emit an event trace when built with `-Dtrace=true` (comptime-gated, zero cost otherwise). The simulation harness (lane B8) records traces, and `TraceMvcc.tla` checks with TLC that each trace is a behavior of the spec. |
| **Crash replay** | WAL recovery | the crash harness (lane B9) produces cut points and torn frames. The Lean recovery function predicts the recovered state, and the real binary must match it. |

## 3. Theorem catalog

Status values: `todo` → `stated` (definitions and statement checked in) →
`proved` → `linked` (connected to the code by the listed mechanism). Tier 1 is
done first.

### Tier 1

| Id | Area | Statement | Model | Link | Lane |
| --- | --- | --- | --- | --- | --- |
| FV-01 | three-valued logic | SQL `AND`/`OR`/`NOT` over {T, F, U} satisfy the Kleene laws, including De Morgan. `WHERE` keeps exactly the rows whose predicate is T. `x = NULL` is U for every x. | Lean | vectors (full truth tables, comparison operators against NULL) | C1 |
| FV-02 | integer arithmetic | For int2, int4 and int8, `+ - * / %` and unary minus and `abs` return the exact mathematical result when it is in range, and SQLSTATE `22003` otherwise. Never a wrapped value. Division truncates toward zero, `%` takes the sign of the dividend, division by zero gives `22012`. | Lean | vectors at every boundary (min, max, -1, 0, 1) plus oracle | C1 |
| FV-03 | parameter substitution | For every accepted statement template S and parameter values v: `tokens(interpolate(S, v)) = tokens(S)` with each placeholder `$i` replaced by exactly one literal token whose value is `v[i]`. This covers string quoting (`pg_types.quote`, `postgres.quoted`), integer rendering (`Writer.number`, bare text today), the placeholder scanner (`postgres.scan`) and TDS `expand`. It is the "no injection through bind parameters" theorem. **It is false today** (finding NET-01: `bal-$1` with `-5` lexes as a `--` comment). Once fixed by binding typed values instead of splicing text, the theorem becomes "the bound value equals the decoded parameter". | Lean (lexer token fragment, scanner, renderer) | oracle with random templates and values, including negative numbers after `-`, `'`, `\`, `$`, `--`, nested `/* */` | C1 |
| FV-04 | LIKE with ESCAPE | The implementation's matcher agrees with the declarative language of the pattern (`%` is any string, `_` is one character, escape semantics, error on a trailing escape). Running time is O(len(text) x len(pattern)). | Lean | oracle plus vectors | C1 |
| FV-05 | WAL recovery | Records r1..rn are appended; acknowledgement of ri happens only after its fsync. A crash can damage only the unsynced suffix (truncated, zeroed, garbage or out-of-order sector writeback). Recovery then returns exactly `apply(r1..rk)` for some k ≥ the number of acknowledged records, and the in-flight record is applied all or nothing. Damage confined to the unsynced suffix never prevents startup (today it does: STO-05). Damage to synced bytes fails closed. Recovery is idempotent: running it twice leaves the file byte-identical. | Lean | crash replay | C3 |
| FV-05b | replay refinement | For every schedule of concurrent transactions (DDL, DML, `nextval`, savepoints, aborts), the published in-memory state equals `replay(WAL)`. Out-of-order DDL commits (STO-01) and sequence log-ahead (STO-04) are the known counterexamples today. | Lean model + TLA+ `WalCommit` | random schedules through the simulator, compared with `sameDatabase` | C3 |
| FV-06 | flush before publish | In every reachable state, a published or acknowledged transaction is durable. After a write or flush failure, no further transaction is published until recovery. | TLA+ `WalCommit` | trace validation | C3 |
| FV-07 | READ COMMITTED visibility | Each statement sees exactly the versions committed before its snapshot plus its own transaction's writes. No dirty read (G1a), no intermediate read (G1b), no dirty write (G0). A writer that meets a concurrent update blocks, then re-evaluates the row (EvalPlanQual) as PostgreSQL does, so no update is lost. | TLA+ `MvccReadCommitted` + Lean `Txn/Mvcc` | trace validation, Elle-style history check | C2 |
| FV-08 | reclamation safety | A version is reclaimed only when no snapshot can see it, whether pinned **or currently running** (resumed statements and COPY included). It is false today: `horizon()` ignores running snapshots (STO-02). | TLA+ `Reclamation` | trace validation | C2 |
| FV-09 | admission ledger | Outstanding grants never exceed the budget. Every failed admission or page commit returns its grant. Ledger arithmetic cannot overflow for inputs the CLI accepts. | Lean (arithmetic) + TLA+ `AdmissionLedger` (concurrent workers) | property tests on `capacity.sig` and server admission | C5 |
| FV-10 | PostgreSQL session | No Query, Parse, Bind, Execute, Describe, Close or COPY message is executed before authentication completes. After an ErrorResponse in extended mode, messages are discarded until Sync. At most one suspended portal exists. SASL messages are accepted only in the authentication phase. | Lean `Proto/PgSession` | enumeration to depth 6 | C4 |

### Tier 2

| Id | Area | Statement | Model | Link | Lane |
| --- | --- | --- | --- | --- | --- |
| FV-11 | numeric | NUMERIC as (mantissa, scale): addition and subtraction are exact, multiplication scale is s1 + s2, division follows PostgreSQL's scale rule, and `ROUND(x, s)` rounds half away from zero. A result outside the configured digit capacity is an error, never a truncated value. | Lean | vectors plus oracle | C1 |
| FV-12 | temporal | days-from-civil and civil-from-days are inverse bijections on the supported range. Month arithmetic clamps to the end of the month as PostgreSQL does. The leap-year rule is Gregorian. **No overflow:** every input that passes the range guards keeps every intermediate value strictly inside i64, otherwise the function returns `22008`. This is false today: `y = 294277, m = 1, d = 10` passes the guard and overflows (SQLF-01). | Lean | vectors at every guard boundary, plus oracle | C1 |
| FV-13 | ordering | The comparator is a strict weak order for every type, with NULLS FIRST/LAST and DESC. Sort output is a sorted permutation. Top-k equals the first k of the full sort under the same tie rule. | Lean | oracle | C1 |
| FV-14 | factorized aggregation | For primary-key inner joins, aggregating per group over the join equals aggregating the factorized form (SUM distributes over matching multiplicities, COUNT DISTINCT over bitmap union). This is the theorem behind the streamed join, typed factor and dimension group executors. | Lean `Sql/Aggregate` | differential: fast path vs reference path (lane B10) | C1 |
| FV-15 | lock manager | The deadlock detector reports a cycle if and only if the waits-for graph has one. Granted locks are pairwise compatible. Releasing a victim's locks unblocks at least one waiter. | TLA+ `LockManager` | trace validation | C2 |
| FV-16 | reader/writer gate | No publication or reclamation happens while a parallel reader is active. Under weak fairness every waiting reader and writer eventually proceeds. The gate cannot deadlock with row locks. | TLA+ `RwGate` | trace validation from worker runs | C2 |
| FV-17 | wire framing | For every input byte string, each decoder returns exactly one of: a frame with `consumed <= input.len` and a length within limits, "need more", or an error. It never reads past the input. Encode then decode is the identity for supported messages. | Lean `Proto/Framing` | fuzzing with this as the oracle (lane B1) | C4 |

### Tier 3

| Id | Area | Statement | Model | Link | Lane |
| --- | --- | --- | --- | --- | --- |
| FV-18 | float text | For every finite double x, `parse(format(x)) = x`, and `format` emits the shortest such digit string. NaN and the infinities print as PostgreSQL does. | Lean (statement) + reference algorithm | oracle over 10^8 random and all boundary doubles | C1 |
| FV-19 | SCRAM | The server accepts a proof if and only if `H(ClientProof xor ClientSignature) = StoredKey`. The combined nonce begins with the client nonce. The RFC 7677 test vectors pass. Constant-time comparison is checked by code review and a statistical timing test, not by proof. | Lean | vectors | C4 |
| FV-20 | equality, hash, collation | For every type, `a = b` implies `hash(a) = hash(b)`, and the "C" byte-order collation is a total order consistent with equality. This makes hash joins, DISTINCT and the primary-key index agree with `=`. | Lean | oracle | C1 |
| FV-21 | primary-key index | After any sequence of inserts, deletes, updates and rollbacks, an index lookup returns exactly the visible rows with that key. | Lean model + simulation | model-based test | C2 |
| FV-22 | sequences | Values returned by `nextval` are never returned twice, including across crash and recovery. Gaps are allowed. | TLA+ `WalCommit` extension | crash replay | C3 |

## 4. Process

1. **State first.** A lane checks in definitions and theorem statements with
   `sorry` on a branch. A reviewer who knows the code confirms the model
   matches the implementation's intended behavior **before** proving starts.
   Disagreements found at this point are bugs or spec decisions, recorded in
   the module doc.
2. **Link before proof** when it is cheaper. Generating vectors from the
   executable model often finds implementation bugs before the proof is done.
   Fix them in the owning lane.
3. **Proof review.** A second agent checks that each theorem says what the
   catalog says, with no weakened hypotheses and no vacuous premises (for
   example a precondition that no real input satisfies).
4. **Merge** only with no `sorry` and with the link mechanism running in CI.
5. **Maintain.** A code change to a module with a linked model must keep the
   vectors, enumerations or trace checks green, or update the model in the
   same PR.

## 5. What this does not cover

- That the Sig compiler emits correct code (trusted).
- That the operating system honors `fsync` (axiom; power-loss tests in lane B9
  provide empirical evidence).
- Timing side channels (code review plus statistical tests).
- Performance properties. Complexity bounds such as FV-04's are proved on the
  model only.
