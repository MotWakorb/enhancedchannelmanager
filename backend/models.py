"""
SQLAlchemy ORM models for the Journal and Bandwidth tracking features.
"""
import json
import logging
from datetime import datetime, date
from sqlalchemy import Column, Integer, BigInteger, String, Text, Boolean, DateTime, Date, Float, Index, ForeignKey, UniqueConstraint, CheckConstraint
from sqlalchemy import text as sa_text
from sqlalchemy.orm import relationship
from db_base import Base

logger = logging.getLogger(__name__)


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
    # Actor / origin of the mutation (enhancedchannelmanager-vp1rx / W3).
    # One of: "ui" (operator via JWT), "mcp_ai" (AI agent via static MCP key),
    # "scheduler" (a scheduled task), "auto_creation" (the auto-creation pipeline).
    # NULL for legacy rows and any path that did not stamp an actor — distinct
    # from ``user_initiated`` (manual-vs-automatic), which is left untouched for
    # back-compat. ``mutation_source`` answers WHO/WHAT initiated the change so
    # an AI-driven mutation is traceable and recoverable; ``user_initiated`` only
    # answers whether a human clicked a button.
    mutation_source = Column(String(20), nullable=True)
    # Automation marker (enhancedchannelmanager-uliyr follow-up). Whether the
    # write came from a self-declared automated client:
    #   True  — the request carried the ``X-ECM-Automated-Client`` header
    #           (the backend E2E harness) — eligible for the noise purge.
    #   False — an /api/* request WITHOUT the header (a real UI/MCP
    #           operator) — the noise purge must keep these rows.
    #   NULL  — pre-marker legacy rows and non-HTTP internal writers
    #           (scheduler, pipelines, bandwidth tracker). Legacy rule
    #           create/delete rows keep aging out of the noise purge until
    #           the unmarked set shrinks to zero.
    # Orthogonal to ``mutation_source`` (WHO: ui/mcp_ai/scheduler/...) —
    # the E2E harness authenticates as a real user, so both automated and
    # operator traffic read ``mutation_source="ui"``; only this
    # self-declaration separates them. No index: the sole consumer is the
    # noise purge's category/action-narrowed scan (idx_journal_category).
    automated_client = Column(Boolean, nullable=True)

    # Indexes for common queries.
    #
    # bd-dmu8w added the next two on top of the bd-91mcq per-entity audit work:
    #
    # * ``idx_journal_batch_id`` — single-column on ``batch_id``. Forensic
    #   "show me everything in batch X" queries (auto-creation bulk runs)
    #   used to full-scan; bulk operations now amplify row growth N-fold,
    #   making the scan cost grow with table size.
    # * ``idx_journal_entity`` — composite ``(category, entity_id, timestamp DESC)``
    #   for the "history for this entity" access pattern (e.g.
    #   ``WHERE category='channel' AND entity_id=42 ORDER BY timestamp DESC``).
    #   Leading columns are the equality filters; the trailing
    #   ``timestamp DESC`` lets SQLite serve newest-first ranges from the
    #   index without an additional sort.
    __table_args__ = (
        Index("idx_journal_timestamp", timestamp.desc()),
        Index("idx_journal_category", category),
        Index("idx_journal_action_type", action_type),
        Index("idx_journal_batch_id", batch_id),
        Index("idx_journal_entity", category, entity_id, timestamp.desc()),
        # Single-column index for the "show me everything the AI did" forensic
        # query (``WHERE mutation_source='mcp_ai'``) surfaced by the journal API
        # filter (enhancedchannelmanager-vp1rx / W3).
        Index("idx_journal_mutation_source", mutation_source),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
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
            "mutation_source": self.mutation_source,
            "automated_client": self.automated_client,
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
    Legacy per-channel watch-stats aggregate (pre-v0.17.0).

    Tracks watch counts and time per channel. Each time a channel was
    seen active in stats, we used to increment its watch count, and
    watch time accumulated while a channel remained active.

    v0.17.0 (bd-skqln.3 step (d)): this table is **no longer written
    from BandwidthTracker._collect_stats**. The popularity calculator
    and the ``/api/stats/top-watched`` endpoint derive their inputs
    from ``session_telemetry`` (per-poll grain) and
    ``unique_client_connections`` (channel name side-load) instead.
    See ``docs/database_migrations.md`` → "Backfill policy for
    session_telemetry" for the cutover-day reasoning.

    The schema is intentionally **kept** at v0.17.0 — pre-cutover rows
    are still here. The settings reset paths still delete from it.
    Dropping the table is a separate decision tracked as a follow-up
    cleanup bead once we are confident nothing in the post-cutover code
    paths reads it. Until then, ``channel_watch_stats_v`` (migration
    0008) is the read-compat view over ``session_telemetry`` for the
    columns that faithfully map across the grain change.
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
    consecutive_failures = Column(Integer, default=0, nullable=False)  # Strike rule: consecutive probe failures
    is_black_screen = Column(Boolean, default=False, nullable=False)  # Black screen detected during probe
    is_low_fps = Column(Boolean, default=False, nullable=False)  # Low FPS detected during probe (< 20 FPS)

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
            "consecutive_failures": self.consecutive_failures or 0,
            "is_black_screen": self.is_black_screen or False,
            "is_low_fps": self.is_low_fps or False,
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
    {"channel_groups": ["Sports", "News"], "timeout": 30, "max_concurrent": 8}
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
            return json.loads(self.parameters)
        except (ValueError, TypeError):
            return {}

    def set_parameters(self, params: dict) -> None:
        """Set parameters from dictionary."""
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
    # When True, a tag_group prefix/suffix match requires a STRONG delimiter
    # (':', '-', '|', '/' — surrounding spaces allowed) after/before the tag,
    # NOT a bare space (bd-0emgo.2). This distinguishes a category column
    # ("NFL: Buffalo Bills" -> strip) from a brand name ("NFL RedZone" -> keep).
    # Default False preserves the legacy bare-space-accepting behavior.
    # server_default="0" matches the Alembic 0020 add-column so ORM/migration
    # agree (no schema drift) and the NOT NULL add succeeds on populated tables.
    require_delimiter = Column(
        Boolean, default=False, server_default="0", nullable=False
    )
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
            "require_delimiter": self.require_delimiter,
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
            except (json.JSONDecodeError, TypeError) as e:
                logger.debug("[MODELS] Suppressed alert_sources JSON parse error: %s", e)

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
            return json.loads(self.groups_data)
        except (ValueError, TypeError):
            return {"groups": []}

    def set_groups_data(self, data: dict) -> None:
        """Set groups_data from dictionary."""
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

    # Relationship to snapshot.
    # lazy="select" (load-on-access), NOT "joined": M3USnapshot.groups_data is a
    # large JSON blob (every group's full stream-name list). Every change row from
    # a refresh shares one snapshot_id, so a joined eager load re-streams that blob
    # once per row — an O(rows x blob) memory fanout that OOM-killed the container
    # on the unbounded digest query (GH #473). Nothing reads .snapshot anyway
    # (to_dict() uses the snapshot_id FK column), so on-demand loading is free here.
    snapshot = relationship("M3USnapshot", lazy="select")

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
            return json.loads(self.stream_names)
        except (ValueError, TypeError):
            return []

    def set_stream_names(self, names: list) -> None:
        """Set stream_names from list."""
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
    # JSON arrays of regex patterns for excluding groups/streams from digest
    exclude_group_patterns = Column(Text, nullable=True)
    exclude_stream_patterns = Column(Text, nullable=True)
    # JSON array of M3U account IDs to include in digest NOTIFICATIONS
    # (GH #496). Scopes which accounts' changes are emailed/Discorded —
    # DB logging in M3UChangeLog stays complete for every account
    # regardless of this setting. Empty/null = all accounts (unchanged
    # default behavior). Added for operators running a high-churn "FAST"
    # provider (10k+ stream URL changes/hour) alongside slow-changing
    # standard providers, who want the noisy provider excluded from
    # notifications without losing its change history.
    account_ids = Column(Text, nullable=True)
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
            return json.loads(self.email_recipients)
        except (ValueError, TypeError):
            return []

    def set_email_recipients(self, emails: list) -> None:
        """Set email_recipients from list."""
        self.email_recipients = json.dumps(emails) if emails else None

    def get_exclude_group_patterns(self) -> list:
        """Parse exclude_group_patterns JSON into list."""
        if not self.exclude_group_patterns:
            return []
        try:
            return json.loads(self.exclude_group_patterns)
        except (ValueError, TypeError):
            return []

    def set_exclude_group_patterns(self, patterns: list) -> None:
        """Set exclude_group_patterns from list."""
        self.exclude_group_patterns = json.dumps(patterns) if patterns else None

    def get_exclude_stream_patterns(self) -> list:
        """Parse exclude_stream_patterns JSON into list."""
        if not self.exclude_stream_patterns:
            return []
        try:
            return json.loads(self.exclude_stream_patterns)
        except (ValueError, TypeError):
            return []

    def set_exclude_stream_patterns(self, patterns: list) -> None:
        """Set exclude_stream_patterns from list."""
        self.exclude_stream_patterns = json.dumps(patterns) if patterns else None

    def get_account_ids(self) -> list:
        """Parse account_ids JSON into list."""
        if not self.account_ids:
            return []
        try:
            return json.loads(self.account_ids)
        except (ValueError, TypeError):
            return []

    def set_account_ids(self, ids: list) -> None:
        """Set account_ids from list."""
        self.account_ids = json.dumps(ids) if ids else None

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
            "exclude_group_patterns": self.get_exclude_group_patterns(),
            "exclude_stream_patterns": self.get_exclude_stream_patterns(),
            "account_ids": self.get_account_ids(),
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
    user_id = Column(Integer, nullable=True)  # Dispatcharr user ID (null if not available)
    username = Column(String(255), nullable=True)  # Cached username from Dispatcharr
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
            "user_id": self.user_id,
            "username": self.username,
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
# Stats v2 — session_telemetry fact table (v0.17.0)
# =============================================================================

class SessionTelemetry(Base):
    """Unified per-poll stream-telemetry fact table (Stats v2).

    One row is written per ``BandwidthTracker`` poll cycle for each active
    viewing session. Append-mostly: rows are pruned by ``observed_at`` per the
    retention policy in ``docs/adr/ADR-007-session-telemetry-retention.md``
    (raw rows pruned at 30 days; the daily rollup tables, the
    ``telemetry_rollup_state`` marker, and the nightly prune job are out of
    scope here — they live in bead ``enhancedchannelmanager-7i2vv``).

    Privacy classification + redaction rules:
    ``docs/security/threat_model_stats_v2.md`` §2.1, §7. ``user_id`` is an
    **opaque Dispatcharr-side identifier — NOT a FK to ECM ``users``**.
    The FK was dropped in migration 0011 (bd-gsn3r): ECM's local ``users``
    table holds ECM auth identities, while Dispatcharr's ``users`` table
    holds stream-viewer accounts; the two namespaces happen to use
    overlapping integer IDs but mean different things. The FK was a
    structural lie — a join from this column to ``users`` returned the
    wrong human's username when integer IDs collided. The denormalized
    ``dispatcharr_username`` column carries the Dispatcharr-side username
    captured at write time (no read-side lookups against any ECM table).
    ``channel_id`` is a plain indexed ``String(64)`` Dispatcharr UUID —
    NOT a foreign key (``channels`` is not an ECM table) and matches
    every other channel-keyed table in this schema (``channel_watch_stats``
    / ``channel_bandwidth`` / ``channel_popularity_scores`` /
    ``unique_client_connections``). ``provider_id`` is a plain nullable
    indexed integer (provider tagging, skqln.14, is still pending).

    NOTE: migration 0006 originally declared ``channel_id INTEGER NULL`` —
    that was inconsistent with the rest of the schema and the writer (which
    keys channels by Dispatcharr UUID string). Migration 0007 corrects it
    in-place: ``channel_watch_stats_v`` (skqln.3 step (b)) builds on top of
    this column and needs the UUID shape to GROUP BY a channel meaningfully.

    ``bitrate_bps`` is deliberately NOT stored — it is derivable from
    ``bytes_delta / poll_interval_ms`` and storing a derived value invites
    disagreement (DBA, team-plan).

    Bead: ``enhancedchannelmanager-skqln.2``.
    """
    __tablename__ = "session_telemetry"

    # Synthetic PK — matches the house pattern for these fact tables
    # (channel_bandwidth, channel_popularity_scores). SQLite needs a PK; the
    # query/access keys are the composite indexes below.
    id = Column(Integer, primary_key=True, autoincrement=True)

    # UUID string correlating all rows of one viewing session.
    session_id = Column(Text, nullable=False)
    # Unix-epoch milliseconds. Mandatory index — retention sweeps and all
    # time-range queries key off this.
    observed_at = Column(Integer, nullable=False)

    # Opaque Dispatcharr-side viewer identifier — NOT a FK to ECM ``users``.
    # The FK was dropped in migration 0011 (bd-gsn3r); see the class
    # docstring for the namespace-collision rationale. ``_coerce_session_user_id``
    # in ``bandwidth_tracker.py`` still scrubs the anonymous ``0``
    # sentinel to NULL so analytics queries see honest "anonymous" rather
    # than the noise value.
    user_id = Column(Integer, nullable=True)
    # Denormalized Dispatcharr-side username for the row's ``user_id``.
    # Captured at write time from the per-poll ``dispatcharr_user_map``
    # the writer already maintains for the bd-uqbob exclude filter — no
    # read-side lookups against ECM's ``users`` table. NULL when the
    # writer could not resolve the username (anonymous viewer with
    # ``user_id=NULL``, Dispatcharr ``get_users()`` failure that poll,
    # or a pre-0011 row that landed before the column existed). Added
    # in migration 0011 (bd-gsn3r).
    dispatcharr_username = Column(Text, nullable=True)
    # Upstream Dispatcharr M3U provider. Plain indexed column, NOT a FK
    # (providers is not an ECM table). Nullable — provider tagging (GH-59)
    # lands separately (skqln.14).
    provider_id = Column(Integer, nullable=True)
    # Upstream Dispatcharr channel UUID. Plain indexed column, NOT a FK
    # (channels is not an ECM table). String(64) matches every other
    # channel-keyed table in this schema. NOT NULL — every writer of a
    # session_telemetry row (BandwidthTracker today; future buffer-event
    # ingest in skqln.15) is tied to an active channel by definition.
    channel_id = Column(String(64), nullable=False)

    # Per-poll bytes delta (NOT cumulative). Named CHECK so the constraint
    # name is stable across SQLite versions (docs/database_migrations.md).
    bytes_delta = Column(BigInteger, nullable=False)
    # Count of ``channel_buffering`` events observed during this poll
    # window. Preserved verbatim from the pre-0012 schema — see the
    # per-type counters below for the bd-ov5vb broadening that surfaced
    # reconnect/error/switch events alongside this one. On real installs
    # ``channel_buffering`` is rare (ffmpeg-speed threshold only); the
    # operationally-meaningful health signals are the three counters
    # below.
    buffer_event_count = Column(Integer, nullable=False, server_default="0", default=0)
    # bd-ov5vb (migration 0013): per-type channel-event counters paired
    # with ``buffer_event_count``. Pre-0012 the writer pulled only
    # ``event_type=buffering`` from Dispatcharr's system-events feed,
    # which on real installs returned zero because the events that
    # actually represent channel-health problems are
    # ``channel_reconnect`` / ``channel_error`` / ``stream_switch``.
    # The broadened ingest in ``BandwidthTracker._collect_channel_events``
    # buckets each event type into its own column so the Providers panel
    # can surface them distinctly without losing the per-poll
    # attribution. Each is INTEGER NOT NULL DEFAULT 0 so
    # ``SUM(<column>) GROUP BY provider, time_bucket`` works the same
    # way the legacy buffer rollup does. Attribution model is identical
    # to ``buffer_event_count``: each per-channel count lands on the
    # FIRST row emitted for that channel per poll; sibling rows write 0
    # so per-client double-counting cannot inflate the aggregate.
    reconnect_event_count = Column(
        Integer, nullable=False, server_default="0", default=0
    )
    error_event_count = Column(
        Integer, nullable=False, server_default="0", default=0
    )
    switch_event_count = Column(
        Integer, nullable=False, server_default="0", default=0
    )
    # Poll cadence in ms — avoids baking in a fixed-interval assumption and
    # makes bitrate derivable.
    poll_interval_ms = Column(Integer, nullable=False)
    # Dispatcharr stream row id (``streams.id`` upstream). Plain nullable
    # column, NOT a FK — ``streams`` is not an ECM table, matching the
    # ``provider_id`` / ``channel_id`` design above. NULL when the resolver
    # could not attribute the active stream (same failure modes as
    # provider_id; see ``_resolve_provider_ids`` docstring). Added in
    # migration 0010 (bd-kh23e).
    stream_id = Column(Integer, nullable=True)
    # Stream display name (``name`` field on Dispatcharr's stream record,
    # e.g. ``"US: TNT"``). Side-loaded by the same batched
    # ``get_streams_by_ids`` call that powers provider attribution — zero
    # extra round-trips per poll. NULL on the same conditions as
    # ``stream_id``. The frontend composes the display label as
    # ``[<provider_name>] - <stream_name>`` (PO directive 2026-05-14).
    # Added in migration 0010 (bd-kh23e).
    stream_name = Column(Text, nullable=True)
    # bd-k026g (migration 0016): Emby user attribution columns. ECM only
    # sees the Dispatcharr stream session's IP, and when users watch via
    # an Emby server ALL stream pulls share that one IP — Stats can't
    # distinguish individual Emby viewers without enrichment. The Emby
    # integration (parent epic bd-2cenq) cross-references each live Emby
    # session against ECM's active streams; when a match is resolved at
    # write time the ``BandwidthTracker`` writer (bd-gih6d) populates
    # these two columns from the per-poll Emby ``/Sessions`` lookup the
    # resolver maintains. Both columns are NULL for non-Emby rows (most
    # rows on most installs — only sessions whose client IP matches the
    # configured Emby server IP AND has a concurrent matching Emby
    # session are enriched). ``emby_user_id`` is TEXT because Emby user
    # IDs are GUIDs, NOT integers like the Dispatcharr ``user_id``
    # column. Denormalized on this table rather than split into a
    # separate ``emby_users`` table to keep Stats v2 query patterns
    # flat — same rationale as the bd-gsn3r ``dispatcharr_username``
    # denormalization. Read paths surface these columns verbatim;
    # there are no read-side joins against any Emby endpoint or local
    # table. Plain nullable columns, NOT FKs — Emby's user table is
    # upstream and not an ECM table, mirroring the established pattern
    # for every other upstream identifier on this row (``user_id``,
    # ``provider_id``, ``channel_id``, ``stream_id``).
    emby_user_id = Column(Text, nullable=True)
    emby_user_name = Column(Text, nullable=True)
    # bd-r5f0c.1 (migration 0017): Plex + Jellyfin user attribution
    # columns, at parity with the Emby pair above. Identical rationale —
    # ECM only sees the Dispatcharr stream session's IP, so when users
    # watch via a Plex or Jellyfin server all stream pulls collapse to
    # the media server's IP. The Plex (W2) and Jellyfin (W3) resolvers
    # cross-reference each live upstream session against ECM's active
    # streams and the W4 ``BandwidthTracker`` writer populates these
    # four columns from the per-poll upstream lookup. Both ID columns
    # are TEXT — Plex serves user IDs as strings in its ``/sessions``
    # payload, and Jellyfin (Emby fork) uses GUID-string IDs — staying
    # aligned with the ``emby_user_id`` TEXT choice. All four are NULL
    # for non-Plex / non-Jellyfin rows. Plain nullable columns, NOT
    # FKs — Plex's and Jellyfin's user tables are upstream and not ECM
    # tables, mirroring the established pattern for every other
    # upstream identifier on this row.
    plex_user_id = Column(Text, nullable=True)
    plex_user_name = Column(Text, nullable=True)
    jellyfin_user_id = Column(Text, nullable=True)
    jellyfin_user_name = Column(Text, nullable=True)
    # bd-r5f0c.9 (migration 0018): multi-viewer attribution. Media servers
    # are transcoding proxies — N upstream viewers share ONE ECM client
    # (the server itself). The single-viewer columns above retain the
    # most-recent viewer for back-compat (Stats v2 aggregations, frontend
    # rendering pre-W5); these three new TEXT columns hold the FULL list
    # of viewers as JSON-encoded ``[{"user_id": "...", "user_name":
    # "..."}, ...]`` strings (or NULL when the source matched zero
    # viewers). Application-layer JSON serialization (``json.dumps`` at
    # write time, ``json.loads`` on read) — not SQLAlchemy's JSON type —
    # because SQLite stores JSON as TEXT either way and the in-row
    # serialization keeps the column shape identical to the other TEXT
    # attribution columns above (no driver-specific type adapter coupling
    # for the W5 frontend reader to know about). Order in the list is
    # ``last_activity_date`` descending so position 0 is the most-recent
    # viewer (matches the legacy *_user_name column's content).
    emby_viewers = Column(Text, nullable=True)
    plex_viewers = Column(Text, nullable=True)
    jellyfin_viewers = Column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "bytes_delta >= 0",
            name="ck_session_telemetry_bytes_delta_non_negative",
        ),
        # Retention sweeps + bare time-range scans.
        Index("idx_session_telemetry_observed_at", observed_at),
        # GH-62 "watch time by user" — index-range scan per user.
        Index("idx_session_telemetry_user_observed", user_id, observed_at),
        # GH-59 per-provider performance (forward-looking).
        Index("idx_session_telemetry_provider_observed", provider_id, observed_at),
        # Session reconstruction.
        Index("idx_session_telemetry_session_id", session_id),
        # GH-59 channels-by-provider heatmap (skqln.16). Trailing bytes_delta
        # makes it a covering index for that aggregate. Plain composite — the
        # Postgres ``INCLUDE`` form is not valid in SQLite; an ``INCLUDE``
        # variant is a Postgres-migration future enhancement (ADR D7).
        Index(
            "idx_session_telemetry_provider_channel_observed_bytes",
            provider_id,
            channel_id,
            observed_at,
            bytes_delta,
        ),
    )

    def __repr__(self):
        return (
            f"<SessionTelemetry(id={self.id}, session={self.session_id}, "
            f"observed_at={self.observed_at}, user_id={self.user_id}, "
            f"provider_id={self.provider_id}, channel_id={self.channel_id})>"
        )


