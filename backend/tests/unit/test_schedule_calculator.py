"""
Unit tests for the schedule_calculator module.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from schedule_calculator import (
    DAY_NAMES,
    calculate_next_run,
    describe_schedule,
    get_seconds_until,
    format_relative_time,
    _describe_interval,
    _describe_days,
    _format_time,
)


class TestCalculateNextRunInterval:
    """Tests for interval-based schedule calculations."""

    def test_interval_from_now(self):
        """Interval calculates from now when no last_run."""
        result = calculate_next_run(
            schedule_type="interval",
            interval_seconds=3600,
        )
        expected = datetime.utcnow() + timedelta(seconds=3600)
        # Allow 2 second tolerance
        assert abs((result - expected).total_seconds()) < 2

    def test_interval_from_last_run(self):
        """Interval calculates from last_run when provided."""
        last_run = datetime.utcnow() - timedelta(seconds=1800)
        result = calculate_next_run(
            schedule_type="interval",
            interval_seconds=3600,
            last_run=last_run,
        )
        # Should be 30 minutes from now (60 min interval - 30 min since last)
        expected = last_run + timedelta(seconds=3600)
        assert abs((result - expected).total_seconds()) < 2

    def test_interval_past_last_run_schedules_from_now(self):
        """If calculated next_run is in past, schedule from now."""
        last_run = datetime.utcnow() - timedelta(hours=2)
        result = calculate_next_run(
            schedule_type="interval",
            interval_seconds=3600,
            last_run=last_run,
        )
        # Last run was 2 hours ago with 1 hour interval, so should be from now
        expected = datetime.utcnow() + timedelta(seconds=3600)
        assert abs((result - expected).total_seconds()) < 2

    def test_interval_returns_none_for_zero(self):
        """Returns None for zero interval."""
        result = calculate_next_run(
            schedule_type="interval",
            interval_seconds=0,
        )
        assert result is None

    def test_interval_returns_none_for_negative(self):
        """Returns None for negative interval."""
        result = calculate_next_run(
            schedule_type="interval",
            interval_seconds=-100,
        )
        assert result is None


class TestCalculateNextRunDaily:
    """Tests for daily schedule calculations."""

    def test_daily_future_time_today(self):
        """Daily schedule for future time today."""
        # Use a time that's definitely in the future
        future_hour = (datetime.utcnow().hour + 2) % 24
        result = calculate_next_run(
            schedule_type="daily",
            schedule_time=f"{future_hour:02d}:30",
            timezone="UTC",
        )
        assert result is not None
        assert result > datetime.utcnow()

    def test_daily_past_time_schedules_tomorrow(self):
        """Daily schedule for past time schedules tomorrow."""
        # Use a time that's definitely in the past
        past_hour = (datetime.utcnow().hour - 2) % 24
        result = calculate_next_run(
            schedule_type="daily",
            schedule_time=f"{past_hour:02d}:00",
            timezone="UTC",
        )
        assert result is not None
        # Should be tomorrow
        assert result.day != datetime.utcnow().day or result > datetime.utcnow()

    def test_daily_returns_none_without_time(self):
        """Returns None when schedule_time is missing."""
        result = calculate_next_run(
            schedule_type="daily",
            schedule_time=None,
        )
        assert result is None

    def test_daily_returns_none_for_invalid_time(self):
        """Returns None for invalid time format."""
        result = calculate_next_run(
            schedule_type="daily",
            schedule_time="invalid",
        )
        assert result is None


class TestCalculateNextRunWeekly:
    """Tests for weekly schedule calculations."""

    def test_weekly_returns_future_datetime(self):
        """Weekly schedule returns future datetime."""
        result = calculate_next_run(
            schedule_type="weekly",
            schedule_time="10:00",
            timezone="UTC",
            days_of_week=[0, 1, 2, 3, 4, 5, 6],  # Every day
        )
        assert result is not None
        assert result > datetime.utcnow()

    def test_weekly_returns_none_without_days(self):
        """Returns None when days_of_week is missing."""
        result = calculate_next_run(
            schedule_type="weekly",
            schedule_time="10:00",
            days_of_week=None,
        )
        assert result is None

    def test_weekly_returns_none_without_time(self):
        """Returns None when schedule_time is missing."""
        result = calculate_next_run(
            schedule_type="weekly",
            schedule_time=None,
            days_of_week=[1],
        )
        assert result is None


class TestCalculateNextRunBiweekly:
    """Tests for biweekly schedule calculations."""

    def test_biweekly_returns_future_datetime(self):
        """Biweekly schedule returns future datetime."""
        result = calculate_next_run(
            schedule_type="biweekly",
            schedule_time="10:00",
            timezone="UTC",
            days_of_week=[1],  # Monday
            week_parity=0,
        )
        assert result is not None
        assert result > datetime.utcnow()

    def test_biweekly_returns_none_without_days(self):
        """Returns None when days_of_week is missing."""
        result = calculate_next_run(
            schedule_type="biweekly",
            schedule_time="10:00",
            days_of_week=None,
        )
        assert result is None


class TestCalculateNextRunMonthly:
    """Tests for monthly schedule calculations."""

    def test_monthly_day_15(self):
        """Monthly on day 15."""
        result = calculate_next_run(
            schedule_type="monthly",
            schedule_time="09:00",
            timezone="UTC",
            day_of_month=15,
        )
        assert result is not None
        # Should be on day 15 or later
        assert result.day == 15 or result > datetime.utcnow()

    def test_monthly_last_day(self):
        """Monthly on last day (-1)."""
        result = calculate_next_run(
            schedule_type="monthly",
            schedule_time="09:00",
            timezone="UTC",
            day_of_month=-1,
        )
        assert result is not None
        # Last day varies by month, just check it's valid
        assert 28 <= result.day <= 31

    def test_monthly_returns_none_without_day(self):
        """Returns None when day_of_month is missing."""
        result = calculate_next_run(
            schedule_type="monthly",
            schedule_time="09:00",
            day_of_month=None,
        )
        assert result is None


class TestCalculateNextRunUnknownType:
    """Tests for unknown schedule types."""

    def test_unknown_type_returns_none(self):
        """Unknown schedule type returns None."""
        result = calculate_next_run(schedule_type="unknown")
        assert result is None


class TestDescribeSchedule:
    """Tests for describe_schedule()."""

    def test_describe_interval_seconds(self):
        """Describes interval in seconds."""
        desc = describe_schedule(
            schedule_type="interval",
            interval_seconds=30,
        )
        assert "30" in desc and "second" in desc.lower()

    def test_describe_interval_minutes(self):
        """Describes interval in minutes."""
        desc = describe_schedule(
            schedule_type="interval",
            interval_seconds=300,
        )
        assert "5" in desc and "minute" in desc.lower()

    def test_describe_interval_hours(self):
        """Describes interval in hours."""
        desc = describe_schedule(
            schedule_type="interval",
            interval_seconds=7200,
        )
        assert "2" in desc and "hour" in desc.lower()

    def test_describe_daily(self):
        """Describes daily schedule."""
        desc = describe_schedule(
            schedule_type="daily",
            schedule_time="09:00",
            timezone="America/New_York",
        )
        assert "daily" in desc.lower()
        assert "9:00" in desc or "AM" in desc

    def test_describe_weekly(self):
        """Describes weekly schedule."""
        desc = describe_schedule(
            schedule_type="weekly",
            schedule_time="10:00",
            days_of_week=[1],  # Monday
        )
        assert "weekly" in desc.lower()
        assert "monday" in desc.lower()

    def test_describe_biweekly(self):
        """Describes biweekly schedule."""
        desc = describe_schedule(
            schedule_type="biweekly",
            schedule_time="10:00",
            days_of_week=[1],  # Monday
        )
        assert "2 weeks" in desc.lower() or "bi" in desc.lower()

    def test_describe_monthly(self):
        """Describes monthly schedule."""
        desc = describe_schedule(
            schedule_type="monthly",
            schedule_time="09:00",
            day_of_month=15,
        )
        assert "monthly" in desc.lower()
        assert "15" in desc or "day" in desc.lower()

    def test_describe_monthly_last_day(self):
        """Describes monthly last day."""
        desc = describe_schedule(
            schedule_type="monthly",
            schedule_time="09:00",
            day_of_month=-1,
        )
        assert "last" in desc.lower()

    def test_describe_includes_timezone(self):
        """Includes timezone in description."""
        desc = describe_schedule(
            schedule_type="daily",
            schedule_time="09:00",
            timezone="America/New_York",
        )
        assert "America/New_York" in desc

    def test_describe_unknown_type(self):
        """Handles unknown schedule type."""
        desc = describe_schedule(schedule_type="unknown")
        assert "unknown" in desc.lower()


class TestDescribeInterval:
    """Tests for _describe_interval()."""

    def test_seconds(self):
        """Describes seconds."""
        assert "second" in _describe_interval(45).lower()

    def test_minutes_singular(self):
        """Describes 1 minute."""
        desc = _describe_interval(60)
        assert "1" in desc and "minute" in desc.lower()
        assert "minutes" not in desc.lower()  # Should be singular

    def test_minutes_plural(self):
        """Describes multiple minutes."""
        desc = _describe_interval(300)
        assert "5" in desc and "minutes" in desc.lower()

    def test_hours_singular(self):
        """Describes 1 hour."""
        desc = _describe_interval(3600)
        assert "1" in desc and "hour" in desc.lower()
        assert "hours" not in desc.lower()  # Should be singular

    def test_hours_plural(self):
        """Describes multiple hours."""
        desc = _describe_interval(7200)
        assert "2" in desc and "hours" in desc.lower()

    def test_hours_with_minutes(self):
        """Describes hours with remaining minutes."""
        desc = _describe_interval(5400)  # 1.5 hours
        assert "1h" in desc and "30m" in desc

    def test_days_singular(self):
        """Describes 1 day."""
        desc = _describe_interval(86400)
        assert "1" in desc and "day" in desc.lower()

    def test_days_with_hours(self):
        """Describes days with remaining hours."""
        desc = _describe_interval(90000)  # 25 hours
        assert "1d" in desc and "1h" in desc


class TestDescribeDays:
    """Tests for _describe_days()."""

    def test_empty_list(self):
        """Describes empty list."""
        assert "no days" in _describe_days([]).lower()

    def test_all_days(self):
        """Describes all days."""
        desc = _describe_days([0, 1, 2, 3, 4, 5, 6])
        assert "every day" in desc.lower()

    def test_weekdays(self):
        """Describes weekdays."""
        desc = _describe_days([1, 2, 3, 4, 5])
        assert "weekdays" in desc.lower()

    def test_weekends(self):
        """Describes weekends."""
        desc = _describe_days([0, 6])
        assert "weekends" in desc.lower()

    def test_single_day(self):
        """Describes single day."""
        desc = _describe_days([1])
        assert "monday" in desc.lower()


class TestFormatTime:
    """Tests for _format_time()."""

    def test_midnight(self):
        """Formats midnight."""
        assert "12:00 AM" in _format_time("00:00")

    def test_noon(self):
        """Formats noon."""
        assert "12:00 PM" in _format_time("12:00")

    def test_morning(self):
        """Formats morning time."""
        result = _format_time("09:30")
        assert "9:30" in result and "AM" in result

    def test_afternoon(self):
        """Formats afternoon time."""
        result = _format_time("14:30")
        assert "2:30" in result and "PM" in result

    def test_invalid_time(self):
        """Returns original for invalid time."""
        assert _format_time("invalid") == "invalid"


class TestGetSecondsUntil:
    """Tests for get_seconds_until()."""

    def test_future_time(self):
        """Returns positive seconds for future time."""
        future = datetime.utcnow() + timedelta(hours=1)
        result = get_seconds_until(future)
        assert 3500 < result < 3700  # Allow some tolerance

    def test_past_time(self):
        """Returns 0 for past time."""
        past = datetime.utcnow() - timedelta(hours=1)
        result = get_seconds_until(past)
        assert result == 0

    def test_none(self):
        """Returns None for None input."""
        assert get_seconds_until(None) is None


class TestFormatRelativeTime:
    """Tests for format_relative_time()."""

    def test_not_scheduled(self):
        """Formats None as 'Not scheduled'."""
        assert "not scheduled" in format_relative_time(None).lower()

    def test_now(self):
        """Formats immediate time as 'Now'."""
        result = format_relative_time(datetime.utcnow())
        assert result.lower() == "now"

    def test_seconds(self):
        """Formats time in seconds."""
        future = datetime.utcnow() + timedelta(seconds=30)
        result = format_relative_time(future)
        assert "s" in result.lower()

    def test_minutes(self):
        """Formats time in minutes."""
        future = datetime.utcnow() + timedelta(minutes=45)
        result = format_relative_time(future)
        assert "m" in result.lower()

    def test_hours(self):
        """Formats time in hours."""
        future = datetime.utcnow() + timedelta(hours=2)
        result = format_relative_time(future)
        assert "h" in result.lower()

    def test_days(self):
        """Formats time in days."""
        future = datetime.utcnow() + timedelta(days=3)
        result = format_relative_time(future)
        assert "d" in result.lower()
