"""Shared display exclusions, independent of collection and digest rules."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import select, tuple_, union
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from models import M3UChangeExclusion, M3UChangeLog

_SQL_BATCH_SIZE = 200  # Two binds per identity; stay below SQLite's legacy 999 limit.


class M3UChangeGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    m3u_account_id: Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]
    group_name: Annotated[str, Field(strict=True, min_length=1, max_length=255)]


class M3UChangeExclusionsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    add: list[M3UChangeGroup] = Field(default_factory=list, max_length=1000)
    remove: list[M3UChangeGroup] = Field(default_factory=list, max_length=1000)
    clear: StrictBool = False

    @model_validator(mode="after")
    def validate_delta(self):
        if self.clear and (self.add or self.remove):
            raise ValueError("clear cannot be combined with add or remove")
        if set(self.add) & set(self.remove):
            raise ValueError("a group cannot be both added and removed")
        return self


def visible_changes(query):
    """Correlated NOT EXISTS preserves NULL group rows and uses the composite PK."""
    excluded = select(M3UChangeExclusion.m3u_account_id).where(
        M3UChangeExclusion.m3u_account_id == M3UChangeLog.m3u_account_id,
        M3UChangeExclusion.group_name == M3UChangeLog.group_name,
    ).exists()
    return query.filter(~excluded)


def list_exclusions(db: Session) -> dict:
    rows = db.query(M3UChangeExclusion).order_by(
        M3UChangeExclusion.m3u_account_id, M3UChangeExclusion.group_name,
    ).all()
    return {"exclusions": [row.to_dict() for row in rows]}


def update_exclusions(db: Session, delta: M3UChangeExclusionsUpdate) -> dict:
    """Write deltas in one transaction; never read/replace another client's list."""
    try:
        if delta.clear:
            db.query(M3UChangeExclusion).delete(synchronize_session=False)
        for start in range(0, len(delta.add), _SQL_BATCH_SIZE):
            rows = [group.model_dump() for group in delta.add[start:start + _SQL_BATCH_SIZE]]
            db.execute(insert(M3UChangeExclusion).values(rows).on_conflict_do_nothing())
        for start in range(0, len(delta.remove), _SQL_BATCH_SIZE):
            identities = [(g.m3u_account_id, g.group_name) for g in delta.remove[start:start + _SQL_BATCH_SIZE]]
            db.query(M3UChangeExclusion).filter(
                tuple_(M3UChangeExclusion.m3u_account_id, M3UChangeExclusion.group_name).in_(identities),
            ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return list_exclusions(db)


def list_groups(db: Session, page: int, page_size: int, account_id: int | None, search: str) -> dict:
    # Include saved identities even after history retention removes their logs.
    groups = union(
        select(M3UChangeLog.m3u_account_id, M3UChangeLog.group_name).where(
            M3UChangeLog.group_name.isnot(None), M3UChangeLog.group_name != "",
        ),
        select(M3UChangeExclusion.m3u_account_id, M3UChangeExclusion.group_name),
    ).subquery()
    query = db.query(groups)
    if account_id is not None:
        query = query.filter(groups.c.m3u_account_id == account_id)
    if search:
        query = query.filter(groups.c.group_name.contains(search, autoescape=True))
    total = query.count()
    rows = query.order_by(groups.c.m3u_account_id, groups.c.group_name).offset(
        (page - 1) * page_size,
    ).limit(page_size).all()
    return {
        "results": [{"m3u_account_id": row.m3u_account_id, "group_name": row.group_name} for row in rows],
        "total": total, "page": page, "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }
