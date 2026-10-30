# Module: `ramsql.com/` marketing website

| Field | Value |
| --- | --- |
| Repository / path | `ramsql/ramsql.com/` (added in `88bfc0c`) |
| Pinned revision | ZPM `2f060c2` (not used) / RAMSQL `6d677ee` (origin/main) |
| Layer | none: a static site outside the product and the Sig layers |
| Size | 39 files, 6.63 MB (34% of tracked bytes; `src/` is 119 KB). Text: `index.html` 201, `app.js` 171, `styles.css` 109 lines. Binaries: 22 PNG/JPG/ICO/ZIP files, including 2.61 MB in `assets/` and 3.25 MB of PWA screenshots |
| Owner lane | `A1` (extraction), `A5` (claims and copy) |
| Status | live content; not built, checked or deployed by any workflow |
| Formatting | not applicable |

## 1. Purpose and scope

A one-page marketing and licensing site for ramsql.com, served by Firebase
Hosting (`.firebaserc` project `ramsql-com`, `firebase.json` with
`"public": "public"`). `tools/build-assets.mjs` uses Playwright (`^1.64.1`,
with lockfile) to render OG images, icons and screenshots. The site is not
documentation and is not shipped in release archives.

## 2. Public surface

| Item | Where | Contract |
| --- | --- | --- |
| Landing page | `public/index.html` | hero numbers, feature claims, benchmark tabs, licensing plans |
| Benchmark data | `public/app.js:30-61` (`DATA`) | values hard-coded by hand; the comment at `:30` says they come from `/benchmarks` |
| Security headers | `firebase.json` | CSP `script-src 'self'`, HSTS preload, `X-Frame-Options: DENY`, nosniff |
| PWA metadata | `public/site.webmanifest`, `sitemap.xml`, `robots.txt` | manifest screenshots are PNGs with benchmark charts baked in |
| Asset generator | `tools/build-assets.mjs`, `tools/templates/*.html` | regenerates images and screenshots from `public/` |
| Raw art | `assets/` | 3 source PNGs and `favicon_io (5).zip`; file names contain spaces, commas and parentheses |

## 3. Dependencies and layering

- Runtime: Google Fonts (`public/index.html:49-51`), allowed by the CSP
  (`style-src`, `font-src`). No other third-party script.
- Build: Node and Playwright Chromium (`tools/package.json`, `package-lock.json`).
- Deploy: Firebase CLI, run by hand (no workflow). Where the credentials live
  is unverified.
- Links point to `github.com/SB0LTD/ramsql` and its `LICENSE`. Nothing in
  RAMSQL imports the site.

## 4. Invariants

- **INV-SITE-1**: every number names its boundary, size, client count and
  binary, and links exactly one result summary (METHODOLOGY §8).
- **INV-SITE-2**: product claims (isolation, compatibility, maturity) match
  `README.md`, `SQL_REFERENCE.md` and `compatibility/release-gates.json`.
- **INV-SITE-3**: the license text matches `LICENSE`.
- **INV-SITE-4**: no third-party script runs. The CSP enforces this today.

## 5. Current status and evidence

