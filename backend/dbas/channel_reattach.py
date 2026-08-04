"""Post-create channel reattachment — EPG links, logos, profile membership.

Bead ``enhancedchannelmanager-dfkbn`` items 1-3. Three kinds of channel state
that the drill (run 2026-08-04-run1, ECM 0.18.1-0022 / Dispatcharr 0.28.2)
measured as SILENTLY LOST while the restore reported ``success … created 32,
failed 0``:

======================  ========================  ===============================
State                   Drill measurement         Why it was lost
======================  ========================  ===============================
Channel logos           13 -> 0                   ``logo_id`` is dropped from the
                                                  channel create payload (it is
                                                  a SOURCE id) and nothing put it
                                                  back afterwards.
Channel EPG links       10 of 12 linked -> 0      ``epg_data_id`` is dropped for
                                                  the same reason; the EPG SOURCE
                                                  restored fine (14,668 entries)
                                                  but no channel was reconnected.
Profile membership      9 of 12 -> 12 of 12       Dispatcharr's channel create
                                                  adds each new channel to EVERY
                                                  profile with ``enabled=True``,
                                                  and nothing re-asserted the
                                                  archived selection.
======================  ========================  ===============================

Dropping the two FK fields at create time is CORRECT — a stale source id would
either 400 or, worse, silently bind an unrelated destination row. What was
missing is the second half: a post-create pass that re-derives each reference on
the DESTINATION and PATCHes it back. That is this module.

----------------------------------------------------------------------------
HOW EACH REFERENCE IS RE-DERIVED
----------------------------------------------------------------------------

* **Logos** — through the ``LOGO`` :class:`~dbas.restore_contracts.IdRemapTable`
  namespace the logos importer populates (matched OR uploaded OR re-created by
  URL). A channel whose archived ``logo_id`` does not resolve is a COUNTED miss:
  it feeds :attr:`~dbas.restore_contracts.RestoreReport.logo_misses` with the
  affected channels NAMED. This is the counter the drill needed — it read ``0``
  while 12 channels lost a logo they had.

* **EPG links** — by the channel's archived ``tvg_id``, NOT by ``epg_data_id``.
  A Dispatcharr EPG row's pk is instance-local and is re-minted when the source
  re-downloads its guide, so it cannot round-trip; ``tvg_id`` is the stable
  cross-instance identity (it is the id the XMLTV feed itself carries, and
  Dispatcharr's own channel↔EPG matching keys on it). Unresolvable => counted in
  ``epg_links_unrestored`` and named.

* **Profile membership** — from the archived CHANNEL PROFILE rows, whose
  ``channels`` field is Dispatcharr's list of ENABLED channel ids
  (``ChannelProfileSerializer.get_channels`` filters ``enabled=True``). This is
  the only place the selection exists: Dispatcharr's CHANNEL serializer carries
  no membership at all, which is why the channels importer's original
  ``profile_memberships``-on-the-channel reattach never had anything to read.
  Every restored channel is re-asserted ENABLED or DISABLED against that list,
  and each flip away from the destination's enable-everything default is counted
  as DRIFT.

----------------------------------------------------------------------------
BEST-EFFORT, NEVER FATAL
----------------------------------------------------------------------------

These passes run after their category's entities already exist. An upstream
error is logged and surfaced (as a report note or a counted miss); it never
raises, never fails the category, and never triggers a rollback of state the
operator would rather keep. Losing a logo must not cost the operator their
channels — the same reasoning that made ``dispatcharr_users`` the one non-fatal
category (bead ``…-y65si``).

Conventions (``docs/style_guide.md`` / ``backend/CLAUDE.md``): ``snake_case``;
Google-style docstrings; lazy ``%`` logging with a ``[DBAS-REATTACH]`` prefix;
only operator-facing names in logs and report fields — never a URL or a secret.
"""

from __future__ import annotations

import logging

