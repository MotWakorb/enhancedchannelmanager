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

### Fast Development Workflow - CRITICAL

**IMPORTANT: To speed up development, we use a container-first workflow:**

1. **Copy work to container FIRST** - Before any git commit, copy changed files to the `ecm-ecm-1` container where the live application runs:
   - The application lives in `/app` in the container (NOT `/app/static`)
   - Test changes in the running container immediately
   - Iterate quickly without git commits

2. **Only commit when told to "ship the fix"** - Do NOT create git commits until the user explicitly says to ship/commit the fix:
   - This allows rapid iteration and testing
   - Commits happen only after the fix is verified working in the container
   - User will explicitly tell you when to proceed with the commit workflow

**Container Copy Command:**
```bash
docker cp <local-file> ecm-ecm-1:/app/<destination-path>
```

**Python Package Management:**
This project uses **uv** (not pip) for Python package management. When installing packages in the container:
```bash
docker exec ecm-ecm-1 uv pip install <package>
```

When doing work on this project, follow these steps in order:

**During Development (iterative, fast cycle):**

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

4. **Copy to container and test** - Copy changed files to the `ecm-ecm-1` container and verify they work:
   ```bash
   docker cp <local-file> ecm-ecm-1:/app/<destination-path>
   ```
   - Test in the running container
   - Iterate on changes as needed
   - Repeat steps 3-4 until the fix works

**When User Says "Ship the Fix" (commit cycle):**

5. **Run quality gates** (if code changed) - **MANDATORY** before committing:
   ```bash
   # Backend: Python syntax check + unit/integration tests (REQUIRED for backend changes)
   python -m py_compile backend/main.py
   cd backend && python -m pytest tests/ -q

   # Frontend: Unit tests + TypeScript compilation (REQUIRED for frontend changes)
   cd frontend && npm test && npm run build
   ```
   **CRITICAL**: If syntax checks or tests fail, fix errors before proceeding. Never commit broken code.

   **Test Coverage:**
   - **Backend**: Unit tests (fast, isolated) + Integration tests (with database/APIs)
   - **Frontend**: Hook tests, service tests, component tests
   - **E2E**: Run on merge to main only (not during regular dev workflow)

6. **Update the bead with work done** - Document what was changed:
   ```bash
   bd update <id> --description "Detailed description of changes made"
   ```

7. **Increment the version** - Use bug fix build number format (e.g., 0.7.3-0094):
   - Edit `frontend/package.json` to update the version
   - **Re-run quality gates** to verify with new version:
     ```bash
     python -m py_compile backend/main.py  # If backend changed
     cd frontend && npm run build          # Always run for version change
     ```


8. **Close the bead**:
   ```bash
   bd close <id>
   ```

9. **Update README.md if needed** - If the change adds, removes, or modifies a feature, update the documentation

10. **Push updates to dev branch** - This is MANDATORY:
   ```bash
   git add backend/main.py frontend/package.json  # Add only changed files (or use -A for all)
   git commit -m "v0.x.xxxxx: Brief description"
   git push origin dev
   git status  # MUST show "up to date with origin"
   ```
   **NOTE:** Do NOT run `bd sync` as part of code commits. `bd sync` is ONLY for syncing beads issue tracking data and creates its own separate commit.

11. **File beads for remaining work** - Create beads for anything that needs follow-up

**CRITICAL SHIPPING RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
- Always use `enhancedchannelmanager` as the repository name when creating beads
- **NEVER chain `bd create` and `bd close` in one command** - The `bd list` output format doesn't work with shell parsing. Always run them as separate commands:
  ```bash
  bd create enhancedchannelmanager "Description"  # Note the ID it prints
  bd close <id>                                    # Use the exact ID from above
  ```

## Testing Guidelines

**Test Infrastructure Overview:**

This project has comprehensive test coverage at three levels:

### 1. Backend Tests (Python/pytest)
Located in `backend/tests/`, run with `cd backend && python -m pytest tests/ -q`

