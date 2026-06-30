"""
SQLAlchemy ORM models for cloud storage targets and cross-instance sync targets.
Tables: cloud_storage_targets, sync_targets.

These are consumed by the cloud-target router (``routers/cloud_targets.py``), DBAS
backup/sync (``tasks/dbas_backup.py``, ``tasks/dbas_sync*.py``,
``routers/sync_targets.py``), and the ``list_cloud_targets`` MCP tool. The
former Export-tab models (playlist_profiles, publish_configurations,
publish_history) were removed with the Export tab (beads vrrxv / 1w428).
"""
import logging
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, event, text, inspect as sa_inspect
from db_base import Base

logger = logging.getLogger(__name__)


class CloudStorageTarget(Base):
    """Cloud storage destination for published exports."""
    __tablename__ = "cloud_storage_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    # Provider: "s3", "gdrive", "onedrive", "dropbox"
    provider_type = Column(String(20), nullable=False)
    # Encrypted JSON with provider-specific credentials
    credentials = Column(Text, nullable=False, default="{}")
    upload_path = Column(String(500), nullable=False, default="/")
    enabled = Column(Boolean, nullable=False, default=True)
    # Monotonic counter bumped on EVERY credentials write (see the
    # before_insert/before_update listeners below). Scheduled / deferred
    # operations capture the version they were configured against and
    # re-check it before firing, so a rotated or revoked credential never
    # gets used silently (ADR-008 Security Mandatory #5, bead 0i2vt.4).
    # server_default='1' because this column lands on a table that may
    # already hold live rows — a NOT NULL add without a server default
    # fails on SQLite. ORM default=1 keeps freshly-constructed instances
    # consistent before flush.
    credential_version = Column(Integer, nullable=False, server_default="1", default=1)
    # Set when the credentials are explicitly revoked; a non-NULL value is a
    # hard stop for any scheduled op that resolves this target (consumed by
    # 0i2vt.6 / .8). NULL == not revoked.
    token_revoked_at = Column(DateTime, nullable=True)
    # Per-target TLS-verification skip flag. Default False (verify TLS).
    # TODO(0i2vt.8): when this is True the cloud-upload path must skip TLS
    #   verification AND emit a per-request audit log entry recording that an
    #   insecure connection was made to this target. The column lands now so
    #   .8 isn't gated on a migration; the per-request audit is .8's work.
    # server_default='0' because this column lands on a table that may already
    # hold live rows (NOT NULL add without a default fails on populated SQLite).
    # Existing rows backfill to False (verify TLS).
    insecure = Column(Boolean, nullable=False, server_default=text("0"), default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self, mask_credentials: bool = True) -> dict:
        # Credentials are stored encrypted — don't try to parse here.
        # The router layer handles decryption and masking.
        return {
            "id": self.id,
            "name": self.name,
            "provider_type": self.provider_type,
            "credentials": {} if mask_credentials else self.credentials,
            "upload_path": self.upload_path,
            "enabled": self.enabled,
            "credential_version": self.credential_version,
            "token_revoked_at": self.token_revoked_at.isoformat() + "Z" if self.token_revoked_at else None,
            "insecure": self.insecure,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<CloudStorageTarget(id={self.id}, name={self.name}, provider={self.provider_type})>"


@event.listens_for(CloudStorageTarget, "before_insert")
def _cloud_target_init_credential_version(mapper, connection, target):
    """Ensure a freshly-inserted target starts at credential_version >= 1.

    The ORM/server defaults already supply 1; this guards against a caller
    that explicitly passed None or 0.
    """
    if not target.credential_version or target.credential_version < 1:
        target.credential_version = 1


@event.listens_for(CloudStorageTarget, "before_update")
def _cloud_target_bump_credential_version(mapper, connection, target):
    """Bump credential_version IN THE SAME TRANSACTION as a credentials write.

    Fires inside the flush that emits the UPDATE, so the version bump and the
    new ciphertext land in one atomic statement — no read-modify-write across
    two commits (ADR-008 Security Mandatory #5, bead 0i2vt.4 AC#2).

    The bump is gated on ``credentials`` actually being dirty: a metadata-only
    update (rename, toggle ``enabled``, change ``upload_path``) must NOT bump
    the version, otherwise every unrelated edit would invalidate live
    schedules. SQLAlchemy's attribute history tells us whether ``credentials``
    changed in this flush.
    """
    hist = sa_inspect(target).attrs.credentials.history
    if hist.has_changes():
        target.credential_version = (target.credential_version or 0) + 1


class SyncTarget(Base):
    """Dispatcharr-B sync destination with Fernet-encrypted credentials.

    SCHEMA-PRESENT / CODE-ABSENT (deliberate): this model and its
    ``sync_targets`` table land in v0.18.0 so the v0.18.1 cross-instance sync
    feature is not gated on a migration when it ships. There are NO v0.18.0
    API endpoints, Pydantic schemas, or service code that touch this table —
    it is excluded from the public OpenAPI surface by virtue of having no
    router. Do not add endpoints here until v0.18.1 (bead 0i2vt.4).

    Mirrors CloudStorageTarget's credential-freshness contract:
    ``credential_version`` bumps on every credentials write (same-txn, via the
    listeners below) and ``token_revoked_at`` hard-stops a stale scheduled op.
    """
    __tablename__ = "sync_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    # Base URL of the remote Dispatcharr-B instance.
    base_url = Column(String(500), nullable=False)
    # Encrypted JSON with the remote instance credentials (token / user+pass).
    credentials = Column(Text, nullable=False, default="{}")
    enabled = Column(Boolean, nullable=False, default=True)
    # Same credential-freshness contract as CloudStorageTarget. This is a new
    # table created in one statement, so there are no pre-existing rows a
    # NOT NULL add would orphan — no server_default needed (matches the
    # auto_creation_snapshots precedent). ORM default=1.
    credential_version = Column(Integer, nullable=False, default=1)
    token_revoked_at = Column(DateTime, nullable=True)
    # Per-target TLS-verification skip flag (see CloudStorageTarget.insecure).
    insecure = Column(Boolean, nullable=False, default=False)
    # --- Persisted sync state (v0.18.1, bead vigbu; SPIKE xp6mp DBA Ruling 2) ---
    # These columns land ADDITIVELY onto a table that already exists (created in
    # 0023). The three bookkeeping columns are NULLABLE — there is genuinely no
    # last sync yet for a freshly-configured target — so they need NO
    # server_default (a NULL backfill is the correct "never synced" sentinel).
    # ``fuzzy_stream_matching`` is the one NOT-NULL add and therefore DOES carry
    # a server_default('0'): a NOT NULL column added to a populated table fails
    # on SQLite without one (docs/database_migrations.md — the smart-bootstrap
    # stamp-skip trap). Existing rows backfill to False (exact stream matching).
    # Timestamp of the last completed full sync run (NULL == never synced).
    last_full_sync_at = Column(DateTime, nullable=True)
    # Outcome of the last sync run ("success", "failed", "partial", ...);
    # NULL == never synced. Free-form short string set by the sync engine (tjaey).
    last_outcome = Column(String(40), nullable=True)
    # Fingerprint of the source config the last sync pushed, for idempotent
    # skip-if-unchanged on the next run (NULL == never synced).
    last_source_fingerprint = Column(Text, nullable=True)
    # Opt-in: include the stream floor in the sync and fuzzy-match streams on the
    # remote instead of requiring exact identity. Default False (exact matching).
    fuzzy_stream_matching = Column(Boolean, nullable=False, server_default=text("0"), default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def to_dict(self, mask_credentials: bool = True) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "credentials": {} if mask_credentials else self.credentials,
            "enabled": self.enabled,
            "credential_version": self.credential_version,
            "token_revoked_at": self.token_revoked_at.isoformat() + "Z" if self.token_revoked_at else None,
            "insecure": self.insecure,
            "last_full_sync_at": self.last_full_sync_at.isoformat() + "Z" if self.last_full_sync_at else None,
            "last_outcome": self.last_outcome,
            "last_source_fingerprint": self.last_source_fingerprint,
            "fuzzy_stream_matching": self.fuzzy_stream_matching,
            "created_at": self.created_at.isoformat() + "Z" if self.created_at else None,
            "updated_at": self.updated_at.isoformat() + "Z" if self.updated_at else None,
        }

    def __repr__(self):
        return f"<SyncTarget(id={self.id}, name={self.name})>"


@event.listens_for(SyncTarget, "before_insert")
def _sync_target_init_credential_version(mapper, connection, target):
    if not target.credential_version or target.credential_version < 1:
        target.credential_version = 1


@event.listens_for(SyncTarget, "before_update")
def _sync_target_bump_credential_version(mapper, connection, target):
    """Same-txn credential_version bump on a credentials write (see CloudStorageTarget)."""
    hist = sa_inspect(target).attrs.credentials.history
    if hist.has_changes():
        target.credential_version = (target.credential_version or 0) + 1
