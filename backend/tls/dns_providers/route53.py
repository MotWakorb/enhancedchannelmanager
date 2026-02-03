"""
AWS Route53 DNS provider for ACME DNS-01 challenges.
"""
import asyncio
import logging
from typing import Optional

from .base import DNSProvider, DNSProviderError

# boto3 is optional - only needed for Route53
try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    _boto3_available = True
except ImportError:
    _boto3_available = False

logger = logging.getLogger(__name__)


class Route53DNS(DNSProvider):
    """
    AWS Route53 DNS API implementation for ACME DNS-01 challenges.

    Requires AWS credentials with the following permissions:
    - route53:ListHostedZones
    - route53:ListHostedZonesByName
    - route53:GetHostedZone
    - route53:ChangeResourceRecordSets
    - route53:GetChange

    Authentication can be provided via:
    1. Access key ID and secret access key (passed to constructor)
    2. AWS environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
    3. IAM role (when running on AWS infrastructure)
    """

    def __init__(
        self,
        access_key_id: str = "",
        secret_access_key: str = "",
        zone_id: str = "",
        region: str = "us-east-1",
    ):
        """
        Initialize Route53 DNS provider.

        Args:
            access_key_id: AWS access key ID (optional if using IAM role)
            secret_access_key: AWS secret access key (optional if using IAM role)
            zone_id: Optional hosted zone ID (can be auto-detected from domain)
            region: AWS region (Route53 is global but SDK needs a region)
        """
        if not _boto3_available:
            raise DNSProviderError(
                "boto3 is required for Route53 support. "
                "Install with: pip install boto3"
            )

        self.zone_id = zone_id
        self.region = region

        # Create boto3 client with explicit credentials if provided
        if access_key_id and secret_access_key:
            self._client = boto3.client(
                "route53",
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name=region,
            )
        else:
            # Use default credential chain (env vars, IAM role, etc.)
            self._client = boto3.client("route53", region_name=region)

    async def verify_credentials(self) -> tuple[bool, Optional[str]]:
        """Verify that the AWS credentials are valid for Route53."""
        try:
            # Run in thread pool since boto3 is synchronous
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: self._client.list_hosted_zones(MaxItems="1"),
            )
            return True, None
        except NoCredentialsError:
            return False, "AWS credentials not found"
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            return False, f"AWS error ({error_code}): {error_msg}"
        except Exception as e:
            return False, f"Connection error: {e}"

    async def get_zone_id(self, domain: str) -> Optional[str]:
        """
        Get the hosted zone ID for a domain.

        Route53 hosted zones are typically the apex domain (e.g., example.com.).
        Note: Route53 zone names end with a trailing dot.
        """
        # If zone_id is already set, return it
        if self.zone_id:
            return self.zone_id

        try:
            loop = asyncio.get_event_loop()

            # Try progressively shorter domain parts
            parts = domain.split(".")
            for i in range(len(parts) - 1):
                zone_name = ".".join(parts[i:])
                # Route53 zone names have trailing dot
                zone_name_dot = f"{zone_name}."

                response = await loop.run_in_executor(
                    None,
                    lambda zn=zone_name_dot: self._client.list_hosted_zones_by_name(
                        DNSName=zn,
                        MaxItems="1",
                    ),
                )

                zones = response.get("HostedZones", [])
                for zone in zones:
                    # Check if this zone matches our domain
                    if zone["Name"] == zone_name_dot:
                        # Extract zone ID from ARN format "/hostedzone/Z1234567890"
                        zone_id = zone["Id"].replace("/hostedzone/", "")
                        logger.info(f"Found Route53 hosted zone: {zone_name} ({zone_id})")
                        return zone_id

            return None

        except ClientError as e:
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            raise DNSProviderError(f"Failed to get hosted zone: {error_msg}")
        except Exception as e:
            raise DNSProviderError(f"Failed to get hosted zone: {e}")

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
            ttl: TTL in seconds

        Returns:
            Record identifier (name) for deletion
        """
        # Extract domain from name to get zone
        # _acme-challenge.sub.example.com -> sub.example.com
        domain = name.replace("_acme-challenge.", "")
        zone_id = await self.get_zone_id(domain)

        if not zone_id:
            raise DNSProviderError(f"Could not find hosted zone for domain: {domain}")

        # Store zone_id for later deletion
        self.zone_id = zone_id

        # Route53 requires quoted TXT record values
        quoted_value = f'"{value}"'

        # Ensure name has trailing dot for Route53
        record_name = name if name.endswith(".") else f"{name}."

        try:
            loop = asyncio.get_event_loop()

            change_batch = {
                "Comment": "ACME DNS-01 challenge",
                "Changes": [
                    {
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": record_name,
                            "Type": "TXT",
                            "TTL": ttl,
                            "ResourceRecords": [{"Value": quoted_value}],
                        },
                    }
                ],
            }

            response = await loop.run_in_executor(
                None,
                lambda: self._client.change_resource_record_sets(
                    HostedZoneId=zone_id,
                    ChangeBatch=change_batch,
                ),
            )

            change_id = response["ChangeInfo"]["Id"]
            logger.info(f"Created Route53 TXT record: {name} (change: {change_id})")

            # Wait for the change to propagate
            await self._wait_for_change(change_id)

            # Return the record name as the identifier
            return record_name

        except ClientError as e:
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            raise DNSProviderError(f"Failed to create TXT record: {error_msg}")
        except Exception as e:
            raise DNSProviderError(f"Failed to create TXT record: {e}")

    async def delete_txt_record(self, record_id: str) -> bool:
        """
        Delete a TXT record.

        Args:
            record_id: Record name (with trailing dot) from create_txt_record

        Returns:
            True if successful
        """
        if not self.zone_id:
            raise DNSProviderError(
                "Zone ID required for deletion. "
                "Save zone_id after create_txt_record."
            )

        try:
            loop = asyncio.get_event_loop()

            # First, get the current record to know its value
            response = await loop.run_in_executor(
                None,
                lambda: self._client.list_resource_record_sets(
                    HostedZoneId=self.zone_id,
                    StartRecordName=record_id,
                    StartRecordType="TXT",
                    MaxItems="1",
                ),
            )

            records = response.get("ResourceRecordSets", [])
            if not records or records[0]["Name"] != record_id:
                logger.warning(f"TXT record not found (already deleted?): {record_id}")
                return True

            record = records[0]

            # Delete the record
            change_batch = {
                "Comment": "ACME DNS-01 challenge cleanup",
                "Changes": [
                    {
                        "Action": "DELETE",
                        "ResourceRecordSet": record,
                    }
                ],
            }

            await loop.run_in_executor(
                None,
                lambda: self._client.change_resource_record_sets(
                    HostedZoneId=self.zone_id,
                    ChangeBatch=change_batch,
                ),
            )

            logger.info(f"Deleted Route53 TXT record: {record_id}")
            return True

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            # Record might already be deleted
            if error_code == "InvalidChangeBatch":
                logger.warning(f"TXT record not found (already deleted?): {record_id}")
                return True
            error_msg = e.response.get("Error", {}).get("Message", str(e))
            raise DNSProviderError(f"Failed to delete TXT record: {error_msg}")
        except Exception as e:
            raise DNSProviderError(f"Failed to delete TXT record: {e}")

    async def _wait_for_change(
        self,
        change_id: str,
        timeout: int = 120,
        poll_interval: int = 5,
    ) -> None:
        """
        Wait for a Route53 change to propagate.

        Args:
            change_id: The change ID from change_resource_record_sets
            timeout: Maximum time to wait in seconds
            poll_interval: Time between status checks
        """
        loop = asyncio.get_event_loop()
        elapsed = 0

        while elapsed < timeout:
            response = await loop.run_in_executor(
                None,
                lambda: self._client.get_change(Id=change_id),
            )

            status = response["ChangeInfo"]["Status"]
            if status == "INSYNC":
                logger.debug(f"Route53 change {change_id} is in sync")
                return

            logger.debug(f"Route53 change {change_id} status: {status}")
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        logger.warning(f"Route53 change {change_id} did not sync within {timeout}s")

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
            raise DNSProviderError(f"Could not find hosted zone for domain: {domain}")

        # Store zone_id for later deletion
        self.zone_id = zone_id

        record_id = await self.create_txt_record(name, value, ttl)
        return record_id, zone_id
