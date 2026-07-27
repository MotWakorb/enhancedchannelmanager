# Runbook: ECM Cross-Instance Sync Stalled — Target Drift

> **STUB — section headers + metric names present, real triage and resolution procedures pending.** This runbook ships at v0.18.1 alongside the `ECMSyncStalledTargetDrift` alert (bead `enhancedchannelmanager-k78ja`, epic `i39wu`) so the alert has a `runbook_url` to resolve. The procedures fill in as the team accumulates incident experience after operators begin scheduling cross-instance sync. If you are the first responder to this alert, the structure below is your skeleton — capture what you do in real time and feed it back into this file via a follow-up bead.

- **Severity**: P3 warning
- **Owner**: SRE
- **Last reviewed**: 2026-06-19 (stub)
- **Related beads**: `enhancedchannelmanager-k78ja` (this runbook + alert + `ecm_sync_runs_total` metric), `enhancedchannelmanager-i39wu` (cross-instance sync epic), `enhancedchannelmanager-tjaey` (one-way sync engine), `enhancedchannelmanager-5gzg5` (sync task wrapper)

**Alerts that route here:**

- `ECMSyncStalledTargetDrift` (warning) — the `dbas_sync` task has not recorded a FULL success in over 3 hours (hourly cadence basis, 3-missed-run budget); sustained 1h.

