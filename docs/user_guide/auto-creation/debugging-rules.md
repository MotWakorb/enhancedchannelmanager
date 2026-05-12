# Debugging Rules

> **Audience:** Operator investigating why an auto-creation rule is not firing
> as expected, or auditing a large rule set for silent misconfigurations.

## What the rule analyzer is

When a rule does not produce the channels you expected, the cause usually falls
into one of two categories:

1. **Static configuration bug** — the rule is written in a way that can never
   work regardless of which streams arrive. Common examples: a regex condition
   that accidentally matches every stream, a `merge_streams` action targeting
   a group with no channels to merge into, or a `never` condition that silently
   disables the whole rule.

2. **Runtime mismatch** — the rule is structurally sound but the streams
   arriving at runtime do not match the conditions. Diagnosing this requires
   running a dry-run (see [test-a-rule.md](test-a-rule.md) — planned).

The **rule analyzer** catches category 1 without running anything. It reads
your saved rules and checks them for known bad patterns, then tells you exactly
which rule, which condition or action, and what the problem is. It is
**advisory only**: running it never changes anything in ECM, and saving a rule
is never blocked by analyzer warnings.

If you have 40 rules and you are not sure which ones are subtly broken, start
with the analyzer. It is cheap to run and finds the "this rule can never work"
class in seconds.

---

## Analyzer vs. dry-run: when to use each

| | Rule analyzer | Dry-run / preview |
|---|---|---|
| **What it checks** | Static configuration bugs in saved rules | What a rule *would do* against current streams |
| **Touches the DB?** | Read-only (live mode) or not at all (bundle mode) | Read-only |
| **Creates or changes channels?** | Never | Never (dry_run=true) |
| **When to use it** | First — catch rules that can never work | Second — verify that a structurally-sound rule matches the streams you expect |

Run the analyzer first. Fix any findings it reports, then use the dry-run to
confirm the surviving rules produce the right channels.

---

## The seven finding codes

Each finding has a severity, a code, the field it points at, and a suggestion.
Six of the seven codes are `warning` severity — problems worth fixing, not fatal
errors. The seventh, `MERGE_SCOPE_NOT_TARGET_GROUP`, is `info` — an advisory
heads-up about a setting whose default changed, not a misconfiguration.

### `REGEX_TRIVIALLY_MATCHES_ALL`

**What it looks like in the rule editor:**
You entered something like `UK|` or `|UK` in a **Matches (Regex)** condition
field.

**Why it is wrong:**
The pipe `|` in a regex means "or". `UK|` means "UK or empty string". Because
every string contains an empty string at position 0, this pattern matches every
single stream — the condition becomes useless as a filter.

**Worked example:**
You want to match streams whose group name starts with `UK|`. You type `UK|`
into Stream Group → Matches (Regex). The rule now applies to every stream in
every group.

**How to fix it:**

- Switch the operator to **Begins With** and enter `UK` — the pipe is not part
  of the value, it is a delimiter in the raw M3U group name.
- Or, if you genuinely need a regex and want to match the literal characters
  `UK|`, escape the pipe: `^UK\|`.

---

### `OPERATOR_VALUE_LOOKS_LIKE_REGEX`

**What it looks like in the rule editor:**
You entered something like `^4K` or `HD$` in a **Contains** condition field
(any `*_contains` operator).

**Why it is wrong:**
**Contains** is a plain substring search, not a regex. The `^` and `$`
characters are not anchors here — they are treated as literal characters. A
search for `^4K` looks for a group name that contains the two characters `^`
and `4K` next to each other. No M3U group name contains a literal `^`, so the
condition matches nothing and the rule fires for no streams.

The analyzer does **not** flag a bare `|` in a Contains field. Many M3U group
names legitimately contain a pipe (`UK| MOVIES`), so searching for `UK|` under
Contains is a valid substring match.

**Worked example:**
You want streams whose group name begins with `4K`. You type `^4K` into Stream
Group Contains. The rule matches nothing, silently.

**How to fix it:**

- Use **Begins With** and enter `4K` (drop the `^`).
- Use **Ends With** and enter the suffix (drop the `$`).
- Switch to **Matches (Regex)** if you genuinely need regex, and verify the
  pattern in the rule editor's preview.

---

### `REGEX_REDUNDANT_ESCAPE_CARET`

**What it looks like in the rule editor:**
A Matches (Regex) condition field contains a pattern starting with `^\^` —
that is, a caret anchor followed immediately by an escaped (literal) caret,
like `^\^4K`.

