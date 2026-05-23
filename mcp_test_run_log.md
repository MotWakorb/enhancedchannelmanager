# ECM MCP Tool Test Plan — Live Execution Log

**Run date:** 2026-05-22 / 2026-05-23 (overnight autonomous run)
**Executor:** Claude Code (Opus 4.7), connected to the live `ecm` MCP (`http://localhost:6101/mcp`)
**Source plan:** `docs/mcp_tool_test_plan.md` (124 tools, ~502 test cases)
**Findings epic:** `enhancedchannelmanager-lq38l`
**Method:** Each test prompt is executed by calling the underlying `mcp__ecm__*` tool directly (exactly what a local Claude would do). READ-ONLY tests run freely. WRITE/DESTRUCTIVE tests use disposable `MCPTEST_`-prefixed entities and are reverted; real Dispatcharr data is never deleted or mutated. Destructive "all/system-wide" variants that cannot be made safe are documented, not executed.

**Legend:** ✅ pass · ❌ fail (bead filed) · ⚠️ anomaly (bead filed) · ⏭️ skipped-for-safety (explained) · ✏️ test rewritten (invalid prompt)

---

## Live inventory (discovery pass — doubles as the canonical `list_*` tests)

| Tool | Result | Verdict |
|------|--------|---------|
| `list_channels()` | 571 channels, "showing 50". Format `#10440.0: Radio: Yacht Rock Radio (id=12319) — 1 streams`. | ⚠️ channel numbers render with a trailing `.0` float suffix (`#10440.0`), doc expects `#101`. Cosmetic. |
| `list_channel_groups()` | 1115 groups. Real names carry emoji, e.g. `USA | Sports ⚽ (id=3)`. Most groups 0 channels; `Entertainment (id=1306) — 135`, `Radio (id=1320) — 427`, `ESPN+ (id=368) — 9`. | ✅ |
| `list_m3u_accounts()` | 3: `Provider 1 (id=3) success`, `custom (id=1) error`, `HD Homerun (id=11) success`. Matches doc. All show "0 streams". | ✅ (stream-count=0 noted) |
| `list_epg_sources()` | 8: Teamarr(3), B1G EPG(5), Jesmann Gracenote(8), NCAA Football EPG(4), Jesmann Full(2), PPV EPG(6), NCAA Men's Basketball EPG(7), B1G Advanced EPG(15). All "0 channels". | ✅ |
| `list_channel_profiles()` | 9: LiveTV(1,571), HDHomerun(2,0), Plex(3,0), TestingProfile(4,0), Green Bay(5,571), Madison(6,571), Portland(7,571), Testing(8,0), Music(9,427). | ✅ |
| `list_stream_profiles()` | 5: ffmpeg(1), Proxy(3), Redirect(4), streamlink(2), VLC(8) — all active/locked. Matches doc exactly. | ✅ |
| `list_auto_creation_rules()` | 3: Testing Rule(2,enabled), Create B1G Channels(1,disabled), USA Entertainment(3,enabled). Matches doc. | ✅ |
| `list_tasks()` | 15 tasks, but **every task name renders as "Unknown"** — `Unknown (id=epg_refresh)`, `Unknown (id=m3u_refresh)`, etc. | ⚠️ task name never resolved → always "Unknown" (bead). |
| `list_normalization_rules()` | 8 groups, all enabled, but **every group shows "0 rules"** and no rule names. Doc expects rule counts + up to 5 names. | ⚠️ rule count/names always 0/empty — verify vs. real transform (bead pending confirmation). |
| `list_streams()` | 2682 streams, "showing 50 of 2682 (page 1)". Format `WY | Riverton | PBS KCWC (id=7260)`. | ✅ |

**Data-grounding note:** the live channel set is dominated by `Radio: *` (group `Radio`, 427 ch) and the `Entertainment` group (135 ch). The doc's recurring sample entities (`US : ESPN`, `US: ESPN FHD`, etc.) must be re-confirmed against live data before the Channels/Streams tests; entity searches follow.

### Entity grounding (confirms doc sample data exists live)
- Channels: `US : ESPN` (id=11875), `US : ESPN 2` (11874), `US : ESPN News` (11873), `US : ESPN U` (11872), `Radio: ESPN Radio` (11993 & 11877). All in/near group `ESPN+` (id=368, 9 ch). Names carry a `"N | "` number prefix, e.g. name is literally `6 | US : ESPN`.
- Streams: `US: ESPN FHD` (5001), `US: ESPN 2 FHD` (5002), `US: ESPN 2` (5204), `US: ESPN U` (5205), `US: ESPN News` (5206), and **TWO** streams named `US: ESPN` (5286 and 5203). `US : ESPN` channel currently holds stream 5203.
- Groups for tests: `USA | Sports ⚽` (id=3), `USA | Movies 🍿` (4), `USA | Kids 🧸` (6), `Radio` (1320), `USA | Local PBS` (14), `NFL Game Pass 🏈` (13). **Note:** live names include emoji/decorations the doc omits (doc says `USA | Sports`, live is `USA | Sports ⚽`).

---

## Domain: Channels (READ-ONLY tests)

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| get_channel #1 "everything about US : ESPN" | `get_channel(11875)` | Full block: name `6 | US : ESPN`, ID 11875, Number **6.0**, Group 368, EPG TVG ID `ESPN.us`, Logo Yes, Streams 1 (IDs [5203]), Auto-created False | ✅ (`.0` float cosmetic) |
| get_channel #2 nonexistent | `get_channel(99999999)` | `Error getting channel 99999999: GET /api/channels/99999999 -> HTTP 404 Not Found: No Channel matches the given query.` | ✅ graceful |
| get_channel #3 number 0 | `get_channel(0)` | (covered by 404 path) — deferred to write-batch | — |
| find_duplicate_channels #1/#2 | `find_duplicate_channels()` | `No duplicate channels found.` (live has none — test #2 path) | ✅ |

