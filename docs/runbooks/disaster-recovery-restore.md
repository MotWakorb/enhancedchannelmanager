# Runbook: Disaster Recovery (ECM Configuration Restore)

> Restore ECM + Dispatcharr configuration from a DBAS backup artifact after host failure, data loss, or a corrupt Dispatcharr instance.

- **Severity**: P1 (complete configuration loss) / P2 (partial data loss, instance degraded)
- **Owner**: SRE / Operator
- **Last reviewed**: 2026-08-04
- **Related beads**: `enhancedchannelmanager-0i2vt` (epic), `enhancedchannelmanager-sxx9x` (this doc)
- **Related ADR**: ADR-012 (`docs/adr/ADR-012-dbas-absorption-approach.md`)

---

## Alert / Trigger

- **Manual trigger**: Host failure, container loss, or accidental bulk-deletion that destroyed Dispatcharr configuration.
- **Manual trigger**: Post-migration: need to restore an archived configuration onto a freshly installed Dispatcharr.
- **Alert**: If `ECMSyncStalledTargetDrift` fires and the standby instance needs to be promoted, use this runbook to restore the latest backup onto the standby before treating it as primary.

---

## Symptoms

- Dispatcharr has no channels, M3U accounts, or EPG sources (fresh/wiped instance).
- ECM has lost its `journal.db`, settings, or logo files.
- The `/api/health/ready` endpoint returns unhealthy due to missing Dispatcharr configuration.
- Channels were bulk-deleted accidentally and the instance needs to be rolled back.

---

## Diagnosis

### Step 1: Confirm you have a usable backup

```bash
ls -lh /config/backups/ecm-backup-*.zip
```

Expected: one or more `.zip` files sorted by timestamp. The most recent is the best candidate.

If no local backup exists, check your configured cloud destinations (S3 bucket, WebDAV server, GDrive folder) for the most recent artifact.

### Step 2: Verify the artifact integrity

```bash
sha256sum -c /config/backups/ecm-backup-YYYY-MM-DD_HHMMSS.zip.sha256
```

Expected output: `ecm-backup-YYYY-MM-DD_HHMMSS.zip: OK`

If the sidecar is absent or the hash does not match, the artifact is corrupted. Use an earlier backup.

### Step 3: Check ECM is running and can reach Dispatcharr

```bash
docker exec ecm-ecm-1 curl -s http://localhost:8080/api/health/ready | python3 -m json.tool
```

Expected: `"dispatcharr": "ok"` (or equivalent healthy state). If Dispatcharr is unreachable, resolve that first. The restore pipeline writes directly to the Dispatcharr API.

### Step 4: Determine if the artifact is encrypted

```bash
python3 -c "
import sys
with open('/config/backups/ecm-backup-YYYY-MM-DD_HHMMSS.zip','rb') as f:
    print('ENCRYPTED' if f.read(8)==b'ECMBKENC' else 'PLAINTEXT')
"
```

If `ENCRYPTED`: have the passphrase ready before proceeding. If the passphrase is lost, you cannot decrypt this artifact. Go to an older unencrypted backup.

Note that encrypted does not imply credential-bearing. Only an artifact taken with **Include credentials** carries provider credentials and ECM accounts; an encrypted artifact taken without it is redacted exactly like a standard one, and nothing in the file distinguishes the two. If you are recovering onto a fresh instance and need credentials to travel, confirm which artifact you have before planning around it.

### Step 5: Confirm the restoring build is not older than the artifact

```bash
docker exec ecm-ecm-1 python -c "import os,urllib.request,json; \
print(json.load(urllib.request.urlopen('http://localhost:%s/api/health' % os.environ.get('ECM_PORT','6100'))))"
```

Compare that version against the `app_version` the artifact's manifest reports. **Restore only with the build that produced the artifact or a newer one.**

`schema_version` did not move when full redaction shipped, so the version gate will **not** stop you from restoring a new artifact with an older ECM. That combination has three known problems, all of which arrive behind an apparently successful restore:

1. **The restoring admin can be signed out of their own instance.** Older ECM has no step that preserves the destination's accounts across a restore, and a fully redacted artifact carries none of its own. During a DR rebuild this looks like the restore working and then dropping you at the setup wizard on an instance you were administering.
2. Alert-method `username` and `chat_id` are written in as the literal text `***REDACTED***`.
3. An alert method whose configuration could not be read at backup time is left as the literal text `***REDACTED***` in its entirety and stops sending notifications until reconfigured.

None is retroactively fixable. If you are rolling ECM back, roll back to an artifact taken before the upgrade instead of restoring a newer one onto the older build.

