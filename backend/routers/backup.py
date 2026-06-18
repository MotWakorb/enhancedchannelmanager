"""
Backup & Restore router — create and restore ECM configuration backups.

Backs up: settings.json, journal.db, uploads/logos/, tls/, m3u_uploads/
YAML export: settings + DB tables + Dispatcharr state in a single file.
"""
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text

from auth import RequireAdminIfEnabled
from config import CONFIG_DIR, CONFIG_FILE, DispatcharrSettings, get_settings, save_settings, clear_settings_cache
from database import close_db, get_engine, get_session, init_db, JOURNAL_DB_FILE
from dispatcharr_client import get_client, reset_client
from models import (
    AutoCreationRule,
    DummyEPGProfile,
    DummyEPGChannelAssignment,
    FFmpegProfile,
    NormalizationRuleGroup,
    NormalizationRule,
    ScheduledTask,
    TaskSchedule,
    TagGroup,
    Tag,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["Backup"])


def _resolve_backup_normalization_group_ids(item: dict, session) -> str | None:
    """Resolve normalization_group_ids from backup data, with backward compat."""
    norm_ids = item.get("normalization_group_ids")
    if norm_ids is not None:
        return json.dumps(norm_ids) if norm_ids else None
    if item.get("normalize_names"):
        from models import NormalizationRuleGroup
        groups = session.query(NormalizationRuleGroup.id).filter(
            NormalizationRuleGroup.enabled == True
        ).order_by(NormalizationRuleGroup.priority).all()
        return json.dumps([g.id for g in groups]) if groups else None
    return None

# Directories to include in backup (relative to CONFIG_DIR)
BACKUP_DIRS = ["uploads/logos", "tls", "m3u_uploads"]

# App version for manifest (imported at call time to avoid circular imports).
#
# IMPORTANT (versioning.md touchpoint): APP_VERSION is a CI-enforced version
# literal. scripts/check_version_consistency.py greps for the exact
# ``APP_VERSION = "..."`` shape and fails the PR if it diverges from
# frontend/package.json and backend/main.py. Do NOT rename it, change its
# shape, or repurpose it. It is an INFORMATIONAL human-readable string ("which
# ECM build produced this artifact") — it is NOT a compatibility gate.
APP_VERSION = "0.17.6-0004"

# DBAS backup-artifact schema version (ADR-008 D1 / ADR-012 D1). This is a
# DEDICATED, MONOTONIC INTEGER that is DISTINCT from the human-readable
# APP_VERSION string above. Restore gates on THIS value (manifest_version <=
# BACKUP_SCHEMA_VERSION accepted; a newer artifact is refused with the
# user-facing "Unsupported backup version"). Bump by +1 only on a
# backward-incompatible artifact-format change; never tie it to APP_VERSION.
# Starts at 1 for the first v0.18.0 DBAS artifact (0i2vt.7).
BACKUP_SCHEMA_VERSION = 1

REDACTED = "***REDACTED***"

# Credential fields in DispatcharrSettings that must never appear raw in an
# exported backup. Mirrors the YAML export contract for parity (bd-l0nhi).
# bd-jmi1c (GH #273): both ``dispatcharr_api_key`` (canonical) and the
# legacy ``api_key`` are listed so the back-compat mirror in
# ``config.save_settings`` doesn't accidentally leak a value the canonical
# redaction would have caught.
# Back-compat: drop 'api_key' from this tuple in v0.19.0 (bd-ewm4h) when
# the legacy field is removed from the model. The debug-bundle redactor in
# routers/auto_creation.py imports this tuple, so a single edit there
# propagates everywhere.
_SETTINGS_CREDENTIAL_FIELDS = (
    "password",
    "dispatcharr_api_key",
    "api_key",
    "smtp_password",
    "telegram_bot_token",
    "mcp_api_key",
)

# Credential-class keys that may live inside alert_methods.config JSON. Matches
# the masking set in AlertMethod.to_dict (models.py) so backup redaction stays
# in lock-step with the API-response masking already shipped to clients.
_ALERT_METHOD_CREDENTIAL_KEYS = ("password", "bot_token", "webhook_url", "api_key")

# SINGLE shared credential-key denylist for the DBAS artifact (0i2vt.7, ADR-012
# D1 redact-by-default). Used by the NON-BYPASSABLE deep redactor
# (_redact_credentials_deep) that runs over EVERY category — including
# Dispatcharr-sourced sections (M3U / EPG accounts), which the shipped YAML
# export does NOT scrub on its own. Production Dispatcharr happens to never
# return the M3U password (write-only, SHA1-hashed at fetch — see
# docs/dispatcharr_api.md), but the artifact MUST NOT depend on that: redaction
# is defense-in-depth and runs before any byte enters the archive. This union
# folds in the settings + alert-method denylists so there is exactly one list
# to maintain. Matched case-insensitively against dict keys.
_REDACT_KEYS = frozenset(
    {k.lower() for k in _SETTINGS_CREDENTIAL_FIELDS}
    | {k.lower() for k in _ALERT_METHOD_CREDENTIAL_KEYS}
    | {
        # Dispatcharr / cloud-target credential-class keys that can ride along
        # in gathered sections. Keep additive — never remove without confirming
        # the field is not credential-bearing.
        "password",
        "passwd",
        "secret",
        "secret_key",
        "access_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "auth_token",
        "bearer_token",
    }
)


def _redact_credentials_deep(obj):
    """Recursively replace any value whose key (case-insensitive) is in the
    shared :data:`_REDACT_KEYS` denylist with the REDACTED sentinel.

    NON-BYPASSABLE artifact-pipeline stage (0i2vt.7): there is no plaintext
    switch. Walks dicts and lists in place-safe fashion (returns a new
    structure) so credential-class values never enter the archive regardless of
    which category/source produced them. Non-credential values are untouched.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if isinstance(key, str) and key.lower() in _REDACT_KEYS:
                # Only redact truthy values — preserve None/"" so restore-side
                # preserve-on-omit semantics still distinguish "unset".
                out[key] = REDACTED if value not in (None, "") else value
            else:
                out[key] = _redact_credentials_deep(value)
        return out
    if isinstance(obj, list):
        return [_redact_credentials_deep(item) for item in obj]
    return obj


def _get_backup_filename() -> str:
    """Generate a timestamped backup filename."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return f"ecm-backup-{now}.zip"


def _build_manifest(files: list[str]) -> dict:
    """Build backup manifest with version and file list."""
    return {
        "version": APP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def _scrub_journal_db_to_temp(src: Path) -> Path:
    """Copy journal.db to a temp file and redact credential-class keys in
    alert_methods.config rows. Returns the temp file path; caller must unlink.

    Per bd-l0nhi: PR #163 began storing SMTP password (and other creds) inside
    alert_methods.config JSON, so the live DB cannot be zipped raw without
    leaking credentials.
    """
    fd, tmp_name = tempfile.mkstemp(prefix="ecm-backup-journal-", suffix=".db")
    os.close(fd)
    tmp_path = Path(tmp_name)
    shutil.copyfile(src, tmp_path)

    try:
        conn = sqlite3.connect(str(tmp_path))
    except sqlite3.Error as e:
        # Source isn't a usable SQLite DB — log and ship the byte-for-byte copy
        # so the backup doesn't fail outright. The validator will still reject
        # it on restore if it's truly malformed.
        logger.warning("[BACKUP] Could not open journal.db for scrub, shipping as-is: %s", e)
        return tmp_path

    try:
        cur = conn.cursor()
        # alert_methods table may not exist on freshly-bootstrapped DBs.
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_methods'"
            )
            if cur.fetchone() is None:
                return tmp_path
            cur.execute("SELECT id, config FROM alert_methods")
            rows = cur.fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("[BACKUP] alert_methods scrub skipped (DB read failed): %s", e)
            return tmp_path

        for row_id, raw_config in rows:
            if not raw_config:
                continue
            try:
                cfg = json.loads(raw_config)
            except (json.JSONDecodeError, TypeError):
                # Leave malformed rows alone — restore-side will refuse to
                # parse them anyway, and we don't want to silently rewrite.
                continue
            if not isinstance(cfg, dict):
                continue
            changed = False
            for key in _ALERT_METHOD_CREDENTIAL_KEYS:
                if key in cfg and cfg[key]:
                    cfg[key] = REDACTED
                    changed = True
            if changed:
                cur.execute(
                    "UPDATE alert_methods SET config=? WHERE id=?",
                    (json.dumps(cfg), row_id),
                )
        conn.commit()
        logger.info("[BACKUP] Scrubbed alert_methods.config in %d rows", len(rows))
    finally:
        conn.close()
    return tmp_path


