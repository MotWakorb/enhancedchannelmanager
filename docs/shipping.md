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

> **Deploying to the dev container** (the local edit→deploy→verify loop, separate from this PR flow): use `scripts/deploy-frontend.sh` for the frontend. It clears stale `/app/static/assets/*` before copying, which a hand-run `docker cp` skips. See CLAUDE.md → "Container-First Development".

### 2. Update the Bead

```bash
bd update <id> --description "Detailed description of changes made"
```

### 3. Increment the Version

The version literal is hand-edited in **three** files, and all three must move in lockstep. See [`docs/versioning.md`](versioning.md#touchpoints) → Touchpoints for the canonical list. CI enforces this via the `version-consistency` job (`scripts/check_version_consistency.py`) and fails the PR on divergence.

| File | Identifier to change |
| --- | --- |
| `frontend/package.json` | `"version"` (canonical source) |
| `backend/routers/backup.py` | `APP_VERSION` |
| `backend/main.py` | `version=` kwarg to `FastAPI(...)` |

Bump all three to the same string, using bug fix build number format (e.g., `0.12.0-0014`).

Verify locally before pushing. Run both checks, and both must pass:
```bash
python scripts/check_version_consistency.py   # must report all 3 agree
cd frontend && npm run build
```

The version-bump commit lands via PR like every other change to `dev`. The branch protection rule applies regardless of how trivial the diff is. This was surfaced by `bd-i6a1m`'s tag-stone bump (commit `c479b99a`, `0.16.0-0058 → 0.16.0-0059`), which was rejected on direct push and had to merge via [PR #175](https://github.com/MotWakorb/enhancedchannelmanager/pull/175). Tag-stone bumps follow the same §6 PR flow as feature work; there is no fast-path exemption.

### 4. Close the Bead

```bash
bd close <id>
```

### 5. Update README.md and CHANGELOG.md if Needed

If the change adds, removes, or modifies a feature, update the documentation.

Every user-facing change must also be recorded in [`CHANGELOG.md`](../CHANGELOG.md) under the `[Unreleased]` section, in the appropriate Keep-a-Changelog category (Added / Changed / Deprecated / Removed / Fixed / Security). When cutting a release, rename the `[Unreleased]` heading to the new version with the release date and start a fresh empty `[Unreleased]` section above it.

### 6. Commit and Open the PR

`dev` branch protection requires PRs with 7 passing status checks (`enforce_admins=true`: no one bypasses, including the PO). Direct push to `dev` is rejected. Branch from current `origin/dev`, push the branch, open a PR, wait for the required checks, then merge.

Each of those 7 names is emitted by exactly one job, on every PR. If you ever see the same context reported twice on one commit, stop: that is bead `enhancedchannelmanager-5rwzy` recurring, and a permanently green duplicate can hide a real failure behind the aggregate view. See `docs/testing.md` section "One source of truth per required check".

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

# Wait for the 7 required checks AND for the PR to be mergeable.
# Do NOT use `gh pr checks <#> --watch` here: see "Read the gate, not just
# the checks" below for why an all-green watch can still be unmergeable.
gh pr view <#> --json statusCheckRollup,mergeable,mergeStateStatus --jq '
  ((.statusCheckRollup // [])[]
    | select((.name // .context) as $n
        | ["Backend Tests","Frontend Tests","MCP Server Tests",
           "CodeQL Analysis (python)","CodeQL Analysis (javascript-typescript)",
           "Semgrep Lint","Version Consistency"] | index($n))
    | "  \(.name // .context): \(.conclusion // .state // "PENDING")"),
  "  mergeable=\(.mergeable)  mergeStateStatus=\(.mergeStateStatus)"'

# Merge with a merge commit (NOT --squash, NOT --rebase) per ADR-004 —
# preserves per-commit bisection/forensics into dev.
gh pr merge <#> --merge --delete-branch

# Verify
git checkout dev && git pull
git status  # MUST show "up to date with origin"

# Confirm the image actually published (see below - this step is NOT optional)
.venv/bin/python scripts/check_publish.py
```

The required check names above are pulled from `gh api /repos/MotWakorb/enhancedchannelmanager/branches/dev/protection | jq '.required_status_checks.contexts'`. If branch protection changes, update this list.

#### Read the gate, not just the checks

`gh pr checks <#> --watch` reports **check conclusions and nothing else**. It has no view of whether the branch actually merges, so a PR whose seven required checks are all green can still be sitting on a merge conflict, and the watch will report green right up to the moment `gh pr merge` refuses. That blind spot nearly landed a conflicted merge on 2026-08-10.

Re-run the `gh pr view` command above until all three conditions hold:

1. **Seven context lines appear.** Exactly seven, one per required name. A name that never appears is worse than a failing one: branch protection waits forever for a context no job emits, and `enforce_admins=true` means nobody can bypass it. If a required name is missing, stop and fix the workflow that should emit it, rather than waiting.
2. **Every one reads `SUCCESS`.** `PENDING`, `QUEUED` or `IN_PROGRESS` means keep polling. `FAILURE` means fix the branch. `SKIPPED` on a required context is a red flag, not a pass: GitHub counts a skipped job as satisfying the check, so a `SKIPPED` required context means a job skipped itself instead of gating its steps.
3. **`mergeStateStatus` is `CLEAN`, or is `UNSTABLE` and you have read the failing non-required check and decided to accept it.** See "When `UNSTABLE` is the terminal state" below: `UNSTABLE` can be permanent, and the ship agent is required to drive the merge, so it needs a documented exit rather than a poll that never ends.

| `mergeStateStatus` | What it means | What to do |
| --- | --- | --- |
| `CLEAN` | Required checks green, no conflict. | Merge. |
| `UNSTABLE` | A non-required check is failing; the required seven are green. | Read the failing check, then decide. |
| `BLOCKED` | A required check is failing, missing, or a review is outstanding. | Do not merge. Find which one. |
| `DIRTY` | Merge conflict with the base branch. | `git fetch origin && git merge origin/dev`, resolve, push. |
| `BEHIND` | The branch is behind the base and the base requires up-to-date branches. | Update the branch from `origin/dev`. |
| `UNKNOWN` | GitHub has not finished computing mergeability. | Poll again; it resolves within seconds. |

`mergeable` carries the same signal in coarser form (`MERGEABLE` / `CONFLICTING` / `UNKNOWN`). It is worth printing alongside `mergeStateStatus` because it settles first after a push, so it is the earlier warning of a conflict.

##### When `UNSTABLE` is the terminal state

`UNSTABLE` means every required check is green and something else is red. Polling will not clear it. Several jobs on every PR are deliberately **not** required contexts, including `Operator Docs`, `Fake-Test Guard`, `Visual Regression`, `Screen-Reader-Only Rendering Guard` and `Operator Workspace Release Matrix`. Any of them failing produces a permanent `UNSTABLE`.

This matters most for `Operator Docs`, which is where `mkdocs build --strict` and the em-dash ratchet live. A broken user-guide link fails that job and nothing else, so the PR sits at `UNSTABLE` forever.

`CLAUDE.md` requires the ship agent to drive the merge, so "wait for `CLEAN`" is not a usable instruction here. The rule is:

- **Open the failing job and read it.** `gh pr checks <#>` lists the non-required failures by name.
- **If the failure is caused by this branch, fix it and push.** A broken link, a new em-dash, a fake-test marker: these are your change's defects even though branch protection will not stop you.
- **If it is a known flake or unrelated infrastructure failure, merging on `UNSTABLE` is allowed.** Say in the merge message which check was red and why you accepted it.
- **Never merge on `UNSTABLE` without naming the red check.** The whole point of these jobs being unrequired is that a human or agent judges them; skipping the judgement makes them decorative.

#### What a green check actually ran

Six of the seven required checks gate their real work on `scripts/classify_changed_paths.py`: on a documentation-only change they pass without running a suite. The check name is identical either way, so every required job writes one line to the run's **Summary** page saying which of the two happened, for example `Backend Tests: documentation-only change, no backend source changed, the pytest suite was NOT run.` versus `Backend Tests: ran the backend pytest suite. 2147 tests, 0 failed, 0 errored.`

Read that summary before treating a green rollup as proof the suite passed. `gh run view <run-id>` links it, or open the run in the browser.

#### Confirm the image published

A merged PR is not a published image. Merging to `dev` triggers the **Build and Push Docker Image** workflow, and that workflow can fail *after* the merge for reasons the PR's own checks never saw. The published `:dev` tag has silently lagged `dev` four times, from four unrelated causes:

1. A Buildx flake that skipped the multi-arch manifest.
2. A GitHub Actions outage that orphaned queued runs.
3. A frontend test flake that correctly gated the publish.
4. PR #793 (2026-08-07): the `dev` Tests workflow failed on one order-dependent frontend flake, so the publish gate correctly refused to ship from a failed suite.

In every case the gate behaved correctly and nothing surfaced the drift. On the most recent one, `dev` carried the fix and the registry did not, for about five hours, until an unrelated merge republished it by accident. Nobody noticed until somebody went looking.

So after `git checkout dev && git pull`, run:

```bash
.venv/bin/python scripts/check_publish.py
```

The script checks two things for the merge commit at `HEAD`:

1. The **Build and Push Docker Image** workflow run for that commit concluded `success`.
2. The published tag's build marker (`ECM_VERSION`, baked into the image from the `ECM_VERSION` build-arg) equals the version in `frontend/package.json` **at that commit**.

Both must hold. A green workflow with a stale marker means the push did not land on the tag. A correct marker with a failed workflow means the tag is still serving an older build.

**It is a post-merge check, deliberately not a CI gate.** A check that runs after the merge cannot gate the merge it follows, and adding it to the PR flow would put a permanently-failing context on every open PR. Running it *before* the merge is the one way to misread it: on a feature branch the version bump has not reached `dev` yet, so the registry cannot possibly carry it. The script detects that case and prints a `PRE-MERGE RUN` banner saying the mismatch is expected.

If it fails after a merge, re-run the failed workflow from the URL the script prints. Do not wait for the next merge to republish by accident.

```bash
gh run rerun <run-id> --failed
```

Useful flags:

| Flag | Effect |
| --- | --- |
| `--commit <sha>` | Verify a specific commit instead of `HEAD`. |
| `--pull` | Read the marker by dropping and re-pulling the tag, the heavier check, instead of reading the registry config blob. |
| `--skip-workflow` / `--skip-image` | Run only one of the two checks. |

The same "prove the image before you trust it" discipline applies to any other manual image check you run: drop the local tag, pull it fresh, and read the version markers baked into the image before trusting it, the same way `--pull` above does.

### 7. File Beads for Remaining Work

Create beads for anything that needs follow-up.

### Worktree quirks: npm and node_modules unavailable

When an agent is spawned into `.claude/worktrees/agent-*/`, the git sparse-checkout
does not carry `frontend/node_modules`, and the agent's shell environment does not
have `fnm`/`nvm` on `PATH`. Running `npm run lint`, `npm test`, or `npm run build`
fails immediately with "command not found".

**Root cause:** git worktrees share the `.git` object store but each has its own
working tree populated by the sparse-checkout filter. `node_modules` is gitignored
and therefore absent in every fresh worktree. The agent harness does not run
`npm install` on spawn, and the subagent shell does not inherit the parent session's
`fnm`/`nvm` init.

**Why a plain symlink is not enough:** the main checkout's `node_modules` is installed
inside the container as root, so `node_modules/.vite-temp` and `node_modules/.vite`
are owned by root:root (0755). Vite's config-loader writes per-run `.mjs` timestamp
files into `.vite-temp`; the dep-optimizer writes its cache into `.vite`. Both
EACCES immediately in a non-root worktree even though a `mkdir(.vite-temp, {recursive:true})`
returns success (the dir already exists).

**Fix:** run the bootstrap script once from the worktree root:

```bash
bash scripts/worktree-bootstrap.sh
```

The script creates a **real, writable `frontend/node_modules` directory** in the
worktree (replacing any old plain symlink). Inside it, `.vite-temp/` and `.vite/`
are local writable directories owned by the agent user. Every package and `.bin` is
symlinked from the main checkout's `node_modules` so no disk duplication occurs.

After bootstrapping, invoke frontend tooling via the `.bin` wrappers directly instead
of `npm run`:

```bash
./frontend/node_modules/.bin/vitest run
./frontend/node_modules/.bin/eslint frontend/src --max-warnings 0
./frontend/node_modules/.bin/tsc --noEmit
./frontend/node_modules/.bin/vite build
```

You also need `node` on `PATH`. The correct fnm path on this system is:

```bash
export PATH="$HOME/.local/share/fnm/node-versions/v24.13.0/installation/bin:./frontend/node_modules/.bin:$PATH"
```

Note: the fnm root is `$HOME/.local/share/fnm/` (NOT `$HOME/.fnm/`, which does
not exist on this host). The `fnm` binary itself lives at
`$HOME/.local/share/fnm/fnm`; node versions are under
`$HOME/.local/share/fnm/node-versions/`.

See `frontend/CLAUDE.md` → "Worktree workaround" for a quick-reference version of
these steps.

### Worktree quirks: harness-level caveats (not fixable in this repo)

These two behaviors are Claude Code harness behaviors. They cannot be fixed by
in-repo changes; document them here so agents know the mitigations.

**cwd-trap:** The Bash tool's working directory resets to the **main checkout** root
between tool calls, not to the worktree root. A stale `cd <worktree>` from a prior
turn does not persist. Mitigation: always use **absolute paths** for every Read,
Edit, and Bash call. Use `pwd && git branch --show-current` to verify context before
any destructive git operation.

**Locked-worktree accumulation:** Terminated or crashed agents sometimes leave their
worktree directories with a lock file, preventing `git worktree remove`. To clear:

```bash
# List all worktrees
git worktree list

# Force-remove a stale worktree (safe when no uncommitted changes)
git worktree remove -f /path/to/.claude/worktrees/agent-<id>

# If the lock file blocks even --force, remove it manually first:
rm /path/to/.claude/worktrees/agent-<id>/.git/worktree-lock   # or similar path
git worktree prune
```

Skip any worktree that shows uncommitted changes in `git -C <path> status`. Those
may be in-flight agent work, not orphans.

## Critical Shipping Rules

- Work is NOT complete until the PR merges into `dev`
- NEVER stop before the PR is merged: an open PR is not a shipped change
- NEVER say "ready to merge when you are": YOU must drive the merge once the required checks are green
- If a required check fails, fix the underlying issue and push to the same branch; do not bypass or skip checks
- Always use `enhancedchannelmanager` as the repository name when creating beads
- **NEVER chain `bd create` and `bd close` in one command**: the `bd list` output format doesn't work with shell parsing. Always run them as separate commands:
  ```bash
  bd create enhancedchannelmanager "Description"  # Note the ID it prints
  bd close <id>                                    # Use the exact ID
  ```
- Do NOT run `bd sync` as part of code commits. `bd sync` is ONLY for syncing beads issue tracking data.

## MCP Release Verification

Before cutting any release that touches MCP code, the releaser must walk the manual verification checklist in [`docs/runbooks/mcp-release-verification.md`](runbooks/mcp-release-verification.md). This covers:

1. Static `?api_key=` connection (query-param path) end-to-end
2. Making a tool call over the static-key connection
3. Settings panel smoke check (MCP server status, key generate/regenerate)

Sign-off text from the checklist goes in the release PR description alongside the G1a–G7 gate checklist.

> **Note (bd-9axgc):** the MCP OAuth "Custom Connector" offering was retired. The
> static `?api_key=` path is the supported MCP authentication method. The OAuth
> verification steps were removed from this checklist.

Releases that do not touch `mcp-server/` or `MCPSettingsSection.tsx` may skip this checklist (at releaser discretion).

## Release Workflow (Merging to Main)

Release cuts are **intentional, gated acts**, not emergent side effects of whatever PR next targets `main`. This workflow is authoritative per [ADR-004: Release-Cut Promotion Discipline](adr/ADR-004-release-cut-promotion-discipline.md); read that ADR for full context on why each step exists and which alternatives were rejected.

**Who**: PO (authorizes the cut) + Project Engineer (executes the mechanics). **When**: on PO decision to promote a specific `dev` SHA to `main`. **Why this shape**: the short-lived `release/vX.Y.Z` branch creates an explicit cut point, the merge-commit PR preserves per-commit bisection/forensics into `main`, and the pre-cut gate (G1a–G7 below) closes the 0.16.0-rollback failure mode (shipped with open P0/P1 bugs) and the PR #82 failure mode (scope-sprawl doc PR swept 90 unrelated commits).

Non-release PRs to `main` are forbidden: documentation, dep bumps, config tweaks, and feature work all flow through `dev` and reach `main` only via the next release cut. The one exception is the hotfix path below.

### Cut Mechanics (Step-by-Step)

Adapted from ADR-004 §"Cut Mechanics", with the post-cut dev update (step 8) corrected for `dev` branch protection: the ADR's original `git push origin dev` is rejected by the required-status-check rule, so the back-merge + counter-reopen are delivered via a PR (bd-5s1vd). ADR-004's mechanics predate that protection and should get a matching addendum. Steps 0 and 5 are load-bearing discipline; steps 1–4 and 6–9 are mechanical.

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

# 8. Back-sync dev AND re-open the build counter — via a PR.
# `dev` is a protected branch: a direct `git push origin dev` is rejected with
# "GH006: Protected branch update failed ... N of N required status checks are expected".
# So the post-cut dev update is delivered as ONE dev-targeting PR that folds together
# the back-merge (CHANGELOG promotion + version, plus any stabilization fixes that landed
# on the release branch — all now on `main`) and the reopened build counter.
git checkout dev && git pull
git checkout -b chore/post-release-v0.17.0
git merge origin/main --no-edit          # fast-forwards to the released state (dev is an ancestor of main)
# Re-open the dev build counter: bump ALL THREE version touchpoints from "0.17.0"
# (no suffix, inherited from main) to the next build-numbered version "0.17.1-0000"
# (or the next planned minor's -0000). The Version Consistency check requires all three:
#   - frontend/package.json        "version"
#   - backend/main.py              FastAPI(version=...)
#   - backend/routers/backup.py    APP_VERSION
python3 scripts/check_version_consistency.py    # must report all 3 agree
git add frontend/package.json backend/main.py backend/routers/backup.py
git commit -m "Post-release: back-sync dev + reopen build counter to 0.17.1-0000"
git push -u origin chore/post-release-v0.17.0
POST_PR_URL=$(gh pr create --base dev --head chore/post-release-v0.17.0 \
  --title "Post-release: back-sync dev + reopen counter (0.17.1-0000)" \
  --body "Back-syncs \`dev\` after the v0.17.0 cut (CHANGELOG already promoted on main) and reopens the build counter to 0.17.1-0000. Delivered via PR because \`dev\` is protected.")
# Wait for the required checks to pass, then merge (merge commit, per ADR-004):
gh pr merge "${POST_PR_URL##*/}" --merge --delete-branch

# 9. Delete the release branch — already done by `--delete-branch` in step 6 (both local
# and remote). Verify nothing lingers, then return the root checkout to dev:
git branch -a | grep "release/v0.17.0" || echo "release branch fully removed"
git checkout dev && git pull
```

**Root checkout MUST stay on `dev`** throughout. Never leave it on `main`.

### Pre-Cut Gate Checklist

All seven items must pass before the release-cut PR can merge. Copy-paste this block into the release-cut PR description (step 5 above already includes it). **Phase 2 (`bd-3d0tv`) lifted G1a, G1b, G5, G6, G7 to mechanical CI enforcement** via `.github/workflows/release-cut-gate.yml`. The workflow runs on every PR opened against `main`, classifies release-cut PRs by title-regex (`^Release vX.Y.Z$`) AND head-branch-regex (`^release/vX.Y.Z$`), and fails the `Release Cut Gate` required check if any of the five mechanical gates fail. The PR-description checklist is now a redundant safety net (kept for the cut-authorizer to read; no longer the primary gate). G2, G3, G4 are mechanically enforced via existing required checks (`Backend Tests`, `Frontend Tests`, `CodeQL Analysis (python|javascript-typescript)`).

| # | Gate | Enforcement | Cites |
|---|---|---|---|
| G1a | **Zero open P0/P1 bugs at the `dev` cut SHA** (beads board, all scopes) | Mechanical: `Release Cut Gate` workflow runs `bd list --status open --priority 0/1` on the release branch's `.beads/issues.jsonl`. PR-description "G1a Justifications" parsing is not yet automated, so open P0/P1s require manual override (close them, or escalate to the cut-authorizing reviewer) | `bd-vgm4l` root cause; `bd-3d0tv` automation |
| G1b | **Zero open HIGH/CRITICAL security findings not formally waived** (GitHub Security tab + active advisories). This is distinct from G1a, so a mis-triaged finding cannot slip through "the bug board is clean" | Mechanical: `Release Cut Gate` workflow queries `code-scanning/alerts?state=open` and fails on any HIGH/CRITICAL. Dismissed-in-Security-tab alerts have `state=dismissed` and naturally pass. PR-description cross-reference (the second half of "formally waived" semantics) is human-verified | Complement to ADR-005 gate G4; `bd-3d0tv` automation |
| G2 | `Backend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G3 | `Frontend Tests` green on the release branch | Branch protection required check | Existing `bd-8w33i` |
| G4 | **CodeQL delta-zero vs. `main` base** (both matrix check-runs). The delta is computed between the release-cut PR head and `main`, **not** against the release-branch cut SHA. The release branch's own base is transparent to GitHub's merge protection rule, which compares the incoming head to the target branch. | Code Scanning merge protection rule + branch protection required checks | ADR-005 |
| G5 | `CHANGELOG.md` `[Unreleased]` has been promoted to `[X.Y.Z]` with today's date and a fresh empty `[Unreleased]` above | Mechanical: `Release Cut Gate` workflow asserts (a) `[Unreleased]` heading present, (b) `[X.Y.Z] — YYYY-MM-DD` heading present with today's UTC date, (c) `[Unreleased]` line-number is above `[X.Y.Z]` (Keep-a-Changelog ordering) | `shipping.md` §CHANGELOG convention; `bd-3d0tv` automation |
| G6 | Version updated in `frontend/package.json` from the current `0.A.B-NNNN` dev build to the target release version `X.Y.Z`. The target is not necessarily `A.B` with the suffix stripped: a minor or patch bump is permitted (e.g., current dev tip `0.16.0-0041` → release `0.17.0`), but it must match the release-branch name (`release/vX.Y.Z`) | Mechanical: `Release Cut Gate` workflow extracts version from branch name and asserts `jq -r .version frontend/package.json` returns the same string | `shipping.md` §Increment the Version; `bd-3d0tv` automation |
| G7 | **No other release-cut or hotfix PR targeting `main` is open at merge time.** If a hotfix PR and a release-cut PR contend simultaneously, the **hotfix has priority**: the release-cut PR rebases on the merged hotfix and re-runs the gate. Prevents live-lock during an incident. | Mechanical at PR-open/sync (steady-state catch): `Release Cut Gate` workflow lists open PRs against `main` and fails on any other release/hotfix branch. The merge-time race window between the last sync and the merge click is still author/reviewer-verified | PR #82 root cause; `bd-3d0tv` automation |

#### `Release Cut Gate` workflow output

The workflow lives at `.github/workflows/release-cut-gate.yml`. To inspect its output for a given release PR, check the "Release Cut Gate" check on the PR's checks tab, or:

```bash
gh run list --workflow=release-cut-gate.yml --branch release/vX.Y.Z --limit 1
gh run view <run-id> --log
```

Per-gate pass/fail messages are prefixed with the gate name (`G1a PASS:`, `G5 FAIL: ...`) for grep-friendly inspection. Non-release PRs to `main` (hotfixes; accidental main-bound feature PRs) short-circuit to a pass. The workflow only enforces gates when both the title and head-branch regex match the release-cut shape.

#### G1b "formally waived" semantics

A HIGH/CRITICAL CodeQL or active security advisory finding is **formally waived** for purposes of G1b only when **both** of the following are true at merge time:

1. **GitHub Security-tab dismissal with rationale.** The alert is dismissed in the repository's Security tab via the GitHub UI, with a non-empty comment recording the dismissal category and a one-line justification. The dismissal becomes part of the alert's audit record and is visible to the monthly/quarterly dismissal-log audit. Permitted dismissal categories are exactly those defined in [ADR-005](adr/ADR-005-code-security-gating-strategy.md) §Dismiss-With-Comment Policy: **false-positive (with linked evidence)** or **test-only sink**. "Won't fix" is **not** a Phase 1 dismissal category. Risk acceptance for a confirmed true-positive runs through a separate Security-Engineer-reviewed bead, and the alert stays open (G1b is therefore **not** satisfied for that finding).
2. **PR-description cross-reference.** The release-cut PR description includes a line citing the alert number, the dismissal category, and the dismissing user, e.g. `- Alert #1418 (py/path-injection, HIGH): dismissed as false-positive (with evidence) on 2026-04-22 by @user — sanitized via Path.resolve().relative_to() at backup.py:164-167.` This belt-and-suspenders cross-reference makes the waiver legible to the cut-authorizing reviewer without requiring them to context-switch into the Security tab, and survives in `git log` after the dismissal record is later edited or the alert is reopened.

A Security-tab dismissal **without** a corresponding PR-description line does **not** satisfy G1b. The cross-reference is the visible-in-PR-record half of the gate. Conversely, a PR-description claim of dismissal **without** an actual Security-tab dismissal is a false attestation; reviewers must spot-check by running the G1b query in `Cut Mechanics` step 0 and confirming it returns `0` after the claimed dismissals.

This mirrors ADR-005's Dismiss-With-Comment Policy item 1 ("the comment becomes part of the alert record and is visible in future audits") and extends it to the release-cut surface so the same dismissal evidence is visible in two places: the Security tab (for security audits) and the PR description (for release-cut audits).

### Hotfix Path

Genuine production-blocking bugs, critical security advisories, and GHCR/branch-protection emergencies can bypass the release-branch mechanism via a **hotfix PR branched directly from `main`**, not from `dev`. Prose rules (per ADR-004 §4):

- **Branch name**: `hotfix/vX.Y.(Z+1)-description`: patch version increment by default. Minor bump permitted only with explicit PO authorization recorded in the hotfix PR description (e.g., schema change to mitigate a CVE).
- **Scope**: minimal. One bug or one advisory per hotfix; no opportunistic cleanup.
- **Gates that apply**:
  - G2, G3, G4 (tests + CodeQL): must pass as on any release cut. **Exception (G4 re-attribution waiver)**: if a security hotfix trips CodeQL delta-zero solely because its touched code is near a pre-existing `main` finding that CodeQL re-attributes to the new commit, the author may waive G4 with a linked Security-tab dismissal rationale in the PR description. Genuinely new HIGH/CRITICAL findings introduced by the hotfix code **cannot** be waived.
  - G1a applies only to bugs *regressed or introduced* by the hotfix. Pre-existing P0/P1s on `dev` are being bypassed intentionally because the hotfix is more urgent.
  - G1b applies only to findings *introduced* by the hotfix. The hotfix is often the remediation of a pre-existing finding and must not block itself.
  - G5 (CHANGELOG) applies with a hotfix-scoped entry.
  - G6 (version) applies as a patch bump.
  - G7 (no other main-bound PRs) applies; hotfix-has-priority tiebreaker.
- **Back-merge to `dev` within 24 hours**, manual, via a standard `dev`-targeting PR that merges the hotfix branch. Merge (not cherry-pick) preserves the hotfix commit chain in `dev`'s history and keeps bisection symmetric with the release-cut pattern.
- **Hotfixes should be rare.** Every hotfix is a signal the pre-cut gate failed. File a retro bead for each.
- **Mechanical ceiling**: if more than **two hotfixes** land between consecutive release cuts, a **mandatory incident review bead** must be filed and landed before the next release cut can proceed. This prevents the `hotfix/*` branch from becoming a de facto replacement for the release-cut PR (the PR #82 pattern with different branch names).

Step-by-step hotfix commands follow the same shell pattern as the release cut above, substituting `release/vX.Y.Z` with `hotfix/vX.Y.(Z+1)-description` and branching from `main` rather than a `dev` SHA. CHANGELOG entry goes under a hotfix-scoped version heading.

## Branch Protection on Main

`main` is protected (configured via bead `enhancedchannelmanager-8w33i`). Enforced rules:

- **Required status checks** (strict, branch must be up-to-date): `Backend Tests`, `Frontend Tests` (both in `.github/workflows/test.yml`), `CodeQL Analysis (python)` and `CodeQL Analysis (javascript-typescript)` (matrix in `.github/workflows/build.yml`), and `Release Cut Gate` (mechanical G1a/G1b/G5/G6/G7 verification in `.github/workflows/release-cut-gate.yml` per `bd-3d0tv`).
- **Force-pushes blocked** and **deletions blocked**.
- **Required conversation resolution** on PRs.
- **Admins are NOT enforced**: the PO can push hotfixes directly if a check outage would otherwise block a release. Use sparingly.
- **PR reviews are NOT required**: solo-maintainer workaround; add a review requirement when contributor count grows.
- **Linear history not required** and **signed commits not required**: matches current merge-commit-tolerant workflow.

To inspect or adjust: `gh api /repos/MotWakorb/enhancedchannelmanager/branches/main/protection`. Full config lives only in the GitHub API (no IaC yet).
