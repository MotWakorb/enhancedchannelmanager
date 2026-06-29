# Runbook: Disaster Recovery — ECM Configuration Restore

> Restore ECM + Dispatcharr configuration from a DBAS backup artifact after host failure, data loss, or a corrupt Dispatcharr instance.

- **Severity**: P1 (complete configuration loss) / P2 (partial data loss, instance degraded)
- **Owner**: SRE / Operator
- **Last reviewed**: 2026-06-28
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

### Step 1 — Confirm you have a usable backup

```bash
ls -lh /config/backups/ecm-backup-*.zip
```

Expected: one or more `.zip` files sorted by timestamp. The most recent is the best candidate.

If no local backup exists, check your configured cloud destinations (S3 bucket, WebDAV server, GDrive folder) for the most recent artifact.

### Step 2 — Verify the artifact integrity

```bash
sha256sum -c /config/backups/ecm-backup-YYYY-MM-DD_HHMMSS.zip.sha256
```

Expected output: `ecm-backup-YYYY-MM-DD_HHMMSS.zip: OK`

If the sidecar is absent or the hash does not match, the artifact is corrupted. Use an earlier backup.

### Step 3 — Check ECM is running and can reach Dispatcharr

```bash
docker exec ecm-ecm-1 curl -s http://localhost:8080/api/health/ready | python3 -m json.tool
```

Expected: `"dispatcharr": "ok"` (or equivalent healthy state). If Dispatcharr is unreachable, resolve that first — the restore pipeline writes directly to the Dispatcharr API.

### Step 4 — Determine if the artifact is encrypted

```bash
python3 -c "
import sys
with open('/config/backups/ecm-backup-YYYY-MM-DD_HHMMSS.zip','rb') as f:
    print('ENCRYPTED' if f.read(8)==b'ECMBKENC' else 'PLAINTEXT')
"
```

If `ENCRYPTED`: have the passphrase ready before proceeding. If the passphrase is lost, you cannot decrypt this artifact — go to an older unencrypted backup.

---

## Resolution

### Phase 1 — Pre-restore (5 minutes)

**1.1 Take a fresh backup of current state (if any state exists).**

If the Dispatcharr instance still has any configuration (even partial), snapshot it first:

```bash
# Trigger a manual backup via the API
curl -s -X POST http://localhost:8080/api/backup/save \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Or use the UI: **Settings → Backup & Restore → Back Up Now**.

**1.2 Open the Restore DBAS Backup modal.**

Go to **Settings → Backup & Restore → Restore DBAS Backup** in the ECM UI.

### Phase 2 — Upload and validate (2 minutes)

**2.1** Upload the backup `.zip` artifact. If it is encrypted, enter the passphrase when prompted.

ECM validates:
- Decompression-bomb check (header scan only — nothing decompressed).
- `manifest.json` presence and integrity.
- `schema_version` compatibility (artifact must not be newer than this ECM build).
- Per-member SHA-256 verification.

If validation fails, stop and use an older backup.

**2.2** Note the reported `schema_version` and `app_version` from the manifest display.

### Phase 3 — Dry-run preview (3 minutes)

**3.1** Click **Preview** (default mode — no changes are written).

**3.2** Review the per-category counts:

| Category | Expected count | What to check |
|-|-|-|
| M3U accounts | Matches source instance | Wrong count = wrong backup |
| Channel groups | Matches source instance | |
| Channels | Matches source instance | |
| Logos | Any non-zero count | Logo misses acceptable; channels still work |
| Users | 0 (not selected by default) | Enable explicitly if needed |

**3.3** If counts look wrong: verify you uploaded the correct artifact. Do not apply if the category counts are significantly lower than expected.

**3.4** Check for pre-flight problems in the report notes. Common ones:
- `unresolved_fk_reference` — the backup's channels reference groups/profiles not in the archive. Ensure channel groups are in the restore selection.
- `duplicate_unique_name` — duplicate names in one category. Contact support — this should not occur in an ECM-produced artifact.

### Phase 4 — Apply (15–30 minutes depending on logo count)

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

### Phase 5 — Interpret the result

| Outcome | Action |
|-|-|
| **Success** | Go to Phase 6 — verification. |
| **Restore failed — state rolled back** | The rollback ran cleanly. Instance is at pre-restore state. Diagnose the failure reason in the report notes and retry. See Phase 5a. |
| **Restore failed — rollback incomplete** | **Critical — stop.** See Phase 5b. |

**Phase 5a — Rolled-back failure:**

Read the notes section. Common causes:
- Dispatcharr returned an API error for a specific entity. Check Dispatcharr logs: `docker logs dispatcharr 2>&1 | tail -100`.
- Network timeout. Confirm ECM→Dispatcharr connectivity: `docker exec ecm-ecm-1 curl -s http://dispatcharr:8080/api/health`.
- Name conflict on the destination. If the destination has existing entities with conflicting names, the restore skips or fails them. Consider wiping the destination first if this is a fresh install.

After resolving the cause, re-attempt from Phase 3.

**Phase 5b — Rollback incomplete (instance in partial state):**

The restore created some entities and could not roll them back. The report lists the entity IDs.

```bash
# Get the residue list from the restore report (displayed in the UI)
# Manually delete each listed entity via the Dispatcharr UI or API
# Example: delete a channel group by its destination ID
curl -s -X DELETE http://dispatcharr:8080/api/channel-groups/ID \
  -H "Authorization: Bearer DISPATCHARR_TOKEN"
```

Repeat for each listed entity type and ID. Once all residue is deleted, take a fresh local backup and retry the restore from Phase 3.

### Phase 6 — Verification (5 minutes)

**6.1** Navigate to **Channels** in ECM and confirm the channel count matches the backup.

**6.2** Check channel groups are present: **Settings → Channel Groups**.

**6.3** Run an M3U refresh to confirm streams are populating:
```bash
curl -s -X POST http://localhost:8080/api/tasks/m3u_refresh/run \
  -H "Authorization: Bearer YOUR_TOKEN"
```

**6.4** Run an EPG refresh if guide data is absent.

**6.5** Test playback on one channel per M3U provider to confirm stream assignment worked.

**6.6** If logos are missing (red banner on restore-complete), this is a warning — channels work. Re-upload logos manually or re-run an EPG logo match.

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

## References

- [Backup & Restore overview](../user_guide/backup-restore/backup-overview.md)
- [Restore a backup — user guide](../user_guide/backup-restore/restore-a-backup.md)
- [Troubleshoot a restore](../user_guide/backup-restore/troubleshoot-restore.md)
- ADR-012 (`docs/adr/ADR-012-dbas-absorption-approach.md`) — the restore pipeline design
- Threat model (`docs/security/threat_model_dbas_import.md`) — restore security controls
