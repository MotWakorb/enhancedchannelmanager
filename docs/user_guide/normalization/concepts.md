# How Normalization Works

Normalization is the step between the raw name a provider puts on a stream
and the name ECM stores on a channel. This article explains what the engine
does, the four surfaces that run it, and the one difference between those
surfaces that is configuration rather than a bug.

## What normalization does

A provider's playlist rarely gives you the name you want on a channel. The
same logical channel arrives as `US: ESPN HD` from one provider and
`ESPN` from another. Normalization applies an ordered set of rules to the
raw name and returns a cleaned-up name.

You can see this for yourself without changing anything. Go to
**Settings → Channel Normalization**, expand **Test Rules**, paste sample
names, and press **Run Test**. With ECM's default rule groups enabled:

| You type | Test Rules returns | Why |
|-|-|-|
| `US: ESPN HD` | `ESPN` | Country prefix stripped, then quality suffix stripped |
| `NFL: FOX Sports 1 EAST` | `FOX Sports 1` | League prefix stripped, then timezone suffix stripped |
| `NFL Network` | `NFL Network` | The league strip requires a delimiter, so a brand name survives |
| `ESPN²` | `ESPN2` | Unicode preprocessing, before any rule runs |

Two things are worth noticing in that table. Rules chain: the second rule
sees what the first rule produced, not the raw input. And the last row
changed even though no rule matched it, because a fixed Unicode
preprocessing step runs ahead of your rules.

## Rules chain, and the whole set repeats

Rules are a pipeline, not a set of independent matchers. Groups run in
group-priority order; rules run in rule-priority order within their group;
each rule receives the previous rule's output.

After the full set has run once, ECM runs it again, and keeps running it
until a pass produces no change (up to ten passes). That is why
`4K/UHD`-style double suffixes collapse completely: stripping one suffix
exposes the next one, and the next pass strips that too. Runs of
whitespace are collapsed to a single space between passes.

[Rule groups and ordering](rule-groups-and-ordering.md) covers the
consequences of this in detail.

## Unicode preprocessing runs first

Before any rule you wrote gets a look at the name, ECM applies a fixed
preprocessing policy. It is not configurable per rule, and it is identical
on every surface. In short, it canonicalizes accented characters, removes
invisible formatting code points such as zero-width space and byte-order
mark, and converts superscripts to ASCII (`ESPN²` becomes `ESPN2`,
`ESPN ᴴᴰ` becomes `ESPN HD`).

There is also an opt-in confusables fold, off by default, that collapses
Cyrillic and Greek look-alike characters to Latin. Enable it only if you
have evidence of homoglyph collisions in your feed.

The full policy, including exactly which characters survive and which do
not, is documented in
[`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md#normalizationpolicy-the-unicode-preprocessor).

## The four places normalization runs

| Surface | What it normalizes | Which rule groups it uses |
|-|-|-|
| **Test Rules** (Settings → Channel Normalization) | Whatever you paste into the box. Read-only, no side effects. | Every enabled group |
| **Channel Pipeline** rules | The stream name a rule is about to turn into a channel name. | Only the groups picked in that rule's **Normalization Groups** field |
| **Apply normalization rules** checkbox, when creating channels from streams in Channel Manager | The name of each channel being created. | Every enabled group |
| **Apply to existing channels** | Names already stored on channels. Manual, previewed, per-row confirmed. | Every enabled group |

All four call the same engine with the same preprocessing policy. For the
same input and the same set of groups they produce the same output. That
is the *parity contract*, and it is checked nightly by an automated canary
tied to [SLO-5](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/sre/slos.md#slo-5-normalization-correctness) (GitHub).
If two
surfaces disagree on the same input with the same groups, that is a defect
in ECM, not something to work around with an extra rule. See
[When things look wrong](when-things-look-wrong.md).

### The difference that is configuration, not a defect

Read the right-hand column of that table again. A Channel Pipeline rule
applies **only** the normalization groups selected on that rule. Every
other surface applies every enabled group.

So the most common report of "Test Rules and my created channels
disagree" is not a parity break at all: the rule that created the channel
had fewer groups selected than Test Rules used, or none at all. A rule
with no groups selected skips normalization entirely, and the rule editor
says so under the picker.

Fix it in the rule, not in the rule set: open the rule, set
**Normalization Groups**, and save. Existing channels are not renamed
retroactively by that change. Use
[Apply to existing channels](apply-to-existing-channels.md) for the
cleanup.

### One more surface-specific detail

**Apply to existing channels** splits a leading channel-number prefix of
the form `107 | ` off the stored name, normalizes only the rest, and puts
the prefix back. Test Rules has no such notion, so pasting the full
`107 | RTL HD` into Test Rules and comparing it against what Apply to
existing channels proposes is not a like-for-like comparison. Paste the
part after the prefix.

## What normalization is not

- **Not search.** Searching channels or streams does its own matching and
  does not run the rule set.
- **Not EPG matching.** Normalization decides the channel name that EPG
  matching later consumes, but it is not itself EPG logic.
- **Not the fold match key.** The Channel Pipeline's *Ignore spacing &
  case differences when matching* option and the Find Duplicates *Ignore
  spacing differences* option compare names by a folded key. They never
  rewrite a stored name. Normalization rules rewrite stored names.
- **Not automatic for channels you already have.** A rule change affects
  names produced from that point on. Existing channels keep their stored
  names until you run Apply to existing channels.

## Going deeper

- [Author your first rule](author-your-first-rule.md): the hands-on
  walkthrough.
- [Rule groups and ordering](rule-groups-and-ordering.md): priority,
  chaining, and the multi-pass loop.
- [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md): the full dual-audience
  reference. The Unicode policy, the confusables fold, the parity contract,
  the metrics, and the developer reference all live there.
- [`docs/runbooks/normalization-canary-divergence.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/normalization-canary-divergence.md):
  what happens when the parity canary fires.
