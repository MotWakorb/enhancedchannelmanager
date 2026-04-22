"""
Export router — playlist profile CRUD, generate, preview, download,
and cloud storage target CRUD endpoints.
"""
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, field_validator

from cloud_storage.base import get_adapter
from cloud_storage.crypto import encrypt_credentials, decrypt_credentials
from cloud_storage.onedrive_adapter import _validate_tenant_id, _validate_drive_id
from database import get_session
from dispatcharr_client import get_client
from export_manager import ExportManager
from export_models import PlaylistProfile, CloudStorageTarget, PublishConfiguration, PublishHistory
from publish_pipeline import execute_publish, VALID_EVENT_TRIGGERS
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/export", tags=["Export"])

_export_manager = ExportManager()

# Track in-progress generations to prevent concurrent runs per profile
_generating: set[int] = set()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

FILENAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

SelectionMode = Literal["all", "groups", "channels"]
SortOrder = Literal["name", "number", "group"]
UrlMode = Literal["direct", "proxy"]


class ProfileCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    selection_mode: SelectionMode = "all"
    selected_groups: list[int] = []
    selected_channels: list[int] = []
    stream_url_mode: UrlMode = "direct"
    include_logos: bool = True
    include_epg_ids: bool = True
    include_channel_numbers: bool = True
    sort_order: SortOrder = "number"
    filename_prefix: str = "playlist"

    @field_validator("filename_prefix")
    @classmethod
    def validate_filename(cls, v):
        if not v or not FILENAME_RE.match(v):
            raise ValueError("filename_prefix must be alphanumeric, hyphens, or underscores only")
        return v


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    selection_mode: Optional[SelectionMode] = None
    selected_groups: Optional[list[int]] = None
    selected_channels: Optional[list[int]] = None
    stream_url_mode: Optional[UrlMode] = None
    include_logos: Optional[bool] = None
    include_epg_ids: Optional[bool] = None
    include_channel_numbers: Optional[bool] = None
    sort_order: Optional[SortOrder] = None
    filename_prefix: Optional[str] = None

    @field_validator("filename_prefix")
    @classmethod
    def validate_filename(cls, v):
        if v is not None and (not v or not FILENAME_RE.match(v)):
            raise ValueError("filename_prefix must be alphanumeric, hyphens, or underscores only")
        return v


# ---------------------------------------------------------------------------
# Profile CRUD
# ---------------------------------------------------------------------------

@router.get("/profiles")
async def list_profiles():
    """List all playlist profiles."""
    db = get_session()
    try:
        profiles = db.query(PlaylistProfile).order_by(PlaylistProfile.name).all()
        result = []
        for p in profiles:
            data = p.to_dict()
            # Add file info if exports exist
            export_dir = _export_manager.get_export_path(p.id)
            prefix = p.filename_prefix or "playlist"
            m3u_file = export_dir / f"{prefix}.m3u"
            xmltv_file = export_dir / f"{prefix}.xml"
            data["has_generated"] = m3u_file.exists()
            if m3u_file.exists():
                data["m3u_size"] = m3u_file.stat().st_size
                data["last_generated_at"] = m3u_file.stat().st_mtime
            if xmltv_file.exists():
                data["xmltv_size"] = xmltv_file.stat().st_size
            result.append(data)
        return result
    finally:
        db.close()


