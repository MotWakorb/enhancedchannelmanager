"""Terminal precedence through real reorder/preparation methods and SQLite.

Only external API/media/notification boundaries are substituted. In particular,
the reorder functions, smart sort, history and terminal exception handlers run
unchanged in the RED layer; no production provider or service is contacted.
"""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
import stream_prober
from models import StreamStats
from sqlalchemy.orm import sessionmaker

from tests.unit.test_stream_probe_capacity import (
    capacity_probe as capacity_probe,  # noqa: PLC0414 - pytest fixture re-export
)
from tests.unit.test_stream_probe_capacity import prepare_probe_all

pytestmark = [pytest.mark.asyncio, pytest.mark.usefixtures("vlc_stream_user_agent")]


def prepare_terminal_run(prober, lane):
    if lane != "bulk":
        prepare_probe_all(prober, lane == "parallel")
    prober._last_probe_scope_kind = "scoped"
    prober._last_probe_channel_stream_ids = {99}


async def run_batch(prober, lane):
    if lane == "bulk":
        return await prober.probe_streams_by_ids([7])
    return await prober.probe_all_streams(skip_m3u_refresh=True)


def assert_terminal_run(prober, result, expected):
    assert result["status"] == expected
    progress = prober.get_probe_progress()
    assert progress["status"] == expected
    assert progress["current_stream"] == ""
    assert not progress["in_progress"]
    assert prober.get_probe_history()[0]["status"] == expected
    assert not prober._bulk_probe_tasks
    assert not prober._probe_tasks
    assert not prober._probe_account_connections
    assert not prober._probe_profile_connections
    assert prober._probe_run_task is None


@pytest.fixture
def reorder_database(test_engine, monkeypatch):
    sessions = sessionmaker(bind=test_engine, expire_on_commit=False)
    monkeypatch.setattr(stream_prober, "get_session", sessions)
    monkeypatch.setattr(stream_prober.journal, "log_entry", MagicMock())
    with sessions() as session:
        session.add_all(
            [
                StreamStats(
                    stream_id=7,
                    stream_name="1080p",
                    probe_status="success",
                    resolution="1920x1080",
                ),
                StreamStats(
                    stream_id=8,
                    stream_name="720p",
                    probe_status="success",
                    resolution="1280x720",
                ),
            ]
        )
        session.commit()


