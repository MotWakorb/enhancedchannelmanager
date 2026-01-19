"""
Schedule Calculator Module.

Provides timezone-aware schedule calculation for the new multi-schedule system.
Supports schedule types: interval, daily, weekly, biweekly, monthly.
"""
import logging
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Day name mapping for descriptions
DAY_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
DAY_NAMES_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]


def calculate_next_run(
    schedule_type: str,
    interval_seconds: Optional[int] = None,
    schedule_time: Optional[str] = None,
    timezone: Optional[str] = None,
    days_of_week: Optional[list] = None,
    day_of_month: Optional[int] = None,
    week_parity: Optional[int] = None,
    last_run: Optional[datetime] = None,
) -> Optional[datetime]:
    """
    Calculate the next run time for a schedule.

    Args:
        schedule_type: One of "interval", "daily", "weekly", "biweekly", "monthly"
        interval_seconds: For interval type - seconds between runs
        schedule_time: For daily/weekly/biweekly/monthly - time in HH:MM format
        timezone: IANA timezone name (e.g., "America/New_York")
        days_of_week: For weekly/biweekly - list of day numbers (0=Sunday, 6=Saturday)
        day_of_month: For monthly - day of month (1-31, or -1 for last day)
        week_parity: For biweekly - 0 or 1 to track odd/even weeks
        last_run: Optional last run time (UTC) for interval calculations

    Returns:
        Next run time in UTC, or None if schedule is invalid
    """
    tz = ZoneInfo(timezone) if timezone else ZoneInfo("UTC")
    now_utc = datetime.utcnow()
    now_local = datetime.now(tz)

    if schedule_type == "interval":
        return _calculate_interval_next_run(interval_seconds, last_run, now_utc)
    elif schedule_type == "daily":
        return _calculate_daily_next_run(schedule_time, tz, now_local)
    elif schedule_type == "weekly":
        return _calculate_weekly_next_run(schedule_time, tz, now_local, days_of_week)
    elif schedule_type == "biweekly":
        return _calculate_biweekly_next_run(schedule_time, tz, now_local, days_of_week, week_parity)
    elif schedule_type == "monthly":
        return _calculate_monthly_next_run(schedule_time, tz, now_local, day_of_month)
    else:
        logger.warning(f"Unknown schedule type: {schedule_type}")
        return None


def _calculate_interval_next_run(
    interval_seconds: Optional[int],
    last_run: Optional[datetime],
    now_utc: datetime,
) -> Optional[datetime]:
    """Calculate next run for interval-based schedule."""
    if not interval_seconds or interval_seconds <= 0:
        return None

    if last_run:
        next_run = last_run + timedelta(seconds=interval_seconds)
        # If next_run is in the past, calculate from now
        if next_run <= now_utc:
            next_run = now_utc + timedelta(seconds=interval_seconds)
        return next_run
    else:
        # No last run, schedule for interval from now
        return now_utc + timedelta(seconds=interval_seconds)


def _calculate_daily_next_run(
    schedule_time: Optional[str],
    tz: ZoneInfo,
    now_local: datetime,
) -> Optional[datetime]:
    """Calculate next run for daily schedule."""
    if not schedule_time:
        return None

    try:
        hour, minute = map(int, schedule_time.split(":"))
    except (ValueError, AttributeError):
        logger.warning(f"Invalid schedule_time format: {schedule_time}")
        return None

    # Create target time for today in local timezone
    target_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If we've already passed this time today, schedule for tomorrow
    if target_local <= now_local:
        target_local += timedelta(days=1)

    # Convert back to UTC
    return target_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _calculate_weekly_next_run(
    schedule_time: Optional[str],
    tz: ZoneInfo,
    now_local: datetime,
    days_of_week: Optional[list],
) -> Optional[datetime]:
    """Calculate next run for weekly schedule."""
    if not schedule_time or not days_of_week:
        return None

    try:
        hour, minute = map(int, schedule_time.split(":"))
    except (ValueError, AttributeError):
        logger.warning(f"Invalid schedule_time format: {schedule_time}")
        return None

    # Convert days to 0-6 (Sunday=0 in our system, but Python's weekday() has Monday=0)
    # Python: Monday=0, Sunday=6
    # Our system: Sunday=0, Saturday=6
    # Conversion: our_day = (python_weekday + 1) % 7

    current_weekday = (now_local.weekday() + 1) % 7  # Convert to our Sunday=0 system

    # Find the next matching day
    best_next = None
    for target_day in sorted(days_of_week):
        days_ahead = (target_day - current_weekday) % 7

        target_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        target_local += timedelta(days=days_ahead)

        # If it's today but time has passed, check if we need to go to next week
        if days_ahead == 0 and target_local <= now_local:
            # Look for next occurrence this week
            continue

        if best_next is None or target_local < best_next:
            best_next = target_local

    # If no valid time found this week, get the earliest day next week
    if best_next is None and days_of_week:
        first_day = min(days_of_week)
        days_ahead = ((first_day - current_weekday) % 7) or 7  # At least 1 week away
        best_next = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        best_next += timedelta(days=days_ahead)

    if best_next:
        return best_next.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return None


