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
from typing import Optional

# Backslash-escaped. "<" covers <@id>, <@&id>, <#id>, <t:..> and <url> forms.
_MARKDOWN_SPECIAL_RE = re.compile(r"([\\*_~`|>#\-\[\]()<])")
# Breaking "scheme:" stops Discord auto-linking an http(s) URL. No word
# boundary: a scheme glued to a word character ("xhttp://") still links.
_URL_SCHEME_RE = re.compile(r"(https?):(?=//)", re.IGNORECASE)
# A value must not start new lines (headings, quotes, fake bullets). Covers
# the Unicode line and paragraph separators and NEL as well as CR and LF.
_LINE_BREAK_RE = re.compile("[\r\n\u2028\u2029\x85]+")

# Zero-width space after "@" breaks @everyone, @here and <@id> as Discord
# parses them, the same approach discord.py's escape_mentions uses.
_ZWSP = "\u200b"
_ELLIPSIS = "\u2026"


def escape_markdown(value, max_len: Optional[int] = None) -> str:
    """Render a third-party value as inert Discord text.

    Args:
        value: The value to escape. Non-strings are converted with ``str()``.
        max_len: If set, a longer value is cut to ``max_len`` characters
            (the last one an ellipsis) before escaping, so no escape sequence
            is split. Escaping at most doubles a character, so the result is
            at most ``2 * max_len`` long.

    Returns:
        The escaped text, which Discord displays as the original characters
        (bar collapsed line breaks and truncation) with no formatting, links
        or mentions.
    """
    text = _LINE_BREAK_RE.sub(" ", str(value))
    if max_len is not None and len(text) > max_len:
        text = text[:max_len - 1] + _ELLIPSIS
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