def _create_backup_zip() -> io.BytesIO:
    """Create a zip file containing all ECM config data."""
    # Flush SQLite WAL so journal.db is self-contained before we zip it.
    # WAL mode is enabled by the engine-connect PRAGMA listener in
    # database.py, so the checkpoint is meaningful: without it, recent
    # writes would still live in journal.db-wal and be lost from the backup.
    try:
        engine = get_engine()
        with engine.connect() as conn:
            # PRAGMA wal_checkpoint(TRUNCATE) returns ``(busy, log,
            # checkpointed)``. ``busy=1`` means SQLite could not acquire
            # the exclusive WAL lock and the WAL was NOT fully truncated,
            # so the zipped journal.db may not contain the most recent
            # writes (they still live in the un-truncated WAL on disk and
            # are not part of the backup). Surface that as WARN — matches
            # the database.py startup checkpoint pattern (bd-ej995 polish).
            row = conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
            conn.commit()
        busy = row[0] if row else 0
        if busy:
            logger.warning("[BACKUP] WAL checkpoint completed (incomplete -- WAL busy)")
        else:
            logger.info("[BACKUP] WAL checkpoint completed")
    except Exception as e:
        logger.warning("[BACKUP] WAL checkpoint failed (non-fatal): %s", e)

    buf = io.BytesIO()
    files_added = []
    scrubbed_db_path: Optional[Path] = None

    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add settings.json — written from the redacted dict so credential
            # fields (password, api_key, smtp_password, telegram_bot_token,
            # mcp_api_key) never hit the archive raw.
            if CONFIG_FILE.exists():
                redacted = _gather_settings()
                zf.writestr("settings.json", json.dumps(redacted, indent=2))
                files_added.append("settings.json")
                logger.info("[BACKUP] Added settings.json (redacted)")

            # Add journal.db — copied to a temp file and scrubbed of
            # alert_methods.config credential-class keys before zipping.
            if JOURNAL_DB_FILE.exists():
                scrubbed_db_path = _scrub_journal_db_to_temp(JOURNAL_DB_FILE)
                zf.write(scrubbed_db_path, "journal.db")
                files_added.append("journal.db")
                logger.info(
                    "[BACKUP] Added journal.db (%d bytes, scrubbed)",
                    scrubbed_db_path.stat().st_size,
                )

            # Add directories
            for dir_rel in BACKUP_DIRS:
                dir_path = CONFIG_DIR / dir_rel
                if dir_path.exists() and dir_path.is_dir():
                    for file_path in dir_path.rglob("*"):
                        if file_path.is_file():
                            arcname = str(file_path.relative_to(CONFIG_DIR))
                            zf.write(file_path, arcname)
                            files_added.append(arcname)
                    if any(1 for _ in dir_path.rglob("*") if _.is_file()):
                        logger.info("[BACKUP] Added directory %s", dir_rel)

            # Add manifest
            manifest = _build_manifest(files_added)
            zf.writestr("ecm_backup.json", json.dumps(manifest, indent=2))
    finally:
        if scrubbed_db_path is not None:
            try:
                scrubbed_db_path.unlink()
            except OSError as e:
                logger.warning("[BACKUP] Failed to unlink scrubbed journal temp %s: %s", scrubbed_db_path, e)

    buf.seek(0)
    logger.info("[BACKUP] Backup created with %d files", len(files_added))
    return buf


# ---------------------------------------------------------------------------
# DBAS backup artifact builder (0i2vt.7)
#
# The NEW v0.18.0 DBAS artifact format. Distinct from the legacy
# ``_create_backup_zip`` above (which the shipped GET /create + POST /save +
# restore paths still use). The new artifact is a ZIP containing:
#
#   manifest.json                 — schema_version (int) + app_version (str) +
#                                   created_at + per-file SHA-256 + redacted flag.
#                                   This is the CLEARTEXT HEADER: schema_version
#                                   is readable WITHOUT decrypting (encryption seam
#                                   for u81kh — a future wrapper encrypts the whole
#                                   ZIP file, but the schema_version must remain
#                                   discoverable from the manifest before decrypt).
#   categories/<name>.yaml        — per-category redacted config (reuses
#                                   build_yaml_export / _gather_* — single source).
#   journal.db                    — scrubbed via _scrub_journal_db_to_temp.
#   binary/metadata.json          — logo inventory.
#   binary/url-mappings.json      — logo-file -> source-URL map.
#   binary/logos/<file>           — per-image logo files (streamed, not buffered).
#
# A SHA-256 checksum SIDECAR file is written ALONGSIDE the ZIP (ADR-012 D1):
# ``<artifact>.sha256``, computed by STREAMING the finished ZIP file (never by
# hashing an in-RAM blob). Redaction is a NON-BYPASSABLE pipeline stage that
# runs BEFORE any bytes enter the archive: there is no "ship plaintext" switch.
# ---------------------------------------------------------------------------

# Path layout inside the new artifact ZIP.
ARTIFACT_MANIFEST_NAME = "manifest.json"
ARTIFACT_CATEGORY_DIR = "categories"
ARTIFACT_BINARY_DIR = "binary"
ARTIFACT_LOGO_DIR = "binary/logos"
ARTIFACT_BINARY_METADATA = "binary/metadata.json"
ARTIFACT_BINARY_URL_MAPPINGS = "binary/url-mappings.json"

# Streaming chunk size for SHA-256 computation over the finished artifact.
_SHA256_CHUNK = 1024 * 1024  # 1 MiB

# Headroom multiplier for the pre-build free-disk check. The redacted source
# (logos + journal.db) is read once into a compressed ZIP; we conservatively
# require free space >= estimated_source_bytes (the ZIP is typically smaller,
# but DEFLATE on already-compressed PNG/JPG logos barely shrinks them, so we do
# not discount). A clear failure here beats filling /config and corrupting
# journal.db mid-write (D8 / grooming note).
_DISK_HEADROOM_BYTES = 64 * 1024 * 1024  # 64 MiB absolute floor on top of estimate


class BackupArtifact:
    """Result of :func:`build_backup_artifact`.

    Attributes:
        zip_path: Path to the sealed (redacted) ZIP artifact on disk.
        sidecar_path: Path to the ``<zip>.sha256`` checksum sidecar.
        schema_version: The integer schema version stamped in the manifest.
        sha256: Hex SHA-256 of the final artifact bytes (== sidecar contents).
        file_count: Number of member files written into the ZIP.
    """

    __slots__ = ("zip_path", "sidecar_path", "schema_version", "sha256", "file_count")

    def __init__(self, zip_path, sidecar_path, schema_version, sha256, file_count):
        self.zip_path = zip_path
        self.sidecar_path = sidecar_path
        self.schema_version = schema_version
        self.sha256 = sha256
        self.file_count = file_count


