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
DISPATCHARR_VERSION=0.28.2 docker compose -p dbas-sync-testenv \
  -f docker-compose.dbas-sync-test.yml up -d
# A = http://127.0.0.1:9601 (in-cluster: dispatcharr-a-web:9191)
# B = http://127.0.0.1:9602 (in-cluster: dispatcharr-b-web:9191)
```

> **GOTCHA (0.28.2, still true):** `DISPATCHARR_SUPERUSER_*` in the compose file
> is **not honored**. Both instances come up showing the first-run "Create your
> Super User Account" wizard at `/login`. Complete it in the browser on each —
> that is the intended UI-driven path and it takes ten seconds.

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

# HANDOVER — the documentation environment (bead `enhancedchannelmanager-gk4d0`)

**BUILT 2026-08-20.** Everything below is live and disposable. This section is
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
Dispatcharr instances run **0.28.2**.

ECM's own config volume is `ecm-docenv-config`; the image is
`ecm-docenv:dd77d587`. The nginx document root is a **generated** fixture at
`/home/lecaptainc/ecm-docenv-fixture` (see "Regenerating the fixture" below) —
it is deliberately outside the repo and is not committed.

### The XC values ECM will render

Read back from A after its first XC refresh, harvested from P's real XC API:

```
status "Active" | exp_date 2026-11-18 (90 days from first auth)
active_cons "0" | max_connections "4" | allowed_output_formats ["ts","mp4"]
```

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

| Thing | Count | Names / ids |
|---|---|---|
| M3U accounts | 3 | id 6 **`Northwind IPTV (Xtream Codes)`** (`account_type = XC`, `server_url http://dispatcharr-p-web:9191`, user `northwind-demo`, 53 streams); id 7 `Northwind Local Affiliates (Standard M3U)` (`STD`, 6 streams); id 1 `custom` (Dispatcharr's locked built-in) |
| EPG sources | 2 | id 2 `Northwind IPTV EPG (Xtream Codes)` — 2,937 programmes / 53 channels; id 3 `Northwind Local XMLTV` — 421 programmes / 6 channels |
| Channel profiles | 2 | id 2 `Living Room` (all 59 channels); id 3 `Kids & Family` (6 channels — the Kids group only) |
| Stream profiles | 6 | 5 Dispatcharr built-ins + id 8 `Northwind Direct (ffmpeg)`, which references the custom user agent |
| User agents | 4 | 3 built-ins (TiviMate, VLC, Chrome) + id 22 `Northwind Set-Top Box` (`NorthwindSTB/2.4 (Linux; documentation-environment)`) |
| Logos | 60 | 59 remote-URL logos from the provider feed, **plus id 61 `Meridian News (hosted on Dispatcharr)`** |
| Server groups | 0 | — |
| Dispatcharr users | 1 | `ecmtest-a` |

**The Dispatcharr-hosted logo** is id 61, bound to channel **100 Meridian
News**. Its bytes live in **A's own** `/data/logos/meridian-news-hosted.png`
(md5 `3ba03ea3c50f92ec7a8873e7f5b751b9`) — not in ECM's `/config/uploads/logos/`,
which is empty and must stay that way. That distinction is the whole point of
bead `cfxml`; if you re-upload a logo, upload it through **A's** Logo Manager.

## What is on instance P (you will rarely need to open it)

53 channels / 53 streams / 8 groups (7 populated + `Default Group`), all with
logos and all linked to EPG. One STD M3U account `Northwind Origin Feed`
reading the nginx `playlist.m3u`, one EPG source `Northwind XMLTV` (3,608
programmes), one custom user agent `Northwind Fixture Agent`, and two users:
the admin `ecmtest-p` and the XC-facing `northwind-demo`.

P exists only to be a believable provider. The writer's screenshots are of ECM
and, where the guide needs them, of A and B.

## Instance B — the sync destination, currently EMPTY

B is reset and holds the fresh-0.28.2 baseline, **which is not zero of
everything** — record these as "empty" when captioning:

```
0 channels · 0 streams · 0 channel groups · 0 channel profiles · 0 logos
0 EPG sources · 0 server groups
3 user agents (ids 1-3: TiviMate, VLC, Chrome)
5 stream profiles (ids 1-5: ffmpeg, streamlink, Proxy, Redirect, VLC)
1 M3U account (id 1, `custom`, Dispatcharr's locked built-in)
1 user (`ecmtest-b`)
```

Those id ranges matter: an artifact that lands on B with an id **outside** 1-3
(user agents) or 1-5 (stream profiles) is one the sync created, not one that
was already there.

**No sync target exists in ECM.** That is deliberate — creating it is part of
the workflow you are documenting, so you get to walk and shoot that path
yourself. Point it at `http://dispatcharr-b-web:9191` with B's credentials.

### Resetting B between screenshot runs

You will want a clean destination more than once. From `tests/dbas-test-env`:

```bash
DISPATCHARR_VERSION=0.28.2 docker compose -p dbas-sync-testenv \
  -f docker-compose.dbas-sync-test.yml \
  rm -sf dispatcharr-b-web dispatcharr-b-celery dispatcharr-b-db dispatcharr-b-redis
docker volume rm dbas-sync-testenv_dbas-sync-b-data dbas-sync-testenv_dbas-sync-b-pg
DISPATCHARR_VERSION=0.28.2 docker compose -p dbas-sync-testenv \
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

**`DISPATCHARR_VERSION=0.28.2` is not optional.** The compose file's default is
`0.26.0`; omit the variable and B silently comes back a minor version behind A,
which is a different (out-of-scope) test matrix. This bit the build — B came up
0.26.0 on the first reset and had to be torn down again. Confirm with
`curl -s http://127.0.0.1:9602/api/core/version/` every time.

After a reset, ECM's existing sync target still points at B correctly, but B's
JWT changed — the next run re-authenticates on its own.

## Traps — read before you spend an hour on one of these

**1. B throttles `/api/accounts/token/` to roughly 3 requests per minute.**
Pace anything that authenticates against B, and cache the token rather than
re-authenticating per request. A self-inflicted `429` surfaces as
`partial_failed_rolled_back` and has been misread as a code failure twice. One
operation at a time; never reset B while a run is in flight.

**2. A fresh Dispatcharr can import an entire playlist as ONE stream.** This
cost real time during the build. Dispatcharr caches its settings groups in
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
exactly what EPG source id 2 points at. The 6 Standard-M3U channels keep the
real slug ids and match `local-epg.xml`. Do not "fix" A by pointing the XC
lineup at `http://provider-northwind/epg.xml` — nothing will match.

**4. Dispatcharr returns `403`, not `404`, for an API path that does not
exist.** `/api/m3u/accounts/6/refresh/` and
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

# A and B (pin the version — see trap 1):
DISPATCHARR_VERSION=0.28.2 docker compose -p dbas-sync-testenv \
  -f docker-compose.dbas-sync-test.yml up -d

# P and the nginx origin (joins A/B's network, so bring A/B up FIRST):
NORTHWIND_FIXTURE_DIR=/home/lecaptainc/ecm-docenv-fixture \
  docker compose -p dbas-xc-provider -f docker-compose.xc-provider.yml up -d

