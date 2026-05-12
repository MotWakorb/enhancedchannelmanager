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
   bd create "Brief title" --description "Why this exists and what needs to be done"
   ```
   The first positional arg is the **title**, not the repo. Repo is auto-routed from `.beads/`. Don't pass `enhancedchannelmanager` as the title — that's the most common foot-gun.

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

- Beads auto-route to this repo via `.beads/`. If you ever need to override (e.g., creating a bead targeting a different rig), use `--repo enhancedchannelmanager`.
- **NEVER chain `bd create` and `bd close`** — run them as separate commands
- The `.git/beads-worktrees/dev` worktree is **only for beads issue tracking** (sparse checkout of `.beads/` only — no code files). Do NOT edit code there.

## Invoking Personas (project-engineer, qa-engineer, sre, etc.)

Personas are skills at `~/.claude/skills/<persona>/SKILL.md`, NOT subagent types. To spawn them — especially in parallel — use the Agent tool with `subagent_type: "general-purpose"` and load the persona identity in the prompt:

```
Read ~/.claude/skills/<persona>/SKILL.md for your domain scope.
Read ~/.claude/skills/<persona>/identity.md (if present).
Read ~/.claude/skills/_shared/engineering-discipline.md.

You are the <Persona>. <question/task>
```

The canonical pattern is documented in `~/.claude/skills/spike/SKILL.md` (Step 3). Do NOT try `subagent_type: "project-engineer"` — the registered subagent types are only `general-purpose`, `Explore`, `Plan`, `claude-code-guide`, `statusline-setup`.

For multi-persona workflows (team-plan, team-review, spike, grooming, standup, retro, onboard), invoke the orchestrating skill via the Skill tool — it handles the fan-out itself.

## Sizing Vocabulary — No Calendar Estimates

Size work as **Small / Medium / Large / Epic — needs decomposition** (per `~/.claude/skills/grooming/SKILL.md`). Do NOT give the PO calendar estimates in hours/days/weeks/months. Calendar estimates invite commitment theater and are almost always wrong.

Exception — governance cadence rules from ADRs (e.g., ADR-005's monthly-then-quarterly audit cadence) are project-defined constraints and can be quoted verbatim. Do not multiply them out into wall-time estimates. Quote the rule; let the PO do the arithmetic if they want it.

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
| **Style Guide (canonical)** | `docs/style_guide.md` |
| CSS Guidelines | `docs/css_guidelines.md` |
| Beads (Issue Tracking) | `~/.claude/projects/<project-slug>/memory/beads.md` |
| Dispatcharr API | `docs/dispatcharr_api.md` |
| Discord Release Notes | `docs/discord_release_notes.md` |
| Frontend Lint Policy | `docs/frontend_lint.md` |
| Testing Details | `docs/testing.md` |
| Shipping Workflow | `docs/shipping.md` |
| Dummy EPG Template Engine | `docs/template_engine.md` |
| DBAS Import Threat Model | `docs/security/threat_model_dbas_import.md` |
| CodeQL Configuration | `docs/security/codeql-config.md` |
| Normalization (user + dev guide) | `docs/normalization.md` |
| Auto-Creation Rule Analyzer | `docs/auto_creation_rule_analyzer.md` |
| Versioning Scheme | `docs/versioning.md` |
| API Reference | `docs/api.md` |
| SLOs | `docs/sre/slos.md` |
| User Guide (operator-facing) | `docs/user_guide/` |
| Graphify findings (past traces) | `graphify-out/memory/*.md` |
| Graph audit report | `graphify-out/GRAPH_REPORT.md` |

## Architecture Questions

For codebase-architecture questions (how X connects to Y, what a component's role is, where the hot path runs), the order of precedence is:

1. **`docs/architecture.md`** — the hand-curated system overview + auto-creation pipeline internals + MCP + external API contract.
2. **`graphify-out/memory/*.md`** — saved Q&A from past graph traces. Each file is one question + answer. Greppable. Cheap to read.
3. **Rebuild the graph** only if (1) and (2) don't cover it: `/graphify backend frontend docs`. Then query via `graphify query "..."` / `graphify explain "NodeName"` / `graphify path "A" "B"`.

The raw `graph.json` and `cross-repo-graph.json` files are gitignored (large, machine-local paths). Rebuild on demand.

**For coding conventions** (naming, module organization, comments, error
handling, regex, CSS, lint, tests), `docs/style_guide.md` is the canonical
reference. Other guides in this table cover their own subject (CSS shared
classes, lint per-rule patterns, etc.) and are cited from the style guide
where they remain authoritative.

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

Follow `docs/shipping.md`. The full PR-driven flow (branch from `origin/dev`, push, open PR via `gh pr create --base dev`, wait for the 5 required checks, then `gh pr merge --merge --delete-branch`) lives in `docs/shipping.md` §6 — do not duplicate it here.

**Non-negotiable rules:**
- Work is NOT complete until the PR merges into `dev`
- NEVER stop before the PR is merged — an open PR is not a shipped change
- NEVER say "ready to merge when you are" — YOU must drive the merge once the required checks are green
