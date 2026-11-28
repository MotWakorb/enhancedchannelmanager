"""
Unit tests for Discord escaping and mention suppression in the M3U digest.

Group names and stream names come from M3U providers. A group named
"@everyone" must not ping the channel, and a name carrying markdown or a link
must render as text. Two layers: the webhook payload sends
``allowed_mentions: {"parse": []}``, and every provider value is escaped.
"""
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from m3u_digest_template import M3UDigestTemplate
from models import M3UChangeLog
from tasks.m3u_digest import M3UDigestTask, _FilteredChange

ZWSP = "\u200b"

# Hostile provider value -> exact escaped form in the Discord message.
EVERYONE = "@everyone"
EVERYONE_ESC = f"@{ZWSP}everyone"
HERE = "@here"
HERE_ESC = f"@{ZWSP}here"
USER = "<@123>"
USER_ESC = f"\\<@{ZWSP}123\\>"
ROLE = "<@&456>"
ROLE_ESC = f"\\<@{ZWSP}&456\\>"
LINK = "[x](http://evil)"
LINK_ESC = "\\[x\\]\\(http\\://evil\\)"
BOLD = "**bold**"
BOLD_ESC = "\\*\\*bold\\*\\*"
URL = "http://evil.example"
URL_ESC = "http\\://evil.example"

PAYLOADS = [
    (EVERYONE, EVERYONE_ESC),
    (HERE, HERE_ESC),
    (USER, USER_ESC),
    (ROLE, ROLE_ESC),
    (LINK, LINK_ESC),
    (BOLD, BOLD_ESC),
    (URL, URL_ESC),
]

SINCE = datetime(2026, 1, 2, 3, 4, 5)
FAKE_WEBHOOK = "https://discord.com/api/webhooks/" + "0000/" + "x" * 12


def _change(change_type, group_name, stream_names=None, count=3, account_id=7):
    change = M3UChangeLog(
        m3u_account_id=account_id,
        change_type=change_type,
        group_name=group_name,
        count=count,
        enabled=False,
    )
    if stream_names is not None:
        change.set_stream_names(stream_names)
    return change


def _render(changes):
    chunks = M3UDigestTemplate().render_discord(changes, SINCE, show_detailed_list=True)
    return "".join(chunks)


def _assert_no_raw(text, raw_values):
    for raw in raw_values:
        assert raw not in text, f"raw provider value {raw!r} reached Discord"


class TestGroupNameEscaping:
    @pytest.mark.parametrize("change_type", ["group_added", "group_removed"])
    @pytest.mark.parametrize("raw,escaped", PAYLOADS)
    def test_group_change_name_is_escaped(self, change_type, raw, escaped):
        text = _render([_change(change_type, raw)])
        assert f"• **{escaped}** (3 streams)\n" in text
        _assert_no_raw(text, [raw])

    @pytest.mark.parametrize("raw,escaped", PAYLOADS)
    def test_stream_change_with_names_escapes_group(self, raw, escaped):
        text = _render([_change("streams_added", raw, stream_names=["A"], count=1)])
        assert f"• **{escaped}**: A\n" in text
        _assert_no_raw(text, [raw])

    @pytest.mark.parametrize("raw,escaped", PAYLOADS)
    def test_stream_change_count_only_escapes_group(self, raw, escaped):
        names = [f"s{i}" for i in range(6)]  # over 5 names renders the count only
        text = _render([_change("streams_removed", raw, stream_names=names, count=6)])
        assert f"• **{escaped}**: 6 streams\n" in text
        _assert_no_raw(text, [raw])


class TestStreamNameEscaping:
    @pytest.mark.parametrize("raw,escaped", PAYLOADS)
    def test_stream_name_is_escaped(self, raw, escaped):
        text = _render([_change("streams_added", "Sports", stream_names=[raw], count=1)])
        assert f"• **Sports**: {escaped}\n" in text
        _assert_no_raw(text, [raw])

    def test_each_name_escaped_before_join(self):
        names = [EVERYONE, USER, ROLE, LINK, BOLD]
        text = _render([_change("streams_removed", "G", stream_names=names, count=5)])
        assert (
            f"• **G**: {EVERYONE_ESC}, {USER_ESC}, {ROLE_ESC}, {LINK_ESC}, {BOLD_ESC}\n"
        ) in text
        _assert_no_raw(text, names)

    def test_filtered_change_proxy_names_are_escaped(self):
        # Exclude filtering wraps a row in a proxy that substitutes the names.
        original = _change("streams_added", EVERYONE, stream_names=[HERE, "dropped"])
        text = _render([_FilteredChange(original, [URL])])
        assert f"• **{EVERYONE_ESC}**: {URL_ESC}\n" in text
        _assert_no_raw(text, [EVERYONE, HERE, URL])


