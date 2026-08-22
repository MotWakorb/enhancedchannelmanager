"""Sync Targets router — CRUD for cross-instance live-sync destinations.

A SyncTarget is a remote Dispatcharr-B instance ECM can push config to
(epic i39wu, bead vigbu). This router mirrors the CloudStorageTarget CRUD in
``routers/cloud_targets.py`` exactly:

* credentials are Fernet-encrypted at rest via ``cloud_storage.crypto`` and are
  NEVER returned decrypted — every response masks them (last-4 only);
* ``credential_version`` bumps same-txn on a credentials write via the ORM
  before_update listener on the model (``export_models.py``) — a rename or
  enable-toggle does NOT bump it;
* every route is admin-gated (``auth.RequireAdminIfEnabled``), reads and writes
  alike, and that gate ADMITS the static MCP service principal — see
  :func:`create_sync_target` for why (bead 9kwzp.10).

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

from auth import RequireAdminIfEnabled, ResolveIsMcpServicePrincipalIfEnabled
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
    # One-time credential provisioning gate state (ADR-013 S10-S13). Timestamps,
    # never credentials — see export_models.SyncTarget.
    credentials_provisioned_at: Optional[str] = None
    destination_credentials_observed_at: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class SyncTargetProvisionRequest(BaseModel):
    """Body for the provisioning action (ADR-013 S10).

    Under the ratified HARVEST input model there is nothing to supply: A reads
    its own current provider values. The one exception is the Schedules Direct
    password, which Dispatcharr marks write-only and never returns, so it does
    not exist anywhere on A for a harvest to read.

    ``schedules_direct_password`` is REQUEST-SCOPED — applied to this run's
    Schedules Direct EPG sources and discarded with the request. It is never
    persisted on this instance, never carried on a cycle, and appears in the
    audit row by FIELD NAME only (INV-3 / S13). Omitting it is fully supported:
    the run then STATES, per source, that the password could not cross.
    """

    schedules_direct_password: Optional[str] = None


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
# Per-target sync task lifecycle hooks (7ipq2.3 / ADR-013 S6)
# ---------------------------------------------------------------------------


def _ensure_sync_task_best_effort(target_id: int, target_name: str) -> None:
    """Register/refresh this target's ``dbas_sync_<id>`` task, best-effort.

    Task registration must never fail the CRUD write that already committed —
    on failure the target simply has no schedulable task until the next
    startup reconcile (``register_sync_target_tasks``), and we log loudly.
    Lazy import: routers load before the tasks package during startup.
    """
    try:
        from tasks.dbas_sync import ensure_sync_target_task

        ensure_sync_target_task(target_id, target_name)
    except Exception as e:
        logger.warning(
            "[SYNC] Failed to register sync task for target %s: %s", target_id, e
        )


def _remove_sync_task_best_effort(target_id: int) -> None:
    """Unregister a deleted target's task + prune its schedule rows, best-effort."""
    try:
        from tasks.dbas_sync import remove_sync_target_task

        remove_sync_target_task(target_id)
    except Exception as e:
        logger.warning(
            "[SYNC] Failed to remove sync task for target %s: %s", target_id, e
        )


# ---------------------------------------------------------------------------
# One-time credential provisioning (ADR-013 S10-S13, bead wd20y)
# ---------------------------------------------------------------------------
#
# THE WRITER IS NOT IN THIS FILE, DELIBERATELY. It lives at
# ``backend/tasks/dbas_sync_provisioning.py`` because the SSRF chokepoint guard
# (``tests/test_ssrf_chokepoint_guard.py``) scans ``cloud_storage/*.py`` and the
# literal glob ``dbas_sync*.py`` under ``backend/tasks/`` — ``routers/`` is NOT
# scanned. A provisioning writer placed here, the natural home, would put the
# one outbound path that carries a provider credential outside the guard
# entirely (threat model §11.5.4 item 2 — a build gate, decided before the code
# was placed rather than discovered afterwards).
#
# The imports below are LAZY, per the same rule the task-registration hooks
# above follow: routers load before the tasks package during startup.


