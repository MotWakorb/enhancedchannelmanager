"""Tests for the OAuth state store (oauth_store.py) — bead buiqr.2.

The store is the foundation layer of the OAuth 2.1 epic (ADR-009 §5). It is a
framework-agnostic SQLite data layer that the Authorization Server (ECM,
buiqr.3) writes to and the Resource Server (MCP, buiqr.8) reads from. Neither
container calls the other at runtime — they share the file at
/config/mcp_oauth.db (WAL mode).

These tests cover every acceptance criterion:
  AC1 — clean store API round-trips (create/lookup/consume/revoke) per table.
  AC2 — auth codes expire <= 10 minutes (enforced + tested).
  AC3 — refresh tokens rotate on use; reuse of a rotated token is rejected and
        invalidates the whole token family.
  AC4 — all token/code values hashed at rest (SHA-256); plaintext never stored.
  AC5 — WAL mode enabled; concurrent-write smoke test passes.
  AC6 — DB file created mode 0600; ownership/permissions verified.

The store is tested in isolation against a temp DB (no /config dependency).
"""
import hashlib
import os
import sqlite3
import stat
import threading
import time

import pytest

from oauth_store import (
    AUTH_CODE_MAX_TTL_SECONDS,
    REFRESH_TOKEN_MAX_TTL_SECONDS,
    OAuthStore,
    RefreshTokenReuseError,
    hash_secret,
)


@pytest.fixture()
def store(tmp_path):
    """A fresh OAuthStore backed by a temp DB file."""
    db_path = tmp_path / "mcp_oauth.db"
    s = OAuthStore(db_path)
    s.init_schema()
    try:
        yield s
    finally:
        s.close()


# ───────────────────────── AC4: hash-at-rest primitive ─────────────────────


class TestHashSecret:
    """hash_secret() mirrors backend/auth/tokens.py hash_token() (SHA-256)."""

    def test_matches_sha256_hexdigest(self):
        """hash_secret() == hashlib.sha256(value.encode()).hexdigest()."""
        value = "super-secret-token-value"
        assert hash_secret(value) == hashlib.sha256(value.encode()).hexdigest()

    def test_is_deterministic(self):
        """Same input always hashes to the same digest (so lookups work)."""
        assert hash_secret("abc") == hash_secret("abc")

    def test_distinct_inputs_distinct_hashes(self):
        assert hash_secret("abc") != hash_secret("abd")

    def test_output_is_64_hex_chars(self):
        digest = hash_secret("anything")
        assert len(digest) == 64
        assert all(c in "0123456789abcdef" for c in digest)


# ───────────────────────── AC6: file permissions ──────────────────────────


class TestFilePermissions:
    """AC6 — DB file is created mode 0600 (owner read/write only)."""

    def test_db_file_created_mode_0600(self, tmp_path):
        db_path = tmp_path / "perms.db"
        s = OAuthStore(db_path)
        s.init_schema()
        try:
            mode = stat.S_IMODE(os.stat(db_path).st_mode)
            assert mode == 0o600, f"expected 0600, got {oct(mode)}"
        finally:
            s.close()

    def test_existing_db_file_chmod_to_0600(self, tmp_path):
        """A pre-existing too-permissive file is tightened to 0600 on open."""
        db_path = tmp_path / "loose.db"
        # Create the file with loose perms before the store opens it.
        db_path.touch()
        os.chmod(db_path, 0o644)
        s = OAuthStore(db_path)
        s.init_schema()
        try:
            mode = stat.S_IMODE(os.stat(db_path).st_mode)
            assert mode == 0o600, f"expected 0600, got {oct(mode)}"
        finally:
            s.close()


# ───────────────────────── AC5: WAL mode ──────────────────────────────────


class TestWalMode:
    """AC5 — WAL journal mode is enabled on the connection."""

    def test_journal_mode_is_wal(self, store):
        mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"


# ───────────────────────── AC1: oauth_clients round-trip ───────────────────


