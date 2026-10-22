"""
Unit tests for the PlaybackProbeTask.

ffmpeg is replaced by a small shell script on PATH, so the task drives a real
subprocess (kill, deadline, stderr pipe). The SSRF relay is replaced by a
context manager that hands ffmpeg a dummy argument.
"""
import asyncio
import json
import os
import stat
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from models import StreamStats
from stream_prober import StreamProber
from tasks import playback_probe
from tasks.playback_probe import PlaybackProbeTask, parse_ffmpeg_stderr

CHANNEL = {"id": 7, "uuid": "uuid-7", "name": "NPO 1", "streams": [101, 102]}
LEAD = {"id": 101, "name": "NPO 1 HD", "url": "http://provider.example/101.ts", "m3u_account": 1}
BACKUP = {"id": 102, "name": "NPO 1 FHD", "url": "http://provider.example/102.ts", "m3u_account": 1}

FAKE_FFMPEG = """#!/bin/sh
[ -n "$FAKE_FFMPEG_MARKER" ] && echo $$ >> "$FAKE_FFMPEG_MARKER"
printf '%b' "$FAKE_FFMPEG_STDERR" >&2
if [ "${FAKE_FFMPEG_EXIT:-0}" = 0 ]; then
    exec sleep "${FAKE_FFMPEG_SLEEP:-0}"
fi
sleep "${FAKE_FFMPEG_SLEEP:-0}"
exit "$FAKE_FFMPEG_EXIT"
"""


class _FakeRelayInput:
    argument = "http://127.0.0.1:1/resource/token"
    is_http_relay = True

    def __init__(self, failure=None):
        self._failure = failure

    async def raise_for_relay_failure(self):
        if self._failure is not None:
            raise self._failure


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    """Put a fake ffmpeg first on PATH; tests steer it through env vars."""
    script = tmp_path / "ffmpeg"
    script.write_text(FAKE_FFMPEG)
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    marker = tmp_path / "started"
    monkeypatch.setenv("FAKE_FFMPEG_MARKER", str(marker))
    monkeypatch.setenv("FAKE_FFMPEG_STDERR", "")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    return marker


@pytest.fixture
def relay(monkeypatch):
    """Replace the SSRF relay; records the URLs it was asked to open."""
    state = {"urls": [], "failure": None}

    @asynccontextmanager
    async def fake_input(url, **kwargs):
        state["urls"].append(url)
        yield _FakeRelayInput(state["failure"])

    monkeypatch.setattr(playback_probe, "validated_subprocess_input", fake_input)
    return state


@pytest.fixture
def env(test_session, monkeypatch, fake_ffmpeg, relay):
    """Patch the task's collaborators that are not under test."""
    monkeypatch.setattr(playback_probe, "get_session", lambda: test_session)
    monkeypatch.setattr(playback_probe, "VIEWER_POLL_SECONDS", 0.05)
    # The fake plays for well under a minute; this tolerance lets its clean
    # exit count as a complete sample. Tests of ended_early narrow it again.
    monkeypatch.setattr(playback_probe, "SAMPLE_DEADLINE_GRACE_SECONDS", 1000)
    monkeypatch.setattr(
        playback_probe.BandwidthTracker, "get_top_watched_channels",
        staticmethod(lambda limit=10, sort_by="views": [{"channel_id": "uuid-7"}]),
    )
    resolver = MagicMock()
    resolver.return_value.resolve = AsyncMock(return_value="TestAgent/1.0")
    monkeypatch.setattr(playback_probe, "StreamUserAgentResolver", resolver)
    return {"session": test_session, "marker": fake_ffmpeg, "relay": relay, "resolver": resolver}


def _probed(env, *stream_ids):
    """Give streams the stats row a metadata probe leaves behind."""
    env["session"].add_all(
        StreamStats(stream_id=sid, stream_name=f"Stream {sid}", probe_status="success")
        for sid in stream_ids
    )
    env["session"].commit()


def _assert_nothing_stored(env):
    row = env["session"].query(StreamStats).filter_by(stream_id=101).one()
    assert (row.playback_samples, row.playback_start_failures, row.last_playback_probed) == (None, 0, None)


