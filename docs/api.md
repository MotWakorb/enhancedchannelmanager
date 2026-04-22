# ECM API Reference

Interactive API documentation is available at `/api/docs` (Swagger UI) and `/api/redoc` (ReDoc). `/swagger` also redirects to `/api/docs` for convenience.

All API endpoints require JWT Bearer token authentication. To authenticate in the Swagger UI:

1. Call `POST /api/auth/login` with `{"username": "...", "password": "..."}`
2. Copy the `access_token` from the response
3. Click the **Authorize** button in the Swagger UI and enter the token

## Channels

| Endpoint | Description |
|-|-|
| `GET /api/channels` | List channels (paginated, searchable, filterable) |
| `POST /api/channels` | Create channel |
| `GET /api/channels/{id}` | Get channel details |
| `GET /api/channels/{id}/streams` | Get streams for a channel |
| `PATCH /api/channels/{id}` | Update channel |
| `DELETE /api/channels/{id}` | Delete channel |
| `POST /api/channels/{id}/add-stream` | Add stream to channel |
| `POST /api/channels/{id}/remove-stream` | Remove stream from channel |
| `POST /api/channels/{id}/reorder-streams` | Reorder channel streams |
| `POST /api/channels/assign-numbers` | Bulk assign channel numbers |
| `POST /api/channels/bulk-commit` | Batch multiple channel operations in one request |
| `POST /api/channels/merge` | Merge duplicate channels |
| `POST /api/channels/clear-auto-created` | Clear auto-created flag from channels |
| `GET /api/channels/csv-template` | Download CSV template for channel import |
| `GET /api/channels/export-csv` | Export all channels to CSV |
| `POST /api/channels/import-csv` | Import channels from CSV file |
| `POST /api/channels/preview-csv` | Preview and validate CSV before import |

## Channel Groups

| Endpoint | Description |
|-|-|
| `GET /api/channel-groups` | List all groups |
| `POST /api/channel-groups` | Create group |
| `PATCH /api/channel-groups/{id}` | Update group |
| `DELETE /api/channel-groups/{id}` | Delete group |
| `GET /api/channel-groups/orphaned` | List orphaned groups (no streams, channels, or M3U association) |
| `DELETE /api/channel-groups/orphaned` | Delete orphaned groups (optionally specify group IDs) |
| `GET /api/channel-groups/hidden` | List hidden channel groups |
| `POST /api/channel-groups/{id}/restore` | Restore a hidden channel group |
| `GET /api/channel-groups/auto-created` | List groups with auto-created channels |
| `GET /api/channel-groups/with-streams` | List groups that have channels with streams |

## Logos

| Endpoint | Description |
|-|-|
| `GET /api/channels/logos` | List logos (paginated, searchable) |
| `GET /api/channels/logos/{id}` | Get a single logo |
| `POST /api/channels/logos` | Create logo from URL |
| `POST /api/channels/logos/upload` | Upload logo image file |
| `PATCH /api/channels/logos/{id}` | Update logo |
| `DELETE /api/channels/logos/{id}` | Delete logo |

## Streams

| Endpoint | Description |
|-|-|
| `GET /api/streams` | List streams (paginated, searchable, filterable) |
| `POST /api/streams/by-ids` | Get streams by specific IDs |
| `GET /api/stream-groups` | List stream groups with stream counts |

## M3U

