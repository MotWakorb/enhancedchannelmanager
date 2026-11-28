"""
Discord webhook safety helpers.

Third-party values (M3U group and stream names, upstream text) are placed
inside ECM's own Discord markdown. Two independent layers keep them inert:

- ``no_mentions()`` goes on every webhook payload as ``allowed_mentions``, so
  Discord pings nobody whatever the content says.
- ``escape_markdown()`` is applied to each third-party value before it is
  interpolated, so it cannot add formatting, masked or auto-linked URLs, or
  mention syntax that renders as a highlighted mention.
"""
import re

# Backslash-escaped. "<" covers <@id>, <@&id>, <#id>, <t:..> and <url> forms.
_MARKDOWN_SPECIAL_RE = re.compile(r"([\\*_~`|>#\-\[\]()<])")
# Breaking "scheme:" stops Discord auto-linking a bare http(s) URL.
_URL_SCHEME_RE = re.compile(r"\b(https?):(?=//)", re.IGNORECASE)
# A value must not start new lines (headings, quotes, fake bullets).
_LINE_BREAK_RE = re.compile(r"[\r\n]+")

# Zero-width space after "@" breaks @everyone, @here and <@id> as Discord
# parses them, the same approach discord.py's escape_mentions uses.
_ZWSP = "\u200b"


def escape_markdown(value) -> str:
    """Render a third-party value as inert Discord text.

    Args:
        value: The value to escape. Non-strings are converted with ``str()``.

    Returns:
        The escaped text, which Discord displays as the original characters
        (bar collapsed line breaks) with no formatting, links or mentions.
    """
    text = _LINE_BREAK_RE.sub(" ", str(value))
    text = _MARKDOWN_SPECIAL_RE.sub(r"\\\1", text)
    text = _URL_SCHEME_RE.sub(r"\1\\:", text)
    return text.replace("@", "@" + _ZWSP)


def no_mentions() -> dict:
    """Return a fresh ``allowed_mentions`` value that permits no pings.

    Returns:
        ``{"parse": []}``, a new dict on every call so callers can't share
        mutable state.
    """
    return {"parse": []}
