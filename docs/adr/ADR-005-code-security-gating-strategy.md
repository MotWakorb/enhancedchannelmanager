# ADR-005: Code Security Gating Strategy (CodeQL Delta-Zero)

- **Status**: Accepted
- **Date**: 2026-04-21 (proposed) / 2026-04-21 (accepted)
- **Author**: IT Architect persona (on behalf of PO)
- **Bead**: `enhancedchannelmanager-sm3n3`
- **Related**:
  - `enhancedchannelmanager-xnqgo` — ADR-001 (dependency-upgrade validation gate; subsumed for CodeQL specifically)
  - `enhancedchannelmanager-4lk1q` — ADR-004 (release-cut promotion discipline; complementary backstop)
  - `enhancedchannelmanager-0a1pr` — concrete example: 4 HIGH py/path-injection alerts (CodeQL 1416-1419, `backend/routers/backup.py`) that landed on `dev` and were only surfaced at the next PR-to-`main`

## Context

PR #82 attempted a `dev`→`main` promotion and was blocked by 4 HIGH-severity py/path-injection CodeQL findings (alerts 1416-1419) in `backend/routers/backup.py`. The findings had already been merged to `dev` across multiple earlier PRs; nobody saw them until the `main`-bound aggregate check ran. The PR was delayed by the remediation work that should have happened at the original PR's review time.

This ADR addresses the architectural gap that PR #82 exposed.

**Observed current state** (`.github/workflows/build.yml`, verified 2026-04-21):

1. **CodeQL job `if:` gate** (line 72):
   `(push && ref == refs/heads/main) || (pull_request && base_ref == main)`
   So CodeQL fires on:
   - ✅ Push to `main`
   - ✅ PR to `main`
   - ❌ Push to `dev` (job is skipped, not just absent)
   - ❌ PR to `dev` (job is skipped)
   - ❌ PR to any other branch
2. **Main branch protection** (per `sm3n3`): requires only `Backend Tests` + `Frontend Tests`. The CodeQL check is visible on main-bound PRs but **not a required status check** — a PR to `main` with HIGH CodeQL findings can theoretically be admin-merged.
3. **ADR-001** accepted 2026-04-20 proposed a `paths-filter`-scoped extension of `build-amd64`, `dast-scan`, and `trivy-scan` to dep-bump PRs targeting `dev`. Implementation hasn't landed yet (no paths-filter in `build.yml` as of commit `5494d449`). ADR-001's scope did not explicitly include CodeQL.
4. **CodeQL config** (`.github/codeql/codeql-config.yml`): `security-and-quality` suite, with `py/log-injection` excluded (runtime sanitizer in `backend/log_utils.py` handles CWE-117). This is the source of truth for what counts as a finding.

**The actual gap** is that **code security findings (CodeQL) land on `dev` without any PR-time signal** and surface only when the next promotion to `main` attempts to merge an aggregate of them. This is the same class of gap ADR-001 solved for dependency-upgrade PRs, but ADR-001's scope is narrower (dep-bump manifest changes only) and excludes CodeQL.

So the architectural question is: **should CodeQL gate ALL PRs (at least PR-to-`dev`), and should it be a required status check on `main` branch protection?**

## Decision

**Adopt the combination of Option 1 + Option 4** — extend CodeQL scope AND enforce delta-zero at merge — **sequenced** (Option 1 → dirty-base remediation → Option 4; see Sequencing & Rollout below):

1. **Extend `codeql-analysis` job trigger** from `(push-to-main OR PR-to-main)` to `(push-to-main OR push-to-dev OR PR-to-dev OR PR-to-main)`. CodeQL runs on every PR targeting either long-lived branch, plus on merge to either branch for baseline maintenance. No paths-filter — we accept the ~2 minute CI cost per PR as the price of closing the silent-finding window.

2. **Enforce delta-zero via GitHub Code Scanning merge protection rules** (Settings → Code security → Code scanning → Protection rules), configured to fail any PR that introduces a new HIGH or CRITICAL alert on either `dev` or `main`. This is the native mechanism that actually enforces "no new findings." Required status checks alone enforce "CodeQL completed," not "CodeQL found nothing new"; merge protection rules are the mechanism that enforces delta-zero.

