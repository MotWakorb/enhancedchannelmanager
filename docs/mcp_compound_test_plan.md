# ECM MCP Compound (Chained) Test Plan

> **Purpose:** Beyond verifying each of the 124 MCP tools in isolation, this plan
> checks whether the tools **chain** — i.e.
> whether the output of one tool feeds correctly into the next across a realistic
> multi-step workflow. The interesting failures here are at the **seams**: an id
> or name that tool A emits but tool B can't consume, an envelope shape that
> doesn't line up, a count that's right alone but stale after a sibling tool
> mutated state, or a workflow that "succeeds" at every step yet leaves the wrong
> end-state.
>
> Run each scenario as a single conversation with your local Claude (Desktop or
> Claude Code) connected to the ECM MCP. Drive it with the natural-language
> prompts; let Claude resolve names→ids via the `list_*`/`search_*` tools. After
> each step, **confirm the tool actually fired and consumed the prior step's
> output** (the "seam check"). Mark the scenario ✅ only if the final verification
> matches the expected end-state.

## How to use this plan

1. **One scenario = one chat.** Keep the whole chain in a single conversation so
   Claude carries the ids/names forward the way a real operator session would.
2. **Watch the seams, not just the steps.** Each scenario lists **Seam checks** —
   the specific handoffs to scrutinize. A chain can pass every individual step and
   still be broken at a handoff (e.g. a tool returns a count that another tool
   then reads under the wrong key).
3. **Talk in names, never ids.** "the 'USA | Sports ⚽' group", "the 'US: ESPN
   FHD' stream". Claude resolves to ids under the hood. If a name is ambiguous it
   should list matches and ask.
4. **Verify the end-state with read tools.** Every scenario ends with a
   `get_channel` / `get_streams_for_channel` / `list_*` confirmation. The chain is
   only good if the *persisted* state is correct, not just if the last tool
   printed "success".
5. **Mark `Result: ☐`** per scenario → ✅ pass · ❌ fail (note the failing seam) ·
   ⚠️ partial.

## ⚠️ Safety

These scenarios **create, mutate, merge, and delete** real entities. Protect your
data:

- **Use disposable, prefixed names.** Everything a scenario creates uses an
  `MCPTEST_` prefix so it's easy to find and revert. Never run the destructive
  steps against your real channels/groups/providers.
- **Back up first.** Run `create_backup` before any scenario that deletes or
  merges (and confirm it returns a size, e.g. "Backup created successfully (… KB)").
- **Each scenario ends with a Cleanup block.** Run it. Then confirm with
  `list_channels(search="MCPTEST")` → "No channels found" and
  `list_channel_groups` (no `MCPTEST_*`).
- **Do not run the system-wide destructive forms** (`clear_auto_created(all_groups=True)`,
  `run_auto_creation(dry_run=False)` against real rules on a production lineup,
  `refresh_all_*` no-arg, `probe_streams` over the whole catalog) as part of these
  chains unless you're on a disposable instance.

## Reference data (live as of the 0.17.2 sweep)

Use these real entities for the read/search/verify parts. Create everything else
disposably.

- **Streams (real):** `US: ESPN FHD`, `US: ESPN 2 FHD`, `US: ESPN 2`, `US: ESPN U`,
  `US: ESPN News`, `US: ESPN` (note: two streams share this exact name),
  `Radio: ESPN Radio`, `US: Music Choice Yacht Rock`, `WY | Casper | NBC 13 KCWY`.
- **Channels (real):** `US : ESPN`, `US : ESPN 2`, `US : ESPN News`, `US : ESPN U`
  (in the `ESPN+` group). Channel display names may carry a `"N | "` number prefix.
- **Groups (real):** `USA | Sports ⚽`, `USA | Movies 🍿`, `USA | Kids 🧸`, `Radio`,
  `USA | Local PBS`, `NFL Game Pass 🏈`, `ESPN+`, `Entertainment`.
- **M3U accounts (real):** `Provider 1` (Xtream, ~2624 streams), `HD Homerun`
  (~56 streams), `custom` (ERROR state).
