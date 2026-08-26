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

State it as an invariant, not a file list — the compose files were three
examples of the property, not the whole of it:

> **No live default anywhere in `tests/dbas-test-env/` (or the docs describing
> it) pins a Dispatcharr version. The default is always `latest`, and anything
> that needs to know the version READS it from the running instance's version
> endpoint and records what it saw.**

What that covers today, all swept 2026-08-20:

| Where | Was | Now |
|---|---|---|
| `docker-compose.dbas-test.yml` | `:${DISPATCHARR_VERSION:-0.26.0}` (x2) | `ghcr.io/…:${DISPATCHARR_VERSION:-latest}` |
| `docker-compose.dbas-sync-test.yml` | `:${DISPATCHARR_VERSION:-0.26.0}` (x2) | same |
| `docker-compose.xc-provider.yml` | `:${DISPATCHARR_VERSION:-0.28.2}` (x2) | same |
| `.env.example` | `DISPATCHARR_VERSION=0.26.0` | commented out — and note `docker compose` auto-loads `.env` here for **every** compose file, so a stray value pins all three stacks |
| `validate-version-behaviors.py` | `EXPECTED_VERSION = os.environ.get(…, "0.26.0")`, asserted | reads the version, stamps it on the report, and **hard-fails** if it cannot |
| `capture-snapshot.sh` | stamped `version pin: ${DISPATCHARR_VERSION:-0.26.0}` into every manifest | stamps the version it actually read, or the literal `unknown (…)` — never a guess |
| `seed/edge_supplement.py` | prose asserting a "pinned 0.26.0 schema" | prose pointing at the running schema |
| `docs/testing/dbas-test-env.md` | a "Version pin" table + bump procedure | a version-policy section carrying this invariant |

