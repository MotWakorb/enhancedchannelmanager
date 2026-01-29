"""
Factory functions for creating test data.

Each factory creates a model instance with sensible defaults that can be overridden.
All factories accept a session parameter and commit the created object.
"""
import json
from datetime import datetime, date, timedelta
from typing import Optional, Any
from sqlalchemy.orm import Session

from models import (
    JournalEntry, BandwidthDaily, ChannelWatchStats, HiddenChannelGroup,
    StreamStats, ScheduledTask, TaskSchedule, TaskExecution, Notification, AlertMethod,
    TagGroup, Tag, NormalizationRuleGroup, NormalizationRule
)


# Counter for generating unique IDs
_counter = {"value": 0}


def _next_id() -> int:
    """Generate a unique incrementing ID."""
    _counter["value"] += 1
    return _counter["value"]


def reset_counter() -> None:
    """Reset the counter (useful between tests)."""
    _counter["value"] = 0


# -----------------------------------------------------------------------------
# JournalEntry Factory
# -----------------------------------------------------------------------------

def create_journal_entry(
    session: Session,
    category: str = "channel",
    action_type: str = "create",
    entity_name: str = None,
    entity_id: int = None,
    description: str = None,
    before_value: dict = None,
    after_value: dict = None,
    user_initiated: bool = True,
    batch_id: str = None,
    timestamp: datetime = None,
    **kwargs
) -> JournalEntry:
    """Create a JournalEntry instance.

    Args:
        session: Database session
        category: Entry category (channel, epg, m3u)
        action_type: Type of action (create, update, delete)
        entity_name: Human-readable name of affected entity
        entity_id: ID of the affected entity
        description: Change description
        before_value: Previous state as dict (will be JSON serialized)
        after_value: New state as dict (will be JSON serialized)
        user_initiated: Whether change was manual vs automatic
        batch_id: ID to group related changes
        timestamp: Entry timestamp

    Returns:
        Created and committed JournalEntry instance
    """
    entry_id = _next_id()
    entry = JournalEntry(
        timestamp=timestamp or datetime.utcnow(),
        category=category,
        action_type=action_type,
        entity_id=entity_id or entry_id,
        entity_name=entity_name or f"Test Entity {entry_id}",
        description=description or f"Test description for entry {entry_id}",
        before_value=json.dumps(before_value) if before_value else None,
        after_value=json.dumps(after_value) if after_value else None,
        user_initiated=user_initiated,
        batch_id=batch_id,
        **kwargs
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


# -----------------------------------------------------------------------------
# BandwidthDaily Factory
# -----------------------------------------------------------------------------

def create_bandwidth_daily(
    session: Session,
    date_value: date = None,
    bytes_transferred: int = 0,
    peak_channels: int = 0,
    peak_clients: int = 0,
    **kwargs
) -> BandwidthDaily:
    """Create a BandwidthDaily instance.

    Args:
        session: Database session
        date_value: Date for the record (defaults to today minus counter days)
        bytes_transferred: Total bytes transferred
        peak_channels: Peak concurrent channels
        peak_clients: Peak concurrent clients

    Returns:
        Created and committed BandwidthDaily instance
    """
    offset = _next_id()
    bandwidth = BandwidthDaily(
        date=date_value or (date.today() - timedelta(days=offset)),
        bytes_transferred=bytes_transferred,
        peak_channels=peak_channels,
        peak_clients=peak_clients,
        **kwargs
    )
    session.add(bandwidth)
    session.commit()
    session.refresh(bandwidth)
    return bandwidth


# -----------------------------------------------------------------------------
# ChannelWatchStats Factory
# -----------------------------------------------------------------------------

def create_channel_watch_stats(
    session: Session,
    channel_id: str = None,
    channel_name: str = None,
    watch_count: int = 0,
    total_watch_seconds: int = 0,
    last_watched: datetime = None,
    **kwargs
) -> ChannelWatchStats:
    """Create a ChannelWatchStats instance.

    Args:
        session: Database session
        channel_id: Dispatcharr channel UUID
        channel_name: Channel name for display
        watch_count: Number of times seen watching
        total_watch_seconds: Total seconds watched
        last_watched: Last time channel was active

    Returns:
        Created and committed ChannelWatchStats instance
    """
    counter = _next_id()
    stats = ChannelWatchStats(
        channel_id=channel_id or f"channel-uuid-{counter}",
        channel_name=channel_name or f"Test Channel {counter}",
        watch_count=watch_count,
        total_watch_seconds=total_watch_seconds,
        last_watched=last_watched,
        **kwargs
    )
    session.add(stats)
    session.commit()
    session.refresh(stats)
    return stats


# -----------------------------------------------------------------------------
# HiddenChannelGroup Factory
# -----------------------------------------------------------------------------

def create_hidden_channel_group(
    session: Session,
    group_id: int = None,
    group_name: str = None,
    hidden_at: datetime = None,
    **kwargs
) -> HiddenChannelGroup:
    """Create a HiddenChannelGroup instance.

    Args:
        session: Database session
        group_id: Dispatcharr channel group ID
        group_name: Group name for display
        hidden_at: When the group was hidden

    Returns:
        Created and committed HiddenChannelGroup instance
    """
    counter = _next_id()
    group = HiddenChannelGroup(
        group_id=group_id or counter,
        group_name=group_name or f"Hidden Group {counter}",
        hidden_at=hidden_at or datetime.utcnow(),
        **kwargs
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


# -----------------------------------------------------------------------------
# StreamStats Factory
# -----------------------------------------------------------------------------

def create_stream_stats(
    session: Session,
    stream_id: int = None,
    stream_name: str = None,
    resolution: str = "1920x1080",
    fps: str = "29.97",
    video_codec: str = "h264",
    audio_codec: str = "aac",
    audio_channels: int = 2,
    stream_type: str = "HLS",
    bitrate: int = 5000000,
    video_bitrate: int = 4500000,
    probe_status: str = "success",
    error_message: str = None,
    last_probed: datetime = None,
    **kwargs
) -> StreamStats:
    """Create a StreamStats instance.

    Args:
        session: Database session
        stream_id: Dispatcharr stream ID
        stream_name: Cached stream name
        resolution: Video resolution (e.g., "1920x1080")
        fps: Frame rate (e.g., "29.97")
        video_codec: Video codec (e.g., "h264", "hevc")
        audio_codec: Audio codec (e.g., "aac", "ac3")
        audio_channels: Number of audio channels
        stream_type: Stream type (e.g., "HLS", "MPEG-TS")
        bitrate: Overall stream bitrate in bps
        video_bitrate: Video stream bitrate in bps
        probe_status: Probe status (success, failed, pending, timeout)
        error_message: Error details if probe failed
        last_probed: Last probe timestamp

    Returns:
        Created and committed StreamStats instance
    """
    counter = _next_id()
    stats = StreamStats(
        stream_id=stream_id or counter,
        stream_name=stream_name or f"Test Stream {counter}",
        resolution=resolution,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        stream_type=stream_type,
        bitrate=bitrate,
        video_bitrate=video_bitrate,
        probe_status=probe_status,
        error_message=error_message,
        last_probed=last_probed or datetime.utcnow(),
        **kwargs
    )
    session.add(stats)
    session.commit()
    session.refresh(stats)
    return stats


def create_failed_stream_stats(
    session: Session,
    stream_id: int = None,
    stream_name: str = None,
    error_message: str = "Connection timeout",
    **kwargs
) -> StreamStats:
    """Create a StreamStats instance with failed probe status.

    Convenience factory for creating a stream that failed probing.
    """
    return create_stream_stats(
        session,
        stream_id=stream_id,
        stream_name=stream_name,
        resolution=None,
        fps=None,
        video_codec=None,
        audio_codec=None,
        audio_channels=None,
        stream_type=None,
        bitrate=None,
        video_bitrate=None,
        probe_status="failed",
        error_message=error_message,
        **kwargs
    )


# -----------------------------------------------------------------------------
# ScheduledTask Factory
# -----------------------------------------------------------------------------

def create_scheduled_task(
    session: Session,
    task_id: str = None,
    task_name: str = None,
    description: str = None,
    enabled: bool = True,
    schedule_type: str = "manual",
    interval_seconds: int = None,
    cron_expression: str = None,
    schedule_time: str = None,
    timezone: str = "America/New_York",
    config: dict = None,
    last_run_at: datetime = None,
    next_run_at: datetime = None,
    **kwargs
) -> ScheduledTask:
    """Create a ScheduledTask instance.

    Args:
        session: Database session
        task_id: Unique task identifier
        task_name: Human-readable name
        description: Task description
        enabled: Whether task is enabled
        schedule_type: Type of schedule (interval, cron, manual)
        interval_seconds: Interval in seconds (for interval type)
        cron_expression: Cron expression (for cron type)
        schedule_time: Time of day HH:MM (for daily scheduling)
        timezone: IANA timezone name
        config: Task-specific configuration dict
        last_run_at: Last execution timestamp
        next_run_at: Next scheduled execution

    Returns:
        Created and committed ScheduledTask instance
    """
    counter = _next_id()
    task = ScheduledTask(
        task_id=task_id or f"test_task_{counter}",
        task_name=task_name or f"Test Task {counter}",
        description=description or f"Description for test task {counter}",
        enabled=enabled,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        cron_expression=cron_expression,
        schedule_time=schedule_time,
        timezone=timezone,
        config=json.dumps(config) if config else None,
        last_run_at=last_run_at,
        next_run_at=next_run_at,
        **kwargs
    )
    session.add(task)
    session.commit()
    session.refresh(task)
    return task


# -----------------------------------------------------------------------------
# TaskSchedule Factory
# -----------------------------------------------------------------------------

def create_task_schedule(
    session: Session,
    task_id: str = None,
    name: str = None,
    enabled: bool = True,
    schedule_type: str = "daily",
    interval_seconds: int = None,
    schedule_time: str = "03:00",
    timezone: str = "America/New_York",
    days_of_week: str = None,
    day_of_month: int = None,
    week_parity: int = None,
    next_run_at: datetime = None,
    **kwargs
) -> TaskSchedule:
    """Create a TaskSchedule instance.

    Args:
        session: Database session
        task_id: References ScheduledTask.task_id
        name: Optional label for this schedule
        enabled: Whether schedule is active
        schedule_type: Type (interval, daily, weekly, biweekly, monthly)
        interval_seconds: Seconds between runs (for interval type)
        schedule_time: Time of day HH:MM (for daily/weekly/monthly)
        timezone: IANA timezone name
        days_of_week: Comma-separated days (0=Sun, 6=Sat) for weekly/biweekly
        day_of_month: Day of month (1-31, -1 for last) for monthly
        week_parity: 0=even weeks, 1=odd weeks for biweekly
        next_run_at: Calculated next run time

    Returns:
        Created and committed TaskSchedule instance
    """
    counter = _next_id()
    schedule = TaskSchedule(
        task_id=task_id or f"test_task_{counter}",
        name=name or f"Schedule {counter}",
        enabled=enabled,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        schedule_time=schedule_time,
        timezone=timezone,
        days_of_week=days_of_week,
        day_of_month=day_of_month,
        week_parity=week_parity,
        next_run_at=next_run_at,
        **kwargs
    )
    session.add(schedule)
    session.commit()
    session.refresh(schedule)
    return schedule


# -----------------------------------------------------------------------------
# TaskExecution Factory
# -----------------------------------------------------------------------------

def create_task_execution(
    session: Session,
    task_id: str = None,
    started_at: datetime = None,
    completed_at: datetime = None,
    duration_seconds: float = None,
    status: str = "completed",
    success: bool = True,
    message: str = None,
    error: str = None,
    total_items: int = 10,
    success_count: int = 10,
    failed_count: int = 0,
    skipped_count: int = 0,
    details: dict = None,
    triggered_by: str = "scheduled",
    **kwargs
) -> TaskExecution:
    """Create a TaskExecution instance.

    Args:
        session: Database session
        task_id: References ScheduledTask.task_id
        started_at: Execution start time
        completed_at: Execution end time
        duration_seconds: Total duration
        status: Execution status (running, completed, failed, cancelled)
        success: Whether execution was successful
        message: Summary message
        error: Error message if failed
        total_items: Total items processed
        success_count: Successfully processed items
        failed_count: Failed items
        skipped_count: Skipped items
        details: Additional execution details dict
        triggered_by: What triggered execution (scheduled, manual, api)

    Returns:
        Created and committed TaskExecution instance
    """
    counter = _next_id()
    now = datetime.utcnow()

    # Calculate defaults
    start = started_at or (now - timedelta(seconds=30))
    end = completed_at or now if status == "completed" else None
    duration = duration_seconds
    if duration is None and end:
        duration = (end - start).total_seconds()

    execution = TaskExecution(
        task_id=task_id or f"test_task_{counter}",
        started_at=start,
        completed_at=end,
        duration_seconds=duration,
        status=status,
        success=success,
        message=message or f"Execution {counter} completed successfully" if success else None,
        error=error,
        total_items=total_items,
        success_count=success_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        details=json.dumps(details) if details else None,
        triggered_by=triggered_by,
        **kwargs
    )
    session.add(execution)
    session.commit()
    session.refresh(execution)
    return execution


def create_failed_task_execution(
    session: Session,
    task_id: str = None,
    error: str = "Task failed due to connection error",
    **kwargs
) -> TaskExecution:
    """Create a TaskExecution instance with failed status.

    Convenience factory for creating a failed execution.
    """
    return create_task_execution(
        session,
        task_id=task_id,
        status="failed",
        success=False,
        message=None,
        error=error,
        success_count=0,
        failed_count=kwargs.pop("total_items", 10),
        **kwargs
    )


# -----------------------------------------------------------------------------
# Notification Factory
# -----------------------------------------------------------------------------

def create_notification(
    session: Session,
    type: str = "info",
    title: str = None,
    message: str = None,
    read: bool = False,
    source: str = "test",
    source_id: str = None,
    action_label: str = None,
    action_url: str = None,
    extra_data: dict = None,
    created_at: datetime = None,
    read_at: datetime = None,
    expires_at: datetime = None,
    **kwargs
) -> Notification:
    """Create a Notification instance.

    Args:
        session: Database session
        type: Notification type (info, success, warning, error)
        title: Optional title
        message: Notification message
        read: Whether user has seen this
        source: Source of notification (task, api, system)
        source_id: Identifier within source
        action_label: Button label for action
        action_url: URL or route to navigate
        extra_data: Additional context dict
        created_at: Creation timestamp
        read_at: When marked as read
        expires_at: Auto-delete after this time

    Returns:
        Created and committed Notification instance
    """
    counter = _next_id()
    notification = Notification(
        type=type,
        title=title or f"Test Notification {counter}",
        message=message or f"This is test notification {counter}",
        read=read,
        source=source,
        source_id=source_id or f"source_{counter}",
        action_label=action_label,
        action_url=action_url,
        extra_data=json.dumps(extra_data) if extra_data else None,
        created_at=created_at or datetime.utcnow(),
        read_at=read_at,
        expires_at=expires_at,
        **kwargs
    )
    session.add(notification)
    session.commit()
    session.refresh(notification)
    return notification


def create_error_notification(
    session: Session,
    title: str = "Error",
    message: str = "An error occurred",
    **kwargs
) -> Notification:
    """Create an error Notification.

    Convenience factory for error notifications.
    """
    return create_notification(
        session,
        type="error",
        title=title,
        message=message,
        **kwargs
    )


def create_success_notification(
    session: Session,
    title: str = "Success",
    message: str = "Operation completed successfully",
    **kwargs
) -> Notification:
    """Create a success Notification.

    Convenience factory for success notifications.
    """
    return create_notification(
        session,
        type="success",
        title=title,
        message=message,
        **kwargs
    )


# -----------------------------------------------------------------------------
# AlertMethod Factory
# -----------------------------------------------------------------------------

def create_alert_method(
    session: Session,
    name: str = None,
    method_type: str = "discord",
    enabled: bool = True,
    config: dict = None,
    notify_info: bool = False,
    notify_success: bool = True,
    notify_warning: bool = True,
    notify_error: bool = True,
    alert_sources: dict = None,
    last_sent_at: datetime = None,
    **kwargs
) -> AlertMethod:
    """Create an AlertMethod instance.

    Args:
        session: Database session
        name: User-friendly name
        method_type: Alert type (discord, telegram, smtp)
        enabled: Whether method is enabled
        config: Type-specific configuration dict
        notify_info: Send info notifications
        notify_success: Send success notifications
        notify_warning: Send warning notifications
        notify_error: Send error notifications
        alert_sources: Granular source filtering dict
        last_sent_at: Last notification sent time

    Returns:
        Created and committed AlertMethod instance
    """
    counter = _next_id()

    # Provide default configs based on method_type
    if config is None:
        if method_type == "discord":
            config = {"webhook_url": f"https://discord.com/api/webhooks/test_{counter}"}
        elif method_type == "telegram":
            config = {"bot_token": f"test_bot_token_{counter}", "chat_id": f"test_chat_{counter}"}
        elif method_type == "smtp":
            config = {
                "host": "smtp.test.com",
                "port": 587,
                "username": f"test_{counter}@test.com",
                "password": "test_password",
                "from_address": f"test_{counter}@test.com",
                "to_addresses": [f"recipient_{counter}@test.com"]
            }
        else:
            config = {}

    method = AlertMethod(
        name=name or f"Test Alert {counter}",
        method_type=method_type,
        enabled=enabled,
        config=json.dumps(config),
        notify_info=notify_info,
        notify_success=notify_success,
        notify_warning=notify_warning,
        notify_error=notify_error,
        alert_sources=json.dumps(alert_sources) if alert_sources else None,
        last_sent_at=last_sent_at,
        **kwargs
    )
    session.add(method)
    session.commit()
    session.refresh(method)
    return method


def create_discord_alert(
    session: Session,
    name: str = None,
    webhook_url: str = None,
    **kwargs
) -> AlertMethod:
    """Create a Discord AlertMethod.

    Convenience factory for Discord webhooks.
    """
    counter = _next_id()
    return create_alert_method(
        session,
        name=name or f"Discord Alert {counter}",
        method_type="discord",
        config={"webhook_url": webhook_url or f"https://discord.com/api/webhooks/{counter}"},
        **kwargs
    )


def create_telegram_alert(
    session: Session,
    name: str = None,
    bot_token: str = None,
    chat_id: str = None,
    **kwargs
) -> AlertMethod:
    """Create a Telegram AlertMethod.

    Convenience factory for Telegram alerts.
    """
    counter = _next_id()
    return create_alert_method(
        session,
        name=name or f"Telegram Alert {counter}",
        method_type="telegram",
        config={
            "bot_token": bot_token or f"test_bot_{counter}",
            "chat_id": chat_id or f"test_chat_{counter}"
        },
        **kwargs
    )


# -----------------------------------------------------------------------------
# TagGroup Factory
# -----------------------------------------------------------------------------

def create_tag_group(
    session: Session,
    name: str = None,
    description: str = None,
    is_builtin: bool = False,
    **kwargs
) -> TagGroup:
    """Create a TagGroup instance.

    Args:
        session: Database session
        name: Group name (e.g., "Quality Tags")
        description: Optional description
        is_builtin: Whether this is a system-created group

    Returns:
        Created and committed TagGroup instance
    """
    counter = _next_id()
    group = TagGroup(
        name=name or f"Test Tag Group {counter}",
        description=description or f"Description for group {counter}",
        is_builtin=is_builtin,
        **kwargs
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


def create_quality_tag_group(
    session: Session,
    **kwargs
) -> TagGroup:
    """Create a Quality Tags group with common quality tags.

    Convenience factory for quality tag groups.
    """
    group = create_tag_group(
        session,
        name="Quality Tags",
        description="Video quality indicators",
        **kwargs
    )
    # Add common quality tags
    for tag_value in ["HD", "FHD", "UHD", "4K", "SD", "1080P", "720P"]:
        create_tag(session, group_id=group.id, value=tag_value)
    return group


# -----------------------------------------------------------------------------
# Tag Factory
# -----------------------------------------------------------------------------

def create_tag(
    session: Session,
    group_id: int,
    value: str = None,
    case_sensitive: bool = False,
    enabled: bool = True,
    is_builtin: bool = False,
    **kwargs
) -> Tag:
    """Create a Tag instance.

    Args:
        session: Database session
        group_id: ID of the parent TagGroup
        value: Tag value (e.g., "HD", "US")
        case_sensitive: Whether to match case
        enabled: Whether tag is active
        is_builtin: Whether this is a system-created tag

    Returns:
        Created and committed Tag instance
    """
    counter = _next_id()
    tag = Tag(
        group_id=group_id,
        value=value or f"TAG{counter}",
        case_sensitive=case_sensitive,
        enabled=enabled,
        is_builtin=is_builtin,
        **kwargs
    )
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return tag


# -----------------------------------------------------------------------------
# NormalizationRuleGroup Factory
# -----------------------------------------------------------------------------

def create_normalization_rule_group(
    session: Session,
    name: str = None,
    description: str = None,
    priority: int = 100,
    enabled: bool = True,
    is_builtin: bool = False,
    **kwargs
) -> NormalizationRuleGroup:
    """Create a NormalizationRuleGroup instance.

    Args:
        session: Database session
        name: Group name
        description: Optional description
        priority: Processing order (lower = first)
        enabled: Whether group is active
        is_builtin: Whether this is a system-created group

    Returns:
        Created and committed NormalizationRuleGroup instance
    """
    counter = _next_id()
    group = NormalizationRuleGroup(
        name=name or f"Test Rule Group {counter}",
        description=description or f"Description for rule group {counter}",
        priority=priority,
        enabled=enabled,
        is_builtin=is_builtin,
        **kwargs
    )
    session.add(group)
    session.commit()
    session.refresh(group)
    return group


# -----------------------------------------------------------------------------
# NormalizationRule Factory
# -----------------------------------------------------------------------------

def create_normalization_rule(
    session: Session,
    group_id: int,
    name: str = None,
    condition_type: str = "contains",
    condition_value: str = None,
    case_sensitive: bool = False,
    action_type: str = "remove",
    action_value: str = None,
    priority: int = 100,
    enabled: bool = True,
    is_builtin: bool = False,
    tag_group_id: int = None,
    tag_match_position: str = None,
    else_action_type: str = None,
    else_action_value: str = None,
    **kwargs
) -> NormalizationRule:
    """Create a NormalizationRule instance.

    Args:
        session: Database session
        group_id: ID of the parent NormalizationRuleGroup
        name: Rule name
        condition_type: Type of condition (contains, prefix, suffix, regex, tag_group)
        condition_value: Value to match
        case_sensitive: Whether to match case
        action_type: Type of action (remove, replace, strip_prefix, strip_suffix)
        action_value: Replacement value
        priority: Processing order within group
        enabled: Whether rule is active
        is_builtin: Whether this is a system-created rule
        tag_group_id: ID of tag group for tag_group condition
        tag_match_position: Position for tag matching (prefix, suffix, contains)
        else_action_type: Action when condition doesn't match
        else_action_value: Value for else action

    Returns:
        Created and committed NormalizationRule instance
    """
    counter = _next_id()
    rule = NormalizationRule(
        group_id=group_id,
        name=name or f"Test Rule {counter}",
        condition_type=condition_type,
        condition_value=condition_value or f"test_value_{counter}",
        case_sensitive=case_sensitive,
        action_type=action_type,
        action_value=action_value,
        priority=priority,
        enabled=enabled,
        is_builtin=is_builtin,
        tag_group_id=tag_group_id,
        tag_match_position=tag_match_position,
        else_action_type=else_action_type,
        else_action_value=else_action_value,
        **kwargs
    )
    session.add(rule)
    session.commit()
    session.refresh(rule)
    return rule


def create_tag_group_rule(
    session: Session,
    rule_group_id: int,
    tag_group_id: int,
    tag_match_position: str = "suffix",
    action_type: str = "strip_suffix",
    name: str = None,
    **kwargs
) -> NormalizationRule:
    """Create a NormalizationRule that uses tag_group condition.

    Convenience factory for tag-group based rules.
    """
    counter = _next_id()
    return create_normalization_rule(
        session,
        group_id=rule_group_id,
        name=name or f"Tag Group Rule {counter}",
        condition_type="tag_group",
        condition_value=None,
        tag_group_id=tag_group_id,
        tag_match_position=tag_match_position,
        action_type=action_type,
        **kwargs
    )
