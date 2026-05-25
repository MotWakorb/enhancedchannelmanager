# Auto-Creation Rule Analyzer

The rule analyzer surfaces structural and regex-style configuration
bugs in auto-creation rules **without running them**. Findings are
advisory — they are warnings or info, never errors, and saves are
never blocked. The analyzer is a support tool, not a gate.

Bead: `enhancedchannelmanager-0gntx` (Phase 1).

## How to use it

Two endpoints, both backend-side:

```
POST /api/auto-creation/rules/analyze
POST /api/auto-creation/rules/analyze/from-bundle   (multipart, file=<tar.gz>)
```

The first analyzes the rules currently in the DB. The second analyzes
`rules.yaml` (and, if present, `channel_groups_diagnostic.json`)
inside an uploaded debug bundle. The `from-bundle` endpoint never
touches the DB, so it is safe to point at any user's bundle.

The MCP server exposes both via one tool:

```python
analyze_auto_creation_rules()                       # live mode
analyze_auto_creation_rules(bundle_path="/path/to/debug-bundle.tar.gz")
```

The tool returns a markdown report with one section per rule.

## Response shape

```json
{
  "rules": [
    {
      "rule_id": 2,
      "rule_name": "Sports Networks - excl Fr and Es",
      "findings": [
        {
          "code": "REGEX_TRIVIALLY_MATCHES_ALL",
          "severity": "warning",
          "field": "conditions[1].value",
          "message": "...",
          "suggestion": "",
          "detail": {"reason": "empty-alternation"}
        }
      ]
    }
  ],
  "summary": {"error": 0, "warning": 6, "info": 0}
}
```

`severity` is always one of `error`, `warning`, or `info`. Most
findings are `warning`-level; `MERGE_SCOPE_NOT_TARGET_GROUP` is `info`
(advisory, not a misconfiguration).

## Finding codes

### `REGEX_TRIVIALLY_MATCHES_ALL`

**Trigger.** A regex with an empty alternation: `UK|`, `|UK`,
`(UK|)`, `(|UK)`. The empty branch always matches the empty string,
so the whole pattern matches every input at position 0.

**Real-world example.** A user typed their M3U group prefix `UK|`
into the "Matches (Regex)" field expecting a literal pipe, but the
"Matches" operator interprets the value as regex and `UK|` reads as
"UK or empty string" — every group matches.

**Remediation.**
- Switch the operator from "Matches (Regex)" to **Begins With** or
  **Contains**. The UI escapes the pipe automatically.
- Or escape the pipe yourself: `^UK\|` (anchored) is the correct
  regex for "starts with the literal characters `UK|`".

### `REGEX_REDUNDANT_ESCAPE_CARET`

**Trigger.** Pattern starts with `^\^` — anchor immediately followed
by an escaped (literal) caret.

**Real-world example.** A user typed `^4K` into "Matches (Regex)",
then the UI's escape pass added a backslash, producing `^\^4k`.
Almost always a typo; the user meant either `^4K` (anchored) or
`\^4K` (literal caret) — not both.

**Remediation.** Drop one of the carets. If you want "starts with
4K", use `^4K`. If you want a literal caret somewhere, drop the `^`
anchor at position 0.

### `OPERATOR_VALUE_LOOKS_LIKE_REGEX`

**Trigger.** A `*_contains` operator's value contains substrings
that suggest the user meant regex syntax: leading `^`, trailing `$`,
`.*`, `.+`, or any of `\b`, `\B`, `\d`, `\D`, `\w`, `\W`, `\s`,
`\S`. **Bare `|` is not flagged** — M3U groups commonly contain a
literal pipe (`UK| MOVIES`), and substring search for `UK|` is a
legitimate use of the Contains operator.

**Real-world example.** A user typed `^4K` into "Stream Group
Contains" thinking the `^` would anchor. Contains is substring
match, so the search is for the literal characters `^4K` — which no
group name contains, so the rule matches nothing.

**Remediation.** Switch the operator to **Begins With** (and drop
the `^`), **Ends With** (and drop the `$`), or **Matches (Regex)**
if you genuinely want the regex shape.

### `ANDOR_DROPS_GUARD`

**Trigger.** A "guard" condition (one of `normalized_name_in_group`,
`normalized_name_not_in_group`, `normalized_name_exists`,
`provider_is`) appears in some OR-groups but not others.

**Why it matters.** The condition list `A AND B OR C` reads as
`(A AND B) OR C` because AND binds tighter than OR (per
`auto_creation_evaluator.evaluate_conditions`). If `A` is the guard
and `C` doesn't include it, then any stream matched by `C` fires the
rule regardless of whether it would have passed the guard.

**Real-world example.** The Sports Networks rule had:

```
normalized_name_in_group=1464  AND
stream_group_matches=UK|
OR  stream_group_matches=US|
OR  stream_group_contains=^4K
```

