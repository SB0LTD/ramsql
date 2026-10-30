# Module: `types` and `value` (configuration, cells, casts and value operators)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/types.sig`, `zpm/src/core/sql/value.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | types 695 lines, 30 KB; value 589 lines, 29 KB |
| Owner lane | B7 |
| Status | beta |
| Formatting (`sig fmt --check`) | `types.sig` dirty; `value.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`types.sig` holds the SQL `Config`, the `Error` set (each error maps to one
SQLSTATE), `ExecutionControl` (work budget and cancellation polling), the
type enums and the PostgreSQL type table. It also defines the `Cell` tagged
union with `compare`, `hash` and store-time `validate`.

`value.sig` holds the per-statement `Session` (time zone, zone resolver,
clocks, sequence hook). It implements text input and output for every type,
casts by pg_cast context, typmod coercion, and the operators on numeric,
float, datetime and interval cells. Integer arithmetic lives in `expr.sig`.
The value-specific algorithms live in `numeric.sig`, `float_text.sig`,
`temporal.sig` and `jsonb.sig`.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Config` | struct | Comptime capacities and options. Defaults include `name_bytes` 32, `text_bytes` 128, `predicates` 8, `expr_depth` 64, `numeric_digits` 64, `json_depth` 64 (`types.sig:8-99`). |
| `Error` | error set | One SQLSTATE per member, documented inline (`types.sig:109-236`). |
| `ExecutionControl.checkpoint/advance/reset` | fn | Counts work, enforces `max_work` (`QueryWorkLimit`) and polls the cancel hook every `poll_interval` steps (`types.sig:243-284`). |
| `DataType`, `Type`, `typeInfo`, `typeByName`, `typeFromOid`, `sqlTypeName` | enums/fn | 16 stored types plus `unknown`, with their PostgreSQL OIDs and names (`types.sig:290-460`). |
| `Values(config).Cell`, `compare`, `hash`, `validate` | type/fn | Tagged cell. Total order within a tag. Hash consistent with `compare`. Checks NOT NULL, tag and int2/int4/oid range on store (`types.sig:500-695`). |
| `castable`, `comparisonType`, `common`, `arithmeticType` | fn | Cast existence by context, comparison operand type, `select_common_type`, arithmetic result type (`value.sig:104-160`). |
| `Io(config).parse/format/cast/applyTypmod` | fn | Input and output functions, explicit/assignment/implicit casts, NUMERIC(p,s) and TIMESTAMP(p) typmods (`value.sig:213-478`). |
| `Io.arithmetic/negate/atZone/resolveZone` | fn | Non-integer operators, unary minus, AT TIME ZONE (`value.sig:487-589`). |

## 3. Dependencies and layering

- types imports `numeric`, `temporal` and `jsonb`. value imports `ast`,
  `expr`, `float_text`, `jsonb`, `numeric`, `temporal` and `types`.
- Almost every `sql/*.sig` imports types. value is imported by `binder`,
  `dml`, `exec`, `expr` and `sql.sig`.
- Layer rule: within L0 there are import cycles: `types` and `numeric`,
  `types` and `temporal`, `types` and `jsonb`, `value` and `expr`. Sig
  resolves them, but they blur which file is the lowest level. Recorded
  under SQLF-12.

## 4. Invariants

- **INV-TYPES-1**: Within one tag, `compare` is a total preorder. NULL sorts
  first (callers apply NULLS FIRST/LAST). Float NaN equals NaN and is
  greater than everything else, and `-0 = 0`. Text compares as unsigned
  bytes, the "C" collation (`types.sig:576-610`, `491-498`).
- **INV-TYPES-2**: `compare(a, b) == 0` implies `hash(a) == hash(b)`. Floats
  are normalized, NUMERIC ignores trailing fractional zeros, and INTERVAL
  hashes its 30-day span (`types.sig:632-676`).
- **INV-TYPES-3**: `validate` enforces NOT NULL, the exact cell tag, and the
  int2 (22003), int4 (22003) and oid ranges before a value is stored
  (`types.sig:678-693`).
- **INV-TYPES-4**: `advance(n)` behaves exactly like `n` calls to
  `checkpoint`, including the work-limit position and when the hook is
  called (`types.sig:257-283`).
- **INV-VALUE-1**: The implicit casts are a subset of the assignment casts,
  which are a subset of the explicit casts. Each is a subset of PostgreSQL's
  pg_cast for these 16 types (`value.sig:104-128`).
- **INV-VALUE-2**: Integer results are range-checked in the result type.
  Float and NUMERIC to integer casts round (float half-even, NUMERIC half
  away from zero) and then check the range. No result wraps
  (`value.sig:349-404`).
- **INV-VALUE-3**: For every finite value x of a type, `parse(format(x)) ==
  x`. This is assumed by text COPY, the wire text format and change
  records. It has not been checked systematically.

## 5. Current status and evidence

