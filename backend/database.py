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
        from models import JournalEntry, BandwidthDaily, ChannelWatchStats, HiddenChannelGroup, StreamStats, ScheduledTask, TaskSchedule, TaskExecution, Notification, AlertMethod, TagGroup, Tag, NormalizationRuleGroup, NormalizationRule  # noqa: F401

        # Create all tables
        Base.metadata.create_all(bind=_engine)
        logger.debug("Database tables created/verified")

        # Run migrations for existing tables (add new columns if missing)
        _run_migrations(_engine)

        # Create demo normalization rule groups if none exist
        _create_demo_normalization_rules()

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

            # Add tag_group and else_action columns to normalization_rules (v0.8.7)
            _add_tag_group_and_else_columns(conn)

            # Populate built-in tag groups (v0.8.7)
            _populate_builtin_tags(conn)

            # Convert normalization rules from built-in to editable (v0.8.7)
            _convert_normalization_rules_to_editable(conn)

            # Fix tag-group rule action types (v0.8.7)
            _fix_tag_group_action_types(conn)

            # Add enabled column to m3u_change_logs (v0.10.0)
            _add_m3u_change_logs_enabled_column(conn)

            logger.debug("All migrations complete - schema is up to date")
    except Exception as e:
        logger.exception(f"Migration failed: {e}")
        raise


def _create_demo_normalization_rules() -> None:
    """Create demo normalization rule groups if none exist.

    This creates the 5 demo rule groups (Strip Quality Suffixes, Strip Country Prefixes,
    etc.) that use tag-group-based conditions. Rules are disabled by default.
    """
    try:
        db = _SessionLocal()
        try:
            from normalization_migration import create_demo_rules
            result = create_demo_rules(db, force=False)
            if result.get("skipped"):
                logger.debug("Demo normalization rules already exist, skipping creation")
            else:
                groups = result.get("groups_created", 0)
                rules = result.get("rules_created", 0)
                if groups > 0:
                    logger.info(f"Created {groups} demo normalization rule groups with {rules} rules")
        finally:
            db.close()
    except Exception as e:
        logger.warning(f"Could not create demo normalization rules: {e}")
        # Non-fatal - don't block startup


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


def _add_tag_group_and_else_columns(conn) -> None:
    """Add tag_group and else_action columns to normalization_rules table (v0.8.7)."""
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

    # Add tag_group_id column if missing
    if "tag_group_id" not in columns:
        logger.info("Adding tag_group_id column to normalization_rules table")
        conn.execute(text(
            "ALTER TABLE normalization_rules ADD COLUMN tag_group_id INTEGER REFERENCES tag_groups(id) ON DELETE SET NULL"
        ))
        conn.commit()
        logger.info("Migration complete: added tag_group_id column to normalization_rules")

    # Add tag_match_position column if missing
    if "tag_match_position" not in columns:
        logger.info("Adding tag_match_position column to normalization_rules table")
        conn.execute(text(
            "ALTER TABLE normalization_rules ADD COLUMN tag_match_position VARCHAR(20)"
        ))
        conn.commit()
        logger.info("Migration complete: added tag_match_position column to normalization_rules")

    # Add else_action_type column if missing
    if "else_action_type" not in columns:
        logger.info("Adding else_action_type column to normalization_rules table")
        conn.execute(text(
            "ALTER TABLE normalization_rules ADD COLUMN else_action_type VARCHAR(20)"
        ))
        conn.commit()
        logger.info("Migration complete: added else_action_type column to normalization_rules")

    # Add else_action_value column if missing
    if "else_action_value" not in columns:
        logger.info("Adding else_action_value column to normalization_rules table")
        conn.execute(text(
            "ALTER TABLE normalization_rules ADD COLUMN else_action_value VARCHAR(500)"
        ))
        conn.commit()
        logger.info("Migration complete: added else_action_value column to normalization_rules")