def _guard_insecure_write(target: SyncTarget, requested_insecure: Optional[bool]) -> None:
    """Refuse ``insecure=true`` on a target that holds a credential on B (S11).

    ONE PREDICATE, AT THE SERVICE LAYER, so REST and MCP are both covered — the
    MCP ``update_sync_target`` tool calls this same route, and a guard in the
    frontend form would cover neither surface. ``insecure`` has in fact been
    editable on ``PUT /api/sync-targets/{id}`` since the router's first commit
    (``ed98f32f``), so S7's "forbidden-by-construction" is a construction this
    BUILDS rather than one it restores.

    Symmetric with :func:`provision_credentials`, which refuses the other
    ordering. Clearing ``insecure`` is always allowed.
    """
    from tasks.dbas_sync_provisioning import insecure_refusal_reason

    reason = insecure_refusal_reason(target, requested_insecure=bool(requested_insecure))
    if reason:
        logger.warning("[SYNC] %s", reason)
        raise HTTPException(status_code=409, detail=reason)


def _provisioning_surface(caller_is_mcp: bool) -> str:
    """Which surface the attempt came in on, for the S13 audit row."""
    from tasks.dbas_sync_provisioning import SURFACE_MCP, SURFACE_REST

    return SURFACE_MCP if caller_is_mcp else SURFACE_REST


def _actor_name(admin) -> Optional[str]:
    """The acting admin principal's name for the audit row, or ``None``.

    ``RequireAdminIfEnabled`` returns the ``User`` when auth is on and ``None``
    in setup mode; never anything credential-bearing.
    """
    return getattr(admin, "username", None)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.post("", status_code=201)