def _compute_sha256_streaming(path: Path) -> str:
    """Compute the SHA-256 of a file by streaming it in chunks.

    Never reads the whole file into RAM — the artifact can be multi-GB (D8).
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_SHA256_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _estimate_artifact_source_bytes() -> int:
    """Estimate the on-disk byte cost of the artifact before building.

    Sums journal.db plus every file under the backup directories (logos, tls,
    m3u_uploads). DEFLATE rarely shrinks already-compressed logo images, so we
    treat the raw source size as the floor for the free-disk pre-check.
    """
    total = 0
    if JOURNAL_DB_FILE.exists():
        try:
            total += JOURNAL_DB_FILE.stat().st_size
        except OSError:
            pass
    for dir_rel in BACKUP_DIRS:
        dir_path = CONFIG_DIR / dir_rel
        if not (dir_path.exists() and dir_path.is_dir()):
            continue
        for file_path in dir_path.rglob("*"):
            try:
                if file_path.is_file():
                    total += file_path.stat().st_size
            except OSError:
                continue
    return total


def _check_free_disk(target_dir: Path, required_bytes: int) -> None:
    """Raise RuntimeError if ``target_dir``'s partition lacks ``required_bytes``
    of free space (plus a fixed headroom floor).

    A giant artifact can fill the /config partition and break the live
    journal.db; failing loudly BEFORE we start writing is the safe behavior
    (grooming note / D8).
    """
    needed = required_bytes + _DISK_HEADROOM_BYTES
    try:
        usage = shutil.disk_usage(str(target_dir))
    except OSError as e:
        # If we cannot stat the partition, do not block the backup outright —
        # log and proceed; the write itself will fail loudly if truly full.
        logger.warning("[BACKUP] Could not check free disk on %s: %s", target_dir, e)
        return
    if usage.free < needed:
        raise RuntimeError(
            "Insufficient free disk to build backup artifact: need ~%d bytes "
            "(estimate %d + headroom %d), have %d free on %s"
            % (needed, required_bytes, _DISK_HEADROOM_BYTES, usage.free, target_dir)
        )


def _build_artifact_manifest(
    schema_version: int,
    file_hashes: dict[str, str],
) -> dict:
    """Build the new-format artifact manifest (cleartext header).

    ``schema_version`` is a dedicated integer, DISTINCT from ``app_version``
    (the human-readable APP_VERSION string). Both are kept: ``app_version`` for
    operator info, ``schema_version`` for the restore compatibility gate.
    ``files`` carries a per-member SHA-256 so an unpacked member can be
    integrity-checked independently of the whole-artifact sidecar.
    """
    return {
        "schema_version": schema_version,
        "app_version": APP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "redacted": True,
        "files": [
            {"path": path, "sha256": sha}
            for path, sha in sorted(file_hashes.items())
        ],
    }


async def _gather_redacted_categories() -> dict[str, str]:
    """Produce the per-category redacted YAML payloads for the artifact.

    REUSES build_yaml_export / _gather_settings / _gather_db_tables /
    _gather_dispatcharr_sections — the SAME gather + redaction pipeline the
    shipped YAML export uses. There is no second gather and no divergent
    redaction list: settings credentials are masked by _gather_settings via the
    shared _SETTINGS_CREDENTIAL_FIELDS denylist before any byte is emitted.

    Returns a mapping of ``<category-name>.yaml`` -> YAML text. Each restorable
    section is emitted as its own file so a future selective restore (Phase 2)
    can read one category without parsing the whole archive.
    """
    out: dict[str, str] = {}
    for key in RESTORABLE_SECTIONS:
        # build_yaml_export routes settings/db/dispatcharr correctly and applies
        # the settings-field redaction. That is NOT sufficient on its own:
        # Dispatcharr-sourced sections (M3U / EPG accounts) can carry
        # credential-class fields the settings redactor never touches. So every
        # category's gathered payload passes through the shared NON-BYPASSABLE
        # deep redactor before it is serialized into the archive — one denylist,
        # every category, no plaintext path.
        yaml_text = await build_yaml_export({key})
        parsed = yaml.safe_load(yaml_text)
        redacted = _redact_credentials_deep(parsed)
        out["%s.yaml" % key] = yaml.dump(
            redacted, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
    return out


def _gather_logo_binary_subtree() -> tuple[list[tuple[Path, str]], dict, dict]:
    """Enumerate logo files for the binary subtree without reading them.

    Returns ``(entries, metadata, url_mappings)`` where:
      - ``entries`` is a list of ``(source_path, arcname)`` to stream into the
        ZIP one file at a time (D8 streaming-upload model — the builder writes
        each via zf.write(), which streams from disk, never buffering all logos
        in RAM).
      - ``metadata`` is the inventory written to binary/metadata.json.
      - ``url_mappings`` maps each archived logo filename to its (best-effort)
        source reference for restore-side re-hosting.
    """
    entries: list[tuple[Path, str]] = []
    files_meta: list[dict] = []
    url_mappings: dict[str, str] = {}

    logos_dir = CONFIG_DIR / "uploads" / "logos"
    if logos_dir.exists() and logos_dir.is_dir():
        for file_path in sorted(logos_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(logos_dir).as_posix()
            arcname = "%s/%s" % (ARTIFACT_LOGO_DIR, rel)
            entries.append((file_path, arcname))
            try:
                size = file_path.stat().st_size
            except OSError:
                size = None
            files_meta.append({"filename": rel, "size_bytes": size})
            # Local logos are referenced by their on-disk relative path; the
            # restore importer (Phase 2, 0i2vt.15) re-hosts them. Remote logo
            # URL reconstruction is a restore-side concern and out of scope for
            # the builder — record the local path so the mapping is complete.
            url_mappings[rel] = "uploads/logos/%s" % rel

    metadata = {
        "logo_count": len(files_meta),
        "logos": files_meta,
    }
    return entries, metadata, url_mappings


async def build_backup_artifact(dest_dir: Optional[Path] = None) -> BackupArtifact:
    """Build the new-format DBAS backup artifact (0i2vt.7).

    Streams a redacted, sealed ZIP to a temp file under ``dest_dir`` (defaults
    to a temp dir on the CONFIG partition), then writes a SHA-256 sidecar
    computed by streaming the finished file. Returns a :class:`BackupArtifact`.

    Redaction is non-bypassable: there is no plaintext switch. The redacted
    bytes are produced as a clean stream so a future wrapper (u81kh) can encrypt
    the whole file without re-cutting the format; ``manifest.json`` remains the
    cleartext header carrying ``schema_version``.

    On ANY failure, partial temp artifacts are cleaned up.
    """
    # Flush WAL so journal.db is self-contained (same rationale as the legacy
    # builder — see _create_backup_zip).
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
            conn.commit()
        if row and row[0]:
            logger.warning("[BACKUP] WAL checkpoint completed (incomplete -- WAL busy)")
        else:
            logger.info("[BACKUP] WAL checkpoint completed")
    except Exception as e:
        logger.warning("[BACKUP] WAL checkpoint failed (non-fatal): %s", e)

    # Pre-build free-disk check on the partition we will write to.
    if dest_dir is None:
        dest_dir = CONFIG_DIR
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    _check_free_disk(dest_dir, _estimate_artifact_source_bytes())

    # Gather redacted payloads BEFORE opening the archive so a gather failure
    # never leaves a half-written ZIP on disk.
    categories = await _gather_redacted_categories()
    logo_entries, logo_metadata, url_mappings = _gather_logo_binary_subtree()

    fd, tmp_zip_name = tempfile.mkstemp(prefix="ecm-artifact-", suffix=".zip", dir=str(dest_dir))
    os.close(fd)
    zip_path = Path(tmp_zip_name)
    sidecar_path = Path(str(zip_path) + ".sha256")
    scrubbed_db_path: Optional[Path] = None
    file_hashes: dict[str, str] = {}

    def _writestr_hashed(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
        zf.writestr(arcname, data)
        file_hashes[arcname] = hashlib.sha256(data).hexdigest()

    def _write_hashed(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
        # Stream the file into the ZIP AND hash it in the same single pass over
        # the bytes (no second read, no whole-file buffer).
        zinfo = zipfile.ZipInfo(arcname)
        zinfo.compress_type = zipfile.ZIP_DEFLATED
        h = hashlib.sha256()
        with open(src, "rb") as fsrc, zf.open(zinfo, "w") as fdst:
            for chunk in iter(lambda: fsrc.read(_SHA256_CHUNK), b""):
                fdst.write(chunk)
                h.update(chunk)
        file_hashes[arcname] = h.hexdigest()

    try:
        # Open the ZIP on a writable FILE HANDLE (NamedTemporaryFile-class temp
        # path), NOT io.BytesIO — the artifact is streamed to disk (D8).
        with open(zip_path, "wb") as zfh:
            with zipfile.ZipFile(zfh, "w", zipfile.ZIP_DEFLATED) as zf:
                # Per-category redacted YAML.
                for name, yaml_text in categories.items():
                    _writestr_hashed(
                        zf,
                        "%s/%s" % (ARTIFACT_CATEGORY_DIR, name),
                        yaml_text.encode("utf-8"),
                    )

                # journal.db — scrubbed copy (alert_methods.config creds redacted).
                if JOURNAL_DB_FILE.exists():
                    scrubbed_db_path = _scrub_journal_db_to_temp(JOURNAL_DB_FILE)
                    _write_hashed(zf, scrubbed_db_path, "journal.db")

                # Binary subtree: metadata + url-mappings + per-image logo files.
                _writestr_hashed(
                    zf,
                    ARTIFACT_BINARY_METADATA,
                    json.dumps(logo_metadata, indent=2).encode("utf-8"),
                )
                _writestr_hashed(
                    zf,
                    ARTIFACT_BINARY_URL_MAPPINGS,
                    json.dumps(url_mappings, indent=2).encode("utf-8"),
                )
                for src_path, arcname in logo_entries:
                    _write_hashed(zf, src_path, arcname)

                # Manifest LAST so it can carry every member's hash. This is the
                # cleartext header (schema_version readable pre-decrypt).
                manifest = _build_artifact_manifest(BACKUP_SCHEMA_VERSION, file_hashes)
                # The manifest itself is not in file_hashes (it hashes the others).
                zf.writestr(ARTIFACT_MANIFEST_NAME, json.dumps(manifest, indent=2))

        # SHA-256 of the FINISHED artifact, computed by streaming the file.
        artifact_sha = _compute_sha256_streaming(zip_path)
        sidecar_path.write_text(
            "%s  %s\n" % (artifact_sha, zip_path.name), encoding="utf-8"
        )

        logger.info(
            "[BACKUP] Built artifact %s (schema_version=%d, %d members, sha256=%s)",
            zip_path.name, BACKUP_SCHEMA_VERSION, len(file_hashes), artifact_sha,
        )
        return BackupArtifact(
            zip_path=zip_path,
            sidecar_path=sidecar_path,
            schema_version=BACKUP_SCHEMA_VERSION,
            sha256=artifact_sha,
            file_count=len(file_hashes),
        )
    except Exception:
        # Clean up partial temp artifacts on ANY failure.
        for p in (zip_path, sidecar_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError as e:
                logger.warning("[BACKUP] Failed to clean up partial artifact %s: %s", p, e)
        raise
    finally:
        if scrubbed_db_path is not None:
            try:
                scrubbed_db_path.unlink()
            except OSError as e:
                logger.warning(
                    "[BACKUP] Failed to unlink scrubbed journal temp %s: %s",
                    scrubbed_db_path, e,
                )


def verify_artifact_sha256(zip_path: Path, sidecar_path: Path) -> bool:
    """Verify a built artifact against its SHA-256 sidecar.

    Streams the artifact (no whole-file buffer) and compares against the hash in
    the sidecar. Returns True on match, False on mismatch or unreadable sidecar.
    """
    try:
        sidecar_text = Path(sidecar_path).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    expected = sidecar_text.split()[0] if sidecar_text else ""
    if not expected:
        return False
    actual = _compute_sha256_streaming(Path(zip_path))
    return actual == expected


def _validate_backup_zip(zf: zipfile.ZipFile) -> dict:
    """Validate a backup zip file and return its manifest."""
    # Must contain manifest
    if "ecm_backup.json" not in zf.namelist():
        raise HTTPException(status_code=400, detail="Not a valid ECM backup: missing ecm_backup.json manifest")

    # Parse manifest
    try:
        manifest = json.loads(zf.read("ecm_backup.json"))
    except (json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=400, detail="Invalid backup manifest: %s" % str(e))

    if not isinstance(manifest, dict) or "version" not in manifest:
        raise HTTPException(status_code=400, detail="Invalid backup manifest: missing version")

    # Validate settings.json if present
    if "settings.json" in zf.namelist():
        try:
            json.loads(zf.read("settings.json"))
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Backup contains invalid settings.json")

    # Validate journal.db if present (check SQLite magic bytes)
    if "journal.db" in zf.namelist():
        db_header = zf.read("journal.db")[:16]
        if not db_header.startswith(b"SQLite format 3"):
            raise HTTPException(status_code=400, detail="Backup contains invalid journal.db (not a SQLite database)")

    # Check for path traversal in zip entries
    for name in zf.namelist():
        if name.startswith("/") or ".." in name:
            raise HTTPException(status_code=400, detail="Backup contains unsafe file paths")
        # Canonicalize and verify resolved path stays within CONFIG_DIR
        resolved = (CONFIG_DIR / name).resolve()
        if not str(resolved).startswith(str(CONFIG_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Backup contains unsafe file paths")

    return manifest


def _merge_settings_preserving_redacted(zip_settings_bytes: bytes) -> bytes:
    """Apply restored settings.json on top of existing settings, dropping
    REDACTED sentinels so existing credentials are preserved.

    Mirrors the YAML restore semantics in _restore_settings (lines below) so
    a redacted ZIP behaves the same as a redacted YAML export. Backward-compat:
    legacy non-redacted ZIPs have no sentinels, so every value is taken as-is.
    """
    try:
        zipped = json.loads(zip_settings_bytes)
    except (json.JSONDecodeError, TypeError):
        # Validator has already accepted this as JSON; if it's somehow not a
        # dict, fall back to writing as-is rather than corrupting the file.
        return zip_settings_bytes
    if not isinstance(zipped, dict):
        return zip_settings_bytes

    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, json.JSONDecodeError):
            existing = {}
    else:
        existing = {}

    merged = dict(existing)
    skipped = []
    for key, value in zipped.items():
        if value == REDACTED:
            skipped.append(key)
            continue
        merged[key] = value
    if skipped:
        logger.info("[BACKUP] Preserved existing values for redacted settings: %s", skipped)
    return json.dumps(merged, indent=2).encode("utf-8")


def _capture_existing_alert_method_configs() -> dict[int, dict]:
    """Read existing alert_methods rows directly from journal.db so we can
    re-merge non-redacted credential fields after the restored DB is written.

    Returns {id: parsed_config_dict}. Rows with malformed JSON or missing
    table are skipped silently — the caller treats absent ids as 'no merge'.
    """
    if not JOURNAL_DB_FILE.exists():
        return {}
    out: dict[int, dict] = {}
    try:
        conn = sqlite3.connect(str(JOURNAL_DB_FILE))
    except sqlite3.Error as e:
        logger.warning("[BACKUP] Could not open journal.db for pre-restore capture: %s", e)
        return {}
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_methods'"
            )
            if cur.fetchone() is None:
                return {}
            cur.execute("SELECT id, config FROM alert_methods")
            rows = cur.fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("[BACKUP] Could not read alert_methods for pre-restore capture: %s", e)
            return {}
        for row_id, raw in rows:
            if not raw:
                continue
            try:
                cfg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(cfg, dict):
                out[row_id] = cfg
    finally:
        conn.close()
    return out


def _merge_alert_method_creds_after_restore(prior: dict[int, dict]) -> None:
    """For each alert_methods row in the restored DB, restore non-redacted
    credential-class values from the prior snapshot when the restored value
    is the REDACTED sentinel. Match by row id.

    Backward-compat: legacy non-redacted ZIPs carry no sentinel — every value
    survives the merge unchanged.
    """
    if not JOURNAL_DB_FILE.exists():
        return
    try:
        conn = sqlite3.connect(str(JOURNAL_DB_FILE))
    except sqlite3.Error as e:
        logger.warning("[BACKUP] Could not open restored journal.db for cred merge: %s", e)
        return
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_methods'"
            )
            if cur.fetchone() is None:
                return
            cur.execute("SELECT id, config FROM alert_methods")
            rows = cur.fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("[BACKUP] Could not read alert_methods after restore for merge: %s", e)
            return
        merged_count = 0
        for row_id, raw in rows:
            if not raw:
                continue
            try:
                cfg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(cfg, dict):
                continue
            prior_cfg = prior.get(row_id, {})
            changed = False
            for key in _ALERT_METHOD_CREDENTIAL_KEYS:
                if cfg.get(key) == REDACTED and prior_cfg.get(key) not in (None, "", REDACTED):
                    cfg[key] = prior_cfg[key]
                    changed = True
            if changed:
                cur.execute(
                    "UPDATE alert_methods SET config=? WHERE id=?",
                    (json.dumps(cfg), row_id),
                )
                merged_count += 1
        conn.commit()
        if merged_count:
            logger.info(
                "[BACKUP] Re-merged credentials into %d alert_methods rows after restore",
                merged_count,
            )
    finally:
        conn.close()


def _restore_from_zip(zf: zipfile.ZipFile, manifest: dict) -> list[str]:
    """Restore files from a validated backup zip."""
    restored = []

    # Capture existing alert_methods.config BEFORE we close/replace the DB so
    # we can merge real creds back where the restored ZIP has REDACTED.
    prior_alert_configs = _capture_existing_alert_method_configs()

    # Close database before replacing files
    close_db()
    logger.info("[BACKUP] Database closed for restore")

    try:
        # Restore settings.json — drop REDACTED sentinels in favor of the
        # currently-on-disk value, mirroring YAML restore semantics.
        if "settings.json" in zf.namelist():
            CONFIG_FILE.write_bytes(_merge_settings_preserving_redacted(zf.read("settings.json")))
            restored.append("settings.json")
            logger.info("[BACKUP] Restored settings.json")

        # Restore journal.db
        if "journal.db" in zf.namelist():
            JOURNAL_DB_FILE.write_bytes(zf.read("journal.db"))
            restored.append("journal.db")
            logger.info("[BACKUP] Restored journal.db")
            # Merge any REDACTED alert_methods.config creds back from the
            # pre-restore snapshot so existing rows aren't degraded.
            _merge_alert_method_creds_after_restore(prior_alert_configs)

        # Restore directories — clear existing before writing
        for dir_rel in BACKUP_DIRS:
            dir_path = CONFIG_DIR / dir_rel
            # Find files in this directory from the zip
            prefix = dir_rel + "/"
            dir_files = [n for n in zf.namelist() if n.startswith(prefix) and not n.endswith("/")]

            if dir_files:
                # Clear existing directory
                if dir_path.exists():
                    shutil.rmtree(dir_path)
                dir_path.mkdir(parents=True, exist_ok=True)

                for name in dir_files:
                    target = CONFIG_DIR / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(name))
                    restored.append(name)
                logger.info("[BACKUP] Restored %d files to %s", len(dir_files), dir_rel)

    finally:
        # Always reinitialize database
        init_db()
        logger.info("[BACKUP] Database reinitialized after restore")

    # Clear settings cache and reset client
    clear_settings_cache()
    try:
        reset_client()
    except Exception as e:
        logger.warning("[BACKUP] Failed to reset Dispatcharr client (non-fatal): %s", e)

    return restored


@router.get("/create")
async def create_backup(_admin=RequireAdminIfEnabled):
    """Create and download a backup zip of all ECM configuration. Admin only."""
    logger.info("[BACKUP] Creating backup")

    try:
        buf = _create_backup_zip()
        filename = _get_backup_filename()

        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.exception("[BACKUP] Failed to create backup: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create backup: %s" % str(e))


@router.post("/restore")
async def restore_backup(file: UploadFile = File(...), _admin=RequireAdminIfEnabled):
    """Restore ECM configuration from an uploaded backup zip. Admin only."""
    logger.info("[BACKUP] Restore requested, filename=%s", file.filename)

    # Read uploaded file
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file: %s" % str(e))

    # Open and validate zip
    try:
        buf = io.BytesIO(content)
        zf = zipfile.ZipFile(buf, "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")

    with zf:
        manifest = _validate_backup_zip(zf)
        restored = _restore_from_zip(zf, manifest)

    logger.info("[BACKUP] Restore complete, %d files restored", len(restored))
    return {
        "status": "ok",
        "backup_version": manifest.get("version", "unknown"),
        "backup_date": manifest.get("created_at", "unknown"),
        "restored_files": restored,
    }


@router.post("/restore-initial")
async def restore_backup_initial(file: UploadFile = File(...)):
    """Restore from backup during initial setup (no auth required).

    Only works when the app is not yet configured (first-run state).
    """
    settings = get_settings()
    if settings.is_configured():
        raise HTTPException(
            status_code=403,
            detail="App is already configured. Use /api/backup/restore instead.",
        )

    logger.info("[BACKUP] Initial restore requested, filename=%s", file.filename)

    # Read uploaded file
    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail="Failed to read uploaded file: %s" % str(e))

    # Open and validate zip
    try:
        buf = io.BytesIO(content)
        zf = zipfile.ZipFile(buf, "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")

    with zf:
        manifest = _validate_backup_zip(zf)
        restored = _restore_from_zip(zf, manifest)

    logger.info("[BACKUP] Initial restore complete, %d files restored", len(restored))
    return {
        "status": "ok",
        "backup_version": manifest.get("version", "unknown"),
        "backup_date": manifest.get("created_at", "unknown"),
        "restored_files": restored,
    }


def _gather_settings() -> dict:
    """Read settings.json and return as dict (excluding sensitive fields)."""
    settings = get_settings()
    data = settings.model_dump()
    # Redact credentials — the export is for review/portability, not secret storage
    for key in _SETTINGS_CREDENTIAL_FIELDS:
        if key in data:
            data[key] = REDACTED
    return data


def _gather_db_tables() -> dict:
    """Export key DB tables as lists of dicts."""
    session = get_session()
    try:
        sections = {}

        # Scheduled tasks
        tasks = session.query(ScheduledTask).all()
        sections["scheduled_tasks"] = [t.to_dict() for t in tasks]

        # Task schedules
        schedules = session.query(TaskSchedule).all()
        sections["task_schedules"] = [
            {
                "task_id": s.task_id,
                "name": s.name,
                "enabled": s.enabled,
                "schedule_type": s.schedule_type,
                "interval_seconds": s.interval_seconds,
                "schedule_time": s.schedule_time,
                "timezone": s.timezone,
                "days_of_week": s.days_of_week,
                "day_of_month": s.day_of_month,
                "week_parity": s.week_parity,
                "parameters": json.loads(s.parameters) if s.parameters else None,
            }
            for s in schedules
        ]

        # Normalization rules
        groups = session.query(NormalizationRuleGroup).all()
        norm_groups = []
        for g in groups:
            rules = session.query(NormalizationRule).filter_by(group_id=g.id).order_by(NormalizationRule.priority).all()
            norm_groups.append({
                **g.to_dict(),
                "rules": [
                    {
                        "name": r.name,
                        "enabled": r.enabled,
                        "priority": r.priority,
                        "condition_type": r.condition_type,
                        "condition_value": r.condition_value,
                        "conditions": json.loads(r.conditions) if r.conditions else None,
                        "condition_logic": r.condition_logic,
                        "action_type": r.action_type,
                        "action_value": r.action_value,
                        "else_action_type": r.else_action_type,
                        "else_action_value": r.else_action_value,
                        "stop_processing": r.stop_processing,
                        "is_builtin": r.is_builtin,
                    }
                    for r in rules
                ],
            })
        sections["normalization_rule_groups"] = norm_groups

        # Tag groups
        tag_groups = session.query(TagGroup).all()
        tag_groups_out = []
        for tg in tag_groups:
            tags = session.query(Tag).filter_by(group_id=tg.id).all()
            tag_groups_out.append({
                **tg.to_dict(),
                "tags": [t.to_dict() for t in tags],
            })
        sections["tag_groups"] = tag_groups_out

        # Auto-creation rules
        ac_rules = session.query(AutoCreationRule).all()
        sections["auto_creation_rules"] = [r.to_dict() for r in ac_rules]

        # FFmpeg profiles
        profiles = session.query(FFmpegProfile).all()
        sections["ffmpeg_profiles"] = [p.to_dict() for p in profiles]

        # Dummy EPG profiles
        depg = session.query(DummyEPGProfile).all()
        depg_out = []
        for d in depg:
            assignments = session.query(DummyEPGChannelAssignment).filter_by(profile_id=d.id).all()
            depg_out.append({
                **d.to_dict(),
                "channel_assignments": [a.to_dict() for a in assignments],
            })
        sections["dummy_epg_profiles"] = depg_out

        return sections
    finally:
        session.close()


async def _gather_dispatcharr_sections(selected: set[str]) -> dict:
    """Fetch full Dispatcharr data for selected sections.

    Returns a dict keyed by section name with full data suitable for restore.
    Only fetches sections that are in the selected set.
    """
    dispatcharr_keys = {k for k, v in RESTORABLE_SECTIONS.items() if v.get("dispatcharr")}
    needed = selected & dispatcharr_keys
    if not needed:
        return {}

    try:
        client = get_client()
        if not client:
            return {"_warning": "Dispatcharr not connected — Dispatcharr sections skipped"}

        result = {}
        if "m3u_accounts" in needed:
            accounts = await client.get_m3u_accounts()
            result["m3u_accounts"] = accounts or []
        if "epg_sources" in needed:
            sources = await client.get_epg_sources()
            result["epg_sources"] = sources or []
        if "channel_groups" in needed:
            groups = await client.get_channel_groups()
            result["channel_groups"] = groups or []
        if "channel_profiles" in needed:
            profiles = await client.get_channel_profiles()
            result["channel_profiles"] = profiles or []
        if "stream_profiles" in needed:
            profiles = await client.get_stream_profiles()
            result["stream_profiles"] = profiles or []

        return result
    except Exception as e:
        logger.warning("[BACKUP] Failed to fetch Dispatcharr data: %s", e)
        return {"_warning": "Dispatcharr not connected — %s" % str(e)}


async def build_yaml_export(sections: Optional[set[str]] = None) -> str:
    """Build a YAML export string, optionally limited to specific sections.

    If sections is None, all sections are included. Otherwise only the
    specified section keys (from RESTORABLE_SECTIONS) are included.
    """
    all_keys = set(RESTORABLE_SECTIONS.keys())
    selected = sections if sections else all_keys

    export_data: dict = {
        "ecm_export": {
            "version": APP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "sections_included": sorted(selected),
        },
    }

    if "settings" in selected:
        export_data["settings"] = _gather_settings()

    # ECM database sections
    db_sections = _gather_db_tables()
    filtered_db = {k: v for k, v in db_sections.items() if k in selected}
    if filtered_db:
        export_data["database"] = filtered_db

    # Dispatcharr-managed sections
    dispatcharr_data = await _gather_dispatcharr_sections(selected)
    if dispatcharr_data:
        export_data["dispatcharr"] = dispatcharr_data

    return yaml.dump(export_data, default_flow_style=False, sort_keys=False, allow_unicode=True)


@router.get("/export-sections")
async def get_export_sections(_admin=RequireAdminIfEnabled):
    """Return available section keys and labels for selective export."""
    return [
        {"key": key, "label": info["label"]}
        for key, info in RESTORABLE_SECTIONS.items()
    ]


@router.get("/export")
async def export_yaml(
    sections: Optional[str] = Query(None, description="Comma-separated section keys to include"),
    _admin=RequireAdminIfEnabled,
):
    """Export ECM configuration as a YAML file download.

    Optionally pass ?sections=settings,tag_groups,... to include only
    specific sections. If omitted, all sections are exported.
    """
    logger.info("[BACKUP] YAML export requested, sections=%s", sections)

    selected = None
    if sections:
        selected = {s.strip() for s in sections.split(",") if s.strip()}
        invalid = selected - set(RESTORABLE_SECTIONS.keys())
        if invalid:
            raise HTTPException(status_code=400, detail="Unknown sections: %s" % ", ".join(sorted(invalid)))

    yaml_str = await build_yaml_export(selected)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filename = f"ecm-export-{now}.yaml"

    logger.info("[BACKUP] YAML export complete, %d bytes", len(yaml_str))
    return PlainTextResponse(
        content=yaml_str,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# YAML Validate & Selective Restore
# ---------------------------------------------------------------------------

# Sections that can be selectively restored from a YAML export.
# Keys map to the YAML structure paths; "db_key" is the key under "database".
RESTORABLE_SECTIONS = {
    "settings": {"label": "Settings"},
    "scheduled_tasks": {"label": "Scheduled Tasks", "db_key": "scheduled_tasks"},
    "task_schedules": {"label": "Task Schedules", "db_key": "task_schedules"},
    "normalization_rule_groups": {"label": "Normalization Rules", "db_key": "normalization_rule_groups"},
    "tag_groups": {"label": "Tag Groups", "db_key": "tag_groups"},
    "auto_creation_rules": {"label": "Auto-Creation Rules", "db_key": "auto_creation_rules"},
    "ffmpeg_profiles": {"label": "FFmpeg Profiles", "db_key": "ffmpeg_profiles"},
    "dummy_epg_profiles": {"label": "Dummy EPG Profiles", "db_key": "dummy_epg_profiles"},
    # Dispatcharr-managed sections (restored via Dispatcharr API)
    "m3u_accounts": {"label": "M3U Accounts", "dispatcharr": True},
    "epg_sources": {"label": "EPG Sources", "dispatcharr": True},
    "channel_groups": {"label": "Channel Groups", "dispatcharr": True},
    "channel_profiles": {"label": "Channel Profiles", "dispatcharr": True},
    "stream_profiles": {"label": "Stream Profiles", "dispatcharr": True},
}


def _parse_yaml_export(content: bytes) -> dict:
    """Parse and validate a YAML export file. Raises HTTPException on failure."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail="Invalid YAML: %s" % str(e))

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid YAML export: expected a mapping at top level")

    if "ecm_export" not in data:
        raise HTTPException(status_code=400, detail="Not a valid ECM YAML export: missing ecm_export header")

    return data