def _populate_builtin_tags(conn) -> None:
    """Populate built-in tag groups and tags (v0.8.7).

    Creates the following built-in tag groups:
    - Quality Tags: HD, FHD, UHD, 4K, SD, 1080P, etc.
    - Country Tags: US, UK, CA, AU, BR, etc.
    - Timezone Tags: EST, PST, ET, PT, etc.
    - League Tags: NFL, NBA, MLB, NHL, etc.
    - Network Tags: PPV, LIVE, BACKUP, VIP, etc.

    Tag groups are built-in (immutable group names) but users can add custom tags.
    """
    from sqlalchemy import text

    # Check if tag_groups table exists
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_groups'"
    ))
    if not result.fetchone():
        logger.debug("tag_groups table doesn't exist yet, skipping built-in tags population")
        return

    # Sync built-in tags - adds any missing groups and tags
    # This runs on every startup to ensure new built-in tags are added to existing installations
    logger.debug("Syncing built-in tag groups and tags")

    # Define built-in tag groups and their tags
    builtin_groups = {
        "Quality Tags": {
            "description": "Video quality indicators (HD, 4K, etc.)",
            "tags": ["HD", "FHD", "UHD", "4K", "SD", "1080P", "1080I", "720P", "480P", "HEVC", "H264", "H265"]
        },
        "Country Tags": {
            "description": "Country codes and abbreviations",
            "tags": [
                # North America
                "US", "CA", "MX",
                # Central America & Caribbean
                "CR", "PA", "CU", "DO", "PR", "JM",
                # South America
                "BR", "AR", "CL", "CO", "PE", "VE", "EC", "UY", "PY", "BO",
                # Western Europe
                "UK", "GB", "DE", "FR", "ES", "IT", "NL", "BE", "PT", "AT", "CH", "IE",
                # Northern Europe
                "SE", "NO", "DK", "FI", "IS",
                # Eastern Europe
                "PL", "CZ", "SK", "HU", "RO", "BG", "HR", "SI", "RS", "UA", "RU", "BY",
                # Southern Europe
                "GR", "TR", "CY", "MT",
                # Middle East
                "AE", "SA", "QA", "KW", "BH", "OM", "IL", "JO", "LB", "IQ", "IR", "SY",
                # North Africa
                "EG", "MA", "DZ", "TN", "LY",
                # Sub-Saharan Africa
                "ZA", "NG", "KE", "GH", "ET", "TZ", "UG",
                # South Asia
                "IN", "PK", "BD", "LK", "NP",
                # East Asia
                "CN", "JP", "KR", "TW", "HK", "MO",
                # Southeast Asia
                "SG", "MY", "TH", "VN", "PH", "ID", "MM",
                # Oceania
                "AU", "NZ", "FJ"
            ]
        },
        "Timezone Tags": {
            "description": "Timezone abbreviations",
            "tags": [
                # Universal
                "UTC", "GMT",
                # US/Canada
                "EST", "EDT", "ET", "CST", "CDT", "CT", "MST", "MDT", "MT",
                "PST", "PDT", "PT", "AST", "ADT", "HST", "AKST", "AKDT",
                # Europe
                "CET", "CEST", "EET", "EEST", "WET", "WEST", "BST", "IST",
                # Asia - East
                "JST", "KST", "CST", "HKT", "PHT", "SGT", "MYT", "WIB", "WITA", "WIT",
                # Asia - South
                "IST", "PKT", "BST", "NPT", "BTT",
                # Asia - Central/West
                "ICT", "THA", "MMT",
                # Middle East
                "GST", "AST", "IRST", "TRT", "IDT",
                # Australia/Pacific
                "AEST", "AEDT", "ACST", "ACDT", "AWST", "NZST", "NZDT", "FJT",
                # Americas (non-US)
                "BRT", "BRST", "ART", "CLT", "CLST", "COT", "PET", "VET", "ECT",
                # Africa
                "CAT", "EAT", "WAT", "SAST", "CET"
            ]
        },
        "League Tags": {
            "description": "Sports league abbreviations",
            "tags": [
                # US Major Leagues
                "NFL", "NBA", "MLB", "NHL", "MLS",
                # US College & Other
                "NCAA", "NCAAF", "NCAAB", "WNBA", "NWSL", "CFL", "XFL", "USFL",
                # Soccer/Football - International
                "FIFA", "UEFA", "UCL", "UEL",
                # Soccer/Football - Europe
                "EPL", "LA LIGA", "SERIE A", "BUNDESLIGA", "LIGUE 1",
                "PREMIER LEAGUE", "FA CUP", "EREDIVISIE",
                # Soccer/Football - Americas
                "LIGA MX", "CPL", "CONMEBOL", "CONCACAF",
                # Combat Sports
                "UFC", "WWE", "AEW", "BELLATOR", "ONE", "PFL", "BOXING",
                # Golf
                "PGA", "LPGA", "LIV", "DP WORLD",
                # Tennis
                "ATP", "WTA", "US OPEN", "WIMBLEDON", "ROLAND GARROS",
                # Motorsports
                "F1", "NASCAR", "INDYCAR", "MOTOGP", "WRC", "NHRA",
                # Basketball - International
                "FIBA", "EUROLEAGUE",
                # Hockey - US Minor Leagues
                "AHL", "ECHL", "USHL", "SPHL",
                # Hockey - International
                "IIHF", "KHL",
                # Cricket (CPL already listed under Soccer/Football - Americas)
                "IPL", "BBL", "PSL", "ICC",
                # Rugby
                "SIX NATIONS", "SUPER RUGBY", "NRL", "PREMIERSHIP RUGBY",
                # Australian Sports
                "AFL", "A-LEAGUE",
                # Other
                "OLYMPICS", "X GAMES"
            ]
        },
        "Network Tags": {
            "description": "Network and stream type indicators",
            "tags": ["PPV", "LIVE", "BACKUP", "VIP", "PREMIUM", "24/7", "REPLAY"]
        }
    }

    groups_created = 0
    tags_added = 0

    for group_name, group_data in builtin_groups.items():
        # Check if group exists
        result = conn.execute(text("SELECT id FROM tag_groups WHERE name = :name"), {"name": group_name})
        row = result.fetchone()

        if row:
            group_id = row[0]
        else:
            # Create the group
            conn.execute(text("""
                INSERT INTO tag_groups (name, description, is_builtin, created_at, updated_at)
                VALUES (:name, :description, 1, datetime('now'), datetime('now'))
            """), {"name": group_name, "description": group_data["description"]})
            result = conn.execute(text("SELECT id FROM tag_groups WHERE name = :name"), {"name": group_name})
            group_id = result.fetchone()[0]
            groups_created += 1
            logger.info(f"Created built-in group '{group_name}'")

        # Get existing tags for this group
        result = conn.execute(text("SELECT value FROM tags WHERE group_id = :group_id"), {"group_id": group_id})
        existing_tags = set(row[0] for row in result.fetchall())

        # Deduplicate the tag list (in case of duplicates like CPL)
        unique_tags = list(dict.fromkeys(group_data["tags"]))

        # Insert missing tags
        for tag_value in unique_tags:
            if tag_value not in existing_tags:
                conn.execute(text("""
                    INSERT INTO tags (group_id, value, case_sensitive, enabled, is_builtin)
                    VALUES (:group_id, :value, 0, 1, 1)
                """), {"group_id": group_id, "value": tag_value})
                tags_added += 1

    conn.commit()

    if groups_created > 0 or tags_added > 0:
        logger.info(f"Built-in tags sync complete: {groups_created} groups created, {tags_added} tags added")
    else:
        logger.debug("Built-in tags sync complete: no changes needed")


