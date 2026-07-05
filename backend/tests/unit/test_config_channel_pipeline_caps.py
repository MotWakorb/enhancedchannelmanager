"""Tests for the GH #473 auto-creation OOM safety-valve caps (skg35).

``max_auto_created_channels_per_run`` and ``max_auto_creation_log_entries``
are surfaced via the settings API/UI so an operator can adjust them instead of
hand-editing settings.json. The config-layer validator normalizes the shared
``<= 0`` disable sentinel: a negative collapses to the single canonical
disabled value ``0`` while positive values pass through untouched (operators may
deliberately raise the cap for a large planned expansion).
"""
import pytest

from config import (
    DEFAULT_MAX_AUTO_CREATED_CHANNELS_PER_RUN,
    DEFAULT_MAX_AUTO_CREATION_LOG_ENTRIES,
    DispatcharrSettings,
    clear_settings_cache,
)


@pytest.fixture(autouse=True)
def reset_module_state():
    clear_settings_cache()
    yield
    clear_settings_cache()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_channel_cap_default_is_500():
    s = DispatcharrSettings()
    assert s.max_auto_created_channels_per_run == 500
    assert s.max_auto_created_channels_per_run == DEFAULT_MAX_AUTO_CREATED_CHANNELS_PER_RUN


def test_log_entries_cap_default_is_500():
    s = DispatcharrSettings()
    assert s.max_auto_creation_log_entries == 500
    assert s.max_auto_creation_log_entries == DEFAULT_MAX_AUTO_CREATION_LOG_ENTRIES


# ---------------------------------------------------------------------------
# Validator: negatives normalize to 0 (disabled); positives untouched
# ---------------------------------------------------------------------------


def test_negative_channel_cap_normalizes_to_zero():
    s = DispatcharrSettings(max_auto_created_channels_per_run=-5)
    assert s.max_auto_created_channels_per_run == 0


def test_negative_log_entries_cap_normalizes_to_zero():
    s = DispatcharrSettings(max_auto_creation_log_entries=-100)
    assert s.max_auto_creation_log_entries == 0


def test_zero_channel_cap_is_preserved_as_disabled_sentinel():
    s = DispatcharrSettings(max_auto_created_channels_per_run=0)
    assert s.max_auto_created_channels_per_run == 0


def test_positive_channel_cap_passes_through_unclamped():
    # An operator running a deliberate large expansion can raise it arbitrarily.
    s = DispatcharrSettings(max_auto_created_channels_per_run=10_000)
    assert s.max_auto_created_channels_per_run == 10_000


def test_positive_log_entries_cap_passes_through_unclamped():
    s = DispatcharrSettings(max_auto_creation_log_entries=2_000)
    assert s.max_auto_creation_log_entries == 2_000
