# Backup & Restore

> **Audience:** Operator setting up backups, migrating to a new install, or recovering from a failure.
>
> **Status:** Placeholder. The first user-facing import workflow ships with bd-gb5r5.3 (DBAS import). The full Backup & Restore operator surface is the v0.18.0 epic (bd-0i2vt). UX has confirmed the in-UI label is **Backup & Restore** — the internal acronym DBAS only appears in dev docs and the threat model.

## Section purpose (planned)

Cover everything an operator needs to:

1. Take a backup of their ECM configuration on a regular cadence.
2. Verify a backup is valid before they need it.
3. Restore a backup to a fresh install or recover from accidental data loss.
4. Understand the safety semantics of import (what is overwritten, what is merged, what is rejected).

This section is unusually high-stakes — restore is a one-way door for the configuration that ends up in place — so the articles will lean heavily on dry-run workflows, conflict resolution semantics, and recovery patterns.

## Intended audience

- **Operator** setting up routine backups (read once, configure, forget until needed).
- **Operator** in the middle of a recovery (read under pressure — clarity matters).
- **Operator** migrating ECM to new hardware or a new container.

End users do not read this section, but they care intensely about the outcome (their channels still working after a restore).

## Planned articles

| Article | Purpose |
|-|-|
| `backup-overview.md` | What a backup contains, what it does **not** contain (e.g., the SQLite journal vs. config), recommended backup frequency. |
| `take-a-backup.md` | The Backup & Restore tab — exporting a backup, where the file lives, naming conventions. |
| `verify-a-backup.md` | The dry-run workflow before restoring, what the dry-run output tells you. |
| `restore-a-backup.md` | The actual restore flow, conflict resolution semantics, category ordering, what to expect post-restore. **Step-by-step, written for the operator under pressure.** |
| `migrate-to-a-new-install.md` | End-to-end migration: backup on old install, install on new host, restore, verify. |
| `import-from-elsewhere.md` | Importing configuration that didn't come from an ECM backup (DBAS import; see threat model for security context). |
| `troubleshoot-restore.md` | "The restore reported conflicts" / "the restore appeared to succeed but my channels are different" — diagnostic patterns. |

## Going deeper (for now)

- [`docs/security/threat_model_dbas_import.md`](../../security/threat_model_dbas_import.md) — security context for the import flow. Operators evaluating restore safety should be aware this exists.
- [`docs/database_migrations.md`](../../database_migrations.md) — the migration story for the underlying SQLite schema, relevant when restoring across versions.

## Tracking

- bd-gb5r5.3 — *DBAS import / restore* — the first user-facing article in this section. **Blocked by this scaffolding bead (bd-f1wnt).**
- bd-0i2vt — *Backup & Restore epic* — fills in the rest of the section in v0.18.0.