def _convert_normalization_rules_to_editable(conn) -> None:
    """Convert normalization rule groups and rules from built-in to editable (v0.8.7).

    This migration changes is_builtin from 1 to 0 for all normalization rules,
    making them fully editable and deletable by users. Preserves all other
    settings (enabled status, customizations, etc.).
    """
    from sqlalchemy import text

    # Check if normalization_rule_groups table exists
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='normalization_rule_groups'"
    ))
    if not result.fetchone():
        logger.debug("normalization_rule_groups table doesn't exist yet, skipping conversion")
        return

    # Count how many built-in rules exist
    result = conn.execute(text("SELECT COUNT(*) FROM normalization_rule_groups WHERE is_builtin = 1"))
    builtin_groups = result.fetchone()[0]

    result = conn.execute(text("SELECT COUNT(*) FROM normalization_rules WHERE is_builtin = 1"))
    builtin_rules = result.fetchone()[0]

    if builtin_groups == 0 and builtin_rules == 0:
        logger.debug("No built-in normalization rules to convert")
        return

    # Convert all built-in rule groups to editable
    if builtin_groups > 0:
        conn.execute(text("UPDATE normalization_rule_groups SET is_builtin = 0 WHERE is_builtin = 1"))
        logger.info(f"Converted {builtin_groups} normalization rule groups from built-in to editable")

    # Convert all built-in rules to editable
    if builtin_rules > 0:
        conn.execute(text("UPDATE normalization_rules SET is_builtin = 0 WHERE is_builtin = 1"))
        logger.info(f"Converted {builtin_rules} normalization rules from built-in to editable")

    conn.commit()


