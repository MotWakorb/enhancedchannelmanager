"""
DELIBERATE CODEQL HIGH SYNTHETIC TEST — DO NOT MERGE.

This module exists solely to trigger CodeQL HIGH findings so we can
verify the ADR-005 Phase 3b delta-zero enforcement step in
.github/workflows/build.yml correctly fails a PR with open HIGH alerts.

Two triggers for redundancy:
  1. py/command-injection (HIGH) via os.system on user input
  2. py/path-injection (HIGH) via open() on user input

Bead: enhancedchannelmanager-kgjci (Phase 3b validation v2)
Will be removed before merge.
"""

import os

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/_phase3b_synthetic/exec")
def synthetic_command_injection(name: str = Query(...)) -> int:
    # Unsanitized user input flows directly into os.system — CodeQL
    # py/command-injection (HIGH) should flag this.
    return os.system("echo " + name)  # noqa: S605 — synthetic test


@router.get("/_phase3b_synthetic/read")
def synthetic_path_injection(name: str = Query(...)) -> str:
    # Unsanitized user input flows directly into open() — CodeQL
    # py/path-injection (HIGH) should flag this.
    with open("/tmp/" + name, "r") as f:  # noqa: S108 — synthetic test
        return f.read()