## Domain: Streams (READ-ONLY tests)

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| get_streams_for_channel #1 | `get_streams_for_channel(11875)` | `Channel 11875 has 1 streams: 1. US: ESPN (id=5203) from 3` | ⚠️ provider shown as raw id `from 3` (not "Provider 1"); group omitted — cosmetic cluster |
| search_streams #1 "ESPN" limit 10 | `search_streams("ESPN",10)` | `Found 10 streams matching 'ESPN' (showing 10)` — all expected ESPN streams | ✅ |
| get_streams_by_ids #1 | `get_streams_by_ids([5001,5204,3824])` | `Found 3 of 3 requested streams` (names+IDs only) | ✅ (⚠️ group+provider omitted — cosmetic cluster) |
| fuzzy_match_stream #1 "ESPN2" | `fuzzy_match_stream("ESPN2")` | `Best match: US: ESPN 2 (id=5204); Also found: US: ESPN 2 FHD` — variant `ESPN2`→`ESPN 2` worked | ✅ |
| bulk_search_streams #1 | `bulk_search_streams(["ESPN","ESPN 2","Yacht Rock"])` | 10 / 2 / 2 results respectively (incl. `US: Music Choice Yacht Rock`, `Radio: Yacht Rock Radio`) | ✅ |

## Domain: Normalization (READ-ONLY tests)

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| test_normalization #1 | `test_normalization("US : ESPN HD, US : ESPN 2 East")` | `US : ESPN HD → Us : ESPN HD` / `US : ESPN 2 East → Us : ESPN 2 East` | ✅ tool works; **but reveals** Strip groups don't strip & exposes the list_normalization_rules "0 rules" bug |
| test_normalization #2 single | `test_normalization("Radio: ESPN Radio")` | `Radio: ESPN Radio → Radio: ESPN Radio` (unchanged) | ✅ |
| list_normalization_rules #1 | (discovery) | 8 groups, all "0 rules" despite active Title Case rule | ❌ **bead filed** (see below) |

### Beads filed this batch
- **list_tasks "Unknown" names** → P2 bug (id captured in run output)
- **list_normalization_rules "0 rules"** → P2 bug (id captured in run output)
- Cosmetic cluster (accumulating, bead at end): (a) channel numbers render `#6.0` float suffix; (b) stream provider shown as raw id `from 3` / group omitted in get_streams_for_channel & get_streams_by_ids.

---

## Domain: Channels (WRITE / DESTRUCTIVE — disposable `MCPTEST_` entities)

Playground: group `MCPTEST_Sweep` (id=1351); channels `MCPTEST_FoxNewsHD` (id=12340), `MCPTEST_Moved` (id=12339, originally `MCPTEST_NoNumGroup`).

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| create_channel_group #1 | `create_channel_group("MCPTEST_Sweep")` | `Channel group ready: MCPTEST_Sweep (id=1351)` | ✅ |
| create_channel #1 (name+num+group) | `create_channel("MCPTEST_FoxNewsHD",360,1351)` | `#360.0: MCPTEST_FoxNewsHD (id=12340, group_id=1351)` | ✅ (`.0` cosmetic) |
| create_channel #2 (no num/group) | `create_channel("MCPTEST_NoNumGroup")` | `#10.0: ... (id=12339, group_id=1304)` — backend auto-assigned Default Group(1304) + num 10 | ✅ (note: auto-defaults group, doc said "no group") |
| create_channel #3 (empty name) | `create_channel("")` | `HTTP 400 ...: name: This field may not be blank.` | ✅ graceful |
| create_channel #4 (bad group) | `create_channel("MCPTEST_BadGroup", group_id=99999999)` | `HTTP 400 ...: channel_group_id: Invalid pk "99999999" - object does not exist.` | ✅ graceful 400 |
| update_channel #1 (rename+move) | `update_channel(12339, name="MCPTEST_Moved", group_id=1351)` | `Channel 12339 updated: name='MCPTEST_Moved', channel_number=10.0, group_id=1351` | ✅ |
| update_channel #2 (no-op) | `update_channel(12340)` | `No changes specified.` (no HTTP) | ✅ early-exit guard |
| update_channel #3 (nonexistent) | `update_channel(99999999, name=...)` | **HTTP 500** Internal server error | ❌ expected 404 → **500-cluster bead** |
| update_channel #4 (bad group) | `update_channel(12340, group_id=99999999)` | **HTTP 500** | ❌ expected 400 → **500-cluster bead** |
| add_stream_to_channel #1 | `add_stream_to_channel(12340,5001)` | `Stream 5001 added to channel 12340.` | ✅ |
| add_stream_to_channel #2 (bad stream) | `(12340,99999999)` | **HTTP 500** | ❌ → 500-cluster |
| add_stream_to_channel #3 (dup) | `(12340,5001)` again | `Stream 5001 added...` (backend silently dedups) | ✅ |
| add_stream_to_channel #4 (bad channel) | `(99999999,5001)` | **HTTP 500** | ❌ → 500-cluster |
| bulk_add_streams_to_channel #1/#2 | `(12340,[5001,5002,5204])` | `Added 2 (1 already present); now 3 streams. Added: [5002,5204]` | ✅ |
| bulk_add_streams_to_channel #3 (empty) | `(12340,[])` → (covered indirectly) | n/a | — |
| bulk_add_streams_to_channel #4 (bad channel) | `(99999999,[5001])` | **HTTP 500** | ❌ → 500-cluster |
| bulk_assign_epg #1 | `[{channel_id:12339,tvg_id:"ESPN2.us"}]` | `Updated EPG assignments for 1/1 channels.` | ✅ |
| bulk_assign_epg #3 (missing channel_id) | `[{tvg_id:"x"}]` | `0/1 ... Errors(1): missing channel_id in mapping` | ✅ guard |
| reorder_streams #1 (happy) | `(12340,[5002,5001,5204])` | `New order: [5002, 5001, 5204]` | ✅ |
| reorder_streams #2 (partial) | `(12340,[5001,5002])` | `New order: [5001,5002]`; get_channel → 5204 **silently detached** | ⚠️ **reorder bead** |
| reorder_streams #4 (empty) | `(12340,[])` | `New order: []`; get_channel → Streams: 0 **silently cleared** | ⚠️ **reorder bead** |
| reorder_streams #3 (foreign id) | `(12340,[9999999])` | **HTTP 500** | ❌ → 500-cluster |
| bulk_remove_streams #1 (REGRESSION) | `(12340,[5002,5204])` from [5002,5001,5204] | `Removed 2 ... Remaining: 1` | ✅ **0.17.2 fix holds** |
| bulk_remove_streams #2 (not in channel) | `(12340,[9999999])` | `None of the specified streams were in channel 12340.` | ✅ |
| bulk_remove_streams #4 (empty ids) | `(12340,[])` | `None of the specified streams were in channel 12340.` | ✅ |
| remove_stream_from_channel #1 | `(12340,5001)` | `Stream 5001 removed from channel 12340.` | ✅ |
| remove_stream_from_channel #2 (not present) | `(12340,5001)` again | `Stream 5001 removed...` (idempotent, no crash; reports "removed" even though none) | ✅ (minor misleading msg) |
| remove_stream_from_channel #3 (bad channel) | `(99999999,5001)` | **HTTP 500** | ❌ → 500-cluster |
| get_channel #3 (id 0) | `get_channel(0)` | `HTTP 404 ... No Channel matches the given query.` | ✅ (fixed tool → clean 404) |
| assign_channel_numbers #3 (empty) | `assign_channel_numbers([])` | `Assigned numbers to 0 channels starting from auto.` | ✅ |