def _make_prober(accounts=None, active_channels=None, streams=None):
    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=accounts if accounts is not None else [
        {"id": 1, "name": "Provider", "max_streams": 0, "profiles": []},
    ])
    client.get_channel_stats = AsyncMock(return_value={"channels": active_channels or []})
    client.get_channels = AsyncMock(return_value={"results": [CHANNEL], "next": None})
    client.get_channel_streams = AsyncMock(return_value=streams or [LEAD, BACKUP])
    return StreamProber(client)


def _make_task(prober, **config):
    task = PlaybackProbeTask()
    task.set_prober(prober)
    if config:
        task.update_config(config)
    return task


async def _wait_for(predicate, timeout=5.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not predicate():
        if loop.time() > end:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.02)


@pytest.mark.parametrize("text, expected", [
    ("", (0, 0, 0)),
    ("[mpegts @ 0x1] timestamp discontinuity (stream id=256): -1234, new offset= 5678\n", (1, 0, 0)),
    ("[http @ 0x2] Will reconnect at 1048576 in 0 second(s), error=End of file.\n", (0, 1, 0)),
    ("[http @ 0x2] Will reconnect at 0 in 1 second(s), error=Connection refused.\n", (0, 0, 1)),
    (
        "[mpegts @ 0x1] timestamp discontinuity (stream id=256): 1, new offset= 2\n"
        "[http @ 0x2] Will reconnect at 524288 in 1 second(s), error=Connection reset by peer.\n"
        "[mpegts @ 0x1] timestamp discontinuity (stream id=257): 3, new offset= 4\n"
        "[aac @ 0x3] Invalid frame\n",
        (2, 1, 0),
    ),
    ("[mpegts @ 0x1] Packet corrupt (stream = 0, dts = 1).\n", (0, 0, 0)),
])
def test_parse_ffmpeg_stderr(text, expected):
    assert parse_ffmpeg_stderr(text) == expected


def _status_sequence(*snapshots):
    """get_channel_stats side effect: one snapshot per call, the last repeats.

    An Exception instance in the sequence is raised instead of returned.
    """
    calls = []

    async def get_channel_stats():
        snapshot = snapshots[min(len(calls), len(snapshots) - 1)]
        calls.append(snapshot)
        if isinstance(snapshot, Exception):
            raise snapshot
        return {"channels": snapshot}

    return get_channel_stats


