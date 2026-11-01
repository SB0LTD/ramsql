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

from test_postgres_compat import (BASE, ROOT, candidate_mode, differences, error_result, execute, json_value,
                                  native_postgres, reference_timezone_dir, restart_native, running_ramsql)


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
    # Standalone sequences dropped before and after the case.
    sequences: tuple[str, ...] = ()

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
    Case("q03_to_char_templates", "q03", 3.2, feature="temporal to_char calendar/time fields, modifiers and text metadata", statements=(
        "SELECT to_char(DATE '2024-02-29','YYYY-MM-DD'), to_char(TIMESTAMP '1999-12-31 23:59:58.123456','YYYY-MM-DD HH24:MI:SS.MS US')",
        "SELECT to_char(TIMESTAMP '0001-01-01 BC','YYYY BC B.C. CC'), to_char(DATE '2024-02-03','FMMonth FMDDth FMYYYY')",
        "SELECT to_char(DATE '2024-01-01','DDD WW W Q J D'), to_char(TIMESTAMP '2000-02-29 12:34:56.012345','FF1 FF2 FF3 FF4 FF5 FF6')",
        "SELECT to_char(NULL::timestamp,'YYYY'), to_char(DATE '2024-01-01',NULL), to_char(TIMESTAMP 'infinity','YYYY'), to_char(TIMESTAMP '-infinity','YYYY')",
        "SELECT to_char(TIMESTAMP '2000-01-01',''), to_char(TIMESTAMP '2000-01-01','FM'), to_char(TIMESTAMP '2000-01-01','\"literal YYYY\" YYYY')",
        "SELECT to_char('2024-01-01','YYYY')")),
    Case("q03_to_char_zones", "q03", 3.2, feature="timestamptz formatting uses session zone; timestamp does not", statements=(
        "SET TIME ZONE 'UTC'",
        "SELECT to_char(TIMESTAMPTZ '2024-01-01 23:30:00+00','YYYY-MM-DD HH24:MI'), to_char(TIMESTAMP '2024-01-01 23:30','YYYY-MM-DD HH24:MI')",
        "SET TIME ZONE '+02:00'",
        "SELECT to_char(TIMESTAMPTZ '2024-01-01 23:30:00+00','YYYY-MM-DD HH24:MI'), to_char(TIMESTAMP '2024-01-01 23:30','YYYY-MM-DD HH24:MI')")),
    Case("q03_to_char_three_way_group", "q03", 3.2, feature="dbMeter region/month shape over three-way unique joins", tables=("compat", "aux", "report_regions"), setup=(
        "CREATE TABLE report_regions(id INTEGER PRIMARY KEY,label TEXT)",
        "CREATE TABLE aux(id INTEGER PRIMARY KEY,region_id INTEGER)",
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,store_id INTEGER,sale_date DATE,quantity INTEGER,cents BIGINT)",
        "INSERT INTO report_regions VALUES(1,'north'),(2,'south'),(3,NULL)",
        "INSERT INTO aux VALUES(1,1),(2,1),(3,2),(4,3)",
        "INSERT INTO compat VALUES(1,1,'2024-01-01',2,10),(2,2,'2024-01-31',3,20),(3,1,'2024-02-29',4,30),(4,3,NULL,5,40),(5,4,'2024-01-01',6,50),(6,9,'2024-01-01',7,60)"), statements=(
        "SELECT r.label AS region,to_char(s.sale_date,'YYYY-MM') AS sale_month,COUNT(*) AS sales,SUM(s.quantity) AS units,SUM(s.cents) AS revenue_cents FROM compat s JOIN aux st ON st.id=s.store_id JOIN report_regions r ON r.id=st.region_id GROUP BY r.label,to_char(s.sale_date,'YYYY-MM') ORDER BY region,sale_month",
        "SELECT to_char(sale_date,'YYYY-MM') AS month,COUNT(*) FROM compat GROUP BY to_char(sale_date,'YYYY-MM') HAVING COUNT(*)>1 ORDER BY month")),
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
    Case("q03_temporal_boundaries", "q03", 3.2, feature="checked temporal arithmetic, date operator types and infinity errors", statements=(
        "SELECT '294277-06-01'::timestamp",
        "SELECT '300000-01-01'::date::timestamp",
        "SELECT '300000-01-01'::date::timestamptz",
        "SELECT timestamp '294276-12-31' + interval '1 month'",
        "SELECT timestamptz '294276-12-31 00:00:00+00' + interval '1 month'",
        "SELECT date 'infinity' - date 'infinity'",
        "SELECT date '-infinity' - date '2000-01-01'",
        "SELECT date '2000-01-01' - date 'infinity'",
        "SELECT date '2000-01-01' + 1::bigint",
        "SELECT 1::bigint + date '2000-01-01'",
        "SELECT date '2000-01-01' - 1::bigint",
        "SELECT date '2000-01-01' - (-2147483648)::integer",
        "SELECT (date '5874897-12-31' - date '4714-11-24 BC')::text, (date '4714-11-24 BC' - date '5874897-12-31')::text",
        "SELECT ('294276-12-31 23:59:59.999999'::timestamp(0))::text, ('4714-11-24 00:00:00.000001 BC'::timestamp(0))::text",
        "SELECT ('294277-01-01 00:00:00+01'::timestamptz)::text, (date 'infinity' + 1)::text, (date '-infinity' - 1)::text",
        "SELECT 1")),
    Case("q03_interval_boundaries", "q03", 3.2, feature="interval overflow returns 22015 without terminating the session", statements=(
        "SELECT '9223372036854.9999999 seconds'::interval",
        "SELECT '9223372036854775807.9 microseconds'::interval",
        "SELECT '-9223372036854775807.9 microseconds ago'::interval",
        "SELECT '9223372036854776 milliseconds'::interval",
        "SELECT '153722867281 minutes'::interval",
        "SELECT '2562047789 hours'::interval",
        "SELECT '2147483648 days'::interval",
        "SELECT '2147483648 months'::interval",
        "SELECT -interval '-2147483648 months'",
        "SELECT interval '2147483647 months' + interval '1 month'",
        "SELECT '-9223372036854775807.9 microseconds'::interval::text, '9223372036854775807 microseconds'::interval::text",
        "SELECT 1")),
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
# Inventory JSON follow-up: native PG16/PG18 and candidate share each query;
# values, metadata, errors and durable restart results remain exact comparisons.
_JSON_CONCAT_VALUES = ('{}', '[]', 'null', '1', 'true', '"s"',
                       '{"a":1}', '[1,2]', '{"a":{"x":1}}')
SUITES["q04"] += (
    Case("q04_concat_matrix", "q04", 3.2, setup=(), feature="JSONB concat kinds, duplicate keys, unknown overload", statements=tuple(
        "SELECT '%s'::jsonb || '%s'::jsonb" % (left, right)
        for left in _JSON_CONCAT_VALUES for right in _JSON_CONCAT_VALUES) + (
        "SELECT '{\"aa\":1,\"b\":2,\"a\":3}'::jsonb || '{\"a\":4,\"b\":null,\"z\":9}'::jsonb",
        "SELECT '{\"a\":1}'::jsonb || '{\"b\":2}', '{\"a\":1}' || '{\"b\":2}'::jsonb",
        "SELECT '1'::jsonb || 'x'::text, 'x'::text || '1'::jsonb",
        "SELECT '{}'::jsonb || 'not json'",
        "SELECT '{}'::jsonb || 1",
        "SELECT '{}'::jsonb -> 1::bigint",
        "SELECT '1'::jsonb -> 0, '1'::jsonb #> '{0}', '[1]'::jsonb #> '{0,0}'",
        "SELECT '[10,20]'::jsonb #> '{+1}', '[10,20]'::jsonb #> '{ 1}'")),
    Case("q04_plain_json_extract", "q04", 3.2, setup=(), feature="lexical JSON extraction, duplicates, unicode, NULL", statements=(
        "SELECT ' { \"a\" : 1, \"a\" : 2.50 } '::json -> 'a', ' [ 1 , 2.50 ] '::json -> -1",
        "SELECT '{\"a\":{ \"b\" : [1,2.50] }}'::json #> '{a,b}', '{\"a\":null}'::json ->> 'a'",
        "SELECT '1'::json -> 0, '1'::json #> '{}', '[10,20]'::json #> '{+1}'",
        "SELECT '\"\\ud800\"'::json, '\"\\u0000\"'::json",
        "SELECT '{\"bad\":\"\\ud800\",\"a\":1}'::json -> 'a'",
        "SELECT '{\"bad\":\"\\u0000\",\"a\":1}'::json -> 'a'",
        "SELECT '{}'::json -> 1::bigint",
        ("SELECT %s::json -> %s, %s::jsonb || %s::jsonb", ('{"x":2.50}', 'x', '{"a":1}', '{"b":2}')),
        ("SELECT '{\"x\" : [1,2.50]}'::json -> 'x', '{\"a\":1}'::jsonb || '{\"b\":2}'", None, {"binary": True}))),
    Case("q04_concat_restart", "q04", 3.2, feature="new concat values pass WAL replay", setup=DOCS,
         statements=("UPDATE compat SET payload = payload || '{\"new\":[1,null,2.50]}'::jsonb",),
         restart=("SELECT id, payload, raw -> 'a' FROM compat ORDER BY id",)),
)

# Q08 (design §5.7(d), step 3.3): sorting, DISTINCT, DISTINCT ON and
# pagination. Ties are only ever projected through the tie column itself or
# broken by id, because PostgreSQL's sort is not stable.
SORTS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, flag BOOLEAN, label TEXT, n INTEGER)",
         "INSERT INTO compat VALUES (1, TRUE, 'one', 10)", "INSERT INTO compat VALUES (2, FALSE, NULL, 20)",
         "INSERT INTO compat VALUES (3, NULL, 'three', 10)", "INSERT INTO compat VALUES (4, TRUE, 'four', NULL)",
         "INSERT INTO compat VALUES (5, FALSE, 'five', 20)", "INSERT INTO compat VALUES (6, TRUE, 'one', 30)")
SUITES["q08"] = (
    Case("q08_probe", "q08", 3.3, feature="distinct probe", statements=(
        "SELECT DISTINCT flag FROM compat ORDER BY flag",
        "SELECT DISTINCT flag FROM compat ORDER BY flag DESC")),
    Case("q08_multi_column", "q08", 3.3, feature="multi-column DISTINCT", setup=SORTS, statements=(
        "SELECT DISTINCT flag, n FROM compat ORDER BY flag, n",
        "SELECT DISTINCT label FROM compat ORDER BY label NULLS FIRST",
        "SELECT DISTINCT flag, label FROM compat ORDER BY label, flag",
        "SELECT ALL flag FROM compat ORDER BY flag, id")),
    Case("q08_nulls_collapse", "q08", 3.3, feature="NULLs collapse in DISTINCT", setup=SORTS, statements=(
        "SELECT DISTINCT n FROM compat ORDER BY n",
        "SELECT DISTINCT n, flag IS NULL FROM compat ORDER BY 1, 2",
        "SELECT DISTINCT NULL::integer FROM compat",
        "SELECT DISTINCT label IS NULL AS missing FROM compat ORDER BY missing")),
    Case("q08_distinct_on", "q08", 3.3, feature="DISTINCT ON with ORDER BY", setup=SORTS, statements=(
        "SELECT DISTINCT ON (n) n, id FROM compat ORDER BY n, id DESC",
        "SELECT DISTINCT ON (flag) flag, label FROM compat ORDER BY flag, id",
        "SELECT DISTINCT ON (n) id, label FROM compat ORDER BY n DESC NULLS LAST, id",
        "SELECT DISTINCT ON (label) label, id FROM compat ORDER BY label, id DESC",
        "SELECT DISTINCT ON (flag, n) flag, n, id FROM compat ORDER BY flag, n, id")),
    Case("q08_order_keys", "q08", 3.3, feature="multiple keys, mixed directions, NULLS FIRST/LAST", setup=SORTS, statements=(
        "SELECT id FROM compat ORDER BY flag DESC, n ASC NULLS LAST, id",
        "SELECT id FROM compat ORDER BY n NULLS FIRST, id DESC",
        "SELECT id, n FROM compat ORDER BY n DESC NULLS LAST, label ASC NULLS FIRST, id",
        "SELECT label, n FROM compat ORDER BY 2 DESC, 1, id",
        "SELECT id FROM compat ORDER BY label DESC, id")),
    Case("q08_ordinal_expression", "q08", 3.3, feature="ordinal and expression ORDER BY", setup=SORTS, statements=(
        "SELECT id, n FROM compat ORDER BY 2, 1 DESC",
        "SELECT id FROM compat ORDER BY id % 3, id DESC",
        "SELECT id, n * -1 AS m FROM compat ORDER BY m NULLS FIRST, id",
        "SELECT label FROM compat ORDER BY COALESCE(n, 0) DESC, id",
        "SELECT id FROM compat ORDER BY flag IS NULL, n IS NULL DESC, id")),
    Case("q08_pagination", "q08", 3.3, feature="OFFSET, LIMIT ALL, FETCH FIRST, OFFSET past the end", setup=SORTS, statements=(
        "SELECT id FROM compat ORDER BY id OFFSET 2",
        "SELECT id FROM compat ORDER BY id LIMIT 2 OFFSET 3",
        "SELECT id FROM compat ORDER BY id OFFSET 3 LIMIT 2",
        "SELECT id FROM compat ORDER BY id LIMIT ALL",
        "SELECT id FROM compat ORDER BY id OFFSET 1 ROWS FETCH FIRST 2 ROWS ONLY",
        "SELECT id FROM compat ORDER BY id FETCH NEXT ROW ONLY",
        "SELECT id FROM compat ORDER BY id OFFSET 10",
        "SELECT id FROM compat ORDER BY id LIMIT 0",
        "SELECT DISTINCT n FROM compat ORDER BY n LIMIT 2 OFFSET 1")),
    Case("q08_ties", "q08", 3.3, feature="ties", setup=SORTS, statements=(
        "SELECT n FROM compat ORDER BY n",
        "SELECT n FROM compat ORDER BY n DESC LIMIT 3",
        "SELECT flag FROM compat ORDER BY flag DESC OFFSET 1 LIMIT 3",
        "SELECT label FROM compat ORDER BY label LIMIT 2")),
    Case("q08_errors", "q08", 3.3, feature="42P10 and 2201W/2201X", setup=SORTS, statements=(
        "SELECT DISTINCT flag FROM compat ORDER BY id",
        "SELECT DISTINCT ON (n) id FROM compat ORDER BY id, n",
        "SELECT DISTINCT ON (n) n FROM compat ORDER BY flag",
        "SELECT id FROM compat LIMIT -1",
        "SELECT id FROM compat ORDER BY id LIMIT -1",
        "SELECT id FROM compat OFFSET -1",
        "SELECT id FROM compat ORDER BY 7")),
)
# Q06 (design §5.5/§5.6(d), step 3.4): aggregates, GROUP BY and HAVING.
KINDS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, s SMALLINT, i INTEGER, b BIGINT, d NUMERIC(12,3), r REAL, f DOUBLE PRECISION)",
         "INSERT INTO compat VALUES (1, 1, 1, 9223372036854775807, 1.250, 1.5, 0.5)",
         "INSERT INTO compat VALUES (2, 2, 2147483647, 9223372036854775807, 2.5, 2.25, 0.25)",
         "INSERT INTO compat VALUES (3, 2, 2147483647, -1, NULL, NULL, '1e300')",
         "INSERT INTO compat VALUES (4, NULL, NULL, NULL, 0.001, -0.5, NULL)")
