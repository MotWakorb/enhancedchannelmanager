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

# The single placeholder the backup pipeline substitutes for a credential-class
# value. Kept verbatim (not derived) because it is part of the on-disk artifact
# format: artifacts written by earlier ECM builds carry this exact string, and a
# restore of one of those must still recognize it.
REDACTION_SENTINEL = "***REDACTED***"


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
