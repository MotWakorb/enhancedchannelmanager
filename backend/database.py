"""
SQLite database setup for the Journal feature.
Uses SQLAlchemy with async support via aiosqlite.
"""
import logging
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.pool import StaticPool

from config import CONFIG_DIR

logger = logging.getLogger(__name__)

# Database file location
JOURNAL_DB_FILE = CONFIG_DIR / "journal.db"

# SQLAlchemy Base for model declarations
Base = declarative_base()

# Engine and session factory (initialized on startup)
_engine = None
_SessionLocal = None


def get_database_url() -> str:
    """Get the SQLite database URL."""
    return f"sqlite:///{JOURNAL_DB_FILE}"


def init_db() -> None:
    """Initialize the database, creating tables if they don't exist."""
    global _engine, _SessionLocal

    try:
        # Ensure config directory exists
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Config directory ensured: {CONFIG_DIR}")

        database_url = get_database_url()
        logger.info(f"Initializing journal database at {JOURNAL_DB_FILE}")

        # Create engine with SQLite-specific settings
        _engine = create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,  # Set to True for SQL debugging
        )
        logger.debug("Database engine created")

        # Create session factory
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

        # Import models to register them with Base
        from models import JournalEntry, BandwidthDaily, ChannelWatchStats, HiddenChannelGroup, StreamStats, ScheduledTask, TaskSchedule, TaskExecution, Notification, AlertMethod  # noqa: F401

        # Create all tables
        Base.metadata.create_all(bind=_engine)
        logger.debug("Database tables created/verified")

        # Run migrations for existing tables (add new columns if missing)
        _run_migrations(_engine)

        # Perform maintenance: purge old entries and vacuum
        _perform_maintenance(_engine)

        logger.info("Journal database initialized successfully")
    except Exception as e:
        logger.exception(f"Failed to initialize database: {e}")
        raise


def _run_migrations(engine) -> None:
    """Run database migrations to add new columns to existing tables."""
    from sqlalchemy import text

    logger.debug("Checking for database migrations")
    try:
        with engine.connect() as conn:
            # Check if total_watch_seconds column exists in channel_watch_stats
            result = conn.execute(text("PRAGMA table_info(channel_watch_stats)"))
            columns = [row[1] for row in result.fetchall()]

            if "total_watch_seconds" not in columns:
                logger.info("Adding total_watch_seconds column to channel_watch_stats")
                conn.execute(text(
                    "ALTER TABLE channel_watch_stats ADD COLUMN total_watch_seconds INTEGER DEFAULT 0 NOT NULL"
                ))
                conn.commit()
                logger.info("Migration complete: added total_watch_seconds column")

            # Check if video_bitrate column exists in stream_stats
            result = conn.execute(text("PRAGMA table_info(stream_stats)"))
            columns = [row[1] for row in result.fetchall()]

            if "video_bitrate" not in columns:
                logger.info("Adding video_bitrate column to stream_stats")
                conn.execute(text(
                    "ALTER TABLE stream_stats ADD COLUMN video_bitrate BIGINT"
                ))
                conn.commit()
                logger.info("Migration complete: added video_bitrate column")

            # Check if dismissed_at column exists in stream_stats (v0.8.4-0059)
            if "dismissed_at" not in columns:
                logger.info("Adding dismissed_at column to stream_stats")
                conn.execute(text(
                    "ALTER TABLE stream_stats ADD COLUMN dismissed_at DATETIME"
                ))
                conn.commit()
                logger.info("Migration complete: added dismissed_at column")

            # Migrate existing schedules from scheduled_tasks to task_schedules
            _migrate_task_schedules(conn)

            # Ensure alert_methods table exists (for databases created before v0.8.2)
            _ensure_alert_methods_table(conn)

            # Add alert_sources column to alert_methods (v0.8.2-0026)
            _add_alert_sources_column(conn)

            # Remove min_interval_seconds column from alert_methods (v0.8.2-0028)
            _remove_min_interval_seconds_column(conn)

            # Add parameters column to task_schedules (v0.8.7)
            _add_task_schedule_parameters_column(conn)

            # Add compound conditions columns to normalization_rules (v0.8.7)
            _add_compound_conditions_columns(conn)

            logger.debug("All migrations complete - schema is up to date")
    except Exception as e:
        logger.exception(f"Migration failed: {e}")
        raise


