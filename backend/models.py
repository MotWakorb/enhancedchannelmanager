"""
SQLAlchemy ORM models for the Journal and Bandwidth tracking features.
"""
from datetime import datetime, date
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, Date, Float, Index, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
from database import Base


class JournalEntry(Base):
    """
    Represents a single change entry in the journal.
    Tracks all modifications to channels, EPG sources, and M3U accounts.
    """
    __tablename__ = "journal_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    category = Column(String(20), nullable=False)  # "channel", "epg", "m3u"
    action_type = Column(String(30), nullable=False)  # "create", "update", "delete", etc.
    entity_id = Column(Integer, nullable=True)  # ID of the affected entity
    entity_name = Column(String(255), nullable=False)  # Human-readable name
    description = Column(Text, nullable=False)  # Human-readable change description
    before_value = Column(Text, nullable=True)  # JSON of previous state
    after_value = Column(Text, nullable=True)  # JSON of new state
    user_initiated = Column(Boolean, default=True, nullable=False)  # Manual vs automatic
    batch_id = Column(String(50), nullable=True)  # Groups related changes

    # Indexes for common queries
    __table_args__ = (
        Index("idx_journal_timestamp", timestamp.desc()),
        Index("idx_journal_category", category),
        Index("idx_journal_action_type", action_type),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        import json
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat() + "Z" if self.timestamp else None,
            "category": self.category,
            "action_type": self.action_type,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "description": self.description,
            "before_value": json.loads(self.before_value) if self.before_value else None,
            "after_value": json.loads(self.after_value) if self.after_value else None,
            "user_initiated": self.user_initiated,
            "batch_id": self.batch_id,
        }

    def __repr__(self):
        return f"<JournalEntry(id={self.id}, category={self.category}, action={self.action_type}, entity={self.entity_name})>"


