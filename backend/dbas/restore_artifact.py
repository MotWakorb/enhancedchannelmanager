"""Decode a new-format DBAS backup artifact into a restore :class:`ImportPlan`.

Bead ``enhancedchannelmanager-o8tbv`` — the "round-trip gap" glue. Every
backend restore primitive existed before this module
(``validate_artifact_manifest`` .17, ``run_restore`` / ``run_dry_run`` .18/.16,
every importer .10-.15) but NOTHING turned an on-disk
:func:`routers.backup.build_backup_artifact` ZIP into the
:class:`~dbas.preflight.ImportPlan` the orchestrator consumes. This module is
that decode step, and it is the SINGLE place an untrusted uploaded artifact is
unpacked into structured records.

Decode pipeline (validate-before-decode, never the reverse)
-----------------------------------------------------------
1. :func:`routers.backup.validate_artifact_manifest` runs FIRST (version gate +
   per-member SHA-256). It is the caller's responsibility to call it before
   :func:`decode_artifact_to_plan`; this module re-asserts the manifest is
   present and parses it but does NOT re-run integrity (the caller already did,
   on the same open ``ZipFile``). The orchestrator then re-checks the
   schema-version a SECOND time in pre-flight (defence in depth — .17 + .18).

2. Per-category YAML (``categories/<section>.yaml``) is parsed and the entity
   list extracted from its ``dispatcharr.<section>`` / ``database.<section>``
   slice. Each artifact section maps to exactly one :class:`EntityType`.

3. The binary subtree (``binary/logos/<rel>`` + ``binary/metadata.json``) is
   decoded into the per-logo records the logos importer (.15) consumes:
   ``{"name", "filename", "content_b64", "size"}``. The bytes are base64-encoded
   here so the importer's one-at-a-time decode/upload loop (.15, D8) drives the
   memory profile — this module never decodes a logo's IMAGE bytes, only reads
   the stored file bytes and re-encodes them as the transport the importer
   already expects.

ZIP-SLIP / PATH-TRAVERSAL SAFETY (the untrusted-upload surface)
---------------------------------------------------------------
This module NEVER extracts a member to the filesystem — it only ``zf.read()``s
named members it itself chose (the manifest-listed categories + the enumerated
binary subtree). Even so, every member name pulled from the artifact's own
listing is screened by :func:`_is_safe_member` (reject absolute paths, ``..``
segments, and backslash separators) before it is read, so a crafted archive
entry can never widen the read surface. The logos importer (.15) independently
re-validates each filename basename before any upload — defence in depth.

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log line.
"""

from __future__ import annotations

import base64
import logging
import posixpath
import zipfile

import yaml

from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_contracts import EntityType

logger = logging.getLogger(__name__)

# Artifact layout constants mirror routers.backup (the builder). Kept here as a
# local copy so the decoder does not import the heavy backup router module at
# import time; a drift between the two is caught by the round-trip test.
_CATEGORY_DIR = "categories"
_LOGO_DIR = "binary/logos"
_BINARY_METADATA = "binary/metadata.json"
_MANIFEST_NAME = "manifest.json"

# Artifact category-file (section) -> the EntityType it restores. The section
# key is the YAML leaf the builder wrote under ``dispatcharr`` / ``database``.
# Sections with no restorable EntityType (settings, scheduled_tasks, …) are not
# listed and are ignored by the decode — they are restored by the legacy YAML
# path, not the Phase-2 importers.
_SECTION_TO_ENTITY: dict[str, EntityType] = {
    "m3u_accounts": EntityType.M3U_ACCOUNT,
    "epg_sources": EntityType.EPG_SOURCE,
    "channel_groups": EntityType.CHANNEL_GROUP,
    "channel_profiles": EntityType.CHANNEL_PROFILE,
    "stream_profiles": EntityType.STREAM_PROFILE,
    # 7i8rf — the producer now emits these categories, so the decoder maps them
    # to the EntityTypes the WIRED apply importers consume (channels: 4vouz,
    # dispatcharr_users: l1p4p). Without these two rows the producer's bytes
    # decoded to nothing and restore was a silent no-op.
    "channels": EntityType.CHANNEL,
    "dispatcharr_users": EntityType.USER,
    # lc6zu — the settings/agents entity categories (importers:
    # dbas/importers/settings_agents.py). The core_settings/comskip SETTINGS
    # blobs are NOT listed here: they are key/value mappings, not entity lists,
    # and decode through their own seam (_decode_settings_category).
    "user_agents": EntityType.USER_AGENT,
    "dvr_rules": EntityType.DVR_RULE,
    # tyrg1 — the Dispatcharr ServerGroup an M3U account's ``server_group`` FK
    # points at. Its category is ordered BEFORE M3U_ACCOUNT in all three
    # registries so the account's FK has a populated namespace to remap
    # through; without this row the account could only drop the FK (g8tyd) and
    # the replica lost its provider connection-limit grouping.
    "server_groups": EntityType.SERVER_GROUP,
}

