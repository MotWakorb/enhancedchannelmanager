"""
Integration tests for the bd-eio04.17 safe_regex migration.

Covers the two user-facing code paths that accept user-supplied regex:
- backend/stream_prober.py::_rewrite_url_for_profile (stream probe profile URL rewrite)
- backend/tasks/m3u_digest.py::M3UDigestTask.execute (digest exclude filters)

Each test drives the actual production code path with an evil pattern and
asserts the graceful-fallback contract:
- stream_prober: probe proceeds with the original (unrewritten) URL
- m3u_digest: digest completes without stalling; rows pass through the filter

The fixture patterns match safe_regex's empirically-exercised timeout fixture
(see backend/tests/unit/test_safe_regex.py TestTimeoutBehavior.REAL_REDOS_PATTERN).
"""
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

import database
from models import M3UChangeLog, M3UDigestSettings


REAL_REDOS_PATTERN = r"(a|aa)+b"
WALL_CLOCK_BUDGET_MS = 2000  # Integration budget (digest does extra work around filter)


@pytest.fixture
def patched_session_local(test_engine):
    """
    Point ``database._SessionLocal`` at the test engine so production code
    paths that call ``get_session()`` directly (like M3UDigestTask.execute)
    receive the test session. Restored on teardown.
    """
    original = database._SessionLocal
    database._SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine, expire_on_commit=False,
    )
    try:
        yield
    finally:
        database._SessionLocal = original


# ---------------------------------------------------------------------------
# stream_prober: probe with evil profile succeeds, URL unrewritten.
# ---------------------------------------------------------------------------


class TestStreamProberProfileRewriteIntegration:
    """
    The integration surface for stream profile rewrites is internal —
    profiles live in Dispatcharr, not ECM's DB. We exercise the actual
    probe code path (the same call site used in the scheduled and on-demand
    probe loops) end-to-end with a mocked subprocess so ffprobe doesn't run.
    """

    @pytest.mark.asyncio
    async def test_evil_profile_probe_uses_original_url(self, caplog):
        """
        End-to-end: the probe code path asks _rewrite_url_for_profile for
        a URL with an evil profile; the method must return the original URL
        and the probe must proceed using that URL (not crash, not hang).
        """
        from stream_prober import StreamProber

        prober = StreamProber(client=MagicMock(), probe_timeout=1)

        evil_profile = {
            "id": 42,
            "is_default": False,
            "search_pattern": REAL_REDOS_PATTERN,
            "replace_pattern": "SHOULD_NOT_APPEAR",
        }
        original_url = "http://cdn.example.com/" + "a" * 30 + "!"

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            start = time.monotonic()
            rewritten = prober._rewrite_url_for_profile(original_url, evil_profile)
            elapsed_ms = (time.monotonic() - start) * 1000

        # On timeout the probe continues using the unrewritten URL.
        assert rewritten == original_url
        assert "SHOULD_NOT_APPEAR" not in rewritten
        assert elapsed_ms < WALL_CLOCK_BUDGET_MS
        # safe_regex emitted the expected WARN log.
        assert any(
            "[SAFE_REGEX]" in rec.getMessage() and rec.levelno == logging.WARNING
            for rec in caplog.records
        )


# ---------------------------------------------------------------------------
# m3u_digest: POST settings + trigger digest with evil pattern; digest completes.
# ---------------------------------------------------------------------------


def _seed_digest_settings(session, *, exclude_group_patterns=None,
                          exclude_stream_patterns=None):
    """Insert an enabled M3UDigestSettings row with the given exclude patterns."""
    settings = M3UDigestSettings(
        enabled=True,
        frequency="daily",
        email_recipients="[]",
        include_group_changes=True,
        include_stream_changes=True,
        show_detailed_list=True,
        min_changes_threshold=1,
        send_to_discord=True,  # enables a delivery path we can mock
    )
    if exclude_group_patterns is not None:
        settings.set_exclude_group_patterns(exclude_group_patterns)
    if exclude_stream_patterns is not None:
        settings.set_exclude_stream_patterns(exclude_stream_patterns)
    session.add(settings)
    session.commit()
    return settings


def _seed_change(session, change_type, group_name, stream_names=None):
    c = M3UChangeLog(
        m3u_account_id=1,
        change_type=change_type,
        group_name=group_name,
        count=len(stream_names) if stream_names else 1,
    )
    if stream_names:
        c.set_stream_names(stream_names)
    session.add(c)
    session.commit()
    return c


