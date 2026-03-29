"""
Unit tests for the cron_parser module.
"""
from cron_parser import (
    CRON_PRESETS,
    is_croniter_available,
    validate_cron_expression,
    expand_preset,
    get_next_n_run_times,
    describe_cron_expression,
    get_preset_list,
)


class TestCroniterAvailability:
    """Tests for croniter availability check."""

    def test_is_croniter_available_returns_true(self):
        """is_croniter_available() returns True when croniter is installed."""
        # croniter should be installed in test environment
        assert is_croniter_available() is True


class TestValidateCronExpression:
    """Tests for validate_cron_expression()."""

    def test_validates_empty_expression(self):
        """Empty expression is invalid."""
        is_valid, error = validate_cron_expression("")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_validates_whitespace_only(self):
        """Whitespace-only expression is invalid."""
        is_valid, error = validate_cron_expression("   ")
        assert is_valid is False
        assert "empty" in error.lower()

    def test_validates_preset_name(self):
        """Preset names are valid."""
        is_valid, error = validate_cron_expression("hourly")
        assert is_valid is True
        assert error == ""

    def test_validates_preset_name_case_insensitive(self):
        """Preset validation is case-insensitive."""
        is_valid, error = validate_cron_expression("DAILY_MIDNIGHT")
        assert is_valid is True

    def test_validates_simple_cron(self):
        """Simple cron expression is valid."""
        is_valid, error = validate_cron_expression("0 * * * *")
        assert is_valid is True
        assert error == ""

    def test_validates_complex_cron(self):
        """Complex cron expression is valid."""
        is_valid, error = validate_cron_expression("*/15 9-17 * * 1-5")
        assert is_valid is True

    def test_rejects_invalid_cron(self):
        """Invalid cron expression is rejected."""
        is_valid, error = validate_cron_expression("invalid cron")
        assert is_valid is False
        assert "invalid" in error.lower() or "error" in error.lower()

    def test_rejects_wrong_field_count(self):
        """Cron with wrong field count is rejected."""
        is_valid, error = validate_cron_expression("0 * * *")  # Only 4 fields
        assert is_valid is False


class TestExpandPreset:
    """Tests for expand_preset()."""

    def test_expands_hourly_preset(self):
        """'hourly' expands to correct cron expression."""
        assert expand_preset("hourly") == "0 * * * *"

    def test_expands_daily_midnight(self):
        """'daily_midnight' expands correctly."""
        assert expand_preset("daily_midnight") == "0 0 * * *"

    def test_expands_daily_3am(self):
        """'daily_3am' expands correctly."""
        assert expand_preset("daily_3am") == "0 3 * * *"

    def test_expands_case_insensitive(self):
        """Preset expansion is case-insensitive."""
        assert expand_preset("HOURLY") == "0 * * * *"
        assert expand_preset("Hourly") == "0 * * * *"

    def test_strips_whitespace(self):
        """Preset expansion strips whitespace."""
        assert expand_preset("  hourly  ") == "0 * * * *"

    def test_returns_unchanged_if_not_preset(self):
        """Non-preset expressions are returned unchanged."""
        expr = "*/5 * * * *"
        assert expand_preset(expr) == expr

    def test_all_presets_expand(self):
        """All defined presets have expansions."""
        for preset in CRON_PRESETS:
            result = expand_preset(preset)
            assert result != preset  # Should be a different (expanded) value


class TestGetNextNRunTimes:
    """Tests for get_next_n_run_times()."""

    def test_returns_correct_count(self):
        """Returns requested number of run times."""
        result = get_next_n_run_times("* * * * *", n=5)
        assert len(result) == 5

    def test_times_are_ascending(self):
        """Run times are in ascending order."""
        result = get_next_n_run_times("* * * * *", n=5)
        for i in range(len(result) - 1):
            assert result[i] < result[i + 1]

    def test_respects_cron_schedule(self):
        """Times follow the cron schedule."""
        result = get_next_n_run_times("0 * * * *", n=3)  # Every hour
        # Each should be 1 hour apart
        for i in range(len(result) - 1):
            diff = (result[i + 1] - result[i]).total_seconds()
            assert diff == 3600  # 1 hour

    def test_returns_empty_for_invalid(self):
        """Returns empty list for invalid cron."""
        result = get_next_n_run_times("invalid")
        assert result == []


class TestDescribeCronExpression:
    """Tests for describe_cron_expression()."""

    def test_describes_hourly_preset(self):
        """Describes 'hourly' preset."""
        desc = describe_cron_expression("hourly")
        assert "hour" in desc.lower()

    def test_describes_daily_midnight(self):
        """Describes 'daily_midnight' preset."""
        desc = describe_cron_expression("daily_midnight")
        assert "midnight" in desc.lower()

    def test_describes_every_minute(self):
        """Describes '* * * * *' (every minute)."""
        desc = describe_cron_expression("* * * * *")
        assert "minute" in desc.lower()

    def test_describes_every_hour(self):
        """Describes '0 * * * *' (every hour)."""
        desc = describe_cron_expression("0 * * * *")
        assert "hour" in desc.lower()

    def test_describes_minute_interval(self):
        """Describes '*/15 * * * *' (every 15 minutes)."""
        desc = describe_cron_expression("*/15 * * * *")
        assert "15" in desc and "minute" in desc.lower()

    def test_describes_specific_time(self):
        """Describes '30 9 * * *' (9:30 AM)."""
        desc = describe_cron_expression("30 9 * * *")
        assert "9" in desc or "AM" in desc

    def test_describes_noon(self):
        """Describes '0 12 * * *' (noon)."""
        desc = describe_cron_expression("0 12 * * *")
        assert "noon" in desc.lower() or "12" in desc

    def test_describes_weekday(self):
        """Describes weekday in expression."""
        desc = describe_cron_expression("0 0 * * 1")
        assert "monday" in desc.lower()

    def test_describes_month(self):
        """Describes month in expression."""
        desc = describe_cron_expression("0 0 1 3 *")
        assert "mar" in desc.lower()

    def test_handles_invalid_field_count(self):
        """Handles expression with wrong field count."""
        desc = describe_cron_expression("0 * *")
        assert "invalid" in desc.lower()


class TestGetPresetList:
    """Tests for get_preset_list()."""

    def test_returns_list_of_dicts(self):
        """Returns list of dictionaries."""
        result = get_preset_list()
        assert isinstance(result, list)
        assert all(isinstance(item, dict) for item in result)

    def test_includes_all_presets(self):
        """Includes all defined presets."""
        result = get_preset_list()
        names = [item["name"] for item in result]
        for preset in CRON_PRESETS:
            assert preset in names

    def test_each_preset_has_required_fields(self):
        """Each preset has name, expression, and description."""
        result = get_preset_list()
        for item in result:
            assert "name" in item
            assert "expression" in item
            assert "description" in item


