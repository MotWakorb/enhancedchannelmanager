# Stream Deduplication

> **Audience:** Operator managing a channel lineup. You have streams arriving from M3U sources and want to prevent or resolve duplicate channels.
>
> **Feature version:** v0.17.1 (ADR-008)

## What the dedup feature does

When a stream's name is similar to an existing channel in the same group, ECM intercepts the "create a new channel" action and asks you whether to **merge into the existing channel** instead of creating a new one. A confidence score (0–100%) shows how closely the incoming stream name matches the candidate channel.

The feature fires on three trigger paths:

| Trigger | When it fires |
|-|-|
| Drag-drop | You drag a stream from the Streams pane onto a channel group |
| Add Stream button | You right-click a channel-less stream and choose "Create channel(s) in group" |
| Bulk M3U refresh | ECM's auto-creation pipeline processes an M3U import and finds candidate matches |

Each trigger path routes to the same dedup decision surface: the **StreamDedupModal** (for interactive triggers) or the **Pending Merges queue** (for the bulk M3U path).

> **Terminology note:** "merge into existing channel" in this guide means routing the stream into an existing Dispatcharr channel. This is distinct from the two-channel merge in the "Merge channels" editing surface, which combines two full channels. The dedup feature only ever touches one incoming stream and one candidate channel.

---

## Interactive triggers: drag-drop and Add Stream

### Drag-drop

1. Select a stream in the Streams pane.
2. Drag it onto a channel group header or an existing channel row.
3. If ECM finds a candidate channel — a channel whose name is at or above the configured dedup threshold — the **StreamDedupModal** appears.

### Add Stream button (Create channel(s) in group)

1. Right-click a channel-less stream in the Streams pane.
2. Choose **Create channel(s) in group** from the context menu.
3. If ECM finds a candidate channel in the target group, the **StreamDedupModal** appears.

### What the StreamDedupModal shows

The modal presents:

- The incoming **stream name**.
- The **candidate channel** — the best matching existing channel — with its name and confidence score.
- Two primary actions:
  - **Merge into existing channel** — routes the stream into the candidate channel. The stream becomes part of the existing channel; no new channel is created.
  - **Create new channel** — bypasses the dedup check and creates a new channel as usual.
- A dismiss (close) action — leaves the stream unassigned and closes the modal.

ECM only shows a candidate when the confidence score is at or above your configured threshold (default 80%). If no candidate meets the threshold, the dedup check is silent and a new channel is created as normal.

---

## Bulk M3U refresh: the Pending Merges queue

### How pending merges are created

When an M3U refresh runs and the auto-creation pipeline encounters a stream whose name matches an existing channel at or above the dedup threshold, ECM **does not** create a new channel immediately. Instead, it places a **pending merge** row in a queue for you to review.

Each pending merge row records:

- The incoming stream name.
- The candidate channel (best fuzzy match in the target group).
- The confidence score at the time of queuing.
- The trigger context (`m3u_refresh`).

The same `(stream_name, candidate_channel)` pair can only appear once in the pending queue — repeat M3U refreshes of the same stream against the same candidate produce one row, not duplicates.

After the bulk M3U refresh completes, ECM shows a toast notification indicating how many pending merges were queued (e.g., "Auto-Creation: 0 created, 3 pending merges queued"). You can suppress this toast in Settings if you prefer to check the page on your own schedule — see [Settings](#settings-stream-deduplication).

### Navigating to the Pending Merges page

1. Open the **Channel Manager** tab.
2. The subnav bar shows a **Pending Merges** item with a count badge (e.g., "Pending Merges (3)") when rows are waiting for a decision.
3. Click **Pending Merges** to open the page.

The page lists all pending rows with:

- Stream name
- Candidate channel name and confidence score
- Created-at timestamp
- Per-row action buttons: **Merge** and **Create New**

### Resolving a pending merge

**Merge** (merge into existing channel)

Clicking **Merge** for a row triggers the same Dispatcharr-side operation as the interactive modal. The stream is added to the candidate channel. The row transitions from `pending` to `merged` and is removed from the active queue.

If the candidate channel was deleted in Dispatcharr between when the row was queued and when you click **Merge**, ECM returns an error: "Target channel no longer exists — dismiss this pending merge and refresh." Click **Create New** or dismiss the row, then re-run the M3U refresh to get a fresh candidate.

**Create New**

Clicking **Create New** dismisses the dedup candidate and signals that you want a new channel created for this stream. The row transitions to `dismissed`. You can then run auto-creation again or create the channel manually.

### Inline error handling

If a **Merge** action fails, an error message appears inline next to the row. The row stays in `pending` so you can retry or choose **Create New**. Common errors:

| Error | Cause | Recovery |
|-|-|-|
| Target channel no longer exists | Candidate channel was deleted after the row was queued | Dismiss the row; re-run M3U refresh |
| Invalid state | Row was already resolved (merged or dismissed) by another session | Refresh the page |

Resolved rows (merged or dismissed) are retained in ECM's audit log indefinitely and are accessible via the API (`GET /api/channel-merges?status=merged` or `?status=dismissed`) for historical review.

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

They are retained indefinitely in ECM's database as an audit trail. You can view them via the API (`GET /api/channel-merges?status=merged` or `?status=dismissed`). No automatic pruning occurs in v0.17.1.

**Does the dedup feature affect the auto-creation pipeline's own collision detection?**

No. The auto-creation pipeline has its own unattended collision detection (`match_scope_target_group` / separate-not-merge). The dedup feature described in this guide is the *attended* (operator-driven) path. The two systems are independent and do not share a matcher.

**What does the confidence score represent?**

It is a fuzzy string similarity score (0–100%) computed by RapidFuzz `token_set_ratio` against the stream name and the candidate channel name. Higher is a closer match. ECM normalizes both names before scoring, so variations in spacing, punctuation, and common suffix patterns are factored in.
