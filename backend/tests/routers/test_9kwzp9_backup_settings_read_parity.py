"""bead enhancedchannelmanager-9kwzp.9 — the backup read path must not defeat 9ej7f.

THE DEFECT
----------

Bead 9ej7f made ``GET /api/settings`` withhold the VALUES of
``discord_webhook_url``, ``telegram_bot_token`` and ``telegram_chat_id`` from
any caller ``routers.settings._resolve_settings_admin`` classifies as
non-admin — a set that deliberately includes the static MCP service principal,
because the MCP key is an automation credential, not an operator identity.

``GET /api/backup/create``, ``GET /api/backup/export`` and
``GET /api/backup/saved/{filename}`` carry ``RequireAdminIfEnabled``, which
ADMITS that principal (``auth.dependencies._build_mcp_service_principal`` sets
``is_admin=True``). The artifact's redaction list was a hand-maintained literal
in ``routers.backup``; it happened to contain ``telegram_bot_token`` and did not
contain the other two, and ``_ALERT_METHOD_CREDENTIAL_KEYS`` did not cover them
either — that set matches keys INSIDE the ``alert_methods.config`` JSON blob,
not top-level settings fields. So the exact principal 9ej7f had just refused
read two thirds of the partition out of a standard backup instead.

WHAT IS PINNED HERE
-------------------

The two missing fields are the symptom. The defect is that ONE policy had TWO
hand-maintained expressions, so the class stays closed only while both are
remembered. ``routers.backup._SETTINGS_CREDENTIAL_FIELDS`` now DERIVES the
partition from ``config.ADMIN_ONLY_READ_REDACTED_FIELDS``.

:class:`TestDerivationInvariant` is the tripwire for the derivation itself: it
fails the moment someone re-inlines a literal that drops a field, which is the
only way the two can diverge again. The behavioural tests below prove the
derivation actually reaches the emitted bytes on every read path, and the two
non-regression tests prove it did not break the restore side or the opt-in
credential-carrying artifact.
"""
import io
import json
import sqlite3
import zipfile
from unittest.mock import MagicMock, patch

import pytest
import yaml

from config import ADMIN_ONLY_READ_REDACTED_FIELDS
from credential_sentinel import REDACTION_SENTINEL
from routers.backup import (
    _REDACT_KEYS,
    _SETTINGS_CREDENTIAL_FIELDS,
    _gather_settings,
    _merge_settings_preserving_redacted,
    _redact_credentials_deep,
)


# Distinctive values so a raw leak is findable anywhere in an artifact's bytes.
WEBHOOK = "https://discord.com/api/webhooks/1/9kwzp9-RAW-WEBHOOK-VALUE"
BOT_TOKEN = "9kwzp9-RAW-BOT-TOKEN-VALUE"
CHAT_ID = "9kwzp9-RAW-CHAT-ID-VALUE"

RAW_PARTITION_VALUES = {
    "discord_webhook_url": WEBHOOK,
    "telegram_bot_token": BOT_TOKEN,
    "telegram_chat_id": CHAT_ID,
}


def _settings_dict() -> dict:
    """A settings blob carrying the whole read-redaction partition in clear."""
    return {
        "url": "http://dispatcharr:9191",
        "username": "admin",
        **RAW_PARTITION_VALUES,
    }


def _mock_settings():
    stub = MagicMock()
    stub.model_dump.return_value = _settings_dict()
    return stub


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------
class TestDerivationInvariant:
    """The two enforcement points of one policy cannot drift apart."""

    def test_backup_denylist_covers_the_settings_read_redaction_partition(self):
        missing = ADMIN_ONLY_READ_REDACTED_FIELDS - set(_SETTINGS_CREDENTIAL_FIELDS)
        assert not missing, (
            "routers.backup._SETTINGS_CREDENTIAL_FIELDS no longer covers "
            "config.ADMIN_ONLY_READ_REDACTED_FIELDS: %s. GET /api/settings "
            "withholds these from the MCP service principal; GET "
            "/api/backup/create, /export and /saved/{filename} admit that same "
            "principal, so any field in the partition but not in this tuple is "
            "readable out of a standard backup artifact by the caller the "
            "settings endpoint just refused. Fold the partition back in by "
            "DERIVING it (bead 9kwzp.9) — do not restate the names."
            % sorted(missing)
        )

    def test_deep_artifact_redactor_covers_the_partition_too(self):
        """``_REDACT_KEYS`` is the non-bypassable DBAS-artifact stage."""
        missing = {f.lower() for f in ADMIN_ONLY_READ_REDACTED_FIELDS} - _REDACT_KEYS
        assert not missing, (
            "_REDACT_KEYS (the non-bypassable deep redactor for the DBAS "
            "artifact) no longer covers %s" % sorted(missing)
        )

    def test_denylist_has_no_duplicate_entries(self):
        """The derivation overlaps the literal core on ``telegram_bot_token``.

        A duplicate would be harmless at runtime (the redaction loop is
        idempotent) but signals the dedupe was dropped, which is the shape a
        careless re-inlining takes.
        """
        assert len(_SETTINGS_CREDENTIAL_FIELDS) == len(set(_SETTINGS_CREDENTIAL_FIELDS))

    def test_historical_credential_fields_are_still_present(self):
        """Deriving must ADD to the denylist, never replace what it had."""
        for field in (
            "password",
            "dispatcharr_api_key",
            "api_key",
            "smtp_password",
            "telegram_bot_token",
            "mcp_api_key",
        ):
            assert field in _SETTINGS_CREDENTIAL_FIELDS, field


