# DBAS round-trip test environment

A **throwaway** Dispatcharr instance on `latest` + production-shaped seed tooling so
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

## Version policy — the test env tracks `dispatcharr:latest`

**PO decision, bead `enhancedchannelmanager-xvuk1`:** this directory tracks
`dispatcharr:latest` **literally**. Not a pinned version bumped by hand.

- `docker-compose.dbas-sync-test.yml` (A + B) and `docker-compose.xc-provider.yml`
  (P) both default to `ghcr.io/dispatcharr/dispatcharr:${DISPATCHARR_VERSION:-latest}`.
  `ghcr.io` is the registry the operator's production Dispatcharr pulls from, so
  the test env and production resolve the same artifact.
- **Latest is the default so nobody has to remember a flag.** The old
  `DISPATCHARR_VERSION=0.28.2` instructions are gone; do not reinstate them.
- **To reproduce an old finding deliberately**, pass the override:
  `DISPATCHARR_VERSION=0.28.2 docker compose ... up -d`.
- `:latest` is only as fresh as your last pull — `docker compose ... pull` first
  when you mean to be current.
- **Every validation run reads and reports the version it actually ran against**,
  from `GET /api/core/version/` on each node. Tracking a floating tag means the
  platform can move mid-flight: it did on 2026-08-20, when `latest` went from
  `0.28.2` (image `1f55137b`) to `0.29.0` (image `3621ebe3`,
  digest `sha256:df768adc…`, identical on docker.io and ghcr.io). **A run that
  cannot name its Dispatcharr is not a result.**
- **Do not read `/api/version`'s `git_commit` as proof of what is running** —
  that is baked-in build metadata and has misled three engineers on this work.
  Checksum the deployed files instead (see the HANDOVER verification table).

This covers **all three** compose files in this directory —
`docker-compose.dbas-test.yml` (single instance),
`docker-compose.dbas-sync-test.yml` (A + B) and
`docker-compose.xc-provider.yml` (P). None of them carries a hand-maintained
version pin any more; do not reinstate one.

`.env.example` no longer sets `DISPATCHARR_VERSION` either. Note that
`docker compose` auto-loads `.env` from this directory for **every** compose
file here, so a stray value in a local `.env` pins the sync stack and the XC
provider too — not just the file you think you are running.

---

## Bring it up

```bash
cd tests/dbas-test-env
cp .env.example .env            # optional: customize ports/creds (NOT the version)
docker compose -p dbas-testenv -f docker-compose.dbas-test.yml pull   # if you mean to be current

docker compose -p dbas-testenv -f docker-compose.dbas-test.yml up -d
# wait for the web healthcheck (GET /api/core/version/) to go healthy:
docker compose -p dbas-testenv -f docker-compose.dbas-test.yml ps
```

## Validate the running version's behaviors

This is the ground-truth probe — confirms the 0.23.0-era behaviors ECM encodes
still hold on whatever `latest` currently resolves to, and records the response
shapes the CI fake must reproduce. **Read and record the version first**; the
probe's result is meaningless without it:

```bash
curl -s http://localhost:9591/api/core/version/     # record this with the result

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

## First-bring-up validation checklist (do this on every platform move)

Because this stack tracks `latest`, "once per version bump" is not a thing you
get told about — re-run this checklist whenever `docker compose pull` brings
something new down, and record the version it ran against. The compose env vars
and the edge-supplement endpoint/field names were **best-effort against the
0.26.0 schema** this file used to pin and must be confirmed against the running
image. None of this could be live-verified at authoring time (no
Dispatcharr reachable from the authoring sandbox).

- [ ] **Seed-admin env names.** Confirm `DISPATCHARR_SUPERUSER_USERNAME` /
      `DISPATCHARR_SUPERUSER_PASSWORD` actually create the first admin on the
      running image. If not, do the first-run wizard once, then capture a
      snapshot so the admin is baked into the seed.
- [ ] **`entrypoint.celery.sh` path** exists in the running image
      (`/app/docker/entrypoint.celery.sh`).
- [ ] **`validate-version-behaviors.py` runs clean** — any WARN is a drift
      finding to route to the importer authors.
- [ ] **Edge-supplement endpoints** (`/api/channels/groups/`,
      `/api/channels/streams/`, `/api/accounts/users/`) and the privilege
      fields (`is_superuser`/`is_staff`/`user_level`) match the running schema.
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

---

## Two-instance A→B validation, driven entirely through the UI (bead `xdmru`)

**VALIDATED 2026-08-19** against Dispatcharr **0.28.2** (both instances) and a
disposable ECM built from branch `feat/dbas-sync-gaps` (`0cdf991f`, version
string `0.18.1-0127`).

Why this recipe exists and how it differs from the `7ipq2.2` B-only run above:
that run seeded fixtures and drove the API. It recorded "logos opt-in PASS
(2 uploaded)" against two stale files that happened to be sitting in **ECM's
own** `/config/uploads/logos/` — which is not where a real instance's logos
live. A validation that seeds its own fixture into the directory under test
cannot detect a gather reading the wrong source (bead `cfxml`). **This recipe
builds every piece of state the way an operator does, through a UI, from
nothing, and verifies by opening B rather than by reading A's run report.**

### Isolation contract

Everything below is disposable and must never touch the operator's `ecm-ecm-1`
or their live Dispatcharr. Distinct project name, distinct ports, distinct
volumes, and a **separate ECM container with its own `/config` volume** — the
production ECM is never repointed.

### 1. Two fresh Dispatcharr instances

```bash
cd tests/dbas-test-env
docker compose -p dbas-sync-testenv \
  -f docker-compose.dbas-sync-test.yml up -d
# A = http://127.0.0.1:9601 (in-cluster: dispatcharr-a-web:9191)
# B = http://127.0.0.1:9602 (in-cluster: dispatcharr-b-web:9191)
```

> **GOTCHA (0.28.2 and 0.29.0 — re-confirmed 2026-08-20):**
> `DISPATCHARR_SUPERUSER_*` in the compose file is **not honored**, on the
> modular stack too. Both instances come up showing the first-run "Create your
> Super User Account" wizard at `/login`. Complete it in the browser on each —
> that is the intended UI-driven path and it takes ten seconds — or run
> `createsuperuser` non-interactively (see the `DJANGO_SECRET_KEY` gotcha).

### 2. A fake provider both instances can reach

The M3U and XMLTV have to be served over HTTP on the compose network. Any static
file server works:

```bash
docker run -d --name provider-xdmru \
  --network dbas-sync-testenv_default --network-alias provider-xdmru \
  -v "$PWD/provider:/usr/share/nginx/html:ro" nginx:alpine
# provider/playlist.m3u   — a handful of #EXTINF entries in 2-3 group-titles
# provider/epg.xml        — XMLTV whose channel ids match the tvg-id values
```

### 3. A disposable ECM built from the branch under test

```bash
docker build -t ecm-xdmru:<sha> \
  --build-arg ECM_VERSION=<version> --build-arg GIT_COMMIT=<sha> \
  --build-arg RELEASE_CHANNEL=test -f Dockerfile .