@router.post("/profiles", status_code=201)
async def create_profile(req: ProfileCreateRequest):
    """Create a new playlist profile."""
    # Validate non-empty selection for non-all modes
    if req.selection_mode == "groups" and not req.selected_groups:
        raise HTTPException(status_code=400, detail="selected_groups required when selection_mode is 'groups'")
    if req.selection_mode == "channels" and not req.selected_channels:
        raise HTTPException(status_code=400, detail="selected_channels required when selection_mode is 'channels'")

    db = get_session()
    try:
        # Check name uniqueness
        existing = db.query(PlaylistProfile).filter(PlaylistProfile.name == req.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Profile with name '{req.name}' already exists")

        profile = PlaylistProfile(
            name=req.name,
            description=req.description,
            selection_mode=req.selection_mode,
            selected_groups=json.dumps(req.selected_groups),
            selected_channels=json.dumps(req.selected_channels),
            stream_url_mode=req.stream_url_mode,
            include_logos=req.include_logos,
            include_epg_ids=req.include_epg_ids,
            include_channel_numbers=req.include_channel_numbers,
            sort_order=req.sort_order,
            filename_prefix=req.filename_prefix,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)

        journal.log_entry(
            category="export",
            action_type="create",
            entity_name=profile.name,
            description=f"Created export profile '{profile.name}'",
            entity_id=profile.id,
            after_value=profile.to_dict(),
        )

        logger.info("[EXPORT] Created profile id=%s name=%s", profile.id, profile.name)
        return profile.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to create profile: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.patch("/profiles/{profile_id}")
async def update_profile(profile_id: int, req: ProfileUpdateRequest):
    """Partially update a playlist profile."""
    db = get_session()
    try:
        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        before = profile.to_dict()

        # Check name uniqueness if changing name
        if req.name is not None and req.name != profile.name:
            existing = db.query(PlaylistProfile).filter(
                PlaylistProfile.name == req.name, PlaylistProfile.id != profile_id
            ).first()
            if existing:
                raise HTTPException(status_code=409, detail=f"Profile with name '{req.name}' already exists")

        # Validate selection for the effective mode
        effective_mode = req.selection_mode if req.selection_mode is not None else profile.selection_mode
        if effective_mode == "groups":
            effective_groups = req.selected_groups if req.selected_groups is not None else profile.get_selected_groups()
            if not effective_groups:
                raise HTTPException(status_code=400, detail="selected_groups required when selection_mode is 'groups'")
        if effective_mode == "channels":
            effective_channels = req.selected_channels if req.selected_channels is not None else profile.get_selected_channels()
            if not effective_channels:
                raise HTTPException(status_code=400, detail="selected_channels required when selection_mode is 'channels'")

        # Apply updates
        update_data = req.model_dump(exclude_none=True)
        for field, value in update_data.items():
            if field in ("selected_groups", "selected_channels"):
                setattr(profile, field, json.dumps(value))
            else:
                setattr(profile, field, value)

        db.commit()
        db.refresh(profile)

        journal.log_entry(
            category="export",
            action_type="update",
            entity_name=profile.name,
            description=f"Updated export profile '{profile.name}'",
            entity_id=profile.id,
            before_value=before,
            after_value=profile.to_dict(),
        )

        logger.info("[EXPORT] Updated profile id=%s", profile_id)
        return profile.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to update profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int):
    """Delete a playlist profile and its export files."""
    db = get_session()
    try:
        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        name = profile.name
        db.delete(profile)
        db.commit()

        # Clean up export files
        _export_manager.cleanup(profile_id)

        journal.log_entry(
            category="export",
            action_type="delete",
            entity_name=name,
            description=f"Deleted export profile '{name}'",
            entity_id=profile_id,
        )

        logger.info("[EXPORT] Deleted profile id=%s name=%s", profile_id, name)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to delete profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------

