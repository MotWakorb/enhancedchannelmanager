"""
Cloudflare DNS provider for ACME DNS-01 challenges.
"""
import logging
import re
from typing import Optional

import httpx

from .base import DNSProvider, DNSProviderError


logger = logging.getLogger(__name__)

# Cloudflare zone/record IDs are hex strings
_CF_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _validate_cf_id(value: str, label: str) -> str:
    """Validate a Cloudflare ID (zone_id or record_id) is a 32-char hex string."""
    if not _CF_ID_RE.match(value):
        raise DNSProviderError(f"Invalid {label} format")
    return value


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

    def _client(self) -> httpx.AsyncClient:
        """Create an httpx client with fixed base URL."""
        return httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=self._headers,
            timeout=30.0,
        )

    def _parse_response(self, data: dict) -> dict:
        """Check Cloudflare response for errors."""
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
            async with self._client() as client:
                resp = await client.get("/user/tokens/verify")
                self._parse_response(resp.json())
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
            async with self._client() as client:
                for i in range(len(parts) - 1):
                    zone_name = ".".join(parts[i:])
                    resp = await client.get("/zones", params={"name": zone_name})
                    data = self._parse_response(resp.json())

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

        # Validate zone_id format before using in URL path
        safe_zone_id = _validate_cf_id(zone_id, "zone_id")

        # Cloudflare minimum TTL is 60 seconds (or 1 for automatic)
        ttl = max(60, ttl)

        try:
            # Build path from validated components only
            path = f"/zones/{safe_zone_id}/dns_records"
            async with self._client() as client:
                resp = await client.post(path, json={
                    "type": "TXT",
                    "name": name,
                    "content": value,
                    "ttl": ttl,
                })
                data = self._parse_response(resp.json())

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

        # Validate IDs before using in URL path
        safe_zone_id = _validate_cf_id(self.zone_id, "zone_id")
        safe_record_id = _validate_cf_id(record_id, "record_id")

        try:
            path = f"/zones/{safe_zone_id}/dns_records/{safe_record_id}"
            async with self._client() as client:
                resp = await client.delete(path)
                self._parse_response(resp.json())
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
