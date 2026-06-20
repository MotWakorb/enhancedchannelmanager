"""Remote ``DispatcharrClient`` factory for cross-instance live sync.

Bead ``enhancedchannelmanager-1t3al`` (epic ``i39wu``). Threat model
``docs/security/threat_model_dbas_import.md`` §11 Addendum D rows D1/D5/D6.

Why this module exists
----------------------
Cross-instance sync is "restore over HTTP": it reuses the DBAS restore
orchestrator pointed at a Dispatcharr-B instance instead of the local
``get_client()``. The seam that makes that work is a :class:`DispatcharrClient`
built from a ``SyncTarget`` row. That client makes the SAME class of
operator-configurable outbound request the cloud-upload adapters make, so it is
subject to the SAME SSRF threat (D1): an operator-typed ``base_url`` is a classic
SSRF primitive (IMDS ``169.254.169.254``, internal hosts, loopback) and the sync
is recurring + unattended.

This module provides the REUSABLE building blocks (the per-cycle wiring lives in
later beads ``tjaey``/``5gzg5``):

1. :func:`make_remote_client` — Fernet-decrypt the target credentials AT CALL
   TIME (never logged), build a :class:`DispatcharrSettings`, and return a
   :class:`DispatcharrClient` whose outbound HTTP is routed through the
   :mod:`security.ssrf` chokepoint (resolve-then-connect-by-IP) on EVERY request
   via a custom httpx transport — not just once at config-save time (D1,
   DNS-rebinding). Redirect re-validation + ``https→http`` downgrade refusal come
   from the ssrf primitives.

2. :func:`sync_freshness_reason` — re-read the ``SyncTarget`` FRESH given the
   ``credential_version`` captured at enqueue, and return an abort reason if the
   version changed, ``token_revoked_at`` is set, or the target is missing /
   disabled (D5). Mirrors ``dbas_backup`` ``_check_credential_freshness`` /
   ``_freshness_reason``. Pure / testable.

3. :func:`audit_insecure_cycle` — write the per-cycle ``category='sync_outbound'``
   journal row (``tls_verified=false``) an ``insecure=true`` target requires (D6).
   Mirrors ``dbas_backup`` ``_audit_insecure``.

SSRF integration approach (the genuinely fiddly part)
-----------------------------------------------------
``DispatcharrClient`` builds every request URL as ``f"{base_url}{path}"`` against
a single shared ``httpx.AsyncClient``. Rather than touch each of its ~80 methods,
this module installs a **custom httpx transport** (:class:`_PinnedSSRFTransport`)
on that client. The transport intercepts EVERY outbound request, runs
``validate_outbound_url`` (resolve-then-connect-by-IP) at execute time, rewrites
the request to connect to the validated IP (closing the DNS-rebinding window),
preserves the original hostname for TLS SNI (``sni_hostname`` request extension)
and the ``Host:`` header, and re-validates any redirect hop (refusing
``https→http`` downgrades). This is the same resolve-then-connect-by-IP posture
the cloud-upload WebDAV adapter uses via ``pinned_async_client`` /
``pinned_request_url``, applied uniformly to the whole Dispatcharr surface.
"""
from __future__ import annotations

import logging
from typing import Optional

# This module's ONLY outbound path is the SSRF-chokepointed transport below:
# every request is routed through security.ssrf.validate_outbound_url
# (resolve-then-connect-by-IP) before a socket opens. httpx is imported solely to
# build that pinned transport; there is no raw/un-validated outbound call here.
import httpx  # ssrf-ok: only used to build the security.ssrf pinned transport (see _PinnedSSRFTransport)

import journal
from cloud_storage.crypto import decrypt_credentials
from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient
from security.ssrf import (
    SSRFError,
    get_ssrf_mode,
    validate_outbound_url,
    validate_redirect,
)

logger = logging.getLogger(__name__)


class SyncCredentialError(Exception):
    """Raised when a SyncTarget's credentials cannot be decrypted / are unusable.

    Fail-closed: the caller MUST NOT proceed with a sync it cannot authenticate.
    The message is safe to surface to the admin operator and NEVER carries the
    plaintext credential material.
    """


