# Take a Backup

> **Audience:** Operator who wants to create a backup of their ECM configuration — either on demand or on a recurring schedule.
>
> **Status:** Shipped in v0.18.0.

---

## Before you start

Read [Backup & Restore overview](backup-overview.md) to understand what a backup contains and how credentials are handled. In particular, decide before you start whether you need an **encrypted backup** (to carry M3U/EPG credentials for a migration) or whether a standard redacted backup is sufficient.

---

## Option A — Manual backup (on demand)

Use this when you want a backup right now: before a major change, before a restore, or before migrating to a new install.

1. Go to **Settings → Backup & Restore**.
2. Find the **Backup** card.
3. Click **Back Up Now**.

ECM builds the artifact in the background via the `dbas_backup` task. A notification appears when the backup completes. The artifact is saved to `/config/backups/` on the ECM host.

### Taking an encrypted backup

If you need credentials (M3U passwords, EPG passwords, SMTP config) to travel with the artifact:

1. Go to **Settings → Backup & Restore**.
2. Find the **Encrypted Backup** card.
3. Check the acknowledgement: *"I understand a lost passphrase makes this artifact permanently unrecoverable."*
4. Enter a passphrase of at least 12 characters. Write it down somewhere safe before proceeding — ECM never stores the passphrase.
5. Enable **Include credentials** if you want M3U/EPG passwords included in the encrypted artifact.
6. Click **Create encrypted backup**.

> **Warning:** A lost passphrase means the encrypted artifact is permanently unrecoverable. There is no reset or recovery path. Store the passphrase in a password manager.

Encrypted backups are always manual — a passphrase cannot be persisted in the task scheduler.

---

## Option B — Scheduled backup (recurring)

Use this for routine protection. A scheduled backup runs automatically and applies the retention policy after each successful run.

1. Go to **Settings → Task Schedules**.
2. Find the `dbas_backup` task, or create a new schedule for it.
3. Set the interval (daily is recommended for most operators).
4. Optionally configure a cloud destination to receive the artifact off-host (see [Configure cloud destinations](configure-cloud-destinations.md)).
5. Save the schedule.

To verify the schedule is running, check **Settings → Task History** after the first scheduled fire, or look for the notification the task produces on completion.

---

## Where backups are stored

Local backups are saved to `/config/backups/` (under your ECM `CONFIG_DIR`, the same mounted volume as `journal.db`). Filenames follow the pattern:

```
ecm-backup-YYYY-MM-DD_HHMMSS.zip
```

All timestamps are UTC.

A `.sha256` sidecar file is written alongside each `.zip`, containing the artifact's SHA-256 hash. ECM verifies this hash before any restore, so do not rename or move the sidecar separately from its `.zip`.

### Listing saved backups

In **Settings → Backup & Restore**, the **Saved Backups** section lists all local backups with their creation time and size. You can download or delete individual backups from this list.

---

## What happens during a backup

When a backup runs, ECM:

1. Checkpoints the SQLite write-ahead log (`PRAGMA wal_checkpoint(TRUNCATE)`) so `journal.db` is self-contained.
2. Checks available disk space before writing anything.
3. Gathers all 12 configuration categories from both ECM's own database and the connected Dispatcharr API, applying credential redaction to every category (non-bypassable).
4. Streams the artifact to a temp file under `/config/` — never buffering the whole artifact in RAM, since logo files can be large.
5. Writes a SHA-256 sidecar next to the `.zip`.
6. Optionally uploads to each configured cloud destination and verifies the upload.
7. Applies the retention policy (prunes old local and cloud copies if a verified-successful backup was produced).
8. Emits a notification with the result.

---

## Checking backup status

- **Notifications panel** — a success or failure notification is emitted after each backup run.
- **Settings → Task History** — shows the last run time, duration, and outcome of each task.
- **Settings → Backup & Restore → Saved Backups** — shows the local artifact list.

---

## Next steps

- [Verify a backup](verify-a-backup.md) — run a dry-run preview to confirm the artifact is restorable before you need it.
- [Configure cloud destinations](configure-cloud-destinations.md) — add off-host storage for durability.
- [Restore a backup](restore-a-backup.md) — when you need to recover.
