"""Shared UI/MCP view exclusions: real SQLite, no discarded change history."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from models import M3UChangeLog
from m3u_change_detector import M3UChangeDetector

pytestmark = pytest.mark.asyncio
PATH = "/api/m3u/changes/exclusions"


def group(account=1, name="Sports"):
    return {"m3u_account_id": account, "group_name": name}


def seed(session, account=1, name="Sports", kind="streams_added", count=3):
    session.add(M3UChangeLog(m3u_account_id=account, group_name=name,
                            change_type=kind, count=count, enabled=True,
                            change_time=datetime.utcnow()))
    session.commit()


async def test_account_scoped_exclusions_filter_before_pagination_and_summary(async_client, test_session):
    seed(test_session)
    seed(test_session, name="News", count=2)
    seed(test_session, account=2, count=7)
    response = await async_client.patch(PATH, json={"add": [group()]})
    assert response.status_code == 200, response.text
    assert response.json()["exclusions"] == [group()]
    rows = (await async_client.get("/api/m3u/changes?page_size=1")).json()
    assert rows["total"] == 2 and rows["total_pages"] == 2
    assert all((r["m3u_account_id"], r["group_name"]) != (1, "Sports") for r in rows["results"])
    summary = (await async_client.get("/api/m3u/changes/summary")).json()
    assert summary["total_changes"] == 2 and summary["streams_added"] == 9
    raw_summary = (await async_client.get("/api/m3u/changes/summary?include_excluded=true")).json()
    assert raw_summary["total_changes"] == 3 and raw_summary["streams_added"] == 12
    # Existing digest callers explicitly retain the complete history.
    assert M3UChangeDetector(test_session).get_change_summary(datetime.utcnow()-timedelta(days=1))["total_changes"] == 3
    assert test_session.query(M3UChangeLog).count() == 3


async def test_idempotent_delta_updates_restore_history_without_lost_other_groups(async_client, test_session):
    seed(test_session)
    assert (await async_client.get(PATH)).json() == {"exclusions": []}
    for _ in range(2):
        assert (await async_client.patch(PATH, json={"add": [group()]})).status_code == 200
    await async_client.patch(PATH, json={"add": [group(2, "Movies")]})
    result = await async_client.patch(PATH, json={"remove": [group()]})
    assert result.json()["exclusions"] == [group(2, "Movies")]
    assert (await async_client.get("/api/m3u/changes")).json()["total"] == 1
    assert (await async_client.patch(PATH, json={"clear": True})).json() == {"exclusions": []}


async def test_group_options_include_removed_saved_and_recorded_unicode_groups(async_client, test_session):
    recorded = json.loads((Path(__file__).parents[1]/"fixtures/event_sync/m3u_snapshot_groups_data_capped.json").read_text())
    for row in recorded["groups"]:
        seed(test_session, name=row["name"])
    await async_client.patch(PATH, json={"add": [group(2, "Removed group")]})
    response = await async_client.get("/api/m3u/changes/groups?page_size=2")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(recorded["groups"]) + 1
    assert len(body["results"]) == 2
    saved = (await async_client.get("/api/m3u/changes/groups?m3u_account_id=2")).json()["results"]
    assert saved == [group(2, "Removed group")]


@pytest.mark.parametrize("body", [
    {"add": [group(0)]}, {"add": [group(True)]}, {"add": [group(2**63)]}, {"add": [group(name="")]},
    {"add": [group(name="x"*256)]}, {"add": [group()], "remove": [group()]},
    {"clear": True, "add": [group()]},
])
async def test_invalid_mutations_are_atomic(async_client, body):
    response = await async_client.patch(PATH, json=body)
    assert response.status_code == 422, response.text
    assert (await async_client.get(PATH)).json() == {"exclusions": []}


async def test_exact_names_are_not_regex_or_cross_provider_rules(async_client, test_session):
    for name in ["Sports.*", "Sports", "sports", None]:
        seed(test_session, name=name)
    await async_client.patch(PATH, json={"add": [group(name="Sports.*")]})
    response = await async_client.get("/api/m3u/changes")
    assert response.json()["total"] == 3
    unfiltered = await async_client.get("/api/m3u/changes?include_excluded=true")
    assert unfiltered.json()["total"] == 4


@pytest.mark.parametrize("admin,status", [(False, 403), (True, 200)])
async def test_mutations_require_admin_when_auth_enabled(async_client, admin, status):
    from models import User
    with patch("auth.dependencies.get_auth_settings") as auth, patch(
        "auth.dependencies.get_current_user",
        new=AsyncMock(return_value=User(id=123, username="operator", is_admin=admin, is_active=True)),
    ):
        auth.return_value.require_auth = True
        auth.return_value.setup_complete = True
        response = await async_client.patch(PATH, json={"add": [group()]})
    assert response.status_code == status
    if not admin:
        assert (await async_client.get(PATH)).json() == {"exclusions": []}


async def test_mcp_principal_can_manage_shared_exclusions(async_client):
    from config import DispatcharrSettings
    settings = DispatcharrSettings(url="http://dispatcharr:8000", mcp_api_key="<test-mcp-key>")
    with patch("auth.dependencies.get_auth_settings") as auth, patch(
        "auth.dependencies.get_settings", return_value=settings,
    ):
        auth.return_value.require_auth = True
        auth.return_value.setup_complete = True
        response = await async_client.patch(PATH, json={"add": [group()]},
                                            headers={"Authorization": "Bearer <test-mcp-key>"})
    assert response.status_code == 200, response.text
    assert (await async_client.get(PATH)).json()["exclusions"] == [group()]


async def test_search_is_literal_and_saved_groups_survive_history_cleanup(async_client, test_session):
    seed(test_session, name="Sports%_")
    seed(test_session, name="Sports other")
    await async_client.patch(PATH, json={"add": [group(name="Sports%_")]})
    test_session.query(M3UChangeLog).delete()
    test_session.commit()
    response = await async_client.get("/api/m3u/changes/groups", params={"search": "%_"})
    assert response.json()["results"] == [group(name="Sports%_")]


async def test_batch_bounds_and_unknown_fields_reject_without_changes(async_client):
    for body in [{"add": [group()] * 1001}, {"replace": [group()]}, {"add": [{"m3u_account_id": 1, "group_name": "Sports", "extra": 1}]}]:
        assert (await async_client.patch(PATH, json=body)).status_code == 422
    assert (await async_client.get(PATH)).json() == {"exclusions": []}