async def create_sync_target(
    req: SyncTargetCreate,
    _admin=RequireAdminIfEnabled,
):
    """Create a new sync target with encrypted credentials. Admin only.

    bead 9kwzp.10 item 3, which was filed as a DECISION rather than a defect:
    all five routes were already admin-gated, so the only question was whether
    admitting the MCP service principal is intended. Verdict: yes, on all five,
    writes included.

    Bead jcj0f shipped ``create_sync_target`` / ``update_sync_target`` /
    ``delete_sync_target`` as MCP tools deliberately, and managing
    cross-instance sync destinations is work the sidecar exists to do. Denying
    the principal here breaks those three tools outright, and that capability
    loss is not worth what the denial buys.

    Recorded so the residual is visible rather than implied: a write names a
    remote host, stores the credentials this instance authenticates to it with,
    sets ``insecure`` (which turns off TLS verification for that traffic), and
    registers or refreshes the target's ``dbas_sync_<id>`` scheduled task, so
    an update can repoint a push the operator already configured. The module
    docstring above is explicit that the AUTHORITATIVE SSRF check runs at sync
    EXECUTE time, not here. Encryption at rest, the last-4 masking on responses
    and ``_redact_credentials_deep`` on the outbound payload all bound
    DISCLOSURE, not redirection.

    What remains in force is that the caller must be an admin — the plain gate
    closes the non-admin half completely — and that the outbound POLICY write
    (``PATCH /api/settings/security``, which is what would widen
    ``ssrf_outbound_mode``) still refuses this principal.
    """
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
        # S11 / INV-4: the SAME predicate the update path calls. A brand-new row
        # is neither provisioned nor observed, so this is a pass today — it is
        # here so the gate is a property of "a write that sets insecure",
        # whichever route performs it, rather than of one handler.
        _guard_insecure_write(target, req.insecure)
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
        _ensure_sync_task_best_effort(target.id, target.name)
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
async def update_sync_target(
    target_id: int,
    req: SyncTargetUpdate,
    _admin=RequireAdminIfEnabled,
):
    """Update a sync target. Admin only.

    Credentials are re-encrypted only if provided. The credential_version bumps
    (same-txn, via the ORM listener) ONLY when ``credentials`` is actually
    written — a rename or enable-toggle does not.

    bead 9kwzp.10 item 3, and the sharpest member of the group: this is the
    route that can repoint a ``base_url``, replace the credentials and clear
    ``insecure`` on a target the operator ALREADY configured and that a
    scheduled task ALREADY pushes to. See :func:`create_sync_target` for the
    full verdict, including why the MCP service principal is admitted anyway.
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
            # S11 / INV-4, the second ordering: enabling `insecure` on a target
            # that is RECORDED or OBSERVED to hold a provider credential on B is
            # refused, with the reason and the real remedy. Refused BEFORE any
            # field is assigned, so a rejected write leaves the row untouched.
            _guard_insecure_write(target, req.insecure)
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
        # A rename must reach the per-target task's display name (same id).
        _ensure_sync_task_best_effort(target.id, target.name)
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
async def delete_sync_target(
    target_id: int,
    _admin=RequireAdminIfEnabled,
):
    """Delete a sync target. Admin only.

    bead 9kwzp.10 item 3. Same gate as its create/update siblings: deleting a
    target unregisters its ``dbas_sync_<id>`` task, silently ending the
    cross-instance push the operator configured. See :func:`create_sync_target`.
    """
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
        _remove_sync_task_best_effort(target_id)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[SYNC] Failed to delete sync target %s: %s", target_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Provisioning routes (ADR-013 S10-S13)
# ---------------------------------------------------------------------------

@router.post("/{target_id}/provision-credentials")
async def provision_credentials(
    target_id: int,
    req: SyncTargetProvisionRequest = SyncTargetProvisionRequest(),
    _admin=RequireAdminIfEnabled,
    caller_is_mcp: bool = ResolveIsMcpServicePrincipalIfEnabled,
):
    """Write this instance's provider credentials onto the replica, once.

    ADR-013 S10. The step that turns a structurally-complete replica into a hot
    standby: the replica already has the channels, groups, profiles, logos and
    EPG links; what it lacks is a working provider credential, so every stream
    URL on it reads ``.../live/***REDACTED***/***REDACTED***/.ts`` and 404s.

    Values are HARVESTED from this instance's own provider records, written, and
    discarded within the request — never persisted here for this purpose
    (INV-3), and never on any recurring cycle (INV-2; scheduled or automatic
    re-push is forbidden under every ruling, S12(c)).

    RE-RUNNING THIS IS THE ROTATION CONTROL (S12(a)). After the provider password
    changes on this instance, re-run it; it needs no input. The staleness signal
    that tells the operator to (INV-8) is derived from destination state the
    cycle already reads, never from comparing credential values.

    Refused (409) when TLS verification is disabled for the target (S11) — a
    provider credential must never cross an unverified connection.
    """
    from tasks.dbas_sync_provisioning import (
        ProvisioningRefused,
        provision_target_credentials,
    )

    db = get_session()
    try:
        target = db.query(SyncTarget).filter(SyncTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Sync target not found")
        outcome = await provision_target_credentials(
            session=db,
            sync_target=target,
            actor=_actor_name(_admin),
            surface=_provisioning_surface(caller_is_mcp),
            schedules_direct_password=req.schedules_direct_password,
        )
        if not outcome.succeeded:
            # A partial write is not a success. The response carries the named
            # per-account breakdown so the operator can see which replicated
            # accounts did and did not receive their credential.
            raise HTTPException(status_code=502, detail=outcome.as_response())
        return outcome.as_response()
    except ProvisioningRefused as e:
        logger.warning("[SYNC] Provisioning refused for target %s: %s", target_id, e)
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning(
            "[SYNC] Failed to provision credentials for target %s: %s", target_id, e
        )
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.post("/{target_id}/deprovision-credentials")
async def deprovision_credentials(
    target_id: int,
    _admin=RequireAdminIfEnabled,
    caller_is_mcp: bool = ResolveIsMcpServicePrincipalIfEnabled,
):
    """Clear the provisioned credential fields on the replica, then the marker.

    ADR-013 S11's de-provision escape (PO-ratified 2026-08-22). The clear is
    ATTEMPTED on the replica over the same derived field set the provisioning
    wrote; the marker flips ONLY if that write succeeded for every targeted
    account (INV-9). A partial or total failure returns 502, leaves the marker
    SET, leaves ``insecure`` refused, and NAMES the accounts still holding a
    credential — the marker means "the replica may still hold a credential", and
    a failed clear is exactly that state.

    The response always carries ``residual_statement``: what a SUCCESSFUL
    de-provision cannot guarantee, told at the moment of the action rather than
    left in a doc.
    """
    from tasks.dbas_sync_provisioning import deprovision_target_credentials

    db = get_session()
    try:
        target = db.query(SyncTarget).filter(SyncTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Sync target not found")
        outcome = await deprovision_target_credentials(
            session=db,
            sync_target=target,
            actor=_actor_name(_admin),
            surface=_provisioning_surface(caller_is_mcp),
        )
        if not outcome.succeeded:
            raise HTTPException(status_code=502, detail=outcome.as_response())
        return outcome.as_response()
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning(
            "[SYNC] Failed to de-provision credentials for target %s: %s", target_id, e
        )
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