class BandwidthDaily(Base):
    """
    Daily aggregated bandwidth statistics.
    One row per day with totals and peaks.

    Inbound = bandwidth from upstream providers (one stream per channel)
    Outbound = bandwidth to clients (multiplied by viewer count)
    """
    __tablename__ = "bandwidth_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True)
    bytes_transferred = Column(BigInteger, default=0, nullable=False)  # Legacy: total bytes (same as bytes_out)
    bytes_in = Column(BigInteger, default=0, nullable=False)  # Inbound from providers
    bytes_out = Column(BigInteger, default=0, nullable=False)  # Outbound to clients
    peak_channels = Column(Integer, default=0, nullable=False)
    peak_clients = Column(Integer, default=0, nullable=False)
    peak_bitrate_in = Column(BigInteger, default=0, nullable=False)  # Peak inbound bitrate (bps)
    peak_bitrate_out = Column(BigInteger, default=0, nullable=False)  # Peak outbound bitrate (bps)

    __table_args__ = (
        Index("idx_bandwidth_daily_date", date.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "bytes_transferred": self.bytes_transferred,
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "peak_channels": self.peak_channels,
            "peak_clients": self.peak_clients,
            "peak_bitrate_in": self.peak_bitrate_in,
            "peak_bitrate_out": self.peak_bitrate_out,
        }

    def __repr__(self):
        return f"<BandwidthDaily(date={self.date}, in={self.bytes_in}, out={self.bytes_out})>"


class ChannelWatchStats(Base):
    """
    Tracks watch counts and time per channel.
    Each time a channel is seen active in stats, we increment its watch count.
    Watch time accumulates while a channel remains active.
    """
    __tablename__ = "channel_watch_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(64), nullable=False, unique=True)  # Dispatcharr channel UUID
    channel_name = Column(String(255), nullable=False)  # Channel name (for display)
    watch_count = Column(Integer, default=0, nullable=False)  # Number of times seen watching
    total_watch_seconds = Column(Integer, default=0, nullable=False)  # Total seconds watched
    last_watched = Column(DateTime, nullable=True)  # Last time this channel was active

    __table_args__ = (
        Index("idx_channel_watch_count", watch_count.desc()),
        Index("idx_channel_watch_time", total_watch_seconds.desc()),
        Index("idx_channel_watch_channel_id", channel_id),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "watch_count": self.watch_count,
            "total_watch_seconds": self.total_watch_seconds,
            "last_watched": self.last_watched.isoformat() + "Z" if self.last_watched else None,
        }

    def __repr__(self):
        return f"<ChannelWatchStats(channel_id={self.channel_id}, name={self.channel_name}, count={self.watch_count})>"


class HiddenChannelGroup(Base):
    """
    Tracks channel groups that are hidden from the UI but still exist in Dispatcharr.
    Used for groups with active M3U sync settings - they're hidden instead of deleted
    to prevent breaking M3U auto-sync functionality.
    """
    __tablename__ = "hidden_channel_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, nullable=False, unique=True)  # Dispatcharr channel group ID
    group_name = Column(String(255), nullable=False)  # Group name (for display)
    hidden_at = Column(DateTime, default=datetime.utcnow, nullable=False)  # When it was hidden

    __table_args__ = (
        Index("idx_hidden_group_id", group_id),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.group_id,
            "name": self.group_name,
            "hidden_at": self.hidden_at.isoformat() + "Z" if self.hidden_at else None,
        }

    def __repr__(self):
        return f"<HiddenChannelGroup(group_id={self.group_id}, name={self.group_name})>"


class StreamStats(Base):
    """
    Stores ffprobe-derived stream metadata.
    One row per stream, updated on each probe.
    """
    __tablename__ = "stream_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    stream_id = Column(Integer, nullable=False, unique=True)  # Dispatcharr stream ID
    stream_name = Column(String(255), nullable=True)  # Cached stream name
    resolution = Column(String(20), nullable=True)  # e.g., "1920x1080"
    fps = Column(String(20), nullable=True)  # e.g., "29.97" - stored as string for flexibility
    video_codec = Column(String(50), nullable=True)  # e.g., "h264", "hevc"
    audio_codec = Column(String(50), nullable=True)  # e.g., "aac", "ac3"
    audio_channels = Column(Integer, nullable=True)  # e.g., 2, 6
    stream_type = Column(String(20), nullable=True)  # e.g., "HLS", "MPEG-TS"
    bitrate = Column(BigInteger, nullable=True)  # bits per second (overall stream)
    video_bitrate = Column(BigInteger, nullable=True)  # bits per second (video stream only)
    probe_status = Column(String(20), nullable=False, default="pending")  # success, failed, pending, timeout
    error_message = Column(Text, nullable=True)  # Error details for failed probes
    last_probed = Column(DateTime, nullable=True)  # Last probe timestamp
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    dismissed_at = Column(DateTime, nullable=True)  # When failure was dismissed (acknowledged)

    __table_args__ = (
        Index("idx_stream_stats_stream_id", stream_id),
        Index("idx_stream_stats_probe_status", probe_status),
        Index("idx_stream_stats_last_probed", last_probed.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "stream_id": self.stream_id,
            "stream_name": self.stream_name,
            "resolution": self.resolution,
            "fps": self.fps,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "audio_channels": self.audio_channels,
            "stream_type": self.stream_type,
            "bitrate": self.bitrate,
            "video_bitrate": self.video_bitrate,
            "probe_status": self.probe_status,
            "error_message": self.error_message,
            "last_probed": self.last_probed.isoformat() + "Z" if self.last_probed else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "dismissed_at": self.dismissed_at.isoformat() + "Z" if self.dismissed_at else None,
        }

    def __repr__(self):
        return f"<StreamStats(stream_id={self.stream_id}, name={self.stream_name}, status={self.probe_status})>"


class ScheduledTask(Base):
    """
    Configuration for a scheduled task.
    One row per task type with its schedule and settings.
    """
    __tablename__ = "scheduled_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(50), nullable=False, unique=True)  # e.g., "stream_probe", "epg_refresh"
    task_name = Column(String(100), nullable=False)  # Human-readable name
    description = Column(Text, nullable=True)  # Task description
    enabled = Column(Boolean, default=True, nullable=False)  # Is task enabled
    # Legacy schedule configuration (kept for backwards compatibility, will be migrated to TaskSchedule)
    schedule_type = Column(String(20), nullable=False, default="manual")  # "interval", "cron", "manual"
    interval_seconds = Column(Integer, nullable=True)  # For interval scheduling
    cron_expression = Column(String(100), nullable=True)  # For cron scheduling
    schedule_time = Column(String(10), nullable=True)  # HH:MM for daily scheduling
    timezone = Column(String(50), nullable=True)  # IANA timezone name
    # Task-specific configuration (JSON)
    config = Column(Text, nullable=True)  # JSON with task-specific settings
    # Alert configuration - control which alerts this task sends
    send_alerts = Column(Boolean, default=True, nullable=False)  # Master toggle for external alerts (email, etc.)
    alert_on_success = Column(Boolean, default=True, nullable=False)  # Alert when task succeeds
    alert_on_warning = Column(Boolean, default=True, nullable=False)  # Alert on partial failures
    alert_on_error = Column(Boolean, default=True, nullable=False)  # Alert on complete failures
    alert_on_info = Column(Boolean, default=False, nullable=False)  # Alert on info messages
    # Notification channels - which channels to send alerts to
    send_to_email = Column(Boolean, default=True, nullable=False)  # Send alerts via email (if SMTP configured)
    send_to_discord = Column(Boolean, default=True, nullable=False)  # Send alerts via Discord (if webhook configured)
    send_to_telegram = Column(Boolean, default=True, nullable=False)  # Send alerts via Telegram (if bot configured)
    show_notifications = Column(Boolean, default=True, nullable=False)  # Show in NotificationCenter (bell icon)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_run_at = Column(DateTime, nullable=True)  # Last execution start
    next_run_at = Column(DateTime, nullable=True)  # Next scheduled execution (computed from schedules)

    __table_args__ = (
        Index("idx_scheduled_task_id", task_id),
        Index("idx_scheduled_task_enabled", enabled),
        Index("idx_scheduled_task_next_run", next_run_at),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        import json
        return {
            "id": self.id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "description": self.description,
            "enabled": self.enabled,
            "schedule_type": self.schedule_type,
            "interval_seconds": self.interval_seconds,
            "cron_expression": self.cron_expression,
            "schedule_time": self.schedule_time,
            "timezone": self.timezone,
            "config": json.loads(self.config) if self.config else None,
            "send_alerts": self.send_alerts,
            "alert_on_success": self.alert_on_success,
            "alert_on_warning": self.alert_on_warning,
            "alert_on_error": self.alert_on_error,
            "alert_on_info": self.alert_on_info,
            "send_to_email": self.send_to_email,
            "send_to_discord": self.send_to_discord,
            "send_to_telegram": self.send_to_telegram,
            "show_notifications": self.show_notifications,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
            "last_run_at": self.last_run_at.isoformat() + "Z" if self.last_run_at else None,
            "next_run_at": self.next_run_at.isoformat() + "Z" if self.next_run_at else None,
        }

    def __repr__(self):
        return f"<ScheduledTask(task_id={self.task_id}, enabled={self.enabled})>"


class TaskSchedule(Base):
    """
    Individual schedule for a task (many-to-one with ScheduledTask).
    Supports multiple schedules per task with different types:
    - interval: Run every X seconds
    - daily: Run once per day at a specific time
    - weekly: Run on specific days each week
    - biweekly: Run every other week on specific days
    - monthly: Run on a specific day of month

    Each schedule can have task-specific parameters stored as JSON.
    For example, a StreamProber schedule might have:
    {"channel_groups": ["Sports", "News"], "batch_size": 10, "timeout": 30}
    """
    __tablename__ = "task_schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(50), nullable=False)  # References ScheduledTask.task_id
    name = Column(String(100), nullable=True)  # Optional label for this schedule
    enabled = Column(Boolean, default=True, nullable=False)  # Is this schedule active
    # Schedule type: interval, daily, weekly, biweekly, monthly
    schedule_type = Column(String(20), nullable=False)
    # For interval type: number of seconds between runs
    interval_seconds = Column(Integer, nullable=True)
    # For daily/weekly/biweekly/monthly: time of day (HH:MM in 24h format)
    schedule_time = Column(String(10), nullable=True)
    # IANA timezone name (e.g., "America/New_York")
    timezone = Column(String(50), nullable=True)
    # For weekly/biweekly: comma-separated list of days (0=Sunday, 6=Saturday)
    days_of_week = Column(String(20), nullable=True)  # e.g., "0,3,6" for Sun, Wed, Sat
    # For monthly: day of month (1-31, or -1 for last day)
    day_of_month = Column(Integer, nullable=True)
    # For biweekly: which week (0 or 1) - used to track odd/even weeks
    week_parity = Column(Integer, nullable=True)  # 0 = even weeks, 1 = odd weeks
    # Task-specific parameters as JSON
    parameters = Column(Text, nullable=True)  # JSON object with task-specific settings
    # Calculated next run time
    next_run_at = Column(DateTime, nullable=True)
    # Last execution time for this specific schedule
    last_run_at = Column(DateTime, nullable=True)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_task_schedule_task_id", task_id),
        Index("idx_task_schedule_enabled", enabled),
        Index("idx_task_schedule_next_run", next_run_at),
    )

    def get_days_of_week_list(self) -> list:
        """Parse days_of_week string into list of integers."""
        if not self.days_of_week:
            return []
        try:
            return [int(d.strip()) for d in self.days_of_week.split(",") if d.strip()]
        except ValueError:
            return []

    def set_days_of_week_list(self, days: list) -> None:
        """Set days_of_week from list of integers."""
        self.days_of_week = ",".join(str(d) for d in sorted(days)) if days else None

    def get_parameters(self) -> dict:
        """Parse parameters JSON into dictionary."""
        if not self.parameters:
            return {}
        try:
            import json
            return json.loads(self.parameters)
        except (ValueError, TypeError):
            return {}

    def set_parameters(self, params: dict) -> None:
        """Set parameters from dictionary."""
        import json
        self.parameters = json.dumps(params) if params else None

    def get_parameter(self, key: str, default=None):
        """Get a specific parameter value."""
        return self.get_parameters().get(key, default)

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "name": self.name,
            "enabled": self.enabled,
            "schedule_type": self.schedule_type,
            "interval_seconds": self.interval_seconds,
            "schedule_time": self.schedule_time,
            "timezone": self.timezone,
            "days_of_week": self.get_days_of_week_list(),
            "day_of_month": self.day_of_month,
            "week_parity": self.week_parity,
            "parameters": self.get_parameters(),
            "next_run_at": self.next_run_at.isoformat() + "Z" if self.next_run_at else None,
            "last_run_at": self.last_run_at.isoformat() + "Z" if self.last_run_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<TaskSchedule(id={self.id}, task_id={self.task_id}, name={self.name}, type={self.schedule_type})>"


class TaskExecution(Base):
    """
    Record of a task execution.
    One row per execution attempt with results.
    """
    __tablename__ = "task_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(50), nullable=False)  # References ScheduledTask.task_id
    # Execution timing
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    # Execution result
    status = Column(String(20), nullable=False)  # "running", "completed", "failed", "cancelled"
    success = Column(Boolean, nullable=True)  # True if completed successfully
    message = Column(Text, nullable=True)  # Summary message
    error = Column(Text, nullable=True)  # Error message if failed
    # Counters
    total_items = Column(Integer, default=0, nullable=False)
    success_count = Column(Integer, default=0, nullable=False)
    failed_count = Column(Integer, default=0, nullable=False)
    skipped_count = Column(Integer, default=0, nullable=False)
    # Details (JSON)
    details = Column(Text, nullable=True)  # JSON with execution details
    # Trigger info
    triggered_by = Column(String(20), default="scheduled", nullable=False)  # "scheduled", "manual", "api"

    __table_args__ = (
        Index("idx_task_exec_task_id", task_id),
        Index("idx_task_exec_started_at", started_at.desc()),
        Index("idx_task_exec_status", status),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        import json
        return {
            "id": self.id,
            "task_id": self.task_id,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "success": self.success,
            "message": self.message,
            "error": self.error,
            "total_items": self.total_items,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "details": json.loads(self.details) if self.details else None,
            "triggered_by": self.triggered_by,
        }

    def __repr__(self):
        return f"<TaskExecution(id={self.id}, task_id={self.task_id}, status={self.status})>"


class Notification(Base):
    """
    Persistent notification storage.
    Notifications appear in the notification center and can be marked as read.
    """
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(20), nullable=False, default="info")  # info, success, warning, error
    title = Column(String(255), nullable=True)  # Optional title
    message = Column(Text, nullable=False)  # Notification message
    read = Column(Boolean, default=False, nullable=False)  # Has user seen this
    # Source tracking
    source = Column(String(50), nullable=True)  # e.g., "task", "api", "system"
    source_id = Column(String(100), nullable=True)  # e.g., task_id, endpoint name
    # Optional action
    action_label = Column(String(50), nullable=True)  # Button label
    action_url = Column(String(500), nullable=True)  # URL or route to navigate
    # Extra data (JSON) for additional context
    # Note: 'metadata' is reserved by SQLAlchemy, so we use 'extra_data'
    extra_data = Column(Text, nullable=True)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    read_at = Column(DateTime, nullable=True)  # When marked as read
    expires_at = Column(DateTime, nullable=True)  # Auto-delete after this time

    __table_args__ = (
        Index("idx_notification_read", read),
        Index("idx_notification_created_at", created_at.desc()),
        Index("idx_notification_type", type),
        Index("idx_notification_source", source),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        import json
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "message": self.message,
            "read": self.read,
            "source": self.source,
            "source_id": self.source_id,
            "action_label": self.action_label,
            "action_url": self.action_url,
            "metadata": json.loads(self.extra_data) if self.extra_data else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "read_at": self.read_at.isoformat() + "Z" if self.read_at else None,
            "expires_at": self.expires_at.isoformat() + "Z" if self.expires_at else None,
        }

    def __repr__(self):
        return f"<Notification(id={self.id}, type={self.type}, read={self.read})>"


class NormalizationRuleGroup(Base):
    """
    Groups normalization rules for organization and bulk enable/disable.
    Rules within a group execute in priority order.
    Groups themselves execute in priority order.

    Built-in groups are created from existing tag-based normalization settings
    and marked with is_builtin=True. Users can create additional custom groups.
    """
    __tablename__ = "normalization_rule_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)  # e.g., "Quality Tags", "Country Prefixes"
    description = Column(Text, nullable=True)  # Optional description
    enabled = Column(Boolean, default=True, nullable=False)  # Enable/disable entire group
    priority = Column(Integer, default=0, nullable=False)  # Lower = runs first
    is_builtin = Column(Boolean, default=False, nullable=False)  # True for migrated tag groups
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_norm_group_enabled", enabled),
        Index("idx_norm_group_priority", priority),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
            "is_builtin": self.is_builtin,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<NormalizationRuleGroup(id={self.id}, name={self.name}, enabled={self.enabled})>"


class NormalizationRule(Base):
    """
    Individual normalization rule with condition and action.

    Condition Types:
    - 'always': Always matches (useful for unconditional transformations)
    - 'contains': Match if input contains the pattern
    - 'starts_with': Match if input starts with the pattern
    - 'ends_with': Match if input ends with the pattern
    - 'regex': Match using regular expression
    - 'tag_group': Match if text contains ANY tag from specified tag group

    Action Types:
    - 'remove': Remove the matched portion
    - 'replace': Replace matched portion with action_value
    - 'regex_replace': Use regex substitution (condition must be 'regex')
    - 'strip_prefix': Remove pattern from start (with optional separator)
    - 'strip_suffix': Remove pattern from end (with optional separator)
    - 'normalize_prefix': Keep prefix but standardize format (e.g., "US:" -> "US | ")

    If/Then/Else Logic:
    - IF condition matches: apply action_type/action_value
    - ELSE (if else_action_type is set): apply else_action_type/else_action_value

    Example Rules:
    - Strip "HD" suffix: condition_type='ends_with', condition_value='HD',
                         action_type='strip_suffix'
    - Remove country prefix: condition_type='regex', condition_value='^(US|UK|CA)[:\\s|]+',
                             action_type='remove'
    - Normalize quality: condition_type='regex', condition_value='\\s*(FHD|UHD|4K|HD|SD)\\s*$',
                        action_type='remove'
    - Strip quality tag: condition_type='tag_group', tag_group_id=1, tag_match_position='suffix',
                        action_type='remove'
    """
    __tablename__ = "normalization_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, nullable=False)  # References NormalizationRuleGroup.id
    name = Column(String(100), nullable=False)  # e.g., "Strip HD suffix"
    description = Column(Text, nullable=True)  # Optional description
    enabled = Column(Boolean, default=True, nullable=False)  # Enable/disable rule
    priority = Column(Integer, default=0, nullable=False)  # Order within group (lower = first)
    # Condition configuration (legacy single condition - kept for backward compatibility)
    condition_type = Column(String(20), nullable=True)  # always, contains, starts_with, ends_with, regex, tag_group
    condition_value = Column(String(500), nullable=True)  # Pattern to match (null for 'always' or 'tag_group')
    case_sensitive = Column(Boolean, default=False, nullable=False)  # Case sensitivity for matching
    # Tag group condition (v0.8.7) - used when condition_type='tag_group'
    tag_group_id = Column(Integer, ForeignKey("tag_groups.id", ondelete="SET NULL"), nullable=True)
    tag_match_position = Column(String(20), nullable=True)  # 'prefix', 'suffix', or 'contains'
    # Compound conditions (new - takes precedence over legacy fields if set)
    conditions = Column(Text, nullable=True)  # JSON array of condition objects: [{type, value, negate, case_sensitive}]
    condition_logic = Column(String(3), default="AND", nullable=False)  # "AND" or "OR" for combining conditions
    # Action configuration
    action_type = Column(String(20), nullable=False)  # remove, replace, regex_replace, strip_prefix, strip_suffix, normalize_prefix
    action_value = Column(String(500), nullable=True)  # Replacement value (null for remove actions)
    # Else action (v0.8.7) - executed when condition does NOT match
    else_action_type = Column(String(20), nullable=True)  # Same values as action_type
    else_action_value = Column(String(500), nullable=True)  # Replacement value for else action
    # Stop processing flag - if true, no further rules execute after this one matches
    stop_processing = Column(Boolean, default=False, nullable=False)
    # Built-in flag for migrated rules
    is_builtin = Column(Boolean, default=False, nullable=False)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship to TagGroup (for tag_group condition type)
    tag_group = relationship("TagGroup", lazy="joined")

    __table_args__ = (
        Index("idx_norm_rule_group", group_id),
        Index("idx_norm_rule_enabled", enabled),
        Index("idx_norm_rule_priority", group_id, priority),
        Index("idx_norm_rule_tag_group", tag_group_id),
    )

    def get_conditions(self) -> list:
        """Parse conditions JSON into list of condition objects."""
        if not self.conditions:
            return []
        try:
            import json
            return json.loads(self.conditions)
        except (json.JSONDecodeError, TypeError):
            return []

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "group_id": self.group_id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
            "condition_type": self.condition_type,
            "condition_value": self.condition_value,
            "case_sensitive": self.case_sensitive,
            "tag_group_id": self.tag_group_id,
            "tag_match_position": self.tag_match_position,
            "tag_group_name": self.tag_group.name if self.tag_group else None,
            "conditions": self.get_conditions(),
            "condition_logic": self.condition_logic,
            "action_type": self.action_type,
            "action_value": self.action_value,
            "else_action_type": self.else_action_type,
            "else_action_value": self.else_action_value,
            "stop_processing": self.stop_processing,
            "is_builtin": self.is_builtin,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<NormalizationRule(id={self.id}, name={self.name}, type={self.condition_type})>"


class AlertMethod(Base):
    """
    Configuration for an external alert method (Discord, Telegram, Email, etc.).
    Stores credentials and settings for sending notifications to external services.
    """
    __tablename__ = "alert_methods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)  # User-friendly name
    method_type = Column(String(50), nullable=False)  # discord, telegram, smtp, etc.
    enabled = Column(Boolean, default=True, nullable=False)
    # Configuration (JSON) - contains type-specific settings
    # Discord: webhook_url
    # Telegram: bot_token, chat_id
    # SMTP: host, port, username, password, from_address, to_addresses
    config = Column(Text, nullable=False)
    # Filter settings - which notification types to send
    notify_info = Column(Boolean, default=False, nullable=False)
    notify_success = Column(Boolean, default=True, nullable=False)
    notify_warning = Column(Boolean, default=True, nullable=False)
    notify_error = Column(Boolean, default=True, nullable=False)
    # Granular source filtering (JSON) - controls which sources trigger alerts
    # Schema: {"version": 1, "epg_refresh": {...}, "m3u_refresh": {...}, "probe_failures": {...}}
    # NULL means "send all" (backwards compatible)
    alert_sources = Column(Text, nullable=True)
    # Digest tracking
    last_sent_at = Column(DateTime, nullable=True)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_alert_method_type", method_type),
        Index("idx_alert_method_enabled", enabled),
    )

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Convert to dictionary for API responses.

        By default, sensitive config values are masked.
        Set include_sensitive=True to include actual values.
        """
        import json
        config = json.loads(self.config) if self.config else {}

        # Mask sensitive fields unless explicitly requested
        if not include_sensitive:
            masked_config = {}
            for key, value in config.items():
                if key in ('password', 'bot_token', 'webhook_url', 'api_key'):
                    masked_config[key] = '********' if value else None
                else:
                    masked_config[key] = value
            config = masked_config

        # Parse alert_sources JSON, defaulting to None if not set
        alert_sources = None
        if self.alert_sources:
            try:
                alert_sources = json.loads(self.alert_sources)
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "id": self.id,
            "name": self.name,
            "method_type": self.method_type,
            "enabled": self.enabled,
            "config": config,
            "notify_info": self.notify_info,
            "notify_success": self.notify_success,
            "notify_warning": self.notify_warning,
            "notify_error": self.notify_error,
            "alert_sources": alert_sources,
            "last_sent_at": self.last_sent_at.isoformat() + "Z" if self.last_sent_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<AlertMethod(id={self.id}, name={self.name}, type={self.method_type})>"


class TagGroup(Base):
    """
    Groups of tags for vocabulary management in the normalization engine.
    Tag groups organize related strings (e.g., Quality, Country, Timezone).
    Built-in groups are created automatically and cannot be deleted.
    """
    __tablename__ = "tag_groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)  # e.g., "Quality Tags", "Country Tags"
    description = Column(Text, nullable=True)  # Optional description
    is_builtin = Column(Boolean, default=False, nullable=False)  # True for system-created groups
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationship to tags - cascade delete removes all tags when group is deleted
    tags = relationship("Tag", back_populates="group", cascade="all, delete-orphan", lazy="dynamic")

    __table_args__ = (
        Index("idx_tag_group_name", name),
        Index("idx_tag_group_builtin", is_builtin),
    )

    def to_dict(self, include_tags: bool = False) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "is_builtin": self.is_builtin,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }
        if include_tags:
            result["tags"] = [tag.to_dict() for tag in self.tags]
        return result

    def __repr__(self):
        return f"<TagGroup(id={self.id}, name={self.name}, is_builtin={self.is_builtin})>"


class Tag(Base):
    """
    Individual tag within a tag group.
    Tags are string values used for pattern matching in normalization rules.
    """
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, autoincrement=True)
    group_id = Column(Integer, ForeignKey("tag_groups.id", ondelete="CASCADE"), nullable=False)
    value = Column(String(100), nullable=False)  # The tag value, e.g., "HD", "US", "NFL"
    case_sensitive = Column(Boolean, default=False, nullable=False)  # Match case when searching
    enabled = Column(Boolean, default=True, nullable=False)  # Can be disabled without deleting
    is_builtin = Column(Boolean, default=False, nullable=False)  # True for system-created tags

    # Relationship back to group
    group = relationship("TagGroup", back_populates="tags")

    __table_args__ = (
        UniqueConstraint("group_id", "value", name="uq_tag_group_value"),
        Index("idx_tag_group_id", group_id),
        Index("idx_tag_enabled", enabled),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "group_id": self.group_id,
            "value": self.value,
            "case_sensitive": self.case_sensitive,
            "enabled": self.enabled,
            "is_builtin": self.is_builtin,
        }

    def __repr__(self):
        return f"<Tag(id={self.id}, group_id={self.group_id}, value={self.value})>"


class M3USnapshot(Base):
    """
    Point-in-time snapshot of M3U playlist state.
    Stored on each M3U refresh to enable change detection.
    """
    __tablename__ = "m3u_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    m3u_account_id = Column(Integer, nullable=False)  # Dispatcharr M3U account ID
    snapshot_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    # JSON with group names and stream counts: {"groups": [{"name": "Sports", "stream_count": 50}, ...]}
    groups_data = Column(Text, nullable=True)
    total_streams = Column(Integer, default=0, nullable=False)
    # Dispatcharr's updated_at timestamp when this snapshot was taken (for change monitoring)
    dispatcharr_updated_at = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_m3u_snapshot_account", m3u_account_id),
        Index("idx_m3u_snapshot_time", snapshot_time.desc()),
        Index("idx_m3u_snapshot_account_time", m3u_account_id, snapshot_time.desc()),
    )

    def get_groups_data(self) -> dict:
        """Parse groups_data JSON into dictionary."""
        if not self.groups_data:
            return {"groups": []}
        try:
            import json
            return json.loads(self.groups_data)
        except (ValueError, TypeError):
            return {"groups": []}

    def set_groups_data(self, data: dict) -> None:
        """Set groups_data from dictionary."""
        import json
        self.groups_data = json.dumps(data) if data else None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "m3u_account_id": self.m3u_account_id,
            "snapshot_time": self.snapshot_time.isoformat() + "Z" if self.snapshot_time else None,
            "groups_data": self.get_groups_data(),
            "total_streams": self.total_streams,
            "dispatcharr_updated_at": self.dispatcharr_updated_at,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

    def __repr__(self):
        return f"<M3USnapshot(id={self.id}, m3u_account_id={self.m3u_account_id}, total_streams={self.total_streams})>"


class M3UChangeLog(Base):
    """
    Persisted log of detected changes in M3U playlists.
    Records additions, removals, and modifications of groups and streams.
    """
    __tablename__ = "m3u_change_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    m3u_account_id = Column(Integer, nullable=False)  # Dispatcharr M3U account ID
    change_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Change type: group_added, group_removed, streams_added, streams_removed, streams_modified
    change_type = Column(String(30), nullable=False)
    group_name = Column(String(255), nullable=True)  # Affected group name (if applicable)
    # JSON array of stream names for bulk changes: ["Stream 1", "Stream 2", ...]
    stream_names = Column(Text, nullable=True)
    count = Column(Integer, default=0, nullable=False)  # Number of items affected
    enabled = Column(Boolean, default=False, nullable=False)  # Whether the group is enabled in the M3U
    snapshot_id = Column(Integer, ForeignKey("m3u_snapshots.id", ondelete="SET NULL"), nullable=True)

    # Relationship to snapshot
    snapshot = relationship("M3USnapshot", lazy="joined")

    __table_args__ = (
        Index("idx_m3u_change_account", m3u_account_id),
        Index("idx_m3u_change_time", change_time.desc()),
        Index("idx_m3u_change_account_time", m3u_account_id, change_time.desc()),
        Index("idx_m3u_change_type", change_type),
    )

    def get_stream_names(self) -> list:
        """Parse stream_names JSON into list."""
        if not self.stream_names:
            return []
        try:
            import json
            return json.loads(self.stream_names)
        except (ValueError, TypeError):
            return []

    def set_stream_names(self, names: list) -> None:
        """Set stream_names from list."""
        import json
        self.stream_names = json.dumps(names) if names else None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "m3u_account_id": self.m3u_account_id,
            "change_time": self.change_time.isoformat() + "Z" if self.change_time else None,
            "change_type": self.change_type,
            "group_name": self.group_name,
            "stream_names": self.get_stream_names(),
            "count": self.count,
            "enabled": self.enabled,
            "snapshot_id": self.snapshot_id,
        }

    def __repr__(self):
        return f"<M3UChangeLog(id={self.id}, m3u_account_id={self.m3u_account_id}, type={self.change_type}, count={self.count}, enabled={self.enabled})>"


class M3UDigestSettings(Base):
    """
    Settings for M3U change digest email reports.
    Controls frequency and content of automated change notifications.
    """
    __tablename__ = "m3u_digest_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    enabled = Column(Boolean, default=False, nullable=False)
    # Frequency: immediate, hourly, daily, weekly
    frequency = Column(String(20), default="daily", nullable=False)
    # JSON array of email addresses: ["user@example.com", ...]
    email_recipients = Column(Text, nullable=True)
    # Content filters
    include_group_changes = Column(Boolean, default=True, nullable=False)
    include_stream_changes = Column(Boolean, default=True, nullable=False)
    # Show detailed list of streams/groups in digest (vs just summary counts)
    show_detailed_list = Column(Boolean, default=True, nullable=False)
    # Only send digest if at least this many changes occurred
    min_changes_threshold = Column(Integer, default=1, nullable=False)
    # Send digest to Discord (uses shared Discord webhook from General Settings)
    send_to_discord = Column(Boolean, default=False, nullable=False)
    # Tracking
    last_digest_at = Column(DateTime, nullable=True)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def get_email_recipients(self) -> list:
        """Parse email_recipients JSON into list."""
        if not self.email_recipients:
            return []
        try:
            import json
            return json.loads(self.email_recipients)
        except (ValueError, TypeError):
            return []

    def set_email_recipients(self, emails: list) -> None:
        """Set email_recipients from list."""
        import json
        self.email_recipients = json.dumps(emails) if emails else None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "enabled": self.enabled,
            "frequency": self.frequency,
            "email_recipients": self.get_email_recipients(),
            "include_group_changes": self.include_group_changes,
            "include_stream_changes": self.include_stream_changes,
            "show_detailed_list": self.show_detailed_list,
            "min_changes_threshold": self.min_changes_threshold,
            "send_to_discord": self.send_to_discord,
            "last_digest_at": self.last_digest_at.isoformat() + "Z" if self.last_digest_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<M3UDigestSettings(id={self.id}, enabled={self.enabled}, frequency={self.frequency})>"


# =============================================================================
# Enhanced Statistics Models (v0.11.0)
# =============================================================================

class UniqueClientConnection(Base):
    """
    Tracks individual client connections for unique viewer analytics.
    Records each time a client IP connects to watch a channel.
    Used for calculating unique viewers and connection patterns.
    """
    __tablename__ = "unique_client_connections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip_address = Column(String(45), nullable=False)  # IPv4 or IPv6
    channel_id = Column(String(64), nullable=False)  # Dispatcharr channel UUID
    channel_name = Column(String(255), nullable=False)  # Cached for display
    date = Column(Date, nullable=False)  # Date of connection (for daily aggregation)
    connected_at = Column(DateTime, nullable=False)  # When connection started
    disconnected_at = Column(DateTime, nullable=True)  # When connection ended (null if still active)
    watch_seconds = Column(Integer, default=0, nullable=False)  # Duration of this session
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_unique_client_ip", ip_address),
        Index("idx_unique_client_channel", channel_id),
        Index("idx_unique_client_date", date.desc()),
        Index("idx_unique_client_channel_date", channel_id, date),
        Index("idx_unique_client_ip_date", ip_address, date),
        # Composite for finding unique viewers per channel per day
        Index("idx_unique_client_channel_ip_date", channel_id, ip_address, date),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "date": self.date.isoformat() if self.date else None,
            "connected_at": self.connected_at.isoformat() + "Z" if self.connected_at else None,
            "disconnected_at": self.disconnected_at.isoformat() + "Z" if self.disconnected_at else None,
            "watch_seconds": self.watch_seconds,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

    def __repr__(self):
        return f"<UniqueClientConnection(id={self.id}, ip={self.ip_address}, channel={self.channel_name})>"


class ChannelBandwidth(Base):
    """
    Per-channel bandwidth tracking (daily aggregates).
    Tracks how much data each channel transfers, enabling per-channel analytics.
    """
    __tablename__ = "channel_bandwidth"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(64), nullable=False)  # Dispatcharr channel UUID
    channel_name = Column(String(255), nullable=False)  # Cached for display
    date = Column(Date, nullable=False)  # Date of data
    bytes_transferred = Column(BigInteger, default=0, nullable=False)  # Total bytes for this channel this day
    peak_clients = Column(Integer, default=0, nullable=False)  # Max concurrent clients
    total_watch_seconds = Column(Integer, default=0, nullable=False)  # Cumulative watch time
    connection_count = Column(Integer, default=0, nullable=False)  # Number of connections started
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("channel_id", "date", name="uq_channel_bandwidth_channel_date"),
        Index("idx_channel_bandwidth_channel", channel_id),
        Index("idx_channel_bandwidth_date", date.desc()),
        Index("idx_channel_bandwidth_bytes", bytes_transferred.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "date": self.date.isoformat() if self.date else None,
            "bytes_transferred": self.bytes_transferred,
            "peak_clients": self.peak_clients,
            "total_watch_seconds": self.total_watch_seconds,
            "connection_count": self.connection_count,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<ChannelBandwidth(id={self.id}, channel={self.channel_name}, date={self.date}, bytes={self.bytes_transferred})>"


class ChannelPopularityScore(Base):
    """
    Calculated popularity scores for channels.
    Updated periodically by the popularity calculator service.
    Combines multiple metrics into a single score for ranking.
    """
    __tablename__ = "channel_popularity_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    channel_id = Column(String(64), nullable=False, unique=True)  # Dispatcharr channel UUID
    channel_name = Column(String(255), nullable=False)  # Cached for display
    # Composite popularity score (0-100 scale)
    score = Column(Float, default=0.0, nullable=False)
    # Current rank (1 = most popular)
    rank = Column(Integer, nullable=True)
    # Component metrics (7-day rolling window)
    watch_count_7d = Column(Integer, default=0, nullable=False)  # Number of watch sessions
    watch_time_7d = Column(Integer, default=0, nullable=False)  # Total seconds watched
    unique_viewers_7d = Column(Integer, default=0, nullable=False)  # Distinct IP addresses
    bandwidth_7d = Column(BigInteger, default=0, nullable=False)  # Bytes transferred
    # Trend indicators
    trend = Column(String(10), default="stable", nullable=False)  # "up", "down", "stable"
    trend_percent = Column(Float, default=0.0, nullable=False)  # Percentage change from previous period
    previous_score = Column(Float, nullable=True)  # Score from previous calculation
    previous_rank = Column(Integer, nullable=True)  # Rank from previous calculation
    # Calculation metadata
    calculated_at = Column(DateTime, nullable=False)
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_popularity_score", score.desc()),
        Index("idx_popularity_rank", rank),
        Index("idx_popularity_channel", channel_id),
        Index("idx_popularity_trend", trend),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "score": self.score,
            "rank": self.rank,
            "watch_count_7d": self.watch_count_7d,
            "watch_time_7d": self.watch_time_7d,
            "unique_viewers_7d": self.unique_viewers_7d,
            "bandwidth_7d": self.bandwidth_7d,
            "trend": self.trend,
            "trend_percent": self.trend_percent,
            "previous_score": self.previous_score,
            "previous_rank": self.previous_rank,
            "calculated_at": self.calculated_at.isoformat() + "Z" if self.calculated_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<ChannelPopularityScore(id={self.id}, channel={self.channel_name}, score={self.score}, rank={self.rank})>"


# =============================================================================
# Authentication Models (v0.11.5)
# =============================================================================

class User(Base):
    """
    User account for authentication.
    Supports local auth and external providers (OIDC, SAML, LDAP, Dispatcharr).
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)  # Null for external auth

    # External authentication
    auth_provider = Column(String(50), default="local", nullable=False)  # local, oidc, saml, ldap, dispatcharr
    external_id = Column(String(255), nullable=True)  # ID from external provider

    # Profile
    display_name = Column(String(255), nullable=True)

    # Status
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)

    # Relationships
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    identities = relationship("UserIdentity", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_user_auth_provider", auth_provider),
        Index("idx_user_external_id", auth_provider, external_id),
    )

    def to_dict(self, include_sensitive: bool = False) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "display_name": self.display_name,
            "auth_provider": self.auth_provider,
            "is_active": self.is_active,
            "is_admin": self.is_admin,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() + "Z" if self.last_login_at else None,
        }
        if include_sensitive:
            result["external_id"] = self.external_id
        return result

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, provider={self.auth_provider})>"