def _count_section_items(data: dict, section_key: str) -> int:
    """Count the number of items in a section of the parsed YAML."""
    if section_key == "settings":
        settings = data.get("settings")
        return len(settings) if isinstance(settings, dict) else 0

    # Check database sections
    db = data.get("database", {})
    if section_key in db:
        items = db[section_key]
        return len(items) if isinstance(items, list) else 0

    # Check dispatcharr sections
    dispatcharr = data.get("dispatcharr", {})
    if section_key in dispatcharr:
        items = dispatcharr[section_key]
        return len(items) if isinstance(items, list) else 0

    return 0


@router.post("/validate")
async def validate_yaml_export(file: UploadFile = File(...), _admin=RequireAdminIfEnabled):
    """Parse a YAML export and return section metadata with item counts.

    Used by the frontend to show which sections are available for selective restore.
    """
    logger.info("[BACKUP] YAML validate requested, filename=%s", file.filename)

    content = await file.read()
    data = _parse_yaml_export(content)

    export_meta = data.get("ecm_export", {})
    sections = []
    for key, info in RESTORABLE_SECTIONS.items():
        count = _count_section_items(data, key)
        sections.append({
            "key": key,
            "label": info["label"],
            "item_count": count,
            "available": count > 0,
        })

    return {
        "valid": True,
        "version": export_meta.get("version"),
        "exported_at": export_meta.get("exported_at"),
        "sections": sections,
    }


