"""
M3U Digest Task.

Scheduled task to send digest emails with M3U playlist changes.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

import safe_regex
from database import get_session
from models import M3UChangeLog, M3UDigestSettings
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task
from m3u_digest_template import M3UDigestTemplate

logger = logging.getLogger(__name__)

# Hard cap on how many change rows a single digest run will load into memory.
# The email/Discord templates only render the 20 most-recent items per
# account-and-type, so loading more than this never affects the rendered output —
# it only risks OOM. An unbounded .all() over the change log (each row can carry
# its own stream-name JSON blob) drove RSS to ~18.5 GiB and corrupted the SQLite
# DB on a large deployment (GH #473). 5000 is far above any sane digest size.
MAX_DIGEST_CHANGES = 5000


@dataclass
class _DigestPayload:
    """Result of the synchronous (off-loop) digest build step.

    ``_build_digest_payload`` runs the blocking work — DB load, exclude
    filtering, and template rendering — in a worker thread and returns this
    so ``execute()`` can do the async delivery (email/Discord) back on the
    event loop. Either ``early_result`` is set (the run short-circuited:
    disabled / no recipients / below threshold / no changes) and the caller
    returns it verbatim, or the rendered content fields are populated.
    """

    # Short-circuit: when set, execute() returns this TaskResult directly.
    early_result: Optional["TaskResult"] = None

    # Rendered, ready-to-deliver content (None when early_result is set).
    subject: Optional[str] = None
    html_content: Optional[str] = None
    plain_content: Optional[str] = None
    discord_content: Optional[List[str]] = None

    # Delivery context carried back to the event loop.
    changes: List = field(default_factory=list)
    recipients: List[str] = field(default_factory=list)
    send_to_discord: bool = False
    is_test: bool = False
    since: Optional[datetime] = None


class _FilteredChange:
    """Proxy that wraps an M3UChangeLog to override stream names/count after filtering."""

    def __init__(self, original, kept_names: list):
        self._original = original
        self._kept_names = kept_names

    def get_stream_names(self) -> list:
        return self._kept_names

    @property
    def count(self):
        return len(self._kept_names)

    def __getattr__(self, name):
        return getattr(self._original, name)


def apply_exclude_filters(changes, group_patterns_raw, stream_patterns_raw):
    """Apply user-supplied exclude patterns to a list of change rows.

    This is the production exclude-filter used by ``M3UDigestTask.execute()``.
    It is a module-level function (rather than an inline block) so the unit
    tests can drive the exact code path the task runs, instead of a
    hand-copied reimplementation.

    Group patterns drop an entire change when its ``group_name`` matches.
    Stream patterns filter individual stream names within ``streams_added`` /
    ``streams_removed`` changes; a change whose stream names are all excluded
    is dropped, and a partially-excluded change is wrapped in
    ``_FilteredChange`` so its count/names reflect the survivors.

    Patterns are compiled via ``safe_regex`` so oversized patterns are
    rejected up-front (PatternTooLongError) and syntactically invalid ones
    are logged and skipped (SafeRegexError). Matching goes through
    ``safe_regex.search`` so every call has a per-invocation ReDoS timeout —
    on timeout or other failure, search returns None and the row is treated
    as NOT matched (i.e. not excluded), which is the safe default for an
    exclude filter.

    Returns the filtered list; the input list is not mutated. When no
    patterns are supplied, ``changes`` is returned unchanged.
    """
    if not group_patterns_raw and not stream_patterns_raw:
        return changes

    group_regexes = []
    for p in group_patterns_raw:
        try:
            group_regexes.append(safe_regex.compile(p, flags=re.IGNORECASE))
        except safe_regex.SafeRegexError as e:
            logger.debug("[M3U-DIGEST] Suppressed invalid group regex pattern: %s", e)

    stream_regexes = []
    for p in stream_patterns_raw:
        try:
            stream_regexes.append(safe_regex.compile(p, flags=re.IGNORECASE))
        except safe_regex.SafeRegexError as e:
            logger.debug("[M3U-DIGEST] Suppressed invalid stream regex pattern: %s", e)

    filtered_changes = []
    for change in changes:
        # Check group exclude: if group_name matches any pattern, drop it
        if group_regexes and change.group_name:
            if any(
                safe_regex.search(rx, change.group_name) is not None
                for rx in group_regexes
            ):
                continue

        # Check stream exclude: filter individual stream names
        if stream_regexes and change.change_type in ("streams_added", "streams_removed"):
            original_names = change.get_stream_names()
            if original_names:
                kept = [
                    n for n in original_names
                    if not any(
                        safe_regex.search(rx, n) is not None
                        for rx in stream_regexes
                    )
                ]
                if not kept:
                    continue  # All streams excluded, drop entire entry
                if len(kept) < len(original_names):
                    # Partial exclusion — wrap in a proxy to override count/names
                    change = _FilteredChange(change, kept)

        filtered_changes.append(change)
    return filtered_changes


def get_or_create_digest_settings(db: Session) -> M3UDigestSettings:
    """Get or create the M3U digest settings singleton."""
    settings = db.query(M3UDigestSettings).first()
    if not settings:
        settings = M3UDigestSettings(
            enabled=False,
            frequency="daily",
            include_group_changes=True,
            include_stream_changes=True,
            min_changes_threshold=1,
        )
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_frequency_delta(frequency: str) -> timedelta:
    """Get the timedelta for a frequency setting."""
    if frequency == "immediate":
        return timedelta(minutes=5)  # Look back 5 minutes for immediate
    elif frequency == "hourly":
        return timedelta(hours=1)
    elif frequency == "daily":
        return timedelta(days=1)
    elif frequency == "weekly":
        return timedelta(weeks=1)
    else:
        return timedelta(days=1)


@register_task
class M3UDigestTask(TaskScheduler):
    """
    Task to send M3U change digest emails.

    Supports two modes:
    - Scheduled: Runs on configured interval (hourly/daily/weekly)
    - Immediate: Can be triggered after M3U refresh to send changes immediately
    """

    task_id = "m3u_digest"
    task_name = "M3U Change Digest"
    task_description = "Send email digest of M3U playlist changes"

    def __init__(self, schedule_config: Optional[ScheduleConfig] = None):
        # Default to daily at 8 AM
        if schedule_config is None:
            schedule_config = ScheduleConfig(
                schedule_type=ScheduleType.MANUAL,
                schedule_time="08:00",
            )
        super().__init__(schedule_config)

    def get_config(self) -> dict:
        """Get digest task configuration (from DB settings)."""
        db = get_session()
        try:
            settings = get_or_create_digest_settings(db)
            return settings.to_dict()
        finally:
            db.close()

    def update_config(self, config: dict) -> None:
        """Update digest settings in database."""
        db = get_session()
        try:
            settings = get_or_create_digest_settings(db)

            if "enabled" in config:
                settings.enabled = config["enabled"]
            if "frequency" in config:
                settings.frequency = config["frequency"]
            if "email_recipients" in config:
                settings.set_email_recipients(config["email_recipients"])
            if "include_group_changes" in config:
                settings.include_group_changes = config["include_group_changes"]
            if "include_stream_changes" in config:
                settings.include_stream_changes = config["include_stream_changes"]
            if "min_changes_threshold" in config:
                settings.min_changes_threshold = config["min_changes_threshold"]

            db.commit()
        finally:
            db.close()

    async def execute(self, force: bool = False, m3u_account_id: Optional[int] = None) -> TaskResult:
        """
        Execute the M3U digest task.

        The heavy, loop-blocking work — the bounded SQLAlchemy load, the
        exclude-pattern filtering loop, and the CPU-bound template rendering —
        runs in a worker thread via ``run_in_threadpool`` (see
        ``_build_digest_payload``). Async delivery (email via threaded smtplib,
        Discord via aiohttp) then happens back on the event loop. This keeps a
        heavy digest from freezing every other request (GH #473), where the
        inline synchronous body blocked the loop for ~182s.

        Args:
            force: If True, send digest even if disabled or below threshold
            m3u_account_id: Optional filter for specific M3U account
        """
        started_at = datetime.utcnow()

        try:
            self._set_progress(status="fetching_changes")

            # Offload the blocking DB load + filter + render to a worker thread.
            # The builder owns its own DB session (do NOT share a SQLAlchemy
            # Session across threads) and returns rendered, ready-to-deliver
            # content — or an early_result to short-circuit.
            payload: _DigestPayload = await run_in_threadpool(
                self._build_digest_payload, force, m3u_account_id, started_at
            )

            if payload.early_result is not None:
                return payload.early_result

            recipients = payload.recipients
            send_to_discord = payload.send_to_discord
            changes = payload.changes

            # Track delivery results
            email_success = False
            discord_success = False
            delivery_methods = []

            # Send email if recipients configured
            if recipients:
                self._set_progress(status="sending_email")
                email_success = await self._send_digest_email(
                    recipients=recipients,
                    subject=payload.subject,
                    html_content=payload.html_content,
                    plain_content=payload.plain_content,
                )
                if email_success:
                    delivery_methods.append(f"email ({len(recipients)} recipients)")

            # Send to Discord if enabled (aiohttp — already async, stays on loop)
            if send_to_discord:
                self._set_progress(status="sending_discord")
                discord_success = await self._send_digest_discord(
                    changes=changes,
                    discord_content=payload.discord_content,
                    is_test=payload.is_test,
                )
                if discord_success:
                    delivery_methods.append("Discord")

            # Check if at least one delivery succeeded
            if email_success or discord_success:
                # Update last digest time (only for real digests, not tests).
                # Single-row UPDATE+commit — negligible vs the offloaded body,
                # so it stays on the loop in its own short-lived session.
                if changes:
                    db = get_session()
                    try:
                        s = get_or_create_digest_settings(db)
                        s.last_digest_at = datetime.utcnow()
                        db.commit()
                    finally:
                        db.close()

                # Build result message
                if changes:
                    message = f"Sent M3U digest with {len(changes)} changes via {', '.join(delivery_methods)}"
                else:
                    message = f"Test sent successfully via {', '.join(delivery_methods)}"

                return TaskResult(
                    success=True,
                    message=message,
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(changes),
                    success_count=len(changes),
                    details={
                        "changes_count": len(changes),
                        "recipients": recipients,
                        "discord_enabled": send_to_discord,
                        "email_success": email_success,
                        "discord_success": discord_success,
                        "since": payload.since.isoformat() if changes else None,
                        "is_test": payload.is_test,
                    },
                )
            else:
                failed_methods = []
                if recipients and not email_success:
                    failed_methods.append("email")
                if send_to_discord and not discord_success:
                    failed_methods.append("Discord")

                return TaskResult(
                    success=False,
                    message=f"Failed to send M3U digest via {', '.join(failed_methods)}",
                    error="All delivery methods failed",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(changes),
                )

        except Exception as e:
            logger.exception("[%s] M3U digest failed: %s", self.task_id, e)
            return TaskResult(
                success=False,
                message=f"M3U digest failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
            )

    def _build_digest_payload(
        self,
        force: bool,
        m3u_account_id: Optional[int],
        started_at: datetime,
    ) -> _DigestPayload:
        """
        Synchronous, loop-blocking digest build — run via ``run_in_threadpool``.

        Owns its own DB session (a SQLAlchemy Session is NOT thread-safe and
        must never be shared across the event loop and a worker thread). Loads
        the bounded change set, applies settings/exclude filtering, evaluates
        the threshold, and renders the email/Discord content. Returns a
        ``_DigestPayload`` whose ``early_result`` short-circuits ``execute()``
        when there is nothing to deliver, or whose rendered fields carry the
        content for async delivery back on the event loop.

        NOTE: this runs OFF the event loop, so it must not touch
        ``self._progress`` notification scheduling or any other event-loop
        state. ``self._set_progress`` calls stay in ``execute()``.
        """
        db = get_session()
        try:
            settings = get_or_create_digest_settings(db)

            # Check if enabled
            if not settings.enabled and not force:
                return _DigestPayload(early_result=TaskResult(
                    success=True,
                    message="M3U digest is disabled",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                ))

            # Check for at least one delivery method
            recipients = settings.get_email_recipients()
            send_to_discord = getattr(settings, 'send_to_discord', False)

            if not recipients and not send_to_discord:
                return _DigestPayload(early_result=TaskResult(
                    success=False,
                    message="No delivery methods configured (no email recipients and Discord disabled)",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                ))

            # Determine time range
            since = settings.last_digest_at
            if not since:
                # First time - use frequency to determine lookback
                since = datetime.utcnow() - get_frequency_delta(settings.frequency)

            # Get changes since last digest.
            # Bounded load (MAX_DIGEST_CHANGES): the templates only show the 20
            # newest items per account-and-type, so an unbounded .all() here is
            # pure memory risk with no effect on the rendered digest (GH #473).
            query = db.query(M3UChangeLog).filter(M3UChangeLog.change_time >= since)
            if m3u_account_id:
                query = query.filter(M3UChangeLog.m3u_account_id == m3u_account_id)

            total_changes = query.count()
            if total_changes > MAX_DIGEST_CHANGES:
                logger.warning(
                    "[%s] %s changes since %s exceeds cap of %s — digest will "
                    "summarize the %s most-recent changes only",
                    self.task_id, total_changes, since.isoformat(),
                    MAX_DIGEST_CHANGES, MAX_DIGEST_CHANGES,
                )

            changes = (
                query.order_by(M3UChangeLog.change_time.desc())
                .limit(MAX_DIGEST_CHANGES)
                .all()
            )

            # Filter by settings
            if not settings.include_group_changes:
                changes = [c for c in changes if c.change_type not in ("group_added", "group_removed")]
            if not settings.include_stream_changes:
                changes = [c for c in changes if c.change_type not in ("streams_added", "streams_removed")]

            # Apply exclude patterns (see apply_exclude_filters for the
            # safe_regex / graceful-fallback contract).
            changes = apply_exclude_filters(
                changes,
                settings.get_exclude_group_patterns(),
                settings.get_exclude_stream_patterns(),
            )

            # Check threshold
            if len(changes) < settings.min_changes_threshold and not force:
                logger.info(
                    "[%s] Only %s changes, below threshold of %s",
                    self.task_id, len(changes), settings.min_changes_threshold
                )
                return _DigestPayload(early_result=TaskResult(
                    success=True,
                    message=f"No digest sent: only {len(changes)} changes (threshold: {settings.min_changes_threshold})",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(changes),
                ))

            if not changes:
                if not force:
                    return _DigestPayload(early_result=TaskResult(
                        success=True,
                        message="No changes to report since last digest",
                        started_at=started_at,
                        completed_at=datetime.utcnow(),
                        total_items=0,
                    ))
                # Force mode with no changes - send a test email
                subject = "[ECM] M3U Digest Test - Configuration Verified"
                html_content = """
                <html>
                <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; padding: 20px; background-color: #f5f5f5;">
                    <div style="max-width: 600px; margin: 0 auto; background: white; border-radius: 8px; padding: 30px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
                        <h1 style="color: #22C55E; margin-top: 0;">✓ Test Successful</h1>
                        <p style="color: #333; font-size: 16px;">
                            Your M3U Digest email configuration is working correctly.
                        </p>
                        <p style="color: #666; font-size: 14px;">
                            When there are actual M3U changes to report, you will receive digest emails at this address.
                        </p>
                        <hr style="border: none; border-top: 1px solid #eee; margin: 20px 0;">
                        <p style="color: #999; font-size: 12px;">
                            This is a test email from Enhanced Channel Manager.
                        </p>
                    </div>
                </body>
                </html>
                """
                plain_content = """
M3U Digest Test - Configuration Verified

Your M3U Digest email configuration is working correctly.

When there are actual M3U changes to report, you will receive digest emails at this address.

---
This is a test email from Enhanced Channel Manager.
                """.strip()
                discord_content = None
            else:
                # Build digest content (CPU-bound string building — off-loop).
                template = M3UDigestTemplate()
                show_detailed = getattr(settings, 'show_detailed_list', True)
                html_content = template.render_html(changes, since, show_detailed_list=show_detailed)
                plain_content = template.render_plain(changes, since, show_detailed_list=show_detailed)
                subject = template.get_subject(changes)
                discord_content = (
                    template.render_discord(changes, since, show_detailed_list=show_detailed)
                    if send_to_discord else None
                )

            return _DigestPayload(
                subject=subject,
                html_content=html_content,
                plain_content=plain_content,
                discord_content=discord_content,
                changes=changes,
                recipients=recipients,
                send_to_discord=send_to_discord,
                is_test=not bool(changes),
                since=since,
            )
        finally:
            db.close()

    async def _send_digest_email(
        self,
        recipients: List[str],
        subject: str,
        html_content: str,
        plain_content: str,
    ) -> bool:
        """
        Send the digest email using shared SMTP settings or alert methods.

        Priority:
        1. Shared SMTP settings (from General Settings)
        2. First enabled SMTP alert method (fallback)

        Returns True if email was sent successfully.
        """
        from config import get_settings

        # First, try shared SMTP settings
        settings = get_settings()
        if settings.is_smtp_configured():
            logger.info("[%s] Using shared SMTP settings", self.task_id)
            smtp_config = {
                "smtp_host": settings.smtp_host,
                "smtp_port": settings.smtp_port,
                "smtp_user": settings.smtp_user,
                "smtp_password": settings.smtp_password,
                "from_email": settings.smtp_from_email,
                "from_name": settings.smtp_from_name,
                "use_tls": settings.smtp_use_tls,
                "use_ssl": settings.smtp_use_ssl,
            }
            success = await self._send_custom_email_with_config(
                smtp_config,
                recipients,
                subject,
                html_content,
                plain_content,
            )
            if success:
                return True
            logger.warning("[%s] Shared SMTP failed, trying alert methods", self.task_id)

        # Fall back to SMTP alert methods
        try:
            from alert_methods import get_alert_manager

            alert_manager = get_alert_manager()

            # Find SMTP alert methods
            smtp_methods = [
                m for m in alert_manager.get_methods()
                if m.method_type == "smtp" and m.enabled
            ]

            if not smtp_methods:
                logger.warning("[%s] No enabled SMTP alert methods found", self.task_id)
                return False

            # Use the first enabled SMTP method
            smtp_method = smtp_methods[0]
            logger.info("[%s] Using SMTP alert method: %s", self.task_id, smtp_method.name)

            success = await self._send_custom_email_with_config(
                smtp_method.config,
                recipients,
                subject,
                html_content,
                plain_content,
            )
            return success

        except Exception as e:
            logger.error("[%s] Failed to send digest email: %s", self.task_id, e)
            return False

    async def _send_custom_email_with_config(
        self,
        config: Dict,
        recipients: List[str],
        subject: str,
        html_content: str,
        plain_content: str,
    ) -> bool:
        """Send a custom email with pre-built HTML content using SMTP config dict.

        The synchronous smtplib I/O (connect / STARTTLS / login / sendmail) is
        offloaded to a worker thread so SMTP latency cannot block the asyncio
        event loop (GH #473).
        """
        return await run_in_threadpool(
            self._send_custom_email_with_config_sync,
            config,
            recipients,
            subject,
            html_content,
            plain_content,
        )

    def _send_custom_email_with_config_sync(
        self,
        config: Dict,
        recipients: List[str],
        subject: str,
        html_content: str,
        plain_content: str,
    ) -> bool:
        """Blocking smtplib send — runs in a worker thread, never on the loop."""
        import smtplib
        import ssl
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        try:
            smtp_host = config.get("smtp_host")
            smtp_port = int(config.get("smtp_port", 587))
            from_email = config.get("from_email")
            from_name = config.get("from_name", "ECM M3U Digest")

            if not all([smtp_host, from_email]):
                logger.error("[%s] Missing SMTP configuration", self.task_id)
                return False

            # Build the email
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = ", ".join(recipients)

            msg.attach(MIMEText(plain_content, "plain"))
            msg.attach(MIMEText(html_content, "html"))

            # Connect and send
            use_ssl = config.get("use_ssl", False)
            use_tls = config.get("use_tls", True)

            if use_ssl:
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=30)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
                if use_tls:
                    context = ssl.create_default_context()
                    server.starttls(context=context)

            # Authenticate if credentials provided
            smtp_user = config.get("smtp_user")
            smtp_password = config.get("smtp_password")
            if smtp_user and smtp_password:
                server.login(smtp_user, smtp_password)

            server.sendmail(from_email, recipients, msg.as_string())
            server.quit()

            logger.info("[%s] Sent digest email to %s recipients", self.task_id, len(recipients))
            return True

        except Exception as e:
            logger.error("[%s] SMTP error: %s", self.task_id, e)
            return False

    async def _send_digest_discord(
        self,
        changes: List,
        discord_content: Optional[List[str]],
        is_test: bool = False,
    ) -> bool:
        """
        Send digest to Discord using shared webhook from settings.

        Args:
            changes: List of M3UChangeLog entries
            discord_content: List of message chunks (each under 2000 chars)
            is_test: If True, send a test message instead of content

        Returns True if message was sent successfully.
        """
        import aiohttp
        from config import get_settings

        settings = get_settings()
        webhook_url = None

        # Try shared webhook from General Settings first
        if settings.is_discord_configured():
            webhook_url = settings.discord_webhook_url
        else:
            # Fall back to first enabled Discord alert method
            try:
                from database import get_session as get_db_session
                from models import AlertMethod
                import json
                db = get_db_session()
                try:
                    method = db.query(AlertMethod).filter(
                        AlertMethod.method_type == "discord",
                        AlertMethod.enabled == True,
                    ).first()
                    if method:
                        config = json.loads(method.config) if isinstance(method.config, str) else method.config
                        webhook_url = config.get("webhook_url")
                        logger.info("[%s] Using Discord webhook from alert method '%s'", self.task_id, method.name)
                finally:
                    db.close()
            except Exception as e:
                logger.warning("[%s] Failed to look up Discord alert method: %s", self.task_id, e)

        if not webhook_url:
            logger.warning("[%s] No Discord webhook configured (checked General Settings and Alert Methods)", self.task_id)
            return False

        # Validate webhook URL format
        if not webhook_url.startswith("https://discord.com/api/webhooks/") and \
           not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
            logger.error("[%s] Invalid Discord webhook URL format", self.task_id)
            return False

        try:
            async with aiohttp.ClientSession() as session:
                if is_test:
                    # Send test message
                    payload = {
                        "content": (
                            "**✓ M3U Digest Test Successful**\n\n"
                            "Your Discord webhook is configured correctly for M3U Digest notifications.\n"
                            "When there are actual M3U changes to report, you will receive digests here."
                        ),
                        "username": "ECM M3U Digest",
                    }

                    async with session.post(
                        webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        if response.status == 204:
                            logger.info("[%s] Discord test message sent successfully", self.task_id)
                            return True
                        else:
                            text = await response.text()
                            logger.error("[%s] Discord webhook failed: %s - %s", self.task_id, response.status, text)
                            return False

                # Send actual digest content - may be multiple messages
                if not discord_content:
                    logger.warning("[%s] No Discord content to send", self.task_id)
                    return False

                for i, chunk in enumerate(discord_content):
                    payload = {
                        "content": chunk,
                        "username": "ECM M3U Digest",
                    }

                    async with session.post(
                        webhook_url,
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as response:
                        if response.status == 204:
                            logger.debug("[%s] Discord message %s/%s sent", self.task_id, i + 1, len(discord_content))
                        elif response.status == 429:
                            # Rate limited - wait and retry
                            retry_after = int(response.headers.get("Retry-After", 1))
                            logger.warning("[%s] Discord rate limited, waiting %ss", self.task_id, retry_after)
                            import asyncio
                            await asyncio.sleep(retry_after)
                            # Retry this message
                            async with session.post(
                                webhook_url,
                                json=payload,
                                timeout=aiohttp.ClientTimeout(total=10),
                            ) as retry_response:
                                if retry_response.status != 204:
                                    logger.error("[%s] Discord retry failed: %s", self.task_id, retry_response.status)
                                    return False
                        else:
                            text = await response.text()
                            logger.error("[%s] Discord webhook failed: %s - %s", self.task_id, response.status, text)
                            return False

                    # Small delay between messages to avoid rate limiting
                    if i < len(discord_content) - 1:
                        import asyncio
                        await asyncio.sleep(0.5)

                logger.info("[%s] Sent %s Discord message(s)", self.task_id, len(discord_content))
                return True

        except aiohttp.ClientError as e:
            logger.error("[%s] Discord connection error: %s", self.task_id, e)
            return False
        except Exception as e:
            logger.exception("[%s] Discord unexpected error: %s", self.task_id, e)
            return False


async def send_immediate_digest(m3u_account_id: int) -> TaskResult:
    """
    Send an immediate digest for a specific M3U account.
    Called after M3U refresh if immediate mode is enabled.
    """
    db = get_session()
    try:
        settings = get_or_create_digest_settings(db)

        if not settings.enabled or settings.frequency != "immediate":
            return TaskResult(
                success=True,
                message="Immediate digest not enabled",
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
            )

        task = M3UDigestTask()
        return await task.execute(m3u_account_id=m3u_account_id)
    finally:
        db.close()
