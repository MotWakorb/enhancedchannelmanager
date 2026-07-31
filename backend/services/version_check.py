"""Update-availability check (bead enhancedchannelmanager-nhkd4).

The header used to carry an "Update available" pill fed by a check that ran in
the BROWSER (``services/api.ts`` → ``checkForUpdates``). PO decision
2026-07-30: the pill goes away and the same information arrives as a
notification-centre entry instead.

That move changes the problem. A pill is ephemeral client state — recomputing
it on every page load costs nothing. A notification is a persisted row, so the
check has to be reconciling, not appending.

Why the check moved to the backend rather than staying client-side and POSTing
-----------------------------------------------------------------------------
Every notification in this app is written by the backend; nothing in the
frontend has ever POSTed one. Beyond following that grain, a server-side check
is the only way to get a single writer: with the check in the browser, every
open tab races to create the same row, and the notifications table cannot
enforce uniqueness on ``(source, source_id)`` — task_engine, channel_pipeline
and emby all legitimately write repeat rows on that pair. A server-side check
also lets the notice RETIRE itself once the operator has updated, which a
client-side POST could never do.

Reconcile semantics
-------------------
Each run resolves to exactly one of:

* running version is current (or ahead), or the release is not newer
  → any existing ``version_check`` row is deleted (the notice has been acted on)
* a row already exists for this exact release → no-op (this is the idempotency
  that makes restarts and the 24h tick free)
* a newer release with no row, or a row for an OLDER release
  → supersede: drop what is there and write one row for the new release

Superseding rather than accumulating mirrors the emby "one progress
notification at a time" pattern (``routers/emby.py``): an "0.18.0 available"
entry is not history once 0.18.1 ships, it is wrong.

The GitHub endpoints are compile-time constants pointing at this project's own
repository — there is no operator-supplied URL here and therefore nothing for
the SSRF chokepoint (``security.ssrf``) to validate.
"""
import asyncio
import logging
import os
import re
from typing import Optional

import aiohttp

from database import get_session
from services.notification_service import (
    create_notification_internal,
    delete_notifications_by_source_internal,
)

logger = logging.getLogger(__name__)

#: ``Notification.source`` for every row this module writes. Also the handle
#: used to retire/supersede rows, so it must stay stable.
NOTIFICATION_SOURCE = "version_check"

GITHUB_REPO = "MotWakorb/enhancedchannelmanager"

#: Matches the TLS renewal manager's daily cadence (``main.startup_event``).
CHECK_INTERVAL_SECONDS = 86400

#: Let the container finish booting before the first outbound call.
INITIAL_DELAY_SECONDS = 60

_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

# Serialises the read-then-write reconcile. The loop is the only caller today
# (and is skipped in the HTTPS subprocess, so there is one per container), but
# the reconcile is not atomic at the DB level and must not become racy the
# moment a second caller appears.
_reconcile_lock = asyncio.Lock()

_loop_task: Optional[asyncio.Task] = None


# ---------------------------------------------------------------------------
# Running build identity
# ---------------------------------------------------------------------------

def current_version() -> str:
    """The version this container reports, matching ``GET /api/health``.

    Falls back to the CI-enforced ``APP_VERSION`` constant when ``ECM_VERSION``
    is unset (dev containers built outside the release pipeline) so the check
    still works there instead of silently disabling itself.
    """
    version = os.environ.get("ECM_VERSION", "").strip()
    if version and version != "unknown":
        return version
    try:
        from routers.backup import APP_VERSION

        return APP_VERSION
    except Exception:  # pragma: no cover — import guard only
        return "unknown"


def current_release_channel() -> str:
    return os.environ.get("RELEASE_CHANNEL", "latest")


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

_LEADING_INT = re.compile(r"^\s*[+-]?\d+")


def _parse_int_prefix(value: str) -> int:
    """JavaScript ``parseInt(v, 10) || 0`` semantics.

    The removed frontend implementation relied on parseInt's leniency ("1x" is
    1, "abc" is 0). Python's ``int()`` raises on both, so the leniency is
    reproduced explicitly to keep the move behaviour-preserving.
    """
    match = _LEADING_INT.match(value or "")
    return int(match.group()) if match else 0


def _base_and_build(version: str) -> tuple[tuple[int, int, int], int]:
    base, _, suffix = (version or "").partition("-")
    parts = base.split(".")
    major, minor, patch = (
        _parse_int_prefix(parts[0] if len(parts) > 0 else ""),
        _parse_int_prefix(parts[1] if len(parts) > 1 else ""),
        _parse_int_prefix(parts[2] if len(parts) > 2 else ""),
    )
    return (major, minor, patch), _parse_int_prefix(suffix)