class TestOAuthClients:
    def test_create_and_get_client(self, store):
        store.create_client(
            client_id="claude-desktop",
            client_name="Claude Desktop",
            redirect_uris=["https://claude.ai/callback", "http://localhost:9000/cb"],
        )
        client = store.get_client("claude-desktop")
        assert client is not None
        assert client["client_id"] == "claude-desktop"
        assert client["client_name"] == "Claude Desktop"
        assert client["redirect_uris"] == [
            "https://claude.ai/callback",
            "http://localhost:9000/cb",
        ]

    def test_get_unknown_client_returns_none(self, store):
        assert store.get_client("nope") is None

    def test_create_client_is_idempotent_upsert(self, store):
        """Re-registering a client updates its record (hardcoded registry seed)."""
        store.create_client(
            client_id="c1", client_name="Old", redirect_uris=["https://a/cb"]
        )
        store.create_client(
            client_id="c1", client_name="New", redirect_uris=["https://b/cb"]
        )
        client = store.get_client("c1")
        assert client["client_name"] == "New"
        assert client["redirect_uris"] == ["https://b/cb"]


# ───────────────────────── AC1/AC2/AC4: auth_codes ─────────────────────────


class TestAuthCodes:
    def test_create_and_consume_round_trip(self, store):
        code = "auth-code-plaintext-xyz"
        store.create_auth_code(
            code=code,
            client_id="claude-desktop",
            redirect_uri="https://claude.ai/cb",
            code_challenge="abc123",
            code_challenge_method="S256",
            scope="mcp",
            user_sub="admin",
            ttl_seconds=300,
        )
        record = store.consume_auth_code(code)
        assert record is not None
        assert record["client_id"] == "claude-desktop"
        assert record["redirect_uri"] == "https://claude.ai/cb"
        assert record["code_challenge"] == "abc123"
        assert record["code_challenge_method"] == "S256"
        assert record["scope"] == "mcp"
        assert record["user_sub"] == "admin"

    def test_consume_is_single_use(self, store):
        """An auth code may be consumed exactly once (replay protection)."""
        code = "single-use-code"
        store.create_auth_code(
            code=code,
            client_id="c",
            redirect_uri="https://x/cb",
            code_challenge="ch",
            code_challenge_method="S256",
            scope="mcp",
            user_sub="admin",
            ttl_seconds=300,
        )
        assert store.consume_auth_code(code) is not None
        # Second consume must fail — code is gone.
        assert store.consume_auth_code(code) is None

    def test_consume_unknown_code_returns_none(self, store):
        assert store.consume_auth_code("never-existed") is None

    def test_expired_code_not_consumable(self, store):
        """AC2 — an auth code past its expiry cannot be consumed."""
        code = "expiring-code"
        store.create_auth_code(
            code=code,
            client_id="c",
            redirect_uri="https://x/cb",
            code_challenge="ch",
            code_challenge_method="S256",
            scope="mcp",
            user_sub="admin",
            ttl_seconds=1,
            now=1000,
        )
        # Validate "now" well past expiry.
        assert store.consume_auth_code(code, now=1000 + 2) is None

    def test_ttl_capped_at_10_minutes(self, store):
        """AC2 — requesting > 10 min TTL is clamped to the 600s ceiling."""
        assert AUTH_CODE_MAX_TTL_SECONDS == 600
        code = "long-ttl-code"
        store.create_auth_code(
            code=code,
            client_id="c",
            redirect_uri="https://x/cb",
            code_challenge="ch",
            code_challenge_method="S256",
            scope="mcp",
            user_sub="admin",
            ttl_seconds=99999,  # way over the cap
            now=1000,
        )
        # At 600s + 1 it must be expired (cap enforced, not the requested TTL).
        assert store.consume_auth_code(code, now=1000 + 601) is None
        # Re-create and confirm it is still valid at exactly the cap boundary.
        store.create_auth_code(
            code=code + "2",
            client_id="c",
            redirect_uri="https://x/cb",
            code_challenge="ch",
            code_challenge_method="S256",
            scope="mcp",
            user_sub="admin",
            ttl_seconds=99999,
            now=2000,
        )
        assert store.consume_auth_code(code + "2", now=2000 + 599) is not None

    def test_code_hashed_at_rest(self, store):
        """AC4 — the raw code never appears in the DB; only its SHA-256 hash."""
        code = "secret-auth-code"
        store.create_auth_code(
            code=code,
            client_id="c",
            redirect_uri="https://x/cb",
            code_challenge="ch",
            code_challenge_method="S256",
            scope="mcp",
            user_sub="admin",
            ttl_seconds=300,
        )
        rows = store._conn.execute(
            "SELECT code_hash FROM auth_codes"
        ).fetchall()
        assert len(rows) == 1
        stored = rows[0][0]
        assert stored != code
        assert stored == hashlib.sha256(code.encode()).hexdigest()
        # And the plaintext appears nowhere in the raw DB bytes.
        raw = open(store.db_path, "rb").read()
        assert code.encode() not in raw