GROUPS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, g INTEGER, v INTEGER)",) + tuple(
    f"INSERT INTO compat VALUES ({index}, 7, {index})" for index in range(512))
SUITES["q06"] = (
    Case("q06_window_bounded_rows", "q06", 3.4, feature="moving ROWS, empty frames and exact integer/Boolean transitions", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,k INTEGER,v INTEGER)",
        "INSERT INTO compat VALUES(1,1,10),(2,1,10),(3,1,30),(4,2,5),(5,2,NULL),(6,NULL,7),(7,3,NULL),(8,3,NULL)"), statements=(
        "SELECT id,COUNT(*) OVER (ORDER BY id ROWS BETWEEN 2 PRECEDING AND 1 FOLLOWING),SUM(v) OVER (ORDER BY id ROWS BETWEEN 2 PRECEDING AND 1 FOLLOWING),AVG(v) OVER (ORDER BY id ROWS BETWEEN 2 PRECEDING AND 1 FOLLOWING) FROM compat ORDER BY id",
        "SELECT id,MIN(v) OVER (PARTITION BY k ORDER BY id ROWS 1 PRECEDING),MAX(v) OVER (PARTITION BY k ORDER BY id ROWS 1 PRECEDING) FROM compat ORDER BY id",
        "SELECT id,SUM(v) OVER (ORDER BY id ROWS BETWEEN 3 FOLLOWING AND 1 FOLLOWING),COUNT(*) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND 3 PRECEDING) FROM compat ORDER BY id",
        "SELECT id,BOOL_AND(flag) OVER (ORDER BY id ROWS 2 PRECEDING),BOOL_OR(flag) OVER (ORDER BY id ROWS 2 PRECEDING) FROM (SELECT id,v>7 AS flag FROM compat) s ORDER BY id")),
    Case("q06_window_groups_exclusions", "q06", 3.4, feature="GROUPS and exclusions retain peers and real NULLs", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,k INTEGER,v INTEGER)",
        "INSERT INTO compat VALUES(1,1,10),(2,1,10),(3,1,30),(4,2,5),(5,2,NULL),(6,NULL,7),(7,3,NULL),(8,3,NULL)"), statements=(
        "SELECT id,SUM(v) OVER (ORDER BY v GROUPS BETWEEN 1 PRECEDING AND 1 FOLLOWING EXCLUDE CURRENT ROW),MIN(v) OVER (ORDER BY v GROUPS 1 PRECEDING EXCLUDE GROUP),MAX(v) OVER (ORDER BY v GROUPS 1 PRECEDING EXCLUDE TIES) FROM compat ORDER BY id",
        "SELECT id,COUNT(*) OVER (PARTITION BY k ORDER BY v DESC NULLS FIRST GROUPS BETWEEN CURRENT ROW AND 1 FOLLOWING EXCLUDE TIES),SUM(v) OVER (RANGE CURRENT ROW EXCLUDE GROUP) FROM compat ORDER BY id",
        "SELECT id,SUM(v) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING EXCLUDE CURRENT ROW),MIN(v) OVER (ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING EXCLUDE GROUP) FROM compat ORDER BY id")),
    Case("q06_window_range_offsets", "q06", 3.4, feature="integral RANGE offsets, direction, NULL placement and widened bounds", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,v BIGINT)",
        "INSERT INTO compat VALUES(1,-9223372036854775808),(2,9223372036854775807),(3,10),(4,10),(5,NULL),(6,7)"), statements=(
        "SELECT id,COUNT(*) OVER (ORDER BY v RANGE BETWEEN 5 PRECEDING AND 5 FOLLOWING),MIN(v) OVER (ORDER BY v DESC NULLS FIRST RANGE BETWEEN 5 PRECEDING AND 10 FOLLOWING) FROM compat ORDER BY id",
        "SELECT id,COUNT(*) OVER (ORDER BY v RANGE BETWEEN 9223372036854775807 PRECEDING AND 9223372036854775807 FOLLOWING),SUM(v) OVER (ORDER BY v RANGE BETWEEN CURRENT ROW AND CURRENT ROW EXCLUDE TIES) FROM compat ORDER BY id",
        "SELECT id,COUNT(*) OVER (ORDER BY v RANGE BETWEEN 10 FOLLOWING AND 5 FOLLOWING) FROM compat ORDER BY id")),
    Case("q06_window_filter_and_deferred_inputs", "q06", 3.4, feature="FILTER precedes arguments and unselected frame rows cannot raise errors", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,k INTEGER,v INTEGER)",
        "INSERT INTO compat VALUES(1,1,10),(2,1,10),(3,1,30),(4,2,5),(5,2,NULL),(6,NULL,7)"), statements=(
        "SELECT id,SUM(v) FILTER (WHERE v>7) OVER (ORDER BY id ROWS 1 PRECEDING),MIN(v) FILTER (WHERE id<>3) OVER (ORDER BY id GROUPS 1 PRECEDING),COUNT(*) FILTER (WHERE NULL) OVER () FROM compat ORDER BY id",
        "SELECT id,SUM(10/(id-3)) FILTER (WHERE id<>3) OVER () FROM compat ORDER BY id",
        "SELECT id,SUM(10/(id-1)) OVER (ORDER BY id ROWS BETWEEN 1 FOLLOWING AND 1 FOLLOWING),COUNT(*) FILTER (WHERE 10/(id-id)>0) OVER (ORDER BY id ROWS BETWEEN 3 FOLLOWING AND 1 FOLLOWING) FROM compat ORDER BY id",
        "WITH g AS (SELECT k,SUM(v) AS amount,COUNT(*) AS sales FROM compat GROUP BY k) SELECT k,amount,SUM(amount) FILTER (WHERE sales>1) OVER (ORDER BY k NULLS LAST ROWS 1 PRECEDING) FROM g ORDER BY k NULLS LAST",
        "SELECT SUM(1/0) FILTER (WHERE FALSE) OVER () FROM compat")),
    Case("q06_window_frame_errors", "q06", 3.4, feature="frame syntax, scope and runtime SQLSTATEs including empty input and LIMIT 0", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER)",
        "INSERT INTO compat VALUES(1,10),(2,NULL)"), statements=(
        "SELECT SUM(v) OVER (ROWS -1 PRECEDING) FROM compat",
        "SELECT SUM(v) OVER (ROWS NULL PRECEDING) FROM compat",
        "SELECT SUM(v) OVER (ROWS -1 PRECEDING) FROM compat WHERE FALSE",
        "SELECT SUM(v) OVER (ROWS -1 PRECEDING) FROM compat LIMIT 0",
        "SELECT RANK() OVER (ORDER BY id ROWS -1 PRECEDING) FROM compat ORDER BY id",
        "SELECT SUM(v) OVER (ROWS v PRECEDING) FROM compat",
        "SELECT SUM(v) OVER (ROWS TRUE PRECEDING) FROM compat",
        "SELECT SUM(v) OVER (ROWS COUNT(*) PRECEDING) FROM compat",
        "SELECT SUM(v) OVER (ROWS UNBOUNDED FOLLOWING) FROM compat",
        "SELECT SUM(v) OVER (GROUPS 1 PRECEDING) FROM compat",
        "SELECT SUM(v) OVER (ORDER BY id,v RANGE 1 PRECEDING) FROM compat",
        "SELECT SUM(v) FILTER (WHERE COUNT(*)>1) OVER () FROM compat",
        "SELECT SUM(v) FILTER (WHERE ROW_NUMBER() OVER ()>1) OVER () FROM compat",
        "SELECT COUNT(DISTINCT v) OVER () FROM compat",
        "SELECT 1/0,SUM(v) OVER () FROM compat LIMIT 0")),
    Case("q06_window_parameters_binary", "q06", 3.4, feature="frame offset parameters, binary result values and aggregate metadata", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER)",
        "INSERT INTO compat VALUES(1,10),(2,20),(3,NULL),(4,40)"), statements=(
        ("SELECT id,SUM(v) OVER (ORDER BY id ROWS BETWEEN %s PRECEDING AND %s FOLLOWING),AVG(v) OVER (ORDER BY id ROWS %s PRECEDING) FROM compat ORDER BY id", (1, 2, 1), {"binary": True}),
        ("SELECT id,MIN(v) OVER (ORDER BY id GROUPS %s PRECEDING),COUNT(*) FILTER (WHERE v>%s) OVER (ORDER BY id RANGE %s PRECEDING) FROM compat ORDER BY id", (1, 15, 1), {"binary": True}),
        ("SELECT SUM(v) OVER (ROWS %s PRECEDING) FROM compat", (None,)),
        ("SELECT SUM(v) OVER (ROWS %s PRECEDING) FROM compat", (-1,)))),
    Case("q06_window_final_projection", "q06", 3.4, feature="window results precede LIMIT; projection values and selected-row errors", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER)",
        "INSERT INTO compat VALUES(1,10),(2,20),(3,30)"), statements=(
        # PG16 returns a row for the unfiltered DESC form while PG18 raises
        # 22012. Retain that native-reference disagreement in the progress
        # record; portable assertions must not demand both incompatible plans.
        "SELECT id,10/(id-1),SUM(v) OVER () FROM compat WHERE id>1 ORDER BY id DESC LIMIT 1",
        "SELECT id,10/(id-1),SUM(v) OVER () FROM compat ORDER BY id LIMIT 1",
        "SELECT id,10/(id-1),SUM(v) OVER () FROM compat ORDER BY (id+0) DESC LIMIT 1",
        "SELECT id,SUM(v) OVER ()+id AS marker FROM compat ORDER BY marker DESC LIMIT 2",
        "SELECT id,SUM(v) OVER (ORDER BY id ROWS 1 PRECEDING) FROM compat ORDER BY id DESC LIMIT 1 OFFSET 1")),
    Case("q06_window_sequence_evaluation", "q06", 3.4, feature="offset once, ignored ranking offset and filtered/late-projected volatile inputs", sequences=("frame_seq",), setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER)",
        "INSERT INTO compat VALUES(1,10),(2,20),(3,30)",
        "CREATE SEQUENCE frame_seq START 1"), statements=(
        "SELECT id,SUM(v) OVER (ORDER BY id ROWS nextval('frame_seq') PRECEDING) FROM compat ORDER BY id",
        "SELECT currval('frame_seq')",
        "SELECT id,RANK() OVER (ORDER BY id ROWS nextval('frame_seq') PRECEDING) FROM compat ORDER BY id",
        "SELECT currval('frame_seq')",
        "SELECT id,COUNT(*) FILTER (WHERE nextval('frame_seq')>0) OVER () FROM compat ORDER BY id",
        "SELECT currval('frame_seq')",
        "SELECT id,nextval('frame_seq'),SUM(v) OVER () FROM compat ORDER BY id DESC LIMIT 2",
        "SELECT id,id+SUM(v) OVER () AS amount,nextval('frame_seq') FROM compat ORDER BY id DESC LIMIT 2",
        "SELECT currval('frame_seq')")),
    Case("q06_window_rank_peers", "q06", 3.4, feature="ranking peers, partitions, NULL ordering and window SQLSTATE", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,k INTEGER,v INTEGER)",
        "INSERT INTO compat VALUES(1,1,10),(2,1,10),(3,1,30),(4,2,5),(5,2,NULL),(6,NULL,7)"), statements=(
        "SELECT id,RANK() OVER (ORDER BY v DESC NULLS LAST),DENSE_RANK() OVER (PARTITION BY k ORDER BY v),ROW_NUMBER() OVER (PARTITION BY k ORDER BY v,id) FROM compat ORDER BY id",
        "SELECT id FROM compat ORDER BY RANK() OVER (ORDER BY v DESC NULLS LAST),id LIMIT 4",
        "SELECT id FROM compat WHERE RANK() OVER ()>1",
        "SELECT SUM(RANK() OVER ()) FROM compat")),
    Case("q06_window_frames_and_totals", "q06", 3.4, feature="full/default-peer/prefix-ROWS aggregate windows run before LIMIT", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,k INTEGER,v INTEGER)",
        "INSERT INTO compat VALUES(1,1,10),(2,1,10),(3,1,30),(4,2,5),(5,2,NULL),(6,NULL,7)"), statements=(
        "SELECT id,SUM(v) OVER (PARTITION BY k ORDER BY v),SUM(v) OVER (PARTITION BY k ORDER BY v,id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW),AVG(v) OVER () FROM compat ORDER BY id",
        "SELECT id,SUM(v) OVER (),COUNT(*) OVER (),COUNT(v) OVER () FROM compat WHERE id>2 ORDER BY id LIMIT 2 OFFSET 1",
        "SELECT k,SUM(v),SUM(SUM(v)) OVER (),RANK() OVER (ORDER BY SUM(v) DESC) FROM compat GROUP BY k HAVING SUM(v)>5 ORDER BY k NULLS LAST",
        "WITH product_revenue AS (SELECT k,SUM(v) AS revenue_cents FROM compat GROUP BY k) SELECT k,revenue_cents,ROUND(100.0*revenue_cents/SUM(revenue_cents) OVER (),4) AS pct FROM product_revenue ORDER BY revenue_cents DESC,k NULLS LAST LIMIT 2",
        "SELECT COUNT(*) OVER (),SUM(v) OVER () FROM compat WHERE id<0",
        ("SELECT id,RANK() OVER (ORDER BY v),SUM(v) OVER () FROM compat ORDER BY id", None, {"binary": True}))),
    Case("q06_window_three_way_category", "q06", 3.4, feature="dbMeter category DISTINCT and rank over grouped revenue", tables=("compat", "aux", "report_categories"), setup=(
        "CREATE TABLE report_categories(id INTEGER PRIMARY KEY,label TEXT)",
        "CREATE TABLE aux(id INTEGER PRIMARY KEY,category_id INTEGER)",
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,product_id INTEGER,customer_id INTEGER,cents BIGINT)",
        "INSERT INTO report_categories VALUES(1,'a'),(2,'b'),(3,'a')",
        "INSERT INTO aux VALUES(1,1),(2,3),(3,2)",
        "INSERT INTO compat VALUES(1,1,11,10),(2,2,11,20),(3,3,12,30),(4,1,13,5),(5,3,NULL,5)"), statements=(
        "SELECT c.label AS category,COUNT(*) AS sales,COUNT(DISTINCT s.customer_id) AS customers,SUM(s.cents) AS revenue_cents,RANK() OVER (ORDER BY SUM(s.cents) DESC) AS revenue_rank FROM compat s JOIN aux p ON p.id=s.product_id JOIN report_categories c ON c.id=p.category_id GROUP BY c.label ORDER BY c.label",
        "SELECT c.label,SUM(s.cents),DENSE_RANK() OVER (ORDER BY SUM(s.cents) DESC) FROM compat s JOIN aux p ON p.id=s.product_id JOIN report_categories c ON c.id=p.category_id GROUP BY c.label HAVING COUNT(*)>1 ORDER BY c.label")),
    Case("q06_rollup_levels", "q06", 3.4, feature="ROLLUP separates genuine NULL groups, subtotal levels and exact AVG", setup=(
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,k INTEGER,v INTEGER)",
        "INSERT INTO compat VALUES(1,1,10),(2,1,10),(3,1,30),(4,2,5),(5,2,NULL),(6,NULL,7)"), statements=(
        "SELECT k,v,GROUPING(k,v),COUNT(*),SUM(v),ROUND(AVG(v),4) FROM compat GROUP BY ROLLUP(k,v) ORDER BY k NULLS LAST,v NULLS LAST,GROUPING(k,v)",
        "SELECT k,GROUPING(k),COUNT(DISTINCT v),SUM(v) FROM compat GROUP BY ROLLUP(k) HAVING COUNT(*)>1 ORDER BY GROUPING(k),k NULLS LAST",
        "SELECT k,v,GROUPING(k,v),COUNT(*),SUM(v) FROM compat WHERE id<0 GROUP BY ROLLUP(k,v)",
        "SELECT COUNT(*),SUM(v) FROM compat WHERE id<0 GROUP BY ()",
        "SELECT k,GROUPING(k),SUM(v),RANK() OVER (ORDER BY SUM(v) DESC) FROM compat GROUP BY ROLLUP(k) ORDER BY GROUPING(k),k NULLS LAST",
        "SELECT GROUPING(v),COUNT(*) FROM compat GROUP BY ROLLUP(k)")),
    Case("q06_rollup_segment_discount", "q06", 3.4, feature="dbMeter segment/discount subtotal query with NULLs and exact average", setup=(
        "CREATE TABLE aux(id INTEGER PRIMARY KEY,segment TEXT)",
        "CREATE TABLE compat(id INTEGER PRIMARY KEY,customer_id INTEGER,discount_pct INTEGER,cents BIGINT)",
        "INSERT INTO aux VALUES(1,'a'),(2,'a'),(3,NULL)",
        "INSERT INTO compat VALUES(1,1,0,10),(2,2,0,20),(3,1,5,40),(4,3,NULL,50),(5,2,NULL,NULL)"), statements=(
        "SELECT cu.segment,s.discount_pct,COUNT(*) AS sales,SUM(s.cents) AS revenue_cents,ROUND(AVG(s.cents),2) AS avg_sale_cents,GROUPING(cu.segment,s.discount_pct) AS level FROM compat s JOIN aux cu ON cu.id=s.customer_id GROUP BY ROLLUP(cu.segment,s.discount_pct) ORDER BY cu.segment NULLS LAST,s.discount_pct NULLS LAST,GROUPING(cu.segment,s.discount_pct)",
        "SELECT cu.segment,GROUPING(cu.segment),SUM(s.cents) FILTER (WHERE s.discount_pct=0) FROM compat s JOIN aux cu ON cu.id=s.customer_id GROUP BY ROLLUP(cu.segment) ORDER BY GROUPING(cu.segment),cu.segment NULLS LAST")),
    Case("q06_probes", "q06", 3.4, feature="aggregate and group_by probes", statements=(
        "SELECT COUNT(*), SUM(id) FROM compat",
        "SELECT flag, COUNT(*) FROM compat GROUP BY flag ORDER BY flag")),
    Case("q06_empty", "q06", 3.4, feature="empty input: COUNT 0, others NULL",
         setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, v INTEGER, b BIGINT, d NUMERIC(10,2))",), statements=(
        "SELECT COUNT(*), COUNT(v), SUM(v), AVG(v), MIN(v), MAX(d), SUM(b), BOOL_AND(v > 0) FROM compat",
        "SELECT v, COUNT(*) FROM compat GROUP BY v",
        "SELECT COUNT(*) FROM compat HAVING COUNT(*) = 0",
        "SELECT SUM(d), AVG(d), AVG(b) FROM compat")),
    Case("q06_nulls_ignored", "q06", 3.4, feature="NULLs ignored, COUNT(col)", setup=SORTS, statements=(
        "SELECT COUNT(*), COUNT(n), COUNT(label), SUM(n), MIN(n), MAX(n), AVG(n) FROM compat",
        "SELECT MIN(label), MAX(label), BOOL_AND(flag), BOOL_OR(flag), EVERY(id > 0) FROM compat",
        "SELECT COUNT(flag), COUNT(NULL::integer), MIN(NULL::text) FROM compat")),
    Case("q06_result_types", "q06", 3.4, feature="result OIDs: SUM int8/numeric, AVG numeric/float8, COUNT int8", setup=KINDS, statements=(
        "SELECT SUM(s), SUM(i), SUM(b), SUM(d), SUM(r), SUM(f), COUNT(*) FROM compat",
        "SELECT AVG(s), AVG(i), AVG(b), AVG(d), AVG(r) FROM compat",
        # float8_accum overflows on Sxx (1e300 squared) -> 22003.
        "SELECT AVG(f) FROM compat",
        "SELECT AVG(f) FROM compat WHERE id < 3",
        "SELECT MIN(s), MAX(i), MIN(b), MAX(d), MIN(r), MAX(f), COUNT(d) FROM compat")),
    Case("q06_avg_scale", "q06", 3.4, feature="AVG scale (numeric division)", setup=SORTS, statements=(
        "SELECT AVG(id), AVG(n), AVG(id) FILTER (WHERE id < 4) FROM compat",
        "SELECT flag, AVG(id) FROM compat GROUP BY flag ORDER BY flag",
        "SELECT AVG(n::numeric(10,3)), SUM(n::numeric(10,3)) FROM compat")),
    Case("q06_sum_bigint_limit", "q06", 3.4, feature="SUM(bigint) near the limit is numeric", setup=KINDS, statements=(
        "SELECT SUM(b), AVG(b), MAX(b), MIN(b) FROM compat",
        "SELECT SUM(i), AVG(i) FROM compat",
        "SELECT SUM(b) FILTER (WHERE id < 3) FROM compat",
        "SELECT SUM(i::bigint * 4294967296) FROM compat")),
    Case("q06_count_distinct", "q06", 3.4, feature="aggregate DISTINCT", setup=SORTS, statements=(
        "SELECT COUNT(DISTINCT n), COUNT(DISTINCT label), COUNT(DISTINCT flag), SUM(DISTINCT n), AVG(DISTINCT n) FROM compat",
        "SELECT flag, COUNT(DISTINCT n), COUNT(n) FROM compat GROUP BY flag ORDER BY flag",
        "SELECT COUNT(DISTINCT label) FILTER (WHERE id > 1) FROM compat")),
    Case("q06_filter", "q06", 3.4, feature="FILTER (WHERE)", setup=SORTS, statements=(
        "SELECT COUNT(*) FILTER (WHERE flag), SUM(n) FILTER (WHERE id > 2), COUNT(label) FILTER (WHERE n = 10) FROM compat",
        "SELECT flag, COUNT(*) FILTER (WHERE n IS NULL), MAX(id) FILTER (WHERE n > 10) FROM compat GROUP BY flag ORDER BY flag")),
    Case("q06_group_forms", "q06", 3.4, feature="multi-column, expression, ordinal and alias GROUP BY", setup=SORTS, statements=(
        "SELECT flag, n, COUNT(*) FROM compat GROUP BY flag, n ORDER BY flag, n",
        "SELECT n % 20, COUNT(*) FROM compat GROUP BY n % 20 ORDER BY 1",
        "SELECT n, COUNT(*) FROM compat GROUP BY 1 ORDER BY 1 DESC",
        "SELECT n + 1 AS k, SUM(id) FROM compat GROUP BY k ORDER BY k",
        "SELECT label IS NULL, COUNT(*) FROM compat GROUP BY label IS NULL ORDER BY 1",
        "SELECT n FROM compat GROUP BY n, flag ORDER BY n, flag")),
    Case("q06_having", "q06", 3.4, feature="HAVING", setup=SORTS, statements=(
        "SELECT n, COUNT(*) FROM compat GROUP BY n HAVING COUNT(*) > 1 ORDER BY n",
        "SELECT flag FROM compat GROUP BY flag HAVING SUM(n) >= 30 ORDER BY flag",
        "SELECT COUNT(*) FROM compat HAVING COUNT(*) > 100",
        "SELECT n FROM compat GROUP BY n HAVING n > 10 ORDER BY n",
        "SELECT MAX(id) FROM compat HAVING MIN(id) = 1")),
    Case("q06_order_by_aggregate", "q06", 3.4, feature="ORDER BY aggregates", setup=SORTS, statements=(
        "SELECT flag FROM compat GROUP BY flag ORDER BY COUNT(*) DESC, flag",
        "SELECT n, MAX(id) FROM compat GROUP BY n ORDER BY MAX(id)",
        "SELECT DISTINCT COUNT(*) FROM compat GROUP BY n ORDER BY 1")),
    Case("q06_grouping_errors", "q06", 3.4, feature="ungrouped column / misplaced aggregate -> 42803", setup=SORTS, statements=(
        "SELECT id, COUNT(*) FROM compat",
        "SELECT label FROM compat GROUP BY flag",
        "SELECT id FROM compat WHERE COUNT(*) > 1",
        "SELECT SUM(COUNT(*)) FROM compat",
        "SELECT n FROM compat GROUP BY n ORDER BY id",
        "SELECT COUNT(*) FROM compat GROUP BY COUNT(*)",
        "SELECT SUM(label) FROM compat",
        "SELECT SUM('1') FROM compat")),
    Case("q06_large", "q06", 3.4, feature="one group of 512 rows and 512 groups of one (default profile)", setup=GROUPS, statements=(
        "SELECT g, COUNT(*), SUM(v), MIN(v), MAX(v), AVG(v) FROM compat GROUP BY g",
        "SELECT v, COUNT(*) FROM compat GROUP BY v ORDER BY v DESC",
        "SELECT COUNT(DISTINCT v), COUNT(DISTINCT g) FROM compat",
        "SELECT v % 7, COUNT(*), SUM(v) FROM compat GROUP BY 1 ORDER BY 1")),
    Case("q06_restart", "q06", 3.4, feature="aggregates over replayed rows", setup=SORTS, statements=(
        "INSERT INTO compat VALUES (7, NULL, 'seven', 40)",), restart=(
        "SELECT flag, COUNT(*), SUM(n), MIN(label) FROM compat GROUP BY flag ORDER BY flag",)),
)
# Offset-window inventory slice; each query is compared live with both native versions.
_OFFSET_ROWS=("CREATE TABLE compat(id INTEGER PRIMARY KEY,g INTEGER,v INTEGER,o INTEGER)",
              "INSERT INTO compat VALUES(1,1,10,1),(2,1,NULL,0),(3,1,30,-1),(4,2,40,NULL),(5,2,50,99),(6,2,60,-2147483648)")
