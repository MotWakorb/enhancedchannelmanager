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
}

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


def _decode_logo_records(zf: zipfile.ZipFile) -> list[dict]:
    """Decode the binary logo subtree into per-logo importer records.

    Each ``binary/logos/<rel>`` member becomes a record the logos importer (.15)
    consumes::

        {"name": <basename-stem>, "filename": <basename>,
         "content_b64": <base64 of the file bytes>, "size": <byte length>}

    The artifact carries no source logo id, so tier-1 (src) match never applies;
    the importer's tier-2 (name) / tier-3 (file) match handle reconciliation.

    Member names are screened by :func:`_is_safe_member` before read; an unsafe
    name is skipped (logged), never read.
    """
    records: list[dict] = []
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
        records.append(
            {
                "name": stem,
                "filename": basename,
                "content_b64": base64.b64encode(raw).decode("ascii"),
                "size": len(raw),
            }
        )
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

    logo_records = _decode_logo_records(zf)
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
