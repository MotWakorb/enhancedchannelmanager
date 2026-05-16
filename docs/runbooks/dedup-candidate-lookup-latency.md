# Runbook: ECM Dedup Candidate-Lookup Latency High

> **STUB — section headers + metric names present, real triage and resolution procedures pending.** This runbook ships at v0.17.1 alongside the SLO-10 alert rules (bd-ft3hk / BD-M) so the alert has a runbook_url to resolve. The procedures fill in as the team accumulates incident experience after BD-D/E/F deploy the metric emitters. If you are the first responder to this alert, the structure below is your skeleton — capture what you do in real time and feed it back into this file via a follow-up bead.

- **Severity**: P3 warning
- **Owner**: SRE
- **Last reviewed**: 2026-05-16 (stub)
- **Related beads**: `enhancedchannelmanager-ft3hk` (this runbook + alert rule), `enhancedchannelmanager-1v4ht` (dedup epic), BD-B (dedup index), BD-D (candidate-lookup endpoint)

**Alerts that route here:**

- `ECMDedupCandidateLookupLatencyHigh` (warning) — `histogram_quantile(0.99, ecm_dedup_candidate_lookup_duration_seconds_bucket) > 500ms` sustained 10m

**SLO:** [SLO-10a Candidate Lookup Latency](../sre/slos.md#slo-10-channel-deduplication-v0171-dedup-epic-bd-1v4ht--bd-ft3hk)

---

## What this is

The channel-merge candidate-lookup endpoint (`POST /api/channel-merges/candidates`, BD-D) is the server-side matcher that produces the candidate list shown in the merge prompt on drag-drop, add-stream, and bulk M3U import triggers. Its p99 latency is the operator-visible "did the modal pop instantly or did it stutter" signal.

The histogram metric `ecm_dedup_candidate_lookup_duration_seconds` is emitted by the BD-D endpoint wrapper. The matcher reads channels/streams from the same SQLite instance ECM already queries on every page load, so it shares the database substrate with every other backend hot path.

## Why this matters

The merge modal is interactive UI — operators drag, drop, and expect a candidate list within "instant" human perception (~250ms is the upper bound for "feels instant"). The 500ms SLO threshold leaves 2× headroom, so a sustained breach means the matcher has stopped serving the interaction smoothly. Operators perceive stalls as bugs, not slow queries, and the dedup epic loses operator trust quickly.

## Symptoms

- TODO: capture the user-visible symptom (modal hang, partial render, etc.) from the first real incident.
- `histogram_quantile(0.99, sum by (le) (rate(ecm_dedup_candidate_lookup_duration_seconds_bucket[5m])))` > 0.5 sustained.
- TODO: characteristic log signature once we know what BD-D logs on slow paths.

## First 10 minutes

1. **Confirm the alert is real.** Read the p99 directly:
   ```promql
   histogram_quantile(0.99, sum by (le) (rate(ecm_dedup_candidate_lookup_duration_seconds_bucket[5m])))
   ```
   If `NaN`, the matcher is not being called (no current activity) — the alert should not have fired; treat as a false positive and capture for tuning.

2. **TODO: pull recent request logs.** Once BD-D logs the matcher invocation with timing, the grep pattern goes here. Expected format something like:
   ```bash
   docker logs ecm-ecm-1 --since 15m | grep '\[DEDUP\] candidate_lookup'
   ```

3. **Check broader backend latency.** If `ECMHTTPLatencyHighP95` is also firing, this is not dedup-specific — go to [http_latency.md](./http_latency.md) first. Otherwise the matcher is the regression.

## Diagnosis

TODO — fill in as the team accumulates incident experience. Initial branches to investigate:

### Branch A: The dedup index regressed

If a database migration or query refactor caused the candidate matcher to stop using the dedup index (BD-B's index design), every lookup performs a full scan instead of an indexed lookup.

- TODO: command to inspect query plan.
- TODO: command to verify the dedup index exists and is being used.

### Branch B: The matcher is called inside a per-channel handler loop

A regression class where a caller forgets the "one matcher call per trigger" contract and invokes the matcher inside a request-handler loop, multiplying the latency by N channels.

- TODO: caller-side log signature to look for.

### Branch C: SQLite is under bulk-write contention

The matcher shares the database with auto-creation, bulk-merge, and M3U import paths. A long-running bulk operation can starve the matcher.

- TODO: command to identify the bulk operation in flight.
- Cross-reference: same root cause class as [stats-v2-write-failures.md](./stats-v2-write-failures.md) Branch B.

## Resolution

TODO — fill in once the team has resolved at least one real incident. Likely categories:

1. **Index regression**: re-create the dedup index, re-run query plan check.
2. **Per-channel loop**: revert the offending caller change, add a regression test.
3. **Bulk-write contention**: throttle or schedule the bulk operation off-peak; matcher self-heals once the lock clears.

## Escalation

If the alert persists more than 1 hour after triage:

- Page on-call SRE with: current p99, log snippets from `[DEDUP]` lines, output of the query-plan check.
- Cross-reference `ECMDedupMergeApiErrorRateHigh` — if both are firing, treat as a dedup-subsystem incident, not isolated latency.

## Post-incident

- [ ] Update this runbook with the actual diagnosis steps that worked.
- [ ] If a new metric or label would have made diagnosis faster, file a bead for it.
- [ ] If the matcher needs a structural fix (different index, query rewrite, caching layer), file an epic.

## References

- [SLO-10](../sre/slos.md#slo-10-channel-deduplication-v0171-dedup-epic-bd-1v4ht--bd-ft3hk)
- bd-ft3hk (this runbook + alert), bd-1v4ht (dedup epic)
- BD-B (dedup index), BD-D (candidate-lookup endpoint)
