# PostgreSQL feature gaps and divergences (ZPM SQL front end)

This page lists the PostgreSQL features that real applications, drivers and
ORMs are likely to need but that RAMSQL's SQL engine (ZPM `2f060c2`, pinned
by RAMSQL `6d677ee`) does not provide. It also lists every known case where
RAMSQL gives a different result from PostgreSQL. Every row was checked
against the code at that revision.

"Error" means the statement fails with the SQLSTATE shown, which is safe but
blocks the application. **Silently wrong** means the statement succeeds with
a result PostgreSQL would not give, which is the worst class. Priorities are
provisional:

- **P0**: blocks schema creation or connection setup for common drivers and ORMs.
- **P1**: common in application queries.
- **P2**: occasional.
- **P3**: rare.

Lane B11 owns the gaps and will reorder them by the failures seen in the
L6 client and ORM probes (psql/libpq, psycopg 3, asyncpg, JDBC, pgx,
node-postgres, SQLAlchemy, Django). Lane B7 owns the divergences. Finding
ids refer to the module pages under `docs/modules/zpm-sql/`. ZPM's own
inventory (`zpm/tests/sql_conformance/README.md`, 118 smoke programs and the
755-record PostgreSQL 18 feature list) is a second source. Its status table
predates the window and ROLLUP work.

## 1. Application-blocking gaps (SQLF-14)

