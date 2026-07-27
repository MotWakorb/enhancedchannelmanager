"""Unit tests for ``observability.update_auto_creation_snapshot_metrics``
(ADR-010 §D7 / bead ``enhancedchannelmanager-uc51o.3``).

The snapshot count/size gauges are emitted from ``CleanupTask.execute`` after
the snapshot prune. These tests exercise the helper directly against a real
in-memory SQLite session so the count + SUM(LENGTH(channels_data)) contract
stays locked.
"""
from __future__ import annotations

from datetime import datetime

import pytest
from prometheus_client import generate_latest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import database
import observability
from models import ChannelPipelineExecution, ChannelPipelineSnapshot


@pytest.fixture(autouse=True)
def _reset_observability_state():
    observability.reset_for_tests()
    yield
    observability.reset_for_tests()


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
        database.Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _add_snapshot(session, channels):
    execution = ChannelPipelineExecution(
        mode="execute", triggered_by="manual",
        started_at=datetime.utcnow(), status="completed",
    )
    session.add(execution)
    session.commit()
    snap = ChannelPipelineSnapshot(
        execution_id=execution.id,
        snapshot_time=datetime.utcnow(),
        channel_count=len(channels),
    )
    snap.set_channels_data({"channels": channels})
    session.add(snap)
    session.commit()
    return snap


class TestUpdateChannelPipelineSnapshotMetrics:
    def test_publishes_count_and_bytes(self, session):
        observability.install_metrics()
        snap1 = _add_snapshot(session, [{"id": 1, "stream_ids": [1, 2]}])
        snap2 = _add_snapshot(session, [{"id": 2, "stream_ids": [3]}])
        expected_bytes = float(len(snap1.channels_data) + len(snap2.channels_data))

        observability.update_auto_creation_snapshot_metrics(session=session)

        assert observability.get_metric("auto_creation_snapshot_count")._value.get() == 2.0
        assert observability.get_metric("auto_creation_snapshot_bytes")._value.get() == expected_bytes

    def test_zero_when_no_snapshots(self, session):
        observability.install_metrics()
        observability.update_auto_creation_snapshot_metrics(session=session)
        assert observability.get_metric("auto_creation_snapshot_count")._value.get() == 0.0
        assert observability.get_metric("auto_creation_snapshot_bytes")._value.get() == 0.0

    def test_does_not_raise_when_metrics_uninitialized(self, session):
        """Cold start: get_metric lazily installs; helper must never raise."""
        observability.reset_for_tests()
        _add_snapshot(session, [{"id": 1, "stream_ids": [1]}])
        # Must not raise.
        observability.update_auto_creation_snapshot_metrics(session=session)

    def test_metrics_render_with_expected_names(self, session):
        observability.install_metrics()
        _add_snapshot(session, [{"id": 1, "stream_ids": [1]}])
        observability.update_auto_creation_snapshot_metrics(session=session)
        rendered = generate_latest(observability.REGISTRY).decode("utf-8")
        assert "ecm_auto_creation_snapshot_count" in rendered
        assert "ecm_auto_creation_snapshot_bytes" in rendered