# =============================================================================
# Stats v2 — daily rollup tables + once-per-day marker (ADR-007 D3/D4/D5)
# =============================================================================

class SessionTelemetryUserDaily(Base):
    """Per-user, per-channel, per-UTC-day watch rollup (ADR-007 D4).

    Materialised daily by the ``stats_v2_rollup`` nightly task (see
    ``backend/tasks/stats_v2_rollup.py``). Reads: the Users panel watch-time
    selector (``SUM(watch_seconds) GROUP BY user_id WHERE day >= ?``) and
    the per-channel breakdown (``GROUP BY channel_id WHERE user_id = ?``).

    Why a rollup *table*, not a view: ADR-007 D2 — a view over
    ``session_telemetry`` re-scans up to 26M raw rows on every panel load;
    the daily-rollup table is on the order of thousands of rows.

    NULL ``user_id`` handling: raw ``session_telemetry.user_id`` is
    nullable (anonymous/system traffic with no behavioral subject to
    attribute). The rollup intentionally **excludes** NULL ``user_id``
    rows — there is no user to attribute the watch time to, so no row is
    written for them. This is the per-user analog of the per-provider
    ``'unknown'`` bucket; the per-user case drops rather than buckets
    because a fabricated ``user_id = -1`` (or similar) would pollute the
    ``users`` namespace and surface in the Users panel as a non-existent
    user. The per-provider rollup uses a literal ``'unknown'`` string
    bucket because TEXT ``provider_id`` allows it without colliding with
    real provider IDs.

    Bead: ``enhancedchannelmanager-7i2vv``.
    """
    __tablename__ = "session_telemetry_user_daily"

    # Composite PK matching the access pattern (Users panel queries).
    # Dispatcharr user id from the FK in session_telemetry; INTEGER NOT NULL
    # — NULL raw rows are excluded at rollup time (no behavioral subject).
    user_id = Column(Integer, primary_key=True, nullable=False)
    # Dispatcharr channel UUID; String(64) matching session_telemetry.channel_id
    # (after migration 0007) and every other channel-keyed table in the schema.
    channel_id = Column(String(64), primary_key=True, nullable=False)
    # UTC calendar day.
    day = Column(Date, primary_key=True, nullable=False)

    # Sum of distinct (channel_id, observed_at) poll intervals for the
    # (user, channel, day) grouping, in seconds. Same DISTINCT-(channel_id,
    # observed_at) collapse the channel_watch_stats_v view uses (skqln.3
    # step (b)) so per-poll-per-client multiplicity doesn't inflate the
    # number.
    watch_seconds = Column(Integer, nullable=False)
    # Distinct session_ids contributing to this (user, channel, day).
    # Approximates "distinct viewing sessions"; feeds the panel's
    # "times watched" column.
    session_count = Column(Integer, nullable=False)

    __table_args__ = (
        Index("idx_session_telemetry_user_daily_day", "day"),
    )

    def __repr__(self):
        return (
            f"<SessionTelemetryUserDaily(user_id={self.user_id}, "
            f"channel_id={self.channel_id}, day={self.day}, "
            f"watch_seconds={self.watch_seconds})>"
        )


