# Backup & Restore: Overview

> **Status:** Shipped in v0.18.0 (epic `0i2vt`, ADR-012).

---

## What a backup is

A **Backup & Restore** backup is a single `.zip` artifact that captures your complete Dispatcharr + ECM configuration. It contains everything needed to bring a fresh Dispatcharr instance to the same operational state as the one that produced it, or to recover from accidental data loss on the same instance.

Backups are built by the `dbas_backup` task (scheduled or on-demand) and stored locally under `/config/backups/`. They can optionally be uploaded to off-host cloud storage for durability.

---

## What a backup contains (12 categories)

A backup covers the following configuration categories. All are included by default; a selective restore can opt individual categories out.

| Category | What is included |
|-|-|
| **M3U accounts** | Source URLs and account settings. Credentials are redacted by default (see [Credentials and passphrase encryption](#credentials-and-passphrase-encryption)). |
| **EPG sources** | Source URLs and refresh settings. Credentials are redacted by default. |
| **Channel groups** | Group names and structure. |
| **Channel profiles** | All channel profile definitions. |
| **Stream profiles** | All stream profile definitions. |
| **User agents** | Configured user-agent strings. |
| **Core settings** | ECM settings (`settings.json`). |
| **DVR rules** | Any configured DVR recording rules. |
| **Comskip config** | Comskip commercial-detection configuration. |
| **Users** | Dispatcharr user accounts (opt-in: see [User restore semantics](#user-restore-semantics)). |
| **Channels (with embedded streams)** | The full channel list, with their embedded stream assignments. |
| **Logos** | Logo image files from `/config/uploads/logos/`, plus their URL-mapping inventory. |

> **Plugins are not backed up.** Plugin state is excluded from v0.18.0 backups. This is a deliberate safety decision. Plugin restore semantics are not yet defined. If you rely on plugins, document your plugin configuration separately.

---

## What a backup does not contain

- **Live stream content**: a backup captures *definitions* (which streams are assigned to which channels), not the streams themselves.
- **The SQLite WAL file**: ECM checkpoints the write-ahead log before building the artifact, so `journal.db` in the archive is self-contained, but the WAL itself is not included.
- **Dispatcharr's own database**: ECM backs up the configuration it manages. Dispatcharr's internal database (viewer history, its own task state, etc.) is outside ECM's scope.
- **Credentials in a standard backup**: M3U/EPG passwords and similar secrets are replaced with a `REDACTED` placeholder. See [Credentials and passphrase encryption](#credentials-and-passphrase-encryption) for the migration path that does carry credentials.

---

## The artifact format

Each backup is a `.zip` file containing:

- `manifest.json`: a cleartext header with `schema_version`, `app_version`, creation timestamp, and a per-member SHA-256 hash list.
- `categories/<name>.yaml`: one YAML file per configuration category.
- `journal.db`: a scrubbed copy of the ECM SQLite database (alert-method credential fields redacted).
- `binary/logos/<file>`: per-image logo files, streamed one at a time.
- `binary/metadata.json`: logo inventory.
- `binary/url-mappings.json`: logo filename to source-URL map.

A `.sha256` sidecar file is written alongside the ZIP, containing the SHA-256 of the whole artifact. ECM verifies this hash before any restore begins.

### Schema version and forward compatibility

The `manifest.json` contains a `schema_version` integer (distinct from the ECM app version string). A restore that receives an artifact whose `schema_version` is newer than the running ECM build refuses with "Unsupported backup version" and does not attempt a partial restore of an incompatible artifact.

When restoring a backup produced by an older ECM onto a newer ECM, the schema version is accepted (older ≤ current = accepted). This means backups are forward-compatible: an artifact from ECM v0.18.0 can be restored onto a later ECM build.

---

## Credentials and passphrase encryption

By default, all backups are **redact-by-default**: credential fields (M3U passwords, EPG passwords, API keys, SMTP passwords, etc.) are replaced with a `REDACTED` sentinel. A restore from this artifact re-uses whatever credentials are already configured on the destination, or leaves the credential blank on a fresh install.

If you are migrating to a new install and want credentials to travel with the backup, use the **Encrypted Backup** option:

1. In **Settings → Backup & Restore**, open the **Encrypted Backup** card.
2. Check the **"I understand a lost passphrase makes this artifact permanently unrecoverable"** acknowledgement.
3. Set a passphrase of at least 12 characters. The passphrase is never stored, so keep it somewhere safe.
4. Enable **Include credentials** to carry M3U/EPG passwords and alert-method credentials alongside the encrypted artifact.

An encrypted backup uses scrypt (N=2¹⁵) for key derivation and ChaCha20-Poly1305 for authenticated encryption, applied as a chunked streaming pass over the whole artifact. The passphrase is never logged or stored.

> **Warning: lost passphrase = permanently unrecoverable artifact.** There is no recovery path. Store your passphrase in a password manager before taking an encrypted backup.

Encrypted backups are manual-only, because a passphrase is never persisted in the task schedule store.

---

## Retention model

ECM automatically prunes old local backups (and old off-host copies at each configured cloud destination) after each verified-successful backup run. The default policy is:

- **Keep the newest 7 backups**, regardless of age.
- **Additionally prune** any backup beyond the newest 7 that is older than 30 days.

The newest-N floor is always respected: even if a backup is older than 30 days, it is kept if it is within the newest 7. A failed or partial backup run never prunes anything. Retention only runs when a verified-successful new backup has been written.

---

## User restore semantics

Restoring user accounts is **opt-in**. Users are not selected by default in the restore modal. When you do restore users:

- The **current admin account** is never overwritten. ECM detects the currently authenticated admin and skips it during restore, so you cannot lock yourself out via a restore.
- A user account that already exists on the destination with the same username is updated (not duplicated).

See [Restore a backup](restore-a-backup.md) for the full restore flow.

---

## Recommended backup cadence

- **Daily scheduled backup**: sufficient for most operators. Configure a `dbas_backup` task schedule in **Settings → Scheduled Tasks**.
- **Before any major change**: take a manual backup before reconfiguring M3U sources, bulk-editing channels, or running a major Channel Pipeline rule change.
- **Before a restore**: always take a fresh backup of the current state before restoring an older artifact, so you can roll back if the restore does not produce the result you expected.

---

## Going deeper

- [Take a backup](take-a-backup.md): step-by-step backup workflow.
- [Verify a backup](verify-a-backup.md): dry-run preview before committing.
- [Restore a backup](restore-a-backup.md): full restore flow with safety semantics.
- [Configure cloud destinations](configure-cloud-destinations.md): off-host storage for durability.
- [Migrate to a new install](migrate-to-a-new-install.md): end-to-end migration walkthrough.
- [`docs/security/threat_model_dbas_import.md`](../../security/threat_model_dbas_import.md): security context for import and restore, for operators evaluating the trust boundary.