- **EPG sources (real):** `Teamarr`, `Jesmann Gracenote`, `Jesmann Full`, `B1G EPG`,
  `B1G Advanced EPG` (dummy).
- **Profiles (real):** channel profiles `LiveTV`, `HDHomerun`, `TestingProfile`;
  stream profiles `ffmpeg`, `VLC`.
- **Auto-creation rules (real):** `Testing Rule` (matches stream name contains
  "ESPN"), `Create B1G Channels`, `USA Entertainment`.
- **Users (real):** `home`, `kmfelmer` (Dispatcharr).
- **Tasks (real ids):** `stream_probe`, `epg_refresh`, `m3u_refresh`,
  `popularity_calculation`.

---

# Scenarios

## Scenario 1 — Build a channel from streams, under a brand-new group, with EPG and logo

**Goal:** The canonical "stand up a channel end-to-end" chain — the seam-test the
whole plan exists for.
**Tools chained:** `create_channel_group` → `search_streams` → `create_channel` →
`bulk_add_streams_to_channel` → `bulk_assign_epg` → `match_channels_epg` →
`set_logo_from_epg` → `get_channel` / `get_streams_for_channel`.

**Steps**
1. *"Create a new channel group called 'MCPTEST_ESPN Bundle'."* — `create_channel_group`.
2. *"Search for streams matching 'US: ESPN' and show me the matches."* — `search_streams`. (Expect the six ESPN streams; note there are two named exactly `US: ESPN`.)
3. *"Create a channel called 'MCPTEST_ESPN' number 8001 in the 'MCPTEST_ESPN Bundle' group."* — `create_channel`. **Seam:** the group id from step 1 must be the one used here.
4. *"Add the 'US: ESPN FHD', 'US: ESPN 2 FHD', and 'US: ESPN' streams to that channel."* — `bulk_add_streams_to_channel`. **Seam:** the stream ids resolved in step 2 must be the ones attached; the ambiguous `US: ESPN` should be disambiguated (Claude should pick one and say which, or ask).
5. *"Set the EPG id for 'MCPTEST_ESPN' to 'ESPN.us'."* — `bulk_assign_epg`.
6. *"Run EPG auto-matching for just the 'MCPTEST_ESPN' channel."* — `match_channels_epg(channel_ids=[…])`. **Seam:** the channel id flows from step 3; output reports exact/multiple/unmatched (not "0 matched").
7. *"Set the logo for 'MCPTEST_ESPN' from its EPG entry."* — `set_logo_from_epg`.
8. *"Show me everything about the 'MCPTEST_ESPN' channel and list its streams."* — `get_channel` + `get_streams_for_channel`.

**Expected end-state:** `get_channel` shows `MCPTEST_ESPN`, number 8001, group = MCPTEST_ESPN Bundle's id, EPG TVG ID `ESPN.us`, 3 streams; `get_streams_for_channel` lists the 3 attached streams.
**Seam checks:** (a) group-id created in #1 == group of the channel in #8; (b) the streams searched in #2 are exactly those attached in #4 (no extras, no silent drops); (c) the tvg_id set in #5 survives the EPG match in #6 (match doesn't clobber the manual id unexpectedly); (d) logo step reports assigned (or a clean "no icon_url" skip), not a crash.
**Cleanup:** *"Delete the 'MCPTEST_ESPN' channel, then delete the 'MCPTEST_ESPN Bundle' group."* (or `delete_channel_group(delete_channels=True)`).
**Result:** ☐

---

## Scenario 2 — Fuzzy-match a provider's streams onto a fresh lineup

**Goal:** Test the search→fuzzy→create→assign chain that powers "fill a group from
a provider".
**Tools chained:** `create_channel_group` → `list_channels`(baseline) →
`build_channel_lineup` → `match_streams_to_channels` → `list_channels`(verify) →
`get_streams_for_channel`.

