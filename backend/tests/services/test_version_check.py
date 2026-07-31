"""Unit tests for the server-side update-availability check.

Bead enhancedchannelmanager-nhkd4. The header "Update available" pill was
replaced by a notification-center entry, which means the update signal moved
from ephemeral client state to a PERSISTED row. The whole risk of that move is
idempotency: the check runs on a 24h loop and again on every container
restart, so a naive "create a notification when an update exists" turns a
quiet pill into a spammer.

The tests below therefore run the check REPEATEDLY (and concurrently) and
assert the row count stays at one — a single-run test would pass against the
non-idempotent implementation too.
"""
import asyncio
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _version_rows(session):
    """All notification rows written by the version check, newest first."""
    from models import Notification
    from services.version_check import NOTIFICATION_SOURCE

    return (
        session.query(Notification)
        .filter(Notification.source == NOTIFICATION_SOURCE)
        .order_by(Notification.id.desc())
        .all()
    )


class _Release:
    """Stand-in for the parsed GitHub payload ``fetch_latest_release`` returns."""

    def __init__(self, version, url="https://example.invalid/release"):
        self.payload = {"version": version, "release_url": url, "release_notes": None}


def _patched(session, current="1.0.0", channel="latest", latest="2.0.0"):
    """Patch the module's DB + network + running-version seams together.

    ``delete_notifications_by_source_internal`` and
    ``create_notification_internal`` live in notification_service and open
    their own sessions, so that module's ``get_session`` is patched too.
    """
    release = _Release(latest).payload if latest is not None else None

    async def _fetch(*_args, **_kwargs):
        return release

    return [
        patch("services.version_check.get_session", return_value=session),
        patch("services.notification_service.get_session", return_value=session),
        patch("services.version_check.current_version", return_value=current),
        patch("services.version_check.current_release_channel", return_value=channel),
        patch("services.version_check.fetch_latest_release", new=_fetch),
    ]


class _Ctx:
    """Enter/exit a list of patchers as one context manager."""

    def __init__(self, patchers):
        self._patchers = patchers

    def __enter__(self):
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False


# ---------------------------------------------------------------------------
# Version comparison — port of the frontend's isNewerVersion()
# ---------------------------------------------------------------------------

class TestIsNewerVersion:
    """Ports the semantics of the removed frontend ``isNewerVersion()``.

    Kept identical so the move to the backend is behaviour-preserving:
    major/minor/patch compared numerically, then the ``-NNNN`` build suffix
    as a tiebreaker.
    """

    @pytest.mark.parametrize(
        "latest,current,expected",
        [
            ("1.0.0", "0.9.9", True),
            ("0.19.0", "0.18.1", True),
            ("0.18.2", "0.18.1", True),
            ("0.18.1", "0.18.1", False),
            ("0.18.0", "0.18.1", False),
            ("0.17.9", "0.18.0", False),
            # Build suffixes.
            ("0.18.1-0004", "0.18.1-0003", True),
            ("0.18.1-0003", "0.18.1-0003", False),
            ("0.18.1-0002", "0.18.1-0003", False),
            # A plain release is NOT newer than the same base with a build.
            ("0.18.1", "0.18.1-0001", False),
            # A build IS newer than the same plain base.
            ("0.18.1-0001", "0.18.1", True),
            # Base version wins over build number.
            ("0.19.0", "0.18.1-9999", True),
            # Junk components degrade to 0 rather than raising.
            ("x.y.z", "0.0.0", False),
            ("1.x.0", "0.9.0", True),
        ],
    )
    def test_comparison(self, latest, current, expected):
        from services.version_check import is_newer_version

        assert is_newer_version(latest, current) is expected


# ---------------------------------------------------------------------------
# Idempotency — the reason this bead exists
# ---------------------------------------------------------------------------

