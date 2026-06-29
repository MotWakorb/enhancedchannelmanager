"""
Unit tests for the DBAS backup artifact builder (bead 0i2vt.7).

Covers the NEW v0.18.0 artifact format produced by
``routers.backup.build_backup_artifact``:

  - manifest round-trips schema_version (integer, present, == expected)
    and keeps it DISTINCT from the human-readable app_version string.
  - SHA-256 sidecar validates against the artifact; a flipped byte fails.
  - REDACTION proof: known M3U / SMTP / cloud secrets never appear in ANY
    byte of the ZIP (manifest, per-category YAML, journal.db, binary subtree).
  - STREAMING: the builder writes to a temp FILE path (not io.BytesIO) and
    hashes by streaming the finished file.
  - binary subtree present (metadata.json + url-mappings.json + logo dir)
    when logos exist.
  - free-disk pre-check fails clearly and leaves no partial artifact.

These tests exercise the builder directly (block-level) rather than through an
HTTP route, mirroring the unit-test style in test_backup.py while targeting the
new sealed-artifact seam that Phase-1 .6/.8 and Phase-2 restore will consume.
"""
import io
import json
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from routers import backup as backup_mod
from routers.backup import (
    APP_VERSION,
    BACKUP_SCHEMA_VERSION,
    build_backup_artifact,
    verify_artifact_sha256,
)

# Distinctive sentinels seeded into source state. If ANY of these strings shows
# up in ANY byte of the produced ZIP, redaction has failed.
SECRET_M3U_PASSWORD = "M3U_SECRET_pw_ZZZ111"
SECRET_SMTP_PASSWORD = "SMTP_SECRET_pw_ZZZ222"
SECRET_CLOUD_TOKEN = "CLOUD_SECRET_tok_ZZZ333"
SECRET_DISPATCHARR_KEY = "DISPATCHARR_SECRET_key_ZZZ444"
SECRET_TELEGRAM_TOKEN = "TELEGRAM_SECRET_tok_ZZZ555"


def _mock_settings_with_secrets():
    """A settings stub whose model_dump carries every credential sentinel under
    the field names in ``_SETTINGS_CREDENTIAL_FIELDS``."""
    s = MagicMock()
    s.model_dump.return_value = {
        "url": "http://dispatcharr:9191",
        "username": "admin",
        "password": SECRET_M3U_PASSWORD,
        "dispatcharr_api_key": SECRET_DISPATCHARR_KEY,
        "api_key": SECRET_DISPATCHARR_KEY,
        "smtp_password": SECRET_SMTP_PASSWORD,
        "telegram_bot_token": SECRET_TELEGRAM_TOKEN,
        "mcp_api_key": SECRET_CLOUD_TOKEN,
    }
    return s


def _mock_engine():
    eng = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = (0, 0, 0)
    eng.connect.return_value.__enter__ = MagicMock(return_value=conn)
    eng.connect.return_value.__exit__ = MagicMock(return_value=False)
    return eng