docker run -d --name ecm-xdmru \
  --network dbas-sync-testenv_default \
  -p 127.0.0.1:6300:6300 -v ecm-xdmru-config:/config \
  -e ECM_PORT=6300 -e ECM_HTTPS_PORT=6343 -e CONFIG_DIR=/config \
  --add-host host.docker.internal:host-gateway ecm-xdmru:<sha>
```

**Confirm the artifact before trusting any result** — a `:dev` image would be
testing code without the fixes:

```bash
curl -s http://127.0.0.1:6300/api/version   # {"version":"...","git_commit":"..."}
```

### 4. Build A's state through the UI

In ECM (`http://127.0.0.1:6300`): create the admin account, then set the
Dispatcharr connection to `http://dispatcharr-a-web:9191` with A's superuser.
Then, still in ECM's UI: **M3U Manager → Add M3U Account** (URL
`http://provider-xdmru/playlist.m3u`), **EPG Manager → Add Standard EPG**
(`http://provider-xdmru/epg.xml`), and **Channel Manager → Edit Mode → expand a
stream group → "Create channels from this group" → Done → Apply All**.

In A's own UI (`http://127.0.0.1:9601`), because ECM does not surface these:

- **Settings → User-Agents → Add User-Agent** — a custom UA (the `hiacv` case).
- **Settings → Stream Profiles → Add Stream Profile** — referencing that UA.
- **System → Logo Manager → Add Logo → upload a PNG** — this puts the file in
  **A's** `/data/logos/`, which is the whole point of the `cfxml` case. Then
  assign it to a channel via the channel edit dialog's Logo picker.
- **Channels → profile selector → new profile** — a channel profile to sync.

> **GOTCHA (fresh 0.28.2):** the first M3U refresh fails with
> `UserAgent matching query does not exist` until the M3U account is given an
> explicit User-Agent (edit the account → User-Agent). See the known-issue note
> below about what that then does to the sync.

Three ECM/UI traps that cost time on the first run:

- **Stream rows render empty after the first M3U import.** ECM caches
  Dispatcharr's channel-group list in-process; if it was cached while A had no
  groups, every stream comes back with `channel_group_name: null` and the
  Channel Manager shows group headers with counts but no selectable rows.
  Restarting the ECM container clears it.
- **"Create channels from this group" is a silent no-op on a COLLAPSED group.**
  Expand the group first.
- The bulk-create modal renders in a portal — look for it with a page-wide
  search, not inside the Channels pane.

### 5. The sync target

ECM → **Settings → Backup & Restore → Cross-Instance Sync → Add sync target**:
base URL `http://dispatcharr-b-web:9191`, B's superuser credentials.

`sync_logos` has **no control in the ECM UI**; set it over the API:

```bash
curl -X PUT http://127.0.0.1:6300/api/sync-targets/<id> \
  -H 'Content-Type: application/json' -d '{"sync_logos": true}'   # + auth cookie
```

Then **Sync now (preview)** → the **Apply** button appears → **Apply**. Repeat
for the idempotency cycle.

### 6. Smoke-test the instruments BEFORE reading any result