@router.post("/profiles/{profile_id}/generate")
async def generate_export(profile_id: int):
    """Generate M3U and XMLTV files for a profile."""
    if profile_id in _generating:
        raise HTTPException(status_code=409, detail="Generation already in progress for this profile")

    db = get_session()
    try:
        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")

        profile_dict = profile.to_dict()
    finally:
        db.close()

    journal.log_entry(
        category="export",
        action_type="generate_started",
        entity_name=profile_dict["name"],
        description=f"Started export generation for '{profile_dict['name']}'",
        entity_id=profile_id,
        user_initiated=True,
    )

    _generating.add(profile_id)
    start_time = time.time()
    try:
        result = await _export_manager.generate(profile_dict)
        duration_ms = int((time.time() - start_time) * 1000)
        result["duration_ms"] = duration_ms

        journal.log_entry(
            category="export",
            action_type="generate_completed",
            entity_name=profile_dict["name"],
            description=f"Export generation completed for '{profile_dict['name']}': {result.get('channels_count', 0)} channels in {duration_ms}ms",
            entity_id=profile_id,
            after_value=result,
            user_initiated=False,
        )

        logger.info("[EXPORT] Generation complete for profile %s in %sms", profile_id, duration_ms)
        return result
    except Exception as e:
        duration_ms = int((time.time() - start_time) * 1000)
        journal.log_entry(
            category="export",
            action_type="generate_failed",
            entity_name=profile_dict["name"],
            description=f"Export generation failed for '{profile_dict['name']}': {e}",
            entity_id=profile_id,
            after_value={"error": str(e), "duration_ms": duration_ms},
            user_initiated=False,
        )
        logger.warning("[EXPORT] Generation failed for profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _generating.discard(profile_id)


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

@router.get("/profiles/{profile_id}/preview")
async def preview_export(profile_id: int):
    """Preview which channels would be included without generating files."""
    db = get_session()
    try:
        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        profile_dict = profile.to_dict()
    finally:
        db.close()

    try:
        client = get_client()
        channels = await _export_manager._fetch_channels(client, profile_dict)
        channels = _export_manager._sort_channels(channels, profile_dict.get("sort_order", "number"))

        preview_channels = []
        for ch in channels[:10]:
            preview_channels.append({
                "id": ch.get("id"),
                "name": ch.get("name"),
                "channel_number": ch.get("channel_number"),
                "channel_group_name": ch.get("channel_group_name"),
                "tvg_id": ch.get("tvg_id"),
                "logo_url": ch.get("logo_url"),
                "stream_count": len(ch.get("streams", [])),
            })

        return {
            "total_channels": len(channels),
            "preview_channels": preview_channels,
        }
    except Exception as e:
        logger.warning("[EXPORT] Preview failed for profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

@router.get("/profiles/{profile_id}/download/m3u")
async def download_m3u(profile_id: int, regenerate: bool = False):
    """Download the generated M3U file."""
    db = get_session()
    try:
        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        profile_dict = profile.to_dict()
        prefix = profile.filename_prefix or "playlist"
    finally:
        db.close()

    if regenerate:
        await _export_manager.generate(profile_dict)

    m3u_path = _export_manager.get_export_path(profile_id) / f"{prefix}.m3u"
    if not m3u_path.exists():
        raise HTTPException(status_code=404, detail="M3U file not generated yet. Call generate first.")

    journal.log_entry(
        category="export",
        action_type="download",
        entity_name=profile_dict["name"],
        description=f"Downloaded M3U for '{profile_dict['name']}'",
        entity_id=profile_id,
    )

    content = m3u_path.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="{prefix}.m3u"'},
    )


@router.get("/profiles/{profile_id}/download/xmltv")
async def download_xmltv(profile_id: int, regenerate: bool = False):
    """Download the generated XMLTV file."""
    db = get_session()
    try:
        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == profile_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found")
        profile_dict = profile.to_dict()
        prefix = profile.filename_prefix or "playlist"
    finally:
        db.close()

    if regenerate:
        await _export_manager.generate(profile_dict)

    xmltv_path = _export_manager.get_export_path(profile_id) / f"{prefix}.xml"
    if not xmltv_path.exists():
        raise HTTPException(status_code=404, detail="XMLTV file not generated yet. Call generate first.")

    journal.log_entry(
        category="export",
        action_type="download",
        entity_name=profile_dict["name"],
        description=f"Downloaded XMLTV for '{profile_dict['name']}'",
        entity_id=profile_id,
    )

    content = xmltv_path.read_text(encoding="utf-8")
    return Response(
        content=content,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{prefix}.xml"'},
    )


# ---------------------------------------------------------------------------
# Cloud Storage Target Pydantic models
# ---------------------------------------------------------------------------

VALID_PROVIDERS = ("s3", "gdrive", "onedrive", "dropbox")


def _validate_onedrive_credentials(provider_type: Optional[str], creds: Optional[dict]) -> Optional[dict]:
    """If provider_type is onedrive, validate tenant_id/drive_id shape.

    Rejects SSRF-prone identifiers at the API boundary. See CodeQL alerts
    1361 and 1362 and bead enhancedchannelmanager-zbt74.
    """
    if provider_type != "onedrive" or not creds:
        return creds
    tenant_id = creds.get("tenant_id")
    if tenant_id is not None:
        try:
            _validate_tenant_id(tenant_id)
        except ValueError as exc:
            raise ValueError(f"credentials.tenant_id: {exc}") from exc
    drive_id = creds.get("drive_id")
    if drive_id is not None and drive_id != "":
        try:
            _validate_drive_id(drive_id)
        except ValueError as exc:
            raise ValueError(f"credentials.drive_id: {exc}") from exc
    return creds


class CloudTargetCreateRequest(BaseModel):
    name: str
    provider_type: Literal["s3", "gdrive", "onedrive", "dropbox"]
    credentials: dict
    upload_path: str = "/"
    enabled: bool = True

    @field_validator("credentials")
    @classmethod
    def _check_credentials(cls, v, info):
        return _validate_onedrive_credentials(info.data.get("provider_type"), v)


class CloudTargetUpdateRequest(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[Literal["s3", "gdrive", "onedrive", "dropbox"]] = None
    credentials: Optional[dict] = None
    upload_path: Optional[str] = None
    enabled: Optional[bool] = None

    @field_validator("credentials")
    @classmethod
    def _check_credentials(cls, v, info):
        return _validate_onedrive_credentials(info.data.get("provider_type"), v)


class CloudTargetTestRequest(BaseModel):
    provider_type: Literal["s3", "gdrive", "onedrive", "dropbox"]
    credentials: dict

    @field_validator("credentials")
    @classmethod
    def _check_credentials(cls, v, info):
        return _validate_onedrive_credentials(info.data.get("provider_type"), v)


def _mask_credentials(creds: dict) -> dict:
    """Mask sensitive credential values, showing only last 4 chars."""
    masked = {}
    for key, value in creds.items():
        if isinstance(value, str) and len(value) > 8:
            masked[key] = "***" + value[-4:]
        elif isinstance(value, str):
            masked[key] = "***"
        elif isinstance(value, dict):
            masked[key] = _mask_credentials(value)
        else:
            masked[key] = value
    return masked


# ---------------------------------------------------------------------------
# Cloud Storage Target CRUD
# ---------------------------------------------------------------------------

@router.get("/cloud-targets")
async def list_cloud_targets():
    """List all cloud storage targets with masked credentials."""
    db = get_session()
    try:
        targets = db.query(CloudStorageTarget).order_by(CloudStorageTarget.name).all()
        result = []
        for t in targets:
            data = t.to_dict(mask_credentials=True)
            # Decrypt and re-mask to show last 4 chars
            try:
                decrypted = decrypt_credentials(t.credentials)
                data["credentials"] = _mask_credentials(decrypted)
            except Exception:
                data["credentials"] = {"error": "Could not decrypt"}
            result.append(data)
        return result
    finally:
        db.close()


@router.post("/cloud-targets", status_code=201)
async def create_cloud_target(req: CloudTargetCreateRequest):
    """Create a new cloud storage target with encrypted credentials."""
    db = get_session()
    try:
        existing = db.query(CloudStorageTarget).filter(CloudStorageTarget.name == req.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Target with name '{req.name}' already exists")

        encrypted = encrypt_credentials(req.credentials)
        target = CloudStorageTarget(
            name=req.name,
            provider_type=req.provider_type,
            credentials=encrypted,
            upload_path=req.upload_path,
            enabled=req.enabled,
        )
        db.add(target)
        db.commit()
        db.refresh(target)

        journal.log_entry(
            category="export",
            action_type="create",
            entity_name=target.name,
            description=f"Created cloud target '{target.name}' ({target.provider_type})",
            entity_id=target.id,
        )

        data = target.to_dict(mask_credentials=True)
        data["credentials"] = _mask_credentials(req.credentials)
        logger.info("[EXPORT] Created cloud target id=%s name=%s provider=%s", target.id, target.name, target.provider_type)
        return data
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to create cloud target: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.patch("/cloud-targets/{target_id}")
async def update_cloud_target(target_id: int, req: CloudTargetUpdateRequest):
    """Update a cloud storage target. Credentials are re-encrypted if provided."""
    db = get_session()
    try:
        target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Cloud target not found")

        if req.name is not None and req.name != target.name:
            existing = db.query(CloudStorageTarget).filter(
                CloudStorageTarget.name == req.name, CloudStorageTarget.id != target_id
            ).first()
            if existing:
                raise HTTPException(status_code=409, detail=f"Target with name '{req.name}' already exists")
            target.name = req.name

        if req.provider_type is not None:
            target.provider_type = req.provider_type
        if req.upload_path is not None:
            target.upload_path = req.upload_path
        if req.enabled is not None:
            target.enabled = req.enabled
        if req.credentials is not None:
            target.credentials = encrypt_credentials(req.credentials)

        db.commit()
        db.refresh(target)

        journal.log_entry(
            category="export",
            action_type="update",
            entity_name=target.name,
            description=f"Updated cloud target '{target.name}'",
            entity_id=target.id,
        )

        data = target.to_dict(mask_credentials=True)
        try:
            decrypted = decrypt_credentials(target.credentials)
            data["credentials"] = _mask_credentials(decrypted)
        except Exception:
            pass
        logger.info("[EXPORT] Updated cloud target id=%s", target_id)
        return data
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to update cloud target %s: %s", target_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/cloud-targets/{target_id}", status_code=204)
async def delete_cloud_target(target_id: int):
    """Delete a cloud storage target."""
    db = get_session()
    try:
        target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Cloud target not found")

        name = target.name
        db.delete(target)
        db.commit()

        journal.log_entry(
            category="export",
            action_type="delete",
            entity_name=name,
            description=f"Deleted cloud target '{name}'",
            entity_id=target_id,
        )
        logger.info("[EXPORT] Deleted cloud target id=%s name=%s", target_id, name)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to delete cloud target %s: %s", target_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cloud Storage Test Connection
# ---------------------------------------------------------------------------

@router.post("/cloud-targets/{target_id}/test")
async def test_cloud_target(target_id: int):
    """Test connection to a saved cloud storage target."""
    db = get_session()
    try:
        target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Cloud target not found")
        provider_type = target.provider_type
        creds = decrypt_credentials(target.credentials)
    finally:
        db.close()

    try:
        adapter = get_adapter(provider_type, creds)
        result = await adapter.test_connection()
        return {
            "success": result.success,
            "message": result.message,
            "provider_info": result.provider_info,
        }
    except ImportError as e:
        return {"success": False, "message": f"Missing dependency: {e}"}
    except Exception as e:
        logger.warning("[EXPORT] Cloud target test failed: %s", e)
        return {"success": False, "message": str(e)}


@router.post("/cloud-targets/test")
async def test_cloud_target_inline(req: CloudTargetTestRequest):
    """Test connection with inline credentials (before saving)."""
    try:
        adapter = get_adapter(req.provider_type, req.credentials)
        result = await adapter.test_connection()
        return {
            "success": result.success,
            "message": result.message,
            "provider_info": result.provider_info,
        }
    except ImportError as e:
        return {"success": False, "message": f"Missing dependency: {e}"}
    except Exception as e:
        logger.warning("[EXPORT] Inline cloud target test failed: %s", e)
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
# Publish Configuration Pydantic models
# ---------------------------------------------------------------------------


class PublishConfigCreateRequest(BaseModel):
    name: str
    profile_id: int
    target_id: Optional[int] = None
    schedule_type: Literal["manual", "cron", "event"] = "manual"
    cron_expression: Optional[str] = None
    event_triggers: list[str] = []
    enabled: bool = True
    webhook_url: Optional[str] = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v):
        if v is not None and v.strip():
            try:
                from croniter import croniter
                croniter(v)
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid cron expression: {e}")
        return v

    @field_validator("event_triggers")
    @classmethod
    def validate_triggers(cls, v):
        for t in v:
            if t not in VALID_EVENT_TRIGGERS:
                raise ValueError(f"Invalid event trigger '{t}'. Valid: {VALID_EVENT_TRIGGERS}")
        return v


class PublishConfigUpdateRequest(BaseModel):
    name: Optional[str] = None
    profile_id: Optional[int] = None
    target_id: Optional[int] = None
    schedule_type: Optional[Literal["manual", "cron", "event"]] = None
    cron_expression: Optional[str] = None
    event_triggers: Optional[list[str]] = None
    enabled: Optional[bool] = None
    webhook_url: Optional[str] = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, v):
        if v is not None and v.strip():
            try:
                from croniter import croniter
                croniter(v)
            except (ValueError, KeyError) as e:
                raise ValueError(f"Invalid cron expression: {e}")
        return v

    @field_validator("event_triggers")
    @classmethod
    def validate_triggers(cls, v):
        if v is not None:
            for t in v:
                if t not in VALID_EVENT_TRIGGERS:
                    raise ValueError(f"Invalid event trigger '{t}'. Valid: {VALID_EVENT_TRIGGERS}")
        return v


