# Stats v2 history cutover (v0.17.0)

> **Audience:** Operators upgrading to v0.17.0 who want to know what
> happens to historical watch-time and stats data at the cutover.
>
> **TL;DR:** Stats v2 metrics begin on the day you deploy v0.17.0.
> History from before that point is not reconstructable into the new
> view, and the Stats tab's v2 panels will start filling in from zero.

## What changed at v0.17.0

ECM v0.17.0 replaces the per-channel watch-stats aggregate path
(`channel_watch_stats`) with a per-poll observation stream
(`session_telemetry`). The new shape is what every v0.17.0+ stats
feature reads from — watch-time-by-user, provider performance, buffer
events, popularity ranking — and it is the foundation the next round
of Stats v2 features will build on.

## Why there is no backfill

`channel_watch_stats` recorded one lifetime aggregate row per channel
(channel name, watch count, total seconds, last-watched timestamp).
`session_telemetry` records one row per poll per active client per
channel (~one row every 10 seconds for every viewer). The two grains
are not compatible — there is no honest way to derive per-poll
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

- **Before v0.17.0 deploys:** the Stats tab reads from
  `channel_watch_stats`. All your historical data is still there.
- **The day v0.17.0 deploys:** `session_telemetry` starts recording on
  the first stats-poll cycle (default: every 10 seconds).
- **The first week after:** the v2 panels (top-watched, popularity
  ranking, watch-time-by-user) reflect only post-cutover viewing.
  This is expected.
- **Two weeks after and beyond:** the v2 rolling-window views (7-day
  popularity, etc.) reach steady state.

The legacy `channel_watch_stats` rows are not deleted at the cutover
— they remain in the database alongside `session_telemetry` until a
later v0.17.x cleanup retires the table.

## Tracking

- Bead `bd-skqln.3` (single-write refactor + read repointing).
- ADR-007 (retention policy for `session_telemetry`).
