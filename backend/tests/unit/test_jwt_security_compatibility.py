"""GH1024: exercise security fixes and compact-JWS compatibility at the auth seam."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest


@pytest.mark.parametrize("decoder", [jwt.decode, jwt.decode_complete])
def test_unverified_peek_does_not_disable_later_expiry_validation(decoder):
    """CVE-2026-103001: reusing options must not retain disabled claim checks."""
    secret = "gh1024-synthetic-signing-key-not-a-real-credential"
    token = jwt.encode(
        {"exp": datetime.now(timezone.utc) - timedelta(hours=1)},
        secret,
        algorithm="HS256",
    )
    options = {"verify_signature": False}

    decoder(token, options=options)
    assert options == {"verify_signature": False}
    options["verify_signature"] = True

    with pytest.raises(jwt.ExpiredSignatureError):
        decoder(token, secret, algorithms=["HS256"], options=options)


def test_signed_recursive_payload_is_an_invalid_token_not_an_uncaught_exception():
    """CVE-2026-101918 must be contained by ECM's existing PyJWTError handler."""
    from auth.tokens import ALGORITHM, InvalidTokenError, _get_secret_key, decode_token

    payload = b'{"nested":' + b"[" * 20000 + b"]" * 20000 + b"}"
    token = jwt.api_jws.encode(payload, _get_secret_key(), algorithm=ALGORITHM)

    with pytest.raises(InvalidTokenError, match="^Invalid token:"):
        decode_token(token)


def test_signature_with_non_base64url_junk_is_rejected():
    """CVE-2026-102269: ignored signature junk must not produce an accepted JWS."""
    from auth.tokens import InvalidTokenError, create_access_token, decode_token

    token = create_access_token(user_id=42, username="compatibility")

    with pytest.raises(InvalidTokenError):
        decode_token(token + "!!!!")


def test_trailing_signature_padding_remains_compatible():
    """2.15.1 restores padded signatures without relaxing alphabet validation."""
    from auth.tokens import create_access_token, decode_token

    token = create_access_token(user_id=42, username="compatibility")
    header, payload, signature = token.split(".")
    padded = f"{header}.{payload}.{signature}{'=' * (-len(signature) % 4)}"

    claims = decode_token(padded)
    assert claims["sub"] == 42
    assert claims["username"] == "compatibility"