class SessionTelemetryProviderDaily(Base):
    """Per-provider, per-channel, per-UTC-day performance rollup (ADR-007 D5).

    Materialised daily by the ``stats_v2_rollup`` nightly task. Reads:
    every visualisation on the Providers panel — buffering-by-provider
    time series, time-per-provider stacked area, channels-by-provider
    heatmap, and bitrate-by-provider (derived from
    ``bytes_delta_sum * 8 / watch_seconds``).

    NULL ``provider_id`` handling: raw ``session_telemetry.provider_id`` is
    nullable INTEGER — the resolver (skqln.14) is best-effort and a miss
    leaves provider unset. ADR-007 §line 109 requires that those rows
    **surface as an ``'unknown'`` bucket, not silently drop**. The rollup
    job coalesces raw NULLs to the literal string ``'unknown'`` at rollup
    time and writes them under that PK; this column is therefore TEXT NOT
    NULL even though the source column is INTEGER nullable. The cost is
    one CAST in the rollup INSERT; the gain is that the GH-59 "silently
    lies" DBA pushback never materialises.

    Bead: ``enhancedchannelmanager-7i2vv``.
    """
    __tablename__ = "session_telemetry_provider_daily"

    # TEXT (not INTEGER) so the 'unknown' sentinel from ADR-007 §line 109
    # can live in the PK as a literal string without colliding with real
    # provider IDs. The rollup job is responsible for the CAST at write
    # time.
    provider_id = Column(Text, primary_key=True, nullable=False)
    # Dispatcharr channel UUID; String(64) matches session_telemetry.
    channel_id = Column(String(64), primary_key=True, nullable=False)
    # UTC calendar day.
    day = Column(Date, primary_key=True, nullable=False)

    # Total watch time for the (provider, channel, day) — same DISTINCT-
    # (channel_id, observed_at) collapse used in channel_watch_stats_v
    # (skqln.3) and the per-user rollup. Feeds the stacked-area
    # "time per provider" visualisation.
    watch_seconds = Column(Integer, nullable=False)
    # Summed bytes for the day; bitrate is derivable as
    # bytes_delta_sum * 8 / watch_seconds (epic decision — store the
    # numerator, not the derived rate).
    bytes_delta_sum = Column(BigInteger, nullable=False)
    # Count of buffer/stall events ingested for this provider that day
    # (skqln.15). Preserved for back-compat with pre-bd-d0ha9 read paths.
    buffer_event_count = Column(Integer, nullable=False)
    # Per-type channel-event counters added by migration 0014 (bd-d0ha9).
    # Companion columns to the three that migration 0013 (bd-ov5vb) added
    # to the raw ``session_telemetry`` table; the rollup now SUMs them
    # alongside ``buffer_event_count`` so historical data beyond the 30-day
    # raw-row retention window retains the full event-type breakdown.
    reconnect_event_count = Column(
        Integer, nullable=False, server_default="0", default=0
    )
    error_event_count = Column(
        Integer, nullable=False, server_default="0", default=0
    )
    switch_event_count = Column(
        Integer, nullable=False, server_default="0", default=0
    )

    __table_args__ = (
        Index(
            "idx_session_telemetry_provider_daily_provider_day",
            "provider_id",
            "day",
        ),
        Index("idx_session_telemetry_provider_daily_day", "day"),
    )

    def __repr__(self):
        return (
            f"<SessionTelemetryProviderDaily(provider_id={self.provider_id}, "
            f"channel_id={self.channel_id}, day={self.day}, "
            f"watch_seconds={self.watch_seconds}, "
            f"bytes_delta_sum={self.bytes_delta_sum})>"
        )


