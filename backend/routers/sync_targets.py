"""Sync Targets router — CRUD for cross-instance live-sync destinations.

A SyncTarget is a remote Dispatcharr-B instance ECM can push config to
(epic i39wu, bead vigbu). This router mirrors the CloudStorageTarget CRUD in
``routers/cloud_targets.py`` exactly:

* credentials are Fernet-encrypted at rest via ``cloud_storage.crypto`` and are
  NEVER returned decrypted — every response masks them (last-4 only);
* ``credential_version`` bumps same-txn on a credentials write via the ORM
  before_update listener on the model (``export_models.py``) — a rename or
  enable-toggle does NOT bump it;
* every write is admin-gated (``auth.RequireAdminIfEnabled``).

base_url validation
-------------------
On create/update the ``base_url`` must be a well-formed http(s) URL; other
schemes / unparseable values are rejected at write time (see
``_validate_base_url``). This is a config-time best-effort check ONLY.

The AUTHORITATIVE SSRF defence — resolve-by-IP against the always-on denylist
+ active mode, with DNS-rebinding mitigation — runs at sync EXECUTE time via
``security/ssrf.py`` ``validate_outbound_url`` (bead 1t3al / the sync engine),
NOT here. Config-time scheme/format validation alone is insufficient against
DNS rebinding (the host can resolve to an internal IP between config and
execute), which is exactly why the execute-time gate re-resolves and connects
by the validated IP. We deliberately do NOT call the (synchronous, blocking,
fail-closed) ``validate_outbound_url`` at write time: it would reject a
legitimately-configured target whose host is merely unresolvable at the moment
of saving, and DNS done here proves nothing about DNS at execute time.
"""
import logging
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from auth import RequireAdminIfEnabled
from cloud_storage.crypto import encrypt_credentials, decrypt_credentials
from database import get_session
from export_models import SyncTarget
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync-targets", tags=["Sync Targets"])

_ALLOWED_SCHEMES = ("http", "https")


# ---------------------------------------------------------------------------
# base_url validation (config-time, best-effort — see module docstring)
# ---------------------------------------------------------------------------

