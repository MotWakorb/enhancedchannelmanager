"""
URL and text obfuscation utilities for debug bundles.

Redacts hostnames, IP addresses, and credentials from URLs and free text
so diagnostic data can be shared safely.

THE PROPERTY THIS MODULE OWES ITS CALLERS (bead …-d0hoc):

    No provider credential VALUE survives, at any path shape, in any position.

That is deliberately not "``/live/`` URLs are scrubbed". Until 2026-08-23 this
module's only credential rule was a single regex anchored on a THREE-segment
XtreamCodes path (``/user/pass/id.ext``). Dispatcharr renders FOUR
(``/live/user/pass/id.ts``), plus ``/movie/``, ``/series/`` and
``/server1/live/...``, so the pattern never matched and the credential branch
never ran — while the hostname WAS replaced with ``example.com``, which is why
a bundle carrying 316 copies of a real username and password looked obfuscated
to everyone who opened one. Widening that regex to accept ``/live/`` would have
pinned the reported example and left the other three shapes live.

So the scrub is layered, VALUE first and SHAPE only as a fallback:

1. **Value (primary)** — the caller harvests the credential values this instance
   actually holds (:func:`routers.backup._collect_credential_values`) and passes
   them in. Any path segment or query value carrying one is replaced, at any
   position, in any URL shape, raw or percent-encoded. This is a literal match
   against what the operator's own accounts hold, not a guess about what a
   credential looks like — the same mechanism the backup artifact uses
   (bead …-msqf7), reused rather than re-derived so the two cannot drift.
2. **Shape (fallback)** — an XtreamCodes stream path ends
   ``/<user>/<pass>/<id>.<ext>`` at ANY prefix depth. This is what reaches a
   credential no account record holds: a stale stream row, a hand-entered URL.
3. **Query name** — a credential-named query parameter
   (:data:`routers.backup._URL_CREDENTIAL_QUERY_KEYS`) loses its value even when
   the value itself is unknown.
4. **Authority** — hostname/IP and userinfo are replaced wholesale, which is
   what kills the ``user:pass@host`` form.

:func:`scrub_credential_values` is the fifth and last layer, and the only one
that is not URL-aware: a literal sweep of known credential values over a whole
text. It exists because ``_URL_RE`` cannot see a credential a log line names
outside any URL, and because a bundle member is only as safe as its LEAST
URL-shaped byte. Callers building a multi-member artifact should run it over
every member as a final stage rather than trusting each producer.
"""
import json
import logging
import re
from urllib.parse import (
    parse_qsl,
    quote,
    quote_plus,
    unquote,
    urlencode,
    urlparse,
    urlunparse,
)

logger = logging.getLogger(__name__)

# The sentinel. Same spelling the backup artifact uses, so an operator reading
# either artifact sees one vocabulary for "a credential was here".
REDACTED = "***REDACTED***"

# XtreamCodes id segment: the trailing ``<digits>.<ext>`` that marks a stream
# path. Used with ``fullmatch`` against a SINGLE already-split segment, so it
# carries no repetition ambiguity and no ReDoS surface (contrast the quadratic
# forward-matching URL pattern removed in :mod:`routers.backup` under CodeQL
# alert #1879).
_XC_ID_SEGMENT_RE = re.compile(r"\d+\.\w+")

# Kept for the module's own documentation of what the ORIGINAL rule matched.
# The live rule is :func:`_redact_xc_path_shape`, which is a strict superset:
# the same ``/<user>/<pass>/<id>.<ext>`` tail, at any prefix depth instead of
# only at depth zero.
_XC_PATH_RE = re.compile(r"^/[^/]+/[^/]+/\d+\.\w+$")

# IP address pattern
_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# URL pattern for matching in free text (generous but avoids trailing punctuation)
_URL_RE = re.compile(r"https?://[^\s\"'<>\])}]+")

# Minimum length for a value to be swept LITERALLY out of a whole text
# (:func:`scrub_credential_values`).
#
# The literal sweep is unanchored by design — that is the point of it — so a
# very short credential value would match incidental text everywhere and destroy
# the artifact it is protecting: an operator whose password is ``1`` would get a
# bundle in which every ``1`` is the sentinel, including every channel number,
# timestamp and stream id. Below this length the value is still scrubbed by the
# STRUCTURAL layers (path segment, query value, authority), which are precise
# about position and therefore safe at any length; only the unanchored sweep
# stands down. This is a real, named gap, not a claim of completeness: a
# credential shorter than this that appears OUTSIDE a URL survives.
_MIN_LITERAL_SWEEP_LENGTH = 4


