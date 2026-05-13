# Stats v2 Metric Glossary

> **Audience:** ECM operator. Plain-language definitions for every number shown in the Stats v2 panels (Users panel, Providers panel).
>
> **v0.17.0 and later.** These metrics are produced by the `session_telemetry` data pipeline introduced in v0.17.0.

This glossary defines every metric that appears in the Stats v2 panels. Each entry includes what the number means, how it is computed, what units it is in, and what causes it to change — so you know whether a number moving up or down is expected.

All Stats v2 data originates from `session_telemetry`, a table ECM writes once per poll cycle per active viewing session. The default poll cadence is **10 seconds**. If you have changed `stats_poll_interval` in Settings, every "10 seconds" reference below reflects your configured value instead.

---

## total_watch_seconds

**What it is:** The total time, in seconds, that a user (or provider) was observed streaming content within the selected date range.

**How it is computed:**

1. Collect all `session_telemetry` rows for the user/provider in the date range.
2. Apply a multi-client collapse: for any (user, channel, poll-tick) tuple where multiple concurrent sessions exist, count that tick **once**, not once per session. This uses `MAX(poll_interval_ms)` within the group — taking the longest reported interval for that tick rather than summing across clients.
3. Sum the resulting `poll_interval_ms` values.
4. Divide by 1000 to convert milliseconds to seconds. The result is truncated to an integer.

**Units:** Seconds (integer).

**What causes it to change:** Every 10-second poll where a user is actively streaming adds one poll interval (typically `10000` ms = 10 s) to this total. Changing the poll interval changes how quickly watch time accumulates. Watch time only counts while a stream connection is open in Dispatcharr — pausing playback at the media-player level does not reduce it if the stream connection stays open.

**Important:** This is a polling-based estimate, not a precise duration. A user who connects mid-poll and disconnects mid-poll will have fractional seconds unaccounted for at each edge. At 10-second resolution, the typical error per session is up to 10 seconds. Over many sessions this error averages out.

---

## session_count

**What it is:** The count of distinct viewing sessions a user has had on a channel within the selected date range.

**How it is computed:**

`COUNT(DISTINCT session_id)` across `session_telemetry` rows, filtered by user and channel for the date range. Each continuous viewing session carries a unique `session_id`; when a user reconnects to a channel, a new `session_id` is assigned.

**Units:** Integer (count of sessions).

**What causes it to change:** Increases by 1 each time a user starts a new viewing session on the channel within the date range. Reconnects (e.g., after a network drop) create a new session.

**Important distinction:** `session_count` is **not** the legacy "watch count" metric from ECM versions before v0.17.0. The legacy counter was incremented on Dispatcharr channel-start events (state transitions). `session_count` is derived from the per-poll `session_id` values in `session_telemetry` — a different and more reliable source. The two will agree in most cases but are not guaranteed to match exactly. `session_count` is labeled "Times watched" in the UI.

---

## last_watched

**What it is:** The most recent timestamp ECM recorded a polling observation for a user on a channel.

**How it is computed:**

`MAX(observed_at)` across `session_telemetry` rows for the (user, channel) pair, within the selected date range. `observed_at` is stored as milliseconds since Unix epoch and displayed in the UI as an ISO-8601 UTC timestamp.

**Units:** Timestamp (ISO-8601 UTC, displayed in your local timezone in the UI).

**What causes it to change:** Updates every time ECM polls and finds the user actively streaming that channel. The maximum is recalculated within the selected date range — changing the date range may show an earlier "last watched" if the most recent observation falls outside the range.

**Note:** "Last watched" tells you the most recent poll tick ECM observed the user on this channel. If a user stops watching between poll ticks, the last observation will be up to 10 seconds before they actually stopped.

---

## buffer_event_count

**What it is:** The count of buffering events Dispatcharr reported for a channel during a poll cycle, attributed to a provider.

**How it is computed:**

Dispatcharr reports buffering events (event type `buffering`) as part of its channel-stats payload. ECM stores the count reported during each poll cycle as `buffer_event_count` on the `session_telemetry` row for that (provider, channel, poll-tick). In the Providers panel, these are aggregated as `SUM(buffer_event_count)` per provider per time bucket.

**Units:** Integer (count of buffering events per time bucket, as aggregated in the Providers panel).

**What causes it to change:** A non-zero value in a time bucket means Dispatcharr reported at least one buffering event for at least one channel served by that provider during that period. High values on a provider over time suggest the provider has reliability or capacity problems. Zero values indicate no buffering was reported — which may mean genuinely smooth delivery or may reflect that Dispatcharr did not surface any events.

**Note:** Buffer event attribution depends on `provider_id` resolution (see below). If provider resolution fails for a channel during a poll, the buffer events for that channel are attributed to the "Unknown" provider bucket.

---

## provider_id

**What it is:** The M3U provider (account) responsible for the stream ECM observed on a channel during a poll cycle.

**How it is determined:**

Each time ECM polls for active channel activity, it looks up the active stream on each channel via the Dispatcharr API and resolves the stream's `m3u_account_id`. This integer ID is stored as `provider_id` on the `session_telemetry` row.

**"Unknown" bucket:** When provider resolution fails — because the channel had no active stream ID, the Dispatcharr lookup returned an error, the stream record had no M3U account, or the stream's M3U account was deleted — `provider_id` is stored as `NULL`. In the Providers panel, `NULL` provider rows are displayed as an **"Unknown"** bucket. The Unknown bucket is always shown explicitly, not silently dropped, so you can see the size of the attribution gap.