→ groups 2 and 3 don't share the `name_in_group=1464` constraint.
Streams from US| or 4K groups would qualify regardless of whether
they're in the Sports group.

**Remediation.** Either repeat the guard in every OR-group, or split
the rule into one rule per OR-arm so each rule has its own guard.
The `detail` field on this finding tells you exactly which OR-groups
have the guard and which don't.

### `MERGE_STREAMS_NO_TARGET_CHANNELS`

**Trigger.** A rule with a `merge_streams` action and an explicit
`target_group_id` that points at a channel group with **0 channels**
(per `channel_groups_diagnostic.json`).

**Why it matters.** `merge_streams` only attaches streams to
channels that **already exist**. If the target group is empty, every
matched stream is skipped with "no existing channel found." The user
typically expected new channels to appear.

**Remediation.**
- If you want new channels created, switch the action to
  `create_channel`.
- Or seed the target group with channels first, then re-run.

This finding is only available in the `from-bundle` flow when the
bundle includes `channel_groups_diagnostic.json`. The live-mode
endpoint does not currently fetch channel-group counts.

### `MERGE_SCOPE_NOT_TARGET_GROUP`

**Severity.** `info` (advisory, not a misconfiguration error).

**Trigger.** A rule with a `create_channel` action whose `if_exists`
is `merge` or `merge_only`, on a rule whose `match_scope_target_group`
is **off** (falsy). (`if_exists` is a flat key on the action JSON —
`Action.to_dict()` spreads the action's params onto the top level.)

**Why it matters.** With `match_scope_target_group` off, the
existing-channel name lookup for `create_channel` searches **every**
channel group, not just the rule's target group. If a channel with
the same normalized name already exists in *any* other group, the
stream merges into that channel — `channels_updated` increments and
**no channel is created in this rule's target group** (the rule's
"created" count stays 0). With the flag on, the lookup is restricted
to the rule's target group, so the rule creates a new channel there
even when a same-name channel exists elsewhere.