def _seed_journal_db(path):
    """Write a real SQLite journal.db carrying an alert_methods.config row whose
    SMTP password is a known secret sentinel (the scrub path must redact it)."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE alert_methods (id INTEGER PRIMARY KEY, config TEXT)")
        conn.execute(
            "INSERT INTO alert_methods (id, config) VALUES (?, ?)",
            (1, json.dumps({"host": "smtp.example.com", "password": SECRET_SMTP_PASSWORD})),
        )
        conn.commit()
    finally:
        conn.close()


def _patched_build(tmp_path, *, with_logos=False, dest_dir=None):
    """Run build_backup_artifact with CONFIG_DIR/JOURNAL_DB_FILE pointed at
    tmp_path, settings carrying secrets, and Dispatcharr returning M3U accounts
    that embed an M3U password sentinel."""
    config_dir = tmp_path
    journal = config_dir / "journal.db"
    _seed_journal_db(journal)

    settings_file = config_dir / "settings.json"
    settings_file.write_text("{}")

    if with_logos:
        logos = config_dir / "uploads" / "logos"
        logos.mkdir(parents=True)
        (logos / "abc.png").write_bytes(b"PNG_ABC_DATA")
        (logos / "def.jpg").write_bytes(b"JPG_DEF_DATA")

    # Dispatcharr M3U accounts carry a password sentinel in their raw payload —
    # the gather path must NOT leak it (it flows through the YAML export, which
    # is the shared redaction pipeline).
    mock_client = MagicMock()
    mock_client.get_m3u_accounts = AsyncMock(
        return_value=[{"id": 1, "name": "prov", "password": SECRET_M3U_PASSWORD,
                       "server_url": "http://prov/get.php"}]
    )
    mock_client.get_epg_sources = AsyncMock(return_value=[])
    mock_client.get_channel_groups = AsyncMock(return_value=[])
    mock_client.get_channel_profiles = AsyncMock(return_value=[])
    mock_client.get_stream_profiles = AsyncMock(return_value=[])

    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
         patch.object(backup_mod, "CONFIG_FILE", settings_file), \
         patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
         patch.object(backup_mod, "get_engine", return_value=_mock_engine()), \
         patch.object(backup_mod, "get_settings", return_value=_mock_settings_with_secrets()), \
         patch.object(backup_mod, "get_session", return_value=session), \
         patch.object(backup_mod, "get_client", return_value=mock_client):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            build_backup_artifact(dest_dir=dest_dir)
        )


class TestSchemaVersionManifest:
    def test_schema_version_is_a_positive_integer_constant(self):
        assert isinstance(BACKUP_SCHEMA_VERSION, int)
        assert BACKUP_SCHEMA_VERSION >= 1

    def test_manifest_round_trips_schema_version(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert "schema_version" in manifest
        assert isinstance(manifest["schema_version"], int)
        assert manifest["schema_version"] == BACKUP_SCHEMA_VERSION
        assert art.schema_version == BACKUP_SCHEMA_VERSION

    def test_schema_version_distinct_from_app_version_string(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        # app_version is the human string; schema_version is the int gate.
        assert manifest["app_version"] == APP_VERSION
        assert isinstance(manifest["app_version"], str)
        assert manifest["schema_version"] != manifest["app_version"]

    def test_manifest_carries_per_file_sha256(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        assert isinstance(manifest["files"], list)
        assert manifest["files"], "expected at least one member hash"
        for entry in manifest["files"]:
            assert "path" in entry and "sha256" in entry
            assert len(entry["sha256"]) == 64


class TestSha256Sidecar:
    def test_sidecar_written_alongside_zip(self, tmp_path):
        art = _patched_build(tmp_path)
        assert art.sidecar_path.exists()
        assert art.sidecar_path.name == art.zip_path.name + ".sha256"
        # Sidecar is a sibling of the ZIP, not inside it.
        assert art.sidecar_path.parent == art.zip_path.parent

    def test_sidecar_validates_against_artifact(self, tmp_path):
        art = _patched_build(tmp_path)
        assert verify_artifact_sha256(art.zip_path, art.sidecar_path) is True

    def test_flipping_a_byte_fails_validation(self, tmp_path):
        art = _patched_build(tmp_path)
        data = bytearray(art.zip_path.read_bytes())
        # Flip a byte well inside the file (past the local file header).
        data[len(data) // 2] ^= 0xFF
        art.zip_path.write_bytes(bytes(data))
        assert verify_artifact_sha256(art.zip_path, art.sidecar_path) is False

    def test_sidecar_sha_matches_streamed_recompute(self, tmp_path):
        import hashlib
        art = _patched_build(tmp_path)
        expected = hashlib.sha256(art.zip_path.read_bytes()).hexdigest()
        assert art.sha256 == expected
        assert art.sidecar_path.read_text().split()[0] == expected


class TestRedaction:
    def test_no_secret_appears_in_any_byte(self, tmp_path):
        art = _patched_build(tmp_path, with_logos=True)
        raw = art.zip_path.read_bytes()
        for secret in (
            SECRET_M3U_PASSWORD,
            SECRET_SMTP_PASSWORD,
            SECRET_CLOUD_TOKEN,
            SECRET_DISPATCHARR_KEY,
            SECRET_TELEGRAM_TOKEN,
        ):
            assert secret.encode("utf-8") not in raw, (
                "secret %s leaked into raw ZIP bytes" % secret
            )

    def test_no_secret_in_decompressed_members(self, tmp_path):
        """Belt-and-suspenders: DEFLATE could in theory hide a substring from a
        raw-bytes scan, so also scan every decompressed member."""
        art = _patched_build(tmp_path, with_logos=True)
        with zipfile.ZipFile(art.zip_path) as zf:
            for name in zf.namelist():
                member = zf.read(name)
                for secret in (
                    SECRET_M3U_PASSWORD,
                    SECRET_SMTP_PASSWORD,
                    SECRET_CLOUD_TOKEN,
                    SECRET_DISPATCHARR_KEY,
                    SECRET_TELEGRAM_TOKEN,
                ):
                    assert secret.encode("utf-8") not in member, (
                        "secret %s leaked into member %s" % (secret, name)
                    )

    def test_redacted_sentinel_present_in_settings_category(self, tmp_path):
        """Proves redaction actually fired (the values were replaced, not just
        absent because the field was dropped)."""
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            settings_yaml = zf.read("categories/settings.yaml").decode("utf-8")
        assert backup_mod.REDACTED in settings_yaml

    def test_journal_db_alert_method_password_scrubbed(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            db_bytes = zf.read("journal.db")
        assert SECRET_SMTP_PASSWORD.encode("utf-8") not in db_bytes
        assert db_bytes.startswith(b"SQLite format 3")


class TestStreaming:
    def test_builds_to_temp_file_not_bytesio(self, tmp_path):
        """The builder must write to a real file path, never io.BytesIO. We
        assert no ZipFile is constructed over a BytesIO during the build."""
        real_zipfile = zipfile.ZipFile

        def _guard(file, *a, **k):
            # Writing path: the first positional is the file object/handle.
            if k.get("mode", a[0] if a else "r") == "w" or (a and a[0] == "w"):
                assert not isinstance(file, io.BytesIO), (
                    "builder used io.BytesIO for the artifact — must stream to a file"
                )
            return real_zipfile(file, *a, **k)

        with patch.object(zipfile, "ZipFile", side_effect=_guard):
            art = _patched_build(tmp_path)
        assert art.zip_path.exists()

    def test_artifact_written_to_requested_dest_dir(self, tmp_path):
        dest = tmp_path / "out"
        art = _patched_build(tmp_path, dest_dir=dest)
        assert art.zip_path.parent == dest
        assert art.zip_path.exists()


class TestBinarySubtree:
    def test_binary_subtree_present_when_logos_exist(self, tmp_path):
        art = _patched_build(tmp_path, with_logos=True)
        with zipfile.ZipFile(art.zip_path) as zf:
            names = zf.namelist()
        assert "binary/metadata.json" in names
        assert "binary/url-mappings.json" in names
        assert "binary/logos/abc.png" in names
        assert "binary/logos/def.jpg" in names

    def test_metadata_inventories_logos(self, tmp_path):
        art = _patched_build(tmp_path, with_logos=True)
        with zipfile.ZipFile(art.zip_path) as zf:
            meta = json.loads(zf.read("binary/metadata.json"))
            mappings = json.loads(zf.read("binary/url-mappings.json"))
        assert meta["logo_count"] == 2
        filenames = {e["filename"] for e in meta["logos"]}
        assert filenames == {"abc.png", "def.jpg"}
        assert set(mappings.keys()) == {"abc.png", "def.jpg"}

    def test_binary_subtree_present_but_empty_without_logos(self, tmp_path):
        art = _patched_build(tmp_path, with_logos=False)
        with zipfile.ZipFile(art.zip_path) as zf:
            names = zf.namelist()
            meta = json.loads(zf.read("binary/metadata.json"))
        assert "binary/metadata.json" in names
        assert "binary/url-mappings.json" in names
        assert meta["logo_count"] == 0


class TestCategories:
    def test_per_category_yaml_files_present(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            names = zf.namelist()
        assert "categories/settings.yaml" in names
        # Every restorable section gets its own category file.
        for key in backup_mod.RESTORABLE_SECTIONS:
            assert "categories/%s.yaml" % key in names

    def test_category_yaml_is_valid_yaml(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/settings.yaml"))
        assert isinstance(parsed, dict)
        assert "ecm_export" in parsed


class TestFreeDiskGuard:
    def test_insufficient_disk_fails_clearly_no_partial(self, tmp_path):
        """When the partition lacks free space, the build raises and leaves no
        partial ZIP or sidecar behind."""
        import collections
        config_dir = tmp_path
        journal = config_dir / "journal.db"
        _seed_journal_db(journal)
        (config_dir / "settings.json").write_text("{}")

        fake_usage = collections.namedtuple("u", "total used free")(0, 0, 1)

        mock_client = MagicMock()
        mock_client.get_m3u_accounts = AsyncMock(return_value=[])
        mock_client.get_epg_sources = AsyncMock(return_value=[])
        mock_client.get_channel_groups = AsyncMock(return_value=[])
        mock_client.get_channel_profiles = AsyncMock(return_value=[])
        mock_client.get_stream_profiles = AsyncMock(return_value=[])
        session = MagicMock()
        session.query.return_value.all.return_value = []

        import asyncio
        with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
             patch.object(backup_mod, "CONFIG_FILE", config_dir / "settings.json"), \
             patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
             patch.object(backup_mod, "get_engine", return_value=_mock_engine()), \
             patch.object(backup_mod, "get_settings", return_value=_mock_settings_with_secrets()), \
             patch.object(backup_mod, "get_session", return_value=session), \
             patch.object(backup_mod, "get_client", return_value=mock_client), \
             patch.object(backup_mod.shutil, "disk_usage", return_value=fake_usage):
            with pytest.raises(RuntimeError, match="Insufficient free disk"):
                asyncio.get_event_loop().run_until_complete(build_backup_artifact())

        # No partial artifacts left in the dest dir. The builder now owns the
        # canonical ecm-backup-<ts>.zip name directly (bead e0r3h), so a failed
        # build must leave no ecm-backup-* (and no legacy ecm-artifact-*) residue.
        leftovers = (
            list(config_dir.glob("ecm-backup-*.zip"))
            + list(config_dir.glob("ecm-backup-*.zip.sha256"))
            + list(config_dir.glob("ecm-artifact-*"))
        )
        assert leftovers == [], "partial artifact left behind: %s" % leftovers


# A stream URL embeds IPTV provider credentials; it must NEVER appear in the
# artifact (7i8rf). Distinctive sentinel so a leak is unambiguous in a byte scan.
SECRET_STREAM_URL = "http://prov/user/STREAM_SECRET_url_ZZZ666/cnn.ts"


def _build_with_channels_and_users(tmp_path, *, dest_dir=None):
    """Build a real artifact whose Dispatcharr client returns channels (with
    embedded stream IDs), the matching stream records (carrying a SECRET URL the
    producer must drop), and a dispatcharr user. Used to prove the 7i8rf
    producer-side round-trip + the stream-URL redaction contract."""
    config_dir = tmp_path
    journal = config_dir / "journal.db"
    _seed_journal_db(journal)
    (config_dir / "settings.json").write_text("{}")

    client = MagicMock()
    client.get_m3u_accounts = AsyncMock(return_value=[])
    client.get_epg_sources = AsyncMock(return_value=[])
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_channel_profiles = AsyncMock(return_value=[])
    client.get_stream_profiles = AsyncMock(return_value=[])
    # A Dispatcharr channel's ``streams`` field is a list of stream IDs.
    client.get_channels = AsyncMock(
        return_value={
            "count": 1,
            "next": None,
            "results": [
                {"id": 5, "name": "CNN", "channel_number": 1, "streams": [11, 12]},
            ],
        }
    )
    # The stream records the producer joins against — each carries a SECRET url
    # (provider creds) that must be dropped, plus the safe match fields.
    client.get_streams = AsyncMock(
        return_value={
            "count": 2,
            "next": None,
            "results": [
                {"id": 11, "name": "CNN HD", "m3u_account": 1, "url": SECRET_STREAM_URL},
                {"id": 12, "name": "CNN SD", "m3u_account": 1, "url": SECRET_STREAM_URL},
            ],
        }
    )
    client.get_users = AsyncMock(
        return_value=[{"id": 7, "username": "alice", "is_superuser": False}]
    )

    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
         patch.object(backup_mod, "CONFIG_FILE", config_dir / "settings.json"), \
         patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
         patch.object(backup_mod, "get_engine", return_value=_mock_engine()), \
         patch.object(backup_mod, "get_settings", return_value=_mock_settings_with_secrets()), \
         patch.object(backup_mod, "get_session", return_value=session), \
         patch.object(backup_mod, "get_client", return_value=client):
        import asyncio
        return asyncio.get_event_loop().run_until_complete(
            build_backup_artifact(dest_dir=dest_dir)
        )


class TestChannelsUsersProducers:
    """7i8rf — the builder must emit the channels + dispatcharr_users categories
    the restore importers consume (was a no-op before this bead)."""

    def test_restorable_sections_include_channels_and_users(self):
        assert "channels" in backup_mod.RESTORABLE_SECTIONS
        assert "dispatcharr_users" in backup_mod.RESTORABLE_SECTIONS
        assert backup_mod.RESTORABLE_SECTIONS["channels"]["dispatcharr"] is True
        assert backup_mod.RESTORABLE_SECTIONS["dispatcharr_users"]["dispatcharr"] is True

    def test_channels_category_emitted_with_embedded_streams(self, tmp_path):
        art = _build_with_channels_and_users(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/channels.yaml"))
        rows = parsed["dispatcharr"]["channels"]
        assert len(rows) == 1
        ch = rows[0]
        assert ch["id"] == 5
        # Embedded streams are enriched to id + safe match fields, in order.
        streams = ch["streams"]
        assert [s["id"] for s in streams] == [11, 12]
        assert streams[0]["name"] == "CNN HD"
        assert streams[0]["m3u_account"] == 1

    def test_users_category_emitted(self, tmp_path):
        art = _build_with_channels_and_users(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/dispatcharr_users.yaml"))
        rows = parsed["dispatcharr"]["dispatcharr_users"]
        assert [u["username"] for u in rows] == ["alice"]

    def test_embedded_stream_url_never_in_artifact(self, tmp_path):
        """The stream URL (provider creds) must not appear in ANY byte of the
        artifact — the producer drops it, never carries it."""
        art = _build_with_channels_and_users(tmp_path)
        raw = art.zip_path.read_bytes()
        assert SECRET_STREAM_URL.encode("utf-8") not in raw
        with zipfile.ZipFile(art.zip_path) as zf:
            for name in zf.namelist():
                assert SECRET_STREAM_URL.encode("utf-8") not in zf.read(name)

    def test_embedded_stream_carries_no_url_key(self, tmp_path):
        art = _build_with_channels_and_users(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/channels.yaml"))
        for s in parsed["dispatcharr"]["channels"][0]["streams"]:
            assert "url" not in s
            assert "custom_url" not in s
            assert "stream_hash" not in s


class TestCanonicalArtifactName:
    """e0r3h — the builder owns the canonical timestamped name directly."""

    def test_zip_name_matches_retention_allowlist(self, tmp_path):
        art = _patched_build(tmp_path)
        assert backup_mod._BACKUP_ZIP_FILENAME_RE.fullmatch(art.zip_path.name), (
            "builder zip name %r does not match retention allowlist" % art.zip_path.name
        )

    def test_no_legacy_artifact_name(self, tmp_path):
        art = _patched_build(tmp_path)
        assert not art.zip_path.name.startswith("ecm-artifact-")
        assert art.zip_path.name.startswith("ecm-backup-")

    def test_sidecar_name_tracks_canonical_zip(self, tmp_path):
        art = _patched_build(tmp_path)
        assert art.sidecar_path.name == art.zip_path.name + ".sha256"
        assert art.zip_path.name in art.sidecar_path.read_text()
