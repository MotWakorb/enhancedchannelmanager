# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

## Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>         # Complete work
bd sync               # Sync with git
bd list --status closed  # View closed beads for historical context
```

## Development Workflow

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

4. **Run quality gates** (if code changed) - Tests, linters, builds

5. **Update the bead with work done** - Document what was changed:
   ```bash
   bd update <id> --description "Detailed description of changes made"
   ```

6. **Increment the version** - Use bug fix build number format (e.g., 0.3.10001):
   - Edit `frontend/package.json` to update the version
   - Build to verify: `cd frontend && npm run build`


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
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
- Always use `enhancedchannelmanager` as the repository name when creating beads
