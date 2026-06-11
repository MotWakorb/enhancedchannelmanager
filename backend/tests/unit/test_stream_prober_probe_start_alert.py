"""Unit tests: the stream prober's "probe started" notification only dispatches
an external alert when the caller opts in (GH #462 follow-up, bd-yqnzs).

``StreamProber._create_probe_notification`` emits an info-level "Stream probe
started" notification. It used to hardcode ``send_alerts=True`` on the create
callback (stream_prober.py, since v0.14.0-0008), so the start message was pushed
to Telegram/Discord/email on every run regardless of the stream_probe task's
``alert_on_info`` setting — the leak bd-on4sr fixed for the task-engine path but
missed for this separate prober path. The fix threads a ``send_alerts`` value
through ``_create_probe_notification`` (and ``probe_all_streams`` /
``probe_streams_by_ids`` as ``start_send_alerts``); these tests pin that the
callback receives exactly what the caller passed.
"""
from unittest.mock import AsyncMock

import pytest

from stream_prober import StreamProber


def _make_prober_with_callbacks():
    prober = StreamProber(client=AsyncMock())
    create_cb = AsyncMock(return_value={"id": 1})
    prober.set_notification_callbacks(
        create_callback=create_cb,
        update_callback=AsyncMock(),
        delete_by_source_callback=AsyncMock(return_value=0),
    )
    return prober, create_cb


@pytest.mark.asyncio
async def test_probe_start_alert_suppressed_when_send_alerts_false():
    """send_alerts=False -> in-app notification still created, but no external alert."""
    prober, create_cb = _make_prober_with_callbacks()

    await prober._create_probe_notification(5, send_alerts=False)

    create_cb.assert_awaited_once()
    assert create_cb.await_args.kwargs["send_alerts"] is False


@pytest.mark.asyncio
async def test_probe_start_alert_dispatched_when_send_alerts_true():
    """send_alerts=True -> external alert dispatched (opted-in info alerts)."""
    prober, create_cb = _make_prober_with_callbacks()

    await prober._create_probe_notification(5, send_alerts=True)

    create_cb.assert_awaited_once()
    assert create_cb.await_args.kwargs["send_alerts"] is True


@pytest.mark.asyncio
async def test_probe_start_alert_defaults_to_true():
    """Default preserves pre-fix behavior for any caller that doesn't pass the flag."""
    prober, create_cb = _make_prober_with_callbacks()

    await prober._create_probe_notification(5)

    create_cb.assert_awaited_once()
    assert create_cb.await_args.kwargs["send_alerts"] is True
