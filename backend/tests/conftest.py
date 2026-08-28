"""
Pytest configuration and shared fixtures for backend tests.
"""
import inspect
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Add backend directory to Python path
backend_dir = Path(__file__).parent.parent
sys.path.insert(0, str(backend_dir))

# Create a new, explicit test posture before importing application modules.
# Never reuse CONFIG_DIR from the host: auth settings are persistent state and
# a stale developer/runner directory can silently turn broad API tests into an
# authenticated deployment (and make the suite order-dependent).
from tests._config_harness import cleanup_test_config, initialize_test_config

_TEST_CONFIG_DIR = initialize_test_config()
# Disable rate limiting in tests
os.environ["RATE_LIMIT_ENABLED"] = "0"

import database
import models  # noqa: F401 — registers all tables with SQLAlchemy Base
import export_models  # noqa: F401 — registers export tables with SQLAlchemy Base
# Reference side-effect imports so static analysis sees them as used
assert models and export_models


def pytest_sessionfinish(session, exitstatus):
    """Clean up only the uniquely marked directory this process created."""
    del session, exitstatus
    cleanup_test_config(_TEST_CONFIG_DIR)


def patch_ssrf_dns(*ips: str):
    """Pin the SSRF chokepoint's resolver so host policy tests are deterministic.

    ``security.ssrf`` resolves a hostname once and rejects the whole URL if ANY
    returned record is denied (threat model §9.4 item 3). Whether ``localhost``
    resolves dual-stack therefore decides the outcome — and that depends on the
    runner's ``/etc/hosts``: a ``network_mode: host`` box answers ``127.0.0.1``
    only, while Docker's generated hosts file answers ``::1`` THEN
    ``127.0.0.1``. GH #754 was only reproducible on the latter, so tests that
    care about host policy pin the record set explicitly instead of inheriting
    whatever the runner happens to be.

    Args:
        *ips: the records the resolver should return, in order.
    """
    import ipaddress
    from unittest.mock import patch

    from security import ssrf

    return patch.object(
        ssrf, "_resolve",
        lambda host, port: [ipaddress.ip_address(i) for i in ips],
    )


def closing_create_task_mock() -> MagicMock:
    """Return a MagicMock drop-in for ``asyncio.create_task`` that does not leak.

    Endpoints that fire-and-forget background work call
    ``asyncio.create_task(some_coro())``. Python evaluates ``some_coro()`` —
    creating a coroutine object — *before* the (patched-out) ``create_task`` ever
    sees it. A bare ``MagicMock`` discards that coroutine without awaiting or
    closing it, so it survives until garbage collection and then emits
    ``RuntimeWarning: coroutine '...' was never awaited``.

    That warning fires at ``gc.collect()`` time, which is non-deterministic and
    can be attributed by pytest's unraisable-exception plugin to whatever test
    happens to be running when GC fires — causing order-dependent, flaky
    failures in *unrelated* later tests (bead enhancedchannelmanager-gqtyf).

    This mock closes the coroutine it receives (``coro.close()``), which is the
    correct way to dispose of a coroutine that will never be awaited. The mock
    still records the call, so ``assert_called_once()`` / ``assert_not_called()``
    assertions on the returned mock keep working unchanged.

    Usage::

        with patch("asyncio.create_task", new=closing_create_task_mock()) as m:
            ...
        m.assert_called_once()
    """
    mock = MagicMock(name="create_task")

    def _close_coro(coro=None, *args, **kwargs):
        if inspect.iscoroutine(coro):
            coro.close()
        return MagicMock(name="task")

    mock.side_effect = _close_coro
    return mock


