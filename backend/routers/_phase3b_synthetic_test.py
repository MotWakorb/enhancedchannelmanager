"""
DELIBERATE CODEQL HIGH SYNTHETIC TEST — DO NOT MERGE.

This module exists solely to trigger a py/path-injection HIGH finding
so we can verify the ADR-005 Phase 3b delta-zero enforcement step in
.github/workflows/build.yml correctly fails a PR with open HIGH alerts.

Bead: enhancedchannelmanager-kgjci (Phase 3b test)
Will be removed before merge.
"""

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/_phase3b_synthetic/read")
def synthetic_path_injection(name: str = Query(...)) -> str:
    # Unsanitized user input flows directly into open() — CodeQL
    # py/path-injection (HIGH) should flag this.
    with open("/tmp/" + name, "r") as f:  # noqa: S108 — synthetic test
        return f.read()
