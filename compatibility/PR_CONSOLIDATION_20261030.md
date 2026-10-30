# PR consolidation — 30 October 2026

The workspace sync found 13 open ZPM PRs, three RAMSQL PRs and one dbMeter PR.
The integrator preserves existing worktrees, uncommitted files, original feature
commits/authors and experimental branches. No history is reset or force-pushed.

## Integrated into main

All 17 original PRs are resolved: nine merged and eight closed without merging.
The two consolidation merges are ZPM `2c0ac40` (PR 81) and RAMSQL `ed3f21e`
(PR 36); the other included PR heads are ancestors of those main branches.
All three repositories have zero open PRs at this checkpoint. Experiment and
workflow branches remain available.

RAMSQL PR 36 is the consolidation vehicle. It includes the original heads of
PR 34 (typed dimension DISTINCT integration) and PR 30 (complete positive and
negative benchmark evidence). It also merges the committed SRV-01 branch
`b5/srv01-create-data` at `81ceb3e`, and the hardening audit/documentation branch
`docs/hardening-plan` at `2bbe505`, so those advances are not left in isolated
worktrees. The new explicit-create behavior is documented in README/OPERATIONS;
first durable startup requires `--create-data`. Existing-file reopen and offline
backup never create a missing source.

ZPM PR 81 carries windows/FILTER plus the original heads of PRs 77 and 82–85:
exact DISTINCT membership, PostgreSQL/TDS parameter boundaries, concurrent DDL
replay order, bounded in-place lock upgrades and precise missing-file errors.
RAMSQL uses the combined immutable archive by full commit and verified SHA-256;
it does not select one older feature pin at the expense of another.

The hardening plan's baseline tables remain historical observations at 6d677ee /
2f060c2. They must be read with this consolidation record and the fresh combined
qualification, not treated as a current source fingerprint. SQLF/NET/STO/SRV
findings not actually fixed by these commits remain open.

## Closed without merging

ZPM PRs 53, 54, 58, 59, 60, 61 and 66 were unadopted performance experiments.
Their repeated measurements showed regressions, overlapping ranges, interference
or no repeatable benefit. Their code, branches and full positive/negative evidence
are preserved; they are not silently included in the production pin. See ZPM's
`docs/PR_CONSOLIDATION_20261030.md` for each decision.

dbMeter PR 1 is closed without merging because its complete run 41024725859 has
`qualified: false` / `merge_allowed: false`. Its workflow branch remains usable
for future experiments; closing it does not assert a benchmark advantage. The
original SQL and strict policy are unchanged. All original full/subset checks
pass 7/7, but stock text/column/table capabilities fail and full mixed performance
still trails IRDB. The retained 648-file live bundle exactly regenerates all
three summaries and the final failed decision.

## Qualification and inventory refresh

The earlier window source cd67d39 / RAMSQL bb2568d passed all 32 active server,
hosted artifact and native checks. Release run 41041672334 attempt 1 retained a
native PostgreSQL CAT1 observation failure (ROLLBACK exceeded the 300 ms harness
window); attempt 2 reran only that job against the same artifact and succeeded.
No test, policy or artifact was changed to obtain the retry. Source database
matrix 41041634923 passed Linux/macOS/Windows; broad legacy CI 41041635020 retains
four known unrelated SABR/TLS/TTS failures, with 298 successful and 22 skipped
steps. Those failures are not described as green.

The larger integration has now passed fresh qualification: ZPM database matrix
41114412805 succeeds on Linux/macOS/Windows; RAMSQL CI 41114733707 and release
matrix 41114733789 pass all 32 active checks, including every PG16/PG18 cell,
hosted exact artifact and native format. Broad legacy source run 41114412832
retains four unrelated SABR/TLS/TTS failures, with 300 successful and 22 skipped
steps. No such failure is counted as passing.

The original 118-case SQL TDD map is refreshed against consolidated ZPM main:
8 smoke passes, 93 failures and 17 unexecuted scopes/comparators. The former
6/95/17 output is preserved. See
[the current SQL checkpoint](https://github.com/SB0LTD/zpm/blob/main/docs/SQL_CONFORMANCE_TDD_STATUS.md)
and its per-ID inventory; cross-reference the hardening feature/divergence audit. Passing a
finite smoke test never closes a whole SQL family, operational gate or 100M-row
capacity target.
