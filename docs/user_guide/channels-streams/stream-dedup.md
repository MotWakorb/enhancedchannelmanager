# Stream Deduplication

## What the dedup feature does

When a stream's name is similar to an existing channel in the same group, ECM intercepts the "create a new channel" action and asks you whether to **merge into the existing channel** instead of creating a new one. A confidence score (0–100%) shows how closely the incoming stream name matches the candidate channel.

The feature fires on three trigger paths:

| Trigger | When it fires |
|-|-|
| Drag-drop | You drag a **single** stream from the Streams pane onto a channel group |
| Create in… menu | In Edit Mode, you select a **single** channel-less stream and use the selection strip's **Create in…** menu to pick a target channel group |
| Bulk M3U refresh | ECM's Channel Pipeline processes an M3U import and finds candidate matches |

Each trigger path routes to the same dedup decision surface: the **StreamDedupModal** (for interactive triggers) or the **Pending Merges queue** (for the bulk M3U path).

### Where the dedup check does not run

This is the other half of the table, and it is the half that generates support questions. None of these surfaces creates channels through the interactive matcher, so none of them will ever raise a merge prompt, no matter how closely the names match:

| Surface | Why not |
|-|-|
| The plain **Create** button in the Streams panel selection strip | It opens the Create Channels dialog directly. It is not a dedup trigger and never has been. Use **Create in…** if you want the check |
| A multi-stream selection or a multi-stream drop | The check runs on a single stream only. Bulk duplicate handling is the Pending Merges queue, which is fed by M3U refresh, not by the panel |
| Dropping a stream onto an existing **channel row** | You have already told ECM which channel you mean, so there is nothing to disambiguate |
| **Run Pipeline** and **Run Rule** (manual Channel Pipeline runs) | The dedup hook is gated to the M3U refresh trigger. A manual run reports itself as an API-triggered run, and the hook is bypassed for it entirely. Channels are created without a duplicate check |
| CSV import (`POST /api/channels/import-csv`) | The import path never reaches the matcher |