| Gap | PostgreSQL behavior | RAMSQL at ZPM 2f060c2 | Evidence | Priority | Lane |
| --- | --- | --- | --- | --- | --- |
| `VARCHAR(n)`, `CHAR(n)`, `CHARACTER VARYING` | String types with a length check | Error 42601 in CREATE TABLE and casts. Only TEXT and NAME exist. | `parser.sig:555-559` | P0 | B11 |
| `SERIAL`, `BIGSERIAL` | Integer column with an owned sequence default | Error 42601. `GENERATED ... AS IDENTITY` works instead. | `parser.sig:555-559` | P0 | B11 |
| Table constraints: composite or table-level `PRIMARY KEY (a, b)`, `UNIQUE`, `CHECK`, `REFERENCES`/`FOREIGN KEY` | Enforced constraints | Error 42601. Only column-level NOT NULL and one single-column PRIMARY KEY exist. | `parser.sig:714-781` | P0 | B11 |
| `CREATE [UNIQUE] INDEX` | Secondary indexes | Error 42601. `create()` requires TABLE. | `parser.sig:176-180` | P0 | B11 |
| `IF [NOT] EXISTS` on CREATE/DROP | Idempotent DDL | Error 42601. `IF` is read as the table name. | `parser.sig:176-186`, `714-716` | P0 | B11 |
| `ALTER TABLE`, `TRUNCATE`, `CREATE VIEW`, `CREATE SCHEMA`, `COMMENT ON`, `GRANT` | DDL and administration | Error 0A000 (ALTER, TRUNCATE, COMMENT, GRANT) or 42601 (CREATE VIEW/SCHEMA) | `parser.sig:491`, `176-180` | P0 (ALTER), P2 others | B11 |
| String functions: `lower`, `upper`, `length`, `substring`, `trim`, `replace`, `position`, `concat`, `left`, `right`, `lpad` | Built-ins | Error 42883. The function table has 21 entries: clocks, `round`, `to_char`, `grouping`, jsonb, sequence and catalog functions. | `binder.sig:581-606` | P0 | B11 |
| Session and system functions: `version()`, `current_user`, `session_user`, `current_setting`, `set_config`, `pg_backend_pid` | Built-ins that drivers and tools call | 42883. `current_user` is parsed as a column and gives 42703. | `binder.sig:581-606` | P0 (unverified until L6 probes) | B11 |
| Math functions: `abs`, `floor`, `ceil`, `mod()`, `power`, `sqrt`, `random` | Built-ins | Error 42883 | `binder.sig:581-606` | P1 | B11 |
| Date and time functions: `extract`/`date_part`, `date_trunc`, `age`, `make_date`, `to_timestamp`; `to_char` for numeric or interval | Built-ins | 42601 (the `EXTRACT(x FROM y)` syntax) or 42883 | `binder.sig:581-606`, `771` | P1 | B11 |
| Aggregates: `string_agg`, `array_agg`, `json(b)_agg`, `stddev`, `variance`, `AVG(interval)` | Built-ins | Error 42883. Aggregates are COUNT, SUM, AVG, MIN, MAX, BOOL_AND/EVERY and BOOL_OR. | `binder.sig:608-620`, `681` | P1 | B11 |
| `UUID`, `BYTEA` | Types | Error 42601 | `parser.sig:555-559` | P1 | B11 |
| Arrays: `int[]`, `ARRAY[...]`, `= ANY($1)` with an array | Types and operators | 42601, or 0A000 for array ANY | `parser.sig:555-559`, `1334-1335` | P1 | B11 |
| `LIMIT`/`OFFSET` with an expression or a text-typed parameter | Any bigint expression | 42601. Only an integer literal is accepted. Integer parameters work only because the wire layer splices them as bare text (NET-01). | `parser.sig:1192-1204` | P1 | B11 (B1 for parameters) |
| `UPDATE ... FROM`, `DELETE ... USING` | Joined DML | Error 42601 | `parser.sig:1205-1233` | P1 | B11 |
| `E'...'` strings, dollar quoting, `U&'...'`, `B'...'`/`X'...'` | Literal forms used by `pg_dump` and migration tools | Error 42601 | `lexer.sig:375-468` | P1 | B11 |
| JSONB operators and functions: `\|\|`, `-`, `#-`, `?\|`, `?&`, `jsonb_set`, `jsonb_build_object`, `jsonb_each`, `to_jsonb`, jsonpath, operators on JSON | Built-ins | 42883 or 42601. `jsonb \|\| 'literal'` is silently wrong (section 2). | `jsonb.sig`, `binder.sig:581-606` | P1 | B11 |
| Window functions `lag`, `lead`, `first_value`, `last_value`, `ntile`, `percent_rank`; RANGE, GROUPS and bounded ROWS frames; named windows; FILTER or DISTINCT in window aggregates | Window functions | 42883 or 0A000. Supported: RANK, DENSE_RANK, ROW_NUMBER, and aggregates with default or UNBOUNDED ROWS frames. | `binder.sig:697-724`, `parser.sig:1631-1641` | P2 | B11 |
| Regular expressions (`~`, `~*`, `!~`, `SIMILAR TO`, `regexp_*`) | Operators and functions | Error 42601 (the lexer rejects `~`) | `lexer.sig:375-468` | P2 | B11 |
| `NATURAL JOIN`, `LATERAL`, parenthesized join trees, set-returning functions in FROM (`generate_series`) | Join forms | 42601 or 0A000 | `parser.sig:1127-1190` | P2 | B11 |
| `TIME`, `TIMETZ`; non-ISO date input (`'Jan 1 2026'`); ISO 8601 durations (`'P1D'`); zone abbreviations (`EST`) | Types and input styles | 42601 for types; 22007 or 22023 for input | `parser.sig:555-559`, `temporal.sig:249-319`, `664-740` | P2 | B11 |
| `SELECT ... FOR UPDATE` over joins or grouping | Row locking for general queries | Error 0A000 (single-table queries only) | `parser.sig:910` | P2 | B11 |
| `CUBE`, `GROUPING SETS` | Grouping | 42883 (`CUBE(...)` is read as a function call) or 42601 | `parser.sig:1103-1117` | P3 | B11 |
| Collation: `COLLATE`, locale-aware ordering, case-insensitive ILIKE beyond ASCII | Database and column collations | Text orders as bytes (the C collation). COLLATE gives 42601. ILIKE folds only ASCII. | `types.sig:491-498`, `expr.sig:222-224` | P2 | B11 |
| Non-ASCII unquoted identifiers; identifiers over 63 bytes | Accepted; long names truncated to 63 bytes with a NOTICE | 42601 (non-ASCII); 54000 `NameCapacity` (long names) | `lexer.sig:375-385`, `parser.sig:132-136`, `ramsql/src/server.sig:84` | P3 | B11 |
| Default capacities: 8 columns, 4 FROM items, 8 WHERE leaf predicates, 128-byte text cells | No such limits | 54000/54001 on ordinary schemas. These are build options. | `ramsql/build.sig:112-118` | P0 (build profile) | B11 (with the capacity owner) |
| NUMERIC beyond 64 significant digits or 120 decimal places | Up to 131072 + 16383 digits | Error 54000 (`NumericCapacity`) | `numeric.sig:20-24`, `134-140` | P3 | B11 |
| More than one statement per query string | Simple-query protocol runs them in order | 42601 at the engine (`finish()`). Whether the wire layer splits strings first is unverified. | `parser.sig:115-118` | P2 | B11 (B1) |

## 2. Known divergences from PostgreSQL

