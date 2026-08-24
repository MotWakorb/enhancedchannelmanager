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
import json
import logging
from typing import Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

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
    # Logo replication — DEFAULT ON since bead …-2yq19. It shipped OFF under
    # bead 7ipq2.1 and the reason was COST, not correctness; under ADR-013's
    # faithful-copy principle an OFF default is a silent omission, so the cost
    # is answered by ``logo_sync_interval_hours`` below instead.
    sync_logos: bool = True
    # How often the logo slice may run, in hours. ``0`` means every cycle.
    logo_sync_interval_hours: int = Field(default=24, ge=0)
    # Core-settings blobs this target declines (bead …-10wnq). Omit or leave
    # empty to replicate every blob the engine's register allows.
    core_settings_excluded: Optional[list[str]] = None
    # THE ONE CREDENTIAL THE OPERATOR TYPES, and they type it once (PO ruling
    # 2026-08-22). Write-only: encrypted at rest and never echoed back — the
    # read shape carries ``has_schedules_direct_password``, a boolean.
    #
    # It is asked for ONLY when this instance actually has a ``schedules_direct``
    # EPG source; the UI reads ``GET /api/sync-targets/source-credential-needs``
    # to decide, so an operator with no SD source is never shown a field they
    # cannot fill in. Every other provider credential is harvested from this
    # instance's own records and needs no input at all.
    schedules_direct_password: Optional[str] = None

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
    logo_sync_interval_hours: Optional[int] = Field(default=None, ge=0)
    core_settings_excluded: Optional[list[str]] = None
    # Omitted => unchanged (the ``credentials`` precedent). An explicit empty
    # string CLEARS it, which is the only way to withdraw a stored SD password
    # without deleting the target.
    schedules_direct_password: Optional[str] = None

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
    logo_sync_interval_hours: int = 24
    # The blobs this target declines. Always a list on the read shape, even
    # when the column is NULL — an operator surface should not have to tell
    # "excludes nothing" from "not set".
    core_settings_excluded: list[str] = []
    # When the logo slice last actually ran (bead …-2yq19); None == never, which
    # is also what makes a new target carry logos on its FIRST cycle.
    last_logo_sync_at: Optional[str] = None
    # PRESENCE of the stored Schedules Direct password — never the value. The
    # two one-time-provisioning markers this replaced
    # (``credentials_provisioned_at`` / ``destination_credential_observed_at``)
    # were dropped in migration 0047 along with the gate that read them.
    has_schedules_direct_password: bool = False
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


def _encrypt_sd_password(value: Optional[str]) -> Optional[str]:
    """Encrypt a supplied Schedules Direct password for storage, or ``None``.

    Uses the SAME ``cloud_storage.crypto`` Fernet path as this row's own
    ``credentials`` column, wrapped in a one-key dict so the stored ciphertext
    has the same shape as every other credential blob in this table and the
    decrypt side has one code path rather than two.

    ``None`` and the empty string both store ``None``: "no Schedules Direct
    password" is the absence of a value, not an empty one, and an empty string
    written onto the replica would take a working SD source DOWN.
    """
    from tasks.dbas_sync_engine import SCHEDULES_DIRECT_PASSWORD_FIELD

    if not value:
        return None
    return encrypt_credentials({SCHEDULES_DIRECT_PASSWORD_FIELD: value})