def _validate_base_url(value: str) -> str:
    """Reject anything that is not a well-formed http(s) URL with a host.

    Config-time best-effort only — the authoritative resolve-by-IP SSRF gate
    runs at sync EXECUTE time (security/ssrf.py, bead 1t3al). See module
    docstring for why we do not resolve DNS here.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("base_url is required")
    candidate = value.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise ValueError(f"base_url is not a valid URL: {exc}") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"base_url scheme '{scheme or '(none)'}' is not allowed "
            "(only http/https permitted)"
        )
    if not parts.hostname:
        raise ValueError("base_url has no host")
    # Surface a malformed port early (urlsplit defers the ValueError to .port).
    try:
        _ = parts.port
    except ValueError as exc:
        raise ValueError(f"base_url has an invalid port: {exc}") from exc
    return candidate


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class SyncTargetCreate(BaseModel):
    name: str
    base_url: str
    # Write-only: encrypted at rest, never echoed back decrypted.
    credentials: dict = {}
    enabled: bool = True
    insecure: bool = False
    fuzzy_stream_matching: bool = False
    # Opt-in logo replication (bead 7ipq2.1) — default OFF (ADR-013 S9).
    sync_logos: bool = False

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v):
        return _validate_base_url(v)


class SyncTargetUpdate(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    credentials: Optional[dict] = None
    enabled: Optional[bool] = None
    insecure: Optional[bool] = None
    fuzzy_stream_matching: Optional[bool] = None
    sync_logos: Optional[bool] = None

    @field_validator("base_url")
    @classmethod
    def _check_base_url(cls, v):
        if v is None:
            return v
        return _validate_base_url(v)


class SyncTargetResponse(BaseModel):
    """Read shape — credentials are always masked (last-4 only), never plaintext."""
    id: int
    name: str
    base_url: str
    credentials: dict
    enabled: bool
    insecure: bool
    credential_version: int
    token_revoked_at: Optional[str] = None
    last_full_sync_at: Optional[str] = None
    last_outcome: Optional[str] = None
    last_source_fingerprint: Optional[str] = None
    fuzzy_stream_matching: bool
    sync_logos: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _mask_credentials(creds: dict) -> dict:
    """Mask sensitive credential values, showing only last 4 chars.

    Mirrors ``routers/cloud_targets.py._mask_credentials`` — kept local so the
    sync router has no import dependency on the cloud-target router module.
    """
    masked: dict = {}
    for key, value in creds.items():
        if isinstance(value, str) and len(value) > 8:
            masked[key] = "***" + value[-4:]
        elif isinstance(value, str):
            masked[key] = "***"
        elif isinstance(value, dict):
            masked[key] = _mask_credentials(value)
        else:
            masked[key] = value
    return masked


def _serialize(target: SyncTarget, plaintext_creds: Optional[dict] = None) -> dict:
    """Build a response dict with masked credentials.

    ``plaintext_creds`` (the just-written dict) is masked directly when present;
    otherwise the stored ciphertext is decrypted and masked. Decryption is
    fail-soft (FERNET_KEY rotation) — falls back to the empty masked placeholder.
    """
    data = target.to_dict(mask_credentials=True)
    if plaintext_creds is not None:
        data["credentials"] = _mask_credentials(plaintext_creds)
    else:
        decrypted = decrypt_credentials(target.credentials)
        if decrypted is None:
            data["credentials"] = {"error": "Could not decrypt"}
        else:
            data["credentials"] = _mask_credentials(decrypted)
    return data


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_sync_target(req: SyncTargetCreate, _admin=RequireAdminIfEnabled):
    """Create a new sync target with encrypted credentials."""
    db = get_session()
    try:
        existing = db.query(SyncTarget).filter(SyncTarget.name == req.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Sync target with name '{req.name}' already exists")

        target = SyncTarget(
            name=req.name,
            base_url=req.base_url,
            credentials=encrypt_credentials(req.credentials),
            enabled=req.enabled,
            insecure=req.insecure,
            fuzzy_stream_matching=req.fuzzy_stream_matching,
            sync_logos=req.sync_logos,
        )
        db.add(target)
        db.commit()
        db.refresh(target)

        journal.log_entry(
            category="sync",
            action_type="create",
            entity_name=target.name,
            description=f"Created sync target '{target.name}'",
            entity_id=target.id,
        )
        logger.info("[SYNC] Created sync target id=%s name=%s", target.id, target.name)
        return _serialize(target, plaintext_creds=req.credentials)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[SYNC] Failed to create sync target: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.get("")
async def list_sync_targets(_admin=RequireAdminIfEnabled):
    """List all sync targets with masked credentials."""
    db = get_session()
    try:
        targets = db.query(SyncTarget).order_by(SyncTarget.name).all()
        return [_serialize(t) for t in targets]
    finally:
        db.close()


@router.get("/{target_id}")
async def get_sync_target(target_id: int, _admin=RequireAdminIfEnabled):
    """Get a single sync target with masked credentials."""
    db = get_session()
    try:
        target = db.query(SyncTarget).filter(SyncTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Sync target not found")
        return _serialize(target)
    finally:
        db.close()


@router.put("/{target_id}")
async def update_sync_target(target_id: int, req: SyncTargetUpdate, _admin=RequireAdminIfEnabled):
    """Update a sync target. Credentials are re-encrypted only if provided.

    The credential_version bumps (same-txn, via the ORM listener) ONLY when
    ``credentials`` is actually written — a rename or enable-toggle does not.
    """
    db = get_session()
    try:
        target = db.query(SyncTarget).filter(SyncTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Sync target not found")

        if req.name is not None and req.name != target.name:
            clash = db.query(SyncTarget).filter(
                SyncTarget.name == req.name, SyncTarget.id != target_id
            ).first()
            if clash:
                raise HTTPException(status_code=409, detail=f"Sync target with name '{req.name}' already exists")
            target.name = req.name

        if req.base_url is not None:
            target.base_url = req.base_url
        if req.enabled is not None:
            target.enabled = req.enabled
        if req.insecure is not None:
            target.insecure = req.insecure
        if req.fuzzy_stream_matching is not None:
            target.fuzzy_stream_matching = req.fuzzy_stream_matching
        if req.sync_logos is not None:
            target.sync_logos = req.sync_logos
        if req.credentials is not None:
            target.credentials = encrypt_credentials(req.credentials)

        db.commit()
        db.refresh(target)

        journal.log_entry(
            category="sync",
            action_type="update",
            entity_name=target.name,
            description=f"Updated sync target '{target.name}'",
            entity_id=target.id,
        )
        logger.info("[SYNC] Updated sync target id=%s", target_id)
        # Mask the just-written plaintext when credentials were supplied; else
        # decrypt-and-mask the stored ciphertext.
        return _serialize(target, plaintext_creds=req.credentials if req.credentials is not None else None)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[SYNC] Failed to update sync target %s: %s", target_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/{target_id}", status_code=204)
async def delete_sync_target(target_id: int, _admin=RequireAdminIfEnabled):
    """Delete a sync target."""
    db = get_session()
    try:
        target = db.query(SyncTarget).filter(SyncTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Sync target not found")

        name = target.name
        db.delete(target)
        db.commit()

        journal.log_entry(
            category="sync",
            action_type="delete",
            entity_name=name,
            description=f"Deleted sync target '{name}'",
            entity_id=target_id,
        )
        logger.info("[SYNC] Deleted sync target id=%s name=%s", target_id, name)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[SYNC] Failed to delete sync target %s: %s", target_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