3. **Add both matrix check-runs as required status checks** on `dev` and `main` branch protection: `CodeQL Analysis (python)` and `CodeQL Analysis (javascript-typescript)`. The matrix-strategy job emits one check-run per language; both must be required so branch protection enforces "CodeQL completed on both languages." (Required status checks guarantee the scan *ran*; Code Scanning merge protection rules guarantee its findings were clean. Both gates are needed.)

4. **Disable "Allow administrators to bypass required status checks"** on both branch protection rules. Without this, admin-merge re-opens the loophole the gate is meant to close.

Mechanism: workflow `if:` edit + two branch-protection updates + two Code Scanning merge protection rules + two admin-bypass toggles. No new infrastructure, no new tooling, no vendor lock-in introduced.

## Alternatives Considered

| # | Option | Pros | Cons | Portability | Cost |
|---|---|---|---|---|---|
| 1 | **Extend CodeQL to all PR-to-`dev` + push-to-`dev`** (gate scope change) | Catches findings at the earliest possible point — the introducing PR, with one author and a small diff. Cheap workflow edit. Consistent with ADR-001's philosophy of "gate at PR time, not at promotion time." | +~2–4 min CI wall-clock per PR. Doubles CodeQL runner minutes across the two long-lived branches. Some pre-existing-alert triage work on first run. | High — pure GitHub Actions | ~negligible; well under free-tier quota at current PR volume |
| 2 | **Advisory-only on PR-to-`dev`, blocking on PR-to-`main`** | Surfaces findings early without adding merge friction on `dev`. Keeps dev velocity high. | Findings still land on `dev` — the PR #82 pattern repeats at the next release cut, just with pre-existing warning. Authorship blur returns because the release-cut PR author isn't the finding's introducer. | High | Same as Option 1 |
| 3 | **Status quo + rely on ADR-004 release-cut gate** | Zero workflow work. Treats the release cut as the single quality gate. | Moves remediation to whichever engineer is cutting the release — the wrong person to fix it, and the wrong time (release blocked on unrelated security work). PR #82 *is* this failure mode — it happened last week. | High | $0 now, high ops/morale cost per occurrence |
| 4 | **Add `CodeQL Analysis` as required status check on `main` branch protection** (gate enforcement) | Turns a visible-but-advisory check into a hard merge block. Closes the "admin-merge past a HIGH finding" loophole. Zero CI work. | Doesn't address the *early detection* gap — a release cut can still stack up findings and be blocked late. Only effective in combination with Option 1 or 2 for early signal. | High | $0 |
| 5 | **Option 1 + Option 4 (chosen)** | Catches findings early AND enforces delta-zero at promotion. Belt-and-suspenders. Each half is cheap; combined they close both the detection gap and the enforcement gap. | Combined cost of Option 1; no new downside beyond that. | High | Same as Option 1 |

## Consequences

### Positive

- **Attribution is fixed**: CodeQL findings are flagged at the PR that introduces them. The engineer who wrote the code reviews the finding, not the release cutter.
- **Post-incident analysis is cheaper**: the introducing PR and author are identified at the moment of introduction, so retros and security postmortems don't need to reconstruct blame from `git log` at release-cut time. "Who introduced this CWE-22?" has a direct answer attached to the alert.
- **Human reviewers gain a second pair of static-analysis eyes inline**: on a large diff, data-flow sinks (traversal, injection, deserialization) are exactly what human reviewers plausibly miss. CodeQL on PR-to-`dev` surfaces them while the diff is still small and fresh.
- **Release cuts are cleaner**: ADR-004's release-cut PR no longer eats aggregate security debt from the intervening `dev` merges. This makes ADR-004 implementable as a discipline, not a crisis ritual.
- **Remediation bead `0a1pr` stops being a special case**: once the gate is enforced, `0a1pr`-class findings either get fixed at introduction or don't land at all.
- **Main branch protection enforces what it publishes**: no admin-merge path past HIGH/CRITICAL findings. The admin-bypass toggle disabled in the Decision block is what makes the gate hold even for repo admins — without it, the gate is advisory to whoever has admin rights.
- **Exit cost is low**: one workflow-file edit + four branch-protection edits (remove two required checks, re-enable admin bypass on two branches) + two merge protection rule deletions. A hard revert takes ~15 minutes.
- **No vendor lock-in**: GitHub Actions + GitHub-hosted CodeQL are both portable (CodeQL is a GitHub-owned tool but the config file and queries are standard SARIF-compatible — off-CodeQL exit path is documented below).

