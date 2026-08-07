# Migrate to a New Install

> **Status:** Shipped in v0.18.0.

---

## Overview

A migration is a backup on the old install, followed by a restore on the new install. Because the backup captures the full Dispatcharr configuration (M3U accounts, EPG sources, channels, groups, profiles, logos, and ECM settings), a restore on a fresh Dispatcharr instance brings it to the same operational state as the source.

This walkthrough assumes:
- The old install is still running (or was running long enough to take a backup).
- You have a new host ready with ECM and Dispatcharr installed but not yet configured.
- You want to carry your M3U/EPG credentials (if so, you need the encrypted backup path).

!!! danger "Read this before you migrate"
    Written for ECM `0.18.1-0040` / Dispatcharr `0.28.2`. A restored
    lineup genuinely **plays**, confirmed by fetching real media bytes,
    not just checking a URL is set. One thing still needs your attention
    on every migration:

    - **A standard (redacted) backup needs a recovery sequence** before
      playback works: re-enter credentials, then refresh the M3U account.
      As of `0.18.1-0033` those two steps are the whole recovery; on
      earlier builds a third step (re-running the restore) was also
      required. See Step 6.

    EPG links round-trip correctly as of the round-trip drill's most
    recent measurement (9 of 9 seeded links survived on `0.18.1-0035`,
    on both artifact variants); this article previously said the
    opposite. See Step 7 and [Run a restore drill](run-a-restore-drill.md)
    for the full findings, including the exact version pin each claim was
    measured on.

---

## ECM-uploaded logos and this migration

!!! success "Uploaded logos are included in the backup and restore intact"
    As of `0.18.1-0024`, a logo uploaded through ECM's own Logo Manager
    has its image bytes archived in the backup and restores intact on the
    new install. No manual steps are needed for these logos.

    Logos assigned from a remote http(s) URL (auto-assigned from an M3U
    or EPG feed) were always handled differently: they are not stored in
    the artifact, and restore by re-fetching the same URL on the new
    install.

    If a logo still fails to restore for some other reason, that failure
    is counted and named in the restore report. It no longer aborts or
    rolls back the rest of the migration.

    **On builds before `0.18.1-0024`:** none of this applied. The backup
    did not archive uploaded-logo bytes at all, and a logo miss aborted
    and rolled back the entire migration. If you are migrating from an
    install running an older build, upgrade to `0.18.1-0024` or later
    before you rely on this section.

---

## Step 1: Take a backup on the old install

Take a **manual encrypted backup** so credentials travel with the artifact. If you are OK re-entering credentials on the new install, a standard (unencrypted) backup is sufficient.

!!! warning "Take the standard backup first, if you want one at all"
    Creating an encrypted backup leaves the `DBAS Backup` task contaminated:
    every later run of that task on this install, including the standard
    **Run Now** path and every unattended scheduled run, produces an
    **encrypted, credential-bearing artifact** instead of the default
    redacted one, until ECM's container restarts
    (`enhancedchannelmanager-cytzj`). If you want both a standard artifact
    for your records and an encrypted one for the migration, take the
    standard backup **before** you create any encrypted backup.

### Standard backup (if re-entering credentials is acceptable)

The Backup & Restore page does not have a **Back Up Now** button. Use the Scheduled Tasks path:

1. On the old install, go to **Settings → Scheduled Tasks**.
2. Find the **DBAS Backup** task card.
3. Click **Run Now**.
4. Wait for the completion notification, then go to **Settings → Backup & Restore → Saved Backups** and download the `.zip` to your workstation.

