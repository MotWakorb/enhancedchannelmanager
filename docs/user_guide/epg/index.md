# EPG

> **Audience:** Operator configuring electronic programme guide (EPG) data for their channels.
>
> **Status:** Stub — articles below are placeholders.

## Section purpose

Cover the EPG Manager tab and EPG-related settings: adding EPG sources, how ECM matches channels to EPG entries, refresh schedules, and the dummy EPG template engine for channels without upstream EPG data.

## Intended audience

- **Operator** wiring up EPG sources for the first time.
- **Operator** debugging "this channel has the wrong programme listings."
- **Operator** authoring dummy EPG templates for channels Dispatcharr would otherwise show as blank.

End users do not read this section, though the EPG they see in their player is the downstream output of decisions documented here.

## Planned articles

| Article | Purpose |
|-|-|
| `epg-sources.md` | Adding an XMLTV URL or upload, refresh interval, what happens on refresh, what a healthy source looks like. |
| `channel-to-epg-matching.md` | How ECM matches a channel to an EPG entry (TVG-ID + name), why a match fails, how to fix it. Note: matching is *not* the same as normalization — explicit cross-link. |
| `dummy-epg-overview.md` | What dummy EPG is, when to use it, the relationship between dummy EPG and "real" EPG sources. |
| `dummy-epg-templates.md` | Authoring templates in the operator UI, with the template syntax taught at the user level. Defers to `docs/template_engine.md` for the full syntax reference. |
| `troubleshoot-epg.md` | Common EPG issues — wrong listings, blank guide, slow refresh, channel matched to the wrong programme — and how to diagnose. |

## Going deeper

- [`docs/template_engine.md`](../../template_engine.md) — full dummy EPG template syntax reference (placeholders, pipes, conditionals).
- [`docs/api.md`](../../api.md) — the `/epg` router endpoints.
