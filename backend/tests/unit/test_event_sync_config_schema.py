"""Write-time validation of event_sync_config (bead ti939.1.3).

Covers every branch of ``channel_pipeline_schema.validate_event_sync_config``:
the mandatory-scoping rail (master present, secondaries non-empty, master not
in secondaries), safe_regex compilation of operator parse patterns, the
attach_threshold hard floor (imported from services.event_sync_matcher — the
single source of truth), default filling, and the teaching-error format
(field, got, expected, doc link).
"""
from __future__ import annotations

import pytest

from channel_pipeline_schema import validate_event_sync_config
from services.event_sync_matcher import (
    DEFAULT_TIME_WINDOW_MINUTES,
    EVENT_ATTACH_FLOOR,
)


def _valid_config(**overrides) -> dict:
    """Minimal valid config; kwargs override/extend."""
    config = {
        "master_group_id": 10,
        "secondary_group_ids": [20, 30],
    }
    config.update(overrides)
    return config


def _valid_pattern(**overrides) -> dict:
    pattern = {
        "name": "test-pattern",
        "title_pattern": r"^(?P<title>.+?)\s*@",
        "time_pattern": r"(?P<hour>\d{1,2}):(?P<minute>\d{2})",
        "date_pattern": r"@\s*(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})",
    }
    pattern.update(overrides)
    return pattern


class TestValidConfigs:
    def test_minimal_config_passes(self):
        assert validate_event_sync_config(_valid_config()) == []

    def test_minimal_config_fills_defaults(self):
        """Defaults are filled IN PLACE so stored configs are explicit."""
        config = _valid_config()
        validate_event_sync_config(config)
        assert config["time_window_minutes"] == DEFAULT_TIME_WINDOW_MINUTES
        assert config["attach_threshold"] == EVENT_ATTACH_FLOOR
        assert config["enabled"] is True

    def test_full_config_passes(self):
        config = _valid_config(
            patterns=[_valid_pattern()],
            group_patterns={"20": [_valid_pattern(name="grp")]},
            time_window_minutes=45,
            attach_threshold=0.9,
            enabled=False,
        )
        assert validate_event_sync_config(config) == []
        # Explicit values are not overwritten by default filling.
        assert config["time_window_minutes"] == 45
        assert config["attach_threshold"] == 0.9
        assert config["enabled"] is False

    def test_threshold_exactly_at_floor_passes(self):
        config = _valid_config(attach_threshold=EVENT_ATTACH_FLOOR)
        assert validate_event_sync_config(config) == []

    def test_group_patterns_int_keys_accepted(self):
        """Dict callers may use int keys; JSON round-trips produce strings."""
        config = _valid_config(group_patterns={20: [_valid_pattern()]})
        assert validate_event_sync_config(config) == []

    def test_group_patterns_for_master_group_accepted(self):
        config = _valid_config(group_patterns={"10": [_valid_pattern()]})
        assert validate_event_sync_config(config) == []


class TestConfigShape:
    @pytest.mark.parametrize("bad", [None, [], "config", 42, True])
    def test_non_dict_rejected(self, bad):
        errors = validate_event_sync_config(bad)
        assert len(errors) == 1
        assert "expected a JSON object" in errors[0]

    def test_unknown_top_level_key_rejected(self):
        """A typo'd optional key must not silently fall back to a default."""
        errors = validate_event_sync_config(
            _valid_config(attach_treshold=0.9)  # deliberate typo
        )
        assert any("attach_treshold" in e for e in errors)


class TestMandatoryScoping:
    """The anti-1,341 rail: scoping is schema-enforced, not convention."""

    @pytest.mark.parametrize("bad", [None, "10", 0, -1, True, 1.5])
    def test_master_group_id_invalid(self, bad):
        config = _valid_config()
        config["master_group_id"] = bad
        errors = validate_event_sync_config(config)
        assert any("master_group_id" in e for e in errors)

    def test_master_group_id_missing(self):
        config = _valid_config()
        del config["master_group_id"]
        errors = validate_event_sync_config(config)
        assert any("master_group_id" in e for e in errors)

    @pytest.mark.parametrize("bad", [None, [], {}, "20", [0], [-5], [True], ["20"], [20.5]])
    def test_secondary_group_ids_invalid(self, bad):
        config = _valid_config()
        config["secondary_group_ids"] = bad
        errors = validate_event_sync_config(config)
        assert any("secondary_group_ids" in e for e in errors)

    def test_secondary_group_ids_missing(self):
        config = _valid_config()
        del config["secondary_group_ids"]
        errors = validate_event_sync_config(config)
        assert any("secondary_group_ids" in e for e in errors)

    def test_master_in_secondaries_rejected(self):
        """A group cannot be both master and secondary — the scoping rail."""
        errors = validate_event_sync_config(
            _valid_config(secondary_group_ids=[10, 20])
        )
        assert any(
            "secondary_group_ids" in e and "master_group_id" in e
            for e in errors
        )


