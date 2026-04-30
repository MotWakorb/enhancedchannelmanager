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

`severity` is always one of `error`, `warning`, or `info`. Phase 1
emits only `warning`-level findings.

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