**SLO:** [Task scheduler health](../sre/slos.md#capacity-planning-task-scheduler-health-bd-qxi02) (capacity-planning class, not a numbered SLO).

---

## What this is

The cross-instance sync task (`dbas_sync`, epic `i39wu`) does a one-way push of this instance's config (and channels) from Dispatcharr-A to a remote Dispatcharr-B `SyncTarget`. It is idempotent — each cycle reads A's then-current config and converges B toward it, so "just re-run next interval" is a complete recovery story.

The alert fires when `dbas_sync` has not recorded a FULL success within its staleness budget. The metrics:

- `ecm_task_schedule_last_success_timestamp{task_id="dbas_sync"}` (gauge) — Unix-epoch seconds of the last FULL success. Stamped by the task_engine on `TaskResult.success`. The alert is `(time() - this) > 10800`, guarded `> 0` so it stays silent on fresh installs / operators who left the task MANUAL.
- `ecm_sync_runs_total{result}` (counter) — tri-state run outcome, `result ∈ {success, partial, failed}` (mirrors `ecm_backup_runs_total`). The companion signal that tells you WHICH failure mode you are in.

## Why this matters

The #1 operator risk for cross-instance sync is **silent drift discovered at failover** — a sync quietly failing (or looping on partial) for N cycles while B diverges from A, noticed only when you actually fail over to B and find it stale. This alert is the cheapest catch for that: a stale last-success timestamp means B is not being kept current.

**Partial-apply caveat (read this first):** ONLY a full success stamps the last-success gauge. A tri-state `partial` run — an APPLY that mixed / rolled-back — does NOT stamp it. So this alert ALSO fires on a *sustained partial-apply loop*, not just on outright failures. That is correct (B is drifting either way), but it means "the task is running" is not the same as "the alert should clear." Always check `ecm_sync_runs_total{result="partial"}` vs `{result="failed"}` before concluding the task is dead.

## Symptoms

- `ECMSyncStalledTargetDrift` firing (last success > 3h).
- TODO: capture the operator-visible signal from the first real incident — likely "I failed over to B and it was missing recent channels/EPG."
- TODO: `ecm_sync_runs_total{result="partial"}` or `{result="failed"}` climbing while `{result="success"}` is flat.

## First 10 minutes

1. **Confirm the alert is real.** Read the staleness directly:
   ```promql
   time() - ecm_task_schedule_last_success_timestamp{task_id="dbas_sync"}
   ```
   If the gauge is `0` / absent, the task has never succeeded on this install (fresh install or operator left it MANUAL) — the `> 0` guard should have suppressed the alert; treat as a false positive and capture for tuning.

2. **Failed vs partial — which mode?** Compare the tri-state counter:
   ```promql
   sum(increase(ecm_sync_runs_total{result="failed"}[1h]))
   sum(increase(ecm_sync_runs_total{result="partial"}[1h]))
   ```
   - `failed` climbing → the run is aborting (B unreachable, credentials rotated/revoked, or no target configured). Go to **Branch A**.
   - `partial` climbing → applies are landing but keep half-failing on at least one category. Go to **Branch B**.

3. **Is B reachable?** TODO: command to check connectivity to the configured `SyncTarget.base_url` from the ECM container.

## Diagnosis

TODO — fill in as the team accumulates incident experience. Initial branches:

### Branch A: Run is aborting (`result="failed"`)

The most common subcase is a **credential-freshness abort** — the bound `SyncTarget` was disabled, its token was revoked, or its `credential_version` was rotated since the schedule was configured. The abort is non-silent.

- **Check the `sync_outbound` journal for a freshness abort:**
  ```bash
  # TODO: confirm the exact journal query/filter. The abort logs category
  # "sync_outbound", action_type "scheduled_sync_skipped", with a sanitized
  # reason (disabled / revoked / rotated / missing target).
  docker logs ecm-ecm-1 --since 1h | grep '\[DBAS_SYNC\]'
  ```
- TODO: where to confirm B reachability (the engine builds a remote client only after the freshness gate passes).
- If credentials were rotated, the schedule's captured `cloud_credential_version` is stale — the operator must re-save the sync schedule against the target to re-capture the current version.

### Branch B: Sustained partial apply (`result="partial"`)

The apply runs but a category mixes/rolls-back every cycle, so the run never reports a clean success and the last-success gauge never advances.

- TODO: command to pull the most recent `dbas_sync` task-execution `details.sync_report` to see which category is failing.
- TODO: cross-reference whether the failing category is a known-fragile importer (channels/streams 4-tier matcher, per ADR-013 S9).

## Resolution

TODO — fill in once the team has resolved at least one real incident. Likely categories:

1. **B unreachable**: restore connectivity to Dispatcharr-B, then force one manual re-sync (idempotent) — `POST /api/tasks/dbas_sync/run` with `{sync_target_id, confirm_apply}`. The alert clears on the next full success.
2. **Credentials rotated/revoked**: re-validate the `SyncTarget`, re-save the sync schedule to re-capture `credential_version`, then force a manual re-sync.
3. **Partial-apply loop**: identify the failing category from the sync report, fix the underlying cause (B-side schema/config divergence), then re-sync.

In all cases the engine is idempotent — once the root cause is fixed, a single manual re-sync converges B to A and resets the staleness clock.

## Escalation

If the alert persists more than a few evaluation windows after triage:

- Escalate to SRE + PE for a coordinated A/B look (the sync engine spans both instances).
- If the partial-apply loop is a B-side divergence the operator cannot resolve, file a bead against the sync epic (`i39wu`).

## Post-incident

- [ ] Update this runbook with the actual diagnosis steps that worked.
- [ ] If the failure mode was a partial-apply loop, evaluate whether a per-category sync outcome metric (cardinality-bounded) would have made diagnosis faster — weigh against the `ecm_task_scheduler` group's cardinality posture.
- [ ] If the interval basis (hourly → 3h budget) produced a false positive against the operator's actual configured cadence, capture for threshold tuning.

## References

- [Task scheduler health SLO](../sre/slos.md#capacity-planning-task-scheduler-health-bd-qxi02)
- ADR-013 cross-instance live sync — S6 (trigger / overlap guard), S8 (operational posture), S9 (per-cycle importers)
- `enhancedchannelmanager-k78ja` (this runbook + alert + metric), `enhancedchannelmanager-i39wu` (epic)
- Sibling staleness runbook: [`task_scheduler_stalled.md`](./task_scheduler_stalled.md)