**Important:** `provider_id` is the Dispatcharr `m3u_account_id` (an integer), **not** the M3U account display name. ECM stores the ID, not the name. The Providers panel resolves the display name from Dispatcharr at query time. If you rename a provider in Dispatcharr, historical data still carries the original ID, which Dispatcharr will resolve to the new name correctly — the ID is stable; the name is not.

**What causes the Unknown bucket to grow:** Provider resolution is best-effort and happens once per poll cycle per channel. Transient Dispatcharr connectivity issues, channels that lose their active stream mid-poll, or orphaned streams whose M3U account was deleted all produce `NULL` entries. A sustained high Unknown rate is a signal to check Dispatcharr connectivity or investigate orphaned streams.

---

## bytes_delta

**What it is:** The number of bytes ECM observed being streamed during a single poll cycle for one session.

**How it is computed:**

Derived from Dispatcharr's per-client stats at poll time. ECM records the bytes-transferred delta (the increase since the last poll) rather than a cumulative total, so each `session_telemetry` row reflects only what was transferred in that 10-second window.

**Units:** Bytes (integer, non-negative).

**What causes it to change:** Increases proportionally to the stream's bitrate and the poll interval. A 10 Mbps stream over a 10-second poll produces approximately `10,000,000 / 8 = 1,250,000` bytes per poll (1.25 MB). Buffering, network stalls, or paused streams reduce it. Zero bytes in a poll cycle may indicate a stalled or very slow stream.

**Usage:** `bytes_delta` is not displayed directly in the UI. It feeds the **bitrate** calculation in the Providers panel and the **channel-heatmap** visualization (which shows total bytes transferred per provider-channel pair over the selected window).

---

## bitrate_bps

**What it is:** The derived average bitrate for a provider over a time bucket, in bits per second.

**How it is computed:**

```
bitrate_bps = SUM(bytes_delta) * 8 * 1000 / SUM(poll_interval_ms)
```

Per `(provider_id, time_bucket)` aggregate, after applying the multi-client collapse (one row per `(provider, channel, poll-tick)` tuple, not per session). The formula:

- `SUM(bytes_delta)` — total bytes transferred across all (provider, channel, poll-tick) tuples in the bucket
- `* 8` — converts bytes to bits
- `* 1000` — adjusts for the `poll_interval_ms` denominator being in milliseconds rather than seconds
- `/ SUM(poll_interval_ms)` — divides by total milliseconds of streaming time in the bucket

The result is truncated to an integer. Buckets where `SUM(poll_interval_ms) == 0` are skipped (no streaming time to divide by).

**Units:** Bits per second (bps), integer.

**What causes it to change:** Reflects the actual throughput Dispatcharr was delivering for streams on this provider during the bucket. A rising bitrate on a provider suggests higher-quality streams or more concurrent viewers. A falling bitrate may indicate congestion, stream quality degradation, or reduced viewership.

**Note:** `bitrate_bps` is a derived metric — it is not stored in `session_telemetry`. ECM computes it at query time from `bytes_delta` and `poll_interval_ms`. This avoids storing a pre-computed value that might disagree with the underlying components.

---

## Data horizon and retention

ECM retains raw `session_telemetry` rows for **30 days** from the observation date (per ADR-007). Queries for the 7-day and 30-day windows read raw rows. Queries for the 90-day window read from daily pre-aggregated rollup tables for the portion beyond 30 days.

After a fresh v0.17.0 install or upgrade:

- The **7-day window** has meaningful data after 7 days.
- The **30-day window** is fully populated after 30 days.
- The **90-day window** reflects your full history after 90 days.

For the Providers panel specifically, the panel is most useful from 30 days onward and most useful for provider keep/drop decisions from 90 days onward. See [stats-v2-history-cutover.md](stats-v2-history-cutover.md).

---

## Prometheus metrics (operator/SRE reference)

These metrics are available on ECM's `/metrics` endpoint for operators running their own monitoring stack:

| Metric name | Type | What it measures |
|---|---|---|
| `ecm_session_telemetry_writes_total` | Counter | Write attempts per poll cycle, by result (`success` / `failure`) |
| `ecm_session_telemetry_write_duration_seconds` | Histogram | Wall time of one batch write to `session_telemetry` |
| `ecm_session_telemetry_row_count` | Gauge | Rows written in the most recent poll cycle |
| `ecm_provider_resolution_total` | Counter | Provider resolution outcomes per poll, by result (`resolved` / `unresolved`) |
| `ecm_stats_query_duration_seconds` | Histogram | Latency of Stats v2 HTTP queries, by endpoint and granularity |

The `ecm_provider_resolution_total` resolved/unresolved ratio is the data-consistency SLI for provider attribution. A healthy installation maintains ≥95% resolved at steady state. A sustained drop in the resolved rate means the Unknown bucket is growing and provider attribution is degraded.

---

## Going deeper

- [Users panel](users-panel.md) — how the Users panel is built from these metrics.
- [Stats v2 history cutover](stats-v2-history-cutover.md) — why history starts from the v0.17.0 deploy date.
- [ADR-007: session_telemetry retention policy](../../adr/ADR-007-session-telemetry-retention.md) — raw retention (30 days), rollup design, and the 400-day rollup horizon.
- [`docs/api.md`](../../api.md) — the API endpoints that power the Stats v2 panels.