class UserSession(Base):
    """
    Active user session tracking.
    Stores refresh tokens and session metadata.
    """
    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    # Token tracking (store hash of refresh token, not the token itself)
    refresh_token_hash = Column(String(255), nullable=False, unique=True)

    # Session metadata
    ip_address = Column(String(45), nullable=True)  # IPv6 can be up to 45 chars
    user_agent = Column(String(500), nullable=True)

    # Expiration
    expires_at = Column(DateTime, nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Status
    is_revoked = Column(Boolean, default=False, nullable=False)

    # Relationships
    user = relationship("User", back_populates="sessions")

    __table_args__ = (
        Index("idx_session_user", user_id),
        Index("idx_session_expires", expires_at),
        Index("idx_session_token_hash", refresh_token_hash),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "expires_at": self.expires_at.isoformat() + "Z" if self.expires_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() + "Z" if self.last_used_at else None,
            "is_revoked": self.is_revoked,
        }

    def __repr__(self):
        return f"<UserSession(id={self.id}, user_id={self.user_id}, expires={self.expires_at})>"


class PasswordResetToken(Base):
    """
    Password reset tokens for forgot password flow.
    """
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(255), nullable=False, unique=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_reset_token_hash", token_hash),
        Index("idx_reset_token_user", user_id),
    )

    def __repr__(self):
        return f"<PasswordResetToken(id={self.id}, user_id={self.user_id})>"


