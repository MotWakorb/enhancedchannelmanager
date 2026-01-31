"""
M3U Digest Task.

Scheduled task to send digest emails with M3U playlist changes.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from sqlalchemy.orm import Session

from database import get_session
from models import M3UChangeLog, M3UDigestSettings
from task_scheduler import TaskScheduler, TaskResult, ScheduleConfig, ScheduleType
from task_registry import register_task
from m3u_digest_template import M3UDigestTemplate

logger = logging.getLogger(__name__)


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

        Args:
            force: If True, send digest even if disabled or below threshold
            m3u_account_id: Optional filter for specific M3U account
        """
        started_at = datetime.utcnow()
        db = get_session()

        try:
            settings = get_or_create_digest_settings(db)

            # Check if enabled
            if not settings.enabled and not force:
                return TaskResult(
                    success=True,
                    message="M3U digest is disabled",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                )

            # Check for at least one delivery method
            recipients = settings.get_email_recipients()
            send_to_discord = getattr(settings, 'send_to_discord', False)

            if not recipients and not send_to_discord:
                return TaskResult(
                    success=False,
                    message="No delivery methods configured (no email recipients and Discord disabled)",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                )

            # Determine time range
            since = settings.last_digest_at
            if not since:
                # First time - use frequency to determine lookback
                since = datetime.utcnow() - get_frequency_delta(settings.frequency)

            self._set_progress(status="fetching_changes")

            # Get changes since last digest
            query = db.query(M3UChangeLog).filter(M3UChangeLog.change_time >= since)
            if m3u_account_id:
                query = query.filter(M3UChangeLog.m3u_account_id == m3u_account_id)

            changes = query.order_by(M3UChangeLog.change_time.desc()).all()

            # Filter by settings
            if not settings.include_group_changes:
                changes = [c for c in changes if c.change_type not in ("group_added", "group_removed")]
            if not settings.include_stream_changes:
                changes = [c for c in changes if c.change_type not in ("streams_added", "streams_removed")]

            # Check threshold
            if len(changes) < settings.min_changes_threshold and not force:
                logger.info(
                    f"[{self.task_id}] Only {len(changes)} changes, below threshold of {settings.min_changes_threshold}"
                )
                return TaskResult(
                    success=True,
                    message=f"No digest sent: only {len(changes)} changes (threshold: {settings.min_changes_threshold})",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(changes),
                )

            if not changes:
                if not force:
                    return TaskResult(
                        success=True,
                        message="No changes to report since last digest",
                        started_at=started_at,
                        completed_at=datetime.utcnow(),
                        total_items=0,
                    )
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
            else:
                self._set_progress(status="building_digest", total=len(changes))

                # Build digest content
                template = M3UDigestTemplate()
                show_detailed = getattr(settings, 'show_detailed_list', True)
                html_content = template.render_html(changes, since, show_detailed_list=show_detailed)
                plain_content = template.render_plain(changes, since, show_detailed_list=show_detailed)
                subject = template.get_subject(changes)

            # Track delivery results
            email_success = False
            discord_success = False
            delivery_methods = []

            # Send email if recipients configured
            if recipients:
                self._set_progress(status="sending_email")
                email_success = await self._send_digest_email(
                    recipients=recipients,
                    subject=subject,
                    html_content=html_content,
                    plain_content=plain_content,
                )
                if email_success:
                    delivery_methods.append(f"email ({len(recipients)} recipients)")

            # Send to Discord if enabled
            if send_to_discord:
                self._set_progress(status="sending_discord")
                discord_content = template.render_discord(changes, since, show_detailed_list=show_detailed) if changes else None
                discord_success = await self._send_digest_discord(
                    changes=changes,
                    discord_content=discord_content,
                    is_test=not bool(changes),
                )
                if discord_success:
                    delivery_methods.append("Discord")

            # Check if at least one delivery succeeded
            if email_success or discord_success:
                # Update last digest time (only for real digests, not tests)
                if changes:
                    settings.last_digest_at = datetime.utcnow()
                    db.commit()

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
                        "since": since.isoformat() if changes else None,
                        "is_test": not bool(changes),
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
            logger.exception(f"[{self.task_id}] M3U digest failed: {e}")
            return TaskResult(
                success=False,
                message=f"M3U digest failed: {str(e)}",
                error=str(e),
                started_at=started_at,
                completed_at=datetime.utcnow(),
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
            logger.info(f"[{self.task_id}] Using shared SMTP settings")
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
            logger.warning(f"[{self.task_id}] Shared SMTP failed, trying alert methods")

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
                logger.warning(f"[{self.task_id}] No enabled SMTP alert methods found")
                return False

            # Use the first enabled SMTP method
            smtp_method = smtp_methods[0]
            logger.info(f"[{self.task_id}] Using SMTP alert method: {smtp_method.name}")

            success = await self._send_custom_email_with_config(
                smtp_method.config,
                recipients,
                subject,
                html_content,
                plain_content,
            )
            return success

        except Exception as e:
            logger.error(f"[{self.task_id}] Failed to send digest email: {e}")
            return False

    async def _send_custom_email_with_config(
        self,
        config: Dict,
        recipients: List[str],
        subject: str,
        html_content: str,
        plain_content: str,
    ) -> bool:
        """Send a custom email with pre-built HTML content using SMTP config dict."""
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
                logger.error(f"[{self.task_id}] Missing SMTP configuration")
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

            logger.info(f"[{self.task_id}] Sent digest email to {len(recipients)} recipients")
            return True

        except Exception as e:
            logger.error(f"[{self.task_id}] SMTP error: {e}")
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
        if not settings.is_discord_configured():
            logger.warning(f"[{self.task_id}] Discord not configured in General Settings")
            return False

        webhook_url = settings.discord_webhook_url

        # Validate webhook URL format
        if not webhook_url.startswith("https://discord.com/api/webhooks/") and \
           not webhook_url.startswith("https://discordapp.com/api/webhooks/"):
            logger.error(f"[{self.task_id}] Invalid Discord webhook URL format")
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
                            logger.info(f"[{self.task_id}] Discord test message sent successfully")
                            return True
                        else:
                            text = await response.text()
                            logger.error(f"[{self.task_id}] Discord webhook failed: {response.status} - {text}")
                            return False

                # Send actual digest content - may be multiple messages
                if not discord_content:
                    logger.warning(f"[{self.task_id}] No Discord content to send")
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
                            logger.debug(f"[{self.task_id}] Discord message {i+1}/{len(discord_content)} sent")
                        elif response.status == 429:
                            # Rate limited - wait and retry
                            retry_after = int(response.headers.get("Retry-After", 1))
                            logger.warning(f"[{self.task_id}] Discord rate limited, waiting {retry_after}s")
                            import asyncio
                            await asyncio.sleep(retry_after)
                            # Retry this message
                            async with session.post(
                                webhook_url,
                                json=payload,
                                timeout=aiohttp.ClientTimeout(total=10),
                            ) as retry_response:
                                if retry_response.status != 204:
                                    logger.error(f"[{self.task_id}] Discord retry failed: {retry_response.status}")
                                    return False
                        else:
                            text = await response.text()
                            logger.error(f"[{self.task_id}] Discord webhook failed: {response.status} - {text}")
                            return False

                    # Small delay between messages to avoid rate limiting
                    if i < len(discord_content) - 1:
                        import asyncio
                        await asyncio.sleep(0.5)

                logger.info(f"[{self.task_id}] Sent {len(discord_content)} Discord message(s)")
                return True

        except aiohttp.ClientError as e:
            logger.error(f"[{self.task_id}] Discord connection error: {e}")
            return False
        except Exception as e:
            logger.error(f"[{self.task_id}] Discord unexpected error: {e}")
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
