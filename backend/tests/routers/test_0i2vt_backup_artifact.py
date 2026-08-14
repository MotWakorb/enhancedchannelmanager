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
import base64
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
# The other two thirds of the 9ej7f read-redaction partition. Added by bead
# …-9kwzp.9: they were absent from ``_SETTINGS_CREDENTIAL_FIELDS`` and therefore
# rode into the artifact in clear, readable by the MCP service principal that
# GET /api/settings refuses them to. Seeded here so the whole-archive byte scans
# below cover the partition, not just the bot token that happened to be listed.
SECRET_DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1/DISCORD_SECRET_ZZZ888"
SECRET_TELEGRAM_CHAT_ID = "TELEGRAM_SECRET_chat_ZZZ999"
# A Dispatcharr CORE SETTING whose key matches the importer-side dangerous-key
# markers (``api_key``). The core_settings producer (lc6zu) must redact its
# VALUE before any byte enters the archive.
SECRET_CORE_SETTING = "CORESETTING_SECRET_val_ZZZ777"

# A real 1x1 PNG. The logo-byte tests archive it and the restore-side validator
# checks magic bytes, so the fixture has to be a genuine image, not a marker.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

# All secret sentinels a produced artifact must never carry, in one tuple so the
# redaction byte-scans cannot silently miss a newly seeded secret.
ALL_SECRET_SENTINELS = (
    SECRET_M3U_PASSWORD,
    SECRET_SMTP_PASSWORD,
    SECRET_CLOUD_TOKEN,
    SECRET_DISPATCHARR_KEY,
    SECRET_TELEGRAM_TOKEN,
    SECRET_DISCORD_WEBHOOK,
    SECRET_TELEGRAM_CHAT_ID,
    SECRET_CORE_SETTING,
)


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
        "discord_webhook_url": SECRET_DISCORD_WEBHOOK,
        "telegram_chat_id": SECRET_TELEGRAM_CHAT_ID,
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


