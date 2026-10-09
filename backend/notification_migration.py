"""
Move the legacy shared Discord / Telegram settings into alert methods (GH #1026).

Before #1026 Discord and Telegram were configured once, in ``settings.json``
(``discord_webhook_url``, ``telegram_bot_token``, ``telegram_chat_id``), and
sent to directly with no per-destination filtering. They are now
``alert_methods`` rows like every other destination, so this module copies
any legacy values into rows and then clears the settings fields.

It runs at startup and after every settings restore (a legacy ZIP, a YAML
restore, a DBAS ECM-settings import), because a restored ``settings.json``
can carry the legacy fields again and a restore does not restart the app.

Why not an Alembic revision: the change is data-only, and ECM's smart
bootstrap stamps forward past revisions that add no schema
(docs/database_migrations.md), so the DML would never run on an existing
install. The cleared settings fields are the idempotency marker instead.

Crash safety: rows are committed first and the settings fields cleared after.
A crash in between leaves both; the next run finds the identical row, creates
nothing, and clears the fields. Values are never logged.
"""
import json
import logging
from typing import Any, Dict, List

from credential_sentinel import credential_is_present
from notification_validation import is_valid_discord_webhook, is_valid_telegram_bot_token

logger = logging.getLogger(__name__)

LEGACY_NOTIFICATION_FIELDS = ("discord_webhook_url", "telegram_bot_token", "telegram_chat_id")

# Migrated destinations receive every severity, so nothing the shared settings
# delivered before the upgrade stops arriving. New destinations created in the
# UI use the alert-method defaults instead (info off).
_MIGRATED_SEVERITIES = {
    "notify_info": True,
    "notify_success": True,
    "notify_warning": True,
    "notify_error": True,
}


def _find_identical_row(session, method_type: str, wanted: Dict[str, str]):
    """An existing row of ``method_type`` whose config holds ``wanted``, if any.

    Disabled rows count too: an operator who already moved the webhook into
    Alert Methods and switched it off must not get a fresh enabled copy.
    """
    from models import AlertMethod

    for row in session.query(AlertMethod).filter(AlertMethod.method_type == method_type).all():
        try:
            config = json.loads(row.config) if row.config else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(config, dict) and all(config.get(k) == v for k, v in wanted.items()):
            return row
    return None


def _legacy_destinations(settings) -> tuple[List[Dict[str, Any]], List[str]]:
    """Destinations to create from ``settings``, and fields holding junk.

    Returns ``(destinations, junk_fields)``. Each destination has the row's
    ``method_type``, ``name`` and ``config``, plus ``identity``: the config
    subset that makes an existing row the same destination. A junk field
    holds a value that could never be delivered (a non-Discord host, a
    malformed token, half of the Telegram pair); it is cleared without
    creating a row.
    """
    destinations: List[Dict[str, Any]] = []
    junk: List[str] = []

    webhook = settings.discord_webhook_url
    if credential_is_present(webhook):
        if is_valid_discord_webhook(webhook):
            destinations.append({
                "method_type": "discord",
                "name": "Discord",
                # The legacy sender posted as "ECM Alerts"; keep that name.
                "config": {"webhook_url": webhook, "username": "ECM Alerts"},
                "identity": {"webhook_url": webhook},
            })
        else:
            logger.warning(
                "[NOTIFY-MIGRATE] Legacy discord_webhook_url is not an allowlisted Discord "
                "webhook; it was not migrated and will be cleared"
            )
            junk.append("discord_webhook_url")

    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if credential_is_present(token) or credential_is_present(chat_id):
        if (
            credential_is_present(token)
            and credential_is_present(chat_id)
            and is_valid_telegram_bot_token(token)
        ):
            config = {"bot_token": token, "chat_id": chat_id}
            destinations.append({
                "method_type": "telegram",
                "name": "Telegram",
                "config": config,
                "identity": config,
            })
        else:
            logger.warning(
                "[NOTIFY-MIGRATE] Legacy Telegram settings are incomplete or malformed; "
                "they were not migrated and will be cleared"
            )
            junk.extend(["telegram_bot_token", "telegram_chat_id"])

    return destinations, junk


def migrate_legacy_notification_settings(*, allow_settings_write: bool = True) -> Dict[str, Any]:
    """Copy legacy shared Discord / Telegram settings into alert-method rows.

    Args:
        allow_settings_write: Whether the legacy fields may be cleared from
            ``settings.json`` afterwards. Rows are created either way, so a
            settings file that cannot be written never costs alert delivery.

    Returns:
        ``{"created": [row ids], "existing": [row ids], "cleared": [field names]}``
    """
    from config import get_settings, save_settings
    from database import get_session
    from models import AlertMethod

    result: Dict[str, Any] = {"created": [], "existing": [], "cleared": []}
    settings = get_settings()
    present = [f for f in LEGACY_NOTIFICATION_FIELDS if getattr(settings, f, "")]
    if not present:
        return result

    destinations, junk = _legacy_destinations(settings)

    if destinations:
        session = get_session()
        try:
            for destination in destinations:
                existing = _find_identical_row(session, destination["method_type"], destination["identity"])
                if existing is not None:
                    result["existing"].append(existing.id)
                    continue
                row = AlertMethod(
                    name=destination["name"],
                    method_type=destination["method_type"],
                    enabled=True,
                    config=json.dumps(destination["config"]),
                    alert_sources=None,
                    **_MIGRATED_SEVERITIES,
                )
                session.add(row)
                session.flush()
                result["created"].append(row.id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        if result["created"]:
            from alert_methods import get_alert_manager

            manager = get_alert_manager()
            for method_id in result["created"]:
                manager.reload_method(method_id)
            logger.info(
                "[NOTIFY-MIGRATE] Moved %d legacy shared notification destination(s) into Alert Methods",
                len(result["created"]),
            )

    if not allow_settings_write:
        logger.warning(
            "[NOTIFY-MIGRATE] settings.json is not writable at startup; the legacy Discord/Telegram "
            "fields were left in place and will be cleared on a later start"
        )
        return result

    # Clear every legacy field that held anything: migrated values, junk, and
    # redaction placeholders restored from a backup (those carry no credential).
    to_clear = {f: "" for f in present}
    save_settings(settings.model_copy(update=to_clear))
    result["cleared"] = sorted(to_clear)
    if junk:
        logger.info("[NOTIFY-MIGRATE] Cleared undeliverable legacy field(s): %s", ", ".join(junk))
    return result


def migrate_after_settings_restore() -> None:
    """Run the migration after a restore wrote ``settings.json`` or ``journal.db``.

    A restore swaps ``journal.db`` underneath the alert manager and can put
    legacy fields back into ``settings.json``, and it does not restart the
    app. So migrate again, then reload every alert method from the restored
    database. Never raises: a failure is logged and the next startup retries.
    """
    try:
        migrate_legacy_notification_settings()
    except Exception as e:
        logger.warning("[NOTIFY-MIGRATE] Post-restore notification migration failed: %s", e)
    try:
        from alert_methods import get_alert_manager

        get_alert_manager().load_methods()
    except Exception as e:
        logger.warning("[NOTIFY-MIGRATE] Could not reload alert methods after restore: %s", e)