SUITES["q06"] += (
    Case("q06_window_offsets_matrix", "q06", 3.4, setup=_OFFSET_ROWS,
         feature="LAG/LEAD partition, offset, default, NULL, type and error matrix",
         statements=(
             'SELECT id,lag(v) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,0) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,0) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,1) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,1) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-1) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-1) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,2,-99) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,2,-99) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-2,-99) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-2,-99) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,99,0) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,99,0) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-99,0) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-99,0) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,NULL,9) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,NULL,9) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,o,id*100) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,o,id*100) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,2147483647,7) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,2147483647,7) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-2147483648,7) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,-2147483648,7) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             "SELECT id,lag(id::text,1,'outside') OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id",
             "SELECT id,lag(id::text,1,'outside') OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id",
             'SELECT id,lag(v::numeric,1,2.50) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v::numeric,1,2.50) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(NULL,1,NULL) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(NULL,1,NULL) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             "SELECT id,lag(NULL,1,'outside') OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id",
             "SELECT id,lag(NULL,1,'outside') OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id",
             'SELECT id,lag(\'{"a":1}\'::jsonb,1,\'{}\') OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(\'{"a":1}\'::jsonb,1,\'{}\') OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,1::smallint,99) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,1::smallint,99) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag() OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,1,2,3) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,1::bigint) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,1.5) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,true) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lag(v,0,1/0) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lag(v) OVER (ORDER BY id ROWS BETWEEN -1 PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v) OVER (ORDER BY id ROWS BETWEEN NULL PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lag(v) FILTER (WHERE true) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lead(v) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,0) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,0) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,1) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,1) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-1) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-1) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,2,-99) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,2,-99) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-2,-99) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-2,-99) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,99,0) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,99,0) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-99,0) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-99,0) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,NULL,9) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,NULL,9) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,o,id*100) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,o,id*100) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,2147483647,7) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,2147483647,7) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-2147483648,7) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,-2147483648,7) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             "SELECT id,lead(id::text,1,'outside') OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id",
             "SELECT id,lead(id::text,1,'outside') OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id",
             'SELECT id,lead(v::numeric,1,2.50) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v::numeric,1,2.50) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(NULL,1,NULL) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(NULL,1,NULL) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             "SELECT id,lead(NULL,1,'outside') OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id",
             "SELECT id,lead(NULL,1,'outside') OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id",
             'SELECT id,lead(\'{"a":1}\'::jsonb,1,\'{}\') OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(\'{"a":1}\'::jsonb,1,\'{}\') OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,1::smallint,99) OVER (PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,1::smallint,99) OVER (ORDER BY id DESC ROWS BETWEEN CURRENT ROW AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead() OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,1,2,3) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,1::bigint) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,1.5) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,true) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lead(v,0,1/0) OVER () AS value FROM compat ORDER BY id',
             'SELECT id,lead(v) OVER (ORDER BY id ROWS BETWEEN -1 PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v) OVER (ORDER BY id ROWS BETWEEN NULL PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
             'SELECT id,lead(v) FILTER (WHERE true) OVER () AS value FROM compat ORDER BY id',
         )),

    Case("q06_window_offsets_parameters", "q06", 3.4, setup=_OFFSET_ROWS,
         feature="offset/default parameters, binary output and bounded demand", statements=(
        ("SELECT id,LAG(v,%s,%s) OVER(PARTITION BY g ORDER BY id),LEAD(v,%s,%s) OVER(ORDER BY id) FROM compat ORDER BY id",(1,-9,-1,99),{"binary":True}),
        ("SELECT id,LAG(v,%s::int4,%s::int4) OVER(ORDER BY id) FROM compat ORDER BY id",(None,9)),
        "SELECT id,LAG(10/(id-6),0) OVER(ORDER BY id) FROM compat ORDER BY id LIMIT 1",
        "SELECT id,LEAD(10/(id-1)) OVER(ORDER BY id DESC) FROM compat ORDER BY id DESC LIMIT 2 OFFSET 1",
        "SELECT g,LAG(SUM(v)) OVER(ORDER BY g) FROM compat GROUP BY g ORDER BY g",
        "SELECT lag(1)",
        "SELECT lead(1,1,false) OVER()")),
    Case("q06_window_offsets_sequences", "q06", 3.4,
         feature="lazy/current-target evaluation, nullable partition order and prefix demand",
         sequences=("offset_value_seq","offset_default_seq","offset_null_seq"), setup=_OFFSET_ROWS+(
        "CREATE TABLE aux(id INTEGER PRIMARY KEY,g INTEGER)",
        "INSERT INTO aux VALUES(1,NULL),(2,1),(3,NULL),(4,1)",
        "CREATE SEQUENCE offset_value_seq", "CREATE SEQUENCE offset_default_seq", "CREATE SEQUENCE offset_null_seq"), statements=(
        "SELECT id,LAG(nextval('offset_value_seq')*100+id,o,nextval('offset_default_seq')*1000+id) OVER(ORDER BY id) FROM compat ORDER BY id",
        "SELECT currval('offset_value_seq'),currval('offset_default_seq')",
        "SELECT id,LAG(nextval('offset_null_seq'),0) OVER(PARTITION BY g ORDER BY id) FROM aux ORDER BY id",
        "SELECT id,LAG(nextval('offset_null_seq'),0) OVER(PARTITION BY g ORDER BY id) FROM aux ORDER BY g NULLS LAST,id LIMIT 1",
        "SELECT LAG(nextval('offset_value_seq'),0) OVER(ORDER BY id) FROM compat LIMIT 1",
        "SELECT currval('offset_value_seq'),currval('offset_null_seq')")),
    Case("q06_window_offsets_restart", "q06", 3.4,
         feature="INSERT SELECT offset outputs are replayable", setup=_OFFSET_ROWS+(
        "CREATE TABLE aux(id INTEGER PRIMARY KEY,v INTEGER)",), statements=(
        "INSERT INTO aux SELECT id,LAG(v,1,-9) OVER(PARTITION BY g ORDER BY id) FROM compat",), restart=(
        "SELECT id,v FROM aux ORDER BY id",
        "SELECT id,LEAD(v) OVER(PARTITION BY g ORDER BY id) FROM compat ORDER BY id")),
)

