# Shipping Workflow

## When User Says "Ship the Fix"

Follow these steps in order:

### 1. Run Quality Gates (MANDATORY)

```bash
# Backend (if backend changed)
python -m py_compile backend/main.py
cd backend && python -m pytest tests/ -q

# Frontend (if frontend changed)
cd frontend && npm test && npm run build
```

**CRITICAL**: If syntax checks or tests fail, fix errors before proceeding. Never commit broken code.

### 2. Update the Bead

```bash
bd update <id> --description "Detailed description of changes made"
```

### 3. Increment the Version

Edit `frontend/package.json` version using bug fix build number format (e.g., `0.12.0-0014`).

Re-run build to verify:
```bash
cd frontend && npm run build
```

The version-bump commit lands via PR like every other change to `dev` — the branch protection rule applies regardless of how trivial the diff is. This was surfaced by `bd-i6a1m`'s tag-stone bump (commit `c479b99a`, `0.16.0-0058 → 0.16.0-0059`), which was rejected on direct push and had to merge via [PR #175](https://github.com/MotWakorb/enhancedchannelmanager/pull/175). Tag-stone bumps follow the same §6 PR flow as feature work; there is no fast-path exemption.

### 4. Close the Bead

```bash
bd close <id>
```

### 5. Update README.md and CHANGELOG.md if Needed

If the change adds, removes, or modifies a feature, update the documentation.

Every user-facing change must also be recorded in [`CHANGELOG.md`](../CHANGELOG.md) under the `[Unreleased]` section, in the appropriate Keep-a-Changelog category (Added / Changed / Deprecated / Removed / Fixed / Security). When cutting a release, rename the `[Unreleased]` heading to the new version with the release date and start a fresh empty `[Unreleased]` section above it.

### 6. Commit and Open the PR

`dev` branch protection requires PRs with 5 passing status checks (`enforce_admins=true` — no one bypasses, including the PO). Direct push to `dev` is rejected. Branch from current `origin/dev`, push the branch, open a PR, wait for the required checks, then merge.

```bash
# Branch from current origin/dev
git fetch origin
git checkout -b <feature-or-chore-branch> origin/dev

# Stage and commit only changed files
git add frontend/package.json backend/main.py backend/routers/
git commit -m "v0.x.x-xxxx: Brief description"

# Push and open the PR
git push -u origin <feature-or-chore-branch>
gh pr create --base dev --head <feature-or-chore-branch> \
  --title "v0.x.x-xxxx: Brief description" \
  --body "Summary of the change and link to the bead."

# Wait for the 5 required checks to pass:
#   - Backend Tests
#   - Frontend Tests
#   - CodeQL Analysis (python)
#   - CodeQL Analysis (javascript-typescript)
#   - Semgrep Lint
gh pr checks <#> --watch

# Merge with a merge commit (NOT --squash, NOT --rebase) per ADR-004 —
# preserves per-commit bisection/forensics into dev.
gh pr merge <#> --merge --delete-branch

# Verify
git checkout dev && git pull
git status  # MUST show "up to date with origin"
```

The required check names above are pulled from `gh api /repos/MotWakorb/enhancedchannelmanager/branches/dev/protection | jq '.required_status_checks.contexts'` — if branch protection changes, update this list.

### 7. File Beads for Remaining Work

Create beads for anything that needs follow-up.

## Critical Shipping Rules

- Work is NOT complete until the PR merges into `dev`
- NEVER stop before the PR is merged — an open PR is not a shipped change
- NEVER say "ready to merge when you are" — YOU must drive the merge once the required checks are green
- If a required check fails, fix the underlying issue and push to the same branch; do not bypass or skip checks
- Always use `enhancedchannelmanager` as the repository name when creating beads
- **NEVER chain `bd create` and `bd close` in one command** — the `bd list` output format doesn't work with shell parsing. Always run them as separate commands:
  ```bash
  bd create enhancedchannelmanager "Description"  # Note the ID it prints
  bd close <id>                                    # Use the exact ID
  ```