class TelemetryRollupState(Base):
    """Once-per-calendar-day marker for each named rollup (ADR-007 D3).

    One row per named rollup (``user_daily``, ``provider_daily``). The
    ``stats_v2_rollup`` nightly task reads ``last_completed_day`` to
    decide which days still need rolling up (catch-up budget = D1 retention
    window minus margin), and writes ``last_run_at_ms`` /
    ``last_run_status`` / ``last_run_error`` after each pass so SRE can
    alert on staleness (failure modes 1+2: >36h warn, >25d page) and the
    Providers panel can read "last updated" from a single source of truth.

    Why this is a separate table rather than a settings key: the marker
    is rollup-name keyed (two named rollups today, more later) and carries
    structured columns the settings KV would have to JSON-encode. A
    bespoke 5-column table is cheaper to read, easier to alert on, and
    survives a settings reset (which a routine support flow can perform).

    Bead: ``enhancedchannelmanager-7i2vv``.
    """
    __tablename__ = "telemetry_rollup_state"

    # 'user_daily' | 'provider_daily' — see ROLLUP_NAME_USER_DAILY /
    # ROLLUP_NAME_PROVIDER_DAILY in tasks/stats_v2_rollup.py for the
    # canonical string constants used by the writer + the staleness alert.
    rollup_name = Column(Text, primary_key=True, nullable=False)
    # Most recent UTC day this rollup has durably aggregated. Nullable for
    # the first-run case (no day has yet been rolled up).
    last_completed_day = Column(Date, nullable=True)
    # Wall-clock ms-since-epoch of the most recent run (success OR failure).
    # SRE's staleness alert reads this — "no successful run in >36h" is
    # encoded as ``(now_ms - last_run_at_ms) > 36*3600*1000 AND
    # last_run_status != 'success'`` plus the symmetric "no run at all".
    last_run_at_ms = Column(BigInteger, nullable=True)
    # 'success' | 'failure' | 'partial' (a multi-rollup run where one
    # rollup succeeded and another failed records 'partial').
    last_run_status = Column(Text, nullable=True)
    # Error detail on failure, NULL on success. Held as free-form text
    # rather than a structured exception class so the runbook can quote
    # the message verbatim during triage.
    last_run_error = Column(Text, nullable=True)

    def __repr__(self):
        return (
            f"<TelemetryRollupState(rollup_name={self.rollup_name!r}, "
            f"last_completed_day={self.last_completed_day}, "
            f"last_run_status={self.last_run_status!r})>"
        )


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

    # Rotation grace window (bd-x67qe): hash of the immediately-prior refresh
    # token and when it was rotated away. For a short window after rotation
    # the predecessor is still accepted by /auth/refresh (idempotent-refresh
    # semantics) so two tabs racing the same rotation don't hard-logout the
    # loser. Only ONE generation is kept — a normal rotation overwrites both
    # fields, so a graced token can never chain to an older one.
    prior_refresh_token_hash = Column(String(255), nullable=True)
    rotated_at = Column(DateTime, nullable=True)

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
        Index("idx_session_prior_token_hash", prior_refresh_token_hash),
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

class ChannelPipelineRule(Base):
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
    sort_regex = Column(String(500), nullable=True)  # Regex with capture group for stream_name_regex sort

    # Stream-level sorting - reorders streams within channels (Pass 3.5)
    stream_sort_field = Column(String(50), nullable=True)  # None = no stream reorder
    stream_sort_order = Column(String(4), default="asc")   # "asc" or "desc"
    # When stream_sort_field is quality: tie-break equal resolution using ECM M3U priorities
    quality_tie_break_order = Column(String(4), default="desc")  # "asc" or "desc"
    quality_m3u_tie_break_enabled = Column(Boolean, default=True, nullable=False)

    # Normalization - JSON array of NormalizationRuleGroup IDs to apply, null/empty = disabled
    normalization_group_ids = Column(Text, nullable=True)

    # Strike filtering - skip streams that have been struck out (consecutive_failures >= strike_threshold)
    skip_struck_streams = Column(Boolean, default=False, nullable=False)

    # Tracking
    last_run_at = Column(DateTime, nullable=True)
    last_run_stats = Column(Text, nullable=True)  # JSON: {matched: 10, created: 5, skipped: 5, errors: 0}
    match_count = Column(Integer, default=0)  # Cumulative match count across all executions

    # Reconciliation - tracks which channels this rule currently owns
    # JSON array of channel IDs. Null = never run (first run will populate without deletions)
    managed_channel_ids = Column(Text, nullable=True)

    # Orphan cleanup behavior: "delete", "move_uncategorized", "delete_and_cleanup_groups", or "none"
    orphan_action = Column(String(30), default="delete", nullable=False)

    # Duplicate-check scope: when True, existing-channel name lookups during
    # create_channel are restricted to the rule's target group, so two rules
    # targeting different groups can create separate channels with the same
    # name instead of merging into an existing channel in another group
    # (GH-92, bd-r9mtd). Code default is True for new rules (bd-p6ko9, GH #226):
    # the all-groups lookup is a footgun for create_channel if_exists=merge.
    # Existing rule rows keep their explicit stored value (Alembic 0002
    # backfilled False into every pre-existing row) — flipping the code
    # default does NOT migrate them; no Alembic revision is needed.
    match_scope_target_group = Column(Boolean, default=True, nullable=False)

    # Explicit rule-level scope group for merge lookups (GH #298, bd-kncun).
    # When match_scope_target_group is on, this column lets the operator pin
    # the group that name lookups are restricted to — independent of any
    # Create Channel action's Target Group. NULL (the default) preserves the
    # prior behavior: create_channel derives the scope from the action's
    # effective group_id, and merge_streams stays group-agnostic. A non-NULL
    # value is enforced across BOTH create_channel and merge_streams name
    # lookups so a Merge-Streams-only rule can finally scope its match to one
    # group instead of matching same-name channels in any group.
    match_scope_group_id = Column(Integer, nullable=True)

    # Manual-channel isolation (enhancedchannelmanager-orzck / W1). When False
    # (the default, and the safe behavior), auto-creation will NOT adopt a
    # hand-built MANUAL channel (a channel whose ``auto_created`` flag is
    # missing/falsy) as a merge/update/rename target on a name collision — the
    # manual channel is treated as "not found" and a new auto channel is created
    # instead. This fixes the reported bleed where merging auto-created channels
    # overwrote regular (manual) channels' names/metadata/filters. Set True to
    # opt a rule back into the legacy behavior of adopting same-name manual
    # channels; each adoption is journaled for audit.
    allow_manual_channel_merge = Column(Boolean, default=False, nullable=False)

    # Fold match key (GH #645 / bead 0vao3). When True (opt-in, default
    # False so existing installs are unchanged), the create_channel
    # ``if_exists`` merge lookup additionally compares names by a canonical
    # fold key — casefold + strip ALL whitespace (match_fold.fold_match_key)
    # — so spelling variants like "eurosport 2" / "Eurosport2" merge into one
    # channel instead of creating duplicates. Comparison key ONLY: visible
    # channel names are never altered (this is deliberately NOT a
    # normalization rule — see docs/normalization.md parity contract).
    # Caveat: folding can over-merge genuinely distinct channels whose names
    # differ only in spacing/case, which is why it is per-rule opt-in.
    fold_match_key = Column(Boolean, default=False, nullable=False)

    # Event Sync (enhancedchannelmanager-ti939.1.3, epic ti939). JSON config
    # for the event_sync rule KIND: master_group_id, secondary_group_ids[],
    # optional parse patterns (shared or per-group title/time/date regexes),
    # time_window_minutes, attach_threshold, enabled. A rule with a non-NULL
    # value IS an event_sync rule (see is_event_sync()); NULL = standard rule,
    # so every pre-feature row keeps its exact prior behavior. Validated at
    # write time by channel_pipeline_schema.validate_event_sync_config().
    # Alembic 0031. PO decision (stateless recompute): NO new tables — this
    # one nullable column plus journal provenance rows is the feature's entire
    # durable state; channel IDs are never persisted across runs.
    event_sync_config = Column(Text, nullable=True)

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
            return json.loads(self.conditions)
        except (ValueError, TypeError):
            return []

    def set_conditions(self, conditions: list) -> None:
        """Set conditions from list."""
        self.conditions = json.dumps(conditions) if conditions else "[]"

    def get_actions(self) -> list:
        """Parse actions JSON into list."""
        if not self.actions:
            return []
        try:
            return json.loads(self.actions)
        except (ValueError, TypeError):
            return []

    def set_actions(self, actions: list) -> None:
        """Set actions from list."""
        self.actions = json.dumps(actions) if actions else "[]"

    def get_last_run_stats(self) -> dict:
        """Parse last_run_stats JSON into dict."""
        if not self.last_run_stats:
            return {}
        try:
            return json.loads(self.last_run_stats)
        except (ValueError, TypeError):
            return {}

    def set_last_run_stats(self, stats: dict) -> None:
        """Set last_run_stats from dict."""
        self.last_run_stats = json.dumps(stats) if stats else None

    def get_managed_channel_ids(self) -> list[int]:
        """Parse managed_channel_ids JSON into list of ints."""
        if not self.managed_channel_ids:
            return []
        try:
            return json.loads(self.managed_channel_ids)
        except (ValueError, TypeError):
            return []

    def set_managed_channel_ids(self, ids: list[int]) -> None:
        """Set managed_channel_ids from list of ints."""
        self.managed_channel_ids = json.dumps(sorted(set(ids))) if ids else None

    def get_normalization_group_ids(self) -> list[int]:
        """Parse normalization_group_ids JSON into list of ints."""
        if not self.normalization_group_ids:
            return []
        try:
            return json.loads(self.normalization_group_ids)
        except (ValueError, TypeError):
            return []

    def set_normalization_group_ids(self, ids: list[int]) -> None:
        """Set normalization_group_ids from list of ints."""
        self.normalization_group_ids = json.dumps(sorted(set(ids))) if ids else None

    def get_event_sync_config(self) -> dict | None:
        """Parse event_sync_config JSON into a dict (None when unset/corrupt)."""
        if not self.event_sync_config:
            return None
        try:
            parsed = json.loads(self.event_sync_config)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def set_event_sync_config(self, config: dict | None) -> None:
        """Set event_sync_config from a dict (None/empty clears it)."""
        self.event_sync_config = json.dumps(config) if config else None

    def is_event_sync(self) -> bool:
        """True when this rule is the event_sync KIND (ti939.1.3).

        Kind is determined by the RAW column being set — not by parse
        success — so a rule whose stored config is corrupt still counts as
        event_sync and stays excluded from Pass 1/2 evaluation and Pass 4
        orphan reconciliation, rather than falling back to running as a
        standard rule against Dispatcharr-owned channels.
        """
        return bool(self.event_sync_config)

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
            "sort_regex": self.sort_regex,
            "stream_sort_field": self.stream_sort_field,
            "stream_sort_order": self.stream_sort_order or "asc",
            "quality_tie_break_order": self.quality_tie_break_order or "desc",
            "quality_m3u_tie_break_enabled": bool(self.quality_m3u_tie_break_enabled),
            "normalization_group_ids": self.get_normalization_group_ids(),
            "skip_struck_streams": self.skip_struck_streams or False,
            "orphan_action": self.orphan_action or "delete",
            "match_scope_target_group": self.match_scope_target_group or False,
            "match_scope_group_id": self.match_scope_group_id,
            "allow_manual_channel_merge": self.allow_manual_channel_merge or False,
            "fold_match_key": self.fold_match_key or False,
            "event_sync_config": self.get_event_sync_config(),
            "last_run_at": self.last_run_at.isoformat() + "Z" if self.last_run_at else None,
            "last_run_stats": self.get_last_run_stats(),
            "match_count": self.match_count or 0,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<ChannelPipelineRule(id={self.id}, name={self.name}, enabled={self.enabled}, priority={self.priority})>"