def _patched_build(
    tmp_path, *, with_logos=False, dest_dir=None, client_overrides=None,
    get_client_override=None, source_logos=None, logo_images=None, capture=None,
):
    """Run build_backup_artifact with CONFIG_DIR/JOURNAL_DB_FILE pointed at
    tmp_path, settings carrying secrets, and Dispatcharr returning M3U accounts
    that embed an M3U password sentinel.

    ``client_overrides`` maps a Dispatcharr client method name -> an Exception
    instance to raise instead of returning the default fixture data (zt3kf —
    used to prove degraded-category isolation: a failing category must not
    blast-radius the rest of the gather).

    ``get_client_override`` — a dict of kwargs (``return_value`` or
    ``side_effect``) for patching ``get_client`` ITSELF, instead of returning
    the default fully-mocked client. Used to reproduce total client
    unavailability (PR #770 review) — ``get_client()`` returning falsy or
    raising BEFORE any per-key fetch is attempted — which is a DIFFERENT
    failure shape than a single method on an otherwise-working client
    raising (``client_overrides`` above).

    ``source_logos`` replaces the SOURCE Dispatcharr logo listing (bead xb58a),
    and ``logo_images`` maps a source logo id to what ``fetch_logo_image``
    should do for it: ``bytes`` to return, an ``Exception`` instance to raise,
    or ``None`` for an upstream that returned an error status. A logo id absent
    from the map fetches as ``None``.

    ``capture`` is an optional dict the helper fills with ``{"client": ...}`` so
    a test can assert on the calls the builder did (or did NOT) make.
    """
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
    # channels / dispatcharr_users categories (7i8rf) — configured with clean
    # empty-page responses so the default build is genuinely clean (no
    # category degraded); zt3kf's degraded-category tests override exactly one
    # of these per test to prove isolation.
    mock_client.get_channels = AsyncMock(return_value={"count": 0, "next": None, "results": []})
    mock_client.get_streams = AsyncMock(return_value={"count": 0, "next": None, "results": []})
    mock_client.get_users = AsyncMock(return_value=[])
    # lc6zu — the settings/agents producer set. Core settings come back in the
    # list-of-{key,value} shape (the client returns the raw payload; the
    # producer normalizes). One key is dangerous-marked (``api_key``) and its
    # value must be redacted; one is a comskip key the producer must split into
    # the comskip section.
    mock_client.get_user_agents = AsyncMock(
        return_value=[{"id": 31, "name": "ECM UA", "user_agent": "ECM/1.0"}]
    )
    mock_client.get_dvr_rules = AsyncMock(
        return_value=[{"id": 41, "name": "Record CNN", "channel": 5}]
    )
    mock_client.get_core_settings = AsyncMock(
        return_value=[
            {"id": 1, "key": "default_user_agent", "value": "ECM/1.0"},
            {"id": 2, "key": "provider_api_key", "value": SECRET_CORE_SETTING},
            {"id": 3, "key": "comskip_ini", "value": "[main]"},
        ]
    )
    # PR #743 review item 1 (cm9bi): the SOURCE Dispatcharr logos list. The
    # producer joins each archived logo FILE to its source logo record by URL
    # basename so the artifact carries the stable source logo id the restore
    # importer's affected-channel lookup keys on. abc.png matches source logo
    # id 21; def.jpg deliberately has no source record (no id in metadata).
    mock_client.get_all_logos_paginated = AsyncMock(
        return_value=source_logos if source_logos is not None else [
            {"id": 21, "name": "ABC Logo", "url": "http://dispatcharr/media/logos/abc.png"},
        ]
    )

    # xb58a: the logo BYTE fetch. Dispatcharr is the source of truth for logo
    # images, so the builder asks it for the bytes of every Dispatcharr-hosted
    # logo. Default: every fetch comes back empty, which is the pre-fix shape.
    images = logo_images or {}

    async def _fetch_logo_image(logo_id):
        outcome = images.get(logo_id)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    mock_client.fetch_logo_image = AsyncMock(side_effect=_fetch_logo_image)

    for method_name, exc in (client_overrides or {}).items():
        getattr(mock_client, method_name).side_effect = exc
    if capture is not None:
        capture["client"] = mock_client

    session = MagicMock()
    session.query.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.all.return_value = []
    session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = []
    session.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    get_client_kwargs = get_client_override or {"return_value": mock_client}

    with patch.object(backup_mod, "CONFIG_DIR", config_dir), \
         patch.object(backup_mod, "CONFIG_FILE", settings_file), \
         patch.object(backup_mod, "JOURNAL_DB_FILE", journal), \
         patch.object(backup_mod, "get_engine", return_value=_mock_engine()), \
         patch.object(backup_mod, "get_settings", return_value=_mock_settings_with_secrets()), \
         patch.object(backup_mod, "get_session", return_value=session), \
         patch.object(backup_mod, "get_client", **get_client_kwargs):
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
        for secret in ALL_SECRET_SENTINELS:
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
                for secret in ALL_SECRET_SENTINELS:
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

    def test_metadata_carries_source_logo_id_correlation(self, tmp_path):
        """PR #743 review item 1 (cm9bi): the artifact must preserve the SOURCE
        Dispatcharr logo id per archived file (joined by URL basename) so the
        restore importer's affected-channel drill-down works on REAL backups —
        without it the decoder emits id-less logo rows and _affected_channels()
        always returns []."""
        art = _patched_build(tmp_path, with_logos=True)
        with zipfile.ZipFile(art.zip_path) as zf:
            meta = json.loads(zf.read("binary/metadata.json"))
        by_filename = {e["filename"]: e for e in meta["logos"]}
        # abc.png matched source logo 21 — id + display name carried.
        assert by_filename["abc.png"]["id"] == 21
        assert by_filename["abc.png"]["name"] == "ABC Logo"
        # def.jpg has no source logo record — no fabricated id.
        assert "id" not in by_filename["def.jpg"]

    def test_binary_subtree_present_but_empty_without_logos(self, tmp_path):
        art = _patched_build(tmp_path, with_logos=False)
        with zipfile.ZipFile(art.zip_path) as zf:
            names = zf.namelist()
            meta = json.loads(zf.read("binary/metadata.json"))
        assert "binary/metadata.json" in names
        assert "binary/url-mappings.json" in names
        assert meta["logo_count"] == 0