| Endpoint | Description |
|-|-|
| `GET /api/m3u/accounts/{id}` | Get M3U account details |
| `GET /api/m3u/accounts/{id}/stream-metadata` | Get stream metadata (tvg-id mappings) |
| `POST /api/m3u/accounts` | Create M3U account |
| `PUT /api/m3u/accounts/{id}` | Update M3U account (full) |
| `PATCH /api/m3u/accounts/{id}` | Partially update M3U account |
| `DELETE /api/m3u/accounts/{id}` | Delete M3U account |
| `POST /api/m3u/upload` | Upload M3U file |
| `POST /api/m3u/refresh` | Refresh all active M3U accounts |
| `POST /api/m3u/refresh/{id}` | Refresh a single M3U account |
| `POST /api/m3u/accounts/{id}/refresh-vod` | Refresh VOD content (XtreamCodes) |
| `GET /api/m3u/accounts/{id}/filters` | List filters for an account |
| `POST /api/m3u/accounts/{id}/filters` | Create filter for an account |
| `PUT /api/m3u/accounts/{id}/filters/{fid}` | Update a filter |
| `DELETE /api/m3u/accounts/{id}/filters/{fid}` | Delete a filter |
| `GET /api/m3u/accounts/{id}/profiles/` | List profiles for an account |
| `POST /api/m3u/accounts/{id}/profiles/` | Create profile for an account |
| `GET /api/m3u/accounts/{id}/profiles/{pid}/` | Get a specific profile |
| `PATCH /api/m3u/accounts/{id}/profiles/{pid}/` | Update a profile |
| `DELETE /api/m3u/accounts/{id}/profiles/{pid}/` | Delete a profile |
| `PATCH /api/m3u/accounts/{id}/group-settings` | Update group settings for an account |
| `GET /api/m3u/accounts/{id}/changes` | Get change history for an account |
| `GET /api/m3u/snapshots` | List M3U snapshots |
| `GET /api/m3u/server-groups` | List server groups |
| `POST /api/m3u/server-groups` | Create server group |
| `PATCH /api/m3u/server-groups/{id}` | Update server group |
| `DELETE /api/m3u/server-groups/{id}` | Delete server group |

## M3U Digest

| Endpoint | Description |
|-|-|
| `GET /api/m3u/changes` | Get M3U change history (paginated, filterable) |
| `GET /api/m3u/changes/summary` | Get change summary for a time period |
| `GET /api/m3u/digest/settings` | Get digest email settings |
| `PUT /api/m3u/digest/settings` | Update digest email settings |
| `POST /api/m3u/digest/test` | Send a test digest email |

## EPG

| Endpoint | Description |
|-|-|
| `GET /api/epg/sources` | List EPG sources |
| `GET /api/epg/sources/{id}` | Get EPG source details |
| `POST /api/epg/sources` | Create EPG source (including dummy sources) |
| `PATCH /api/epg/sources/{id}` | Update EPG source |
| `DELETE /api/epg/sources/{id}` | Delete EPG source |
| `POST /api/epg/sources/{id}/refresh` | Refresh EPG source |
| `POST /api/epg/import` | Trigger EPG import |
| `GET /api/epg/data` | Search EPG data (paginated) |
| `GET /api/epg/data/{id}` | Get individual EPG data entry |
| `GET /api/epg/grid` | Get EPG program grid for guide view |
| `GET /api/epg/lcn` | Get LCN (Logical Channel Number) for a TVG-ID |
| `POST /api/epg/lcn/batch` | Batch LCN lookup for multiple TVG-IDs |

## Channel Profiles

| Endpoint | Description |
|-|-|
| `GET /api/channel-profiles` | List all channel profiles |
| `POST /api/channel-profiles` | Create channel profile |
| `GET /api/channel-profiles/{id}` | Get channel profile |
| `PATCH /api/channel-profiles/{id}` | Update channel profile |
| `DELETE /api/channel-profiles/{id}` | Delete channel profile |
| `PATCH /api/channel-profiles/{id}/channels/bulk-update` | Bulk enable/disable channels for a profile |
| `PATCH /api/channel-profiles/{id}/channels/{cid}` | Enable/disable a single channel for a profile |

## Stream Profiles

| Endpoint | Description |
|-|-|
| `GET /api/stream-profiles` | List available stream profiles |

## Providers

| Endpoint | Description |
|-|-|
| `GET /api/providers` | List M3U accounts (legacy) |
| `GET /api/providers/group-settings` | Get provider group settings |

## Settings

