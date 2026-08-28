from __future__ import annotations

import json
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy.orm import sessionmaker

from auth import RequireHumanAdminForOperatorDecision
from auth.mcp_service import MCPServiceCredentials
from config import DispatcharrSettings
from models import JournalEntry, ProfileConflictReview, User
from services.profile_conflict_review import (
    canonical_conflict_evidence,
    conflict_fingerprint,
    InvalidChoice,
    now_epoch_ms,
    accept_profile_conflict_review,
)


@pytest.fixture(autouse=True)
def _human_admin_profile_review_routes():
    from main import app

    admin = User(
        id=42, username="profile-review-admin", is_admin=True, is_active=True,
        auth_provider="local",
    )
    app.dependency_overrides[RequireHumanAdminForOperatorDecision.dependency] = (
        lambda: admin
    )
    try:
        yield
    finally:
        app.dependency_overrides.pop(
            RequireHumanAdminForOperatorDecision.dependency, None
        )


def _shape(profile_b=None):
    return {
        "effective_group_id": 665,
        "source_group_ids": [823, 2866],
        "choices": [
            {"profile_ids": [6, 7], "sources": [{
                "source_group_id": 823, "m3u_account_id": 1,
                "m3u_account_name": "Stryker",
            }]},
            {"profile_ids": profile_b or [14], "sources": [{
                "source_group_id": 2866, "m3u_account_id": 2,
                "m3u_account_name": "Strong",
            }]},
        ],
    }


def _settings(shape=None):
    shape = shape or _shape()
    rows = {
        823: {
            "id": 10, "channel_group": 823, "source_group_id": 823,
            "m3u_account_id": 1, "m3u_account_name": "Stryker",
            "enabled": True, "auto_channel_sync": True,
            "custom_properties": {"group_override": 665, "channel_profile_ids": [6, 7], "keep": "a"},
        },
        2866: {
            "id": 20, "channel_group": 2866, "source_group_id": 2866,
            "m3u_account_id": 2, "m3u_account_name": "Strong",
            "enabled": True, "auto_channel_sync": True,
            "custom_properties": {"group_override": 665, "channel_profile_ids": [14], "keep": "b"},
        },
    }
    return {
        gid: {
            **row,
            "_ecm_channel_profile_conflict": True,
            "_ecm_profile_conflict_shape": shape,
            "_ecm_profile_source_rows": [row],
        }
        for gid, row in rows.items()
    }


def _review(test_session, shape=None):
    shape = shape or _shape()
    evidence = canonical_conflict_evidence(
        shape, {665: "NBA", 823: "Events", 2866: "Strong Events"},
        {6: "Sports", 7: "Family", 14: "Strong only", 15: "Changed"},
    )
    row = ProfileConflictReview(
        fingerprint=conflict_fingerprint(evidence), fingerprint_version=1,
        effective_group_id=665, status="pending", evidence=json.dumps(evidence),
        created_at=now_epoch_ms(), last_seen_at=now_epoch_ms(),
    )
    test_session.add(row)
    test_session.commit()
    return row, evidence


def _client(settings):
    client = AsyncMock()
    client.get_all_m3u_group_settings.return_value = settings
    client.get_channel_groups.return_value = [
        {"id": 665, "name": "NBA"}, {"id": 823, "name": "Events"},
        {"id": 2866, "name": "Strong Events"},
    ]
    client.get_channel_profiles.return_value = [
        {"id": 6, "name": "Sports"}, {"id": 7, "name": "Family"},
        {"id": 14, "name": "Strong only"}, {"id": 15, "name": "Changed"},
    ]
    return client


@pytest.mark.asyncio
async def test_list_returns_pending_evidence(async_client, test_session):
    row, _ = _review(test_session)
    response = await async_client.get("/api/profile-conflict-reviews")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["reviews"][0]["id"] == row.id
    assert body["reviews"][0]["evidence"]["target"]["name"] == "NBA"


