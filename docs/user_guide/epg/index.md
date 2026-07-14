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
| `schedules-direct.md` | Adding a Schedules Direct account, managing lineups, logo/poster options. (See the inline section below until this article lands.) |
| `channel-to-epg-matching.md` | How ECM matches a channel to an EPG entry (TVG-ID + name), why a match fails, how to fix it. Note: matching is *not* the same as normalization — explicit cross-link. |
| `dummy-epg-overview.md` | What dummy EPG is, when to use it, the relationship between dummy EPG and "real" EPG sources. |
| `dummy-epg-templates.md` | Authoring templates in the operator UI, with the template syntax taught at the user level. Defers to `docs/template_engine.md` for the full syntax reference. |
| `troubleshoot-epg.md` | Common EPG issues — wrong listings, blank guide, slow refresh, channel matched to the wrong programme — and how to diagnose. |
| [`finding-mislinked-channels.md`](finding-mislinked-channels.md) | Find & fix channels sharing one EPG row (the West-shows-East mis-link) using the read-only duplicate-link audit. |

## Schedules Direct (SD)

[Schedules Direct](https://www.schedulesdirect.org/) is a paid EPG service
(~$35/year) that provides high-quality guide data for US/Canada and several
other regions. ECM exposes it as an EPG source type alongside XMLTV and dummy
EPG.

**Add an SD source**

1. EPG Manager tab → **Add Standard EPG**.
2. Set **Source Type** → *Schedules Direct*.
3. Enter your SD **Username** and **Password**, then save.
   - On later edits, leave the password field blank to keep the stored one — it
     is never displayed back.

**Add lineups** (required — an SD source with no lineups pulls no data)

After saving, reopen the source. A **Lineups** panel appears:

1. Pick your **Country** and enter a **Postal Code**, then **Search**.
2. Click **Add** next to the lineup that matches your provider.
3. SD limits you to **4 active lineups** and a small number of lineup changes per
   day (the panel shows how many you have left). Choose carefully.

**Logo & poster options**

- **Station Logo Style** (dark/white/gray/light) picks the variant SD serves for
  channel logos.
- **Auto-apply EPG logos to channels** stamps SD station logos onto matched
  channels on refresh.
- **Fetch program posters** pulls per-programme artwork (costs extra SD API
  requests; off by default).

**Refresh**

SD enforces rate limits, so the refresh interval has a **2-hour minimum** (24
hours recommended). After adding lineups, run a refresh, then use
**channel-to-EPG matching** to link your channels to SD stations.

## Going deeper

- [`docs/template_engine.md`](../../template_engine.md) — full dummy EPG template syntax reference (placeholders, pipes, conditionals).
- [`docs/api.md`](../../api.md) — the `/epg` router endpoints.
