"""Unit tests for the MCP Resource Server OAuth Bearer-JWT verifier (bd-buiqr.8).

These exercise ``oauth_rs.py`` in isolation — credential SHAPE classification
and the offline HS256 validation — without going through the Starlette app.
The HTTP-level dual-path routing + no-fail-cascade invariant lives in
``test_dual_path_routing.py``.

Security contract under test (ADR-009 §1/§2/§3; threat model CD1/SP1/SP2/SP6/EP1/EP2):
  - alg PINNED to HS256 (reject alg:none, RS256, anything else).
  - aud == "ecm-mcp", iss == OAUTH_ISSUER, exp not past, scope contains "mcp".
  - Classification is by SHAPE, decided BEFORE any validation.
  - The verifier reads ONLY the dedicated ``mcp_oauth_signing_secret`` and never
    opens the token store (offline verify, AC4).
"""
import base64
import json
import time

import pytest

import oauth_rs


# A signing secret comfortably above the 32-byte HMAC floor (avoids PyJWT's
# InsecureKeyLengthWarning skewing test output; mirrors a real generated secret).
_SECRET = "test-oauth-signing-secret-at-least-32-bytes-long-padding"
_ISSUER = "https://ecm.example.com"
_AUDIENCE = "ecm-mcp"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _mint(claims: dict, *, secret: str = _SECRET, alg: str = "HS256") -> str:
    """Mint an HS256 JWT exactly as the ECM AS does (jose-compatible)."""
    import jwt as pyjwt

    return pyjwt.encode(claims, secret, algorithm=alg)


def _base_claims(**overrides) -> dict:
    now = int(time.time())
    claims = {
        "sub": "admin",
        "aud": _AUDIENCE,
        "iss": _ISSUER,
        "scope": "mcp",
        "jti": "jti-test",
        "iat": now,
        "exp": now + 900,
        "token_type": "access",
    }
    claims.update(overrides)
    return claims


# ───────────────────────── shape classification ─────────────────────────────


class TestShapeClassification:
    """``looks_like_jwt`` decides routing BEFORE any validation (CD1 / SP6)."""

    def test_valid_jwt_shape_is_jwt(self):
        token = _mint(_base_claims())
        assert oauth_rs.looks_like_jwt(token) is True

    def test_static_key_is_not_jwt(self):
        # A typical static key (token_urlsafe-style) has no dots.
        assert oauth_rs.looks_like_jwt("Zm9vYmFyYmF6cXV4LXN0YXRpYy1rZXk") is False

    def test_two_segments_is_not_jwt(self):
        assert oauth_rs.looks_like_jwt("aGVhZGVy.cGF5bG9hZA") is False

    def test_four_segments_is_not_jwt(self):
        assert oauth_rs.looks_like_jwt("a.b.c.d") is False

    def test_empty_is_not_jwt(self):
        assert oauth_rs.looks_like_jwt("") is False

    def test_three_dots_but_header_not_json_is_not_jwt(self):
        # Three segments shaped like a JWT but the header is not base64url JSON.
        assert oauth_rs.looks_like_jwt("!!!.payload.sig") is False

    def test_three_segments_header_json_without_alg_is_not_jwt(self):
        # Header decodes to JSON but has no ``alg`` field → not a JWT we route.
        header = _b64url(json.dumps({"typ": "JWT"}).encode())
        payload = _b64url(json.dumps({"sub": "x"}).encode())
        assert oauth_rs.looks_like_jwt(f"{header}.{payload}.sig") is False

    def test_alg_none_header_is_jwt_shaped(self):
        # CRITICAL: an ``alg:none`` token IS JWT-shaped — it MUST classify as
        # JWT (and therefore route to OAuth-only, where it is rejected). It must
        # NOT be misclassified as static-key shaped (which would let it fall
        # onto the static-key path — the exact CD1 bypass).
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps(_base_claims()).encode())
        assert oauth_rs.looks_like_jwt(f"{header}.{payload}.") is True

    def test_static_key_with_two_embedded_dots_but_bad_header(self):
        # Defense: a static key that happens to contain two dots but whose first
        # segment is not base64url-JSON-with-alg is NOT JWT-shaped.
        assert oauth_rs.looks_like_jwt("part1.part2.part3") is False


# ───────────────────────── offline validation ───────────────────────────────


class TestVerifyValid:
    def test_valid_token_returns_claims(self):
        token = _mint(_base_claims(sub="admin-7"))
        claims = oauth_rs.verify_oauth_token(
            token, secret=_SECRET, issuer=_ISSUER
        )
        assert claims["sub"] == "admin-7"
        assert claims["aud"] == _AUDIENCE
        assert claims["scope"] == "mcp"


class TestVerifyRejects:
    """Every failure path raises OAuthTokenError → caller maps to 401."""

    def test_alg_none_rejected(self):
        header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps(_base_claims()).encode())
        token = f"{header}.{payload}."
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_alg_confusion_rs256_header_hs_signed_rejected(self):
        # RS256 header but HMAC-signed with the shared secret — the classic
        # alg-confusion attack (SP2). Pinned HS256 allowlist must reject it.
        import hashlib
        import hmac

        header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = _b64url(json.dumps(_base_claims()).encode())
        signing_input = f"{header}.{payload}".encode()
        sig = _b64url(
            hmac.new(_SECRET.encode(), signing_input, hashlib.sha256).digest()
        )
        token = f"{header}.{payload}.{sig}"
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_bad_signature_rejected(self):
        token = _mint(_base_claims())
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(
                token, secret="a-totally-different-secret-32-bytes-padding", issuer=_ISSUER
            )

    def test_expired_rejected(self):
        token = _mint(_base_claims(exp=int(time.time()) - 10))
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_wrong_audience_rejected(self):
        token = _mint(_base_claims(aud="some-other-rs"))
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_missing_audience_rejected(self):
        claims = _base_claims()
        del claims["aud"]
        token = _mint(claims)
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_wrong_issuer_rejected(self):
        token = _mint(_base_claims(iss="https://evil.example.com"))
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_missing_issuer_rejected(self):
        claims = _base_claims()
        del claims["iss"]
        token = _mint(claims)
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_missing_exp_rejected(self):
        claims = _base_claims()
        del claims["exp"]
        token = _mint(claims)
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_scope_missing_rejected(self):
        claims = _base_claims()
        del claims["scope"]
        token = _mint(claims)
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_scope_without_mcp_rejected(self):
        token = _mint(_base_claims(scope="read write"))
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)

    def test_scope_with_mcp_among_others_accepted(self):
        # Space-delimited scope string containing "mcp" is acceptable.
        token = _mint(_base_claims(scope="mcp extra"))
        claims = oauth_rs.verify_oauth_token(token, secret=_SECRET, issuer=_ISSUER)
        assert "mcp" in claims["scope"].split()

    def test_garbage_token_rejected(self):
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(
                "not.a.jwt", secret=_SECRET, issuer=_ISSUER
            )

    def test_empty_secret_rejected(self):
        # A token must never validate against an empty/absent signing secret.
        token = _mint(_base_claims())
        with pytest.raises(oauth_rs.OAuthTokenError):
            oauth_rs.verify_oauth_token(token, secret="", issuer=_ISSUER)
