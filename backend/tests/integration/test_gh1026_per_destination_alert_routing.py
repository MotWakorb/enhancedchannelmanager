"""GH #1026 — per-destination alert routing, across the real boundaries.

Real: the ``/api/alert-methods`` and ``/api/settings`` routes over the ASGI
app, SQLite (the test engine that every direct ``get_session()`` call reaches
through ``async_client``), the ``AlertMethodManager`` and its digest flush,
the Discord / Telegram method classes, ``notification_service`` dispatch,
``settings.json`` persistence for the legacy-settings migration, and the
YAML / DBAS restore helpers. Mocked: only the outbound HTTP client
(``aiohttp.ClientSession``), which records what would have been sent.
"""
import asyncio
import html
import json
import re
from unittest.mock import patch

import pytest

import alert_methods
import config
from alert_methods import AlertMethodManager, alert_category_for_task
from alert_methods_discord import DiscordWebhookMethod
from alert_methods_ntfy import NtfyMethod
from alert_methods_smtp import SMTPMethod
from alert_methods_telegram import TelegramBotMethod
from credential_sentinel import REDACTION_SENTINEL
from models import AlertMethod

WEBHOOK_A = "https://discord.com/api/webhooks/101/aaaaaaaa"
WEBHOOK_B = "https://discord.com/api/webhooks/202/bbbbbbbb"
CANARY_WEBHOOK = "https://canary.discord.com/api/webhooks/303/cccccccc"
EVIL_WEBHOOK = "https://evil.example.com/api/webhooks/1/x"
# Real Telegram token SHAPE (``<bot id>:<secret>``), assembled from parts so no
# token-shaped literal sits on one line (docs/pytest_conventions.md).
BOT_TOKEN = "123456789:" + "AAEabcdefghijklmno" + "pqrstuvwxyz012345"
CHAT_ID = "-1001234567890"
MASK = "********"


# ---------------------------------------------------------------------------
# Outbound HTTP recorder
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload
        self.headers = {}

    async def json(self):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Outbox:
    """Stands in for ``aiohttp.ClientSession``; records every request."""

    def __init__(self):
        self.posts = []

    def __call__(self, *args, **kwargs):
        return _FakeSession(self)

    def urls(self):
        return [url for url, _ in self.posts]

    def payloads_to(self, url):
        return [payload for posted_url, payload in self.posts if posted_url == url]


class _FakeSession:
    def __init__(self, outbox):
        self._outbox = outbox

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, **kwargs):
        self._outbox.posts.append((url, json))
        if "api.telegram.org" in url:
            return _FakeResponse(200, {"ok": True})
        return _FakeResponse(204, {})

    def get(self, url, **kwargs):
        return _FakeResponse(200, {"ok": True, "result": {"username": "ecm_test_bot"}})


@pytest.fixture
def outbox(monkeypatch):
    recorder = _Outbox()
    # Both method modules (and the M3U digest) use ``aiohttp.ClientSession``
    # from the one aiohttp module object.
    monkeypatch.setattr("alert_methods_discord.aiohttp.ClientSession", recorder)
    return recorder


@pytest.fixture
def manager(monkeypatch):
    """A fresh global alert manager over the test DB, real method classes.

    Other suites clear the method registry without restoring it, so the four
    shipped types are registered here explicitly for the test's duration.
    """
    for cls in (DiscordWebhookMethod, TelegramBotMethod, NtfyMethod, SMTPMethod):
        monkeypatch.setitem(alert_methods._method_registry, cls.method_type, cls)
    monkeypatch.setattr(alert_methods, "_manager", None)
    yield alert_methods.get_alert_manager
    current = alert_methods._manager
    if current is not None and current._flush_task is not None:
        current._flush_task.cancel()


async def _create(client, **body):
    body.setdefault("enabled", True)
    response = await client.post("/api/alert-methods", json=body)
    assert response.status_code == 200, response.text
    return response.json()["id"]


async def _discord(client, name, url, **extra):
    return await _create(client, name=name, method_type="discord", config={"webhook_url": url}, **extra)


