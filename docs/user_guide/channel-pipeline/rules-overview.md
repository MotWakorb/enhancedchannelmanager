# Channel Pipeline Rules Overview

The Channel Pipeline turns incoming M3U streams into channels automatically,
by running your saved rules against every stream each time it processes. This
page covers what a rule is made of and how to create your first one. For the
condition/action catalogue, see
[Build conditions and actions](conditions-and-actions.md).

## Common tasks

### Create your first Standard rule

1. Open **Channel Pipeline** in the left navigation.

   ![Channel Pipeline landing page showing the Rules table with one existing rule, the Event Sync Review panel above it, and Create Rule / Run buttons in the top right](../../images/user_guide/channel-pipeline/1-pipeline-page.png)

2. Click **Create Rule** (top right).
3. In the **Create Rule** dialog, choose **Standard rule**, conditions plus
   actions: create channels, merge streams, sort, assign. The other option,
   **Event Sync rule**, is a different rule kind covered in
   [Event Sync quick start](event-sync-quickstart.md); don't pick it for a
   general-purpose rule.

   ![Create Rule dialog with two options: Standard rule (conditions + actions) and Event Sync rule (one channel per live event across providers)](../../images/user_guide/channel-pipeline/2-create-rule-chooser.png)

4. Type a **Rule Name**. This is required before the rule can save. Leave
   **Enabled** checked (default) unless you want to build the rule now and
   turn it on later.
5. Work through the three tabs at the top of the dialog: **1 Logic**,
   **2 Targeting**, **3 Output & Run**. Logic is where conditions and actions
   live (see [Build conditions and actions](conditions-and-actions.md)). A
   brand-new rule opens here with an empty Conditions list and an empty
   Actions list.

   ![Empty Logic step showing the Conditions and Actions sections, and the "WHAT THIS RULE WILL DO" live summary panel reading "Add a condition and an action to see what this rule will do."](../../images/user_guide/channel-pipeline/3-logic-step-live-summary.png)

6. Watch the **What this rule will do** panel on the right as you build the
   rule. It updates live in plain English. For example: *"When a stream
   matches 1 condition, create a channel. Orders streams by smart sort.
   Orphaned channels are deleted. Stops after the first matching rule."*
   Use it to sanity-check the rule before you save, not just the individual
   fields.
7. Click **Save**. If a required field is missing (no rule name, an action
   missing a target group, and so on), the dialog stays open and shows the
   specific error inline next to the field. Fix it and click Save again.

**Result:** the rule appears in the **Rules** table with a priority number,
an **ENABLED** status badge, and a **Matches** count (0 until it has run).
Closing the dialog without saving after making changes prompts a "You have
unsaved changes" confirmation, so an accidental close never silently
discards work.

### Understand the rest of the wizard

The Targeting and Output & Run tabs shape *how* a rule's actions behave.
You don't need them for a minimal rule, but know what they control:

| Tab | Controls |
|-|-|
| **2 Targeting** | Merge lookup scope (whether "does a channel with this name already exist?" searches only this rule's target group or every group), manual-channel protection, spacing/case sensitivity in merge matching, and which normalization rule groups apply to channel names. |
| **3 Output & Run** | Channel Sort and Stream Sort (processing/renumbering order, see [Channel Sort vs. Channel Numbering](sort-vs-numbering.md) for the full explanation and the Auto-numbering gotcha), Orphan Cleanup (what happens to channels that stop matching), and the Run behavior toggles: **Run on M3U refresh**, **Stop on first match**, **Skip struck-out streams**. |

**Priority and execution order.** Every rule has a **Priority** number shown
in the Rules table, and by default a rule with **Stop on first match** stops
the *whole* rule set at the first rule that matches a given stream, which
means order matters. Rules can be reordered by dragging the handle in the
table. **Don't reorder rules while a search or filter is active in the
Rules table.** Reordering a filtered subset does not currently preserve the
global priority order correctly for rules hidden by the filter. Clear the
search box and any active filter first, then drag.

**Result:** you know which tab to open for a given change without
re-reading the whole dialog each time.

## Beyond the rule dialog

The **⋮** (more actions) menu next to **Run** on the Channel Pipeline page
has four more operations that act on the whole rule set rather than one
rule: **Dry Run** (preview every enabled rule against live streams, nothing
written), **Import**, **Export**, and **Pipeline Debug Bundle** (a support
bundle you can hand to someone helping you, or run through the rule
analyzer; see [Debugging rules](debugging-rules.md#2-upload-a-debug-bundle-from-bundle-mode)).

## Going deeper

- [Build conditions and actions](conditions-and-actions.md): the condition
  and action catalogue with worked examples.
- [Test a rule before enabling it](test-a-rule.md): the per-rule dry-run
  workflow.
- [Bulk-edit multiple rules](bulk-rule-settings.md): changing a setting
  across many rules at once.
- [Duplicate a rule to reuse it](clone-a-rule.md).
- [Channel Sort vs. Channel Numbering](sort-vs-numbering.md): the Output &
  Run tab's Channel Sort field, explained in full.
- [Debugging rules](debugging-rules.md): when a rule doesn't fire the way
  you expected.
- [Runaway safety cap](runaway-safety-cap.md): the per-run cap on channels
  created, and what "capped" means on a run.
