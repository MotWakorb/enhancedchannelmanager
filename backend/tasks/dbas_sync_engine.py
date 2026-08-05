"""One-way cross-instance sync ENGINE CORE — config categories (epic ``i39wu``).

Bead ``enhancedchannelmanager-tjaey``. Architecture: [ADR-013](../../docs/adr/
ADR-013-cross-instance-live-sync.md) S1/S3/S4/S5/S7/S9. Security: threat model
``docs/security/threat_model_dbas_import.md`` §11 Addendum D (D2/D3/D5/D8/D9).

What this is (the proven thesis — spike ``xp6mp``)
--------------------------------------------------
Sync is **"restore over HTTP"**. The DBAS restore orchestrator
(``dbas.restore_orchestrator.run_restore`` / ``run_dry_run``) and its importers
take the Dispatcharr ``client`` as an injected parameter — the ONLY coupling to
"local" is a single ``get_client()`` call in the archive-restore task. So sync:

1. gathers the LOCAL source-A config (the SAME ``_gather_dispatcharr_sections``
   pointed at ``get_client()`` the backup builder uses),
2. REDACTS it to topology-only via the shared ``_REDACT_KEYS`` deep redactor
   (no secrets on the wire — Addendum D D2),
3. maps each category to its :class:`EntityType` (the SAME
   ``restore_artifact._SECTION_TO_ENTITY`` table the archive decoder uses),
4. assembles an :class:`~dbas.preflight.ImportPlan` whose manifest carries
   ``schema_version = BACKUP_SCHEMA_VERSION`` (the orchestrator runs the .17
   pre-flight gate; without the stamp it refuses the plan — spike empirical find),
5. and runs the UNCHANGED orchestrator against a remote (dest-B) client built
   from a ``SyncTarget`` row (``dbas_sync_client.make_remote_client``).

There are **zero edits to ``backend/dbas/``** — the orchestrator + importers are
reused as-is. The only new code here is the live-source plan reader, the
config-only step registry, ``run_sync``, and the shared never-sync constant.

Scope of THIS engine (ADR-013 phasing / S9)
-------------------------------------------
CONFIG categories (bead ``tjaey``): ``m3u_accounts``, ``epg_sources``,
``channel_groups``, ``channel_profiles``, ``stream_profiles``.

CHANNELS + STREAMS (bead ``kcxie``, Phase-2): the channels category is gathered
WITH its embedded streams and synced AFTER the config categories, through the
SAME reused ``import_channels`` importer, but with the spike ``xp6mp`` DBA
collision-safe floor applied for the continuous-sync context:

* **Channels (ruling 1a)** — a ``(name, channel_number)`` name match where the
  number is null/absent on BOTH sides is AMBIGUOUS and is surfaced as a
  ``CONFLICT`` (failed-with-reason), never a silent ``ALREADY_EXISTS_IDENTICAL``.
  That fix lives uniformly in ``dbas/importers/channels.py`` (it was a latent
  one-shot bug); this engine simply reuses the corrected importer.
* **Streams (ruling 1b)** — the embedded-stream matcher is FLOORED at Tier-3
  exact-normalized for the sync path. Tier-4 fuzzy (``token_set_ratio``) is
  opt-in per ``SyncTarget`` via ``fuzzy_stream_matching`` (default off); when on,
  a fuzzy hit is flagged LOW-CONFIDENCE in ``report.notes``, never a silent
  ``updated``. The flag threads from the target row into ``import_channels`` via
  its ``allow_fuzzy_stream_match`` parameter.

LOGOS are OPT-IN per target (bead ``7ipq2.1``), not per-cycle-unconditional
(ADR-013 S9): the logos importer carries a DESTRUCTIVE ``clear_existing``
bulk-delete plus a per-logo streaming-upload cost that does not belong in the
default per-cycle slice. The guarded slice this engine ships is exactly the S9
exit path: a ``SyncTarget.sync_logos`` flag (default OFF); when ON the LOGO
category is assembled METADATA-ONLY (never bytes in the plan) and the REUSED
logos importer runs with ``clear_existing`` hard-disabled (the sync path can
NEVER bulk-delete B's logos) and a lazy ``content_provider`` that reads each
MISSED logo's file from the local backup source dir one at a time (D8
streaming: match first, hydrate misses only, one payload live at a time).

Users NEVER sync (D3). The deferred auto-sync / EPG-download phase is **not** run
per cycle (S9) — the step registry passes a deferred-apply no-op to the
orchestrator.

This module is the ENGINE FUNCTION. The scheduled-task wrapper + manual trigger
(``TaskScheduler`` subclass, overlap guard) is a separate bead (``5gzg5``);
``run_sync`` is kept callable + testable so that wrapper is a thin shell.

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log or report field.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import posixpath
from pathlib import Path
from typing import Optional

import journal
from dbas.preflight import (
    CHANNEL_FK_FIELDS,
    ImportPlan,
    NAME_UNIQUE_CATEGORIES,
    PlanCategory,
)
from dbas.restore_artifact import _SECTION_TO_ENTITY
from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import (
    ApplyContext,
    ImporterCallable,
    ImporterStep,
    _importer_step_builders,
    new_restore_id,
    run_restore,
)
from dbas.importers.channels import import_channels
from dbas.importers.logos import import_logos
from routers import backup as backup_mod
from routers.backup import (
    BACKUP_SCHEMA_VERSION,
    _gather_dispatcharr_sections,
    _redact_credentials_deep,
)
from tasks.dbas_sync_client import make_remote_client, sync_freshness_reason

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared NEVER-SYNC constant — code-enforced (mirrors the always-on _REDACT_KEYS).
# ---------------------------------------------------------------------------

# The categories that MUST NEVER appear in a sync plan, unconditionally — no
# settings key, no opt-in (Addendum D D3, ADR-013 S3). Continuous one-way push of
# ``users`` would repeatedly overwrite B's privilege flags / lock out B's
# operator. This constant is imported by the plan assembler AND its test so the
# exclusion is enforced at code level, exactly like the SSRF denylist — not soft
# scope a future edit could erode.
SYNC_NEVER_CATEGORIES: frozenset[str] = frozenset({"users"})

# Credential-class columns that must never be assembled onto the wire either
# (the SyncTarget credential-freshness columns + raw credentials). These are not
# Dispatcharr config sections, but they are named here so the never-sync surface
# is one auditable constant. Defence in depth alongside the _REDACT_KEYS deep
# redactor (D2) that strips secret VALUES from whatever is gathered.
SYNC_NEVER_CREDENTIAL_COLUMNS: frozenset[str] = frozenset(
    {"credentials", "credential_version", "token_revoked_at"}
)

# The CONFIG categories this bead (tjaey) syncs — topology config only. Channels
# / streams / logos are bead kcxie; users are never (above). Each maps to an
# EntityType via _SECTION_TO_ENTITY (the same table the archive decoder uses).
SYNC_CONFIG_CATEGORIES: frozenset[str] = frozenset(
    {
        "m3u_accounts",
        "epg_sources",
        "channel_groups",
        "channel_profiles",
        "stream_profiles",
    }
)

# Bead kcxie adds the CHANNELS category (with embedded streams). It is gathered
# separately from the config sections (channels are not a backup RESTORABLE_SECTION
# the config gather knows) and synced AFTER config, with the collision-safe floor
# (ruling 1a/1b). LOGOS are deliberately NOT here (ADR-013 S9 — destructive
# clear_existing + streaming-upload cost is not a per-cycle slice).
SYNC_CHANNEL_CATEGORIES: frozenset[str] = frozenset({"channels"})

# The UNCONDITIONAL per-cycle sync surface = config + channels. Logos are a
# separate OPT-IN set (below); users are never synced (D3). Exposed as one
# auditable constant.
SYNC_ALL_CATEGORIES: frozenset[str] = SYNC_CONFIG_CATEGORIES | SYNC_CHANNEL_CATEGORIES

# Logos are OPT-IN per SyncTarget (``sync_logos``, default off — bead 7ipq2.1,
# the ADR-013 S9 exit path). Deliberately NOT part of SYNC_ALL_CATEGORIES: the
# unconditional per-cycle set stays exactly what S9 ratified, and the logo
# slice only runs for a target whose operator opted in. When it runs it is
# NEVER destructive (clear_existing is hard-disabled in the sync logos step).
SYNC_LOGO_CATEGORIES: frozenset[str] = frozenset({"logos"})


# ---------------------------------------------------------------------------
# Live-source plan reader — gather LOCAL config -> redact -> ImportPlan.
# ---------------------------------------------------------------------------


def _assert_no_never_sync(section_key: str) -> None:
    """Fail-closed guard: a never-sync category must never reach plan assembly.

    The config-category loop already excludes ``users`` by construction (it is
    not in :data:`SYNC_CONFIG_CATEGORIES`), but this explicit guard makes the D3
    invariant code-enforced at the assembly chokepoint — a future edit that adds
    a category to the gather cannot silently smuggle ``users`` onto the wire.
    """
    if section_key in SYNC_NEVER_CATEGORIES:
        raise AssertionError(
            "never-sync category %r must not be assembled into a sync plan (D3)"
            % section_key
        )


async def _gather_live_channels() -> list[dict]:
    """Gather source-A channels WITH their embedded streams (bead kcxie).

    Channels are not a backup ``RESTORABLE_SECTION`` the config gather knows, so
    this reader fetches them directly from the LOCAL ``get_client()`` (the same
    source the config gather reads). For each channel it resolves the channel's
    stream RECORDS (via :meth:`get_channel_streams`) and embeds them under the
    ``streams`` key the channels importer consumes — the same shape the DBAS
    archive decoder produces. Channel-profile memberships are passed through as
    the channel object carries them.

    Best-effort and fail-soft (mirrors :func:`_gather_dispatcharr_sections`): an
    unavailable local client, or a per-channel stream-fetch error, degrades to an
    empty/partial list and a WARN rather than crashing the sync cycle. No secret
    is logged (only channel names + counts).

    Returns:
        A list of channel records, each a dict with an embedded ``streams`` list
        of full stream dicts. Empty when the local client is unavailable.
    """
    # Resolve the LOCAL client through the routers.backup module (the SAME seam
    # _gather_dispatcharr_sections uses) so the gather is patchable in tests and
    # there is one local-client lookup point.
    client = backup_mod.get_client()
    if not client:
        logger.warning(
            "[SYNC] Local Dispatcharr not connected — channels slice skipped."
        )
        return []

    channels: list[dict] = []
    try:
        page = 1
        while True:
            resp = await client.get_channels(page=page, page_size=1000)
            results = resp.get("results", []) if isinstance(resp, dict) else resp
            page_items = [c for c in (results or []) if isinstance(c, dict)]
            channels.extend(page_items)
            # Stop on the last page (fewer than requested) or a non-paginated shape.
            if not isinstance(resp, dict) or len(page_items) < 1000:
                break
            page += 1
    except Exception as exc:  # noqa: BLE001 - fail-soft: no channels rather than crash
        logger.warning("[SYNC] Could not list source channels: %s", exc)
        return []

    # Resolve each channel's embedded stream records so the importer can match
    # them against B's streams. A per-channel failure leaves that channel with no
    # embedded streams (it still syncs its row) rather than aborting the cycle.
    for channel in channels:
        channel_id = channel.get("id")
        if channel_id is None:
            continue
        try:
            streams = await client.get_channel_streams(int(channel_id))
            channel["streams"] = [s for s in (streams or []) if isinstance(s, dict)]
        except Exception as exc:  # noqa: BLE001 - best-effort per channel
            logger.warning(
                "[SYNC] Could not fetch streams for channel '%s' (id=%s): %s",
                channel.get("name") or "<unknown>",
                channel_id,
                exc,
            )
            channel["streams"] = []

    logger.info("[SYNC] Gathered %d source channel(s) with embedded streams.", len(channels))
    return channels


# The private key a sync-assembled logo record uses to remember which file
# (relative to <CONFIG_DIR>/uploads/logos) backs it. Consumed ONLY by
# :func:`_load_logo_content_b64`; never a secret, never logged, never uploaded
# (the importer reads name/filename/size/content_b64 — this key is inert there).
_LOGO_REL_KEY = "_ecm_logo_rel"


def _sync_logos_dir() -> Path:
    """The local logo source dir — resolved through ``routers.backup`` at call
    time so tests patching ``backup_mod.CONFIG_DIR`` steer both the gather and
    the lazy content loader with one seam (the SAME dir the backup builder
    archives)."""
    return Path(backup_mod.CONFIG_DIR) / "uploads" / "logos"


async def _gather_live_logos() -> list[dict]:
    """Gather source-A logos as METADATA-ONLY records (bead 7ipq2.1 — D8).

    Reuses the backup builder's enumeration + correlation seam
    (:func:`routers.backup._fetch_source_logo_index` +
    :func:`routers.backup._gather_logo_binary_subtree`) so the sync slice reads
    the files under ECM's OWN ``/config/uploads/logos/``, correlated to the
    source Dispatcharr logo ``id``/``name`` by URL basename. NOTE: this is no
    longer the same set a backup artifact archives. Since bead …-xb58a the
    artifact ALSO carries the bytes of every Dispatcharr-hosted logo, fetched
    from Dispatcharr at gather time
    (:func:`routers.backup._gather_dispatcharr_logo_payloads`), and this gather
    does not. Closing that gap for cross-instance sync is its own bead. The records mirror
    the archive decoder's shape (``name``/``filename``/``size``/``id``) with
    ONE deliberate difference: **no** ``content_b64``. Bytes are hydrated
    lazily, one MISSED logo at a time, by :func:`_load_logo_content_b64` inside
    the importer loop — assembling every logo's base64 into the plan up front
    would hold the whole logo set in memory and defeat D8.

    Returns:
        Metadata-only logo records; empty when the logos dir is absent/empty.
    """
    try:
        source_index = await backup_mod._fetch_source_logo_index()
    except Exception as exc:  # noqa: BLE001 - correlation is best-effort
        logger.warning("[SYNC] Could not build source logo index: %s", exc)
        source_index = {}

    try:
        _entries, metadata, _url_mappings = backup_mod._gather_logo_binary_subtree(
            source_index
        )
    except Exception as exc:  # noqa: BLE001 - fail-soft: no logos rather than crash
        logger.warning("[SYNC] Could not enumerate source logos: %s", exc)
        return []

    records: list[dict] = []
    for meta in metadata.get("logos") or []:
        rel = meta.get("filename")
        if not isinstance(rel, str) or not rel:
            continue
        basename = posixpath.basename(rel)
        if not basename:
            continue
        record: dict = {
            # Decoder-parity shape: display name (correlated) or basename-stem.
            "name": basename.rsplit(".", 1)[0],
            "filename": basename,
            "size": meta.get("size_bytes"),
            _LOGO_REL_KEY: rel,
        }
        source_id = meta.get("id")
        if isinstance(source_id, int) and not isinstance(source_id, bool):
            record["id"] = source_id
        display_name = meta.get("name")
        if isinstance(display_name, str) and display_name.strip():
            record["name"] = display_name
        records.append(record)

    logger.info("[SYNC] Gathered %d source logo record(s) (metadata-only).", len(records))
    return records


async def _load_logo_content_b64(record: dict) -> Optional[str]:
    """Lazily read ONE logo's file and return its base64 payload (D8 seam).

    The ``content_provider`` handed to the reused logos importer: called only
    for a MISSED logo, immediately before validation+upload, so at most one
    logo's payload is ever live. Containment-guarded: the record's relative
    path must resolve INSIDE the logos dir (belt-and-braces — the rel paths
    come from our own enumeration, but the record travelled through the plan).
    Returns ``None`` on any failure (the importer surfaces a per-logo,
    path-free VALIDATION_ERROR).
    """
    rel = record.get(_LOGO_REL_KEY)
    if not isinstance(rel, str) or not rel:
        return None
    logos_dir = _sync_logos_dir().resolve()
    try:
        path = (logos_dir / rel).resolve()
        path.relative_to(logos_dir)  # raises ValueError if it escaped
    except (ValueError, OSError):
        logger.warning(
            "[SYNC] Refused logo content read outside the logos dir for '%s'.",
            record.get("name") or "<unknown>",
        )
        return None
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        logger.warning(
            "[SYNC] Could not read logo content for '%s': %s",
            record.get("name") or "<unknown>",
            exc.__class__.__name__,  # class only — never the path-bearing message
        )
        return None
    return base64.b64encode(data).decode("ascii")


async def build_live_source_plan(*, include_logos: bool = False) -> ImportPlan:
    """Gather the LOCAL source-A config, redact it, and assemble an ImportPlan.

    Reuses the backup gather (:func:`_gather_dispatcharr_sections`, which reads
    the LOCAL ``get_client()`` itself) and the shared
    :func:`_redact_credentials_deep` deep redactor — the SAME topology-only
    pipeline the redacted backup artifact uses (Addendum D D2: no secrets on the
    wire). Each gathered section maps to its :class:`EntityType` via
    :data:`_SECTION_TO_ENTITY`.

    The plan's manifest carries ``schema_version = BACKUP_SCHEMA_VERSION`` — the
    orchestrator's pre-flight runs the SAME .17 schema-version gate as archive
    restore and refuses a plan without it (spike ``xp6mp`` empirical find).

    Args:
        include_logos: the per-target ``sync_logos`` opt-in (bead 7ipq2.1 —
            ADR-013 S9 exit path). ``True`` appends a METADATA-ONLY ``LOGO``
            category LAST (no ``content_b64`` in the plan — D8; bytes hydrate
            lazily per missed logo at import time). ``False`` (default) keeps
            logos out of the plan entirely.

    Returns:
        An :class:`ImportPlan` of the redacted config categories PLUS the channels
        category (with embedded streams, gathered separately — bead kcxie) PLUS,
        only when ``include_logos`` is set, the metadata-only logos category.
        The ``users`` category is NEVER present (D3).
    """
    # Gather ONLY the config categories (never users; never channels/streams/
    # logos — those are other beads). _gather_dispatcharr_sections owns the LOCAL
    # client lookup and returns a ``{"_warning": ...}`` dict (no config rows) when
    # the local Dispatcharr is unavailable — never a crash.
    sections = await _gather_dispatcharr_sections(set(SYNC_CONFIG_CATEGORIES))

    # Redact to topology-only BEFORE the rows enter the plan — one shared denylist,
    # every category, no plaintext path (D2). preserve_keys is intentionally empty:
    # sync NEVER carries credentials, unlike the opt-in migration artifact (u81kh).
    redacted_sections = _redact_credentials_deep(sections, preserve_keys=frozenset())

    categories: list[PlanCategory] = []
    for section_key, entity_type in _SECTION_TO_ENTITY.items():
        if section_key not in SYNC_CONFIG_CATEGORIES:
            # Skip any decoder section outside this bead's config scope (the
            # decoder table may list more than we sync).
            continue
        _assert_no_never_sync(section_key)
        rows = redacted_sections.get(section_key) if isinstance(redacted_sections, dict) else None
        entities = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        categories.append(PlanCategory(entity_type=entity_type, entities=entities))

    # CHANNELS (bead kcxie) — gathered separately (not a config RESTORABLE_SECTION)
    # WITH embedded streams, then redacted through the SAME deep denylist (D2). The
    # redactor strips only secret-NAMED keys (password/token/...); stream ``url``
    # — the matcher's Tier-1 identity — is NOT a redact key and survives, so the
    # stream floor still works on the wire. The CHANNEL category is appended LAST
    # so it applies after every config dependency (groups/profiles/M3U) is created.
    channels = await _gather_live_channels()
    redacted_channels = _redact_credentials_deep(
        {"channels": channels}, preserve_keys=frozenset()
    )
    channel_rows = redacted_channels.get("channels") if isinstance(redacted_channels, dict) else None
    channel_entities = (
        [c for c in channel_rows if isinstance(c, dict)] if isinstance(channel_rows, list) else []
    )
    categories.append(
        PlanCategory(entity_type=EntityType.CHANNEL, entities=channel_entities)
    )

    # LOGOS (bead 7ipq2.1) — OPT-IN per target, appended AFTER channels (the
    # same hard Phase-2 ordering the restore registries use: logos LAST, so the
    # CHANNEL remap is populated for the logo-miss affected-channel drill-down).
    # METADATA-ONLY records: no content_b64 ever enters the plan (D8) — bytes
    # hydrate lazily per missed logo via _load_logo_content_b64. No redaction
    # pass is needed (name/filename/size only; no secret-named keys).
    if include_logos:
        logo_records = await _gather_live_logos()
        categories.append(
            PlanCategory(entity_type=EntityType.LOGO, entities=logo_records)
        )

    plan = ImportPlan(
        # The schema_version stamp is the load-bearing manifest field: pre-flight
        # refuses a plan without it (preflight.py -> validate_restore_schema_version).
        manifest={"schema_version": BACKUP_SCHEMA_VERSION},
        categories=categories,
    )
    logger.info(
        "[SYNC] Assembled redacted source plan: %s",
        ", ".join("%s=%d" % (c.entity_type.value, len(c.entities)) for c in categories),
    )
    return plan


# ---------------------------------------------------------------------------
# Source-side name-conflict tolerance — sync degrades PER-ITEM, unlike backup/
# restore's all-or-nothing preflight refusal.
# ---------------------------------------------------------------------------


def _split_name_conflicts(
    plan: ImportPlan,
) -> tuple[ImportPlan, dict[EntityType, list[dict]]]:
    """Dedup name-unique categories so a source-side duplicate degrades to a
    per-item CONFLICT instead of preflight's all-or-nothing plan refusal.

    ``dbas.preflight._validate_unique_names`` refuses the ENTIRE plan when ANY
    :data:`~dbas.preflight.NAME_UNIQUE_CATEGORIES` category carries two entities
    sharing a (trimmed, case-insensitive) name — correct for backup/restore,
    where a half-applied one-shot snapshot is worse than no restore at all
    (ADR / Dispatcharr has no DB transactions). Continuous cross-instance sync
    has the opposite failure mode: one duplicated name anywhere in the source
    (e.g. two channel groups both named "World Cup 2026") must not blank out
    every OTHER category's diff. This mirrors the per-item tolerance
    ``dbas/importers/channels.py`` already applies to an unrelated ambiguity
    (``_is_ambiguous_null_key`` — a name collision with a null channel_number on
    both sides is surfaced as a CONFLICT for that one channel, not a plan
    refusal) rather than inventing a second tolerance model.

    For each category in :data:`NAME_UNIQUE_CATEGORIES` — the SAME set
    preflight checks, imported directly so the two lists can never drift apart
    on which categories they cover — entities are scanned in archive order
    using preflight's EXACT normalization (``name.strip().lower()``; a missing,
    non-string, or empty name is left alone and never flagged, matching
    ``_validate_unique_names``). The first entity to claim a given name is kept;
    every later entity with the same name is removed from the returned plan and
    recorded in the excluded mapping so the caller can surface it as a CONFLICT
    once the (now preflight-safe) plan has been restored.

    Dropping a duplicate is not enough on its own: a CHANNEL entity elsewhere in
    the plan may carry a ``channel_group_id`` / ``stream_profile_id`` (the two
    fields :data:`~dbas.preflight.CHANNEL_FK_FIELDS` points at) that referenced
    the EXCLUDED duplicate's source id. Left alone, that reference would dangle
    and ``dbas.preflight._validate_fk_references`` would refuse the WHOLE
    (now-deduped) plan again — reproducing the exact all-or-nothing refusal this
    function exists to avoid, just via a different validator. So this function
    also builds a source-id -> kept-id remap for every FK-target category
    (:data:`~dbas.preflight.CHANNEL_FK_FIELDS` values) and rewrites any CHANNEL
    entity's matching FK field that pointed at an excluded id, so it now points
    at the surviving same-named entity instead.

    Args:
        plan: The freshly-assembled live-source plan, not yet preflighted.

    Returns:
        A tuple of ``(deduped_plan, excluded)`` where ``excluded`` maps each
        affected :class:`EntityType` to the list of archive entity dicts that
        were dropped. ``deduped_plan`` cannot trigger
        ``PreflightProblemKind.DUPLICATE_UNIQUE_NAME`` — every name-unique
        category now carries at most one entity per normalized name — and any
        CHANNEL FK that referenced a dropped duplicate has been remapped onto
        the entity that survived, so it cannot trigger
        ``PreflightProblemKind.UNRESOLVED_FK_REFERENCE`` either.
    """
    excluded: dict[EntityType, list[dict]] = {}
    # Excluded (dropped) source id -> surviving (kept, same-name) source id, per
    # FK-target entity type. Only populated for categories CHANNEL_FK_FIELDS can
    # point at (currently CHANNEL_GROUP / STREAM_PROFILE) — imported directly
    # from preflight so this can never drift from the FK fields preflight
    # actually validates.
    fk_remap: dict[EntityType, dict[int, int]] = {}
    new_categories: list[PlanCategory] = []
    for cat in plan.categories:
        if cat.entity_type not in NAME_UNIQUE_CATEGORIES:
            new_categories.append(cat)
            continue
        seen: set[str] = set()
        kept_id_by_name: dict[str, int] = {}
        kept: list[dict] = []
        for entity in cat.entities:
            raw = entity.get("name")
            if not isinstance(raw, str):
                kept.append(entity)
                continue
            key = raw.strip().lower()
            if not key:
                kept.append(entity)
                continue
            if key in seen:
                excluded.setdefault(cat.entity_type, []).append(entity)
                excluded_id = entity.get("id")
                kept_id = kept_id_by_name.get(key)
                if excluded_id is not None and kept_id is not None:
                    fk_remap.setdefault(cat.entity_type, {})[int(excluded_id)] = kept_id
                continue
            seen.add(key)
            kept.append(entity)
            kept_id = entity.get("id")
            if kept_id is not None:
                kept_id_by_name[key] = int(kept_id)
        new_categories.append(
            PlanCategory(
                entity_type=cat.entity_type, entities=kept, selected=cat.selected
            )
        )

    if fk_remap:
        for index, cat in enumerate(new_categories):
            if cat.entity_type != EntityType.CHANNEL:
                continue
            rewritten: list[dict] = []
            for channel in cat.entities:
                updated_channel = channel
                for field, target_type in CHANNEL_FK_FIELDS.items():
                    field_remap = fk_remap.get(target_type)
                    if not field_remap:
                        continue
                    ref = updated_channel.get(field)
                    if ref is None:
                        continue
                    try:
                        ref_id = int(ref)
                    except (TypeError, ValueError):
                        continue
                    if ref_id in field_remap:
                        if updated_channel is channel:
                            updated_channel = dict(channel)
                        updated_channel[field] = field_remap[ref_id]
                rewritten.append(updated_channel)
            new_categories[index] = PlanCategory(
                entity_type=cat.entity_type, entities=rewritten, selected=cat.selected
            )

    deduped_plan = plan.model_copy(update={"categories": new_categories})
    return deduped_plan, excluded


def _apply_name_conflict_details(
    report: RestoreReport, excluded: dict[EntityType, list[dict]]
) -> None:
    """Surface each entity :func:`_split_name_conflicts` dropped as a CONFLICT.

    Mirrors ``dbas/importers/channels.py``'s ambiguous-collision shape exactly
    (``cat.failed += 1`` + one :class:`FailureDetail` per entity) so the sync
    report's per-entity conflict UX is uniform regardless of which tolerance
    path produced it. Applied UNCONDITIONALLY — dry-run and apply alike — so a
    dry-run preview surfaces the conflict before an operator ever confirms
    apply, matching the channels.py precedent (no ``is_dry_run`` guard there
    either).

    Args:
        report: The :class:`RestoreReport` returned by :func:`~dbas.
            restore_orchestrator.run_restore` for the deduped plan.
        excluded: The mapping :func:`_split_name_conflicts` returned — entity
            type -> the archive entities it removed from the plan.
    """
    for entity_type, entities in excluded.items():
        cat = report.category(entity_type)
        for entity in entities:
            # Guaranteed a non-empty str by _split_name_conflicts (only entities
            # with a valid duplicate name are ever collected here).
            label = str(entity.get("name"))
            source_id = entity.get("id")
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.CONFLICT,
                    label=label,
                    message=(
                        "duplicate %s name in source archive: '%s' — a "
                        "same-named entity was kept and synced; this one was "
                        "skipped to avoid ambiguity." % (entity_type.value, label)
                    ),
                    source_export_id=int(source_id) if source_id is not None else None,
                )
            )
        logger.warning(
            "[SYNC] %d %s name-conflict(s) resolved: kept first, skipped %d "
            "duplicate(s).",
            len(entities),
            entity_type.value,
            len(entities),
        )
        report.notes.append(
            "%d %s name-conflict(s) resolved: kept first, skipped %d "
            "duplicate(s)." % (len(entities), entity_type.value, len(entities))
        )


# ---------------------------------------------------------------------------
# Config-only importer step registry — REUSE the orchestrator's builders.
# ---------------------------------------------------------------------------


def _sync_channels_step(*, allow_fuzzy_stream_match: bool) -> ImporterCallable:
    """Build the CHANNELS importer step for the sync path (bead kcxie).

    Unlike the orchestrator's shared ``_channels`` builder, this one threads the
    per-``SyncTarget`` ``allow_fuzzy_stream_match`` flag into ``import_channels``
    so the embedded-stream matcher is FLOORED at Tier-3 exact-normalized unless
    the target explicitly opted into fuzzy (spike ``xp6mp`` ruling 1b). The
    channel-row collision-safe floor (ruling 1a) is inside ``import_channels``
    itself, so it always applies regardless of this flag.
    """

    async def _channels(ctx: ApplyContext) -> list[dict] | None:
        cat = ctx.plan.category(EntityType.CHANNEL)
        await import_channels(
            archive_channels=list(cat.entities) if cat else [],
            client=ctx.client,
            selected=bool(cat.selected) if cat else False,
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            allow_fuzzy_stream_match=allow_fuzzy_stream_match,
        )
        return None

    return _channels


def _sync_logos_step() -> ImporterCallable:
    """Build the LOGOS importer step for the sync path (bead 7ipq2.1).

    Reuses the UNCHANGED ``import_logos`` with two sync-specific bindings:

    * ``clear_existing=False`` — HARD-CODED, not a parameter. The destructive
      bulk-delete pre-step can never fire on the sync path (ADR-013 S9's core
      objection to per-cycle logos); B's existing logos are only ever matched
      against or added to, never cleared.
    * ``content_provider=_load_logo_content_b64`` — the D8 lazy-hydration seam:
      the plan's logo records are metadata-only, and each MISSED logo's bytes
      are read from the local source dir one at a time inside the importer loop
      (a matched logo never reads its file at all).

    When the plan carries no LOGO category (the target did not opt in), the
    step is a structural no-op — same single registry serves opted-in and
    opted-out targets, dry-run and apply alike (the kxcjf parity lesson: there
    is exactly ONE list to which a category can be added).
    """

    async def _logos(ctx: ApplyContext) -> list[dict] | None:
        cat = ctx.plan.category(EntityType.LOGO)
        if cat is None:
            return None  # target did not opt into logo sync — nothing to do.
        channel_cat = ctx.plan.category(EntityType.CHANNEL)
        await import_logos(
            archive_logos=list(cat.entities),
            client=ctx.client,
            selected=bool(cat.selected),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            clear_existing=False,  # NEVER destructive on the sync path.
            archive_channels=list(channel_cat.entities) if channel_cat else [],
            content_provider=_load_logo_content_b64,
        )
        return None

    return _logos


def sync_config_importer_steps(
    *, allow_fuzzy_stream_match: bool = False
) -> list[ImporterStep]:
    """The step registry for a sync cycle — config categories + channels + logos.

    Reuses :func:`dbas.restore_orchestrator._importer_step_builders` (the SAME
    callables that back the archive apply + dry-run registries) for the config
    categories so there is no second importer path, then appends the CHANNELS
    step (bead kcxie) after every config dependency — groups/profiles/M3U — and
    the LOGOS step (bead 7ipq2.1) LAST. The LOGOS step is a structural no-op
    unless the plan carries a LOGO category (the per-target ``sync_logos``
    opt-in), and can never bulk-delete (``clear_existing`` hard-disabled).
    Users are excluded permanently (D3).

    The CHANNELS step threads ``allow_fuzzy_stream_match`` (the per-``SyncTarget``
    ``fuzzy_stream_matching`` flag, default off) into ``import_channels`` so the
    embedded-stream matcher floors at Tier-3 exact-normalized for the sync path
    (spike ``xp6mp`` ruling 1b). The channel-row collision-safe floor (ruling 1a)
    is inside the importer and always applies.

    CRITICAL (ADR-013 S9): the M3U step is registered with ``defers=False`` and
    the orchestrator is given a deferred-apply no-op, so the per-cycle deferred
    auto-sync / EPG-download phase NEVER fires — re-triggering provider auto-sync
    on B every interval is exactly the behaviour S9 forbids. The M3U importer
    still returns its deferred settings; we simply never apply them.
    """
    s = _importer_step_builders()
    return [
        # M3U first (EPG sources resolve their m3u_account FK through the remap
        # M3U writes). defers=False: the deferred auto-sync phase is suppressed.
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=False),
        ImporterStep(EntityType.EPG_SOURCE, s["epg"]),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        # NOTE (bead …-lvfwd): this registry carries no USER_AGENT step, even
        # though ADR-013 S9 lists user agents in the per-cycle config set. A
        # stream profile carrying a ``user_agent`` FK therefore finds nothing in
        # the USER_AGENT remap namespace and is skipped DEPENDENCY_UNRESOLVED
        # rather than created. That is strictly safer than the previous
        # behaviour (POST the source id — a 400 that failed the whole cycle, or
        # a silent bind to whatever occupies that id on B), but it does mean a
        # custom-user-agent stream profile does not sync. Wiring the USER_AGENT
        # step in changes what a cycle mutates on B, so it is a separate,
        # ADR-scoped decision — not a drive-by here.
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        # CHANNELS (+ embedded streams) after every config dependency.
        ImporterStep(
            EntityType.CHANNEL,
            _sync_channels_step(allow_fuzzy_stream_match=allow_fuzzy_stream_match),
        ),
        # LOGOS LAST (restore-registry ordering parity: channels populate the
        # CHANNEL remap the logo-miss drill-down reads). Structurally a no-op
        # unless the plan carries a LOGO category (per-target sync_logos opt-in).
        ImporterStep(EntityType.LOGO, _sync_logos_step()),
    ]


async def _no_deferred_apply(*, deferred: list[dict], client) -> list[dict]:
    """Deferred-apply no-op for the sync path (ADR-013 S9).

    The orchestrator only calls a deferred-apply fn when ``ctx.deferred`` is
    non-empty AND the run is a clean non-dry-run apply. The config step registry
    registers M3U with ``defers=False``, but the M3U importer still RETURNS its
    deferred settings; to guarantee S9 even if a future builder change starts
    collecting them, this fn drops them on the floor and logs.
    """
    if deferred:
        logger.info(
            "[SYNC] Suppressing %d deferred auto-sync setting(s) (ADR-013 S9 — "
            "per-cycle provider auto-sync is not re-triggered on B).",
            len(deferred),
        )
    return []


# ---------------------------------------------------------------------------
# run_sync — the engine entrypoint.
# ---------------------------------------------------------------------------


def _journal_sync_run(
    target,
    report: Optional[RestoreReport],
    *,
    confirm_apply: bool,
    aborted_reason: Optional[str] = None,
) -> None:
    """Write the per-run ``sync_outbound`` audit row (Addendum D D9).

    Records target id, the config categories + their counts, the result, and the
    redaction mode. Best-effort — a journal failure must not crash the sync. Only
    SAFE fields (names, counts, outcome) are logged; never a credential.
    """
    try:
        if aborted_reason is not None:
            description = "Cross-instance sync ABORTED: %s" % aborted_reason
            counts = {}
            result = "aborted"
        else:
            counts = {
                cat.entity_type.value: {
                    "created": cat.created,
                    "would_create": cat.would_create,
                    "skipped": cat.skipped,
                    "failed": cat.failed,
                }
                for cat in (report.categories if report else [])
            }
            outcome = report.outcome.value if (report and report.outcome) else None
            result = (
                "dry_run" if (report and report.is_dry_run) else (outcome or "unknown")
            )
            # Report the categories THIS run actually processed (includes the
            # opt-in logos slice when the target enabled it); fall back to the
            # unconditional set when the report carries no categories.
            ran_categories = sorted(counts) if counts else sorted(SYNC_ALL_CATEGORIES)
            description = (
                "Cross-instance sync run (mode=%s, redaction_mode=topology_only, "
                "categories=%s)" % (result, ran_categories)
            )
        journal.log_entry(
            category="sync_outbound",
            action_type="sync_run",
            entity_name=getattr(target, "name", None) or ("sync target %s" % getattr(target, "id", "?")),
            entity_id=getattr(target, "id", None),
            description=description,
            # after_value carries the structured run record (no secrets — only
            # category names, counts, and the result/redaction mode).
            after_value={
                "confirm_apply": confirm_apply,
                "redaction_mode": "topology_only",
                "result": result,
                "counts": counts,
            },
            user_initiated=False,
        )
    except Exception as exc:  # pragma: no cover — journal best-effort
        logger.warning("[SYNC] Failed to journal sync run: %s", exc)


async def run_sync(
    sync_target,
    *,
    confirm_apply: bool = False,
    session=None,
    captured_version: Optional[int] = None,
    ledger_dir: Optional[Path] = None,
) -> RestoreReport:
    """Run one cross-instance config sync cycle for ``sync_target`` (A → B).

    The engine entrypoint:

    1. **Freshness gate (D5)** — :func:`sync_freshness_reason` re-reads the target
       FRESH. A non-None reason ABORTS the cycle fail-closed: no remote client is
       built, no writes happen, the abort is journalled (``sync_outbound``), and a
       report carrying the reason in ``notes`` (``outcome=None``) is returned.
    2. **Remote client** — :func:`make_remote_client` builds an SSRF-guarded
       dest-B client from the target row.
    3. **Live-source plan** — :func:`build_live_source_plan` gathers + redacts A's
       config categories (D2) into an :class:`ImportPlan` (never users — D3).
    4. **Restore** — the UNCHANGED :func:`run_restore` runs the config-only step
       registry against dest-B. ``confirm_apply=False`` (the DEFAULT) produces a
       counts-only dry-run (would-create) with ZERO writes; ``confirm_apply=True``
       applies source-wins (A overwrites B; match→skip-or-create idempotent).
    5. **Audit (D9)** — the run is journalled (categories, counts, result,
       redaction_mode).

    Args:
        sync_target: a ``SyncTarget`` ORM row (or any object exposing ``id`` /
            ``name`` / ``base_url`` / ``credentials`` / ``enabled`` /
            ``token_revoked_at`` / ``credential_version`` / ``insecure`` /
            ``fuzzy_stream_matching`` / ``sync_logos``).
        confirm_apply: opt-IN to MUTATE B. ``False`` (default) is a counts-only
            dry-run (no writes); ``True`` applies source-wins.
        session: an open DB session for the freshness re-read. The caller owns its
            lifecycle. Optional only so the dry-run preview can run without one;
            when ``None`` the freshness gate is skipped (the scheduled wrapper
            bead ``5gzg5`` always passes one).
        captured_version: the ``credential_version`` captured at enqueue, threaded
            to the freshness gate (D5). ``None`` skips the version check.
        ledger_dir: override the durable rollback-ledger directory (tests).

    Returns:
        The :class:`RestoreReport` — dry-run (``is_dry_run=True``, ``outcome=None``)
        or a realized apply with the tri-state ``outcome``. On a freshness abort,
        a report with the reason in ``notes`` and ``outcome=None``.
    """
    target_label = getattr(sync_target, "name", None) or (
        "sync target %s" % getattr(sync_target, "id", "?")
    )

    # --- 1. Freshness gate (D5) — abort fail-closed on stale/revoked/disabled. ---
    if session is not None:
        reason = sync_freshness_reason(
            session, getattr(sync_target, "id", None), captured_version
        )
        if reason is not None:
            logger.warning("[SYNC] Aborting sync for %s: %s", target_label, reason)
            report = RestoreReport(is_dry_run=not confirm_apply)
            report.notes.append("sync aborted: %s" % reason)
            _journal_sync_run(
                sync_target, report, confirm_apply=confirm_apply, aborted_reason=reason
            )
            return report

    # --- 2. Remote dest-B client (SSRF-guarded). ---
    client = make_remote_client(sync_target)

    # --- 3. Redacted live-source plan (config categories, never users). The
    # logos slice is per-target OPT-IN (sync_logos, default off — 7ipq2.1);
    # when on, the plan gains a METADATA-ONLY logo category (bytes hydrate
    # lazily at import time, misses only — D8). ---
    include_logos = bool(getattr(sync_target, "sync_logos", False))
    plan = await build_live_source_plan(include_logos=include_logos)

    # --- 3b. Degrade a source-side duplicate name to a per-item CONFLICT ---
    # instead of inheriting preflight's all-or-nothing plan refusal (see
    # _split_name_conflicts) — a single duplicated group/profile/M3U name must
    # not blank out every other category's diff.
    plan, excluded_name_conflicts = _split_name_conflicts(plan)

    # --- 4. Restore (reused orchestrator) — dry-run default, source-wins apply. ---
    # The per-target fuzzy-stream-matching opt-in (default off) threads into the
    # channels step; off => the stream matcher floors at Tier-3 exact (ruling 1b).
    allow_fuzzy = bool(getattr(sync_target, "fuzzy_stream_matching", False))
    report = RestoreReport(is_dry_run=not confirm_apply)
    ledger = RollbackLedger(restore_id=new_restore_id())
    result = await run_restore(
        plan=plan,
        client=client,
        steps=sync_config_importer_steps(allow_fuzzy_stream_match=allow_fuzzy),
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=confirm_apply,
        deferred_apply_fn=_no_deferred_apply,  # ADR-013 S9 — suppress per-cycle defer.
        ledger_dir=ledger_dir,
    )

    # --- 4b. Surface each deduped-out duplicate name as a per-item CONFLICT. ---
    _apply_name_conflict_details(result, excluded_name_conflicts)

    # The conflict details above land AFTER run_restore computed the tri-state
    # outcome, so without this re-check an APPLY with source-side name
    # conflicts would report outcome=SUCCESS alongside failed>0 — violating
    # the ratified "NEVER SUCCESS on mixed state" invariant (ADR-013 S8) that
    # the task wrapper's failed-count contract depends on (live-validation
    # finding, bead 7ipq2.2). Mirror compute_outcome's no-rollback branch: a
    # per-item conflict with no rollback is FAILED_ROLLBACK_INCOMPLETE, exactly
    # what the channels importer's in-run CONFLICT path already yields. Only a
    # clean SUCCESS is ever downgraded — a rolled-back outcome stays as
    # computed (the rollback verdict is already correct for it).
    if (
        excluded_name_conflicts
        and not result.is_dry_run
        and result.outcome == RestoreOutcome.SUCCESS
    ):
        result.outcome = RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE

    # --- 5. Audit the run (D9). ---
    _journal_sync_run(sync_target, result, confirm_apply=confirm_apply)

    # --- 5b. Stamp the persisted per-target sync state (DBA ruling, spike
    # xp6mp / migration 0024): last_outcome on every REALIZED apply, and
    # last_full_sync_at only on a FULL success — the staleness/status surface
    # must never read a mixed apply (or a dry-run preview) as "B was current
    # as of this time". Live-validation finding (bead 7ipq2.2): these columns
    # existed but nothing ever wrote them. Best-effort: a stamp failure must
    # not fail an otherwise-completed sync. last_source_fingerprint stays
    # unwritten (semantics unratified — follow-up bead). ---
    if session is not None and not result.is_dry_run and result.outcome is not None:
        try:
            from datetime import datetime, timezone

            sync_target.last_outcome = result.outcome.value
            if result.outcome == RestoreOutcome.SUCCESS:
                sync_target.last_full_sync_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - stamping is best-effort
            logger.warning("[SYNC] Failed to stamp persisted sync state: %s", exc)

    logger.info(
        "[SYNC] Sync cycle for %s complete (mode=%s, outcome=%s).",
        target_label,
        "dry_run" if result.is_dry_run else "apply",
        result.outcome.value if result.outcome else "none",
    )
    return result