# ECM:
docker start ecm-docenv
until curl -fsS http://127.0.0.1:6400/api/health/ready >/dev/null; do sleep 3; done
```

All persistent state is in named volumes, so nothing needs rebuilding.

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

Recorded so a later reader can tell a checked claim from an assumed one.

| Claim | How it was checked |
|---|---|
| ECM runs `origin/dev` `dd77d587` | **Not** `/api/version` (that is baked-in build metadata and has misled three engineers). All **244** non-test `backend/**.py` files were sha256'd inside the container against `git show dd77d587:<path>`: **244 OK, 0 mismatched, 0 absent.** |
| …and that checksum harness can actually fail | Two known-bad runs. Compared against an older revision (29 differing files) → **29 MISMATCH**. Then a byte was appended to `/app/main.py` in the live container → **exactly 1 MISMATCH**, restored → back to 244/244. (A first attempt to use the parent commit `b867538a` as the known-bad was **vacuous** — `dd77d587` is its merge commit, so 0 files differ and the check had no signal. Vacuous known-bads are the failure this table exists to prevent.) |
| A's account really is XC | Read `account_type` from **A's Postgres directly** (`select account_type from m3u_m3uaccount`) → `XC`, not from a serializer that could be defaulting. Cross-checked in ECM's own UI: the M3U Manager renders the badge **`XtreamCodes`** on account 6 and **`Standard M3U`** on account 7. |
| P is a genuine XC server | `player_api.php` with correct credentials returns a real `user_info` (auth 1, Active, exp_date, active_cons, max_connections); with a wrong password and with no credentials it returns **HTTP 401** `{"error":"Unauthorized"}`. Both poles from the same endpoint. `get_live_categories` → 7, `get_live_streams` → 53, `xmltv.php` → 200/776 KB, `get.php` → 200/13.7 KB. |
| The A→B sync path works with an XC account in the mix | **Dry-run preview only** — no writes to B. Wrong password → `success=False`, `SYNC_DESTINATION_UNREADABLE`. Correct password → `success=True`, *"would create 78, update 0, skip 9, 0 conflict(s) across 9 categories"*. B re-read afterwards: still **0 channels / 0 streams / 0 groups / 0 logos / 0 EPG sources.** The temporary sync target and all 6 resulting notifications were then deleted, so ECM has no sync history for you to work around. (ECM's own scheduled tasks — the M3U Change Monitor in particular — keep producing routine notifications; those are normal, not leftovers. Clear them before a notifications screenshot with `DELETE /api/notifications?read_only=false`.) |
| The Dispatcharr client used to build all this could report failure | Smoke-tested before use: known-good returns real JSON and exits 0; a nonexistent path and an unreachable host both exit **1** loudly. It never degrades an error into "0 rows". |
| nginx is serving what we think | Real files return 200 with non-zero sizes; a bogus path returns **404** — checked again after the fixture was relocated. |
| Playwright is pointed at a live app | Navigating to a dead port errors before any real page was read. The Channel Manager then rendered **"59 channels"** and all 9 Northwind groups. |
| The committed generator matches the live fixture | `playlist.m3u`, `local.m3u` and all **59** logos compare byte-identical; the two XMLTV files match on the same UTC day. |
| Production is untouched | `ecm-ecm-1` and `ecm-ecm-mcp-1` still `Exited`, with `FinishedAt` timestamps predating this session and no `StartedAt` since. Production `dispatcharr` still `Up`, unchanged, on `dispatcharr-green_default` — a different network from `dbas-sync-testenv_default`. |

### What this environment does NOT cover

- **No apply-mode sync has been run.** Only a dry-run preview. The first real
  A→B apply will be the writer's, which is the intent — but it means the apply
  path has not been exercised *with an XC account on A* by anyone yet. The
  preview says it plans 78 creates across 9 categories; if the apply disagrees,
  that is new information, not a known-good regression.
- **The channel→logo binding does not cross the sync** (bead
  `enhancedchannelmanager-xgbjm`, still open). Logo records and bytes land on B,
  but every synced channel there shows `logo_id null` and B's Logo Manager reads
  `UNUSED`. Do not write the guide as though it works.
- **VOD and Series are empty.** P serves `get_vod_streams` / `get_series` as
  empty lists, so ECM's VOD surfaces have nothing to show. Populating them was
  out of scope.
- **No stream actually plays.** The `.ts` URLs in the fixture are 404s — nginx
  serves no `/stream/` directory. Everything about lineup management,
  EPG, profiles and sync is real; anything that requires a live video
  transport (probing, bitrate, playback) is not.
- **ECM's first-run walkthrough has already been completed here.** The admin
  account exists and the Dispatcharr connection is set. If you need to shoot
  Getting Started from a genuinely blank ECM, do it against a throwaway second
  container rather than resetting this one:
  `docker rm -f ecm-docenv && docker volume rm ecm-docenv-config` then re-run
  the `docker run` from "Bringing the environment back up" with a fresh volume —
  but you will then have to re-create the admin and re-point it at A.