class UserIdentity(Base):
    """
    Links multiple authentication providers to a single user account.
    Allows users to log in with any linked identity and access the same account.

    Providers: 'local', 'dispatcharr', 'oidc', 'saml', 'ldap'
    """
    __tablename__ = "user_identities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(50), nullable=False)  # local, dispatcharr, oidc, saml, ldap
    external_id = Column(String(255), nullable=True)  # Provider-specific ID (null for local)
    identifier = Column(String(255), nullable=False)  # Username/email used with this provider
    linked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)

    # Relationships
    user = relationship("User", back_populates="identities")

    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_identity_provider_external"),
        UniqueConstraint("provider", "identifier", name="uq_identity_provider_identifier"),
        Index("idx_identity_user_id", user_id),
        Index("idx_identity_provider", provider),
        Index("idx_identity_external_id", provider, external_id),
        Index("idx_identity_identifier", provider, identifier),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "provider": self.provider,
            "external_id": self.external_id,
            "identifier": self.identifier,
            "linked_at": self.linked_at.isoformat() + "Z" if self.linked_at else None,
            "last_used_at": self.last_used_at.isoformat() + "Z" if self.last_used_at else None,
        }

    def __repr__(self):
        return f"<UserIdentity(id={self.id}, user_id={self.user_id}, provider={self.provider}, identifier={self.identifier})>"


