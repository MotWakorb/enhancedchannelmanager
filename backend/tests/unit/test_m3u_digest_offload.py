"""
Regression tests for the M3U digest event-loop offload (GH #473).

``M3UDigestTask.execute()`` is ``async def`` but used to perform long,
synchronous, loop-blocking work inline: a blocking SQLAlchemy load, the
Python exclude-filter loop, CPU-bound template rendering, and a blocking
``smtplib`` send. During the GH #473 OOM run that froze the asyncio event
loop for ~182s, stalling every other endpoint.

These tests lock in that the heavy work now runs OFF the event loop:
  1. The synchronous data-gather + filter + render runs in a worker thread
     (``_build_digest_payload`` invoked via ``run_in_threadpool``).
  2. The blocking ``smtplib`` send runs in a worker thread.
  3. While ``execute()`` is running, the event loop is not blocked — a
     concurrent coroutine still makes progress.

Behavioral correctness of the digest itself (filtering, threshold, cap,
delivery, last_digest_at) is covered by the sibling suites
(test_m3u_digest_exclude, _safe_regex, _bounded) and test_m3u_digest.py.
"""
import asyncio
import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from models import M3UChangeLog, M3UDigestSettings


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


def _add_change(session, i):
    base = datetime.utcnow() - timedelta(hours=1)
    row = M3UChangeLog(
        m3u_account_id=1,
        change_time=base + timedelta(seconds=i),
        change_type="streams_added",
        group_name=f"Group {i}",
        count=1,
    )
    row.set_stream_names([f"Stream {i}"])
    session.add(row)
    session.commit()
    return row


@pytest.mark.asyncio
async def test_build_payload_runs_in_worker_thread(test_session):
    """The synchronous digest builder must run off the main (event-loop) thread."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask

    _make_enabled_settings(test_session)
    for i in range(3):
        _add_change(test_session, i)

    recorded = {}
    real_builder = M3UDigestTask._build_digest_payload

    def _spy(self, *args, **kwargs):
        recorded["thread_is_main"] = (
            threading.current_thread() is threading.main_thread()
        )
        return real_builder(self, *args, **kwargs)

    with patch.object(digest_mod, "get_session",
                      return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "_build_digest_payload", new=_spy), \
         patch.object(M3UDigestTask, "_send_digest_email",
                      new=AsyncMock(return_value=True)):
        task = M3UDigestTask()
        result = await task.execute(force=True)

    assert result.success is True
    assert recorded.get("thread_is_main") is False, (
        "_build_digest_payload ran on the main thread — the heavy digest "
        "work is still blocking the event loop (GH #473)"
    )


@pytest.mark.asyncio
async def test_execute_offloads_builder_via_threadpool(test_session):
    """execute() must dispatch the heavy builder through run_in_threadpool."""
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask

    _make_enabled_settings(test_session)
    _add_change(test_session, 0)

    with patch.object(digest_mod, "get_session",
                      return_value=_NoCloseSession(test_session)), \
         patch.object(digest_mod, "run_in_threadpool",
                      wraps=digest_mod.run_in_threadpool) as spy_pool, \
         patch.object(M3UDigestTask, "_send_digest_email",
                      new=AsyncMock(return_value=True)):
        task = M3UDigestTask()
        result = await task.execute(force=True)

    assert result.success is True
    builder_dispatched = any(
        getattr(call.args[0], "__name__", "") == "_build_digest_payload"
        for call in spy_pool.call_args_list
    )
    assert builder_dispatched, (
        "run_in_threadpool was not called with _build_digest_payload — "
        "the heavy digest path is not being offloaded"
    )


@pytest.mark.asyncio
async def test_event_loop_not_blocked_during_execute(test_session):
    """A concurrent coroutine must make progress while execute() runs.

    We make the builder block on a threading.Event for a beat. If the builder
    ran inline on the event loop, the concurrent ticker could not advance until
    the builder returned. Because it runs in a worker thread, the loop stays
    free and the ticker advances while the builder is still parked.
    """
    from tasks import m3u_digest as digest_mod
    from tasks.m3u_digest import M3UDigestTask

    _make_enabled_settings(test_session)
    _add_change(test_session, 0)

    builder_entered = threading.Event()
    release_builder = threading.Event()
    real_builder = M3UDigestTask._build_digest_payload

    def _blocking_builder(self, *args, **kwargs):
        builder_entered.set()
        # Park the worker thread. If this ran on the loop, the ticker below
        # could never advance and the wait() would time out -> test fails.
        assert release_builder.wait(timeout=5.0), "builder release timed out"
        return real_builder(self, *args, **kwargs)

    ticks = {"n": 0}

    async def _ticker():
        # Wait until the builder is parked in its worker thread, then prove the
        # loop is alive by advancing several times before releasing it.
        while not builder_entered.is_set():
            await asyncio.sleep(0.001)
        for _ in range(5):
            ticks["n"] += 1
            await asyncio.sleep(0.001)
        release_builder.set()

    with patch.object(digest_mod, "get_session",
                      return_value=_NoCloseSession(test_session)), \
         patch.object(M3UDigestTask, "_build_digest_payload",
                      new=_blocking_builder), \
         patch.object(M3UDigestTask, "_send_digest_email",
                      new=AsyncMock(return_value=True)):
        task = M3UDigestTask()
        exec_task = asyncio.create_task(task.execute(force=True))
        await _ticker()
        result = await exec_task

    assert result.success is True
    assert ticks["n"] == 5, (
        "the concurrent ticker did not advance while the digest builder was "
        "parked — the event loop was blocked (GH #473)"
    )


@pytest.mark.asyncio
async def test_smtp_send_runs_off_event_loop(test_session):
    """The blocking smtplib send must run in a worker thread, not on the loop."""
    from tasks.m3u_digest import M3UDigestTask

    recorded = {}

    class _FakeSMTP:
        def __init__(self, *args, **kwargs):
            recorded["thread_is_main"] = (
                threading.current_thread() is threading.main_thread()
            )

        def starttls(self, *a, **k):
            pass

        def login(self, *a, **k):
            pass

        def sendmail(self, *a, **k):
            pass

        def quit(self):
            pass

    task = M3UDigestTask()
    config = {
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "from_email": "from@example.com",
        "from_name": "ECM",
        "use_tls": True,
        "use_ssl": False,
    }
    with patch("smtplib.SMTP", _FakeSMTP):
        ok = await task._send_custom_email_with_config(
            config, ["to@example.com"], "subj", "<p>html</p>", "plain"
        )

    assert ok is True
    assert recorded.get("thread_is_main") is False, (
        "smtplib send ran on the main thread — SMTP I/O still blocks the "
        "event loop (GH #473)"
    )
