"""
Tags router — tag group and tag management endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
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
        logger.error(f"Failed to list tag groups: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/groups")
async def create_tag_group(request: CreateTagGroupRequest):
    """Create a new tag group."""
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
            logger.info(f"Created tag group: id={group.id}, name={group.name}")
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create tag group: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/groups/{group_id}")
async def get_tag_group(group_id: int):
    """Get a tag group with all its tags."""
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
        logger.error(f"Failed to get tag group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/groups/{group_id}")
async def update_tag_group(group_id: int, request: UpdateTagGroupRequest):
    """Update a tag group name/description."""
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
            logger.info(f"Updated tag group: id={group.id}, name={group.name}")
            return group.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update tag group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/groups/{group_id}")
async def delete_tag_group(group_id: int):
    """Delete a tag group and all its tags."""
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
            logger.info(f"Deleted tag group: id={group_id}, name={group_name}")

            from normalization_engine import invalidate_tag_cache
            invalidate_tag_cache()

            return {"status": "deleted", "id": group_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete tag group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/groups/{group_id}/tags")
async def add_tags_to_group(group_id: int, request: CreateTagsRequest):
    """Add one or more tags to a group."""
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
            logger.info(f"Added {len(created_tags)} tags to group {group_id}, skipped {len(skipped_tags)} duplicates")

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
        logger.error(f"Failed to add tags to group {group_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/groups/{group_id}/tags/{tag_id}")
async def update_tag(group_id: int, tag_id: int, request: UpdateTagRequest):
    """Update a tag's enabled or case_sensitive status."""
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
            logger.info(f"Updated tag: id={tag.id}, value={tag.value}")

            from normalization_engine import invalidate_tag_cache
            invalidate_tag_cache()

            return tag.to_dict()
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update tag {tag_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/groups/{group_id}/tags/{tag_id}")
async def delete_tag(group_id: int, tag_id: int):
    """Delete a tag from a group."""
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
            logger.info(f"Deleted tag: id={tag_id}, value={tag_value}")

            from normalization_engine import invalidate_tag_cache
            invalidate_tag_cache()

            return {"status": "deleted", "id": tag_id}
        finally:
            session.close()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete tag {tag_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/test")
async def test_tags(request: TestTagsRequest):
    """Test text against a tag group to find matches."""
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
        logger.error(f"Failed to test tags: {e}")
        raise HTTPException(status_code=500, detail=str(e))
