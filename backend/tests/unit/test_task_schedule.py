"""
Unit tests for the TaskSchedule model and parameter handling.

Tests the TaskSchedule model's parameter storage, retrieval, and manipulation
methods that support the generalized scheduling system.
"""
import pytest
import json
from datetime import datetime, timedelta


class TestTaskScheduleParameters:
    """Tests for TaskSchedule parameter handling methods."""

    def test_set_parameters_stores_json(self, test_session):
        """set_parameters() stores parameters as JSON string."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        params = {"batch_size": 20, "timeout": 60, "channel_groups": ["Sports", "News"]}

        schedule.set_parameters(params)
        test_session.commit()

        # Verify raw storage is JSON string
        assert schedule.parameters is not None
        stored = json.loads(schedule.parameters)
        assert stored == params

    def test_get_parameters_returns_dict(self, test_session):
        """get_parameters() returns parameters as dict."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        params = {"batch_size": 20, "timeout": 60}
        schedule.set_parameters(params)
        test_session.commit()

        result = schedule.get_parameters()
        assert isinstance(result, dict)
        assert result["batch_size"] == 20
        assert result["timeout"] == 60

    def test_get_parameters_returns_empty_dict_when_none(self, test_session):
        """get_parameters() returns empty dict when parameters is None."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        # Don't set parameters
        result = schedule.get_parameters()
        assert result == {}

    def test_get_parameter_returns_value(self, test_session):
        """get_parameter() returns specific parameter value."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.set_parameters({"batch_size": 25, "timeout": 45})
        test_session.commit()

        assert schedule.get_parameter("batch_size") == 25
        assert schedule.get_parameter("timeout") == 45

    def test_get_parameter_returns_default_when_missing(self, test_session):
        """get_parameter() returns default when key is missing."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.set_parameters({"batch_size": 25})
        test_session.commit()

        result = schedule.get_parameter("nonexistent", default=100)
        assert result == 100

    def test_get_parameter_returns_none_by_default(self, test_session):
        """get_parameter() returns None when key is missing and no default."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.set_parameters({})
        test_session.commit()

        result = schedule.get_parameter("nonexistent")
        assert result is None

    def test_parameters_with_nested_structures(self, test_session):
        """Parameters can contain nested structures."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        params = {
            "channel_groups": ["Sports", "News", "Movies"],
            "options": {"skip_recent": True, "max_retries": 3},
        }
        schedule.set_parameters(params)
        test_session.commit()

        result = schedule.get_parameters()
        assert result["channel_groups"] == ["Sports", "News", "Movies"]
        assert result["options"]["skip_recent"] is True
        assert result["options"]["max_retries"] == 3

    def test_parameters_with_empty_array(self, test_session):
        """Parameters can contain empty arrays."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.set_parameters({"channel_groups": []})
        test_session.commit()

        result = schedule.get_parameters()
        assert result["channel_groups"] == []


class TestTaskScheduleToDict:
    """Tests for TaskSchedule.to_dict() serialization."""

    def test_to_dict_includes_parameters(self, test_session):
        """to_dict() includes parameters in output."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.set_parameters({"batch_size": 30})
        test_session.commit()

        result = schedule.to_dict()
        assert "parameters" in result
        assert result["parameters"]["batch_size"] == 30

    def test_to_dict_includes_last_run_at(self, test_session):
        """to_dict() includes last_run_at in output."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(test_session, task_id="stream_probe")
        schedule.last_run_at = datetime.utcnow()
        test_session.commit()

        result = schedule.to_dict()
        assert "last_run_at" in result
        assert result["last_run_at"] is not None

    def test_to_dict_includes_all_schedule_fields(self, test_session):
        """to_dict() includes all standard schedule fields."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            name="Morning Probe",
            enabled=True,
            schedule_type="daily",
            schedule_time="06:00",
            timezone="America/New_York",
        )

        result = schedule.to_dict()

        assert result["id"] == schedule.id
        assert result["task_id"] == "stream_probe"
        assert result["name"] == "Morning Probe"
        assert result["enabled"] is True
        assert result["schedule_type"] == "daily"
        assert result["schedule_time"] == "06:00"
        assert result["timezone"] == "America/New_York"


class TestTaskScheduleDaysOfWeek:
    """Tests for days_of_week handling."""

    def test_set_days_of_week_list(self, test_session):
        """set_days_of_week_list() stores days as comma-separated string."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session, task_id="stream_probe", schedule_type="weekly"
        )
        schedule.set_days_of_week_list([1, 3, 5])  # Mon, Wed, Fri
        test_session.commit()

        assert schedule.days_of_week == "1,3,5"

    def test_get_days_of_week_list(self, test_session):
        """get_days_of_week_list() returns list of integers."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="weekly",
            days_of_week="0,2,4,6",  # Sun, Tue, Thu, Sat
        )

        result = schedule.get_days_of_week_list()
        assert result == [0, 2, 4, 6]

    def test_get_days_of_week_list_returns_empty_for_none(self, test_session):
        """get_days_of_week_list() returns empty list when None."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session, task_id="stream_probe", schedule_type="daily", days_of_week=None
        )

        result = schedule.get_days_of_week_list()
        assert result == []


class TestTaskScheduleIntervalTypes:
    """Tests for different schedule types."""

    def test_interval_schedule(self, test_session):
        """Interval schedule stores interval_seconds."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="interval",
            interval_seconds=3600,
        )

        assert schedule.schedule_type == "interval"
        assert schedule.interval_seconds == 3600

    def test_daily_schedule(self, test_session):
        """Daily schedule stores time and timezone."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="daily",
            schedule_time="14:30",
            timezone="Europe/London",
        )

        assert schedule.schedule_type == "daily"
        assert schedule.schedule_time == "14:30"
        assert schedule.timezone == "Europe/London"

    def test_weekly_schedule(self, test_session):
        """Weekly schedule stores days and time."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="weekly",
            schedule_time="09:00",
            days_of_week="1,2,3,4,5",  # Weekdays
        )

        assert schedule.schedule_type == "weekly"
        assert schedule.get_days_of_week_list() == [1, 2, 3, 4, 5]

    def test_monthly_schedule(self, test_session):
        """Monthly schedule stores day of month."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="monthly",
            schedule_time="03:00",
            day_of_month=15,
        )

        assert schedule.schedule_type == "monthly"
        assert schedule.day_of_month == 15

    def test_monthly_last_day(self, test_session):
        """Monthly schedule can use -1 for last day."""
        from tests.fixtures.factories import create_task_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="monthly",
            schedule_time="03:00",
            day_of_month=-1,
        )

        assert schedule.day_of_month == -1
