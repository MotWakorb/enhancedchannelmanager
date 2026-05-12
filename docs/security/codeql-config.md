# CodeQL Configuration — Single Source of Truth

> Operational reference for the CodeQL static analysis pipeline.
> Use this to add/remove rules, audit current configuration, or verify there
> is no drift between the custom workflow and any latent GitHub Default Setup.

- **Owner**: Security Engineer (rule decisions) + Project Engineer (workflow plumbing)
- **Scope**: First-party Python and TypeScript code in this repo
- **Authoritative ADR**: [`docs/adr/ADR-005-code-security-gating-strategy.md`](../adr/ADR-005-code-security-gating-strategy.md) — gating policy and dismissal categories
- **Last reviewed**: 2026-04-23 (bd-bsbr3 investigation)

## Single Source of Truth

There is **exactly one** CodeQL scan configured for this repository:

| Layer | Location | Owns |
|-|-|-|
| **Workflow** | [`.github/workflows/build.yml`](../../.github/workflows/build.yml), job `codeql-analysis` | When CodeQL runs, language matrix, action version, delta-zero gate |
| **Rule config** | [`.github/codeql/codeql-config.yml`](../../.github/codeql/codeql-config.yml) | Query suite, query exclusions, path-scoped exclusions |

GitHub-managed **Default Setup is `not-configured`** for this repository (verified
2026-04-23, see "Verifying no Default-Setup drift" below). All historical
analyses and all open alerts originate from `analysis_key =
".github/workflows/build.yml:codeql-analysis"`. There is no parallel
GitHub-Default-Setup pipeline producing a competing alert stream.

**If anyone proposes turning on Default Setup: don't.** It would create a second
alert source with a different rule selection, no `query-filters`, and no
`security-and-quality` extension; alerts from the two pipelines would diverge
silently and double-count in the PR-time delta-zero gate. ADR-005 Open
Question 4 records this decision explicitly.

## Why custom over Default Setup

Custom workflow is the source of truth for four reasons that the GitHub-managed
Default Setup cannot satisfy:

1. **Rule set control.** We extend beyond Default Setup by running the
   `security-and-quality` query pack (line 89 of `build.yml`), which surfaces
   correctness queries Default Setup omits.
2. **Query exclusions.** We exclude `py/log-injection` repository-wide (custom
   runtime sanitizer in `backend/log_utils.py` makes the static-analysis flow
   model wrong for our code) and `py/unused-global-variable` for
   `backend/alembic/versions/**` only (Alembic reads module-level names by
   runtime introspection — see PR #110, alerts 1466-1469 dismissed as
   false-positive). Default Setup does not support `query-filters` at all.
3. **Language matrix control.** We pin to `['javascript-typescript', 'python']`
   — Default Setup's auto-language detection currently expands to five
   languages including `actions`, `javascript`, and `typescript` (see API
   output in the verification command below), most of which would either
   double-scan or scan irrelevant content.