from dbas.restore_contracts import (
    EntityType,
    IdRemapTable,
    LogoMissChannel,
    RestoreReport,
)
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# Upper bound on EPG rows pulled for the tvg_id index. A real guide is tens of
# thousands of rows (the drill's source carried 14,668); this bounds the fetch
# so a pathological destination cannot make the reattach pass unbounded.
_EPG_INDEX_MAX_ROWS = 200_000


def _as_int(value) -> int | None:
    """Coerce to ``int`` when cleanly one, else ``None`` (``bool`` rejected)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _channel_label(archive_channel: dict) -> str:
    """Operator-facing identifier for a channel — its name, never a secret."""
    name = archive_channel.get("name")
    return str(name) if name else "<unknown>"


# ---------------------------------------------------------------------------
# Logos (bead …-dfkbn item 1)
# ---------------------------------------------------------------------------


async def reattach_channel_logos(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    remap: IdRemapTable,
    archive_channels: list[dict],
) -> int:
    """Put each restored channel's archived logo back on it.

    Resolves the archived ``logo_id`` through the ``LOGO`` remap namespace and
    PATCHes it onto the destination channel. A reference that does not resolve —
    the logo's bytes were only ever on the Dispatcharr volume, or its upload was
    rejected — is recorded as a logo MISS naming the affected channel, so the
    aggregate the operator reads is the number of channels left without the logo
    they had rather than a flat ``0``.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`.
        remap: The shared :class:`IdRemapTable` (``CHANNEL`` + ``LOGO``).
        archive_channels: The CHANNEL records from the export archive.

    Returns:
        The number of channels whose logo was successfully reattached.
    """
    reattached = 0
    # One miss ROW per archived logo, listing every channel it left without a
    # logo — the LogoMissDetail contract counts logos and names channels.
    missed: dict[int, list[LogoMissChannel]] = {}

    for archive_channel in archive_channels or []:
        source_logo_id = _as_int(archive_channel.get("logo_id"))
        if source_logo_id is None:
            continue
        source_channel_id = _as_int(archive_channel.get("id"))
        dest_channel_id = (
            remap.resolve(EntityType.CHANNEL, source_channel_id)
            if source_channel_id is not None
            else None
        )
        label = _channel_label(archive_channel)

        dest_logo_id = remap.resolve(EntityType.LOGO, source_logo_id)
        if dest_logo_id is None or dest_channel_id is None:
            missed.setdefault(source_logo_id, []).append(
                LogoMissChannel(channel_id=dest_channel_id, name=label)
            )
            continue

        try:
            await client.update_channel(dest_channel_id, {"logo_id": dest_logo_id})
        except Exception as exc:  # noqa: BLE001 - per-channel containment
            logger.warning(
                "[DBAS-REATTACH] Could not reattach the logo for channel '%s' "
                "(id=%s): %s",
                label, dest_channel_id, exc,
            )
            missed.setdefault(source_logo_id, []).append(
                LogoMissChannel(channel_id=dest_channel_id, name=label)
            )
            continue
        reattached += 1

    for source_logo_id, channels in missed.items():
        report.record_logo_miss(
            label="logo #%d (archived)" % source_logo_id,
            source_export_id=source_logo_id,
            channels=channels,
        )

    if missed:
        logger.warning(
            "[DBAS-REATTACH] %d archived logo(s) could not be reinstated, "
            "affecting %d channel(s).",
            len(missed),
            sum(len(v) for v in missed.values()),
        )
    logger.info("[DBAS-REATTACH] Reattached %d channel logo(s).", reattached)
    return reattached


# ---------------------------------------------------------------------------
# EPG links (bead …-dfkbn item 2)
# ---------------------------------------------------------------------------


async def _tvg_id_index(client: DispatcharrClient) -> dict[str, int]:
    """Build a ``tvg_id -> destination EPG-data id`` index.

    Case-insensitive, whitespace-trimmed keys. On a duplicate ``tvg_id`` the
    LOWEST id wins — the same deterministic, order-independent tie-break the
    group and logo matchers use, so the reattach result does not depend on the
    order Dispatcharr happened to return rows in.

    Never raises: a failed fetch yields an empty index, which turns the whole
    pass into "every link is an honest, counted miss" rather than a crash.
    """
    try:
        rows = await client.get_epg_data(max_results=_EPG_INDEX_MAX_ROWS)
    except Exception as exc:  # noqa: BLE001 - best-effort post-create pass
        logger.warning("[DBAS-REATTACH] Could not list destination EPG data: %s", exc)
        return {}
    index: dict[str, int] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tvg_id = row.get("tvg_id")
        row_id = _as_int(row.get("id"))
        if not isinstance(tvg_id, str) or row_id is None:
            continue
        key = tvg_id.strip().lower()
        if not key:
            continue
        existing = index.get(key)
        if existing is None or row_id < existing:
            index[key] = row_id
    return index


async def reattach_epg_links(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    remap: IdRemapTable,
    archive_channels: list[dict],
) -> int:
    """Reconnect each restored channel to its EPG row via the archived tvg_id.

    Only channels that HAD a link in the archive are considered: a channel with
    no archived ``epg_data_id`` was unlinked on the source too, and linking it
    now would be inventing state the operator never had.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`.
        remap: The shared :class:`IdRemapTable` (``CHANNEL``).
        archive_channels: The CHANNEL records from the export archive.

    Returns:
        The number of channels relinked.
    """
    wanted = [
        ch
        for ch in archive_channels or []
        if _as_int(ch.get("epg_data_id")) is not None
    ]
    if not wanted:
        return 0

    index = await _tvg_id_index(client)
    relinked = 0

    for archive_channel in wanted:
        label = _channel_label(archive_channel)
        source_channel_id = _as_int(archive_channel.get("id"))
        dest_channel_id = (
            remap.resolve(EntityType.CHANNEL, source_channel_id)
            if source_channel_id is not None
            else None
        )
        raw_tvg = archive_channel.get("tvg_id")
        tvg_id = raw_tvg.strip() if isinstance(raw_tvg, str) else ""
        dest_epg_id = index.get(tvg_id.lower()) if tvg_id else None

        if dest_channel_id is None or dest_epg_id is None:
            report.record_epg_link_unrestored(
                name=label, channel_id=dest_channel_id, tvg_id=tvg_id
            )
            continue

        try:
            await client.update_channel(dest_channel_id, {"epg_data_id": dest_epg_id})
        except Exception as exc:  # noqa: BLE001 - per-channel containment
            logger.warning(
                "[DBAS-REATTACH] Could not relink channel '%s' (id=%s) to its EPG "
                "row: %s",
                label, dest_channel_id, exc,
            )
            report.record_epg_link_unrestored(
                name=label, channel_id=dest_channel_id, tvg_id=tvg_id
            )
            continue
        relinked += 1

    if report.epg_links_unrestored:
        logger.warning(
            "[DBAS-REATTACH] %d channel(s) restored WITHOUT their EPG link.",
            report.epg_links_unrestored,
        )
    logger.info("[DBAS-REATTACH] Relinked %d channel(s) to EPG data.", relinked)
    return relinked


# ---------------------------------------------------------------------------
# Channel-profile membership (bead …-dfkbn item 3)
# ---------------------------------------------------------------------------


def _archived_enabled_channels(archive_profile: dict) -> set[int] | None:
    """The SOURCE channel ids a profile had ENABLED, or ``None`` if unknown.

    Dispatcharr's ``ChannelProfileSerializer`` exposes ``channels`` as the list
    of ENABLED channel ids (``get_channels`` filters ``enabled=True``), so the
    absence of a channel from that list IS the exclusion. A profile record with
    no ``channels`` key at all carries no selection to restore — distinct from an
    EMPTY list, which is a real "nothing is enabled" selection and must be
    honoured.
    """
    channels = archive_profile.get("channels")
    if not isinstance(channels, list):
        return None
    enabled: set[int] = set()
    for value in channels:
        if isinstance(value, dict):
            value = value.get("id") if "id" in value else value.get("channel")
        as_int = _as_int(value)
        if as_int is not None:
            enabled.add(as_int)
    return enabled


async def reattach_profile_memberships(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    remap: IdRemapTable,
    archive_profiles: list[dict],
    archive_channels: list[dict],
) -> int:
    """Re-assert each archived profile's channel selection on the destination.

    For every restored channel, the membership is PATCHed to the archived
    ``enabled`` state. Dispatcharr's single-membership endpoint
    (``PATCH /api/channels/profiles/<p>/channels/<c>/``) CREATES the row when it
    is absent (0.28.2 ``apps/channels/api_views.py``), so this works whether the
    channel create already added it or not.

    Only channels THIS restore resolved are touched: a channel that already
    existed on the destination and is not in the archive keeps whatever
    membership the operator gave it.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`; each flip away from the
            destination's default is counted as membership DRIFT.
        remap: The shared :class:`IdRemapTable` (``CHANNEL`` + ``CHANNEL_PROFILE``).
        archive_profiles: The CHANNEL_PROFILE records from the export archive.
        archive_channels: The CHANNEL records from the export archive.

    Returns:
        The number of memberships successfully asserted.
    """
    asserted = 0

    for archive_profile in archive_profiles or []:
        enabled_sources = _archived_enabled_channels(archive_profile)
        if enabled_sources is None:
            continue
        source_profile_id = _as_int(archive_profile.get("id"))
        dest_profile_id = (
            remap.resolve(EntityType.CHANNEL_PROFILE, source_profile_id)
            if source_profile_id is not None
            else None
        )
        profile_label = str(archive_profile.get("name") or "<unknown>")
        if dest_profile_id is None:
            report.notes.append(
                "channel profile '%s' was not restored on this destination, so its "
                "channel selection could not be re-applied." % profile_label
            )
            continue

        # Dispatcharr enables EVERY channel in EVERY profile on create, so the
        # channels the archive EXCLUDED are the ones that silently widened the
        # profile. Track both directions so the drift row says what happened.
        disabled_names: list[str] = []
        enabled_names: list[str] = []

        for archive_channel in archive_channels or []:
            source_channel_id = _as_int(archive_channel.get("id"))
            if source_channel_id is None:
                continue
            dest_channel_id = remap.resolve(EntityType.CHANNEL, source_channel_id)
            if dest_channel_id is None:
                continue
            label = _channel_label(archive_channel)
            should_be_enabled = source_channel_id in enabled_sources
            try:
                await client.update_profile_channel(
                    dest_profile_id, dest_channel_id, {"enabled": should_be_enabled}
                )
            except Exception as exc:  # noqa: BLE001 - per-membership containment
                logger.warning(
                    "[DBAS-REATTACH] Could not set channel '%s' %s in profile '%s': %s",
                    label,
                    "enabled" if should_be_enabled else "disabled",
                    profile_label,
                    exc,
                )
                continue
            asserted += 1
            if not should_be_enabled:
                # The destination had it ENABLED (Dispatcharr's create default)
                # and the archive says it was excluded — the drift that turned a
                # 9-of-12 profile into a 12-of-12 one.
                disabled_names.append(label)

        report.record_profile_membership_drift(
            name=profile_label,
            profile_id=dest_profile_id,
            channels_disabled=disabled_names,
            channels_enabled=enabled_names,
        )
        if disabled_names:
            logger.warning(
                "[DBAS-REATTACH] Profile '%s': re-excluded %d channel(s) that the "
                "destination had enabled by default.",
                profile_label, len(disabled_names),
            )

    logger.info("[DBAS-REATTACH] Asserted %d channel-profile membership(s).", asserted)
    return asserted
