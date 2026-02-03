"""
Dispatcharr authentication provider.

Authenticates users against a Dispatcharr instance using its JWT token API.
"""
import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from config import get_settings


logger = logging.getLogger(__name__)


@dataclass
class DispatcharrAuthResult:
    """Result of Dispatcharr authentication."""
    user_id: str  # External user identifier
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None


class DispatcharrAuthError(Exception):
    """Base exception for Dispatcharr auth errors."""
    pass


class DispatcharrConnectionError(DispatcharrAuthError):
    """Connection to Dispatcharr failed."""
    pass


class DispatcharrAuthenticationError(DispatcharrAuthError):
    """Authentication with Dispatcharr failed."""
    pass


class DispatcharrClient:
    """
    Client for authenticating users against Dispatcharr.

    Uses Dispatcharr's JWT token endpoint to validate credentials.
    """

    def __init__(self, base_url: Optional[str] = None):
        """
        Initialize Dispatcharr auth client.

        Args:
            base_url: Dispatcharr instance URL. If not provided, uses settings.
        """
        if base_url:
            self._base_url = base_url.rstrip("/")
        else:
            settings = get_settings()
            if not settings.url:
                raise DispatcharrConnectionError(
                    "Dispatcharr URL not configured. Please set the Dispatcharr URL in Settings."
                )
            self._base_url = settings.url.rstrip("/")

        self._client = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "DispatcharrClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def authenticate(self, username: str, password: str) -> DispatcharrAuthResult:
        """
        Authenticate a user against Dispatcharr.

        Args:
            username: Dispatcharr username
            password: Dispatcharr password

        Returns:
            DispatcharrAuthResult with user information

        Raises:
            DispatcharrAuthenticationError: Invalid credentials
            DispatcharrConnectionError: Connection failed
            TimeoutError: Request timed out
        """
        logger.info(f"Authenticating user '{username}' against Dispatcharr at {self._base_url}")

        try:
            # Authenticate to get JWT token
            response = await self._client.post(
                f"{self._base_url}/api/accounts/token/",
                json={
                    "username": username,
                    "password": password,
                },
            )

            if response.status_code == 401:
                logger.warning(f"Dispatcharr auth failed for user '{username}': Invalid credentials")
                raise DispatcharrAuthenticationError("Invalid username or password")

            if response.status_code != 200:
                logger.error(f"Dispatcharr auth failed: HTTP {response.status_code}")
                raise DispatcharrAuthenticationError(
                    f"Authentication failed with status {response.status_code}"
                )

            data = response.json()
            access_token = data.get("access")

            if not access_token:
                logger.error("Dispatcharr response missing access token")
                raise DispatcharrAuthenticationError("Invalid response from Dispatcharr")

            # Try to get user info (optional - some Dispatcharr versions may not have this)
            user_info = await self._get_user_info(access_token, username)

            logger.info(f"Successfully authenticated user '{username}' via Dispatcharr")

            return DispatcharrAuthResult(
                user_id=user_info.get("id", f"dispatcharr:{username}"),
                username=user_info.get("username", username),
                email=user_info.get("email"),
                display_name=user_info.get("display_name") or user_info.get("first_name"),
            )

        except httpx.TimeoutException:
            logger.error(f"Dispatcharr connection timed out: {self._base_url}")
            raise TimeoutError("Connection to Dispatcharr timed out")

        except httpx.ConnectError as e:
            logger.error(f"Cannot connect to Dispatcharr: {e}")
            raise DispatcharrConnectionError(f"Cannot connect to Dispatcharr: {e}")

        except (DispatcharrAuthenticationError, TimeoutError):
            raise

        except Exception as e:
            logger.exception(f"Unexpected error during Dispatcharr auth: {e}")
            raise DispatcharrAuthError(f"Authentication error: {e}")

    async def _get_user_info(self, access_token: str, username: str) -> dict:
        """
        Get user information from Dispatcharr.

        Args:
            access_token: JWT access token
            username: Username to use as fallback

        Returns:
            Dict with user info (may be partial if endpoint not available)
        """
        try:
            # Try to get user profile
            response = await self._client.get(
                f"{self._base_url}/api/accounts/me/",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if response.status_code == 200:
                user_data = response.json()
                return {
                    "id": str(user_data.get("id", f"dispatcharr:{username}")),
                    "username": user_data.get("username", username),
                    "email": user_data.get("email"),
                    "display_name": user_data.get("display_name"),
                    "first_name": user_data.get("first_name"),
                }

            # Endpoint might not exist, fall back to username only
            logger.debug(f"User info endpoint returned {response.status_code}, using username only")

        except Exception as e:
            logger.debug(f"Could not get user info from Dispatcharr: {e}")

        # Return minimal info using username
        return {
            "id": f"dispatcharr:{username}",
            "username": username,
        }
