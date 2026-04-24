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

### With MCP Server (Claude AI Integration)

To add the optional MCP server for managing ECM through Claude, add the MCP service to your compose file:

```yaml
services:
  ecm:
    image: ghcr.io/motwakorb/enhancedchannelmanager:latest
    ports:
      - "6100:6100"
      - "6143:6143"
    volumes:
      - ./config:/config
    environment:
      - PUID=1000
      - PGID=1000

  ecm-mcp:
    image: ghcr.io/motwakorb/enhancedchannelmanager-mcp:latest
    ports:
      - "6101:6101"
    volumes:
      - ./config:/config:ro
    environment:
      - ECM_URL=http://ecm:6100
      - MCP_PORT=6101
    depends_on:
      ecm:
        condition: service_healthy
```

Or if you're building from source, use the MCP compose overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.mcp.yml up -d
```

See [MCP Server (Claude Integration)](#mcp-server-claude-integration) for setup instructions.

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
A rules-based automation engine for channel creation, stream merging, and lifecycle management. Build complex conditions (stream name, group, quality, codec, normalized matching, etc.) with AND/OR logic, then define actions (create channel/group, merge streams, assign metadata, set variables, name transforms). Per-rule normalization group selection lets each rule apply specific normalization groups. Supports dry-run preview, execution rollback, YAML import/export, orphan reconciliation, and a diagnostic debug bundle for troubleshooting.

### FFMPEG Builder
Visual interface for constructing FFmpeg commands with Simple (three-step IPTV wizard) and Advanced modes. Includes 8 built-in IPTV presets, hardware acceleration support (CUDA, QSV, VAAPI), annotated command preview with tooltips, saved profiles, and direct push to Dispatcharr as stream profiles.

### Stream Health & Probing
Automated stream probing with configurable schedules, batch sizes, retry logic, and rate limit detection. Profile-aware probing distributes connections across M3U profiles. Results drive smart stream sorting by resolution, bitrate, framerate, video codec, and M3U priority with configurable ordering for deprioritized stream categories. Black screen detection identifies streams showing dark/blank content, and low FPS detection flags streams below a configurable threshold (5/10/15/20 FPS). Both are deprioritized in Smart Sort. A strikeout system tracks consecutive failures for bulk cleanup.

### Logo Manager
Browse, search, upload, and assign logos to channels. Supports URL import and file upload to Dispatcharr with usage tracking and pagination.

### Stats & Monitoring
Live dashboard showing active channels, M3U connection counts, per-channel FFmpeg metrics (speed, FPS, bitrate), and bandwidth charts. Enhanced analytics include unique viewer tracking with Dispatcharr user identification, per-channel bandwidth, popularity scoring with trend analysis, and watch history with user attribution.

### Journal
Activity log tracking all changes to channels, EPG, and M3U accounts with filtering by category, action type, and time range.

### Settings
Comprehensive configuration including Dispatcharr connection, channel defaults, stream name normalization (tag-based and rule-based engines), stream probing, scheduled tasks (EPG/M3U refresh, probing, cleanup), alert methods (Discord, Telegram, email), authentication (local + Dispatcharr SSO), user management, TLS certificates, VLC integration, appearance themes, and backup/restore.

### Authentication
First-run setup wizard, local auth with bcrypt hashing, Dispatcharr SSO, account linking, email-based password reset, and CLI password reset for lockout recovery. JWT-based sessions with automatic token refresh.

### Notification Center
In-app notification bell with history, active task pinning, and external alert methods (Discord webhooks, Telegram bots, SMTP email) with digest batching and source filtering.

## MCP Server (Claude Integration)

ECM includes an MCP (Model Context Protocol) server that lets Claude manage your channels through natural language. Ask Claude to list channels, refresh M3U accounts, probe stream health, run auto-creation pipelines, find and merge duplicate channels, view stats, and more — 124 tools across 14 domains.

### Setup

1. **Generate an API key** in ECM Settings > MCP Integration
2. **Start the MCP container** — add the `ecm-mcp` service to your compose file (see [With MCP Server](#with-mcp-server-claude-ai-integration)) and start it on port 6101
3. **Connect Claude** using one of the methods below (replace `YOUR_ECM_HOST` and `YOUR_API_KEY`):

**Claude Desktop** — add to your Claude Desktop config:
```json
{
  "mcpServers": {
    "ecm": {
      "url": "http://YOUR_ECM_HOST:6101/sse?api_key=YOUR_API_KEY"
    }
  }
}
```

**Claude Code** — create a `.mcp.json` file in any project directory where you want ECM tools available:
```json
{
  "mcpServers": {
    "ecm": {
      "type": "sse",
      "url": "http://YOUR_ECM_HOST:6101/sse?api_key=YOUR_API_KEY"
    }
  }
}
```

To connect:
1. Create the `.mcp.json` file above in your project root (replace `YOUR_ECM_HOST` and `YOUR_API_KEY`)
2. Start Claude Code in that directory — it auto-detects `.mcp.json` on launch
3. Run `/mcp` to reconnect if the MCP server restarts
4. Ask Claude to manage your channels — e.g. "list my channels", "create an auto-creation rule for sports", "probe all streams"

If running ECM locally, use `localhost` as your host. If the MCP container is on the same Docker network as Claude Code, use the container name (`ecm-mcp`).

### Available Tools (124)

| Tool | Description |
|-|-|
| **Channels (18)** | |
| `list_channels` | List channels with optional group/search/stream count filtering |
| `get_channel` | Get detailed channel info (streams, EPG, logo) |
| `create_channel` | Create a new channel |
| `update_channel` | Update channel name, number, or group |
| `delete_channel` | Delete a channel |
| `bulk_delete_channels` | Delete multiple channels at once |
| `add_stream_to_channel` | Add a stream to a channel |
| `remove_stream_from_channel` | Remove a stream from a channel |
| `reorder_streams` | Reorder streams within a channel by priority |
| `assign_channel_numbers` | Bulk-assign sequential channel numbers |
| `merge_channels` | Merge multiple channels into one |
| `find_duplicate_channels` | Scan for channels with matching normalized names |
| `bulk_merge_duplicate_channels` | Merge multiple groups of duplicates at once |
| `bulk_commit_channels` | Commit a batch of channel operations atomically |
| `build_channel_lineup` | Bulk-create channels and fuzzy-match streams |
| `clear_auto_created` | Remove auto-created channels by group |
| `bulk_add_streams_to_channel` | Add multiple streams to a channel at once |
| `bulk_assign_epg` | Assign EPG IDs (tvg_id) to multiple channels |
| **Groups (8)** | |
| `list_channel_groups` | List all groups with channel counts |
| `create_channel_group` | Create a new group |
| `get_orphaned_groups` | Find groups with no channels |
| `delete_channel_group` | Delete a group, optionally deleting its channels |
| `delete_orphaned_groups` | Delete groups with no channels assigned |
| `get_hidden_groups` | List hidden channel groups |
| `get_auto_created_groups` | List auto-created groups |
| `get_groups_with_streams` | List groups with stream count info |
| **Streams (17)** | |
| `list_streams` | List streams with group/provider/search filtering |
| `search_streams` | Search streams by name across all providers |
| `get_streams_by_ids` | Fetch detailed info for specific stream IDs |
| `get_streams_for_channel` | Get streams assigned to a channel |
| `get_stream_health` | Stream health summary from last probe |
| `probe_streams` | Start probing all streams (background) |
| `probe_single_stream` | Probe one specific stream |
| `probe_bulk_streams` | Probe multiple streams at once |
| `get_probe_progress` | Check ongoing probe status |
| `get_probe_results` | Results from the most recent probe |
| `get_struck_out_streams` | List streams with consecutive failures |
| `cleanup_struck_out_streams` | Remove struck-out streams from channels |
| `bulk_remove_streams` | Remove multiple streams from a channel |
| `cancel_probe` | Cancel a running probe |
| `bulk_search_streams` | Search multiple stream names in one call |
| `fuzzy_match_stream` | Find best fuzzy match for a stream name |
| `match_streams_to_channels` | Match streams to channels by name similarity |
| **M3U (9)** | |
| `list_m3u_accounts` | List all M3U provider accounts |
| `get_m3u_account` | Get detailed account info |
| `create_m3u_account` | Create a new M3U account |
| `update_m3u_account` | Update account name or URL |
| `delete_m3u_account` | Delete an M3U account |
| `refresh_m3u` | Refresh a specific M3U account |
| `refresh_all_m3u` | Refresh all M3U accounts |
| `update_m3u_group_settings` | Enable/disable stream groups on an account |
| `bulk_update_m3u_group_settings` | Enable/disable multiple stream groups at once |
| **EPG (10)** | |
| `list_epg_sources` | List EPG data sources |
| `create_epg_source` | Create a new EPG source |
| `update_epg_source` | Update an EPG source |
| `delete_epg_source` | Delete an EPG source |
| `refresh_epg` | Refresh a specific EPG source |
| `match_channels_epg` | Auto-match channels to EPG data |
| `refresh_all_epg` | Refresh multiple or all EPG sources at once |
| `get_epg_grid` | What's on TV now — EPG schedule grid |
| `list_dummy_epg_profiles` | List dummy EPG profiles |
| `generate_dummy_epg` | Regenerate dummy EPG XMLTV data |
| **Auto-Creation (12)** | |
| `list_auto_creation_rules` | List all rules |
| `get_auto_creation_rule` | Get rule details (conditions, actions, normalization groups, sort config) |
| `create_auto_creation_rule` | Create a rule with conditions, actions, and per-rule normalization groups |
| `update_auto_creation_rule` | Update an existing rule (supports normalization_group_ids) |
| `delete_auto_creation_rule` | Delete a rule |
| `toggle_auto_creation_rule` | Enable/disable a rule |
| `duplicate_auto_creation_rule` | Duplicate a rule |
| `run_auto_creation` | Run pipeline (dry_run=true by default) |
| `list_auto_creation_executions` | View execution history |
| `rollback_auto_creation` | Undo an execution |
| `get_auto_creation_debug_bundle` | Info about the diagnostic debug bundle for troubleshooting |
| `bulk_toggle_auto_creation_rules` | Toggle multiple rules at once |
| **Export (6)** | |
| `list_export_profiles` | List export profiles |
| `create_export_profile` | Create an export profile |
| `delete_export_profile` | Delete an export profile |
| `generate_export` | Generate M3U/XMLTV for a profile |
| `list_cloud_targets` | List cloud storage targets |
| `publish_export` | Publish to a cloud target |
| **FFmpeg (14)** | |
| `ffmpeg_capabilities` | Detect system FFmpeg capabilities |
| `ffmpeg_probe` | Probe a media source for stream info |
| `ffmpeg_list_configs` | List saved FFmpeg configurations |
| `ffmpeg_create_config` | Create new FFmpeg configuration |
| `ffmpeg_get_config` | Get specific configuration |
| `ffmpeg_update_config` | Update configuration |
| `ffmpeg_delete_config` | Delete configuration |
| `ffmpeg_validate` | Validate builder state |
| `ffmpeg_generate_command` | Generate annotated FFmpeg command |
| `ffmpeg_list_jobs` | List transcoding jobs |
| `ffmpeg_create_job` | Create and queue transcoding job |
| `ffmpeg_get_job` | Get job status and progress |
| `ffmpeg_cancel_job` | Cancel running job |
| `ffmpeg_delete_job` | Delete job record |
| **Tasks (7)** | |
| `list_tasks` | List scheduled tasks and status |
| `run_task` | Run a task immediately |
| `cancel_task` | Cancel a running task |
| `get_task_history` | View task execution history |
| `list_task_schedules` | List schedules for a task |
| `create_task_schedule` | Create a cron schedule |
| `delete_task_schedule` | Delete a schedule |
| **Stats (7)** | |
| `get_channel_stats` | Channel viewing stats and active viewers |
| `get_top_watched` | Most-watched channels by viewing time |
| `get_bandwidth` | Bandwidth usage (today, week, month, all-time) |
| `get_popularity_rankings` | Channel popularity scores and trending |
| `get_watch_history` | Watch history with user attribution and filters (channel, IP, days) |
| `get_unique_viewers` | Unique viewer counts by channel |
| `compute_stream_sort` | Compute optimal stream sort order (resolution, bitrate, video codec, etc.) |
| **System (6)** | |
| `get_settings` | ECM settings overview |
| `create_backup` | Create config backup |
| `get_export_sections` | List available YAML export sections |
| `list_saved_backups` | List saved YAML backup files |
| `delete_saved_backup` | Delete a saved backup file |
| `get_journal` | Activity audit log (with limit/category filters) |
| **Notifications (5)** | |
| `list_notifications` | List notifications with unread count |
| `mark_notifications_read` | Mark all as read |
| `delete_all_notifications` | Clear all notifications |
| `list_alert_methods` | List configured alert methods (Discord, Telegram, email) |
| `test_alert_method` | Send a test notification through an alert method |
| **Profiles (3)** | |
| `list_channel_profiles` | List channel profiles |
| `list_stream_profiles` | List stream profiles |
| `apply_profile_to_channels` | Bulk-assign a profile to channels |
| **Normalization (2)** | |
| `test_normalization` | Test how stream names normalize |
| `list_normalization_rules` | List normalization rule groups |

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
| MCP Server | Python, FastMCP, SSE transport, 124 tools |
| Deployment | Docker Compose, two containers (ECM + MCP) |

## API Reference

Interactive API docs are available at `/api/docs` (Swagger UI) and `/api/redoc`. See [docs/api.md](docs/api.md) for the full endpoint reference.

## Roadmap

### Completed

- **v0.15.1** — OWASP hardening (security headers, CORS, rate limiting, NIST password policy, log redaction, path validation)
- **v0.15.0** — Server-side EPG matching, stream normalization, PUID/PGID support, low FPS detection, export/publish pipeline
- **v0.14.0** — Dummy EPG profiles, auto-creation pipeline, normalization engine
- **v0.13.0** — Backend modularization (20+ routers), auth system, task engine

### v0.16.0 — MCP Server & Claude Integration (Current)
MCP server for natural language channel management via Claude. 124 tools across 14 domains, SSE transport with API key auth, separate Docker container, frontend settings UI with connection status. Per-rule normalization group selection, video codec as smart sort criterion, configurable deprioritized stream ordering, diagnostic debug bundles, user identification in watch history and stats, and settings persistence hardening.

### v0.17.0 — Dashboard & Analytics
Enhanced dashboard with real-time stream monitoring, historical analytics, and customizable widgets.