async def _dispatch(**kwargs):
    from services.notification_service import _dispatch_to_alert_channels

    kwargs.setdefault("title", "Test alert")
    kwargs.setdefault("message", "Something happened")
    kwargs.setdefault("notification_type", "warning")
    kwargs.setdefault("source", "test")
    kwargs.setdefault("metadata", None)
    return await _dispatch_to_alert_channels(**kwargs)


def _stored_config(test_session, method_id):
    test_session.expire_all()
    row = test_session.query(AlertMethod).filter(AlertMethod.id == method_id).one()
    return json.loads(row.config)


# ---------------------------------------------------------------------------
# Routing: each destination applies its own toggles and filters
# ---------------------------------------------------------------------------
class TestPerDestinationRouting:
    @pytest.mark.asyncio
    async def test_category_filter_applies_per_destination(self, async_client, outbox, manager):
        """Discord B opts out of Emby alerts; Discord A still gets them. Driven
        through ``create_notification_internal``, the public entry point."""
        from services import notification_service

        await _discord(async_client, "A", WEBHOOK_A)
        await _discord(async_client, "B", WEBHOOK_B, alert_sources={"emby": {"enabled": False}})

        dispatches = []

        def _capture(coro):
            task = asyncio.ensure_future(coro)
            dispatches.append(task)
            return task

        with patch.object(notification_service.asyncio, "create_task", side_effect=_capture):
            await notification_service.create_notification_internal(
                notification_type="error",
                title="Clear Emby Logos",
                message="Clear Emby logos failed",
                source="emby",
                alert_category="emby",
            )
        await asyncio.gather(*dispatches)
        await manager().flush_now()

        assert outbox.urls() == [WEBHOOK_A]
        payload = outbox.payloads_to(WEBHOOK_A)[0]
        assert payload["embeds"][0]["description"] == "Clear Emby logos failed"

    @pytest.mark.asyncio
    async def test_per_task_toggle_covers_every_destination_of_that_type(self, async_client, outbox, manager):
        await _discord(async_client, "A", WEBHOOK_A)
        await _discord(async_client, "B", WEBHOOK_B)
        await _create(
            async_client, name="TG", method_type="telegram",
            config={"bot_token": BOT_TOKEN, "chat_id": CHAT_ID},
        )

        await _dispatch(
            alert_category="task_results",
            channel_settings={"send_to_email": False, "send_to_discord": False, "send_to_telegram": True},
        )
        await manager().flush_now()

        assert outbox.urls() == [f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"]

    @pytest.mark.asyncio
    async def test_uncategorised_alert_is_filed_under_other(self, async_client, outbox, manager):
        await _discord(async_client, "A", WEBHOOK_A, alert_sources={"other": {"enabled": False}})
        await _discord(async_client, "B", WEBHOOK_B)

        await _dispatch(alert_category=None)
        await manager().flush_now()

        assert outbox.urls() == [WEBHOOK_B]

    @pytest.mark.asyncio
    async def test_severity_toggles_apply_per_destination(self, async_client, outbox, manager):
        # A takes the create defaults (info off); B opts into info.
        await _discord(async_client, "A", WEBHOOK_A)
        await _discord(async_client, "B", WEBHOOK_B, notify_info=True)

        await _dispatch(notification_type="info", alert_category="task_results")
        await manager().flush_now()

        assert outbox.urls() == [WEBHOOK_B]

    @pytest.mark.asyncio
    async def test_epg_refresh_alert_reaches_discord_with_entity_filter(self, async_client, outbox, manager):
        """EPG / M3U refresh alerts call ``alert_methods.send_alert`` directly.
        Before #1026 they never reached the shared Discord webhook; a Discord
        destination now receives them, subject to its own source list."""
        await _discord(
            async_client, "A", WEBHOOK_A,
            alert_sources={"epg_refresh": {"enabled": True, "filter_mode": "only_selected", "source_ids": [5]}},
        )

        await alert_methods.send_alert(
            title="EPG Refresh: Guide 6", message="failed", notification_type="error",
            alert_category="epg_refresh", entity_id=6,
        )
        await manager().flush_now()
        assert outbox.urls() == []

        await alert_methods.send_alert(
            title="EPG Refresh: Guide 5", message="failed", notification_type="error",
            alert_category="epg_refresh", entity_id=5,
        )
        await manager().flush_now()
        assert outbox.urls() == [WEBHOOK_A]

    @pytest.mark.asyncio
    async def test_scheduled_task_results_have_their_own_category(self, async_client, outbox, manager):
        """Turning off "task results" silences ordinary task alerts but not the
        stream-probe tasks, which stay under the probe-failure category."""
        assert alert_category_for_task("cleanup") == "task_results"
        assert alert_category_for_task("stream_probe") == "probe_failures"
        assert alert_category_for_task("auto_creation") == "channel_pipeline"
        assert alert_category_for_task("dbas_restore") == "dbas_backup"
        await _discord(async_client, "A", WEBHOOK_A, alert_sources={"task_results": {"enabled": False}})

        await _dispatch(alert_category=alert_category_for_task("cleanup"))
        await manager().flush_now()
        assert outbox.urls() == []

        await _dispatch(alert_category=alert_category_for_task("stream_probe"))
        await manager().flush_now()
        assert outbox.urls() == [WEBHOOK_A]

    @pytest.mark.asyncio
    async def test_invalid_category_section_is_rejected(self, async_client, manager):
        response = await async_client.post("/api/alert-methods", json={
            "name": "A", "method_type": "discord", "config": {"webhook_url": WEBHOOK_A},
            "alert_sources": {"emby": {"enabled": "no"}},
        })
        assert response.status_code == 400
        assert "emby.enabled" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Editing a destination never needs (or leaks) its secret
# ---------------------------------------------------------------------------
class TestEditKeepsStoredSecrets:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("sent", [None, "", MASK])
    async def test_discord_blank_or_masked_webhook_keeps_stored(self, async_client, test_session, manager, sent):
        method_id = await _discord(async_client, "A", WEBHOOK_A)
        config_body = {"username": "Renamed bot"}
        if sent is not None:
            config_body["webhook_url"] = sent

        response = await async_client.patch(f"/api/alert-methods/{method_id}", json={"config": config_body})

        assert response.status_code == 200, response.text
        assert _stored_config(test_session, method_id) == {"webhook_url": WEBHOOK_A, "username": "Renamed bot"}

    @pytest.mark.asyncio
    async def test_discord_new_webhook_replaces_stored(self, async_client, test_session, manager):
        method_id = await _discord(async_client, "A", WEBHOOK_A)

        response = await async_client.patch(
            f"/api/alert-methods/{method_id}", json={"config": {"webhook_url": WEBHOOK_B}},
        )

        assert response.status_code == 200, response.text
        assert _stored_config(test_session, method_id)["webhook_url"] == WEBHOOK_B

    @pytest.mark.asyncio
    async def test_telegram_masked_token_kept_while_chat_changes(self, async_client, test_session, manager):
        method_id = await _create(
            async_client, name="TG", method_type="telegram",
            config={"bot_token": BOT_TOKEN, "chat_id": CHAT_ID},
        )
        listed = (await async_client.get("/api/alert-methods")).json()
        echoed = next(m for m in listed if m["id"] == method_id)["config"]
        assert echoed["bot_token"] == MASK and echoed["chat_id"] == MASK

        response = await async_client.patch(
            f"/api/alert-methods/{method_id}",
            json={"config": {**echoed, "chat_id": "-100999"}},
        )

        assert response.status_code == 200, response.text
        assert _stored_config(test_session, method_id) == {"bot_token": BOT_TOKEN, "chat_id": "-100999"}

    @pytest.mark.asyncio
    async def test_filter_only_patch_leaves_config_untouched(self, async_client, test_session, manager):
        method_id = await _discord(async_client, "A", WEBHOOK_A)

        response = await async_client.patch(f"/api/alert-methods/{method_id}", json={
            "notify_info": True,
            "alert_sources": {"emby": {"enabled": False}},
        })

        assert response.status_code == 200, response.text
        assert _stored_config(test_session, method_id) == {"webhook_url": WEBHOOK_A}
        test_session.expire_all()
        row = test_session.query(AlertMethod).filter(AlertMethod.id == method_id).one()
        assert row.notify_info is True
        assert json.loads(row.alert_sources) == {"emby": {"enabled": False}}

    @pytest.mark.asyncio
    async def test_ntfy_masked_token_is_still_refused(self, async_client, manager):
        """The keep-stored-secret rule deliberately excludes ntfy: its token
        must never ride along to a new server_url (credential harvesting)."""
        method_id = await _create(
            async_client, name="ntfy", method_type="ntfy",
            config={"server_url": "https://ntfy.example.test", "topic": "ecm", "access_token": "tk_" + "abc"},
        )

        response = await async_client.patch(f"/api/alert-methods/{method_id}", json={
            "config": {"server_url": "https://attacker.example.test", "topic": "ecm", "access_token": MASK},
        })

        assert response.status_code == 400


class TestDestinationValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("url", [WEBHOOK_A, CANARY_WEBHOOK, "https://ptb.discord.com/api/webhooks/1/x"])
    async def test_allowlisted_discord_hosts_are_accepted(self, async_client, manager, url):
        await _discord(async_client, "A", url)

    @pytest.mark.asyncio
    async def test_non_discord_host_is_refused_on_create_and_update(self, async_client, manager):
        response = await async_client.post("/api/alert-methods", json={
            "name": "A", "method_type": "discord", "config": {"webhook_url": EVIL_WEBHOOK},
        })
        assert response.status_code == 400

        method_id = await _discord(async_client, "A", WEBHOOK_A)
        response = await async_client.patch(
            f"/api/alert-methods/{method_id}", json={"config": {"webhook_url": EVIL_WEBHOOK}},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_malformed_telegram_token_is_refused(self, async_client, manager):
        response = await async_client.post("/api/alert-methods", json={
            "name": "TG", "method_type": "telegram",
            "config": {"bot_token": "not/a/token", "chat_id": CHAT_ID},
        })
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_stored_non_discord_url_is_never_posted(self, async_client, test_session, outbox, manager):
        """A row written before the allowlist existed is re-checked at send."""
        test_session.add(AlertMethod(
            name="Legacy", method_type="discord", enabled=True,
            config=json.dumps({"webhook_url": EVIL_WEBHOOK}),
            notify_info=True, notify_success=True, notify_warning=True, notify_error=True,
        ))
        test_session.commit()

        await _dispatch(alert_category="task_results")
        await manager().flush_now()

        assert outbox.posts == []


# ---------------------------------------------------------------------------
# Batching onto the digest path must not lose a large digest
# ---------------------------------------------------------------------------
def _telegram_visible_length(text: str) -> int:
    """Characters Telegram counts: the text after HTML markup is parsed."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


class TestDigestStaysWithinPlatformLimits:
    @pytest.mark.asyncio
    async def test_large_discord_digest_fits_one_embed(self, async_client, outbox, manager):
        await _discord(async_client, "A", WEBHOOK_A)

        for i in range(60):
            await _dispatch(title=f"EPG Refresh: Source {i}", message="x" * 200, alert_category="task_results")
        await manager().flush_now()

        assert outbox.urls() == [WEBHOOK_A]
        embed = outbox.payloads_to(WEBHOOK_A)[0]["embeds"][0]
        assert len(embed["description"]) <= 4096
        total = len(embed.get("title", "")) + len(embed["description"]) + len(embed.get("footer", {}).get("text", ""))
        total += sum(len(f["name"]) + len(f["value"]) for f in embed.get("fields", []))
        assert total <= 6000
        assert embed["description"].endswith("…")

    @pytest.mark.asyncio
    async def test_large_telegram_digest_fits_one_message(self, async_client, outbox, manager):
        await _create(
            async_client, name="TG", method_type="telegram",
            config={"bot_token": BOT_TOKEN, "chat_id": CHAT_ID},
        )

        for i in range(60):
            await _dispatch(title=f"M3U Refresh: Account {i}", message="<&>" * 70, alert_category="task_results")
        await manager().flush_now()

        assert len(outbox.posts) == 1
        text = outbox.posts[0][1]["text"]
        assert _telegram_visible_length(text) <= 4096


# ---------------------------------------------------------------------------
# Legacy shared settings -> Alert Methods rows
# ---------------------------------------------------------------------------
@pytest.fixture
def settings_file(monkeypatch, tmp_path):
    """Real settings.json persistence in a temp config dir."""
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "MCP_SECRETS_DIR", tmp_path / "mcp")
    monkeypatch.setattr(config, "MCP_KEY_FILE", tmp_path / "mcp" / "api-key")
    (tmp_path / "mcp").mkdir(mode=0o700)
    config.clear_settings_cache()

    def write(**fields):
        config.save_settings(config.DispatcharrSettings(**fields))
        config.clear_settings_cache()

    yield write
    config.clear_settings_cache()


def _reloaded_settings():
    config.clear_settings_cache()
    return config.get_settings()


def _rows(test_session, method_type=None):
    test_session.expire_all()
    query = test_session.query(AlertMethod)
    if method_type:
        query = query.filter(AlertMethod.method_type == method_type)
    return query.order_by(AlertMethod.id).all()


class TestLegacySettingsMigration:
    @pytest.mark.asyncio
    async def test_both_legacy_destinations_become_rows_and_fields_clear(
        self, async_client, test_session, settings_file, manager
    ):
        from notification_migration import migrate_legacy_notification_settings

        settings_file(discord_webhook_url=WEBHOOK_A, telegram_bot_token=BOT_TOKEN, telegram_chat_id=CHAT_ID)

        result = migrate_legacy_notification_settings()

        rows = {row.method_type: row for row in _rows(test_session)}
        assert set(rows) == {"discord", "telegram"}
        assert json.loads(rows["discord"].config) == {"webhook_url": WEBHOOK_A, "username": "ECM Alerts"}
        assert json.loads(rows["telegram"].config) == {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}
        for row in rows.values():
            assert row.enabled is True
            assert (row.notify_info, row.notify_success, row.notify_warning, row.notify_error) == (True, True, True, True)
            assert row.alert_sources is None
        assert sorted(result["created"]) == sorted(row.id for row in rows.values())

        on_disk = _reloaded_settings()
        assert (on_disk.discord_webhook_url, on_disk.telegram_bot_token, on_disk.telegram_chat_id) == ("", "", "")
        # The running manager already routes to the new rows.
        assert set(manager()._methods) >= {row.id for row in rows.values()}

    @pytest.mark.asyncio
    async def test_second_run_is_a_no_op(self, async_client, test_session, settings_file, manager):
        from notification_migration import migrate_legacy_notification_settings

        settings_file(discord_webhook_url=WEBHOOK_A)
        migrate_legacy_notification_settings()
        result = migrate_legacy_notification_settings()

        assert result == {"created": [], "existing": [], "cleared": []}
        assert len(_rows(test_session, "discord")) == 1

    @pytest.mark.asyncio
    async def test_crash_between_row_commit_and_settings_write_recovers(
        self, async_client, test_session, settings_file, manager, monkeypatch
    ):
        from notification_migration import migrate_legacy_notification_settings

        settings_file(discord_webhook_url=WEBHOOK_A)
        real_save = config.save_settings
        calls = {"n": 0}

        def _fail_once(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk full")
            return real_save(*args, **kwargs)

        monkeypatch.setattr(config, "save_settings", _fail_once)

        with pytest.raises(OSError):
            migrate_legacy_notification_settings()
        assert len(_rows(test_session, "discord")) == 1
        assert _reloaded_settings().discord_webhook_url == WEBHOOK_A

        result = migrate_legacy_notification_settings()

        assert len(_rows(test_session, "discord")) == 1
        assert result["created"] == [] and len(result["existing"]) == 1
        assert _reloaded_settings().discord_webhook_url == ""

    @pytest.mark.asyncio
    async def test_undeliverable_values_are_cleared_without_rows(
        self, async_client, test_session, settings_file, manager
    ):
        from notification_migration import migrate_legacy_notification_settings

        settings_file(discord_webhook_url=EVIL_WEBHOOK, telegram_bot_token=BOT_TOKEN, telegram_chat_id="")
        migrate_legacy_notification_settings()

        assert _rows(test_session) == []
        on_disk = _reloaded_settings()
        assert (on_disk.discord_webhook_url, on_disk.telegram_bot_token) == ("", "")

    @pytest.mark.asyncio
    async def test_redaction_placeholders_create_nothing(self, async_client, test_session, settings_file, manager):
        from notification_migration import migrate_legacy_notification_settings

        settings_file(
            discord_webhook_url=REDACTION_SENTINEL,
            telegram_bot_token=REDACTION_SENTINEL,
            telegram_chat_id=REDACTION_SENTINEL,
        )
        migrate_legacy_notification_settings()

        assert _rows(test_session) == []
        assert _reloaded_settings().discord_webhook_url == ""

    @pytest.mark.asyncio
    async def test_existing_identical_row_is_not_duplicated(self, async_client, test_session, settings_file, manager):
        """An operator who already moved the webhook into Alert Methods and
        switched it off does not get a fresh enabled copy."""
        from notification_migration import migrate_legacy_notification_settings

        method_id = await _discord(async_client, "Mine", WEBHOOK_A, enabled=False)
        settings_file(discord_webhook_url=WEBHOOK_A)

        result = migrate_legacy_notification_settings()

        rows = _rows(test_session, "discord")
        assert [row.id for row in rows] == [method_id]
        assert rows[0].enabled is False
        assert result["existing"] == [method_id]

    @pytest.mark.asyncio
    async def test_unwritable_settings_still_get_rows(self, async_client, test_session, settings_file, manager):
        from notification_migration import migrate_legacy_notification_settings

        settings_file(discord_webhook_url=WEBHOOK_A)
        migrate_legacy_notification_settings(allow_settings_write=False)

        assert len(_rows(test_session, "discord")) == 1
        assert _reloaded_settings().discord_webhook_url == WEBHOOK_A

    @pytest.mark.asyncio
    async def test_yaml_settings_restore_migrates_restored_fields(
        self, async_client, test_session, settings_file, manager, outbox
    ):
        from routers.backup import _restore_settings

        settings_file()
        _restore_settings({"discord_webhook_url": WEBHOOK_A})

        assert [json.loads(r.config)["webhook_url"] for r in _rows(test_session, "discord")] == [WEBHOOK_A]
        assert _reloaded_settings().discord_webhook_url == ""
        # The reloaded manager delivers to it straight away.
        await _dispatch(alert_category="task_results")
        await manager().flush_now()
        assert outbox.urls() == [WEBHOOK_A]

    @pytest.mark.asyncio
    async def test_dbas_ecm_settings_import_migrates_restored_fields(
        self, async_client, test_session, settings_file, manager
    ):
        from dbas.importers.ecm_settings import import_ecm_settings
        from dbas.restore_contracts import RestoreReport

        settings_file()
        await import_ecm_settings(
            archive_settings={"telegram_bot_token": BOT_TOKEN, "telegram_chat_id": CHAT_ID},
            selected=True,
            report=RestoreReport(is_dry_run=False),
            is_dry_run=False,
        )

        rows = _rows(test_session, "telegram")
        assert [json.loads(r.config) for r in rows] == [{"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}]
        on_disk = _reloaded_settings()
        assert (on_disk.telegram_bot_token, on_disk.telegram_chat_id) == ("", "")


# ---------------------------------------------------------------------------
# Consumers that read Discord / Telegram presence
# ---------------------------------------------------------------------------
class TestConsumersReadAlertMethods:
    @pytest.mark.asyncio
    async def test_settings_configured_flags_follow_enabled_rows(self, async_client, manager):
        before = (await async_client.get("/api/settings")).json()
        assert (before["discord_configured"], before["telegram_configured"]) == (False, False)

        await _create(
            async_client, name="TG", method_type="telegram",
            config={"bot_token": BOT_TOKEN, "chat_id": CHAT_ID},
        )
        await _discord(async_client, "A", WEBHOOK_A, enabled=False)

        after = (await async_client.get("/api/settings")).json()
        assert (after["discord_configured"], after["telegram_configured"]) == (False, True)
        assert "telegram_bot_token" not in after

    @pytest.mark.asyncio
    async def test_m3u_digest_uses_first_enabled_discord_destination(self, async_client, outbox, manager):
        from tasks.m3u_digest import M3UDigestTask

        await _discord(async_client, "Off", WEBHOOK_B, enabled=False)
        await _discord(async_client, "Canary", CANARY_WEBHOOK)
        await _discord(async_client, "Later", WEBHOOK_A)

        sent = await M3UDigestTask()._send_digest_discord(changes=[], discord_content=None, is_test=True)

        assert sent is True
        assert outbox.urls() == [CANARY_WEBHOOK]

    @pytest.mark.asyncio
    async def test_m3u_digest_refuses_a_stored_non_discord_url(self, async_client, test_session, outbox, manager):
        test_session.add(AlertMethod(
            name="Legacy", method_type="discord", enabled=True,
            config=json.dumps({"webhook_url": EVIL_WEBHOOK}),
        ))
        test_session.commit()
        from tasks.m3u_digest import M3UDigestTask

        sent = await M3UDigestTask()._send_digest_discord(changes=[], discord_content=None, is_test=True)

        assert sent is False
        assert outbox.posts == []


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------
class TestShutdownDeliversBatchedAlerts:
    @pytest.mark.asyncio
    async def test_shutdown_flushes_alerts_still_in_the_digest_window(self, async_client, outbox, manager):
        """Discord / Telegram are batched since #1026, so an alert queued just
        before a restart must be delivered by shutdown, not dropped."""
        import main

        await _discord(async_client, "A", WEBHOOK_A)
        await _dispatch(alert_category="task_results")
        assert outbox.posts == []  # still waiting in the 30 s window

        with patch("concurrency.shutdown_cpu_pool"), \
             patch("tls.https_server.stop_https_server"), \
             patch("services.version_check.stop_version_check_loop"), \
             patch("tls.renewal.renewal_manager"), \
             patch("task_engine.stop_engine"):
            await main.shutdown_event()

        assert outbox.urls() == [WEBHOOK_A]

    @pytest.mark.asyncio
    async def test_flush_is_a_no_op_before_the_manager_exists(self, monkeypatch):
        monkeypatch.setattr(alert_methods, "_manager", None)

        await alert_methods.flush_pending_alerts()

        assert alert_methods._manager is None


class TestTelegramHardening:
    @pytest.mark.asyncio
    async def test_connection_test_refuses_a_malformed_token(self, outbox):
        method = TelegramBotMethod(1, "TG", {"bot_token": "1/../../x", "chat_id": CHAT_ID})

        ok, message = await method.test_connection()

        assert ok is False
        assert message == "Invalid bot token format"
        assert outbox.posts == []

    @pytest.mark.asyncio
    async def test_failed_test_message_does_not_echo_the_chat_id(self, monkeypatch):
        method = TelegramBotMethod(1, "TG", {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID})

        async def _fail(_message):
            return False

        monkeypatch.setattr(method, "send", _fail)
        monkeypatch.setattr("alert_methods_discord.aiohttp.ClientSession", _Outbox())

        ok, message = await method.test_connection()

        assert ok is False
        assert CHAT_ID not in message

    @pytest.mark.asyncio
    async def test_huge_metadata_still_fits_one_message(self, outbox):
        method = TelegramBotMethod(1, "TG", {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID})
        message = alert_methods.AlertMessage(
            title="Task Completed",
            message="body " * 500,
            metadata={f"field_{i}": "v" * 400 for i in range(40)},
        )

        assert await method.send(message) is True

        assert _telegram_visible_length(outbox.posts[0][1]["text"]) <= 4096
        assert "body" in outbox.posts[0][1]["text"]


class TestDiscordRestoredUsername:
    @pytest.mark.asyncio
    async def test_redacted_username_from_a_backup_is_not_posted(self, outbox):
        method = DiscordWebhookMethod(1, "D", {"webhook_url": WEBHOOK_A, "username": REDACTION_SENTINEL})

        assert await method.send(alert_methods.AlertMessage(title="T", message="M")) is True

        assert "username" not in outbox.posts[0][1]


class TestAlertSourcesValidation:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("sources,detail", [
        ({"m3u_refresh": {"account_ids": 3}}, "m3u_refresh.account_ids must be an array"),
        ({"epg_refresh": {"source_ids": "1"}}, "epg_refresh.source_ids must be an array"),
        ({"probe_failures": {"min_failures": -1}}, "probe_failures.min_failures must be a non-negative integer"),
        ({"epg_refresh": []}, "epg_refresh must be an object"),
    ])
    async def test_bad_sections_are_refused_with_their_message(self, async_client, manager, sources, detail):
        response = await async_client.post("/api/alert-methods", json={
            "name": "A", "method_type": "discord", "config": {"webhook_url": WEBHOOK_A},
            "alert_sources": sources,
        })

        assert response.status_code == 400
        assert response.json()["detail"] == detail