- Do NOT run `bd sync` as part of code commits. `bd sync` is ONLY for syncing beads issue tracking data.

## Release Workflow (Merging to Main)

Release cuts are **intentional, gated acts** — not emergent side effects of whatever PR next targets `main`. This workflow is authoritative per [ADR-004: Release-Cut Promotion Discipline](adr/ADR-004-release-cut-promotion-discipline.md); read that ADR for full context on why each step exists and which alternatives were rejected.

**Who**: PO (authorizes the cut) + Project Engineer (executes the mechanics). **When**: on PO decision to promote a specific `dev` SHA to `main`. **Why this shape**: the short-lived `release/vX.Y.Z` branch creates an explicit cut point, the merge-commit PR preserves per-commit bisection/forensics into `main`, and the pre-cut gate (G1a–G7 below) closes the 0.16.0-rollback failure mode (shipped with open P0/P1 bugs) and the PR #82 failure mode (scope-sprawl doc PR swept 90 unrelated commits).

Non-release PRs to `main` are forbidden — documentation, dep bumps, config tweaks, and feature work all flow through `dev` and reach `main` only via the next release cut. The one exception is the hotfix path below.

### Cut Mechanics (Step-by-Step)

Copied verbatim from ADR-004 §"Cut Mechanics". Steps 0 and 5 are load-bearing discipline; steps 1–4 and 6–10 are mechanical.

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

**Root checkout MUST stay on `dev`** throughout — never leave it on `main`.

### Pre-Cut Gate Checklist

All seven items must pass before the release-cut PR can merge. Copy-paste this block into the release-cut PR description (step 5 above already includes it). **Phase 2 (`bd-3d0tv`) lifted G1a, G1b, G5, G6, G7 to mechanical CI enforcement** via `.github/workflows/release-cut-gate.yml` — the workflow runs on every PR opened against `main`, classifies release-cut PRs by title-regex (`^Release vX.Y.Z$`) AND head-branch-regex (`^release/vX.Y.Z$`), and fails the `Release Cut Gate` required check if any of the five mechanical gates fail. The PR-description checklist is now a redundant safety net (kept for the cut-authorizer to read; no longer the primary gate). G2, G3, G4 are mechanically enforced via existing required checks (`Backend Tests`, `Frontend Tests`, `CodeQL Analysis (python|javascript-typescript)`).

| # | Gate | Enforcement | Cites |
|---|---|---|---|
| G1a | **Zero open P0/P1 bugs at the `dev` cut SHA** (beads board, all scopes) | Mechanical: `Release Cut Gate` workflow runs `bd list --status open --priority 0/1` on the release branch's `.beads/issues.jsonl`. PR-description "G1a Justifications" parsing is not yet automated — open P0/P1s require manual override (close them, or escalate to the cut-authorizing reviewer) | `bd-vgm4l` root cause; `bd-3d0tv` automation |
| G1b | **Zero open HIGH/CRITICAL security findings not formally waived** (GitHub Security tab + active advisories) — distinct from G1a so a mis-triaged finding cannot slip through "the bug board is clean" | Mechanical: `Release Cut Gate` workflow queries `code-scanning/alerts?state=open` and fails on any HIGH/CRITICAL. Dismissed-in-Security-tab alerts have `state=dismissed` and naturally pass. PR-description cross-reference (the second half of "formally waived" semantics) is human-verified | Complement to ADR-005 gate G4; `bd-3d0tv` automation |
| G2 | `Backend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G3 | `Frontend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G4 | **CodeQL delta-zero vs. `main` base** (both matrix check-runs). The delta is computed between the release-cut PR head and `main`, **not** against the release-branch cut SHA — the release branch's own base is transparent to GitHub's merge protection rule, which compares the incoming head to the target branch. | Code Scanning merge protection rule + branch protection required checks | ADR-005 |
| G5 | `CHANGELOG.md` `[Unreleased]` has been promoted to `[X.Y.Z]` with today's date and a fresh empty `[Unreleased]` above | Mechanical: `Release Cut Gate` workflow asserts (a) `[Unreleased]` heading present, (b) `[X.Y.Z] — YYYY-MM-DD` heading present with today's UTC date, (c) `[Unreleased]` line-number is above `[X.Y.Z]` (Keep-a-Changelog ordering) | `shipping.md` §CHANGELOG convention; `bd-3d0tv` automation |
| G6 | Version updated in `frontend/package.json` from the current `0.A.B-NNNN` dev build to the target release version `X.Y.Z`. The target is not necessarily `A.B` with the suffix stripped — a minor or patch bump is permitted (e.g., current dev tip `0.16.0-0041` → release `0.17.0`) — but must match the release-branch name (`release/vX.Y.Z`) | Mechanical: `Release Cut Gate` workflow extracts version from branch name and asserts `jq -r .version frontend/package.json` returns the same string | `shipping.md` §Increment the Version; `bd-3d0tv` automation |
| G7 | **No other release-cut or hotfix PR targeting `main` is open at merge time.** If a hotfix PR and a release-cut PR contend simultaneously, the **hotfix has priority**: the release-cut PR rebases on the merged hotfix and re-runs the gate. Prevents live-lock during an incident. | Mechanical at PR-open/sync (steady-state catch): `Release Cut Gate` workflow lists open PRs against `main` and fails on any other release/hotfix branch. The merge-time race window between the last sync and the merge click is still author/reviewer-verified | PR #82 root cause; `bd-3d0tv` automation |

