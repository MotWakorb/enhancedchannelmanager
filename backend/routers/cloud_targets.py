"""
Cloud storage target router — CRUD and connection-test endpoints for the
cloud storage targets that DBAS backup uploads to.

These endpoints back the cloud-destination management UI (Settings → Backup)
and the ``list_cloud_targets`` MCP tool. They were historically served by the
(removed) Export tab's router under ``/api/export``; with the Export tab gone
(beads vrrxv / 1w428) the surface was relocated here under ``/api/cloud-targets``
to match the documented backup API contract (``docs/api.md`` → Cloud
destination endpoints). DBAS backup (``tasks/dbas_backup.py``) consumes the
``CloudStorageTarget`` rows these endpoints manage.
"""
import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from auth import RequireAdminIfEnabled, RequireHumanAdminForOutboundDestination, RequireHumanAdminForOutboundTest
from cloud_storage import SUPPORTED_PROVIDERS, get_adapter
from cloud_storage.crypto import encrypt_credentials, decrypt_credentials
from cloud_storage.onedrive_adapter import _validate_tenant_id, _validate_drive_id
from database import get_session
from export_models import CloudStorageTarget
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cloud-targets", tags=["Backup"])


# ---------------------------------------------------------------------------
# Cloud Storage Target Pydantic models
# ---------------------------------------------------------------------------


def _validate_onedrive_credentials(provider_type: Optional[str], creds: Optional[dict]) -> Optional[dict]:
    """If provider_type is onedrive, validate tenant_id/drive_id shape.

    Rejects SSRF-prone identifiers at the API boundary. See CodeQL alerts
    1361 and 1362 and bead enhancedchannelmanager-zbt74.
    """
    if provider_type != "onedrive" or not creds:
        return creds
    tenant_id = creds.get("tenant_id")
    if tenant_id is not None:
        try:
            _validate_tenant_id(tenant_id)
        except ValueError as exc:
            raise ValueError(f"credentials.tenant_id: {exc}") from exc
    drive_id = creds.get("drive_id")
    if drive_id is not None and drive_id != "":
        try:
            _validate_drive_id(drive_id)
        except ValueError as exc:
            raise ValueError(f"credentials.drive_id: {exc}") from exc
    return creds


def _reject_reserved_credential_keys(creds: Optional[dict]) -> Optional[dict]:
    """Refuse a credentials dict that smuggles the RESERVED ``insecure`` key.

    PR #743 review item 2 (0i2vt.8): TLS policy is derived ONLY from the
    top-level ``insecure`` flag (persisted on ``CloudStorageTarget.insecure``,
    audited on every skip). Before this guard, an arbitrary
    ``credentials.insecure`` key silently disabled verification on the
    saved/inline test paths while scheduled uploads overrode it from the
    column — a split-brain where a test could succeed against an endpoint the
    real upload would refuse (or vice versa). Reject-loudly at the API
    boundary; the saved-test path additionally strips the key from LEGACY
    stored rows before overriding from the column.
    """
    if creds and "insecure" in creds:
        raise ValueError(
            "credentials.insecure is reserved — use the top-level 'insecure' "
            "flag to control TLS verification"
        )
    return creds


def _validate_request_credentials(provider_type: Optional[str], creds: Optional[dict]) -> Optional[dict]:
    """Shared credential validation for every cloud-target request model."""
    _reject_reserved_credential_keys(creds)
    return _validate_onedrive_credentials(provider_type, creds)


class CloudTargetCreateRequest(BaseModel):
    name: str
    provider_type: Literal["s3", "gdrive", "webdav", "onedrive", "dropbox"]
    credentials: dict
    upload_path: str = "/"
    enabled: bool = True
    # Top-level TLS-verification skip (persists to CloudStorageTarget.insecure).
    # The ONLY way to weaken TLS — credentials.insecure is rejected above.
    insecure: bool = False

    @field_validator("credentials")
    @classmethod
    def _check_credentials(cls, v, info):
        return _validate_request_credentials(info.data.get("provider_type"), v)


