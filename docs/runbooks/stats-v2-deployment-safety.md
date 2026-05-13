# Runbook: Stats v2 Deployment Safety

> Stats v2 deployment-safety checklist. Read **before** deploying any release that touches `session_telemetry`, the BandwidthTracker writer, the resolver, or `/api/stats/*` endpoints.

- **Severity**: Deployment runbook (not alert-driven)
- **Owner**: SRE
- **Last reviewed**: 2026-05-13
- **Related beads**: `enhancedchannelmanager-skqln.11`, `enhancedchannelmanager-skqln.12`, `enhancedchannelmanager-skqln.3`, `enhancedchannelmanager-skqln.14`

**When to read this:**

- Before any v0.17.0+ deploy that includes a `session_telemetry` schema change.
- Before any deploy that changes `backend/bandwidth_tracker.py` write path.
- Before any deploy that changes the active-stream → provider resolver.
- After any incident involving Stats v2 — re-read to verify the deploy preconditions still hold.

---

## What this runbook is for

Stats v2 has structural deploy hazards that are unique to its architecture:

1. **The writer runs on a poll loop**, not on user request. A migration mismatch surfaces inside a background task, not on a user-visible API call — easy to miss without instrumentation.
2. **The `ECM_SESSION_TELEMETRY_WRITE_ENABLED` flag is retired** (skqln.3 step (d)). There is no kill-switch. If the writer ships broken, the only mitigation is rollback or hotfix — you cannot env-disable it.
3. **Popularity rankings shift on first deploy after upgrade**, because the new data shape changes how the panels aggregate. See [stats-v2-history-cutover](../user_guide/stats/stats-v2-history-cutover.md) — operators should be warned in release notes.

This runbook codifies the pre-deploy and post-deploy checks that catch the common failure modes.

## Pre-deploy preconditions

Run through this list before the merge button is pressed.

### 1. Migration order matters

Alembic migrations under `backend/alembic/versions/` are applied at container startup. The order is:

1. Container starts, `entrypoint.sh` (or equivalent) runs `alembic upgrade head` BEFORE `uvicorn main:app` boots.
2. The app starts with the schema at `head`.
3. The BandwidthTracker poll loop kicks in after the app is ready.

**Hazard:** If a migration adds a column the writer reads, and the migration fails OR is skipped, the writer will fail every poll cycle with `OperationalError: no such column`. See `stats-v2-write-failures.md` Branch D.

**Pre-deploy check:**
```bash
# Verify Alembic head in the new code matches what the deployment will apply.
cd backend && alembic heads
# Should be one head. Multiple heads = unresolved migration conflict — STOP.
```

If migrations have been added since the last deploy, verify they apply cleanly against a copy of the production DB:
```bash
# In a scratch container, copy production journal.db, then:
docker exec <scratch> alembic upgrade head
# Expected: no errors. Failure here means the migration will fail in prod.
```

### 2. The retired flag

`ECM_SESSION_TELEMETRY_WRITE_ENABLED` is **retired**. Do not:

- Add it to a `docker-compose.yml` thinking it disables the writer.
- Tell an operator "if Stats v2 is broken, set the flag to false."
- Reintroduce it as a kill-switch — that ship has sailed and the writer's try/except is the architectural replacement.

Confirm the flag does not appear in any deployment manifest:
```bash
grep -rn ECM_SESSION_TELEMETRY_WRITE_ENABLED \
  /path/to/deployment-config/ /path/to/docker-compose*.yml 2>/dev/null \
  || echo "OK — flag absent"
```

If you find it: remove it. It does nothing, and leaving it gives the next operator a false sense of control.

### 3. Popularity-ranking shift expected

On the first deploy after upgrade, popularity rankings on the Stats panels will shift. This is **expected behavior** and is documented in:

- [`docs/user_guide/stats/stats-v2-history-cutover.md`](../user_guide/stats/stats-v2-history-cutover.md) — the operator-facing "useful in 30d, fully useful in 90d" framing.
- ADR-007 (retention policy for `session_telemetry`).

The release notes for the deploy must call this out — see [`docs/discord_release_notes.md`](../discord_release_notes.md) for the release-note template. Pre-deploy: confirm the release-notes draft includes a cutover note for Stats v2 changes.

### 4. Perf-benchmark gate is green

skqln.10 shipped a CI regression gate against `pytest-benchmark`. Before merging:

```bash
# In CI, the gate runs automatically. Locally, verify nothing regressed:
cd backend && python -m pytest tests/benchmarks/ -m benchmark --benchmark-only
```

A regression here means SLO-9 (Stats v2 query latency) is about to breach in production. Do not merge through a red benchmark.

## Post-deploy verification

Within 5 minutes of container restart, verify the writer is healthy.

### 1. Writer is emitting

```promql
# Successful writes in the last 5m should be > 0 (or > 0.001 rate).
sum(rate(ecm_session_telemetry_writes_total{result="success"}[5m]))
```

If zero, the writer is not running. Check container logs:
```bash
docker logs ecm-ecm-1 --since 5m | grep -iE '\[(STATS_V2|BANDWIDTH)\]' | head -30
```

### 2. Write failure rate is healthy

```promql
sum(rate(ecm_session_telemetry_writes_total{result="failure"}[5m]))
/ sum(rate(ecm_session_telemetry_writes_total[5m]))
```

Expected: < 0.05. If > 0.05 immediately after deploy, go to [stats-v2-write-failures runbook](./stats-v2-write-failures.md) Branch D (migration mismatch) first.

### 3. Provider resolver is healthy

