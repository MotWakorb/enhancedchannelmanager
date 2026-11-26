# Recover Data from a Container with No Config Mount

Use this page when ECM's startup log says `DATA IS NOT PERSISTENT: /config is not a mounted volume`, **and the container already holds data you want to keep**. The goal is to copy the whole `/config` directory out of that container, give ECM a durable `/config` mount that starts from that copy, recreate the container, and verify.

This is the recovery sequence. Setting up the mount in the first place, before there is anything to lose, is covered in [Install ECM](../getting-started/installation.md#give-ecm-a-place-to-keep-its-data) and [First run](../getting-started/first-run.md#what-the-preflight-checks-tell-you).

## Why this page copies the whole directory

ECM keeps its own state in `/config`, not only the data a backup artifact carries. The in-app backups are not a full copy of it:

- **The Restore DBAS Backup flow restores Dispatcharr-side data** (M3U accounts, EPG sources, channel groups and profiles, channels, stream profiles, Dispatcharr users, user agents, DVR rules, server groups, upcoming recordings). It decodes only a fixed list of sections and ignores every other section in the artifact. It never reads the artifact's `journal.db`.
- **The Restore Full Backup flow does not accept that artifact.** It requires a legacy `ecm_backup.json` manifest that the current artifacts do not contain.
- **Dispatcharr's own data is not in the ECM container**, so recreating ECM does not touch it. What you stand to lose is ECM's side.

Recreating the container after only a DBAS backup would silently drop everything in this table, and a DBAS restore does not bring any of it back:

| Lost without the whole-directory copy | Where it lives in `/config` |
|-|-|
| ECM login accounts and sessions | `journal.db` |
| Alert methods, scheduled tasks and their schedules | `journal.db` |
| Auto-creation rules, normalization rules and mappings, tag groups | `journal.db` |
| FFmpeg and dummy-EPG profiles, hidden groups, journal history | `journal.db` |
| Authentication settings | `auth_settings.json` |
| TLS settings, certificate and private key | `tls_settings.json`, `tls/` |
| The key that decrypts stored cloud-storage and sync-target credentials | `.export_key` |
| The MCP API key and service credentials | `api-key`, `mcp-service.json`, `mcp_oauth.db` |
| Probe history | `probe_history.json` |
| ECM settings and uploaded playlist files | `settings.json`, `m3u_uploads/` |

So the primary path below copies everything. A DBAS configuration backup is an optional extra copy (Step 2), not the restore source.

!!! danger "Do not remove or recreate the container until the copy is verified (Step 5)"
    Everything ECM stores is in the container's own writable layer. **Removing** the container (`docker rm`, `docker compose down`, an "update" in Portainer, Unraid or Watchtower, `docker compose up --force-recreate`) destroys it, and nothing can bring it back. **Stopping** or **restarting** the container keeps it. Step 7 is the only step that cannot be undone, and it comes strictly after the verified copy. The commands use the container name `ecm-ecm-1`. Use your own name if it differs.

## What you need

- Shell access to the Docker host, and enough free disk space for a second copy of `/config`. Check its size first: `docker exec ecm-ecm-1 du -sh /config` (if the container is running).
- A place outside the container to keep the copy: a directory on the host. The examples use `./ecm-config`, which also becomes the new mount. Treat it as secret: it holds password hashes, the TLS private key and the encryption key for stored credentials.

## Step 1: Confirm the container has no `/config` mount

```bash
docker inspect ecm-ecm-1 --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}'
```

If `/config` is **not** in the output, the directory is unmounted and you are in the right place. You can also search the startup log for the warning:

```bash
docker logs ecm-ecm-1 2>&1 | grep "DATA IS NOT PERSISTENT"
```

If `/config` **is** listed, your data is already on a volume. Stop here, and back it up as you normally would before any change.

Also note the exact image tag the container runs, and use the same tag when you recreate it:

```bash
docker inspect ecm-ecm-1 --format '{{.Config.Image}}'
```

## Step 2 (optional): Take a DBAS configuration backup, if ECM is running

Skip this if ECM is not running. If it is, take a backup in **Settings → Backup & Restore** (see [Take a backup](take-a-backup.md#option-a-manual-backup-on-demand)) and wait for the completion notification. The artifact is written to `/config/backups/`, so the whole-directory copy in Step 4 carries it along.

This is an **additional** copy of the Dispatcharr-side configuration, useful for a later selective restore. It is not a substitute for Step 4: see [Why this page copies the whole directory](#why-this-page-copies-the-whole-directory).

## Step 3: Stop the container

```bash
docker stop ecm-ecm-1
```

Stopping lets ECM close its SQLite databases cleanly, so `journal.db` and its `-wal` file are consistent when you copy them. A stopped container keeps its data, and `docker start ecm-ecm-1` brings it back. If the container is already stopped or exits at startup, this command does nothing and you continue with Step 4.

## Step 4: Copy the whole `/config` directory out

`docker cp` works on a stopped container.

```bash
mkdir -p ./ecm-config
docker cp ecm-ecm-1:/config/. ./ecm-config/
```

The files keep their permissions, such as the owner-only `0600` on key files, and belong to the user who ran `docker cp`. ECM changes the ownership to your `PUID` and `PGID` the next time it starts.

## Step 5: Verify the copy before you destroy anything

1. List the copy and check that the files you expect are present:

   ```bash
   ls -la ./ecm-config
   ls -la ./ecm-config/tls ./ecm-config/backups
   du -sh ./ecm-config
   ```

   Expected, as applicable to your instance:

   - `journal.db`, non-zero in size. A `journal.db-wal` and `journal.db-shm` may also be present.
   - `settings.json`
   - `auth_settings.json`
   - `tls_settings.json` and a `tls/` directory, if you use TLS
   - `.export_key`, `api-key`, `mcp-service.json` and `mcp_oauth.db`
   - `probe_history.json`
   - `m3u_uploads/`, if you uploaded playlist files
   - `backups/`, with any backup artifacts (including the one from Step 2)
   - `uploads/` (logos)

   Dotfiles do not show in a plain `ls`, which is why the command uses `-a`. An empty `journal.db` or a missing `settings.json` means the copy failed. Repeat Step 4.

2. Check that the database is readable and holds your accounts. This works on a scratch duplicate, so it does not touch the copy:

   ```bash
   tmp=$(mktemp -d)
   cp ./ecm-config/journal.db* "$tmp"/
   python3 -c "
   import sqlite3, sys
   c = sqlite3.connect(sys.argv[1])
   print(c.execute('PRAGMA integrity_check').fetchone()[0])
   print(c.execute('SELECT count(*) FROM users').fetchone()[0])
   " "$tmp/journal.db"
   rm -rf "$tmp"
   ```

   Expected: `ok`, then the number of ECM accounts you have (at least `1` if you had signed in before). Use any Python 3 on the host.

3. Make a second copy of the verified directory, and keep it somewhere that survives the host if the host itself is at risk:

   ```bash
   cp -a ./ecm-config ./ecm-config-rescue
   ```

   ECM will change the ownership of the first copy at startup, so this one stays untouched as your fallback.

Do not continue until both checks pass.

## Step 6: Add a durable `/config` mount

Point `/config` at the verified copy. Edit your compose file so the mount is a bind mount of `./ecm-config` (or a durable path you choose, after moving the copy there):

```yaml
services:
  ecm:
    image: <the same image tag from Step 1>
    volumes:
      - ./ecm-config:/config      # bind mount: the directory you just copied into
```

For `docker run`, add `-v "$(pwd)/ecm-config:/config"` to your original command, keeping the same image tag.

The mount path inside the container must be `/config`. ECM's startup script prepares and checks that exact path, so mounting somewhere else and changing `CONFIG_DIR` is not a supported shortcut. See [Startup and interpreter variables](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/README.md#startup-and-interpreter-variables-advanced) for what `CONFIG_DIR` does and does not control.

You do not need to fix ownership yourself. At startup ECM changes ownership of everything in `/config` to the `PUID` and `PGID` you run ECM with (`chown -R` in `backend/entrypoint.sh`, line 385). Set `PUID` and `PGID` in your environment if your host user is not the default `1000`.

!!! danger "Step 7 destroys the old container's data"
    Removing the container is the one irreversible step on this page. Do it only once Step 5 passed and the mount from this step is in your compose file or run command.

## Step 7: Recreate the container (irreversible)

```bash
docker compose up -d
```

Compose recreates the service because its definition changed, which removes the old container. With plain `docker run`, run `docker rm ecm-ecm-1`, then start it again with your original options plus the `-v` mount from Step 6. Use the same image tag. Do not pull a newer image or run `docker compose pull` as part of this step: bring the instance back as it was first, and update afterwards.

Confirm the mount took effect:

```bash
docker logs ecm-ecm-1 2>&1 | grep "mounted volume"
```

Expected: `Config directory /config is on a mounted volume (data persists across recreates)`. If you see `DATA IS NOT PERSISTENT` again, the mount is not in place. Fix the compose file and repeat this step.

If the container exits instead of starting, see the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/container-preflight-failure.md). Your data is safe in `./ecm-config`: the runbook's fixes now apply with the mount in place.

## Step 8: Verify the recovery

1. Confirm the mount is in place and ECM is ready:

   ```bash
   docker inspect ecm-ecm-1 --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}'
   docker exec ecm-ecm-1 python3 -c "
   import json, os, urllib.error, urllib.request
   port = os.environ.get('ECM_PORT', '6100')
   try:
       response = urllib.request.urlopen(f'http://localhost:{port}/api/health/ready', timeout=5)
   except urllib.error.HTTPError as error:
       response = error
   print(response.status)
   print(json.dumps(json.load(response), indent=2))
   "
   ```

   Expected: `/config` in the mount list, and `200` with `"status": "ready"`. The image ships no `curl`, which is why this uses `python3`. The readiness endpoint needs no credentials.

2. Open ECM and sign in with the **same accounts** you used before. You should not be sent to first-run setup. If you are, the copy did not carry `journal.db`: stop, and see [If something is missing](#if-something-is-missing).
3. Check that your data is back: **Channels**, **M3U Manager** and **EPG Manager** show your data, your auto-creation and normalization rules are listed, your scheduled tasks and their schedules are present, your alert methods are listed, and, if you use TLS, ECM's TLS settings still show your certificate.
4. Keep `./ecm-config-rescue` until you are satisfied that everything came back, then delete it, because it holds sensitive data.
5. Schedule the **DBAS Backup** task so you also keep a recent configuration backup: [Take a backup, scheduled backup](take-a-backup.md#option-b-scheduled-backup-recurring). It is an extra safety net on top of copying `/config`, not a replacement.

## If something is missing

Your original data is still in `./ecm-config-rescue`. Stop the container (`docker stop ecm-ecm-1`), replace the contents of `./ecm-config` with a fresh copy of `./ecm-config-rescue` (`cp -a ./ecm-config-rescue/. ./ecm-config/`), and start it again. If a DBAS backup from Step 2 is in `./ecm-config/backups/`, you can use it for a selective restore of the Dispatcharr-side configuration: [Restore a backup](restore-a-backup.md). It does not bring back the items in the table at the top of this page.

## If ECM is not running

Use the same sequence. A stopped or crash-looping container still keeps its data, and `docker cp` reads it. Skip Step 2 (the in-app backup needs a running ECM), run Step 3 (a no-op if the container has already exited), and go on through Step 8. If the cause of the failure is in your container definition, apply the fix from the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/container-preflight-failure.md) at Step 7, when you recreate the container with the mount. Do not remove the container before Step 5 has passed.
