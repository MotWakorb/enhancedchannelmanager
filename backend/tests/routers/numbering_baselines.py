"""The numbering baselines a caller that read a given lineup would send.

Every numbering write to an existing channel must carry the number its caller
last read (``expectedNumber`` on ``updateChannel``, ``expectedNumbers`` on
``bulkAssignChannelNumbers``, ``expected_numbers`` on ``POST
/api/channels/assign-numbers``). Tests whose subject is something else, such as
ordering, compensation or journaling, still have to send them, and the honest
value is the number the test's mocked lineup holds.

These helpers fill in only what an operation leaves out, so a test that sends a
baseline deliberately keeps the one it chose. The tests of the baseline check
itself live in ``test_renumber_baselines.py`` and spell every baseline out.
"""
from __future__ import annotations

import copy
from typing import Iterable


def lineup_numbers(lineup: Iterable[dict]) -> dict[int, object]:
    """``{channel id: channel_number}``, ``None`` for a channel with no number."""
    return {channel["id"]: channel.get("channel_number") for channel in lineup}


def with_lineup_baselines(operations: list[dict], lineup: Iterable[dict]) -> list[dict]:
    """Return ``operations`` with each missing existing-channel baseline filled in.

    A channel the lineup does not list gets ``None``, which is what a caller
    that never saw it would claim.
    """
    numbers = lineup_numbers(lineup)
    filled = []
    for op in operations:
        if not isinstance(op, dict):
            filled.append(op)
            continue
        op = copy.deepcopy(op)
        if (
            op.get("type") == "updateChannel"
            and "channel_number" in (op.get("data") or {})
            and op.get("channelId", -1) >= 0
            and "expectedNumber" not in op
        ):
            op["expectedNumber"] = {"number": numbers.get(op["channelId"])}
        elif op.get("type") == "bulkAssignChannelNumbers" and "expectedNumbers" not in op:
            op["expectedNumbers"] = {
                cid: numbers.get(cid) for cid in op.get("channelIds", []) if cid >= 0
            }
        filled.append(op)
    return filled


def assign_body_with_baselines(body: dict, lineup: Iterable[dict]) -> dict:
    """An ``/assign-numbers`` body with ``expected_numbers`` filled from ``lineup``."""
    if "expected_numbers" in body:
        return body
    numbers = lineup_numbers(lineup)
    return {
        **body,
        "expected_numbers": {cid: numbers.get(cid) for cid in body.get("channel_ids", [])},
    }