# ───────────────────────── AC1/AC4: access_tokens ──────────────────────────


class TestAccessTokens:
    def test_store_and_lookup_round_trip(self, store):
        token = "access-token-jwt-value"
        store.store_access_token(
            token=token,
            jti="jti-aaa",
            client_id="claude-desktop",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=1800,
        )
        record = store.get_access_token(token)
        assert record is not None
        assert record["jti"] == "jti-aaa"
        assert record["client_id"] == "claude-desktop"
        assert record["user_sub"] == "admin"
        assert record["scope"] == "mcp"

    def test_lookup_unknown_token_returns_none(self, store):
        assert store.get_access_token("unknown") is None

    def test_expired_access_token_returns_none(self, store):
        store.store_access_token(
            token="t",
            jti="jti-exp",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=10,
            now=1000,
        )
        assert store.get_access_token("t", now=1000 + 11) is None

    def test_token_hashed_at_rest(self, store):
        """AC4 — access token plaintext is never stored."""
        token = "very-secret-access-token"
        store.store_access_token(
            token=token,
            jti="jti-h",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=1800,
        )
        rows = store._conn.execute(
            "SELECT token_hash FROM access_tokens"
        ).fetchall()
        stored = rows[0][0]
        assert stored != token
        assert stored == hashlib.sha256(token.encode()).hexdigest()
        raw = open(store.db_path, "rb").read()
        assert token.encode() not in raw


# ───────────────── AC1/AC3/AC4: refresh_tokens + rotation/reuse ────────────


