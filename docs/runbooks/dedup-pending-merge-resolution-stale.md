# Runbook: ECM Dedup Pending-Merge Resolution Stale

> **STUB — section headers + metric names present, real triage and resolution procedures pending.** This runbook ships at v0.17.1 alongside the SLO-10 alert rules (bd-ft3hk / BD-M) so the alert has a runbook_url to resolve. The procedures fill in as the team accumulates incident experience after BD-D/E/F deploy the metric emitters. If you are the first responder to this alert, the structure below is your skeleton — capture what you do in real time and feed it back into this file via a follow-up bead.

- **Severity**: P3 warning
- **Owner**: SRE
- **Last reviewed**: 2026-05-16 (stub)
- **Related beads**: `enhancedchannelmanager-ft3hk` (this runbook + alert rule), `enhancedchannelmanager-1v4ht` (dedup epic), BD-E (merge endpoint), BD-F (pending-merges queue)

**Alerts that route here:**

- `ECMDedupPendingMergeResolutionStale` (warning) — less than 95% of merge requests added to the pending queue in the last 24h have reached a terminal state (`success` or `dismissed`); sustained 1h

**SLO:** [SLO-10b Pending Merge Resolution Rate](../sre/slos.md#slo-10-channel-deduplication-v0171-dedup-epic-bd-1v4ht--bd-ft3hk)

---

## What this is

The pending-merges queue (BD-F) is where candidate merges land between the candidate-lookup step and operator action. A healthy queue clears at roughly the rate items are added — operators accept (merge) or dismiss (intentional skip), and either outcome is a terminal state that counts toward resolution.

The alert fires when the 24h resolution ratio drops below 95% — i.e., more than 5% of merge requests added to the queue in the last 24h have NOT reached a terminal state. The metrics:

- `ecm_pending_merges_queue_depth_added_total` (counter) — denominator. Incremented on every queue insertion.
- `ecm_dedup_merge_requests_total{status="success"|"dismissed"}` (counter) — numerator. Terminal-state transitions out of the queue.

## Why this matters

The pending-merges queue is supposed to be an interrupt, not an inbox. When merges accumulate without operator action, the modal stops being useful — operators learn to ignore it, the dedup epic loses operator engagement, and the channels list drifts back toward the duplicate state the epic exists to prevent.

The 24h horizon matches operator daily-attention patterns. A merge sitting longer than a day is functionally abandoned regardless of whether it was technically queued.

## Symptoms

- TODO: capture the operator-visible signal from the first real incident — likely "the merge-queue badge has been growing for days" or "I haven't seen the merge modal in a week."
- Resolution ratio < 95% sustained 1h on the alert query.
- TODO: queue-depth gauge trend (climbing without periodic decreases).

## First 10 minutes

1. **Confirm the alert is real.** Read the ratio directly:
   ```promql
   sum(increase(ecm_dedup_merge_requests_total{status=~"success|dismissed"}[24h]))
   / sum(increase(ecm_pending_merges_queue_depth_added_total[24h]))
   ```
   If `NaN`, no merges entered the queue in 24h — the alert should not have fired (guard); treat as a false positive and capture for tuning.

2. **Check the merge API error rate.** If `ECMDedupMergeApiErrorRateHigh` is firing concurrently, the modal is erroring before the operator can act — that's the root cause, go to [dedup-merge-api-error-rate-high.md](./dedup-merge-api-error-rate-high.md).

3. **Check the candidate-flood pattern.** If a recent bulk M3U import added many candidates at once, the operator may be overwhelmed rather than ignoring the modal.
   ```bash
   # TODO: command to count recent candidates by source (drag_drop / add_stream / bulk_m3u).
   ```

## Diagnosis

TODO — fill in as the team accumulates incident experience. Initial branches to investigate:

### Branch A: The merge modal is broken

A frontend regression in the merge modal causes it to error before the operator can act. Items enter the queue but can never reach a terminal state from the operator's side.

- TODO: cross-reference `ecm_client_errors_total{kind="boundary"}` for a spike correlated with the queue-resolution drop.
- TODO: cross-reference the merge-modal-specific log signature.

### Branch B: Operator overload from candidate flood

A bulk M3U import produces 500 candidates in one batch; the operator can realistically clear 10-20 per day; the queue grows faster than it shrinks.

- TODO: command to inspect queue source breakdown.
- Mitigation: introduce a queue-depth cap on the bulk-M3U source (deferred — see SRE position from team-plan).

### Branch C: Dismiss path is broken

A regression where the dismiss button does not actually record a `status="dismissed"` transition — the item sits in the queue and the operator thinks they cleared it.

- TODO: command to compare dismiss button clicks against `ecm_dedup_merge_requests_total{status="dismissed"}` rate.

## Resolution

TODO — fill in once the team has resolved at least one real incident. Likely categories:

1. **Modal regression**: identify the failing frontend code path, ship a fix, validate dismiss + accept both record transitions.
2. **Operator overload**: tactical — clear the backlog manually with a one-shot dismiss-all action; strategic — introduce a queue-depth cap or a per-batch surfacing limit.
3. **Dismiss path broken**: same as modal regression but specifically scoped to the dismiss handler.

## Escalation

If the alert persists more than 24 hours after triage (one full evaluation window):

- Escalate to SRE + PE on-call for a coordinated frontend/backend look.
- Consider whether the queue cap (deferred from team-plan) needs to be expedited.

## Post-incident

- [ ] Update this runbook with the actual diagnosis steps that worked.
- [ ] If a per-source label (e.g., `kind` on the queue-added counter) would have made diagnosis faster, evaluate against the SLO-10 cardinality budget before adding.
- [ ] If the underlying cause is operator overload, file a UX bead — the queue-depth cap conversation belongs to the dedup epic owner, not this runbook.

## References

- [SLO-10b](../sre/slos.md#slo-10-channel-deduplication-v0171-dedup-epic-bd-1v4ht--bd-ft3hk)
- bd-ft3hk (this runbook + alert), bd-1v4ht (dedup epic)
- BD-E (merge endpoint), BD-F (pending-merges queue)
