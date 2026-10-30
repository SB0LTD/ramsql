# Module: `numeric` and `float_text` (exact decimals and floating-point text)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/numeric.sig`, `zpm/src/core/sql/float_text.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | numeric 521 lines, 23 KB; float_text 276 lines, 10 KB |
| Owner lane | B7 (models: C1) |
| Status | beta |
| Formatting (`sig fmt --check`) | `numeric.sig` dirty; `float_text.sig` clean (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`numeric.sig` implements NUMERIC as a signed `u256` coefficient with a
display scale: value = coef x 10^-dscale. It supports NaN and ±Infinity.
The coefficient is limited to `Config.numeric_digits` digits (default 64,
maximum 76) and dscale to 120. Rounding is half away from zero, division and
modulo scales follow PostgreSQL's `select_div_scale` and `mod_var`, typmods
follow PostgreSQL 15+, and it converts to and from the base-10000 wire
format. `float_text.sig` implements float4/float8 input with the `float8in`
grammar checked before `std.fmt.parseFloat`, shortest round-trip output laid
out like `float8out`, checked arithmetic, `rint` and float-to-integer casts.

Neither file has a NUMERIC result wider than the capacity, transcendental
functions (`sqrt`, `exp`, `ln`, `power`), or float `%`, which PostgreSQL also
lacks.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Numeric`, `Special` | types | `limbs[4]`, `dscale`, `negative`, `special`. Zero is never negative (`numeric.sig:18-46`). |
| `compareValues`, `numeric_hash` | fn | Order -Inf < finite < +Inf < NaN, with NaN = NaN. The hash ignores trailing fractional zeros (`98-126`). |
| `Ops(digits).parse/format` | fn | `numeric_in` (whitespace, sign, `_` separators, exponent, NaN/Infinity) and `numeric_out` (`151-258`). |
| `Ops.add/sub/mul/div/mod/negate` | fn | Exact or `NumericCapacity` (54000). Division by zero is 22012 (`277-399`). |
| `Ops.divScale`, `baseWeight` | fn | PostgreSQL `select_div_scale` on derived base-10000 weights (`335-358`). |
| `Ops.round(value, scale)` | fn | Half away from zero. A negative scale rounds to 10^-scale. The result dscale is max(scale, 0) (`403-425`). |
| `Ops.encodeTypmod/decodeTypmod/applyTypmod` | fn | NUMERIC(p,s) with -1000 <= s <= 1000. Integer-digit overflow and ±Inf give 22003 (`429-454`). |
| `Ops.toInt`, `fromInt` | fn | Round half away from zero, then range check (22003). NaN and Inf give 0A000 (`142-147`, `458-469`). |
| `Ops.toBase10000/fromBase10000` | fn | Binary wire digits, with input digits < 10000 checked (`472-519`). |
| `float_text.parse/format` | fn | float4in/float8in (22P02 grammar, 22003 overflow and underflow) and shortest output (`84-176`). |
| `ArithmeticOf(T).add/sub/mul/div` | fn | 22003 on overflow from finite inputs and on underflow to zero for `*` and `/`. Division by zero is 22012 (`212-245`). |
| `rint`, `toInt`, `compare`, `roundedText` | fn | Round half to even keeping the sign of zero, range-checked cast, NaN-last order, `%.*e` text for float-to-NUMERIC (`185-276`). |

## 3. Dependencies and layering

- Both import `types.sig`. `float_text` also uses exactly
  `std.fmt.parseFloat` and `std.fmt.float.render`, as `sql.sig:81-84` allows.
- numeric is imported by `change_log`, `jsonb`, `parser`, `types`, `value`
  and `sql.sig`. float_text is imported by `expr`, `value` and `sql.sig`.
- Layer rule: no violation. The `types` and `numeric` import cycle is
  recorded on [types-and-values](types-and-values.md).

## 4. Invariants

- **INV-NUMERIC-1**: Every finite `Numeric` has coef < 10^digits and dscale
  <= 120. A zero coefficient clears `negative` (`setCoef`, `38-41`).
- **INV-NUMERIC-2**: `add` and `sub` are exact with dscale max(da, db).
  `mul` is exact with dscale da + db. A result outside capacity is
  `NumericCapacity`, never truncated.
- **INV-NUMERIC-3**: `div` returns the quotient rounded half away from zero
  to `divScale(a, b)`. `mod` returns `a - trunc(a/b)*b` with the sign of `a`
  and dscale max(da, db).
- **INV-NUMERIC-4**: `round` is half away from zero, and its result equals
  the rational rounding of the input.
- **INV-NUMERIC-5**: `applyTypmod(x, (p, s))` rounds to s and then requires
  at most p - s integer digits. NaN passes; ±Inf is 22003.
- **INV-NUMERIC-6**: `fromBase10000(toBase10000(x), weight, sign, dscale) ==
  x` for every finite x.
- **INV-FLOAT-1**: `parse` accepts exactly the grammar in
  `float_text.sig:1-13`. Underscores are rejected even though Zig's parser
  allows them.
- **INV-FLOAT-2**: `format` prints the shortest digits that round-trip. It
  uses fixed notation for decimal exponents -4..14 (float4: -4..5),
  otherwise `d.ddde±XX`.
- **INV-FLOAT-3**: Arithmetic never returns ±Inf from finite inputs, and
  never returns 0 from `*` or `/` of non-zero finite inputs.

## 5. Current status and evidence

Both files are unchanged since ZPM `9606877`. Evidence is
`tests/test_sql_types.sig`:

- `rounding`: PostgreSQL 18.3 fixtures, an independent integer quantization
  model over 2,001 values for `ROUND(x, 2)`, and 129 float half-steps for
  `rint`.
- `numeric`: division scales such as `1/3` giving 20 digits, typmods
  including negative scale, NaN ordering, and a base-10000 round trip.
- `floats`: notation thresholds, hex input, underflow and overflow, casts,
  and denormals.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-11 | low | correctness | `'1._5'::numeric` is accepted: the underscore check does not reset after `.` (`numeric.sig:174-183`). PostgreSQL 16 rejects an underscore right after the point. `'NaN'::float8 / 0` gives 22012 (`float_text.sig:238`). PostgreSQL's `float8_div` returns NaN for a NaN dividend. Both PostgreSQL behaviors come from reading its source and are unverified against a live server. | Require a digit on both sides of `_`. Check NaN before zero in `div`. | B7 |
| SQLF-12 | low | maintainability | `space`, `lower` and `insensitive` are copied in `numeric.sig:55-67` and `float_text.sig:18-30` (and in `temporal.sig`, `lexer.sig`, `expr.sig`). | One shared helper set. | B7 |
| SQLF-13 | medium | testing | Only `ROUND` has a model-based loop. `add`, `mul`, `div`, `mod`, typmods, `fromBase10000` with untrusted wire digits, and float round trips are covered only by fixed examples. | Add the property and oracle tests in section 8. | B7 + C1 |

The documented capacity limits are not findings: more than `numeric_digits`
significant digits, or more than 120 decimal places, give 54000 where
PostgreSQL would continue. They are listed in
[FEATURE_GAPS](../../compatibility/FEATURE_GAPS.md).

## 7. Security considerations

- Inputs: SQL literals, text COPY fields, and binary NUMERIC from the wire
  and binary COPY (`fromBase10000` rejects digits >= 10000 and limits the
  digit count, `502-519`). Binary float input is outside this module.
- All arithmetic uses `u512` or `u1024` intermediates with explicit digit
  checks before multiplying (`scaled`, `quotient`, `round`). Reading the
  code found no unchecked overflow path. A fuzz confirmation is still
  missing.
- Denial of service: parsing is linear in the input. Each operation does a
  bounded amount of wide-integer work.

## 8. Tests

- **Existing:** `test_sql_types.sig` (`rounding`, `numeric`, `floats`,
  `changeLog`). `test_sql_query.sig:228-233` checks SUM and AVG result
  scales over integers, for example `AVG(n)` giving `15.0000000000000000`.
- **Missing:**
  - [ ] C1: FV-11 oracle. Random (coef, dscale, sign, special) inputs for
    `add`, `sub`, `mul`, `div`, `mod`, `round` and `applyTypmod`, checked
    against the Lean model, with the same inputs run through PostgreSQL
    `numeric` (L7, B7). Shrink mismatches into vectors.
  - [ ] C1: FV-18 exhaustive float4 check. For all 2^32 bit patterns,
    `parse(format(x))` is bit-identical for finite x, and no shorter digit
    string round-trips. Then check float8 on 10^8 random values plus
    boundaries (subnormals, powers of two, `DBL_MAX`) against PostgreSQL
    `float8out`.
  - [ ] B7: a typmod matrix vs PostgreSQL. Precision 1..76 with scale
    from -1000 to 1000, including scale > precision.
  - [ ] B7: fuzz `fromBase10000` and `Ops.parse` with arbitrary bytes and
    groups. Oracle: no trap; any accepted value satisfies INV-NUMERIC-1.
  - [ ] B7: regressions for `'1._5'` and `'NaN'::float8 / 0`.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-11 | Lean + vectors + oracle | INV-NUMERIC-2..5: add and sub exact, mul scale s1 + s2, div scale `select_div_scale` with half-away rounding, `ROUND` half away from zero. A result outside capacity is an error, never truncated. | todo |
| FV-18 | reference algorithm + oracle | For every finite double x, `parse(format(x)) = x` and `format` emits the shortest such string. NaN and ±Infinity print as PostgreSQL does. The float4 case is exhaustive. | todo |
| FV-02 | vectors | `Ops.toInt` and `float_text.toInt` give the exact rounded value or 22003, at every int2/int4/int8 boundary ±0.5. | todo |
| FV-13 | oracle | `compareValues` and `float_text.compare` are total orders with NaN last and NaN = NaN. | todo |
| FV-20 | oracle | `compareValues(a, b) == 0` implies `numeric_hash(a) == numeric_hash(b)` (scale variants such as 1.0 and 1.00). | todo |

## 10. Benchmarks

S1: NUMERIC add, mul and div, and float parse and format, as instruction
counts, gated at +3% in PR CI. S8 (TPC-H-derived): NUMERIC SUM and AVG
throughput. S7 dbMeter: reporting aggregates.

## 11. Open questions

- What capacity policy should NUMERIC have? PostgreSQL allows up to 131072
  digits before the point and 16383 after it. RAMSQL allows 64 significant
  digits and 120 decimal places. Is a variable-length NUMERIC in scope
  (F-S01)?
- Should transcendental NUMERIC functions be implemented exactly, as
  PostgreSQL does, or not at all?
