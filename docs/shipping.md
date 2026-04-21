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

1. Bump version to release number (e.g., `0.12.0`) in `frontend/package.json`
2. Rebuild: `cd frontend && npm run build`
3. Commit version bump on dev, push
4. Merge dev to main — use a temporary worktree to avoid displacing dev from root:
   ```bash
   git worktree add /tmp/ecm-main main
   cd /tmp/ecm-main && git merge dev --no-edit && git push origin main
   git worktree remove /tmp/ecm-main
   ```
5. Create GitHub release: `gh release create vX.Y.Z --target main --title "vX.Y.Z" --notes-file /tmp/release-notes.md`
6. **Root checkout MUST stay on dev** — never leave it on main

## Branch Protection on Main

`main` is protected (configured via bead `enhancedchannelmanager-8w33i`). Enforced rules:

- **Required status checks** (strict, branch must be up-to-date): `Backend Tests`, `Frontend Tests` — both jobs defined in `.github/workflows/test.yml`.
- **Force-pushes blocked** and **deletions blocked**.
- **Required conversation resolution** on PRs.
- **Admins are NOT enforced** — the PO can push hotfixes directly if a check outage would otherwise block a release. Use sparingly.
- **PR reviews are NOT required** — solo-maintainer workaround; add a review requirement when contributor count grows.
- **Linear history not required** and **signed commits not required** — matches current merge-commit-tolerant workflow.

To inspect or adjust: `gh api /repos/MotWakorb/enhancedchannelmanager/branches/main/protection`. Full config lives only in the GitHub API (no IaC yet).
