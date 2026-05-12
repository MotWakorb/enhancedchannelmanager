# ADR-005 Phase 5 — Monthly CodeQL Dismissal-Log Audit (April 2026)

- **Audit period**: 2026-04-22 (Phase 3 enforcement landed in PR #108, merge `e20d5f6`) → 2026-04-25 (audit run date)
- **Audit cycle**: First monthly audit of the post-Phase-3 enforcement window
- **Auditor**: Security Engineer (persona)
- **Bead**: `enhancedchannelmanager-o8t26`
- **ADR reference**: [ADR-005 §Audit Cadence (item 6)](../adr/ADR-005-code-security-gating-strategy.md)
- **Cadence**: Monthly for the first quarter after Phase 3, then quarterly. Next audit: end of May 2026.

## Executive Summary

- **Overall posture**: **Yellow — discipline mostly clean, one categorical violation found.**
- **Total dismissed alerts (Scope A) in repository**: 27 (audit covers the full dismissal log; the audit window spans the brief life of Phase 3 enforcement).
- **HIGH/CRITICAL dismissals**: 16 (all justified with cited sanitizers + test references).
- **Note-severity dismissals**: 11 (mostly CodeQL intra-file analysis limitations).
- **Discipline issues**: **1 finding (3 alerts)** — alerts #233, #1242, #1243 dismissed under GitHub's `won't fix` UI category, which ADR-005 §Dismiss-With-Comment Policy item 3 explicitly forbids as a Phase 1 dismissal class. All three are note-severity (`py/unused-import`) so blast radius is informational, not security-impacting. Categorized as **discipline issue, not exploitability issue**.
- **Scope B (PO-downgrade detection)**: No release-cut PRs in audit window. Naturally clean.
- **Scope C (branch-protection flip events)**: First baseline snapshot — no prior snapshot to diff against. All audit-relevant fields verified compliant.
- **Compliance rate (Scope A)**: 24/27 dismissals fully compliant with Phase 1 categories = **88.9%**. Remaining 3/27 = 11.1% miscategorized but non-exploitative; remediation is a category re-flag, not a code change.

## Scope A — CodeQL Dismissal Log

### Methodology

```bash
gh api 'repos/MotWakorb/enhancedchannelmanager/code-scanning/alerts?state=dismissed&per_page=100' \
  --paginate \
  --jq '.[] | {number, rule: .rule.id, severity: .rule.severity, security_severity: .rule.security_severity_level, dismissed_by: .dismissed_by.login, dismissed_at, comment: .dismissed_comment, reason: .dismissed_reason, path: .most_recent_instance.location.path}'
```

For each alert: verify the dismissal reason maps to a Phase 1 permitted category, the comment is non-empty and substantive, and (for any release-cut-time dismissals) the PR-description cross-reference exists per [shipping.md §G1b "formally waived" semantics](../shipping.md). No release-cut PRs occurred in the audit window, so the PR-cross-reference half does not bind any alerts in this cycle.

### Phase 1 permitted dismissal categories (per ADR-005 §Dismiss-With-Comment Policy item 2)

1. **False positive (with evidence)** — CodeQL data-flow model is wrong. Comment must cite the flow mismatch; if claiming an upstream sanitizer, comment must reference an existing test that exercises the sanitizer.
2. **Used in tests / test-only sink** — sink is in test code with no production exposure. Comment must name the test path.

**Explicitly NOT permitted in Phase 1**: `won't fix`, risk acceptance for confirmed true-positives (those run through a separate Security-Engineer-reviewed bead per ADR-005 §Dismiss-With-Comment Policy item 3), and any global-severity dismissals.

### Per-alert audit table

Sorted oldest first. Severity column reports `security_severity` (CodeQL HIGH/CRITICAL classification) when present; otherwise the rule severity tier.

| # | Alert | Rule | Severity | Dismissed by | Category (UI) | Phase 1 mapping | Justification (one-liner) | Audit verdict |
|---|---|---|---|---|---|---|---|---|
| 1 | 1416 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Sanitizer upstream — `_BACKUP_FILENAME_RE` allowlist + `Path.resolve().relative_to(BACKUPS_DIR)` containment; test ref `test_backup.py::TestSavedBackupsPathInjection`; admin-only endpoint. | OK |
| 2 | 1417 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1416 (same sanitizer, same test class, sibling sink). | OK |
| 3 | 1418 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1416 (same sanitizer, same test class, sibling sink). | OK |
| 4 | 1419 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1416 (same sanitizer, same test class, sibling sink). | OK |
| 5 | 1461 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same `_BACKUP_FILENAME_RE` + `relative_to` sanitizer family in `backup.py`. | OK |
| 6 | 1462 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same `_BACKUP_FILENAME_RE` + `relative_to` sanitizer family in `backup.py`. | OK |
| 7 | 1354 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | `profile_id` is FastAPI int path param (422 at routing) + `prefix` Pydantic-validated against anchored `FILENAME_RE`; test `test_export.py::test_invalid_filename_prefix_returns_422`. | OK |
| 8 | 1355 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1354 (sibling sink in `export_manager.py`). | OK |
| 9 | 1356 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1354 (sibling sink in `routers/export.py`). | OK |
| 10 | 1357 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1354. | OK |
| 11 | 1358 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1354. | OK |
| 12 | 1359 | py/path-injection | HIGH | MotWakorb | false positive | False positive (with evidence) | Same justification as #1354. | OK |
| 13 | 1404 | js/regex/missing-regexp-anchor | HIGH | MotWakorb | used in tests | Used in tests | Vitest test file `frontend/src/components/autoCreation/ActionEditor.test.tsx:237`; placeholder-attr matcher, not URL validation; not bundled into production. Test path explicitly named in the comment. | OK |
| 14 | 1360 | py/reflective-xss | HIGH | MotWakorb | false positive | False positive (with evidence) | Sanitizer upstream — `_escape_html` (`export.py:1085-1093`) applied to `request.title` at :1235; test ref `test_export.py::TestPrintGuideXSS` (3 tests, PR #100 / `e7a6020e`). | OK |
| 15 | 1361 | py/partial-ssrf | CRITICAL | MotWakorb | false positive | False positive (with evidence) | Sanitizer upstream — `_TENANT_ID_RE` (GUID-or-domain) at adapter + Pydantic at `CloudTarget*Request`; test ref `test_cloud_storage.py::TestOneDriveTenantIdValidator` + API 422 tests (PR #101 / `7d94e982`). | OK |
| 16 | 1362 | py/partial-ssrf | CRITICAL | MotWakorb | false positive | False positive (with evidence) | Sanitizer upstream — `_DRIVE_ID_RE` (base64url 1-128 chars) validates `drive_id` at adapter + Pydantic at `CloudTarget*Request`; test ref `test_cloud_storage.py::TestOneDriveDriveIdValidator` + API 422 tests. | OK |
| 17 | 1466 | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | Alembic framework requires module-level identifiers (`revision`, `down_revision`, `branch_labels`, `depends_on`); `env.py` reads them by introspection. CodeQL query limitation. | OK |
| 18 | 1467 | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | Same Alembic framework justification as #1466. | OK |
| 19 | 1468 | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | Same Alembic framework justification as #1466. | OK |
| 20 | 1469 | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | Same Alembic framework justification as #1466. | OK |
| 21 | 233  | py/unused-import | note | MotWakorb | **won't fix** | **NOT a Phase 1 category** | Side-effect import: registers `@register_method` decorator at module load time. Marked with `# noqa: F401`. Removing breaks alert dispatch (bd-kdsn3). | **DISCIPLINE ISSUE** — see Finding A1 |
| 22 | 1242 | py/unused-import | note | MotWakorb | **won't fix** | **NOT a Phase 1 category** | Same `@register_method` side-effect import justification (alert_methods_*, `backend/main.py`). | **DISCIPLINE ISSUE** — see Finding A1 |
| 23 | 1243 | py/unused-import | note | MotWakorb | **won't fix** | **NOT a Phase 1 category** | Same `@register_method` side-effect import justification. | **DISCIPLINE ISSUE** — see Finding A1 |
| 24 | 1431 | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | `_pragma_logged` is a first-time-only logging guard at `backend/database.py:93/99`; replacement would require Lock+class-state for the same effect. (bd-kdsn3) | OK |
| 25 | 1432 | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | `_last_ready_state` read at `backend/routers/health.py:252`, written at :262 — in-process state machine, not unused. (bd-kdsn3) | OK |
| 26 | 1433 | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | `_fuzzy_helpers` exported and imported by `mcp-server/tools/channels.py:683`; CodeQL intra-file analysis can't follow cross-module import. (bd-kdsn3) | OK |
| 27 | 3    | py/unused-global-variable | note | MotWakorb | false positive | False positive (with evidence) | `_client_settings_hash` is the settings-change-detection cache: read at `backend/dispatcharr_client.py:1052`, written at :1054/:1063. (bd-kdsn3) | OK |

### Compliance rollup

| Reason category (UI label) | Count | Phase 1 permitted? |
|---|---:|---|
| `false positive` | 23 | Yes — maps to "false positive (with evidence)" |
| `used in tests` | 1 | Yes — maps to "test-only sink" |
| `won't fix` | **3** | **No — explicitly forbidden in Phase 1** |
| **Total** | 27 | 24/27 = 88.9% compliant |

### Findings

#### Finding A1 — `won't fix` category used on 3 alerts (discipline issue, P2)
- **Severity**: Informational (the underlying alerts are note-severity `py/unused-import`)
- **Risk Rating**: Low (discipline drift, not exploitability)
- **Category**: Process compliance — Dismiss-With-Comment Policy
- **Framework Reference**: ADR-005 §Dismiss-With-Comment Policy item 3
- **Affected Alerts**: #233, #1242, #1243 (all `py/unused-import` in `backend/main.py`)
- **Description**: Three alerts were dismissed under GitHub's `won't fix` UI reason category. ADR-005 §Dismiss-With-Comment Policy item 3 explicitly forbids `won't fix` as a Phase 1 dismissal category: *"Risk acceptance for a confirmed true-positive requires a separate path: a Security Engineer-reviewed bead documenting the acceptance, PO sign-off, and the alert stays open."* The dismissal happened during the bd-kdsn3 cleanup pass (PR #161, merged 2026-04-24).
- **Evidence**:
  - GitHub Code Scanning shows `dismissed_reason: "won't fix"` for all three alerts.
  - The dismissal comments substantively justify the dismissals correctly — these *are* false-positives (CodeQL doesn't model the `@register_method` side-effect-at-import pattern, which is why the modules carry `# noqa: F401`). The substantive reasoning is a textbook **false positive (with evidence)** — the test/evidence is module-load behavior at startup that registers alert dispatch handlers.
  - PR #161 description (`chore(security): CodeQL note-level cleanup — empty-except + unused + cyclic + unreachable (bd-kdsn3)`) openly tabulates "Dismissed (UI): 3 (side-effect imports)" for `py/unused-import`, indicating the engineer did the dismissal openly — this is a category-selection mistake, not a stealth dismissal.
- **Exploitability**: None. `py/unused-import` is informational hygiene; no code-execution path. The dismissed alerts represent legitimate false-positives in any honest reading.
- **Impact**: Process drift only. If the same category-selection habit propagates to a HIGH/CRITICAL dismissal, the audit would have to flag a real exploitability question without any clear remediation path (since "won't fix" has no co-sign mechanism in Phase 1).
- **Existing Mitigations**: The substantive justifications in the comments are sound; the wrong-category-selection is the only defect. The PR-description tabulation makes the dismissals visible in `git log`.
- **Risk Justification**: Low — the underlying alerts are note-severity, the substantive analysis is correct, and the discipline failure is a category-pick mistake during a bulk cleanup pass. Not Critical/High because no exploitable risk is being concealed; not Informational because the policy violation is real and recurrence on a HIGH alert would be a meaningful gate failure.
- **Remediation**:
  - **Action**: Re-dismiss the three alerts under `false positive` with the same substantive comments (verbatim is fine — the comment text already cites the concrete sanitizer pattern: side-effect-at-import + `# noqa: F401`). Then close the bead.
  - **Effort**: Low (3 UI clicks + status note on a follow-up bead; no code change).
  - **Priority**: Short-term — file as P2 follow-up bead. Not blocking; do before the May audit so the next audit closes cleanly.
  - **Verification**: Re-run the dismissal-log query and confirm zero alerts with `dismissed_reason == "won't fix"`.

### Patterns surveyed (per ADR-005 §Audit Cadence Scope A bullet list)

1. **Reflexive dismissals (same engineer, same rule, short justification)** — All 27 dismissals are by `MotWakorb`. This is expected for a single-developer-led project, not a finding. Justifications are uniformly substantive (cite sanitizer location, test path, or framework-introspection mechanism). No reflexive-dismissal pattern detected.
2. **Sanitizer-justified dismissals whose referenced tests have since been deleted or modified** — Spot-checked the cited test references in the HIGH/CRITICAL dismissals:
   - `test_backup.py::TestSavedBackupsPathInjection` — referenced by alerts #1416-1419, #1461, #1462. Verified to exist (covered under bd-rbmkt / bd-0a1pr remediation, PR #82 follow-on).
   - `test_export.py::test_invalid_filename_prefix_returns_422` — referenced by alerts #1354-1359. Verified to exist.
   - `test_export.py::TestPrintGuideXSS` (PR #100 / `e7a6020e`) — referenced by alert #1360. PR #100 merge cited.
   - `test_cloud_storage.py::TestOneDriveTenantIdValidator` / `TestOneDriveDriveIdValidator` (PR #101 / `7d94e982`) — referenced by alerts #1361, #1362. PR #101 merge cited.
   - **Verdict**: All cited tests/PRs exist. No deleted-test risk in this audit cycle. (Future audits should re-check after major refactors — `test_backup.py` and `test_export.py` are large suites, and a renamed test class would silently invalidate the dismissal evidence.)
3. **Risk-accepted beads (Phase 1 substitute for "won't fix") that have aged past their documented review date** — None filed yet. Phase 1's risk-acceptance bead path has not been exercised in the audit window. If Finding A1 propagates to a HIGH/CRITICAL alert in the future, this gap will need a worked example.

## Scope B — PO-Downgrade Detection (Release-Cut PRs)

### Methodology
Per ADR-005 §Audit Cadence Scope B, enumerate release-cut PRs merged into `main` in the audit window and inspect for P1→P2-or-lower priority transitions whose closing line is within 7 days of the cut SHA, where the downgrader is the PO or the cut-PR author.

```bash
gh pr list --base main --state merged \
  --search 'Release in:title merged:>=2026-03-22' \
  --json number,title,mergedAt,mergeCommit
```

### Findings

**No release-cut PRs in the audit window.** The most recent release-cut to `main` is PR #6 (`v0.7.2: Dispatcharr 0.17 compatibility release`, merged 2026-01-14), and only one PR has merged to `main` since 2026-04-22 — PR #158 (a docs consolidation, not a release cut).

The G1a-bypass pattern cannot exist when no release cuts have happened. **Scope B verdict: vacuously clean — no follow-up action.**

When the next release cut to `main` lands, this audit cycle's report should be re-checked to ensure the Scope B query window covers it.

## Scope C — `allow_force_pushes` Flip-Event Audit

### Methodology
Per ADR-005 §Audit Cadence Scope C, run the snapshot-diff helper:

```bash
scripts/audit-branch-protection-snapshot.sh capture
scripts/audit-branch-protection-snapshot.sh diff
```

### State
- Snapshots present: `.audit/branch-protection/main/2026-04-25.json`, `.audit/branch-protection/dev/2026-04-25.json` (both committed via PR #178 / bd-se7ay as the baseline).
- Diff output: *"main: only one snapshot — nothing to diff yet. dev: only one snapshot — nothing to diff yet. AUDIT: no drift detected."*

This is the **first audit cycle after the snapshot baseline was committed**, so by construction there is no prior snapshot to diff against. The next monthly audit (May 2026) will be the first cycle with a real diff.

### Audit-relevant fields explicitly inspected (per ADR-005 §Scope C)

| Field | `main` value | `dev` value | Required state | Verdict |
|---|---|---|---|---|
| `allow_force_pushes.enabled` | `false` | `false` | `false` (must be) | OK |
| `enforce_admins.enabled` | `true` | `true` | `true` (per ADR-005 §Decision item 4) | OK |
| `required_status_checks.contexts` | `["Backend Tests", "Frontend Tests", "CodeQL Analysis (python)", "CodeQL Analysis (javascript-typescript)"]` | `["Backend Tests", "Frontend Tests", "CodeQL Analysis (python)", "CodeQL Analysis (javascript-typescript)", "Semgrep Lint"]` | Must include the four ADR-005 mandates on both | OK — `dev` carries the additional `Semgrep Lint` check (stricter than required, no shortening) |
| `allow_deletions.enabled` | `false` | `false` | drift-tracked only | OK (no drift to compare) |
| `block_creations.enabled` | `false` | `false` | drift-tracked only | OK |
| `required_conversation_resolution.enabled` | `true` | `false` | drift-tracked only | Noted — divergence between branches is intentional (`main` requires conversation resolution; `dev` does not). Not a finding. |

### Verdict
**Scope C clean.** No flip-events to investigate (no prior snapshot). All four ADR-005 §Decision item 4 mandates are present on both branches. Baseline is established for next month's diff.

## Summary & Recommendations

### Compliance metrics
- **Scope A**: 24/27 dismissals (88.9%) categorically compliant. 3 (11.1%) miscategorized but substantively sound. 0 stealth or reasoning-by-assertion dismissals.
- **Scope B**: Vacuously clean (no release-cut PRs in window).
- **Scope C**: Baseline snapshot established; all fields compliant.

### Discipline issues
1. **Finding A1**: `won't fix` category used on alerts #233, #1242, #1243 — file P2 follow-up bead (see Follow-up Beads below).

### Recommendations
- **Calibration nudge**: When dismissing a CodeQL alert that's a CodeQL-modeling false-positive (cross-module imports, framework introspection, side-effect-at-import patterns), the GitHub UI category to pick is **`false positive`**, not `won't fix`. Phase 1 has exactly two categories; if it's not "test-only sink," it's "false positive." The substantive justification belongs in the comment, not in the category dropdown. Consider adding a line to `docs/security/codeql-config.md` cross-referencing ADR-005's two-category constraint to short-circuit this calibration error.
- **Re-categorization of A1 alerts**: The 3 affected alerts should be re-dismissed under `false positive` with the existing comment text retained verbatim. This is mechanical and should land before the May audit so the recurring-violation question doesn't compound.
- **Test-reference durability check** (forward-looking): The HIGH/CRITICAL dismissals all cite specific test class/method paths. A follow-on enhancement would be a CI check that fails when a referenced test path no longer exists in `git ls-files`. Phase 1 acceptable; consider for Phase 2.
- **Cadence**: Next monthly audit due end of May 2026. After three monthly audits (May, June, July), cadence drops to quarterly per ADR-005 §Audit Cadence item 6.

### Follow-up beads filed
- **enhancedchannelmanager-jy47f** (P2) — Re-dismiss CodeQL alerts #233, #1242, #1243 under `false positive` category to comply with ADR-005 §Dismiss-With-Comment Policy item 3. Comment text retained verbatim. Acceptance: audit query returns zero `won't fix` dismissals before next monthly audit (end of May 2026).

## Audit History

| Date (UTC) | Cycle | Scope A dismissals reviewed | Scope A discipline issues | Scope B release-cut PRs | Scope C drift | Follow-up beads | Auditor |
|---|---|---:|---:|---:|---|---|---|
| 2026-04-25 | First monthly (post-Phase-3) | 27 | 1 finding (3 alerts) | 0 | None (baseline) | 1 (Finding A1) | Security Engineer |
