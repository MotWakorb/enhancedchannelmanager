"""Credential redaction for TLS error strings (bead enhancedchannelmanager-2owpi).

Why this exists on top of :func:`cloud_storage.upload_security.redact_secrets`
-----------------------------------------------------------------------------

``redact_secrets`` is a pattern sweep: it recognises credential SHAPES
(``Authorization:`` headers, ``Bearer <token>``, ``AKIA…``, S3 signed-URL
params, ``key=value`` pairs). That is the right tool when you do not know what
the secret is, and it stays the second half of every call here.

But on the TLS path we DO know what the secret is. ``TLSSettings`` is holding
the exact ``dns_api_token``, ``aws_access_key_id`` and ``aws_secret_access_key``
that a third-party exception might be quoting back at us, so the stronger move
is to delete those values by identity first and let the pattern sweep catch
whatever identity misses. Identity redaction does not care about the token's
charset, length or delimiters, which is exactly where a pattern sweep is
brittle: ``_BEARER_RE`` matches ``[A-Za-z0-9._-]+``, so a provider that ever
issues a token containing a character outside that set would be masked only up
to the first one. Belt and braces, cheapest first.

Callers pass the values they hold; this module never reads settings itself, so
it stays a leaf with no import cycle into :mod:`tls.settings`.
"""
from __future__ import annotations

from typing import Iterable, Optional

from cloud_storage.upload_security import redact_secrets

# Same placeholder redact_secrets uses, so a redacted string reads consistently
# regardless of which half of the redaction caught the value.
_MASK = "***REDACTED***"

# Values shorter than this are not treated as secrets to delete by identity.
# An empty or one-character credential is either unset or nonsense, and
# blanket-replacing a short string would shred unrelated error text.
_MIN_IDENTITY_LENGTH = 8


def redact_secret_values(text: str, secrets: Iterable[Optional[str]]) -> str:
    """Delete known credential values from ``text``, then pattern-sweep it.

    Args:
        text: An error/log string that may quote a credential.
        secrets: The credential values the caller is holding. ``None``, empty
            and implausibly short entries are ignored.

    Returns:
        ``text`` with every known value replaced, then passed through
        ``redact_secrets``. Never raises: redaction sits on logging and error
        paths, where blowing up would be worse than the thing it prevents.
    """
    if not text:
        return text
    try:
        out = text
        for secret in secrets:
            # Non-str entries are skipped rather than coerced. A caller passing
            # a mock or an unexpected type must not cause the whole message to
            # be discarded by the fail-safe below.
            if not secret or not isinstance(secret, str):
                continue
            # A pasted credential often carries surrounding whitespace that
            # never made it through validation. Redact both the stored form
            # and its stripped form, longest first, so the shorter one cannot
            # leave a fragment of the longer behind.
            candidates = {secret, secret.strip()}
            for candidate in sorted(candidates, key=len, reverse=True):
                if len(candidate) >= _MIN_IDENTITY_LENGTH and candidate in out:
                    out = out.replace(candidate, _MASK)
        return redact_secrets(out)
    except Exception:  # pragma: no cover — redaction must never break a log call
        return _MASK
