# Module: `temporal`, `tzif` and `datetime_format` (dates, times, zones, to_char)

| Field | Value |
| --- | --- |
| Repository / path | `zpm/src/core/sql/temporal.sig`, `zpm/src/core/sql/tzif.sig`, `zpm/src/core/sql/datetime_format.sig` |
| Pinned revision | ZPM `2f060c2` (from `build.sig.zon`) / RAMSQL `6d677ee` |
| Layer | ZPM core (L0) |
| Size | temporal 848 lines, 34 KB; tzif 405 lines, 15 KB; datetime_format 128 lines, 8 KB |
| Owner lane | B7 (models: C1) |
| Status | beta |
| Formatting (`sig fmt --check`) | `temporal.sig` dirty; `tzif.sig` clean; `datetime_format.sig` dirty (measured at `2f060c2`, 2026-10-30; REPO-21) |

## 1. Purpose and scope

`temporal.sig` stores DATE as i32 days since 2000-01-01, TIMESTAMP[TZ] as
i64 microseconds (TIMESTAMPTZ in UTC), and INTERVAL as {months, days, us},
as PostgreSQL does. It covers ISO 8601 input, DateStyle ISO and
IntervalStyle postgres output, calendar arithmetic, and local/UTC conversion
with PostgreSQL's DST gap and overlap rules. `tzif.sig` decodes TZif v2+
files in place and parses POSIX TZ strings. `datetime_format.sig` implements
`to_char(timestamp|timestamptz, text)` templates. There is no TIME or TIMETZ
type, no non-ISO input styles, no zone abbreviations, no ISO 8601 durations
(`P1D`), and no `extract`, `date_trunc` or `age`.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Interval`, `Zone`, `Resolver`, range constants | types/consts | Representation and PostgreSQL range limits (`temporal.sig:17-54`). |
| `validDate`, `validTimestamp`, `monthLength`, `dateOf`, `civilOf` | fn | Range checks and the proleptic Gregorian calendar (`56-99`). |
| `offsetAt`, `toUtc`, `toLocal` | fn | Zone offset at an instant, and local-to-UTC per DetermineTimeZoneOffset (`104-136`). |
| `parseDate`, `parseTimestamp`, `parseInterval` | fn | Input functions: 22007 malformed, 22008 out of range (`328-374`, `664-740`). |
| `formatDate`, `formatTimestamp`, `formatInterval`, `roundTimestamp` | fn | Output functions and TIMESTAMP(p) rounding (`377-544`). |
| `intervalSpan`, `compareInterval`, `negateInterval`, `addIntervals` | fn | 30-day-month ordering and checked interval arithmetic (`744-765`). |
| `timestampPlus`, `timestamptzPlus`, `timestampDifference`, `datePlusDays`, `dateToTimestamp`, `timestampToDate` | fn | Datetime arithmetic and conversions (`785-843`). |
| `tzif.view`, `tzif.validate` | fn | Locate the 64-bit block; full structural validation (`tzif.sig:70-121`). |
| `tzif.parsePosix`, `infoAt`, `nextBoundary`, `posixInfo`, `posixBoundary`, `resolveLocal` | fn | POSIX rules, offset lookup, next transition, gap and overlap resolution (`223-405`). |
| `tzif.leap`, `daysFromCivil`, `yearOf` | fn | Calendar helpers (`253-277`). |
| `datetime_format.format(us, pattern, out)` | fn | Writes the whole result or nothing (a sizing pass first). Unsupported tokens give 0A000 (`datetime_format.sig:119-128`). |

## 3. Dependencies and layering

- temporal imports `types` and `tzif`. tzif imports `types`.
  datetime_format imports `temporal` and `types`.
- temporal is imported by `change_log`, `datetime_format`, `expr`, `types`,
  `value` and `sql.sig`. tzif by `temporal` and `sql.sig` (re-exported for
  RAMSQL's zone provider). datetime_format by `expr`.
- Layer rule: no violation. Zone data comes from the host:
  `ramsql/src/timezones.sig` loads and validates TZif files.

## 4. Invariants

- **INV-TEMPORAL-1**: A DATE is in `[min_date, end_date)` or is a ±infinity
  sentinel. A TIMESTAMP[TZ] is in `[min_timestamp, end_timestamp)` or is a
  sentinel. Native precision rounding may produce the next boundary value;
  the follow-up preserves that PostgreSQL behavior explicitly.
- **INV-TEMPORAL-2**: `civilOf(dateOf(y, m, d)) == (y, m, d)` for every
  valid civil date in range. Leap years are Gregorian (`tzif.sig:253-255`).
- **INV-TEMPORAL-3**: Adding months clamps the day to the target month's
  length (`temporal.sig:778`), as PostgreSQL does.
- **INV-TEMPORAL-4**: Interval order and hash use `(months*30 + days) *
  86400e6 + us` computed in i128.
- **INV-TEMPORAL-5 (reported paths fixed; FV-12 proof open)**: No intermediate i64
  operation overflows for any input that passes the guards.
- **INV-TZIF-1**: After `validate(bytes)` succeeds, `view`, `infoAt` and
  `nextBoundary` read only inside `bytes`. Callers must validate first;
  `Zone.bytes` is a plain slice.
- **INV-TZIF-2**: In a spring-forward gap, local times use the offset
  before the transition. In a fall-back overlap, they use the offset after
  it (PostgreSQL Appendix B.1).
- **INV-DTFMT-1**: `datetime_format.format` writes all of its output or
  none of it. Infinite input and an empty template return NULL in the
  caller (`expr.sig:864-873`).

## 5. Current status and evidence

temporal.sig and tzif.sig are unchanged since ZPM `9606877`.
datetime_format.sig is new. Evidence:

- `test_sql_types.sig` `temporals`: ISO input, BC, `24:00`, fraction
  rounding, TIMESTAMP(p), interval output, date and timestamp arithmetic, AT
  TIME ZONE, POSIX gaps and overlaps, clocks.
- `test_sql_types.sig` `tzif`: a synthetic TZif file with a footer rule,
  corruptions, and every truncation.
- `test_sql_to_char.sig`: 252 PostgreSQL 16.15 fixtures from
  `tests/evidence/to-char-pg16.tsv`, plus error atomicity.

## Current follow-up (30 October 2026)

The tables below retain the historical audit locations. The temporal boundary
follow-up fixes SQLF-01's six reported overflow paths and SQLF-05's infinite-date
subtraction. The core contract checks every candidate timestamp year against
wide arithmetic, plus 28 PostgreSQL 16.15 fixtures captured with UTC explicitly
set. RAMSQL adds two PG16/PG18 server cases and native SB0 SQLSTATE/session
survival probes. See ZPM `docs/SQL_TEMPORAL_BOUNDARIES.md` for exact scope.
FV-12's formal theorem remains open; these are executable regressions, not a
proof of every temporal operation.

SQLF-11(a)'s suggested range rejection is withdrawn: native PostgreSQL 16.15 and
18.3 source permit rounding across the documented finite boundary. A test now
preserves that behavior. Other SQLF-11 subitems and SQLF-02 syntax remain open.

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| SQLF-01 | high | robustness/security | Unchecked i64 overflow is reachable from one SQL literal. It traps in Debug/ReleaseSafe and is undefined behavior in ReleaseFast. Cases: (1) `parseTimestamp` allows year <= 294277 (`temporal.sig:352`) and then computes `days * usecs_per_day` (`355`), so `'294277-06-01'::timestamp` overflows. (2) `dateToTimestamp` (`834`) overflows for any DATE after 294276, for example `'300000-01-01'::date::timestamp` or a date-timestamp comparison. (3) `addCalendar` (`779`): `timestamp '294276-12-31' + interval '1 month'`. (4) `datePlusDays` (`826`) with an int8 operand (binder `binder.sig:510-517`, value `value.sig:529`). (5) Interval units add after a checked multiply (`606-610`), for example `'9223372036854.9999999 seconds'::interval`. (6) `ago` negation (`733-737`). | Use `@mulWithOverflow`/`@addWithOverflow` and return 22008. Check the day count against the timestamp range before multiplying. Accept only int4 date offsets. Prove INV-TEMPORAL-5 (section 9). | B7 |
| SQLF-02 | high | correctness | SQL-standard interval literals are misread. A number without a unit defaults to seconds, and a sign starts a new number (`temporal.sig:716-729`). `'1-2'::interval` gives `-00:00:01`; PostgreSQL gives `1 year 2 mons`. `'3 4:05:06'` gives `04:05:09`; PostgreSQL gives `3 days 04:05:06` (both forms are documented in PostgreSQL section 8.5.4, Interval Input). `INTERVAL '1' DAY` is covered in [parser-and-ast](parser-and-ast.md). | Implement the `Y-M` and `D H:M:S` forms, or reject a number with no unit followed by another field (22007). | B7 |
| SQLF-11 | low | correctness | (a) `roundTimestamp` does not re-check the range after rounding (`377-384`). (b) Fractional units round half up (`606-633`); PostgreSQL uses `rint`, which is half-even (unverified). (c) A zone name such as `CET`, `EET`, `MET` or `WET` is sent to the host resolver. In tzdata these files have DST, while PostgreSQL may treat them as fixed abbreviations (unverified; depends on RAMSQL's resolver). (d) `view` trusts a prior `validate` (`temporal.sig:111`, `127`). | (a) Re-check the range. (b) Use half-even. (c) Decide the abbreviation policy. (d) Make validated zones a distinct type. | B7 |
| SQLF-12 | low | maintainability | `space`, `lower`, `insensitive` and `trim` are copied (`temporal.sig:140-160`). datetime_format has its own `Writer.number` and 53 compressed-style lines. | Shared helpers; `sig fmt` (A4). | A4 + B7 |
| SQLF-13 | medium | testing | The only boundary test is that `'294277-01-01'::timestamp` is rejected (`tests/test_sql_types.sig:190`). Nothing tests arithmetic near the range limits, SQL-standard interval forms or interval text round trips. | Add the generators in section 8. | B7 + C1 |
| SQLF-14 | high | completeness | No TIME/TIMETZ, no `extract`/`date_part`/`date_trunc`/`age`/`make_*`, and `to_char` handles only timestamps and dates. Non-ISO input such as `'Jan 1 2026'` gives 22007. See [FEATURE_GAPS](../../compatibility/FEATURE_GAPS.md). | In client-probe order. | B11 |

## 7. Security considerations

- Every input function takes attacker text. The historical SQLF-01 paths
  allowed one unprivileged literal to panic a safe build or overflow in
  ReleaseFast. The temporal follow-up checks those six reported paths and
  returns SQL errors; independent review and regression coverage do not
  replace FV-12's still-open formal proof.
- TZif bytes are host files, trusted after `validate`. RAMSQL validates
  them on load (`ramsql/src/timezones.sig:158`). The validation sweep
  covers truncation and corruption. There is no fuzzing.
- `to_char` output is bounded by `text_bytes`. Each statement renders a
  template twice, comparing up to 74 supported and 27 unsupported tokens at each position.

## 8. Tests

- **Existing:** see section 5.
- **Missing:**
  - [ ] B7: a boundary generator for every function in section 2. Use
    years -4713, -4712, 294276, 294277 and 5874897, days 1, 9, 10 and 31,
    and month and day deltas around the limits. Oracle: the same result as
    PostgreSQL 16/18, or 22008. Never a trap in ReleaseSafe.
  - [ ] B7: interval differential. Generate strings in postgres,
    SQL-standard (`Y-M`, `D H:M:S`) and ISO 8601 (`P1Y2M`) styles,
    including fractional units and `ago`. Compare with PostgreSQL output or
    SQLSTATE.
  - [ ] B7 + C1: property that `parseInterval(formatInterval(v)) == v` for
    all v, and `parseTimestamp(formatTimestamp(t)) == t` for valid t.
  - [ ] B7: DST differential. For 20 tzdata zones and every transition from
    1970 to 2037, check timestamptz input at gap and overlap times and
    `+ interval '1 day'` against PostgreSQL.
  - [ ] B7: fuzz `tzif.validate` with mutated real tzdata files. Oracle: no
    trap, and accepted files give in-bounds `infoAt` for 10^5 instants.
  - [ ] B7: `to_char` differential against PostgreSQL 18 with random
    templates, adding to the 252 PostgreSQL 16 fixtures.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |
| FV-12 | Lean + vectors | `daysFromCivil`/`dateOf` and `civilOf`/`yearOf` are inverse bijections on [4714-11-24 BC, 5874897-12-31]. Month arithmetic clamps to the month end. The leap-year rule is Gregorian. | todo |
| FV-12 (no-overflow extension; to be added to the catalog entry) | Lean | For every input accepted by the guards of `parseTimestamp`, `addCalendar`, `dateToTimestamp`, `datePlusDays`, `timestamptzPlus` and `Accumulator.add`, every intermediate i64 value is in `[-2^63, 2^63)` and the result is valid or 22008. For `parseTimestamp`: -4713 <= y <= 294277, (y, m, d) valid, 0 <= t <= 86400·10^6 implies abs(dateOf(y, m, d) · 86400·10^6 + t) < 2^63. **Historical counterexample**: y = 294277, m = 1, d = 10 overflowed unchecked i64. Checked operations now reject it; restate the theorem in terms of checked success/error. | implementation unblocked; formal theorem open |
| none | property | INV-TZIF-1: `validate(b)` succeeding implies that all `view`, `infoAt` and `nextBoundary` reads on any instant stay in bounds. | todo |
| none | differential | INV-TZIF-2 matches PostgreSQL for all transitions in the zone sample. | todo |

## 10. Benchmarks

S1: timestamp parse and format and interval parse, as instruction counts,
gated at +3% in PR CI. S7 dbMeter reporting uses `to_char` and date grouping.
Formatting `timestamptz` output also appears in S3 result encoding.

## 11. Open questions

- Preserve temporal boundary and error-class coverage in every supported
  optimize mode. The reported SQLF-01 paths are fixed; extend FV-12 to a
  formal proof rather than treating the generated range tests as one.
- Zone abbreviations: implement PostgreSQL's `Default` abbreviation set, or
  reject names that are not tzdata zones?
- Should TIME/TIMETZ and `extract` be in scope for the next release (B11)?
