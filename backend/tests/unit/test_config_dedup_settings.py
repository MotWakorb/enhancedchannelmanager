"""Tests for dedup_threshold and dedup_m3u_toast_suppressed settings (bd-0b6xj / BD-B).

ADR-008 §D2: hard confidence floor of 0.60 enforced at the settings-persistence
boundary. The matcher service (BD-A) is the load-bearing enforcement layer; this
validator is layer 2 of three-layer defense in depth.
"""
import logging

import pytest

import config as cfg
from config import DispatcharrSettings, clear_settings_cache


@pytest.fixture(autouse=True)
def reset_module_state():
    """Reset module-level warn flag and settings cache between tests for isolation."""
    clear_settings_cache()
    yield
    clear_settings_cache()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_dedup_threshold_default_is_0_80():
    s = DispatcharrSettings()
    assert s.dedup_threshold == pytest.approx(0.80)


def test_dedup_m3u_toast_suppressed_default_is_false():
    s = DispatcharrSettings()
    assert s.dedup_m3u_toast_suppressed is False


# ---------------------------------------------------------------------------
# Validator: below-floor clamps to 0.60 with WARN
# ---------------------------------------------------------------------------


def test_below_floor_clamps_to_0_60(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        s = DispatcharrSettings(dedup_threshold=0.30)
    assert s.dedup_threshold == pytest.approx(0.60)


def test_below_floor_emits_warn_log(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        DispatcharrSettings(dedup_threshold=0.30)
    assert any(
        "[CONFIG] dedup_threshold=" in r.message and "integrity floor" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


def test_below_floor_warn_references_adr_008(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        DispatcharrSettings(dedup_threshold=0.10)
    warn_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("ADR-008" in m for m in warn_messages)


def test_negative_value_clamps_to_floor(caplog):
    """Negative dedup_threshold values hit the lower-bound branch and clamp to
    CONFIDENCE_FLOOR (0.60) with the same WARN as any other below-floor value.
    Guards against future refactor that might short-circuit negative values to 0
    before the clamp check runs."""
    with caplog.at_level(logging.WARNING, logger="config"):
        s = DispatcharrSettings(dedup_threshold=-0.50)
    assert s.dedup_threshold == pytest.approx(0.60)
    assert any(
        "integrity floor" in r.message
        for r in caplog.records
        if r.levelno == logging.WARNING
    )


# ---------------------------------------------------------------------------
# Validator: at-floor (0.60) accepted, no WARN
# ---------------------------------------------------------------------------


def test_at_floor_0_60_accepted(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        s = DispatcharrSettings(dedup_threshold=0.60)
    assert s.dedup_threshold == pytest.approx(0.60)
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# Validator: above-floor (e.g., 0.75) accepted, no WARN
# ---------------------------------------------------------------------------


def test_above_floor_0_75_accepted(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        s = DispatcharrSettings(dedup_threshold=0.75)
    assert s.dedup_threshold == pytest.approx(0.75)
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# Validator: clamps above 1.00 silently
# ---------------------------------------------------------------------------


def test_above_1_00_clamps_to_1_00(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        s = DispatcharrSettings(dedup_threshold=1.50)
    assert s.dedup_threshold == pytest.approx(1.00)
    # Silent — no WARN for the upper-bound clamp
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


def test_exactly_1_00_accepted(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        s = DispatcharrSettings(dedup_threshold=1.00)
    assert s.dedup_threshold == pytest.approx(1.00)
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# WARN fires once per process; clear_settings_cache resets it
# ---------------------------------------------------------------------------


def test_warn_fires_once_per_process(caplog):
    """The WARN log for a below-floor value fires only once per process instance."""
    with caplog.at_level(logging.WARNING, logger="config"):
        DispatcharrSettings(dedup_threshold=0.30)
        DispatcharrSettings(dedup_threshold=0.10)

    warn_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "integrity floor" in r.message
    ]
    assert len(warn_records) == 1


def test_cache_clear_resets_warn_flag(caplog):
    """After clear_settings_cache(), the WARN fires again on the next below-floor construction."""
    with caplog.at_level(logging.WARNING, logger="config"):
        DispatcharrSettings(dedup_threshold=0.30)
        clear_settings_cache()
        DispatcharrSettings(dedup_threshold=0.10)

    warn_records = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "integrity floor" in r.message
    ]
    assert len(warn_records) == 2
