# Cross-Instance Sync

> **Audience:** Operator who runs two ECM/Dispatcharr instances — a primary and a DR standby, or a local box and a remote box — and wants B to track A's configuration automatically.
>
> **Status:** Shipped in v0.18.1 (epic `i39wu`). UI path: **Settings → Backup & Restore → Cross-Instance Sync**.

---

## Two semantics you must understand before you start

**ONE-WAY.** Sync replicates from A to B on a schedule. B is a managed replica — edits you make directly on B are overwritten by A on the next sync cycle. Do not use B as a working instance if you intend sync to keep running.

**CREDENTIALS ARE NOT SYNCED.** B receives source and channel *definitions* (URLs, names, group structure, profiles) but not M3U/EPG passwords or other secrets. After the first sync, log into B and re-enter the credentials for each M3U account and EPG source. For migrating secrets to a fresh B all at once, use the [encrypted backup artifact](index.md) (the **Encrypted Backup** card), not sync — that is the only path that carries credentials.

---

## What sync is

Cross-instance sync is a recurring, automated one-way push of configuration from ECM-A (your primary instance) to a second Dispatcharr instance B (your replica). It is *not* a backup. It does not produce a restorable file. It does not merge changes bidirectionally. It continuously converges B toward A.

**Use sync when you want:**
- A DR/standby instance that stays current without manual effort.
- A second site whose lineup tracks your primary automatically.

