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

            # Check for recipients
            recipients = settings.get_email_recipients()
            if not recipients:
                return TaskResult(
                    success=False,
                    message="No email recipients configured for M3U digest",
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
                return TaskResult(
                    success=True,
                    message="No changes to report since last digest",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=0,
                )

            self._set_progress(status="building_digest", total=len(changes))

            # Build digest content
            template = M3UDigestTemplate()
            html_content = template.render_html(changes, since)
            plain_content = template.render_plain(changes, since)
            subject = template.get_subject(changes)

            self._set_progress(status="sending_email")

            # Send via SMTP alert method (if configured)
            send_success = await self._send_digest_email(
                recipients=recipients,
                subject=subject,
                html_content=html_content,
                plain_content=plain_content,
            )

            if send_success:
                # Update last digest time
                settings.last_digest_at = datetime.utcnow()
                db.commit()

                return TaskResult(
                    success=True,
                    message=f"Sent M3U digest with {len(changes)} changes to {len(recipients)} recipients",
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    total_items=len(changes),
                    success_count=len(changes),
                    details={
                        "changes_count": len(changes),
                        "recipients": recipients,
                        "since": since.isoformat(),
                    },
                )
            else:
                return TaskResult(
                    success=False,
                    message="Failed to send M3U digest email",
                    error="Email sending failed",
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
        Send the digest email using configured SMTP alert methods.

        Returns True if at least one email was sent successfully.
        """
        from alert_methods import get_alert_manager, AlertMessage

        try:
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

            # Create a custom alert message for the digest
            message = AlertMessage(
                notification_type="info",
                title=subject,
                message=plain_content,
                source="m3u_digest",
                metadata={"digest": True},
            )

            # Override the to_emails in config temporarily
            original_to = smtp_method.config.get("to_emails")
            smtp_method.config["to_emails"] = recipients

            try:
                # The SMTP method will handle HTML/plain text
                # We need a custom send for digest with pre-built HTML
                success = await self._send_custom_email(
                    smtp_method,
                    recipients,
                    subject,
                    html_content,
                    plain_content,
                )
                return success
            finally:
                # Restore original recipients
                smtp_method.config["to_emails"] = original_to

        except Exception as e:
            logger.error(f"[{self.task_id}] Failed to send digest email: {e}")
            return False

    async def _send_custom_email(
        self,
        smtp_method,
        recipients: List[str],
        subject: str,
        html_content: str,
        plain_content: str,
    ) -> bool:
        """Send a custom email with pre-built HTML content."""
        import smtplib
        import ssl
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        try:
            config = smtp_method.config
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
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port)
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
