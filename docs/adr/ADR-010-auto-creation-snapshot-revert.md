# ADR-010: Auto-Creation Pre-Run Snapshot & Full Revert

- **Status**: Accepted
- **Date**: 2026-05-23 (PO decisions locked) / 2026-05-25 (ADR written + accepted)
- **Author**: IT Architect persona (database-engineer lens), on behalf of the PO, encoding the 2026-05-23 ten-persona grooming pass and the PO-locked decisions of the same date. This ADR **documents and makes implementable** seven PO-locked decisions; it is **not** the place to relitigate them.
- **Bead**: `enhancedchannelmanager-uc51o.1` (ADR; child of epic `enhancedchannelmanager-uc51o`)
- **Related**:
  - `enhancedchannelmanager-uc51o` — Epic: Pre-run snapshot of all channels + streams before auto-creation, for full revert. Carries the full PO-grooming record this ADR encodes.
  - `enhancedchannelmanager-uc51o.2` — Schema + pre-run capture + read-only API (MVP). **Blocked on this ADR.** Consumes §D6 (schema), §D2 (capture point), §D3 (what-to-snapshot).
  - `enhancedchannelmanager-uc51o.3` — Retention / pruning. Consumes §D7. **Ships with / before .2 in production** (retention is not deferrable — see §D7 rationale).
  - `enhancedchannelmanager-uc51o.4` — Restore algorithm + endpoint. Consumes §D4, §D5, §D8.
  - `enhancedchannelmanager-uc51o.5` — Unify rollback to use snapshot when present, else fall back. Consumes §D8.
  - `enhancedchannelmanager-uc51o.6` — MCP `restore_auto_creation_snapshot` tool + `has_snapshot` on list-executions. Consumes §D8.
  - `enhancedchannelmanager-uc51o.7` — Auto-creation revert UI (execution history). Consumes §D5 (optimistic-overwrite warning), §D8 (partial-failure surfacing).
  - `enhancedchannelmanager-757hc` — **CLOSED/merged**: Auto-creation router endpoints now carry `RequireAdminIfEnabled` (rollback + mutating ops). The new restore endpoint in §D8 inherits this guard; see §D8.
  - `docs/database_migrations.md` — Alembic authoring guide; the `AutoCreationSnapshot` table lands as migration **0022** per that doc.
  - `docs/adr/ADR-007-session-telemetry-retention.md` — Retention precedent: "bounded by construction" framing, age-window + nightly-prune-before-VACUUM pattern, count/size metrics. §D7 models on it.
  - `docs/adr/ADR-008-interactive-stream-dedup.md` — Sibling ADR; partial-failure-never-silent contract and Dispatcharr-as-source-of-truth (no local FK across the process boundary) framing this ADR reuses.
  - `docs/architecture.md` — system overview (auto-creation pipeline internals); update on acceptance to reference the snapshot/restore boundary.

> **Terminology note (post-decision):** the "Auto-Creation" feature this ADR governs was later renamed **Channel Pipeline** (`enhancedchannelmanager-3udrl`). This ADR is a historical decision record and is left as originally written — table names, module paths, config keys, and endpoint paths quoted below reflect the code as it existed at the time this ADR was accepted. Where current docs and code refer to the feature by name, they now say "Channel Pipeline"; the persisted identifiers this ADR references (`auto_creation_snapshots` table, `auto_creation_snapshot_days`/`auto_creation_snapshot_max` settings, etc.) remain unchanged in the codebase today.

## Context

When the auto-creation pipeline runs, it can create, modify, and re-assign streams across hundreds of channels (570+ on a representative operator instance). The current **rollback** facility is narrow: it only undoes what a run **created**, and surgically un-merges streams a run **added** to pre-existing channels. It cannot restore the broader pre-run channel↔streams state.

### The gap this epic fills (grounded in code)

`AutoCreationExecution` (`backend/models.py:2074`) records two JSON columns for rollback:

- `created_entities` (`models.py:2111`) — populated in `RunContext.add_result()` (`backend/auto_creation_executor.py:107–121`): `{type:"channel"|"group", id, name}` for each entity the run **created**.
- `modified_entities` (`models.py:2113`) — populated at `auto_creation_executor.py:143–148`: `{type, id, name, previous: previous_state}`, where `previous_state` for a stream-merge is `{"streams": current_streams.copy()}` captured **just before** the merge (`executor.py:974`).

`rollback_execution(execution_id, ...)` (`backend/auto_creation_engine.py:266–372`) then:

1. Refuses (rather than silently no-ops) when a run has zero created **and** zero modified entities (`engine.py:307–321`) — a legacy/no-op run.
2. Deletes created entities in reverse order (`engine.py:323–325`).
3. Prefers a **surgical journal-driven un-merge** (`_journal_driven_unmerge`, `engine.py:336`) that removes **only** the stream IDs this run added, preserving streams a concurrent edit added afterward. If that handles the batch, the modified-entity restore below is **skipped** (running it would clobber the concurrent edits the surgical pass just preserved — `engine.py:328–335`).
4. Otherwise (legacy runs with no merge-journal entries) restores `modified_entities` in reverse order via `_rollback_modified_entity` (last-write-wins from each entry's `previous` snapshot — `engine.py:338–349`).

**What rollback does NOT restore** (the epic's confirmed gap, `uc51o` body):

- **Streams REMOVED from existing channels** — the executor's `remove_from_channel` / prune paths (`executor.py:2337`, `2498`) track `streams_removed` as a **count only** (`auto_creation_executor.py:142`), with no per-channel before-state recorded in `modified_entities` for the removal case. Rollback cannot re-add them.
- **The general pre-run channel↔streams state** — only entities the run **touched** have a `previous_state`; channels the run never touched have no recorded baseline. A run that, e.g., reorders or replaces a stream set on a channel through a non-merge path leaves no restorable record.
- **Cross-effects** — anything that depends on the full pre-run picture rather than the per-action deltas the executor happened to record.

So a run that merges/edits/removes streams against existing channels **cannot be fully reverted today** — only newly-created entities can be deleted, and only run-added merge streams can be surgically un-merged. This ADR adds a **fuller revert via a pre-run point-in-time snapshot** of the channel↔stream state.

### Why this is an architecture decision (warrants an ADR)

The snapshot/restore boundary touches: a **new persisted table** (storage shape, size, indexes), a **credential-safety** constraint (stream URLs embed Xtream-Codes credentials), a **retention** policy (an unbounded-growth risk on the hourly-M3U-refresh path), a **conflict policy** (restore overwrites post-run manual edits), and the **source-of-truth** relationship between ECM and Dispatcharr. Each is a contract that `uc51o.2`–`.7` consume. The PO locked the seven decisions below on 2026-05-23; this ADR turns each into buildable design.

## Decision

The seven PO-locked decisions (a)–(g) map to the design sections below:

| PO decision (2026-05-23) | Section |
|---|---|
| (a) Store STREAM IDs only, never URLs | §D1 |
| (b) Snapshot ALL channels EXCEPT Dispatcharr-auto-created-from-groups | §D3 |
| (c) Optimistic-overwrite restore + pre-revert warning; NO conflict-detect in v1 | §D5 |
| (d) Whole-run revert granularity | §D4 |
| (e) Retention modeled on M3USnapshot / ADR-007; ships WITH the feature | §D7 |
| (f) New `AutoCreationSnapshot` table — do NOT reuse M3USnapshot | §D6 |
| (g) Dispatcharr is source-of-truth; restore is a write-back; keep entity-rollback, new restore uses snapshot when present, else falls back | §D8 |

### D1 — Store STREAM IDs only, never stream URLs (credential safety) — PO decision (a)

The per-channel snapshot payload stores each channel's stream assignment as a **list of integer stream IDs** (`stream_ids: [int]`) — **never** stream objects and **never** stream URLs.

**Why this is a hard constraint, not a size optimization.** Xtream-Codes stream URLs embed live credentials in the path (`/live/<username>/<password>/<stream_id>.ts`). The backup ZIP scrubber `_scrub_journal_db_to_temp` (`backend/routers/backup.py:110`) redacts **only** `alert_methods.config` credential keys (`backup.py:141–159`) — it touches no other table. A snapshot table that stored stream URLs would therefore be **unscrubbed credential-at-rest** that leaks into:

- every backup ZIP (the scrubber would not redact it),
- the auto-creation debug bundle,
- any future raw-DB export.

This is the Security Engineer's SEC-1 (High) finding from grooming. Storing IDs is sufficient for restore: `update_channel(channel_id, {"streams": [ids]})` is the established full-replace primitive (used at `auto_creation_executor.py:979`, `2337`, `2423`, `2498`; `routers/channels.py:879/2247/2451/...`; `stream_prober.py:1991/2139`). Dispatcharr resolves IDs to URLs on its side. **IDs in, IDs out — credentials never cross the snapshot boundary.**

A deleted stream (an ID in the snapshot that no longer resolves in Dispatcharr at restore time) is a **partial failure surfaced per §D5**, not a silent drop.

### D2 — Capture point: before any mutation, in `run_pipeline`, dry-run skipped

The snapshot is captured in `AutoCreationEngine.run_pipeline` (`backend/auto_creation_engine.py:~107`), **after** `_load_existing_data()` (`engine.py:111`) loads the current channel list and **before** `_process_streams(...)` (`engine.py:177`) — the single call that performs all mutation. This ordering guarantees the snapshot reflects the **true pre-run state**.

- **Dry-run detection / skip.** `run_pipeline(dry_run: bool = ...)` is the flag (`engine.py:82`). When `dry_run` is True, **no snapshot is written** — a dry run mutates nothing, so there is nothing to revert and a snapshot would only consume storage. The capture is gated on `if not dry_run:`, mirroring the existing `if not dry_run:` rule-stats guard at `engine.py:213`. The execution `mode` column already records `"dry_run"` vs `"execute"` (`models.py:2081`); the snapshot is written only for `mode="execute"`.
- **Reuse the already-loaded channel list — no N+1.** `_load_existing_data()` (`engine.py:378–403`) calls `client.get_channels(page=..., page_size=100)` paginated into `self._existing_channels`. Dispatcharr's channel payload **embeds per-channel streams as a list of IDs**: the engine reads `ch.get("streams", [])` at `engine.py:138`, and the executor normalizes `s["id"] if isinstance(s, dict) else s` at `executor.py:918/2313/2386/2469`. So the snapshot is built from `self._existing_channels` **in memory** — there is **no** per-channel `get_channel_streams` call, no N+1. (If a future Dispatcharr version stops embedding streams in the list payload, the snapshot capture must fall back to `client.get_channel_streams(channel_id)` per channel — flag this at build time; the in-memory path is the contract today.)
- **Failure handling.** A snapshot-capture failure must **not** silently proceed into a mutating run with no revert safety net. The capture wraps in try/except; on failure it logs `[AUTO-CREATE-ENGINE]` at WARNING and the run proceeds **without** a snapshot (the run is still revertible via the legacy entity-rollback per §D8). The execution row records `has_snapshot=False` so the UI/MCP surface does not offer snapshot-restore for a run that has none. (Whether a capture failure should hard-abort the run is an explicit out-of-scope question for `uc51o.2` grooming; v1 logs-and-proceeds, matching the engine's existing best-effort `_load_existing_data` posture at `engine.py:400–403`.)

### D3 — Snapshot ALL channels EXCEPT Dispatcharr-auto-created-from-groups — PO decision (b)

**Snapshot every channel that is NOT auto-created by Dispatcharr from its M3U groups.** The identifying attribute is resolved below.

**How Dispatcharr-auto-created channels are identified (resolved):** the Dispatcharr channel object carries a boolean **`auto_created`** field (and a companion **`auto_created_by`**). Channels that Dispatcharr generated from its M3U groups' **auto-channel-sync** feature (the per-group `auto_channel_sync` setting — `backend/dispatcharr_client.py:542/561/566/717`) have `auto_created == True`. ECM already relies on this exact field:

- `backend/routers/channels.py:614` — `manual_channels = [ch for ch in all_channels if not ch.get("auto_created", False)]` filters them out of the lineup builder.
- `backend/routers/channels.py:1810` — `if channel.get("auto_created") and channel.get("channel_group_id") in group_ids:` selects them for the "clear auto-created flag" operation, which sets `{"auto_created": False, "auto_created_by": None}` (`channels.py:1846–1847`).

**The snapshot filter is therefore:** include a channel iff `not ch.get("auto_created", False)`.

**Rationale for excluding them.** Dispatcharr re-derives `auto_created=True` channels from its M3U groups on every refresh — they are **Dispatcharr-owned, regenerable** state, not ECM-managed state. Snapshotting and restoring them would (1) fight Dispatcharr's own sync (Dispatcharr is source-of-truth, §D8), (2) bloat the snapshot with the largest, most volatile slice of the channel list, and (3) restore a stream-set that Dispatcharr will immediately re-sync anyway. Manual / ECM-managed channels (the `auto_created=False` set) are the ones an auto-creation run mutates in ways Dispatcharr will **not** re-derive — those are exactly what needs revert protection.

**Edge case to handle in `uc51o.2`:** the `clear-auto-created` operation (`channels.py:1783`) can flip a channel from `auto_created=True` to `False` between runs. The snapshot captures whatever the flag says **at capture time** — a channel that was Dispatcharr-auto-created but has since been "claimed" as manual (flag cleared) **is** snapshotted from that point on. This is correct: once claimed, it is ECM-managed. No special handling needed; the flag read at capture time is authoritative.

### D4 — Whole-run revert granularity — PO decision (d)

Restore granularity is **whole-run**: a revert restores the **entire** snapshotted channel↔stream state captured before execution `{id}`. There is **no per-channel revert** in v1.

- The restore operates over **all** channels in the snapshot in one operation.
- Per-channel selective restore is an additive future enhancement (see Exit Path); it is **not** built for v1. Whole-run matches the operator's mental model ("undo that run") and avoids a partial-restore UI/consistency surface for v1.
- The snapshot is keyed 1:1 to an `AutoCreationExecution` (FK, §D6), so "the run" is the unit of both capture and restore.

### D5 — Optimistic-overwrite restore + explicit pre-revert warning; NO conflict-detection in v1 — PO decision (c)

Restore is **optimistic-overwrite**: it writes the snapshot's stream-set back to each channel via `update_channel`, **unconditionally overwriting** whatever the current state is — including any manual edits the operator made, or Dispatcharr drift, **after** the run but **before** the revert.

- **No conflict-detection in v1.** The restore does **not** compare current state against the snapshot to detect intervening edits, and does **not** offer a merge/three-way reconciliation. This is a deliberate v1 scope bound (PO decision (c)); conflict-detection is a Phase-2 upgrade (Exit Path).
- **Explicit pre-revert warning is mandatory.** Because restore silently overwrites post-run edits, the restore endpoint/MCP-tool/UI **MUST** surface an explicit warning before executing: *"Reverting will overwrite the current stream assignments of N channels with the state captured before this run. Any changes made after the run will be lost. This cannot be undone."* The UI affordance (`uc51o.7`) renders this as a confirmation dialog; the MCP tool (`uc51o.6`) documents it in the tool description and requires an explicit confirm parameter. This warning is the **only** mitigation for the overwrite risk in v1 — it is not optional.
- **Idempotent.** Re-running the same restore against the same snapshot is a no-op-equivalent (it writes the same stream-sets again). Restore must be safe to retry after a partial failure (§D8).

ADR-008 §D4's precedent applies: Dispatcharr is the source of truth across the process boundary; ECM does not attempt cross-process locking to prevent the race. The warning + optimistic-overwrite is the v1 contract; conflict-detection is the documented upgrade path when operator feedback shows intervening-edit loss is a real problem.

### D6 — New `AutoCreationSnapshot` table (do NOT reuse M3USnapshot) — PO decision (f)

A **new** table `AutoCreationSnapshot` lands in `backend/models.py` and migration **0022** (current Alembic head is `0021` — `backend/alembic/versions/20260524_0430_0021_auto_creation_executions_channels_touched.py`). It is **not** a reuse of `M3USnapshot` (`models.py:885`): `M3USnapshot` is a per-M3U-account playlist-change-detection artifact (`m3u_account_id`, `groups_data`, `total_streams`, `dispatcharr_updated_at`) with entirely different semantics, lifecycle, and FK. Reusing it would be a naming/semantic collision (Naming Discipline). `M3USnapshot` is the **structural precedent** for the retention-index pattern (§D7), not the table to overload.

**Schema:**

```python
class AutoCreationSnapshot(Base):
    """Point-in-time snapshot of the manual (non-Dispatcharr-auto-created)
    channel<->stream state captured BEFORE an auto-creation execution mutated
    anything, to enable a full whole-run revert (ADR-010)."""
    __tablename__ = "auto_creation_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 1:1 to the execution whose pre-run state this captures. CASCADE so a
    # pruned/deleted execution row takes its snapshot with it.
    execution_id = Column(
        Integer,
        ForeignKey("auto_creation_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Number of channels captured (denormalized for cheap list/size reporting
    # without parsing the BLOB; feeds the retention metric in §D7).
    channel_count = Column(Integer, default=0, nullable=False)
    # Serialized per-channel payload. JSON TEXT (the project convention for
    # snapshot/entity BLOBs — cf. AutoCreationExecution.created_entities at
    # models.py:2111 and M3USnapshot.groups_data at models.py:896).
    channels_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Unique 1:1 — one snapshot per execution. Also the FK lookup index.
        UniqueConstraint("execution_id", name="uq_auto_snapshot_execution"),
        # Age-window prune scan (§D7).
        Index("idx_auto_snapshot_time", snapshot_time.desc()),
    )
```

**Serialized per-channel payload shape** (`channels_data` is `json.dumps({"channels": [ ... ]})`), one entry per snapshotted (non-auto-created) channel:

```json
{
  "channels": [
    {
      "id": 1234,
      "name": "ESPN HD",
      "channel_group_id": 12,
      "epg_data_id": 5678,
      "tvg_id": "espn.us",
      "stream_ids": [9001, 9002, 9003]
    }
  ]
}
```

Field provenance (all read from the in-memory `self._existing_channels` payload, §D2 — no extra API calls):

| Payload field | Source channel key | Notes |
|---|---|---|
| `id` | `ch["id"]` | Dispatcharr channel id; the restore target. |
| `name` | `ch.get("name")` | Display/audit; not strictly required for restore but cheap and useful for the UI list and partial-failure messages. |
| `channel_group_id` | `ch.get("channel_group_id")` | Key metadata; restored if drifted (`channels.py:1815` precedent). |
| `epg_data_id` | `ch.get("epg_data_id")` | The channel↔EPG link (`channels.py:2136` sets `epg_data_id`). |
| `tvg_id` | `ch.get("tvg_id")` | The tvg/guide id (`executor.py:1090/1096`). |
| `stream_ids` | `[s["id"] if isinstance(s, dict) else s for s in ch.get("streams", [])]` | **IDs only** per §D1. Normalization matches the executor's own coercion (`executor.py:918`). |

`logo_url` / `channel_number` are **not** snapshotted in v1 — they are not part of the channel↔stream state this epic restores, and `channel_number` re-assignment is its own pipeline pass. If a future need appears, the payload is a JSON object and can be widened additively without a migration (existing rows simply lack the new key).

**`has_snapshot` surfacing:** `uc51o.2` exposes whether an execution has a snapshot. Implement as a derived boolean on the execution list/detail response (`backend/routers/auto_creation.py:1228/1266`) computed from the existence of an `AutoCreationSnapshot` row for that `execution_id` — **not** a new column on `AutoCreationExecution` (avoids a denormalized flag that can drift from the FK truth). The `(execution_id)` unique index makes this lookup O(1).

**Migration discipline (per `docs/database_migrations.md`):** migration 0022 is idempotent (per-statement guards against a pre-existing table from `create_all()`, smart-bootstrap-fast-path safe, following the bd-5w6jz pattern ADR-008 §D8 cites). `downgrade()` drops the table (the data is a recoverable convenience artifact, not source-of-truth — Dispatcharr is, §D8 — so the drop is acceptable and reversible-in-spirit). Add `AutoCreationSnapshot` to the `test_baseline_matches_metadata_no_drift` drift test. Name the `UniqueConstraint` explicitly (`uq_auto_snapshot_execution`) per the SQLite-gotchas section of the migrations guide.

### D7 — Retention: age window + count cap, ships WITH the feature — PO decision (e)

Retention is **not deferrable**: a per-run snapshot of ~570 channels serializes to roughly 50 KB–3 MB, and with `run_on_refresh=True` on an hourly M3U refresh, snapshots accumulate every hour. Without a prune this is a silent SQLite-growth bomb (the same class of problem ADR-007 §D7 and the `auto_creation_executions` BLOB-bloat finding — `tasks/cleanup.py:56–61`, 77% of one operator's DB — exist to prevent). **Retention ships with `uc51o.2`/`.3`, before this reaches a production instance.**

Modeled on `M3USnapshot` (the count-cap precedent) and ADR-007 (the age-window + prune-before-VACUUM + metric precedent), the policy has **two** bounds, whichever fires first:

- **Age window** — snapshots older than `auto_creation_snapshot_days` (default **30 days**, matching the `auto_creation_blob_days=30` cadence already in `tasks/cleanup.py:104`) are pruned by `snapshot_time`.
- **Count cap** — at most `auto_creation_snapshot_max` snapshots are retained (default **50**). When the count exceeds the cap, the **oldest** are pruned regardless of age. This bounds the hourly-refresh worst case (50 × up-to-3 MB ≈ 150 MB ceiling) independent of the age window.

**Where the prune runs:** a new step in the existing **`CleanupTask`** (`backend/tasks/cleanup.py`), inserted **before** the VACUUM step (VACUUM is the last step, currently step 7 — `cleanup.py:389`). It follows the established per-step shape there: own try/except, `session.commit()`, log line, count into `deleted_counts`. Two config knobs are added to `CleanupTask.get_config()` / `update_config()` (`cleanup.py:109–136`) alongside the existing retention knobs. The CASCADE FK (§D6) means deleting the execution row (e.g., the existing BLOB-prune does not delete rows, but any future execution-row prune would) takes its snapshot with it; the snapshot prune here deletes `AutoCreationSnapshot` rows directly by the age/count bounds.

**Metric:** emit a count/size signal so growth is observable, consistent with ADR-007 D6 and the existing `ecm_database_size_bytes` gauge (`cleanup.py:419–430`). At minimum, log the number of snapshots pruned and the current retained count; ideally publish `ecm_auto_creation_snapshot_count` / `ecm_auto_creation_snapshot_bytes` gauges (the `channel_count` denormalized column and a `LENGTH(channels_data)` sum make the size cheap to compute without parsing the BLOB). The SRE owns the alert threshold; the DBA owns that the prune is correct and the cap is right; the engineer owns the implementation.

**Prune batching note:** snapshot counts are bounded (tens, not millions) by the count cap, so the ADR-007 §D3 batched-DELETE-to-dodge-the-write-lock concern does **not** apply here — a single `DELETE` is fine. (If the cap is ever raised to thousands, revisit.)

### D8 — Dispatcharr is source-of-truth; restore is a write-back; keep entity-rollback, new restore uses snapshot when present, else falls back — PO decision (g)

**Dispatcharr is the system of record for channel state.** The snapshot is an ECM-local convenience record of a past Dispatcharr state; **restore is a write-back** to Dispatcharr (a sequence of `update_channel` PATCHes), not a local mutation. No local FK to Dispatcharr channel ids is created (ADR-008 §D4 precedent — an FK across the process boundary is not enforceable; the snapshot stores Dispatcharr ids as plain integers in the JSON payload, not as DB FKs).

**Two revert surfaces coexist:**

1. **Existing entity-rollback** (`rollback_execution`, `engine.py:266`) — **unchanged**. It deletes created entities + surgically un-merges run-added streams (§Context). It remains the path for runs that have **no** snapshot (legacy runs, dry-run-then-claimed runs, capture-failure runs per §D2).
2. **New snapshot-restore** — the fuller revert. When an execution **has** a snapshot, the unified revert (`uc51o.5`) uses snapshot-restore; **else** it falls back to the existing entity-rollback. This is PO decision (g)'s "uses the snapshot when present, else falls back to delete-created-only."

**New endpoint:** `POST /auto-creation/executions/{id}/restore-snapshot`, landing in `backend/routers/auto_creation.py` directly alongside the existing `POST /executions/{id}/rollback` (`auto_creation.py:1298`). It **MUST** carry `_admin=RequireAdminIfEnabled` — the same guard the rollback endpoint now carries (`auto_creation.py:1299`), established by the now-merged **bd-757hc** (the router imports `from auth import RequireAdminIfEnabled` at `auto_creation.py:22` and gates every mutating endpoint). A destructive bulk write-back without the admin guard would reintroduce exactly the gap bd-757hc closed. The endpoint writes a journal entry (`category="auto_creation"`, `action_type="restore_snapshot"`) mirroring the rollback endpoint's journaling (`auto_creation.py:1322–1328`).

**Restore ALGORITHM (the contract for `uc51o.4`):**

1. **Load** the `AutoCreationSnapshot` for `execution_id` (404 if none — and the caller is told to use `/rollback` instead). Refuse if the execution `mode == "dry_run"` (a dry run has no snapshot anyway) or is already `rolled_back`, mirroring the rollback guards at `engine.py:286–290`.
2. **Surface the §D5 pre-revert warning** (enforced at the UI/MCP layer; the endpoint may require an explicit `confirm=true` parameter so a raw API call cannot skip the acknowledgement).
3. **Delete run-created channels first.** Read `execution.get_created_entities()` (`models.py:2141`) and delete each created `type=="channel"` (and `type=="group"`) via the existing `_rollback_created_entity` path (`engine.py:324–325`). This removes channels that did not exist at snapshot time, so the restore does not leave orphans. (Created entities are by definition **not** in the snapshot, which captured the pre-run state.)
4. **Per snapshot channel, full-replace the stream set and key metadata.** For each entry in `channels_data["channels"]`:
   - `await client.update_channel(channel["id"], {"streams": channel["stream_ids"], ...})` — the **full-replace** primitive (§D1). This re-adds streams the run removed, removes streams the run added, and reorders to the snapshot order, in one PATCH per channel.
   - Restore key metadata that may have drifted: `channel_group_id`, `epg_data_id`, `tvg_id` (from the payload). These are included in the same PATCH body.
5. **Idempotency.** Re-running the restore re-issues the same PATCHes → same end state. Safe to retry after a partial failure.
6. **Collect and SURFACE partial failures — never silent success.** A channel id in the snapshot that 404s in Dispatcharr (channel deleted since the run), or a stream id that no longer resolves, is a **per-channel failure** collected into a `failed_channels: [{id, name, error}]` list and returned in the response (alongside `restored_channels` count and `removed_channels` count). The operation does **not** abort on the first failure — it attempts every channel and reports the full result. The response shape mirrors the partial-success pattern already used by `clear-auto-created` (`channels.py` returns `updated_channels` / `failed_channels`). A restore that touched 200 channels and failed on 3 returns success-with-warnings carrying those 3, **not** a blanket 200 or a blanket 500. (ADR-008's "audit is real, not nominal" / never-silent contract.)
7. **Mark + journal.** On completion, write the journal entry (step above) recording `restored`/`removed`/`failed` counts. Whether to set `execution.status="rolled_back"` (sharing the rollback terminal state) vs a distinct `"restored"` status is an implementation choice for `uc51o.4`/`.5` to settle when it unifies the two surfaces; this ADR requires only that the terminal state and idempotency guards (already-reverted → refuse) are consistent between the two paths.

**MCP + UI:** `uc51o.6` adds `restore_auto_creation_snapshot(execution_id, confirm)` mirroring this endpoint and the §D5 warning; `uc51o.7` adds the execution-history revert affordance with the confirmation dialog and partial-failure surfacing. Both consume this algorithm verbatim.

## Alternatives Considered

| # | Option | Pros | Cons | Portability | Cost |
|---|---|---|---|---|---|
| 1 | **Chosen — IDs-only snapshot of non-auto-created channels, optimistic-overwrite whole-run restore, new table + retention, write-back to Dispatcharr** | Closes the real gap (removed/edited streams on existing channels); no credential-at-rest; bounded by construction; reuses `update_channel` + `CleanupTask` + the in-memory channel list (no N+1); keeps the proven entity-rollback as fallback | Optimistic-overwrite can lose post-run manual edits (mitigated by the §D5 warning); a new table + prune is new surface; whole-run only in v1 | High — pure SQLite + existing client; no new infra | One model + migration 0022, one capture hook, one restore endpoint, one CleanupTask step |
| 2 | **Store full stream objects (incl. URLs) for richer restore** | Could restore a stream even if Dispatcharr deleted it; self-contained | **Credential-at-rest** — URLs embed XC creds and the backup scrubber (`backup.py:110`) only covers `alert_methods`, so every backup/debug-bundle leaks creds (SEC-1 High); larger rows; Dispatcharr is source-of-truth anyway so a deleted stream cannot be re-created from a URL alone | High | Same build cost, unacceptable security cost — **rejected** |
| 3 | **Snapshot only run-touched channels (not all)** | Smaller snapshots; cheaper | Misses cross-effects and the general pre-run baseline — the exact gap the epic exists to close; "touched" is defined by which deltas the executor recorded, which is the incomplete record we are trying to supersede | High | Lower storage, but fails the acceptance criterion |
| 4 | **Snapshot ALL channels including Dispatcharr-auto-created** | Simplest filter (none) | Fights Dispatcharr's own group-sync (it re-derives `auto_created` channels every refresh); bloats the snapshot with the largest, most volatile slice; restoring them is pointless (Dispatcharr re-syncs) — violates source-of-truth (§D8) | High | Higher storage for zero revert value on that slice |
| 5 | **Conflict-detection (three-way merge) in v1** | No silent loss of post-run edits | Large scope (detect drift, present diffs, merge UI); the operator's mental model is "undo the run"; YAGNI until intervening-edit loss is shown to be a real problem | High | Large — turns a Medium restore into a Large; deferred to Exit Path |
| 6 | **Per-channel revert granularity in v1** | Finer control | Partial-restore UI + consistency surface; whole-run matches "undo that run"; additive later without breaking whole-run | High | Higher UI/consistency cost for v1; deferred |
| 7 | **Reuse `M3USnapshot` table** | One fewer table | Semantic/naming collision (per-M3U-account playlist-change vs per-execution channel-state); different FK, lifecycle, retention; overloading it makes both meanings harder to read | High | Lower table count, higher confusion cost — PO rejected (decision f) |
| 8 | **Replace the existing entity-rollback entirely with snapshot-restore** | One revert path | Legacy/dry-run/capture-failure runs have no snapshot and would lose their only revert; the surgical journal-driven un-merge preserves concurrent edits in a way a blind snapshot-overwrite does not | High | Regression risk — keep both, snapshot-when-present (decision g) |
| 9 | **Local FK from snapshot stream/channel ids to a synced Dispatcharr cache** | Catches deleted targets at write time | Requires a reconciliation job (ADR-008 §D4 rejected this same pattern); does not prevent the race; Dispatcharr is source-of-truth | High | New sync job + failure modes for no correctness gain |

## Consequences

### Positive

- **The real gap closes.** Streams removed from / edited on existing channels are restorable, not just newly-created entities. The acceptance criterion ("created removed, modified restored, removed re-added") is met by the full-replace restore.
- **No credential-at-rest.** IDs-only (§D1) means the snapshot table is safe to ship in backups and debug bundles without extending the scrubber. The SEC-1 finding is closed by construction.
- **Bounded by construction.** Age window + count cap (§D7), pruned in `CleanupTask` before VACUUM, with a size metric — the hourly-refresh growth bomb is defused before it can detonate. ADR-007's framing applied.
- **No N+1, no new infra.** Capture reuses the already-loaded in-memory channel list (§D2); restore reuses `update_channel`; prune reuses `CleanupTask`. SQLite, the existing client, the existing scheduled-task loop — nothing new to operate.
- **The proven rollback survives.** The surgical journal-driven un-merge and created-entity teardown remain the fallback for snapshot-less runs (§D8). No regression for legacy executions.
- **Admin-gated write-back.** The restore endpoint inherits bd-757hc's `RequireAdminIfEnabled`; a bulk destructive write-back cannot be triggered by a narrowly-scoped token.
- **Partial failures are visible.** A restore that fails on a few channels reports exactly which ones — never a silent partial success (§D8 step 6).

### Negative

- **Optimistic-overwrite can lose post-run edits.** A manual edit made after the run and before the revert is silently overwritten. The §D5 pre-revert warning is the **only** mitigation in v1 — if an operator dismisses it carelessly, data is lost. Conflict-detection (Exit Path) is the real fix when evidence shows this bites.
- **Whole-run only.** No "revert just channel X" in v1; an operator who wants to undo one channel's worth of a run must revert the whole run or edit manually. Additive later.
- **A deleted stream is not recoverable from the snapshot.** IDs-only means a stream Dispatcharr deleted after the run cannot be re-created (only re-referenced if it still exists). This surfaces as a per-channel partial failure, not a silent gap — but it is a real limit of the source-of-truth model (option 2's "fix" is worse).
- **New table + prune is new surface.** One more table in the drift test, one more `CleanupTask` step that can fail or fall behind. Mitigated by the metric (§D7) and the count cap's hard ceiling.
- **Capture failure degrades silently to no-snapshot.** Per §D2, a capture failure lets the run proceed with only legacy-rollback safety. The `has_snapshot=False` surfacing makes this visible, but an operator expecting full revert on a run whose capture failed gets the narrower fallback. (Hard-abort-on-capture-failure is an open `uc51o.2` question.)

### Neutral / Out of Scope

- **The conflict-detection design** (three-way diff, merge UI) is explicitly Phase-2 (§D5, Exit Path) — not specified here beyond "additive."
- **Per-channel revert** is deferred (§D4, Exit Path).
- **`logo_url` / `channel_number` snapshotting** is out of scope (§D6) — additive via the JSON payload if needed.
- **Whether a capture failure should hard-abort the mutating run** is an `uc51o.2` grooming question (§D2); v1 logs-and-proceeds.
- **The exact terminal status** (`rolled_back` shared vs `restored` distinct) is left to `uc51o.4`/`.5` (§D8 step 7); this ADR fixes only the guards and idempotency.

## Exit Path

If the chosen design proves wrong:

1. **Soft — tune retention.** `auto_creation_snapshot_days` / `auto_creation_snapshot_max` are config knobs read by the `CleanupTask` prune (§D7). Raise/lower with no schema change.
2. **Additive — conflict-detection (Phase 2).** Add a pre-restore drift check that compares current Dispatcharr state against the snapshot and presents the diff; the optimistic-overwrite path stays as the "force" option. No schema change (the snapshot already holds the comparison baseline); a new compare step + UI. This is the documented upgrade for the §D5 overwrite risk when operator feedback shows intervening-edit loss is real.
3. **Additive — per-channel revert.** The snapshot already stores per-channel rows; a per-channel restore is a filter over the same `channels_data` payload + a narrower endpoint/UI. No schema change.
4. **Additive — widen the payload.** `logo_url`, `channel_number`, stream-profile, or other metadata can be added to the JSON `channels` entries; existing rows simply lack the new key (forward-compatible read).
5. **Hard — Dispatcharr stops embedding streams in the channel-list payload.** If a future Dispatcharr version drops `streams` from `get_channels`, capture must fall back to per-channel `get_channel_streams` (re-introducing the N+1 §D2 avoids today). Detect at build/upgrade time; the snapshot shape and restore algorithm are unaffected — only the capture-read path changes.

No vendor relationship to unwind; no external dependency introduced.

## Open Questions

### Resolved inline (no PO action needed)

- **What identifies a Dispatcharr-auto-created channel?** → The `auto_created` boolean on the channel (with `auto_created_by`); filter is `not ch.get("auto_created", False)` (§D3). Grounded at `channels.py:614/1810`.
- **N+1 on capture?** → No. Dispatcharr embeds `streams` (IDs) in the channel-list payload; capture builds from the in-memory `self._existing_channels` (§D2). Grounded at `engine.py:138`, `executor.py:918`.
- **URLs or IDs?** → IDs only — credential safety; the backup scrubber covers only `alert_methods` (§D1). Grounded at `backup.py:110`.
- **Reuse M3USnapshot?** → No; new `AutoCreationSnapshot` table, migration 0022 (§D6). PO decision (f).
- **Conflict policy?** → Optimistic-overwrite + mandatory pre-revert warning; no conflict-detection in v1 (§D5). PO decision (c).
- **Granularity?** → Whole-run (§D4). PO decision (d).
- **Retention?** → Age window (30d default) + count cap (50 default), pruned in `CleanupTask` before VACUUM, with a size metric; ships with the feature (§D7). PO decision (e).
- **Restore vs existing rollback?** → Keep entity-rollback; snapshot-restore when present, else fall back; restore is a write-back to Dispatcharr (source-of-truth); admin-gated (§D8). PO decision (g).
- **Where does the restore endpoint guard come from?** → `RequireAdminIfEnabled`, established by the merged bd-757hc; the new endpoint inherits it (§D8). Grounded at `auto_creation.py:22/1299`.

### PO decisions — locked 2026-05-23 (documented here, not relitigated)

1. **(a) Stream IDs only, never URLs.** Credential safety. Final.
2. **(b) Snapshot all channels except Dispatcharr-auto-created-from-groups** (`auto_created==True`). Final.
3. **(c) Optimistic-overwrite + pre-revert warning; no conflict-detect in v1.** Final.
4. **(d) Whole-run revert granularity.** Final.
5. **(e) Retention (age window + count cap) ships with the feature**, modeled on M3USnapshot / ADR-007. Final.
6. **(f) New `AutoCreationSnapshot` table; do NOT reuse M3USnapshot.** Final.
7. **(g) Dispatcharr is source-of-truth; restore is a write-back; keep entity-rollback; snapshot-when-present, else fall back.** Final.

### Flagged for `uc51o.2` grooming (implementation decisions, not blocking this ADR)

- **Capture-failure policy:** v1 logs-and-proceeds (snapshot-less run, legacy rollback only, `has_snapshot=False`). Should a capture failure instead hard-abort the mutating run? Open. (§D2)
- **Terminal status naming:** share `rolled_back` or introduce `restored`? Settled when `uc51o.5` unifies the two revert surfaces. (§D8)

## References

- Bead `enhancedchannelmanager-uc51o.1` — this ADR's tracker
- Bead `enhancedchannelmanager-uc51o` — epic; the PO-grooming record this ADR encodes
- Beads `enhancedchannelmanager-uc51o.2` … `.7` — the sub-beads that consume this ADR (schema/capture/API; retention; restore; unify; MCP; UI)
- Bead `enhancedchannelmanager-757hc` — CLOSED: auto-creation router admin guards (the restore endpoint inherits `RequireAdminIfEnabled`)
- `backend/auto_creation_engine.py:266–372` — `rollback_execution` (the existing entity-rollback this ADR keeps as fallback)
- `backend/auto_creation_engine.py:107–177` — `run_pipeline` capture point (after `_load_existing_data`, before `_process_streams`); `:138` — `ch.get("streams", [])` embedding (no N+1)
- `backend/auto_creation_executor.py:107–148` — `created_entities` / `modified_entities` capture; `:142` — `streams_removed` count-only (the gap); `:918` — stream-id coercion; `:979` — `update_channel({"streams": ...})` full-replace
- `backend/models.py:2074` — `AutoCreationExecution` (FK target); `:885` — `M3USnapshot` (retention-index precedent, NOT reused)
- `backend/routers/channels.py:614` — `auto_created` filter; `:1810/1846` — `auto_created`/`auto_created_by` field usage; `:2136` — `epg_data_id`
- `backend/dispatcharr_client.py:334` — `update_channel` PATCH; `:273` — `get_channels` (embeds streams); `:542/717` — `auto_channel_sync` group setting
- `backend/routers/backup.py:110` — `_scrub_journal_db_to_temp` (covers only `alert_methods` → the IDs-only rationale)
- `backend/routers/auto_creation.py:22` — `from auth import RequireAdminIfEnabled`; `:1298/1299` — existing rollback endpoint + guard (the restore endpoint's sibling)
- `backend/tasks/cleanup.py` — `CleanupTask` (the prune host; step 4 BLOB prune precedent at `:226–280`, VACUUM last at `:389`, size metric at `:419–430`)
- `backend/alembic/versions/20260524_0430_0021_auto_creation_executions_channels_touched.py` — current Alembic head (0021); the snapshot migration is 0022
- `docs/database_migrations.md` — Alembic authoring guide (idempotency, drift test, named constraints)
- `docs/adr/ADR-007-session-telemetry-retention.md` — retention/prune/metric precedent
- `docs/adr/ADR-008-interactive-stream-dedup.md` — Dispatcharr-source-of-truth, no-cross-process-FK, never-silent-partial-failure precedent

## Revision History

| Date | Bead | Change | Rationale |
|---|---|---|---|
| 2026-05-25 | `enhancedchannelmanager-uc51o.1` | Proposed + accepted | Encodes the seven PO-locked decisions (2026-05-23) as buildable design; hard prerequisite for `uc51o.2`–`.7` |