| Endpoint | Description |
|-|-|
| `GET /api/settings` | Get current settings |
| `POST /api/settings` | Update settings |
| `POST /api/settings/test` | Test Dispatcharr connection |
| `POST /api/settings/test-smtp` | Test SMTP connection |
| `POST /api/settings/test-discord` | Test Discord webhook |
| `POST /api/settings/test-telegram` | Test Telegram bot |
| `POST /api/settings/restart-services` | Restart background services |
| `POST /api/settings/reset-stats` | Reset all statistics |

## Stream Stats

| Endpoint | Description |
|-|-|
| `GET /api/stream-stats` | Get all stream probe statistics |
| `GET /api/stream-stats/summary` | Get probe statistics summary |
| `GET /api/stream-stats/{id}` | Get probe stats for a specific stream |
| `POST /api/stream-stats/by-ids` | Get probe stats for multiple streams |
| `POST /api/stream-stats/probe/{id}` | Probe a single stream |
| `POST /api/stream-stats/probe/bulk` | Probe multiple streams |
| `POST /api/stream-stats/probe/all` | Probe all streams (background task) |
| `GET /api/stream-stats/probe/progress` | Get probe progress |
| `GET /api/stream-stats/probe/results` | Get results of last probe-all operation |
| `GET /api/stream-stats/probe/history` | Get probe run history |
| `POST /api/stream-stats/probe/cancel` | Cancel running probe |
| `POST /api/stream-stats/probe/reset` | Force reset stuck probe state |
| `POST /api/stream-stats/dismiss` | Dismiss probe failures for streams |
| `GET /api/stream-stats/dismissed` | Get list of dismissed stream IDs |
| `POST /api/stream-stats/clear` | Clear probe stats for specific streams |
| `POST /api/stream-stats/clear-all` | Clear all probe stats |
| `GET /api/stream-stats/struck-out` | List struck-out streams (exceeding failure threshold) |
| `POST /api/stream-stats/struck-out/remove` | Bulk remove struck-out streams from all channels |
| `POST /api/stream-stats/compute-sort` | Compute sort scores for streams (resolution, bitrate, framerate, video codec, M3U priority, audio channels) |

## Enhanced Stats

| Endpoint | Description |
|-|-|
| `GET /api/stats/bandwidth` | Get bandwidth summary with in/out breakdown |
| `GET /api/stats/channels` | Get status of all active channels |
| `GET /api/stats/channels/{id}` | Get detailed stats for a channel |
| `GET /api/stats/activity` | Get system activity events |
| `POST /api/stats/channels/{id}/stop` | Stop a channel |
| `POST /api/stats/channels/{id}/stop-client` | Stop a specific client connection |
| `GET /api/stats/top-watched` | Get top watched channels |
| `GET /api/stats/unique-viewers` | Get unique viewer summary for period |
| `GET /api/stats/channel-bandwidth` | Get per-channel bandwidth stats |
| `GET /api/stats/unique-viewers-by-channel` | Get unique viewers per channel |
| `GET /api/stats/watch-history` | Get watch history log (paginated, filterable by channel/IP/days, includes user attribution) |

## Popularity

| Endpoint | Description |
|-|-|
| `GET /api/stats/popularity/rankings` | Get channel popularity rankings (paginated) |
| `GET /api/stats/popularity/channel/{id}` | Get popularity score for specific channel |
| `GET /api/stats/popularity/trending` | Get trending channels (up or down) |
| `POST /api/stats/popularity/calculate` | Trigger popularity score calculation |

## Normalization