**Steps**
1. *"Create a group 'MCPTEST_Lineup'."* — `create_channel_group`.
2. *"How many channels are in 'MCPTEST_Lineup' right now?"* — `list_channels(group_id=…)` → expect 0. (Baseline for the created-count seam.)
3. *"Build a lineup in 'MCPTEST_Lineup' with channels: 'MCPTEST_ESPN' at 8101 and 'MCPTEST_ESPN 2' at 8102, matching streams from 'Provider 1' in the east market."* — `build_channel_lineup`. **Seam:** "created" must equal 2 (only the new ones), not the group total.
4. *"Now auto-match streams to any unassigned channels in 'MCPTEST_Lineup'."* — `match_streams_to_channels`. **Seam:** it should only touch channels with 0 streams; channels matched in #3 should be skipped.
5. *"List channels in 'MCPTEST_Lineup' with their stream counts, and show the streams on each."* — `list_channels` + `get_streams_for_channel`.

**Expected end-state:** exactly 2 channels created; their stream attachments reflect the fuzzy matches (or a clear "unmatched" if Provider 1 had no name match — both are valid, the point is the count/labels are honest).
**Seam checks:** (a) `build_channel_lineup` "created" count == 2 (regression: it used to report the whole group); (b) `match_streams_to_channels` doesn't re-process the channels `build_channel_lineup` already populated; (c) the "unmatched" list never includes pre-existing channels.
**Cleanup:** delete the 2 channels + the group.
**Result:** ☐

---

## Scenario 3 — Add-stream dedup decision loop (prompt → review → accept/dismiss)

**Goal:** Test the dedup chain end-to-end, including the new exact-match-only
auto-accept behavior.
**Tools chained:** `create_channel_group` → `add_stream`(force_new) →
`add_stream`(prompt) → `list_pending_channel_merges` →
`dismiss_channel_merge` / `accept_channel_merge` → `add_stream`(merge_if_found) →
`get_channel`.

**Steps**
1. *"Create a group 'MCPTEST_Dedup'."* — `create_channel_group`.
2. *"Add the stream 'US: ESPN U' to 'MCPTEST_Dedup', force a new channel."* — `add_stream(dedup_action='force_new')`. Creates the candidate channel.
3. *"Add the stream 'US: ESPN 2' to 'MCPTEST_Dedup', and prompt me if there's a duplicate."* — `add_stream(dedup_action='prompt')`. **Seam:** returns `action=pending_merge` with a `merge_id` + candidate + confidence.
4. *"Show me the pending channel-merge suggestions."* — `list_pending_channel_merges`. **Seam:** the `merge_id` from #3 appears as `pending`, referencing the candidate channel from #2.
5. *"That suggestion is wrong — dismiss it."* — `dismiss_channel_merge(merge_id)`. **Seam:** the id flows from #3/#4; status flips to `dismissed`; no Dispatcharr mutation.
6. *"Add the stream 'US: ESPN U' to 'MCPTEST_Dedup' with merge_if_found."* — `add_stream(dedup_action='merge_if_found')`. **Seam:** because the candidate name matches **exactly**, it auto-accepts and attaches; a *non-exact* name would instead return `pending_merge` (post-0.17.2 behavior).
7. *"Show me the 'MCPTEST_ESPN U'/candidate channel and its streams."* — `get_channel` + `get_streams_for_channel`.

**Expected end-state:** the dismissed suggestion left no merge; the exact-match merge_if_found attached the stream to the existing candidate channel (no duplicate channel created).
**Seam checks:** (a) `merge_id` is stable across prompt→list→dismiss; (b) `merge_if_found` only auto-accepts on an exact normalized-name match (fuzzy → pending_merge, not a silent wrong-merge); (c) accepting/dismissing actually changes `list_pending_channel_merges(status=…)`.
**Cleanup:** dismiss any leftover pending rows; delete the channels + group.
**Result:** ☐

---

## Scenario 4 — Provider onboarding → auto-creation → rollback

