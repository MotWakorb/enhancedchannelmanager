# ADR-004: Release-Cut Promotion Discipline (dev → main Merge Strategy)

- **Status**: Accepted
- **Date**: 2026-04-21 (proposed) / 2026-04-21 (accepted)
- **Author**: IT Architect persona (on behalf of PO)
- **Bead**: `enhancedchannelmanager-4lk1q`
- **Related**:
  - `enhancedchannelmanager-sm3n3` — ADR-005 (CodeQL delta-zero gating; this ADR relies on ADR-005 as one of the pre-cut gates)
  - `enhancedchannelmanager-xnqgo` — ADR-001 (dep-bump validation gate; complementary)
  - `enhancedchannelmanager-vgm4l` — 0.16.0 rollback bead (exemplar failure mode this ADR is designed to prevent)
  - `enhancedchannelmanager-mghjm` — the 0.16.0 release that was rolled back
  - `enhancedchannelmanager-8w33i` — main branch protection config

## Context

Two recent incidents surfaced the same architectural gap — there is no explicit release-cut discipline for `dev`→`main` promotions:

1. **v0.16.0 rollback** (2026-04-20, `bd-vgm4l`). 0.16.0 was tagged, released on GitHub, and pushed to GHCR. The same day, the PO authorized a hard rollback because **open P0/P1 bugs the PO wanted cleared first had been silently included in the cut**. No pre-cut gate existed that would have blocked the release on outstanding bug status. The rollback was destructive but safe (no external consumer had pulled yet) and is now `docs/runbooks/v0.16.0-rollback.md`. Cost: one yanked release, one runbook-writing day, one bead hierarchy re-plumbed.

2. **PR #82 scope sprawl** (the ADR-001 doc PR). A small documentation PR targeting `main` swept in **90 unrelated commits from `dev` (38+ feature merges since v0.15.2)** as a side effect of the merge. The PR was architecturally a doc change; operationally, it was an unintentional release cut of everything currently on `dev`. There was no mechanism to prevent this — `main` simply accepts whatever the PR author's branch pulls in, and "small PR" was not a scope signal the pipeline respected.

Both incidents share a root cause: **`dev`→`main` merges happen as side effects of whatever PR next targets `main`, rather than as intentional release cuts with explicit scope, gate, and approval.**

**Observed current state:**

