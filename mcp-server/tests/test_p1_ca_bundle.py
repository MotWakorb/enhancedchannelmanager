"""Actual SSL trust regression for enhancedchannelmanager-xyfpa (no network).

https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2
Compromised intermediate keys chain to emSign ECC Root CA - G3. The unaffected
G1 root must remain trusted: certifi 2026.10.19 restores it after 2026.10.18.
"""

import hashlib
import ssl

import certifi

# Authoritative DER SHA-256 fingerprints from the upstream bundle patches:
# https://github.com/certifi/python-certifi/commit/5cb238caeeb6c30ec1a7309ad3e16a5aa7b10ce8
# https://github.com/certifi/python-certifi/commit/662df63d7906df66f1f07c0751a3dfe9da14d914
_COMPROMISED_G3 = "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b"
_UNAFFECTED_G1 = "40f6af0346a99aa1cd1d555a4e9cce62c7f9634603ee406615833dc8c8d00367"


def _trusted_ca_fingerprints() -> set[str]:
    context = ssl.create_default_context(cafile=certifi.where())
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    return {
        hashlib.sha256(certificate).hexdigest()
        for certificate in context.get_ca_certs(binary_form=True)
    }


def test_certifi_does_not_trust_compromised_emsign_g3() -> None:
    assert _COMPROMISED_G3 not in _trusted_ca_fingerprints()


def test_certifi_still_trusts_unaffected_emsign_g1() -> None:
    assert _UNAFFECTED_G1 in _trusted_ca_fingerprints()
