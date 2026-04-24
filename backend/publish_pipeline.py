"""
Publish pipeline — orchestrates export generation and cloud upload.
"""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from cloud_storage.base import get_adapter
from cloud_storage.crypto import decrypt_credentials
from database import get_session
from export_manager import ExportManager
from export_models import PublishConfiguration, PublishHistory, PlaylistProfile, CloudStorageTarget
import journal

logger = logging.getLogger(__name__)

_export_manager = ExportManager()


@dataclass
class PublishResult:
    success: bool
    channels_count: int = 0
    m3u_size: int = 0
    xmltv_size: int = 0
    upload_result: Optional[dict] = None
    duration_ms: int = 0
    error: str = ""


async def execute_publish(config_id: int, dry_run: bool = False) -> PublishResult:
    """Execute the publish pipeline for a configuration.

    Args:
        config_id: ID of the PublishConfiguration to execute.
        dry_run: If True, resolve channels but don't generate files or upload.

    Returns:
        PublishResult with execution details.
    """
    start = time.time()

    # 1. Load config, profile, and target from DB
    db = get_session()
    try:
        config = db.query(PublishConfiguration).filter(PublishConfiguration.id == config_id).first()
        if not config:
            return PublishResult(success=False, error=f"Config {config_id} not found")

        profile = db.query(PlaylistProfile).filter(PlaylistProfile.id == config.profile_id).first()
        if not profile:
            return PublishResult(success=False, error=f"Profile {config.profile_id} not found")

        profile_dict = profile.to_dict()
        config_dict = config.to_dict()

        target = None
        target_creds = None
        if config.target_id:
            target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == config.target_id).first()
            if target:
                try:
                    target_creds = decrypt_credentials(target.credentials)
                except Exception as e:
                    return PublishResult(success=False, error=f"Failed to decrypt target credentials: {e}")

        # Create history entry (unless dry run)
        history = None
        if not dry_run:
            history = PublishHistory(
                config_id=config_id,
                status="running",
            )
            db.add(history)
            db.commit()
            db.refresh(history)
    finally:
        db.close()

    # 2. Dry run — just resolve channels and return counts
    if dry_run:
        try:
            from dispatcharr_client import get_client
            client = get_client()
            channels = await _export_manager._fetch_channels(client, profile_dict)
            duration_ms = int((time.time() - start) * 1000)
            return PublishResult(
                success=True,
                channels_count=len(channels),
                duration_ms=duration_ms,
            )
        except Exception as e:
            return PublishResult(success=False, error=str(e))

    # 3. Generate files
    try:
        gen_result = await _export_manager.generate(profile_dict)
    except Exception as e:
        _finalize_history(config_id, history, False, error=str(e), start=start)
        _log_publish_event(config_dict, profile_dict, False, error=str(e))
        return PublishResult(success=False, error=f"Generation failed: {e}")

    channels_count = gen_result.get("channels_count", 0)
    m3u_size = gen_result.get("m3u_size", 0)
    xmltv_size = gen_result.get("xmltv_size", 0)

    # 4. Upload to cloud target if configured
    upload_result_dict = None
    if target and target_creds:
        try:
            adapter = get_adapter(target.provider_type, target_creds)
            upload_path = (target.upload_path or "/").rstrip("/")
            prefix = profile_dict.get("filename_prefix", "playlist")
            export_dir = _export_manager.get_export_path(profile_dict["id"])

            m3u_path = export_dir / f"{prefix}.m3u"
            xmltv_path = export_dir / f"{prefix}.xml"

            upload_results = []
            if m3u_path.exists():
                r = await adapter.upload(m3u_path, f"{upload_path}/{prefix}.m3u")
                upload_results.append({"file": f"{prefix}.m3u", "success": r.success, "url": r.remote_url, "error": r.error})
            if xmltv_path.exists():
                r = await adapter.upload(xmltv_path, f"{upload_path}/{prefix}.xml")
                upload_results.append({"file": f"{prefix}.xml", "success": r.success, "url": r.remote_url, "error": r.error})

            upload_result_dict = {"uploads": upload_results}
            upload_failed = any(not u["success"] for u in upload_results)
            if upload_failed:
                error_msgs = [u["error"] for u in upload_results if not u["success"]]
                _finalize_history(config_id, history, False, channels_count=channels_count,
                                  m3u_size=m3u_size, xmltv_size=xmltv_size,
                                  error=f"Upload failed: {'; '.join(error_msgs)}", start=start,
                                  details=upload_result_dict)
                _log_publish_event(config_dict, profile_dict, False, error=f"Upload failed: {'; '.join(error_msgs)}")
                await _send_webhook(config_dict, PublishResult(
                    success=False, channels_count=channels_count, m3u_size=m3u_size,
                    xmltv_size=xmltv_size, error=f"Upload failed: {'; '.join(error_msgs)}",
                ))
                return PublishResult(
                    success=False, channels_count=channels_count,
                    m3u_size=m3u_size, xmltv_size=xmltv_size,
                    upload_result=upload_result_dict,
                    error=f"Upload failed: {'; '.join(error_msgs)}",
                )
        except ImportError as e:
            _finalize_history(config_id, history, False, channels_count=channels_count,
                              error=f"Missing dependency: {e}", start=start)
            return PublishResult(success=False, error=f"Missing dependency: {e}")
        except Exception as e:
            _finalize_history(config_id, history, False, channels_count=channels_count,
                              error=str(e), start=start)
            _log_publish_event(config_dict, profile_dict, False, error=str(e))
            return PublishResult(success=False, error=f"Upload error: {e}")

    # 5. Success
    duration_ms = int((time.time() - start) * 1000)
    _finalize_history(config_id, history, True, channels_count=channels_count,
                      m3u_size=m3u_size, xmltv_size=xmltv_size, start=start,
                      details=upload_result_dict)
    _log_publish_event(config_dict, profile_dict, True, channels_count=channels_count, duration_ms=duration_ms)

    result = PublishResult(
        success=True, channels_count=channels_count,
        m3u_size=m3u_size, xmltv_size=xmltv_size,
        upload_result=upload_result_dict, duration_ms=duration_ms,
    )
    await _send_webhook(config_dict, result)
    return result


