"""Regression coverage for bounded legacy ZIP restore ingestion (9kwzp.3)."""

import io
import json
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from starlette.datastructures import UploadFile

from routers import backup as backup_mod


def _legacy_zip(*, journal: bytes = b"SQLite format 3\x00") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "ecm_backup.json",
            json.dumps({"version": "1.0", "files": ["journal.db"]}),
        )
        zf.writestr("journal.db", journal)
    return buffer.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", [False, True])
async def test_legacy_restore_streams_through_upload_cap_and_cleans_partial_file(
    tmp_path, initial
):
    payload = _legacy_zip(journal=b"SQLite format 3\x00" + b"x" * 512)
    settings = type("Settings", (), {"is_configured": lambda self: False})()

    with (
        patch.object(backup_mod, "_RESTORE_MAX_UPLOAD_BYTES", 32),
        patch.object(backup_mod, "_DBAS_RESTORE_TMP_DIR", tmp_path),
        patch.object(backup_mod, "get_settings", return_value=settings),
        patch.object(backup_mod, "_guard_initial_restore", new=AsyncMock()),
        patch.object(backup_mod, "_restore_from_zip", return_value=[]),
    ):
        upload = UploadFile(io.BytesIO(payload), filename="backup.zip")
        with pytest.raises(backup_mod.HTTPException) as exc:
            if initial:
                await backup_mod.restore_backup_initial(
                    request=object(), file=upload, session=object()
                )
            else:
                await backup_mod.restore_backup(file=upload, _admin=None)

    assert exc.value.status_code == 413
    assert exc.value.detail == "Uploaded artifact is too large"
    assert list(tmp_path.iterdir()) == []


def test_legacy_validator_rejects_high_ratio_member_before_any_read(tmp_path):
    archive = tmp_path / "bomb.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr("journal.db", b"0" * (2 * 1024 * 1024))

    with zipfile.ZipFile(archive) as zf:
        with patch.object(zf, "read", side_effect=AssertionError("member decompressed")):
            with pytest.raises(backup_mod.HTTPException) as exc:
                backup_mod._validate_backup_zip(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Backup archive rejected"


@pytest.mark.parametrize("member_name", ["journal.db", "m3u_uploads/playlist.m3u"])
def test_legacy_validator_accepts_ecm_shaped_high_ratio_member(tmp_path, member_name):
    """Only legacy SQLite/M3U data may exceed the DBAS 100x ratio cap."""
    archive = tmp_path / "legacy-high-ratio.zip"
    content = b"SQLite format 3\x00" + b"abcde12345" * (1024 * 1024 // 10)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr(member_name, content)

    with zipfile.ZipFile(archive) as zf:
        info = zf.getinfo(member_name)
        assert 292 < info.file_size / info.compress_size < 650
        manifest = backup_mod._validate_backup_zip(zf)

    assert manifest["version"] == "1.0"


def test_legacy_validator_keeps_100x_ratio_for_other_members_before_any_read(tmp_path):
    """The legacy allowance is not a blanket relaxation for arbitrary members."""
    archive = tmp_path / "non-legacy-data-bomb.zip"
    content = b"abcde12345" * (1024 * 1024 // 10)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("tls/cert.pem", content)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(zf, "read", side_effect=AssertionError("member decompressed")):
            with pytest.raises(backup_mod.HTTPException) as exc:
                backup_mod._validate_backup_zip(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Backup archive rejected"


@pytest.mark.parametrize(
    ("members", "limit_name", "limit"),
    [
        ([("one", b"x"), ("two", b"x")], "_ARTIFACT_MAX_ENTRIES", 1),
        ([("oversized", b"x" * 65)], "_ARTIFACT_MAX_MEMBER_UNCOMPRESSED", 64),
        (
            [("first", b"x" * 48), ("second", b"x" * 48)],
            "_ARTIFACT_MAX_TOTAL_UNCOMPRESSED",
            64,
        ),
    ],
)
def test_legacy_validator_enforces_declared_bounds_before_any_read(
    tmp_path, monkeypatch, members, limit_name, limit
):
    """Legacy compatibility does not relax entry or declared-size controls."""
    archive = tmp_path / "bounded.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, content in members:
            zf.writestr(name, content)
    monkeypatch.setattr(backup_mod, limit_name, limit)

    with zipfile.ZipFile(archive) as zf:
        with patch.object(zf, "read", side_effect=AssertionError("member decompressed")):
            with pytest.raises(backup_mod.HTTPException) as exc:
                backup_mod._validate_backup_zip(zf)

    assert exc.value.status_code == 400
    assert exc.value.detail == "Backup archive rejected"


def test_legacy_validator_reads_only_sqlite_header_from_journal(tmp_path):
    archive = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("ecm_backup.json", json.dumps({"version": "1.0"}))
        zf.writestr("journal.db", b"SQLite format 3\x00" + b"x" * 4096)

    with zipfile.ZipFile(archive) as zf:
        original_open = zf.open
        journal_reads: list[int] = []

        def tracked_open(name, *args, **kwargs):
            member = original_open(name, *args, **kwargs)
            if name != "journal.db":
                return member

            class TrackedMember:
                def __enter__(self):
                    member.__enter__()
                    return self

                def __exit__(self, *exc):
                    return member.__exit__(*exc)

                def read(self, size=-1):
                    journal_reads.append(size)
                    return member.read(size)

            return TrackedMember()

        with patch.object(zf, "open", side_effect=tracked_open):
            manifest = backup_mod._validate_backup_zip(zf)

    assert manifest["version"] == "1.0"
    assert journal_reads == [16]
