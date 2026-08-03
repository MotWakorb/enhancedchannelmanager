# Fuzzy Matching for Local / OTA Channels

## What it is

ECM's Channel Pipeline engine defaults to **exact normalized-name equality** when
merging streams into existing channels. That default is intentional. It ended
a 1,341-false-positive merge incident caused by over-broad word-prefix matching.

Local and OTA streams are the one category where exact matching reliably fails.
Different providers tag the same channel differently:

| Provider A | Provider B | Provider C |
|-|-|-|
| `WI \| WBAY CBS Green Bay HD` | `US: WBAY-DT (CBS)` | `WBAY CBS 2.1` |

All three point at the same station, but no two strings match exactly after
normalization.

**Fuzzy locals matching** adds a scored matching mode for these cases. It uses
FCC callsigns as a hard correctness gate (WBAY ≠ WGBA, no matter how similar
the names look), then applies a confidence-scored fuzzy match on cleaned names
as a tiebreaker. You configure a minimum confidence score and scope it to the
channel groups that hold your Local channels.

## When to use it

Use fuzzy locals matching when:

- You have a "Local" or "OTA" channel group and streams are landing in M3U
  under names like `WI | WBAY`, `US: WBAY-DT`, or `WBAY CBS Green Bay RAW`.
- Streams are arriving but not merging into existing Local channels because
  exact-name matching keeps missing them.
- You have already checked the exact-match rule and confirmed it is not a
  normalization gap that a normalization rule would fix first.

Do **not** use it as a catch-all for non-local content. The callsign gate only
protects pairs where both sides have a parseable callsign. For content without
callsigns (sports networks, premium channels, international feeds) the gate
cannot fire and the safety margin is narrower.

## How it works

When a `merge_streams` rule includes both `loose_name_match: true` and a
`min_score` threshold, the engine uses a three-rung scoring ladder instead of
the legacy fuzzy cascade:

1. **Callsign hard-reject (M1).** If both the stream name and the channel name
   contain a parseable FCC callsign and they differ, the pair is rejected
   unconditionally: no score, no merge. `WGBA` against `WBAY` is always
   rejected here regardless of how similar the surrounding words look.

2. **Callsign equality override.** If the callsigns on both sides match (or the
   `tvg_id` callsigns match), the score is set to 1.0 and the pair is admitted
   immediately: no fuzzy scoring needed.

3. **LOCALS fuzzy scoring.** Names are cleaned (state-code and market prefixes
   stripped, superscripts converted, quality tags removed, `GREENBAY` split to
   `Green Bay`) and scored with a token-based similarity algorithm. The result
   is a float between 0.0 and 1.0; the pair is admitted when it reaches your
   configured `min_score`.

The minimum score is floored at 0.60. Values below 0.60 are rejected at save
time.

## Setting up a fuzzy locals rule

### Step 1: identify your Local channel groups

Go to **Channel Groups** and note the ID(s) of the groups that hold your local
channels (e.g., "Locals", "OTA"). You will need these group IDs for the
`target_channel_in_group` allowlist.

### Step 2: preview before writing

Use the preview tool to see what would match at your intended threshold before
saving any rule, via the MCP `preview_fuzzy_matches` tool:

```
preview_fuzzy_matches(group_ids=[14, 22], min_score=0.75)
```

The preview returns scored `(stream, channel)` pairs in descending score order.
Review the `callsign_verdict` column:

- `match`: both sides have a callsign and they agree. These are the
  high-confidence matches.
- `absent`: at least one side has no parseable callsign. Admitted by default
  only when `allow_no_callsign` is set (see below).

Inspect any surprising pairs in the results. The preview never writes anything.

### Step 3: build the rule action

Add a `merge_streams` action to your rule with these fields:

```json
{
  "type": "merge_streams",
  "target": "auto",
  "loose_name_match": true,
  "min_score": 0.75,
  "target_channel_in_group": [14, 22]
}
```

- `target_channel_in_group` is **required** for scored-fuzzy rules. The schema
  rejects a scored-fuzzy rule without it. This is the scoping constraint that
  keeps fuzzy matching from touching unintended channel groups.
- `min_score` must be in [0.60, 1.0]. 0.75 is a reasonable starting point for
  callsign-verified matches; lower values increase recall at the cost of
  precision.

### Step 4: run dry-run first

Run the pipeline in dry-run mode before enabling the rule, via the MCP
`match_streams_to_channels` tool:

```
match_streams_to_channels(group_ids=[14, 22], min_score=0.75, apply=false)
```

The dry-run shows which streams would be matched and at what score, without
writing anything.

### Step 5: enable and monitor

Enable the rule and watch the [Journal](../journal/index.md) page.
Each scored-fuzzy merge writes a journal entry with the score, `callsign_verdict`,
signal, and the callsigns that were parsed. This lets you audit exactly why each
merge fired.

If a run produces unexpected matches, use **Undo this run** or **Rollback** on
the Channel Pipeline's Execution History panel to undo it (see
[Recovery Patterns](../troubleshooting/recovery-patterns.md#undo-a-pipeline-run)).

## No-callsign opt-in

The default policy requires a parseable FCC callsign on both sides of a match.
For channels or streams where no callsign is present in the name or `tvg_id`,
the pair is skipped by default.

To admit no-callsign pairs, add `"allow_no_callsign": true` to the action. When
set, pairs where at least one side has no callsign are admitted only when they
score ≥ 0.90. The 0.90 bar is higher than the normal `min_score` floor to
partially compensate for the absence of the callsign correctness gate.

The M1 hard-reject still fires if both sides have parseable but different
callsigns. `allow_no_callsign` does not affect that case.

## Safety guarantees

- **Dry-run by default.** The `match_streams_to_channels` MCP tool supports
  `dry_run: true`. The `preview_fuzzy_matches` MCP tool never writes under any
  circumstances.
- **Admin-gated.** Fuzzy-preview access requires admin authentication when
  auth is enabled.
- **Rollback-able.** Channel Pipeline executions are recorded and reversible
  from the Execution History panel (see
  [Recovery Patterns](../troubleshooting/recovery-patterns.md#undo-a-pipeline-run)).
- **Scoping required.** The schema refuses to save a scored-fuzzy rule without
  a non-empty `target_channel_in_group` allowlist.
- **Callsign hard-reject.** Pairs with conflicting callsigns on both sides are
  never admitted, regardless of `min_score` or `allow_no_callsign`.

## MCP tools

The MCP server exposes two tools for the fuzzy locals workflow:

**`preview_fuzzy_matches(group_ids, min_score)`**

Read-only. Returns ranked `(stream, channel, score)` triples for the given
groups. Uses the same scoring core and admission policy as the rule executor.

**`match_streams_to_channels(group_ids, min_score, apply=false, backfill=false)`**

Defaults to dry-run (`apply=false`). Shows which channels would receive a stream.
Set `apply=true` to actually assign the best-scoring stream per channel. The
`backfill` flag extends matching to channels that already have streams (default
is channels with zero streams only).

Both tools call `GET /api/channel-pipeline/fuzzy-preview` under the hood and
inherit M1/M2 admission from the shared policy. They cannot see or assign
non-admissible pairs.

## Deep reference

| Topic | Where |
|-|-|
| Preview API reference | [`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md): the fuzzy-preview endpoint reference |
| Scored-fuzzy rule path (dev reference) | [`docs/channel_pipeline_rule_analyzer.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/channel_pipeline_rule_analyzer.md): "Scored-fuzzy rule path" section |
| Confidence floor | 0.60 — the same floor used by interactive stream dedup |
