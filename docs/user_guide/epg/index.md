# EPG

## Section purpose

Cover the EPG Manager page and EPG-related settings: adding EPG sources, how ECM matches channels to EPG entries, refresh schedules, and the dummy EPG template engine for channels without upstream EPG data.

## Start here

| I want to… | Go to |
|-|-|
| Add an XMLTV EPG source and check it's healthy | [Add and refresh EPG sources](epg-sources.md) |
| Connect a paid Schedules Direct account | [Connect a Schedules Direct account](schedules-direct.md) |
| Link my channels to guide data | [Match channels to EPG data](channel-to-epg-matching.md) |
| Fix a channel showing the wrong (or another channel's) listings | [Finding and fixing mis-linked channels](finding-mislinked-channels.md) |
| Generate guide data for channels with no upstream EPG | [Dummy EPG overview](dummy-epg-overview.md) |
| Author a dummy EPG template | [Author dummy EPG templates](dummy-epg-templates.md) |
| Move existing guide assignments to a different source | [Migrate channel guides between IPTV and Gracenote](migrate-guides.md) |
| Diagnose a blank guide, wrong listings, or a stuck refresh | [Troubleshoot EPG issues](troubleshoot-epg.md) |
| Export data before upgrading past the Lookup Tables removal | [Lookup Tables retired](lookup-tables-retired.md) |

## Channel-to-EPG matching

Linking a channel to a guide entry is a separate step from adding a source.
See the full walkthrough, including confidence-score guidance and
timezone/region-aware ranking, at [Match channels to EPG
data](channel-to-epg-matching.md).

## Dummy EPG

For channels with no upstream guide data at all, ECM can generate listings
from channel/stream names instead of pulling them from a provider. See
[Dummy EPG overview](dummy-epg-overview.md) and [Author dummy EPG
templates](dummy-epg-templates.md).

## Articles

| Article | Purpose |
|-|-|
| [`epg-sources.md`](epg-sources.md) | Adding an XMLTV URL, refresh interval, what happens right after you add a source, what a healthy source looks like. |
| [`schedules-direct.md`](schedules-direct.md) | Adding a Schedules Direct account, adding lineups, logo/poster options, rate limits. |
| [`channel-to-epg-matching.md`](channel-to-epg-matching.md) | The Bulk EPG Assignment workflow, reading confidence scores, fixing unmatched channels, and timezone/region-aware ranking. |
| [`dummy-epg-overview.md`](dummy-epg-overview.md) | What dummy EPG is, when to use it, Dummy EPG Profiles vs. the deprecated legacy Dummy EPG Sources path. |
| [`dummy-epg-templates.md`](dummy-epg-templates.md) | Authoring patterns and templates in the profile editor; defers to `docs/template_engine.md` for full syntax. |
| [`troubleshoot-epg.md`](troubleshoot-epg.md) | Common EPG issues (blank guide, wrong listings, slow/stuck refresh, bad bulk matches) and how to diagnose them. |
| [`migrate-guides.md`](migrate-guides.md) | Preview and safely apply IPTV ↔ Gracenote guide assignment migrations using LCN/Gracenote station identifiers. |
| [`finding-mislinked-channels.md`](finding-mislinked-channels.md) | Find & fix channels sharing one EPG row (the West-shows-East mis-link) using the read-only duplicate-link audit. |
| [`lookup-tables-retired.md`](lookup-tables-retired.md) | **Upgrade note.** Lookup Tables and the `{key\|lookup:<table>}` pipe were removed; export any rows before upgrading. |

## Going deeper

- [`docs/template_engine.md`](../../template_engine.md): full dummy EPG template syntax reference (placeholders, pipes, conditionals).
- [`docs/api.md`](../../api.md): the `/epg` router endpoints.
- [`docs/event_sync.md`](../../event_sync.md#automatic-guide-data-for-master-channels-dummy-epg): wiring a Dummy EPG Profile into an Event Sync rule.
