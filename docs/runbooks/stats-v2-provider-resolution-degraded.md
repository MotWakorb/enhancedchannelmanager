# Runbook: Stats v2 Provider Resolution Degraded

> Provider attribution is degraded — channel-polls are landing in the "Unknown" bucket on the Providers panel. **Not a page**, not a user-facing outage. Triage at next business hour.

- **Severity**: P3 warning (NOT pageable)
- **Owner**: SRE
- **Last reviewed**: 2026-05-13
- **Related beads**: `enhancedchannelmanager-skqln.11`, `enhancedchannelmanager-skqln.12`, `enhancedchannelmanager-skqln.14`

**Alerts that route here:**

- `ECMStatsProviderResolutionDegraded` (warning) — resolution rate < 80% over 1h

**SLO:** [SLO-8 Provider Attribution Rate](../sre/slos.md#slo-8-provider-attribution-rate)

---

## What this is NOT

This alert does **not** indicate a user-facing outage:

- The `session_telemetry` row is still written; the writer's `result="success"` counter continues to climb.
- Watch-time math, the Channels panel, and the Users panel are all unaffected.
- Only the Providers panel is degraded — its "Unknown" slice grows when resolution fails.

A `NULL` provider_id is **correct behavior** when ECM genuinely cannot map a stream to a provider. The Providers panel surfaces "Unknown" as a deliberate operator-visible signal, not a silent failure. The alert fires when "Unknown" dominates enough to make the panel uninterpretable, not when any single resolution fails.

## Symptoms

- Providers panel shows a large "Unknown" slice (typically > 20% of total minutes).
- `ecm_provider_resolution_total{result="unresolved"}` rate climbs disproportionately.
- `[STATS_V2] provider_resolution_failed channel=... reason=...` log lines appear at elevated rate.

## First 10 minutes

1. **Confirm the alert is real.** Read the resolution ratio directly:
   ```promql
   sum(rate(ecm_provider_resolution_total{result="resolved"}[1h]))
   / sum(rate(ecm_provider_resolution_total[1h]))
   ```
   Expected if real: < 0.80.

2. **Identify the failure subcategory.** The resolver logs `reason=<subcategory>` on every unresolved channel:
   ```bash
   docker logs ecm-ecm-1 --since 1h \
     | grep '\[STATS_V2\] provider_resolution_failed' \
     | sed -E 's/.*reason=([^ ]+).*/\1/' \
     | sort | uniq -c | sort -rn
   ```
   The output gives a frequency table of subcategories — pick the dominant one to drive triage.

3. **Read one representative log line.** Pick a `channel=` value from the dominant subcategory and inspect the surrounding lines:
   ```bash
   docker logs ecm-ecm-1 --since 1h \
     | grep -A1 -B1 'channel=<CHANNEL_ID>.*reason=<subcategory>' \
     | head -30
   ```

## Common subcategories

The `reason=` value tells you which branch applies. Subcategories shipped in skqln.14:

### reason=no_stream_id

The resolver received a channel record from Dispatcharr that had no `stream_id` field, or the stream_id was null/empty.

**Likely root cause:** Dispatcharr stream-API quirk — either a Dispatcharr-side rotation of stream identifiers, or a Dispatcharr response shape that ECM doesn't yet handle.

**Triage:**
1. Hit Dispatcharr's stream endpoint directly and inspect the shape:
   ```bash
   # Replace placeholders with operator values from settings.
   curl -sS -H "Authorization: Bearer <TOKEN>" \
     http://<dispatcharr-host>:<port>/api/streams/<STREAM_ID> | jq .
   ```
2. If Dispatcharr returns a different envelope than ECM expects, file a bead for the resolver to handle the new shape. Reference `backend/bandwidth_tracker.py` resolver function for the expected shape.
3. If Dispatcharr returns the expected shape, the channel record arrived at ECM without a stream_id — investigate the upstream poll path.

### reason=lookup_raised

The resolver's lookup call against Dispatcharr raised an exception (network blip, timeout, 5xx from Dispatcharr).

**Likely root cause:** Transient network or Dispatcharr-side instability. If sustained > 1h, it's no longer transient.

**Triage:**
1. Check Dispatcharr readiness independently: `curl -sS http://<dispatcharr-host>:<port>/api/health || echo DOWN`.
2. Cross-reference with `ecm_health_ready_check_duration_seconds{check="dispatcharr"}` — if the dispatcharr sub-check is also slow, the issue is upstream, not in the resolver.
3. If transient: no action — resolves naturally as Dispatcharr stabilizes. Note as a finding for capacity-planning.
4. If sustained: page the Dispatcharr operator (this is upstream-incident territory).

### reason=no_match

The lookup succeeded but no matching M3U provider row exists for the returned stream.

**Likely root cause:** Mid-failover storm — the operator is in the middle of migrating providers, or an M3U source has rotated upstream URLs and ECM hasn't re-synced yet.

**Triage:**
1. Confirm M3U sources are healthy and have re-synced recently:
   ```bash
   docker logs ecm-ecm-1 --since 6h | grep '\[M3U\] sync.*completed' | tail -10
   ```
2. If M3U syncs are stale, trigger a manual M3U refresh via the Settings → M3U Sources UI (or `POST /api/m3u/refresh`).
3. If the operator is mid-migration: this is expected behavior; the alert will clear as the migration completes. Note start time for postmortem context if it lasts > 6h.

## Resolution

- For `lookup_raised` and `no_match`: usually resolves naturally as upstream stabilizes. Monitor `ecm:provider_resolution_ratio:1h` recording rule (defined in `prometheus_rules.yaml`); the alert clears automatically once back above 80%.
- For `no_stream_id`: requires a code fix in the resolver. File a bead; the alert is informational until the fix ships.

**Do not** force a resolver retry by restarting the container — the resolver runs on every poll cycle (default 60s), so the next cycle is already a retry. Restarting only loses incident logs.

## Escalation

If degradation persists > 24h or the subcategory is unfamiliar:

- File a P2 bead with the subcategory breakdown table from step 2.
- Engage the project engineer if the resolver needs a code change.
- This alert never pages — escalation is "ticket and triage tomorrow," not "wake someone up."

## Post-incident

- [ ] If a new subcategory appeared that's not in this runbook, add a branch.
- [ ] If a Dispatcharr-side change caused the alert, document it in [`docs/dispatcharr_api.md`](../dispatcharr_api.md).
- [ ] If sustained > 24h, open a bead to investigate whether SLO-8's 80% alert threshold or 95% SLO target need re-tuning.

## See also

- [SLO-8: Provider Attribution Rate](../sre/slos.md#slo-8-provider-attribution-rate)
- [`backend/bandwidth_tracker.py`](../../backend/bandwidth_tracker.py) — `[STATS_V2] provider_resolution_failed` log emission
- [`docs/dispatcharr_api.md`](../dispatcharr_api.md) — Dispatcharr API surface ECM consumes
- [stats-v2-history-cutover](../user_guide/stats/stats-v2-history-cutover.md) — operator-facing note that the Providers panel is "useful in 30d, fully useful in 90d"
