"""Write-time API enforcement of event_sync_config (bead ti939.1.3).

POST /rules and PUT /rules/{id} must reject invalid event_sync configs with
teaching errors (400), persist valid ones with defaults filled, support the
explicit-null clear (model_fields_set convention), and keep the kind on
duplication. Bulk-update must refuse the field outright (bd-gjoe5 pattern)
instead of silently dropping it.
"""
import pytest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    with patch("routers.channel_pipeline.get_session") as mock:
        session = MagicMock()
        mock.return_value = session
        yield session


@pytest.fixture
def test_client():
    """Create test client with the admin gate bypassed (auth-off passthrough).

    Same pattern as tests/integration/test_api_channel_pipeline.py — see the
    rationale there.
    """
    from main import app
    from auth import RequireAdminIfEnabled as _prebuilt

    async def _allow_no_auth():
        return None

    app.dependency_overrides[_prebuilt.dependency] = _allow_no_auth
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(_prebuilt.dependency, None)


def _rule_body(event_sync_config=None) -> dict:
    body = {
        "name": "Event Rule",
        "conditions": [{"type": "always"}],
        "actions": [{"type": "skip"}],
    }
    if event_sync_config is not None:
        body["event_sync_config"] = event_sync_config
    return body


def _valid_config(**overrides) -> dict:
    config = {
        "master_group_id": 10,
        "secondary_group_ids": [20, 30],
    }
    config.update(overrides)
    return config


class TestCreateRuleEventSyncConfig:
    def test_create_without_config_unchanged(self, test_client, mock_db_session):
        """Backward compat: a standard rule create is untouched."""
        added = []
        mock_db_session.add = added.append
        with patch("routers.channel_pipeline.journal.log_entry"):
            response = test_client.post(
                "/api/auto-creation/rules", json=_rule_body()
            )
        assert response.status_code == 200
        assert added[0].event_sync_config is None

    def test_create_with_valid_config_persists_with_defaults(
        self, test_client, mock_db_session
    ):
        added = []
        mock_db_session.add = added.append
        with patch("routers.channel_pipeline.journal.log_entry"):
            response = test_client.post(
                "/api/auto-creation/rules",
                json=_rule_body(event_sync_config=_valid_config()),
            )
        assert response.status_code == 200
        stored = added[0].get_event_sync_config()
        assert stored["master_group_id"] == 10
        assert stored["secondary_group_ids"] == [20, 30]
        # Defaults filled at validation time so the stored JSON is explicit.
        assert stored["time_window_minutes"] == 30
        assert stored["attach_threshold"] == 0.80
        assert stored["enabled"] is True

    def test_create_with_missing_master_rejected_with_teaching_error(
        self, test_client, mock_db_session
    ):
        config = _valid_config()
        del config["master_group_id"]
        response = test_client.post(
            "/api/auto-creation/rules",
            json=_rule_body(event_sync_config=config),
        )
        assert response.status_code == 400
        detail = response.json()["detail"]
        assert detail["message"] == "Invalid rule configuration"
        assert any(
            "master_group_id" in e and "docs/event_sync.md" in e
            for e in detail["errors"]
        )

    def test_create_with_master_in_secondaries_rejected(
        self, test_client, mock_db_session
    ):
        """The mandatory-scoping rail is enforced at the API boundary."""
        response = test_client.post(
            "/api/auto-creation/rules",
            json=_rule_body(
                event_sync_config=_valid_config(secondary_group_ids=[10, 20])
            ),
        )
        assert response.status_code == 400

    def test_create_with_below_default_threshold_accepted(
        self, test_client, mock_db_session
    ):
        # bead krkm4-sibling: 0.80 is the default, not a hard floor. A rule
        # may set a lower operator-authoritative threshold through the API.
        response = test_client.post(
            "/api/auto-creation/rules",
            json=_rule_body(
                event_sync_config=_valid_config(attach_threshold=0.5)
            ),
        )
        assert response.status_code in (200, 201)

    def test_create_with_out_of_bounds_threshold_rejected(
        self, test_client, mock_db_session
    ):
        response = test_client.post(
            "/api/auto-creation/rules",
            json=_rule_body(
                event_sync_config=_valid_config(attach_threshold=1.5)
            ),
        )
        assert response.status_code == 400
        assert any(
            "attach_threshold" in e
            for e in response.json()["detail"]["errors"]
        )


