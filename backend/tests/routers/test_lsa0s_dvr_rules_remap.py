"""The dvr_rules backup category, exercised across the real HTTP seam (lsa0s).

Every other DVR test in the suite mocks ``DispatcharrClient`` itself, so none of
them could catch the defect this bead fixes: ECM asked for ``/api/dvr/rules/``,
a path Dispatcharr 0.28.2 has no route for, and Dispatcharr answered the SPA
shell (200 ``text/html``) instead of a 404. ``get_dvr_rules()`` then died in
``response.json()`` and :func:`_gather_dispatcharr_sections` converted that into
a ``{"_warning": "Dispatcharr not connected — ..."}`` stub, so EVERY backup run
silently shipped a category with no data in it.

These tests therefore build a REAL :class:`DispatcharrClient` over an
``httpx.MockTransport`` that reproduces Dispatcharr 0.28.2's routing behaviour —
including the SPA catch-all — using the recorded fixture
``tests/fixtures/dispatcharr_dvr_recurring_rules_recorded.json`` as the source
of truth for what each path answers. That is the only layer at which a wrong
PATH is provable; a mocked client would happily return rows for any path at all.
"""
import json
from pathlib import Path

import httpx
import pytest
from unittest.mock import patch

import routers.backup as backup_mod
from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient

_RECORDED = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "dispatcharr_dvr_recurring_rules_recorded.json"
    ).read_text()
)

_SPA_SHELL = (
    "<!doctype html>\n<html lang=\"en\">\n  <head>\n    "
    "<title>Dispatcharr</title>\n  </head>\n  <body>\n    "
    "<div id=\"root\"></div>\n  </body>\n</html>\n"
)

# One archived-shaped recurring rule, field-for-field the recorded
# RecurringRecordingRule schema (name identity + one integer ``channel`` FK).
_LIVE_RULE = {
    "id": 41,
    "name": "Weeknight News",
    "channel": 5,
    "days_of_week": [0, 1, 2, 3, 4],
    "start_time": "18:00:00",
    "end_time": "18:30:00",
    "enabled": True,
    "start_date": "2026-08-01",
    "end_date": None,
    "created_at": "2026-08-01T12:00:00Z",
    "updated_at": "2026-08-01T12:00:00Z",
}


def _dispatcharr_0_28_2_transport(rules: list[dict]) -> httpx.MockTransport:
    """A transport that routes like Dispatcharr 0.28.2 — SPA catch-all included.

    Only the paths the recorded fixture proves exist answer JSON. Anything else
    falls through to the SPA shell with a 200 and ``text/html``, exactly as the
    live instance did for ``/api/dvr/rules/``.
    """
    known_json = {
        _RECORDED["recurring_rules_list_response"]["path"]: rules,
        _RECORDED["comskip_config_response"]["path"]: _RECORDED[
            "comskip_config_response"
        ]["body"],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = known_json.get(request.url.path)
        if body is not None:
            return httpx.Response(200, json=body)
        return httpx.Response(
            200, text=_SPA_SHELL, headers={"content-type": "text/html; charset=utf-8"}
        )

    return httpx.MockTransport(handler)


def _client_over(transport: httpx.MockTransport) -> DispatcharrClient:
    client = DispatcharrClient(
        DispatcharrSettings(
            url="http://dispatcharr:9191",
            auth_method="api_key",
            dispatcharr_api_key="key-123",
        )
    )
    # Swap the real connection pool for the routing emulator; everything else
    # (auth header, URL construction, JSON decoding) stays the production path.
    original = client._client
    client._client = httpx.AsyncClient(transport=transport)
    return client, original


@pytest.mark.asyncio
async def test_dvr_rules_survive_the_spa_catch_all_and_carry_real_rows():
    """The gather step returns REAL rows, not a ``_warning`` stub.

    Red before the fix: the old ``/api/dvr/rules/`` request lands on the SPA
    catch-all, ``response.json()`` raises, and the section degrades to the
    warning stub.
    """
    client, original = _client_over(_dispatcharr_0_28_2_transport([_LIVE_RULE]))
    try:
        with patch.object(backup_mod, "get_client", return_value=client):
            result = await backup_mod._gather_dispatcharr_sections({"dvr_rules"})
    finally:
        await client._client.aclose()
        await original.aclose()

    assert "_warning" not in result
    assert result["dvr_rules"] == [_LIVE_RULE]


@pytest.mark.asyncio
async def test_dvr_rules_empty_upstream_is_an_honest_empty_category():
    """No rules configured upstream is an EMPTY list, never a warning stub.

    This is the doc-test instance's literal recorded state (``[]``): a backup of
    an instance with no DVR rules must say "0 rules", not "could not fetch".
    """
    recorded_empty = _RECORDED["recurring_rules_list_response"]["body"]
    client, original = _client_over(_dispatcharr_0_28_2_transport(recorded_empty))
    try:
        with patch.object(backup_mod, "get_client", return_value=client):
            result = await backup_mod._gather_dispatcharr_sections({"dvr_rules"})
    finally:
        await client._client.aclose()
        await original.aclose()

    assert result == {"dvr_rules": []}
