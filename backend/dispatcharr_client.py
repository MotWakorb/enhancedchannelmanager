import httpx
import logging
from typing import Optional
from config import get_settings, DispatcharrSettings

logger = logging.getLogger(__name__)


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
        logger.debug(f"Authenticating to Dispatcharr at {self.base_url}")
        try:
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
            logger.info(f"Successfully authenticated to Dispatcharr - username: {self.settings.username}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Authentication failed - status: {e.response.status_code}")
            raise
        except Exception as e:
            logger.exception(f"Authentication error: {e}")
            raise

    async def _refresh_access_token(self) -> None:
        """Refresh the access token using the refresh token."""
        if not self.refresh_token:
            logger.debug("No refresh token available, performing full login")
            await self._login()
            return

        logger.debug("Refreshing access token")
        response = await self._client.post(
            f"{self.base_url}/api/accounts/token/refresh/",
            json={"refresh": self.refresh_token},
        )
        if response.status_code == 200:
            data = response.json()
            self.access_token = data["access"]
            logger.debug("Access token refreshed successfully")
        else:
            # Refresh token expired, do full login
            logger.warning(f"Refresh token expired (status: {response.status_code}), performing full login")
            await self._login()

    async def _request(
        self,
        method: str,
        path: str,
        **kwargs,
    ) -> httpx.Response:
        """Make an authenticated request with automatic token refresh."""
        logger.debug(f"API request: {method} {path}")
        await self._ensure_authenticated()

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self.access_token}"

        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                **kwargs,
            )

            # If unauthorized, try refreshing token and retry
            if response.status_code == 401:
                logger.debug(f"Got 401, refreshing token and retrying: {method} {path}")
                await self._refresh_access_token()
                headers["Authorization"] = f"Bearer {self.access_token}"
                response = await self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=headers,
                    **kwargs,
                )

            if response.status_code >= 400:
                logger.warning(f"API request failed: {method} {path} - status: {response.status_code}")
            else:
                logger.debug(f"API request successful: {method} {path} - status: {response.status_code}")

            return response
        except Exception as e:
            logger.exception(f"API request error: {method} {path} - {e}")
            raise

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
        """Create a new channel.

        Returns the created channel, or raises an exception with the response body
        if creation fails.
        """
        response = await self._request(
            "POST", "/api/channels/channels/", json=data
        )
        if response.status_code >= 400:
            # Include response body in exception for better error handling
            error_body = response.text
            raise Exception(f"Channel creation failed: {response.status_code} - {error_body}")
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
        """Create a new channel group.

        Returns the created group, or raises an exception with the response body
        if creation fails (e.g., group already exists).
        """
        response = await self._request(
            "POST", "/api/channels/groups/", json={"name": name}
        )
        if response.status_code >= 400:
            # Include response body in exception for better error handling
            error_body = response.text
            raise Exception(f"Channel group creation failed: {response.status_code} - {error_body}")
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
        accounts = response.json()
        logger.debug(f"Dispatcharr returned {len(accounts)} M3U accounts")
        for account in accounts:
            logger.debug(f"  M3U Account: id={account.get('id')}, name={account.get('name')}, server_url={account.get('server_url')}")
        return accounts

    async def get_all_m3u_group_settings(self) -> dict:
        """Get group settings for all M3U accounts, returns dict mapping channel_group_id to settings.

        The channel_groups data is embedded in the accounts response, so we extract it from there.
        When multiple accounts have settings for the same group, prefer the one with auto_channel_sync enabled.
        """
        accounts = await self.get_m3u_accounts()
        logger.info(f"get_all_m3u_group_settings: Processing {len(accounts)} M3U accounts")
        all_settings = {}
        total_groups_found = 0
        for account in accounts:
            # channel_groups is embedded in the account response
            channel_groups = account.get("channel_groups", [])
            total_groups_found += len(channel_groups)
            logger.info(f"  Account {account.get('id')}: {account.get('name')} has {len(channel_groups)} channel_groups")
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
        logger.info(f"  Total channel_groups entries across all accounts: {total_groups_found}")
        logger.info(f"  Unique channel group IDs extracted: {len(all_settings)}")
        return all_settings

    async def get_m3u_account(self, account_id: int) -> dict:
        """Get a single M3U account by ID."""
        response = await self._request("GET", f"/api/m3u/accounts/{account_id}/")
        response.raise_for_status()
        return response.json()

    async def create_m3u_account(self, data: dict) -> dict:
        """Create a new M3U account."""
        response = await self._request("POST", "/api/m3u/accounts/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_m3u_account(self, account_id: int, data: dict) -> dict:
        """Update an M3U account (full update)."""
        response = await self._request(
            "PUT", f"/api/m3u/accounts/{account_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def patch_m3u_account(self, account_id: int, data: dict) -> dict:
        """Partially update an M3U account (e.g., toggle is_active)."""
        response = await self._request(
            "PATCH", f"/api/m3u/accounts/{account_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_m3u_account(self, account_id: int) -> None:
        """Delete an M3U account."""
        response = await self._request("DELETE", f"/api/m3u/accounts/{account_id}/")
        response.raise_for_status()

    async def refresh_m3u_account(self, account_id: int) -> dict:
        """Trigger refresh for a single M3U account."""
        response = await self._request(
            "POST", f"/api/m3u/refresh/{account_id}/"
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "Refresh initiated"}

    async def refresh_all_m3u_accounts(self) -> dict:
        """Trigger refresh for all active M3U accounts."""
        response = await self._request("POST", "/api/m3u/refresh/")
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "Refresh initiated"}

    async def refresh_m3u_vod(self, account_id: int) -> dict:
        """Refresh VOD content for an XtreamCodes account."""
        response = await self._request(
            "POST", f"/api/m3u/accounts/{account_id}/refresh-vod/"
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "VOD refresh initiated"}

    # -------------------------------------------------------------------------
    # M3U Filters
    # -------------------------------------------------------------------------

    async def get_m3u_filters(self, account_id: int) -> list:
        """Get all filters for an M3U account."""
        response = await self._request(
            "GET", f"/api/m3u/accounts/{account_id}/filters/"
        )
        response.raise_for_status()
        return response.json()

    async def create_m3u_filter(self, account_id: int, data: dict) -> dict:
        """Create a new filter for an M3U account."""
        response = await self._request(
            "POST", f"/api/m3u/accounts/{account_id}/filters/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def update_m3u_filter(
        self, account_id: int, filter_id: int, data: dict
    ) -> dict:
        """Update a filter for an M3U account."""
        response = await self._request(
            "PUT", f"/api/m3u/accounts/{account_id}/filters/{filter_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_m3u_filter(self, account_id: int, filter_id: int) -> None:
        """Delete a filter from an M3U account."""
        response = await self._request(
            "DELETE", f"/api/m3u/accounts/{account_id}/filters/{filter_id}/"
        )
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # M3U Profiles
    # -------------------------------------------------------------------------

    async def get_m3u_profiles(self, account_id: int) -> list:
        """Get all profiles for an M3U account."""
        response = await self._request("GET", f"/api/m3u/accounts/{account_id}/profiles/")
        response.raise_for_status()
        return response.json()

    async def create_m3u_profile(self, account_id: int, data: dict) -> dict:
        """Create a new profile for an M3U account."""
        response = await self._request(
            "POST", f"/api/m3u/accounts/{account_id}/profiles/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def get_m3u_profile(self, account_id: int, profile_id: int) -> dict:
        """Get a specific profile for an M3U account."""
        response = await self._request(
            "GET", f"/api/m3u/accounts/{account_id}/profiles/{profile_id}/"
        )
        response.raise_for_status()
        return response.json()

    async def update_m3u_profile(
        self, account_id: int, profile_id: int, data: dict
    ) -> dict:
        """Update a profile for an M3U account."""
        response = await self._request(
            "PATCH", f"/api/m3u/accounts/{account_id}/profiles/{profile_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_m3u_profile(self, account_id: int, profile_id: int) -> None:
        """Delete a profile from an M3U account."""
        response = await self._request(
            "DELETE", f"/api/m3u/accounts/{account_id}/profiles/{profile_id}/"
        )
        response.raise_for_status()

    # -------------------------------------------------------------------------
    # M3U Group Settings
    # -------------------------------------------------------------------------

    async def update_m3u_group_settings(self, account_id: int, data: dict) -> dict:
        """Update group settings for an M3U account.

        Data should contain group settings like:
        {
            "group_settings": [
                {"channel_group": 123, "enabled": true, "auto_channel_sync": false, ...}
            ]
        }
        """
        response = await self._request(
            "PATCH", f"/api/m3u/accounts/{account_id}/group-settings/", json=data
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Server Groups
    # -------------------------------------------------------------------------

    async def get_server_groups(self) -> list:
        """Get all server groups."""
        response = await self._request("GET", "/api/m3u/server-groups/")
        response.raise_for_status()
        return response.json()

    async def create_server_group(self, data: dict) -> dict:
        """Create a new server group."""
        response = await self._request("POST", "/api/m3u/server-groups/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_server_group(self, group_id: int, data: dict) -> dict:
        """Update a server group."""
        response = await self._request(
            "PATCH", f"/api/m3u/server-groups/{group_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_server_group(self, group_id: int) -> None:
        """Delete a server group."""
        response = await self._request("DELETE", f"/api/m3u/server-groups/{group_id}/")
        response.raise_for_status()

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
        """Create a new logo.

        Returns the created logo, or raises an exception with the response body
        if creation fails (e.g., logo already exists).
        """
        response = await self._request("POST", "/api/channels/logos/", json=data)
        if response.status_code >= 400:
            # Include response body in exception for better error handling
            error_body = response.text
            raise Exception(f"Logo creation failed: {response.status_code} - {error_body}")
        return response.json()

    async def find_logo_by_url(self, url: str) -> Optional[dict]:
        """Find an existing logo by its URL.

        Paginates through logos to find exact URL match.
        Uses larger page size to minimize API calls.
        """
        page = 1
        page_size = 500  # Use larger page size for efficiency
        while True:
            result = await self.get_logos(page=page, page_size=page_size)
            for logo in result.get("results", []):
                if logo.get("url") == url:
                    return logo
            # Check if there are more pages
            if not result.get("next"):
                break
            page += 1
        return None

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

    async def create_epg_source(self, data: dict) -> dict:
        """Create a new EPG source."""
        response = await self._request("POST", "/api/epg/sources/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_epg_source(self, source_id: int, data: dict) -> dict:
        """Update an EPG source."""
        response = await self._request("PATCH", f"/api/epg/sources/{source_id}/", json=data)
        response.raise_for_status()
        return response.json()

    async def delete_epg_source(self, source_id: int) -> None:
        """Delete an EPG source."""
        response = await self._request("DELETE", f"/api/epg/sources/{source_id}/")
        response.raise_for_status()

    async def refresh_epg_source(self, source_id: int) -> dict:
        """Refresh a single EPG source.

        Dispatcharr's /api/epg/import/ endpoint expects the EPG source ID
        in the request body as {"id": source_id}.
        """
        # Send POST to /api/epg/import/ with the source ID in the body
        response = await self._request(
            "POST",
            "/api/epg/import/",
            json={"id": source_id}
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True, "message": "Refresh initiated"}

    async def trigger_epg_import(self) -> dict:
        """Trigger an EPG data import for all active sources.

        Dispatcharr's /api/epg/import/ endpoint requires an ID in the body,
        so we need to iterate over all active non-dummy sources and trigger
        each one individually.
        """
        # Get all EPG sources
        sources = await self.get_epg_sources()

        # Filter to active non-dummy sources
        active_sources = [
            s for s in sources
            if s.get("source_type") != "dummy" and s.get("is_active", True)
        ]

        if not active_sources:
            return {"success": True, "message": "No active EPG sources to refresh"}

        # Trigger refresh for each active source
        refreshed = []
        errors = []
        for source in active_sources:
            try:
                await self.refresh_epg_source(source["id"])
                refreshed.append(source["name"])
            except Exception as e:
                errors.append(f"{source['name']}: {str(e)}")

        if errors:
            return {
                "success": len(refreshed) > 0,
                "message": f"Refreshed {len(refreshed)} sources, {len(errors)} failed",
                "refreshed": refreshed,
                "errors": errors
            }

        return {
            "success": True,
            "message": f"EPG import initiated for {len(refreshed)} sources",
            "refreshed": refreshed
        }

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

    async def get_epg_grid(self, start: str = None, end: str = None) -> list:
        """Get EPG programs.

        Uses /api/epg/programs/ endpoint which returns all programs.

        Args:
            start: Optional start datetime in ISO format (not currently used)
            end: Optional end datetime in ISO format (not currently used)
        """
        response = await self._request("GET", "/api/epg/programs/")
        response.raise_for_status()
        data = response.json()
        # Handle both list and dict responses
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("data", data.get("results", []))
        return []

    # -------------------------------------------------------------------------
    # Stream Profiles
    # -------------------------------------------------------------------------

    async def get_stream_profiles(self) -> list:
        """Get all stream profiles."""
        response = await self._request("GET", "/api/core/streamprofiles/")
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------------------
    # Channel Profiles
    # -------------------------------------------------------------------------

    async def get_channel_profiles(self) -> list:
        """Get all channel profiles."""
        response = await self._request("GET", "/api/channels/profiles/")
        response.raise_for_status()
        return response.json()

    async def get_channel_profile(self, profile_id: int) -> dict:
        """Get a single channel profile by ID."""
        response = await self._request("GET", f"/api/channels/profiles/{profile_id}/")
        response.raise_for_status()
        return response.json()

    async def create_channel_profile(self, data: dict) -> dict:
        """Create a new channel profile."""
        response = await self._request("POST", "/api/channels/profiles/", json=data)
        response.raise_for_status()
        return response.json()

    async def update_channel_profile(self, profile_id: int, data: dict) -> dict:
        """Update a channel profile (PATCH)."""
        response = await self._request(
            "PATCH", f"/api/channels/profiles/{profile_id}/", json=data
        )
        response.raise_for_status()
        return response.json()

    async def delete_channel_profile(self, profile_id: int) -> None:
        """Delete a channel profile."""
        response = await self._request("DELETE", f"/api/channels/profiles/{profile_id}/")
        response.raise_for_status()

    async def bulk_update_profile_channels(self, profile_id: int, data: dict) -> dict:
        """Bulk enable/disable channels for a profile.

        Input format: {"channel_ids": [1, 2, 3], "enabled": true}
        API format: {"channels": [{"channel_id": 1, "enabled": true}, ...]}
        """
        # Transform from our format to API format
        channel_ids = data.get("channel_ids", [])
        enabled = data.get("enabled", True)
        api_data = {
            "channels": [
                {"channel_id": cid, "enabled": enabled}
                for cid in channel_ids
            ]
        }
        response = await self._request(
            "PATCH",
            f"/api/channels/profiles/{profile_id}/channels/bulk-update/",
            json=api_data
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    async def update_profile_channel(
        self, profile_id: int, channel_id: int, data: dict
    ) -> dict:
        """Enable/disable a single channel for a profile.

        Data format: {"enabled": true}
        """
        response = await self._request(
            "PATCH",
            f"/api/channels/profiles/{profile_id}/channels/{channel_id}/",
            json=data
        )
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    # -------------------------------------------------------------------------
    # Stats & Monitoring
    # -------------------------------------------------------------------------

    async def get_channel_stats(self) -> dict:
        """Get status of all active channels.

        Returns summary including active channels, client counts, bitrates, etc.
        """
        response = await self._request("GET", "/proxy/ts/status")
        response.raise_for_status()
        return response.json()

    async def get_channel_stats_detail(self, channel_id: int) -> dict:
        """Get detailed stats for a specific channel.

        Includes per-client information, buffer status, codec details, etc.
        """
        response = await self._request("GET", f"/proxy/ts/status/{channel_id}")
        response.raise_for_status()
        return response.json()

    async def get_system_events(
        self,
        limit: int = 100,
        offset: int = 0,
        event_type: Optional[str] = None,
    ) -> dict:
        """Get recent system events (channel start/stop, buffering, client connections).

        Args:
            limit: Number of events to return (max 1000)
            offset: Pagination offset
            event_type: Optional filter by event type
        """
        params = {"limit": limit, "offset": offset}
        if event_type:
            params["event_type"] = event_type

        response = await self._request("GET", "/api/core/system-events/", params=params)
        response.raise_for_status()
        return response.json()

    async def stop_channel(self, channel_id: int) -> dict:
        """Stop a channel and release all associated resources."""
        response = await self._request("POST", f"/proxy/ts/stop/{channel_id}")
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

    async def stop_client(self, channel_id: int) -> dict:
        """Stop a specific client connection."""
        response = await self._request("POST", f"/proxy/ts/stop_client/{channel_id}")
        response.raise_for_status()
        return response.json() if response.content else {"success": True}

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
