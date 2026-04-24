# Agent Instructions

## STOP — Read Beads, Then Create a Bead

Before reading code, editing files, or exploring the codebase for ANY code task:

1. **Read existing beads** for context on past work:
   ```bash
   bd list --status closed
   bd ready
   ```
2. **Create a bead** for the current task:
   ```bash
   bd create enhancedchannelmanager "Brief description"
   ```

No exceptions. No "I'll do it later." The bead comes before the first Read, Grep, or Edit.
After the work is deployed and verified, close it: `bd close <bead-id>`

## Beads Quick Reference

```bash
bd ready                      # Find available work
bd show <id>                  # View issue details
bd update <id> --status in_progress  # Claim work
bd close <id>                 # Complete work
bd list --status closed       # View closed beads for context
bd sync                       # Sync beads data only (NOT for code commits)
```

- Always use `enhancedchannelmanager` as the repository name
- **NEVER chain `bd create` and `bd close`** — run them as separate commands
- The `.git/beads-worktrees/dev` worktree is **only for beads issue tracking** (sparse checkout of `.beads/` only — no code files). Do NOT edit code there.

## Reference Guides

| Guide | Location |
|-|-|
| Architecture Diagram | `docs/architecture.md` |
| Auth Middleware | `docs/auth_middleware.md` |
| Backend Architecture | `docs/backend_architecture.md` |
| Database Migrations | `docs/database_migrations.md` |
| Pytest Conventions | `docs/pytest_conventions.md` |
| Project Architecture | `docs/project_architecture.md` |
| Runbooks | `docs/runbooks/` |
| CSS Guidelines | `docs/css_guidelines.md` |
| Beads (Issue Tracking) | `~/.claude/projects/<project-slug>/memory/beads.md` |
| Dispatcharr API | `docs/dispatcharr_api.md` |
| Discord Release Notes | `docs/discord_release_notes.md` |
| Frontend Lint Policy | `docs/frontend_lint.md` |
| Testing Details | `docs/testing.md` |
| Shipping Workflow | `docs/shipping.md` |
| Dummy EPG Template Engine | `docs/template_engine.md` |
| DBAS Import Threat Model | `docs/security/threat_model_dbas_import.md` |
| Normalization (user + dev guide) | `docs/normalization.md` |
| Versioning Scheme | `docs/versioning.md` |
| API Reference | `docs/api.md` |
| SLOs | `docs/sre/slos.md` |
| User Guide (operator-facing) | `docs/user_guide/` |

## Development Workflow

**Always work from the `dev` branch.** Container name: `ecm-ecm-1`

### Container-First Development

Edit code locally, deploy to container, iterate. Do NOT commit until told to "ship the fix."

```bash
docker cp <local-file> ecm-ecm-1:/app/<destination-path>
```

**Frontend deploy:**
```bash
cd frontend && npm run build
docker exec ecm-ecm-1 sh -c 'rm -rf /app/static/assets/*'
docker cp dist/. ecm-ecm-1:/app/static/
```
Always clean `/app/static/assets/` before copying — `docker cp` only adds files, never removes stale bundles.

**Backend deploy** (to `/app/`, NOT `/app/backend/`):
```bash
docker cp backend/main.py ecm-ecm-1:/app/main.py
docker cp backend/routers/. ecm-ecm-1:/app/routers/
docker restart ecm-ecm-1
```

**Python packages** use `uv` (not pip): `docker exec ecm-ecm-1 uv pip install <package>`

### Shipping (When User Says "Ship the Fix")

Follow `docs/shipping.md`.

**Non-negotiable rules:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push
