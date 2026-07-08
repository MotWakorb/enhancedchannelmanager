# Components

This project's deployment-tier inventory. See `_shared/deployment-tier.md` (Claude agent skills) for tier definitions. Produced by `/onboard` on 2026-07-08; owned by the PO.

## Components

| Component | Tier | Purpose |
|-----------|------|---------|
| ecm-core | home-lab | ECM backend (FastAPI monolith: routers, task engine, prober, TLS/ACME) + React SPA served from the same container |
| mcp-server | home-lab | Optional sidecar container exposing ECM tools to Claude over Streamable HTTP (static API key) |
| data-store | home-lab | SQLite `journal.db` + `settings.json` on the `ecm-config` volume; Alembic-managed schema |
| dbas-backup | home-lab | Backup/restore subsystem + cloud storage adapters. Highest-care home-lab component: restore must stay periodically exercised |
| ci-release | small-team | GitHub Actions workflows, release-cut gate, and published ghcr.io images (`ecm`, `ecm-mcp`) |
| docs | home-lab | Docs corpus (architecture, ADRs, runbooks, user guide). Re-evaluate when the public docs site (bead dg5dj / GH #381) ships |
| dev-tooling | home-lab | Test/E2E harnesses (pytest, vitest, Playwright), beads issue rig, observability/SLO scaffold |

## Notes

- **PO declaration (2026-07-08):** the deployment is home-lab — single operator, self-hosted Docker on a private LAN.
- **ci-release is deliberately small-team**, diverging from the blanket declaration: the ghcr.io images have unknown external consumers pulling `:latest` on a rolling release, and the project already operates this component at small-team rigor (release-cut gate, image push gated on tests, ADR-001/ADR-004 discipline). Tiering it small-team prevents future "simplification" back down.
- **mcp-server** stays home-lab but carries a security-posture note from onboarding: it is an AI-agent-facing network surface whose own auth implementation had not been code-reviewed as of the 2026-07-08 onboarding pass; the static API key is the sole control. Fine on a private LAN; revisit if port 6101 is ever exposed beyond it.
- **docs** stays home-lab for now; the technical writer proposed startup-leaning treatment for the future public GitHub Pages site. Revisit at dg5dj ship time.
- **Observability/SLO catalog** (`docs/sre/slos.md`) is kept as a scaffold. Per tier calibration, SLO burn must not gate or generate work at home-lab.
- Cross-tier rule: changes spanning ci-release and home-lab components are reviewed at small-team rigor (strictest-wins).