def _migrate_task_schedules(conn) -> None:
    """Migrate existing schedules from ScheduledTask to TaskSchedule table."""
    from sqlalchemy import text

    # Check if task_schedules table exists and has any data
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='task_schedules'"
    ))
    if not result.fetchone():
        logger.debug("task_schedules table doesn't exist yet, skipping migration")
        return

    # Check if we've already migrated (table has data)
    result = conn.execute(text("SELECT COUNT(*) FROM task_schedules"))
    count = result.fetchone()[0]
    if count > 0:
        logger.debug(f"task_schedules already has {count} entries, skipping migration")
        return

    # Get all scheduled_tasks with non-manual schedules that need migration
    result = conn.execute(text("""
        SELECT task_id, schedule_type, interval_seconds, cron_expression,
               schedule_time, timezone
        FROM scheduled_tasks
        WHERE schedule_type != 'manual'
    """))
    tasks_to_migrate = result.fetchall()

    if not tasks_to_migrate:
        logger.debug("No scheduled tasks need migration to task_schedules")
        return

    logger.info(f"Migrating {len(tasks_to_migrate)} task schedules to new format")

    for task in tasks_to_migrate:
        task_id = task[0]
        schedule_type = task[1]
        interval_seconds = task[2]
        cron_expression = task[3]
        schedule_time = task[4]
        timezone = task[5]

        # Convert old schedule types to new format
        if schedule_type == "interval":
            # Keep as interval
            conn.execute(text("""
                INSERT INTO task_schedules
                (task_id, name, enabled, schedule_type, interval_seconds, timezone, created_at, updated_at)
                VALUES (:task_id, 'Migrated Schedule', 1, 'interval', :interval_seconds, :timezone,
                        datetime('now'), datetime('now'))
            """), {
                "task_id": task_id,
                "interval_seconds": interval_seconds,
                "timezone": timezone or "UTC"
            })
            logger.info(f"Migrated {task_id} interval schedule: every {interval_seconds}s")

        elif schedule_type == "cron":
            # Convert cron to appropriate type based on expression
            new_schedule = _convert_cron_to_schedule(cron_expression, timezone)
            if new_schedule:
                conn.execute(text("""
                    INSERT INTO task_schedules
                    (task_id, name, enabled, schedule_type, interval_seconds, schedule_time,
                     timezone, days_of_week, day_of_month, created_at, updated_at)
                    VALUES (:task_id, 'Migrated Schedule', 1, :schedule_type, :interval_seconds,
                            :schedule_time, :timezone, :days_of_week, :day_of_month,
                            datetime('now'), datetime('now'))
                """), {
                    "task_id": task_id,
                    **new_schedule
                })
                logger.info(f"Migrated {task_id} cron schedule to {new_schedule['schedule_type']}")
            else:
                # Fallback to daily if cron can't be converted
                time_str = schedule_time or "03:00"
                conn.execute(text("""
                    INSERT INTO task_schedules
                    (task_id, name, enabled, schedule_type, schedule_time, timezone,
                     created_at, updated_at)
                    VALUES (:task_id, 'Migrated Schedule', 1, 'daily', :schedule_time, :timezone,
                            datetime('now'), datetime('now'))
                """), {
                    "task_id": task_id,
                    "schedule_time": time_str,
                    "timezone": timezone or "UTC"
                })
                logger.info(f"Migrated {task_id} cron to daily at {time_str} (cron conversion fallback)")

    conn.commit()
    logger.info("Task schedule migration complete")