class YamlRestoreRequest(BaseModel):
    sections: list[str]


@router.post("/restore-yaml")
async def restore_from_yaml(
    file: UploadFile = File(...),
    sections: str = Body(..., description="JSON array of section keys to restore"),
    _admin=RequireAdminIfEnabled,
):
    """Selectively restore ECM configuration from a YAML export.

    Accepts a YAML file and a list of section keys. Each section is restored
    independently; partial failures are reported without aborting other sections.
    Restore semantics: delete existing → recreate from YAML (replace all).
    """
    logger.info("[BACKUP] YAML restore requested, filename=%s", file.filename)

    # Parse sections list from form field
    try:
        selected_sections = json.loads(sections)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid sections parameter: expected JSON array")

    if not isinstance(selected_sections, list) or not selected_sections:
        raise HTTPException(status_code=400, detail="Must select at least one section to restore")

    # Validate section keys
    invalid = [s for s in selected_sections if s not in RESTORABLE_SECTIONS]
    if invalid:
        raise HTTPException(status_code=400, detail="Unknown sections: %s" % ", ".join(invalid))

    content = await file.read()
    data = _parse_yaml_export(content)

    sections_restored = []
    sections_failed = []
    warnings = []
    errors = []

    for section_key in selected_sections:
        try:
            result = await _restore_section(data, section_key)
            sections_restored.append(section_key)
            if result.get("warnings"):
                warnings.extend(result["warnings"])
            logger.info("[BACKUP] Restored section: %s", section_key)
        except Exception as e:
            sections_failed.append(section_key)
            # CodeQL py/stack-trace-exposure (#1412): do NOT include str(e) in
            # the response. The full exception is logged with type and trace
            # via logger.exception so operators can correlate via X-Request-ID;
            # the client receives only the section key + exception class so
            # internal paths/values do not leak. Restore is admin-only, but
            # ADR-005 disallows "won't fix" dismissal — this is the real fix.
            errors.append("%s: %s" % (section_key, type(e).__name__))
            logger.exception(
                "[BACKUP] Failed to restore section %s", section_key
            )

    success = len(sections_failed) == 0

    logger.info(
        "[BACKUP] YAML restore complete: %d restored, %d failed",
        len(sections_restored), len(sections_failed),
    )
    return {
        "success": success,
        "sections_restored": sections_restored,
        "sections_failed": sections_failed,
        "warnings": warnings,
        "errors": errors,
    }


