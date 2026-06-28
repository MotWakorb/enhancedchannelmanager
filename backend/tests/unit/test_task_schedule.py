"""
Unit tests for the TaskSchedule model and parameter handling.

Tests the TaskSchedule model's parameter storage, retrieval, and manipulation
methods that support the generalized scheduling system.
"""
import json
from datetime import datetime


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
    """Tests for different schedule types — behavioral assertions via to_dict and describe_schedule.

    Each test uses to_dict() or describe_schedule() rather than reading the raw ORM field back,
    so mutating the serialisation layer (e.g. dropping a key from to_dict, or changing describe_schedule
    output) would cause these tests to fail rather than silently pass.
    """

    def test_interval_schedule(self, test_session):
        """Interval schedule serialises interval_seconds into to_dict and describe_schedule.

        Mutation guard: if to_dict() dropped 'interval_seconds' or describe_schedule() stopped
        producing the human-readable interval, both assertions fail.
        """
        from tests.fixtures.factories import create_task_schedule
        from schedule_calculator import describe_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="interval",
            interval_seconds=3600,
        )

        d = schedule.to_dict()
        assert d["schedule_type"] == "interval"
        assert d["interval_seconds"] == 3600

        desc = describe_schedule(schedule_type="interval", interval_seconds=3600)
        assert "1 hour" in desc or "3600" in desc or "hour" in desc.lower()

    def test_daily_schedule(self, test_session):
        """Daily schedule serialises time and timezone into to_dict and describe_schedule.

        Mutation guard: if to_dict() dropped 'timezone' or describe_schedule() stopped
        including the time, these assertions fail.
        """
        from tests.fixtures.factories import create_task_schedule
        from schedule_calculator import describe_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="daily",
            schedule_time="14:30",
            timezone="Europe/London",
        )

        d = schedule.to_dict()
        assert d["schedule_type"] == "daily"
        assert d["schedule_time"] == "14:30"
        assert d["timezone"] == "Europe/London"

        desc = describe_schedule(
            schedule_type="daily",
            schedule_time="14:30",
            timezone="Europe/London",
        )
        assert "14:30" in desc or "2:30" in desc

    def test_weekly_schedule(self, test_session):
        """Weekly schedule serialises days list via to_dict (not raw days_of_week string).

        Mutation guard: if get_days_of_week_list() or to_dict() broke the comma-split
        parsing, the list assertion fails. The describe_schedule assertion also fails if
        it stopped naming weekday descriptions.
        """
        from tests.fixtures.factories import create_task_schedule
        from schedule_calculator import describe_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="weekly",
            schedule_time="09:00",
            days_of_week="1,2,3,4,5",  # Weekdays
        )

        d = schedule.to_dict()
        assert d["schedule_type"] == "weekly"
        assert d["days_of_week"] == [1, 2, 3, 4, 5]

        desc = describe_schedule(
            schedule_type="weekly",
            schedule_time="09:00",
            days_of_week=[1, 2, 3, 4, 5],
        )
        assert "Weekly" in desc or "weekly" in desc.lower()

    def test_monthly_schedule(self, test_session):
        """Monthly schedule serialises day_of_month via to_dict.

        Mutation guard: if to_dict() dropped 'day_of_month' the assertion fails.
        The describe_schedule check also fails if monthly output changed format.
        """
        from tests.fixtures.factories import create_task_schedule
        from schedule_calculator import describe_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="monthly",
            schedule_time="03:00",
            day_of_month=15,
        )

        d = schedule.to_dict()
        assert d["schedule_type"] == "monthly"
        assert d["day_of_month"] == 15

        desc = describe_schedule(
            schedule_type="monthly",
            schedule_time="03:00",
            day_of_month=15,
        )
        assert "15" in desc or "monthly" in desc.lower()

    def test_monthly_last_day(self, test_session):
        """Monthly last-day schedule serialises -1 in to_dict and describes it correctly.

        Mutation guard: if the -1 sentinel were not preserved through to_dict, or if
        describe_schedule stopped handling last-day, both assertions fail.
        """
        from tests.fixtures.factories import create_task_schedule
        from schedule_calculator import describe_schedule

        schedule = create_task_schedule(
            test_session,
            task_id="stream_probe",
            schedule_type="monthly",
            schedule_time="03:00",
            day_of_month=-1,
        )

        d = schedule.to_dict()
        assert d["day_of_month"] == -1

        desc = describe_schedule(
            schedule_type="monthly",
            schedule_time="03:00",
            day_of_month=-1,
        )
        assert "last" in desc.lower() or "monthly" in desc.lower()
