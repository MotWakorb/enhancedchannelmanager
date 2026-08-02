# Stats v2 history cutover (v0.17.0)

> **TL;DR:** Stats v2 metrics begin on the day you deploy v0.17.0.
> History from before that point is not reconstructable into the new
> view, and the Stats page's v2 panels will start filling in from zero.

## What changed at v0.17.0

ECM v0.17.0 replaces the per-channel watch-stats aggregate path
(`channel_watch_stats`) with a per-poll observation stream
(`session_telemetry`). The new shape is what every v0.17.0+ stats
feature reads from (watch-time-by-user, provider performance, buffer
events, popularity ranking), and it is the foundation the next round
of Stats v2 features will build on.

## Why there is no backfill

`channel_watch_stats` recorded one lifetime aggregate row per channel
(channel name, watch count, total seconds, last-watched timestamp).
`session_telemetry` records one row per poll per active client per
channel (~one row every 10 seconds for every viewer). The two grains
are not compatible. There is no honest way to derive per-poll
observations from a lifetime aggregate.

We considered synthesizing rows or running a UNION-of-shapes
transition window. Both were rejected: synthesized rows would be
fabricated data that downstream features (popularity ranking, GH-62
watch-time-by-user) cannot distinguish from real observations, and a
UNION window doubles read cost on every query with no natural close.

The full DBA reasoning lives in
[`docs/database_migrations.md` → "Backfill policy for
session_telemetry"](../../database_migrations.md#backfill-policy-for-session_telemetry).

## What you will see

- **Before v0.17.0 deploys:** the Stats page reads from
  `channel_watch_stats`. All your historical data is still there.
- **The day v0.17.0 deploys:** `session_telemetry` starts recording on
  the first stats-poll cycle (default: every 10 seconds).
- **The first week after:** the v2 panels (top-watched, popularity
  ranking, watch-time-by-user) reflect only post-cutover viewing.
  This is expected.
- **Two weeks after and beyond:** the v2 rolling-window views (7-day
  popularity, etc.) reach steady state.

The legacy `channel_watch_stats` rows are not deleted at the cutover.
They remain in the database alongside `session_telemetry` until a
later v0.17.x cleanup retires the table.

## How to opt out of Stats v2 entirely

Stats v2 collects per-poll observations (one row per active viewer per
channel per poll, default every 10 seconds) so the new Stats panels
(top-watched, popularity ranking, watch-time-by-user, buffering events
by provider) have real data to render. If you are running ECM in a
household where this level of viewing-history detail is undesirable, or
your security posture forbids retaining per-session metadata at all,
you can disable the entire Stats v2 data path with one environment
variable.

**Set `ECM_STATS_TELEMETRY_OPT_OUT=true` on the ECM container.** Any of
the values `true`, `1`, `yes`, `on` (case-insensitive) enables the
opt-out; anything else (including unset, the default) leaves Stats v2
recording normally.

```yaml
# Example docker-compose.yml
services:
  ecm:
    image: enhancedchannelmanager:latest
    environment:
      ECM_STATS_TELEMETRY_OPT_OUT: "true"
```

When the opt-out is enabled:

- Zero rows land in `session_telemetry`. The table stays empty (or
  retains only pre-opt-out rows if you flip the flag mid-run).
- The provider resolver does **not** call Dispatcharr's streams
  endpoint each poll (one Dispatcharr API round-trip saved).
- The buffer-event ingest does **not** call Dispatcharr's
  system-events endpoint each poll (one more round-trip saved).
- On startup, ECM logs one line so you can confirm the flag is live:
  `[STATS_V2] telemetry opt-out is ENABLED — no session_telemetry
  data will be collected`. Grep for that string in `docker logs
  ecm-ecm-1` if you're not sure whether the flag was picked up.

What still happens with the opt-out on:

- All **legacy** stats keep recording: bandwidth totals
  (`BandwidthDaily`), per-channel bandwidth (`ChannelBandwidth`),
  unique-client connections (`UniqueClientConnection`). These pre-date
  Stats v2 and are not part of the opt-out surface. The original
  Stats page panels (bandwidth chart, total clients, peak bitrate) keep
  working.
- The Stats page's v2 panels (top-watched, popularity ranking,
  watch-time-by-user, buffering events by provider) show no data
  because there is no data to show. The panels render an empty state
  rather than erroring.
- The frontend error-telemetry opt-out
  (`telemetry_client_errors_enabled` in Settings, documented in
  [Error Telemetry & Opt-out](../error-telemetry-opt-out.md)) is
  independent. Flipping one does not affect the other.

The flag is read on every poll cycle, so flipping it at runtime takes
effect within one poll interval (default 10 seconds) without a
container restart. If you want the opt-out to survive container
restarts, set the env var in your compose/run command so it persists
across restarts.

> **Tracking:** bead `bd-tp1pd` (operator-global telemetry opt-out
> toggle). The Stats v2 epic is `bd-skqln`.

## Excluding a specific viewer (`ECM_TELEMETRY_EXCLUDE_USERS`): deprecated

> **Deprecated; scheduled for removal in v0.18.0.** You no longer need this
> env var for its original purpose. It still works for now. No action
> required if you rely on it for an unrelated reason.

`ECM_TELEMETRY_EXCLUDE_USERS` was introduced as a workaround for a
namespace-collision bug: ECM's local `users` table (ECM login identities)
and Dispatcharr's `users` table (stream viewers) have separate but
coincidentally-overlapping integer IDs, so a Dispatcharr viewer could be
mis-attributed to an ECM login (for example, the admin account) in the
Stats v2 Watch Time panel. The env var let operators suppress the
mis-attributed rows by username or Dispatcharr user-id.

The architectural fix (migration `0011`, which adds
`session_telemetry.dispatcharr_username` and drops the foreign key to ECM's
`users` table) removes that collision at the source. Telemetry rows now
carry the Dispatcharr-side username directly, so the panel no longer
attributes a viewer's sessions to an ECM login. **The env var is therefore
no longer needed for the namespace-collision use case.**

It remains available, for now, if you want to exclude a specific Dispatcharr
viewer from Stats v2 for any *other* reason. When the var is set, ECM logs a
one-time deprecation warning at the first stats poll:

```
[BANDWIDTH] ECM_TELEMETRY_EXCLUDE_USERS is set (N token(s)) but is DEPRECATED: … will be REMOVED in v0.18.0.
```

If you no longer need the exclusion, unset the var. If you still rely on it,
no action is required before v0.18.0. The removal is tracked under bead
`bd-lbpl7`.

## Tracking

- Bead `bd-skqln.3` (single-write refactor + read repointing).
- Bead `bd-tp1pd` (operator-facing Stats v2 telemetry opt-out: the
  `ECM_STATS_TELEMETRY_OPT_OUT` env var documented above).
- Bead `bd-gsn3r` (namespace-collision architectural fix: migration 0011
  adds `session_telemetry.dispatcharr_username`).
- Bead `bd-ye075` (soft-deprecation of `ECM_TELEMETRY_EXCLUDE_USERS`).
- Bead `bd-lbpl7` (v0.18.0 removal of `ECM_TELEMETRY_EXCLUDE_USERS`).
- ADR-007 (retention policy for `session_telemetry`).
