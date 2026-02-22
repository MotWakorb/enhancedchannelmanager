"""
Dummy EPG router — profile CRUD, channel assignments, preview, and XMLTV output.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from cache import get_cache
from database import get_session
from dispatcharr_client import get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dummy-epg", tags=["Dummy EPG"])

cache = get_cache()


# =============================================================================
# Pydantic models
# =============================================================================


class SubstitutionPairModel(BaseModel):
    find: str
    replace: str
    is_regex: bool = False
    enabled: bool = True


class PatternVariantModel(BaseModel):
    name: str = "Default"
    title_pattern: Optional[str] = None
    time_pattern: Optional[str] = None
    date_pattern: Optional[str] = None
    title_template: Optional[str] = None
    description_template: Optional[str] = None
    channel_logo_url_template: Optional[str] = None
    program_poster_url_template: Optional[str] = None
    pattern_builder_examples: Optional[str] = None
    upcoming_title_template: Optional[str] = None
    upcoming_description_template: Optional[str] = None
    ended_title_template: Optional[str] = None
    ended_description_template: Optional[str] = None
    fallback_title_template: Optional[str] = None
    fallback_description_template: Optional[str] = None


class ProfileCreateRequest(BaseModel):
    name: str
    enabled: bool = True
    name_source: str = "channel"
    stream_index: int = 1
    title_pattern: Optional[str] = None
    time_pattern: Optional[str] = None
    date_pattern: Optional[str] = None
    substitution_pairs: list[SubstitutionPairModel] = []
    title_template: Optional[str] = None
    description_template: Optional[str] = None
    upcoming_title_template: Optional[str] = None
    upcoming_description_template: Optional[str] = None
    ended_title_template: Optional[str] = None
    ended_description_template: Optional[str] = None
    fallback_title_template: Optional[str] = None
    fallback_description_template: Optional[str] = None
    event_timezone: str = "US/Eastern"
    output_timezone: Optional[str] = None
    program_duration: int = 180
    categories: Optional[str] = None
    channel_logo_url_template: Optional[str] = None
    program_poster_url_template: Optional[str] = None
    tvg_id_template: str = "ecm-{channel_number}"
    include_date_tag: bool = False
    include_live_tag: bool = False
    include_new_tag: bool = False
    pattern_builder_examples: Optional[str] = None
    pattern_variants: Optional[list[PatternVariantModel]] = None
    channel_group_ids: Optional[list[int]] = None


class ProfileUpdateRequest(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    name_source: Optional[str] = None
    stream_index: Optional[int] = None
    title_pattern: Optional[str] = None
    time_pattern: Optional[str] = None
    date_pattern: Optional[str] = None
    substitution_pairs: Optional[list[SubstitutionPairModel]] = None
    title_template: Optional[str] = None
    description_template: Optional[str] = None
    upcoming_title_template: Optional[str] = None
    upcoming_description_template: Optional[str] = None
    ended_title_template: Optional[str] = None
    ended_description_template: Optional[str] = None
    fallback_title_template: Optional[str] = None
    fallback_description_template: Optional[str] = None
    event_timezone: Optional[str] = None
    output_timezone: Optional[str] = None
    program_duration: Optional[int] = None
    categories: Optional[str] = None
    channel_logo_url_template: Optional[str] = None
    program_poster_url_template: Optional[str] = None
    tvg_id_template: Optional[str] = None
    include_date_tag: Optional[bool] = None
    include_live_tag: Optional[bool] = None
    include_new_tag: Optional[bool] = None
    pattern_builder_examples: Optional[str] = None
    pattern_variants: Optional[list[PatternVariantModel]] = None
    channel_group_ids: Optional[list[int]] = None


class PreviewRequest(BaseModel):
    sample_name: str
    substitution_pairs: list[SubstitutionPairModel] = []
    title_pattern: Optional[str] = None
    time_pattern: Optional[str] = None
    date_pattern: Optional[str] = None
    title_template: Optional[str] = None
    description_template: Optional[str] = None
    upcoming_title_template: Optional[str] = None
    upcoming_description_template: Optional[str] = None
    ended_title_template: Optional[str] = None
    ended_description_template: Optional[str] = None
    fallback_title_template: Optional[str] = None
    fallback_description_template: Optional[str] = None
    event_timezone: str = "US/Eastern"
    output_timezone: Optional[str] = None
    program_duration: int = 180
    channel_logo_url_template: Optional[str] = None
    program_poster_url_template: Optional[str] = None
    pattern_variants: Optional[list[PatternVariantModel]] = None


class BatchPreviewRequest(BaseModel):
    sample_names: list[str]
    substitution_pairs: list[SubstitutionPairModel] = []
    title_pattern: Optional[str] = None
    time_pattern: Optional[str] = None
    date_pattern: Optional[str] = None
    title_template: Optional[str] = None
    description_template: Optional[str] = None
    upcoming_title_template: Optional[str] = None
    upcoming_description_template: Optional[str] = None
    ended_title_template: Optional[str] = None
    ended_description_template: Optional[str] = None
    fallback_title_template: Optional[str] = None
    fallback_description_template: Optional[str] = None
    event_timezone: str = "US/Eastern"
    output_timezone: Optional[str] = None
    program_duration: int = 180
    channel_logo_url_template: Optional[str] = None
    program_poster_url_template: Optional[str] = None
    pattern_variants: Optional[list[PatternVariantModel]] = None


# =============================================================================
# Profile CRUD
# =============================================================================


@router.get("/profiles")
async def list_profiles(db: Session = Depends(get_session)):
    """List all profiles with assignment count."""
    logger.debug("[DUMMY-EPG] GET /profiles")
    try:
        from models import DummyEPGProfile
        profiles = db.query(DummyEPGProfile).all()
        result = []
        for profile in profiles:
            d = profile.to_dict()
            d["group_count"] = len(profile.get_channel_group_ids())
            result.append(d)
        return result
    except Exception as e:
        logger.warning("[DUMMY-EPG] Failed to list profiles: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/profiles")
async def create_profile(req: ProfileCreateRequest, db: Session = Depends(get_session)):
    """Create a new Dummy EPG profile."""
    logger.debug("[DUMMY-EPG] POST /profiles name=%s", req.name)
    try:
        from models import DummyEPGProfile
        # Check for duplicate name
        existing = db.query(DummyEPGProfile).filter(
            DummyEPGProfile.name == req.name
        ).first()
        if existing:
            logger.warning("[DUMMY-EPG] Profile name already exists: %s", req.name)
            raise HTTPException(status_code=409, detail=f"Profile with name '{req.name}' already exists")

        profile = DummyEPGProfile(
            name=req.name,
            enabled=req.enabled,
            name_source=req.name_source,
            stream_index=req.stream_index,
            title_pattern=req.title_pattern,
            time_pattern=req.time_pattern,
            date_pattern=req.date_pattern,
            title_template=req.title_template,
            description_template=req.description_template,
            upcoming_title_template=req.upcoming_title_template,
            upcoming_description_template=req.upcoming_description_template,
            ended_title_template=req.ended_title_template,
            ended_description_template=req.ended_description_template,
            fallback_title_template=req.fallback_title_template,
            fallback_description_template=req.fallback_description_template,
            event_timezone=req.event_timezone,
            output_timezone=req.output_timezone,
            program_duration=req.program_duration,
            categories=req.categories,
            channel_logo_url_template=req.channel_logo_url_template,
            program_poster_url_template=req.program_poster_url_template,
            tvg_id_template=req.tvg_id_template,
            include_date_tag=req.include_date_tag,
            include_live_tag=req.include_live_tag,
            include_new_tag=req.include_new_tag,
            pattern_builder_examples=req.pattern_builder_examples,
        )
        if req.substitution_pairs:
            profile.set_substitution_pairs([p.model_dump() for p in req.substitution_pairs])
        if req.pattern_variants:
            profile.set_pattern_variants([v.model_dump() for v in req.pattern_variants])
        if req.channel_group_ids is not None:
            profile.set_channel_group_ids(req.channel_group_ids)

        db.add(profile)
        db.commit()
        db.refresh(profile)

        cache.invalidate_prefix("dummy_epg_xmltv")
        logger.info("[DUMMY-EPG] Created profile id=%s name=%s", profile.id, profile.name)
        return profile.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[DUMMY-EPG] Failed to create profile: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/profiles/{profile_id}")
async def get_profile(profile_id: int, db: Session = Depends(get_session)):
    """Get a single profile with its assignments."""
    logger.debug("[DUMMY-EPG] GET /profiles/%s", profile_id)
    try:
        from models import DummyEPGProfile
        profile = db.query(DummyEPGProfile).filter(
            DummyEPGProfile.id == profile_id
        ).first()
        if not profile:
            logger.warning("[DUMMY-EPG] Profile not found: %s", profile_id)
            raise HTTPException(status_code=404, detail="Profile not found")
        return profile.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[DUMMY-EPG] Failed to get profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.patch("/profiles/{profile_id}")
async def update_profile(profile_id: int, req: ProfileUpdateRequest, db: Session = Depends(get_session)):
    """Update a profile (partial)."""
    logger.debug("[DUMMY-EPG] PATCH /profiles/%s", profile_id)
    try:
        from models import DummyEPGProfile
        profile = db.query(DummyEPGProfile).filter(
            DummyEPGProfile.id == profile_id
        ).first()
        if not profile:
            logger.warning("[DUMMY-EPG] Profile not found: %s", profile_id)
            raise HTTPException(status_code=404, detail="Profile not found")

        # Check for name conflict if name is being changed
        if req.name is not None and req.name != profile.name:
            existing = db.query(DummyEPGProfile).filter(
                DummyEPGProfile.name == req.name,
                DummyEPGProfile.id != profile_id,
            ).first()
            if existing:
                logger.warning("[DUMMY-EPG] Profile name already exists: %s", req.name)
                raise HTTPException(status_code=409, detail=f"Profile with name '{req.name}' already exists")

        update_data = req.model_dump(exclude_unset=True)
        sub_pairs = update_data.pop("substitution_pairs", None)
        pattern_variants = update_data.pop("pattern_variants", None)
        channel_group_ids = update_data.pop("channel_group_ids", None)

        for field, value in update_data.items():
            setattr(profile, field, value)

        if sub_pairs is not None:
            profile.set_substitution_pairs([p.model_dump() if hasattr(p, "model_dump") else p for p in sub_pairs])
        if pattern_variants is not None:
            profile.set_pattern_variants([v.model_dump() if hasattr(v, "model_dump") else v for v in pattern_variants])
        if channel_group_ids is not None:
            profile.set_channel_group_ids(channel_group_ids)

        db.commit()
        db.refresh(profile)

        cache.invalidate_prefix("dummy_epg_xmltv")
        logger.info("[DUMMY-EPG] Updated profile id=%s", profile_id)
        return profile.to_dict()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[DUMMY-EPG] Failed to update profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/profiles/{profile_id}", status_code=204)
async def delete_profile(profile_id: int, db: Session = Depends(get_session)):
    """Delete a profile and its assignments (cascade)."""
    logger.debug("[DUMMY-EPG] DELETE /profiles/%s", profile_id)
    try:
        from models import DummyEPGProfile
        profile = db.query(DummyEPGProfile).filter(
            DummyEPGProfile.id == profile_id
        ).first()
        if not profile:
            logger.warning("[DUMMY-EPG] Profile not found: %s", profile_id)
            raise HTTPException(status_code=404, detail="Profile not found")

        db.delete(profile)
        db.commit()

        cache.invalidate_prefix("dummy_epg_xmltv")
        logger.info("[DUMMY-EPG] Deleted profile id=%s", profile_id)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[DUMMY-EPG] Failed to delete profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# =============================================================================
# Preview & Output
# =============================================================================


@router.post("/preview")
async def preview_epg(req: PreviewRequest):
    """Test the EPG pipeline with sample data (no DB)."""
    logger.debug("[DUMMY-EPG] POST /preview sample_name=%s", req.sample_name)
    try:
        from dummy_epg_engine import preview_pipeline
        config = _build_preview_config(req)
        result = preview_pipeline(config, req.sample_name)
        return result
    except Exception as e:
        logger.warning("[DUMMY-EPG] Preview failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/preview/batch")
async def preview_epg_batch(req: BatchPreviewRequest):
    """Test the EPG pipeline with multiple sample names (no DB)."""
    logger.debug("[DUMMY-EPG] POST /preview/batch count=%s", len(req.sample_names))
    try:
        from dummy_epg_engine import preview_pipeline_batch
        config = _build_preview_config(req)
        results = preview_pipeline_batch(config, req.sample_names[:100])  # Cap at 100
        return results
    except Exception as e:
        logger.warning("[DUMMY-EPG] Batch preview failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


def _build_preview_config(req) -> dict:
    """Build a config dict from a preview request for the engine."""
    config = {
        "substitution_pairs": [p.model_dump() for p in req.substitution_pairs],
        "title_pattern": req.title_pattern,
        "time_pattern": req.time_pattern,
        "date_pattern": req.date_pattern,
        "title_template": req.title_template,
        "description_template": req.description_template,
        "upcoming_title_template": req.upcoming_title_template,
        "upcoming_description_template": req.upcoming_description_template,
        "ended_title_template": req.ended_title_template,
        "ended_description_template": req.ended_description_template,
        "fallback_title_template": req.fallback_title_template,
        "fallback_description_template": req.fallback_description_template,
        "event_timezone": req.event_timezone,
        "output_timezone": req.output_timezone,
        "program_duration": req.program_duration,
        "channel_logo_url_template": req.channel_logo_url_template,
        "program_poster_url_template": req.program_poster_url_template,
    }
    if req.pattern_variants:
        config["pattern_variants"] = [v.model_dump() for v in req.pattern_variants]
    return config


def _resolve_group_assignments(channel_group_ids: list, channel_map: dict) -> list:
    """Resolve group IDs to channel assignment dicts from channel_map."""
    group_ids = set(channel_group_ids)
    assignments = []
    for ch_id, ch in channel_map.items():
        if ch.get("channel_group_id") in group_ids:
            assignments.append({"channel_id": ch_id, "channel_name": ch.get("name", "")})
    return assignments


async def _fetch_all_channels() -> dict:
    """Fetch all channels from Dispatcharr (paginated) and return {id: channel_dict}."""
    client = get_client()
    all_channels = []
    page = 1
    while True:
        resp = await client.get_channels(page=page, page_size=500)
        results = resp.get("results", [])
        all_channels.extend(results)
        if not resp.get("next"):
            break
        page += 1
    return {ch["id"]: ch for ch in all_channels}


@router.get("/xmltv")
async def get_xmltv_all(db: Session = Depends(get_session)):
    """Combined XMLTV output for all enabled profiles."""
    logger.debug("[DUMMY-EPG] GET /xmltv")
    try:
        # Check cache first
        cached = cache.get("dummy_epg_xmltv_all", ttl=300)
        if cached is not None:
            logger.debug("[DUMMY-EPG] Returning cached XMLTV (all profiles)")
            return Response(content=cached, media_type="application/xml")

        from models import DummyEPGProfile
        from dummy_epg_engine import generate_xmltv

        profiles = db.query(DummyEPGProfile).filter(
            DummyEPGProfile.enabled == True  # noqa: E712
        ).all()

        channel_map = await _fetch_all_channels()

        # Build profile data — resolve group IDs to channel assignments
        profile_data = []
        for profile in profiles:
            p_dict = profile.to_dict()
            p_dict["channel_assignments"] = _resolve_group_assignments(
                p_dict.get("channel_group_ids", []), channel_map
            )
            p_dict["channel_map"] = channel_map
            profile_data.append(p_dict)

        xml_string = generate_xmltv(profile_data, channel_map)
        cache.set("dummy_epg_xmltv_all", xml_string)

        logger.info("[DUMMY-EPG] Generated XMLTV for %s enabled profiles", len(profiles))
        return Response(content=xml_string, media_type="application/xml")
    except Exception as e:
        logger.warning("[DUMMY-EPG] Failed to generate XMLTV: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("/xmltv/{profile_id}")
async def get_xmltv_profile(profile_id: int, db: Session = Depends(get_session)):
    """XMLTV output for a single profile."""
    logger.debug("[DUMMY-EPG] GET /xmltv/%s", profile_id)
    try:
        cache_key = f"dummy_epg_xmltv_{profile_id}"
        cached = cache.get(cache_key, ttl=300)
        if cached is not None:
            logger.debug("[DUMMY-EPG] Returning cached XMLTV for profile %s", profile_id)
            return Response(content=cached, media_type="application/xml")

        from models import DummyEPGProfile
        from dummy_epg_engine import generate_xmltv

        profile = db.query(DummyEPGProfile).filter(
            DummyEPGProfile.id == profile_id
        ).first()
        if not profile:
            logger.warning("[DUMMY-EPG] Profile not found: %s", profile_id)
            raise HTTPException(status_code=404, detail="Profile not found")

        channel_map = await _fetch_all_channels()

        p_dict = profile.to_dict()
        p_dict["channel_assignments"] = _resolve_group_assignments(
            p_dict.get("channel_group_ids", []), channel_map
        )
        p_dict["channel_map"] = channel_map
        profile_data = [p_dict]

        xml_string = generate_xmltv(profile_data, channel_map)
        cache.set(cache_key, xml_string)

        logger.info("[DUMMY-EPG] Generated XMLTV for profile %s", profile_id)
        return Response(content=xml_string, media_type="application/xml")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[DUMMY-EPG] Failed to generate XMLTV for profile %s: %s", profile_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/generate")
async def force_regenerate(db: Session = Depends(get_session)):
    """Force regeneration of all XMLTV cache."""
    logger.debug("[DUMMY-EPG] POST /generate")
    try:
        from models import DummyEPGProfile
        from dummy_epg_engine import generate_xmltv

        # Invalidate all XMLTV cache
        cache.invalidate_prefix("dummy_epg_xmltv")

        profiles = db.query(DummyEPGProfile).filter(
            DummyEPGProfile.enabled == True  # noqa: E712
        ).all()

        channel_map = await _fetch_all_channels()

        # Build profile data — resolve group IDs to channel assignments
        profile_data = []
        for profile in profiles:
            p_dict = profile.to_dict()
            p_dict["channel_assignments"] = _resolve_group_assignments(
                p_dict.get("channel_group_ids", []), channel_map
            )
            p_dict["channel_map"] = channel_map
            profile_data.append(p_dict)

        xml_string = generate_xmltv(profile_data, channel_map)
        cache.set("dummy_epg_xmltv_all", xml_string)

        # Also cache per-profile
        for profile in profiles:
            p_dict = profile.to_dict()
            p_dict["channel_assignments"] = _resolve_group_assignments(
                p_dict.get("channel_group_ids", []), channel_map
            )
            p_dict["channel_map"] = channel_map
            per_xml = generate_xmltv([p_dict], channel_map)
            cache.set(f"dummy_epg_xmltv_{profile.id}", per_xml)

        logger.info("[DUMMY-EPG] Force-regenerated XMLTV for %s enabled profiles", len(profiles))
        return {"status": "ok", "profiles_generated": len(profiles)}
    except Exception as e:
        logger.warning("[DUMMY-EPG] Failed to force-regenerate XMLTV: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
