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
|-|-|
| Architecture Diagram | `docs/architecture.md` |
| Project Architecture | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/architecture.md` |
| Beads (Issue Tracking) | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/beads.md` |
| CSS Guidelines | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/css-guidelines.md` |
| Dispatcharr API | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/dispatcharr-api.md` |
| Discord Release Notes | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/discord.md` |
| Testing Details | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/testing.md` |
| Shipping Workflow | `/home/lecaptainc/.claude/projects/-home-lecaptainc/memory/shipping.md` |

See `docs/architecture.md` for a full system architecture diagram (Mermaid).

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
- Frontend: `cd frontend && npm test && npm run build`

**Non-negotiable rules:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing — that leaves work stranded locally
- NEVER say "ready to push when you are" — YOU must push

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