### Negative

- **PR-to-`dev` latency** rises by ~2–4 minutes (CodeQL run time, two languages in parallel). Acceptable; CodeQL already runs on PR-to-`main` in the same runner budget.
- **Pre-existing HIGH/CRITICAL alerts on `dev`** become a one-time triage task. As of 2026-04-21: 4 HIGH alerts outstanding (alerts 1416-1419, tracked in `0a1pr`). These must be remediated or dismissed per the Dismiss-With-Comment Policy before Option 4 turns on. Since won't-fix is not a Phase 1 dismissal category (see policy below), remediation is the only path for confirmed true-positives. **Sequencing: Option 1 first (adds signal), then `0a1pr` remediation, then Option 4 (enforcement).**
- **First-day friction**: engineers will encounter CodeQL feedback on PRs that previously merged without it. Expect 1–2 weeks of calibration on dismiss-with-comment practice (remediate, false-positive-with-evidence, or test-only sink).
- **CodeQL config becomes load-bearing**: `py/log-injection` is already excluded in `.github/codeql/codeql-config.yml`. Any future exclusion must be justified in-file (comment explaining the runtime mitigation, referencing a test that proves the mitigation fires) AND reviewed by the Security Engineer. The config file is now part of the security posture, not a convenience artifact.

### ADR-001 Scope Interaction (Noted Explicitly)

ADR-001's CodeQL coverage was implicit (dep-bump paths-filter, never implemented). **ADR-005 strictly dominates** — Option 1's always-on coverage subsumes anything ADR-001 would have given for CodeQL specifically.

ADR-001's non-CodeQL jobs — `build-amd64`, `dast-scan`, `trivy-scan` — are untouched. They remain governed by ADR-001 and continue to gate only dep-bump PRs to `dev` (plus push-to-`main` and PR-to-`main`) via the paths-filter mechanism ADR-001 specifies. **ADR-005 does not extend those scanners.** If a similar gap appears for Trivy or DAST on general PRs, that's a separate ADR.

This split matters for the engineer implementing either ADR: CodeQL is ADR-005's responsibility; everything else in `build.yml` is ADR-001's.

### Out of Scope (Threat-Model Boundary)

This ADR governs static analysis findings on first-party Python and TypeScript code via CodeQL. It explicitly does **not** cover:

- **Supply-chain / transitive dependency vulnerabilities** — covered by `trivy-scan` (container layer), `pip-audit` (Python deps), and `npm audit` (Node deps). Gated by ADR-001 on dep-bump PRs and by default on PR-to-`main` / push-to-`main`. Delta-zero enforcement from this ADR does **not** extend to those scanners.
- **Secret scanning** — org-level GitHub-native feature, enabled at the org level per standard config. Separate ADR if tightening is needed.
- **Runtime secret handling and credential storage** — threat model belongs to the feature-specific ADR (e.g., ADR-006 for two-instance sync credentials).
- **Container CVEs** — Trivy covers these; unchanged by this ADR.
- **IaC misconfiguration** — Checkov covers Dockerfile and GitHub Actions config; unchanged.
- **Dynamic application security** — DAST / OWASP ZAP baseline runs on PR-to-`main` and push-to-`main`; unchanged.
- **CodeQL query-set tuning** — whether `security-and-quality` is the right query pack is a Security Engineer decision outside this ADR's scope. Additions or removals from the query set require their own ADR or addendum.
- **Non-CodeQL SAST tools** (Semgrep, Bandit, etc.) — not currently in the pipeline. If added, each needs its own gating decision.

Every scanner in the pipeline has its own gating path; this one governs CodeQL.

## Dismiss-With-Comment Policy

