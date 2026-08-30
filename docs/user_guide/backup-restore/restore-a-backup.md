# Restore a Backup

> **Status:** Shipped in v0.18.0.

---

## Before you restore

**Read this section before you click anything.**

1. **Take a fresh backup first.** Before restoring an older artifact onto a running instance, take a fresh backup of the current state. This gives you a way back if the restore does not produce the result you expected.
2. **Run a dry-run preview.** See [Verify a backup](verify-a-backup.md). The dry-run shows you what will change without making any modifications. Review the counts before applying.
3. **Have your passphrase ready.** If the artifact is encrypted, you need the passphrase before the restore can proceed. There is no recovery path for a lost passphrase.

---

## The restore flow

### Step 1: Upload the artifact

1. Go to **Settings → Backup & Restore**.
2. Find the **Restore DBAS Backup** card.
3. Click **Upload artifact** and select your `.zip` backup file.

If the artifact is encrypted, ECM detects this automatically (by reading the file's 8-byte magic header) and shows a passphrase prompt. Enter the passphrase before proceeding.

ECM validates the artifact immediately on upload:

- Checks for decompression-bomb characteristics (before decompressing anything).
- Reads and validates `manifest.json`.
- Checks `schema_version`: if the artifact was produced by a newer ECM build, the upload is refused with "Unsupported backup version."
- Verifies each archive member's SHA-256 against the manifest.

If validation fails, ECM shows the error and makes no changes.

### Step 2: Run a dry-run preview (default)

The restore modal runs a **dry-run by default**. It does not apply anything unless you explicitly confirm. After uploading:

1. Click **Preview** (or the run button, which is in dry-run mode by default).
2. ECM runs the full importer pipeline in read-only mode against the artifact.
3. The report shows, for each category: **would create / would update / would skip** counts, with reasons for skips.
4. Logo misses are reported as an aggregate count. If logos cannot be matched, the report flags them here.

Review the report before applying. A large unexpected "would create" count on an existing instance is a signal to investigate before proceeding.

### Step 3: Apply

If the preview looks correct:

1. Click **Apply these changes**.
2. Confirm the dialog.
3. ECM runs the restore pipeline with live mutations. Live progress is shown for each of the 13 restore stages.

---

## Category restore order

The restore applies categories in a fixed order determined by their dependencies. You cannot change this order:

1. **M3U accounts**: must exist before channels, since channels reference their provider.
2. **EPG sources**: must exist before channels, since channels can reference EPG sources.
3. **Channel groups, channel profiles, stream profiles**: must exist before channels, since channels reference them.
4. **User agents, core settings**: applied alongside other config.
5. **Users**: opt-in; see [User restore semantics](#user-restore-semantics).
6. **Channels (with embedded streams)**: applied after all the entities they reference.
7. **Logos**: applied last; references channels and URL mappings.

This ordering is enforced by the restore orchestrator and cannot be reordered via the UI.

---

## How streams are matched to channels

When restoring channels, ECM must attach each channel's archived streams to streams that already exist on the destination Dispatcharr instance. The archive records which stream URLs were assigned to each channel, but stream IDs differ between instances. ECM uses a four-tier matching ladder to find the right stream on the destination:

**Tier 1: Exact URL match.** The archived stream URL matches a destination stream URL exactly (case-sensitive). This is the strongest signal: the same provider is serving the same endpoint. A Tier-1 match is never a false positive.

**Tier 2: Exact name + same provider.** Same display name (after normalization) and same M3U account on both instances. Covers the common case where a provider rotated its stream URLs (token refresh, CDN hostname change) but kept the channel lineup stable.

**Tier 3: Exact normalized name (any provider).** Same normalized display name, regardless of which M3U account or provider the stream comes from. Useful when restoring onto an instance whose M3U account IDs differ from the source.

**Tier 4: Fuzzy name match.** A fuzzy normalized name comparison with a similarity floor (≥60%). Catches minor name drift: quality-tag reordering (`HD` vs `FHD`), punctuation differences, minor wording changes. Used as a last resort before declaring a miss.

**If no tier matches (miss):** The stream is synthesized as a custom-stream entry so the channel is still created and visible. A WARN log entry is recorded and the restore-complete report includes an aggregate logo-miss count (for logos) and stream-miss details (for streams). Misses are a signal to check your M3U account configuration on the destination.

When multiple destination streams match the same tier, the one with the lowest stream ID wins. This tie-break is deterministic. The same inputs always produce the same result.

---

## User restore semantics

This category holds your **Dispatcharr** user accounts. ECM's own accounts are a different thing and are not restored here; see [What a standard backup does not carry](backup-overview.md#what-a-standard-backup-does-not-carry).

Users are **opt-in**. They are not selected by default in the restore modal. This is intentional: user accounts are a privilege surface, so the category is conservative in every direction.

When you opt in to restoring users:

- **The current admin is always preserved.** ECM identifies the account its Dispatcharr credentials authenticate as and skips it, so you cannot lock yourself out via a restore. The check is on the authenticated identity, not on a username from the archive, so a colliding name in the backup cannot get past it.
- **Existing users with matching usernames are skipped, not updated.** An account already on the destination is never overwritten.
- **No password travels with the backup.** Dispatcharr never returns a password or a hash, so there is nothing to carry: a restored account is created with a random password that ECM discards immediately and never records. The account is unusable until you set a password yourself, and the report flags it as needing that.
- **Restored accounts arrive without privileges.** Superuser, staff, and user-level flags in the backup are dropped, and every restored account is created non-privileged. Re-grant what you need by hand.
- **A user's channel-profile limits are re-pointed at the destination's own profiles.** Dispatcharr numbers channel profiles per instance, so the backup's numbering means nothing on the destination. Each limit is translated to the destination's matching profile rather than sent as-is, which used to attach the restored account to whichever profiles happened to hold those numbers. Two things follow if a profile in the backup is missing on the destination. If the account had other profiles that do exist, it is restored with only those, and the report names it so you can re-assign the rest. If **none** of its profiles exist, the account is **not restored at all** and the report says why: Dispatcharr treats a user with no channel profiles as having unrestricted access, so creating it would widen that account's reach rather than restore it. Restore the channel-profile category first, then re-run the user category.

---

## What a standard backup cannot restore, and how ECM tells you

A standard (non-encrypted) backup applies ECM's structured credential-redaction rules, so some things simply are not in it to restore. See [What a standard backup does not carry](backup-overview.md#what-a-standard-backup-does-not-carry) for the complete list and the free-text limitation. The two that need action from you after a restore:

- **Provider credentials.** Restored M3U accounts and EPG sources come back with the credential unset rather than wrong, username as well as password. The restore report names each account and field that needs re-entering. See [Step 6 of Migrate to a new install](migrate-to-a-new-install.md#step-6-re-enter-credentials-standard-backup-only).
- **ECM's own accounts, and the settings that hold credentials.** These live in `journal.db`, which only the **Restore Full Backup** path writes. The **Restore DBAS Backup** flow on this page does not write `journal.db` at all, so it never changes your ECM accounts in either direction.

### Notices after a Full Backup restore

When you restore a **Full Backup (legacy `.zip`)**, or restore from a backup during first-run setup, ECM shows a warning notice for anything the artifact could not carry. The restore-complete panel counts the files that landed; it cannot, by its nature, report what was never there. The notices fill that gap.

There are two, and both are read from your instance *after* the restore rather than guessed from the artifact, so they describe what actually happened:

| Notice | When you see it | What to do |
|-|-|-|
| **First-run setup required** | The instance ends up with no ECM account, which is the normal outcome of restoring a standard backup onto a fresh instance. | Create your admin account through first-run setup, then sign in. To migrate accounts between instances instead, take an encrypted backup with **Include credentials**. |
| **Re-establish configured surfaces** | Something you had configured before the restore is gone after it, because a standard backup omits credential stores and personal data. It names only what this instance actually lost: cloud storage targets, sync targets, M3U digest settings, or event-sync exclusions. | Re-create the named items. |

If you restore onto an instance that already has ECM accounts, those accounts are preserved across the restore and you will not see the first notice. The restore does not sign you out.

---

## Rollback and partial-state recovery

If a restore fails mid-run (a network error, a Dispatcharr API error, or a validation failure on a specific category), ECM runs a **compensating rollback**:

1. Every entity that was created during the current restore run is tracked in a durable on-disk ledger (written to `/config/dbas/restore_ledger_<id>.json`).
2. On failure, ECM issues delete requests for those entities in reverse creation order (logos first, then channels, then the groups and profiles they referenced, then M3U accounts and EPG sources). Reverse order ensures a parent entity is never deleted before the children that reference it are gone.
3. A delete that returns 404 (already gone) is counted as success. The rollback is idempotent.

The restore-complete screen reports one of three outcomes:

| Outcome | What happened |
|-|-|
| **Success** | All selected categories applied cleanly. No failures, no rollback needed. |
| **Restore failed, state rolled back** | One or more categories failed; the compensating rollback deleted all entities created in this run. The instance is back to its pre-restore state. |
| **Restore failed, rollback incomplete** | A failure occurred AND the rollback could not delete one or more entities (a non-404 error on a delete). The instance is in a partially modified state. The report shows which entities remain and their IDs so you can clean them up manually. |

**If you see "rollback incomplete":** Check the Dispatcharr API is reachable and that your admin credentials are valid. Look at the entity IDs listed in the report and delete them manually via the Dispatcharr UI. Then re-attempt the restore from scratch.

---

## Restoring from a saved local backup

If you want to restore a backup that is already stored locally (not re-uploading from your workstation):

1. Go to **Settings → Backup & Restore → Saved Backups**.
2. Find the backup file you want.
3. Click **Restore this backup** (or use the `restore_dbas_backup_saved` MCP tool with the filename).

This is the same restore flow as uploading. It runs through the same validation, dry-run, and apply pipeline. The saved file is not deleted by a restore.

On a brand-new install, the server-side backups directory (`/config/backups`) does not exist until ECM has written its first backup, so a freshly rebuilt instance shows an empty Saved Backups list. Restoring an artifact you are holding on your own machine doesn't depend on that directory. Use **Restore from artifact…** (upload) instead.

---

## Logo misses after restore

If logos could not be matched during restore, the restore-complete screen shows a **red banner** with the aggregate miss count. This is a warning, not a failure: channels are still created and functional, but their logos may be absent or using placeholder images.

Causes of logo misses:
- The backup was taken on an instance with locally uploaded logos that do not exist on the destination.
- The logo URL mapping points to a URL that is no longer reachable.

To resolve: re-upload the missing logos manually in **Settings → Channels**, or re-run a channel EPG logo match if the EPG carries logos.

---

## Next steps

- [Troubleshoot a restore](troubleshoot-restore.md): if the restore produced unexpected results.
- [Migrate to a new install](migrate-to-a-new-install.md): the full end-to-end migration walkthrough.
