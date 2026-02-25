# GEMINI.md - Enhanced Channel Manager

This document provides essential context, architectural overviews, and development workflows for the Enhanced Channel Manager (ECM) project.

## Project Overview

**Enhanced Channel Manager (ECM)** is a professional-grade web interface for managing IPTV configurations in conjunction with Dispatcharr. It features a tab-based interface for channel management, EPG data, logos, auto-creation rules, and a visual FFmpeg command builder.

### Core Technologies
- **Frontend:** React 18, TypeScript, Vite, @dnd-kit (Drag-and-Drop), mpegts.js (Video Playback), Recharts (Analytics).
- **Backend:** Python 3.x, FastAPI, SQLAlchemy (SQLite), Modular Router Architecture.
- **Infrastructure:** Docker (Single container serving both frontend as static files and backend API).
- **Upstream Integration:** Dispatcharr API.

---

## Architecture & Structure

### Directory Map
- `backend/`: FastAPI application, modular routers (`routers/`), models, and services.
- `frontend/`: React source code, components, and hooks.
- `e2e/`: Playwright end-to-end test suites.
- `docs/`: Architectural diagrams and detailed documentation.
- `scripts/`: Quality gates, utility scripts, and automation.
- `config/`: (Runtime) Persistence for SQLite database and settings.

### System Architecture
ECM operates as a proxy/manager for Dispatcharr. It maintains its own SQLite database (`ecm.db`) for audit logs (Journal), popularity stats, and local configurations while syncing channel and stream data with the upstream Dispatcharr instance.

---

## Development Workflows

### Standard Operating Procedures
1. **Branching:** Always work directly on the `dev` branch.
2. **Issue Tracking:** Use the `bd` (beads) tool for task management (`bd ready`, `bd create`, `bd close`).
3. **Iteration:** Employ a "Container-First" approach. Modify code locally, then use `docker cp <file> ecm-ecm-1:/app/<path>` to test changes in the live environment before committing.
4. **Quality Gates:** Never commit without passing the quality gates.

### Key Commands

#### Development Environment
- **Frontend Dev:** `cd frontend && npm install && npm run dev`
- **Backend Dev:** `cd backend && pip install -r requirements.txt && uvicorn main:app --reload`
- **Full Stack (Docker):** `docker compose up --build`

#### Testing & Validation
- **Quality Gates:** `./scripts/quality-gates.sh` (Runs all checks: syntax, unit tests, FE build, E2E).
- **Backend Tests:** `cd backend && python -m pytest tests/`
- **Frontend Tests:** `cd frontend && npm test`
- **E2E Tests:** `npm run test:e2e` (Playwright)

#### Utilities
- **Password Reset:** `docker exec -it enhancedchannelmanager python /app/reset_password.py`
- **Search Streams:** `./scripts/search-stream.sh <url> <user> <pass> "<query>"`

---

## Engineering Standards

### Backend Conventions
- **Modular Routers:** Logic is split into ~20 domain-specific routers in `backend/routers/`. Avoid bloating `main.py`.
- **Log Security:** Use `log_utils` for sanitized logging (CWE-117 protection).
- **Database:** Use SQLAlchemy ORM for all database interactions.

### Frontend Conventions
- **State Management:** Centralized state in `App.tsx`. Use custom hooks for complex logic.
- **Styling:** Follow the CSS design token system for theme consistency (Dark/Light/High Contrast).
- **Lazy Loading:** Tabs are lazy-loaded to optimize performance.

### Testing Mandates
- **Empirical Reproduction:** Always reproduce a bug with a test case (E2E or unit) before applying a fix.
- **Regression Testing:** Ensure `scripts/quality-gates.sh` passes in its entirety before any "Ship the fix" directive is considered complete.

---

## Security & Safety
- **Credentials:** Never commit `.env` or configuration files.
- **TLS:** Management is handled via the `tls/` router and Let's Encrypt integration.
- **Authentication:** JWT-based sessions. All API endpoints (except health/auth) require valid tokens.

---

## Context Management (Agent Specific)
- **Subagent Usage:** Prefer subagents for tasks over ~5 tool calls if the total context exceeds 50k tokens.
- **File Reading:** Use `grep_search` to target specific sections of large files before reading.
- **Response Format:** Be concise. Prioritize technical rationale and outcomes. Use Markdown tables with minimum separators (`|-|-|`).
