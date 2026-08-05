"""Archive-shape constants shared by the backup PRODUCER and the restore CONSUMER.

A leaf module with no DBAS imports. It exists because the two halves of the
channel EPG-link round trip live in different packages and must agree exactly:

* the PRODUCER is ``routers/backup.py``, which resolves each EPG-linked
  channel's natural key at backup time and stamps it on the archived record;
* the CONSUMERS are ``dbas/importers/channels.py`` (which strips the key from
  the channel create payload) and ``dbas/channel_reattach.py`` (which matches
  on it).

Bead ``enhancedchannelmanager-dfkbn``, PR review W3. Before this module the
producer imported the contract FROM the consumer, which is the wrong dependency
direction, and each side carried its own copy of the id-coercion rule. Two
implementations of one rule is exactly the producer/consumer disagreement this
bead exists to fix: harden :func:`as_int` on one side only and the consumer
counts a miss the producer refused to stamp. One rule, one home, both sides
import it.

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings.
"""

from __future__ import annotations

# Archived-channel key carrying the ``tvg_id`` of the EPG-DATA ROW the channel
# was linked to on the source, resolved at backup time.
#
# DISTINCT from the channel's own ``tvg_id`` field, which is the channel's own
# attribute and is routinely null on a genuinely EPG-linked channel: ECM's own
# channel PATCH sets ``epg_data_id`` and leaves ``tvg_id`` alone (verified
# empirically on drill run 2026-08-04-run2). All 7 of that run's linked channels
# reached the restore with nothing to match on and every link was dropped.
#
# ADDITIVE and OPTIONAL. An artifact without it restores exactly as it did
# before, and an older ECM reading an artifact WITH it drops the unknown key at
# Dispatcharr's ``ChannelSerializer`` (a plain ``ModelSerializer`` with a field
# allowlist and no ``validate()``), so the create still returns 200.
ARCHIVE_EPG_TVG_ID_KEY = "epg_data_tvg_id"

# Upper bound on EPG rows pulled to build a ``tvg_id`` index in EITHER direction:
# the producer's ``epg_data_id -> tvg_id`` map and the restore's
# ``tvg_id -> epg_data_id`` map. A real guide is tens of thousands of rows (the
# drill's source carried 14,668); this bounds the fetch so a pathological
# instance cannot make an index pass unbounded. ONE cap, one place to change it.
EPG_INDEX_MAX_ROWS = 200_000


def as_int(value) -> int | None:
    """Coerce ``value`` to ``int`` when it cleanly is one, else ``None``.

    ``bool`` is rejected: ``True`` is an ``int`` in Python and is never a row id.
    Deliberately strict. It does NOT accept numeric strings, because a row id
    that arrives as a string is a shape the archive contract does not promise
    and guessing at it on one side of the seam only is how the two halves drift.

    Args:
        value: Any archived field value.

    Returns:
        The value as an ``int``, or ``None`` when it is not cleanly one.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None