class ChannelPipelineExecution(Base):
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
    # Distinct channels that received at least one stream merge this run
    # (bd-0emgo.4). Counts target channels, not merge operations — so it reads
    # honestly even when many streams merge into a handful of channels.
    # server_default="0" matches the Alembic 0021 add-column so an existing-row
    # NOT NULL add succeeds and ORM/migration stay drift-free.
    channels_touched = Column(Integer, nullable=False, server_default="0", default=0)
    streams_skipped = Column(Integer, default=0, nullable=False)
    streams_excluded = Column(Integer, default=0, nullable=False)

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

    # Non-fatal run warnings surfaced in the run summary / executions UI
    # (enhancedchannelmanager-e8p1h). JSON array of warning dicts, e.g. a rule
    # that references DISABLED/missing normalization groups (so normalization
    # silently applies nothing). Distinct from error_message — the run still
    # completes; these are advisory config problems the operator should fix.
    warnings = Column(Text, nullable=True)

    # Structured Event Sync per-rule run summaries (enhancedchannelmanager-7wuhd).
    # JSON array of the per-rule summary dicts the event_sync attach phase
    # computes (secondary_streams, attached, already_attached, ambiguous_skipped,
    # unmatched, parse_failed, attach_errors, capped, review_enqueued, ...) —
    # persisted so the executions UI can render an event_sync-aware summary
    # instead of the standard evaluated/matched/created counters, which are
    # structurally 0 for event_sync runs. Nullable; get_event_sync_summary()
    # returns [] for NULL.
    event_sync_summary = Column(Text, nullable=True)

    # True only for a PURE event_sync run — event_sync rule(s) ran and NO
    # standard rules were in scope. Lets the executions UI swap the standard
    # counter block for the event_sync block reliably even after the source
    # rule is deleted (rule_id is ON DELETE SET NULL, so the rule kind can't be
    # re-derived from the rule). A MIXED run (both kinds in one execution) is
    # False so the UI stacks both blocks. server_default="0" so the NOT NULL
    # add succeeds on tables with existing rows; the drift test filters the
    # modify_default noise.
    is_event_sync = Column(
        Boolean, nullable=False, server_default=sa_text("0"), default=False
    )

    # Rollback tracking
    rolled_back_at = Column(DateTime, nullable=True)
    rolled_back_by = Column(String(100), nullable=True)  # username or "system"

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationship to rule
    rule = relationship("ChannelPipelineRule", lazy="joined")

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
            return json.loads(self.created_entities)
        except (ValueError, TypeError):
            return []

    def set_created_entities(self, entities: list) -> None:
        """Set created_entities from list."""
        self.created_entities = json.dumps(entities) if entities else None

    def add_created_entity(self, entity_type: str, entity_id: int, name: str = None, extra: dict = None) -> None:
        """Add a created entity to the tracking list."""
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
            return json.loads(self.modified_entities)
        except (ValueError, TypeError):
            return []

    def set_modified_entities(self, entities: list) -> None:
        """Set modified_entities from list."""
        self.modified_entities = json.dumps(entities) if entities else None

    def add_modified_entity(self, entity_type: str, entity_id: int, previous_state: dict, name: str = None) -> None:
        """Add a modified entity to the tracking list with its previous state for rollback."""
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
            return json.loads(self.dry_run_results)
        except (ValueError, TypeError):
            return []

    def set_dry_run_results(self, results: list) -> None:
        """Set dry_run_results from list."""
        self.dry_run_results = json.dumps(results) if results else None

    def get_execution_log(self) -> list:
        """Parse execution_log JSON into list."""
        if not self.execution_log:
            return []
        try:
            return json.loads(self.execution_log)
        except (ValueError, TypeError):
            return []

    def set_execution_log(self, log: list) -> None:
        """Set execution_log from list."""
        self.execution_log = json.dumps(log) if log else None

    def get_warnings(self) -> list:
        """Parse warnings JSON into list (empty list when none)."""
        if not self.warnings:
            return []
        try:
            return json.loads(self.warnings)
        except (ValueError, TypeError):
            return []

    def set_warnings(self, warnings: list) -> None:
        """Set warnings from list."""
        self.warnings = json.dumps(warnings) if warnings else None

    def get_event_sync_summary(self) -> list:
        """Parse event_sync_summary JSON into list (empty list when none)."""
        if not self.event_sync_summary:
            return []
        try:
            return json.loads(self.event_sync_summary)
        except (ValueError, TypeError):
            return []

    def set_event_sync_summary(self, summaries: list) -> None:
        """Set event_sync_summary from list (JSON), NULL when empty."""
        self.event_sync_summary = json.dumps(summaries) if summaries else None

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
            "channels_touched": self.channels_touched,
            "streams_skipped": self.streams_skipped,
            "streams_excluded": self.streams_excluded,
            "rolled_back_at": self.rolled_back_at.isoformat() + "Z" if self.rolled_back_at else None,
            "rolled_back_by": self.rolled_back_by,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            # Advisory, non-fatal run warnings (e.g. disabled normalization
            # groups). Always present so the UI can render unconditionally.
            "warnings": self.get_warnings(),
            # Event Sync run-kind flag + structured per-rule summaries
            # (enhancedchannelmanager-7wuhd). Always present so the executions
            # UI can branch its layout unconditionally: is_event_sync True ⇒
            # pure event_sync run (swap the standard counter block), and
            # event_sync_summary is [] for standard runs.
            # bool() coerces the pre-flush None (Python default not yet applied)
            # and any legacy NULL row to a real boolean.
            "is_event_sync": bool(self.is_event_sync),
            "event_sync_summary": self.get_event_sync_summary(),
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
        return f"<ChannelPipelineExecution(id={self.id}, rule_id={self.rule_id}, status={self.status}, mode={self.mode})>"