Remaining mentions of `0.26.0` / `0.28.2` in this directory are **history or
opt-in override examples** — a past validation run, the retired default being
described as retired, or a commented-out `DISPATCHARR_VERSION=0.28.2` showing
how to reproduce an old finding. None of them is a live default.

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
DBAS_TEST_BASE_URL=http://localhost:9591 \
DBAS_TEST_ADMIN_USER=ecmtest DBAS_TEST_ADMIN_PASS=ecmtestpass \
python3 validate-version-behaviors.py
```

The probe reads `GET /api/core/version/` itself, prints
`[summary] VALIDATED AGAINST DISPATCHARR <version> at <url>`, and **exits 2
without reporting any shape** if it cannot read a version — so a recorded
response shape can never be attributed to the wrong platform. Quote that
summary line with any result.

To assert a version deliberately (only when reproducing an old finding), set
the opt-in `DBAS_EXPECT_VERSION`; a mismatch is a loud `WARN`, not a silent
pass. It is unset by default, which is what keeps the probe from carrying a
pin.

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

# HANDOVER — the documentation environment (beads `gk4d0`, `xvuk1`, `maopq`)

> **SUPERSEDED 2026-08-21 by bead `enhancedchannelmanager-kdz6p`.** Instance A was
> **destroyed and rebuilt** for the UI-only acceptance run recorded at the end of
> this file, so **every count and id in this section is stale**: A no longer
> carries 132 channels across five categories, it carries **316 across two**, and
> the `Northwind Local` / `Northwind Regional` channels, the hosted-logo fixture
> and the credentialed EPG source (id 3) are **gone**. `ecm-docenv` was stopped
> and now points at an A it does not describe. The *method* in this section —
> the credential rules, the traps, the pacing, the reset recipes — is still
> current and is what you should read. The *numbers* are not.

**BUILT 2026-08-20 on a synthetic provider chain. REPOINTED 2026-08-21 ONTO THE
OPERATOR'S LIVE XTREAM CODES ACCOUNT** (bead `enhancedchannelmanager-maopq`, PO
instruction). The synthetic Xtream Codes provider — Dispatcharr **P** — is
**retired**; the nginx origin is **kept**, for the reason given below. Every
count and id in this section was measured on 2026-08-21 after the repoint.

**Why the repoint happened, because it changes what this environment is for.**
The synthetic chain could not tell a working stream from a broken one. Its 53 XC
streams answered HTTP 200 from a Dispatcharr proxy, which is not proof of video;
its 6 standard-M3U streams answered 404 because the nginx origin was never given
any `.ts` files at all. Bead `1td94` had already proved a replica's channels were
bound to dead addresses while the run reported zero unplayable, and its own proof
was an HTTP 200 from a ranged GET — *"a well-formed but revoked address still
reads as playable; the predicate answers the offline question only."* A real
provider supplies the missing half: streams that genuinely play, and streams that
genuinely do not, neither of which can be faked on purpose.

---

## CREDENTIALS — the environment holds them, the repo NEVER does

This is the single hardest rule in this file and it **inverts** what the
retired section said. The old chain was entirely synthetic, so it was correct to
write every URL, container name and credential down here. It is **not** correct
now.

**The provider host, username and password are supplied by the PO out of band.
They are configured into the running Dispatcharr A and into nothing else. They
do not appear in this README, in `.env` files, in compose files, in scripts, in
fixtures, in probe output, in commit messages, or in beads.** If something
genuinely cannot be recorded without a credential, leave it out and say so.

Two reasons this is not optional:

- Bead `enhancedchannelmanager-wfz8z` exists because a live `mcp_api_key` was
  once found rendered **inside a committed image** — invisible to gitleaks,
  detect-secrets and GitHub secret scanning alike. **Byte scanners cannot read
  pixels.**
- Bead `msqf7`'s whole finding is that **a live XC stream URL carries the
  username and password in its path segments** (`/live/<user>/<pass>/<id>.ts`).
  So a recorded stream URL *is* a credential. That is why the committed
  inventory identifies streams by **provider stream id and name**, never by URL.

**Known pre-existing exposure, NOT introduced by this work and not fixed here.**
The provider's **hostname** (not its credentials) is already committed on `dev`:
a frontend unit-test fixture in `frontend/src/utils/hdhomerun.test.ts` uses it as
an example Xtream host (added 2026-07-28, commit `1d6b1450`), and bead
`gsnw0.16`'s notes name it as a hostname visible in unshipped screenshots. No
username or password appears in either. Flagged for the PO to decide; do not
treat its presence as licence to add more.

### Screenshot hazards specific to the live account

Read these **before** the first capture, alongside the general sidebar hazard in
the traps list.

- **Anything that renders the M3U account's server URL, username or password.**
  ECM's provider/M3U account editor and Dispatcharr A's own M3U account form
  both do.
- **The XC account-info blob.** Dispatcharr stores the provider's `player_api`
  response verbatim in `m3u_m3uaccountprofile.custom_properties`, and that JSON
  contains `"username"` and `"password"` in cleartext. Any surface that renders
  raw custom properties renders the credentials.
- **Stream URLs anywhere** — Dispatcharr's stream detail, ECM's stream browser,
  any log line, any `ffprobe` error message (ffprobe echoes the full input URL
  on failure).
- **EPG source 3's URL**, which is `xmltv.php?username=…&password=…`.

Black these out at capture time. Do not rely on noticing them later.

### Configuring the account (values come from the PO, not from here)

On Dispatcharr **A**, create an M3U account with:

| Field | Value |
|---|---|
| Name | `Live XC Provider` (any name; this one is what the counts below assume) |
| Account type | **XC** (Xtream Codes) |
| Server URL / username / password | **from the PO, out of band** |
| User-Agent | a real client UA — the environment uses the built-in `TiviMate` (id 1) |
| Max streams | **3** — match the account's `max_connections`; see Pacing |
| Refresh interval | **0** (manual only) — see trap 13 |
| Auto-enable new groups (live) | **OFF, before the first refresh** — see trap 13 |
| VOD / Series | **OFF** |

Then refresh **twice**: the first refresh only discovers categories, the second
imports the streams of whichever categories you enabled in between.

---

## The platform this was measured on

| Node | `GET /api/core/version/` | Image |
|---|---|---|
| Dispatcharr A (source) | `0.29.0` | `ghcr.io/dispatcharr/dispatcharr:latest` |
| Dispatcharr B (destination) | `0.29.0` | same |
| ECM (`ecm-docenv`) | tree of `42280ecb` — see below | image tagged `ecm-docenv:dd77d587` |

`ecm-docenv`'s image tag and its `/api/version` both say `dd77d587`. **That is
build metadata and it is wrong about what is running** — modules were
`docker cp`'d in afterwards. Checksumming the container's tree against git says
`42280ecb`: **244 non-test `backend/**.py` files, 244 OK, 0 MISMATCH, 0 ABSENT**.
The same harness against `42280ecb~40` reports 56 MISMATCH, so it is not
vacuous. This is the fourth time `/api/version` has misled someone on this work
(trap 12). Checksum; do not read the tag.

```
  the PO's LIVE Xtream Codes account          provider-northwind (nginx:
   (host/user/pass supplied out of band)       local.m3u, local-epg.xml, 6 logos)
        |                                        |
        |  XTREAM CODES (account_type = XC)      |  STANDARD M3U
        v                                        v
                 Dispatcharr A  <---- reads ----  ECM (ecm-docenv)
                 (the operator's instance)            |
                                                      | cross-instance sync
                                                      v
                                                Dispatcharr B (destination, EMPTY)

  [RETIRED] Dispatcharr P — the synthetic XC provider — is stopped.
```

## URLs, ports, containers, credentials

| What | Host URL | In-cluster address | Container | Credentials |
|---|---|---|---|---|
| **ECM** (under test) | `http://127.0.0.1:6400` | — | `ecm-docenv` | `ecm-demo-admin` / `Docs-Demo-Not-Real-2026!` |
| **Dispatcharr A** — source | `http://127.0.0.1:9601` | `http://dispatcharr-a-web:9191` | `dbas-sync-testenv-dispatcharr-a-web-1` | `ecmtest-a` / `ecmtestpass-a` |
| **Dispatcharr B** — sync destination | `http://127.0.0.1:9602` | `http://dispatcharr-b-web:9191` | `dbas-sync-testenv-dispatcharr-b-web-1` | `ecmtest-b` / `ecmtestpass-b` |
| **provider-northwind** (nginx origin, KEPT) | *no host port* | `http://provider-northwind/` | `dbas-xc-provider-provider-northwind-1` | none |
| **The live XC provider** | — | — | — | **from the PO, out of band. Not recorded here.** |
| ~~Dispatcharr P~~ — **RETIRED, stopped** | ~~`http://127.0.0.1:9603`~~ | — | `dbas-xc-provider-dispatcharr-p-*` (all `Exited`) | — |

Postgres ports: A `5446`, B `5447` (P `5448`, stopped). The three throwaway
credential pairs above are still deliberately fake-looking and are still safe to
put in a screenshot. **The provider's are not, and are not written down here.**

---

## What is on instance A

**132 channels / 497 streams / 8 channel groups.** 126 of those channels come
from the live XC account; 6 come from the credential-free nginx origin.

| Group | Channels | Numbers | Streams held | Source |
|---|---|---|---|---|
| `USA: NEWS NETWORK [1080p]` | 25 | 100–124 | 69 | live XC account |
| `USA: SPORTS NETWORK [1080p]` | 36 | 200–235 | 102 | live XC account |
| `USA: MOVIES NETWORK [1080p]` | 20 | 300–319 | 74 | live XC account |
| `USA: KIDS NETWORK [1080p]` | 20 | 400–419 | 32 | live XC account |
| `USA: ENTERTAINMENT [1080p]` | 25 | 600–624 | 214 | live XC account |
| `Northwind Local` | 3 | 800–802 | 3 | nginx origin (Standard M3U) |
| `Northwind Regional` | 3 | 850–852 | 3 | nginx origin (Standard M3U) |
| `Default Group` | 0 | — | 0 | Dispatcharr built-in |

### Why those five categories, and how the scale was held down

The account carries **53,659 live streams across 777 categories** (measured
2026-08-21 — the earlier brief said 53,535, so the catalogue moves; re-read it,
do not quote this). Importing it whole would make this environment unusable and
every measurement slow, so the account is restricted at two levels:

1. **At import**: exactly five of the 777 categories are enabled on the M3U
   account — `USA: NEWS NETWORK`, `USA: SPORTS NETWORK`, `USA: MOVIES NETWORK`,
   `USA: KIDS NETWORK`, `USA: ENTERTAINMENT` (PO-confirmed; they map onto the
   retired fixture's News/Sports/Movies/Kids/Entertainment shape so prior
   measurements stay comparable). That is **491 streams, 0.9% of the catalogue**
   (Dispatcharr's own log: `Retrieved 53659 total live streams from provider` …
   `Filtered 491 streams from 5 enabled categories`).
2. **At channel creation**: a documented subset becomes channels — the first
   *N* streams of each group in the provider's own `num` order (NEWS 25,
   SPORTS 30, MOVIES 20, KIDS 20, ENTERTAINMENT 25), **plus every stream the
   playability census found not-playable**, so the known-bad set is guaranteed
   to be reachable from a channel. SPORTS therefore holds 36, not 30.

**Result: 126 XC channels — 114 known-good, 12 known-bad.**

The 771 category rows the discovery refresh created for the categories we did
*not* enable were deleted afterwards (they held no channels and no streams).
They come back, **disabled**, if anyone re-runs the discovery refresh; see
trap 13.

### The known-good / known-bad inventory

The full census of all 491 imported streams is committed as
**`tests/dbas-test-env/xc-stream-census.json`** — provider stream id, name,
category, verdict, HTTP status, bytes read, MPEG-TS framing, and the channel
number where one exists. **No host, no credentials, no URLs.** Tests can assert
against it by stream id and name.

Totals: **479 of 491 playable, 12 not playable.** All 12 are bound to channels:

| Channel | Category | Provider stream id | Name |
|---|---|---|---|
| 102 | NEWS | 13422 | `✶⋆✶ NEWS NETWORK ✶⋆✶ [1080p]` |
| 200 | SPORTS | 13580 | `✶⋆✶ SPORTS NETWORK ✶⋆✶ [1080p]` |
| 208 | SPORTS | 13590 | `USA: BEIN SPORTS 2 [720p]` |
| 230 | SPORTS | 13634 | `USA: FOX SPORTS DETROIT [720p]` |
| 231 | SPORTS | 13635 | `USA: FOX SPORTS FLORIDA [720p]` |
| 232 | SPORTS | 13637 | `USA: FOX SPORTS NORTH [720p]` |
| 233 | SPORTS | 13638 | `USA: FOX SPORTS OHIO [720p]` |
| 234 | SPORTS | 13642 | `USA: FOX SPORTS SOUTH [720p]` |
| 235 | SPORTS | 13644 | `USA: FOX SPORTS SUN [720p]` |
| 301 | MOVIES | 13706 | `✶⋆✶ MOVIES NETWORK ✶⋆✶ [1080p]` |
| 400 | KIDS | 13547 | `✶⋆✶ KIDS NETWORK ✶⋆✶ [1080p]` |
| 600 | ENTERTAINMENT | 13210 | `✶⋆✶ ENTERTAINMENT ✶⋆✶ [1080p]` |

Two kinds of bad, and the difference matters when you write an assertion:

- **The five `✶⋆✶ … ✶⋆✶` rows** are the provider's own category banners. They
  are permanently dead by construction and will not come back.
- **The seven regional sports channels** are genuinely revoked feeds. They are
  *live provider state* and could start working again, or others could stop.
  **Re-run the census before trusting the list** (see "Re-running the census").

Alongside them, the nginx origin still supplies **six channels (800–802,
850–852) whose `.ts` URLs 404 by construction** — the deterministic, offline,
credential-free known-bad control. Do not conflate the two sets: the nginx six
are "the address does not resolve", the provider twelve are "the address
resolves and serves nothing".

### Everything else on A

| Thing | Count | Names / ids |
|---|---|---|
| M3U accounts | 3 | id 4 **`Live XC Provider`** (`account_type = XC`, 491 streams, `max_streams` 3, `refresh_interval` 0); id 3 `Northwind Local Affiliates (Standard M3U)` (`STD`, 6 streams); id 1 `custom` (Dispatcharr's locked built-in) |
| EPG sources | 2 | id 2 `Northwind Local XMLTV` — **active**, 421 programmes / 6 channels, credential-free URL; id 3 `Live XC Provider EPG (Xtream Codes)` — **INACTIVE and never imported**, see below |
| Channel profiles | 2 | id 1 `Living Room` (132/132 enabled); id 2 `Kids & Family` (**20**/132 enabled — the KIDS group only) |
| Stream profiles | 6 | 5 Dispatcharr built-ins (ids 1–5) + id 6 `Northwind Direct (ffmpeg)`, which references the custom user agent |
| User agents | 4 | 3 built-ins (ids 1–3: TiviMate, VLC, Chrome) + id 4 `Northwind Set-Top Box` |
| Logos | 133 | 132 remote-URL logos + id 187 `Hosted Logo Probe (on Dispatcharr A)`, bytes in **A's own** `/data/logos/meridian-news-hosted.png` (md5 `f7e3278711ad164d1fd9c1e6324d81bc`, 7,180 bytes), bound to channel **100** |
| Channel groups | 8 | see the table above |
| Server groups | 0 | — |
| Output profiles | 2 | ids 1–2, Dispatcharr built-ins |
| Dispatcharr users | 1 | `ecmtest-a` |