All 16 types are implemented with PostgreSQL text formats (DateStyle ISO,
IntervalStyle postgres, extra_float_digits 1). Since `9606877`, value.sig
gained `comparisonType` (`value.sig:131-134`), which fixed SQLF-03. Config
gained four executor options (`block_presence_filters`,
`factorized_aggregates`, `typed_grouped_aggregates`, `dimension_group_ids`).
Error gained `WindowingError` (42P20) and `InvalidObjectDefinition` (42P17).
Evidence: `tests/test_sql_types.sig` (fixtures measured on PostgreSQL 18)
and `tests/test_sql_expr.sig` (`assignmentAndDialects`, `errors`).

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-01 | high | robustness | `integer + date` and `date ± integer` pass an int8 straight to `datePlusDays` (`value.sig:494`, `529`). `-b` at `529` overflows when b is the int8 minimum, for example `date '2000-01-01' - (-9223372036854775808)`. Details in [temporal](temporal.md). | Restrict the operands to int4 in the binder, and use checked negation and addition. | B7 |
| SQLF-05 | medium | correctness | `date - date` returns `a - b` with no infinity check and no int4 range check (`value.sig:530`), although the binder types it int4 (`binder.sig:519-521`). `'infinity'::date - '2000-01-01'::date` returns 2147483647, and `'-infinity' - 'infinity'` returns -4294967295 in an int4 column. PostgreSQL raises 22008. | Return 22008 for infinite operands and range-check with `checkedInteger`. | B7 |
| SQLF-06 | medium | robustness | Formatting uses stack buffers of `text_bytes*8+64` bytes (`value.sig:183`, `244`, `426`, `436`). RAMSQL allows `text_bytes` up to 65535 (`build.sig:126`), which gives about 512 KB per frame. If the compiler inlines these into the recursive evaluator, deep expressions can overflow the stack (unverified: frame layout depends on the compiler). | Format into caller-owned scratch, and measure the worst-case stack at the largest profile. | B7 |
| SQLF-08 | medium | maintainability | `Io.arithmetic` picks a branch from the cell tags and ignores `op` for `integer + date` and `date - date` (`value.sig:494`, `530`). It is correct only because of binder invariants. | Match on `(op, left tag, right tag)` and return `UndefinedFunction` otherwise. Covered by the B10 differential. | B10 |
| SQLF-11 | low | correctness | Truncation to NAME's 63 bytes can split a multi-byte UTF-8 character (`value.sig:290`, `424`, `428`). PostgreSQL clips on a character boundary. | Clip at the last complete UTF-8 sequence. | B7 |
| SQLF-12 | low | maintainability | The import cycles in section 3. The duplicated integer-to-text writers: `value.sig` `integerText`, `expr.sig` `digitsCell`, `deparse.sig` `integer`, `temporal.sig` `Out.number`, `datetime_format.sig` `Writer.number`. | Move to one shared writer. Make `types.sig` import-free of value modules, with cell payload types in their own file. | B7 |

## 7. Security considerations

- `Io.parse` is the input function for every attacker-supplied literal, COPY
  field and spliced parameter. Its failures are typed SQLSTATEs, except for
  the temporal overflows (SQLF-01).
- `resolveZone` calls the host's zone resolver with an attacker-chosen name.
  RAMSQL's resolver validates TZif bytes when loading them
  (`ramsql/src/timezones.sig:158`).
- `ExecutionControl` is the cancellation and statement-timeout mechanism.
  Work done without a checkpoint is a DoS surface (SQLF-07,
  [jsonb](jsonb.md)).

## 8. Tests

- **Existing:**
  - `test_sql_types.sig`: `rounding`, `numeric`, `floats`, `temporals`, `tzif`, `json` and `changeLog`. These check text I/O, typmods, casts, error classes and change-record round trips for every type.
  - `test_sql_expr.sig`: dialect assignment and error SQLSTATEs.
  - `test_sql_work_control.sig`: batched `advance` matches scalar `checkpoint` exactly, including hook timing.
  - `test_sql_nullif_types.sig`.
- **Missing:**
  - [ ] B7: cast-matrix differential. For every (from, to, context) over the
    16 types plus `unknown`, compare against PostgreSQL 16 and 18 with
    boundary values for each type: accept or reject, SQLSTATE, and output
    text.
  - [ ] B7 + C1: a property test of `parse(format(x)) == x` per type
    (INV-VALUE-3), with generators for each cell tag.
  - [ ] C1: compare/hash laws per tag (FV-13, FV-20) over generated cells,
    including NaN, -0, numeric scale variants and intervals that differ but
    have equal spans.
  - [ ] B7: regressions for SQLF-05 (infinite date subtraction), the
    SQLF-01 int8 date operands, and NAME truncation of multi-byte text.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-02 | vectors + oracle | `checkedInteger` and the integer casts in `Io.cast` return the exact value or 22003. Float to integer uses rint and NUMERIC to integer rounds half away from zero, both before the range check. | todo |
| FV-13 | oracle | `Values.compare` is a strict weak order for each tag with NULLS FIRST/LAST and DESC. A sort is a sorted permutation. | todo |
| FV-18 | oracle | `Io.format` of float4/float8 equals `float_text.format` and round-trips (see [numeric-and-float](numeric-and-float.md)). | todo |
| FV-20 | oracle | For every tag, `compare(a, b) == 0` implies `hash(a) == hash(b)`, and byte order is a total order consistent with equality. | todo |

## 10. Benchmarks

S1: `compare` and `hash` per tag, and `format`/`parse` per type, as
instruction counts. S2: group, join and DISTINCT, which hash and compare
cells. Gates: S1 +3% in PR CI; S2 median +5% nightly.

## 11. Open questions

- Collation: only byte order exists. Should ICU or libc collations ever be
  supported, given the no-runtime-dependency rule? Until then,
  differential tests must run PostgreSQL with the C collation.
- Should `Config` defaults match RAMSQL's PostgreSQL profile
  (`nulls_first_ascending = false`, `integer_bits = 32`), so library users
  get PostgreSQL semantics by default?