# Complete ranking/distribution signatures over the supported window pipeline.
_DISTRIBUTION_ROWS=("CREATE TABLE compat(id INTEGER PRIMARY KEY,g INTEGER,v INTEGER,b INTEGER)",
 "INSERT INTO compat VALUES(1,1,10,NULL),(2,1,10,2),(3,1,30,3),(4,2,40,2),(5,2,40,0),(6,2,NULL,-1),(7,NULL,60,3),(8,NULL,60,1),(9,3,70,NULL)")
SUITES["q06"] += (
 Case("q06_window_distribution_matrix","q06",3.4,setup=_DISTRIBUTION_ROWS,
      feature="percent_rank, cume_dist and ntile exact types, peers, NULLs, buckets and errors",statements=(
  'SELECT id,percent_rank() OVER () AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank() OVER (ORDER BY v) AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank() OVER (ORDER BY v DESC NULLS LAST) AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank() OVER (PARTITION BY g ORDER BY v) AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank() OVER (PARTITION BY g) AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank() OVER (ORDER BY v,id ROWS BETWEEN -1 PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank() OVER (ORDER BY v,id ROWS BETWEEN NULL PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank(1) OVER () AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank(NULL) OVER () AS value FROM compat ORDER BY id',
  'SELECT id,percent_rank(*) OVER () AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist() OVER () AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist() OVER (ORDER BY v) AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist() OVER (ORDER BY v DESC NULLS LAST) AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist() OVER (PARTITION BY g ORDER BY v) AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist() OVER (PARTITION BY g) AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist() OVER (ORDER BY v,id ROWS BETWEEN -1 PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist() OVER (ORDER BY v,id ROWS BETWEEN NULL PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist(1) OVER () AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist(NULL) OVER () AS value FROM compat ORDER BY id',
  'SELECT id,cume_dist(*) OVER () AS value FROM compat ORDER BY id',
  'SELECT id,ntile(1) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(1) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(3) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(3) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(4) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(4) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(8) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(8) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(9) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(9) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(10) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(10) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2147483647) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2147483647) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(NULL) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(NULL) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(b) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(b) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  "SELECT id,ntile('2') OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id",
  "SELECT id,ntile('2') OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id",
  'SELECT id,ntile(2::smallint) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2::smallint) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(0) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(0) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(-1) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(-1) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(-2147483648) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(-2147483648) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2::bigint) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2::bigint) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(1.5) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(1.5) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(true) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(true) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(1/0) OVER (PARTITION BY g ORDER BY v,id) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(1/0) OVER (ORDER BY id DESC) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(3) OVER (ORDER BY id ROWS BETWEEN -1 PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
  'SELECT id,ntile(3) OVER (ORDER BY id ROWS BETWEEN NULL PRECEDING AND CURRENT ROW) AS value FROM compat ORDER BY id',
  'SELECT id,ntile() OVER () AS value FROM compat ORDER BY id',
  'SELECT id,ntile(2,3) OVER () AS value FROM compat ORDER BY id',
 )),
 Case("q06_window_distribution_binary_parameters","q06",3.4,setup=_DISTRIBUTION_ROWS,
      feature="float8/int4 binary results, bucket parameters, argument signatures and folding",statements=(
  ("SELECT id,percent_rank() OVER(PARTITION BY g ORDER BY v),cume_dist() OVER(PARTITION BY g ORDER BY v),ntile(%s) OVER(PARTITION BY g ORDER BY id) FROM compat ORDER BY id",(3,),{"binary":True}),
  ("SELECT id,ntile(%s::int4) OVER(ORDER BY id) FROM compat ORDER BY id",(None,)),
  "SELECT ntile(0) OVER() FROM compat WHERE FALSE",
  "SELECT ntile(-1) OVER() FROM compat LIMIT 0",
  "SELECT ntile(2) OVER(ROWS (1/0) PRECEDING)",
  "SELECT percent_rank() OVER(ROWS (1/0) PRECEDING) LIMIT 0",
  "SELECT rank(*) OVER(),dense_rank(*) OVER(),row_number(*) OVER(),percent_rank(*) OVER(),cume_dist(*) OVER()",
  "SELECT ntile(2)","SELECT rank()","SELECT cume_dist()","SELECT percent_rank()")),
 Case("q06_window_ntile_lazy_sequence","q06",3.4,setup=_DISTRIBUTION_ROWS+(
  "CREATE SEQUENCE distribution_seq",),sequences=("distribution_seq",),
      feature="leading NULL bucket retries, cached initial count and prefix demand",statements=(
  "SELECT id,ntile(NULLIF((nextval('distribution_seq')/3)::integer,0)) OVER(ORDER BY id) FROM compat ORDER BY id",
  "SELECT currval('distribution_seq')",
  "SELECT setval('distribution_seq',1,false)",
  "SELECT id,ntile(NULLIF((nextval('distribution_seq')/3)::integer,0)) OVER(ORDER BY id) FROM compat LIMIT 1",
  "SELECT currval('distribution_seq')")),
 Case("q06_window_distribution_restart","q06",3.4,setup=_DISTRIBUTION_ROWS+(
  "CREATE TABLE aux(id INTEGER PRIMARY KEY,p DOUBLE PRECISION,c DOUBLE PRECISION,b INTEGER)",),
      feature="distribution INSERT SELECT results survive durable replay",statements=(
  "INSERT INTO aux SELECT id,percent_rank() OVER(ORDER BY v),cume_dist() OVER(ORDER BY v),ntile(3) OVER(ORDER BY id) FROM compat",),restart=(
  "SELECT id,p,c,b FROM aux ORDER BY id",)),
)


# Scoped named definitions, inheritance and shared sort-key evaluation.
_NAMED_ROWS = ('CREATE TABLE compat(id INTEGER PRIMARY KEY,g INTEGER,v INTEGER)', 'INSERT INTO compat VALUES(1,1,10),(2,1,NULL),(3,1,30),(4,2,40),(5,2,50),(6,NULL,60)')
SUITES["q06"] += (
 Case("q06_named_window_matrix","q06",3.4,setup=_NAMED_ROWS,
      feature="27 native-reference named window values/types/errors",statements=('SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(ORDER BY id) ORDER BY id', 'SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(PARTITION BY g ORDER BY id ROWS 1 PRECEDING) ORDER BY id', 'SELECT id,lag(v) OVER x AS value FROM compat WINDOW x AS(PARTITION BY g ORDER BY id ROWS CURRENT ROW) ORDER BY id', 'SELECT id,cume_dist() OVER x AS value FROM compat WINDOW x AS(PARTITION BY g ORDER BY v) ORDER BY id', 'SELECT id,ntile(2) OVER x AS value FROM compat WINDOW x AS(PARTITION BY g ORDER BY id) ORDER BY id', 'SELECT id,sum(v) OVER (x ORDER BY id) AS value FROM compat WINDOW x AS(PARTITION BY g) ORDER BY id', 'SELECT id,sum(v) OVER (x ROWS 1 PRECEDING) AS value FROM compat WINDOW x AS(ORDER BY id) ORDER BY id', 'SELECT id,sum(v) OVER z AS value FROM compat WINDOW x AS(PARTITION BY g),y AS(x ORDER BY id),z AS(y ROWS 1 PRECEDING) ORDER BY id', 'SELECT id,sum(v) OVER (x) AS value FROM compat WINDOW x AS(ORDER BY id) ORDER BY id', 'SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(ROWS UNBOUNDED PRECEDING) ORDER BY id', 'SELECT id,sum(v) OVER (x) AS value FROM compat WINDOW x AS(ROWS UNBOUNDED PRECEDING) ORDER BY id', 'SELECT id,sum(v) OVER (x PARTITION BY g) AS value FROM compat WINDOW x AS() ORDER BY id', 'SELECT id,sum(v) OVER (x ORDER BY id) AS value FROM compat WINDOW x AS(ORDER BY v) ORDER BY id', 'SELECT id,sum(v) OVER missing AS value FROM compat WINDOW x AS() ORDER BY id', 'SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(y),y AS() ORDER BY id', 'SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(x) ORDER BY id', 'SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(),x AS() ORDER BY id', 'SELECT id,sum(v) OVER z AS value FROM compat WINDOW x AS(ORDER BY id RANGE UNBOUNDED PRECEDING),z AS(x) ORDER BY id', 'SELECT id,sum(v) OVER "X" AS value FROM compat WINDOW "X" AS(ORDER BY id) ORDER BY id', 'SELECT id,sum(v) OVER "X" AS value FROM compat WINDOW x AS(ORDER BY id) ORDER BY id', 'SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(ORDER BY id GROUPS BETWEEN 1 PRECEDING AND 1 FOLLOWING EXCLUDE CURRENT ROW) ORDER BY id', 'SELECT 1 AS id,1 AS value FROM compat WINDOW x AS(ORDER BY nonexistent)', 'SELECT 1 AS id,1 AS value FROM compat WINDOW x AS(ORDER BY sum(v))', 'SELECT 1 AS id,count(*) AS value FROM compat WINDOW x AS(ORDER BY v)', 'SELECT id,(SELECT sum(v) OVER x FROM compat) AS value FROM compat WINDOW x AS() ORDER BY id', 'SELECT id,sum(v) OVER x AS value FROM compat WHERE FALSE WINDOW x AS(ROWS -1 PRECEDING)', 'SELECT id,sum(v) OVER x AS value FROM compat WINDOW x AS(ROWS (1/0) PRECEDING) LIMIT 0')),
 Case("q06_named_window_binary_parameters","q06",3.4,setup=_NAMED_ROWS,feature="named frame/offset parameters, binary metadata and independent CTE scopes",statements=(('SELECT id,sum(v) OVER x,lag(v,%s,%s) OVER x FROM compat WINDOW x AS(PARTITION BY g ORDER BY id ROWS %s PRECEDING) ORDER BY id', (1, 99, 1), {'binary': True}), 'WITH q AS(SELECT id,sum(v) OVER x AS s FROM compat WINDOW x AS(ORDER BY id)) SELECT id,sum(s) OVER x FROM q WINDOW x AS(ORDER BY id DESC) ORDER BY id', 'SELECT id,sum(v) OVER z FROM compat WINDOW x AS(PARTITION BY g),y AS(x ORDER BY id),z AS(y ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING EXCLUDE CURRENT ROW) ORDER BY id')),
 Case("q06_named_window_shared_sequence","q06",3.4,setup=_NAMED_ROWS+("CREATE SEQUENCE named_seq",),sequences=("named_seq",),feature="sorted volatile arguments, scalar-key sharing and per-row aggregate interleaving",statements=("SELECT id,sum(nextval('named_seq')) OVER x FROM compat WINDOW x AS(ORDER BY id DESC) ORDER BY id", "SELECT currval('named_seq')", "SELECT setval('named_seq',1,false)", "SELECT rank() OVER x,row_number() OVER x FROM compat WINDOW x AS(ORDER BY (SELECT nextval('named_seq')))", "SELECT currval('named_seq')", "SELECT setval('named_seq',1,false)", "SELECT rank() OVER x,row_number() OVER x FROM compat c WINDOW x AS(ORDER BY (SELECT nextval('named_seq')+c.id))", "SELECT currval('named_seq')", "SELECT setval('named_seq',1,false)", "SELECT min(nextval('named_seq')) OVER x,min(nextval('named_seq')) OVER x,max(nextval('named_seq')) OVER x FROM compat WINDOW x AS() LIMIT 1", "SELECT currval('named_seq')")),
 Case("q06_named_window_restart","q06",3.4,setup=_NAMED_ROWS+("CREATE TABLE aux(id INTEGER PRIMARY KEY,s BIGINT)",),feature="named-window materialization survives durable replay",statements=("INSERT INTO aux SELECT id,sum(v) OVER x FROM compat WINDOW x AS(PARTITION BY g ORDER BY id)",),restart=("SELECT id,s FROM aux ORDER BY id",)),
)


