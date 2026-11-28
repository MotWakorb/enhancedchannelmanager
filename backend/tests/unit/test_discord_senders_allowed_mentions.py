"""
Every Discord webhook sender that can carry third-party text must send
``allowed_mentions: {"parse": []}``.

Alert bodies interpolate provider-controlled values (stream names, group
names, M3U account names, upstream error text). Without ``allowed_mentions``
a value such as "@everyone" in message ``content`` pings the whole channel.
These tests mock the HTTP boundary and assert on the payload dict ECM posts.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alert_methods import AlertMessage
from alert_methods_discord import DiscordWebhookMethod

FAKE_WEBHOOK = "https://discord.com/api/webhooks/" + "0000/" + "x" * 12
HOSTILE = "@everyone <@123> <@&456> [x](http://evil)"


def _mock_session(status=204):
    response = AsyncMock()
    response.status = status
    response.text = AsyncMock(return_value="")
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = ctx
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestDiscordAlertMethod:
    @pytest.mark.asyncio
    async def test_send_payload_suppresses_mentions(self):
        method = DiscordWebhookMethod(1, "ops", {"webhook_url": FAKE_WEBHOOK})
        session = _mock_session()
        with patch("alert_methods_discord.aiohttp.ClientSession", return_value=session):
            ok = await method.send(AlertMessage(
                title=f"Probe failed: {HOSTILE}",
                message=f"Stream {HOSTILE} is down",
                notification_type="error",
                source="stream_prober",
            ))

        assert ok is True
        session.post.assert_called_once()
        assert session.post.call_args.args[0] == FAKE_WEBHOOK
        payload = session.post.call_args.kwargs["json"]
        assert payload["allowed_mentions"] == {"parse": []}
        assert payload["embeds"][0]["description"] == f"Stream {HOSTILE} is down"


class TestLegacyNotificationDiscord:
    @pytest.mark.asyncio
    async def test_dispatch_payload_suppresses_mentions(self):
        settings = MagicMock()
        settings.is_discord_configured.return_value = True
        settings.is_telegram_configured.return_value = False
        settings.is_smtp_configured.return_value = False
        settings.discord_webhook_url = FAKE_WEBHOOK
        session = _mock_session()

        with patch("services.notification_service.get_settings", return_value=settings), \
             patch("services.notification_service.get_session", side_effect=RuntimeError("no db")), \
             patch("aiohttp.ClientSession", return_value=session):
            from services.notification_service import _dispatch_to_alert_channels

            results = await _dispatch_to_alert_channels(
                title="M3U refresh failed",
                message=f"Account {HOSTILE} returned an error",
                notification_type="error",
                source="test",
                metadata=None,
            )

        assert results["discord"] is True
        session.post.assert_called_once()
        assert session.post.call_args.args[0] == FAKE_WEBHOOK
        payload = session.post.call_args.kwargs["json"]
        assert payload["allowed_mentions"] == {"parse": []}
        assert f"Account {HOSTILE} returned an error" in payload["content"]
