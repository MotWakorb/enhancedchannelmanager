"""Integration test: ``task_start_alerts_enabled`` gates the info-level probe
"started" external alert on ``send_alerts AND alert_on_info`` (GH #462, bd-yqnzs).

Manual probe endpoints (probe/all, probe/bulk) don't get an engine-gated
``send_alerts`` like scheduled tasks do, so they look up the stream_probe task's
alert config via this helper. The helper mirrors the task-engine gate: info-level
external alerts are opt-in, defaulting to off when the task has no row.
"""
import pytest
from sqlalchemy.orm import sessionmaker

import database
from models import ScheduledTask
from services.notification_service import task_start_alerts_enabled

TASK_ID = "stream_probe"


def _seed_task(SessionLocal, *, send_alerts: bool, alert_on_info: bool):
    s = SessionLocal()
    try:
        s.query(ScheduledTask).filter(ScheduledTask.task_id == TASK_ID).delete()
        s.add(ScheduledTask(
            task_id=TASK_ID,
            task_name="Stream Probe",
            description="seed",
            enabled=True,
            schedule_type="manual",
            send_alerts=send_alerts,
            alert_on_info=alert_on_info,
            show_notifications=True,
        ))
        s.commit()
    finally:
        s.close()


@pytest.fixture
def _session_local(test_engine, monkeypatch):
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False
    )
    monkeypatch.setattr(database, "_SessionLocal", SessionLocal)
    return SessionLocal


def test_disabled_when_alert_on_info_false(_session_local):
    """Master send_alerts on, info alerts off (the reporter's config) -> no alert."""
    _seed_task(_session_local, send_alerts=True, alert_on_info=False)
    assert task_start_alerts_enabled(TASK_ID) is False


def test_enabled_when_both_on(_session_local):
    """Explicitly opted into info alerts -> external start alert allowed."""
    _seed_task(_session_local, send_alerts=True, alert_on_info=True)
    assert task_start_alerts_enabled(TASK_ID) is True


def test_disabled_when_master_send_alerts_off(_session_local):
    """Master toggle off overrides alert_on_info -> no alert."""
    _seed_task(_session_local, send_alerts=False, alert_on_info=True)
    assert task_start_alerts_enabled(TASK_ID) is False


def test_defaults_off_when_no_task_row(_session_local):
    """No ScheduledTask row -> default to off (info alerts are opt-in)."""
    s = _session_local()
    try:
        s.query(ScheduledTask).filter(ScheduledTask.task_id == TASK_ID).delete()
        s.commit()
    finally:
        s.close()
    assert task_start_alerts_enabled(TASK_ID) is False