# Frame value windows: finite native parity, explicit boundary deviations documented separately.
_VALUE_ROWS = ("CREATE TABLE compat(id INTEGER PRIMARY KEY,g INTEGER,v INTEGER)","INSERT INTO compat VALUES(1,1,10),(2,1,NULL),(3,1,10),(4,2,40),(5,2,50),(6,NULL,60)")
SUITES["q06"] += (
 Case("q06_window_values_matrix","q06",3.4,setup=_VALUE_ROWS,feature="78 scoped native value-window programs; no boundary-deviation parity claim",statements=('SELECT id,first_value(v) OVER() AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY v NULLS LAST) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY id ROWS BETWEEN 2 FOLLOWING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY v GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW EXCLUDE TIES) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY v RANGE BETWEEN 10 PRECEDING AND 10 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER(ORDER BY id ROWS CURRENT ROW EXCLUDE CURRENT ROW) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER() AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY v NULLS LAST) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY id ROWS BETWEEN 2 FOLLOWING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY v GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW EXCLUDE TIES) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY v RANGE BETWEEN 10 PRECEDING AND 10 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS value FROM compat ORDER BY id', 'SELECT id,last_value(v) OVER(ORDER BY id ROWS CURRENT ROW EXCLUDE CURRENT ROW) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER() AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY v NULLS LAST) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY id ROWS BETWEEN 2 FOLLOWING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY v GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW EXCLUDE TIES) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY v RANGE BETWEEN 10 PRECEDING AND 10 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1) OVER(ORDER BY id ROWS CURRENT ROW EXCLUDE CURRENT ROW) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER() AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY v NULLS LAST) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY id ROWS BETWEEN 2 FOLLOWING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY v GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW EXCLUDE TIES) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY v RANGE BETWEEN 10 PRECEDING AND 10 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2) OVER(ORDER BY id ROWS CURRENT ROW EXCLUDE CURRENT ROW) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER() AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY v NULLS LAST) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY id ROWS BETWEEN 2 FOLLOWING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY v GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW EXCLUDE TIES) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY v RANGE BETWEEN 10 PRECEDING AND 10 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,7) OVER(ORDER BY id ROWS CURRENT ROW EXCLUDE CURRENT ROW) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER() AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(PARTITION BY g ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY v NULLS LAST) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY id ROWS BETWEEN 2 FOLLOWING AND 1 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY v GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW EXCLUDE TIES) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY v RANGE BETWEEN 10 PRECEDING AND 10 FOLLOWING) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY v ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING EXCLUDE GROUP) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,NULL) OVER(ORDER BY id ROWS CURRENT ROW EXCLUDE CURRENT ROW) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,0) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,-1) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,g) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,2147483647) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1::smallint) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1::bigint) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1.2) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,first_value(NULL) OVER(ORDER BY id) AS value FROM compat ORDER BY id', "SELECT id,last_value('text') OVER(ORDER BY id) AS value FROM compat ORDER BY id", 'SELECT id,first_value(v,1) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,last_value() OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,first_value(*) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,nth_value(v,1/0) OVER(ORDER BY id) AS value FROM compat ORDER BY id', 'SELECT id,first_value(v) OVER x AS value FROM compat WINDOW x AS(PARTITION BY g ORDER BY id ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) ORDER BY id', 'SELECT id,nth_value(v,0) OVER() AS value FROM compat LIMIT 0', 'SELECT id,nth_value(v,0) OVER() AS value FROM compat WHERE FALSE', 'SELECT id,first_value(v::text) OVER(ORDER BY id DESC) AS value FROM compat ORDER BY id')),
 Case("q06_window_values_binary_parameters","q06",3.4,setup=_VALUE_ROWS,feature="polymorphic binary values, parameters, signatures and immutable folding",statements=(('SELECT id,first_value(v) OVER x,last_value(v) OVER x,nth_value(v,%s) OVER x FROM compat WINDOW x AS(PARTITION BY g ORDER BY id ROWS BETWEEN %s PRECEDING AND 1 FOLLOWING) ORDER BY id', (2, 1), {'binary': True}), ('SELECT id,nth_value(v,%s::int4) OVER(ORDER BY id) FROM compat ORDER BY id', (None,)), 'SELECT first_value(1)', 'SELECT nth_value(1,2)', 'SELECT nth_value(1,0) OVER() LIMIT 0', 'SELECT nth_value(1/0,NULL::int4) OVER()')),
 Case("q06_window_values_lazy_sequence","q06",3.4,setup=_VALUE_ROWS+("CREATE SEQUENCE value_seq",),sequences=("value_seq",),feature="NULL position and empty frame avoid value evaluation; volatile prefix demand",statements=("SELECT id,nth_value(nextval('value_seq'),NULL::int4) OVER(ORDER BY id) FROM compat ORDER BY id", "SELECT currval('value_seq')", "SELECT id,first_value(nextval('value_seq')) OVER(ORDER BY id) FROM compat ORDER BY id LIMIT 2", "SELECT currval('value_seq')", 'SELECT id,last_value(100/(id-1)) OVER(ORDER BY id ROWS CURRENT ROW EXCLUDE CURRENT ROW) FROM compat ORDER BY id')),
 Case("q06_window_values_restart","q06",3.4,setup=_VALUE_ROWS+("CREATE TABLE aux(id INTEGER PRIMARY KEY,f INTEGER,l INTEGER,n INTEGER)",),feature="typed value-window materialization and durable replay",statements=("INSERT INTO aux SELECT id,first_value(v) OVER x,last_value(v) OVER x,nth_value(v,2) OVER x FROM compat WINDOW x AS(PARTITION BY g ORDER BY id ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING)",),restart=("SELECT id,f,l,n FROM aux ORDER BY id",)),
)

# Q07 (design §5.8/§5.9(d), step 3.5): joins, subqueries, CTEs and set operations.
JOINS = SORTS + ("CREATE TABLE aux (id INTEGER PRIMARY KEY, tag TEXT)", "INSERT INTO aux VALUES (1, 'joined')",
                 "INSERT INTO aux VALUES (3, 'three')", "INSERT INTO aux VALUES (9, 'nine')",
                 "CREATE TABLE extra (k INTEGER, id INTEGER)", "INSERT INTO extra VALUES (1, 1)",
                 "INSERT INTO extra VALUES (2, 1)", "INSERT INTO extra VALUES (3, NULL)")
JOIN_TABLES = ("extra", "aux", "compat")
PAIRS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, v INTEGER)", "CREATE TABLE aux (id INTEGER PRIMARY KEY, w INTEGER)") + tuple(
    f"INSERT INTO compat VALUES ({index}, {index})" for index in range(512)) + tuple(
    f"INSERT INTO aux VALUES ({511 - index}, {index})" for index in range(512))