class TestDispatcharrHostedLogoBytes:
    """bead enhancedchannelmanager-xb58a: the backup archives the BYTES of every
    Dispatcharr-hosted logo, fetched from Dispatcharr at gather time.

    Dispatcharr is ECM's source of truth for logos, so a logo uploaded through
    ECM's own Logo Manager lands in DISPATCHARR's ``/data/logos/`` and never in
    the ``/config/uploads/logos/`` the builder used to be the only thing the
    builder collected. Drill run 2026-08-04-run2 produced
    ``{"logo_count": 0, "logos": []}`` for an instance that had an uploaded logo
    assigned to a channel, and that logo could not be restored on ANY artifact
    variant. The same drill proved the bytes were reachable the whole time: a
    plain authenticated GET returned a byte-identical copy.
    """

    HOSTED = {"id": 13, "name": "Drill Uploaded Logo", "url": "/data/logos/drill-logo.png"}
    REMOTE = {"id": 21, "name": "ABC Logo", "url": "https://cdn.example.com/abc.png"}

    def test_dispatcharr_hosted_logo_is_archived_with_its_bytes(self, tmp_path):
        art = _patched_build(
            tmp_path, source_logos=[self.HOSTED], logo_images={13: PNG_1X1},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            assert "binary/logos/drill-logo.png" in zf.namelist()
            assert zf.read("binary/logos/drill-logo.png") == PNG_1X1
            meta = json.loads(zf.read("binary/metadata.json"))
        assert meta["logo_count"] == 1
        entry = meta["logos"][0]
        # The FILENAME key is what makes the record restorable: the restore-side
        # validator rejects a logo record that has none, which is exactly how
        # the drill's uploaded logo failed.
        assert entry["filename"] == "drill-logo.png"
        assert entry["size_bytes"] == len(PNG_1X1)
        assert entry["id"] == 13
        assert entry["name"] == "Drill Uploaded Logo"

    def test_remote_cdn_logo_is_not_archived_as_bytes(self, tmp_path):
        """A CDN logo round-trips from its archived URL; archiving bytes for it
        would be waste (10 of 10 verified byte-identical through the URL path)."""
        capture = {}
        art = _patched_build(
            tmp_path,
            source_logos=[self.REMOTE],
            logo_images={21: PNG_1X1},
            capture=capture,
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            logo_members = [n for n in zf.namelist() if n.startswith("binary/logos/")]
            meta = json.loads(zf.read("binary/metadata.json"))
        assert logo_members == []
        assert meta["logo_count"] == 0
        # Not merely unarchived: never even fetched.
        capture["client"].fetch_logo_image.assert_not_awaited()

    def test_mixed_set_archives_only_the_hosted_logo(self, tmp_path):
        art = _patched_build(
            tmp_path,
            source_logos=[self.REMOTE, self.HOSTED],
            logo_images={13: PNG_1X1, 21: PNG_1X1},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            logo_members = [n for n in zf.namelist() if n.startswith("binary/logos/")]
            meta = json.loads(zf.read("binary/metadata.json"))
        assert logo_members == ["binary/logos/drill-logo.png"]
        assert [e["id"] for e in meta["logos"]] == [13]

    def test_fetch_failure_degrades_to_a_counted_miss_not_a_failed_backup(self, tmp_path, caplog):
        """The whole backup must survive one unfetchable logo.

        The residual failure modes (an upstream 5xx, a logo whose source is
        genuinely gone) degrade to the pre-fix behaviour: the logo's bytes are
        not archived, the restore reports it as a miss, and the artifact is
        otherwise complete and verifiable.
        """
        import httpx

        with caplog.at_level("WARNING"):
            art = _patched_build(
                tmp_path,
                source_logos=[self.HOSTED],
                logo_images={13: httpx.ConnectError("upstream down")},
            )
        assert art.zip_path.exists()
        assert verify_artifact_sha256(art.zip_path, art.sidecar_path) is True
        with zipfile.ZipFile(art.zip_path) as zf:
            logo_members = [n for n in zf.namelist() if n.startswith("binary/logos/")]
            meta = json.loads(zf.read("binary/metadata.json"))
        assert logo_members == []
        assert meta["logo_count"] == 0
        # The miss is REPORTED, and no path or url leaked into the log.
        assert "logo id=13" in caplog.text
        assert "/data/logos/" not in caplog.text

    def test_upstream_error_status_degrades_to_a_counted_miss(self, tmp_path):
        """A fetch that returns no bytes (upstream 404/5xx) is the same miss."""
        art = _patched_build(
            tmp_path, source_logos=[self.HOSTED], logo_images={13: None},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            meta = json.loads(zf.read("binary/metadata.json"))
        assert meta["logo_count"] == 0

    def test_stale_local_file_never_shadows_the_fetched_dispatcharr_bytes(self, tmp_path):
        """ONE source logo id must produce exactly ONE archived file, the real one.

        ``_build_source_logo_index`` correlates an ECM-local
        ``/config/uploads/logos/abc.png`` to a Dispatcharr logo BY BASENAME and
        stamps that logo's id on it. If the byte fetch then archived the same id
        under a second filename, the artifact would carry two entries claiming
        one source id, and the restore cannot tell them apart: the local file
        uploads first (on-disk members are written before fetched ones),
        registers the LOGO remap, and the authoritative bytes are then tier-1
        matched through it and SKIPPED as ALREADY_EXISTS_IDENTICAL. The channel
        silently keeps the stale image, with no failure and no miss.

        Dispatcharr is the source of truth, so the fetched bytes win outright.
        """
        art = _patched_build(
            tmp_path,
            with_logos=True,  # writes uploads/logos/abc.png (GIF) + def.jpg
            source_logos=[{"id": 44, "name": "Hosted ABC", "url": "/data/logos/abc.png"}],
            logo_images={44: PNG_1X1},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            names = set(zf.namelist())
            meta = json.loads(zf.read("binary/metadata.json"))
            archived = zf.read("binary/logos/abc.png")

        # Exactly one member for source id 44, carrying DISPATCHARR's bytes.
        claiming_44 = [e for e in meta["logos"] if e.get("id") == 44]
        assert len(claiming_44) == 1
        assert claiming_44[0]["filename"] == "abc.png"
        assert archived == PNG_1X1
        # The superseded local copy is gone, not renamed alongside.
        assert "binary/logos/abc-44.png" not in names
        # An UNCORRELATED local file is untouched: it is not a stale copy of
        # anything, so dropping it would lose an image.
        assert "binary/logos/def.jpg" in names

    def test_uncorrelated_local_logo_still_gets_a_collision_free_name(self, tmp_path):
        """A local file that is NOT a copy of the fetched logo keeps its own slot.

        Same basename, different source logo: both images are genuine and both
        must survive, so the fetched one takes the id-suffixed name.
        """
        art = _patched_build(
            tmp_path,
            with_logos=True,
            source_logos=[
                # id 44 correlates to the local abc.png by basename, so it is
                # NOT hosted (remote URL) and never fetched. id 45 is hosted and
                # happens to share the basename.
                {"id": 44, "name": "Remote ABC", "url": "https://cdn.example.com/abc.png"},
                {"id": 45, "name": "Hosted ABC", "url": "/data/logos/abc.png"},
            ],
            logo_images={45: PNG_1X1},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            names = set(zf.namelist())
            meta = json.loads(zf.read("binary/metadata.json"))
        assert "binary/logos/abc.png" in names
        assert "binary/logos/abc-45.png" in names
        by_filename = {e["filename"]: e for e in meta["logos"]}
        assert by_filename["abc-45.png"]["id"] == 45
        assert by_filename["abc.png"]["id"] == 44

    def test_no_spool_files_survive_the_build(self, tmp_path):
        """The fetched bytes live only until they are inside the ZIP."""
        art = _patched_build(
            tmp_path, source_logos=[self.HOSTED], logo_images={13: PNG_1X1},
        )
        leftovers = [p.name for p in art.zip_path.parent.glob("ecm-logo-bytes-*")]
        assert leftovers == []


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


class TestDegradedCategoryReporting:
    """zt3kf — a Dispatcharr fetch failure for one category must be visible on
    the built BackupArtifact (not just silently stubbed inside the ZIP), and
    must NOT degrade sibling categories in the same build (isolation
    contract)."""

    def test_clean_build_has_no_degraded_categories(self, tmp_path):
        art = _patched_build(tmp_path)
        assert art.degraded_categories == []

    def test_one_failing_category_is_reported_degraded(self, tmp_path):
        art = _patched_build(
            tmp_path,
            client_overrides={"get_dvr_rules": Exception("Dispatcharr 500")},
        )
        assert art.degraded_categories == ["dvr_rules"]

    def test_degraded_category_stub_is_isolated_from_siblings(self, tmp_path):
        """The failing category's YAML carries the _warning stub; every OTHER
        category's YAML still carries real gathered data, not a stub."""
        art = _patched_build(
            tmp_path,
            client_overrides={"get_dvr_rules": Exception("Dispatcharr 500")},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            dvr = yaml.safe_load(zf.read("categories/dvr_rules.yaml"))
            m3u = yaml.safe_load(zf.read("categories/m3u_accounts.yaml"))
            user_agents = yaml.safe_load(zf.read("categories/user_agents.yaml"))
        assert "_warning" in dvr["dispatcharr"]["dvr_rules"]
        # Siblings gathered in their OWN per-category call are untouched.
        assert m3u["dispatcharr"]["m3u_accounts"][0]["id"] == 1
        assert user_agents["dispatcharr"]["user_agents"][0]["id"] == 31

    def test_multiple_failing_categories_all_reported(self, tmp_path):
        art = _patched_build(
            tmp_path,
            client_overrides={
                "get_dvr_rules": Exception("Dispatcharr 500"),
                "get_user_agents": Exception("Dispatcharr 500"),
            },
        )
        assert sorted(art.degraded_categories) == ["dvr_rules", "user_agents"]

    # -----------------------------------------------------------------
    # PR #770 review BLOCK: total client unavailability (get_client()
    # falsy/raising BEFORE any per-key fetch) produces the UN-NESTED
    # {"_warning": ...} shape rather than the per-key nested shape above.
    # The reviewer's repro: patch get_client to raise/return None, run the
    # real build_backup_artifact -> degraded_categories must list EVERY
    # requested dispatcharr category, not stay empty.
    # -----------------------------------------------------------------

    def _all_dispatcharr_keys(self):
        return sorted(
            k for k, v in backup_mod.RESTORABLE_SECTIONS.items() if v.get("dispatcharr")
        )

    def test_client_returns_none_degrades_every_dispatcharr_category(self, tmp_path):
        art = _patched_build(tmp_path, get_client_override={"return_value": None})
        assert sorted(art.degraded_categories) == self._all_dispatcharr_keys()

    def test_client_raises_degrades_every_dispatcharr_category(self, tmp_path):
        art = _patched_build(
            tmp_path,
            get_client_override={"side_effect": Exception("Dispatcharr unreachable")},
        )
        assert sorted(art.degraded_categories) == self._all_dispatcharr_keys()


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


class TestSettingsAgentsProducers:
    """lc6zu — the builder must emit the user_agents / dvr_rules / core_settings
    / comskip categories the settings/agents restore importer consumes (were
    absent before this bead: the wired importers received empty plan slices)."""

    def test_restorable_sections_include_settings_agents_categories(self):
        for key in ("user_agents", "dvr_rules", "core_settings", "comskip"):
            assert key in backup_mod.RESTORABLE_SECTIONS, "missing section %s" % key
            info = backup_mod.RESTORABLE_SECTIONS[key]
            assert info["dispatcharr"] is True, "%s must be Dispatcharr-sourced" % key
            # Legacy per-section YAML restore has no restorer for these — they
            # are artifact-path-only, exactly like channels/dispatcharr_users.
            assert info["artifact_only"] is True, "%s must be artifact_only" % key

    def test_user_agents_category_emitted(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/user_agents.yaml"))
        rows = parsed["dispatcharr"]["user_agents"]
        assert [r["name"] for r in rows] == ["ECM UA"]
        assert rows[0]["user_agent"] == "ECM/1.0"

    def test_dvr_rules_category_emitted(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/dvr_rules.yaml"))
        rows = parsed["dispatcharr"]["dvr_rules"]
        assert [r["name"] for r in rows] == ["Record CNN"]
        assert rows[0]["channel"] == 5

    def test_core_settings_normalized_split_and_redacted(self, tmp_path):
        """The list-of-{key,value} upstream shape is normalized to a mapping,
        comskip-prefixed keys are split OUT, and a dangerous-marked key's value
        carries the redaction sentinel (never the real value)."""
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/core_settings.yaml"))
        blob = parsed["dispatcharr"]["core_settings"]
        assert blob["default_user_agent"] == "ECM/1.0"
        # ``provider_api_key`` matches the importer's dangerous-key markers —
        # its value is redacted at the producer (the importer would never apply
        # it anyway; carrying the real value is pure leak risk).
        assert blob["provider_api_key"] == backup_mod.REDACTED
        assert "comskip_ini" not in blob

    def test_comskip_section_carries_the_comskip_keys(self, tmp_path):
        art = _patched_build(tmp_path)
        with zipfile.ZipFile(art.zip_path) as zf:
            parsed = yaml.safe_load(zf.read("categories/comskip.yaml"))
        blob = parsed["dispatcharr"]["comskip"]
        assert blob == {"comskip_ini": "[main]"}


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


class TestLogoByteBudgets:
    """The fetched logo subtree stays inside the caps the RESTORE side enforces.

    A builder that quietly produced an artifact whose member count or
    uncompressed size exceeded the restore-time decompression-bomb guard would
    be writing a silently unrestorable backup, which is the same class of defect
    as the missing bytes themselves.
    """

    def test_file_budget_stays_under_the_restore_side_entry_cap(self):
        assert backup_mod._MAX_FETCHED_LOGO_COUNT < backup_mod._ARTIFACT_MAX_ENTRIES

    def test_budget_is_what_is_LEFT_of_the_cumulative_cap_not_a_flat_constant(self, tmp_path):
        """The restore-side uncompressed cap is CUMULATIVE over every member.

        Comparing the flat ceiling against the cap proves nothing: a journal.db
        and an on-disk logo subtree spend the same budget, so a builder that
        always spent the flat ceiling on logos could still exceed the cap and
        write an artifact ECM's own validator refuses. The budget must shrink by
        whatever the other members already committed.
        """
        committed = backup_mod._ARTIFACT_MAX_TOTAL_UNCOMPRESSED - 1024
        budget = backup_mod._logo_byte_budget(tmp_path, committed)
        assert budget <= 1024

    def test_budget_is_zero_when_the_artifact_has_no_headroom_left(self, tmp_path):
        assert backup_mod._logo_byte_budget(
            tmp_path, backup_mod._ARTIFACT_MAX_TOTAL_UNCOMPRESSED
        ) == 0

    def test_budget_is_bounded_by_free_disk_measured_at_fetch_time(self, tmp_path, monkeypatch):
        """The pre-build free-disk check sized journal.db + BACKUP_DIRS, which by
        this bead's premise carry no logo bytes at all. Fetched payloads land on
        that partition TWICE (spooled, then compressed into the ZIP)."""
        free = 200 * 1024 * 1024

        class _Usage:
            total = free
            used = 0

        _Usage.free = free
        monkeypatch.setattr(backup_mod.shutil, "disk_usage", lambda _p: _Usage)
        budget = backup_mod._logo_byte_budget(tmp_path, 0)
        assert budget == (free - backup_mod._DISK_HEADROOM_BYTES) // 2
        assert budget < backup_mod._MAX_FETCHED_LOGO_TOTAL_BYTES

    def test_budget_never_goes_negative_on_a_nearly_full_disk(self, tmp_path, monkeypatch):
        class _Usage:
            total = 1
            used = 1
            free = 0

        monkeypatch.setattr(backup_mod.shutil, "disk_usage", lambda _p: _Usage)
        assert backup_mod._logo_byte_budget(tmp_path, 0) == 0

    def test_an_artifact_built_at_budget_passes_the_restore_side_guard(self, tmp_path):
        """The claim these budgets exist to make, tested end to end.

        Builds a real artifact whose logo subtree is filled right up against a
        (shrunk) budget and runs the finished ZIP through the SAME guard the
        restore path calls. If the budget arithmetic were wrong, this is where a
        silently unrestorable artifact would show up.
        """
        big = PNG_1X1 + b"\x00" * 4096
        art = _patched_build(
            tmp_path,
            source_logos=[
                {"id": i, "name": "L%d" % i, "url": "/data/logos/l%d.png" % i}
                for i in range(1, 21)
            ],
            logo_images={i: big for i in range(1, 21)},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            # Does not raise: the artifact this builder produced is one this
            # build of ECM will accept back.
            backup_mod.guard_artifact_against_zip_bomb(zf)
            meta = json.loads(zf.read("binary/metadata.json"))
        assert meta["logo_count"] == 20

    def test_file_budget_stops_the_fetch_and_counts_the_rest(self, tmp_path, monkeypatch):
        """Past the cap the builder stops fetching and reports the shortfall."""
        monkeypatch.setattr(backup_mod, "_MAX_FETCHED_LOGO_COUNT", 1)
        art = _patched_build(
            tmp_path,
            source_logos=[
                {"id": 1, "name": "One", "url": "/data/logos/one.png"},
                {"id": 2, "name": "Two", "url": "/data/logos/two.png"},
            ],
            logo_images={1: PNG_1X1, 2: PNG_1X1},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            logo_members = [n for n in zf.namelist() if n.startswith("binary/logos/")]
        assert logo_members == ["binary/logos/one.png"]

    def test_a_logo_over_the_per_logo_cap_is_not_archived(self, tmp_path):
        """The per-logo cap mirrors the restore-side validator's own cap, so the
        builder never archives a payload the importer would reject."""
        oversized = PNG_1X1 + b"\x00" * (backup_mod.MAX_LOGO_BYTES + 1)
        art = _patched_build(
            tmp_path,
            source_logos=[{"id": 9, "name": "Huge", "url": "/data/logos/huge.png"}],
            logo_images={9: oversized},
        )
        with zipfile.ZipFile(art.zip_path) as zf:
            meta = json.loads(zf.read("binary/metadata.json"))
        assert meta["logo_count"] == 0


class TestLogoGatherFailsSoft:
    """No logo problem may fail the operator's backup.

    Every logo path in the builder is best-effort by contract. These pin the
    paths that could otherwise propagate into build_backup_artifact's
    ``except Exception:`` and destroy the artifact over a picture.
    """

    HOSTED = {"id": 13, "name": "Hosted", "url": "/data/logos/hosted.png"}

    def test_get_client_raising_does_not_fail_the_backup(self, tmp_path):
        """``get_client`` reads settings and can throw.

        It is called for the logo listing AND the byte fetch, outside any
        per-category isolation, so an unguarded call turns a settings problem
        into a failed backup.
        """
        art = _patched_build(
            tmp_path,
            get_client_override={"side_effect": RuntimeError("settings exploded")},
        )
        assert art.zip_path.exists()
        assert verify_artifact_sha256(art.zip_path, art.sidecar_path) is True

    def test_spool_dir_creation_failure_does_not_fail_the_backup(self, tmp_path, monkeypatch):
        """A read-only or full /config must degrade the logo bytes, not the run."""
        def _boom(*args, **kwargs):
            raise OSError("read-only file system")

        monkeypatch.setattr(backup_mod.tempfile, "mkdtemp", _boom)
        art = _patched_build(
            tmp_path, source_logos=[self.HOSTED], logo_images={13: PNG_1X1},
        )
        assert art.zip_path.exists()
        assert verify_artifact_sha256(art.zip_path, art.sidecar_path) is True
        with zipfile.ZipFile(art.zip_path) as zf:
            assert [n for n in zf.namelist() if n.startswith("binary/logos/")] == []
        # Degraded, and reported as such.
        assert "logos" in art.degraded_categories
        assert art.unarchived_logo_bytes == 1


class TestLogoDegradedReporting:
    """A backup that could not archive logo bytes is a WARNING, never silent.

    zt3kf's ratified shape for a partial gather. Before this, the ONLY signal was
    a container log line: the task returned ``success=True, failed_count=0`` for
    an artifact whose logo payload was entirely missing.
    """

    HOSTED = {"id": 13, "name": "Hosted", "url": "/data/logos/hosted.png"}

    def test_unfetchable_logo_marks_the_logos_category_degraded(self, tmp_path):
        art = _patched_build(
            tmp_path, source_logos=[self.HOSTED], logo_images={13: None},
        )
        assert "logos" in art.degraded_categories
        assert art.unarchived_logo_bytes == 1

    def test_fully_archived_logos_leave_the_backup_clean(self, tmp_path):
        art = _patched_build(
            tmp_path, source_logos=[self.HOSTED], logo_images={13: PNG_1X1},
        )
        assert art.degraded_categories == []
        assert art.unarchived_logo_bytes == 0

    def test_logos_is_a_real_restorable_section_key(self):
        """It threads through tasks.dbas_backup's existing degraded plumbing only
        because it is a genuine category key, not a synthetic marker."""
        assert "logos" in backup_mod.RESTORABLE_SECTIONS

    def test_degraded_logos_does_not_mask_a_degraded_dispatcharr_category(self, tmp_path):
        """Both signals coexist; neither overwrites the other."""
        art = _patched_build(
            tmp_path,
            source_logos=[self.HOSTED],
            logo_images={13: None},
            client_overrides={"get_dvr_rules": RuntimeError("upstream 500")},
        )
        assert "logos" in art.degraded_categories
        assert "dvr_rules" in art.degraded_categories
        assert art.degraded_categories == sorted(art.degraded_categories)


class TestOrphanedLogoSpoolSweep:
    """A container kill mid-backup must not leave hundreds of MB in /config.

    Retention's allowlist matches only ``ecm-backup-*.zip``, so nothing else
    would ever reclaim these.
    """

    def test_stale_spool_dir_is_removed_on_the_next_build(self, tmp_path):
        import os
        import time as _time

        stale = tmp_path / (backup_mod._LOGO_SPOOL_PREFIX + "orphan")
        stale.mkdir()
        (stale / "13").write_bytes(b"leftover")
        old = _time.time() - backup_mod._LOGO_SPOOL_ORPHAN_AGE_SECONDS - 60
        os.utime(stale, (old, old))

        _patched_build(tmp_path, dest_dir=tmp_path)
        assert not stale.exists()

    def test_a_concurrent_builds_fresh_spool_is_left_alone(self, tmp_path):
        """The age floor is what makes the sweep safe to run unconditionally."""
        live = tmp_path / (backup_mod._LOGO_SPOOL_PREFIX + "live")
        live.mkdir()
        (live / "13").write_bytes(b"in-flight")

        _patched_build(tmp_path, dest_dir=tmp_path)
        assert live.exists()
