# Stats

> **Audience:** Operator wanting visibility into what ECM is doing — how many channels, how many streams, recent task activity, error rates.
>
> **Status:** Placeholder. The Stats tab today shows a basic v1 view. The full operator-facing documentation lands with the v0.17.0 Stats v2 work (bd-skqln.9), which will add a metric glossary and an interpretation guide.

## Section purpose (planned)

Once Stats v2 ships, this section will document:

- What every metric on the Stats tab means in operator language.
- The difference between metrics that count things (channels, streams) and metrics that measure rates (task completions per minute, errors per hour).
- How to read the Stats tab during normal operation vs. during an incident.
- Cross-links to the SLO framing for operators curious about how reliability targets are set.

## Intended audience

- **Operator** doing routine "is everything healthy?" checks.
- **Operator** investigating a slowdown or surge.

End users do not read this section.

## Planned articles (post-v0.17.0)

| Article | Purpose |
|-|-|
| `stats-tab-overview.md` | Tour of the Stats tab as it ships in v0.17.0. |
| `metric-glossary.md` | One entry per metric: name, definition, units, what causes it to move. |
| `interpretation-guide.md` | "What does it mean when X is Y?" — common patterns and what they indicate. |
| `stats-vs-slos.md` | How the operator-facing Stats relate to the SRE-facing SLOs in `docs/sre/slos.md`. |

## Going deeper (for now)

- [`docs/sre/slos.md`](../../sre/slos.md) — the SLO definitions ECM is measured against. Today this is the closest thing to an operator-facing reliability reference.
- The `/stats` and `/stream-stats` API routes (see [`docs/api.md`](../../api.md)) — what the Stats tab consumes under the hood.

## Tracking

- bd-skqln.9 — *Stats v2: user guide entry + metric glossary* — fills in this section.
