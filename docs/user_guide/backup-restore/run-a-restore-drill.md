# Run a Restore Drill

> **Status:** Operational procedure. Not a shipped feature; there is no "drill mode" button in ECM. This article is the whole procedure, written to be followed without repository access.

---

!!! danger "Read this before you start"
    This procedure was run against **Dispatcharr `0.28.2`** and **ECM
    `0.18.1-0023`**. A restore result is only a result for the version it
    was measured on. If you are on different versions, treat every claim
    below as "true as of this pin," re-run the drill, and update your own
    notes.

    The most recent drill (run 2, `0.18.1-0023`) found real progress: a
    restored lineup now genuinely **plays**, proven by fetching real media
    bytes, not just checking that a URL is set. But there is also a new
    **P0 blocker**: the backup does not archive the bytes of logos
    uploaded through ECM's own Logo Manager
    (`enhancedchannelmanager-xb58a`), so if the source instance has any
    such logo, the restore aborts and rolls back the entire run
    (`enhancedchannelmanager-d0agi`). Read
    [Before you seed: the logo blocker](#before-you-seed-the-logo-blocker)
    below before you get anywhere near Step 2, and
    [Known failures](#known-failures) before you conclude your own drill
    passed.

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
Confirm **Test Connection** reports **Connected**, then **Save**.

---

## Before you seed: the logo blocker

!!! danger "P0: the backup does not archive uploaded logo bytes, and the resulting miss aborts the restore"
    As of `0.18.1-0023`, if the source instance has **any** logo uploaded
    through ECM's own Logo Manager, restoring that backup **aborts and
    rolls back the entire run**, not just the logo category
    (`enhancedchannelmanager-d0agi`).

    **Why:** a logo uploaded through ECM's Logo Manager is written to
    **Dispatcharr's** `/data/logos/`, not ECM's own
    `/config/uploads/logos/` (ECM's own upload directory stays empty,
    verified). Those bytes are fully retrievable at backup time.
    Dispatcharr serves them over its own logo cache endpoint (each logo
    row carries a `cache_url`), reachable with the same API key ECM
    already holds and already uses for every other backup category. The
    drill proved this directly: it fetched an uploaded logo's bytes over
    HTTP and got a byte-identical copy of the original file (matching
    SHA-256). **The backup simply never asks for them**:
    `binary/metadata.json` records `{"logo_count": 0, "logos": []}` even
    when an uploaded logo exists on the source. This is a backup-side
    gap, not something inherent to the storage location, tracked as
    `enhancedchannelmanager-xb58a` (P0). The bytes only become genuinely
    unrecoverable once the source Dispatcharr volume is destroyed, which
    is exactly the event a backup exists to survive.

    Because the backup never captured the bytes, the restore correctly
    detects the miss (`logo_misses: 1`). A logo failure is currently
    classified as **fatal**, so the whole restore rolls back:
    `outcome: partial_failed_rolled_back`. Everything that had already
    succeeded, including channels, streams, both accounts, the profile,
    the user, and the other logos, is deleted again by the compensating
    rollback (`enhancedchannelmanager-d0agi`). Settings changes are the
    one exception; they are not compensatable and remain applied even
    after this rollback.

    **Logos referenced by a remote http(s) URL are unaffected** and
    round-trip correctly: a drill run measured 10 of 10 restored
    byte-identical. This gap is specific to logos uploaded through ECM's
    own Logo Manager.

    **The only way to get a restore to complete today is to remove
    ECM-uploaded logo records before taking the backup you intend to
    restore.** That has a real cost: those logos are then not in the
    backup at all, and must be re-uploaded by hand after the restore.
    Neither `enhancedchannelmanager-xb58a` (archive the bytes at backup
    time) nor `enhancedchannelmanager-d0agi` (stop treating a logo miss as
    fatal) is shipped. Do not treat this warning as describing a problem
    someone has already fixed; re-check both beads' status before you
    rely on this section being current.

    **For this drill:** to reach a restore that can complete on this pin,
    do not upload a logo through ECM's own Logo Manager in Step 2 below.
    Stick to remote-CDN logos auto-assigned from the M3U feed (the common
    case anyway). If you specifically want to *exercise* this defect,
    upload one deliberately and expect the restore to abort; that is the
    correct (bad) result, not a mistake on your part.

---

## Step 2: Seed a small instance

**Use exactly one small M3U group.** This is the single most important
scheduling decision in the whole drill. A whole-catalogue M3U refresh can
take on the order of 25 minutes with no progress indication. Restricting
sync to one small group brings convergence down dramatically: run 2
measured **~1.4 seconds** for a single group of 110 streams.

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
sufficient; refresh it and confirm entries populate.

**Create a handful of channels through ECM**, in Edit Mode: select
streams from the group you enabled, use "Create in…" to place them into
one or two channel groups, then **Done → Apply All**. Nothing reaches
Dispatcharr until you click **Apply All**. A staged change in Edit Mode
looks finished but is not yet applied.

**To exercise the restore path more thoroughly** (recommended if you have
the time; each of these maps to a specific restore behavior worth
proving), also set up:

| Feature | Why it's worth seeding |
|-|-|
| A multi-stream channel with a deliberate stream order | Proves whether stream ordering survives the restore's 4-tier matcher |
| A channel profile with non-default membership (some channels excluded) | Proves whether profile scoping survives, or silently widens to "all channels" |
| A custom stream profile assigned to a channel | Proves the user-agent binding fix (`enhancedchannelmanager-lvfwd`, closed) still holds |
| A custom user agent assigned to that stream profile | Same as above; the two are usually configured together |
| A channel with its logo deliberately cleared | Gives you a known-absent case to contrast against the known-present ones |
| At least one non-default ECM setting (timezone, poll interval, etc.) | Proves whether settings actually restore or silently revert to defaults |
| EPG links on most but not all channels | Gives you a mix to check post-restore rather than an all-or-nothing signal |

If any seeded logo is a **remote CDN URL** auto-assigned from the M3U
feed (the common case), the backup's binary logo subtree will be empty
and most of the logo-restore path goes untested.

!!! danger "Uploading a logo here changes what this drill measures"
    See [Before you seed: the logo blocker](#before-you-seed-the-logo-blocker)
    above. On this version pin, uploading a logo through ECM's own Logo
    Manager will make your restore abort and roll back
    (`enhancedchannelmanager-d0agi`). Only do this if you are
    deliberately exercising that defect and expect the restore not to
    complete.

---

## Step 3: Take both backup artifacts

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

Confirm both completed at **success**, not warning-level, in
**Settings → Backup & Restore → Saved Backups**. A warning-level backup
means the source artifact was already degraded before you ever get to the
restore side, and any drift you find afterward can't be cleanly attributed
to the restore path.

---

## Step 4: Evacuate and verify, before you destroy anything

This is the gate. Do not proceed past it until both checks below pass.

1. Copy both `.zip` artifacts and their `.sha256` sidecars out of the
   container, onto the drill host's filesystem (not a volume that will be
   wiped):

   ```bash
   docker cp bkr-ecm:/config/backups/<standard-file>.zip ./artifact/
   docker cp bkr-ecm:/config/backups/<standard-file>.zip.sha256 ./artifact/
   docker cp bkr-ecm:/config/backups/<encrypted-file>.zip ./artifact/
   docker cp bkr-ecm:/config/backups/<encrypted-file>.zip.sha256 ./artifact/
   ```

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

Note: an **encrypted** artifact is not a readable zip (attempting to
open it as one fails with `BadZipFile`), so you cannot peek inside it
before restoring. Only the plaintext (standard) artifact can be inspected
this way. This does not affect the `sha256sum -c` check above, which
verifies the file's bytes regardless of encryption.

Optional but recommended: capture a "before" inventory (channel count,
group count, M3U/EPG source counts, ECM settings you changed, logo count
and hashes) so you have something concrete to diff against after the
restore, rather than relying on the restore report's own counts, which
[measured wrong in the reference run](#known-failures)
(`dfkbn`).

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

   !!! warning "The preview lies about logos"
       The dry-run preview reports **every** URL-restorable logo as
       `validation_error: unsafe or empty logo filename`, even ones that
       restore fine on apply. Run 2 measured this directly: the preview
       flagged all 11 seeded logos as failures; the apply then restored
       10 of them correctly and failed only the one genuinely-lost logo
       (`enhancedchannelmanager-dgnms`). Cause: the preview never
       simulates the URL re-create path, so every URL-only logo falls
       through to a byte-validation path that expects a `filename` key
       the preview's records don't have. **Preview first, always**, but
       do not abort a restore because of logo failures shown in a
       preview; the real miss is easy to miss among the invented ones.
       Every other category's preview numbers are unaffected.

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

This is the single most valuable, most counter-intuitive fact from run 2.
Skip it and playback will look broken when it's actually just unfinished.

A standard (redacted) artifact has no M3U password, so nothing was ever
ingested from the real provider. Right after the restore, playback is
*expected* to fail, not because of a defect, but because the M3U account
has no credential yet. Run 2 measured the recovery in three states:

| State | Streams | Channel bindings | Playback (bytes actually fetched) |
|-|-|-|-|
| 1. Straight after the restore | 14, **0 with a URL** | 12 channels on placeholders | **0/2 (HTTP 500)** |
| 2. After re-entering the credential **and** refreshing the M3U account | 124 (110 real) | **still** 12 on placeholders | still fails |
| 3. After **running the same restore again** | 124 | all 12 on real streams, correct order | **2/2 (HTTP 200, `video/mp2t`, real bytes)** |

**State 2 is not enough, and this is the part operators will not guess.**
ECM's placeholder-rebind pass is a one-shot step that runs immediately
after the restore's own deferred M3U refresh. On a redacted artifact
there is nothing to match against at that instant: no credential yet,
and the rebind pass never re-runs on its own. A later manual refresh adds
the real streams *beside* the placeholders and rebinds nothing.

**The correct recovery sequence:**

1. Re-enter the provider credential on every M3U account the restore
   report or the post-restore UI names as needing it. Both now name the
   exact account and field; see
   [Improved reporting](#improved-reporting) below.
2. Refresh the M3U account.
3. **Run the same restore again**, from the same artifact. This is the
   step most operators will not think to do, and skipping it is why
   playback still looks broken after step 2.

**Alternative: skip this whole sequence.** An **encrypted artifact with
"Include credentials" enabled** restores the credential automatically.
Verified in run 2 with an identical credential fingerprint before and
after, and playback working on the first restore, no recovery pass
needed. If you don't specifically need the redacted variant's
smaller/shareable footprint, prefer the encrypted-with-credentials path
for any artifact you actually intend to restore onto a working instance.

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

   Record status, content type, and byte count. A few hundred KB is
   enough to call it real media. A read-deadline hit *after* bytes have
   started arriving counts as a pass; a live stream never ends on its
   own.

   - **Encrypted artifact with "Include credentials":** expect this to
     pass immediately, on the first restore, no extra steps.
   - **Standard (redacted) artifact:** expect this to **fail**
     immediately after the restore. That is not the `2o0cz` defect
     resurfacing; it's an M3U account with no credential yet. Work
     through [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)
     above; playback should pass once you've re-entered the credential,
     refreshed, and **run the restore again**. If it still fails after
     that full sequence, that *is* the residual
     `enhancedchannelmanager-2o0cz` defect and is worth filing as a fresh
     occurrence rather than assuming it away.
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
   confirm the logo actually renders. As of `0.18.1-0023`, expect logo
   bytes to restore correctly when the restore completes at all: run 2
   measured 10 of 11 logos sha256-identical to source. **But the restore
   may not complete at all**: see
   [Before you seed: the logo blocker](#before-you-seed-the-logo-blocker);
   any ECM-uploaded logo currently aborts the whole restore before logos
   are even reached (`enhancedchannelmanager-d0agi`). Don't trust the
   dry-run preview's logo numbers either; see the preview warning in
   [Step 6](#step-6-restore) (`enhancedchannelmanager-dgnms`).
5. **EPG links.** Check whether channels that had an EPG link before the
   backup still have one. As of `0.18.1-0023`, expect them to still be
   **gone**. This is a residual, still-open defect, not fixed
   (`enhancedchannelmanager-dfkbn`). The restore relinks by the channel's
   archived `tvg_id`, but ECM's own channel rows carry `epg_data_id` with
   `tvg_id: None` (confirmed this is not a seeding quirk: setting
   `epg_data_id` through ECM's own API also leaves `tvg_id` null), so
   there is nothing to match on. The restore report now names exactly
   which channels lost their link (`epg_link_miss_details`); re-link
   those channels by hand or re-run EPG auto-match. See
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

## What a passing drill looks like

A drill "passes" only when every item below is true, and you checked each
one directly rather than trusting the restore-complete report's summary:

- Both backup artifacts completed at **success**, not warning-level.
- Both `.sha256` sidecars verified `OK` on host copies **before** the
  source instance was destroyed.
- Your source instance had **no ECM-uploaded logos**, or you have
  consciously accepted that the restore will abort. See
  [Before you seed: the logo blocker](#before-you-seed-the-logo-blocker).
  A drill that hits `enhancedchannelmanager-d0agi` did not fail because of
  something you did wrong; it reproduced a real, currently-unfixed defect.
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
  - Channel-profile membership matches exactly, including any
    deliberately excluded channels.
  - Every non-default setting you changed came back as you set it, not
    at its default.
- If you restored a **standard (redacted)** artifact, you completed the
  full [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)
  recovery sequence (re-enter credential, refresh, **run the restore
  again**) before checking playback. Playback failing before that
  sequence is complete is expected, not a regression.

**What still does not pass, even on an otherwise clean run, as of
`0.18.1-0023`:** EPG links (`enhancedchannelmanager-dfkbn` residual;
expect every linked channel to lose its guide link and need manual
re-linking; see [Step 7](#step-7-verify)). Note this and move on; it's a
tracked, open defect, not a surprise finding.

As of ECM `0.18.1-0023` / Dispatcharr `0.28.2`, a drill run **can** pass
this bar: an encrypted artifact with "Include credentials," from a source
with no ECM-uploaded logos, accepting the EPG-link gap as a known
residual, plays correctly on the first restore. A standard (redacted)
artifact can also pass, but only after the full Step 6a recovery
sequence. See the next two sections for the complete, current defect
picture.

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
  naming every channel still bound to a placeholder, plus a note that
  they will not play until reattached.
- `epg_links_unrestored` with `epg_link_miss_details` per channel.
- `logo_misses` with `logo_miss_details` naming the affected channel.
- `profile_membership_drift` listing which channels were enabled or
  disabled relative to the source.
- A note that a restored Dispatcharr user gets a random password ECM
  does not record, so it needs an out-of-band reset before use.

The UI shows matching panels after a restore, for example "1 logo is
missing after this restore" with a "Fix in Dispatcharr" link, and "2
accounts need credentials re-entered before they will work." Use these
instead of re-deriving the same information by hand.

---

## Known failures

As of ECM `0.18.1-0023` / Dispatcharr `0.28.2` (see the version pin at
the top of this article). Nine beads total: **four fixed and closed**,
**two partially fixed with a named residual**, and **three new**. Check
each bead's live status before you assume any row below is still true on
the version you're running; this describes what run 2 measured, not a
permanent guarantee.

### Fixed, closed

| Bead | What used to happen | Status |
|-|-|-|
| `enhancedchannelmanager-lvfwd` | The restore imported stream profiles before user agents and passed the archived user agent's raw source-side ID through unremapped, aborting the restore (or silently binding the wrong agent) if the target didn't already have a matching id. | **Fixed.** The restore now binds to the correctly **named** agent, verified even with decoy agents occupying the source's numeric id on the target. No pre-creation workaround needed. |
| `enhancedchannelmanager-y65si` | The restore aborted and rolled back the entire instance at the `user` category whenever the rebuilt Dispatcharr's superuser had a different username than the source's. | **Fixed.** Run 2 used deliberately **different** superuser names (`rebuiltadmin`, `secondadmin` vs. source `drilladmin`) and both restores completed. |
| `enhancedchannelmanager-6pilh` | A redacted (standard) restore wrote the literal string `***REDACTED***` into the M3U password field instead of leaving it empty, so the account presented as fully configured and then failed to authenticate. | **Fixed.** The field is now correctly empty. It still needs the real credential re-entered (see [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)), but that part was never the bug. |
| `enhancedchannelmanager-cytzj` | After one manual encrypted backup, the `DBAS Backup` task kept producing encrypted, credential-bearing artifacts on every later run, including unattended scheduled runs, until the ECM container restarted. | **Fixed.** The encryption transient is now genuinely one-shot. |

### Partially fixed: residual still open

| Bead | What's fixed | What's still broken |
|-|-|-|
| `enhancedchannelmanager-2o0cz` | Stream reattachment, stream ordering, M3U enabled-group selection, and playback all now work once the M3U account has a real credential. An encrypted artifact with "Include credentials" plays on the first restore attempt (verified: 2/2 real fetches, HTTP 200, `video/mp2t`). | A **redacted** artifact still requires the recovery sequence in [Step 6a](#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback): re-enter credential, refresh, then **run the restore again**. A refresh alone is not enough; it adds real streams beside the placeholders without rebinding them. |
| `enhancedchannelmanager-dfkbn` | Logos (10/11 sha256-identical), channel-profile membership (9-of-12 preserved), and non-default settings (`stats_poll_interval`, `user_timezone`) all now restore correctly, and the report is honest about it. | **EPG links are still lost** on every linked channel, on both artifact variants. Root cause identified: the restore relinks by archived `tvg_id`, but ECM's own channel rows carry `epg_data_id` with `tvg_id: None`. See [Step 7](#step-7-verify). |

### New

| Bead | Severity | What happens |
|-|-|-|
| `enhancedchannelmanager-xb58a` | **P0, root cause** | The backup does not archive the bytes of logos uploaded through ECM's own Logo Manager, even though Dispatcharr serves them on request over its logo cache endpoint with the same API key ECM already holds. `binary/metadata.json` records zero logos for a source that has one. See [Before you seed: the logo blocker](#before-you-seed-the-logo-blocker). |
| `enhancedchannelmanager-d0agi` | **P0, consequence** | Because the uploaded logo's bytes were never archived, the restore can't recreate it, records the miss, and (since a logo failure is currently fatal) aborts and rolls back the **entire** restore, not just the logo category. See [Before you seed: the logo blocker](#before-you-seed-the-logo-blocker). |
| `enhancedchannelmanager-dgnms` | P1 | The dry-run preview reports every URL-restorable logo as failed (`validation_error: unsafe or empty logo filename`), even ones that restore fine on apply. Logo-category preview numbers specifically are not trustworthy; every other category's preview is unaffected. |

**No workarounds are required to get an ordinary restore to complete on
this pin**, unless your source has an ECM-uploaded logo. In that case,
completing the restore requires removing that logo's record before
taking the backup (see above); the logo itself is then not preserved and
must be re-uploaded by hand afterward. The two workarounds this article
used to document (matching superuser name, pre-created user agent) are
gone: both underlying defects are fixed, and (see [Step 6](#step-6-restore))
re-applying them now would actively work against you, hiding whether the
fixes still hold.

**What works, confirmed by run 2:** object identity and naming,
channel/group/profile assignment, stream ordering on multi-stream
channels, the custom stream profile → user agent binding, M3U
enabled-group selection, non-default settings, channel-profile
membership, and (the headline result) **actual playback**, verified by
fetching real media bytes rather than checking that a URL is set. The
restore is no longer just a good skeleton: on an encrypted artifact with
"Include credentials" and no ECM-uploaded logos, it reproduces a working
instance.

---

## What this drill does not cover

Stated plainly rather than left as an implied "everything else works":

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