# The SETTINGS-blob artifact sections (key/value mappings, not entity lists).
# Both decode into the SINGLE ``EntityType.SETTINGS`` plan category as
# self-describing records ``{"section": <name>, "values": {...}}`` — the
# contract the orchestrator's settings step consumes
# (restore_orchestrator._settings -> settings_agents.import_core_settings /
# import_comskip). Order here is the fixed apply order.
_SETTINGS_BLOB_SECTIONS: tuple[str, ...] = ("core_settings", "comskip")

# ECM's OWN settings blob (…-dfkbn item 4). It lives at the TOP LEVEL of
# ``categories/settings.yaml`` — not under ``dispatcharr`` / ``database`` — and
# decodes into its own :data:`EntityType.ECM_SETTINGS` category, distinct from
# the Dispatcharr core-settings blobs above. The builder has emitted this member
# since the artifact format existed; there was simply no decoder row for it, so
# ECM's settings silently reverted to defaults on every restore.
_ECM_SETTINGS_SECTION = "settings"

# The Dispatcharr LOGO INVENTORY section (…-dfkbn item 1). Deliberately absent
# from :data:`_SECTION_TO_ENTITY`: its rows are MERGED into the single
# ``EntityType.LOGO`` category alongside the binary subtree's byte-bearing
# records (see :func:`_merge_logo_records`), because two plan categories of the
# same EntityType would leave one of them permanently shadowed by
# ``ImportPlan.category``.
_LOGO_INVENTORY_SECTION = "logos"

# Where in the parsed category YAML each section's entity list lives. The
# Dispatcharr-managed sections sit under ``dispatcharr``; ECM DB sections under
# ``database``. We probe both so the decode is robust to either.
_YAML_CONTAINERS = ("dispatcharr", "database")


def _is_safe_member(name: str) -> bool:
    """True when an artifact member name is safe to ``zf.read()`` (no traversal).

    Rejects absolute paths, any ``..`` path segment, and backslash separators
    (a Windows-style traversal). This is a screen on the member NAME only — the
    decoder never writes a member to disk — but it keeps a crafted listing from
    widening the read surface beyond the well-formed subtree we expect.
    """
    if not name or name.startswith("/") or name.startswith("\\"):
        return False
    if "\\" in name:
        return False
    # Reject ANY parent-ref segment in the RAW name (before normalisation): a
    # ``binary/logos/../../x`` collapses to a harmless ``x`` under normpath, which
    # would mask the traversal intent. Screening the raw segments refuses the
    # crafted entry outright rather than silently relocating its read target.
    if ".." in name.split("/"):
        return False
    # Belt-and-braces: the normalised form must not escape upward either.
    normalized = posixpath.normpath(name)
    return not (normalized.startswith("..") or normalized.startswith("/"))


def _extract_section_entities(parsed: object, section_key: str) -> list[dict]:
    """Pull the entity list for ``section_key`` from a parsed category YAML.

    Probes the ``dispatcharr`` then ``database`` container. Returns a list of
    dict records (the raw archive rows) or an empty list when the section is
    absent / malformed — a missing category is a no-op, never a crash.
    """
    if not isinstance(parsed, dict):
        return []
    for container in _YAML_CONTAINERS:
        block = parsed.get(container)
        if isinstance(block, dict) and section_key in block:
            rows = block[section_key]
            if isinstance(rows, list):
                return [r for r in rows if isinstance(r, dict)]
    return []


