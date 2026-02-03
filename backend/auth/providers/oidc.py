"""
OpenID Connect (OIDC) authentication provider.

Implements OIDC authorization code flow with PKCE for secure authentication.
Supports providers like Authentik, Keycloak, Auth0, Okta, Google, and Azure AD.
"""
import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

import httpx
from authlib.jose import jwt
from authlib.jose.errors import JoseError

from auth.settings import OIDCSettings


logger = logging.getLogger(__name__)


# =============================================================================
# Exceptions
# =============================================================================

class OIDCError(Exception):
    """Base exception for OIDC errors."""
    pass


class OIDCDiscoveryError(OIDCError):
    """Failed to discover OIDC provider configuration."""
    pass


class OIDCTokenError(OIDCError):
    """Failed to exchange code for tokens or refresh tokens."""
    pass


class OIDCValidationError(OIDCError):
    """Token validation failed."""
    pass


class OIDCUserInfoError(OIDCError):
    """Failed to fetch user info."""
    pass


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class OIDCProviderMetadata:
    """OIDC provider metadata from discovery document."""
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: Optional[str] = None
    jwks_uri: Optional[str] = None
    end_session_endpoint: Optional[str] = None
    scopes_supported: list[str] = field(default_factory=lambda: ["openid"])
    response_types_supported: list[str] = field(default_factory=list)
    code_challenge_methods_supported: list[str] = field(default_factory=list)


@dataclass
class OIDCAuthState:
    """State for an ongoing OIDC authentication flow."""
    state: str
    nonce: str
    code_verifier: str
    redirect_uri: str
    created_at: float
    expires_at: float
    # For account linking
    linking_user_id: Optional[int] = None


@dataclass
class OIDCTokenResponse:
    """Response from token endpoint."""
    access_token: str
    token_type: str
    id_token: Optional[str] = None
    refresh_token: Optional[str] = None
    expires_in: Optional[int] = None
    scope: Optional[str] = None


@dataclass
class OIDCAuthResult:
    """Result of successful OIDC authentication."""
    sub: str  # Subject identifier (unique user ID from provider)
    username: str
    email: Optional[str] = None
    name: Optional[str] = None
    email_verified: Optional[bool] = None
    groups: Optional[list[str]] = None
    raw_claims: dict = field(default_factory=dict)


# =============================================================================
# State Storage (in-memory with expiration)
# =============================================================================

class OIDCStateStore:
    """
    In-memory storage for OIDC authentication state.

    Stores state, nonce, and PKCE code verifier during the auth flow.
    States expire after 5 minutes.
    """

    STATE_EXPIRY_SECONDS = 300  # 5 minutes

    def __init__(self):
        self._states: dict[str, OIDCAuthState] = {}

    def create_state(
        self,
        redirect_uri: str,
        linking_user_id: Optional[int] = None,
    ) -> OIDCAuthState:
        """Create and store a new auth state."""
        now = time.time()

        # Clean expired states
        self._cleanup_expired()

        state = OIDCAuthState(
            state=secrets.token_urlsafe(32),
            nonce=secrets.token_urlsafe(32),
            code_verifier=secrets.token_urlsafe(64),
            redirect_uri=redirect_uri,
            created_at=now,
            expires_at=now + self.STATE_EXPIRY_SECONDS,
            linking_user_id=linking_user_id,
        )

        self._states[state.state] = state
        logger.debug(f"Created OIDC state: {state.state[:8]}...")
        return state

    def get_state(self, state: str) -> Optional[OIDCAuthState]:
        """Get and remove a state (one-time use)."""
        self._cleanup_expired()

        auth_state = self._states.pop(state, None)
        if auth_state is None:
            logger.warning(f"OIDC state not found: {state[:8]}...")
            return None

        if time.time() > auth_state.expires_at:
            logger.warning(f"OIDC state expired: {state[:8]}...")
            return None

        logger.debug(f"Retrieved OIDC state: {state[:8]}...")
        return auth_state

    def _cleanup_expired(self) -> None:
        """Remove expired states."""
        now = time.time()
        expired = [k for k, v in self._states.items() if now > v.expires_at]
        for k in expired:
            del self._states[k]
        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired OIDC states")


# Global state store instance
_state_store = OIDCStateStore()


def get_state_store() -> OIDCStateStore:
    """Get the global OIDC state store."""
    return _state_store


# =============================================================================
# PKCE Helpers
# =============================================================================

def generate_code_challenge(code_verifier: str) -> str:
    """Generate PKCE code challenge from code verifier (S256 method)."""
    digest = hashlib.sha256(code_verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


# =============================================================================
# OIDC Client
# =============================================================================

class OIDCClient:
    """
    OpenID Connect client for authentication.

    Handles discovery, authorization URL generation, token exchange,
    and ID token validation.
    """

    def __init__(self, settings: OIDCSettings):
        """
        Initialize OIDC client.

        Args:
            settings: OIDC configuration settings
        """
        self.settings = settings
        self._metadata: Optional[OIDCProviderMetadata] = None
        self._jwks: Optional[dict] = None
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._http_client.aclose()

    async def __aenter__(self) -> "OIDCClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def discover(self) -> OIDCProviderMetadata:
        """
        Fetch OIDC provider metadata from discovery URL.

        Returns:
            OIDCProviderMetadata with provider endpoints

        Raises:
            OIDCDiscoveryError: If discovery fails
        """
        if self._metadata is not None:
            return self._metadata

        discovery_url = self.settings.discovery_url
        if not discovery_url:
            raise OIDCDiscoveryError("OIDC discovery URL not configured")

        logger.info(f"Discovering OIDC provider at: {discovery_url}")

        try:
            response = await self._http_client.get(discovery_url)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as e:
            logger.error(f"OIDC discovery failed: {e}")
            raise OIDCDiscoveryError(f"Failed to fetch discovery document: {e}")
        except Exception as e:
            logger.error(f"OIDC discovery error: {e}")
            raise OIDCDiscoveryError(f"Discovery error: {e}")

        # Validate required fields
        required_fields = ["issuer", "authorization_endpoint", "token_endpoint"]
        for field in required_fields:
            if field not in data:
                raise OIDCDiscoveryError(f"Discovery document missing required field: {field}")

        self._metadata = OIDCProviderMetadata(
            issuer=data["issuer"],
            authorization_endpoint=data["authorization_endpoint"],
            token_endpoint=data["token_endpoint"],
            userinfo_endpoint=data.get("userinfo_endpoint"),
            jwks_uri=data.get("jwks_uri"),
            end_session_endpoint=data.get("end_session_endpoint"),
            scopes_supported=data.get("scopes_supported", ["openid"]),
            response_types_supported=data.get("response_types_supported", []),
            code_challenge_methods_supported=data.get("code_challenge_methods_supported", []),
        )

        logger.info(f"Discovered OIDC provider: {self._metadata.issuer}")
        return self._metadata

    async def get_jwks(self) -> dict:
        """Fetch JSON Web Key Set from provider."""
        if self._jwks is not None:
            return self._jwks

        metadata = await self.discover()
        if not metadata.jwks_uri:
            raise OIDCValidationError("Provider does not publish JWKS")

        try:
            response = await self._http_client.get(metadata.jwks_uri)
            response.raise_for_status()
            self._jwks = response.json()
            return self._jwks
        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch JWKS: {e}")
            raise OIDCValidationError(f"Failed to fetch JWKS: {e}")

    async def get_authorization_url(
        self,
        redirect_uri: str,
        state: str,
        nonce: str,
        code_verifier: str,
    ) -> str:
        """
        Generate authorization URL for redirect.

        Args:
            redirect_uri: URL to redirect back to after auth
            state: CSRF protection state
            nonce: Replay protection nonce
            code_verifier: PKCE code verifier

        Returns:
            Authorization URL to redirect user to
        """
        metadata = await self.discover()

        code_challenge = generate_code_challenge(code_verifier)

        params = {
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.settings.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }

        url = f"{metadata.authorization_endpoint}?{urlencode(params)}"
        logger.debug(f"Generated authorization URL: {metadata.authorization_endpoint}")
        return url

    async def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> OIDCTokenResponse:
        """
        Exchange authorization code for tokens.

        Args:
            code: Authorization code from callback
            redirect_uri: Same redirect URI used in authorization
            code_verifier: PKCE code verifier

        Returns:
            OIDCTokenResponse with tokens

        Raises:
            OIDCTokenError: If token exchange fails
        """
        metadata = await self.discover()

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.settings.client_id,
            "client_secret": self.settings.client_secret,
            "code_verifier": code_verifier,
        }

        logger.info("Exchanging authorization code for tokens")

        try:
            response = await self._http_client.post(
                metadata.token_endpoint,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                error_data = response.json() if response.content else {}
                error = error_data.get("error", "unknown_error")
                error_desc = error_data.get("error_description", response.text)
                logger.error(f"Token exchange failed: {error} - {error_desc}")
                raise OIDCTokenError(f"Token exchange failed: {error_desc}")

            token_data = response.json()

        except httpx.HTTPError as e:
            logger.error(f"Token exchange HTTP error: {e}")
            raise OIDCTokenError(f"Token exchange failed: {e}")

        return OIDCTokenResponse(
            access_token=token_data["access_token"],
            token_type=token_data.get("token_type", "Bearer"),
            id_token=token_data.get("id_token"),
            refresh_token=token_data.get("refresh_token"),
            expires_in=token_data.get("expires_in"),
            scope=token_data.get("scope"),
        )

    async def validate_id_token(
        self,
        id_token: str,
        nonce: str,
    ) -> dict:
        """
        Validate and decode ID token.

        Args:
            id_token: JWT ID token from token response
            nonce: Expected nonce value

        Returns:
            Decoded claims from ID token

        Raises:
            OIDCValidationError: If validation fails
        """
        metadata = await self.discover()

        try:
            # Fetch JWKS for signature verification
            jwks = await self.get_jwks()

            # Decode and validate the token
            claims = jwt.decode(
                id_token,
                jwks,
                claims_options={
                    "iss": {"essential": True, "value": metadata.issuer},
                    "aud": {"essential": True, "value": self.settings.client_id},
                    "exp": {"essential": True},
                    "iat": {"essential": True},
                    "nonce": {"essential": True, "value": nonce},
                }
            )
            claims.validate()

            logger.info(f"ID token validated for subject: {claims.get('sub')}")
            return dict(claims)

        except JoseError as e:
            logger.error(f"ID token validation failed: {e}")
            raise OIDCValidationError(f"ID token validation failed: {e}")
        except Exception as e:
            logger.error(f"ID token validation error: {e}")
            raise OIDCValidationError(f"ID token validation error: {e}")

    async def get_userinfo(self, access_token: str) -> dict:
        """
        Fetch user info from userinfo endpoint.

        Args:
            access_token: Access token from token response

        Returns:
            User info claims

        Raises:
            OIDCUserInfoError: If fetch fails
        """
        metadata = await self.discover()

        if not metadata.userinfo_endpoint:
            logger.debug("No userinfo endpoint available")
            return {}

        try:
            response = await self._http_client.get(
                metadata.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch userinfo: {e}")
            raise OIDCUserInfoError(f"Failed to fetch userinfo: {e}")

    async def authenticate(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce: str,
    ) -> OIDCAuthResult:
        """
        Complete OIDC authentication flow.

        Exchanges code for tokens, validates ID token, and extracts user info.

        Args:
            code: Authorization code from callback
            redirect_uri: Same redirect URI used in authorization
            code_verifier: PKCE code verifier
            nonce: Expected nonce value

        Returns:
            OIDCAuthResult with user information

        Raises:
            OIDCError: If authentication fails
        """
        # Exchange code for tokens
        tokens = await self.exchange_code(code, redirect_uri, code_verifier)

        # Validate ID token and get claims
        if tokens.id_token:
            claims = await self.validate_id_token(tokens.id_token, nonce)
        else:
            # Fallback to userinfo if no ID token
            claims = await self.get_userinfo(tokens.access_token)

        # Try to get additional info from userinfo endpoint
        try:
            userinfo = await self.get_userinfo(tokens.access_token)
            # Merge userinfo into claims (userinfo takes precedence)
            claims = {**claims, **userinfo}
        except OIDCUserInfoError:
            pass  # Use claims from ID token only

        # Extract user info using configured claim mappings
        sub = claims.get("sub")
        if not sub:
            raise OIDCValidationError("ID token missing required 'sub' claim")

        username = claims.get(self.settings.username_claim) or claims.get("preferred_username") or claims.get("email") or sub
        email = claims.get(self.settings.email_claim) or claims.get("email")
        name = claims.get(self.settings.name_claim) or claims.get("name")
        email_verified = claims.get("email_verified")
        groups = claims.get("groups", [])

        logger.info(f"OIDC authentication successful for: {username} (sub={sub})")

        return OIDCAuthResult(
            sub=sub,
            username=username,
            email=email,
            name=name,
            email_verified=email_verified,
            groups=groups if isinstance(groups, list) else None,
            raw_claims=claims,
        )
