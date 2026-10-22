from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import sessionmaker

from config import DispatcharrSettings
from models import StreamStats
from stream_prober import StreamProber


@pytest.fixture
def prober(test_engine, monkeypatch, tmp_path):
    monkeypatch.setattr("stream_prober.get_session", sessionmaker(bind=test_engine))
    monkeypatch.setattr("stream_prober.PROBE_HISTORY_FILE", tmp_path / "probe_history.json")
    return StreamProber(MagicMock())


def test_defaults_and_saved_custom_order():
    settings = DispatcharrSettings()
    assert settings.playback_reconnect_threshold == 4.0
    assert settings.playback_start_failure_threshold == 2
    assert settings.deprioritize_unstable is False
    assert settings.failed_stream_sort_order[-1] == "unstable"
    saved = DispatcharrSettings(failed_stream_sort_order=["low_fps", "failed", "black_screen", "low_bitrate"])
    assert saved.failed_stream_sort_order == ["low_fps", "failed", "black_screen", "low_bitrate", "unstable"]
    custom = DispatcharrSettings(failed_stream_sort_order=["unstable", "failed", "black_screen", "low_fps", "low_bitrate"])
    assert custom.failed_stream_sort_order[0] == "unstable"


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_threshold_positive_finite(threshold):
    with pytest.raises(ValidationError):
        DispatcharrSettings(playback_reconnect_threshold=threshold)
    with pytest.raises(ValueError):
        StreamProber(MagicMock(), playback_reconnect_threshold=threshold)


def test_start_failure_threshold_at_least_one():
    with pytest.raises(ValueError):
        StreamProber(MagicMock(), playback_start_failure_threshold=0)


@pytest.mark.parametrize("seconds,reconnects,start_failures,expected", [
    (0, 0, 0, None),
    (599, 50, 0, None),
    (600, 0, 0, False),
    (600, 1, 0, True),       # 6.0 per hour, above the default threshold of 4
    (3600, 3, 0, False),
    (3600, 4, 0, True),      # exactly at the default threshold
    (7200, 7, 0, False),
    (7200, 8, 0, True),
    (0, 0, 2, True),         # two consecutive failed starts flag regardless of exposure
    (3600, 0, 1, False),
    (599, 0, 1, None),
])
def test_classify_unstable_boundaries(prober, seconds, reconnects, start_failures, expected):
    assert prober.classify_unstable(seconds, reconnects, start_failures) is expected


def test_classify_unstable_follows_live_thresholds(prober):
    prober.playback_reconnect_threshold = 20.0
    assert prober.classify_unstable(3600, 19, 0) is False
    assert prober.classify_unstable(3600, 20, 0) is True
    prober.playback_start_failure_threshold = 3
    assert prober.classify_unstable(0, 0, 2) is None
    assert prober.classify_unstable(0, 0, 3) is True


def test_threshold_change_reclassifies_rows(prober, test_session):
    test_session.add_all([
        StreamStats(stream_id=1, probe_status="success", playback_seconds=3600, playback_reconnects=10, is_unstable=True),
        StreamStats(stream_id=2, probe_status="success", playback_seconds=3600, playback_reconnects=2, is_unstable=False),
        StreamStats(stream_id=3, probe_status="success", playback_seconds=300, playback_reconnects=40, is_unstable=None),
        StreamStats(stream_id=4, probe_status="success", playback_seconds=0, playback_start_failures=2, is_unstable=True),
    ])
    test_session.commit()
    prober.playback_reconnect_threshold = 12.0
    prober.reclassify_unstable()
    rows = {row.stream_id: row.is_unstable for row in test_session.query(StreamStats).all()}
    assert rows == {1: False, 2: False, 3: None, 4: True}
    prober.playback_reconnect_threshold = 1.0
    prober.playback_start_failure_threshold = 3
    prober.reclassify_unstable()
    test_session.expire_all()
    rows = {row.stream_id: row.is_unstable for row in test_session.query(StreamStats).all()}
    assert rows == {1: True, 2: True, 3: None, 4: None}


def test_live_sort_snapshot(prober):
    prober.update_sort_settings([], {}, {}, deprioritize_unstable=True)
    snapshot = prober._sort_settings_snapshot()
    assert snapshot["deprioritize_unstable"] is True
    assert "unstable" in snapshot["failed_stream_sort_order"]