**EPG source 3 exists but was deliberately never imported.** The provider's
`xmltv.php` returns **95,268,539 bytes (≈91 MiB)** of guide data — measured
2026-08-21, `application/xml`, HTTP 200 — covering the whole 53,659-stream
catalogue, for the 126 channels this environment actually has. Importing it
would blow the scale budget for no benefit. The source row exists because the `f5a5j` fidelity
finding is about *the credentialed URL field itself* being stripped on the
destination, and that measurement needs a credential-bearing EPG source to
exist. It is `is_active = false`, `refresh_interval = 0`.

**Consequence: only the 6 nginx channels carry EPG data.** The 126 XC channels
have no `tvg_id` match and no programmes. That is a deliberate trade, not a
defect — but do not caption a screenshot "all channels have a guide."

**The A-side fixture pairs that earlier fidelity beads depend on are intact**:
the credential-free EPG source (id 2) as the control against the credentialed
one (id 3); the Dispatcharr-hosted logo bound to a channel (`cfxml`); a channel
profile whose enabled set is a strict subset (`Kids & Family`, 20 of 132).

---

## What was retired, what was kept, and why

**RETIRED — Dispatcharr P** (`dbas-xc-provider-dispatcharr-p-web / -celery /
-db / -redis`). Its only job was to be a believable fake XC server. The live
account does that job for real, so P has no remaining purpose, and it was four
containers plus two Postgres volumes of overhead.

Nothing committed depends on P **running**. Five backend test files mention
`dispatcharr-p-web` / `northwind`, and every one of them uses those strings as
**literals inside mock-driven unit tests** — no socket is opened:
`backend/tests/dbas/test_1td94_sentinel_url_is_a_dead_address.py`,
`backend/tests/tasks/test_dbas_sync_engine.py`,
`backend/tests/tasks/test_msqf7_stream_url_credential_leak.py`,
`backend/tests/tasks/test_sync_roundtrip.py`,
`backend/tests/tasks/test_sync_xc_guide_url_visibility.py`. Nothing in CI, and
no script or recipe outside this README, brings P up.

**P's containers were STOPPED, not destroyed, and its volumes were left in
place.** Reconstructing P from scratch is manual work driven by prose in this
file, so destroying it is a one-way door that nobody asked for. Bringing it back
is `docker compose -p dbas-xc-provider -f docker-compose.xc-provider.yml start
dispatcharr-p-db dispatcharr-p-redis dispatcharr-p-web dispatcharr-p-celery`.
Destroying it for real is a separate, deliberate step:

```bash
cd tests/dbas-test-env
docker compose -p dbas-xc-provider -f docker-compose.xc-provider.yml rm -sf \
  dispatcharr-p-web dispatcharr-p-celery dispatcharr-p-db dispatcharr-p-redis
docker volume rm dbas-xc-provider_dbas-xc-p-data dbas-xc-provider_dbas-xc-p-pg
```

`docker-compose.xc-provider.yml` and `northwind-fixture.py` **stay committed**.
They are the only reproduction path for the retired synthetic chain and for the
0.28.2 / 0.29.0 measurements taken against it.

**KEPT — `provider-northwind` (nginx).** It is one container serving static
files and it still earns its place three times over:

1. It is the **only credential-free M3U + XMLTV source** in the environment.
   Epic `f5a5j`'s central finding is that a *credential-bearing* EPG URL is
   stripped to empty on the sync destination while a *credential-free* one
   crosses intact. Retiring nginx would delete the control half of that
   comparison, on the branch that exists to fix it.
2. Its six `.ts` URLs 404 deterministically and offline — the negative control
   any playability probe needs to prove it can report failure at all.
3. It costs nothing and depends on nothing (P read *from* nginx, not the other
   way round).

---

## Instance B — the sync destination, reset to EMPTY

B was destroyed (containers **and** both volumes) and recreated on 2026-08-21,
its superuser re-created, and its `stream_settings` cache busted (trap 2). Read
straight out of B's Postgres, no API in the path:

```
0 channels · 0 streams · 0 channel groups · 0 channel profiles · 0 logos
0 EPG sources · 0 EPG programmes · 0 server groups
3 user agents (ids 1-3)  ·  5 stream profiles (ids 1-5)
2 output profiles (ids 1-2)  ·  1 M3U account (id 1, `custom`)
1 user (`ecmtest-b`)
```

**"Empty" is not zero of everything** — caption it from that block. Those id
ranges are what make the FK checks non-vacuous: an artifact that lands on B with
an id outside 1–3 (user agents), 1–5 (stream profiles) or 1 (M3U accounts) was
created by the sync. `Default Group` is **not** present on a fresh instance; it
appears the moment something creates an M3U account, so its arrival on B is a
sync side effect.

**The destination is unspent** — no sync has ever written to this volume, and
**no sync target exists in ECM**. Creating one is part of the workflow being
documented. Point it at `http://dispatcharr-b-web:9191` with B's credentials.

### Resetting B between runs

```bash
cd tests/dbas-test-env
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml \
  rm -sf dispatcharr-b-web dispatcharr-b-celery dispatcharr-b-db dispatcharr-b-redis
docker volume rm dbas-sync-testenv_dbas-sync-b-data dbas-sync-testenv_dbas-sync-b-pg
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml up -d \
  dispatcharr-b-db dispatcharr-b-redis dispatcharr-b-web dispatcharr-b-celery

until curl -fsS http://127.0.0.1:9602/api/core/version/ >/dev/null; do sleep 5; done
docker exec dbas-sync-testenv-dispatcharr-b-web-1 sh -c '
  cd /app && export DJANGO_SECRET_KEY="$(tr -d "\r\n" < /data/jwt)" && \
  DJANGO_SUPERUSER_USERNAME=ecmtest-b \
  DJANGO_SUPERUSER_PASSWORD=ecmtestpass-b \
  DJANGO_SUPERUSER_EMAIL=ecmtest-b@example.invalid \
  python manage.py createsuperuser --noinput'
docker exec dbas-sync-testenv-dispatcharr-b-redis-1 \
  redis-cli DEL ':1:coresettings:group:stream_settings'
```