---

## Resolution

### Phase 1: Pre-restore (5 minutes)

**1.1 Take a fresh backup of current state (if any state exists).**

If the Dispatcharr instance still has any configuration (even partial), snapshot it first:

```bash
# Trigger a manual backup via the API
curl -s -X POST http://localhost:8080/api/backup/save \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Or use the UI: **Settings → Backup & Restore → Back Up Now**.

!!! danger "If this backup fails with a scrub error, that is the control working"
    ECM removes its accounts, credentials, telemetry and history from the artifact's copy of
    `journal.db` before writing anything. If it cannot open, read or rewrite that database, the
    **whole backup fails and no artifact is written**. Earlier builds fell back to shipping the
    database untouched behind a success, which is the outcome this replaces. Do not work around
    it, and do not treat it as backup-system corruption.

    In a DR context the cause is usually the incident itself: a truncated or damaged
    `/config/journal.db`, a full or read-only `/config` volume, or an interrupted earlier restore.

    ```bash
    docker logs --since 30m ecm-ecm-1 2>&1 | grep BACKUP
    docker exec ecm-ecm-1 sh -c 'ls -l /config/journal.db && df -h /config'
    docker exec ecm-ecm-1 python -c "import sqlite3; sqlite3.connect('/config/journal.db').execute('PRAGMA integrity_check').fetchall()"
    ```

    If the database is damaged, skip the pre-restore snapshot and proceed. You are about to
    replace that state anyway, and a failed snapshot must not stall the recovery. Record that you
    have no pre-restore backup before you continue.

**1.2 Open the Restore DBAS Backup modal.**

Go to **Settings → Backup & Restore → Restore DBAS Backup** in the ECM UI.

### Phase 2: Upload and validate (2 minutes)

**2.1** Upload the backup `.zip` artifact. If it is encrypted, enter the passphrase when prompted.

ECM validates:
- Decompression-bomb check (header scan only, nothing decompressed).
- `manifest.json` presence and integrity.
- `schema_version` compatibility (artifact must not be newer than this ECM build).
- Per-member SHA-256 verification.

If validation fails, stop and use an older backup.

**2.2** Note the reported `schema_version` and `app_version` from the manifest display.

### Phase 3: Dry-run preview (3 minutes)

**3.1** Click **Preview** (default mode, no changes are written).

**3.2** Review the per-category counts:

| Category | Expected count | What to check |
|-|-|-|
| M3U accounts | Matches source instance | Wrong count = wrong backup |
| Channel groups | Matches source instance | |
| Channels | Matches source instance | |
| Logos | Any non-zero count | As of `0.18.1-0024`, logos uploaded through ECM's own Logo Manager have their image bytes archived in the backup and restore intact, and a logo failure is a non-fatal restore category: it is counted and named in the report instead of aborting the whole restore. Logos referenced by a remote http(s) URL are unaffected either way and always round-tripped from their URL. **On builds before `0.18.1-0024`,** a logo uploaded through ECM's own Logo Manager was never archived, and the resulting miss aborted and rolled back the entire restore. On builds before `0.18.1-0032`, the preview also can't be trusted for this category specifically: it may report logo failures that don't occur on apply, or miss the one that will (`enhancedchannelmanager-dgnms`). |
| Users | 0 (not selected by default) | Enable explicitly if needed |

**3.3** If counts look wrong: verify you uploaded the correct artifact. Do not apply if the category counts are significantly lower than expected.

**3.4** Check for pre-flight problems in the report notes. Common ones:
- `unresolved_fk_reference`: the backup's channels reference groups/profiles not in the archive. Ensure channel groups are in the restore selection.
- `duplicate_unique_name`: duplicate names in one category. Contact support. This should not occur in an ECM-produced artifact.

### Phase 4: Apply (15–30 minutes depending on logo count)

**4.1** Click **Apply these changes**.

**4.2** Confirm the dialog.

**4.3** Monitor live progress. The restore runs in the fixed order:

1. M3U accounts (with deferred auto-sync)
2. EPG sources
3. Channel groups
4. Channel profiles
5. Stream profiles
6. User agents
7. Users (if selected)
8. Channels (with 4-tier stream matching)
9. Logos
10. Deferred auto-sync settings (applied last)

**4.4** Wait for the restore-complete report.

### Phase 5: Interpret the result

| Outcome | Action |
|-|-|
| **Success** | Go to Phase 6: verification. |
| **Restore failed: state rolled back** | The rollback ran cleanly. Instance is at pre-restore state. Diagnose the failure reason in the report notes and retry. See Phase 5a. |
| **Restore failed: rollback incomplete** | **Critical: stop.** See Phase 5b. |

**Phase 5a: Rolled-back failure:**

Read the notes section. Common causes:
- Dispatcharr returned an API error for a specific entity. Check Dispatcharr logs: `docker logs dispatcharr 2>&1 | tail -100`.
- Network timeout. Confirm ECM→Dispatcharr connectivity: `docker exec ecm-ecm-1 curl -s http://dispatcharr:8080/api/health`.
- Name conflict on the destination. If the destination has existing entities with conflicting names, the restore skips or fails them. Consider wiping the destination first if this is a fresh install.

