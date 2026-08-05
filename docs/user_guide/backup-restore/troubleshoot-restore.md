# Troubleshoot a Restore

> **Status:** Shipped in v0.18.0.

---

## "Unsupported backup version"

**Symptom:** The upload step shows "Unsupported backup version" and the restore is refused.

**Cause:** The artifact was produced by a newer ECM build than the one currently running. The schema version inside the artifact is higher than this build supports.

**Fix:** Upgrade ECM to at least the version that produced the backup. Alternatively, if you have an older backup that is still usable, restore from that instead.

---

## "Backup integrity check failed"

**Symptom:** The upload step fails with "Backup integrity check failed."

**Cause:** One or more files inside the artifact do not match their SHA-256 hashes recorded in `manifest.json`. The artifact may have been corrupted during transit, partially overwritten, or tampered with.

**Fix:**
1. Download the artifact again from its original source.
2. If you have the `.sha256` sidecar, verify the artifact locally: `sha256sum -c ecm-backup-YYYY-MM-DD_HHMMSS.zip.sha256`.
3. If the artifact is corrupt, restore from an earlier backup.

---

## "Could not decrypt artifact: wrong passphrase or corrupted artifact"

**Symptom:** After entering a passphrase for an encrypted backup, the restore is refused with this message.

**Cause:** Either the passphrase is wrong, or the encrypted artifact is corrupted. ECM returns the same message for both cases. This is intentional (no oracle).

**Fix:**
1. Double-check the passphrase. Passphrases are case-sensitive.
2. If you are confident the passphrase is correct, the artifact may be corrupted. Verify the `.sha256` sidecar if available.
3. If the passphrase is lost: there is no recovery path. The encrypted artifact is permanently unrecoverable. Restore from an unencrypted backup, or from a different encrypted backup for which you have the passphrase.

---

## "Pre-flight refused: ..."

**Symptom:** The restore is refused at the pre-flight stage with one or more problem messages.

Pre-flight runs before any mutation. If it fails, nothing was changed.

Common pre-flight failures:

| Problem kind | Meaning | Fix |
|-|-|-|
| `unsupported_schema_version` | Artifact schema version is unsupported (same as above). | Upgrade ECM. |
| `unresolved_fk_reference` | A channel references a channel group or stream profile that is not in the archive and does not already exist on the destination. | Restore the channel groups and stream profiles first, or include them in the same restore. |
| `duplicate_unique_name` | The archive contains two M3U accounts, channel groups, or channel profiles with the same name. | The archive is malformed. Contact support if this was produced by ECM's own backup tool. |
| `count_out_of_bounds` | A category has an implausibly large number of entities (above the sanity ceiling). | The archive may be malformed or from an untrusted source. |

---

## Restore aborted and rolled back at the logo category

**Symptom:** The restore-complete report shows `outcome:
partial_failed_rolled_back`, with a logo entry like:

```
logo   created=10 updated=0 skipped=0 failed=1
  reason=validation_error label='Drill Uploaded Logo'
  message=unsafe or empty logo filename
logo_misses: 1
notes:
  - restore failed at category logo; compensating rollback ran.
  - rollback completed: 44 entity/entities removed.
```

**Cause:** A logo uploaded through ECM's own Logo Manager (as opposed to
one auto-assigned from an M3U or EPG feed) is written to **Dispatcharr's**
storage, not ECM's own upload directory. Those bytes are retrievable at
backup time over Dispatcharr's own logo cache endpoint, using the same
API key ECM already holds for every other backup category, but the
backup does not currently request them (`enhancedchannelmanager-xb58a`).
Because the backup never captured the bytes, the restore correctly
detects the miss, but a logo failure is currently classified as
**fatal**, so it aborts and rolls back the entire restore, not just the
logo category. Every other category that had already succeeded
(channels, streams, accounts, profile, users, other logos) is deleted
again by the compensating rollback (`enhancedchannelmanager-d0agi`).
Logos referenced by a remote http(s) URL are unaffected; this only
happens for logos uploaded through ECM's own Logo Manager.