**Pass no version flag** — the compose default is `latest`, which is what A is
on. Confirm it anyway and record what you saw.

---

## PACING — the account allows THREE concurrent connections

`max_connections = 3` is a hard constraint on the PO's real account, and
exceeding it or hammering the provider is a real cost to them, not a test-env
inconvenience.

- **Probe strictly sequentially. One connection at a time.** The 491-stream
  census below was taken that way and took about 15 minutes.
- **Never hold a stream open.** Read a bounded number of bytes and close.
- **`max_streams` on the M3U account is set to 3** so Dispatcharr will not
  exceed it either. Leave it there.
- Dispatcharr A and B additionally throttle their **own** `/api/accounts/token/`
  to roughly 3 requests per minute (re-measured on 0.29.0). Cache the JWT.

---

## The playability census — how "plays" was separated from "resolves"

This is the point of the whole repoint, so the method matters more than the
numbers. Three tiers, and only the middle one produced the committed verdicts:

| Tier | Question it answers | How |
|---|---|---|
| 1 — resolves | does the address answer? | final HTTP status after redirects |
| 2 — **plays** | does it deliver video? | read the first 64 KiB of the body and require MPEG-TS framing (`0x47` at 188-byte stride) |
| 3 — decodes | does a decoder accept it? | `ffprobe`, used to *validate* tier 2 on a sample |

**Tier 1 alone would have called all 491 streams playable.** That is exactly the
false green bead `1td94` reported. Twelve streams answer **HTTP 200 and then
deliver zero bytes**; seven of those even send `Content-Type: video/mp2t` first.
Re-probed three times over the session, all twelve reproduce — and the seven
revoked sports feeds alternate between `200` + zero bytes and a bare `HTTP 500`
from run to run, so *a status-code probe would grade the same dead channel
differently on consecutive runs.*

The probe was smoke-tested against five poles before it was armed, and each
produced a distinct verdict:

| Pole | Verdict |
|---|---|
| a real live stream | `PLAYS_TS` — 200, 65,536 bytes, TS-framed |
| a nonexistent stream id | `DEAD_HTTP_500` |
| a real stream id with a **wrong password** | `DEAD_HTTP_404` |
| an unreachable host | `UNREACHABLE` |
| **`player_api.php` — HTTP 200, but JSON, not video** | `RESOLVES_NOT_TS` |

That last pole is the one that matters: it proves the instrument can separate
"resolved" from "played". A probe that cannot distinguish those two is not
measuring playability.

**Tier 3 agreed with tier 2 on 20 sampled good streams and all 12 bad ones** —
good ones decode as h264 + aac (1280×720 / 1920×1080), bad ones give
`Invalid data found when processing input`. But see trap 16: three of those 20
first reported NO-DECODE and were **false negatives of my own ffprobe budget**,
not bad streams.

### Re-running the census

The provider's channel health is live state. Re-measure before trusting the
committed inventory in a new session. The probe must:

- use **GET, never HEAD** (trap 14),
- **cap bytes and elapsed time client-side**, because the provider ignores
  `Range` (trap 15),
- run **one connection at a time**,
- identify streams by **provider stream id**, and write **no URLs** to disk.

`tests/dbas-test-env/xc-stream-census.json` carries the method, the pacing rule
and both traps in its own `_method` block, so a reader who finds only that file
still gets the rules.

---

## Traps — read before you spend an hour on one of these

**1. A and B throttle `/api/accounts/token/` to roughly 3 requests per minute.**
Re-measured on 0.29.0. Requests 1–3 return `200`, request 4 returns `429`
`{"detail":"Request was throttled…"}`. Cache the token; a self-inflicted `429`
surfaces as `partial_failed_rolled_back` and has been misread as a code failure
twice. One operation at a time; never reset B while a run is in flight.

**2. A fresh Dispatcharr can import an entire playlist as ONE stream.** Still
live on 0.29.0. On a first boot `stream_settings` can be cached in Redis
*before* the migration writes it, so `m3u_hash_key` reads `""` instead of
`"url"`, every stream hashes to `sha256("{}")` and the playlist collapses to a
single row — reporting `status: success`. Bust the cache on **every** fresh
instance before the first M3U refresh:

```bash
docker exec <stack>-dispatcharr-<x>-db-1 psql -U dispatch -d dispatcharr \
  -tAc "select value from core_coresettings where key='stream_settings';"
docker exec <stack>-dispatcharr-<x>-redis-1 \
  redis-cli DEL ':1:coresettings:group:stream_settings'
```

**3. XC-imported channels carry the provider's `epg_channel_id`, not a slug.**
Whatever the provider publishes is what lands in `tvg_id`. Do not assume it
matches any XMLTV you have lying around.

**4. Dispatcharr returns `403`, not `404`, for some API paths that do not
exist** (`/api/m3u/accounts/<id>/refresh/`), and its SPA catch-all returns
**`200` with HTML** for others (`/api/channels/nope/`). **Never read a bare
status code as proof a Dispatcharr route exists** — check the body shape. The
real routes are `/api/m3u/refresh/<account_id>/` and
`/api/channels/channels/from-stream/bulk/`.

**5. Bulk channel-profile membership is `PATCH`, not `POST`.**
`/api/channels/profiles/<id>/channels/bulk-update/` answers 405 to a POST.

**6. `createsuperuser` needs `DJANGO_SECRET_KEY` exported.** A bare `manage.py`
fails with "SECRET_KEY must not be empty"; the entrypoint derives it from
`/data/jwt`.

**7. ECM's first-run setup rejects `.invalid` email addresses.** Use
`example.com`.

**8. ECM caches Dispatcharr's channel-group list in-process.** If stream rows
render empty under group headers that show non-zero counts, the cache is stale.
`docker restart ecm-docenv` clears it. Restart ECM after **any** bulk group
change on A — this bit during the repoint.

**9. "Create channels from this group" is a silent no-op on a COLLAPSED group.**
Expand it first. The bulk-create modal renders in a portal — search the whole
page.

**10. Dispatcharr's bulk channel creation is asynchronous.** The POST returns a
`task_id`; poll `/api/channels/channels/` until the count settles.

**11. SCREENSHOT HAZARD — Dispatcharr's sidebar renders the host's REAL PUBLIC
IP**, and the live provider adds four more hazards listed under "Screenshot
hazards specific to the live account". Nothing in CI can catch any of them: a
byte scanner cannot see pixels (bead `wfz8z`).

**12. `/api/version`'s `git_commit` is baked-in build metadata, not what is
running.** It has now misled four engineers on this work. `ecm-docenv` reports
`dd77d587` while running the tree of `42280ecb`. Checksum the deployed tree.

**13. An XC account discovers ALL of the provider's categories, and enables them
all by default.** On this account that is **777 categories**, and the next full
refresh would then import **all 53,659 streams**. Two protections, both
required:

- Create the account with **auto-enable new groups (live) = OFF** *before* its
  first refresh. Dispatcharr does **not** auto-refresh an XC account on create
  (it does for STD), so you get exactly one chance to set this cleanly.
- Set **`refresh_interval = 0`** so the periodic task cannot re-run discovery
  unattended.

The discovery refresh still creates all 777 `ChannelGroup` rows (disabled). The
771 unused ones were deleted after the import; **they will silently reappear,
disabled, on any future discovery refresh.** Delete them again, or accept a
777-entry group dropdown in every screenshot.

**14. THE PROVIDER 404s `HEAD` ON URLS THAT SERVE `200` TO `GET`.** Measured
directly: `HEAD` on a stream that `GET` proves plays returns **404**. This false
negative nearly buried bead `msqf7`. **Never probe this provider with HEAD.**

**15. The provider IGNORES the `Range` header.** `curl -r 0-0` does **not**
return a one-byte 206 — it returns `200` and then streams continuously (8.8 MB
in 20 s, measured, terminated by the client). A "ranged GET" is therefore not a
cheap probe here, and it is also not evidence of playability. Cap bytes **and**
elapsed time on the client side, and read the bytes you got.

