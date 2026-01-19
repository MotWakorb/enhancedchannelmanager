"""
SMTP Email Alert Method.

Sends notifications via email using SMTP.
"""
import logging
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, List

from alert_methods import AlertMethod, AlertMessage, register_method

logger = logging.getLogger(__name__)


@register_method
class SMTPMethod(AlertMethod):
    """Sends alerts via SMTP email."""

    method_type = "smtp"
    display_name = "Email (SMTP)"
    required_config_fields = ["smtp_host", "smtp_port", "from_email", "to_emails"]
    optional_config_fields = {
        "smtp_user": "",
        "smtp_password": "",
        "use_tls": True,
        "use_ssl": False,
        "from_name": "ECM Alerts",
    }

    # Emoji alternatives for plain text
    TYPE_LABELS = {
        "info": "[INFO]",
        "success": "[SUCCESS]",
        "warning": "[WARNING]",
        "error": "[ERROR]",
    }

    def _build_html_message(self, message: AlertMessage) -> str:
        """Build an HTML email body."""
        colors = {
            "info": "#3B82F6",
            "success": "#22C55E",
            "warning": "#F59E0B",
            "error": "#EF4444",
        }
        color = colors.get(message.notification_type, "#808080")

        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: {color}; color: white; padding: 15px; border-radius: 8px 8px 0 0; }}
                .header h2 {{ margin: 0; font-size: 18px; }}
                .body {{ background-color: #f8f9fa; padding: 20px; border: 1px solid #e9ecef; border-top: none; }}
                .message {{ color: #333; line-height: 1.6; }}
                .metadata {{ margin-top: 15px; padding-top: 15px; border-top: 1px solid #e9ecef; }}
                .metadata-item {{ font-size: 13px; color: #666; margin: 5px 0; }}
                .footer {{ font-size: 12px; color: #999; margin-top: 15px; padding-top: 15px; border-top: 1px solid #e9ecef; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>{self.get_emoji(message.notification_type)} {message.title or 'Notification'}</h2>
                </div>
                <div class="body">
                    <div class="message">{message.message.replace(chr(10), '<br>')}</div>
        """

        if message.metadata:
            html += '<div class="metadata">'
            for key, value in message.metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    html += f'<div class="metadata-item"><strong>{key.replace("_", " ").title()}:</strong> {value}</div>'
            html += '</div>'

        html += f"""
                    <div class="footer">
                        Sent from Enhanced Channel Manager{' - ' + message.source if message.source else ''}<br>
                        {message.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        return html

    def _build_plain_message(self, message: AlertMessage) -> str:
        """Build a plain text email body."""
        label = self.TYPE_LABELS.get(message.notification_type, "[NOTIFICATION]")
        parts = [
            f"{label} {message.title or 'Notification'}",
            "",
            message.message,
        ]

        if message.metadata:
            parts.append("")
            parts.append("-" * 40)
            for key, value in message.metadata.items():
                if isinstance(value, (str, int, float, bool)):
                    parts.append(f"{key.replace('_', ' ').title()}: {value}")

        parts.append("")
        parts.append("-" * 40)
        parts.append(f"Sent from Enhanced Channel Manager{' - ' + message.source if message.source else ''}")
        parts.append(f"Time: {message.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        return "\n".join(parts)

    async def send(self, message: AlertMessage) -> bool:
        """Send an email alert via SMTP."""
        smtp_host = self.config.get("smtp_host")
        smtp_port = int(self.config.get("smtp_port", 587))
        from_email = self.config.get("from_email")
        to_emails = self.config.get("to_emails")

        logger.debug(f"SMTP method {self.name}: config keys={list(self.config.keys())}, from_email={from_email}, to_emails={to_emails}")

        if not all([smtp_host, from_email, to_emails]):
            logger.error(f"SMTP method {self.name}: Missing required configuration")
            return False

        # Parse to_emails if it's a string
        if isinstance(to_emails, str):
            to_emails = [e.strip() for e in to_emails.split(",") if e.strip()]

        if not to_emails:
            logger.error(f"SMTP method {self.name}: No recipients configured")
            return False

        # Build the email
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"{self.TYPE_LABELS.get(message.notification_type, '')} {message.title or 'ECM Notification'}"
        msg["From"] = f"{self.config.get('from_name', 'ECM Alerts')} <{from_email}>"
        msg["To"] = ", ".join(to_emails)

        # Attach both plain text and HTML versions
        plain_text = self._build_plain_message(message)
        html_text = self._build_html_message(message)

        msg.attach(MIMEText(plain_text, "plain"))
        msg.attach(MIMEText(html_text, "html"))

        # Get authentication credentials
        smtp_user = self.config.get("smtp_user") or None
        smtp_password = self.config.get("smtp_password") or None
        use_tls = self.config.get("use_tls", True)
        use_ssl = self.config.get("use_ssl", False)

        try:
            # Connect to SMTP server
            if use_ssl:
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=10)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)

            try:
                if use_tls and not use_ssl:
                    server.starttls(context=ssl.create_default_context())

                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)

                logger.debug(f"SMTP method {self.name}: Sending from={from_email}, to={to_emails}, From header={msg['From']}")
                server.sendmail(from_email, to_emails, msg.as_string())
                logger.info(f"SMTP method {self.name}: Email sent to {len(to_emails)} recipient(s)")
                return True

            finally:
                server.quit()

        except smtplib.SMTPAuthenticationError as e:
            logger.error(f"SMTP method {self.name}: Authentication failed: {e}")
            return False
        except smtplib.SMTPException as e:
            logger.error(f"SMTP method {self.name}: SMTP error: {e}")
            return False
        except Exception as e:
            logger.error(f"SMTP method {self.name}: Unexpected error: {e}")
            return False

    async def test_connection(self) -> tuple[bool, str]:
        """Test the SMTP connection by sending a test email."""
        smtp_host = self.config.get("smtp_host")
        smtp_port = int(self.config.get("smtp_port", 587))
        from_email = self.config.get("from_email")
        to_emails = self.config.get("to_emails")

        if not smtp_host:
            return False, "SMTP host not configured"
        if not from_email:
            return False, "From email not configured"
        if not to_emails:
            return False, "No recipients configured"

        # First, just test the connection
        use_ssl = self.config.get("use_ssl", False)
        use_tls = self.config.get("use_tls", True)
        smtp_user = self.config.get("smtp_user")
        smtp_password = self.config.get("smtp_password")

        try:
            if use_ssl:
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=10)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)

            try:
                if use_tls and not use_ssl:
                    server.starttls(context=ssl.create_default_context())

                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)

                # Send test message
                test_message = AlertMessage(
                    title="Connection Test",
                    message="This is a test message from Enhanced Channel Manager. "
                            "If you see this, your SMTP settings are configured correctly!",
                    notification_type="info",
                    source="ECM Alert Test",
                )

                success = await self.send(test_message)
                if success:
                    return True, "Test email sent successfully"
                else:
                    return False, "Connected but failed to send test email"

            finally:
                server.quit()

        except smtplib.SMTPAuthenticationError:
            return False, "Authentication failed - check username and password"
        except smtplib.SMTPConnectError:
            return False, f"Could not connect to {smtp_host}:{smtp_port}"
        except TimeoutError:
            return False, f"Connection timed out to {smtp_host}:{smtp_port}"
        except Exception as e:
            return False, f"Error: {str(e)}"

    async def send_digest(self, messages: List[AlertMessage]) -> bool:
        """Send a digest of multiple messages as a nicely formatted email."""
        if not messages:
            return True

        if len(messages) == 1:
            return await self.send(messages[0])

        # Build digest email
        smtp_host = self.config.get("smtp_host")
        smtp_port = int(self.config.get("smtp_port", 587))
        from_email = self.config.get("from_email")
        to_emails = self.config.get("to_emails")

        if not all([smtp_host, from_email, to_emails]):
            logger.error(f"SMTP method {self.name}: Missing required configuration")
            return False

        if isinstance(to_emails, str):
            to_emails = [e.strip() for e in to_emails.split(",") if e.strip()]

        if not to_emails:
            logger.error(f"SMTP method {self.name}: No recipients configured")
            return False

        # Count by type
        counts = {"success": 0, "error": 0, "warning": 0, "info": 0}
        for msg in messages:
            counts[msg.notification_type] = counts.get(msg.notification_type, 0) + 1

        # Build summary for subject
        summary_parts = []
        if counts["success"]:
            summary_parts.append(f"{counts['success']} ✓")
        if counts["error"]:
            summary_parts.append(f"{counts['error']} ✗")
        if counts["warning"]:
            summary_parts.append(f"{counts['warning']} ⚠")

        subject = f"ECM Digest: {', '.join(summary_parts) if summary_parts else f'{len(messages)} notifications'}"

        # Build HTML digest
        html = self._build_html_digest(messages, counts)
        plain = self._build_plain_digest(messages, counts)

        # Create email
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{self.config.get('from_name', 'ECM Alerts')} <{from_email}>"
        msg["To"] = ", ".join(to_emails)

        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(html, "html"))

        # Send
        use_tls = self.config.get("use_tls", True)
        use_ssl = self.config.get("use_ssl", False)
        smtp_user = self.config.get("smtp_user") or None
        smtp_password = self.config.get("smtp_password") or None

        try:
            if use_ssl:
                context = ssl.create_default_context()
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=10)
            else:
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)

            try:
                if use_tls and not use_ssl:
                    server.starttls(context=ssl.create_default_context())

                if smtp_user and smtp_password:
                    server.login(smtp_user, smtp_password)

                logger.debug(f"SMTP method {self.name}: Sending digest with {len(messages)} alerts")
                server.sendmail(from_email, to_emails, msg.as_string())
                logger.info(f"SMTP method {self.name}: Digest sent with {len(messages)} alerts")
                return True

            finally:
                server.quit()

        except Exception as e:
            logger.error(f"SMTP method {self.name}: Failed to send digest: {e}")
            return False

    def _build_html_digest(self, messages: List[AlertMessage], counts: dict) -> str:
        """Build an HTML digest email body."""
        colors = {
            "info": "#3B82F6",
            "success": "#22C55E",
            "warning": "#F59E0B",
            "error": "#EF4444",
        }

        # Determine header color based on worst status
        if counts.get("error", 0) > 0:
            header_color = colors["error"]
        elif counts.get("warning", 0) > 0:
            header_color = colors["warning"]
        else:
            header_color = colors["success"]

        # Build summary line
        summary_parts = []
        if counts.get("success", 0):
            summary_parts.append(f"✅ {counts['success']} succeeded")
        if counts.get("error", 0):
            summary_parts.append(f"❌ {counts['error']} failed")
        if counts.get("warning", 0):
            summary_parts.append(f"⚠️ {counts['warning']} warnings")
        if counts.get("info", 0):
            summary_parts.append(f"ℹ️ {counts['info']} info")

        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background-color: {header_color}; color: white; padding: 15px; border-radius: 8px 8px 0 0; }}
                .header h2 {{ margin: 0; font-size: 18px; }}
                .summary {{ font-size: 14px; margin-top: 5px; opacity: 0.9; }}
                .body {{ background-color: #f8f9fa; padding: 20px; border: 1px solid #e9ecef; border-top: none; border-radius: 0 0 8px 8px; }}
                .alert-item {{ padding: 12px; margin-bottom: 10px; border-radius: 6px; border-left: 4px solid; background: white; }}
                .alert-success {{ border-color: #22C55E; }}
                .alert-error {{ border-color: #EF4444; }}
                .alert-warning {{ border-color: #F59E0B; }}
                .alert-info {{ border-color: #3B82F6; }}
                .alert-title {{ font-weight: 600; margin-bottom: 4px; }}
                .alert-message {{ font-size: 14px; color: #666; }}
                .footer {{ font-size: 12px; color: #999; margin-top: 15px; padding-top: 15px; border-top: 1px solid #e9ecef; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>📊 ECM Notification Digest</h2>
                    <div class="summary">{' | '.join(summary_parts)}</div>
                </div>
                <div class="body">
        """

        for message in messages:
            emoji = self.get_emoji(message.notification_type)
            alert_class = f"alert-{message.notification_type}"
            html += f"""
                    <div class="alert-item {alert_class}">
                        <div class="alert-title">{emoji} {message.title or 'Notification'}</div>
                        <div class="alert-message">{message.message}</div>
                    </div>
            """

        now = messages[0].timestamp if messages else None
        time_str = now.strftime('%Y-%m-%d %H:%M:%S UTC') if now else ''

        html += f"""
                    <div class="footer">
                        Sent from Enhanced Channel Manager<br>
                        {time_str}
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        return html

    def _build_plain_digest(self, messages: List[AlertMessage], counts: dict) -> str:
        """Build a plain text digest."""
        parts = ["ECM NOTIFICATION DIGEST", "=" * 40, ""]

        # Summary
        summary_parts = []
        if counts.get("success", 0):
            summary_parts.append(f"{counts['success']} succeeded")
        if counts.get("error", 0):
            summary_parts.append(f"{counts['error']} failed")
        if counts.get("warning", 0):
            summary_parts.append(f"{counts['warning']} warnings")
        if counts.get("info", 0):
            summary_parts.append(f"{counts['info']} info")

        parts.append(f"Summary: {', '.join(summary_parts)}")
        parts.append("")
        parts.append("-" * 40)

        for message in messages:
            label = self.TYPE_LABELS.get(message.notification_type, "[NOTIFICATION]")
            parts.append(f"{label} {message.title or 'Notification'}")
            parts.append(f"  {message.message}")
            parts.append("")

        parts.append("-" * 40)
        parts.append("Sent from Enhanced Channel Manager")

        return "\n".join(parts)