| Endpoint | Description |
|-|-|
| `GET /api/normalization/rules` | Get all rules organized by group |
| `GET /api/normalization/rules/{id}` | Get a specific rule |
| `POST /api/normalization/rules` | Create rule |
| `PATCH /api/normalization/rules/{id}` | Update rule |
| `DELETE /api/normalization/rules/{id}` | Delete rule |
| `GET /api/normalization/groups` | List rule groups |
| `POST /api/normalization/groups` | Create rule group |
| `GET /api/normalization/groups/{id}` | Get rule group |
| `PATCH /api/normalization/groups/{id}` | Update rule group |
| `DELETE /api/normalization/groups/{id}` | Delete rule group and all its rules |
| `POST /api/normalization/groups/reorder` | Reorder rule groups |
| `POST /api/normalization/groups/{id}/rules/reorder` | Reorder rules within a group |
| `POST /api/normalization/test` | Test a rule against sample text |
| `POST /api/normalization/test-batch` | Test all enabled rules against multiple texts |
| `POST /api/normalization/normalize` | Normalize text using all enabled rules |
| `GET /api/normalization/rule-stats` | Get stream match statistics per rule |
| `GET /api/normalization/export` | Export normalization rules |
| `POST /api/normalization/import` | Import normalization rules |
| `GET /api/normalization/migration/status` | Get migration status |
| `POST /api/normalization/migration/run` | Run demo rules migration |

## Tags

| Endpoint | Description |
|-|-|
| `GET /api/tags/groups` | List all tag groups with counts |
| `POST /api/tags/groups` | Create tag group |
| `GET /api/tags/groups/{id}` | Get tag group with all tags |
| `PATCH /api/tags/groups/{id}` | Update tag group |
| `DELETE /api/tags/groups/{id}` | Delete tag group and all tags |
| `POST /api/tags/groups/{id}/tags` | Add tags to a group |
| `PATCH /api/tags/groups/{gid}/tags/{tid}` | Update a tag |
| `DELETE /api/tags/groups/{gid}/tags/{tid}` | Delete a tag |
| `POST /api/tags/test` | Test text against a tag group |
| `GET /api/tags/export` | Export all tag groups and tags |
| `POST /api/tags/import` | Import tag groups and tags |

## Stream Preview

| Endpoint | Description |
|-|-|
| `GET /api/stream-preview/{id}` | Preview a stream (proxy with optional transcoding) |
| `GET /api/channel-preview/{id}` | Preview a channel (proxy with optional transcoding) |

## Journal

| Endpoint | Description |
|-|-|
| `GET /api/journal` | Get journal entries (paginated, filterable) |
| `GET /api/journal/stats` | Get journal statistics |
| `DELETE /api/journal/purge` | Purge old journal entries |

## Notifications

| Endpoint | Description |
|-|-|
| `GET /api/notifications` | Get notifications (paginated, filterable by read status) |
| `POST /api/notifications` | Create a notification |
| `PATCH /api/notifications/{id}` | Update notification (mark as read) |
| `DELETE /api/notifications/{id}` | Delete notification |
| `PATCH /api/notifications/mark-all-read` | Mark all notifications as read |
| `DELETE /api/notifications` | Clear notifications (read only or all) |
| `DELETE /api/notifications/by-source` | Delete notifications by source |

## Alert Methods

| Endpoint | Description |
|-|-|
| `GET /api/alert-methods` | List all alert methods |
| `GET /api/alert-methods/types` | Get available alert method types |
| `POST /api/alert-methods` | Create alert method |
| `GET /api/alert-methods/{id}` | Get alert method details |
| `PATCH /api/alert-methods/{id}` | Update alert method |
| `DELETE /api/alert-methods/{id}` | Delete alert method |
| `POST /api/alert-methods/{id}/test` | Send test notification |

## Scheduled Tasks

| Endpoint | Description |
|-|-|
| `GET /api/tasks` | List all tasks with status |
| `GET /api/tasks/{id}` | Get task details with schedules |
| `PATCH /api/tasks/{id}` | Update task configuration |
| `POST /api/tasks/{id}/run` | Run task immediately |
| `POST /api/tasks/{id}/cancel` | Cancel running task |
| `GET /api/tasks/{id}/history` | Get task execution history |
| `GET /api/tasks/engine/status` | Get task engine status |
| `GET /api/tasks/history/all` | Get execution history for all tasks |
| `GET /api/tasks/{id}/parameter-schema` | Get parameter schema for a task type |
| `GET /api/tasks/parameter-schemas` | Get all task parameter schemas |
| `GET /api/tasks/{id}/schedules` | Get task schedules |
| `POST /api/tasks/{id}/schedules` | Add schedule to task |
| `PATCH /api/tasks/{id}/schedules/{sid}` | Update schedule |
| `DELETE /api/tasks/{id}/schedules/{sid}` | Delete schedule |

