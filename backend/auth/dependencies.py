"""
FastAPI authentication dependencies.

Provides dependency injection functions for extracting and validating
user authentication from requests.
"""
import hmac
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from config import get_settings
from database import get_session
from models import User
from .tokens import decode_token, TokenExpiredError, InvalidTokenError, TokenRevokedError
from .settings import get_auth_settings


logger = logging.getLogger(__name__)

# Synthetic, non-persisted user id for the static-MCP-key service principal.
# Negative so it can never collide with a real autoincrement users.id row
# (SQLite/Postgres autoincrement is always positive) and so any accidental
# FK write would fail loudly rather than silently corrupting a real row.
# The only place a principal's id reaches the DB is the dedup audit
# ``actor_token_id`` column (models.PendingMergeJournal), which is free-text
# (``Text``), not a foreign key — see routers/channel_merges.py:_actor_token_id.
MCP_SERVICE_PRINCIPAL_ID = -1
MCP_SERVICE_PRINCIPAL_USERNAME = "mcp-service"


def _is_mcp_service_token(token: str) -> bool:
    """Return True iff ``token`` is the configured static MCP API key.

    The static MCP key is a permanent, operator-set bearer credential
    (threat model EP2). The global ``auth_middleware`` in main.py already
    accepts it for any ``/api/*`` path; this recognizes it at the route
    dependency layer too so that JWT route-guards (``RequireAuthIfEnabled``
    / ``RequireAdminIfEnabled``) stop rejecting it as a malformed JWT.

    Uses :func:`hmac.compare_digest` (constant-time) rather than ``==`` to
    avoid a timing oracle on the static key (bd-i3axt LOW-1). An empty or
    unset ``mcp_api_key`` never matches — including against an empty token.
    """
    if not token:
        return False
    expected = get_settings().mcp_api_key
    if not expected:
        return False
    return hmac.compare_digest(token, expected)


def _build_mcp_service_principal() -> User:
    """Construct the admin-equivalent, non-persisted MCP service principal.

    Returned to callers of ``get_current_user`` when the static MCP key is
    presented. It is a transient ``User`` instance — never added to a
    session, never committed. Callers only read ``.id``, ``.username``,
    ``.is_admin`` and ``.is_active``.
    """
    return User(
        id=MCP_SERVICE_PRINCIPAL_ID,
        username=MCP_SERVICE_PRINCIPAL_USERNAME,
        is_admin=True,
        is_active=True,
        auth_provider="mcp",
    )


def is_mcp_service_principal(user: User) -> bool:
    """Return True iff ``user`` is the transient, non-persisted MCP principal.

    The MCP service principal (built by :func:`_build_mcp_service_principal`)
    is a detached ``User`` instance that was never added to a session — it
    carries ``auth_provider == "mcp"`` and the synthetic
    :data:`MCP_SERVICE_PRINCIPAL_ID`. We key off both so a real DB row that
    somehow had ``auth_provider == "mcp"`` (it never should) still wouldn't be
    mistaken for the transient principal, and vice versa.
    """
    return getattr(user, "auth_provider", None) == "mcp" or (
        getattr(user, "id", None) == MCP_SERVICE_PRINCIPAL_ID
    )


def reject_mcp_service_principal_mutation(user: User) -> None:
    """Reject self-mutation routes invoked with the MCP service principal.

    The MCP principal is transient (never persisted), so ORM operations like
    ``session.refresh(current_user)`` raise an opaque 500. It also has no
    business mutating an ECM user account: the static MCP key is an
    admin-equivalent *service* credential, not a user identity. Self-mutation
    routes (e.g. ``PUT /api/auth/me``, ``POST /api/auth/change-password``)
    call this to fail fast with a clean 403 instead (bd-1wq7z.24 (c)).
    """
    if is_mcp_service_principal(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MCP service principal cannot modify a user account",
        )


