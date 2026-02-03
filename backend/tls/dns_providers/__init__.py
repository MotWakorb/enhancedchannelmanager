"""
DNS provider implementations for DNS-01 ACME challenges.

Supported providers:
- Cloudflare
- AWS Route53
"""

from .base import DNSProvider, DNSProviderError
from .cloudflare import CloudflareDNS

# Route53 requires boto3 - import conditionally
try:
    from .route53 import Route53DNS
    _route53_available = True
except ImportError:
    Route53DNS = None  # type: ignore
    _route53_available = False

__all__ = [
    "DNSProvider",
    "DNSProviderError",
    "CloudflareDNS",
    "Route53DNS",
    "get_dns_provider",
]


def get_dns_provider(
    provider_name: str,
    api_token: str = "",
    zone_id: str = "",
    aws_access_key_id: str = "",
    aws_secret_access_key: str = "",
    aws_region: str = "us-east-1",
) -> DNSProvider:
    """
    Get a DNS provider instance by name.

    Args:
        provider_name: Name of the provider (e.g., "cloudflare", "route53")
        api_token: API token for authentication (Cloudflare)
        zone_id: Zone ID (optional, can be auto-detected)
        aws_access_key_id: AWS access key ID (Route53)
        aws_secret_access_key: AWS secret access key (Route53)
        aws_region: AWS region (Route53, default us-east-1)

    Returns:
        DNSProvider instance

    Raises:
        ValueError: If provider is not supported
    """
    provider_name = provider_name.lower().strip()

    if provider_name == "cloudflare":
        return CloudflareDNS(api_token=api_token, zone_id=zone_id)
    elif provider_name == "route53":
        if not _route53_available:
            raise ValueError(
                "Route53 support requires boto3. Install with: pip install boto3"
            )
        return Route53DNS(
            access_key_id=aws_access_key_id,
            secret_access_key=aws_secret_access_key,
            zone_id=zone_id,
            region=aws_region,
        )
    else:
        raise ValueError(f"Unsupported DNS provider: {provider_name}")