class TestUpdateRuleEventSyncConfig:
    def _mock_rule(self, mock_db_session) -> MagicMock:
        rule = MagicMock()
        rule.id = 1
        rule.name = "Rule"
        rule.get_conditions.return_value = [{"type": "always"}]
        rule.get_actions.return_value = [{"type": "skip"}]
        rule.to_dict.return_value = {"id": 1, "name": "Rule"}
        mock_db_session.query.return_value.filter.return_value.first.return_value = rule
        return rule

    def test_update_with_valid_config_sets_it(self, test_client, mock_db_session):
        rule = self._mock_rule(mock_db_session)
        with patch("routers.channel_pipeline.journal.log_entry"):
            response = test_client.put(
                "/api/auto-creation/rules/1",
                json={"event_sync_config": _valid_config()},
            )
        assert response.status_code == 200
        rule.set_event_sync_config.assert_called_once()
        stored = rule.set_event_sync_config.call_args.args[0]
        assert stored["master_group_id"] == 10
        assert stored["attach_threshold"] == 0.80  # default filled

    def test_update_with_invalid_config_rejected(self, test_client, mock_db_session):
        rule = self._mock_rule(mock_db_session)
        response = test_client.put(
            "/api/auto-creation/rules/1",
            json={"event_sync_config": {"master_group_id": 10,
                                        "secondary_group_ids": []}},
        )
        assert response.status_code == 400
        rule.set_event_sync_config.assert_not_called()

    def test_update_with_explicit_null_clears_config(
        self, test_client, mock_db_session
    ):
        """Explicit null reverts the rule to the standard kind
        (model_fields_set convention, like match_scope_group_id)."""
        rule = self._mock_rule(mock_db_session)
        with patch("routers.channel_pipeline.journal.log_entry"):
            response = test_client.put(
                "/api/auto-creation/rules/1",
                json={"event_sync_config": None},
            )
        assert response.status_code == 200
        rule.set_event_sync_config.assert_called_once_with(None)

    def test_update_without_field_leaves_config_untouched(
        self, test_client, mock_db_session
    ):
        """Delta-on-write: a rename must not touch (or re-validate) the
        stored config."""
        rule = self._mock_rule(mock_db_session)
        with patch("routers.channel_pipeline.journal.log_entry"):
            response = test_client.put(
                "/api/auto-creation/rules/1",
                json={"name": "Renamed"},
            )
        assert response.status_code == 200
        rule.set_event_sync_config.assert_not_called()


class TestBulkUpdateRejectsEventSyncConfig:
    def test_bulk_update_with_event_sync_config_rejected(
        self, test_client, mock_db_session
    ):
        """bd-gjoe5 pattern: rule logic is not bulk-updatable — refuse rather
        than silently drop."""
        response = test_client.post(
            "/api/auto-creation/rules/bulk-update",
            json={
                "rule_ids": [1, 2],
                "event_sync_config": _valid_config(),
            },
        )
        assert response.status_code == 422
        assert "event_sync_config" in response.text


class TestDuplicateKeepsKind:
    def test_duplicate_copies_event_sync_config(self, test_client, mock_db_session):
        """Dropping the config on duplicate would silently turn the copy into
        a standard rule that executes in the pipeline."""
        source = MagicMock()
        source.id = 1
        source.name = "Event Rule"
        source.priority = 0
        source.event_sync_config = '{"master_group_id": 10, "secondary_group_ids": [20]}'
        source.required_provider_ids = None
        mock_db_session.query.return_value.filter.return_value.first.return_value = source

        added = []
        mock_db_session.add = added.append

        response = test_client.post("/api/auto-creation/rules/1/duplicate")
        assert response.status_code == 200
        assert added[0].event_sync_config == source.event_sync_config
