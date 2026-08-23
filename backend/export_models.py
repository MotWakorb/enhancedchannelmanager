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
    # Provider: "s3", "gdrive", "webdav", "onedrive", "dropbox"
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
    # Include the LOGO slice in this target's sync cycles.
    #
    # DEFAULT ON since bead ``…-2yq19`` (migration 0048). It shipped default
    # OFF under bead ``7ipq2.1``, and the reason was COST, not correctness: the
    # logos importer carries a destructive ``clear_existing`` bulk-delete plus a
    # per-logo streaming upload that is wrong to pay every interval. ADR-013's
    # 2026-08-22 governing principle does not disturb that mechanism decision —
    # it disturbs the DEFAULT. An operator who never finds the toggle gets a
    # replica with NO ARTWORK, which is the second half of the failure epic
    # ``f5a5j`` is literally named for ("...has lost its guide data and its
    # branding"). A silent omission is precisely what the principle forbids.
    #
    # The cost is handled by a SUB-INTERVAL rather than by omission — see
    # ``logo_sync_interval_hours`` below. When the slice runs, it still streams
    # missed logos one at a time (D8) and can still never bulk-delete B's logos
    # (``clear_existing`` is hard-disabled in the sync logos step).
    #
    # NOT NULL add to a possibly-populated table, so it carries a
    # server_default like fuzzy_stream_matching above. EXISTING ROWS KEEP THEIR
    # STORED VALUE: migration 0048 changes what a NEW target defaults to and
    # rewrites nobody's saved choice, because a stored ``False`` is
    # indistinguishable from an operator who deliberately turned it off.
    sync_logos = Column(Boolean, nullable=False, server_default=text("1"), default=True)
    # How often the LOGO slice may run, in hours (bead ``…-2yq19``).
    #
    # This is the whole of the cost answer, and it is why the default above can
    # be ON. Logos are not high-churn state: an operator adds artwork
    # occasionally, and a replica that picks it up within a day is faithful in
    # every sense an operator can observe. The config/channel slices stay on the
    # cycle interval; only the expensive slice is throttled.
    #
    # ``0`` means "every cycle" — the pre-sub-interval behaviour, available to
    # an operator who wants it and cheap to reason about. NOT NULL add to a
    # possibly-populated table, so it carries a server_default.
    logo_sync_interval_hours = Column(
        Integer, nullable=False, server_default=text("24"), default=24
    )
    # When the LOGO slice last actually ran for this target (NULL == never).
    #
    # Nullable additive DDL with no server_default, matching the three
    # persisted-state columns above: NULL is the correct "never ran" backfill,
    # and it is also what makes the FIRST cycle after a target is created carry
    # logos immediately rather than making the operator wait out a sub-interval
    # for a replica that has no artwork at all.
    last_logo_sync_at = Column(DateTime, nullable=True)
    # --- Schedules Direct password (PO ruling 2026-08-22) --------------------
    # THE ONE CREDENTIAL THE OPERATOR TYPES, and they type it ONCE, ever.
    #
    # Every other provider credential is harvested off this instance's own
    # records on every cycle and needs no input at all. Schedules Direct cannot
    # be: Dispatcharr marks its password write-only, never returns it, and
    # SHA1-hashes it at fetch, so the value never enters ECM's process and there
    # is nothing on A to read. Absence is UNREADABLE, not unset.
    #
    # Stored Fernet-encrypted through the SAME ``cloud_storage.crypto`` path as
    # this row's own ``credentials`` column, decrypted per cycle, and NEVER
    # returned by any route — not even as ciphertext. :meth:`to_dict` emits a
    # BOOLEAN; a stored credential blob has no business on a response, and a
    # test that only checked "the plaintext is absent" was satisfied by echoing
    # the ciphertext (found by mutation, 2026-08-22).
    #
    # Persisting it is the point: "request-scoped, never persisted" was the
    # previous design and it is exactly the re-typing the ruling removes.
    #
    # WHAT THIS REPLACED. Two timestamp columns —
    # ``credentials_provisioned_at`` and ``destination_credential_observed_at``
    # — recorded whether a one-time provisioning action had run and whether a
    # cycle had observed a credential on the replica. Both existed only to feed
    # the S11 ``insecure`` refusal, which the PO removed. Migration 0047 drops
    # them rather than leaving dead bookkeeping the next reader mistakes for a
    # control.
    schedules_direct_password = Column(Text, nullable=True)
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
            "sync_logos": self.sync_logos,
            "logo_sync_interval_hours": self.logo_sync_interval_hours,
            "last_logo_sync_at": (
                self.last_logo_sync_at.isoformat() + "Z"
                if self.last_logo_sync_at
                else None
            ),
            # PRESENCE, never the value and never the ciphertext.
            "has_schedules_direct_password": bool(self.schedules_direct_password),
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