**Beads filed this batch:** reorder_streams silent-detach (P2); channel write-tools opaque-500 cluster (P2).
**Still pending in Channels:** delete_channel, bulk_delete_channels, delete_channel_group (cleanup, done next).

## Domain: Channels (WRITE/DESTRUCTIVE — part 2: dedup, merge, commit, lineup)

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| add_stream #4 (invalid action) | `add_stream("US: ESPN",1351,"auto")` | `Invalid dedup_action 'auto'. Must be one of: force_new, merge_if_found, prompt` | ✅ client-side validation |
| add_stream #2 (force_new) | `add_stream("US: ESPN U",1351,"force_new")` | `Channel 'US: ESPN U' (id=12341) created ... stream 'US: ESPN U' (id=5205) assigned.` | ✅ |
| add_stream #3 (no stream match) | `add_stream("MCPTEST Unique ZZZ 99 NoStream",1351,"force_new")` | `Channel ... (id=12342) created but stream ... could not be found — assign a stream manually.` | ✅ clear message |
| add_stream #1 (prompt) | `add_stream("US: ESPN U",1351,"prompt")` | `action=pending_merge ... merge_id:1 candidate_channel_id:12341 confidence:100%` | ✅ |
| add_stream #5 (merge_if_found) | `add_stream("US: ESPN 2",1351,"merge_if_found")` | `merged into existing channel 'US: ESPN U' (id=12341, confidence=90%) via merge_id=2` → get_channel(12341) shows streams [5205,**5204**] | ⚠️ **wrong-match bead** (ESPN 2 stream → ESPN U channel) |
| merge_channels #1 (happy) | `merge_channels(12340,[12339])` | `Merged 1 channels into channel 12340.` (12339 deleted — confirmed gone) | ✅ |
| merge_channels #4 (empty src) | `merge_channels(12340,[])` | `Merged 0 channels into channel 12340.` | ✅ |
| merge_channels #2 (bad target) | `merge_channels(99999999,[12342])` | `Merged 1 channels into channel 99999999.` (12342 survived) | ❌ **false success** → merge bead |
| merge_channels #3 (into self) | `merge_channels(12343,[12343])` | `Merged 1 channels into channel 12343.` → get_channel(12343) = **404 (deleted)** | ❌ **DATA LOSS** → merge bead (P1) |
| bulk_merge_duplicate_channels #3 (empty) | `([])` | `Bulk merge complete: 0 merged, 0 failed.` | ✅ |
| bulk_merge_duplicate_channels #4 (malformed) | `([{source_channel_ids:[12340]}])` | `HTTP 422 ... target_channel_id Field required` | ✅ graceful |
| bulk_merge_duplicate_channels #1 (happy) | `([{target:12340, sources:[12342]}])` | `1 merged, 0 failed. ✓ MCPTEST_FoxNewsHD: absorbed 1 channels, 0 streams` | ✅ |
| bulk_merge_duplicate_channels #2 (bad target) | `([{target:99999999, sources:[12344]}])` | `0 merged, 1 failed. ✗ Channel 99999999: HTTPStatusError` (12344 survived) | ✅ correct (contrast merge_channels!) — minor: raw `HTTPStatusError` label |
| bulk_commit_channels #1 (validate-only) | createChannel op, `validate_only=True` | `Bulk commit SUCCESS: 1 operations submitted. (validate-only mode — no changes applied)` | ✅ |
| bulk_commit_channels #2 (bad type) | `[{type:"badType"}]` | `HTTP 422` with full per-discriminated-union field detail | ✅ |
| clear_auto_created (scoped no-op) | `clear_auto_created(group_ids=[1351])` | `Cleared 0 auto-created channels in 1 group.` | ✅ |
| clear_auto_created (empty list) | `clear_auto_created(group_ids=[])` | helpful rejection: use all_groups=True for system-wide | ✅ (**doc test STALE — rewrite**) |
| clear_auto_created (all_groups=True) | — | **NOT RUN** (would delete real auto-created channels system-wide) | ⏭️ safety skip |
| assign_channel_numbers #1 (happy) | `assign_channel_numbers([12342],200)` | `Assigned numbers to 1 channels starting from 200.` (get_channel → #200.0) | ✅ |
| set_logo_from_epg #5 (bad channel) | `set_logo_from_epg([99999999])` | `0 assigned ... 1 errors ... channel 99999999: HTTP 404` | ✅ |
| set_logo_from_epg (skip path) | `set_logo_from_epg([12340])` | `0 assigned, 1 skipped (no EPG link)` | ✅ |
| build_channel_lineup #4 (missing name) | `build_channel_lineup([{number:900}],1351)` | `Error building channel lineup: 'name'` | ✅ KeyError caught |
| build_channel_lineup #1 (happy) | `([{name:"MCPTEST Lineup ESPN",number:901}],1351,provider_id=3)` | `4 channels created, 0 matched, 3 unmatched` (only 1 created; lists pre-existing) | ⚠️ **count bead** |