# =============================================================================
# Auto-Creation Pipeline Models (v0.12.0)
# =============================================================================

class AutoCreationRule(Base):
    """
    Rule for automatic channel creation from streams.

    Rules evaluate streams from M3U accounts and perform actions to create/configure
    channels. Rules run in priority order (lower number = higher priority) when:
    - Manually triggered
    - After M3U refresh (if run_on_refresh is enabled)
    - On schedule (optional)

    Conditions are stored as JSON array and support logical operators (AND/OR/NOT).
    Actions are stored as JSON array and execute in sequence when conditions match.
    """
    __tablename__ = "auto_creation_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)  # e.g., "Create Sports Channels"
    description = Column(Text, nullable=True)  # User notes about this rule
    enabled = Column(Boolean, default=True, nullable=False)
    priority = Column(Integer, default=0, nullable=False)  # Lower = runs first

    # Scope - which streams this rule applies to
    m3u_account_id = Column(Integer, nullable=True)  # Null = all accounts
    target_group_id = Column(Integer, nullable=True)  # Default group for created channels

    # Rule logic - stored as JSON
    conditions = Column(Text, nullable=False)  # JSON array of condition objects
    actions = Column(Text, nullable=False)  # JSON array of action objects

    # Behavior
    run_on_refresh = Column(Boolean, default=False, nullable=False)  # Auto-run after M3U refresh
    stop_on_first_match = Column(Boolean, default=True, nullable=False)  # Don't process further rules for matched streams

    # Sorting - applied to matched streams before executing actions
    sort_field = Column(String(50), nullable=True)   # None = no sort (process in fetch order)
    sort_order = Column(String(4), default="asc")    # "asc" or "desc"
    probe_on_sort = Column(Boolean, default=False, nullable=False)  # Probe unprobed streams before quality sort

    # Normalization - apply normalization engine rules to channel names
    normalize_names = Column(Boolean, default=False, nullable=False)

    # Tracking
    last_run_at = Column(DateTime, nullable=True)
    last_run_stats = Column(Text, nullable=True)  # JSON: {matched: 10, created: 5, skipped: 5, errors: 0}
    match_count = Column(Integer, default=0)  # Cumulative match count across all executions

    # Reconciliation - tracks which channels this rule currently owns
    # JSON array of channel IDs. Null = never run (first run will populate without deletions)
    managed_channel_ids = Column(Text, nullable=True)

    # Orphan cleanup behavior: "delete", "move_uncategorized", "delete_and_cleanup_groups", or "none"
    orphan_action = Column(String(30), default="delete", nullable=False)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_auto_rule_enabled", enabled),
        Index("idx_auto_rule_priority", priority),
        Index("idx_auto_rule_enabled_priority", enabled, priority),
        Index("idx_auto_rule_m3u_account", m3u_account_id),
        Index("idx_auto_rule_run_on_refresh", run_on_refresh),
    )

    def get_conditions(self) -> list:
        """Parse conditions JSON into list."""
        if not self.conditions:
            return []
        try:
            import json
            return json.loads(self.conditions)
        except (ValueError, TypeError):
            return []

    def set_conditions(self, conditions: list) -> None:
        """Set conditions from list."""
        import json
        self.conditions = json.dumps(conditions) if conditions else "[]"

    def get_actions(self) -> list:
        """Parse actions JSON into list."""
        if not self.actions:
            return []
        try:
            import json
            return json.loads(self.actions)
        except (ValueError, TypeError):
            return []

    def set_actions(self, actions: list) -> None:
        """Set actions from list."""
        import json
        self.actions = json.dumps(actions) if actions else "[]"

    def get_last_run_stats(self) -> dict:
        """Parse last_run_stats JSON into dict."""
        if not self.last_run_stats:
            return {}
        try:
            import json
            return json.loads(self.last_run_stats)
        except (ValueError, TypeError):
            return {}

    def set_last_run_stats(self, stats: dict) -> None:
        """Set last_run_stats from dict."""
        import json
        self.last_run_stats = json.dumps(stats) if stats else None

    def get_managed_channel_ids(self) -> list[int]:
        """Parse managed_channel_ids JSON into list of ints."""
        if not self.managed_channel_ids:
            return []
        try:
            import json
            return json.loads(self.managed_channel_ids)
        except (ValueError, TypeError):
            return []

    def set_managed_channel_ids(self, ids: list[int]) -> None:
        """Set managed_channel_ids from list of ints."""
        import json
        self.managed_channel_ids = json.dumps(sorted(set(ids))) if ids else None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "enabled": self.enabled,
            "priority": self.priority,
            "m3u_account_id": self.m3u_account_id,
            "target_group_id": self.target_group_id,
            "conditions": self.get_conditions(),
            "actions": self.get_actions(),
            "run_on_refresh": self.run_on_refresh,
            "stop_on_first_match": self.stop_on_first_match,
            "sort_field": self.sort_field,
            "sort_order": self.sort_order or "asc",
            "probe_on_sort": self.probe_on_sort or False,
            "normalize_names": self.normalize_names or False,
            "orphan_action": self.orphan_action or "delete",
            "last_run_at": self.last_run_at.isoformat() + "Z" if self.last_run_at else None,
            "last_run_stats": self.get_last_run_stats(),
            "match_count": self.match_count or 0,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<AutoCreationRule(id={self.id}, name={self.name}, enabled={self.enabled}, priority={self.priority})>"