New rules default `match_scope_target_group` to **on** (bd-p6ko9,
GH #226); this finding flags pre-existing rules that still have it
**off** so operators can decide whether the all-groups lookup is
intentional.

**Remediation.** Enable **"scope merge lookups to this rule's target
group"** (the *Merge lookup scope* option in the rule builder) if you
want channels created in the target group. Leave it off if you
deliberately want a same-name channel in another group to absorb the
streams (the original GH-92 behavior).

### `RULE_HAS_NO_HOPE_OF_MATCHING`

**Trigger.** Every OR-group on the rule contains a `never`
condition. The rule provably matches no stream.

**Remediation.** Disable the rule, or remove the `never` conditions.

## What the analyzer does NOT do (yet)

Phase 2 candidates, not in this build:

- **Live regex match counts.** "This regex would match all 1,472
  groups" — strong signal that the analyzer can't produce without a
  group corpus.
- **Per-rule dry-run replay** over a bundle's `channels.csv` to count
  match/skip outcomes.
- **Surfacing findings inside the rule-builder UI.** The API is the
  contract; a frontend follow-up is a separate bead.
- **Auto-fix / quick-fix actions** ("change Matches (Regex) → Begins
  With for this condition").

## Implementation

| Component | Location |
|---|---|
| Lint codes | `backend/regex_lint.py` (`lint_pattern_advisory`, `lint_conditions_json_advisory`) |
| Structural analyzer | `backend/auto_creation_rule_analyzer.py` |
| Endpoints | `backend/routers/auto_creation.py` (search for `/rules/analyze`) |
| MCP tool | `mcp-server/tools/auto_creation.py` (`analyze_auto_creation_rules`) |
| Acceptance fixture | `backend/tests/fixtures/bd_0gntx/user_2026_04_28_rules.yaml` |
| Acceptance tests | `backend/tests/unit/test_bd_0gntx_user_bundle.py` |

The analyzer's OR-grouping logic is duplicated from
`auto_creation_evaluator.evaluate_conditions` (lines 828–834). The
duplication is intentional: the evaluator is performance-critical
and we don't want a runtime import dependency in the analyzer.
`split_or_groups` and the test
`test_users_sports_rule_grouping` lock the contract; if the
evaluator's grouping algorithm ever changes, the analyzer must
change with it.

---

## Group protection: stream-side fire gate vs merge-target filter

Two distinct mechanisms protect specific channel groups from unwanted
merges. They are NOT interchangeable — each applies at a different
point in the pipeline.

| Mechanism | Kind | When it applies | What it checks | Primary use case |
|---|---|---|---|---|
| `normalized_name_in_group` / `normalized_name_not_in_group` | **Condition** (stream-side fire gate) | Evaluated before the rule fires | Whether the triggering stream's normalized name IS (or is NOT) already present as a channel in group N | Skip the whole rule for a stream if a same-name channel already exists (or doesn't) in a specific group |
| `target_channel_in_group` / `target_channel_not_in_group` | **`merge_streams` action param** (merge-target filter) | Applied after the merge target channel is resolved | Whether the resolved target channel's group is in (or not in) the provided list | Keep merges OUT of (or ONLY into) specific groups, regardless of how the stream or rule fired |

### Stream-side fire gate (`normalized_name_[not_]in_group`)

These are **condition types** on the rule's condition list. When
the evaluator reaches one of these, it checks whether a channel
whose normalized name matches the triggering stream's normalized
name already exists inside group N:

- `normalized_name_in_group` — fires the rule only when such a
  channel **exists** in group N.
- `normalized_name_not_in_group` — fires the rule only when no
  such channel exists in group N.

**Critical limitation.** These conditions gate whether the rule
fires at all. Once the rule fires, they have no further effect.
They do NOT constrain which existing channel a `merge_streams`
action eventually targets — the merge resolution runs independently
and may land the stream in any group.

### Merge-target filter (`target_channel_[not_]in_group`)

These are **parameters on a `merge_streams` action dict**, applied
post-resolution. After the executor resolves a candidate target
channel (via `target=auto`, `name_exact`, `name_regex`, or
`tvg_id`), it checks the resolved channel's `channel_group_id`
against the filter list:

- `target_channel_not_in_group` — **skip** the merge if the
  resolved channel's group is in this list.
- `target_channel_in_group` — **skip** the merge if the resolved
  channel's group is NOT in this list.

Both default to absent (no filter), preserving existing behavior for
rules that predate this feature. Provide an empty list (`[]`) to
explicitly no-op one direction while setting the other. Values must
be integer group IDs; `bool` values are rejected by the schema
validator.

Action dict example:

```json
{
  "type": "merge_streams",
  "target": "auto",
  "target_channel_not_in_group": [42, 99]
}
```

### Concrete worked example

Scenario: a rule's condition list includes
`normalized_name_not_in_group=5` (only fire for streams whose
normalized name is not yet a channel in group 5). The `merge_streams`
action has `target_channel_not_in_group=[5]` (skip any merge that
would land in group 5).

1. Stream "Sky Sport 1" arrives. Its normalized name "sky sport 1"
   is NOT in group 5 — the **stream-side gate passes**, so the rule
   fires.
2. The executor resolves the auto merge target and finds an existing
   channel named "Sky Sport 1" in group 5.
3. The **merge-target filter** fires: the resolved channel is in
   group 5, which is excluded. The merge is **skipped** with
   `skipped=True`; the stream is left unattached.

Without `target_channel_not_in_group`, step 3 would have merged the
stream into the group-5 channel — the stream-side condition alone
could not prevent it, because it only checked whether the name
existed, not whether the merge itself would land there.

> **Cross-reference.** Both parameters are also documented in the
> `create_auto_creation_rule` MCP tool docstring
> (`mcp-server/tools/auto_creation.py`, conditions block and
> `merge_streams` actions block).

---

## Scored-fuzzy rule path (`loose_name_match` + `min_score`)

Shipped in v0.17.3-0006 (bead jnzst). This path adds a callsign-aware, confidence-scored stream→channel matching mode on top of the existing `loose_name_match` boolean. It is opt-in — a `loose_name_match` rule without `min_score` keeps running the legacy fuzzy cascade exactly as before.

### How to activate it

Add `min_score` to a `merge_streams` action that already has `loose_name_match: true`:

```json
{
  "type": "merge_streams",
  "target": "auto",
  "loose_name_match": true,
  "min_score": 0.75,
  "target_channel_in_group": [14, 22],
  "allow_no_callsign": false
}
```

When `min_score` is present, the executor delegates to the unified scoring core (`services.dedup_matcher.score_all`) instead of the legacy cascade. The score is a normalized float in [0.0, 1.0] produced by RapidFuzz `token_set_ratio` on LOCALS-cleaned names, with two override rungs (callsign exact match → 1.0, tvg_id callsign equality → 1.0) that take precedence.

### Required fields and validation

| Field | Type | Required | Notes |
|-|-|-|-|
| `loose_name_match` | boolean | Yes (`true`) | Must be `true`; `min_score` has no meaning on the exact path. |
| `min_score` | float [0.0–1.0] | Yes (to activate scored path) | Floored at `CONFIDENCE_FLOOR` (0.60). A value below the floor is rejected at write time with a `400`. |
| `target_channel_in_group` | list of integer group IDs | **Yes — non-empty** | A scored-fuzzy rule with no `target_channel_in_group` allowlist is rejected by the schema validator (`400`). This is an intentional safety constraint: an unscoped fuzzy rule was the root cause of a 1,341-false-positive merge incident. |
| `allow_no_callsign` | boolean | No (default `false`) | Opt-in to matching pairs where at least one side has no parseable callsign. When `true`, such pairs are admitted only at score ≥ 0.90. |

### Scoring precedence (shared core)

The scoring core applies a hard precedence ladder — the same ladder the `fuzzy-preview` endpoint uses:

1. **M1 callsign hard-reject.** If both the stream name and the channel name parse a callsign (e.g. `WBAY`/`WGBA`) and they differ, the pair is rejected unconditionally — score 0.0, verdict `"conflict"`. This fires before any threshold and cannot be overridden.
2. **tvg_id callsign override.** If the stream's `tvg_id` (or its name) and the channel's `tvg_id` (or its name) parse the same callsign, the score is set to 1.0 (`tvg_id-override`). Only reached when M1 did not fire.
3. **LOCALS-cleaned fuzzy.** RapidFuzz `token_set_ratio` on names cleaned with the LOCALS cleaner (strips `US |` / state-code / `CITY:` / `DIREC TV` prefixes, dotted sub-channel numbers, superscripts, quality tags like `RAW`/`HD`, and run-together market tokens like `GREENBAY → GREEN BAY`).

### No-callsign opt-in (`allow_no_callsign`)

The default policy requires a parseable FCC callsign on both sides of a match. This is the safe default — without a callsign cross-check, `WGBA 2 (NBC)` scored 0.889 against `WBAY` in the spike that motivated the callsign gate.

Set `allow_no_callsign: true` to admit pairs where at least one side lacks a parseable callsign, subject to the 0.90 floor (`NO_CALLSIGN_FLOOR`). Even with the opt-in, an M1 conflict (different callsigns on both sides) is still never admitted.

### Journal provenance

For each stream matched via the scored-fuzzy path, the executor writes a journal entry with:

- The match `score` (float)
- `callsign_verdict` (`"match"` or `"absent"`)
- `signal` — which scoring rung fired (`"callsign-exact"`, `"tvg_id-override"`, `"fuzzy-with-callsign"`, `"fuzzy-no-callsign-floor"`)
- The stream and channel callsigns (when parsed)

This lets you audit exactly why a merge fired — accessible in the ECM journal tab or via `GET /api/journal`.

### Rollback

Auto-creation executions are rollback-able via `POST /api/auto-creation/executions/{id}/rollback` regardless of whether the run used the scored-fuzzy path or the exact path.

### What is unchanged

- The global exact-match rule path is unchanged. Rules without `min_score` run as before.
- `match_by` remains a validated no-op (see below).
- `CONFIDENCE_FLOOR` (0.60) is the same floor used by the interactive stream deduplication (ADR-008 §D2). Both import the constant from `services.dedup_matcher` so they cannot drift.

### Previewing before committing

Use the `GET /api/auto-creation/fuzzy-preview` endpoint (or the `preview_fuzzy_matches` MCP tool) to inspect scored triples before enabling a rule. The preview applies the identical scoring core and admission policy, so it shows exactly what the rule would do. See [`docs/api.md`](api.md) for the endpoint reference.

---

## Matching: `loose_name_match` vs the deprecated `match_by`

### `loose_name_match` (the real control)

`loose_name_match` is a **boolean parameter on a `merge_streams`
action** (default `false`). It controls whether `target=auto`
matching uses strict or fuzzy resolution:

- `false` (default) — merge into an existing channel only on
  **exact normalized-name equality** (case-insensitive). The stream's
  normalized name must equal the channel's normalized name character
  for character.
- `true` — restore the **legacy fuzzy cascade**: core-name
  match → deparenthesize → word-prefix containment → call-sign
  lookup. Use when you explicitly want the older broad-matching
  behavior.

The strict default (`false`) was introduced to fix production
over-matching where the word-prefix step caused unrelated streams
(e.g., 75 "Sky Sport *" variants) to be absorbed by a single "Sky
Sport 4K" channel.

Action dict example:

```json
{
  "type": "merge_streams",
  "target": "auto",
  "loose_name_match": true
}
```

### `match_by` (validated, runtime no-op — back-compat only)

There is **no `exact_match` or `match_strict` parameter** — a common
misconception. `match_by` accepts `"tvg_id"`, `"normalized_name"`,
or `"stream_group"` and is validated by the schema, but **it is
never consumed by the executor at runtime**. It is retained solely
for backward compatibility of stored rules; changing it does not
change matching behavior. Use `loose_name_match` to control fuzzy
vs exact matching.

> **Cross-reference.** Both parameters are documented in the
> `create_auto_creation_rule` MCP tool docstring
> (`mcp-server/tools/auto_creation.py`, `merge_streams` actions
> block, including the explicit `NOTE: match_by is a DEPRECATED
> no-op` callout).
