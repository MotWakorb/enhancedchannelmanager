# EPG

> **Audience:** Operator configuring electronic programme guide (EPG) data for their channels.
>
> **Status:** Stub — articles below are placeholders.

## Section purpose

Cover the EPG Manager page and EPG-related settings: adding EPG sources, how ECM matches channels to EPG entries, refresh schedules, and the dummy EPG template engine for channels without upstream EPG data.

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
| `channel-to-epg-matching.md` | How ECM matches a channel to an EPG entry (TVG-ID + name), why a match fails, how to fix it. Note: matching is *not* the same as normalization — explicit cross-link. Timezone/region-aware ranking is covered inline below until this article lands. |
| `dummy-epg-overview.md` | What dummy EPG is, when to use it, the relationship between dummy EPG and "real" EPG sources. |
| `dummy-epg-templates.md` | Authoring templates in the operator UI, with the template syntax taught at the user level. Defers to `docs/template_engine.md` for the full syntax reference. |
| `troubleshoot-epg.md` | Common EPG issues — wrong listings, blank guide, slow refresh, channel matched to the wrong programme — and how to diagnose. |
| [`migrate-guides.md`](migrate-guides.md) | Preview and safely apply IPTV ↔ Gracenote guide assignment migrations using LCN/Gracenote station identifiers. |
| [`finding-mislinked-channels.md`](finding-mislinked-channels.md) | Find & fix channels sharing one EPG row (the West-shows-East mis-link) using the read-only duplicate-link audit. |

## Schedules Direct (SD)

[Schedules Direct](https://www.schedulesdirect.org/) is a paid EPG service
(~$35/year) that provides high-quality guide data for US/Canada and several
other regions. ECM exposes it as an EPG source type alongside XMLTV and dummy
EPG.

**Add an SD source**

1. EPG Manager page → **Add Standard EPG**.
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

## Channel-to-EPG matching

*Full walkthrough is the planned `channel-to-epg-matching.md` article; this is
the timezone/region-awareness slice of it.*

When auto-matching (`match_channels_epg`), the matcher can silently prefer the
wrong regional feed unless it understands timezone. Many guide providers carry
one "default" entry (implicitly East-coast) for a network plus a separately
tagged Pacific row, e.g. `USANetwork(Pacific)(USAP).us`. Without region
awareness a "…West" channel would score the East-coast entry higher than its
own Pacific counterpart — the channel got *a* guide, just the wrong one, three
hours off. This was the root cause behind the [shared-EPG-link
bug](finding-mislinked-channels.md): West feeds silently sharing East's
`epg_data_id` (field report, 2026-07-14).

**Regional equivalences.** These region words are treated as timezone tags on
the *same* network, not separate networks:

| Region word | Region code | Notes |
|-|-|-|
| East | `E` | |
| West | `W` | |
| Pacific | `W` | West and Pacific are the same region — US West-coast feeds run on Pacific time, and guide providers label the West feed "(Pacific)". |
| Central | `C` | Its own region — not aliased to West. |
| Mountain | `M` | Its own region — not aliased to West. |

**How a region is detected**, for both the channel and each EPG candidate:

1. A parenthetical in the `tvg_id` wins first — e.g.
   `USANetwork(Pacific)(USAP).us` → West. Authoritative when present.
2. Otherwise, the *last meaningful word* of the display name, skipping
   trailing quality/format tags (`HD`, `FHD`, `4K`, ...) and bare trailing
   numbers — so `"AMC West HD"` and `"AMC West 2"` both still detect `West`.
3. Only an exact whole-word region token counts. Adjective forms like
   "Eastern" or "Western" (`PBS Eastern`, `Starz Encore Westerns`) are
   deliberately **not** treated as a region tag.

**How region affects ranking.** Region only breaks ties — it never promotes a
worse match over a better one. It's the last tie-break applied, after
confidence, source priority, and exact/token-overlap scoring: among
candidates that are otherwise tied, the entry whose region matches the
channel's wins. A channel with no detected region (ESPN, CNN, most
non-regional networks) is unaffected.

**Example — before and after.** Channel `AMC West`; the EPG source has both
`AMC` (implicitly East) and `AMC (Pacific)`:

- **Before:** `AMC West` matched `AMC`, the East-coast entry — the West/East
  tag was stripped when building the match key and "Pacific" wasn't
  recognized as related, so `AMC (Pacific)` wasn't even a match candidate for
  a short network name like AMC.
- **After:** `AMC (Pacific)` collapses into the same match key as `AMC`,
  making it a genuine candidate; the region-consistency tie-break then ranks
  it above the plain `AMC` entry because West↔Pacific agree. `AMC West` now
  links to `AMC (Pacific)`.

**Checking your fleet.** Run the [duplicate-EPG-link
audit](finding-mislinked-channels.md) (`audit_epg_duplicates` MCP tool /
`GET /api/epg/audit-duplicates`) to find channels still mis-linked — either
left over from before this fix, or a region combination the matcher doesn't
have enough signal to disambiguate automatically.

> **Dev note:** this logic lives in `backend/epg_matching.py` —
> `detect_region()`, the region-collapse step in `epg_match_key()`, and the
> `region_consistency` tie-break in `_sort_matches()`. The separate
> auto-creation matcher (`channel_pipeline_executor._match_epg_data`) now
> reuses `detect_region()` for its own region-consistency tie-break within
> each match tier (bead vznut.4) — one shared source of regional truth for
> both matchers.

## Dummy EPG

Dummy EPG generates programme listings from channel/stream **names** (via regex
patterns and templates) for channels that have no upstream guide data. In ECM
this is managed in the **Dummy EPG Profiles** section at the bottom of
**Operations** → **EPG Manager**: create a profile, then copy its XMLTV URL (or use *Add to
Dispatcharr*) to wire it in as a guide source. Profiles offer live preview,
rich per-state templates, and Event Sync integration.

> **Legacy note.** ECM previously also exposed Dispatcharr's native
> `source_type=dummy` EPG sources through a separate "Dummy EPG Sources"
> section. That path is **deprecated**: the section now appears only if such
> sources already exist on your instance, and it no longer lets you create new
> ones. Existing legacy sources keep working and stay editable — nothing is
> removed — but new dummy EPG should be authored as a **Dummy EPG Profile**.

## Going deeper

- [`docs/template_engine.md`](../../template_engine.md) — full dummy EPG template syntax reference (placeholders, pipes, conditionals).
- [`docs/api.md`](../../api.md) — the `/epg` router endpoints.
