"""GH #1018 defence in depth: the handlers refuse ``preview_trigger`` on a live
run even when request-model validation was bypassed."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from routers import channel_pipeline as router_module
from routers.channel_pipeline import (
    RunPipelinePreviewableRequest,
    RunSelectedRulesRequest,
)


@pytest.mark.asyncio
async def test_run_refuses_preview_trigger_on_a_live_run_without_model_validation():
    request = RunPipelinePreviewableRequest.model_construct(
        dry_run=False, preview_trigger="m3u_refresh", rule_ids=None,
    )
    with pytest.raises(HTTPException) as caught:
        await router_module.run_auto_creation_pipeline(
            request, _admin=None, caller_is_mcp=False,
        )
    assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_run_selected_refuses_preview_trigger_on_a_live_run_without_model_validation():
    request = RunSelectedRulesRequest.model_construct(
        dry_run=False, preview_trigger="m3u_refresh", rule_ids=[1],
    )
    with pytest.raises(HTTPException) as caught:
        await router_module.run_selected_auto_creation_rules(
            request, _admin=None, caller_is_mcp=False,
        )
    assert caught.value.status_code == 422