**Do not use sync to:**
- Replace backups — sync does not capture a point-in-time snapshot you can restore from. Take regular encrypted backups as well.
- Carry credentials to B — they are redacted before transmission. Use an encrypted backup for the initial credential migration.
- Replicate user accounts — users are never synced (see [What never syncs](#what-syncs-vs-what-never-does)).

---

## What syncs vs. what never does

### Synced every cycle

| Category | Notes |
|-|-|
| M3U accounts | Source URL and settings. Credentials are stripped — re-enter on B. |
| EPG sources | Source URL and settings. Credentials are stripped — re-enter on B. |
| Channel groups | Group names and ordering. |
| Channel profiles | Profile definitions. |
| Stream profiles | Profile definitions. |
| Channels (+ embedded streams) | Channel names, numbers, groups, and their stream assignments. |

### Never synced

| Category | Why |
|-|-|
| **Users** | Continuous one-way push of `users` would overwrite B's privilege flags and could lock out B's operator. This exclusion is permanent and code-enforced — it cannot be configured away. |
| **Credentials** | M3U passwords, EPG passwords, API tokens. Redacted before transmission to avoid streaming live secrets on a recurring schedule. Migrate secrets via encrypted backup. |
| **Logos** | Excluded from the per-cycle slice in v0.18.1. Will be added in a later release once the cost of streaming logo assets per-interval is measured. |

---

## Setup walkthrough

### Step 1 — Add a sync target

1. Go to **Settings → Backup & Restore**.
2. Find the **Cross-Instance Sync** card.
3. Click **Add sync target**.
4. Fill in the fields:

| Field | What goes here |
|-|-|
| **Name** | A label for this target (e.g., `DR standby`, `remote site`). |
| **Base URL** | The base URL of Dispatcharr-B's API (e.g., `http://192.168.1.50:8080`). |
| **Username / Password** | Credentials ECM-A uses to authenticate against Dispatcharr-B's API. This is not what B syncs — it is how A reaches B. |
| **Allow insecure TLS** | Disable TLS verification for self-signed certs. Use only on isolated LANs where you control both endpoints. Every sync cycle using this option is logged. |

5. Click **Save**.

The target appears in the list. It is disabled by default.

### Step 2 — Run a preview

Before enabling the schedule, run a manual dry-run to confirm A can reach B and the configuration looks correct.

1. On the target row, click **Sync now**.
2. ECM runs a dry-run (no changes are written to B). The results show what would be created, updated, or skipped.
3. Review the preview. If you see unexpected conflicts, see [Troubleshooting](#troubleshooting).
4. Click **Apply** to write the changes to B. A confirmation dialog warns you that **A's configuration overwrites B** — confirm if you are ready.

After a successful apply, the target row shows the last sync timestamp and the outcome.

### Step 3 — Enable a sync schedule

1. On the target row, click **Edit**.
2. Set the **Sync interval** (e.g., every 1 hour).
3. Toggle **Enable** to on.
4. Save.

ECM now runs sync automatically at the configured interval. You can still trigger a manual sync at any time with **Sync now**.

### The kill switch

The **Enable** toggle on each target is the kill switch. Flipping it off immediately stops all scheduled runs for that target. The target definition (URL, credentials, interval) is preserved. Flip it back on to resume.

---

## DR standby setup (first-time flow)

If you are setting up B from scratch as a DR standby:

1. **Take an encrypted backup on A.** Use the **Encrypted Backup** card. This captures everything including credentials.
2. **Stand up a fresh Dispatcharr-B** on your standby host. Import the encrypted backup from A — this seeds B with A's full configuration, including credentials.
3. **Add a sync target on A** pointing at B (steps above). Run a preview-then-apply. Because B is already seeded from the backup, the first sync should be mostly skips.
4. **Enable the sync schedule on A.** B now tracks A.
5. **Verify B is healthy.** Log into B, check the channels and EPG, and test a stream if possible.

After the initial seeding via encrypted backup, sync keeps B current. You only need to re-enter credentials on B if a new M3U or EPG source is added on A — credentials for existing sources survive because B already has them from the backup.

---

## Conflicts

Sync uses a **source-wins** policy: when a configuration item exists on both A and B with matching identity, A's version is applied to B.

One case surfaces as a conflict rather than a silent overwrite: **a channel with no channel number that ambiguously matches a no-number channel on B** (same name, both with null channel numbers). ECM cannot safely determine whether these are the same channel, so it skips the item and surfaces a `CONFLICT` result in the sync report. Assign channel numbers on A to resolve the ambiguity, then re-sync.

---

## Troubleshooting

### Sync is failing or B appears to have drifted

Check whether the `ECMSyncStalledTargetDrift` alert has fired. This alert triggers when the sync task has not recorded a full success in approximately 3 hours (3 missed cycles on the hourly cadence). Follow the [Sync Target Stalled / Target Drift runbook](../../runbooks/sync-target-stalled-drift.md) for step-by-step diagnosis.

Common causes:
- **B is unreachable** — confirm connectivity to `base_url` from the ECM container.
- **Credentials rotated/revoked** — if B's API credentials changed after the sync schedule was set up, ECM aborts the run at the credential-freshness check. Edit the sync target, update the credentials, save, and trigger a manual sync.
- **Target disabled** — check the **Enable** toggle on the target. A disabled target never runs.
- **Partial-apply loop** — the sync runs but a category keeps failing on apply (not B unreachable, but a recurring mix/rollback). Pull the most recent sync report from the task history; identify the failing category.

### Conflict on a channel with no channel number

A channel on A has no channel number, and B already has a channel with the same name and also no number. ECM cannot safely decide these are the same channel. **Resolution:** assign a channel number to the channel on A, then re-sync.

### B has credentials for sources that A can't provide

Expected. Credentials are intentionally not synced. Log into B and re-enter them manually. Sync will not overwrite B's credentials — it only sends redacted (credential-stripped) definitions.

### The "Allow insecure TLS" warning

If you enabled **Allow insecure TLS** on a target, every sync cycle logs an audit row. You will see these in the journal. This is expected behavior — it is the record that TLS verification is being bypassed.

---

## Going deeper

- [ADR-013 — Cross-Instance Live Sync](../../adr/ADR-013-cross-instance-live-sync.md) — the architecture decision record; covers conflict policy, security controls, category decisions, and phasing.
- [Security threat model — Addendum D](../../security/threat_model_dbas_import.md#11-addendum-d-cross-instance-live-sync-v0181-one-way-ab-config-replication) — STRIDE analysis of the sync egress surface: why credentials are redacted, why users never sync, SSRF controls.
- [Runbook: ECMSyncStalledTargetDrift](../../runbooks/sync-target-stalled-drift.md) — step-by-step when the sync stalls alert fires.
