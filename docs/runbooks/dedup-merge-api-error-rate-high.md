# Runbook: ECM Dedup Merge API Error Rate High

> **STUB — section headers + metric names present, real triage and resolution procedures pending.** This runbook ships at v0.17.1 alongside the SLO-10 alert rules (bd-ft3hk / BD-M) so the alert has a runbook_url to resolve. The procedures fill in as the team accumulates incident experience after BD-D/E/F deploy the metric emitters. If you are the first responder to this alert, the structure below is your skeleton — capture what you do in real time and feed it back into this file via a follow-up bead.

- **Severity**: **P1 page** — this is the load-bearing write path of the dedup epic. A failed accept potentially leaves a channel in a half-merged state.
- **Owner**: SRE
- **Last reviewed**: 2026-05-16 (stub)
- **Related beads**: `enhancedchannelmanager-ft3hk` (this runbook + alert rule), `enhancedchannelmanager-1v4ht` (dedup epic), `enhancedchannelmanager-ct9wl` (existing single-merge 422 pattern), `enhancedchannelmanager-ozhkf` (bulk-merge 422 pattern), BD-E (merge endpoint)

**Alerts that route here:**

- `ECMDedupMergeApiErrorRateHigh` (**page**) — `sum(rate(ecm_dedup_merge_requests_total{status="error"}[5m])) / sum(rate(ecm_dedup_merge_requests_total[5m])) > 0.01` sustained 5m, with a guard that total rate must exceed 0.01 req/s to avoid paging on a single mid-call exception during idle periods