| Divergence | PostgreSQL | RAMSQL at ZPM 2f060c2 | Evidence | Finding | Priority | Lane |
| --- | --- | --- | --- | --- | --- | --- |
| Timestamps near the upper range limit, DATE to TIMESTAMP after year 294276, `date ± bigint`, extreme interval units | 22008, or 42883 for `date + bigint` | **Crash** (Debug/ReleaseSafe) or **undefined behavior** (ReleaseFast) from i64 overflow | `temporal.sig:355`, `606-610`, `733-737`, `779`, `826`, `834`; `value.sig:529`; `binder.sig:510-517` | SQLF-01 | P0 | B7 |
| `'1-2'::interval`, `'3 4:05:06'::interval` | `1 year 2 mons`, `3 days 04:05:06` | **Silently wrong**: `-00:00:01`, `04:05:09` | `temporal.sig:716-729` | SQLF-02 | P1 | B7 |
| `SELECT INTERVAL '1' DAY` | `1 day` | **Silently wrong**: 1 second in a column named `day` | `parser.sig:1747-1758`, `1090` | SQLF-02 | P1 | B7 |
| `jsonb \|\| '{"b":2}'` | jsonb merge | **Silently wrong**: text concatenation | `binder.sig:1509-1514` | SQLF-04 | P1 | B7 |
| `date - date` with an infinite operand | Error 22008 | **Silently wrong**: 2147483647, or a value outside the int4 range in an int4 column | `value.sig:530` | SQLF-05 | P2 | B7 |
| JSONB value whose rendered text exceeds the output buffer (for example many `1e-120` numbers) | Stores and reads back | Insert succeeds; text output then fails with 54000 | `jsonb.sig:508-512`, `value.sig:183` | SQLF-09 | P2 | B7 |
| `SELECT t.x FROM t, t` | Error 42712 | **Accepted**; binds to the first `t` | `binder.sig:2786` | SQLF-10 | P3 | B7 |
| `ORDER BY` a name that two output columns share | Error 42702 | **Accepted**; uses the first match | `binder.sig:2418`, `2392` | SQLF-10 | P3 | B7 |
| `FROM a, b JOIN c ON a.x = c.x` | Error 42P01 (invalid reference to `a`) | **Accepted** | `binder.sig:2791` | SQLF-10 | P3 | B7 |
| `SELECT x ISNULL FROM t` | Boolean IS NULL test | **Silently wrong**: returns `x` in a column named `isnull` | `parser.sig:1234-1259` | SQLF-10 | P2 | B7 |
| Column named `key` | Allowed (KEY is unreserved) | Error 42601 unless quoted | `lexer.sig:196` | SQLF-11 | P2 | B7 |
| `'NaN'::float8 / 0` | NaN (from PostgreSQL source; unverified live) | Error 22012 | `float_text.sig:238` | SQLF-11 | P3 | B7 |
| `'1._5'::numeric` | Error 22P02 (from PostgreSQL source; unverified live) | Accepted as 1.5 | `numeric.sig:174-183` | SQLF-11 | P3 | B7 |
| Casting multi-byte text to NAME at 63 bytes | Clips on a character boundary | May split a UTF-8 character | `value.sig:290`, `424`, `428` | SQLF-11 | P3 | B7 |
| TIMESTAMP(p) rounding at the upper limit | Error 22008 | Returns an out-of-range value | `temporal.sig:377-384` | SQLF-11 | P3 | B7 |
| Fractional interval units, for example `'0.5 microseconds'` | `rint` (half to even) (unverified) | Half up | `temporal.sig:606-633` | SQLF-11 | P3 | B7 |
| Zone names such as `CET`, `EET`, `MET`, `WET` in timestamptz input | Abbreviation table, fixed offset (unverified) | Passed to the tzdata resolver, which may apply DST (unverified) | `temporal.sig:360-365` | SQLF-11 | P3 | B7 |
| `pg_get_expr` and `column_default` text for quoted or mixed-case identifiers and typmod casts | Quoted where needed; typmod shown | Raw spelling; typmod dropped | `deparse.sig:205-207`, `252` | SQLF-11 | P3 | B7 |
| ILIKE and text ordering with non-ASCII text | Locale rules | ASCII-only folding; byte order | `expr.sig:222-224`, `types.sig:491-498`, `jsonb.sig:750` | SQLF-11 | P2 | B7 |

Closed at 2f060c2: comparisons mixing REAL with integer or NUMERIC operands
(`16777217 = 16777216::real`, `0.1 = 0.1::real`) now use float8 as
PostgreSQL does (SQLF-03; `value.sig:131-134`, tests in
`test_sql_quantified.sig:75-81`). Also closed since the audit pin:

- LIKE ... ESCAPE;
- IS [NOT] DISTINCT FROM;
- quantified ANY/SOME/ALL over subqueries;
- BETWEEN SYMMETRIC;
- GREATEST/LEAST;
- ROLLUP/GROUPING and `GROUP BY ()`;
- basic window functions;
- `to_char` for timestamps.

Parameter-splicing issues are tracked as NET-01 and NET-02 (lane B1), not
here.