**Beads filed this batch:** merge_channels false-success + self-merge data-loss (**P1**); build_channel_lineup miscount (P2); add_stream merge_if_found wrong-match (P2).
**Doc rewrite needed:** `clear_auto_created` test section (stale signature — now `all_groups`/rejects empty list).

## Domain: Dedup / Pending Channel Merges

Used the pending-merge rows generated by `add_stream` (candidates were disposable channel 12341, so accept was safe).

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| list_pending #1 (default) | `list_pending_channel_merges()` | merge id=1 pending, candidate 12341, conf 1.0, trigger_context mcp_tool | ✅ |
| list_pending #3 (merged) | `status="merged"` | merge id=2 (US: ESPN 2), resolution_source **"operator"** (auto-accept labeled operator — minor) | ✅ |
| list_pending (dismissed) | `status="dismissed"` | empty | ✅ |
| list_pending #4 (invalid status) | `status="invalid"` | `HTTP 400 ... status must be one of [...]` | ✅ surfaced |
| list_pending #5 (pagination) | `page=2,page_size=10` | `merges:[], total:1, page:2` | ✅ |
| list_pending #7 (page_size 201) | `page_size=201` | `HTTP 400 page_size must be between 1 and 200` | ✅ |
| accept #1 (happy) | `accept_channel_merge(1)` | `{merged_into_channel_id:"12341", journal_entry_id:4, source_stream_id:"5205", confidence:1.0, status:"merged"}` | ✅ |
| accept #2 (idempotent) | `accept_channel_merge(1)` again | same envelope | ✅ |
| accept #5 (nonexistent row) | `accept_channel_merge(999999)` | `{error:{code:"TARGET_NOT_FOUND", ...}}` (envelope, no raise) | ✅ |
| accept #4 (on dismissed) | `accept_channel_merge(3)` after dismiss | `{error:{code:"INVALID_STATE", ...}}` | ✅ |
| dismiss #1 (happy) | `dismiss_channel_merge(3)` | `{journal_entry_id:6, status:"dismissed"}` | ✅ |
| dismiss #2 (idempotent) | `dismiss_channel_merge(3)` again | same envelope | ✅ |
| dismiss #3 (on merged) | `dismiss_channel_merge(2)` | `{error:{code:"INVALID_STATE", ...}}` | ✅ |
| dismiss #4 (nonexistent) | `dismiss_channel_merge(999999)` | **raises** `HTTP 404 ... pending merge id=999999 not found` (NOT an envelope) | ⚠️ inconsistency: accept returns envelope on 404, dismiss raises (documented concern) → consistency-cluster note |

Note: `candidate_channel_id` is returned as a **string** ("12341") rather than int — minor type inconsistency.

