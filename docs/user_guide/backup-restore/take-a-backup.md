# Take a Backup

> **Status:** Shipped in v0.18.0.

---

## Before you start

Read [Backup & Restore overview](backup-overview.md) to understand what a backup contains and how credentials are handled. In particular, decide before you start whether you need an **encrypted backup** (to carry M3U/EPG credentials for a migration) or whether a standard redacted backup is sufficient.

---

## Option A: Manual backup (on demand)

Use this first when setting up backups, and whenever you want a fresh recovery point before a major change or restore.

1. Go to **Settings → Backup & Restore**.
2. Find the **Configuration Backup** card.
3. Click **Create Configuration Backup**.

ECM builds the artifact in the background via the `dbas_backup` task. A notification appears when the backup completes. The artifact and a `.sha256` sidecar are saved to `/config/backups/` on the ECM host.

This is a **configuration-only** backup. ECM replaces recognized M3U and EPG provider credential fields and credential-bearing URL values, and it removes your ECM login accounts. On a fresh install, you must create an ECM admin account and re-enter every provider credential after restoring it. Operator-authored free text, such as source names and rule notes, may travel verbatim and could contain a secret ECM cannot recognize. Credential-free provider addresses remain. Inspect that content before sharing the artifact outside your control.

If you need credentials and ECM accounts to travel with the artifact for a migration, use the Encrypted Backup card described below and enable **Include credentials**.

### Taking an encrypted backup

If you need credentials (M3U passwords, EPG passwords, SMTP config) to travel with the artifact:

1. Go to **Settings → Backup & Restore**.
2. Find the **Encrypted Backup (Migration)** card.
3. Check the acknowledgement: *"I understand that a lost passphrase means this backup cannot be recovered."*
4. Enter a passphrase of at least 12 characters in the **Passphrase** field, then repeat it in the **Confirm passphrase** field. Write it down somewhere safe before proceeding. ECM never stores the passphrase.
5. Enable **Include credentials** if you want M3U/EPG passwords included in the encrypted artifact.
6. Click **Create Encrypted Backup**.

> **Warning:** A lost passphrase means the encrypted artifact is permanently unrecoverable. There is no reset or recovery path. Store the passphrase in a password manager.

Encrypted backups are always manual. A passphrase cannot be persisted in the task scheduler.

> **Note:** An encrypted artifact is written with the same
> `ecm-backup-<date>_<time>.zip` filename convention as a standard
> backup. Nothing in the filename, the `.sha256` sidecar, or the Saved
> Backups list distinguishes an encrypted artifact from a standard one.
> Record which artifacts are encrypted alongside the passphrase you
> stored for them. The restore flow needs to be told which is which:
> the upload path (**Restore from artifact…**) detects encryption
> automatically and prompts for the passphrase, but the Saved Backups
> path requires you to tick **This backup is encrypted** yourself
> before running the preview. (If you need to check a file by hand,
> `file(1)` reports a standard artifact as `Zip archive data` and an
> encrypted one as plain `data`; the encrypted container starts with
> the magic bytes `ECMBKENC`.)

---

## Option B: Scheduled backup (recurring)

After creating and verifying a manual backup, set up a schedule for ongoing protection. A backup you take by hand only reflects that moment; a schedule keeps a current recovery point on disk. Scheduled backups are always redacted configuration backups because ECM never stores an encryption passphrase in the scheduler. This is the unattended-recovery key strategy: the scheduled artifact deliberately contains no ECM account hashes, live credentials, TLS private keys, or raw uploaded playlists, so it needs no secret key that could be lost with the host. After a restore, keep the destination's existing credentials or re-enter them.

1. Go to **Settings → Scheduled Tasks**.
2. Find the **DBAS Backup** task, or create a new schedule for it.
3. Set the interval (daily is recommended for most operators).
4. Optionally configure a cloud destination to receive the artifact off-host (see [Configure cloud destinations](configure-cloud-destinations.md)).
5. Save the schedule.

To verify the schedule is running, expand **History** on the DBAS Backup task card after the first scheduled fire, or look for the notification the task produces on completion. There is no separate "Task History" destination in Settings; run history lives per-task, behind each card's own History expander.

---

## Where backups are stored

Local backups are saved to `/config/backups/` (under your ECM `CONFIG_DIR`, the same mounted volume as `journal.db`). Filenames follow the pattern:

```
ecm-backup-YYYY-MM-DD_HHMMSS.zip
```

All timestamps are UTC.

A `.sha256` sidecar file is written alongside each `.zip`, containing the artifact's SHA-256 hash. ECM verifies this hash before any restore, so do not rename or move the sidecar separately from its `.zip`.

ECM creates locally persisted ZIP, YAML, and checksum files with owner-only
permissions (`0600`). Preserve equivalent access controls when copying an
artifact to another filesystem or backup service.

### Listing saved backups

The **Saved Backups** card on **Settings → Backup & Restore** lists the DBAS `.zip` artifacts that Option A and Option B above produce, each with **restore**, **download**, and **delete** actions. Take a DBAS backup and open Saved Backups right after, and the new artifact appears in the list (for example, `ecm-backup-2026-08-03_000043.zip · 995.4 KB`).

The card's own caption still reads "YAML backups saved on the server by the scheduled backup task." That caption is stale and describes an older behavior; the card lists DBAS `.zip` artifacts, not YAML exports. This is a known issue; treat the caption as leftover copy, not a description of what the card actually shows.

To download an artifact directly from the card, or to verify one before you need it, see [Verify a backup](verify-a-backup.md#verifying-a-scheduled-backup-proactively).

---

## What happens during a backup

When a backup runs, ECM:

1. Checkpoints the SQLite write-ahead log (`PRAGMA wal_checkpoint(TRUNCATE)`) so `journal.db` is self-contained.
2. Checks available disk space before writing anything.
3. Gathers all 12 configuration categories from both ECM's own database and the connected Dispatcharr API, applying credential redaction to every category (non-bypassable). In the same pass it reduces its copy of `journal.db` to the fixed list of configuration tables a standard artifact is allowed to carry, dropping every other table and compacting the file. See [What a standard backup does not carry](backup-overview.md#what-a-standard-backup-does-not-carry).
4. Streams the artifact to a temp file under `/config/`, never buffering the whole artifact in RAM, since logo files can be large.
5. Writes a SHA-256 sidecar next to the `.zip`.
6. Optionally uploads to each configured cloud destination and verifies the upload.
7. Applies the retention policy (prunes old local and cloud copies if a verified-successful backup was produced).
8. Emits a notification with the result.

---

## If a backup fails while removing sensitive data

Removing your accounts, credentials and history from the artifact's copy of `journal.db` is a required step, not a best-effort one. If ECM cannot open that database, cannot read one of its tables, or cannot rewrite it, **the whole backup fails and no artifact is written**. The task reports a failure and the on-demand download returns an error instead of a file.

This is deliberate and it is the safe direction. Earlier builds fell back to including the database untouched, which produced a normal-looking backup that quietly carried everything the redaction was supposed to remove. A backup that fails is visible; one that silently ships your admin password hash is not.

**What to do:**

1. Check ECM's logs for the failure: `docker logs --since 30m ecm-ecm-1 2>&1 | grep BACKUP`. The message names which table or step failed.
2. Confirm `/config/journal.db` is present, readable, and not truncated. A restore that was interrupted, a full disk, or a volume mounted read-only are the usual causes.
3. Confirm free space on the `/config` volume. The scrub works on a temporary copy of the database and needs room for it.
4. Retry the backup once the cause is cleared.

The temporary unscrubbed copy is deleted on the way out of a failed scrub, so a failure does not leave an unredacted database sitting in the container's temp directory.

If the database itself is damaged, restore the most recent good backup before taking a new one.

---

## Checking backup status

- **Notifications panel**: a success or failure notification is emitted after each backup run.
- **Scheduled Tasks → DBAS Backup → History**: shows the last run time, duration, and outcome for the task. There is no separate "Task History" destination in Settings.
- **Settings → Backup & Restore → Saved Backups**: lists the DBAS `.zip` artifacts themselves, with restore, download, and delete actions; see [Listing saved backups](#listing-saved-backups) above.

---

## Next steps

- [Verify a backup](verify-a-backup.md): run a dry-run preview to confirm the artifact is restorable before you need it.
- [Configure cloud destinations](configure-cloud-destinations.md): add off-host storage for durability.
- [Restore a backup](restore-a-backup.md): when you need to recover.
