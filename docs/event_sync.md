# Event Sync

One channel per live event across providers. Event Sync (epic
`enhancedchannelmanager-ti939`) designates ONE provider's event group as
the **master** group — Dispatcharr's `auto_channel_sync` stays ON for it,
and Dispatcharr owns the full channel lifecycle (create/update/delete)
from that group. Other providers' event groups are **secondary**: auto-sync
OFF, pure stream sources. ECM matches each secondary stream to a master
channel (parse → time-window blocking → fuzzy scoring of parsed titles →
team-token check) and, in a later phase, attaches the stream to the
matched channel. **ECM never creates or deletes channels in this feature.**

> **Phase status:** attach path implemented (Phase 1B, bead ti939.2.1),
> **manual-run-only**. event_sync rules stay excluded from the pipeline's
> per-stream Pass 1/2 evaluation; on a manually triggered run they execute
> via a dedicated attach phase that resolves matches through the same
> resolver the preview uses and attaches streams via the existing merge
> internals. They never fire from the unattended post-refresh task or any
> scheduled path (auto-run is a Phase 2 explicit opt-in).

## Rule configuration (`event_sync_config`)

An auto-creation rule becomes an event_sync rule by carrying an
`event_sync_config` JSON object. Everything else about the rule
(conditions, actions, sorting) is ignored for this kind — the config IS
the rule.

```json
{
  "master_group_id": 12,
  "secondary_group_ids": [34, 56],
  "patterns": [
    {
      "name": "my-provider-shape",
      "title_pattern": "^(?P<title>.+?)\\s*@",
      "time_pattern": "(?P<hour>\\d{1,2}):(?P<minute>\\d{2})(?:\\s*(?P<ampm>[AaPp])\\.?[Mm]?\\.?)?\\s*(?:E[SD]?T)?\\s*$",
      "date_pattern": "@\\s*(?P<day>\\d{1,2})\\s+(?P<month>[A-Za-z]{3,9})"
    }
  ],
  "group_patterns": {
    "34": [ { "title_pattern": "..." } ]
  },
  "time_window_minutes": 30,
  "attach_threshold": 0.80,
  "enabled": true
}
```

| Field | Required | Meaning |
|-|-|-|
| `master_group_id` | yes | The ONE Dispatcharr group whose channels Dispatcharr owns (`auto_channel_sync` ON). Positive integer group ID. |
| `secondary_group_ids` | yes, non-empty | The secondary event groups (`auto_channel_sync` OFF) whose streams get matched onto master channels. Must NOT contain `master_group_id`. |
| `patterns` | no | Shared parse-pattern variants (title/time/date regexes with named capture groups, same shape as the built-in defaults in `backend/services/event_sync_matcher.py`). Omit to use the built-in defaults. |
| `group_patterns` | no | Per-group pattern overrides, keyed by group ID (master or a secondary). |
| `time_window_minutes` | no (default 30) | Parsed start times must be within ± this window to become candidate pairs. Capped at 1440 (24 hours). |
| `attach_threshold` | no (default 0.80) | Auto-attach score floor on the parsed-title score. **Hard-clamped ≥ 0.80** — it can be raised per rule, never lowered. |
| `max_attach_per_run` | no (default 100) | Per-run attach cap (1–1000). On overage the run stops attaching, warns in the execution log, and records the overage count. Runs are idempotent — run again to continue. |
| `enabled` | no (default true) | Feature toggle within the rule. |

### Why validation is strict

Validation errors are designed to teach — each carries the field, the
value you sent, what was expected, and a link back to this document.

* **Mandatory scoping** (`master_group_id` present, `secondary_group_ids`
  non-empty, master not in secondaries) is schema-enforced, not
  convention. It is the rail that prevents recurrence of the prior
  fuzzy-matching incident (1,341 false-positive merges): an unscoped
  event rule is refused at save time.
* **Parse regexes compile through `safe_regex` at save time.** Operator
  regex is the ReDoS surface; the save-time compiler is the exact one the
  runtime uses.
* **The 0.80 attach floor is hard-clamped twice** — rejected below the
  floor at save time, and clamped again at runtime by the matcher's
  admission policy (`EVENT_ATTACH_FLOOR` in
  `backend/services/event_sync_matcher.py`, the single source of truth).
  Precision over recall everywhere.
