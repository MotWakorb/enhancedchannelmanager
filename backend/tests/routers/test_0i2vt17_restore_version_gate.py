"""
Tests for the restore-ingest schema_version gate (bead 0i2vt.17).

Covers the NEW v0.18.0 artifact restore-ingest validation chokepoint:
``routers.backup.validate_restore_schema_version`` (the reusable version
comparator) and ``routers.backup.validate_artifact_manifest`` (the
new-format ZIP validator that runs the version gate + per-member SHA-256
integrity BEFORE any restore mutation).

Security contract (ADR-008 D1 + S4 — no schema-internals leakage):

  - schema_version == BACKUP_SCHEMA_VERSION  -> accepted.
  - schema_version <  BACKUP_SCHEMA_VERSION  -> accepted (older artifact).
  - schema_version >  BACKUP_SCHEMA_VERSION  -> REFUSED with the user-facing
    message EXACTLY "Unsupported backup version" and NO version numbers /
    schema internals in the user-facing body.
  - missing / malformed schema_version       -> refused safely (same
    user-facing message; detail logged server-side).
  - the ACTUAL version detail (got X, support up to Y) is logged SERVER-SIDE
    only (logger.warning, lazy %%-formatting).
  - validation runs BEFORE any mutation: a refused version performs NO
    restore/import side effect.

NOTE: this bead is ONLY the manifest ``schema_version`` axis. The embedded
journal.db alembic_version is a DISTINCT axis owned by another bead.
"""
import io
import json
import logging
import zipfile

import pytest
from fastapi import HTTPException

from routers import backup as backup_mod
from routers.backup import (
    BACKUP_SCHEMA_VERSION,
    UnsupportedBackupVersionError,
    UNSUPPORTED_BACKUP_VERSION_MESSAGE,
    validate_artifact_manifest,
    validate_restore_schema_version,
)

# The one user-facing string. Asserted verbatim everywhere a refusal surfaces.
USER_FACING = "Unsupported backup version"


# ---------------------------------------------------------------------------
# Helpers — build an in-memory new-format artifact ZIP with a chosen manifest.
# ---------------------------------------------------------------------------