class AutoCreationExecution(Base):
    """
    Tracks each pipeline execution for audit and undo support.

    Records what was created/modified during each run, enabling:
    - Audit trail of all changes
    - Rollback/undo of a specific execution
    - Dry-run mode that shows what would happen without executing
    """
    __tablename__ = "auto_creation_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Execution context
    rule_id = Column(Integer, ForeignKey("auto_creation_rules.id", ondelete="SET NULL"), nullable=True)
    rule_name = Column(String(100), nullable=True)  # Cached for display after rule deletion
    mode = Column(String(20), nullable=False, default="execute")  # execute, dry_run
    triggered_by = Column(String(20), nullable=False, default="manual")  # manual, scheduled, m3u_refresh, api

    # Timing
    started_at = Column(DateTime, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Status
    status = Column(String(20), nullable=False, default="pending")  # pending, running, completed, failed, rolled_back
    error_message = Column(Text, nullable=True)  # Error details if failed

    # Statistics
    streams_evaluated = Column(Integer, default=0, nullable=False)
    streams_matched = Column(Integer, default=0, nullable=False)
    channels_created = Column(Integer, default=0, nullable=False)
    channels_updated = Column(Integer, default=0, nullable=False)
    groups_created = Column(Integer, default=0, nullable=False)
    streams_merged = Column(Integer, default=0, nullable=False)
    streams_skipped = Column(Integer, default=0, nullable=False)

    # For rollback - tracks what was created/modified
    # JSON array: [{type: "channel", id: 123, name: "ESPN HD"}, ...]
    created_entities = Column(Text, nullable=True)
    # JSON array: [{type: "channel", id: 99, previous: {name: "...", streams: [...]}}, ...]
    modified_entities = Column(Text, nullable=True)

    # Dry-run results (if mode=dry_run)
    # JSON array of planned actions
    dry_run_results = Column(Text, nullable=True)

    # Per-stream execution log (JSON array of log entries)
    # Captures condition evaluations and action results for each matched stream
    execution_log = Column(Text, nullable=True)

    # Rollback tracking
    rolled_back_at = Column(DateTime, nullable=True)
    rolled_back_by = Column(String(100), nullable=True)  # username or "system"

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship to rule
    rule = relationship("AutoCreationRule", lazy="joined")

    __table_args__ = (
        Index("idx_auto_exec_rule", rule_id),
        Index("idx_auto_exec_status", status),
        Index("idx_auto_exec_mode", mode),
        Index("idx_auto_exec_started", started_at.desc()),
        Index("idx_auto_exec_triggered_by", triggered_by),
    )

    def get_created_entities(self) -> list:
        """Parse created_entities JSON into list."""
        if not self.created_entities:
            return []
        try:
            import json
            return json.loads(self.created_entities)
        except (ValueError, TypeError):
            return []

    def set_created_entities(self, entities: list) -> None:
        """Set created_entities from list."""
        import json
        self.created_entities = json.dumps(entities) if entities else None

    def add_created_entity(self, entity_type: str, entity_id: int, name: str = None, extra: dict = None) -> None:
        """Add a created entity to the tracking list."""
        import json
        entities = self.get_created_entities()
        entity = {"type": entity_type, "id": entity_id}
        if name:
            entity["name"] = name
        if extra:
            entity.update(extra)
        entities.append(entity)
        self.created_entities = json.dumps(entities)

    def get_modified_entities(self) -> list:
        """Parse modified_entities JSON into list."""
        if not self.modified_entities:
            return []
        try:
            import json
            return json.loads(self.modified_entities)
        except (ValueError, TypeError):
            return []

    def set_modified_entities(self, entities: list) -> None:
        """Set modified_entities from list."""
        import json
        self.modified_entities = json.dumps(entities) if entities else None

    def add_modified_entity(self, entity_type: str, entity_id: int, previous_state: dict, name: str = None) -> None:
        """Add a modified entity to the tracking list with its previous state for rollback."""
        import json
        entities = self.get_modified_entities()
        entity = {"type": entity_type, "id": entity_id, "previous": previous_state}
        if name:
            entity["name"] = name
        entities.append(entity)
        self.modified_entities = json.dumps(entities)

    def get_dry_run_results(self) -> list:
        """Parse dry_run_results JSON into list."""
        if not self.dry_run_results:
            return []
        try:
            import json
            return json.loads(self.dry_run_results)
        except (ValueError, TypeError):
            return []

    def set_dry_run_results(self, results: list) -> None:
        """Set dry_run_results from list."""
        import json
        self.dry_run_results = json.dumps(results) if results else None

    def get_execution_log(self) -> list:
        """Parse execution_log JSON into list."""
        if not self.execution_log:
            return []
        try:
            import json
            return json.loads(self.execution_log)
        except (ValueError, TypeError):
            return []

    def set_execution_log(self, log: list) -> None:
        """Set execution_log from list."""
        import json
        self.execution_log = json.dumps(log) if log else None

    def to_dict(self, include_entities: bool = False, include_log: bool = False) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_name": self.rule_name or (self.rule.name if self.rule else None),
            "mode": self.mode,
            "triggered_by": self.triggered_by,
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "error_message": self.error_message,
            "streams_evaluated": self.streams_evaluated,
            "streams_matched": self.streams_matched,
            "channels_created": self.channels_created,
            "channels_updated": self.channels_updated,
            "groups_created": self.groups_created,
            "streams_merged": self.streams_merged,
            "streams_skipped": self.streams_skipped,
            "rolled_back_at": self.rolled_back_at.isoformat() + "Z" if self.rolled_back_at else None,
            "rolled_back_by": self.rolled_back_by,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }
        if include_entities:
            result["created_entities"] = self.get_created_entities()
            result["modified_entities"] = self.get_modified_entities()
        if self.mode == "dry_run":
            result["dry_run_results"] = self.get_dry_run_results()
        if include_log:
            result["execution_log"] = self.get_execution_log()
        return result

    def __repr__(self):
        return f"<AutoCreationExecution(id={self.id}, rule_id={self.rule_id}, status={self.status}, mode={self.mode})>"


