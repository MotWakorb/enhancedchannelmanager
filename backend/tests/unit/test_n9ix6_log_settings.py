"""Persisted rotating-log settings recover defensively from manual input."""

import pytest

from config import DispatcharrSettings


MIB = 1024 * 1024


def test_rotating_log_defaults_nominally_retain_fifty_mib():
    settings = DispatcharrSettings()

    assert settings.backend_log_file_max_bytes == 10 * MIB
    assert settings.backend_log_file_backup_count == 4
    assert settings.backend_log_file_max_bytes * (
        settings.backend_log_file_backup_count + 1
    ) == 50 * MIB


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("backend_log_file_max_bytes", 0, 1 * MIB),
        ("backend_log_file_max_bytes", 101 * MIB, 100 * MIB),
        ("backend_log_file_max_bytes", "not-a-number", 10 * MIB),
        ("backend_log_file_backup_count", 0, 1),
        ("backend_log_file_backup_count", 10, 9),
        ("backend_log_file_backup_count", {}, 4),
    ],
)
def test_persisted_rotation_values_clamp_or_recover_to_default(field, raw, expected):
    settings = DispatcharrSettings(**{field: raw})

    assert getattr(settings, field) == expected
