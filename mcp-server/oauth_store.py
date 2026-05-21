"""OAuth 2.1 state store — SQLite at /config/mcp_oauth.db (bead buiqr.2).

Foundation layer of the OAuth 2.1 epic (``enhancedchannelmanager-buiqr``).
This module is a clean, framework-agnostic data layer; it holds **no** HTTP
logic, no MCP-SDK coupling, and no FastAPI/Starlette dependency. The
Authorization Server (ECM, bead ``buiqr.3``) and the Resource Server (MCP,
bead ``buiqr.8``) both consume this store. Per ADR-009 §1 and §5 the two
containers **share the SQLite file, not an HTTP endpoint** — the AS *writes*
hashed token/code/refresh/revocation records and the RS *reads* them for
validation/revocation. Neither calls the other at runtime, preserving the
failure-isolation envelope.

Security posture (ADR-009 §5, threat model TS1/T3/SP*):
  - **Hashed-at-rest.** Token/code values are SHA-256 hashed before storage,
    mirroring ``backend/auth/tokens.py`` ``hash_token()``
    (``hashlib.sha256(value.encode()).hexdigest()``). A read of the DB yields
    hashes, not bearer credentials (TS1).
  - **jti revocation.** Revocation marks the ``jti`` revoked, mirroring
    ``revoke_token(jti)`` in the existing JWT subsystem.
  - **Refresh rotation + reuse detection.** Refresh tokens rotate on use; a
    replayed (already-consumed) refresh token is rejected AND invalidates the
    whole token *family* — the standard breach response to refresh reuse (T3).
  - **Parameterized SQL only.** No string interpolation into SQL anywhere.
  - **0600 file mode.** The DB file is created/tightened to owner-only rw.
  - **WAL mode.** Enables concurrent AS-write / RS-read access.

================================================================================
SCHEMA — THE CROSS-CONTAINER CONTRACT
================================================================================
ECM (``buiqr.3``) opens the SAME ``/config/mcp_oauth.db`` file. It MUST use the
identical DDL below (idempotent ``CREATE TABLE IF NOT EXISTS``). The canonical
DDL lives in ``_SCHEMA_DDL`` in this module. The recommended pattern is for
ECM to import this module's schema constant (shared schema, one source of
truth) rather than mirroring the DDL by hand — see the bead report / ADR-009
follow-up for ``buiqr.3``.

All time columns are **epoch seconds** (``int(time.time())``), consistent with
JWT ``iat``/``exp`` and the second-resolution epoch used elsewhere in the
codebase (e.g. ``stream_prober.py``). Boolean columns are stored as INTEGER
0/1 (SQLite has no native bool).

oauth_clients      — the hardcoded OAuth client registry (seeded by buiqr.6).
  client_id        TEXT PRIMARY KEY    — e.g. "claude-desktop", "claude-code".
  client_name      TEXT NOT NULL       — display name pinned on the consent UI.
  redirect_uris    TEXT NOT NULL       — JSON array of exact-match allowlisted URIs.
  created_at       INTEGER NOT NULL    — epoch seconds.

auth_codes         — short-lived PKCE authorization codes (<=10 min TTL).
  code_hash        TEXT PRIMARY KEY    — SHA-256(code). The raw code is never stored.
  client_id        TEXT NOT NULL
  redirect_uri     TEXT NOT NULL       — the redirect_uri presented at /authorize.
  code_challenge   TEXT NOT NULL       — PKCE S256 challenge.
  code_challenge_method TEXT NOT NULL  — "S256" (ADR-009 §3; "plain" rejected upstream).
  scope            TEXT NOT NULL       — "mcp" (single scope, v1).
  user_sub         TEXT NOT NULL       — the ECM admin subject the grant binds to.
  created_at       INTEGER NOT NULL    — epoch seconds.
  expires_at       INTEGER NOT NULL    — epoch seconds; capped at created_at + 600.
  consumed_at      INTEGER             — epoch seconds when consumed; NULL = unused.

access_tokens      — issued access-token records (hashed, for revocation/audit).
  token_hash       TEXT PRIMARY KEY    — SHA-256(token).
  jti              TEXT NOT NULL       — unique token id for revocation (UNIQUE).
  client_id        TEXT NOT NULL
  user_sub         TEXT NOT NULL
  scope            TEXT NOT NULL
  created_at       INTEGER NOT NULL
  expires_at       INTEGER NOT NULL

refresh_tokens     — refresh tokens with rotation/reuse-detection (<=30 day cap).
  token_hash       TEXT PRIMARY KEY    — SHA-256(token).
  jti              TEXT NOT NULL       — unique id (UNIQUE).
  family_id        TEXT NOT NULL       — rotation lineage; reuse kills the family.
  client_id        TEXT NOT NULL
  user_sub         TEXT NOT NULL
  scope            TEXT NOT NULL
  created_at       INTEGER NOT NULL
  expires_at       INTEGER NOT NULL    — capped at created_at + 30 days.
  consumed         INTEGER NOT NULL    — 0 = live, 1 = rotated/consumed.
  consumed_at      INTEGER             — epoch seconds when rotated; NULL = live.

revocations        — explicit revocation ledger (jti-based and family-based).
  id               INTEGER PRIMARY KEY AUTOINCREMENT
  jti              TEXT                — revoked token jti (NULL for family rows).
  family_id        TEXT                — revoked refresh family (NULL for jti rows).
  reason           TEXT
  revoked_at       INTEGER NOT NULL    — epoch seconds.
================================================================================
"""
import hashlib
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