- **Playwright**: navigate to a dead port and confirm it errors, and read a
  known-empty surface (B's `Logos (0 logos)`) before the run so a later non-zero
  reading means something.
- **The sync report**: confirm it can say FAILURE at all. Point the target at B
  with a deliberately **wrong password** and run a preview, then repeat with the
  correct one. Since `jqfxm` (branch `feat/dbas-sync-gaps`) the wrong-password
  preview is itself the working known-bad half: it answers `success=False`,
  `error=SYNC_DESTINATION_UNREADABLE`, notification `type=error`, and the
  **Apply button is not rendered**; the correct-password preview answers
  `success=True` with a would-create plan and Apply appears. Both poles from the
  same control, so no separate failure proof is needed. A dead port gives the
  third shape, `"could not be reached (ConnectError)"`.
- **Every other instrument** (the collection dumper, the md5 comparison, the
  normalize-and-diff idempotency check) gets the same treatment before it is
  armed: run it against a known-good AND a known-bad input and read its output,
  never its exit status. A dumper that degrades an unreachable or unauthorized
  host to "0 rows" is indistinguishable from a dumper reading a genuinely empty
  B — make it exit non-zero instead, and prove that it does.

### 7. Verify by opening B, not by reading A's report

```bash
# with a B access token in $TOKB
for ep in /api/channels/channels/ /api/channels/streams/ /api/channels/groups/ \
          /api/channels/profiles/ /api/channels/logos/ /api/core/useragents/ \
          /api/core/streamprofiles/ /api/m3u/accounts/ /api/epg/sources/; do
  echo "### $ep"; curl -s -H "Authorization: Bearer $TOKB" "http://127.0.0.1:9602$ep"
done
```

For logos, compare the **bytes**, not the record — that is what `cfxml` is
about, and confirm ECM's own upload dir is still empty:

```bash
docker exec dbas-sync-testenv-dispatcharr-a-web-1 md5sum /data/logos/*
docker exec dbas-sync-testenv-dispatcharr-b-web-1 md5sum /data/logos/*
curl -s -H "Authorization: Bearer $TOKB" \
  http://127.0.0.1:9602/api/channels/logos/1/cache/ | md5sum
docker exec ecm-xdmru ls -la /config/uploads/logos/     # must be EMPTY
```

For idempotency, dump every collection before and after a repeat apply and
`diff` them (normalize `created_at`/`updated_at`/`last_seen`/`last_message` and
the `?v=` cache-buster first) — the report's own counters are the thing under
test, so they cannot be the evidence.

### Known issues this recipe will hit

Fixed on `feat/dbas-sync-gaps` (`b867538a`, `0.18.1-0127`) and re-verified live
by the `boz2h` run below — the three bullets that used to stand here (a custom
User-Agent aborting the apply, a wrong-password preview reporting success, and
the unplayable downgrade firing only on the creating cycle) are gone. What is
still live:

- **The channel→logo binding does not cross.** The logo record and bytes land on
  B; every channel there still shows no logo and B's Logo Manager reads
  `UNUSED`. Confirmed still true on `b867538a`. `cfxml`'s scope was the BYTES,
  and those now cross correctly; the binding is separate and open.
- **The `stream` category reports `updated N` on a converged destination** while
  B's stream rows do not change (their `updated_at` / `last_seen` do not move).
  The counter overstates; the destination does not churn. Cosmetic, not a
  convergence failure — but do not read `updated 0` as the idempotency criterion,
  read the before/after dump diff.

---

## Final ten-fix validation, B reset to empty (bead `boz2h`)

**VALIDATED 2026-08-20** against Dispatcharr **0.28.2** (A and B) and a
disposable ECM **rebuilt from `feat/dbas-sync-gaps` HEAD `b867538a`**.

### Prove what is RUNNING, not what the image says it is

`ecm-xdmru` has had individual modules `docker cp`'d into it by several
engineers, so its baked-in `git_commit` is BUILD METADATA AND NOT EVIDENCE.
Rebuild the image, then checksum the running tree against git:

```bash
docker build -t ecm-xdmru:$(git rev-parse --short=8 HEAD) \
  --build-arg ECM_VERSION=<version> --build-arg GIT_COMMIT=<sha> \
  --build-arg RELEASE_CHANNEL=test -f Dockerfile .
docker rm -f ecm-xdmru && docker run -d --name ecm-xdmru ... ecm-xdmru:<sha>

# EVERY non-test backend .py, container vs `git show HEAD:` — not a spot check
git ls-tree -r --name-only HEAD backend/ | grep '\.py$' | grep -v '^backend/tests/' |
while read f; do
  g=$(git show "HEAD:$f" | sha256sum | cut -d' ' -f1)
  c=$(docker exec ecm-xdmru sha256sum "/app/${f#backend/}" | cut -d' ' -f1)
  [ "$g" = "$c" ] || echo "MISMATCH $f"
done
```

The `boz2h` run compared 244 files this way: 244 OK, 0 mismatched, 0 absent.

### Reset B to empty rather than reasoning about accumulated state

```bash
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml \
  rm -sf dispatcharr-b-web dispatcharr-b-celery dispatcharr-b-db dispatcharr-b-redis
docker volume rm dbas-sync-testenv_dbas-sync-b-data dbas-sync-testenv_dbas-sync-b-pg
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml up -d \
  dispatcharr-b-db dispatcharr-b-redis dispatcharr-b-web dispatcharr-b-celery
# then re-create B's superuser (see the 0.28.2 createsuperuser gotcha above)
```

A fresh 0.28.2 B holds: 0 channels / streams / groups / profiles / logos /
EPG sources / server groups, **3 user agents (ids 1-3)**, 5 stream profiles,
1 M3U account (`custom`). Record that baseline — the id ranges are what make the
FK checks below non-vacuous.

### The A-side fixtures each fix needs, and why their ids matter

| Fixture on A | Proves | Id discipline |
|---|---|---|
| Custom user agent + a stream profile referencing it | `hiacv` | Use an agent whose pk is **outside B's 1-3 range** (the run used pk 21 → B assigned 6). An agent that lands on the SAME pk on both sides cannot distinguish a remap from a raw forward — A's `XDMRU Custom Agent` is pk 4 on both instances and is a FALSE GREEN for this check on its own |
| M3U account with a custom user agent | `9h6cv` | Same rule — the run used agent pk 20 on A → pk 5 on B |
| M3U account with `server_group` populated | `g8tyd` | Create a `ServerGroup` on A (`POST /api/m3u/server-groups/`); B has zero, so any pk is outside its range (the run used 21) |
| A Dispatcharr-hosted logo bound to a channel | `cfxml` | Upload through **A's** Logo Manager so the bytes live in A's `/data/logos/`, never in ECM's `/config/uploads/logos/` |
| A channel with `streams: []` | `15g1j` (faithful half) | Must NOT be counted — the archive carries no streams for it |
| A channel bound to a **URL-less** stream (`url: ""`) | `kcfru` / `daziw` negative control | Must be counted, on EVERY cycle |
| Two near-name channels (`XDMRU News One` / `Two`) | `efvyg` | See the fuzzy pair below |

### The cycles the run drove, and what each proved

| # | Setup | Read on B |
|---|---|---|
| 0a | preview, deliberately WRONG password | `success=False`, `SYNC_DESTINATION_UNREADABLE`, notification `type=error`, **no Apply button rendered**, and B's own log shows exactly ONE `POST /api/accounts/token/ 401` — not the eight the pre-fix path made |
| 0b | preview, `base_url` on a dead port | `success=False`, `"the destination could not be reached (ConnectError)"`, zero categories |
| 0c | preview, CORRECT password | `success=True`, "would create 20, update 0, skip 9", `publish Apply` appears. Both poles from the same control — this pair is the report's own smoke test |
| 1 | empty B, apply | 22 created; every fix's artifact present; `0` unplayable |
| 2 | repeat, B untouched | `created 0`; normalized before/after dump **byte-identical**; the only raw field change anywhere was B's `accounts/users.last_login` (the sync's own login) |
| 3 | delete B's `XDMRU News One` channel AND its stream, leaving `XDMRU News Two` as the only near-name candidate; `fuzzy_stream_matching=false` | channel re-created and bound to a fresh `XDMRU News One` placeholder (`news-one.ts`). Rebind log: `1 placeholder stream(s) created this run` … `0 slot(s) rebound` — the pass HAD a fuzzy-matchable candidate and declined it |
| 4 | same shape, `fuzzy_stream_matching=true` | channel bound to `XDMRU News Two` (`news-two.ts`) — the wrong binding, reproduced on demand. This is the known-bad half that proves the cycle-3 reading is not vacuous. **Both cycles reported `outcome: success, failed 0`**, which is exactly why the report cannot be the evidence |
| 5 | add the URL-less channel to A, apply | `completed_with_failures`, `channels_with_no_playable_stream: 1`, named `XDMRU Dead Slot`; `XDMRU Empty Slot` NOT named |
| 6, 7 | repeat, B unchanged | the SAME verdict both times — `1`, same channel. Steady state, not a creating-cycle artifact |
| 8 | add a non-aliasing agent + profile to A | B's `XDMRU Probe Profile` created with `user_agent=6` resolving to `XDMRU Profile FK Probe UA` (A's pk 21) |

### Pacing

B throttles `/api/accounts/token/` to roughly **3 requests per minute**. Cache
the JWT in every instrument and reuse it; a self-inflicted `429` surfaces as
`partial_failed_rolled_back` and has twice been misread as a code failure. One
operation at a time — never reset or re-cycle while a proof is mid-run.

### What this recipe still does NOT exercise

- **`if05f` (users importer `channel_profiles` remap).** `users` is in
  `SYNC_NEVER_CATEGORIES`, so no sync cycle can reach the users importer — this
  is a RESTORE-path fix and the sync UI structurally cannot cover it. It was
  live-proven at the ECM-importer↔Dispatcharr seam by its own bead, not by an
  end-to-end restore run.
- **`15g1j`'s undelivered half.** Reaching it live needs a fault injection at
  `importers/channels._attach_streams`; it is unit-proven only. The obvious
  shortcut does NOT work and was tried: PATCHing a converged B channel to
  `streams: []` while A still carries one is HEALED by the channel importer on
  the next cycle (it re-attaches the stream) before the verdict runs, so the
  undelivered shape never exists at the point the counter is taken.

### State this run leaves behind

A keeps the probe fixtures (`XDMRU SG Probe` + `ServerGroup` 21, `XDMRU UA
Probe` on agent 20, `XDMRU Profile FK Probe UA` 21 + `XDMRU Probe Profile`,
`XDMRU Empty Slot`, and `XDMRU Dead Slot` on a URL-less stream), and B is
converged onto them. The `XDMRU Dead Slot` channel means **every subsequent
cycle reports `completed_with_failures` with `1` unplayable** — that is the
negative control working, not a regression. Delete A's `XDMRU Dead Slot` channel
and its stream to return the stack to an all-green steady state, or tear the
whole thing down below.

### Tear down

```bash
docker rm -f ecm-xdmru provider-xdmru
docker volume rm ecm-xdmru-config
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml down -v
docker image rm ecm-xdmru:<sha>     # optional
```

