# Enhanced Channel Manager

A professional-grade web interface for managing IPTV configurations with [Dispatcharr](https://github.com/Dispatcharr/Dispatcharr). Built with React + TypeScript and Python FastAPI.

ECM gives you full control over your IPTV setup: manage M3U accounts and EPG sources, create and organize channels with drag-and-drop, automate channel creation with a powerful rules engine, probe stream health, build FFmpeg commands visually, and monitor live streaming stats — all from a single interface.

## Installation

### Docker Compose (Recommended)

```yaml
services:
  ecm:
    image: ghcr.io/motwakorb/enhancedchannelmanager:latest
    ports:
      - "6100:6100"   # HTTP (configurable via ECM_PORT)
      - "6143:6143"   # HTTPS (configurable via ECM_HTTPS_PORT)
    volumes:
      - ./config:/config
    environment:
      - PUID=1000
      - PGID=1000
      - ECM_PORT=6100
      - ECM_HTTPS_PORT=6143
```

That's it. Open `http://localhost:6100` and the setup wizard will guide you through creating an admin account and connecting to Dispatcharr.

**User / Group Identifiers:**
- **PUID** (default: 1000) — User ID the application runs as
- **PGID** (default: 1000) — Group ID the application runs as

Set these to match the owner of your bind-mounted volumes to avoid permission issues. Find your IDs with `id your_user`.

**Port Configuration:**
- **ECM_PORT** (default: 6100) — HTTP interface (always available as fallback)
- **ECM_HTTPS_PORT** (default: 6143) — HTTPS interface (when TLS is configured in Settings)

**Volumes:**
- `/config` — Persistent storage for database, settings, logos, TLS certificates, and backups

### Backup & Restore

ECM supports full backup and restore of all configuration. You can create backups from Settings, or restore from a backup during the first-run setup wizard.

### Development Setup

```bash
# Frontend
cd frontend && npm install && npm run dev

# Backend
cd backend && pip install -r requirements.txt && uvicorn main:app --reload
```

## Features

### Channel Management
Full CRUD for channels and groups with a split-pane layout. Drag-and-drop streams onto channels, reorder streams by priority, bulk-create channels from stream groups with smart name normalization (quality variants, country prefixes, timezone handling), and organize everything into numbered channel groups. Staged edit mode lets you queue changes locally and commit or discard them as a batch.

### M3U Manager
Manage Standard M3U, XtreamCodes, and HD Homerun accounts. Link related accounts so group enable/disable changes cascade automatically. Track changes detected across M3U refreshes with filtering, search, and optional email digest notifications.

### EPG Manager
Configure multiple XMLTV and Schedules Direct EPG sources with drag-and-drop priority ordering. Create dummy EPG entries for channels without guide data. Bulk EPG assignment uses country-aware matching, call sign scoring, and HD preference to automatically map EPG data to channels.

### TV Guide
EPG grid view with now-playing highlights, date/time navigation, channel profile filtering, and click-to-edit channel metadata.

### Auto-Creation Pipeline
A rules-based automation engine for channel creation, stream merging, and lifecycle management. Build complex conditions (stream name, group, quality, codec, normalized matching, etc.) with AND/OR logic, then define actions (create channel/group, merge streams, assign metadata, set variables, name transforms). Supports dry-run preview, execution rollback, YAML import/export, and orphan reconciliation.

### FFMPEG Builder
Visual interface for constructing FFmpeg commands with Simple (three-step IPTV wizard) and Advanced modes. Includes 8 built-in IPTV presets, hardware acceleration support (CUDA, QSV, VAAPI), annotated command preview with tooltips, saved profiles, and direct push to Dispatcharr as stream profiles.

### Stream Health & Probing
Automated stream probing with configurable schedules, batch sizes, retry logic, and rate limit detection. Profile-aware probing distributes connections across M3U profiles. Results drive smart stream sorting by resolution, bitrate, framerate, and M3U priority. Black screen detection identifies streams showing dark/blank content, and low FPS detection flags streams below a configurable threshold (5/10/15/20 FPS). Both are deprioritized in Smart Sort. A strikeout system tracks consecutive failures for bulk cleanup.

### Logo Manager
Browse, search, upload, and assign logos to channels. Supports URL import and file upload to Dispatcharr with usage tracking and pagination.

### Stats & Monitoring
Live dashboard showing active channels, M3U connection counts, per-channel FFmpeg metrics (speed, FPS, bitrate), and bandwidth charts. Enhanced analytics include unique viewer tracking, per-channel bandwidth, popularity scoring with trend analysis, and watch history.

### Journal
Activity log tracking all changes to channels, EPG, and M3U accounts with filtering by category, action type, and time range.

### Settings
Comprehensive configuration including Dispatcharr connection, channel defaults, stream name normalization (tag-based and rule-based engines), stream probing, scheduled tasks (EPG/M3U refresh, probing, cleanup), alert methods (Discord, Telegram, email), authentication (local + Dispatcharr SSO), user management, TLS certificates, VLC integration, appearance themes, and backup/restore.

### Authentication
First-run setup wizard, local auth with bcrypt hashing, Dispatcharr SSO, account linking, email-based password reset, and CLI password reset for lockout recovery. JWT-based sessions with automatic token refresh.

### Notification Center
In-app notification bell with history, active task pinning, and external alert methods (Discord webhooks, Telegram bots, SMTP email) with digest batching and source filtering.

## MCP Server (Claude Integration)

ECM includes an MCP (Model Context Protocol) server that lets Claude manage your channels through natural language. Ask Claude to list channels, refresh M3U accounts, probe stream health, run auto-creation pipelines, and more.

### Setup

1. **Generate an API key** in ECM Settings > MCP Integration
2. **Start the MCP container** — it's included in `docker-compose.yml` and starts automatically alongside ECM on port 6101
3. **Connect Claude Desktop** — add this to your Claude Desktop config (replace `YOUR_ECM_HOST` with your server IP):

```json
{
  "mcpServers": {
    "ecm": {
      "url": "http://YOUR_ECM_HOST:6101/sse?api_key=YOUR_API_KEY"
    }
  }
}
```

### Available Tools (33)

| Tool | Description |
|-|-|
| **Channels** | |
| `list_channels` | List channels with optional group/search filtering |
| `get_channel` | Get detailed channel info (streams, EPG, logo) |
| `create_channel` | Create a new channel |
| `update_channel` | Update channel name, number, or group |
| `delete_channel` | Delete a channel |
| `add_stream_to_channel` | Add a stream to a channel |
| `remove_stream_from_channel` | Remove a stream from a channel |
| `reorder_streams` | Reorder streams within a channel by priority |
| `assign_channel_numbers` | Bulk-assign sequential channel numbers |
| `get_streams_for_channel` | Get detailed stream info for a channel |
| **Groups** | |
| `list_channel_groups` | List all groups with channel counts |
| `create_channel_group` | Create a new group |
| `get_orphaned_groups` | Find groups with no channels |
| **Streams** | |
| `list_streams` | List streams with group/provider/search filtering |
| `search_streams` | Search streams by name across all providers |
| `get_stream_health` | Stream health summary from last probe |
| `probe_streams` | Start probing all streams (background) |
| **M3U** | |
| `list_m3u_accounts` | List all M3U provider accounts |
| `refresh_m3u` | Refresh a specific M3U account |
| `refresh_all_m3u` | Refresh all M3U accounts |
| **EPG** | |
| `list_epg_sources` | List EPG data sources |
| `refresh_epg` | Refresh a specific EPG source |
| `match_channels_epg` | Auto-match channels to EPG data |
| **Auto-Creation** | |
| `list_auto_creation_rules` | List all auto-creation rules |
| `run_auto_creation` | Run pipeline (dry_run=true by default) |
| **Export** | |
| `list_export_profiles` | List export profiles |
| `generate_export` | Generate M3U/XMLTV for a profile |
| **Tasks** | |
| `list_tasks` | List scheduled tasks and status |
| `run_task` | Run a task immediately |
| **Stats** | |
| `get_channel_stats` | Channel viewing stats and active viewers |
| **System** | |
| `get_settings` | ECM settings overview |
| `create_backup` | Create config backup |
| `get_journal` | Activity audit log (with limit/category filters) |

Three read-only MCP resources provide quick context without a tool call: `ecm://stats/overview`, `ecm://channels/summary`, and `ecm://tasks/status`.

## CLI Utilities

### Password Reset

```bash
# Interactive mode (lists users, prompts for password)
docker exec -it enhancedchannelmanager python /app/reset_password.py

# Non-interactive
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'NewPass123'

# Skip password strength validation
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'simple' --force
```

### Search Streams

```bash
./scripts/search-stream.sh http://dispatcharr:9191 admin password "ESPN"
```

## Technical Stack

| Layer | Technology |
|-|-|
| Frontend | React 18, TypeScript, Vite, @dnd-kit |
| Backend | Python, FastAPI, 20+ modular API routers |
| Deployment | Docker, single container, static frontend |

## API Reference

Interactive API docs are available at `/api/docs` (Swagger UI) and `/api/redoc`. See [docs/api.md](docs/api.md) for the full endpoint reference.

## Roadmap

### v0.14.0 — Enhanced Dummy EPG
Text transforms, conditionals, lookup tables, per-source inline lookups, and enhanced live preview for dummy EPG templates.

### v0.15.0 — Move Logic Server-Side
Migrate heavy client-side computation (EPG matching, stream normalization, print guide generation, edit mode consolidation) to backend APIs.

### v0.16.0 — M3U/EPG Export & Cloud Distribution
Generate M3U playlists and XMLTV EPG from managed channels. Playlist profiles, cloud adapters (S3, Google Drive, OneDrive, Dropbox), scheduled publish pipeline, and full management UI.