def _credential_forms(value: str) -> set:
    """Every spelling of ``value`` a bundle member could legitimately hold.

    A credential does not always reach an artifact as the bytes the operator
    typed. It arrives percent-encoded inside a URL, backslash-escaped inside
    JSON, quote-doubled inside CSV or single-quoted YAML. Each of those is still
    the credential, and a sweep that only knew the raw form would report clean
    on all of them.
    """
    forms = {value}
    forms.add(quote(value, safe=""))
    forms.add(quote_plus(value))
    # JSON string escaping (``json.dumps`` wraps in quotes; strip them).
    forms.add(json.dumps(value)[1:-1])
    # CSV / double-quoted escaping.
    forms.add(value.replace('"', '""'))
    # Single-quoted YAML escaping.
    forms.add(value.replace("'", "''"))
    return {f for f in forms if f}


def scrub_credential_values(
    text: str, secrets: frozenset = frozenset(), identities: frozenset = frozenset()
) -> str:
    """Replace every literal occurrence of a known credential value in ``text``.

    The layer that is not URL-aware, and the reason a caller can state the
    invariant over a WHOLE artifact rather than over its URLs. ``_URL_RE`` cannot
    see ``[M3U] auth rejected for user=<user> pass=<pass>``; this can.

    Longest values are replaced first so a username that is a substring of the
    password cannot leave a half-scrubbed remainder behind.

    Args:
        text: Any text destined for a shareable artifact.
        secrets: Known credential values, the authenticating half.
        identities: Known credential values, the identifying half.

    Returns:
        ``text`` with every known credential value replaced by
        :data:`REDACTED`. Byte-identical when nothing matched.
    """
    if not text:
        return text
    forms: set = set()
    for value in set(secrets) | set(identities):
        if not isinstance(value, str) or value == REDACTED:
            continue
        if len(value) < _MIN_LITERAL_SWEEP_LENGTH:
            continue
        forms |= _credential_forms(value)
    for form in sorted(forms, key=len, reverse=True):
        if form and form != REDACTED:
            text = text.replace(form, REDACTED)
    return text


def _rewrite_known_credential_values(
    url: str, secrets: frozenset, identities: frozenset
):
    """Layer 1 — delegate to the backup router's value-based path rewriter.

    Imported lazily rather than at module scope on purpose: this module is a
    leaf utility that :mod:`routers.client_errors` imports on a request path,
    and :mod:`routers.backup` is a large router module. A module-level import
    would make every consumer of the scrubber pull in a router, and would create
    a cycle the first time the backup router wanted to scrub something.

    Reusing that function rather than re-deriving the rule here is the point:
    it already encodes the secret-gated, percent-decoding, whole-segment
    containment semantics argued out under bead …-msqf7, and a second
    implementation of the same rule is a second implementation to keep correct.
    """
    if not secrets:
        return None
    try:
        from routers.backup import _rewrite_known_credential_segments
    except Exception:  # pragma: no cover - defensive; import must never sink a scrub
        logger.warning(
            "[OBFUSCATE] Value-based credential rewrite unavailable; "
            "falling back to the shape rule only"
        )
        return None
    return _rewrite_known_credential_segments(url, secrets, identities)


def _redact_xc_path_shape(path: str) -> str:
    """Layer 2 — blank the credential pair of an XtreamCodes stream path.

    THE RULE: a path whose LAST segment is ``<digits>.<ext>`` and which has at
    least two segments in front of it carries ``…/<user>/<pass>/<id>.<ext>``.
    The two segments before the id are replaced with ``user`` / ``pass``.

    Anchored at the TAIL, not at the root, and that is the whole difference from
    the rule this replaced. ``/live/u/p/1.ts``, ``/movie/u/p/9.mkv``,
    ``/series/u/p/4.mp4``, ``/server1/live/u/p/1.ts`` and ``/a/b/c/live/u/p/5.ts``
    all end the same way; only the prefix varies, so only the prefix is ignored.
    The original three-segment form ``/u/p/1.ts`` is the zero-prefix case and
    keeps its exact previous output.

    A segment already replaced by layer 1 is left alone — the sentinel is a
    stronger statement than ``user``, and overwriting it would hide which layer
    caught the credential.

    This layer is a HEURISTIC and is second for that reason: it is what reaches
    a credential no account record holds, at the cost of also blanking two
    segments of any non-credential URL that happens to end in this shape. In a
    diagnostic artifact that trade is one-sided.
    """
    if not path:
        return path
    segments = path.split("/")
    if len(segments) < 4:
        return path
    if not _XC_ID_SEGMENT_RE.fullmatch(segments[-1]):
        return path
    user_idx, pass_idx = len(segments) - 3, len(segments) - 2
    if not segments[user_idx] or not segments[pass_idx]:
        return path
    out = list(segments)
    if out[user_idx] != REDACTED:
        out[user_idx] = "user"
    if out[pass_idx] != REDACTED:
        out[pass_idx] = "pass"
    return "/".join(out)


