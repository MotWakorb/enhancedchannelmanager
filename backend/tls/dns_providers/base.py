"""
Base DNS provider interface for ACME DNS-01 challenges.
"""
from abc import ABC, abstractmethod
from typing import Optional


class DNSProviderError(Exception):
    """Error from a DNS provider operation."""

    pass


class DNSProvider(ABC):
    """
    Abstract base class for DNS providers.

    Implementations must provide methods to create and delete TXT records
    for ACME DNS-01 challenges.
    """

    @abstractmethod
    async def create_txt_record(
        self,
        name: str,
        value: str,
        ttl: int = 60,
    ) -> str:
        """
        Create a TXT record for DNS-01 challenge.

        Args:
            name: The record name (e.g., "_acme-challenge.example.com")
            value: The record value (base64url-encoded challenge response)
            ttl: Time-to-live in seconds

        Returns:
            Record ID for later deletion

        Raises:
            DNSProviderError: If creation fails
        """
        pass

    @abstractmethod
    async def delete_txt_record(self, record_id: str) -> bool:
        """
        Delete a TXT record after challenge completion.

        Args:
            record_id: The record ID returned from create_txt_record

        Returns:
            True if deletion was successful

        Raises:
            DNSProviderError: If deletion fails
        """
        pass

    @abstractmethod
    async def get_zone_id(self, domain: str) -> Optional[str]:
        """
        Get the zone ID for a domain.

        Args:
            domain: The domain name

        Returns:
            Zone ID, or None if not found

        Raises:
            DNSProviderError: If lookup fails
        """
        pass

    @abstractmethod
    async def verify_credentials(self) -> tuple[bool, Optional[str]]:
        """
        Verify that the provider credentials are valid.

        Returns:
            Tuple of (success, error_message)
        """
        pass
