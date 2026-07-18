"""
Tests for per-account M3U digest notification filtering (GH #496).

An operator running a high-churn "FAST" provider (10k+ stream URL changes/
hour) alongside slow-changing standard providers wants that provider's
changes excluded from digest NOTIFICATIONS while M3UChangeLog keeps logging
every account's changes unabridged. ``M3UDigestSettings.account_ids`` scopes
which accounts' changes the digest task builds into email/Discord content;
an empty/unset list means "all accounts" (unchanged default behavior).

Covers:
  1. Model getter/setter/to_dict round-trip for account_ids.
  2. _build_digest_payload / execute(force=True) respects account_ids when
     no explicit single-account filter is requested.
  3. Empty account_ids == all accounts (no filtering).
  4. An explicit single m3u_account_id (test send / immediate digest) takes
     precedence over the account_ids scope.
  5. send_immediate_digest() skips sending — without calling execute() at
     all — when the refreshed account isn't in a non-empty account_ids
     selection, and proceeds normally otherwise.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from models import M3UChangeLog, M3UDigestSettings
from task_scheduler import TaskResult


class _NoCloseSession:
    """Wrap the test session so the task's get_session().close() is a no-op.

    The fixture keeps ownership of cleanup; the task opening/closing its own
    session must not close the shared StaticPool connection underneath it.
    """

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def close(self):
        pass


def _make_enabled_settings(session, **overrides):
    kwargs = dict(
        enabled=True,
        frequency="weekly",
        include_group_changes=True,
        include_stream_changes=True,
        show_detailed_list=True,
        min_changes_threshold=1,
    )
    kwargs.update(overrides)
    settings = M3UDigestSettings(**kwargs)
    settings.set_email_recipients(["ops@example.com"])
    session.add(settings)
    session.commit()
    return settings


def _add_change(session, account_id, i):
    base = datetime.utcnow() - timedelta(hours=1)
    row = M3UChangeLog(
        m3u_account_id=account_id,
        change_time=base + timedelta(seconds=i),
        change_type="streams_added",
        group_name=f"Group {account_id}-{i}",
        count=1,
    )
    row.set_stream_names([f"Stream {account_id}-{i}"])
    session.add(row)
    session.commit()
    return row


# ---------------------------------------------------------------------------
# M3UDigestSettings.account_ids getter/setter/to_dict
# ---------------------------------------------------------------------------

class TestDigestSettingsAccountIds:
    def test_get_empty_when_unset(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        test_session.add(settings)
        test_session.commit()

        assert settings.get_account_ids() == []
        assert settings.to_dict()["account_ids"] == []

    def test_set_and_get_round_trip(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        test_session.add(settings)
        test_session.commit()

        settings.set_account_ids([3, 1, 2])
        test_session.commit()

        assert settings.get_account_ids() == [3, 1, 2]
        assert settings.to_dict()["account_ids"] == [3, 1, 2]

    def test_set_empty_list_stores_null(self, test_session):
        settings = M3UDigestSettings(enabled=False, frequency="daily")
        settings.set_account_ids([1, 2])
        test_session.add(settings)
        test_session.commit()

        settings.set_account_ids([])
        test_session.commit()

        assert settings.account_ids is None
        assert settings.get_account_ids() == []


# ---------------------------------------------------------------------------
# _build_digest_payload / execute() account scoping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scheduled_digest_filters_to_selected_accounts(test_session):
    """A non-empty account_ids selection scopes the built digest to those
    accounts only, when no explicit single-account filter is requested."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask

    _make_enabled_settings(test_session, account_ids='[1]')
    _add_change(test_session, 1, 0)  # included
    _add_change(test_session, 1, 1)  # included
    _add_change(test_session, 2, 0)  # excluded (fast provider)

    with patch.object(digest_mod, "get_session", return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "_send_digest_email", new=AsyncMock(return_value=True)):
        task = M3UDigestTask()
        result = await task.execute(force=True)

    assert result.success is True
    assert result.total_items == 2, (
        f"expected only account 1's 2 changes, got {result.total_items}"
    )


@pytest.mark.asyncio
async def test_scheduled_digest_empty_account_ids_includes_all_accounts(test_session):
    """Empty/unset account_ids is the pre-existing 'all accounts' behavior."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask

    _make_enabled_settings(test_session)  # account_ids left unset
    _add_change(test_session, 1, 0)
    _add_change(test_session, 2, 0)

    with patch.object(digest_mod, "get_session", return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "_send_digest_email", new=AsyncMock(return_value=True)):
        task = M3UDigestTask()
        result = await task.execute(force=True)

    assert result.success is True
    assert result.total_items == 2


@pytest.mark.asyncio
async def test_explicit_account_id_overrides_account_ids_scope(test_session):
    """A caller-supplied m3u_account_id (test send / immediate digest for
    one account) takes precedence over the account_ids scope, matching the
    existing single-account filter semantics."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask

    # Selection excludes account 1, but the caller explicitly asks for
    # account 1's digest (e.g. immediate digest right after its refresh).
    _make_enabled_settings(test_session, account_ids='[2]')
    _add_change(test_session, 1, 0)
    _add_change(test_session, 2, 0)

    with patch.object(digest_mod, "get_session", return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "_send_digest_email", new=AsyncMock(return_value=True)):
        task = M3UDigestTask()
        result = await task.execute(force=True, m3u_account_id=1)

    assert result.success is True
    assert result.total_items == 1


# ---------------------------------------------------------------------------
# send_immediate_digest() skip-for-excluded-account behavior
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_immediate_digest_skips_excluded_account(test_session):
    """When account_ids is non-empty and the refreshed account isn't in it,
    send_immediate_digest must skip WITHOUT invoking execute() at all."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask, send_immediate_digest

    _make_enabled_settings(test_session, frequency="immediate", account_ids='[1, 2]')

    with patch.object(digest_mod, "get_session", return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "execute", new=AsyncMock()) as mock_execute:
        result = await send_immediate_digest(m3u_account_id=99)

    assert result.success is True
    assert "skip" in result.message.lower()
    mock_execute.assert_not_called()


@pytest.mark.asyncio
async def test_send_immediate_digest_proceeds_for_included_account(test_session):
    """When the refreshed account IS in a non-empty selection, the digest
    proceeds normally."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask, send_immediate_digest

    _make_enabled_settings(test_session, frequency="immediate", account_ids='[1, 2]')

    expected = TaskResult(
        success=True, message="ok",
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
    )
    with patch.object(digest_mod, "get_session", return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "execute", new=AsyncMock(return_value=expected)) as mock_execute:
        result = await send_immediate_digest(m3u_account_id=1)

    assert result is expected
    mock_execute.assert_awaited_once_with(m3u_account_id=1)


@pytest.mark.asyncio
async def test_send_immediate_digest_proceeds_when_no_selection(test_session):
    """Empty/unset account_ids ('all accounts') never skips."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask, send_immediate_digest

    _make_enabled_settings(test_session, frequency="immediate")  # account_ids unset

    expected = TaskResult(
        success=True, message="ok",
        started_at=datetime.utcnow(), completed_at=datetime.utcnow(),
    )
    with patch.object(digest_mod, "get_session", return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "execute", new=AsyncMock(return_value=expected)) as mock_execute:
        result = await send_immediate_digest(m3u_account_id=42)

    assert result is expected
    mock_execute.assert_awaited_once_with(m3u_account_id=42)