---

# HANDOVER — the documentation environment (beads `gk4d0`, `xvuk1`)

**BUILT 2026-08-20. REBUILT FROM SCRATCH 2026-08-20 on Dispatcharr 0.29.0**
(bead `enhancedchannelmanager-xvuk1`) — the first build was on 0.28.2, which
`latest` has moved past. Every count, id and md5 below was re-measured on the
rebuild; where the two builds differ it is called out.

**THE PLATFORM THIS WAS BUILT AND MEASURED ON:**

| Node | `GET /api/core/version/` | Image |
|---|---|---|
| Dispatcharr A (source) | `0.29.0` | `ghcr.io/dispatcharr/dispatcharr:latest` = `3621ebe3` |
| Dispatcharr B (destination) | `0.29.0` | same |
| Dispatcharr P (XC provider) | `0.29.0` | same |
| ECM (`ecm-docenv`) | `origin/dev` `dd77d587` | `ecm-docenv:dd77d587` |

`latest` resolved to digest `sha256:df768adc…`, identical on docker.io and
ghcr.io. Read the version endpoint again yourself before you shoot anything —
the tag floats, and a screenshot that cannot name its platform is not evidence.

Everything below is live and disposable. This section is
the contract between the QA engineer who built the environment and the
technical writer who will screenshot it — it is not a user guide, and it does
not tell you what to write.

The whole point of the build is that the writer can document the **Xtream
Codes** path against a **real XC server**, not a hand-rolled fake. Dispatcharr
implements the XC *server* side itself (`xc_player_api` / `xc_panel_api` /
`get.php` / `xmltv.php`, routed in `dispatcharr/urls.py`), so a third
Dispatcharr — **P** — loaded with a lineup and re-serving it over XC produces
genuine XC fields. Instance **A** subscribes to P as an `account_type = XC`
account, and every XC value ECM renders (expiry, connection counts, category
list) came out of that real implementation.

```
  provider-northwind (nginx: playlist.m3u, local.m3u, epg.xml, local-epg.xml, 59 logos)
        |                                        |
        |  STANDARD M3U                          |  STANDARD M3U (local.m3u only)
        v                                        v
   Dispatcharr P  ---- XTREAM CODES ---->   Dispatcharr A  <---- reads ----  ECM
   (the provider)                           (the operator's                  (ecm-docenv)
                                             instance)                          |
                                                                                | cross-instance sync
                                                                                v
                                                                          Dispatcharr B
                                                                          (destination, EMPTY)
```

## NO REAL CREDENTIALS ANYWHERE

Every credential in this environment is synthetic and chosen to read as fake in
a screenshot (`northwind-demo` / `not-a-real-password`, `ecmtest-a` /
`ecmtestpass-a`, and so on). The provider, the channel names and the listings
are all invented. Keep it that way: per bead
`enhancedchannelmanager-wfz8z`, a live `mcp_api_key` was once found rendered
inside a committed image, invisible to gitleaks, detect-secrets and GitHub
secret scanning alike — **byte scanners cannot see pixels.** If you need a new
credential for a screenshot, invent one that is obviously fake; never paste a
real one in "just to see how it looks".

## URLs, ports, containers, credentials

| What | Host URL | In-cluster address | Container | Credentials |
|---|---|---|---|---|
| **ECM** (under test) | `http://127.0.0.1:6400` | — | `ecm-docenv` | `ecm-demo-admin` / `Docs-Demo-Not-Real-2026!` |
| **Dispatcharr A** — source | `http://127.0.0.1:9601` | `http://dispatcharr-a-web:9191` | `dbas-sync-testenv-dispatcharr-a-web-1` | `ecmtest-a` / `ecmtestpass-a` |
| **Dispatcharr B** — sync destination | `http://127.0.0.1:9602` | `http://dispatcharr-b-web:9191` | `dbas-sync-testenv-dispatcharr-b-web-1` | `ecmtest-b` / `ecmtestpass-b` |
| **Dispatcharr P** — XC provider | `http://127.0.0.1:9603` | `http://dispatcharr-p-web:9191` | `dbas-xc-provider-dispatcharr-p-web-1` | admin `ecmtest-p` / `ecmtestpass-p` |
| **P's XC account** (what A subscribes with) | — | — | — | `northwind-demo` / `not-a-real-password` |
| **provider-northwind** (nginx origin) | *no host port* | `http://provider-northwind/` | `dbas-xc-provider-provider-northwind-1` | none |

Postgres ports, if you ever need them: A `5446`, B `5447`, P `5448`. All three
Dispatcharr instances run **0.29.0** (see the table at the top of this section).

ECM's own config volume is `ecm-docenv-config`; the image is
`ecm-docenv:dd77d587`. ECM's first-run setup is already complete and it is
already pointed at A (`http://dispatcharr-a-web:9191`, password auth,
`ecmtest-a`). The nginx document root is a **generated** fixture at
`/home/lecaptainc/ecm-docenv-fixture` (see "Regenerating the fixture" below) —
it is deliberately outside the repo and is not committed.

### The XC values ECM will render

Read back from A after its first XC refresh, harvested from P's real XC API:

```
status "Active" | exp_date 2026-11-18 (90 days from first auth)
active_cons "0" | max_connections "4" | allowed_output_formats ["ts","mp4"]
```

Re-measured on 0.29.0: `player_api.php` with the right credentials returns
exactly that; `get_live_categories` → 7, `get_live_streams` → 53,
`get_vod_streams` / `get_series` → `[]`, `xmltv.php` → 200 / ~756 KB,
`get.php?type=m3u_plus` → 200 / ~13.7 KB. A wrong password and no credentials
both return **HTTP 401** `{"error":"Unauthorized"}`.

`max_connections` is 4 because P's `northwind-demo` user has `stream_limit = 4`
— change the user on P if you want a different number in the shot.

## What is on instance A, by name and count

**59 channels / 59 streams / 10 channel groups** (9 populated + Dispatcharr's
always-present `Default Group`, which holds 0 channels). Every channel has a
logo and a `tvg_id`, and **all 59 are linked to EPG data**.

### Channel groups and numbering

| Group | Channels | Numbers | Comes from |
|---|---|---|---|
| Northwind News | 8 | 100–107 | XC account |
| Northwind Sports | 10 | 200–209 | XC account |
| Northwind Movies | 9 | 300–308 | XC account |
| Northwind Kids | 6 | 400–405 | XC account |
| Northwind Documentary | 7 | 500–506 | XC account |
| Northwind Entertainment | 8 | 600–607 | XC account |
| Northwind Music | 5 | 700–704 | XC account |
| Northwind Local | 3 | 800–802 | Standard M3U account |
| Northwind Regional | 3 | 850–852 | Standard M3U account |

### The full channel list (safe to caption verbatim)

