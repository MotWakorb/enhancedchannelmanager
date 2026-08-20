# Cross-Instance Sync

UI path: **Settings → Backup & Restore → Cross-Instance Sync**.

---

## Two semantics you must understand before you start

**ONE-WAY.** Sync replicates from A to B on a schedule. B is a managed replica. Edits you make directly on B are overwritten by A on the next sync cycle. Do not use B as a working instance if you intend sync to keep running.

**CREDENTIALS ARE NOT SYNCED.** B receives source and channel *definitions* (URLs, names, group structure, profiles) but not M3U/EPG passwords or other secrets. After the first sync, log into B and re-enter the credentials for each M3U account and EPG source. For migrating secrets to a fresh B all at once, use the [encrypted backup artifact](index.md) (the **Encrypted Backup** card), not sync. That is the only path that carries credentials.

---

## What sync is

Cross-instance sync is a recurring, automated one-way push of configuration from ECM-A (your primary instance) to a second Dispatcharr instance B (your replica). It is *not* a backup. It does not produce a restorable file. It does not merge changes bidirectionally. It continuously converges B toward A.

**Use sync when you want:**
- A DR/standby instance that stays current without manual effort.
- A second site whose lineup tracks your primary automatically.

**Do not use sync to:**
- Replace backups: sync does not capture a point-in-time snapshot you can restore from. Take regular encrypted backups as well.
- Carry credentials to B: they are redacted before transmission. Use an encrypted backup for the initial credential migration.
- Replicate user accounts: users are never synced (see [What never syncs](#what-syncs-vs-what-never-does)).

---

## What syncs vs. what never does

### Synced every cycle

| Category | Notes |
|-|-|
| M3U accounts | Source URL and settings. Credentials are stripped; re-enter on B. **If the credentials are embedded in the URL itself** — a plain-M3U playlist URL of the form `…/get.php?username=…&password=…` — then the address cannot be separated from the secret, so the whole URL is left blank on B and you re-enter the URL rather than just a password. An **Xtream Codes** account is not affected here: Dispatcharr keeps its server URL and its credentials in separate fields, so the URL crosses and only the password needs re-entering. Either way the run summary names the account. |
| EPG sources | Source URL and settings. Credentials are stripped; re-enter on B. The same URL-embedded-credentials case applies, and it is the common one: an Xtream Codes guide URL (`xmltv.php?username=…&password=…`) arrives on B blank, the source shows **No URL provided**, and until you re-enter it every channel that guide feeds has no programme data on B. |
| Channel groups | Group names and ordering. |
| Channel profiles | Profile definitions. |
| User agents | The custom user-agent strings an M3U account fetches with and a stream profile plays through. Synced first, before both, so each one's user-agent link is re-pointed at B's copy. Distinct from user *accounts*, which are never synced. |
| Stream profiles | Profile definitions, including their user-agent link. |
| Channels (+ embedded streams) | Channel names, numbers, groups, and their stream assignments. |

### Opt-in per target

| Category | Notes |
|-|-|
| Logos | Off by default. Enable a target's `sync_logos` flag (API/MCP) to replicate A's logos each cycle. Covers both sources: the files in ECM's own `/config/uploads/logos/` and the logos Dispatcharr hosts (where a logo you upload through Logo Manager actually lives). Where both describe the same logo, Dispatcharr's copy is the one that travels. Only logos B is missing are uploaded (matched by id, name, then filename), fetched and uploaded one image at a time; sync never deletes or bulk-clears B's existing logos. Because sync runs unattended, the image fetching is time-bounded per image and per cycle — a very large logo set that runs out of budget is reported as missed logos for that cycle and picked up on the next one. |

### Never synced

| Category | Why |
|-|-|
| **Users** | Continuous one-way push of `users` would overwrite B's privilege flags and could lock out B's operator. This exclusion is permanent and code-enforced. It cannot be configured away. |
| **Credentials** | M3U passwords, EPG passwords, API tokens. Redacted before transmission to avoid streaming live secrets on a recurring schedule. Migrate secrets via encrypted backup. |
| **Server groups** | Dispatcharr's server groups — the grouping that makes several M3U accounts share one provider's connection limit. ECM has no server-group category, so a group cannot be created on B and an account's assignment cannot be re-pointed at one. An account that belongs to a server group on A is created on B without one; the sync report names the account so you can re-assign it on B. Create the server group on B yourself if the accounts sharing it need a shared connection limit. |

---

## Setup walkthrough

### Step 1: Add a sync target

1. Go to **Settings → Backup & Restore**.
2. Find the **Cross-Instance Sync** card.
3. Click **Add sync target**.
4. Fill in the fields:

| Field | What goes here |
|-|-|
| **Name** | A label for this target (e.g., `DR standby`, `remote site`). |
| **Base URL** | The base URL of Dispatcharr-B's API (e.g., `http://192.168.1.50:8080`). |
| **Username / Password** | Credentials ECM-A uses to authenticate against Dispatcharr-B's API. This is not what B syncs. It is how A reaches B. |
| **Allow insecure TLS** | Disable TLS verification for self-signed certs. Use only on isolated LANs where you control both endpoints. Every sync cycle using this option is logged. |

5. Click **Save**.

The target appears in the list. It is disabled by default.

### Step 2: Run a preview

Before enabling the schedule, run a manual dry-run to confirm A can reach B and the configuration looks correct.

1. On the target row, click **Sync now**.
2. ECM runs a dry-run (no changes are written to B). The results show what would be created, updated, or skipped.
3. Review the preview. If you see unexpected conflicts, see [Troubleshooting](#troubleshooting).
4. Click **Apply** to write the changes to B. A confirmation dialog warns you that **A's configuration overwrites B**. Confirm if you are ready.

After a successful apply, the target row shows the last sync timestamp and the outcome.

### Step 3: Enable a sync schedule

1. On the target row, click **Edit**.
2. Set the **Sync interval** (e.g., every 1 hour).
3. Toggle **Enable** to on.
4. Save.

ECM now runs sync automatically at the configured interval. You can still trigger a manual sync at any time with **Sync now**.

If you have several sync targets, they sync independently and can run at the same time. A slow or unreachable target never delays the others. ECM never runs two syncs against the *same* target at once (a second attempt while one is in progress is refused and simply runs on its next interval), and it caps how many targets sync simultaneously (3 by default; the `ECM_SYNC_MAX_CONCURRENT` environment variable adjusts it, and extra targets wait their turn rather than being skipped).

### The kill switch

The **Enable** toggle on each target is the kill switch. Flipping it off immediately stops all scheduled runs for that target. The target definition (URL, credentials, interval) is preserved. Flip it back on to resume.

---

## DR standby setup (first-time flow)

If you are setting up B from scratch as a DR standby:

1. **Take an encrypted backup on A.** Use the **Encrypted Backup** card. This captures everything including credentials.
2. **Stand up a fresh Dispatcharr-B** on your standby host. Import the encrypted backup from A. This seeds B with A's full configuration, including credentials.
3. **Add a sync target on A** pointing at B (steps above). Run a preview-then-apply. Because B is already seeded from the backup, the first sync should be mostly skips.
4. **Enable the sync schedule on A.** B now tracks A.
5. **Verify B is healthy.** Log into B, check the channels and EPG, and test a stream if possible.

After the initial seeding via encrypted backup, sync keeps B current. You only need to re-enter credentials on B if a new M3U or EPG source is added on A. Credentials for existing sources survive because B already has them from the backup.

---

## Conflicts

Sync uses a **source-wins** policy: when a configuration item exists on both A and B with matching identity, A's version is applied to B.

One case surfaces as a conflict rather than a silent overwrite: **a channel with no channel number that ambiguously matches a no-number channel on B** (same name, both with null channel numbers). ECM cannot safely determine whether these are the same channel, so it skips the item and surfaces a `CONFLICT` result in the sync report. Assign channel numbers on A to resolve the ambiguity, then re-sync.

**What "overwritten by A" means in practice** (live-validated): sync converges by *recreate*, not by pruning. If you **delete** an item on B, the next cycle recreates it from A (a deleted channel comes back with its streams re-attached). If you **rename** an item on B, the next cycle recreates A's version alongside it. The renamed copy is now a B-local extra that sync will **not** delete (sync never deletes anything on B). Clean up B-local extras by hand if they matter to you, or avoid editing B directly.

---

## Troubleshooting

### Sync is failing or B appears to have drifted

Check whether the `ECMSyncStalledTargetDrift` alert has fired. This alert triggers when the sync task has not recorded a full success in approximately 3 hours (3 missed cycles on the hourly cadence). Follow the [Sync Target Stalled / Target Drift runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/sync-target-stalled-drift.md) for step-by-step diagnosis.

Common causes:
- **B is unreachable**: confirm connectivity to `base_url` from the ECM container.
- **Credentials rotated/revoked**: if B's API credentials changed after the sync schedule was set up, ECM aborts the run at the credential-freshness check. Edit the sync target, update the credentials, save, and trigger a manual sync.
- **Target disabled**: check the **Enable** toggle on the target. A disabled target never runs.
- **Partial-apply loop**: the sync runs but a category keeps failing on apply (not B unreachable, but a recurring mix/rollback). Pull the most recent sync report from the task history; identify the failing category.

### The sync reports "Completed with Warnings"

The sync ran, wrote its changes to B, and rolled nothing back — but the result
was not clean, so ECM does not call it a success. The most common reason is a
channel on B left holding **no playable stream**: the channel exists, but not one
of the streams attached to it has a URL behind it, so playing it fails. The
notification and the task-history entry name how many channels are affected;
the sync report in task history names each one.

**Resolution:** attach a real stream to each named channel on B, or fix the
matching problem on A (usually a stream that A's provider no longer carries) and
re-sync.

This is a warning, not a failure — nothing was undone, and everything else in the
run was applied. If a target has a shortfall you already know about and accept,
turn off **Warning** alerts for that target's sync task in the scheduled-task
alert settings. That silences the external alert (email/Discord/Telegram) while
leaving the outcome, the in-app notification and the task history honest. Do not
expect the target row to show a full sync timestamp for such a run: only a clean
success records "B was current as of this time".

### Conflict on a channel with no channel number

A channel on A has no channel number, and B already has a channel with the same name and also no number. ECM cannot safely decide these are the same channel. **Resolution:** assign a channel number to the channel on A, then re-sync.

### B has credentials for sources that A can't provide

Expected. Credentials are intentionally not synced. Log into B and re-enter them manually. Sync will not overwrite B's credentials. It only sends redacted (credential-stripped) definitions.

### Most of B's channels have no programme data, and B's EPG source says "No URL provided"

Expected on an **Xtream Codes** provider, and the run tells you: the summary reads
`… ; 1 source(s) need their URL re-entered (the address carried the credentials, so it could not be copied); N channel(s) restored without an EPG link`.

An Xtream Codes guide URL authenticates by putting the username and password *in the URL*. Sync cannot ship the address without shipping the secret with it, so it ships neither — which leaves the source on B with nowhere to fetch from, and every channel that guide feeds without programme data. A guide URL that carries **no** credential (a plain XMLTV file, for instance) is unaffected and arrives intact, which is why some of B's channels usually still have their guide.

**Resolution:** on B, open the named EPG source, paste in the full guide URL including its credentials, and refresh it. On the next cycle the channels relink to it on their own — you do not need to re-run anything on A. To seed B with working URLs from the start instead, do the [initial migration with an encrypted backup](#dr-standby-setup-first-time-flow), which is the one path that carries credentials.

### The "Allow insecure TLS" warning

If you enabled **Allow insecure TLS** on a target, every sync cycle logs an audit row. You will see these in the journal. This is expected behavior. It is the record that TLS verification is being bypassed.

---

## Going deeper

- [Security threat model, Addendum D](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/security/threat_model_dbas_import.md#11-addendum-d-cross-instance-live-sync-v0181-one-way-ab-config-replication), covering STRIDE analysis of the sync egress surface: why credentials are redacted, why users never sync, SSRF controls.
- [Runbook: ECMSyncStalledTargetDrift](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/sync-target-stalled-drift.md): step-by-step when the sync stalls alert fires.