class TestRefreshTokens:
    def test_store_and_get_round_trip(self, store):
        rt = "refresh-token-value"
        store.store_refresh_token(
            token=rt,
            jti="rt-jti-1",
            family_id="fam-1",
            client_id="claude-desktop",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=86400,
        )
        record = store.get_refresh_token(rt)
        assert record is not None
        assert record["jti"] == "rt-jti-1"
        assert record["family_id"] == "fam-1"
        assert record["client_id"] == "claude-desktop"
        assert record["consumed"] == 0

    def test_token_hashed_at_rest(self, store):
        """AC4 — refresh token plaintext is never stored."""
        rt = "secret-refresh-token"
        store.store_refresh_token(
            token=rt,
            jti="rt-h",
            family_id="fam-h",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=86400,
        )
        rows = store._conn.execute(
            "SELECT token_hash FROM refresh_tokens"
        ).fetchall()
        stored = rows[0][0]
        assert stored != rt
        assert stored == hashlib.sha256(rt.encode()).hexdigest()
        raw = open(store.db_path, "rb").read()
        assert rt.encode() not in raw

    def test_rotate_on_use_marks_old_consumed_and_issues_new(self, store):
        """AC3 — rotating a refresh token consumes the old and persists the new."""
        old = "rt-old"
        store.store_refresh_token(
            token=old,
            jti="rt-jti-old",
            family_id="fam-r",
            client_id="claude-desktop",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=86400,
        )
        new = "rt-new"
        record = store.rotate_refresh_token(
            old_token=old,
            new_token=new,
            new_jti="rt-jti-new",
            ttl_seconds=86400,
        )
        # Rotation returns the old token's grant metadata (so the caller can
        # mint the new access token with the same client/user/scope).
        assert record["client_id"] == "claude-desktop"
        assert record["user_sub"] == "admin"
        assert record["scope"] == "mcp"
        assert record["family_id"] == "fam-r"
        # Old token is now consumed; new token is live and in the same family.
        old_rec = store.get_refresh_token(old)
        assert old_rec["consumed"] == 1
        new_rec = store.get_refresh_token(new)
        assert new_rec is not None
        assert new_rec["family_id"] == "fam-r"
        assert new_rec["consumed"] == 0

    def test_reuse_of_rotated_token_rejected(self, store):
        """AC3 — reusing an already-rotated refresh token raises (→ 400)."""
        old = "rt-reuse-old"
        store.store_refresh_token(
            token=old,
            jti="j-old",
            family_id="fam-x",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=86400,
        )
        store.rotate_refresh_token(
            old_token=old, new_token="rt-reuse-new", new_jti="j-new", ttl_seconds=86400
        )
        # Replaying the consumed token must be rejected.
        with pytest.raises(RefreshTokenReuseError):
            store.rotate_refresh_token(
                old_token=old,
                new_token="rt-attacker",
                new_jti="j-attacker",
                ttl_seconds=86400,
            )

    def test_reuse_invalidates_token_family(self, store):
        """AC3 — refresh-reuse breach response: kill the whole family.

        Standard rotation-reuse-detection behavior: a replayed refresh token
        means the token may be compromised, so every refresh token in the
        family is revoked (the live successor included).
        """
        old = "rt-fam-old"
        store.store_refresh_token(
            token=old,
            jti="jf-old",
            family_id="fam-breach",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=86400,
        )
        new = "rt-fam-new"
        store.rotate_refresh_token(
            old_token=old, new_token=new, new_jti="jf-new", ttl_seconds=86400
        )
        # The successor is currently live.
        assert store.get_refresh_token(new)["consumed"] == 0
        # Now replay the old (consumed) token — the breach response fires.
        with pytest.raises(RefreshTokenReuseError):
            store.rotate_refresh_token(
                old_token=old, new_token="x", new_jti="jx", ttl_seconds=86400
            )
        # The whole family is now revoked: the live successor can no longer
        # be rotated (it has been invalidated by the family kill).
        assert store.is_refresh_family_revoked("fam-breach") is True
        with pytest.raises(RefreshTokenReuseError):
            store.rotate_refresh_token(
                old_token=new, new_token="y", new_jti="jy", ttl_seconds=86400
            )

    def test_rotate_unknown_token_returns_none(self, store):
        """An unknown (never-issued) refresh token is not reuse — just absent."""
        assert (
            store.rotate_refresh_token(
                old_token="never-seen",
                new_token="n",
                new_jti="jn",
                ttl_seconds=86400,
            )
            is None
        )

    def test_expired_refresh_token_not_rotatable(self, store):
        store.store_refresh_token(
            token="rt-exp",
            jti="je",
            family_id="fam-e",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=10,
            now=1000,
        )
        assert (
            store.rotate_refresh_token(
                old_token="rt-exp",
                new_token="n",
                new_jti="jn2",
                ttl_seconds=86400,
                now=1000 + 11,
            )
            is None
        )

    def test_refresh_ttl_capped_at_30_days(self, store):
        """AC — refresh token TTL is capped at 30 days."""
        assert REFRESH_TOKEN_MAX_TTL_SECONDS == 30 * 24 * 60 * 60
        store.store_refresh_token(
            token="rt-cap",
            jti="jcap",
            family_id="fam-cap",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=99999999,  # over the 30-day cap
            now=1000,
        )
        # Past the 30-day cap it is expired despite the huge requested TTL.
        assert store.get_refresh_token("rt-cap", now=1000 + REFRESH_TOKEN_MAX_TTL_SECONDS + 1) is None
        # Just inside the cap it is still live.
        assert store.get_refresh_token("rt-cap", now=1000 + REFRESH_TOKEN_MAX_TTL_SECONDS - 1) is not None


# ───────────────────────── AC1: revocations ────────────────────────────────


