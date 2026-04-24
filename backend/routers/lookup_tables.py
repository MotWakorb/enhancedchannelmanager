"""
Lookup Tables router — CRUD for named key→value tables used by the dummy EPG
template engine's `|lookup:<name>` pipe transform.
"""
import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database import get_session
from models import LookupTable


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lookup-tables", tags=["Lookup Tables"])


_MAX_NAME_LEN = 100
_MAX_ENTRIES = 10_000  # Soft cap — single table shouldn't grow unbounded.


class LookupTableCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=_MAX_NAME_LEN)
    description: Optional[str] = None
    entries: dict[str, str] = Field(default_factory=dict)


class LookupTableUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=_MAX_NAME_LEN)
    description: Optional[str] = None
    entries: Optional[dict[str, str]] = None


def _validate_entries(entries: dict[str, str]) -> None:
    if len(entries) > _MAX_ENTRIES:
        raise HTTPException(
            status_code=400,
            detail=f"Lookup table exceeds maximum of {_MAX_ENTRIES} entries",
        )
    for k, v in entries.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise HTTPException(
                status_code=400,
                detail="Lookup table keys and values must both be strings",
            )


@router.get("")
async def list_lookup_tables():
    """Return all lookup tables with entry counts (entries omitted for brevity)."""
    db = get_session()
    try:
        rows = db.query(LookupTable).order_by(LookupTable.name).all()
        return [
            {
                "id": r.id,
                "name": r.name,
                "description": r.description,
                "entry_count": len(json.loads(r.entries or "{}")),
                "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
                "updated_at": r.updated_at.isoformat() + "Z" if r.updated_at else None,
            }
            for r in rows
        ]
    finally:
        db.close()


@router.post("", status_code=201)
async def create_lookup_table(request: LookupTableCreateRequest):
    _validate_entries(request.entries)
    db = get_session()
    try:
        existing = db.query(LookupTable).filter(LookupTable.name == request.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Lookup table '{request.name}' already exists")

        row = LookupTable(
            name=request.name,
            description=request.description,
            entries=json.dumps(request.entries),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        logger.info("[LOOKUP] Created lookup table id=%s name=%s entries=%d", row.id, row.name, len(request.entries))
        return row.to_dict()
    finally:
        db.close()


@router.get("/{table_id}")
async def get_lookup_table(table_id: int):
    db = get_session()
    try:
        row = db.query(LookupTable).filter(LookupTable.id == table_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Lookup table not found")
        return row.to_dict()
    finally:
        db.close()


@router.patch("/{table_id}")
async def update_lookup_table(table_id: int, request: LookupTableUpdateRequest):
    db = get_session()
    try:
        row = db.query(LookupTable).filter(LookupTable.id == table_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Lookup table not found")

        if request.name is not None and request.name != row.name:
            clash = db.query(LookupTable).filter(
                LookupTable.name == request.name,
                LookupTable.id != table_id,
            ).first()
            if clash:
                raise HTTPException(status_code=409, detail=f"Lookup table '{request.name}' already exists")
            row.name = request.name

        if request.description is not None:
            row.description = request.description

        if request.entries is not None:
            _validate_entries(request.entries)
            row.entries = json.dumps(request.entries)

        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        logger.info("[LOOKUP] Updated lookup table id=%s name=%s", row.id, row.name)
        return row.to_dict()
    finally:
        db.close()


@router.delete("/{table_id}", status_code=204)
async def delete_lookup_table(table_id: int):
    db = get_session()
    try:
        row = db.query(LookupTable).filter(LookupTable.id == table_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Lookup table not found")
        name = row.name
        db.delete(row)
        db.commit()
        logger.info("[LOOKUP] Deleted lookup table id=%s name=%s", table_id, name)
        return None
    finally:
        db.close()
