"""
SQLAlchemy ORM models for the Journal and Bandwidth tracking features.
"""
from datetime import datetime, date
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, Date, Float, Index
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
    """
    __tablename__ = "bandwidth_daily"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, unique=True)
    bytes_transferred = Column(BigInteger, default=0, nullable=False)
    peak_channels = Column(Integer, default=0, nullable=False)
    peak_clients = Column(Integer, default=0, nullable=False)

    __table_args__ = (
        Index("idx_bandwidth_daily_date", date.desc()),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "date": self.date.isoformat() if self.date else None,
            "bytes_transferred": self.bytes_transferred,
            "peak_channels": self.peak_channels,
            "peak_clients": self.peak_clients,
        }

    def __repr__(self):
        return f"<BandwidthDaily(date={self.date}, bytes={self.bytes_transferred})>"


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