def _convert_cron_to_schedule(cron_expr: str, timezone: str) -> dict:
    """
    Convert a cron expression to the new schedule format.
    Returns a dict with schedule parameters or None if can't convert.
    """
    if not cron_expr:
        return None

    parts = cron_expr.strip().split()
    if len(parts) != 5:
        return None

    minute, hour, day_of_month, month, day_of_week = parts

    # Check for interval patterns (e.g., */30 * * * * = every 30 minutes)
    if minute.startswith("*/") and hour == "*" and day_of_month == "*" and month == "*" and day_of_week == "*":
        try:
            minutes = int(minute[2:])
            return {
                "schedule_type": "interval",
                "interval_seconds": minutes * 60,
                "schedule_time": None,
                "timezone": timezone or "UTC",
                "days_of_week": None,
                "day_of_month": None,
            }
        except ValueError:
            pass

    if hour.startswith("*/") and day_of_month == "*" and month == "*" and day_of_week == "*":
        try:
            hours = int(hour[2:])
            return {
                "schedule_type": "interval",
                "interval_seconds": hours * 3600,
                "schedule_time": None,
                "timezone": timezone or "UTC",
                "days_of_week": None,
                "day_of_month": None,
            }
        except ValueError:
            pass

    # Check for daily pattern (e.g., 0 3 * * * = daily at 3:00 AM)
    if day_of_month == "*" and month == "*" and day_of_week == "*":
        try:
            h = int(hour) if hour != "*" else 0
            m = int(minute) if minute != "*" else 0
            return {
                "schedule_type": "daily",
                "interval_seconds": None,
                "schedule_time": f"{h:02d}:{m:02d}",
                "timezone": timezone or "UTC",
                "days_of_week": None,
                "day_of_month": None,
            }
        except ValueError:
            pass

    # Check for weekly pattern (e.g., 0 3 * * 0,3,6 = specific days of week)
    if day_of_month == "*" and month == "*" and day_of_week != "*":
        try:
            h = int(hour) if hour != "*" else 0
            m = int(minute) if minute != "*" else 0
            # Parse day_of_week (can be comma-separated or ranges)
            days = []
            for part in day_of_week.split(","):
                if "-" in part:
                    start, end = part.split("-")
                    days.extend(range(int(start), int(end) + 1))
                else:
                    days.append(int(part))
            return {
                "schedule_type": "weekly",
                "interval_seconds": None,
                "schedule_time": f"{h:02d}:{m:02d}",
                "timezone": timezone or "UTC",
                "days_of_week": ",".join(str(d) for d in sorted(set(days))),
                "day_of_month": None,
            }
        except ValueError:
            pass

    # Check for monthly pattern (e.g., 0 3 15 * * = 15th of each month)
    if day_of_month != "*" and month == "*" and day_of_week == "*":
        try:
            h = int(hour) if hour != "*" else 0
            m = int(minute) if minute != "*" else 0
            dom = int(day_of_month)
            return {
                "schedule_type": "monthly",
                "interval_seconds": None,
                "schedule_time": f"{h:02d}:{m:02d}",
                "timezone": timezone or "UTC",
                "days_of_week": None,
                "day_of_month": dom,
            }
        except ValueError:
            pass

    # Can't convert this cron expression
    return None