# ---------------------------------------------------------------------------
# Publish Configuration CRUD
# ---------------------------------------------------------------------------

@router.get("/publish-configs")
async def list_publish_configs():
    """List all publish configurations with profile/target names."""
    db = get_session()
    try:
        configs = db.query(PublishConfiguration).order_by(PublishConfiguration.name).all()
        result = []
        for c in configs:
            data = c.to_dict()
            # Resolve profile name
            profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == c.profile_id).first()
            data["profile_name"] = profile.name if profile else None
            # Resolve target name
            if c.target_id:
                target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == c.target_id).first()
                data["target_name"] = target.name if target else None
            else:
                data["target_name"] = None
            result.append(data)
        return result
    finally:
        db.close()


@router.post("/publish-configs", status_code=201)
async def create_publish_config(req: PublishConfigCreateRequest):
    """Create a new publish configuration."""
    db = get_session()
    try:
        # Validate profile exists
        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == req.profile_id).first()
        if not profile:
            raise HTTPException(status_code=400, detail=f"Profile {req.profile_id} not found")

        # Validate target exists if provided
        if req.target_id is not None:
            target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == req.target_id).first()
            if not target:
                raise HTTPException(status_code=400, detail=f"Cloud target {req.target_id} not found")

        config = PublishConfiguration(
            name=req.name,
            profile_id=req.profile_id,
            target_id=req.target_id,
            schedule_type=req.schedule_type,
            cron_expression=req.cron_expression,
            event_triggers=json.dumps(req.event_triggers),
            enabled=req.enabled,
            webhook_url=req.webhook_url,
        )
        db.add(config)
        db.commit()
        db.refresh(config)

        journal.log_entry(
            category="export",
            action_type="create",
            entity_name=config.name,
            description=f"Created publish config '{config.name}'",
            entity_id=config.id,
        )

        logger.info("[EXPORT] Created publish config id=%s name=%s", config.id, config.name)
        return config.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to create publish config: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.patch("/publish-configs/{config_id}")