class TestVersionCheckIdempotency:

    @pytest.mark.asyncio
    async def test_repeated_checks_create_exactly_one_notification(self, test_session):
        """Five sequential checks (restart, restart, 24h tick, ...) => one row.

        This is the guard that fails against a naive implementation: a test
        that ran the check ONCE would pass either way.
        """
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="1.0.0", latest="2.0.0")):
            for _ in range(5):
                await run_version_check()

        rows = _version_rows(test_session)
        assert len(rows) == 1, f"expected 1 version_check notification, got {len(rows)}"
        assert rows[0].source_id == "2.0.0"

    @pytest.mark.asyncio
    async def test_concurrent_checks_create_exactly_one_notification(self, test_session):
        """Ten checks racing in one loop => one row.

        The check has a single writer by construction (one background loop,
        skipped in the HTTPS subprocess), but the reconcile is a
        read-then-write, so it is serialised behind a lock. Without that lock
        every racer reads "no row yet" and inserts.
        """
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="1.0.0", latest="2.0.0")):
            await asyncio.gather(*(run_version_check() for _ in range(10)))

        rows = _version_rows(test_session)
        assert len(rows) == 1, f"expected 1 version_check notification, got {len(rows)}"

    @pytest.mark.asyncio
    async def test_newer_version_supersedes_the_previous_notification(self, test_session):
        """A newer release replaces the old row rather than stacking on it.

        Superseding (not accumulating) matches the emby "one progress
        notification at a time" pattern and keeps the centre from filling with
        entries that are each obsolete the moment the next release lands.
        """
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="1.0.0", latest="2.0.0")):
            await run_version_check()
        with _Ctx(_patched(test_session, current="1.0.0", latest="2.1.0")):
            await run_version_check()
            await run_version_check()

        rows = _version_rows(test_session)
        assert len(rows) == 1
        assert rows[0].source_id == "2.1.0"
        assert "2.1.0" in rows[0].message

    @pytest.mark.asyncio
    async def test_check_retires_the_notification_once_the_update_is_applied(self, test_session):
        """After the operator updates, the stale row is cleared by the next check.

        This self-healing is only possible because the check runs server-side
        against the running version — the pill got this for free by being
        ephemeral, and a persisted row has to earn it.
        """
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="1.0.0", latest="2.0.0")):
            await run_version_check()
        assert len(_version_rows(test_session)) == 1

        with _Ctx(_patched(test_session, current="2.0.0", latest="2.0.0")):
            await run_version_check()

        assert _version_rows(test_session) == []

    @pytest.mark.asyncio
    async def test_no_notification_when_already_current(self, test_session):
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="2.0.0", latest="2.0.0")):
            await run_version_check()

        assert _version_rows(test_session) == []

    @pytest.mark.asyncio
    async def test_unknown_running_version_never_creates_a_notification(self, test_session):
        """``ECM_VERSION`` unset must not produce a bogus "update available"."""
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="unknown", latest="2.0.0")):
            await run_version_check()

        assert _version_rows(test_session) == []

    @pytest.mark.asyncio
    async def test_fetch_failure_leaves_an_existing_notification_alone(self, test_session):
        """A GitHub outage must not retire a still-valid update notice."""
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="1.0.0", latest="2.0.0")):
            await run_version_check()
        assert len(_version_rows(test_session)) == 1

        with _Ctx(_patched(test_session, current="1.0.0", latest=None)):
            await run_version_check()

        assert len(_version_rows(test_session)) == 1


class TestVersionCheckNotificationShape:

    @pytest.mark.asyncio
    async def test_notification_fields(self, test_session):
        """Fields map onto the existing model — no schema change (bead nhkd4).

        ``info``, not ``warning``: the pill was a neutral affordance, and a
        "far behind" severity threshold is policy the PO has not asked for.
        """
        from services.version_check import NOTIFICATION_SOURCE, run_version_check

        with _Ctx(_patched(test_session, current="1.0.0", latest="2.0.0")):
            await run_version_check()

        row = _version_rows(test_session)[0]
        assert row.type == "info"
        assert row.title == "Update available"
        assert "2.0.0" in row.message
        assert row.source == NOTIFICATION_SOURCE
        assert row.source_id == "2.0.0"
        assert row.action_label == "View release"
        assert row.action_url == "https://example.invalid/release"
        assert row.read is False

    @pytest.mark.asyncio
    async def test_does_not_dispatch_external_alerts(self, test_session):
        """No Discord/Telegram/email blast — the PO asked to move the pill into
        the notification centre, not to add an outbound alert channel."""
        from services.version_check import run_version_check

        with _Ctx(_patched(test_session, current="1.0.0", latest="2.0.0")):
            with patch(
                "services.notification_service._dispatch_to_alert_channels"
            ) as dispatch:
                await run_version_check()

        dispatch.assert_not_called()