def _calculate_biweekly_next_run(
    schedule_time: Optional[str],
    tz: ZoneInfo,
    now_local: datetime,
    days_of_week: Optional[list],
    week_parity: Optional[int],
) -> Optional[datetime]:
    """Calculate next run for biweekly schedule."""
    if not schedule_time or not days_of_week:
        return None

    try:
        hour, minute = map(int, schedule_time.split(":"))
    except (ValueError, AttributeError):
        logger.warning(f"Invalid schedule_time format: {schedule_time}")
        return None

    # Calculate current week number (ISO week)
    current_week = now_local.isocalendar()[1]
    current_parity = current_week % 2

    # If week_parity is not set, default to current parity (run this week)
    if week_parity is None:
        week_parity = current_parity

    current_weekday = (now_local.weekday() + 1) % 7  # Convert to Sunday=0 system

    # Check if this is "our" week
    is_our_week = (current_parity == week_parity)

    best_next = None

    if is_our_week:
        # Look for a matching day this week
        for target_day in sorted(days_of_week):
            days_ahead = (target_day - current_weekday) % 7

            target_local = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            target_local += timedelta(days=days_ahead)

            # If it's today but time has passed, skip
            if days_ahead == 0 and target_local <= now_local:
                continue

            if best_next is None or target_local < best_next:
                best_next = target_local

    # If no valid time this week, schedule for our next biweekly occurrence
    if best_next is None and days_of_week:
        # Days until next "our" week
        if is_our_week:
            # If we're in our week but missed all days, go to next occurrence (2 weeks)
            days_to_next_week = 7 - current_weekday + 7  # Rest of this week + skip next week
        else:
            # We're in the "off" week, next week is ours
            days_to_next_week = 7 - current_weekday

        first_day = min(days_of_week)
        best_next = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        best_next += timedelta(days=days_to_next_week + first_day)

    if best_next:
        return best_next.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return None