def is_newer_version(latest_version: str, current_version_str: str) -> bool:
    """True when ``latest_version`` supersedes ``current_version_str``.

    Port of the frontend's ``isNewerVersion``: compare major.minor.patch, then
    use the ``-NNNN`` build suffix as the tiebreaker when the bases match.
    """
    latest_base, latest_build = _base_and_build(latest_version)
    current_base, current_build = _base_and_build(current_version_str)

    if latest_base != current_base:
        return latest_base > current_base
    return latest_build > current_build


# ---------------------------------------------------------------------------
# GitHub lookup
# ---------------------------------------------------------------------------

async def fetch_latest_release(release_channel: str) -> Optional[dict]:
    """Resolve the newest published version for a release channel.

    Returns ``{"version", "release_url", "release_notes"}``, or ``None`` when
    the lookup fails or the channel has no releases yet. ``None`` means "no
    information", which the reconcile treats as "change nothing" — a GitHub
    outage must never retire a valid update notice.
    """
    try:
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            if release_channel == "dev":
                url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/dev/frontend/package.json"
                async with session.get(url, headers={"Cache-Control": "no-cache"}) as response:
                    if response.status != 200:
                        logger.debug("[VERSION-CHECK] dev manifest fetch returned %s", response.status)
                        return None
                    payload = await response.json(content_type=None)
                version = (payload or {}).get("version")
                if not version:
                    return None
                return {
                    "version": version,
                    "release_url": f"https://github.com/{GITHUB_REPO}/tree/dev",
                    "release_notes": None,
                }

            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            async with session.get(
                url, headers={"Accept": "application/vnd.github.v3+json"}
            ) as response:
                if response.status != 200:
                    # 404 == no releases published yet; anything else is a real
                    # failure. Both mean "no information".
                    logger.debug("[VERSION-CHECK] releases/latest returned %s", response.status)
                    return None
                payload = await response.json(content_type=None)
            tag = (payload or {}).get("tag_name") or ""
            version = tag[1:] if tag.startswith("v") else tag
            if not version:
                return None
            return {
                "version": version,
                "release_url": (payload or {}).get("html_url")
                or f"https://github.com/{GITHUB_REPO}/releases/latest",
                "release_notes": (payload or {}).get("body"),
            }
    except Exception as exc:
        logger.debug("[VERSION-CHECK] Update lookup failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def _existing_source_ids() -> list[str]:
    from models import Notification

    session = get_session()
    try:
        rows = (
            session.query(Notification.source_id)
            .filter(Notification.source == NOTIFICATION_SOURCE)
            .all()
        )
        return [row[0] for row in rows]
    finally:
        session.close()


async def run_version_check() -> Optional[dict]:
    """Reconcile the ``version_check`` notification against the newest release.

    Returns the created notification dict, or ``None`` when nothing changed
    (already up to date, already notified, or the lookup failed).
    """
    running = current_version()
    if not running or running == "unknown":
        logger.debug("[VERSION-CHECK] Running version unknown — skipping check")
        return None

    release = await fetch_latest_release(current_release_channel())
    if release is None:
        return None

    latest = release["version"]

    async with _reconcile_lock:
        if not is_newer_version(latest, running):
            # Up to date (or ahead of the channel). Retire a notice the
            # operator has already acted on.
            await delete_notifications_by_source_internal(NOTIFICATION_SOURCE)
            return None

        existing = _existing_source_ids()
        if latest in existing:
            # Idempotent no-op: this is the path taken on every container
            # restart and every 24h tick until a newer release lands.
            return None

        if existing:
            await delete_notifications_by_source_internal(NOTIFICATION_SOURCE)

        logger.info("[VERSION-CHECK] Update available: %s (running %s)", latest, running)
        return await create_notification_internal(
            notification_type="info",
            title="Update available",
            message=f"Version {latest} is available. You are running {running}.",
            source=NOTIFICATION_SOURCE,
            source_id=latest,
            action_label="View release",
            action_url=release.get("release_url"),
            metadata={"latest_version": latest, "current_version": running},
            # The pill never left the browser. Moving it into the notification
            # centre is the ask; broadcasting it to Discord/Telegram/email is
            # not, so external dispatch stays off.
            send_alerts=False,
        )


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------

async def version_check_loop(
    interval_seconds: int = CHECK_INTERVAL_SECONDS,
    initial_delay_seconds: int = INITIAL_DELAY_SECONDS,
) -> None:
    await asyncio.sleep(initial_delay_seconds)
    while True:
        try:
            await run_version_check()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover — belt and braces
            logger.warning("[VERSION-CHECK] Check failed: %s", exc)
        await asyncio.sleep(interval_seconds)


def start_version_check_loop() -> Optional[asyncio.Task]:
    """Start the daily check. Idempotent; a strong reference is kept here
    because ``asyncio`` only holds weak references to running tasks."""
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        return _loop_task
    _loop_task = asyncio.create_task(version_check_loop())
    return _loop_task


def stop_version_check_loop() -> None:
    global _loop_task
    if _loop_task is not None and not _loop_task.done():
        _loop_task.cancel()
    _loop_task = None
