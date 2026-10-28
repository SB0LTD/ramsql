# Implemented SQL reference

This describes the implemented subset, not PostgreSQL parity. See CAPACITY.md for current server budgets.

## SQL and transactions

CREATE/DROP TABLE; CREATE/DROP SEQUENCE; INSERT [AS alias] with optional
columns, multi-row VALUES (with DEFAULT items), INSERT ... SELECT or DEFAULT
VALUES, `OVERRIDING SYSTEM|USER VALUE`, `ON CONFLICT [(pk) | ON CONSTRAINT
<table>_pkey] DO NOTHING | DO UPDATE SET ... [WHERE ...]` with EXCLUDED;
SELECT; UPDATE with expression SET (and `SET col = DEFAULT`); DELETE; and
`RETURNING *`/`t.*`/expressions on INSERT, UPDATE and DELETE (new rows for
INSERT/UPDATE, the deleted rows for DELETE, emitted exactly once even when the
statement waits for a lock). Upsert arbitrates on the primary key only (the
only unique index; `42P10` otherwise): a conflict with an in-progress inserter
or deleter waits and re-checks, a row hit twice by one DO UPDATE is `21000`,
and DO UPDATE ... WHERE false locks the row and skips it. Column DEFAULT
expressions are evaluated per row; `GENERATED ALWAYS|BY DEFAULT AS IDENTITY
[(sequence options)]` (one identity column per table) owns a sequence
`<table>_<column>_seq` dropped with the table; ALWAYS rejects explicit values
with `428C9` unless OVERRIDING SYSTEM VALUE; `GENERATED ALWAYS AS (expr)
STORED` columns are recomputed on every insert and update (`428C9` when
assigned). Sequences support INCREMENT, MINVALUE/MAXVALUE (NO ...), START,
[NO] CYCLE and AS smallint|integer|bigint (CACHE other than 1 is `0A000`),
`nextval`, `currval` (`55000` before the session's first nextval), `setval`
and `lastval`; exhaustion is `2200H`; values are never rolled back and follow
PostgreSQL's 32-value log-ahead, so identities and sequences resume at
PostgreSQL's post-crash values (a SELECT that advances a sequence is durable
before its reply; an aborted transaction's log-ahead value rides on the next
commit record from any session). At most `Config.sequences` (16) sequences
exist; there are no sequence locks (a concurrent DROP SEQUENCE waits only for
another uncommitted DROP). Types are SMALLINT/INT2, INTEGER/INT/INT4,
BIGINT/INT8, NUMERIC/DECIMAL[(p[,s])], REAL/FLOAT4, DOUBLE PRECISION/FLOAT8,
BOOLEAN, TEXT, NAME, OID, DATE, TIMESTAMP[(p)] [WITHOUT TIME ZONE],
TIMESTAMPTZ[(p)]/TIMESTAMP WITH TIME ZONE, INTERVAL, JSON and JSONB, plus NULL.
NOT NULL and one single-column PRIMARY KEY are supported.

Values follow PostgreSQL where implemented: NUMERIC rounds half away from
zero, uses PostgreSQL's division/AVG scales and NUMERIC(p,s) typmods (negative
scales allowed, `22003` on overflow), and supports NaN/Infinity (NaN sorts
last); REAL/DOUBLE accept the measured `float8in` grammar (decimal, `0x..p`
hex, Infinity/inf/NaN) and print shortest round-trip digits like `float8out`;
float-to-integer casts round half to even, numeric-to-integer half away.
Temporal input is ISO 8601 (`T` or space, fractions, `Z`/`+-HH[:MM[:SS]]`, a
trailing zone name, `BC`), `infinity`/`-infinity`/`epoch`, with PostgreSQL's
range limits (`22008`) and `22007` for malformed text; output uses DateStyle
ISO and IntervalStyle postgres. `+ -` on dates, timestamps and intervals,
`AT TIME ZONE`, `now()`/`CURRENT_TIMESTAMP`/`transaction_timestamp()` (the
transaction start), `statement_timestamp()`, `clock_timestamp()` and
`CURRENT_DATE` are supported; nonexistent and ambiguous local times resolve as
PostgreSQL's Appendix B.1 describes. `SET TIME ZONE` accepts UTC, a number of
hours, zone names and POSIX TZ strings, is transactional (ROLLBACK, ROLLBACK
TO, SET LOCAL, reverted by an error in a block) and is reported through
ParameterStatus with DateStyle, IntervalStyle and application_name. JSON keeps
its validated text; JSONB is canonical (sorted keys, last duplicate wins,
NUMERIC numbers, PostgreSQL output spacing) with `-> ->> #> #>> @> <@ ?`,
equality and btree ordering, `jsonb_typeof` and `jsonb_array_length`.
PostgreSQL binary results and parameters are supported for every type.