def _fix_tag_group_action_types(conn) -> None:
    """Fix action types for tag-group-based normalization rules (v0.8.7).

    Rules using tag_group conditions with prefix/suffix position should use
    strip_prefix/strip_suffix instead of 'remove' to properly handle
    separator characters (: | - /).
    """
    from sqlalchemy import text

    # Check if normalization_rules table exists
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='normalization_rules'"
    ))
    if not result.fetchone():
        logger.debug("normalization_rules table doesn't exist yet, skipping action type fix")
        return

    # Update prefix rules: remove -> strip_prefix
    result = conn.execute(text("""
        UPDATE normalization_rules
        SET action_type = 'strip_prefix'
        WHERE condition_type = 'tag_group'
          AND tag_match_position = 'prefix'
          AND action_type = 'remove'
    """))
    prefix_updated = result.rowcount

    # Update suffix rules: remove -> strip_suffix
    result = conn.execute(text("""
        UPDATE normalization_rules
        SET action_type = 'strip_suffix'
        WHERE condition_type = 'tag_group'
          AND tag_match_position = 'suffix'
          AND action_type = 'remove'
    """))
    suffix_updated = result.rowcount

    total_updated = prefix_updated + suffix_updated
    if total_updated > 0:
        conn.commit()
        logger.info(f"Fixed {total_updated} tag-group rules to use strip_prefix/strip_suffix actions")
    else:
        logger.debug("No tag-group rules needed action type fixes")


def _add_m3u_change_logs_enabled_column(conn) -> None:
    """Add enabled column to m3u_change_logs table (v0.10.0).

    Tracks whether a group is enabled in the M3U account.
    """
    from sqlalchemy import text

    # Check if m3u_change_logs table exists
    result = conn.execute(text(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='m3u_change_logs'"
    ))
    if not result.fetchone():
        logger.debug("m3u_change_logs table doesn't exist yet, skipping enabled column migration")
        return

    # Check if enabled column already exists
    result = conn.execute(text("PRAGMA table_info(m3u_change_logs)"))
    columns = [row[1] for row in result.fetchall()]

    if "enabled" not in columns:
        logger.info("Adding enabled column to m3u_change_logs")
        conn.execute(text(
            "ALTER TABLE m3u_change_logs ADD COLUMN enabled BOOLEAN DEFAULT 0 NOT NULL"
        ))
        conn.commit()
        logger.info("Migration complete: added enabled column to m3u_change_logs")
    else:
        logger.debug("m3u_change_logs.enabled column already exists")


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
