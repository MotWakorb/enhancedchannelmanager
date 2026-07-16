"""
Stream-name staleness signal for Event Sync's ``assume_current_date`` rail
(bead enhancedchannelmanager-jqwfq).

The field-reported failure: a provider leaves YESTERDAY's dateless slot name
("FURY vs HALL 6PM") in the M3U; ``assume_current_date`` synthesizes TODAY's
date at that clock time; identical title + tiny time delta scores 1.0 and the
stream attaches to the WRONG master. Undecidable from the name alone — the
missing fact is *when this exact name was first seen*.

Dispatcharr's per-stream timestamps cannot answer that (``updated_at`` /
``last_seen`` are bumped for EVERY stream on every refresh — refresh
liveness, not name freshness; spike finding on this bead). The one cheap
source that can is :class:`models.M3USnapshot`: the M3U change monitor
persists full per-group stream-name lists ~2x daily. A stream name is
**stale-suspect** when it already appears in the most recent snapshot of its
M3U account taken BEFORE local midnight today (``DEFAULT_EVENT_TIMEZONE`` —
the same anchor ``parse_event_name`` places dateless times on, so "today"
means the same day on both sides).

FAIL OPEN by construction. Snapshot capture has two known blind spots —
stream names are captured for ENABLED groups only, and each group's list is
capped at ``tasks.m3u_refresh.MAX_STREAM_NAMES`` (mirrored here as
:data:`SNAPSHOT_MAX_STREAM_NAMES`) — so ABSENCE from a snapshot is
inconclusive and maps to ``None`` (unknown), never ``False``-as-proof.
Positive membership is the only definitive answer: the cap can cause missed
detections, never false staleness.

Pure sync module: one DB read, no Dispatcharr client, no engine imports.
Async callers (the preview router / pipeline engine) run
:func:`previous_day_names` via ``run_in_threadpool``.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pytz

from services.event_sync_matcher import DEFAULT_EVENT_TIMEZONE

logger = logging.getLogger(__name__)

__all__ = [
    "SNAPSHOT_MAX_STREAM_NAMES",
    "StalenessLookup",
    "local_midnight_utc",
    "name_seen_before_today",
    "previous_day_names",
]

# Mirror of ``tasks.m3u_refresh.MAX_STREAM_NAMES`` — the per-group cap the
# snapshot writer applies. Deliberately NOT imported from there: importing
# tasks.m3u_refresh executes ``register_task(...)`` at module load, which
# would drag the task registry into this pure service's import path. A unit
# test pins the two constants equal so they cannot drift silently.
SNAPSHOT_MAX_STREAM_NAMES = 500

# {m3u_account_id: {group_name: frozenset(stream_names)}} from each
# account's latest qualifying (pre-midnight) snapshot.
StalenessLookup = dict[int, dict[str, frozenset]]


def local_midnight_utc(now: datetime | None = None) -> datetime:
    """Naive-UTC instant of TODAY's local midnight in the event timezone.

    ``M3USnapshot.snapshot_time`` is stored naive UTC (``datetime.utcnow``),
    so the comparison boundary must be converted to the same convention.
    ``now`` (tz-aware) is injectable for tests; default is the current time.
    """
    tz = pytz.timezone(DEFAULT_EVENT_TIMEZONE)
    local_now = datetime.now(tz) if now is None else now.astimezone(tz)
    midnight = tz.localize(
        datetime(local_now.year, local_now.month, local_now.day)
    )
    return midnight.astimezone(pytz.utc).replace(tzinfo=None)


def previous_day_names(
    account_ids: list[int],
    before_utc: datetime,
    db=None,
) -> StalenessLookup:
    """Per-account previous-day stream-name membership sets.

    For each account id, loads the LATEST :class:`models.M3USnapshot` whose
    ``snapshot_time`` (naive UTC) is strictly before ``before_utc`` (local
    midnight today — :func:`local_midnight_utc`) and parses its
    ``groups_data`` JSON into ``{group_name: frozenset(stream_names)}``.
    Groups with no captured ``stream_names`` list (disabled groups, legacy
    snapshots) are omitted — absent group ⇒ unknown, per the fail-open
    contract in :func:`name_seen_before_today`.

    Accounts with no qualifying snapshot are absent from the result.
    Sync DB read (~350 kB JSON max observed per row); async callers must
    dispatch via ``run_in_threadpool``. ``db`` is injectable for tests;
    default opens (and closes) its own session.
    """
    from database import get_session
    from models import M3USnapshot

    lookup: StalenessLookup = {}
    if not account_ids:
        return lookup

    session = db if db is not None else get_session()
    try:
        for account_id in account_ids:
            snapshot = (
                session.query(M3USnapshot)
                .filter(
                    M3USnapshot.m3u_account_id == account_id,
                    M3USnapshot.snapshot_time < before_utc,
                )
                .order_by(M3USnapshot.snapshot_time.desc())
                .first()
            )
            if snapshot is None or not snapshot.groups_data:
                continue
            try:
                data = json.loads(snapshot.groups_data)
            except (ValueError, TypeError):
                logger.warning(
                    "[EVENT-SYNC] staleness: snapshot %s (account %s) has "
                    "unparseable groups_data — treating freshness as unknown",
                    snapshot.id, account_id,
                )
                continue
            groups: dict[str, frozenset] = {}
            for group in data.get("groups", []):
                gname = group.get("name")
                names = group.get("stream_names")
                if not gname or not isinstance(names, list):
                    continue
                groups[gname] = frozenset(n for n in names if n)
            if groups:
                lookup[account_id] = groups
    finally:
        if db is None:
            session.close()

    logger.debug(
        "[EVENT-SYNC] staleness: previous-day lookup built for %d/%d "
        "account(s) before %s", len(lookup), len(account_ids), before_utc,
    )
    return lookup


def name_seen_before_today(
    lookup: StalenessLookup,
    provider_id: int | None,
    group_name: str | None,
    stream_name: str,
) -> bool | None:
    """Was this exact stream name already present before local midnight?

    Tri-state, fail-open (spike contract on bead jqwfq):

    * ``True`` — the name is a member of its group's list in the account's
      latest pre-midnight snapshot. Positive membership is definitive: the
      name predates today, so an ``assume_current_date`` parse of it is
      stale-suspect.
    * ``False`` — the group's list was captured, is NOT at the
      ``SNAPSHOT_MAX_STREAM_NAMES`` cap, and the name is absent: best-effort
      "first seen today". Advisory only — no caller may demote on it.
    * ``None`` — unknown, every inconclusive case: no provider id, no
      qualifying snapshot for the account, the group missing from the
      snapshot (disabled / legacy), or the group's list AT the cap (a capped
      list proves nothing about names beyond the cap).
    """
    if provider_id is None or not group_name:
        return None
    groups = lookup.get(provider_id)
    if groups is None:
        return None
    names = groups.get(group_name)
    if names is None:
        return None
    if stream_name in names:
        return True
    if len(names) >= SNAPSHOT_MAX_STREAM_NAMES:
        # Capped capture: the absent name may simply be past the cap.
        return None
    return False