Delta-zero enforcement is only workable if there's a defined path for **justified false positives** and **test-code sinks**. The policy:

1. **Dismissal requires a comment** — GitHub's CodeQL UI supports this; the comment becomes part of the alert record and is visible in future audits.

2. **Phase 1 dismissal categories (exactly two):**
   - **False positive** — CodeQL's data-flow model is wrong for this code. The comment must cite the specific flow mismatch.
     - **Sub-case: sanitized upstream.** If the justification is "a sanitizer upstream prevents the flow" (e.g., regex validation, HTML-escape, canonicalization, `Path.resolve().relative_to()`), the dismissal comment must reference an existing test that proves the sanitizer fires for the relevant input class. Reasoning-by-assertion is not acceptable; the test is the evidence. If the test doesn't exist, write it first, then dismiss.
   - **Used in tests** — the sink is test code with no production exposure. The comment must name the test path.

3. **"Won't fix" is NOT a Phase 1 dismissal category.** Risk acceptance for a confirmed true-positive requires a separate path: a Security Engineer-reviewed bead documenting the acceptance, PO sign-off, and the alert stays open. The finding is visible in the scan until it's remediated or the bead closes out the accepted risk explicitly. This is deliberate — Phase 1 has no mechanism to reliably enforce co-signed dismissals, so we do not offer a dismissal class that would depend on one. Re-evaluate for Phase 2 once a co-sign enforcement mechanism exists (e.g., a GitHub Action that reopens won't-fix dismissals lacking a Security-tagged review comment).

4. **Config-level exclusions** (like the current `py/log-injection` exclusion) require an ADR addendum or a new ADR if the exclusion is architectural rather than per-alert. These are stricter than per-alert dismissals and get more review.

5. **No global severity dismissals** — every dismissal is per-alert.

