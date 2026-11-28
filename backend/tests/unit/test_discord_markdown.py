"""
Unit tests for the Discord markdown escaping helper.

Third-party values (M3U group and stream names) are rendered inside ECM's own
Discord markdown. The helper must leave them as inert text: no formatting, no
masked or auto-linked URLs, no mention syntax.
"""
import pytest

from discord_markdown import escape_markdown, no_mentions

ZWSP = "\u200b"

# Hostile value -> exact escaped form.
CASES = [
    ("@everyone", f"@{ZWSP}everyone"),
    ("@here", f"@{ZWSP}here"),
    ("<@123>", f"\\<@{ZWSP}123\\>"),
    ("<@!123>", f"\\<@{ZWSP}!123\\>"),
    ("<@&456>", f"\\<@{ZWSP}&456\\>"),
    ("<#789>", "\\<\\#789\\>"),
    ("[x](http://evil)", "\\[x\\]\\(http\\://evil\\)"),
    ("**bold**", "\\*\\*bold\\*\\*"),
    ("http://evil.example", "http\\://evil.example"),
    ("HTTPS://evil.example/a_b", "HTTPS\\://evil.example/a\\_b"),
    ("__u__ ~~s~~ ||spoiler|| `code`", "\\_\\_u\\_\\_ \\~\\~s\\~\\~ \\|\\|spoiler\\|\\| \\`code\\`"),
    ("> quote", "\\> quote"),
    ("# Heading", "\\# Heading"),
    ("- item", "\\- item"),
    ("back\\slash", "back\\\\slash"),
]


class TestEscapeMarkdown:
    @pytest.mark.parametrize("raw,expected", CASES)
    def test_hostile_value_is_escaped_exactly(self, raw, expected):
        assert escape_markdown(raw) == expected

    @pytest.mark.parametrize("raw", ["@everyone", "@here"])
    def test_broadcast_mention_text_is_broken(self, raw):
        assert raw not in escape_markdown(raw)

    @pytest.mark.parametrize("raw", ["<@123>", "<@&456>", "<#789>"])
    def test_id_mention_text_is_broken(self, raw):
        assert raw not in escape_markdown(raw)

    def test_line_breaks_collapse_so_a_name_cannot_start_new_lines(self):
        assert escape_markdown("a\r\n# b\nc") == "a \\# b c"

    def test_plain_name_is_unchanged(self):
        assert escape_markdown("ESPN HD 4K: US/East") == "ESPN HD 4K: US/East"

    def test_non_string_value_is_stringified(self):
        assert escape_markdown(42) == "42"


class TestNoMentions:
    def test_parses_nothing(self):
        assert no_mentions() == {"parse": []}

    def test_returns_a_fresh_dict_each_call(self):
        first = no_mentions()
        first["parse"].append("everyone")
        assert no_mentions() == {"parse": []}
