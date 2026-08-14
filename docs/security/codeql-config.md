# CodeQL Configuration: Single Source of Truth

> Operational reference for the CodeQL static analysis pipeline.
> Use this to add/remove rules, audit current configuration, or verify there
> is no drift between the custom workflow and any latent GitHub Default Setup.

- **Owner**: Security Engineer (rule decisions) + Project Engineer (workflow plumbing)
- **Scope**: First-party Python and TypeScript code in this repo
- **Authoritative ADR**: [`docs/adr/ADR-005-code-security-gating-strategy.md`](../adr/ADR-005-code-security-gating-strategy.md): gating policy and dismissal categories
- **Last reviewed**: 2026-05-25 (bd-aqu3f path-scoping investigation)

## Single Source of Truth

There is **exactly one** CodeQL scan configured for this repository:

| Layer | Location | Owns |
|-|-|-|
| **Workflow** | [`.github/workflows/build.yml`](../../.github/workflows/build.yml), job `codeql-analysis` | When CodeQL runs, language matrix, action version, delta-zero gate |
| **Rule config** | [`.github/codeql/codeql-config.yml`](../../.github/codeql/codeql-config.yml) | Query suite, query exclusions (repo-wide only; see §Path-scoping limitation) |

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
   model wrong for our code), `py/unused-global-variable` repository-wide
   (Alembic introspection pattern + global one-shot latch pattern in config.py
   and bandwidth_tracker.py: both produce pervasive false positives; see PR #110,
   alerts 1466-1469, and bd-mqtrq), and `py/weak-sensitive-data-hashing`
   repository-wide (`_settings_hash()` in dispatcharr_client.py derives a
   process-local HMAC-SHA-256 cache key, not a stored password; see bd-jmi1c
   P2-3). All three exclusions are repo-wide; see §Path-scoping limitation
   below for why path-scoped exclusions are not available. Default Setup does not
   support `query-filters` at all.
3. **Language matrix control.** We pin to `['javascript-typescript', 'python']`:
   Default Setup's auto-language detection currently expands to five
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
the workflow for rule selection: only for trigger conditions, language
matrix, or action versions.

### Adding a new query exclusion (false positive that recurs)

1. **Confirm it's a true false positive**, not a real finding. If a runtime
   sanitizer is the justification, identify the test that proves the
   sanitizer fires for the relevant input class (ADR-005 Phase 1 dismissal
   policy item 2, sub-case "sanitized upstream").
2. **Note: all config-level exclusions are repo-wide**: see §Path-scoping
   limitation below. Per-file suppression is not available at the config level;
   if you need to suppress only in a specific file, the only current option is
   per-alert API dismissal (ADR-005 category (b)).
3. **Edit `.github/codeql/codeql-config.yml`** under `query-filters`:
   ```yaml
   - exclude:
       id: <query-id>
   ```
4. **Add an in-file comment** above the exclusion explaining: the query, why
   it is a false positive for ECM specifically, and why repo-wide suppression
   is safe (i.e., confirm no real finding of this type could exist elsewhere
   in the codebase). Comment-less exclusions get reverted in code review.
5. **Open a PR.** Per ADR-005 Phase 1 policy item 4, config-level exclusions
   require Security Engineer review (architectural exclusion is stricter
   than per-alert dismissal). Reference the original alerts and the dismissal
   record; see the existing exclusion comments in the config file as the
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

## Path-scoping limitation (bd-aqu3f, 2026-05-25)

**`query-filters.exclude.paths` is not a supported property in CodeQL action v4.**

