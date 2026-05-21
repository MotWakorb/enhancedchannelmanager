"""Rate-limiting policy for the OAuth Authorization Server endpoints (bead buiqr.4).

The OAuth token-issuance surface (``/api/oauth/authorize`` + ``/api/oauth/token``)
is a credential-handling surface: ``/token`` exchanges PKCE codes / rotates
refresh tokens, and ``/authorize`` drives consent for the single ECM admin.
Threat model rows **D1** (``/token`` brute-force / rate-limit bypass) and **D2**
(``/authorize`` consent-spam) mandate **per-IP AND per-user** rate limiting.

This module reuses the *same* slowapi ``Limiter`` instance the login endpoints
already use (``auth.routes.limiter``) — so the existing
``RateLimitExceeded`` exception handler wired in ``main.py`` applies unchanged,
and the existing ``RATE_LIMIT_ENABLED=0`` test escape hatch disables these limits
too (the test suite sets it in ``conftest.py``).

TWO BUCKETS PER ENDPOINT
========================
slowapi 0.1.9 lets a single endpoint stack multiple ``@limiter.limit(...)``
decorators, each with its own ``key_func``. We apply two per OAuth endpoint:

  - **per-IP** — :func:`get_remote_address` (the same key the login limiter uses).
  - **per-user** — :func:`oauth_user_rate_key`, which keys on the *principal*
    driving the request (the authenticated admin subject on ``/authorize``, or
    the ``client_id`` on ``/token``), falling back to the client IP.

Both buckets must have headroom for a request to pass; whichever is exhausted
first trips the 429. This means a single noisy IP cannot exhaust the limit for a
different admin/client, and a single principal cannot grind the endpoint from
many IPs — the two STRIDE concerns D1/D2 call out.

KEY FUNCTIONS ARE SYNCHRONOUS
=============================
slowapi calls ``key_func(request)`` synchronously and does **not** await the
result (``slowapi.extension`` line ~496), so a key function cannot read the POST
body (``await request.form()``). The per-user key therefore derives the
principal from synchronously-available sources only: the JWT subject (cookie /
Authorization header) and the query string. The OAuth ``client_id`` is read from
the query params when present; clients that send it only in the form body fall
back to the per-IP bucket, which still bounds them.

CONFIGURABLE DEFAULTS
=====================
Defaults mirror the existing login limit (``5/minute``) per ADR-009 Assumptions
§A3 ("thresholds sized to single-admin usage"). They are overridable via env
vars without a code change:

  - ``OAUTH_AUTHORIZE_RATE_LIMIT`` (default ``"5/minute"``)
  - ``OAUTH_TOKEN_RATE_LIMIT``     (default ``"10/minute"``)

``/token`` gets a slightly higher default than ``/authorize`` because a single
legitimate consent yields one ``/authorize`` hit but can drive several ``/token``
calls (code exchange + subsequent refresh rotations) within a window.
"""
from __future__ import annotations

import os

from fastapi import Request
from slowapi.util import get_remote_address

from auth.dependencies import decode_token_safe, get_token_from_request

#: Default per-bucket limit for ``/authorize`` (mirrors the login limit, A3).
DEFAULT_AUTHORIZE_RATE_LIMIT = "5/minute"
#: Default per-bucket limit for ``/token`` — higher than /authorize because one
#: consent drives a code exchange plus later refresh rotations in a window.
DEFAULT_TOKEN_RATE_LIMIT = "10/minute"


def authorize_rate_limit() -> str:
    """Resolve the ``/authorize`` rate-limit string (env-overridable)."""
    return os.environ.get("OAUTH_AUTHORIZE_RATE_LIMIT", DEFAULT_AUTHORIZE_RATE_LIMIT)


def token_rate_limit() -> str:
    """Resolve the ``/token`` rate-limit string (env-overridable)."""
    return os.environ.get("OAUTH_TOKEN_RATE_LIMIT", DEFAULT_TOKEN_RATE_LIMIT)


def oauth_user_rate_key(request: Request) -> str:
    """Per-user rate-limit key for the OAuth endpoints (D1/D2 second bucket).

    Returns a stable principal key derived from synchronously-available request
    data, prefixed so it can never collide with a raw IP key:

      1. ``oauth-sub:<jti-subject>`` — the authenticated admin subject, when a
         valid JWT is presented (the ``/authorize`` case; the admin session
         carries the JWT in a cookie or ``Authorization: Bearer``).
      2. ``oauth-client:<client_id>`` — the OAuth ``client_id`` from the query
         string, when present (a best-effort principal for ``/token``/``/authorize``
         requests that put ``client_id`` in the query).
      3. ``oauth-ip:<remote-addr>`` — fall back to the client IP so the bucket is
         always populated (e.g. the public ``/token`` POST that carries
         ``client_id`` only in the body).

    Key functions run synchronously and cannot read the POST body, so this never
    inspects form data — body-only ``client_id`` callers are still bounded by the
    per-IP bucket on the same endpoint.
    """
    token = get_token_from_request(request)
    if token:
        payload = decode_token_safe(token)
        if payload and payload.get("sub"):
            return f"oauth-sub:{payload['sub']}"

    client_id = request.query_params.get("client_id")
    if client_id:
        return f"oauth-client:{client_id}"

    return f"oauth-ip:{get_remote_address(request)}"