def auto_advancing_clock(step: float):
    """Return a monotonic clock that advances ``step`` seconds per call.

    ADR-013 §D2 / bead 312nk.3 wired a ``telemetry_write_interval`` throttle and
    a wall-clock-based watch-time accrual into ``BandwidthTracker``, both read
    through the injectable ``now_fn`` clock. The tracker reads the clock exactly
    once per observation (``_process_channel_snapshot``). BandwidthTracker unit
    tests drive that method back-to-back without sleeping, so a real monotonic
    clock would report ~0s between observations — throttling every poll after
    the first and zeroing watch-time accrual.

    This factory makes each successive observation land exactly ``step`` seconds
    (the poll interval) after the previous one, so the throttle is always due
    (matching today's per-poll write cadence) and watch-time accrues exactly the
    poll interval per continuing poll — i.e. the pre-throttle behavior the
    existing assertions encode, with no test-by-test clock bookkeeping.
    """
    state = {"t": 0.0}

    def _clock() -> float:
        state["t"] += step
        return state["t"]

    return _clock


@pytest.fixture
def telemetry_clock():
    """An auto-advancing 10s monotonic clock for BandwidthTracker unit tests.

    Inject as ``BandwidthTracker(..., now_fn=telemetry_clock)`` so back-to-back
    ``_collect_stats`` / ``_process_channel_snapshot`` calls each land one poll
    interval apart — keeping the ADR-013 §D2 throttle due every poll and the
    watch-time accrual at one poll interval per poll, matching the pre-throttle
    behavior the existing assertions encode. See ``auto_advancing_clock``.
    """
    return auto_advancing_clock(10.0)


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
def non_admin_client(async_client, test_session):
    """An authenticated NON-admin client (auth enabled, require_auth + setup).

    Closes the kgz3k test gap: there was no non-admin fixture, so the
    field-level admin gate on POST /api/settings could only be exercised
    against an admin/auth-disabled caller — a vacuous test. This fixture
    forces auth-ENABLED at the settings router's admin-resolver and binds the
    resolver's ``get_current_user`` to a real, non-admin ``User`` so the
    gate's 403 path actually bites.

    ``routers.settings._resolve_settings_admin`` reads ``get_auth_settings``
    and ``get_current_user`` as module-level names in ``routers.settings``, so
    we patch them there (a direct call, not a FastAPI sub-dependency, so
    ``dependency_overrides`` would NOT apply). Yields the ``AsyncClient``.
    """
    from unittest.mock import AsyncMock, patch
    from models import User

    non_admin = User(
        id=4242,
        username="regular-user",
        is_admin=False,
        is_active=True,
        auth_provider="local",
    )

    auth_patch = patch("routers.settings.get_auth_settings")
    user_patch = patch(
        "routers.settings.get_current_user",
        new=AsyncMock(return_value=non_admin),
    )
    auth_mock = auth_patch.start()
    user_patch.start()
    auth_mock.return_value.require_auth = True
    auth_mock.return_value.setup_complete = True
    try:
        yield async_client
    finally:
        auth_patch.stop()
        user_patch.stop()


@pytest.fixture
def admin_client(async_client, test_session):
    """An authenticated ADMIN client (auth enabled), companion to
    :func:`non_admin_client`.

    Proves the field-level admin gate ALLOWS an admin to change admin-only
    fields when auth is enabled — the positive control for the 403 tests.
    """
    from unittest.mock import AsyncMock, patch
    from models import User

    admin = User(
        id=4243,
        username="admin-user",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )

    auth_patch = patch("routers.settings.get_auth_settings")
    user_patch = patch(
        "routers.settings.get_current_user",
        new=AsyncMock(return_value=admin),
    )
    auth_mock = auth_patch.start()
    user_patch.start()
    auth_mock.return_value.require_auth = True
    auth_mock.return_value.setup_complete = True
    try:
        yield async_client
    finally:
        auth_patch.stop()
        user_patch.stop()


@pytest.fixture
def debug_bundle_admin():
    """Authorize debug-bundle endpoint tests with a stable human principal."""
    from auth.dependencies import require_authenticated_human_admin
    from main import app
    from models import User

    admin = User(
        id=5150,
        username="debug-bundle-admin",
        is_admin=True,
        is_active=True,
        auth_provider="local",
    )
    app.dependency_overrides[require_authenticated_human_admin] = lambda: admin
    try:
        yield admin
    finally:
        app.dependency_overrides.pop(require_authenticated_human_admin, None)


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