def _member_sha256(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _build_artifact_zip(schema_version, *, omit_schema_version=False,
                        corrupt_member=False, extra_members=None):
    """Return bytes of a new-format artifact ZIP carrying ``manifest.json``.

    ``manifest.json`` is the cleartext header (per-member SHA-256 + schema
    version). One trivial category member is always written so the integrity
    check has something to verify.
    """
    members = {"categories/settings.yaml": b"key: value\n"}
    if extra_members:
        members.update(extra_members)

    file_hashes = {path: _member_sha256(blob) for path, blob in members.items()}
    manifest = {
        "app_version": "0.17.6-test",
        "created_at": "2026-06-18T00:00:00+00:00",
        "redacted": True,
        "files": [{"path": p, "sha256": h} for p, h in sorted(file_hashes.items())],
    }
    if not omit_schema_version:
        manifest["schema_version"] = schema_version

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for path, blob in members.items():
            payload = blob + (b"X" if corrupt_member else b"")
            zf.writestr(path, payload)
    buf.seek(0)
    return buf.getvalue()


def _open(zip_bytes):
    return zipfile.ZipFile(io.BytesIO(zip_bytes), "r")


# ---------------------------------------------------------------------------
# validate_restore_schema_version — the reusable comparator
# ---------------------------------------------------------------------------


class TestVersionComparator:
    def test_equal_version_accepted(self):
        # No raise == accepted.
        validate_restore_schema_version({"schema_version": BACKUP_SCHEMA_VERSION})

    def test_older_version_accepted(self):
        validate_restore_schema_version({"schema_version": BACKUP_SCHEMA_VERSION - 1})

    def test_zero_version_accepted(self):
        # A pre-1 artifact (hypothetical) is still <= current -> accepted.
        validate_restore_schema_version({"schema_version": 0})

    def test_newer_version_refused(self):
        with pytest.raises(UnsupportedBackupVersionError) as exc:
            validate_restore_schema_version(
                {"schema_version": BACKUP_SCHEMA_VERSION + 1}
            )
        # User-facing message is EXACTLY the constant, nothing more.
        assert str(exc.value) == USER_FACING

    def test_newer_version_user_message_has_no_version_numbers(self):
        newer = BACKUP_SCHEMA_VERSION + 7
        with pytest.raises(UnsupportedBackupVersionError) as exc:
            validate_restore_schema_version({"schema_version": newer})
        msg = str(exc.value)
        assert msg == USER_FACING
        # No actual version numbers / schema internals leak into the message.
        assert str(newer) not in msg
        assert str(BACKUP_SCHEMA_VERSION) not in msg
        assert "schema_version" not in msg

    def test_newer_version_logs_detail_server_side(self, caplog):
        newer = BACKUP_SCHEMA_VERSION + 3
        with caplog.at_level(logging.WARNING, logger="routers.backup"):
            with pytest.raises(UnsupportedBackupVersionError):
                validate_restore_schema_version({"schema_version": newer})
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "[BACKUP]" in joined
        # The ACTUAL detail (got X, support up to Y) IS in the server log.
        assert str(newer) in joined
        assert str(BACKUP_SCHEMA_VERSION) in joined

    def test_missing_schema_version_refused(self):
        with pytest.raises(UnsupportedBackupVersionError) as exc:
            validate_restore_schema_version({"app_version": "0.18.0"})
        assert str(exc.value) == USER_FACING

    def test_missing_schema_version_logs_detail(self, caplog):
        with caplog.at_level(logging.WARNING, logger="routers.backup"):
            with pytest.raises(UnsupportedBackupVersionError):
                validate_restore_schema_version({"app_version": "0.18.0"})
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert "[BACKUP]" in joined

    @pytest.mark.parametrize("bad", ["1", 1.5, None, [], {}, "not-a-number"])
    def test_malformed_schema_version_refused(self, bad):
        with pytest.raises(UnsupportedBackupVersionError) as exc:
            validate_restore_schema_version({"schema_version": bad})
        assert str(exc.value) == USER_FACING

    def test_non_dict_manifest_refused(self):
        with pytest.raises(UnsupportedBackupVersionError) as exc:
            validate_restore_schema_version(["not", "a", "dict"])
        assert str(exc.value) == USER_FACING


# ---------------------------------------------------------------------------
# validate_artifact_manifest — the new-format ZIP chokepoint (version + SHA-256)
# ---------------------------------------------------------------------------


class TestArtifactManifestValidator:
    def test_current_version_artifact_accepted(self):
        zb = _build_artifact_zip(BACKUP_SCHEMA_VERSION)
        with _open(zb) as zf:
            manifest = validate_artifact_manifest(zf)
        assert manifest["schema_version"] == BACKUP_SCHEMA_VERSION

    def test_older_version_artifact_accepted(self):
        zb = _build_artifact_zip(BACKUP_SCHEMA_VERSION - 1)
        with _open(zb) as zf:
            manifest = validate_artifact_manifest(zf)
        assert manifest["schema_version"] == BACKUP_SCHEMA_VERSION - 1

    def test_newer_version_artifact_refused_http_400(self):
        zb = _build_artifact_zip(BACKUP_SCHEMA_VERSION + 1)
        with _open(zb) as zf:
            with pytest.raises(HTTPException) as exc:
                validate_artifact_manifest(zf)
        assert exc.value.status_code == 400
        assert exc.value.detail == USER_FACING

    def test_newer_version_http_body_has_no_version_leak(self):
        newer = BACKUP_SCHEMA_VERSION + 9
        zb = _build_artifact_zip(newer)
        with _open(zb) as zf:
            with pytest.raises(HTTPException) as exc:
                validate_artifact_manifest(zf)
        body = exc.value.detail
        assert body == USER_FACING
        assert str(newer) not in body
        assert str(BACKUP_SCHEMA_VERSION) not in body
        assert "schema_version" not in body

    def test_newer_version_logs_detail_server_side(self, caplog):
        newer = BACKUP_SCHEMA_VERSION + 2
        zb = _build_artifact_zip(newer)
        with caplog.at_level(logging.WARNING, logger="routers.backup"):
            with _open(zb) as zf:
                with pytest.raises(HTTPException):
                    validate_artifact_manifest(zf)
        joined = " ".join(r.getMessage() for r in caplog.records)
        assert str(newer) in joined
        assert str(BACKUP_SCHEMA_VERSION) in joined

    def test_missing_manifest_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("categories/settings.yaml", "k: v\n")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(HTTPException) as exc:
                validate_artifact_manifest(zf)
        assert exc.value.status_code == 400

    def test_malformed_manifest_json_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("manifest.json", "{ this is not json")
        buf.seek(0)
        with zipfile.ZipFile(buf, "r") as zf:
            with pytest.raises(HTTPException) as exc:
                validate_artifact_manifest(zf)
        assert exc.value.status_code == 400

    def test_version_gate_runs_before_integrity_check(self, caplog):
        # A NEWER-version artifact that ALSO has a corrupt member must be
        # refused on VERSION (not integrity) — the version gate is first, and a
        # corrupt newer artifact still surfaces the version message, never
        # leaking integrity/schema internals.
        zb = _build_artifact_zip(BACKUP_SCHEMA_VERSION + 1, corrupt_member=True)
        with _open(zb) as zf:
            with pytest.raises(HTTPException) as exc:
                validate_artifact_manifest(zf)
        assert exc.value.detail == USER_FACING

    def test_integrity_failure_refused_when_version_ok(self):
        # Version is fine, but a member's bytes don't match the manifest hash.
        zb = _build_artifact_zip(BACKUP_SCHEMA_VERSION, corrupt_member=True)
        with _open(zb) as zf:
            with pytest.raises(HTTPException) as exc:
                validate_artifact_manifest(zf)
        assert exc.value.status_code == 400
        # Integrity failure is a distinct, non-version message — but still must
        # not leak schema internals (no schema_version numbers).
        assert str(BACKUP_SCHEMA_VERSION) not in exc.value.detail


# ---------------------------------------------------------------------------
# Validation runs BEFORE any mutation — refused version performs NO side effect.
# ---------------------------------------------------------------------------


class TestNoMutationOnRefusal:
    def test_refused_version_does_not_invoke_any_restore_side_effect(self, monkeypatch):
        """A newer-version artifact is refused at validation; nothing that
        mutates state (restore/import) is ever reached."""
        called = {"mutated": False}

        # Sentinel: if any restore-mutation entry point were called, this flips.
        def _boom(*a, **k):
            called["mutated"] = True
            raise AssertionError("restore mutation invoked on a refused artifact")

        # Guard whatever mutation seams exist today on the module.
        for name in ("_restore_from_zip",):
            if hasattr(backup_mod, name):
                monkeypatch.setattr(backup_mod, name, _boom)

        zb = _build_artifact_zip(BACKUP_SCHEMA_VERSION + 1)
        with _open(zb) as zf:
            with pytest.raises(HTTPException):
                validate_artifact_manifest(zf)
        assert called["mutated"] is False
