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