## Auto-Creation

| Endpoint | Description |
|-|-|
| `GET /api/auto-creation/rules` | List all rules sorted by priority |
| `GET /api/auto-creation/rules/{id}` | Get rule details |
| `POST /api/auto-creation/rules` | Create rule |
| `PUT /api/auto-creation/rules/{id}` | Update rule |
| `DELETE /api/auto-creation/rules/{id}` | Delete rule |
| `POST /api/auto-creation/rules/bulk-update` | Apply the same field changes to multiple rules (omitted fields unchanged) |
| `POST /api/auto-creation/rules/reorder` | Reorder rules by priority |
| `POST /api/auto-creation/rules/{id}/toggle` | Toggle rule enabled state |
| `POST /api/auto-creation/rules/{id}/duplicate` | Duplicate a rule |
| `POST /api/auto-creation/rules/{id}/run` | Run a single rule (supports dry_run) |
| `POST /api/auto-creation/run` | Run the full pipeline (execute or dry_run) |
| `GET /api/auto-creation/executions` | Get execution history (paginated) |
| `GET /api/auto-creation/executions/{id}` | Get execution details (optional log/entities) |
| `POST /api/auto-creation/executions/{id}/rollback` | Rollback an execution |
| `POST /api/auto-creation/validate` | Validate a rule definition |
| `GET /api/auto-creation/export/yaml` | Export all rules as YAML |
| `POST /api/auto-creation/import/yaml` | Import rules from YAML |
| `GET /api/auto-creation/schema/conditions` | Get available condition types |
| `GET /api/auto-creation/schema/actions` | Get available action types |
| `GET /api/auto-creation/schema/template-variables` | Get available template variables |
| `GET /api/auto-creation/debug-bundle` | Download diagnostic bundle (obfuscated channels, rules, streams, probe stats, settings, task schedules, logs) |

## FFMPEG Builder

| Endpoint | Description |
|-|-|
| `GET /api/ffmpeg/capabilities` | Detect system FFmpeg capabilities (codecs, formats, filters, hardware) |
| `POST /api/ffmpeg/probe` | Probe a media source for stream info (codec, resolution, bitrate) |
| `GET /api/ffmpeg/configs` | List all saved configurations |
| `POST /api/ffmpeg/configs` | Create new configuration |
| `GET /api/ffmpeg/configs/{id}` | Get specific configuration |
| `PUT /api/ffmpeg/configs/{id}` | Update configuration |
| `DELETE /api/ffmpeg/configs/{id}` | Delete configuration |
| `POST /api/ffmpeg/validate` | Validate builder state, return errors/warnings |
| `POST /api/ffmpeg/generate-command` | Generate annotated FFmpeg command from builder state |
| `GET /api/ffmpeg/jobs` | List all transcoding jobs |
| `POST /api/ffmpeg/jobs` | Create and queue new transcoding job |
| `GET /api/ffmpeg/jobs/{id}` | Get job status and progress |
| `POST /api/ffmpeg/jobs/{id}/cancel` | Cancel running job |
| `DELETE /api/ffmpeg/jobs/{id}` | Delete job record |
| `GET /api/ffmpeg/queue-config` | Get job queue configuration |
| `PUT /api/ffmpeg/queue-config` | Update queue settings (max concurrent, retries) |
| `GET /api/ffmpeg/profiles` | List saved user profiles |
| `POST /api/ffmpeg/profiles` | Save builder state as a profile |
| `DELETE /api/ffmpeg/profiles/{id}` | Delete saved profile |

## Cache

| Endpoint | Description |
|-|-|
| `POST /api/cache/invalidate` | Invalidate cached data (optional prefix filter) |
| `GET /api/cache/stats` | Get cache statistics |

## TLS