def _finalize_history(
    config_id: int,
    history: Optional[PublishHistory],
    success: bool,
    channels_count: int = 0,
    m3u_size: int = 0,
    xmltv_size: int = 0,
    error: str = "",
    start: float = 0,
    details: Optional[dict] = None,
) -> None:
    """Update the publish history entry with results."""
    if not history:
        return
    db = get_session()
    try:
        h = db.query(PublishHistory).filter(PublishHistory.id == history.id).first()
        if h:
            h.status = "success" if success else "failed"
            h.completed_at = datetime.utcnow()
            h.channels_count = channels_count
            h.file_size_bytes = m3u_size + xmltv_size
            h.error_message = error or None
            if details:
                h.details = json.dumps(details)
            db.commit()
    except Exception as e:
        logger.warning("[PUBLISH] Failed to update history: %s", e)
        db.rollback()
    finally:
        db.close()


def _log_publish_event(
    config_dict: dict,
    profile_dict: dict,
    success: bool,
    channels_count: int = 0,
    duration_ms: int = 0,
    error: str = "",
) -> None:
    """Log a journal entry for a publish event."""
    if success:
        journal.log_entry(
            category="export",
            action_type="publish_completed",
            entity_name=config_dict.get("name", ""),
            description=f"Published '{profile_dict.get('name', '')}': {channels_count} channels in {duration_ms}ms",
            entity_id=config_dict.get("id"),
            after_value={"channels_count": channels_count, "duration_ms": duration_ms},
            user_initiated=False,
        )
    else:
        journal.log_entry(
            category="export",
            action_type="publish_failed",
            entity_name=config_dict.get("name", ""),
            description=f"Publish failed for '{profile_dict.get('name', '')}': {error}",
            entity_id=config_dict.get("id"),
            after_value={"error": error},
            user_initiated=False,
        )


async def _send_webhook(config_dict: dict, result: PublishResult) -> None:
    """Send webhook notification if configured."""
    url = config_dict.get("webhook_url")
    if not url:
        return

    import httpx
    payload = {
        "event": "publish_completed" if result.success else "publish_failed",
        "config_name": config_dict.get("name", ""),
        "success": result.success,
        "channels_count": result.channels_count,
        "m3u_size": result.m3u_size,
        "xmltv_size": result.xmltv_size,
        "duration_ms": result.duration_ms,
        "error": result.error or None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }

    for attempt in range(2):  # 1 retry
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code < 400:
                    logger.info("[PUBLISH] Webhook sent to %s (status %s)", url, resp.status_code)
                    return
                logger.warning("[PUBLISH] Webhook returned %s", resp.status_code)
        except Exception as e:
            logger.warning("[PUBLISH] Webhook attempt %s failed: %s", attempt + 1, e)
        if attempt == 0:
            await asyncio.sleep(5)

    logger.warning("[PUBLISH] Webhook failed after retries: %s", url)


# ---------------------------------------------------------------------------
# Event trigger support
# ---------------------------------------------------------------------------

VALID_EVENT_TRIGGERS = ("m3u_refresh", "channel_edit", "epg_refresh")

# Per-config debounce timers
_debounce_tasks: dict[int, asyncio.Task] = {}
DEBOUNCE_SECONDS = 5


async def fire_event(event_type: str) -> None:
    """Fire an event that may trigger publish configurations.

    Args:
        event_type: One of VALID_EVENT_TRIGGERS.
    """
    if event_type not in VALID_EVENT_TRIGGERS:
        return

    db = get_session()
    try:
        configs = db.query(PublishConfiguration).filter(
            PublishConfiguration.enabled == True,
            PublishConfiguration.schedule_type == "event",
        ).all()

        matching = []
        for config in configs:
            triggers = config.get_event_triggers()
            if event_type in triggers:
                matching.append(config.id)
    finally:
        db.close()

    for config_id in matching:
        _debounce_publish(config_id)


def _debounce_publish(config_id: int) -> None:
    """Schedule a debounced publish for a config (5-second delay)."""
    existing = _debounce_tasks.get(config_id)
    if existing and not existing.done():
        existing.cancel()

    async def _delayed_publish():
        await asyncio.sleep(DEBOUNCE_SECONDS)
        try:
            logger.info("[PUBLISH] Event-triggered publish for config %s", config_id)
            await execute_publish(config_id)
        except Exception as e:
            logger.warning("[PUBLISH] Event-triggered publish failed for config %s: %s", config_id, e)
        finally:
            _debounce_tasks.pop(config_id, None)

    _debounce_tasks[config_id] = asyncio.create_task(_delayed_publish())