SUITES["q07"] = (
    Case("q07_probes", "q07", 3.5, feature="join and cte probes",
         setup=BASE + ("CREATE TABLE aux (id INTEGER PRIMARY KEY, label TEXT)", "INSERT INTO aux VALUES (1, 'joined')"), statements=(
        "SELECT compat.label, aux.label FROM compat JOIN aux ON compat.id = aux.id",
        "WITH selected AS (SELECT label FROM compat WHERE id = 1) SELECT label FROM selected")),
    Case("q07_inner_outer", "q07", 3.5, feature="INNER/LEFT/RIGHT/FULL/CROSS, null-extended rows", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT compat.id, aux.tag FROM compat JOIN aux ON compat.id = aux.id ORDER BY 1",
        "SELECT compat.id, aux.tag FROM compat LEFT JOIN aux ON aux.id = compat.id ORDER BY 1",
        "SELECT compat.id, aux.id FROM compat RIGHT OUTER JOIN aux ON aux.id = compat.id ORDER BY aux.id",
        "SELECT compat.id, aux.id FROM compat FULL JOIN aux ON aux.id = compat.id AND compat.id > 1 ORDER BY compat.id, aux.id",
        "SELECT COUNT(*) FROM compat CROSS JOIN aux",
        "SELECT COUNT(*) FROM compat, aux WHERE compat.id < aux.id",
        "SELECT compat.id, aux.tag FROM compat LEFT JOIN aux ON aux.id = compat.id WHERE aux.id IS NULL ORDER BY 1")),
    Case("q07_using", "q07", 3.5, feature="USING and merged columns", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT id, tag FROM compat JOIN aux USING (id) ORDER BY id",
        "SELECT * FROM aux FULL JOIN extra USING (id) ORDER BY id, k",
        "SELECT * FROM aux LEFT JOIN extra USING (id) ORDER BY id, k",
        "SELECT aux.id, extra.id FROM aux RIGHT JOIN extra USING (id) ORDER BY extra.k")),
    Case("q07_duplicates_self", "q07", 3.5, feature="duplicate matches and self-joins with aliases", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT aux.tag, extra.k FROM aux JOIN extra ON extra.id = aux.id ORDER BY extra.k",
        "SELECT a.id, b.id FROM compat a JOIN compat b ON a.n = b.n AND a.id < b.id ORDER BY a.id, b.id",
        "SELECT a.label, b.label FROM compat AS a JOIN compat AS b ON b.id = a.id + 1 ORDER BY a.id")),
    Case("q07_three_table_nonequi", "q07", 3.5, feature="three-table and non-equi joins", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT compat.id, aux.tag, extra.k FROM compat JOIN aux ON aux.id = compat.id LEFT JOIN extra ON extra.id = aux.id ORDER BY compat.id, extra.k",
        "SELECT compat.id, aux.id FROM compat JOIN aux ON compat.id < aux.id AND aux.id < 4 ORDER BY 1, 2",
        "SELECT c.id, a.id FROM compat c LEFT JOIN aux a ON a.id BETWEEN c.id AND c.id + 1 ORDER BY 1, 2")),
    Case("q07_name_errors", "q07", 3.5, feature="42702 ambiguous, 42P01 unknown qualifier", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT id FROM compat JOIN aux ON compat.id = aux.id",
        "SELECT x.id FROM compat JOIN aux ON compat.id = aux.id",
        "SELECT compat.id FROM compat c",
        "SELECT * FROM compat JOIN aux ON compat.id = aux.nope",
        "SELECT * FROM compat JOIN aux USING (nope)")),
    Case("q07_scalar_subquery", "q07", 3.5, feature="scalar subqueries (more than one row -> 21000)", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT id, (SELECT tag FROM aux WHERE aux.id = compat.id) FROM compat ORDER BY id",
        "SELECT (SELECT MAX(id) FROM aux), (SELECT COUNT(*) FROM compat WHERE n = 10)",
        "SELECT id FROM compat WHERE n = (SELECT MAX(n) FROM compat) ORDER BY id",
        "SELECT (SELECT id FROM aux)",
        "SELECT id, (SELECT k FROM extra WHERE extra.id = compat.id) FROM compat ORDER BY id")),
    Case("q07_in_subquery", "q07", 3.5, feature="IN / NOT IN with NULLs", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT id FROM compat WHERE id IN (SELECT id FROM aux) ORDER BY id",
        "SELECT id FROM compat WHERE id NOT IN (SELECT id FROM extra) ORDER BY id",
        "SELECT id FROM compat WHERE id NOT IN (SELECT id FROM extra WHERE id IS NOT NULL) ORDER BY id",
        "SELECT id, id IN (SELECT id FROM extra), NULL IN (SELECT id FROM aux WHERE id > 100), n NOT IN (SELECT n FROM compat WHERE id > 4) FROM compat WHERE id < 4 ORDER BY id",
        "SELECT id FROM compat WHERE id IN (SELECT id, tag FROM aux)")),
    Case("q07_exists", "q07", 3.5, feature="correlated and uncorrelated EXISTS / NOT EXISTS", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT id FROM compat WHERE EXISTS (SELECT 1 FROM extra WHERE extra.id = compat.id) ORDER BY id",
        "SELECT id FROM compat WHERE NOT EXISTS (SELECT 1 FROM aux WHERE aux.id = compat.id) ORDER BY id",
        "SELECT id, EXISTS (SELECT 1 FROM aux WHERE aux.id > compat.n) FROM compat ORDER BY id",
        "SELECT COUNT(*) FROM compat WHERE EXISTS (SELECT 1 FROM aux WHERE id = 9)")),
    Case("q07_derived", "q07", 3.5, feature="derived tables", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT s.x FROM (SELECT id * 2 AS x FROM compat WHERE id > 3) s ORDER BY x",
        "SELECT s.n, s.c FROM (SELECT n, COUNT(*) AS c FROM compat GROUP BY n) s WHERE s.c > 1 ORDER BY s.n",
        "SELECT t.id, a.tag FROM (SELECT id FROM compat WHERE flag) t LEFT JOIN aux a ON a.id = t.id ORDER BY t.id",
        "SELECT * FROM (SELECT id, label FROM compat ORDER BY id DESC LIMIT 2) s ORDER BY id")),
    Case("q07_ctes", "q07", 3.5, feature="multiple and chained CTEs", setup=JOINS, tables=JOIN_TABLES, statements=(
        "WITH a AS (SELECT id, n FROM compat WHERE id > 2), b AS (SELECT id FROM a WHERE n IS NOT NULL) SELECT b.id FROM b ORDER BY 1",
        "WITH x AS (SELECT n FROM compat) SELECT COUNT(*) FROM x p JOIN x q ON p.n = q.n",
        "WITH counts(n, c) AS (SELECT n, COUNT(*) FROM compat GROUP BY n) SELECT n, c FROM counts ORDER BY c DESC, n",
        "WITH t AS (SELECT tag FROM aux WHERE id = 9) SELECT compat.id, (SELECT tag FROM t) FROM compat WHERE id = 1")),
    Case("q07_recursive", "q07", 3.5, feature="WITH RECURSIVE: UNION ALL to 10, UNION cycle termination", setup=JOINS, tables=JOIN_TABLES, statements=(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 10) SELECT x FROM c ORDER BY x",
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 10) SELECT SUM(x), COUNT(*) FROM c",
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION SELECT (x % 3) + 1 FROM c) SELECT x FROM c ORDER BY x",
        "WITH RECURSIVE walk(id, depth) AS (SELECT 1, 0 UNION SELECT compat.id, walk.depth + 1 FROM walk JOIN compat ON compat.id = walk.id + 2 WHERE walk.depth < 5) SELECT id, depth FROM walk ORDER BY id")),
    Case("q07_set_operations", "q07", 3.5, feature="UNION/INTERSECT/EXCEPT [ALL]", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT n FROM compat UNION SELECT k FROM extra ORDER BY 1",
        "SELECT n FROM compat UNION ALL SELECT n FROM compat ORDER BY n LIMIT 5",
        "SELECT n FROM compat INTERSECT SELECT 10 ORDER BY 1",
        "SELECT n FROM compat INTERSECT ALL SELECT n FROM compat WHERE id > 2 ORDER BY 1",
        "SELECT n FROM compat EXCEPT SELECT 20 ORDER BY 1",
        "SELECT n FROM compat EXCEPT ALL SELECT n FROM compat WHERE id < 3 ORDER BY 1",
        "SELECT 'a' UNION SELECT 'b' ORDER BY 1",
        "SELECT 1 UNION SELECT 2::bigint ORDER BY 1 DESC")),
    Case("q07_set_errors", "q07", 3.5, feature="set-operation arity and type errors", setup=JOINS, tables=JOIN_TABLES, statements=(
        "SELECT 1 UNION SELECT 1, 2",
        "SELECT 1 UNION SELECT TRUE",
        "SELECT id, label FROM compat UNION SELECT id, tag FROM aux ORDER BY 1, 2")),
    Case("q07_transaction_join", "q07", 3.5, feature="a join in a transaction sees its own uncommitted rows", setup=JOINS, tables=JOIN_TABLES, statements=(
        "BEGIN",
        "INSERT INTO aux VALUES (2, 'mine')",
        "SELECT compat.id, aux.tag FROM compat JOIN aux ON aux.id = compat.id ORDER BY 1",
        "SELECT COUNT(*) FROM compat WHERE id IN (SELECT id FROM aux)",
        "ROLLBACK",
        "SELECT compat.id, aux.tag FROM compat JOIN aux ON aux.id = compat.id ORDER BY 1")),
    Case("q07_large_join", "q07", 3.5, feature="512 x 512 primary-key equi-join (default profile)", setup=PAIRS, statements=(
        "SELECT COUNT(*), SUM(compat.v + aux.w) FROM compat JOIN aux ON aux.id = compat.id",
        "SELECT compat.id, aux.w FROM compat JOIN aux ON aux.id = compat.id ORDER BY compat.id DESC LIMIT 5",
        "SELECT COUNT(aux.id), COUNT(*) FROM compat LEFT JOIN aux ON aux.id = compat.v + 256",
        "SELECT COUNT(*) FROM compat a JOIN aux b ON b.id = 511 - a.id WHERE b.w = a.v")),
    Case("q07_restart", "q07", 3.5, feature="joins and CTEs over replayed rows", setup=JOINS, tables=JOIN_TABLES, statements=(
        "INSERT INTO aux VALUES (5, 'five')",), restart=(
        "SELECT compat.id, aux.tag FROM compat JOIN aux USING (id) ORDER BY 1",
        "WITH j AS (SELECT id FROM aux) SELECT COUNT(*) FROM j")),
)
# Q09 (design §5.10(d), step 3.6): general DML and RETURNING. BASE rows:
# (1, TRUE, 'one'), (2, FALSE, NULL), (3, NULL, 'three').
ALL_ROWS = "SELECT id, flag, label FROM compat ORDER BY id"
SUITES["q09"] = (
    Case("q09_probe", "q09", 3.6, feature="returning probe", statements=(
        "INSERT INTO compat VALUES (4, FALSE, 'four') RETURNING label",)),
    Case("q09_returning_forms", "q09", 3.6, feature="RETURNING *, t.*, expressions and aliases", statements=(
        "INSERT INTO compat VALUES (4, FALSE, 'four') RETURNING *",
        "INSERT INTO compat VALUES (5, TRUE, 'five') RETURNING id * 2 AS twice, label || '!', NOT flag, compat.id",
        "INSERT INTO compat (label, id) VALUES ('six', 6) RETURNING compat.*, id + 0.5",
        "INSERT INTO compat VALUES (7, NULL, NULL) RETURNING id, label IS NULL AS missing, COALESCE(label, 'none') label",
        ALL_ROWS)),
    Case("q09_multirow", "q09", 3.6, feature="multi-row VALUES with RETURNING, tags and rowcount", statements=(
        "INSERT INTO compat VALUES (4, TRUE, 'a'), (5, FALSE, 'b'), (6, NULL, NULL) RETURNING id, label",
        "INSERT INTO compat (id, label) VALUES (7, 'c'), (8, 'd')",
        "INSERT INTO compat (id) VALUES (9), (10), (11) RETURNING id",
        ALL_ROWS)),
    Case("q09_insert_select", "q09", 3.6, feature="INSERT ... SELECT with RETURNING", statements=(
        "INSERT INTO compat SELECT id + 10, flag, label FROM compat WHERE id < 3 ORDER BY id DESC RETURNING id, label",
        "INSERT INTO compat (id, label) SELECT 30, 'literal' RETURNING *",
        "INSERT INTO compat (id) SELECT '40' RETURNING id",
        "INSERT INTO compat (id, label) SELECT id + 100, UPPER_NOT_A_FUNCTION FROM compat",
        "INSERT INTO compat (id, label) SELECT MAX(id) + 1, 'max' FROM compat RETURNING id, label",
        "INSERT INTO compat SELECT * FROM compat WHERE id = 1",
        ALL_ROWS)),
    Case("q09_update_delete", "q09", 3.6, feature="UPDATE / DELETE RETURNING (new and old rows)", statements=(
        "UPDATE compat SET label = label || '+', flag = NOT flag WHERE id >= 2 RETURNING id, label, flag",
        "UPDATE compat SET label = 'none' WHERE id = 99 RETURNING id",
        "UPDATE compat SET id = id + 10 WHERE id = 1 RETURNING id, label",
        "DELETE FROM compat WHERE id = 3 RETURNING *",
        "DELETE FROM compat WHERE id = 99 RETURNING id",
        # Unordered scans after an UPDATE follow rowid order in RAMSQL and
        # heap order in PostgreSQL; each remaining DELETE touches one row.
        "DELETE FROM compat WHERE id > 5 RETURNING id, label IS NULL",
        "DELETE FROM compat RETURNING id, label IS NULL",
        ALL_ROWS)),
    Case("q09_atomic_failures", "q09", 3.6, feature="a failing multi-row INSERT / RETURNING returns nothing and changes nothing", statements=(
        "INSERT INTO compat VALUES (7, TRUE, 'a'), (8, TRUE, 'b'), (1, TRUE, 'c') RETURNING id",
        ALL_ROWS,
        "INSERT INTO compat (id) VALUES (9), (9) RETURNING id",
        "UPDATE compat SET flag = TRUE RETURNING 10 / (id - 3)",
        "DELETE FROM compat RETURNING 1 / (id - 2)",
        "INSERT INTO compat VALUES (7, TRUE, 'a'), (8, 'x', 'b')",
        ALL_ROWS)),
    Case("q09_errors", "q09", 3.6, feature="RETURNING and VALUES error classes", statements=(
        "INSERT INTO compat VALUES (7, TRUE, 'a') RETURNING nope",
        "INSERT INTO compat VALUES (7, TRUE, 'a') RETURNING other.*",
        "INSERT INTO compat VALUES (7, TRUE, 'a'), (8, TRUE)",
        "INSERT INTO compat (id, label) VALUES (7, 'a', 'b')",
        "INSERT INTO compat (id, label) SELECT id FROM compat",
        "SELECT DEFAULT",
        "UPDATE compat SET nope = 1 RETURNING id",
        ALL_ROWS)),
    Case("q09_default_keyword", "q09", 3.6, feature="DEFAULT items, DEFAULT VALUES and SET col = DEFAULT without declared defaults", statements=(
        "INSERT INTO compat VALUES (7, DEFAULT, DEFAULT) RETURNING *",
        "INSERT INTO compat (id) VALUES (8) RETURNING *",
        "UPDATE compat SET label = DEFAULT WHERE id = 1 RETURNING *",
        "INSERT INTO compat DEFAULT VALUES",
        ALL_ROWS)),
    Case("q09_prepared", "q09", 3.6, feature="extended-protocol RETURNING executes exactly once", statements=(
        ("INSERT INTO compat VALUES (%s, %s, %s) RETURNING id, label", (5, True, "five")),
        ("UPDATE compat SET label = %s WHERE id = %s RETURNING id, label", ("prepared", 1)),
        ("DELETE FROM compat WHERE id = %s RETURNING id", (2,)),
        ("INSERT INTO compat VALUES (%s, %s, %s) RETURNING id, flag", (6, False, "six"), {"binary": True}),
        "SELECT COUNT(*), SUM(id) FROM compat",
        ALL_ROWS)),
    Case("q09_transaction", "q09", 3.6, feature="RETURNING inside a block, rollback discards", statements=(
        "BEGIN",
        "INSERT INTO compat VALUES (4, TRUE, 'tx') RETURNING id",
        "UPDATE compat SET label = 'changed' WHERE id IN (1, 4) RETURNING id, label",
        "SELECT id, label FROM compat ORDER BY id",
        "ROLLBACK",
        ALL_ROWS)),
    # Upsert (design §5.11(d), step 3.8).
    Case("q09_upsert_probe", "q09", 3.8, feature="upsert probe", statements=(
        "INSERT INTO compat VALUES (1, FALSE, 'upsert') ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label RETURNING label",
        ALL_ROWS)),
    Case("q09_upsert_nothing", "q09", 3.8, feature="DO NOTHING with and without a target, mixed rows, tags", statements=(
        "INSERT INTO compat VALUES (1, TRUE, 'x') ON CONFLICT DO NOTHING",
        "INSERT INTO compat VALUES (2, TRUE, 'x'), (4, TRUE, 'four') ON CONFLICT (id) DO NOTHING RETURNING id, label",
        "INSERT INTO compat VALUES (5, NULL, 'a'), (5, NULL, 'b') ON CONFLICT DO NOTHING RETURNING label",
        "INSERT INTO compat SELECT id, flag, 'copy' FROM compat ON CONFLICT DO NOTHING",
        ALL_ROWS)),
    Case("q09_upsert_update", "q09", 3.8, feature="DO UPDATE with EXCLUDED, alias, ON CONSTRAINT, WHERE false", statements=(
        "INSERT INTO compat AS c VALUES (2, TRUE, 'b') ON CONFLICT ON CONSTRAINT compat_pkey DO UPDATE SET label = COALESCE(c.label, 'none') || EXCLUDED.label, flag = EXCLUDED.flag RETURNING *",
        "INSERT INTO compat VALUES (3, TRUE, 'z') ON CONFLICT (id) DO UPDATE SET label = 'never' WHERE compat.flag RETURNING id",
        "INSERT INTO compat VALUES (1, FALSE, 'u'), (6, FALSE, 'new') ON CONFLICT (id) DO UPDATE SET label = compat.label || '/' || EXCLUDED.label, id = EXCLUDED.id + 10 RETURNING id, label",
        "INSERT INTO compat SELECT id, flag, 'again' FROM compat WHERE id < 4 ORDER BY id ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label RETURNING id, label",
        "INSERT INTO compat VALUES (3, NULL, 'd') ON CONFLICT (id) DO UPDATE SET label = DEFAULT RETURNING label",
        ALL_ROWS)),
    Case("q09_upsert_errors", "q09", 3.8, feature="same row twice (21000), no matching unique index (42P10), 42704, 42601", statements=(
        "INSERT INTO compat VALUES (5, TRUE, 'a'), (5, TRUE, 'b') ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label",
        "INSERT INTO compat VALUES (1, TRUE, 'a'), (1, TRUE, 'b') ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label",
        "INSERT INTO compat VALUES (1, TRUE, 'a') ON CONFLICT (label) DO NOTHING",
        "INSERT INTO compat VALUES (1, TRUE, 'a') ON CONFLICT (id, label) DO NOTHING",
        "INSERT INTO compat VALUES (1, TRUE, 'a') ON CONFLICT ON CONSTRAINT nope DO NOTHING",
        "INSERT INTO compat VALUES (1, TRUE, 'a') ON CONFLICT DO UPDATE SET label = 'x'",
        "INSERT INTO compat VALUES (1, TRUE, 'a') ON CONFLICT (id) DO UPDATE SET label = excluded.nope",
        "INSERT INTO compat VALUES (1, TRUE, 'a') ON CONFLICT (id) DO UPDATE SET label = label",
        "INSERT INTO compat VALUES (1, TRUE, 'a') ON CONFLICT (nope) DO NOTHING",
        ALL_ROWS)),
    Case("q09_upsert_transaction", "q09", 3.8, feature="own uncommitted rows conflict; rollback discards", statements=(
        "BEGIN",
        "INSERT INTO compat VALUES (4, TRUE, 'mine')",
        "INSERT INTO compat VALUES (4, FALSE, 'again') ON CONFLICT (id) DO UPDATE SET label = compat.label || '+' RETURNING *",
        "INSERT INTO compat VALUES (4, FALSE, 'x') ON CONFLICT DO NOTHING RETURNING id",
        "ROLLBACK",
        "INSERT INTO compat VALUES (4, FALSE, 'x') ON CONFLICT DO NOTHING RETURNING id",
        ALL_ROWS)),
    Case("q09_upsert_prepared", "q09", 3.8, feature="extended-protocol upsert executes once", statements=(
        ("INSERT INTO compat VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label RETURNING id, label", (1, True, "p")),
        ("INSERT INTO compat VALUES (%s, %s, %s) ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label RETURNING id, label", (8, True, "q")),
        ALL_ROWS)),
    Case("q09_upsert_restart", "q09", 3.8, feature="upserted rows survive a durable restart", statements=(
        "INSERT INTO compat VALUES (1, FALSE, 'u'), (4, TRUE, 'n') ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label",
        "INSERT INTO compat VALUES (2, TRUE, 'x') ON CONFLICT DO NOTHING"), restart=(
        ALL_ROWS,)),
    Case("q09_restart", "q09", 3.6, feature="RETURNING mutations survive a durable restart", statements=(
        "INSERT INTO compat VALUES (4, TRUE, 'a'), (5, NULL, 'b') RETURNING id",
        "UPDATE compat SET label = 'u' WHERE id = 5 RETURNING label",
        "DELETE FROM compat WHERE id = 2 RETURNING id",
        "INSERT INTO compat SELECT id + 10, flag, label FROM compat WHERE id < 4 RETURNING id"), restart=(
        ALL_ROWS,)),
)
# Q10 (design §5.12/§5.13(d), step 3.7): defaults, identity, generated
# columns and sequences. Sequence values are non-transactional; CACHE > 1
# is outside RAMSQL's subset (0A000) and not compared.
IDENT = ("CREATE TABLE compat (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label TEXT)",)
DEFAULTS = ("CREATE TABLE compat (id INTEGER PRIMARY KEY, label TEXT DEFAULT 'default', n INTEGER DEFAULT 2 + 3, "
            "flag BOOLEAN DEFAULT TRUE, created TIMESTAMPTZ DEFAULT now())",)
