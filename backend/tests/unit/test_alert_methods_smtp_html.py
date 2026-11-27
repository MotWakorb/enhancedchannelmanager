"""
Unit tests for HTML escaping in the SMTP alert method's email bodies.

Alert titles, messages, sources and metadata carry channel, stream and group
names supplied by M3U providers, so they must render as text, never markup.
"""
import email
from datetime import datetime
from html.parser import HTMLParser
from unittest.mock import MagicMock, patch

import pytest

from alert_methods import AlertMessage

SCRIPT = "<script>alert(1)</script>"
SCRIPT_ESCAPED = "&lt;script&gt;alert(1)&lt;/script&gt;"
LINK = '<a href="https://evil">click</a>'
LINK_ESCAPED = "&lt;a href=&quot;https://evil&quot;&gt;click&lt;/a&gt;"
SPECIALS = "Tom & Jerry's \"Best\" <HD> > 1"
SPECIALS_ESCAPED = "Tom &amp; Jerry&#x27;s &quot;Best&quot; &lt;HD&gt; &gt; 1"

# Every tag the builders emit themselves. Anything else in the parsed output
# would have come from message content.
OWN_TAGS = {"html", "head", "style", "body", "div", "h2", "strong", "br"}


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


def _smtp_class():
    # Imported lazily: a module-level import registers SMTP at collection time,
    # before other suites clear the global method registry, and router tests
    # that rely on re-registration on first import would then lose it.
    from alert_methods_smtp import SMTPMethod
    return SMTPMethod


def _method():
    return _smtp_class()(1, "test-smtp", {"to_emails": ["ops@example.com"]})


def _message(title=SCRIPT, message=LINK, source=None, metadata=None, notification_type="error"):
    msg = AlertMessage(
        title=title,
        message=message,
        notification_type=notification_type,
        source=source,
        metadata=metadata,
    )
    msg.timestamp = datetime(2026, 1, 2, 3, 4, 5)
    return msg