6. **Audit cadence**: the Security Engineer reviews **three audit scopes** on the same cadence — **monthly for the first quarter after Option 4 lands**, then **quarterly thereafter**. The first-quarter cadence catches early-calibration drift before it calcifies; quarterly is the steady state. Audits are filed as beads under `enhancedchannelmanager` so the cadence itself is visible and delinquent audits are themselves a signal.

   ### Scope A — CodeQL dismissal log (original scope)

   Reviews the GitHub Code Scanning dismissed-alerts list. Each audit surfaces:
   - Patterns of reflexive dismissals (same engineer, same rule, short justification).
   - Sanitizer-justified dismissals whose referenced tests have since been deleted or modified.
   - Any unresolved risk-accepted beads (the Phase-1 substitute for "won't fix") that have aged beyond their documented review date.

   Pull command:
   ```bash
   gh api repos/MotWakorb/enhancedchannelmanager/code-scanning/alerts --paginate \
     --jq '.[] | select(.state=="dismissed") | {number, rule: .rule.id, dismissed_by: .dismissed_by.login, dismissed_at, comment: .dismissed_comment, justification: .dismissed_reason}'
   ```

   ### Scope B — PO-downgrade detection (added 2026-04-24, bd-se7ay)

   Reviews release-cut PRs in the audit window for the **G1a-bypass pattern** that ADR-004 §Decision G1a permits ("zero open P0/P1 bugs at the `dev` cut SHA … or each justified in PR"). A downgrade *is* authorized, but a recurring downgrade-immediately-before-cut pattern is a signal that the gate is being culturally weakened.

   Per audit cycle:
   1. Enumerate release-cut PRs merged into `main` in the audit window:
      ```bash
      # For monthly cadence — adjust --search window for quarterly
      gh pr list --base main --state merged --search 'Release in:title merged:>=2026-MM-DD' \
        --json number,title,mergedAt,mergeCommit
      ```
   2. For each release-cut PR, capture the cut SHA (the head of the release branch at merge time) and a 30-day pre-cut window.
   3. Walk the `.beads/issues.jsonl` history (via `git log -p .beads/issues.jsonl` between the window start and the cut SHA) and flag any P1→P2-or-lower transitions whose closing line is `"priority":2` (or higher digit) where the prior line had `"priority":1`. Manual `git log -p` review is acceptable for Phase 1; if the audit becomes recurring noise, file a follow-on bead for a `scripts/bd-priority-history.sh` helper.
   4. Cross-reference each downgrade event with the bead's owner and the release-cut PR author. **Flag** any case where the downgrader is the PO or the cut-PR author and the downgrade landed within 7 days of the cut SHA — that is the tightest signal of pre-cut gate-weakening.
   5. File a separate bead per flagged event (per ADR-004 §Decision item 4); a clean audit closes with no follow-on beads.

   ### Scope C — `allow_force_pushes` flip-event audit (added 2026-04-24, bd-se7ay)

   Reviews `main` (and `dev`) branch protection for unsanctioned `allow_force_pushes` toggles. Every legitimate flip corresponds to an authorized rollback bead (e.g., `bd-vgm4l` for v0.16.0). Anything else is investigated — see ADR-004 §Interaction with ADR-005 item 3 for the underlying TOCTOU concern.

   **GitHub audit-log API constraint.** The `/orgs/{org}/audit-log` endpoint is **org-only**; the `MotWakorb/enhancedchannelmanager` repository is hosted under a personal account, so the audit-log API returns 404 (verified 2026-04-24). The Phase 1 audit therefore uses a **snapshot-diff** strategy instead, captured by `scripts/audit-branch-protection-snapshot.sh`:

   ```bash
   # Capture today's protection state for both long-lived branches.
   scripts/audit-branch-protection-snapshot.sh capture

   # Diff against the previous audit's snapshot (lives under .audit/branch-protection/).
   scripts/audit-branch-protection-snapshot.sh diff
   ```

   The diff alerts on **any change** to the protection JSON since the last audit. Audit-relevant fields the Security Engineer must explicitly inspect even when no diff lines fire:
   - `allow_force_pushes.enabled` — must be `false` at audit time. A `true` value at audit time means a rollback flip-back was missed (TOCTOU realized).
   - `enforce_admins.enabled` — must be `true` (per ADR-005 §Decision item 4 admin-bypass disabled).
   - `required_status_checks.contexts` — must include the four ADR-005 mandates: `Backend Tests`, `Frontend Tests`, `CodeQL Analysis (python)`, `CodeQL Analysis (javascript-typescript)`. A shortened list is a PO-downgrade signal.
   - `allow_deletions.enabled`, `block_creations.enabled`, `required_conversation_resolution.enabled` — record drift even if not currently mandated.

   For each flip detected:
   1. Look for a corresponding rollback bead (search closed beads with the `rollback` or `runbook` label, or `git log` for `runbooks/` activity in the same window).
   2. If a rollback bead exists, the flip is authorized — record the bead reference in the audit bead's comment.
   3. If no bead exists, file a P1 incident bead and page the PO.

   **Phase 2 upgrade path.** If/when the repo migrates to an org or an org-admin PAT is provisioned, the audit can switch to the live audit-log API:
   ```bash
   gh api '/orgs/<org>/audit-log?phrase=action:protected_branch.update+repo:<org>/enhancedchannelmanager' --paginate
   ```
   File a sub-bead under the next audit cycle if PAT provisioning becomes the bottleneck (per bd-se7ay acceptance item 3).

## Sequencing & Rollout

To avoid a bulk-fail state on the first day of Option 4 enforcement, roll out in order:

1. **Week 1** — land Option 1 workflow edit. CodeQL fires on PR-to-`dev` and push-to-`dev`, **advisory** (no merge protection rule, no required status check yet). New findings are visible on every PR but do not block merge. Triage the 4 existing HIGH alerts (`0a1pr`).

