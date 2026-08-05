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
    Measured against ECM `0.18.1-0023` / Dispatcharr `0.28.2`. A restored
    lineup now genuinely **plays**, confirmed by fetching real media bytes,
    not just checking a URL is set: a substantial improvement over the
    prior pin (`0.18.1-0022`), where nothing played at all. But two things
    still need your attention on every migration:

    - **If your source instance has any logo uploaded through ECM's own
      Logo Manager, the restore will abort and roll back entirely**
      (`enhancedchannelmanager-d0agi`), because the backup does not
      currently archive those logo bytes even though Dispatcharr serves
      them on request (`enhancedchannelmanager-xb58a`). Check for
      ECM-uploaded logos before you rely on this procedure. See
      [Before you begin](#before-you-begin-check-for-ecm-uploaded-logos)
      below.
    - **A standard (redacted) backup needs a specific, counter-intuitive
      recovery sequence** before playback works: re-enter credentials,
      refresh, and **run the restore again** (a refresh alone is not
      enough). See Step 6.

    EPG links are also still lost on every migration and need re-linking by
    hand (Step 7). See [Run a restore drill](run-a-restore-drill.md) for
    the full findings, including the exact version pin this was measured
    on.

---

## Before you begin: check for ECM-uploaded logos

!!! danger "P0: the backup does not archive uploaded logo bytes, and the resulting miss aborts the migration"
    On `0.18.1-0023`, if the old install has **any** logo uploaded through
    ECM's own Logo Manager, the restore on the new install will **abort
    and roll back the entire migration**, not just the logo category
    (`enhancedchannelmanager-d0agi`).

    A logo uploaded through ECM's Logo Manager is written to
    **Dispatcharr's** storage, not ECM's own upload directory. Those bytes
    are fully retrievable at backup time, over Dispatcharr's own logo
    cache endpoint, using the same API key ECM already holds for every
    other backup category, but the backup does not currently request
    them (`enhancedchannelmanager-xb58a`, P0). Because the backup never
    captured the bytes, the restore correctly detects the miss on the new
    install, and currently treats a logo failure as **fatal**: everything
    else that had already migrated successfully, including channels,
    streams, accounts, profile, users, and other logos, is deleted again
    by the compensating rollback (`enhancedchannelmanager-d0agi`).

    Logos referenced by a remote http(s) URL are unaffected and
    round-trip correctly; this gap is specific to logos uploaded through
    ECM's own Logo Manager.

    **The only way to get the migration restore to complete today is to
    remove ECM-uploaded logo records on the old install before taking the
    backup you intend to migrate from, and re-add them manually on the
    new install afterward.** That means those logos are not in the backup
    at all. Before you start this migration, check the old install for
    any logo that was uploaded through ECM's Logo Manager (as opposed to
    auto-assigned from an M3U or EPG feed). Neither
    `enhancedchannelmanager-xb58a` nor `enhancedchannelmanager-d0agi` is
    shipped; re-check both beads' status before you rely on this section.

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

- **Logo bytes now restore correctly.** A round-trip drill measured 10
  of 11 logos sha256-identical to source (`enhancedchannelmanager-dfkbn`,
  the logo-loss part of this defect is fixed). **But this only matters if
  the restore completes at all**: see
  [Before you begin](#before-you-begin-check-for-ecm-uploaded-logos).
  Any ECM-uploaded logo on the old install currently aborts the whole
  restore before logos are even reached
  (`enhancedchannelmanager-d0agi`). Don't trust the dry-run preview's
  logo numbers either: the preview reports every URL-restorable logo as
  failed even when it will restore fine on apply
  (`enhancedchannelmanager-dgnms`). Verify logos on the new install
  directly rather than trusting either the preview or the report's logo
  count.

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

!!! danger "A refresh alone does not finish this. Read the whole sequence before you start."
    This is the single most counter-intuitive fact about a redacted
    restore, and skipping the last step is why playback looks broken
    afterward even when everything else worked:

    1. Re-enter the credential (steps 1–4 below).
    2. Refresh the M3U account.
    3. **Run the restore again, from the same artifact.**

    Step 2 alone is not enough. ECM's placeholder-rebind pass (the step
    that reattaches restored channels to real streams) runs once,
    immediately after the restore's own deferred M3U refresh. On a
    redacted artifact there is nothing to match against at that instant
    (no credential yet), and the rebind pass never re-runs on its own. A
    later manual refresh adds the real streams *beside* the placeholders
    without rebinding anything to them. Re-running the restore is what
    triggers the rebind pass again, this time with real streams present
    to match against.

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
6. **Go back to Settings → Backup & Restore → Restore DBAS Backup and run
   the same restore again**, from the same artifact. This is the step
   that actually reattaches your channels to the now-real streams. See
   the callout above; do not skip it.
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

   If you used an encrypted backup with **Include credentials** and had no
   ECM-uploaded logos, expect this to pass on the first restore. If you
   used a standard (redacted) backup, expect it to pass only after you
   completed the **full** Step 6 sequence, including running the restore
   a second time.

!!! danger "EPG links are still lost, and still need manual re-linking"
    Every channel that had an EPG link on the old install loses it on the
    new one. This is a confirmed, still-open residual defect, not a
    seeding artifact (`enhancedchannelmanager-dfkbn`). Root cause: the
    restore relinks by the channel's archived `tvg_id`, but ECM's own
    channel rows carry `epg_data_id` with `tvg_id: None`, so there is
    nothing for the restore to match against. Setting `epg_data_id`
    through ECM's own API has the same effect; this is not specific to
    the restore path.

    The restore report now names exactly which channels lost their EPG
    link (`epg_link_miss_details`), and the post-restore UI surfaces the
    same list. Re-link those channels by hand, or re-run EPG auto-match;
    see [Match channels to EPG data](../epg/channel-to-epg-matching.md).

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