See [Take a backup](take-a-backup.md#option-a-manual-backup-on-demand) for the full on-demand backup walkthrough.

### Encrypted backup (recommended for migration)

1. On the old install, go to **Settings → Backup & Restore → Encrypted Backup**.
2. Check the acknowledgement: *"I understand a lost passphrase makes this artifact permanently unrecoverable."*
3. Enter a passphrase of at least 12 characters. **Write it down in a password manager right now.** You need this passphrase on the new install. There is no recovery path if you lose it.
4. Enable **Include credentials** to include M3U/EPG passwords, SMTP passwords, and alert-method credentials in the artifact.
5. Click **Create encrypted backup**.

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

!!! success "You no longer need to match the old admin's username"
    As of `0.18.1-0023`, the new Dispatcharr's superuser can have a
    **different** username than the old install's admin.
    `enhancedchannelmanager-y65si` (the defect that used to abort and
    roll back the entire restore at the `user` category on a username
    mismatch) is fixed and closed. Name the new install's superuser
    whatever you want at Dispatcharr's own first-run wizard; you no
    longer have to reproduce the old install's admin username to avoid a
    failed migration.

- **Logo bytes now restore correctly**, including logos uploaded through
  ECM's own Logo Manager. A round-trip drill measured 10 of 11 logos
  sha256-identical to source (`enhancedchannelmanager-dfkbn`, the
  logo-loss part of this defect is fixed). See
  [ECM-uploaded logos and this migration](#ecm-uploaded-logos-and-this-migration)
  for what's archived and what isn't. On builds before `0.18.1-0032` the
  dry-run preview also reported every URL-restorable logo as failed even
  when it would restore fine on apply (`enhancedchannelmanager-dgnms`);
  as of `0.18.1-0032` the preview's logo counts match what the apply
  does. Either way, verify logos on the new install directly rather than
  trusting a count.

---

## Step 6: Re-enter credentials (standard backup only)

If you used a standard (unencrypted) backup, M3U account passwords, EPG passwords, and similar credentials were redacted. On the new install:

!!! success "The password field is honestly empty now"
    After a redacted restore, the M3U account's password field is
    correctly **empty**. The old bug, where the field showed the literal
    string `***REDACTED***` and presented as configured when it wasn't,
    is fixed and closed (`enhancedchannelmanager-6pilh`). An empty field
    was always the honest state to show; it still needs the real
    credential entered before the account will authenticate, same as
    before.

!!! success "Two steps, as of `0.18.1-0033`: re-enter the credential, then refresh"
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

    **On builds before `0.18.1-0033`** there was a mandatory third step:
    **run the restore again, from the same artifact**. The reattach pass
    ran only once, during the restore itself, and never re-ran on its own,
    so a later refresh added the real streams *beside* the placeholders
    and rebound nothing. If you are migrating onto an older build, do that
    third step after the refresh.

1. Go to **Settings → M3U Accounts**.
2. Edit each M3U account and enter the real password. The restore report
   and the post-restore UI both name the exact account and field that
   needs it, for example an account named `Infinity` needing
   `profiles[0].custom_properties.user_info.password`.
3. Go to **Settings → EPG Sources**.
4. Edit each EPG source and re-enter the password (if applicable).
5. Refresh the M3U account (**Save & Refresh**). Confirm real streams
   populate. Channel-group selection now survives the restore as-is
   (`enhancedchannelmanager-dfkbn`: a round-trip drill measured this
   preserved exactly, unlike an earlier pin where it always reverted to
   zero enabled groups). If the account instead shows `No streams
   returned from Xtream Codes provider` with `0 / N` groups enabled, that
   specifically means no groups are enabled yet, not a provider outage;
   enable your groups and refresh again.
6. Check that your channels now play. The completed refresh in step 5 is
   what reattaches them to the real streams. On a build before
   `0.18.1-0033`, go back to **Settings → Backup & Restore → Restore DBAS
   Backup** and run the same restore again from the same artifact; see
   the callout above.
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
   backup, expect it to pass only after you completed the **full** Step 6
   sequence: on `0.18.1-0033` and later, that is the credential re-entry
   plus the M3U refresh; on an earlier build it also includes running the
   restore a second time.

!!! success "EPG links survive a migration"
    A round-trip drill measured this directly on `0.18.1-0035`: all 9
    seeded EPG links survived, on both artifact variants, with the
    restore's own `epg_links_unrestored` at `0` and no channels named in
    `epg_link_miss_details` (`enhancedchannelmanager-dfkbn`). This
    article previously said every linked channel loses its EPG link on
    every migration; that was wrong for this build.

    If you ever do see a channel lose its EPG link, the restore report
    names exactly which ones in `epg_link_miss_details`, and the
    post-restore UI surfaces the same list. Re-link those channels by
    hand, or re-run EPG auto-match; see
    [Match channels to EPG data](../epg/channel-to-epg-matching.md).

    See [Run a restore drill](run-a-restore-drill.md) for the full
    accounting of what a clean round trip does and does not currently
    reproduce.

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
