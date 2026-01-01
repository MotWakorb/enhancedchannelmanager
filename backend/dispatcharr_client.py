import httpx
from typing import Optional
from config import get_settings, DispatcharrSettings


class DispatcharrClient:
    """API client for Dispatcharr with JWT authentication."""

    def __init__(self, settings: DispatcharrSettings):
        self.settings = settings
        self.base_url = self.settings.url.rstrip("/")
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self._client = httpx.AsyncClient(timeout=30.0)

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid access token."""
        if not self.access_token:
            await self._login()

    async def _login(self) -> None:
        """Authenticate and obtain JWT tokens."""
        response = await self._client.post(
            f"{self.base_url}/api/accounts/token/",
            json={
                "username": self.settings.username,
                "password": self.settings.password,
            },
        )
        response.raise_for_status()
        data = response.json()
        self.access_token = data["access"]
        self.refresh_token = data.get("refresh")

    async def _refresh_access_token(self) -> None:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            await self._login()
            return

        response = await self._client.post(
            f"{self.base_url}/api/accounts/token/refresh/",
            json={"refresh": self.refresh_token},
        )
        if response.status_code == 200:
            data = response.json()
            self.access_token = data["access"]
        else:
            # Refresh token expired, do full login
            await self._login()

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an authenticated request with automatic token refresh."""
        await self._ensure_authenticated()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"

        response = await self._client.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            **kwargs,
        )

        # If unauthorized, try refreshing token and retry
        if response.status_code == 401:
            await self._refresh_access_token()
            headers["Authorization"] = f"Bearer {self.access_token}"
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                **kwargs,
            )

        return response

    # -------------------------------------------------------------------------
    # Channels
    # -------------------------------------------------------------------------

    async def get_channels(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
        channel_group: Optional[int] = None,
    ) -> dict:
        """Get paginated list of channels."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if channel_group:
            params["channel_group"] = channel_group

        response = await self._request("GET", "/api/channels/channels/", params=params)
        response.raise_for_status()
        return response.json()

    async def get_channel(self, channel_id: int) -> dict:
        """Get a single channel by ID."""
        response = await self._request("GET", f"/api/channels/channels/{channel_id}/")
        response.raise_for_status()
        return response.json()

    async def get_channel_streams(self, channel_id: int) -> list:
        """Get streams assigned to a channel."""
        response = await self._request(
            "GET", f"/api/channels/channels/{channel_id}/streams/"
        )
        response.raise_for_status()
        return response.json()

    async def update_channel(self, channel_id: int, data: dict) -> dict:
        """Update a channel (PATCH)."""
        response = await self._request(
            "PATCH", f"/api/channels/channels/{channel_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def create_channel(self, data: dict) -> dict:
        """Create a new channel."""
        response = await self._request(
            "POST", "/api/channels/channels/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_channel(self, channel_id: int) -> None:
        """Delete a channel."""
        response = await self._request(
            "DELETE", f"/api/channels/channels/{channel_id}/"
        )
        response.raise_for_status()

    async def assign_channel_numbers(
        self, channel_ids: list[int], starting_number: Optional[float] = None
    ) -> dict:
        """Bulk assign channel numbers."""
        data = {"channel_ids": channel_ids}
        if starting_number is not None:
            data["starting_number"] = starting_number

        response = await self._request(
            "POST", "/api/channels/channels/assign/", json=data
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Channel Groups
    # -------------------------------------------------------------------------

    async def get_channel_groups(self) -> list:
        """Get all channel groups."""
        response = await self._request("GET", "/api/channels/groups/")
        response.raise_for_status()
        return response.json()

    async def create_channel_group(self, name: str) -> dict:
        """Create a new channel group."""
        response = await self._request(
            "POST", "/api/channels/groups/", json={"name": name}
        )
        response.raise_for_status()
        return response.json()

    async def update_channel_group(self, group_id: int, data: dict) -> dict:
        """Update a channel group."""
        response = await self._request(
            "PATCH", f"/api/channels/groups/{group_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_channel_group(self, group_id: int) -> None:
        """Delete a channel group."""
        response = await self._request("DELETE", f"/api/channels/groups/{group_id}/")
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # Streams
    # -------------------------------------------------------------------------

    async def get_streams(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
        channel_group_name: Optional[str] = None,
        m3u_account: Optional[int] = None,
    ) -> dict:
        """Get paginated list of streams."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if channel_group_name:
            params["channel_group_name"] = channel_group_name
        if m3u_account:
            params["m3u_account"] = m3u_account

        response = await self._request("GET", "/api/channels/streams/", params=params)
        response.raise_for_status()
        return response.json()

    async def get_stream(self, stream_id: int) -> dict:
        """Get a single stream by ID."""
        response = await self._request("GET", f"/api/channels/streams/{stream_id}/")
        response.raise_for_status()
        return response.json()

    async def get_streams_by_ids(self, ids: list[int]) -> list:
        """Get multiple streams by IDs."""
        response = await self._request(
            "POST", "/api/channels/streams/by-ids/", json={"ids": ids}
        )
        response.raise_for_status()
        return response.json()

    async def get_stream_groups(self) -> list:
        """Get all stream groups (for filtering)."""
        response = await self._request("GET", "/api/channels/streams/groups/")
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # M3U Accounts (Providers)
    # -------------------------------------------------------------------------

    async def get_m3u_accounts(self) -> list:
        """Get all M3U accounts/providers."""
        response = await self._request("GET", "/api/m3u/accounts/")
        response.raise_for_status()
        return response.json()

    async def get_all_m3u_group_settings(self) -> dict:
        """Get group settings for all M3U accounts, returns dict mapping channel_group_id to settings.

        The channel_groups data is embedded in the accounts response, so we extract it from there.
        When multiple accounts have settings for the same group, prefer the one with auto_channel_sync enabled.
        """
        accounts = await self.get_m3u_accounts()
        all_settings = {}
        for account in accounts:
            # channel_groups is embedded in the account response
            channel_groups = account.get("channel_groups", [])
            for setting in channel_groups:
                channel_group_id = setting.get("channel_group")
                if channel_group_id:
                    new_setting = {
                        **setting,
                        "m3u_account_id": account["id"],
                        "m3u_account_name": account.get("name", ""),
                    }
                    # If this group already exists, only overwrite if new setting has auto_channel_sync
                    # and existing one doesn't (prefer the one with auto_channel_sync enabled)
                    existing = all_settings.get(channel_group_id)
                    if existing is None:
                        all_settings[channel_group_id] = new_setting
                    elif new_setting.get("auto_channel_sync") and not existing.get("auto_channel_sync"):
                        all_settings[channel_group_id] = new_setting
        return all_settings

    # -------------------------------------------------------------------------
    # Logos
    # -------------------------------------------------------------------------

    async def get_logos(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
    ) -> dict:
        """Get paginated list of logos."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search

        response = await self._request("GET", "/api/channels/logos/", params=params)
        response.raise_for_status()
        return response.json()

    async def get_logo(self, logo_id: int) -> dict:
        """Get a single logo by ID."""
        response = await self._request("GET", f"/api/channels/logos/{logo_id}/")
        response.raise_for_status()
        return response.json()

    async def create_logo(self, data: dict) -> dict:
        """Create a new logo."""
        response = await self._request("POST", "/api/channels/logos/", json=data)
        if response.status_code >= 400:
            import sys
            print(f"Logo creation error - Status: {response.status_code}, Body: {response.text}", file=sys.stderr, flush=True)
        response.raise_for_status()
        return response.json()

    async def update_logo(self, logo_id: int, data: dict) -> dict:
        """Update a logo."""
        response = await self._request(
            "PATCH", f"/api/channels/logos/{logo_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_logo(self, logo_id: int) -> None:
        """Delete a logo."""
        response = await self._request("DELETE", f"/api/channels/logos/{logo_id}/")
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # EPG Sources
    # -------------------------------------------------------------------------

    async def get_epg_sources(self) -> list:
        """Get all EPG sources."""
        response = await self._request("GET", "/api/epg/sources/")
        response.raise_for_status()
        return response.json()

    async def get_epg_source(self, source_id: int) -> dict:
        """Get a single EPG source by ID."""
        response = await self._request("GET", f"/api/epg/sources/{source_id}/")
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # EPG Data
    # -------------------------------------------------------------------------

    async def get_epg_data(
        self,
        page: int = 1,
        page_size: int = 100,
        search: Optional[str] = None,
        epg_source: Optional[int] = None,
    ) -> dict:
        """Get paginated list of EPG data entries."""
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if epg_source:
            params["epg_source"] = epg_source

        response = await self._request("GET", "/api/epg/epgdata/", params=params)
        response.raise_for_status()
        return response.json()

    async def get_epg_data_by_id(self, data_id: int) -> dict:
        """Get a single EPG data entry by ID."""
        response = await self._request("GET", f"/api/epg/epgdata/{data_id}/")
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Stream Profiles
    # -------------------------------------------------------------------------

    async def get_stream_profiles(self) -> list:
        """Get all stream profiles."""
        response = await self._request("GET", "/api/core/streamprofiles/")
        response.raise_for_status()
        return response.json()

    async def get_stream_profile(self, profile_id: int) -> dict:
        """Get a single stream profile by ID."""
        response = await self._request("GET", f"/api/core/streamprofiles/{profile_id}/")
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Cleanup
    # -------------------------------------------------------------------------

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


# Singleton instance
_client: Optional[DispatcharrClient] = None
_client_settings_hash: Optional[str] = None


def _settings_hash(settings: DispatcharrSettings) -> str:
    """Get a hash of settings to detect changes."""
    return f"{settings.url}:{settings.username}:{settings.password}"


def get_client() -> DispatcharrClient:
    """Get the Dispatcharr client, recreating if settings changed."""
    global _client, _client_settings_hash

    settings = get_settings()
    current_hash = _settings_hash(settings)

    if _client is None or _client_settings_hash != current_hash:
        _client = DispatcharrClient(settings)
        _client_settings_hash = current_hash

    return _client


def reset_client() -> None:
    """Reset the client (call after settings change)."""
    global _client, _client_settings_hash
    _client = None
    _client_settings_hash = None
