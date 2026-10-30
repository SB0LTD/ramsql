# Module: `<name>`

> Copy this file for every new module page. Keep every section; write "none" or
> "not yet known" rather than deleting a heading. A reviewer must be able to
> tell from this page what the module does, what it promises, how that promise
> is checked, and what is still missing.

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/src/...` or `zpm/src/...` |
| Pinned revision | ZPM `9606877` (from `build.sig.zon`) / RAMSQL `<commit>` |
| Layer | Sig / ZPM core (L0) / ZPM platform-protocol (L1) / RAMSQL |
| Size | `<LOC>` lines, `<KB>` KB |
| Owner lane | `PLAN.md` lane id, e.g. `B1` |
| Status | prototype / beta / hardened / verified |
| Formatting (`sig fmt --check`) | clean / dirty |

## 1. Purpose and scope

One paragraph: what the module is for and what it is explicitly *not* for.

## 2. Public surface

| Symbol | Kind | Contract (one line) |
| --- | --- | --- |
| `Foo.init` | fn | caller-owned storage; never allocates |

## 3. Dependencies and layering

- Imports: ...
- Imported by: ...
- Layer rule: lower layers never import higher ones; list any violation.

## 4. Invariants

Number every invariant. Tests, proofs and reviews refer to these ids.

- **INV-<MOD>-1** — precise statement.

## 5. Current status and evidence

What works today and which test, benchmark or report proves it.

## 6. Findings

Findings come from the 2026-10-30 audit. Severity is critical, high, medium
or low. Ids match `PLAN.md` section 3.

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |

## 7. Security considerations

Trust boundary, attacker-controlled inputs, secrets handled, and denial-of-service
surface.

## 8. Tests

- **Existing:** test files and what they assert.
- **Missing:** each gap as a checklist item with the lane that owns it.

## 9. Verification obligations

| Id | Kind (Lean / TLA+ / property / fuzz / differential) | Statement | Status |
| --- | --- | --- | --- |

## 10. Benchmarks

Which suites exercise the module, which metrics matter and which regression
threshold applies.

## 11. Open questions

Decisions that need an owner before work starts.
