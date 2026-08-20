"""The sync preview must have READ B before it describes B (bead ``…-jqfxm``).

THE DEFECT this file pins
-------------------------
With a deliberately WRONG PASSWORD on the sync target, the dry-run preview
reported ``outcome=success, would create 24, failed 0`` and UNLOCKED Apply,
while destination B logged seven ``401``/``429`` responses on
``/api/accounts/token/``. Seven, because the config step registry makes exactly
seven destination reads and EVERY importer degrades a failed read to
``except Exception: existing = []`` — "B is empty" — so every source entity
looked like a fresh create. ``would create 24`` was a statement about SOURCE A
wearing B's clothes.

The credential-freshness abort (bead ``7ipq2.2``) surfaces correctly because it
is a LOCAL DB check (``sync_freshness_reason`` reads the ``SyncTarget`` row) that
runs BEFORE a client exists. Nothing anywhere asked B a question and checked the
answer.

THE INVARIANT (not the reproduction)
------------------------------------
No preview reports success unless it actually READ the destination it claims to
describe. A wrong password is ONE example; a preview blocked by a network
failure, TLS refusal, SSRF policy, DNS failure, a 5xx, or a rate limit fails the
same way — and a preview that did not read B must not unlock Apply.

WHICH STRUCTURE THESE TESTS ASSERT ON
-------------------------------------
This subsystem records conditions three ways (``skip_details``/``SkipReason``,
``failure_details``/``FailureReason``, and top-level ``int`` counters on
:class:`RestoreReport`), and asserting on the wrong one yields a test that
passes against broken code. These tests assert on NEITHER of the three: they
assert on

* :attr:`RestoreReport.destination_unreadable` — the new top-level
  ``str | None`` this bead adds, whose PRESENCE is the "I never read B" fact; and
* ``TaskResult.success`` / ``TaskResult.error`` — the contract the Settings card
  gates the Apply button on (``SyncTargetsCard.tsx`` adds the target to
  ``previewedIds`` only when ``result.success`` is true).

Counting a category ``failed`` would NOT do: a dry-run's ``cat.failed`` legally
carries source-side CONFLICTs (duplicate names, ambiguous null channel numbers)
that are facts about A and do not mean B went unread.

RATE LIMIT vs AUTH FAILURE
--------------------------
B's Dispatcharr rate-limits ``/api/accounts/token/`` at 3/min per IP, so
back-to-back sync cycles produce ``429`` — which is NOT a wrong password.
Conflating the two would be its own defect, so both are pinned separately.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dbas.restore_contracts import EntityType, RestoreReport
from routers import backup as backup_mod
from security.ssrf import SSRFError
from tasks import dbas_sync_engine as engine
from tasks.dbas_sync_engine import run_sync
from tests.tasks.test_dbas_sync_engine import (
    _empty_dest_client,
    _source_client,
    _sync_target,
)
# Re-exported so pytest can resolve it as a fixture in THIS module (fixtures are
# module-scoped names, not globals) — the same in-memory-SQLite wiring the sync
# task's own tests use, so `get_session()` / journal / notifications land in the
# test DB rather than a real one.
from tests.tasks.test_dbas_sync_task import _wire_db  # noqa: F401


# ---------------------------------------------------------------------------
# Destination clients that REFUSE the way Dispatcharr actually refuses.
# A permissive mock goes green against the broken code, so every fixture here
# raises out of the SAME `get_*` methods the importers call.
# ---------------------------------------------------------------------------


def _http_error(status: int) -> httpx.HTTPStatusError:
    """The exception ``DispatcharrClient`` really raises for an HTTP failure.

    ``_login`` and every ``get_*`` end in ``response.raise_for_status()``, so a
    wrong password surfaces as ``HTTPStatusError`` carrying a 401 response —
    not a bespoke sentinel.
    """
    request = httpx.Request("POST", "http://dr-box.lan:9191/api/accounts/token/")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("auth failed", request=request, response=response)


def _refusing_dest_client(exc: Exception) -> AsyncMock:
    """A dest-B client whose every READ raises ``exc`` (writes stay recordable).

    Mirrors the real failure shape: with a bad password the client cannot
    authenticate, so EVERY read fails — including the ones the importers
    swallow into ``existing = []``.
    """
    client = _empty_dest_client()
    for name in (
        "get_m3u_accounts",
        "get_epg_sources",
        "get_channel_groups",
        "get_channel_profiles",
        "get_stream_profiles",
        "get_user_agents",
        "get_channels",
        "get_streams",
    ):
        setattr(client, name, AsyncMock(side_effect=exc))
    return client


async def _preview(target, dest, tmp_path, *, confirm_apply: bool = False):
    src = _source_client()
    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client", return_value=dest), \
         patch.object(engine, "sync_freshness_reason", return_value=None):
        return await run_sync(
            target,
            confirm_apply=confirm_apply,
            session=MagicMock(),
            ledger_dir=tmp_path,
        )


# ---------------------------------------------------------------------------
# The reproduction — a wrong password.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_password_preview_is_not_a_zero_failure_success(tmp_path):
    """The live reproduction: B rejects the credentials, so the preview must NOT
    come back as a clean plan with would-create counts."""
    dest = _refusing_dest_client(_http_error(401))

    report = await _preview(_sync_target(), dest, tmp_path)

    assert report.destination_unreadable is not None
    assert "authenticat" in report.destination_unreadable.lower()
    # And the count that was a statement about A is not offered at all.
    assert report.category(EntityType.M3U_ACCOUNT).would_create == 0
    assert sum(c.would_create for c in report.categories) == 0


@pytest.mark.asyncio
async def test_unreadable_destination_never_reaches_the_importers(tmp_path):
    """Fail-CLOSED and fail-FAST: the cycle stops before any importer runs, so
    the token endpoint is hit ONCE, not once per category. (Seven back-to-back
    logins is what drove B's 3/min limiter into 429 during live validation.)"""
    dest = _refusing_dest_client(_http_error(401))

    await _preview(_sync_target(), dest, tmp_path)

    # Exactly one destination read attempt — the gate's own probe.
    total_reads = sum(
        getattr(dest, name).await_count
        for name in (
            "get_m3u_accounts", "get_epg_sources", "get_channel_groups",
            "get_channel_profiles", "get_stream_profiles", "get_user_agents",
            "get_channels",
        )
    )
    assert total_reads == 1
    dest.create_m3u_account.assert_not_called()
    dest.create_channel_group.assert_not_called()


@pytest.mark.asyncio
async def test_apply_against_an_unreadable_destination_writes_nothing(tmp_path):
    """The gate is not preview-only: a confirmed APPLY aborts identically rather
    than pushing A's whole config at a destination it cannot read."""
    dest = _refusing_dest_client(_http_error(401))

    report = await _preview(_sync_target(), dest, tmp_path, confirm_apply=True)

    assert report.destination_unreadable is not None
    assert report.outcome is None
    dest.create_m3u_account.assert_not_called()
    dest.create_epg_source.assert_not_called()
    dest.create_channel_group.assert_not_called()
    dest.create_channel_profile.assert_not_called()
    dest.create_stream_profile.assert_not_called()


# ---------------------------------------------------------------------------
# The INVARIANT — every other way a destination can go unread fails the same.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc,expected_fragment",
    [
        (_http_error(403), "authenticat"),
        (httpx.ConnectError("connection refused"), "could not be reached"),
        (httpx.ConnectTimeout("timed out"), "did not respond"),
        (httpx.ReadTimeout("timed out"), "did not respond"),
        (SSRFError("blocked by policy"), "blocked"),
        (_http_error(500), "server error"),
        (_http_error(502), "server error"),
        (RuntimeError("something else entirely"), "could not be read"),
    ],
)
@pytest.mark.asyncio
async def test_every_unreadable_destination_fails_the_same_way(
    tmp_path, exc, expected_fragment
):
    """TLS refusal, DNS failure and a refused connection all arrive as an httpx
    ``ConnectError``; SSRF policy as ``SSRFError``; a 5xx as an
    ``HTTPStatusError``. None of them may produce would-create counts."""
    dest = _refusing_dest_client(exc)

    report = await _preview(_sync_target(), dest, tmp_path)

    assert report.destination_unreadable is not None
    assert expected_fragment in report.destination_unreadable.lower()
    assert sum(c.would_create for c in report.categories) == 0


