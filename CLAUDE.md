# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync beads data only (NOT for code commits)
bd list --status closed  # View closed beads for historical context
```

## Development Workflow

**IMPORTANT: Always work from the `dev` branch, never from `main`.**

The `dev` branch is checked out in a beads worktree at:
```
/home/lecaptainc/ecm/enhancedchannelmanager/.git/beads-worktrees/dev
```

At the start of any session, ensure you're working in the dev worktree or switch to it:
```bash
cd /home/lecaptainc/ecm/enhancedchannelmanager/.git/beads-worktrees/dev
```

When doing work on this project, follow these steps in order:

1. **Check closed beads for historical context** - Review closed beads to understand features implemented, past bugs fixed, and avoid repeating mistakes:
   ```bash
   bd list --status closed
   bd show <id>  # View details of relevant past work
   ```

2. **Create a bead for all work** - Always create a bead before starting work. Use the proper repository name `enhancedchannelmanager`:
   ```bash
   bd create enhancedchannelmanager "Brief description of work"
   ```

3. **Update the code** - Make the necessary changes to implement the feature or fix

4. **Run quality gates** (if code changed) - **MANDATORY** before committing:
   ```bash
   # Backend Python syntax check (REQUIRED for any backend changes)
   python -m py_compile backend/main.py

   # Frontend TypeScript compilation and build (REQUIRED for any frontend changes)
   cd frontend && npm run build

   # Or run all quality gates at once:
   ./scripts/quality-gates.sh
   ```
   **CRITICAL**: If syntax checks fail, fix errors before proceeding. Never commit broken code.

5. **Update the bead with work done** - Document what was changed:
   ```bash
   bd update <id> --description "Detailed description of changes made"
   ```

6. **Increment the version** - Use bug fix build number format (e.g., 0.7.3-0094):
   - Edit `frontend/package.json` to update the version
   - **Re-run quality gates** to verify with new version:
     ```bash
     python -m py_compile backend/main.py  # If backend changed
     cd frontend && npm run build          # Always run for version change
     ```


7. **Close the bead**:
   ```bash
   bd close <id>
   ```

8. **Update README.md if needed** - If the change adds, removes, or modifies a feature, update the documentation

9. **Push updates to dev branch**:
   ```bash
   git add -A
   git commit -m "v0.x.xxxxx: Brief description"
   git push origin dev
   ```

10. **File beads for remaining work** - Create beads for anything that needs follow-up

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push origin dev
   git status  # MUST show "up to date with origin"
   ```
   **NOTE:** Do NOT run `bd sync` as part of code commits. `bd sync` is ONLY for syncing beads issue tracking data and creates its own separate commit. Run it independently if needed to sync issue status.
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
- Always use `enhancedchannelmanager` as the repository name when creating beads

## CSS/Styling Guidelines

**Button Styling - IMPORTANT:**
- NEVER use `--accent-primary` for button backgrounds with white/light text - it causes white-on-white in dark mode
- ALWAYS use `--button-primary-bg` for primary button backgrounds
- ALWAYS use `--button-primary-text` for primary button text color
- Example of correct button styling:
  ```css
  .btn-primary {
    background-color: var(--button-primary-bg);
    color: var(--button-primary-text);
  }
  ```