# ── TTL ceilings (ADR-009 §3, §5; epic AC) ──────────────────────────────────
#: Authorization codes expire in at most 10 minutes (AC2).
AUTH_CODE_MAX_TTL_SECONDS = 10 * 60  # 600
#: Refresh tokens are capped at 30 days (AC).
REFRESH_TOKEN_MAX_TTL_SECONDS = 30 * 24 * 60 * 60  # 2_592_000

#: File mode for the DB file: owner read/write only (AC6).
_DB_FILE_MODE = 0o600

# ── Canonical schema DDL (idempotent). THE cross-container contract. ─────────
# ECM (buiqr.3) MUST open the same file with this exact DDL. Importing this
# constant is preferred over mirroring it by hand.
_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id     TEXT PRIMARY KEY,
    client_name   TEXT NOT NULL,
    redirect_uris TEXT NOT NULL,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash             TEXT PRIMARY KEY,
    client_id             TEXT NOT NULL,
    redirect_uri          TEXT NOT NULL,
    code_challenge        TEXT NOT NULL,
    code_challenge_method TEXT NOT NULL,
    scope                 TEXT NOT NULL,
    user_sub              TEXT NOT NULL,
    created_at            INTEGER NOT NULL,
    expires_at            INTEGER NOT NULL,
    consumed_at           INTEGER
);

CREATE TABLE IF NOT EXISTS access_tokens (
    token_hash TEXT PRIMARY KEY,
    jti        TEXT NOT NULL UNIQUE,
    client_id  TEXT NOT NULL,
    user_sub   TEXT NOT NULL,
    scope      TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash  TEXT PRIMARY KEY,
    jti         TEXT NOT NULL UNIQUE,
    family_id   TEXT NOT NULL,
    client_id   TEXT NOT NULL,
    user_sub    TEXT NOT NULL,
    scope       TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    consumed    INTEGER NOT NULL DEFAULT 0,
    consumed_at INTEGER
);