SUITES["q10"] = (
    Case("q10_probes", "q10", 3.7, feature="defaults and identity probes",
         setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, label TEXT DEFAULT 'default')",), statements=(
             "INSERT INTO compat (id) VALUES (1)", "SELECT label FROM compat",
             "CREATE TABLE aux (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label TEXT)",
             "INSERT INTO aux (label) VALUES ('generated') RETURNING id")),
    Case("q10_returning_generated", "q10", 3.7, feature="RETURNING identity, default and generated values",
         setup=("CREATE TABLE compat (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label TEXT DEFAULT 'd', "
                "n INTEGER, twice INTEGER GENERATED ALWAYS AS (n * 2) STORED)",), statements=(
             "INSERT INTO compat (n) VALUES (5) RETURNING *",
             "INSERT INTO compat (label, n) VALUES ('a', 1), ('b', NULL), (DEFAULT, 3) RETURNING id, label, twice",
             "INSERT INTO compat DEFAULT VALUES RETURNING id, label, n, twice",
             "UPDATE compat SET n = n + 10 WHERE id <= 2 RETURNING id, twice",
             "SELECT * FROM compat ORDER BY id")),
    Case("q10_defaults", "q10", 3.7, feature="constant and now() defaults, DEFAULT keyword, DEFAULT VALUES, SET col = DEFAULT",
         setup=DEFAULTS, statements=(
             "INSERT INTO compat (id) VALUES (1)",
             "INSERT INTO compat VALUES (2, DEFAULT, 7, DEFAULT, DEFAULT), (3, 'x', DEFAULT, NULL, NULL)",
             "SELECT id, label, n, flag, created IS NOT NULL FROM compat ORDER BY id",
             "UPDATE compat SET label = 'changed', n = 1 WHERE id = 1",
             "UPDATE compat SET label = DEFAULT, n = DEFAULT WHERE id = 1 RETURNING label, n",
             "BEGIN",
             "INSERT INTO compat (id) VALUES (4) RETURNING created = now(), created = transaction_timestamp()",
             "COMMIT",
             "SELECT id, label, n, flag FROM compat ORDER BY id")),
    Case("q10_not_null_default", "q10", 3.7, feature="a default violating NOT NULL -> 23502",
         setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, label TEXT NOT NULL DEFAULT NULL, n INTEGER NOT NULL DEFAULT 0)",), statements=(
             "INSERT INTO compat (id) VALUES (1)",
             "INSERT INTO compat (id, label) VALUES (1, 'ok') RETURNING *",
             "UPDATE compat SET label = DEFAULT WHERE id = 1",
             "INSERT INTO compat VALUES (2, 'x', DEFAULT) RETURNING n",
             "SELECT * FROM compat ORDER BY id")),
    Case("q10_identity_always", "q10", 3.7, feature="ALWAYS rejects explicit values (428C9) unless OVERRIDING SYSTEM VALUE",
         setup=IDENT, statements=(
             "INSERT INTO compat (label) VALUES ('a') RETURNING id",
             "INSERT INTO compat VALUES (10, 'x')",
             "INSERT INTO compat (id, label) SELECT 11, 'y'",
             "INSERT INTO compat VALUES (DEFAULT, 'b') RETURNING id",
             "INSERT INTO compat OVERRIDING SYSTEM VALUE VALUES (10, 'x') RETURNING id",
             "INSERT INTO compat OVERRIDING USER VALUE VALUES (99, 'y') RETURNING id",
             "UPDATE compat SET id = 50 WHERE id = 1",
             "UPDATE compat SET id = DEFAULT WHERE id = 10 RETURNING id",
             "SELECT id, label FROM compat ORDER BY id")),
    Case("q10_identity_by_default", "q10", 3.7, feature="BY DEFAULT accepts explicit values; identity options",
         setup=("CREATE TABLE compat (id BIGINT GENERATED BY DEFAULT AS IDENTITY (START WITH 100 INCREMENT BY 10) PRIMARY KEY, label TEXT)",), statements=(
             "INSERT INTO compat (label) VALUES ('a') RETURNING id",
             "INSERT INTO compat VALUES (5, 'explicit') RETURNING id",
             "INSERT INTO compat OVERRIDING USER VALUE VALUES (5, 'ignored') RETURNING id",
             "INSERT INTO compat (label) VALUES ('b'), ('c') RETURNING id",
             "SELECT currval('compat_id_seq'), lastval()",
             "SELECT id, label FROM compat ORDER BY id")),
    Case("q10_rollback_gap", "q10", 3.7, feature="a rolled-back insert leaves a gap (insert, roll back, insert -> 2)",
         setup=IDENT, statements=(
             "BEGIN", "INSERT INTO compat (label) VALUES ('gone') RETURNING id", "ROLLBACK",
             "INSERT INTO compat (label) VALUES ('kept') RETURNING id",
             "BEGIN", "SAVEPOINT s", "INSERT INTO compat (label) VALUES ('inner') RETURNING id", "ROLLBACK TO s",
             "INSERT INTO compat (label) VALUES ('after') RETURNING id", "COMMIT",
             "SELECT id, label FROM compat ORDER BY id")),
    Case("q10_sequence_functions", "q10", 3.7, feature="CREATE SEQUENCE, currval before nextval (55000), setval, lastval",
         sequences=("s",), statements=(
             "SELECT lastval()",
             "CREATE SEQUENCE s",
             "SELECT currval('s')",
             "SELECT nextval('s')",
             "SELECT nextval('s'), nextval('s')",
             "SELECT currval('s'), lastval()",
             "SELECT setval('s', 10)",
             "SELECT nextval('s'), currval('S')",
             "SELECT setval('s', 20, false), nextval('s')",
             "SELECT setval('s', 0)",
             "SELECT nextval('missing')",
             "SELECT nextval('compat')",
             "SELECT nextval(NULL)")),
    Case("q10_sequence_bounds", "q10", 3.7, feature="MAXVALUE exhaustion (2200H), CYCLE, descending sequences, option errors",
         sequences=("m", "c", "d", "z"), statements=(
             "CREATE SEQUENCE m MAXVALUE 2",
             "SELECT nextval('m'), nextval('m')",
             "SELECT nextval('m')",
             "CREATE SEQUENCE c MINVALUE 1 MAXVALUE 2 CYCLE",
             "SELECT nextval('c'), nextval('c'), nextval('c')",
             "CREATE SEQUENCE d INCREMENT BY -2 START WITH -1",
             "SELECT nextval('d'), nextval('d')",
             "CREATE SEQUENCE z INCREMENT 0",
             "CREATE SEQUENCE z MINVALUE 5 MAXVALUE 5",
             "CREATE SEQUENCE z START 100 MAXVALUE 10",
             "CREATE SEQUENCE z AS smallint START 40000",
             "CREATE SEQUENCE m",
             "CREATE SEQUENCE z AS smallint INCREMENT BY 30000 START 32000",
             "SELECT nextval('z')",
             "SELECT nextval('z')")),
    Case("q10_sequence_ddl", "q10", 3.7, feature="DROP SEQUENCE, owned sequences, object-kind errors, transactional CREATE",
         setup=BASE + ("CREATE TABLE aux (id INTEGER GENERATED BY DEFAULT AS IDENTITY, v INTEGER)",), sequences=("s", "t"), statements=(
             "CREATE SEQUENCE s",
             "DROP TABLE s",
             "DROP SEQUENCE compat",
             "DROP SEQUENCE aux_id_seq",
             "CREATE TABLE s (id INTEGER)",
             "SELECT nextval('aux_id_seq')",
             "BEGIN", "CREATE SEQUENCE t", "SELECT nextval('t')", "ROLLBACK",
             "SELECT nextval('t')",
             "DROP SEQUENCE s",
             "SELECT nextval('s')",
             "DROP TABLE aux",
             "SELECT nextval('aux_id_seq')")),
    Case("q10_nontransactional", "q10", 3.7, feature="nextval is never rolled back (block, savepoint, failed statement)",
         sequences=("s",), statements=(
             "CREATE SEQUENCE s",
             "BEGIN", "SELECT nextval('s')", "ROLLBACK",
             "SELECT nextval('s')",
             "BEGIN", "SAVEPOINT p", "SELECT nextval('s')", "ROLLBACK TO p", "SELECT nextval('s')", "COMMIT",
             "SELECT nextval('s') / 0",
             "SELECT nextval('s')")),
    Case("q10_defaults_with_sequences", "q10", 3.7, feature="DEFAULT nextval('s') and expression defaults",
         setup=("CREATE SEQUENCE s START 7",
                "CREATE TABLE compat (id INTEGER PRIMARY KEY DEFAULT nextval('s'), label TEXT DEFAULT 'n' || 'x')",),
         sequences=("s",), statements=(
             "INSERT INTO compat DEFAULT VALUES RETURNING *",
             "INSERT INTO compat (label) VALUES ('a'), ('b') RETURNING id, label",
             "INSERT INTO compat VALUES (100, DEFAULT) RETURNING *",
             "SELECT currval('s')",
             "SELECT * FROM compat ORDER BY id")),
    Case("q10_generated", "q10", 3.7, feature="stored generated columns (recomputed on insert/update; 428C9 on assignment)",
         setup=("CREATE TABLE compat (id INTEGER PRIMARY KEY, a INTEGER, label TEXT, b INTEGER GENERATED ALWAYS AS (a * 2 + 1) STORED, "
                "tag TEXT GENERATED ALWAYS AS (label || '!') STORED)",), statements=(
             "INSERT INTO compat (id, a, label) VALUES (1, 5, 'x') RETURNING *",
             "INSERT INTO compat VALUES (2, 1, 'y', 3, DEFAULT)",
             "INSERT INTO compat VALUES (2, 1, 'y', DEFAULT, DEFAULT) RETURNING b, tag",
             "INSERT INTO compat (id) VALUES (3) RETURNING b, tag",
             "UPDATE compat SET a = 7 WHERE id = 1 RETURNING b",
             "UPDATE compat SET b = 1",
             "UPDATE compat SET tag = DEFAULT, label = 'z' WHERE id = 2 RETURNING tag",
             "SELECT * FROM compat ORDER BY id")),
    Case("q10_restart", "q10", 3.7, feature="identity, sequences and defaults after a durable crash restart (SEQ_LOG_VALS)",
         setup=("CREATE TABLE compat (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, label TEXT DEFAULT 'd')",),
         sequences=("s",), statements=(
             "INSERT INTO compat (label) VALUES ('a')", "INSERT INTO compat (label) VALUES ('b')",
             "INSERT INTO compat DEFAULT VALUES",
             "CREATE SEQUENCE s", "SELECT nextval('s')", "SELECT setval('s', 100)"), restart=(
             "INSERT INTO compat DEFAULT VALUES RETURNING id, label",
             "SELECT nextval('s')",
             "SELECT id, label FROM compat ORDER BY id")),
)
# P01 (design §5.14/§5.15(d), step 3.9): pg_catalog and information_schema.
# User OIDs are compared relationally (joins, regclass), built-in OIDs by
# value. Columns PostgreSQL declares as "char", regproc, int2vector,
# character_data or yes_or_no are cast to text (RAMSQL reports them as text).
BUILTIN_TYPES = ("bool", "name", "int8", "int2", "int4", "text", "oid", "json", "float4", "float8", "date",
                 "timestamp", "timestamptz", "interval", "numeric", "jsonb")
CATALOG_TABLE = ("CREATE TABLE compat (id INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, amount NUMERIC(10,2), "
                 "happened TIMESTAMPTZ DEFAULT now(), doc JSONB DEFAULT '{}', label TEXT DEFAULT 'd' NOT NULL, "
                 "twice INTEGER GENERATED ALWAYS AS (small * 2) STORED, at TIMESTAMP(3), small SMALLINT)",)