@pytest.mark.asyncio
async def test_rate_limit_is_reported_as_a_rate_limit_not_a_bad_password(tmp_path):
    """429 is B's limiter, not a credential problem. Reporting it as an auth
    failure would send an operator to rotate perfectly good credentials."""
    dest = _refusing_dest_client(_http_error(429))

    report = await _preview(_sync_target(), dest, tmp_path)

    reason = report.destination_unreadable
    assert reason is not None
    assert "rate-limit" in reason.lower()
    assert "429" in reason
    # Explicitly NOT described as an authentication failure.
    assert "authentication to the destination was rejected" not in reason.lower()


@pytest.mark.asyncio
async def test_a_readable_destination_still_produces_a_real_preview(tmp_path):
    """The gate must not turn every preview red: a reachable B still yields the
    would-create plan, and the marker stays None."""
    dest = _empty_dest_client()

    report = await _preview(_sync_target(), dest, tmp_path)

    assert report.destination_unreadable is None
    assert report.is_dry_run is True
    assert report.category(EntityType.M3U_ACCOUNT).would_create == 1
    assert report.category(EntityType.CHANNEL_GROUP).would_create == 1


@pytest.mark.asyncio
async def test_a_read_that_fails_after_the_gate_still_fails_the_preview(tmp_path):
    """The gate proves B was readable at t0; the importers' ``existing = []``
    fallback can still turn a LATER failed read into "B is empty".

    So the client handed to the orchestrator records read failures: a category
    whose destination read failed mid-cycle cannot be described as would-create.
    """
    dest = _empty_dest_client()
    # The gate's probe succeeds; the M3U category's own read then fails.
    dest.get_m3u_accounts = AsyncMock(side_effect=_http_error(503))

    report = await _preview(_sync_target(), dest, tmp_path)

    assert report.destination_unreadable is not None
    assert "m3u" in report.destination_unreadable.lower()