**Fix:** Removing the ECM-uploaded logo record from the source before
taking the backup you intend to restore, then re-adding the logo
manually on the destination afterward, is still the only way to get the
restore to complete today. Those logos are then not in the backup at
all. Neither the backup gap (`enhancedchannelmanager-xb58a`, archiving
the bytes at backup time) nor the fatal classification
(`enhancedchannelmanager-d0agi`, treating a logo miss as non-fatal) is
shipped; re-check both beads' status before relying on this section.

---

## Preview reports logo failures that don't happen on apply

**Symptom:** The dry-run preview reports some or all logos as
`validation_error: unsafe or empty logo filename`, but the apply of the
same artifact restores most or all of them successfully.

**Cause:** The preview never simulates the URL re-create path for logos
(it's gated behind an `if not is_dry_run` check), so every URL-only logo
falls through to a byte-validation path that expects a `filename` key
the preview's records don't have. This makes the logo category's preview
numbers unreliable in both directions: it can report failures that won't
happen, and it can hide the one that will
(`enhancedchannelmanager-dgnms`).

**Fix:** Still preview first; every other category's preview numbers
are accurate. But do not abort a restore solely because of logo failures
shown in the preview. Compare the preview's logo count against what you
expect qualitatively, then verify actual logo outcomes after the apply
completes (see [Logo misses: red banner after restore](#logo-misses-red-banner-after-restore)
below), not before.

---

## Playback still fails after a redacted restore, even after a refresh

**Symptom:** You restored a standard (redacted) backup. Streams populate
after you re-enter the M3U credential and refresh, but restored channels
still won't play; they remain bound to placeholder streams.

**Cause:** ECM's placeholder-rebind pass (the step that reattaches
restored channels to real provider streams) runs exactly once,
immediately after the restore's own deferred M3U refresh. On a redacted
artifact, the M3U account has no credential yet at that instant, so
there is nothing to match against, and the rebind pass does not re-run on
its own. A later manual refresh adds the real streams *beside* the
placeholders without rebinding anything to them.

**Fix:** Re-entering the credential and refreshing is necessary but not
sufficient. After the refresh confirms real streams are present, **run
the same restore again, from the same artifact.** This re-triggers the
rebind pass, now with real streams to match against. See
[Step 6a of Run a restore drill](run-a-restore-drill.md#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)
for the full measured sequence. If playback still fails after a full
credential re-entry → refresh → re-restore cycle, that is the residual
`enhancedchannelmanager-2o0cz` defect, not a step you missed. File it as
a fresh occurrence with the restore report attached.

An encrypted artifact with **Include credentials** does not need this
sequence at all; the credential round-trips automatically and playback
works on the first restore.

---

## One channel still won't play, and it is a channel with several streams

**Symptom:** The restore reports success and every other channel plays,
but one channel errors on playback. The restore-complete report names
it under "channel(s) have NO playable stream" (field
`channels_needing_stream_reattach`, with the channel named in
`stream_reattach_details`). That channel's streams show the provider
**ECM Custom Streams (DBAS restore)** instead of the real provider. The
channel has more than one stream assigned.

**Cause:** When ECM reattaches a restored channel to real streams, it
matches each archived stream by name. If two streams on the same
channel have names that differ only in ways the name matcher normalizes
away (capitalization, spacing, punctuation), both archived streams
resolve to the **same** real stream. ECM then asks Dispatcharr to save
the channel with that stream listed twice; Dispatcharr rejects the
entire update because a channel cannot hold the same stream twice, and
ECM safely reverts the channel to its placeholder streams. Because the
save is all-or-nothing, that channel's other streams lose their correct
matches too, even though they had matched fine. Real measured example:
two streams named `TX | Dallas | PBS KERA` and `TX | DALLAS | PBS KERA`
on one channel are enough to trigger it.

**Fix:** Reattach that channel's streams by hand. This is a one-time
per-channel repair:

1. Open Channel Manager and find the channel the report named.
2. Remove the streams showing the **ECM Custom Streams (DBAS restore)** provider.
3. Add the real streams back from your provider, in the order you want.
4. Play the channel to confirm.

Re-running the restore does not clear this: it hits the same collision
and reports the channel under `failed` again. Every other channel is
unaffected, so there is no need to redo the whole restore.

Prevention: on the source instance, give near-identical streams on the
same channel distinguishable names before taking the backup.

---

## The restore ran but channels look wrong

**Symptom:** The restore reported success, but channel numbers are wrong, channels are missing, or streams are not playing.

**Check 1: Stream matching tiers.** The restore-complete report shows how many streams were matched at each tier (exact URL, exact name+provider, exact normalized name, fuzzy name). A large number of Tier-4 fuzzy matches or misses means ECM had trouble re-attaching streams from the archive to the streams on this Dispatcharr instance. This happens most often when the M3U provider's stream URLs have changed significantly, or when restoring onto an instance with different M3U accounts.

**Fix for stream misses:** Check that the M3U accounts on the destination instance are configured and active. Run an M3U refresh to make sure all streams are present. Then re-attempt the restore. The stream matcher will have more candidates to match against.

**Check 2: Did the M3U accounts restore first?** If you ran a partial restore that included channels but excluded M3U accounts, channels cannot be attached to their providers. Restore M3U accounts first, then restore channels.

**Check 3: Channel number conflicts.** The restore reports a `CONFLICT` (failed with reason) for a channel when the channel has no channel number and an existing channel with the same name and no number is already present. This is ambiguous. ECM cannot determine if they are the same channel or two different ones. The second channel is not created. Assign explicit channel numbers on the source before re-taking the backup to avoid this.

---

## The restore failed part-way: "restore failed, state rolled back"

**Symptom:** The restore-complete screen shows "restore failed, state rolled back."

**What happened:** A failure occurred mid-restore (a Dispatcharr API error, a timeout, or a validation error). ECM ran a compensating rollback: every entity created during this restore run was deleted in reverse order. The instance is back to its pre-restore state.

**Fix:** Investigate the failure reason shown in the report (it appears in the notes section). Common causes:
- Dispatcharr API returned an error for a specific entity (name conflict, validation failure). Check the Dispatcharr logs.
- Network timeout between ECM and Dispatcharr. Check connectivity and retry.

After resolving the underlying cause, re-run the restore from scratch.

---

## The restore failed: "rollback incomplete"

**Symptom:** The restore-complete screen shows "restore failed, rollback incomplete."

**What happened:** A failure occurred mid-restore AND the compensating rollback could not delete one or more entities (the delete returned an error that was not 404). The instance is in a partially modified state. The report lists the entity IDs and types that could not be rolled back.

**This is the worst outcome. Take it seriously.** The instance state is indeterminate. Some entities from the backup are present, others are not. Do not attempt another restore until you have cleaned up the residue.

**Fix:**
1. Read the report. Note every entity type and destination ID listed as residue.
2. Log into the Dispatcharr UI (or use the Dispatcharr API) and manually delete each listed entity.
3. Confirm the entities are gone.
4. Take a fresh backup of the current state.
5. Re-attempt the restore.

If you are on a fresh install and the instance has no channels you care about, a simpler recovery is: delete all channels and M3U accounts via the Dispatcharr UI, then re-run the restore from scratch.

---

## Logo misses: red banner after restore

**Symptom:** After a successful restore, a red banner shows "N logos could not be matched."

**What happened:** One or more logo files in the archive did not match any existing logo on the destination. The channels exist and work. Only the logos are affected.

**Fix:** Re-upload the missing logos manually in the Dispatcharr UI, or re-run an EPG logo match if your EPG sources carry logo URLs.

---

## Checking logs

ECM logs all restore activity at the `[DBAS-RESTORE]` log prefix. For detailed diagnostics:

```bash
docker logs ecm-ecm-1 2>&1 | grep "\[DBAS"
```

The logs contain entity types, IDs, and outcome codes, but never credentials or passphrase values.

The restore ledger is stored at `/config/dbas/restore_ledger_<id>.json` while a restore is in progress, and deleted on clean success. If a ledger file remains after a failed restore, it records the exact entities that were created and (if rollback was incomplete) which ones remain.

---

## Still stuck?

- [Backup & Restore overview](backup-overview.md): to understand the artifact format and what each category contains.
- [`docs/security/threat_model_dbas_import.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/security/threat_model_dbas_import.md): the security analysis of the restore pipeline.
- [Disaster Recovery runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/disaster-recovery-restore.md): for structured incident response.
