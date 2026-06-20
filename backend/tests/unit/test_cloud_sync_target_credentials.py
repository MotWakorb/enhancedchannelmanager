"""ORM-level tests for the credential-freshness contract on CloudStorageTarget
and SyncTarget (bead enhancedchannelmanager-0i2vt.4).

Covers:
* credential_version defaults to 1 on a fresh row.
* credential_version bumps on a credentials write, in the SAME transaction
  (single flush) as the write — no read-modify-write across two commits.
* a metadata-only update (rename, enable toggle) does NOT bump the version.
* token_revoked_at defaults to NULL and round-trips.
* to_dict() masks credentials by default and never emits ciphertext.
* SyncTarget mirrors the same contract.
* SyncTarget is absent from the application's OpenAPI schema (schema-present /
  code-absent intent — no v0.18.0 endpoints).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import inspect as sa_inspect

from export_models import CloudStorageTarget, SyncTarget


class TestCloudStorageTargetCredentialVersion:
    def test_credential_version_defaults_to_one(self, test_session):
        target = CloudStorageTarget(
            name="t-default",
            provider_type="s3",
            credentials="ciphertext-v1",
        )
        test_session.add(target)
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version == 1

    def test_token_revoked_at_defaults_to_null(self, test_session):
        target = CloudStorageTarget(
            name="t-revoke-default",
            provider_type="s3",
            credentials="ciphertext",
        )
        test_session.add(target)
        test_session.commit()
        test_session.refresh(target)
        assert target.token_revoked_at is None

    def test_insecure_defaults_to_false(self, test_session):
        target = CloudStorageTarget(
            name="t-insecure-default",
            provider_type="s3",
            credentials="ciphertext",
        )
        test_session.add(target)
        test_session.commit()
        test_session.refresh(target)
        assert target.insecure is False

    def test_credential_version_bumps_on_credentials_write(self, test_session):
        target = CloudStorageTarget(
            name="t-bump",
            provider_type="s3",
            credentials="ciphertext-v1",
        )
        test_session.add(target)
        test_session.commit()
        assert target.credential_version == 1

        target.credentials = "ciphertext-v2"
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version == 2

        target.credentials = "ciphertext-v3"
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version == 3

    def test_credential_version_bump_is_same_transaction_as_write(self, test_session):
        """The bump must happen in the SAME flush that emits the UPDATE.

        We assert that after a single flush (no intermediate commit), both the
        new ciphertext AND the bumped version are persisted together — i.e. the
        version is not bumped by a separate later write.
        """
        target = CloudStorageTarget(
            name="t-same-txn",
            provider_type="s3",
            credentials="ciphertext-v1",
        )
        test_session.add(target)
        test_session.commit()

        target.credentials = "ciphertext-v2"
        # Single flush — the before_update listener bumps the version inside it.
        test_session.flush()
        assert target.credential_version == 2
        # The ciphertext and version are both dirty-flushed in one statement;
        # no further changes are pending after the flush.
        assert not sa_inspect(target).attrs.credentials.history.has_changes()

    def test_metadata_only_update_does_not_bump_version(self, test_session):
        """Rename / toggle without touching credentials must NOT bump."""
        target = CloudStorageTarget(
            name="t-meta",
            provider_type="s3",
            credentials="ciphertext-v1",
        )
        test_session.add(target)
        test_session.commit()
        assert target.credential_version == 1

        target.name = "t-meta-renamed"
        target.enabled = False
        target.upload_path = "/new/path"
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version == 1, (
            "metadata-only update must not invalidate credential freshness"
        )

    def test_to_dict_masks_credentials_by_default(self, test_session):
        target = CloudStorageTarget(
            name="t-mask",
            provider_type="s3",
            credentials="SECRET-CIPHERTEXT",
        )
        test_session.add(target)
        test_session.commit()
        test_session.refresh(target)

        d = target.to_dict()  # default mask_credentials=True
        assert d["credentials"] == {}
        assert "SECRET-CIPHERTEXT" not in str(d)
        # New columns surface in the dict.
        assert d["credential_version"] == 1
        assert d["token_revoked_at"] is None
        assert d["insecure"] is False

    def test_to_dict_unmasked_returns_raw_blob(self, test_session):
        target = CloudStorageTarget(
            name="t-unmask",
            provider_type="s3",
            credentials="SECRET-CIPHERTEXT",
        )
        test_session.add(target)
        test_session.commit()
        test_session.refresh(target)
        d = target.to_dict(mask_credentials=False)
        assert d["credentials"] == "SECRET-CIPHERTEXT"


class TestSyncTargetCredentialVersion:
    def test_credential_version_defaults_to_one(self, test_session):
        target = SyncTarget(
            name="sync-default",
            base_url="https://dispatcharr-b.example",
            credentials="ciphertext-v1",
        )
        test_session.add(target)
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version == 1

    def test_credential_version_bumps_on_credentials_write(self, test_session):
        target = SyncTarget(
            name="sync-bump",
            base_url="https://dispatcharr-b.example",
            credentials="ciphertext-v1",
        )
        test_session.add(target)
        test_session.commit()

        target.credentials = "ciphertext-v2"
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version == 2

    def test_metadata_only_update_does_not_bump_version(self, test_session):
        target = SyncTarget(
            name="sync-meta",
            base_url="https://dispatcharr-b.example",
            credentials="ciphertext-v1",
        )
        test_session.add(target)
        test_session.commit()

        target.enabled = False
        target.base_url = "https://dispatcharr-c.example"
        test_session.commit()
        test_session.refresh(target)
        assert target.credential_version == 1

    def test_to_dict_masks_credentials_by_default(self, test_session):
        target = SyncTarget(
            name="sync-mask",
            base_url="https://dispatcharr-b.example",
            credentials="SECRET-CIPHERTEXT",
        )
        test_session.add(target)
        test_session.commit()
        test_session.refresh(target)
        d = target.to_dict()
        assert d["credentials"] == {}
        assert "SECRET-CIPHERTEXT" not in str(d)


class TestSyncTargetExposedInOpenAPI:
    """v0.18.1 (bead vigbu, epic i39wu): the SyncTarget CRUD router is now
    deliberately exposed — the v0.18.0 "schema-present / code-absent" invariant
    was intentionally reversed when the live-sync feature shipped its consumers.
    The /api/sync-targets paths must appear in the public OpenAPI surface."""

    def test_sync_target_router_in_openapi_schema(self):
        from main import app

        schema = app.openapi()
        paths = schema.get("paths", {})
        # The CRUD root + item paths are registered.
        assert "/api/sync-targets" in paths
        assert "/api/sync-targets/{target_id}" in paths