The two interactive surfaces that *do* run the check now say so when the check did not produce a prompt. See [What ECM tells you when there is no prompt](#what-ecm-tells-you-when-there-is-no-prompt).

> **Terminology note:** "merge into existing channel" in this guide means routing the stream into an existing Dispatcharr channel. This is distinct from the two-channel merge in the "Merge channels" editing surface, which combines two full channels. The dedup feature only ever touches one incoming stream and one candidate channel.

---

## Interactive triggers: drag-drop and Create in…

### Drag-drop

1. Select a stream in the Streams pane.
2. Drag it onto a channel group header. (Dropping onto an existing channel row adds the stream to that channel and runs no dedup check.)
3. If ECM finds a candidate channel (a channel whose name is at or above the configured dedup threshold), the **StreamDedupModal** appears.
4. If it does not, ECM tells you which of the three non-prompt outcomes happened. See [What ECM tells you when there is no prompt](#what-ecm-tells-you-when-there-is-no-prompt).

Dragging more than one stream at once skips the check. ECM says so rather than dropping the streams in silently.

### Create in… menu (create channel in a chosen group)

1. Enter **Edit Mode** and select a single channel-less stream in the Streams pane (its row checkbox).
2. In the selection strip at the top of the pane, open the **Create in…** menu and choose an enabled channel group (type to filter the list).
3. If ECM finds a candidate channel in the target group, the **StreamDedupModal** appears.
4. If it does not, ECM tells you which of the three non-prompt outcomes happened. See [What ECM tells you when there is no prompt](#what-ecm-tells-you-when-there-is-no-prompt).

The neighbouring plain **Create** button is not this button. It opens the Create Channels dialog with no dedup check at all. If you want the check, use **Create in…**.

> Before build 0161 this trigger lived on a right-click context menu
> ("Create channel(s) in group"). The menu was replaced by the keyboard-
> accessible **Create in…** menu; the dedup behavior is unchanged.

### What the StreamDedupModal shows

The modal opens titled **"Stream matches an existing channel"** and presents:

- The incoming **stream name**.
- The **candidate channel** (the best matching existing channel), with its name and a confidence score. For a 100% match, the score renders as the words **"Exact match"** instead of a percentage.
- Three buttons:
  - **Merge**: routes the stream into the candidate channel. The stream becomes part of the existing channel; no new channel is created.
  - **Create New**: bypasses the dedup check and creates a new channel as usual.
  - **Cancel**: leaves the stream unassigned and closes the modal.

ECM only shows a candidate when the confidence score is at or above your configured threshold (default 80%).

### What ECM tells you when there is no prompt

An absent merge prompt used to mean nothing in particular. It covered three different outcomes, and the one operators most often read it as ("nothing in the group was similar enough") was frequently not the one that had happened. Both interactive paths, drag-drop and **Create in…**, now name the outcome in the same words, so what you learn on one path holds on the other.

| Notification | What happened | What to do |
|-|-|-|
| *(no notification)* | A candidate cleared the threshold and the **StreamDedupModal** is on screen | Answer the modal |
| **No duplicate found** | The check ran against the target group and nothing was close enough. A new channel is being created | Nothing, unless you expected a prompt. If you did, lower the dedup confidence threshold in Settings |
| **Duplicate check unavailable** | The lookup itself failed. The channel is being created **without** a duplicate check | Check that ECM can reach its backend, then review the group for a duplicate by hand |
| **Duplicate check skipped** | The check never ran: you selected or dropped more than one stream, or ECM could not read the stream's name | Retry with a single stream if you want the check |

A found candidate stays quiet on purpose: the modal is the message, and a toast repeating it would only be noise.

---

## Bulk M3U refresh: the Pending Merges queue

### How pending merges are created

When an M3U refresh runs and the Channel Pipeline encounters a stream whose name matches an existing channel at or above the dedup threshold, ECM **does not** create a new channel immediately. Instead, it places a **pending merge** row in a queue for you to review.

Each pending merge row records:

- The incoming stream name.
- The candidate channel (best fuzzy match in the target group).
- The confidence score at the time of queuing.
- The trigger context (`m3u_refresh`).

The same `(stream_name, candidate_channel)` pair can only appear once in the pending queue. Repeat M3U refreshes of the same stream against the same candidate produce one row, not duplicates.

After the bulk M3U refresh completes, ECM shows a toast notification indicating how many pending merges were queued (e.g., "Auto-Creation: 0 created, 3 pending merges queued"). You can suppress this toast in Settings if you prefer to check the page on your own schedule. See [Settings](#settings-stream-deduplication).

### Navigating to the Pending Merges page

1. Under **Operations**, open **Channel Manager**.
2. The subnav bar shows a **Pending Merges** item with a count badge next to it.
3. Click **Pending Merges** to open the page.

> **The subnav is not always there.** It renders only when the pending count is above zero, or when you are already on the Pending Merges page. With an empty queue there is no subnav bar at all, not a **Pending Merges** item showing zero, so "I cannot find the Pending Merges link" almost always means "there is nothing pending". The count polls every 30 seconds, so a queue filled by an M3U refresh that just finished can take up to that long to appear. Resolving the last row leaves the subnav on screen while you are still on the page, so you always have a way back to **Channels & Streams**. (`frontend/src/components/tabs/ChannelManagerTab.tsx`: the condition is at line 480 and gates the whole `<nav>` at line 517.)

The page lists all pending rows with:

- Stream name
- Candidate channel name and confidence score
- Created-at timestamp
- Per-row action buttons: **Merge** and **Create New**
- A checkbox plus **Select all**, **Deselect all**, **Merge selected**, **Clear selected**, **Merge all**, and **Clear all** controls. **Select all** spans the complete paginated queue, not only the rows currently visible.

### Resolving a pending merge

**Merge** (merge into existing channel)

Clicking **Merge** for a row triggers the same Dispatcharr-side operation as the interactive modal. The stream is added to the candidate channel. The row transitions from `pending` to `merged` and is removed from the active queue.

If the candidate channel was deleted in Dispatcharr between when the row was queued and when you click **Merge**, ECM returns an error: "Target channel no longer exists — dismiss this pending merge and refresh." Click **Create New** or dismiss the row, then re-run the M3U refresh to get a fresh candidate.

**Create New**

Clicking **Create New** dismisses the dedup candidate and signals that you want a new channel created for this stream. The row transitions to `dismissed`. You can then run the Channel Pipeline again or create the channel manually.

### Resolving merges in bulk

Use the row checkboxes for a targeted batch, or **Merge all** / **Clear all** for the entire pending queue. ECM loads one coherent, bounded server snapshot before showing the confirmation, so the count and records you confirm are the records it processes. If the queue exceeds the safety limit, ECM shows an error and changes nothing.

For very large queues, ECM keeps the complete snapshot as the action target but renders at most 200 queue rows at once. Later records move into view as earlier records resolve, preventing the browser from mounting up to 20,000 interactive rows at the safety ceiling.

Every bulk action opens a confirmation dialog showing the exact record count and consequence. After confirmation, ECM processes records one at a time and keeps a live progress message visible. Choose **Stop** to finish only the request already in flight and leave every later record selected for a future retry.

**Merge all is irreversible within ECM.** The confirmation dialog is the safety boundary: review its exact count before continuing. Recovery requires correcting the affected channels in Dispatcharr; ECM cannot automatically undo completed merges.

One failure does not stop the rest of a batch. Successful records disappear; failed records stay visible and selected with their exact backend errors and per-row controls. You can correct the cause and retry only those selected failures.

### Inline error handling

If a **Merge** action fails, an error message appears inline next to the row. The row stays in `pending` so you can retry or choose **Create New**. Common errors:

| Error | Cause | Recovery |
|-|-|-|
| Target channel no longer exists | Candidate channel was deleted after the row was queued | Dismiss the row; re-run M3U refresh |
| Invalid state | Row was already resolved (merged or dismissed) by another session | Refresh the page |

Resolved rows (merged or dismissed) are retained in ECM's audit log indefinitely as a historical record; there is currently no UI that lists them.

---

## Settings: Stream Deduplication

Navigate to **Settings → Channel Defaults → Stream Deduplication**.

### Dedup confidence threshold

Controls how similar a stream name must be to a candidate channel before ECM offers a dedup prompt.

| Property | Value |
|-|-|
| Range | 60–100% |
| Default | 80% |
| Hard floor | 60% (enforced by the matcher; cannot be bypassed) |

A threshold of 80% means ECM only presents a candidate when the fuzzy match score is at or above 80%. Setting the threshold lower (toward 60%) causes ECM to prompt on less confident matches. Setting it higher (toward 100%) causes ECM to only prompt on very close matches.

The hard floor of 60% is an integrity constraint, not a UI setting. It prevents low-quality matches from appearing in the dedup queue regardless of what the threshold is configured to. ECM will never offer a candidate with a confidence score below 60%.

### Suppress "pending merges queued" toast

When checked, ECM suppresses the post-M3U-refresh toast notification that announces how many pending merges were queued. The pending merges are still created and visible on the Pending Merges page; only the toast is hidden.

Use this if you find the toast disruptive or prefer to check the Pending Merges page on your own schedule.

---

## MCP agent access

If you use the ECM MCP server with an AI agent, the dedup surface is exposed through three tools and an `add_stream` extension:

| Tool | What it does |
|-|-|
| `list_pending_channel_merges(group_id?, status?)` | Paginate the pending merges queue; `status` defaults to `pending` |
| `accept_channel_merge(merge_id)` | Merge the stream into the candidate channel; mirrors the **Merge** button |
| `dismiss_channel_merge(merge_id)` | Dismiss the candidate; mirrors the **Create New** path |
| `add_stream(stream_name, group_id, dedup_action?)` | Add a stream with dedup control: `prompt` (return candidates for agent decision), `force_new` (skip dedup), `merge_if_found` (auto-accept if above threshold) |

MCP-driven accepts and dismisses are recorded in the audit log with `trigger_context='mcp_tool'` and attributed to the MCP token, so the journal distinguishes AI-agent decisions from operator decisions.

---

## Frequently asked questions

**Why do I see the same pending merge again after I dismissed it?**

"Not a match" decisions are not remembered between M3U refreshes. If the same stream appears in the next refresh and the candidate channel is still above the threshold, a new pending row will be queued. This is expected behavior in v0.17.1. If dismissal fatigue becomes a problem, raise the dedup threshold to reduce prompts for lower-confidence matches.

**Can I configure the threshold per channel group?**

Not in v0.17.1. The threshold is a single global setting. Per-group overrides are a planned backlog item.

**What happens to merged and dismissed rows?**

They are retained indefinitely in ECM's database as an audit trail; there is currently no UI that lists them. No automatic pruning occurs in v0.17.1.

**Does the dedup feature affect the Channel Pipeline feature's own collision detection?**

No. The Channel Pipeline feature has its own unattended collision detection (`match_scope_target_group` / separate-not-merge). The dedup feature described in this guide is the *attended* (operator-driven) path. The two systems are independent and do not share a matcher.

**What does the confidence score represent?**

It is a fuzzy string similarity score (0–100%) computed by RapidFuzz `token_set_ratio` against the stream name and the candidate channel name. Higher is a closer match. The matcher cleans both names before scoring, so variations in spacing, letter case, and Dispatcharr's channel-number prefix are factored in. That cleaner is the matcher's own and is not the normalization engine; see the next question.

**Does turning off "Normalization Rules" weaken the duplicate check?**

No. The two are independent, which is worth stating plainly now that the **Normalization Rules** toggle in the Create Channels dialog genuinely decides whether names are normalized. A control that real is easy to assume governs everything downstream of it, and this is one thing it does not.

The duplicate check receives the **raw provider name**, before the Create Channels dialog exists, and applies its own cleaner to both that name and every candidate channel name: NFC Unicode normalization, then stripping a leading `N | ` channel-number prefix (Dispatcharr renders channel numbers that way), then lowercasing, then trimming. `US: CNN` and `CNN` therefore still score 100% against each other whether the **Normalization Rules** toggle is on or off, and the same candidate is offered either way.

What the toggle *does* change is the name the resulting channel is created with, and, because that resolved name is also the key the bulk create merges streams on, how many channels a multi-stream create produces. See [Normalization](../normalization/index.md) and [Assign Streams to Channels](assign-streams-to-channels.md).
