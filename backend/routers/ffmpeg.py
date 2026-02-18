"""
FFMPEG Builder router — capabilities, probe, validate, generate-command,
saved configs CRUD, jobs CRUD, queue config, and profiles CRUD.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import json as json_module
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from database import get_session
from ffmpeg_builder.probe import probe_source, detect_capabilities as ffmpeg_detect_capabilities
from ffmpeg_builder.validation import validate_config as ffmpeg_validate_config
from ffmpeg_builder.command_generator import generate_command as ffmpeg_generate_command

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ffmpeg", tags=["FFMPEG Builder"])


# =============================================================================
# Models
# =============================================================================

class FFMPEGProbeRequest(BaseModel):
    path: str
    timeout: Optional[int] = 30


# =============================================================================
# FFMPEG Builder API
# =============================================================================

@router.get("/capabilities")
async def get_ffmpeg_capabilities():
    """Detect system ffmpeg capabilities (codecs, formats, filters, hwaccel)."""
    logger.debug("[FFMPEG] GET /api/ffmpeg/capabilities")
    caps = ffmpeg_detect_capabilities()
    return caps


@router.post("/probe")
async def probe_ffmpeg_source(request: FFMPEGProbeRequest):
    """Probe a media source using ffprobe."""
    logger.debug("[FFMPEG] POST /api/ffmpeg/probe - path=%s", request.path)
    result = probe_source(request.path, timeout=request.timeout)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error)
    return {
        "success": True,
        "streams": result.streams,
        "format_name": result.format_name,
        "duration": result.duration,
        "bit_rate": result.bit_rate,
        "size": result.size,
    }


@router.post("/validate")
async def validate_ffmpeg_config_endpoint(request: Request):
    """Validate an FFMPEG builder configuration."""
    logger.debug("[FFMPEG] POST /api/ffmpeg/validate")
    body = await request.json()
    result = ffmpeg_validate_config(body)
    # Handle both dict (mock in tests) and ValidationResult (real) returns
    if isinstance(result, dict):
        return result
    return {
        "valid": result.valid,
        "errors": result.errors,
        "warnings": result.warnings,
        "command": getattr(result, "command", ""),
    }


@router.post("/generate-command")
async def generate_ffmpeg_command_endpoint(request: Request):
    """Generate an annotated ffmpeg command from configuration."""
    logger.debug("[FFMPEG] POST /api/ffmpeg/generate-command")
    body = await request.json()
    result = ffmpeg_generate_command(body)
    # Handle both dict (mock in tests) and list (real) returns
    if isinstance(result, dict):
        return result
    # Real return is a command list — annotate it
    from ffmpeg_builder.command_generator import annotate_command
    annotated = annotate_command(body)
    return {
        "command": " ".join(annotated.command),
        "annotations": [
            {
                "flag": a.flag,
                "explanation": a.explanation,
                "category": a.category,
            }
            for a in annotated.annotations
        ],
    }


# --- Saved Configs CRUD (stubs — will be backed by DB in Epic 8) ---

def ffmpeg_list_configs():
    return []

def ffmpeg_create_config(data):
    return data

def ffmpeg_get_config(config_id):
    return None

def ffmpeg_update_config(config_id, data):
    return data

def ffmpeg_delete_config(config_id):
    return {"status": "deleted"}


@router.get("/configs")
async def list_ffmpeg_configs():
    logger.debug("[FFMPEG] GET /api/ffmpeg/configs")
    configs = ffmpeg_list_configs()
    return {"configs": configs}


@router.post("/configs", status_code=201)
async def create_ffmpeg_config(request: Request):
    logger.debug("[FFMPEG] POST /api/ffmpeg/configs")
    body = await request.json()
    result = ffmpeg_create_config(body)
    logger.info("[FFMPEG] Created config")
    return result


@router.get("/configs/{config_id}")
async def get_ffmpeg_config(config_id: int):
    logger.debug("[FFMPEG] GET /api/ffmpeg/configs/%s", config_id)
    result = ffmpeg_get_config(config_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Config not found")
    return result


@router.put("/configs/{config_id}")
async def update_ffmpeg_config(config_id: int, request: Request):
    logger.debug("[FFMPEG] PUT /api/ffmpeg/configs/%s", config_id)
    body = await request.json()
    result = ffmpeg_update_config(config_id, body)
    logger.info("[FFMPEG] Updated config id=%s", config_id)
    return result


@router.delete("/configs/{config_id}")
async def delete_ffmpeg_config(config_id: int):
    logger.debug("[FFMPEG] DELETE /api/ffmpeg/configs/%s", config_id)
    result = ffmpeg_delete_config(config_id)
    logger.info("[FFMPEG] Deleted config id=%s", config_id)
    return result


# --- Jobs CRUD (stubs — will be backed by job queue in Epic 6) ---

def ffmpeg_list_jobs():
    return []

def ffmpeg_create_job(data):
    return data

def ffmpeg_get_job(job_id):
    return None

def ffmpeg_cancel_job(job_id):
    return {"status": "cancelled"}

def ffmpeg_delete_job(job_id):
    return {"status": "deleted"}


@router.get("/jobs")
async def list_ffmpeg_jobs():
    logger.debug("[FFMPEG] GET /api/ffmpeg/jobs")
    jobs = ffmpeg_list_jobs()
    return {"jobs": jobs}


@router.post("/jobs", status_code=201)
async def create_ffmpeg_job_endpoint(request: Request):
    logger.debug("[FFMPEG] POST /api/ffmpeg/jobs")
    body = await request.json()
    result = ffmpeg_create_job(body)
    logger.info("[FFMPEG] Created job")
    return result


@router.get("/jobs/{job_id}")
async def get_ffmpeg_job(job_id: str):
    logger.debug("[FFMPEG] GET /api/ffmpeg/jobs/%s", job_id)
    result = ffmpeg_get_job(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return result


@router.post("/jobs/{job_id}/cancel")
async def cancel_ffmpeg_job(job_id: str):
    logger.debug("[FFMPEG] POST /api/ffmpeg/jobs/%s/cancel", job_id)
    try:
        result = ffmpeg_cancel_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.delete("/jobs/{job_id}")
async def delete_ffmpeg_job(job_id: str):
    logger.debug("[FFMPEG] DELETE /api/ffmpeg/jobs/%s", job_id)
    result = ffmpeg_delete_job(job_id)
    logger.info("[FFMPEG] Deleted job id=%s", job_id)
    return result


# --- Queue Config (stubs — will be backed by config in Epic 6) ---

def ffmpeg_get_queue_config():
    return {"max_concurrent": 2, "default_priority": "normal", "auto_start": True}

def ffmpeg_update_queue_config(data):
    return data


@router.get("/queue-config")
async def get_ffmpeg_queue_config():
    logger.debug("[FFMPEG] GET /api/ffmpeg/queue-config")
    return ffmpeg_get_queue_config()


@router.put("/queue-config")
async def update_ffmpeg_queue_config(request: Request):
    logger.debug("[FFMPEG] PUT /api/ffmpeg/queue-config")
    body = await request.json()
    result = ffmpeg_update_queue_config(body)
    logger.info("[FFMPEG] Updated queue config")
    return result


# ---------------------------------------------------------------------------
# FFMPEG Profiles — save/load user-created FFMPEG Builder profiles
# ---------------------------------------------------------------------------

@router.get("/profiles", tags=["FFMPEG Profiles"])
async def list_ffmpeg_profiles():
    """List all saved FFMPEG profiles."""
    logger.debug("[FFMPEG] GET /api/ffmpeg/profiles")
    try:
        with get_session() as db:
            from models import FFmpegProfile
            profiles = db.query(FFmpegProfile).order_by(FFmpegProfile.created_at.desc()).all()
            return {"profiles": [p.to_dict() for p in profiles]}
    except Exception as e:
        logger.exception("[FFMPEG] Failed to list profiles")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/profiles", tags=["FFMPEG Profiles"])
async def create_ffmpeg_profile(request: Request):
    """Save a new FFMPEG profile."""
    logger.debug("[FFMPEG] POST /api/ffmpeg/profiles")
    try:
        body = await request.json()
        name = body.get("name", "").strip()
        config = body.get("config")
        if not name:
            raise HTTPException(status_code=400, detail="Profile name is required")
        if not config:
            raise HTTPException(status_code=400, detail="Profile config is required")

        with get_session() as db:
            from models import FFmpegProfile
            profile = FFmpegProfile(
                name=name,
                config=json_module.dumps(config),
            )
            db.add(profile)
            db.commit()
            logger.info("[FFMPEG] Created profile id=%s name=%s", profile.id, name)
            return profile.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[FFMPEG] Failed to save profile")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/profiles/{profile_id}", tags=["FFMPEG Profiles"])
async def delete_ffmpeg_profile(profile_id: int):
    """Delete a saved FFMPEG profile."""
    logger.debug("[FFMPEG] DELETE /api/ffmpeg/profiles/%s", profile_id)
    try:
        with get_session() as db:
            from models import FFmpegProfile
            profile = db.query(FFmpegProfile).filter(FFmpegProfile.id == profile_id).first()
            if not profile:
                raise HTTPException(status_code=404, detail="Profile not found")
            name = profile.name
            db.delete(profile)
            db.commit()
            logger.info("[FFMPEG] Deleted profile id=%s name=%s", profile_id, name)
            return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[FFMPEG] Failed to delete profile %s", profile_id)
        raise HTTPException(status_code=500, detail=str(e))