CREATE TABLE IF NOT EXISTS revocations (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    jti        TEXT,
    family_id  TEXT,
    reason     TEXT,
    revoked_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_revocations_jti       ON revocations(jti);
CREATE INDEX IF NOT EXISTS idx_revocations_family    ON revocations(family_id);
CREATE INDEX IF NOT EXISTS idx_refresh_family        ON refresh_tokens(family_id);
CREATE INDEX IF NOT EXISTS idx_auth_codes_expires    ON auth_codes(expires_at);
CREATE INDEX IF NOT EXISTS idx_access_tokens_expires ON access_tokens(expires_at);
"""


class RefreshTokenReuseError(Exception):
    """Raised when an already-rotated (consumed) refresh token is replayed.

    Per the standard refresh-rotation breach response (ADR-009 §3, threat
    model T3), the consumer maps this to HTTP 400 ``invalid_grant`` AND the
    store invalidates the entire token family. A replayed refresh token is a
    signal that the token chain may be compromised.
    """


def hash_secret(value: str) -> str:
    """Return the SHA-256 hex digest of a token/code value (hashed-at-rest).

    Mirrors ``backend/auth/tokens.py`` ``hash_token()`` exactly so the AS and
    RS produce identical hashes for the same value. The plaintext is never
    persisted; only this digest goes into the DB.
    """
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> int:
    """Current epoch time in seconds (matches JWT iat/exp resolution)."""
    return int(time.time())


class OAuthStore:
    """SQLite-backed OAuth state store (WAL, hashed-at-rest, 0600).

    A single instance owns one ``sqlite3.Connection``. Instances are **not**
    shared across threads (sqlite3 connections are per-thread by default);
    each writer/reader thread opens its own ``OAuthStore``. WAL mode lets
    multiple connections (AS writer, RS reader, even across processes/
    containers on the shared ``/config`` volume) operate concurrently.
    """

    def __init__(self, db_path: Union[str, Path]):
        self.db_path = Path(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    def connect(self) -> sqlite3.Connection:
        """Open (or reuse) the connection, enforcing WAL + 0600 file mode.

        Idempotent: calling twice returns the same connection. Use this when
        the schema already exists (e.g. a second container opening the file).
        """
        if self._conn is not None:
            return self._conn

        # Ensure the parent dir exists (e.g. /config on first run).
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Create the file with 0600 BEFORE sqlite opens it, so the bytes are
        # never briefly world-readable. If it already exists, tighten it.
        if not self.db_path.exists():
            # O_CREAT|O_EXCL with mode 0600; closed immediately, sqlite reopens.
            fd = os.open(
                self.db_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, _DB_FILE_MODE
            )
            os.close(fd)
        else:
            self._enforce_file_mode()

        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        # WAL mode for concurrent AS-write / RS-read (AC5, ADR-009 §5).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # Wait up to 5s for a write lock rather than failing immediately —
        # smooths concurrent-writer contention under WAL.
        conn.execute("PRAGMA busy_timeout=5000")
        self._conn = conn

        # WAL creates -wal/-shm sidecar files; tighten their perms too.
        self._enforce_file_mode()
        return conn

    def init_schema(self) -> None:
        """Create all tables (idempotent) and confirm WAL + 0600.

        Safe to call from either container — ``CREATE TABLE IF NOT EXISTS``.
        """
        conn = self.connect()
        conn.executescript(_SCHEMA_DDL)
        conn.commit()
        self._enforce_file_mode()

    def close(self) -> None:
        """Close the underlying connection if open."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _enforce_file_mode(self) -> None:
        """Set the DB file (and WAL sidecars) to 0600 (AC6)."""
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.db_path) + suffix)
            if p.exists():
                try:
                    os.chmod(p, _DB_FILE_MODE)
                except OSError as e:  # pragma: no cover - platform-dependent
                    logger.warning(
                        "[OAUTH-STORE] Could not chmod %s to 0600: %s", p, e
                    )

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            return self.connect()
        return self._conn

    # ── oauth_clients ─────────────────────────────────────────────────────────

    def create_client(
        self, *, client_id: str, client_name: str, redirect_uris: list[str]
    ) -> None:
        """Insert or update a registered OAuth client (idempotent upsert).

        The client registry is hardcoded (ADR-009 §3); this method is the seed
        path (buiqr.6). ``redirect_uris`` is stored as a JSON array of
        exact-match allowlisted URIs.
        """
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                client_name   = excluded.client_name,
                redirect_uris = excluded.redirect_uris
            """,
            (client_id, client_name, json.dumps(redirect_uris), _now()),
        )
        conn.commit()

    def get_client(self, client_id: str) -> Optional[dict[str, Any]]:
        """Return the client record, or None if unregistered."""
        conn = self._require_conn()
        row = conn.execute(
            "SELECT client_id, client_name, redirect_uris, created_at "
            "FROM oauth_clients WHERE client_id = ?",
            (client_id,),
        ).fetchone()
        if row is None:
            return None
        record = dict(row)
        record["redirect_uris"] = json.loads(record["redirect_uris"])
        return record

    # ── auth_codes ────────────────────────────────────────────────────────────

    def create_auth_code(
        self,
        *,
        code: str,
        client_id: str,
        redirect_uri: str,
        code_challenge: str,
        code_challenge_method: str,
        scope: str,
        user_sub: str,
        ttl_seconds: int,
        now: Optional[int] = None,
    ) -> None:
        """Persist a PKCE authorization code (hashed). TTL capped at 10 min.

        The raw ``code`` is hashed before storage. The requested ``ttl_seconds``
        is clamped to ``AUTH_CODE_MAX_TTL_SECONDS`` (AC2) so a caller can never
        mint a code that outlives the 10-minute ceiling.
        """
        now = _now() if now is None else now
        ttl = min(ttl_seconds, AUTH_CODE_MAX_TTL_SECONDS)
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO auth_codes (
                code_hash, client_id, redirect_uri, code_challenge,
                code_challenge_method, scope, user_sub, created_at, expires_at,
                consumed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                hash_secret(code),
                client_id,
                redirect_uri,
                code_challenge,
                code_challenge_method,
                scope,
                user_sub,
                now,
                now + ttl,
            ),
        )
        conn.commit()

    def consume_auth_code(
        self, code: str, now: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """Atomically consume an auth code: return its grant or None.

        Returns the grant record (client_id, redirect_uri, PKCE challenge,
        scope, user_sub) on the first call for a valid, unexpired, unconsumed
        code; subsequent calls return None (single-use replay protection, AC1).
        An expired code (AC2) also returns None.
        """
        now = _now() if now is None else now
        code_hash = hash_secret(code)
        conn = self._require_conn()
        row = conn.execute(
            """
            SELECT client_id, redirect_uri, code_challenge,
                   code_challenge_method, scope, user_sub, expires_at, consumed_at
            FROM auth_codes WHERE code_hash = ?
            """,
            (code_hash,),
        ).fetchone()
        if row is None:
            return None
        if row["consumed_at"] is not None:
            return None  # already used (replay)
        if row["expires_at"] <= now:
            return None  # expired (AC2)
        # Mark consumed atomically — only succeeds if still unconsumed.
        cur = conn.execute(
            "UPDATE auth_codes SET consumed_at = ? "
            "WHERE code_hash = ? AND consumed_at IS NULL",
            (now, code_hash),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None  # lost a race; another caller consumed it
        record = dict(row)
        record.pop("expires_at", None)
        record.pop("consumed_at", None)
        return record

    # ── access_tokens ─────────────────────────────────────────────────────────

    def store_access_token(
        self,
        *,
        token: str,
        jti: str,
        client_id: str,
        user_sub: str,
        scope: str,
        ttl_seconds: int,
        now: Optional[int] = None,
    ) -> None:
        """Persist an issued access token's hashed record (audit + revocation).

        The token is hashed before storage (AC4). The record exists so the RS
        can correlate the ``jti`` for revocation and so issuance is auditable
        (threat model R2).
        """
        now = _now() if now is None else now
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO access_tokens (
                token_hash, jti, client_id, user_sub, scope, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (hash_secret(token), jti, client_id, user_sub, scope, now, now + ttl_seconds),
        )
        conn.commit()

    def get_access_token(
        self, token: str, now: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """Look up an access-token record by value; None if absent/expired.

        Note: this does NOT check revocation — the caller (RS validator,
        buiqr.8) consults :meth:`is_jti_revoked` on the returned ``jti``. This
        keeps the store's responsibilities orthogonal: lookup vs revocation.
        """
        now = _now() if now is None else now
        conn = self._require_conn()
        row = conn.execute(
            "SELECT jti, client_id, user_sub, scope, created_at, expires_at "
            "FROM access_tokens WHERE token_hash = ?",
            (hash_secret(token),),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:
            return None
        return dict(row)

    # ── refresh_tokens ────────────────────────────────────────────────────────

    def store_refresh_token(
        self,
        *,
        token: str,
        jti: str,
        family_id: str,
        client_id: str,
        user_sub: str,
        scope: str,
        ttl_seconds: int,
        now: Optional[int] = None,
    ) -> None:
        """Persist a refresh token (hashed) in a rotation family.

        ``ttl_seconds`` is clamped to ``REFRESH_TOKEN_MAX_TTL_SECONDS`` (30-day
        cap). ``family_id`` ties rotation lineage together so reuse detection
        can invalidate the whole chain.
        """
        now = _now() if now is None else now
        ttl = min(ttl_seconds, REFRESH_TOKEN_MAX_TTL_SECONDS)
        conn = self._require_conn()
        conn.execute(
            """
            INSERT INTO refresh_tokens (
                token_hash, jti, family_id, client_id, user_sub, scope,
                created_at, expires_at, consumed, consumed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            """,
            (
                hash_secret(token),
                jti,
                family_id,
                client_id,
                user_sub,
                scope,
                now,
                now + ttl,
            ),
        )
        conn.commit()

    def get_refresh_token(
        self, token: str, now: Optional[int] = None
    ) -> Optional[dict[str, Any]]:
        """Look up a refresh-token record by value; None if absent/expired.

        Returns the row including ``consumed`` (0 live / 1 rotated) and
        ``family_id`` so callers can reason about rotation state.
        """
        now = _now() if now is None else now
        conn = self._require_conn()
        row = conn.execute(
            "SELECT jti, family_id, client_id, user_sub, scope, "
            "created_at, expires_at, consumed, consumed_at "
            "FROM refresh_tokens WHERE token_hash = ?",
            (hash_secret(token),),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:
            return None
        return dict(row)

    def rotate_refresh_token(
        self,
        *,
        old_token: str,
        new_token: str,
        new_jti: str,
        ttl_seconds: int,
        now: Optional[int] = None,
    ) -> Optional[dict[str, Any]]:
        """Rotate a refresh token: consume the old, persist the new (AC3).

        Returns the old token's grant metadata (client_id, user_sub, scope,
        family_id) on success so the caller can mint the replacement access +
        refresh tokens bound to the same grant. The new refresh token inherits
        the old token's ``family_id``.

        Failure modes:
          - **Unknown / expired old token** → returns ``None`` (not reuse; the
            caller maps this to ``invalid_grant`` with no family kill).
          - **Already-consumed old token (reuse)** → raises
            :class:`RefreshTokenReuseError` AND revokes the whole family
            (breach response, T3).
          - **Family already revoked** → raises :class:`RefreshTokenReuseError`
            (a successor of a breached family can no longer rotate).
        """
        now = _now() if now is None else now
        conn = self._require_conn()
        row = conn.execute(
            "SELECT jti, family_id, client_id, user_sub, scope, expires_at, consumed "
            "FROM refresh_tokens WHERE token_hash = ?",
            (hash_secret(old_token),),
        ).fetchone()
        if row is None:
            return None  # never issued — not reuse, just absent

        family_id = row["family_id"]

        # If the family was already killed (prior breach), reject.
        if self.is_refresh_family_revoked(family_id):
            raise RefreshTokenReuseError(
                f"refresh token family {family_id} is revoked"
            )

        if row["consumed"]:
            # Reuse of an already-rotated token: breach response — kill family.
            self.revoke_refresh_family(
                family_id, reason="refresh-token-reuse-detected", now=now
            )
            raise RefreshTokenReuseError(
                f"refresh token already rotated (family {family_id} invalidated)"
            )

        if row["expires_at"] <= now:
            return None  # expired — not reuse

        ttl = min(ttl_seconds, REFRESH_TOKEN_MAX_TTL_SECONDS)
        # Mark the old token consumed (only if still live — race-safe).
        cur = conn.execute(
            "UPDATE refresh_tokens SET consumed = 1, consumed_at = ? "
            "WHERE token_hash = ? AND consumed = 0",
            (now, hash_secret(old_token)),
        )
        if cur.rowcount != 1:
            # Lost a race: someone consumed it between our read and write.
            # Treat as reuse and kill the family.
            conn.commit()
            self.revoke_refresh_family(
                family_id, reason="refresh-token-reuse-detected", now=now
            )
            raise RefreshTokenReuseError(
                f"refresh token already rotated (family {family_id} invalidated)"
            )
        # Insert the successor in the same family.
        conn.execute(
            """
            INSERT INTO refresh_tokens (
                token_hash, jti, family_id, client_id, user_sub, scope,
                created_at, expires_at, consumed, consumed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            """,
            (
                hash_secret(new_token),
                new_jti,
                family_id,
                row["client_id"],
                row["user_sub"],
                row["scope"],
                now,
                now + ttl,
            ),
        )
        conn.commit()
        return {
            "jti": row["jti"],
            "family_id": family_id,
            "client_id": row["client_id"],
            "user_sub": row["user_sub"],
            "scope": row["scope"],
        }

    # ── revocations ───────────────────────────────────────────────────────────

    def revoke_jti(
        self, jti: str, reason: Optional[str] = None, now: Optional[int] = None
    ) -> None:
        """Mark a token ``jti`` revoked (mirrors ``revoke_token(jti)``).

        Idempotent at the API level — re-revoking the same jti is harmless;
        :meth:`is_jti_revoked` is the read side.
        """
        now = _now() if now is None else now
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO revocations (jti, family_id, reason, revoked_at) "
            "VALUES (?, NULL, ?, ?)",
            (jti, reason, now),
        )
        conn.commit()

    def is_jti_revoked(self, jti: str) -> bool:
        """True if the given token ``jti`` has been revoked."""
        conn = self._require_conn()
        row = conn.execute(
            "SELECT 1 FROM revocations WHERE jti = ? LIMIT 1", (jti,)
        ).fetchone()
        return row is not None

    def revoke_refresh_family(
        self, family_id: str, reason: Optional[str] = None, now: Optional[int] = None
    ) -> None:
        """Revoke an entire refresh-token family (rotation-reuse breach kill).

        Records a family revocation row AND marks every refresh token in the
        family consumed so no successor can rotate (T3 breach response).
        """
        now = _now() if now is None else now
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO revocations (jti, family_id, reason, revoked_at) "
            "VALUES (NULL, ?, ?, ?)",
            (family_id, reason, now),
        )
        conn.execute(
            "UPDATE refresh_tokens SET consumed = 1, consumed_at = ? "
            "WHERE family_id = ? AND consumed = 0",
            (now, family_id),
        )
        conn.commit()

    def is_refresh_family_revoked(self, family_id: str) -> bool:
        """True if the refresh-token family has been revoked."""
        conn = self._require_conn()
        row = conn.execute(
            "SELECT 1 FROM revocations WHERE family_id = ? LIMIT 1", (family_id,)
        ).fetchone()
        return row is not None