def _decode_excluded_core_settings(raw) -> list[str]:
    """Read the stored JSON list back, fail-soft to "excludes nothing".

    A corrupt column must not stop settings replicating (the faithful-copy
    direction), and it must not 500 a read of the whole target either.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logger.warning("[SYNC-TARGETS] Unreadable core_settings_excluded; treating as empty.")
        return []
    if not isinstance(parsed, list):
        return []
    return sorted({n for n in parsed if isinstance(n, str) and n})


def _encode_excluded_core_settings(names: Optional[list[str]]) -> Optional[str]:
    """Validate and store an opt-out list, or ``None`` for "excludes nothing".

    VALIDATED AGAINST THE ENGINE'S OWN REGISTER, not against a copy of it, so
    the API cannot accept a blob name the engine has never heard of and leave an
    operator believing they excluded something. Naming a never-sync blob is
    rejected rather than silently accepted: it opts into nothing either way, and
    accepting it would tell the operator their choice mattered.
    """
    if names is None:
        return None
    from tasks.dbas_sync_engine import (
        NEVER_SYNC_CORE_SETTINGS_BLOBS,
        SYNC_CORE_SETTINGS_BLOBS,
    )

    cleaned = sorted({n.strip() for n in names if isinstance(n, str) and n.strip()})
    if not cleaned:
        return None
    unknown = [n for n in cleaned if n not in SYNC_CORE_SETTINGS_BLOBS]
    if unknown:
        never = [n for n in unknown if n in NEVER_SYNC_CORE_SETTINGS_BLOBS]
        if never:
            raise HTTPException(
                status_code=400,
                detail=(
                    "%s is never replicated to a sync target, so it cannot be "
                    "excluded per target." % ", ".join(never)
                ),
            )
        raise HTTPException(
            status_code=400,
            detail=(
                "Unknown core-settings blob(s): %s. Valid values: %s."
                % (", ".join(unknown), ", ".join(sorted(SYNC_CORE_SETTINGS_BLOBS)))
            ),
        )
    return json.dumps(cleaned)


def _serialize(target: SyncTarget, plaintext_creds: Optional[dict] = None) -> dict:
    """Build a response dict with masked credentials.

    ``plaintext_creds`` (the just-written dict) is masked directly when present;
    otherwise the stored ciphertext is decrypted and masked. Decryption is
    fail-soft (FERNET_KEY rotation) — falls back to the empty masked placeholder.
    """
    data = target.to_dict(mask_credentials=True)
    # The column stores a JSON list; the read shape is a real list, always
    # present (bead …-10wnq). An operator surface should not have to tell
    # "excludes nothing" from "column never set".
    data["core_settings_excluded"] = _decode_excluded_core_settings(
        target.core_settings_excluded
    )
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
# Provider credentials cross on EVERY cycle (PO ruling 2026-08-22)
# ---------------------------------------------------------------------------
#
# THERE IS NO PROVISIONING ROUTE HERE ANY MORE, and its absence is the feature.
# Until this commit the operator had to take a separate "Provision Credentials"
# action, and before that they had to re-type the provider password on the
# replica by hand. The PO removed both: "We should be sending credentials every
# time so that we don't need the user to deal with needing to re-type anything.
# Any update happens as soon as the next scheduled sync occurs."
#
# So the credential fields are part of what the ordinary sync cycle writes
# (``tasks.dbas_sync_engine.PROVIDER_CREDENTIAL_SECTIONS``), and this router is
# back to plain CRUD. Deleted with the routes:
#
# * ``POST /{id}/provision-credentials`` and ``/{id}/deprovision-credentials``;
# * ``_guard_insecure_write`` — the S11 refusal that returned 409 when a target
#   holding a credential was set ``insecure``, in both write orders. It is now a
#   WARNING the cycle emits on every credential-carrying run
#   (``insecure_transmission_warning``) and the SyncTargets card renders. The PO
#   owns that risk explicitly: "That's on the user to mitigate, not us."
# * the ``credentials_provisioned_at`` / ``destination_credential_observed_at``
#   markers (migration 0047 drops the columns).
#
# The ONE thing an operator still types is the Schedules Direct password, and
# they type it once: it is a write-only field on this row (see
# ``SyncTargetCreate.schedules_direct_password``), because Dispatcharr never
# returns it and there is nothing on this instance to harvest.


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
            logo_sync_interval_hours=req.logo_sync_interval_hours,
            core_settings_excluded=_encode_excluded_core_settings(
                req.core_settings_excluded
            ),
            schedules_direct_password=_encrypt_sd_password(
                req.schedules_direct_password
            ),
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


@router.get("/source-credential-needs")
async def source_credential_needs(_admin=RequireAdminIfEnabled):
    """What this instance CANNOT harvest, and therefore has to be typed.

    Exactly one thing can be on this list, and usually nothing is. Every
    provider credential on this instance is read off its own records on every
    sync cycle and needs no operator input at all; the Schedules Direct
    password is the sole exception, because Dispatcharr marks it write-only,
    never returns it, and SHA1-hashes it at fetch. Absence there means
    UNREADABLE, not unset.

    THIS ROUTE EXISTS SO THE FORM CAN STAY EMPTY. The SyncTargets create form
    shows a Schedules Direct password field ONLY when
    ``needs_schedules_direct_password`` is true — i.e. only when this instance
    actually has a ``schedules_direct`` EPG source. An operator with no such
    source is never shown a credential box they have no value for, which is the
    whole of "easy for the user": zero typing unless zero is impossible.

    Declared BEFORE ``/{target_id}`` so the literal path is not swallowed by the
    int path parameter.

    Fail-soft: an unreachable local Dispatcharr answers "nothing needed" rather
    than failing target creation. The cost of being wrong that way is a
    Schedules Direct source that keeps its existing password on the replica; the
    cost of the other way is an operator who cannot create a target at all.
    """
    from tasks.dbas_sync_engine import SCHEDULES_DIRECT_SOURCE_TYPE

    names: list[str] = []
    try:
        from dispatcharr_client import get_client

        sources = await get_client().get_epg_sources() or []
        names = [
            source.get("name") or "<unnamed>"
            for source in sources
            if isinstance(source, dict)
            and source.get("source_type") == SCHEDULES_DIRECT_SOURCE_TYPE
        ]
    except Exception as e:  # noqa: BLE001 — advisory only; never block creation
        logger.warning(
            "[SYNC] Could not read local EPG sources for credential needs: %s", e
        )
    return {
        "needs_schedules_direct_password": bool(names),
        "schedules_direct_sources": names,
    }


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
            # NOT REFUSED ANY MORE. This used to 409 when the target held a
            # provider credential on the replica (ADR-013 S11, both write
            # orders). The PO removed that gate — "I know the security risks.
            # That's on the user to mitigate, not us." — so the write goes
            # through and the exposure is stated instead, on every
            # credential-carrying cycle
            # (``tasks.dbas_sync_engine.insecure_transmission_warning``) and on
            # the target's row in the UI.
            target.insecure = req.insecure
        if req.fuzzy_stream_matching is not None:
            target.fuzzy_stream_matching = req.fuzzy_stream_matching
        if req.sync_logos is not None:
            target.sync_logos = req.sync_logos
        if req.logo_sync_interval_hours is not None:
            target.logo_sync_interval_hours = req.logo_sync_interval_hours
        if req.core_settings_excluded is not None:
            target.core_settings_excluded = _encode_excluded_core_settings(
                req.core_settings_excluded
            )
        if req.credentials is not None:
            target.credentials = encrypt_credentials(req.credentials)
        if req.schedules_direct_password is not None:
            # An explicit empty string CLEARS the stored password; any other
            # value replaces it. Omitting the field leaves it untouched.
            target.schedules_direct_password = _encrypt_sd_password(
                req.schedules_direct_password
            )

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
