"""
Backup & Restore router — create and restore ECM configuration backups.

Backs up: settings.json, journal.db, uploads/logos/, tls/, m3u_uploads/
YAML export: settings + DB tables + Dispatcharr state in a single file.
"""
import io
import json
import logging
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import text

from auth import RequireAdminIfEnabled
from config import CONFIG_DIR, CONFIG_FILE, get_settings, clear_settings_cache
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
    # Flush SQLite WAL to ensure journal.db is self-contained
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


async def _gather_dispatcharr() -> dict:
    """Fetch summary data from Dispatcharr. Returns empty dict + warning on failure."""
    try:
        client = get_client()
        if not client:
            return {"_warning": "Dispatcharr not connected — section empty"}

        channels = await client.get_channels()
        channel_groups = await client.get_channel_groups()
        m3u_accounts = await client.get_m3u_accounts()
        epg_sources = await client.get_epg_sources()
        stream_profiles = await client.get_stream_profiles()
        channel_profiles = await client.get_channel_profiles()

        return {
            "channels_count": len(channels) if channels else 0,
            "channel_groups": channel_groups or [],
            "m3u_accounts": [
                {"id": a.get("id"), "name": a.get("name"), "url": a.get("url")}
                for a in (m3u_accounts or [])
            ],
            "epg_sources": [
                {"id": e.get("id"), "name": e.get("name"), "url": e.get("url")}
                for e in (epg_sources or [])
            ],
            "stream_profiles": stream_profiles or [],
            "channel_profiles": channel_profiles or [],
        }
    except Exception as e:
        logger.warning("[BACKUP] Failed to fetch Dispatcharr data: %s", e)
        return {"_warning": "Dispatcharr not connected — %s" % str(e)}


@router.get("/export")
async def export_yaml(_admin=RequireAdminIfEnabled):
    """Export full ECM configuration as a YAML file download.

    Gathers settings, DB tables, and Dispatcharr state into one file.
    """
    logger.info("[BACKUP] YAML export requested")

    export_data = {
        "ecm_export": {
            "version": APP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
        },
        "settings": _gather_settings(),
        "database": _gather_db_tables(),
        "dispatcharr": await _gather_dispatcharr(),
    }

    yaml_str = yaml.dump(export_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filename = f"ecm-export-{now}.yaml"

    logger.info("[BACKUP] YAML export complete, %d bytes", len(yaml_str))
    return PlainTextResponse(
        content=yaml_str,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