```
News           100 Meridian News · 101 Meridian News HD · 102 Capitol Report
               103 Global Wire · 104 Beacon Business · 105 Continental 24
               106 Harbour Weather · 107 The Briefing
Sports         200 Summit Sports 1 · 201 Summit Sports 2 · 202 Summit Sports HD
               203 Velodrome TV · 204 Gridiron Network · 205 Pitchside FC
               206 Court Vision · 207 Outdoor Pursuits · 208 Paddock Live · 209 Ringside
Movies         300 Silverline Cinema · 301 Silverline Classics · 302 Silverline Action
               303 Nightscreen Thrillers · 304 Matinee Family · 305 Indie Reel
               306 Westward Westerns · 307 Orbit Sci-Fi · 308 Silverline 4K
Kids           400 Sprout Junction · 401 Cartoon Cove · 402 Little Explorers
               403 Storybook TV · 404 Puzzle Pals · 405 Dino Dash
Documentary    500 Terra Discovery · 501 Wild Frontier · 502 History Vault
               503 Deep Ocean · 504 Cosmos Files · 505 Engineering Marvels · 506 Culture Trail
Entertainment  600 Primetime Plus · 601 Sitcom Central · 602 Reality Row
               603 Talk of the Town · 604 Stage & Screen · 605 Retro Rewind
               606 Lifestyle Loft · 607 Comedy Corner
Music          700 Amp Rock · 701 Cadence Classical · 702 Groove Lounge
               703 Chart Pulse · 704 Bayou Country
Local          800 Harbour City TV · 801 Lakeside Local · 802 Riverbend Community
Regional       850 Northern Counties · 851 Coastal Region One · 852 Valley Public
```

### Everything else on A

> **The ids below are from the 2026-08-20 0.29.0 rebuild and are LOWER than
> the ones the first build recorded** (the first build created and deleted
> objects on the way, so its ids had gaps). If a doc or bead quotes M3U account
> `6`, EPG source `2` or logo `61`, it is quoting the retired 0.28.2 build.

