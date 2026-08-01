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

Users are **opt-in**. They are not selected by default in the restore modal. This is intentional: restoring users onto a running instance can change login credentials and privilege flags for existing accounts.

When you opt in to restoring users:

- **The current admin is always preserved.** The account you are currently authenticated with is detected and skipped, so you cannot lock yourself out via a restore.
- Existing users with matching usernames are updated, not duplicated.
- Password hashes are restored if the destination supports the same hash algorithm. If there is a hash-algorithm mismatch, the user is reported as failed (with reason) rather than silently applied with a corrupted password.

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