class TestLoginGuard:
    @pytest.mark.parametrize("profiles, active_profile_id", [
        pytest.param(
            [{"id": 11, "name": "Main", "max_streams": 1, "is_default": True, "is_active": True}],
            11, id="profile-map",
        ),
        pytest.param([], 1, id="empty-profile-map"),
    ])
    async def test_skips_when_account_has_no_free_login(self, env, profiles, active_profile_id):
        prober = _make_prober(
            accounts=[{"id": 1, "name": "Provider", "max_streams": 1, "profiles": profiles}],
            active_channels=[{"channel_id": "uuid-other", "m3u_profile_id": active_profile_id}],
        )
        # A stale cached count must not admit the sample; the fresh snapshot decides.
        prober._get_profile_active_connections = AsyncMock(return_value={})
        result = await _make_task(prober).execute()

        assert result.success is True
        assert result.details["skipped_streams"] == [
            {"id": 101, "name": "NPO 1 HD", "reason": "M3U 'Provider' at max connections (1/1)"}
        ]
        assert env["relay"]["urls"] == []
        assert not env["marker"].exists()

    async def test_unreadable_m3u_accounts_abort_the_run(self, env):
        prober = _make_prober(streams=[LEAD])
        prober.client.get_m3u_accounts = AsyncMock(side_effect=httpx.ConnectError("down"))
        result = await _make_task(prober).execute()

        assert result.success is False
        assert result.error == "M3U_ACCOUNTS_UNAVAILABLE"
        assert env["relay"]["urls"] == []
        assert not env["marker"].exists()

    @pytest.mark.parametrize("m3u_account", [None, 5], ids=["unresolvable", "missing-from-map"])
    async def test_skips_stream_with_unknown_account(self, env, m3u_account):
        prober = _make_prober(streams=[{**LEAD, "m3u_account": m3u_account}])
        result = await _make_task(prober).execute()

        assert result.details["skipped_streams"] == [
            {"id": 101, "name": "NPO 1 HD", "reason": "M3U account unknown"}
        ]
        assert not env["marker"].exists()

    async def test_unlimited_account_still_samples_through_a_profile(self, env):
        _probed(env, 101)
        prober = _make_prober(
            accounts=[{"id": 1, "name": "Provider", "max_streams": 0, "profiles": [
                {"id": 12, "name": "Second", "max_streams": 0, "is_default": False, "is_active": True,
                 "search_pattern": "provider.example", "replace_pattern": "second.example"},
            ]}],
            streams=[LEAD],
        )
        result = await _make_task(prober).execute()

        assert result.success_count == 1
        assert env["relay"]["urls"] == ["http://second.example/101.ts"]

    async def test_user_agent_resolves_from_the_listed_stream(self, env):
        _probed(env, 101)
        await _make_task(_make_prober(streams=[LEAD])).execute()

        env["resolver"].return_value.resolve.assert_awaited_once_with(LEAD, stream_id=101)

    async def test_samples_through_free_profile_with_rewritten_url(self, env):
        _probed(env, 101)
        prober = _make_prober(
            accounts=[{"id": 1, "name": "Provider", "max_streams": 2, "profiles": [
                {"id": 11, "name": "Main", "max_streams": 1, "is_default": True, "is_active": True},
                {"id": 12, "name": "Second", "max_streams": 1, "is_default": False, "is_active": True,
                 "search_pattern": "provider.example", "replace_pattern": "second.example"},
            ]}],
            active_channels=[{"channel_id": "uuid-other", "m3u_profile_id": 11}],
            streams=[LEAD],
        )
        result = await _make_task(prober).execute()

        assert result.success_count == 1
        assert env["relay"]["urls"] == ["http://second.example/101.ts"]

    async def test_unreadable_status_at_admission_skips_the_stream(self, env):
        _probed(env, 101)
        prober = _make_prober(streams=[LEAD])
        prober.client.get_channel_stats = AsyncMock(side_effect=httpx.ConnectError("down"))
        result = await _make_task(prober).execute()

        assert result.details["skipped_streams"] == [
            {"id": 101, "name": "NPO 1 HD", "reason": "channel status unavailable"}
        ]
        assert env["relay"]["urls"] == []
        assert not env["marker"].exists()
        _assert_nothing_stored(env)

    async def test_malformed_status_skips_the_stream_and_the_run_continues(self, env):
        _probed(env, 101, 102)
        env["session"].query(StreamStats).filter_by(stream_id=102).one().playback_samples = "[]"
        env["session"].query(StreamStats).filter_by(stream_id=102).one().last_playback_probed = (
            datetime.utcnow() - timedelta(days=8)
        )
        env["session"].commit()
        prober = _make_prober()
        prober.client.get_channel_stats = _status_sequence([None], [])
        result = await _make_task(prober, playback_retest_days=7).execute()

        assert result.success is True
        assert [e["id"] for e in result.details["error_streams"]] == [101]
        assert [s["id"] for s in result.details["sampled_streams"]] == [102]

    async def test_stream_without_probe_stats_is_skipped_and_no_row_is_created(self, env):
        result = await _make_task(_make_prober(streams=[LEAD])).execute()

        assert result.details["skipped_streams"] == [
            {"id": 101, "name": "NPO 1 HD", "reason": "no probe stats yet"}
        ]
        assert env["relay"]["urls"] == []
        assert not env["marker"].exists()
        assert env["session"].query(StreamStats).count() == 0

    async def test_skips_channel_that_is_playing(self, env):
        prober = _make_prober(active_channels=[{"channel_id": "uuid-7", "m3u_profile_id": 99}], streams=[LEAD])
        result = await _make_task(prober).execute()

        assert result.details["skipped_streams"] == [
            {"id": 101, "name": "NPO 1 HD", "reason": "channel is playing"}
        ]
        assert not env["marker"].exists()


