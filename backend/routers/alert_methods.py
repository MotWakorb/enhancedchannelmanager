"""
Alert methods router — alert method CRUD and testing endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from alert_methods import (
    ALERT_CATEGORIES,
    ENTITY_FILTER_ALERT_CATEGORIES,
    create_method,
    get_alert_manager,
    get_method_types,
)
from auth import (
    RequireAdminIfEnabled,
    RequireHumanAdminForNotificationCredential,
    RequireHumanAdminForOutboundTest,
)
from auth.dependencies import require_admin_if_enabled
from database import get_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/alert-methods", tags=["Alert Methods"])

_require_ntfy_write_admin = require_admin_if_enabled(
    reject_mcp_service_principal=True,
    enforce_when_auth_disabled=True,
    mcp_denial_detail=(
        "The MCP service principal cannot write ntfy alert destinations. "
        "Persisting an outbound destination must be driven by a human operator admin."
    ),
)


async def _enforce_ntfy_write_admin(request: Request, method_type: str) -> None:
    if method_type != "ntfy":
        return
    session = get_session()
    try:
        await _require_ntfy_write_admin(request=request, session=session)
    finally:
        session.close()


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


# PATCH keeps the stored value of these config keys when the request leaves
# them blank or echoes the '********' mask back (GH #1026), so the edit form
# can show a destination without ever holding its secret. ntfy is deliberately
# absent: keeping its access token across a server_url change would send the
# token to a server the operator never gave it to (credential harvesting), so
# an ntfy config update must always carry the token again.
_PRESERVED_ON_UPDATE: dict[str, tuple[str, ...]] = {
    "discord": ("webhook_url",),
    "telegram": ("bot_token", "chat_id"),
}
_MASKED_CONFIG_VALUE = "********"


def _preserve_stored_secrets(method_type: str, stored_config: dict, new_config: dict) -> dict:
    """Fill blank or masked secrets in ``new_config`` from ``stored_config``."""
    preserved_keys = _PRESERVED_ON_UPDATE.get(method_type, ())
    if not preserved_keys:
        return new_config
    merged = dict(new_config)
    for key in preserved_keys:
        incoming = merged.get(key)
        if (incoming is None or incoming == "" or incoming == _MASKED_CONFIG_VALUE) and stored_config.get(key):
            merged[key] = stored_config[key]
    return merged


def validate_alert_sources(alert_sources: Optional[dict]) -> Optional[str]:
    """Validate alert_sources structure. Returns error message or None if valid."""
    if alert_sources is None:
        return None

    for category in sorted(ALERT_CATEGORIES):
        if category not in alert_sources:
            continue
        section = alert_sources[category]
        if not isinstance(section, dict):
            return f"{category} must be an object"
        if "enabled" in section and not isinstance(section["enabled"], bool):
            return f"{category}.enabled must be a boolean"

    # EPG source / M3U account sections (every section is a dict by now)
    valid_filter_modes = {"all", "only_selected", "all_except"}
    for category, ids_key in ENTITY_FILTER_ALERT_CATEGORIES.items():
        section = alert_sources.get(category)
        if section is None:
            continue
        if "filter_mode" in section and section["filter_mode"] not in valid_filter_modes:
            return f"{category}.filter_mode must be one of: {valid_filter_modes}"
        if ids_key in section and not isinstance(section[ids_key], list):
            return f"{category}.{ids_key} must be an array"

    # Probe failures section
    probe = alert_sources.get("probe_failures")
    if probe is not None and "min_failures" in probe:
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
    request: Request,
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
        await _enforce_ntfy_write_admin(request, data.method_type)
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
    request: Request,
    data: AlertMethodUpdate,
    _admin=RequireHumanAdminForNotificationCredential,
):
    """Update an alert method. Human-admin only.

    bead 9kwzp.10 item 4: can replace the stored notification credentials, so
    it can repoint where ECM sends alerts. See :func:`create_alert_method`.

    ``config`` replaces the stored config, except that a blank or masked
    Discord ``webhook_url`` or Telegram ``bot_token`` / ``chat_id`` keeps the
    stored value (:data:`_PRESERVED_ON_UPDATE`). Omitting ``config`` leaves it
    untouched, which is how severity / filter edits are saved.
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

        await _enforce_ntfy_write_admin(request, method.method_type)

        if data.name is not None:
            method.name = data.name
        if data.config is not None:
            stored_config = json.loads(method.config) if method.config else {}
            new_config = _preserve_stored_secrets(method.method_type, stored_config, data.config)
            # Canonicalize SMTP to_emails to list[str] before validation/persistence (bd-9vz32).
            new_config = _normalize_smtp_config_for_persist(method.method_type, new_config)
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