# ---------------------------------------------------------------------------
# Pinned SSRF transport — the chokepoint for EVERY outbound request (D1).
# ---------------------------------------------------------------------------
class _PinnedSSRFTransport(httpx.AsyncBaseTransport):
    """An httpx transport that routes every request through the SSRF chokepoint.

    On each request it:

    * runs :func:`validate_outbound_url` on the request URL at EXECUTE time
      (resolve-then-connect-by-IP, fail-closed) — so a host that rebinds to a
      denied address between config-save and fire-time is rejected per request,
      not trusted from a one-time check;
    * rewrites the request authority to the validated IP (the socket goes to the
      IP, no second DNS lookup) while preserving the original hostname for TLS
      SNI (the ``sni_hostname`` request extension, honoured by httpcore) and the
      ``Host:`` header (so vhost routing still works);
    * re-validates redirect hops via :func:`validate_redirect` (refusing
      ``https→http`` downgrades).

    ``verify`` is threaded down to the inner transport's TLS context. The
    per-target ``insecure=true`` flag wires ``verify=False`` here; the caller is
    responsible for the per-cycle insecure audit row (:func:`audit_insecure_cycle`).
    """

    def __init__(self, *, verify: bool = True, timeout: float = 30.0):
        self._verify = verify
        # The real network transport. Built once; redirects are NOT followed at
        # this layer (the AsyncClient drives redirects and each hop re-enters
        # handle_async_request, so every hop is independently re-validated).
        self._inner = httpx.AsyncHTTPTransport(verify=verify)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        original_url = str(request.url)
        mode = get_ssrf_mode()

        # Re-validate the (possibly redirected) target on EVERY request. httpx
        # stores the originating URL in the ``from_url`` extension on a redirect
        # follow-up; when present we run the redirect validator so a 3xx to an
        # internal address / an https→http downgrade is refused.
        from_url = request.extensions.get("from_url") if request.extensions else None
        try:
            if from_url:
                target = validate_redirect(str(from_url), original_url, mode)
            else:
                target = validate_outbound_url(original_url, mode)
        except SSRFError:
            # Re-raise unchanged (safe message, no credential material) so the
            # caller's sync loop aborts + audits this cycle.
            raise

        # Rewrite the authority to the validated IP. Preserve scheme/path/query
        # from the original request; the socket now goes to the IP (no second
        # DNS lookup — closes the rebinding window).
        host = f"[{target.ip}]" if target.ip.version == 6 else str(target.ip)
        pinned_url = request.url.copy_with(host=host, port=target.port)

        pinned_request = httpx.Request(
            method=request.method,
            url=pinned_url,
            headers=request.headers,
            stream=request.stream,
            extensions={
                **(request.extensions or {}),
                # Preserve TLS SNI for the original hostname despite connecting
                # by IP (honoured by httpcore).
                "sni_hostname": target.hostname,
            },
        )
        # Present the original hostname as the Host header so vhost routing /
        # TLS cert matching still work.
        pinned_request.headers["Host"] = target.host_header

        return await self._inner.handle_async_request(pinned_request)

    async def aclose(self) -> None:
        await self._inner.aclose()


# ---------------------------------------------------------------------------
# Credential mapping (free-form encrypted dict -> DispatcharrSettings).
# ---------------------------------------------------------------------------
def _settings_from_credentials(base_url: str, creds: dict, *, verify: bool) -> DispatcharrSettings:
    """Map a decrypted credentials dict onto a :class:`DispatcharrSettings`.

    The SyncTarget credentials blob is free-form (operator-supplied). We infer
    the auth method from the keys present: an API key (``api_key`` /
    ``dispatcharr_api_key``) selects api-key mode; otherwise username+password.
    NEVER log any value out of ``creds``.
    """
    auth_method = creds.get("auth_method")
    api_key = creds.get("dispatcharr_api_key") or creds.get("api_key") or ""
    username = creds.get("username") or ""
    password = creds.get("password") or ""

    if not auth_method:
        auth_method = "api_key" if api_key else "password"

    return DispatcharrSettings(
        url=base_url,
        auth_method=auth_method,
        username=username,
        password=password,
        dispatcharr_api_key=api_key,
        api_key=api_key,
    )