@pytest.mark.asyncio
async def test_list_normalizes_malformed_stored_evidence(async_client, test_session):
    row = ProfileConflictReview(
        fingerprint="malformed", fingerprint_version=1,
        effective_group_id=665, status="pending", evidence="not-json",
        accepted_profile_ids="also-not-json", created_at=1, last_seen_at=1,
    )
    test_session.add(row)
    test_session.commit()

    response = await async_client.get("/api/profile-conflict-reviews")

    assert response.status_code == 200
    record = response.json()["reviews"][0]
    assert record["evidence"] == {
        "fingerprint_version": 1,
        "target": {"effective_group_id": 665, "name": "Group 665"},
        "choices": [],
    }
    assert record["accepted_profile_ids"] is None


def _request_with_token(token: str | None = None) -> Request:
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
    return Request({
        "type": "http", "method": "GET", "path": "/api/profile-conflict-reviews",
        "query_string": b"", "headers": headers,
    })


@pytest.mark.asyncio
async def test_operator_decision_gate_enforces_owned_auth_disabled_instance(test_session):
    test_session.add(User(
        username="owner", is_admin=True, is_active=True, auth_provider="local",
    ))
    test_session.commit()
    auth_off = type("Auth", (), {"require_auth": False, "setup_complete": True})()
    dependency = RequireHumanAdminForOperatorDecision.dependency

    with patch("auth.dependencies.get_auth_settings", return_value=auth_off):
        with pytest.raises(HTTPException) as anonymous:
            await dependency(_request_with_token(), test_session)

    assert anonymous.value.status_code == 401


@pytest.mark.asyncio
async def test_operator_decision_gate_requires_login_on_headless_auth_disabled_instance(
    test_session,
):
    auth_off = type("Auth", (), {"require_auth": False, "setup_complete": False})()
    dependency = RequireHumanAdminForOperatorDecision.dependency

    with patch("auth.dependencies.get_auth_settings", return_value=auth_off):
        with pytest.raises(HTTPException) as anonymous:
            await dependency(_request_with_token(), test_session)

    assert anonymous.value.status_code == 401


@pytest.mark.parametrize("token", ["public-mcp", "private-mcp"])
@pytest.mark.asyncio
async def test_operator_decision_gate_rejects_both_mcp_credentials_when_auth_disabled(
    test_session, token,
):
    test_session.add(User(
        username="owner", is_admin=True, is_active=True, auth_provider="local",
    ))
    test_session.commit()
    auth_off = type("Auth", (), {"require_auth": False, "setup_complete": True})()
    runtime = DispatcharrSettings(
        url="http://dispatcharr:8000", username="u", password="p",
        mcp_api_key="public-mcp",
    )
    credentials = MCPServiceCredentials(
        backend_key="private-mcp", confirmation_key="confirm",
    )
    dependency = RequireHumanAdminForOperatorDecision.dependency

    with patch("auth.dependencies.get_auth_settings", return_value=auth_off), \
         patch("auth.dependencies.get_settings", return_value=runtime), \
         patch("auth.dependencies.load_mcp_service_credentials", return_value=credentials):
        with pytest.raises(HTTPException) as denied:
            await dependency(_request_with_token(token), test_session)

    assert denied.value.status_code == 403
    assert "MCP service principal" in denied.value.detail


@pytest.mark.asyncio
async def test_operator_decision_gate_allows_human_admin_when_auth_disabled(test_session):
    test_session.add(User(
        username="owner", is_admin=True, is_active=True, auth_provider="local",
    ))
    test_session.commit()
    auth_off = type("Auth", (), {"require_auth": False, "setup_complete": True})()
    admin = User(
        id=77, username="signed-in", is_admin=True, is_active=True,
        auth_provider="local",
    )
    dependency = RequireHumanAdminForOperatorDecision.dependency

    with patch("auth.dependencies.get_auth_settings", return_value=auth_off), \
         patch("auth.dependencies.get_current_user", new=AsyncMock(return_value=admin)):
        assert await dependency(_request_with_token("jwt"), test_session) is admin