async def update_publish_config(config_id: int, req: PublishConfigUpdateRequest):
    """Update a publish configuration."""
    db = get_session()
    try:
        config = db.query(PublishConfiguration).filter(PublishConfiguration.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="Publish config not found")

        if req.profile_id is not None:
            profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == req.profile_id).first()
            if not profile:
                raise HTTPException(status_code=400, detail=f"Profile {req.profile_id} not found")
            config.profile_id = req.profile_id

        if req.target_id is not None:
            target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == req.target_id).first()
            if not target:
                raise HTTPException(status_code=400, detail=f"Cloud target {req.target_id} not found")
            config.target_id = req.target_id

        if req.name is not None:
            config.name = req.name
        if req.schedule_type is not None:
            config.schedule_type = req.schedule_type
        if req.cron_expression is not None:
            config.cron_expression = req.cron_expression
        if req.event_triggers is not None:
            config.event_triggers = json.dumps(req.event_triggers)
        if req.enabled is not None:
            config.enabled = req.enabled
        if req.webhook_url is not None:
            config.webhook_url = req.webhook_url

        db.commit()
        db.refresh(config)

        journal.log_entry(
            category="export",
            action_type="update",
            entity_name=config.name,
            description=f"Updated publish config '{config.name}'",
            entity_id=config.id,
        )

        logger.info("[EXPORT] Updated publish config id=%s", config_id)
        return config.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to update publish config %s: %s", config_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/publish-configs/{config_id}", status_code=204)