**SLO:** [SLO-10c Merge API Error Rate](../sre/slos.md#slo-10-channel-deduplication-v0171-dedup-epic-bd-1v4ht--bd-ft3hk)

---

## What this is

`POST /api/channel-merges/{id}/accept` is the load-bearing write path of the dedup epic — it executes the merge that the operator approved in the modal. The operation proxies to Dispatcharr (channel update with the merged stream list) and updates ECM-side state (delete source channels, record the merge in the dedup ledger).

The `status` label on `ecm_dedup_merge_requests_total`:

- `success` — accept completed end-to-end.
- `error` — 5xx response or unhandled exception (the alert numerator).
- `dismissed` — operator dismissed instead of accepting (not an error, not a numerator event).
- `cancelled` — operator started accept but cancelled mid-flow.

4xx responses are **explicitly excluded** from `status="error"`. A 422 on stale source IDs (the bd-ct9wl / bd-ozhkf pattern) is correct backend behavior, not service unreliability — that response correctly tells the frontend "refresh the channels list and try again."

## Why this matters

The merge endpoint is the single point in the dedup epic where ECM state and Dispatcharr state can drift apart. A failed accept can leave one of the following half-states:

1. **Target updated, sources not deleted** — the target channel got the merged streams but the source channels are still in Dispatcharr. The operator sees duplicates in the channels list and a phantom merge entry.
2. **Sources deleted, target not updated** — the source channels are gone from Dispatcharr but the target did not receive the streams. The streams are orphaned.
3. **Dedup-ledger updated, neither side reconciled** — the ECM-side merge record exists but neither Dispatcharr side reflects it.

Recovery from any half-state is manual and expensive. The page-severity alert exists so on-call can stop the bleeding before the half-merge count grows.

## Symptoms

- Operator-visible: "the merge succeeded but the source channels are still there" / "the merge failed but my stream is gone" / "I clicked merge and got a generic error."
- TODO: capture the characteristic error banner copy once BD-E's UI ships.
- `sum(rate(ecm_dedup_merge_requests_total{status="error"}[5m]))` climbing.
- TODO: log signature once BD-E logs the failure mode.

## First 5 minutes

1. **Confirm the alert is real.** Read the error ratio directly:
   ```promql
   sum(rate(ecm_dedup_merge_requests_total{status="error"}[5m]))
   / sum(rate(ecm_dedup_merge_requests_total[5m]))
   ```
   If `NaN` or below 1%, the alert should not have fired (rate guard); treat as a false positive and capture for tuning.

2. **Identify the half-merge candidates from the last 15 minutes.**
   ```bash
   docker logs ecm-ecm-1 --since 15m | grep '\[DEDUP\]' | grep -iE 'merge.*(error|exception|failed)'
   ```
   Capture every `target_channel` and `source_channel` ID that appears in an error line. These are the candidates for half-merge state.

3. **Check Dispatcharr connectivity.** If the proxy call to Dispatcharr is timing out, every merge fails the same way — this is a Dispatcharr incident, not a dedup-subsystem incident.
   ```bash
   docker exec ecm-ecm-1 curl -sS http://<dispatcharr-host>:<port>/api/health || echo DOWN
   ```

4. **Cross-reference broader 5xx rate.** If `ECMHTTPError5xxElevated` is also firing, the merge endpoint is a downstream symptom of a broader backend failure — go to [http_error_rate.md](./http_error_rate.md) first.

## Diagnosis

TODO — fill in as the team accumulates incident experience. Initial branches to investigate:

### Branch A: Dispatcharr proxy failure

The most expected failure mode — Dispatcharr returns 5xx or times out partway through the merge. The half-state risk is highest here because the source-delete and target-update are not in a shared transaction across the two systems.

- TODO: command to inspect the merge endpoint's transaction boundary.
- TODO: capture the exact log signature of "Dispatcharr returned X partway through merge."

### Branch B: SQLite lock contention

A long-running bulk operation (auto-creation, M3U import) holds a write lock; the merge endpoint's ECM-side update times out.

- Cross-reference: [stats-v2-write-failures.md](./stats-v2-write-failures.md) Branch B (same root cause class).

### Branch C: Unhandled exception in merge code

A code path in BD-E's merge logic raises an exception that is not caught — the request returns 500 and the global error handler increments `status="error"`.

- TODO: command to grep traceback lines.

## Resolution

TODO — fill in once the team has resolved at least one real incident. Mitigation priorities:

1. **Stop the bleeding.** Disable the merge endpoint via feature flag if available, or surface a "merge temporarily unavailable" banner to operators.
2. **Identify half-merge candidates.** Use the log-grep from step 2 above to identify every `(target_channel, source_channel)` pair from an error line in the incident window.
3. **Reconcile half-state.** For each half-merge candidate:
   - TODO: query to determine which of the three half-states applies.
   - TODO: command to either complete the merge (target update + source delete) or rollback (undelete sources, revert target stream list).
4. **Fix root cause.** Per the branch identified above.

## Escalation

If the alert persists more than 15 minutes after triage:

- Page on-call SRE AND PE for a coordinated look at the merge endpoint.
- Escalate to the dedup-epic owner (bd-1v4ht) — sustained merge failures may require disabling the dedup modal until the root cause is fixed.
- Consider opening an incident channel — half-merge state across multiple channels needs a coordinated reconciliation.

## Post-incident

- [ ] Update this runbook with the actual diagnosis steps that worked, especially the half-state reconciliation queries.
- [ ] File a blameless postmortem — page-severity incidents on a load-bearing write path warrant one.
- [ ] If the merge endpoint needs a structural fix (transactional boundary across ECM + Dispatcharr, idempotency token, two-phase commit), file an epic.
- [ ] If a new metric (per-failure-mode `reason` label on the error counter, bounded to a small set) would have made diagnosis faster, evaluate against the SLO-10 cardinality budget.

## References

- [SLO-10c](../sre/slos.md#slo-10-channel-deduplication-v0171-dedup-epic-bd-1v4ht--bd-ft3hk)
- bd-ft3hk (this runbook + alert), bd-1v4ht (dedup epic)
- bd-ct9wl (single-merge 422 pattern — the correct-behavior reference for what NOT to count as error)
- bd-ozhkf (bulk-merge 422 pattern — same)
- BD-E (merge endpoint)