The official GitHub docs for customizing advanced setup CodeQL
([customizing-your-advanced-setup-for-code-scanning](https://docs.github.com/en/code-security/code-scanning/creating-an-advanced-setup-for-code-scanning/customizing-your-advanced-setup-for-code-scanning))
document `id` and `tags` as the only valid properties under `query-filters.exclude`.
The `paths:` sub-key has no effect: it is silently ignored by the action.

**Evidence from bd-jmi1c / bd-aqu3f:** A `paths:`-scoped exclusion was added for
`py/weak-sensitive-data-hashing` targeting `backend/dispatcharr_client.py`. The
alert still fired on the next scan. Resolution required per-alert API dismissal,
not a config change. Investigation confirmed that the `py/unused-global-variable`
exclusion for `backend/alembic/versions/**` was also silently repo-wide all along.

**Current state:** All three exclusions in `.github/codeql/codeql-config.yml` are
repo-wide. The `paths:` sub-keys have been removed to reflect actual behavior.

**Workarounds for path-limited suppression (when needed):**

1. **Per-alert API dismissal** (ADR-005 category (b)): dismiss the specific alert
   via the GitHub Code Scanning API with a documented rationale. This is the only
   mechanism that currently works for file-scoped suppression. See bd-jmi1c for
   the dismissal pattern.
2. **Custom QL pack with per-file path filters**: possible via a `.ql` pack that
   wraps the standard query with a path predicate, but requires QL authoring
   expertise and maintenance overhead. Not recommended unless multiple path-scoped
   suppressions of the same rule are needed regularly.
3. **Accept repo-wide suppression** when the false-positive pattern is pervasive
   enough that no real finding of that type could be masked. Document the reasoning
   in the config-file comment per the standard format above.

**Re-check periodically:** CodeQL schema evolves. If a future CodeQL action version
adds `paths:` support under `query-filters.exclude`, this limitation can be revisited.
Check the release notes for `github/codeql-action` and the CodeQL config schema docs
when upgrading the action version.

## Before suppressing: check whether a NAME is the finding

**Try this before reaching for any of the three workarounds above.** None of them
teach CodeQL anything, so all three have to be re-applied for every future
occurrence. Sometimes the alert is caused by an identifier, and renaming it both
removes the alert permanently and leaves the code more accurate.

The sensitive-data queries (`py/clear-text-logging-sensitive-data`,
`py/clear-text-storage-sensitive-data`, and the other CWE-312 family members)
do not detect secrets. They detect *names that look like secrets*. Their shared
heuristic, `shared/concepts/codeql/concepts/internal/SensitiveDataHeuristics.qll`
in `github/codeql`, works in two passes:

1. `maybeSensitiveRegexp()` marks a name as maybe-sensitive. The `secret` class
   is broadly "contains `secret`"; there are sibling classes for passwords
   (`api.?(key|tok)` and friends), account info, certificates and private data.
2. `notSensitiveRegexp()` then *subtracts* names implying the data has already
   been rendered non-sensitive. Its terms include `redact`, `censor`,
   `obfuscate`, `hash`, `md5`, `sha`, `random`, `crypt` and `encode`.

A function whose **definition name** survives both passes is treated as a source
of sensitive data, which means its RETURN VALUE is taint. So a redaction helper
named `mask_secrets` is read as a function that *returns* a secret, and every
caller that logs its output is reported as clear-text logging.

That is exactly what happened on PR #864 (bead `enhancedchannelmanager-9kwzp`):
six HIGH alerts, five in `backend/tls/routes.py` and one in
`backend/tls/renewal.py`, every one of them sourced at the `mask_secrets()` call
inside `backend/tls/redaction.py`. Not one path started at an actual credential.
The fix was to rename the helper to `redact_secrets`. All six alerts went to
`fixed` on the next scan, with no config change, no dismissal and no QL pack.

The tell that this is the mechanism, and not a coincidence, was already in the
data: `redact_secret_values` sits in the same call chain and also contains
`secret`, but contains `redact` too, and it appeared in all six data-flow paths
as an ordinary intermediate step and never as a source.

Two cautions:

- **Only do this when the new name is more truthful, not less.** Renaming a
  function that really does return a credential so that it contains `redact` is
  hiding a finding, and the next reviewer has no way to see it. The test is
  whether you would defend the name with CodeQL out of the picture.
- **The name becomes load-bearing.** Say so at the definition, or a later
  cleanup silently reintroduces the alerts. `redact_secrets` carries that note
  in its docstring, and `TestRedactorNameIsNotClassifiedSensitiveByCodeQL` in
  `backend/tests/test_cloud_upload_security.py` pins it with both regexes
  transcribed, including an assertion that the OLD name would still classify as
  a source so the test cannot pass vacuously.

**How to tell a name-caused alert from a real one.** Read the data-flow path,
not the sink line. The alert JSON from the REST API omits code flows; fetch the
SARIF instead, which contains `codeFlows` for every result:

```bash
# find the analysis for the PR merge ref, then pull its SARIF
gh api "repos/MotWakorb/enhancedchannelmanager/code-scanning/analyses?ref=refs/pull/<PR>/merge" \
  --jq '.[] | "\(.id)  \(.category)  results=\(.results_count)"'
gh api -H "Accept: application/sarif+json" \
  "repos/MotWakorb/enhancedchannelmanager/code-scanning/analyses/<ID>" > analysis.sarif
```

If every path starts at a sanitizer/formatter call rather than at a credential,
the name is the finding. Confirm it at runtime as well before concluding it is a
false positive: drive the sink with a synthetic credential and read the emitted
line, and smoke-test that check by neutering the redaction first, so you know a
clean result means "no leak" and not "broken probe".

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
| `not-configured` | Default Setup never enabled, or explicitly disabled | OK: no drift |
| `configured` | Default Setup is enabled in parallel with the custom workflow | **Drift: disable it** (Settings → Code security → Code scanning → Default setup → Disable) |
| `errored` | Default Setup attempted to configure and failed | Investigate; usually safe but log it |

**Cross-check via analyses endpoint**: every analysis on the repo should
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

- [ADR-005: Code Security Gating Strategy](../adr/ADR-005-code-security-gating-strategy.md): gating policy, dismissal categories, sequencing. Open Question 4 is the canonical decision to keep custom over Default Setup
- [`.github/workflows/build.yml`](../../.github/workflows/build.yml): workflow (job `codeql-analysis`)
- [`.github/codeql/codeql-config.yml`](../../.github/codeql/codeql-config.yml): query exclusions
- [`backend/log_utils.py`](../../backend/log_utils.py): runtime sanitizer justifying the `py/log-injection` exclusion
- PR #108: workflow-level delta-zero enforcement (substitute for UI merge protection rules)
- PR #110: Alembic `py/unused-global-variable` exclusion (bd-877dw)
- Bead `enhancedchannelmanager-bsbr3`: investigation that produced this document
- Bead `enhancedchannelmanager-jmi1c`: Dispatcharr `py/weak-sensitive-data-hashing` exclusion (P2-3 fix-forward)
- Bead `enhancedchannelmanager-aqu3f`: path-scoping limitation investigation (2026-05-25)
- Bead `enhancedchannelmanager-mqtrq`: `py/unused-global-variable` on global latch pattern (config.py)