**Why it is wrong:**
This almost always means a typo or a double-escape. The user typed `^4K` (or
the UI's escape pass added a backslash), producing `^\^4K`. The `^` anchors the
match to the start of the string; `\^` then requires a literal caret character
at that position. Most stream group names do not start with a caret, so the
pattern matches far fewer streams than intended — or none.

**Worked example:**
You want streams whose name starts with `4K`. The condition ends up storing
`^\^4k` instead of `^4k`.

**How to fix it:**
Decide which of these you actually want:

- `^4K` — matches strings that start with `4K`.
- `\^4K` — matches strings that contain a literal caret followed by `4K`
  anywhere (rarely what you want).

Drop the extra caret accordingly.

---

### `ANDOR_DROPS_GUARD`

**What it looks like in the rule editor:**
A rule has multiple OR branches, and one or more of those branches includes a
"guard" condition — for example, **Normalized Name In Group** — but at least
one other OR branch does not.

Guards in this context are conditions that constrain *which streams* the rule
applies to at all, regardless of the stream's name or group:

- Normalized Name In Group
- Normalized Name Not In Group
- Normalized Name Exists
- Provider Is

**Why it is wrong:**
In a condition list, AND binds tighter than OR. The expression:

```
A AND B OR C
```

evaluates as:

```
(A AND B) OR C
```

The guard in arm 1 (`A AND B`) does **not** carry into arm 2 (`C`). Any stream
that satisfies arm 2 fires the rule regardless of whether it would have passed
the guard.

**Worked example:**
You want a Sports rule that only processes streams already in the Sports
normalization group, and you add three OR arms to cover different prefixes:

```
Normalized Name In Group = Sports  AND  Stream Group Matches = UK|
OR  Stream Group Matches = US|
OR  Stream Group Contains = ^4K
```

The second and third arms have no guard. Streams from any `US|` or `^4K` group
will trigger the rule whether they are in the Sports group or not.

**How to fix it:**

- Repeat the guard in every OR arm:

  ```
  Normalized Name In Group = Sports  AND  Stream Group Matches = UK|
  OR  Normalized Name In Group = Sports  AND  Stream Group Matches = US|
  OR  Normalized Name In Group = Sports  AND  Stream Group Contains = ^4K
  ```

- Or split the rule into three separate rules (one per OR arm), each carrying
  its own guard. This also makes individual rules easier to enable or disable
  independently.

The `detail` field in the API response tells you the exact OR-group index
numbers that are missing the guard.

---

### `MERGE_STREAMS_NO_TARGET_CHANNELS`

**What it looks like in the rule editor:**
A rule uses the **Merge Streams** action and points at a specific channel group,
but that group currently has zero channels in it.

**Why it is wrong:**
`merge_streams` attaches incoming streams to channels that **already exist** in
the target group. It does not create channels. If the group is empty, every
matched stream is silently skipped with "no existing channel found" — no
channels are created and no errors are raised. The operator typically expected
new channels to appear.

**Worked example:**
You set up a "Merge into Sports" rule pointing at group ID 42. Group 42 was
recently cleared as part of a cleanup. The rule runs, matches 150 streams, and
produces zero channels.

**How to fix it:**

- If you want new channels created, change the action to **Create Channel**.
- If you want to merge into existing channels, seed the target group with
  channels first (either manually or via a Create Channel rule), then re-run.

**Note:** This finding is only available when you run the analyzer via the
**from-bundle** path and the bundle includes `channel_groups_diagnostic.json`.
The live-mode endpoint does not fetch channel-group counts.

---

### `MERGE_SCOPE_NOT_TARGET_GROUP`

**Severity:** `info` (an advisory heads-up, not a misconfiguration).

**What it looks like in the rule editor:**
A rule has a **Create Channel** action with **If channel exists → Merge**
(or **Merge only**), and the rule's **Merge lookup scope** option —
*"Scope merge lookups to this rule's target group"* — is **off**.

**Why the analyzer flags it:**
When **Merge lookup scope** is off, the "does a channel with this name already
exist?" check searches **every channel group**, not just this rule's target
group. If a channel with the same name already exists in *any* other group, the
incoming stream merges into that channel — and **no channel is created in this
rule's target group**. The rule's run report shows channels updated, but
0 created, even though you pointed it at a fresh group.

New rules now ship with this option **on** by default, which is almost always
what you want for a Create-Channel-and-merge rule. This finding surfaces older
rules that still have it **off** so you can decide whether that is intentional.

**Worked example:**
You have a "UK Sports" rule targeting group **UK | Sports**, with a Create
Channel action set to *Merge* on existing. A "US Sports" rule already created
a channel called **ESPN** in group **US | Sports**. Your UK rule runs, finds
the existing **ESPN** in the US group (because the lookup is not scoped), and
attaches the UK stream to it. Group **UK | Sports** never gets an **ESPN**
channel. Turning on **Scope merge lookups to this rule's target group** makes
the UK rule create its own **ESPN** in **UK | Sports**.

**How to fix it:**

- If you want this rule to create channels in its target group, turn on
  **Scope merge lookups to this rule's target group** in the rule editor (or in
  bulk edit). New same-name channels will then be created in the target group
  instead of merging into a same-name channel elsewhere.
- Leave it off if you *deliberately* want a same-name channel in another group
  to absorb these streams — the original behavior. This is a deliberate-choice
  finding, not an error.

---

### `RULE_HAS_NO_HOPE_OF_MATCHING`

