"""GH #801 / bead rtst2.1 - the merge-scope pin advisory reaches the API.

The analyzer unit tests (tests/unit/test_channel_pipeline_rule_analyzer.py ::
TestMergeScopePinnedToOtherGroup) pin the finding logic. These tests pin the
wiring: POST /rules/analyze-body builds its own trimmed rule dict for the live
rule builder, and MERGE_SCOPE_PINNED_TO_OTHER_GROUP only fires there if that
dict carries match_scope_group_id and orphan_action. Without the forwarding the
advisory would exist but never render while an operator is authoring the rule
that needs it.
"""
import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def test_client():
    """Client with the admin gate overridden, matching the sibling API tests."""
    from main import app
    from auth import RequireAdminIfEnabled as _prebuilt

    async def _allow_no_auth():
        return None

    app.dependency_overrides[_prebuilt.dependency] = _allow_no_auth
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(_prebuilt.dependency, None)


def _analyze_body(client, body: dict) -> list[dict]:
    with patch("routers.channel_pipeline.get_session") as get_session:
        get_session.return_value = MagicMock()
        response = client.post("/api/channel-pipeline/rules/analyze-body", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["rules"]) == 1
    return payload["rules"][0]["findings"]


_PIN_CODE = "MERGE_SCOPE_PINNED_TO_OTHER_GROUP"


class TestAnalyzeBodySurfacesMergeScopePin:
    def test_pinned_scope_mismatch_is_reported(self, test_client):
        findings = _analyze_body(test_client, {
            "name": "PPV",
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
            "target_group_id": 12,
            "match_scope_target_group": True,
            "match_scope_group_id": 7,
            "orphan_action": "delete",
        })
        pin = [f for f in findings if f["code"] == _PIN_CODE]
        assert len(pin) == 1
        assert pin[0]["severity"] == "warning"
        assert pin[0]["detail"]["match_scope_group_id"] == 7
        assert pin[0]["detail"]["create_group_id"] == 12
        assert pin[0]["detail"]["deletes_orphans"] is True

    def test_matching_scope_pin_is_not_reported(self, test_client):
        findings = _analyze_body(test_client, {
            "name": "PPV",
            "conditions": [],
            "actions": [{"type": "create_channel", "if_exists": "merge"}],
            "target_group_id": 12,
            "match_scope_target_group": True,
            "match_scope_group_id": 12,
            "orphan_action": "delete",
        })
        assert [f for f in findings if f["code"] == _PIN_CODE] == []