class TestOverflowPreview:
    def test_overflow_suffix_unchanged_with_hostile_names(self):
        hostile = [EVERYONE, HERE, USER, ROLE, LINK, BOLD, URL]
        changes = [
            _change("group_added", hostile[i % len(hostile)], account_id=7)
            for i in range(18)
        ]
        text = _render(changes)
        assert "• _...and 3 more_\n" in text
        for raw, escaped in PAYLOADS:
            assert f"• **{escaped}** (3 streams)\n" in text
        _assert_no_raw(text, hostile)


class TestTemplateMarkdownUnchanged:
    def test_benign_rows_render_exact_markdown(self):
        text = _render([
            _change("group_added", "News", count=4),
            _change("streams_added", "Sports", stream_names=["A", "B"], count=2),
        ])
        assert text.startswith("**M3U Playlist Changes**\nSince: 2026-01-02 03:04 UTC\n\n")
        assert "\n**M3U Account #7**\n" in text
        assert "\n🟢 __Groups Added__\n• **News** (4 streams)\n" in text
        assert "\n🟢 __Streams Added__\n• **Sports**: A, B\n" in text

    def test_summary_only_mode_unchanged(self):
        chunks = M3UDigestTemplate().render_discord(
            [_change("group_added", EVERYONE)], SINCE, show_detailed_list=False
        )
        assert len(chunks) == 1
        assert chunks[0].endswith("\n_Detailed list disabled in settings._")
        assert EVERYONE not in chunks[0]


def _mock_session(statuses):
    """aiohttp.ClientSession stand-in; each post returns the next status."""
    responses = []
    for status in statuses:
        response = AsyncMock()
        response.status = status
        response.headers = {"Retry-After": "0"}
        response.text = AsyncMock(return_value="")
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=response)
        ctx.__aexit__ = AsyncMock(return_value=False)
        responses.append(ctx)
    session = MagicMock()
    session.post.side_effect = responses
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


def _settings():
    settings = MagicMock()
    settings.is_discord_configured.return_value = True
    settings.discord_webhook_url = FAKE_WEBHOOK
    return settings


async def _send(discord_content, statuses, is_test=False):
    session = _mock_session(statuses)
    with patch("config.get_settings", return_value=_settings()), \
         patch("aiohttp.ClientSession", return_value=session), \
         patch("asyncio.sleep", new=AsyncMock()):
        ok = await M3UDigestTask()._send_digest_discord(
            [], discord_content=discord_content, is_test=is_test
        )
    return ok, [c.kwargs["json"] for c in session.post.call_args_list]


class TestWebhookPayload:
    @pytest.mark.asyncio
    async def test_digest_payload_suppresses_mentions_and_carries_escaped_text(self):
        changes = [
            _change("group_added", EVERYONE),
            _change("group_removed", ROLE),
            _change("streams_added", HERE, stream_names=[USER, LINK, BOLD, URL], count=4),
        ]
        content = M3UDigestTemplate().render_discord(changes, SINCE)

        ok, payloads = await _send(content, [204] * len(content))

        assert ok is True
        assert len(payloads) == len(content) == 1
        payload = payloads[0]
        assert payload["allowed_mentions"] == {"parse": []}
        assert payload["username"] == "ECM M3U Digest"
        body = payload["content"]
        assert f"• **{EVERYONE_ESC}** (3 streams)\n" in body
        assert f"• **{ROLE_ESC}** (3 streams)\n" in body
        assert f"• **{HERE_ESC}**: {USER_ESC}, {LINK_ESC}, {BOLD_ESC}, {URL_ESC}\n" in body
        _assert_no_raw(body, [EVERYONE, HERE, USER, ROLE, LINK, BOLD, URL])

    @pytest.mark.asyncio
    async def test_every_chunk_suppresses_mentions(self):
        ok, payloads = await _send(["first", "second", "third"], [204, 204, 204])
        assert ok is True
        assert [p["content"] for p in payloads] == ["first", "second", "third"]
        assert [p["allowed_mentions"] for p in payloads] == [{"parse": []}] * 3

    @pytest.mark.asyncio
    async def test_rate_limit_retry_suppresses_mentions(self):
        ok, payloads = await _send(["only"], [429, 204])
        assert ok is True
        assert len(payloads) == 2
        assert [p["allowed_mentions"] for p in payloads] == [{"parse": []}] * 2

    @pytest.mark.asyncio
    async def test_test_message_suppresses_mentions(self):
        ok, payloads = await _send(None, [204], is_test=True)
        assert ok is True
        assert len(payloads) == 1
        assert payloads[0]["allowed_mentions"] == {"parse": []}
        assert payloads[0]["content"].startswith("**✓ M3U Digest Test Successful**")
