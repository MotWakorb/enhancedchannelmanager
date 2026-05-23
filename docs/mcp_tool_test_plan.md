# ECM MCP Tool Test Plan (v0.17.2 — MCP fixes release)

> **Purpose:** 0.17.2 focuses on fixing the MCP integration. This is a manual
> test plan covering **every one of the 124 tools** the ECM MCP server exposes,
> across 14 domains. For each tool you get: what it does, a natural-language
> **prompt to type to your local Claude**, the expected result, edge/failure
> prompts, and code-level "watch for" notes flagging where it may be broken.
>
> Run the prompts against a local Claude (Claude Desktop or Claude Code)
> connected to your ECM MCP server, and record pass/fail in the tracking
> checklist below. Anything that fails becomes a 0.17.2 bead.

## How to use this plan

1. **Connect** your local Claude to the ECM MCP server (Settings → MCP Integration
   for the endpoint URL + API key, or your existing `mcp-remote` / `.mcp.json`
   setup). Confirm the connection is live by asking: *"What ECM tools do you have?"*
   — you should see the tools listed.
2. **Verify which tool fired.** After each prompt, confirm Claude actually called
   the intended tool (Claude Code shows the tool call inline; in Claude Desktop,
   expand the tool-use block). A plausible-sounding answer with **no tool call**
   is a failure — the model answered from memory instead of calling ECM.
3. **Ask by name, not by ID.** Phrase prompts the way you'd actually talk —
   *"the group named 'News'"*, *"the 'ESPN' channel"* — never raw backend IDs.
   Claude resolves names to IDs under the hood via the `list_*` / `search_*`
   tools. (If a name is ambiguous, Claude should list the matches and ask which
   you mean.) The prompts below are written this way on purpose.