After resolving the cause, re-attempt from Phase 3.

**Phase 5b: Rollback incomplete (instance in partial state):**

The restore created some entities and could not roll them back. The report lists the entity IDs.

```bash
# Get the residue list from the restore report (displayed in the UI)
# Manually delete each listed entity via the Dispatcharr UI or API
# Example: delete a channel group by its destination ID
curl -s -X DELETE http://dispatcharr:8080/api/channel-groups/ID \
  -H "Authorization: Bearer DISPATCHARR_TOKEN"
```

Repeat for each listed entity type and ID. Once all residue is deleted, take a fresh local backup and retry the restore from Phase 3.

### Phase 6: Verification (5 minutes)

**6.1** Navigate to **Channels** in ECM and confirm the channel count matches the backup.

**6.2** Check channel groups are present: **Settings → Channel Groups**.

**6.3** Run an M3U refresh. This confirms streams are populating **and**,
as of `0.18.1-0033`, repairs the channel bindings: a completed refresh
reattaches any channel still holding a placeholder stream onto the real
provider stream, then removes the leftover placeholders and the synthetic
`ECM Custom Streams (DBAS restore)` account. On a restore from a standard
(redacted) artifact, this is the step that makes the lineup playable, and
it is why you re-enter the provider credential before running it.

```bash
curl -s -X POST http://localhost:8080/api/tasks/m3u_refresh/run \
  -H "Authorization: Bearer YOUR_TOKEN"
```

The scheduled `m3u_refresh` task above and the Refresh action on an
individual account both trigger the reattach. A "refresh all accounts"
call (`POST /api/m3u/refresh`) and a refresh performed in Dispatcharr's
own UI do not; those are picked up on the next scheduled `m3u_refresh`
run. On builds before `0.18.1-0033` no refresh reattached anything, and
recovery required re-running the whole restore after the refresh.

**6.4** Run an EPG refresh if guide data is absent.

**6.4a** If you restored a **legacy full ZIP** (not the DBAS artifact), read the
notices on the restore response before anything else. A standard artifact carries no ECM
accounts, so a rebuilt instance lands at first-run setup and the notice says so; create the
admin account and sign in. A second notice names any configured surface this instance had
before the restore and does not have after, which for a standard artifact means cloud storage
targets, sync targets, M3U digest settings, or event-sync exclusions. Those are read from the
live instance on both sides of the restore, so the notice names only what you actually lost.
Re-establish each one and record it in the incident. The DBAS artifact restore does not write
`journal.db` and emits no notices.

**6.5** Test playback on one channel per M3U provider to confirm stream assignment worked.

**6.6** If logos are missing (red banner on restore-complete), and the restore still reached this phase, it's a warning: channels work. Re-upload logos manually or re-run an EPG logo match. On builds before `0.18.1-0024`, a restore that never completed and instead reported `partial_failed_rolled_back` at the logo category was the fatal case: the whole restore rolled back, not just the logos. As of `0.18.1-0024` a logo failure cannot cause that outcome.

---

## Escalation

If the restore repeatedly fails or if the rollback-incomplete state cannot be resolved within 30 minutes:

1. Preserve the restore-complete report (screenshot or copy the text).
2. Note the restore ID from the ledger filename: `ls /config/dbas/restore_ledger_*.json`.
3. File an incident bead with: artifact name, schema_version, failure notes, and the entity residue list.

---

## Post-incident

- [ ] Take a fresh backup from the recovered instance once verification passes.
- [ ] If you used an old backup, file a bead to investigate why newer backups were not available.
- [ ] If the restore pipeline itself failed (not a data issue), attach logs and file a bead.
- [ ] Update cloud destination configuration if the incident exposed a gap in off-host storage coverage.
- [ ] Consider enabling [Cross-Instance Sync](../user_guide/backup-restore/cross-instance-sync.md) for a standing DR standby.
- [ ] Schedule a postmortem if this was P1 (use `/postmortem` skill).

