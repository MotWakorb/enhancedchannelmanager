# Stats

> **Audience:** Operator wanting visibility into what ECM is doing — how many channels, how many streams, recent task activity, error rates.

## Stats v2 (v0.17.0)

ECM v0.17.0 introduced the Stats v2 feature set: a new data pipeline (`session_telemetry`) that powers the Users panel and Providers panel on the Stats tab.

### Users panel

- **[Users panel](users-panel.md)** — what the Users panel shows, who can access it (admin-only), how watch-time is computed from poll observations, and what to expect in the days after a fresh install or upgrade.

### Metric glossary

- **[Metric glossary](metric-glossary.md)** — precise definition of every Stats v2 number: `total_watch_seconds`, `session_count`, `last_watched`, `buffer_event_count`, `provider_id` (and the "Unknown" bucket), `bytes_delta`, and `bitrate_bps`. Start here if a number in the UI is not what you expected.

### History cutover note

- **[Stats v2 history cutover](stats-v2-history-cutover.md)** — what happens to historical watch-stats data at the v0.17.0 cutover. Short version: Stats v2 metrics begin on the day v0.17.0 deploys; prior history is not reconstructable into the new view.

---

## Section purpose

This section documents the Stats tab for operators:

- What every metric on the Stats tab means in operator language.
- The difference between metrics that count things (channels, streams) and metrics that measure rates (task completions per minute, errors per hour).
- How to read the Stats tab during normal operation vs. during an incident.
- Cross-links to the SLO framing for operators curious about how reliability targets are set.

## Intended audience

- **Operator** doing routine "is everything healthy?" checks.
- **Operator** investigating a slowdown or surge.

End users do not read this section.

## Planned articles

| Article | Status | Purpose |
|-|-|-|
| `stats-tab-overview.md` | Planned | Tour of the Stats tab as it ships in v0.17.0. |
| `metric-glossary.md` | **Done** | One entry per metric: name, definition, units, what causes it to move. |
| `users-panel.md` | **Done** | Operator guide to the Users panel (admin-only). |
| `interpretation-guide.md` | Planned | "What does it mean when X is Y?" — common patterns and what they indicate. |
| `stats-vs-slos.md` | Planned | How the operator-facing Stats relate to the SRE-facing SLOs in `docs/sre/slos.md`. |

## Going deeper

- [`docs/sre/slos.md`](../../sre/slos.md) — the SLO definitions ECM is measured against.
- The `/api/stats/watch-time` and `/api/stats/providers/*` API routes (see [`docs/api.md`](../../api.md)) — what the Stats v2 panels consume under the hood.
- [ADR-007: session_telemetry retention policy](../../adr/ADR-007-session-telemetry-retention.md) — the 30-day raw retention and rollup design.

## Tracking

- bd-skqln.9 — *Stats v2: user guide entry + metric glossary* — delivered this section.