def make_remote_client(sync_target) -> DispatcharrClient:
    """Build an SSRF-guarded :class:`DispatcharrClient` for a ``SyncTarget`` row.

    Decrypts ``sync_target.credentials`` via Fernet AT CALL TIME (never logged),
    builds a :class:`DispatcharrSettings` pointed at ``sync_target.base_url``, and
    returns a client whose every outbound request is routed through the
    :mod:`security.ssrf` chokepoint (resolve-then-connect-by-IP) via
    :class:`_PinnedSSRFTransport`.

    The ``base_url`` is validated once at construct time (fail-fast on an
    obviously denied host like IMDS) AND again on every request by the transport
    (the load-bearing DNS-rebinding control — D1).

    Args:
        sync_target: a ``SyncTarget`` ORM row (or any object exposing
            ``base_url``, ``credentials``, ``insecure``).

    Returns:
        A :class:`DispatcharrClient` ready to make SSRF-guarded calls to B.

    Raises:
        SyncCredentialError: credentials cannot be decrypted (fail-closed).
        security.ssrf.SSRFError: ``base_url`` is denied by the SSRF policy.
    """
    base_url = (sync_target.base_url or "").rstrip("/")
    insecure = bool(getattr(sync_target, "insecure", False))
    verify = not insecure

    # Decrypt at call time. decrypt_credentials fails soft (returns None); we
    # turn that into a hard, fail-closed error — never proceed unauthenticated.
    creds = decrypt_credentials(sync_target.credentials)
    if creds is None:
        # Log only the target id — NEVER the ciphertext or any plaintext.
        logger.warning(
            "[SYNC] Could not decrypt credentials for sync target id=%s — "
            "refusing to build a remote client (fail-closed)",
            getattr(sync_target, "id", "?"),
        )
        raise SyncCredentialError(
            "Sync target credentials could not be decrypted; refusing to sync."
        )
    if not isinstance(creds, dict):
        raise SyncCredentialError("Sync target credentials are malformed.")

    # Fail-fast SSRF check on the base_url at construct time. This is a
    # convenience guard; the per-request transport is the authoritative control.
    validate_outbound_url(base_url, get_ssrf_mode())

    settings = _settings_from_credentials(base_url, creds, verify=verify)

    client = DispatcharrClient(settings)
    # Swap the client's transport for the pinned SSRF transport so every request
    # the existing DispatcharrClient methods make is chokepointed. The default
    # transport is freshly constructed and has NOT opened any socket / connection
    # pool yet (httpx is lazy — the pool opens on first request), so replacing it
    # in place leaks nothing. From here on, ``client``'s sole outbound path is the
    # pinned SSRF transport.
    client._client._transport = _PinnedSSRFTransport(verify=verify)

    logger.info(
        "[SYNC] Built SSRF-guarded remote client for sync target id=%s "
        "(auth_method=%s, tls_verify=%s)",
        getattr(sync_target, "id", "?"), settings.auth_method, verify,
    )
    return client


# ---------------------------------------------------------------------------
# Credential-freshness (D5) — mirrors dbas_backup _check_credential_freshness.
# ---------------------------------------------------------------------------
def sync_freshness_reason(
    session, target_id: int, captured_version: Optional[int]
) -> Optional[str]:
    """Re-read a ``SyncTarget`` FRESH and return an abort reason, or None.

    Mirrors ``dbas_backup`` ``_check_credential_freshness`` / ``_freshness_reason``
    (D5). The ``credential_version`` is captured at enqueue (task-config JSON, no
    new column) and re-checked here at execute time, alongside a
    ``token_revoked_at`` hard-stop and an ``enabled`` check. A non-None return is
    a fail-closed abort signal — the caller WARNs + journals
    (``category='sync_outbound'``) and skips the cycle.

    Args:
        session: an open DB session (the caller owns its lifecycle).
        target_id: the SyncTarget id bound to the scheduled sync.
        captured_version: the ``credential_version`` captured at enqueue. ``None``
            means "no version was captured" and the version check is skipped
            (mirrors the per-target ``_freshness_reason`` behaviour).

    Returns:
        A human-readable abort reason, or ``None`` to proceed.
    """
    from export_models import SyncTarget

    target = (
        session.query(SyncTarget).filter(SyncTarget.id == target_id).first()
    )
    if target is None:
        return "sync target %s no longer exists" % target_id
    if not target.enabled:
        return "sync target '%s' (id=%s) is disabled" % (target.name, target.id)
    if target.token_revoked_at is not None:
        return "credentials for sync target '%s' (id=%s) were revoked" % (
            target.name, target.id,
        )
    if captured_version is not None and target.credential_version != captured_version:
        return (
            "credentials for sync target '%s' (id=%s) were rotated "
            "(configured v%s, current v%s)" % (
                target.name, target.id, captured_version, target.credential_version,
            )
        )
    return None


# ---------------------------------------------------------------------------
# Per-cycle insecure audit (D6) — mirrors dbas_backup _audit_insecure.
# ---------------------------------------------------------------------------
def audit_insecure_cycle(target_id, target_name) -> None:
    """Write the per-cycle ``sync_outbound`` audit row for an insecure sync (D6).

    An ``insecure=true`` SyncTarget skips TLS verification, so EVERY sync cycle
    against it must leave an auditable ``tls_verified=false`` trail (the recurring
    exposure the threat model bounds). Mirrors ``dbas_backup`` ``_audit_insecure``;
    best-effort (a journal failure must not crash the sync).
    """
    try:
        journal.log_entry(
            category="sync_outbound",
            action_type="sync_insecure_tls",
            entity_name=target_name or ("sync target %s" % target_id),
            entity_id=target_id,
            description=(
                "TLS verification SKIPPED (insecure=true, tls_verified=false) for "
                "sync target '%s' (id=%s) on a cross-instance sync cycle" % (
                    target_name, target_id,
                )
            ),
            user_initiated=False,
        )
    except Exception as e:  # pragma: no cover — journal best-effort
        logger.warning("[SYNC] Failed to journal insecure audit: %s", e)