---

## Recurring maintenance

> Added 2026-08-03 (bead `enhancedchannelmanager-nvhg7`). `COMPONENTS.md` tags `dbas-backup` as the highest-care home-lab component: "restore must stay periodically exercised." Before this section, nothing prompted or enforced that — the only round-trip exercise this restore pipeline had ever received was a manual one-off doc-test run. **Tier: home-lab.** This is a documentation-level operational habit, not enforced infrastructure — there is no scheduled task or CI gate behind it (see "Future automation path" below).

**Do this monthly**, or immediately after any change to the backup/restore pipeline itself:

1. Go to **Settings → Backup & Restore → Saved Backups** and locate the most recent artifact (the one your regular schedule actually produced — don't manufacture a fresh one for this check; the point is to exercise what you would actually reach for in an incident).
2. Click that artifact's **Restore as DBAS backup** action (the restore icon on its row) — this opens the restore modal directly against the saved file, no download/upload round-trip required, and defaults to a counts-only dry run. Click **Run preview**. This is a dry-run — nothing is written. See [Verify a backup](../user_guide/backup-restore/verify-a-backup.md) for what a restore validates before the preview runs (decompression-bomb guard, manifest integrity, schema-version gate, per-member SHA-256) — that guide walks the upload-a-file variant, but the same checks and the same dry-run-first contract apply to the saved-backup path used here.
3. **What to check in the report:**
   - Every category you expect present (M3U accounts, channel groups, channels, EPG sources, settings, etc.) has a row, and its counts are in the neighborhood you expect for your instance — not zero, not wildly off.
   - **FAILED is 0**, or every non-zero FAILED row has a reason you already understand (see the `DEPENDENCY_UNRESOLVED` case in the next bullet — a settings key the destination doesn't have). A settings key skipped for being denylisted (credential/auth/instance-identity — see `backend/dbas/importers/settings_agents.py`) is reported as a separate **skipped** count, never as FAILED — it can never be the explanation for a non-zero FAILED row.
   - As of build 0015 (bead `y6zg6`), the dry-run **resolves settings keys against the destination** and reports `DEPENDENCY_UNRESOLVED` for any key that would 404 on apply — the same check and the same message the apply path uses. A clean preview on the Settings category is a real signal that those keys would apply; it is not a claim your Dispatcharr credentials or connectivity are also fine (see "What the preview does not check" in the Verify-a-Backup guide) — the preview cannot predict every class of upstream error.
   - No unexpected warning banner on the artifact itself. As of build 0019 (bead `zt3kf`), a backup that failed to gather one or more categories from Dispatcharr at capture time reports **warning-level, with the affected categories named** — it no longer masquerades as a clean SUCCESS. If your monthly artifact carries this warning, the backup you'd be restoring from is degraded; treat that as the finding, not the dry-run.
4. **Do not click Apply** as part of this exercise. A monthly maintenance check is a preview only, against a real artifact, on your real instance. If you do want to prove the apply path end to end, do it against a disposable or throwaway Dispatcharr instance, never the production one: see `tests/dbas-test-env/` for the project's own disposable-instance tooling.
5. **On any failure or surprise** (unexpected FAILED rows, a degraded-backup warning, counts that don't match expectations): file a bead capturing the artifact name/date, the report's counts and failure details, and whether this is the first month it's happened. Do not silently re-run and move on — a monthly check that "resolves itself" on retry without an explanation is exactly the kind of drift this section exists to catch.

### Future automation path

If this project ever moves up-tier from home-lab, a periodic restore exercise could be wired into CI rather than remaining a manual monthly habit: bead `1zwmr` (referenced from `backend/dbas/stream_matcher.py` and its tests) already flags stream-matching behavior that needs a seeded Dispatcharr instance to confirm, and bead `zqtjj`'s disposable-instance/seed tooling (`tests/dbas-test-env/`, documented in `docs/testing/dbas-test-env.md`) is the closest existing building block for a repeatable, non-production restore-exercise environment. Neither is currently wired into a schedule or CI job — this is a noted path, not a commitment.

---

## References

- [Backup & Restore overview](../user_guide/backup-restore/backup-overview.md)
- [Restore a backup: user guide](../user_guide/backup-restore/restore-a-backup.md)
- [Troubleshoot a restore](../user_guide/backup-restore/troubleshoot-restore.md)
- ADR-012 (`docs/adr/ADR-012-dbas-absorption-approach.md`): the restore pipeline design
- Threat model (`docs/security/threat_model_dbas_import.md`): restore security controls
