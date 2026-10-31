# Snapshot and sequence safety checkpoint — 31 October 2026

Qualification is in progress; merge only after the final source matrices pass.
This checkpoint addresses STO-02 and STO-04, not full production readiness or
SQL/performance parity. Reusable allocator-free changes live in ZPM.

## Reproduced defects and fixes

**STO-02 — live snapshot reclamation.** A three-row UPDATE parks after changing
its first row. Its blocker fills the remaining version slots and commits. The
old server resumes, vacuums a still-visible third row, then acknowledges
`UPDATE 2`. In the new server, running and open COPY snapshots participate in
the horizon. If the caller-provided arena cannot retain live versions and
complete the statement, it returns 53200 and rolls back its partial writes.
Normal adequate-capacity EPQ schedules still complete; this is not a new row
limit. COPY tests cover retention across messages and release on end/cancel.

An independent loopback wire probe uses `--memory --rows 16 --versions 32`.
The old artifact commits rows `(1,1),(2,37),(3,10)` after incorrectly skipping
the third update. The fixed server returns VersionCapacity and leaves the
blocker's committed rows `(1,0),(2,36),(3,10)` intact. Both are real server
processes using independent PostgreSQL wire messages, not a substitute engine.
The equivalent deterministic pressure regression is mandatory in ZPM's runnable
`test_sql_mvcc.sig` contract.

**STO-04 — a live owner's sequence log-ahead.** Owner A takes 1 and stays open;
consumer B takes 2 and commits. The old server writes no sequence watermark
with B's commit, so a kill/restart starts at 1 and can reissue 2. Global pending
watermarks now survive the live-owner/consumer ordering and flush before ACK.
Later hits in an already-durable cache retain the no-record path. Creator-local
dirty bits retain the obligation for a CREATE invisible to another committer.
Ascending/descending replay and independent process kills cover SELECT and
INSERT commit acknowledgements. Explicit setval/CYCLE resets remain excluded
from the non-reissue guarantee; sequence gaps remain intentional.

The old Windows artifact was verified with its original dependency manifest:
run [41122569270](https://github.com/SB0LTD/ramsql/actions/runs/41122569270),
image SHA-256 `f418eac5581c2ac32afb9602a16d15a090b5b526348074e5b0ede9f7b523aa62`.
It fails the new bare-SELECT-ACK process test with recovered value 1 instead of
34. The fixed local server passes 68 persistence cases / 1,848 assertions with
two standard crash cycles and all four new independent sequence scenarios.

## Source and verification

- ZPM [PR 87](https://github.com/SB0LTD/zpm/pull/87):
  `6c9ad4451bb5f966f06611307f3b3ef7ba980653`, archive SHA-256
  `355d864a77912891f5a7a27f28725816a8a778444e9f03759e04e5a419680c38`.
- RAMSQL [PR 39](https://github.com/SB0LTD/ramsql/pull/39):
  `4dca1e418dca22179b0963a73f0a58f3a533b66c`.
- MVCC 351 and DML 229 assertions pass locally in Debug, ReleaseSafe and
  ReleaseFast. Independent review reruns ReleaseSafe and approves exact heads.
- Python qualification contracts pass 9/9; SQL oracle/evidence tests 16/16.
- Final qualification: [ZPM database matrix](https://github.com/SB0LTD/zpm/actions/runs/41150514054),
  [RAMSQL CI](https://github.com/SB0LTD/ramsql/actions/runs/41150556722),
  [release matrix](https://github.com/SB0LTD/ramsql/actions/runs/41150556696).
  These must finish before this checkpoint is marked qualified.

## TCP contract repair

Initial macOS [run 41149707471](https://github.com/SB0LTD/ramsql/actions/runs/41149707471)
failed assertion 49 in backpressure accept fairness. The test assumed the first
poll wakeup must report listener readiness, although unrelated writable events
or EINTR may wake it. The log does not distinguish which event actually caused
the failure. The corrected test retains interest masks, accept/read/payload
assertions and one absolute one-second deadline. No transport implementation or
timeout relaxation is introduced. Independent review approves; 20/20 local
TCP contract repetitions pass. The final matrices test this corrected source;
the earlier failure remains part of the evidence.

## Open work

NET-02 nested-comment binding, STO-05 full-length torn-tail handling, volatile
DML replay, SQL interval/JSONB correctness and the wider SQL backlog remain open.
FV-05/FV-08/FV-22 formal proofs remain open. Corrupt complete WAL tails still
fail closed; never discard a possibly acknowledged commit to improve recovery
availability. Standard CI/release performance checks run, but this safety batch
makes no speedup claim. No release tag/publishing is requested here.
