# Git workflow for parallel lanes and subagents

This is the operating contract for the hardening program in
[`PLAN.md`](../../PLAN.md). Many agents (human or AI) work at the same time, so
every rule here exists to avoid one of three failures: two writers editing the
same file, unverified claims reaching `main`, and history that nobody can
bisect or explain.

## 1. Repositories and trunks

| Repo | Remote | Trunk | Integration branch | Who merges into trunk |
| --- | --- | --- | --- | --- |
| RAMSQL | `SB0LTD/ramsql` | `main` | `int/hardening` | maintainer, at a wave checkpoint |
| ZPM | `SB0LTD/zpm` | `main` | `int/ramsql-hardening` | maintainer, at a wave checkpoint |

- `main` only moves at wave checkpoints (see `PLAN.md` §10) after the full gate
  matrix passes on the integration branch.
- Each repo has one integration branch for the whole program, not one per
  workflow. ZPM has a single pin in `build.sig.zon`, and separate integration
  branches would make every pin bump conflict.
- Nobody force-pushes `main` or `int/*`.

## 2. Lanes

A **lane** is a unit of parallel work with one owner agent, one branch per repo
and a declared set of owned paths. `PLAN.md` §7–§9 lists every lane.

| Item | Convention | Example |
| --- | --- | --- |
| Lane id | workflow letter + number | `B3` |
| Branch | `<lane-id-lowercase>/<slug>` | `b3/pg-tls` |
| Worktree | `<repo>/.worktrees/<lane-id-lowercase>` | `ramsql/.worktrees/b3` |
| Scratch output | `<repo>/.scratch/<lane-id-lowercase>/` (git-ignored) | logs, profiles |
| Changelog fragment | `changes/unreleased/<lane-id>-<n>.md` | `changes/unreleased/B3-1.md` |

Create a lane worktree from the integration branch:

```sh
git -C ramsql fetch origin
git -C ramsql worktree add .worktrees/b3 -b b3/pg-tls origin/int/hardening
```

Rules:

1. **One writer per path.** A lane edits only the paths it owns. Shared files
   follow §4. Lanes that need a change in someone else's path open an issue
   against the owning lane instead of editing it.
2. **Rebase, don't merge.** Before opening or updating a PR, rebase the lane on
   the current integration branch. Never merge `int/*` into a lane branch.
3. **Small PRs.** Aim for at most about 400 changed lines of non-test,
   non-doc code per PR. Use stacked PRs inside a lane for larger work.
4. **Every commit builds** and passes the contract suites for the modules it
   touches, so `git bisect` works.

## 3. Cross-repo changes (ZPM then RAMSQL)

Engine code lives in ZPM; RAMSQL pins one ZPM commit by archive URL and SHA-256.
There are no implicit sibling paths (`AGENTS.md`).

1. Implement and test the engine change on the ZPM lane branch (`zpm/.worktrees/<lane>`).
2. Push it. In the RAMSQL lane, pin that commit's archive provisionally. Mark
   the commit with the trailer `Pin: provisional`.
3. When the ZPM lane merges into `int/ramsql-hardening`, the **integrator**
   replaces every provisional pin with the integration commit and records the
   hash. Only the integrator edits `build.sig.zon` on `int/hardening`.
4. A RAMSQL PR that carries a provisional pin cannot merge into `int/hardening`.

## 4. Shared files

| File | Rule |
| --- | --- |
| `build.sig.zon`, `zpm.pkg.zon` | integrator only |
| `build.sig` test registration | append-only, one entry per line, kept in lane blocks; integrator resolves conflicts |
| `CHANGELOG.md` | never edited by lanes; fragments in `changes/unreleased/` are compiled at release |
| `README.md`, `ROADMAP.md` | lane A5 only; other lanes file a doc request |
| `PLAN.md` findings register | each lane updates only the status column of its own findings |
| `docs/modules/<module>.md` | the lane that owns the module |
| `.github/workflows/*` | lane A4 only |

## 5. Commits

Use Conventional Commits with the module as scope:

```
fix(pg-wire): reject negative Bind parameter lengths before slicing

The length field is attacker controlled. A negative i32 other than -1 was
cast to usize and used as a slice bound.

Finding: NET-02
Lane: B1
Evidence: tests/contracts/pg_wire_bounds.sig, CI run <url>
```

Types: `feat`, `fix`, `sec`, `perf`, `test`, `docs`, `refactor`, `build`, `ci`,
`chore`, `formal`. `sec` is for security fixes. Disclose details only after the
fix ships (see `SECURITY.md` once lane A5 adds it).

## 6. Pull requests

A PR from a lane into `int/hardening` merges only when all of these hold:

- [ ] CI is green, including the gates in `PLAN.md` §11 for the touched area.
- [ ] Tests that fail without the change were added first and are linked.
- [ ] An **independent reviewer** approved. A reviewer agent starts with a
      fresh context, gets the diff and the module doc, never gets the
      implementer's summary, and runs the adversarial checklist in
      `PLAN.md` §14.3.
- [ ] A **verifier** re-ran every command listed under "Evidence" and attached
      exit codes. Claims without a command are rejected.
- [ ] The module doc (`docs/modules/...`) and findings statuses are updated.
- [ ] No provisional ZPM pin.
- [ ] Benchmarks: if the PR claims a performance change, it includes a matched
      before/after run following [`docs/benchmarks/METHODOLOGY.md`](../benchmarks/METHODOLOGY.md).

Merge style: squash-merge per PR into `int/*` with the PR title as a
Conventional Commit. `int/*` merges into `main` with a merge commit that names
the wave.

## 7. Evidence and generated output

- Raw benchmark logs, CSV timelines, profiles and qualification JSON do **not**
  go into git. They go to release assets or the evidence store described in
  [`docs/benchmarks/METHODOLOGY.md`](../benchmarks/METHODOLOGY.md). The repo keeps
  a short summary plus a manifest of SHA-256 hashes.
- `.scratch/`, `.agents/`, `.worktrees/`, `.sig-cache/` and `sig-out/` are
  git-ignored. Agents write scratch only to `.scratch/<lane>/`, never to
  `.sig-cache/`, which is the compiler cache.
- Never delete another lane's scratch, caches or worktrees. Cleanup is a
  maintainer decision.

## 8. Subagent operating rules

Every agent working a lane:

1. Reads `AGENTS.md`, its lane in `PLAN.md`, and the module docs it owns
   before touching code.
2. Works only in its own worktree and branch.
3. Writes or extends the failing test first, then the fix.
4. Never weakens, skips or deletes an assertion to get a green run. If a test is
   wrong, it says so in the report and asks the integrator.
5. Never claims a result it did not observe. Every claim in the final report
   carries the command that produced it and its exit code.
6. Ends with the report format in `PLAN.md` §14.2.
