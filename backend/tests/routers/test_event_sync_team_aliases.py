"""
Unit tests for routers/event_sync_aliases.py (bead
enhancedchannelmanager-ti939.4.2) — the operator team-alias dictionary
settings surface.

GET returns the stored alias groups (empty by default — the shipped
dictionary is deliberately empty; operator aliases are corpus-gated).
PUT is a full-replace write: validated, journaled, persisted via the
settings store (JSON setting — no DB table, no migration).
"""
import pytest
from unittest.mock import patch

from httpx import ASGITransport, AsyncClient

from config import DispatcharrSettings, MCPApiKeyStorageError


def _current(groups=None):
    return DispatcharrSettings(event_sync_team_aliases=groups or [])


_VALID_BODY = {
    "groups": [
        {"terms": ["Red Devils", "Manchester United", "MUFC"], "note": "corpus pair 2026-07-18"},
        {"terms": ["Spurs", "Tottenham Hotspur"]},
    ]
}


class TestGetTeamAliases:
    @pytest.mark.asyncio
    async def test_returns_empty_groups_by_default(self, async_client):
        with patch("routers.event_sync_aliases.get_settings", return_value=_current()):
            response = await async_client.get("/api/event-sync/team-aliases")
        assert response.status_code == 200
        assert response.json() == {"groups": []}

    @pytest.mark.asyncio
    async def test_returns_stored_groups(self, async_client):
        stored = [{"terms": ["Spurs", "Tottenham Hotspur"], "note": None}]
        with patch("routers.event_sync_aliases.get_settings", return_value=_current(stored)):
            response = await async_client.get("/api/event-sync/team-aliases")
        assert response.status_code == 200
        assert response.json() == {"groups": stored}


class TestPutTeamAliases:
    @pytest.mark.asyncio
    async def test_round_trip_persists_via_settings_store(self, async_client):
        captured = {}

        def capture_save(new_settings):
            captured["groups"] = new_settings.event_sync_team_aliases

        with patch("routers.event_sync_aliases.get_settings", return_value=_current()), \
             patch("routers.event_sync_aliases.save_settings", side_effect=capture_save), \
             patch("routers.event_sync_aliases.journal.log_entry"):
            response = await async_client.put(
                "/api/event-sync/team-aliases", json=_VALID_BODY
            )

        assert response.status_code == 200, response.json()
        body = response.json()
        assert [g["terms"] for g in body["groups"]] == [
            ["Red Devils", "Manchester United", "MUFC"],
            ["Spurs", "Tottenham Hotspur"],
        ]
        assert captured["groups"] == body["groups"]

    @pytest.mark.asyncio
    async def test_journals_before_and_after(self, async_client):
        before = [{"terms": ["Spurs", "Tottenham Hotspur"], "note": None}]
        with patch("routers.event_sync_aliases.get_settings", return_value=_current(before)), \
             patch("routers.event_sync_aliases.save_settings"), \
             patch("routers.event_sync_aliases.journal.log_entry") as log_entry:
            response = await async_client.put(
                "/api/event-sync/team-aliases", json=_VALID_BODY
            )

        assert response.status_code == 200
        log_entry.assert_called_once()
        kwargs = log_entry.call_args.kwargs
        assert kwargs["category"] == "event_sync"
        assert kwargs["action_type"] == "update"
        assert kwargs["before_value"] == {"groups": before}
        assert len(kwargs["after_value"]["groups"]) == 2

    @pytest.mark.asyncio
    async def test_clearing_the_dictionary_is_valid(self, async_client):
        with patch("routers.event_sync_aliases.get_settings", return_value=_current()), \
             patch("routers.event_sync_aliases.save_settings"), \
             patch("routers.event_sync_aliases.journal.log_entry"):
            response = await async_client.put(
                "/api/event-sync/team-aliases", json={"groups": []}
            )
        assert response.status_code == 200
        assert response.json() == {"groups": []}

    @pytest.mark.asyncio
    async def test_storage_failure_uses_sanitized_app_level_503(self):
        from main import app

        secret = "mcp-secret-that-must-not-escape"
        resolved_path = "/resolved/private/ecm-mcp/api-key"
        failure = MCPApiKeyStorageError(
            f"authority {resolved_path} contains {secret}"
        )
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        with patch(
            "routers.event_sync_aliases.get_settings", return_value=_current()
        ), patch(
            "routers.event_sync_aliases.save_settings", side_effect=failure
        ), patch("routers.event_sync_aliases.journal.log_entry"):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                response = await client.put(
                    "/api/event-sync/team-aliases", json=_VALID_BODY
                )

        assert response.status_code == 503
        assert response.json() == {
            "detail": {
                "code": "mcp_api_key_storage_unavailable",
                "message": (
                    "MCP credential storage is unavailable or untrusted. Repair "
                    "api-key and .api-key.recovery under MCP_SECRETS_DIR as "
                    "owner-only regular files (mode 0600, correct PUID/PGID, no "
                    "links), then retry. Preserve malformed recovery content; do "
                    "not guess, rewrite, or delete it."
                ),
                "operation": "settings save",
                "retry_after_storage_repair": True,
            }
        }
        assert secret not in response.text
        assert resolved_path not in response.text


