# Dummy EPG overview

A real EPG source (see [Add and refresh EPG sources](epg-sources.md)) gives
you programme listings someone else compiled. **Dummy EPG** is the
opposite: ECM *generates* listings by parsing your own channel or stream
**names** with regex patterns and rendering the extracted pieces into a
title/description template, with no upstream provider involved.

## When to use it

Reach for dummy EPG when a channel's real guide data will never exist
upstream, not as a substitute for matching a channel that *does* have real
guide data available. Always try [matching](channel-to-epg-matching.md)
against a real source first for anything a provider actually carries.
Typical candidates:

- Sports/PPV or event channels whose "programme" is really just a stream
  title like `Flo Racing 02: FLORACING 002 | 2026 USAC INDIANA SPRINT WEEK…`.
- Auto-created master channels from the Channel Pipeline's Event Sync
  feature, which by design have no upstream guide (see [Automatic guide
  data for master channels](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/event_sync.md#automatic-guide-data-for-master-channels-dummy-epg)).
- Any 24/7 or filler channel where "what's on" is either constant or
  meaningless, and a simple `{channel}` title is enough.

## Dummy EPG Profiles vs. the legacy Dummy EPG Sources

Dummy EPG is managed as a **Dummy EPG Profile**, in the **Dummy EPG
Profiles** section at the bottom of **EPG Manager**. Create a profile,
then either copy its XMLTV URL into Dispatcharr yourself or use **Add to
Dispatcharr as EPG source** to wire it in automatically. Profiles offer a
live preview, rich per-state templates (see [Author dummy EPG
templates](dummy-epg-templates.md)), and Event Sync integration.

> **Legacy note.** ECM previously also exposed Dispatcharr's native
> `source_type=dummy` EPG sources through a separate "Dummy EPG Sources"
> section. That path is **deprecated**: it now appears only if such sources
> already exist on your instance, and it no longer lets you create new
> ones. Existing legacy sources keep working and stay editable (nothing is
> removed), but any new dummy EPG should be authored as a Dummy EPG
> Profile.

## What a profile actually produces

A profile watches the channels in its selected **channel groups**, applies
its patterns to each channel's (or stream's) name to pull out variables
like `{team1}`, `{sport}`, or `{starttime}`, then renders those variables
into your **Title Template** and **Description Template** to produce XMLTV
programme entries. Separate templates for the **Upcoming** (before the
event) and **Ended** (after the event) states let the guide read
differently depending on where you are relative to the event. See [Author
dummy EPG templates](dummy-epg-templates.md) for the actual authoring
workflow.

## Going deeper

- [Author dummy EPG templates](dummy-epg-templates.md): the operator-level
  walkthrough for building patterns and templates in the profile editor.
- [`docs/template_engine.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/template_engine.md): the full template
  syntax reference (placeholders, pipes, conditionals) that the profile
  editor's fields accept.
- [Lookup Tables retired](lookup-tables-retired.md): if you're upgrading
  an older profile that used the `{key|lookup:<table>}` pipe, read this
  first; the feature and pipe are gone.
- [Automatic guide data for master channels](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/event_sync.md#automatic-guide-data-for-master-channels-dummy-epg):
  wiring a profile into an Event Sync rule so new auto-created channels
  get guide data on every run.
- [Add and refresh EPG sources](epg-sources.md): for channels that *do*
  have real upstream guide data, match against a real source instead.