class TestProbeGuard:
    async def test_already_running_when_another_holder_has_the_guard(self, env):
        prober = _make_prober()
        assert prober.try_acquire_probe_guard() is not None

        result = await _make_task(prober).execute()

        assert result.error == "ALREADY_RUNNING"
        assert prober._probing_in_progress is True
        assert not env["marker"].exists()

    async def test_probe_first_blocks_black_screen_scan(self, env, monkeypatch):
        _probed(env, 101)
        from tasks.black_screen_scan import BlackScreenScanTask

        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        prober = _make_prober(streams=[LEAD])
        task = _make_task(prober)
        run = asyncio.create_task(task.execute())
        await _wait_for(env["marker"].exists)

        scan = BlackScreenScanTask()
        scan.set_prober(prober)
        scan_result = await scan.execute()
        assert scan_result.success is False
        assert scan_result.error == "PROBE_RUNNING"

        task._cancel_requested = True
        result = await asyncio.wait_for(run, timeout=5)
        assert result.error == "CANCELLED"
        assert prober._probing_in_progress is False

    async def test_task_cancellation_releases_guard(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        prober = _make_prober(streams=[LEAD])
        run = asyncio.create_task(_make_task(prober).execute())
        await _wait_for(env["marker"].exists)
        assert prober._probing_in_progress is True

        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=5)
        assert prober._probing_in_progress is False

    async def test_cancel_probe_stops_the_run_and_releases_guard(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        prober = _make_prober(streams=[LEAD])
        run = asyncio.create_task(_make_task(prober).execute())
        await _wait_for(env["marker"].exists)

        prober.cancel_probe()
        result = await asyncio.wait_for(run, timeout=5)

        assert result.error == "CANCELLED"
        assert result.details["incomplete_streams"] == [{"id": 101, "name": "NPO 1 HD", "reason": "cancelled"}]
        assert prober._probing_in_progress is False

    async def test_stale_holder_release_after_force_reset_keeps_new_guard(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        prober = _make_prober(streams=[LEAD])
        task = _make_task(prober)
        run = asyncio.create_task(task.execute())
        await _wait_for(env["marker"].exists)

        prober.force_reset_probe_state()
        assert prober.try_acquire_probe_guard() is not None
        # The new acquire clears the cancel flag; the revoked token alone stops the run.
        result = await asyncio.wait_for(run, timeout=5)

        assert result.error == "CANCELLED"
        assert prober._probing_in_progress is True

    async def test_revoked_token_reads_cancelled_after_reacquire(self, env):
        prober = _make_prober()
        first = prober.try_acquire_probe_guard()
        prober.force_reset_probe_state()
        second = prober.try_acquire_probe_guard()

        assert prober.probe_guard_cancelled(first) is True
        assert prober.probe_guard_cancelled(second) is False

    async def test_set_prober_mid_sample_keeps_run_on_its_prober(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        first = _make_prober(streams=[LEAD])
        task = _make_task(first)
        run = asyncio.create_task(task.execute())
        await _wait_for(env["marker"].exists)

        second = _make_prober(streams=[LEAD])
        task.set_prober(second)
        assert second.try_acquire_probe_guard() is not None
        first.cancel_probe()
        result = await asyncio.wait_for(run, timeout=5)

        assert result.error == "CANCELLED"
        assert first._probing_in_progress is False
        assert second._probing_in_progress is True

    async def test_pause_holds_the_next_sample_until_resume(self, env, monkeypatch):
        _probed(env, 101)
        # The first sample outlasts the pause call, so the pause is set while it runs.
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "1.5")
        env["session"].add(StreamStats(
            stream_id=102, stream_name="NPO 1 FHD", playback_samples="[]",
            last_playback_probed=datetime.utcnow() - timedelta(days=8),
        ))
        env["session"].commit()
        prober = _make_prober()
        run = asyncio.create_task(_make_task(prober).execute())
        await _wait_for(env["marker"].exists)

        assert prober.pause_probe()["status"] == "paused"
        await asyncio.sleep(2.5)
        assert len(env["marker"].read_text().split()) == 1
        assert not run.done()

        prober.resume_probe()
        result = await asyncio.wait_for(run, timeout=10)
        assert [s["id"] for s in result.details["sampled_streams"]] == [101, 102]


class TestSampleOutcomes:
    async def test_viewer_arrival_aborts_and_leaves_stored_window(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        window = [{"at": "2026-10-01T00:00:00Z", "seconds": 300, "discontinuities": 2, "reconnects": 1}]
        env["session"].add(StreamStats(
            stream_id=101, stream_name="NPO 1 HD", playback_samples=json.dumps(window),
            playback_seconds=300, playback_discontinuities=2, playback_reconnects=1,
            last_playback_probed=datetime(2026, 10, 1),
        ))
        env["session"].commit()

        prober = _make_prober(
            accounts=[{"id": 1, "name": "Provider", "max_streams": 2, "profiles": []}], streams=[LEAD],
        )
        # Baseline read at the login check, then a viewer on the same account.
        prober.client.get_channel_stats = _status_sequence(
            [], [{"channel_id": "uuid-other", "m3u_profile_id": 1}],
        )
        result = await asyncio.wait_for(_make_task(prober).execute(), timeout=10)

        assert result.success is True
        assert result.success_count == 0
        assert result.details["incomplete_streams"] == [{"id": 101, "name": "NPO 1 HD", "reason": "viewer"}]
        row = env["session"].query(StreamStats).filter_by(stream_id=101).one()
        assert json.loads(row.playback_samples) == window
        assert (row.playback_seconds, row.playback_discontinuities, row.playback_reconnects) == (300, 2, 1)
        assert row.last_playback_probed == datetime(2026, 10, 1)

    async def test_viewer_on_other_account_does_not_abort(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.3")
        prober = _make_prober(accounts=[
            {"id": 1, "name": "Provider", "max_streams": 2, "profiles": []},
            {"id": 2, "name": "Other", "max_streams": 2, "profiles": []},
        ], streams=[LEAD])
        prober.client.get_channel_stats = AsyncMock(
            return_value={"channels": [{"channel_id": "uuid-x", "m3u_profile_id": 2}]}
        )
        result = await _make_task(prober).execute()

        assert result.success_count == 1
        assert result.details["incomplete_streams"] == []

    async def test_viewer_replacing_another_aborts(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        prober = _make_prober(
            accounts=[{"id": 1, "name": "Provider", "max_streams": 2, "profiles": []}], streams=[LEAD],
        )
        # The account's connection count stays at one, but the channel changes.
        prober.client.get_channel_stats = _status_sequence(
            [{"channel_id": "uuid-a", "m3u_profile_id": 1}],
            [{"channel_id": "uuid-b", "m3u_profile_id": 1}],
        )
        result = await asyncio.wait_for(_make_task(prober).execute(), timeout=10)

        assert result.details["incomplete_streams"] == [{"id": 101, "name": "NPO 1 HD", "reason": "viewer"}]

    async def test_unreadable_status_mid_sample_aborts_and_stores_nothing(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        prober = _make_prober(streams=[LEAD])
        prober.client.get_channel_stats = _status_sequence([], httpx.ConnectError("down"))
        result = await asyncio.wait_for(_make_task(prober).execute(), timeout=10)

        assert result.details["incomplete_streams"] == [
            {"id": 101, "name": "NPO 1 HD", "reason": "status_unavailable"}
        ]
        _assert_nothing_stored(env)

    async def test_deadline_kills_hung_ffmpeg(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        monkeypatch.setattr(playback_probe, "SAMPLE_DEADLINE_GRACE_SECONDS", 0.3)
        prober = _make_prober()
        task = _make_task(prober)

        loop = asyncio.get_running_loop()
        started = loop.time()
        sample = await task._sample_stream(
            prober, "http://provider.example/101.ts", guard=prober.try_acquire_probe_guard(),
            user_agent="TestAgent/1.0", sample_seconds=0, account_id=1, baseline=set(),
        )

        assert sample.outcome == "deadline"
        assert loop.time() - started < 5

    async def test_offset_0_retries_at_deadline_count_as_failed_start(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        monkeypatch.setenv("FAKE_FFMPEG_STDERR", "[http @ 0x2] Will reconnect at 0 in 1 second(s)\\n" * 3)
        monkeypatch.setattr(playback_probe, "SAMPLE_DEADLINE_GRACE_SECONDS", 0.3)
        window = [{"at": "2026-10-01T00:00:00Z", "seconds": 300, "discontinuities": 0, "reconnects": 0}]
        env["session"].add(StreamStats(
            stream_id=101, stream_name="NPO 1 HD", playback_samples=json.dumps(window),
            playback_seconds=300, last_playback_probed=datetime(2026, 10, 1),
        ))
        env["session"].commit()
        task = _make_task(_make_prober(streams=[LEAD]))
        sample_stream = task._sample_stream

        async def short_sample(*args, **kwargs):
            # The task floors a sample at one minute; the subprocess still runs.
            return await sample_stream(*args, **{**kwargs, "sample_seconds": 0})

        monkeypatch.setattr(task, "_sample_stream", short_sample)
        result = await asyncio.wait_for(task.execute(), timeout=10)

        [error] = result.details["error_streams"]
        assert error["error"].startswith("failed_start: ffmpeg did not exit within")
        row = env["session"].query(StreamStats).filter_by(stream_id=101).one()
        assert row.playback_start_failures == 1
        assert json.loads(row.playback_samples) == window
        assert row.last_playback_probed == datetime(2026, 10, 1)

    async def test_deadline_after_reconnect_is_not_a_failed_start(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        monkeypatch.setenv("FAKE_FFMPEG_STDERR", (
            "[http @ 0x2] Will reconnect at 2048 in 1 second(s)\\n"
            + "[http @ 0x2] Will reconnect at 0 in 1 second(s)\\n" * 2
        ))
        monkeypatch.setattr(playback_probe, "SAMPLE_DEADLINE_GRACE_SECONDS", 0.3)
        task = _make_task(_make_prober(streams=[LEAD]))
        sample_stream = task._sample_stream

        async def short_sample(*args, **kwargs):
            return await sample_stream(*args, **{**kwargs, "sample_seconds": 0})

        monkeypatch.setattr(task, "_sample_stream", short_sample)
        result = await asyncio.wait_for(task.execute(), timeout=10)

        [error] = result.details["error_streams"]
        assert error["error"].startswith("ffmpeg did not exit within")
        _assert_nothing_stored(env)

    async def test_poll_error_reaps_ffmpeg(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "30")
        task = _make_task(_make_prober(streams=[LEAD]))

        async def broken_poll(*args):
            await _wait_for(lambda: env["marker"].exists() and env["marker"].read_text().strip())
            raise RuntimeError("poll broke")

        monkeypatch.setattr(task, "_poll_viewer", broken_poll)
        result = await asyncio.wait_for(task.execute(), timeout=10)

        assert [e["id"] for e in result.details["error_streams"]] == [101]
        pid = int(env["marker"].read_text().split()[0])
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    async def test_relay_failure_names_last_drop(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_STDERR", "[http @ 0x2] Will reconnect at 2048 in 1 second(s)\\n")
        env["relay"]["failure"] = httpx.ReadError("upstream closed")
        result = await _make_task(_make_prober(streams=[LEAD])).execute()

        assert result.details["sampled_streams"][0]["reconnects"] == 1
        assert result.details["sampled_streams"][0]["drop_reason"] == "ReadError"

    @pytest.mark.parametrize("stderr, exit_code", [
        pytest.param("[tcp @ 0x1] Connection refused\\n", "1", id="nonzero-exit"),
        pytest.param(
            "[mpegts @ 0x1] timestamp discontinuity (stream id=256)\\n" * 12
            + "[http @ 0x2] Will reconnect at 0 in 1 second(s)\\n" * 3,
            "0", id="offset-0-reconnects-only",
        ),
    ])
    async def test_failed_start_counts_and_leaves_window(self, env, monkeypatch, stderr, exit_code):
        monkeypatch.setenv("FAKE_FFMPEG_STDERR", stderr)
        monkeypatch.setenv("FAKE_FFMPEG_EXIT", exit_code)
        window = [{"at": "2026-10-01T00:00:00Z", "seconds": 300, "discontinuities": 0, "reconnects": 0}]
        env["session"].add(StreamStats(
            stream_id=101, stream_name="NPO 1 HD", playback_samples=json.dumps(window),
            playback_seconds=300, last_playback_probed=datetime(2026, 10, 1),
        ))
        env["session"].commit()
        result = await _make_task(_make_prober(streams=[LEAD])).execute()

        assert result.success_count == 0
        assert [e["error"].split(":")[0] for e in result.details["error_streams"]] == ["failed_start"]
        row = env["session"].query(StreamStats).filter_by(stream_id=101).one()
        assert row.playback_start_failures == 1
        assert json.loads(row.playback_samples) == window
        assert row.last_playback_probed == datetime(2026, 10, 1)

    async def test_nonzero_exit_after_reconnect_is_failed_with_redacted_line(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_STDERR", (
            "[http @ 0x2] Will reconnect at 2048 in 1 second(s)\\n"
            "[http @ 0x2] Failed to open http://127.0.0.1:1/resource/token: Connection refused\\n"
        ))
        monkeypatch.setenv("FAKE_FFMPEG_EXIT", "1")
        result = await _make_task(_make_prober(streams=[LEAD])).execute()

        assert result.details["error_streams"] == [{
            "id": 101, "name": "NPO 1 HD",
            "error": "failed: [http @ 0x2] Failed to open [REDACTED stream URL]: Connection refused",
        }]
        _assert_nothing_stored(env)

    async def test_clean_exit_before_sample_length_is_ended_early(self, env, monkeypatch):
        _probed(env, 101)
        monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "1")
        monkeypatch.setattr(playback_probe, "SAMPLE_DEADLINE_GRACE_SECONDS", 30)
        result = await asyncio.wait_for(
            _make_task(_make_prober(streams=[LEAD]), sample_minutes=1).execute(), timeout=10,
        )

        assert result.success_count == 0
        assert result.details["error_streams"] == [
            {"id": 101, "name": "NPO 1 HD", "error": "ended_early: no ffmpeg output"}
        ]
        _assert_nothing_stored(env)


class TestSampleWindow:
    async def test_window_keeps_last_three_samples(self, env, monkeypatch):
        _probed(env, 101)
        before = datetime.utcnow()
        prober = _make_prober(streams=[LEAD])
        for discontinuities in (1, 2, 3, 4):
            monkeypatch.setenv(
                "FAKE_FFMPEG_STDERR",
                "[mpegts @ 0x1] timestamp discontinuity (stream id=256)\\n" * discontinuities,
            )
            result = await _make_task(prober).execute()
            assert result.success_count == 1

        row = env["session"].query(StreamStats).filter_by(stream_id=101).one()
        assert [s["discontinuities"] for s in json.loads(row.playback_samples)] == [2, 3, 4]
        assert row.playback_discontinuities == 9
        assert row.playback_reconnects == 0
        assert before <= row.last_playback_probed <= datetime.utcnow()

    async def test_complete_sample_counts_nonzero_reconnects_and_clears_start_failures(self, env, monkeypatch):
        monkeypatch.setenv("FAKE_FFMPEG_STDERR", (
            "[mpegts @ 0x1] timestamp discontinuity (stream id=256)\\n" * 3
            + "[http @ 0x2] Will reconnect at 1048576 in 1 second(s)\\n"
        ))
        env["session"].add(StreamStats(stream_id=101, stream_name="NPO 1 HD", playback_start_failures=2))
        env["session"].commit()
        result = await _make_task(_make_prober(streams=[LEAD])).execute()

        assert result.success_count == 1
        row = env["session"].query(StreamStats).filter_by(stream_id=101).one()
        assert (row.playback_discontinuities, row.playback_reconnects) == (3, 1)
        assert row.playback_start_failures == 0

    async def test_unreadable_window_is_an_error_and_the_run_continues(self, env):
        env["session"].add_all([
            StreamStats(stream_id=101, stream_name="NPO 1 HD", playback_samples="not json"),
            StreamStats(stream_id=102, stream_name="NPO 1 FHD", playback_samples="[]",
                        last_playback_probed=datetime.utcnow() - timedelta(days=8)),
        ])
        env["session"].commit()
        result = await _make_task(_make_prober(), playback_retest_days=7).execute()

        assert result.success is True
        assert [e["id"] for e in result.details["error_streams"]] == [101]
        assert [s["id"] for s in result.details["sampled_streams"]] == [102]

    async def test_retests_backup_stream_only_when_sample_is_stale(self, env):
        _probed(env, 101)
        env["session"].add(StreamStats(
            stream_id=102, stream_name="NPO 1 FHD", playback_samples="[]",
            last_playback_probed=datetime.utcnow() - timedelta(days=8),
        ))
        env["session"].commit()
        result = await _make_task(_make_prober(), playback_retest_days=7).execute()
        assert [s["id"] for s in result.details["sampled_streams"]] == [101, 102]

        result = await _make_task(_make_prober(), playback_retest_days=7).execute()
        assert [s["id"] for s in result.details["sampled_streams"]] == [101]
