# Recover Data from a Container with No Config Mount

Use this page when ECM's startup log says `DATA IS NOT PERSISTENT: /config is not a mounted volume`, **and the container may hold data you want to keep**. The goal is to copy the whole `/config` directory out of that container, give ECM a durable `/config` mount that starts from that copy, recreate the container on the same image, and verify.

The same warning ends with `Back up first (Settings -> Backup & Restore) if this container already holds data you care about`. For a container with no mount, that advice alone is not enough: an in-app backup does not carry everything ECM keeps in `/config` (see the table below), and a container that fails at startup cannot take one at all. Follow the copy-first steps on this page instead.

This is the recovery sequence. Setting up the mount in the first place, before there is anything to lose, is covered in [Install ECM](../getting-started/installation.md#give-ecm-a-place-to-keep-its-data) and [First run](../getting-started/first-run.md#what-the-preflight-checks-tell-you).

## Why this page copies the whole directory

ECM keeps its own state in `/config`. Each in-app backup type restores only part of it:

- **Restore DBAS Backup** (for Configuration Backup, scheduled and Encrypted Backup artifacts) restores the Dispatcharr-side data (M3U accounts, EPG sources, channel groups and profiles, channels, stream profiles, Dispatcharr users, user agents, DVR rules, server groups, upcoming recordings) and logos. It also restores ECM's own `settings.json`, except the Dispatcharr connection settings, any credential the artifact redacted, and ECM's internal bookkeeping keys. It never reads the artifact's `journal.db`, so it applies none of ECM's accounts, rules or schedules.
- **Restore Full Backup** (the legacy Full Backup ZIP, and the restore option in first-run setup) writes `journal.db` and `settings.json`. It brings back ECM's rules, scheduled tasks and the other configuration tables listed below, with credential fields removed. A Full Backup taken on a current build carries no ECM accounts. A Full Backup ZIP from an older build that still carried accounts installs them when you restore it onto an instance that has none. This flow does not accept a DBAS artifact: it requires a legacy `ecm_backup.json` manifest that DBAS artifacts do not contain.
- **Dispatcharr's own data is not in the ECM container**, so recreating ECM does not touch it. What you stand to lose is ECM's side.

| What ECM keeps | Where it lives in `/config` | Restore DBAS Backup | Restore Full Backup |
|-|-|-|-|
| ECM login accounts and sessions | `journal.db` | No | No (yes only from an older ZIP that carried accounts, onto an instance with none) |
| Auto-creation rules, normalization rules and mappings, tag groups | `journal.db` | No | Yes |
| Scheduled tasks and their schedules | `journal.db` | No | Yes |
| Alert methods | `journal.db` | No | Yes, without their credentials |
| FFmpeg and dummy-EPG profiles, hidden groups | `journal.db` | No | Yes |
| Journal history, cloud storage and sync targets, M3U digest settings | `journal.db` | No | No |
| ECM settings | `settings.json` | Yes, except the connection, redacted credentials and internal keys | Yes, except the credential fields the backup masked |
| Authentication settings | `auth_settings.json` | No | No |
| TLS settings, certificate and private key | `tls_settings.json`, `tls/` | No | No |
| The key that decrypts stored cloud-storage and sync-target credentials | `.export_key` | No | No |
| The MCP API key and service credentials | `api-key`, `.api-key.recovery`, `mcp-service.json` (in `/config`, or in `MCP_SECRETS_DIR` when that variable points elsewhere) | No | No |
| Probe history | `probe_history.json` | No | No |
| Uploaded playlist files | `m3u_uploads/` | No | No |

Only the whole-directory copy keeps all of it. A DBAS configuration backup is an optional extra copy (Step 2), not the restore source.

!!! danger "Do not remove or recreate the container until the copy is verified (Step 5)"
    Everything ECM stores is in the container's own writable layer. **Removing** the container (`docker rm`, `docker compose down`, an "update" in Portainer, Unraid or Watchtower, `docker compose up --force-recreate`) destroys it, and nothing can bring it back. **Stopping** or **restarting** the container keeps it. Step 7 is the only step that cannot be undone, and it comes strictly after the verified copy. The commands use the container name `ecm-ecm-1`. Use your own name if it differs.

## What you need

- Shell access to the Docker host, and enough free disk space for two copies of `/config`. Check its size first: `docker exec ecm-ecm-1 du -sh /config` (if the container is running).
- A directory on the host to keep the copy. The examples use `./ecm-config`, which also becomes the new mount. Treat it as secret: it holds password hashes, the TLS private key and the encryption key for stored credentials.
- **Run every command on this page as the same host user.** `docker cp` writes files owned by whoever ran it, and the key files are owner-only (`0600`). If you run `sudo docker cp`, the copy belongs to root, and a later `cp -a` or Python check run without `sudo` cannot read it. Either use `sudo` for every step or for none.
- No Python is needed on the host. Step 5 shows how to run the check with the ECM image's own interpreter.

## Step 1: Confirm the container has no `/config` mount, and record its settings

1. List the container's mounts:

   ```bash
   docker inspect ecm-ecm-1 --format '{{range .Mounts}}{{.Type}} {{if .Name}}{{.Name}}{{else}}{{.Source}}{{end}} -> {{.Destination}}{{"\n"}}{{end}}'
   ```

   If no line ends in `-> /config`, the directory is unmounted and you are in the right place. You can also search the startup log for the warning:

   ```bash
   docker logs ecm-ecm-1 2>&1 | grep "DATA IS NOT PERSISTENT"
   ```

   If a line ends in `-> /config`, your data is already on a volume. Stop here, and back it up as you normally would before any change. Note every **other** line in the list, such as a volume at `/run/secrets/ecm-mcp`. The recreated container needs each of them.

2. Record the exact image the container runs, by ID:

   ```bash
   docker inspect ecm-ecm-1 --format '{{.Image}}'
   ```

   The output is an ID such as `sha256:d314be90...`. Write it down: Step 7 recreates the container on this exact image. Do not use the tag from `{{.Config.Image}}` for this. Tags such as `latest` or `dev` move, so the same tag can already name a newer image.

3. Save the container's full definition, so you can rebuild it with the same settings:

   ```bash
   docker inspect ecm-ecm-1 > ecm-inspect.json
   chmod 600 ecm-inspect.json
   ```

   **Treat `ecm-inspect.json` as secret.** It contains the container's environment, and environment variables can hold credentials. Do not paste it into an issue or a forum post. Step 6 uses it.

4. Check whether the MCP credentials live outside `/config`:

   ```bash
   docker inspect ecm-ecm-1 --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^MCP_SECRETS_DIR='
   ```

   No output means ECM keeps its MCP credential files in `/config`, and Step 4 copies them with everything else. If it prints a path, such as `MCP_SECRETS_DIR=/run/secrets/ecm-mcp`, compare it with the mount list from item 1. A path that **is** a mount (the MCP overlay mounts a named volume there) survives the recreate as long as you keep that mount. A path that is **not** a mount is in the writable layer too, and Step 4 copies it.

## Step 2 (optional): Take a DBAS configuration backup, if ECM is running

Skip this if ECM is not running. If it is, take a backup in **Settings → Backup & Restore** (see [Take a backup](take-a-backup.md#option-a-manual-backup-on-demand)) and wait for the completion notification. The artifact is written to `/config/backups/`, so the whole-directory copy in Step 4 carries it along.

This is an **additional** copy, useful for a later selective restore. It is not a substitute for Step 4: see [Why this page copies the whole directory](#why-this-page-copies-the-whole-directory).

## Step 3: Stop the container

```bash
docker stop ecm-ecm-1
```

Stopping lets ECM close its SQLite databases cleanly, so `journal.db` and its `-wal` file are consistent when you copy them. A stopped container keeps its data, and `docker start ecm-ecm-1` brings it back.

If the container is in a restart loop, `docker stop` ends the loop: its status changes from `Restarting` to `Exited`, and it stays stopped. If it has already exited, the command changes nothing. Either way, continue with Step 4.

## Step 4: Copy the whole `/config` directory out

`docker cp` works on a stopped container.

```bash
mkdir -p ./ecm-config
docker cp ecm-ecm-1:/config/. ./ecm-config/
```

If Step 1 found an `MCP_SECRETS_DIR` path that is **not** a mount, copy that directory as well. Use the path it printed:

```bash
mkdir -p ./ecm-mcp-secrets
docker cp ecm-ecm-1:/run/secrets/ecm-mcp/. ./ecm-mcp-secrets/
```

The files keep their permissions, such as the owner-only `0600` on key files, and belong to the host user who ran `docker cp`. Step 6 makes ECM run as that owner.

## Step 5: Verify the copy before you destroy anything

1. Check that the copy holds a database:

   ```bash
   ls -la ./ecm-config/journal.db
   ```

   If this prints `No such file or directory`, **stop**: go to [If the copy has no `journal.db`](#if-the-copy-has-no-journaldb). Repeating the copy will not change the result.

2. List the copy and check that the files you expect are present:

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
   - `.export_key`
   - `api-key` and `mcp-service.json`, here or in `./ecm-mcp-secrets` (see Step 1, item 4)
   - `probe_history.json`
   - `m3u_uploads/`, if you uploaded playlist files
   - `backups/`, with any backup artifacts (including the one from Step 2)
   - `uploads/` (logos)
   - Optional: `logs/` (ECM's persistent log), `dbas/` (restore working files), `mcp_oauth.db` (the store of the retired MCP OAuth feature, whose code was removed in v0.17.3; nothing reads it now, so keep it in the copy and ignore it)

   Dotfiles do not show in a plain `ls`, which is why the command uses `-a`. If `journal.db` is zero bytes, or `docker cp` printed an error, repeat Step 4.

3. Check that the database is readable and holds your accounts. This works on a scratch duplicate, so it does not touch the copy. With Python 3 on the host:

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

   If the host has no Python 3 (stock Unraid has none), run the same check with the ECM image's own interpreter. Replace `<image-id>` with the ID from Step 1:

   ```bash
   tmp=$(mktemp -d)
   cp ./ecm-config/journal.db* "$tmp"/
   docker run --rm --user "$(id -u):$(id -g)" -v "$tmp:/c" \
     --entrypoint /opt/venv/bin/python <image-id> -c "
   import sqlite3, sys
   c = sqlite3.connect(sys.argv[1])
   print(c.execute('PRAGMA integrity_check').fetchone()[0])
   print(c.execute('SELECT count(*) FROM users').fetchone()[0])
   " /c/journal.db
   rm -rf "$tmp"
   ```

   The scratch duplicate is mounted writable, not `:ro`, because ECM's database uses SQLite's write-ahead log, and SQLite cannot open a write-ahead-log database in a read-only directory. Only the throwaway duplicate is exposed, never `./ecm-config`.

   Expected: `ok`, then the number of ECM accounts you have (at least `1` if you had signed in before).

4. Make a second copy of the verified directory:

   ```bash
   cp -a ./ecm-config ./ecm-config-rescue
   ```

   ECM may change the ownership of the first copy at startup, so this one stays untouched as your fallback. Both copies are on the same host. If the host itself is at risk, move `./ecm-config-rescue` to another machine or disk as well.

Do not continue until these checks pass.

## Step 6: Add a durable `/config` mount, with the container's other settings

Point `/config` at the verified copy, and rebuild everything else about the container from `ecm-inspect.json`.

**If you manage ECM with Portainer, Unraid or Compose,** edit the existing stack, template or compose file to add the `/config` volume, rather than typing a new `docker run` command. That keeps the ports, network and other settings it already has. For Compose:

```yaml
services:
  ecm:
    image: sha256:<the image ID from Step 1>
    volumes:
      - ./ecm-config:/config      # bind mount: the directory you just copied into
```

Keep every other volume the service already has. If Step 4 copied an MCP secrets directory, mount it at the same path, for example `- ./ecm-mcp-secrets:/run/secrets/ecm-mcp`. If your tool only accepts an image tag, keep the same tag, and do not choose any option that pulls a newer image.

**With plain `docker run`,** rebuild the command from `ecm-inspect.json`. These fields hold no secrets and can be printed directly:

```bash
docker inspect ecm-ecm-1 --format 'Ports: {{json .HostConfig.PortBindings}}{{"\n"}}Network: {{.HostConfig.NetworkMode}}{{"\n"}}Restart: {{.HostConfig.RestartPolicy.Name}}{{"\n"}}ExtraHosts: {{json .HostConfig.ExtraHosts}}'
```

Carry over:

- the published ports (`-p`), the network (`--network`; often how ECM reaches Dispatcharr), the restart policy (`--restart`) and any extra hosts (`--add-host`)
- `PUID`, `PGID`, every `ECM_*` variable and any other variable you set yourself, from `Config.Env` in `ecm-inspect.json`
- **every** other mount from Step 1, such as the `/run/secrets/ecm-mcp` volume
- the new `-v "$(pwd)/ecm-config:/config"` mount, and the image ID from Step 1 as the image

**Do not copy `PATH`** (or `PYTHON_VERSION`, `GPG_KEY` and the other variables the image sets itself) from `ecm-inspect.json`. A `PATH` copied from a container created before 0.16.0 is exactly the trap described in the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/container-preflight-failure.md). Let the image supply it.

The mount path inside the container must be `/config`. ECM's startup script prepares and checks that exact path, so mounting somewhere else and changing `CONFIG_DIR` is not a supported shortcut. See [Startup and interpreter variables](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/README.md#startup-and-interpreter-variables-advanced) for what `CONFIG_DIR` does and does not control.

**Make ECM run as the owner of the copy.** At startup ECM switches its `appuser` account to your `PUID` and `PGID`, then tries `chown -R` on `/config`. That `chown` is best effort: it fails silently on shares that refuse ownership changes (NFS with `root_squash`, some NAS shares). The only check afterwards is that ECM can create a file in `/config` itself, so ECM can pass that check and still be unable to open `.export_key`, `settings.json` or the database, and then fail to start. Do one of these:

- Set `PUID` and `PGID` to the owner of the copy. `stat -c '%u %g' ./ecm-config` prints them.
- Or change the copy's owner on the host to the `PUID` and `PGID` you run ECM with (the default is `1000`): `sudo chown -R 1000:1000 ./ecm-config`.

!!! danger "Step 7 destroys the old container's data"
    Removing the container is the one irreversible step on this page. Do it only once Step 5 passed and the mount from this step is in your compose file, template or run command.

## Step 7: Recreate the container on the same image (irreversible)

```bash
docker compose up -d
```

Compose recreates the service because its definition changed, which removes the old container. In Portainer or Unraid, apply the edited stack or template. With plain `docker run`, run `docker rm ecm-ecm-1`, then the command you built in Step 6.

Recreate on the **same image** you recorded in Step 1. Do not pull a newer image or run `docker compose pull` in this step. Bring the instance back as it was, verify it, and update afterwards in Step 9.

Confirm the mount and the image:

```bash
docker logs ecm-ecm-1 2>&1 | grep "mounted volume"
docker inspect ecm-ecm-1 --format '{{.Image}}'
```

Expected: `Config directory /config is on a mounted volume (data persists across recreates)`, and the same image ID as in Step 1. If you see `DATA IS NOT PERSISTENT` again, the mount is not in place. Fix the definition and repeat this step.

The mount line is printed even when another startup check fails. If the container exits instead of starting, your data is now safe in `./ecm-config`, and every further recreate is safe. Follow the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/container-preflight-failure.md), keeping this mount, and then continue with Step 8.

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
4. Keep `./ecm-config-rescue` until you are satisfied that everything came back, then delete it, because it holds sensitive data. Delete `ecm-inspect.json` too once you no longer need it.
5. Schedule the **DBAS Backup** task so you also keep a recent configuration backup: [Take a backup, scheduled backup](take-a-backup.md#option-b-scheduled-backup-recurring). It is an extra safety net on top of copying `/config`, not a replacement.

## Step 9: Update the image, as a separate step

Only after Step 8 passes, move back to your normal image tag (for example `ghcr.io/motwakorb/enhancedchannelmanager:latest`) in place of the image ID, pull it and recreate. With `/config` mounted, this recreate keeps your data:

```bash
docker compose pull ecm
docker compose up -d ecm
```

Then repeat Step 8, item 1. If the update brings a startup failure, use the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/container-preflight-failure.md).

## If the copy has no `journal.db`

This container never held your data. The usual way to get here: an update tool (Portainer, Unraid, Watchtower) recreated a container that had no `/config` mount, and that recreate already discarded the data. The new container then failed its startup checks before ECM ever ran, so its `/config` holds only the empty `tls/` and `uploads/logos/` directories that ship in the image. The ECM image declares no Docker volume, so no anonymous volume kept a copy either. Repeating the copy cannot bring the data back.

First, run `docker ps -a`. If it lists another, older ECM container, that one may still hold the data: run this page against it instead.

Otherwise, restore from an artifact you hold **outside** the container, or start fresh. Either way, first give ECM a mount and get it running: there is nothing in this container to lose now, so do Steps 6 and 7 with an empty `./ecm-config`, fix the startup failure with the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/container-preflight-failure.md), and confirm ECM is ready (Step 8, item 1). Then restore:

| What you have | How to restore it | What it brings back |
|-|-|-|
| A DBAS artifact you downloaded (Configuration Backup, Encrypted Backup, or a download from **Saved Backups**) | **Restore DBAS Backup**: [Restore a backup](restore-a-backup.md). Run the dry-run preview first. | Dispatcharr-side configuration, logos, and ECM's settings except the connection, redacted credentials and internal keys. No ECM accounts, rules, schedules or alert methods. |
| A DBAS artifact in a cloud destination you had configured | Download it from the provider's own interface. ECM cannot restore from a cloud destination directly, and the destination's configuration was in the discarded container. Then use **Restore DBAS Backup** as above. | The same as the row above. |
| A Full Backup ZIP you downloaded | **Restore Full Backup** in **Settings → Backup & Restore**, or the restore option in first-run setup. See [Notices after a Full Backup restore](restore-a-backup.md#notices-after-a-full-backup-restore). | ECM's rules, scheduled tasks, alert methods (without credentials), profiles and settings. ECM accounts only if the ZIP comes from an older build that still carried them. |
| Nothing outside the container | Start fresh: [First run](../getting-started/first-run.md). | Nothing. |

Dispatcharr's own data was not in the ECM container, so it is most likely still intact. The DBAS dry-run preview shows what a restore would change on it before you apply anything.

## If something is missing

Your original data is still in `./ecm-config-rescue`. Stop the container (`docker stop ecm-ecm-1`), replace the contents of `./ecm-config` with a fresh copy of `./ecm-config-rescue` (`cp -a ./ecm-config-rescue/. ./ecm-config/`), check its ownership as in Step 6, and start it again. If a DBAS backup from Step 2 is in `./ecm-config/backups/`, you can use it for a selective restore: [Restore a backup](restore-a-backup.md). It restores only what the **Restore DBAS Backup** column in the table at the top of this page lists.

## If ECM is not running

Use the same sequence. A stopped or crash-looping container still keeps whatever data it holds, and `docker cp` reads it. Skip Step 2 (the in-app backup needs a running ECM), run Step 3 (it ends a restart loop), and go on through Step 8. If Step 5 finds no `journal.db`, follow [If the copy has no `journal.db`](#if-the-copy-has-no-journaldb). If the cause of the failure is in your container definition, Step 7 brings the container back on the same image with the mount in place, and you then apply the fix from the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/container-preflight-failure.md) as a separate, now safe, recreate. Do not remove the container before Step 5 has passed.
