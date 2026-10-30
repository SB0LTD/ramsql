# Definition of done

A task, PR or lane is done only when every applicable item holds. Reviewers
reject work that skips one. `PLAN.md` acceptance criteria add to this list and
never replace it.

## Code

- [ ] Allocator-free: no heap allocator, no hidden growth. New capacity limits
      are named constants or runtime budgets, documented in `CAPACITY.md` and
      reported by `--capabilities`.
- [ ] Every error path preserves statement atomicity and ownership. No
      `catch unreachable` or `@panic` on input that a client, a file or a
      command-line argument controls.
- [ ] Integer arithmetic on sizes, lengths and counts is checked (`std.math.add`,
      `mul`, explicit range checks) where an input can reach it.
- [ ] Layering holds: lower layers never import higher ones; engine logic lives
      in ZPM, product policy in RAMSQL.
- [ ] `sig fmt --check` is clean for touched files.
- [ ] Public functions and types have doc comments that state ownership,
      capacity and error behavior.
- [ ] No function longer than about 150 lines is added; existing ones are not
      made longer without a split.

## Tests

- [ ] A test that fails without the change exists and is linked from the PR.
- [ ] Contract suites for touched modules pass in ReleaseSafe.
- [ ] Parsers of untrusted input: fuzz target added or extended, with a 10-minute
      local run and no findings.
- [ ] Semantic changes: differential run against PostgreSQL 16 and 18 on the
      affected corpus, with zero unexplained divergences.
- [ ] Concurrency or durability changes: simulation seeds and crash-point runs
      pass (see the verification strategy).
- [ ] No test was skipped, weakened or deleted to get a green run.

## Verification

- [ ] Invariants touched by the change are listed in the module doc.
- [ ] If a Lean or TLA+ model covers the module, the model and its generated
      test vectors still pass, or the model is updated in the same PR.

## Documentation

- [ ] The module doc under `docs/modules/` reflects the new behavior, findings
      status and tests.
- [ ] User-visible behavior changes have a changelog fragment.
- [ ] `SQL_REFERENCE.md` and `CAPACITY.md` match the code.

## Evidence

- [ ] Every claim in the PR description carries the command that produced it and
      its exit code, re-run by a verifier.
- [ ] Performance claims follow the benchmark methodology and link a result
      summary. Raw output is in the evidence store, not in git.
