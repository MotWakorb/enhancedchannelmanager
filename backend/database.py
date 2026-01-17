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
        from models import JournalEntry, BandwidthDaily, ChannelWatchStats, HiddenChannelGroup, StreamStats, ScheduledTask, TaskExecution  # noqa: F401

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

            logger.debug("All migrations complete - schema is up to date")
    except Exception as e:
        logger.exception(f"Migration failed: {e}")
        raise


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