**16. `ffprobe` produces FALSE NEGATIVES on live TS if its budget is too
small.** Three of twenty known-good streams reported *"non-existing SPS 0
referenced"* and no stream at `-analyzeduration 3000000 -probesize 3000000`;
all three decoded cleanly at `-analyzeduration 15000000 -probesize 16000000` or
above. If you use ffprobe as a playability oracle, prove the budget first
against a stream you have already confirmed by other means — otherwise you will
report live channels as dead.

---

## Regenerating the nginx fixture

The nginx document root is generated, not committed (1.7 MB, mostly logos). The
generator is committed as `northwind-fixture.py`:

```bash
python3 tests/dbas-test-env/northwind-fixture.py /home/lecaptainc/ecm-docenv-fixture
```

Its XMLTV files are anchored to **today in UTC** and cover 3 days, so
`Northwind Local XMLTV` (EPG source 2) goes stale. Re-run the generator and
re-import source 2. The generator also still emits the full 53-channel
`playlist.m3u` and `epg.xml` that fed the retired Dispatcharr P; those files are
harmless and are what makes P reconstructable.

## Bringing the environment back up from cold

```bash
cd tests/dbas-test-env

# A and B (no version flag — the compose default is `latest`):
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml up -d

# the nginx origin ONLY — do NOT start the retired P services:
NORTHWIND_FIXTURE_DIR=/home/lecaptainc/ecm-docenv-fixture \
  docker compose -p dbas-xc-provider -f docker-compose.xc-provider.yml up -d provider-northwind

docker start ecm-docenv
until curl -fsS http://127.0.0.1:6400/api/health/ready >/dev/null; do sleep 3; done
```

All persistent state is in named volumes. A bare `up -d` reuses the cached
`latest` deliberately, so a cold restart cannot move the platform under a
screenshot run. If ECM's container is gone rather than stopped:

```bash
docker run -d --name ecm-docenv \
  --network dbas-sync-testenv_default \
  -p 127.0.0.1:6400:6400 \
  -e ECM_PORT=6400 -e ECM_HTTPS_PORT=6443 -e CONFIG_DIR=/config -e PUID=1000 -e PGID=1000 \
  -v ecm-docenv-config:/config \
  ecm-docenv:dd77d587
```

(The image tag is `dd77d587`; the tree inside is `42280ecb`. See trap 12.)

## Tearing the whole thing down

```bash
docker rm -f ecm-docenv
docker volume rm ecm-docenv-config

cd tests/dbas-test-env
docker compose -p dbas-xc-provider  -f docker-compose.xc-provider.yml  down -v
docker compose -p dbas-sync-testenv -f docker-compose.dbas-sync-test.yml down -v

rm -rf /home/lecaptainc/ecm-docenv-fixture
```

Tear down `dbas-xc-provider` **before** `dbas-sync-testenv` — it attaches to
`dbas-sync-testenv_default` as an external network, and Docker will refuse to
remove a network that still has endpoints on it.

**AND: delete the live XC M3U account (id 4) and EPG source (id 3) from
Dispatcharr A before handing this host to anyone else.** They hold the PO's real
credentials in A's Postgres.

**Never touch `ecm-ecm-1`, `ecm-ecm-mcp-1`, or the `dispatcharr` container.**
Those are the operator's production stack, they are RUNNING, and they are not
part of this environment.

---

## What was verified on the 2026-08-21 repoint, and how

| Claim | How it was checked |
|---|---|
| The provider account is live and its capability is what we think | `player_api.php` with the right credentials returns `auth 1`, `status Active`, `max_connections 3`, `active_cons 0`, `allowed_output_formats ["ts","m3u8","rtmp"]`. **Both poles**: a wrong password returns **HTTP 404** (note: *not* 401 — this provider does not distinguish). `get_live_categories` → **777**. |
| The five enabled categories hold 491 streams | Two independent methods that agree: the provider's own `get_live_streams&category_id=` per category (69 + 102 + 74 + 32 + 214 = 491), and Dispatcharr's import result (`Streams: 491 created`). |
| Only those five categories are enabled | Read from A's Postgres: `dispatcharr_channels_channelgroupm3uaccount` for account 4 → 5 rows `enabled = t`, 777 rows total before cleanup, 771 deleted. |
| A's account really is XC | `select account_type from m3u_m3uaccount` on A's Postgres → id 4 `XC` — not from a serializer that could be defaulting. |
| **Playability, not reachability** | 491 streams fetched sequentially; verdict taken from the **body bytes**, not the status code. 479 playable / 12 not. The 12 reproduce across three separate runs. See the census section for the five instrument poles. |
| The probe can report failure at all | Five poles, five distinct verdicts, including a `200`-but-not-video pole. Run before the census, not after. |
| ffprobe corroborates the byte-level verdict | 20 sampled good → h264 + aac; 12 bad → `Invalid data found`. Three initial NO-DECODEs were traced to the ffprobe budget and reversed (trap 16) rather than reported as dead channels. |
| ECM runs the tree of `42280ecb` | `sha256sum` inside the container against `git show 42280ecb:<path>` for **all 244** non-test `backend/**.py`: 244 OK / 0 MISMATCH / 0 ABSENT. **Known-bad pole**: the same harness against `42280ecb~40` reports 56 MISMATCH / 14 absent, so it is not vacuous. |
| ECM is actually talking to A | `/api/health/ready` → `dispatcharr: reachable (HTTP 200)`, `ffprobe: ok`. `GET /api/channels` → **count 132**. `GET /api/providers` → 3 accounts including `Live XC Provider`. Login proven both poles: correct password 200, wrong password **401**. |
| B is empty and unspent | Read straight from B's Postgres after a container **and volume** destroy/recreate. **The reader can fail**: a bogus table name produces `ERROR: relation … does not exist`, not `0`. B's token endpoint proven both poles (200 / 401). ECM holds no sync target (`settings.json` has no `sync_targets` key) and its `/config/uploads/logos/` is empty. |
| The nginx origin still serves, and can still 404 | From inside A: `local.m3u` 200, `local-epg.xml` 200, a bogus path **404**, and `stream/valley-public.ts` **404** — the deliberate offline known-bad. Both poles. |
| Dispatcharr P is really down | All four P containers `Exited`; from inside A, `http://dispatcharr-p-web:9191/api/core/version/` gives curl exit 6 (could not resolve host). |
| No committed artifact carries a credential | `grep -F` for the password, the username as a whole word, the provider host, `/live/`, `http://` and `https://` across `xc-stream-census.json` → **0 hits each**. **The grep instrument was smoke-tested both poles** on the same file: a known-present string returns 491 lines, a nonsense string returns 0. The full staged diff was swept the same way before commit. |
| Production is untouched | `ecm-ecm-1`, `ecm-ecm-mcp-1` and the operator's `dispatcharr` were never addressed by any command in this run; the only containers written to were `ecm-docenv`, `dbas-sync-testenv-*` and `dbas-xc-provider-*`. |

## What this environment still does NOT cover

- **The XC channels have no EPG.** Only the 6 nginx channels do. See the
  EPG-source note above for why, and do not caption otherwise.
- **VOD and Series are off.** `enable_vod` is false on the XC account; the
  provider has both, so this is a deliberate scale decision that can be
  revisited category by category.
- **The apply path has not been exercised since the repoint.** The last
  measured A→B apply is the 0.29.0 run archived at the end of this file,
  taken against the retired synthetic lineup. Its numbers do **not** describe
  the current A.
- **No screenshots have been taken against the live provider.** Bead
  `enhancedchannelmanager-x4eoi` resumes here. Read the credential section and
  traps 11 and 12 before the first capture. The redaction burden is materially
  higher than it was against the synthetic chain — every provider URL, the
  account form, and the XC account-info blob all need blacking out.
