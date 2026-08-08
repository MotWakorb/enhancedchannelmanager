# Run a Restore Drill

> **Status:** Operational procedure. Not a shipped feature; there is no "drill mode" button in ECM. This article is the whole procedure, written to be followed without repository access.

---

!!! danger "Read this before you start"
    This article is written for **Dispatcharr `0.28.2`** and **ECM
    `0.18.1-0040`**. A restore result is only a result for the version it
    was measured on. Every claim below carries the build it was last
    confirmed on; where a build isn't named, it's current as of `0.18.1-0040`.
    If you are on different versions, re-run the drill and update your own
    notes rather than trusting a claim past its pin.

    The most recent full drill (run 12, `0.18.1-0040`) reproduced the
    instance completely, **on a freshly-wiped target**, on both artifact
    variants, including a lineup that genuinely **plays**, proven by
    fetching real media bytes, not just checking that a URL is set. The
    genuine logo-failure path passed all five of its assertions again.
    Restoring onto an **already-populated target** is a different story
    and gets its own section: run 12 found that a channel's group
    membership drifting from the archive went completely unreported in
    either relink mode, and that a same-named-but-different channel group
    was adopted while the report claimed its contents had been compared.
    Both are **fixed as of `0.18.1-0041`**: drift is now reported on
    every mode and reconciled under overwrite, and the name-match skip
    now says what it actually checked, but that fix has not yet been
    proven by a live drill run against a populated target; run 12 itself
    measured the pre-fix behavior. See [Known failures](#known-failures)
    and [Restoring onto a populated
    target](#restoring-onto-a-populated-target) for the full account
    before you conclude your own drill passed.

---

## Why run this

Every other article in this section documents what the restore path is
*supposed* to do. This one tells you how to find out what it *actually*
does on your own versions, without trusting the restore-complete report at
face value. An earlier drill run (against ECM `0.18.1-0022`) reported a
clean success on a run whose actual result was: every channel present, not
one channel playable, every logo gone, and the count columns lined up
perfectly the whole time. Counts reconciling is not proof of anything by
itself.

The situation has since improved substantially (see the version pin and
danger admonition above), but the discipline still matters: this same
drill found a brand-new P0 blocker on the newer pin that a report-only
check would never have surfaced. This drill is the only way to know what
your own version actually does.

Run it:

- Before you rely on migration-by-restore for a real move (see
  [Migrate to a new install](migrate-to-a-new-install.md)).
- After any change to the backup/restore pipeline itself.
- Periodically, per the disaster-recovery runbook's
  [Recurring maintenance](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/disaster-recovery-restore.md#recurring-maintenance)
  cadence, if you want to prove the *apply* path (not just the dry-run
  preview that recurring maintenance normally checks).

---

## Safety rules (non-negotiable)

This drill destroys an entire Dispatcharr + ECM configuration on purpose,
repeatedly. Every rule below exists because getting it wrong destroys the
wrong instance instead.

1. **Never touch this stack without an explicit `-p` (project name) and
   `-f` (compose file) on every `docker compose` call.** Do not `cd` into
   a directory and rely on ambient project/file discovery. A bare
   `docker compose down -v` run from the wrong directory, or with no
   project flag, can reach containers and volumes you did not intend.
   Every command in this article includes both flags. Do not drop them,
   even to save typing.
2. **Never run `docker volume prune` or `docker system prune` as part of
   this drill.** Both are host-wide, not project-scoped. They will remove
   volumes belonging to containers that have nothing to do with the
   drill, including a real production instance running on the same
   Docker host. This drill only ever destroys its own project's named
   volumes, via `down -v` scoped to the drill's own `-p` project name.
3. **Rename the Dispatcharr container before the first `up`.** The
   upstream Dispatcharr all-in-one compose file names the service's
   container `dispatcharr`. If you run a production Dispatcharr instance
   on the same Docker host, its container is very likely also named
   `dispatcharr`. Two containers cannot share a name; if you bring up the
   drill stack with the default name, Docker will either refuse to start
   it or, worse, you will `docker rm` the wrong one while cleaning up.
   The compose file below already renames it (`bkr-dispatcharr`).
   **Do not remove that rename.** If you copy this file into your own
   tooling, keep the rename or pick your own, but never leave it at the
   upstream default if a real Dispatcharr container might exist on the
   same host.
4. **Use named volumes, never bind mounts, for the drill's `/config` and
   `/data`.** The entire premise of the drill is that `down -v`
   annihilates configuration. A bind mount survives `down -v` and would
   silently invalidate the test (you would restore onto config that never
   actually left).
5. **Pick host ports that do not collide with anything else running.**
   Check what is already listening before you choose. The compose file
   below uses `9391`, `6300`, and `6343`; treat those as a starting point,
   not a fixed requirement, and change them if they collide with your own
   host's existing services (including a production ECM or Dispatcharr).
6. **If you use ECM's MCP (Claude AI) integration, confirm which instance
   it is pointed at before running any MCP tool during the drill.** An MCP
   server configured against your production ECM will act on production,
   not the drill stack, regardless of what you're looking at in a browser
   tab.
7. **Evacuate and verify backup artifacts before you destroy anything.**
   See [Evacuate and verify](#step-4-evacuate-and-verify-before-you-destroy-anything)
   below. This is the single most important ordering rule in the whole
   procedure: if you destroy before you've confirmed the artifacts are
   good copies sitting outside the containers, a bad drill run leaves you
   with nothing to restore and nothing to learn from.

---

## The drill stack

This is a disposable Docker Compose project, separate from any production
ECM/Dispatcharr install. It is derived from the upstream Dispatcharr
all-in-one (AIO) compose file with a small number of deliberate changes,
called out in the comments below. Save it as
`docker-compose.backup-restore-drill.yml` anywhere on your drill host.

```yaml
# ECM backup/restore round-trip drill stack.
#
# DISPOSABLE. This project is created and destroyed repeatedly by the drill.
# It is NOT a production stack. If you run a real Dispatcharr or ECM
# instance on this host, nothing in this file may ever touch it.
#
# ALWAYS invoke with an explicit project name and an explicit file:
#   docker compose -p ecmbkr -f <this file> up -d
#   docker compose -p ecmbkr -f <this file> down -v
#
# Derived from upstream Dispatcharr docker/docker-compose.aio.yml with
# exactly these deltas:
#   - added `name: ecmbkr` so `down -v` can only reach this project
#   - container_name dispatcharr -> bkr-dispatcharr  (upstream's default name
#     may be byte-identical to a real production container on this host --
#     never let the drill `docker rm` a container it doesn't own)
#   - image :latest -> a pinned version tag (floating tags make runs
#     incomparable across time)
#   - host port 9191 -> 9391 (9191 is commonly a production instance)
#   - restart: unless-stopped -> "no" (throwaway; nothing should resurrect)
#   - added a healthcheck so ECM's depends_on has a real ready signal
# Everything else is upstream as shipped, commented blocks included.

name: ecmbkr

services:
  dispatcharr:
    # build:
    #   context: .
    #   dockerfile: Dockerfile
    image: ghcr.io/dispatcharr/dispatcharr:0.28.2
    restart: "no"
    container_name: bkr-dispatcharr
    ports:
      - "9391:9191"
    volumes:
      - dispatcharr_data:/data
    environment:
      - DISPATCHARR_ENV=aio
      - REDIS_HOST=localhost
      - CELERY_BROKER_URL=redis://localhost:6379/0
      - DISPATCHARR_LOG_LEVEL=info
      # Legacy CPU Support (Optional)
      # Uncomment to enable legacy NumPy build for older CPUs (circa 2009)
      # that lack support for newer baseline CPU features
      #- USE_LEGACY_NUMPY=true
      # Process Priority Configuration (Optional)
      # Lower values = higher priority. Range: -20 (highest) to 19 (lowest)
      # Negative values require cap_add: SYS_NICE (uncomment below)
      #- UWSGI_NICE_LEVEL=-5   # uWSGI/FFmpeg/Streaming (default: 0, recommended: -5 for high priority)
      #- CELERY_NICE_LEVEL=5   # Celery/EPG/Background tasks (default: 5, low priority)
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:9191/api/core/version/"]
      interval: 10s
      timeout: 5s
      retries: 60
      start_period: 60s
    #
    # Uncomment to enable high priority for streaming (required if UWSGI_NICE_LEVEL < 0)
    #cap_add:
    #  - SYS_NICE
    # Optional for hardware acceleration
    #devices:
    #  - /dev/dri:/dev/dri  # For Intel/AMD GPU acceleration (VA-API)
    # Uncomment the following lines for NVIDIA GPU support
    # NVidia GPU support (requires NVIDIA Container Toolkit)
    #deploy:
    #  resources:
    #      reservations:
    #          devices:
    #              - driver: nvidia
    #                count: all
    #                capabilities: [gpu]

  ecm:
    # :dev, NOT :latest -- pick the tag that matches what you actually want
    # to measure. Note the exact tag/build in your own drill notes; a result
    # is only a result for the version it was measured on.
    image: ghcr.io/motwakorb/enhancedchannelmanager:dev
    restart: "no"
    container_name: bkr-ecm
    ports:
      - "6300:6100"
      - "6343:6143"
    volumes:
      # NAMED VOLUME, never a bind mount. The drill's entire premise is that
      # `down -v` annihilates ECM's config; a bind mount would survive the
      # wipe and silently invalidate the test.
      - ecm-config:/config
    environment:
      - PUID=1000
      - PGID=1000
      - CONFIG_DIR=/config
      - ECM_PORT=6100
      - ECM_HTTPS_PORT=6143
    depends_on:
      dispatcharr:
        condition: service_healthy

volumes:
  dispatcharr_data:
  ecm-config:
```

### Port assignments

| Service | Container port | Host port (this drill) | Notes |
|-|-|-|-|
| Dispatcharr | `9191` | `9391` | Browser: `http://192.168.1.50:9391` (replace with your drill host's own address) |
| ECM (HTTP) | `6100` | `6300` | Browser: `http://192.168.1.50:6300` |
| ECM (HTTPS) | `6143` | `6343` | |

Before your first `up`, confirm none of `9391`, `6300`, `6343` are already
bound on your host (`ss -ltnp` or `docker ps --format '{{.Ports}}'`), and
confirm `docker volume ls` shows no leftover volume from a prior drill run
under this project name. If any of the three ports collide with something
else on your host, change the host-side number in the compose file before
you start; do not change the container-side number.

---

## Step 1: Roll out the source instance

```bash
docker compose -p ecmbkr -f docker-compose.backup-restore-drill.yml up -d
```

Wait for `bkr-dispatcharr` to report healthy (the healthcheck polls
`/api/core/version/`), then confirm `bkr-ecm` started after it
(`depends_on: service_healthy` should have gated it).

```bash
docker compose -p ecmbkr -f docker-compose.backup-restore-drill.yml ps
```

Both containers should show `running (healthy)` or `running`.

**Dispatcharr first-run wizard.** Open `http://192.168.1.50:9391` in a
browser. A fresh `/data` volume presents "Create your Super User Account."
Create one now, and **write down the exact username you choose** (for
example `drilladmin`), not because you need to match it later, but so
you can confirm in [Step 6](#step-6-restore) that the rebuilt
Dispatcharr's superuser is genuinely **different**. As of `0.18.1-0023`,
a mismatched superuser name is no longer a failure mode
(`enhancedchannelmanager-y65si`, fixed and closed); using a different
name on purpose is what keeps this drill proving the fix rather than
hiding a regression that no longer exists.

A fresh Dispatcharr `0.28.2` install also ships with **5** locked stream
profiles (`ffmpeg`, `Proxy`, `Redirect`, `streamlink`, `VLC`), **3** user
agents (`TiviMate`, `VLC`, `Chrome`), and **1** M3U account (`custom`).
Use these as your baseline object counts if you're diffing a "before" and
"after" inventory by hand.

**Dispatcharr API key.** In Dispatcharr 0.28.2, the working path to mint a
key is **System → Users → (edit your user, pencil icon) → "API & XC" tab →
Generate API Key**. The `Account → API Keys` path referenced in some ECM
in-product copy does not exist; use the path above. Copy the key value
somewhere you control; you will paste it into ECM next and it is the kind
of credential that must never end up committed anywhere.

**ECM first-run wizard.** Open `http://192.168.1.50:6300`, create an ECM
admin account. ECM rejects `.local` email addresses at signup ("reserved
name"); use something like `admin@example.com` instead. It also rejects a
password that contains the username ("Password cannot contain your
username"); the inline hint does not say so, so if the create silently
fails, that's the likely reason.

**Connect ECM to Dispatcharr.** Use the **API Key** authentication method
(recommended; immune to Dispatcharr's login rate limit). If both
containers are on the drill's own compose network, address Dispatcharr by
its service name from inside ECM's connection form
(`http://bkr-dispatcharr:9191`), not the host-mapped port. Use the
host-mapped address (`http://192.168.1.50:9391`, or a stable DNS name such
as `dispatcharr.example.local:9191` if the two are *not* on a shared
Docker network) only when ECM cannot reach Dispatcharr by container name.
Confirm **Test Connection** reports **Connected**, then **Save**. Both of
Dispatcharr's documented auth paths work here: a later drill run tested
username/password and API key side by side, and both returned
`{"success":true,"message":"Connection successful"}`.

!!! note "Historical: opening the ECM web UI used to turn authentication on"
    **This no longer reproduces on `0.18.1-0040`.** A brand-new install
    now reports `"require_auth": true` from `GET /api/auth/status`
    **before any browser has opened it**. There is no anonymous window
    to rely on. Authenticate your API calls from the start (cookie-based:
    `POST /api/auth/login`, then keep the cookie jar); do not script a
    restore assuming anonymous access will work even briefly.

    The rest of this note is kept for anyone on an older build who hits
    a `401` partway through a scripted run and needs to know why.
    Measured on a fresh install on that earlier pin: after completing
    setup over the API (`POST /api/auth/setup`), ECM's API stayed
    reachable **anonymously**; a scripted drill run drove its restores
    with no credentials at all. The first time the ECM **web UI** was
    opened, an auth-settings write occurred (log: `[AUTH-SETTINGS] Auth
    settings saved` at 23:11:29.320, immediately after `GET /?cb=…` at
    23:11:29.258), and every subsequent API call returned `401 Not
    authenticated`; `/config/auth_settings.json` then showed
    `"require_auth": true`. No restore wrote auth settings in that run,
    and `require_auth` does not appear anywhere in the backup artifact.
    If you script a restore against a brand-new install on that build and
    it works, then stops working with `401` after someone opens the web
    UI, this is why, but the cookie-jar advice above is the right
    instruction regardless of which build you're on.

---

## ECM-uploaded logos and this drill

!!! success "Fixed as of 0.18.1-0024: uploaded logo bytes are archived, and a logo miss is no longer fatal"
    Run 2 of this drill (`0.18.1-0023`) found that the backup never
    archived the bytes of a logo uploaded through ECM's own Logo Manager,
    and that the resulting miss on restore was classified as fatal,
    aborting and rolling back the entire run. Both are fixed as of
    `0.18.1-0024`:

    - **The backup now archives uploaded-logo bytes.** A logo uploaded
      through ECM's Logo Manager is written to **Dispatcharr's**
      `/data/logos/`, not ECM's own `/config/uploads/logos/`. The backup
      now fetches those bytes at gather time over Dispatcharr's own logo
      cache endpoint, using the same API key ECM already holds for every
      other backup category, and archives them alongside the logo's
      filename. A round-trip drill confirmed the restored file is
      byte-for-byte identical to the source (matching SHA-256), on both
      artifact variants.
    - **A logo failure is no longer fatal.** Logos joined Dispatcharr
      users as a non-fatal restore category: a logo that cannot be
      restored is counted and named in the report, and the rest of the
      restore completes as `completed_with_failures` rather than rolling
      back.

    **Logos referenced by a remote http(s) URL are unaffected either
    way** and always round-tripped correctly: a drill run measured 10 of
    10 restored byte-identical. Only logos uploaded through ECM's own
    Logo Manager were ever affected by this gap.

    **On builds before `0.18.1-0024`:** neither fix applied. Any
    ECM-uploaded logo on the source made the restore abort and roll back
    entirely. The only way to get a restore to complete on those builds
    was to remove ECM-uploaded logo records before taking the backup, at
    the cost of the logo not being in the backup at all. If you are
    drilling against an older build, expect that behavior instead of what
    this section describes.

    **For this drill:** on `0.18.1-0024` and later, upload a logo through
    ECM's own Logo Manager in Step 2 below like any other seeded data;
    the restore should complete and the logo should come back intact.
    This is now the useful case to seed, since it exercises the archive
    path the earlier drill runs could not reach.

!!! success "The genuine-failure path was measured live for the first time in run 9"
    Every earlier drill exercised the happy path only: an uploaded logo
    that restores cleanly. Run 9 (`0.18.1-0035`) constructed a real
    failure instead, by occupying the archived logo's destination path
    with a directory before applying the restore, and checked all five
    non-fatal-logo claims directly rather than assuming them from the
    happy-path result:

    - The restore **completed** (`outcome: completed_with_failures`),
      never a rolled-back outcome.
    - **Nothing else was rolled back.** Every other category matched the
      pre-restore counts exactly; only the one blocked logo was missing.
    - The failure was **named**: `reason: upstream_api_error`, the
      correct logo label, and the correct affected channel.
    - **The instance still played**, including the channel whose logo
      failed.

    One thing was wrong: `logo_misses` read **2** for the one logo that
    actually failed (`enhancedchannelmanager-k2r7m`). The report's own
    category line and its `note` field agreed on one failure; only the
    aggregate count doubled it. **Fixed as of `0.18.1-0036`:** a second
    report of a logo already recorded now merges into that logo's row
    instead of appending a duplicate. If you deliberately reproduce this
    failure to prove the non-fatal path yourself, expect `logo_misses` to
    equal the actual number of failed logos, not double it.

---

## Step 2: Seed a small instance

**Use exactly one small M3U group.** This is the single most important
scheduling decision in the whole drill. A whole-catalogue M3U refresh can
take on the order of 25 minutes with no progress indication. Restricting
sync to one small group brings convergence down dramatically: run 2
measured **~1.4 seconds** for a single group of 110 streams, and a later
drill run measured **2 seconds** for a single group of 96 streams.

1. Add an XtreamCodes M3U account pointed at your real provider (for
   example `https://provider.example.com`), with your real credentials.
   Give it a name that isn't a customer-identifying string if you plan to
   share drill screenshots (for example `Birch`, not your provider's
   account label).
2. **Uncheck "Auto-enable new groups (Live)"** before saving, so you get
   to choose one group instead of all of them.
3. After the account is created, it will show an ERROR status with "No
   streams returned from Xtream Codes provider" while reporting `0 / N`
   groups enabled. This is expected and self-correcting: group discovery
   already succeeded, the "error" is just the consequence of zero groups
   being enabled yet.
4. Enable exactly **one** group (pick something small, ideally under a few
   hundred streams), then click **Save & Refresh**.
5. Confirm the account reaches **READY** with a non-zero stream count.
   Expect convergence in well under a minute for a single small group
   (seconds, not minutes), not the ~25 minutes a full-catalogue refresh
   takes.

**Add an EPG source.** Any small XMLTV or Schedules Direct source is
sufficient; refresh it and confirm entries populate. A large XMLTV
source is still workable: a later drill run measured `UnitedStates.xml.gz`
(14,668 entries) refreshing in 35 seconds.

**Create a handful of channels through ECM**, in Edit Mode: select
streams from the group you enabled, use "Create in…" to place them into
one or two channel groups, then **Done → Apply All**. Nothing reaches
Dispatcharr until you click **Apply All**. A staged change in Edit Mode
looks finished but is not yet applied.

!!! warning "\"Create in…\" requires a Starting Channel Number, silently"
    The create dialog requires a **Starting Channel Number**. When you
    create into an **existing** channel group, this field comes up empty.
    While it's empty, the dialog's `Create N Channels` button is
    **disabled with no visible validation message**. It reads exactly
    like a broken button. Confirmed on `0.18.1-0040`. Fill in a starting
    channel number before you go looking for a bug.

**To exercise the restore path more thoroughly** (recommended if you have
the time; each of these maps to a specific restore behavior worth
proving), also set up:

| Feature | Why it's worth seeding | Where to create it |
|-|-|-|
| A multi-stream channel with a deliberate stream order | Proves whether stream ordering survives the restore's 4-tier matcher | ECM, Edit Mode |
| A channel profile with non-default membership (some channels excluded) | Proves whether profile scoping survives, or silently widens to "all channels" | Create the profile in ECM, then set membership in ECM too (see below) |
| A custom stream profile assigned to a channel | Proves the user-agent binding fix (`enhancedchannelmanager-lvfwd`, closed) still holds | Create in Dispatcharr (see below); assign in ECM, Edit Channel → **STREAM PROFILE** |
| A custom user agent assigned to that stream profile | Same as above; the two are usually configured together | Create in Dispatcharr (see below) |
| A channel with its logo deliberately cleared | Gives you a known-absent case to contrast against the known-present ones | ECM |
| At least one non-default ECM setting (timezone, poll interval, etc.) | Proves whether settings actually restore or silently revert to defaults | ECM, Settings |
| EPG links on most but not all channels | Gives you a mix to check post-restore rather than an all-or-nothing signal | ECM |

If any seeded logo is a **remote CDN URL** auto-assigned from the M3U
feed (the common case), the backup's binary logo subtree will be empty
and most of the logo-restore path goes untested.

!!! warning "User agents and stream profiles: ECM can assign, not create. Dispatcharr's UI doesn't create them either."
    ECM's Edit Channel dialog can *assign* an existing stream profile to a
    channel, but ECM has no UI to create a stream profile or a user agent
    at all; neither object is one of ECM's 12 backup categories on its own
    (they ride in as attributes of what references them). Dispatcharr
    `0.28.2`'s own Settings page only exposes **Default User Agent** /
    **Default Stream Profile** dropdowns over objects that already exist,
    with no visible create-or-manage screen for either.

    Drill run 9 confirmed the only working path is Dispatcharr's REST API:

    ```bash
    curl -X POST http://<dispatcharr-host>:<port>/api/core/useragents/ \
      -H "X-API-Key: <key>" -H "Content-Type: application/json" \
      -d '{"name": "Run9 Custom Agent", "user_agent": "Run9/1.0"}'

    curl -X POST http://<dispatcharr-host>:<port>/api/core/streamprofiles/ \
      -H "X-API-Key: <key>" -H "Content-Type: application/json" \
      -d '{"name": "Run9 Custom Profile", "user_agent": <agent id from above>}'
    ```

    Then, in ECM, open **Edit Channel → STREAM PROFILE** on the channel
    you want to prove the binding on, and select the profile you just
    created. If you skip this pair entirely rather than create it via the
    API, you silently lose the only coverage this drill has for the
    user-agent binding fix (`enhancedchannelmanager-lvfwd`); a restore
    that never had a custom stream profile to restore proves nothing about
    whether the binding survives.

!!! note "Channel-profile membership: set it in ECM"
    Create the channel profile itself in ECM, then set membership without
    leaving ECM. Confirmed working on `0.18.1-0040`:

    **Channel Manager → `⋮` (the overflow control at the top of the
    CHANNELS panel) → Channel Profiles → `tune` on the profile row.** This
    opens a **Manage Channels** panel for that profile showing `N / N
    enabled`, per-channel toggles grouped by channel group, `Enable
    Visible` / `Disable Visible`, and a `Save Changes (N)` button. Set a
    non-default membership here and it persists to Dispatcharr.

    Dispatcharr's own **Channels** view can also toggle membership per row
    (select the profile from its dropdown), if you're already there for
    another reason. But ECM's own **Manage Channels** panel is the
    working, discoverable path. You do not need to leave ECM for this.

!!! note "Upload a logo here to exercise the archive path"
    See [ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill)
    above. On `0.18.1-0024` and later, uploading a logo through ECM's own
    Logo Manager is the useful case to seed: it exercises the archived-bytes
    path that a remote-URL-only logo does not. On an older build, expect
    this to make the restore abort and roll back instead.

---

## Step 3: Take both backup artifacts

On a brand-new install, `/config/backups` does not exist yet. You do not
need to create it by hand: the DBAS Backup task creates the directory
itself, as the container's own user, the first time it runs. Hand-staging
a `/config/backups` directory is only needed if you want to restore an
artifact this instance did not produce itself (for example, copying in a
`.zip` from another host to test the Saved Backups path against it).

If you are scripting the backup via the API rather than clicking through
Scheduled Tasks, `GET /api/tasks/dbas_backup/parameter-schema` documents
the encrypted-backup options directly. As of `0.18.1-0035` it returns an
empty `parameters` array (there is nothing to persist to a *schedule*)
alongside a `run_parameters` array describing the manual-run-only fields:

```json
"parameters": [],
"run_parameters": [
  {"name": "passphrase", ...},
  {"name": "include_credentials", ...},
  {"name": "acknowledge_unrecoverable", ...}
],
"description": "Manual-run encryption parameters, sent in the 'parameters'
                 object of POST /api/tasks/dbas_backup/run. Never persisted
                 to a schedule; a scheduled run always produces the default
                 redacted artifact."
```

Send those three fields nested under `{"parameters": {...}}` on the
run-task request to produce an encrypted, credential-bearing artifact.
The empty top-level `parameters` array is accurate, not misleading: it
correctly says a *schedule* cannot carry these options, and the
`run_parameters` array right next to it is the best available
documentation of what a manual run accepts.

Take them **in this order**: standard first, encrypted second. Creating
an encrypted backup leaves the `DBAS Backup` task producing encrypted,
credential-bearing artifacts for every later run (including scheduled
ones) until the container restarts (`enhancedchannelmanager-cytzj`). If
you take the encrypted one first, your "standard" artifact will silently
come out encrypted too.

1. **Standard (redacted):** **Settings → Scheduled Tasks → DBAS Backup →
   Run Now**. Wait for the completion notification.
2. **Encrypted + credentials:** **Settings → Backup & Restore → Encrypted
   Backup**. Check the acknowledgement, set a passphrase (do not reuse a
   real passphrase you use elsewhere; this is a throwaway artifact), enable
   **Include credentials**, and click **Create Encrypted Backup**.

Confirm both completed at **success**, not warning-level, in the
**Notifications panel** (the bell icon in the header). That is where the
per-task severity actually lives: look for a success-level entry for each
`DBAS Backup` run (icon and heading along the lines of `Task Completed:
DBAS Backup`, not an exact transcript, since the message body's wording
and counts change between builds), not a warning-level one. **Settings →
Backup & Restore → Saved Backups** will not tell you this: that panel
lists filename, timestamp, and size, with no status, severity, or outcome
field anywhere in it. A warning-level backup means the source artifact
was already degraded before you ever get to the restore side, and any
drift you find afterward can't be cleanly attributed to the restore path.

---

## Step 4: Evacuate and verify, before you destroy anything

This is the gate. Do not proceed past it until both checks below pass.

!!! warning "The two artifacts are not distinguishable by filename"
    Both land in `/config/backups` as
    `ecm-backup-<YYYY-MM-DD>_<HHMMSS>.zip`. Nothing in the filename, the
    Saved Backups row, or the notification says which is which, so
    `<standard-file>` and `<encrypted-file>` below are not something you
    can read off the container by name. Copy everything out, then
    identify each file: an **encrypted** artifact is not a readable zip
    (attempting to open it as one fails with `BadZipFile`); a standard
    artifact opens normally. This is the reliable discriminator, not just
    a caveat about peeking inside one.

    ```bash
    for f in ./artifact/*.zip; do
      python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1])" "$f" \
        2>/dev/null && echo "$f: standard" || echo "$f: encrypted"
    done
    ```

    Rename the two files (or just note which is which) before continuing,
    so the rest of this procedure's `<standard-file>` / `<encrypted-file>`
    placeholders are unambiguous.

1. Copy both `.zip` artifacts and their `.sha256` sidecars out of the
   container, onto the drill host's filesystem (not a volume that will be
   wiped):

   ```bash
   docker cp bkr-ecm:/config/backups/. ./artifact/
   ```

   Then identify which is which, as above.

2. Verify both **on the host copies**, not the in-container ones:

   ```bash
   cd artifact
   sha256sum -c <standard-file>.zip.sha256
   sha256sum -c <encrypted-file>.zip.sha256
   ```

   Expected output: `<file>.zip: OK` for both.

**Do not run `down -v` until both commands print `OK`.** If either check
fails, do not destroy the source instance. Re-take the failing backup and
re-verify. An unverified copy is not a backup; it's an unproven claim
that a backup exists.

Optional but recommended: capture a "before" inventory (channel count,
group count, M3U/EPG source counts, ECM settings you changed, logo count
and hashes) so you have something concrete to diff against after the
restore, rather than relying on the restore report's own counts alone.
Earlier drill runs measured those counts wrong before the reporting
fixes documented in [Known failures](#known-failures) landed; capturing
your own inventory is good practice regardless of which build you're on.

---

## Step 5: Destroy and roll out clean

```bash
docker compose -p ecmbkr -f docker-compose.backup-restore-drill.yml down -v
```

Confirm the named volumes are actually gone:

```bash
docker volume ls | grep ecmbkr
```

Expected: no output. If either `ecmbkr_dispatcharr_data` or
`ecmbkr_ecm-config` still exists, the wipe did not complete; do not
proceed to restore against a target that might still have prior state.

Bring up a genuinely fresh stack:

```bash
docker compose -p ecmbkr -f docker-compose.backup-restore-drill.yml up -d
```

Wait for `bkr-dispatcharr` healthy, then `bkr-ecm` up, exactly as in
Step 1.

---

## Step 6: Restore

**Dispatcharr first-run wizard, again.** Create the superuser with a
**different** username than the one you used in Step 1 (for example
`rebuiltadmin` or `secondadmin`, vs. the source's `drilladmin`). Do this
on purpose. Neither of the two workarounds this article used to document
here is needed on `0.18.1-0023`:

!!! success "Both former blockers are fixed and closed on this pin"
    - **Differently-named superuser.** Used to abort and roll back the
      entire restore at the `user` category (`enhancedchannelmanager-y65si`).
      **Fixed.** Run 2 used deliberately different superuser names on
      every restore and both completed. Matching the source's username is
      no longer required, and deliberately *not* matching it is what
      keeps this drill proving the fix stays fixed, rather than quietly
      re-introducing the workaround for a bug that no longer exists.
    - **Custom user agent for a custom stream profile.** Used to require
      pre-creating a matching user agent on the target before restoring,
      or the restore would fail (or silently bind the wrong agent)
      (`enhancedchannelmanager-lvfwd`). **Fixed.** The restore now binds
      to the correctly **named** agent, verified in run 2 even with decoy
      user agents deliberately occupying the source's numeric id on the
      target. Do not pre-create a matching user agent: leaving the
      target's user agents exactly as Dispatcharr's first-run wizard
      created them is what proves the fix.

**Mint a fresh API key and connect ECM**, same as Step 1.

**Now run the restore, once per artifact variant** (you took two; restore
each onto its own fresh rollout of the target so the two runs don't
contaminate each other, or run them sequentially with a `down -v` /
`up -d` cycle between them if you only have one target host):

1. **Settings → Backup & Restore → Restore DBAS Backup**, upload the
   artifact.
2. If encrypted, enter the passphrase.
3. Click **Preview** (dry-run). Sanity-check the counts.

   !!! warning "What a preview does and does not predict"
       As of `0.18.1-0032`, the logo and EPG-link reattach splits and
       `profile_membership_drift` match what the apply then does. The two
       stream-health counters (`channels_needing_stream_reattach`,
       `channels_with_no_playable_stream`) read **`null`**, meaning "not
       predicted": the pass that writes them matches against streams the
       deferred M3U refresh materializes, and a preview refreshes nothing.
       Read `null` as "unknowable until the apply", not as zero.

       **`channel_groups` and `Streams` need a second look, though.**
       Measured on `0.18.1-0040` (`enhancedchannelmanager-tddmw`): a
       preview reported `378 WILL CREATE / 0 WILL SKIP` for
       `channel_groups`; the same artifact's apply reported `3 CREATED /
       375 SKIPPED`. Every other category matched. Mechanism, still true
       and still worth knowing: the restore creates the M3U account
       first, and that account's ingest materializes the provider's
       groups before the `channel_groups` category runs, so by apply time
       most of them already exist and are skipped, while the preview,
       which refreshes nothing, predicts creates against the state it
       can actually see. The end state is correct either way (378 groups
       exist afterward); this was always a reporting divergence, not data
       loss. **As of `0.18.1-0041`, the preview says so itself:** the
       `channel_groups` category now carries an explanatory caveat naming
       exactly this ordering, instead of leaving the operator to notice
       the mismatch unaided.

       The same fix also gave the `Streams` category (the one that
       synthesizes placeholder streams when the matcher misses) a row on
       the preview for the first time. It used to be absent entirely (not
       `0`, missing); it now appears **not predicted**, the same
       treatment the two stream-health counters above already get:
       Streams is matched against this install's own streams, which the
       deferred M3U refresh only materializes during the apply, so a
       preview has nothing to compare against.

       Read a `channel_groups` caveat and a `Streams: not predicted` row
       as the preview being honest about its own limits, not as proof
       nothing will change there. The apply is still the number that
       counts.

       On builds before `0.18.1-0032`, the preview reported **every**
       URL-restorable logo as `validation_error: unsafe or empty logo
       filename`, even ones that restored fine on apply. Run 2 measured
       this directly: the preview flagged all 11 seeded logos as failures;
       the apply then restored 10 of them correctly and failed only the
       one genuinely-lost logo (`enhancedchannelmanager-dgnms`). The same
       pin reported a confident `0` for both stream-health counters where
       the apply reported 12 and 12. **Preview first, always**, but on
       those builds do not abort a restore because of logo failures shown
       in a preview; the real miss is easy to lose among the invented ones.

4. Click **Apply these changes**. The confirmation dialog requires you to
   **type the artifact's exact filename** before it will let you
   proceed; this is a type-to-confirm safety gate, not a broken button.
   If nothing happens when you click confirm, check that what you typed
   matches the filename exactly.
5. Read the restore-complete report. Note the outcome (`success`,
   `completed_with_failures`, `partial_failed_rolled_back`, etc.), the
   created/updated/skipped/failed counts per category, and the elapsed
   time.

Tip: if you ever want a quick sanity check that the preview logic itself
is behaving, restore an artifact onto the same instance it came from. It
should preview as entirely `already_exists_identical` across every
category.

**Relink mode default.** A restore onto a target that already has the
channels (a second restore, or a restore onto a populated instance)
accepts a relink mode governing whether an existing channel's own EPG
link and logo are preserved, or overwritten by the archive's values. A
later drill run proved the default resolution on an **apply**, not just
the echoed request value, across all four degenerate configurations a
scripted caller might send: the field absent, `null`, an empty string,
and an unrecognised string. All four resolved to `preserve`, and the
operator's own EPG links and logo on the destination survived the
restore in every case.

If a restore fails and rolls back, the instance is **not necessarily
back to its exact pre-restore state**. Settings changes are not
compensatable and remain applied even after a rollback, and anything
Dispatcharr created on its own outside the restore's tracked ledger (for
example, groups created by an M3U ingest that started mid-restore) is not
touched by the rollback either. If you hit a failed attempt, treat the
next attempt as starting from an unknown state, not a clean one. A fresh
`down -v` / `up -d` cycle is the only way to guarantee a clean baseline
for your next try.

---

## Step 6a: If you restored a standard (redacted) artifact, recover credentials before you check playback

Skip this and playback will look broken when it's actually just
unfinished.

A standard (redacted) artifact has no M3U password, so nothing was ever
ingested from the real provider. Right after the restore, playback is
*expected* to fail, not because of a defect, but because the M3U account
has no credential yet.

**The recovery sequence, as of `0.18.1-0033`:**

1. Re-enter the provider credential on every M3U account the restore
   report or the post-restore UI names as needing it. Both name the exact
   account and field; see [Improved reporting](#improved-reporting) below.
2. Refresh that M3U account, and wait for the refresh to complete.

That is the whole recovery. When the refresh finishes, ECM re-runs the
reattach pass over the streams it has just materialized, moves every
channel off its placeholder onto the real stream, and then deletes the
leftover placeholders and the synthetic `ECM Custom Streams (DBAS
restore)` account. Only placeholders ECM itself created are touched.
Measured end to end: 12 channels went from unplayable to all playing after
the refresh alone, finishing with zero leftover placeholder streams and
the synthetic account gone.

!!! note "Which refresh actions trigger the reattach, and which do not"
    **Covered:** the **Refresh** action on an individual M3U account, and
    the scheduled M3U refresh task.

    **Not covered:** a "refresh all accounts" action, and a refresh
    performed in Dispatcharr's own UI. Neither reports completion back to
    ECM in a way the reattach can hang on, so an instance that reached
    real streams by one of those routes heals on the **next scheduled M3U
    refresh** rather than immediately. For a drill, refresh the individual
    account so you are measuring the immediate path.

**On builds before `0.18.1-0033`,** step 2 was not enough, and this was
the part operators would not guess. The reattach pass ran once,
immediately after the restore's own deferred M3U refresh, and never
re-ran on its own. On a redacted artifact there was nothing to match
against at that instant, so a later manual refresh added the real streams
*beside* the placeholders and rebound nothing. Recovery needed a third
step: **run the same restore again**, from the same artifact. Measured on
that pin, in three states:

| State | Streams | Channel bindings | Playback (bytes actually fetched) |
|-|-|-|-|
| 1. Straight after the restore | 14, **0 with a URL** | 12 channels on placeholders | **0/2 (HTTP 500)** |
| 2. After re-entering the credential **and** refreshing the M3U account | 124 (110 real) | **still** 12 on placeholders | still fails |
| 3. After **running the same restore again** | 124 | all 12 on real streams, correct order | **2/2 (HTTP 200, `video/mp2t`, real bytes)** |

If your drill is on a build before `0.18.1-0033`, expect state 2 and do
the third step. If you are on `0.18.1-0033` or later and state 2 still
leaves channels on placeholders after the refresh **completed**, that is
a fresh finding worth filing with the restore report attached.

**Alternative: skip this whole sequence.** An **encrypted artifact with
"Include credentials" enabled** restores the credential automatically.
Verified with an identical credential fingerprint before and after, and
playback working on the first restore, no recovery pass needed. If you
don't specifically need the redacted variant's smaller/shareable
footprint, prefer the encrypted-with-credentials path for any artifact you
actually intend to restore onto a working instance.

---

## Step 7: Verify

Do not trust the restore-complete report's counts alone. Check, by hand:

1. **Playback: fetch bytes, don't just look at a URL field.**
   "A URL is set" is not confirmation of playback. Fetch the stream and
   assert both a **2xx status and real media bytes**, with a hard timeout
   so a hanging stream can't stall the check:

   ```
   GET http://<dispatcharr-host>:<port>/proxy/ts/stream/<channel-uuid>
       header: X-API-Key: <key>
   ```

   **Use at least a 60-second deadline for this first fetch.** This has
   now been measured across three separate runs. Run 9 measured a
   25-second deadline returning 0 bytes on the very first post-restore
   fetch of a channel, with the identical fetch passing at 40 seconds.
   **Run 10 is the run that raises the figure: a 40-second deadline
   itself returned 0 bytes on the first fetch, and the identical fetch
   passed at 60 seconds.** Run 11, on the published `0.18.1-0038` image,
   used a 60-second deadline throughout and passed (40 seconds was not
   retested there, so run 11 corroborates that 60 seconds works without
   independently proving 40 seconds insufficient. Run 10 already
   establishes that). Treat 60 seconds as the floor, not a guess: it's
   what has been observed to work on this drill's host and provider, not
   a guarantee for every environment.

   The first fetch after a restore opens a fresh upstream connection to
   the provider and is materially slower than steady state; a shorter
   deadline reads as a playback failure that isn't one. **A timeout hit
   before any bytes arrive is inconclusive on this cold fetch.** Retry
   once at a longer deadline before concluding playback is broken. Once
   you've confirmed a channel plays once, later checks in the same
   session can use a shorter deadline.

   Record status, content type, and byte count. A few hundred KB is
   enough to call it real media. A read-deadline hit *after* bytes have
   started arriving counts as a pass; a live stream never ends on its
   own.

   - **Encrypted artifact with "Include credentials":** expect this to
     pass immediately, on the first restore, no extra steps.
   - **Standard (redacted) artifact:** expect this to **fail**
     immediately after the restore. That is not a defect resurfacing;
     it's an M3U account with no credential yet. Work through
     [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)
     above. As of `0.18.1-0033`, playback should pass once you have
     re-entered the credential and the account's refresh has **completed**;
     on an earlier build it passes only after you also run the restore
     again. If it still fails after the full sequence for your build, that
     is worth filing as a fresh occurrence rather than assuming it away.
2. **Credentials.** Open the M3U account. On a standard (redacted)
   artifact, the password field is now correctly **empty**. The
   `***REDACTED***` sentinel bug is fixed and closed
   (`enhancedchannelmanager-6pilh`). Empty is the expected, honest state;
   it still needs the real credential re-entered before the account
   authenticates (see
   [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)).
   On an encrypted artifact with "Include credentials," confirm the
   credential round-tripped: a refresh should succeed immediately, no
   re-entry needed.
3. **M3U group selection.** Check whether the group(s) you enabled before
   the backup are still enabled after the restore. As of `0.18.1-0023`,
   expect this to be preserved (run 2 measured 1 of 375 groups correctly
   restored; on the prior pin, `0.18.1-0022`, this reverted to zero every
   time).
4. **Logos.** Open a channel that had a logo before the backup and
   confirm the logo actually renders. As of `0.18.1-0024`, this includes
   logos uploaded through ECM's own Logo Manager: run 2 measured 10 of 11
   logos sha256-identical to source, and a genuine logo miss no longer
   aborts the rest of the restore. See
   [ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill).
   On builds before `0.18.1-0032`, don't trust the dry-run preview's logo
   numbers either; see the preview warning in
   [Step 6](#step-6-restore) (`enhancedchannelmanager-dgnms`).
5. **EPG links.** Check whether channels that had an EPG link before the
   backup still have one. Run 9 measured this directly on `0.18.1-0035`:
   all 9 seeded links survived, on both artifact variants, with
   `epg_links_unrestored: 0` and an empty `epg_link_miss_details` in
   every restore. This article previously told you to expect every
   linked channel to lose its link; that guidance was wrong for this
   build and is corrected here (`enhancedchannelmanager-dfkbn`). If you
   ever see a genuine EPG-link loss, the restore report still names the
   affected channels in `epg_link_miss_details`; re-link by hand or
   re-run EPG auto-match. See
   [Match channels to EPG data](../epg/channel-to-epg-matching.md).
6. **Channel-profile membership.** If you seeded a profile with some
   channels deliberately excluded, check whether that exclusion survived.
   As of `0.18.1-0023`, expect it to be preserved (run 2 measured a
   non-default 9-of-12 membership restored exactly).
7. **Non-default settings.** Check any ECM setting you deliberately
   changed away from its default before the backup (timezone, poll
   interval, etc.). As of `0.18.1-0023`, expect these to be preserved
   (run 2 measured `stats_poll_interval` and `user_timezone` restored
   exactly).

Compare against your "before" inventory from Step 4 if you captured one.

---

## Step 8: Restore onto a populated target

Steps 1–7 above run entirely against a **freshly-wiped** target: every
`down -v` / `up -d` cycle destroys the previous instance before the
restore runs, so there is never an existing channel for either relink
mode to preserve or overwrite. A drill that stops at Step 7 never
exercises the populated-target path at all, and can't reach the
group-identity behaviour described in [Restoring onto a populated
target](#restoring-onto-a-populated-target) just below. This step is
that missing round.

It exists because `enhancedchannelmanager-r1ei7`, `-3t74w`, and `-tddmw`
were fixed in `0.18.1-0041`, and none of the three has yet been proven by
a live drill run against a populated target. Run 12, the run that found
them, measured the pre-fix behavior on `0.18.1-0040`. See [Known
failures](#known-failures) for the beads themselves.

### Why this round exists

Every restore before run 12 landed on a freshly-wiped target, where
`preserve` and the overwrite mode are indistinguishable (there is no
existing channel to preserve or overwrite), so a green result on that
kind of target meant nothing about either mode's actual behavior. This
round is the only one that exercises them, and the only one that can
reach the group-identity behaviour at all.

### Construction

Run this **as an additional round, after the fresh-target rounds in
Steps 1–7 are measured**. It does not replace them. Do not `down -v`
first: reuse the instance a completed restore left behind, then
deliberately diverge it from the archive before restoring onto it again.

1. **Rename** one archived channel group to a name the archive does not
   contain.
2. **Delete** a different archived channel group outright. Move its
   channels out of the group first. Dispatcharr refuses to delete a
   group that still has channels. Get the order right the first time:
   run 12 lost a full cycle to this.
3. **Create a name collision**: a channel group whose name matches an
   archived group but is a different object: a different id, holding
   different members.
4. **Diverge one channel's logo**, and **clear one channel's EPG link**,
   so `preserve` and the overwrite mode have something observable to
   differ on. Without this, both modes produce identical output and the
   comparison proves nothing.
5. Leave everything else alone, as a control.
6. **Record the exact before-state**: every group's id, name, and member
   count; every channel's group, logo, and EPG link. You cannot judge
   the after-state without this baseline.

### Run it twice, from an identical baseline

1. Restore the same artifact with the default relink mode (`preserve`).
   Measure against the checklist below.
2. **Reset to the same baseline**: delete the channel groups the restore
   in step 1 created, so the target is back to the diverged state you
   recorded in Construction, step 6.
3. Restore the same artifact again, this time with the overwrite mode.
   Measure again.

Both runs must start from the same diverged state, or the comparison
between them is meaningless.

### What to assert: by measurement, not from the report

Check each of the following directly; do not take the restore-complete
report's word for any of them.

- **`preserve`:** channel-group drift is reported and **non-zero**,
  naming each affected channel, the group it is currently in, and the
  group the archive says it belongs to, and **nothing moved**. Re-read
  each named channel's own group afterward to confirm.
- **overwrite mode:** the same channels are reported as drift, **and**
  are actually moved into the archive's groups. Re-read each channel's
  group to confirm the move happened, not just that it was reported.
- **The drift count appears on the dry-run preview**, before the apply.
  That is the number that tells an operator what overwrite is about to
  do to their lineup. It is useless after the fact.
- **The name-collision group reports `already_exists_name_match`**, not
  `already_exists_identical`. Channel profiles and stream profiles,
  matched by the same generic engine, must still report
  `already_exists_identical`. Only channel groups changed.
- **The preview carries its `channel_groups` caveat**, and a
  **`Streams`** row appears, flagged **not predicted** rather than being
  absent.
- **Playback still works** after each run: fetch a stream and confirm a
  2xx status and real media bytes, per [Step 7](#step-7-verify). Fetched,
  not inferred.
- **Deselect the `channel_groups` category once**, on any one restore in
  this round, and confirm the report says grouping was **not checked**
  rather than showing a drift count of `0`. A `0` and "not checked" are
  different claims, and only one of them is honest when the category
  never ran.

### Expect a noisy diff

The inventory diff after this round is noisy **by construction**: the
target was deliberately diverged before the restore ran, so most of the
differences are expected, not defects. Classify each
difference against the before-state you recorded in Construction, step
6, and against what the relink mode you ran promises: **expected**, if
it matches what that mode says it does, or **finding**, if it does not.
"The diff had findings" is not itself a defect in this round; an
**unreported** or **unexplainable** difference is.

See [Restoring onto a populated
target](#restoring-onto-a-populated-target) just below for what each
relink mode is documented to do, and [Known
failures](#known-failures) for the beads this round exists to prove.

---

## Restoring onto a populated target

Everything above this section, through [Step 7](#step-7-verify), was
measured on a **freshly-wiped** target: `down -v` destroyed the previous
state before the restore ran, so there was nothing on the target to
conflict with the archive. Every drill run before run 12 worked this
way, which means `preserve` and `overwrite` were indistinguishable in
practice (a fresh target has no existing channel for either relink mode
to preserve or overwrite), and the gaps below were structurally
unreachable until then. Run 12 was the first to restore onto a target
that already had its own, diverged state, and found two silent gaps: a
channel's group membership was never reconciled to the archive or
reported, and a same-named-but-different channel group was adopted while
the report claimed its contents had been compared. **Both are now fixed**
(`enhancedchannelmanager-r1ei7`, `enhancedchannelmanager-3t74w`). This
section describes the fixed behavior and what to check. [Step
8](#step-8-restore-onto-a-populated-target) above is the executable
procedure for actually running a populated-target round yourself; this
section is the reference, not the procedure.

!!! success "The relink modes DO differ on a populated target: this part works"
    Run 12 diverged an identical baseline two ways (a channel's logo
    changed, a channel's EPG link cleared) and ran both relink modes
    against separate copies of it. The mode labels themselves widened in
    the same fix that added the grouping column below
    (`enhancedchannelmanager-r1ei7`): **preserve** now reads *"Keep their
    current guide data, logos, and grouping"*, and **overwrite** now
    reads *"Replace their guide data, logos, and grouping with the
    backup's"*.

    | field | diverged baseline | after **preserve** | after **overwrite** | archive |
    |-|-|-|-|-|
    | channel logo | changed away from archive | **kept the diverged value** | **overwritten with the archive's value** | archive's logo |
    | channel EPG link | cleared | **left cleared** | **restored from the archive** | archive's link |
    | channel group | moved to a different group | **kept in the diverged group, and reported as drift** | **moved into the archive's group, and reported as moved** | archive's group |

    The logo and EPG-link rows are run 12's own measurement. The grouping
    row describes the fixed behavior below: implemented and covered by
    a test suite that reproduces run 12's exact scenario
    (`backend/tests/dbas/test_channel_group_drift.py`), but **not yet
    proven by a live drill run** against a populated target the way the
    other two rows were. `preserve` is the default, and it also emits an
    explicit "Channels you already had were left alone" panel naming
    exactly what it left untouched.

!!! info "Name-matching a channel group is deliberate: the fix was to stop overclaiming, not to stop adopting"
    **Bead `enhancedchannelmanager-3t74w`.** If the target has a channel
    group whose **name** matches an archived group, the restore still
    adopts the target's existing object and remaps the archive's
    references onto it. That part is unchanged, and it's intentional: a
    Dispatcharr channel group carries nothing but a name, name is the
    only identity a group has across two different instances, and
    treating a name match as a hard conflict would cascade into
    `DEPENDENCY_UNRESOLVED` for every channel that points at the group.

    What changed is the claim. Run 12 built a target group named `Drill
    Movies` that was a genuinely different object (different id, holding
    a different channel), watched the restore adopt it, and read back
    `already exists identical` and `success` / `failed 0`: a false
    assurance, since nothing about the group's contents was ever
    compared. As of the fix, a name-matched channel group reports the
    new skip reason **`already_exists_name_match`** ("matched by name,
    nothing else compared") instead. Channel profiles and stream
    profiles, matched by the same generic engine, are unaffected and
    still report `already_exists_identical`. Only channel groups
    changed, because only channel groups had nothing else to compare.
    The evidence that a name-matched group's contents actually diverge
    now arrives separately, via the channel-group drift reporting below.

!!! success "Channel-group drift is now reported, and reconciled under overwrite"
    **Bead `enhancedchannelmanager-r1ei7`.** A new pass runs after every
    channel is created or matched (it has to run after channels, since
    a group's membership lives on the CHANNEL's `channel_group_id`, not
    on the group row, and groups restore before channels) and compares
    each channel's destination group against the group the archive
    assigns it, by **name** (not id: the archive's own id is
    instance-local and can collide with an unrelated destination group).

    - **`preserve` (the default) reports only.** Every channel whose
      destination group differs from the archive's is recorded as
      **channel-group drift** (naming the channel, the group it's
      currently in, and the group the archive says it belongs in), and
      **nothing is moved**. The "never overwrite an existing channel"
      contract still holds.
    - **`overwrite` reports and moves.** The same divergence is
      recorded, and the channel is moved into the archive's group.
    - A channel **this restore created** is never counted as drift: it
      was created already carrying the archive's group, so flagging it
      would report a correction that never happened.
    - The drift count is **predicted on the dry-run preview**, from the
      same two inputs the apply itself uses, so an operator can see what
      `overwrite` is about to do to their lineup before committing to
      it.
    - **If you untick the `channel_groups` category, the check can't
      run**: nothing restores the archive's groups, so there's no
      destination group to compare against. Rather than report a `0`
      that reads like a clean result, the restore says *"Channel
      grouping was not checked"* outright, in the restore-complete panel
      and in the task-history line. A drift count of zero is only
      meaningful when that note is **absent**.

    This closes exactly the gap run 12 found: the restore used to report
    `outcome=success, failed 0` while silently leaving seven channels in
    the wrong group, with no counter, panel, or report line anywhere
    mentioning it.

**Operator guidance:** on a populated target, a same-named group being
adopted is expected: don't treat it as something to work around, it's
how a group's cross-instance identity is defined. What you should
actually check is the **channel-group drift count**: read it on the
dry-run preview before you choose a mode, and read it again in the
restore-complete report afterward. `preserve` leaves every drifted
channel exactly where it was and only tells you about it; `overwrite`
moves it. Stay on `preserve` if you don't want any channel's grouping
touched; pick `overwrite`, and expect the reported count of channels to
move, if you want your lineup's grouping to match the archive exactly.
Before you read a drift count of zero as good news, check that the
report doesn't also carry the *"Channel grouping was not checked"* note.
That note means you left the `channel_groups` category unticked and
nothing was compared.

---

## What a passing drill looks like

A drill "passes" only when every item below is true, and you checked each
one directly rather than trusting the restore-complete report's summary.
A complete drill now includes the [Step
8](#step-8-restore-onto-a-populated-target) populated-target round, not
just the fresh-target items below:

- Both backup artifacts completed at **success**, not warning-level.
- Both `.sha256` sidecars verified `OK` on host copies **before** the
  source instance was destroyed.
- On `0.18.1-0024` and later, ECM-uploaded logos on the source are no
  longer a precondition to check: they restore like any other category.
  See [ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill).
  On an older build, a drill that aborts on an ECM-uploaded logo did not
  fail because of something you did wrong; it reproduced a real,
  since-fixed defect.
- The restore report shows `FAILED: 0` across every category, **and** you
  independently confirmed:
  - At least one restored channel actually plays: fetch the stream and
    assert **2xx status and real media bytes**, bounded by a hard
    timeout, not merely "a URL is set." See the exact request shape in
    [Step 7](#step-7-verify).
  - The M3U account's credential actually authenticates (a refresh
    succeeds), not merely "the password field is non-empty."
  - The M3U account's enabled-group selection matches what you set
    before the backup.
  - Every logo that existed before the backup renders after the restore,
    not just the reported logo count.
  - Every channel that had an EPG link before the backup still has one
    after the restore.
  - Channel-profile membership matches exactly, including any
    deliberately excluded channels.
  - Every non-default setting you changed came back as you set it, not
    at its default.
  - **The Step 8 populated-target round completed and matched its
    checklist:** channel-group drift reported and non-zero under
    `preserve`, with nothing moved; the same channels reported as drift
    and actually moved under the overwrite mode; that drift count
    visible on the dry-run preview before either apply, not just after;
    the name-collision group reporting `already_exists_name_match`
    rather than `already_exists_identical`; and, with the
    `channel_groups` category deselected once, the report saying
    grouping was **not checked** rather than showing a drift count of
    `0`. See [Step 8](#step-8-restore-onto-a-populated-target) and
    [Restoring onto a populated
    target](#restoring-onto-a-populated-target).
- If you restored a **standard (redacted)** artifact, you completed the
  full [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)
  recovery sequence before checking playback: re-enter the credential and
  refresh the individual account, plus, on a build before `0.18.1-0033`,
  **run the restore again**. Playback failing before that sequence is
  complete is expected, not a regression.
- After that sequence, the synthetic `ECM Custom Streams (DBAS restore)`
  account and its placeholder streams are **gone**, not merely unused. As
  of `0.18.1-0032` a redacted round trip finishes with zero URL-less
  leftovers and the M3U account list back to what the source had. Leftover
  placeholders bound to no channel, or an empty synthetic account still
  listed, are a finding on that pin or later.

As of ECM `0.18.1-0040` / Dispatcharr `0.28.2`, a drill run **can** pass
this bar in full: an encrypted artifact with "Include credentials" plays
correctly on the first restore, ECM-uploaded logos and EPG links
included, and a genuine non-fatal logo failure (see
[ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill))
completes with the failure counted once and named, not rolled back. A
standard (redacted) artifact can also pass, but only after the full
Step 6a recovery sequence. **This bar is for a fresh target.** Restoring
onto an already-populated target adds one more thing to check:
channel-group drift, covered above. See [Restoring onto a populated
target](#restoring-onto-a-populated-target). See the next two sections
for the complete, current picture.

---

## Improved reporting

Worth knowing about outside a drill too: the restore-complete report and
the post-restore UI are now specific where they used to be silent or
outright wrong.

The report carries:

- `credentials_needing_reentry` with `credential_reentry_details` naming
  **the account** and **the exact field** (for example, an account named
  `Infinity` needing
  `profiles[0].custom_properties.user_info.password`, `password`).
- `channels_needing_stream_reattach` with `stream_reattach_details`
  naming every channel still holding a placeholder stream, and
  `channels_with_no_playable_stream` counting the ones that have no real
  stream left at all. Only the second group cannot play. As of
  `0.18.1-0029` this audit covers **every** restored channel, judged from
  what it is actually left holding, so a channel stranded by an *earlier*
  restore is named too. On the prior pin the audit only inspected
  placeholders the current run had created, and a repeat restore over an
  already-stranded channel reported `0` and `0` for a channel that
  answered HTTP 500 on playback. On a **dry run** both counters read
  `null`; see the preview warning in [Step 6](#step-6-restore).
- `epg_links_unrestored` with `epg_link_miss_details` per channel.
- `logo_misses` with `logo_miss_details` naming the affected channel. As
  of `0.18.1-0036`, a second report of a logo already recorded merges
  into that logo's row instead of appending a duplicate; see
  [ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill).
- `profile_membership_drift` listing which channels were enabled or
  disabled relative to the source.
- A note that a restored Dispatcharr user gets a random password ECM
  does not record, so it needs an out-of-band reset before use.

**The alert severity is keyed to the outcome, not to which category
degraded it.** Any restore that finishes `completed_with_failures`, for
any reason, raises a **warning** alert, never a plain success:
unplayable channels, a non-fatal category failure (currently only
logos), or both together. `error` / "Task Failed" is reserved for the
outcomes that actually failed or rolled back
(`partial_failed_rolled_back`, `failed_rollback_incomplete`) and for
orchestration errors. **Fixed as of `0.18.1-0036`
(`enhancedchannelmanager-cwmid`):** on `0.18.1-0035`, a restore degraded
*only* by a non-fatal logo miss incorrectly raised `error` / "Task
Failed: DBAS Restore", while a restore in which not one channel could
play correctly raised `warning`, the severity ordering was inverted for
triage. If you hit an `error` / "Task Failed" alert on `0.18.1-0036` or
later, that means the restore actually rolled back or ended in an
indeterminate state, not merely that some non-fatal category degraded.

The UI shows matching panels after a restore: a credentials panel (for
example "2 accounts need credentials re-entered before they will work"),
and, as of `0.18.1-0036` (`enhancedchannelmanager-d0bd3`), a
stream-reattach panel alongside it naming every channel with no playable
stream. On `0.18.1-0035` and earlier, the restore-complete dialog showed
only the credentials panel even when every restored channel was
unplayable; the report data was correct, only the panel was missing. Use
these panels instead of re-deriving the same information by hand.

---

## Known failures

Run 2 measured nine beads on ECM `0.18.1-0023` / Dispatcharr `0.28.2`.
Run 9 (`0.18.1-0035`) added three more, all now fixed as of `0.18.1-0036`.
Run 12 (`0.18.1-0040`) added three more, all now fixed as of
`0.18.1-0041`. Current tally, **fifteen beads total**: **eight fixed and
closed**, **one partially fixed with a named residual**, **three** that
landed fixed in the same build that found them (see [ECM-uploaded logos
and this drill](#ecm-uploaded-logos-and-this-drill)), and **three** more,
found by restoring onto a populated target and by comparing a preview
against its own apply, that landed fixed one build after the run that
found them (see [Restoring onto a populated
target](#restoring-onto-a-populated-target)). That's **fourteen of
fifteen fully fixed**, one with a named residual still open, and none
currently open outright. Check each bead's live status before you
assume any row is still true on the version you're running; this is a
snapshot, not a permanent guarantee.

### Fixed, closed

| Bead | What used to happen | Status |
|-|-|-|
| `enhancedchannelmanager-lvfwd` | The restore imported stream profiles before user agents and passed the archived user agent's raw source-side ID through unremapped, aborting the restore (or silently binding the wrong agent) if the target didn't already have a matching id. | **Fixed.** The restore now binds to the correctly **named** agent, verified even with decoy agents occupying the source's numeric id on the target. No pre-creation workaround needed. |
| `enhancedchannelmanager-y65si` | The restore aborted and rolled back the entire instance at the `user` category whenever the rebuilt Dispatcharr's superuser had a different username than the source's. | **Fixed.** Run 2 used deliberately **different** superuser names (`rebuiltadmin`, `secondadmin` vs. source `drilladmin`) and both restores completed. |
| `enhancedchannelmanager-6pilh` | A redacted (standard) restore wrote the literal string `***REDACTED***` into the M3U password field instead of leaving it empty, so the account presented as fully configured and then failed to authenticate. | **Fixed.** The field is now correctly empty. It still needs the real credential re-entered (see [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)), but that part was never the bug. |
| `enhancedchannelmanager-cytzj` | After one manual encrypted backup, the `DBAS Backup` task kept producing encrypted, credential-bearing artifacts on every later run, including unattended scheduled runs, until the ECM container restarted. | **Fixed.** The encryption transient is now genuinely one-shot. |
| `enhancedchannelmanager-dfkbn` | This article told you EPG links were "still gone" on every restore, on top of logos, channel-profile membership, and non-default settings all needing verification. | **Fixed, and the doc claim was wrong for this build.** Run 9 measured logos, channel-profile membership (9-of-12), non-default settings, **and EPG links** (9/9) all surviving a restore, on both artifact variants. See [Step 7](#step-7-verify). |
| `enhancedchannelmanager-k2r7m` | `logo_misses` reported **2** for a single genuinely-failed logo, doubling the operator-facing count of a non-fatal failure. | **Fixed as of `0.18.1-0036`.** A second report of a logo already recorded now merges into that logo's row instead of appending a duplicate. See [ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill). |
| `enhancedchannelmanager-cwmid` | A restore degraded *only* by a non-fatal logo miss raised `error` / "Task Failed: DBAS Restore", while a restore in which not one channel could play correctly raised `warning`, the severity ordering was inverted for triage. | **Fixed as of `0.18.1-0036`.** The alert severity is now keyed to the restore's outcome, not to which category degraded it; see [Improved reporting](#improved-reporting). |
| `enhancedchannelmanager-d0bd3` | The restore-complete UI showed only the credentials panel, even when every restored channel had no playable stream. | **Fixed as of `0.18.1-0036`.** A stream-reattach panel now renders alongside the credentials panel; see [Improved reporting](#improved-reporting). |

### Partially fixed: residual still open

| Bead | What's fixed | What's still broken |
|-|-|-|
| `enhancedchannelmanager-2o0cz` | Stream reattachment, stream ordering, M3U enabled-group selection, and playback all now work once the M3U account has a real credential. An encrypted artifact with "Include credentials" plays on the first restore attempt (verified: 2/2 real fetches, HTTP 200, `video/mp2t`). As of `0.18.1-0033`, a **redacted** artifact recovers in two steps: re-enter the credential, then refresh the account. A completed refresh reattaches the channels, and the leftover placeholders and their synthetic account are cleaned up in the same pass; run 9 confirmed this again and needed no third restore. | Two refresh routes are not covered and heal only on the next scheduled M3U refresh: a "refresh all accounts" action, and a refresh performed in Dispatcharr's own UI. Refresh the individual account for the immediate path. On builds before `0.18.1-0033` the recovery still needed a third step, **running the restore again**, because a refresh alone added real streams beside the placeholders without rebinding them. |

### New

| Bead | Severity | What happens |
|-|-|-|
| `enhancedchannelmanager-xb58a` | **P0, root cause** | The backup did not archive the bytes of logos uploaded through ECM's own Logo Manager, even though Dispatcharr serves them on request over its logo cache endpoint with the same API key ECM already holds. `binary/metadata.json` recorded zero logos for a source that had one. **Fixed as of `0.18.1-0024`:** the backup now fetches and archives those bytes at gather time; a round-trip restore reproduces the file byte-for-byte. See [ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill). |
| `enhancedchannelmanager-d0agi` | **P0, consequence** | Because the uploaded logo's bytes were never archived, the restore couldn't recreate it, recorded the miss, and (since a logo failure was classified as fatal) aborted and rolled back the **entire** restore, not just the logo category. **Fixed as of `0.18.1-0024`:** logos joined Dispatcharr users as a non-fatal restore category; a logo miss is now counted and named, and the rest of the restore completes. See [ECM-uploaded logos and this drill](#ecm-uploaded-logos-and-this-drill). |
| `enhancedchannelmanager-dgnms` | P1 | The dry-run preview reported every URL-restorable logo as failed (`validation_error: unsafe or empty logo filename`), even ones that restore fine on apply, and reported a confident `0` for counters it had not measured. **Fixed as of `0.18.1-0032`:** the logo split and `profile_membership_drift` are genuinely predicted and match the apply, and the two stream-health counters report `null` ("not predicted") instead of a misleading `0`. See the preview warning in [Step 6](#step-6-restore). |
| `enhancedchannelmanager-3t74w` | P2 | Found in run 12, restoring onto a **populated target**: a target channel group that shares a **name** with an archived group but is a different object (different id, different members) is silently adopted by name, and reported `already_exists_identical` as if its contents had been compared. **Fixed as of `0.18.1-0041`:** the adopt-by-name behavior is unchanged and intentional (name is the only cross-instance identity a group has), but the claim is now honest. A name-matched channel group reports the new skip reason `already_exists_name_match` instead. See [Restoring onto a populated target](#restoring-onto-a-populated-target). |
| `enhancedchannelmanager-r1ei7` | P2 | Found in run 12, restoring onto a **populated target**: channel→group membership was never reconciled to the archive or reported, in either relink mode. `profile_membership_drift` covers channel-*profile* membership only, with no channel-*group* equivalent. **Fixed as of `0.18.1-0041`:** a new post-channels pass reports every channel whose group differs from the archive's as channel-group drift under both modes, and `overwrite` additionally moves the channel into the archive's group. Not yet proven by a live drill run against a populated target. See [Restoring onto a populated target](#restoring-onto-a-populated-target). |
| `enhancedchannelmanager-tddmw` | P2 | Found in run 12: the dry-run preview diverged from the apply on `channel_groups` (`378 WILL CREATE / 0 WILL SKIP` vs. `3 CREATED / 375 SKIPPED`), and the `Streams` category (the one that synthesizes placeholder streams) was absent from the preview entirely, not just `0`. End state was always correct; this was a reporting gap. **Fixed as of `0.18.1-0041`:** the preview now carries an explanatory caveat on `channel_groups` naming the divergence's cause, and a `Streams` row now appears on the preview, flagged **not predicted** rather than omitted. See the preview warning in [Step 6](#step-6-restore). |

**No workarounds are required to get an ordinary restore to complete**,
on `0.18.1-0024` and later, including one with an ECM-uploaded logo. The
workarounds this article used to document (matching superuser name,
pre-created user agent, removing ECM-uploaded logo records before backup)
are all gone: the underlying defects are fixed, and (see
[Step 6](#step-6-restore)) re-applying them now would actively work
against you, hiding whether the fixes still hold.

**What works, confirmed by run 2 and reconfirmed by run 9:** object
identity and naming, channel/group/profile assignment, stream ordering
on multi-stream channels, the custom stream profile → user agent
binding, M3U enabled-group selection, non-default settings,
channel-profile membership, EPG links, and (the headline result)
**actual playback**, verified by fetching real media bytes rather than
checking that a URL is set. The restore is no longer just a good
skeleton: on an encrypted artifact with "Include credentials," it
reproduces a working instance, ECM-uploaded logos included as of
`0.18.1-0024`, with a genuine non-fatal logo failure counted correctly
and named, not rolled back, as of `0.18.1-0036`.

---

## What this drill does not cover

Stated plainly rather than left as an implied "everything else works".
**Restoring onto a populated target is not on this list**. That path is
covered by [Step 8](#step-8-restore-onto-a-populated-target) above, not
omitted. What follows is genuinely out of scope:

- **Cloud destinations** (S3, WebDAV, Google Drive): the off-host upload
  and retention-pruning leg of
  [Configure cloud destinations](configure-cloud-destinations.md) is not
  exercised by this procedure. "The backup verified" in this drill means
  only that a verified copy sits on the same host as the instance it came
  from. That is not a disaster-recovery position by itself.
- **Cross-instance sync**: not exercised; see
  [Cross-Instance Sync](cross-instance-sync.md) separately.
- **Plugin state**: not known to be included in the DBAS artifact at all.
- **Dispatcharr's own database beyond what ECM archives**: anything
  Dispatcharr-side that ECM's backup categories don't cover is gone once
  you destroy the instance.
- **VOD, series, and DVR rules**: only exercised if you seed them
  yourself; the reference run had none.
- **Scale**: a drill run with a dozen channels and a few hundred streams
  says nothing about restore behavior at a few thousand channels,
  particularly for the per-category rollback path.
- **Multi-provider instances**: this procedure seeds one M3U account.
  Whether the placeholder-stream defect (`2o0cz`) behaves differently
  with several providers configured is unknown.

---

## Teardown

When you're done:

```bash
docker compose -p ecmbkr -f docker-compose.backup-restore-drill.yml down -v
docker volume ls | grep ecmbkr   # expect no output
```

Revoke any Dispatcharr API key you minted for the drill (**System → Users
→ edit → API & XC tab → Revoke API Key**). Delete the local `artifact/`
directory once you no longer need the evidence, since it contains
credential-bearing artifacts if you took the encrypted variant with
**Include credentials** enabled.

---

## Related reading

- [Migrate to a new install](migrate-to-a-new-install.md): the operator
  walkthrough this drill validates, including the redacted-artifact
  recovery sequence documented inline at the step where it applies.
- [Restore a backup](restore-a-backup.md): category ordering, stream
  matching tiers, and rollback mechanics.
- [Troubleshoot a restore](troubleshoot-restore.md): failure-message
  reference.
- [Disaster Recovery runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/disaster-recovery-restore.md):
  the SRE-facing incident procedure; its "Recurring maintenance" section
  points here for proving the apply path specifically.
