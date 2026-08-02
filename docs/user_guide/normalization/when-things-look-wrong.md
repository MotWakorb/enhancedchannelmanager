# When Normalization Looks Wrong

Most reports of "normalization is broken" turn out to be one of five
configuration causes, all of which you can confirm yourself in a couple of
minutes. This article works through them in the order that resolves the
most cases fastest, and then describes the one symptom that really is a
defect in ECM and how to escalate it.

Throughout, **Test Rules** at **Settings → Channel Normalization** is your
instrument. It is read-only, it shows a per-rule trace, and it runs the
same engine every other surface runs.

## Start here: does Test Rules give the right answer?

Paste the raw name into **Test Rules** and press **Run Test**.

- **Test Rules gives the name you want.** The rule set is fine. The problem
  is in *which* rules ran on the surface that produced the bad name. Go to
  [The rule ran with a different set of groups](#the-rule-ran-with-a-different-set-of-groups).
- **Test Rules gives the wrong name too.** The problem is in the rules
  themselves. Go to
  [The rule did not match what you thought](#the-rule-did-not-match-what-you-thought).

## The rule ran with a different set of groups

This is the single most common cause, and it is configuration, not a bug.

**Test Rules** applies **every enabled group**. A Channel Pipeline rule
applies **only the groups selected in its own Normalization Groups field**.
A rule with nothing selected does not normalize at all, and the rule editor
warns about it under the picker.

To check and fix:

1. Open the Channel Pipeline rule that created the channel.
2. Look at **Normalization Groups**. If it is empty, or missing a group you
   expected, that is your answer.
3. Select the groups you want. **Select all enabled** picks all of them.
4. Save the rule and run it again.

Channels created before this change keep their old names. Use
[Apply to existing channels](apply-to-existing-channels.md) to bring them
up to date.

## The rule did not match what you thought

Work the trace, not your intuition. The trace lines under each **Test
Rules** result name every rule that fired and show its before and after. If
your rule is not in the trace, it did not match.

### An earlier rule already changed the text

Rules chain. If a rule earlier in the order stripped `HD`, a later rule
whose pattern is `HD` has nothing left to match. This is why a rule can
look correct in the dialog's **Live Preview**, which evaluates that rule
alone, and contribute nothing in **Test Rules**, which runs the whole
chain.

Read the trace top-down and find the value your rule actually received.
Then either fix the pattern to match that value, or reorder so your rule
runs first. See [Rule groups and ordering](rule-groups-and-ordering.md).

### Starts With and Ends With need a separator

Neither matches mid-word. `ES` does not match `ESPN`, and `HD` does not
match `ADHD`: the character on the far side of the pattern has to be a
space, tab, colon, hyphen, pipe or slash. Neither will match when the
pattern is the whole name either, since that would leave nothing behind.

If you genuinely want a mid-word match, use **Contains** or **Regex**.

### A Tag Group rule with the delimiter option on

Tag Group rules matching at **Prefix** or **Suffix** can require a real
delimiter rather than a bare space. When that option is on, `NFL: Buffalo
Bills` loses its prefix but `NFL Network` does not. That is the option
doing its job. Turn it off on the rule if you want bare-space matches too,
and expect brand names starting with a tag to be affected.

### The strip was refused to protect the name

Strip Prefix and Strip Suffix decline to run when the result would be a
single generic word: *network, tv, channel, channels, sports, sport, news,
the, plus, hd, uhd*. The name is left untouched and the rule leaves no
trace entry. `NFL Network` is not reduced to `Network` for this reason.

The check only fires when the entire remainder is one generic word. Multi
word remainders always go through.

### A regex condition is timing out

Regex evaluation has a 100 ms budget per input. A pattern that exceeds it
is abandoned for that input, and the rule behaves exactly as if it had not
matched, with no error surfaced in the UI. The signal is a `[SAFE_REGEX]`
warning in the ECM logs naming the rule.

The tell is a rule that works on short names and stops working on long
ones. Check the logs, then simplify the pattern.

## The name changed but no rule fired

Some transformations happen before your rules do. If the trace is empty but
the name still changed, it was the Unicode preprocessing policy:

- Superscripts convert to ASCII. `ESPN²` becomes `ESPN2` and `ESPN ᴴᴰ`
  becomes `ESPN HD`.
- Accented characters are canonicalized to their pre-composed form.
- Invisible formatting characters such as zero-width space and byte-order
  mark are removed.

This is not configurable per rule. It is documented in
[`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/normalization.md#normalizationpolicy-the-unicode-preprocessor).

The mirror image of this is a pattern that will not match because of an
invisible or look-alike character in the input. Two characters that render
identically may be different code points, most often a Cyrillic letter
standing in for a Latin one. The default policy does not fold those
together. Copy the exact characters out of the raw name into your pattern,
or turn on the opt-in confusables fold described in the same reference.

## Existing channels did not change

Nothing renames a channel retroactively. Rule edits, group toggles and
newly selected Normalization Groups all affect names produced from that
point forward. Stored names stay as they are until you run
[Apply to existing channels](apply-to-existing-channels.md).

## The one that really is a bug

**Symptom:** two surfaces produce different output for the same input with
the same set of groups applied. For example, Test Rules with only Group A
enabled returns one name, and a Channel Pipeline rule configured with only
Group A creates a channel with a different name.

Every surface runs one engine with one preprocessing policy, and that they
agree is a contract ECM enforces, not an aspiration. It is checked nightly
by an automated canary, and a divergence is tracked as a zero-tolerance
breach of [SLO-5](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/sre/slos.md#slo-5-normalization-correctness) (GitHub).

So if you have genuinely ruled out the group-scope difference above, do not
paper over it with an extra rule. A workaround rule hides the divergence
and will produce wrong names again the moment the underlying defect is
fixed.

**What to gather before reporting:**

- The exact raw input, copied rather than retyped, so invisible characters
  survive.
- The output from each surface that disagrees.
- Which rule groups were enabled, and which were selected on the Channel
  Pipeline rule.
- The ECM version, from the footer.
- The approximate time, so the run can be found in the logs.

**Where it goes:**
[`docs/runbooks/normalization-canary-divergence.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/normalization-canary-divergence.md)
is the response procedure. If your instance recently rolled back the
unified Unicode policy, read
[`docs/runbooks/normalization-unified-policy.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/normalization-unified-policy.md)
first: running in the legacy mode deliberately changes what the
preprocessor does, and can look like a divergence.

## Going deeper

- [How normalization works](concepts.md): the four surfaces and the parity
  contract.
- [Rule groups and ordering](rule-groups-and-ordering.md): ordering,
  chaining, and the repeat loop.
- [Condition and action types](condition-and-action-types.md): the exact
  matching rules for each condition type.
- [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/normalization.md#troubleshooting): the
  reference troubleshooting section, including log lines and metrics.
- [Troubleshooting](../troubleshooting/index.md): the general
  troubleshooting section for problems that are not normalization-specific.
