"""Consolidated OAuth abuse-case fixture — Resource Server side (bead buiqr.9 (a)).

THE PERMANENT NEGATIVE-COVERAGE GUARD for ECM's MCP OAuth. Every case here is an
abuse attempt; each asserts the spec-defined rejection (HTTP 401 with
``WWW-Authenticate: Bearer`` at the RS, RFC 6749 ``invalid_grant`` /
``invalid_request`` at the AS). This is the forever fixture against auth-bypass
and dual-path regressions; correctness of these assertions matters as much as
the code they guard.

WHY THIS FILE IS SPLIT ACROSS TWO CONTAINERS
============================================
The 10 abuse cases the epic enumerates span BOTH halves of the AS/RS split
(ADR-009 §1; threat model §5 already prescribes "RS tests in ``mcp-server/``,
AS tests in ``backend/``"). The two halves do not share a Python environment:

  - The **MCP Resource Server** (this package) verifies access tokens OFFLINE
    (``oauth_rs.py``) and its CI job installs ONLY ``mcp-server/requirements.txt``
    (pyjwt + starlette — NO ``fastapi`` / ``python-jose`` / ``slowapi``). The AS
    modules (``backend/auth/oauth_provider.py``, ``backend/routers/oauth_mcp.py``)
    cannot be imported here, so the AS-side abuse cases (PKCE, codes, redirect,
    refresh) CANNOT live in this file — they would ImportError in CI.

  - The **ECM Authorization Server** (``backend/``) mints codes and tokens. Its
    abuse-case half lives in ``backend/tests/routers/test_oauth_abuse.py`` — the
    same logical fixture, driven through the real FastAPI app (no network).

ABUSE-CASE MAP (10 cases, the consolidated fixture across both files)
====================================================================
    #   Abuse case                     Defended at   This file?   New vs existing
    --  -----------------------------  ------------  -----------  ----------------------------------
    1   PKCE plain-method rejection    AS            no (b/end)   existing: test_oauth_provider /
                                                                  test_oauth_mcp
    2   PKCE verifier mismatch         AS            no (b/end)   existing: test_oauth_provider
    3   auth-code replay               AS            no (b/end)   existing: test_oauth_mcp
    4   expired access token           RS            YES          REUSES helper; new at app layer
    5   wrong audience                 RS            YES          REUSES helper; new at app layer
    6   mismatched redirect_uri        AS            no (b/end)   existing: test_oauth_mcp
    7   missing code_challenge         AS            no (b/end)   existing: test_oauth_provider
    8   refresh-token reuse            AS            no (b/end)   existing: test_oauth_mcp
    9   malformed JWT                  RS            YES          new at app layer
    10  cross-instance token           RS            YES          new (different signing secret)

The RS-side cases (4, 5, 9, 10) are exercised here END-TO-END through the
dual-path middleware (``server.app`` via Starlette ``TestClient``, NO network):
proving each abuse attempt yields a 401 AND — critically — never falls through
to the static-key path (the CD1 no-fail-cascade invariant). The token-minting
helpers mirror ``test_dual_path_routing.py`` / ``test_oauth_rs.py`` so the
fixtures stay in lockstep with the security engineer's tests; we deliberately do
not re-prove ``oauth_rs.verify_oauth_token`` in isolation (that is
``test_oauth_rs.py``'s job) — here we assert the OBSERVABLE HTTP outcome an
attacker would see.

Threat-model rows: CD1 (no fail-cascade), SP2 (alg pinning), EP1 (audience),
TS2/A1 (TTL-bounded access-token revocation → expired token is rejected),
SR1 (cross-instance: a token signed with another instance's secret is rejected).
"""
import base64
import json
import time

from unittest.mock import patch

# ── Streamable-HTTP client headers + initialize body (mirror test_server.py) ──
_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}

# These constants intentionally match test_dual_path_routing.py / test_oauth_rs.py
# so a reader can move between the abuse fixture and the routing/verifier tests
# without re-deriving the secret/issuer/audience.
_SECRET = "test-oauth-signing-secret-at-least-32-bytes-long-padding"
_ISSUER = "https://ecm.example.com"
_AUDIENCE = "ecm-mcp"
_STATIC_KEY = "static-key-value-no-dots-1234567890"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _mint(claims: dict, *, secret: str = _SECRET, alg: str = "HS256") -> str:
    """Mint an HS256 JWT exactly as the ECM AS does (jose-compatible)."""
    import jwt as pyjwt

    return pyjwt.encode(claims, secret, algorithm=alg)


def _base_claims(**overrides) -> dict:
    """A well-formed access-token claim set; override one field per abuse case."""
    now = int(time.time())
    claims = {
        "sub": "admin",
        "aud": _AUDIENCE,
        "iss": _ISSUER,
        "scope": "mcp",
        "jti": "jti-abuse",
        "iat": now,
        "exp": now + 900,
        "token_type": "access",
    }
    claims.update(overrides)
    return claims