class AutoCreationConflict(Base):
    """
    Tracks conflicts detected during pipeline execution.

    Conflicts occur when:
    - Multiple rules match the same stream
    - A channel with the target name already exists
    - Merge operation finds conflicting data
    """
    __tablename__ = "auto_creation_conflicts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    execution_id = Column(Integer, ForeignKey("auto_creation_executions.id", ondelete="CASCADE"), nullable=False)

    # What conflicted
    stream_id = Column(Integer, nullable=True)  # Dispatcharr stream ID
    stream_name = Column(String(255), nullable=True)  # Cached for display

    # Which rules were involved
    winning_rule_id = Column(Integer, nullable=True)  # Rule that was applied
    losing_rule_ids = Column(Text, nullable=True)  # JSON array of rule IDs that also matched but didn't execute

    # Conflict details
    conflict_type = Column(String(30), nullable=False)  # duplicate_match, channel_exists, merge_conflict, name_collision
    resolution = Column(String(30), nullable=False)  # skipped, merged, overwritten, created_anyway
    description = Column(Text, nullable=True)  # Human-readable description

    # Additional context (JSON)
    details = Column(Text, nullable=True)  # {existing_channel_id: 123, existing_channel_name: "...", ...}

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship to execution
    execution = relationship("AutoCreationExecution", lazy="joined")

    __table_args__ = (
        Index("idx_auto_conflict_execution", execution_id),
        Index("idx_auto_conflict_type", conflict_type),
        Index("idx_auto_conflict_stream", stream_id),
        Index("idx_auto_conflict_winning_rule", winning_rule_id),
    )

    def get_losing_rule_ids(self) -> list:
        """Parse losing_rule_ids JSON into list."""
        if not self.losing_rule_ids:
            return []
        try:
            import json
            return json.loads(self.losing_rule_ids)
        except (ValueError, TypeError):
            return []

    def set_losing_rule_ids(self, rule_ids: list) -> None:
        """Set losing_rule_ids from list."""
        import json
        self.losing_rule_ids = json.dumps(rule_ids) if rule_ids else None

    def get_details(self) -> dict:
        """Parse details JSON into dict."""
        if not self.details:
            return {}
        try:
            import json
            return json.loads(self.details)
        except (ValueError, TypeError):
            return {}

    def set_details(self, details: dict) -> None:
        """Set details from dict."""
        import json
        self.details = json.dumps(details) if details else None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "stream_id": self.stream_id,
            "stream_name": self.stream_name,
            "winning_rule_id": self.winning_rule_id,
            "losing_rule_ids": self.get_losing_rule_ids(),
            "conflict_type": self.conflict_type,
            "resolution": self.resolution,
            "description": self.description,
            "details": self.get_details(),
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

    def __repr__(self):
        return f"<AutoCreationConflict(id={self.id}, execution_id={self.execution_id}, type={self.conflict_type})>"


class FFmpegProfile(Base):
    """
    User-saved FFMPEG Builder profiles.
    Stores the full FFMPEGBuilderState config as JSON.
    """
    __tablename__ = "ffmpeg_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    config = Column(Text, nullable=False)  # JSON-serialized FFMPEGBuilderState
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_ffmpeg_profiles_created", created_at.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        import json
        return {
            "id": self.id,
            "name": self.name,
            "config": json.loads(self.config) if self.config else {},
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

    def __repr__(self):
        return f"<FFmpegProfile(id={self.id}, name={self.name})>"