#### `Release Cut Gate` workflow output

The workflow lives at `.github/workflows/release-cut-gate.yml`. To inspect its output for a given release PR, check the "Release Cut Gate" check on the PR's checks tab, or:

```bash
gh run list --workflow=release-cut-gate.yml --branch release/vX.Y.Z --limit 1
gh run view <run-id> --log
```

Per-gate pass/fail messages are prefixed with the gate name (`G1a PASS:`, `G5 FAIL: ...`) for grep-friendly inspection. Non-release PRs to `main` (hotfixes; accidental main-bound feature PRs) short-circuit to a pass — the workflow only enforces gates when both the title and head-branch regex match the release-cut shape.

#### G1b "formally waived" semantics

A HIGH/CRITICAL CodeQL or active security advisory finding is **formally waived** for purposes of G1b only when **both** of the following are true at merge time:

1. **GitHub Security-tab dismissal with rationale.** The alert is dismissed in the repository's Security tab via the GitHub UI, with a non-empty comment recording the dismissal category and a one-line justification. The dismissal becomes part of the alert's audit record and is visible to the monthly/quarterly dismissal-log audit. Permitted dismissal categories are exactly those defined in [ADR-005](adr/ADR-005-code-security-gating-strategy.md) §Dismiss-With-Comment Policy: **false-positive (with linked evidence)** or **test-only sink**. "Won't fix" is **not** a Phase 1 dismissal category — risk acceptance for a confirmed true-positive runs through a separate Security-Engineer-reviewed bead, and the alert stays open (G1b is therefore **not** satisfied for that finding).
2. **PR-description cross-reference.** The release-cut PR description includes a line citing the alert number, the dismissal category, and the dismissing user, e.g. `- Alert #1418 (py/path-injection, HIGH): dismissed as false-positive (with evidence) on 2026-04-22 by @user — sanitized via Path.resolve().relative_to() at backup.py:164-167.` This belt-and-suspenders cross-reference makes the waiver legible to the cut-authorizing reviewer without requiring them to context-switch into the Security tab, and survives in `git log` after the dismissal record is later edited or the alert is reopened.

A Security-tab dismissal **without** a corresponding PR-description line does **not** satisfy G1b — the cross-reference is the visible-in-PR-record half of the gate. Conversely, a PR-description claim of dismissal **without** an actual Security-tab dismissal is a false attestation; reviewers must spot-check by running the G1b query in `Cut Mechanics` step 0 and confirming it returns `0` after the claimed dismissals.

