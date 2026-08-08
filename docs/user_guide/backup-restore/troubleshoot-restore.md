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

**Applies to:** builds before `0.18.1-0024`.

**Symptom:** The restore-complete report showed `outcome:
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
storage, not ECM's own upload directory. On these builds the backup did
not fetch those bytes, even though they were retrievable at backup time
over Dispatcharr's own logo cache endpoint, using the same API key ECM
already holds for every other backup category. Because the backup never
captured the bytes, the restore correctly detected the miss, and a logo
failure was classified as **fatal**, so it aborted and rolled back the
entire restore, not just the logo category. Every other category that
had already succeeded (channels, streams, accounts, profile, users,
other logos) was deleted again by the compensating rollback. Logos
referenced by a remote http(s) URL were unaffected; this only happened
for logos uploaded through ECM's own Logo Manager.

**Fix:** As of `0.18.1-0024`, the backup archives an uploaded logo's
image bytes at gather time, and a logo failure of any kind is a
non-fatal restore category: it is counted and named in the report, and
the rest of the restore completes. Upgrade to `0.18.1-0024` or later.
This symptom should not occur on current builds; if you see it anyway,
check the Dispatcharr logs for what actually failed on that specific
logo rather than assuming this is the same defect.

---

## Preview reports logo failures that don't happen on apply

**Symptom:** The dry-run preview reports some or all logos as
`validation_error: unsafe or empty logo filename`, but the apply of the
same artifact restores most or all of them successfully.

**Applies to:** builds before `0.18.1-0032`.

**Cause:** On those builds the preview never simulated the URL re-create
path for logos, so every URL-only logo fell through to a byte-validation
path that expects a `filename` key the preview's records don't carry.
That made the logo category's preview numbers unreliable in both
directions: they could report failures that would not happen, and they
could bury the one that would (`enhancedchannelmanager-dgnms`).

**Fix:** As of `0.18.1-0032` the preview takes the same branch the apply
takes. A logo with no archived bytes but a usable absolute `http(s)` URL
is counted as a would-create, not a failure, so the logo category's
preview counts now match what the apply does.

On an older build, still preview first (every other category's numbers
were accurate there too), but do not abort a restore solely because of
logo failures shown in the preview. Compare the preview's logo count
against what you expect qualitatively, then verify actual logo outcomes
after the apply completes (see
[Logo misses: red banner after restore](#logo-misses-red-banner-after-restore)
below), not before.

---

## Playback fails after a redacted restore

**Symptom:** You restored a standard (redacted) backup. Restored channels
will not play, and their stream lists show the provider **ECM Custom
Streams (DBAS restore)** instead of your real provider.

**Cause:** A standard artifact carries no provider credentials, so at the
moment the restore reattaches channels to real streams there are no real
streams to attach to: the M3U account cannot authenticate yet. Every
channel is parked on a URL-less placeholder instead. Immediately after a
redacted restore this is the expected state, not a defect.

**Fix, as of `0.18.1-0033`: re-enter the credential and refresh. That is
the whole recovery.**

1. Go to **Settings → M3U Accounts** and enter the real password on every
   account the restore report (or the post-restore panel) names as
   needing one.
2. Refresh that account, with **Save & Refresh** or the account's own
   **Refresh** action.

When that refresh completes, ECM re-runs the reattach pass over the
streams it has just materialized, moves each channel off its placeholder
onto the real stream, and then deletes the leftover placeholders and the
synthetic **ECM Custom Streams (DBAS restore)** account behind it. Only
placeholders ECM itself created are ever touched. A custom stream you
created yourself is never rebound and never removed.

!!! note "Which refresh actions trigger the reattach"
    **Covered:** the **Refresh** action on an individual M3U account, and
    the scheduled M3U refresh task.

    **Not covered:** a "refresh all accounts" action, and a refresh
    performed in Dispatcharr's own UI. Neither reports completion back to
    ECM in a way the reattach can hang on, so an instance that reached
    real streams by one of those routes is picked up on the **next
    scheduled M3U refresh** rather than immediately. If you used one of
    those and your channels are still on placeholders, refresh the
    individual account and they will rebind straight away.

**On builds before `0.18.1-0033`,** the reattach pass ran exactly once,
during the restore itself, and never re-ran on its own. A later refresh
added the real streams *beside* the placeholders and rebound nothing to
them, so the recovery needed a third step: once the refresh confirmed
real streams were present, **run the same restore again, from the same
artifact**. If you are on an older build, that third step is still
required. See
[Step 6a of Run a restore drill](run-a-restore-drill.md#step-6a-if-you-restored-a-standard-redacted-artifact-recover-credentials-before-you-check-playback)
for the measured sequence on both builds.

An encrypted artifact with **Include credentials** needs none of this on
any build. The credential round-trips with the artifact and playback works
on the first restore.

If a channel is still on a placeholder after a completed refresh of its
own M3U account, see
[A restored channel is still on the ECM Custom Streams provider](#a-restored-channel-is-still-on-the-ecm-custom-streams-provider)
below.

---

## A restored channel is still on the ECM Custom Streams provider

**Symptom:** One channel's stream list shows the provider **ECM Custom
Streams (DBAS restore)** instead of your real provider. There are two
shapes of this, and only one of them is an outage:

- **The channel plays.** It kept its real streams and is holding one
  leftover placeholder in one slot. The report counts it under
  `channels_needing_stream_reattach` and names it in
  `stream_reattach_details` with `has_playable_stream: true`. The
  restore still reports success. This is untidy, not broken.
- **The channel errors on playback.** Nothing on it is a real stream.
  The report counts it under "channel(s) have NO playable stream"
  (`channels_with_no_playable_stream`), names it in
  `stream_reattach_details` with `has_playable_stream: false`, and the
  restore reports `completed_with_failures` rather than success.

**Cause of the leftover placeholder (the channel plays):** When ECM
reattaches a restored channel to real streams, it matches each archived
stream by name. A channel cannot hold the same stream twice, so if two
archived streams on one channel resolve to the **same** real stream, ECM
gives that stream to the first slot which claimed it and leaves the second
slot on its placeholder. The collision costs one slot rather than the
whole channel.

As of `0.18.1-0029` this only happens when two archived streams on the
same channel have names that are **genuinely identical**. No per-stream
name match can tell those apart, so one of them must lose its slot.

On builds before `0.18.1-0029`, names that differed only in ways the
matcher normalizes away also collided. Two streams named
`TX | Dallas | PBS KERA` and `TX | DALLAS | PBS KERA` on one channel were
enough to trigger it, because the matcher folded the case difference and
handed both slots the same stream. Matching now prefers a destination
stream whose name is character-for-character identical to the archived
one, so a case-differing pair resolves to the two distinct streams that
actually exist and the channel restores complete, in its archived order.

**Cause of a channel that will not play:** every one of its slots is a
placeholder, so there is nothing to stream. That happens when nothing on
the destination matched any of the channel's archived streams (most
often because the provider's streams had not materialized yet, or the
names moved too far from the archive to match), or when the update that
would have reattached the channel failed upstream and left every slot as
it was. The report names these channels specifically; they are the ones
worth acting on first.

**Fix:** Reattach that channel's streams by hand. This is a one-time
per-channel repair:

1. Open Channel Manager and find the channel the report named.
2. Remove the streams showing the **ECM Custom Streams (DBAS restore)** provider.
3. Add the real streams back from your provider, in the order you want.
4. Play the channel to confirm.

Before you re-run a restore to fix one channel, refresh that channel's
own M3U account first. On `0.18.1-0033` and later, a completed refresh of
an individual account re-runs the reattach pass by itself, which is
cheaper and safer than a second restore. Re-running the restore is worth
trying only for a channel that will not play, and only once the real
streams are actually present on the destination.

!!! warning "On builds before `0.18.1-0029`, a second run made a collided channel worse"
    A second run of the same artifact cleared the leftover slot by
    *dropping* it rather than repairing it, leaving the channel with fewer
    streams than the archive holds and silently reordering the survivors,
    while reporting the run as entirely clean. Hand-reattaching (above) was
    the only reliable repair. From `0.18.1-0029` the collision that caused
    it does not occur for case-differing names, and the report names every
    channel left holding a slot that streams nothing, whichever run created
    it.

Either way the channel is a named action item, never a `failed` row, and
every other channel is unaffected. There is no need to redo the whole
restore.

Prevention: on the source instance, give identically-named streams on the
same channel distinguishable names before taking the backup. This prevents
the leftover placeholder, which is an annoyance rather than an outage. A
channel that will not play at all has a different cause and is not
prevented by renaming.

---

## A clean report is not proof of playback

**Symptom:** The report says the run finished cleanly. Most channels play.
One does not.

**The durable advice:** a restore report tells you what ECM believes it
did, not what your instance can stream. Before you call any restore done,
actually play a channel: fetch the stream and confirm you get both a 2xx
status and real media bytes, not merely that a URL field is populated.
When something is wrong, read the per-category `failed` rows as well as
the summary counters, and check Dispatcharr's own log for the underlying
cause. Summary counters are a starting point, never the verdict.

That advice is worth keeping regardless of build, because it is what
caught the two defects below.

**On builds before `0.18.1-0029`,** the counters could be actively
misleading on a repeat restore:

- The "channels needing attention" counters
  (`channels_needing_stream_reattach`, `channels_with_no_playable_stream`)
  only inspected placeholders the *current* restore had created. A channel
  stranded by an *earlier* restore was invisible to them, so a repeat run
  could report `0` and `0`, with an empty `notes[]`, for a channel that
  answered HTTP 500 on playback. As of `0.18.1-0029` the audit is taken
  for **every** restored channel, from what it is actually left holding,
  whichever run put it there.
- The channel that hid behind those zeroes was a name-collision case: the
  reattach pass tried to attach a stream the channel already held, and
  Dispatcharr rejected the whole update with
  `psycopg.errors.UniqueViolation: duplicate key value violates unique
  constraint "unique_channel_stream"`. The only trace in the report was a
  single `stream` category row with `failed: 1` and
  `reason: upstream_api_error`. That collision is resolved as of
  `0.18.1-0029`; see
  [A restored channel is still on the ECM Custom Streams provider](#a-restored-channel-is-still-on-the-ecm-custom-streams-provider)
  above.

If you are on an older build and a channel fails playback while the
summary counters look clean, this check confirms the failure mode:

```bash
docker logs <dispatcharr-container> 2>&1 | grep -i "unique_channel_stream"
```

---

## What a preview can and cannot tell you about stream health

**Symptom:** You want to know, before you commit, whether the restore will
leave channels unable to play. The preview's stream-health counters do not
answer that question.

**How to read a preview as of `0.18.1-0032`:**

| What the preview reports | How far to trust it |
|-|-|
| Per-category `would_create` / `would_update` / `would_skip` | Accurate. Measured against a matching apply, these matched exactly, category by category. |
| `logo_reattach` and `epg_link_reattach` splits | Predicted, and they match the apply. |
| `profile_membership_drift` | Predicted, and it matches the apply exactly. |
| `channels_needing_stream_reattach` and `channels_with_no_playable_stream` | **Not predicted.** Both read `null` on a dry run. |

**What `null` means here.** These two counters are written by the pass
that reattaches channels to real provider streams, and that pass matches
against streams the restore's own deferred M3U refresh materializes. A
preview performs no refresh, so it has nothing to look at. `null` says
exactly that: this number is not knowable before the apply. Read it as
"not predicted", never as "zero channels need attention".

**On builds before `0.18.1-0032`,** those two counters reported a
confident `0` derived from having looked at nothing, and the logo split
and profile-membership counts under-reported on a fresh target. Measured
on the same artifact against the same freshly-rolled-out target:

| counter | preview | apply |
|-|-|-|
| `epg_link_reattach.created_channels` | 12 | 12 |
| `logo_reattach.created_channels` | 0 | 11 |
| `channels_needing_stream_reattach` | 0 | 12 |
| `channels_with_no_playable_stream` | 0 | 12 |
| `profile_membership_drift` | 0 | 6 |

Per-category `would_create` matched the apply's `created` exactly even
then, for every category: `m3u_account` 1, `epg_source` 1,
`channel_group` 377, `channel_profile` 1, `user_agent` 1,
`stream_profile` 1, `user` 1, `channel` 12, `logo` 11. On a **populated**
target the splits were already exact in both relink modes: preserve mode
(keeps a channel's own existing EPG link and logo) measured preview 12/11
preserved matching apply 12/11 preserved, and overwrite mode (replaces
them with the archive's values) measured preview 12/11 existing matching
apply 12/11 existing. Only the fresh-target case under-reported. This
measurement predates `enhancedchannelmanager-r1ei7`; as of `0.18.1-0041`
the same two relink modes also govern a channel's **group**, not just its
EPG link and logo: preserve reads *"Keep their current guide data,
logos, and grouping"* and overwrite reads *"Replace their guide data,
logos, and grouping with the backup's"*, and the resulting
channel-group drift count is itself predicted on the preview. See
[Restoring onto a populated
target](run-a-restore-drill.md#restoring-onto-a-populated-target) for
that measurement.

**Fix:** Preview to confirm scope. Verify stream health, and actual
playback, after the apply completes, on any build.

---

## The restore ran but channels look wrong

**Symptom:** The restore reported success, but channel numbers are wrong, channels are missing, or streams are not playing.

**Check 1: Stream matching tiers.** The restore-complete report shows how many streams were matched at each tier (exact URL, exact name+provider, exact normalized name, fuzzy name). A large number of Tier-4 fuzzy matches or misses means ECM had trouble re-attaching streams from the archive to the streams on this Dispatcharr instance. This happens most often when the M3U provider's stream URLs have changed significantly, or when restoring onto an instance with different M3U accounts.

**Fix for stream misses:** Check that the M3U accounts on the destination instance are configured and active, then refresh each account so all streams are present. As of `0.18.1-0033`, a completed refresh of an individual account also reattaches any channel still holding a placeholder, so check playback before doing anything else. If channels are still short of streams after that, re-attempt the restore: the stream matcher will have more candidates to match against.

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
