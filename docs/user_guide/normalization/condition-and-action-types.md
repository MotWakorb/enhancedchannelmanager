# Condition and Action Types

Every normalization rule is one condition and one action: what to look for
in the name, and what to do when it is found. This article is the catalogue
of both, plus the safety behaviours that can make a rule quietly decline to
fire.

The fields described here are in the rule dialog at **Settings → Channel
Normalization**, reached from any group's **Add Rule** button.

## Condition types

Matching is case-insensitive unless you tick **Case Sensitive**.

| Condition Type | Matches when | Watch out for |
|-|-|-|
| **Starts With** | The name begins with your **Pattern** *and* the next character is a separator | `ES` does not match `ESPN`. It matches `ES: ...`, `ES \| ...`, `ES-...`, `ES/...` and `ES ...`. It also will not match when the pattern is the entire name, because nothing would be left |
| **Ends With** | The name ends with your **Pattern** *and* the character before it is a separator | `HD` does not match `ADHD`. Same "must leave something behind" restriction |
| **Contains** | Your **Pattern** appears anywhere in the name | No separator requirement, so short patterns match inside words |
| **Regex** | Your **Pattern**, as a regular expression, matches anywhere in the name | See [Regex safety](#regex-safety) below |
| **Tag Group** | Any tag in the chosen tag vocabulary matches at the chosen position | Choose the vocabulary in **Tag Group** and the position in **Match Position** |
| **Always** | Always. The **Pattern** box is disabled | Use with care: an always-matching rule applies its action to every name |

The separator characters for **Starts With** and **Ends With** are space,
tab, colon, hyphen, pipe and forward slash.

### Tag Group conditions

**Tag Group** matches against a named vocabulary you manage under
[Tags](../settings/tags.md), rather than a single pattern. This is how
ECM's shipped groups work: one rule against *Quality Tags*, one against
*Country Tags*, and so on.

**Match Position** has three values:

- **Prefix**: the tag appears at the start.
- **Suffix**: the tag appears at the end.
- **Anywhere**: the tag appears anywhere in the name.

With **Prefix** or **Suffix** selected, an extra checkbox appears: **Only
strip when followed by a delimiter (not a space)**. Ticking it means a bare
space is not enough to trigger the strip. This is what keeps `NFL Network`
intact while `NFL: Buffalo Bills` still loses its prefix. It is off by
default, and only meaningful for prefix and suffix matches.

### Compound conditions

The dialog has a **Condition Mode** toggle: **Simple** or **Compound
(AND/OR/NOT)**. In compound mode you build a list of conditions, each with
its own type, pattern, **NOT** checkbox and **Aa** (case sensitive)
checkbox, and combine them with **AND (all must match)** or **OR (any must
match)**.

One thing to know about how the action then applies: the text the action
operates on is the span matched by the **first condition in the list that
actually matched and is not negated**. If you are combining conditions with
OR and using a positional action such as Strip Prefix, order the list so
the condition whose span you want to act on comes first.

## Action types

| Action Type | What it does |
|-|-|
| **Strip Prefix** | Removes the matched text from the start, plus any separators immediately after it |
| **Strip Suffix** | Removes the matched text from the end, plus any separators immediately before it |
| **Remove** | Removes exactly the matched span, wherever it is |
| **Replace** | Replaces the matched span with **Replacement Value** |
| **Regex Replace** | Runs a regex substitution over the whole name |
| **Normalize Prefix** | Keeps the matched prefix but standardises the separator after it |
| **Capitalize** | Recases the whole name |

**Replacement Value** is only editable for **Replace**, **Regex Replace**
and **Normalize Prefix**. For the other actions the field is disabled,
because they take no value.

Notes on the ones with surprises in them:

- **Regex Replace requires the Regex condition type.** Pair it with any
  other condition type and the rule does nothing at all. Backreferences may
  be written either `$1` or `\1`.
- **Normalize Prefix** uses **Replacement Value** as the separator to put
  after the prefix. Leave it empty and you get ` | `. So `US: ESPN` with an
  empty value becomes `US | ESPN`.
- **Capitalize** turns **Replacement Value** into a dropdown with **Title
  Case**, **UPPERCASE**, **lowercase** and **Sentence case**. Title Case is
  the default and is deliberately conservative: it preserves recognised
  abbreviations and names that already carry intentional mixed case.

## The else branch

Tick **Execute alternate action if condition doesn't match (Else)** and you
get **Else Action Type** and **Else Replacement Value**. The else action
fires on names the condition did *not* match, which is how you write "strip
this suffix if present, otherwise recase" in a single rule.

Not every action makes sense with nothing matched, and three behave
differently in the else branch:

- **Replace** as an else action replaces the **entire name** with the else
  value, not a span. There was no matched span to replace.
- **Remove** and **Normalize Prefix** as else actions do nothing. They both
  need a matched span.
- **Strip Prefix** and **Strip Suffix** as else actions strip leading or
  trailing separators and whitespace only.

## Stop Processing After Match

**Stop Processing After Match** halts the remaining rules **in that rule's
own group**. Later groups still run. See
[Rule groups and ordering](rule-groups-and-ordering.md).

## Safety behaviours you should expect

### A strip that would gut the name is refused

**Strip Prefix** and **Strip Suffix** will not complete if the result would
be a single generic word. `NFL Network` is not reduced to `Network`,
because that collapses genuinely distinct channels onto one name. When this
guard trips, the name is left exactly as it was and the rule contributes
nothing to the trace.

The generic word list defaults to *network, tv, channel, channels, sports,
sport, news, the, plus, hd, uhd*, and is overridable by creating a tag
group named `Generic Word Tags`.

Note the shape of the guard: it applies only when the whole remainder is
one generic word. `Sky Sports Main Event` survives a strip that leaves it,
because it is more than one word.

### Regex safety

Regex patterns are checked twice.

**When you save.** Regex patterns are linted before they are stored, and
the save is rejected outright on three grounds: a pattern longer than 500
characters, a pattern the regex compiler will not accept, and a nested
unbounded quantifier such as `(a+)+` or `(.*)*`, which can backtrack
catastrophically. The rejection message names which of the three tripped
and what to do about it. Only regex conditions are linted; replacement
values are literal templates, not patterns.

**When they run.** Every regex evaluation has a 100 ms budget. A pattern
that exceeds it is abandoned for that input and the rule behaves as if it
did not match. There is no crash and no partial rewrite, and the only
signal is a `[SAFE_REGEX]` warning in the ECM logs naming the rule.

If a rule with a regex condition works on short names and silently stops
working on long ones, suspect the timeout and check the logs. The rewrite
guidance is in the Regex section of
[`docs/style_guide.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/style_guide.md).

## Going deeper

- [Author your first normalization rule](author-your-first-rule.md): these
  fields in the context of an actual task.
- [Rule groups and ordering](rule-groups-and-ordering.md): how several
  rules combine.
- [Tags](../settings/tags.md): managing the vocabularies that **Tag Group**
  conditions match against.
- [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/normalization.md#safe_regex-log-entries):
  how to read the `[SAFE_REGEX]` log lines a timed-out pattern leaves
  behind.