| Thing | Count | Names / ids |
|---|---|---|
| M3U accounts | 3 | id 2 **`Northwind IPTV (Xtream Codes)`** (`account_type = XC`, `server_url http://dispatcharr-p-web:9191`, user `northwind-demo`, 53 streams); id 3 `Northwind Local Affiliates (Standard M3U)` (`STD`, 6 streams); id 1 `custom` (Dispatcharr's locked built-in) |
| EPG sources | 2 | id 1 `Northwind IPTV EPG (Xtream Codes)` — 2,884 programmes / 53 channels; id 2 `Northwind Local XMLTV` — 421 programmes / 6 channels |
| Channel profiles | 2 | id 1 `Living Room` (all 59 channels); id 2 `Kids & Family` (6 channels — the Kids group only) |
| Stream profiles | 6 | 5 Dispatcharr built-ins (ids 1-5) + id 6 `Northwind Direct (ffmpeg)`, which references the custom user agent |
| User agents | 4 | 3 built-ins (ids 1-3: TiviMate, VLC, Chrome) + id 4 `Northwind Set-Top Box` (`NorthwindSTB/2.4 (Linux; documentation-environment)`) |
| Logos | 60 | 59 remote-URL logos from the provider feed, **plus id 60 `Meridian News (hosted on Dispatcharr)`** |
| Channel groups | 10 | id 1 `Default Group` (0 channels) + ids 2-10 in lineup order: News, Sports, Movies, Kids, Documentary, Entertainment, Music, Local, Regional |
| Server groups | 0 | — |
| Output profiles | 2 | ids 1-2, `Media Server (AC3 Audio)` and `Web Player (AAC Audio)` — Dispatcharr built-ins on 0.28.2 **and** 0.29.0. The first build never recorded these; they are not new |
| Dispatcharr users | 1 | `ecmtest-a` |

The XC EPG programme count moves with the clock: the fixture XMLTV covers 3 days
from 00:00 UTC, and P's `xmltv.php` re-serves only the window it still holds, so
2,884 today is not a contradiction of the 2,937 the first build saw.

**The Dispatcharr-hosted logo** is id 60, bound to channel **100 Meridian
News**. Its bytes live in **A's own** `/data/logos/meridian-news-hosted.png`
(md5 `f7e3278711ad164d1fd9c1e6324d81bc`, 7,180 bytes — the fixture's
`logos/meridian-news.png` uploaded verbatim; the retired build's copy was a
re-encode, md5 `3ba03ea3…`) — not in ECM's `/config/uploads/logos/`, which is
empty and must stay that way. That distinction is the whole point of bead
`cfxml`; if you re-upload a logo, upload it through **A's** Logo Manager.

## What is on instance P (you will rarely need to open it)

53 channels / 53 streams / 8 groups (7 populated + `Default Group`), all with
logos and all linked to EPG, numbered exactly as A numbers them (100-107,
200-209, 300-308, 400-405, 500-506, 600-607, 700-704). One STD M3U account
id 2 `Northwind Origin Feed` reading the nginx `playlist.m3u`, one EPG source
id 1 `Northwind XMLTV` (3,608 programmes / 53 channels), one custom user agent
id 4 `Northwind Fixture Agent`, and two users: the admin `ecmtest-p` (id 1) and
the XC-facing `northwind-demo` (id 2, `stream_limit = 4`, `user_level = 1`,
`custom_properties.xc_password`).

P exists only to be a believable provider. The writer's screenshots are of ECM
and, where the guide needs them, of A and B.

## Instance B — the sync destination, currently EMPTY

B was reset **after** the 0.29.0 fidelity measurement below and holds the
fresh-0.29.0 baseline, **which is not zero of everything** — record these as
"empty" when captioning. Every number here was read straight out of B's
Postgres, not from an API:

```
0 channels · 0 streams · 0 channel groups · 0 channel profiles · 0 logos
0 EPG sources · 0 server groups · 0 EPG programmes
3 user agents (ids 1-3: TiviMate, VLC, Chrome)
5 stream profiles (ids 1-5: ffmpeg, streamlink, Proxy, Redirect, VLC)
2 output profiles (ids 1-2: Media Server (AC3 Audio), Web Player (AAC Audio))
1 M3U account (id 1, `custom`, Dispatcharr's locked built-in)
1 user (`ecmtest-b`)
```

Those id ranges matter: an artifact that lands on B with an id **outside** 1-3
(user agents), 1-5 (stream profiles) or 1 (M3U accounts) is one the sync
created, not one that was already there. Note `Default Group` is **not**
present on a fresh instance — it appears the moment something creates an M3U
account, so its arrival on B is a sync side effect, not a pre-existing row.

**The destination is unspent.** The one apply-mode run recorded below was
followed by a full container+volume reset, so nothing on B has been written by
a sync.

**No sync target exists in ECM.** That is deliberate — creating it is part of
the workflow you are documenting, so you get to walk and shoot that path
yourself. Point it at `http://dispatcharr-b-web:9191` with B's credentials.

### Resetting B between screenshot runs

You will want a clean destination more than once. From `tests/dbas-test-env`:

```bash
docker compose -p dbas-sync-testenv \
  -f docker-compose.dbas-sync-test.yml \
  rm -sf dispatcharr-b-web dispatcharr-b-celery dispatcharr-b-db dispatcharr-b-redis
docker volume rm dbas-sync-testenv_dbas-sync-b-data dbas-sync-testenv_dbas-sync-b-pg
docker compose -p dbas-sync-testenv \
  -f docker-compose.dbas-sync-test.yml up -d \
  dispatcharr-b-db dispatcharr-b-redis dispatcharr-b-web dispatcharr-b-celery

# wait for health, then RE-CREATE THE SUPERUSER — the volume took it with it:
until curl -fsS http://127.0.0.1:9602/api/core/version/ >/dev/null; do sleep 5; done
docker exec dbas-sync-testenv-dispatcharr-b-web-1 sh -c '
  cd /app && export DJANGO_SECRET_KEY="$(tr -d "\r\n" < /data/jwt)" && \
  DJANGO_SUPERUSER_USERNAME=ecmtest-b \
  DJANGO_SUPERUSER_PASSWORD=ecmtestpass-b \
  DJANGO_SUPERUSER_EMAIL=ecmtest-b@example.invalid \
  python manage.py createsuperuser --noinput'
```

**Pass no version flag.** The compose default is `latest`, which is what A and
P are running, so a bare reset brings B back on the same platform they are on.
Confirm it anyway — `curl -s http://127.0.0.1:9602/api/core/version/` — and
record the answer with whatever you measured. (This used to read
"`DISPATCHARR_VERSION=0.28.2` is not optional", because the old default was
`0.26.0` and a reset silently came back a minor version behind A. That default
is gone; the instruction is now the opposite one.)

After a reset, ECM's existing sync target still points at B correctly, but B's
JWT changed — the next run re-authenticates on its own.

## Traps — read before you spend an hour on one of these

**1. B throttles `/api/accounts/token/` to roughly 3 requests per minute.**
**Re-measured on 0.29.0, 2026-08-20 — it carried over, do not assume it stays
that way.** Sequential token POSTs: requests 1-3 returned `200`, request 4
returned `429` `{"detail":"Request was throttled. Expected available in 57
seconds."}`. 0.29.0 ships a NEW `apps/accounts/throttling.py`, so this is a
re-implementation that happens to land on the same rate — re-measure it after
every platform move rather than quoting this paragraph.

Pace anything that authenticates against B, and cache the token rather than
re-authenticating per request. A self-inflicted `429` surfaces as
`partial_failed_rolled_back` and has been misread as a code failure twice. One
operation at a time; never reset B while a run is in flight. The same throttle
is live on A and P — a script that re-authenticates per process (rather than
caching the token to disk) will trip it against P during a fixture rebuild.

**2. A fresh Dispatcharr can import an entire playlist as ONE stream.**
**Still live on 0.29.0 — confirmed 2026-08-20 on ALL THREE fresh instances.**
Immediately after first boot, A, B and P each had `m3u_hash_key: "url"` in
Postgres and `m3u_hash_key: ""` in the Redis cache. Bust the cache on every
fresh instance **before the first M3U refresh**; it is a required build step,
not a rescue. This cost real time during the first build. Dispatcharr caches its settings groups in
Redis, and on a first boot the `stream_settings` group can be cached **before**
the migration writes it — so `m3u_hash_key` reads as `""` instead of `"url"`,
`"".split(",")` yields `[""]`, the per-stream hash payload is the empty dict,
and every stream in the playlist hashes to `sha256("{}")` =
`44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a` and
deduplicates down to a single row. P imported **1 of 53 streams** this way and
reported `status: success`. If a refresh reports far fewer streams than the
playlist has, check for that hash before suspecting the playlist:

```bash
# the DB says one thing...
docker exec dbas-xc-provider-dispatcharr-p-db-1 psql -U dispatch -d dispatcharr \
  -tAc "select value from core_coresettings where key='stream_settings';"
# ...and the cache says another. Bust it:
docker exec dbas-xc-provider-dispatcharr-p-redis-1 \
  redis-cli DEL ':1:coresettings:group:stream_settings'
```
Then delete the collapsed stream(s) and refresh again. A and P are both correct
right now; this only bites a freshly created instance.

**3. XC-imported channels do not carry the origin's `tvg-id`.** P's XC API
publishes `epg_channel_id` as the **channel number** (`"100"`, `"101"`, …), not
`meridian-news.northwind.example`. So on A, the 53 XC channels have
`tvg_id = "100"`-style ids and match **P's XC XMLTV**
(`http://dispatcharr-p-web:9191/xmltv.php?username=…&password=…`), which is
exactly what EPG source id 1 points at (id 2 on the retired 0.28.2 build).
**Re-confirmed on 0.29.0.** The 6 Standard-M3U channels keep the
real slug ids and match `local-epg.xml`. Do not "fix" A by pointing the XC
lineup at `http://provider-northwind/epg.xml` — nothing will match.

**4. Dispatcharr returns `403`, not `404`, for an API path that does not
exist.** `/api/m3u/accounts/2/refresh/` and
`/api/channels/channels/from-stream/bulk` (no trailing slash) both answer 403
and look like a permissions problem. The real routes are
`/api/m3u/refresh/<account_id>/` and
`/api/channels/channels/from-stream/bulk/`. When a Dispatcharr call 403s,
check the path before checking the token.

**5. Bulk channel-profile membership is `PATCH`, not `POST`.**
`/api/channels/profiles/<id>/channels/bulk-update/` answers 405 to a POST.

**6. `createsuperuser` needs `DJANGO_SECRET_KEY` exported.** A bare `manage.py`
fails with "SECRET_KEY must not be empty"; the entrypoint derives it from
`/data/jwt`. The reset recipe above already does this.

**7. ECM's first-run setup rejects `.invalid` email addresses.** `EmailStr`
refuses reserved TLDs, so `admin@example.invalid` 422s. Use `example.com`.

**8. ECM caches Dispatcharr's channel-group list in-process.** If stream rows
render empty under group headers that show non-zero counts, the cache was
populated when A had no groups. `docker restart ecm-docenv` clears it.

**9. "Create channels from this group" is a silent no-op on a COLLAPSED group.**
Expand the group first. The bulk-create modal renders in a portal — search the
whole page for it, not just the Channels pane.

**10. Dispatcharr's bulk channel creation is asynchronous.** The POST returns a
`task_id` immediately; poll `/api/channels/channels/` until the count settles
rather than reading the response as the result.

**11. SCREENSHOT HAZARD — Dispatcharr's sidebar renders the host's REAL PUBLIC
IP.** A previous capture of B included it. Nothing in CI can catch this: a byte
scanner cannot see pixels (bead `enhancedchannelmanager-wfz8z`, where a live
`mcp_api_key` was found inside a committed image, invisible to gitleaks,
detect-secrets and GitHub secret scanning alike). **Suppress or crop it on
EVERY capture of B and of P**, and check A too. Crop the sidebar out, or blank
the element in devtools before the shot — do not rely on noticing it later.

**12. `/api/version`'s `git_commit` is baked-in build metadata, not what is
running.** It has misled three engineers on this work. Prove the deployed tree
by checksum instead — see the verification table at the end of this section.

## Regenerating the fixture

The nginx document root is generated, not committed (1.7 MB, mostly logos). The
**generator** is committed as `northwind-fixture.py` and reproduces the
playlists and all 59 logos byte for byte:

```bash
python3 tests/dbas-test-env/northwind-fixture.py /home/lecaptainc/ecm-docenv-fixture
```

The two XMLTV files are anchored to **today in UTC** and cover 3 days, so the
listings go stale. When they do, re-run the generator and re-import all three
EPG sources (P id 1; A ids 2 and 3) — the channel ids are stable, so existing
EPG links survive:

```bash
# with a Dispatcharr access token in $TOK
curl -s -X POST -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"id":1}' http://127.0.0.1:9603/api/epg/import/
```

Changing the lineup itself (adding channels, renaming groups) means re-running
the generator **and** re-refreshing P's M3U account, then A's — the counts in
this handover would then be wrong, so update them here too.

## Bringing the environment back up from cold

If the host reboots, or containers with `RestartPolicy=no` get stopped:

```bash
cd tests/dbas-test-env

# A and B (no version flag — the compose default is `latest`):
docker compose -p dbas-sync-testenv \
  -f docker-compose.dbas-sync-test.yml up -d

# P and the nginx origin (joins A/B's network, so bring A/B up FIRST):
NORTHWIND_FIXTURE_DIR=/home/lecaptainc/ecm-docenv-fixture \
  docker compose -p dbas-xc-provider -f docker-compose.xc-provider.yml up -d

# ECM:
docker start ecm-docenv
until curl -fsS http://127.0.0.1:6400/api/health/ready >/dev/null; do sleep 3; done
```

All persistent state is in named volumes, so nothing needs rebuilding.

**A bare `up -d` reuses the locally-cached `latest`.** That is deliberate — it
keeps a cold restart from silently moving the platform under a screenshot run in
progress. When you *do* mean to be current, pull first and then re-read every
version endpoint:

```bash
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml pull
NORTHWIND_FIXTURE_DIR=/home/lecaptainc/ecm-docenv-fixture \
  docker compose -p dbas-xc-provider -f docker-compose.xc-provider.yml pull
for port in 9601 9602 9603; do curl -s "http://127.0.0.1:$port/api/core/version/"; echo; done
```

If ECM's container is gone rather than merely stopped, this is the run line it
was created with:

```bash
docker run -d --name ecm-docenv \
  --network dbas-sync-testenv_default \
  -p 127.0.0.1:6400:6400 \
  -e ECM_PORT=6400 -e ECM_HTTPS_PORT=6443 -e CONFIG_DIR=/config -e PUID=1000 -e PGID=1000 \
  -v ecm-docenv-config:/config \
  ecm-docenv:dd77d587
```

## Tearing the whole thing down

```bash
docker rm -f ecm-docenv
docker volume rm ecm-docenv-config
docker image rm ecm-docenv:dd77d587                      # optional

cd tests/dbas-test-env
docker compose -p dbas-xc-provider  -f docker-compose.xc-provider.yml  down -v
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml down -v

rm -rf /home/lecaptainc/ecm-docenv-fixture               # the generated fixture
```

Tear down the `dbas-xc-provider` project **before** `dbas-sync-testenv`: P and
nginx attach to `dbas-sync-testenv_default` as an external network, and Docker
will refuse to remove a network that still has endpoints on it.

**Never touch `ecm-ecm-1`, `ecm-ecm-mcp-1`, or the `dispatcharr` container.**
Those are the operator's production stack. `ecm-ecm-1` and `ecm-ecm-mcp-1` are
currently STOPPED and must stay stopped; production `dispatcharr` is running on
its own network (`dispatcharr-green_default`) and is not part of this
environment.

## What was verified when this was built, and how

Recorded so a later reader can tell a checked claim from an assumed one. Every
row below was re-run on the **2026-08-20 0.29.0 rebuild** — this is not the
0.28.2 table carried forward.

| Claim | How it was checked |
|---|---|
| Every node is on `0.29.0` | `GET /api/core/version/` on A (`:9601`), B (`:9602`) and P (`:9603`) each returned `{"version":"0.29.0"}`. The image behind all three is `ghcr.io/dispatcharr/dispatcharr:latest` = `3621ebe3`, digest `sha256:df768adc…`, and `docker pull` confirmed docker.io and ghcr.io agree on that digest. |
| The compose default really is `latest`, and the override really works | `docker compose config` resolves `ghcr.io/dispatcharr/dispatcharr:latest` with no variable set, and `ghcr.io/dispatcharr/dispatcharr:0.28.2` with `DISPATCHARR_VERSION=0.28.2` — both poles, all three files. Then proven live: B was reset with **no flag** and came back `0.29.0`, which is exactly the failure the old `0.26.0` default caused. |
| ECM runs `origin/dev` `dd77d587` | **Not** `/api/version` (baked-in build metadata; it has misled three engineers). Two passes, both `sha256sum` inside the container against `git show dd77d587:<path>`: **243 non-test `backend/**.py` → 243 OK / 0 MISMATCH / 0 ABSENT**, and **all 698 `backend/**.py` including tests → 698 OK / 0 MISMATCH / 0 ABSENT**, with **0** `.py` files in `/app` that are not in the ref. (The retired build's "244" counted `backend/test_dispatcharr_api.py`, a top-level test file this filter excludes: 243 + 1 = 244, reconciled.) |
| …and that checksum harness can actually fail | Two known-bad runs, exit status captured directly rather than through a pipe. (1) Compared against `b0e0a5ad`, 30 first-parent commits back → **48 MISMATCH**, harness exit 1. (2) A byte appended to `/app/main.py` in the live container → **exactly 1 MISMATCH** on `main.py`, harness exit 1; restored → **243 OK**, exit 0. (A previous attempt to use the parent commit `b867538a` as the known-bad was **vacuous** — `dd77d587` is its merge commit, so 0 files differ. Vacuous known-bads are the failure this table exists to prevent.) |
| The Dispatcharr client used to build all this could report failure | Smoke-tested against four poles before use: known-good returns real JSON; a nonexistent path, an unreachable host, and a wrong password each raise loudly. It never degrades an error into "0 rows". |
| The DB reader could report failure | The counters below come from `psql` against A's and B's Postgres directly, no API in the path. A bogus table name produces `ERROR: relation ... does not exist` in the cell, not `0`. Its B column went from all-zero to non-zero across the apply and back to all-zero after the reset, so it is not structurally stuck. |
| A's account really is XC | Read `account_type` from **A's Postgres directly** (`select id, name, account_type from m3u_m3uaccount`) → id 2 `XC`, id 3 `STD`, id 1 `STD` — not from a serializer that could be defaulting. |
| **The XC server side survived the 0.29.0 bump** | `dispatcharr/urls.py` still routes `player_api.php` → `xc_player_api`, `panel_api.php` → `xc_panel_api`, `get.php` → `xc_get`, `xmltv.php` → `xc_xmltv`, all still defined in `apps/output/views.py`. The module changed between versions but the surface the provider design depends on did not move, and 0.29.0 *added* XC coverage (`apps/output/tests/test_xc_series_info.py`, `test_xc_vod_info.py`). Then proven live, both poles: `player_api.php` with the right credentials returns a real `user_info` (auth 1, Active, `exp_date` 2026-11-18, `active_cons` 0, `max_connections` 4); a wrong password and no credentials each return **HTTP 401** `{"error":"Unauthorized"}`. `get_live_categories` → 7, `get_live_streams` → 53, `xmltv.php` → 200 / 755,706 bytes, `get.php?type=m3u_plus` → 200 / 13,719 bytes. |
| nginx is serving what we think | From inside P: `playlist.m3u` 200/12,587, `local.m3u` 200/1,499, `epg.xml` 200/1,089,386, a logo 200/7,180 — and a bogus path **404**. Both poles. |
| The committed generator reproduces the fixture | `northwind-fixture.py` was re-run into a wiped directory and produced 53 + 6 channels and **59** logos, the same lineup the handover captions. |
| ECM is actually talking to A | `/api/health/ready` reports `dispatcharr: reachable (HTTP 200)`; `GET /api/channels` returns **count 59**. Login was proven both poles: correct password 200, wrong password **401**. |
| Production is untouched | `ecm-ecm-1` and `ecm-ecm-mcp-1` are still `exited`, `FinishedAt 2026-08-20T03:12`, with no `StartedAt` after it. Production `dispatcharr` is still `Up` on `dispatcharr-green_default` — a different network from `dbas-sync-testenv_default` — on image id `1f55137b`, which it has held since `03:13`. |

### The 0.29.0 replica-fidelity measurement (epic `enhancedchannelmanager-f5a5j`)

Epic `f5a5j` was diagnosed on 0.28.2 and explicitly requires re-confirmation
before any member is treated as diagnosed. **This is that re-confirmation, taken
on 0.29.0.** One apply-mode run, `A → B`, sync target `sync_logos: true`,
`confirm_apply: true`, `cloud_credential_version: 1`. What the run reported:

```
outcome success | 146 items | created 134 | updated 0 | failed 0 | skipped 12 | 3.7s

category            created  updated  skipped  failed
user_agent                1        0        3       0   (3x already_exists_identical)
m3u_account               2        0        1       0   (custom, already_exists_identical)
epg_source                2        0        0       0
channel_group             7        0        3       0   (3x already_exists_name_match)
channel_profile           2        0        0       0
stream_profile            1        0        5       0   (5x already_exists_identical)
channel                  59        0        0       0
stream                   59        0        0       0
logo                      1        0        0       0
```

Both databases read directly afterwards (`psql`, no API in the path):

```
                          A      B
channels                 59     59
streams                  59     59
groups                   10     10
channel profiles          2      2
EPG sources               2      2
channels with an EPG link 59      6
channels with a logo     59      0
logos present at all     60      1
```

EPG source `url`, both sides:

```
A  1 | Northwind IPTV EPG (Xtream Codes) | http://dispatcharr-p-web:9191/xmltv.php?username=northwind-demo&password=not-a-real-password
A  2 | Northwind Local XMLTV             | http://provider-northwind/local-epg.xml
B  1 | Northwind IPTV EPG (Xtream Codes) | <EMPTY>
B  2 | Northwind Local XMLTV             | http://provider-northwind/local-epg.xml
```

**The 0.28.2 finding reproduces exactly on 0.29.0**: the credential-bearing XC
URL is stripped to empty on the destination while the credential-free one
crosses intact. B's EPG source 1 is left in `status = error`, *"No URL provided
and no valid local file exists"*.

What else the direct read showed, all of it measured, none of it fixed here:

- **Channel → logo binding does not cross** (bead `xgbjm`, still open). 59
  channels on A carry a `logo_id`; **0** on B.
- **Only 1 of A's 60 logo records landed on B** — the Dispatcharr-hosted one.
  A's other 59 are remote-URL logos and no record was created for them. The
  retired 0.28.2 note said "logo records and bytes land on B"; on this run they
  did not. Treat that older sentence as superseded, not as a regression claim —
  the two runs differed in `sync_logos` and are not a controlled comparison.
- **EPG programme data does not cross, and B's sources are not re-imported.**
  B ends with 6 `epg_epgdata` rows and **0** `epg_programdata` rows. Its usable
  source imported before any channel was linked, so it still reports *"No
  channels mapped"*.
- **Channel-profile membership *enablement* does not cross.** A: `Living Room`
  59/59 enabled, `Kids & Family` **6**/59 enabled. B: `Living Room` 59/59,
  `Kids & Family` **59**/59. Both profiles arrive with all 59 channels enabled,
  so the destination's `Kids & Family` is not the profile the source has.
- **Provider attribution does not cross.** All 59 streams on B hang off a
  synthesized 4th M3U account, `ECM Custom Streams (DBAS restore)` (`STD`, empty
  `server_url`) — not off the replicated XC account (id 2) or STD account
  (id 3), both of which arrive but hold 0 streams.
- **`Default Group` on B is a sync side effect.** A fresh instance has zero
  channel groups; the group appears once an M3U account is created, and the run
  then reports it as `already_exists_name_match`.

**B was reset to empty immediately after this measurement** (container + volume
destroyed, superuser re-created, `stream_settings` cache busted), the temporary
sync target was deleted, and ECM's notifications were cleared. The destination
the writer receives is unspent.

### What this environment does NOT cover

- **The apply path has now been exercised once, by QA, and B was reset after.**
  The writer's own apply will be the first one whose output is documented. The
  numbers above are what to expect; a disagreement is new information.
- **VOD and Series are empty.** P serves `get_vod_streams` / `get_series` as
  empty lists, so ECM's VOD surfaces have nothing to show. Populating them was
  out of scope — note that 0.29.0 added real VOD/series work
  (`apps/vod/image_proxy.py`, `vod_...0005_movie_is_adult`), so this is a bigger
  gap on this platform than it was on 0.28.2.
- **No stream actually plays.** The `.ts` URLs in the fixture are 404s — nginx
  serves no `/stream/` directory. Everything about lineup management, EPG,
  profiles and sync is real; anything that requires a live video transport
  (probing, bitrate, playback) is not.
- **No screenshots were taken on this rebuild.** Re-shooting the guide is bead
  `enhancedchannelmanager-x4eoi`, which resumes after this. Read trap 11 before
  the first capture.
- **ECM's first-run walkthrough has already been completed here.** The admin
  account exists and the Dispatcharr connection is set. If you need to shoot
  Getting Started from a genuinely blank ECM, do it against a throwaway second
  container rather than resetting this one:
  `docker rm -f ecm-docenv && docker volume rm ecm-docenv-config` then re-run
  the `docker run` from "Bringing the environment back up" with a fresh volume —
  but you will then have to re-create the admin and re-point it at A.
- **Playwright was not exercised on this rebuild**, so unlike the first build
  there is no rendered-browser evidence in the table above. The ECM API was
  proven live (count 59) but nothing rendered a page.
- **`docker-compose.dbas-test.yml`, the single-instance stack, now defaults to
  `latest` too** (PO decision, same bead), but it was **not stood up** on this
  platform — only its resolved config was checked. Its first bring-up on 0.29.0
  is unvalidated; run the first-bring-up checklist above.
