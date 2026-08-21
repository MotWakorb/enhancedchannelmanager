"""The redaction sentinel and its consumer-side predicates (bead ``…-6pilh``).

Cross-cutting utility (``docs/style_guide.md`` — "cross-cutting utilities at top
level"): a leaf module with no ECM imports, so both the artifact PRODUCER
(``routers.backup``) and the restore CONSUMERS (``dbas.importers.*``,
``config``) can share one definition without an import cycle.

WHY THIS EXISTS
---------------

The DBAS backup pipeline is redact-by-default: every credential-class value is
replaced with :data:`REDACTION_SENTINEL` before a byte enters the archive
(``routers.backup._redact_credentials_deep``). That is correct. What was missing
was the other half of the contract — the restore side had no way to RECOGNIZE
the placeholder, so it wrote the literal string straight into the destination's
credential field.

The failure mode that produced this module: a restored Xtream-Codes M3U account
whose ``password`` was the 14-character string ``***REDACTED***``. It presents
as fully configured — the UI shows a populated field and every truthiness-based
"is a credential present?" probe returns True — and then fails at the provider
with a 512, materializing zero streams. That is strictly WORSE than an empty
password, which is visibly incomplete and prompts the operator to act.

THE TWO RULES
-------------

1. **Never write the sentinel into a destination field.** Strip it and leave the
   credential unset (:func:`strip_redaction_sentinels`).
2. **Never let a presence check be fooled by it.** A value ECM itself wrote as a
   placeholder is ABSENT, not present (:func:`credential_is_present`).

Detection is by VALUE, not by key name. A key-name denylist has to be kept in
sync with every producer; "this exact string is our own placeholder" is true
regardless of which field carries it, including keys nested inside
``custom_properties`` blobs.
"""

from __future__ import annotations

import re

# The single placeholder the backup pipeline substitutes for a credential-class
# value. Kept verbatim (not derived) because it is part of the on-disk artifact
# format: artifacts written by earlier ECM builds carry this exact string, and a
# restore of one of those must still recognize it.
REDACTION_SENTINEL = "***REDACTED***"

# The ``[0]`` list positions ``strip_redaction_sentinels`` writes into a
# dotted path, read back by :func:`value_at_path`. Pre-compiled on a literal
# pattern (``docs/style_guide.md`` — no bare ``re.*`` on a built pattern).
_INDEX_PATTERN = re.compile(r"(\d+)\]")

# Dotted-path segment marking a credential ECM read out of the DESTINATION'S OWN
# CACHE of the provider's reply rather than out of an operator-editable field
# (bead ``…-posm1``). Dispatcharr stores the ``player_api.php`` response on an
# Xtream-Codes M3U account profile as
# ``profiles[N].custom_properties.user_info``, and that blob echoes the
# ``username`` and ``password`` it authenticated with — so the deep redactor
# strips them there too, and the restore reports them as fields to re-enter.
#
# There is no field on any screen to re-enter them INTO. The blob is rewritten
# wholesale by the destination on its next successful refresh, using whatever
# credentials the operator DID enter. Measured live on 0.29.0 (bead ``…-ukjx5``):
# after B's real provider credentials were entered, the account's own
# ``username``/``password`` dropped out of the action item and the row survived
# on these two paths alone, so the operator's line still read "1 account(s) need
# credentials re-entered" after they had done exactly what it asked.
#
# An action item that cannot be cleared by performing it is bead ``…-kcfru``'s
# crying-wolf failure, and bead ``…-15g1j`` is the ruling: a FAITHFUL absence is
# not a shortfall. This is one — the destination is not missing anything it will
# not repopulate itself.
CACHED_PROVIDER_RESPONSE_SEGMENT = "custom_properties.user_info."


def credential_path_is_operator_actionable(path: object) -> bool:
    """True unless ``path`` names a destination-owned cached-response field.

    The predicate that keeps :meth:`RestoreReport.record_credential_reentry` from
    reporting work the operator cannot do. Matches on the dotted PATH that
    :func:`strip_redaction_sentinels` emits, so it is indifferent to which
    profile index the blob sits under (``profiles[0]`` and ``profiles[3]`` are
    the same fact) and it never suppresses a real field that merely shares a
    leaf name: ``username`` and ``profiles[0].username`` stay actionable, and so
    does anything else under ``custom_properties`` — ``xc_id`` and its siblings
    are not credentials and never reach this list at all.

    Args:
        path: One dotted path exactly as ``strip_redaction_sentinels`` emitted
            it. Coerced with ``str`` so a caller cannot suppress the check by
            handing over a non-string.

    Returns:
        True when the operator has somewhere to re-enter the credential.
    """
    return CACHED_PROVIDER_RESPONSE_SEGMENT not in str(path)


def is_redaction_sentinel(value: object) -> bool:
    """True iff ``value`` is exactly the redaction placeholder.

    Exact match only — no trimming, no case folding. A credential that merely
    resembles the sentinel is a real credential and must survive untouched.

    Args:
        value: Any value read out of an archive or a settings blob.

    Returns:
        True when the value is ECM's own placeholder.
    """
    return isinstance(value, str) and value == REDACTION_SENTINEL