class ChannelPipelineSnapshot(Base):
    """Point-in-time snapshot of the manual (non-Dispatcharr-auto-created)
    channel<->stream state captured BEFORE an auto-creation execution mutated
    anything, to enable a full whole-run revert (ADR-010).

    Stores STREAM IDs ONLY — never stream URLs (ADR-010 §D1: XC URLs embed
    live credentials and the backup ZIP scrubber covers only
    ``alert_methods``, so storing URLs here would be unscrubbed
    credential-at-rest). One row per ``mode="execute"`` execution (1:1 FK,
    UNIQUE on ``execution_id``); dry-run executions get no snapshot.
    """
    __tablename__ = "auto_creation_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 1:1 to the execution whose pre-run state this captures. CASCADE so a
    # pruned/deleted execution row takes its snapshot with it.
    execution_id = Column(
        Integer,
        ForeignKey("auto_creation_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    snapshot_time = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Number of channels captured (denormalized for cheap list/size reporting
    # without parsing the BLOB; feeds the retention metric in ADR-010 §D7).
    channel_count = Column(Integer, default=0, nullable=False)
    # Serialized per-channel payload. JSON TEXT (the project convention for
    # snapshot/entity BLOBs — cf. ChannelPipelineExecution.created_entities and
    # M3USnapshot.groups_data). Shape: {"channels": [{id, name,
    # channel_group_id, epg_data_id, tvg_id, stream_ids: [int]}, ...]}.
    channels_data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Unique 1:1 — one snapshot per execution. Also the FK lookup index
        # that makes the has_snapshot existence check O(1) (ADR-010 §D6).
        UniqueConstraint("execution_id", name="uq_auto_snapshot_execution"),
        # Age-window prune scan (ADR-010 §D7).
        Index("idx_auto_snapshot_time", snapshot_time.desc()),
    )

    def get_channels_data(self) -> dict:
        """Parse channels_data JSON into a dict ({"channels": [...]})."""
        if not self.channels_data:
            return {"channels": []}
        try:
            return json.loads(self.channels_data)
        except (ValueError, TypeError):
            return {"channels": []}

    def set_channels_data(self, data: dict) -> None:
        """Set channels_data from a dict."""
        self.channels_data = json.dumps(data) if data else None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "snapshot_time": self.snapshot_time.isoformat() + "Z" if self.snapshot_time else None,
            "channel_count": self.channel_count,
            "channels": self.get_channels_data().get("channels", []),
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

    def __repr__(self):
        return f"<ChannelPipelineSnapshot(id={self.id}, execution_id={self.execution_id}, channel_count={self.channel_count})>"


class ChannelPipelineConflict(Base):
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
    execution = relationship("ChannelPipelineExecution", lazy="joined")

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
            return json.loads(self.losing_rule_ids)
        except (ValueError, TypeError):
            return []

    def set_losing_rule_ids(self, rule_ids: list) -> None:
        """Set losing_rule_ids from list."""
        self.losing_rule_ids = json.dumps(rule_ids) if rule_ids else None

    def get_details(self) -> dict:
        """Parse details JSON into dict."""
        if not self.details:
            return {}
        try:
            return json.loads(self.details)
        except (ValueError, TypeError):
            return {}

    def set_details(self, details: dict) -> None:
        """Set details from dict."""
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
        return f"<ChannelPipelineConflict(id={self.id}, execution_id={self.execution_id}, type={self.conflict_type})>"


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
        return {
            "id": self.id,
            "name": self.name,
            "config": json.loads(self.config) if self.config else {},
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

    def __repr__(self):
        return f"<FFmpegProfile(id={self.id}, name={self.name})>"


# =============================================================================
# Enhanced Dummy EPG Models (v0.14.0)
# =============================================================================

class DummyEPGProfile(Base):
    """
    Configuration profile for ECM-native EPG generation.

    Each profile defines regex patterns, substitution pairs, and templates
    for generating XMLTV data from channel/stream names. Channel groups are
    assigned via channel_group_ids; at XMLTV generation time, group IDs
    are resolved to channels from Dispatcharr.
    """
    __tablename__ = "dummy_epg_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)

    # Name source configuration
    name_source = Column(String(20), default="channel", nullable=False)  # "channel" or "stream"
    stream_index = Column(Integer, default=1, nullable=False)

    # Pattern configuration (regex with named groups)
    title_pattern = Column(String(500), nullable=True)
    time_pattern = Column(String(500), nullable=True)
    date_pattern = Column(String(500), nullable=True)

    # Substitution pairs: JSON array of {find, replace, is_regex, enabled}
    substitution_pairs = Column(Text, nullable=True)

    # Output templates
    title_template = Column(String(500), nullable=True)
    description_template = Column(Text, nullable=True)

    # Upcoming/Ended templates
    upcoming_title_template = Column(String(500), nullable=True)
    upcoming_description_template = Column(Text, nullable=True)
    ended_title_template = Column(String(500), nullable=True)
    ended_description_template = Column(Text, nullable=True)

    # Fallback templates
    fallback_title_template = Column(String(500), nullable=True)
    fallback_description_template = Column(Text, nullable=True)

    # EPG settings
    event_timezone = Column(String(50), default="US/Eastern", nullable=False)
    output_timezone = Column(String(50), nullable=True)
    program_duration = Column(Integer, default=180, nullable=False)  # minutes
    categories = Column(String(500), nullable=True)  # comma-separated
    channel_logo_url_template = Column(String(500), nullable=True)
    program_poster_url_template = Column(String(500), nullable=True)
    tvg_id_template = Column(String(255), default="ecm-{channel_number}", nullable=False)

    # EPG tags
    include_date_tag = Column(Boolean, default=False, nullable=False)
    include_live_tag = Column(Boolean, default=False, nullable=False)
    include_new_tag = Column(Boolean, default=False, nullable=False)

    # Pattern Builder state (JSON — examples + annotations for visual builder)
    pattern_builder_examples = Column(Text, nullable=True)

    # Multi-variant pattern support (JSON array of variant objects)
    pattern_variants = Column(Text, nullable=True)

    # Channel group assignment (JSON array of group IDs)
    channel_group_ids = Column(Text, nullable=True)

    # Timestamps
    last_generated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_dummy_epg_profile_enabled", enabled),
    )

    def get_substitution_pairs(self) -> list:
        """Parse substitution_pairs JSON into list."""
        if not self.substitution_pairs:
            return []
        try:
            return json.loads(self.substitution_pairs)
        except (ValueError, TypeError):
            return []

    def set_substitution_pairs(self, pairs: list) -> None:
        """Set substitution_pairs from list."""
        self.substitution_pairs = json.dumps(pairs) if pairs else None

    def get_pattern_variants(self) -> list:
        """Parse pattern_variants JSON into list."""
        if not self.pattern_variants:
            return []
        try:
            return json.loads(self.pattern_variants)
        except (ValueError, TypeError):
            return []

    def set_pattern_variants(self, variants: list) -> None:
        """Set pattern_variants from list."""
        self.pattern_variants = json.dumps(variants) if variants else None

    def get_channel_group_ids(self) -> list:
        """Parse channel_group_ids JSON into list."""
        if not self.channel_group_ids:
            return []
        try:
            return json.loads(self.channel_group_ids)
        except (ValueError, TypeError):
            return []

    def set_channel_group_ids(self, ids: list) -> None:
        """Set channel_group_ids from list."""
        self.channel_group_ids = json.dumps(ids) if ids else None

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        result = {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "name_source": self.name_source,
            "stream_index": self.stream_index,
            "title_pattern": self.title_pattern,
            "time_pattern": self.time_pattern,
            "date_pattern": self.date_pattern,
            "substitution_pairs": self.get_substitution_pairs(),
            "title_template": self.title_template,
            "description_template": self.description_template,
            "upcoming_title_template": self.upcoming_title_template,
            "upcoming_description_template": self.upcoming_description_template,
            "ended_title_template": self.ended_title_template,
            "ended_description_template": self.ended_description_template,
            "fallback_title_template": self.fallback_title_template,
            "fallback_description_template": self.fallback_description_template,
            "event_timezone": self.event_timezone,
            "output_timezone": self.output_timezone,
            "program_duration": self.program_duration,
            "categories": self.categories,
            "channel_logo_url_template": self.channel_logo_url_template,
            "program_poster_url_template": self.program_poster_url_template,
            "tvg_id_template": self.tvg_id_template,
            "include_date_tag": self.include_date_tag,
            "include_live_tag": self.include_live_tag,
            "include_new_tag": self.include_new_tag,
            "pattern_builder_examples": self.pattern_builder_examples,
            "pattern_variants": self.get_pattern_variants(),
            "channel_group_ids": self.get_channel_group_ids(),
            "last_generated_at": self.last_generated_at.isoformat() + "Z" if self.last_generated_at else None,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }
        return result

    def __repr__(self):
        return f"<DummyEPGProfile(id={self.id}, name={self.name}, enabled={self.enabled})>"


class DummyEPGChannelAssignment(Base):
    """
    Links a Dispatcharr channel to a DummyEPGProfile.

    Each channel can be assigned to one profile. The tvg_id_override
    allows per-channel customization of the tvg-id used in XMLTV output.
    """
    __tablename__ = "dummy_epg_channel_assignments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    profile_id = Column(Integer, ForeignKey("dummy_epg_profiles.id", ondelete="CASCADE"), nullable=False)
    channel_id = Column(Integer, nullable=False)  # Dispatcharr channel ID
    channel_name = Column(String(255), nullable=False)  # Cached for display
    tvg_id_override = Column(String(255), nullable=True)  # Optional per-channel tvg-id

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("profile_id", "channel_id", name="uq_dummy_epg_profile_channel"),
        Index("idx_dummy_epg_channel_id", channel_id),
        Index("idx_dummy_epg_profile_id", profile_id),
    )

    def to_dict(self) -> dict:
        """Convert to dictionary for API responses."""
        return {
            "id": self.id,
            "profile_id": self.profile_id,
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "tvg_id_override": self.tvg_id_override,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
        }

    def __repr__(self):
        return f"<DummyEPGChannelAssignment(id={self.id}, profile_id={self.profile_id}, channel_id={self.channel_id})>"


class LookupTable(Base):
    """
    Named key→value lookup tables used by the dummy EPG template engine.

    Referenced via the `|lookup:<name>` pipe transform. `entries` is a JSON
    object (str → str); key miss falls back to the input value at render time.
    """
    __tablename__ = "lookup_tables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    entries = Column(Text, nullable=False, default="{}")  # JSON-encoded dict[str, str]
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_lookup_table_name", name),
    )

    def to_dict(self) -> dict:
        import json as _json
        try:
            entries_obj = _json.loads(self.entries) if self.entries else {}
        except (ValueError, TypeError):
            entries_obj = {}
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "entries": entries_obj,
            "entry_count": len(entries_obj),
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<LookupTable(id={self.id}, name={self.name})>"