Expressions follow PostgreSQL precedence and typing for that type set: AND/OR/NOT
with three-valued logic, comparisons, IS [NOT] NULL/TRUE/FALSE/UNKNOWN,
[NOT] IN lists, [NOT] BETWEEN, [NOT] LIKE/ILIKE, `||`, checked integer
`+ - * / %`, CASE, COALESCE, NULLIF and `::`/CAST, with PostgreSQL SQLSTATEs
(42804 for `WHERE id`, 42883 for `label + 1`, 22P02, 22003, 22012). A statement
that fails after changing many rows changes none. ORDER BY accepts expressions,
output positions and names with ASC/DESC and NULLS FIRST/LAST, then LIMIT
[ALL], OFFSET and FETCH FIRST|NEXT n ROW|ROWS ONLY (negative counts give
`2201W`/`2201X`). Queries support SELECT DISTINCT and DISTINCT ON (NULLs equal;
PostgreSQL's `42P10` rules); COUNT(*), COUNT, SUM, AVG, MIN, MAX, BOOL_AND,
BOOL_OR/EVERY with DISTINCT and FILTER (WHERE), GROUP BY (columns, expressions,
positions, output names) and HAVING, with PostgreSQL result types (SUM of
int2/int4 is int8, SUM(int8) and AVG of integers are NUMERIC, COUNT is int8;
`42803` for ungrouped columns); INNER, LEFT, RIGHT, FULL and CROSS joins with
ON or USING, comma joins, aliases and self-joins (`42702` for an ambiguous
column, `42P01` for an unknown qualifier); scalar subqueries (`21000` for more
than one row), [NOT] IN and [NOT] EXISTS subqueries (correlated or not) and
derived tables; WITH CTEs (at most 4 per statement), WITH RECURSIVE over
UNION [ALL] to a fixpoint, and UNION, INTERSECT and EXCEPT [ALL]. AVG(interval),
LATERAL, NATURAL joins, parenthesized join trees, window functions (flag F6)
and data-modifying CTEs (flag F7) are not provided.
Default ordering follows PostgreSQL: ASC NULLS LAST, DESC NULLS FIRST. A WHERE
holds at most 8 leaf predicates. The default embedded profile has 512 expression
nodes; the current hosted profile has 8,192, nested 64 deep. Check the actual
binary's `--capabilities` because these profile limits are configurable.
Unquoted names are ASCII and case insensitive, quoted names keep
case; text is bytewise and text bounds count bytes (including `||` results).
Apostrophes are doubled in SQL text. Each execute call accepts one statement.
Development supports `ROUND(numeric)`, `ROUND(numeric, integer)` and
`ROUND(double precision)`, including column and aggregate inputs. Decimal ties
round away from zero; floating ties use nearest-even and retain signed zero.
The existing numeric coefficient/display-scale capacities still apply; an
unrepresentable result returns a capacity error. Function arguments remain
subject to normal type conversion and NULL rules.

Other functions (including `pg_typeof` and `to_char`), ROLLUP, window functions,
arrays, JSON (non-B) operators, jsonpath, zone abbreviations (`EST`),
non-ISO DateStyle and non-postgres IntervalStyle are not supported. MySQL and
TDS clients receive NUMERIC, REAL/DOUBLE, temporal and JSON values as text
columns (no native DECIMAL/DOUBLE/DATETIME metadata).

MySQL and TDS sessions additionally accept integer 0/1 when assigning BOOLEAN
values (INSERT/UPDATE), for common BIT client encodings; expressions are typed
identically on every listener. Raw library configurations retain the ZPM
defaults (ASC NULLS FIRST, 64-bit INTEGER outside the PostgreSQL dialect);
select `integer_bits = 32` and `nulls_first_ascending = false` for PostgreSQL
scalar semantics.

Transactions are READ COMMITTED only, over row versions in one shared store
(no per-connection copies). Every statement takes a new snapshot, so it sees
everything committed before it started plus its own transaction's earlier
writes. A write that meets a row being changed by another live transaction
waits for it, then re-checks the row's newest committed version
(PostgreSQL's EvalPlanQual), so concurrent `v = v + 1` updates are never lost;
an INSERT waits for a live inserter or deleter of the same key, then gets
`23505` or proceeds. `SELECT ... FOR UPDATE|SHARE [NOWAIT|SKIP LOCKED]` takes
row locks; DROP TABLE takes an exclusive table lock that waits for readers and
writers (and that readers then wait for). Deadlocks are detected once per wait
after 1 s (`40P01`, the detecting waiter is the victim); `lock_timeout` gives
`55P03`, `statement_timeout` and PostgreSQL cancel requests give `57014`.
Waiting never blocks the reactor; other sessions keep running. Running scans,
joins, expression evaluation, sorts and mutations also check statement deadlines
and cancellation. Cancel requests use bounded control polling, including when the
SQL connection pool is full. Rollback and synchronous durable publication are
not interrupted. Execution remains synchronous; fair scheduling, streaming results
and concurrent CPU execution remain open. See [execution controls](compatibility/EXECUTION_CONTROL.md).
`BEGIN`/`SET TRANSACTION ISOLATION LEVEL REPEATABLE READ|SERIALIZABLE` is
rejected with `0A000` instead of silently giving READ COMMITTED;
`SHOW transaction_isolation` reports `read committed`. DDL is transactional:
an uncommitted CREATE TABLE is invisible to other sessions and ROLLBACK of a
DROP TABLE restores the table and its rows. SAVEPOINT, RELEASE [SAVEPOINT] and
ROLLBACK TO [SAVEPOINT] nest (a repeated name refers to the newest; `3B001` for
an unknown name, `25P01` outside a block); ROLLBACK TO undoes rows, DDL, row
locks and SET values after the savepoint and recovers a failed block. An error
inside a block discards the work since the innermost savepoint, releases its
locks at once and fails the block (`25P02`) until ROLLBACK or ROLLBACK TO, as
PostgreSQL does. SET [SESSION|LOCAL], SHOW and RESET [ALL] support
`lock_timeout`, `statement_timeout`, `application_name` and read-only views of
the reported session defaults; startup `options` accept `-c name=value`.
Failed transactions and disconnected sessions cannot publish their changes.
Cursors and two-phase commit do not exist.

System views are computed at scan time from the versioned catalog and table
definitions under the statement snapshot, so they follow transactional DDL:
`pg_catalog.pg_type` (the 16 supported base types), `pg_namespace`,
`pg_class` (tables, primary-key indexes `<table>_pkey`, sequences),
`pg_attribute` (user columns), `pg_index`, `pg_attrdef`, `pg_sequence`, and
`information_schema.schemata`, `tables`, `columns`, `table_constraints` and
`key_column_usage` (primary keys). pg_catalog views resolve unqualified;
information_schema views must be qualified. Columns PostgreSQL declares as
`"char"`, `regproc`, `int2vector`, `character_data` or `yes_or_no` are TEXT
here (cast them with `::text` for identical RowDescription OIDs); system
columns, array/composite types, toast tables, NOT NULL constraints and other
catalogs are absent. `current_database()`, `current_schema()`, `to_regtype`,
`to_regclass`, `format_type(oid, typmod)`, `pg_get_expr` and the casts
`::regclass`, `::regtype` and `::oid` are supported; regclass/regtype values
are OIDs that print as names only through `::text` (flag F14). DEFAULT and
generation expressions are shown in PostgreSQL's canonical form for literals,
function calls, operators and casts.

`COPY t [(columns)] FROM STDIN` and `COPY {t [(columns)] | (query)} TO STDOUT`
run over the PostgreSQL simple query protocol with `FORMAT text|csv|binary`,
`DELIMITER`, `NULL` (at most 64 bytes), `HEADER [true|false|match]`, `QUOTE`
and `ESCAPE` (single bytes) and the pre-9.0 option spelling (`WITH CSV HEADER`,
`BINARY`, ...). COPY FROM fills omitted columns with their DEFAULT or identity
value (supplied identity values are kept, as PostgreSQL does; generated
columns cannot be listed, `42P10`) and runs as one statement: malformed data
(`22P04`, binary length errors `22P03`), an input-function error (`22P02`), a
duplicate key (`23505`), a client CopyFail (`57014`) or a disconnect rolls the
whole COPY back. A row whose key is held by an in-progress inserter waits for
it like INSERT and is re-checked when that transaction ends. CopyData messages
are streamed, so their size is not bounded by the 64 KiB receive buffer; one
data row (text line, CSV record or binary tuple) is at most 8192 bytes
(`54000` beyond). FORCE_QUOTE/FORCE_NOT_NULL/FORCE_NULL, `DEFAULT`, `ON_ERROR`,
`WHERE`, encodings other than UTF-8 and COPY over the extended query protocol
are not supported (`0A000`); option SQLSTATEs follow PostgreSQL 16.

Foreign keys, secondary indexes, window functions and complete SQL-standard
semantics remain release blockers. The committed finite differential gate
reports ([PostgreSQL 18](compatibility/postgres-report.json),
[PostgreSQL 16](compatibility/postgres16-report.json)) match 37/37 probes, and
the same 37 match in durable+SCRAM mode; the differential semantics,
isolation, COPY and crash suites also match both versions (see
[the gap-closing plan](compatibility/GAP_CLOSING_PLAN.md#gap-closing-run-evidence-24-october-2026)).
37 probes are a finite gate, not PostgreSQL parity: the catalog subset and
broader SQL behavior remain incomplete, and `postgres_compatibility` stays
false in the release gates.
