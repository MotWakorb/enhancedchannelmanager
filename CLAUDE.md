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
| Project Architecture | `/home/user/.claude/projects/-home-user/memory/architecture.md` |
| Beads (Issue Tracking) | `/home/user/.claude/projects/-home-user/memory/beads.md` |
| CSS Guidelines | `/home/user/.claude/projects/-home-user/memory/css-guidelines.md` |
| Dispatcharr API | `/home/user/.claude/projects/-home-user/memory/dispatcharr-api.md` |
| Discord Release Notes | `/home/user/.claude/projects/-home-user/memory/discord.md` |
| Testing Details | `/home/user/.claude/projects/-home-user/memory/testing.md` |
| Shipping Workflow | `/home/user/.claude/projects/-home-user/memory/shipping.md` |

See `docs/architecture.md` for a full system architecture diagram (Mermaid).

## Development Workflow

**Always work from the `dev` branch.** The root checkout at `/home/user/ecm/enhancedchannelmanager` is on `dev`. All edits, builds, and deploys happen here — no worktrees.

- Container name: `enhancedchannelmanager-ecm-1`

### Container-First Development

We iterate fast by deploying to the live container before committing:

1. **Create a bead** (see "STOP" section above — must happen before any code exploration)
2. **Edit code** locally
3. **Copy to container and test**:
   ```bash
   docker cp <local-file> enhancedchannelmanager-ecm-1:/app/<destination-path>
   ```
   Repeat steps 2-3 until the fix works. Do NOT commit until told to "ship the fix."

**Frontend deploy:**
```bash
cd /home/user/ecm/enhancedchannelmanager/frontend && npm run build
docker exec enhancedchannelmanager-ecm-1 sh -c 'rm -rf /app/static/assets/*'
docker cp dist/. enhancedchannelmanager-ecm-1:/app/static/
```
Always clean `/app/static/assets/` before copying — `docker cp` only adds files, never removes stale bundles.

**Backend deploy** (to `/app/`, NOT `/app/backend/` — entrypoint runs `cd /app && uvicorn main:app`):
```bash
docker cp backend/main.py enhancedchannelmanager-ecm-1:/app/main.py
docker cp backend/routers/. enhancedchannelmanager-ecm-1:/app/routers/
docker restart enhancedchannelmanager-ecm-1   # No --reload; restart required
```

**Python packages** use `uv` (not pip): `docker exec enhancedchannelmanager-ecm-1 uv pip install <package>`

### Shipping (When User Says "Ship the Fix")

Follow `/home/user/.claude/projects/-home-user/memory/shipping.md`.

Summary: Quality gates → Update bead → Bump version → Rebuild → Close bead → Update README if needed → Commit → Push to dev → File follow-up beads.

**Quality gate commands:**
- Backend: `python -m py_compile backend/main.py && cd backend && python -m pytest tests/ -q --tb=short 2>&1 | tail -1`
- Frontend: `cd frontend && npm test && npm run build`

**Non-negotiable rules:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push

## Backend Architecture

- **Modular routers**: `backend/routers/` has 20+ domain-focused modules (channels, m3u, epg, settings, etc.)
- **Router registry**: `routers/__init__.py` has `all_routers` list; `main.py` includes them via `app.include_router()`
- **main.py** retains: app lifecycle, middleware, auth, startup/shutdown
- **Mock patches**: When testing router endpoints, patch `routers.<module>.X` not `main.X`
- **Auth middleware**: Global middleware in `main.py` blocks unauthenticated `/api/*` requests. All endpoints are secure by default. To make an endpoint public, add its path to `AUTH_EXEMPT_PATHS` in `main.py`. Respects `RequireAuthIfEnabled` semantics (skips enforcement when auth disabled or setup not complete).

## Dev Branch Patterns

- Use `CustomSelect` component (not native `<select>`) for dropdowns
- Actions/conditions use `order-number-input` for the reorder number in card headers only
- Form fields in action editor use `action-input` CSS class (not `order-number-input`)
- `getChannelGroups()` is already imported in ActionEditor for the Target Group selector

## Types Reference

- `EPGSource` type: `frontend/src/types/index.ts` (has `id`, `name`, etc.)
- `Action` interface: `frontend/src/types/autoCreation.ts` (has `epg_id?: number`, `channel_number?: string | number`)
- API: `getEPGSources()` in `frontend/src/services/api.ts`

## Context Efficiency

### Subagent Discipline

**Context-aware delegation:**
- Under ~50k context: prefer inline work for tasks under ~5 tool calls.
- Over ~50k context: prefer subagents for self-contained tasks, even simple ones — the per-call token tax on large contexts adds up fast.

When using subagents, include output rules: "Final response under 2000 characters. List outcomes, not process."
Never call TaskOutput twice for the same subagent. If it times out, increase the timeout — don't re-read.

### File Reading

Read files with purpose. Before reading a file, know what you're looking for.
Use Grep to locate relevant sections before reading entire large files.
Never re-read a file you've already read in this session.
For files over 500 lines, use offset/limit to read only the relevant section.

### Responses

Don't echo back file contents you just read — the user can see them.
Don't narrate tool calls ("Let me read the file..." / "Now I'll edit..."). Just do it.
Keep explanations proportional to complexity. Simple changes need one sentence, not three paragraphs.

**Tables — STRICT RULES (apply everywhere, always):**
- Markdown tables: use minimum separator (`|-|-|`). Never pad with repeated hyphens (`|---|---|`).
- NEVER use box-drawing / ASCII-art tables with characters like `┌`, `┬`, `─`, `│`, `└`, `┘`, `├`, `┤`, `┼`. These are completely banned.
- No exceptions. Not for "clarity", not for alignment, not for terminal output.
