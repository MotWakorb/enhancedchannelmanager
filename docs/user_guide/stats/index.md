# Stats

## Start here: per-panel walkthroughs

The Stats page is a stack of independent panels. Each walkthrough below
is framed around the operator question that panel answers, not just
what it displays:

| I want to know… | Go to |
|-|-|
| What's streaming right now, and which channels get watched the most? | [Overview, Top Watched, and Channel Drill-Down](overview-top-watched.md) |
| Where is my bandwidth going? | [Bandwidth](bandwidth.md) |
| How many distinct viewers are there, and which channels use the most bandwidth? | [Enhanced Statistics](enhanced-statistics.md) |
| Which channels are trending up or down? | [Popularity](popularity.md) |
| Who watched what, and when, session by session? | [Watch History](watch-history.md) |
| Who's watching the most, in total? | [Users panel](users-panel.md) |
| Is a specific provider degrading, or how much of its catalog do I use? | [Providers](providers.md) |
| What does this specific number mean? | [Metric glossary](metric-glossary.md) |

## Stats v2 (v0.17.0)

ECM v0.17.0 introduced the Stats v2 feature set: a new data pipeline (`session_telemetry`) that powers the Users panel and Providers panel on the Stats page.

### Users panel

- **[Users panel](users-panel.md)**: what the Users panel shows, who can access it (admin-only), how watch-time is computed from poll observations, and what to expect in the days after a fresh install or upgrade.

### Metric glossary

- **[Metric glossary](metric-glossary.md)**: precise definitions for every Stats v2 number, including `total_watch_seconds`, `session_count`, `last_watched`, `buffer_event_count`, `provider_id` (and the "Unknown" bucket), `bytes_delta`, and `bitrate_bps`. Start here if a number in the UI is not what you expected.

### History cutover note

- **[Stats v2 history cutover](stats-v2-history-cutover.md)**: what happens to historical watch-stats data at the v0.17.0 cutover. Short version: Stats v2 metrics begin on the day v0.17.0 deploys; prior history is not reconstructable into the new view.

---

## Section purpose

This section documents the Stats page for operators:

- What every metric on the Stats page means in operator language.
- The difference between metrics that count things (channels, streams) and metrics that measure rates (task completions per minute, errors per hour).
- How to read the Stats page during normal operation vs. during an incident.
- Cross-links to the SLO framing for operators curious about how reliability targets are set.

## Articles

| Article | Purpose |
|-|-|
| `overview-top-watched.md` | Live-counts header, Active Channels, Recent Events, Top Watched Channels, and the per-channel drill-down modal: the "what's streaming right now" tour that replaces the earlier planned `stats-tab-overview.md`. |
| `bandwidth.md` | Bandwidth Usage (headline totals + 7-day chart) and Bandwidth In/Out (inbound vs. outbound, peak bitrates), both part of ECM's legacy bandwidth pipeline, unaffected by the Stats v2 telemetry opt-out. |
| `enhanced-statistics.md` | The Enhanced Statistics panel: 7-day unique-viewer counts and per-channel bandwidth/connections/watch-time ranking. |
| `popularity.md` | Popularity Rankings and Trending: how the score is calculated and refreshed. |
| `watch-history.md` | Session-by-session watch log with time/channel/IP filters. |
| `metric-glossary.md` | One entry per metric: name, definition, units, what causes it to move. |
| `users-panel.md` | Operator guide to the Users panel (admin-only). |
| `providers.md` | The admin-only Providers telemetry panel (buffering, watch time, top channels, bitrate, per provider) and the non-admin Provider Stream Usage catalog table. |
| `interpretation-guide.md` | "What does it mean when X is Y?" (common patterns and what they indicate). |
| `stats-vs-slos.md` | How the operator-facing Stats relate to the SRE-facing SLOs in `docs/sre/slos.md`. |

## Going deeper

- [`docs/sre/slos.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/sre/slos.md): the SLO definitions ECM is measured against.
- [`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md): the API reference for the Stats v2 panels, useful if you want to query the data programmatically instead of through the UI.