@pytest.mark.asyncio
async def test_accept_persists_before_remote_writes_journals_and_never_writes_membership(
    async_client, test_session,
):
    row, evidence = _review(test_session)
    client = _client(_settings())

    async def assert_decision_first(_account_id, _payload):
        test_session.expire_all()
        persisted = test_session.get(ProfileConflictReview, row.id)
        assert persisted.status == "accepted"
        assert persisted.accepted_choice_key == evidence["choices"][0]["choice_key"]
        return {"ok": True}

    client.update_m3u_group_settings.side_effect = assert_decision_first
    with patch("routers.profile_conflict_reviews.get_client", return_value=client):
        response = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": evidence["choices"][0]["choice_key"]},
        )

    assert response.status_code == 200
    assert response.json()["applied"] is True
    assert client.update_m3u_group_settings.await_count == 1
    assert client.bulk_update_profile_channels.await_count == 0
    assert test_session.query(JournalEntry).filter(
        JournalEntry.action_type == "profile_conflict_accept"
    ).count() == 1


@pytest.mark.asyncio
async def test_accept_stale_shape_returns_409_without_any_remote_write(async_client, test_session):
    row, evidence = _review(test_session)
    client = _client(_settings(_shape([15])))
    with patch("routers.profile_conflict_reviews.get_client", return_value=client):
        response = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": evidence["choices"][0]["choice_key"]},
        )

    assert response.status_code == 409
    client.update_m3u_group_settings.assert_not_awaited()
    assert test_session.get(ProfileConflictReview, row.id).status == "pending"


@pytest.mark.asyncio
async def test_accept_partial_account_failure_is_durable_and_retryable(async_client, test_session):
    row, evidence = _review(test_session)
    client = _client(_settings())
    client.update_m3u_group_settings.side_effect = [RuntimeError("down"), {"ok": True}]
    with patch("routers.profile_conflict_reviews.get_client", return_value=client):
        response = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": evidence["choices"][0]["choice_key"]},
        )

    assert response.status_code == 200
    assert response.json()["applied"] is False
    assert response.json()["failed_account_ids"] == [2]
    test_session.expire_all()
    persisted = test_session.get(ProfileConflictReview, row.id)
    assert persisted.status == "accepted"
    assert persisted.applied_at is None
    assert "account 2" in persisted.retry_error
    queued = await async_client.get("/api/profile-conflict-reviews")
    assert queued.status_code == 200
    assert queued.json()["reviews"][0]["id"] == row.id
    assert queued.json()["reviews"][0]["status"] == "accepted"


@pytest.mark.asyncio
async def test_retry_accepts_compatible_shape_created_by_the_partial_write(
    async_client, test_session,
):
    row, evidence = _review(test_session)
    row.status = "accepted"
    row.accepted_choice_key = evidence["choices"][0]["choice_key"]
    row.accepted_profile_ids = json.dumps(evidence["choices"][0]["profile_ids"])
    row.retry_error = "account 1: down"
    test_session.commit()
    changed = _shape(evidence["choices"][0]["profile_ids"])
    client = _client(_settings(changed))

    with patch("routers.profile_conflict_reviews.get_client", return_value=client):
        response = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": row.accepted_choice_key},
        )

    assert response.status_code == 200
    assert response.json()["applied"] is True
    assert client.update_m3u_group_settings.await_count == 1


@pytest.mark.asyncio
async def test_audit_failure_keeps_applied_review_retryable(async_client, test_session):
    row, evidence = _review(test_session)
    client = _client(_settings())
    selected = evidence["choices"][0]["choice_key"]
    with patch("routers.profile_conflict_reviews.get_client", return_value=client), \
         patch("services.profile_conflict_review.journal.log_entry", side_effect=[None, object(), object()]) as log:
        first = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": selected},
        )
        second = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": selected},
        )

    assert first.status_code == 200
    assert first.json()["applied"] is False
    assert "audit" in first.json()["retry_error"].lower()
    assert second.status_code == 200
    assert second.json()["applied"] is True
    assert [call.kwargs["action_type"] for call in log.call_args_list] == [
        "profile_conflict_accept",
        "profile_conflict_accept",
        "profile_conflict_retry",
    ]


