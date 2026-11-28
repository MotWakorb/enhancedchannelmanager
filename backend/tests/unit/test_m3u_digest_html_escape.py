"""
Unit tests for HTML escaping in the M3U digest email.

Group names and stream names come from M3U providers, so the digest HTML must
render them as text, never as markup.
"""
import email
from datetime import datetime
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

import pytest

from m3u_digest_template import M3UDigestTemplate
from models import M3UChangeLog
from tasks.m3u_digest import _FilteredChange

IMG = "<img src=x onerror=alert(1)>"
IMG_ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
BREAKOUT = '"><a href="https://evil.example">click</a>'
BREAKOUT_ESCAPED = "&quot;&gt;&lt;a href=&quot;https://evil.example&quot;&gt;click&lt;/a&gt;"
AMP = "Tom & Jerry's"
AMP_ESCAPED = "Tom &amp; Jerry&#x27;s"

SINCE = datetime(2026, 1, 2, 3, 4, 5)

# Every tag the template emits itself. Anything else in the parsed output
# would have come from provider-supplied content.
OWN_TAGS = {
    "html", "head", "meta", "style", "body", "div", "h1", "p", "span",
    "ul", "li", "strong", "em", "br",
}


class _TagCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


def _parse(markup):
    parser = _TagCollector()
    parser.feed(markup)
    parser.close()
    return parser


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
    return M3UDigestTemplate().render_html(changes, SINCE, show_detailed_list=True)


def _assert_no_foreign_markup(html):
    parsed = _parse(html)
    foreign = [tag for tag, _ in parsed.tags if tag not in OWN_TAGS]
    assert foreign == [], f"provider content produced markup: {foreign}"
    for tag, attrs in parsed.tags:
        assert "onerror" not in attrs, f"<{tag}> gained an onerror attribute"
        assert "href" not in attrs, f"<{tag}> gained an href attribute"
        assert "src" not in attrs, f"<{tag}> gained a src attribute"


class TestGroupNameEscaping:
    @pytest.mark.parametrize("change_type", ["group_added", "group_removed"])
    def test_group_change_img_payload_is_escaped(self, change_type):
        html = _render([_change(change_type, IMG)])
        assert IMG not in html
        assert (
            f'<li><strong>{IMG_ESCAPED}</strong> '
            f'<span class="stream-count">(3 streams)</span></li>'
        ) in html
        _assert_no_foreign_markup(html)

    @pytest.mark.parametrize("change_type", ["group_added", "group_removed"])
    def test_group_change_attribute_breakout_is_escaped(self, change_type):
        html = _render([_change(change_type, BREAKOUT)])
        assert BREAKOUT not in html
        assert "<a " not in html
        assert f"<li><strong>{BREAKOUT_ESCAPED}</strong> " in html
        _assert_no_foreign_markup(html)

    def test_group_change_ampersand_is_escaped(self):
        html = _render([_change("group_added", AMP)])
        assert f"<li><strong>{AMP_ESCAPED}</strong> " in html
        assert "Tom & Jerry" not in html

    def test_stream_change_without_names_escapes_group(self):
        html = _render([_change("streams_added", IMG, stream_names=[])])
        assert IMG not in html
        assert f"<li><strong>{IMG_ESCAPED}</strong>: 3 streams</li>" in html
        _assert_no_foreign_markup(html)

    def test_stream_change_with_names_escapes_group(self):
        html = _render([_change("streams_removed", BREAKOUT, stream_names=["ok"])])
        assert BREAKOUT not in html
        assert f"<li><strong>{BREAKOUT_ESCAPED}</strong>: 3 streams " in html
        _assert_no_foreign_markup(html)

    def test_group_text_survives_round_trip(self):
        html = _render([_change("group_added", f"{IMG} {AMP}")])
        assert f"{IMG} {AMP}" in "".join(_parse(html).text)