| Endpoint | Description |
|-|-|
| `GET /api/tls/status` | Get TLS configuration status |
| `GET /api/tls/settings` | Get TLS settings |
| `POST /api/tls/configure` | Configure TLS settings |
| `POST /api/tls/request-cert` | Request Let's Encrypt certificate (DNS-01 challenge) |
| `POST /api/tls/complete-challenge` | Complete pending DNS challenge |
| `POST /api/tls/upload-cert` | Upload custom certificate and key |
| `POST /api/tls/renew` | Manually trigger certificate renewal |
| `DELETE /api/tls/certificate` | Delete certificate and disable TLS |
| `POST /api/tls/test-dns-provider` | Test DNS provider credentials |
| `POST /api/tls/https/start` | Start HTTPS server |
| `POST /api/tls/https/stop` | Stop HTTPS server |
| `POST /api/tls/https/restart` | Restart HTTPS server |
| `GET /api/tls/https/status` | Get HTTPS server status |

## Cron

| Endpoint | Description |
|-|-|
| `GET /api/cron/presets` | List cron schedule presets |
| `POST /api/cron/validate` | Validate a cron expression |

## Dummy EPG

| Endpoint | Description |
|-|-|
| `GET /api/dummy-epg/profiles` | List dummy EPG profiles |
| `POST /api/dummy-epg/profiles` | Create dummy EPG profile |
| `GET /api/dummy-epg/profiles/{id}` | Get dummy EPG profile |
| `PATCH /api/dummy-epg/profiles/{id}` | Update dummy EPG profile |
| `DELETE /api/dummy-epg/profiles/{id}` | Delete dummy EPG profile |
| `POST /api/dummy-epg/generate` | Generate dummy EPG data |
| `POST /api/dummy-epg/preview` | Preview dummy EPG output |
| `POST /api/dummy-epg/preview/batch` | Batch preview dummy EPG |
| `GET /api/dummy-epg/xmltv` | Get combined XMLTV output |
| `GET /api/dummy-epg/xmltv/{id}` | Get XMLTV output for a profile |
| `GET /api/dummy-epg/profiles/export/yaml` | Export profiles as YAML |
| `POST /api/dummy-epg/profiles/import/yaml` | Import profiles from YAML |

`POST /api/dummy-epg/preview` accepts the full profile config plus:

- `inline_lookups: {<name>: {<key>: <value>, ...}, ...}` — per-source lookup tables referenced by `{key|lookup:<name>}`. Inline tables override globals of the same name.
- `global_lookup_ids: [id, ...]` — IDs of saved tables from `/api/lookup-tables`.
- `include_trace: bool` — when true, the response carries a `traces` dict keyed by template field (`title_template`, `description_template`, …). Trace entries describe literals, placeholders (with per-pipe input/output and lookup hit/miss), and conditionals (taken/skipped + branch kind).

## Lookup Tables

Named key → value tables used by the dummy EPG template engine's `{key|lookup:<name>}` pipe.

| Endpoint | Description |
|-|-|
| `GET /api/lookup-tables` | List all tables (summary — entry counts, no entries) |
| `POST /api/lookup-tables` | Create a table (`{name, description?, entries?}`) |
| `GET /api/lookup-tables/{id}` | Get a single table with full `entries` dict |
| `PATCH /api/lookup-tables/{id}` | Rename, edit description, and/or replace entries |
| `DELETE /api/lookup-tables/{id}` | Delete a table (cascades to any source still referencing it by ID — the preview path skips missing IDs silently) |

Names are unique. Each table is capped at 10 000 entries.

## Export