4. **Mark each test's result.** Every test under each tool ends with `Result: ☐` —
   change the ☐ to ✅ (output matched the Expect) or ❌ (it didn't), and jot the
   actual behavior next to any ❌. The per-domain tracking checklist below is a
   quick index of which tools you've covered.

## ⚠️ Safety — read before running destructive tests

Each tool is tagged **READ-ONLY**, **WRITE**, or **DESTRUCTIVE**.

- **Take a backup first.** Run `create_backup` (System & Backup) — or the UI
  backup — before exercising any DESTRUCTIVE tool. (`create_backup` was fixed in
  0.17.2; still verify the backup exists via `list_saved_backups` / the UI.)
- **Prefer a test instance or disposable data.** DESTRUCTIVE tools delete
  channels, groups, M3U/EPG accounts, streams, schedules, notifications, and
  backups, or merge channels — several are hard or impossible to undo.
- **`run_auto_creation` defaults to `dry_run=True`.** Only the explicit
  `dry_run=False` form creates channels in bulk. Watch that Claude doesn't pick
  the live form from an ambiguous prompt.

## Legend

| Tag | Meaning |
|-----|---------|
| **READ-ONLY** | Safe to run anywhere; no state change. |
| **WRITE** | Creates/updates/refreshes/probes/runs; changes state but generally recoverable. |
| **DESTRUCTIVE** | Deletes/merges/clears/cleanup; hard or impossible to undo. Back up first. |

Each test under a tool ends with **`Result: ☐`** — mark it **✅** if the output matched the Expect, **❌** if not.

---

## 🔥 Regression focus — these were the 0.17.2 fixes (re-test first)

> **✅ As of v0.17.2 the issues below were confirmed and FIXED** (epics
> `bd-1wq7z` + `co5wh`) and verified live against the backend. Keep this list as
> the **regression set** — re-test these first to confirm the fixes still hold.
> Each maps to a tool entry below.

### P1 — likely returns a wrong/empty result or silently does nothing
| Tool | Suspected problem |
|------|-------------------|
| `bulk_remove_streams` | Compares backend stream **objects** to int ids → likely always "0 removed" (silent no-op). |
| `match_channels_epg` | Reads `matched`/`unmatched`; backend returns `exact`/`multiple`/`none` → likely always "0 matched". |
| `update_m3u_group_settings` / `bulk_update_m3u_group_settings` | Sends `{name: bool}`; backend expects structured list with **int group ids** → likely silent no-op. |
| `get_orphaned_groups` / `get_auto_created_groups` / `get_groups_with_streams` | Don't unwrap the `{...: [...]}` envelope → likely garbled output (iterating dict keys). |
| `delete_orphaned_groups` | Reads `deleted`/`groups`; backend returns `deleted_groups`/`failed_groups` → likely always "none deleted". |
| `rollback_auto_creation` | Reads `deleted`; engine returns `entities_removed` → likely always "0 channels deleted". |
| `create_backup` | Backend streams a **zip**; client calls `.json()` → likely reports failure even when the backup succeeds. |
| `delete_all_notifications` | Can't send `read_only=False` → likely only clears **read** notifications, not all. |

### P1 — wrong target / misleading success (data-impacting)
| Tool | Suspected problem |
|------|-------------------|
| `add_stream` (dedup modes) | Substring search returns `streams[0]` with no exact-name match → may attach the **wrong** stream. |
| `reorder_streams` | Sends the given list as the full set → omitted streams may be **silently detached**. |
| `clear_auto_created` | Empty `group_ids=[]` reads as falsy → summary says "across all groups"; possible system-wide clear. |
| `cancel_task` | Always reports "cancelled" even when the task wasn't running (`status: not_running`). |
| `delete_channel_group` | Backend may **hide** (not delete) groups with sync settings; tool always says "deleted". |

### P2 — robustness / UX / discovery gaps
| Tool | Suspected problem |
|------|-------------------|
| `dismiss_channel_merge` | 404 re-raises a raw exception (sibling `accept_channel_merge` handles it cleanly). |
| `search_streams` | `limit > 100` can print "… and **-N** more results". |
| `probe_single_stream` | 30s timeout surfaces as a tool failure on slow streams; backend may finish anyway. |
| `apply_profile_to_channels` | May omit required `enabled` field → 422; read-back can silently fail. |
| `create_task_schedule` | `days_of_week` convention + missing `timezone` param → schedules may target the wrong day/time. |
| `delete_task_schedule` | int/str id read-back compare → may falsely confirm deletion. |
| `compute_stream_sort` | Unvalidated freeform input → raw 422 with no guidance. |
| `publish_export` | No `list_publish_configs` tool → operator can't discover valid `config_id`. |
| `build_channel_lineup` | Runtime import of stream fuzzy helpers; "created" count includes pre-existing channels. |

### 🔒 Security — verify no secret leakage
| Tool | Watch |
|------|-------|
| `get_settings` | Confirm SMTP password / `telegram_bot_token` / `discord_webhook_url` are **masked**, never returned raw. |
| `get_journal` | Confirm historic entries (esp. `settings` category) don't surface pre-redaction credential values. |

---

## Tracking checklist

Mark each as you go: ✅ pass · ❌ fail · ⚠️ partial. (124 tools)

- **Channels (20):** list_channels · get_channel · create_channel · update_channel · delete_channel · bulk_delete_channels · add_stream_to_channel · add_stream · bulk_add_streams_to_channel · bulk_assign_epg · remove_stream_from_channel · reorder_streams · assign_channel_numbers · merge_channels · clear_auto_created · find_duplicate_channels · bulk_merge_duplicate_channels · bulk_commit_channels · set_logo_from_epg · build_channel_lineup
- **Dedup (3):** list_pending_channel_merges · accept_channel_merge · dismiss_channel_merge
- **Streams (17):** list_streams · get_stream_health · probe_streams · get_probe_progress · probe_single_stream · get_struck_out_streams · cleanup_struck_out_streams · bulk_remove_streams · cancel_probe · get_probe_results · get_streams_for_channel · search_streams · get_streams_by_ids · probe_bulk_streams · bulk_search_streams · fuzzy_match_stream · match_streams_to_channels
- **Profiles (3):** list_channel_profiles · list_stream_profiles · apply_profile_to_channels
- **Normalization (2):** test_normalization · list_normalization_rules
- **M3U Accounts (9):** list_m3u_accounts · refresh_m3u · refresh_all_m3u · get_m3u_account · create_m3u_account · update_m3u_account · delete_m3u_account · update_m3u_group_settings · bulk_update_m3u_group_settings
- **EPG Sources (10):** list_epg_sources · refresh_epg · refresh_all_epg · match_channels_epg · create_epg_source · update_epg_source · delete_epg_source · get_epg_grid · list_dummy_epg_profiles · generate_dummy_epg
- **Channel Groups (8):** list_channel_groups · create_channel_group · get_orphaned_groups · delete_channel_group · get_hidden_groups · get_auto_created_groups · delete_orphaned_groups · get_groups_with_streams
- **Auto-Creation (13):** list_auto_creation_rules · run_auto_creation · get_auto_creation_rule · toggle_auto_creation_rule · bulk_toggle_auto_creation_rules · duplicate_auto_creation_rule · delete_auto_creation_rule · create_auto_creation_rule · update_auto_creation_rule · list_auto_creation_executions · rollback_auto_creation · analyze_auto_creation_rules · get_auto_creation_debug_bundle
- **Tasks & Schedules (7):** list_tasks · run_task · cancel_task · get_task_history · list_task_schedules · create_task_schedule · delete_task_schedule
- **Stats & Analytics (14):** get_channel_stats · get_top_watched · get_bandwidth · get_popularity_rankings · get_watch_history · get_unique_viewers · compute_stream_sort · get_provider_stats · get_user_watch_time · get_user_channel_breakdown · get_trending · get_channel_popularity · get_activity · get_channel_bandwidth
- **System & Backup (6):** get_settings · create_backup · get_export_sections · list_saved_backups · delete_saved_backup · get_journal
- **Notifications & Alerts (5):** list_notifications · mark_notifications_read · delete_all_notifications · list_alert_methods · test_alert_method
- **Export & Publish (7):** list_export_profiles · generate_export · create_export_profile · delete_export_profile · list_cloud_targets · publish_export · list_publish_configs

---

# Per-tool test prompts
## Channels

#### `list_channels(group_id: int = None, search: str = None, max_streams: int = None, min_streams: int = None, limit: int = 50, compact: bool = False)` — READ-ONLY
_List all channels with optional filters for group, name search, and stream count; compact mode returns pipe-delimited output._
1. **"Show me all my channels, 50 at a time."** — Expect: `list_channels()` called with no args; numbered list like `#101: US : ESPN (id=42) — 1 stream`; total shown vs total found counts are correct. — Result: ☐
2. **"List channels in a group that doesn't exist, like 'Empty Test Group'"** — Expect: Returns "No channels found" (empty list from backend), not an error crash. — Result: ☐
3. **"List all channels with 0 streams so I can clean up empties"** — Expect: `list_channels(max_streams=0, compact=False)` called; paginated fetch loop runs; only channels where `len(streams) == 0` returned; shown count matches actual zero-stream channels. — Result: ☐
4. **"Give me a compact list of all my channels for analysis"** — Expect: `list_channels(compact=True)` called; pipe-delimited `number|id|name|streams` format with all channels (no limit applied); `?` appears for channels missing `channel_number`. — Result: ☐
5. **"Search for channels named 'ESPN' with at least 1 stream"** — Expect: `list_channels(search='ESPN', min_streams=1)` called; search passed to backend AND stream-count filter applied afterward. — Result: ☐

---

#### `get_channel(channel_id: int)` — READ-ONLY
_Retrieve detailed information about a single channel by ID, including streams, group, EPG tvg_id, and logo status._
1. **"Tell me everything about the 'US : ESPN' channel."** — Expect: `get_channel(channel_id=<resolved id>)` called; formatted block with name, ID, number, group_id, EPG tvg_id, logo presence, stream count with first 10 stream IDs, and auto_created flag. — Result: ☐
2. **"Look up a channel called 'Does Not Exist'"** — Expect: Backend returns 404; error caught and returned as `"Error getting channel <id>: GET /api/channels/<id> -> HTTP 404 Not Found"`; no stack trace in Claude response. — Result: ☐
3. **"Check the details for a channel with number 0"** — Expect: Backend returns 404 or 400; same graceful error handling. — Result: ☐

---

#### `create_channel(name: str, channel_number: int = None, group_id: int = None)` — WRITE
_Create a new channel with an optional channel number and group assignment._
1. **"Create a channel called 'Fox News HD' in the 'USA | Sports' group, number 360."** — Expect: `create_channel(name='Fox News HD', channel_number=360, group_id=<resolved id>)` called; `group_id` mapped to `channel_group_id` in payload; response echoes `Channel created: #360: Fox News HD (id=<new_id>, group_id=<resolved id>)`. — Result: ☐
2. **"Create a channel called 'Test' with no number or group"** — Expect: `create_channel(name='Test')` called; payload contains only `{"name": "Test"}`; response shows `channel_number=?` and no group info; neither `channel_number` nor `channel_group_id` sent as `null`. — Result: ☐
3. **"Create a channel with an empty name ''"** — Expect: Backend returns 400 or 422; graceful error message, not a created channel with a blank name. — Result: ☐
4. **"Create a channel in a group called 'Does Not Exist Group'"** — Expect: Backend may 400 or silently ignore the group; returned `group_id` reflects actual persisted value, not the requested value. — Result: ☐

---

#### `update_channel(channel_id: int, name: str = None, channel_number: int = None, group_id: int = None)` — WRITE
_Update an existing channel's name, number, or group assignment via a PATCH request._
1. **"Rename the 'US : ESPN' channel to 'US : ESPN Classic' and move it to the 'USA | Movies' group."** — Expect: `update_channel(channel_id=<resolved id>, name='US : ESPN Classic', group_id=<resolved id>)` called; `group_id` mapped to `channel_group_id`; response echoes backend-returned state: `Channel <id> updated: name='US : ESPN Classic', channel_number=<N>, group_id=<resolved id>`. — Result: ☐
2. **"Update the 'US : ESPN Classic' channel but don't change anything"** — Expect: `update_channel(channel_id=<resolved id>)` called with no optional args; tool hits the `if not payload: return "No changes specified."` guard and returns early; no HTTP request made. — Result: ☐
3. **"Update a channel called 'Does Not Exist'"** — Expect: Backend returns 404; graceful error message. — Result: ☐
4. **"Move the 'US : ESPN' channel to a group called 'Does Not Exist Group'"** — Expect: Response `group_id` field reflects actual persisted value, not the requested value. — Result: ☐

---

#### `delete_channel(channel_id: int)` — DESTRUCTIVE
_Permanently delete a single channel by ID._
1. **"Delete the 'US : ESPN News' channel."** — Expect: `delete_channel(channel_id=<resolved id>)` called; backend returns 204; tool returns `"Channel <id> deleted."`; channel no longer appears in `list_channels`. — Result: ☐
2. **"Delete a channel called 'Does Not Exist'"** — Expect: Backend returns 404; graceful error: `"Error deleting channel <id>: GET ... -> HTTP 404 Not Found"`. — Result: ☐
3. **"Delete the 'US : ESPN News' channel a second time after it's already been deleted"** — Expect: Same 404 path; no crash. — Result: ☐

---

#### `bulk_delete_channels(channel_ids: list[int])` — DESTRUCTIVE
_Delete multiple channels serially, collecting errors without aborting, then reporting a summary._
1. **"Delete the 'US : ESPN U', 'US : ESPN 2', and 'Radio: ESPN Radio' channels all at once."** — Expect: `bulk_delete_channels(channel_ids=[<id1>, <id2>, <id3>])` called; three sequential deletes issued; response: `"Bulk delete complete: 3 deleted, 0 errors out of 3 requested."`; all three channels gone. — Result: ☐
2. **"Bulk delete 'US : ESPN U', a channel called 'Does Not Exist', and 'Radio: ESPN Radio'"** — Expect: Returns `"Bulk delete complete: 2 deleted, 1 errors out of 3 requested."` with first error detail; two valid channels deleted despite mid-list error. — Result: ☐
3. **"Bulk delete with an empty list []"** — Expect: Returns `"Bulk delete complete: 0 deleted, 0 errors out of 0 requested."`; no crash. — Result: ☐
4. **"Bulk delete 100 channels"** — Expect: All 100 sequential HTTP calls complete; no timeout during the run. — Result: ☐

---

#### `add_stream_to_channel(channel_id: int, stream_id: int)` — WRITE
_Add a single stream to a channel via `POST /api/channels/{id}/add-stream`._
1. **"Add the 'US: ESPN FHD' stream to the 'US : ESPN' channel."** — Expect: `add_stream_to_channel(channel_id=<resolved id>, stream_id=<resolved id>)` called; returns `"Stream <id> added to channel <id>."`; `get_channel` on 'US : ESPN' confirms stream now in stream list. — Result: ☐
2. **"Add a stream called 'Does Not Exist Stream' to the 'US : ESPN' channel"** — Expect: Backend returns 404 or 400; graceful error string. — Result: ☐
3. **"Add the 'US: ESPN FHD' stream to the 'US : ESPN' channel again when it's already there"** — Expect: Backend either silently deduplicates or returns an error; tool handles both without crashing. — Result: ☐
4. **"Add the 'US: ESPN FHD' stream to a channel called 'Does Not Exist'"** — Expect: Backend 404; graceful error. — Result: ☐

---

#### `add_stream(stream_name: str, group_id: int, dedup_action: str = "prompt")` — WRITE
_Create a channel from a stream name with deduplication control: prompt, force_new, or merge_if_found._
1. **"Add the stream 'US: ESPN' to the 'USA | Sports' group and let me know if there's a duplicate."** — Expect: `add_stream(stream_name='US: ESPN', group_id=<resolved id>, dedup_action='prompt')` called; if candidate found: response contains `action=pending_merge`, a `merge_id`, candidate channel name/id, and confidence percentage; if no candidate: new channel created and stream attached. — Result: ☐
2. **"Add stream 'US: ESPN' to the 'USA | Sports' group, force a new channel regardless of duplicates"** — Expect: `add_stream(stream_name='US: ESPN', group_id=<resolved id>, dedup_action='force_new')` called; dedup enqueue skipped; channel created and stream attached; no pending merge row created. — Result: ☐
3. **"Add stream 'XYZ Totally Unique 12345' to the 'USA | Sports' group"** — Expect: No candidate found; tool proceeds directly to channel create + stream attach; new channel appears in `list_channels` for that group. — Result: ☐
4. **"Add stream with invalid dedup_action='auto'"** — Expect: Tool immediately returns validation error before any backend call: `"Invalid dedup_action 'auto'. Must be one of: force_new, merge_if_found, prompt"`. — Result: ☐
5. **"Add stream 'US: ESPN' with dedup_action='merge_if_found' when confidence is above threshold"** — Expect: `merge_if_found` called; if `meets_threshold=True` in enqueue response, tool auto-calls `channel_merges_accept` and returns `"merge_if_found: stream '...' merged into existing channel '...'"`. — Result: ☐
6. **"Add stream '' (empty name) to the 'USA | Sports' group"** — Expect: Backend's enqueue endpoint rejects blank `stream_name` with 400; tool returns a structured `action=error` string. — Result: ☐

---

#### `bulk_add_streams_to_channel(channel_id: int, stream_ids: list[int])` — WRITE
_Add multiple streams to a channel in a single backend call, skipping streams already present._
1. **"Add a second stream to 'US : ESPN' first, then add the streams 'US: ESPN FHD', 'US: ESPN 2 FHD', and 'US: ESPN 2' to the 'US : ESPN' channel all in one go."** — Expect: `bulk_add_streams_to_channel(channel_id=<resolved id>, stream_ids=[<id1>, <id2>, <id3>])` called; returns `"Added 3 stream(s) to channel <id>; channel now has N streams."`; `get_channel` on 'US : ESPN' confirms all stream IDs present. — Result: ☐
2. **"Add 'US: ESPN FHD' and 'US: ESPN 2 FHD' to the 'US : ESPN' channel, where 'US: ESPN FHD' is already on the channel"** — Expect: Response shows `"Added 1 stream(s) to channel <id> (1 already present); channel now has N streams."` and `"Added: [<id of US: ESPN 2 FHD>]"`; already-present stream is in `skipped`. — Result: ☐
3. **"Add an empty list of streams to the 'US : ESPN' channel"** — Expect: `stream_ids=[]`; likely `"Added 0 stream(s) to channel <id>."` without error; no crash. — Result: ☐
4. **"Add streams to a channel called 'Does Not Exist'"** — Expect: Backend 404; graceful error: `"Error adding streams to channel <id>: ..."`. — Result: ☐

---

#### `bulk_assign_epg(mappings: list[dict])` — WRITE
_Assign EPG tvg_id values to multiple channels at once via sequential PATCH calls._
1. **"Set the EPG ID for the 'US : ESPN' channel to 'ESPN.us' and the 'US : ESPN 2' channel to 'ESPN2.us' in one shot."** — Expect: `bulk_assign_epg(mappings=[{"channel_id": <US : ESPN id>, "tvg_id": "ESPN.us"}, {"channel_id": <US : ESPN 2 id>, "tvg_id": "ESPN2.us"}])` called; returns `"Updated EPG assignments for 2/2 channels."`; both channels show correct `tvg_id` via `get_channel`. — Result: ☐
2. **"Clear the EPG ID for the 'US : ESPN' channel by passing an empty tvg_id"** — Expect: `mappings=[{"channel_id": <US : ESPN id>, "tvg_id": ""}]` sent; `{"tvg_id": ""}` sent to PATCH; channel EPG link cleared, not silently ignored. — Result: ☐
3. **"Bulk assign EPG with a mapping that is missing 'channel_id'"** — Expect: Tool hits `if cid is None` guard; appends `"missing channel_id in mapping"` to errors; summary: `"Updated EPG assignments for 0/1 channels."` with one error. — Result: ☐
4. **"Bulk assign EPG to a channel called 'Does Not Exist'"** — Expect: PATCH returns 404; error appended; final error count surfaced in summary. — Result: ☐

---

#### `remove_stream_from_channel(channel_id: int, stream_id: int)` — WRITE
_Remove a single stream from a channel via `POST /api/channels/{id}/remove-stream`._
1. **"Remove the 'US: ESPN FHD' stream from the 'US : ESPN' channel."** — Expect: `remove_stream_from_channel(channel_id=<resolved id>, stream_id=<resolved id>)` called; returns `"Stream <id> removed from channel <id>."`; `get_channel` on 'US : ESPN' confirms stream no longer in stream list. — Result: ☐
2. **"Remove the 'US: ESPN FHD' stream from the 'US : ESPN' channel when it isn't on the channel"** — Expect: Backend may 400 or silently succeed; graceful error handling either way. — Result: ☐
3. **"Remove the 'US: ESPN FHD' stream from a channel called 'Does Not Exist'"** — Expect: Backend 404; graceful error string. — Result: ☐

---

#### `reorder_streams(channel_id: int, stream_ids: list[int])` — WRITE
_Set the priority order of streams within a channel by supplying the full ordered stream ID list (first = highest priority)._
1. **"Add a second stream to 'US : ESPN' first (e.g. 'US: ESPN FHD'), then reorder the streams on the 'US : ESPN' channel so 'US: ESPN FHD' comes first, then 'US: ESPN'."** — Expect: `reorder_streams(channel_id=<resolved id>, stream_ids=[<id of US: ESPN FHD>, <id of US: ESPN>])` called; returns `"Streams reordered for channel <id>. New order: [<id1>, <id2>]"`; `get_channel` on 'US : ESPN' confirms updated stream order. — Result: ☐
2. **"Reorder streams on the 'US : ESPN' channel with an incomplete list (omitting one stream)"** — Expect: Backend may reject the partial list or silently remove the omitted stream; verify graceful handling and confirm no silent stream detachment without an explicit error. — Result: ☐
3. **"Reorder streams on the 'US : ESPN' channel with stream IDs that don't belong to it"** — Expect: Backend rejects; graceful error handling. — Result: ☐
4. **"Reorder streams on the 'US : ESPN' channel with an empty list []"** — Expect: Backend may clear all streams from the channel; confirm behavior is surfaced explicitly rather than silently applied. — Result: ☐

---

#### `assign_channel_numbers(channel_ids: list[int], starting_number: int = None)` — WRITE
_Bulk-assign sequential channel numbers to a list of channels, starting from a specified number or auto-assigned if omitted._
1. **"Assign sequential channel numbers to 'US : ESPN', 'US : ESPN 2', and 'US : ESPN News' starting from 200."** — Expect: `assign_channel_numbers(channel_ids=[<US : ESPN id>, <US : ESPN 2 id>, <US : ESPN News id>], starting_number=200)` called; returns `"Assigned numbers to 3 channels starting from 200."`; `get_channel` confirms numbers 200, 201, 202. — Result: ☐
2. **"Auto-assign channel numbers to 'US : ESPN', 'US : ESPN 2', and 'US : ESPN News' (no starting number)"** — Expect: `assign_channel_numbers` called without `starting_number`; response: `"Assigned numbers to 3 channels starting from auto."`; backend chooses sensible numbers. — Result: ☐
3. **"Assign channel numbers to an empty list"** — Expect: `channel_ids=[]`; response: `"Assigned numbers to 0 channels starting from auto."`; no backend error. — Result: ☐
4. **"Assign starting number 0 or a negative number"** — Expect: Backend validates and returns graceful error if rejected. — Result: ☐

---

#### `merge_channels(target_channel_id: int, source_channel_ids: list[int])` — DESTRUCTIVE
_Merge one or more source channels into a target channel, keeping the target and absorbing source streams._
1. **"Merge the 'US : ESPN U' channel into 'US : ESPN', keeping 'US : ESPN' and deleting the other."** — Expect: `merge_channels(target_channel_id=<US : ESPN id>, source_channel_ids=[<US : ESPN U id>])` called; returns `"Merged 1 channels into channel <id>."`; source channel deleted; target contains its streams. — Result: ☐
2. **"Merge a 'US : ESPN Alt' channel into a target channel that doesn't exist"** — Expect: Backend returns 404 or 422; graceful error: `"Error merging channels: POST /api/channels/bulk-merge -> HTTP 404 ..."`. — Result: ☐
3. **"Merge the 'US : ESPN' channel into itself"** — Expect: Backend may 400 or silently no-op; no crash. — Result: ☐
4. **"Merge into 'US : ESPN' with an empty source list []"** — Expect: `source_channel_ids=[]`; response: `"Merged 0 channels into channel <id>."`; no crash. — Result: ☐

---

#### `clear_auto_created(group_ids: list[int] = None)` — DESTRUCTIVE
_Delete all channels marked `auto_created=True`, optionally scoped to specific group IDs._
1. **"Clear all auto-created channels from the 'USA | Sports' and 'Radio' groups."** — Expect: `clear_auto_created(group_ids=[<USA | Sports id>, <Radio id>])` called; returns `"Cleared N auto-created channels in 2 groups."`; channels with `auto_created=True` in those groups are gone. — Result: ☐
2. **"Clear ALL auto-created channels across the entire system (no group filter)"** — Expect: `clear_auto_created()` called with no args; payload is `{}`; response says "across all groups" with accurate deletion count. — Result: ☐
3. **"Clear auto-created channels from a group with no auto-created channels — try 'USA | Kids'"** — Expect: Returns `"Cleared 0 auto-created channels in 1 groups."` without error. — Result: ☐
4. **"Clear auto-created with empty group_ids list []"** — Expect: Backend behavior explicitly observed — either treated as no-op or treated as all groups; confirm the response accurately describes scope. — Result: ☐

---

#### `find_duplicate_channels()` — READ-ONLY
_Scan all channels using the normalization engine to identify channels resolving to the same normalized name._
1. **"Find all duplicate channels in my lineup so I can see what needs merging."** — Expect: `find_duplicate_channels()` called; grouped report of duplicate clusters with normalized name, channel count, and each channel's number/name/id/stream count/group; up to 30 groups shown. — Result: ☐
2. **"Find duplicates when there are none"** — Expect: Backend returns `{"groups": []}`; tool returns `"No duplicate channels found."`. — Result: ☐
3. **"Find duplicates when there are more than 30 groups"** — Expect: First 30 shown; `"... and N more groups"` appended; count is accurate. — Result: ☐

---

#### `bulk_merge_duplicate_channels(merges: list[dict])` — DESTRUCTIVE
_Execute a batch of merge operations produced after `find_duplicate_channels`, keeping target channels and absorbing sources._
1. **"Merge the duplicates I found: keep the main 'US : ESPN' and absorb its duplicates; also keep the main 'US : ESPN 2' and absorb its duplicate."** — Expect: `bulk_merge_duplicate_channels(merges=[{"target_channel_id": <main US : ESPN id>, "source_channel_ids": [<US : ESPN dup ids>]}, {"target_channel_id": <main US : ESPN 2 id>, "source_channel_ids": [<US : ESPN 2 dup id>]}])` called; returns summary: `"Bulk merge complete: 2 merged, 0 failed."` with per-merge results showing target name, sources absorbed, and total streams. — Result: ☐
2. **"Bulk merge where one target channel doesn't exist"** — Expect: Backend returns a result entry with `success=False`; tool shows `"✗ Channel <id>: <error>"`; count is `1 merged, 1 failed`. — Result: ☐
3. **"Bulk merge with an empty merges list []"** — Expect: Returns `"Bulk merge complete: 0 merged, 0 failed."`; no crash. — Result: ☐
4. **"Bulk merge with a malformed merges entry (missing target_channel_id)"** — Expect: Backend returns 422; graceful error via outer exception handler. — Result: ☐

---

#### `bulk_commit_channels(operations: list[dict], validate_only: bool = False, continue_on_error: bool = False)` — WRITE
_Submit a batch of channel operations atomically to the backend bulk-commit endpoint._
1. **"Create two new channels atomically: 'Test Channel A' at number 900 and 'Test Channel B' at number 901, both in the 'USA | Local PBS' group. Use validate-only mode first."** — Expect: `bulk_commit_channels(operations=[{"type": "createChannel", "tempId": -1, "name": "Test Channel A", "channelNumber": 900, "groupId": <USA | Local PBS group id>}, {"type": "createChannel", "tempId": -2, "name": "Test Channel B", "channelNumber": 901, "groupId": <USA | Local PBS group id>}], validate_only=True)` called; response: `"Bulk commit SUCCESS: 2 operations submitted. (validate-only mode — no changes applied)"`; then repeated without `validate_only=True` to apply. — Result: ☐
2. **"Bulk commit with an invalid operation type 'badType'"** — Expect: Backend 422; graceful error with validation detail surfaced including the offending field path. — Result: ☐
3. **"Bulk commit with continue_on_error=True where one operation fails"** — Expect: Partial success; response shows `SUCCESS=False`; individual failures in `validationIssues`; `id_mappings` captures any successful creations' temp-id → real-id maps. — Result: ☐
4. **"Bulk commit with validate_only=True and invalid operations"** — Expect: Returns validation issues without applying anything; `(validate-only mode — no changes applied)` in response. — Result: ☐

---

#### `set_logo_from_epg(channel_ids: list[int])` — WRITE
_For each channel, read its linked EPG entry's icon_url and PATCH the channel with the resulting logo_id._
1. **"Set the logos for the 'US : ESPN', 'US : ESPN 2', and 'US : ESPN News' channels using their EPG entries."** — Expect: `set_logo_from_epg(channel_ids=[<US : ESPN id>, <US : ESPN 2 id>, <US : ESPN News id>])` called; returns summary: `"Set logos from EPG: 3 assigned, 0 skipped (no EPG link), 0 skipped (no icon_url), 0 errors out of 3 requested."`. — Result: ☐
2. **"Set logos for a channel with no EPG link — try the 'Radio: Yacht Rock Radio' channel if it has no EPG assigned"** — Expect: Channel counted in `skipped_no_epg`; summary shows 0 assigned, 1 skipped (no EPG link). — Result: ☐
3. **"Set logos for a channel whose EPG entry has no icon_url"** — Expect: Counted in `skipped_no_icon`; summary: 0 assigned, 1 skipped (no icon_url). — Result: ☐
4. **"Set logos for 'US : ESPN' and 'US : ESPN 2' when they share the same EPG icon_url"** — Expect: Logo cache reuses same `logo_id` for the second channel; only one logo creation POST made. — Result: ☐
5. **"Set logos for a channel called 'Does Not Exist'"** — Expect: 404 caught; added to `errors`; summary: `"0 assigned, ... 1 errors"`. — Result: ☐

---

#### `build_channel_lineup(channels: list[dict], group_id: int, provider_id: int = None, market: str = "east")` — WRITE
_Orchestrated multi-step tool: bulk-create channels from a name/number list, then fuzzy-match and assign streams from a provider._
1. **"Build a lineup in the 'NFL Game Pass' group with these channels: ESPN at 200, ESPN 2 at 201, ESPN News at 202. Match streams from the 'USA | Sports' provider in the east market."** — Expect: `build_channel_lineup(channels=[{"name": "ESPN", "number": 200}, {"name": "ESPN 2", "number": 201}, {"name": "ESPN News", "number": 202}], group_id=<NFL Game Pass group id>, provider_id=<resolved id>, market="east")` called; all three created, fuzzy-matched, and streams assigned; summary: `"Lineup built: 3 channels created, 3 streams matched, 0 unmatched."`; `list_channels` for that group confirms all three channels with streams. — Result: ☐
2. **"Build lineup where no streams match"** — Expect: All channels appear in the Unmatched section; `0 streams matched, 3 unmatched.`; channels still exist with 0 streams (build is not rolled back on match failure). — Result: ☐
3. **"Build lineup with market='west'"** — Expect: Channels created; fuzzy score function receives `market='west'`; correct market preference passed to `_score_match` for all channels. — Result: ☐
4. **"Build lineup with a channel entry missing the 'name' key"** — Expect: Outer `except Exception as e` catches the `KeyError`; returns `"Error building channel lineup: 'name'"`; no unhandled crash. — Result: ☐

---

## Dedup (Pending Channel Merges)

#### `list_pending_channel_merges(group_id: int = None, status: str = None, page: int = None, page_size: int = None)` — READ-ONLY
_List pending or resolved channel-merge candidates from the dedup queue with optional group and status filtering._
1. **"Show me all the pending channel merge suggestions waiting for review."** — Expect: `list_pending_channel_merges()` called; default `status='pending'` applied; response dict contains `{merges: [...], total, page, page_size, total_pages}`; each row contains `id`, `stream_name`, `group_id`, `candidate_channel_id`, `confidence`, `status`, `created_at`. — Result: ☐
2. **"List merge history — show me dismissed merges from the 'USA | Sports' group"** — Expect: `list_pending_channel_merges(group_id=<USA | Sports id>, status='dismissed')` called; `status='dismissed'` sent; results filtered to that group. — Result: ☐
3. **"List merged (accepted) history"** — Expect: `list_pending_channel_merges(status='merged')` called; terminal-state rows returned. — Result: ☐
4. **"List pending merges with status='invalid'"** — Expect: Backend returns 400: `"status must be one of ['pending', 'merged', 'dismissed']; got 'invalid'"`; exception propagates to Claude as a tool call error; Claude surfaces a useful message to the operator. — Result: ☐
5. **"List pending merges page 2 with page_size 10"** — Expect: `list_pending_channel_merges(page=2, page_size=10)` called; correct 10 rows from offset 10 returned; `total_pages` is correct. — Result: ☐
6. **"List pending merges when the queue is empty"** — Expect: Returns `{merges: [], total: 0, page: 1, page_size: 50, total_pages: 0}`. — Result: ☐
7. **"List with page_size=201 (over the max of 200)"** — Expect: Backend returns 400 (`page_size must be between 1 and 200`); tool re-raises; Claude sees a tool error. — Result: ☐

---

#### `accept_channel_merge(merge_id: int)` — DESTRUCTIVE
_Confirm a pending dedup merge: adds the stream to the candidate channel, flips the pending row to `merged`, and writes the audit journal entry._
1. **"Accept the pending merge suggestion for the 'US : ESPN' channel — confirm it should be merged into that existing channel."** — Expect: `accept_channel_merge(merge_id=<resolved id>)` called; returns dict: `{merged_into_channel_id, journal_entry_id, source_stream_id, confidence, status: 'merged'}`; `list_pending_channel_merges(status='merged')` confirms row is now merged. — Result: ☐
2. **"Accept the same pending merge for 'US : ESPN' again (idempotency)"** — Expect: Returns same outcome envelope with `status: 'merged'` and original `journal_entry_id`; no error. — Result: ☐
3. **"Accept a merge for 'US : ESPN' where the target channel was deleted from Dispatcharr"** — Expect: Backend returns 404; tool catches it and returns `{"error": {"code": "TARGET_NOT_FOUND", "message": "..."}}`; Claude prompts operator to dismiss the stale pending merge. — Result: ☐
4. **"Accept a merge that has already been dismissed"** — Expect: Backend returns 409; tool catches it and returns `{"error": {"code": "INVALID_STATE", "message": "..."}}`; Claude does NOT retry. — Result: ☐
5. **"Accept a merge that doesn't exist"** — Expect: Backend returns 404 for the pending row; tool returns `TARGET_NOT_FOUND` error envelope; error message is sensible in context. — Result: ☐
6. **"Accept a merge where the stream name matches multiple Dispatcharr streams"** — Expect: Backend logs a WARN; return envelope shows `status: 'merged'`; operator must manually assign the stream. — Result: ☐

---

#### `dismiss_channel_merge(merge_id: int)` — DESTRUCTIVE
_Reject a pending dedup candidate, recording the dismissal in the audit journal with no Dispatcharr call._
1. **"Dismiss the pending merge suggestion for the 'US : ESPN 2' channel — that suggested merge is wrong, don't merge those channels."** — Expect: `dismiss_channel_merge(merge_id=<resolved id>)` called; returns `{journal_entry_id, status: 'dismissed'}`; `list_pending_channel_merges(status='dismissed')` confirms row is now dismissed. — Result: ☐
2. **"Dismiss the same pending merge for 'US : ESPN 2' again (idempotency)"** — Expect: Returns original outcome envelope with same `journal_entry_id`; no error. — Result: ☐
3. **"Dismiss a merge for 'US : ESPN' that has already been accepted (cross-state)"** — Expect: Backend returns 409; tool catches it and returns `{"error": {"code": "INVALID_STATE", "message": "Pending merge id=<id> is already in a terminal state that does not allow dismissal (e.g. already merged). Do not retry this operation."}}`; Claude does NOT retry. — Result: ☐
4. **"Dismiss a pending merge that doesn't exist"** — Expect: Backend returns 404; exception propagates (not caught by tool); Claude sees raw exception — confirm Claude surfaces a useful message. — Result: ☐
5. **"Use dismiss as recovery after accept returns TARGET_NOT_FOUND for the 'US : ESPN' merge"** — Expect: `dismiss_channel_merge` succeeds with `status: 'dismissed'`; stale row cleaned up. — Result: ☐

---

## Streams

#### `list_streams(group: str | None = None, provider_id: int | None = None, search: str | None = None, page: int = 1, page_size: int = 50)` — READ-ONLY
_List streams with optional filtering by group name, M3U provider ID, or name search; results are paginated._
1. **"Show me all streams in the 'USA | Sports' group, page 1."** — Expect: `list_streams(group="USA | Sports")` called; response lists stream names, IDs, group labels, and provider names; header reads `Showing N of TOTAL streams (page 1):`; underlying call sends `channel_group_name=USA | Sports` to `GET /api/streams`. — Result: ☐
2. **"List streams for a group name that doesn't exist, like 'ZZZ_NoSuchGroup'"** — Expect: ECM returns an empty page; Claude says "No streams found." not an error. — Result: ☐
3. **"List streams with page_size=200"** — Expect: Code clamps to `min(page_size, 100)` = 100; Claude does not report 200 per page. — Result: ☐

---

#### `get_stream_health()` — READ-ONLY
_Get an overview of stream health from the most recent probe results via `GET /api/stream-stats/summary`._
1. **"What's the current health status of all streams?"** — Expect: `get_stream_health()` called; each key in the summary dict printed with a title-cased label (e.g., "Total Streams: 843", "Healthy Count: 712", "Failed Count: 131"). — Result: ☐
2. **"Ask for stream health when ECM has never been probed"** — Expect: Backend returns empty dict or `{}`; Claude returns "No stream health data available. Run a probe first." — Result: ☐
3. **"Ask for stream health when ECM backend is unreachable"** — Expect: `get` raises; Claude returns "Error getting stream health: ..." with HTTP detail. — Result: ☐

---

#### `probe_streams()` — WRITE
_Start a background probe of ALL streams; uses 300 s timeout on the POST._
1. **"Start a health probe on all streams now."** — Expect: `probe_streams()` called; response is "Stream probe started. \<backend message\>"; backend call is `POST /api/stream-stats/probe/all`; use `get_probe_progress()` to follow up. — Result: ☐
2. **"Start a probe when one is already running"** — Expect: Backend returns 409 or message indicating probe already in progress; Claude surfaces the backend message without crashing. — Result: ☐
3. **"Start probe then immediately ask for progress"** — Expect: `get_probe_progress()` called after; `in_progress: true` shown. — Result: ☐

---

#### `get_probe_progress()` — READ-ONLY
_Check the progress of an ongoing stream probe via `GET /api/stream-stats/probe/progress`._
1. **"How far along is the stream probe?"** — Expect: `get_probe_progress()` called; if a probe is running: `Probe in progress: N/TOTAL (PCT%)` plus success/failed/skipped counts and current stream name; if no probe running: "No probe is currently running." — Result: ☐
2. **"Check probe progress when no probe has ever been started"** — Expect: Backend returns `{"in_progress": false, ...}`; Claude returns "No probe is currently running." — Result: ☐
3. **"Check probe progress when `total` is 0"** — Expect: Division-by-zero guard fires; 0% shown, not a ZeroDivisionError. — Result: ☐

---

#### `probe_single_stream(stream_id: int)` — WRITE
_Probe a single stream by ID to check its health via `POST /api/stream-stats/probe/{stream_id}`._
1. **"Check the health of the 'US: ESPN FHD' stream."** — Expect: `probe_single_stream(stream_id=<resolved id>)` called; response: "Stream <id> probe complete. Status: \<status\>"; status is one of "success", "failed", or "timeout". — Result: ☐
2. **"Probe a stream called 'Does Not Exist Stream'"** — Expect: Backend returns 404; Claude returns "Error probing stream <id>: POST /api/stream-stats/probe/<id> -> HTTP 404 Not Found". — Result: ☐
3. **"Probe a stream with ID -1 or 0"** — Expect: No client-side guard; backend validation is the only gate; graceful error surfaced. — Result: ☐

---

#### `get_struck_out_streams()` — READ-ONLY
_List streams that have exceeded the consecutive-failure strike threshold, with their channel assignments._
1. **"Which streams have been struck out due to probe failures?"** — Expect: `get_struck_out_streams()` called; struck stream names, IDs, failure counts, and channel assignments shown; if strike detection disabled: "Strike detection is disabled."; if no streams struck out: threshold shown with a clean message. — Result: ☐
2. **"Ask for struck-out streams when strike detection is disabled in ECM settings"** — Expect: Backend returns `{"enabled": false}`; Claude says "Strike detection is disabled." — Result: ☐
3. **"Ask for struck-out streams when more than 30 are struck"** — Expect: Code hard-caps display at 30 with a "... and N more" line; truncation is visible to the operator. — Result: ☐

---

#### `cleanup_struck_out_streams(delete_empty_channels: bool = False)` — DESTRUCTIVE
_Remove all struck-out streams from their channels in one bulk operation; optionally delete channels left with no streams._
1. **"Clean up all struck-out streams and delete any channels that become empty."** — Expect: `cleanup_struck_out_streams(delete_empty_channels=True)` called; response shows count of struck streams removed, stream-channel links removed, and list of deleted channels. — Result: ☐
2. **"Clean up struck-out streams when there are none"** — Expect: `if not streams:` fires; returns "No struck-out streams to clean up"; clean no-op. — Result: ☐
3. **"Clean up with `delete_empty_channels=True` when channel GET fails mid-loop"** — Expect: Inner `except Exception: pass` silently skips that channel; note whether the omitted channel is visible in the summary. — Result: ☐
4. **"Run cleanup twice in a row (idempotency)"** — Expect: Second call finds no struck streams; returns "No struck-out streams to clean up." — Result: ☐

---

#### `bulk_remove_streams(channel_id: int, stream_ids: list[int])` — DESTRUCTIVE
_Remove multiple streams from a specific channel in one operation by PATCHing the channel's stream list._
1. **"Add a second stream to 'US : ESPN' first (e.g. 'US: ESPN FHD'), then remove the streams 'US: ESPN FHD' and 'US: ESPN' from the 'US : ESPN' channel."** — Expect: `bulk_remove_streams(channel_id=<US : ESPN id>, stream_ids=[<id of US: ESPN FHD>, <id of US: ESPN>])` called; tool fetches current stream list, filters out specified IDs, PATCHes back remainder; response: "Removed N streams from channel <id>. Remaining: M streams." — Result: ☐
2. **"Remove streams that are not actually in the 'US : ESPN' channel"** — Expect: `actually_removed == 0`; Claude returns "None of the specified streams were in channel <id>." — clean no-op. — Result: ☐
3. **"Remove all streams from the 'US : ESPN' channel (empty the stream list)"** — Expect: `filtered = []`; PATCH sends `{"streams": []}`; confirm backend accepts empty list without 400. — Result: ☐
4. **"Pass an empty `stream_ids` list"** — Expect: `remove_set = set()`; `filtered` equals `current_streams`; `actually_removed = 0`; returns "None of the specified streams were in channel." — correct no-op. — Result: ☐

---

#### `cancel_probe()` — WRITE
_Cancel the currently running stream probe via `POST /api/stream-stats/probe/cancel`._
1. **"Cancel the stream probe that's running right now."** — Expect: `cancel_probe()` called; response: "Probe cancelled. \<backend message\>". — Result: ☐
2. **"Cancel probe when no probe is running"** — Expect: Backend behavior determines the response; Claude relays whatever message the backend sends without erroring. — Result: ☐
3. **"Cancel probe immediately after starting one"** — Expect: Cancel actually stops the probe; follow up with `get_probe_progress()` confirms `in_progress: false`. — Result: ☐

---

#### `get_probe_results()` — READ-ONLY
_Get results from the most recent completed probe run via `GET /api/stream-stats/probe/results`._
1. **"Show me the results from the last stream probe."** — Expect: `get_probe_results()` called; if results are a dict: key/value pairs printed with titled labels; if results are a list: total count, healthy count, and failed count shown. — Result: ☐
2. **"Ask for probe results when no probe has ever completed"** — Expect: Backend returns empty; `if not result:` returns "No probe results available." for `{}`, `[]`, and `None`. — Result: ☐
3. **"Ask for probe results when the response is a large list (1000+ streams)"** — Expect: Code returns only the summary (aggregate counts); no per-stream detail visible via MCP alone. — Result: ☐

---

#### `get_streams_for_channel(channel_id: int)` — READ-ONLY
_Get detailed stream information for a specific channel including names, groups, and providers._
1. **"List all streams assigned to the 'US : ESPN' channel."** — Expect: `get_streams_for_channel(channel_id=<resolved id>)` called; numbered streams listed with name, ID, group, and provider; hits `GET /api/channels/<id>/streams`. — Result: ☐
2. **"Get streams for a channel with no streams assigned — try 'Radio: Yacht Rock Radio' if it has no streams"** — Expect: `if not streams:` fires; returns "Channel <id> has no streams assigned." — clean. — Result: ☐
3. **"Get streams for a channel with a negative or zero ID"** — Expect: No client-side guard; backend 404 surfaced via `_http_error`. — Result: ☐
4. **"Get streams for a channel with 50+ streams"** — Expect: No truncation cap; all streams listed; response length does not hit MCP output limits. — Result: ☐

---

#### `search_streams(query: str, provider_id: int | None = None, limit: int = 25)` — READ-ONLY
_Search for streams by name across all providers; thin wrapper over `list_streams`._
1. **"Search for streams matching 'ESPN' and show me the top 10."** — Expect: `search_streams(query="ESPN", limit=10)` called; response: "Found N streams matching 'ESPN' (showing 10):" with name, ID, group, and provider; streams 'US: ESPN FHD', 'US: ESPN', 'US: ESPN 2 FHD', 'US: ESPN 2', 'US: ESPN U', 'US: ESPN News' all appear. — Result: ☐
2. **"Search for a name that returns zero results, like 'ZZZNOTASTREAM'"** — Expect: Returns "No streams found matching 'ZZZNOTASTREAM'." without error. — Result: ☐
3. **"Search with `limit=200`"** — Expect: `page_size` clamped to 100; slice `streams[:200]` never truncates (at most 100 returned); "... and N more" math (`total - limit`) verified not to go negative. — Result: ☐

---

#### `get_streams_by_ids(stream_ids: list[int])` — READ-ONLY
_Fetch detailed stream information for a specific list of stream IDs via `POST /api/streams/by-ids`._
1. **"Give me details on the streams 'US: ESPN FHD', 'US: ESPN 2', and 'WY | Casper | NBC 13 KCWY'."** — Expect: `get_streams_by_ids(stream_ids=[<id1>, <id2>, <id3>])` called; response: "Found N of 3 requested streams:" with name, ID, group, and provider per stream. — Result: ☐
2. **"Request stream details where one name doesn't match anything, like 'US: ESPN FHD' and 'DOES NOT EXIST STREAM'"** — Expect: Backend returns only found streams; Claude reports "Found 1 of 2 requested streams"; count delta shows which are missing. — Result: ☐
3. **"Pass an empty list: `[]`"** — Expect: POST body is `{"stream_ids": []}`; if backend returns empty list: Claude says "No streams found for the given 0 IDs."; if backend errors: HTTP error surfaced. — Result: ☐
4. **"Pass a list with duplicate stream names resolving to the same ID"** — Expect: Backend deduplicates or not; "Found N of M" count is reported accurately. — Result: ☐

---

#### `probe_bulk_streams(stream_ids: list[int])` — WRITE
_Probe multiple specific streams at once and return a health results summary; 300 s timeout._
1. **"Probe these three streams for health: 'US: ESPN FHD', 'US: ESPN 2 FHD', and 'WY | Casper | NBC 13 KCWY'."** — Expect: `probe_bulk_streams(stream_ids=[<id1>, <id2>, <id3>])` called; response: "Bulk probe completed for 3 streams: Success: N, Failed: M" plus failed stream names/errors (up to 20 shown). — Result: ☐
2. **"Probe bulk streams with a list containing a stream name that doesn't exist"** — Expect: Backend probes what it can; non-existent ID appears in "failed" with appropriate error. — Result: ☐
3. **"Probe bulk streams with an empty list `[]`"** — Expect: Backend behavior observed; result is not a confusing raw `{}` in the output. — Result: ☐
4. **"Probe a large list of 500+ streams"** — Expect: 300 s timeout is sufficient; results list truncation (20 shown + "and N more") works correctly. — Result: ☐

---

#### `bulk_search_streams(queries: list[str], provider_id: int | None = None, limit_per_query: int = 10)` — READ-ONLY
_Search for multiple stream names in one call, running one search per query term._
1. **"Search for streams matching 'ESPN', 'ESPN 2', and 'Yacht Rock' all at once."** — Expect: `bulk_search_streams(queries=["ESPN", "ESPN 2", "Yacht Rock"])` called; concatenated results per term: `Results for "ESPN" (N found):` followed by stream name + ID lines; terms with no matches show `No results for "ESPN"`; ESPN queries surface the full set of ESPN streams. — Result: ☐
2. **"Search with an empty queries list `[]`"** — Expect: For-loop doesn't execute; result is empty string; behavior (blank response or MCP silence) observed and noted. — Result: ☐
3. **"Search with 50 query terms"** — Expect: 50 sequential backend calls complete without timeout; all results render. — Result: ☐
4. **"Search with a query that matches 200+ streams but `limit_per_query=5`"** — Expect: `page_size` clamped to 5; backend returns 5; header reports 5 (no "... and N more" shown — operator does not know more exist). — Result: ☐

---

#### `fuzzy_match_stream(name: str, provider_id: int | None = None, market: str = "east")` — READ-ONLY
_Search using multiple auto-generated name variants and score matches using the fuzzy matching engine._
1. **"Find the best stream match for 'ESPN2' using fuzzy matching."** — Expect: `fuzzy_match_stream(name="ESPN2")` called; variants generated via `_generate_variants("ESPN2")` (including "ESPN 2" from `_ABBREVIATIONS`); best match returned: `Best match for "ESPN2": US: ESPN 2 FHD (id=<id>)` plus up to 10 alternatives including 'US: ESPN 2'. — Result: ☐
2. **"Fuzzy match a stream name that has no possible matches: 'ZZZNOMATCH'"** — Expect: `_fuzzy_search` returns `(None, [])`; Claude shows "No match found" message with variant list attempted. — Result: ☐
3. **"Fuzzy match 'MC - Blues'"** — Expect: Prefix expansion generates "Music Choice Blues" as a variant; expansion fires and produces a scored match. — Result: ☐
4. **"Fuzzy match with `market='west'`"** — Expect: West-preference scoring (`+10 WEST`, `-5 EAST`) ranks WEST-suffixed streams higher. — Result: ☐

---

#### `match_streams_to_channels(group_id: int, provider_id: int | None = None, market: str = "east")` — WRITE
_Auto-match streams to all unassigned (0-stream) channels in a channel group using fuzzy name matching._
1. **"Auto-match streams to all unassigned channels in the 'USA | Sports' group."** — Expect: `match_streams_to_channels(group_id=<USA | Sports id>)` called; paginates through all channels, filters to 0-stream channels, fuzzy-matches each, POSTs `add-stream` for each hit; response: "Matched N of M unassigned channels in the 'USA | Sports' group:" with `#NUM ChannelName → StreamName (id=ID)` lines plus Unmatched section; 'US : ESPN' fuzzy-matches to 'US: ESPN' or 'US: ESPN FHD'. — Result: ☐
2. **"Run on a group where all channels already have streams — try 'Radio'"** — Expect: `if not unassigned:` fires; returns "All N channels in that group already have streams assigned." — clean. — Result: ☐
3. **"Run on a group name that doesn't exist"** — Expect: `channels_list` returns empty; `if not all_channels:` fires; returns "No channels found in that group." — clean. — Result: ☐
4. **"Run on a group with 200 unassigned channels"** — Expect: Up to 2000+ sequential backend calls complete; no timeout; partial-failure behavior when some `add-stream` calls fail mid-batch is isolated per-channel with graceful error reporting. — Result: ☐
## Profiles

---

#### `list_channel_profiles()` — READ-ONLY

_List all channel profiles with name, ID, and assigned channel count._

1. **"What channel profiles are configured in ECM?"** — Expect: Claude calls `list_channel_profiles()`; response lists each profile's name, ID, and channel count. If none: "No channel profiles configured." — Result: ☐
2. **"List channel profiles when none are configured"** — Expect: backend returns `[]` or `null`; response is "No channel profiles configured." — Result: ☐
3. **"List channel profiles when the backend is unreachable"** — Expect: response is "Error listing channel profiles: ..." with no unhandled exception. — Result: ☐

---

#### `list_stream_profiles()` — READ-ONLY

_List all stream transcoding profiles with name, ID, active status, and lock state._

1. **"Show me all stream transcoding profiles."** — Expect: Claude calls `list_stream_profiles()`; response lists each profile's name, ID, active/inactive status, and locked state. Expect profiles named "ffmpeg", "Proxy", "Redirect", "streamlink", and "VLC". If none: "No stream profiles configured." — Result: ☐
2. **"List stream profiles when none exist"** — Expect: response is "No stream profiles configured." — Result: ☐
3. **"List when a profile has no `is_active` field"** — Expect: missing key treated as falsy; profile labeled "inactive" even if the concept does not apply. — Result: ☐

---

#### `apply_profile_to_channels(profile_id: int, channel_ids: list[int])` — WRITE

_Bulk-assign a channel profile to multiple channels via a single PATCH._

1. **"Apply the 'LiveTV' channel profile to the channels named 'US : ESPN' and 'US : ESPN 2'."** — Expect: Claude resolves both names and calls `apply_profile_to_channels(profile_id=<resolved>, channel_ids=[<resolved>, <resolved>])`; response is "Profile 'LiveTV' applied to 2 channels. Profile now has N channels." — Result: ☐
2. **"Apply the 'HDHomerun' profile to 'Radio: ESPN Radio'"** — Expect: Claude resolves both names via list tools, calls the tool, and the channel count on 'HDHomerun' profile reflects the update. — Result: ☐
3. **"Apply a profile called 'Nonexistent Profile' to some channels"** — Expect: Claude resolves via `list_channel_profiles`, finds no match, and reports the profile could not be found before attempting the PATCH. — Result: ☐
4. **"Apply the 'Plex' profile to an empty channel list"** — Expect: PATCH body `{"channel_ids": []}` is sent; operator is warned this may be a bulk-clear operation. — Result: ☐
5. **"Apply the 'Music' profile to a channel that doesn't exist"** — Expect: backend either silently skips the bad ID or returns 400; verify which behavior occurs and that the tool surfaces it. — Result: ☐

---

## Normalization

---

#### `test_normalization(text: str)` — READ-ONLY

_Test how enabled normalization rules transform one or more stream names._

1. **"Test how ECM normalizes the stream name 'US : ESPN HD, US : ESPN 2 East'."** — Expect: Claude calls `test_normalization(text="US : ESPN HD, US : ESPN 2 East")`; response is "Normalization Results:" with `original → normalized` for each entry. — Result: ☐
2. **"Test normalization with just one name: 'Radio: ESPN Radio'"** — Expect: single-item list; response shows one arrow pair. — Result: ☐
3. **"Test normalization with a name that no rules match"** — Expect: response shows `original → original` (unchanged) clearly, not an empty result. — Result: ☐
4. **"Test with empty string input ''"** — Expect: POST body `{"texts": []}` is sent; backend behavior TBD; response is "No normalization results." or equivalent, not an error. — Result: ☐
5. **"Test with a name containing a comma inside the name: 'ESPN, the channel'"** — Expect: comma-split separates into "ESPN" and " the channel" as two queries; limitation is visible in output. — Result: ☐

---

#### `list_normalization_rules()` — READ-ONLY

_List all normalization rule groups with ID, enabled state, rule count, and up to 5 rule names._

1. **"What normalization rules are configured in ECM?"** — Expect: Claude calls `list_normalization_rules()`; response is "Normalization Rules (N groups):" with each group's name, ID, enabled/disabled status, rule count, and up to 5 rule names/types with "... and N more" if applicable. — Result: ☐
2. **"List normalization rules when none are configured"** — Expect: response is "No normalization rules configured." — Result: ☐
3. **"List when a group has more than 5 rules"** — Expect: only first 5 shown plus "and N more"; verify count arithmetic is correct (e.g., 8 rules → 5 shown + "and 3 more"). — Result: ☐
4. **"List when the backend returns `{\"groups\": null}`"** — Expect: `groups` resolves to `None`; `if not groups:` catches it; response is "No normalization rules configured." — Result: ☐

---

## M3U Accounts

#### `list_m3u_accounts()` — READ-ONLY

_List all configured M3U provider accounts with stream counts and status._

1. **"Show me all my M3U accounts and how many streams each one has."** — Expect: Claude calls `list_m3u_accounts()`; response lists each account with name, id, stream count, and status. Expect to see "Provider 1", "HD Homerun", and "custom" (in ERROR state). Empty ECM returns "No M3U accounts configured." — Result: ☐
2. **"List my M3U providers"** (no accounts configured) — Expect: response is "No M3U accounts configured.", not an error or empty dict. — Result: ☐
3. **"What M3U accounts do I have?"** when ECM backend is unreachable — Expect: response is "Error listing M3U accounts: <detail>", no unhandled exception. — Result: ☐

---

#### `refresh_m3u(account_id: int)` — WRITE

_Trigger an async refresh of a specific M3U account to fetch the latest stream list._

1. **"Refresh the 'Provider 1' M3U account."** — Expect: Claude resolves the name and calls `refresh_m3u(account_id=<resolved>)`; response is "M3U account 'Provider 1' refresh started. <backend message>"; Claude communicates the refresh is asynchronous. — Result: ☐
2. **"Refresh an M3U account called 'Nonexistent'"** — Expect: Claude resolves via `list_m3u_accounts`, finds no match, and reports it cannot find the account rather than calling with a bad ID. — Result: ☐
3. **"Refresh the 'HD Homerun' M3U account"** when a refresh is already running for that account — Expect: no lock prevents double-trigger; both run; response still says "refresh started"; operator is noted about concurrent refreshes. — Result: ☐
4. **"Inspect the 'custom' M3U account"** (currently in ERROR state), then attempt refresh — Expect: `get_m3u_account` returns full error detail; `refresh_m3u` surfaces any backend error clearly rather than reporting "refresh started" optimistically. — Result: ☐

---

#### `refresh_all_m3u()` — WRITE

_Trigger a bulk async refresh of all M3U accounts via a single backend call._

1. **"Refresh all my M3U feeds right now."** — Expect: Claude calls `refresh_all_m3u()`; response is "M3U refresh started for all accounts. <backend message>"; active accounts "Provider 1" and "HD Homerun" are refreshed; inactive/ERROR accounts like "custom" may be skipped (Dispatcharr-side). — Result: ☐
2. **"Refresh all M3U"** when no accounts exist — Expect: backend may return 204 (no body); `isinstance(result, dict)` guard keeps `msg` blank; output is "M3U refresh started for all accounts." (misleading but not an error); verify behavior. — Result: ☐
3. **"Refresh all M3U"** called twice in rapid succession — Expect: two concurrent refreshes on all accounts; tool has no idempotency guard; Dispatcharr may or may not deduplicate. — Result: ☐

---

#### `get_m3u_account(account_id: int)` — READ-ONLY

_Get full details for a specific M3U account including URL, status, stream count, and last refresh._

1. **"Show me the full details for the 'HD Homerun' M3U account."** — Expect: Claude resolves the name and calls `get_m3u_account(account_id=<resolved>)`; response includes name, id, type, URL (truncated at 60 chars), status, stream count, and last refresh timestamp. — Result: ☐
2. **"Get details for an M3U account called 'Nonexistent Provider'"** — Expect: Claude resolves via `list_m3u_accounts`, finds no match, and reports it cannot find the account. — Result: ☐
3. **"Get details for the 'custom' account"** (currently in ERROR state) — Expect: response includes full detail with status showing "ERROR" rather than "unknown", and last refresh timestamp. — Result: ☐

---

#### `create_m3u_account(name: str, url: str, server_type: str = "standard")` — WRITE

_Create a new M3U provider account (standard, Xtream Codes, or HD Homerun)._

1. **"Add a new M3U account named 'MCPTEST Provider' with the URL https://example-iptv.com/playlist.m3u8 — WARNING: this will actually create an account in ECM."** — Expect: Claude calls `create_m3u_account(name="MCPTEST Provider", url="https://example-iptv.com/playlist.m3u8", server_type="standard")`; response is "M3U account created: MCPTEST Provider (id=<N>)". — Result: ☐
2. **"Create an M3U account named 'MCPTEST Local' with url=http://192.168.1.100/list.m3u"** (private IP / non-https URL) — Expect: `validate_url_scheme` may reject it; tool surfaces the HTTP 500 detail; verify whether `http://` is accepted or only `https://`. — Result: ☐
3. **"Create an M3U account named 'MCPTEST Provider' again"** (duplicate name) — Expect: Dispatcharr returns 400 conflict; backend swallows to 500; tool returns "Error creating M3U account: POST /api/m3u/accounts -> HTTP 500". — Result: ☐

---

#### `update_m3u_account(account_id: int, name: str | None = None, url: str | None = None)` — WRITE

_Rename an M3U account or change its playlist URL._

1. **"Rename the 'Provider 1' M3U account to 'Primary Provider'."** — Expect: Claude resolves the name and calls `update_m3u_account(account_id=<resolved>, name="Primary Provider")`; response is "M3U account 'Provider 1' updated: name='Primary Provider', url='<current url>'". — Result: ☐
2. **"Update the 'HD Homerun' account but don't change anything"** (no name, no url provided) — Expect: tool returns "No changes specified." without making an HTTP call. — Result: ☐
3. **"Change the URL for an M3U account called 'Nonexistent Feed' to https://newprovider.com/list.m3u"** — Expect: Claude resolves via `list_m3u_accounts`, finds no match, and reports the account could not be found. — Result: ☐

---

#### `delete_m3u_account(account_id: int)` — DESTRUCTIVE

_Delete an M3U provider account and all its streams, including cascade deletion of orphaned channel groups._

1. **"Delete the 'MCPTEST Provider' M3U account."** — Expect: Claude resolves the name and calls `delete_m3u_account(account_id=<resolved>)`; response is "M3U account 'MCPTEST Provider' deleted." — Result: ☐
2. **"Delete an M3U account called 'Nonexistent Provider'"** — Expect: Claude resolves via `list_m3u_accounts`, finds no match, and reports the account could not be found rather than calling with a bad ID. — Result: ☐
3. **"Delete the 'custom' account"** (account in ERROR state) — Expect: tool returns "M3U account 'custom' deleted." and the ERROR-state cascade (groups, streams) completes without a server error. — Result: ☐

---

#### `update_m3u_group_settings(account_id: int, group_name: str, enabled: bool)` — WRITE

_Enable or disable a single stream group on an M3U account by group name._

1. **"Disable the 'USA | Sports' group on the 'Provider 1' M3U account."** — Expect: Claude resolves the account name and calls `update_m3u_group_settings(account_id=<resolved>, group_name="USA | Sports", enabled=False)`; response is "Group 'USA | Sports' disabled on M3U account 'Provider 1'." — Result: ☐
2. **"Enable the 'USA | Movies' group on the 'HD Homerun' account"** where that group name does not exist — Expect: backend receives `{"USA | Movies": True}` and either silently ignores or returns 400; verify whether the tool surfaces the error or reports success regardless of the HTTP response body. — Result: ☐
3. **"Disable the 'USA | Kids' group on an account called 'Nonexistent Provider'"** — Expect: Claude resolves via `list_m3u_accounts`, finds no match, and reports the account could not be found. — Result: ☐

---

#### `bulk_update_m3u_group_settings(account_id: int, groups: dict[str, bool])` — WRITE

_Enable or disable multiple stream groups on an M3U account in a single call._

1. **"On the 'Provider 1' M3U account, disable the 'USA | Sports' and 'USA | Kids' groups, and enable the 'Radio' group all at once."** — Expect: Claude resolves the account name and calls `bulk_update_m3u_group_settings(account_id=<resolved>, groups={"USA | Sports": False, "USA | Kids": False, "Radio": True})`; response lists each change: "Updated 3 groups on M3U account 'Provider 1':" with each group and its new state. — Result: ☐
2. **"Update groups with an empty list on the 'HD Homerun' account"** (`groups={}`) — Expect: tool sends empty-body PATCH; response is "Updated 0 groups on M3U account 'HD Homerun':" (trailing newline with no entries — minor formatting issue). — Result: ☐
3. **"Bulk update 50 groups on the 'Provider 1' account"** — Expect: all 50 group-name keys sent in one PATCH body; if any names don't exist, Dispatcharr may silently ignore or reject; tool does not report which keys were rejected. — Result: ☐

---

## EPG Sources

#### `list_epg_sources()` — READ-ONLY

_List all configured EPG data sources with channel counts and truncated URLs._

1. **"What EPG sources do I have configured?"** — Expect: Claude calls `list_epg_sources()`; response lists each source with id, name, channel count, and truncated URL. Expect to see "Teamarr", "Jesmann Gracenote", "Jesmann Full", "B1G EPG", "NCAA Football EPG", and "PPV EPG". Empty returns "No EPG sources configured." — Result: ☐
2. **"List EPG sources"** when backend returns a plain list vs. an envelope dict — Expect: code handles both via `isinstance(sources, dict)` unwrapping; verify the actual Dispatcharr response shape uses `"sources"` or `"results"` as the key. — Result: ☐
3. **"Show my EPG feeds"** when no sources exist — Expect: response is "No EPG sources configured." — Result: ☐

---

#### `refresh_epg(source_id: int)` — WRITE

_Trigger an async refresh of a single EPG source to fetch the latest program guide data._

1. **"Refresh the 'Teamarr' EPG source."** — Expect: Claude resolves the name and calls `refresh_epg(source_id=<resolved>)`; response is "EPG source 'Teamarr' refresh started. <backend message>"; tool returns before completion. — Result: ☐
2. **"Refresh an EPG source called 'Nonexistent Guide'"** — Expect: Claude resolves via `list_epg_sources`, finds no match, and reports it cannot find the source rather than calling with a bad ID. — Result: ☐
3. **"Refresh the 'Jesmann Gracenote' source"** twice rapidly — Expect: two concurrent background pollers for the same source; no guard in MCP or backend; second trigger may succeed or fail depending on Dispatcharr's concurrency handling. — Result: ☐

---

#### `refresh_all_epg(source_ids: list[int] | None = None)` — WRITE

_Refresh multiple EPG sources sequentially; refreshes all sources if no IDs are given._

1. **"Refresh all my EPG sources."** — Expect: Claude calls `refresh_all_epg()`; response is "Refreshed N/M EPG sources." plus an errors block if any failed. — Result: ☐
2. **"Refresh just the 'Teamarr' and 'Jesmann Full' EPG sources."** — Expect: Claude resolves both names and calls `refresh_all_epg(source_ids=[<resolved>, <resolved>])`; response shows 2/2 refreshed. — Result: ☐
3. **"Refresh 'Jesmann Gracenote' and a source called 'Nonexistent Guide'"** — Expect: 'Jesmann Gracenote' refreshes; Claude reports 'Nonexistent Guide' could not be resolved and is skipped; tool reports partial result. — Result: ☐
4. **"Refresh all EPG"** when the source list fetch itself fails — Expect: outer `except` catches and returns "Error refreshing EPG sources: ..." with no partial information. — Result: ☐

---

#### `match_channels_epg()` — WRITE

_Auto-match channels to EPG data based on channel names with confidence scoring._

1. **"Run EPG auto-matching to match my channels to guide data."** — Expect: Claude calls `match_channels_epg()`; response is "EPG auto-match complete: N channels matched, M unmatched." with real counts (not always 0). — Result: ☐
2. **"Match channels to EPG"** when no EPG sources are configured — Expect: backend returns empty match categories; tool still reports a valid "N channels matched, M unmatched." summary. — Result: ☐
3. **"Match channels to EPG using the 'Teamarr' source data"** — Expect: run `list_epg_sources` first to confirm Teamarr is loaded, then run `match_channels_epg()`; verify the tool surfaces actual match counts, not always "0 matched, 0 unmatched." — Result: ☐
4. **"Match channels to EPG"** with 2000+ channels — Expect: no timeout/504 error from the match endpoint; if a 504 occurs, the tool surfaces "Error getting EPG grid: ... HTTP 504". — Result: ☐

---

#### `create_epg_source(name: str, url: str)` — WRITE

_Create a new XMLTV EPG data source._

1. **"Add a new EPG source named 'MCPTEST Guide' with URL https://xmltv.example.com/us-guide.xml.gz — WARNING: this will actually create an EPG source."** — Expect: Claude calls `create_epg_source(name="MCPTEST Guide", url="https://xmltv.example.com/us-guide.xml.gz")`; response is "EPG source created: MCPTEST Guide (id=<N>)". — Result: ☐
2. **"Create EPG source named 'MCPTEST Bad URL' with URL file:///etc/passwd"** — Expect: `validate_url_scheme` rejects non-http/https; backend returns 400/500; tool surfaces the error. — Result: ☐
3. **"Create EPG source named 'MCPTEST Guide' again"** (duplicate name) — Expect: Dispatcharr may return 400; backend swallows to 500; tool returns an error. — Result: ☐

---

#### `update_epg_source(source_id: int, name: str | None = None, url: str | None = None)` — WRITE

_Rename an EPG source or change its XMLTV feed URL._

1. **"Change the URL for the 'Jesmann Gracenote' EPG source to https://newguide.example.com/guide.xml."** — Expect: Claude resolves the name and calls `update_epg_source(source_id=<resolved>, url="https://newguide.example.com/guide.xml")`; response is "EPG source 'Jesmann Gracenote' updated: name='Jesmann Gracenote', url='https://newguide.example.com/guide.xml'". — Result: ☐
2. **"Update the 'Teamarr' EPG source with no changes"** — Expect: tool returns "No changes specified." without making an HTTP call. — Result: ☐
3. **"Rename an EPG source called 'Nonexistent Guide' to 'New Name'"** — Expect: Claude resolves via `list_epg_sources`, finds no match, and reports the source could not be found. — Result: ☐

---

#### `delete_epg_source(source_id: int)` — DESTRUCTIVE

_Delete an EPG data source; channels matched to it lose their EPG association._

1. **"Delete the 'MCPTEST Guide' EPG source."** — Expect: Claude resolves the name and calls `delete_epg_source(source_id=<resolved>)`; response is "EPG source 'MCPTEST Guide' deleted." — Result: ☐
2. **"Delete an EPG source called 'Nonexistent Guide'"** — Expect: Claude resolves via `list_epg_sources`, finds no match, and reports it cannot find the source. — Result: ☐
3. **"Delete the 'MCPTEST Guide' EPG source"** twice — Expect: second call: Claude resolves via `list_epg_sources`, finds it no longer exists, and reports it cannot find the source (or backend returns 500 on the delete call). — Result: ☐

---

#### `get_epg_grid(channel_id: int | None = None, limit: int = 20)` — READ-ONLY

_Get the EPG schedule grid showing current and upcoming programs, with optional channel and limit filters._

1. **"What's on TV right now according to the EPG?"** — Expect: Claude calls `get_epg_grid()`; response is "EPG Schedule (N programs): [ChannelName] Title (start - end)" for up to 20 programs. — Result: ☐
2. **"Show me the EPG schedule for the channel named 'US : ESPN', up to 10 programs."** — Expect: Claude resolves the channel name and calls `get_epg_grid(channel_id=<resolved>, limit=10)`; response shows up to 10 programs for that channel only. — Result: ☐
3. **"Show EPG grid for a channel called 'No Guide Channel'"** (channel with no programs or non-existent) — Expect: backend returns full grid, tool filters for the resolved ID, finds nothing, and returns "No EPG schedule data available." — Result: ☐
4. **"Get EPG grid"** with a large EPG dataset (2000+ channels) — Expect: tool surfaces "Error getting EPG grid: GET /api/epg/grid -> HTTP 504 ..." rather than hanging. — Result: ☐

---

#### `list_dummy_epg_profiles()` — READ-ONLY

_List all dummy EPG profiles used to generate placeholder guide data for channels without real EPG._

1. **"Show me the dummy EPG profiles I have set up."** — Expect: Claude calls `list_dummy_epg_profiles()`; response lists each profile's name, id, enabled/disabled state, and group count. Expect to see "B1G Advanced EPG" in the list. Empty returns "No dummy EPG profiles configured." — Result: ☐
2. **"List dummy EPG profiles"** when none are configured — Expect: response is "No dummy EPG profiles configured." — Result: ☐
3. **"Show dummy profiles"** when backend returns `{"profiles": null}` — Expect: `profiles.get("profiles", ...)` returns `None`; `if not profiles:` catches it; response is "No dummy EPG profiles configured." — Result: ☐

---

#### `generate_dummy_epg()` — WRITE

_Force regeneration of all dummy EPG XMLTV data from enabled profiles._

1. **"Regenerate the dummy EPG data now."** — Expect: Claude calls `generate_dummy_epg()`; response is "Dummy EPG regenerated for N enabled profiles." where N comes from `result.get("profiles_generated", 0)`. If "B1G Advanced EPG" is enabled it should be included in the count. — Result: ☐
2. **"Generate dummy EPG"** when no profiles are enabled — Expect: backend returns `{"profiles_generated": 0}`; tool reports "Dummy EPG regenerated for 0 enabled profiles." (technically correct; verify it does not mislead). — Result: ☐
3. **"Generate dummy EPG"** when backend returns a list instead of a dict — Expect: `isinstance(result, dict)` guard defaults `count` to 0; tool returns "Dummy EPG regenerated for 0 enabled profiles." without raising an exception. — Result: ☐

---

## Channel Groups

#### `list_channel_groups()` — READ-ONLY

_List all visible channel groups with channel counts (hidden groups excluded by the backend)._

1. **"List all my channel groups."** — Expect: Claude calls `list_channel_groups()`; response is "Found N channel groups: <name> (id=<N>) — <count> channels". Expect groups like "USA | Sports", "USA | Movies", "USA | Kids", "Radio", "USA | Local PBS", and "NFL Game Pass" to appear. — Result: ☐
2. **"List channel groups"** when none exist — Expect: response is "No channel groups found." — Result: ☐
3. **"Show my groups"** on an ECM instance with many hidden groups — Expect: tool returns the filtered list (hidden excluded); count displayed is visible count only. — Result: ☐

---

#### `create_channel_group(name: str)` — WRITE

_Create a new channel group; returns the existing group if the name already exists (idempotent by design)._

1. **"Create a new channel group called 'UK Sports'."** — Expect: Claude calls `create_channel_group(name="UK Sports")`; response is "Channel group ready: UK Sports (id=<N>)". — Result: ☐
2. **"Create a group called 'UK Sports'"** when it already exists — Expect: backend returns 400, catches the error, looks up the existing group, and returns "Channel group ready: UK Sports (id=<existing-id>)". — Result: ☐
3. **"Create a channel group with name ''"** (empty string) — Expect: backend Pydantic validation returns 422; backend swallows to 500; tool returns an error. — Result: ☐

---

#### `get_orphaned_groups()` — READ-ONLY

_List channel groups with no streams, no channels, and no M3U association._

1. **"Show me any orphaned channel groups that I can clean up."** — Expect: Claude calls `get_orphaned_groups()`; response is "Found N orphaned groups: <name> (id=<N>)". Expect names like "24/7 COMEDY VIP", "24/7 DRAMA VIP", and "FOR ADULTS" to appear (746 orphaned in this system). — Result: ☐
2. **"Find orphaned groups"** when none exist — Expect: response is "No orphaned channel groups found." — Result: ☐
3. **"Find orphaned groups"** with thousands of streams — Expect: response eventually returns (backend paginates in 500-item pages); no timeout or unhandled exception even if response is delayed. — Result: ☐

---

#### `delete_channel_group(group_id: int, delete_channels: bool = False)` — DESTRUCTIVE

_Delete a channel group; groups with M3U sync may be hidden instead of deleted._

1. **"Delete the 'USA | Local PBS' channel group."** — Expect: Claude resolves the name and calls `delete_channel_group(group_id=<resolved>)`; response is "Channel group 'USA | Local PBS' deleted." — Result: ☐
2. **"Delete the 'USA | Sports' group"** where it has active M3U sync — Expect: backend hides the group (returns `{"status": "hidden"}`); tool still reports "Channel group 'USA | Sports' deleted."; run `get_hidden_groups()` afterward to verify actual state. — Result: ☐
3. **"Delete the 'Radio' group with delete_channels=True"** where some channels fail to delete — Expect: best-effort partial deletion continues; failed channels are not reported to MCP; group delete is attempted; if channels remain the tool shows "Error deleting channel group 'Radio': ...". — Result: ☐
4. **"Delete a group called 'Nonexistent Group'"** — Expect: Claude resolves via `list_channel_groups`, finds no match, and reports the group could not be found. — Result: ☐

---

#### `get_hidden_groups()` — READ-ONLY

_List channel groups hidden from the UI via ECM's local SQLite hidden state._

1. **"Show me any channel groups that are hidden."** — Expect: Claude calls `get_hidden_groups()`; response is "Found N hidden groups: <name> (id=<N>)". Data comes from ECM's local `HiddenChannelGroup` table, not Dispatcharr. — Result: ☐
2. **"Show hidden groups"** when none are hidden — Expect: response is "No hidden channel groups." — Result: ☐
3. **"Show hidden groups"** after a `delete_channel_group` call on a synced group that silently hid it — Expect: the group appears in the hidden list, confirming it was hidden not deleted. — Result: ☐

---

#### `get_auto_created_groups()` — READ-ONLY

_List channel groups containing channels created by the auto-creation pipeline._

1. **"Which channel groups have auto-created channels in them?"** — Expect: Claude calls `get_auto_created_groups()`; response is "Found N auto-created groups: <name> (id=<N>) — <count> channels". Groups like "USA | Sports", "USA | Movies", or "NFL Game Pass" may appear if auto-creation rules have targeted them. — Result: ☐
2. **"Show auto-created groups"** when no auto-creation rules have run — Expect: response is "No auto-created channel groups." or backend returns `{"groups": [], "total_auto_created_channels": 0}`. — Result: ☐
3. **"Show auto-created groups"** when backend returns `{"groups": [...], "total_auto_created_channels": N}` — Expect: tool correctly unwraps the envelope and iterates over the group objects, not the dict keys. — Result: ☐

---

#### `delete_orphaned_groups(group_ids: list[int] | None = None)` — DESTRUCTIVE

_Delete orphaned channel groups; optionally targets specific groups by ID, otherwise deletes all orphaned._

1. **"Clean up all orphaned channel groups."** — Expect: Claude calls `delete_orphaned_groups()`; response lists each deleted group: "Deleted N orphaned group(s): <name> (id=<N>)". — Result: ☐
2. **"Delete the orphaned groups named '24/7 COMEDY VIP' and '24/7 DRAMA VIP' only."** — Expect: Claude resolves both names, confirms they appear in `get_orphaned_groups()`, and calls `delete_orphaned_groups(group_ids=[<resolved>, <resolved>])`; only those two groups are deleted, remaining 744 orphaned groups are untouched. — Result: ☐
3. **"Delete all orphaned groups"** when none exist — Expect: backend returns `{"deleted_groups": [], "failed_groups": []}`; tool returns "No orphaned groups were deleted." — Result: ☐
4. **"Delete all orphaned groups"** with groups having partial failures — Expect: backend returns `{"deleted_groups": [...], "failed_groups": [...]}`; tool surfaces partial results including any failed deletions. — Result: ☐

---

#### `get_groups_with_streams()` — READ-ONLY

_List channel groups that have at least one channel containing at least one stream._

1. **"Which channel groups have streams attached to them?"** — Expect: Claude calls `get_groups_with_streams()`; response is "Found N groups with stream info: <name> (id=<N>) — <count> streams". Expect groups like "USA | Sports", "USA | Movies", "Radio", and "NFL Game Pass" to appear. — Result: ☐
2. **"Show groups with streams"** when no channels have streams assigned — Expect: response is "No channel groups found." — Result: ☐
3. **"Show groups with streams"** with thousands of channels — Expect: response returns without error even if delayed (backend paginates with a 50-page safety limit); no timeout. — Result: ☐
## Auto-Creation Rules

---

#### `list_auto_creation_rules()` — READ-ONLY
_List all auto-creation rules with id, name, enabled state, and priority._
1. **"Show me all my auto-creation rules."** — Expect: Returns numbered list formatted as `[priority] name (id=N) — enabled/disabled`; backend `{"rules": [...]}` unwrapped; priority, name, id, enabled shown only (not conditions/actions). — Result: ☐
2. **"List my auto-creation rules" when zero rules are configured** — Expect: Returns "No auto-creation rules configured." (not an empty list, not an error). — Result: ☐
3. **Simulate backend returning bare list instead of `{"rules": [...]}` wrapper** — Expect: Defensive unwrap fires (`resp.get("rules", []) if isinstance(resp, dict) else (resp or [])`); no AttributeError; graceful handling confirmed. — Result: ☐

---

#### `run_auto_creation(dry_run: bool = True)` — READ-ONLY (dry_run=True) | WRITE (dry_run=False) — HIGH-IMPACT when live
_Run the auto-creation pipeline; dry_run=True previews without changes, dry_run=False creates channels._
1. **"Preview what the auto-creation pipeline would create — don't make any changes yet."** — Expect: Claude calls `run_auto_creation(dry_run=True)`; returns "Auto-creation Dry run complete:" with streams evaluated/matched, "Channels would be created: N", groups created, skipped count, duration, and sample up to 20 entity names; no channels actually created. — Result: ☐
2. **"Run auto-creation for real and actually create the channels."** — Expect: Claude calls `run_auto_creation(dry_run=False)`; returns "Auto-creation Execution complete:" with "Channels created: N" (no "would be"); channels physically created in ECM; timeout is 300 seconds. — Result: ☐
3. **Ask "run auto-creation" without specifying dry_run** — Expect: Claude defaults to `dry_run=True` (safe default); Claude does NOT pass `dry_run=False` without explicit instruction. — Result: ☐
4. **"Run auto-creation dry run" with no streams in ECM** — Expect: Returns 0 streams evaluated, not an error. — Result: ☐
5. **Simulate pipeline timeout (>300s)** — Expect: Surfaces a `TimeoutError`, not a silent hang. — Result: ☐
6. **"Run live auto-creation" when a rule's condition contains an invalid regex** — Expect: Pipeline still completes (returns errors or skips affected rule); does not 500. — Result: ☐

---

#### `get_auto_creation_rule(rule_id: int)` — READ-ONLY
_Get detailed conditions and actions for a single auto-creation rule._
1. **"Show me the full details of the 'Testing Rule' auto-creation rule."** — Expect: Claude resolves name to id via `list_auto_creation_rules`, then calls `get_auto_creation_rule(rule_id=N)`; returns name, id, enabled state, priority, description, run_on_refresh, stop_on_first_match, skip_struck_streams, orphan_action, sort settings, normalization group ids, up to 10 conditions, up to 10 actions; conditions/actions beyond 10 show "... and N more". — Result: ☐
2. **"Show me the details of the 'Phantom Rule' auto-creation rule" (non-existent name)** — Expect: Claude attempts lookup; backend returns 404; tool returns `Error getting rule: GET /api/auto-creation/rules/N -> HTTP 404 Not Found: ...`. — Result: ☐
3. **Rule with no conditions or actions** — Expect: Fields omitted cleanly; no KeyError. — Result: ☐
4. **Rule with exactly 10 conditions, then 11 conditions** — Expect: Truncation `... and 1 more` appears at 11. — Result: ☐

---

#### `toggle_auto_creation_rule(rule_id: int)` — WRITE
_Flip the enabled/disabled state of one auto-creation rule._
1. **"Disable the 'Testing Rule' auto-creation rule."** — Expect: Claude resolves name via `list_auto_creation_rules`, calls `toggle_auto_creation_rule(rule_id=N)`; returns "Rule N is now disabled." — Result: ☐
2. **"Now re-enable it."** — Expect: Second call returns "Rule N is now enabled."; toggle is idempotent — calling twice returns to original state. — Result: ☐
3. **"Toggle the 'Phantom Rule' auto-creation rule" (non-existent name)** — Expect: Claude attempts lookup; backend 404; tool returns `Error toggling rule N: ...`. — Result: ☐
4. **Toggle a rule twice in sequence** — Expect: State returns to original; enabled/disabled string in the response correctly reflects the final backend state (not the previous state). — Result: ☐

---

#### `bulk_toggle_auto_creation_rules(rule_ids: list[int])` — WRITE
_Toggle multiple auto-creation rules at once, reporting per-rule success and failures._
1. **"Toggle the 'Testing Rule', 'Create B1G Channels', and 'USA Entertainment' auto-creation rules all at once."** — Expect: Claude resolves each name via `list_auto_creation_rules`, calls `bulk_toggle_auto_creation_rules(rule_ids=[N, M, P])`; returns "Toggled 3/3 rules:" with one "Rule N: enabled/disabled" line per id. — Result: ☐
2. **Mix valid names and one non-existent name (e.g., "toggle 'Testing Rule', 'Phantom Rule', and 'Create B1G Channels'")** — Expect: Claude resolves what it can; valid rules toggled successfully; error reported for unresolved one; final output is "Toggled 2/3 rules:" with Errors section visible. — Result: ☐
3. **Empty name list** — Expect: Returns "Toggled 0/0 rules:" (no crash on empty loop). — Result: ☐
4. **Single rule named** — Expect: Equivalent result to `toggle_auto_creation_rule`; both paths give the same outcome. — Result: ☐

---

#### `duplicate_auto_creation_rule(rule_id: int)` — WRITE
_Create a copy of an existing auto-creation rule with a new id._
1. **"Duplicate the 'Testing Rule' so I can modify the copy."** — Expect: Claude resolves name via `list_auto_creation_rules`, calls `duplicate_auto_creation_rule(rule_id=N)`; returns "Rule N duplicated. New rule ID: M". — Result: ☐
2. **"Duplicate the 'Phantom Rule'" (non-existent name)** — Expect: Claude attempts lookup; backend 404; tool returns `Error duplicating rule N: ...`. — Result: ☐
3. **Duplicate a rule, then immediately list rules** — Expect: New rule appears in `list_auto_creation_rules` with a higher id and a name like "Copy of Testing Rule" (backend-determined naming). — Result: ☐

---

#### `delete_auto_creation_rule(rule_id: int)` — DESTRUCTIVE
_Permanently delete an auto-creation rule; irreversible._
1. **"Delete the 'Testing Rule' auto-creation rule."** — Expect: Claude resolves name via `list_auto_creation_rules`, calls `delete_auto_creation_rule(rule_id=N)`; returns "Rule N deleted."; rule no longer appears in `list_auto_creation_rules`. — Result: ☐
2. **"Delete the 'Phantom Rule'" (non-existent name)** — Expect: Claude attempts lookup; backend 404; tool returns `Error deleting rule N: ...`. — Result: ☐
3. **Delete a rule that is referenced by existing executions** — Expect: Backend may return 409 or succeed; error is surfaced to the operator, not swallowed. — Result: ☐
4. **Delete a rule, then call `get_auto_creation_rule` by the same name** — Expect: Returns a clean error, not a crash. — Result: ☐

---

#### `create_auto_creation_rule(name: str, conditions: list[dict], actions: list[dict], ...)` — WRITE
_Create a new auto-creation rule with conditions, actions, and optional configuration._
1. **"Create an auto-creation rule called 'USA Sports HD' that matches streams whose group name contains 'USA \| Sports' and whose name contains 'HD', creates a group called 'Sports HD', and creates a channel named after the stream. Set priority to 10 and enable it."** — Expect: Claude calls `create_auto_creation_rule` with `name="USA Sports HD"`, conditions for `stream_group_contains` and `stream_name_contains`, actions for `create_group` and `create_channel`, `priority=10`, `enabled=True`; returns "Created auto-creation rule 'USA Sports HD' (id=N)." — Result: ☐
2. **Omit `conditions` or `actions`** — Expect: Backend validation error (400/422); tool surfaces it: `Error creating rule: POST /api/auto-creation/rules -> HTTP 422 ...`. — Result: ☐
3. **Pass an invalid condition type (e.g., `{"type": "invalid_type", "value": "x"}`)** — Expect: Backend may accept or reject; behavior documented. — Result: ☐
4. **Pass `orphan_action="explode"` (invalid value)** — Expect: Backend validation rejects with 422. — Result: ☐
5. **Create rule with `quality_tie_break_order` or `match_scope_target_group` fields (declared in `_AC_RULE_CREATE_FIELDS` but NOT in the tool's parameter list)** — Expect: These fields cannot be set via the MCP tool; this is a confirmed feature gap — document as a finding. — Result: ☐
6. **Create rule with `normalization_group_ids=[999]` (non-existent group)** — Expect: Backend response documented (verify whether it accepts or rejects). — Result: ☐

---

#### `update_auto_creation_rule(rule_id: int, ...)` — WRITE
_Partially update an existing rule; only supplied fields are changed._
1. **"Update the 'Create B1G Channels' rule: change its priority to 5 and enable it."** — Expect: Claude resolves name via `list_auto_creation_rules`, calls `update_auto_creation_rule(rule_id=N, priority=5, enabled=True)`; payload is `{"priority": 5, "enabled": True}` (only supplied fields); returns "Updated rule 'Create B1G Channels' (id=N). Changed: priority, enabled". — Result: ☐
2. **"Update the 'Create B1G Channels' rule with no changes" (pass no optional fields)** — Expect: Tool returns "No fields to update." without making any backend call; early-exit path confirmed. — Result: ☐
3. **"Update the 'Phantom Rule'" (non-existent name)** — Expect: Claude attempts lookup; backend 404; tool returns `Error updating rule N: ...`. — Result: ☐
4. **Update `conditions` with a full replacement list** — Expect: Entire conditions list is replaced (not merged); operator behavior documented clearly. — Result: ☐
5. **`enabled=False`** — Expect: `False` is included in the payload (not silently dropped by the `if value is not None:` loop); backend receives `{"enabled": false}`. — Result: ☐
6. **`normalization_group_ids=[]` (empty list)** — Expect: Empty list passes `if value is not None:` check and is sent; backend accepts it to clear normalization groups. — Result: ☐

---

#### `list_auto_creation_executions(limit: int = 10)` — READ-ONLY
_List recent auto-creation pipeline execution records with status, channel count, and timestamp._
1. **"Show me the last 5 auto-creation pipeline runs."** — Expect: Claude calls `list_auto_creation_executions(limit=5)`; returns "Recent executions (N):" with one line per execution: `#id: status — N channels (dry run) (timestamp)`; dry-run executions labeled "(dry run)". — Result: ☐
2. **No executions yet** — Expect: Returns "No auto-creation executions found." — Result: ☐
3. **`limit=1`** — Expect: Returns only the most recent execution. — Result: ☐
4. **`limit=50`** — Expect: Returns up to 50 entries; limit is passed as a query parameter correctly. — Result: ☐

---

#### `rollback_auto_creation(execution_id: int)` — DESTRUCTIVE
_Roll back a live execution by deleting all channels it created and restoring modified entities._
1. **"Roll back execution #7 from yesterday's run."** — Expect: Claude calls `rollback_auto_creation(execution_id=7)`; returns "Execution 7 rolled back. N channels deleted."; backend deletes created channels, restores modified entities, sets execution status to "rolled_back"; timeout is 300 seconds. — Result: ☐
2. **"Roll back an execution that doesn't exist"** — Expect: Backend returns 400 "Execution not found"; tool returns `Error rolling back execution N: POST /api/auto-creation/executions/N/rollback -> HTTP 400 Not Found: Execution not found`. — Result: ☐
3. **"Roll back a dry-run execution"** — Expect: Backend returns `{"success": False, "error": "Cannot rollback a dry-run execution"}`; tool surfaces this error clearly, does NOT silently say "0 channels deleted". — Result: ☐
4. **"Roll back an execution that's already been rolled back"** — Expect: Backend returns 400 "Execution already rolled back"; tool surfaces this, does not appear to succeed. — Result: ☐
5. **"Roll back an execution that is currently running"** — Expect: Behavior documented (undefined — test and record). — Result: ☐

---

#### `analyze_auto_creation_rules(bundle_path: str | None = None)` — READ-ONLY
_Lint live auto-creation rules or rules from a debug bundle and return a markdown findings report._
1. **"Analyze my auto-creation rules for any problems."** — Expect: Claude calls `analyze_auto_creation_rules()` with no args; POSTs to `/api/auto-creation/rules/analyze`; returns formatted markdown report with summary line ("N errors, N warnings, N info") and a table per rule; if clean, returns "No findings across N rule(s) — looks clean." — Result: ☐
2. **"Analyze the auto-creation rules in my debug bundle at /tmp/ecm-debug.tar.gz."** — Expect: Claude calls `analyze_auto_creation_rules(bundle_path="/tmp/ecm-debug.tar.gz")`; reads file, uploads via multipart POST to `/api/auto-creation/rules/analyze/from-bundle`; returns same report format labeled with the bundle filename. — Result: ☐
3. **Bundle path that doesn't exist** — Expect: Returns "Bundle file not found: /path/to/file" without attempting the upload. — Result: ☐
4. **Bundle path to a file that is not a valid tar.gz** — Expect: Backend returns an error; surfaces cleanly. — Result: ☐
5. **Live analysis with zero rules** — Expect: "No findings across 0 rule(s) — looks clean." — Result: ☐
6. **Rule with a regex like `.*` as a `stream_name_matches` condition** — Expect: Analyzer flags it as `REGEX_TRIVIALLY_MATCHES_ALL`; finding appears in the table. — Result: ☐

---

#### `get_auto_creation_debug_bundle()` — READ-ONLY
_Return static instructions about the debug bundle endpoints, UI path, and contents._
1. **"How do I get the auto-creation debug bundle?"** — Expect: Claude calls `get_auto_creation_debug_bundle()`; returns hardcoded string describing the two HTTP endpoints (`POST /api/auto-creation/debug-bundle` → 202 + job_id, then `GET /api/auto-creation/debug-bundle/{job_id}`), the UI path, list of bundle file contents (channels.json, rules.yaml, normalization_rules.yaml, channels.csv, settings.json, task_schedules.json, channel_groups_diagnostic.json, logs.txt, manifest.json), and that credentials are redacted in settings.json; no backend call is made. — Result: ☐
2. **Verify output mentions credentials are redacted in settings.json** — Expect: The static string explicitly states credentials are redacted. — Result: ☐

---

## Tasks & Schedules

---

#### `list_tasks()` — READ-ONLY
_List all registered scheduled tasks with id, enabled state, status, and last run time._
1. **"Show me all scheduled tasks."** — Expect: Claude calls `list_tasks()`; returns "Found N tasks:" with one line per task: `name (id=task_id) — enabled/disabled, status: idle/running, last run: timestamp/never`. — Result: ☐
2. **No tasks configured** — Expect: Returns "No tasks configured." — Result: ☐
3. **A task that is currently running** — Expect: Status shows "running", not "idle". — Result: ☐
4. **Backend returns `task_id` vs `id` inconsistency** — Expect: Tool reads `t.get("task_id", t.get("id", "?"))`; both field names handled without error. — Result: ☐

---

#### `run_task(task_id: str)` — WRITE
_Immediately trigger a scheduled task to run outside its schedule._
1. **"Run the M3U Refresh task right now."** — Expect: Claude calls `run_task(task_id="m3u_refresh")`; returns "Task 'm3u_refresh' started. Status: running. <message>"; task starts asynchronously. — Result: ☐
2. **"Run the 'nonexistent_task'"** — Expect: Backend 404; tool returns `Error running task 'nonexistent_task': POST /api/tasks/nonexistent_task/run -> HTTP 404 ...`. — Result: ☐
3. **Run a task that is already running** — Expect: Backend may return an error or start a second instance; behavior documented. — Result: ☐
4. **Backend returns a plain (non-dict) response** — Expect: Tool falls back to "Task 'X' started." without crashing. — Result: ☐

---

#### `cancel_task(task_id: str)` — DESTRUCTIVE
_Cancel a currently running task; safe no-op if the task is not running._
1. **"Cancel the Stream Probe task."** — Expect: Claude calls `cancel_task(task_id="stream_probe")`; returns "Task 'stream_probe' cancelled. <message>" on success; backend sets status to "cancelled" and records a journal entry. — Result: ☐
2. **Cancel the Stream Probe task when it is not running** — Expect: Backend returns `{"status": "not_running", "message": "Task stream_probe is not running"}` as HTTP 200; MCP tool returns "Task 'stream_probe' cancelled. Task stream_probe is not running" — note this messaging is misleading (says "cancelled" but task wasn't running); verify and flag. — Result: ☐
3. **Cancel a task with a non-existent id** — Expect: Backend raises 404; tool returns `Error cancelling task 'bad_id': POST /api/tasks/bad_id/cancel -> HTTP 404 Not Found: ...`. — Result: ☐
4. **Cancel a task that just finished (race condition)** — Expect: Same as "not running" case above. — Result: ☐

---

#### `get_task_history(task_id: str | None = None, limit: int = 10)` — READ-ONLY
_Return execution history for a specific task or all tasks combined._
1. **"Show me the last 5 runs of the M3U Refresh task."** — Expect: Claude calls `get_task_history(task_id="m3u_refresh", limit=5)`; returns "Task history (N entries):" with lines: `task_name: status (Xs) — timestamp`. — Result: ☐
2. **"Show me the last 20 task runs across all tasks."** — Expect: Claude calls `get_task_history(limit=20)` (no task_id); uses the `tasks_history_all` endpoint at `/api/tasks/history/all`. — Result: ☐
3. **"Show me the history for the M3U Refresh task" when it has never run** — Expect: Returns "No task history for task 'm3u_refresh'." — Result: ☐
4. **`limit=100`** — Expect: Declared `limit` query param is sent; `offset` is not exposed in the tool so pagination is not possible. — Result: ☐
5. **`get_task_history()` with no args (all tasks, default limit=10)** — Expect: Uses `tasks_history_all` endpoint, not the per-task endpoint with `task_id=None`. — Result: ☐

---

#### `list_task_schedules(task_id: str)` — READ-ONLY
_List all configured schedules for a specific task with type, description, enabled state, and next run._
1. **"Show me the schedules for the EPG Refresh task."** — Expect: Claude calls `list_task_schedules(task_id="epg_refresh")`; returns "Schedules for 'epg_refresh' (N):" with lines `#id: schedule_type — description (enabled/disabled), next: timestamp`. — Result: ☐
2. **No schedules configured for the task** — Expect: Returns "No schedules configured for task 'epg_refresh'." — Result: ☐
3. **Unknown task id** — Expect: Backend may return 404 or empty list; tool handles both without crashing. — Result: ☐
4. **Task with multiple schedules of different types (interval, daily, weekly)** — Expect: All appear in the list. — Result: ☐
5. **`next_run_at` field missing from a schedule** — Expect: Fallback to `next_run`; if both absent, shows "?". — Result: ☐

---

#### `create_task_schedule(task_id: str, schedule_type: str, ...)` — WRITE
_Create a new schedule for a task using interval, daily, weekly, biweekly, or monthly types._
1. **"Schedule the Stream Probe task to run every 4 hours."** — Expect: Claude calls `create_task_schedule(task_id="stream_probe", schedule_type="interval", interval_seconds=14400)`; returns "Schedule created for 'stream_probe': <description> (id=N)". — Result: ☐
2. **"Schedule M3U Refresh to run daily at 3:30 AM."** — Expect: Claude calls `create_task_schedule(task_id="m3u_refresh", schedule_type="daily", schedule_time="03:30")`; returns schedule created confirmation. — Result: ☐
3. **"Schedule the EPG Refresh task every Monday and Wednesday at 2:00 AM."** — Expect: Claude calls `create_task_schedule(task_id="epg_refresh", schedule_type="weekly", days_of_week=[2, 4], schedule_time="02:00")` (days: 0=Sunday, 1=Monday, 2=Tuesday…); returns schedule created confirmation. — Result: ☐
4. **`schedule_type="interval"` with `interval_seconds=0`** — Expect: Backend Pydantic validator rejects with "interval_seconds must be > 0"; tool surfaces the 422 error. — Result: ☐
5. **`schedule_type="interval"` with `interval_seconds=None` (omit it)** — Expect: Backend Pydantic validator rejects; tool surfaces error. — Result: ☐
6. **`schedule_type="cron_expression"` (the old removed type)** — Expect: Backend rejects it; rejection is clear, not a silent 500. — Result: ☐
7. **`schedule_type="daily"` with no `schedule_time`** — Expect: Backend may default or reject; behavior documented. — Result: ☐
8. **`schedule_type="monthly"` with `day_of_month=-1`** — Expect: "Last day of month" — backend accepts this value. — Result: ☐
9. **`schedule_type="weekly"` with `days_of_week=[0, 6]` (Sunday and Saturday)** — Expect: Valid; schedule created successfully. — Result: ☐

---

#### `delete_task_schedule(task_id: str, schedule_id: int)` — DESTRUCTIVE
_Delete a specific task schedule and verify it is gone via a read-back check._
1. **"Delete schedule #3 from the M3U Refresh task."** — Expect: Claude calls `delete_task_schedule(task_id="m3u_refresh", schedule_id=3)`; deletes the schedule; reads back the schedule list; confirms schedule 3 is absent; returns "Schedule 3 deleted from task 'm3u_refresh'." — Result: ☐
2. **Non-existent schedule_id** — Expect: Backend returns 404; tool returns `Error deleting schedule: DELETE /api/tasks/m3u_refresh/schedules/3 -> HTTP 404 Not Found: Schedule 3 not found for task m3u_refresh`. — Result: ☐
3. **Non-existent task id** — Expect: Backend 404; same error surfacing. — Result: ☐
4. **Delete the last schedule for a task** — Expect: Task now has zero schedules; still runs on-demand but no longer on a schedule; read-back returns an empty list cleanly. — Result: ☐
5. **Simulate a partial failure: delete succeeds (204) but the read-back list call fails** — Expect: `still_present` is set to `None` (not `True`), so the WARNING is NOT shown even though confirmation is unavailable; success message is returned — this is a silent gap; confirm and document it. — Result: ☐

---

## Stats & Analytics

#### `get_channel_stats()` — READ-ONLY
_Get active viewer counts with media-server user names, client IP, and provider attribution per channel._
1. **"Who's watching right now, and on which media server or device?"** — Expect: Claude calls `get_channel_stats()`; returns total channel count, active channel count, and for each active channel: channel name, viewer count, media-server user names (e.g., "home via Dispatcharr"), client IP, and provider name where available. Example: "Channel Stats (3 active of 47 total): Active channels: US : ESPN — 2 viewer(s) [home via Dispatcharr · 192.168.1.10 · Provider 1], US : ESPN 2 — 1 viewer(s)". — Result: ☐
2. **"Show me channel stats" when all streams are idle** — Expect: Returns "No active channels." (not an error; `active` list is empty). — Result: ☐
3. **"Show me channel stats" when the stats endpoint returns an empty list (`[]`)** — Expect: Returns "No active channels." — verify the `if not channels:` branch fires correctly, not `"No channel statistics available."`. — Result: ☐
4. **"Show me channel stats" when Dispatcharr is unreachable** — Expect: Returns user-facing error string: "Error getting channel stats: ..." with underlying exception detail surfaced through `_http_error`. — Result: ☐
5. **"Who's watching?" when media-server attribution fields (`emby_username`, `client_ip`, `provider_name`) are absent from the backend response** — Expect: Tool degrades gracefully and shows viewer count only; no crash. — Result: ☐

---

#### `get_top_watched(limit: int = 10)` — READ-ONLY
_Get the most-watched channels ranked by total viewing time._
1. **"Show me the top 5 most-watched channels by total viewing time."** — Expect: Claude calls `get_top_watched(limit=5)`; returns ranked list: "Top 5 most-watched channels: 1. US : ESPN — 42.3h watched, 18 unique viewers …"; hours computed from `total_watch_seconds` or `total_watch_time`, whichever is present. — Result: ☐
2. **"Show me the top 50 most-watched channels"** — Expect: Claude passes `limit=50`; backend returns up to 50; tool does not truncate further (tool slices `items[:limit]` after API response — if API caps at 20, only 20 shown; document if confirmed). — Result: ☐
3. **"Which channels have the most viewers?" when no watch data exists** — Expect: Returns "No watch data available." — Result: ☐
4. **"Show me the top 0 most-watched channels"** — Expect: `limit=0`; tool slices `items[:0]` producing empty list; response is "No watch data available." or empty-but-valid ranked list, not a crash. — Result: ☐

---

#### `get_bandwidth()` — READ-ONLY
_Get current bandwidth usage statistics across all channels in human-readable units._
1. **"What's the current bandwidth usage for ECM? Show me today, this week, and all time."** — Expect: Claude calls `get_bandwidth()`; returns today, this week, this month, and all-time totals in human-readable units (B/KB/MB/GB/TB), plus today's peak bitrate in/out if available. Example: "Bandwidth Usage: Today: 14.2 GB, This Week: 87.5 GB …". — Result: ☐
2. **"Show bandwidth" on a fresh install with zero usage** — Expect: All fields at "0 B"; peak bitrate lines do not appear (conditional on `peak_in or peak_out` being truthy). — Result: ☐
3. **"Show bandwidth" when the backend returns a non-dict (e.g., the stats endpoint returns a list)** — Expect: Calling `.get()` on a list is caught by the outer `except Exception as e`; error is surfaced as "Error getting bandwidth: …" rather than crashing the tool call entirely. — Result: ☐
4. **"Show bandwidth stats" when `bytes_val` is a very large number (PB range)** — Expect: The `fmt()` loop correctly exits after "TB" and falls through to "PB" without IndexError. — Result: ☐

---

#### `get_popularity_rankings(limit: int = 10)` — READ-ONLY
_Get channel popularity rankings with scores and trending indicators._
1. **"Show me the popularity rankings for channels — top 20 please."** — Expect: Claude calls `get_popularity_rankings(limit=20)`; returns "Channel Popularity Rankings (150 total, showing top 20): US : ESPN — score: 92.4 ↑ …"; trend icons appear only when `trend` field equals "up" or "down". — Result: ☐
2. **"Show me popularity rankings" when no channels have viewing activity** — Expect: Returns "No popularity data available. Channels need viewing activity first." — Result: ☐
3. **"Show me popularity rankings" using `limit=1`** — Expect: Claude passes `limit=1`; only one entry returned; header says "showing top 1". — Result: ☐
4. **"Show me popularity rankings" when backend returns a bare list instead of `{"rankings": [...], "total": N}`** — Expect: Tool handles both via the ternary; `total` falls back to `len(rankings)`; header is still correct. — Result: ☐

---

#### `get_watch_history(limit: int = 20, channel_id: str | None = None, ip_address: str | None = None, days: int | None = None)` — READ-ONLY
_Get recent channel watch history with optional filters by channel, IP, or days._
1. **"Show me the last 10 watch history entries for the past 7 days."** — Expect: Claude calls `get_watch_history(limit=10, days=7)`; returns entries with channel name, watch duration in minutes, IP address, optional username, active/done status, and connection timestamp; summary stats appear when provided by the backend. — Result: ☐
2. **"Show me watch history for the 'US : ESPN' channel"** — Expect: Claude resolves channel name to id, calls `get_watch_history(channel_id="<resolved-id>")`; only entries for that channel appear. — Result: ☐
3. **"Show me watch history filtered by IP 192.168.1.50"** — Expect: Claude passes `ip_address="192.168.1.50"`; query param is sent correctly. — Result: ☐
4. **"Show me watch history" when there are no history entries** — Expect: Returns "No watch history available." — Result: ☐
5. **"Show me watch history for the last 0 days"** — Expect: `days=0` is falsy — the tool's `if days:` guard skips it, effectively removing the filter; this is a silent no-op (document as semantic bug — operator may intend "today only" and instead get all history). — Result: ☐

---

#### `get_unique_viewers()` — READ-ONLY
_Get unique viewer counts, total connections, and average watch time with optional per-channel breakdown._
1. **"How many unique viewers have connected to ECM? Show me the breakdown by channel."** — Expect: Claude calls `get_unique_viewers()`; returns total unique viewers, today's unique viewers, total connections, and average watch time; if per-channel endpoint is available, also lists top 10 channels by unique viewer count; if per-channel endpoint fails, only totals shown (graceful degradation). — Result: ☐
2. **"How many unique viewers?" on a fresh install** — Expect: All counts at 0; average watch time line is suppressed (`if avg:` guard); no crash on zero values. — Result: ☐
3. **"How many unique viewers?" when `stats_unique_viewers_by_channel` returns a 404 (endpoint not implemented on this Dispatcharr version)** — Expect: Inner `except` catches it silently (DEBUG level); outer result still returns the totals section intact. — Result: ☐
4. **"Show unique viewers" when the primary stats endpoint is unreachable** — Expect: Outer `except` surfaces error: "Error getting unique viewers: …". — Result: ☐

---

#### `compute_stream_sort(channels: list[dict], mode: str = "smart")` — READ-ONLY
_Compute optimal stream sort order for channels using smart or named sorting criteria._
1. **"Compute the smart sort order for the 'US : ESPN' channel — it has three streams. Which order should they be in?"** — Expect: Claude resolves channel name to id and lists streams via other tools, then calls `compute_stream_sort(channels=[{"channel_id": N, "stream_ids": [M, P, Q]}], mode="smart")`; returns sorted stream IDs per channel and flags whether order changed: "Stream Sort Results (1 channels, 1 changed): Channel N: [P, M, Q] (changed)"; uses a 60-second timeout. — Result: ☐
2. **"Compute sort for the 'US : ESPN' channel using resolution mode"** — Expect: Claude passes `mode="resolution"`; mode is sent in the request body; sort results differ from smart mode. — Result: ☐
3. **"Compute sort with an invalid mode like 'banana'"** — Expect: Backend returns a validation error; outer `except` catches it and returns "Error computing stream sort: …" with informative detail from `_http_error`. — Result: ☐
4. **"Compute sort for a channel with no streams"** — Expect: `stream_ids=[]`; backend likely returns an unchanged result with empty sorted_stream_ids; no crash. — Result: ☐
5. **"Compute sort for 50 channels at once"** — Expect: Large body sent; 60-second timeout is respected; timeout surfaces as a clean error rather than a hung tool call. — Result: ☐
6. **"Compute sort for a channel that doesn't exist"** — Expect: Backend likely returns 404 or empty results; graceful handling confirmed. — Result: ☐

---

#### `get_provider_stats(metric: str = "buffering", window: str = "7d", bucket: str = "hour", top_n: int = 50)` — READ-ONLY
_Per-provider Stats v2 data — buffering, watch time, channel heatmap, or bitrate — broken down by provider._
1. **"Which provider is buffering the most over the last week?"** — Expect: Claude calls `get_provider_stats(metric="buffering", window="7d")`; returns ranked list of providers with buffering event counts. Example: "Provider Stats — buffering (7d): 1. Provider 1 — 142 events, 2. HD Homerun — 87 events …". — Result: ☐
2. **"Show me watch time by provider over the last month."** — Expect: Claude calls `get_provider_stats(metric="watch_time", window="30d")`; returns provider watch-time totals. — Result: ☐
3. **"Which provider buffered the most this month?"** — Expect: Claude calls `get_provider_stats(metric="buffering", window="30d")`; `window` query param is sent correctly. — Result: ☐
4. **"Show me bitrate by provider, bucketed by day, over the last 90 days"** — Expect: Claude calls `get_provider_stats(metric="bitrate", window="90d", bucket="day")`; all three params are sent. — Result: ☐
5. **"Show me the channel heatmap for providers — top 10 channels"** — Expect: Claude calls `get_provider_stats(metric="channel_heatmap", top_n=10)`; `top_n` is sent as a query param; returns per-provider table of top-N channels by activity. — Result: ☐
6. **"Show provider stats" with no providers configured** — Expect: Returns "No provider stats available." or an empty table, not a crash. — Result: ☐
7. **Invalid `metric` value (e.g., `metric="latency"`)** — Expect: Backend returns 422 or 400; tool surfaces the error cleanly. — Result: ☐

---

#### `get_user_watch_time(group_by: str = "total", user_id: int | None = None)` — READ-ONLY
_Per-user watch-time totals or daily time series, optionally scoped to one user._
1. **"How much has each user watched in total?"** — Expect: Claude calls `get_user_watch_time(group_by="total")`; returns ranked list of users with cumulative watch-time hours. Example: "Watch Time by User (total): home — 214.3h, kmfelmer — 88.1h". — Result: ☐
2. **"Show me daily watch time per user so I can see trends."** — Expect: Claude calls `get_user_watch_time(group_by="day")`; each user entry expands into a per-day series. — Result: ☐
3. **"How much has the user 'home' watched?"** — Expect: Claude resolves user name to `user_id`, calls `get_user_watch_time(user_id=N)`; `user_id` query param is sent correctly. — Result: ☐
4. **"How much has the user 'kmfelmer' watched?"** — Expect: Claude resolves and calls with the other known user; both dispatcharr-source users resolve correctly. — Result: ☐
5. **No watch data yet** — Expect: Returns "No user watch time data available." or an empty list, not a crash. — Result: ☐
6. **Invalid `group_by` value (e.g., `group_by="week"`)** — Expect: Backend returns 422; tool surfaces the error. — Result: ☐

---

#### `get_user_channel_breakdown(user_id: str, source: str = "dispatcharr")` — READ-ONLY
_Per-channel watch time breakdown for a single user, scoped to dispatcharr or emby source._
1. **"What has the user 'home' been watching, channel by channel?"** — Expect: Claude resolves user name to user_id, calls `get_user_channel_breakdown(user_id="<resolved-id>", source="dispatcharr")`; returns channels watched by that user ranked by time. Example: "Channel breakdown for 'home' (dispatcharr): 1. US : ESPN — 42.3h, 2. US : ESPN 2 — 18.1h, 3. Radio: ESPN Radio — 6.4h …". — Result: ☐
2. **"Show me channel breakdown for the user 'home' via Emby"** — Expect: Claude calls `get_user_channel_breakdown(user_id="<id>", source="emby")`; note: no Emby user is active in this deployment right now — use source=dispatcharr with 'home' or 'kmfelmer', or run this test when an Emby session exists; correct endpoint path used (`/api/stats/users/emby/<id>`). — Result: ☐
3. **"Show channel breakdown for 'kmfelmer'"** — Expect: Claude resolves and calls with `source="dispatcharr"`; this known user returns data. — Result: ☐
4. **"Show channel breakdown for a user who has never watched anything"** — Expect: Returns "No channel data for this user." or an empty list, not a crash. — Result: ☐
5. **Invalid `source` value (e.g., `source="plex"`)** — Expect: Backend returns 422 or 404; tool surfaces the error cleanly. — Result: ☐
6. **User name that does not resolve (e.g., 'phantom_user')** — Expect: Claude reports it cannot find the user; does not call the tool with an invalid id. — Result: ☐

---

#### `get_trending(direction: str = "up", limit: int = 10)` — READ-ONLY
_Channels trending up or down in viewer interest._
1. **"What channels are trending up this week?"** — Expect: Claude calls `get_trending(direction="up", limit=10)`; returns channels with trend score or velocity. Example: "Trending Up (top 10): 1. US : ESPN — +34% this week, 2. Radio: ESPN Radio — +18% this week …". — Result: ☐
2. **"Which channels are losing viewers right now?"** — Expect: Claude calls `get_trending(direction="down")`; returns channels with declining viewership. — Result: ☐
3. **"What's trending down?"** — Expect: Claude calls `get_trending(direction="down")`; `direction` param is sent correctly. — Result: ☐
4. **"Show me the top 25 trending channels"** — Expect: Claude calls `get_trending(limit=25)`; `limit` is sent as a query param. — Result: ☐
5. **No trending data yet (popularity calculation has not been run)** — Expect: Returns "No trending data available." or "Run a popularity calculation first" — if the response is "Run a popularity calculation first", that is the expected response on a fresh deployment; run `get_popularity_rankings` or trigger a popularity calculation before retesting. — Result: ☐
6. **Invalid `direction` value (e.g., `direction="sideways"`)** — Expect: Backend returns 422; tool surfaces the error. — Result: ☐

---

#### `get_channel_popularity(channel_id: str)` — READ-ONLY
_Popularity score, rank, trend direction, and recent viewer counts for a single channel._
1. **"How popular is the 'US : ESPN' channel?"** — Expect: Claude resolves channel name to channel_id, calls `get_channel_popularity(channel_id="<resolved-id>")`; returns the channel's popularity score, rank among all channels, trend direction, and recent viewer metrics. Example: "Popularity for 'US : ESPN': score 92.4, rank #3 of 150, trending ↑, 18 unique viewers this week." — Result: ☐
2. **"How popular is the 'Phantom Channel'?" (non-existent name)** — Expect: Claude cannot resolve the name; reports it cannot find the channel; does not call the tool with an invalid id. — Result: ☐
3. **"How popular is the 'US : ESPN 2' channel?" when it has never been watched** — Expect: Backend may return a zero-score result or 404; tool handles both without crashing. — Result: ☐
4. **"How popular is the 'Radio: ESPN Radio' channel?"** — Expect: Name resolves and endpoint responds; or returns meaningful empty state if popularity data is absent (popularity tables may be empty until a popularity calculation runs). — Result: ☐
5. **Channel with `score=None` in the backend response** — Expect: Formatter handles None gracefully; no `TypeError` on `.1f` format. — Result: ☐

---

#### `get_activity(limit: int = 50, offset: int = 0, event_type: str | None = None)` — READ-ONLY
_Recent system activity events — channel start/stop, buffering, client connects — with optional type filter._
1. **"Show me recent activity on the server."** — Expect: Claude calls `get_activity(limit=50)`; returns events in reverse-chronological order: timestamp, event type, channel name (if applicable), client IP, and relevant detail. Example: "Recent Activity (20 events): 2026-05-22 14:32 — channel_start: US : ESPN (192.168.1.10), 2026-05-22 14:31 — buffering: US : ESPN 2 …". — Result: ☐
2. **"Show me the last channel-start events."** — Expect: Claude calls `get_activity(event_type="channel_start")`; only channel-start events appear. — Result: ☐
3. **"Show me only buffering events"** — Expect: Claude calls `get_activity(event_type="buffering")`; `event_type` query param is sent correctly; only buffering events appear. — Result: ☐
4. **"Show me the next page of activity"** — Expect: Claude calls `get_activity(offset=50)`; `offset` is sent as a query param. — Result: ☐
5. **No activity yet** — Expect: Returns "No recent activity." or an empty list, not a crash. — Result: ☐
6. **Invalid `event_type` value (e.g., `event_type="explosion"`)** — Expect: Backend returns 422 or empty list; tool surfaces the result correctly. — Result: ☐

---

#### `get_channel_bandwidth(days: int = 7, limit: int = 20, sort_by: str = "bytes")` — READ-ONLY
_Per-channel bandwidth consumption — bytes, connection count, and watch time — sortable by metric._
1. **"Which channels used the most bandwidth this week?"** — Expect: Claude calls `get_channel_bandwidth(days=7, limit=20, sort_by="bytes")`; returns ranked list of channels with bandwidth totals in human-readable units. Example: "Channel Bandwidth (7d, top 20, sorted by bytes): 1. US : ESPN — 142.3 GB, 847 connections, 2. US : ESPN 2 — 87.1 GB, 612 connections …". — Result: ☐
2. **"Which channels had the most connections in the last 30 days?"** — Expect: Claude calls `get_channel_bandwidth(days=30, sort_by="connections")`; both `days` and `sort_by` are sent correctly. — Result: ☐
3. **"Which channels had the most connections this month?"** — Expect: Claude calls `get_channel_bandwidth(days=30, sort_by="connections")`; both params sent correctly. — Result: ☐
4. **"Show me channel bandwidth sorted by watch time"** — Expect: Claude calls `get_channel_bandwidth(sort_by="watch_time")`; sort is applied server-side, not client-side. — Result: ☐
5. **No bandwidth data yet** — Expect: Returns "No channel bandwidth data available." or an empty list, not a crash. — Result: ☐
6. **Invalid `sort_by` value (e.g., `sort_by="latency"`)** — Expect: Backend returns 422; tool surfaces the error. — Result: ☐

---
## System & Backup

#### `get_settings()` — READ-ONLY
_Retrieve current ECM settings: connection status, preferences, and probe configuration._
1. **"Show me the current ECM settings, including connection status and probe configuration."** — Expect: `get_settings()` called; response shows Dispatcharr URL, connection status, theme, timezone, probe timeout/parallelism/concurrency/schedule, and notification method presence as boolean indicators (SMTP, Discord, Telegram configured/not configured); no raw credentials shown; secret-leak verified clean in 0.17.2 live test — no webhook URL or bot token in any output path. — Result: ☐
2. **"Show me ECM settings" on a fresh unconfigured install** — Expect: `configured: False`, URL "Not configured"; no crash on missing fields. — Result: ☐
3. **"Show me ECM settings" when the backend is unreachable** — Expect: outer `except` returns "Error getting settings: …". — Result: ☐

---

#### `create_backup()` — WRITE
_Trigger an ECM configuration backup of settings, database, and logos._
1. **"Create a backup of the ECM configuration right now."** — Expect: `create_backup()` called; backup endpoint fires; response: "Backup created successfully. Download it from the ECM Settings page." Note: backup tools require the MCP key to be admin-authorized (confirmed 0.17.2). — Result: ☐
2. **"Create a backup now" when the backup endpoint returns the streaming zip** — Expect: critical path — `r.json()` raises `JSONDecodeError` on the streaming zip response; outer `except` returns "Error creating backup: …" even though the backup succeeded; confirm this known bug behavior is present. — Result: ☐
3. **"Create a backup now" when the ECM container has insufficient disk space** — Expect: backend returns HTTP 500; error detail from `_http_error` is surfaced cleanly. — Result: ☐
4. **"Create a backup now" and immediately do it again** — Expect: idempotent; two separate zips produced; no state corruption. — Result: ☐

---

#### `get_export_sections()` — READ-ONLY
_List the available YAML export sections for selective backup._
1. **"What sections can I include in a YAML export backup?"** — Expect: `get_export_sections()` called; response lists all sections with keys and labels, e.g. "Available export sections: - settings: Settings, - scheduled_tasks: Scheduled Tasks, …" (13 sections from `RESTORABLE_SECTIONS`). — Result: ☐
2. **"What export sections are available?" when the backend is unconfigured** — Expect: endpoint available regardless of Dispatcharr connection; static section list returned normally. — Result: ☐
3. **"What export sections are available?" when backend returns an empty list** — Expect: "No export sections available." — Result: ☐
4. **"What export sections are available?" when a section dict lacks `key` or `label` keys** — Expect: `KeyError` caught by outer `except`; returns `"Error: 'key'"`; confirm this is not triggered by the current backend. — Result: ☐

---

#### `list_saved_backups()` — READ-ONLY
_List saved YAML backup files on the server, sorted newest first._
1. **"List all saved backups on the server."** — Expect: `list_saved_backups()` called; response: "Saved backups (N): ecm-backup-2026-05-20_030000.yaml — 142.3 KB (2026-05-20T03:00:00+00:00) …"; files listed newest first. Note: requires admin-authorized MCP key (confirmed 0.17.2). — Result: ☐
2. **"List saved backups" when no scheduled backups have run yet (empty backups directory)** — Expect: "No saved backups." — Result: ☐
3. **"List saved backups" when the `backups/` directory exists but contains no matching files** — Expect: backend returns `[]`; "No saved backups." — Result: ☐
4. **"List saved backups" when a backup file is very large (e.g., 50 MB)** — Expect: `size_kb` shows `51200.0 KB` verbatim; no crash. — Result: ☐

---

#### `delete_saved_backup(filename: str)` — DESTRUCTIVE
_Delete a named YAML backup file from the server._
1. **"List my saved backups, then delete the oldest one."** — Expect: `list_saved_backups()` called to find oldest backup by date; `delete_saved_backup(filename="<oldest-filename>")` called; read-back confirms file no longer in list; response: "Deleted backup: ecm-backup-\<date\>.yaml". Note: requires admin-authorized MCP key (confirmed 0.17.2). — Result: ☐
2. **"Delete the backup from last Tuesday" (a date with no matching file)** — Expect: Claude resolves via `list_saved_backups`; no match found; Claude reports none found rather than calling `delete_saved_backup` with a fabricated name; backend would return HTTP 404 if called with a non-existent filename. — Result: ☐
3. **"Delete the backup from yesterday" using a path traversal attempt in the resolved name** — Expect: backend `_BACKUP_FILENAME_RE` rejects it with HTTP 400 ("Invalid filename"); tool surfaces the 400 as an error; tool does NOT attempt to construct the path locally. — Result: ☐
4. **"Delete the backup from this morning" when already deleted** — Expect: first call succeeds; second attempt returns HTTP 404. — Result: ☐

---

#### `get_journal(limit: int = 20, category: str | None = None)` — READ-ONLY
_Retrieve recent ECM activity journal entries, optionally filtered by category._
1. **"Show me the last 50 journal entries for the settings category."** — Expect: `get_journal(limit=50, category="settings")` called; response: "Recent journal entries (N): [timestamp] settings/update: Changed Dispatcharr URL to http://… …"; detail truncated to 80 characters; secret-leak verified clean in 0.17.2 live test — no non-redacted credential field values in settings category entries. — Result: ☐
2. **"Show me journal entries" when the journal is empty (fresh install)** — Expect: "No journal entries found." — Result: ☐
3. **"Show me journal entries for the channels category"** — Expect: `category="channels"` sent as a query param; filtered results returned. — Result: ☐
4. **"Show me the last 100 journal entries"** — Expect: `limit=100` sent as `page_size=100`; backend returns up to 100 entries; confirm `page_size` param is used correctly (fixed in bd-vtghg Phase 2 — old `limit` param was ignored). — Result: ☐
5. **"Show me journal entries filtered by an invalid category like 'nonexistent'"** — Expect: backend returns empty list or 400; handled gracefully with no crash. — Result: ☐

---

## Notifications & Alerts

#### `list_notifications(limit: int = 20)` — READ-ONLY
_List current notifications with unread count and per-item read status._
1. **"Show me all my unread notifications."** — Expect: `list_notifications(limit=20)` called; response: "Notifications (3 unread, 15 total): Stream probe failed (stream_stats) [NEW] — 2026-05-22T10:00:00 …"; unread items marked `[NEW]`, read items have no marker. — Result: ☐
2. **"Show me my notifications" when there are none** — Expect: "No notifications." — Result: ☐
3. **"Show me the last 100 notifications"** — Expect: `limit=100` sent as `page_size=100`; backend may cap results at 50; verify capped response is returned cleanly without error. — Result: ☐
4. **"Show me my notifications" when the backend returns a bare list instead of `{"notifications": [...], "total": N, "unread_count": N}`** — Expect: tool falls back gracefully; `unread` defaults to 0; output shown without error. — Result: ☐

---

#### `mark_notifications_read()` — WRITE
_Mark all notifications as read and confirm via read-back._
1. **"Mark all my notifications as read."** — Expect: `mark_notifications_read()` called; PATCH sent to `/api/notifications/mark-all-read`; read-back check passes; response: "All notifications marked as read." — Result: ☐
2. **"Mark all my notifications as read" when there are already no unread notifications** — Expect: backend marks 0 rows; read-back sees `unread_count=0`; response: "All notifications marked as read." (idempotent). — Result: ☐
3. **"Mark all my notifications as read" when the backend is transiently slow and the read-back fires before propagation** — Expect: WARNING fires ("WARNING: marked all read but N notification(s) still show as unread"); operator should retry manually to confirm — false positive risk is documented. — Result: ☐
4. **"Mark all my notifications as read" when the backend returns a non-dict response** — Expect: tool defaults unread count to 0 gracefully; no crash. — Result: ☐

---

#### `delete_all_notifications()` — DESTRUCTIVE
_Delete notifications; note backend defaults to read-only deletion._
1. **"Delete all my notifications to clear the notification center."** — Expect: `delete_all_notifications()` called; response: "All notifications deleted." On soft failure (notifications remain): "WARNING: requested delete-all but N notification(s) remain." — Result: ☐
2. **"Clear all my notifications" when the inbox has both read and unread notifications** — Expect: CRITICAL BEHAVIOR BUG — backend `DELETE /api/notifications` defaults to `read_only=True`; only read notifications are deleted; unread notifications survive; read-back triggers WARNING; operator's intent ("delete all") is not fulfilled; confirm this semantic bug is present. — Result: ☐
3. **"Clear all my notifications" when there are no notifications** — Expect: backend returns `{"deleted": 0}`; read-back sees `total=0`; response: "All notifications deleted." (correct). — Result: ☐
4. **"Clear all my notifications" twice** — Expect: first call removes read ones; second call removes any that were unread if another tool or UI marked them read between calls; still affected by `read_only=True` default. — Result: ☐

---

#### `list_alert_methods()` — READ-ONLY
_List all configured alert methods with type, enabled state, and notification levels._
1. **"Show me what alert methods are configured in ECM."** — Expect: `list_alert_methods()` called; response: "Alert Methods (2): My Discord (id=1) — discord, enabled [error, warning], Email Alerts (id=2) — smtp, disabled [error]"; each method shows type, enabled state, and notification levels. — Result: ☐
2. **"Show me my alert methods" when none are configured** — Expect: "No alert methods configured." — Result: ☐
3. **"Show me my alert methods" when the `alert-methods` endpoint is not available (older Dispatcharr)** — Expect: outer `except` surfaces the HTTP error. — Result: ☐
4. **"Show me my alert methods" when a method has no levels configured (all `notify_*` fields false)** — Expect: `level_str` is empty string; method appears with no level bracket; no crash. — Result: ☐

---

#### `test_alert_method(method_id: int)` — WRITE
_Send a test notification through a configured alert method._
1. **"Send a test through my alert method."** — Expect: `list_alert_methods()` called to resolve named method to numeric ID; `test_alert_method(method_id=<id>)` called; backend sends test message via configured channel (Discord webhook, email, Telegram); response: "Test alert sent successfully. Test message dispatched." — Result: ☐
2. **"Test an alert method" when no alert methods are configured** — Expect: Claude resolves via `list_alert_methods`; no methods found; Claude reports none configured rather than guessing an ID. — Result: ☐
3. **"Test my alert method" when the method's credentials are invalid** — Expect: backend attempts delivery, fails; response: "Test alert failed: Connection refused" (or similar backend error detail). — Result: ☐
4. **"Test my alert method" when the method is disabled** — Expect: verify whether backend sends test anyway (ignoring enabled state) or returns `success: false`; confirm ECM's actual behavior. — Result: ☐
5. **"Test an alert method" using ID 0 (resolved edge case)** — Expect: `method_id=0` unlikely to match any real method; backend returns 404. — Result: ☐

---

## Export & Publish

#### `list_export_profiles()` — READ-ONLY
_List all export profiles for generating M3U/XMLTV files._
1. **"Show me all the export profiles configured in ECM."** — Expect: `list_export_profiles()` called; in this deployment no export profiles are configured — expected response: "No export profiles configured." If profiles exist, response lists each one: name, ID, and selection mode. — Result: ☐
2. **"List my export profiles" when none are configured** — Expect: "No export profiles configured." (expected result in this deployment). — Result: ☐
3. **"List my export profiles" when the backend returns a non-list response** — Expect: iterating a dict iterates keys, not values — output would list key names as profile names; confirm this latent bug behavior if the backend ever wraps the list in an envelope. — Result: ☐

---

#### `generate_export(profile_id: int)` — WRITE
_Trigger M3U/XMLTV generation for an export profile by ID._
1. **"Create an export profile named 'MCPTEST Export' first, then generate it."** — Expect: `create_export_profile(name="MCPTEST Export")` called first; `list_export_profiles()` called to resolve name to numeric ID; `generate_export(profile_id=<id>)` called; response: "Export generated for profile \<id\>. Check ECM for download links." — Result: ☐
2. **"Generate the export for a profile that doesn't exist"** — Expect: Claude resolves via `list_export_profiles`; no match found; Claude reports none found rather than guessing an ID; backend would return HTTP 404 if called with a non-existent ID. — Result: ☐
3. **"Generate the export for my 'MCPTEST Export' profile" when another generation for the same profile is already in progress** — Expect: backend tracks in-progress generations in `_generating: set[int]`; concurrent request may be rejected; verify tool surfaces the error correctly. — Result: ☐
4. **"Generate the export for my 'MCPTEST Export' profile" with a very large channel list** — Expect: generation may exceed the 30-second default timeout; verify whether timeout produces a clean error or a hung call. — Result: ☐

---

#### `create_export_profile(name: str)` — WRITE
_Create a new export profile with default settings._
1. **"Create a new export profile called 'MCPTEST Export'."** — Expect: `create_export_profile(name="MCPTEST Export")` called; backend creates profile with default settings (selection_mode=all, direct stream URL, etc.); response: "Export profile created: MCPTEST Export (id=\<new-id\>)". — Result: ☐
2. **"Create an export profile" with an empty name** — Expect: `name=""` sent to backend; Pydantic `ProfileCreateRequest` may reject with 422; verify error is surfaced. — Result: ☐
3. **"Create an export profile called 'MCPTEST/Export'" (special characters)** — Expect: `name` field has no character restriction; backend accepts it; `filename_prefix` defaults to "playlist" (valid). — Result: ☐
4. **"Create an export profile called 'MCPTEST Export'" twice** — Expect: verify whether backend allows duplicate names (no uniqueness constraint documented) or rejects the second creation. — Result: ☐

---

#### `delete_export_profile(profile_id: int)` — DESTRUCTIVE
_Delete an export profile by ID._
1. **"List my export profiles, then delete the one called 'MCPTEST Export'."** — Expect: `list_export_profiles()` called to resolve 'MCPTEST Export' to its ID; `delete_export_profile(profile_id=<id>)` called; response: "Export profile \<id\> deleted." — Result: ☐
2. **"Delete an export profile that doesn't exist"** — Expect: Claude resolves via `list_export_profiles`; no match found; Claude reports none found rather than guessing an ID; backend would return HTTP 404 if called directly. — Result: ☐
3. **"Delete the 'MCPTEST Export' profile" when a publish configuration references it** — Expect: backend returns 400/409 due to foreign key constraint; error detail surfaced. — Result: ☐
4. **"Delete the 'MCPTEST Export' profile" twice** — Expect: first call succeeds; second call returns HTTP 404. — Result: ☐

---

#### `list_cloud_targets()` — READ-ONLY
_List configured cloud storage targets for publishing exports._
1. **"What cloud storage targets are set up for publishing exports?"** — Expect: `list_cloud_targets()` called; in this deployment no cloud targets are configured — expected response: "No cloud targets configured." If targets exist, response lists each one: name, ID, and provider type (s3, r2, etc.). — Result: ☐
2. **"List my cloud targets" when none are configured** — Expect: "No cloud targets configured." (expected result in this deployment). — Result: ☐
3. **"List my cloud targets" when the export cloud-targets endpoint returns a non-list response** — Expect: same envelope-handling risk as `list_export_profiles`; verify graceful output rather than iterating dict keys. — Result: ☐

---

#### `list_publish_configs()` — READ-ONLY
_List publish configurations linking export profiles to cloud targets, for use with `publish_export`._
1. **"What publish configurations do I have set up?"** — Expect: `list_publish_configs()` called; in this deployment no publish configurations are configured — expected response: "No publish configurations configured." If configs exist, response lists each one: name, export profile it draws from, cloud target it publishes to, schedule type (manual / scheduled), and enabled state. — Result: ☐
2. **"What publish configurations do I have set up?" when none exist** — Expect: "No publish configurations configured." (expected result in this deployment). — Result: ☐
3. **"What publish configurations do I have set up?" when the `/api/export/publish-configs` endpoint returns a non-list response** — Expect: same envelope-handling risk as `list_export_profiles` and `list_cloud_targets`; verify graceful output rather than iterating dict keys. — Result: ☐
4. **"What publish configurations do I have set up?" when the backend is unreachable** — Expect: outer `except` surfaces the HTTP error cleanly. — Result: ☐

---

#### `publish_export(config_id: int)` — WRITE
_Trigger a publish pipeline for an export configuration to its cloud target._
1. **"Publish my export."** — Expect: `list_publish_configs()` called to resolve named publish config to numeric `config_id`; `publish_export(config_id=<id>)` called; response: "Publish started for config \<id\>. [message from backend if present]". Note: no publish configs in this deployment — create one first before testing. — Result: ☐
2. **"Publish an export" when no publish configs exist** — Expect: Claude resolves via `list_publish_configs`; no configs found; Claude reports none configured rather than guessing an ID; backend would return HTTP 404 if called directly. — Result: ☐
3. **"Publish an export" when the cloud target is misconfigured (wrong credentials)** — Expect: publish pipeline runs asynchronously; MCP response may say "Publish started" even if upload will fail; verify whether endpoint returns a job ID or final status. — Result: ☐
4. **"Publish an export" twice concurrently** — Expect: backend may queue or reject the second request; verify tool surfaces the response correctly. — Result: ☐