async def delete_publish_config(config_id: int):
    """Delete a publish configuration."""
    db = get_session()
    try:
        config = db.query(PublishConfiguration).filter(PublishConfiguration.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="Publish config not found")

        name = config.name
        db.delete(config)
        db.commit()

        journal.log_entry(
            category="export",
            action_type="delete",
            entity_name=name,
            description=f"Deleted publish config '{name}'",
            entity_id=config_id,
        )
        logger.info("[EXPORT] Deleted publish config id=%s name=%s", config_id, name)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[EXPORT] Failed to delete publish config %s: %s", config_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Publish / Dry-run
# ---------------------------------------------------------------------------

@router.post("/publish-configs/{config_id}/publish")
async def publish_now(config_id: int):
    """Immediately execute the publish pipeline for a config."""
    db = get_session()
    try:
        config = db.query(PublishConfiguration).filter(PublishConfiguration.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="Publish config not found")
    finally:
        db.close()

    result = await execute_publish(config_id)
    return {
        "success": result.success,
        "channels_count": result.channels_count,
        "m3u_size": result.m3u_size,
        "xmltv_size": result.xmltv_size,
        "upload_result": result.upload_result,
        "duration_ms": result.duration_ms,
        "error": result.error or None,
    }


@router.post("/publish-configs/{config_id}/dry-run")
async def publish_dry_run(config_id: int):
    """Simulate publish without generating files or uploading."""
    db = get_session()
    try:
        config = db.query(PublishConfiguration).filter(PublishConfiguration.id == config_id).first()
        if not config:
            raise HTTPException(status_code=404, detail="Publish config not found")
    finally:
        db.close()

    result = await execute_publish(config_id, dry_run=True)
    return {
        "success": result.success,
        "channels_count": result.channels_count,
        "duration_ms": result.duration_ms,
        "error": result.error or None,
    }


# ---------------------------------------------------------------------------
# Publish History
# ---------------------------------------------------------------------------

@router.get("/publish-history")
async def list_publish_history(
    config_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
):
    """List publish history entries with pagination."""
    db = get_session()
    try:
        query = db.query(PublishHistory)
        if config_id is not None:
            query = query.filter(PublishHistory.config_id == config_id)
        if status is not None:
            query = query.filter(PublishHistory.status == status)

        total = query.count()
        entries = (
            query.order_by(PublishHistory.started_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        # Resolve config/profile/target names
        result = []
        for e in entries:
            data = e.to_dict()
            config = db.query(PublishConfiguration).filter(PublishConfiguration.id == e.config_id).first()
            if config:
                data["config_name"] = config.name
                profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == config.profile_id).first()
                data["profile_name"] = profile.name if profile else None
                if config.target_id:
                    target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == config.target_id).first()
                    data["target_name"] = target.name if target else None
                else:
                    data["target_name"] = None
            else:
                data["config_name"] = None
                data["profile_name"] = None
                data["target_name"] = None
            result.append(data)

        return {"total": total, "page": page, "per_page": per_page, "entries": result}
    finally:
        db.close()


@router.delete("/publish-history/{history_id}", status_code=204)
async def delete_publish_history_entry(history_id: int):
    """Delete a single publish history entry."""
    db = get_session()
    try:
        entry = db.query(PublishHistory).filter(PublishHistory.id == history_id).first()
        if not entry:
            raise HTTPException(status_code=404, detail="History entry not found")
        db.delete(entry)
        db.commit()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/publish-history")
async def delete_publish_history_bulk(older_than_days: int = 30):
    """Delete publish history entries older than N days."""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    db = get_session()
    try:
        deleted = db.query(PublishHistory).filter(PublishHistory.started_at < cutoff).delete()
        db.commit()
        return {"deleted": deleted}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Print Guide
# ---------------------------------------------------------------------------

_GROUP_COLORS = [
    {"header": "#4A90E2", "bg": "#E8F2FC"},
    {"header": "#50C878", "bg": "#E8F8F0"},
    {"header": "#9B59B6", "bg": "#F4ECF7"},
    {"header": "#E67E22", "bg": "#FDF2E9"},
    {"header": "#16A085", "bg": "#E8F6F3"},
    {"header": "#C0392B", "bg": "#FADBD8"},
    {"header": "#F39C12", "bg": "#FEF5E7"},
    {"header": "#2C3E50", "bg": "#EAF2F8"},
    {"header": "#D35400", "bg": "#FBEEE6"},
    {"header": "#8E44AD", "bg": "#F5EEF8"},
]