def credential_is_present(value: object) -> bool:
    """True iff ``value`` is a usable credential — NOT merely truthy.

    Use this anywhere the question is "has the operator supplied this
    credential?". Plain truthiness answers YES for the placeholder, which is how
    a non-functional restored account passed a before/after credential-presence
    diff as byte-identical while the instance was dead.

    Args:
        value: The credential value to test.

    Returns:
        True when the value is non-empty and is not the redaction placeholder.
    """
    return bool(value) and not is_redaction_sentinel(value)


def strip_redaction_sentinels(payload: dict) -> tuple[dict, list[str]]:
    """Remove every sentinel-valued key, at any depth, without mutating input.

    Removal (rather than rewriting to ``""``) is deliberate: an absent key means
    "unset" to Dispatcharr's serializers, which is the state an operator can
    see and fix. Falsy values (``None`` / ``""``) are NOT touched — those are
    meaningful "explicitly unset" values the artifact carries on purpose (the
    deep redactor preserves them for exactly that reason).

    Args:
        payload: A record decoded from a backup artifact. Nested dicts and lists
            are walked too, so a placeholder inside a ``custom_properties`` blob
            is caught as well.

    Returns:
        ``(cleaned, removed_paths)`` — a new dict with the placeholder keys
        dropped, and the dotted paths of the keys that were removed (field NAMES
        only, never values), in document order.
    """
    removed: list[str] = []
    cleaned = _strip(payload, "", removed)
    return cleaned, removed  # type: ignore[return-value]  # dict in, dict out


def _strip(node: object, prefix: str, removed: list[str]) -> object:
    """Recursive worker for :func:`strip_redaction_sentinels`."""
    if isinstance(node, dict):
        out: dict = {}
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if is_redaction_sentinel(value):
                removed.append(path)
                continue
            out[key] = _strip(value, path, removed)
        return out
    if isinstance(node, list):
        return [
            _strip(item, f"{prefix}[{index}]", removed)
            for index, item in enumerate(node)
        ]
    return node


def value_at_path(payload: object, path: str) -> object:
    """Read back the value at one dotted path from :func:`strip_redaction_sentinels`.

    That function reports WHICH keys it removed, as dotted paths
    (``password``, ``custom_properties.password``, ``profiles[0].password``).
    The other half of the round trip is asking a DIFFERENT record — the one the
    destination currently holds — what IT has at the same path, which is how a
    repeat cycle tells "the operator has since re-entered this" from "the
    destination still has nothing here" (bead ``…-ukjx5``).

    A path that does not resolve yields ``None``, which
    :func:`credential_is_present` then reads as ABSENT. That is the fail-loud
    direction on purpose: a destination record that does not expose the field at
    all cannot be shown to have the credential, and reporting an action item the
    operator can dismiss is recoverable, where silently dropping one is the
    failure this bead exists to end.

    Args:
        payload: The record to read — typically the destination entity as its
            list endpoint returned it.
        path: One dotted path exactly as ``strip_redaction_sentinels`` emitted it.

    Returns:
        The value at ``path``, or ``None`` when any segment does not resolve.
    """
    node: object = payload
    for segment in path.split("."):
        key, _, indexes = segment.partition("[")
        if key:
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        for index in _INDEX_PATTERN.findall(indexes):
            if not isinstance(node, list):
                return None
            position = int(index)
            if position >= len(node):
                return None
            node = node[position]
    return node


def url_is_redacted(value: object) -> bool:
    """True iff ``value`` is an address ECM cut a credential OUT of.

    The narrower sibling of :func:`url_can_serve`, and deliberately not the same
    question. ``url_can_serve`` answers "could this fetch anything?", which is
    ``False`` for an EMPTY url too; this one answers "is this our own record
    that the address carried a secret?" — the specific shortfall bead
    ``…-msqf7`` creates and bead ``…-ukjx5`` has to count on every cycle. An
    empty url is a different loss with a different remedy, and conflating them
    would put one population in the other's counter.

    Args:
        value: A ``url`` read off a stream record — archived or destination.

    Returns:
        True when the value is non-empty and carries the redaction placeholder.
    """
    return bool(value) and REDACTION_SENTINEL in str(value)


def url_can_serve(value: object) -> bool:
    """True iff ``value`` is an address that could actually fetch something.

    Use this anywhere the question is "does this stream have a usable address?".
    Plain truthiness answers YES for a redacted URL, which is how 53 channels on
    a replica fetched HTTP 404 while the run reported zero unplayable channels
    (bead ``…-1td94``).

    THE SECOND HALF OF :func:`credential_is_present`'S CONTRACT. That function
    covers a credential FIELD whose whole value ECM replaced. This one covers a
    URL that merely CONTAINS the placeholder: the credential-redacting producer
    (bead ``…-msqf7``) rewrites only the credential path segments of an Xtream
    Codes stream URL, so what crosses is a real-looking address —
    ``http://host/live/***REDACTED***/***REDACTED***/53.ts`` — that names where
    the stream pointed and resolves to nothing. Containment, not equality, is
    therefore the right test here, and equality the right test there.

    NOT A SUBSTITUTE FOR FETCHING. An address can be dead for reasons no local
    predicate can see (the provider revoked it, the host is gone). This answers
    the narrower question ECM can answer offline: is this an address at all, or
    is it ECM's own record that there ISN'T one? It exists as a single named
    function so a future reason an address is unusable is added HERE, once,
    rather than at each site that currently asks.

    Args:
        value: A ``url`` read off a stream record — archived or destination.

    Returns:
        True when the value is non-empty and carries no redaction placeholder.
    """
    return bool(value) and REDACTION_SENTINEL not in str(value)
