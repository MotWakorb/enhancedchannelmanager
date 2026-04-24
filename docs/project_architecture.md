# Project Architecture

Enhanced Channel Manager (ECM) — a web app for managing IPTV channels via Dispatcharr.

## Stack

| Layer | Tech |
|---|---|
| Backend | Python 3.12 / FastAPI (modular: `main.py` core + `routers/` modules) |
| Frontend | React 18 / TypeScript / Vite |
| Database | SQLite via SQLAlchemy (`/config/journal.db`) |
| Container | Docker (single container `ecm-ecm-1`) |
| Testing | pytest (backend), Vitest (frontend), Playwright (E2E) |
| Packages | `uv` (Python), `npm` (JS) |

## Container Layout

```
/app/                    ← WORKDIR, backend code lives here
/app/static/             ← Built frontend (Vite output copied here)
/app/static/assets/      ← JS/CSS bundles
/config/                 ← Persistent volume (settings.json, journal.db, TLS certs)
```

Ports: `6100` (HTTP), `6143` (HTTPS when TLS enabled)

## How the SPA is Served

1. Vite builds frontend to `frontend/dist/`
2. Dockerfile copies `dist/` → `/app/static/`
3. FastAPI mounts `/assets` as static files
4. Catch-all route `/{full_path:path}` serves `index.html` for all non-API routes

## Backend (`backend/`)

**Modular router architecture** (v0.13.0 refactor) — `main.py` handles app lifecycle, middleware, and WebSocket; domain endpoints live in `routers/` modules.

### Router Modules (`backend/routers/`)

| Module | Endpoints | Description |
|---|---|---|
| `channels.py` | 22 | Channel CRUD, CSV import/export, bulk ops, logos |
| `channel_groups.py` | 10 | Group management, orphan cleanup, hidden groups |
| `m3u.py` | 24 | M3U accounts, filters, profiles, refresh, server groups |
| `m3u_digest.py` | 4 | Change detection, digest settings, test digest |
| `epg.py` | 12 | EPG sources, data, grid, LCN lookup |
| `settings.py` | 8 | App configuration, connection test, service restart |
| `tasks.py` | 16 | Task engine, cron, schedules |
| `ffmpeg.py` | 16+ | FFMPEG builder, profiles, probing |
| `auto_creation.py` | 15+ | Rule-based channel auto-creation |
| `stream_stats.py` | 10+ | Stream probing and health |
| `stream_preview.py` | 3 | Live stream/channel preview |
| `notifications.py` | 7 | In-app notification system |
| `alert_methods.py` | 7 | Discord, SMTP, Telegram alerts |
| `stats.py` | 10+ | Channel stats, bandwidth, watch history |
| `tags.py` | 9 | Tag groups and tag engine |
| `profiles.py` | 10 | Channel/stream profile management |
| `normalization.py` | 5+ | Stream name normalization rules |
| `journal.py` | 3 | Activity logging |
| `health.py` | 3 | Health check, cache stats |
| `streams.py` | 5 | Stream listing, providers |

All routers are registered via `routers/__init__.py` → `all_routers` list, included in `main.py` with `app.include_router()`.

### API Route Groups

| Prefix | Tag | Purpose |
|---|---|---|
| `/api/health` | Health | Health check, request rates |
| `/api/settings` | Settings | App configuration |
| `/api/channels` | Channels | Channel CRUD, CSV import/export, bulk operations |
| `/api/channel-groups` | Channel Groups | Group management, orphan cleanup |
| `/api/streams` | Streams | Stream listing |
| `/api/m3u/` | M3U | M3U accounts, filters, profiles, refresh, VOD |
| `/api/m3u/changes` | M3U Digest | Change detection and digest notifications |
| `/api/epg/` | EPG | EPG sources, data, grid, LCN |
| `/api/channel-profiles` | Channel Profiles | Profile management |
| `/api/stream-profiles` | Stream Profiles | Stream profile listing |
| `/api/journal` | Journal | Activity logging |
| `/api/notifications` | Notifications | In-app notification system |
| `/api/alert-methods` | Alert Methods | Discord, SMTP, Telegram alerts |
| `/api/stats/` | Stats | Channel stats, bandwidth, watch history, popularity |
| `/api/stream-stats` | Stream Stats | Stream probing and health |
| `/api/tasks` | Tasks | Scheduled task engine |
| `/api/cron/` | Cron | Cron expression presets/validation |
| `/api/normalization/` | Normalization | Stream name normalization rules |
| `/api/tags/` | Tags | Tag groups and tag engine |
| `/api/auto-creation/` | Auto-Creation | Rule-based channel auto-creation |
| `/api/stream-preview/` | Stream Preview | Live stream preview |