def _escape_html(text: str) -> str:
    """Escape HTML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


_CHANNEL_NUM_PREFIX_RE = re.compile(r"^\d+(\.\d+)?\s*[-|:]\s*")


def _clean_channel_name(name: str, channel_number) -> str:
    """Remove channel number prefix from display name."""
    if not name:
        return "Unknown Channel"
    cleaned = _CHANNEL_NUM_PREFIX_RE.sub("", name)
    if channel_number is not None:
        try:
            num_val = float(channel_number)
            num_str = str(int(num_val)) if num_val == int(num_val) else str(num_val)
            # escaped_num is re.escape() of a numeric string — can't contain
            # regex metacharacters by construction; pattern is fixed-length.
            escaped_num = re.escape(num_str)
            cleaned = re.sub(rf"^{escaped_num}\s*[-|:]?\s*", "", cleaned, flags=re.IGNORECASE)  # nosemgrep: no-bare-re-on-dynamic-pattern
        except (ValueError, TypeError):
            pass
    return cleaned.strip() or name


def _fmt_channel_number(n) -> str:
    """Format a channel number for display."""
    if n is None:
        return "N/A"
    try:
        num = float(n)
        return str(int(num)) if num == int(num) else str(num)
    except (ValueError, TypeError):
        return str(n)


class PrintGuideGroupSetting(BaseModel):
    group_id: int
    selected: bool = True
    mode: Literal["detailed", "summary"] = "detailed"


class PrintGuideRequest(BaseModel):
    title: str = "Channel Guide"
    groups: list[PrintGuideGroupSetting] = []


@router.post("/print-guide")
async def generate_print_guide(request: PrintGuideRequest):
    """Generate a printable HTML channel guide.

    Returns complete HTML document with CSS pagination and print styling.
    Groups are colored from a rotating 10-color palette.
    Each group renders in detailed (all channels) or summary (range) mode.
    """
    start = time.time()
    logger.info("[EXPORT] Generating print guide: title=%s, groups=%d",
                request.title, len(request.groups))

    client = get_client()
    try:
        channel_groups = await client.get_channel_groups()
        channels_result = await client.get_channels(page=1, page_size=10000)
        channels = channels_result.get("results", [])

        # Build settings map
        selected_ids: set[int] = set()
        settings_map: dict[int, PrintGuideGroupSetting] = {}
        if request.groups:
            for gs in request.groups:
                if gs.selected:
                    selected_ids.add(gs.group_id)
                settings_map[gs.group_id] = gs
        else:
            selected_ids = {g["id"] for g in channel_groups}

        # Sort groups by lowest channel number
        group_order: dict[int, float] = {}
        for ch in channels:
            gid = ch.get("channel_group_id") or ch.get("channel_group")
            num = ch.get("channel_number")
            if gid and num is not None:
                if gid not in group_order or num < group_order[gid]:
                    group_order[gid] = num
        sorted_groups = sorted(
            channel_groups,
            key=lambda g: group_order.get(g["id"], 999999),
        )

        # Build groups HTML
        groups_html_parts: list[str] = []
        color_index = 0
        total_channels = 0

        for group in sorted_groups:
            gid = group["id"]
            if selected_ids and gid not in selected_ids:
                continue

            gs = settings_map.get(gid)
            mode = gs.mode if gs else "detailed"
            color = _GROUP_COLORS[color_index % len(_GROUP_COLORS)]
            color_index += 1

            group_channels = sorted(
                [
                    ch for ch in channels
                    if (ch.get("channel_group_id") or ch.get("channel_group")) == gid
                    and ch.get("channel_number") is not None
                ],
                key=lambda ch: ch.get("channel_number", 0),
            )
            if not group_channels:
                continue

            total_channels += len(group_channels)
            gname = _escape_html(group.get("name", "Unknown"))

            if mode == "summary":
                first_num = group_channels[0].get("channel_number")
                last_num = group_channels[-1].get("channel_number")
                range_str = (
                    _fmt_channel_number(first_num) if first_num == last_num
                    else f"{_fmt_channel_number(first_num)} - {_fmt_channel_number(last_num)}"
                )
                groups_html_parts.append(
                    f'<div class="channel-group summary-mode" style="background:{color["bg"]}">'
                    f'<div class="group-title" style="background:{color["header"]};color:#fff">{gname}</div>'
                    f'<div class="channel-list"><div class="channel-line">'
                    f'<span class="ch-num">{range_str}</span> ({len(group_channels)} channels)'
                    f'</div></div></div>'
                )
            else:
                lines: list[str] = []
                for ch in group_channels:
                    num_str = _fmt_channel_number(ch.get("channel_number"))
                    dname = _escape_html(_clean_channel_name(ch.get("name", ""), ch.get("channel_number")))
                    lines.append(f'<div class="channel-line"><span class="ch-num">{num_str}</span> {dname}</div>')
                groups_html_parts.append(
                    f'<div class="channel-group" style="background:{color["bg"]}">'
                    f'<div class="group-title" style="background:{color["header"]};color:#fff">{gname}</div>'
                    f'<div class="channel-list">{"".join(lines)}</div></div>'
                )

        groups_html = "\n".join(groups_html_parts)
        title_esc = _escape_html(request.title)

        html = _build_print_guide_html(title_esc, total_channels, groups_html)

        elapsed = (time.time() - start) * 1000
        logger.info("[EXPORT] Print guide generated: %d channels, %d groups in %.1fms",
                    total_channels, color_index, elapsed)

        return Response(content=html, media_type="text/html")

    except Exception as e:
        logger.exception("[EXPORT] Failed to generate print guide: %s", e)
        raise HTTPException(status_code=500, detail="Failed to generate print guide")


def _build_print_guide_html(title_esc: str, total_channels: int, groups_html: str) -> str:
    """Assemble the full print guide HTML document."""
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title_esc}</title>
<style>
@page{{size:11in 8.5in;margin:0.3in 0.4in}}
*{{margin:0;padding:0;box-sizing:border-box;-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}}
html,body{{height:100%}}
body{{font-family:Arial,sans-serif;background:#e0e0e0;padding:20px}}
.page{{width:10.2in;height:7.9in;margin:0 auto 20px auto;padding:0.3in 0.4in;background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.2);font-size:6pt;line-height:1.15;color:#000;overflow:hidden;position:relative}}
.header{{column-span:all;text-align:center;border-bottom:1.5px solid #000;padding-bottom:3px;margin-bottom:6px}}
.header h1{{font-size:14pt;font-weight:bold;margin:0 0 2px 0;letter-spacing:.5px}}
.header .subtitle{{font-size:7pt;margin:0;color:#333}}
.channel-group{{break-inside:auto;page-break-inside:auto;border:1px solid #999;border-radius:2px;padding:3px 4px;margin-bottom:4px}}
.group-title{{font-size:7pt;font-weight:bold;padding:2px 4px;margin:-3px -4px 2px -4px;border-radius:1px 1px 0 0}}
.channel-line{{margin:0;padding:.5px 0;line-height:1.2;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.ch-num{{font-weight:bold;display:inline-block;min-width:28px;color:#000}}
.summary-mode .channel-line{{font-style:italic}}
.content{{column-count:5;column-gap:10px;column-fill:auto;height:calc(100% - 45px);overflow:hidden}}
.print-hint{{text-align:center;padding:10px;background:#fff3cd;border:1px solid #ffc107;border-radius:4px;margin-bottom:20px;font-size:10pt}}
.page-footer{{position:absolute;bottom:0;left:0;right:0;height:15px;text-align:right;padding-right:.1in;font-size:7pt;color:#666}}
@media print{{body{{background:#fff;padding:0}}.page{{width:auto;height:7.9in;margin:0;padding:0.3in 0.4in;box-shadow:none;page-break-after:always;position:relative;overflow:hidden}}.page:last-child{{page-break-after:auto}}.content{{height:calc(100% - 45px)}}.print-hint{{display:none}}}}
</style>
</head>
<body>
<div class="print-hint">Print dialog will open automatically. If it doesn't, press Ctrl+P (Cmd+P on Mac).</div>
<div id="pages-container">
<div class="page" id="page-1">
<div class="header"><h1>{title_esc}</h1><div class="subtitle">{total_channels} channels</div></div>
<div class="content">{groups_html}</div>
</div>
</div>
<script>
window.addEventListener("load",function(){{setTimeout(function(){{handlePagination();setTimeout(function(){{window.print()}},300)}},200)}});
function handlePagination(){{var c=document.getElementById("pages-container"),p=document.getElementById("page-1"),t=p.querySelector(".content"),g=Array.from(t.querySelectorAll(".channel-group"));if(!g.length){{addPN(p,1);return}}var r=t.getBoundingClientRect(),m=r.left+r.width;paginate(c,p,g,m)}}
function paginate(c,p,g,m){{var t=p.querySelector(".content");t.style.overflow="visible";var sg=null,si=-1,oi=-1;for(var i=0;i<g.length;i++){{var r=g[i].getBoundingClientRect();if(r.left>=m){{oi=i;break}}else if(r.right>m+2){{sg=g[i];si=i}}}}t.style.overflow="hidden";if(oi===-1&&!sg){{addPN(p,getPN(p));updPN(c);return}}var n=c.querySelectorAll(".page").length+1,np=document.createElement("div");np.className="page";np.id="page-"+n;np.innerHTML='<div class="header" style="border-bottom:1px solid #999"><h1 style="font-size:10pt">{title_esc} (continued)</h1></div><div class="content"></div>';c.appendChild(np);var nc=np.querySelector(".content");if(sg){{var cl=sg.cloneNode(true);var te=cl.querySelector(".group-title");if(te)te.textContent=te.textContent+" (continued)";nc.appendChild(cl)}}if(oi!==-1){{g.slice(oi).forEach(function(x){{nc.appendChild(x)}})}}addPN(p,getPN(p));setTimeout(function(){{paginate(c,np,Array.from(nc.querySelectorAll(".channel-group")),m)}},50)}}
function getPN(p){{var m=p.id.match(/page-(\\d+)/);return m?parseInt(m[1],10):1}}
function updPN(c){{var a=c.querySelectorAll(".page"),t=a.length;a.forEach(function(p,i){{var f=p.querySelector(".page-footer");if(f)f.textContent="Page "+(i+1)+" of "+t}})}}
function addPN(p,n){{if(p.querySelector(".page-footer"))return;var f=document.createElement("div");f.className="page-footer";f.textContent="Page "+n;p.appendChild(f)}}
</script>
</body>
</html>'''