**Goal:** The biggest chain: stand up a provider, refresh it, scope its groups,
auto-create channels, then roll it back cleanly.
**Tools chained:** `create_m3u_account` → `get_m3u_account` → `refresh_m3u` →
`list_streams`(provider) → `bulk_update_m3u_group_settings` →
`create_auto_creation_rule` → `analyze_auto_creation_rules` →
`run_auto_creation`(dry) → `run_auto_creation`(live) →
`list_auto_creation_executions` → `rollback_auto_creation` → verify →
`delete_auto_creation_rule` → `delete_m3u_account`.

> ⚠️ Run this only on a disposable instance, or point the M3U URL at a tiny test
> playlist — the live auto-creation step creates real channels (then rolls back).

**Steps**
1. *"Add an M3U account 'MCPTEST_Provider' with url <a small test m3u8>."* — `create_m3u_account`.
2. *"Show me the 'MCPTEST_Provider' account details."* — `get_m3u_account`. **Seam:** Streams count + URL render real values (not 0 / N/A) once refreshed.
3. *"Refresh 'MCPTEST_Provider'."* — `refresh_m3u`; then re-check `get_m3u_account` until stream count > 0.
4. *"List the streams from 'MCPTEST_Provider'."* — `list_streams(provider_id=…)`. **Seam:** provider id from #1 filters correctly; count matches step 2's reported total.
5. *"On 'MCPTEST_Provider', disable every group except the one I want to import."* — `bulk_update_m3u_group_settings`. **Seam:** unknown group names are reported as "not found" (not silently swallowed).
6. *"Create an auto-creation rule 'MCPTEST_Rule' that matches streams from 'MCPTEST_Provider' whose name contains '<token>' and creates channels named after the stream, priority 50, enabled."* — `create_auto_creation_rule`.
7. *"Analyze my auto-creation rules for problems."* — `analyze_auto_creation_rules`. **Seam:** the new rule appears in the report.
8. *"Preview what auto-creation would create — dry run."* — `run_auto_creation(dry_run=True)`. Note the "would be created" count + the execution id.
9. *"Run it for real."* — `run_auto_creation(dry_run=False)`. **Seam:** the live "created" count should match the dry-run "would be created" count.
10. *"Show me the last few auto-creation runs."* — `list_auto_creation_executions`. **Seam:** the live run from #9 is the most recent, status completed, with a real id.
11. *"Roll back that last live execution."* — `rollback_auto_creation(execution_id)`. **Seam:** the execution id from #10 feeds the rollback; the deleted-channel count equals the created count from #9.
12. *"Confirm those channels are gone, then delete 'MCPTEST_Rule' and the 'MCPTEST_Provider' account."* — `list_channels` + `delete_auto_creation_rule` + `delete_m3u_account`.

**Expected end-state:** post-rollback the created channels are gone; rule + provider deleted; stream catalog back to baseline.
**Seam checks:** dry-count == live-count == rollback-count (the three numbers must agree); execution id is stable across run→list→rollback; deleting the provider cascades its streams/groups without orphaning the auto-created channels (already rolled back).
**Cleanup:** as in step 12 (and `delete_orphaned_groups` for any groups the provider left behind — scope to the named ones, never blanket-delete).
**Result:** ☐

---

## Scenario 5 — Duplicate cleanup: find → merge → renumber → reorder

**Goal:** Test the channel-hygiene chain and the data-loss guards added in 0.17.2.
**Tools chained:** `create_channel_group` → `add_stream`×N (seed dupes) →
`find_duplicate_channels` → `bulk_merge_duplicate_channels` →
`assign_channel_numbers` → `reorder_streams` → `get_channel`.