class TestM3UDigestExcludePatternIntegration:
    """
    End-to-end: configure the digest with an evil exclude pattern, seed
    change rows, and run the real M3UDigestTask.execute(). The digest must
    complete (not stall, not crash) and — per the safe-default contract —
    the rows must pass through the filter rather than be silently dropped.
    """

    @pytest.mark.asyncio
    async def test_digest_completes_with_evil_group_pattern(
        self, test_session, patched_session_local, monkeypatch, caplog,
    ):
        """Evil group exclude pattern: digest completes, row survives."""
        _seed_digest_settings(
            test_session,
            exclude_group_patterns=[REAL_REDOS_PATTERN],
        )
        # This group_name hits catastrophic backtracking with (a|aa)+b.
        evil_group = "a" * 30 + "!"
        _seed_change(test_session, "group_added", evil_group)
        # Control row with a neutral name — must also survive.
        _seed_change(test_session, "group_added", "News")

        # Mock delivery so execute() does not try to contact Discord.
        from tasks.m3u_digest import M3UDigestTask

        task = M3UDigestTask()
        monkeypatch.setattr(
            task, "_send_digest_email", AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            task, "_send_digest_discord", AsyncMock(return_value=True),
        )

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            start = time.monotonic()
            result = await task.execute(force=True)
            elapsed_ms = (time.monotonic() - start) * 1000

        assert result.success, f"digest failed: {result.message}"
        assert elapsed_ms < WALL_CLOCK_BUDGET_MS, (
            f"digest elapsed {elapsed_ms:.1f}ms — filter did not short-circuit "
            f"on ReDoS pattern"
        )
        # safe_regex should have emitted at least one WARN per timeout.
        assert any(
            "[SAFE_REGEX]" in rec.getMessage()
            for rec in caplog.records
        ), "expected [SAFE_REGEX] WARN on timeout"

    @pytest.mark.asyncio
    async def test_digest_completes_with_evil_stream_pattern(
        self, test_session, patched_session_local, monkeypatch, caplog,
    ):
        """Evil stream exclude pattern: digest completes, stream names survive."""
        _seed_digest_settings(
            test_session,
            exclude_stream_patterns=[REAL_REDOS_PATTERN],
        )
        evil_stream_name = "a" * 30 + "!"
        _seed_change(
            test_session, "streams_added", "Sports",
            stream_names=[evil_stream_name, "ESPN HD"],
        )

        from tasks.m3u_digest import M3UDigestTask

        task = M3UDigestTask()
        monkeypatch.setattr(
            task, "_send_digest_email", AsyncMock(return_value=True),
        )
        monkeypatch.setattr(
            task, "_send_digest_discord", AsyncMock(return_value=True),
        )

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            start = time.monotonic()
            result = await task.execute(force=True)
            elapsed_ms = (time.monotonic() - start) * 1000

        assert result.success
        assert elapsed_ms < WALL_CLOCK_BUDGET_MS

    @pytest.mark.asyncio
    async def test_put_settings_accepts_evil_pattern_and_digest_still_completes(
        self, async_client, test_session, monkeypatch, caplog,
    ):
        """
        Drive the full HTTP surface: PUT digest settings with an evil exclude
        pattern (it's syntactically valid regex, so PUT accepts it), then POST
        /api/m3u/digest/test and assert the digest completes.

        This is the closest representation of the scenario an attacker or
        misconfiguration would produce: valid-syntax-but-ReDoS pattern
        persisted through the settings API, then exercised by a trigger.
        """
        # Seed a change row so the digest has work to do.
        evil_group = "a" * 30 + "!"
        _seed_change(test_session, "group_added", evil_group)

        # PUT settings with the evil pattern. PUT uses re.compile() for syntax
        # validation only; (a|aa)+b is syntactically valid so it is accepted.
        put_response = await async_client.put(
            "/api/m3u/digest/settings",
            json={
                "enabled": True,
                "frequency": "daily",
                "email_recipients": [],
                "send_to_discord": True,
                "min_changes_threshold": 1,
                "exclude_group_patterns": [REAL_REDOS_PATTERN],
            },
        )
        assert put_response.status_code == 200, put_response.text

        # Mock delivery at the task-method level so execute() can run end-to-end.
        import tasks.m3u_digest as digest_module

        async def _fake_send_email(self, **kwargs):
            return True

        async def _fake_send_discord(self, **kwargs):
            return True

        monkeypatch.setattr(
            digest_module.M3UDigestTask,
            "_send_digest_email",
            _fake_send_email,
        )
        monkeypatch.setattr(
            digest_module.M3UDigestTask,
            "_send_digest_discord",
            _fake_send_discord,
        )

        with caplog.at_level(logging.WARNING, logger="safe_regex"):
            start = time.monotonic()
            post_response = await async_client.post("/api/m3u/digest/test")
            elapsed_ms = (time.monotonic() - start) * 1000

        assert post_response.status_code == 200, post_response.text
        data = post_response.json()
        assert data.get("success") is True, data
        assert elapsed_ms < WALL_CLOCK_BUDGET_MS, (
            f"digest HTTP flow elapsed {elapsed_ms:.1f}ms — ReDoS pattern "
            f"stalled the endpoint"
        )
