"""
Pytest configuration and shared fixtures for backend tests.
"""
import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Add backend directory to Python path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Set test config directory before importing modules
os.environ["CONFIG_DIR"] = "/tmp/ecm_test_config"

# Ensure test config directory exists
Path("/tmp/ecm_test_config").mkdir(parents=True, exist_ok=True)

from database import Base
from models import (
    JournalEntry, BandwidthDaily, ChannelWatchStats, HiddenChannelGroup,
    StreamStats, ScheduledTask, TaskSchedule, TaskExecution, Notification, AlertMethod,
    TagGroup, Tag, NormalizationRuleGroup, NormalizationRule,
    M3USnapshot, M3UChangeLog
)


@pytest.fixture(scope="function")
def test_engine():
    """Create an in-memory SQLite engine for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    # Create all tables
    Base.metadata.create_all(bind=engine)
    yield engine
    # Cleanup
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(scope="function")
def test_session(test_engine):
    """Create a test database session."""
    # expire_on_commit=False allows accessing object attributes after commit/close
    # This is needed because production code commits and closes the session,
    # but tests need to verify attributes on returned objects
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False)
    session = TestSessionLocal()
    yield session
    session.close()


@pytest.fixture(scope="function")
def override_get_session(test_session):
    """
    Fixture that provides a function to override the get_session dependency.
    Use with FastAPI's dependency_overrides.
    """
    def _get_test_session():
        return test_session
    return _get_test_session


@pytest.fixture(scope="function")
async def async_client(test_session):
    """
    Create an async test client for the FastAPI app.
    Patches database.get_session to return the test session.
    """
    from unittest.mock import patch
    from httpx import AsyncClient, ASGITransport
    from main import app

    # Patch get_session at the module level where it's called
    # This intercepts direct calls to get_session() in endpoint code
    with patch('database.get_session', return_value=test_session):
        # Also patch in main.py in case it imports get_session directly
        with patch('main.get_session', return_value=test_session, create=True):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client


@pytest.fixture
def sample_journal_entry(test_session):
    """Create a sample journal entry for testing."""
    from datetime import datetime
    entry = JournalEntry(
        timestamp=datetime.utcnow(),
        category="channel",
        action="create",
        target_type="channel",
        target_id=1,
        target_name="Test Channel",
        details={"channel_number": 100},
    )
    test_session.add(entry)
    test_session.commit()
    test_session.refresh(entry)
    return entry


@pytest.fixture
def sample_notification(test_session):
    """Create a sample notification for testing."""
    from datetime import datetime
    notification = Notification(
        created_at=datetime.utcnow(),
        category="task",
        title="Test Notification",
        message="This is a test notification",
        level="info",
        is_read=False,
    )
    test_session.add(notification)
    test_session.commit()
    test_session.refresh(notification)
    return notification


@pytest.fixture
def sample_alert_method(test_session):
    """Create a sample alert method for testing."""
    from datetime import datetime
    alert_method = AlertMethod(
        created_at=datetime.utcnow(),
        name="Test Discord",
        method_type="discord",
        enabled=True,
        config={"webhook_url": "https://discord.com/api/webhooks/test"},
        alert_sources=["task_success", "task_failure"],
    )
    test_session.add(alert_method)
    test_session.commit()
    test_session.refresh(alert_method)
    return alert_method


# Pytest-asyncio configuration
@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default event loop policy."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
