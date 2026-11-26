# Recover Data from a Container with No Config Mount

Use this page when ECM's startup log says `DATA IS NOT PERSISTENT: /config is not a mounted volume`, **and the container already holds data you want to keep**. The goal is to get your configuration out of that container, give ECM a durable `/config` mount, recreate the container, and restore.

This is the recovery sequence. Setting up the mount in the first place, before there is anything to lose, is covered in [Install ECM](../getting-started/installation.md#give-ecm-a-place-to-keep-its-data) and [First run](../getting-started/first-run.md#what-the-preflight-checks-tell-you). The general restore tools are covered in [Take a backup](take-a-backup.md), [Verify a backup](verify-a-backup.md) and [Restore a backup](restore-a-backup.md). Read [Recovery patterns](../troubleshooting/recovery-patterns.md#restore-from-a-backup) if you want the wider picture of when a restore is the right tool.

!!! danger "Do not remove or recreate the container until Step 4 passes"
    Everything ECM stores is in the container's own writable layer. **Removing** the container (`docker rm`, `docker compose down`, an "update" in Portainer, Unraid or Watchtower, `docker compose up --force-recreate`) destroys it, and nothing can bring it back. **Stopping** or **restarting** the container keeps it. The commands below use the container name `ecm-ecm-1`. Use your own name if it differs.

## What you need

- Shell access to the Docker host.
- ECM running and reachable, so you can take a backup from the UI. If ECM will not start at all, see [If ECM is not running](#if-ecm-is-not-running).
- A place outside the container to keep the copy: a directory on the host, or better, another machine. The examples use `./ecm-rescue`.

## Step 1: Confirm the container has no `/config` mount

```bash
docker inspect ecm-ecm-1 --format '{{range .Mounts}}{{.Destination}}{{"\n"}}{{end}}'
```

If `/config` is **not** in the output, the directory is unmounted and you are in the right place. You can also search the startup log for the warning:

```bash
docker logs ecm-ecm-1 2>&1 | grep "DATA IS NOT PERSISTENT"
```

If `/config` **is** listed, your data is already on a volume. Stop here, and take a normal backup before any change.

## Step 2: Take a backup inside ECM

1. Open **Settings → Backup & Restore**.
2. Choose the artifact you need:
   - **Configuration Backup → Create Configuration Backup** for a standard backup. It replaces provider credentials with placeholders and leaves out your ECM accounts, so you will re-enter provider credentials and create an admin account after the restore.
   - **Encrypted Backup (Migration)** with **Include credentials** if you want M3U and EPG credentials to travel with the artifact. You choose a passphrase of at least 12 characters. A lost passphrase cannot be recovered. Store it in a password manager now.
3. Wait for the completion notification.

The new artifact is written to `/config/backups/` inside the container, which is exactly the directory you are about to lose. It also appears in the **Saved Backups** card. **Do not stop here.** Step 3 puts a copy somewhere else.

[Take a backup](take-a-backup.md#option-a-manual-backup-on-demand) has the full walkthrough and explains what each artifact does and does not contain.

## Step 3: Copy the backup outside the container

Copy the whole backups directory, so each `.zip` and its `.sha256` sidecar travel together:

```bash
mkdir -p ./ecm-rescue
docker cp ecm-ecm-1:/config/backups/. ./ecm-rescue/backups/
ls -l ./ecm-rescue/backups/
```

Expected: your new `ecm-backup-YYYY-MM-DD_HHMMSS.zip` and `ecm-backup-YYYY-MM-DD_HHMMSS.zip.sha256`. The files keep owner-only permissions (`0600`) and belong to the user who ran `docker cp`. Treat them as secrets, especially a plaintext standard artifact, which can carry operator-written text ECM cannot recognize as a secret.

Alternatively, download the artifact from the browser: in **Saved Backups**, use the download icon on the new artifact's row. The `docker cp` copy is the one to rely on, because it also brings the checksum sidecar and any earlier backups.

**If you use TLS**, also copy the certificate directory. Neither backup format carries `/config/tls` (it can hold your private key):

```bash
docker cp ecm-ecm-1:/config/tls ./ecm-rescue/tls
```

Uploaded M3U playlist files in `/config/m3u_uploads` are not carried either. Copy that directory too if it exists in your container.

## Step 4: Verify the copy before you destroy anything

1. Check the checksum on the copy, from the directory that holds both files:

   ```bash
   cd ./ecm-rescue/backups
   sha256sum -c ecm-backup-YYYY-MM-DD_HHMMSS.zip.sha256
   ```

   Expected: `ecm-backup-YYYY-MM-DD_HHMMSS.zip: OK`. Use your artifact's filename. If it does not say `OK`, copy the file again.

2. Run a dry-run preview from the copy:
   1. In **Settings → Backup & Restore**, open **Restore DBAS Backup → Restore from artifact...**.
   2. Select the `.zip` from `./ecm-rescue/backups/`. Enter the passphrase if it is encrypted.
   3. Leave the mode on **Preview (dry run)**, which makes no changes, and run it.
   4. Confirm the report lists your M3U accounts, EPG sources and channels, then close the dialog. **Do not choose Apply.**

   See [Verify a backup](verify-a-backup.md) for how to read the report.

3. Move or copy `./ecm-rescue` somewhere that will survive the host if the host itself is at risk.

Do not continue until both checks pass.

## Step 5: Add a durable `/config` mount

Edit your compose file so `/config` is backed by a volume. Either form is durable:

```yaml
services:
  ecm:
    volumes:
      - ./config:/config          # bind mount: a host directory you can browse and back up
      # or, as a named volume:
      # - ecm-config:/config
# For the named-volume form, declare it at the top level:
# volumes:
#   ecm-config:
```

For `docker run`, add `-v "$(pwd)/config:/config"` or `-v ecm-config:/config` to your original command. A bind-mount directory should be owned by the `PUID` and `PGID` you run ECM with (see the environment reference in the README). At startup ECM changes ownership of `/config` to that user.

The mount path inside the container must be `/config`. ECM's startup script prepares and checks that exact path, so mounting somewhere else and changing `CONFIG_DIR` is not a supported shortcut. See [Startup and interpreter variables](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/README.md#startup-and-interpreter-variables-advanced) for what `CONFIG_DIR` does and does not control.

## Step 6: Recreate the container

This is the step that destroys the old writable layer. Only continue once Step 4 passed.

```bash
docker compose up -d
```

Compose recreates the service because its definition changed. With plain `docker run`, run `docker stop ecm-ecm-1 && docker rm ecm-ecm-1`, then start it again with your original options plus the `-v` mount from Step 5.

Confirm the mount took effect:

```bash
docker logs ecm-ecm-1 2>&1 | grep "mounted volume"
```

Expected: `Config directory /config is on a mounted volume (data persists across recreates)`. If you see `DATA IS NOT PERSISTENT` again, the mount is not in place. Fix the compose file and repeat this step before you restore anything.

If the container exits instead of starting, see the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/container-preflight-failure.md). An update tool that copies the old container's environment is a known cause.

## Step 7: Set up the fresh instance

The new `/config` is empty, so ECM starts at first-run setup:

1. Create an admin account ([First run](../getting-started/first-run.md#create-the-admin-account)).
2. Connect ECM to Dispatcharr ([Connect ECM to Dispatcharr](../getting-started/connect-dispatcharr.md)). The restore writes to the Dispatcharr API, so this must work before you restore.

The restore flow in the next step does not write ECM's own accounts database, so it neither restores nor removes the admin account you just created. See [What a standard backup does not carry](backup-overview.md#what-a-standard-backup-does-not-carry).

## Step 8: Restore

1. Open **Settings → Backup & Restore → Restore DBAS Backup → Restore from artifact...**.
2. Select the `.zip` you copied in Step 3. Enter the passphrase if it is encrypted.
3. Run the **Preview (dry run)** first and read the report. On an empty instance, expect your entities under **WILL CREATE**.
4. Switch the mode to **Apply** (or use **Apply these changes** after a preview), type the file name when asked to confirm, and let it run.

[Restore a backup](restore-a-backup.md) covers category order, how streams are matched, and rollback behavior. If it refuses or reports failures, see [Troubleshoot a restore](troubleshoot-restore.md).

Restoring a standard backup leaves provider credentials unset. Re-enter them and refresh each M3U account as described in [Step 6 of Migrate to a new install](migrate-to-a-new-install.md#step-6-re-enter-credentials-standard-backup-only). An encrypted backup with **Include credentials** does not need that step.

If you copied `./ecm-rescue/tls` in Step 3, put the certificate files back into the new `/config/tls` directory, or reissue the certificate from ECM's TLS settings.

## Step 9: Verify the recovery

1. Confirm the mount is still in place and ECM is ready:

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

2. In the UI, check that **Channels**, **M3U Manager** and **EPG Manager** show your data, and that a refresh of an M3U account succeeds once credentials are back.
3. Take a fresh backup (Step 2) and confirm it is listed in **Saved Backups**. It is now on a volume that survives a recreate.
4. Schedule the **DBAS Backup** task so you always have a recent copy: [Take a backup, scheduled backup](take-a-backup.md#option-b-scheduled-backup-recurring).

Keep `./ecm-rescue` until you are satisfied that everything came back, then delete it, because it holds sensitive data.

## If ECM is not running

If the container is stopped or exits at startup, the in-app backup in Step 2 is not available. A stopped container still keeps its data, so `docker cp` still works:

```bash
docker cp ecm-ecm-1:/config/backups/. ./ecm-rescue/backups/
```

That recovers any backups already in `/config/backups/`, for example from the scheduled **DBAS Backup** task. Continue with Step 4 using the newest one. If there are none, do not remove the container. Fix the startup failure first with the [container preflight failure runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/container-preflight-failure.md), then take a backup.