class RuleLintFinding(Base):
    """
    Persistent lint finding for a stored rule pattern (bd-eio04.7).

    Written by :mod:`regex_lint`'s migration-scan step for pre-lint rows
    that would now fail the write-time lint. Kept separate from the rule
    tables so the hot-path rows aren't widened with optional
    audit-style metadata (DB-engineer grooming decision).

    The table is purely diagnostic: findings do NOT disable or modify the
    underlying rule. UI surfaces the findings via GET endpoints on each
    router so an operator can decide whether to edit or keep the rule.

    Scan semantics:
    - Idempotent. Before each scan the existing findings for that
      ``(rule_type, rule_id)`` pair are cleared; re-running the scan on
      the same corpus produces the same flagged set.
    - Best-effort. If a rule's JSON payload fails to deserialize the scan
      logs and skips that row — a separate concern from the pattern
      being pathological.
    """

    __tablename__ = "rule_lint_findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Logical rule type — one of "normalization", "auto_creation",
    # "dummy_epg". Not a real FK because the three rule tables can't share
    # one; the scan keeps the string coordinate and the rule_id together.
    rule_type = Column(String(30), nullable=False)
    rule_id = Column(Integer, nullable=False)
    # Human-readable path (e.g., "condition_value", "actions[1].pattern").
    field = Column(String(120), nullable=False)
    # REGEX_TOO_LONG / REGEX_COMPILE_ERROR / REGEX_NESTED_QUANTIFIER —
    # kept as a String column rather than an Enum so a newer scan can
    # surface codes this column wasn't built knowing about.
    code = Column(String(40), nullable=False)
    message = Column(Text, nullable=False)
    # JSON-encoded per-code context (pattern_len, compile_error, reason).
    detail = Column(Text, nullable=True)
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_rule_lint_finding_rule", rule_type, rule_id),
        Index("idx_rule_lint_finding_code", code),
    )

    def to_dict(self) -> dict:
        import json as _json

        try:
            detail_obj = _json.loads(self.detail) if self.detail else {}
        except (ValueError, TypeError):
            detail_obj = {}
        return {
            "id": self.id,
            "rule_type": self.rule_type,
            "rule_id": self.rule_id,
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "detail": detail_obj,
            "detected_at": (
                self.detected_at.isoformat() + "Z" if self.detected_at else None
            ),
        }

    def __repr__(self):
        return (
            f"<RuleLintFinding(id={self.id}, rule_type={self.rule_type}, "
            f"rule_id={self.rule_id}, code={self.code})>"
        )


# =============================================================================
# v0.17.1 Interactive Stream Deduplication (ADR-008 §D8 / BD-C / bd-6by2n)
# =============================================================================
#
# Migration 0014 creates the two tables below. Both are out-of-band of the
# Stats v2 / auto-creation pipelines — they back the interactive
# stream-to-channel deduplication queue surfaced via /api/channel-merges/*
# (ADR-008 §D1) and the MCP tools in §D7.
#
# The model declarations exist so the smart-bootstrap fast-path
# (``database._schema_matches_head``) correctly detects that an install at
# alembic_version=0013 with no pending_merges* tables is BEHIND head and
# runs the upgrade. Without these models, the fast-path would falsely
# stamp forward without ever creating the tables (bd-zaaey-class bug).
#
# Column shape and index set MUST match migration 0014 — the schema-parity
# guard (``_assert_schema_matches_models``) verifies every model column
# exists in the live DB on every boot. Type mismatches are tolerated
# (SQLite affinity is fuzzy) but column NAMES are load-bearing.


class PendingMerge(Base):
    """One pending / resolved fuzzy-match candidate from the dedup matcher.

    Source of truth: ``docs/adr/ADR-008-interactive-stream-dedup.md`` §D8 +
    ``backend/alembic/versions/.../0014_pending_merges.py``. Schema review
    history and the BD-C ``trigger_context``-no-CHECK deviation note live
    in the migration docstring; this model carries only the shape, not the
    rationale.

    State machine (§D3): ``status`` transitions
    ``pending → merged`` (operator/auto/MCP accepted the candidate) or
    ``pending → dismissed`` (operator/auto/MCP rejected). Terminal states
    are not garbage-collected in v0.17.1 (§D10 retention is deferred).

    Idempotency invariant (§D5): the partial unique index
    ``uq_pending_merges_active`` prevents duplicate ``pending`` rows for
    the same ``(stream_name, candidate_channel_id)`` pair. The repeating
    bulk-M3U-import path can re-queue the same candidate every refresh
    and the DB filters at insert time — the application does not need to
    pre-check.
    """

    __tablename__ = "pending_merges"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Raw stream name as delivered by the import surface. NOT normalised
    # — operators see what the M3U actually delivered; normalisation is
    # the matcher's compare-time job (ADR-008 §D8).
    stream_name = Column(Text, nullable=False)
    # Dispatcharr group id (nullable; NULL = ungrouped import scope).
    group_id = Column(Integer, nullable=True)
    # Dispatcharr channel UUID. TEXT, no local FK (channels is not an
    # ECM table — ADR-008 §D4).
    candidate_channel_id = Column(Text, nullable=False)
    # RapidFuzz token_set_ratio, 0.0–1.0. Application enforces the
    # §D2 floor; not a DB CHECK because the floor is install-configurable.
    confidence = Column(Float, nullable=False)
    # State machine column. CHECK is load-bearing for §D3 transitions.
    status = Column(
        Text,
        nullable=False,
        server_default=sa_text("'pending'"),
        default="pending",
    )
    # Epoch-ms — matches session_telemetry / ADR-007 convention.
    created_at = Column(Integer, nullable=False)
    # Epoch-ms when the row left 'pending'; NULL while pending.
    resolved_at = Column(Integer, nullable=True)
    # 'operator' / 'auto' / 'bulk_m3u_hook' / 'mcp_tool'; NULL while
    # pending. App-validated enum (no DB CHECK).
    resolution_source = Column(Text, nullable=True)
    # Surface that enqueued the row: 'drag_drop' / 'add_stream' /
    # 'm3u_refresh' / 'mcp_tool'. App-validated enum per the BD-C
    # implementation brief (migration 0014 docstring documents the
    # ADR-vs-brief deviation).
    trigger_context = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','merged','dismissed')",
            name="ck_pending_merges_status",
        ),
        # Dominant queue-list-per-group read path (§D8).
        Index("idx_pending_merges_group_created", "group_id", "created_at"),
        # Sweep pending rows (badge count, retention reaper, §D8).
        Index("idx_pending_merges_status_created", "status", "created_at"),
        # Offline orphan detection (§D4): which queue rows reference
        # candidate channels that no longer exist in Dispatcharr.
        Index("idx_pending_merges_candidate", "candidate_channel_id"),
        # §D5 invariant: at most one ``pending`` row per
        # (stream_name, candidate_channel_id) pair. ``merged`` and
        # ``dismissed`` rows are historical and may repeat freely.
        # SQLite native partial-index syntax via sqlite_where; the index
        # is fully formed on every create_all() / migration replay.
        Index(
            "uq_pending_merges_active",
            "stream_name",
            "candidate_channel_id",
            unique=True,
            sqlite_where=sa_text("status = 'pending'"),
        ),
    )

    def __repr__(self):
        return (
            f"<PendingMerge(id={self.id}, stream={self.stream_name!r}, "
            f"channel={self.candidate_channel_id}, status={self.status}, "
            f"confidence={self.confidence})>"
        )


class PendingMergeJournal(Base):
    """Audit trail row for every accept / dismiss / queue / auto-age action.

    Source of truth: ``docs/adr/ADR-008-interactive-stream-dedup.md`` §D6 +
    ``backend/alembic/versions/.../0014_pending_merges.py``.

    Discrete audit substrate — separate from ``journal_entries`` (PO
    decision 2026-05-16) and separate from ``pending_merges``. Every
    audit field is a queryable column, NOT a JSON blob; the
    MCP-vs-operator distinction the epic asks for is answerable from
    ``actor_token_id`` + ``trigger_context`` directly.

    FK ``pending_merge_id`` is NOT NULL with ``ON DELETE RESTRICT`` (BD-C
    brief): the audit substrate is the system of record, so deleting a
    queue row whose journal still references it is rejected. SQLite
    enforces FKs only when ``PRAGMA foreign_keys=ON``; ECM's connect
    listener handles that everywhere, but raw ``sqlite3`` CLI sessions
    opened for debugging must set the PRAGMA themselves.
    """

    __tablename__ = "pending_merge_journal"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Back-reference to the queue row (ADR-008 §D6).
    pending_merge_id = Column(
        Integer,
        ForeignKey(
            "pending_merges.id",
            name="fk_pending_merge_journal_pending_merge",
            ondelete="RESTRICT",
        ),
        nullable=False,
    )
    # Opaque token DB id, NOT a username string (§D6 audit-actor
    # contract). This is a token row id, not a token secret.
    actor_token_id = Column(Text, nullable=False)
    # What was decided. CHECK is load-bearing for §D6 audit semantics.
    action_type = Column(Text, nullable=False)
    # Identifier for the Dispatcharr stream that triggered the prompt.
    # Audit-first contract (ADR-008 §D6): when the accept-path's
    # stream-name lookup resolves to exactly one Dispatcharr stream,
    # this is the resolved stream id as a string. When the lookup
    # returns zero matches, multiple ambiguous matches, or fails, this
    # falls back to the raw ``stream_name`` from the pending_merges
    # row so the operator decision is still recorded in a queryable
    # form. The lookup-failure case is triageable from the journal via
    # the human-readable name; the resolved-id case is the canonical
    # cross-system reference. Both shapes are valid per the audit-first
    # contract.
    source_channel_id = Column(Text, nullable=False)
    # Dispatcharr channel UUID that was the merge candidate.
    target_channel_id = Column(Text, nullable=False)
    # RapidFuzz score captured at action time, 0.0–1.0. Stored so
    # auditors can answer "what was the confidence when the decision
    # was made?" without reconstructing from a deleted pending row.
    confidence_score = Column(Float, nullable=False)
    # Epoch-ms, UTC — consistent with pending_merges.created_at and
    # ADR-007's epoch-ms convention.
    timestamp_utc = Column(Integer, nullable=False)
    # Surface the decision came in through. App-validated enum (no
    # DB CHECK) per BD-C implementation brief.
    trigger_context = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action_type IN ('merge_confirmed','merge_dismissed',"
            "'auto_queued','auto_aged_out')",
            name="ck_pending_merge_journal_action_type",
        ),
        # FK lookup — every "show all audit rows for this queue row"
        # query keys off this column (§D8 indexes).
        Index("idx_pending_merge_journal_pending", "pending_merge_id"),
        # Time-range queries (audit log reviews, analytics, retention).
        Index("idx_pending_merge_journal_time", "timestamp_utc"),
        # Revocation audits: "find all actions taken by this token."
        Index("idx_pending_merge_journal_actor", "actor_token_id"),
    )

    def __repr__(self):
        return (
            f"<PendingMergeJournal(id={self.id}, "
            f"pending_merge_id={self.pending_merge_id}, "
            f"action={self.action_type}, "
            f"trigger={self.trigger_context})>"
        )


