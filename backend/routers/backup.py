"""
Backup & Restore router — create and restore ECM configuration backups.

Backs up: settings.json, journal.db, uploads/logos/, tls/, m3u_uploads/
YAML export: settings + DB tables + Dispatcharr state in a single file.
"""
import io
import json
import logging
import re
import shutil
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

# App version for manifest (imported at call time to avoid circular imports)
APP_VERSION = "0.16.0"


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


def _create_backup_zip() -> io.BytesIO:
    """Create a zip file containing all ECM config data."""
    # Flush SQLite WAL so journal.db is self-contained before we zip it.
    # WAL mode is enabled by the engine-connect PRAGMA listener in
    # database.py, so the checkpoint is meaningful: without it, recent
    # writes would still live in journal.db-wal and be lost from the backup.
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            conn.commit()
        logger.info("[BACKUP] WAL checkpoint completed")
    except Exception as e:
        logger.warning("[BACKUP] WAL checkpoint failed (non-fatal): %s", e)

    buf = io.BytesIO()
    files_added = []

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add settings.json
        if CONFIG_FILE.exists():
            zf.write(CONFIG_FILE, "settings.json")
            files_added.append("settings.json")
            logger.info("[BACKUP] Added settings.json")

        # Add journal.db
        if JOURNAL_DB_FILE.exists():
            zf.write(JOURNAL_DB_FILE, "journal.db")
            files_added.append("journal.db")
            logger.info("[BACKUP] Added journal.db (%d bytes)", JOURNAL_DB_FILE.stat().st_size)

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

    buf.seek(0)
    logger.info("[BACKUP] Backup created with %d files", len(files_added))
    return buf


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


def _restore_from_zip(zf: zipfile.ZipFile, manifest: dict) -> list[str]:
    """Restore files from a validated backup zip."""
    restored = []

    # Close database before replacing files
    close_db()
    logger.info("[BACKUP] Database closed for restore")

    try:
        # Restore settings.json
        if "settings.json" in zf.namelist():
            CONFIG_FILE.write_bytes(zf.read("settings.json"))
            restored.append("settings.json")
            logger.info("[BACKUP] Restored settings.json")

        # Restore journal.db
        if "journal.db" in zf.namelist():
            JOURNAL_DB_FILE.write_bytes(zf.read("journal.db"))
            restored.append("journal.db")
            logger.info("[BACKUP] Restored journal.db")

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
    for key in ("password", "smtp_password"):
        if key in data:
            data[key] = "***REDACTED***"
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

REDACTED = "***REDACTED***"


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
            errors.append("%s: %s" % (section_key, str(e)))
            logger.warning("[BACKUP] Failed to restore section %s: %s", section_key, e)

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
                normalization_group_ids=_resolve_backup_normalization_group_ids(item, session),
                skip_struck_streams=item.get("skip_struck_streams", False),
                orphan_action=item.get("orphan_action", "delete"),
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
_BACKUP_FILENAME_RE = re.compile(r"^ecm-backup-\d{4}-\d{2}-\d{2}_\d{6}\.yaml$")


@router.get("/saved")
async def list_saved_backups(_admin=RequireAdminIfEnabled):
    """List saved YAML backup files on disk, newest first."""
    if not BACKUPS_DIR.exists():
        return []
    files = sorted(BACKUPS_DIR.glob("ecm-backup-*.yaml"), reverse=True)
    return [
        {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
        }
        for f in files
    ]


@router.get("/saved/{filename}")
async def download_saved_backup(filename: str, _admin=RequireAdminIfEnabled):
    """Download a saved YAML backup file."""
    if not _BACKUP_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = BACKUPS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    content = path.read_text()
    return PlainTextResponse(
        content=content,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/saved/{filename}", status_code=200)
async def delete_saved_backup(filename: str, _admin=RequireAdminIfEnabled):
    """Delete a saved YAML backup file."""
    if not _BACKUP_FILENAME_RE.match(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = BACKUPS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    path.unlink()
    logger.info("[BACKUP] Deleted saved backup: %s", filename)
    return {"status": "ok", "deleted": filename}
