"""
Alert methods router — alert method CRUD and testing endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from alert_methods import get_alert_manager, get_method_types, create_method
from auth import (
    RequireAdminIfEnabled,
    RequireHumanAdminForNotificationCredential,
    RequireHumanAdminForOutboundTest,
)
from database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alert-methods", tags=["Alert Methods"])


# Request models
class AlertMethodCreate(BaseModel):
    name: str
    method_type: str
    config: dict
    enabled: bool = True
    notify_info: bool = False
    notify_success: bool = True
    notify_warning: bool = True
    notify_error: bool = True
    alert_sources: Optional[dict] = None  # Granular source filtering


class AlertMethodUpdate(BaseModel):
    name: Optional[str] = None
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    notify_info: Optional[bool] = None
    notify_success: Optional[bool] = None
    notify_warning: Optional[bool] = None
    notify_error: Optional[bool] = None
    alert_sources: Optional[dict] = None  # Granular source filtering


def _normalize_smtp_config_for_persist(method_type: str, config: dict) -> dict:
    """Canonicalize SMTP `to_emails` to `list[str]` before persistence (bd-9vz32).

    The frontend has historically sent `to_emails` as either a comma-joined
    string or a list. We persist as JSON via `json.dumps`, so the shape
    round-trips unchanged — which means callers reading back have to
    disambiguate. Decision (bd-9vz32): canonicalize on write to `list[str]`
    so reads are typed and parse-free. Legacy string rows still load via
    the SMTPMethod coerce helper, so this is a write-strict / read-tolerant
    pattern (no Alembic migration needed for the JSON-blob field).
    """
    if method_type != "smtp" or not isinstance(config, dict):
        return config
    raw = config.get("to_emails")
    if isinstance(raw, str):
        normalized = [token.strip() for token in raw.split(",") if token.strip()]
        # Return a shallow copy — config came from Pydantic and shouldn't be
        # mutated for the caller.
        config = {**config, "to_emails": normalized}
    elif isinstance(raw, list):
        # Re-strip and drop empties so list-shape input also lands cleanly.
        config = {
            **config,
            "to_emails": [str(item).strip() for item in raw if str(item).strip()],
        }
    return config


def validate_alert_sources(alert_sources: Optional[dict]) -> Optional[str]:
    """Validate alert_sources structure. Returns error message or None if valid."""
    if alert_sources is None:
        return None

    valid_filter_modes = {"all", "only_selected", "all_except"}

    # Validate EPG refresh section
    if "epg_refresh" in alert_sources:
        epg = alert_sources["epg_refresh"]
        if not isinstance(epg, dict):
            return "epg_refresh must be an object"
        if "filter_mode" in epg and epg["filter_mode"] not in valid_filter_modes:
            return f"epg_refresh.filter_mode must be one of: {valid_filter_modes}"
        if "source_ids" in epg and not isinstance(epg["source_ids"], list):
            return "epg_refresh.source_ids must be an array"

    # Validate M3U refresh section
    if "m3u_refresh" in alert_sources:
        m3u = alert_sources["m3u_refresh"]
        if not isinstance(m3u, dict):
            return "m3u_refresh must be an object"
        if "filter_mode" in m3u and m3u["filter_mode"] not in valid_filter_modes:
            return f"m3u_refresh.filter_mode must be one of: {valid_filter_modes}"
        if "account_ids" in m3u and not isinstance(m3u["account_ids"], list):
            return "m3u_refresh.account_ids must be an array"

    # Validate probe failures section
    if "probe_failures" in alert_sources:
        probe = alert_sources["probe_failures"]
        if not isinstance(probe, dict):
            return "probe_failures must be an object"
        if "min_failures" in probe:
            min_failures = probe["min_failures"]
            if not isinstance(min_failures, int) or min_failures < 0:
                return "probe_failures.min_failures must be a non-negative integer"

    return None


@router.get("/types")
async def get_alert_method_types(_admin=RequireAdminIfEnabled):
    """Get available alert method types and their configuration fields. Admin only.

    bead 9kwzp.10 item 4: takes the PLAIN admin tier on the strongest grounds
    in this router. It returns a static catalogue of the method types this
    build supports and their field descriptors — no install data, no stored
    value, nothing per-operator — so admitting the MCP service principal
    discloses nothing at all.

    :func:`list_alert_methods` is on the same tier for a different and weaker
    reason (a shipped MCP tool needs it, and it does disclose credentials);
    the four remaining non-test routes are human-admin, see
    :func:`create_alert_method`.
    """
    logger.debug("[ALERTS] GET /types")
    try:
        types = get_method_types()
        logger.debug("[ALERTS] Found %s alert method types: %s", len(types), [t['type'] for t in types])
        return types
    except Exception as e:
        logger.exception("[ALERTS] Failed to fetch alert method types")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("")
async def list_alert_methods(_admin=RequireAdminIfEnabled):
    """List all configured alert methods. Admin only, MCP principal ADMITTED.

    bead 9kwzp.10 item 4. This router carried NO route dependency on any of its
    six non-test routes, so every one of them was reachable by any
    authenticated non-admin and by the static MCP service principal. The
    non-admin half is closed here and on every sibling below.

    THE MCP HALF IS A DELIBERATE ADMISSION, AND THE RESPONSE IS MASKED. The
    shipped MCP tool ``list_alert_methods`` is the operator's inventory of
    their own alert methods, and refusing it removed a capability the sidecar
    was built to provide, so the principal stays admitted. What used to make
    that admission expensive was the RESPONSE, not the gate: this handler
    hand-rolled its dict and emitted ``AlertMethod.config`` VERBATIM, and that
    blob is where the Discord webhook URL, the Telegram bot token and the SMTP
    password live — the same families bead 9ej7f withheld from this principal
    on GET /api/settings, handed out in clear through a second table.

    Bead enhancedchannelmanager-9kwzp.13 closed that at the response. This
    handler now serializes through
    ``models.AlertMethod.to_dict(include_sensitive=False)``, which substitutes
    ``'********'`` for ``password``, ``bot_token``, ``webhook_url`` and
    ``api_key``, so an admitted caller — human admin or automation credential
    — receives the inventory without any credential VALUE. No caller is
    permitted ``include_sensitive=True`` over HTTP; there is no route, query
    parameter or header that reaches it, and adding one would reopen exactly
    this hole.

    DO NOT REINTRODUCE A HAND-ROLLED RESPONSE DICT HERE. ``to_dict`` is the
    single masking implementation this route shares with
    :func:`get_alert_method`, and ``routers/backup.py`` keeps its DBAS
    redaction denylist in lock-step with the same key set; a second
    implementation is how those two drift apart.
    ``tests/routers/test_9kwzp13_alert_method_masking.py`` pins that neither
    read route emits a raw credential value.

    The five sibling routes below keep
    ``RequireHumanAdminForNotificationCredential``: no MCP tool calls any of
    them, so denying the principal there costs nothing and holds the line on
    the write half and on the single-method read.
    """
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] GET /alert-methods")
    session = get_session()
    try:
        methods = session.query(AlertMethodModel).all()
        logger.debug("[ALERTS] Found %s alert methods in database", len(methods))
        return [m.to_dict(include_sensitive=False) for m in methods]
    except Exception as e:
        logger.exception("[ALERTS] Failed to list alert methods")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.post("")
async def create_alert_method(
    data: AlertMethodCreate,
    _admin=RequireHumanAdminForNotificationCredential,
):
    """Create a new alert method. Human-admin only.

    bead 9kwzp.10 item 4, and the canonical statement of the verdict the four
    ``RequireHumanAdminForNotificationCredential`` routes in this router share.

    This route writes the notification credentials ECM later sends under — the
    Discord webhook URL, the Telegram bot token, the SMTP password that live in
    ``AlertMethod.config`` — so it can point the operator's own alerts at a
    destination the caller names. That is the shape bead kgz3k denies the MCP
    service principal on ``POST /api/settings``, so the principal is denied
    here too.

    The denial costs the sidecar nothing: NO shipped MCP tool calls this route,
    or :func:`get_alert_method`, :func:`update_alert_method` or
    :func:`delete_alert_method`. The one alert-method tool that exists,
    ``list_alert_methods``, calls :func:`list_alert_methods`, which is on the
    plain admin tier for that reason and carries a disclosure residual recorded
    in its own docstring.
    """
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] POST /alert-methods - name=%s type=%s", data.name, data.method_type)

    session = None
    try:
        # Validate method type
        method_types = {mt["type"] for mt in get_method_types()}
        if data.method_type not in method_types:
            logger.warning("[ALERTS] Unknown method type attempted: %s", data.method_type)
            raise HTTPException(status_code=400, detail=f"Unknown method type: {data.method_type}")

        # Canonicalize SMTP to_emails to list[str] before validation/persistence (bd-9vz32).
        # Legacy comma-joined string input is normalized at the API boundary so all
        # downstream readers see a single shape.
        config = _normalize_smtp_config_for_persist(data.method_type, data.config)

        # Validate config
        method = create_method(data.method_type, 0, data.name, config)
        if method:
            is_valid, error = method.validate_config(config)
            if not is_valid:
                logger.warning("[ALERTS] Invalid config for method %s: %s", data.name, error)
                raise HTTPException(status_code=400, detail=error)

        # Validate alert_sources if provided
        if data.alert_sources is not None:
            alert_sources_error = validate_alert_sources(data.alert_sources)
            if alert_sources_error:
                logger.warning("[ALERTS] Invalid alert_sources for method %s: %s", data.name, alert_sources_error)
                raise HTTPException(status_code=400, detail=alert_sources_error)

        session = get_session()
        method_model = AlertMethodModel(
            name=data.name,
            method_type=data.method_type,
            config=json.dumps(config),
            enabled=data.enabled,
            notify_info=data.notify_info,
            notify_success=data.notify_success,
            notify_warning=data.notify_warning,
            notify_error=data.notify_error,
            alert_sources=json.dumps(data.alert_sources) if data.alert_sources else None,
        )
        session.add(method_model)
        session.commit()
        session.refresh(method_model)

        # Reload the manager to pick up the new method
        get_alert_manager().reload_method(method_model.id)

        logger.info("[ALERTS] Created alert method id=%s name=%s type=%s", method_model.id, method_model.name, method_model.method_type)
        return {
            "id": method_model.id,
            "name": method_model.name,
            "method_type": method_model.method_type,
            "enabled": method_model.enabled,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ALERTS] Failed to create alert method")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        if session:
            session.close()


@router.get("/{method_id}")
async def get_alert_method(
    method_id: int,
    _admin=RequireHumanAdminForNotificationCredential,
):
    """Get a specific alert method. Human-admin only.

    bead 9kwzp.10 item 4: see :func:`create_alert_method` for the group
    verdict on the gate.

    Note honestly what this gate does and does not buy. :func:`list_alert_methods`
    is on the plain admin tier and returns exactly these fields for EVERY
    method, so against the MCP service principal this route discloses nothing
    the list does not already. The gate here is held because no MCP tool needs
    the route, not because it is containing a disclosure.

    What contains the disclosure is bead
    enhancedchannelmanager-9kwzp.13, which landed: this handler serializes
    through ``models.AlertMethod.to_dict(include_sensitive=False)`` instead of
    hand-rolling a dict that emitted ``config`` verbatim, so the webhook URL,
    the bot token and the SMTP password come back as ``'********'`` for every
    caller. No caller receives ``include_sensitive=True`` over HTTP. See
    :func:`list_alert_methods` for why the hand-rolled dict must not come back.
    """
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] GET /alert-methods/%s", method_id)
    session = get_session()
    try:
        method = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method:
            logger.debug("[ALERTS] Alert method not found: id=%s", method_id)
            raise HTTPException(status_code=404, detail="Alert method not found")

        logger.debug("[ALERTS] Found alert method: id=%s name=%s", method.id, method.name)
        return method.to_dict(include_sensitive=False)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ALERTS] Failed to get alert method %s", method_id)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.patch("/{method_id}")
async def update_alert_method(
    method_id: int,
    data: AlertMethodUpdate,
    _admin=RequireHumanAdminForNotificationCredential,
):
    """Update an alert method. Human-admin only.

    bead 9kwzp.10 item 4: can replace the stored notification credentials, so
    it can repoint where ECM sends alerts. See :func:`create_alert_method`.
    """
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] PATCH /alert-methods/%s", method_id)
    session = get_session()
    try:
        method = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method:
            logger.debug("[ALERTS] Alert method not found for update: id=%s", method_id)
            raise HTTPException(status_code=404, detail="Alert method not found")

        if data.name is not None:
            method.name = data.name
        if data.config is not None:
            # Canonicalize SMTP to_emails to list[str] before validation/persistence (bd-9vz32).
            new_config = _normalize_smtp_config_for_persist(method.method_type, data.config)
            # Validate new config
            method_instance = create_method(method.method_type, method.id, method.name, new_config)
            if method_instance:
                is_valid, error = method_instance.validate_config(new_config)
                if not is_valid:
                    logger.warning("[ALERTS] Invalid config for method %s: %s", method_id, error)
                    raise HTTPException(status_code=400, detail=error)
            method.config = json.dumps(new_config)
        if data.enabled is not None:
            method.enabled = data.enabled
        if data.notify_info is not None:
            method.notify_info = data.notify_info
        if data.notify_success is not None:
            method.notify_success = data.notify_success
        if data.notify_warning is not None:
            method.notify_warning = data.notify_warning
        if data.notify_error is not None:
            method.notify_error = data.notify_error
        if data.alert_sources is not None:
            # Validate alert_sources
            alert_sources_error = validate_alert_sources(data.alert_sources)
            if alert_sources_error:
                logger.warning("[ALERTS] Invalid alert_sources for method %s: %s", method_id, alert_sources_error)
                raise HTTPException(status_code=400, detail=alert_sources_error)
            method.alert_sources = json.dumps(data.alert_sources) if data.alert_sources else None

        session.commit()

        # Reload the manager to pick up the changes
        get_alert_manager().reload_method(method_id)

        logger.info("[ALERTS] Updated alert method id=%s name=%s", method_id, method.name)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ALERTS] Failed to update alert method %s", method_id)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.delete("/{method_id}")
async def delete_alert_method(
    method_id: int,
    _admin=RequireHumanAdminForNotificationCredential,
):
    """Delete an alert method. Human-admin only.

    bead 9kwzp.10 item 4: silently ends the operator's alert delivery, which
    is the availability half of the same control. See
    :func:`create_alert_method`.
    """
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] DELETE /alert-methods/%s", method_id)
    session = get_session()
    try:
        method = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method:
            logger.debug("[ALERTS] Alert method not found for deletion: id=%s", method_id)
            raise HTTPException(status_code=404, detail="Alert method not found")

        method_name = method.name
        session.delete(method)
        session.commit()

        # Remove from manager
        get_alert_manager().reload_method(method_id)

        logger.info("[ALERTS] Deleted alert method id=%s name=%s", method_id, method_name)
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ALERTS] Failed to delete alert method %s", method_id)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.post("/{method_id}/test")
async def test_alert_method(
    method_id: int,
    _admin=RequireHumanAdminForOutboundTest,
):
    """Test an alert method by sending a test message.

    bead 9kwzp.6: admin-gated, and the static MCP service principal is
    refused. This endpoint carried NO route dependency, so any authenticated
    caller could drive it. It sends with the method's STORED credentials (the
    Discord webhook URL, the Telegram bot token, the SMTP password held in
    ``AlertMethod.config``) — the caller never has to know them, which is the
    same class as ``/api/settings/test-smtp`` that bead i4qrp closed, and it
    reports the upstream verdict back.

    ``RequireAdminIfEnabled`` would NOT do here: the MCP principal carries
    ``is_admin=True`` (``auth.dependencies._build_mcp_service_principal``), so
    the plain admin gate would close the non-admin half and leave the MCP half
    open. The gate no-ops when ``require_auth`` is False or setup is
    incomplete, so first-run configuration is untouched.
    """
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] POST /alert-methods/%s/test", method_id)
    session = get_session()
    try:
        method_model = session.query(AlertMethodModel).filter(
            AlertMethodModel.id == method_id
        ).first()

        if not method_model:
            logger.debug("[ALERTS] Alert method not found for test: id=%s", method_id)
            raise HTTPException(status_code=404, detail="Alert method not found")

        config = json.loads(method_model.config) if method_model.config else {}
        method = create_method(
            method_model.method_type,
            method_model.id,
            method_model.name,
            config
        )

        if not method:
            logger.warning("[ALERTS] Unknown method type for test: %s", method_model.method_type)
            raise HTTPException(status_code=400, detail=f"Unknown method type: {method_model.method_type}")

        logger.debug("[ALERTS] Sending test message to method: %s (%s)", method_model.name, method_model.method_type)
        success, message = await method.test_connection()
        logger.info("[ALERTS] Test result for method %s: success=%s message=%s", method_model.name, success, message)
        return {"success": success, "message": message}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ALERTS] Failed to test alert method %s", method_id)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()