def _ensure_alert_methods_table(conn) -> None:
    """Ensure alert_methods table exists for databases created before v0.8.2."""
    from sqlalchemy import text

    # Check if alert_methods table exists
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_methods'"
    ))
    if result.fetchone():
        logger.debug("alert_methods table already exists")
        return

    logger.info("Creating alert_methods table (database predates v0.8.2)")
    conn.execute(text("""
        CREATE TABLE alert_methods (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL,
            method_type VARCHAR(50) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            config TEXT NOT NULL,
            notify_info BOOLEAN NOT NULL DEFAULT 0,
            notify_success BOOLEAN NOT NULL DEFAULT 1,
            notify_warning BOOLEAN NOT NULL DEFAULT 1,
            notify_error BOOLEAN NOT NULL DEFAULT 1,
            last_sent_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))
    conn.execute(text("CREATE INDEX idx_alert_method_type ON alert_methods (method_type)"))
    conn.execute(text("CREATE INDEX idx_alert_method_enabled ON alert_methods (enabled)"))
    conn.commit()
    logger.info("Created alert_methods table successfully")


def _add_alert_sources_column(conn) -> None:
    """Add alert_sources column to alert_methods table (v0.8.2-0026)."""
    from sqlalchemy import text

    # Check if alert_sources column already exists
    result = conn.execute(text("PRAGMA table_info(alert_methods)"))
    columns = [row[1] for row in result.fetchall()]

    if "alert_sources" in columns:
        logger.debug("alert_sources column already exists in alert_methods")
        return

    logger.info("Adding alert_sources column to alert_methods table")
    conn.execute(text(
        "ALTER TABLE alert_methods ADD COLUMN alert_sources TEXT"
    ))
    conn.commit()
    logger.info("Migration complete: added alert_sources column to alert_methods")


def _remove_min_interval_seconds_column(conn) -> None:
    """Remove min_interval_seconds column from alert_methods table (v0.8.2-0028).

    This column was removed in v0.8.2-0025 but existing databases still have it
    with a NOT NULL constraint, causing inserts to fail.
    SQLite requires table recreation to drop columns in older versions.
    """
    from sqlalchemy import text

    # Check if min_interval_seconds column exists
    result = conn.execute(text("PRAGMA table_info(alert_methods)"))
    columns = {row[1]: row for row in result.fetchall()}

    if "min_interval_seconds" not in columns:
        logger.debug("min_interval_seconds column already removed from alert_methods")
        return

    logger.info("Removing min_interval_seconds column from alert_methods table")

    # SQLite table recreation to drop the column
    # 1. Create new table without the column
    conn.execute(text("""
        CREATE TABLE alert_methods_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(100) NOT NULL,
            method_type VARCHAR(50) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT 1,
            config TEXT NOT NULL,
            notify_info BOOLEAN NOT NULL DEFAULT 0,
            notify_success BOOLEAN NOT NULL DEFAULT 1,
            notify_warning BOOLEAN NOT NULL DEFAULT 1,
            notify_error BOOLEAN NOT NULL DEFAULT 1,
            alert_sources TEXT,
            last_sent_at DATETIME,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """))

    # 2. Copy data from old table (excluding min_interval_seconds)
    # Build column list dynamically based on what exists in both tables
    new_columns = ["id", "name", "method_type", "enabled", "config",
                   "notify_info", "notify_success", "notify_warning", "notify_error",
                   "last_sent_at", "created_at", "updated_at"]

    # Add alert_sources if it exists in old table
    if "alert_sources" in columns:
        new_columns.append("alert_sources")

    cols_str = ", ".join(new_columns)
    conn.execute(text(f"INSERT INTO alert_methods_new ({cols_str}) SELECT {cols_str} FROM alert_methods"))

    # 3. Drop old table
    conn.execute(text("DROP TABLE alert_methods"))

    # 4. Rename new table
    conn.execute(text("ALTER TABLE alert_methods_new RENAME TO alert_methods"))

    # 5. Recreate indexes
    conn.execute(text("CREATE INDEX idx_alert_method_type ON alert_methods (method_type)"))
    conn.execute(text("CREATE INDEX idx_alert_method_enabled ON alert_methods (enabled)"))

    conn.commit()
    logger.info("Migration complete: removed min_interval_seconds column from alert_methods")


def _add_task_schedule_parameters_column(conn) -> None:
    """Add missing columns to task_schedules table (v0.8.7)."""
    from sqlalchemy import text

    # Check if task_schedules table exists
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='task_schedules'"
    ))
    if not result.fetchone():
        logger.debug("task_schedules table doesn't exist yet, skipping migration")
        return

    # Check which columns already exist
    result = conn.execute(text("PRAGMA table_info(task_schedules)"))
    columns = [row[1] for row in result.fetchall()]

    # Add parameters column if missing
    if "parameters" not in columns:
        logger.info("Adding parameters column to task_schedules table")
        conn.execute(text(
            "ALTER TABLE task_schedules ADD COLUMN parameters TEXT"
        ))
        conn.commit()
        logger.info("Migration complete: added parameters column to task_schedules")

    # Add last_run_at column if missing
    if "last_run_at" not in columns:
        logger.info("Adding last_run_at column to task_schedules table")
        conn.execute(text(
            "ALTER TABLE task_schedules ADD COLUMN last_run_at DATETIME"
        ))
        conn.commit()
        logger.info("Migration complete: added last_run_at column to task_schedules")


def _add_compound_conditions_columns(conn) -> None:
    """Add compound conditions columns to normalization_rules table (v0.8.7)."""
    from sqlalchemy import text

    # Check if normalization_rules table exists
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='normalization_rules'"
    ))
    if not result.fetchone():
        logger.debug("normalization_rules table doesn't exist yet, skipping migration")
        return

    # Get current columns
    result = conn.execute(text("PRAGMA table_info(normalization_rules)"))
    columns = [row[1] for row in result.fetchall()]

    # Add conditions column if missing (JSON array of condition objects)
    if "conditions" not in columns:
        logger.info("Adding conditions column to normalization_rules table")
        conn.execute(text(
            "ALTER TABLE normalization_rules ADD COLUMN conditions TEXT"
        ))
        conn.commit()
        logger.info("Migration complete: added conditions column to normalization_rules")

    # Add condition_logic column if missing ("AND" or "OR")
    if "condition_logic" not in columns:
        logger.info("Adding condition_logic column to normalization_rules table")
        conn.execute(text(
            "ALTER TABLE normalization_rules ADD COLUMN condition_logic VARCHAR(3) DEFAULT 'AND' NOT NULL"
        ))
        conn.commit()
        logger.info("Migration complete: added condition_logic column to normalization_rules")


def _perform_maintenance(engine) -> None:
    """Perform database maintenance on startup: purge old entries and vacuum."""
    from sqlalchemy import text
    from datetime import datetime, timedelta

    PURGE_DAYS = 30  # Keep 30 days of journal entries

    with engine.connect() as conn:
        try:
            # Purge old journal entries
            cutoff_date = datetime.utcnow() - timedelta(days=PURGE_DAYS)
            result = conn.execute(
                text("DELETE FROM journal_entries WHERE timestamp < :cutoff"),
                {"cutoff": cutoff_date}
            )
            deleted_count = result.rowcount
            if deleted_count > 0:
                logger.info(f"Purged {deleted_count} journal entries older than {PURGE_DAYS} days")

            # Purge old bandwidth records (keep 1 year)
            bandwidth_cutoff = datetime.utcnow() - timedelta(days=365)
            result = conn.execute(
                text("DELETE FROM bandwidth_daily WHERE date < :cutoff"),
                {"cutoff": bandwidth_cutoff.date()}
            )
            if result.rowcount > 0:
                logger.info(f"Purged {result.rowcount} bandwidth records older than 1 year")

            conn.commit()

            # Run VACUUM to reclaim disk space (must be outside transaction)
            conn.execute(text("VACUUM"))
            logger.info("Database vacuum completed")

        except Exception as e:
            logger.error(f"Database maintenance failed: {e}")


def get_session():
    """Get a database session. Use as context manager or close manually."""
    if _SessionLocal is None:
        logger.error("Attempted to get database session before initialization")
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal()


def get_engine():
    """Get the database engine."""
    if _engine is None:
        logger.error("Attempted to get database engine before initialization")
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine
