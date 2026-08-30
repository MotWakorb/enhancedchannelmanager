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

from config import MCP_SERVICE_FILE, get_settings  # get_settings retained for test/back-compat patch seams
from auth.mcp_service import load_mcp_service_credentials
from database import get_session
from models import User
from .tokens import decode_token, TokenExpiredError, InvalidTokenError, TokenRevokedError
from .settings import get_auth_settings


logger = logging.getLogger(__name__)

# Synthetic, non-persisted user id for the private MCP sidecar principal.
# Negative so it can never collide with a real autoincrement users.id row
# (SQLite/Postgres autoincrement is always positive) and so any accidental
# FK write would fail loudly rather than silently corrupting a real row.
# The only place a principal's id reaches the DB is the dedup audit
# ``actor_token_id`` column (models.PendingMergeJournal), which is free-text
# (``Text``), not a foreign key — see routers/channel_merges.py:_actor_token_id.
MCP_SERVICE_PRINCIPAL_ID = -1
MCP_SERVICE_PRINCIPAL_USERNAME = "mcp-service"


def _is_mcp_service_token(token: str) -> bool:
    """Return True iff ``token`` is the private sidecar backend credential.

    The operator-configured ``mcp_api_key`` is accepted only by the MCP
    listener and never authenticates here. The global middleware accepts the
    owner-only sidecar projection; this recognizes it at the route
    dependency layer too so that JWT route-guards (``RequireAuthIfEnabled``
    / ``RequireAdminIfEnabled``) stop rejecting it as a malformed JWT.

    Uses :func:`hmac.compare_digest` (constant-time) rather than ``==`` to
    avoid a timing oracle on the service key. Missing or empty projected
    credentials never match.

    Never raises on an unusable projection. ``get_current_user`` calls this
    before the JWT decode for every token-bearing request, and 16 router
    modules depend on it, so a projection ECM cannot read or write must
    degrade the sidecar principal here exactly as it does in
    ``main.auth_middleware`` — otherwise an operator holding a valid JWT gets
    a 500 from the route dependency while ``/api/health`` (exempt from auth,
    and what the container HEALTHCHECK probes) still reports healthy
    (…-04c0u.8). ``None`` means no sidecar principal exists; the empty string
    it degrades to is rejected by the truthiness guard below rather than
    reaching ``compare_digest``, so "no credential" can never authenticate an
    empty bearer token. Pinned by
    ``tests/auth/test_mcp_sidecar_boundary.py::TestBrokenProjectionAtTheRouteDependencySeam``.
    """
    if not token:
        return False
    credentials = load_mcp_service_credentials(MCP_SERVICE_FILE)
    private_key = credentials.backend_key if credentials else ""
    # The public key comparison remains at the dependency seam for direct
    # dependency callers and legacy in-process integrations. Every HTTP API
    # request crosses main.auth_middleware first, which explicitly refuses the
    # public key before route dispatch, so it can never become this principal
    # over the network.
    public_key = get_settings().mcp_api_key
    return bool(
        (private_key and hmac.compare_digest(token, private_key))
        or (public_key and hmac.compare_digest(token, public_key))
    )


def _build_mcp_service_principal() -> User:
    """Construct the admin-equivalent, non-persisted MCP service principal.

    Returned to callers of ``get_current_user`` when the private backend key is
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


def token_matches_user_auth_epoch(payload: dict, user: User) -> bool:
    """Return whether an access-token epoch is current for ``user``.

    Tokens issued before the epoch claim was introduced are epoch zero. Using
    one shared comparison keeps dependency-guarded and middleware-only routes
    on the same invalidation policy.
    """
    token_epoch = payload.get("auth_epoch", 0)
    return type(token_epoch) is int and token_epoch == user.auth_epoch


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
    # token". The global auth_middleware first restricts this key through the
    # deny-by-default method+route capability matrix; honoring it here lets an
    # explicitly admitted request satisfy ordinary route dependencies.
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

    if not token_matches_user_auth_epoch(payload, user):
        raise AuthenticationError("Token has been invalidated")

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


async def require_authenticated_human_admin(
    request: Request,
    session: Session = Depends(get_session),
) -> User:
    """Require a persisted human admin regardless of the general auth mode.

    Diagnostic artifacts can contain operational data that must never become
    public merely because ``require_auth`` is disabled. The MCP principal is
    intentionally admin-equivalent for automation, but it is not a stable
    operator identity and cannot own or download a debug artifact.
    """
    user = await get_current_user(request, session)
    if not user.is_admin:
        raise PermissionError("Admin access required")
    if is_mcp_service_principal(user):
        raise PermissionError(
            "Debug artifacts must be created and downloaded by a human admin"
        )
    if not isinstance(user.id, int) or user.id <= 0:
        raise PermissionError("Debug artifacts require a stable human identity")
    return user


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


def instance_has_operator_identity(session: Session) -> bool:
    """Report whether the instance holds an identity a caller could take over.

    A user row is the primary signal: it becomes true the instant
    ``/api/auth/setup`` creates the first admin. ``setup_complete`` is OR'd in
    as a second, independent signal so an instance that lost its user rows but
    kept its auth settings is still treated as owned. Fails closed — an
    unreadable users table is treated as owned — because the callers of this
    predicate use it to decide whether an ANONYMOUS caller may proceed, so the
    unknown case must not be the permissive one.

    Shared by ``routers.backup._guard_initial_restore`` (bead lf29s) and by the
    ``enforce_when_auth_disabled`` branch of :func:`require_admin_if_enabled`
    (bead jy006). It lives here, in one copy, deliberately: two identical
    fail-closed security predicates in two modules is the drift defect bead
    9kwzp.9 is about, and these two must answer "is this instance owned?"
    identically or the auth-disabled posture is inconsistent between the
    restore path and the credential paths.
    """
    try:
        if session.query(User).count() > 0:
            return True
    except Exception as e:
        logger.warning(
            "[AUTH] Could not read the users table for the operator-identity "
            "check, treating the instance as owned: %s",
            e,
        )
        return True

    return bool(get_auth_settings().setup_complete)


def require_admin_if_enabled(
    *,
    reject_mcp_service_principal: bool = False,
    mcp_denial_detail: str = _MCP_DENIAL_DETAIL_DEFAULT,
    enforce_when_auth_disabled: bool = False,
    always_require_auth: bool = False,
):
    """
    Factory function to create a dependency that requires admin when auth is enabled.

    When auth is disabled (setup not complete or require_auth=False),
    the endpoint is publicly accessible. When auth is enabled, the
    caller must be an authenticated admin.

    ``enforce_when_auth_disabled`` (bead jy006, PO decision 2026-08-13;
    extended by bead 2u4e0, PO decision 2026-08-15): when True, the
    auth-disabled short-circuit above applies ONLY while the instance holds no
    operator identity. ``require_auth: false`` is a supported ECM operating
    mode and stays fully permissive for ordinary data and configuration routes.
    The flag marks the surfaces that are refused to an ANONYMOUS caller even in
    that mode.

    ``always_require_auth`` has no first-run carve-out. It is reserved for
    actions that are themselves human decisions rather than ordinary instance
    administration, so anonymous reachability can never stand in for identity.

    THE RULE. A surface carries the flag when reaching it anonymously gives the
    caller something the mode itself does not already give them — something
    that outlives the mode, or something they could not otherwise have. Two
    axes qualify today, and a new gate joins only by naming which:

    1. DURABILITY OF THE RESULTING IDENTITY (jy006). The caller walks away
       holding a credential or a key that keeps working after the operator
       turns authentication back on. A settings write, however destructive,
       does not.

         * ``POST /api/backup/restore-initial`` — replaces every admin password
           hash (gated in ``routers.backup._guard_initial_restore``, which needs
           its own copy of the rule because it must also survive a damaged
           ``setup_complete``; it shares this module's
           :func:`instance_has_operator_identity`).
         * ``POST``/``DELETE /api/settings/mcp-api-key`` — plants or destroys a
           persistent, admin-equivalent bearer credential
           (:data:`RequireHumanAdminForServiceCredential`).
         * the ``/api/tls`` certificate/key material and HTTPS lifecycle —
           installs a caller-supplied private key as the instance's TLS
           identity (:data:`RequireHumanAdminForTLSMaterial`).

    2. CREDENTIAL ORACLE (2u4e0). The route reaches the network with
       credentials ALREADY STORED on the instance, to a host the caller can
       often name, and echoes the upstream verdict back. The caller spends a
       secret they never had to learn, and reads an in-band port scan off the
       reply. That is the class beads i4qrp / 9kwzp.6 / 9kwzp.7 gated against
       the MCP service principal, and leaving it open to an anonymous caller
       gave a stranger on the LAN more reach than the automation credential.

         * the twelve connection-test routes on
           :data:`RequireHumanAdminForOutboundTest` — the ``/api/settings``
           test verbs, the Emby/Plex/Jellyfin test-connection routes, the
           alert-method and M3U-digest test sends, both ``/api/cloud-targets``
           test verbs, and ``POST /api/tls/test-dns-provider``.

    The second axis closed a shape that was incoherent inside one router: until
    2u4e0, ``POST /api/tls/test-dns-provider`` could be driven anonymously on an
    owned auth-disabled instance while ``GET /api/tls/settings``, which
    discloses the same DNS-provider credentials only MASKED, was refused. The
    cost was weighed and accepted: on such an instance a browser that is not
    signed in now gets 403 from every Test Connection button in Settings, and
    the remedy is to sign in at ``/login`` (bead p388h) rather than to change
    the mode.

    THE NO-IDENTITY CARVE-OUT IS LOAD-BEARING, not a softening. A literal
    "always require an admin" would make these routes permanently unreachable
    on an instance that runs with ``require_auth: false`` and never created a
    user — a supported headless posture — with no in-band recovery, since the
    only way to obtain an admin would be to run the setup wizard and thereby
    change the posture the operator chose. The carve-out follows the shape
    already shipped and security-reviewed under bead lf29s.

    Everything ``require_auth: false`` still permits is documented in
    ``docs/auth_middleware.md`` → "What ``require_auth: false`` permits".

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
        enforcing_over_disabled_auth = False
        if not settings.require_auth or not settings.setup_complete:
            if always_require_auth:
                enforcing_over_disabled_auth = True
            elif not enforce_when_auth_disabled:
                return None
            elif not instance_has_operator_identity(session):
                # Genuine first run, or a deliberately headless auth-disabled
                # instance that never created a user. Nothing exists here for a
                # caller to take over, and refusing would lock the operator out
                # of the only path that configures this surface.
                return None
            else:
                enforcing_over_disabled_auth = True

        # Auth is required - get the user and check admin
        try:
            user = await get_current_user(request, session)
        except HTTPException:
            if enforcing_over_disabled_auth:
                # The refusal an operator most wants to see in the log: an
                # anonymous caller reaching for an identity primitive or a
                # stored-credential probe on an instance whose owner turned
                # authentication off. Also the line that explains a Test
                # Connection button returning 403 in that mode (bead 2u4e0).
                logger.warning(
                    "[AUTH] Refused an unauthenticated request to a guarded "
                    "surface on an auth-disabled instance that already has an "
                    "operator identity: %s %s",
                    request.method,
                    request.url.path,
                )
            raise
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
#
# bead 2u4e0 (PO decision 2026-08-15) — the gate now also ENFORCES WHEN
# ``require_auth`` IS FALSE, once the instance has an operator identity. It
# carries twelve routes, and jy006 left every one of them open because the
# decision it implemented named three identity primitives and none of these.
# The shape that produced was incoherent inside a single router: an anonymous
# caller on an owned auth-disabled instance could POST /api/tls/
# test-dns-provider, exercising the stored DNS-provider credentials and
# enumerating the operator's hosted zone, while GET /api/tls/settings, which
# discloses those same credentials only MASKED, was refused.
#
# The jy006 axis was DURABILITY OF THE RESULTING IDENTITY. This family joins on
# a second axis the PO accepted alongside it: each route is a CREDENTIAL ORACLE
# — it reaches the network with credentials ALREADY STORED on the instance, to
# a host the caller can often name, and echoes the upstream verdict back. The
# caller never has to learn a secret to spend it, and the reply is an in-band
# port scanner besides. That is the class beads i4qrp, 9kwzp.6 and 9kwzp.7
# gated against the MCP principal; leaving it ungated against an ANONYMOUS
# caller gave the automation credential less reach than a stranger on the LAN.
#
# THE OPERATOR COST IS REAL AND WAS ACCEPTED. On an auth-disabled instance that
# HAS a user account, a browser that is not signed in now gets 403 from every
# Test Connection button in Settings. The remedy is in-band and already
# documented: browse to ``/login`` and sign in (bead p388h), which needs no
# change to ``require_auth``. Instances that never created a user — the
# supported headless posture — are untouched by the no-identity carve-out in
# :func:`require_admin_if_enabled`.
RequireHumanAdminForOutboundTest = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        enforce_when_auth_disabled=True,
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
#
# bead jy006 — one of the three gates that ENFORCES EVEN WHEN ``require_auth``
# IS FALSE, once the instance has an operator identity. The key this route
# mints is a persistent, admin-equivalent bearer credential that the global
# middleware accepts across the whole ``/api/`` surface, so an anonymous LAN
# caller minting one on an auth-disabled instance walks away with an identity
# that OUTLIVES the operator turning authentication back on. That is the
# property that distinguishes it from the rest of the auth-disabled surface,
# which is merely open while the mode is on.
RequireHumanAdminForServiceCredential = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        enforce_when_auth_disabled=True,
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
#
# bead jy006 — the third gate that ENFORCES EVEN WHEN ``require_auth`` IS
# FALSE, once the instance has an operator identity. ``upload-cert`` and the
# ACME trio install a private key that becomes the instance's TLS identity, so
# an anonymous caller reaching them on an auth-disabled instance can serve
# their own key to every client of that instance from then on — again an
# identity that OUTLIVES the operator turning authentication back on.
#
# The enforcement is applied to this gate WHOLESALE — all ten routes, including
# ``GET /settings``, ``DELETE /certificate`` and the https trio — rather than
# to the key-install subset alone. That is the "coarse on purpose" trade
# ``tests/test_admin_gate_inventory.py`` documents for this family: splitting
# it would need a second constant with its own 403 body and its own inventory
# group, and it costs the operator nothing, because
# ``TLSSettingsSection.tsx`` makes NO API call at all when it renders
# non-admin (``useEffect`` returns at ``if (!isAdmin) return``), which is
# exactly the state an auth-disabled instance is already in — ``useAuth``
# never resolves a user when ``require_auth`` is false, so that section is
# handed ``isAdmin={user?.is_admin ?? false}``. Nothing that works today stops
# working.
RequireHumanAdminForTLSMaterial = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        enforce_when_auth_disabled=True,
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


# NO OUTBOUND-DESTINATION GATE, DELIBERATELY. There was one — this bead built
# ``RequireHumanAdminForOutboundDestination`` for the write halves of
# ``/api/cloud-targets`` and ``/api/sync-targets`` — and the PO removed it
# before it shipped. Recorded here rather than deleted silently, because the
# argument for it is still sound and the next reader will otherwise re-derive
# it: a write to either router names the host a SCHEDULED job then sends to
# (``tasks/dbas_backup.py`` PUTs the operator's archive to a cloud target;
# ``tasks/dbas_sync.py`` pushes config to a sync target), stores the
# credentials that job authenticates with, and sets ``insecure``, which turns
# off TLS verification for that traffic — so an update repoints a flow the
# operator already configured.
#
# It was removed because bead jcj0f ships six MCP tools over exactly those
# routes and denying the principal broke all six. The capability was judged
# worth the residual. Both routers therefore run on plain
# ``RequireAdminIfEnabled`` end to end: admin required, MCP principal
# admitted. What still refuses the principal on that surface is the outbound
# POLICY write (``RequireHumanAdminForOutboundPolicy``) and both cloud-target
# ``/test`` verbs (``RequireHumanAdminForOutboundTest``).
#
# If a future change makes a destination write reachable by something weaker
# than an admin, or removes those tools, this is the gate to bring back.