TYPE_LIST = ", ".join("'" + name + "'" for name in BUILTIN_TYPES)
SUITES["p01"] = (
    Case("p01_probes", "p01", 3.9, feature="pg_type and information_schema probes", statements=(
        "SELECT oid, typname FROM pg_catalog.pg_type WHERE typname = 'int4'",
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'compat' ORDER BY table_name")),
    Case("p01_pg_type", "p01", 3.9, feature="pg_type rows for every supported built-in type", statements=(
        f"SELECT oid, typname, typlen, typcategory::text, typarray, typdelim::text, typinput::text FROM pg_type WHERE typname IN ({TYPE_LIST}) ORDER BY oid",
        f"SELECT COUNT(*) FROM pg_catalog.pg_type WHERE typname IN ({TYPE_LIST}) AND typnamespace = 11 AND typtype = 'b'",
        "SELECT t.typname, n.nspname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE t.oid = 1700")),
    Case("p01_typeinfo", "p01", 3.9, feature="psycopg TypeInfo.fetch for every supported built-in type",
         statements=tuple((name, None, {"typeinfo": True}) for name in BUILTIN_TYPES + ("integer", "double precision", "nope"))),
    Case("p01_regtype_format", "p01", 3.9, feature="regtype, to_regtype, format_type", statements=(
        "SELECT 'int4'::regtype::text, 'numeric'::regtype::text, 'timestamptz'::regtype::text, 'bigint'::regtype::text",
        "SELECT to_regtype('jsonb')::text, to_regtype('nope') IS NULL, to_regtype('integer') = 23::oid",
        "SELECT format_type(1700, 655366), format_type(1184, 3), format_type(1114, NULL), format_type(23, -1)",
        "SELECT 'nope'::regtype")),
    Case("p01_class_index", "p01", 3.9, feature="pg_class / pg_index for a table, its PK index and identity sequence", setup=CATALOG_TABLE, statements=(
        "SELECT relname, relkind::text, relnatts FROM pg_class WHERE relnamespace = 2200 ORDER BY relname",
        "SELECT c.relname FROM pg_class c WHERE c.oid = 'compat'::regclass",
        "SELECT 'compat'::regclass::text, 'compat_pkey'::regclass::text, 'compat_id_seq'::regclass::text, to_regclass('nope') IS NULL",
        "SELECT i.indisprimary, i.indisunique, i.indkey::text, c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid WHERE i.indrelid = 'compat'::regclass",
        "SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public'",
        "SELECT 'nope'::regclass")),
    Case("p01_attribute", "p01", 3.9, feature="pg_attribute, pg_attrdef and format_type for numeric/timestamptz/jsonb/identity/default/generated columns", setup=CATALOG_TABLE, statements=(
        "SELECT attname, atttypid, atttypmod, attnum, attnotnull, attidentity::text, attgenerated::text, atthasdef FROM pg_attribute WHERE attrelid = 'compat'::regclass AND attnum > 0 ORDER BY attnum",
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = 'compat' AND a.attnum > 0 ORDER BY a.attnum",
        "SELECT adnum, pg_get_expr(adbin, adrelid) FROM pg_attrdef WHERE adrelid = 'compat'::regclass ORDER BY adnum")),
    Case("p01_columns", "p01", 3.9, feature="information_schema.columns spellings, precision, defaults and identity", setup=CATALOG_TABLE, statements=(
        "SELECT column_name, ordinal_position, data_type::text, is_nullable::text, numeric_precision, numeric_scale FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'compat' ORDER BY ordinal_position",
        "SELECT column_name, column_default::text, numeric_precision_radix, datetime_precision FROM information_schema.columns WHERE table_name = 'compat' ORDER BY ordinal_position",
        "SELECT column_name, is_identity::text, identity_generation::text, is_generated::text, generation_expression::text FROM information_schema.columns WHERE table_name = 'compat' ORDER BY ordinal_position")),
    Case("p01_tables_constraints", "p01", 3.9, feature="information_schema tables, table_constraints, key_column_usage, schemata, namespaces", setup=CATALOG_TABLE + ("CREATE TABLE aux (k TEXT)",), statements=(
        "SELECT table_name, table_type::text FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name",
        "SELECT constraint_name, constraint_type::text, table_name FROM information_schema.table_constraints WHERE table_schema = 'public' AND constraint_type = 'PRIMARY KEY' ORDER BY constraint_name",
        "SELECT constraint_name, column_name, ordinal_position FROM information_schema.key_column_usage WHERE table_name = 'compat'",
        "SELECT schema_name FROM information_schema.schemata WHERE schema_name IN ('public', 'pg_catalog', 'information_schema') ORDER BY schema_name",
        "SELECT oid, nspname FROM pg_namespace WHERE nspname IN ('pg_catalog', 'public') ORDER BY oid",
        "SELECT current_database(), current_schema()",
        "SELECT table_catalog = current_database() FROM information_schema.tables WHERE table_name = 'aux'")),
    Case("p01_sequences", "p01", 3.9, feature="pg_sequence for identity and standalone sequences", setup=CATALOG_TABLE + (
            "CREATE SEQUENCE p01_seq INCREMENT BY 5 START WITH 10 MAXVALUE 1000 CYCLE",), sequences=("p01_seq",), statements=(
        "SELECT seqtypid, seqstart, seqincrement, seqmax, seqmin, seqcache, seqcycle FROM pg_sequence WHERE seqrelid = 'compat_id_seq'::regclass",
        "SELECT seqtypid, seqstart, seqincrement, seqmax, seqmin, seqcycle FROM pg_sequence WHERE seqrelid = 'p01_seq'::regclass",
        "SELECT relkind::text FROM pg_class WHERE oid = 'p01_seq'::regclass")),
    Case("p01_transactional", "p01", 3.9, feature="catalog views follow transactional DDL in one session", statements=(
        "BEGIN", "CREATE TABLE aux (id INTEGER PRIMARY KEY)",
        "SELECT relname FROM pg_class WHERE relname LIKE 'aux%' ORDER BY relname",
        "ROLLBACK",
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'aux'",
        "BEGIN", "SAVEPOINT s", "CREATE TABLE aux (id INTEGER)", "ROLLBACK TO s",
        "SELECT COUNT(*) FROM pg_class WHERE relname = 'aux'", "COMMIT",
        "CREATE TABLE aux (id INTEGER)", "DROP TABLE aux",
        "SELECT COUNT(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relname = 'aux'")),
    Case("p01_restart", "p01", 3.9, feature="catalog metadata (including OIDs) survives a durable restart", setup=CATALOG_TABLE + (
            "CREATE TABLE aux (o OID, i OID)",
            "INSERT INTO aux SELECT 'compat'::regclass, 'compat_pkey'::regclass"), statements=(
        "SELECT a.o = c.oid FROM aux a JOIN pg_class c ON c.relname = 'compat'",), restart=(
        "SELECT a.o = 'compat'::regclass, a.i = 'compat_pkey'::regclass FROM aux a",
        "SELECT attname, atttypid, atttypmod, attidentity::text FROM pg_attribute WHERE attrelid = 'compat'::regclass AND attnum > 0 ORDER BY attnum",
        "SELECT column_name, column_default::text FROM information_schema.columns WHERE table_name = 'compat' ORDER BY ordinal_position",
        "SELECT relname, relkind::text FROM pg_class WHERE relnamespace = 2200 ORDER BY relname")),
)
SUITES["selfcheck"] = (
    Case("selfcheck_point_select", "selfcheck", 0.0, statements=("SELECT label FROM compat WHERE id = 3",)),
    Case("selfcheck_future_step", "selfcheck", 99.0, statements=("SELECT label FROM compat WHERE id = 1",)),
    Case("selfcheck_restart", "selfcheck", 0.0,
         statements=("INSERT INTO compat VALUES (4, FALSE, 'durable')",),
         restart=("SELECT label FROM compat WHERE id = 4",)),
)


# Basic mathematical slice. Queries are frozen from the independently checked
# core contract; each hosted run still executes native PostgreSQL as reference.
_NUMERIC_PROGRAMS = json.loads((Path(__file__).parent / "fixtures" / "sql_numeric_programs.json").read_text(encoding="utf8"))["programs"]
SUITES["q01"] += tuple(Case(program["name"], "q01", 3.9, setup=(),
    statements=tuple(program["sql"]), tables=("nf", "nd"), sequences=("nf_seq",),
    feature="ABS/CEIL/FLOOR/TRUNC/SIGN overloads, decimal matrix and strict evaluation")
    for program in _NUMERIC_PROGRAMS)
SUITES["q01"] += (Case("q01_numeric_parameter_binary", "q01", 3.9, setup=(),
    statements=(
        ("SELECT abs(%s::int2),abs(%s::real),ceil(%s::numeric),floor(%s::float8),trunc(%s::numeric,%s::int4)",
         (-12,-1.25,Decimal("1.2"),-1.2,Decimal("-1.239"),2), {"binary":True}),
        ("SELECT sign(%s::numeric),trunc(%s::numeric,%s::int4),abs(%s::int8)",
         (Decimal("NaN"),None,2,-123), {"binary":True}),
    ), feature="numeric overloads with typed parameters and binary results"),)



_SQRT_PROGRAMS = json.loads((Path(__file__).parent / "fixtures" / "sql_square_root_programs.json").read_text(encoding="utf8"))["programs"]
SUITES["q01"] += tuple(Case(program["name"], "q01", 3.9, setup=(),
    statements=tuple(program["sql"]), tables=("roots", "root_default"), sequences=("root_seq",),
    feature="SQRT exact decimal matrix, overloads, generated/default values and atomicity")
    for program in _SQRT_PROGRAMS)
SUITES["q01"] += (Case("q01_sqrt_parameter_binary", "q01", 3.9, setup=(),
    statements=(
        ("SELECT sqrt(%s::numeric),sqrt(%s::float8),sqrt(%s::real)",
         (Decimal("2.25"),4.0,9.0), {"binary":True}),
        ("SELECT sqrt(%s::numeric),sqrt(%s::float8)",(None,float("nan")), {"binary":True}),
        ("SELECT sqrt(%s::numeric)",(Decimal("-1"),), {"binary":True}),
        ("SELECT sqrt(%s::float8) AS binary_value,sqrt(%s::float8)::text AS text_value",
         (-0.0,-0.0), {"binary":True}),
    ), feature="SQRT typed/binary parameters and2201F domain errors"),)


def split_statement(statement):
    if isinstance(statement, str):
        return statement, None, {}
    if len(statement) == 2:
        return statement[0], statement[1], {}
    return statement


REPORTED = ("TimeZone", "DateStyle", "IntervalStyle")


def type_info(connection, name):
    """psycopg TypeInfo.fetch(connection, name) as comparable values."""
    from psycopg.types import TypeInfo
    try:
        info = TypeInfo.fetch(connection, name)
    except Exception as error:  # noqa: BLE001 - compared by SQLSTATE
        return error_result(error)
    if info is None:
        return {"sqlstate": "00000", "typeinfo": None}
    return {"sqlstate": "00000", "typeinfo": {"name": info.name, "oid": info.oid, "array_oid": info.array_oid,
                                              "regtype": info.regtype, "delimiter": info.delimiter}}


def run_statements(connection, statements):
    events = []
    for statement in statements:
        sql, parameters, options = split_statement(statement)
        if options.get("typeinfo"):
            outcome = type_info(connection, sql)
            events.append({"sql": "TypeInfo.fetch(" + sql + ")", "outcome": outcome})
            continue
        outcome = execute(connection, sql, parameters, binary=bool(options.get("binary")))
        status = {name: connection.info.parameter_status(name) for name in REPORTED}
        events.append({"sql": sql, "parameters": json_value(parameters), "binary": bool(options.get("binary")),
                       "outcome": outcome, "parameter_status": status})
    return events



# Conditional DDL: real catalog/data behavior, not NoticeResponse parity.
SUITES["p01"] += (
 Case('p01_conditional_create_absent',"p01",3.9,setup=(),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER)', 'SELECT COUNT(*) AS value FROM compat'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_different',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(other TEXT)', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_default',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(other INTEGER DEFAULT undefined_function())', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_divide',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(other INTEGER DEFAULT 1/0)', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_identity',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER GENERATED ALWAYS AS IDENTITY(INCREMENT BY 0))', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_generated',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER GENERATED ALWAYS AS(nonexistent) STORED)', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_duplicates',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER,id INTEGER)', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_primary',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER PRIMARY KEY,v INTEGER PRIMARY KEY)', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_not_null',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER NOT NULL NOT NULL)', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_table_defaults',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER DEFAULT 1 DEFAULT 2)', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_table_collision_sequence',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE TABLE IF NOT EXISTS ddl_seq(id INTEGER)', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_sequence_collision_table',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE SEQUENCE IF NOT EXISTS compat INCREMENT BY 0', 'SELECT id,v FROM compat ORDER BY id'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_zero',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq INCREMENT BY 0', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_type',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq AS TEXT', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_bounds',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq MINVALUE 10 MAXVALUE 1', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_cache',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq CACHE 0', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_changed',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq START WITH 100 INCREMENT BY -1', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_redundant_0',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq AS TEXT AS BIGINT', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_redundant_1',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq INCREMENT BY 1 INCREMENT BY 2', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_redundant_2',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq MINVALUE 1 NO MINVALUE', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_redundant_3',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq NO MAXVALUE MAXVALUE 20', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_redundant_4',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq START 1 START 2', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_redundant_5',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq CACHE 1 CACHE 1', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_existing_sequence_redundant_6',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq CYCLE NO CYCLE', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_redundant_0',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq AS TEXT AS BIGINT', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_redundant_1',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq INCREMENT BY 1 INCREMENT BY 2', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_redundant_2',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq MINVALUE 1 NO MINVALUE', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_redundant_3',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq NO MAXVALUE MAXVALUE 20', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_redundant_4',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq START 1 START 2', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_redundant_5',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq CACHE 1 CACHE 1', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_redundant_6',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq CYCLE NO CYCLE', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_absent_invalid_type',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq AS TEXT', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_sequence_absent',"p01",3.9,setup=(),statements=('CREATE SEQUENCE IF NOT EXISTS ddl_seq START WITH 9', "SELECT nextval('ddl_seq') AS value"),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_drop_missing_table',"p01",3.9,setup=(),statements=('DROP TABLE IF EXISTS missing', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_drop_existing_table',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('DROP TABLE IF EXISTS compat', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_wrong_kind_table',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('DROP TABLE IF EXISTS ddl_seq', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_ordinary_if_table',"p01",3.9,setup=(),statements=('CREATE TABLE if(id INTEGER)', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_drop_if_table',"p01",3.9,setup=('CREATE TABLE if(id INTEGER)',),statements=('DROP TABLE if', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_drop_missing_sequence',"p01",3.9,setup=(),statements=('DROP SEQUENCE IF EXISTS missing', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_drop_existing_sequence',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('DROP SEQUENCE IF EXISTS ddl_seq', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_wrong_kind_sequence',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('DROP SEQUENCE IF EXISTS compat', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_ordinary_if_sequence',"p01",3.9,setup=(),statements=('CREATE SEQUENCE if', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_drop_if_sequence',"p01",3.9,setup=('CREATE SEQUENCE if',),statements=('DROP SEQUENCE if', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_ordinary_duplicate_table',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)', 'INSERT INTO compat VALUES(1,10)'),statements=('CREATE TABLE compat(id INTEGER)', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_ordinary_duplicate_sequence',"p01",3.9,setup=('CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3',),statements=('CREATE SEQUENCE ddl_seq', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_bad_create_clause',"p01",3.9,setup=(),statements=('CREATE TABLE IF EXISTS compat(id INTEGER)', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_bad_drop_clause',"p01",3.9,setup=(),statements=('DROP TABLE IF NOT EXISTS compat', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_identity_owned',"p01",3.9,setup=('CREATE TABLE compat(id INTEGER GENERATED ALWAYS AS IDENTITY)',),statements=('DROP SEQUENCE IF EXISTS compat_id_seq', 'SELECT 1 AS value'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case('p01_conditional_quoted_case',"p01",3.9,setup=('CREATE TABLE "COMPAT"(id INTEGER)',),statements=('CREATE TABLE IF NOT EXISTS compat(id INTEGER)', 'SELECT COUNT(*) AS value FROM compat'),tables=("aux","compat","if",'"COMPAT"'),sequences=("ddl_seq","if","compat","compat_id_seq"),feature="native conditional DDL and post-command state"),
 Case("p01_conditional_restart","p01",3.9,setup=(
 "CREATE TABLE compat(id INTEGER PRIMARY KEY,v INTEGER DEFAULT 7)","INSERT INTO compat VALUES(1,10)",
 "CREATE SEQUENCE ddl_seq START WITH 19 INCREMENT BY 3","CREATE TABLE aux(id INTEGER PRIMARY KEY,v BIGINT)",
 'CREATE TABLE "Case Table"("Case Column" INTEGER)','CREATE SEQUENCE "Case Seq"'),
 tables=("aux","compat",'"Case Table"'),sequences=("ddl_seq",'"Case Seq"'),feature="skipped definitions and quoted table/column/sequence names survive durable replay",statements=(
 "CREATE TABLE IF NOT EXISTS compat(other INTEGER DEFAULT undefined_function())",
 "CREATE SEQUENCE IF NOT EXISTS ddl_seq AS TEXT AS BIGINT",
 "DROP TABLE IF EXISTS missing","DROP SEQUENCE IF EXISTS missing",
 "INSERT INTO aux SELECT 1,nextval('ddl_seq')","INSERT INTO aux SELECT 2,nextval('ddl_seq')",'INSERT INTO "Case Table" VALUES(17)'),restart=(
 "SELECT id,v FROM compat ORDER BY id","SELECT id,v FROM aux ORDER BY id",
 "INSERT INTO compat(id) VALUES(2)","SELECT id,v FROM compat ORDER BY id",'SELECT "Case Column" FROM "Case Table"',
 "SELECT relname::text FROM pg_class WHERE relname='Case Seq'")),
)

SUITES["p01"] += (Case("p01_numeric_restart", "p01", 3.9, setup=(),
    statements=(
        "CREATE SEQUENCE numeric_seq",
        "CREATE TABLE numeric_state(id INT PRIMARY KEY,v NUMERIC,n NUMERIC DEFAULT trunc(NULL::numeric,nextval('numeric_seq')::int4),g NUMERIC GENERATED ALWAYS AS(trunc(abs(v),1)) STORED)",
        "INSERT INTO numeric_state(id,v) VALUES(1,-1.29)",
        "SELECT id,v,n,g FROM numeric_state ORDER BY id",
    ), restart=(
        "SELECT id,v,n,g FROM numeric_state ORDER BY id",
        "INSERT INTO numeric_state(id,v) VALUES(2,-2.99) RETURNING id,g,n",
        "SELECT nextval('numeric_seq') AS value",
        "UPDATE numeric_state SET v=-3.81 WHERE id=1 RETURNING g",
    ), tables=("numeric_state",), sequences=("numeric_seq",),
    feature="numeric default/generated expression recovery and strict sequence effects"),)


SUITES["p01"] += (Case("p01_sqrt_restart", "p01", 3.9, setup=(),
    statements=(
        "CREATE TABLE root_replay(id INT PRIMARY KEY,v NUMERIC,n NUMERIC DEFAULT sqrt(4.0),g NUMERIC GENERATED ALWAYS AS(sqrt(v)) STORED)",
        "INSERT INTO root_replay(id,v) VALUES(1,4)",
        "SELECT id,v,n,g FROM root_replay ORDER BY id",
    ), restart=(
        "SELECT id,v,n,g FROM root_replay ORDER BY id",
        "INSERT INTO root_replay(id,v) VALUES(2,9) RETURNING g,n",
        "UPDATE root_replay SET v=-1 WHERE id=1",
        "SELECT id,v,g,n FROM root_replay ORDER BY id",
    ), tables=("root_replay",),
    feature="SQRT generated/default recovery and failed post-restart update atomicity"),)


def drop_tables(psycopg, arguments, tables, sequences=()):
    with psycopg.connect(**arguments) as connection:
        execute(connection, "ROLLBACK")
        for name in tables:
            execute(connection, "DROP TABLE " + name)
        for name in sequences:
            execute(connection, "DROP SEQUENCE " + name)


def run_semantic_case(psycopg, arguments, case, restart):
    """Run one case on one side; `restart()` crash-restarts that side in place."""
    drop_tables(psycopg, arguments, case.tables, case.sequences)
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
        primary_error = sys.exc_info()[1]
        try:
            drop_tables(psycopg, arguments, case.tables, case.sequences)
        except Exception as cleanup_error:
            # Recovery can fail before a replacement listener exists. Preserve
            # that failure instead of replacing it with connection-refused.
            if primary_error is None:
                raise
            if hasattr(primary_error, "add_note"):
                primary_error.add_note("case cleanup also failed: " + type(cleanup_error).__name__)


class Candidate:
    """A restartable RAMSQL process; restart reuses the same --data WAL."""

    def __init__(self, binary, arguments):
        self.binary, self.arguments, self.manager, self.connection = binary, arguments, None, None

    def start(self):
        self.manager = running_ramsql(self.binary, **self.arguments)
        started = self.manager.__enter__()
        self.arguments = dict(self.arguments, create_data=False)  # restarts reopen the created WAL
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


def write_report(path, report):
    # Plain JSON may legally retain escaped lone surrogates. Client JSON
    # decoders can expose them as Python surrogate code points, which UTF8
    # cannot encode. ASCII escapes preserve every code point in valid JSON.
    path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


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
    write_report(args.report, report)
    print(json.dumps(report["summary"], sort_keys=True))
    print("Report: " + str(args.report.resolve()))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
