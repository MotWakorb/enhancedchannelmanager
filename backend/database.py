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

    # Ensure config directory exists
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    database_url = get_database_url()
    logger.info(f"Initializing journal database at {JOURNAL_DB_FILE}")

    # Create engine with SQLite-specific settings
    _engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,  # Set to True for SQL debugging
    )

    # Create session factory
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

    # Import models to register them with Base
    from models import JournalEntry  # noqa: F401

    # Create all tables
    Base.metadata.create_all(bind=_engine)
    logger.info("Journal database initialized successfully")


def get_session():
    """Get a database session. Use as context manager or close manually."""
    if _SessionLocal is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _SessionLocal()


def get_engine():
    """Get the database engine."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _engine