| Site claim | Where | Source / docs | Verdict |
| --- | --- | --- | --- |
| "488x faster than IRDB on key lookups at 1M rows"; hero "488×" and "1.43M point reads/s" | `index.html:7`, `:80-81` | `benchmarks/production-20261026/REPORT.md:126` (1,425,601.8 vs 2,920.3 ops/s, `uniform_get`). README calls these pre-beta.1 results (`README.md:274`), says they are not "a PostgreSQL/IRDB win" (`:184`) and not "general benchmark leadership" (`:228`), and reports million-row analytics and 5M scans/joins as "substantially slower than IRDB" (`:226`, `:232`) | traceable, but the headline drops the documented losses |
| "25× vs PostgreSQL 18.3, filtered scan, 100k rows" | `index.html:82` | the 1-client row in `dbmeter-reviewer-20261027/REPORT.md:67` (9,393 vs 367); the chart on the same page shows 8 clients, 16× (`app.js:49`, REPORT `:68`) | two ratios for one workload; the hero omits the client count |
| "Snapshot isolation. Shared MVCC storage. Readers never block writers." | `index.html:128` | `README.md:132` and `SQL_REFERENCE.md:135` say READ COMMITTED only; REPEATABLE READ/SERIALIZABLE are rejected (`SQL_REFERENCE.md:153`); "Published beta.2 serializes SQL execution" (`README.md:147`) | **contradicts the docs** |
| "Wire-compatible. Connect with psql, JDBC and standard PostgreSQL drivers." | `index.html:125` | gate `postgres_compatibility: failed` (`release-gates.json`); AGENTS.md says wire framing does not imply dialect compatibility | overstated |
| "RamSQL is alpha software" | `index.html:196` | version `0.3.0-beta.2` (`build.sig.zon:3`) | stale |
| Licensing plans, `licensing@ramsql.com` | `index.html:163-180` | matches `LICENSE:14-37` | consistent with LICENSE; README still says MIT (REPO-02) |
| Roadmap tab: RamSQL 43.6 vs IRDB 1,053.6 q/s | `app.js:54-58` | `dbmeter-comment-balanced-million-20261028` and `dbmeter-native-million-irdb-20261028` logs | good practice: the site publishes this loss |

## 6. Findings

| Id | Sev | Category | Evidence (`file:line`) | Remedy | Lane |
| --- | --- | --- | --- | --- | --- |
| REPO-03 | high | docs | Rows marked "contradicts", "overstated" and "stale" in section 5 | Rewrite the copy to README's scope; generate `DATA`, hero numbers and screenshots from one results file; add a docs-consistency check | A5 |
| REPO-11 | medium | hygiene | 6.63 MB of site binaries in the database repo; no build or deploy workflow; `assets/favicon_io (5).zip` | Extract to its own repository (below) | A1 |
| REPO-02 | high | license | The site matches LICENSE, but `README.md:343` and `zpm.pkg.zon:12` say MIT | Fix README and the manifest (see [release-pipeline.md](release-pipeline.md)) | A5 |

## 7. Security considerations

- The headers are strong. `style-src` allows `'unsafe-inline'` and Google
  Fonts. Self-hosting the fonts would drop a third-party request that reveals
  visitor IP addresses (a privacy question; legal impact unverified).
- No forms or cookies; contact is by `mailto:` only.
- The Firebase hosting cache that `88bfc0c` committed was removed in `455973e`.
  It holds file hashes, not secrets. Grepping the site for tokens or API keys
  found none.
- The deploy credential and who can deploy are outside this repository
  (unverified). An extracted repository should deploy from a protected
  workflow environment.

## 8. Tests

- **Existing:** none.
- **Missing:**
  - [ ] HTML and link check; CSP check (A5, in the site repository after extraction)
  - [ ] claim check: every number in `DATA` and the hero equals a value in a pinned results file (A5)

## 9. Verification obligations

| Id | Kind | Statement | Status |
| --- | --- | --- | --- |
| VO-SITE-1 | review | every public number links one `docs/benchmarks/results/` summary | open |
| VO-SITE-2 | review | the isolation and compatibility wording equals the README wording | open |

## 10. Benchmarks

None run here. The site republishes numbers from `benchmarks/`. After the A2
migration it should read them from `docs/benchmarks/results/` at a recorded
RAMSQL commit.

## 11. Open questions

Recommended extraction (maintainer approval required):

1. `git subtree split --prefix=ramsql.com` into a new `SB0LTD/ramsql.com`
   repository, so the site keeps its history.
2. In the new repository: a Firebase deploy workflow behind a protected
   environment, a link and HTML check, and a build step that pulls benchmark
   numbers from a pinned RAMSQL commit.
3. Keep raw art (`assets/`) out of git, or in LFS there. Drop the favicon zip.
4. In RAMSQL: remove `ramsql.com/` in one commit that links the new
   repository. History stays unless a rewrite is approved separately.

Open: who approves public performance copy, and should site deploys be gated
on the docs-consistency job?
