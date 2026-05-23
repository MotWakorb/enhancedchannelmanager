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
4. **Record the result** in the tracking checklist (✅ pass / ❌ fail / ⚠️ partial)
   and note the actual behavior on any ❌/⚠️.

## ⚠️ Safety — read before running destructive tests

Each tool is tagged **READ-ONLY**, **WRITE**, or **DESTRUCTIVE**.

- **Take a backup first.** Run `create_backup` (System & Backup) — or the UI
  backup — before exercising any DESTRUCTIVE tool. (Note: `create_backup` itself
  is a *suspected-broken* tool — see priorities below — so verify your backup
  exists via `list_saved_backups` / the UI.)
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
- **Purpose:** List all channels with optional filters for group, name search, and stream count; compact mode returns pipe-delimited output of all channels.
- **Prompt to Claude:** "Show me all my channels, 50 at a time."
- **Expected:** Claude calls `list_channels()` with no args. Returns a numbered list like `#101: ESPN (id=42) — 3 streams`. Total shown vs total found counts are correct.
- **Edge / failure tests:**
  - "List channels in a group that doesn't exist, like 'Empty Test Group'" → Should return "No channels found" (empty list from backend), not an error crash.
  - "List all channels with 0 streams so I can clean up empties" → Claude calls `list_channels(max_streams=0, compact=False)`. Tool enters the paginated fetch loop (page_size=500). Verify it returns only channels where `len(streams) == 0`. Confirm that after the loop the shown count matches actual zero-stream channels — the stream-count filter is applied client-side after fetching all pages, so a partial first page would silently under-count.
  - "Give me a compact list of all my channels for analysis" → Claude calls `list_channels(compact=True)`. Should return pipe-delimited `number|id|name|streams` format with all channels (no limit applied). Confirm `?` appears for channels missing `channel_number`.
  - "Search for channels named 'ESPN' with at least 2 streams" → Claude calls `list_channels(search='ESPN', min_streams=2)`. Verify the search is passed to the backend AND the stream-count filter is applied afterward.
- **Watch for (possible bug areas):**
  - In the non-compact/non-stream-filter path, `page_size=limit` is sent to the backend but the response `total` comes from the backend's count field, not len(channels). If the backend paginates (and returns only `limit` rows), `total` may report the full count while the shown list is one page — the `"... and N more"` message may misrepresent how many MORE are fetchable vs already fetched.
  - Stream-count filtering (`min_streams`/`max_streams`) filters on `len(c.get("streams", []))`. If the backend's list endpoint returns stream IDs (not full objects), this works. If it returns stream objects with nested fields, it still works. But if `streams` key is missing entirely (some lightweight list responses), all channels silently fail the filter and return empty — no error surfaced.
  - The compact mode ignores `limit` ("shows all") but this is only documented in the docstring — Claude may not know to mention it in responses.

---

#### `get_channel(channel_id: int)` — READ-ONLY
- **Purpose:** Retrieve detailed information about a single channel by ID, including stream IDs, group, EPG tvg_id, and logo status.
- **Prompt to Claude:** "Tell me everything about the 'ESPN' channel." (just name it — Claude looks it up)
- **Expected:** Claude calls `get_channel(channel_id=<resolved id>)`. Returns formatted block with name, ID, number, group_id, EPG tvg_id, logo presence, stream count with first 10 stream IDs, and auto_created flag.
- **Edge / failure tests:**
  - "Look up a channel called 'Does Not Exist'" → Backend returns 404; the exception is caught and returned as `"Error getting channel <id>: GET /api/channels/<id> -> HTTP 404 Not Found"`. Confirm no stack trace leaks to the Claude response.
  - "Check the details for a channel with number 0" → Should trigger a backend 404 or 400; same graceful error handling expected.
- **Watch for (possible bug areas):**
  - Stream list is truncated at 10 IDs in display (`stream_ids[:10]`) — if a channel has 11+ streams, Claude reports the count correctly but only shows the first 10. This is display-only truncation, not a data bug, but operators may not realize they're seeing a partial list.
  - `c.get('group_id')` is displayed as `channel_group_id` from the API response. If the Dispatcharr field name is `channel_group` (not `channel_group_id`), this always shows `None` even for grouped channels.

---

#### `create_channel(name: str, channel_number: int = None, group_id: int = None)` — WRITE
- **Purpose:** Create a new channel with an optional channel number and group assignment.
- **Prompt to Claude:** "Create a channel called 'Fox News HD' in the 'USA | Sports' group, number 360." (just name it — Claude looks it up)
- **Expected:** Claude calls `create_channel(name='Fox News HD', channel_number=360, group_id=<resolved id>)`. Tool maps `group_id` → `channel_group_id` in the payload (bd-7q9l3 fix). Response echoes the created channel's actual values: `Channel created: #360: Fox News HD (id=<new_id>, group_id=<resolved id>)`.
- **Edge / failure tests:**
  - "Create a channel called 'Test' with no number or group" → Claude calls `create_channel(name='Test')`. Payload contains only `{"name": "Test"}`. Response shows `channel_number=?` and no group_info. Confirm neither `channel_number` nor `channel_group_id` is sent as `null` (they are guarded by `if ... is not None`).
  - "Create a channel with an empty name ''" → Backend should return 400 or 422; confirm graceful error message rather than a created channel with a blank name.
  - "Create a channel in a group called 'Does Not Exist Group'" → Backend may 400 or silently ignore the group. Verify the returned group_id reflects what actually got set, not what was requested — the tool correctly echoes `result.get("channel_group_id")` from the response.
- **Watch for (possible bug areas):**
  - The bug that triggered the bd-7q9l3 fix was sending `group_id` (bare) instead of `channel_group_id`. Verify the current code correctly uses `payload["channel_group_id"] = group_id`. If regression: the channel gets created but with no group, and the response returns `group_id=None` silently.
  - `channel_number` is typed `int | None` in the MCP tool signature but the backend `CreateChannelRequest` accepts `Optional[float]`. An integer passed from Claude should coerce fine, but verify a large integer (e.g., 9999) doesn't cause issues.

---

#### `update_channel(channel_id: int, name: str = None, channel_number: int = None, group_id: int = None)` — WRITE
- **Purpose:** Update an existing channel's name, number, or group assignment via a PATCH request.
- **Prompt to Claude:** "Rename the 'ESPN' channel to 'ESPN Classic' and move it to the 'News' group." (just name them — Claude looks them up)
- **Expected:** Claude calls `update_channel(channel_id=<resolved id>, name='ESPN Classic', group_id=<resolved id>)`. Tool maps `group_id` → `channel_group_id`. Response echoes the backend's returned state: `Channel <id> updated: name='ESPN Classic', channel_number=<N>, group_id=<resolved id>`. Critically: the response reflects what the backend returned, not what was sent — so a no-op PATCH (value unchanged) is detectable.
- **Edge / failure tests:**
  - "Update the 'ESPN Classic' channel but don't change anything" → Claude calls `update_channel(channel_id=<resolved id>)` with no optional args. Tool hits the `if not payload: return "No changes specified."` guard and returns early without a backend call. Confirm no HTTP request is made.
  - "Update a channel called 'Does Not Exist'" → Backend returns 404; graceful error message expected.
  - "Move the 'ESPN' channel to a group called 'Does Not Exist Group'" → Behavior depends on backend validation. Verify the response's `group_id` field reflects the actual persisted value, not the requested value.
- **Watch for (possible bug areas):**
  - Same `group_id` → `channel_group_id` mapping as `create_channel` — this was the bd-7q9l3 fix class. If this mapping regresses, `update_channel(group_id=<id>)` silently ignores the group change and returns a 200 with the old group.
  - The backend PATCH endpoint accepts `data: dict` (free-form, forwarded to Dispatcharr). The endpoint contract guard validates keys against `_CHANNEL_PATCH_FIELDS` before sending. A future field addition that is in the backend but not in `_CHANNEL_PATCH_FIELDS` will be silently blocked by the `ContractError` guard.
  - If the backend returns `204 No Content` (unlikely for PATCH, but defensive check): `result` is `None`, and the fallback `return f"Channel {channel_id} updated."` fires rather than the structured response. The operator gets no confirmation of the actual new state.

---

#### `delete_channel(channel_id: int)` — DESTRUCTIVE
- **Purpose:** Permanently delete a single channel by ID.
- **Prompt to Claude:** "Delete the 'ESPN' channel." (just name it — Claude looks it up)
- **Expected:** Claude calls `delete_channel(channel_id=<resolved id>)`. Backend returns 204; tool returns `"Channel <id> deleted."`. Confirm the channel no longer appears in `list_channels`.
- **Edge / failure tests:**
  - "Delete a channel called 'Does Not Exist'" → Backend returns 404; graceful error: `"Error deleting channel <id>: GET ... -> HTTP 404 Not Found"`.
  - "Delete the 'ESPN' channel a second time after it's already been deleted" → Same 404 path; no crash.
- **Watch for (possible bug areas):**
  - No confirmation prompt before deletion — the tool fires immediately. There is no "are you sure?" guard; a Claude agent running on an ambiguous prompt could delete the wrong channel with no recourse. This is the expected design, but operators should be coached to always confirm the channel name before deleting.
  - The success message echoes the resolved ID, not any backend-confirmed state. If the backend silently succeeded on a different channel (routing bug), the operator would not know.

---

#### `bulk_delete_channels(channel_ids: list[int])` — DESTRUCTIVE
- **Purpose:** Delete multiple channels serially, collecting errors without aborting, then reporting a summary.
- **Prompt to Claude:** "Delete the 'ESPN Backup', 'ESPN Alt', and 'ESPN West' channels all at once." (just name them — Claude looks them up)
- **Expected:** Claude calls `bulk_delete_channels(channel_ids=[<id1>, <id2>, <id3>])`. Tool issues three sequential `channels_delete` calls. Response: `"Bulk delete complete: 3 deleted, 0 errors out of 3 requested."` Verify all three channels are gone.
- **Edge / failure tests:**
  - "Bulk delete 'ESPN Backup', a channel called 'Does Not Exist', and 'ESPN West'" → Should return `"Bulk delete complete: 2 deleted, 1 errors out of 3 requested."` with the first error detail included. Confirm the two valid channels were deleted despite the mid-list error.
  - "Bulk delete with an empty list []" → The for-loop body never executes. Result: `"Bulk delete complete: 0 deleted, 0 errors out of 0 requested."` — not an error, just a no-op. Confirm no crash.
  - "Bulk delete 100 channels" → Tests that all 100 sequential HTTP calls complete without timeout. With `DEFAULT_TIMEOUT=30s` per call this is up to 50 minutes worst case — no per-batch timeout or batch size limit exists.
- **Watch for (possible bug areas):**
  - Serial implementation (one DELETE per channel) with no concurrency or batching. For large lists (50+ channels) this is extremely slow and risks the MCP tool call timing out before all deletions complete. The tool-level result string may show partial progress with no way for the operator to know how far it got.
  - Error details are only captured for the first 3 errors (`if errors <= 3`): if channel 4+ fails, the error is counted but not surfaced. For bulk operations on large inputs this gives an incomplete picture.
  - No validation that `channel_ids` is non-empty or that IDs are positive integers before the loop starts.

---

#### `add_stream_to_channel(channel_id: int, stream_id: int)` — WRITE
- **Purpose:** Add a single stream to a channel via `POST /api/channels/{id}/add-stream`.
- **Prompt to Claude:** "Add the 'US: ESPN FHD' stream to the 'ESPN' channel." (just name them — Claude looks them up)
- **Expected:** Claude calls `add_stream_to_channel(channel_id=<resolved id>, stream_id=<resolved id>)`. Returns `"Stream <id> added to channel <id>."` Confirm via `get_channel` on the 'ESPN' channel that the stream now appears in the stream list.
- **Edge / failure tests:**
  - "Add a stream called 'Does Not Exist Stream' to the 'ESPN' channel" → Backend should 404 or 400; graceful error string expected.
  - "Add the 'US: ESPN FHD' stream to the 'ESPN' channel again when it's already there" → Backend behavior varies — Dispatcharr may silently dedup or return an error. Confirm the tool handles both without crashing.
  - "Add the 'US: ESPN FHD' stream to a channel called 'Does Not Exist'" → Backend 404; graceful error.
- **Watch for (possible bug areas):**
  - Success response is purely optimistic — the tool returns the success string without reading back the updated channel to confirm the stream was actually added. A backend bug that returns 200 without persisting would be invisible.
  - No return value on `await client.call_endpoint(...)` is checked — the result is discarded. A 200 response with an error body would be silently treated as success.

---

#### `add_stream(stream_name: str, group_id: int, dedup_action: str = "prompt")` — WRITE
- **Purpose:** Create a channel from a stream name (assigned to a group), with deduplication control: `prompt` queues a merge candidate and returns a `merge_id`; `force_new` skips dedup; `merge_if_found` auto-accepts if confidence meets threshold.
- **Prompt to Claude:** "Add the stream 'NBC Sports HD' to the 'USA | Sports' group and let me know if there's a duplicate." (just name the group — Claude looks it up)
- **Expected:** Claude calls `add_stream(stream_name='NBC Sports HD', group_id=<resolved id>, dedup_action='prompt')`. If a candidate is found: response contains `action=pending_merge`, a `merge_id`, candidate channel name/id, and confidence percentage. Claude should then prompt the operator: accept or dismiss? If no candidate: a new channel is created and the stream is attached.
- **Edge / failure tests:**
  - "Add stream 'NBC Sports HD' to the 'USA | Sports' group, force a new channel regardless of duplicates" → Claude calls `add_stream(stream_name='NBC Sports HD', group_id=<resolved id>, dedup_action='force_new')`. Dedup enqueue is skipped; channel is created and stream attached. Confirm no pending merge row is created.
  - "Add stream 'XYZ Totally Unique 12345' to the 'USA | Sports' group" → No candidate found; tool proceeds directly to channel create + stream attach. Verify the new channel appears in `list_channels` for that group.
  - "Add stream with invalid dedup_action='auto'" → Tool immediately returns the validation error string before any backend call: `"Invalid dedup_action 'auto'. Must be one of: force_new, merge_if_found, prompt"`.
  - "Add stream 'NBC Sports HD' with dedup_action='merge_if_found' when confidence is above threshold" → Claude calls with `merge_if_found`. If `meets_threshold=True` in the enqueue response, the tool auto-calls `channel_merges_accept` and returns `"merge_if_found: stream '...' merged into existing channel '...'"`.
  - "Add stream '' (empty name) to the 'USA | Sports' group" → Backend's enqueue endpoint rejects blank `stream_name` with 400; the tool's enqueue error handler returns a structured `action=error` string.