| Endpoint | Description |
|-|-|
| `GET /api/export/profiles` | List export profiles |
| `POST /api/export/profiles` | Create export profile |
| `PATCH /api/export/profiles/{id}` | Update export profile |
| `DELETE /api/export/profiles/{id}` | Delete export profile |
| `POST /api/export/profiles/{id}/generate` | Generate export files |
| `GET /api/export/profiles/{id}/preview` | Preview export output |
| `GET /api/export/profiles/{id}/download/m3u` | Download exported M3U |
| `GET /api/export/profiles/{id}/download/xmltv` | Download exported XMLTV |
| `GET /api/export/cloud-targets` | List cloud storage targets |
| `POST /api/export/cloud-targets` | Create cloud storage target |
| `PATCH /api/export/cloud-targets/{id}` | Update cloud storage target |
| `DELETE /api/export/cloud-targets/{id}` | Delete cloud storage target |
| `POST /api/export/cloud-targets/test` | Test cloud storage credentials |
| `POST /api/export/cloud-targets/{id}/test` | Test a specific cloud target |
| `GET /api/export/publish-configs` | List publish configurations |
| `POST /api/export/publish-configs` | Create publish configuration |
| `PATCH /api/export/publish-configs/{id}` | Update publish configuration |
| `DELETE /api/export/publish-configs/{id}` | Delete publish configuration |
| `POST /api/export/publish-configs/{id}/publish` | Publish to cloud target |
| `POST /api/export/publish-configs/{id}/dry-run` | Dry-run publish |
| `GET /api/export/publish-history` | Get publish history |
| `DELETE /api/export/publish-history` | Clear publish history |
| `DELETE /api/export/publish-history/{id}` | Delete publish history entry |

## Backup

| Endpoint | Description |
|-|-|
| `GET /api/backup/create` | Download backup zip of all configuration |
| `POST /api/backup/restore` | Restore from uploaded backup zip |
| `POST /api/backup/restore-initial` | Restore from backup during initial setup (no auth) |
| `GET /api/backup/export-sections` | List available YAML export sections |
| `POST /api/backup/export` | Export selected sections as YAML |
| `POST /api/backup/import` | Import from YAML backup |
| `GET /api/backup/saved` | List saved YAML backup files |
| `DELETE /api/backup/saved/{filename}` | Delete a saved YAML backup file |

## Authentication

| Endpoint | Description |
|-|-|
| `GET /api/auth/status` | Get authentication status and configuration |
| `GET /api/auth/setup-required` | Check if first-run setup is needed |
| `POST /api/auth/setup` | Complete first-run setup (create admin account) |
| `POST /api/auth/login` | Login with username/password |
| `POST /api/auth/logout` | Logout and clear session |
| `POST /api/auth/refresh` | Refresh access token |
| `GET /api/auth/me` | Get current user info |
| `PUT /api/auth/me` | Update current user profile |
| `POST /api/auth/change-password` | Change current user's password |
| `POST /api/auth/forgot-password` | Request password reset email |
| `POST /api/auth/reset-password` | Reset password with token |
| `GET /api/auth/providers` | List available auth providers |
| `POST /api/auth/dispatcharr/login` | Login via Dispatcharr credentials |
| `GET /api/auth/identities` | List linked auth identities for current user |
| `POST /api/auth/identities/link` | Link a new auth identity to current user |
| `DELETE /api/auth/identities/{id}` | Unlink an auth identity |
| `GET /api/auth/admin/settings` | Get auth settings (admin) |
| `PUT /api/auth/admin/settings` | Update auth settings (admin) |
| `GET /api/auth/admin/users` | List users (admin) |
| `GET /api/auth/admin/users/{id}` | Get user details (admin) |
| `PUT /api/auth/admin/users/{id}` | Update user (admin) |
| `DELETE /api/auth/admin/users/{id}` | Delete user (admin) |

## User Management (Admin)

| Endpoint | Description |
|-|-|
| `GET /api/admin/users` | List all users (paginated, searchable) |
| `POST /api/admin/users` | Create new user |
| `GET /api/admin/users/{id}` | Get user details |
| `PATCH /api/admin/users/{id}` | Update user |
| `DELETE /api/admin/users/{id}` | Delete (deactivate) user |

## Health

| Endpoint | Description |
|-|-|
| `GET /api/health` | Health check |
| `GET /api/debug/request-rates` | Request rate statistics (diagnostics) |