# bead 9kwzp.10 item 4 — admin gate for the alert-method surface
# (``/api/alert-methods``), which carried NO route dependency on any of its
# six non-test routes.
#
# Scope: the four routes NO MCP tool calls — ``POST ""``, ``GET /{id}``,
# ``PATCH /{id}`` and ``DELETE /{id}``. An alert method holds the Discord
# webhook URL, the Telegram bot token and the SMTP password in
# ``AlertMethod.config``; writing one repoints where ECM's own alerts go, and
# the reads return that blob verbatim. Denying the principal on these four
# costs the sidecar nothing, because it has no tool for any of them.
#
# ``GET ""`` is NOT here: the shipped ``list_alert_methods`` tool needs it, so
# it runs on plain ``RequireAdminIfEnabled`` and the automation credential can
# read every method through it — including everything ``GET /{id}`` returns,
# for every method. So this gate never contained the disclosure; it holds the
# WRITE half and marks the read intent.
#
# What contains the disclosure is the RESPONSE, and it landed under bead
# enhancedchannelmanager-9kwzp.13: both read handlers now serialize through
# ``models.AlertMethod.to_dict(include_sensitive=False)``, so the webhook URL,
# the bot token and the SMTP password come back as ``'********'`` to every
# caller including this one. This gate is unchanged by that and still worth
# holding — the three writes here can repoint or end the operator's alerts,
# which masking does nothing about.
#
# Its own body rather than a reuse: ``RequireHumanAdminForOutboundTest``
# already gates ``POST /api/alert-methods/{id}/test`` in this same router and
# correctly names the send; a caller refused on a WRITE being told it cannot
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


# Profile-conflict acceptance resolves contradictory upstream settings by
# choosing which source rows become authoritative. That is an operator policy
# decision rather than unattended channel maintenance.
RequireHumanAdminForOperatorDecision = Depends(
    require_admin_if_enabled(
        reject_mcp_service_principal=True,
        always_require_auth=True,
        mcp_denial_detail=(
            "The MCP service principal cannot review or accept profile "
            "conflicts. Choosing the authoritative channel profiles must be "
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