class TestStreamNameEscaping:
    def test_stream_name_img_payload_is_escaped(self):
        html = _render([_change("streams_added", "Sports", stream_names=[IMG])])
        assert IMG not in html
        assert f'<span class="stream-count">({IMG_ESCAPED})</span>' in html
        _assert_no_foreign_markup(html)

    def test_stream_name_attribute_breakout_is_escaped(self):
        html = _render([_change("streams_removed", "Sports", stream_names=[BREAKOUT])])
        assert BREAKOUT not in html
        assert "<a " not in html
        assert f'<span class="stream-count">({BREAKOUT_ESCAPED})</span>' in html
        _assert_no_foreign_markup(html)

    def test_stream_name_ampersand_is_escaped(self):
        html = _render([_change("streams_added", "Sports", stream_names=[AMP, "B&B"])])
        assert f'<span class="stream-count">({AMP_ESCAPED}, B&amp;B)</span>' in html
        assert "B&B" not in html

    def test_preview_with_overflow_suffix_escapes_each_name(self):
        names = [IMG, BREAKOUT, AMP, "fourth", "fifth"]
        html = _render([_change("streams_added", "G", stream_names=names, count=5)])
        assert (
            f'<span class="stream-count">'
            f'({IMG_ESCAPED}, {BREAKOUT_ESCAPED}, {AMP_ESCAPED} and 2 more)</span>'
        ) in html
        _assert_no_foreign_markup(html)

    def test_filtered_change_proxy_names_are_escaped(self):
        # Exclude filtering wraps a row in a proxy that substitutes the names.
        original = _change("streams_added", IMG, stream_names=[IMG, "dropped"])
        html = _render([_FilteredChange(original, [BREAKOUT])])
        assert IMG not in html
        assert BREAKOUT not in html
        assert (
            f'<li><strong>{IMG_ESCAPED}</strong>: 1 streams '
            f'<span class="stream-count">({BREAKOUT_ESCAPED})</span></li>'
        ) in html
        _assert_no_foreign_markup(html)


class TestChangeTypeEscaping:
    def test_unknown_change_type_is_escaped_in_class_and_label(self):
        html = _render([_change(BREAKOUT, "G")])
        assert BREAKOUT not in html
        assert (
            f'<span class="change-type {BREAKOUT_ESCAPED}">{BREAKOUT_ESCAPED}</span>'
        ) in html
        _assert_no_foreign_markup(html)


class TestTemplateMarkupUnchanged:
    def test_benign_rows_render_exact_markup(self):
        html = _render([
            _change("group_added", "News", count=4),
            _change("streams_added", "Sports", stream_names=["A", "B"], count=2),
        ])
        assert '<div class="account-header">M3U Account #7</div>' in html
        assert '<span class="change-type group_added">Groups Added</span>' in html
        assert (
            '<li><strong>News</strong> <span class="stream-count">(4 streams)</span></li>'
        ) in html
        assert (
            '<li><strong>Sports</strong>: 2 streams '
            '<span class="stream-count">(A, B)</span></li>'
        ) in html


class TestSubjectAndPlainText:
    def test_subject_carries_no_provider_content(self):
        subject = M3UDigestTemplate().get_subject([_change("group_added", f"x\r\nBcc: a@b {IMG}")])
        assert subject == "[ECM] M3U Digest: 1 change detected"

    def test_plain_text_keeps_names_verbatim(self):
        plain = M3UDigestTemplate().render_plain([_change("group_added", AMP)], SINCE)
        assert f"  * {AMP} (3 streams)" in plain


class TestSentEmail:
    def test_sent_html_part_is_escaped(self):
        from tasks.m3u_digest import M3UDigestTask

        changes = [
            _change("group_added", IMG),
            _change("streams_added", AMP, stream_names=[BREAKOUT]),
        ]
        template = M3UDigestTemplate()
        server = MagicMock()
        with patch("smtplib.SMTP", return_value=server):
            ok = M3UDigestTask()._send_custom_email_with_config_sync(
                {"smtp_host": "smtp.example.com", "smtp_port": 25,
                 "from_email": "ecm@example.com", "use_tls": False},
                ["ops@example.com"],
                template.get_subject(changes),
                template.render_html(changes, SINCE),
                template.render_plain(changes, SINCE),
            )
        assert ok is True
        raw = server.sendmail.call_args.args[2]
        msg = email.message_from_string(raw)
        html_parts = [
            p.get_payload(decode=True).decode(p.get_content_charset() or "utf-8")
            for p in msg.walk()
            if p.get_content_type() == "text/html"
        ]
        assert len(html_parts) == 1
        html = html_parts[0]
        for raw_value in (IMG, BREAKOUT, AMP):
            assert raw_value not in html
        assert f"<strong>{IMG_ESCAPED}</strong>" in html
        assert f"<strong>{AMP_ESCAPED}</strong>" in html
        assert f"({BREAKOUT_ESCAPED})" in html
        _assert_no_foreign_markup(html)