class TestPatterns:
    @pytest.mark.parametrize("bad", [[], "patterns", {}, 42])
    def test_patterns_not_a_nonempty_list_rejected(self, bad):
        errors = validate_event_sync_config(_valid_config(patterns=bad))
        assert any("patterns" in e for e in errors)

    def test_pattern_entry_not_dict_rejected(self):
        errors = validate_event_sync_config(_valid_config(patterns=["regex"]))
        assert any("patterns[0]" in e for e in errors)

    def test_pattern_missing_title_pattern_rejected(self):
        pattern = _valid_pattern()
        del pattern["title_pattern"]
        errors = validate_event_sync_config(_valid_config(patterns=[pattern]))
        assert any("patterns[0].title_pattern" in e for e in errors)

    def test_pattern_unknown_key_rejected(self):
        errors = validate_event_sync_config(
            _valid_config(patterns=[_valid_pattern(date_patern=r"@\d+")])
        )
        assert any("date_patern" in e for e in errors)

    def test_pattern_non_string_name_rejected(self):
        errors = validate_event_sync_config(
            _valid_config(patterns=[_valid_pattern(name=42)])
        )
        assert any("patterns[0].name" in e for e in errors)

    @pytest.mark.parametrize("field", ["title_pattern", "time_pattern", "date_pattern"])
    def test_invalid_regex_rejected_via_safe_regex(self, field):
        """Operator regex is the ReDoS surface (bd-ltjyx) — safe_regex at save time."""
        errors = validate_event_sync_config(
            _valid_config(patterns=[_valid_pattern(**{field: "(unclosed"})])
        )
        assert any(f"patterns[0].{field}" in e for e in errors)

    def test_over_length_regex_rejected(self):
        """safe_regex's pattern length cap applies at save time too."""
        errors = validate_event_sync_config(
            _valid_config(patterns=[_valid_pattern(title_pattern="(?P<title>a)" + "b" * 5000)])
        )
        assert any("patterns[0].title_pattern" in e for e in errors)

    @pytest.mark.parametrize("field", ["time_pattern", "date_pattern"])
    def test_non_string_optional_pattern_rejected(self, field):
        errors = validate_event_sync_config(
            _valid_config(patterns=[_valid_pattern(**{field: 42})])
        )
        assert any(f"patterns[0].{field}" in e for e in errors)


class TestGroupPatterns:
    @pytest.mark.parametrize("bad", [[], "x", 42])
    def test_group_patterns_not_dict_rejected(self, bad):
        errors = validate_event_sync_config(_valid_config(group_patterns=bad))
        assert any("group_patterns" in e for e in errors)

    def test_non_integer_key_rejected(self):
        errors = validate_event_sync_config(
            _valid_config(group_patterns={"abc": [_valid_pattern()]})
        )
        assert any("group_patterns['abc']" in e for e in errors)

    def test_out_of_scope_group_rejected(self):
        """Per-group patterns must target the master or a secondary."""
        errors = validate_event_sync_config(
            _valid_config(group_patterns={"99": [_valid_pattern()]})
        )
        assert any("group_patterns['99']" in e for e in errors)

    def test_invalid_regex_in_group_patterns_rejected(self):
        errors = validate_event_sync_config(
            _valid_config(group_patterns={
                "20": [_valid_pattern(title_pattern="(bad")]
            })
        )
        assert any("group_patterns['20'][0].title_pattern" in e for e in errors)

    def test_group_patterns_value_not_list_rejected(self):
        errors = validate_event_sync_config(
            _valid_config(group_patterns={"20": _valid_pattern()})
        )
        assert any("group_patterns['20']" in e for e in errors)


class TestMatchingKnobs:
    @pytest.mark.parametrize("bad", [True, "30", 0, -5, 1.5, 1441, 100000])
    def test_time_window_minutes_invalid(self, bad):
        errors = validate_event_sync_config(
            _valid_config(time_window_minutes=bad)
        )
        assert any("time_window_minutes" in e for e in errors)

    def test_time_window_ceiling_is_1440(self):
        """PR #612 review: an oversized window re-opens the same-teams-
        different-day false-positive class — capped at 24h, and the error
        teaches why."""
        assert validate_event_sync_config(
            _valid_config(time_window_minutes=1440)
        ) == []
        errors = validate_event_sync_config(
            _valid_config(time_window_minutes=1441)
        )
        assert len(errors) == 1
        assert "1440" in errors[0]
        assert "same-teams-different-day" in errors[0]

    @pytest.mark.parametrize("bad", [True, "0.9", -0.1, 1.1])
    def test_attach_threshold_invalid(self, bad):
        errors = validate_event_sync_config(
            _valid_config(attach_threshold=bad)
        )
        assert any("attach_threshold" in e for e in errors)

    def test_attach_threshold_below_floor_rejected(self):
        """The 0.80 floor is hard-clamped — schema-enforced, not convention."""
        errors = validate_event_sync_config(
            _valid_config(attach_threshold=0.79)
        )
        assert any(
            "attach_threshold" in e and str(EVENT_ATTACH_FLOOR) in e
            for e in errors
        )

    def test_floor_error_references_matcher_constant(self):
        """The floor in the error is the MATCHER's constant, not a duplicate."""
        errors = validate_event_sync_config(_valid_config(attach_threshold=0.5))
        assert any(str(EVENT_ATTACH_FLOOR) in e for e in errors)

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_enabled_non_bool_rejected(self, bad):
        errors = validate_event_sync_config(_valid_config(enabled=bad))
        assert any("enabled" in e for e in errors)


class TestTeachingErrorFormat:
    """Errors must teach: field, got, expected, doc link."""

    def test_error_carries_field_got_expected_and_doc_link(self):
        config = _valid_config()
        config["master_group_id"] = "not-an-int"
        errors = validate_event_sync_config(config)
        assert len(errors) == 1
        err = errors[0]
        assert "event_sync_config.master_group_id" in err  # field
        assert "'not-an-int'" in err                       # got
        assert "expected" in err                           # expected
        assert "docs/event_sync.md" in err                 # doc link

    def test_every_error_links_the_doc(self):
        config = {
            "master_group_id": True,
            "secondary_group_ids": [],
            "patterns": "nope",
            "time_window_minutes": -1,
            "attach_threshold": 0.1,
            "enabled": "yes",
            "bogus_key": 1,
        }
        errors = validate_event_sync_config(config)
        assert len(errors) >= 6
        assert all("docs/event_sync.md" in e for e in errors)
