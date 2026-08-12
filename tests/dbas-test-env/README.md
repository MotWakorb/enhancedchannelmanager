# DBAS round-trip test environment

A pinned, **throwaway** Dispatcharr instance + production-shaped seed tooling so
the ECM ↔ Dispatcharr DBAS round-trip success signal (epic
`enhancedchannelmanager-0i2vt`) can be tested against a **live** Dispatcharr
instead of mocks.

> Why this exists: every Dispatcharr-touching ECM test currently mocks the
> client. The mocks **encode our assumptions** of Dispatcharr's async behavior
> (M3U 2-stage refresh polling, EPG download wait, the `is_active` quirk,
> `/api/accounts/users/me/`) — which is exactly what Phase 2 must *validate*.
> This stack lets us validate against reality. Prereq for meaningful coverage of
> beads `.10` (M3U importer), `.11` (EPG importer), `al6e3`/`.14` (4-tier stream
> matcher), `.15` (logos importer), `.18` (pre-flight + rollback), and `l1p4p`
> (users importer).

Full rationale and the CI-fast-path strategy live in
`docs/testing/dbas-test-env.md`.

---

## Isolation contract — READ FIRST

This stack **must never touch** the production ECM stack (`ecm-ecm-1`,
docker-compose.yml/.mcp.yml) or the operator's live Dispatcharr.