class AuthenticationError(HTTPException):
    """Authentication failed."""
    def __init__(self, detail: str = "Could not validate credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )


class PermissionError(HTTPException):
    """User lacks required permissions."""
    def __init__(self, detail: str = "Permission denied"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail,
        )


def get_token_from_request(request: Request) -> Optional[str]:
    """
    Extract JWT token from request.

    Checks for token in the following order:
    1. access_token cookie (httpOnly cookie for web clients)
    2. Authorization header (Bearer token for API clients)

    Args:
        request: The FastAPI request object.

    Returns:
        The JWT token string or None if not found.
    """
    # Check cookies first (web clients)
    token = request.cookies.get("access_token")
    if token:
        return token

    # Check Authorization header (API clients)
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:]  # Remove "Bearer " prefix

    return None


def decode_token_safe(token: str) -> Optional[dict]:
    """Decode a JWT token without raising exceptions. Returns payload or None."""
    try:
        return decode_token(token)
    except (TokenExpiredError, InvalidTokenError, TokenRevokedError):
        return None


def get_refresh_token_from_request(request: Request) -> Optional[str]:
    """
    Extract refresh token from request.

    Checks for token in the following order:
    1. refresh_token cookie
    2. X-Refresh-Token header

    Args:
        request: The FastAPI request object.

    Returns:
        The refresh token string or None if not found.
    """
    # Check cookies first
    token = request.cookies.get("refresh_token")
    if token:
        return token

    # Check custom header
    token = request.headers.get("X-Refresh-Token")
    if token:
        return token

    return None


