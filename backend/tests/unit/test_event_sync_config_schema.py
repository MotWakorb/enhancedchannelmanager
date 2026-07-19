"""Write-time validation of event_sync_config (bead ti939.1.3).

Covers every branch of ``channel_pipeline_schema.validate_event_sync_config``:
the mandatory-scoping rail (master present, secondaries non-empty, master not
in secondaries), safe_regex compilation of operator parse patterns, the
attach_threshold [0, 1] bounds (0.80 default imported from
services.event_sync_matcher — operator-authoritative, lowerable per rule),
default filling, and the teaching-error format (field, got, expected, doc
link).
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
        # bead krkm4: the time-window gate is enforced by default.
        assert config["enforce_time_window"] is True

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

    def test_enforce_time_window_false_passes(self):
        # bead krkm4: explicit opt-out of the candidacy gate.
        config = _valid_config(enforce_time_window=False)
        assert validate_event_sync_config(config) == []
        assert config["enforce_time_window"] is False

    def test_enforce_time_window_non_bool_rejected(self):
        errors = validate_event_sync_config(
            _valid_config(enforce_time_window="yes")
        )
        assert any("enforce_time_window" in e for e in errors)

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

    def test_attach_threshold_below_default_accepted(self):
        # bead krkm4-sibling: 0.80 is the DEFAULT, not a hard floor.
        # Operator-authoritative — any value in [0, 1] is accepted and stored
        # verbatim.
        for value in (0.79, 0.5, 0.0):
            config = _valid_config(attach_threshold=value)
            assert validate_event_sync_config(config) == []
            assert config["attach_threshold"] == value

    def test_attach_threshold_out_of_bounds_rejected(self):
        # Only the [0, 1] bounds are enforced now.
        for bad in (-0.1, 1.1):
            errors = validate_event_sync_config(
                _valid_config(attach_threshold=bad)
            )
            assert any("attach_threshold" in e for e in errors)

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_enabled_non_bool_rejected(self, bad):
        errors = validate_event_sync_config(_valid_config(enabled=bad))
        assert any("enabled" in e for e in errors)

    # --- max_attach_per_run (bead ti939.2.1 — per-run attach cap) --------

    def test_max_attach_per_run_default_filled(self):
        from services.event_sync_resolver import DEFAULT_MAX_ATTACH_PER_RUN
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert config["max_attach_per_run"] == DEFAULT_MAX_ATTACH_PER_RUN

    @pytest.mark.parametrize("good", [1, 100, 1000])
    def test_max_attach_per_run_valid_values(self, good):
        config = _valid_config(max_attach_per_run=good)
        assert validate_event_sync_config(config) == []
        assert config["max_attach_per_run"] == good

    @pytest.mark.parametrize("bad", [True, False, "100", 0, -1, 1001, 1.5])
    def test_max_attach_per_run_invalid_rejected(self, bad):
        errors = validate_event_sync_config(
            _valid_config(max_attach_per_run=bad)
        )
        assert any("max_attach_per_run" in e for e in errors)

    def test_max_attach_per_run_ceiling_error_teaches(self):
        errors = validate_event_sync_config(
            _valid_config(max_attach_per_run=99999)
        )
        assert len(errors) == 1
        assert "1000" in errors[0]
        assert "cap" in errors[0]


class TestAutoRunFlag:
    """auto_run opt-in flag (bead ti939.3.1 — Phase 2 unattended runs).

    The flag is the EXPLICIT per-rule opt-in for watermark auto-runs.
    Default OFF is a safety property: an absent key must always read as
    false, and existing stored configs (which predate the key) must
    validate unchanged.
    """

    def test_auto_run_default_filled_false(self):
        """Absent auto_run is filled IN PLACE as False — default off."""
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert config["auto_run"] is False

    @pytest.mark.parametrize("good", [True, False])
    def test_auto_run_bool_accepted(self, good):
        config = _valid_config(auto_run=good)
        assert validate_event_sync_config(config) == []
        assert config["auto_run"] is good

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_auto_run_non_bool_rejected(self, bad):
        config = _valid_config()
        config["auto_run"] = bad
        errors = validate_event_sync_config(config)
        assert any("auto_run" in e for e in errors)

    def test_existing_stored_config_without_auto_run_still_validates(self):
        """Backward compat: a pre-Phase-2 stored config (no auto_run key)
        re-validates clean at run time — the rail that keeps existing rules
        running exactly as before."""
        stored = _valid_config(
            time_window_minutes=30,
            attach_threshold=0.85,
            max_attach_per_run=50,
            enabled=True,
        )
        assert validate_event_sync_config(stored) == []
        assert stored["auto_run"] is False


class TestRefreshProvidersBeforeRunFlag:
    """refresh_providers_before_run opt-in flag (bead y8yby).

    Pre-refreshes the rule's M3U providers before a MANUAL run (live Run AND
    dry-run Test). Same default-off / absent-reads-false / backward-compat
    convention as auto_run: an absent key must always read as false and
    pre-existing stored configs must validate unchanged.
    """

    def test_default_filled_false(self):
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert config["refresh_providers_before_run"] is False

    @pytest.mark.parametrize("good", [True, False])
    def test_bool_accepted_and_round_trips(self, good):
        config = _valid_config(refresh_providers_before_run=good)
        assert validate_event_sync_config(config) == []
        assert config["refresh_providers_before_run"] is good

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_non_bool_rejected(self, bad):
        config = _valid_config()
        config["refresh_providers_before_run"] = bad
        errors = validate_event_sync_config(config)
        assert any("refresh_providers_before_run" in e for e in errors)

    def test_existing_stored_config_without_key_still_validates(self):
        """Backward compat: a stored config that predates the flag validates
        clean and reads as false — manual-run behavior unchanged."""
        stored = _valid_config(
            time_window_minutes=30,
            attach_threshold=0.85,
            max_attach_per_run=50,
            enabled=True,
        )
        assert validate_event_sync_config(stored) == []
        assert stored["refresh_providers_before_run"] is False


class TestIncludeMasterGroupStreamsFlag:
    """include_master_group_streams flag (bead 6xxmp — master self-attach).

    Same default-fill/safety convention as auto_run: absent reads as false,
    and pre-existing stored configs validate unchanged.
    """

    def test_default_filled_false(self):
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert config["include_master_group_streams"] is False

    @pytest.mark.parametrize("good", [True, False])
    def test_bool_accepted(self, good):
        config = _valid_config(include_master_group_streams=good)
        assert validate_event_sync_config(config) == []
        assert config["include_master_group_streams"] is good

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_non_bool_rejected(self, bad):
        config = _valid_config()
        config["include_master_group_streams"] = bad
        errors = validate_event_sync_config(config)
        assert any("include_master_group_streams" in e for e in errors)

    def test_master_still_forbidden_in_secondary_list(self):
        # The flag is the sanctioned path — it does NOT relax the rail that
        # forbids the master id inside secondary_group_ids.
        config = _valid_config(
            master_group_id=10,
            secondary_group_ids=[10, 20],
            include_master_group_streams=True,
        )
        errors = validate_event_sync_config(config)
        assert any("secondary_group_ids" in e for e in errors)

    def test_empty_secondary_allowed_with_flag(self):
        # bead 3ux85: the pure same-named-group case — master group is the
        # only stream source, so an empty secondary list is valid WITH the
        # flag on (and both None and [] normalize to []).
        for empty in ([], None):
            config = _valid_config(include_master_group_streams=True)
            if empty is None:
                del config["secondary_group_ids"]
            else:
                config["secondary_group_ids"] = empty
            assert validate_event_sync_config(config) == []
            assert config["secondary_group_ids"] == []

    def test_empty_secondary_rejected_without_flag(self):
        config = _valid_config(secondary_group_ids=[])
        errors = validate_event_sync_config(config)
        assert any("secondary_group_ids" in e for e in errors)


class TestAssumeCurrentDateFlag:
    """assume_current_date flag (bead assume-current-date — dateless opt-in).

    Same default-fill/safety convention as auto_run: absent reads as false,
    and pre-existing stored configs validate unchanged.
    """

    def test_default_filled_false(self):
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert config["assume_current_date"] is False

    @pytest.mark.parametrize("good", [True, False])
    def test_bool_accepted(self, good):
        config = _valid_config(assume_current_date=good)
        assert validate_event_sync_config(config) == []
        assert config["assume_current_date"] is good

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_non_bool_rejected(self, bad):
        config = _valid_config()
        config["assume_current_date"] = bad
        errors = validate_event_sync_config(config)
        assert any("assume_current_date" in e for e in errors)


class TestDemoteStaleDatelessFlag:
    """demote_stale_dateless knob (bead jqwfq — stale-dateless demote rail).

    DEFAULT TRUE — the rail guards assume_current_date users by default;
    turning it OFF is the recurring-daily-event escape hatch. Absent-key
    default-fill means every stored pre-knob config validates unchanged
    and gets the guard.
    """

    def test_default_filled_true(self):
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert config["demote_stale_dateless"] is True

    @pytest.mark.parametrize("good", [True, False])
    def test_bool_accepted(self, good):
        config = _valid_config(demote_stale_dateless=good)
        assert validate_event_sync_config(config) == []
        assert config["demote_stale_dateless"] is good

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_non_bool_rejected(self, bad):
        config = _valid_config()
        config["demote_stale_dateless"] = bad
        errors = validate_event_sync_config(config)
        assert any("demote_stale_dateless" in e for e in errors)


class TestParseMasterFromStreamFlag:
    """parse_master_from_stream flag (bead parse-from-stream)."""

    def test_default_filled_false(self):
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert config["parse_master_from_stream"] is False

    @pytest.mark.parametrize("good", [True, False])
    def test_bool_accepted(self, good):
        config = _valid_config(parse_master_from_stream=good)
        assert validate_event_sync_config(config) == []
        assert config["parse_master_from_stream"] is good

    @pytest.mark.parametrize("bad", ["true", 1, 0])
    def test_non_bool_rejected(self, bad):
        config = _valid_config()
        config["parse_master_from_stream"] = bad
        errors = validate_event_sync_config(config)
        assert any("parse_master_from_stream" in e for e in errors)


class TestDummyEpgProfileId:
    """dummy_epg_profile_id reference (bead ti939.3.3 — dummy EPG
    auto-assignment for master event channels).

    OPTIONAL: absent means the feature is off, and the key is NOT
    default-filled. When present it must be a positive int that RESOLVES
    to an existing DummyEPGProfile row — a dangling reference is refused
    with a teaching error at save time and by the engine's per-run
    re-validation. Enabled-ness is deliberately NOT validated (a
    temporarily disabled profile must not kill the rule's attach path).
    """

    @pytest.fixture()
    def profile_db(self, monkeypatch):
        """In-memory DB with one enabled + one disabled profile; patches
        database._SessionLocal so the validator's direct get_session()
        call resolves to it (mirrors conftest.async_client's approach)."""
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        import database
        from models import DummyEPGProfile

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=engine)
        session_local = sessionmaker(
            autocommit=False, autoflush=False, bind=engine
        )
        session = session_local()
        session.add(DummyEPGProfile(id=7, name="Events EPG", enabled=True))
        session.add(DummyEPGProfile(id=8, name="Disabled EPG", enabled=False))
        session.commit()
        session.close()
        monkeypatch.setattr(database, "_SessionLocal", session_local)
        try:
            yield
        finally:
            database.Base.metadata.drop_all(bind=engine)
            engine.dispose()

    def test_absent_key_is_feature_off_and_not_filled(self):
        """No dummy_epg_profile_id → valid, and the key is NOT
        default-filled (absent means OFF, not null)."""
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert "dummy_epg_profile_id" not in config

    def test_existing_profile_id_accepted(self, profile_db):
        config = _valid_config(dummy_epg_profile_id=7)
        assert validate_event_sync_config(config) == []
        assert config["dummy_epg_profile_id"] == 7

    def test_disabled_profile_id_still_validates(self, profile_db):
        """Enabled-ness is a run-time concern (warning + EPG step skip),
        never a validation error — otherwise disabling a profile would
        invalidate the rule and kill its attach path."""
        config = _valid_config(dummy_epg_profile_id=8)
        assert validate_event_sync_config(config) == []

    def test_nonexistent_profile_id_rejected_with_teaching_error(
        self, profile_db
    ):
        config = _valid_config(dummy_epg_profile_id=999)
        errors = validate_event_sync_config(config)
        assert len(errors) == 1
        err = errors[0]
        assert "event_sync_config.dummy_epg_profile_id" in err
        assert "999" in err
        assert "EXISTING dummy EPG profile" in err
        assert "docs/event_sync.md" in err

    @pytest.mark.parametrize("bad", [True, False, 0, -1, "7", 1.5])
    def test_non_positive_int_rejected(self, bad):
        """Shape errors are caught WITHOUT a DB lookup (bool is an int
        subclass — rejected like _is_group_id does)."""
        config = _valid_config()
        config["dummy_epg_profile_id"] = bad
        errors = validate_event_sync_config(config)
        assert any("dummy_epg_profile_id" in e for e in errors)

    def test_typoed_key_rejected_by_unknown_key_rail(self):
        config = _valid_config()
        config["dummy_epg_profile"] = 7  # missing _id suffix
        errors = validate_event_sync_config(config)
        assert any("dummy_epg_profile" in e for e in errors)


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
            "attach_threshold": 1.5,  # out of [0, 1] bounds
            "enabled": "yes",
            "bogus_key": 1,
        }
        errors = validate_event_sync_config(config)
        assert len(errors) >= 6
        assert all("docs/event_sync.md" in e for e in errors)