def _decode_categories(zf: zipfile.ZipFile) -> list[PlanCategory]:
    """Decode every ``categories/<section>.yaml`` member into PlanCategories.

    Only sections present in :data:`_SECTION_TO_ENTITY` produce a category; a
    section file that is missing, empty, or carries no rows yields an empty
    category (still listed so the operator sees "0" rather than a gap).
    """
    categories: list[PlanCategory] = []
    names = set(zf.namelist())
    for section_key, entity_type in _SECTION_TO_ENTITY.items():
        member = "%s/%s.yaml" % (_CATEGORY_DIR, section_key)
        entities: list[dict] = []
        if member in names and _is_safe_member(member):
            try:
                parsed = yaml.safe_load(zf.read(member).decode("utf-8"))
            except (yaml.YAMLError, UnicodeDecodeError) as exc:
                # A malformed category YAML is a per-category empty (logged), not
                # a whole-restore crash. Pre-flight still sees a consistent plan.
                logger.warning(
                    "[DBAS-RESTORE] Category %s YAML unreadable; treating as empty: %s",
                    section_key, exc,
                )
                parsed = None
            entities = _extract_section_entities(parsed, section_key)
        categories.append(PlanCategory(entity_type=entity_type, entities=entities))
    return categories


def _extract_section_blob(parsed: object, section_key: str) -> dict:
    """Pull a SETTINGS-blob mapping for ``section_key`` from a parsed category YAML.

    Mirrors :func:`_extract_section_entities` but for the key/value blob shape
    (core_settings / comskip). Returns ``{}`` when the section is absent or not
    a mapping — a malformed blob decodes to nothing, never a crash.
    """
    if not isinstance(parsed, dict):
        return {}
    for container in _YAML_CONTAINERS:
        block = parsed.get(container)
        if isinstance(block, dict) and section_key in block:
            blob = block[section_key]
            if isinstance(blob, dict):
                return blob
    return {}