**Steps**
1. *"Create a group 'MCPTEST_Dupes'."*
2. Seed duplicates: *"Force-create channels for the streams 'US: ESPN' and 'US: ESPN' again in 'MCPTEST_Dupes'."* — two `add_stream(force_new)` calls so two same-named channels exist.
3. *"Find duplicate channels."* — `find_duplicate_channels`. **Seam:** the two seeded channels surface as one duplicate cluster (normalization engine).
4. *"Merge that duplicate cluster, keeping one and absorbing the other."* — `bulk_merge_duplicate_channels`. **Seam:** the target/source ids come straight from the cluster in #3; the report counts ACTUAL merges (a bad target reports failure, not false success).
5. *"Assign sequential numbers starting at 8200 to the channels in 'MCPTEST_Dupes'."* — `assign_channel_numbers`.
6. *"Reorder the streams on the surviving channel — put '<stream B>' first."* — `reorder_streams`. **Seam:** the supplied list must be the COMPLETE current stream set; an incomplete list is **refused** (no silent detach).
7. *"Show me the surviving channel and its stream order."* — `get_channel`.

**Expected end-state:** one merged channel with both streams in the requested order; numbers assigned; the absorbed channel gone.
**Seam checks:** (a) merge consumes the duplicate-cluster ids correctly; (b) `reorder_streams` refuses a partial set (regression guard) and only reorders on a true permutation; (c) `merge_channels`/`bulk_merge_duplicate_channels` never report success for a target that doesn't exist.
**Cleanup:** delete the surviving channel + the group.
**Result:** ☐

---

## Scenario 6 — EPG source lifecycle → grid → match → logos

**Goal:** Stand up an EPG source and thread it through matching and logos.
**Tools chained:** `create_epg_source` → `refresh_epg` → `list_epg_sources` →
`get_epg_grid` → `match_channels_epg`(scoped) → `set_logo_from_epg` →
`get_channel`.

**Steps**
1. *"Add an EPG source 'MCPTEST_Guide' with url <a small XMLTV url>."* — `create_epg_source`. (A `file://` url should be rejected with a clean 400.)
2. *"Refresh 'MCPTEST_Guide'."* — `refresh_epg`; then *"List my EPG sources"* — `list_epg_sources`. **Seam:** 'MCPTEST_Guide' shows a real channel count after the refresh (not 0).
3. *"What's on the EPG grid for the 'US : ESPN' channel?"* — `get_epg_grid(channel_id=…)`.
4. *"Match the 'US : ESPN', 'US : ESPN 2', 'US : ESPN News' channels to EPG using only 'MCPTEST_Guide'."* — `match_channels_epg(channel_ids=[…], epg_source_ids=[…])`. **Seam:** scoping by both channel ids and source id works together; output is exact/multiple/unmatched.
5. *"Set logos for those three channels from EPG."* — `set_logo_from_epg`.
6. *"Show the three channels and confirm their EPG ids + logos."* — `get_channel` ×3.