class TestProviderScopedSchema:
    """Nested provider-scoped shape (bead 3p2af/2c2vz).

    Canonical config is {master:{group_id,m3u_account_id}, secondary:[...]};
    a legacy flat config auto-upgrades on read (no data migration); the flat
    keys are kept in sync so existing readers are untouched.
    """

    def test_legacy_flat_config_upgrades_to_nested(self):
        config = {"master_group_id": 10, "secondary_group_ids": [20, 30]}
        assert validate_event_sync_config(config) == []
        assert config["master"] == {"group_id": 10, "m3u_account_id": None}
        assert config["secondary"] == [
            {"group_id": 20, "m3u_account_id": None},
            {"group_id": 30, "m3u_account_id": None},
        ]
        # derived flat keys stay in sync for existing readers
        assert config["master_group_id"] == 10
        assert config["secondary_group_ids"] == [20, 30]

    def test_nested_config_derives_flat_keys(self):
        config = {
            "master": {"group_id": 10, "m3u_account_id": 3},
            "secondary": [{"group_id": 10, "m3u_account_id": 7}],
        }
        assert validate_event_sync_config(config) == []
        assert config["master_group_id"] == 10
        assert config["secondary_group_ids"] == [10]
        assert config["master"]["m3u_account_id"] == 3
        assert config["secondary"][0]["m3u_account_id"] == 7

    def test_same_group_different_provider_is_valid(self):
        # The whole point: master group 10 / provider 3, secondary group 10 /
        # provider 7 — same group, different provider — is allowed.
        config = {
            "master": {"group_id": 10, "m3u_account_id": 3},
            "secondary": [{"group_id": 10, "m3u_account_id": 7}],
        }
        assert validate_event_sync_config(config) == []

    def test_identical_group_provider_pair_rejected(self):
        config = {
            "master": {"group_id": 10, "m3u_account_id": 3},
            "secondary": [{"group_id": 10, "m3u_account_id": 3}],
        }
        errors = validate_event_sync_config(config)
        assert any("secondary_group_ids" in e for e in errors)

    def test_same_group_null_provider_both_still_rejected(self):
        # Legacy behavior preserved: group 10 (any provider) as both master and
        # secondary is still forbidden (identical pair (10, None)).
        config = {"master_group_id": 10, "secondary_group_ids": [10]}
        errors = validate_event_sync_config(config)
        assert any("secondary_group_ids" in e for e in errors)

    @pytest.mark.parametrize("bad_pid", ["3", 0, -1, True, 1.5])
    def test_bad_provider_id_rejected(self, bad_pid):
        config = {
            "master": {"group_id": 10, "m3u_account_id": bad_pid},
            "secondary": [{"group_id": 20, "m3u_account_id": None}],
        }
        errors = validate_event_sync_config(config)
        assert any("master_group_id" in e for e in errors)

    def test_null_provider_means_whole_group(self):
        config = {
            "master": {"group_id": 10, "m3u_account_id": None},
            "secondary": [{"group_id": 20, "m3u_account_id": None}],
        }
        assert validate_event_sync_config(config) == []
        assert config["master_group_id"] == 10


