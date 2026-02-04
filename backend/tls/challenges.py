"""
ACME DNS-01 challenge verification utilities.

This module provides utilities for verifying DNS-01 TXT records.
"""
import logging
from typing import Optional

import httpx


logger = logging.getLogger(__name__)


async def verify_dns_challenge(
    domain: str,
    expected_value: str,
    timeout: float = 10.0,
) -> tuple[bool, Optional[str]]:
    """
    Verify that a DNS-01 challenge TXT record exists.

    Args:
        domain: The domain being validated
        expected_value: The expected TXT record value
        timeout: Timeout for DNS lookup

    Returns:
        Tuple of (success, error_message)
    """
    txt_name = f"_acme-challenge.{domain}"

    try:
        # Use DNS-over-HTTPS for reliable external lookup
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Use Google's DNS-over-HTTPS
            resp = await client.get(
                "https://dns.google/resolve",
                params={"name": txt_name, "type": "TXT"},
            )
            data = resp.json()

            if data.get("Status") != 0:
                return False, f"DNS lookup failed: status {data.get('Status')}"

            answers = data.get("Answer", [])
            for answer in answers:
                if answer.get("type") == 16:  # TXT record
                    # TXT records come quoted
                    value = answer.get("data", "").strip('"')
                    if value == expected_value:
                        return True, None

            found_values = [
                a.get("data", "").strip('"')
                for a in answers
                if a.get("type") == 16
            ]
            if found_values:
                return False, f"TXT record found but value doesn't match. Found: {found_values}"
            return False, f"No TXT record found for {txt_name}"

    except Exception as e:
        return False, f"DNS lookup error: {e}"