async def get_current_user(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """
    FastAPI dependency to get the current authenticated user.

    Extracts and validates the JWT token, then loads the user from database.

    Args:
        request: The FastAPI request object.
        session: Database session (injected).

    Returns:
        The authenticated User object.

    Raises:
        AuthenticationError: If token is missing, invalid, or user not found.
    """
    token = get_token_from_request(request)
    if not token:
        raise AuthenticationError("Not authenticated")

    # Static MCP API key: recognize it BEFORE attempting JWT decode. The key
    # is not a 3-part JWT, so decode_token() would reject it as "Malformed
    # token". The global auth_middleware already grants this key full /api/*
    # access; honoring it here aligns the route-dependency layer with that
    # grant and unblocks JWT-guarded routes (dedup, backup, add_stream).
    if _is_mcp_service_token(token):
        logger.debug("[AUTH] Authenticated request via static MCP service key")
        return _build_mcp_service_principal()

    try:
        payload = decode_token(token)
    except TokenExpiredError:
        raise AuthenticationError("Token has expired")
    except TokenRevokedError:
        raise AuthenticationError("Token has been revoked")
    except InvalidTokenError as e:
        raise AuthenticationError(f"Invalid token: {str(e)}")

    # Get user ID from token payload
    user_id = payload.get("sub")
    if user_id is None:
        raise AuthenticationError("Invalid token payload")

    # Load user from database
    user = session.query(User).filter(User.id == user_id).first()
    if user is None:
        raise AuthenticationError("User not found")

    # Check if user is active
    if not user.is_active:
        raise AuthenticationError("User account is disabled")

    return user


async def get_current_active_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    FastAPI dependency to require an admin user.

    Args:
        current_user: The authenticated user (injected).

    Returns:
        The authenticated admin User object.

    Raises:
        PermissionError: If user is not an admin.
    """
    if not current_user.is_admin:
        raise PermissionError("Admin access required")
    return current_user


def require_auth_if_enabled():
    """
    Factory function to create a dependency that checks auth if enabled.

    This allows endpoints to optionally require authentication based
    on the auth settings. When auth is disabled (setup not complete or
    require_auth=False), the endpoint is publicly accessible.

    Returns:
        A dependency function that returns the user or None.
    """
    async def check_auth(
        request: Request,
        session: Session = Depends(get_session),
    ) -> Optional[User]:
        settings = get_auth_settings()

        # If auth not required or setup not complete, allow anonymous access
        if not settings.require_auth or not settings.setup_complete:
            return None

        # Auth is required - get the user
        return await get_current_user(request, session)

    return check_auth


# Pre-built dependency for common use
RequireAuthIfEnabled = Depends(require_auth_if_enabled())


_MCP_DENIAL_DETAIL_DEFAULT = (
    "The MCP service principal cannot perform this operation. It is a "
    "channel/stream automation credential, not an operator identity, and this "
    "route must be driven by a human operator admin."
)


def require_admin_if_enabled(
    *,
    reject_mcp_service_principal: bool = False,
    mcp_denial_detail: str = _MCP_DENIAL_DETAIL_DEFAULT,
):
    """
    Factory function to create a dependency that requires admin when auth is enabled.

    When auth is disabled (setup not complete or require_auth=False),
    the endpoint is publicly accessible. When auth is enabled, the
    caller must be an authenticated admin.

    ``reject_mcp_service_principal`` (kgz3k / bead 6n76m): when True, the static
    MCP service principal is DENIED even though it carries ``is_admin=True``.
    The MCP key is a channel/stream automation credential, not an operator
    identity — it has no business rewriting outbound base URLs or secrets. This
    mirrors the field-level carve-out ``routers.settings._resolve_settings_admin``
    applies to POST /api/settings, extending it to the backup-restore endpoints
    that write the settings blob WHOLESALE (and so would otherwise let the MCP
    key flip every admin-only field via a restore, bypassing the settings gate).
    The default (False) preserves the historical behaviour for every other
    admin-gated route the MCP key is meant to reach (channel management, etc.).

    ``mcp_denial_detail`` is the 403 body for that MCP rejection. It is
    per-call-site because the message is operator-facing: a caller refused on
    ``/api/settings/test-discord`` being told it "cannot perform backup
    restore" sends incident triage the wrong way. Every message names the MCP
    principal so a caller can tell this refusal apart from a plain non-admin
    one.
    """
    async def check_admin(
        request: Request,
        session: Session = Depends(get_session),
    ) -> Optional[User]:
        settings = get_auth_settings()

        # If auth not required or setup not complete, allow anonymous access
        if not settings.require_auth or not settings.setup_complete:
            return None

        # Auth is required - get the user and check admin
        user = await get_current_user(request, session)
        if not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required"
            )
        # kgz3k / 6n76m: the MCP service principal is is_admin=True but must be
        # denied the settings-write path (restore rewrites the whole settings
        # blob). Reject it AFTER the is_admin check so a non-admin still gets the
        # generic "Admin access required" message and the MCP-specific reason is
        # only disclosed to a caller that WAS admin-equivalent.
        if reject_mcp_service_principal and is_mcp_service_principal(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=mcp_denial_detail,
            )
        return user

    return check_admin


RequireAdminIfEnabled = Depends(require_admin_if_enabled())

# kgz3k / bead 6n76m — admin gate that ALSO rejects the static MCP service
# principal. Use for endpoints that write the settings blob wholesale (the
# backup-restore paths), which would otherwise let the MCP key bypass the
# field-level admin gate ``routers.settings._resolve_settings_admin`` enforces
# on POST /api/settings.
RequireHumanAdminIfEnabled = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot perform backup restore. "
            "Restore rewrites admin-only settings (outbound URLs, "
            "secrets) and must be driven by a human operator admin."
        ),
    )
)

# bead i4qrp — admin gate for the credential-carrying connection-test endpoints
# (POST /api/settings/test, /test-smtp, /test-discord, /test-telegram). Those
# reach the network with operator-supplied or STORED credentials and echo the
# upstream result back, which is a status-code oracle and an in-band port
# scanner. The plain ``RequireAdminIfEnabled`` would ADMIT the MCP principal
# (``_build_mcp_service_principal`` sets is_admin=True) and so would leave the
# exact principal kgz3k denies on the settings WRITE path able to drive the
# probe.
RequireHumanAdminForOutboundTest = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot run connection tests. A "
            "connection test sends credentials to a caller-named host and "
            "reports the upstream result; it must be driven by a human "
            "operator admin."
        ),
    )
)

# bead 9kwzp.8 — admin gate for the static MCP key's own lifecycle
# (POST/DELETE /api/settings/mcp-api-key). Same behaviour as the two gates
# above, different reason, hence its own denial detail rather than a reuse of
# ``RequireHumanAdminForOutboundTest``: nothing here reaches the network, so a
# 403 body naming connection tests would send incident triage at a probe this
# route never makes.
#
# The reason the MCP principal is denied is that it would otherwise be the
# bearer rotating and revoking its OWN credential. Minting returns the new key
# in the response body, so the caller is the only party that learns it: a
# holder of a leaked key could mint a successor that survives the operator's
# rotation, and revoke is a self-inflicted outage on the sidecar with no
# operator in the loop. Credential lifecycle belongs to the human operator who
# owns the credential, which is the same principle
# :func:`reject_mcp_service_principal_mutation` applies to the self-mutation
# auth routes.
RequireHumanAdminForServiceCredential = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot manage the MCP API key. "
            "Rotating or revoking the key it authenticates with is a "
            "credential-lifecycle operation and must be driven by a human "
            "operator admin."
        ),
    )
)


# bead 9kwzp.11 — admin gate for the TLS certificate/key material and the
# HTTPS termination lifecycle (``tls/routes.py``: configure, request-cert,
# complete-challenge, upload-cert, renew, certificate DELETE, the https
# start/stop/restart trio, and the settings read that emits masked DNS
# credentials). That router carried NO route dependency at all, so every one of
# those was reachable by any authenticated non-admin AND by this principal.
#
# A fourth constant rather than a reuse of the three above, for the reason
# ``mcp_denial_detail`` is a per-call-site parameter at all: none of the
# existing bodies describes this surface. "cannot perform backup restore" and
# "cannot manage the MCP API key" both name a subsystem these routes never
# touch, and "cannot run connection tests" names a probe that only ONE route in
# the router makes (``/test-dns-provider``, which correctly keeps
# ``RequireHumanAdminForOutboundTest``). A caller refused on an upload of
# certificate material must not be pointed at any of the three.
#
# The principal is denied because TLS material is operator infrastructure, not
# channel/stream automation: the router accepts caller-supplied certificate and
# private-key material and serves it, destroys the operator's own material,
# writes the plaintext DNS-provider credentials in ``/config/tls_settings.json``
# (bead 2owpi), and starts or stops the operator's HTTPS listener. None of that
# is work the MCP sidecar exists to do — it exposes no TLS tool — and the
# availability half means a leaked key could take the operator's own HTTPS
# termination down.
RequireHumanAdminForTLSMaterial = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot manage TLS. Issuing, uploading, "
            "renewing or deleting certificate material, writing DNS-provider "
            "credentials, and starting or stopping HTTPS termination are "
            "operator infrastructure operations and must be driven by a human "
            "operator admin."
        ),
    )
)


# bead 9kwzp.10 item 1 — admin gate for the outbound-policy write
# (``PATCH /api/settings/security``, the only field-specific writer of
# ``ssrf_outbound_mode``; the wholesale-config restore paths can persist the
# same field without a source-level assignment, which is why they are
# human-admin too).
#
# A fifth constant rather than a reuse, for the reason 9kwzp.8 and 9kwzp.11
# each needed one: none of the four existing bodies describes this surface.
# "backup restore", "MCP API key", "connection tests" and "TLS" all name a
# subsystem this route never touches, and this route is not itself a probe —
# it is the POLICY the probes are measured against.
#
# The principal is denied because this setting decides which hosts every
# outbound path in ECM may reach. Widening it from ``public_only`` to
# ``lan_friendly`` re-admits RFC1918 and loopback for the sinks that beads
# i4qrp, 9kwzp.6 and 9kwzp.7 gated, for the sync and backup-upload
# destinations, and for the runtime pollers. Gating the sinks while leaving
# their policy writable by the same principal is a partial control: the
# principal cannot drive the probe, but it can move the fence the probe would
# have been measured against. Note the always-on half (link-local / IMDS /
# ULA / CGNAT / multicast, ``security/ssrf.py``) is NOT operator-togglable and
# is unaffected either way.
RequireHumanAdminForOutboundPolicy = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot change the outbound-policy "
            "mode. That setting decides which hosts every outbound path in "
            "ECM may reach, so widening it is a security-policy change and "
            "must be driven by a human operator admin."
        ),
    )
)


# bead 9kwzp.10 item 4 / the sync-target half of item 3 — admin gate for the
# WRITE half of the two outbound-destination routers
# (``/api/cloud-targets`` and ``/api/sync-targets``).
#
# A destination is not a probe and not a settings blob, so none of the five
# bodies above fits: "connection tests" names the ``/test`` verbs these
# routers already carry (correctly, via ``RequireHumanAdminForOutboundTest``),
# and a caller refused on a CRUD write being told it cannot run a connection
# test would send triage at the wrong half of its own router.
#
# The principal is denied because writing a destination REPOINTS scheduled,
# credential-bearing outbound traffic. A cloud target is where
# ``tasks/dbas_backup.py`` PUTs the operator's archive; a sync target is the
# remote instance ``tasks/dbas_sync.py`` pushes config to on a timer, and
# creating one registers its ``dbas_sync_<id>`` task. Both accept a
# caller-named host, store credentials, and expose an ``insecure`` flag that
# turns off TLS verification for that traffic. That is the same shape kgz3k
# denies this principal on ``POST /api/settings`` — rewriting an outbound base
# URL a background job will then contact — deferred onto a schedule rather
# than issued in-request, which makes it quieter, not safer: no operator sees
# a result and the redirect repeats every cycle.
#
# Encryption at rest and the last-4 masking on responses bound DISCLOSURE of a
# stored credential; they do nothing about redirection, replacement or the TLS
# downgrade, which is why the READ half of both routers keeps the plain admin
# tier and only the writes are here.
RequireHumanAdminForOutboundDestination = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot create, change or delete an "
            "outbound destination. A backup-upload target or sync target "
            "names the host ECM's scheduled jobs then send to, and carries "
            "the credentials and TLS-verification flag they send under, so "
            "writing one must be driven by a human operator admin."
        ),
    )
)


# bead 9kwzp.10 item 4 — admin gate for the alert-method surface
# (``/api/alert-methods``), which carried NO route dependency on any of its
# six non-test routes.
#
# The principal is denied on both halves, and the READ half is the sharper
# one: ``list_alert_methods`` and ``get_alert_method`` return
# ``AlertMethod.config`` verbatim, and that blob holds the Discord webhook
# URL, the Telegram bot token and the SMTP password. Those are the exact three
# families bead 9ej7f withheld from this principal on ``GET /api/settings``
# and kgz3k denies it on the settings WRITE — handed out unmasked through a
# second table. A field you may not write, you may not read.
#
# Its own body rather than a reuse: ``RequireHumanAdminForOutboundTest``
# already gates ``POST /api/alert-methods/{id}/test`` in this same router and
# correctly names the send; a caller refused on a LIST being told it cannot
# run a connection test would describe the neighbouring route, not this one.
RequireHumanAdminForNotificationCredential = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot read or write alert methods. "
            "An alert method holds the notification credentials ECM sends "
            "under (webhook URL, bot token, SMTP password), so both reading "
            "and changing one must be driven by a human operator admin."
        ),
    )
)


# bead 9kwzp.12 — admin gate for ``POST /api/settings/reset-stats``, which
# carried no dependency at all and deletes every row of seven statistics
# tables.
#
# Decided on its own merits rather than by copying the sibling this bead was
# split from. ``POST /api/settings/restart-services`` (9kwzp.6) kept the PLAIN
# admin tier because it rebuilds background services from already-saved
# settings — work a settings write schedules for itself anyway, so denying it
# would deny a restart the principal can already trigger indirectly. Nothing
# about reset-stats is recoverable that way: the seven tables are the
# operator's own watch, bandwidth, popularity, telemetry and client-connection
# history, there is no compensating write, no rollback ledger, and no other
# route re-derives them. An automation credential that can silently erase the
# observability record is a credential that can erase the evidence of its own
# activity, which is why this one goes the other way.
#
# Its own body for the usual reason: nothing here reaches the network, writes
# the settings blob, touches the MCP key or touches TLS.
RequireHumanAdminForStatisticsReset = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        mcp_denial_detail=(
            "The MCP service principal cannot reset statistics. Clearing the "
            "channel, stream, bandwidth, popularity and telemetry history is "
            "an irreversible destructive operation with no undo and must be "
            "driven by a human operator admin."
        ),
    )
)


def resolve_is_mcp_service_principal_if_enabled():
    """Factory: resolve whether the caller IS the MCP principal, WITHOUT rejecting.

    bead 9kwzp.10 item 2 (PR #855 review). Sibling of
    :func:`resolve_is_admin_if_enabled`: it answers a question and lets the
    handler decide, for the one case where the verdict depends on something a
    route dependency cannot see. ``POST /api/backup/restore-dbas-saved``
    refuses this principal the APPLY and admits it the counts-only preview,
    and ``confirm_apply`` lives in the request body, so a dependency would have
    to consume the body to read it.

    It lives HERE rather than in the router deliberately. The router's
    conditional refusal must no-op in setup mode under EXACTLY the same
    condition as the gate stacked above it; a private copy of that check in a
    router is one refactor away from drifting from the gate it qualifies.
    Returns False whenever ``require_auth`` is false or setup is incomplete,
    which is the same early return every gate in this module makes.
    """
    async def check_mcp(
        request: Request,
        session: Session = Depends(get_session),
    ) -> bool:
        settings = get_auth_settings()
        if not settings.require_auth or not settings.setup_complete:
            return False
        user = await get_current_user(request, session)
        return is_mcp_service_principal(user)

    return check_mcp


ResolveIsMcpServicePrincipalIfEnabled = Depends(
    resolve_is_mcp_service_principal_if_enabled()
)


def resolve_is_admin_if_enabled():
    """Factory: resolve whether the caller is privileged, WITHOUT rejecting.

    Mirrors :func:`require_admin_if_enabled` exactly for the auth-disabled
    (setup-incomplete / ``require_auth=False``) case — it returns ``True`` so
    behaviour is identical to the rest of the app in setup mode. The difference
    is that an authenticated **non-admin** does NOT get a 403 here; the caller
    receives ``False`` and decides what to do.

    This is for endpoints that are reachable by ordinary users for ordinary
    work but must gate a *subset* of their behaviour to admins (e.g. the
    generic task-run endpoint, which serves user-triggerable tasks but must
    refuse privileged task ids for non-admins). It lets the handler enforce the
    admin requirement only for the privileged path while leaving ordinary
    behaviour unchanged.

    Returns:
        A dependency that yields ``True`` when the caller is an admin (or auth
        is disabled) and ``False`` when the caller is an authenticated
        non-admin. A missing/invalid token in auth-enabled mode still raises
        via :func:`get_current_user` (the endpoint is not anonymous).
    """
    async def check(
        request: Request,
        session: Session = Depends(get_session),
    ) -> bool:
        settings = get_auth_settings()

        # Auth disabled (setup mode) — treat as privileged, exactly like
        # RequireAdminIfEnabled, so setup-mode behaviour is unchanged.
        if not settings.require_auth or not settings.setup_complete:
            return True

        user = await get_current_user(request, session)
        return bool(user.is_admin)

    return check


ResolveIsAdminIfEnabled = Depends(resolve_is_admin_if_enabled())