* **`time_window_minutes` is capped at 1440 (24 hours).** The time window
  is the rail that keeps same-teams-different-day fixtures apart — an
  oversized window re-opens that false-positive class, and the frozen
  regression corpus only proves the matcher's precision at sane windows.
* **Unknown keys are rejected**, so a typo'd optional key cannot silently
  fall back to its default.

## Previewing matches (Phase 1A)

`POST /api/channel-pipeline/event-sync-preview` runs the full matcher
against live Dispatcharr data with **zero writes** — per-stream match rows
(score, band, team-token verdict, time delta, reject reason), unmatched
streams, parse failures grouped by group, and summary counts that
reconcile exactly with the detail rows. It accepts either a saved rule id
or an inline `event_sync_config` (so the rule editor can preview before
saving). Full request/response contract: `docs/api.md`. Headless mirror:
the `preview_event_sync` MCP tool. The preview and the attach path share
one resolver (`backend/services/event_sync_resolver.py`), so what the
preview shows is what a run does — dry-run parity by construction.

## Running the attach path (Phase 1B)

A **manual** pipeline run (the Run button / `POST /api/channel-pipeline/run`
or a single-rule run) executes event_sync rules through a dedicated attach
phase:

* Every secondary stream resolves through
  `backend/services/event_sync_resolver.py` — **the same function the
  preview calls**, so preview decisions and run decisions are identical on
  identical inputs.
* **Band semantics:** attach band → the stream is attached to the master
  channel via the existing merge machinery; ambiguous → skipped and
  counted (including the **contested rail**: more than one attach-band
  candidate, or a runner-up within 0.05 of the winner, is ambiguous —
  never an attach to an alphabetical tie-break); reject / unmatched /
  parse-failed → skipped with reason.
* **Idempotent:** a stream already on its master channel is a no-op, so
  re-running after every refresh is safe and is the intended usage.
* **Journal provenance:** every attach writes a journal entry (category
  `event_sync`, batch_id = execution id) carrying names alongside IDs —
  secondary stream name+id, provider, master channel name+id — plus score,
  band, time delta and team-token verdict.
* **Run summary line** in the execution log and on the execution record:
  `event_sync: X attached, Y ambiguous skipped, Z unmatched, W parse
  failures` — the operator's drift detector. A silently broken parse
  pattern shows up here as a parse-failure spike within a day.
* **Rollback:** the run's pre-mutation snapshot includes the master
  group's channels, so the standard execution rollback / snapshot restore
  undoes attaches.

## Pre-flight checks

Before a preview (and later, a run), ECM verifies against Dispatcharr —
read-only, ECM never toggles group settings:

* master group has `auto_channel_sync` **ON** (otherwise no master
  channels exist and the whole feature silently matches nothing);
* every secondary group has `auto_channel_sync` **OFF** (otherwise
  Dispatcharr is creating duplicate channels from a stream-source group);
* every configured group still exists in some account's group settings.

Failures surface in the preview/run results with the expected/actual
setting and which group failed.

## What ECM deliberately does NOT do

* **No channel lifecycle.** Dispatcharr creates, updates and deletes the
  master channels (verified: its sync task updates in place, preserves
  channel UUIDs, never resets a channel's stream list, and deletes a
  channel only when the master provider drops the stream — the cascade
  detaches secondary streams cleanly).
* **No orphan reconciliation.** event_sync rules never populate
  `managed_channel_ids` and hard-bypass the pipeline's Pass 4 orphan
  cleanup — reconciling channels ECM doesn't own would delete or move
  Dispatcharr-owned channels.
* **No persisted channel IDs.** Matching is recomputed statelessly every
  run; master channels are the identity anchor. Any state that must
  survive refreshes keys on event identity, never channel/stream IDs.
* **No auto-run.** Manual-run-only until Phase 2's explicit opt-in flag.
  Enforced twice: the unattended watermark task excludes event_sync rules
  from its query, and the engine refuses to execute them for any
  non-manual trigger (deny-by-default).

## Operator caveats

Wrong attachments are reversible and non-compounding but NOT self-healing
— deterministic matcher repeats a bad match until the operator adjusts a
pattern/threshold or the provider renames.

Known edge: Dispatcharr channel groups are global **by name** (bd-dgs64).
If a secondary provider publishes a group with the SAME name as another
account's auto-synced group, they share a group ID, and the pre-flight
secondary check will fail for it (correctly — Dispatcharr is auto-syncing
that group ID). Real event groups are provider-distinct-named.
