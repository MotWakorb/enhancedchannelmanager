# Migrate to a New Install

---

## Overview

A migration is a backup on the old install, followed by a restore on the new install. Because the backup captures the full Dispatcharr configuration (M3U accounts, EPG sources, channels, groups, profiles, logos, and ECM settings), a restore on a fresh Dispatcharr instance brings it to the same operational state as the source.

This walkthrough assumes:
- The old install is still running (or was running long enough to take a backup).
- You have a new host ready with ECM and Dispatcharr installed but not yet configured.
- You want to carry your M3U/EPG credentials (if so, you need the encrypted backup path).

!!! danger "Read this before you migrate"
    Written for ECM `0.18.1` / Dispatcharr `0.28.2`. A restored
    lineup genuinely **plays**, confirmed by fetching real media bytes,
    not just checking a URL is set. One thing still needs your attention
    on every migration:

    - **A standard (redacted) backup needs a recovery sequence** before
      playback works: re-enter credentials, then refresh the M3U account.
      Those two steps are the whole recovery. See Step 6. Note that a
      standard artifact now removes the provider **username** as well as
      the password, so Step 6 re-enters both.

    - **A standard backup does not migrate your ECM accounts.** Only an
      encrypted backup with **Include credentials** carries them. Without
      it, the new install starts at first-run setup and you create an
      admin account there. See
      [What a standard backup does not carry](backup-overview.md#what-a-standard-backup-does-not-carry).

    EPG links round-trip correctly (9 of 9 seeded links survived on
    `0.18.1`, on both artifact variants). See Step 7 below to verify
    this on your own install.

---

## ECM-uploaded logos and this migration

!!! success "Uploaded logos are included in the backup and restore intact"
    A logo uploaded through ECM's own Logo Manager has its image bytes
    archived in the backup and restores intact on the new install. No
    manual steps are needed for these logos.

    Logos assigned from a remote http(s) URL (auto-assigned from an M3U
    or EPG feed) are handled differently: they are not stored in the
    artifact, and restore by re-fetching the same URL on the new
    install.

    If a logo still fails to restore for some other reason, that failure
    is counted and named in the restore report. It does not abort or
    roll back the rest of the migration.

---

## Step 1: Take a backup on the old install

Take a **manual encrypted backup** so credentials travel with the artifact. If you are OK re-entering credentials on the new install, a standard (unencrypted) backup is sufficient.

If you want both a redacted recovery point and a credential-complete migration artifact, create the configuration backup first, then create the encrypted backup. The encrypted-backup settings apply only to that one run; later manual and scheduled backups return to the standard redacted format.

### Standard backup (if re-entering credentials is acceptable)

1. On the old install, go to **Settings → Backup & Restore**.
2. Find the **Configuration Backup** card.
3. Click **Create Configuration Backup**.
4. Wait for the completion notification, then download the new `.zip` from **Saved Backups** to your workstation.

This artifact does not contain working provider credentials or ECM login accounts. Restoring it onto a fresh install means creating an ECM admin account and re-entering every M3U and EPG provider credential by hand.

See [Take a backup](take-a-backup.md#option-a-manual-backup-on-demand) for the full on-demand backup walkthrough.

### Encrypted backup (recommended for migration)

1. On the old install, go to **Settings → Backup & Restore → Encrypted Backup (Migration)**.
2. Check the acknowledgement: *"I understand a lost passphrase makes this artifact permanently unrecoverable."*
3. Enter a passphrase of at least 12 characters. **Write it down in a password manager right now.** You need this passphrase on the new install. There is no recovery path if you lose it.
4. Enable **Include credentials** to include M3U/EPG passwords, SMTP passwords, and alert-method credentials in the artifact.
5. Click **Create Encrypted Backup**.

Wait for the backup to complete (a notification appears). Then go to **Settings → Backup & Restore → Saved Backups** and download the `.zip` to your workstation.

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
3. Watch the live progress for each of the 12 restore categories.
4. Review the restore-complete report.

The restore applies categories in the fixed hard order: M3U accounts → EPG sources → channel groups/profiles/stream profiles → user agents/settings → users → channels → logos.

### What to watch for

!!! success "You do not need to match the old admin's username"
    The new Dispatcharr's superuser can have a **different** username
    than the old install's admin. Name the new install's superuser
    whatever you want at Dispatcharr's own first-run wizard.

- **Logo bytes restore correctly**, including logos uploaded through
  ECM's own Logo Manager. A round-trip drill measured 10 of 11 logos
  sha256-identical to source. See
  [ECM-uploaded logos and this migration](#ecm-uploaded-logos-and-this-migration)
  for what's archived and what isn't. The dry-run preview's logo counts
  match what the apply does. Either way, verify logos on the new install
  directly rather than trusting a count.

---

## Step 6: Re-enter credentials (standard backup only)

If you used a standard (unencrypted) backup, the whole provider credential was removed, **username as well as password**, along with EPG credentials and similar secrets. On the new install:

!!! success "The credential fields are empty after a redacted restore"
    After a redacted restore, the M3U account's username and password
    fields are correctly **empty**, not filled with a placeholder. This
    is expected; both need the real values entered before the account
    will authenticate. If a provider URL carried the credential in its
    query string, that URL is blank too and needs re-entering with it.

!!! success "Two steps: re-enter the credential, then refresh"
    A redacted restore leaves every channel on a placeholder stream,
    because the M3U account had no credential at the moment the restore
    tried to attach real streams. The recovery is:

    1. Re-enter the credential (steps 1–4 below).
    2. Refresh the M3U account (step 5 below).

    When that refresh completes, ECM reattaches every channel still
    holding a placeholder onto the real stream, then removes the leftover
    placeholders and the synthetic **ECM Custom Streams (DBAS restore)**
    account. Only placeholders ECM itself created are touched.

    **This covers the Refresh action on an individual M3U account and the
    scheduled M3U refresh task.** A "refresh all accounts" action, or a
    refresh performed in Dispatcharr's own UI, is picked up on the next
    scheduled refresh instead of immediately. If you used one of those and
    channels are still on placeholders, refresh the individual account.

1. Go to **Settings → M3U Accounts**.
2. Edit each M3U account and enter the real username and password. The
   restore report and the post-restore UI both name the exact account and
   fields that need it, for example an account named `Infinity` needing
   `username` and `profiles[0].custom_properties.user_info.password`.
3. Go to **Settings → EPG Sources**.
4. Edit each EPG source and re-enter the username and password (if applicable).
5. Refresh the M3U account (**Save & Refresh**). Confirm real streams
   populate. Channel-group selection survives the restore as-is (a
   round-trip drill measured this preserved exactly). If the account
   instead shows `No streams returned from Xtream Codes provider` with
   `0 / N` groups enabled, that specifically means no groups are enabled
   yet, not a provider outage; enable your groups and refresh again.
6. Check that your channels play. The completed refresh in step 5 is
   what reattaches them to the real streams.
7. Run an EPG refresh to populate guide data.

If you used an encrypted backup with **Include credentials**, none of this
step is needed: the credential round-trips automatically, and playback
works on the first restore (verified: credential fingerprint identical
before and after, playback succeeding immediately). Prefer the encrypted
path with credentials included for any migration you intend to bring
online right away.

---

## Step 7: Verify the new install

After the restore:

1. Go to **Channels** and confirm your channel list is present.
2. Check that channel groups, profiles, and stream assignments look correct.
3. Run an M3U refresh if streams are not populating.
4. Run an EPG refresh if guide data is absent.
5. **Test playback by fetching bytes, not by checking that a URL is set.**
   Request the stream directly and confirm both a 2xx status and real
   media bytes, bounded by a timeout so a hanging stream can't stall the
   check:

   ```
   GET http://<dispatcharr-host>:<port>/proxy/ts/stream/<channel-uuid>
       header: X-API-Key: <key>
   ```

   If you used an encrypted backup with **Include credentials**, expect
   this to pass on the first restore. If you used a standard (redacted)
   backup, expect it to pass only after you completed the full Step 6
   sequence: the credential re-entry plus the M3U refresh.

!!! success "EPG links survive a migration"
    A round-trip drill measured this directly on `0.18.1`: all 9
    seeded EPG links survived, on both artifact variants, with the
    restore's own `epg_links_unrestored` at `0` and no channels named in
    `epg_link_miss_details`.

    If you ever do see a channel lose its EPG link, the restore report
    names exactly which ones in `epg_link_miss_details`, and the
    post-restore UI surfaces the same list. Re-link those channels by
    hand, or re-run EPG auto-match; see
    [Match channels to EPG data](../epg/channel-to-epg-matching.md).

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

If your goal is ongoing DR (keeping a standby always in sync with your primary), consider [Cross-Instance Sync](cross-instance-sync.md) instead of, or in addition to, manual migration. Sync is not a backup and does not produce a restorable archive, but it keeps a second Dispatcharr instance continuously tracking the primary's configuration.

The recommended pattern for a DR setup:
1. Migrate the initial configuration to the standby via an encrypted backup restore (this article).
2. Configure cross-instance sync for ongoing replication after the initial migration.