**What it looks like in the rule editor:**
Every OR arm of the rule's conditions contains a **Never** condition.

**Why it is wrong:**
A `never` condition is permanently unsatisfiable — no stream can pass it. If
every OR arm of your rule contains `never`, the rule matches no stream, ever.

This most commonly appears after a rule is disabled by toggling a condition to
`never` as a quick workaround, and the operator later forgets the rule is
effectively dead.

**How to fix it:**

- Disable or delete the rule if it is no longer needed.
- Remove the `never` conditions you no longer want.

---

## How to run the analyzer

There is **no UI surface for the analyzer today.** This is a known gap tracked
separately. The three current ways to run it are:

### 1. Call the API directly (live mode)

```
POST /api/auto-creation/rules/analyze
```

No request body needed. ECM reads all rules from the database and returns the
analysis immediately.

This endpoint requires authentication. See [`docs/api.md`](../../api.md) for
how to authenticate.

Response shape:

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
          "suggestion": "...",
          "detail": { "reason": "empty-alternation" }
        }
      ]
    }
  ],
  "summary": { "error": 0, "warning": 6, "info": 0 }
}
```

Rules with no findings appear in the list with an empty `findings` array. The
`summary` counts findings by severity across all rules.

### 2. Upload a debug bundle (from-bundle mode)

```
POST /api/auto-creation/rules/analyze/from-bundle
```

Upload a debug bundle (`tar.gz`) as a multipart file field named `file`. ECM
reads `rules.yaml` from inside the bundle and runs the same analysis — **it
never touches the database**.

This is the safe way to get support help. Because the from-bundle endpoint does
not read your live installation, you can hand a bundle to someone helping you
(another operator, a support helper, an AI assistant) without exposing your
live channel data. The helper runs the analysis on their end against the bundle
you provided.

To generate a debug bundle from your ECM installation:

1. `POST /api/auto-creation/debug-bundle` — starts the bundle build and returns
   a `job_id`.
2. `GET /api/auto-creation/debug-bundle/{job_id}` — poll until `status` is no
   longer `"running"`. When ready, the response is the `tar.gz` file itself
   (download it).

If the bundle includes `channel_groups_diagnostic.json` (all bundles generated
by the current debug-bundle endpoint include this), the
[`MERGE_STREAMS_NO_TARGET_CHANNELS`](#merge_streams_no_target_channels) finding
becomes available. Without it, that check is skipped — the analyzer never
invents findings from data it does not have.

### 3. Use the `/analyze-rules` agent command

If you are working with an AI assistant (Claude Code or the ECM MCP server),
the `/analyze-rules` command runs the analyzer and formats the results as a
readable report. See [`docs/commands/analyze-rules.md`](../../commands/analyze-rules.md)
for how to invoke it and what arguments it accepts.

The command can run in live mode (against the current database) or bundle mode
(pass a path to a debug bundle `tar.gz`).

---

## Unicode suffix surprises

If a pattern appears to match when you read it but the engine says otherwise
(or vice versa) for stream names containing `ᴴᴰ`, `ᴿᴬᵂ`, `²`, `³`, or
invisible characters like zero-width spaces, the cause is almost always a
normalization mismatch rather than a rule configuration bug.

Under ECM's unified normalization policy, every stream name is NFC-canonicalized,
certain invisible characters are stripped, and superscript letters and digits
are converted to their ASCII equivalents **before** any rule condition is
evaluated. A pattern typed against the raw bytes (for example, one that contains
a zero-width space you cannot see) will not match the post-policy input even
though they look identical on screen.

To diagnose this class of mismatch, use **Settings → Normalization → Test
Rules**: paste the raw stream name and inspect the trace. The trace shows you
exactly what the engine sees when evaluating conditions.

See [`docs/normalization.md`](../../normalization.md) for the full normalization
reference and the parity contract.

---

## What the analyzer does not cover (yet)

The current analyzer (Phase 1) checks static configuration. It does not:

- Report how many streams each rule would match ("this regex matches 1,472
  groups").
- Run a dry-run replay over a bundle's stream list.
- Surface findings inside the rule-builder UI (this is a separate, tracked
  follow-up).
- Offer auto-fix actions ("click here to change Contains → Begins With").

For the per-rule dry-run, see [test-a-rule.md](test-a-rule.md) (planned).

---

## Going deeper

- [`docs/auto_creation_rule_analyzer.md`](../../auto_creation_rule_analyzer.md)
  — the full technical reference: all finding codes with the exact trigger
  logic, the response JSON schema, implementation notes, and what Phase 2 will
  add.
- [`docs/api.md`](../../api.md) — the `/api/auto-creation/rules/analyze` and
  `/api/auto-creation/rules/analyze/from-bundle` endpoints.
- [`docs/commands/analyze-rules.md`](../../commands/analyze-rules.md) — the
  `/analyze-rules` agent command.
- [`docs/normalization.md`](../../normalization.md) — normalization concepts,
  the parity contract, and the Test Rules preview tool.
