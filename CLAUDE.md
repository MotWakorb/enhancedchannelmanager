# Agent Instructions

This project uses **bd** (beads) for issue tracking. Run `bd onboard` to get started.

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

## Reference Guides

| Guide | Location |
|---|---|
| Project Architecture | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/architecture.md` |
| Beads (Issue Tracking) | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/beads.md` |
| CSS Guidelines | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/css-guidelines.md` |
| Dispatcharr API | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/dispatcharr-api.md` |
| Discord Release Notes | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/discord.md` |
| Testing Details | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/testing.md` |
| Shipping Workflow | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/shipping.md` |

## Development Workflow

**Always work from the `dev` branch.** The root checkout is on `dev`. All edits, builds, and deploys happen here — no worktrees.

### Container-First Development

We iterate fast by deploying to the live container before committing:

1. **Check closed beads** for context on past work: `bd list --status closed`
2. **Create a bead**: `bd create enhancedchannelmanager "Brief description"`
3. **Edit code** locally
4. **Copy to container and test**:
   ```bash
   docker cp <local-file> ecm-ecm-1:/app/<destination-path>
   ```
   Repeat steps 3-4 until the fix works. Do NOT commit until told to "ship the fix."

**Python packages** use `uv` (not pip): `docker exec ecm-ecm-1 uv pip install <package>`

### Shipping (When User Says "Ship the Fix")

Follow `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/shipping.md`.

Summary: Quality gates → Update bead → Bump version → Rebuild → Close bead → Update README if needed → Commit → Push to dev → File follow-up beads.

**Quality gate commands:**
- Backend: `python -m py_compile backend/main.py && cd backend && python -m pytest tests/ -q`
- Frontend: `cd frontend && npm run lint && npm test && npm run build`

**Non-negotiable rules:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push
