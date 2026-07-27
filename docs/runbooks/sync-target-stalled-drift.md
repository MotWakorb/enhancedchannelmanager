# Runbook: ECM Cross-Instance Sync Stalled — Target Drift

> **STUB — section headers + metric names present, real triage and resolution procedures pending.** This runbook ships at v0.18.1 alongside the `ECMSyncStalledTargetDrift` alert (bead `enhancedchannelmanager-k78ja`, epic `i39wu`) so the alert has a `runbook_url` to resolve. The procedures fill in as the team accumulates incident experience after operators begin scheduling cross-instance sync. If you are the first responder to this alert, the structure below is your skeleton — capture what you do in real time and feed it back into this file via a follow-up bead.

- **Severity**: P3 warning
- **Owner**: SRE
- **Last reviewed**: 2026-06-19 (stub)
- **Related beads**: `enhancedchannelmanager-k78ja` (this runbook + alert + `ecm_sync_runs_total` metric), `enhancedchannelmanager-i39wu` (cross-instance sync epic), `enhancedchannelmanager-tjaey` (one-way sync engine), `enhancedchannelmanager-5gzg5` (sync task wrapper)

**Alerts that route here:**

- `ECMSyncStalledTargetDrift` (warning) — a sync target has not recorded a FULL APPLIED sync in over 3 hours (hourly cadence basis, 3-missed-run budget); sustained 1h. Fires PER TARGET — the `$labels.sync_target_id` in the alert names which target is drifting (7ipq2.3: one task per `SyncTarget`; another target syncing cleanly does NOT clear this). **Only a full apply clears it** — a dry-run preview writes nothing to B and deliberately does not advance the clock.