class EventSyncReview(Base):
    """One reviewable event_sync pairing + its outcome (bead ti939.3.2).

    Ambiguous-band matches (including the PR #613 contested rail) from
    event_sync runs enqueue here instead of being silently skipped. One row
    = one (secondary stream identity, master event identity) PAIRING under
    one rule; a stream contested between two masters produces two rows.

    **Keying (HARD security constraint, epic ti939.3):** the identity
    columns are the content fingerprint — ``rule_id``, ``provider_id``,
    ``stream_name_hash``, ``event_key`` — NEVER channel or stream IDs.
    Stream IDs churn on provider refresh; channel IDs live only as long as
    the event's channel. Fingerprint semantics (normalization, sentinel,
    UTC event key) are defined in ``services/event_sync_review.py``; this
    model carries only the shape.

    ``evidence`` is a DISPLAY-ONLY JSON snapshot (raw names, parsed
    identities, score/band/verdict/delta, snapshot stream/channel ids for
    the accept endpoint's lazy re-verification). Nothing in it is ever
    authoritative for identity — the accept path re-verifies snapshot IDs
    against live Dispatcharr before using them and falls back to the
    fingerprint-keyed next-run auto-attach when verification fails.

    State machine: ``pending → accepted | rejected`` via the operator
    endpoints; ``pending → superseded`` when a sibling pairing for the same
    stream fingerprint is accepted (the stream-level question was answered;
    superseded is terminal but distinct from an operator "no"). All
    terminal rows persist as the decision record — the unique fingerprint
    index makes "the queue must not refill with answered questions"
    DB-enforced rather than application-checked.

    Audit trail: accept/reject actions and queue-driven attaches write
    ``journal_entries`` rows under category ``event_sync`` (no second
    journal table — deliberate divergence from the ADR-008 §D6 twin-table
    precedent, which predates the mutation_source-aware shared journal).
    """

    __tablename__ = "event_sync_reviews"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # The event_sync rule the question arose under. FK CASCADE: deleting a
    # rule deletes its open questions AND its decisions — decisions are
    # meaningless without the rule's config (patterns define the parse).
    rule_id = Column(
        Integer,
        ForeignKey(
            "auto_creation_rules.id",
            name="fk_event_sync_reviews_rule",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # M3U account id of the secondary stream. 0 is the documented
    # unknown-provider sentinel (services/event_sync_review.py) — NOT NULL
    # because SQLite unique indexes treat NULLs as distinct, which would
    # break the dedup invariant.
    provider_id = Column(Integer, nullable=False)
    # SHA-256 hex of the LOCALS-normalized secondary stream name.
    stream_name_hash = Column(Text, nullable=False)
    # Normalized master event identity: "<cleaned title>|<UTC ISO start>".
    event_key = Column(Text, nullable=False)
    # State machine column (CHECK is load-bearing).
    status = Column(
        Text,
        nullable=False,
        server_default=sa_text("'pending'"),
        default="pending",
    )
    # Epoch-ms (ADR-007 / pending_merges convention).
    created_at = Column(Integer, nullable=False)
    # Epoch-ms of the last run that re-encountered this pending pairing
    # (evidence snapshot refreshed alongside).
    last_seen_at = Column(Integer, nullable=False)
    # Epoch-ms when the row left 'pending'; NULL while pending.
    resolved_at = Column(Integer, nullable=True)
    # 'operator' / 'superseded_by_accept' (app-validated enum; NULL while
    # pending).
    resolution_source = Column(Text, nullable=True)
    # Opaque acting-user DB id (ADR-008 §D6 posture); "anonymous" when auth
    # is disabled; NULL while pending or when superseded mechanically.
    actor_token_id = Column(Text, nullable=True)
    # Display-only JSON snapshot (see class docstring).
    evidence = Column(Text, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','accepted','rejected','superseded')",
            name="ck_event_sync_reviews_status",
        ),
        # THE dedup invariant: one row per fingerprint, ever. Answered rows
        # persist as decisions; re-encounters refresh, never duplicate.
        Index(
            "uq_event_sync_reviews_fingerprint",
            "rule_id",
            "provider_id",
            "stream_name_hash",
            "event_key",
            unique=True,
        ),
        # Queue list + badge count read path.
        Index("idx_event_sync_reviews_status_created", "status", "created_at"),
        # Per-rule decision load (every run) + per-rule queue filters.
        Index("idx_event_sync_reviews_rule_status", "rule_id", "status"),
    )

    def __repr__(self):
        return (
            f"<EventSyncReview(id={self.id}, rule_id={self.rule_id}, "
            f"provider_id={self.provider_id}, status={self.status})>"
        )


class EventSyncExclusion(Base):
    """One operator "never attach this pairing" exclusion (bead ti939.3.5).

    Solves the stateless-recompute loop the epic predicted: a false-positive
    attach the operator manually detaches is re-attached on the next run,
    forever, until the pattern/threshold changes. An exclusion row is the
    durable "never": the resolver removes the pairing from candidate
    consideration BEFORE the attach band is honored, on every run and
    preview.

    **Keying (HARD security constraint, epic ti939.3 — locked at planning):**
    identity columns are the content fingerprint — ``rule_id``,
    ``provider_id``, ``stream_name_hash``, ``event_key`` — NEVER channel or
    stream IDs (both churn; see ``EventSyncReview``). Fingerprint semantics
    live in ``services/event_sync_review.py``. Survival across refreshes and
    stream-ID churn is therefore by construction.

    **Precedence:** an exclusion outranks a prior review-queue ACCEPT for
    the same fingerprint — the resolver filters excluded candidates before
    the accept-upgrade step, so the two can never both apply
    (``services/event_sync_resolver.py`` pins this).

    Unlike ``EventSyncReview`` there is no state machine: the row's
    existence IS the decision; removal (DELETE) is the undo. ``evidence``
    is the same display-only JSON snapshot shape as review rows — raw
    names for the operator's eyes, never identity-authoritative. Create
    and delete journal under category ``event_sync``
    (``exclusion_create`` / ``exclusion_delete``).
    """

    __tablename__ = "event_sync_exclusions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # FK CASCADE mirrors event_sync_reviews: an exclusion is meaningless
    # without the rule's parse patterns.
    rule_id = Column(
        Integer,
        ForeignKey(
            "auto_creation_rules.id",
            name="fk_event_sync_exclusions_rule",
            ondelete="CASCADE",
        ),
        nullable=False,
    )
    # M3U account id of the secondary stream; 0 = documented
    # unknown-provider sentinel (NOT NULL — SQLite unique indexes treat
    # NULLs as distinct, which would break the dedup invariant).
    provider_id = Column(Integer, nullable=False)
    # SHA-256 hex of the LOCALS-normalized secondary stream name.
    stream_name_hash = Column(Text, nullable=False)
    # Normalized master event identity (see services/event_sync_review.py).
    event_key = Column(Text, nullable=False)
    # Epoch-ms (ADR-007 / pending_merges convention).
    created_at = Column(Integer, nullable=False)
    # Optional operator free-text ("why I excluded this").
    note = Column(Text, nullable=True)
    # Opaque acting-user DB id; "anonymous" when auth is disabled.
    actor_token_id = Column(Text, nullable=True)
    # Display-only JSON snapshot (same shape/role as EventSyncReview.evidence).
    evidence = Column(Text, nullable=False)

    __table_args__ = (
        # One exclusion per fingerprint, ever — create is idempotent.
        Index(
            "uq_event_sync_exclusions_fingerprint",
            "rule_id",
            "provider_id",
            "stream_name_hash",
            "event_key",
            unique=True,
        ),
        # Per-rule load (every run/preview) + per-rule list filter.
        Index("idx_event_sync_exclusions_rule", "rule_id"),
    )

    def __repr__(self):
        return (
            f"<EventSyncExclusion(id={self.id}, rule_id={self.rule_id}, "
            f"provider_id={self.provider_id})>"
        )
