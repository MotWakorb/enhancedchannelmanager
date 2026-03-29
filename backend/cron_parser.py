"""
Cron Expression Parser Utilities.

Provides validation, parsing, and human-readable descriptions for cron expressions.
Uses croniter for schedule calculations.
"""
import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# Common cron presets for user convenience
CRON_PRESETS = {
    "hourly": "0 * * * *",
    "daily_midnight": "0 0 * * *",
    "daily_3am": "0 3 * * *",
    "daily_noon": "0 12 * * *",
    "weekly_sunday": "0 0 * * 0",
    "weekly_monday": "0 0 * * 1",
    "monthly_first": "0 0 1 * *",
    "every_6_hours": "0 */6 * * *",
    "every_12_hours": "0 */12 * * *",
}

# Human-readable descriptions for cron fields
CRON_FIELD_NAMES = ["minute", "hour", "day of month", "month", "day of week"]


def is_croniter_available() -> bool:
    """Check if croniter is installed."""
    try:
        import croniter  # noqa: F401
        return True
    except ImportError:
        return False


def validate_cron_expression(expression: str) -> tuple[bool, str]:
    """
    Validate a cron expression.

    Args:
        expression: The cron expression to validate (5 fields: minute hour day month weekday)

    Returns:
        Tuple of (is_valid, error_message). error_message is empty if valid.
    """
    if not expression or not expression.strip():
        return False, "Cron expression is empty"

    expression = expression.strip()

    # Check for preset name
    if expression.lower() in CRON_PRESETS:
        return True, ""

    try:
        from croniter import croniter
        # Try to create a croniter instance - this validates the expression
        croniter(expression)
        return True, ""
    except ImportError:
        return False, "croniter library not installed"
    except (ValueError, KeyError) as e:
        return False, f"Invalid cron expression: {str(e)}"
    except Exception as e:
        return False, f"Error validating cron expression: {str(e)}"


def expand_preset(expression: str) -> str:
    """
    Expand a preset name to its cron expression.

    Args:
        expression: Either a preset name or a cron expression

    Returns:
        The cron expression (expanded if preset, unchanged otherwise)
    """
    return CRON_PRESETS.get(expression.lower().strip(), expression)


def get_next_n_run_times(
    expression: str,
    n: int = 5,
    base_time: Optional[datetime] = None,
    timezone: Optional[str] = None,
) -> list[datetime]:
    """
    Calculate the next N run times for a cron expression.

    Args:
        expression: Cron expression or preset name
        n: Number of run times to calculate
        base_time: Base time for calculation (default: now)
        timezone: IANA timezone name (default: UTC)

    Returns:
        List of next N run times as datetime in UTC
    """
    try:
        from croniter import croniter

        expression = expand_preset(expression)

        # Determine base time
        if base_time is None:
            if timezone:
                try:
                    tz = ZoneInfo(timezone)
                    base_time = datetime.now(tz)
                except Exception as e:
                    logger.warning("[CRON] Invalid timezone '%s', falling back to UTC: %s", timezone, e)
                    base_time = datetime.utcnow()
            else:
                base_time = datetime.utcnow()

        cron = croniter(expression, base_time)
        times = []
        for _ in range(n):
            next_time = cron.get_next(datetime)
            # Convert to UTC if timezone was used
            if timezone and next_time.tzinfo:
                next_time = next_time.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            times.append(next_time)

        return times
    except ImportError:
        logger.warning("[CRON] croniter not installed, cannot calculate run times")
        return []
    except Exception as e:
        logger.error("[CRON] Failed to calculate run times for '%s': %s", expression, e)
        return []


def describe_cron_expression(expression: str) -> str:
    """
    Generate a human-readable description of a cron expression.

    Args:
        expression: Cron expression or preset name

    Returns:
        Human-readable description
    """
    # Check for preset
    if expression.lower().strip() in CRON_PRESETS:
        preset = expression.lower().strip()
        descriptions = {
            "hourly": "Every hour at minute 0",
            "daily_midnight": "Every day at midnight",
            "daily_3am": "Every day at 3:00 AM",
            "daily_noon": "Every day at noon",
            "weekly_sunday": "Every Sunday at midnight",
            "weekly_monday": "Every Monday at midnight",
            "monthly_first": "First day of every month at midnight",
            "every_6_hours": "Every 6 hours",
            "every_12_hours": "Every 12 hours",
        }
        return descriptions.get(preset, f"Preset: {preset}")

    expression = expression.strip()
    parts = expression.split()

    if len(parts) != 5:
        return f"Invalid expression (expected 5 fields, got {len(parts)})"

    minute, hour, day, month, weekday = parts

    # Build description
    desc_parts = []

    # Time part
    if minute == "*" and hour == "*":
        desc_parts.append("Every minute")
    elif minute == "0" and hour == "*":
        desc_parts.append("Every hour")
    elif minute.startswith("*/"):
        desc_parts.append(f"Every {minute[2:]} minutes")
    elif hour.startswith("*/"):
        desc_parts.append(f"Every {hour[2:]} hours")
    elif minute == "0" and hour != "*":
        if hour.isdigit():
            h = int(hour)
            if h == 0:
                desc_parts.append("At midnight")
            elif h == 12:
                desc_parts.append("At noon")
            elif h < 12:
                desc_parts.append(f"At {h}:00 AM")
            else:
                desc_parts.append(f"At {h-12}:00 PM")
        else:
            desc_parts.append(f"At hour {hour}")
    else:
        if minute.isdigit() and hour.isdigit():
            h = int(hour)
            m = int(minute)
            if h < 12:
                desc_parts.append(f"At {h}:{m:02d} AM")
            elif h == 12:
                desc_parts.append(f"At 12:{m:02d} PM")
            else:
                desc_parts.append(f"At {h-12}:{m:02d} PM")
        else:
            desc_parts.append(f"At minute {minute}, hour {hour}")

    # Day/Month part
    if day != "*":
        if day.isdigit():
            desc_parts.append(f"on day {day}")
        else:
            desc_parts.append(f"on days {day}")

    if month != "*":
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        if month.isdigit():
            m = int(month)
            if 1 <= m <= 12:
                desc_parts.append(f"in {months[m-1]}")
            else:
                desc_parts.append(f"in month {month}")
        else:
            desc_parts.append(f"in months {month}")

    # Weekday part
    if weekday != "*":
        days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        if weekday.isdigit():
            w = int(weekday)
            if 0 <= w <= 6:
                desc_parts.append(f"on {days[w]}")
            else:
                desc_parts.append(f"on weekday {weekday}")
        else:
            desc_parts.append(f"on weekdays {weekday}")

    return " ".join(desc_parts)


def get_preset_list() -> list[dict]:
    """
    Get list of available presets with descriptions.

    Returns:
        List of dicts with name, expression, and description
    """
    return [
        {
            "name": name,
            "expression": expr,
            "description": describe_cron_expression(name),
        }
        for name, expr in CRON_PRESETS.items()
    ]


