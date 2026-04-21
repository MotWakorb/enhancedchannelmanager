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

### 4. Close the Bead

```bash
bd close <id>
```

### 5. Update README.md and CHANGELOG.md if Needed

If the change adds, removes, or modifies a feature, update the documentation.

Every user-facing change must also be recorded in [`CHANGELOG.md`](../CHANGELOG.md) under the `[Unreleased]` section, in the appropriate Keep-a-Changelog category (Added / Changed / Deprecated / Removed / Fixed / Security). When cutting a release, rename the `[Unreleased]` heading to the new version with the release date and start a fresh empty `[Unreleased]` section above it.

### 6. Commit and Push

```bash
git add frontend/package.json backend/main.py backend/routers/  # Add only changed files
git commit -m "v0.x.x-xxxx: Brief description"
git push origin dev
git status  # MUST show "up to date with origin"
```

### 7. File Beads for Remaining Work

Create beads for anything that needs follow-up.

## Critical Shipping Rules

- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push
- If push fails, resolve and retry until it succeeds
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

All seven items must pass before the release-cut PR can merge. Copy-paste this block into the release-cut PR description (step 5 above already includes it). G1a, G1b, G5, G6, G7 are author/reviewer-verified at Phase 1 and can be lifted to CI at Phase 2 (separate follow-on bead). G2, G3, G4 are mechanically enforced from day one.

| # | Gate | Enforcement | Cites |
|---|---|---|---|
| G1a | **Zero open P0/P1 bugs at the `dev` cut SHA** (beads board, all scopes) | Manual verification by cut-authorizer; checklist item in the release-cut PR description | `bd-vgm4l` root cause |
| G1b | **Zero open HIGH/CRITICAL security findings not formally waived** (GitHub Security tab + active advisories) — distinct from G1a so a mis-triaged finding cannot slip through "the bug board is clean" | Manual verification at Phase 1 via `gh api .../code-scanning/alerts` + Security tab review; mechanical at Phase 2 | Complement to ADR-005 gate G4 |
| G2 | `Backend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G3 | `Frontend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G4 | **CodeQL delta-zero vs. `main` base** (both matrix check-runs). The delta is computed between the release-cut PR head and `main`, **not** against the release-branch cut SHA — the release branch's own base is transparent to GitHub's merge protection rule, which compares the incoming head to the target branch. | Code Scanning merge protection rule + branch protection required checks | ADR-005 |
| G5 | `CHANGELOG.md` `[Unreleased]` has been promoted to `[X.Y.Z]` with today's date and a fresh empty `[Unreleased]` above | Manual in PR review; Phase 2 CI check possible | `shipping.md` §CHANGELOG convention |
| G6 | Version updated in `frontend/package.json` from the current `0.A.B-NNNN` dev build to the target release version `X.Y.Z`. The target is not necessarily `A.B` with the suffix stripped — a minor or patch bump is permitted (e.g., current dev tip `0.16.0-0041` → release `0.17.0`) — but must match the release-branch name (`release/vX.Y.Z`) | Manual in PR review; trivially automatable | `shipping.md` §Increment the Version |
| G7 | **No other release-cut or hotfix PR targeting `main` is open at merge time.** If a hotfix PR and a release-cut PR contend simultaneously, the **hotfix has priority**: the release-cut PR rebases on the merged hotfix and re-runs the gate. Prevents live-lock during an incident. | Manual verification; one-line `gh pr list --base main --state open --json number,title` | PR #82 root cause |

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

- **Required status checks** (strict, branch must be up-to-date): `Backend Tests`, `Frontend Tests` — both jobs defined in `.github/workflows/test.yml`.
- **Force-pushes blocked** and **deletions blocked**.
- **Required conversation resolution** on PRs.
- **Admins are NOT enforced** — the PO can push hotfixes directly if a check outage would otherwise block a release. Use sparingly.
- **PR reviews are NOT required** — solo-maintainer workaround; add a review requirement when contributor count grows.
- **Linear history not required** and **signed commits not required** — matches current merge-commit-tolerant workflow.

To inspect or adjust: `gh api /repos/MotWakorb/enhancedchannelmanager/branches/main/protection`. Full config lives only in the GitHub API (no IaC yet).