1. **`shipping.md` Release Workflow** (lines 74-86) prescribes merge-commit from a temporary worktree (`git merge dev --no-edit`) for release cuts, plus version bump + GHCR retag. Useful but silent on: *when* a cut is allowed, *what* must be clean first, *how* to prevent unintentional cuts (the PR #82 pattern).
2. **Main branch protection** (`bd-8w33i`): requires `Backend Tests` + `Frontend Tests`; admins not enforced (the PO can push hotfixes directly); force-push blocked; conversation resolution required; linear history not required; PR reviews not required. ADR-005 adds CodeQL delta-zero enforcement on top of this once implemented.
3. **CHANGELOG.md** follows Keep-a-Changelog with `[Unreleased]` promoted to `[X.Y.Z]` on cut. The convention is documented in `shipping.md:45` but is not mechanically enforced; a cut without updating CHANGELOG is not currently blocked.
4. **Version convention**: `0.X.Y-NNNN` on dev (build-number suffix per `shipping.md:28`); `X.Y.Z` on release (bumped as part of the cut).
5. **Rollback runbook** (`docs/runbooks/v0.16.0-rollback.md`) exists as first exemplar; its Option 1 (admin-bypass) will be unavailable once ADR-005 disables the bypass toggle, forcing Option 2 (temporarily flip `allow_force_pushes`). This ADR must acknowledge that interaction.

So the architectural question is: **how do we make `dev`→`main` promotion an intentional, gated, scoped act rather than an emergent side effect of whatever merges next?**

## Decision

**Adopt a short-lived release-branch + merge-commit PR pattern, with an enforced pre-cut gate and an explicit hotfix path as the only exceptions.** Four components:

### 1. Release cuts go through a short-lived release branch

For each cut:
- Cut `release/vX.Y.Z` from a specific `dev` SHA (the cut point, chosen by the PO).
- The release branch carries **only release-adjacent changes**: version bump in `frontend/package.json` (from `0.X.Y-NNNN` to `X.Y.Z`), CHANGELOG promotion (`[Unreleased]` → `[X.Y.Z] — YYYY-MM-DD` with a fresh empty `[Unreleased]` above), and no other code changes. Stabilization fixes targeted at the release window land as PRs into the release branch (not into `dev`), then are merged back to `dev` after the cut.
- The release branch has a short life: from cut decision to merge, ideally same day, maximum one business week. Long-lived release branches are explicitly forbidden (that's git-flow — see Alternative 1).

### 2. `dev`→`main` happens via a single release-cut PR, merge-commit not squash

- The release-cut PR merges `release/vX.Y.Z` into `main` as a **merge commit** (`--no-ff`), preserving the individual `dev`-origin commits for bisection and attribution. Squash-merge is rejected for release cuts because it collapses per-commit blame and invalidates `git log` as a release-notes source.
- The PR title MUST match `Release vX.Y.Z` (exact prefix) — used by tooling and reviewers as the scope signal.
- The PR description MUST include the promoted `[X.Y.Z]` CHANGELOG block as its top section.

### 3. Pre-cut gate (required — all must pass before the release-cut PR can merge)

| # | Gate | Enforcement | Cites |
|---|---|---|---|
| G1a | **Zero open P0/P1 bugs at the `dev` cut SHA** (beads board, all scopes) | Manual verification by cut-authorizer; checklist item in the release-cut PR description | `bd-vgm4l` root cause |
| G1b | **Zero open HIGH/CRITICAL security findings not formally waived** (GitHub Security tab + active advisories) — distinct from G1a so a mis-triaged finding cannot slip through "the bug board is clean" | Manual verification at Phase 1 via `gh api .../code-scanning/alerts` + Security tab review; mechanical at Phase 2 | Complement to ADR-005 gate G4 |
| G2 | `Backend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G3 | `Frontend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G4 | **CodeQL delta-zero vs. `main` base** (both matrix check-runs). The delta is computed between the release-cut PR head and `main`, **not** against the release-branch cut SHA — the release branch's own base is transparent to GitHub's merge protection rule, which compares the incoming head to the target branch. | Code Scanning merge protection rule + branch protection required checks | ADR-005 |
| G5 | `CHANGELOG.md` `[Unreleased]` has been promoted to `[X.Y.Z]` with today's date and a fresh empty `[Unreleased]` above | Manual in PR review; Phase 2 CI check possible | `shipping.md:45` |
| G6 | Version updated in `frontend/package.json` from the current `0.A.B-NNNN` dev build to the target release version `X.Y.Z`. The target is not necessarily `A.B` with the suffix stripped — a minor or patch bump is permitted (e.g., current dev tip `0.16.0-0041` → release `0.17.0`) — but must match the release-branch name (`release/vX.Y.Z`) | Manual in PR review; trivially automatable | `shipping.md:28` |
| G7 | **No other release-cut or hotfix PR targeting `main` is open at merge time.** If a hotfix PR and a release-cut PR contend simultaneously, the **hotfix has priority**: the release-cut PR rebases on the merged hotfix and re-runs the gate. Prevents live-lock during an incident. | Manual verification; one-line `gh pr list --base main --state open --json number,title` | PR #82 root cause |

G1a, G1b, G5, G6, G7 are author/reviewer-verified at Phase 1 and can be lifted to CI at Phase 2 (scope of a follow-on bead, not this ADR). G2, G3, G4 are mechanically enforced from day one.

### 4. Non-release PRs to `main` are forbidden, with one exception

- **The rule**: any PR targeting `main` that is not a release-cut PR is closed without merge and retargeted to `dev`. Documentation, config tweaks, dep bumps, and feature work all flow through `dev` and reach `main` only via the next release cut. This closes the PR #82 pattern.
- **The exception — hotfix path**: genuine production-blocking bugs, critical security advisories, or GHCR/branch-protection emergencies can land as a **hotfix PR** branched directly from `main` (not from `dev`):
  - Branch name: `hotfix/vX.Y.(Z+1)-description` (patch version increment only).
  - Scope: minimal — one bug or one advisory per hotfix; no opportunistic cleanup.
  - Must still pass G2, G3, G4 (tests + CodeQL).
  - **G4 re-attribution waiver (security-driven hotfixes).** If a hotfix trips CodeQL delta-zero *solely* because its touched code is near a pre-existing `main` finding that CodeQL re-attributes to the new commit, the hotfix author may waive G4 with a linked Security-tab dismissal rationale recorded in the PR description. Genuinely new HIGH/CRITICAL findings introduced by the hotfix code cannot be waived. Without this carve-out, a security hotfix can be blocked by the very gate meant to protect the release.
  - G1a (P0/P1 count) applies only to bugs *regressed or introduced* by the hotfix, not to pre-existing P0/P1s on `dev` (those are being bypassed intentionally because the hotfix is more urgent).
  - G1b (HIGH/CRITICAL security findings) applies to the hotfix's own introduced findings only — the hotfix is often the *remediation* of a pre-existing finding, which must not block itself.
  - G5 (CHANGELOG) applies with a hotfix-scoped entry.
  - G6 (version) applies as a patch bump.
  - G7 (no other main-bound PRs) applies, with hotfix-has-priority tiebreaker.
  - After merge to `main`, the hotfix is **merged** (not cherry-picked) back to `dev` as a follow-up PR within 24 hours so `dev` does not drift. Merge preserves the hotfix commit chain in `dev`'s history and keeps bisection symmetric with the release-cut pattern.
- Hotfixes should be **rare**. Every hotfix is a signal that the pre-cut gate failed — file a retro bead for each.
- **Mechanical hotfix ceiling.** If more than **two hotfixes** land between consecutive release cuts, a **mandatory incident review bead** must be filed and landed before the next release cut can proceed. This converts the cultural "rare" signal into a gate: the review forces root-cause analysis of why the pre-cut gate failed repeatedly, and prevents the `hotfix/*` branch from becoming a de facto replacement for the release-cut PR (the PR #82 pattern with different branch names).

## Alternatives Considered

| # | Option | Pros | Cons | Portability | Cost |
|---|---|---|---|---|---|
| 1 | **Full git-flow with long-lived release branches** (maintained for each minor version) | Supports parallel release stabilization; enterprise-familiar; allows backporting fixes to older releases | Heavy overhead for a single-maintainer project; release branches calcify and accumulate drift; currently zero backport demand | High | Significant — branch hygiene ops, policy for EOL of old release branches |
| 2 | **Squash-merge release-cut PR from `dev`→`main`** | Clean main history; one commit per release is easy to find | **Destroys bisection granularity** — a release that regresses cannot be `git bisect`'d to a single `dev`-origin commit. Breaks the existing `git log` → release-notes workflow. Worsens postmortem attribution. | High | Low to implement, high to live with |
| 3 | **Trunk-based with tags off `main` (main tracks dev closely)** | No cut mechanics — just tag the dev tip; zero branch overhead | **No stabilization window** — a tagged release with a P1 bug has no release branch to absorb the fix; any fix requires a forward release. The 0.16.0 rollback need would not have existed (good), but any in-release bug would have forced an immediate patch release (bad for a small team) | High | Low to implement; potentially high incident cost |
| 4 | **Status quo + documented conventions only** | Zero mechanical work | **Does not close either failure mode.** Nothing prevents the next PR #82 scope sprawl; nothing blocks a future 0.16.0-style cut with open P0/P1s. The conventions exist already in `shipping.md` — the incidents happened despite them. | High | $0 now, high incident cost repeated |
| 5 | **Short-lived release branch + merge-commit PR + pre-cut gate (chosen)** | Intentional cut point (branch cut SHA); stabilization window via branch; preserves dev commit history in main for bisection; scope clarity via dedicated PR; pre-cut gate mechanically closes the 0.16.0 failure mode; non-release-PR ban closes the PR #82 failure mode | +1 git operation per release (branch cut + branch delete); requires the PO or authorized persona to follow the cut checklist; adds conceptual weight compared to status quo | High — pure git + GitHub Actions | ~5 minutes per release cut; no new infrastructure |

## Consequences

### Positive

- **Release cuts are intentional, not emergent.** The act of creating `release/vX.Y.Z` is the authorizer saying "this SHA is the cut." No more accidental cuts via scope-sprawl PRs.
- **P0/P1 bug gate is auditable, not cultural.** Gates G1a (bug board) and G1b (security findings) are checklist items — skipping one requires an explicit unchecked-or-falsely-checked box in the PR description. The 0.16.0 failure mode becomes harder to reproduce without a visible lie in the PR record. The real defense here is *auditability*, not prevention; Phase 2 automation (`bd` in CI) closes the remaining honor-system gap.
- **Bisection stays cheap AND security forensics improve.** Merge-commit preserves dev-origin commits in `main`'s first-parent chain, so `git bisect --first-parent` works on main and full bisection works on the merge-commit side. Beyond bisection, this materially improves security incident response: CVE attribution, dependency-introduction tracing, and exploit-window determination all rely on per-commit granularity surviving into `main`. Squash-merge would have collapsed all of this.
- **Scope is legible.** A release-cut PR is titled `Release vX.Y.Z` with the promoted CHANGELOG block as the top of its description. Any reviewer, future engineer, or auditor can see what shipped in a release without running `git log`.
- **Hotfix path is bounded.** Hotfixes are small, explicit, same-gate-except-G1, and must flow back to `dev` within 24h. This prevents hotfix drift (main diverging silently from dev via a series of direct hotfixes).
- **Rollback remains tractable.** `docs/runbooks/v0.16.0-rollback.md` continues to work under this ADR. The runbook's Option 1 (admin-bypass) becomes unavailable after ADR-005 lands; Option 2 (temporarily flip `allow_force_pushes`) becomes the default path. Documented as an ADR-005 interaction (see below).
- **Interaction with ADR-005**: CodeQL delta-zero (gate G4) becomes a mechanical pre-cut gate rather than a best-effort hope. No release cut can merge while CodeQL has new HIGH/CRITICAL findings introduced by the cut's content — which was exactly the PR #82 situation.

### Negative

- **Extra git ceremony per release.** Cutting a release branch, promoting CHANGELOG, bumping version, opening the PR, checking gate items — all of this is currently done ad-hoc via `shipping.md`. This ADR formalizes it, which adds ~5 minutes per cut. Acceptable; cuts are infrequent.
- **Stabilization fixes must target the release branch, not `dev`.** This is a discipline shift. If a release-branch-only fix is needed, it's a small PR to `release/vX.Y.Z`, and post-cut it gets merged back into `dev`. Engineers unfamiliar with this pattern will initially target `dev` out of habit; expect 1-2 weeks of calibration.
- **Hotfix rarity depends on cultural buy-in.** The architecture cannot stop an authorized admin from creating a hotfix PR for every bug — only the "every hotfix needs a retro bead" policy disincentivizes that. If hotfixes proliferate, the pre-cut gate has effectively been bypassed and we're back to the PR #82 pattern with different branch names. SRE and PM should watch hotfix-to-release ratio and escalate if it exceeds ~1 hotfix per release on average.
- **ADR-005 admin-bypass-off changes rollback mechanics.** The v0.16.0-rollback runbook currently prefers Option 1 (admin-bypass) for the force-push reset of `main`. Once ADR-005 disables admin-bypass, Option 1 is unavailable and Option 2 (temporarily flip `allow_force_pushes: true`, reset, flip back) becomes the default. **Option 2 is a TOCTOU-shaped operational risk** — if the engineer forgets to flip `allow_force_pushes` back, or a CI-triggered push races during the window, main's protection is silently degraded. Required follow-on runbook edits (see "Interaction with ADR-005" below): add a post-action verification step that `gh api` returns `allow_force_pushes.enabled: false`; consider a short-TTL wrapper script that auto-reverts regardless of outcome; and SRE should periodically review the repo audit log for unsanctioned `allow_force_pushes` flips.
- **Linear-history-not-required remains true.** This ADR keeps the current merge-commit workflow, so `main`'s history stays non-linear. If a future ADR decides to enforce linear history, the cut mechanism would need to switch from merge-commit to rebase-merge (not squash-merge — that breaks bisection as noted). Out of scope here.

### Out of Scope (Boundary)

This ADR governs how code and docs flow from `dev` to `main`. It explicitly does **not** cover:

- **Release cadence** — when/how often cuts happen is a PM/PO concern, not this ADR. We document a mechanism, not a schedule.
- **Feature flag strategy** — if scope needs to be held out of a cut at SHA granularity, that's a feature-flag ADR (not currently filed; propose if the need arises).
- **Backporting to older releases** — single-supported-version project. Not supported. If this changes, revisit with Alternative 1 (git-flow).
- **Release announcements / Discord / external comms** — `docs/discord_release_notes.md` covers this; this ADR is silent.
- **GHCR tag management and image retention** — governed by `shipping.md` and the `.github/workflows/build.yml` multi-arch manifest flow.
- **Rollback procedure** — `docs/runbooks/v0.16.0-rollback.md` is the source of truth. This ADR only notes the ADR-005 interaction.
- **Automating CHANGELOG promotion, version bumps, or gate enforcement** — Phase 2, filed as a separate engineering bead after this ADR is accepted.

## Cut Mechanics (Step-by-Step)

For reference — this goes into `shipping.md` on acceptance.

```bash
# 0. Pre-flight — gate items G1a, G1b, G7 (human checks)
bd list --status open --priority 0                # G1a: must be empty
bd list --status open --priority 1                # G1a: must be empty (or each justified in PR)
gh api repos/:owner/:repo/code-scanning/alerts --paginate \
  | jq '[.[] | select(.state=="open" and (.rule.security_severity_level=="high" or .rule.security_severity_level=="critical"))] | length'
                                                   # G1b: must be 0 (or each formally waived)
gh pr list --base main --state open --json number,title
                                                   # G7: must be empty (or only a hotfix PR with priority)

# 1. Cut the release branch from the chosen dev SHA
git fetch origin
git checkout -b release/v0.17.0 <dev-cut-sha>

# 2. Bump version
# Edit frontend/package.json: "version": "0.17.0" (target release version per G6)
cd frontend && npm run build                      # validates the bump
cd ..

# 3. Promote CHANGELOG
# Edit CHANGELOG.md:
#   - Rename [Unreleased] heading to "[0.17.0] — 2026-MM-DD"
#   - Insert a fresh empty [Unreleased] section above it

# 4. Commit on release branch
git add frontend/package.json CHANGELOG.md
git commit -m "Release v0.17.0"
git push -u origin release/v0.17.0

# 5. Open the release-cut PR — capture the PR number for steps 6 & 7
# Replace the <paste ...> placeholder with the promoted CHANGELOG [0.17.0] block before running.
PR_URL=$(gh pr create --base main --head release/v0.17.0 \
  --title "Release v0.17.0" \
  --body "$(cat <<'EOF'
## Release v0.17.0

<paste the promoted CHANGELOG [0.17.0] block here>

### Pre-Cut Gate Checklist
- [ ] G1a: Zero open P0/P1 bugs at cut SHA (verified via `bd list`)
- [ ] G1b: Zero open HIGH/CRITICAL security findings not formally waived (GitHub Security tab)
- [x] G2: Backend Tests green (CI will verify)
- [x] G3: Frontend Tests green (CI will verify)
- [x] G4: CodeQL delta-zero vs. `main` (CI will verify via Code Scanning merge protection rule)
- [ ] G5: CHANGELOG [Unreleased] promoted to [0.17.0] with today's date, fresh empty [Unreleased] above
- [ ] G6: Version in frontend/package.json matches `0.17.0` (release branch name)
- [ ] G7: No other release-cut or hotfix PR targeting main is open
EOF
)")
PR_NUM="${PR_URL##*/}"                             # extract trailing number from gh pr create URL
echo "Release PR: $PR_URL (#$PR_NUM)"

# 6. Wait for CI green on all required checks, confirm all gate items, then merge
# --merge produces a merge commit (not --squash or --rebase); preserves per-commit bisection/forensics.
gh pr merge "$PR_NUM" --merge --delete-branch

# 7. Tag and release
# Use annotated tag (-a) so `git describe` and `git log --decorate` carry author/date metadata.
git checkout main && git pull
git tag -a v0.17.0 -m "Release v0.17.0"
git push origin v0.17.0
gh release create v0.17.0 --target main --title "v0.17.0" \
  --notes-file <(gh pr view "$PR_NUM" --json body -q .body)

# 8. Back-merge release branch into dev (catches CHANGELOG/version bump)
# Expected to be conflict-free if only version + CHANGELOG changed on the release branch.
# If stabilization fixes landed on the release branch, resolve any dev conflicts here.
git checkout dev
git pull
git merge release/v0.17.0 --no-edit
git push origin dev

# 9. Delete the release branch
git branch -d release/v0.17.0
git push origin --delete release/v0.17.0

# 10. Re-open the dev build counter
# After step 8, dev's frontend/package.json is at "0.17.0" (no suffix). Next dev push would
# violate shipping.md:28 convention. Bump dev to the next build-numbered version:
# Edit frontend/package.json: "version": "0.17.1-0000" (or next planned minor's -0000)
cd frontend && npm run build
cd ..
git add frontend/package.json
git commit -m "Bump dev to 0.17.1-0000"
git push origin dev
```

Steps 0 and 5 are the load-bearing discipline. Steps 1-4 and 6-10 are mechanical.

## Sequencing & Rollout

1. **Week 0 — this ADR accepted.** Bead `4lk1q` moves to in-progress; this document becomes authoritative.

2. **Week 1 — `shipping.md` rewrite (single PR).** Replace the current "Release Workflow" section (lines 74-86) with a reference to this ADR plus the Cut Mechanics block above. No behavioral change yet; docs catch up to the decision.

3. **Week 1-2 — first cut under ADR-004.** The next release cut after `shipping.md` is updated is the canary. Execute Cut Mechanics by hand; record each gate item's outcome in the release-cut PR description; file a retro bead if any gate is unclear or needs refinement.

4. **Week 2+ — branch protection teachings.** Once ADR-005 enforcement is active (per its own Week 2-end sequencing), the gate-G4 check is mechanically unavoidable on release-cut PRs. No additional branch protection change is required for ADR-004 specifically — we reuse ADR-005's mechanism.

5. **Month 2+ — Phase 2 automation (separate bead).** If manual gate verification proves flaky, file a follow-on bead to automate G1/G5/G6/G7 via a GitHub Action that reads the PR description checklist and fails if any required gate-item box is unchecked. Not required for ADR-004 acceptance.

**No rollback trigger is specified for this ADR itself** because the observable failure modes of ADR-004 (hotfix proliferation, stabilization-fix-targets-dev-by-habit) take weeks to surface. The SRE/PM should review at the first monthly standup after the first cut and flag if either pattern emerges.

## Implementation Sketch

Three small deliverables, all on the acceptance PR or follow-ons:

### 1. `docs/shipping.md` edits

Replace lines 74-86 ("Release Workflow (Merging to Main)") with a reference to this ADR plus the Cut Mechanics block. The existing "When User Says 'Ship the Fix'" section (lines 3-72) is untouched — that covers *dev* ship cycles, which this ADR does not change.

### 2. Branch protection on `main` — additive

**ADR-004 adds no new required checks beyond those ADR-005 introduces** (`CodeQL Analysis (python)`, `CodeQL Analysis (javascript-typescript)`, plus the "Allow administrators to bypass required status checks" setting being disabled). ADR-004's release-cut gate relies on: (a) manual checklist verification in the PR description (G1a, G1b, G5, G6, G7), and (b) ADR-005's mechanical checks (G2, G3, G4 via required status checks + Code Scanning merge protection rule).

### 3. PR template for release cuts (optional polish)

`.github/PULL_REQUEST_TEMPLATE/release.md` with the gate checklist. Users invoke it via `gh pr create --template release.md`. Not required for ADR-004 to function; nice-to-have for consistency.

### Out of scope for this ADR

- CI-enforced gate automation (Phase 2, separate bead).
- CHANGELOG lint/format enforcement (separate docs-tooling bead if desired).
- Migration of historical main commits into the new pattern (the pattern applies forward from acceptance).

## Interaction with ADR-005

ADR-005 disables "Allow administrators to bypass required status checks" on both `dev` and `main`. That setting is central to the v0.16.0-rollback runbook's Option 1. Once ADR-005 lands, the runbook's rollback choice matrix changes:

- **Before ADR-005**: Option 1 (admin-bypass) is preferred; Option 2 (temporary `allow_force_pushes` flip) is fallback.
- **After ADR-005**: Option 1 is unavailable; Option 2 is the only path.

Required follow-on edits (same PR that updates `shipping.md` for ADR-004):

1. **Reorder runbook options 1 and 2** at `docs/runbooks/v0.16.0-rollback.md`, with Option 2 first and a one-line note citing ADR-005 as the reason.
2. **Harden Option 2 against TOCTOU.** Option 2 flips `allow_force_pushes: true`, performs the destructive action, then flips back. A forgotten flip-back leaves `main` silently unprotected; a push racing into the flip window bypasses all gates. Add to the runbook:
   - A post-action verification step (`gh api /repos/.../branches/main/protection | jq '.allow_force_pushes.enabled'` must return `false` before the runbook step is marked complete).
   - Consider a short-TTL wrapper script that auto-reverts `allow_force_pushes` after N minutes regardless of whether the rollback succeeded.
3. **Add a periodic audit by SRE** of the repo audit log for unsanctioned `allow_force_pushes` flips. Every legitimate flip corresponds to an authorized rollback bead; anything else is investigated. Cadence: fold into ADR-005's monthly-then-quarterly audit scope since that persona already holds the audit cadence.
4. **Fold into ADR-005's monthly/quarterly dismissal audit**: surface any release-cut PRs where a PO downgraded a P1 to P2 to clear G1a in the 30 days prior to the cut. This catches the authorized-but-risky bypass pattern that G1a's "explicit escape hatch" permits.

These runbook/audit edits are not mechanism changes in ADR-004 itself — they're dependencies ADR-004 creates on operational documentation. Tracked as acceptance items in the ADR-004 implementation bead.

## Exit Path

If the short-lived-release-branch + merge-commit + pre-cut gate pattern proves too heavy:

1. **Soft exit — drop the release branch, keep the gate.** Retain the pre-cut gate and the "Release vX.Y.Z" PR-title convention, but open the PR directly from `dev`→`main`. Loses the cut-point-SHA clarity of a release branch; keeps scope visibility and gate enforcement. `shipping.md` revert to a merge-from-`dev` step. ~15 minutes of doc edits.
2. **Medium exit — relax to Option 3 (trunk-based with tags).** Abandon the release-branch mechanism entirely; main tracks dev via merge-on-green; releases are just tags on main. Requires removing the no-other-main-PRs gate (G7). Reintroduces the PR #82 risk surface. ADR-004 would be superseded by a successor ADR.
3. **Hard exit — revert to pre-ADR state.** `shipping.md` reverts to its prior Release Workflow section; branch protection stays as ADR-005 left it. ~5 minutes. The 0.16.0 failure mode re-opens; the PR #82 failure mode re-opens. This ADR's failure modes (if any) would be documented in the retro bead motivating the exit.
4. **Full-git-flow upgrade (Alternative 1)**. If the project scales to multiple maintained release versions with backporting, upgrade to long-lived release branches per minor version. This ADR becomes the transition-era pattern; a successor ADR formalizes git-flow. 1-2 days of process work, significant doc churn.

No infrastructure to tear down; no vendor relationship to unwind. All exit paths are doc + branch-protection changes.

## Open Questions (Resolved by PO — 2026-04-21)

1. **Is hotfix version numbering always a patch bump?** **Resolved: default patch bump (`vX.Y.(Z+1)`); minor bump permitted only when explicitly authorized by the PO in the hotfix PR description** (e.g., schema change to mitigate a CVE). The exception is auditable because the authorization lives in the PR body, not in a private channel.
2. **How is G1a (zero open P0/P1) verified mechanically?** **Resolved: Phase 1 is human-verified** via the PR description checklist; **Phase 2 (tracked as a follow-on bead) automates both G1a and G1b in CI**. G1b automates more easily today (`gh api .../code-scanning/alerts` is available in CI without extra tooling); G1a requires `bd` to be runnable from CI, which is a small infrastructure lift. Phase 2 work is not blocking for ADR-004 to take effect.
3. **Do documentation-only PRs to `dev` need to wait for the next release to reach `main`?** **Resolved: yes, with one narrow exception.** Doc-only hotfixes are bounded to the specific runbook or ADR file actively being referenced during a live incident — they do not cover general doc typos, broken links, or updates to unrelated guides. General doc fixes always wait for the next cut. This keeps the hotfix path from normalizing as "any small change to main" and ties doc-hotfixes to an auditable incident trigger.
4. **When does a hotfix warrant back-merging to `dev`?** **Resolved: within 24 hours, manual**, via a standard `dev`-targeting PR that merges the hotfix branch. A follow-on bead files to automate via GitHub Action if hotfixes become more than occasional; for now the manual path preserves reviewer visibility on back-merges.

## References

- Bead `enhancedchannelmanager-4lk1q` — this ADR's tracker (P1)
- Bead `enhancedchannelmanager-sm3n3` — ADR-005 (CodeQL delta-zero; dependency)
- Bead `enhancedchannelmanager-vgm4l` — 0.16.0 rollback (root-cause exemplar for gate G1a)
- Bead `enhancedchannelmanager-mghjm` — 0.16.0 release that was rolled back
- Bead `enhancedchannelmanager-8w33i` — main branch protection config
- `docs/shipping.md` — existing ship-cycle + release-workflow docs (edits required on acceptance)
- `docs/runbooks/v0.16.0-rollback.md` — rollback runbook (one-line ADR-005 note to be added on acceptance)
- `CHANGELOG.md` — Keep-a-Changelog source; `[Unreleased]` → `[X.Y.Z]` promotion is gate G5
- PR #82 — incident reference (scope-sprawl failure mode, root-cause exemplar for gate G7)