# ---------------------------------------------------------------------------
# The behaviour, on every read path that admits the MCP principal
# ---------------------------------------------------------------------------
class TestArtifactRedaction:
    def test_gather_settings_redacts_the_whole_partition(self):
        with patch("routers.backup.get_settings", return_value=_mock_settings()):
            data = _gather_settings()

        for field in ADMIN_ONLY_READ_REDACTED_FIELDS:
            assert data[field] == REDACTION_SENTINEL, field

    @pytest.mark.asyncio
    async def test_zip_create_leaks_no_partition_value(self, async_client, tmp_path):
        """GET /api/backup/create — the ZIP an MCP-key caller can download."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps(_settings_dict()))
        db_file = tmp_path / "journal.db"
        sqlite3.connect(str(db_file)).close()

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("routers.backup.CONFIG_DIR", tmp_path), \
             patch("routers.backup.CONFIG_FILE", settings_file), \
             patch("routers.backup.JOURNAL_DB_FILE", db_file), \
             patch("routers.backup.get_engine", return_value=mock_engine), \
             patch("routers.backup.get_settings", return_value=_mock_settings()):
            response = await async_client.get("/api/backup/create")

        assert response.status_code == 200
        archive = response.content
        for field, value in RAW_PARTITION_VALUES.items():
            assert value.encode() not in archive, (
                "raw %s leaked into the backup ZIP" % field
            )

        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            emitted = json.loads(zf.read("settings.json"))
        for field in ADMIN_ONLY_READ_REDACTED_FIELDS:
            assert emitted[field] == REDACTION_SENTINEL, field

    @pytest.mark.asyncio
    async def test_yaml_export_leaks_no_partition_value(self, async_client, test_session):
        """GET /api/backup/export — the same values over the YAML path."""
        with patch("routers.backup.get_settings", return_value=_mock_settings()), \
             patch("routers.backup.get_client", return_value=None):
            response = await async_client.get("/api/backup/export")

        assert response.status_code == 200
        for field, value in RAW_PARTITION_VALUES.items():
            assert value not in response.text, (
                "raw %s leaked into the YAML export" % field
            )

        data = yaml.safe_load(response.text)
        for field in ADMIN_ONLY_READ_REDACTED_FIELDS:
            assert data["settings"][field] == REDACTION_SENTINEL, field

    def test_deep_redactor_catches_the_partition_at_any_depth(self):
        """The DBAS artifact stage, which runs over EVERY gathered category."""
        payload = {
            "settings": {"discord_webhook_url": WEBHOOK},
            "rows": [{"telegram_chat_id": CHAT_ID, "telegram_bot_token": BOT_TOKEN}],
        }
        out = _redact_credentials_deep(payload)
        assert out["settings"]["discord_webhook_url"] == REDACTION_SENTINEL
        assert out["rows"][0]["telegram_chat_id"] == REDACTION_SENTINEL
        assert out["rows"][0]["telegram_bot_token"] == REDACTION_SENTINEL


# ---------------------------------------------------------------------------
# Non-regression: redacting more must not break the two paths that read it back
# ---------------------------------------------------------------------------
class TestRedactingMoreBreaksNothing:
    def test_restore_preserves_the_existing_value_behind_the_sentinel(self, tmp_path):
        """A redacted artifact must never overwrite a working webhook.

        This is the …-6pilh contract and it is why redacting MORE fields is
        safe: the restore side skips every sentinel-valued key and keeps what
        the destination already had, rather than writing the placeholder in.
        """
        config_file = tmp_path / "settings.json"
        config_file.write_text(json.dumps({
            "url": "http://dispatcharr:9191",
            "discord_webhook_url": "https://discord.com/api/webhooks/existing",
            "telegram_chat_id": "existing-chat",
        }))
        zipped = json.dumps({
            "url": "http://restored:9191",
            "discord_webhook_url": REDACTION_SENTINEL,
            "telegram_chat_id": REDACTION_SENTINEL,
        }).encode()

        with patch("routers.backup.CONFIG_FILE", config_file):
            merged = json.loads(_merge_settings_preserving_redacted(zipped))

        assert merged["url"] == "http://restored:9191"
        assert merged["discord_webhook_url"] == "https://discord.com/api/webhooks/existing"
        assert merged["telegram_chat_id"] == "existing-chat"

    def test_credential_carrying_artifact_still_carries_the_partition(self):
        """ADR-012 D12 / u81kh — the opt-in encrypted migration path is unaffected.

        ``include_credentials`` is only ever True inside a passphrase-encrypted
        artifact, so the operator who explicitly asked to migrate credentials
        still gets the webhook and chat id across.
        """
        with patch("routers.backup.get_settings", return_value=_mock_settings()):
            data = _gather_settings(include_credentials=True)

        for field, value in RAW_PARTITION_VALUES.items():
            assert data[field] == value, field
