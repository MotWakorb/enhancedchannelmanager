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
async def get_alert_method_types():
    """Get available alert method types and their configuration fields."""
    logger.debug("[ALERTS] GET /types")
    try:
        types = get_method_types()
        logger.debug("[ALERTS] Found %s alert method types: %s", len(types), [t['type'] for t in types])
        return types
    except Exception as e:
        logger.exception("[ALERTS] Failed to fetch alert method types")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("")
async def list_alert_methods():
    """List all configured alert methods."""
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] GET /alert-methods")
    session = get_session()
    try:
        methods = session.query(AlertMethodModel).all()
        logger.debug("[ALERTS] Found %s alert methods in database", len(methods))
        result = []
        for m in methods:
            alert_sources = None
            if m.alert_sources:
                try:
                    alert_sources = json.loads(m.alert_sources)
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append({
                "id": m.id,
                "name": m.name,
                "method_type": m.method_type,
                "enabled": m.enabled,
                "config": json.loads(m.config) if m.config else {},
                "notify_info": m.notify_info,
                "notify_success": m.notify_success,
                "notify_warning": m.notify_warning,
                "notify_error": m.notify_error,
                "alert_sources": alert_sources,
                "last_sent_at": m.last_sent_at.isoformat() + "Z" if m.last_sent_at else None,
                "created_at": m.created_at.isoformat() + "Z" if m.created_at else None,
            })
        return result
    except Exception as e:
        logger.exception("[ALERTS] Failed to list alert methods")
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.post("")
async def create_alert_method(data: AlertMethodCreate):
    """Create a new alert method."""
    from models import AlertMethod as AlertMethodModel

    logger.debug("[ALERTS] POST /alert-methods - name=%s type=%s", data.name, data.method_type)

    session = None
    try:
        # Validate method type
        method_types = {mt["type"] for mt in get_method_types()}
        if data.method_type not in method_types:
            logger.warning("[ALERTS] Unknown method type attempted: %s", data.method_type)
            raise HTTPException(status_code=400, detail=f"Unknown method type: {data.method_type}")

        # Validate config
        method = create_method(data.method_type, 0, data.name, data.config)
        if method:
            is_valid, error = method.validate_config(data.config)
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
            config=json.dumps(data.config),
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
async def get_alert_method(method_id: int):
    """Get a specific alert method."""
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
        alert_sources = None
        if method.alert_sources:
            try:
                alert_sources = json.loads(method.alert_sources)
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "id": method.id,
            "name": method.name,
            "method_type": method.method_type,
            "enabled": method.enabled,
            "config": json.loads(method.config) if method.config else {},
            "notify_info": method.notify_info,
            "notify_success": method.notify_success,
            "notify_warning": method.notify_warning,
            "notify_error": method.notify_error,
            "alert_sources": alert_sources,
            "last_sent_at": method.last_sent_at.isoformat() + "Z" if method.last_sent_at else None,
            "created_at": method.created_at.isoformat() + "Z" if method.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[ALERTS] Failed to get alert method %s", method_id)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.patch("/{method_id}")
async def update_alert_method(method_id: int, data: AlertMethodUpdate):
    """Update an alert method."""
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
            # Validate new config
            method_instance = create_method(method.method_type, method.id, method.name, data.config)
            if method_instance:
                is_valid, error = method_instance.validate_config(data.config)
                if not is_valid:
                    logger.warning("[ALERTS] Invalid config for method %s: %s", method_id, error)
                    raise HTTPException(status_code=400, detail=error)
            method.config = json.dumps(data.config)
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
async def delete_alert_method(method_id: int):
    """Delete an alert method."""
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
async def test_alert_method(method_id: int):
    """Test an alert method by sending a test message."""
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
