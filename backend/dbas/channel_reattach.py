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

* **EPG links** — by the archived ``tvg_id``, NOT by ``epg_data_id``. A
  Dispatcharr EPG row's pk is instance-local and is re-minted when the source
  re-downloads its guide, so it cannot round-trip; ``tvg_id`` is the stable
  cross-instance identity (it is the id the XMLTV feed itself carries, and
  Dispatcharr's own channel↔EPG matching keys on it). Unresolvable => counted in
  ``epg_links_unrestored`` and named.

  WHICH ``tvg_id`` (drill run 2026-08-04-run2, bead ``…-dfkbn``). A channel row
  carries its OWN ``tvg_id`` field, and that field is NOT the link: ECM's own
  channel PATCH sets ``epg_data_id`` and leaves ``tvg_id`` null, which the drill
  measured directly (``epg_data_id=1``, ``tvg_id=None`` after the PATCH). Every
  one of the drill's 7 linked channels therefore reached this pass with an empty
  ``tvg_id`` and every link was dropped. The link's natural key lives on the EPG
  ROW the channel points at, so the backup producer now resolves it there and
  stamps it on the archived channel as :data:`ARCHIVE_EPG_TVG_ID_KEY`
  (``routers/backup.py``, ``_resolve_epg_link_natural_keys``). This pass PREFERS
  that resolved value and FALLS BACK to the channel's own ``tvg_id``. An OLD
  artifact carries no resolved key, so it behaves exactly as it did before.

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
WHAT A DRY RUN CAN HONESTLY PREDICT
----------------------------------------------------------------------------

Both passes also run on a DRY RUN, because the number that decides whether an
operator wants :attr:`~dbas.restore_contracts.ChannelReattachMode.OVERWRITE` at
all is "how many channels I ALREADY have would this replace", and that number is
useless after the fact. Neither pass mutates anything on a dry run.

Neither pass records a MISS on a dry run either. A miss is a claim that
something the operator had is GONE, and the dgnms defect was exactly a preview
making that claim about a restore that was going to work. On a dry run these
passes report the population SPLIT and nothing else.

What each pass may resolve at preview time is NOT symmetric, and the asymmetry
is about one question: does the destination state this pass matches against
already exist, or does THIS RESTORE create it?

* **EPG links — resolve the CHANNEL remap, never the GUIDE.** The two are
  different kinds of state and the rule above sorts them cleanly.

  The GUIDE rows this pass matches ``tvg_id`` against are rows the restore ITSELF
  puts there: the EPG-source step runs before channels and waits for Dispatcharr
  to download the guide (``_epg_step_with_download_wait``), and on a dry run that
  wrapper is a pass-through. Reading the destination's guide during a preview
  therefore reads the PRE-restore guide, which on a disaster-recovery target is
  EMPTY. An earlier cut of this module did resolve it, and a 200-channel DR
  preview reported "200 channels restored without an EPG link", named all 200,
  and then the apply restored every one.

  The CHANNEL REMAP is the opposite: this same run's channels importer populates
  it, on a dry run as much as on an apply, so it is state that already exists by
  the time this pass runs. A later cut over-corrected and stopped reading it too,
  and a channel the importer SKIPPED — ``DEPENDENCY_UNRESOLVED``, or the
  ambiguous null-``channel_number`` CONFLICT — has no entry in it, so it was
  classified as a pre-existing channel whose live guide link would be REPLACED.
  An operator previewing a merge with "replace" selected and channel groups
  deselected got a red alert naming their channels for a restore that touches
  none of them. A channel that does not resolve is in NEITHER half of the split
  and is not a miss: it is simply not visible from a preview.

* **Logos — resolve what already exists, plus what this run has decided to
  create.** The ``LOGO`` remap this pass reads is populated during the SAME run
  by the logos importer, which registers a destination id for every archived
  logo it MATCHES against the destination (``importers/logos.py``) on a dry run
  as much as on an apply. For a merge into a live install that matched
  population IS the population, so the preview was already faithful there.

  It was NOT faithful on a FRESH target (bead ``…-dgnms``, drill run 4). Nothing
  matches on an empty destination, so every logo is a would-CREATE, and a
  would-create has no destination id — the importer deliberately registers none,
  because the id an apply mints is not knowable and a fabricated one would
  corrupt every FK resolved through it. The whole population fell out of the
  split and the preview reported ``logo_reattach.created_channels: 0`` for an
  apply that reattached 11 minutes later.

  So the importer now also reports the SOURCE ids it would create
  (``LogoImportResult.would_create_source_ids``) and this pass counts those
  channels too. No id is invented: "this logo will exist" is a decision the same
  run's importer has already taken, which is the same class of already-existing
  state as the CHANNEL remap — and squarely the opposite of the destination
  GUIDE, which no part of the preview has decided anything about. A logo the
  importer REJECTS is not in the set, so it stays out of the split.

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

from dbas.archive_keys import (
    ARCHIVE_EPG_TVG_ID_KEY,
    EPG_INDEX_MAX_ROWS,
    as_int as _as_int,
)
from dbas.restore_contracts import (
    ChannelReattachMode,
    EntityType,
    IdRemapTable,
    LogoMissChannel,
    ReattachPopulation,
    RestoreReport,
)
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# Re-exported for callers that already import the archive contract from here.
__all__ = [
    "ARCHIVE_EPG_TVG_ID_KEY",
    "EPG_INDEX_MAX_ROWS",
    "reattach_channel_logos",
    "reattach_epg_links",
    "reattach_profile_memberships",
]


def _channel_label(archive_channel: dict) -> str:
    """Operator-facing identifier for a channel — its name, never a secret."""
    name = archive_channel.get("name")
    return str(name) if name else "<unknown>"


def _is_preserved(
    source_channel_id: int | None,
    *,
    mode: ChannelReattachMode,
    created_source_ids: set[int] | None,
) -> bool:
    """Whether this archived channel must be left alone by a reattach pass.

    True only in :attr:`ChannelReattachMode.PRESERVE` for a channel this restore
    did NOT create. ``created_source_ids`` is the set of ARCHIVE (source) ids the
    channels importer created or, on a dry run, would create; source ids are used
    rather than destination ids because a dry run's provisional destination id is
    not the id an apply would mint, and the split has to read identically in both
    modes.

    Two deliberate FALSE cases, both "we cannot prove we did not create it, so we
    must not claim we preserved it" (PR review round 2, findings 4 and 6):

    * ``source_channel_id`` is ``None`` — the archived row carried no id this
      module's coercion accepts, so it is not IN ``created_source_ids`` for a
      reason that has nothing to do with who created the channel. Preserving on
      that would put "we left your existing channel alone" in the report about a
      channel THIS RESTORE MADE. Returning False sends it down the normal path,
      where its unresolvable destination id becomes a counted, named miss — the
      behaviour before any of this existed.
    * ``created_source_ids`` is ``None`` — the caller has no population
      information at all. This is now only reachable from a test that passes it
      explicitly; the parameter is required on both passes precisely so a
      forgotten argument cannot silently disable a safety default.
    """
    if mode is not ChannelReattachMode.PRESERVE:
        return False
    if created_source_ids is None:
        return False
    if source_channel_id is None:
        return False
    return source_channel_id not in created_source_ids


# ---------------------------------------------------------------------------
# Logos (bead …-dfkbn item 1)
# ---------------------------------------------------------------------------


async def reattach_channel_logos(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    remap: IdRemapTable,
    archive_channels: list[dict],
    created_source_ids: set[int] | None,
    mode: ChannelReattachMode = ChannelReattachMode.PRESERVE,
    is_dry_run: bool = False,
    would_create_logo_source_ids: set[int] | None = None,
) -> int:
    """Put each restored channel's archived logo back on it.

    Resolves the archived ``logo_id`` through the ``LOGO`` remap namespace and
    PATCHes it onto the destination channel. A reference that does not resolve
    (the logo's bytes were only ever on the Dispatcharr volume, or its upload was
    rejected) is recorded as a logo MISS naming the affected channel, so the
    aggregate the operator reads is the number of channels left without the logo
    they had rather than a flat ``0``.

    MODE (PR review W1). In :attr:`ChannelReattachMode.PRESERVE` (the default) a
    channel this restore did NOT create keeps whatever logo the operator gave it.
    In :attr:`ChannelReattachMode.OVERWRITE` the archive's logo is applied to it
    too. Either way the split lands in ``report.logo_reattach``.

    DRY RUN. Resolves exactly what the apply resolves, PATCHes nothing, and
    records NO miss. A channel is counted into the split only when BOTH its
    destination id and its logo resolve, which is the same condition the apply
    requires before it touches anything: counting before resolution told the
    operator two channels would be replaced when the apply replaced none.

    A logo the restore will CREATE counts too, via
    ``would_create_logo_source_ids`` (bead ``…-dgnms``, drill run 4). It has no
    destination id yet — the logos importer registers none for a would-create,
    because the id an apply mints is not knowable — but "this logo will exist"
    is a FACT that importer already established, so the preview reports it. This
    used to be the documented LOWER BOUND, and on a FRESH target the bound was
    the whole answer: nothing matches, so every logo is a would-create, and the
    preview reported ``created_channels: 0`` for an apply that reattached 11.
    See the module docstring for why the EPG pass makes the opposite call about
    the destination GUIDE — that is state the restore creates and cannot be read
    early; this is state the SAME RUN's logos importer has already decided.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`.
        remap: The shared :class:`IdRemapTable` (``CHANNEL`` + ``LOGO``).
        archive_channels: The CHANNEL records from the export archive.
        created_source_ids: ARCHIVE ids of the channels this restore created (or,
            on a dry run, would create). REQUIRED, and deliberately not
            defaulted: ``None`` disables preserving entirely, so a forgotten
            argument would silently turn a safety default off (PR review round 2,
            finding 6). Pass ``None`` explicitly to mean "no population
            information".
        mode: What to do about channels this restore did not create.
        is_dry_run: Report the split without mutating anything.
        would_create_logo_source_ids: ARCHIVE (source) logo ids a DRY RUN
            determined the apply would create — from
            :attr:`dbas.importers.logos.LogoImportResult.would_create_source_ids`.
            Read ONLY on a dry run; an apply resolves real destination ids
            through the remap and ignores this entirely.

    Returns:
        The number of channels whose logo was reattached (0 on a dry run).
    """
    population = ReattachPopulation(mode=mode)
    report.logo_reattach = population
    reattached = 0
    # One miss ROW per archived logo, listing every channel it left without a
    # logo. The LogoMissDetail contract counts logos and names channels.
    missed: dict[int, list[LogoMissChannel]] = {}

    for archive_channel in archive_channels or []:
        source_logo_id = _as_int(archive_channel.get("logo_id"))
        if source_logo_id is None:
            continue
        source_channel_id = _as_int(archive_channel.get("id"))
        label = _channel_label(archive_channel)
        was_created = (
            created_source_ids is not None and source_channel_id in created_source_ids
        )

        if _is_preserved(
            source_channel_id, mode=mode, created_source_ids=created_source_ids
        ):
            population.name_preserved(label)
            continue

        dest_channel_id = (
            remap.resolve(EntityType.CHANNEL, source_channel_id)
            if source_channel_id is not None
            else None
        )
        dest_logo_id = remap.resolve(EntityType.LOGO, source_logo_id)
        # A dry-run-only widening (bead …-dgnms): the logo has no destination id
        # because the apply has not created it yet, but the logos importer has
        # already decided it WILL. Never consulted on an apply, where
        # ``dest_logo_id`` is the real, minted id and the only thing PATCHable.
        would_be_created = (
            is_dry_run
            and dest_logo_id is None
            and would_create_logo_source_ids is not None
            and source_logo_id in would_create_logo_source_ids
        )
        if (dest_logo_id is None and not would_be_created) or dest_channel_id is None:
            # A dry run records NO miss: the logos importer has not uploaded yet,
            # so an unresolved reference here is "not visible from a preview",
            # not "the operator lost this". Claiming otherwise is the dgnms
            # defect. The channel simply stays out of the split.
            if not is_dry_run:
                missed.setdefault(source_logo_id, []).append(
                    LogoMissChannel(channel_id=dest_channel_id, name=label)
                )
            continue

        if is_dry_run:
            # Resolvable, so the apply WOULD reattach it. Count the split and
            # stop: a preview mutates nothing.
            if was_created:
                population.created_channels += 1
            else:
                population.name_existing(label)
            continue

        try:
            await client.update_channel(dest_channel_id, {"logo_id": dest_logo_id})
        except Exception as exc:  # noqa: BLE001 - per-channel containment
            # TYPE only (PR review W4): an httpx error's str() embeds the full
            # request URL, and a Dispatcharr URL is not a thing this log may
            # carry. The channel is already named; the type is the diagnosis.
            logger.warning(
                "[DBAS-REATTACH] Could not reattach the logo for channel '%s' "
                "(id=%s): %s",
                label, dest_channel_id, type(exc).__name__,
            )
            missed.setdefault(source_logo_id, []).append(
                LogoMissChannel(channel_id=dest_channel_id, name=label)
            )
            continue
        reattached += 1
        if was_created:
            population.created_channels += 1
        else:
            population.name_existing(label)

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
    if population.preserved_channels:
        logger.info(
            "[DBAS-REATTACH] Left the logo of %d pre-existing channel(s) alone "
            "(mode=%s).",
            population.preserved_channels, mode.value,
        )
    logger.info(
        "[DBAS-REATTACH] Reattached %d channel logo(s) (dry_run=%s; %d created, "
        "%d pre-existing).",
        reattached, is_dry_run, population.created_channels,
        population.existing_channels,
    )
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
        rows = await client.get_epg_data(max_results=EPG_INDEX_MAX_ROWS)
    except Exception as exc:  # noqa: BLE001 - best-effort post-create pass
        # TYPE only (PR review W4): an httpx error's str() embeds the request URL.
        logger.warning(
            "[DBAS-REATTACH] Could not list destination EPG data: %s",
            type(exc).__name__,
        )
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


def _link_tvg_id(archive_channel: dict) -> str:
    """The natural key to relink this archived channel by, trimmed (may be "").

    PREFERS :data:`ARCHIVE_EPG_TVG_ID_KEY`, the tvg_id the backup producer read
    off the EPG-DATA ROW the channel pointed at, which is the identity of the
    LINK. Falls back to the channel's own ``tvg_id`` field, which is the only
    thing an OLD artifact carries and is what this pass always used before.

    The preference order matters and is not arbitrary: the two can legitimately
    DISAGREE (an operator can link a channel to a guide row whose tvg_id differs
    from the one the channel's own field advertises), and in that case the row
    the operator actually linked is the one to restore. The channel's own
    ``tvg_id`` remains its own field and is restored to the channel untouched by
    the channel create. This pass only reads it, never writes it.
    """
    for key in (ARCHIVE_EPG_TVG_ID_KEY, "tvg_id"):
        raw = archive_channel.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


async def reattach_epg_links(
    *,
    client: DispatcharrClient,
    report: RestoreReport,
    remap: IdRemapTable,
    archive_channels: list[dict],
    created_source_ids: set[int] | None,
    mode: ChannelReattachMode = ChannelReattachMode.PRESERVE,
    is_dry_run: bool = False,
) -> int:
    """Reconnect each restored channel to its EPG row via the archived tvg_id.

    Only channels that HAD a link in the archive are considered: a channel with
    no archived ``epg_data_id`` was unlinked on the source too, and linking it
    now would be inventing state the operator never had.

    MODE (PR review W1). In :attr:`ChannelReattachMode.PRESERVE` (the default) a
    channel this restore did NOT create keeps the EPG link the operator gave it;
    in :attr:`ChannelReattachMode.OVERWRITE` the archived link is applied to it
    too. The split lands in ``report.epg_link_reattach`` either way.

    DRY RUN — SPLIT ONLY, and this pass resolves NOTHING (PR review round 2, the
    blocking finding). The guide rows it matches ``tvg_id`` against are rows the
    RESTORE ITSELF creates: EPG sources are restored and downloaded before
    channels, and on a dry run that download wrapper is a pass-through. Reading
    the destination guide during a preview therefore reads the PRE-restore guide,
    which on a disaster-recovery target is empty. An earlier cut of this function
    did resolve here, and a measured 2-channel DR preview reported
    ``epg_links_unrestored=2`` naming both channels while the apply relinked both
    — the dgnms class, on the very pass this changeset added to the dry run. So a
    preview partitions preserved-vs-actionable, which needs no destination state,
    and reports that. It never fetches the index, never records a miss, and the
    ``created`` / ``existing`` counts it reports are what the apply will act on.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`.
        remap: The shared :class:`IdRemapTable` (``CHANNEL``).
        archive_channels: The CHANNEL records from the export archive.
        created_source_ids: ARCHIVE ids of the channels this restore created (or,
            on a dry run, would create). REQUIRED, and deliberately not
            defaulted: ``None`` disables preserving entirely, so a forgotten
            argument would silently turn a safety default off (PR review round 2,
            finding 6). Pass ``None`` explicitly to mean "no population
            information".
        mode: What to do about channels this restore did not create.
        is_dry_run: Report the split without reading or writing anything.

    Returns:
        The number of channels relinked (the WOULD-BE number on a dry run).
    """
    population = ReattachPopulation(mode=mode)
    report.epg_link_reattach = population

    wanted = [
        ch
        for ch in archive_channels or []
        if _as_int(ch.get("epg_data_id")) is not None
    ]
    if not wanted:
        return 0

    # Partition BEFORE the fetch: when every linked channel is preserved there is
    # nothing left to resolve, and the guide fetch would be pure cost.
    actionable = []
    for archive_channel in wanted:
        source_channel_id = _as_int(archive_channel.get("id"))
        if _is_preserved(
            source_channel_id, mode=mode, created_source_ids=created_source_ids
        ):
            population.name_preserved(_channel_label(archive_channel))
            continue
        actionable.append((archive_channel, source_channel_id))

    if not actionable:
        logger.info(
            "[DBAS-REATTACH] Left the EPG link of %d pre-existing channel(s) "
            "alone (mode=%s); nothing to relink.",
            population.preserved_channels, mode.value,
        )
        return 0

    if is_dry_run:
        # The destination GUIDE is not readable as a prediction here (see the
        # docstring): the restore is what puts those rows there. The CHANNEL
        # REMAP is a different kind of state and IS readable — this same run's
        # channels importer populates it, on a dry run as much as on an apply,
        # which is the identical justification the logo pass resolves under.
        #
        # A channel the importer SKIPPED has no entry in it, and skipping the
        # resolve here classified every such channel as a pre-existing channel
        # whose live guide link would be REPLACED. Two reachable producers, both
        # ordinary: ``DEPENDENCY_UNRESOLVED`` (the operator deselected
        # ``channel_groups``, or a group create failed) and the ambiguous
        # null-``channel_number`` CONFLICT. An operator previewing a merge with
        # "replace" selected and groups deselected got a red alert naming their
        # channels for a restore that touches none of them.
        for archive_channel, source_channel_id in actionable:
            dest_channel_id = (
                remap.resolve(EntityType.CHANNEL, source_channel_id)
                if source_channel_id is not None
                else None
            )
            if dest_channel_id is None:
                # Not visible from a preview, so it is neither a split entry nor
                # a miss. The apply reaches the same verdict by a different road:
                # an unresolvable destination id there is a counted, NAMED miss,
                # and a miss is never in the split.
                continue
            if created_source_ids is not None and source_channel_id in created_source_ids:
                population.created_channels += 1
            else:
                population.name_existing(_channel_label(archive_channel))
        would_relink = population.created_channels + population.existing_channels
        logger.info(
            "[DBAS-REATTACH] Dry run: %d channel EPG link(s) would be applied "
            "(%d created, %d pre-existing), %d left alone (mode=%s).",
            would_relink, population.created_channels,
            population.existing_channels, population.preserved_channels,
            mode.value,
        )
        return would_relink

    index = await _tvg_id_index(client)
    relinked = 0

    for archive_channel, source_channel_id in actionable:
        label = _channel_label(archive_channel)
        dest_channel_id = (
            remap.resolve(EntityType.CHANNEL, source_channel_id)
            if source_channel_id is not None
            else None
        )
        tvg_id = _link_tvg_id(archive_channel)
        dest_epg_id = index.get(tvg_id.lower()) if tvg_id else None
        was_created = (
            created_source_ids is not None and source_channel_id in created_source_ids
        )

        if dest_channel_id is None or dest_epg_id is None:
            report.record_epg_link_unrestored(
                name=label, channel_id=dest_channel_id, tvg_id=tvg_id
            )
            continue

        try:
            await client.update_channel(dest_channel_id, {"epg_data_id": dest_epg_id})
        except Exception as exc:  # noqa: BLE001 - per-channel containment
            # TYPE only (PR review W4): an httpx error's str() embeds the
            # request URL. The channel is already named in this line.
            logger.warning(
                "[DBAS-REATTACH] Could not relink channel '%s' (id=%s) to its "
                "EPG row: %s",
                label, dest_channel_id, type(exc).__name__,
            )
            report.record_epg_link_unrestored(
                name=label, channel_id=dest_channel_id, tvg_id=tvg_id
            )
            continue
        relinked += 1
        if was_created:
            population.created_channels += 1
        else:
            population.name_existing(label)

    if report.epg_links_unrestored:
        logger.warning(
            "[DBAS-REATTACH] %d channel(s) restored WITHOUT their EPG link.",
            report.epg_links_unrestored,
        )
    if population.preserved_channels:
        logger.info(
            "[DBAS-REATTACH] Left the EPG link of %d pre-existing channel(s) "
            "alone (mode=%s).",
            population.preserved_channels, mode.value,
        )
    logger.info(
        "[DBAS-REATTACH] Relinked %d channel(s) to EPG data (%d created, "
        "%d pre-existing).",
        relinked, population.created_channels, population.existing_channels,
    )
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
    is_dry_run: bool = False,
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

    DRY RUN (bead ``…-dgnms``, drill run 4). This pass used to be apply-only, so
    a preview reported ``profile_membership_drift: 0`` for an apply that then
    reported 6 — the counter that exists precisely to warn an operator their
    hide-these-channels profile is about to widen was silent in the one place it
    could still be acted on. It is now predicted, because it is entirely
    computable from state the preview already holds: the flip set is
    "restored channels the archived profile EXCLUDES", and Dispatcharr's
    enable-everything create default is what they would all be flipped away
    from. The dry run does the identical arithmetic against the identical remap
    entries and PATCHes nothing.

    The prediction is exact rather than a bound because the apply's own count is
    "memberships we set to disabled", not "memberships that were observed
    enabled first" — the apply never reads the destination's current membership
    either. Whatever changes that must change both branches together.

    Args:
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`; each flip away from the
            destination's default is counted as membership DRIFT.
        remap: The shared :class:`IdRemapTable` (``CHANNEL`` + ``CHANNEL_PROFILE``).
        archive_profiles: The CHANNEL_PROFILE records from the export archive.
        archive_channels: The CHANNEL records from the export archive.
        is_dry_run: Report the drift without PATCHing any membership.

    Returns:
        The number of memberships successfully asserted (the WOULD-BE number on
        a dry run).
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
            if is_dry_run:
                # Predict, never PATCH. Falls through to the same counting below
                # so the preview and the apply can only ever report the same
                # number for the same inputs.
                asserted += 1
                if not should_be_enabled:
                    disabled_names.append(label)
                continue
            try:
                await client.update_profile_channel(
                    dest_profile_id, dest_channel_id, {"enabled": should_be_enabled}
                )
            except Exception as exc:  # noqa: BLE001 - per-membership containment
                # TYPE only (PR review W4): an httpx error's str() embeds the
                # request URL. Both the channel and the profile are already
                # named in this line, so the type is the only thing missing.
                logger.warning(
                    "[DBAS-REATTACH] Could not set channel '%s' %s in profile '%s': %s",
                    label,
                    "enabled" if should_be_enabled else "disabled",
                    profile_label,
                    type(exc).__name__,
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
                "[DBAS-REATTACH] Profile '%s': %s %d channel(s) that the "
                "destination had enabled by default.",
                profile_label,
                "would re-exclude" if is_dry_run else "re-excluded",
                len(disabled_names),
            )

    logger.info(
        "[DBAS-REATTACH] Asserted %d channel-profile membership(s) (dry_run=%s).",
        asserted, is_dry_run,
    )
    return asserted
