# Backup & Restore

The UX label for this feature is **Backup & Restore**. The internal acronym DBAS only appears in dev docs and the threat model. You will not see it in the ECM UI.

---

## Start here

| I want to… | Go to |
|-|-|
| Understand what a backup contains | [Backup & Restore overview](backup-overview.md) |
| Take a backup right now | [Take a backup](take-a-backup.md) |
| Confirm a backup is valid before I need it | [Verify a backup](verify-a-backup.md) |
| Restore from a backup | [Restore a backup](restore-a-backup.md) |
| Move to a new host | [Migrate to a new install](migrate-to-a-new-install.md) |
| Upload backups to S3, WebDAV, or Google Drive | [Configure cloud destinations](configure-cloud-destinations.md) |
| Diagnose a failed restore | [Troubleshoot a restore](troubleshoot-restore.md) |
| Keep a standby instance automatically in sync | [Cross-Instance Sync](cross-instance-sync.md) |

---

## Articles

| Article | Purpose |
|-|-|
| [Overview](backup-overview.md) | What a backup contains, what it does not contain (plugins excluded), credentials and passphrase encryption, retention model. |
| [Take a Backup](take-a-backup.md) | Manual and scheduled backup workflows, encrypted backup walkthrough. |
| [Verify a Backup](verify-a-backup.md) | Dry-run preview; what the preview report tells you; counts-only limit. |
| [Restore a Backup](restore-a-backup.md) | Full restore flow, category ordering, 4-tier stream matching, rollback/partial-state callout, user restore semantics. |
| [Configure Cloud Destinations](configure-cloud-destinations.md) | Per-provider setup: S3/S3-compatible (shipped), WebDAV (shipped), Google Drive (shipped), OneDrive (deferred), Dropbox (deferred). |
| [Troubleshoot a Restore](troubleshoot-restore.md) | Failure modes, log patterns, rollback-incomplete recovery, logo misses. |
| [Migrate to a New Install](migrate-to-a-new-install.md) | End-to-end migration: backup on old install, install on new host, restore, verify. |
| [Cross-Instance Sync](cross-instance-sync.md) | One-way A→B config replication for DR standbys and multi-instance setups. |

---

## One thing you must know about encrypted backups

If you create an encrypted backup, **a lost passphrase makes the artifact permanently unrecoverable**. There is no reset, no recovery path, and no way to extract the plaintext without the passphrase. Store your passphrase in a password manager before creating an encrypted backup. This acknowledgement is required in the UI before an encrypted backup can be produced.

---

## Going deeper (for operators and security evaluators)

- [`docs/security/threat_model_dbas_import.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/security/threat_model_dbas_import.md): STRIDE analysis of the restore pipeline and cloud upload surface. Operators evaluating the trust boundary of a restore should read this.
- [`docs/runbooks/disaster-recovery-restore.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/disaster-recovery-restore.md): the SRE runbook for a full configuration restore under incident conditions.
- [`docs/api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md#backup-restore): HTTP API reference for the Backup & Restore endpoints.
- [`docs/database_migrations.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/database_migrations.md): the migration story for the underlying SQLite schema, relevant when restoring across ECM versions.