@pytest.mark.parametrize("lane", ["sequential", "parallel", "bulk"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("lookup_error", [False, True])
async def test_real_reorder_terminal_precedence_and_landed_writes(
    capacity_probe, reorder_database, caplog, lane, cancel, lookup_error
):
    prober, _, _ = capacity_probe
    prepare_terminal_run(prober, lane)
    prober.auto_reorder_after_probe = True
    diagnostic = "Disposable reorder channel lookup failure"
    channel = {"id": 1, "name": "Disposable", "streams": [8, 7]}

    async def get_channels(**_kwargs):
        if cancel:
            assert prober.cancel_probe()["status"] == "cancelling"
        if lookup_error:
            raise RuntimeError(diagnostic)
        return {"results": [channel], "next": None}

    prober.client.get_channels.side_effect = get_channels
    prober.client.get_channel.return_value = channel
    result = await run_batch(prober, lane)
    # Bulk's real optional reorder catches lookup errors. Do not turn that
    # existing best-effort behavior into an aborting-run policy.
    aborts = lookup_error and lane != "bulk"
    expected = "cancelled" if cancel else "failed" if aborts else "completed"
    assert_terminal_run(prober, result, expected)
    history = prober.get_probe_history()[0]
    prober.client.get_channels.assert_awaited_once_with(page=1, page_size=500)
    if lookup_error:
        prober.client.update_channel.assert_not_awaited()
        assert history["reordered_channels"] == []
        assert diagnostic in caplog.text
        if aborts:
            assert result["error"] == history["error"] == diagnostic
        else:
            assert history["error"] is None
            assert result["success"] == 1
    else:
        prober.client.update_channel.assert_awaited_once_with(1, {"streams": [7, 8]})
        assert history["error"] is None
        assert len(history["reordered_channels"]) == 1
        assert history["reordered_channels"][0]["channel_id"] == 1
        if lane != "bulk":
            assert result["reordered_channels"] == 1
    if lane != "bulk":
        assert prober._last_probe_channel_stream_ids == (
            {7} if expected == "completed" else {99}
        )


@pytest.mark.parametrize("lane", ["sequential", "parallel", "bulk"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
async def test_real_late_preparation_terminal_precedence(
    capacity_probe, lane, cancel, malformed
):
    prober, _, stream = capacity_probe
    prepare_terminal_run(prober, lane)
    prober._fetch_all_streams = stream_prober.StreamProber._fetch_all_streams.__get__(
        prober
    )
    # Decouple the fixture's fresh-row API from its original list-read mock.
    prober.client.get_stream.side_effect = None
    prober.client.get_stream.return_value = dict(stream)

    async def get_streams(**_kwargs):
        if cancel:
            assert prober.cancel_probe()["status"] == "cancelling"
        # Actual paginated fetch returns this JSON shape. The batch's real
        # indexing then fails before any media, not a mocked reorder exception.
        return {"results": [None] if malformed else [stream], "next": None}

    prober.client.get_streams.side_effect = get_streams
    result = await run_batch(prober, lane)
    expected = "cancelled" if cancel else "failed" if malformed else "completed"
    assert_terminal_run(prober, result, expected)
    if malformed:
        assert "NoneType" in result["error"]
        assert prober.get_probe_history()[0]["error"] == result["error"]
    else:
        assert prober.get_probe_history()[0]["error"] is None
    if cancel or malformed:
        prober._run_ffprobe.assert_not_awaited()
        prober._save_probe_result.assert_not_called()
    else:
        prober._run_ffprobe.assert_awaited_once()


@pytest.mark.parametrize("lane", ["sequential", "parallel"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("outcome", ["success", "named_error", "empty_error"])
async def test_real_channel_preparation_terminal_precedence(
    capacity_probe, lane, cancel, outcome
):
    prober, _, _ = capacity_probe
    prepare_terminal_run(prober, lane)
    prober._fetch_channel_stream_ids = (
        stream_prober.StreamProber._fetch_channel_stream_ids.__get__(prober)
    )
    diagnostic = "Disposable preparation channel lookup failure"

    async def get_channels(**_kwargs):
        if cancel:
            assert prober.cancel_probe()["status"] == "cancelling"
        if outcome != "success":
            raise RuntimeError(diagnostic if outcome == "named_error" else "")
        return {"results": [{"id": 1, "streams": [7]}], "next": None}

    prober.client.get_channels.side_effect = get_channels
    result = await run_batch(prober, lane)
    failed = outcome != "success"
    expected = "cancelled" if cancel else "failed" if failed else "completed"
    assert_terminal_run(prober, result, expected)
    if failed:
        assert result["error"] == (diagnostic if outcome == "named_error" else "")
        assert prober.get_probe_history()[0]["error"] == result["error"]
    if cancel or failed:
        prober._run_ffprobe.assert_not_awaited()
        prober._save_probe_result.assert_not_called()


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("error", [None, "Disposable retained diagnostic"])
async def test_history_diagnostic_cannot_override_cancellation_precedence(
    capacity_probe, status, cancel, error
):
    prober, _, _ = capacity_probe
    prober._probe_progress_status = status
    prober._probe_cancelled = cancel
    # The history duration calculation still uses the legacy naive UTC clock.
    prober._save_probe_history(datetime.now(UTC).replace(tzinfo=None), 0, error=error)
    expected = (
        "cancelled"
        if cancel or status == "cancelled"
        else "failed"
        if error or status == "failed"
        else "completed"
    )
    history = prober.get_probe_history()[0]
    assert history["status"] == expected
    assert history["error"] == error
