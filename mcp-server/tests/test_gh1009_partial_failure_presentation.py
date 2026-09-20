"""GH #1009 / PR #1010: how the sidecar presents a planned-commit 424.

The backend answers a partial replay with ``424 Failed Dependency`` whose
``detail`` carries only bounded, payload-free descriptors
(``backend/tests/integration/test_gh1009_commit_partial_failure_evidence.py``
proves that at the producer, including the credential canary). The sidecar
is a pass-through: ``ECMClient.post`` logs the first 500 bytes of the body
and ``_http_error`` surfaces ``detail`` in the raised message, which the
``run_channel_pipeline`` tool returns to the operator. These tests pin that
presentation:

* the operator text and the sidecar log carry the correlation an operator
  needs (``execution_id``, ``failed_write``, ``failed_outcome``,
  ``not_applied``) verbatim;
* the sidecar adds nothing of its own — a control shows that a body which
  DID carry a URL would reach the log and the tool text unchanged, which is
  exactly why the redaction has to happen at the backend producer and is
  asserted there.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import httpx
import pytest

import ecm_client
from ecm_client import ECMClient, _http_error

COMMIT_PATH = "/api/channel-pipeline/run/commit"
CANARY = "SECRET-TOKEN-8f3a9c-canary"

# The exact field set the backend's commit handler emits on a partial replay
# (routers/channel_pipeline.py, ``except PartialReplayError``), with the
# descriptors the backend produced for a rejected credential-bearing
# ``create_logo`` first write. No payload contents appear by construction.
BACKEND_424_DETAIL = {
    "message": "pipeline replay partially failed",
    "execution_id": 91,
    "failed_index": 0,
    "failed_write": "create_logo#0",
    "failed_outcome": "rejected",
    "pre_mutation": True,
    "completed_writes": [],
    "not_applied": ["create_channel#1", "update_channel:pending(-2)"],
    "compensation_errors": [],
}


def _sidecar_over(body: dict, status: int = 424) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == COMMIT_PATH
        return httpx.Response(status, json=body, request=request)

    return httpx.AsyncClient(base_url="http://ecm", transport=httpx.MockTransport(handler))


def _register_and_get_mcp():
    from tools.channel_pipeline import register
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    register(mcp)
    return mcp


def _text(result) -> str:
    return result[0][0].text


@pytest.mark.asyncio
async def test_client_error_and_log_carry_the_safe_descriptors(caplog):
    caplog.set_level(logging.DEBUG)
    transport_client = _sidecar_over({"detail": BACKEND_424_DETAIL})
    try:
        with patch.object(ecm_client, "_get_client", return_value=transport_client):
            with pytest.raises(RuntimeError) as error:
                await ECMClient().post(COMMIT_PATH, json_data={
                    "plan_id": "p", "plan_hash": "h", "phase": "execute",
                })
    finally:
        await transport_client.aclose()

    message = str(error.value)
    assert message.startswith(f"POST {COMMIT_PATH} -> HTTP 424")
    for token in ("'execution_id': 91", "create_logo#0", "'failed_outcome': 'rejected'",
                  "create_channel#1", "update_channel:pending(-2)"):
        assert token in message, message
    assert "create_logo#0" in caplog.text and "424" in caplog.text
    # Nothing that is not in the backend body can appear here.
    assert CANARY not in message and CANARY not in caplog.text
    assert "http://provider" not in message and "logo.png" not in caplog.text


@pytest.mark.asyncio
async def test_run_channel_pipeline_tool_returns_the_correlation_to_the_operator(caplog):
    caplog.set_level(logging.DEBUG)
    mcp = _register_and_get_mcp()
    transport_client = _sidecar_over({"detail": BACKEND_424_DETAIL})
    try:
        with patch.object(ecm_client, "_get_client", return_value=transport_client), \
             patch("tools.channel_pipeline.get_ecm_client", return_value=ECMClient()):
            result = await mcp.call_tool(
                "run_channel_pipeline",
                {"dry_run": False, "plan_id": "p", "plan_hash": "h", "plan_phase": "execute"},
            )
    finally:
        await transport_client.aclose()

    text = _text(result)
    assert text.startswith("Error running auto-creation:")
    assert "424" in text
    assert "'execution_id': 91" in text
    assert "create_logo#0" in text
    assert "'failed_outcome': 'rejected'" in text
    assert CANARY not in text and CANARY not in caplog.text


def test_http_error_renders_detail_verbatim_which_is_why_the_producer_must_be_safe():
    """Control: the sidecar does not redact. A body that carried a URL would
    surface it unchanged, so the credential guarantee is the backend's and is
    proven at that boundary, not here."""
    request = httpx.Request("POST", "http://ecm" + COMMIT_PATH)
    leaky = {**BACKEND_424_DETAIL, "failed_write": f"create_logo:http://p.example/l.png?token={CANARY}"}
    response = httpx.Response(424, json={"detail": leaky}, request=request)
    exc = httpx.HTTPStatusError("424", request=request, response=response)

    rendered = str(_http_error("POST", COMMIT_PATH, exc))

    assert CANARY in rendered  # pass-through, by design: safety lives at the producer
    safe = httpx.Response(424, json={"detail": BACKEND_424_DETAIL}, request=request)
    assert CANARY not in str(_http_error(
        "POST", COMMIT_PATH, httpx.HTTPStatusError("424", request=request, response=safe),
    ))
