"""Decompression-bomb guard on the DBAS restore read sites (O8TBV-2 / O8TBV-3).

Security review found that the 2 GiB upload cap bounds only the COMPRESSED bytes:
both ``routers.backup._verify_artifact_member_integrity`` (via
``validate_artifact_manifest``) and ``dbas.restore_artifact.decode_artifact_to_plan``
call ``zf.read(member)`` with no decompressed-size guard. A small ZIP with a
high-ratio member could OOM the single-process container, reachable even on the
admin dry-run path.

The fix is a SINGLE shared guard, ``routers.backup.guard_artifact_against_zip_bomb``,
imported by both read sites and run BEFORE any ``zf.read()``. It enforces the
threat-model D2 caps (docs/security/threat_model_dbas_import.md §3.5 D2):

  * per-entry decompressed:compressed ratio (100x),
  * cumulative declared uncompressed size (1 GiB), and
  * entry count (10,000).

A violation refuses with a GENERIC message (no sizes/internals leaked); the
specifics go to the server log only. These tests prove a crafted high-ratio,
over-cumulative, and over-count archive are each refused BEFORE the bomb member
is read, and that a normal artifact still passes.
"""
from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from routers import backup as backup_mod
from routers.backup import guard_artifact_against_zip_bomb


def _zip_from_infos(entries):
    """Build an in-memory ZIP from (name, uncompressed_size, compressed_size)
    tuples by directly fabricating ZipInfo records — lets us declare a high
    ratio without actually allocating gigabytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, file_size, compress_size in entries:
            info = zipfile.ZipInfo(name)
            # writestr with real bytes for honest sizes; for the bomb cases we
            # overwrite the declared sizes on the ZipInfo afterwards.
            zf.writestr(info, b"x" * 1)
    buf.seek(0)
    return buf


def _open_with_declared_sizes(entries):
    """Return an open ZipFile whose infolist() reports the declared
    (file_size, compress_size) per entry — simulating a bomb's lying header
    WITHOUT decompressing anything. The guard reads infolist() metadata only."""
    # Build a minimal real zip, then monkey-build a fake infolist on a real
    # ZipFile object so guard_artifact_against_zip_bomb sees the declared sizes.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, _fs, _cs in entries:
            zf.writestr(name, b"")
    buf.seek(0)
    zf = zipfile.ZipFile(buf, "r")
    fake_infos = []
    for name, file_size, compress_size in entries:
        info = zipfile.ZipInfo(name)
        info.file_size = file_size
        info.compress_size = compress_size
        fake_infos.append(info)
    zf.infolist = lambda: fake_infos  # type: ignore[assignment]
    return zf


class TestGuardAgainstZipBomb:
    def test_normal_artifact_passes(self):
        # Realistic small entries, sane ratios.
        zf = _open_with_declared_sizes([
            ("manifest.json", 200, 120),
            ("categories/m3u_accounts.yaml", 4096, 800),
            ("binary/logos/a.png", 50_000, 49_000),
        ])
        try:
            guard_artifact_against_zip_bomb(zf)  # must not raise
        finally:
            zf.close()

    def test_high_ratio_member_refused(self):
        # 1 KiB+ compressed expanding 200x -> over the 100x cap.
        zf = _open_with_declared_sizes([
            ("manifest.json", 200, 120),
            ("bomb.bin", 2048 * 200, 2048),
        ])
        try:
            with pytest.raises(HTTPException) as ei:
                guard_artifact_against_zip_bomb(zf)
            assert ei.value.status_code == 400
            # Generic message — no sizes/ratios/member names leaked.
            assert ei.value.detail == "Backup archive rejected"
        finally:
            zf.close()

    def test_tiny_stored_entry_not_falsely_flagged(self):
        # A 12-byte stored file has a degenerate ratio but is below the compressed
        # floor, so it must NOT trip the ratio check.
        zf = _open_with_declared_sizes([
            ("manifest.json", 12, 12),
            ("categories/m3u_accounts.yaml", 4096, 4096),
        ])
        try:
            guard_artifact_against_zip_bomb(zf)  # must not raise
        finally:
            zf.close()

    def test_over_cumulative_refused(self):
        # Two entries each under the ratio cap but together over the 1 GiB
        # cumulative cap.
        half_plus = (backup_mod._ARTIFACT_MAX_TOTAL_UNCOMPRESSED // 2) + 1024
        zf = _open_with_declared_sizes([
            ("a.bin", half_plus, half_plus),
            ("b.bin", half_plus, half_plus),
        ])
        try:
            with pytest.raises(HTTPException) as ei:
                guard_artifact_against_zip_bomb(zf)
            assert ei.value.status_code == 400
            assert ei.value.detail == "Backup archive rejected"
        finally:
            zf.close()

    def test_over_entry_count_refused(self):
        entries = [
            (f"f{i}.txt", 10, 10)
            for i in range(backup_mod._ARTIFACT_MAX_ENTRIES + 1)
        ]
        zf = _open_with_declared_sizes(entries)
        try:
            with pytest.raises(HTTPException) as ei:
                guard_artifact_against_zip_bomb(zf)
            assert ei.value.status_code == 400
            assert ei.value.detail == "Backup archive rejected"
        finally:
            zf.close()

    def test_oversized_logo_refused_from_metadata(self):
        zf = _open_with_declared_sizes([
            ("manifest.json", 200, 120),
            ("binary/logos/oversized.png", backup_mod.MAX_LOGO_BYTES + 1, 49_000_000),
        ])
        try:
            with pytest.raises(HTTPException) as ei:
                guard_artifact_against_zip_bomb(zf)
            assert ei.value.status_code == 400
            assert ei.value.detail == "Backup archive rejected"
        finally:
            zf.close()


class TestGuardWiredIntoReadSites:
    """The guard must run at BOTH read sites before any zf.read()."""

    def _bomb_artifact_bytes(self):
        """A real ZIP whose declared header would expand hugely — but we assert
        the guard refuses it BEFORE the member is read, so we never need to
        allocate the expanded bytes. We achieve a true high ratio with a highly
        compressible member."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", b'{"schema_version": 1, "files": []}')
            # 2 MiB of zeros compresses to a few KiB -> ratio well over 100x.
            zf.writestr("bomb.bin", b"\x00" * (2 * 1024 * 1024))
        buf.seek(0)
        return buf.getvalue()

    def test_validate_manifest_refuses_bomb_before_read(self):
        from routers.backup import validate_artifact_manifest

        zf = zipfile.ZipFile(io.BytesIO(self._bomb_artifact_bytes()), "r")
        # Spy on zf.read so we can prove the bomb member is never read.
        read_targets = []
        orig_read = zf.read

        def _tracked_read(name, *a, **kw):
            read_targets.append(name.filename if hasattr(name, "filename") else name)
            return orig_read(name, *a, **kw)

        zf.read = _tracked_read  # type: ignore[assignment]
        try:
            with pytest.raises(HTTPException) as ei:
                validate_artifact_manifest(zf)
            assert ei.value.status_code == 400
            assert "bomb.bin" not in read_targets
        finally:
            zf.close()

    def test_decode_refuses_bomb_before_read(self):
        from dbas.restore_artifact import decode_artifact_to_plan

        zf = zipfile.ZipFile(io.BytesIO(self._bomb_artifact_bytes()), "r")
        read_targets = []
        orig_read = zf.read

        def _tracked_read(name, *a, **kw):
            read_targets.append(name.filename if hasattr(name, "filename") else name)
            return orig_read(name, *a, **kw)

        zf.read = _tracked_read  # type: ignore[assignment]
        try:
            with pytest.raises(HTTPException) as ei:
                decode_artifact_to_plan(zf)
            assert ei.value.status_code == 400
            assert "bomb.bin" not in read_targets
        finally:
            zf.close()


class TestManifestIntegrityMemoryShape:
    def test_integrity_hashes_logo_without_reading_it_whole(self):
        import hashlib
        import json

        from routers.backup import validate_artifact_manifest

        logo = b"\x89PNG\r\n\x1a\n" + (b"x" * 128_000)
        manifest = {
            "schema_version": 1,
            "files": [{
                "path": "binary/logos/a.png",
                "sha256": hashlib.sha256(logo).hexdigest(),
            }],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as out:
            out.writestr("manifest.json", json.dumps(manifest))
            out.writestr("binary/logos/a.png", logo)
        buf.seek(0)

        with zipfile.ZipFile(buf, "r") as zf:
            original_read = zf.read

            def tracked_read(member, *args, **kwargs):
                name = member.filename if isinstance(member, zipfile.ZipInfo) else member
                assert name != "binary/logos/a.png"
                return original_read(member, *args, **kwargs)

            zf.read = tracked_read
            validate_artifact_manifest(zf)