class CloudTargetUpdateRequest(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[Literal["s3", "gdrive", "webdav", "onedrive", "dropbox"]] = None
    credentials: Optional[dict] = None
    upload_path: Optional[str] = None
    enabled: Optional[bool] = None
    insecure: Optional[bool] = None

    @field_validator("credentials")
    @classmethod
    def _check_credentials(cls, v, info):
        return _validate_request_credentials(info.data.get("provider_type"), v)


class CloudTargetTestRequest(BaseModel):
    provider_type: Literal["s3", "gdrive", "webdav", "onedrive", "dropbox"]
    credentials: dict
    # Inline-test TLS opt-out — same semantics as the saved target's column so
    # a pre-save test exercises the same policy the scheduled upload will use.
    insecure: bool = False

    @field_validator("credentials")
    @classmethod
    def _check_credentials(cls, v, info):
        return _validate_request_credentials(info.data.get("provider_type"), v)


def _mask_credentials(creds: dict) -> dict:
    """Mask sensitive credential values, showing only last 4 chars."""
    masked = {}
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


# ---------------------------------------------------------------------------
# Cloud Storage Target CRUD
# ---------------------------------------------------------------------------

@router.get("")
async def list_cloud_targets(_admin=RequireAdminIfEnabled):
    """List all cloud storage targets with masked credentials. Admin only.

    bead 9kwzp.10 item 4: this router carried no dependency on any of its four
    CRUD routes, so any authenticated non-admin reached them. The read takes
    the PLAIN admin tier — the MCP service principal stays admitted, which is
    what keeps the shipped ``list_cloud_targets`` tool working — because every
    credential in the response is masked to its last four characters and no
    stored secret can be recovered through it. The three WRITES below are a
    different verdict; see :func:`create_cloud_target`.
    """
    db = get_session()
    try:
        targets = db.query(CloudStorageTarget).order_by(CloudStorageTarget.name).all()
        result = []
        for t in targets:
            data = t.to_dict(mask_credentials=True)
            # Decrypt and re-mask to show last 4 chars
            try:
                decrypted = decrypt_credentials(t.credentials)
                data["credentials"] = _mask_credentials(decrypted)
            except Exception:
                data["credentials"] = {"error": "Could not decrypt"}
            result.append(data)
        return result
    finally:
        db.close()


@router.post("", status_code=201)
async def create_cloud_target(
    req: CloudTargetCreateRequest,
    _admin=RequireHumanAdminForOutboundDestination,
):
    """Create a new cloud storage target with encrypted credentials. Human-admin only.

    bead 9kwzp.10 item 4. Two halves to the verdict.

    The missing admin tier is the plain defect: this route wrote Fernet-encrypted
    credentials for any authenticated non-admin, while bead 9kwzp.6 had already
    put both ``/test`` verbs behind the human-admin gate — so a caller could
    create a credential-bearing target and not be allowed to test it.

    The MCP half is decided on the evidence rather than on precedent. Bead jcj0f
    did expose ``create_cloud_target`` / ``update_cloud_target`` /
    ``delete_cloud_target`` as MCP tools, but a deliberately-shipped tool
    establishes product intent, not least privilege. What a write here actually
    does is name the host ``tasks/dbas_backup.py`` PUTs the operator's archive
    to, supply the credentials it authenticates with, and set ``insecure``,
    which turns off TLS verification for that upload. Updating an EXISTING
    target repoints a flow the operator already configured. That is the shape
    kgz3k denies this principal on POST /api/settings — rewriting an outbound
    base URL a background job will then contact — deferred onto a schedule,
    which makes it quieter rather than safer. Encryption at rest and the
    last-4 masking bound disclosure of a stored credential; they say nothing
    about redirection, replacement or the TLS downgrade.
    """
    db = get_session()
    try:
        existing = db.query(CloudStorageTarget).filter(CloudStorageTarget.name == req.name).first()
        if existing:
            raise HTTPException(status_code=409, detail=f"Target with name '{req.name}' already exists")

        encrypted = encrypt_credentials(req.credentials)
        target = CloudStorageTarget(
            name=req.name,
            provider_type=req.provider_type,
            credentials=encrypted,
            upload_path=req.upload_path,
            enabled=req.enabled,
            insecure=req.insecure,
        )
        db.add(target)
        db.commit()
        db.refresh(target)

        journal.log_entry(
            category="backup",
            action_type="create",
            entity_name=target.name,
            description=f"Created cloud target '{target.name}' ({target.provider_type})",
            entity_id=target.id,
        )

        data = target.to_dict(mask_credentials=True)
        data["credentials"] = _mask_credentials(req.credentials)
        logger.info("[CLOUD-TARGETS] Created cloud target id=%s name=%s provider=%s", target.id, target.name, target.provider_type)
        return data
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[CLOUD-TARGETS] Failed to create cloud target: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.patch("/{target_id}")
async def update_cloud_target(
    target_id: int,
    req: CloudTargetUpdateRequest,
    _admin=RequireHumanAdminForOutboundDestination,
):
    """Update a cloud storage target. Human-admin only.

    bead 9kwzp.10 item 4, and the sharpest member of the group: this is the
    route that can repoint a destination the operator ALREADY configured and
    that the backup task ALREADY uploads to. Credentials are re-encrypted if
    provided. See :func:`create_cloud_target` for the full verdict.
    """
    db = get_session()
    try:
        target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Cloud target not found")

        if req.name is not None and req.name != target.name:
            existing = db.query(CloudStorageTarget).filter(
                CloudStorageTarget.name == req.name, CloudStorageTarget.id != target_id
            ).first()
            if existing:
                raise HTTPException(status_code=409, detail=f"Target with name '{req.name}' already exists")
            target.name = req.name

        if req.provider_type is not None:
            target.provider_type = req.provider_type
        if req.upload_path is not None:
            target.upload_path = req.upload_path
        if req.enabled is not None:
            target.enabled = req.enabled
        if req.insecure is not None:
            target.insecure = req.insecure
        if req.credentials is not None:
            target.credentials = encrypt_credentials(req.credentials)

        db.commit()
        db.refresh(target)

        journal.log_entry(
            category="backup",
            action_type="update",
            entity_name=target.name,
            description=f"Updated cloud target '{target.name}'",
            entity_id=target.id,
        )

        data = target.to_dict(mask_credentials=True)
        try:
            decrypted = decrypt_credentials(target.credentials)
            data["credentials"] = _mask_credentials(decrypted)
        except Exception as decrypt_err:
            # Decryption can fail if FERNET_KEY rotated since the row was written;
            # fall back to the masked-credentials placeholder from to_dict() so
            # the API still returns the rest of the target metadata.
            logger.warning(
                "[CLOUD-TARGETS] Could not decrypt credentials for target %s for masked echo: %s",
                target_id,
                decrypt_err,
            )
        logger.info("[CLOUD-TARGETS] Updated cloud target id=%s", target_id)
        return data
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[CLOUD-TARGETS] Failed to update cloud target %s: %s", target_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@router.delete("/{target_id}", status_code=204)
async def delete_cloud_target(
    target_id: int,
    _admin=RequireHumanAdminForOutboundDestination,
):
    """Delete a cloud storage target. Human-admin only.

    bead 9kwzp.10 item 4. Same gate as its create/update siblings: removing the
    destination the backup task uploads to silently ends off-box retention of
    the operator's archives, which is the availability half of the same
    control. See :func:`create_cloud_target`.
    """
    db = get_session()
    try:
        target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Cloud target not found")

        name = target.name
        db.delete(target)
        db.commit()

        journal.log_entry(
            category="backup",
            action_type="delete",
            entity_name=name,
            description=f"Deleted cloud target '{name}'",
            entity_id=target_id,
        )
        logger.info("[CLOUD-TARGETS] Deleted cloud target id=%s name=%s", target_id, name)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.warning("[CLOUD-TARGETS] Failed to delete cloud target %s: %s", target_id, e)
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Cloud Storage Test Connection
# ---------------------------------------------------------------------------


def _audit_insecure_test(
    target_id: Optional[int], target_name: str, provider_type: str
) -> None:
    """Audit row for a connection test that SKIPS TLS verification.

    Mirrors ``tasks.dbas_backup.DbasBackupTask._audit_insecure`` (the upload
    path's row) so the insecure-TLS audit fires on test paths the same as on
    uploads — PR #743 review item 2, requirement (d). Best-effort, never fails
    the test call.
    """
    try:
        journal.log_entry(
            category="backup_outbound",
            action_type="cloud_test_insecure_tls",
            entity_name=target_name,
            entity_id=target_id,
            description=(
                "TLS verification SKIPPED (insecure=true) for %s target "
                "'%s' (id=%s) on a cloud-target connection test" % (
                    provider_type, target_name, target_id,
                )
            ),
            user_initiated=True,
        )
    except Exception as e:  # pragma: no cover — journal best-effort
        logger.warning("[CLOUD-TARGETS] Failed to journal insecure-test audit: %s", e)


def _adapter_credentials(creds: dict, insecure: bool) -> dict:
    """Derive the adapter's credential dict with ONE TLS policy source.

    Strips any (legacy) ``insecure`` key riding inside the stored credentials
    and injects the authoritative top-level flag — the SAME override the
    scheduled upload path applies (``tasks/dbas_backup.py``), so test behavior
    always equals upload behavior (PR #743 item 2, requirement (c)).
    """
    out = dict(creds)
    out.pop("insecure", None)
    out["insecure"] = bool(insecure)
    return out


def _deferred_provider_result(provider_type: str) -> Optional[dict]:
    """Fail-closed test result for a DEFERRED (un-hardened) provider, else None.

    OneDrive and Dropbox adapters are not yet routed through the SSRF chokepoint
    (PO decision 2026-06-17; see ``cloud_storage.SUPPORTED_PROVIDERS``). The
    config-UI "Test Connection" surface must not exercise a deferred adapter's
    raw outbound call, so this returns a non-silent "not supported" result for
    such a provider and ``None`` for a supported one (test proceeds normally).
    SEC-4, bead enhancedchannelmanager-uomwu.
    """
    if provider_type in SUPPORTED_PROVIDERS:
        return None
    return {
        "success": False,
        "message": (
            f"Provider '{provider_type}' is not supported in this release "
            "(deferred to a v0.18.x follow-up)."
        ),
    }


@router.post("/{target_id}/test")
async def test_cloud_target(
    target_id: int,
    _admin=RequireHumanAdminForOutboundTest,
):
    """Test connection to a saved cloud storage target.

    bead 9kwzp.6: admin-gated, and the static MCP service principal is
    refused. This endpoint carried NO route dependency. It decrypts the
    target's STORED credentials and authenticates against the provider with
    them, then reports the verdict back — the same class as
    ``/api/settings/test-smtp`` that bead i4qrp closed. For a WebDAV target
    the stored ``base_url`` is operator-named, so the probe is also an
    outbound reach the gate has to decide the principal for.

    ``RequireAdminIfEnabled`` would NOT do here: the MCP principal carries
    ``is_admin=True``, so the plain admin gate would leave it reachable.
    """
    db = get_session()
    try:
        target = db.query(CloudStorageTarget).filter(CloudStorageTarget.id == target_id).first()
        if not target:
            raise HTTPException(status_code=404, detail="Cloud target not found")
        provider_type = target.provider_type
        target_name = target.name
        insecure = bool(target.insecure)
        creds = decrypt_credentials(target.credentials)
    finally:
        db.close()

    deferred = _deferred_provider_result(provider_type)
    if deferred is not None:
        logger.info("[CLOUD-TARGETS] Test refused for deferred provider '%s'", provider_type)
        return deferred

    # ONE TLS policy source: the target's insecure column — never a key inside
    # the stored credentials (parity with the scheduled upload path). Audit the
    # skip BEFORE the request, same as uploads do.
    creds = _adapter_credentials(creds, insecure)
    if insecure:
        _audit_insecure_test(target_id, target_name, provider_type)

    try:
        adapter = get_adapter(provider_type, creds)
        result = await adapter.test_connection()
        return {
            "success": result.success,
            "message": result.message,
            "provider_info": result.provider_info,
        }
    except ImportError as e:
        # CodeQL py/stack-trace-exposure (#1350): the missing-module name
        # alone (e.g. "msal", "boto3") is operational hint, not a stack trace.
        # Sanitize via ImportError.name so the only field returned is the
        # adapter dep name; do not echo the full str(e) which can include
        # interpreter paths on some platforms.
        logger.exception("[CLOUD-TARGETS] Cloud target test missing dependency")
        missing = getattr(e, "name", None) or "unknown"
        return {
            "success": False,
            "message": f"Missing dependency: {missing}",
        }
    except Exception as e:
        # CodeQL py/stack-trace-exposure (#1351): log full exception for
        # operator diagnosis; return generic message + class to client. Cloud
        # adapter errors can include URLs, tenant IDs, or token fragments.
        logger.exception("[CLOUD-TARGETS] Cloud target test failed")
        return {
            "success": False,
            "message": f"Connection test failed ({type(e).__name__})",
        }


@router.post("/test")
async def test_cloud_target_inline(
    req: CloudTargetTestRequest,
    _admin=RequireHumanAdminForOutboundTest,
):
    """Test connection with inline credentials (before saving).

    bead 9kwzp.6: admin-gated, and the static MCP service principal is
    refused. This endpoint carried NO route dependency and is the most
    caller-controlled sink of the set: the caller names the provider, the
    credentials AND (for WebDAV) the ``base_url``, and reads back whether the
    reach succeeded. That is an in-band probe with attacker-chosen
    credentials, the exact primitive bead i4qrp denied the MCP principal on
    ``/api/settings/test``.
    """
    deferred = _deferred_provider_result(req.provider_type)
    if deferred is not None:
        logger.info("[CLOUD-TARGETS] Inline test refused for deferred provider '%s'", req.provider_type)
        return deferred

    # Same single-source TLS policy as the saved-test/upload paths: the request
    # model rejected credentials.insecure; the top-level flag drives the adapter
    # and the audit row fires on a skip, exactly as uploads audit.
    creds = _adapter_credentials(req.credentials, req.insecure)
    if req.insecure:
        _audit_insecure_test(None, "(inline test)", req.provider_type)

    try:
        adapter = get_adapter(req.provider_type, creds)
        result = await adapter.test_connection()
        return {
            "success": result.success,
            "message": result.message,
            "provider_info": result.provider_info,
        }
    except ImportError as e:
        # CodeQL py/stack-trace-exposure (#1352): see test_cloud_target above.
        logger.exception("[CLOUD-TARGETS] Inline cloud target test missing dependency")
        missing = getattr(e, "name", None) or "unknown"
        return {
            "success": False,
            "message": f"Missing dependency: {missing}",
        }
    except Exception as e:
        # CodeQL py/stack-trace-exposure (#1353): see test_cloud_target above.
        logger.exception("[CLOUD-TARGETS] Inline cloud target test failed")
        return {
            "success": False,
            "message": f"Connection test failed ({type(e).__name__})",
        }
