"""
Cloudflare DNS provider for ACME DNS-01 challenges.
"""
import logging
from typing import Optional

import httpx

from .base import DNSProvider, DNSProviderError


logger = logging.getLogger(__name__)


class CloudflareDNS(DNSProvider):
    """
    Cloudflare DNS API implementation for ACME DNS-01 challenges.

    Requires an API token with DNS:Edit permission for the zone.
    """

    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token: str, zone_id: str = ""):
        """
        Initialize Cloudflare DNS provider.

        Args:
            api_token: Cloudflare API token with DNS:Edit permission
            zone_id: Optional zone ID (can be auto-detected from domain)
        """
        self.api_token = api_token
        self.zone_id = zone_id
        self._headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        }

    # Allowed Cloudflare API path prefixes
    _ALLOWED_PREFIXES = ("/user/", "/zones")

    async def _request(
        self,
        method: str,
        endpoint: str,
        json: dict = None,
    ) -> dict:
        """Make an API request to Cloudflare."""
        # Validate endpoint to prevent SSRF
        if not endpoint.startswith("/") or "://" in endpoint:
            raise DNSProviderError(f"Invalid API endpoint: {endpoint}")
        if not any(endpoint.startswith(p) for p in self._ALLOWED_PREFIXES):
            raise DNSProviderError(f"Disallowed API endpoint: {endpoint}")

        url = f"{self.BASE_URL}{endpoint}"

        async with httpx.AsyncClient() as client:
            resp = await client.request(
                method,
                url,
                headers=self._headers,
                json=json,
                timeout=30.0,
            )

            data = resp.json()

            if not data.get("success", False):
                errors = data.get("errors", [])
                error_msg = "; ".join(
                    e.get("message", "Unknown error") for e in errors
                )
                raise DNSProviderError(f"Cloudflare API error: {error_msg}")

            return data

    async def verify_credentials(self) -> tuple[bool, Optional[str]]:
        """Verify that the API token is valid."""
        try:
            await self._request("GET", "/user/tokens/verify")
            return True, None
        except DNSProviderError as e:
            return False, str(e)
        except Exception as e:
            return False, f"Connection error: {e}"

    async def get_zone_id(self, domain: str) -> Optional[str]:
        """
        Get the zone ID for a domain.

        Cloudflare zones are typically the apex domain (e.g., example.com).
        For subdomains like sub.example.com, we need to find example.com.
        """
        # If zone_id is already set, return it
        if self.zone_id:
            return self.zone_id

        try:
            # Try progressively shorter domain parts
            parts = domain.split(".")
            for i in range(len(parts) - 1):
                zone_name = ".".join(parts[i:])
                data = await self._request(
                    "GET",
                    f"/zones?name={zone_name}",
                )

                zones = data.get("result", [])
                if zones:
                    zone_id = zones[0]["id"]
                    logger.info("[TLS-CLOUDFLARE] Found Cloudflare zone: %s (%s)", zone_name, zone_id)
                    return zone_id

            return None

        except DNSProviderError:
            raise
        except Exception as e:
            raise DNSProviderError(f"Failed to get zone ID: {e}")

    async def create_txt_record(
        self,
        name: str,
        value: str,
        ttl: int = 60,
    ) -> str:
        """
        Create a TXT record for DNS-01 challenge.

        Args:
            name: Record name (e.g., "_acme-challenge.example.com")
            value: Record value
            ttl: TTL in seconds (minimum 60 for Cloudflare)

        Returns:
            Record ID for deletion
        """
        # Extract domain from name to get zone
        # _acme-challenge.sub.example.com -> sub.example.com
        domain = name.replace("_acme-challenge.", "")
        zone_id = await self.get_zone_id(domain)

        if not zone_id:
            raise DNSProviderError(f"Could not find zone for domain: {domain}")

        # Cloudflare minimum TTL is 60 seconds (or 1 for automatic)
        ttl = max(60, ttl)

        try:
            data = await self._request(
                "POST",
                f"/zones/{zone_id}/dns_records",
                json={
                    "type": "TXT",
                    "name": name,
                    "content": value,
                    "ttl": ttl,
                },
            )

            record_id = data["result"]["id"]
            logger.info("[TLS-CLOUDFLARE] Created Cloudflare TXT record: %s (%s)", name, record_id)
            return record_id

        except DNSProviderError:
            raise
        except Exception as e:
            raise DNSProviderError(f"Failed to create TXT record: {e}")

    async def delete_txt_record(self, record_id: str) -> bool:
        """
        Delete a TXT record.

        Args:
            record_id: Record ID from create_txt_record

        Returns:
            True if successful
        """
        # We need the zone_id to delete
        if not self.zone_id:
            raise DNSProviderError(
                "Zone ID required for deletion. "
                "Save zone_id after create_txt_record."
            )

        try:
            await self._request(
                "DELETE",
                f"/zones/{self.zone_id}/dns_records/{record_id}",
            )
            logger.info("[TLS-CLOUDFLARE] Deleted Cloudflare TXT record: %s", record_id)
            return True

        except DNSProviderError as e:
            # Record might already be deleted
            if "not found" in str(e).lower():
                logger.warning("[TLS-CLOUDFLARE] TXT record not found (already deleted?): %s", record_id)
                return True
            raise
        except Exception as e:
            raise DNSProviderError(f"Failed to delete TXT record: {e}")

    async def create_and_get_zone(
        self,
        name: str,
        value: str,
        ttl: int = 60,
    ) -> tuple[str, str]:
        """
        Create a TXT record and return both record ID and zone ID.

        This is useful for cleanup since we need the zone ID to delete.

        Returns:
            Tuple of (record_id, zone_id)
        """
        domain = name.replace("_acme-challenge.", "")
        zone_id = await self.get_zone_id(domain)

        if not zone_id:
            raise DNSProviderError(f"Could not find zone for domain: {domain}")

        # Store zone_id for later deletion
        self.zone_id = zone_id

        record_id = await self.create_txt_record(name, value, ttl)
        return record_id, zone_id