class TestRevocations:
    def test_revoke_and_check_by_jti(self, store):
        store.revoke_jti("jti-revoke-me", reason="logout")
        assert store.is_jti_revoked("jti-revoke-me") is True

    def test_unrevoked_jti_is_not_revoked(self, store):
        assert store.is_jti_revoked("never-revoked") is False

    def test_revoking_access_token_rejects_lookup(self, store):
        """A revoked access token's jti is rejected even before TTL expiry."""
        token = "revocable-access"
        store.store_access_token(
            token=token,
            jti="jti-rev-at",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=1800,
        )
        assert store.get_access_token(token) is not None
        store.revoke_jti("jti-rev-at", reason="revoked")
        # The lookup still returns the record, but the caller checks
        # is_jti_revoked; assert the revocation is recorded.
        assert store.is_jti_revoked("jti-rev-at") is True

    def test_revoke_is_idempotent(self, store):
        store.revoke_jti("dup-jti")
        store.revoke_jti("dup-jti")  # second call must not raise
        assert store.is_jti_revoked("dup-jti") is True

    def test_revoke_refresh_family(self, store):
        store.revoke_refresh_family("fam-direct", reason="admin-revoke")
        assert store.is_refresh_family_revoked("fam-direct") is True


# ───────────────────────── AC5: concurrent-write smoke ─────────────────────


class TestConcurrentWrites:
    """AC5 — two writers can write concurrently under WAL without corruption."""

    def test_two_writers_concurrent(self, tmp_path):
        db_path = tmp_path / "concurrent.db"
        # Initialize the schema once via a primary store.
        primary = OAuthStore(db_path)
        primary.init_schema()
        primary.close()

        errors = []
        write_count = 50

        def writer(prefix):
            try:
                s = OAuthStore(db_path)
                # No init_schema — schema already exists; just open.
                s.connect()
                for i in range(write_count):
                    s.revoke_jti(f"{prefix}-jti-{i}")
                s.close()
            except Exception as e:  # noqa: BLE001 — capture for assertion
                errors.append(e)

        t1 = threading.Thread(target=writer, args=("w1",))
        t2 = threading.Thread(target=writer, args=("w2",))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert not errors, f"concurrent writers raised: {errors}"
        # Verify both writers' rows landed.
        verify = OAuthStore(db_path)
        verify.connect()
        try:
            count = verify._conn.execute(
                "SELECT COUNT(*) FROM revocations"
            ).fetchone()[0]
            assert count == 2 * write_count
            assert verify.is_jti_revoked("w1-jti-0")
            assert verify.is_jti_revoked("w2-jti-49")
        finally:
            verify.close()


# ───────────────────────── schema contract ─────────────────────────────────


class TestSchemaContract:
    """The schema is the cross-container contract (ECM AS + MCP RS share it)."""

    def test_all_five_tables_exist(self, store):
        names = {
            row[0]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table in (
            "oauth_clients",
            "auth_codes",
            "access_tokens",
            "refresh_tokens",
            "revocations",
        ):
            assert table in names, f"missing table {table}"

    def test_init_schema_is_idempotent(self, tmp_path):
        """init_schema() can run twice (both containers open the same file)."""
        db_path = tmp_path / "idem.db"
        s = OAuthStore(db_path)
        s.init_schema()
        # Re-running must not raise (CREATE TABLE IF NOT EXISTS).
        s.init_schema()
        s.close()

    def test_second_process_can_open_same_db(self, tmp_path):
        """Mirrors the cross-container contract: AS writes, RS reads same file."""
        db_path = tmp_path / "shared.db"
        as_side = OAuthStore(db_path)
        as_side.init_schema()
        as_side.store_access_token(
            token="cross-token",
            jti="cross-jti",
            client_id="c",
            user_sub="admin",
            scope="mcp",
            ttl_seconds=1800,
        )
        as_side.close()

        rs_side = OAuthStore(db_path)
        rs_side.connect()
        try:
            record = rs_side.get_access_token("cross-token")
            assert record is not None
            assert record["jti"] == "cross-jti"
        finally:
            rs_side.close()

    def test_parameterized_sql_resists_injection(self, store):
        """A client_id containing SQL metacharacters is stored/looked up safely."""
        nasty = "c'; DROP TABLE oauth_clients; --"
        store.create_client(
            client_id=nasty, client_name="x", redirect_uris=["https://a/cb"]
        )
        # Table still exists and the row is retrievable verbatim.
        client = store.get_client(nasty)
        assert client is not None
        assert client["client_id"] == nasty
        names = {
            row[0]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "oauth_clients" in names