def _calculate_monthly_next_run(
    schedule_time: Optional[str],
    tz: ZoneInfo,
    now_local: datetime,
    day_of_month: Optional[int],
) -> Optional[datetime]:
    """Calculate next run for monthly schedule."""
    if not schedule_time or day_of_month is None:
        return None

    try:
        hour, minute = map(int, schedule_time.split(":"))
    except (ValueError, AttributeError):
        logger.warning(f"Invalid schedule_time format: {schedule_time}")
        return None

    year = now_local.year
    month = now_local.month

    # Determine the actual day to run
    def get_actual_day(y: int, m: int) -> int:
        _, days_in_month = monthrange(y, m)
        if day_of_month == -1:
            return days_in_month
        return min(day_of_month, days_in_month)

    actual_day = get_actual_day(year, month)

    # Create target time for this month
    try:
        target_local = now_local.replace(day=actual_day, hour=hour, minute=minute, second=0, microsecond=0)
    except ValueError:
        # Day doesn't exist in this month, go to next month
        month += 1
        if month > 12:
            month = 1
            year += 1
        actual_day = get_actual_day(year, month)
        target_local = now_local.replace(year=year, month=month, day=actual_day,
                                          hour=hour, minute=minute, second=0, microsecond=0)

    # If we've passed this time this month, schedule for next month
    if target_local <= now_local:
        month += 1
        if month > 12:
            month = 1
            year += 1
        actual_day = get_actual_day(year, month)
        target_local = now_local.replace(year=year, month=month, day=actual_day,
                                          hour=hour, minute=minute, second=0, microsecond=0)

    return target_local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def describe_schedule(
    schedule_type: str,
    interval_seconds: Optional[int] = None,
    schedule_time: Optional[str] = None,
    timezone: Optional[str] = None,
    days_of_week: Optional[list] = None,
    day_of_month: Optional[int] = None,
) -> str:
    """
    Generate a human-readable description of a schedule.

    Returns:
        Human-readable schedule description
    """
    tz_suffix = f" ({timezone})" if timezone and timezone != "UTC" else ""

    if schedule_type == "interval":
        if not interval_seconds:
            return "No interval set"
        return _describe_interval(interval_seconds)

    elif schedule_type == "daily":
        if not schedule_time:
            return "Daily (no time set)"
        return f"Daily at {_format_time(schedule_time)}{tz_suffix}"

    elif schedule_type == "weekly":
        if not schedule_time or not days_of_week:
            return "Weekly (incomplete)"
        days_str = _describe_days(days_of_week)
        return f"Weekly on {days_str} at {_format_time(schedule_time)}{tz_suffix}"

    elif schedule_type == "biweekly":
        if not schedule_time or not days_of_week:
            return "Bi-weekly (incomplete)"
        days_str = _describe_days(days_of_week)
        return f"Every 2 weeks on {days_str} at {_format_time(schedule_time)}{tz_suffix}"

    elif schedule_type == "monthly":
        if not schedule_time or day_of_month is None:
            return "Monthly (incomplete)"
        day_str = "last day" if day_of_month == -1 else f"day {day_of_month}"
        return f"Monthly on {day_str} at {_format_time(schedule_time)}{tz_suffix}"

    return f"Unknown schedule type: {schedule_type}"


def _describe_interval(seconds: int) -> str:
    """Generate human-readable interval description."""
    if seconds < 60:
        return f"Every {seconds} seconds"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"Every {minutes} minute{'s' if minutes != 1 else ''}"
    elif seconds < 86400:
        hours = seconds // 3600
        remaining_minutes = (seconds % 3600) // 60
        if remaining_minutes == 0:
            return f"Every {hours} hour{'s' if hours != 1 else ''}"
        return f"Every {hours}h {remaining_minutes}m"
    else:
        days = seconds // 86400
        remaining_hours = (seconds % 86400) // 3600
        if remaining_hours == 0:
            return f"Every {days} day{'s' if days != 1 else ''}"
        return f"Every {days}d {remaining_hours}h"


def _describe_days(days: list) -> str:
    """Generate human-readable days of week description."""
    if not days:
        return "no days"
    if len(days) == 7:
        return "every day"
    if days == [1, 2, 3, 4, 5]:
        return "weekdays"
    if days == [0, 6]:
        return "weekends"

    # Use short names for multiple days
    if len(days) <= 3:
        return ", ".join(DAY_NAMES[d] for d in sorted(days))
    else:
        return ", ".join(DAY_NAMES_SHORT[d] for d in sorted(days))


def _format_time(schedule_time: str) -> str:
    """Format time string for display (convert to 12h format)."""
    try:
        hour, minute = map(int, schedule_time.split(":"))
        period = "AM" if hour < 12 else "PM"
        display_hour = hour % 12 or 12
        return f"{display_hour}:{minute:02d} {period}"
    except (ValueError, AttributeError):
        return schedule_time


def get_seconds_until(next_run: Optional[datetime]) -> Optional[int]:
    """Get seconds until the next scheduled run."""
    if not next_run:
        return None
    now = datetime.utcnow()
    delta = (next_run - now).total_seconds()
    return max(0, int(delta))


def format_relative_time(next_run: Optional[datetime]) -> str:
    """Format next run time as relative time string."""
    seconds = get_seconds_until(next_run)
    if seconds is None:
        return "Not scheduled"
    if seconds == 0:
        return "Now"
    if seconds < 60:
        return f"in {seconds}s"
    if seconds < 3600:
        minutes = seconds // 60
        return f"in {minutes}m"
    if seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes > 0:
            return f"in {hours}h {minutes}m"
        return f"in {hours}h"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if hours > 0:
        return f"in {days}d {hours}h"
    return f"in {days}d"