- **Playwright was not exercised on this repoint**, so there is no
  rendered-browser evidence in the table above. ECM's API was proven live
  (count 132); nothing rendered a page.
- **The 12 known-bad streams are live provider state**, not a fixture. Re-run
  the census rather than trusting the list across sessions.

---

---

# The UI-ONLY A→B acceptance run (bead `enhancedchannelmanager-kdz6p`)

**RUN 2026-08-21** against Dispatcharr **0.29.0** on both instances and a
disposable ECM built from `fix/f5a5j-replica-fidelity` HEAD **`aed7447b`**. This
is the acceptance test for epic `f5a5j`, and its binding constraint was **"only
Playwright and the UI — no API calls."** Every piece of state below was created
by clicking, in ECM's UI or in Dispatcharr's own first-run wizard. Reading the
destination's Postgres was used for **verification only**, never to write.

**This run is only possible under that constraint because bead `8gnik` shipped
the `sync_logos` UI toggle.** Before it, enabling logo replication needed a raw
API call, and logos are half of what `f5a5j` is about.

## What was stood up, and by which control

| Step | Where | The control that was clicked |
|---|---|---|
| A's superuser | Dispatcharr A `:9601` | first-run "Create your Super User Account" wizard |
| B's superuser | Dispatcharr B `:9602` | same wizard |
| ECM admin + login | ECM `:6500` | first-run "Create Admin Account" |
| ECM → A connection | ECM | Dispatcharr Connection Settings → Test Connection (read `Connected`) → Save |
| XC provider account | ECM | **M3U Manager → Add M3U Account → account type XtreamCodes**, Max Streams `3`, Refresh `0`, **Auto-enable new groups (Live) OFF before the first refresh** (trap 13), VOD/Series off |
| exactly two categories | ECM | **M3U account → ⋮ → Manage Groups**, search each name, flip its Enabled toggle, read `2 / 777 enabled`, **Save & Refresh** |
| EPG source | ECM | **EPG Manager → Add Standard EPG**, XMLTV URL, Refresh `0` |
| 316 channels in two named groups | ECM | **Channel Manager → Edit Mode → expand the stream group (trap 9) → "Create channels from this group" → Channel Group → "Create new group" → Done → Apply All**, once per group |
| EPG links | ECM | Edit Mode → select-all on both groups → **Assign EPG** → Match → **Accept Best Guesses** → Assign → Done → Apply All |
| programme data | ECM | EPG Manager → **Refresh EPG source** (channels must already be mapped — see below) |
| 3 channel profiles | ECM | **Channel Manager → ⋮ → Channel Profiles**, create, then **Manage channels** → group toggle → **Save Changes** |
| the sync target | ECM | **Settings → Backup & Restore → Cross-Instance Sync → Add sync target** |
| logo replication | ECM | the **`Logos off` → `Logos on`** button on the target row (bead `8gnik`) |
| preview / apply | ECM | **Sync now (preview)** then **Apply** |

Nothing in this run needed Dispatcharr A's own UI. ECM covered every step,
including XC account creation and per-category enablement — which the earlier
`xdmru` recipe had to leave to A's own screens.

## What A ended up holding

**316 channels / 316 streams / 779 channel groups / 3 channel profiles.**

| Group | Channels | Numbers | Streams |
|---|---|---|---|
| `Sports` (from `USA: SPORTS NETWORK [1080p]`, provider category `10432`) | 102 | 200–301 | 102 |
| `Entertainment` (from `USA: ENTERTAINMENT [1080p]`, provider category `10387`) | 214 | 600–813 | 214 |

The other **777** channel groups are the provider-category rows the discovery
refresh creates; they hold no channels and were deliberately left in place this
time (see trap 13 for why they come back if you delete them).

| Profile | Enabled / total membership |
|---|---|
| `Sports` | 102 / 316 — Sports group only |
| `Entertainment` | 214 / 316 — Entertainment group only |
| `LiveTV` | 316 / 316 — every channel |

A channel belongs to exactly one **group** and appears in all three **profiles**
with a per-profile enabled flag. That is the reading `LiveTV` requires.

## The real coverage numbers — measured, not aspired to

**Logos: 316 of 316 (100%).** Every channel carries one, and every logo record
is a **remote URL** the provider publishes. **No logo bytes are hosted on A**, so
this run exercised the remote-URL half of logo replication and **not** the
Dispatcharr-hosted-bytes half (`cfxml`). A's `/data/logos` is empty and so is
B's, correctly.

**EPG: 183 of 316 (57.9%).** ECM's bulk matcher against 14,646 EPG entries
returned **0 exact matches, 183 "need review" at 10–57% confidence, and 133
unmatched.** The 183 were linked with the dialog's own **"Accept Best Guesses"**;
that is an operator decision and it is recorded here as one. **133 channels have
no guide and nothing was hand-fixed to hide that.** "Every channel has an EPG"
is not reachable against this provider's names and this guide.

## The 306 MB EPG: it ingested, and it was cheap

The measured risk was that `https://cdn.epg.guru/7dayiptv/UnitedStates.xml.gz`
(**~306 MB gzipped, ~3.26 GB decompressed**, roughly 35× the provider's own
`xmltv.php`) might be slow, memory-hungry, or might not finish. **It finished.**

| Phase | Wall time | Peak celery RSS | Disk |
|---|---|---|---|
| create source → channels parsed (no channels mapped yet) | **~36 s** | ~134 MB reported by the task, ~630 MB container RSS | no measurable change |
| refresh with 183 channels mapped → programmes stored | **~66 s** | ~636 MB container RSS | ~1 GB (the cached file) |

**The reason it is cheap is the thing to remember:** 0.29.0 stream-parses the
gzip and **stores programmes only for channels that are already mapped.** The
first refresh logged `Parsing programs for 143 MAPPED channels … skipping 14503
unmapped EPG entries` and ended `25,618 programs for 143 channels, skipped
2,693,286 programs for unmapped channels`. So **a source added before its
channels exist will show 0 programmes and that is not a failure** — map the
channels, then refresh again. A run that reads programme count after the first
refresh and concludes the ingest failed has misread it.

## The sync, and what was read off B

B was destroyed (containers **and** both volumes), recreated, and its superuser
made in the browser. Its pre-sync baseline, read from **B's own Postgres**:
`0 channels / streams / groups / profiles / memberships / logos / EPG sources`,
`3 user agents (1-3)`, `5 stream profiles (1-5)`, `2 output profiles`,
`1 M3U account (custom)`, `1 user`. B's `/data/logos` empty; ECM's
`/config/uploads/logos` empty.

**Cycle 1 — apply onto empty B:** `completed_with_failures`, **created 1732,
updated 0, failed 0 across 9 categories, 41.6 s.**

**Cycle 2 — preview then apply onto the converged B:** the preview said **would
create 0, update 0, skip 1425, 0 conflicts**; the apply said **created 0, updated
316, failed 0**. The only row that changed anywhere in B was the one EPG link
that had been missing (below). Everything else was byte-identical across a full
dump of eleven collections.

### Every counter the run reported, checked against B

| Report field | Reported | Read off B |
|---|---|---|
| `logo_misses` | 0 | 316/316 channels carry a logo; all **316 channel→logo bindings identical to A** (number + name + logo URL) |
| `logo_reattach.created_channels` | 316 | matches |
| `epg_links_unrestored` (cycle 1) | **1**, named `USA: YES NETWORK [720p]`, `tvg_id YesNetwork(YES).us` | B had **182** links to A's 183; the missing one was **channel 301, exactly that channel** |
| `epg_links_unrestored` (cycle 2) | 0, `existing_channels 183` | B now **183 = A**; the gap **self-healed on the next cycle** |
| `stream_urls_redacted` | 316 | all 316 of B's URLs read `…/live/***REDACTED***/***REDACTED***/<id>.ts` |
| `channels_with_no_playable_stream` | 316, **on both cycles** | consistent — the downgrade is **not** a creating-cycle artifact (`ukjx5`) |
| `credentials_needing_reentry` | 1, `Live XC Provider`, fields `username`,`password` | B's XC account has `server_url` intact and **username/password empty** |
| `profile_membership_drift` | 316 on cycle 1, **0 on cycle 2** | final state: **948 membership rows, all identical to A**, enabled flag included |
| `channel_group_drift` | 0 | **779 groups, all 779 names identical** |
| `entities_blocked_by_dependency` | 0 | — |

