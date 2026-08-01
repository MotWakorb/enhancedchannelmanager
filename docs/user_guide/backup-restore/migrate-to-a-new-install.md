# Migrate to a New Install

> **Status:** Shipped in v0.18.0.

---

## Overview

A migration is a backup on the old install, followed by a restore on the new install. Because the backup captures the full Dispatcharr configuration (M3U accounts, EPG sources, channels, groups, profiles, logos, and ECM settings), a restore on a fresh Dispatcharr instance brings it to the same operational state as the source.

This walkthrough assumes:
- The old install is still running (or was running long enough to take a backup).
- You have a new host ready with ECM and Dispatcharr installed but not yet configured.
- You want to carry your M3U/EPG credentials (if so, you need the encrypted backup path).

---

## Step 1: Take a backup on the old install

Take a **manual encrypted backup** so credentials travel with the artifact. If you are OK re-entering credentials on the new install, a standard (unencrypted) backup is sufficient.

### Encrypted backup (recommended for migration)

1. On the old install, go to **Settings → Backup & Restore → Encrypted Backup**.
2. Check the acknowledgement: *"I understand a lost passphrase makes this artifact permanently unrecoverable."*
3. Enter a passphrase of at least 12 characters. **Write it down in a password manager right now.** You need this passphrase on the new install. There is no recovery path if you lose it.
4. Enable **Include credentials** to include M3U/EPG passwords, SMTP passwords, and alert-method credentials in the artifact.
5. Click **Create encrypted backup**.

Wait for the backup to complete (a notification appears). Then go to **Settings → Backup & Restore → Saved Backups** and download the `.zip` to your workstation.

### Standard backup (if re-entering credentials is acceptable)

1. On the old install, go to **Settings → Backup & Restore → Back Up Now**.
2. Wait for completion, then download the artifact from **Saved Backups**.

---

## Step 2: Install ECM and Dispatcharr on the new host

Follow your normal installation procedure. At first run, ECM will show a setup wizard. You can skip the manual configuration steps. The backup will replace them.

Make sure the new Dispatcharr instance is:
- Running and reachable from ECM.
- Not yet configured (a clean install). If it already has some configuration, the restore will merge: existing entities with the same names will be updated, not duplicated.

---

## Step 3: Connect ECM to the new Dispatcharr instance

Complete the initial setup in ECM to point it at the new Dispatcharr API. The restore needs a working Dispatcharr connection to apply channel and stream configuration.

---

## Step 4: Run a dry-run preview

Before applying the restore, run a preview to confirm the backup artifact is valid and the expected counts look right.

1. On the new install, go to **Settings → Backup & Restore → Restore DBAS Backup**.
2. Upload the `.zip` artifact you downloaded from the old install.
3. If the artifact is encrypted, enter the passphrase when prompted.
4. ECM validates the artifact (integrity, schema version).
5. Click **Preview** (dry-run, the default).
6. Review the per-category counts: M3U accounts, EPG sources, channels, etc.

If the counts look wrong (too few channels, unexpected skip count), do not apply yet. Check the notes in the report for skip reasons.

---

## Step 5: Apply the restore

If the preview looks correct:

1. Click **Apply these changes**.
2. Confirm the dialog.
3. Watch the live progress for each of the 13 restore stages.
4. Review the restore-complete report.

The restore applies categories in the fixed hard order: M3U accounts → EPG sources → channel groups/profiles/stream profiles → user agents/settings → users → channels → logos.

### What to watch for

- **Users** are not restored by default. If you want to restore user accounts, explicitly enable the users category in the restore modal before applying.
- **The current admin account is always preserved**. The account you used to log in to the new ECM cannot be overwritten by the restore.
- **Logo misses** appear as a red banner if any logos could not be matched. This is a warning, not a failure. Channels still work, though logos may be absent.

---

## Step 6: Re-enter credentials (standard backup only)

If you used a standard (unencrypted) backup, M3U account passwords, EPG passwords, and similar credentials were redacted. On the new install:

1. Go to **Settings → M3U Accounts**.
2. Edit each M3U account and re-enter the password.
3. Go to **Settings → EPG Sources**.
4. Edit each EPG source and re-enter the password (if applicable).
5. Run an M3U refresh and an EPG refresh to populate the new Dispatcharr with streams and guide data.

If you used an encrypted backup with **Include credentials**, this step is not needed. Credentials travel with the artifact and are restored automatically.

---

## Step 7: Verify the new install

After the restore:

1. Go to **Channels** and confirm your channel list is present.
2. Check that channel groups, profiles, and stream assignments look correct.
3. Run an M3U refresh if streams are not populating.
4. Run an EPG refresh if guide data is absent.
5. Test playback on a sample channel.

---

## Decommissioning the old install

Once you have verified the new install is working:

1. Take a final backup on the old install (optional, for your records).
2. Shut down the old ECM and Dispatcharr containers.
3. Retain the old `/config/` data for at least a week in case you need to reference it.

---

## If the migration restore fails

See [Troubleshoot a restore](troubleshoot-restore.md) for specific failure scenarios.

For a hard failure on a fresh install (rollback incomplete, instance in a bad state), the simplest recovery is to wipe the new Dispatcharr instance entirely (delete all channels and accounts via the Dispatcharr UI or by re-initializing it), and then re-run the restore from scratch.

---

## Alternative: Cross-instance sync for ongoing DR

If your goal is ongoing DR (keeping a standby always in sync with your primary), consider [Cross-Instance Sync](cross-instance-sync.md) (v0.18.1) instead of, or in addition to, manual migration. Sync is not a backup and does not produce a restorable archive, but it keeps a second Dispatcharr instance continuously tracking the primary's configuration.

The recommended pattern for a DR setup:
1. Migrate the initial configuration to the standby via an encrypted backup restore (this article).
2. Configure cross-instance sync for ongoing replication after the initial migration.
