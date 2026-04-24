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
# Disable rate limiting in tests
os.environ["RATE_LIMIT_ENABLED"] = "0"

# Ensure test config directory exists
Path("/tmp/ecm_test_config").mkdir(parents=True, exist_ok=True)

import database
import models  # noqa: F401 — registers all tables with SQLAlchemy Base
import export_models  # noqa: F401 — registers export tables with SQLAlchemy Base
from ffmpeg_builder import persistence  # noqa: F401 — registers table
# Register SecurityError globally for test specs that reference it without import
from ffmpeg_builder.execution import SecurityError as _SecurityError
import builtins as _builtins
_builtins.SecurityError = _SecurityError
# Reference side-effect imports so static analysis sees them as used
assert models and persistence and export_models


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
    database.Base.metadata.create_all(bind=engine)
    yield engine
    # Cleanup
    database.Base.metadata.drop_all(bind=engine)
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
async def async_client(test_session, test_engine):
    """
    Create an async test client for the FastAPI app.
    Uses FastAPI's dependency_overrides to inject the test session.
    Also patches database module internals for endpoints that call get_session() directly.
    """
    from httpx import AsyncClient, ASGITransport
    from main import app

    # Override the get_session dependency with a function that yields test_session
    def override_get_session():
        try:
            yield test_session
        finally:
            pass  # Don't close - the test_session fixture handles cleanup

    # Use FastAPI's dependency_overrides
    app.dependency_overrides[database.get_session] = override_get_session

    # Also patch database module internals for endpoints that call get_session() directly
    # (rather than using Depends(get_session))
    original_session_local = database._SessionLocal
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False)
    database._SessionLocal = TestSessionLocal

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        # Clear overrides after test
        app.dependency_overrides.clear()
        # Restore original session local
        database._SessionLocal = original_session_local


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


@pytest.fixture(scope="function")
def ci_seed_db(tmp_path):
    """
    Opt-in CI fixture that materializes a tmp SQLite database seeded from
    `tests/fixtures/ci_seed.sql`.

    Yields a tuple of `(engine, session)`. The underlying database file lives
    at `tmp_path / "ecm-test.db"` so the filesystem interaction is exercised
    (matching the CI layout where pytest runs against a tmp SQLite rather
    than an in-memory DB). The in-memory `test_engine`/`test_session`
    fixtures remain the default for speed.

    Usage:
        def test_read_seeded_row(ci_seed_db):
            engine, session = ci_seed_db
            ...
    """
    db_path = tmp_path / "ecm-test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)

    seed_path = Path(__file__).parent / "fixtures" / "ci_seed.sql"
    seed_sql = seed_path.read_text()
    with engine.begin() as conn:
        for statement in seed_sql.split(";"):
            stripped = statement.strip()
            if not stripped or stripped.upper() in {"BEGIN TRANSACTION", "COMMIT"}:
                continue
            conn.exec_driver_sql(stripped)

    TestSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )
    session = TestSessionLocal()
    try:
        yield engine, session
    finally:
        session.close()
        database.Base.metadata.drop_all(bind=engine)
        engine.dispose()


# Pytest-asyncio configuration
@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default event loop policy."""
    import asyncio
    return asyncio.DefaultEventLoopPolicy()