### Side-by-side, both read from Postgres

| | A | B | |
|---|---|---|---|
| channels | 316 | 316 | match |
| channels with a logo | 316 | 316 | match |
| channels with an EPG link | 183 | 183 | match (after cycle 2) |
| streams / channel-stream links | 316 / 316 | 316 / 316 | match |
| logo records | 316 | 316 | match |
| channel groups | 779 | 779 | match |
| channel profiles / memberships / enabled | 3 / 948 / 632 | 3 / 948 / 632 | match |
| EPG sources / entries | 1 / 14,646 | 1 / 14,646 | match |
| user agents / stream profiles | 3 / 5 | 3 / 5 | match |
| programmes | 25,618 | 25,485 | **differs — expected**, B fetched the 7-day file itself at a later moment |
| M3U accounts | 2 | 3 | **differs — expected**, B gains the synthetic `ECM Custom Streams (DBAS restore)` account named in the report's own notes |
| `channelgroupm3uaccount` rows | 777 (2 enabled) | **0** | **differs — a gap, and an unreported one. See below.** |

### The credential sweep on B, both poles

Every credential-bearing column on B — `stream.url`, `stream.custom_properties`,
`m3u_m3uaccount` (`username`/`password`/`custom_properties`),
`m3u_m3uaccountprofile.custom_properties`, `epg_epgsource.url` — returned **0**
matches for the provider's username and for its password. **The same query on A
returns 316 and 1**, so the sweep can find a credential when one is there. B's
XC account also carries **no `player_api` blob at all**, only the four
auto-enable booleans — the cleartext-credentials hazard the screenshot section
warns about does not reach B.

## Findings

**1. The per-account group-enable state does not cross (fidelity gap, unreported).**
A holds 777 `dispatcharr_channels_channelgroupm3uaccount` rows with exactly 2
enabled; **B holds 0**, and no counter in the sync report mentions it. B's XC
account does carry `auto_enable_new_groups_live: false`, so a refresh on B would
not silently import the whole catalogue — but the operator who re-enters
credentials on B has lost the record of *which two categories A had enabled* and
must rediscover it. `channel_group_drift` reports `0` while this is true, so the
report is silent rather than wrong.

**2. ECM's Channel-Profiles "Save Changes" fans out one PATCH per channel with no
batching, no concurrency cap, no retry, and no visible failure (defect).**
Disabling 214 channels in one profile fired **214 concurrent
`PATCH /api/channel-profiles/<id>/channels/<channel_id>`** requests; **118
succeeded and 96 failed with `net::ERR_NETWORK_CHANGED`.** The profile on A was
left in a **partially applied** state (198 enabled instead of 102), and the
dialog went on showing the *intended* count (`102 / 316 enabled`) with its 214
changes still pending, so nothing in the UI said the save had not landed. A
second click of the same button converged it. Dispatcharr exposes a bulk
endpoint for exactly this (`/api/channels/profiles/<id>/channels/bulk-update/`,
`PATCH` — trap 5) which this path does not use. **Not fixed here.**

**3. A sync target's credentials and base URL cannot be edited after creation
(UI gap).** The target row offers only Enabled, Logos, Sync now, Apply and
Delete. Correcting a mistyped password means **deleting and recreating the
target**, which also resets the `sync_logos` toggle and discards
`last_full_sync_at` / `last_outcome`. This run hit it deliberately (the
wrong-password pole was a real target) and had to delete and recreate.

**4. The `stream` category still reports `updated 316` on a converged
destination.** A full before/after dump of B across cycle 2 differs by **one
line** — the healed EPG link — so **no stream row changed.** This is the
cosmetic overstatement already recorded under the `xdmru` recipe; it is still
live on `aed7447b`. Do not read `updated 0` as the idempotency criterion; read
the dump diff.

**5. The preview's `stream` category accounts for nothing.** On the converged
preview every other category reported `would_skip` equal to its row count
(779 groups, 316 channels, 316 logos …) while `stream` reported
`would_create 0, would_update 0, would_skip 0` for 316 existing streams. Minor,
but it means the preview's per-category totals do not sum to its own headline.

## Instruments, and the poles each was proven against

Nothing below was armed before it had produced a distinguishable verdict on a
known-good **and** a known-bad input.

| Instrument | Known-good | Known-bad |
|---|---|---|
| Playwright | navigating to A rendered the login wizard | a dead port raised `ERR_CONNECTION_REFUSED` rather than a blank page |
| the Postgres reader | real tables return integers | a bogus table name and a bogus container both exit **non-zero** with the error text — it never degrades to `0` |
| the collection dumper | 3,017 lines from B | pointed at a nonexistent container it exits `3` and writes one error line, not an empty "converged" dump |
| **the sync report itself** | correct password → `success`, would-create plan, **Apply appears** | wrong password → notification `type=error`, **`SYNC_DESTINATION_UNREADABLE`**, **no Apply button**, and **exactly ONE `401` in B's own log** — not the eight the pre-`jqfxm` path made |
| the credential grep | the provider's password matched 15 files before scrubbing; `Dispatcharr` matches 589 files in the tree | a nonsense token matches 0 |
| the running-tree checksum | **244 / 244** non-test `backend/**.py` match `git show aed7447b:` | the same harness against `aed7447b~40` reports **56 MISMATCH** |

**`/api/version` was not consulted.** It reports build metadata and has misled
four engineers on this work (trap 12); the checksum above is the evidence.

## Screenshot hazard, realised

The Playwright MCP writes an accessibility-tree `.yml` per navigation into
`.playwright-mcp/`. **15 of those files contained the provider's username and
password in cleartext**, because ECM's Add-M3U-Account dialog renders the
password field's value into the tree. The directory is gitignored
(`.gitignore:153`) so nothing was committed, but a gitignored path inside the
working tree is still a live credential on disk — **`.playwright-mcp/` was
deleted at the end of the run**, and a repo-tree grep afterwards returns **0**
hits for the username and **0** for the password. Delete it every time.

The provider **hostname** still appears in two committed files —
`frontend/src/utils/hdhomerun.test.ts` (pre-existing, commit `1d6b1450`,
2026-07-28) and `.beads/issues.jsonl` (already present at `HEAD`). Neither
carries a username or a password. Neither was introduced by this run.

## What this run does NOT cover

- **`cfxml`'s byte-copy half.** Every logo on A is a remote URL; A hosts no logo
  file, so no bytes had to move. The binding half was covered and passed.
- **`v7d37` (XC guide-URL redaction).** A's only EPG source is the
  credential-free `cdn.epg.guru` URL. No `xmltv.php?username=…` source existed,
  so the credentialed-EPG-URL case was not exercised.
- **`4mkoe` (dependency classification).** `entities_blocked_by_dependency` was
  `0` on every cycle; nothing was blocked, so the classifier had no work to do.
- **`efvyg` / fuzzy rebinding.** `fuzzy_stream_matching` was left `false` and no
  stream was deleted on B, so `streams_rebound` stayed `0` on both cycles.
- **Playability as video.** Every stream URL on B is redacted by design, so B's
  channels cannot play and no byte-level playability probe was run against B.
  A's streams were not re-probed either; the census in
  `xc-stream-census.json` describes the **retired** five-category lineup and
  does **not** describe this A.
- **Restore-path fixes** (`if05f` and anything reached only by a full restore).
  `users` is in `SYNC_NEVER_CATEGORIES`; the sync UI structurally cannot reach it.