async def _restore_section(data: dict, section_key: str) -> dict:
    """Restore a single section from parsed YAML. Returns {warnings: [...]}."""
    if section_key == "settings":
        return _restore_settings(data.get("settings", {}))

    # Check DB sections
    db_data = data.get("database", {})
    if section_key in _SECTION_RESTORERS:
        items = db_data.get(section_key, [])
        return _SECTION_RESTORERS[section_key](items)

    # Check Dispatcharr sections
    if section_key in _DISPATCHARR_RESTORERS:
        dispatcharr_data = data.get("dispatcharr", {})
        items = dispatcharr_data.get(section_key, [])
        return await _DISPATCHARR_RESTORERS[section_key](items)

    raise ValueError("No restore handler for section: %s" % section_key)


def _restore_settings(settings_data: dict) -> dict:
    """Restore settings from YAML, preserving redacted credential fields."""
    warnings = []
    current = get_settings()
    merged = current.model_dump()

    for key, value in settings_data.items():
        if value == REDACTED:
            warnings.append("Skipped redacted field: %s (kept existing value)" % key)
            continue
        merged[key] = value

    new_settings = DispatcharrSettings(**merged)
    save_settings(new_settings)
    clear_settings_cache()
    return {"warnings": warnings}


def _restore_scheduled_tasks(items: list) -> dict:
    """Delete all scheduled tasks and recreate from YAML."""
    session = get_session()
    try:
        session.query(ScheduledTask).delete()
        for item in items:
            task = ScheduledTask(
                task_id=item["task_id"],
                task_name=item["task_name"],
                description=item.get("description"),
                enabled=item.get("enabled", True),
                schedule_type=item.get("schedule_type", "manual"),
                interval_seconds=item.get("interval_seconds"),
                cron_expression=item.get("cron_expression"),
                schedule_time=item.get("schedule_time"),
                timezone=item.get("timezone"),
                config=json.dumps(item["config"]) if item.get("config") else None,
                send_alerts=item.get("send_alerts", True),
                alert_on_success=item.get("alert_on_success", True),
                alert_on_warning=item.get("alert_on_warning", True),
                alert_on_error=item.get("alert_on_error", True),
                alert_on_info=item.get("alert_on_info", False),
                send_to_email=item.get("send_to_email", True),
                send_to_discord=item.get("send_to_discord", True),
                send_to_telegram=item.get("send_to_telegram", True),
                show_notifications=item.get("show_notifications", True),
            )
            session.add(task)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_task_schedules(items: list) -> dict:
    """Delete all task schedules and recreate from YAML."""
    session = get_session()
    try:
        session.query(TaskSchedule).delete()
        for item in items:
            schedule = TaskSchedule(
                task_id=item["task_id"],
                name=item.get("name"),
                enabled=item.get("enabled", True),
                schedule_type=item["schedule_type"],
                interval_seconds=item.get("interval_seconds"),
                schedule_time=item.get("schedule_time"),
                timezone=item.get("timezone"),
                days_of_week=item.get("days_of_week"),
                day_of_month=item.get("day_of_month"),
                week_parity=item.get("week_parity"),
                parameters=json.dumps(item["parameters"]) if item.get("parameters") else None,
            )
            session.add(schedule)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_normalization_rule_groups(items: list) -> dict:
    """Delete all normalization groups+rules and recreate from YAML."""
    session = get_session()
    try:
        session.query(NormalizationRule).delete()
        session.query(NormalizationRuleGroup).delete()
        for grp_data in items:
            group = NormalizationRuleGroup(
                name=grp_data["name"],
                description=grp_data.get("description"),
                enabled=grp_data.get("enabled", True),
                priority=grp_data.get("priority", 0),
                is_builtin=grp_data.get("is_builtin", False),
            )
            session.add(group)
            session.flush()  # get group.id

            for rule_data in grp_data.get("rules", []):
                rule = NormalizationRule(
                    group_id=group.id,
                    name=rule_data["name"],
                    enabled=rule_data.get("enabled", True),
                    priority=rule_data.get("priority", 0),
                    condition_type=rule_data.get("condition_type"),
                    condition_value=rule_data.get("condition_value"),
                    conditions=json.dumps(rule_data["conditions"]) if rule_data.get("conditions") else None,
                    condition_logic=rule_data.get("condition_logic", "AND"),
                    action_type=rule_data["action_type"],
                    action_value=rule_data.get("action_value"),
                    else_action_type=rule_data.get("else_action_type"),
                    else_action_value=rule_data.get("else_action_value"),
                    stop_processing=rule_data.get("stop_processing", False),
                    is_builtin=rule_data.get("is_builtin", False),
                )
                session.add(rule)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_tag_groups(items: list) -> dict:
    """Delete all tag groups+tags and recreate from YAML."""
    session = get_session()
    try:
        session.query(Tag).delete()
        session.query(TagGroup).delete()
        for tg_data in items:
            group = TagGroup(
                name=tg_data["name"],
                description=tg_data.get("description"),
                is_builtin=tg_data.get("is_builtin", False),
            )
            session.add(group)
            session.flush()

            for tag_data in tg_data.get("tags", []):
                tag = Tag(
                    group_id=group.id,
                    value=tag_data["value"],
                    case_sensitive=tag_data.get("case_sensitive", False),
                    enabled=tag_data.get("enabled", True),
                    is_builtin=tag_data.get("is_builtin", False),
                )
                session.add(tag)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_auto_creation_rules(items: list) -> dict:
    """Delete all auto-creation rules and recreate from YAML."""
    session = get_session()
    try:
        session.query(AutoCreationRule).delete()
        for item in items:
            rule = AutoCreationRule(
                name=item["name"],
                description=item.get("description"),
                enabled=item.get("enabled", True),
                priority=item.get("priority", 0),
                m3u_account_id=item.get("m3u_account_id"),
                target_group_id=item.get("target_group_id"),
                conditions=json.dumps(item["conditions"]) if item.get("conditions") else "[]",
                actions=json.dumps(item["actions"]) if item.get("actions") else "[]",
                run_on_refresh=item.get("run_on_refresh", False),
                stop_on_first_match=item.get("stop_on_first_match", True),
                sort_field=item.get("sort_field"),
                sort_order=item.get("sort_order", "asc"),
                probe_on_sort=item.get("probe_on_sort", False),
                sort_regex=item.get("sort_regex"),
                stream_sort_field=item.get("stream_sort_field"),
                stream_sort_order=item.get("stream_sort_order", "asc"),
                quality_tie_break_order=item.get("quality_tie_break_order", "desc"),
                quality_m3u_tie_break_enabled=item.get("quality_m3u_tie_break_enabled", True),
                normalization_group_ids=_resolve_backup_normalization_group_ids(item, session),
                skip_struck_streams=item.get("skip_struck_streams", False),
                orphan_action=item.get("orphan_action", "delete"),
                # bd-p6ko9: restore the stored per-rule value; ECM-generated
                # backups always include this field (via to_dict). An ancient
                # backup that omits it inherits the new-rule default (True).
                match_scope_target_group=item.get("match_scope_target_group", True),
                # GH #298 (bd-kncun): None = "Auto" (preserves prior behavior).
                # Backups predating this column omit it and inherit None.
                match_scope_group_id=item.get("match_scope_group_id"),
            )
            session.add(rule)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_ffmpeg_profiles(items: list) -> dict:
    """Delete all FFmpeg profiles and recreate from YAML."""
    session = get_session()
    try:
        session.query(FFmpegProfile).delete()
        for item in items:
            profile = FFmpegProfile(
                name=item["name"],
                config=json.dumps(item["config"]) if item.get("config") else "{}",
            )
            session.add(profile)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_dummy_epg_profiles(items: list) -> dict:
    """Delete all dummy EPG profiles+assignments and recreate from YAML."""
    session = get_session()
    try:
        session.query(DummyEPGChannelAssignment).delete()
        session.query(DummyEPGProfile).delete()
        for item in items:
            profile = DummyEPGProfile(
                name=item["name"],
                enabled=item.get("enabled", True),
                name_source=item.get("name_source", "channel"),
                stream_index=item.get("stream_index", 1),
                title_pattern=item.get("title_pattern"),
                time_pattern=item.get("time_pattern"),
                date_pattern=item.get("date_pattern"),
                substitution_pairs=json.dumps(item["substitution_pairs"]) if item.get("substitution_pairs") else None,
                title_template=item.get("title_template"),
                description_template=item.get("description_template"),
                upcoming_title_template=item.get("upcoming_title_template"),
                upcoming_description_template=item.get("upcoming_description_template"),
                ended_title_template=item.get("ended_title_template"),
                ended_description_template=item.get("ended_description_template"),
                fallback_title_template=item.get("fallback_title_template"),
                fallback_description_template=item.get("fallback_description_template"),
                event_timezone=item.get("event_timezone", "US/Eastern"),
                output_timezone=item.get("output_timezone"),
                program_duration=item.get("program_duration", 180),
                categories=item.get("categories"),
                channel_logo_url_template=item.get("channel_logo_url_template"),
                program_poster_url_template=item.get("program_poster_url_template"),
                tvg_id_template=item.get("tvg_id_template", "ecm-{channel_number}"),
                include_date_tag=item.get("include_date_tag", False),
                include_live_tag=item.get("include_live_tag", False),
                include_new_tag=item.get("include_new_tag", False),
                pattern_builder_examples=item.get("pattern_builder_examples"),
                pattern_variants=json.dumps(item["pattern_variants"]) if item.get("pattern_variants") else None,
                channel_group_ids=json.dumps(item["channel_group_ids"]) if item.get("channel_group_ids") else None,
            )
            session.add(profile)
            session.flush()

            for assignment in item.get("channel_assignments", []):
                a = DummyEPGChannelAssignment(
                    profile_id=profile.id,
                    channel_id=assignment["channel_id"],
                    channel_name=assignment["channel_name"],
                    tvg_id_override=assignment.get("tvg_id_override"),
                )
                session.add(a)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Registry mapping section keys to their restore functions