```promql
sum(rate(ecm_provider_resolution_total{result="resolved"}[5m]))
/ sum(rate(ecm_provider_resolution_total[5m]))
```

Expected: > 0.95 steady state, but may dip immediately after deploy as Dispatcharr re-handshakes are mid-flight. Recheck at +15m; if still < 0.80, go to [stats-v2-provider-resolution-degraded runbook](./stats-v2-provider-resolution-degraded.md).

### 4. Stats endpoints are fast

```promql
histogram_quantile(0.95,
  sum by (le) (rate(ecm_stats_query_duration_seconds_bucket[5m])))
```

Expected: < 0.8 (800ms) for all stats endpoints. If > 0.8, the perf-benchmark gate may have been bypassed — investigate the deployed code matches the gate-passing commit.

### 5. Row growth is sane

```promql
# Compare to pre-deploy baseline.
ecm_session_telemetry_row_count
```

A doubling of per-cycle row count immediately after deploy is the classic double-emit regression signal. See [stats-v2-row-growth runbook](./stats-v2-row-growth.md) Case 2.

### 6. Hit the read endpoints

Smoke-test the Stats v2 read API surface to confirm queries return non-empty payloads. Replace `<token>` with a valid JWT and `<host>` with the deployed host:
```bash
curl -sS -H "Authorization: Bearer <token>" \
  http://<host>:<port>/api/stats/overview | jq '. | length'
# Expected: > 0 (some metric returned).

curl -sS -H "Authorization: Bearer <token>" \
  http://<host>:<port>/api/stats/watch-time | jq '. | length'
# Expected: > 0 once session_telemetry has accumulated data.
```

A 200 with an empty payload immediately after deploy is normal (no data yet); a 500 or a 4xx is not.

## Rollback procedure

If post-deploy verification fails:

1. **Capture logs first.** Container restart loses in-memory state useful for postmortem:
   ```bash
   docker logs ecm-ecm-1 --since 1h > /tmp/stats-v2-deploy-rollback.log
   ```

2. **Rollback via the standard ECM rollback flow.** See [`docs/runbooks/v0.16.0-rollback.md`](./v0.16.0-rollback.md) for the rollback pattern (frontend `dist/`, backend `/app/`, container restart). The Stats v2 case has no special rollback — the same `docker cp` of the prior bundle + `docker restart` works.

3. **If the rollback also fails** (rare, but possible if the schema migrated and the prior code can't read the new schema):
   - The Alembic `current` version is now newer than the prior code expects.
   - Either re-apply the new code (try again, fix forward) OR `alembic downgrade <prior-rev>` and restart with the old code.
   - **Capture log snapshot before any downgrade** — Alembic downgrades can lose data.

4. **Notify the operator** of the cutover behavior:
   - If you rolled back after data was already written under the new schema, the prior code may not see the post-upgrade rows. This is normal — when the new code re-deploys, the rows are still there.
   - The Providers/Users panel "useful in 30d, fully useful in 90d" framing in the release notes assumes monotonic forward progress — a rollback resets that clock for any rows the prior code can't read.

## Common deploy-time failure patterns

### Container restarts in a loop

`docker ps` shows ecm-ecm-1 restarting every 30-60s. Almost always Alembic upgrade failing inside the entrypoint. Check:
```bash
docker logs ecm-ecm-1 --since 5m | grep -iE 'alembic|migration|OperationalError'
```

### `/metrics` shows no `ecm_session_telemetry_*` series

The writer never executed even once. Either the BandwidthTracker poll loop didn't start, or `install_metrics()` hasn't been called. Check:
```bash
curl -sS http://<host>:<port>/metrics | grep -c ecm_session_telemetry
# Expected: > 0 metric lines.
```

If zero: the observability module didn't register the metrics. This is a code-side regression — file a bead and rollback.

### Stats panel renders but data is "Unknown" for everything

This is a misleading signal in fresh deploys — see the "useful in 30d, fully useful in 90d" framing. But if **previously-populated** provider data has suddenly become "Unknown" after a deploy, it's likely [stats-v2-provider-resolution-degraded runbook](./stats-v2-provider-resolution-degraded.md) territory.

## Escalation

If post-deploy verification fails AND rollback fails:

- This is now a P1 incident — the deploy substrate is broken.
- Page the SRE persona via the operator's chosen channel (no rotation defined yet — routes to `curt@lecaptain.org`).
- Capture all artifacts: logs, `alembic current` output, `/metrics` snapshot, `docker ps` output.

## See also

- [SLO-7: Stats v2 Telemetry Write Success Rate](../sre/slos.md#slo-7-stats-v2-telemetry-write-success-rate)
- [SLO-8: Provider Attribution Rate](../sre/slos.md#slo-8-provider-attribution-rate)
- [SLO-9: Stats v2 Query Latency](../sre/slos.md#slo-9-stats-v2-query-latency)
- [stats-v2-write-failures runbook](./stats-v2-write-failures.md)
- [stats-v2-provider-resolution-degraded runbook](./stats-v2-provider-resolution-degraded.md)
- [stats-v2-row-growth runbook](./stats-v2-row-growth.md)
- [`docs/user_guide/stats/stats-v2-history-cutover.md`](../user_guide/stats/stats-v2-history-cutover.md) — operator-facing cutover narrative
- [`docs/database_migrations.md`](../database_migrations.md) — Alembic migration ordering
- [`docs/runbooks/v0.16.0-rollback.md`](./v0.16.0-rollback.md) — generic rollback procedure for ECM releases
- [`docs/discord_release_notes.md`](../discord_release_notes.md) — release-notes template (must call out Stats v2 cutover behavior)
