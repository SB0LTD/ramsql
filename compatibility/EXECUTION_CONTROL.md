# Running SQL execution controls

Implementation milestone: 25 October 2026. This closes part of gap O02;
resumable execution, scalable storage and production qualification remain open.

## Operator behavior

`SET statement_timeout = 5` sets a five-millisecond statement deadline. Running
evaluation checks a live monotonic clock as well as checking deadlines while
waiting for locks. A statement that waits and resumes retains its original
deadline. Cancellation/timeout returns PostgreSQL SQLSTATE `57014`.

The server accepts PostgreSQL CancelRequest packets during CPU evaluation. It
verifies the backend PID and random secret before requesting cancellation. Keys
with the wrong secret are ignored. Four separate, header-only control slots
allow cancellation when all configured SQL connection slots are occupied.
Partial control headers expire after one second; each header is at most 16 bytes.
These slots never authenticate a SQL session or execute a statement.

`ramsql --max-query-work 1000000` caps the number of execution checkpoints a
statement may consume. Zero, the default, disables that limit. Exhaustion returns
`54000` (`QueryWorkLimit`). A work unit is an implementation checkpoint, not a
row, instruction, millisecond or stable cost estimate across releases. Scans,
joins, expression evaluation, comparisons/sorts and mutations account for work.
Work already spent before a lock wait remains charged when execution resumes.

Both interruption paths roll back the statement's private changes. An explicit
transaction enters PostgreSQL's failed state; ROLLBACK or ROLLBACK TO recovers it
according to the existing transaction rules. An implicit transaction aborts and
the connection remains usable. Failed execution preserves the previous internal
result and does not publish a partial result or acknowledge a partial commit.

## Layer ownership

ZPM `core/sql` owns the caller-provided `ExecutionControl`, work accounting and
interruptible evaluation. It imports no socket or clock implementation and
allocates no heap storage. A raw SQL caller can attach its own poll hook through
the session; a null control keeps that interface optional.

ZPM `db/store` owns per-statement accounting, deadline retention, cancellation
state and transaction error cleanup. Its clock and interrupt-I/O callbacks are
provided by the caller. ZPM's PostgreSQL codec owns startup-header inspection and
SQLSTATE mapping. RAMSQL owns CLI policy, the live platform clock and bounded
nonblocking socket polling. The callback never reenters SQL or dispatches a
second statement while the first owns the shared workspace. Ordinary startup
bytes accepted during polling are retained for the outer reactor.

## Validation

The ZPM store contracts exhaust budgets on reads, joins, sorts, aggregates,
updates and deletes. They preserve the previous result, cancel after private
versions exist, expire a fake live clock, verify unchanged committed data and
check that locks, private versions and transactions are released. PostgreSQL
contracts cover partial/invalid cancel headers and the SQLSTATE mapping. The
existing poisoned-memory snapshot test also verifies fresh session initialization.

RAMSQL's required `tools/test_server.py` suite exercises a CPU-intensive
correlated aggregate without a lock wait. It verifies timeout, cancellation with
a full SQL pool and fragmented header, invalid-key rejection, preservation of a
fragmented ordinary startup, budget rollback and failed transaction recovery.
The tests run with the hosted CI and exact release artifacts. Local Linux and
Windows Sig 0.6.4 ReleaseSafe runs passed; a new complete release qualification
is required before publishing this engine revision.

## Remaining limits

Execution still runs synchronously in one reactor. Poll hooks provide bounded
control I/O; they do not schedule other queries or make the engine multicore.
Control I/O is throttled to once per millisecond, and hooks are normally polled
every 256 execution checkpoints. These are polling parameters, not hard realtime
latency guarantees. Parsing, individual value primitives and result publication
have bounded existing capacities but are not all independently interruptible.

Rollback, WAL preparation/flush and commit publication finish synchronously.
Interrupting them between durable flush and publication would violate the commit
contract. COPY streaming and disconnect cleanup need additional lifecycle work;
transaction and idle-in-transaction deadlines are not implemented. Native ABI
fixture tests do not establish real OS socket, clock or durable-device behavior.
This milestone establishes neither complete PostgreSQL compatibility nor
production readiness or universal performance superiority.
