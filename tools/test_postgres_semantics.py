"""Step-aware differential semantics suites against native PostgreSQL.

Each case carries a mandatory float `step` (the plan step that closes it).
`--step N` executes every selected case with case.step <= N; every executed
case must match the reference. Cases above N are listed as skipped in the
report and on stdout, never silently dropped. Cases with `restart` statements
are durable-restart variants: after `statements`, RAMSQL is killed and restarted
on the same --data WAL, the reference container is crash-restarted, and the
`restart` statements are compared. They require --durable and are otherwise
listed as skipped. A statement is SQL, (SQL, parameters) or (SQL, parameters,
{"binary": True}) for a binary-result cursor. Every event also records the
client's ParameterStatus view of TimeZone, DateStyle and IntervalStyle. The
reference image's tzdata is passed to RAMSQL (--timezone-dir) and to the
Python client (PYTHONTZPATH). Matching is necessary evidence, not proof of
full parity.

    python tools/test_postgres_semantics.py --suite selfcheck --step 1.4
    python tools/test_postgres_semantics.py --suite q01,q02 --step 2 --durable --scram

Exit codes: 0 every executed case matches; 1 differences; 2 harness error or
no case executed.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sys

from test_postgres_compat import (BASE, ROOT, candidate_mode, differences, execute, json_value, native_postgres,
                                  reference_timezone_dir, restart_native, running_ramsql)


@dataclass(frozen=True)
class Case:
    name: str
    suite: str
    step: float
    setup: tuple = BASE
    statements: tuple = ()
    restart: tuple = ()
    tables: tuple[str, ...] = ("aux", "compat")
    feature: str = ""

    def __post_init__(self):
        # M3: the step is mandatory and must be a float so ordering is explicit.
        if not isinstance(self.step, float):
            raise TypeError(f"case {self.name}: step must be a float, got {self.step!r}")
        for statement in self.setup + self.statements + self.restart:
            if not (isinstance(statement, str) or (isinstance(statement, tuple) and len(statement) in (2, 3))):
                raise TypeError(f"case {self.name}: statement must be SQL, (SQL, parameters) or (SQL, parameters, options)")


# Seed suites; each later FEAT fills its own suite. `selfcheck` verifies the
# runner itself (step filtering and a trivially matching case).
SUITES: dict[str, tuple[Case, ...]] = {name: () for name in
                                       ("q01", "q02", "q03", "q04", "q06", "q07", "q08", "q09", "q10", "p01")}
# Q01 (design §5.1, step 1.4): typed expressions and complete boolean
# predicates. BASE rows: (1, TRUE, 'one'), (2, FALSE, NULL), (3, NULL, 'three').
WIDE = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, v INTEGER)",) + tuple(
    f"INSERT INTO compat VALUES ({identity}, -1)" for identity in range(1, 401))
ATOMIC_UPDATE = "UPDATE compat SET v = 100 / (id - 399)"
ATOMIC_CHECK = ("SELECT id FROM compat WHERE v <> -1 ORDER BY id", "SELECT v FROM compat WHERE id = 398",
                "SELECT id, v FROM compat WHERE id >= 397 ORDER BY id")
SUITES["q01"] = (
    Case("q01_or_and_precedence", "q01", 1.4, feature="OR < AND precedence", statements=(
        "SELECT id FROM compat WHERE id = 1 OR id = 2 AND flag ORDER BY id",
        "SELECT id FROM compat WHERE id = 3 OR id = 2 AND NOT flag OR label = 'one' ORDER BY id",
        "SELECT TRUE OR FALSE AND FALSE, (TRUE OR FALSE) AND FALSE",
        "SELECT label FROM compat WHERE id = 1 OR id = 3 ORDER BY id")),
    Case("q01_nested_parentheses", "q01", 1.4, feature="nested parentheses", statements=(
        "SELECT id FROM compat WHERE ((id = 1) OR ((id = 3) AND (label = 'three'))) ORDER BY id",
        "SELECT id FROM compat WHERE (((id > 1)) AND ((NOT (id = 3)))) ORDER BY id",
        "SELECT ((1 + 2) * (3 - (4 - 5))) % 5")),
    Case("q01_not", "q01", 1.4, feature="NOT", statements=(
        "SELECT id FROM compat WHERE NOT id = 1 ORDER BY id",
        "SELECT id FROM compat WHERE NOT flag ORDER BY id",
        "SELECT id FROM compat WHERE NOT NOT flag ORDER BY id",
        "SELECT NOT NULL, NOT TRUE, NOT FALSE")),
    Case("q01_null_logic", "q01", 1.4, feature="three-valued AND/OR/NOT", statements=(
        "SELECT id, flag OR TRUE, flag AND FALSE, flag OR FALSE, flag AND TRUE, NOT flag FROM compat ORDER BY id",
        "SELECT NULL AND FALSE, NULL AND TRUE, NULL OR TRUE, NULL OR FALSE",
        "SELECT id FROM compat WHERE flag OR label = 'three' ORDER BY id",
        "SELECT id FROM compat WHERE NOT (flag AND label IS NULL) ORDER BY id")),
    Case("q01_null_in_between", "q01", 1.4, feature="NULL logic in IN and BETWEEN", statements=(
        "SELECT id, label IN ('one', NULL), label NOT IN ('one', NULL), id IN (1, 3), id NOT IN (1, 3) FROM compat ORDER BY id",
        "SELECT id, id BETWEEN 1 AND NULL, id NOT BETWEEN 2 AND 3, id BETWEEN 2 AND 3, NULL BETWEEN 1 AND 2 FROM compat ORDER BY id",
        "SELECT id FROM compat WHERE id IN (3, 1, NULL) ORDER BY id",
        "SELECT id FROM compat WHERE id NOT IN (2, NULL) ORDER BY id",
        "SELECT id FROM compat WHERE id NOT BETWEEN 2 AND 2 ORDER BY id")),
    Case("q01_is_tests", "q01", 1.4, feature="IS [NOT] TRUE|FALSE|UNKNOWN|NULL", statements=(
        "SELECT id, flag IS TRUE, flag IS NOT TRUE, flag IS FALSE, flag IS NOT FALSE FROM compat ORDER BY id",
        "SELECT id, flag IS UNKNOWN, flag IS NOT UNKNOWN, label IS NULL, label IS NOT NULL FROM compat ORDER BY id",
        "SELECT id FROM compat WHERE flag IS NOT TRUE ORDER BY id",
        "SELECT id FROM compat WHERE (id = 3 AND flag) IS UNKNOWN ORDER BY id",
        "SELECT NULL IS NULL, 1 IS NOT NULL")),
    Case("q01_where_integer", "q01", 1.4, feature="WHERE integer -> 42804", statements=(
        "SELECT id FROM compat WHERE id",
        "SELECT id FROM compat WHERE id AND flag",
        "SELECT 1 IS TRUE")),
    Case("q01_undefined_operator", "q01", 1.4, feature="text + integer -> 42883", statements=(
        "SELECT label + 1 FROM compat",
        "SELECT id FROM compat WHERE id = TRUE",
        "SELECT id || 1 FROM compat",
        "SELECT NULL + NULL")),
    Case("q01_invalid_text", "q01", 1.4, feature="'x'::integer -> 22P02", statements=(
        "SELECT 'x'::integer",
        "SELECT id FROM compat WHERE id = 'x'",
        "SELECT ' 42 '::integer, '-7'::bigint, 'yes'::boolean, 'off'::boolean",
        "SELECT 'maybe'::boolean",
        "SELECT label::integer FROM compat WHERE id = 1")),
    Case("q01_integer_overflow", "q01", 1.4, feature="2147483647 + 1 -> 22003", statements=(
        "SELECT 2147483647 + 1",
        "SELECT -2147483648 - 1",
        "SELECT 2147483647::bigint + 1, 2147483647 + 2147483648, -2147483648",
        "SELECT 9223372036854775807 + 1",
        "SELECT 2147483648::integer",
        "SELECT id * 2147483647 FROM compat ORDER BY id")),
    Case("q01_division_by_zero", "q01", 1.4, feature="division by zero -> 22012", statements=(
        "SELECT 1 / 0",
        "SELECT id % 0 FROM compat",
        "SELECT 7 / 2, -7 / 2, 7 % 3, -7 % 3, 7 % -3",
        "SELECT id FROM compat WHERE 10 / (id - 2) > 0 ORDER BY id",
        "SELECT NULL / 0")),
    Case("q01_case", "q01", 1.4, feature="CASE", statements=(
        "SELECT id, CASE WHEN flag THEN 'yes' WHEN NOT flag THEN 'no' ELSE 'unknown' END FROM compat ORDER BY id",
        "SELECT CASE id WHEN 1 THEN 'one' WHEN 2 THEN 'two' END AS spelled FROM compat ORDER BY id",
        "SELECT id, CASE WHEN id > 1 THEN id * 10 ELSE 2147483648 END FROM compat ORDER BY id",
        "SELECT CASE WHEN id = 1 THEN 'x' ELSE 1 END FROM compat",
        "SELECT CASE WHEN 1 THEN 1 END")),
    Case("q01_coalesce_nullif", "q01", 1.4, feature="COALESCE, NULLIF", statements=(
        "SELECT id, COALESCE(label, 'none'), NULLIF(id, 2), COALESCE(NULL, NULL, 3), NULLIF(label, 'one') FROM compat ORDER BY id",
        "SELECT COALESCE(flag, id = 3) FROM compat ORDER BY id",
        "SELECT COALESCE(label, 1) FROM compat")),
    Case("q01_like", "q01", 1.4, feature="LIKE, ILIKE", statements=(
        "SELECT id FROM compat WHERE label LIKE 't%' ORDER BY id",
        "SELECT id FROM compat WHERE label ILIKE 'ON_' ORDER BY id",
        "SELECT id FROM compat WHERE label NOT LIKE '%e' ORDER BY id",
        "SELECT id, label LIKE '%h%', label NOT ILIKE '%E' FROM compat ORDER BY id",
        "SELECT 'a%' LIKE 'a\\%', 'ab' LIKE 'a\\%', 'abc' LIKE '_b_', 'abc' LIKE 'a%c%'",
        "SELECT id LIKE '1' FROM compat")),
    Case("q01_concat", "q01", 1.4, feature="||", statements=(
        "SELECT label || '!', id || ':' || label, flag || 'x', 'a' || NULL FROM compat ORDER BY id",
        "SELECT 'x' || 'y' || 'z', NULL || NULL")),
    Case("q01_names_types", "q01", 1.4, feature="output column names and types", statements=(
        "SELECT id AS \"Ident\", label l, TRUE, 1::text, CAST(id AS bigint), id::text, 'lit', NULL FROM compat WHERE id = 1",
        "SELECT c.id FROM compat AS c WHERE compat.id = 1",
        "SELECT c.label FROM compat c WHERE c.id = 3",
        "SELECT compat.id FROM compat WHERE compat.id = 2",
        "SELECT id + 0 AS k, -id FROM compat ORDER BY k DESC")),
    Case("q01_order_by_expressions", "q01", 1.4, feature="ORDER BY expressions, positions and output names", statements=(
        "SELECT id, label FROM compat ORDER BY 2 DESC NULLS LAST, id",
        "SELECT id FROM compat ORDER BY id % 2, id DESC",
        "SELECT id AS k FROM compat ORDER BY k DESC",
        "SELECT label FROM compat ORDER BY COALESCE(label, 'a')",
        "SELECT id FROM compat ORDER BY 3")),
    Case("q01_unknown_literals", "q01", 1.4, feature="untyped literal resolution", statements=(
        "SELECT id FROM compat WHERE id = '2'",
        "SELECT id FROM compat WHERE flag = 't' ORDER BY id",
        "SELECT id FROM compat WHERE label = 'one' AND '1' = '1'",
        "SELECT '1' + '2'",
        "INSERT INTO compat VALUES ('4', 'yes', 44)",
        "SELECT id, flag, label FROM compat WHERE id = 4")),
    Case("q01_update_expressions", "q01", 1.4, feature="expression SET", statements=(
        "UPDATE compat SET label = label || '-x' WHERE id <> 2",
        "SELECT id, label FROM compat ORDER BY id",
        "UPDATE compat SET id = id + 10, flag = NOT flag WHERE id = 3",
        "SELECT id, flag FROM compat ORDER BY id",
        "UPDATE compat SET label = CASE WHEN flag THEN 'on' ELSE 'off' END",
        "SELECT id, label FROM compat ORDER BY id",
        "UPDATE compat SET id = 1 WHERE id = 2",
        "SELECT id FROM compat ORDER BY id")),
    Case("q01_atomic_failure", "q01", 1.4, feature="failure after many rows changes nothing",
         setup=WIDE, statements=(ATOMIC_UPDATE,) + ATOMIC_CHECK),
    Case("q01_atomic_failure_restart", "q01", 1.4, feature="failure after many rows survives restart",
         setup=WIDE, statements=(ATOMIC_UPDATE, "UPDATE compat SET v = 0 WHERE id = 1"), restart=ATOMIC_CHECK),
    Case("q01_numeric_promotion", "q01", 3.2, feature="mixed int2/int4/int8/numeric promotion (M3)", statements=(
        "SELECT 1::smallint + 2, 1::smallint + 2::bigint, 1 + 1.5, 2::bigint * 1.25",)),
)
# Q02 (design §5.2(d), step 3.2): NUMERIC, SMALLINT, REAL/DOUBLE. Statements
# keep at most 8 output columns (RAMSQL's default column capacity).
AMOUNTS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, amount NUMERIC(18,4), ratio DOUBLE PRECISION, small SMALLINT)",)
SUITES["q02"] = (
    Case("q02_probe", "q02", 3.2, feature="numeric probe", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, amount NUMERIC(18,4))",),
         statements=("INSERT INTO compat VALUES (1, 12.3456)", "SELECT amount FROM compat", "SELECT amount::text FROM compat")),
    Case("q02_typmod_rounding", "q02", 3.2, feature="NUMERIC(18,4) rounding half away from zero", setup=AMOUNTS, statements=(
        "INSERT INTO compat (id, amount) VALUES (1, 12.34565)",
        "INSERT INTO compat (id, amount) VALUES (2, -12.34565)",
        "INSERT INTO compat (id, amount) VALUES (3, '0.00005')",
        "SELECT id, amount, amount::text FROM compat ORDER BY id",
        "SELECT 12.34565::numeric(18,4), 2.5::numeric(5,0), (-2.5)::numeric(5,0), 123.456::numeric(5,-1), 0.5::numeric(1,0)")),
    Case("q02_typmod_overflow", "q02", 3.2, feature="NUMERIC(5,2) overflow -> 22003", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, amount NUMERIC(5,2))",), statements=(
        "INSERT INTO compat VALUES (1, 1000)",
        "INSERT INTO compat VALUES (1, 999.999)",
        "INSERT INTO compat VALUES (1, 999.994)",
        "INSERT INTO compat VALUES (2, 'Infinity')",
        "INSERT INTO compat VALUES (3, 'NaN')",
        "UPDATE compat SET amount = amount * 10 WHERE id = 1",
        "SELECT id, amount FROM compat ORDER BY id",
        "SELECT 1000::numeric(5,2)",
        "SELECT 1::numeric(1001,0)")),
    Case("q02_division_scales", "q02", 3.2, feature="select_div_scale", statements=(
        "SELECT 1/3::numeric, 10/4::numeric, 10::numeric/4, 2/3.0, 100.0/3, 0.001/7, 1e-10/3, 123456789/0.001",
        "SELECT 7.0 / 2, 7 / 2.0, 1 / 7::numeric, 1000000 / 7.0, 1e10/3, 5 % 3.0, -7.5 % 2, 7 % 2.5",
        "SELECT 1.0 / 0",
        "SELECT 1 % 0.0")),
    Case("q02_multiplication_scales", "q02", 3.2, feature="product scale = sum of scales", statements=(
        "SELECT 1.0 * 1.00, 0.0001 * 0.0001, 1.5 * 2.25, -1.5 * 0, 2::bigint * 1.25, 1.10 + 2.205, 1.10 - 2.205",)),
    Case("q02_negative_zero", "q02", 3.2, feature="no negative zero in NUMERIC", statements=(
        "SELECT -0.0::numeric, '-0'::numeric, 0.0 * -1, -0.00, '-0.000'::numeric::text, (-0.0)::numeric(5,2)",
        "SELECT '-0'::float8, -(0.0::float8), '-0'::float8::text, '-0'::float8 = 0::float8")),
    Case("q02_special_values", "q02", 3.2, feature="NaN, Infinity and NaN ordering", setup=AMOUNTS + (
        "INSERT INTO compat (id, ratio) VALUES (1, 1.5)", "INSERT INTO compat (id, ratio) VALUES (2, 'NaN')",
        "INSERT INTO compat (id, ratio) VALUES (3, '-Infinity')", "INSERT INTO compat (id, ratio) VALUES (4, 'Infinity')"), statements=(
        "SELECT 'NaN'::numeric, 'Infinity'::numeric, '-inf'::numeric, 'NaN'::numeric > 'Infinity'::numeric, 'NaN'::numeric = 'NaN'::numeric",
        "SELECT 'Infinity'::numeric + 1, 'Infinity'::numeric - 'Infinity'::numeric, 1 / 'Infinity'::numeric, 'NaN'::numeric * 0",
        "SELECT id, ratio::text FROM compat ORDER BY ratio, id",
        "SELECT id FROM compat ORDER BY ratio DESC, id",
        "SELECT id FROM compat WHERE ratio > 1 ORDER BY id",
        "SELECT 'NaN'::float8 = 'NaN'::float8, 'NaN'::float8 > 'Infinity'::float8, 'NaN'::numeric::float8::text")),
    Case("q02_smallint", "q02", 3.2, feature="SMALLINT bounds -> 22003", setup=AMOUNTS, statements=(
        "SELECT (-32768)::smallint, 32767::smallint, 1::smallint + 2, 1::smallint + 2::bigint, 2::smallint * 3::smallint",
        "SELECT 32768::smallint",
        "SELECT -32768::smallint",
        "SELECT 32767::smallint + 1::smallint",
        "SELECT 2.5::smallint, (-2.5)::smallint, '7'::smallint",
        "INSERT INTO compat (id, small) VALUES (1, 40000)",
        "INSERT INTO compat (id, small) VALUES (1, -32768)",
        "UPDATE compat SET small = small - 1 WHERE id = 1",
        "SELECT id, small FROM compat")),
    Case("q02_float_output", "q02", 3.2, feature="REAL/DOUBLE output (float8out, extra_float_digits=1)", statements=(
        "SELECT 0.1::float8 + 0.2::float8, (0.1::float8 + 0.2::float8)::text, 1e15::float8::text, 1e14::float8::text, 1e-5::float8::text, 0.0001::float8::text",
        "SELECT 1e100::float8::text, 1.5e-7::float8::text, 123456789012345678::float8::text, (1.0/3)::float8::text, '4e-320'::float8::text",
        "SELECT 1e6::float4::text, 100000::float4::text, 1234567.5::float4::text, 3.4e38::float4::text, 1.5::float4 + 2.5::float4, 1.5::float4 + 1",
        "SELECT 0.1::float8, 1e15::float8, 1.5::float4, (1.5::float4 + 1)")),
    Case("q02_float_errors", "q02", 3.2, feature="float overflow, underflow and division by zero", statements=(
        "SELECT '1e308'::float8 * 10",
        "SELECT '1e-300'::float8 * '1e-300'::float8",
        "SELECT 1::float8 / 0",
        "SELECT 1.5::float8 % 1",
        "SELECT '3.5e38'::float4",
        "SELECT 'NaN'::float8::integer",
        "SELECT '1e10'::float8::integer")),
    Case("q02_rounding_casts", "q02", 3.2, feature="numeric->int half away vs float->int rint", statements=(
        "SELECT 2.5::integer, 2.5::float8::integer, 3.5::float8::integer, (-2.5)::integer, (-2.5)::float8::integer, 0.5::bigint, 0.5::float8::bigint",
        "SELECT 2.5::float8::numeric, 0.1::float8::numeric, (1.0/3)::float8::numeric, 1.5::float4::numeric, 12.5::numeric::float8, 1e20::float8::numeric",
        "SELECT 'NaN'::numeric::integer",
        "SELECT 2147483647.5::integer")),
    Case("q02_float_grammar", "q02", 3.2, feature="float8in grammar vectors (design §4.2)", statements=(
        "SELECT '0x1p3'::float8::text, '5.'::float8::text, '.5'::float8::text, ' 1.5 '::float8::text, 'inf'::float8::text, 'nan'::float8::text",
        "SELECT '-Infinity'::float8::text, '+1.5e2'::float8::text, '1E-2'::float8::text, '  -0x.8p1 '::float8::text, 'INFINITY'::float4::text, 'NaN'::float4::text",
        "SELECT '1_0'::float8",
        "SELECT ''::float8",
        "SELECT '1e400'::float8",
        "SELECT '1e-400'::float8",
        "SELECT '1.5x'::float8",
        "SELECT 'infinit'::float8")),
    Case("q02_numeric_input", "q02", 3.2, feature="numeric_in forms and errors", statements=(
        "SELECT ' 12.50 '::numeric, '1e2'::numeric, '1.50E-3'::numeric, '+.5'::numeric, '5.'::numeric, '1_000.5'::numeric, 99999999999999999999",
        "SELECT 'abc'::numeric",
        "SELECT '1e'::numeric",
        "SELECT '.'::numeric")),
    Case("q02_comparisons", "q02", 3.2, feature="cross-type numeric comparisons and promotion", setup=AMOUNTS + (
        "INSERT INTO compat VALUES (1, 1.5, 1.5, 1)", "INSERT INTO compat VALUES (2, 2, 2.25, 2)", "INSERT INTO compat VALUES (3, -1, -1, 3)"), statements=(
        "SELECT 1 = 1.0, 1.5 = 1.5::float8, 1 BETWEEN 0.5 AND 2, 2 IN (1.5, 2.0), 1.50 = 1.5, 3 > 2.99",
        "SELECT id FROM compat WHERE amount = 2 ORDER BY id",
        "SELECT id FROM compat WHERE amount > small ORDER BY id",
        "SELECT id, amount + small, amount * ratio, ratio + small, small + 1.5 FROM compat ORDER BY id",
        "SELECT id FROM compat WHERE amount IN (1.5, -1) ORDER BY amount",
        "SELECT id, amount FROM compat ORDER BY amount DESC")),
    Case("q02_prepared_parameters", "q02", 3.2, feature="Decimal and float parameters, text and binary cursors", setup=AMOUNTS, statements=(
        ("INSERT INTO compat (id, amount, ratio) VALUES (%s, %s, %s)", (1, Decimal("12.34565"), 0.1)),
        ("INSERT INTO compat (id, amount, ratio) VALUES (%t, %t, %t)", (2, Decimal("-7.25"), 2.5e-7)),
        ("INSERT INTO compat (id, amount, ratio) VALUES (%s, %s, %s)", (3, Decimal("NaN"), float("inf"))),
        ("SELECT id, amount, ratio FROM compat WHERE amount = %s", (Decimal("12.3457"),)),
        ("SELECT id, amount, ratio FROM compat ORDER BY id", None, {"binary": True}),
        ("SELECT id, amount, ratio FROM compat WHERE ratio < %s ORDER BY id", (1.0,), {"binary": True}),
        ("SELECT %s::numeric + 1, %s::float8 * 2", (Decimal("0.5"), 0.25)))),
    Case("q02_aggregates", "q02", 3.4, feature="SUM/AVG/MIN/MAX over numeric (step 3.4)", setup=AMOUNTS + (
        "INSERT INTO compat (id, amount) VALUES (1, 1.5)", "INSERT INTO compat (id, amount) VALUES (2, 2.25)"), statements=(
        "SELECT SUM(amount), AVG(amount), MIN(amount), MAX(amount) FROM compat",)),
    Case("q02_restart", "q02", 3.2, feature="numeric/float values survive a durable restart", setup=AMOUNTS, statements=(
        "INSERT INTO compat VALUES (1, 12.34565, 0.1, -32768)",
        "INSERT INTO compat VALUES (2, 'NaN', '-Infinity', 32767)",
        "INSERT INTO compat VALUES (3, -0.00005, '-0', NULL)",
        "UPDATE compat SET amount = amount / 3 WHERE id = 1"), restart=(
        "SELECT id, amount, ratio, small FROM compat ORDER BY id",
        "SELECT id, amount::text, ratio::text FROM compat ORDER BY amount",
        "SELECT id FROM compat WHERE amount = 4.1152")),
)

# Q03 (design §5.3(d), step 3.2): DATE/TIMESTAMP/TIMESTAMPTZ/INTERVAL, zones.
EVENTS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, happened TIMESTAMPTZ, local TIMESTAMP(3), day DATE, span INTERVAL)",
          "INSERT INTO compat VALUES (1, '2026-10-22T12:00:00Z', '2026-10-22 12:00:00.1239', '2026-10-22', '1 day 02:00')",
          "INSERT INTO compat VALUES (2, '2026-03-08 06:59:59Z', '2026-03-08 02:30', '2026-03-08', '-1 mons')")
ZONE_CHECK = ("SELECT id, happened, happened::text FROM compat ORDER BY id", "SHOW TimeZone")
SUITES["q03"] = (
    Case("q03_probe", "q03", 3.2, feature="timestamp probe", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, happened TIMESTAMPTZ)",),
         statements=("INSERT INTO compat VALUES (1, '2026-10-22T12:00:00Z')", "SELECT happened FROM compat", "SELECT happened::text FROM compat")),
    Case("q03_iso_input", "q03", 3.2, feature="ISO 8601 input variants", statements=(
        "SELECT '2026-10-22T12:00:00Z'::timestamptz::text, '2026-10-22 12:00:00+02'::timestamptz::text, '2026-10-22 12:00:00-0230'::timestamptz::text, '2026-10-22T12:00:00.5+05:30'::timestamptz::text",
        "SELECT '2026-10-22 12:00 America/New_York'::timestamptz::text, '2026-10-22T12:00'::timestamp::text, '2026-10-22 12:00:00+09'::timestamp::text, ' 2026-01-05 '::date::text",
        "SELECT '2026-10-22 12:00:00.123456789'::timestamp::text, '2026-10-22 24:00:00'::timestamp::text, '0044-03-15 BC'::date::text, '2026-10-22 12:00:00+05:30:15'::timestamptz::text",
        "SELECT '2026-10-22'::timestamptz, '2026-10-22 12:00'::timestamp, '2026-10-22'::date")),
    Case("q03_specials", "q03", 3.2, feature="infinity, -infinity, epoch", statements=(
        "SELECT 'infinity'::timestamp::text, '-infinity'::timestamptz::text, 'epoch'::timestamp::text, 'epoch'::timestamptz::text, 'infinity'::date::text, '-infinity'::date::text",
        "SELECT 'infinity'::timestamp > '294276-01-01'::timestamp, 'epoch'::date, '-infinity'::date < '4713-01-01 BC'::date, 'infinity'::timestamptz + interval '1 day' = 'infinity'::timestamptz")),
    Case("q03_precision", "q03", 3.2, feature="TIMESTAMP(p) rounding", setup=EVENTS, statements=(
        "SELECT '2026-01-01 12:34:56.5555'::timestamp(3)::text, '2026-01-01 12:34:56.9995'::timestamp(3)::text, '2026-01-01 12:34:56.5'::timestamp(0)::text, '1999-12-31 23:59:59.95'::timestamptz(1)::text",
        "SELECT id, local::text FROM compat ORDER BY id",
        "UPDATE compat SET local = local + interval '0.0004 seconds' WHERE id = 1",
        "SELECT local::text FROM compat WHERE id = 1")),
    Case("q03_out_of_range", "q03", 3.2, feature="out of range -> 22008, bad text -> 22007", statements=(
        "SELECT '294277-01-01'::timestamp",
        "SELECT '2026-13-01'::date",
        "SELECT '2026-02-30'::date",
        "SELECT '2026-10-22 25:00'::timestamp",
        "SELECT '5874898-01-01'::date",
        "SELECT 'garbage'::date",
        "SELECT '2026-10-22 12:00 Mars/Base'::timestamptz",
        "SELECT '1 fortnight'::interval")),
    Case("q03_date_arithmetic", "q03", 3.2, feature="date +- integer, date - date", statements=(
        "SELECT date '2026-01-31' + 1, 1 + date '2026-12-31', date '2026-03-01' - 1, date '2026-03-01' - date '2026-01-01', date '2024-03-01' - date '2024-02-01'",
        "SELECT (date '2026-01-31' + 1)::text, date '2026-01-31' + interval '1 month', date '2026-01-31' - interval '1 hour'")),
    Case("q03_interval_arithmetic", "q03", 3.2, feature="timestamp +- interval, timestamp - timestamp, interval output", statements=(
        "SELECT timestamp '2026-01-31 10:00' + interval '1 month 2 hours', timestamp '2026-01-02 03:00' - timestamp '2026-01-01', timestamp '2024-02-29 00:00' + interval '1 year'",
        "SELECT (timestamp '2026-01-02 03:00' - timestamp '2026-01-01')::text, (timestamp '2026-01-01' - timestamp '2026-01-02 03:00')::text, (interval '1 day' - interval '26 hours')::text",
        "SELECT '1 year 2 mons 3 days 04:05:06.5'::interval::text, '-1 day 2 hours'::interval::text, '1 day -2 hours'::interval::text, '-1 year -2 mons'::interval::text, '25 hours'::interval::text, '0'::interval::text",
        "SELECT '1.5 days'::interval::text, '1.5 months'::interval::text, '1.5 years'::interval::text, '2 weeks'::interval::text, '@ 1 hour ago'::interval::text, '90 minutes'::interval::text",
        "SELECT -interval '1 day 02:00', interval '1 day' + interval '3 hours', '1 mon'::interval > '29 days'::interval, '1 day'::interval = '24 hours'::interval")),
    Case("q03_set_time_zone", "q03", 3.2, feature="SET TIME ZONE UTC/offset/number/name", setup=EVENTS, statements=(
        ("SET TIME ZONE 'UTC'",) + ZONE_CHECK + ("SET TIME ZONE '+05:30'",) + ZONE_CHECK + ("SET TIME ZONE 5",) + ZONE_CHECK +
        ("SET TIME ZONE -3.5",) + ZONE_CHECK + ("SET TIME ZONE 'America/New_York'",) + ZONE_CHECK + ("SET TIME ZONE 'UTC+3'",) + ZONE_CHECK +
        ("SET TIME ZONE 'Mars/Base'", "SHOW TimeZone", "SET TIME ZONE 'Europe/London'", "SELECT happened, day::timestamptz, local::timestamptz FROM compat ORDER BY id"))),
    Case("q03_dst", "q03", 3.2, feature="nonexistent and ambiguous local times (Appendix B.1)", statements=(
        "SET TIME ZONE 'America/New_York'",
        "SELECT '2026-03-08 02:30'::timestamptz::text, '2026-11-01 01:30'::timestamptz::text, '2026-03-08 03:00'::timestamptz::text, '2026-11-01 02:00'::timestamptz::text",
        "SELECT '2026-03-08 02:30'::timestamp AT TIME ZONE 'America/New_York', ('2026-11-01 01:30'::timestamp AT TIME ZONE 'America/New_York')::text",
        "SELECT '2026-03-08 01:00'::timestamptz + interval '1 day', '2026-03-08 01:00'::timestamptz + interval '24 hours', '2026-11-01 00:00'::timestamptz + interval '1 day'",
        "SELECT timestamptz '2026-10-22 12:00:00+02' AT TIME ZONE 'UTC', timestamptz '2026-07-01 12:00Z' AT TIME ZONE 'Asia/Tokyo', timestamp '2026-07-01 12:00' AT TIME ZONE 'Europe/Paris'",
        "SELECT '2026-03-08 02:30'::timestamptz, '2026-11-01 01:30'::timestamptz")),
    Case("q03_now", "q03", 3.2, feature="now() is the transaction start", statements=(
        "SELECT now() = now(), now() = CURRENT_TIMESTAMP, now() = statement_timestamp(), now() = transaction_timestamp()",
        "BEGIN",
        "SELECT now() = now(), now() = CURRENT_TIMESTAMP, now() = statement_timestamp(), clock_timestamp() >= now()",
        "SELECT now() = transaction_timestamp(), CURRENT_DATE = now()::date",
        "COMMIT",
        "SELECT now() > '2024-01-01'::timestamptz, now() < '2100-01-01'::timestamptz, now() - now()")),
    Case("q03_transactional_settings", "q03", 3.2, feature="SET TIME ZONE rolled back by ROLLBACK and ROLLBACK TO", setup=EVENTS, statements=(
        "BEGIN", "SET TIME ZONE 'Asia/Tokyo'", "SELECT happened FROM compat WHERE id = 1", "SHOW TimeZone", "ROLLBACK", "SHOW TimeZone",
        "SELECT happened FROM compat WHERE id = 1",
        "BEGIN", "SAVEPOINT a", "SET TIME ZONE 'Asia/Kolkata'", "SELECT happened FROM compat WHERE id = 1", "ROLLBACK TO SAVEPOINT a",
        "SHOW TimeZone", "SET TIME ZONE 'America/New_York'", "COMMIT", "SHOW TimeZone", "SELECT happened FROM compat WHERE id = 1",
        "BEGIN", "SET LOCAL TIME ZONE 'Asia/Tokyo'", "SHOW TimeZone", "SELECT happened FROM compat WHERE id = 1", "COMMIT", "SHOW TimeZone",
        "BEGIN", "SET TIME ZONE 'Europe/Berlin'", "SELECT missing FROM compat", "ROLLBACK", "SHOW TimeZone")),
    Case("q03_reset_and_canonical", "q03", 3.2, feature="RESET, DEFAULT, LOCAL and canonical zone names", statements=(
        "SET TIME ZONE 'america/new_york'", "SHOW TimeZone", "SET TIME ZONE 'EUROPE/paris'", "SHOW TimeZone",
        "SET TIME ZONE 'Australia/Sydney'", "RESET TimeZone", "SHOW TimeZone",
        "SET TIME ZONE 'Pacific/Auckland'", "RESET ALL", "SHOW TimeZone",
        "SET TIME ZONE 'Asia/Tokyo'", "SET TIME ZONE DEFAULT", "SHOW TimeZone",
        "SET TIME ZONE 'Asia/Tokyo'", "SET TIME ZONE LOCAL", "SHOW TimeZone",
        "SET timezone = 'Etc/GMT+5'", "SHOW timezone", "SELECT '2026-10-22T12:00:00Z'::timestamptz",
        "SET DateStyle = 'ISO, DMY'", "SHOW DateStyle", "RESET DateStyle", "SHOW DateStyle", "SHOW IntervalStyle")),
    Case("q03_binary_cursor", "q03", 3.2, feature="binary timestamp/date/interval round trip", setup=EVENTS, statements=(
        ("SELECT id, happened, local, day, span FROM compat ORDER BY id", None, {"binary": True}),
        "SET TIME ZONE 'America/New_York'",
        ("SELECT id, happened, local, day, span FROM compat ORDER BY id", None, {"binary": True}),
        ("SELECT 'infinity'::timestamptz, '-infinity'::date", None, {"binary": True}))),
    Case("q03_parameters", "q03", 3.2, feature="datetime parameters (aware, naive, date)", setup=EVENTS, statements=(
        ("INSERT INTO compat VALUES (%s, %s, %s, %s, %s)", (3, datetime(2026, 5, 1, 8, 30, 15, 250000, tzinfo=timezone.utc), datetime(2026, 5, 1, 8, 30, 15, 123456), date(2026, 5, 1), timedelta(days=2, hours=3))),
        ("INSERT INTO compat VALUES (%t, %t, %t, %t, %t)", (4, datetime(2026, 5, 1, 8, 30, tzinfo=timezone(timedelta(hours=-4))), datetime(1999, 12, 31, 23, 59, 59), date(1999, 12, 31), timedelta(seconds=90))),
        ("SELECT id FROM compat WHERE happened > %s ORDER BY id", (datetime(2026, 4, 1, tzinfo=timezone.utc),)),
        ("SELECT id, happened, local, day, span FROM compat WHERE id >= 3 ORDER BY id", None, {"binary": True}),
        "SELECT id, happened::text, local::text, span::text FROM compat ORDER BY id")),
    Case("q03_restart", "q03", 3.2, feature="temporal values survive a durable restart", setup=EVENTS, statements=(
        "INSERT INTO compat VALUES (3, 'infinity', '-infinity', 'infinity', '-1 years -2 mons +3 days -04:05:06.5')",
        "UPDATE compat SET span = span + interval '1 hour' WHERE id = 1"), restart=(
        "SELECT id, happened, local, day, span FROM compat ORDER BY id",
        "SELECT id, happened::text, local::text, day::text, span::text FROM compat ORDER BY happened DESC")),
)

# Q04 (design §5.4(d), step 3.2): JSON and JSONB.
DOCS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, payload JSONB, raw JSON)",
        "INSERT INTO compat VALUES (1, '{\"kind\":\"event\",\"tags\":[\"a\",\"b\"],\"n\":1.0}', '{\"kind\" : \"event\", \"kind\": \"dup\"}')",
        "INSERT INTO compat VALUES (2, '[10, 20, {\"x\": null}]', '[10,20]')",
        "INSERT INTO compat VALUES (3, 'null', 'null')",
        "INSERT INTO compat VALUES (4, '\"text\"', '\"text\"')")
SUITES["q04"] = (
    Case("q04_probe", "q04", 3.2, feature="jsonb probe", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, payload JSONB)",),
         statements=("INSERT INTO compat VALUES (1, '{\"kind\":\"event\"}')", "SELECT payload ->> 'kind' FROM compat", "SELECT payload, payload::text FROM compat")),
    Case("q04_navigation", "q04", 3.2, feature="-> and ->> chains, array indexes, missing keys", setup=DOCS, statements=(
        "SELECT id, payload -> 'tags' -> 0, payload -> 'tags' ->> -1, payload ->> 'kind', payload -> 'missing', payload -> 1 FROM compat ORDER BY id",
        "SELECT id, payload -> 2 -> 'x', payload -> -1, payload -> 5, payload -> -4, payload ->> 0 FROM compat ORDER BY id",
        "SELECT '{\"a\":{\"b\":[10,20,30]}}'::jsonb -> 'a' -> 'b' -> -1, '{\"a\":{\"b\":[10,20,30]}}'::jsonb -> 'a' ->> 'b', '[1,2]'::jsonb -> '1', '{\"a\":1}'::jsonb -> 1")),
    Case("q04_paths", "q04", 3.2, feature="#> and #>> with text-array paths", setup=DOCS, statements=(
        "SELECT id, payload #> '{tags,1}', payload #>> '{tags,0}', payload #> '{2,x}', payload #>> '{}', payload #> '{missing,0}' FROM compat ORDER BY id",
        "SELECT '{\"a\":{\"b\":[\"x\",\"y\"]}}'::jsonb #> '{a,b,-1}', '{\"a b\":1}'::jsonb #> '{\"a b\"}', '{\"a\":1}'::jsonb #> '{}'")),
    Case("q04_text_and_spacing", "q04", 3.2, feature="duplicate keys, key order, output spacing", setup=DOCS, statements=(
        "SELECT id, payload::text, raw::text FROM compat ORDER BY id",
        "SELECT '{\"b\": 1, \"a\": [1, 2.50, {\"c\": null}], \"aa\": \"x\", \"a\": true}'::jsonb::text, '{\"b\":1,\"a\":2,\"a\":3}'::json::text, '{\"b\":1,\"a\":2,\"a\":3}'::jsonb::text",
        "SELECT ' [ 1 , \"x\" ] '::jsonb::text, '{ }'::jsonb::text, '[]'::jsonb::text, '{\"\":0}'::jsonb::text, '{\"a\":1}'::json::jsonb::text, '{\"a\": 1}'::jsonb::json::text")),
    Case("q04_nulls", "q04", 3.2, feature="JSON null versus SQL NULL", setup=DOCS, statements=(
        "SELECT 'null'::jsonb IS NULL, '{\"a\":null}'::jsonb ->> 'a' IS NULL, '{\"a\":null}'::jsonb -> 'a' IS NULL, ('{\"a\":null}'::jsonb -> 'a')::text",
        "SELECT id, payload IS NULL, payload ->> 'kind' IS NULL FROM compat ORDER BY id",
        "SELECT id FROM compat WHERE payload = 'null' ORDER BY id")),
    Case("q04_unicode", "q04", 3.2, feature="\\u escapes, surrogate pairs and \\u0000", statements=(
        "SELECT '\"\\u00e9\"'::jsonb::text, '\"\\ud83d\\ude00\"'::jsonb::text, '\"\\u00e9\"'::json::text, '\"a\\tb\\\"c\\\\d\\u0001\"'::jsonb::text, '\"\\/\"'::jsonb::text",
        "SELECT '\"\\u00e9\"'::jsonb #>> '{}', '{\"k\":\"\\u00e9\"}'::jsonb ->> 'k'",
        "SELECT '\"\\u0000\"'::jsonb",
        "SELECT '\"\\u0000\"'::json::text",
        "SELECT '\"\\ud83d\"'::jsonb",
        "SELECT '\"\\ude00\"'::jsonb")),
    Case("q04_invalid", "q04", 3.2, feature="invalid documents -> 22P02", statements=(
        "SELECT '{\"a\":}'::jsonb", "SELECT '[1,2'::json", "SELECT '{a:1}'::jsonb", "SELECT 'tru'::jsonb", "SELECT '01'::jsonb",
        "SELECT '1.'::jsonb", "SELECT '\"x\" \"y\"'::jsonb", "SELECT ''::jsonb", "SELECT '[\"\\x\"]'::jsonb")),
    Case("q04_numbers", "q04", 3.2, feature="JSONB numbers are NUMERIC (1.0 preserved)", statements=(
        "SELECT '1.0'::jsonb::text, '1e2'::jsonb::text, '-0'::jsonb::text, '1.50E-3'::jsonb::text, '[1.10, 2]'::jsonb::text, '12345678901234567890'::jsonb::text",
        "SELECT '1'::jsonb = '1.0'::jsonb, '{\"n\":1.0}'::jsonb = '{\"n\":1}'::jsonb, '[1.5]'::jsonb @> '[1.50]', '{\"n\":1.0}'::jsonb ->> 'n'")),
    Case("q04_containment", "q04", 3.2, feature="@>, <@ and ?", setup=DOCS, statements=(
        "SELECT '{\"a\":1,\"b\":{\"c\":2}}'::jsonb @> '{\"b\":{\"c\":2}}', '{\"a\":1}'::jsonb <@ '{\"a\":1,\"b\":2}', '[1,[2,3]]'::jsonb @> '[[3]]', '[1,2]'::jsonb @> '1', '1'::jsonb @> '[1]', '[1,2,2]'::jsonb @> '[2,1]'",
        "SELECT '{\"a\":1,\"b\":2}'::jsonb ? 'b', '[\"x\",\"y\"]'::jsonb ? 'y', '\"x\"'::jsonb ? 'x', '{\"a\":{\"b\":1}}'::jsonb ? 'b', '[1]'::jsonb ? '1'",
        "SELECT id FROM compat WHERE payload @> '{\"kind\":\"event\"}' ORDER BY id",
        "SELECT id FROM compat WHERE payload ? 'tags' OR payload <@ '[10, 20, {\"x\": null}, 30]' ORDER BY id",
        "SELECT id FROM compat WHERE payload -> 'tags' ? 'b' ORDER BY id")),
    Case("q04_ordering", "q04", 3.2, feature="jsonb equality and btree ORDER BY", setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, payload JSONB)",) + tuple(
        f"INSERT INTO compat VALUES ({index}, '{value}')" for index, value in enumerate(
            ("null", "true", "1", "\"a\"", "[]", "{}", "false", "[1]", "{\"a\":1}", "0.5", "[1,2]", "{\"a\":1,\"b\":2}", "\"\"", "[0,5]", "{\"b\":0}"), 1)), statements=(
        "SELECT id, payload::text FROM compat ORDER BY payload, id",
        "SELECT id FROM compat ORDER BY payload DESC, id",
        "SELECT id FROM compat WHERE payload = '{\"b\": 2, \"a\": 1}'",
        "SELECT id FROM compat WHERE payload > '[1]' ORDER BY id",
        "SELECT '{\"a\":1,\"b\":2}'::jsonb = '{\"b\":2,\"a\":1}'::jsonb, '[1,2]'::jsonb < '[1,3]'::jsonb, '[]'::jsonb < 'null'::jsonb, 'true'::jsonb < '[1]'::jsonb, '{\"a\":1}'::jsonb <> '{\"a\":2}'::jsonb")),
    Case("q04_functions", "q04", 3.2, feature="jsonb_typeof, jsonb_array_length", setup=DOCS, statements=(
        "SELECT id, jsonb_typeof(payload), jsonb_typeof(payload -> 'n'), jsonb_typeof(payload -> 0) FROM compat ORDER BY id",
        "SELECT jsonb_typeof('{}'), jsonb_typeof('[]'), jsonb_typeof('1'), jsonb_typeof('\"s\"'), jsonb_typeof('true'), jsonb_typeof('null'), jsonb_array_length('[1,2,3]'), jsonb_array_length('[]')",
        "SELECT jsonb_array_length(payload) FROM compat WHERE id = 2",
        "SELECT jsonb_array_length('{}')",
        "SELECT jsonb_array_length('5')")),
    Case("q04_type_errors", "q04", 3.2, feature="operator resolution errors", statements=(
        "SELECT '{\"a\":1}' -> 'a'",
        "SELECT '{\"a\":1}'::json = '{\"a\":1}'::json",
        "SELECT '1'::jsonb + 1",
        "SELECT '{\"a\":1}'::jsonb ->> NULL")),
    Case("q04_binary_and_parameters", "q04", 3.2, feature="binary jsonb results and text parameters", setup=DOCS, statements=(
        ("SELECT id, payload, raw FROM compat ORDER BY id", None, {"binary": True}),
        ("INSERT INTO compat VALUES (%s, %s::jsonb, %s::json)", (5, '{"p": [1, "two", null]}', '{"p" : 1}')),
        ("SELECT payload ->> %s FROM compat WHERE id = %s", ("p", 5)),
        ("SELECT payload, raw FROM compat WHERE id = 5", None, {"binary": True}))),
    Case("q04_restart", "q04", 3.2, feature="JSON/JSONB values survive a durable restart", setup=DOCS, statements=(
        "UPDATE compat SET payload = '{\"z\":[1,{\"y\":\"\\u00e9\"}],\"a\":2.50}' WHERE id = 3",), restart=(
        "SELECT id, payload::text, raw::text FROM compat ORDER BY id",
        "SELECT id FROM compat WHERE payload @> '{\"a\":2.5}' ORDER BY payload")),
)
SUITES["selfcheck"] = (
    Case("selfcheck_point_select", "selfcheck", 0.0, statements=("SELECT label FROM compat WHERE id = 3",)),
    Case("selfcheck_future_step", "selfcheck", 99.0, statements=("SELECT label FROM compat WHERE id = 1",)),
    Case("selfcheck_restart", "selfcheck", 0.0,
         statements=("INSERT INTO compat VALUES (4, FALSE, 'durable')",),
         restart=("SELECT label FROM compat WHERE id = 4",)),
)


def split_statement(statement):
    if isinstance(statement, str):
        return statement, None, {}
    if len(statement) == 2:
        return statement[0], statement[1], {}
    return statement


REPORTED = ("TimeZone", "DateStyle", "IntervalStyle")


def run_statements(connection, statements):
    events = []
    for statement in statements:
        sql, parameters, options = split_statement(statement)
        outcome = execute(connection, sql, parameters, binary=bool(options.get("binary")))
        status = {name: connection.info.parameter_status(name) for name in REPORTED}
        events.append({"sql": sql, "parameters": json_value(parameters), "binary": bool(options.get("binary")),
                       "outcome": outcome, "parameter_status": status})
    return events


def drop_tables(psycopg, arguments, tables):
    with psycopg.connect(**arguments) as connection:
        execute(connection, "ROLLBACK")
        for name in tables:
            execute(connection, "DROP TABLE " + name)


def run_semantic_case(psycopg, arguments, case, restart):
    """Run one case on one side; `restart()` crash-restarts that side in place."""
    drop_tables(psycopg, arguments, case.tables)
    try:
        with psycopg.connect(**arguments) as connection:
            for index, statement in enumerate(case.setup):
                sql, parameters, options = split_statement(statement)
                outcome = execute(connection, sql, parameters, binary=bool(options.get("binary")))
                if outcome["sqlstate"] != "00000":
                    return {"stage": "setup", "index": index, "sql": sql, "outcome": outcome}
            result = {"stage": "statements", "events": run_statements(connection, case.statements)}
        if case.restart:
            restart()
            with psycopg.connect(**arguments) as connection:
                result["after_restart"] = run_statements(connection, case.restart)
        return result
    finally:
        drop_tables(psycopg, arguments, case.tables)


class Candidate:
    """A restartable RAMSQL process; restart reuses the same --data WAL."""

    def __init__(self, binary, arguments):
        self.binary, self.arguments, self.manager, self.connection = binary, arguments, None, None

    def start(self):
        self.manager = running_ramsql(self.binary, **self.arguments)
        started = self.manager.__enter__()
        if self.connection is None:
            self.connection = started
        else:
            self.connection.clear()
            self.connection.update(started)

    def stop(self):
        manager, self.manager = self.manager, None
        if manager is not None:
            manager.__exit__(None, None, None)

    def restart(self):
        self.stop()
        self.start()


def select_cases(suites, step, durable):
    executed, skipped = [], []
    for suite in suites:
        for case in SUITES[suite]:
            if case.step > step:
                skipped.append({"name": case.name, "suite": suite, "step": case.step, "reason": f"step {case.step} > {step}"})
            elif case.restart and not durable:
                skipped.append({"name": case.name, "suite": suite, "step": case.step, "reason": "durable-restart case requires --durable"})
            else:
                executed.append(case)
    return executed, skipped


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, help="comma-separated suite names: " + ",".join(SUITES))
    parser.add_argument("--step", type=float, required=True, help="execute cases with case.step <= N")
    parser.add_argument("--binary", type=Path, default=ROOT / "sig-out" / "bin" / ("ramsql.exe" if os.name == "nt" else "ramsql"))
    parser.add_argument("--image", default="postgres:18-alpine", help="locally available immutable reference image (no automatic pull)")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".sig-cache" / "postgres-compat")
    parser.add_argument("--report", type=Path, default=ROOT / ".sig-cache" / "postgres-compat" / "semantics-report.json")
    parser.add_argument("--durable", action="store_true", help="run RAMSQL on a fresh durable WAL instead of --memory")
    parser.add_argument("--scram", action="store_true", help="run RAMSQL with a generated SCRAM-SHA-256 verifier")
    args = parser.parse_args()
    suites = [name.strip() for name in args.suite.split(",") if name.strip()]
    unknown = [name for name in suites if name not in SUITES]
    if not suites or unknown:
        parser.error("unknown suite(s): " + ",".join(unknown or ["<none>"]))
    timezone_dir = reference_timezone_dir(args.image)
    import psycopg

    executed, skipped = select_cases(suites, args.step, args.durable)
    binary = args.binary.resolve()
    report = {"schema": 1, "started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "finite differential semantics suites; matching does not establish full PostgreSQL parity",
              "suites": suites, "step": args.step, "ramsql_binary": str(binary),
              "ramsql_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
              "client": {"name": "psycopg", "version": psycopg.__version__},
              "cases": [], "skipped": skipped}
    for entry in skipped:
        print(f"SKIP {entry['suite']}/{entry['name']}: {entry['reason']}")
    try:
        if not executed:
            raise RuntimeError("no case executed for the selected suites and step")
        with native_postgres(args.image, psycopg, work_directory=args.work_dir) as (native, metadata), \
                candidate_mode(args.work_dir, durable=args.durable, scram=args.scram, timezone_dir=timezone_dir) as (mode_arguments, mode):
            report["reference"] = metadata
            report["mode"] = mode
            report["reference_image"] = {"id": metadata.get("image_id"), "repo_digests": metadata.get("image_digests")}
            candidate = Candidate(binary, mode_arguments)
            candidate.start()
            try:
                for case in executed:
                    reference_result = run_semantic_case(psycopg, native, case,
                                                         lambda: restart_native(native, metadata, psycopg))
                    candidate_result = run_semantic_case(psycopg, candidate.connection, case, candidate.restart)
                    gaps = differences(reference_result, candidate_result)
                    report["cases"].append({"name": case.name, "suite": case.suite, "step": case.step,
                                            "feature": case.feature, "match": not gaps, "differences": gaps,
                                            "postgres": reference_result, "ramsql": candidate_result})
                    print(("PASS" if not gaps else "GAP ") + f" {case.suite}/{case.name}" + ("" if not gaps else ": " + gaps[0]["path"]))
            finally:
                candidate.stop()
        mismatching = sum(not case["match"] for case in report["cases"])
        report["summary"] = {"executed": len(report["cases"]), "matching": len(report["cases"]) - mismatching,
                             "mismatching": mismatching, "skipped": len(skipped), "passed": mismatching == 0}
        result = 1 if mismatching else 0
    except Exception as error:
        report["harness_error"] = {"class": type(error).__name__, "message": str(error)}
        report["summary"] = {"executed": len(report["cases"]), "skipped": len(skipped), "passed": False, "incomplete": True}
        print("HARNESS ERROR: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        result = 2
    report["finished_utc"] = datetime.now(timezone.utc).isoformat()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], sort_keys=True))
    print("Report: " + str(args.report.resolve()))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
