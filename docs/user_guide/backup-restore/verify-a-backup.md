# Verify a Backup

> **Status:** Shipped in v0.18.0.

---

## Why verify before you restore

A restore is a one-way door: it writes configuration changes to a live Dispatcharr instance. Running a dry-run first tells you exactly what a restore *would* do (how many channels, M3U accounts, EPG sources, and other entities would be created, updated, or skipped) without making any changes. If the preview looks wrong, you can stop before any modification occurs.

Verification is also the right way to confirm that a backup you took last week (or last month) is still usable.

---

## What verification checks

When you upload a backup artifact for verification, ECM performs the following checks **before any mutation**:

1. **Decompression-bomb guard**: the artifact is inspected at header level (not decompressed) and rejected if it contains too many entries, a suspicious compression ratio, or an excessive cumulative declared uncompressed size.
2. **Manifest presence and integrity**: the `manifest.json` header must be present and parseable.
3. **Schema version gate**: the artifact's `schema_version` is compared to the running ECM build. An artifact produced by a newer ECM build is refused with "Unsupported backup version" before any further processing.
4. **Per-member SHA-256**: each file listed in `manifest.json` is verified against its recorded hash. A corrupted or tampered member is refused.

If any check fails, ECM returns a descriptive error and makes no changes.

---

## Running a dry-run preview

A dry-run is **on by default** in the restore modal. You do not need to change any settings. If you upload an artifact and click the run button, you get a preview, not an apply.

1. Go to **Settings → Backup & Restore**.
2. Find the **Restore DBAS Backup** card.
3. Upload your `.zip` backup artifact. (If the artifact is encrypted, ECM detects the encryption magic automatically and prompts for the passphrase before proceeding.)
4. ECM validates the artifact (schema version, integrity) immediately on upload.
5. Click **Preview** (or the equivalent run button, which is in dry-run mode by default).
6. ECM runs a counts-only preview and returns a report showing, for each category, four columns: **WILL CREATE** (entities in the archive that do not exist on the destination), **WILL UPDATE** (entities that exist but differ), **WILL SKIP** (entities that already exist identically, or that are excluded), and **FAILED** (entities that could not be processed).

### Reading the dry-run report

The preview report is counts-only. It shows one row per category, with the WILL CREATE / WILL UPDATE / WILL SKIP / FAILED counts for that category, but not a full diff for each entity. A full per-entity diff view is planned for a future release.

The number of category rows shown depends on what the backup and destination actually contain. On a verification run against a fresh instance, a dry run rendered eight category rows: M3U accounts, EPG sources, Channel groups, Channel profiles, Stream profiles, User agents, Settings, and Users. No Channels or Logos rows appeared in that run. If a Channels row and a Logos row are present in your own report, they're worth particular attention:

- **Channels**, if a Channels row is present: a large WILL CREATE count on an existing instance may indicate the backup is from a different source, or that the categories (M3U accounts, channel groups) those channels reference have not been restored yet.
- **Users**: users are opt-in. If users are not selected, the user category shows "skipped (excluded by operator)."
- **Logos**, if a Logos row is present: logo misses (logos in the archive that could not be matched to an existing file) are reported separately as an aggregate count. A non-zero logo-miss count means some logos will be absent after restore; the restore-complete screen shows a prominent warning banner if any logos miss.

### What the preview does not check

- **Whether the Dispatcharr instance is reachable**: the dry-run exercises the importer logic against the archived data, but a subsequent apply will need to reach the Dispatcharr API. Test reachability separately if you are on a new install.
- **Credential validity**: credentials are redacted in a standard backup, so the preview cannot verify that M3U/EPG credentials will work on the destination.

---

## Counts-only limit

The v0.18.0 dry-run engine reports **counts only** per entity per action (WILL CREATE / WILL UPDATE / WILL SKIP / FAILED). It does not produce a full entity-level diff listing every individual channel or stream. This is a deliberate scope choice: the count answers the safety question ("am I about to make a large unexpected change?") at low cost. A full diff view is planned for v0.19.x.

---

## After a successful preview

If the preview looks correct (the category counts match your expectations and there are no unexpected "would create" surprises), you can proceed to apply:

1. In the restore modal, review the dry-run report.
2. Click **Apply these changes** (or the equivalent apply button).
3. ECM shows a confirmation dialog before executing. Confirm to proceed.

See [Restore a backup](restore-a-backup.md) for the full apply flow, including the ordering of categories and what to do if the restore reports failures.

---

## Verifying a scheduled backup proactively

To confirm that the backup produced last night is healthy without waiting for a failure:

1. Go to **Settings → Backup & Restore → Saved Backups**.
2. Download the most recent `.zip`.
3. Upload it in the **Restore DBAS Backup** card and run a dry-run preview (do not apply).
4. Confirm the report looks reasonable.

Do this before migrating to a new install. A few minutes of verification now prevents a failed restore at the moment you need it most.
