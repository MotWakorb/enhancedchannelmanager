"""JWT-shape classification for the MCP RS static-key auth router.

MCP OAuth offering RETIRED (bd-9axgc). ECM no longer accepts OAuth 2.1 Bearer
tokens for MCP; the only supported credential is the static ``?api_key=`` (or
non-JWT-shaped ``Bearer <key>``) path. The OAuth offline HS256 verifier
(``verify_oauth_token``) and the RFC 9728 discovery module were removed with the
feature.

What REMAINS is the shape classifier :func:`looks_like_jwt` — the CD1
no-fail-cascade guard. ``server.py`` calls it BEFORE any static-key compare so a
JWT-shaped Bearer is REJECTED with 401 and can NEVER fall through to the
static-key path (threat model CD1/SP6). This is security-critical: it must stay
even though the OAuth verify path is gone.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging

logger = logging.getLogger(__name__)


def looks_like_jwt(value: str) -> bool:
    """Classify a credential by SHAPE: is it a JWT we route to the OAuth path?

    A value is JWT-shaped iff it has EXACTLY three ``.``-separated segments AND
    the first segment base64url-decodes to a JSON object carrying an ``alg``
    field. This is decided BEFORE any static-key compare (threat model CD1/SP6) —
    classification never depends on whether the token is *valid*, only on its
    shape.

    CRITICAL: an ``alg:none`` token is JWT-SHAPED and returns True here — so it
    routes to the (now-rejected) OAuth path and can NEVER be misrouted to the
    static-key path. A static key (no dots, or dots but a non-JSON / no-``alg``
    first segment) returns False and routes to static-key.

    Returns False on anything that is not unambiguously JWT-shaped — fail
    toward the static-key path is safe here because a real JWT always satisfies
    this shape, and a non-JWT static key never should.
    """
    if not value:
        return False
    segments = value.split(".")
    if len(segments) != 3:
        return False
    header_segment = segments[0]
    if not header_segment:
        return False
    try:
        # base64url-decode the header with correct padding.
        padded = header_segment + "=" * (-len(header_segment) % 4)
        raw = base64.urlsafe_b64decode(padded)
        header = json.loads(raw)
    except (binascii.Error, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(header, dict):
        return False
    return "alg" in header