_SECTION_RESTORERS = {
    "scheduled_tasks": _restore_scheduled_tasks,
    "task_schedules": _restore_task_schedules,
    "normalization_rule_groups": _restore_normalization_rule_groups,
    "tag_groups": _restore_tag_groups,
    "auto_creation_rules": _restore_auto_creation_rules,
    "ffmpeg_profiles": _restore_ffmpeg_profiles,
    "dummy_epg_profiles": _restore_dummy_epg_profiles,
}


# ---------------------------------------------------------------------------
# Dispatcharr section restore functions (async — use Dispatcharr API)
# ---------------------------------------------------------------------------

async def _restore_m3u_accounts(items: list) -> dict:
    """Delete all M3U accounts and recreate from YAML via Dispatcharr API."""
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped M3U accounts restore"]}
    warnings = []
    # Delete existing
    existing = await client.get_m3u_accounts() or []
    for acct in existing:
        try:
            await client.delete_m3u_account(acct["id"])
        except Exception as e:
            warnings.append("Failed to delete M3U account %s: %s" % (acct.get("name"), e))
    # Recreate
    for item in items:
        create_data = {k: v for k, v in item.items() if k not in ("id", "channel_groups", "streams_count")}
        try:
            await client.create_m3u_account(create_data)
        except Exception as e:
            warnings.append("Failed to create M3U account %s: %s" % (item.get("name"), e))
    return {"warnings": warnings}


async def _restore_epg_sources(items: list) -> dict:
    """Delete all EPG sources and recreate from YAML via Dispatcharr API."""
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped EPG sources restore"]}
    warnings = []
    existing = await client.get_epg_sources() or []
    for src in existing:
        try:
            await client.delete_epg_source(src["id"])
        except Exception as e:
            warnings.append("Failed to delete EPG source %s: %s" % (src.get("name"), e))
    for item in items:
        create_data = {k: v for k, v in item.items() if k not in ("id",)}
        try:
            await client.create_epg_source(create_data)
        except Exception as e:
            warnings.append("Failed to create EPG source %s: %s" % (item.get("name"), e))
    return {"warnings": warnings}


async def _restore_channel_groups(items: list) -> dict:
    """Upsert channel groups by name via Dispatcharr API.

    Channel groups are referenced by ID from channels and streams. Deleting and
    recreating them would orphan those references, so we only create groups that
    don't already exist (matched by name) and leave existing groups intact.
    """
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped channel groups restore"]}
    warnings = []
    existing = await client.get_channel_groups() or []
    existing_names = {g.get("name") for g in existing}
    created = 0
    for item in items:
        name = item.get("name")
        if not name or name in existing_names:
            continue
        try:
            await client.create_channel_group(name)
            existing_names.add(name)
            created += 1
        except Exception as e:
            warnings.append("Failed to create channel group %s: %s" % (name, e))
    logger.info("[BACKUP] Channel groups restore: created %d new groups, kept %d existing", created, len(existing))
    return {"warnings": warnings}


async def _restore_channel_profiles(items: list) -> dict:
    """Delete all channel profiles and recreate from YAML via Dispatcharr API."""
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped channel profiles restore"]}
    warnings = []
    existing = await client.get_channel_profiles() or []
    for prof in existing:
        try:
            await client.delete_channel_profile(prof["id"])
        except Exception as e:
            warnings.append("Failed to delete channel profile %s: %s" % (prof.get("name"), e))
    for item in items:
        create_data = {k: v for k, v in item.items() if k not in ("id",)}
        try:
            await client.create_channel_profile(create_data)
        except Exception as e:
            warnings.append("Failed to create channel profile %s: %s" % (item.get("name"), e))
    return {"warnings": warnings}


async def _restore_stream_profiles(items: list) -> dict:
    """Recreate stream profiles from YAML via Dispatcharr API.

    Note: Dispatcharr stream profiles cannot be deleted via API,
    so we only create missing ones.
    """
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped stream profiles restore"]}
    warnings = []
    existing = await client.get_stream_profiles() or []
    existing_names = {p.get("name") for p in existing}
    for item in items:
        if item.get("name") in existing_names:
            continue  # Skip already existing
        create_data = {k: v for k, v in item.items() if k not in ("id",)}
        try:
            await client.create_stream_profile(create_data)
        except Exception as e:
            warnings.append("Failed to create stream profile %s: %s" % (item.get("name"), e))
    if existing_names:
        warnings.append("Existing stream profiles kept (cannot be deleted via API)")
    return {"warnings": warnings}