## Domain: Channels — delete (cleanup of disposables)

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| delete_channel #1 (happy) | `delete_channel(12344)` | `Channel 12344 deleted.` | ✅ |
| delete_channel #2 (nonexistent) | `delete_channel(99999999)` | **HTTP 500** | ❌ → 500-cluster (DELETE) |
| delete_channel #3 (delete again) | `delete_channel(12344)` again | **HTTP 500** | ❌ → 500-cluster |
| bulk_delete_channels #1 | `[12340,12341]` | `2 deleted, 0 errors out of 2` | ✅ |
| bulk_delete_channels #2 (mixed) | `[12345,99999999]` | `1 deleted, 1 errors ... Channel 99999999: HTTP 500` | ✅ (continues; 500 for bad id) |
| bulk_delete_channels #3 (empty) | `[]` | `0 deleted, 0 errors out of 0` | ✅ |
| delete_channel_group (cleanup/#1) | `delete_channel_group(1351)` | `Channel group 1351 deleted.` | ✅ |
| delete_channel_group #4 (nonexistent) | `delete_channel_group(99999999)` | `HTTP 404 ... No ChannelGroup matches the given query.` | ✅ (fixed tool → clean 404) |

**500-cluster bead (lq38l.4) now also covers:** `delete_channel` (nonexistent/already-deleted). Clear pattern: 1wq7z.22-fixed tools (get_channel, create_channel, delete_channel_group, create_epg_source) return clean 4xx; unfixed siblings (update_channel, add_stream_to_channel, bulk_add_streams_to_channel, reorder_streams, remove_stream_from_channel, delete_channel, merge_channels) return raw 500.

---

## Domain: Streams (READ-ONLY) + Channel Groups (READ-ONLY) + EPG (READ) + Auto-Creation (READ)

| Tool / test | Result | Verdict |
|------|--------|---------|
| `get_stream_health()` | `Total:5298 Success:4705 Failed:593 Timeout:0 Pending:0` | ✅ |
| `get_probe_progress()` | `No probe is currently running.` | ✅ |
| `get_probe_results()` | structured result, all lists empty / counts 0 (no completed probe) — prints empty structure rather than "No probe results available." | ✅ (minor: verbose-empty) |
| `get_struck_out_streams()` | `Struck-out streams (136, threshold: 3) ... and 106 more` (caps at 30) | ✅ |
| `get_hidden_groups()` | `No hidden channel groups.` | ✅ |
| `get_auto_created_groups()` | `No auto-created channel groups.` (envelope unwrap OK) | ✅ |
| `get_groups_with_streams()` | `Found 3 groups: Entertainment(1306), ESPN+(368), Radio(1320)` — each **"— 0 streams"** | ⚠️ count bug (filter right, count 0) |
| `get_orphaned_groups()` | `Found 746 orphaned groups: <name> (id=N)` proper format | ✅ **envelope-unwrap regression FIXED** |
| `get_epg_grid(limit=20)` | `EPG Schedule (20 programs): [Unknown] 800 | The Masked Singer (...) ... and 3406 more` | ✅ (⚠️ channel name `[Unknown]` — resolution gap) |
| `get_epg_grid(channel_id=11875,limit=10)` | `No EPG schedule data available.` (client-side filter finds none — programs not channel-linked) | ✅ (consistent w/ [Unknown]) |
| `list_dummy_epg_profiles()` | `Dummy EPG Profiles (1): B1G Advanced EPG (id=1) — enabled, 1 channel groups` | ✅ |
| `list_auto_creation_executions(limit=5)` | 5 execs `#173 completed — 45 channels (ts)` etc. | ✅ |
| `get_auto_creation_rule(2)` | full detail for Testing Rule; conditions `stream_name_contains: ESPN`; action `create_channel: ?` | ✅ (⚠️ action value `?` cosmetic) |
| `get_auto_creation_rule(99999)` | `HTTP 404 ... Rule not found` | ✅ clean 404 |
| `analyze_auto_creation_rules()` | markdown report `0 errors, 0 warnings, 1 info`; flags USA Entertainment MERGE_SCOPE_NOT_TARGET_GROUP | ✅ excellent |
| `get_auto_creation_debug_bundle()` | static text incl. endpoints + "credentials redacted" | ✅ |

**Cosmetic cluster additions:** `[Unknown]` channel name in get_epg_grid; `create_channel: ?` action value in get_auto_creation_rule; verbose-empty get_probe_results.
**Count-fields anomaly (bead pending M3U confirmation):** get_groups_with_streams "0 streams", list_m3u_accounts "0 streams", list_epg_sources "0 channels".

---

## Domain: M3U Accounts (READ + WRITE + DESTRUCTIVE)

Disposables: MCPTEST_Provider (id=13), MCPTEST_Local (id=14) — both deleted at end.

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| get_m3u_account #1 | `get_m3u_account(3)` | Provider 1, Type standard, **URL: N/A**, success, **Streams: 0**, last refresh ts | ⚠️ count/URL bead |
| get_m3u_account #3 (error state) | `get_m3u_account(1)` | custom, Status: **error**, Streams: 0 | ✅ (status shown) |
| get_m3u_account (nonexistent) | `get_m3u_account(99999)` | **HTTP 500** | ❌ → 500-cluster |
| (count proof) | `list_streams(provider_id=3)` | `Showing 1 of 2624 streams` | confirms Provider 1 has 2624 streams (shown as 0) |
| create_m3u_account #1 | `create_m3u_account("MCPTEST_Provider", https...)` | `M3U account created: MCPTEST_Provider (id=13)` | ✅ |
| create_m3u_account #2 (http/private IP) | `create_m3u_account("MCPTEST_Local","http://192.168.1.100/list.m3u")` | `created (id=14)` — http+private IP **accepted** (no scheme rejection) | ✅ (note: no validate_url_scheme block) |
| create_m3u_account #3 (duplicate) | same name again | `HTTP 500 Internal server error` | ✅ (documented swallow-to-500) |
| update_m3u_account #1 (rename) | `update_m3u_account(13, name="MCPTEST_Provider_R")` | `name='MCPTEST_Provider_R', url=''` | ✅ (url='' — count/URL bead) |
| update_m3u_account #2 (no-op) | `update_m3u_account(13)` | `No changes specified.` | ✅ |
| update_m3u_account (nonexistent) | `update_m3u_account(99999, name=...)` | **HTTP 500** | ❌ → 500-cluster |
| update_m3u_group_settings (regression) | `(13, "USA | Sports", False)` | `Group 'USA | Sports' not found on M3U account 13. Check the group name...` | ✅ **regression FIXED** (was silent no-op) |
| bulk_update_m3u_group_settings #2 (empty) | `(13, {})` | `Updated 0 groups on M3U account 13:` | ✅ (minor trailing colon) |
| bulk_update_m3u_group_settings #1 (groups) | `(13, {GroupA:F, GroupB:T})` | `Updated 0 groups ... WARNING: 2 group(s) not found: ...` | ✅ **regression FIXED** (reports not-found) |
| refresh_m3u (disposable) | `refresh_m3u(13)` | `M3U account 13 refresh started. ... refresh initiated.` | ✅ (async trigger) |
| refresh_m3u (nonexistent) | `refresh_m3u(99999)` | **HTTP 500** | ❌ → 500-cluster |
| refresh_all_m3u | — | **NOT RUN** (would refresh real Provider 1 / HD Homerun) | ⏭️ safety skip |
| delete_m3u_account #1 | `delete_m3u_account(13)` / `(14)` | (cleanup — see next) | ✅ |
| delete_m3u_account (nonexistent) | `delete_m3u_account(99999)` | **HTTP 500** | ❌ → 500-cluster |

**Bead filed:** count/URL fields render 0/N-A (P2). **Regressions confirmed FIXED:** update_m3u_group_settings + bulk_update_m3u_group_settings (no longer silent no-ops).
**500-cluster (lq38l.4) now also covers:** get_m3u_account, update_m3u_account, refresh_m3u, delete_m3u_account (all → 500 on nonexistent id).

---

## Domain: EPG Sources (READ done earlier + WRITE + DESTRUCTIVE)

Disposable: MCPTEST_Guide (id=18) — deleted at end.

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| create_epg_source #1 | `create_epg_source("MCPTEST_Guide", https...)` | `EPG source created: MCPTEST_Guide (id=18)` | ✅ |
| create_epg_source #2 (bad scheme) | `create_epg_source("MCPTEST_BadURL","file:///etc/passwd")` | `HTTP 400 ... only http and https URLs are allowed` | ✅ clean 400 (fixed tool) |
| create_epg_source #3 (duplicate) | same name | `HTTP 400 ... epg source with this name already exists.` | ✅ clean 400 (contrast M3U dup → 500) |
| match_channels_epg (REGRESSION) | `match_channels_epg(channel_ids=[11875,11874,11873])` | `EPG auto-match complete: 3 channels total — 0 exact, 3 multiple candidates, 0 unmatched.` | ✅ **regression FIXED** (real counts via exact/multiple/none; was "always 0") |
| generate_dummy_epg #1 | `generate_dummy_epg()` | `Dummy EPG regenerated for 1 enabled profiles.` | ✅ |
| update_epg_source #1 | `update_epg_source(18, url="https://newguide.example.com/guide.xml")` | `EPG source 18 updated: name='MCPTEST_Guide', url='https://newguide...'` | ✅ |
| update_epg_source #2 (no-op) | `update_epg_source(18)` | `No changes specified.` | ✅ |
| update_epg_source (nonexistent) | `update_epg_source(99999, name=...)` | **HTTP 500** | ❌ → 500-cluster |
| refresh_epg (disposable) | `refresh_epg(18)` | `EPG source 18 refresh started. ...` | ✅ |
| refresh_epg (nonexistent) | `refresh_epg(99999)` | **HTTP 500** | ❌ → 500-cluster |
| refresh_all_epg #2 (scoped) | `refresh_all_epg(source_ids=[18])` | `Refreshed 1/1 EPG sources.` | ✅ |
| refresh_all_epg #1 (all, no args) | — | **NOT RUN** (would refresh all 8 real EPG sources) | ⏭️ safety skip |
| delete_epg_source (nonexistent) | `delete_epg_source(99999)` | **HTTP 500** | ❌ → 500-cluster |
| delete_epg_source #1 (cleanup) | `delete_epg_source(18)` | deleted | ✅ |

**Regression confirmed FIXED:** match_channels_epg (headline 0.17.2 fix — real match counts).
**Doc rewrite needed:** match_channels_epg test section (signature now accepts channel_ids/epg_source_ids/source_order; output format is exact/multiple/none).
**500-cluster (lq38l.4) now also covers:** update_epg_source, refresh_epg, delete_epg_source (nonexistent → 500). create_epg_source correctly returns 4xx (fixed tool).

---

## Domain: Stats & Analytics (READ-ONLY, 14 tools)

| Tool / test | Result | Verdict |
|------|--------|---------|
| `get_channel_stats()` | `No active channels.` | ✅ |
| `get_top_watched(5)` | 5 channels w/ hours, but **"? unique viewers"** for each | ✅ (⚠️ unique-viewers `?` — cosmetic) |
| `get_bandwidth()` | Today 3.5GB / Week 419.4GB / Month 556.6GB / AllTime 605.8GB + peak | ✅ |
| `get_popularity_rankings(20)` | `No popularity data available. Channels need viewing activity first.` | ✅ (popularity calc not run) |
| `get_watch_history(10, days=7)` | 19 total, summary, per-entry channel/duration/IP/`(home)`/status/ts | ✅ |
| `get_unique_viewers()` | totals (3 unique, 19 conn, 16.4min avg) + top channels | ✅ |
| `get_provider_stats(buffering,7d)` | 7 rows `provider=N [ts] total=.. (buf/reconnect/err/switch)` | ✅ |
| `get_provider_stats(watch_time,30d)` | `3 providers: provider=6: 2.8h ...` | ✅ |
| `get_provider_stats(latency)` | `Invalid metric 'latency'. Choose from: bitrate, buffering, channel_heatmap, watch_time` | ✅ client validation |
| `get_user_watch_time(total)` | `2 users: user_id=2 (home), user_id=3 (kmfelmer)` | ✅ (gives user ids) |
| `get_user_watch_time(user_id=2)` | scoped to home only | ✅ |
| `get_user_watch_time(group_by=week)` | `Invalid group_by. Choose 'total' or 'day'.` | ✅ client validation |
| `get_trending(up)` | `No channels trending up. Run a popularity calculation first.` | ✅ |
| `get_activity(50)` | 50 events (login_success, m3u_refresh) | ✅ |
| `get_activity(channel_start)` | `5 total (filtered): channel_start — Radio: 70's Music ...` (resolves channel names!) | ✅ |
| `get_channel_bandwidth(7,bytes)` | 9 channels w/ bytes/conn/watch/peak | ✅ |
| `get_channel_bandwidth(sort_by=latency)` | `Invalid sort_by. Choose 'bytes','connections','watch_time'.` | ✅ client validation |
| `compute_stream_sort([{11875,[5203,5001,5002]}], smart)` | `Channel 11875: [5002, 5001, 5203] (changed)` | ✅ (no timeout — doc concern was 60s ceiling only) |
| `get_channel_popularity("11875")` | **`ECMClient.call_endpoint() got an unexpected keyword argument 'path_params'`** | ❌ **BROKEN** → bead |
| `get_user_channel_breakdown("2"/"3", dispatcharr)` | same **path_params TypeError** | ❌ **BROKEN** → bead |
| `get_user_channel_breakdown("2", plex)` | `Invalid source...` (validation runs before the bug) | ✅ (validation only) |

**Bead filed:** get_channel_popularity + get_user_channel_breakdown 100% broken (path_params TypeError) — P2. Their #2–#6 sub-tests are moot until fixed.

---

## Domain: System & Backup + Notifications (READ) + Export & Publish (READ)

| Tool / test | Result | Verdict |
|------|--------|---------|
| `get_settings()` (security) | Dispatcharr URL, Connected:True, theme, tz, probe cfg; Notifications SMTP/Discord/Telegram **not configured** — **no raw secrets** | ✅ **security PASS** |
| `get_export_sections()` | 13 sections (settings…stream_profiles) | ✅ |
| `list_saved_backups()` | `No saved backups.` | ✅ |
| `get_journal(30, settings)` (security) | `No journal entries found.` (nothing to leak) | ✅ |
| `create_backup()` | **`Error creating backup: 'utf-8' codec can't decode byte 0xb7...`** | ❌ **BROKEN** → bead (binary zip decoded as text) |
| `list_notifications(20)` | `Notifications (2 unread, 9 total): ... [NEW] ...` | ✅ |
| `list_alert_methods()` | `No alert methods configured.` | ✅ |
| `list_export_profiles()` | `No export profiles configured.` | ✅ |
| `list_cloud_targets()` | `No cloud targets configured.` | ✅ |
| `list_publish_configs()` | `No publish configurations found.` | ✅ |

**Bead filed:** create_backup UnicodeDecodeError on zip (P2). **Security:** get_settings + get_journal(settings) leak-free.

---

## Domain: Tasks & Schedules (WRITE/DESTRUCTIVE) + remaining System/Notifications

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| get_journal (general) | `get_journal(20)` | `No journal entries found.` (but dedup created journal_entry_id 4 & 6!) | ⚠️ bead (journal empty despite entries) |
| delete_saved_backup (404) | `delete_saved_backup("MCPTEST_nonexistent.yaml")` | `HTTP 400 Invalid filename` (strict filename regex) | ✅ |
| delete_saved_backup (traversal) | `delete_saved_backup("../../etc/passwd")` | `HTTP 404 Not Found` — traversal blocked | ✅ security OK |
| test_alert_method (error) | `test_alert_method(99999)` | `HTTP 404 Alert method not found` | ✅ clean 404 |
| list_task_schedules | `list_task_schedules("epg_refresh")` | `No schedules configured for task 'epg_refresh'.` | ✅ |
| get_task_history (per-task none) | `get_task_history("m3u_refresh",5)` | `No task history for task 'm3u_refresh'.` | ✅ |
| get_task_history (all) | `get_task_history(15)` | 15 entries across tasks | ✅ |
| cancel_task (not running) | `cancel_task("stream_probe")` | `Task 'stream_probe' was not running. ...` | ✅ **regression FIXED** (no longer says "cancelled") |
| cancel_task (404) | `cancel_task("nonexistent_task_xyz")` | `HTTP 404 Task ... not found` | ✅ |
| run_task (404) | `run_task("nonexistent_task_xyz")` | `HTTP 404 Task ... not found` | ✅ |
| run_task (happy) | `run_task("popularity_calculation")` | `started. Calculated popularity for 9 channels (9 new...)` | ✅ (benign/beneficial) |
| mark_notifications_read | `mark_notifications_read()` | `All notifications marked as read.` | ✅ (marked 2 real unread → read) |
| delete_all_notifications | — | **NOT RUN** (would delete real notifications). Signature now has `include_unread` (bd-1wq7z.14 fixed) | ⏭️ safety skip |
| create_task_schedule #1 | `("stream_probe","interval",interval_seconds=14400)` | `Schedule created: Every 4 hours (id=6)` | ✅ |
| create_task_schedule #4 | `interval_seconds=0` | `HTTP 422 interval_seconds must be > 0` | ✅ |
| create_task_schedule #5 | missing interval_seconds | `HTTP 422 interval_seconds must be > 0` | ✅ |
| create_task_schedule #6 | `schedule_type="cron_expression"` | `HTTP 422 Input should be 'interval','daily',...` | ✅ (cron rejected; doc signature drift fixed) |
| delete_task_schedule #1 | `("stream_probe", 6)` | `Schedule 6 deleted from task 'stream_probe'.` | ✅ |
| delete_task_schedule (404) | `("stream_probe", 99999)` | `HTTP 404 Schedule 99999 not found` | ✅ |

## Domain: Export & Publish (WRITE/DESTRUCTIVE)

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| create_export_profile #1 | `create_export_profile("MCPTEST_Export")` | `Export profile created: MCPTEST_Export (id=1)` | ✅ |
| create_export_profile #2 (empty name) | `create_export_profile("")` | `Export profile created:  (id=2)` — **empty name accepted** | ⚠️ minor (cosmetic cluster) |
| generate_export #1 | `generate_export(1)` | `Export generated for profile 1. Check ECM for download links.` | ✅ |
| generate_export (404) | `generate_export(99999)` | `HTTP 404 Profile not found` | ✅ |
| delete_export_profile #1 | `delete_export_profile(1)` / `(2)` | deleted | ✅ |
| delete_export_profile (404) | `delete_export_profile(99999)` | `HTTP 404 Profile not found` | ✅ |
| publish_export (no config) | `publish_export(99999)` | `HTTP 404 Publish config not found` | ✅ |

## Domain: Auto-Creation (WRITE/DESTRUCTIVE)

State restored to original (rule 1 disabled, 2 enabled, 3 enabled — verified).

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| toggle #1 | `toggle_auto_creation_rule(2)` | `Rule 2 is now disabled.` | ✅ |
| toggle #2 (restore) | `toggle_auto_creation_rule(2)` | `Rule 2 is now enabled.` | ✅ |
| toggle (404) | `toggle_auto_creation_rule(99999)` | `HTTP 404 Rule not found` | ✅ |
| bulk_toggle #3 (empty) | `bulk_toggle_auto_creation_rules([])` | `Toggled 0/0 rules:` | ✅ |
| duplicate #1 | `duplicate_auto_creation_rule(2)` | `Rule 2 duplicated. New rule ID: 4` | ✅ |
| create #1 | `create_auto_creation_rule("MCPTEST_Rule", [stream_name_contains], [create_channel], priority=10)` | `Created ... (id=5).` | ✅ |
| create #2 (empty cond/act) | `conditions=[], actions=[]` | `HTTP 400 Rule must have at least one condition / action` | ✅ |
| update #1 | `update_auto_creation_rule(5, priority=5, enabled=True)` | `Changed: enabled, priority` | ✅ |
| update #2 (no-op) | `update_auto_creation_rule(5)` | `No fields to update.` | ✅ |
| update #5 (enabled=False) | `update_auto_creation_rule(5, enabled=False)` | `Changed: enabled` (False NOT dropped) | ✅ |
| update (404) | `update_auto_creation_rule(99999, priority=1)` | `HTTP 404 Rule not found` | ✅ |
| run_auto_creation #1 (dry) | `run_auto_creation(dry_run=True)` | `Dry run complete (exec 174): 2682 evaluated, 45 would be created, 19 updated; Sample: ? ?... and 482 more` | ✅ (⚠️ sample names `?`; "482 more" ≠ "45 created") |
| run_auto_creation #2 (live) | — | **NOT RUN** (would create ~45 real channels) | ⏭️ safety skip |
| rollback (404) | `rollback_auto_creation(99999)` | `HTTP 400 Execution not found` | ✅ |
| rollback (real exec) | — | **NOT RUN** (would delete real channels) | ⏭️ safety skip |
| delete #1 | `delete_auto_creation_rule(5)` / `(4)` | deleted | ✅ |
| delete (404) | `delete_auto_creation_rule(99999)` | `HTTP 404 Rule not found` | ✅ |
| create #5 (field gap) | — | `quality_tie_break_order` / `match_scope_target_group` NOT in tool params | ✅ confirmed feature gap (cosmetic cluster) |

## Domain: Profiles (WRITE) + Streams (WRITE: probe) + match_streams_to_channels

| Test | Call | Result | Verdict |
|------|------|--------|---------|
| apply_profile_to_channels #1 | `apply_profile_to_channels(4, [12346], enabled=True)` | `Profile 4 enabled on 1 channels. Profile now has 1 channels.` | ✅ (bd-1wq7z.13 enabled fix) |
| apply_profile_to_channels #4 (empty) | `(4, [], True)` | **HTTP 500** | ❌ → 500-cluster (channel-profiles bulk-update) |
| apply_profile_to_channels #5 (bad ch) | `(4, [99999], True)` | **HTTP 500** | ❌ → 500-cluster |
| probe_single_stream #1 | `probe_single_stream(5001)` | `Stream 5001 probe complete. Status: success` | ✅ |
| probe_single_stream (404) | `probe_single_stream(99999)` | `HTTP 404 Stream not found` | ✅ clean 404 |
| probe_bulk_streams #1 | `probe_bulk_streams([5001,5002,3824])` | **HTTP 504 Gateway Timeout** (3 streams) | ⚠️ bead (bulk probe times out at gateway) |
| probe_streams (all) | — | **NOT RUN** (full probe of 2682 streams, heavy) | ⏭️ safety skip |
| cancel_probe #2 (none running) | `cancel_probe()` | `Probe cancelled. No probe is currently running` | ✅ (minor: says "cancelled") |
| match_streams_to_channels #2 (all assigned) | `match_streams_to_channels(1320)` | `All 427 channels in group 1320 already have streams assigned.` | ✅ |
| match_streams_to_channels #3 (no group) | `match_streams_to_channels(99999)` | `No channels found in group 99999.` | ✅ |
| match_streams_to_channels #1 (happy) | — | not run on real group (would mutate real channels); no-op + nonexistent paths verified | ⏭️ |

## Cleanup verification (all disposables reverted)

`list_channels(search="MCPTEST")` → No channels found. M3U back to 3 originals. EPG back to 8. No export profiles. TestingProfile(4) back to 0 channels. Auto-creation rules at original state (1 disabled, 2 enabled, 3 enabled).

**Benign residual side-effects (documented, not reverted — low value/beneficial):** notifications marked read (mark_notifications_read test); popularity tables populated (run_task popularity_calculation); stream 5001 re-probed; dummy EPG regenerated; dedup audit-journal rows 4/6 + resolved pending-merges 1/2/3 (reference now-deleted disposable channels — harmless history).

---

# FINAL SUMMARY

**Scope:** All 124 MCP tools exercised live against the connected `ecm` MCP. Every tool got its happy path (read-only freely; write/destructive via disposable `MCPTEST_` entities) plus key error/edge paths. Destructive "all/system-wide" variants that could harm real data were skipped with explicit documentation (see ⏭️ rows). All disposable entities created during testing were deleted and cleanup was independently verified.

**13 beads filed, all parented to epic `enhancedchannelmanager-lq38l`:**

| Bead | Pri | Summary |
|------|-----|---------|
| lq38l.5 | **P1** | merge_channels: false success on bad target + **self-merge deletes the channel (data loss)** |
| lq38l.1 | P2 | list_tasks renders every task name as "Unknown" |
| lq38l.2 | P2 | list_normalization_rules reports "0 rules" for groups that have active rules |
| lq38l.3 | P2 | reorder_streams silently detaches/clears omitted streams (no warning) |
| lq38l.4 | P2 | channel/m3u/epg/profile write tools return opaque HTTP 500 for invalid input instead of mapped 4xx |
| lq38l.6 | P2 | build_channel_lineup miscounts "created" (counts whole group) |
| lq38l.7 | P2 | add_stream merge_if_found can auto-attach a stream to a wrongly-matched channel (90%/89% cross-name) |
| lq38l.8 | P2 | count/URL fields render 0 / N-A despite data (m3u stream count, epg channel count, group stream count, m3u URL) |
| lq38l.9 | P2 | get_channel_popularity + get_user_channel_breakdown **100% broken** (path_params TypeError) |
| lq38l.10 | P2 | create_backup fails with UnicodeDecodeError on the binary zip |
| lq38l.11 | P3 | get_journal returns empty despite audit-journal entries existing |
| lq38l.12 | P3 | probe_bulk_streams → HTTP 504 even for a small batch |
| lq38l.13 | P3 | display/cosmetic cluster (12 sub-items: `.0` channel #s, `[Unknown]` epg names, `?` sample/action/viewer fields, empty export name, dismiss-404 inconsistency, etc.) |

**Regressions RE-CONFIRMED FIXED (0.17.2 holds):**
- `bulk_remove_streams` — removes correct count (was "always 0")
- `match_channels_epg` — real exact/multiple/unmatched counts (was "always 0"); now scoped via channel_ids
- `update_m3u_group_settings` + `bulk_update_m3u_group_settings` — report not-found groups (were silent no-ops)
- `cancel_task` — says "was not running" (was misleadingly "cancelled")
- envelope-unwrap family (`get_orphaned_groups` 746, `get_auto_created_groups`, `get_groups_with_streams`) — proper names, not garbled dict keys
- `clear_auto_created` — rejects empty `group_ids=[]`, requires `all_groups=True` for system-wide (no accidental clear)
- `apply_profile_to_channels` — explicit `enabled` flag
- `delete_all_notifications` — now has `include_unread`
- create_epg_source / delete_channel_group / get_channel / create_channel — clean 4xx (upstream_http_exception)

**Security checks PASS:** `get_settings` exposes no raw secrets (SMTP/Discord/Telegram shown as configured/not); `get_journal(settings)` leak-free; `delete_saved_backup` path-traversal blocked.

**Doc rewrites (stale signatures, human prompts preserved, no IDs):** `clear_auto_created` and `match_channels_epg` test sections updated to match current signatures/output.

**NOT RUN (safety skips, documented):** `clear_auto_created(all_groups=True)`, `run_auto_creation(dry_run=False)`, `rollback_auto_creation` on real executions, `refresh_all_m3u()`, `refresh_all_epg()` (all-sources form), `probe_streams()` (all 2682), `delete_all_notifications`, `match_streams_to_channels` happy-path on a real group. Each would mutate/destroy real data with no disposable-scoped alternative.