def _redact_query_credentials(
    query: str, secrets: frozenset, identities: frozenset
) -> str:
    """Layer 3 — blank credential-bearing query parameters.

    Two rules, matching the backup router's: by NAME
    (``?username=…&password=…``, the shape every XtreamCodes ``get.php`` and
    ``xmltv.php`` URL uses) and by VALUE (a provider that names its parameters
    something no denylist lists, ``?u=…&p=…``).

    The query is only re-encoded when something was actually redacted, so a
    clean URL keeps its query byte-identical rather than being normalized by a
    round trip through :func:`urlencode`.
    """
    if not query:
        return query
    try:
        from routers.backup import _URL_CREDENTIAL_QUERY_KEYS as _CRED_KEYS
    except Exception:  # pragma: no cover - defensive
        _CRED_KEYS = frozenset({"username", "password", "user", "pass", "token"})

    known = {v for v in (set(secrets) | set(identities)) if v and v != REDACTED}
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        return query
    changed = False
    out = []
    for key, value in pairs:
        if value and (
            key.lower() in _CRED_KEYS
            or value in known
            or unquote(value) in known
        ):
            out.append((key, REDACTED))
            changed = True
        else:
            out.append((key, value))
    if not changed:
        return query
    # ``safe="*"`` keeps the sentinel readable instead of ``%2A%2A%2A…``.
    return urlencode(out, safe="*")


def obfuscate_url(
    url: str,
    secrets: frozenset = frozenset(),
    identities: frozenset = frozenset(),
) -> str:
    """Obfuscate a single URL by replacing hostname and credentials.

    - Hostname/IP replaced with ``example.com``, port with ``80``. Userinfo
      (``user:pass@host``) is destroyed with the authority.
    - Path segments carrying a KNOWN credential value are replaced with
      :data:`REDACTED`, at any depth and any URL shape (layer 1).
    - An XtreamCodes ``…/<user>/<pass>/<id>.<ext>`` tail has its credential pair
      replaced with ``user`` / ``pass`` even when the values are unknown
      (layer 2).
    - Credential-bearing query parameters lose their values (layer 3).

    ``secrets`` and ``identities`` default to empty, which disables layer 1
    only — every existing caller keeps working and gains layers 2 and 3.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    if not parsed.scheme or not parsed.hostname:
        return url

    # Layer 1 (PRIMARY): rewrite path segments that literally carry a credential
    # this instance holds. Runs against the original URL because the rewriter
    # needs a whole URL; the result is re-parsed so the later layers see it.
    rewritten = _rewrite_known_credential_values(url, secrets, identities)
    if rewritten is not None:
        try:
            reparsed = urlparse(rewritten)
        except Exception:  # pragma: no cover - a rewrite of a parsed URL parses
            reparsed = None
        if reparsed is not None and reparsed.scheme and reparsed.hostname:
            parsed = reparsed

    # Replace host and port. ``parsed.port`` raises ValueError for a port
    # outside 0-65535 (or non-numeric) — hostile input we must not crash
    # on, since this path scrubs untrusted client-reported URLs. Treat an
    # unparseable port the same as "no port".
    try:
        has_port = bool(parsed.port)
    except ValueError:
        has_port = False
    netloc = "example.com:80" if has_port else "example.com"

    # Layer 2 (FALLBACK): the XtreamCodes path shape, for credentials layer 1
    # could not know about.
    path = _redact_xc_path_shape(parsed.path)

    # Layer 3: credential-bearing query parameters.
    query = _redact_query_credentials(parsed.query, secrets, identities)

    return urlunparse((parsed.scheme, netloc, path, parsed.params, query, parsed.fragment))


def obfuscate_text(
    text: str,
    secrets: frozenset = frozenset(),
    identities: frozenset = frozenset(),
) -> str:
    """Obfuscate IPs, URLs and known credential values in free-form text.

    - URLs run through :func:`obfuscate_url`
    - IP addresses replaced with ``[REDACTED_IP]``
    - Any remaining literal occurrence of a known credential value replaced with
      :data:`REDACTED` (:func:`scrub_credential_values`)

    The last step is not redundant with the first. A log line can name a
    credential without putting it in a URL (``auth rejected for user=… pass=…``),
    and ``_URL_RE`` is blind to that by construction.
    """
    # Replace URLs first (they contain IPs that we don't want double-replaced)
    def _replace_url(match):
        return obfuscate_url(match.group(0), secrets, identities)

    text = _URL_RE.sub(_replace_url, text)

    # Replace any remaining bare IP addresses
    text = _IP_RE.sub("[REDACTED_IP]", text)

    # Anything the URL rule could not see, because it was never in a URL.
    text = scrub_credential_values(text, secrets, identities)

    return text