@pytest.mark.asyncio
async def test_delayed_accept_audit_keeps_the_first_actor(test_session):
    row, evidence = _review(test_session)
    client = _client(_settings())
    selected = evidence["choices"][0]["choice_key"]

    with patch(
        "services.profile_conflict_review.journal.log_entry",
        side_effect=[None, object(), object()],
    ) as log:
        first = await accept_profile_conflict_review(
            test_session, client, row.id, selected, "first-actor"
        )
        second = await accept_profile_conflict_review(
            test_session, client, row.id, selected, "retry-actor"
        )

    assert first["applied"] is False
    assert second["applied"] is True
    assert log.call_args_list[1].kwargs["after_value"]["actor_token_id"] == "first-actor"
    assert log.call_args_list[2].kwargs["after_value"]["actor_token_id"] == "retry-actor"


@pytest.mark.asyncio
async def test_accept_journal_records_named_provenance(async_client, test_session):
    row, evidence = _review(test_session)
    client = _client(_settings())
    with patch("routers.profile_conflict_reviews.get_client", return_value=client):
        response = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": evidence["choices"][0]["choice_key"]},
        )

    assert response.status_code == 200
    entry = test_session.query(JournalEntry).filter_by(
        action_type="profile_conflict_accept"
    ).one()
    after = json.loads(entry.after_value)
    assert after["target"] == {"effective_group_id": 665, "name": "NBA"}
    assert after["selected_choice"]["profile_names"] == ["Sports", "Family"]
    assert after["selected_choice"]["sources"][0]["m3u_account_name"] == "Stryker"


@pytest.mark.asyncio
async def test_waiting_accept_refetches_review_after_group_lock(test_engine, test_session):
    row, evidence = _review(test_session)
    first_client = _client(_settings())
    second_client = _client(_settings())
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_first_settings():
        entered.set()
        await release.wait()
        return _settings()

    first_client.get_all_m3u_group_settings.side_effect = hold_first_settings
    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)
    first_db = SessionLocal()
    second_db = SessionLocal()
    first_choice = evidence["choices"][0]["choice_key"]
    second_choice = evidence["choices"][1]["choice_key"]
    try:
        first = asyncio.create_task(accept_profile_conflict_review(
            first_db, first_client, row.id, first_choice, "1"
        ))
        await entered.wait()
        second = asyncio.create_task(accept_profile_conflict_review(
            second_db, second_client, row.id, second_choice, "2"
        ))
        await asyncio.sleep(0)
        release.set()
        await first
        with pytest.raises(InvalidChoice):
            await second
    finally:
        first_db.close()
        second_db.close()

    test_session.expire_all()
    persisted = test_session.get(ProfileConflictReview, row.id)
    assert persisted.accepted_choice_key == first_choice
    second_client.update_m3u_group_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_accept_retires_a_conflict_that_disappeared(
    async_client, test_session,
):
    row, evidence = _review(test_session)
    settings = _settings()
    for setting in settings.values():
        setting.pop("_ecm_profile_conflict_shape", None)
    client = _client(settings)

    with patch("routers.profile_conflict_reviews.get_client", return_value=client):
        response = await async_client.post(
            f"/api/profile-conflict-reviews/{row.id}/accept",
            json={"choice_key": evidence["choices"][0]["choice_key"]},
        )

    assert response.status_code == 409
    test_session.expire_all()
    assert test_session.get(ProfileConflictReview, row.id).status == "superseded"
    queue = await async_client.get("/api/profile-conflict-reviews")
    assert queue.json()["total"] == 0
