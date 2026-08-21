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
| Channels (+ embedded streams) | Channel names, numbers, groups, and their stream assignments. **A stream URL that carries the provider's credentials in its path** — the Xtream Codes `…/live/<username>/<password>/<id>.ts` form, and its `movie` / `series` variants — arrives on B with those two path segments replaced by `***REDACTED***`. The address is still there, so you can see where the stream pointed, but it will not play until B has its own provider account. The run summary names the count. A stream URL that carries no credential crosses byte-identical. On B every synced stream is filed under a single account named `ECM Custom Streams (DBAS restore)`, **not** under the replicated provider account it comes from — see [that section below](#on-b-every-synced-stream-belongs-to-an-account-called-ecm-custom-streams-dbas-restore) for why and what to do about it. |

### Opt-in per target

| Category | Notes |
|-|-|
| Logos | Off by default. Each target row on the Cross-Instance Sync card carries a **Logos off / Logos on** toggle next to its enable/disable switch; click it to turn logo replication on for that target. The setting is stored on the target, so it survives a reload and applies to that target's later runs. Covers all three places a logo can live: the files in ECM's own `/config/uploads/logos/`, the logos Dispatcharr hosts (where a logo you upload through Logo Manager actually lives), and the ones your provider supplies as a web address — which on an Xtream Codes lineup is usually nearly all of them. The first two travel as image files; the third travels as the address itself, so B points at the same picture A does without either instance re-hosting it. Where two of them describe the same logo, the one holding real image bytes is the one that travels. Only logos B is missing are created (matched by id, name, then filename); sync never deletes or bulk-clears B's existing logos. Because sync runs unattended, the image fetching is time-bounded per image and per cycle — a very large logo set that runs out of budget is reported as missed logos for that cycle and picked up on the next one. A provider address that carries your account's username or password is **not** copied: it would hand B a credential, and once the credential is stripped the address no longer loads, so that logo is reported as a miss with the channels it affected instead. |

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

### B's streams show `***REDACTED***` in their URL and will not play

Expected on an **Xtream Codes** provider, and the run tells you twice. It does not
report success: the outcome is **completed with failures**, and the summary reads

`… ; N channel(s) have NO playable stream; … ; N stream(s) restored without a playable URL (it carried the provider's credentials)`

Each affected channel is named in the run's stream-reattach detail, so you can see
exactly which parts of the lineup are down rather than counting them yourself. A
channel that still plays — because it kept a stream that carries no credential —
is not listed.

**Both counts repeat on every cycle until you fix it, and they stop the cycle after
you do.** They describe what B is holding right now, not what the cycle happened to
write, so a scheduled sync keeps telling you as long as the streams are still
unplayable — and goes quiet on its own once B has its own provider account. The same
is true of the "needs credentials re-entered" line: it names an account that is on B
and still has no password, so it stops the first cycle after you enter one.

An Xtream Codes stream URL puts the username and password in the address itself — `http://provider/live/<username>/<password>/<id>.ts` — so the credential *is* part of the address. Sync replaces those two path segments and carries the rest, which is why B's stream shows something like `http://provider/live/***REDACTED***/***REDACTED***/1234.ts`: you can see which provider and which stream it was, and no secret of yours has been copied onto B.

This is deliberate. Cross-instance sync never puts a provider credential on the wire, and B may be a machine at a different site or trust level — a recurring schedule that kept re-sending your subscription password would be a standing exposure, not a convenience. A stream URL that carries **no** credential (a plain-M3U provider's direct URL, for instance) crosses byte-identical and plays immediately.

**Resolution:** give B its own copy of the provider. Two steps, and neither is destructive:

1. On B, open the matching M3U account, re-enter the credentials, and refresh it. B now ingests the provider's real stream URLs. They arrive **alongside** the redacted ones rather than replacing them, so B briefly holds two copies of every stream and your channels are still on the unplayable copy. Nothing you can see has changed yet — that is expected, and step 2 is what finishes it.
2. Let one more sync cycle run (or force one). It re-matches every channel onto the real streams B just ingested, correctly attributed to your own provider account, and deletes the redacted stand-ins it is no longer using.

Measured end to end on a 59-channel replica: after step 1 the channels were still on
the 53 redacted stand-ins; after step 2 all 53 were bound to the real provider account
and every redacted stand-in was gone, with the run back to reporting success.

> **This used to need a third step** — manually deleting the `ECM Custom Streams (DBAS restore)`
> account between 1 and 2, because the re-match preferred the redacted stand-in over the
> real stream and would not let go of it. It no longer does, so **do not delete that
> account by hand**; on a B that is also a *restore* destination it can hold stand-ins
> that other channels are still using.

To skip all of this, seed B with working URLs from the start: do the [initial migration with an encrypted backup](#dr-standby-setup-first-time-flow), which is the one path that carries credentials.

### On B, every synced stream belongs to an account called "ECM Custom Streams (DBAS restore)"

Expected, and worth knowing before it surprises you. Open B's M3U Manager after a sync and you will see the provider accounts sync replicated — your Xtream Codes account, your Standard M3U account — each showing **no streams**, plus a fourth account named `ECM Custom Streams (DBAS restore)` holding **all** of them.

Sync does not make B fetch from your providers. Re-triggering every provider's playlist download on B on each cycle would hammer them and is deliberately not done, so B has no stream of its own to attach a replicated channel to. Sync therefore creates each stream directly and files it under that one account, which exists precisely to hold streams that have no provider account on the destination to belong to.

The consequence to plan around: **B's account list does not tell you which provider supplies which stream.** Read that from A, which is the instance actually talking to your providers. B's copy is a mirror of A's lineup, not an independent subscriber.

If you follow the [two-step resolution above](#bs-streams-show-redacted-in-their-url-and-will-not-play) for each of your providers, B's streams end up under your real provider accounts and sync deletes the stand-ins it no longer needs — that is the tidiest state B can be in, and it is worth doing once on a replica you intend to keep. The account itself only disappears once nothing is left under it, so it can legitimately linger holding the stand-ins for a provider you have not re-credentialed on B yet.

### The "Allow insecure TLS" warning

If you enabled **Allow insecure TLS** on a target, every sync cycle logs an audit row. You will see these in the journal. This is expected behavior. It is the record that TLS verification is being bypassed.

---

## Going deeper

- [Security threat model, Addendum D](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/security/threat_model_dbas_import.md#11-addendum-d-cross-instance-live-sync-v0181-one-way-ab-config-replication), covering STRIDE analysis of the sync egress surface: why credentials are redacted, why users never sync, SSRF controls.
- [Runbook: ECMSyncStalledTargetDrift](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/sync-target-stalled-drift.md): step-by-step when the sync stalls alert fires.