2. **Week 2 (early)** — after `0a1pr` is remediated (not dismissed — won't-fix is not available, and the alerts are real true-positives needing the canonicalization pattern already present at `backup.py:164-167`), bulk-triage any other outstanding HIGH/CRITICAL alerts per Open Question 2. Verify `dev` tip has zero outstanding HIGH/CRITICAL alerts. Baseline the dismissal log so Phase 1 policy starts from a known state.

3. **Week 2 (end)** — land Option 4 enforcement in a single coordinated change:
   - Configure Code Scanning merge protection rules: fail PR on new alerts ≥ HIGH on both `dev` and `main`.
   - Add both `CodeQL Analysis (python)` and `CodeQL Analysis (javascript-typescript)` as required status checks on both branch protections.
   - Disable "Allow administrators to bypass required status checks" on both branches.

   Enforcement active from this point.

4. **Week 3 — canary observation window.** The first batch of PRs under enforcement is the canary. **Numeric rollback triggers** — fall back via Soft Exit if ANY of the following fire in the first 10 PRs under enforcement:
   - **≥3 false-positive dismissals across distinct PRs** — indicates either a noisy rule or a systemic sanitizer pattern CodeQL doesn't understand. Investigate before continuing enforcement.
   - **Any single PR blocked >24 hours** on a CodeQL finding that is subsequently classified as false-positive or a correctly dismissed test-only sink.
   - **CodeQL runner time exceeds 8 minutes** (matrix-wide wall clock) on any single run — indicates quota or scan-size pathology outside the tunable parameters of this ADR.

   Exceeding any threshold **revokes Option 4 enforcement** (revert to Option 1 advisory — remove merge protection rules and required-status-check entries) pending root-cause investigation. Option 1 (signal-on) remains in place; the signal is still cheap value. Explicit PO re-approval is required to re-enable enforcement after a rollback.

5. **Week 5+ — steady state.** If Week 3 canary passes, the ADR is considered **in effect** and moves from Proposed to Accepted. First monthly dismissal-log audit fires end of Week 5.

## Implementation Sketch

Three changes, all small:

### 1. Workflow change (`.github/workflows/build.yml`, `codeql-analysis` job, line 72)

Conceptual diff:

```yaml
# codeql-analysis:
#   change:
#     if: (github.event_name == 'push' && github.ref == 'refs/heads/main')
#         || (github.event_name == 'pull_request' && github.base_ref == 'main')
#   to (conceptually, not literal GHA syntax):
#     if: (github.event_name == 'push' && github.ref in ('refs/heads/main', 'refs/heads/dev'))
#         || (github.event_name == 'pull_request' && github.base_ref in ('main', 'dev'))
```

GitHub Actions expressions do not support `in`. The real implementation uses `||` expansion:

```yaml
if: (github.event_name == 'push' && (github.ref == 'refs/heads/main' || github.ref == 'refs/heads/dev'))
    || (github.event_name == 'pull_request' && (github.base_ref == 'main' || github.base_ref == 'dev'))
```

### 2. Branch-protection rules (on both `dev` and `main`)

Add to the required status-checks list:
- `CodeQL Analysis (python)`
- `CodeQL Analysis (javascript-typescript)`

**These are two distinct check-runs, not one.** The `codeql-analysis` job uses a matrix strategy (`language: ['javascript-typescript', 'python']` at `build.yml:79`), so GitHub emits one check-run per matrix value, suffixed with the language in parentheses. Branch protection keys on the exact check-run name — a single `CodeQL Analysis` entry would match neither. Both must be listed.

Also **disable "Allow administrators to bypass required status checks"** on both branch protection rules.

### 3. Code Scanning merge protection rules

In GitHub Settings → Code security → Code scanning → Protection rules, add one rule per branch:
- **`dev`**: fail PR on new alerts at severity ≥ HIGH.
- **`main`**: fail PR on new alerts at severity ≥ HIGH.

This is the **native delta-zero mechanism**. Unlike required status checks, it compares PR alerts against the base branch and fails the PR on a true delta (not just on scan completion). Without this, a required `CodeQL Analysis` check only enforces "the scan ran," which is not the same thing.

Document all three changes in `docs/shipping.md` alongside the ADR-001 additions.

### Out of scope for this ADR

- The workflow edit itself is tracked under a separate engineering bead (to be filed after ADR acceptance).
- ADR-001's `build-amd64` / `dast-scan` / `trivy-scan` path-filter extension is **not** subsumed by this ADR. That remains ADR-001's deliverable.

## Exit Path

If Option 1 + Option 4 prove too noisy or too slow:

1. **Soft exit — Option 1 only**: drop merge protection rules and required-status-check entries on both branches; leave CodeQL firing as advisory. Mechanical steps: **four branch-protection edits** (remove `CodeQL Analysis (python)` and `CodeQL Analysis (javascript-typescript)` from required checks on both `dev` and `main`) + **two merge protection rule deletions** (one per branch) + **two admin-bypass toggles** re-enabled if desired to match prior policy. ~10 minutes.
2. **Medium exit — narrow to Option 2 semantics**: keep CodeQL firing on PR-to-`dev` advisory; enforce merge protection rule only on `main`. One merge protection rule deletion + ADR-005 update. Surfaces findings early but re-introduces the release-cut aggregation pattern for remediation.
3. **Hard exit — revert entirely**: restore original `if:` clause, drop branch-protection rules, delete merge protection rules. ~15 minutes. Falls back to status quo (Option 3), which is what PR #82 exposed — so the hard exit should be a temporary stance while a replacement strategy is drafted.
4. **Off-CodeQL exit** (if GitHub retires free CodeQL for this tier or we migrate off GitHub): CodeQL is GitHub-owned but the `security-and-quality` query pack is openly published; standard portable alternatives are **Semgrep** (open-source SAST) and **Bandit** (Python-specific, already a common transitive dep). Migration cost: replace one job with another; findings format differs (SARIF→tool-native) so the branch-protection rule name changes and the merge protection mechanism becomes tool-specific (may require custom Action to fail the PR on a delta). 1–2 days of engineering work.

## Open Questions (Resolved by PO — 2026-04-21)

1. **Delta-zero enforcement mechanism** — GitHub PR annotations ("new alerts vs. base") from the CodeQL action are *visual only*; the `CodeQL Analysis` check conclusion is neutral/success even when the PR introduces new HIGH alerts. **GitHub Code Scanning merge protection rules** are the native mechanism that actually fails a PR on a new-alert delta. **Resolved in-draft** (reviewer correction from the initial draft): this ADR uses merge protection rules as the delta-zero enforcer, not required status checks alone.
2. **Historic alert amnesty — bulk-triage or `0a1pr`-only?** **Resolved: bulk-triage.** Use `gh api repos/:owner/:repo/code-scanning/alerts` to enumerate HIGH/CRITICAL open alerts, add them to an audit bead, and remediate (or dismiss under Phase 1 categories) before enforcement goes live. Estimated scope is low — only 4 HIGH are surfaced today per `sm3n3`, and `0a1pr` already covers those.
3. **When do we introduce "won't fix" as a dismissal category (Phase 2)?** **Resolved: when mechanical co-sign enforcement exists.** A GitHub Action that reopens any won't-fix alert lacking a Security-tagged review comment within N days is the minimum mechanism. Until that ships, risk acceptance runs through a separate bead-tracked process (see Dismiss-With-Comment Policy item 3). Phase 2 is its own ADR addendum — not a TODO under this ADR.
4. **Interaction with the GitHub-native "CodeQL default setup" — switch from custom workflow to managed setup?** **Resolved: no.** Custom workflow gives explicit control over query set, config exclusions, and timing. Default setup is harder to reason about and harder to exit from.

## References

- Bead `enhancedchannelmanager-sm3n3` — this ADR's tracker (P1)
- Bead `enhancedchannelmanager-0a1pr` — concrete remediation dependency (4 HIGH alerts)
- Bead `enhancedchannelmanager-4lk1q` — ADR-004 (release-cut discipline; complementary)
- Bead `enhancedchannelmanager-xnqgo` — ADR-001 (dep-bump gate; scope overlaps)
- `.github/workflows/build.yml` — workflow under change
- `.github/codeql/codeql-config.yml` — query exclusion source of truth
- `docs/security/codeql-config.md` — operational reference: how to add/remove rules, how to verify no Default-Setup drift (filed under bd-bsbr3)
- `backend/routers/backup.py` — site of the 4 outstanding HIGH alerts
- `backend/log_utils.py` — the runtime sanitizer justifying the `py/log-injection` exclusion
- PR #82 — incident reference (aggregate promotion blocked by pre-existing findings)