4. **Delta-zero enforcement at PR time.** The custom job runs an in-workflow
   shell step (`Enforce CodeQL delta-zero`, lines 107-251 of `build.yml`)
   that fails any PR introducing a new HIGH or CRITICAL alert. The
   GitHub-UI "Code scanning merge protection rules" feature is documented to
   require Default Setup. We re-implemented equivalent enforcement in the
   workflow so we can keep the custom config (PR #108).

## How to add or remove a query rule

All rule changes go through `.github/codeql/codeql-config.yml`. Do not edit
the workflow for rule selection — only for trigger conditions, language
matrix, or action versions.

### Adding a new query exclusion (false positive that recurs)

1. **Confirm it's a true false positive**, not a real finding. If a runtime
   sanitizer is the justification, identify the test that proves the
   sanitizer fires for the relevant input class (ADR-005 Phase 1 dismissal
   policy item 2, sub-case "sanitized upstream").
2. **Decide the scope** — repository-wide (like `py/log-injection`) or
   path-scoped (like the Alembic exclusion).
3. **Edit `.github/codeql/codeql-config.yml`** under `query-filters`:
   ```yaml
   - exclude:
       id: <query-id>
       # paths:                       # only if path-scoped
       #   - <glob/relative/to/repo>
   ```
4. **Add an in-file comment** above the exclusion explaining: the query, the
   runtime mitigation (with file reference), and the test that proves the
   mitigation fires. Comment-less exclusions get reverted in code review.
5. **Open a PR.** Per ADR-005 Phase 1 policy item 4, config-level exclusions
   require Security Engineer review (architectural exclusion is stricter
   than per-alert dismissal). For path-scoped exclusions on framework files
   (Alembic, generated migrations, etc.), reference the original alerts and
   the dismissal record — see the Alembic comment in the config file as the
   model.

### Removing an exclusion

The reverse of the above. Remove the entry, expect the previously-suppressed
alerts to re-appear on the next scan, and either remediate them or the
removal is wrong.

### Changing the query suite

`security-and-quality` is set at `build.yml:89`. Changing the suite
(e.g. to `security-extended` or back to `default`) is an ADR-level
decision per ADR-005 "Out of Scope" item: "CodeQL query-set tuning ... is
a Security Engineer decision outside this ADR's scope." File a new ADR or
addendum.

### Changing the language matrix

Edit `build.yml` line 80 (`matrix.language`). Note: the matrix expands to
two check-runs (`CodeQL Analysis (python)` and `CodeQL Analysis
(javascript-typescript)`), both of which are required status checks on
`dev` and `main` branch protection (ADR-005 Implementation Sketch item 2).
Adding a language adds a required check; removing one strands the
branch-protection entry. Coordinate with the repo admin on branch
protection updates.

## Verifying no Default-Setup drift

Run this anytime you need to confirm Default Setup hasn't been silently
enabled (e.g. after a GitHub-side org rollout, or as part of a security
audit):

```bash
gh api /repos/MotWakorb/enhancedchannelmanager/code-scanning/default-setup
```

Expected output (truncated):

```json
{"state":"not-configured", ...}
```

The `state` field has three values to recognize:

| State | Meaning | Action |
|-|-|-|
| `not-configured` | Default Setup never enabled, or explicitly disabled | OK — no drift |
| `configured` | Default Setup is enabled in parallel with the custom workflow | **Drift — disable it** (Settings → Code security → Code scanning → Default setup → Disable) |
| `errored` | Default Setup attempted to configure and failed | Investigate; usually safe but log it |

**Cross-check via analyses endpoint** — every analysis on the repo should
have `analysis_key = ".github/workflows/build.yml:codeql-analysis"`. If any
analysis surfaces with a different `analysis_key` (e.g. `dynamic/github/codeql/...`
indicating Default Setup), that's drift:

```bash
gh api '/repos/MotWakorb/enhancedchannelmanager/code-scanning/analyses?per_page=100' \
  | jq '[.[] | .analysis_key] | unique'
```

Expected output:

```json
[
  ".github/workflows/build.yml:codeql-analysis"
]
```

A second entry in the array is drift.

## Related references

- [ADR-005: Code Security Gating Strategy](../adr/ADR-005-code-security-gating-strategy.md) — gating policy, dismissal categories, sequencing. Open Question 4 is the canonical decision to keep custom over Default Setup
- [`.github/workflows/build.yml`](../../.github/workflows/build.yml) — workflow (job `codeql-analysis`)
- [`.github/codeql/codeql-config.yml`](../../.github/codeql/codeql-config.yml) — query exclusions
- [`backend/log_utils.py`](../../backend/log_utils.py) — runtime sanitizer justifying the `py/log-injection` exclusion
- PR #108 — workflow-level delta-zero enforcement (substitute for UI merge protection rules)
- PR #110 — Alembic `py/unused-global-variable` exclusion (bd-877dw)
- Bead `enhancedchannelmanager-bsbr3` — investigation that produced this document
