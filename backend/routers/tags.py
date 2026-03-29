"""
Tags router — tag group and tag management endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tags", tags=["Tags"])


# Request models
class CreateTagGroupRequest(BaseModel):
    name: str
    description: Optional[str] = None


class UpdateTagGroupRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class CreateTagsRequest(BaseModel):
    tags: list[str]  # List of tag values to add
    case_sensitive: bool = False


class UpdateTagRequest(BaseModel):
    enabled: Optional[bool] = None
    case_sensitive: Optional[bool] = None


class TestTagsRequest(BaseModel):
    text: str
    group_id: int


@router.get("/groups")
async def list_tag_groups():
    """List all tag groups with tag counts."""
    logger.debug("[TAGS] GET /groups")
    try:
        from models import TagGroup, Tag
        from sqlalchemy import func
        session = get_session()
        try:
            # Get groups with tag counts
            groups = session.query(
                TagGroup,
                func.count(Tag.id).label("tag_count")
            ).outerjoin(Tag).group_by(TagGroup.id).order_by(TagGroup.name).all()

            result = []
            for group, tag_count in groups:
                group_dict = group.to_dict()
                group_dict["tag_count"] = tag_count
                result.append(group_dict)

            return {"groups": result}
        finally:
            session.close()
    except Exception as e:
        logger.exception("[TAGS] Failed to list tag groups")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/groups")
async def create_tag_group(request: CreateTagGroupRequest):
    """Create a new tag group."""
    logger.debug("[TAGS] POST /groups - name=%s", request.name)
    try:
        from models import TagGroup
        session = get_session()
        try:
            # Check for duplicate name
            existing = session.query(TagGroup).filter(TagGroup.name == request.name).first()
            if existing:
                raise HTTPException(status_code=400, detail=f"Tag group '{request.name}' already exists")

            group = TagGroup(
                name=request.name,
                description=request.description,
                is_builtin=False
            )
            session.add(group)
            session.commit()
            session.refresh(group)
            logger.info("[TAGS] Created tag group id=%s name=%s", group.id, group.name)
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to create tag group")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/groups/{group_id}")
async def get_tag_group(group_id: int):
    """Get a tag group with all its tags."""
    logger.debug("[TAGS] GET /groups/%s", group_id)
    try:
        from models import TagGroup
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            return group.to_dict(include_tags=True)
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to get tag group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/groups/{group_id}")
async def update_tag_group(group_id: int, request: UpdateTagGroupRequest):
    """Update a tag group name/description."""
    logger.debug("[TAGS] PATCH /groups/%s", group_id)
    try:
        from models import TagGroup
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            # Prevent modifying built-in group name
            if group.is_builtin and request.name is not None and request.name != group.name:
                raise HTTPException(status_code=400, detail="Cannot rename built-in tag group")

            if request.name is not None:
                # Check for duplicate name
                existing = session.query(TagGroup).filter(
                    TagGroup.name == request.name,
                    TagGroup.id != group_id
                ).first()
                if existing:
                    raise HTTPException(status_code=400, detail=f"Tag group '{request.name}' already exists")
                group.name = request.name

            if request.description is not None:
                group.description = request.description

            session.commit()
            session.refresh(group)
            logger.info("[TAGS] Updated tag group id=%s name=%s", group.id, group.name)
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to update tag group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/groups/{group_id}")
async def delete_tag_group(group_id: int):
    """Delete a tag group and all its tags."""
    logger.debug("[TAGS] DELETE /groups/%s", group_id)
    try:
        from models import TagGroup
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            if group.is_builtin:
                raise HTTPException(status_code=400, detail="Cannot delete built-in tag group")

            group_name = group.name
            session.delete(group)  # Cascade deletes all tags
            session.commit()
            logger.info("[TAGS] Deleted tag group id=%s name=%s", group_id, group_name)

            from normalization_engine import invalidate_tag_cache
            invalidate_tag_cache()

            return {"status": "deleted", "id": group_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to delete tag group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/groups/{group_id}/tags")
async def add_tags_to_group(group_id: int, request: CreateTagsRequest):
    """Add one or more tags to a group."""
    logger.debug("[TAGS] POST /groups/%s/tags - count=%s", group_id, len(request.tags))
    try:
        from models import TagGroup, Tag
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            created_tags = []
            skipped_tags = []

            for tag_value in request.tags:
                tag_value = tag_value.strip()
                if not tag_value:
                    continue

                # Check if tag already exists in this group
                existing = session.query(Tag).filter(
                    Tag.group_id == group_id,
                    Tag.value == tag_value
                ).first()

                if existing:
                    skipped_tags.append(tag_value)
                    continue

                tag = Tag(
                    group_id=group_id,
                    value=tag_value,
                    case_sensitive=request.case_sensitive,
                    enabled=True,
                    is_builtin=False
                )
                session.add(tag)
                created_tags.append(tag_value)

            session.commit()
            logger.info("[TAGS] Added %s tags to group %s, skipped %s duplicates", len(created_tags), group_id, len(skipped_tags))

            if created_tags:
                from normalization_engine import invalidate_tag_cache
                invalidate_tag_cache()

            return {
                "created": created_tags,
                "skipped": skipped_tags,
                "group_id": group_id
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to add tags to group %s", group_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/groups/{group_id}/tags/{tag_id}")
async def update_tag(group_id: int, tag_id: int, request: UpdateTagRequest):
    """Update a tag's enabled or case_sensitive status."""
    logger.debug("[TAGS] PATCH /groups/%s/tags/%s", group_id, tag_id)
    try:
        from models import Tag
        session = get_session()
        try:
            tag = session.query(Tag).filter(
                Tag.id == tag_id,
                Tag.group_id == group_id
            ).first()
            if not tag:
                raise HTTPException(status_code=404, detail="Tag not found")

            if request.enabled is not None:
                tag.enabled = request.enabled
            if request.case_sensitive is not None:
                tag.case_sensitive = request.case_sensitive

            session.commit()
            session.refresh(tag)
            logger.info("[TAGS] Updated tag id=%s value=%s", tag.id, tag.value)

            from normalization_engine import invalidate_tag_cache
            invalidate_tag_cache()

            return tag.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to update tag %s", tag_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/groups/{group_id}/tags/{tag_id}")
async def delete_tag(group_id: int, tag_id: int):
    """Delete a tag from a group."""
    logger.debug("[TAGS] DELETE /groups/%s/tags/%s", group_id, tag_id)
    try:
        from models import Tag
        session = get_session()
        try:
            tag = session.query(Tag).filter(
                Tag.id == tag_id,
                Tag.group_id == group_id
            ).first()
            if not tag:
                raise HTTPException(status_code=404, detail="Tag not found")

            if tag.is_builtin:
                raise HTTPException(status_code=400, detail="Cannot delete built-in tag")

            tag_value = tag.value
            session.delete(tag)
            session.commit()
            logger.info("[TAGS] Deleted tag id=%s value=%s", tag_id, tag_value)

            from normalization_engine import invalidate_tag_cache
            invalidate_tag_cache()

            return {"status": "deleted", "id": tag_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to delete tag %s", tag_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/test")
async def test_tags(request: TestTagsRequest):
    """Test text against a tag group to find matches."""
    logger.debug("[TAGS] POST /test - group_id=%s", request.group_id)
    try:
        from models import TagGroup, Tag
        session = get_session()
        try:
            group = session.query(TagGroup).filter(TagGroup.id == request.group_id).first()
            if not group:
                raise HTTPException(status_code=404, detail="Tag group not found")

            # Get all enabled tags in the group
            tags = session.query(Tag).filter(
                Tag.group_id == request.group_id,
                Tag.enabled == True
            ).all()

            matches = []
            text = request.text

            for tag in tags:
                if tag.case_sensitive:
                    if tag.value in text:
                        matches.append({
                            "tag_id": tag.id,
                            "value": tag.value,
                            "case_sensitive": True
                        })
                else:
                    if tag.value.lower() in text.lower():
                        matches.append({
                            "tag_id": tag.id,
                            "value": tag.value,
                            "case_sensitive": False
                        })

            return {
                "text": request.text,
                "group_id": request.group_id,
                "group_name": group.name,
                "matches": matches,
                "match_count": len(matches)
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to test tags")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/export")
async def export_tags():
    """Export all tag groups and their tags as YAML."""
    logger.debug("[TAGS] GET /export")
    try:
        from models import TagGroup, Tag
        session = get_session()
        try:
            groups = session.query(TagGroup).order_by(TagGroup.name).all()

            export_data = {
                "tags": {
                    "version": 1,
                    "groups": []
                }
            }

            for group in groups:
                tags = session.query(Tag).filter(
                    Tag.group_id == group.id
                ).order_by(Tag.value).all()

                group_data = {
                    "name": group.name,
                    "description": group.description,
                    "is_builtin": group.is_builtin,
                    "tags": [
                        {
                            "value": tag.value,
                            "case_sensitive": tag.case_sensitive,
                            "enabled": tag.enabled,
                        }
                        for tag in tags
                    ]
                }
                export_data["tags"]["groups"].append(group_data)

            yaml_content = yaml.dump(export_data, default_flow_style=False, sort_keys=False, allow_unicode=True)
            return Response(
                content=yaml_content,
                media_type="application/x-yaml",
                headers={"Content-Disposition": "attachment; filename=tags.yaml"}
            )
        finally:
            session.close()
    except Exception as e:
        logger.exception("[TAGS] Failed to export tags")
        raise HTTPException(status_code=500, detail="Internal server error")


class ImportTagsRequest(BaseModel):
    yaml_content: str
    overwrite: bool = False  # If true, delete existing non-builtin groups first


@router.post("/import")
async def import_tags(request: ImportTagsRequest):
    """Import tag groups and tags from YAML."""
    logger.debug("[TAGS] POST /import - overwrite=%s", request.overwrite)
    try:
        data = yaml.safe_load(request.yaml_content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    if not data or "tags" not in data:
        raise HTTPException(status_code=400, detail="Missing 'tags' key in YAML")

    tags_data = data["tags"]
    if "groups" not in tags_data:
        raise HTTPException(status_code=400, detail="Missing 'groups' key in tags")

    try:
        from models import TagGroup, Tag
        session = get_session()
        try:
            # If overwrite, delete existing non-builtin groups
            if request.overwrite:
                existing_groups = session.query(TagGroup).filter(
                    TagGroup.is_builtin == False
                ).all()
                for g in existing_groups:
                    session.query(Tag).filter(Tag.group_id == g.id).delete()
                    session.delete(g)
                session.flush()

            created_groups = 0
            created_tags = 0
            skipped_groups = 0

            for group_data in tags_data["groups"]:
                group_name = group_data.get("name")
                if not group_name:
                    continue

                # Check if group exists
                existing = session.query(TagGroup).filter(
                    TagGroup.name == group_name
                ).first()

                if existing:
                    # Add tags to existing group, skip duplicates
                    for tag_data in group_data.get("tags", []):
                        tag_value = tag_data.get("value") if isinstance(tag_data, dict) else str(tag_data)
                        if not tag_value:
                            continue
                        existing_tag = session.query(Tag).filter(
                            Tag.group_id == existing.id,
                            Tag.value == tag_value
                        ).first()
                        if not existing_tag:
                            tag = Tag(
                                group_id=existing.id,
                                value=tag_value,
                                case_sensitive=tag_data.get("case_sensitive", False) if isinstance(tag_data, dict) else False,
                                enabled=tag_data.get("enabled", True) if isinstance(tag_data, dict) else True,
                                is_builtin=False,
                            )
                            session.add(tag)
                            created_tags += 1
                    skipped_groups += 1
                    continue

                group = TagGroup(
                    name=group_name,
                    description=group_data.get("description"),
                    is_builtin=False,
                )
                session.add(group)
                session.flush()
                created_groups += 1

                for tag_data in group_data.get("tags", []):
                    tag_value = tag_data.get("value") if isinstance(tag_data, dict) else str(tag_data)
                    if not tag_value:
                        continue
                    tag = Tag(
                        group_id=group.id,
                        value=tag_value,
                        case_sensitive=tag_data.get("case_sensitive", False) if isinstance(tag_data, dict) else False,
                        enabled=tag_data.get("enabled", True) if isinstance(tag_data, dict) else True,
                        is_builtin=False,
                    )
                    session.add(tag)
                    created_tags += 1

            session.commit()
            logger.info("[TAGS] Imported %s groups, %s tags, merged into %s existing groups",
                        created_groups, created_tags, skipped_groups)

            from normalization_engine import invalidate_tag_cache
            invalidate_tag_cache()

            return {
                "status": "imported",
                "created_groups": created_groups,
                "created_tags": created_tags,
                "merged_groups": skipped_groups,
            }
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[TAGS] Failed to import tags")
        raise HTTPException(status_code=500, detail=str(e))