**Unit Tests** (`backend/tests/unit/`):
- `test_journal.py` - Journal logging system
- `test_cache.py` - Caching mechanisms
- `test_schedule_calculator.py` - Schedule calculations
- `test_cron_parser.py` - Cron expression parsing
- `test_alert_methods.py` - Alert method logic

**Integration Tests** (`backend/tests/integration/`):
- `test_api_settings.py` - Settings API endpoints
- `test_api_tasks.py` - Task scheduler API endpoints
- `test_api_notifications.py` - Notification API endpoints
- `test_api_alert_methods.py` - Alert methods API endpoints

### 2. Frontend Tests (Vitest)
Located in `frontend/src/`, run with `cd frontend && npm test`

- `hooks/useChangeHistory.test.ts` - Change history tracking hook
- `hooks/useAsyncOperation.test.ts` - Async operation management hook
- `hooks/useSelection.test.ts` - Selection state management hook
- `services/api.test.ts` - API service layer

### 3. E2E Tests (Playwright)
Located in `e2e/`, run with `npm run test:e2e` from root

**Test Coverage:**
- `smoke.spec.ts` - Basic smoke tests
- `channels.spec.ts` - Channel management workflows
- `m3u-manager.spec.ts` - M3U playlist management
- `epg-manager.spec.ts` - EPG data management
- `logo-manager.spec.ts` - Logo management
- `guide.spec.ts` - TV guide functionality
- `tasks.spec.ts` - Scheduled tasks
- `settings.spec.ts` - Application settings
- `journal.spec.ts` - Journal/logging
- `stats.spec.ts` - Statistics and analytics
- `alert-methods.spec.ts` - Alert notification methods

**Running E2E Tests:**
```bash
npm run test:e2e           # Headless mode (CI/CD)
npm run test:e2e:ui        # Interactive UI mode
npm run test:e2e:headed    # Run in visible browser
npm run test:e2e:debug     # Debug mode with breakpoints
npm run test:e2e:report    # View test report
```

**When to Run Tests:**
- **Backend tests**: MANDATORY for any backend code changes
- **Frontend tests**: MANDATORY for any frontend code changes
- **E2E tests**: Run on merge to main only (CI/CD pipeline)

## CSS/Styling Guidelines

**Theme-Aware Variables - CRITICAL:**

The `--accent-*` variables have OPPOSITE meanings in dark vs light mode:
- Dark mode: `--accent-primary` = white, `--accent-secondary` = light gray
- Light mode: `--accent-primary` = indigo, `--accent-secondary` = lighter indigo

**NEVER use `--accent-primary` or `--accent-secondary` for:**
- Backgrounds with text on top (causes white-on-white in dark mode)
- Badge/tag backgrounds
- Any element that needs text contrast

**Safe variables for backgrounds:**
```css
/* Use these for backgrounds */
--bg-primary      /* Main background */
--bg-secondary    /* Slightly different background */
--bg-tertiary     /* Cards, chips, tags */
--input-bg        /* Form inputs */
--button-primary-bg  /* Primary buttons */
```

**Safe variables for text:**
```css
/* Use these for text */
--text-primary    /* Main text */
--text-secondary  /* Secondary/label text */
--text-muted      /* Disabled/subtle text */
--button-primary-text  /* Text on primary buttons */
```

**Correct patterns:**
```css
/* Tags, chips, badges */
.tag {
  background: var(--bg-tertiary);
  color: var(--text-primary);
  border: 1px solid var(--border-primary);
}

/* Primary buttons */
.btn-primary {
  background-color: var(--button-primary-bg);
  color: var(--button-primary-text);
}

/* Secondary/subtle badges */
.badge {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}
```

**WRONG - will cause contrast issues:**
```css
/* DON'T DO THIS */
.tag {
  background: var(--accent-secondary);  /* Light in dark mode! */
  color: var(--accent-primary);         /* White in dark mode! */
}
```