**SLO:** [Task scheduler health](../sre/slos.md#capacity-planning-task-scheduler-health-bd-qxi02) (capacity-planning class, not a numbered SLO).

---

## What this is

The cross-instance sync tasks (`dbas_sync_<sync_target_id>` — one registered task per `SyncTarget` row, ADR-013 S6 / bead `7ipq2.3`; epic `i39wu`) do a one-way push of this instance's config (and channels) from Dispatcharr-A to a remote Dispatcharr-B `SyncTarget`. Each cycle is idempotent — it reads A's then-current config and converges B toward it, so "just re-run next interval" is a complete recovery story. Distinct targets run concurrently (bounded by `ECM_SYNC_MAX_CONCURRENT`, default 3); a second run against the SAME target is refused `ALREADY_RUNNING` by the engine's per-task_id guard.

The alert fires when ONE target's task has not recorded a FULL success within its staleness budget. The metrics:

- `ecm_sync_last_full_success_timestamp{sync_target_id}` (gauge, per target) — **the alert's SLI.** Unix-epoch seconds of the last FULL APPLIED sync for that target: stamped only by a `confirm_apply` run whose report outcome was a clean `SUCCESS`. A dry-run preview, a partial/rolled-back apply, and a credential-freshness abort all leave it unchanged. The alert is `(time() - this) > 10800` per series, guarded `> 0` so it stays silent on fresh installs, preview-only targets, and targets the operator left MANUAL.
- `ecm_task_schedule_last_success_timestamp{task_id=~"dbas_sync_.+"}` (gauge, per target) — the GENERIC task-health gauge, advanced by the task_engine on ANY successful run **including a dry-run preview**. Useful for "is this task running at all"; do **not** read it as convergence freshness (that conflation is what let a recurring preview mask real drift — PR #752 review).
- `ecm_sync_runs_total{result}` (counter) — tri-state run outcome, `result ∈ {success, partial, failed}` (mirrors `ecm_backup_runs_total`). The companion signal that tells you WHICH failure mode you are in. NOTE: this counter is result-only — an AGGREGATE across targets; use the per-target gauge (and per-task run history) to attribute a failure mode to a specific target on multi-target installs.

## Why this matters

The #1 operator risk for cross-instance sync is **silent drift discovered at failover** — a sync quietly failing (or looping on partial) for N cycles while B diverges from A, noticed only when you actually fail over to B and find it stale. This alert is the cheapest catch for that: a stale last-success timestamp means B is not being kept current.

**Partial-apply caveat (read this first):** ONLY a full success stamps the last-success gauge. A tri-state `partial` run — an APPLY that mixed / rolled-back — does NOT stamp it. So this alert ALSO fires on a *sustained partial-apply loop*, not just on outright failures. That is correct (B is drifting either way), but it means "the task is running" is not the same as "the alert should clear." Always check `ecm_sync_runs_total{result="partial"}` vs `{result="failed"}` before concluding the task is dead.

## Symptoms

- `ECMSyncStalledTargetDrift` firing (last success > 3h).
- TODO: capture the operator-visible signal from the first real incident — likely "I failed over to B and it was missing recent channels/EPG."
- TODO: `ecm_sync_runs_total{result="partial"}` or `{result="failed"}` climbing while `{result="success"}` is flat.

## First 10 minutes

1. **Confirm the alert is real.** Read the staleness directly for the target named in the alert's `sync_target_id` label:
   ```promql
   time() - ecm_sync_last_full_success_timestamp{sync_target_id="<sync_target_id>"}
   ```
   If the gauge is `0` / absent, that target has never completed a full APPLY on this install (fresh install, target just created, operator left it MANUAL, or the operator only ever runs previews) — the `> 0` guard should have suppressed the alert; treat as a false positive and capture for tuning.

   Note the distinction that matters here: a *preview* advances `ecm_task_schedule_last_success_timestamp{task_id="dbas_sync_<id>"}` but NOT the gauge above. If the task looks healthy in Task History while this alert fires, check whether the schedule is running previews (`confirm_apply` unset) rather than applies.

2. **Failed vs partial — which mode?** Compare the tri-state counter:
   ```promql
   sum(increase(ecm_sync_runs_total{result="failed"}[1h]))
   sum(increase(ecm_sync_runs_total{result="partial"}[1h]))
   ```
   - `failed` climbing → the run is aborting BEFORE touching B (credential-freshness abort — target disabled/revoked/rotated — no target configured, or an SSRF refusal of the `base_url`). Go to **Branch A**.
   - `partial` climbing → the run reached the apply phase but did not finish clean. **Live-validated 2026-07-27 (bead `7ipq2.2`): a fully UNREACHABLE B lands HERE, not in `failed`** — the importers degrade per-item (fail-soft), every category fails, and the compensating rollback yields a `partial_failed_rolled_back` outcome. So check B reachability on this branch too, before assuming a single-category loop. Go to **Branch B**.

3. **Is B reachable?** Live-validated check — hit B's public version endpoint (no auth needed) with the configured `SyncTarget.base_url`:
   ```bash
   curl -fsS <base_url>/api/core/version/
   ```
   Also check `GET /api/sync-targets/<id>` — `last_outcome` / `last_full_sync_at` are stamped per realized apply (only a FULL success advances `last_full_sync_at`).

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

- **First rule out an unreachable B** (live-validated: total B outage surfaces as `partial`, not `failed` — every category fails per-item and the run rolls back): `curl -fsS <base_url>/api/core/version/`.
- Pull the most recent run's per-category report: the task result's `details.sync_report` (Task History UI, or `POST /api/tasks/dbas_sync_<sync_target_id>/run` response for a manual re-run) — each category carries `created/updated/skipped/failed` plus per-item `failure_details` with sanitized upstream messages (live example: `400 {"auto_created_by": ["Invalid pk ..."]}` pinpointed a payload bug in minutes).
- Cross-reference whether the failing category is a known-fragile importer (channels/streams 4-tier matcher, per ADR-013 S9).

## Resolution

TODO — fill in once the team has resolved at least one real incident. Likely categories:

1. **B unreachable**: restore connectivity to Dispatcharr-B, then force one manual re-sync (idempotent) — `POST /api/tasks/dbas_sync_<sync_target_id>/run` with `{confirm_apply}`. The alert clears on that target's next full success.
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
