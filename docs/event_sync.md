# Event Sync

> One channel per live event across providers. This guide is dual-audience:
> the first half is for operators consolidating provider event groups; the
> [Developer reference](#developer-reference) at the bottom is for engineers
> working on the matcher, resolver, or schema.

> **Phase status: attach path implemented (Phase 1B, bead ti939.2.1);
> opt-in auto-run implemented (Phase 2, bead ti939.3.1) — manual-run-only
> by default.** event_sync rules stay excluded from the pipeline's
> per-stream Pass 1/2 evaluation; on a **manually triggered** pipeline run
> they execute via a dedicated attach phase that resolves matches through
> the same resolver the preview uses and attaches streams via the existing
> merge internals. A rule additionally runs from the unattended
> post-refresh watermark task **only** when its config carries the explicit
> `auto_run: true` opt-in; scheduled paths stay denied for every rule. See
> [Running the attach path](#running-the-attach-path-phase-1b) and
> [Automatic runs after refresh](#automatic-runs-after-refresh-phase-2-opt-in).

## Overview

Operators with multiple IPTV providers get N duplicate channels per
sports/PPV event — one per provider's auto-sync event group — because the
same real-world event is named differently by every provider (slot
prefixes, team abbreviations, date formats). Event Sync collapses those N
channels down to one.

The model: designate **one** provider's event group as the **master**
group. Dispatcharr's `auto_channel_sync` stays **ON** for it, and
Dispatcharr owns the full channel lifecycle (create/update/delete) from
that group, exactly as it does today. Every other provider's event group
is a **secondary**: `auto_channel_sync` **OFF**, a pure stream source.
ECM matches each secondary stream to a master channel — parse the name to
(event title, start time), block by time window, fuzzy-score the parsed
titles, cross-check team tokens — and, on a manual run, attaches the
matched stream to that master channel (failover + quality choice on one
channel number). **ECM never creates or deletes channels in this
feature** — Dispatcharr does, from the master group only.

## Quick start — Consolidate event groups across providers

This walkthrough takes one live-events use case (say, three IPTV providers
that each publish a "Sports"/"Events" group with the same fixtures) from
raw duplicate channels to a working preview.

### 1. Pick the master group

Pick the provider whose event group is broadest and most reliable —
usually the one with the most complete fixture list and the most
consistent naming. Its Dispatcharr `auto_channel_sync` should already be
ON (leave it ON; that's what makes it eligible as a master). This group's
channels are the ones every other provider's stream will attach to, so
picking the group with the best coverage minimizes how many events end up
in the [unmatched list](#events-missing-entirely-master-as-ceiling).

### 2. Turn secondary auto-sync OFF

For **every other** provider's event group, disable `auto_channel_sync` in
Dispatcharr (M3U Manager → account → Groups) — or use the rule editor's
**Fix** button (Phase 2, bead ti939.3.4): when the editor's live status
shows a secondary with auto-sync ON (or the master OFF), it offers a
one-click fix behind its own confirmation dialog. The toggle is an
explicit, separately confirmed operator action — never a side effect of
saving a rule or running the pipeline — and every toggle is journaled.
See [Guided setup: the confirmed auto-sync
fix](#guided-setup-the-confirmed-auto-sync-fix). If you skip this,
Dispatcharr keeps creating its own channels from the secondary group and
you're back to duplicates regardless of what ECM matches — see [the
auto-sync gotcha](#still-seeing-duplicate-channels) below.

### 3. Create the Event Sync rule

Channel Pipeline tab → **Create Rule**. Pick **Event Sync rule** from the
kind chooser (not Standard rule — Event Sync rules carry a JSON config
instead of conditions/actions and never run in a Standard rule's engine
path):

![Create Rule modal with two kind options: Standard rule (conditions + actions) and Event Sync rule (one channel per live event across providers, preview-only in this phase)](images/event_sync/1-kind-chooser.png)

### 4. Configure master + secondary groups

Name the rule, then pick the master group and the secondary group(s) from
the dropdowns. The editor shows **live** auto-sync status per group and
warns immediately if the master is OFF or a secondary is still ON:

![Create Event Sync Rule editor: Master group dropdown showing "USA | Peacock Events — auto-sync OFF" with a warning banner explaining Dispatcharr creates no master channels until auto_channel_sync is enabled](images/event_sync/2-editor-master-guidance.png)

Fix any warning here before moving on — the same checks re-run as
[pre-flight](#pre-flight-checks) on every preview.

Both dropdowns list only groups **enabled** on their M3U/provider account by
default — a real instance can have hundreds of groups, and most are not
relevant to this rule. Check **Show all groups** above the pickers to reveal
disabled groups too (useful for a temporarily-disabled group, or one with no
provider settings at all). A group the rule already references stays visible
— marked `(disabled)` — even when it's filtered out of the default list, so
editing an existing rule never silently drops its saved master or secondary
selection.

### 5. Keep the shipped default pattern

The **Parse patterns** section ships with the two built-in patterns
pre-selected (`slot-title-day-first-date`, `slot-title-month-first-date`).
These cover the most common live-stream shape — optional two-digit slot
prefix, title, `@ <date> <time>` — and most rules never need a custom
regex. See the [pattern cookbook](#pattern-cookbook) below if your
provider's names don't fit.

### 6. Use Test Patterns

Before trusting a pattern selection, expand **Test patterns against sample
names**, paste (or fetch live) sample stream names from your groups, and
run the test. It shows exactly what title / date / time each pattern
extracts per name, using the *same* server-side extraction machinery the
matcher uses at preview time — so a green row here is a green row in the
preview, not a guess:

![Test Patterns table showing raw names against Title/Date/Time columns and a Parse status column — several "Fubo Sports Network NN :" placeholder rows flagged "Incomplete date/time" next to one fully "Parsed" row](images/event_sync/3-test-patterns.png)

A row flagged **Incomplete date/time** means Event Sync will never guess
that name's start time — it will show up as a `parse_failed` stream in
the preview, not a mismatch. That's expected for placeholder/filler slots
that haven't been assigned an event yet.

**Parsed** means the matcher would actually build a start time — not just
that the date/time groups were captured. A name like
`A vs B @ 45 Jul 06:00 PM ET` (day 45) or a custom pattern that captures a
garbage month shows its extracted parts in the table but is flagged
**Invalid date/time**: it too would be a `parse_failed` stream, because
Event Sync validates the month name, the hour (≤ 23), and that the result
is a real calendar date before it ever compares times.

### 7. Read the Preview

Click **Preview matches**. The response is a zero-write dry run against
live Dispatcharr data:

![Event Sync preview: a pre-flight warning banner for a master group with auto-sync OFF, a summary line "0 would attach, 0 ambiguous (skipped), 11 unmatched, 55 parse failures · 0 master channels", and per-stream match cards](images/event_sync/4-preview-summary.png)

Read it top to bottom:

- **Pre-flight banner** (if present) — a misconfigured group, shown loudly
  rather than silently producing an empty result. Fix it, don't ignore it.
- **Summary line** — `would_attach` / `ambiguous (skipped)` / `unmatched`
  / `parse_failed` counts that always sum to the total secondary stream
  count, plus the master channel count.
- **Match cards** — one per secondary stream, with its disposition badge,
  parsed title/start, and (for `would_attach`) the master channel it
  would attach to plus every scored candidate in a table (score, band,
  team-token verdict, time delta, reject reason).
- **Unmatched secondary streams** and **Parse failures** — see
  [Troubleshooting](#troubleshooting) below.

If the counts and match cards look right, **Save** the rule, then execute
it with a **manual pipeline Run** — see
[Running the attach path](#running-the-attach-path-phase-1b). Preview
itself never writes; the attach only happens on a run you trigger.

Saved Event Sync rules carry a badge in the rule list. Their per-rule
**Run** / **Dry Run** icons remain hidden in this phase (execute them via
the pipeline-level manual Run, or the single-rule run API):

![Channel Pipeline rule list showing a "Live Events (multi-provider)" rule with an "Event Sync" badge, and no play/eye/dry-run icons in its Actions column (only edit, duplicate, delete)](images/event_sync/7-rule-list-badge.png)

## Pattern cookbook

The three built-in patterns cover the majority of live-stream naming shapes
observed across providers. Below are the verified provider name shapes
and the pattern that parses each — copy-paste consistent with the shipped
patterns in `frontend/src/components/channelPipeline/eventSyncShippedPatterns.json`
and the matcher defaults in `backend/services/event_sync_matcher.py`
(every regex below was run against its example through the real
`parse_event_name()` while writing this guide).

> **The built-ins are tolerant of real-world noise (beads 9c9j7 + numeric
> dates).** They accept `@`, `|`, **or** `(` as the title/date delimiter, an
> optional weekday before the date (`| Sun 12 Jul 02:00 EDT`), a **numeric
> month-first date** (`(7.12 9:15 AM ET)` / `(7/12 12:00 PM ET)`), and —
> crucially — **any trailing text after the time** (a provider/slot label
> like `... @ Jul 11 9:30 AM :Flo Racing 03`, or a region marker like
> `... | Sun 12 Jul 02:00 EDT (US) | US: ESPN+ PPV 40`). A trailing suffix
> after the time used to cause an "Incomplete date/time — would be a parse
> failure"; it no longer does. Single-digit hours (`9:30`) parse too.
>
> **Still a parse failure by design:** a listing with a time but **no date**
> (`Boxing 05 : FURY vs HALL 6PM`, `LIVE EVENT 05 - 4:15pm ...`). Event Sync
> never guesses the date. See [Dateless live listings](#dateless-live-listings).

### 1. Slot-prefixed, month-first date, "@" or "|" (built-in)

```
Fubo Sports Network 07 : Chelsea vs. Brentford @ Jan 17 10:00 AM ET
```

Parses to title `Chelsea vs. Brentford`, start `Jan 17 10:00 AM ET`. This
is the **`slot-title-month-first-date`** built-in — no configuration
needed, it's pre-selected by default.

```
title_pattern: ^(?:[^@:|(]{0,40}?(?<!\d)\d{2}\s*:\s*)?\s*(?P<title>.+?)\s*(?:(?:@|\||\()\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[A-Za-z]*\.?,?\s+)?(?:\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]?\.?)?(?:\s*E[SD]?T)?|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:\s*,?\s*\d{4})?\s+\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]?\.?)?(?:\s*E[SD]?T)?|\d{1,2}[./]\d{1,2}\s+\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]?\.?)?(?:\s*E[SD]?T)?).*)?$
time_pattern:  (?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?\s*(?:E[SD]?T)?\s*$
date_pattern:  (?:@|\||\()\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[A-Za-z]*\.?,?\s+)?(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2})(?:\s*,?\s*(?P<year>\d{4}))?\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?(?:\s*E[SD]?T)?
```

### 2. Slot-prefixed, day-first date, "@" or "|" (built-in)

```
Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET
```

Parses to title `Mercury vs. Aces`, start `11 Jul 06:00 PM ET`. This is
the **`slot-title-day-first-date`** built-in — also pre-selected by
default; the two built-ins run in order and the first complete match
wins, so most rules can leave both on and cover both date shapes without
any per-provider configuration.

```
title_pattern: ^(?:[^@:|(]{0,40}?(?<!\d)\d{2}\s*:\s*)?\s*(?P<title>.+?)\s*(?:(?:@|\||\()\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[A-Za-z]*\.?,?\s+)?(?:\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]?\.?)?(?:\s*E[SD]?T)?|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:\s*,?\s*\d{4})?\s+\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]?\.?)?(?:\s*E[SD]?T)?|\d{1,2}[./]\d{1,2}\s+\d{1,2}:\d{2}(?:\s*[AaPp]\.?[Mm]?\.?)?(?:\s*E[SD]?T)?).*)?$
time_pattern:  (?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?\s*(?:E[SD]?T)?\s*$
date_pattern:  (?:@|\||\()\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[A-Za-z]*\.?,?\s+)?(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})\.?\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?(?:\s*E[SD]?T)?
```

### 3. Slot-prefixed, numeric month-first date (built-in)

Some providers list the date **numerically**, often in parentheses:

```
PPV EVENT 01: Zenith Racing Series at Road America (7.12 9:15 AM ET)
PPV EVENT 10: Redstall vs. Courtney (7/12 12:00 PM ET)
```

Parses to title `Zenith Racing Series at Road America` / `Redstall vs.
Courtney`, start `Jul 12 09:15 AM ET` / `Jul 12 12:00 PM ET`. This is the
**`slot-title-numeric-date`** built-in (pre-selected). Numeric dates are
read **month-first** (US convention, matching the ET default timezone) and
accept `.` or `/` — a provider that lists numeric dates day-first needs a
per-rule override. `(`, `@`, and `|` all work as the opener.

```
date_pattern:  (?:@|\||\()\s*(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[A-Za-z]*\.?,?\s+)?(?P<month>\d{1,2})[./](?P<day>\d{1,2})\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})(?:\s*(?P<ampm>[AaPp])\.?[Mm]?\.?)?(?:\s*E[SD]?T)?
```

(title_pattern and time_pattern are identical across all three built-ins.)

### 4. Slot-prefixed, no "@" separator (shipped, not pre-selected)

Some providers drop the "@" between title and date entirely:

```
NHL Center Ice 03: Rangers vs Islanders 24 Jan 07:00 PM ET
NHL Center Ice 03: Rangers vs Islanders Jan 24 07:00 PM ET
```

Both parse to title `Rangers vs Islanders`, start `24 Jan 07:00 PM ET` /
`Jan 24 07:00 PM ET` respectively. These are shipped as
**`title-day-first-date-no-at`** / **`title-month-first-date-no-at`** in
the pattern picker — check the box for whichever date order your
provider uses (or both). They are not selected by default because the
"@"-based patterns are the common case and an unnecessary extra pattern
only adds a small amount of matching work per name.

```
title_pattern (day-first): ^(?:[^@:]{0,40}?(?<!\d)\d{2}\s*:\s*(?!\d))?\s*(?P<title>.+?)\s*(?:(?:@\s*)?(?:\d{1,2}\s+[A-Za-z]{3,9}\s+\d{1,2}:\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:\s*,?\s*\d{4})?\s+\d{1,2}:\d{2}).*)?$
date_pattern (day-first):  (?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})\s+\d{1,2}:\d{2}
```

(time_pattern is the same on all five shipped patterns.) If your
provider's shape doesn't match any of them, add a custom shared
pattern (or a per-group override) in the rule editor's **Advanced**
section and verify it with Test Patterns before saving — do not guess.

### Dateless live listings

Some providers list a live "today" schedule with a **time but no date**:

```
Boxing 05 : FURY vs HALL 6PM
Boxing 6: Fury v Makhmudov 19:00
LIVE EVENT 05 - 4:15pm Zenith Racing Series Road America
```

By default these are a **parse failure** — Event Sync's core safety rail is
that it **never guesses the date** (otherwise it could match yesterday's
"6PM" to today's), so a listing that omits the date is shown as "Incomplete
date/time" in the preview rather than guessed onto "today".

If a group **only ever lists the current day** and you accept the cross-day
risk, set **`assume_current_date: true`** on the rule (in the editor:
**"Assume today's date for dateless listings"** under Advanced). With it on,
a listing that carries a time but no date is placed on the **current date**
(in the rule's timezone) so it becomes matchable. Both the master and
secondary sides share the same "today", so their times compare on one day.
The built-in dateless shapes cover `… TITLE 6PM` / `… TITLE 19:00` /
`… TITLE @ 06:00 PM ET` (time last, an optional `@`/`|` before the time is
stripped) and time-first after a `:`/`-`/`|` slot separator
(`PPV 06: 4:15pm TITLE`, `LIVE EVENT 05 - 4:15pm TITLE`); a bare time must
carry a colon or an am/pm marker so a lone number in a title is never
mistaken for a time.

**The risk:** a listing that is really for a *different* day (a replay at the
same time-of-day tomorrow) can mis-match — the ±`time_window_minutes` window
still applies, but same-time-of-day collisions can slip through. And even a
correctly-dated listing only attaches when its time lands within the window
of the master, so a group whose listed times are unreliable (hours off the
real start) still won't pair. Leave the flag **off** unless the group is a
genuine same-day live schedule.

**The stale-name guard (bead jqwfq).** The worst cross-day failure is a
provider that leaves *yesterday's* dateless slot name sitting in the
playlist: with `assume_current_date` on, that stale name is placed on
today, scores a perfect match against today's master, and attaches to the
wrong channel. By default (`demote_stale_dateless: true`, editor: **"Send
stale dateless names to review instead of attaching"**) Event Sync checks
each dateless would-attach against the provider's **previous-day M3U
snapshot** (captured by the M3U change monitor): a name that was already
there yesterday is routed to the review queue with reason
`stale_dateless_stream_name` instead of auto-attached. Approving it in the
queue attaches it and remembers the answer. The check **fails open** —
only positive snapshot membership demotes; a missing snapshot, a group
whose names weren't captured (disabled), or a group at the 500-name
capture cap yields "unknown" and never blocks an attach. Dated listings
are never touched. If the group carries a genuinely recurring daily event
(the same name every day, on purpose), turn the guard off — that is the
escape hatch it ships with. The preview surfaces the raw signal per stream
(`name_seen_before_today`) plus `stale_suspect_streams` /
`freshness_unknown_streams` summary counts so you can judge the signal's
coverage before trusting it.

## Rule configuration (`event_sync_config`)

An auto-creation rule becomes an event_sync rule by carrying an
`event_sync_config` JSON object. Everything else about the rule
(conditions, actions, sorting) is ignored for this kind — the config IS
the rule.

```json
{
  "master": { "group_id": 12, "m3u_account_id": 3 },
  "secondary": [
    { "group_id": 12, "m3u_account_id": 7 },
    { "group_id": 56, "m3u_account_id": null }
  ],
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
  "enforce_time_window": true,
  "attach_threshold": 0.80,
  "max_attach_per_run": 100,
  "enabled": true
}
```

### Provider-scoped groups (`master` / `secondary`)

The **canonical** scoping shape is provider-scoped: `master` is one scope
object and `secondary` is a list of them. A scope is
`{ "group_id": int, "m3u_account_id": int | null }` — a channel group plus,
optionally, the ONE M3U provider account whose streams that scope draws from.
`m3u_account_id: null` means the **whole group** (every provider's streams in
it), which is the pre-provider-scope behaviour.

This exists because Dispatcharr channel-group names are globally unique (see
[Two providers, one group name](#two-providers-one-group-name)): a group two
providers both carry is ONE `group_id` with two per-provider junctions. The
scope's `m3u_account_id` is what lets you draw provider A's copy as the master
and provider B's copy of the **same group** as a secondary — the case that
had no expression under the flat shape.

**Derived flat keys (`master_group_id` / `secondary_group_ids`).** The
validator derives these scalar keys from the scopes on every save and stores
**both** — they are a denormalization the execution hot path still reads
directly, kept in lockstep by re-derivation on each persist (they can never
drift from the scopes). A **legacy** rule that carries only the flat keys is
auto-upgraded to whole-group scopes (`m3u_account_id: null`) on read — no
migration, no data touched. Sending only the flat keys is still accepted for
backward compatibility; the rule editor now reads and writes the nested shape.

| Field | Required | Meaning |
|-|-|-|
| `master` | yes† | The master scope `{group_id, m3u_account_id}`: the ONE Dispatcharr group whose channels Dispatcharr owns (`auto_channel_sync` ON), optionally scoped to one provider. †Either `master` (canonical) or the legacy `master_group_id` must be present. |
| `secondary` | yes*† | The secondary scopes (each `auto_channel_sync` OFF) whose streams get matched onto master channels. A scope must NOT duplicate the master's exact `(group_id, m3u_account_id)` pair — but the **same group under a different provider** IS allowed. *May be **empty** when `include_master_group_streams` is true (bead 3ux85). †Legacy `secondary_group_ids` is accepted in its place. |
| `master_group_id` | derived | Legacy/derived scalar master group ID. Present on every stored config (derived from `master`); accepted as input for backward compatibility. Positive integer. |
| `secondary_group_ids` | derived | Legacy/derived list of secondary group IDs (derived from `secondary`). Must NOT contain `master_group_id`. |
| `patterns` | no | Shared parse-pattern variants (title/time/date regexes with named capture groups, same shape as the built-in defaults in `backend/services/event_sync_matcher.py`). Omit to use the built-in defaults. API-authored arrays survive UI resaves: the rule editor round-trips the full array (an untouched save emits it verbatim; patterns beyond the one custom slot the UI can edit are preserved read-only, and built-ins are never silently re-added to an all-custom selection). |
| `group_patterns` | no | Per-group pattern overrides, keyed by group ID (master or a secondary). A group with an override uses ONLY its own patterns for parsing; other groups keep the shared `patterns` selection. Multi-pattern lists round-trip through the UI the same way as `patterns` (the editor edits only the first pattern per group; the rest are preserved). |
| `time_window_minutes` | no (default 30) | Parsed start times must be within ± this window to become candidate pairs. Capped at 1440 (24 hours). Ignored entirely when `enforce_time_window` is false. |
| `enforce_time_window` | no (default **true**) | When false, the time-window candidacy gate is disabled: every parsed master event is a candidate and matches rank on title/team score alone, ignoring `time_window_minutes`. Rescues events whose providers publish different start times for the same fixture. **Safe only for a single-provider, same-day master group** — for recurring or serial titles the missing time gate can match the wrong day's channel. The team-conflict/numeric-identity rails and the 0.90 no-teams floor still apply, so borderline pairs route to review, not auto-attach. See [Disabling the time window](#disabling-the-time-window). |
| `attach_threshold` | no (default 0.80) | Auto-attach score floor on the parsed-title score. **0.80 is the default, not a hard minimum** (operator-authoritative): any value in `[0, 1]`. Raise it for stricter matching; lower it when a provider's titles carry slot/venue noise that caps the score. A lower floor auto-attaches weaker matches — review the preview first. Team-conflict and different-event-number pairs are hard-rejected at any threshold. See [Threshold and bands](#threshold-and-bands). |
| `max_attach_per_run` | no (default 100) | Per-run attach cap (1–1000). On overage the run stops attaching, warns in the execution log, and records the overage count. Runs are idempotent — run again to continue. |
| `enabled` | no (default true) | Feature toggle within the rule. |
| `auto_run` | no (default **false**) | Phase 2 opt-in (bead ti939.3.1): when true, the rule also runs **unattended** after each M3U refresh (the watermark task). Absent means false — manual-run-only. See [Automatic runs after refresh](#automatic-runs-after-refresh-phase-2-opt-in). |
| `refresh_providers_before_run` | no (default **false**) | bead y8yby: when true, a **manual** run of this rule (live **Run** AND dry-run **Test**) first refreshes the M3U provider accounts backing the rule's master + secondary groups, then runs the match/attach — closing the refresh-ordering staleness window. **Consequence:** with this on, **Test is no longer zero-write** (it triggers a real Dispatcharr provider refresh). Best-effort (a failed provider warns, the run proceeds). Never applies to unattended auto-runs (that path already follows a refresh). Absent means false. See [Refresh ordering, self-healing, and refresh-before-run](#refresh-ordering-self-healing-and-refresh-before-run). |
| `dummy_epg_profile_id` | no (absent = off) | Phase 2 (bead ti939.3.3): id of a [dummy EPG profile](template_engine.md) auto-assigned to the master group's channels on every run. Must reference an **existing** profile (teaching error otherwise); the key is never default-filled — omit it to disable. See [Automatic guide data for master channels](#automatic-guide-data-for-master-channels-dummy-epg). |
| `include_master_group_streams` | no (default **false**) | bead 6xxmp: when true, the **master group's own streams** (from *any* provider) are also matched to the master channels; streams already attached are skipped. A whole-group catch-all for a same-named cross-provider group — now usually superseded by adding the same group under the other provider as a `secondary` scope (which targets exactly one provider). Still useful when a same-named group spans **three or more** providers and you want them all. See [Two providers, one group name](#two-providers-one-group-name) below. |
| `assume_current_date` | no (default **false**) | When true, a listing that carries a **time but no date** is placed on the **current date** so it becomes matchable — deliberately relaxing the never-guess-the-date rail. Accepts the cross-day match risk. See [Dateless live listings](#dateless-live-listings). |
| `demote_stale_dateless` | no (default **true**) | bead jqwfq: guard for `assume_current_date`. When true (the default), a would-attach whose **dateless** stream name was **already present in the provider's previous-day M3U snapshot** is routed to the [review queue](#the-review-queue-ambiguous-matches-become-questions) (reason `stale_dateless_stream_name`) instead of auto-attached — a name left over from yesterday must not attach to today's master. Positive snapshot membership is the only demoting signal (missing/capped snapshots **fail open** and never demote); dated names are never touched; a prior review-queue **accept** of the pairing outranks the guard. Set **false** only for recurring daily events whose names legitimately repeat every day. Inert unless `assume_current_date` is on. See [Reviewing ambiguous matches](#reviewing-ambiguous-matches-phase-2-review-queue). |
| `parse_master_from_stream` | no (default **false**) | When true, each master channel's event identity (title + time) is read from its **first attached stream's name** instead of the channel name — so master channels can be named freely. A master with no attached stream is skipped. See [The master channels' date+time must be in their NAMES](#the-master-channels-datetime-must-be-in-their-names). |

### Why validation is strict

Validation errors are designed to teach — each carries the field, the
value you sent, what was expected, and a link back to this document.

* **Mandatory scoping** (a master present, at least one secondary scope —
  or `include_master_group_streams` — and no secondary scope duplicating the
  master's exact `(group_id, m3u_account_id)` pair) is schema-enforced, not
  convention. The uniqueness rail is the (group, provider) **pair**, so the
  same group under a different provider is allowed as a secondary while an
  identical group+provider master/secondary is rejected. It is the rail that
  prevents recurrence of the prior fuzzy-matching incident — see [History:
  the 1,341-incident benchmark](#history-the-1341-incident-benchmark) below.
* **Parse regexes compile through `safe_regex` at save time.** Operator
  regex is the ReDoS surface; the save-time compiler is the exact one the
  runtime uses.
* **`attach_threshold` accepts any value in `[0, 1]`.** 0.80
  (`EVENT_ATTACH_FLOOR` in `backend/services/event_sync_matcher.py`, the
  single source of truth) is the DEFAULT the validator fills, not a hard
  minimum — it is operator-authoritative and may be lowered per rule when a
  provider's data needs it. The matcher's admission policy honors the stored
  value directly at runtime. The hard-reject rails that *do* stay
  unconditional are the team-token and numeric-identity conflicts, not the
  threshold. See [Threshold and bands](#threshold-and-bands).
* **`time_window_minutes` is capped at 1440 (24 hours).** The time window
  is the rail that keeps same-teams-different-day fixtures apart — an
  oversized window re-opens that false-positive class, and the frozen
  regression corpus only proves the matcher's precision at sane windows.
* **Unknown keys are rejected**, so a typo'd optional key cannot silently
  fall back to its default.

### Two providers, one group name

Dispatcharr channel-group names are **globally unique** — its `ChannelGroup`
model declares `name = models.TextField(unique=True)`, and per-provider
settings (`auto_channel_sync`, `enabled`) live in a `ChannelGroupM3UAccount`
junction. So if two M3U providers both carry a group called `MLB PPV`, their
streams land in the **same** channel group — one group ID with two junction
rows (provider A: sync ON, provider B: sync OFF). **Dispatcharr cannot
represent two same-named groups**, so there is only one group ID to work with.

**The provider-scoped picker resolves this directly.** A group carried by two
providers expands in the master and secondary pickers into per-provider rows,
so you scope each role to the provider you mean:

1. Pick the shared group under **provider A** as the master
   (`{group_id: 12, m3u_account_id: 3}`) — provider A has `auto_channel_sync`
   ON, so Dispatcharr owns one channel per event.
2. Pick the **same group under provider B** as a secondary
   (`{group_id: 12, m3u_account_id: 7}`) — provider B has `auto_channel_sync`
   OFF. The secondary fetch filters streams to provider B alone, so only its
   still-unattached streams are matched onto the master channels.

The two roles share a group ID but differ by provider, so the uniqueness rail
(the `(group_id, m3u_account_id)` pair) is satisfied — this is the case that
had no expression before provider scoping.

**`include_master_group_streams` remains as a whole-group catch-all** for the
same shape, and is the right tool when a same-named group spans **three or
more** providers (add the master under one provider, set the flag, and every
other provider's unsynced streams in that group attach without enumerating
each). With the flag on you may **leave `secondary` empty** (bead 3ux85): the
master group is itself the stream source, streams already attached (provider
A's own) are skipped by the resolver, and preview/run stay in lockstep. The
master scope is never *also* added as a secondary — the empty list plus the
flag is the scoped path, so the anti-unscoped-matching rail is untouched.

If you would rather keep the two providers fully separate, the alternative
is to **rename one provider's group** in Dispatcharr (a group override) so
the two names become distinct IDs that pick independently.

## Threshold and bands

Every candidate pair — one secondary stream against one master channel
within the time window — lands in exactly one confidence band:

| Band | Score range | What happens |
|-|-|-|
| **attach** | `score ≥` effective threshold (see below) | Best candidate becomes the stream's `would_attach_master` — a manual run attaches it; the preview reports it. |
| **ambiguous** | `0.60 ≤ score <` effective threshold | Surfaced for operator review in the preview. **Never auto-attached**, at any score. |
| **reject** | `score < 0.60`, or a hard-reject rail fired | Never attached. In-window rejected pairs still appear in the preview's candidates table with a machine-readable reject reason (`team_token_conflict`, `numeric_identity_conflict`, `no_parsed_time`, `parse_failure`, `below_ambiguous_floor`); out-of-window pairs (`outside_time_window`) are excluded from candidacy entirely. |

**Contested rail (ti939.2.1)**: even when the best candidate is in the
attach band, the stream is classified **ambiguous** (machine-readable
reason `contested_top_candidates`) when more than one candidate lands in
the attach band, or the runner-up scores within 0.05 of the winner — the
team-agree boost can tie same-fixture-different-session masters ("Fury
vs. Usyk" main card vs. its Prelims) at identical scores, and attaching to
an alphabetical tie-break winner would be wrong half the time. Skip +
count, never attach. The preview surfaces the reason per stream
(`ambiguous_reason`).

**Venue-conflict rail (bead yjchp)**: a candidate that clears the attach
band and has no team-token conflict can still be lexically
indistinguishable from a *different* real-world event when the title
never splits into teams (racing/PPV shapes) — `token_set_ratio` scores
the shared run of words and is blind to two conflicting leftovers.
`Lucas Oil Late Models Adams County` vs. `Lucas Oil Late Models at Shelby
County` scores 0.9032 and used to auto-attach across two different
venues. Now: when a pair is admitted **without positive team-token
agreement** and, after the same clean/hyphen-bridge/initialism pipeline
the score itself uses, **both** titles still carry at least one identity
token with no counterpart on the other side (a place name — not a stop
word, a team qualifier, or a lone number/year), the pair is demoted to
**ambiguous** with reason `venue_token_conflict` (surfaced per stream as
`ambiguous_reason`, the same channel as the contested rail above). It is
a **demotion, never a hard reject** — a one-sided leftover (a longer
master title carrying extra sponsor/series dressing, e.g. `HLR Joker's
Jackpot at Eldora Speedway` against `Eldora Speedway`) must still be free
to attach, so only a *mutual* conflict trips the rail, and a true match
that does trip it stays rescuable from the [review
queue](#reviewing-ambiguous-matches-phase-2-review-queue) instead of
being lost. Two things bypass the rail entirely: a **team-token AGREE**
verdict (aligned teams at the same kickoff already establish identity),
and an **operator-lowered threshold below the 0.80 default** — dropping
below the default floor is deliberate manual-control territory (see
[Lowering the threshold](#lowering-the-threshold-operator-authoritative)
below), and this rail silently overriding that choice would defeat the
point of lowering it.

The "effective threshold" is not always the value you set: without
positive team-token agreement (the team-token check found no team pair on
one side, or the pairs were inconclusive), the bar rises to 0.90 — lexical
overlap alone has to clear a higher bar than lexical overlap corroborated
by matching team names. **This 0.90 no-teams raise applies only while your
threshold stays at or above the 0.80 default**; once you deliberately lower
the threshold below 0.80 (see below) your exact number is honored for
teamless pairs too, otherwise the raise would silently veto the lowering
you asked for. A team-token *conflict* is a hard reject regardless of
score.

### How the score is computed

The score in the bands above is a **fuzzy similarity of the two parsed
titles**, on a 0–1 scale, optionally lifted by team-token agreement:

1. **Parse both names** into (title, start time) using the rule's patterns.
   Only the extracted `title` is scored — never the raw provider string, and
   never the slot prefix or the date/time. So `Peacock 14: Mercury vs. Aces
   @ 11 Jul 06:00 PM ET` scores on `Mercury vs. Aces`.
2. **Clean each title** (lowercase, strip punctuation/quality tags/locale
   noise) with the same shared cleaner the dedup matcher uses.
3. **Normalize spelling variance across the two titles** (both bridges only
   ever *add* agreement — a pair can never score lower than its plain fuzzy):
   * **Hyphen/dash split, corroborated.** `Shangri-La` (one token) is split
     to `Shangri La` **only when the other title carries that exact
     word-run** — so `Off-Road`↔`Off Road` and `Shangri-La`↔`Shangri La`
     match fully, while a compound like `Pre-Race Show` stays intact against
     an unrelated `Race Day Live` (splitting it there would spuriously match
     `Race`).
   * **Acronym/initialism bridge.** An acronym token on one side and the
     consecutive words it spells on the other collapse to one token —
     `RoC`↔`Race of Champions`, `MUFC`↔`Manchester United` (FC-style suffix
     aware). This is the same initialism logic the team-token layer uses,
     extended to titles that never split into teams.
4. **Fuzzy score** = RapidFuzz `token_set_ratio` of the two normalized
   titles, divided by 100. `token_set_ratio` is **order- and
   duplicate-insensitive**: it compares the *sets* of words, so extra words
   on one side (a year, a venue, a slot label the parse didn't strip) lower
   the score but shared words still count regardless of position. Identical
   cleaned titles short circuit to 1.0. A token set that is a **subset** of
   the other scores 1.0 — which, after the bridges above, is why the noisy
   FloRacing master `FLORACING 003 | 2026 AMSOIL CHAMPIONSHIP OFF-ROAD IN ELK
   RIVER, MN` scores **1.0** against `AMSOIL Championship Off Road`: every
   secondary token is present in the master.
5. **Team-token check** (only when both titles split into two sides on
   `vs`/`v.`/`@`): the two sides are compared order-insensitively. If they
   **agree**, the final score is `max(fuzzy, team_score)` — agreeing teams at
   the same kickoff can lift a lexically-distant abbreviation (`MUFC` /
   `Manchester United`) that pure title fuzz under-scores. If they **conflict**
   (`Rangers vs. Islanders` / `Rangers vs. Yankees`), it's a hard reject at
   0.0. Racing/PPV events usually have **no** team pair (`teams=absent`), so
   their score is pure title fuzz and they face the 0.90 no-teams bar.

So to raise a score you either clean up the parsed titles (a `group_patterns`
override on the master that strips the slot/year/venue noise), add a
**team alias** for a known equivalent spelling (below), or, if the titles
are as clean as they'll get, lower the threshold for that rule.

### Team aliases (operator dictionary)

**Settings → Channel Pipeline → Event Sync Team Aliases** holds an
instance-wide dictionary of **known-equivalent team spellings** — e.g.
`Man Utd == Manchester United == MUFC`, or a nickname a provider uses that
shares no letters with the canonical name (`Red Devils == Manchester
United`). The team-token check consults it on **both** of its paths:

* **Hard-reject rescue** — two aliased spellings at the same kickoff no
  longer read as different teams, so the pair stops hard-rejecting with
  `team_token_conflict`.
* **Boost** — the aliased sides count as full team agreement, so
  `max(fuzzy, team_score)` lifts the pair into the attach band even when
  the surrounding title text shares almost nothing.

This is the **safe direction** for abbreviation-heavy providers: an alias
adds one declared equivalence instead of lowering the evidence bar for
*every* pair the way an `attach_threshold` cut does. The rails are
untouched — the qualifier rail still outranks aliases (`Barcelona W` never
aliases onto the men's side), a pair whose teams sit in *different* alias
groups scores exactly as if the dictionary were empty (an alias can never
*create* a conflict), and the time window / numeric-identity rails still
apply.

Mechanics and policy:

* Each group is a list of 2+ spellings plus an optional note. Terms are
  compared with the matcher's own team normalization (case, punctuation,
  apostrophes, and generic `FC`-style suffixes ignored), and one term may
  belong to only one group.
* **Aliases are corpus-gated: add a group only with evidence.** A wrong
  alias is a new false-positive vector — it can silently auto-attach the
  wrong event every day. Add a group when preview/journal evidence shows a
  recurring missed match traceable to a team-name variant, note the
  evidence in the group's note field, and re-run **Preview** to confirm.
  The shipped dictionary is empty by design.
* The dictionary applies to every Event Sync rule (preview, manual runs
  and auto-runs all read the same setting), and changes are journaled.
* API: `GET`/`PUT /api/event-sync/team-aliases`; MCP:
  `get_event_sync_team_aliases` / `update_event_sync_team_aliases`.

### Lowering the threshold (operator-authoritative)

**0.80 is the default, not a hard floor.** A rule may set `attach_threshold`
to any value in `[0, 1]` — in the editor's **Advanced** section, or via the
API. Lower it when a provider's titles carry unavoidable noise that caps the
fuzzy score below 0.80 (the FloRacing case: teamless events whose master
titles keep `FLORACING NNN |` and a venue string). A lower floor trades
precision for recall — it auto-attaches weaker matches — so **preview first**
and watch the runner-up scores. The rails that stay unconditional at *any*
threshold are the team-token conflict and the different-event-number
(numeric-identity) hard rejects: lowering the floor never resurrects a
contradiction, it only admits lower-confidence *non*-contradictory pairs.

### Disabling the time window

By default a candidate pair must have parsed start times within
±`time_window_minutes`. Set **`enforce_time_window: false`** (editor Advanced
→ "Ignore time window") to drop that gate: every parsed master event becomes
a candidate and matches rank on title/team score alone. Use it when two
providers publish **different start times for the same fixture** (the
FloRacing case — one lists a race at 09:45, the other at 09:00; a 6-hour
disagreement on a third). **Only safe for a single-provider, same-day master
group**: with the gate off and a recurring or serial title (a daily show,
weekly numbered cards), a stream can match the *wrong day's* master channel.
The time delta is still reported in the preview; it just no longer rejects.

**Historically** the 0.80 floor was hard-clamped and could only be raised —
that mirrored the M1 callsign hard-reject rail's precision-over-recall stance.
It is now a per-rule trade-off the operator owns, because of what a wrong
attach actually costs:

Wrong attachments are reversible and non-compounding, but not self-healing — the matcher is deterministic, so a bad match repeats every run until you adjust a pattern or threshold, or the provider renames the stream.

In other words: a bad match doesn't get worse over time (it isn't
compounding — it doesn't cascade into more bad matches), and detaching a
wrongly-attached stream is a normal, low-risk operation. But it also
won't fix itself. If a stream mis-attaches, expect it to mis-attach the
same way every subsequent preview/run until you either raise the
threshold, tighten/fix the pattern that's producing the wrong parsed
title, or the provider changes the name (which is out of your control).
Budget for periodically re-checking the preview after a provider renames
its slots.

## Troubleshooting

### Still seeing duplicate channels?

**A secondary group still has `auto_channel_sync` ON.** This is, by a
wide margin, the most common cause. Event Sync only *attaches streams* to
master channels — it never stops Dispatcharr from creating its own
channels from a secondary group whose auto-sync is still on. If a
secondary is still ON, Dispatcharr keeps creating a parallel set of
channels from that group regardless of what ECM matches, and you'll see
both the master's channel *and* the secondary's own auto-created
duplicate.

Fix: M3U Manager → account → Groups → disable `auto_channel_sync` for
every provider used as a `secondary` scope — or use the rule
editor's **Fix** button (a [confirmed guided
fix](#guided-setup-the-confirmed-auto-sync-fix); the toggle happens only
when you confirm its dialog, never automatically). The rule editor's live
warnings and the [pre-flight check](#pre-flight-checks) on every preview
report the current state.

### Nothing matches

1. Check the **pre-flight** result at the top of the preview. If the
   master group's `auto_channel_sync` is OFF, Dispatcharr has created no
   master channels — there is nothing to match against, and every stream
   will show as `unmatched` even though the matcher itself is working
   correctly.
2. Check the **parse failures** panel. If most or all of your secondary
   streams show up there, your parse pattern isn't matching that
   provider's name shape at all — see the [pattern
   cookbook](#pattern-cookbook) and verify with [Test
   Patterns](#6-use-test-patterns) before assuming the matcher is broken:

   ![Parse failures panel listing "NFL Game Pass" and "CA | Fubo Sports Network" groups with per-name bullet lists and a (no_parsed_time) reason tag next to each group heading](images/event_sync/6-parse-failure-panel.png)

### Events missing entirely (master-as-ceiling)

If a real event never shows up as a channel at all — not even
`unmatched` — check whether it's carried **only** by a secondary
provider and not by the master. This is the accepted "master-as-ceiling"
limitation of the current model: **events carried only by secondary
providers get no channel**, because ECM never creates channels — only
Dispatcharr does, from the master group. Every preview reports these
streams explicitly in the **unmatched secondary streams** list so you
have visibility into how much coverage you're losing:

![Unmatched secondary streams table: stream name, provider, parsed title, parsed start, and a "Best candidate: None in time window" column for each row](images/event_sync/5-unmatched-parse-failures.png)

If this list is large and consistently the same events, it's evidence for
picking a different (broader) master group, or a future promotion feature
(tracked under epic `enhancedchannelmanager-ti939.4`, not built yet) — not
something to work around today.

### The master channels' date+time must be in their NAMES

Event Sync reads a master channel's start time by **parsing the channel's
name** — it does **not** read the time from the stream attached to the
channel, nor from EPG. A master channel whose name has no complete
parseable date+time can never be an attach target (it shows up as an
*unparsed master* in the preview, and every secondary stream for that event
lands in the unmatched list).

Auto-synced master channels normally inherit the master provider's **stream
name**, which already carries the time (`… @ Jul 11 9:30 AM`), so this just
works. The pitfall is a **normalization or naming rule that strips the
date-time out of the master channel name** — that makes the masters
unparseable and nothing attaches.

If you *want* to name the master channels freely (a clean name without the
time), set **`parse_master_from_stream: true`** on the rule (in the editor:
**"Read master event time from the attached stream"** under Advanced). With
it on, each master channel's identity is read from its **first attached
stream's name** instead of the channel name — so the event title+time come
from the underlying auto-synced stream, and the channel name is yours to
choose. A master channel with no attached stream is skipped. Otherwise, keep
the date+time in the master channel names (verify with the preview: the
master count should be non-zero and the "unparsed masters" count zero).

### Using a Dispatcharr Channel Group Override

A Dispatcharr **Channel Group Override** on an auto-synced M3U group sends
the auto-created **channels** into a *different* target group, while the
group's **streams** stay under the original name. Event Sync follows the
override automatically: **point the master at your auto-synced provider
group** (the one you normally pick, where `auto_channel_sync` is ON) and ECM
fetches the master channels from the override's target group for you — both
in the preview and the run. (Pointing the master at the override *target*
group directly also works: the pre-flight reads its auto-sync state through
the source group.) If the preview shows zero master channels for a group you
know is auto-synced, confirm the override target group has actually been
populated by a Dispatcharr auto-sync at least once.

### Undo a bad event_sync run

Every live event_sync run captures a **pre-mutation snapshot** that
includes the master group's channels, so a bad run (a wrong attachment, a
threshold set too low, a pattern matching the wrong fixtures) is fully
reversible by execution id:

1. **Find the execution id.** It's in the run response, the executions
   list (`GET /api/channel-pipeline/executions`), and on every journal
   entry the run wrote (`batch_id` = execution id).
2. **Inspect what the run did.** The journal (category `event_sync`,
   action type `merge_stream`, `batch_id` = the execution id) has one
   entry per attachment carrying the secondary stream **name**+id, the
   provider, the master channel **name**+id, and the score / band /
   time-delta / team-token verdict that justified the match — enough to
   judge each attachment without replaying the run.
3. **Roll it back.**
   `POST /api/channel-pipeline/executions/{id}/rollback` with
   `confirm=true` (or the UI's rollback on that execution). The API
   refuses without `confirm` and tells you what could be overwritten.
   Once confirmed, the rollback prefers a **surgical un-merge**: when the
   run's journal entries fully cover its attaches (the normal case for an
   event_sync run), it removes **only the stream ids the run added** from
   each master's *current* stream list — master-stream churn Dispatcharr
   made after the run survives untouched, and the response carries
   `surgical_unmerge: true`. When coverage can't be proven (legacy runs,
   a failed journal write, mixed runs), it falls back to the snapshot
   restore — an optimistic overwrite of each snapshot channel's stream
   list. Either way the rollback removes the streams the run attached and
   **never deletes the master channels themselves** — ECM didn't create
   them and won't remove them. On the snapshot path, master channels are
   restored with a **streams-only** payload: their name / group / EPG
   linkage stay whatever Dispatcharr currently says (a slot rename that
   happened after the run is not reverted).
4. **Fix the rule before the next run.** The matcher is deterministic: a
   bad match is reversible and non-compounding but **not self-healing** —
   the same rule against the same names will make the same bad match on
   the next manual run, every time, until you adjust the rule's pattern
   or threshold (or the provider renames the stream). Rollback undoes the
   damage; only a rule change prevents the recurrence.

What the journal shows after the rollback: the run's execution record
moves to `rolled_back`, and the original per-attach entries remain as the
historical audit trail (journal entries are never deleted).

## Pre-flight checks

Before a preview (and later, a run), ECM verifies against Dispatcharr —
the pre-flight itself is read-only, and the event_sync feature never
toggles group settings (a static AST gate in
`backend/tests/unit/test_event_sync_rollback_roundtrip.py` proves it; the
only group-settings write ECM offers is the separate [confirmed guided
fix](#guided-setup-the-confirmed-auto-sync-fix) below):

* master group has `auto_channel_sync` **ON** (otherwise no master
  channels exist and the whole feature silently matches nothing);
* every secondary group has `auto_channel_sync` **OFF** (otherwise
  Dispatcharr is creating duplicate channels from a stream-source group);
* every configured group still exists in some account's group settings.

Failures surface in the preview/run results with the expected/actual
setting and which group failed — they never silently block the preview;
you always see the match results alongside the misconfiguration.

**Known edge**: Dispatcharr channel groups are global **by name**
(bd-dgs64). If a secondary provider publishes a group with the SAME name
as another account's auto-synced group, they share a group ID, and the
pre-flight secondary check will fail for it (correctly — Dispatcharr is
auto-syncing that group ID). Real event groups are provider-distinct-named
in practice.

**Cross-rule advice (bead yjchp)**: a `secondary_auto_sync_off` failure
normally tells you to disable `auto_channel_sync` for that group. But if
the failing group is itself the **master** of a *different*, enabled
event_sync rule, disabling its auto-sync would break that other rule —
masters require `auto_channel_sync` **ON**. The pre-flight detects this
case and swaps the advice: the failure message names the conflicting
rule by name and tells you to restructure instead — remove the group
from this rule's secondaries, or point the other rule at a different
master group. The failure carries a `conflicting_rule` field (the other
rule's name) whenever this applies; the machine-readable `check` id
(`secondary_auto_sync_off`) is unchanged, so anything keying off it
still works. This cross-rule check runs against **every** enabled
event_sync rule, not just the ones opted into `auto_run` — a conflicting
rule that only runs manually still counts.

## Guided setup: the confirmed auto-sync fix

When the rule editor's live status detects a misconfigured group — the
master with `auto_channel_sync` OFF, or a secondary with it ON — it offers
a one-click **Fix** button (Phase 2, bead ti939.3.4). Hard constraints,
locked at planning:

* **Explicit and separately confirmed.** The Fix button only opens a
  confirmation dialog stating exactly what will change and why — e.g.
  *"Turn OFF auto-sync for 'FIFA | World Cup' (Provider 2)? Dispatcharr
  will stop creating duplicate channels from this group; existing
  auto-created channels from it may be removed by Dispatcharr."* The
  toggle happens only when you confirm. It is **never** a side effect of
  saving a rule or running the pipeline.
* **Dedicated, admin-gated endpoint.** The confirm button calls
  `POST /api/m3u/accounts/{account_id}/group-auto-sync-toggle` (admin
  when auth is enabled; `confirm: true` required at the API level too).
  Both directions are supported: enable the master, disable a secondary.
* **Journaled per toggle.** Every toggle writes a journal entry with the
  before/after values. **Snapshot restore does NOT revert Dispatcharr
  group settings** — an execution rollback undoes ECM's attaches, not
  Dispatcharr's group configuration. If you need to undo a toggle, the
  journal entry is the recovery breadcrumb: it records which group on
  which account changed, and in which direction — re-run the fix the
  other way (or flip it in M3U Manager → account → Groups).
* **Outside the event_sync feature modules.** The endpoint lives on the
  M3U router (a guided-setup surface), so the AST no-group-writes gate
  keeps proving the attach/preview path itself never writes group
  settings.

After a confirmed fix the editor refetches the live settings, so the
warning clears immediately.

## What ECM deliberately does NOT do

* **No channel lifecycle.** Dispatcharr creates, updates and deletes the
  master channels (verified: its sync task updates in place, preserves
  channel UUIDs, never resets a channel's stream list, and deletes a
  channel only when the master provider drops the stream — the cascade
  detaches secondary streams cleanly).
* **No orphan reconciliation.** event_sync rules never populate
  `managed_channel_ids` and hard-bypass the pipeline's Pass 4 orphan
  cleanup — reconciling channels ECM doesn't own would delete or move
  Dispatcharr-owned channels. See [Pass 4 orphan
  bypass](#pass-4-orphan-bypass) below.
* **No persisted channel IDs.** Matching is recomputed statelessly every
  run; master channels are the identity anchor. See [No durable cluster
  state](#no-durable-cluster-state) below.
* **No silent auto-run.** Manual-run-only unless a rule carries the
  explicit `auto_run: true` opt-in (Phase 2, bead ti939.3.1 — see
  [Automatic runs after refresh](#automatic-runs-after-refresh-phase-2-opt-in)).
  Enforced in layers: the unattended watermark task selects only opted-in
  event_sync rules, the engine's per-rule trigger gate refuses everything
  else (deny-by-default — "scheduled" and unidentified triggers stay
  denied even for opted-in rules), and the attach phase re-checks the gate
  plus the circuit breaker and a pre-flight before any unattended write.
* **No group-settings writes from the feature itself.** The attach,
  preview and dummy-EPG paths never touch `auto_channel_sync` (statically
  proven by the AST gate). The ONLY group-settings write ECM offers is
  the [confirmed guided fix](#guided-setup-the-confirmed-auto-sync-fix)
  — a separate, admin-gated, journaled endpoint the operator drives
  through its own confirmation dialog.

## Previewing matches (Phase 1A)

`POST /api/channel-pipeline/event-sync-preview` runs the full matcher
against live Dispatcharr data with **zero writes** — per-stream match rows
(score, band, team-token verdict, time delta, reject reason), unmatched
streams, parse failures grouped by group, and summary counts that
reconcile exactly with the detail rows. It accepts either a saved rule id
or an inline `event_sync_config` (so the rule editor can preview before
saving). Full request/response contract: [`docs/api.md`](api.md). Headless
mirror: the `preview_event_sync` MCP tool. The preview and the attach path
share one resolver (`backend/services/event_sync_resolver.py`), so what
the preview shows is what a run does — dry-run parity by construction.

## Running the attach path (Phase 1B)

A **manual** pipeline run (`POST /api/channel-pipeline/run`, a single-rule
run, or the UI's pipeline Run) executes event_sync rules through a
dedicated attach phase:

* Every secondary stream resolves through
  `backend/services/event_sync_resolver.py` — **the same function the
  preview calls**, so preview decisions and run decisions are identical on
  identical inputs.
* **Band semantics:** attach band → the stream is attached to the master
  channel via the existing merge machinery; ambiguous (band or
  [contested rail](#threshold-and-bands)) → skipped and counted;
  reject / unmatched / parse-failed → skipped with reason.
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
* **Attach cap:** on `max_attach_per_run` overage the run stops attaching,
  warns in the execution log, and records the overage count on the
  execution record's warnings.
* **Rollback:** the run's pre-mutation snapshot includes the master
  group's channels, so the standard execution rollback / snapshot restore
  undoes attaches. Master channels are restored **streams-only** (never
  their Dispatcharr-owned name/group/EPG metadata) and are never deleted.
  Step-by-step: [Undo a bad event_sync run](#undo-a-bad-event_sync-run).

## Automatic runs after refresh (Phase 2 opt-in)

By default, event_sync rules run only when you run them. Once you trust a
rule's manual runs (clean previews, correct attaches), you can opt that
**one rule** into unattended runs on the existing post-refresh watermark
task (bead ti939.3.1).

### How to enable it

Set `auto_run: true` on the rule — in the rule editor it is the
**"Run automatically after each M3U refresh (auto-run)"** checkbox under
**Advanced**, or set the key in `event_sync_config` via the API. The flag
is per rule and **defaults to false**; rules saved before the flag existed
behave exactly as before (absent means false). The pipeline's scheduled
task must also be enabled (Settings → Scheduled Tasks — the same master
switch that governs standard run-on-refresh rules).

An unattended run is deliberately indistinguishable from a manual run on
the audit surfaces: the same journal provenance (category `event_sync`,
`batch_id` = execution id, names alongside IDs plus score, band, time
delta, team verdict), the same `event_sync: X attached, …` summary line on
the execution record, the same per-run attach cap, and the same rollback
path.

### Notifications you should expect

Because nobody is watching an unattended run, misconfigurations notify
instead of hiding in the run record (existing notification channel — the
bell in the UI plus any configured alert methods):

* **Attach cap reached** — the run stopped at `max_attach_per_run`; the
  overage count is in the message. Runs are idempotent, so the remainder
  attaches on the next refresh (or run manually / raise the cap).
* **Pre-flight failed (rule skipped)** — unattended runs pre-flight the
  Dispatcharr group settings and **fail closed**: if the master group's
  `auto_channel_sync` is OFF, a configured group is missing, or the check
  itself errors, the rule is skipped that run and you get a warning
  notification. Fix the group settings — in M3U Manager or via the rule
  editor's [confirmed Fix
  button](#guided-setup-the-confirmed-auto-sync-fix); ECM never toggles
  them unattended — and the rule runs again on the next refresh. Manual
  runs are unchanged — they do not pre-flight; the preview is your
  pre-flight surface.

The completion notification also carries the unattended attach count
("N event streams attached").

### Circuit breaker interaction

The channel pipeline's run-on-refresh circuit breaker (tripped by the
startup crash-sentinel after an abandoned run, e.g. an OOM kill) now gates
the event_sync auto-run chain too (bead ixujz): while tripped, opted-in
event_sync rules do **not** run unattended. Manual runs stay available —
manual is the recovery surface. Clear the breaker deliberately via
`POST /api/channel-pipeline/reset-circuit-breaker` (or the UI banner);
auto-runs resume on the next refresh. The `ECM_DISABLE_RUN_ON_REFRESH`
break-glass environment variable suppresses the unattended chain the same
way.

### Timing note — refresh ordering

Dispatcharr materializes master channels from a Celery task **after** its
M3U refresh completes, so an ECM watermark run can occasionally land
before a brand-new event's master channel exists. This is accepted, not
engineered around: the stream counts as `unmatched` that run (zero writes,
never a guess) and attaches on the **next** run — convergence, not
immediacy. Covered by the lifecycle race tests
(`backend/tests/unit/test_event_sync_lifecycle.py`).

## Refresh ordering, self-healing, and refresh-before-run

Event Sync's matching and Dispatcharr's M3U refresh are **decoupled**. ECM's
watermark task advances `last_m3u_refresh_completed_at` after a refresh
completes, and the pipeline recomputes matches statelessly on each tick. Two
consequences fall out of that design, and one new per-rule option lets you
tighten it when you need to.

### Why misordered refreshes self-heal

Refreshes for different providers are independent, so they can land in any
order — a **secondary** provider can refresh before its **master**. That
"misordering" is not an error state and needs no coordination:

* Matching is a **stateless, idempotent recompute** — Event Sync stores no
  durable cluster state; the master channels are the identity anchor and every
  run re-derives the full match set from live data. A run never depends on the
  result of a prior run.
* When a secondary stream's master channel does not exist yet (the master
  provider hasn't refreshed, or Dispatcharr's post-refresh Celery sync hasn't
  materialized the channel), that stream is simply counted `unmatched` for that
  run — **zero writes, never a guess**. It attaches on the **next** pipeline
  tick after the master exists.
* Because the recompute is idempotent, repeated runs **converge**: once every
  provider has refreshed and the master channels exist, the same rule against
  the same names produces the complete match set. A stream already on its
  master is a no-op. So a misordered refresh only ever *delays* an attach to a
  later tick — it never produces a wrong or duplicated attach.

This is convergence, not immediacy: the system heals itself on the next run
rather than trying to order the refreshes. It is the same property that makes
[opt-in auto-runs](#automatic-runs-after-refresh-phase-2-opt-in) safe to run
unattended after every refresh.

### The "refresh providers before run" option (per rule)

The self-healing above resolves *within a few ticks*. If you would rather a
**single manual run** work against freshly-refreshed provider data — closing
the staleness window in one shot instead of waiting for the next tick — turn on
**"Refresh this rule's M3U providers before running"** in the rule editor's
**Behavior → Automation** area (config key `refresh_providers_before_run`,
default off).

With it on, a manual run of the rule first refreshes exactly the M3U accounts
backing that rule's **master + secondary** groups (resolved from the rule's
scopes: a provider-scoped scope refreshes that one account; a whole-group scope
refreshes every provider account carrying the group), waits for the refresh,
then runs the match/attach. Key properties:

* **Applies to the live Run AND the dry-run Test.** Both are manual triggers,
  so both pre-refresh.
* **Test is no longer zero-write.** A refresh is a real write to Dispatcharr's
  stream list, so with this flag on the **Test** action triggers that write
  before previewing. The editor toggle, the per-rule **Test** button, and its
  confirm dialog all say so — Test with this flag on is not a dry preview of
  current data. If you want a genuinely read-only preview, leave the flag off
  and use the editor's **Preview** (which never refreshes).
* **Auto-runs are excluded.** The unattended watermark auto-run path is *already*
  triggered by a completed refresh, so pre-refreshing there would be circular.
  This flag only ever affects manual Run/Test; the auto-run path is untouched.
* **Best-effort.** A single failed provider refresh **warns** (a run warning on
  the execution record + a log line) but the run **still proceeds** against
  current data — the same partial-success posture as the M3U refresh watermark
  (one failed account never aborts the batch).
* **Still converges, not instant.** Dispatcharr materializes brand-new master
  channels from a Celery task *after* the refresh, so a run landing immediately
  after the pre-refresh can still precede a new event's master channel — that
  stream attaches on a later run, exactly as in the [timing
  note](#timing-note--refresh-ordering) above. The flag freshens the provider
  *streams* the run sees; it does not force Dispatcharr's channel sync to finish
  first.

## Automatic guide data for master channels (dummy EPG)

Master event channels are created by Dispatcharr with no guide data —
sports/PPV events rarely carry real EPG. Phase 2 (bead ti939.3.3) lets a
rule reference a [dummy EPG profile](template_engine.md) that gets
assigned to the master group's channels on **every** run, manual and
auto-run, so new events show programme information automatically.

### Setup

1. **Create a dummy EPG profile** (EPG Manager → Dummy EPG) whose
   title/time/date patterns match the **master provider's** channel
   naming. For the corpus example master name
   `Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET`:

   ```
   title_pattern:  ^[^:]+:\s*(?<title>.+?)\s*@
   time_pattern:   (?<hour>\d{1,2}):(?<minute>\d{2})\s*(?<ampm>[AP])M
   date_pattern:   @\s*(?<day>\d{1,2})\s+(?<month>[A-Za-z]{3,9})
   ```

   **Tip — share the rule's parse patterns.** The Event Sync rule already
   parses the master group's names with its own `patterns` (title + start
   time). The master provider's naming is the same in both places, so the
   profile's patterns can reuse the rule's regexes (dummy EPG uses
   JS-style `(?<name>...)` groups; the engine accepts Python-style
   `(?P<name>...)` too, so a rule pattern usually pastes in verbatim).
   Author them once, paste twice — don't invent a second grammar for the
   same names.

2. **Ensure a Dispatcharr EPG source serves the profile's XMLTV** — an
   XMLTV source whose URL is ECM's
   `/api/dummy-epg/xmltv/<profile_id>` endpoint (the per-profile URL is
   preferred; the combined `/api/dummy-epg/xmltv` also works). Without
   one, the run warns (`event_sync_dummy_epg_no_source`) and assigns
   nothing.

3. **Reference the profile on the rule** — rule editor → Advanced →
   *Dummy EPG profile*, or set `dummy_epg_profile_id` in the config JSON.
   Validation requires the profile to exist; omitting the key turns the
   feature off.

### What a run does

* Master-group channels with **no** guide data get the profile's EPG via
  the standard `assign_epg` machinery, against the Dispatcharr source
  from step 2.
* Channels the source does not cover **yet** — a brand-new event, or the
  very first run — defer into the pipeline's **existing Pass 5**
  refresh-and-retry: Pass 5 auto-adds the master group to the profile's
  channel groups, regenerates the XMLTV, refreshes the Dispatcharr
  source, and retries the assignment in the same run. No parallel
  mechanism, and nothing to schedule.
* **Idempotent**: already-assigned channels are no-ops on re-runs.
* **Never clobbers**: a channel that already carries guide data from any
  OTHER source (e.g. a hand-assigned real EPG) is left alone and counted
  as `kept foreign EPG` in the run summary.
* **Never fights Dispatcharr refresh semantics**: the assignment is
  `epg_data_id` metadata on existing master channels — exactly what
  standard assign_epg rules write — and Dispatcharr's sync task updates
  channels in place without resetting EPG assignments. ECM still never
  creates or deletes channels; execution rollback restores masters
  streams-only and does not revert guide data.
* The run summary line reports the step:
  `event_sync dummy EPG ('<profile>'): X assigned, Y deferred to Pass 5,
  Z already assigned`.

**Degradation is graceful and attach-safe**: a deleted profile is a
teaching validation error (the rule is loudly skipped until you fix the
reference), but a *disabled* profile or a missing Dispatcharr source only
warns and skips the EPG step — attaches are never blocked by a guide-data
convenience.

## Preferred-provider stream ordering (stream sort)

By default an attach **appends**: the master channel's own
Dispatcharr-synced stream stays first and every attached secondary
stream lands at the bottom, in attach order. If you want a specific
provider's stream to *play* — the first stream on the channel is the one
clients get — set a **stream sort** on the rule (bead io0tv).

Event Sync rules use the same per-rule stream sort as standard pipeline
rules (`stream_sort_field` / `stream_sort_order` on the rule — columns,
not `event_sync_config` keys). In the rule editor: **Behavior → Stream
order**.

### Recipe — preferred provider's stream on top

1. **Set provider priorities** — M3U Manager → *Save Priorities*. Give
   your preferred provider the **highest** number. These are the same
   ECM-side priorities Smart Sort's `m3u_priority` criterion uses; they
   live in ECM settings (`m3u_account_priorities`), not in Dispatcharr.
2. **On the Event Sync rule** — Behavior → Stream order → *Provider
   Order (M3U)*, direction **Descending** (highest priority first; this
   is the default direction the editor picks).
3. **Run the rule** (or let auto-run fire). After the attach phase, the
   engine's stream-reorder pass rewrites each touched master channel's
   stream list in priority order — the preferred provider's stream
   first.

### Behavior notes

* **Ordering heals on every run** — including runs where every stream
  was already attached (idempotent no-ops). Changing provider
  priorities takes effect on the next run; you never need to detach or
  re-attach anything.
* Only master channels the rule touched this run (attached **or**
  already-attached) are reordered — the rule never reorders channels
  outside its master group.
* **No sort field = no change**: existing rules keep pure append-only
  behavior. The reorder pass skips rules without a `stream_sort_field`.
* All the standard sort modes work (`provider_order`, `quality`,
  `stream_name`, `stream_name_natural`, `smart_sort`), but
  `provider_order` is the one this recipe needs; `quality` requires
  probe stats to be useful.
* Interaction with **dummy EPG**: guide text comes from parsing the
  master channel's *name* (or its first stream's name with
  *parse master from stream*), not from stream order — stream sort
  changes which provider's feed **plays**, not what the guide says. The
  two combine cleanly: dummy EPG for programme info, provider order for
  the feed.
* Dry-run (Test) reports `Would reorder N streams in '<channel>' by
  provider order (M3U account priority)` without writing.

## Reviewing ambiguous matches (Phase 2 review queue)

Ambiguous-band matches — a candidate that scored below the attach
threshold but above the reject floor, **or** a contested tie between two
masters — are never auto-attached. Before Phase 2 they were silently
skipped and re-skipped forever; now every event_sync run (manual **and**
auto-run) **queues them for your decision** instead.

The queue lives on the **Channel Pipeline tab → Event Sync Review**
section (it appears once at least one event_sync rule exists). Each card
is **one exact pairing** — one secondary stream against one master
channel — with the full evidence the matcher saw, never just a score:

* both raw provider names side by side,
* both parsed titles and parsed start times,
* the score, confidence band, **team-token verdict**, and start-time
  delta,
* a "Contested between masters" marker when the question exists because
  two masters tied (one card per contender).

### What Accept and Reject mean

* **Accept & attach** — the stream is attached to that master now (via
  the same idempotent, journaled attach internals a run uses), and the
  decision is **recorded permanently**: every future run auto-attaches
  this exact pairing without asking. Accepting one contender of a
  contested tie automatically closes the other contenders' cards (the
  question was answered). If the immediate attach can't be safely
  verified — e.g. the provider refreshed and the snapshot stream id went
  stale — the accept still succeeds and the **next run performs the
  attach**; the banner tells you which happened.
* **Reject pairing** — the pairing is **suppressed permanently**: it will
  never attach (not even if a later run's score drifts into the attach
  band) and never re-enters the queue. Nothing is written to Dispatcharr.

### Decisions survive refreshes — by design

Decisions are keyed on **content identity** — the provider account, the
normalized stream name, and the master's parsed event identity (title +
start time) — **never on channel or stream IDs**. Stream IDs churn on
every provider refresh and channel IDs live only as long as the event's
channel, so an ID-keyed queue would refill with duplicates of questions
you already answered. With fingerprint keying:

* a refresh that re-delivers the same provider string re-applies your
  decision automatically (accepted → attach; rejected → skip);
* the queue never re-asks an answered question — a re-encountered pending
  pairing only refreshes its card's evidence;
* when the event ends and its channel disappears, the decision simply
  never matches again (decision rows are content-scoped, not
  channel-scoped).

The **preview** shows queue state inline when previewing a *saved* rule:
candidates carry `Pending review` / `Accepted (auto-attaches)` /
`Rejected (suppressed)` markers, and a would-attach row driven by your
prior accept is flagged "Via review-queue accept" (with the summary
counting them separately from threshold attaches). Preview and run share
one resolver, so what the preview predicts is exactly what the run does.

Unattended runs (auto-run rules) include the queued count in their
completion notification — "N event matches queued for review" — so
borderline events are one click away instead of silently skipped at 3 AM.

**Audit**: every accept/reject writes a journal entry (category
`event_sync`, action `review_accept` / `review_reject`), and every
queue-driven attach is journaled with `attach_source: "review_queue"` —
distinguishable from threshold attaches (`attach_source: "threshold"`) in
the journal's match provenance.

### Decisions on dateless slots survive midnight too

For rules using **Assume current date** (dateless live listings), the
master's parsed start time carries a date the parser *synthesized* from
"today" — it is not part of the event's real identity. Decision
fingerprints for these parses therefore key on the **clock time only**
(`<title>|dateless|<HH:MM±offset>`, offset being the rule timezone's
standard offset), never the synthesized date. A recurring dateless slot
("PPV 01: FURY vs HALL 6PM" listed every day) mints the *same*
fingerprint every day, so:

* an accept keeps auto-attaching that slot on every following day
  (including overriding the stale-dateless demote rail — your answer
  outranks the heuristic);
* a reject keeps suppressing it — the queue does not re-ask the same
  slot question every morning;
* the same title at a *different* clock time is a different slot and is
  asked separately, and the key does not shift at DST transitions.

Dated events are unaffected — their fingerprints still embed the full
parsed start.

**One-time re-ask after upgrading**: review decisions made on dateless
slots *before* this keying existed were stored with the synthesized date
baked in, and whether a stored key's date was synthesized is not
recoverable, so those old rows cannot be migrated. Each affected dateless
slot will appear in the review queue **once** more after the upgrade;
answer it once and the decision carries forward daily from then on. Dated
events do not re-ask.

## Testing & pre-release verification

### What the automated E2E covers (and what it honestly cannot)

`backend/tests/e2e/test_event_sync.py` runs the happy path against the
live container: configure a rule with the shipped default patterns → the
Test Patterns endpoint parses every shipped pattern's own example → the
preview returns match cards with exactly-reconciling summary counts → a
manual single-rule run (`POST /api/channel-pipeline/rules/{id}/run`,
`triggered_by="api"`) completes with the `event_sync:` summary line in the
execution log → journal provenance is queryable by category + batch_id.
The test picks a master group with **zero channels**, so the execute-mode
run is attach-nothing **by construction** and safe against live data.

What it cannot cover live: the actual multi-provider attachment. The dev
instance has no event group with `auto_channel_sync` ON, and ECM never
toggles that Dispatcharr setting outside the operator-confirmed guided
fix (a test must not drive that dialog against live provider data) — so
no live master channels exist to attach to. The attach behavior itself (streams
from multiple providers landing on master channels, per-attach journal
provenance, idempotent re-runs, refresh survival, rollback) is covered
against a mocked Dispatcharr in
`backend/tests/unit/test_event_sync_attach_execution.py` and
`backend/tests/unit/test_event_sync_lifecycle.py`.

### Pre-release manual script — multi-provider attach segment

Recorded reason for this being a manual script rather than automated E2E
(bead ti939.2.4): demonstrating real attachment requires a Dispatcharr
event group with `auto_channel_sync` ON, which only exists on an
operator's real deployment — the test environment cannot create one
without writing group settings against live provider data (the only
sanctioned write path is the operator-confirmed guided fix, which a test
must not drive).

Run this on a deployment that has a real auto-sync master event group,
before cutting a release that touches event_sync:

1. **Configure**: Channel Pipeline → Create Rule → Event Sync rule. Pick
   the auto-sync master group and ≥ 2 secondary groups from **different
   providers**. Keep the shipped default patterns.
2. **Test Patterns**: fetch live samples from a secondary group and run
   the test — event-shaped names must show parsed title + date + time
   (`Parsed` status).
3. **Preview**: `would_attach` must be > 0 and the pre-flight banner must
   be clean (master auto-sync ON, secondaries OFF). Spot-check a few match
   cards for correct pairings — this is the precision gate; a wrong
   pairing here means STOP (adjust patterns/threshold, re-preview).
4. **Manual run**: Save, then run the rule (pipeline Run button or
   `POST /api/channel-pipeline/rules/{id}/run`). The execution record must
   show the `event_sync: N attached, …` summary line with N > 0 and
   `channels_created` = 0.
5. **Multi-provider check**: open a matched master channel in Channel
   Manager — its stream list must show streams from ≥ 2 providers (the
   master's own plus attached secondaries).
6. **Journal provenance**: Journal → filter category `event_sync` (or
   `GET /api/journal?category=event_sync&batch_id=<execution_id>`) — one
   entry per attach carrying stream/channel names+ids, provider, score,
   band, time delta, team verdict.
7. **Undo check** (optional but recommended once per release): roll the
   execution back and confirm the attached streams are gone from the
   master channels and the channels themselves are untouched
   ([Undo a bad event_sync run](#undo-a-bad-event_sync-run)).

## Developer reference

### Matcher layering

`backend/services/event_sync_matcher.py` scores a candidate pair through
four ordered layers, each existing to reject a specific failure mode the
earlier layers can't catch on their own:

1. **Parse** (`parse_event_name`) — turn a raw provider string into
   `(title, start_datetime, teams)`, reusing the dummy-EPG
   `extract_groups` / `compute_event_times` machinery so operator-authored
   pattern overrides go through the same `safe_regex` path as any other
   untrusted regex in this codebase. A name with no COMPLETE parsed
   date+time is unmatchable by contract — the start time is **never**
   guessed from "now" the way dummy-EPG's filler-programming fallback
   does. This exists because the whole model depends on the parsed start
   time being trustworthy; a guessed time would silently corrupt the next
   layer.
2. **Time-window blocking** — candidate *generation*, not a safety rail:
   only pairs whose parsed start times are within ± `time_window_minutes`
   (default 30, capped at 1440) become candidates at all. This exists
   both for correctness (a Tuesday 7pm game and a Thursday 7pm game
   between the same two teams are different fixtures — same-teams,
   different-day is exactly the false-positive shape a title-only fuzzy
   match would miss) and for performance (it bounds the N×M pair count
   before the more expensive fuzzy scoring runs).
3. **Fuzzy score of PARSED titles** (never raw names) — RapidFuzz
   `token_set_ratio` on LOCALS-cleaned strings via the shared cleaner in
   `services/dedup_matcher.py`. Scoring the *parsed* title rather than the
   raw stream name is what makes "Peacock 14: Mercury vs. Aces @ ..." and
   "FS2 05: Phoenix Mercury vs. Las Vegas Aces @ ..." score high — slot
   prefixes and date/time suffixes are already stripped before this layer
   ever runs.
4. **Team-token check** — split the title on `vs` / `vs.` / `v.` / `@`,
   compare the two sides order-insensitively (including qualifier classes
   like `W`/`Women`/`U21`/`Reserves`, and abbreviation/initialism forms
   like `MUFC` ↔ `Manchester United`). A CONFLICT (both sides parse to
   team pairs and clearly differ) is a HARD REJECT — score forced to 0.0
   — mirroring the M1 callsign hard-reject rail elsewhere in the
   pipeline. Token AGREEMENT raises confidence enough to admit even a
   lexically-distant abbreviation on its own. This layer exists because
   fuzzy title scoring alone is fooled by sibling-program pairs (e.g. two
   different studio shows sharing most of their surrounding words) that
   score high on lexical overlap without denoting the same event.
   The layer also consults the **operator team-alias dictionary** (bead
   ti939.4.2; "Team aliases" above): after the qualifier rail, two sides
   whose identity-token keys resolve to the same alias group score 1.0.
   The lookup is strictly monotonic — different-group or no-group pairs
   fall through to the unchanged base scoring — so aliases can rescue a
   conflict or lift an agreement but can never manufacture a disagree.
   The dictionary is loaded from settings at the `match_streams` /
   `score_pair` boundary (`team_aliases=None`); tests and the frozen
   corpus inject explicit fixtures (or `()`), keeping the corpus gate
   byte-stable.

Layer 5, the **event admission policy** (`is_event_attachable`), is
covered next.

### Event admission policy — structurally separate from the callsign policy

The event admission policy (`is_event_attachable`, gated by its own
`EVENT_ATTACH_FLOOR` constant) is a **deliberately separate** branch from
`services.dedup_matcher`'s callsign-based admission policy used elsewhere
in the pipeline (`merge_streams` / fuzzy dedup). They must never share one
knob, even though they're philosophically parallel (both have a
"no-corroborating-signal" floor that's stricter than the base floor).

#### History: the 1,341-incident benchmark

The reason this separation is schema-mandatory rather than a convention
engineers are trusted to follow: a **prior incident produced 1,341
false-POSITIVE merges** from an unscoped fuzzy-matching rule — streams
that should never have been considered candidates for each other got
merged because the rule had no scoping boundary to stop it. That incident
is the trust benchmark this whole feature is built against:
`master_group_id` / `secondary_group_ids` scoping is schema-enforced (an
unscoped event rule is refused at save time, not caught in review), and
team-token conflict is a hard reject (score 0.0, never admissible at any
fuzzy score) rather than a soft penalty. Precision over recall everywhere
in this module. Provider scoping (the nested `master` / `secondary` shape)
tightens the same rail one notch: the uniqueness boundary is the
`(group_id, m3u_account_id)` pair, so a scope names not just a group but the
one provider it draws from.

### No durable cluster state

Event Sync persists **no database state keyed on Dispatcharr IDs**. The
durable state is: the nullable `event_sync_config` JSON column on
`auto_creation_rules` (the rule's own configuration), the journal
provenance rows the attach path writes per attach, and — since Phase 2
(bead ti939.3.2) — the fingerprint-keyed `event_sync_reviews` table (see
"Review queue keying" below; the Phase 1 "no new tables" decision was
scoped to *match state*, and review rows deliberately contain no
ID-keyed match state). Every preview and every run **recomputes matching
from scratch** against live Dispatcharr data — master channels are
identified by **name**, never by ID, and the matcher/resolver modules
never see, cache, or return a channel ID.

This is a direct consequence of verified Dispatcharr behavior (read from
Dispatcharr's `apps/m3u/tasks.py` `sync_auto_channels`):

* Channel UUIDs are preserved across refreshes — Dispatcharr does in-place
  updates, not recreate-on-refresh.
* The sync task builds its channel map from the master account's streams
  only, and has no code path that resets a channel's existing stream
  list — a foreign (ECM-attached) stream survives a Dispatcharr refresh.
* A channel is deleted only when the master provider drops the stream
  (the event ended); the cascade detaches secondary streams cleanly.

Because attachments persist across refreshes on Dispatcharr's side, ECM
doesn't need to remember what it attached — it just needs to re-resolve
names to current channel IDs on every run. **Never key state on channel
IDs or stream IDs** — they're Dispatcharr's to reassign, not ECM's to
assume stable.

### Pass 4 orphan bypass

The Channel Pipeline's Pass 4 orphan reconciliation walks
`managed_channel_ids` on a rule and deletes/reassigns channels the rule no
longer claims. event_sync rules **never populate `managed_channel_ids`**
and are **hard-bypassed** from Pass 4 — not merely "produce an empty
list," but structurally excluded from that pass running against them at
all. Running orphan reconciliation against an event_sync rule would treat
Dispatcharr-owned master channels as ECM-managed and could delete or move
channels ECM has no authority over. This bypass is a direct consequence
of the "ECM never creates or deletes channels in this feature" contract —
Pass 4 exists to clean up after channel-creating rules, and event_sync
rules don't create channels.

### Future-state constraint

Any future state that must survive a Dispatcharr refresh — Phase 3
exclusion lists, anything an operator would expect to persist — **must
key on content fingerprints / event identity** (parsed title + start
time, or similar), **never on channel/stream IDs**. This constraint
exists because of the same stateless-recompute reasoning above: IDs are
Dispatcharr's, names/content are the stable identity anchor this feature
can actually reason about across runs. The Phase 2 review queue (next
section) is the first consumer of this constraint and the reference
implementation for the next one.

### Review queue keying (ti939.3.2) — fingerprint reference

The `event_sync_reviews` table keys every pending question and every
accepted/rejected outcome on the content fingerprint

```
(rule_id, provider_id, stream_name_hash, event_key)
```

defined once in `backend/services/event_sync_review.py`:

* **`provider_id`** — the secondary stream's M3U account id
  (refresh-stable ECM/Dispatcharr configuration; `0` is the documented
  unknown-provider sentinel, NOT NULL because SQLite unique indexes
  treat NULLs as distinct).
* **`stream_name_hash`** — SHA-256 of the **LOCALS-cleaned** raw stream
  name (`services.dedup_matcher.clean_name`, the ONE shared cleaner the
  scoring stack uses). Cosmetic churn (case, punctuation, quality tags)
  can't mint a new question; anything the *matcher* would see differently
  legitimately re-opens it — the fingerprint can never be more forgiving
  than the scorer.
* **`event_key`** — the **master side's** parsed event identity:
  `<LOCALS-cleaned parsed title>|<parsed start as UTC ISO-8601>`. It
  survives master-channel recreation and provider dressing, keeps two
  sessions of one fixture (main card vs. prelims) distinct, and is
  timezone-representation independent.

The four-column unique index is **full** (unlike `pending_merges`'
partial index): answered rows persist as the decision record, so "the
queue must not refill with answered questions" is DB-enforced. Snapshot
channel/stream ids appear ONLY inside the display-only `evidence` JSON;
the accept endpoint re-verifies both against live Dispatcharr (channel
name must still parse to the row's `event_key`, stream name must still
hash to `stream_name_hash`) before using them, and degrades to
"decision recorded, next run attaches" when verification fails.

Decisions are an **input to the one resolver**
(`resolve_event_sync(..., decisions=...)`), never a second scorer:
rejected pairings are filtered from the candidate set before
classification (suppressing threshold attaches AND re-enqueueing);
accepted pairings upgrade an ambiguous outcome to `would_attach`
(`attach_source="review_queue"`) only while the master is still an
attach/ambiguous-band candidate — an accept never overrides the
matcher's hard rejects (team conflict, time window, below the ambiguous
floor). Preview and run therefore apply decisions identically by
construction.

### Frozen regression corpus — add-only policy

`backend/tests/fixtures/event_sync/matcher_corpus.jsonl` is a **frozen,
append-only** set of labeled real/engineered event-name pairs
(`same_event` / `not_same` / `ambiguous`) that gates the matcher's
precision/recall in CI (`backend/tests/test_event_sync_matcher_corpus.py`).
Every `not_same` pair must land in the `reject` band — a `not_same` pair
that reaches `attach` is an incident-class false positive and fails the
build.

**Add-only, never edit**: add one pair for every matcher bug ever found
(with the bug's bead ID in the pair's `reason` field); never delete or
relabel an existing pair just to make the gate pass. If a matcher change
flips an existing pair's band, that's the gate doing its job — the
change needs to be justified in review or the matcher needs to be fixed,
not the corpus edited to match the new (possibly wrong) behavior. This is
the same trust-but-verify posture as the 1,341-incident history above:
the corpus is the evidence base that a future change hasn't quietly
reopened a previously-fixed false-positive class.

### Debug bundle: `event_sync_matching.json` (bead 03nji, extended by yjchp)

The Channel Pipeline [debug bundle](user_guide/channel-pipeline/debugging-rules.md#2-upload-a-debug-bundle-from-bundle-mode)
(`POST /api/channel-pipeline/debug-bundle`, then poll `GET
/api/channel-pipeline/debug-bundle/{job_id}`) carries an
`event_sync_matching.json` entry: one object per **enabled** event_sync
rule, built by resolving the rule through the exact same zero-write
resolver the preview endpoint uses (`_build_event_sync_matching_section`
in `backend/routers/channel_pipeline.py`), so the bundle can never fork
from what the preview would show. Each rule entry carries the resolved
group ids, every secondary stream's match evidence, summary counts, and:

* **`matching_controls`** — the rule's effective knobs as a flat object:
  `attach_threshold`, `enforce_time_window`, `time_window_minutes`,
  `assume_current_date`, `demote_stale_dateless`,
  `parse_master_from_stream`, `include_master_group_streams`, and (bead
  yjchp) **`auto_run`** — the [Phase 2 opt-in](#automatic-runs-after-refresh-phase-2-opt-in)
  that gates whether the rule fires unattended after an M3U refresh at
  all. Before this field existed, "why didn't this rule run on refresh"
  was undiagnosable from a bundle alone — a rule with everything else
  configured correctly but `auto_run: false` looks identical to a
  correctly-firing rule in every other field. Reading `auto_run` off the
  bundle is now the first check.
* **`preflight`** (bead yjchp) — the same pre-flight result a live
  preview would produce for that rule (`{"ok": bool, "failures": [...]}`,
  see [Pre-flight checks](#pre-flight-checks) above), including the
  [cross-rule `conflicting_rule` advice](#pre-flight-checks) when
  applicable. Captured **before** the rest of the rule's resolution runs,
  so it survives a later failure in that rule's own entry. If the
  pre-flight check itself throws, the field degrades to
  `{"error": "<exception>"}` and the rest of the bundle still builds —
  a broken pre-flight fetch never sinks the whole bundle.

Both fields are the reason a support helper (another operator, or an AI
assistant working from an uploaded bundle) can diagnose "the rule looks
right but never runs unattended" or "the pre-flight is failing and here's
exactly why" without needing live access to the installation.

### Explicitly NOT written (home-lab tier)

Consistent with this project's effective deployment tier: no ADR file for
this feature (the rationale lives in this document plus code comments),
no versioned API reference beyond the `event-sync-preview` entry in
[`docs/api.md`](api.md), no dedicated performance guide.

## Related

- [`docs/api.md`](api.md) — full `POST /api/channel-pipeline/event-sync-preview` request/response contract.
- [`docs/architecture.md`](architecture.md) — Channel Pipeline internals and how event_sync rules fit alongside standard rules.
- `backend/services/event_sync_matcher.py` — the matcher (parse → block → score → admit).
- `backend/services/event_sync_resolver.py` — the shared preview/attach resolution layer.
- `backend/services/event_sync_preflight.py` — the read-only Dispatcharr group-settings check.
- `backend/channel_pipeline_schema.py` `validate_event_sync_config` — the config validator (single source of truth for defaults/clamps, imported from the matcher).
- `backend/tests/fixtures/event_sync/matcher_corpus.jsonl` — the frozen regression corpus.
- `frontend/src/components/channelPipeline/eventSyncShippedPatterns.json` — the shipped pattern definitions consumed by both the frontend picker and a backend test that pins each pattern's example against the real parser.
- Epic `enhancedchannelmanager-ti939` — Event Sync overall (Phase 1A preview, Phase 1B attach — shipped, Phase 2 automation, Phase 3 evidence-driven promotion).