This mirrors ADR-005's Dismiss-With-Comment Policy item 1 ("the comment becomes part of the alert record and is visible in future audits") and extends it to the release-cut surface so the same dismissal evidence is visible in two places — the Security tab (for security audits) and the PR description (for release-cut audits).

### Hotfix Path

Genuine production-blocking bugs, critical security advisories, and GHCR/branch-protection emergencies can bypass the release-branch mechanism via a **hotfix PR branched directly from `main`** — not from `dev`. Prose rules (per ADR-004 §4):

- **Branch name**: `hotfix/vX.Y.(Z+1)-description` — patch version increment by default. Minor bump permitted only with explicit PO authorization recorded in the hotfix PR description (e.g., schema change to mitigate a CVE).
- **Scope**: minimal. One bug or one advisory per hotfix; no opportunistic cleanup.
- **Gates that apply**:
  - G2, G3, G4 (tests + CodeQL) — must pass as on any release cut. **Exception — G4 re-attribution waiver**: if a security hotfix trips CodeQL delta-zero solely because its touched code is near a pre-existing `main` finding that CodeQL re-attributes to the new commit, the author may waive G4 with a linked Security-tab dismissal rationale in the PR description. Genuinely new HIGH/CRITICAL findings introduced by the hotfix code **cannot** be waived.
  - G1a applies only to bugs *regressed or introduced* by the hotfix — pre-existing P0/P1s on `dev` are being bypassed intentionally because the hotfix is more urgent.
  - G1b applies only to findings *introduced* by the hotfix — the hotfix is often the remediation of a pre-existing finding and must not block itself.
  - G5 (CHANGELOG) applies with a hotfix-scoped entry.
  - G6 (version) applies as a patch bump.
  - G7 (no other main-bound PRs) applies; hotfix-has-priority tiebreaker.
- **Back-merge to `dev` within 24 hours**, manual, via a standard `dev`-targeting PR that merges the hotfix branch. Merge (not cherry-pick) preserves the hotfix commit chain in `dev`'s history and keeps bisection symmetric with the release-cut pattern.
- **Hotfixes should be rare.** Every hotfix is a signal the pre-cut gate failed — file a retro bead for each.
- **Mechanical ceiling**: if more than **two hotfixes** land between consecutive release cuts, a **mandatory incident review bead** must be filed and landed before the next release cut can proceed. This prevents the `hotfix/*` branch from becoming a de facto replacement for the release-cut PR (the PR #82 pattern with different branch names).

Step-by-step hotfix commands follow the same shell pattern as the release cut above, substituting `release/vX.Y.Z` with `hotfix/vX.Y.(Z+1)-description` and branching from `main` rather than a `dev` SHA. CHANGELOG entry goes under a hotfix-scoped version heading.

## Branch Protection on Main

`main` is protected (configured via bead `enhancedchannelmanager-8w33i`). Enforced rules:

- **Required status checks** (strict, branch must be up-to-date): `Backend Tests`, `Frontend Tests` (both in `.github/workflows/test.yml`), `CodeQL Analysis (python)` and `CodeQL Analysis (javascript-typescript)` (matrix in `.github/workflows/build.yml`), and `Release Cut Gate` (mechanical G1a/G1b/G5/G6/G7 verification in `.github/workflows/release-cut-gate.yml` per `bd-3d0tv`).
- **Force-pushes blocked** and **deletions blocked**.
- **Required conversation resolution** on PRs.
- **Admins are NOT enforced** — the PO can push hotfixes directly if a check outage would otherwise block a release. Use sparingly.
- **PR reviews are NOT required** — solo-maintainer workaround; add a review requirement when contributor count grows.
- **Linear history not required** and **signed commits not required** — matches current merge-commit-tolerant workflow.

To inspect or adjust: `gh api /repos/MotWakorb/enhancedchannelmanager/branches/main/protection`. Full config lives only in the GitHub API (no IaC yet).