class TestPutValidation:
    @pytest.mark.asyncio
    async def test_group_with_fewer_than_two_terms_is_rejected(self, async_client):
        response = await async_client.put(
            "/api/event-sync/team-aliases",
            json={"groups": [{"terms": ["Manchester United"]}]},
        )
        assert response.status_code == 400
        assert "at least 2" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_blank_term_is_rejected(self, async_client):
        response = await async_client.put(
            "/api/event-sync/team-aliases",
            json={"groups": [{"terms": ["Manchester United", "   "]}]},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_identity_free_term_is_rejected(self, async_client):
        # "FC" normalizes to no identity tokens — it could never match a
        # team side, so saving it would be silent dead weight.
        response = await async_client.put(
            "/api/event-sync/team-aliases",
            json={"groups": [{"terms": ["Manchester United", "FC"]}]},
        )
        assert response.status_code == 400
        assert "FC" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_duplicate_normalized_term_within_group_is_rejected(self, async_client):
        response = await async_client.put(
            "/api/event-sync/team-aliases",
            json={"groups": [{"terms": ["Man Utd", "man utd", "MUFC"]}]},
        )
        assert response.status_code == 400
        assert "duplicate" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_same_term_in_two_groups_is_rejected(self, async_client):
        # A term in two groups would make canonicalization ambiguous.
        response = await async_client.put(
            "/api/event-sync/team-aliases",
            json={"groups": [
                {"terms": ["Spurs", "Tottenham Hotspur"]},
                {"terms": ["Spurs", "San Antonio Spurs"]},
            ]},
        )
        assert response.status_code == 400
        assert "Spurs" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_group_cap_enforced(self, async_client):
        groups = [
            {"terms": [f"Team {i} Alpha", f"Team {i} Beta"]} for i in range(201)
        ]
        response = await async_client.put(
            "/api/event-sync/team-aliases", json={"groups": groups}
        )
        assert response.status_code == 400
        assert "200" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_term_length_cap_enforced(self, async_client):
        response = await async_client.put(
            "/api/event-sync/team-aliases",
            json={"groups": [{"terms": ["Manchester United", "x" * 101]}]},
        )
        # Pydantic max_length constraint → 422.
        assert response.status_code == 422


class TestSettingsPostPreservesDictionary:
    @pytest.mark.asyncio
    async def test_general_settings_post_does_not_clobber_aliases(self, async_client):
        """The general settings form never sends event_sync_team_aliases, and
        routers/settings.py rebuilds the whole model on POST — the field MUST
        be preserved from current settings or every settings save would wipe
        the operator's dictionary.
        """
        from unittest.mock import MagicMock

        stored = [{"terms": ["Spurs", "Tottenham Hotspur"], "note": None}]
        current = DispatcharrSettings(
            url="http://dispatcharr:8000",
            username="admin",
            password="secret",
            event_sync_team_aliases=stored,
        )
        captured = {}

        def capture_save(new_settings):
            captured["groups"] = new_settings.event_sync_team_aliases

        with patch("routers.settings.get_settings", return_value=current), \
             patch("routers.settings.save_settings", side_effect=capture_save), \
             patch("routers.settings.clear_settings_cache"), \
             patch("routers.settings.reset_client"), \
             patch("routers.settings.get_prober", return_value=None), \
             patch("routers.settings.get_cache") as mock_cache:
            mock_cache.return_value = MagicMock()
            response = await async_client.post("/api/settings", json={
                "url": current.url,
                "username": current.username,
            })

        assert response.status_code == 200, response.json()
        assert captured["groups"] == stored, (
            "Settings POST wiped event_sync_team_aliases — preserve-on-omit missing"
        )
