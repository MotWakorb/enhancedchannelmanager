# Stats — Users Panel

> **Audience:** ECM operator. Requires Dispatcharr authentication with an **admin** account.
>
> **v0.17.0 and later.** The Users panel is part of the Stats v2 feature set introduced in v0.17.0.

The Users panel is the fifth panel on the Stats tab. It shows watch-time totals for every Dispatcharr user in your installation. Use it to understand which users watch the most, which channels they watch, and when they last tuned in.

---

## Who can see it

The Users panel is **admin-only**. If you open the Stats tab while logged in as a non-admin user you will see an "admin access required" notice in the panel area. Log out and back in with an admin account to access watch-time data.

When global authentication is disabled (no auth required to access ECM), the panel behaves as admin — all watch-time data is visible.

> **TODO: screenshot placeholder** — Users panel "admin access required" notice (non-admin view). Awaiting container deployment refresh.

---

## What it shows

### Per-user watch totals

The top section lists every Dispatcharr user ECM has observed watching a channel, ranked by total watch time (highest first).

| Column | What it is |
|---|---|
| User | Dispatcharr username resolved from the user's ID |
| Total watch time | Sum of all poll intervals where ECM saw this user streaming any channel, converted to seconds (see [metric glossary](metric-glossary.md#total_watch_seconds)) |
| Last watched | The most recent timestamp ECM recorded a polling observation for this user |

> **TODO: screenshot placeholder** — per-user watch totals table. Awaiting container deployment refresh.

### Per-user channel breakdown

Clicking a user row (or navigating to the per-user detail view) shows a channel-level breakdown:

| Column | What it is |
|---|---|
| Channel | Channel name, resolved from Dispatcharr |
| Watch time | Total seconds this user spent on this channel in the selected date range |
| Times watched | Count of distinct viewing sessions for this channel (see [metric glossary](metric-glossary.md#session_count)) |
| Last watched | Most recent poll observation for this (user, channel) pair |

> **TODO: screenshot placeholder** — per-user channel breakdown. Awaiting container deployment refresh.

### Date-range selector

Both views support a date-range filter. The available windows are:

- **7 days** — last 7 days of data (default)
- **30 days** — last 30 days
- **90 days** — last 90 days

Selecting a range restricts all rows to observations within that window. Watch time, times watched, and last-watched all reflect only observations inside the chosen range.

> **Raw data retention is 30 days.** Observations older than 30 days are pruned from the database per the ADR-007 retention policy. For 90-day queries, data older than 30 days is served from the daily rollup table, not raw rows. In practice, 7-day and 30-day queries feel identical; a 90-day query on an installation younger than 90 days will show zeros for the missing history. See [stats-v2-history-cutover.md](stats-v2-history-cutover.md) for the "metrics start on deploy day" caveat.

> **TODO: screenshot placeholder** — date-range selector UI. Awaiting container deployment refresh.

---

## Where this data comes from

### Polling cadence

ECM's `BandwidthTracker` polls Dispatcharr for active channel activity on a fixed interval — **10 seconds by default** (configurable via `stats_poll_interval` in Settings). Each time a user is seen streaming a channel during a poll, ECM writes one row to the `session_telemetry` table recording:

- The user's ID
- The channel being watched
- The poll timestamp (`observed_at`)
- The poll interval in milliseconds (`poll_interval_ms`)
- Bytes transferred during that poll cycle (`bytes_delta`)
- Any buffering events Dispatcharr reported (`buffer_event_count`)
- The provider ID for the active stream (`provider_id`)

Watch time is derived from these rows: **watch time = sum of `poll_interval_ms` for all (user, channel, poll-tick) tuples in the date range, divided by 1000**.

### Multi-client collapse

If the same user has multiple active sessions on the same channel in the same poll cycle — for example, two browser tabs open simultaneously — ECM counts that as **one poll interval**, not two. This prevents overcounting: a user watching from two devices is counted once per tick, not twice.

The technical mechanism is a `DISTINCT (user_id, channel_id, observed_at)` aggregation that collapses concurrent sessions to a single observation per tick before summing.

### Anonymous traffic

Poll observations where no Dispatcharr user was identified (no auth, or an Dispatcharr instance without user tracking) are **excluded** from the Users panel entirely. Anonymous traffic has no meaningful user row to report.

---

## Known caveats and limitations

**Metrics start on deploy day.** If you upgraded from a version before v0.17.0, watch-time history from before the upgrade is not available in this panel. See [stats-v2-history-cutover.md](stats-v2-history-cutover.md) for why this is and what to expect in the first days after upgrading.

**Usernames require Dispatcharr auth.** ECM resolves usernames from Dispatcharr's user API. If a user was deleted from Dispatcharr, their rows remain in ECM's database (with a null `user_id` after the FK cascade) and may appear as unknown or be excluded, depending on when the deletion occurred relative to the last poll.

**Watch time reflects streaming, not viewing.** A stream is counted as "watched" as long as Dispatcharr reports an active client connection. If a client is paused but the stream remains open, that time is counted. If a client disconnects and reconnects, each connection window is counted independently.

**Session count is informational.** The "times watched" figure counts distinct `session_id` values per channel per date range. It is not derived from the legacy watch-count state-transition counter (which tracked channel-start events). See [metric glossary](metric-glossary.md#session_count) for the distinction.

---

## Going deeper

- [Metric glossary](metric-glossary.md) — precise definition of every number shown in this panel.
- [Stats v2 history cutover](stats-v2-history-cutover.md) — why data starts from the v0.17.0 deploy date and not before.
- [ADR-007: session_telemetry retention policy](../../adr/ADR-007-session-telemetry-retention.md) — the 30-day raw retention and rollup design.
- [`docs/api.md`](../../api.md) — the `/api/stats/watch-time` and `/api/stats/watch-time/{user_id}` endpoints that back this panel.