class TestPromotionKeys:
    """Unmatched-stream promotion config (bead ti939.4.1).

    The opt-in contract: ABSENT keys mean the feature is invisible and the
    validator must not add any of them (the AC-1 byte-identical rail at the
    schema layer); an ENABLED config requires a dedicated target group and
    gets the max_promote_per_run default filled.
    """

    def test_absent_keys_stay_absent(self):
        config = _valid_config()
        assert validate_event_sync_config(config) == []
        assert "promote_unmatched" not in config
        assert "promote_target_group_id" not in config
        assert "max_promote_per_run" not in config

    def test_enabled_requires_target_group(self):
        config = _valid_config(promote_unmatched=True)
        errors = validate_event_sync_config(config)
        assert any("promote_target_group_id" in e for e in errors)
        assert any("required when promote_unmatched" in e for e in errors)

    def test_enabled_with_target_group_passes_and_fills_cap_default(self):
        from services.event_sync_promote import DEFAULT_MAX_PROMOTE_PER_RUN

        config = _valid_config(
            promote_unmatched=True, promote_target_group_id=40
        )
        assert validate_event_sync_config(config) == []
        assert config["max_promote_per_run"] == DEFAULT_MAX_PROMOTE_PER_RUN

    def test_disabled_config_does_not_fill_cap_default(self):
        config = _valid_config(promote_unmatched=False)
        assert validate_event_sync_config(config) == []
        assert "max_promote_per_run" not in config

    def test_target_group_must_not_be_master(self):
        config = _valid_config(
            promote_unmatched=True, promote_target_group_id=10
        )
        errors = validate_event_sync_config(config)
        assert any("NOT the master group" in e for e in errors)

    def test_target_group_must_not_be_secondary(self):
        config = _valid_config(
            promote_unmatched=True, promote_target_group_id=20
        )
        errors = validate_event_sync_config(config)
        assert any("NOT one of the secondary groups" in e for e in errors)

    @pytest.mark.parametrize("bad", ["yes", 1, None])
    def test_non_bool_promote_flag_rejected(self, bad):
        config = _valid_config(promote_unmatched=bad)
        if bad is None:
            # Explicit null is not a valid boolean either — but None is the
            # "absent" sentinel in get(); a literal None key is treated as
            # absent (feature off), matching the other optional flags.
            assert validate_event_sync_config(config) == []
        else:
            errors = validate_event_sync_config(config)
            assert any("promote_unmatched" in e for e in errors)

    @pytest.mark.parametrize("bad", [0, -1, True, "40", 1.5])
    def test_bad_target_group_rejected(self, bad):
        config = _valid_config(
            promote_unmatched=True, promote_target_group_id=bad
        )
        errors = validate_event_sync_config(config)
        assert any("promote_target_group_id" in e for e in errors)

    @pytest.mark.parametrize("bad", [0, -5, 201, True, "10", 2.5])
    def test_bad_cap_rejected(self, bad):
        config = _valid_config(
            promote_unmatched=True, promote_target_group_id=40,
            max_promote_per_run=bad,
        )
        errors = validate_event_sync_config(config)
        assert any("max_promote_per_run" in e for e in errors)

    def test_cap_bounds_accepted(self):
        from services.event_sync_promote import MAX_PROMOTE_CEILING

        for good in (1, 25, MAX_PROMOTE_CEILING):
            config = _valid_config(
                promote_unmatched=True, promote_target_group_id=40,
                max_promote_per_run=good,
            )
            assert validate_event_sync_config(config) == []
            assert config["max_promote_per_run"] == good

    def test_target_group_shape_validated_even_when_disabled(self):
        """An inert (disabled) target-group key still gets shape errors —
        a typo'd id must not lie dormant until the operator flips the
        toggle."""
        config = _valid_config(promote_target_group_id=-3)
        errors = validate_event_sync_config(config)
        assert any("promote_target_group_id" in e for e in errors)