- Always use the project name `-p dbas-testenv` (the compose file also sets
  `name: dbas-testenv`, but pass `-p` explicitly so it's unmistakable).
- Host ports are deliberately off the ECM/live ranges: **9591** (web, vs live
  9191), **5436** (postgres, vs 5432). ECM uses 6100/6143/6101.
- All volumes/network are project-scoped (`dbas-testenv_*`).
- **Tear down with `-v`** when done — it's throwaway.

---

## Bring it up

```bash
cd tests/dbas-test-env
cp .env.example .env            # optional: customize version/ports/creds

docker compose -p dbas-testenv -f docker-compose.dbas-test.yml up -d
# wait for the web healthcheck (GET /api/core/version/) to go healthy:
docker compose -p dbas-testenv -f docker-compose.dbas-test.yml ps
```

## Validate the pinned version's behaviors

This is the ground-truth probe — confirms the 0.23.0-era behaviors ECM encodes
still hold at the pinned version, and records the response shapes the CI fake
must reproduce:

```bash
DBAS_TEST_BASE_URL=http://localhost:9591 \
DBAS_TEST_ADMIN_USER=ecmtest DBAS_TEST_ADMIN_PASS=ecmtestpass \
python3 validate-version-behaviors.py
```

## Seed production-shaped data + edge cases

```bash
# 1. Capture a snapshot (PREFERRED: from the operator's live Dispatcharr DB):
DBAS_SNAPSHOT_PGHOST=<operator-db-host> DBAS_SNAPSHOT_PGPASSWORD=<pw> \
  ./capture-snapshot.sh live
#    ...or from the throwaway stack after you've populated it:
./capture-snapshot.sh testenv

# 2. Load it into the throwaway stack (also applies the edge supplement):
./load-snapshot.sh
```

The snapshot (`seed/dispatcharr-seed.sql.gz`) is **git-ignored** — large and
machine-local. The committed, reviewable artifacts are the **manifest**
(`seed/SNAPSHOT_MANIFEST.txt`, row counts) and the **tooling**. Regenerate the
dump on demand.

## Tear down

```bash
docker compose -p dbas-testenv -f docker-compose.dbas-test.yml down -v
```

---

## First-bring-up validation checklist (do this once per version bump)

The compose env vars and the edge-supplement endpoint/field names are
**best-effort against the pinned 0.26.0 schema** and must be confirmed on first
real bring-up. None of this could be live-verified at authoring time (no
Dispatcharr reachable from the authoring sandbox).

- [ ] **Seed-admin env names.** Confirm `DISPATCHARR_SUPERUSER_USERNAME` /
      `DISPATCHARR_SUPERUSER_PASSWORD` actually create the first admin on the
      pinned image. If not, do the first-run wizard once, then capture a
      snapshot so the admin is baked into the seed.
- [ ] **`entrypoint.celery.sh` path** exists in the pinned image
      (`/app/docker/entrypoint.celery.sh`).
- [ ] **`validate-version-behaviors.py` runs clean** — any WARN is a drift
      finding to route to the importer authors.
- [ ] **Edge-supplement endpoints** (`/api/channels/groups/`,
      `/api/channels/streams/`, `/api/accounts/users/`) and the privilege
      fields (`is_superuser`/`is_staff`/`user_level`) match the pinned schema.
- [ ] **Snapshot table names** in `capture-snapshot.sh` manifest query reflect
      the real schema (the row-count query is schema-introspecting, so it's
      tolerant, but eyeball the manifest).

---

## Two-instance sync tier (epic `i39wu`) — A + B

Cross-instance live sync (`docs/adr/ADR-013-cross-instance-live-sync.md`) pushes
the config of **Dispatcharr-A** to **Dispatcharr-B** over B's WRITE API. Testing
it has **two tiers** (see `docs/testing/dbas-test-env.md` →
"Cross-instance sync test strategy"):

| Tier | What | Where | Status |
|------|------|-------|--------|
| **Fast (per-PR)** | STATEFUL two-instance MOCK harness — convergence / idempotency / partial-failure / never-users / redaction | `backend/tests/fixtures/sync_harness.py` + `backend/tests/tasks/test_sync_roundtrip.py` | **BUILT** (bead `46pkq`) — runs in the normal pytest suite |
| **Live (nightly)** | Two REAL non-colliding Dispatcharr instances; validates the mock's write-contract fidelity (409 / async write completion) against reality | `docker-compose.dbas-sync-test.yml` (this dir) | **DEFERRED** — scaffold only; **not run in CI** until a reachable Dispatcharr-B image is wired |
| **Live B-only (manual)** | A running ECM-A syncing against ONE real, disposable Dispatcharr-B — the full engine path (SSRF transport, freshness gate, importers, rollback, metrics) against a real write API | Recipe below | **VALIDATED 2026-07-27** (bead `enhancedchannelmanager-7ipq2.2`, Dispatcharr 0.28.2) — surfaced + fixed 4 real payload/contract bugs the mock tier could not see |

### Bring up the live two-stack (DEFERRED tier — not yet validated)

> The fast mock tier is the one that runs today. The block below is the
> ready-to-wire scaffold for the live tier. Do **not** assume it works until the
> DEFERRED checklist in `docs/testing/dbas-test-env.md` is closed.

```bash
cd tests/dbas-test-env
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml up -d
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml ps
# A = source  -> host 9601 (pg 5446);  B = dest -> host 9602 (pg 5447)
# In-cluster, A reaches B at  http://dispatcharr-b-web:9191  (the SyncTarget base_url
# analogue — exercises the SSRF chokepoint on B's url).

# Tear down (throwaway):
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml down -v
```

### Live B-only validation recipe (VALIDATED 2026-07-27, bead `7ipq2.2`)

The cheapest REAL end-to-end validation: keep your running ECM (and its real
Dispatcharr-A source) as-is, and stand up ONE disposable Dispatcharr-B as the
sync destination. This is what the first live validation ran; it exercised the
whole engine path — pinned SSRF transport, credential-freshness gate, all
importers, custom-stream fallback, logo streaming upload, compensating
rollback, tri-state metrics — against a real Dispatcharr write API, and caught
four live-shape bugs the mock harness structurally could not (the mock encodes
our own payload assumptions; see the bead for the fix list).

**Never point a sync target at a real/production Dispatcharr. B is always the
throwaway.**

```bash
# 1. Bring up a disposable AIO Dispatcharr-B (same image the operator's live
#    instance runs). Fresh named volume; any free host port (9292 here).
docker run -d --name dispatcharr-b-7ipq2 \
  -p 9292:9191 \
  -v dispatcharr-b-7ipq2-data:/data \
  -e DISPATCHARR_ENV=aio \
  -e REDIS_HOST=localhost \
  -e CELERY_BROKER_URL=redis://localhost:6379/0 \
  ghcr.io/dispatcharr/dispatcharr:latest

# Wait until healthy:
until curl -fsS http://127.0.0.1:9292/api/core/version/; do sleep 5; done

# 2. Create the superuser. GOTCHA (validated on 0.28.2): the AIO image does
#    NOT honor DISPATCHARR_SUPERUSER_* env at first boot, and a bare
#    `manage.py` invocation fails with "SECRET_KEY must not be empty" — the
#    entrypoint derives DJANGO_SECRET_KEY from /data/jwt, so export it first:
docker exec dispatcharr-b-7ipq2 sh -c '
  cd /app && export DJANGO_SECRET_KEY="$(tr -d "\r\n" < /data/jwt)" && \
  DJANGO_SUPERUSER_USERNAME=ecmtest-b \
  DJANGO_SUPERUSER_PASSWORD=<throwaway-password> \
  DJANGO_SUPERUSER_EMAIL=ecmtest-b@example.invalid \
  python manage.py createsuperuser --noinput'

# Sanity: JWT login works (NOTE: /api/accounts/token/ is throttled — cache the
# access token rather than re-authenticating per request):
curl -s -X POST http://127.0.0.1:9292/api/accounts/token/ \
  -H 'Content-Type: application/json' \
  -d '{"username":"ecmtest-b","password":"<throwaway-password>"}'

# 3. Point ECM at B — create the SyncTarget via the ECM API (admin-gated):
#    POST /api/sync-targets
#    {"name":"dispatcharr-b-test","base_url":"http://<host-lan-ip>:9292",
#     "credentials":{"auth_method":"password","username":"ecmtest-b",
#                    "password":"<throwaway-password>"},
#     "enabled":true,"sync_logos":false}
#    (SSRF: a LAN/RFC1918 or RFC 6598 shared base_url needs
#     ssrf_outbound_mode=lan_friendly, the default. Link-local/IMDS remain
#     refused unconditionally.)

# 4. Drive cycles via the privileged task endpoint. Each SyncTarget has its
#    OWN task id — dbas_sync_<id> (7ipq2.3 / ADR-013 S6). The task is BOUND
#    to that target: sync_target_id is optional in the payload and, when
#    present, MUST match the bound target — a foreign id hard-fails
#    BOUND_TARGET_MISMATCH without running (it would otherwise run that
#    target outside its own lock). ONE-SHOT arming still applies to the
#    destructive knobs: confirm_apply / cloud_credential_version reset after
#    every run, so an apply must re-send them (a bare re-run is a dry-run of
#    the bound target, never a replayed apply).
#    POST /api/tasks/dbas_sync_<id>/run
#    {"parameters":{"confirm_apply":false}}                          # dry-run
#    {"parameters":{"confirm_apply":true,
#                   "cloud_credential_version":<current version>}}   # apply
#    Distinct targets can run CONCURRENTLY; a second run against the SAME
#    target while one is in flight is refused ALREADY_RUNNING. The cap on
#    simultaneous runs is ECM_SYNC_MAX_CONCURRENT (default 3).

# 5. Verify from both sides: the sync report (task result JSON), the
#    sync_outbound journal rows, /metrics —
#    ecm_sync_runs_total{result,sync_target_id} (filter by target),
#    ecm_sync_last_full_success_timestamp{sync_target_id="<id>"} (the
#    APPLY-ONLY freshness gauge the drift alert keys on — a dry-run preview
#    deliberately does NOT advance it), and the generic task-health gauge
#    ecm_task_schedule_last_success_timestamp{task_id="dbas_sync_<id>"}
#    (advanced by any successful run, previews included) —
#    GET /api/sync-targets/<id> (last_outcome / last_full_sync_at), and B's
#    own API (channels/groups/streams/logos counts).

# 6. TEAR DOWN completely (container + volume) and remove the SyncTarget:
docker rm -f dispatcharr-b-7ipq2
docker volume rm dispatcharr-b-7ipq2-data
# DELETE /api/sync-targets/<id> on ECM.
```

Failure-mode drills validated with this setup (matrix in bead `7ipq2.2`):
credential rotation → fire-time abort (`CREDENTIAL_FRESHNESS_ABORT`, journal +
notification, no request to B); kill switch (`enabled=false`) → non-silent
skip; denylisted `base_url` (e.g. `http://169.254.169.254:9191`) → SSRF
refusal before any socket; `docker stop` B → run degrades per-item (surfaces
as `partial`, NOT `failed` — see the runbook), last-success gauge stalls;
`docker start` B → next cycle converges and the gauge advances.

### Isolation contract for the sync stack

Same non-negotiable rules as the single-instance stack, with **distinct**
project name / ports / volumes so all three (ECM, single-instance, two-instance)
can coexist:

- Project name `-p dbas-sync-testenv`.
- Host ports **9601/5446** (A) and **9602/5447** (B) — off ECM's 6100/6143/6101,
  the live 9191, and the single-instance stack's 9591/5436.
- Volumes/network are project-scoped (`dbas-sync-testenv_*`).
- Tear down with `-v` — throwaway.