class TestBuildHtmlMessageEscaping:
    def test_title_script_is_escaped(self):
        html = _method()._build_html_message(_message(title=SCRIPT))
        assert f"<h2>❌ {SCRIPT_ESCAPED}</h2>" in html
        assert SCRIPT not in html

    def test_title_link_is_escaped(self):
        html = _method()._build_html_message(_message(title=LINK))
        assert f"<h2>❌ {LINK_ESCAPED}</h2>" in html
        assert "<a " not in html

    def test_message_script_and_link_are_escaped(self):
        html = _method()._build_html_message(_message(message=f"{SCRIPT} {LINK}"))
        assert f'<div class="message">{SCRIPT_ESCAPED} {LINK_ESCAPED}</div>' in html
        assert SCRIPT not in html
        assert "<a " not in html

    def test_special_characters_exact_output(self):
        html = _method()._build_html_message(
            _message(title=SPECIALS, message=SPECIALS, metadata={"channel": SPECIALS})
        )
        assert f"<h2>❌ {SPECIALS_ESCAPED}</h2>" in html
        assert f'<div class="message">{SPECIALS_ESCAPED}</div>' in html
        assert (
            f'<div class="metadata-item"><strong>Channel:</strong> {SPECIALS_ESCAPED}</div>'
            in html
        )

    def test_message_newlines_become_markup_br_after_escaping(self):
        html = _method()._build_html_message(_message(message="a < b\n<br>\nc & d"))
        assert '<div class="message">a &lt; b<br>&lt;br&gt;<br>c &amp; d</div>' in html

    def test_metadata_value_script_and_link_are_escaped(self):
        html = _method()._build_html_message(
            _message(title="t", message="m", metadata={"stream_name": SCRIPT, "url": LINK})
        )
        assert (
            f'<div class="metadata-item"><strong>Stream Name:</strong> {SCRIPT_ESCAPED}</div>'
            in html
        )
        assert f'<div class="metadata-item"><strong>Url:</strong> {LINK_ESCAPED}</div>' in html
        assert SCRIPT not in html
        assert "<a " not in html

    def test_metadata_key_script_and_link_are_escaped(self):
        html = _method()._build_html_message(
            _message(title="t", message="m", metadata={SCRIPT: "v1", LINK: "v2"})
        )
        # Keys are title-cased for display, then escaped, so entities stay intact.
        assert (
            '<div class="metadata-item"><strong>&lt;Script&gt;Alert(1)&lt;/Script&gt;:</strong> v1</div>'
            in html
        )
        assert (
            '<div class="metadata-item"><strong>&lt;A Href=&quot;Https://Evil&quot;&gt;Click&lt;/A&gt;:</strong> v2</div>'
            in html
        )
        assert "<script" not in html.lower()
        assert "<a " not in html.lower()

    def test_non_string_metadata_values_render_as_text(self):
        html = _method()._build_html_message(
            _message(title="t", message="m", metadata={"count": 5, "ratio": 0.5, "ok": True})
        )
        assert '<div class="metadata-item"><strong>Count:</strong> 5</div>' in html
        assert '<div class="metadata-item"><strong>Ratio:</strong> 0.5</div>' in html
        assert '<div class="metadata-item"><strong>Ok:</strong> True</div>' in html

    def test_source_is_escaped(self):
        html = _method()._build_html_message(_message(title="t", message="m", source=LINK))
        assert f"Sent from Enhanced Channel Manager - {LINK_ESCAPED}<br>" in html
        assert "<a " not in html

    def test_hostile_content_everywhere_yields_only_own_markup(self):
        msg = _message(
            title=f"{SCRIPT}{LINK}",
            message=f"{SCRIPT}\n{LINK}",
            source=f"{SCRIPT}{LINK}",
            metadata={f"{SCRIPT}{LINK}": f"{SCRIPT}{LINK}"},
        )
        html = _method()._build_html_message(msg)
        parsed = _parse(html)
        tags = {tag for tag, _ in parsed.tags}
        assert tags <= OWN_TAGS
        assert "script" not in tags and "a" not in tags
        # The hostile strings survive as visible text.
        text = "".join(parsed.text)
        assert SCRIPT in text
        assert LINK in text

    def test_own_markup_is_still_markup(self):
        msg = _message(title="Title", message="line1\nline2", source="src", metadata={"k": "v"})
        html = _method()._build_html_message(msg)
        parsed = _parse(html)
        tags = [tag for tag, _ in parsed.tags]
        for tag in ("html", "head", "style", "body", "h2", "strong", "br"):
            assert tag in tags
        classes = {attrs.get("class") for tag, attrs in parsed.tags if tag == "div"}
        assert {"container", "header", "body", "message", "metadata", "metadata-item", "footer"} <= classes
        assert '<div class="message">line1<br>line2</div>' in html
        assert "<h2>❌ Title</h2>" in html
        assert "&lt;" not in html
        assert "&quot;" not in html


