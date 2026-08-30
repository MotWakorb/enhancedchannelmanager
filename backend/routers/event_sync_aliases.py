"""
Event Sync team-alias dictionary router (bead enhancedchannelmanager-ti939.4.2).

Settings surface for the operator team-alias dictionary consulted by the
event matcher's team-token layer (services/event_sync_matcher.py):
groups of KNOWN-equivalent team spellings ("Man Utd" == "Manchester
United" == "MUFC") that raise recall on abbreviation-heavy providers
WITHOUT loosening the fuzzy threshold.

Storage is a JSON setting on DispatcharrSettings
(``event_sync_team_aliases`` — no DB table, no migration), written ONLY
through this router: full-replace PUT, validated against the matcher's own
normalization, journaled with before/after state. The general settings
form never sends the field and routers/settings.py preserves it on rebuild.

The shipped dictionary is EMPTY by design. Aliases are corpus-gated —
every alias an operator adds should be backed by observed provider pairs
(a wrong alias is a new false-positive vector). See docs/event_sync.md.
"""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import journal
from config import get_settings, save_settings
from services.event_sync_matcher import normalize_alias_term

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/event-sync", tags=["Event Sync"])

# Caps are generous for real dictionaries,
# bounded against unbounded settings.json growth.
_MAX_GROUPS = 200
_MAX_TERMS_PER_GROUP = 50
_MAX_TERM_LEN = 100
_MAX_NOTE_LEN = 500


# Per-term length cap at the Pydantic layer so an oversized term 422s with
# a field path; the semantic checks below own the 400s.
_AliasTerm = Annotated[str, Field(max_length=_MAX_TERM_LEN)]


class TeamAliasGroup(BaseModel):
    """One group of equivalent team spellings (+ optional operator note)."""

    terms: list[_AliasTerm] = Field(..., max_length=_MAX_TERMS_PER_GROUP)
    note: Optional[str] = Field(default=None, max_length=_MAX_NOTE_LEN)


class TeamAliasesUpdateRequest(BaseModel):
    groups: list[TeamAliasGroup]


def _validate_groups(groups: list[TeamAliasGroup]) -> list[dict]:
    """Validate alias groups against the matcher's own normalization.

    Returns the normalized storage shape (list of plain dicts). Raises
    HTTPException(400) with an operator-actionable message on the first
    violation.
    """
    if len(groups) > _MAX_GROUPS:
        raise HTTPException(
            status_code=400,
            detail=f"Too many alias groups ({len(groups)}); maximum is {_MAX_GROUPS}",
        )

    seen: dict[tuple[str, ...], str] = {}
    stored: list[dict] = []
    for group_number, group in enumerate(groups, start=1):
        cleaned_terms: list[str] = []
        for term in group.terms:
            cleaned = term.strip()
            if not cleaned:
                raise HTTPException(
                    status_code=400,
                    detail=f"Alias group {group_number} contains a blank term",
                )
            key = normalize_alias_term(cleaned)
            if not key:
                # A term with no identity tokens ("FC", "W") could never
                # match a team side — saving it would be silent dead weight.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Alias term '{cleaned}' has no identity tokens after "
                        "normalization (generic/qualifier words only) and "
                        "could never match a team name"
                    ),
                )
            if key in seen:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Alias term '{cleaned}' duplicates '{seen[key]}' "
                        "(terms are compared after normalization; a term may "
                        "appear in only one group)"
                    ),
                )
            seen[key] = cleaned
            cleaned_terms.append(cleaned)

        if len(cleaned_terms) < 2:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Alias group {group_number} needs at least 2 terms — a "
                    "single spelling declares no equivalence"
                ),
            )
        note = group.note.strip() if group.note else None
        stored.append({"terms": cleaned_terms, "note": note or None})
    return stored


@router.get("/team-aliases")
async def get_team_aliases():
    """Return the operator team-alias dictionary (alias groups)."""
    settings = get_settings()
    return {"groups": settings.event_sync_team_aliases or []}


@router.put("/team-aliases")
async def update_team_aliases(request: TeamAliasesUpdateRequest):
    """Full-replace write of the team-alias dictionary (validated + journaled)."""
    stored = _validate_groups(request.groups)

    current = get_settings()
    before = list(current.event_sync_team_aliases or [])
    updated = current.model_copy(update={"event_sync_team_aliases": stored})
    save_settings(updated)

    journal.log_entry(
        category="event_sync",
        action_type="update",
        entity_name="Event Sync Team Aliases",
        description=(
            f"Updated team-alias dictionary: {len(stored)} group(s), "
            f"{sum(len(g['terms']) for g in stored)} term(s)"
        ),
        before_value={"groups": before},
        after_value={"groups": stored},
    )
    logger.info(
        "[EVENT-SYNC] Team-alias dictionary updated: %d group(s), %d term(s)",
        len(stored), sum(len(g["terms"]) for g in stored),
    )
    return {"groups": stored}