## What bead `avrix` measured here afterwards, 2026-08-21/22

The finding above ("the per-account group-enable state does not cross") was
worked as bead `enhancedchannelmanager-avrix`, and the first thing it did was
answer the question the finding left open: **what does B do on its own refresh,
having inherited no group-enable state?** Reproduced twice against this A and
this provider, on 0.29.0 both sides.

- **As the replica actually arrives** (`auto_enable_new_groups_live: false`,
  inherited from A): its discovery refresh created all 777
  `channelgroupm3uaccount` rows **DISABLED** — 777 log lines reading
  `creating relationship but DISABLED (auto_enable_new_groups_live=False)` —
  then `Filtered 0 groups for processing: {}`,
  `Retrieved 53661 total live streams from provider`,
  `Filtered 0 streams from 0 enabled categories`, and
  `No streams collected ... aborting refresh to preserve the existing channel
  lineup`. **0 streams against A's 316.** The account lands in `status = error`,
  `No streams returned from Xtream Codes provider`, which blames the provider.
- **One boolean the other way it is far worse, and that boolean is Dispatcharr's
  own default.** `apps/m3u/serializers.py` pops
  `auto_enable_new_groups_live` with a `True` fallback on create (and ECM's
  synthesized `ECM Custom Streams` account on B carries all three auto-enable
  flags `true` for exactly that reason — ECM sets none of them). B inherits
  whatever A has. Armed with `true` and the same empty selection, B's discovery
  refresh enabled **777 of 777** categories in one pass. Celery was killed at
  that point and no streams were imported, but that is the provider's entire
  53,661-stream catalogue one refresh away.

So the SAME missing state sends a replica either way, and which way is decided
by a flag that defaults to the dangerous one. **Do not treat "B inherits
auto-enable OFF here, so it is safe" as a property of the system** — it is a
property of how this A's account happened to be created (trap 13).

**Traps this measurement adds.** Dispatcharr's celery log lists every registered
task name at worker startup, so a grep for `refresh_single_m3u_account` matches
the *registry listing*, not an invocation — match `Task …[` or
`Retrieved N total live streams` instead. And A's and B's channel-group ids
COINCIDE on a B built by one sync from an empty volume, so a live run cannot
tell a remap from a raw forward: create one decoy group on B first to shift its
ids, and confirm what sits at A's ids on B is a *different* group.

## State this run leaves behind

- **`ecm-kdz6p` no longer runs the tree of `aed7447b`.** Bead `avrix` deployed
  the full backend tree of its own branch into it (`fix/avrix-group-enable-state`
  at `65fa24d3`) and verified it the documented way: **244 non-test
  `backend/**.py`, 244 OK / 0 MISMATCH / 0 ABSENT** against `git show`, with the
  same harness against `e8b244c8` reporting 4 MISMATCH so the check is not
  vacuous. Its FRONTEND is still `aed7447b`'s. `/api/version` was not consulted
  (trap 12).
- **Dispatcharr A** (`:9601`) — 316 channels as described above, holding the PO's
  live XC credentials in its Postgres. Re-read at the end of the `avrix` run and
  unchanged: 316 channels, 316 streams, 779 groups, 777 `channelgroupm3uaccount`
  rows with **2** enabled. **Delete the `Live XC Provider` M3U account before
  handing this host to anyone else.**
- **Dispatcharr B** (`:9602`) — **destroyed and recreated at the end of the
  `avrix` run** (containers and both volumes), superuser re-created,
  `stream_settings` cache busted. Read from its own Postgres: 0 channels /
  streams / channel groups / `channelgroupm3uaccount` rows / channel profiles /
  logos / EPG sources; 3 user agents; 5 stream profiles; 1 M3U account
  (`custom`). It holds **no provider credentials** — the ones entered during
  that run went with the volume. The sync target in `ecm-kdz6p` still exists and
  still points at it.
- **ECM `ecm-kdz6p`** on `http://127.0.0.1:6500` (image `ecm-kdz6p:aed7447b`,
  config volume `ecm-kdz6p-config`), admin `ecm-kdz6p-admin`, holding one sync
  target with `sync_logos = 1`.
- **`ecm-docenv` was STOPPED** at the start of this run so it could not touch A
  while A was being rebuilt. It has not been restarted, and the A it is
  configured against is not the A described in its own handover section above.
- `ecm-ecm-1`, `ecm-ecm-mcp-1` and the operator's `dispatcharr` were **never
  addressed by any command**: same container ids, same `StartedAt`, zero
  restarts, before and after.

### Tear down

```bash
docker rm -f ecm-kdz6p
docker volume rm ecm-kdz6p-config
docker image rm ecm-kdz6p:aed7447b
```
plus the two blocks under "Tearing the whole thing down" above.

---

# Acceptance record - 2026-08-26

**ACCEPTED within the scope below** against Dispatcharr **0.29.0** on A and B
and ECM commit **`8e9fb19bc68de3f138bf73a3aeb2373596703cfe`**. The run used the
retained disposable environment and recorded no provider host, username,
password, stream URL, or raw custom property.

## Fresh-destination result

- Preview across 11 categories: **create 1,416 / update 6 / skip 9 / 0 conflicts**.
- Apply: **create 951 / update 6 / failed 0**.
- B after apply: **316 channels, 316 streams, 316 channel-stream links; 316 logos and 316 channel-logo bindings; 781 channel groups; 782 account-group rows with 5 enabled; 4 M3U accounts including the `ECM Custom Streams (DBAS restore)` fallback; 1 EPG source**.
- Playback: one representative B channel returned a client-bounded **1,880-byte MPEG-TS sample** with valid TS framing. The stream address was neither recorded nor printed.
- Credential propagation: a provider credential was rotated on A, propagated to B, then restored on A and propagated to B again. Verification compared outcomes without displaying or recording either value.
- ECM's `/config/uploads/logos/` directory remained empty. Logo acceptance therefore did not depend on planting a fixture in ECM's own upload directory.

## Repeat-run and idempotency result

- Repeat preview: **create 0 / update 6 / skip 1,109**.
- Repeat apply: **create 0 / update 322 / failed 0**.
- Normalized fingerprints for **15 destination collections** were unchanged before and after the repeat apply.

The non-zero repeat update counts are not destination-churn evidence. The
unchanged normalized destination fingerprints are the idempotency proof; sync
can report updates while leaving the converged destination state unchanged.

## Residual defect outside acceptance scope

**`enhancedchannelmanager-ydmu3` remains a separate defect and is explicitly
out of this acceptance scope.** ECM's UI bulk channel creation dropped the
staged stream assignments. Before continuing the acceptance run, the disposable
A fixture was repaired through the documented Dispatcharr channel `PATCH` API,
and only after the operator proved that each channel had one unique intended
stream mapping. No production instance was repaired or modified. Acceptance of
cross-instance sync does not accept, waive, or close the UI bulk-creation defect.

## Screenshot status

Two credential-safe, tightly scoped screenshots now exist and are referenced by
the guide: `docs/images/user_guide/backup-restore/1-sync-target-row.png` and
`docs/images/user_guide/backup-restore/2-sync-scheduled-task-card.png`. They show
only the disposable B target row and its closed scheduled-task card. They exclude
provider-facing fields, stream URLs, raw properties, forms, and browser-native
confirmation.

Broader captures remain prohibited because the retained environment contains
live provider material, and broader operational views can expose provider or
stream secrets through fields, raw properties, URLs, logs, or browser capture
artifacts.

# ARCHIVE — the retired synthetic-provider measurements

Everything below was measured against the **retired** Dispatcharr-P chain (a
59-channel synthetic lineup whose `.ts` URLs never served video). It is kept
because epic `enhancedchannelmanager-f5a5j` was diagnosed from it and its
findings are still open. **None of these numbers describe instance A as it is
today** — A now carries 132 channels sourced from the live XC provider.
Reproducing this run means bringing Dispatcharr P back up (see "What was
retired").

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