class TestBuildHtmlDigestEscaping:
    def _digest(self, messages):
        counts = {"success": 0, "error": 0, "warning": 0, "info": 0}
        for m in messages:
            counts[m.notification_type] = counts.get(m.notification_type, 0) + 1
        return _method()._build_html_digest(messages, counts)

    def test_title_and_message_are_escaped(self):
        html = self._digest([_message(title=SCRIPT, message=LINK), _message(title=LINK, message=SPECIALS)])
        assert f'<div class="alert-title">❌ {SCRIPT_ESCAPED}</div>' in html
        assert f'<div class="alert-message">{LINK_ESCAPED}</div>' in html
        assert f'<div class="alert-title">❌ {LINK_ESCAPED}</div>' in html
        assert f'<div class="alert-message">{SPECIALS_ESCAPED}</div>' in html
        assert SCRIPT not in html
        assert "<a " not in html

    def test_notification_type_cannot_break_out_of_class_attribute(self):
        hostile_type = 'x" onclick="evil()'
        html = self._digest([_message(notification_type=hostile_type), _message()])
        assert '<div class="alert-item alert-x&quot; onclick=&quot;evil()">' in html
        parsed = _parse(html)
        assert all("onclick" not in attrs for _, attrs in parsed.tags)

    def test_hostile_content_yields_only_own_markup(self):
        html = self._digest([_message(title=f"{SCRIPT}{LINK}", message=f"{SCRIPT}{LINK}")] * 2)
        parsed = _parse(html)
        tags = {tag for tag, _ in parsed.tags}
        assert tags <= OWN_TAGS
        text = "".join(parsed.text)
        assert SCRIPT in text
        assert LINK in text

    def test_own_markup_is_still_markup(self):
        html = self._digest([_message(title="One", message="m1"), _message(title="Two", message="m2")])
        parsed = _parse(html)
        classes = {attrs.get("class") for tag, attrs in parsed.tags if tag == "div"}
        assert {"container", "header", "summary", "body", "alert-item alert-error",
                "alert-title", "alert-message", "footer"} <= classes
        assert "<h2>ECM Notification Digest</h2>" in html
        assert '<div class="alert-title">❌ One</div>' in html
        assert "&lt;" not in html


class TestPlainTextUnaffected:
    def test_plain_message_keeps_raw_text(self):
        msg = _message(
            title=SPECIALS,
            message=f"{SCRIPT}\n{LINK}",
            source=LINK,
            metadata={"channel": SPECIALS, SCRIPT: LINK},
        )
        plain = _method()._build_plain_message(msg)
        assert f"[ERROR] {SPECIALS}" in plain
        assert f"{SCRIPT}\n{LINK}" in plain
        assert f"Channel: {SPECIALS}" in plain
        assert f"Sent from Enhanced Channel Manager - {LINK}" in plain
        assert "&amp;" not in plain
        assert "&lt;" not in plain
        assert "&quot;" not in plain

    def test_plain_digest_keeps_raw_text(self):
        counts = {"success": 0, "error": 2, "warning": 0, "info": 0}
        plain = _method()._build_plain_digest(
            [_message(title=SPECIALS, message=SCRIPT), _message(title=LINK, message=SPECIALS)], counts
        )
        assert f"[ERROR] {SPECIALS}" in plain
        assert f"  {SCRIPT}" in plain
        assert f"[ERROR] {LINK}" in plain
        assert "&amp;" not in plain
        assert "&lt;" not in plain


class TestSendMimeParts:
    """The assembled message: escaped HTML part, raw plain part, raw subject."""

    @pytest.mark.asyncio
    async def test_send_attaches_escaped_html_and_raw_plain(self):
        method = _method()
        server = MagicMock()
        smtp_config = {
            "smtp_host": "smtp.invalid",
            "smtp_port": 25,
            "smtp_user": "",
            "smtp_password": "",
            "from_email": "ecm@example.com",
            "from_name": "ECM",
            "use_tls": False,
            "use_ssl": False,
        }
        with patch.object(_smtp_class(), "_get_smtp_config", return_value=smtp_config), \
             patch("alert_methods_smtp.smtplib.SMTP", return_value=server):
            ok = await method.send(_message(title=SPECIALS, message=f"{SCRIPT} {LINK}"))

        assert ok is True
        server.sendmail.assert_called_once()
        raw = server.sendmail.call_args.args[2]
        parsed = email.message_from_string(raw)
        assert parsed["Subject"] == f"[ERROR] {SPECIALS}"
        parts = {p.get_content_type(): p.get_payload(decode=True).decode("utf-8")
                 for p in parsed.walk() if not p.is_multipart()}
        assert f"<h2>❌ {SPECIALS_ESCAPED}</h2>" in parts["text/html"]
        assert f'<div class="message">{SCRIPT_ESCAPED} {LINK_ESCAPED}</div>' in parts["text/html"]
        assert f"[ERROR] {SPECIALS}" in parts["text/plain"]
        assert f"{SCRIPT} {LINK}" in parts["text/plain"]
        assert "&amp;" not in parts["text/plain"]