# Registry for async Dispatcharr restore functions
_DISPATCHARR_RESTORERS = {
    "m3u_accounts": _restore_m3u_accounts,
    "epg_sources": _restore_epg_sources,
    "channel_groups": _restore_channel_groups,
    "channel_profiles": _restore_channel_profiles,
    "stream_profiles": _restore_stream_profiles,
}


# ---------------------------------------------------------------------------
# Saved Backups (on-disk YAML files from scheduled task)
# ---------------------------------------------------------------------------

BACKUPS_DIR = CONFIG_DIR / "backups"
# Strict allowlist for the on-disk filename shape. ``.yaml`` = scheduled YAML
# export; ``.zip`` = on-demand full backup persisted by POST /save (bd-0hjrk.5).
# Both download_saved_backup and delete_saved_backup accept either extension;
# restore-saved further restricts to ``.zip`` (the full-archive restore path).
_BACKUP_FILENAME_RE = re.compile(r"^ecm-backup-\d{4}-\d{2}-\d{2}_\d{6}\.(yaml|zip)$")
# Zip-only allowlist for the full-archive restore path (restore-saved) and the
# on-demand save path. YAML section-import is a different path
# (POST /restore-yaml) and out of scope here.
_BACKUP_ZIP_FILENAME_RE = re.compile(r"^ecm-backup-\d{4}-\d{2}-\d{2}_\d{6}\.zip$")

# SECURITY (CodeQL py/path-injection, CWE-22/23/36/73/99): the two-layer guard
# (strict regex allowlist + canonicalize-and-verify containment under
# BACKUPS_DIR) is INLINED at each filename-addressed endpoint below rather than
# factored into a shared helper. CodeQL's dataflow tracker does NOT follow the
# `relative_to` containment barrier across a function-return boundary, so a
# helper that validates-then-returns a Path is still treated as user-tainted at
# every downstream file-op sink. Keeping the barrier and the file op in the
# SAME function body is the proven-passing pattern (matches origin/dev). The
# allowlist uses re.fullmatch (not .match) so a trailing newline cannot slip
# past the `$` anchor (SEC-1 hardening).


@router.get("/saved")
async def list_saved_backups(_admin=RequireAdminIfEnabled):
    """List saved backup files on disk (YAML exports + on-demand ZIP archives),
    newest first. Each entry carries a ``type`` field: "yaml" (scheduled YAML
    export) or "zip" (full on-demand backup persisted by POST /save)."""
    if not BACKUPS_DIR.exists():
        return []
    files = sorted(
        list(BACKUPS_DIR.glob("ecm-backup-*.yaml"))
        + list(BACKUPS_DIR.glob("ecm-backup-*.zip")),
        key=lambda f: f.name,
        reverse=True,
    )
    return [
        {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            "type": "zip" if f.suffix == ".zip" else "yaml",
        }
        for f in files
    ]


@router.post("/save")
async def save_backup(_admin=RequireAdminIfEnabled):
    """Create a full backup ZIP and PERSIST it to BACKUPS_DIR. Admin only.

    Unlike GET /create (which streams the ZIP to the HTTP client and persists
    nothing), this writes the same ``_create_backup_zip()`` artifact to disk as
    ``ecm-backup-<UTC ts>.zip`` so it is discoverable via GET /saved and
    restorable via POST /restore-saved (bd-0hjrk.5). The persisted ZIP is the
    same redacted artifact GET /create produces — redaction is unchanged.
    """
    logger.info("[BACKUP] Saving backup to disk")
    try:
        buf = _create_backup_zip()
        data = buf.getvalue()
        filename = _get_backup_filename()  # ecm-backup-<UTC ts>.zip
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        # Two-layer guard, inlined (CodeQL py/path-injection, CWE-22/23/36/73/99
        # — cross-function barrier not tracked; keep barrier+sink in one body).
        # Layer 1 (defense in depth): strict zip-only regex allowlist (fullmatch
        # so a trailing newline cannot pass the anchor).
        if not _BACKUP_ZIP_FILENAME_RE.fullmatch(filename):
            raise HTTPException(status_code=400, detail="Invalid filename")
        # Layer 2: canonicalize + verify containment under BACKUPS_DIR.
        try:
            safe_root = BACKUPS_DIR.resolve()
            path = (BACKUPS_DIR / filename).resolve()
            path.relative_to(safe_root)
        except (ValueError, OSError):
            raise HTTPException(status_code=400, detail="Invalid filename")
        path.write_bytes(data)
        logger.info("[BACKUP] Saved backup %s (%d bytes)", filename, len(data))
        return {
            "filename": filename,
            "size_bytes": len(data),
            "created_at": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[BACKUP] Failed to save backup: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save backup: %s" % str(e))


class RestoreSavedRequest(BaseModel):
    filename: str


@router.post("/restore-saved")
async def restore_saved_backup(req: RestoreSavedRequest, _admin=RequireAdminIfEnabled):
    """Restore ECM configuration from an on-disk saved backup ZIP. Admin only.

    Takes ``{"filename": "ecm-backup-<ts>.zip"}``, validates it through the
    strict regex + containment guard (zip-only allowlist), then restores from
    the on-disk archive reusing the EXACT same validate + restore code path as
    the uploaded-ZIP POST /restore (``_validate_backup_zip`` +
    ``_restore_from_zip``). YAML artifacts are rejected — section-import is a
    different path (POST /restore-yaml), out of scope here (bd-0hjrk.5).

    WARNING: this OVERWRITES current ECM state (settings, database, logos).
    """
    logger.info("[BACKUP] Restore-from-saved requested, filename=%s", req.filename)
    filename = req.filename
    # Two-layer guard, inlined (CodeQL py/path-injection, CWE-22/23/36/73/99 —
    # the containment barrier is not tracked across a function-return boundary,
    # so the barrier and the read_bytes sink must live in this same function).
    # Layer 1 (defense in depth): strict zip-only regex allowlist (fullmatch so a
    # trailing newline cannot pass the anchor).
    if not _BACKUP_ZIP_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Layer 2: canonicalize + verify containment under BACKUPS_DIR.
    try:
        safe_root = BACKUPS_DIR.resolve()
        path = (BACKUPS_DIR / filename).resolve()
        path.relative_to(safe_root)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")

    # Open + validate + restore via the SAME path the uploaded-ZIP restore uses.
    try:
        buf = io.BytesIO(path.read_bytes())
        zf = zipfile.ZipFile(buf, "r")
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="Saved file is not a valid zip archive")

    with zf:
        manifest = _validate_backup_zip(zf)
        restored = _restore_from_zip(zf, manifest)

    logger.info("[BACKUP] Restore-from-saved complete, %d files restored", len(restored))
    return {
        "status": "ok",
        "filename": req.filename,
        "backup_version": manifest.get("version", "unknown"),
        "backup_date": manifest.get("created_at", "unknown"),
        "restored_files": restored,
    }


@router.get("/saved/{filename}")
async def download_saved_backup(filename: str, _admin=RequireAdminIfEnabled):
    """Download a saved backup file (YAML export or ZIP archive)."""
    # Two-layer guard, inlined (CodeQL py/path-injection, CWE-22/23/36/73/99 —
    # the containment barrier is not tracked across a function-return boundary,
    # so the barrier and the read_bytes/read_text sinks must live in this same
    # function). Mirrors origin/dev's proven-passing inline pattern.
    # Layer 1 (defense in depth): strict regex allowlist (fullmatch so a trailing
    # newline cannot pass the anchor).
    if not _BACKUP_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Layer 2: canonicalize + verify containment under BACKUPS_DIR.
    try:
        safe_root = BACKUPS_DIR.resolve()
        path = (BACKUPS_DIR / filename).resolve()
        path.relative_to(safe_root)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    if filename.endswith(".zip"):
        return StreamingResponse(
            io.BytesIO(path.read_bytes()),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    content = path.read_text()
    return PlainTextResponse(
        content=content,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/saved/{filename}", status_code=200)
async def delete_saved_backup(filename: str, _admin=RequireAdminIfEnabled):
    """Delete a saved backup file (YAML export or ZIP archive)."""
    # Two-layer guard, inlined (CodeQL py/path-injection, CWE-22/23/36/73/99 —
    # the containment barrier is not tracked across a function-return boundary,
    # so the barrier and the unlink sink must live in this same function). See
    # download_saved_backup above for the rationale.
    # Layer 1 (defense in depth): strict regex allowlist (fullmatch so a trailing
    # newline cannot pass the anchor).
    if not _BACKUP_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Layer 2: canonicalize + verify containment under BACKUPS_DIR.
    try:
        safe_root = BACKUPS_DIR.resolve()
        path = (BACKUPS_DIR / filename).resolve()
        path.relative_to(safe_root)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    path.unlink()
    logger.info("[BACKUP] Deleted saved backup: %s", filename)
    return {"status": "ok", "deleted": filename}