**Expected end-state:** the three channels carry EPG ids/logos sourced from the guide (or clean skips where no icon exists).
**Seam checks:** refresh→count handoff (source channel count updates); `match_channels_epg` honors BOTH the channel-id and source-id scopes simultaneously; logo step reads the EPG link the match step established.
**Cleanup:** *"Delete the 'MCPTEST_Guide' EPG source."* (Re-running matches/logos against real sources afterward is optional; the test channels are real — only the EPG source is disposable here, so verify you didn't leave the three real channels pointing at the deleted source.)
**Result:** ☐

---

## Scenario 7 — Stream-health triage loop

**Goal:** Test the probe→health→struck→cleanup chain.
**Tools chained:** `probe_single_stream` / `probe_bulk_streams` →
`get_stream_health` → `get_probe_results` → `get_struck_out_streams` →
`cleanup_struck_out_streams` → verify.

**Steps**
1. *"Probe the 'US: ESPN FHD' stream."* — `probe_single_stream`. **Seam:** returns a concrete status (success/failed/timeout).
2. *"Probe these three streams: 'US: ESPN FHD', 'US: ESPN 2 FHD', 'WY | Casper | NBC 13 KCWY'."* — `probe_bulk_streams`. (Note: large batches may hit a gateway timeout — keep batches small.)
3. *"What's the overall stream health summary?"* — `get_stream_health`. **Seam:** totals reflect the recent probes.
4. *"Show me the results of the last probe run."* — `get_probe_results`.
5. *"Which streams are struck out?"* — `get_struck_out_streams`. **Seam:** struck streams reference real channel assignments.
6. *(Disposable-instance only)* *"Clean up struck-out streams and delete any channels left empty."* — `cleanup_struck_out_streams(delete_empty_channels=True)`; then re-run `get_struck_out_streams` → expect none.

**Expected end-state:** health/results reflect the probes; (on a disposable instance) cleanup removes struck streams and the second `get_struck_out_streams` is empty.
**Seam checks:** probe results feed the health summary and the struck-out list consistently; cleanup's removed-count matches what `get_struck_out_streams` reported.
**Cleanup:** none created (read-heavy); do NOT run step 6 against a production lineup.
**Result:** ☐

---

## Scenario 8 — Stats cross-reference (read-only, same subject end-to-end)

**Goal:** Confirm the analytics tools agree with each other about the same
user/channel — a pure read chain where the seam is *data consistency*.
**Tools chained:** `get_channel_stats` → `get_watch_history` →
`get_unique_viewers` → `get_user_watch_time` → `get_user_channel_breakdown` →
`get_top_watched` → `get_channel_bandwidth` → `get_channel_popularity`.

**Steps**
1. *"Who's watching right now?"* — `get_channel_stats`.
2. *"Show the last 20 watch-history entries for the past 7 days."* — `get_watch_history`. Note a user (e.g. `home`) and a channel that appears.
3. *"How many unique viewers, and which channels lead?"* — `get_unique_viewers`.
4. *"How much has the user 'home' watched in total?"* — `get_user_watch_time(user_id=…)`. **Seam:** the user resolves to the same id used next.
5. *"What has 'home' been watching, channel by channel?"* — `get_user_channel_breakdown`. **Seam:** the per-channel hours here should roll up toward the total in #4.
6. *"Top 5 most-watched channels?"* — `get_top_watched`. **Seam:** channels here should overlap the watch-history channels in #2.
7. *"Which channels used the most bandwidth this week?"* — `get_channel_bandwidth`.
8. *"How popular is <a channel that actually has watch data>?"* — `get_channel_popularity`. (Run `popularity_calculation` first if popularity tables are empty; an unscored channel returns a clean 404.)

**Expected end-state:** the same users/channels appear consistently across the tools; per-user breakdown sums are consistent with the totals; popularity returns a score for a watched channel.
**Seam checks:** user-id resolution is stable across #4/#5; the channels in watch-history/top-watched/bandwidth are the same real channels (not divergent sets); `get_channel_popularity` and `get_user_channel_breakdown` (the path-param stats tools) return data, not a crash.
**Cleanup:** none (read-only). Running `popularity_calculation` is benign.
**Result:** ☐

---

## Scenario 9 — Cloud targets + backup export-sections discovery

**Goal:** Verify the cloud-targets surface (relocated to `/api/cloud-targets` in v0.18.0, now managed via Settings → Backup & Restore) is discoverable via MCP, and that the backup export-sections tool accurately describes what YAML sections are available for selective restore.
**Tools chained:** `list_cloud_targets` → `get_export_sections`.

**Steps**
1. *"List my cloud storage targets."* — `list_cloud_targets`. **Seam:** returns a list (may be empty on a fresh instance — both empty list and real entries are valid; the key check is that the tool responds with a structured result, not an error).
2. *"What YAML export sections are available?"* — `get_export_sections`. **Seam:** returns a non-empty list of section names (e.g. `normalization_rules`, `auto_creation_rules`, `dummy_epg_profiles`, `channel_profiles`) that represents what a YAML backup/export covers.
3. *(Only if a cloud target exists)* *"Show me the details of the first cloud target."* Note the provider type and whether credentials are masked in the response — they should be (credentials are never returned in clear by `list_cloud_targets`).

**Expected end-state:** `list_cloud_targets` responds with a structured result (empty list is valid); `get_export_sections` returns a non-empty section list; any returned cloud-target credentials are masked.
**Seam checks:** (a) `list_cloud_targets` does not raise an error or return an unstructured blob; (b) `get_export_sections` sections are real names, not an empty list or a stub `["..."]` placeholder; (c) if a cloud target exists, its `provider` field is present and credential fields (access keys, tokens) are absent or masked.
**Cleanup:** none (read-only).
**Result:** ☐

---

## Scenario 10 — Backup-guarded destructive operation

**Goal:** Test the "safety net" chain operators should run around any destructive
action.
**Tools chained:** `create_backup` → `list_saved_backups` → (destructive op on a
disposable: `delete_channel_group(delete_channels=True)`) → verify →
`get_journal`.

**Steps**
1. *"Create a backup now."* — `create_backup`. **Seam:** returns a real success + size (not a decode error).
2. *"List saved backups."* — `list_saved_backups`. (Scheduled YAML backups; may be empty even after #1 since the config backup is a download — note the distinction.)
3. Set up a disposable: *"Create a group 'MCPTEST_Doomed' with a channel in it."*
4. *"Delete the 'MCPTEST_Doomed' group and its channels."* — `delete_channel_group(delete_channels=True)`. **Seam:** reports the channel count it deleted.
5. *"Confirm 'MCPTEST_Doomed' is gone."* — `list_channel_groups` / `list_channels(search="MCPTEST")`.
6. *"Show the recent journal entries."* — `get_journal`. **Seam (known-soft):** confirm whether the destructive op is reflected in the journal (an audit-trail gap is a finding).

**Expected end-state:** backup taken; disposable group + channels deleted; no `MCPTEST_` residue.
**Seam checks:** `create_backup` succeeds before the destructive step; the delete's reported count matches what existed; journal reflects (or is noted as not reflecting) the action.
**Cleanup:** the destructive step IS the cleanup; confirm no residue.
**Result:** ☐

---

## Scenario 11 — Task + schedule lifecycle

**Goal:** Test the task-control chain (run / history / schedule / cancel).
**Tools chained:** `list_tasks` → `run_task` → `get_task_history` →
`create_task_schedule` → `list_task_schedules` → `delete_task_schedule` →
`cancel_task`.

**Steps**
1. *"List my scheduled tasks."* — `list_tasks`. **Seam:** every task shows a real name (not "Unknown"); note a benign task id like `popularity_calculation`.
2. *"Run the Popularity Calculation task now."* — `run_task`. **Seam:** the task name from #1 resolves to its id.
3. *"Show the recent history for that task."* — `get_task_history(task_id=…)`. **Seam:** the run from #2 appears.
4. *"Schedule the Stream Probe task every 6 hours."* — `create_task_schedule(schedule_type='interval', interval_seconds=21600)`.
5. *"List the schedules for Stream Probe."* — `list_task_schedules`. **Seam:** the schedule from #4 appears with an id.
6. *"Delete that schedule."* — `delete_task_schedule(schedule_id=…)`. **Seam:** the id from #5 feeds the delete; a read-back confirms it's gone.
7. *"Cancel the Stream Probe task."* — `cancel_task`. (If not running, expect "was not running", not a false "cancelled".)

**Expected end-state:** task ran (history shows it); schedule created then removed; no leftover MCPTEST schedule.
**Seam checks:** task name→id resolution (#1→#2); schedule id stable create→list→delete; cancel reports truthfully.
**Cleanup:** ensure the schedule from #4 is deleted (step 6).
**Result:** ☐

---

## Scenario 12 — Normalization-driven dedup preview

**Goal:** Confirm the normalization config the engine uses is the same one the
dedup/duplicate tools rely on — a cross-feature consistency seam.
**Tools chained:** `list_normalization_rules` → `test_normalization` →
`find_duplicate_channels` → (interpret).

**Steps**
1. *"What normalization rules are configured?"* — `list_normalization_rules`. **Seam:** groups show real rule counts + names (not "0 rules").
2. *"Test how ECM normalizes 'US : ESPN HD' and '◉ US : ESPN'."* — `test_normalization`. Note what the rules strip.
3. *"Find duplicate channels."* — `find_duplicate_channels`. **Seam:** clusters reflect the SAME normalization shown in #2 (e.g. names that normalize equal are clustered).
4. Interpret: do the rules in #1, the transform in #2, and the clusters in #3 tell a consistent story?

**Expected end-state:** the rule list, the live transform, and the duplicate clustering are mutually consistent.
**Seam checks:** the normalization rules surfaced in #1 are actually applied in #2; #2's normalization is the basis for #3's clustering.
**Cleanup:** none (read-only).
**Result:** ☐

---

# Tracking

| # | Scenario | Result |
|---|----------|--------|
| 1 | Build channel from streams under new group + EPG + logo | ✅ PASS |
| 2 | Fuzzy-match provider streams onto a fresh lineup | ✅ PASS |
| 3 | Add-stream dedup decision loop | ✅ PASS |
| 4 | Provider onboarding → auto-creation → rollback | ✅ PASS |
| 5 | Duplicate cleanup: find → merge → renumber → reorder | ✅ PASS |
| 6 | EPG source lifecycle → grid → match → logos | ✅ PASS (seam fix: znc76.2) |
| 7 | Stream-health triage loop | ⚠️ PARTIAL (znc76.5) |
| 8 | Stats cross-reference (consistency) | ✅ PASS (seam fix: znc76.1) |
| 9 | Cloud targets + backup export-sections discovery | ☐ |
| 10 | Backup-guarded destructive operation | ✅ PASS |
| 11 | Task + schedule lifecycle | ✅ PASS |
| 12 | Normalization-driven dedup preview | ❌ FAIL (znc76.3 — config) |

**Filing findings:** a chain failure is usually a *seam* bug — record which step's output the next step failed to consume, and whether each individual tool behaves correctly on its own. If both tools pass alone but fail chained, that's the high-value finding this plan exists to catch.

## Execution results — 2026-05-23 (live dev instance, agent-driven)

First full run, executed by agents against the live dev ECM. 9/12 PASS; the seam findings were filed under epic `enhancedchannelmanager-znc76` and most were fixed the same day:

- **znc76.1** (S8) — `get_popularity_rankings` / `get_top_watched` now surface the channel `id`, so `get_channel_popularity` is reachable from the chain. **Fixed.**
- **znc76.2** (S6) — `match_channels_epg` now lists the per-channel candidate tvg_ids (with confidence) on a "multiple candidates" result, so the operator can see the options. **Fixed (visibility).** Remaining follow-up: an MCP affordance to *pick + link* a chosen candidate so `set_logo_from_epg` can then proceed.
- **znc76.4** (S4) — `get_m3u_account` now shows the URL for a standard-type account (create normalizes `url`→`server_url`). **Fixed.**
- **znc76.5** (S7) — `probe_bulk_streams` now returns a correct `{total, success, failed}` accounting envelope. **Partly fixed.** Remaining: the bulk endpoint is synchronous and 504s on batches ≥ ~3 — needs an async/background redesign (start+poll); and manual probes still don't populate the `get_probe_results` "latest run" envelope (documented in the tool docstrings).
- **znc76.3** (S12) — the normalization strip rules are **config, not code**: the 7 strip rule groups have `tag_group_id` unset, so they never match (the engine is correct); and Title Case lowercases `US`→`Us` because "US" isn't in the Abbreviation Tags group. **Remedy is rule-data repair** (wire each strip rule's `tag_group_id`/`tag_match_position`; add "US"/"UK"/"EU" to Abbreviation Tags), or a one-shot backfill migration — not an engine change.
- S7's `cleanup_struck_out_streams` was run separately (operator-authorized): 135 struck streams cleared, all unassigned → 0 channels affected.

Reconfirmed pre-existing open items: `lq38l.11` (journal empty for all mutations), `lq38l.12` (probe_bulk 504), `lq38l.13` (cosmetic cluster).