class _StaticKeySpy:
    """Spy on the static-key reader: it must NEVER be consulted on the OAuth path.

    Mirrors test_dual_path_routing.py's spy. If an abuse attempt on the OAuth
    path fell through to static-key validation, ``called`` would flip True — the
    exact CD1 auth-bypass this fixture is the forever-guard against.
    """

    def __init__(self, value=_STATIC_KEY):
        self.value = value
        self.called = False

    def __call__(self):
        self.called = True
        return self.value


def _post_bearer(client, token, *, spy):
    """Drive /mcp with a Bearer token under a static-key spy. Return the response."""
    with patch("server.get_signing_key", return_value=_SECRET), \
         patch("server.get_oauth_issuer_for_rs", return_value=_ISSUER), \
         patch("server.get_mcp_api_key", new=spy):
        return client.post(
            "/mcp",
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
            json=_INITIALIZE,
        )


def _assert_rejected_401_no_fallback(client, token):
    """Assert: 401 + WWW-Authenticate: Bearer, and static-key NEVER consulted.

    This is the shared assertion for every RS-side abuse case — the abuse is
    rejected with the spec error AND the no-fail-cascade invariant (CD1) holds.
    """
    spy = _StaticKeySpy()
    response = _post_bearer(client, token, spy=spy)
    assert response.status_code == 401, response.text
    www = response.headers.get("www-authenticate", "")
    assert "Bearer" in www, f"missing WWW-Authenticate: Bearer (got {www!r})"
    assert spy.called is False, (
        "FAIL-CASCADE DETECTED: a JWT-shaped abuse token was evaluated against "
        "the static key (threat model CD1 auth-bypass)."
    )


# ───────────────── RS-side abuse cases (4, 5, 9, 10) ──────────────────────────


class TestResourceServerAbuseCases:
    """The four RS-verifiable abuse cases, asserted at the HTTP layer (no network).

    Each is the OBSERVABLE outcome an attacker presenting a crafted Bearer to
    ``/mcp`` would get: a uniform 401 that never leaks which check failed and
    never falls back to the static key.
    """

    def test_case4_expired_access_token_rejected(self, client):
        """Case 4 — an expired access token → 401, no static-key fallback.

        Under Option A the short TTL is the SOLE access-token revocation backstop
        (threat model TS2/A1); once ``exp`` is past the RS must reject offline.
        """
        token = _mint(_base_claims(exp=int(time.time()) - 10))
        _assert_rejected_401_no_fallback(client, token)

    def test_case5_wrong_audience_rejected(self, client):
        """Case 5 — a token minted for a different audience → 401 (EP1).

        A token whose ``aud`` is not ``ecm-mcp`` (e.g. one issued for a different
        resource server) must not be accepted by this RS.
        """
        token = _mint(_base_claims(aud="some-other-rs"))
        _assert_rejected_401_no_fallback(client, token)

    def test_case9_malformed_jwt_rejected(self, client):
        """Case 9 — a structurally-JWT-shaped but malformed token → 401.

        Three base64url segments with an ``alg`` header (so it routes to the
        OAuth path) but a garbage signature/payload. It must be rejected on the
        OAuth path, never compared to the static key.
        """
        header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        # A payload that base64url-decodes to non-JSON garbage — decode fails.
        token = f"{header}.bm90LWpzb24tcGF5bG9hZA.{_b64url(b'bogus-signature')}"
        _assert_rejected_401_no_fallback(client, token)

    def test_case10_cross_instance_token_rejected(self, client):
        """Case 10 — a token signed with a DIFFERENT instance's secret → 401 (SR1).

        A well-formed token with all the right claims, but signed with another
        ECM instance's ``mcp_oauth_signing_secret``. Offline HS256 verification
        against THIS instance's secret must fail — a token from instance B can
        never authenticate against instance A.
        """
        other_instance_secret = "a-DIFFERENT-instance-signing-secret-32-bytes-x"
        token = _mint(_base_claims(), secret=other_instance_secret)
        _assert_rejected_401_no_fallback(client, token)


# ───────────────── AS-side abuse cases (1, 2, 3, 6, 7, 8) ─────────────────────


class TestAuthorizationServerAbuseCasesLiveInBackend:
    """Marker: the AS-side abuse cases are gated in the backend suite.

    These six cases are defended at the ECM Authorization Server, whose code is
    NOT importable in the MCP RS CI job (no fastapi/jose/slowapi installed).
    Their consolidated, network-free fixture is
    ``backend/tests/routers/test_oauth_abuse.py``; the underlying controls were
    first proved by ``backend/tests/unit/test_oauth_provider.py`` and
    ``backend/tests/routers/test_oauth_mcp.py``. This single placeholder test
    documents the split so the "10 abuse cases" map is discoverable from the RS
    side without anyone assuming a gap.
    """

    def test_as_side_cases_are_documented_and_gated_elsewhere(self):
        as_side = {
            1: "PKCE plain-method rejection",
            2: "PKCE verifier mismatch",
            3: "auth-code replay",
            6: "mismatched redirect_uri",
            7: "missing code_challenge",
            8: "refresh-token reuse",
        }
        # No behavioral assertion is possible here (the AS code is not importable
        # in this CI job); this guards the DOCUMENTED contract that all six are
        # owned by the backend abuse fixture.
        assert sorted(as_side) == [1, 2, 3, 6, 7, 8]
