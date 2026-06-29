# Backup & Restore

> **Audience:** Operator setting up backups, migrating to a new install, or recovering from a failure.
>
> **Status:** Shipped in v0.18.0 (epic `0i2vt`, ADR-012). Cross-instance sync shipped in v0.18.1. UX label is **Backup & Restore** — the internal acronym DBAS only appears in dev docs and the threat model.

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

| Article | Purpose | Status |
|-|-|-|
| [`backup-overview.md`](backup-overview.md) | What a backup contains, what it does not contain (plugins excluded), credentials and passphrase encryption, retention model. | **Shipped — v0.18.0** |
| [`take-a-backup.md`](take-a-backup.md) | Manual and scheduled backup workflows, encrypted backup walkthrough. | **Shipped — v0.18.0** |
| [`verify-a-backup.md`](verify-a-backup.md) | Dry-run preview; what the preview report tells you; counts-only limit. | **Shipped — v0.18.0** |
| [`restore-a-backup.md`](restore-a-backup.md) | Full restore flow, category ordering, 4-tier stream matching, rollback/partial-state callout, user restore semantics. | **Shipped — v0.18.0** |
| [`configure-cloud-destinations.md`](configure-cloud-destinations.md) | Per-provider setup: S3/S3-compatible (shipped), WebDAV (shipped), Google Drive (shipped), OneDrive (deferred), Dropbox (deferred). | **Shipped — v0.18.0** |
| [`troubleshoot-restore.md`](troubleshoot-restore.md) | Failure modes, log patterns, rollback-incomplete recovery, logo misses. | **Shipped — v0.18.0** |
| [`migrate-to-a-new-install.md`](migrate-to-a-new-install.md) | End-to-end migration: backup on old install, install on new host, restore, verify. | **Shipped — v0.18.0** |
| [`cross-instance-sync.md`](cross-instance-sync.md) | One-way A→B config replication for DR standbys and multi-instance setups. | **Shipped — v0.18.1** |

---

## One thing you must know about encrypted backups

If you create an encrypted backup, **a lost passphrase makes the artifact permanently unrecoverable**. There is no reset, no recovery path, and no way to extract the plaintext without the passphrase. Store your passphrase in a password manager before creating an encrypted backup. This acknowledgement is required in the UI before an encrypted backup can be produced.

---

## Going deeper (for operators and security evaluators)

- [`docs/security/threat_model_dbas_import.md`](../../security/threat_model_dbas_import.md) — STRIDE analysis of the restore pipeline and cloud upload surface. Operators evaluating the trust boundary of a restore should read this.
- [`docs/adr/ADR-012-dbas-absorption-approach.md`](../../adr/ADR-012-dbas-absorption-approach.md) — the design decisions behind the backup/restore subsystem (D1–D12).
- [`docs/runbooks/disaster-recovery-restore.md`](../../runbooks/disaster-recovery-restore.md) — the SRE runbook for a full configuration restore under incident conditions.
- [`docs/api.md`](../../api.md#backup--restore) — HTTP API reference for the Backup & Restore endpoints.
- [`docs/database_migrations.md`](../../database_migrations.md) — the migration story for the underlying SQLite schema, relevant when restoring across ECM versions.