- **Watch for (possible bug areas):**
  - The `_resolve_stream_id` helper searches by name and returns `streams[0].get("id")` — the first result from a `search=` query. If the stream name is a common substring (e.g., "ESPN"), it may match the wrong stream ID and silently attach the wrong stream to the newly created channel. No exact-name filtering is done here (unlike the backend's `_resolve_streams_by_name` which filters to exact matches).
  - The enqueue endpoint (`channel_merges_enqueue`) runs the matcher at the confidence FLOOR (not the operator threshold), meaning more candidates surface here than in the UI modal. A stream that gets `action=pending_merge` at MCP level may never have shown a dedup prompt in the UI — operators may be surprised.
  - When `merge_if_found` auto-accept fails (the `acc_err` path), the error message references `accept_channel_merge({merge_id})` with a Python-style call syntax — the operator needs to know this is the MCP tool name, not a URL.
  - If `enqueue_resp` is not a dict (e.g., backend returns a list or raw string), `enqueue_resp.get("merge_id")` raises `AttributeError` — not caught by the outer `try/except` in that branch because the enqueue succeeded, so the tool would crash with an unhandled exception.

---

#### `bulk_add_streams_to_channel(channel_id: int, stream_ids: list[int])` — WRITE
- **Purpose:** Add multiple streams to a channel in a single backend call (`POST /api/channels/{id}/add-streams`), skipping streams already present.
- **Prompt to Claude:** "Add the streams 'US: ESPN FHD', 'US: ESPN HD', 'US: ESPN East', and 'US: ESPN West' to the 'ESPN' channel all in one go." (just name them — Claude looks them up)
- **Expected:** Claude calls `bulk_add_streams_to_channel(channel_id=<resolved id>, stream_ids=[<id1>, <id2>, <id3>, <id4>])`. Returns `"Added 4 stream(s) to channel <id>; channel now has N streams."` Confirm via `get_channel` on 'ESPN' that all four stream IDs are present.
- **Edge / failure tests:**
  - "Add 'US: ESPN FHD' and 'US: ESPN HD' to the 'ESPN' channel, where 'US: ESPN FHD' is already on the channel" → Response should show `"Added 1 stream(s) to channel <id> (1 already present); channel now has N streams."` and `"Added: [<id of US: ESPN HD>]"`. The already-present stream is in `skipped`.
  - "Add an empty list of streams to the 'ESPN' channel" → `stream_ids=[]`. Behavior depends on backend — likely `"Added 0 stream(s) to channel <id>."` without error. Confirm no crash.
  - "Add streams to a channel called 'Does Not Exist'" → Backend 404; graceful error: `"Error adding streams to channel <id>: ..."`.
- **Watch for (possible bug areas):**
  - The added list is truncated at 20 IDs in the display (`added[:20]`). For large batches, the operator can't see all the added stream IDs without re-reading the channel.
  - `timeout=120.0` is set on this call, which is correct for potentially slow backends, but there's no retry logic. A timeout during a large batch leaves the channel in an unknown partially-updated state with no diagnostic from the tool.
  - If the backend's response doesn't include `added`, `skipped`, or `total_streams` keys (e.g., a different response schema), all three default to `[]`/`None` and the summary line reads `"Added 0 stream(s) to channel <id>."` — a silent lie if streams were actually added.

---

#### `bulk_assign_epg(mappings: list[dict])` — WRITE
- **Purpose:** Assign EPG tvg_id values to multiple channels at once via sequential PATCH calls.
- **Prompt to Claude:** "Set the EPG ID for the 'ESPN' channel to 'ESPN.us' and the 'CNN' channel to 'CNN.us' in one shot." (just name the channels — Claude looks them up)
- **Expected:** Claude calls `bulk_assign_epg(mappings=[{"channel_id": <ESPN id>, "tvg_id": "ESPN.us"}, {"channel_id": <CNN id>, "tvg_id": "CNN.us"}])`. Returns `"Updated EPG assignments for 2/2 channels."` Confirm both channels now have the correct `tvg_id` via `get_channel`.
- **Edge / failure tests:**
  - "Clear the EPG ID for the 'ESPN' channel by passing an empty tvg_id" → `mappings=[{"channel_id": <ESPN id>, "tvg_id": ""}]`. The tool sends `{"tvg_id": ""}` to the PATCH endpoint. Confirm the channel's EPG link is cleared, not silently ignored.
  - "Bulk assign EPG with a mapping that is missing 'channel_id'" → Tool hits the `if cid is None` guard, appends `"missing channel_id in mapping"` to errors, and skips that entry. Summary: `"Updated EPG assignments for 0/1 channels."` with one error.
  - "Bulk assign EPG to a channel called 'Does Not Exist'" → PATCH returns 404; error is appended and the final error count is surfaced.
- **Watch for (possible bug areas):**
  - Sequential PATCH calls (not batched). For large mappings (100+ entries) this is slow. No concurrency, no batch endpoint.
  - Errors are collected but only the first 10 are shown in the response. Large failure sets give an incomplete picture.
  - The `tvg_id` default is `m.get("tvg_id", "")` — if a mapping omits `tvg_id` entirely, it sends `""` and clears the EPG link on that channel silently. This could be a footgun for operators passing partial mappings.
  - No validation that `mappings` is non-empty or that each dict has the expected keys before the loop. An entirely wrong input schema (e.g., a list of strings instead of dicts) would crash inside the loop with an `AttributeError` that propagates as an uncaught exception to the outer `except Exception as e` and returns a generic error string.

---

#### `remove_stream_from_channel(channel_id: int, stream_id: int)` — WRITE
- **Purpose:** Remove a single stream from a channel via `POST /api/channels/{id}/remove-stream`.
- **Prompt to Claude:** "Remove the 'US: ESPN FHD' stream from the 'ESPN' channel." (just name them — Claude looks them up)
- **Expected:** Claude calls `remove_stream_from_channel(channel_id=<resolved id>, stream_id=<resolved id>)`. Returns `"Stream <id> removed from channel <id>."` Confirm via `get_channel` on 'ESPN' that the stream is no longer in the stream list.
- **Edge / failure tests:**
  - "Remove the 'US: ESPN FHD' stream from the 'ESPN' channel when it isn't on the channel" → Backend behavior determines the response — may 400 or silently succeed. Confirm graceful error handling either way.
  - "Remove the 'US: ESPN FHD' stream from a channel called 'Does Not Exist'" → Backend 404; graceful error string.
- **Watch for (possible bug areas):**
  - Same optimistic-success pattern as `add_stream_to_channel`: the result of `call_endpoint` is not read; a 200 with error body is treated as success. No readback to confirm the stream is actually gone.
  - No check for whether the stream is currently on the channel before attempting removal. Silent success on a no-op removal could mask backend bugs.

---

#### `reorder_streams(channel_id: int, stream_ids: list[int])` — WRITE
- **Purpose:** Set the priority order of streams within a channel by supplying the full ordered stream ID list (first = highest priority).
- **Prompt to Claude:** "Reorder the streams on the 'ESPN' channel so 'US: ESPN FHD' comes first, then 'US: ESPN HD', then 'US: ESPN East'." (just name them — Claude looks them up; confirm full stream list with `get_channel` first)
- **Expected:** Claude calls `reorder_streams(channel_id=<resolved id>, stream_ids=[<id1>, <id2>, <id3>])`. Returns `"Streams reordered for channel <id>. New order: [<id1>, <id2>, <id3>]"`. Verify via `get_channel` on 'ESPN' that the stream order is updated.
- **Edge / failure tests:**
  - "Reorder streams on the 'ESPN' channel with an incomplete list (omitting one stream)" → Behavior depends on backend — it may reject the partial list or silently remove the omitted stream from the channel. This is a high-risk case: an incomplete `stream_ids` list could detach streams from the channel permanently.
  - "Reorder streams on the 'ESPN' channel with stream IDs that don't belong to it" → Backend should reject; confirm graceful error handling.
  - "Reorder streams on the 'ESPN' channel with an empty list []" → Backend may clear all streams from the channel. This is a destructive edge case with no confirmation guard in the tool.
- **Watch for (possible bug areas):**
  - The tool accepts an arbitrary `stream_ids` list and sends it verbatim. If the list is incomplete (missing some of the channel's streams), the backend may interpret this as "set the stream list to exactly these IDs" and silently detach the omitted streams. This is a high-severity potential data loss bug for operators who don't include all current streams.
  - No pre-call read to validate that all provided stream IDs are currently on the channel. The tool simply posts and confirms success by echoing the input, not the backend-confirmed state.
  - Return value from `call_endpoint` is discarded (`await client.call_endpoint(...)`); no check that the backend actually persisted the new order.

---

#### `assign_channel_numbers(channel_ids: list[int], starting_number: int = None)` — WRITE
- **Purpose:** Bulk-assign sequential channel numbers to a list of channels, starting from a specified number or auto-assigned if omitted.
- **Prompt to Claude:** "Assign sequential channel numbers to 'ESPN', 'CNN', and 'TSN 5' starting from 200." (just name them — Claude looks them up)
- **Expected:** Claude calls `assign_channel_numbers(channel_ids=[<ESPN id>, <CNN id>, <TSN 5 id>], starting_number=200)`. Returns `"Assigned numbers to 3 channels starting from 200."` Verify via `get_channel` that 'ESPN', 'CNN', 'TSN 5' now have numbers 200, 201, 202 respectively.
- **Edge / failure tests:**
  - "Auto-assign channel numbers to 'ESPN', 'CNN', and 'TSN 5' (no starting number)" → Claude calls without `starting_number`. Response: `"Assigned numbers to 3 channels starting from auto."`. Confirm the backend chooses sensible numbers.
  - "Assign channel numbers to an empty list" → `channel_ids=[]`. Response: `"Assigned numbers to 0 channels starting from auto."` Confirm no backend error.
  - "Assign starting number 0 or a negative number" → Should be validated by the backend. Verify graceful error if rejected.
- **Watch for (possible bug areas):**
  - The return string `f"Assigned numbers to {len(channel_ids)} channels starting from {starting_number or 'auto'}."` is built from the request inputs, not the backend response. The backend response from `call_endpoint` is completely discarded. If the backend only partially applied the numbers (some channels conflicted, some succeeded), the tool still reports full success.
  - `starting_number: int | None` at the MCP level, but the backend model uses `Optional[float]`. Passing `starting_number=0` results in the string showing "starting from auto" (since `0 or 'auto'` evaluates to `'auto'` in Python). If an operator legitimately wants to start from channel 0, the message is misleading. Note: channel 0 is unusual but the coercion bug is real.

---

#### `merge_channels(target_channel_id: int, source_channel_ids: list[int])` — DESTRUCTIVE
- **Purpose:** Merge one or more source channels into a target channel (keeping the target, absorbing source streams, deleting sources) via the bulk-merge endpoint.
- **Prompt to Claude:** "Merge the duplicate 'ESPN' channels, keeping the main one and deleting the others." (just name them — Claude looks them up; use `list_channels` to identify which to keep)
- **Expected:** Claude calls `merge_channels(target_channel_id=<main ESPN id>, source_channel_ids=[<duplicate ids>])`. Tool calls `channels_bulk_merge` with one merge item. Returns `"Merged N channels into channel <id>."`. Confirm the source channels are deleted and the target now contains their streams.
- **Edge / failure tests:**
  - "Merge an 'ESPN Alt' channel into a target channel that doesn't exist" → Backend should 404 or 422 on the bulk-merge request. Graceful error: `"Error merging channels: POST /api/channels/bulk-merge -> HTTP 404 ..."`.
  - "Merge the 'ESPN' channel into itself" → Backend behavior is undefined; may 400 or silently no-op. Confirm no crash.
  - "Merge into 'ESPN' with an empty source list []" → `source_channel_ids=[]`. `merged = len([])` = 0 so response is `"Merged 0 channels into channel <id>."`. Whether the backend accepts an empty sources list is untested.
- **Watch for (possible bug areas):**
  - The return message counts `len(source_channel_ids)` and is built from inputs. The actual backend response from `call_endpoint` is completely discarded (no `result` variable). If the backend partially failed (some sources merged, some errored), the tool still reports full success with the requested count.
  - Comment in code notes this was a previous contract drift fix (bd-vtghg): the old code called a different endpoint with a different payload shape that would silently 422. Test that the current `channels_bulk_merge` path actually works end-to-end.
  - `timeout=300.0` on this call is appropriate for large merges but there is no progress indication for long-running operations.

---

#### `clear_auto_created(group_ids: list[int] = None)` — DESTRUCTIVE
- **Purpose:** Delete all channels marked `auto_created=True`, optionally scoped to specific group IDs.
- **Prompt to Claude:** "Clear all auto-created channels from the 'USA | Sports' and 'Radio' groups." (just name them — Claude looks them up)
- **Expected:** Claude calls `clear_auto_created(group_ids=[<USA | Sports id>, <Radio id>])`. Returns `"Cleared N auto-created channels in 2 groups."` Confirm channels with `auto_created=True` in those groups are gone.
- **Edge / failure tests:**
  - "Clear ALL auto-created channels across the entire system (no group filter)" → Claude calls `clear_auto_created()` with no args. Payload is `{}` (empty). This is the nuclear option — all auto-created channels system-wide are deleted. Confirm the response says "across all groups" and the deletion count is accurate.
  - "Clear auto-created channels from a group with no auto-created channels" → Backend should return 0 deleted. Response: `"Cleared 0 auto-created channels in 1 groups."` — grammatically odd but functionally correct.
  - "Clear auto-created with empty group_ids list []" → `payload = {"group_ids": []}`. Behavior depends on backend: may be treated as "no groups = no-op" or "empty list = all groups". This is an ambiguous boundary case.
- **Watch for (possible bug areas):**
  - `result.get("deleted", result.get("count", 0))` — the tool tries two different response keys. If the backend returns neither `deleted` nor `count`, the reported deletion count is always `0` even if channels were actually deleted. Operators get no feedback on the blast radius.
  - No confirmation guard before deleting potentially hundreds of channels. The tool fires immediately on any prompt that Claude interprets as "clear auto-created."
  - `scope = f"in {len(group_ids)} groups"` uses `group_ids` before the `if group_ids` check — but `group_ids` could be `None` here, which would raise `TypeError`. Wait: `if group_ids is not None: payload["group_ids"] = group_ids` so `group_ids` is `None` when omitted. The string `f"in {len(group_ids)} groups"` is in the `if group_ids` branch? Re-checking: `scope = f"in {len(group_ids)} groups" if group_ids else "across all groups"`. This is a ternary: `if group_ids` evaluates `None` as falsy AND `[]` as falsy — so an empty list `[]` would say "across all groups" rather than "in 0 groups." This is a misrepresentation bug.

---

#### `find_duplicate_channels()` — READ-ONLY
- **Purpose:** Scan all channels using the normalization engine to identify channels that resolve to the same normalized name (e.g., "ESPN" and "◉ ESPN").
- **Prompt to Claude:** "Find all duplicate channels in my lineup so I can see what needs merging."
- **Expected:** Claude calls `find_duplicate_channels()`. Returns a grouped report of duplicate clusters: normalized name, how many channels share it, and each channel's number/name/id/stream count/group. Up to 30 groups shown.
- **Edge / failure tests:**
  - "Find duplicates when there are none" → Backend returns `{"groups": []}`. Tool returns `"No duplicate channels found."`.
  - "Find duplicates when there are more than 30 groups" → Tool shows the first 30 and appends `"... and N more groups"`. Confirm the count is accurate.
- **Watch for (possible bug areas):**
  - `timeout=120.0` — the find-duplicates scan hits `POST /api/channels/find-duplicates` which may be expensive on large channel lists. If the backend times out (> 120s), the tool raises a `TimeoutError`; the outer `except Exception` catches it and returns `"Error finding duplicates: ..."`. No progress indication.
  - The tool calls `find_duplicates` as a POST with no request body. The endpoint contract in `_endpoint_contracts.py` defines `channels_find_duplicates` with no `request_fields`, which is correct. But the backend implementation must not require a body — verify the POST with empty/null body is accepted.
  - `result.get("groups", [])` — if the backend returns a different envelope (e.g., `{"duplicate_groups": [...]}` after a schema change), the tool returns "No duplicate channels found" silently even when duplicates exist.

---

#### `bulk_merge_duplicate_channels(merges: list[dict])` — DESTRUCTIVE
- **Purpose:** Execute a batch of merge operations (typically produced after `find_duplicate_channels`), each keeping a target channel and absorbing/deleting source channels.
- **Prompt to Claude:** "Merge the duplicates I found: keep the main 'ESPN' and absorb the duplicates; also keep the main 'CNN' and absorb its duplicate." (use `find_duplicate_channels` first to identify which to keep, then name them — Claude looks them up)
- **Expected:** Claude calls `bulk_merge_duplicate_channels(merges=[{"target_channel_id": <main ESPN id>, "source_channel_ids": [<ESPN dup ids>]}, {"target_channel_id": <main CNN id>, "source_channel_ids": [<CNN dup id>]}])`. Returns a summary: `"Bulk merge complete: 2 merged, 0 failed."` followed by per-merge results showing target name, sources absorbed, and total streams.
- **Edge / failure tests:**
  - "Bulk merge where one target channel doesn't exist" → Backend returns a result entry with `success=False` for that merge. The tool shows `"✗ Channel <id>: <error>"`. The count is `1 merged, 1 failed`.
  - "Bulk merge with an empty merges list []" → `result.get("merged", 0)` is 0, `result.get("failed", 0)` is 0. Returns `"Bulk merge complete: 0 merged, 0 failed."` — likely backend returns quickly with an empty result set.
  - "Bulk merge with a malformed merges entry (missing target_channel_id)" → Backend should validate and return a 422. Graceful error via the outer exception handler.
- **Watch for (possible bug areas):**
  - Per-result display uses `r.get('target_name')` but the backend's `BulkMergeItem` model stores `target_channel_id` (not a name). If the backend response doesn't include `target_name` in the results array, that field shows as `None` in the output (e.g., `"✓ 'None': absorbed 2 channels"`). This is a likely response-key drift issue.
  - `timeout=300.0` is correct, but no progress indication for large batches.
  - Unicode checkmarks (`✓` / `✗`) in the output string: confirm these render correctly in Claude Desktop / Claude Code's output pane rather than appearing as escape sequences.
  - Same as `merge_channels`: the `merged` and `failed` counts come from the backend response — if the backend's response schema differs (e.g., uses `success_count`/`error_count`), both default to 0 and the summary is wrong.

---

#### `bulk_commit_channels(operations: list[dict], validate_only: bool = False, continue_on_error: bool = False)` — WRITE
- **Purpose:** Submit a batch of channel operations (create, update, delete, add/remove stream, reorder, group operations) atomically to the backend bulk-commit endpoint.
- **Prompt to Claude:** "Create two new channels atomically: 'Test Channel A' at number 900 and 'Test Channel B' at number 901, both in the 'News' group. Use validate-only mode first." (just name the group — Claude looks it up)
- **Expected:** Claude calls `bulk_commit_channels(operations=[{"type": "createChannel", "tempId": -1, "name": "Test Channel A", "channelNumber": 900, "groupId": <News group id>}, {"type": "createChannel", "tempId": -2, "name": "Test Channel B", "channelNumber": 901, "groupId": <News group id>}], validate_only=True)`. Response: `"Bulk commit SUCCESS: 2 operations submitted. (validate-only mode — no changes applied)"`. Then repeat without `validate_only=True` to actually apply.
- **Edge / failure tests:**
  - "Bulk commit with an invalid operation type 'badType'" → Backend 422; graceful error with the validation detail surfaced (FastAPI validation errors include the offending field path).
  - "Bulk commit with continue_on_error=True where one operation fails" → Partial success; response should show SUCCESS=False but individual failures in `validationIssues`. Confirm `id_mappings` still captures any successful creations' temp-id → real-id maps.
  - "Bulk commit with validate_only=True and invalid operations" → Returns validation issues without applying anything. Confirm `(validate-only mode — no changes applied)` is in the response.
- **Watch for (possible bug areas):**
  - The response reads `result.get("tempIdMap")` and `result.get("groupIdMap")` — per the code comment this was a previous contract drift fix (bd-vtghg Phase 1: old code read `idMappings` which always returned empty). Verify the backend actually returns `tempIdMap` and `groupIdMap` keys in its `BulkCommitResponse`.
  - `validate_only` → `"validateOnly"` and `continue_on_error` → `"continueOnError"` key names in the payload are camelCase. Confirm the backend's `BulkCommitRequest` Pydantic model accepts camelCase (it likely uses `alias` or `model_config` with `populate_by_name=True`). If it doesn't, the validate-only flag is silently ignored and operations are applied.
  - `result.get("success", False)` — if the backend returns `{"success": null}` or omits the key, `success` defaults to `False` and the status always shows `FAILED` even for real successes.
  - Per-operation error details are available at `result["errors"]` per code comment, but they are NOT surfaced in the response. Only aggregate status and `validationIssues` are shown. An operator debugging a failed bulk commit has to look elsewhere.

---

#### `set_logo_from_epg(channel_ids: list[int])` — WRITE
- **Purpose:** For each channel, read its linked EPG entry's `icon_url`, create or reuse a Logo record, then PATCH the channel with the logo_id. Mirrors the UI's bulk "Set Logo from EPG" action.
- **Prompt to Claude:** "Set the logos for the 'ESPN', 'CNN', and 'TSN 5' channels using their EPG entries." (just name them — Claude looks them up)
- **Expected:** Claude calls `set_logo_from_epg(channel_ids=[<ESPN id>, <CNN id>, <TSN 5 id>])`. For each channel: reads the channel, looks up EPG data by `epg_data_id`, POSTs to `/api/channels/logos` to create/find the logo, then PATCHes the channel. Returns summary: `"Set logos from EPG: 3 assigned, 0 skipped (no EPG link), 0 skipped (no icon_url), 0 errors out of 3 requested."`.
- **Edge / failure tests:**
  - "Set logos for a channel with no EPG link — try the 'TSN 5' channel if it has no EPG assigned" → That channel is counted in `skipped_no_epg`. Summary shows 0 assigned, 1 skipped (no EPG link).
  - "Set logos for a channel whose EPG entry has no icon_url" → Counted in `skipped_no_icon`. Summary: 0 assigned, 1 skipped (no icon_url).
  - "Set logos for 'ESPN' and 'CNN' when they share the same EPG icon_url" → Logo cache (`logo_cache`) should reuse the same `logo_id` for the second channel, avoiding a duplicate logo POST. Verify only one logo creation call is made.
  - "Set logos for a channel called 'Does Not Exist'" → `client.get(f"/api/channels/{cid}")` returns 404; exception caught and added to `errors`. Summary: `"0 assigned, ... 1 errors"`.
- **Watch for (possible bug areas):**
  - Uses raw `client.get()` / `client.post()` / `client.patch()` calls (contract-exempt). These bypass the `call_endpoint` contract guard, so any path, body, or query key errors surface only at runtime.
  - The EPG entry is fetched via `client.get(f"/api/epg/data/{epg_data_id}")` — but the endpoint contract for EPG data isn't registered. The field read is `epg_entry.get("icon_url") or epg_entry.get("icon")` — if the Dispatcharr EPG API uses a different field name (e.g., `logo_url`), all channels silently fall into `skipped_no_icon`.
  - The logo POST uses `{"name": channel.get("name") or f"channel-{cid}", "url": icon_url}`. If the logo endpoint returns an existing logo (not a new creation) on a name/url collision, the response must still include `"id"`. If the backend's logo endpoint returns a different envelope on conflict (e.g., 409 with `{"existing_id": ...}`), `logo.get("id")` returns `None` and the channel is counted as an error — meaning a logo already exists but the channel never gets linked.
  - First errors capped at 5 in the display. Large-scale failures (100 channels all failing) show only the first 5.

---

#### `build_channel_lineup(channels: list[dict], group_id: int, provider_id: int = None, market: str = "east")` — WRITE
- **Purpose:** Orchestrated multi-step tool: bulk-create channels from a name/number list, then fuzzy-match and assign streams from a provider, with market preference (east/west).
- **Prompt to Claude:** "Build a lineup in the 'News' group with these channels: Fox News at 360, CNN at 200, MSNBC at 356. Match streams from the 'Acme IPTV' provider in the east market." (just name the group and provider — Claude looks them up)
- **Expected:** Claude calls `build_channel_lineup(channels=[{"name": "Fox News", "number": 360}, {"name": "CNN", "number": 200}, {"name": "MSNBC", "number": 356}], group_id=<News group id>, provider_id=<Acme IPTV id>, market="east")`. Tool bulk-creates all three, fetches the group, fuzzy-matches each to a stream, and assigns them. Summary: `"Lineup built: 3 channels created, 3 streams matched, 0 unmatched."` Confirm via `list_channels` on that group that all three channels exist with streams assigned.
- **Edge / failure tests:**
  - "Build lineup where no streams match" → All channels appear in the Unmatched section. `0 streams matched, 3 unmatched.` Channels still exist with 0 streams — the build is not rolled back on match failure.
  - "Build lineup with market='west'" → Channels are created; the fuzzy score function receives `market='west'`. Confirm the correct market preference is passed to `_score_match` for all channels.
  - "Build lineup with a channel entry missing the 'name' key" → Tool crashes with `KeyError: 'name'` at `ch["name"]` in the operations loop — this is not inside a `try/except`. The outer `except Exception as e` catches it and returns `"Error building channel lineup: 'name'"`.
- **Watch for (possible bug areas):**
  - Step 1 uses raw `client.post("/api/channels/bulk-commit", ...)` (contract-exempt). If the bulk-commit response returns `success=False`, the tool immediately returns an error string and stops — but any channels that were partially created are left in the database with no cleanup.
  - Step 2 fetches channels from the group with `client.get("/api/channels", channel_group=group_id, ...)` — this fetches ALL channels in the group, not just the ones just created. If the group already had existing channels before the lineup build, those pre-existing channels (which already have streams) are included in the list but skipped in the fuzzy-match step (guarded by `len(ch.get("streams", [])) > 0`). The summary's "channels created" count is `len(created_channels)` which includes pre-existing ones — potentially overcounting.
  - `from tools.streams import _fuzzy_helpers` is imported at runtime (inside the function body), not at module load. If `tools/streams.py` changes the `_fuzzy_helpers` dict structure, the error surfaces only when this tool is called, not at startup.
  - `provider_id` is passed as `params["m3u_account"] = provider_id` (corrected per bd-vtghg comment). Verify this is the correct Dispatcharr query param for filtering streams by provider — if the backend uses `m3u_account_id` instead, the provider filter is silently ignored and all providers' streams are searched.
  - The `build_channel_lineup` output cap is 30 matches and 30 unmatched shown. For a 100-channel lineup, 70+ unmatched channels may be invisible in the response.

---

## Dedup (Pending Channel Merges)

#### `list_pending_channel_merges(group_id: int = None, status: str = None, page: int = None, page_size: int = None)` — READ-ONLY
- **Purpose:** List pending (or resolved) channel-merge candidates from the dedup queue, with optional filtering by group and status, paginated.
- **Prompt to Claude:** "Show me all the pending channel merge suggestions waiting for review."
- **Expected:** Claude calls `list_pending_channel_merges()`. Default `status='pending'` is applied. Returns a dict: `{merges: [...], total, page, page_size, total_pages}`. Each merge row contains `id`, `stream_name`, `group_id`, `candidate_channel_id`, `confidence`, `status`, `created_at`, `trigger_context`, etc.
- **Edge / failure tests:**
  - "List merge history — show me dismissed merges from the 'USA | Sports' group" → Claude calls `list_pending_channel_merges(group_id=<USA | Sports id>, status='dismissed')`. Confirm `status='dismissed'` is sent and results are filtered to that group.
  - "List merged (accepted) history" → Claude calls `list_pending_channel_merges(status='merged')`. Confirm terminal-state rows are returned.
  - "List pending merges with status='invalid'" → Backend returns 400: `"status must be one of ['pending', 'merged', 'dismissed']; got 'invalid'"`. Since this tool re-raises exceptions (no outer try/except), the exception propagates to Claude as a tool call error. Confirm Claude surfaces a useful message to the operator.
  - "List pending merges page 2 with page_size 10" → Claude calls `list_pending_channel_merges(page=2, page_size=10)`. Confirm the correct 10 rows from offset 10 are returned and `total_pages` is correct.
  - "List pending merges when the queue is empty" → Returns `{merges: [], total: 0, page: 1, page_size: 50, total_pages: 0}`.
  - "List with page_size=201 (over the max of 200)" → Backend returns 400 (`page_size must be between 1 and 200`). Tool re-raises; Claude sees a tool error.
- **Watch for (possible bug areas):**
  - Unlike the channels tools, this tool does NOT have a `try/except` wrapper — it calls `client.call_endpoint(...)` and re-raises any exception directly. Backend 4xx errors (invalid status, out-of-range page_size) propagate as unhandled exceptions. Claude Desktop may show these as tool call failures with a raw error string rather than a graceful message.
  - The return type is `dict` (not `str`). Claude must render the raw JSON dict for the operator. Whether Claude formats this readably or dumps it as a raw Python dict depends on the model's rendering behavior. Operators may find a raw `{merges: [...], ...}` dict hard to read compared to the string-formatted outputs of other tools.
  - `status` defaults to `"pending"` in the query build even when `status=None` is passed (the `else` branch sets `query["status"] = "pending"`). This means `list_pending_channel_merges(status=None)` is NOT the same as "no filter" — it always defaults to pending. Operators cannot list all statuses at once.

---

#### `accept_channel_merge(merge_id: int)` — DESTRUCTIVE
- **Purpose:** Confirm a pending dedup merge: adds the stream to the candidate channel in Dispatcharr, flips the pending row to `merged`, and writes the full audit journal entry.
- **Prompt to Claude:** "Accept the pending merge for the 'ESPN' channel — confirm it should be merged into that existing channel." (use `list_pending_channel_merges` first to see the pending merge, then refer to it by the channel name — Claude finds the merge id via the list)
- **Expected:** Claude calls `accept_channel_merge(merge_id=<resolved id>)`. Returns a dict: `{merged_into_channel_id, journal_entry_id, source_stream_id, confidence, status: 'merged'}`. Confirm via `list_pending_channel_merges(status='merged')` that the row now appears as merged.
- **Edge / failure tests:**
  - "Accept the same pending merge for 'ESPN' again (idempotency)" → Tool returns the same outcome envelope as the first call. Response includes `status: 'merged'` and the original `journal_entry_id`. No error.
  - "Accept a merge for 'ESPN' where the target channel was deleted from Dispatcharr" → Backend returns 404; tool catches it and returns `{"error": {"code": "TARGET_NOT_FOUND", "message": "..."}}`. Claude should then prompt the operator to dismiss that pending merge to clean up the stale row.
  - "Accept a merge that has already been dismissed" → Backend returns 409; tool catches it and returns `{"error": {"code": "INVALID_STATE", "message": "..."}}`. Claude should NOT retry.
  - "Accept a merge that doesn't exist" → Backend returns 404 for the pending row (not the target channel). `_http_status_code` returns 404; tool returns `TARGET_NOT_FOUND` error envelope. Confirm the error message makes sense in this context — the message says "target channel no longer exists" but the actual issue is the merge row itself doesn't exist.
  - "Accept a merge where the stream name matches multiple Dispatcharr streams" → Backend logs a WARN and records the operator decision in the audit trail without adding the stream to the channel. Return envelope still shows `status: 'merged'`. Operator must manually assign the stream.
- **Watch for (possible bug areas):**
  - The 404 error envelope message says "Pending merge id=N: target channel no longer exists in Dispatcharr" — but a 404 from `accept_channel_merge` could also mean the `pending_merges` row itself doesn't exist (the row 404 from the backend). The error message conflates two different 404 causes; an operator acting on a nonexistent merge gets advice to call `dismiss_channel_merge` which will itself 404. This could confuse the operator.
  - The return type is `dict` (not `str`) — same readability concern as `list_pending_channel_merges`.
  - `result["status"] = "merged"` is injected by the tool only if `"status" not in result` — if the backend starts returning `status` in the AcceptOutcome, the tool's injected value is skipped, which is correct behavior. But the injected `status='merged'` is added to the dict after the fact, meaning Claude sees a mutated copy, not the original backend response. This is fine but fragile if the tool is refactored.
  - 5xx errors from the backend are re-raised directly. Claude Desktop sees a raw exception, not a structured error envelope. The operator gets no actionable advice for server errors.

---

#### `dismiss_channel_merge(merge_id: int)` — DESTRUCTIVE
- **Purpose:** Reject a pending dedup candidate — records the dismissal in the audit journal but makes no Dispatcharr call. Also serves as the recovery action when `accept_channel_merge` returns `TARGET_NOT_FOUND`.
- **Prompt to Claude:** "Dismiss the pending merge suggestion for the 'ESPN' channel — that suggested merge is wrong, don't merge those channels." (use `list_pending_channel_merges` first to see the pending merge, then refer to it by channel name — Claude finds the merge id via the list)
- **Expected:** Claude calls `dismiss_channel_merge(merge_id=<resolved id>)`. Returns `{journal_entry_id, status: 'dismissed'}`. Confirm via `list_pending_channel_merges(status='dismissed')` that the row is now dismissed.
- **Edge / failure tests:**
  - "Dismiss the same pending merge for 'ESPN' again (idempotency)" → Returns the original outcome envelope with the same `journal_entry_id`. No error.
  - "Dismiss a merge for 'CNN' that has already been accepted (cross-state)" → Backend returns 409; tool catches it and returns `{"error": {"code": "INVALID_STATE", "message": "Pending merge id=<id> is already in a terminal state that does not allow dismissal (e.g. already merged). Do not retry this operation."}}`. Confirm Claude does NOT retry.
  - "Dismiss a pending merge that doesn't exist" → Backend returns 404 for the pending row. `_http_status_code` returns 404 — but the `dismiss_channel_merge` tool only catches 409! A 404 falls through to the re-raise path. Claude sees a raw exception, not a structured error envelope. This is a gap: the tool handles 409 gracefully but lets 404 propagate.
  - "Use dismiss as recovery after accept returns TARGET_NOT_FOUND for the 'ESPN' merge" → Claude calls `dismiss_channel_merge` for that merge after receiving the TARGET_NOT_FOUND envelope. Should succeed with `status: 'dismissed'`, cleaning up the stale row.
- **Watch for (possible bug areas):**
  - Critical gap: the tool handles 409 (INVALID_STATE) gracefully with a structured error envelope, but does NOT handle 404 (row not found). A 404 from a non-existent merge re-raises as an unhandled exception. This is inconsistent with `accept_channel_merge` which does handle 404 (even if the message is ambiguous). An operator entering the wrong merge reference gets a raw exception from `dismiss` but a structured error from `accept`.
  - The return type is `dict` (not `str`) — same readability concern. The raw `{journal_entry_id: N, status: 'dismissed'}` dict may not render helpfully.
  - `status: 'dismissed'` injection follows the same pattern as `accept` — correct but fragile.
  - 5xx errors re-raise directly with no operator-facing guidance.

---

## Streams

#### `list_streams(group: str | None = None, provider_id: int | None = None, search: str | None = None, page: int = 1, page_size: int = 50)` — READ-ONLY

- **Purpose:** List streams with optional filtering by group name, M3U provider ID, or name search; results are paginated.
- **Prompt to Claude:** "Show me all streams in the Sports group, page 1."
- **Expected:** Claude calls `list_streams(group="Sports")`. Response lists stream names, IDs, group labels, and provider names. Header reads `Showing N of TOTAL streams (page 1):`. Underlying call sends `channel_group_name=Sports` to `GET /api/streams` (not a bare `group` field — that drift was the bd-vtghg bug).
- **Edge / failure tests:**
  - "List streams for a group name that doesn't exist, like 'ZZZ_NoSuchGroup'" → ECM returns an empty page; Claude should say "No streams found." not raise an error.
  - "List streams with page_size=200" → Code clamps to `min(page_size, 100)` = 100; verify Claude does not report 200 per page. Try `page_size=0` — no clamp guards against zero; backend may 400.
- **Watch for (possible bug areas):** The `provider_id` MCP parameter is passed to `_stream_query` as `m3u_account=provider_id` — this name translation is correct post-drift-fix, but if the backend API changes the query param name the tool silently filters nothing. `page_size` clamped to 100 but not guarded at lower bound (0 or negative). `group` doc says "group name" but internally maps to `channel_group_name` — if Claude passes a group ID instead of a name the backend silently returns nothing. Response format branches on `dict` vs bare list; if the backend returns a bare list the `total` will equal `len(streams)` even on a partial page (pagination silently breaks).

---

#### `get_stream_health()` — READ-ONLY

- **Purpose:** Get an overview of stream health from the most recent probe results via `GET /api/stream-stats/summary`.
- **Prompt to Claude:** "What's the current health status of all streams?"
- **Expected:** Claude calls `get_stream_health()`. Response prints each key in the summary dict with a title-cased label (e.g., "Total Streams: 843", "Healthy Count: 712", "Failed Count: 131"). If no probe has ever run, Claude returns "No stream health data available. Run a probe first."
- **Edge / failure tests:**
  - "Ask for stream health when ECM has never been probed" → backend returns empty dict or `{}`; Claude should return the "No stream health data available" message, not a blank "Stream Health Summary:" with no entries.
  - "Ask for stream health when ECM backend is unreachable" → `get` raises; Claude returns "Error getting stream health: ..." with the HTTP detail.
- **Watch for (possible bug areas):** `if not summary:` is falsy on an empty dict `{}` — correct. But if the backend returns `{"total": 0, "healthy": 0}` the check passes and we print a valid summary with zeros — correct behavior, but verify the keys actually match what `StreamProber.get_stats_summary()` returns. No field mapping exists: every key is title-cased as-is, so snake_case keys like `total_streams` become "Total Streams" while a key like `p95_latency_ms` becomes "P95 Latency Ms" — aesthetically odd but functionally fine.

---

#### `probe_streams()` — WRITE

- **Purpose:** Start a background probe of ALL streams; uses 300 s timeout on the POST.
- **Prompt to Claude:** "Start a health probe on all streams now."
- **Expected:** Claude calls `probe_streams()`. Response is "Stream probe started. \<backend message\>". The backend call is `POST /api/stream-stats/probe/all` — a potentially long-running background job; the tool waits up to 300 s for the HTTP response (not for the probe to complete). Use `get_probe_progress()` to follow up.
- **Edge / failure tests:**
  - "Start a probe when one is already running" → backend may return a 409 or a message saying probe is already in progress; Claude should surface the backend message, not crash.
  - "Start probe then immediately ask for progress" → tests the probe-started → progress polling handoff; `get_probe_progress()` should show `in_progress: true`.
- **Watch for (possible bug areas):** The tool does `result.get("message", "Check progress in ECM.")` — if the backend returns a bare string or a list (not a dict), `result.get` raises `AttributeError` which is caught by the outer `except` and returned as an error string. The 300 s `timeout` is passed to `call_endpoint` which passes it to `post()` — correct, but if the backend's async job returns immediately (202 Accepted pattern) with no body, the tool returns "Stream probe started. " with a trailing space — cosmetic only.

---

#### `get_probe_progress()` — READ-ONLY

- **Purpose:** Check the progress of an ongoing stream probe via `GET /api/stream-stats/probe/progress`.
- **Prompt to Claude:** "How far along is the stream probe?"
- **Expected:** Claude calls `get_probe_progress()`. If a probe is running: response shows `Probe in progress: N/TOTAL (PCT%)` plus success/failed/skipped counts and the stream currently being probed. If no probe is running: "No probe is currently running."
- **Edge / failure tests:**
  - "Check probe progress when no probe has ever been started" → backend returns `{"in_progress": false, ...}`; Claude should return "No probe is currently running." not an error.
  - "Check probe progress when `total` is 0" → division-by-zero guard: `pct = (current / total * 100) if total else 0` — verify 0% is shown, not a ZeroDivisionError.
- **Watch for (possible bug areas):** `p.get("in_progress")` — if the backend omits the key entirely, this is falsy and Claude says "No probe is currently running" even if one is. If `current_stream` is an int (stream ID) instead of a name string, it renders fine but is less readable. No polling loop — the operator must re-issue the prompt repeatedly to track progress; Claude may not remind the user to do so.

---

#### `probe_single_stream(stream_id: int)` — WRITE

- **Purpose:** Probe a single stream by ID to check its health; calls `POST /api/stream-stats/probe/{stream_id}`.
- **Prompt to Claude:** "Check the health of the 'US: ESPN FHD' stream." (just name it — Claude looks it up)
- **Expected:** Claude calls `probe_single_stream(stream_id=<resolved id>)`. Response: "Stream <id> probe complete. Status: \<status\>". The status comes from `result.get("status", result.get("probe_status", "unknown"))` — expect values like "success", "failed", or "timeout".
- **Edge / failure tests:**
  - "Probe a stream called 'Does Not Exist Stream'" → backend should 404; `_http_error` converts it to a RuntimeError; Claude returns "Error probing stream <id>: POST /api/stream-stats/probe/<id> -> HTTP 404 Not Found".
  - "Probe a stream with ID -1 or 0" → no client-side guard; backend validation is the only gate.
- **Watch for (possible bug areas):** Status resolution is a nested `.get` chain; if the response is `{"probe_status": "ok"}` neither "status" nor "probe_status" matches "ok" (it does match "probe_status" — that's correct). But if backend returns `{"result": "healthy"}` the tool reports "unknown". No timeout override — uses DEFAULT_TIMEOUT (30 s) which may be too short for a slow stream that takes 45 s to respond/timeout; the tool may return an MCP-level timeout error while the probe is still running on the backend.

---

#### `get_struck_out_streams()` — READ-ONLY

- **Purpose:** List streams that have exceeded the consecutive-failure strike threshold, with their channel assignments.
- **Prompt to Claude:** "Which streams have been struck out due to probe failures?"
- **Expected:** Claude calls `get_struck_out_streams()`. Response shows struck stream names, IDs, failure counts, and the channels they belong to. If strike detection is disabled (threshold ≤ 0), returns "Strike detection is disabled." If no streams are struck out, returns the threshold and a clean message.
- **Edge / failure tests:**
  - "Ask for struck-out streams when strike detection is disabled in ECM settings" → backend returns `{"enabled": false}`; Claude should say "Strike detection is disabled." not an empty list.
  - "Ask for struck-out streams when more than 30 are struck" → code hard-caps display at 30 with a "... and N more" line; verify the operator knows the count is truncated.
- **Watch for (possible bug areas):** Hard cap of 30 results displayed — silently truncates without giving the operator a way to page through the rest via MCP. Field name fallbacks: `stream_name` vs `name`, `stream_id` vs `id`, `consecutive_failures` vs `strike_count` — if the backend changes one of these the tool silently shows "Unknown" or "?" without erroring. `threshold` defaults to `"?"` if missing from the response dict — a string `"?"` in arithmetic comparisons elsewhere could cause issues, but it's only used as a display label here.

---

#### `cleanup_struck_out_streams(delete_empty_channels: bool = False)` — DESTRUCTIVE

- **Purpose:** Remove all struck-out streams from their channels in one bulk operation; optionally delete channels left with no streams after removal.
- **Prompt to Claude:** "Clean up all struck-out streams and delete any channels that become empty."
- **Expected:** Claude calls `cleanup_struck_out_streams(delete_empty_channels=True)`. Response: count of struck streams removed, number of stream-channel links removed, and (if `delete_empty_channels=True`) list of deleted channels. This calls `GET /api/stream-stats/struck-out`, then `POST /api/stream-stats/struck-out/remove`, then optionally `GET /api/channels/{id}` and `DELETE /api/channels/{id}` per affected channel.
- **Edge / failure tests:**
  - "Clean up struck-out streams when there are none" → `if not streams:` returns early with "No struck-out streams to clean up"; verify this is a clean no-op.
  - "Clean up with `delete_empty_channels=True` when channel GET fails mid-loop" → inner `except Exception: pass` silently skips that channel; the operator never knows a channel deletion was attempted and failed. This is the most dangerous silent failure mode in this tool.
  - "Run cleanup twice in a row (idempotency)" → second call should find no struck streams and return "No struck-out streams to clean up."
- **Watch for (possible bug areas):** No confirmation prompt before destructive action — the tool immediately removes streams and deletes channels without asking. Inner `except Exception: pass` for channel deletion swallows errors silently (the only note is the channel is absent from `deleted_channels`). If `POST /api/stream-stats/struck-out/remove` succeeds but a subsequent `DELETE /api/channels/{id}` fails, the operator gets partial results with no error logged to the MCP response. `removed_count` comes from `result.get("removed_from_channels", 0)` — if the backend uses a different key name, this silently reports 0. The unassigned count is computed from the original `streams` list, not the post-removal state — it's an accurate pre-removal count but is labeled in a way that implies it is a post-operation state.

---

#### `bulk_remove_streams(channel_id: int, stream_ids: list[int])` — DESTRUCTIVE

- **Purpose:** Remove multiple streams from a specific channel in one operation by PATCHing the channel's stream list.
- **Prompt to Claude:** "Remove the streams 'US: ESPN FHD', 'US: ESPN HD', and 'US: ESPN East' from the 'ESPN' channel." (just name them — Claude looks them up)
- **Expected:** Claude calls `bulk_remove_streams(channel_id=<ESPN id>, stream_ids=[<id1>, <id2>, <id3>])`. The tool fetches the current stream list via `GET /api/channels/<id>`, filters out the specified IDs, and PATCHes back the remaining list. Response: "Removed N streams from channel <id>. Remaining: M streams."
- **Edge / failure tests:**
  - "Remove streams that are not actually in the 'ESPN' channel" → `actually_removed == 0`; Claude returns "None of the specified streams were in channel <id>." — clean no-op.
  - "Remove all streams from the 'ESPN' channel (empty the stream list)" → `filtered = []`; the PATCH sends `{"streams": []}` — verify the backend accepts an empty list and doesn't 400. This is a potentially dangerous operation that leaves a channelless channel silently.
  - "Pass an empty `stream_ids` list" → `remove_set = set()`; `filtered` equals `current_streams`; `actually_removed = 0`; returns "None of the specified streams were in channel." — correct no-op behavior.
- **Watch for (possible bug areas):** `current_streams` from `channels_get` may be a list of stream objects (dicts) rather than a list of IDs — if the backend returns `[{"id": 201, "name": "..."}, ...]` instead of `[201, ...]`, then `sid not in remove_set` compares dicts to ints and `actually_removed` will always be 0. This is the highest-risk structural bug in this tool. Also: `now_count` falls back to `len(filtered)` if the PATCH response doesn't include `streams` — the reported count may diverge from backend reality if the PATCH succeeded partially.

---

#### `cancel_probe()` — WRITE

- **Purpose:** Cancel the currently running stream probe via `POST /api/stream-stats/probe/cancel`.
- **Prompt to Claude:** "Cancel the stream probe that's running right now."
- **Expected:** Claude calls `cancel_probe()`. Response: "Probe cancelled. \<backend message\>". If no probe is running, the backend may still return 200 with a message like "no probe running" — Claude should surface it.
- **Edge / failure tests:**
  - "Cancel probe when no probe is running" → backend behavior determines the response; Claude should not error — it should relay whatever message the backend sends.
  - "Cancel probe immediately after starting one" → tests that the cancel actually stops the probe; follow up with `get_probe_progress()` to confirm `in_progress: false`.
- **Watch for (possible bug areas):** `result.get("message", "")` — if backend returns a bare string (not dict), this raises `AttributeError` caught by outer except. The `.rstrip()` on the return value strips trailing whitespace — harmless. No guard for calling cancel when no probe is active; outcome depends entirely on backend behavior which is not validated here.

---

#### `get_probe_results()` — READ-ONLY

- **Purpose:** Get results from the most recent completed probe run via `GET /api/stream-stats/probe/results`.
- **Prompt to Claude:** "Show me the results from the last stream probe."
- **Expected:** Claude calls `get_probe_results()`. If results are a dict: prints key/value pairs as titled labels (same pattern as `get_stream_health`). If results are a list of per-stream records: prints total count, healthy count, and failed count.
- **Edge / failure tests:**
  - "Ask for probe results when no probe has ever completed" → backend returns empty; `if not result:` returns "No probe results available." — verify this check works for `{}`, `[]`, and `None`.
  - "Ask for probe results when the response is a large list (1000+ streams)" → code counts healthy/failed but returns only the summary, not per-stream details. The operator gets aggregate counts but no way to drill in via MCP alone.
- **Watch for (possible bug areas):** `if not result:` — `{}` and `[]` are both falsy, so empty results return the "No probe results available" message correctly. But `{"results": []}` is truthy and would enter the dict branch, print "Latest Probe Results:" and one line "Results: []" — potentially confusing. The list branch counts by `status == "success"` and `status == "failed"` — any other status value (e.g., "timeout", "skipped") is silently dropped from both counts, making the totals not add up.

---

#### `get_streams_for_channel(channel_id: int)` — READ-ONLY

- **Purpose:** Get detailed stream information for a specific channel including names, groups, and providers.
- **Prompt to Claude:** "List all streams assigned to the 'ESPN' channel." (just name it — Claude looks it up)
- **Expected:** Claude calls `get_streams_for_channel(channel_id=<resolved id>)`. Response lists numbered streams with name, ID, group, and provider. Hits `GET /api/channels/<id>/streams`.
- **Edge / failure tests:**
  - "Get streams for a channel with no streams assigned — try 'Test Channel A' if it's empty" → `if not streams:` returns "Channel <id> has no streams assigned." — clean.
  - "Get streams for a channel with a negative or zero ID" → no client-side guard; backend 404 is surfaced via `_http_error`.
  - "Get streams for a channel with 50+ streams" → no truncation cap; all streams are listed. Test with a channel known to have many streams to verify response length doesn't hit MCP output limits.
- **Watch for (possible bug areas):** Response resolution: `result if isinstance(result, list) else result.get("streams", result.get("results", []))` — if backend returns `{"data": [...]}` (neither "streams" nor "results"), the code falls back to the dict itself, which is not a list. The `for i, s in enumerate(streams, 1)` would iterate over dict keys — e.g., printing `1. streams (id=?)` — a silent data-shape bug. `provider` falls back to `m3u_account` field name — if neither is present, provider info is blank with no error.

---

#### `search_streams(query: str, provider_id: int | None = None, limit: int = 25)` — READ-ONLY

- **Purpose:** Search for streams by name across all providers; thin wrapper over `list_streams` using the same `streams_list` endpoint.
- **Prompt to Claude:** "Search for streams matching 'ESPN' and show me the top 10."
- **Expected:** Claude calls `search_streams(query="ESPN", limit=10)`. Response: "Found N streams matching 'ESPN' (showing 10):" with name, ID, group, and provider. If more results exist beyond the limit, appends "... and N more results".
- **Edge / failure tests:**
  - "Search for a name that returns zero results, like 'ZZZNOTASTREAM'" → returns "No streams found matching 'ZZZNOTASTREAM'." without error.
  - "Search with `limit=200`" → code clamps to `min(limit, 100)` for the page_size, but then slices `streams[:limit]` — if `limit=200` and `page_size` is clamped to 100, at most 100 streams are returned from the backend but the code tries to slice 200. The "... and N more" line subtracts `total - limit` where `limit=200` and `total` might be 150 — result is negative. This is a latent display bug.
- **Watch for (possible bug areas):** `limit` is applied twice: once as `page_size` (clamped to 100) and once as a slice index on the returned list. If `limit > 100`, `streams[:limit]` never truncates (there are at most 100) but the "and N more" math uses the unclipped `limit`. The `total` count from the backend (which may be much larger than 100) minus `limit=200` yields a negative "more" count. No minimum bound on `limit` — `limit=0` would show nothing but report "Found N streams...".

---

#### `get_streams_by_ids(stream_ids: list[int])` — READ-ONLY

- **Purpose:** Fetch detailed stream information for a specific list of stream IDs via `POST /api/streams/by-ids`.
- **Prompt to Claude:** "Give me details on the streams 'US: ESPN FHD', 'US: CNN HD', and 'CA: TSN 5 HD'." (just name them — Claude looks them up)
- **Expected:** Claude calls `get_streams_by_ids(stream_ids=[<id1>, <id2>, <id3>])`. Response: "Found N of 3 requested streams:" with name, ID, group, and provider per stream.
- **Edge / failure tests:**
  - "Request stream details where one name doesn't match anything, like 'US: ESPN FHD' and 'DOES NOT EXIST STREAM'" → backend returns only the found streams; Claude reports "Found 1 of 2 requested streams" — the operator can see which are missing by count delta.
  - "Pass an empty list: `[]`" → POST body is `{"stream_ids": []}` — backend behavior TBD; if it returns an empty list, Claude says "No streams found for the given 0 IDs." If the backend errors on empty input, Claude surfaces the HTTP error.
  - "Pass a list with duplicate stream names resolving to the same ID" → backend deduplicates or not; the "Found N of M" count may look odd.
- **Watch for (possible bug areas):** Same response-shape ambiguity as `get_streams_for_channel`: `result.get("streams", result.get("results", []))` — if backend returns `{"data": [...]}` the code iterates over dict keys. `stream_ids` is typed `list[int]` — if Claude passes strings (e.g., `["101", "205"]`), FastMCP may coerce or may reject; test explicitly.

---

#### `probe_bulk_streams(stream_ids: list[int])` — WRITE

- **Purpose:** Probe multiple specific streams at once and return a health results summary; 300 s timeout.
- **Prompt to Claude:** "Probe these three streams for health: 'US: ESPN FHD', 'US: CNN HD', and 'CA: TSN 5 HD'." (just name them — Claude looks them up)
- **Expected:** Claude calls `probe_bulk_streams(stream_ids=[<id1>, <id2>, <id3>])`. Response: "Bulk probe completed for 3 streams: Success: N, Failed: M" plus a list of failed stream names/errors (up to 20 shown).
- **Edge / failure tests:**
  - "Probe bulk streams with a list containing a stream name that doesn't exist" → backend should probe what it can; the non-existent ID should appear in "failed" with an appropriate error.
  - "Probe bulk streams with an empty list `[]`" → POST body `{"stream_ids": []}` — backend behavior determines response; if backend returns `{}`, Claude falls to the else branch and prints "Bulk probe started for 0 streams. {}"  — the raw `{}` in the output is confusing.
  - "Probe a large list of 500+ streams" → verify the 300 s timeout is sufficient and the results list truncation (20 shown + "and N more") works correctly.
- **Watch for (possible bug areas):** If the backend returns a bare string instead of a dict, `isinstance(result, dict)` is False and Claude prints "Bulk probe started for N streams. \<string\>". This suggests the tool may have been designed for an async-start pattern but the backend returns synchronous results — confirm which pattern the backend actually uses. Failed stream display caps at 20 but uses `results_list` which may include successful streams — the filter `r.get("status") == "failed"` correctly narrows it, but if the backend uses "error" instead of "failed" as the status value, no failures are shown.

---

#### `bulk_search_streams(queries: list[str], provider_id: int | None = None, limit_per_query: int = 10)` — READ-ONLY

- **Purpose:** Search for multiple stream names in one call, running one search per query term.
- **Prompt to Claude:** "Search for streams matching 'ESPN', 'CNN', and 'Fox News' all at once."
- **Expected:** Claude calls `bulk_search_streams(queries=["ESPN", "CNN", "Fox News"])`. Response is concatenated search results per term: `Results for "ESPN" (N found):` followed by stream name + ID lines, then the next term. Terms with no matches show `No results for "ESPN"`.
- **Edge / failure tests:**
  - "Search with an empty queries list `[]`" → the for loop doesn't execute; Claude returns an empty string (empty `"\n".join([])`). MCP may display this as a blank response — unclear if that surfaces as an error or silence.
  - "Search with 50 query terms" → makes 50 sequential backend calls; verify no timeout and that all results render. The operator should be warned this is a chatty operation.
  - "Search with a query that matches 200+ streams but `limit_per_query=5`" → page_size clamp: `min(5, 100)` = 5; backend returns 5; the `len(streams)` in the header reports 5 but does not tell the operator there are more.
- **Watch for (possible bug areas):** One backend GET call per query — sequential, not parallel. For large query lists this is slow. If any single query raises an exception, the outer `except` catches it and returns an error for the entire batch — individual per-query failures are not isolated. Unlike `search_streams`, there is no "... and N more" message when results are truncated by `limit_per_query`, so the operator doesn't know how many total results exist.

---

#### `fuzzy_match_stream(name: str, provider_id: int | None = None, market: str = "east")` — READ-ONLY

- **Purpose:** Search for a stream using multiple auto-generated name variants (with TV, HD, East/West suffixes, abbreviation expansion, etc.) and score matches.
- **Prompt to Claude:** "Find the best stream match for 'ESPN2' using fuzzy matching."
- **Expected:** Claude calls `fuzzy_match_stream(name="ESPN2")`. The tool generates variants via `_generate_variants("ESPN2")` (including "ESPN 2" from the `_ABBREVIATIONS` map), searches for each, deduplicates, scores all results using `_score_match`, and returns: `Best match for "ESPN2": ESPN 2 HD East (id=507)` plus up to 10 alternatives.
- **Edge / failure tests:**
  - "Fuzzy match a stream name that has no possible matches: 'ZZZNOMATCH'" → `_fuzzy_search` returns `(None, [])` because `scored[0][0] <= 0`; Claude shows the "No match found" message with the variant list attempted.
  - "Fuzzy match 'MC - Blues'" → prefix expansion should generate "Music Choice Blues" as a variant; verify the expansion fires and produces a scored match.
  - "Fuzzy match with `market='west'`" → west-preference scoring (`+10 WEST`, `-5 EAST`) should rank WEST-suffixed streams higher.
- **Watch for (possible bug areas):** `_generate_variants` makes up to ~10 backend calls per fuzzy search (one per variant), all sequential. For an operator fuzzy-matching 20 channels, this is 200 sequential HTTP calls. The "no match" branch in `fuzzy_match_stream` constructs a simplified variant list using `upper` — this is different from what `_generate_variants` actually tried (which is more extensive), so the operator sees an incomplete picture of what was searched. Score threshold is `> 0` (strictly positive) — a score of 0 produces "No match found" even if a partial match exists.

---

#### `match_streams_to_channels(group_id: int, provider_id: int | None = None, market: str = "east")` — WRITE

- **Purpose:** Auto-match streams to all unassigned (0-stream) channels in a channel group using fuzzy name matching; assigns the best match to each unassigned channel.
- **Prompt to Claude:** "Auto-match streams to all unassigned channels in the 'USA | Sports' group." (just name the group — Claude looks it up)
- **Expected:** Claude calls `match_streams_to_channels(group_id=<USA | Sports id>)`. The tool paginates through all channels in the group (`page_size=500`), filters to those with 0 streams, fuzzy-matches each channel name, and POSTs `add-stream` for each hit. Response: "Matched N of M unassigned channels in the 'USA | Sports' group:" followed by `#NUM ChannelName → StreamName (id=ID)` for each match, then an "Unmatched" section for failures.
- **Edge / failure tests:**
  - "Run on a group where all channels already have streams" → `if not unassigned:` returns "All N channels in that group already have streams assigned." — clean.
  - "Run on a group name that doesn't exist" → `channels_list` returns empty; `if not all_channels:` returns "No channels found in that group." — clean.
  - "Run on a group with 200 unassigned channels" → makes up to 2000+ sequential backend calls (10 variants × 200 channels + 200 assign calls). Test for timeout and partial-failure behavior when some `add-stream` calls fail mid-batch.
- **Watch for (possible bug areas):** Extremely chatty: each channel triggers `_fuzzy_search` which makes one GET per variant (up to ~10). With 100 channels, this is ~1000 GET calls + 100 POST calls before any output is produced. No progress reporting mid-operation — the operator sees nothing until all channels are processed or it errors. Matched display hard-caps at 50 with truncation — but all assignments are made; only display is capped. If an `add-stream` POST for one channel fails, the exception is caught per-channel and the channel goes to `unmatched` with `"assign failed: ..."` — good isolation. The pagination loop correctly handles the `next` cursor to handle groups larger than 500 channels.

---
## Profiles

---

#### `list_channel_profiles()` — READ-ONLY

- **Purpose:** List all channel profiles (configuration presets for channels) via `GET /api/channel-profiles`.
- **Prompt to Claude:** "What channel profiles are configured in ECM?"
- **Expected:** Claude calls `list_channel_profiles()`. Response: "Found N channel profiles:" with each profile's name, ID, and how many channels are assigned to it. If none: "No channel profiles configured."
- **Edge / failure tests:**
  - "List channel profiles when none are configured" → backend returns `[]` or `null`; `if not profiles:` returns "No channel profiles configured."
  - "List channel profiles when the backend is unreachable" → exception surfaced as "Error listing channel profiles: ..."
- **Watch for (possible bug areas):** `channel_count = len(p.get("channels", []))` — if `p.get("channels")` returns an integer (a count field rather than a list), `len()` raises `TypeError` that is caught and returned as an error string. If the backend paginates this endpoint (unlikely for profiles but possible), the tool has no pagination — it only gets the first page and silently shows a partial list. No indication of what a profile *does* (no description, settings, or config shown) — the operator can only see names and channel counts.

---

#### `list_stream_profiles()` — READ-ONLY

- **Purpose:** List all stream profiles (FFmpeg/transcoding presets) via `GET /api/stream-profiles`.
- **Prompt to Claude:** "Show me all stream transcoding profiles."
- **Expected:** Claude calls `list_stream_profiles()`. Response: "Found N stream profiles:" with each profile's name, ID, active/inactive status, and whether it is locked. If none: "No stream profiles configured."
- **Edge / failure tests:**
  - "List stream profiles when none exist" → "No stream profiles configured."
  - "List when a profile has no `is_active` field" → `p.get("is_active")` defaults to falsy; profile is labeled "inactive" even if the concept doesn't apply.
- **Watch for (possible bug areas):** `active = "active" if p.get("is_active") else "inactive"` — missing key is treated as "inactive" which may be misleading. `locked = " (locked)" if p.get("locked") else ""` — same issue. No pagination support. No profile details shown (no codec settings, bitrate, resolution) — the list is useful for finding an ID to pass to other tools but gives no configuration visibility.

---

#### `apply_profile_to_channels(profile_id: int, channel_ids: list[int])` — WRITE

- **Purpose:** Bulk-assign a channel profile to multiple channels via `PATCH /api/channel-profiles/{profile_id}/channels/bulk-update`.
- **Prompt to Claude:** "Apply the 'Default' channel profile to the channels named 'CNN HD', 'Fox News', and 'MSNBC'." (just name the profile and channels — Claude looks up the IDs)
- **Expected:** Claude calls `apply_profile_to_channels(profile_id=<resolved>, channel_ids=[<resolved>, <resolved>, <resolved>])`. The tool PATCHes the profile, then reads back the profile list to confirm the new channel count. Response: "Profile 'Default' applied to 3 channels. Profile now has N channels."
- **Edge / failure tests:**
  - "Apply a profile called 'Nonexistent Profile' to some channels" → Claude resolves the name via `list_channel_profiles`, finds no match, and reports the profile could not be found before attempting the PATCH.
  - "Apply the 'Default' profile to an empty channel list" → PATCH body `{"channel_ids": []}` — may be a bulk-clear operation on the backend. No guard in the tool; operator should be warned.
  - "Apply the 'Default' profile to a channel that doesn't exist" → backend may silently skip the bad ID or 400; verify which behavior occurs.
- **Watch for (possible bug areas):** The `enabled` key is in the endpoint's declared `request_fields` (`frozenset({"channel_ids", "enabled"})`) but the tool only sends `{"channel_ids": channel_ids}` — the `enabled` field is never sent. If `enabled` is required by the backend (no default), the PATCH may 422 silently from the call-time guard perspective (the guard only blocks keys NOT in the set, not missing required keys). The read-back confirmation queries `channel_profiles_list` and matches on `p.get("id") == profile_id` — this works only if profiles return their ID as an integer; if the backend returns string IDs the comparison fails and `now_count` is `None`. Exception in the read-back is silently caught and `now_count` is `None` — the operator loses confirmation without being told.

---

## Normalization

---

#### `test_normalization(text: str)` — READ-ONLY

- **Purpose:** Test how stream names are normalized by running all enabled rules against the input; multiple names can be comma-separated.
- **Prompt to Claude:** "Test how ECM normalizes the stream name 'US : ESPN HD, US : CNN East'."
- **Expected:** Claude calls `test_normalization(text="US : ESPN HD, US : CNN East")`. The tool splits on commas, sends `{"texts": ["US : ESPN HD", "US : CNN East"]}` to `POST /api/normalization/test-batch`, and returns: `Normalization Results:` with `original → normalized` per entry.
- **Edge / failure tests:**
  - "Test normalization with just one name: 'US : HBO'" → single-item list; response shows one arrow pair.
  - "Test normalization with a name that no rules match" → `original → original` (unchanged); verify this is shown clearly, not as an empty result.
  - "Test with empty string input ''" → `[t.strip() for t in "".split(",") if t.strip()]` yields `[]`; POST body `{"texts": []}` — backend behavior on empty list TBD. Claude may get `{}` back and return "No normalization results." which is technically correct but opaque.
  - "Test with a name containing a comma inside the name: 'ESPN, the channel'" → the comma-split is naive; "ESPN" and " the channel" become two separate queries. This is a known limitation — document it clearly.
- **Watch for (possible bug areas):** Comma-split for multi-input is naive — no way to escape commas in names. If the backend returns `{"results": {"ESPN HD": "ESPN HD East"}}` (dict of orig→norm, not a list), the code reaches the dict branch and prints `ESPN HD → ESPN HD East` correctly. But if the backend returns `{"results": null}` then `results = null` and `if not results:` returns "No normalization results." — correct. Response field resolution: `r.get("normalized", r.get("result", "?"))` — if the backend uses a key like `output` or `name`, the operator sees "?". This is worth verifying against the actual backend response shape.

---

#### `list_normalization_rules()` — READ-ONLY

- **Purpose:** List all normalization rule groups and their rules via `GET /api/normalization/groups`.
- **Prompt to Claude:** "What normalization rules are configured in ECM?"
- **Expected:** Claude calls `list_normalization_rules()`. Response: "Normalization Rules (N groups):" with each group's name, ID, enabled/disabled status, rule count, and up to 5 rule names/types per group with a "... and N more" if there are additional rules.
- **Edge / failure tests:**
  - "List normalization rules when none are configured" → `if not groups:` returns "No normalization rules configured."
  - "List when a group has more than 5 rules" → only first 5 shown + "... and N more"; verify count arithmetic is correct (e.g., 8 rules → 5 shown + "and 3 more").
  - "List when the backend returns `{"groups": null}`" → `groups = result.get("groups", []) if isinstance(result, dict) else result` → `groups = None`; then `if not groups:` is truthy for `None` → returns "No normalization rules configured." — technically correct but might mask a backend error.
- **Watch for (possible bug areas):** `rname = r.get("name", r.get("pattern", "?"))` — if neither "name" nor "pattern" is present (e.g., the backend uses "match" or "regex"), shows "?". `rtype = r.get("type", "?")` — same. The `enabled` check uses `g.get("enabled", True)` — a missing key defaults to "enabled", which may be the wrong assumption if the backend returns only enabled groups. No ability to see the full rule details (replacement value, flags, order) through this tool — it is an index view only.

---

## M3U Accounts

#### `list_m3u_accounts()` — READ-ONLY
- **Purpose:** List all configured M3U provider accounts with stream counts and status.
- **Prompt to Claude:** "Show me all my M3U accounts and how many streams each one has."
- **Expected:** Claude calls `list_m3u_accounts()`. Response is a formatted list like `Found N M3U accounts: <name> (id=<N>) — <count> streams, status: <status>`. The tool hits `GET /api/providers` (not `/api/m3u/accounts` — the list endpoint goes through the providers path). Empty ECM returns "No M3U accounts configured."
- **Edge / failure tests:**
  - "List my M3U providers" (no accounts configured) → should return "No M3U accounts configured." not an error or empty dict.
  - "What M3U accounts do I have?" when ECM backend is unreachable → should return a graceful `Error listing M3U accounts: <detail>` not raise an unhandled exception.
- **Watch for (possible bug areas):** The list endpoint hits `GET /api/providers`, not `GET /api/m3u/accounts`. If the providers response wraps results in a `{"results": [...]}` envelope, `providers` here is the raw response (a dict, not a list), so `len(providers)` and the `for p in providers` loop will fail or return wrong data. Also: `stream_count` and `status` are listed in the formatter but if Dispatcharr's `/api/providers` response doesn't include those fields (they may be on a detail endpoint), all accounts will show `0 streams, status: unknown` silently.

---

#### `refresh_m3u(account_id: int)` — WRITE
- **Purpose:** Refresh a specific M3U account to fetch the latest stream list (async — triggers a background task).
- **Prompt to Claude:** "Refresh the 'Infinity' M3U account." (just name it — Claude looks it up)
- **Expected:** Claude calls `refresh_m3u(account_id=<resolved>)`. Response is `"M3U account 'Infinity' refresh started. <backend message>"`. The backend triggers Dispatcharr refresh immediately and spawns a background poll task; the MCP tool returns before completion. Claude should communicate that the refresh is asynchronous.
- **Edge / failure tests:**
  - "Refresh an M3U account called 'Nonexistent'" → Claude resolves the name via `list_m3u_accounts`, finds no match, and reports it cannot find the account rather than calling the tool with a bad ID.
  - "Refresh the 'Strong' M3U account" when a refresh is already running for that account → no lock prevents double-trigger; both will run. Watch for the result message — it will just say "refresh started" regardless. Note this for the operator.
- **Watch for (possible bug areas):** `result.get("message", "")` fails silently if the backend returns a non-dict (e.g., a list or `None`). The `isinstance(result, dict)` guard is present but the fallback is an empty string, so `msg` will be blank and Claude will see a bare "M3U account refresh started." with no server detail. The 300s timeout is set on the `call_endpoint` call, but the backend's refresh endpoint itself returns immediately and spawns a background poll — the timeout only guards the trigger call, not actual completion. The operator should be aware: "started" does not mean "finished."

---

#### `refresh_all_m3u()` — WRITE
- **Purpose:** Trigger a refresh of all M3U accounts at once via a single backend call.
- **Prompt to Claude:** "Refresh all my M3U feeds right now."
- **Expected:** Claude calls `refresh_all_m3u()`. Response is `"M3U refresh started for all accounts. <backend message>"`. Backend hits `POST /api/m3u/refresh` which triggers Dispatcharr bulk refresh. All active accounts are refreshed; inactive ones may be skipped (Dispatcharr-side behavior).
- **Edge / failure tests:**
  - "Refresh all M3U" when no accounts exist → backend may return `{"message": "..."}` or an empty/204 response. If it returns 204 (no body), `result.get("message", "")` will fail because `result` will be `None`. `isinstance(result, dict)` guard is present so `msg` will be blank — output will be `"M3U refresh started for all accounts. "` which is misleading but not an error.
  - "Refresh all M3U" called twice in rapid succession → two concurrent refreshes on all accounts. Dispatcharr may deduplicate or run them both. The tool has no idempotency guard.
- **Watch for (possible bug areas):** Unlike `refresh_all_epg`, this tool uses a single backend endpoint and has no partial-failure reporting — if one account fails to refresh, the operator has no visibility from the MCP response alone. The backend's `POST /api/m3u/refresh` delegates to Dispatcharr's bulk-refresh endpoint; it returns immediately. Any failures happen in background polling and surface only as notifications, not in the MCP tool's response.

---

#### `get_m3u_account(account_id: int)` — READ-ONLY
- **Purpose:** Get detailed information about a specific M3U account.
- **Prompt to Claude:** "Show me the full details for the 'Infinity' M3U account." (just name it — Claude looks it up)
- **Expected:** Claude calls `get_m3u_account(account_id=<resolved>)`. Response includes name, id, type, URL (truncated at 60 chars), status, stream count, and last refresh timestamp. URL truncation is deliberate — `'...'` appended only if `len(url) > 60`.
- **Edge / failure tests:**
  - "Get details for an M3U account called 'Nonexistent Provider'" → Claude resolves the name via `list_m3u_accounts`, finds no match, and reports it cannot find the account.
  - "Get details for the 'Strong' account" (valid name, resolved to a valid ID) → should return full detail including stream count and last refresh.
- **Watch for (possible bug areas):** The type field tries both `a.get("type")` and `a.get("server_type")` with fallback to `"standard"` — if Dispatcharr returns the field as `account_type` (which `get_m3u_stream_metadata` uses), the displayed type will silently fall back to `"standard"` even for XC accounts. Last refresh tries both `a.get("last_refresh")` and `a.get("updated_at")` — if neither is present the display shows `"never"`, which is correct but the field names may not match Dispatcharr's actual schema. Verify XC accounts show the correct type.

---

#### `create_m3u_account(name: str, url: str, server_type: str = "standard")` — WRITE
- **Purpose:** Create a new M3U provider account (standard M3U URL, Xtream Codes, or HD Homerun).
- **Prompt to Claude:** "Add a new M3U account named 'MCPTEST Provider' with the URL https://example-iptv.com/playlist.m3u8 — WARNING: this will actually create an account in ECM."
- **Expected:** Claude calls `create_m3u_account(name="MCPTEST Provider", url="https://example-iptv.com/playlist.m3u8", server_type="standard")`. Response is `"M3U account created: MCPTEST Provider (id=<N>)"`. Backend posts `{"name": ..., "url": ..., "server_type": ...}` to `POST /api/m3u/accounts`.
- **Edge / failure tests:**
  - "Create an M3U account named 'MCPTEST Local' with url=http://192.168.1.100/list.m3u" (private IP / non-https URL) → `validate_url_scheme` in the backend may reject it; tool should surface the HTTP 500 detail. Test whether the URL scheme validator allows `http://` or only `https://`.
  - "Create an M3U account named 'MCPTEST Provider' again" (duplicate name) → Dispatcharr may return 400 conflict; backend swallows to 500; tool returns `Error creating M3U account: POST /api/m3u/accounts -> HTTP 500`. No deduplication logic (unlike `create_channel_group` which has a 400-catch-and-return path).
- **Watch for (possible bug areas):** The MCP body sends `{"name", "url", "server_type"}` but the endpoint contract also allows `"server_url"`. For Xtream Codes accounts the actual auth endpoint may require `server_url` rather than `url`, but the MCP tool has no way to pass `server_url` — Claude would need to use the `url` param and hope the backend maps it correctly. This is a potential silent misconfiguration for XC accounts. Also: no validation of `server_type` against the allowed set `{"standard", "xtream", "hdhr"}` — an invalid value passes straight through to Dispatcharr.

---

#### `update_m3u_account(account_id: int, name: str | None = None, url: str | None = None)` — WRITE
- **Purpose:** Rename an M3U account or change its playlist URL.
- **Prompt to Claude:** "Rename the 'Infinity' M3U account to 'Primary Provider'." (just name it — Claude looks it up)
- **Expected:** Claude calls `update_m3u_account(account_id=<resolved>, name="Primary Provider")`. Response is `"M3U account 'Infinity' updated: name='Primary Provider', url='<current url>'"`. Payload only includes fields that were set — `name` only here. The backend hits `PATCH /api/m3u/accounts/{id}`.
- **Edge / failure tests:**
  - "Update the 'Infinity' account but don't change anything" (no name, no url provided) → tool catches this client-side and returns `"No changes specified."` without making an HTTP call.
  - "Change the URL for an M3U account called 'Nonexistent Feed' to https://newprovider.com/list.m3u" → Claude resolves the name via `list_m3u_accounts`, finds no match, and reports the account could not be found.
- **Watch for (possible bug areas):** The MCP `update_m3u_account` uses `PATCH` via the endpoint contract, which maps to `patch_m3u_account` on the backend. The `m3u_update_account` contract's `request_fields` include `{"name", "url", "server_url", "is_active"}`. The tool can only set `name` or `url` — there's no way to toggle `is_active` or set `server_url` through this tool. The URL field in the response is truncated to 60 chars — if the result dict lacks a `"url"` key (possible for XC accounts using `server_url`), `rurl` will be an empty string and the response will show `url=''`.

---

#### `delete_m3u_account(account_id: int)` — DESTRUCTIVE
- **Purpose:** Delete an M3U provider account and all its streams; the backend also attempts to delete orphaned associated channel groups.
- **Prompt to Claude:** "Delete the 'Strong' M3U account." (just name it — Claude looks it up; confirm this is intentional)
- **Expected:** Claude calls `delete_m3u_account(account_id=<resolved>)`. Response is `"M3U account 'Strong' deleted."`. Backend actually does more: it fetches the account's channel groups, identifies non-shared ones, deletes the account, then deletes the orphaned groups, cleans up `linked_m3u_accounts` in settings, and journals the deletion. The MCP tool returns no information about the cascade — how many groups were deleted is not surfaced.
- **Edge / failure tests:**
  - "Delete an M3U account called 'Nonexistent Provider'" → Claude resolves the name via `list_m3u_accounts`, finds no match, and reports the account could not be found rather than calling the tool with a bad ID.
  - "Delete the 'Infinity' account" (account shared with another via server groups) → backend identifies shared groups and skips deleting them. MCP tool still returns `"M3U account 'Infinity' deleted."` — the operator gets no indication that shared groups were retained.
- **Watch for (possible bug areas):** The backend's delete returns `{"status": "deleted", "deleted_groups": [...], "skipped_groups": [...], "failed_groups": [...]}` — this rich response is completely discarded by the MCP tool, which just returns `"M3U account {id} deleted."`. Failed group deletions are silently swallowed from the operator's perspective. The MCP tool has no confirmation step — destructive and immediate. No delete guard in the MCP code itself (unlike `delete_channel_group` which has a read-back verification). Also: no idempotency — calling twice on the same (now-deleted) account will return an error on the second call, not a clean "already deleted."

---

#### `update_m3u_group_settings(account_id: int, group_name: str, enabled: bool)` — WRITE
- **Purpose:** Enable or disable a single stream group on an M3U account by group name.
- **Prompt to Claude:** "Disable the 'Sports HD' group on the 'Infinity' M3U account." (just name the account — Claude looks it up)
- **Expected:** Claude calls `update_m3u_group_settings(account_id=<resolved>, group_name="Sports HD", enabled=False)`. Response is `"Group 'Sports HD' disabled on M3U account 'Infinity'."`. The tool sends `PATCH /api/m3u/accounts/<id>/group-settings` with body `{"Sports HD": False}`.
- **Edge / failure tests:**
  - "Enable the 'Movies 4K' group on the 'Infinity' account" where that group name does not exist on that account → the backend receives `{"Movies 4K": True}` and will likely silently ignore the unknown key or return a 400. Watch whether the tool surfaces the error or returns the success message regardless (the tool's response is built from the state change declaration, not from the HTTP response body).
  - "Disable the 'Sports HD' group on an account called 'Nonexistent Provider'" → Claude resolves the account name via `list_m3u_accounts`, finds no match, and reports the account could not be found.
- **Watch for (possible bug areas):** CRITICAL — The body sent is `{group_name: enabled}` (a flat dict with a raw group name as key), but the backend's `update_m3u_group_settings` router reads `data.get("group_settings", [])` (a list structure) and passes the entire body to `client.update_m3u_group_settings` which forwards it to Dispatcharr's `/api/m3u/accounts/{id}/group-settings/` endpoint. The MCP tool sends `{"Sports HD": false}` but Dispatcharr's endpoint expects a structured body like `{"group_settings": [{"channel_group": 123, "enabled": false}]}`. This format mismatch is the most likely reason this tool is broken in 0.17.2. Verify whether Dispatcharr actually accepts the flat name→bool format or requires structured group objects. The `# contract-exempt` comment acknowledges the dynamic key body but does not guarantee the wire format is correct.

---

#### `bulk_update_m3u_group_settings(account_id: int, groups: dict[str, bool])` — WRITE
- **Purpose:** Enable or disable multiple stream groups on an M3U account in a single call.
- **Prompt to Claude:** "On the 'Infinity' M3U account, disable the 'Sports HD' and 'News' groups, and enable the 'Movies' group all at once." (just name the account — Claude looks it up)
- **Expected:** Claude calls `bulk_update_m3u_group_settings(account_id=<resolved>, groups={"Sports HD": False, "News": False, "Movies": True})`. Response lists each change: `"Updated 3 groups on M3U account 'Infinity':\n  disabled 'Sports HD'\n  disabled 'News'\n  enabled 'Movies'"`. Same PATCH endpoint as `update_m3u_group_settings` but with multiple keys.
- **Edge / failure tests:**
  - "Update groups with an empty list on the 'Infinity' account" (`groups={}`) → the tool sends an empty-body PATCH. Backend receives `{}` — behavior is Dispatcharr-defined. The tool's `changes` list will be empty and the response will be `"Updated 0 groups on M3U account 'Infinity':\n  "` (the join produces a trailing newline with no entries — minor formatting issue).
  - "Bulk update 50 groups on the 'Strong' account" → all 50 group-name keys sent in one PATCH body. If any names don't exist, Dispatcharr may silently ignore them or reject the entire request. The tool does not report which keys were rejected.
- **Watch for (possible bug areas):** Same critical wire-format issue as `update_m3u_group_settings` — the tool sends `{"Sports HD": false, "Movies": true}` but Dispatcharr likely expects a structured list of channel-group objects with integer IDs. Both tools share the identical underlying `client.patch(...)` call. If the single-group tool is broken, the bulk variant is broken in the same way. Additionally, this is the multiplied-blast-radius case from the engineering discipline: a latent format bug in the single-item tool becomes a multi-group silent failure in the bulk variant, with no partial-failure reporting. The success message is constructed from the input `groups` dict regardless of what the backend actually did — even a 200 response with no real effect will print "Updated N groups."

---

## EPG Sources

#### `list_epg_sources()` — READ-ONLY
- **Purpose:** List all configured EPG data sources with channel counts and URLs.
- **Prompt to Claude:** "What EPG sources do I have configured?"
- **Expected:** Claude calls `list_epg_sources()`. Response is a formatted list of sources with id, name, channel count, and truncated URL. If the backend returns a dict envelope (`{"sources": [...]}` or `{"results": [...]}`), the tool unwraps it. Empty returns "No EPG sources configured."
- **Edge / failure tests:**
  - "List EPG sources" when backend returns a plain list vs. an envelope dict → the code handles both: `if isinstance(sources, dict): sources = sources.get("sources", sources.get("results", []))`. Verify the actual Dispatcharr response shape matches one of these two expected keys.
  - "Show my EPG feeds" when no sources exist → "No EPG sources configured." (correct clean state).
- **Watch for (possible bug areas):** The URL is truncated to 50 chars with `...` always appended (unlike M3U which conditionally appends `...` only if `len > 60`). This means a 30-char URL displays as `short-url...` with a misleading ellipsis. Minor display bug but could confuse operators verifying URLs. `channel_count` may be missing from the Dispatcharr response depending on the endpoint — silently shows `0` for all sources.

---

#### `refresh_epg(source_id: int)` — WRITE
- **Purpose:** Refresh a single EPG source to fetch the latest program guide data (async).
- **Prompt to Claude:** "Refresh the 'Schedules Direct' EPG source." (just name it — Claude looks it up)
- **Expected:** Claude calls `refresh_epg(source_id=<resolved>)`. Response is `"EPG source 'Schedules Direct' refresh started. <backend message>"`. Backend triggers Dispatcharr refresh and spawns a background poll task (up to 15 minutes wait vs. M3U's 5 minutes). Tool returns before completion.
- **Edge / failure tests:**
  - "Refresh an EPG source called 'Nonexistent Guide'" → Claude resolves the name via `list_epg_sources`, finds no match, and reports it cannot find the source rather than calling the tool with a bad ID.
  - "Refresh the 'Schedules Direct' source" twice rapidly → two concurrent background pollers for the same source. Second trigger may succeed or fail depending on Dispatcharr's concurrency handling. No guard in MCP or backend.
- **Watch for (possible bug areas):** Same async-completion mismatch as `refresh_m3u` — `result.get("message", "")` on a non-dict result silently gives blank `msg`. The 300s `timeout` on `call_endpoint` guards the trigger HTTP call, not the actual EPG refresh which can take up to 15 minutes per the backend poll loop. If the trigger call itself takes >300s (unlikely for large EPG files but possible with slow Dispatcharr), the tool will time out and return an error even though the refresh did start. The operator has no way to query refresh status through MCP.

---

#### `refresh_all_epg(source_ids: list[int] | None = None)` — WRITE
- **Purpose:** Refresh multiple EPG sources sequentially; if no IDs given, refreshes all sources.
- **Prompt to Claude:** "Refresh all my EPG sources." (or "Refresh just the 'Schedules Direct' and 'Teamarr' EPG sources.")
- **Expected:** Claude calls `refresh_all_epg()` or `refresh_all_epg(source_ids=[<resolved>, <resolved>])`. Response is `"Refreshed N/M EPG sources."` plus an errors block if any failed. Unlike `refresh_all_m3u`, this loops and calls `refresh_epg_source` per source individually, collecting partial failures.
- **Edge / failure tests:**
  - "Refresh 'Schedules Direct' and a source called 'Nonexistent Guide'" → 'Schedules Direct' refreshes; Claude reports it could not resolve 'Nonexistent Guide' and skips it. Tool reports the partial result.
  - "Refresh all EPG" when the source list fetch itself fails → outer `except` catches and returns `"Error refreshing EPG sources: ..."` with no partial information.
- **Watch for (possible bug areas):** When `source_ids=None`, the tool first calls `list_epg_sources` to discover IDs, applying the same `isinstance(sources, dict)` unwrapping. If the list endpoint returns an envelope with a different key (e.g., `{"data": [...]}`), `source_ids` will be empty and the tool returns `"No EPG sources found to refresh."` silently — zero refreshes, no error. The serial refresh loop (one at a time, each with a 300s timeout) can take a very long time with many sources. Each inner `refresh_epg_source` call triggers a background poll — so after `refresh_all_epg` returns, there may be N concurrent background pollers running on the server.

---

#### `match_channels_epg()` — WRITE
- **Purpose:** Auto-match channels to EPG data based on channel names with confidence scoring.
- **Prompt to Claude:** "Run EPG auto-matching to match my channels to guide data."
- **Expected:** Claude calls `match_channels_epg()`. Response is `"EPG auto-match complete: N channels matched, M unmatched."`. Backend runs the full matching pipeline: fetches all channels, all streams (paginated), all EPG data, runs `batch_find_epg_matches`, and returns categorized results with `{"exact": [...], "multiple": [...], "none": [...], "summary": {...}}`.
- **Edge / failure tests:**
  - "Match channels to EPG" when no EPG sources are configured → backend returns `{"exact": [], "multiple": [], "none": [...], "summary": {...}}`. MCP tool reads `result.get("matched", 0)` and `result.get("unmatched", 0)`. These keys do NOT exist in the actual backend response — the backend returns `summary.matched_count` or similar. Verify the actual response keys.
  - "Match channels to EPG" with 2000+ channels → may hit the Dispatcharr/backend timeout for EPG grid (504 handling is in the grid endpoint, not here). The match endpoint fetches streams with pagination, which could take minutes.
- **Watch for (possible bug areas):** CRITICAL — The MCP tool reads `result.get("matched", 0)` and `result.get("unmatched", 0)`, but the backend response shape is `{"exact": [...], "multiple": [...], "none": [...], "summary": {"total_channels": N, "exact_count": N, "multiple_count": N, "none_count": N, "match_time_ms": N}}`. Neither `"matched"` nor `"unmatched"` is a top-level key. This means `matched` will always be `0` and `unmatched` will always be `0` regardless of actual results. The tool will always report `"EPG auto-match complete: 0 channels matched, 0 unmatched."` — completely wrong output even on success. This is almost certainly a broken tool for 0.17.2.

---

#### `create_epg_source(name: str, url: str)` — WRITE
- **Purpose:** Create a new EPG data source (XMLTV feed).
- **Prompt to Claude:** "Add a new EPG source named 'MCPTEST Guide' with URL https://xmltv.example.com/us-guide.xml.gz — WARNING: this will actually create an EPG source."
- **Expected:** Claude calls `create_epg_source(name="MCPTEST Guide", url="https://xmltv.example.com/us-guide.xml.gz")`. Response is `"EPG source created: MCPTEST Guide (id=<N>)"`. Backend validates the URL scheme and forwards to Dispatcharr.
- **Edge / failure tests:**
  - "Create EPG source named 'MCPTEST Bad URL' with URL file:///etc/passwd" → `validate_url_scheme` should reject non-http/https; backend returns 400/500; tool surfaces the error.
  - "Create EPG source named 'MCPTEST Guide' again" (duplicate name) → Dispatcharr may 400; backend swallows to 500; tool returns error. No dedup path like channel groups.
- **Watch for (possible bug areas):** No `source_type` parameter exposed — the MCP tool always sends `{"name": ..., "url": ...}` without specifying whether this is `xmltv`, `dummy`, etc. If Dispatcharr requires `source_type`, it will be absent and may default to something unexpected. Also: the backend `create_epg_source` router body is `request: Request` (raw), so Dispatcharr receives whatever ECM forwards. If Dispatcharr requires a `source_type` field, all EPG sources created via MCP will be misconfigured silently.

---

#### `update_epg_source(source_id: int, name: str | None = None, url: str | None = None)` — WRITE
- **Purpose:** Rename an EPG source or change its XMLTV feed URL.
- **Prompt to Claude:** "Change the URL for the 'Schedules Direct' EPG source to https://newguide.example.com/guide.xml." (just name it — Claude looks it up)
- **Expected:** Claude calls `update_epg_source(source_id=<resolved>, url="https://newguide.example.com/guide.xml")`. Response is `"EPG source 'Schedules Direct' updated: name='Schedules Direct', url='https://newguide.example.com/guide.xml'"`. No call is made if neither `name` nor `url` is provided.
- **Edge / failure tests:**
  - "Update the 'Schedules Direct' EPG source with no changes" → tool returns `"No changes specified."` without making an HTTP call.
  - "Rename an EPG source called 'Nonexistent Guide' to 'New Name'" → Claude resolves the name via `list_epg_sources`, finds no match, and reports the source could not be found.
- **Watch for (possible bug areas):** Same URL-truncation behavior as `update_m3u_account` — `rurl = (result.get("url") or "")[:60]`. If Dispatcharr's response uses a different key for the URL (e.g., `"xmltv_url"` or `"feed_url"`), `rurl` will always be empty and the confirmation message will show `url=''`. The backend fetches the before-state for journaling, but the MCP tool response uses the update result, not the before state.

---

#### `delete_epg_source(source_id: int)` — DESTRUCTIVE
- **Purpose:** Delete an EPG data source.
- **Prompt to Claude:** "Delete the 'Teamarr' EPG source." (just name it — Claude looks it up; confirm this is a disposable source)
- **Expected:** Claude calls `delete_epg_source(source_id=<resolved>)`. Response is `"EPG source 'Teamarr' deleted."`. Backend fetches the source name before deletion for journal logging, then deletes via Dispatcharr.
- **Edge / failure tests:**
  - "Delete an EPG source called 'Nonexistent Guide'" → Claude resolves the name via `list_epg_sources`, finds no match, and reports it cannot find the source.
  - "Delete the 'Teamarr' EPG source" twice → second call: Claude resolves the name via `list_epg_sources`, finds it no longer exists, and reports it cannot find the source (or backend returns 500 on the delete call).
- **Watch for (possible bug areas):** No confirmation step. Backend response on successful delete is `{"status": "deleted"}` — the tool ignores this and constructs its own confirmation string. If the backend returns 204 (no body), `client.delete()` returns `None`; `await client.call_endpoint(...)` returns `None`, which is not checked — but since the return value is not used, this is harmless. Any channels that were matched to this EPG source lose their EPG association; this cascade is handled by Dispatcharr, not surfaced to the operator via MCP.

---

#### `get_epg_grid(channel_id: int | None = None, limit: int = 20)` — READ-ONLY
- **Purpose:** Get the EPG schedule grid showing what programs are on now and upcoming.
- **Prompt to Claude:** "What's on TV right now according to the EPG?" (or "Show me the EPG schedule for the channel named 'CNN HD', up to 10 programs.")
- **Expected:** Claude calls `get_epg_grid()` or `get_epg_grid(channel_id=<resolved>, limit=10)`. Response is a formatted schedule list: `EPG Schedule (N programs): [ChannelName] Title (start - end)`. The backend returns the full grid; `channel_id` and `limit` filtering happen client-side in the MCP tool.
- **Edge / failure tests:**
  - "Show EPG grid for a channel called 'No Guide Channel'" (channel with no programs or non-existent) → backend returns full grid, tool filters for the resolved ID, finds nothing, returns `"No EPG schedule data available."` This is correct but could confuse if the channel exists but simply has no EPG data vs. not being a real channel.
  - "Get EPG grid" with a large EPG dataset (2000+ channels) → backend EPG grid endpoint has known 504 timeout risk (comment in backend router). Tool will surface `"Error getting EPG grid: GET /api/epg/grid -> HTTP 504 ..."`.
- **Watch for (possible bug areas):** `channel_id` filtering uses `channel_id in (p.get("channel_id"), p.get("channel"))` — the program dict key might be `"channel"` (an ID) or `"channel_id"`. If neither matches (e.g., the key is `"channel_name"` for a string), ALL programs are filtered out and the result is `"No EPG schedule data available."` even with data present. The `limit` param is applied after channel filtering with `programs[:limit]` — so a `limit=1` with a channel name set will return at most 1 program from that channel, which is correct. The `limit` default is 20 and is not capped server-side — the operator could ask for `limit=10000` and get a very long response.

---

#### `list_dummy_epg_profiles()` — READ-ONLY
- **Purpose:** List all dummy EPG profiles used to generate placeholder guide data for channels without real EPG.
- **Prompt to Claude:** "Show me the dummy EPG profiles I have set up."
- **Expected:** Claude calls `list_dummy_epg_profiles()`. Response is a formatted list with profile name, id, enabled/disabled state, and group count. Applies the same `isinstance(profiles, dict)` unwrapping as `list_epg_sources`.
- **Edge / failure tests:**
  - "List dummy EPG profiles" when none are configured → "No dummy EPG profiles configured."
  - "Show dummy profiles" when backend returns `{"profiles": null}` → `profiles.get("profiles", ...)` returns `None`; `if not profiles:` catches it; returns "No dummy EPG profiles configured." This is correct.
- **Watch for (possible bug areas):** The response unwrapping tries `profiles.get("profiles", profiles.get("results", []))` — if the actual backend key is neither `"profiles"` nor `"results"` (e.g., the dummy EPG list endpoint returns a bare list or uses `"data"`), the tool will iterate over the dict keys instead of profile objects, producing garbled output. Verify the actual `/api/dummy-epg/profiles` response shape. Also: `p.get("enabled")` is used without truthiness nuance — a missing field will be falsy, so all profiles appear "disabled" if the field is absent.

---

#### `generate_dummy_epg()` — WRITE
- **Purpose:** Force regeneration of all dummy EPG XMLTV data from enabled profiles.
- **Prompt to Claude:** "Regenerate the dummy EPG data now."
- **Expected:** Claude calls `generate_dummy_epg()`. Response is `"Dummy EPG regenerated for N enabled profiles."` where N comes from `result.get("profiles_generated", 0)`. The backend hits `POST /api/dummy-epg/generate` with a 60s timeout.
- **Edge / failure tests:**
  - "Generate dummy EPG" when no profiles are enabled → backend may return `{"profiles_generated": 0}` or `0`. Tool reports `"Dummy EPG regenerated for 0 enabled profiles."` which is technically correct but could mislead the operator into thinking it worked.
  - "Generate dummy EPG" when backend returns a list instead of a dict → `result.get("profiles_generated", 0)` fails with `AttributeError`. The `isinstance(result, dict)` guard correctly defaults to `count = 0` — so the tool returns `"Dummy EPG regenerated for 0 enabled profiles."` silently even if generation succeeded.
- **Watch for (possible bug areas):** The 60s timeout is tight for a generation run with many profiles or large channel sets. If the backend takes longer than 60s, the tool raises `TimeoutError` and returns an error to the operator even though generation may have started. The `count` is derived from `result.get("profiles_generated", 0)` — verify this is the actual key the backend returns (it could be `"count"`, `"generated"`, or similar). If the key name mismatches, count will always be 0 and the success message is misleading.

---

## Channel Groups

#### `list_channel_groups()` — READ-ONLY
- **Purpose:** List all channel groups with channel counts (hidden groups are excluded by the backend).
- **Prompt to Claude:** "List all my channel groups."
- **Expected:** Claude calls `list_channel_groups()`. Response is `"Found N channel groups: <name> (id=<N>) — <count> channels"`. Note: the backend filters out hidden groups and adds `is_auto_sync` flags — the MCP tool does not expose `is_auto_sync` in its output.
- **Edge / failure tests:**
  - "List channel groups" when none exist → "No channel groups found."
  - "Show my groups" on an ECM instance with many hidden groups → tool returns the filtered list (hidden excluded), not the full Dispatcharr list. The count displayed is the visible count only.
- **Watch for (possible bug areas):** The `channel_count` field may or may not be present in the Dispatcharr response for groups — silently shows `0` if absent. The `groups_list` endpoint hits `GET /api/channel-groups` which goes through ECM's backend (including the hidden-group filter and stale-record pruning) — this is heavier than a direct Dispatcharr call. If the backend's M3U group settings fetch fails during the hidden-group filter, the entire endpoint raises 500 and the tool returns an error.

---

#### `create_channel_group(name: str)` — WRITE
- **Purpose:** Create a new channel group; returns the existing group if the name already exists.
- **Prompt to Claude:** "Create a new channel group called 'UK Sports'."
- **Expected:** Claude calls `create_channel_group(name="UK Sports")`. Response is `"Channel group ready: UK Sports (id=<N>)"`. The "ready" wording (not "created") is intentional — the backend deduplicates by name and returns the existing group if found.
- **Edge / failure tests:**
  - "Create a group called 'UK Sports'" when it already exists → backend returns 400, backend catches the error, looks up the existing group by name, and returns it; MCP tool reports `"Channel group ready: UK Sports (id=<existing-id>)"`. This is idempotent by design.
  - "Create a channel group with name ''" (empty string) → backend Pydantic model requires a non-empty `name`; returns 422 validation error; backend router swallows to 500; tool returns error.
- **Watch for (possible bug areas):** The result is always described as "ready" even on first creation — operators who parse the message to detect "created vs. existing" cannot distinguish them. If the name-lookup fallback fails (the `except search_err` path), the tool raises a 500 and loses the idempotency guarantee. Group names are case-sensitive in the lookup — creating "UK Sports" and "uk sports" may create two separate groups depending on Dispatcharr's behavior.

---

#### `get_orphaned_groups()` — READ-ONLY
- **Purpose:** List channel groups that have no streams, no channels, and no M3U association (truly orphaned).
- **Prompt to Claude:** "Show me any orphaned channel groups that I can clean up."
- **Expected:** Claude calls `get_orphaned_groups()`. Response is `"Found N orphaned groups: <name> (id=<N>)"`. The backend runs an expensive computation: fetches all groups, all streams (paginated), all channels (paginated), all M3U group settings, and identifies groups with zero content and no M3U association. Groups that are `group_override` targets of auto_channel_sync M3U groups are excluded.
- **Edge / failure tests:**
  - "Find orphaned groups" when none exist → "No orphaned channel groups found."
  - "Find orphaned groups" with thousands of streams → the backend paginates streams in 500-item pages with no page cap (the channel pagination has a page-50 safety limit, but the stream pagination loop in `get_orphaned_channel_groups` stops only when `len(page_streams) < 500` — a large stream count takes proportional time). Expect a slow response.
- **Watch for (possible bug areas):** The backend response shape from `GET /api/channel-groups/orphaned` is `{"orphaned_groups": [...], "total_groups": N, "groups_with_content": N}` — the MCP tool iterates directly over `groups` (the full response treated as a list). If the response is a dict (which it is), `for g in groups` iterates over dict keys (`"orphaned_groups"`, `"total_groups"`, `"groups_with_content"`), not group objects. This would produce `g.get("name")` and `g.get("id")` returning `None` for all three string keys. This is almost certainly broken — the tool should do `groups = result.get("orphaned_groups", result)` but does not.

---

#### `delete_channel_group(group_id: int, delete_channels: bool = False)` — DESTRUCTIVE
- **Purpose:** Delete a channel group (which may hide rather than delete if it has M3U sync); optionally delete all channels in the group first.
- **Prompt to Claude:** "Delete the 'USA | News' channel group." (just name it — Claude looks it up; for a test, use a group you know is empty)
- **Expected:** Claude calls `delete_channel_group(group_id=<resolved>)`. Response is `"Channel group 'USA | News' deleted."` OR (if the group has M3U sync settings) the backend hides the group and returns `{"status": "hidden"}` — but the MCP tool ignores the response and always reports `"Channel group 'USA | News' deleted."` even if it was actually hidden not deleted. With `delete_channels=True`: MCP tool paginates through channels in the group (page_size=500), deletes each individually, then deletes the group, then does a read-back verification.
- **Edge / failure tests:**
  - "Delete the 'USA | Sports' group" where it has active M3U sync → backend hides it (returns `{"status": "hidden"}`); MCP tool reports `"Channel group 'USA | Sports' deleted."` — misleading to the operator. Run `get_hidden_groups()` afterward to verify actual state.
  - "Delete the 'Radio' group with delete_channels=True" where some channels fail to delete → best-effort: partial deletion continues, failed channels are logged but not reported to MCP. The group delete is then attempted; if channels still exist, the group delete may fail on the backend. Tool will show `"Error deleting channel group 'Radio': ..."`.
  - "Delete a group called 'Nonexistent Group'" → Claude resolves the name via `list_channel_groups`, finds no match, and reports the group could not be found.
- **Watch for (possible bug areas):** The MCP tool always reports `"Channel group deleted."` regardless of whether the backend actually deleted or hid the group — the response dict `{"status": "hidden", "message": "..."}` is never checked. This is a confirmed behavioral gap. The read-back verification after deletion calls `list_channel_groups` (which excludes hidden groups) — if the group was hidden rather than deleted, it will NOT appear in the list, so `still_present` will be `False` and the tool will not issue the WARNING. The hidden state is effectively indistinguishable from deleted from the tool's perspective. With `delete_channels=True`, individual channel deletes have no concurrency — they are strictly serial, which is slow for large groups.

---

#### `get_hidden_groups()` — READ-ONLY
- **Purpose:** List channel groups that are hidden from the UI (ECM-local hidden state, stored in SQLite).
- **Prompt to Claude:** "Show me any channel groups that are hidden."
- **Expected:** Claude calls `get_hidden_groups()`. Response is `"Found N hidden groups: <name> (id=<N>)"`. Data comes from ECM's local `HiddenChannelGroup` table — not Dispatcharr. Hidden groups are ones that have M3U sync and were "deleted" via `delete_channel_group`.
- **Edge / failure tests:**
  - "Show hidden groups" when none are hidden → "No hidden channel groups."
  - "Show hidden groups" after a `delete_channel_group` call that silently hid a group → the group should appear here. Use this as a verification step after any `delete_channel_group` call on a group with M3U sync.
- **Watch for (possible bug areas):** The backend returns a list of `HiddenChannelGroup.to_dict()` objects. The MCP tool iterates `for g in groups` using `g.get("name")` and `g.get("id")` — verify the `to_dict()` schema includes these keys (it might use `group_id` and `group_name` instead of `id` and `name`, which would silently show `Unknown (id=?)` for every entry). This is a potential field-name mismatch. The hidden list is ECM-local; if ECM's database is reset, all hidden group records are lost and those groups reappear as visible.

---

#### `get_auto_created_groups()` — READ-ONLY
- **Purpose:** List channel groups that contain channels created by the auto-creation pipeline.
- **Prompt to Claude:** "Which channel groups have auto-created channels in them?"
- **Expected:** Claude calls `get_auto_created_groups()`. Response is `"Found N auto-created groups: <name> (id=<N>) — <count> channels"`. The backend paginates through all channels and identifies groups with `auto_created=True` channels.
- **Edge / failure tests:**
  - "Show auto-created groups" when no auto-creation rules have run → "No auto-created channel groups." (or the backend returns `{"groups": [], "total_auto_created_channels": 0}`).
  - "Show auto-created groups" when the backend response is `{"groups": [...], "total_auto_created_channels": N}` → MCP tool iterates directly over `groups` (the full dict). If the response is the dict envelope, `for g in groups` iterates over the dict keys, not group objects. Same envelope-unwrapping bug as `get_orphaned_groups`.
- **Watch for (possible bug areas):** Backend returns `{"groups": [...], "total_auto_created_channels": N}` — the MCP tool does `if not groups: return "No auto-created..."` and then `for g in groups`. If `groups` is the dict response (not a list), `not groups` is False (non-empty dict is truthy) and the loop iterates over string keys. The tool should extract `groups = result.get("groups", result)` but does not — this is likely broken for the same reason as `get_orphaned_groups`. Also: `channel_count` is shown from `g.get("channel_count", 0)` but the backend returns `"auto_created_count"` as the field name — verify the field name in the actual response.

---

#### `delete_orphaned_groups(group_ids: list[int] | None = None)` — DESTRUCTIVE
- **Purpose:** Delete orphaned channel groups (those with no streams, channels, or M3U association). Optionally targets specific groups by name; if none specified, deletes all orphaned.
- **Prompt to Claude:** "Clean up all orphaned channel groups." (or "Delete the orphaned groups named 'Old Sports' and 'Unused News' only.")
- **Expected:** Claude calls `delete_orphaned_groups()` or `delete_orphaned_groups(group_ids=[<resolved>, <resolved>])`. Response lists deleted groups: `"Deleted N orphaned group(s):\n  <name> (id=<N>)"`. If `result` is `None`, returns `"No orphaned groups were deleted."`.
- **Edge / failure tests:**
  - "Delete the orphaned group 'Old Sports' only" where 'Old Sports' is genuinely orphaned → Claude resolves the name, confirms it appears in `get_orphaned_groups()`, then calls `delete_orphaned_groups(group_ids=[<resolved>])`. Verify only that group is deleted.
  - "Delete all orphaned groups" when none exist → backend returns `{"status": "ok", "message": "...", "deleted_groups": [], "failed_groups": []}`. MCP reads `result.get("deleted", 0)` — but the backend returns `"deleted_groups"` (a list), not `"deleted"` (an int). So `deleted` will be `0` and the tool returns `"No orphaned groups were deleted."` even on a successful (empty) run. This is a field-name mismatch.
  - "Delete all orphaned groups" with groups having partial failures → backend returns `{"deleted_groups": [...], "failed_groups": [...]}`. The MCP tool reads `result.get("groups", [])` for the display list — but the backend returns `"deleted_groups"`, not `"groups"`. So the tool will show `"Deleted N orphaned group(s):\n"` with an empty list even when groups were deleted. Double field-name mismatch.
- **Watch for (possible bug areas):** CRITICAL — The MCP tool reads `result.get("deleted", 0)` and `result.get("groups", [])` but the backend returns `{"status": ..., "message": ..., "deleted_groups": [...], "failed_groups": [...]}`. Neither `"deleted"` nor `"groups"` is a key in the actual response. Both will be 0/empty, and the tool will always report `"No orphaned groups were deleted."` regardless of actual deletions. This is almost certainly broken. Additionally, the body sent to `DELETE /api/channel-groups/orphaned` is `{"group_ids": group_ids}` — verify the backend `DeleteOrphanedGroupsRequest` model accepts this at the body level (it does: `group_ids: list[int] | None = None`), but when `group_ids` is `None` the MCP tool sends `body=None`, which means no request body at all. The backend router `Body(None)` default handles this correctly.

---

#### `get_groups_with_streams()` — READ-ONLY
- **Purpose:** List channel groups that have at least one channel containing at least one stream (groups eligible for probing).
- **Prompt to Claude:** "Which channel groups have streams attached to them?"
- **Expected:** Claude calls `get_groups_with_streams()`. Response is `"Found N groups with stream info: <name> (id=<N>) — <count> streams"`. Backend runs an expensive operation: fetches all groups and all channels (paginated), then identifies groups where any channel has non-empty `streams` array.
- **Edge / failure tests:**
  - "Show groups with streams" when no channels have streams assigned → "No channel groups found." (backend returns empty or `{"groups": [], "total_groups": N}`).
  - "Show groups with streams" with thousands of channels → backend paginates with a 50-page safety limit; very slow. The tool response may be delayed by 30+ seconds.
- **Watch for (possible bug areas):** The backend returns `{"groups": [...], "total_groups": N}` — same envelope-unwrapping issue as `get_orphaned_groups` and `get_auto_created_groups`. The MCP tool does `if not groups: return "No channel groups found."` and then `for g in groups`. If `groups` is the dict envelope, the loop iterates over the string keys `"groups"` and `"total_groups"`, not group objects. The tool should unwrap `groups = result.get("groups", result)` but does not. Also: `stream_count` is read from `g.get("stream_count", 0)` but the backend response for each group in the `groups_with_streams` list only contains `{"id": ..., "name": ...}` — no `stream_count`. So all groups will show `0 streams` in the output.
## Auto-Creation Rules

---

#### `list_auto_creation_rules()` — READ-ONLY
- **Purpose:** List all auto-creation rules with id, name, enabled state, and priority.
- **Prompt to Claude:** "Show me all my auto-creation rules."
- **Expected:** Claude calls `list_auto_creation_rules()`. Returns a numbered list formatted as `[priority] name (id=N) — enabled/disabled`. If no rules exist, returns "No auto-creation rules configured." Backend response is `{"rules": [...]}` — tool unwraps the `rules` key. Tool shows priority, name, id, and enabled state only (not conditions/actions — use `get_auto_creation_rule` for detail).
- **Edge / failure tests:**
  - "List my auto-creation rules" when zero rules are configured → must return "No auto-creation rules configured." (not an empty list, not an error)
  - Simulate backend returning bare list instead of `{"rules": [...]}` wrapper → tool has defensive unwrap (`resp.get("rules", []) if isinstance(resp, dict) else (resp or [])`) — should not AttributeError; verify graceful handling.
- **Watch for (possible bug areas):** The comment in the code calls out the prior `bd-pvw35 / GH #222` bug where iterating the raw dict caused `str.get() AttributeError`. Confirm the fix holds — if the backend ever returns a plain list or the dict lacks the `rules` key, the fallback path is exercised. Also watch for priority displayed as `"?"` if backend omits the `priority` field.

---

#### `run_auto_creation(dry_run: bool = True)` — READ-ONLY (dry_run=True) | WRITE (dry_run=False) — HIGH-IMPACT when live
- **Purpose:** Run the auto-creation pipeline; dry_run=True (default) previews without making changes; dry_run=False creates channels for real.
- **Prompt to Claude (dry run):** "Preview what the auto-creation pipeline would create — don't make any changes yet."
- **Prompt to Claude (live):** "Run auto-creation for real and actually create the channels."
- **Expected (dry):** Claude calls `run_auto_creation(dry_run=True)`. Returns "Auto-creation Dry run complete:" with streams evaluated/matched, "Channels would be created: N", groups created, skipped count, duration, and a sample of up to 20 entity names. No channels are actually created.
- **Expected (live):** Claude calls `run_auto_creation(dry_run=False)`. Returns "Auto-creation Execution complete:" with "Channels created: N" (no "would be"). Channels are physically created in ECM. Timeout is 300 seconds — a slow pipeline must not appear to hang; if it does, report it.
- **Edge / failure tests:**
  - Ask "run auto-creation" without specifying dry_run → Claude should default to `dry_run=True` (safe default). Verify Claude does NOT pass `dry_run=False` without explicit instruction.
  - "Run auto-creation dry run" with no streams in ECM → should return 0 streams evaluated, not an error.
  - Simulate pipeline timeout (>300s) → should surface a `TimeoutError`, not hang silently.
  - "Run live auto-creation" when a rule's condition contains an invalid regex → pipeline should still complete (returns errors or skips); must not 500.
- **Watch for (possible bug areas):** The dry_run parameter default is `True` in the MCP tool signature — highest safety risk is Claude incorrectly passing `False` when the operator says "run auto-creation" ambiguously. Verify Claude interprets natural language correctly. The `rule_match_counts` dict is rendered raw — watch for truncated or unreadable output on large rule sets. Sample display is capped at 20 entities — "and N more" line must appear correctly.

---

#### `get_auto_creation_rule(rule_id: int)` — READ-ONLY
- **Purpose:** Get detailed information about a single auto-creation rule including conditions and actions.
- **Prompt to Claude:** "Show me the full details of the 'Auto Sports' auto-creation rule." (just name it — Claude looks it up)
- **Expected:** Claude resolves the name to an id via `list_auto_creation_rules`, then calls `get_auto_creation_rule(rule_id=N)`. Returns rule name, id, enabled state, priority, description (if set), run_on_refresh, stop_on_first_match, skip_struck_streams, orphan_action, sort settings, normalization group ids (if set), up to 10 conditions with type and value, and up to 10 actions with type and target/value. Conditions/actions beyond 10 show "... and N more".
- **Edge / failure tests:**
  - "Show me the details of the 'Phantom Rule' auto-creation rule" (non-existent name) → Claude attempts lookup and backend returns 404; tool should return `Error getting rule: GET /api/auto-creation/rules/N -> HTTP 404 Not Found: ...`
  - Rule with no conditions or actions → fields omitted cleanly, no KeyError.
  - Rule with exactly 10 conditions, then 11 conditions → verify truncation `... and 1 more` appears at 11.
- **Watch for (possible bug areas):** Conditions render `c.get('value', c.get('pattern', '?'))` — any condition type that uses neither `value` nor `pattern` field shows `?`. Check if all real condition types use one of these two field names. Actions render `a.get('value', a.get('target', '?'))` — same concern.

---

#### `toggle_auto_creation_rule(rule_id: int)` — WRITE
- **Purpose:** Flip the enabled/disabled state of one auto-creation rule.
- **Prompt to Claude:** "Disable the 'Auto Movies' auto-creation rule." (just name it — Claude looks it up) Then: "Now re-enable it."
- **Expected:** Claude resolves the name to an id via `list_auto_creation_rules`, then calls `toggle_auto_creation_rule(rule_id=N)`. Returns "Rule N is now disabled." On second call returns "Rule N is now enabled." The toggle is idempotent from the UI's perspective — calling it twice returns you to the original state.
- **Edge / failure tests:**
  - "Toggle the 'Phantom Rule' auto-creation rule" (non-existent name) → Claude attempts lookup; backend 404; tool returns `Error toggling rule N: ...`
  - Toggle a rule twice in sequence → state returns to original; verify the enabled/disabled string in the response correctly reflects the final backend state (not the previous state).
- **Watch for (possible bug areas):** `result.get("enabled", "unknown")` — if the backend response omits `enabled`, the tool prints "Rule N is now unknown." Verify backend consistently returns `enabled` in the toggle response body.

---

#### `bulk_toggle_auto_creation_rules(rule_ids: list[int])` — WRITE
- **Purpose:** Toggle multiple rules at once; reports per-rule success and failures.
- **Prompt to Claude:** "Toggle the 'Auto Sports', 'Auto Movies', and 'Kids Cartoons' auto-creation rules all at once." (just name them — Claude looks up the ids)
- **Expected:** Claude resolves each name to an id via `list_auto_creation_rules`, then calls `bulk_toggle_auto_creation_rules(rule_ids=[N, M, P])`. Returns "Toggled 3/3 rules:" with one "Rule N: enabled/disabled" line per id. If any id fails, the Errors section lists each failure without aborting the rest.
- **Edge / failure tests:**
  - Mix valid names and one non-existent name (e.g., "toggle 'Auto Sports', 'Phantom Rule', and 'Auto Movies'") → Claude resolves what it can; should toggle valid rules successfully and report error for the unresolved one; final output is "Toggled 2/3 rules:" with the Errors section visible.
  - Empty name list → returns "Toggled 0/0 rules:" (no crash on empty loop).
  - Single rule named → equivalent to `toggle_auto_creation_rule`; both paths should give the same result.
- **Watch for (possible bug areas):** This tool loops and calls the toggle endpoint individually for each id — it is NOT a true bulk backend call. A backend 5xx on one id will add it to the errors list; verify the outer `except Exception` does not mask individual errors. The top-level `except` catches the whole function — if the `results` list initialization fails somehow (can't happen in practice), errors would be swallowed.

---

#### `duplicate_auto_creation_rule(rule_id: int)` — WRITE
- **Purpose:** Create a copy of an existing rule with a new id.
- **Prompt to Claude:** "Duplicate the 'Auto Sports' rule so I can modify the copy." (just name it — Claude looks it up)
- **Expected:** Claude resolves the name to an id via `list_auto_creation_rules`, then calls `duplicate_auto_creation_rule(rule_id=N)`. Returns "Rule N duplicated. New rule ID: M". Run `list_auto_creation_rules` afterward to verify the new rule appears.
- **Edge / failure tests:**
  - "Duplicate the 'Phantom Rule'" (non-existent name) → Claude attempts lookup; backend 404; tool returns `Error duplicating rule N: ...`
  - Duplicate a rule, then immediately list rules → new rule should appear with a higher id and a name like "Copy of <original name>" (backend-determined naming).
- **Watch for (possible bug areas):** `result.get("id", "?")` — if backend wraps the new rule inside a `{"rule": {...}}` object, `id` would not be at the top level and returns "?". Verify the backend response shape matches the tool's expectation (top-level `id`).

---

#### `delete_auto_creation_rule(rule_id: int)` — DESTRUCTIVE
- **Purpose:** Permanently delete an auto-creation rule.
- **Prompt to Claude:** "Delete the 'Auto Sports' auto-creation rule." (just name it — Claude looks it up. Duplicate it first if you want to preserve the original.)
- **Expected:** Claude resolves the name to an id via `list_auto_creation_rules`, then calls `delete_auto_creation_rule(rule_id=N)`. Returns "Rule N deleted." Rule no longer appears in `list_auto_creation_rules`. This is irreversible.
- **Edge / failure tests:**
  - "Delete the 'Phantom Rule'" (non-existent name) → Claude attempts lookup; backend 404; tool returns `Error deleting rule N: ...`
  - Delete a rule that is referenced by existing executions → backend may return 409 or succeed; verify error is surfaced to the operator, not swallowed.
  - Delete a rule, then call `get_auto_creation_rule` by the same name → must return a clean error, not crash.
- **Watch for (possible bug areas):** The DELETE call returns `None` (204 No Content); `await client.call_endpoint(...)` returns `None` for 204 responses. The tool does not check the return value — this is correct behavior, but verify the success message always prints and is not conditional on the response.

---

#### `create_auto_creation_rule(name: str, conditions: list[dict], actions: list[dict], ...)` — WRITE
- **Purpose:** Create a new auto-creation rule with conditions, actions, and optional configuration.
- **Prompt to Claude:** "Create an auto-creation rule called 'USA Sports HD' that matches streams whose group name contains 'USA | Sports' and whose name contains 'HD', creates a group called 'Sports HD', and creates a channel named after the stream. Set priority to 10 and enable it."
- **Expected:** Claude calls `create_auto_creation_rule` with:
  - `name="USA Sports HD"`
  - `conditions=[{"type": "stream_group_contains", "value": "USA | Sports", "connector": "and"}, {"type": "stream_name_contains", "value": "HD"}]`
  - `actions=[{"type": "create_group", "name_template": "Sports HD", "if_exists": "use_existing"}, {"type": "create_channel", "name_template": "{stream_name}", "if_exists": "merge"}]`
  - `priority=10`, `enabled=True`
  - Returns "Created auto-creation rule 'USA Sports HD' (id=N)."
- **Edge / failure tests:**
  - Omit `conditions` or `actions` → backend validation error (400/422); tool surfaces it: `Error creating rule: POST /api/auto-creation/rules -> HTTP 422 ...`
  - Pass an invalid condition type (e.g., `{"type": "invalid_type", "value": "x"}`) → backend may accept or reject; verify behavior.
  - Pass `orphan_action="explode"` (invalid value) → backend validation should reject with 422.
  - Create rule with `quality_tie_break_order` or `match_scope_target_group` fields (declared in `_AC_RULE_CREATE_FIELDS` but NOT in the tool's parameter list) → these fields cannot currently be set via the MCP tool, which is a gap. Document as a finding.
  - Create rule with `normalization_group_ids=[999]` (non-existent group) → verify backend response.
- **Watch for (possible bug areas):** The tool builds `payload` with all non-None fields hardcoded into the dict, then conditionally adds optional fields. The `result.get("rule", result)` pattern — if the backend returns the new rule at the top level (not nested under `"rule"`), `rule.get("id", "?")` still works. But if the backend wraps differently, the id returns "?". Also: `quality_tie_break_order` and `match_scope_target_group` are registered in `_AC_RULE_CREATE_FIELDS` (backend accepts them) but are NOT parameters in the MCP tool — operators cannot set these via MCP. This is a silent feature gap.

---

#### `update_auto_creation_rule(rule_id: int, ...)` — WRITE
- **Purpose:** Partially update an existing rule; only supplied fields are changed (sparse PATCH semantics, but sent as PUT).
- **Prompt to Claude:** "Update the 'Auto Sports' rule: change its priority to 5 and enable it." (just name it — Claude looks it up)
- **Expected:** Claude resolves the name to an id via `list_auto_creation_rules`, then calls `update_auto_creation_rule(rule_id=N, priority=5, enabled=True)`. Payload is `{"priority": 5, "enabled": True}` (only supplied fields). Returns "Updated rule 'Auto Sports' (id=N). Changed: priority, enabled".
- **Edge / failure tests:**
  - "Update the 'Auto Sports' rule with no changes" (pass no optional fields) → tool returns "No fields to update." without making any backend call. Verify this early-exit path.
  - "Update the 'Phantom Rule'" (non-existent name) → Claude attempts lookup; backend 404; tool returns `Error updating rule N: ...`
  - Update `conditions` with a full replacement list → the entire conditions list is replaced (not merged). Verify the operator is not surprised by this behavior (document it clearly).
  - `enabled=False` — this is handled correctly because the None-exclusion loop uses `if value is not None:`, so `False` is included. Confirm that `enabled=False` actually appears in the payload (not silently dropped).
  - `normalization_group_ids=[]` (empty list) — passes the `if value is not None:` check since `[]` is not `None`, so it IS sent. Verify the backend accepts an empty list to clear normalization groups.
- **Watch for (possible bug areas):** The sparse-field loop `if value is not None:` correctly handles `False` and `0` as valid values. However, if an operator wants to explicitly unset a field (e.g., clear `description` to `None`), they cannot — passing `description=None` is the same as not passing it. This is a known limitation. Also: the underlying HTTP method is PUT (full replace on the backend), not PATCH — confirm the backend's `UpdateAutoCreationRuleRequest` treats missing fields as "no change", or this could silently overwrite unspecified fields to defaults.

---

#### `list_auto_creation_executions(limit: int = 10)` — READ-ONLY
- **Purpose:** List recent auto-creation pipeline execution records with status, channel count, and timestamp.
- **Prompt to Claude:** "Show me the last 5 auto-creation pipeline runs."
- **Expected:** Claude calls `list_auto_creation_executions(limit=5)`. Returns "Recent executions (N):" with one line per execution: `#id: status — N channels (dry run) (timestamp)`. Dry-run executions are labeled "(dry run)".
- **Edge / failure tests:**
  - No executions yet → returns "No auto-creation executions found."
  - `limit=1` → returns only the most recent execution.
  - `limit=50` → returns up to 50 entries; verify the limit is passed as a query parameter correctly.
- **Watch for (possible bug areas):** The tool reads `ex.get("channels_created", ex.get("created", 0))` — if the backend uses a different field name, channel count displays as 0 silently. Also reads `ex.get("created_at", ex.get("timestamp", "?"))` — verify which field the backend actually returns. The `result.get("executions", []) if isinstance(result, dict)` unwrap means a bare list response would be used directly — check backend response shape.

---

#### `rollback_auto_creation(execution_id: int)` — DESTRUCTIVE
- **Purpose:** Roll back a live (non-dry-run) execution by deleting all channels it created and restoring modified entities.
- **Prompt to Claude:** "Roll back the most recent live auto-creation run." (Get a real non-dry-run execution id from `list_auto_creation_executions` first — e.g., "Roll back execution #7 from yesterday's run.")
- **Expected:** Claude calls `rollback_auto_creation(execution_id=7)`. Returns "Execution 7 rolled back. N channels deleted." Backend deletes created channels, restores modified entities, and sets the execution status to "rolled_back". Timeout is 300 seconds.
- **Edge / failure tests:**
  - "Roll back an execution that doesn't exist" → backend returns 400 with "Execution not found"; tool returns `Error rolling back execution N: POST /api/auto-creation/executions/N/rollback -> HTTP 400 Not Found: Execution not found`
  - "Roll back a dry-run execution" → backend engine returns `{"success": False, "error": "Cannot rollback a dry-run execution"}`, router raises HTTP 400; tool must surface this error clearly, NOT silently say "0 channels deleted".
  - "Roll back an execution that's already been rolled back" → backend returns 400 "Execution already rolled back"; tool must surface this, not appear to succeed.
  - "Roll back an execution that is currently running" → behavior is undefined; test and document.
- **Watch for (possible bug areas):** The MCP tool reads `result.get("deleted", result.get("channels_deleted", 0))` — the backend engine method returns `entities_removed` and `entities_restored`, NOT `deleted` or `channels_deleted`. This is a field name mismatch: the tool will always display "0 channels deleted" even on a successful rollback. This is a likely regression for 0.17.2. Verify by checking `auto_creation_engine.py:rollback_execution` return dict against what the tool reads.

---

#### `analyze_auto_creation_rules(bundle_path: str | None = None)` — READ-ONLY
- **Purpose:** Lint all live auto-creation rules (or rules from a debug bundle tar.gz) and return a markdown findings report.
- **Prompt to Claude (live):** "Analyze my auto-creation rules for any problems."
- **Prompt to Claude (bundle):** "Analyze the auto-creation rules in my debug bundle at /tmp/ecm-debug.tar.gz."
- **Expected (live):** Claude calls `analyze_auto_creation_rules()` with no args. POSTs to `/api/auto-creation/rules/analyze`. Returns a formatted markdown report with a summary line ("N errors, N warnings, N info") and a table per rule. If clean, returns "No findings across N rule(s) — looks clean."
- **Expected (bundle):** Claude calls `analyze_auto_creation_rules(bundle_path="/tmp/ecm-debug.tar.gz")`. Reads the file, uploads via multipart POST to `/api/auto-creation/rules/analyze/from-bundle`. Returns the same report format, labeled with the bundle filename.
- **Edge / failure tests:**
  - Bundle path that doesn't exist → returns "Bundle file not found: /path/to/file" without attempting the upload.
  - Bundle path to a file that is not a valid tar.gz → backend will return an error; verify it surfaces cleanly.
  - Live analysis with zero rules → "No findings across 0 rule(s) — looks clean."
  - Rule with a regex like `.*` as a `stream_name_matches` condition → analyzer should flag it as `REGEX_TRIVIALLY_MATCHES_ALL`; verify the finding appears in the table.
- **Watch for (possible bug areas):** The bundle upload path uses `client.post_multipart(...)` which is marked `# contract-exempt` in the code (multipart doesn't go through `call_endpoint`). This means the endpoint contract guard does NOT protect it — a URL typo or field name change would silently fail at runtime. Verify the hardcoded path `/api/auto-creation/rules/analyze/from-bundle` is correct. Also: the `_format_analyze_result` renderer uses `summary.values()` without guaranteeing the `error`/`warning`/`info` keys exist — if the backend omits one, it silently shows 0. Test with a response that omits `info`.

---

#### `get_auto_creation_debug_bundle()` — READ-ONLY
- **Purpose:** Returns static instructions about the debug bundle (endpoints and UI path) and a description of its contents.
- **Prompt to Claude:** "How do I get the auto-creation debug bundle?"
- **Expected:** Claude calls `get_auto_creation_debug_bundle()`. Returns a hardcoded string describing the two HTTP endpoints (`POST /api/auto-creation/debug-bundle` → 202 + job_id, then `GET /api/auto-creation/debug-bundle/{job_id}`) and the UI path, plus a list of bundle file contents (channels.json, rules.yaml, normalization_rules.yaml, channels.csv, settings.json, task_schedules.json, channel_groups_diagnostic.json, logs.txt, manifest.json). No backend call is made.
- **Edge / failure tests:**
  - This tool makes no network call and has no parameters — it cannot fail at runtime. The only risk is content staleness (if bundle contents change and the static string is not updated).
  - Verify the output mentions that credentials are redacted in settings.json.
- **Watch for (possible bug areas):** This is a pure static-string tool. If the debug bundle endpoint paths change in a future release, this tool's hardcoded text will be stale. Not a runtime bug, but a documentation drift risk. Confirm the two bundle API paths are still correct against the current backend.

---

## Tasks & Schedules

---

#### `list_tasks()` — READ-ONLY
- **Purpose:** List all registered scheduled tasks with their id, enabled state, status, and last run time.
- **Prompt to Claude:** "Show me all scheduled tasks."
- **Expected:** Claude calls `list_tasks()`. Returns "Found N tasks:" with one line per task: `name (id=task_id) — enabled/disabled, status: idle/running, last run: timestamp/never`. If no tasks, returns "No tasks configured."
- **Edge / failure tests:**
  - No tasks configured → "No tasks configured."
  - A task that is currently running → status shows "running", not "idle".
  - Backend returns `task_id` vs `id` inconsistency → tool reads `t.get("task_id", t.get("id", "?"))` — both field names are handled.
- **Watch for (possible bug areas):** `t.get("enabled")` returning `None` (field missing) renders as "disabled" because `"enabled" if t.get("enabled") else "disabled"` treats None as falsy. If the backend sometimes omits `enabled`, tasks appear disabled when they may not be.

---

#### `run_task(task_id: str)` — WRITE
- **Purpose:** Immediately trigger a scheduled task to run outside its schedule.
- **Prompt to Claude:** "Run the M3U Refresh task right now." (task ids like `m3u_refresh` are the real task names — use `list_tasks` to confirm the exact id)
- **Expected:** Claude calls `run_task(task_id="m3u_refresh")`. Returns "Task 'm3u_refresh' started. Status: running. <message>". The task starts asynchronously — the tool returns quickly, the task runs in the background.
- **Edge / failure tests:**
  - "Run the 'nonexistent_task'" → backend 404; tool returns `Error running task 'nonexistent_task': POST /api/tasks/nonexistent_task/run -> HTTP 404 ...`
  - Run a task that is already running → backend may return an error or start a second instance; verify behavior.
  - Backend returns a plain (non-dict) response → tool falls back to "Task 'X' started." without crashing.
- **Watch for (possible bug areas):** The `tasks_run` endpoint declares `request_fields=frozenset({"schedule_id", "parameters"})` — the MCP tool never sends these optional fields. This is fine since the tool sends no body at all (body=None). Verify the tool correctly calls with no body. The response `status_info` only appears if `status` is non-empty — verify the backend consistently includes `status` in the run response.

---

#### `cancel_task(task_id: str)` — DESTRUCTIVE
- **Purpose:** Cancel a currently running task; safe-no-op if the task is not running.
- **Prompt to Claude:** "Cancel the Stream Probe task." (Start it first with `run_task` to have something to cancel. Use `list_tasks` to confirm the exact task id.)
- **Expected:** Claude calls `cancel_task(task_id="stream_probe")`. Returns "Task 'stream_probe' cancelled. <message>" on success. The backend engine sets status to "cancelled" and records a journal entry.
- **Edge / failure tests:**
  - Cancel the Stream Probe task when it is not running → backend returns `{"status": "not_running", "message": "Task stream_probe is not running"}` as HTTP 200 (not 404). The MCP tool reads `result.get("message", "")` and returns "Task 'stream_probe' cancelled. Task stream_probe is not running" — which is misleading (says "cancelled" but wasn't). Verify this messaging.
  - Cancel a task with a non-existent id → backend raises 404; tool returns `Error cancelling task 'bad_id': POST /api/tasks/bad_id/cancel -> HTTP 404 Not Found: ...`
  - Cancel a task that just finished (race condition) → same as "not running" case above.
- **Watch for (possible bug areas):** The backend engine returns `{"status": "not_running", ...}` as HTTP 200 — the router does NOT raise a 404 for this case (only raises 404 for "not_found" status). The MCP tool always prepends "Task 'X' cancelled." regardless of the status field in the response — so a not-running cancel appears to succeed with a confusing combined message. This is a usability bug worth flagging.

---

#### `get_task_history(task_id: str | None = None, limit: int = 10)` — READ-ONLY
- **Purpose:** Return execution history for a specific task or all tasks combined.
- **Prompt to Claude (specific):** "Show me the last 5 runs of the M3U Refresh task."
- **Prompt to Claude (all):** "Show me the last 20 task runs across all tasks."
- **Expected (specific):** Claude calls `get_task_history(task_id="m3u_refresh", limit=5)`. Returns "Task history (N entries):" with lines: `task_name: status (Xs) — timestamp`.
- **Expected (all):** Claude calls `get_task_history(limit=20)` (no task_id). Uses the `tasks_history_all` endpoint at `/api/tasks/history/all`.
- **Edge / failure tests:**
  - "Show me the history for the M3U Refresh task" when it has never run → "No task history for task 'm3u_refresh'."
  - `limit=100` → verify only the declared `limit` query param is sent (the `offset` param exists in the contract but is not exposed in the tool — it cannot be paginated).
  - `get_task_history()` with no args (all tasks, default limit=10) → must use `tasks_history_all` endpoint, not the per-task endpoint with `task_id=None`.
- **Watch for (possible bug areas):** When `task_id=None`, the tool calls `tasks_history_all` endpoint — but `limit` is passed only to the per-task branch's `query` dict; the all-tasks branch also passes `query={"limit": limit}` correctly. Verify both branches. The `history` unwrap: `result.get("history", []) if isinstance(result, dict) else result` — if the backend returns a plain list for the all endpoint, it is used directly; verify the actual response shape.

---

#### `list_task_schedules(task_id: str)` — READ-ONLY
- **Purpose:** List all configured schedules for a specific task (type, description, enabled, next run).
- **Prompt to Claude:** "Show me the schedules for the M3U Refresh task."
- **Expected:** Claude calls `list_task_schedules(task_id="m3u_refresh")`. Returns "Schedules for 'm3u_refresh' (N):" with lines `#id: schedule_type — description (enabled/disabled), next: timestamp`. If no schedules, returns "No schedules configured for task 'm3u_refresh'."
- **Edge / failure tests:**
  - Unknown task id → backend may return 404 or empty list; verify tool handles both without crashing.
  - Task with multiple schedules of different types (interval, daily, weekly) → all appear in the list.
  - `next_run_at` field missing from a schedule → fallback to `next_run`; if both absent, shows "?".
- **Watch for (possible bug areas):** The tool reads `schedules if isinstance(schedules, list) else schedules.get("schedules", [])` — both bare list and wrapped `{"schedules": [...]}` responses are handled. The `enabled` rendering: `"enabled" if s.get("enabled") else "disabled"` — same falsy-None issue as `list_tasks`. A schedule missing `enabled` shows as "disabled".

---

#### `create_task_schedule(task_id: str, schedule_type: str, ...)` — WRITE
- **Purpose:** Create a new schedule for a task using one of the supported types: interval, daily, weekly, biweekly, or monthly.
- **Prompt to Claude (interval):** "Schedule the Stream Probe task to run every 4 hours."
- **Prompt to Claude (daily):** "Schedule M3U Refresh to run daily at 3:30 AM."
- **Prompt to Claude (weekly):** "Schedule the M3U Refresh task every Monday and Wednesday at 2:00 AM."
- **Expected (interval):** Claude calls `create_task_schedule(task_id="stream_probe", schedule_type="interval", interval_seconds=14400)`. Returns "Schedule created for 'stream_probe': <description> (id=N)".
- **Expected (daily):** `create_task_schedule(task_id="m3u_refresh", schedule_type="daily", schedule_time="03:30")`.
- **Expected (weekly):** `create_task_schedule(..., schedule_type="weekly", days_of_week=[2, 4], schedule_time="02:00")` (days: 0=Sunday, 1=Monday, 2=Tuesday...).
- **Edge / failure tests:**
  - `schedule_type="interval"` with `interval_seconds=0` → backend Pydantic validator rejects with "interval_seconds must be > 0"; tool surfaces the 422 error.
  - `schedule_type="interval"` with `interval_seconds=None` (omit it) → backend Pydantic validator rejects; tool surfaces error.
  - `schedule_type="cron_expression"` (the old removed type) → backend rejects it; confirm the rejection is clear, not a silent 500.
  - `schedule_type="daily"` with no `schedule_time` → backend may default or reject; verify.
  - `schedule_type="monthly"` with `day_of_month=-1` → "last day of month" — verify backend accepts this value.
  - `schedule_type="weekly"` with `days_of_week=[0, 6]` (Sunday and Saturday) → valid; verify.
- **Watch for (possible bug areas):** The tool does NOT expose the `timezone` parameter even though it is in the endpoint contract (`tasks_create_schedule` has `timezone` in `request_fields`). Schedules created via MCP have no timezone — they use the server default. Also: `days_of_week` day numbering (0=Sunday..6=Saturday per the docstring) must match the backend's convention — if the backend uses 1=Monday ISO convention, there is a day-off-by-one bug. Verify the day numbering convention matches backend.

---

#### `delete_task_schedule(task_id: str, schedule_id: int)` — DESTRUCTIVE
- **Purpose:** Delete a specific task schedule and verify it is gone via a read-back check.
- **Prompt to Claude:** "Delete schedule #3 from the M3U Refresh task." (Get real schedule ids from `list_task_schedules` first.)
- **Expected:** Claude calls `delete_task_schedule(task_id="m3u_refresh", schedule_id=3)`. Deletes the schedule, then reads back the schedule list and confirms schedule 3 is absent. Returns "Schedule 3 deleted from task 'm3u_refresh'."
- **Edge / failure tests:**
  - Non-existent schedule_id → backend returns 404; tool returns `Error deleting schedule: DELETE /api/tasks/m3u_refresh/schedules/3 -> HTTP 404 Not Found: Schedule 3 not found for task m3u_refresh`
  - Non-existent task id → backend 404; same error surfacing.
  - Delete the last schedule for a task → task now has zero schedules; still runs on-demand but no longer on a schedule. Verify the read-back returns an empty list cleanly.
  - Simulate a partial failure: delete succeeds (204) but the read-back list call fails → `still_present` is set to `None` (not `True`), so the WARNING is NOT shown even though confirmation is unavailable. The success message is returned. This is a silent gap — document it.
- **Watch for (possible bug areas):** The read-back confirms deletion: if `still_present is True`, emits a WARNING. But if the read-back call throws, `still_present = None` and the function returns success anyway — this masks network errors on the confirmation step. Also: `any(isinstance(s, dict) and s.get("id") == schedule_id for s in items)` — if the backend returns schedule ids as strings instead of ints, the `==` comparison fails and `still_present` is always `False`, making it appear deleted even if it isn't.

---

## Stats & Analytics

#### `get_channel_stats()` — READ-ONLY
- **Purpose:** Get channel viewing statistics including active viewers, stream status, and media-server attribution (Emby/Plex/Jellyfin user names, client IP, and provider name). Updated in 0.17.2 to surface media-server context alongside viewer counts.
- **Prompt to Claude:** "Who's watching right now, and on which media server or device?"
- **Expected:** Claude calls `get_channel_stats()`. Response lists total channel count, active channel count, and for each active channel: channel name, viewer count, media-server user names (e.g., "alice via Emby"), client IP, and provider name where available. Example: "Channel Stats (3 active of 47 total): Active channels: BBC One — 2 viewer(s) [alice via Emby · 192.168.1.10 · Provider A], CNN — 1 viewer(s)". If no channels are active, returns "No active channels."
- **Edge / failure tests:**
  - "Show me channel stats" when all streams are idle → expects "No active channels." (not an error; the tool explicitly returns that string when `active` list is empty)
  - "Show me channel stats" when the stats endpoint returns an empty list (`[]`) → expects "No active channels." — verify the `if not channels:` branch fires correctly, not `"No channel statistics available."` (that branch requires a falsy body, not an empty list)
  - "Show me channel stats" when Dispatcharr is unreachable → expects a user-facing error string: "Error getting channel stats: ..." with the underlying exception detail surfaced through `_http_error`.
  - "Who's watching?" when media-server attribution fields (`emby_username`, `client_ip`, `provider_name`) are absent from the backend response → tool should degrade gracefully and show viewer count only, no crash.
- **Watch for (possible bug areas):**
  - The tool handles both a bare list and a `{"channels": [...]}` dict from the backend. If the backend changes its response shape, the wrong branch fires silently and the `active` filter runs on a dict's values instead of a list — no error, just wrong output.
  - `channel_name` vs `name` field fallback: if neither key exists on a channel dict, the tool shows "Unknown" with no error. Active viewer count is correct but the channel is unnamed in the output.
  - New in 0.17.2: media-server attribution fields may vary by Dispatcharr version. If the Dispatcharr instance is older and omits these fields, the tool must not raise KeyError — verify all new field reads use `.get()` with a safe default.

---

#### `get_top_watched(limit: int = 10)` — READ-ONLY
- **Purpose:** Get the most-watched channels ranked by total viewing time.
- **Prompt to Claude:** "Show me the top 5 most-watched channels by total viewing time."
- **Expected:** Claude calls `get_top_watched(limit=5)`. Response is a ranked list: "Top 5 most-watched channels: 1. ESPN — 42.3h watched, 18 unique viewers …". Hours are computed from `total_watch_seconds` or `total_watch_time`, whichever is present.
- **Edge / failure tests:**
  - "Show me the top 50 most-watched channels" → Claude passes `limit=50`. Verify the backend returns up to 50 and the tool does not truncate further (the tool slices `items[:limit]` after the API response, so if the API caps at 20 only 20 are shown, which may confuse the operator).
  - "Which channels have the most viewers?" when no watch data exists → expects "No watch data available."
  - "Show me the top 0 most-watched channels" → `limit=0`; result depends on backend behavior. The tool slices `items[:0]` producing an empty list — verify the response is "No watch data available." or an empty-but-valid ranked list, not a crash.
- **Watch for (possible bug areas):**
  - Double-application of `limit`: the tool sends `limit` as a query param to the backend AND then slices the response at `[:limit]`. If the backend ignores the limit and returns all channels, only the first `limit` items are shown — silent truncation. If the backend sends 5 when 10 were requested (e.g., fewer channels exist), the header "Top 10 most-watched channels" is misleading because `len(items)` may be less than `limit`.
  - `total_watch_seconds` vs `total_watch_time` fallback: both field names are tried; if neither is present, hours display as `0.0h` with no error.

---

#### `get_bandwidth()` — READ-ONLY
- **Purpose:** Get current bandwidth usage statistics across all channels.
- **Prompt to Claude:** "What's the current bandwidth usage for ECM? Show me today, this week, and all time."
- **Expected:** Claude calls `get_bandwidth()`. Response shows today, this week, this month, and all-time totals in human-readable units (B/KB/MB/GB/TB), plus today's peak bitrate in/out if available. Example: "Bandwidth Usage: Today: 14.2 GB, This Week: 87.5 GB …".
- **Edge / failure tests:**
  - "Show bandwidth" on a fresh install with zero usage → expects all fields at "0 B"; peak bitrate lines should not appear (they're conditional on `peak_in or peak_out` being truthy).
  - "Show bandwidth" when the backend returns a non-dict (e.g., the stats endpoint returns a list) → calling `.get()` on a list raises `AttributeError`; this would be caught by the outer `except Exception as e` and returned as "Error getting bandwidth: …" — verify the error is surfaced rather than crashing the tool call entirely.
  - "Show bandwidth stats" when `bytes_val` is a very large number (PB range) → verify the `fmt()` loop correctly exits after "TB" and falls through to "PB" without IndexError.
- **Watch for (possible bug areas):**
  - The `fmt()` function modifies `bytes_val` in place across loop iterations (divides by 1024 each cycle). If the value is exactly at a boundary it returns the correct unit, but the final `return f"{bytes_val:.1f} PB"` fires after the loop exhausts — any value ≥ 1024 TB is formatted as PB, which is correct only for PB-scale values. No crash, but semantics are unusual.
  - No field-presence check: if the backend response lacks `today`, `this_week`, etc., those default to `0` via `.get(..., 0)` — silent zeros, not an error. The operator might not notice a misconfigured backend.

---

#### `get_popularity_rankings(limit: int = 10)` — READ-ONLY
- **Purpose:** Get channel popularity rankings with scores and trending data.
- **Prompt to Claude:** "Show me the popularity rankings for channels — top 20 please."
- **Expected:** Claude calls `get_popularity_rankings(limit=20)`. Response lists channels with popularity scores and trend arrows: "Channel Popularity Rankings (150 total, showing top 20): ESPN — score: 92.4 ↑ …". Trend icons appear only when `trend` field equals "up" or "down".
- **Edge / failure tests:**
  - "Show me popularity rankings" when no channels have viewing activity → expects "No popularity data available. Channels need viewing activity first."
  - "Show me popularity rankings" using `limit=1` → Claude passes `limit=1`; verify only one entry is returned and the header says "showing top 1".
  - "Show me popularity rankings" when backend returns a bare list instead of `{"rankings": [...], "total": N}` → the tool correctly handles both via the ternary; verify `total` falls back to `len(rankings)` and the header is still correct.
- **Watch for (possible bug areas):**
  - `score` formatted with `.1f` — if the backend returns `None` for score, this raises `TypeError: unsupported format character`. The outer `except` catches it but the error message leaks backend field names.
  - `offset` is declared as a query param in the endpoint contract (`stats_popularity_rankings` has `query_params=frozenset({"limit", "offset"})`) but the tool never sends `offset`. The tool always fetches from position 0 — there is no pagination support for the operator.

---

#### `get_watch_history(limit: int = 20, channel_id: str | None = None, ip_address: str | None = None, days: int | None = None)` — READ-ONLY
- **Purpose:** Get recent channel watch history with optional filters.
- **Prompt to Claude:** "Show me the last 10 watch history entries for the past 7 days."
- **Expected:** Claude calls `get_watch_history(limit=10, days=7)`. Response shows entries with channel name, watch duration in minutes, IP address, optional username, active/done status, and connection timestamp. Summary stats (unique channels, viewers, total hours) appear when provided by the backend.
- **Edge / failure tests:**
  - "Show me watch history for the 'ESPN' channel" (just name it — Claude resolves the channel id) → Claude calls `get_watch_history(channel_id="<resolved-id>")`; confirm filter is applied and only entries for that channel appear.
  - "Show me watch history filtered by IP 192.168.1.50" → Claude passes `ip_address="192.168.1.50"`; verify the query param is sent correctly.
  - "Show me watch history" when there are no history entries → expects "No watch history available."
  - "Show me watch history for the last 0 days" → `days=0` is falsy in Python — the tool's `if days:` guard skips it, effectively removing the filter. This is a silent no-op that may surprise the operator expecting "no results" for an impossible date range.
- **Watch for (possible bug areas):**
  - The tool sends `page_size=limit` but the endpoint contract declares the param as `page_size`. This is correct and consistent with the contract. However, there is no `page` param sent, so pagination always starts from page 1. Operators with large history cannot page forward through the MCP tool.
  - `ip_address` is shown in the output for every watch entry — this is identifiable information for the operator's own viewers. Not a credentials leak, but worth noting in a privacy context.
  - `days=0` treated as falsy (no filter applied) is a semantic bug: the operator may intend "show me entries from today only" and instead get all history.

---

#### `get_unique_viewers()` — READ-ONLY
- **Purpose:** Get unique viewer counts and connection statistics.
- **Prompt to Claude:** "How many unique viewers have connected to ECM? Show me the breakdown by channel."
- **Expected:** Claude calls `get_unique_viewers()`. Response shows total unique viewers, today's unique viewers, total connections, and average watch time. If the per-channel breakdown endpoint is available, also lists top 10 channels by unique viewer count. If the per-channel endpoint fails, only the totals are shown (graceful degradation).
- **Edge / failure tests:**
  - "How many unique viewers?" on a fresh install → expects all counts at 0; average watch time line is suppressed (`if avg:` guard). Verify no crash on zero values.
  - "How many unique viewers?" when `stats_unique_viewers_by_channel` returns a 404 (endpoint not implemented on this Dispatcharr version) → the inner `except` catches it silently and logs at DEBUG level. Confirm the outer result still returns the totals section intact. This is the most likely scenario for a new deployment.
  - "Show unique viewers" when the primary stats endpoint is unreachable → outer `except` surfaces error: "Error getting unique viewers: …"
- **Watch for (possible bug areas):**
  - The per-channel breakdown endpoint (`/api/stats/unique-viewers-by-channel`) is called with NO query params — but the endpoint contract declares `days` and `limit` as available params. The tool always fetches the default window with no limit. If the backend returns a large list, only the first 10 are shown in the MCP output (sliced at `[:10]`), but the operator has no way to request more.
  - Silently swallowed error for per-channel breakdown (logged only at DEBUG) means operators won't know this supplementary data failed unless they check logs.

---

#### `compute_stream_sort(channels: list[dict], mode: str = "smart")` — READ-ONLY
- **Purpose:** Compute optimal stream sort order for channels using smart sorting criteria.
- **Prompt to Claude:** "Compute the smart sort order for the 'ESPN' channel — it has three streams. Which order should they be in?" (Claude resolves the channel name to an id and lists streams via other tools, then calls compute_stream_sort with the resolved ids.)
- **Expected:** Claude calls `compute_stream_sort(channels=[{"channel_id": N, "stream_ids": [M, P, Q]}], mode="smart")`. Response lists sorted stream IDs per channel, and flags whether the order changed: "Stream Sort Results (1 channels, 1 changed): Channel N: [P, M, Q] (changed)". Uses a 60-second timeout.
- **Edge / failure tests:**
  - "Compute sort for the 'ESPN' channel using resolution mode" → Claude passes `mode="resolution"`; verify the mode is sent in the request body and the sort results differ from smart mode.
  - "Compute sort with an invalid mode like 'banana'" → backend returns a validation error; the outer `except` catches it and returns "Error computing stream sort: …". Confirm the error message is informative (the `_http_error` helper surfaces FastAPI 422 detail).
  - "Compute sort for a channel with no streams" → `stream_ids=[]`; behavior depends on backend — likely returns an unchanged result with empty sorted_stream_ids. Verify no crash.
  - "Compute sort for 50 channels at once" → sends a large body; verify the 60-second timeout is respected and a timeout surfaces as a clean error rather than a hung tool call.
  - "Compute sort for a channel that doesn't exist" → backend likely returns 404 or empty results; verify graceful handling.
- **Watch for (possible bug areas):**
  - `channels` is a raw `list[dict]` — FastMCP passes it as-is from the Claude model's JSON. If Claude constructs a malformed dict (missing `channel_id` or `stream_ids`), the backend returns a 422. The error path surfaces it, but the operator gets a JSON-validation error message with no guidance on correct shape.
  - The tool sends `{"channels": channels, "mode": mode}` — both fields are in `request_fields` per the endpoint contract. However, if `channels` is an empty list, the backend may return `{"results": []}`, causing the tool to return "No sort results." — verify this is handled and doesn't error.
  - Valid `mode` values per the docstring: `smart`, `resolution`, `bitrate`, `framerate`, `video_codec`, `m3u_priority`, `audio_channels`. The tool sends any string the user provides — no validation at the MCP layer.

---

#### `get_provider_stats(metric: str = "buffering", window: str = "7d", bucket: str = "hour", top_n: int = 50)` — READ-ONLY
- **Purpose:** Per-provider Stats v2 data (Providers panel). Returns aggregated metrics broken down by provider. `metric` ∈ `buffering` | `watch_time` | `channel_heatmap` | `bitrate`; `window` ∈ `7d` | `30d` | `90d`; `bucket` ∈ `hour` | `day` (applies to `buffering` and `bitrate`); `top_n` limits results for `channel_heatmap`. New in 0.17.2 (GET `/api/stats/providers/*`). Admin-only; MCP key is admin-equivalent.
- **Prompt to Claude:** "Which provider is buffering the most over the last week?" — or — "Show me watch time by provider over the last month."
- **Expected:** Claude calls e.g. `get_provider_stats(metric="buffering", window="7d")`. Response shows a ranked list of providers with their buffering event counts or watch-time totals, bucketed by hour or day as appropriate. Example: "Provider Stats — buffering (7d): 1. Provider A — 142 events, 2. Provider B — 87 events …". For `channel_heatmap`, returns a per-provider table of the top-N channels by activity.
- **Edge / failure tests:**
  - "Which provider buffered the most this month?" → Claude calls `get_provider_stats(metric="buffering", window="30d")`. Verify the `window` query param is sent correctly.
  - "Show me bitrate by provider, bucketed by day, over the last 90 days" → Claude calls `get_provider_stats(metric="bitrate", window="90d", bucket="day")`. Verify all three params are sent.
  - "Show me the channel heatmap for providers — top 10 channels" → Claude calls `get_provider_stats(metric="channel_heatmap", top_n=10)`. Verify `top_n` is sent as a query param.
  - "Show provider stats" with no providers configured → expects "No provider stats available." or an empty table, not a crash.
  - Invalid `metric` value (e.g., `metric="latency"`) → backend returns 422 or 400; tool surfaces the error cleanly.
- **Watch for (possible bug areas):** New in 0.17.2 — verify the endpoint paths under `/api/stats/providers/` are correct for each metric variant. `bucket` is only meaningful for `buffering` and `bitrate`; verify the tool either ignores it or the backend ignores it gracefully for `watch_time` and `channel_heatmap`. If the backend returns an empty list for a window with no data, the tool must not crash on an empty loop.

---

#### `get_user_watch_time(group_by: str = "total", user_id: int | None = None)` — READ-ONLY
- **Purpose:** Per-user watch-time totals. `group_by` ∈ `total` | `day`. When `group_by="total"` returns a single cumulative total per user; when `group_by="day"` returns a time series broken down by day. `user_id` optionally scopes to one user. New in 0.17.2 (GET `/api/stats/watch-time`). Admin-only; MCP key is admin-equivalent.
- **Prompt to Claude:** "How much has each user watched in total?" — or — "Show me daily watch time per user so I can see trends."
- **Expected:** Claude calls e.g. `get_user_watch_time(group_by="total")`. Response shows a ranked list of users with cumulative watch-time hours. Example: "Watch Time by User (total): home — 214.3h, kids — 88.1h, guest — 12.0h". With `group_by="day"`, each user entry expands into a per-day series.
- **Edge / failure tests:**
  - "How much has the user 'home' watched?" → Claude resolves the user name to a `user_id` (just name it — Claude looks it up), then calls `get_user_watch_time(user_id=N)`. Verify the `user_id` query param is sent correctly.
  - "Show me daily watch time" → Claude calls `get_user_watch_time(group_by="day")`. Verify the `group_by` param is sent.
  - No watch data yet → expects "No user watch time data available." or an empty list, not a crash.
  - Invalid `group_by` value (e.g., `group_by="week"`) → backend returns 422; tool surfaces the error.
- **Watch for (possible bug areas):** New in 0.17.2 — verify the `/api/stats/watch-time` endpoint path is correct and the query param names match the backend contract. If `user_id` is resolved by name but the user list returns multiple matches (e.g., two users named "home"), Claude should ask for disambiguation rather than picking arbitrarily. Watch for integer vs. string user_id type mismatches in the query param.

---

#### `get_user_channel_breakdown(user_id: str, source: str = "dispatcharr")` — READ-ONLY
- **Purpose:** Per-channel breakdown of watch time for a single user. `source` ∈ `dispatcharr` | `emby`. Returns how much time a given user spent on each channel, which is useful for understanding viewing habits. New in 0.17.2 (GET `/api/stats/users/{dispatcharr,emby}/{id}`). Admin-only; MCP key is admin-equivalent.
- **Prompt to Claude:** "What has the user 'home' been watching, channel by channel?" (just name the user — Claude looks it up)
- **Expected:** Claude resolves the user name 'home' to a user_id, then calls `get_user_channel_breakdown(user_id="<resolved-id>", source="dispatcharr")`. Response lists channels watched by that user, ranked by time. Example: "Channel breakdown for 'home' (dispatcharr): 1. ESPN — 42.3h, 2. BBC One — 18.1h, 3. CNN — 6.4h …".
- **Edge / failure tests:**
  - "Show me channel breakdown for the user 'home' via Emby" → Claude calls `get_user_channel_breakdown(user_id="<id>", source="emby")`. Verify the correct endpoint path is used (`/api/stats/users/emby/<id>`).
  - "Show channel breakdown for a user who has never watched anything" → expects "No channel data for this user." or an empty list, not a crash.
  - Invalid `source` value (e.g., `source="plex"`) → backend returns 422 or 404; tool surfaces the error cleanly.
  - User name that does not resolve (e.g., 'phantom_user') → Claude should report it cannot find the user, not call the tool with an invalid id.
- **Watch for (possible bug areas):** New in 0.17.2 — the endpoint path forks on `source` (`/api/stats/users/dispatcharr/{id}` vs `/api/stats/users/emby/{id}`). Verify the tool routes to the correct path based on the `source` param. `user_id` is declared as `str` in the tool signature but the underlying id may be an integer from the Dispatcharr user list — verify the path construction handles both without a type error.

---

#### `get_trending(direction: str = "up", limit: int = 10)` — READ-ONLY
- **Purpose:** Channels that are trending up or down in viewer interest. `direction` ∈ `up` | `down`. New in 0.17.2 (GET `/api/stats/popularity/trending`). Admin-only; MCP key is admin-equivalent.
- **Prompt to Claude:** "What channels are trending up this week?" — or — "Which channels are losing viewers right now?"
- **Expected:** Claude calls `get_trending(direction="up", limit=10)`. Response lists channels with their trend score or velocity. Example: "Trending Up (top 10): 1. ESPN — +34% this week, 2. BBC News — +18% this week …". For `direction="down"`, lists channels with declining viewership.
- **Edge / failure tests:**
  - "What's trending down?" → Claude calls `get_trending(direction="down")`. Verify the `direction` param is sent correctly.
  - "Show me the top 25 trending channels" → Claude calls `get_trending(limit=25)`. Verify `limit` is sent as a query param.
  - No trending data yet (fresh install) → expects "No trending data available." or an empty list, not a crash.
  - Invalid `direction` value (e.g., `direction="sideways"`) → backend returns 422; tool surfaces the error.
- **Watch for (possible bug areas):** New in 0.17.2 — verify the `/api/stats/popularity/trending` endpoint path and that `direction` and `limit` are the correct query param names. If the backend returns a uniform response shape regardless of `direction` and the tool must filter client-side, verify the filtering logic is correct. Watch for `None` trend scores crashing the formatter.

---

#### `get_channel_popularity(channel_id: str)` — READ-ONLY
- **Purpose:** Popularity metrics for a single channel — score, rank, trend direction, and recent viewer counts. New in 0.17.2 (GET `/api/stats/popularity/channel/{id}`). Admin-only; MCP key is admin-equivalent.
- **Prompt to Claude:** "How popular is the 'ESPN' channel?" (just name it — Claude looks it up)
- **Expected:** Claude resolves the channel name 'ESPN' to a channel_id, then calls `get_channel_popularity(channel_id="<resolved-id>")`. Response shows the channel's popularity score, rank among all channels, trend direction, and recent viewer metrics. Example: "Popularity for 'ESPN': score 92.4, rank #3 of 150, trending ↑, 18 unique viewers this week."
- **Edge / failure tests:**
  - "How popular is the 'Phantom Channel'?" (non-existent name) → Claude cannot resolve the name; should report it cannot find the channel, not call the tool with an invalid id.
  - "How popular is the 'ESPN' channel?" when it has never been watched → backend may return a zero-score result or 404; tool must handle both without crashing.
  - Channel with `score=None` in the backend response → formatter must handle None gracefully (no `TypeError` on `.1f` format).
- **Watch for (possible bug areas):** New in 0.17.2 — verify the `/api/stats/popularity/channel/{id}` path is correct. `channel_id` is declared as `str` but ECM channel ids may be integers or UUIDs depending on the backend version — verify the path construction is type-safe. If the backend returns a 404 for a channel with no viewing history rather than a zero-score result, the error message must be user-friendly, not a raw HTTP error.

---

#### `get_activity(limit: int = 50, offset: int = 0, event_type: str | None = None)` — READ-ONLY
- **Purpose:** Recent system activity events — channel start/stop, buffering events, client connects. `event_type` optionally filters to a specific event kind. New in 0.17.2 (GET `/api/stats/activity`). Admin-only; MCP key is admin-equivalent.
- **Prompt to Claude:** "Show me recent activity on the server." — or — "Show me the last channel-start events."
- **Expected:** Claude calls e.g. `get_activity(limit=50)` or `get_activity(limit=20, event_type="channel_start")`. Response lists events in reverse-chronological order: timestamp, event type, channel name (if applicable), client IP, and any relevant detail. Example: "Recent Activity (20 events): 2026-05-22 14:32 — channel_start: ESPN (192.168.1.10), 2026-05-22 14:31 — buffering: BBC One …".
- **Edge / failure tests:**
  - "Show me only buffering events" → Claude calls `get_activity(event_type="buffering")`. Verify the `event_type` query param is sent correctly and only buffering events appear.
  - "Show me the next page of activity" → Claude calls `get_activity(offset=50)`. Verify `offset` is sent as a query param.
  - No activity yet → expects "No recent activity." or an empty list, not a crash.
  - Invalid `event_type` value (e.g., `event_type="explosion"`) → backend returns 422 or empty list; tool surfaces the result correctly.
- **Watch for (possible bug areas):** New in 0.17.2 — verify the `/api/stats/activity` endpoint path and that `limit`, `offset`, and `event_type` are the correct query param names. If `event_type=None` is sent as the literal string `"None"` rather than omitted, the backend may return 0 results silently — verify the tool omits the param entirely when the value is None. Watch for missing or inconsistent field names across different event types (a `channel_start` event may have `channel_name` while a `client_connect` event may not).

---

#### `get_channel_bandwidth(days: int = 7, limit: int = 20, sort_by: str = "bytes")` — READ-ONLY
- **Purpose:** Per-channel bandwidth consumption — bytes transferred, connection count, and total watch time — sortable by metric. `sort_by` ∈ `bytes` | `connections` | `watch_time`. New in 0.17.2 (GET `/api/stats/channel-bandwidth`). Admin-only; MCP key is admin-equivalent.
- **Prompt to Claude:** "Which channels used the most bandwidth this week?" — or — "Which channels had the most connections in the last 30 days?"
- **Expected:** Claude calls e.g. `get_channel_bandwidth(days=7, limit=20, sort_by="bytes")`. Response shows a ranked list of channels with their bandwidth totals formatted in human-readable units. Example: "Channel Bandwidth (7d, top 20, sorted by bytes): 1. ESPN — 142.3 GB, 847 connections, 2. BBC One — 87.1 GB, 612 connections …".
- **Edge / failure tests:**
  - "Which channels had the most connections this month?" → Claude calls `get_channel_bandwidth(days=30, sort_by="connections")`. Verify both `days` and `sort_by` are sent correctly.
  - "Show me channel bandwidth sorted by watch time" → Claude calls `get_channel_bandwidth(sort_by="watch_time")`. Verify the sort is applied server-side, not client-side.
  - No bandwidth data yet → expects "No channel bandwidth data available." or an empty list, not a crash.
  - Invalid `sort_by` value (e.g., `sort_by="latency"`) → backend returns 422; tool surfaces the error.
- **Watch for (possible bug areas):** New in 0.17.2 — verify the `/api/stats/channel-bandwidth` endpoint path and that `days`, `limit`, and `sort_by` are the correct query param names. Bandwidth values in the response may be in bytes (raw integer) and require the same `fmt()` helper used in `get_bandwidth()` — verify the formatter is reused, not reimplemented. If the backend caps `days` at some maximum (e.g., 90), passing `days=365` may return silently truncated results; document this if confirmed.

---
## System & Backup

#### `get_settings()` — READ-ONLY
- **Purpose:** Get current ECM settings (connection status, preferences, probe configuration).
- **Prompt to Claude:** "Show me the current ECM settings, including connection status and probe configuration."
- **Expected:** Claude calls `get_settings()`. Response shows Dispatcharr URL, connection status, theme, timezone, probe timeout/parallelism/concurrency/schedule, and notification method presence (configured/not configured). Credentials are NOT shown raw — only boolean indicators for SMTP, Discord, Telegram.
- **Edge / failure tests:**
  - "Show me ECM settings" on a fresh unconfigured install → `configured: False`, URL "Not configured"; verify no crash on missing fields.
  - "Show me ECM settings" when the backend is unreachable → outer `except` returns "Error getting settings: …".
- **Watch for (possible bug areas):**
  - **SECRET LEAKAGE RISK (HIGH) — verified clean in 0.17.2 live test:** The backend `/api/settings` response includes `discord_webhook_url` and `telegram_bot_token` in plaintext (see `backend/routers/settings.py` lines 460, 463). The MCP `get_settings()` tool only reads `s.get('discord_configured', False)` and `s.get('telegram_configured', False)` — it does NOT render these raw values. The 0.17.2 live test confirmed no secret leakage: no literal webhook URL or bot token appeared in any output or error path. **Continue to test this path** on future refactors: if a debug dump or `repr(s)` ever enters the error string, these secrets would be exposed.
  - **SECRET LEAKAGE RISK (MEDIUM) — verified clean in 0.17.2 live test:** The MCP tool renders `s.get('configured', False)` — it reads the `configured` key from the backend's SettingsResponse. The backend response also includes `mcp_api_key_configured` (a boolean) — not the key itself. The 0.17.2 live test confirmed the raw `mcp_api_key` value is not present in the backend response (SettingsResponse does not include it — only `mcp_api_key_configured`). **Retest if SettingsResponse model changes.**
  - The MCP tool output shows `Dispatcharr URL` — this may reveal internal network topology if MCP is used in a shared-Claude context.

---

#### `create_backup()` — WRITE
- **Purpose:** Create a backup of all ECM configuration (settings, database, logos).
- **Prompt to Claude:** "Create a backup of the ECM configuration right now."
- **Expected:** Claude calls `create_backup()`. The backend `/api/backup/create` endpoint triggers a WAL checkpoint and builds a zip stream — the MCP tool does NOT download the zip file (it just fires the GET and discards the streaming response). Response: "Backup created successfully. Download it from the ECM Settings page." Note: because the endpoint returns a `StreamingResponse`, `r.json()` will fail — the `try` block should raise an error unless the client handles non-JSON responses.
- **Edge / failure tests:**
  - "Create a backup now" when the backup endpoint returns the streaming zip → **critical path**: `ecm_client.get()` calls `r.raise_for_status()` then `r.json()` — a streaming zip response will cause `r.json()` to raise `json.JSONDecodeError`. This propagates through the outer `except` and returns "Error creating backup: …" instead of the success message. This is the most likely bug in this tool for this release.
  - "Create a backup now" when the ECM container has insufficient disk space → backend may return HTTP 500; verify the error detail from `_http_error` is surfaced cleanly.
  - "Create a backup now" and immediately do it again → idempotent; the backup endpoint has no lock, so two simultaneous calls each produce their own zip. No state corruption.
- **Watch for (possible bug areas):**
  - **CRITICAL:** `ecm_client.get()` always calls `r.json()` after `r.raise_for_status()`. The backup endpoint returns `StreamingResponse(media_type="application/zip")` — not JSON. `r.json()` will raise a `JSONDecodeError`. The outer `except Exception as e` catches it and returns an error string. The operator sees "Error creating backup: ..." even though the backup succeeded. This is a fundamental mismatch between the tool's design (assume JSON response) and the endpoint's reality (streaming binary). **This is the highest-priority bug candidate in this module.**
  - The success message tells the operator to "Download it from the ECM Settings page" — but the backup was streamed (and the data was discarded by the client). The UI download path is actually a separate GET from the browser. This may confuse operators who expect a file reference back from the tool.

---

#### `get_export_sections()` — READ-ONLY
- **Purpose:** List available YAML export sections (for selective backup).
- **Prompt to Claude:** "What sections can I include in a YAML export backup?"
- **Expected:** Claude calls `get_export_sections()`. Response lists available sections with keys and labels, e.g.: "Available export sections: - settings: Settings, - scheduled_tasks: Scheduled Tasks, …" (13 sections from `RESTORABLE_SECTIONS` in backup.py).
- **Edge / failure tests:**
  - "What export sections are available?" when the backend is unconfigured → the endpoint is available regardless of Dispatcharr connection; should still return the static section list.
  - "What export sections are available?" when backend returns an empty list → expects "No export sections available." Unlikely in practice since sections are hardcoded in the backend.
  - "What export sections are available?" when a section dict lacks `key` or `label` keys → `s['key']` raises `KeyError`, caught by outer `except`, returns `"Error: 'key'"`. Verify this is not a real scenario with the current backend.
- **Watch for (possible bug areas):**
  - The tool iterates `for s in sections: lines.append(f"  - {s['key']}: {s['label']}")` — no `.get()` guard, so a missing `key` or `label` raises `KeyError`. The outer `except` catches it but the error message is opaque (`Error: 'key'`).
  - Sections returned depend on the backend's `RESTORABLE_SECTIONS` dict. If the backend adds new sections that the MCP tool wasn't designed for, they appear in the list automatically — this is correct behavior, but operators should be told this list is always backend-authoritative.

---

#### `list_saved_backups()` — READ-ONLY
- **Purpose:** List saved YAML backup files on the server (created by scheduled backup task).
- **Prompt to Claude:** "List all saved backups on the server."
- **Expected:** Claude calls `list_saved_backups()`. Response: "Saved backups (N): ecm-backup-2026-05-20_030000.yaml — 142.3 KB (2026-05-20T03:00:00+00:00) …". Files are listed newest first (sorted by the backend).
- **Edge / failure tests:**
  - "List saved backups" when no scheduled backups have run yet (empty backups directory) → expects "No saved backups."
  - "List saved backups" when the `backups/` directory exists but contains no matching files → backend returns `[]`; expects "No saved backups."
  - "List saved backups" when a backup file is very large (e.g., 50 MB) → `size_kb = b.get("size_bytes", 0) / 1024` produces `51200.0 KB` — displayed verbatim. Acceptable but might be better shown as MB.
- **Watch for (possible bug areas):**
  - `b['filename']` (unguarded bracket access): if any backup entry lacks `filename`, raises `KeyError` — caught by outer `except` but with an opaque error string. Use `.get()` defensively.
  - No backend pagination: if there are hundreds of saved backups, all are returned in one response. The MCP tool shows all of them. This could produce a very long response.

---

#### `delete_saved_backup(filename: str)` — DESTRUCTIVE
- **Purpose:** Delete a saved YAML backup file from the server.
- **Prompt to Claude:** "List my saved backups, then delete the oldest one." (just name it — Claude looks up the filename from `list_saved_backups`)
- **Expected:** Claude calls `list_saved_backups()` to get a filename, then calls `delete_saved_backup(filename="ecm-backup-2026-04-07_120000.yaml")`. The tool deletes the file, then does a read-back to confirm it no longer appears in the list. On success: "Deleted backup: ecm-backup-2026-04-07_120000.yaml". On soft failure (file still in list after deletion): "WARNING: requested deletion of … but it still appears in the saved-backups list."
- **Edge / failure tests:**
  - "Delete the backup from last Tuesday" (a date with no matching file) → Claude resolves via `list_saved_backups`; if no backup matches the date, Claude reports none found rather than calling `delete_saved_backup` with a fabricated name. Backend would return HTTP 404 if called with a non-existent filename; `_http_error` surfaces it.
  - "Delete the backup from yesterday" using a path traversal attempt in the resolved name → backend layer 1 regex `_BACKUP_FILENAME_RE` rejects it with HTTP 400 ("Invalid filename"). The MCP tool surfaces the 400 as an error. Verify the tool does NOT attempt to construct the path locally.
  - "Delete the backup from this morning" when already deleted → first call succeeds; a second attempt returns HTTP 404.
- **Watch for (possible bug areas):**
  - **No confirmation prompt:** The tool proceeds immediately without asking the operator to confirm deletion. A single natural-language request like "delete old backups" could result in Claude calling this for every entry from `list_saved_backups`. This is the standard MCP pattern but the operator should be warned in the test plan.
  - The read-back confirmation calls `list_saved_backups` again — if the list call itself fails, `still_present` is set to `None` (not `True`), so the tool returns success even if the delete may not have worked. This silent skip on read-back failure means the WARNING path may not fire when it should.
  - Filename is passed directly as a path argument. The tool does no local validation — it relies entirely on the backend's regex guard. A filename like `ecm-backup-2026-05-20_030000.txt` (wrong extension) will be rejected by the backend with 400.

---

#### `get_journal(limit: int = 20, category: str | None = None)` — READ-ONLY
- **Purpose:** Get recent entries from the ECM activity journal/audit log.
- **Prompt to Claude:** "Show me the last 50 journal entries for the settings category."
- **Expected:** Claude calls `get_journal(limit=50, category="settings")`. Response: "Recent journal entries (N): [timestamp] settings/update: Changed Dispatcharr URL to http://… …". Detail is truncated to 80 characters in the display.
- **Edge / failure tests:**
  - "Show me journal entries" when the journal is empty (fresh install) → expects "No journal entries found."
  - "Show me journal entries for the channels category" → Claude passes `category="channels"`; verify the filter is sent as a query param.
  - "Show me the last 100 journal entries" → `limit=100` is sent as `page_size=100`; verify the backend respects this and returns up to 100 entries. (This was a known bug fixed in bd-vtghg Phase 2 — the old `limit` param was ignored; `page_size` is now correct.)
  - "Show me journal entries filtered by an invalid category like 'nonexistent'" → backend returns an empty list or a 400; verify graceful handling.
- **Watch for (possible bug areas):**
  - **SECRET LEAKAGE RISK (MEDIUM) — verified clean in 0.17.2 live test:** Journal entries record ECM activity including settings changes. The `detail` field (truncated to 80 chars) may contain partial credential values if a previous ECM version logged settings values without redaction. The 0.17.2 live test confirmed no credential field names with non-redacted values appeared in the `settings` category journal entries. **Retest after any settings-write code path changes**, as a future commit that logs settings values before redaction would reintroduce this risk.
  - `action_type` vs `action` field fallback: the tool tries both. If neither exists, the entry shows "?" in the action column with no error.
  - `detail[:80]` truncation: if `detail` is `None`, `None[:80]` raises `TypeError`. The fallback `e.get("detail", e.get("description", ""))` returns `""` if both are absent — safe. But if `detail` is explicitly `None` (not missing), `e.get("detail", ...)` returns `None` and `None[:80]` crashes. The outer `except` catches it but all remaining entries are lost.
  - The category filter only allows specific known values on the backend (`channels`, `m3u`, `epg`, `settings`, etc.) — typos silently return 0 results rather than an error.

---

## Notifications & Alerts

#### `list_notifications(limit: int = 20)` — READ-ONLY
- **Purpose:** List current notifications with unread count.
- **Prompt to Claude:** "Show me all my unread notifications."
- **Expected:** Claude calls `list_notifications(limit=20)`. Response: "Notifications (3 unread, 15 total): Stream probe failed (stream_stats) [NEW] — 2026-05-22T10:00:00 …". Unread notifications are marked `[NEW]`. Read ones have no marker.
- **Edge / failure tests:**
  - "Show me my notifications" when there are none → expects "No notifications."
  - "Show me the last 100 notifications" → Claude passes `limit=100`; the tool sends `page_size=100` to the backend. Verify the backend respects page_size (the backend default is 50; passing 100 may return capped results).
  - "Show me my notifications" when the backend returns a bare list instead of `{"notifications": [...], "total": N, "unread_count": N}` → the tool handles both shapes: `result.get("notifications", [])` falls back to using `result` directly if it's a list; `unread` defaults to 0. Verify unread count is 0 (not an error) in this case.
- **Watch for (possible bug areas):**
  - The tool sends `page_size=limit` but no `page=1` parameter. The endpoint contract declares `page` as a valid query param but it's never sent. The backend defaults to `page=1` — correct for the first page only. There is no way to page through notifications beyond the first page via MCP.
  - `n.get("title", n.get("message", ""))` — if both `title` and `message` are absent, the notification displays as an empty string with no label. No crash, but confusing output.
  - Notification `type` filtering is declared in the endpoint contract (`notification_type` query param) but the MCP tool never uses it. The operator cannot filter by type through MCP.

---

#### `mark_notifications_read()` — WRITE
- **Purpose:** Mark all notifications as read.
- **Prompt to Claude:** "Mark all my notifications as read."
- **Expected:** Claude calls `mark_notifications_read()`. Tool sends PATCH to `/api/notifications/mark-all-read`, then does a read-back check. On success: "All notifications marked as read." On soft failure (unread still remain): "WARNING: marked all read but N notification(s) still show as unread."
- **Edge / failure tests:**
  - "Mark all my notifications as read" when there are already no unread notifications → backend marks 0 rows, returns `{"marked_read": 0}`; the read-back check sees `unread_count=0`; tool returns "All notifications marked as read." (idempotent).
  - "Mark all my notifications as read" when the backend is transiently slow and the read-back fires before propagation → the WARNING fires even though the operation succeeded. This is a false positive risk; the operator should retry "show me my notifications" manually to confirm.
  - "Mark all my notifications as read" when the backend returns a non-dict response → `result.get("unread_count", 0) if isinstance(result, dict) else 0` — the tool handles this gracefully by defaulting to 0.
- **Watch for (possible bug areas):**
  - The read-back check sends `page_size=1` and reads `unread_count` from the response. If `unread_count` is truthy (any non-zero integer), the WARNING fires. However, the backend returns the unread count across ALL notifications (not just the first page), so this is correct behavior — any remaining unread notification triggers the WARNING regardless of pagination position.
  - Race condition: if a new notification arrives between the PATCH and the read-back GET, the WARNING fires incorrectly. This is unavoidable but should be documented in operator guidance.
  - The PATCH endpoint returns `{"marked_read": N}` (a count), not a success boolean. The tool ignores the return value of the PATCH call and only uses the read-back result. If the PATCH silently failed (returned 200 with `marked_read: 0` due to a DB issue), the tool would report "All notifications marked as read." — a false success.

---

#### `delete_all_notifications()` — DESTRUCTIVE
- **Purpose:** Delete all notifications.
- **Prompt to Claude:** "Delete all my notifications to clear the notification center."
- **Expected:** Claude calls `delete_all_notifications()`. On success: "All notifications deleted." On soft failure (notifications remain): "WARNING: requested delete-all but N notification(s) remain."
- **Edge / failure tests:**
  - "Clear all my notifications" when the inbox has both read and unread notifications → **CRITICAL BEHAVIOR BUG**: The backend `DELETE /api/notifications` defaults to `read_only=True`, which means it only deletes READ notifications. The MCP tool sends no `read_only=False` body (the endpoint contract declares no `request_fields`). Unread notifications will survive. The read-back check will show remaining notifications and trigger the WARNING — but the operator's intent ("delete all") is not fulfilled. **This is a confirmed semantic bug in the tool.**
  - "Clear all my notifications" when there are no notifications → backend returns `{"deleted": 0}`; read-back sees `total=0`; tool returns "All notifications deleted." (correct).
  - "Clear all my notifications" twice → first call removes read ones; second call removes any that were unread (if another tool or the UI marked them read between calls). Still affected by the `read_only=True` default.
- **Watch for (possible bug areas):**
  - **BUG CONFIRMED:** The endpoint contract `notifications_delete_all` has no `request_fields`, so the tool cannot send `read_only=False`. The backend default is `read_only=True`. The tool name `delete_all_notifications` and its docstring ("Delete all notifications") are misleading — it actually only deletes read notifications.
  - The read-back check reads `result.get("total", 0)` from the LIST endpoint. If unread notifications remain (due to the `read_only=True` behavior), the WARNING fires — which is accurate but may be confusing if the operator doesn't understand why unread ones weren't deleted.
  - No confirmation before destructive operation. A single "clear all notifications" prompt to Claude could trigger this immediately.

---

#### `list_alert_methods()` — READ-ONLY
- **Purpose:** List all configured alert methods (Discord, Telegram, email).
- **Prompt to Claude:** "Show me what alert methods are configured in ECM."
- **Expected:** Claude calls `list_alert_methods()`. Response: "Alert Methods (2): My Discord (id=1) — discord, enabled [error, warning], Email Alerts (id=2) — smtp, disabled [error]". Each method shows its type, enabled state, and which notification levels it handles.
- **Edge / failure tests:**
  - "Show me my alert methods" when none are configured → expects "No alert methods configured."
  - "Show me my alert methods" when the `alert-methods` endpoint is not available (older Dispatcharr) → outer `except` surfaces the HTTP error.
  - "Show me my alert methods" when a method has no levels configured (all `notify_*` fields false) → the `level_str` is empty string and the output shows the method with no level bracket.
- **Watch for (possible bug areas):**
  - **SECRET LEAKAGE RISK (LOW):** The backend `AlertMethod.to_dict()` masks credential fields in the API response (matching `_ALERT_METHOD_CREDENTIAL_KEYS`). Verify the `/api/alert-methods` response does NOT include raw `webhook_url`, `password`, `bot_token`, or `api_key` values. The MCP tool would surface whatever the API returns — if masking fails on the backend, the webhook URL appears in Claude's response.
  - `m.get("id", "?")` — method ID is shown as "?" if missing. The `test_alert_method` tool requires a numeric method ID; if the operator needs to test an alert method, they need a real integer ID from this list.
  - `method_type` vs type field: the tool tries `m.get("method_type", "?")` — if the backend field is `type` instead of `method_type`, all methods show as "?" for their type.

---

#### `test_alert_method(method_id: int)` — WRITE
- **Purpose:** Send a test notification through an alert method.
- **Prompt to Claude:** "Send a test through my 'Email' alert method." (just name it — Claude looks up the ID from `list_alert_methods`)
- **Expected:** Claude calls `list_alert_methods()` to resolve the 'Email' method name to its numeric ID, then calls `test_alert_method(method_id=<id>)`. The backend sends a test message via the configured channel (Discord webhook, email, Telegram). On success: "Test alert sent successfully. Test message dispatched." On failure: "Test alert failed: [error detail from backend]".
- **Edge / failure tests:**
  - "Test my 'Discord' alert method" when no method named 'Discord' exists → Claude resolves via `list_alert_methods`; if no match found, Claude reports none found rather than guessing an ID.
  - "Test my 'Email' alert method" when the SMTP configuration is invalid → backend attempts delivery, fails, returns `{"success": false, "message": "Connection refused"}` or similar; tool returns "Test alert failed: Connection refused".
  - "Test my 'Telegram' alert method" when the method is disabled → behavior depends on backend — may send test anyway (ignoring enabled state) or may return `success: false`. Verify which behavior ECM implements.
  - "Test my 'Email' alert method" using ID 0 (resolved edge case) → `method_id=0` is a valid integer but unlikely to match any real method; backend returns 404.
- **Watch for (possible bug areas):**
  - `result.get("success", False) if isinstance(result, dict) else False` — if the backend returns a bare `True` or non-dict on success, `success` defaults to `False` and the tool reports failure even though the test succeeded. This would cause a false negative.
  - The tool takes a raw `int` for `method_id` — FastMCP should coerce string input to int, but if Claude passes a float or string that can't be coerced, the call fails before reaching ECM. Verify FastMCP's type coercion for `int` params.
  - Sending a test alert is a side-effect that reaches real external services (Discord, email servers, Telegram). In a shared Claude Desktop session, accidentally triggering test alerts multiple times can spam external endpoints.

---

## Export & Publish

#### `list_export_profiles()` — READ-ONLY
- **Purpose:** List all export profiles for generating M3U/XMLTV files.
- **Prompt to Claude:** "Show me all the export profiles configured in ECM."
- **Expected:** Claude calls `list_export_profiles()`. Response: "Found 3 export profiles: All Channels (id=1) — selection: all, Sports Only (id=2) — selection: groups, …". Shows profile name, ID, and selection mode.
- **Edge / failure tests:**
  - "List my export profiles" when none are configured → expects "No export profiles configured."
  - "List my export profiles" when the backend returns a non-list response → `profiles` would be a dict; iterating `for p in profiles` on a dict iterates keys, not values. The output would list key names as profile names. This may be a latent bug if the backend ever wraps the list in an envelope.
- **Watch for (possible bug areas):**
  - The tool assumes the response is always a list (no dict envelope handling, unlike many other tools). If the backend changes to return `{"profiles": [...]}`, the iteration produces incorrect output silently.
  - `p.get("selection_mode", p.get("type", "unknown"))` — tries two field names. If neither is present, shows "unknown" with no error.
  - No pagination: if there are many export profiles, all are returned in one call. In practice, operators rarely have more than 10-20, so this is low risk.

---

#### `generate_export(profile_id: int)` — WRITE
- **Purpose:** Generate M3U/XMLTV output for an export profile.
- **Prompt to Claude:** "Generate the export for my 'Full Playlist' profile." (just name it — Claude looks up the ID from `list_export_profiles`)
- **Expected:** Claude calls `list_export_profiles()` to resolve 'Full Playlist' to its numeric ID, then calls `generate_export(profile_id=<id>)`. Backend triggers export generation (potentially long-running). Response: "Export generated for profile <id>. Check ECM for download links." The tool does not return the file content — only a trigger confirmation.
- **Edge / failure tests:**
  - "Generate the export for my 'Full Playlist' profile" when no profile named 'Full Playlist' exists → Claude resolves via `list_export_profiles`; if no match, Claude reports none found rather than guessing an ID. Backend would return HTTP 404 if called with a non-existent ID.
  - "Generate the export for my 'Sports Only' profile" when another generation for the same profile is already in progress → the backend tracks in-progress generations in `_generating: set[int]`. It may reject the concurrent request with an error. Verify the MCP tool surfaces this correctly.
  - "Generate the export for my 'Full Playlist' profile" which has a very large channel list → generation may take longer than the default 30-second timeout. The tool uses the client's default timeout (not the 60-second override used by `compute_stream_sort`). Verify whether a timeout produces a clean error or a hung call.
- **Watch for (possible bug areas):**
  - Default timeout is 30 seconds (from `ECMClient`). Large exports could exceed this. The backend endpoint for export generation may be long-running but the tool does not pass a custom `timeout=LONG_TIMEOUT`. This is a potential hang risk.
  - The message `result.get("message", "Check ECM for download links.")` — if the backend returns a non-dict or if `message` is absent, the fallback string "Check ECM for download links." is used regardless. This may be misleading if the export actually failed silently at the backend.

---

#### `create_export_profile(name: str)` — WRITE
- **Purpose:** Create a new export profile for generating M3U/XMLTV files.
- **Prompt to Claude:** "Create a new export profile called 'Kids Channels'."
- **Expected:** Claude calls `create_export_profile(name="Kids Channels")`. Backend creates a profile with default settings (selection_mode=all, direct stream URL, etc.). Response: "Export profile created: Kids Channels (id=7)".
- **Edge / failure tests:**
  - "Create an export profile" with an empty name → `name=""` is sent to the backend. The backend's `ProfileCreateRequest` Pydantic model may reject an empty string with a 422; verify the error is surfaced.
  - "Create an export profile called 'My/Profile'" (special characters) → the `filename_prefix` default is "playlist" (valid), but the `name` field has no character restriction. The backend accepts it.
  - "Create an export profile called 'Kids Channels'" twice → backend may create two profiles with the same name (no uniqueness constraint documented). Verify whether duplicate names are allowed.
- **Watch for (possible bug areas):**
  - The tool only sends `{"name": name}` in the body — all other `ProfileCreateRequest` fields (`description`, `selection_mode`, `stream_url_mode`, etc.) use backend defaults. The operator has no way to set these via MCP. If the defaults are unsuitable (e.g., `stream_url_mode="direct"` when the operator needs `"proxy"`), the profile must be edited in the UI after creation.
  - `result.get("id", "?") if isinstance(result, dict) else "?"` — if the backend returns a non-dict (e.g., the created profile as a bare ID), `pid` shows as "?" and the operator can't reference the new profile. Unlikely but worth checking.

---

#### `delete_export_profile(profile_id: int)` — DESTRUCTIVE
- **Purpose:** Delete an export profile.
- **Prompt to Claude:** "List my export profiles, then delete the one called 'Test Profile'." (just name it — Claude looks up the ID from `list_export_profiles`)
- **Expected:** Claude calls `list_export_profiles()` to resolve 'Test Profile' to its ID, then calls `delete_export_profile(profile_id=<id>)`. On success: "Export profile <id> deleted." On backend error: "Error deleting export profile <id>: …".
- **Edge / failure tests:**
  - "Delete my 'Test Profile' export profile" when no profile named 'Test Profile' exists → Claude resolves via `list_export_profiles`; if no match found, Claude reports none found rather than guessing an ID. Backend would return HTTP 404 if called with a non-existent ID.
  - "Delete my 'Full Playlist' export profile" when a publish configuration references it → backend may return a 400/409 due to a foreign key constraint; verify the error detail is surfaced.
  - "Delete my 'Test Profile' export profile" twice → first call succeeds; second call returns HTTP 404.
- **Watch for (possible bug areas):**
  - **No confirmation prompt.** A natural-language request like "delete all test export profiles" could result in multiple consecutive `delete_export_profile` calls with no per-item confirmation. Since exports may be referenced by publish configurations, silent cascading deletes could break automated publishing.
  - No read-back verification after delete (unlike `delete_saved_backup`). The tool trusts the HTTP 2xx response from the backend. If the backend returns 200 but didn't actually delete the profile (e.g., a bug), the tool silently reports success.
  - The backend endpoint returns HTTP 204 on successful delete (no body). `ecm_client.delete()` handles 204 by returning `None`, which the tool ignores — correct behavior.

---

#### `list_cloud_targets()` — READ-ONLY
- **Purpose:** List configured cloud storage targets for publishing exports.
- **Prompt to Claude:** "What cloud storage targets are set up for publishing exports?"
- **Expected:** Claude calls `list_cloud_targets()`. Response: "Found 2 cloud targets: S3 Bucket (id=1) — s3, Cloudflare R2 (id=2) — r2 …". Shows target name, ID, and provider type.
- **Edge / failure tests:**
  - "List my cloud targets" when none are configured → expects "No cloud targets configured."
  - "List my cloud targets" when the export cloud-targets endpoint returns a non-list response → same issue as `list_export_profiles`: no envelope handling. Silent incorrect output if the backend wraps the list.
- **Watch for (possible bug areas):**
  - **SECRET LEAKAGE RISK (MEDIUM):** Cloud targets store credentials (S3 access keys, R2 tokens, etc.) which the export router encrypts via `cloud_storage.crypto.encrypt_credentials`. Verify the `/api/export/cloud-targets` response does NOT include decrypted credentials. The MCP tool renders `t.get("type", t.get("provider", "unknown"))` — if the backend accidentally returns a `config` or `credentials` field with plaintext secrets, the tool would pass them through to Claude's response (though the tool only renders name/id/type).
  - `t.get("type", t.get("provider", "unknown"))` — tries both field names. If the backend uses a different field name (e.g., `storage_type`), shows "unknown" for all targets.
  - The distinction between "cloud target" (`/api/export/cloud-targets`) and "publish config" (`/api/export/publish-configs/{config_id}/publish`) is important: `publish_export` takes a `config_id` (publish configuration), not a cloud target `id`. Operators must understand these are different resources. Use `list_publish_configs` to discover valid publish config IDs before calling `publish_export`.

---

#### `list_publish_configs()` — READ-ONLY
- **Purpose:** List the publish configurations — each one ties an export profile to a cloud target with a schedule — so the operator or Claude can discover which config to pass to `publish_export`. New in 0.17.2 (epic co5wh). Wraps `GET /api/export/publish-configs`.
- **Prompt to Claude:** "What publish configurations do I have set up?"
- **Expected:** Claude calls `list_publish_configs()`. Response lists each configuration: name, the export profile it draws from, the cloud target it publishes to, schedule type (manual / scheduled), and enabled state. Example: "Publish Configs (2): Nightly S3 — profile: Full Playlist → target: S3 Bucket, schedule: nightly, enabled; Weekly R2 — profile: Sports Only → target: Cloudflare R2, schedule: weekly, disabled."
- **Edge / failure tests:**
  - "What publish configurations do I have set up?" when none exist → expects "No publish configurations configured."
  - "What publish configurations do I have set up?" when the `/api/export/publish-configs` endpoint returns a non-list response → same envelope-handling risk as `list_export_profiles` and `list_cloud_targets`; verify graceful output rather than iterating dict keys.
  - "What publish configurations do I have set up?" when the backend is unreachable → outer `except` surfaces the HTTP error cleanly.
- **Watch for (possible bug areas):**
  - Operators need the config name (not a numeric ID) to interact naturally. Confirm the tool surfaces the human-readable `name` field — if `name` is absent or blank, the output becomes confusing and the operator cannot reference the config naturally.
  - The relationship between publish config → export profile → cloud target forms a three-level chain. If a publish config references a deleted export profile or cloud target, the backend may return partial data or a 500. Verify the tool handles missing foreign-key references gracefully rather than crashing.
  - `schedule_type` field naming may vary (`schedule`, `schedule_type`, `cron`, `frequency`) — try `.get()` with fallback to avoid showing "unknown" for every config's schedule.

---

#### `publish_export(config_id: int)` — WRITE
- **Purpose:** Publish an export to a cloud storage target using a publish configuration.
- **Prompt to Claude:** "Publish my 'Nightly S3' export." (just name it — Claude resolves the config ID via `list_publish_configs`)
- **Expected:** Claude calls `list_publish_configs()` to resolve 'Nightly S3' to its numeric `config_id`, then calls `publish_export(config_id=<id>)`. Backend triggers the publish pipeline for that configuration. Response: "Publish started for config <id>. [message from backend if present]".
- **Edge / failure tests:**
  - "Publish my 'Nightly S3' export" when no config named 'Nightly S3' exists → Claude resolves via `list_publish_configs`; if no match found, Claude reports none found rather than guessing an ID. Backend would return HTTP 404 if called with a non-existent ID.
  - "Publish my 'Nightly S3' export" when the cloud target is misconfigured (wrong credentials) → the publish pipeline runs asynchronously; the MCP response may say "Publish started" even if the upload will fail. Verify whether the endpoint returns a job ID or final status.
  - "Publish my 'Nightly S3' export" twice concurrently → backend may queue or reject the second request.
- **Watch for (possible bug areas):**
  - The publish pipeline likely runs asynchronously; the tool returns "Publish started" immediately. If the upload fails after the HTTP 202/200 response, the operator has no way to check the result through MCP (there is no `get_publish_status` tool). They must check the ECM UI or notifications.
  - `result.get("message", "") if isinstance(result, dict) else ""` — if `message` is absent in the backend response, the tool returns "Publish started for config N. " with a trailing space and no status detail. Slightly misleading.
  - Default timeout is 30 seconds; large file uploads to cloud could exceed this. Unlike the long-running probe/sort tools, `publish_export` does not request a longer timeout. This is a potential timeout risk for large exports.
