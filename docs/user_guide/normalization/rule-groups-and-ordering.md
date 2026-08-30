# Rule Groups and Ordering

Normalization rules run in a defined order, and each rule sees what the
rule before it produced. This article explains how that order is decided,
how to change it, and the three ordering behaviours that most often explain
"my rule previews fine but does nothing in practice."

## Why groups exist

A group is two things at once.

**An ordering bucket.** Groups run in group-priority order, and rules run
in rule-priority order inside their group. Grouping related rules keeps a
long rule set comprehensible and lets you move a whole class of rules
earlier or later in one drag.

**A unit of selection for the Channel Pipeline.** A Channel Pipeline rule
picks which normalization groups it applies, in its **Normalization
Groups** field. Groups are the granularity of that choice: you cannot
select an individual rule there. If you want a rule to be optional for some
pipeline rules, it needs its own group.

Groups you create can be renamed, reordered, deleted and have rules added,
edited and removed. Groups marked **Built-in** can be enabled, disabled and
reordered, and you can add your own rules to them, but their own rules and
the group itself cannot be edited or deleted.

## How ordering is set

There is no priority number to type. Order is set by dragging.

1. Go to **Settings → Channel Normalization**.
2. Press **Reorder** in the **Normalization Rules Engine** header. Drag
   handles appear.
3. Drag a group to move it relative to other groups. Expand a group and
   drag a rule to move it relative to other rules in that group.
4. Press **Done**.

**Result:** the new order is saved immediately and is the order the engine
uses on every surface. Rules cannot be dragged between groups, and rules in
a **Built-in** group cannot be dragged at all.

## Rules chain

The output of one rule is the input to the next. This is the single most
important thing to understand about the rule set, and it is easy to verify:
paste `US: ESPN HD` into **Test Rules** and press **Run Test**. With the
default groups enabled you get `ESPN`, and the trace lines under the result
show two rules firing in sequence, the second one operating on the output
of the first.

The consequences:

- **A later rule never sees the raw name.** If a rule earlier in the order
  strips `HD`, a later rule whose pattern is `HD` will not match. It is not
  disabled and it is not broken; there is simply no longer anything for it
  to match.
- **Reordering changes results.** Two rules that both match a name produce
  different output depending on which runs first. This is why a rule can
  work perfectly in **Live Preview** (which evaluates that rule alone) and
  contribute nothing in **Test Rules** (which runs the whole chain).
- **The trace is the diagnostic.** Both **Test Rules** and the **Apply to
  existing channels** preview list, per name, exactly which rules fired and
  what each one produced. Read them top-down. A rule missing from the trace
  did not match the text as it stood at that point.

## The whole set repeats until nothing changes

After every group and rule has run once, ECM runs the entire set again, and
keeps going until a pass produces no change. There is a ceiling of ten
passes as a loop guard.

This is deliberate: it lets stacked suffixes collapse. `4K/UHD` needs one
pass to strip `UHD` and another to strip the `4K` that is now at the end.
Between passes, runs of whitespace are collapsed to a single space and the
name is trimmed.

Two practical consequences:

- You do not need to write a rule for every combination of suffixes. One
  rule per suffix class is enough; the loop handles the stacking.
- A rule that adds text a preceding rule then removes will oscillate, run
  out of passes, and leave a result that depends on where the loop stopped.
  If you have a rule that puts a token back, make sure nothing earlier
  takes it away again.

## Stop Processing After Match

The rule editor has a **Stop Processing After Match** checkbox. It stops
the **remaining rules in that rule's own group** for the current pass.
Later groups still run, and the outer repeat loop still runs.

Use it for "first match wins" sets: several alternative patterns in one
group where exactly one should apply. Do not reach for it expecting a
global halt, because it is not one.

## Enabled and disabled

Both groups and rules have an enable toggle, and the engine loads only
enabled rules inside enabled groups. Disabling a group is the cleanest way
to take a whole class of transformation out of play temporarily without
losing the rules.

The header shows **Groups Active** and **Rules Active** as
`enabled/total`, plus counts of **Built-in** and **Custom** rules, so you
can see at a glance whether something is switched off.

Two things a disabled group does **not** do:

- It does not rename anything back. Names already stored keep whatever they
  were normalized to. Use
  [Apply to existing channels](apply-to-existing-channels.md) if you want
  stored names recomputed under the new rule set.
- It does not remove itself from Channel Pipeline rules that selected it.
  The selection stays; the group simply contributes nothing while disabled.

## Going deeper

- [How normalization works](concepts.md): the four surfaces, and why a
  Channel Pipeline rule can legitimately produce a different result from
  Test Rules.
- [Condition and action types](condition-and-action-types.md): what an
  individual rule can match and do.
- [When things look wrong](when-things-look-wrong.md): the diagnostic path
  when the trace does not explain the output.
- [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md#rule-groups-and-ordering):
  the reference treatment of ordering and idempotence.