@pytest.mark.asyncio
async def test_a_freshness_abort_also_says_it_never_read_b(tmp_path):
    """The engine's own freshness abort stops before a client exists, so it read
    nothing either — and must not reach the task wrapper looking like an
    ordinary dry run (``is_dry_run=True``, ``outcome=None``), which is the exact
    shape that wrapper reads as a success."""
    src = _source_client()
    target = _sync_target()

    with patch.object(backup_mod, "get_client", return_value=src), \
         patch.object(engine, "make_remote_client") as make_client, \
         patch.object(
             engine, "sync_freshness_reason",
             return_value="credentials for sync target 'DR Box' (id=7) were revoked",
         ):
        report = await run_sync(target, session=MagicMock(), ledger_dir=tmp_path)

    make_client.assert_not_called()
    assert report.destination_unreadable is not None
    assert "revoked" in report.destination_unreadable


@pytest.mark.asyncio
async def test_the_abort_is_journalled(tmp_path):
    """D9: an aborted cycle still leaves a ``sync_outbound`` audit row — a
    silent stop is the failure mode this whole bead is about."""
    dest = _refusing_dest_client(_http_error(401))

    with patch.object(engine.journal, "log_entry") as log_entry:
        await _preview(_sync_target(), dest, tmp_path)

    log_entry.assert_called()
    kwargs = log_entry.call_args.kwargs
    assert kwargs.get("category") == "sync_outbound"
    assert "ABORTED" in kwargs.get("description", "")


# ---------------------------------------------------------------------------
# The Apply button — the contract the Settings card actually gates on.
# ---------------------------------------------------------------------------


def _unreadable_report() -> RestoreReport:
    report = RestoreReport(is_dry_run=True)
    report.destination_unreadable = (
        "authentication to the destination was rejected (HTTP 401)"
    )
    report.notes.append("sync aborted: could not read the destination")
    return report


@pytest.mark.asyncio
async def test_preview_that_never_read_b_does_not_unlock_apply(_wire_db):
    """``SyncTargetsCard`` offers Apply only when ``result.success`` is true, so
    the preview's TaskResult is the lock. A preview that never read B is not a
    success."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask
    from tests.tasks.test_dbas_sync_task import _make_target

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _fake_run_sync(sync_target, **_kw):
        return _unreadable_report()

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id})
        result = await task.execute()

    assert result.success is False
    assert result.error == "SYNC_DESTINATION_UNREADABLE"
    # The operator is told what actually happened, not "would create N".
    assert "could not" in result.message.lower()
    assert "401" in result.message


@pytest.mark.asyncio
async def test_a_real_preview_still_unlocks_apply(_wire_db):
    """Control: an honest preview keeps working (the gate is not a blanket
    downgrade of every dry run)."""
    from tasks import dbas_sync
    from tasks.dbas_sync import DbasSyncTask
    from tests.tasks.test_dbas_sync_task import _make_target

    session = _wire_db()
    target = _make_target(session)
    target_id = target.id
    session.close()

    async def _fake_run_sync(sync_target, **_kw):
        return RestoreReport(is_dry_run=True)

    with patch.object(dbas_sync, "run_sync", side_effect=_fake_run_sync):
        task = DbasSyncTask()
        task.update_config({"sync_target_id": target_id})
        result = await task.execute()

    assert result.success is True
    assert result.error is None