def _decode_settings_category(zf: zipfile.ZipFile) -> PlanCategory:
    """Decode the core_settings/comskip blobs into the SETTINGS plan category.

    Each present, non-empty blob becomes one self-describing record
    ``{"section": <name>, "values": {...}}`` (see
    :data:`_SETTINGS_BLOB_SECTIONS`). The category is always returned — empty
    when neither section carries values — so the operator sees "0" rather than
    a gap, consistent with :func:`_decode_categories`.
    """
    entities: list[dict] = []
    names = set(zf.namelist())
    for section_key in _SETTINGS_BLOB_SECTIONS:
        member = "%s/%s.yaml" % (_CATEGORY_DIR, section_key)
        if member not in names or not _is_safe_member(member):
            continue
        try:
            parsed = yaml.safe_load(zf.read(member).decode("utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as exc:
            logger.warning(
                "[DBAS-RESTORE] Settings section %s YAML unreadable; treating as empty: %s",
                section_key, exc,
            )
            continue
        blob = _extract_section_blob(parsed, section_key)
        if blob:
            entities.append({"section": section_key, "values": blob})
    return PlanCategory(entity_type=EntityType.SETTINGS, entities=entities)


def _read_category_yaml(zf: zipfile.ZipFile, section_key: str) -> object:
    """Parse ``categories/<section>.yaml``, or ``None`` when absent/unreadable.

    A malformed member is a logged empty, never a whole-restore crash — the same
    containment :func:`_decode_categories` applies per category.
    """
    member = "%s/%s.yaml" % (_CATEGORY_DIR, section_key)
    if member not in set(zf.namelist()) or not _is_safe_member(member):
        return None
    try:
        return yaml.safe_load(zf.read(member).decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        logger.warning(
            "[DBAS-RESTORE] Category %s YAML unreadable; treating as empty: %s",
            section_key, exc,
        )
        return None


def _decode_ecm_settings_category(zf: zipfile.ZipFile) -> PlanCategory:
    """Decode ECM's own ``settings`` blob into the ECM_SETTINGS plan category.

    The blob sits at the TOP LEVEL of ``categories/settings.yaml`` (it is neither
    Dispatcharr-managed nor an ECM DB table), and becomes ONE record
    ``{"values": {...}}`` — the contract the orchestrator's ECM-settings step
    consumes. The category is always returned, empty when the member is absent,
    so the operator sees "0" rather than a gap.
    """
    parsed = _read_category_yaml(zf, _ECM_SETTINGS_SECTION)
    values = parsed.get(_ECM_SETTINGS_SECTION) if isinstance(parsed, dict) else None
    entities = [{"values": values}] if isinstance(values, dict) and values else []
    return PlanCategory(entity_type=EntityType.ECM_SETTINGS, entities=entities)


def _decode_logo_inventory(zf: zipfile.ZipFile) -> list[dict]:
    """Decode ``categories/logos.yaml`` into the Dispatcharr logo inventory rows.

    Each row is ``{"id", "name", "url"}`` as Dispatcharr's ``LogoSerializer``
    emits it. These carry NO bytes — the URL is the restorable identity.
    """
    parsed = _read_category_yaml(zf, _LOGO_INVENTORY_SECTION)
    return _extract_section_entities(parsed, _LOGO_INVENTORY_SECTION)


def _merge_logo_records(binary_records: list[dict], inventory: list[dict]) -> list[dict]:
    """Merge the byte-bearing binary records with the URL-bearing inventory rows.

    Both describe the SAME logos from different angles, joined on the source
    Dispatcharr logo id (the builder correlates each archived file to its logo
    record by URL basename and carries the id in ``binary/metadata.json``):

    * a logo present in BOTH is emitted ONCE — the binary record wins (it has the
      actual image) and gains the inventory's ``url`` so the importer's match and
      the URL-recreate fallback both have everything they need;
    * a logo present only in the inventory is emitted as a URL-only record — the
      case that covers every remotely-hosted logo, which the binary subtree could
      never carry;
    * a byte-bearing record the builder could not correlate to an id is emitted
      unchanged (it still matches by name/basename).

    Order is deterministic: binary records first, in their decoded order, then
    the inventory-only rows in archive order.
    """
    merged = list(binary_records)
    by_source_id: dict[int, dict] = {}
    for record in merged:
        source_id = record.get("id")
        if isinstance(source_id, int) and not isinstance(source_id, bool):
            by_source_id.setdefault(source_id, record)

    for row in inventory:
        source_id = row.get("id")
        url = row.get("url")
        existing = (
            by_source_id.get(source_id)
            if isinstance(source_id, int) and not isinstance(source_id, bool)
            else None
        )
        if existing is not None:
            if isinstance(url, str) and url and "url" not in existing:
                existing["url"] = url
            continue
        merged.append(dict(row))
    return merged


def _parse_logo_metadata_index(zf: zipfile.ZipFile) -> dict[str, dict]:
    """Parse ``binary/metadata.json`` into a filename -> entry index.

    PR #743 review item 1 (cm9bi): the builder joins each archived logo file to
    its SOURCE Dispatcharr logo record (``id`` + display ``name``) and carries
    the correlation in the binary metadata. This index lets
    :func:`_decode_logo_records` attach that id to each logo record — the key
    the logos importer's affected-channel lookup (archive channels' ``logo_id``
    FKs) resolves against. Best-effort: a missing/malformed metadata member
    yields an empty index (records then decode id-less, as before).
    """
    import json

    if _BINARY_METADATA not in zf.namelist():
        return {}
    try:
        metadata = json.loads(zf.read(_BINARY_METADATA))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("[DBAS-RESTORE] Binary metadata unreadable during decode: %s", exc)
        return {}
    if not isinstance(metadata, dict):
        return {}
    index: dict[str, dict] = {}
    for entry in metadata.get("logos") or []:
        if isinstance(entry, dict) and isinstance(entry.get("filename"), str):
            index[entry["filename"]] = entry
    return index


def _decode_logo_records(zf: zipfile.ZipFile) -> list[dict]:
    """Decode the binary logo subtree into per-logo importer records.

    Each ``binary/logos/<rel>`` member becomes a record the logos importer (.15)
    consumes::

        {"name": <display name or basename-stem>, "filename": <basename>,
         "content_b64": <base64 of the file bytes>, "size": <byte length>,
         "id": <source Dispatcharr logo id, when correlated>}

    ``id`` + display ``name`` come from the builder's source-logo correlation in
    ``binary/metadata.json`` (PR #743 item 1). The id is what makes the logo-miss
    affected-channel drill-down work on GENUINE artifacts (archive channels
    reference logos by ``logo_id``), and lets a matched/uploaded logo register
    in the LOGO remap namespace. A file the builder could not correlate decodes
    id-less with the basename-stem name fallback — exactly the pre-correlation
    shape; the importer's tier-2 (name) / tier-3 (file) match still applies.

    Member names are screened by :func:`_is_safe_member` before read; an unsafe
    name is skipped (logged), never read.
    """
    records: list[dict] = []
    metadata_index = _parse_logo_metadata_index(zf)
    prefix = _LOGO_DIR + "/"
    for name in zf.namelist():
        if not name.startswith(prefix) or name == prefix:
            continue
        if not _is_safe_member(name):
            logger.warning("[DBAS-RESTORE] Skipping unsafe logo member %r", name)
            continue
        info = zf.getinfo(name)
        if info.is_dir():
            continue
        raw = zf.read(name)
        basename = posixpath.basename(name)
        if not basename:
            continue
        stem = basename.rsplit(".", 1)[0]
        record = {
            "name": stem,
            "filename": basename,
            "content_b64": base64.b64encode(raw).decode("ascii"),
            "size": len(raw),
        }
        # Metadata is keyed by the file's logos-dir-relative path (== the member
        # name minus the binary/logos/ prefix).
        meta = metadata_index.get(name[len(prefix):])
        if meta is not None:
            source_id = meta.get("id")
            if isinstance(source_id, int) and not isinstance(source_id, bool):
                record["id"] = source_id
            display_name = meta.get("name")
            if isinstance(display_name, str) and display_name.strip():
                record["name"] = display_name
        records.append(record)
    return records


def _parse_manifest(zf: zipfile.ZipFile) -> dict:
    """Parse the cleartext ``manifest.json`` header, or return ``{}`` if absent.

    The caller is expected to have already run
    :func:`routers.backup.validate_artifact_manifest` (which refuses a missing /
    malformed / newer-version manifest). This is a best-effort re-parse so the
    plan carries the manifest for pre-flight's second version check.
    """
    import json

    if _MANIFEST_NAME not in zf.namelist():
        return {}
    try:
        manifest = json.loads(zf.read(_MANIFEST_NAME))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("[DBAS-RESTORE] Manifest unreadable during decode: %s", exc)
        return {}
    return manifest if isinstance(manifest, dict) else {}


def decode_artifact_to_plan(zf: zipfile.ZipFile) -> ImportPlan:
    """Decode a validated DBAS artifact into an :class:`ImportPlan`.

    PRECONDITION: the caller has already run
    :func:`routers.backup.validate_artifact_manifest` on ``zf`` (version gate +
    per-member integrity) — this function does NOT re-run integrity. It builds
    the plan the orchestrator (.18) / dry-run engine (.16) consume:

    * the parsed manifest (for pre-flight's independent schema-version gate),
    * one :class:`~dbas.preflight.PlanCategory` per artifact section
      (M3U / EPG / channel groups / channel profiles / stream profiles), and
    * a :class:`~dbas.restore_contracts.EntityType.LOGO` category decoded from
      the binary subtree.

    Args:
        zf: An OPEN, already-validated artifact ``ZipFile`` (read mode).

    Returns:
        The :class:`ImportPlan` — categories default to ``selected=True`` so the
        whole archive is in scope; a caller may deselect categories before
        handing the plan to the orchestrator.
    """
    # D2 zip-bomb guard at the START of decode too — defence in depth. The
    # caller (DbasRestoreTask) runs validate_artifact_manifest first, which
    # already guards, but decode is a separately-callable read site, so re-assert
    # the cap before any zf.read() here. Imported lazily to keep the heavy backup
    # router off this module's import path (see module docstring).
    from routers.backup import guard_artifact_against_zip_bomb

    guard_artifact_against_zip_bomb(zf)

    manifest = _parse_manifest(zf)
    categories = _decode_categories(zf)

    # lc6zu — the SETTINGS category (core_settings + comskip blobs) decodes
    # through its own seam: the sections are key/value mappings, not entity
    # lists, and both land in ONE EntityType.SETTINGS category as
    # {"section", "values"} records (the orchestrator settings-step contract).
    categories.append(_decode_settings_category(zf))

    # dfkbn item 4 — ECM's OWN settings.json blob, which had no decoder row at
    # all and so was silently dropped on every restore.
    categories.append(_decode_ecm_settings_category(zf))

    # dfkbn item 1 — ONE LOGO category built from BOTH sources: the binary
    # subtree (bytes, ECM's own uploads dir only) and the Dispatcharr logo
    # inventory (id + name + URL, every logo the source instance had). Merged
    # rather than appended as two categories, because ``ImportPlan.category``
    # returns the first match and the second would be permanently shadowed.
    logo_records = _merge_logo_records(
        _decode_logo_records(zf), _decode_logo_inventory(zf)
    )
    categories.append(
        PlanCategory(entity_type=EntityType.LOGO, entities=logo_records)
    )

    logger.info(
        "[DBAS-RESTORE] Decoded artifact: %s",
        ", ".join(
            "%s=%d" % (c.entity_type.value, len(c.entities)) for c in categories
        ),
    )
    return ImportPlan(manifest=manifest, categories=categories)