### Key Backend Modules

| File | Purpose |
|---|---|
| `routers/__init__.py` | Router registry (`all_routers` list) |
| `config.py` | Settings management (`/config/settings.json`) |
| `database.py` | SQLAlchemy setup (SQLite) |
| `models.py` | Database models |
| `journal.py` | Activity journal logging |
| `cache.py` | In-memory caching |
| `dispatcharr_client.py` | Dispatcharr API client |
| `bandwidth_tracker.py` | Real-time bandwidth tracking |
| `stream_prober.py` | FFmpeg-based stream health probing |
| `popularity_calculator.py` | Channel popularity scoring |
| `m3u_change_detector.py` | M3U playlist change detection |
| `normalization_engine.py` | Stream name normalization |
| `auto_creation_engine.py` | Auto-creation rule engine |
| `auto_creation_evaluator.py` | Condition evaluation |
| `auto_creation_executor.py` | Action execution |
| `auto_creation_schema.py` | Rule schema definitions |
| `task_engine.py` / `task_scheduler.py` | Background task scheduling |
| `cron_parser.py` | Cron expression parsing |
| `alert_methods.py` | Alert method base + manager |
| `alert_methods_discord.py` | Discord webhook alerts |
| `alert_methods_smtp.py` | Email alerts |
| `alert_methods_telegram.py` | Telegram bot alerts |
| `auth/` | Authentication (JWT tokens, password hashing, admin routes) |
| `tls/` | HTTPS/TLS (ACME client, cert renewal, DNS providers) |
| `tasks/` | Scheduled task implementations (M3U refresh, EPG refresh, cleanup, etc.) |

## Frontend (`frontend/src/`)

### Entry Points

| File | Purpose |
|---|---|
| `main.tsx` | React mount point |
| `App.tsx` | Root component — state management, tab routing, data loading |
| `App.css` | Global styles, `.tab-loading`, `.main` layout |
| `index.css` | Design tokens (CSS variables for theme, spacing, typography) |
| `TabNavigation.tsx` | Tab bar component |

### Tab Components (`components/tabs/`)

ChannelManagerTab loads eagerly; all others are lazy-loaded via `React.lazy()`:

| Tab | File | Sub-panels |
|---|---|---|
| Channels | `ChannelManagerTab.tsx` | — |
| M3U Manager | `M3UManagerTab.tsx` | — |
| EPG Manager | `EPGManagerTab.tsx` | — |
| Guide | `GuideTab.tsx` | — |
| Logo Manager | `LogoManagerTab.tsx` | — |
| M3U Changes | `M3UChangesTab.tsx` | — |
| Journal | `JournalTab.tsx` | — |
| Stats | `StatsTab.tsx` | BandwidthPanel, EnhancedStatsPanel, PopularityPanel, WatchHistoryPanel |
| Settings | `SettingsTab.tsx` | AuthSettingsSection, LinkedAccountsSection, TagEngineSection, NormalizationEngineSection, TLSSettingsSection, UserManagementSection |
| Auto-Creation | `AutoCreationTab.tsx` | RuleBuilder, ConditionEditor, ActionEditor |
| Status | `StatusTab.tsx` | — |

### Shared Code

| Directory | Contents |
|---|---|
| `shared/common.css` | Buttons, forms, loading/error/empty states, badges, animations |
| `hooks/` | Reusable hooks (useChangeHistory, useEditMode, useAuth, useAutoCreationRules, etc.) |
| `services/api.ts` | All backend API calls |
| `services/autoCreationApi.ts` | Auto-creation API calls |
| `contexts/` | NotificationContext |
| `types/` | TypeScript type definitions |
| `utils/` | Helpers (channelRename, clipboard, naturalSort, logger, etc.) |

### CSS Architecture

See `css-guidelines.md` for full details. Layers: design tokens (`index.css`) → common (`shared/common.css`) → tab loading (`App.css`) → settings (`SettingsTab.css`) → modals (`ModalBase.css`) → component-specific.

## Build & Deploy

```bash
# Build frontend
cd frontend && npm run build     # Output: frontend/dist/

# Deploy to container (dev workflow)
docker cp frontend/dist/. ecm-ecm-1:/app/static/   # Frontend only
docker cp backend/main.py ecm-ecm-1:/app/main.py    # Backend core
docker cp backend/routers/. ecm-ecm-1:/app/routers/  # Backend routers (requires restart)

# Restart backend in container
docker restart ecm-ecm-1
```

## Config & Data

All persistent data lives in the `/config` Docker volume:
- `settings.json` — App configuration (Dispatcharr URL, credentials, preferences)
- `journal.db` — SQLite database (journal entries, task history, notifications, etc.)
- `tls/` — TLS certificates and keys
