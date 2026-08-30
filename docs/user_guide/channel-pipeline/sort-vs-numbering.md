# Channel Sort vs. Channel Numbering

## The short version

Two different settings look like they should do the same thing, but don't:

| Setting | Where | What it actually controls |
|-|-|-|
| **Channel Sort** (`sort_field` / `sort_order`) | Rule → Output & Run phase | The order streams are *processed*, and (only when the rule's Create Channel action has a fixed starting number) the order channels are renumbered into that range. |
| **Sort Group** action (`sort_group`) | Rule → Actions | Alphabetically sorts and renumbers **all** channels in a group, once per run, regardless of how the channels were created. |

If your Create Channel action's **Channel Number** is left on **Auto** (the
default), Channel Sort's renumbering half is silently skipped. See
[The Auto gotcha](#the-auto-gotcha) below. **Sort Group is the action built
for "sort my channels by name after the run finishes."** Use it directly
instead of relying on Channel Sort + a fixed number range.

## What Channel Sort actually does

`sort_field` (e.g. "Stream Name") primarily controls **stream processing
order** within a rule: the sequence channels get created/updated in as the
engine walks matching streams. That has a real, visible side effect: when a
rule's Create Channel action has a fixed starting number (a range like
`400-99999`, not Auto), the engine renumbers the channels it created *for that
rule* in `sort_field` order, starting at that number. That's Pass 3 of the
Channel Pipeline engine.

What `sort_field` does **not** do: renumber channels for a rule using **Auto**
numbering, or touch channels created by a different rule, or reorder channels
that already have their final numbers from a previous run.

## The Auto gotcha

`channel_number` on a Create Channel action defaults to `"auto"` (sequential
numbering: Dispatcharr assigns the next available number). With `"auto"`,
the engine has no fixed range to renumber into, so **the rule-level renumber
pass is skipped entirely for that rule**, silently, with no warning in the
UI or execution log. Your `sort_field` setting still affects processing
order, but the channel numbers you see afterward are whatever Dispatcharr
assigned at creation time, not an alphabetical sequence.

This is the single most common cause of "I set Channel Sort to Stream Name
and my channels still aren't in order."

The rule builder shows an inline hint on the Channel Sort field when it
detects this combination (a Create Channel action with Channel Number on
Auto, plus a Channel Sort selected). Look for it in **Output & Run** phase
of the rule editor.

## Sort Group: the action built for this

The **Sort Group** action (category: Management) alphabetically sorts and
renumbers **every channel currently in a target group**, once per run, after
all streams have been processed. This is regardless of which rule or action
created each channel, and independent of each Create Channel action's
Channel Number setting. This is the same sort used by the manual **Sort & Renumber** tool in
the Channels pane, ported to run automatically as part of a pipeline
execution.

Fields:

| Field | Meaning |
|-|-|
| `order` | `asc` (A→Z, default) or `desc` (Z→A). |
| `starting_number` | First channel number to assign. Leave blank to keep the group's current lowest channel number (or start at 1 if none is set yet). |
| `strip_numbers` | Ignore leading channel numbers embedded in stream/channel names when sorting (default on). |
| `ignore_country` | Ignore a country prefix like `US \|` or `UK:` when sorting (default off). |

Sort Group runs **after** the rule-level `sort_field` renumber pass, so if a
rule somehow has both configured for overlapping channels, Sort Group's
result wins. It's the more specific, per-action request.

## Worked example: two groups, two starting ranges

This matches the original request that prompted this page: a Polish channel
group numbered from 1, and a US channel group numbered from 400, each kept
alphabetically sorted as new channels are created.

**Rule 1: Polish channels**, actions in order:

```json
[
  { "type": "create_channel", "name_template": "{stream_name}", "group_id": 1, "if_exists": "merge" },
  { "type": "assign_epg", "epg_id": 1 },
  { "type": "sort_group", "order": "asc", "starting_number": 1, "strip_numbers": true }
]
```

**Rule 2: US channels**, actions in order:

```json
[
  { "type": "create_channel", "name_template": "{stream_name}", "group_id": 2, "if_exists": "merge" },
  { "type": "assign_epg", "epg_id": 1 },
  { "type": "sort_group", "order": "asc", "starting_number": 400, "strip_numbers": true }
]
```

Leave each rule's Create Channel **Channel Number on Auto**. Sort Group
handles the final numbering, so there's no need to fight over a fixed range.
Leave the rule's own **Channel Sort** field blank (`"No sorting"`); it isn't
needed here and only controls processing order, not the alphabetical
renumber you actually want.

Each run: streams are matched, channels are created/merged into group 1 or
group 2, and then (once per group, after every stream in the run has been
processed) Sort Group alphabetically sorts and renumbers the whole group.
Because it renumbers the **whole group** each time (not just channels
created this run), new channels always land in the right alphabetical slot
relative to existing ones.
