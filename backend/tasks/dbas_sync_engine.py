"""One-way cross-instance sync ENGINE CORE — config categories (epic ``i39wu``).

Bead ``enhancedchannelmanager-tjaey``. Architecture: [ADR-013](../../docs/adr/
ADR-013-cross-instance-live-sync.md) S1/S3/S4/S5/S7/S9. Security: threat model
``docs/security/threat_model_dbas_import.md`` §11 Addendum D (D2/D3/D5/D8/D9).

What this is (the proven thesis — spike ``xp6mp``)
--------------------------------------------------
Sync is **"restore over HTTP"**. The DBAS restore orchestrator
(``dbas.restore_orchestrator.run_restore`` / ``run_dry_run``) and its importers
take the Dispatcharr ``client`` as an injected parameter — the ONLY coupling to
"local" is a single ``get_client()`` call in the archive-restore task. So sync:

1. gathers the LOCAL source-A config (the SAME ``_gather_dispatcharr_sections``
   pointed at ``get_client()`` the backup builder uses),
2. REDACTS it to topology-only via the shared ``_REDACT_KEYS`` deep redactor
   (no secrets on the wire — Addendum D D2),
3. maps each category to its :class:`EntityType` (the SAME
   ``restore_artifact._SECTION_TO_ENTITY`` table the archive decoder uses),
4. assembles an :class:`~dbas.preflight.ImportPlan` whose manifest carries
   ``schema_version = BACKUP_SCHEMA_VERSION`` (the orchestrator runs the .17
   pre-flight gate; without the stamp it refuses the plan — spike empirical find),
5. and runs the UNCHANGED orchestrator against a remote (dest-B) client built
   from a ``SyncTarget`` row (``dbas_sync_client.make_remote_client``).

The orchestrator, importers, and post-import natural-key reattachment machinery
are reused rather than reimplemented. Sync-specific code remains the live-source
plan reader, config-only step registry, ``run_sync``, and shared never-sync
constant.

Scope of THIS engine (ADR-013 phasing / S9)
-------------------------------------------
CONFIG categories (bead ``tjaey``): ``m3u_accounts``, ``epg_sources``,
``channel_groups``, ``channel_profiles``, ``stream_profiles``.

CHANNELS + STREAMS (bead ``kcxie``, Phase-2): the channels category is gathered
WITH its embedded streams and synced AFTER the config categories, through the
SAME reused ``import_channels`` importer, but with the spike ``xp6mp`` DBA
collision-safe floor applied for the continuous-sync context:

* **Channels (ruling 1a)** — a ``(name, channel_number)`` name match where the
  number is null/absent on BOTH sides is AMBIGUOUS and is surfaced as a
  ``CONFLICT`` (failed-with-reason), never a silent ``ALREADY_EXISTS_IDENTICAL``.
  That fix lives uniformly in ``dbas/importers/channels.py`` (it was a latent
  one-shot bug); this engine simply reuses the corrected importer.
* **Streams (ruling 1b)** — the embedded-stream matcher is FLOORED at Tier-3
  exact-normalized for the sync path. Tier-4 fuzzy (``token_set_ratio``) is
  opt-in per ``SyncTarget`` via ``fuzzy_stream_matching`` (default off); when on,
  a fuzzy hit is flagged LOW-CONFIDENCE in ``report.notes``, never a silent
  ``updated``. The flag threads from the target row into ``import_channels`` via
  its ``allow_fuzzy_stream_match`` parameter — AND into ``run_restore``'s
  parameter of the same name, which is what carries it to the post-create
  placeholder rebind. Both, always: the rebind is a SECOND matcher pass over the
  same archived streams, and for a while it was the half that silently ignored
  the flag (bead ``…-efvyg``), so a target with fuzzy OFF still had a channel
  bound to a wrong-but-similar destination stream while the cycle reported
  success. The floor is a property of the CYCLE, not of one importer.

LOGOS are OPT-IN per target (bead ``7ipq2.1``), not per-cycle-unconditional
(ADR-013 S9): the logos importer carries a DESTRUCTIVE ``clear_existing``
bulk-delete plus a per-logo streaming-upload cost that does not belong in the
default per-cycle slice. The guarded slice this engine ships is exactly the S9
exit path: a ``SyncTarget.sync_logos`` flag (default OFF); when ON the LOGO
category is assembled METADATA-ONLY (never bytes in the plan) and the REUSED
logos importer runs with ``clear_existing`` hard-disabled (the sync path can
NEVER bulk-delete B's logos) and a lazy ``content_provider`` that hydrates each
MISSED logo one at a time (D8 streaming: match first, hydrate misses only, one
payload live at a time). Bead ``…-cfxml``: that gather covers BOTH logo sources
the backup artifact carries — the files under ECM's own ``/config/uploads/logos/``
AND the bytes of every DISPATCHARR-HOSTED logo, fetched from Dispatcharr at
hydration time. Dispatcharr is ECM's source of truth for logos, so before that
a replica received only whatever happened to sit in A's upload directory. The
fetches are wall-clock bounded per fetch and per cycle, because unlike a backup
this runs unattended on a schedule. Bead ``…-xgbjm`` closed the other half: the
bytes crossing is not the same as the CHANNEL-TO-LOGO BINDING crossing, and for
a while only the first did — B's Logo Manager showed the synced logo as UNUSED
while every channel on B carried ``logo_id`` null. The LOGO step now runs the
same post-create reattach pass the archive-restore registry runs, which is what
its LAST position in the registry exists to make possible.

Users NEVER sync (D3). The deferred auto-sync / EPG-download phase is **not** run
per cycle (S9) — the step registry passes a deferred-apply no-op to the
orchestrator.

This module is the ENGINE FUNCTION. The scheduled-task wrapper + manual trigger
(``TaskScheduler`` subclass, overlap guard) is a separate bead (``5gzg5``);
``run_sync`` is kept callable + testable so that wrapper is a thin shell.

Conventions (``docs/style_guide.md``): ``snake_case``; Google-style docstrings;
lazy ``%``-formatted logging; no secrets in any log or report field.
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import posixpath
import time
from pathlib import Path
from typing import Optional

# EXCEPTION TYPES ONLY — this module opens no socket. The destination readback
# gate below has to tell an operator whether B refused the credentials,
# rate-limited the request, or could not be reached at all, and those arrive as
# httpx exception classes raised by the SSRF-pinned client dbas_sync_client
# built. Deliberately `from httpx import <errors>` rather than `import httpx`:
# the name ``httpx`` never enters this namespace, so no client or request class
# is reachable from here and the module's ONLY outbound path stays
# make_remote_client's chokepointed transport.
from httpx import HTTPStatusError, RequestError, TimeoutException  # ssrf-ok: error classes only, no I/O

import journal
from dbas.channel_reattach import (
    reattach_channel_logos,
    reattach_epg_links,
    reattach_profile_memberships,
)
from dbas.preflight import (
    CHANNEL_FK_FIELDS,
    ImportPlan,
    NAME_UNIQUE_CATEGORIES,
    PlanCategory,
)
from dbas.restore_artifact import _SECTION_TO_ENTITY
from dbas.restore_contracts import (
    ChannelReattachMode,
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    RestoreOutcome,
    RestoreReport,
    RollbackLedger,
)
from dbas.restore_orchestrator import (
    ApplyContext,
    ImporterCallable,
    ImporterStep,
    _importer_step_builders,
    _would_create_logo_ids,
    new_restore_id,
    run_restore,
)
from dbas.importers import logos as logos_mod
from dbas.importers.channels import import_channels
from dbas.importers.logos import import_logos
from routers import backup as backup_mod
from routers.backup import (
    BACKUP_SCHEMA_VERSION,
    _collect_credential_values,
    _gather_dispatcharr_sections,
    _redact_credentials_deep,
)
from security.ssrf import SSRFError
from tasks.dbas_sync_client import make_remote_client, sync_freshness_reason

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared NEVER-SYNC constant — code-enforced (mirrors the always-on _REDACT_KEYS).
# ---------------------------------------------------------------------------

# The categories that MUST NEVER appear in a sync plan, unconditionally — no
# settings key, no opt-in (Addendum D D3, ADR-013 S3). Continuous one-way push of
# ``users`` would repeatedly overwrite B's privilege flags / lock out B's
# operator. This constant is imported by the plan assembler AND its test so the
# exclusion is enforced at code level, exactly like the SSRF denylist — not soft
# scope a future edit could erode.
SYNC_NEVER_CATEGORIES: frozenset[str] = frozenset({"users"})

# Credential-class columns that must never be assembled onto the wire either
# (the SyncTarget credential-freshness columns + raw credentials). These are not
# Dispatcharr config sections, but they are named here so the never-sync surface
# is one auditable constant. Defence in depth alongside the _REDACT_KEYS deep
# redactor (D2) that strips secret VALUES from whatever is gathered.
SYNC_NEVER_CREDENTIAL_COLUMNS: frozenset[str] = frozenset(
    {"credentials", "credential_version", "token_revoked_at"}
)

# The CONFIG categories synced every cycle (bead tjaey) — topology config plus
# the USER AGENTS a stream profile's ``user_agent`` FK resolves through (bead
# …-hiacv; ADR-013 S9 lists user agents in the per-cycle config set). Channels /
# streams / logos are bead kcxie; users are never (above) — a user AGENT is a
# Dispatcharr playback-header record, an entirely different entity from a Django
# USER, so adding it does not touch the D3 never-sync set. Each key maps to an
# EntityType via _SECTION_TO_ENTITY (the same table the archive decoder uses),
# and each needs a matching ImporterStep in sync_config_importer_steps(): a
# gathered category with no step is never imported, and a step with no gathered
# category is a no-op, so the two must be edited together.
SYNC_CONFIG_CATEGORIES: frozenset[str] = frozenset(
    {
        "m3u_accounts",
        "epg_sources",
        "channel_groups",
        "channel_profiles",
        "user_agents",
        "stream_profiles",
    }
)

# Bead kcxie adds the CHANNELS category (with embedded streams). It is gathered
# separately from the config sections (channels are not a backup RESTORABLE_SECTION
# the config gather knows) and synced AFTER config, with the collision-safe floor
# (ruling 1a/1b). LOGOS are deliberately NOT here (ADR-013 S9 — destructive
# clear_existing + streaming-upload cost is not a per-cycle slice).
SYNC_CHANNEL_CATEGORIES: frozenset[str] = frozenset({"channels"})

# The UNCONDITIONAL per-cycle sync surface = config + channels. Logos are a
# separate OPT-IN set (below); users are never synced (D3). Exposed as one
# auditable constant.
SYNC_ALL_CATEGORIES: frozenset[str] = SYNC_CONFIG_CATEGORIES | SYNC_CHANNEL_CATEGORIES

# Logos are OPT-IN per SyncTarget (``sync_logos``, default off — bead 7ipq2.1,
# the ADR-013 S9 exit path). Deliberately NOT part of SYNC_ALL_CATEGORIES: the
# unconditional per-cycle set stays exactly what S9 ratified, and the logo
# slice only runs for a target whose operator opted in. When it runs it is
# NEVER destructive (clear_existing is hard-disabled in the sync logos step).
SYNC_LOGO_CATEGORIES: frozenset[str] = frozenset({"logos"})


# ---------------------------------------------------------------------------
# Live-source plan reader — gather LOCAL config -> redact -> ImportPlan.
# ---------------------------------------------------------------------------


def _assert_no_never_sync(section_key: str) -> None:
    """Fail-closed guard: a never-sync category must never reach plan assembly.

    The config-category loop already excludes ``users`` by construction (it is
    not in :data:`SYNC_CONFIG_CATEGORIES`), but this explicit guard makes the D3
    invariant code-enforced at the assembly chokepoint — a future edit that adds
    a category to the gather cannot silently smuggle ``users`` onto the wire.
    """
    if section_key in SYNC_NEVER_CATEGORIES:
        raise AssertionError(
            "never-sync category %r must not be assembled into a sync plan (D3)"
            % section_key
        )


async def _gather_live_channels() -> list[dict]:
    """Gather source-A channels WITH their embedded streams (bead kcxie).

    Channels are not a backup ``RESTORABLE_SECTION`` the config gather knows, so
    this reader fetches them directly from the LOCAL ``get_client()`` (the same
    source the config gather reads). For each channel it resolves the channel's
    stream RECORDS (via :meth:`get_channel_streams`) and embeds them under the
    ``streams`` key the channels importer consumes — the same shape the DBAS
    archive decoder produces. Channel-profile memberships are passed through as
    the channel object carries them.

    Best-effort and fail-soft (mirrors :func:`_gather_dispatcharr_sections`): an
    unavailable local client, or a per-channel stream-fetch error, degrades to an
    empty/partial list and a WARN rather than crashing the sync cycle. No secret
    is logged (only channel names + counts).

    Returns:
        A list of channel records, each a dict with an embedded ``streams`` list
        of full stream dicts. Empty when the local client is unavailable.
    """
    # Resolve the LOCAL client through the routers.backup module (the SAME seam
    # _gather_dispatcharr_sections uses) so the gather is patchable in tests and
    # there is one local-client lookup point.
    client = backup_mod.get_client()
    if not client:
        logger.warning(
            "[SYNC] Local Dispatcharr not connected — channels slice skipped."
        )
        return []

    channels: list[dict] = []
    try:
        page = 1
        while True:
            resp = await client.get_channels(page=page, page_size=1000)
            results = resp.get("results", []) if isinstance(resp, dict) else resp
            page_items = [c for c in (results or []) if isinstance(c, dict)]
            channels.extend(page_items)
            # Stop on the last page (fewer than requested) or a non-paginated shape.
            if not isinstance(resp, dict) or len(page_items) < 1000:
                break
            page += 1
    except Exception as exc:  # noqa: BLE001 - fail-soft: no channels rather than crash
        logger.warning(
            "[SYNC] Could not list source channels: %s", type(exc).__name__
        )
        return []

    # Resolve each channel's embedded stream records so the importer can match
    # them against B's streams. A per-channel failure leaves that channel with no
    # embedded streams (it still syncs its row) rather than aborting the cycle.
    for channel in channels:
        channel_id = channel.get("id")
        if channel_id is None:
            continue
        try:
            streams = await client.get_channel_streams(int(channel_id))
            channel["streams"] = [s for s in (streams or []) if isinstance(s, dict)]
        except Exception as exc:  # noqa: BLE001 - best-effort per channel
            logger.warning(
                "[SYNC] Could not fetch streams for channel '%s' (id=%s): %s",
                channel.get("name") or "<unknown>",
                channel_id,
                type(exc).__name__,
            )
            channel["streams"] = []

    # Convert the source-instance EPG row id into the portable row identity
    # before the channel importer deliberately discards that unsafe FK. A
    # ceiling hit is unresolved for live sync: partial provenance is not proof.
    try:
        await backup_mod._resolve_epg_link_natural_keys(
            client, channels, allow_truncated=False
        )
    except Exception as exc:  # noqa: BLE001 - optional identity enrichment
        logger.warning(
            "[SYNC] Could not resolve source channel EPG identities: %s",
            type(exc).__name__,
        )
    logger.info(
        "[SYNC] Gathered %d source channel(s) with embedded streams.", len(channels)
    )
    return channels


# The private key a sync-assembled logo record uses to remember which file
# (relative to <CONFIG_DIR>/uploads/logos) backs it. Consumed ONLY by
# :func:`_load_logo_content_b64`; never a secret, never logged, never uploaded
# (the importer reads name/filename/size/content_b64 — this key is inert there).
_LOGO_REL_KEY = "_ecm_logo_rel"

# The sibling key for a logo whose bytes only DISPATCHARR can supply (bead
# …-cfxml): it names the SOURCE logo id to fetch, exactly as the local key names
# a file to read. A record carries one or the other, never both. Also inert in
# the importer, and deliberately NOT the logo's ``url`` — a Dispatcharr-local
# path is a path, and paths are a leak class in this module.
_LOGO_FETCH_ID_KEY = "_ecm_logo_fetch_id"

# Wall-clock bound on ONE Dispatcharr logo-byte fetch. ``DispatcharrClient``
# forwards ``timeout=None`` to httpx, which means NO timeout rather than "the
# client default", so without this an unanswered logo request stalls a SCHEDULED
# cycle indefinitely.
_LOGO_FETCH_TIMEOUT_SECONDS = 30.0

# Wall-clock budget for ALL logo-byte fetches in ONE cycle. The backup builder
# bounds its equivalent by file count and byte total but has no wall-clock bound
# at all (open bead …-sj32h); an unattended, recurring task needs one, because
# nobody is watching it run long. Spending the budget is not data loss: the
# logos already uploaded MATCH on the next cycle, so each cycle makes progress
# and the target converges. A count cap would not — it would truncate the same
# tail every cycle, forever.
_LOGO_FETCH_BUDGET_SECONDS = 300.0


def _sync_logos_dir() -> Path:
    """The local logo source dir — resolved through ``routers.backup`` at call
    time so tests patching ``backup_mod.CONFIG_DIR`` steer both the gather and
    the lazy content loader with one seam (the SAME dir the backup builder
    archives)."""
    return Path(backup_mod.CONFIG_DIR) / "uploads" / "logos"


def _local_logo_records(metadata: dict) -> list[dict]:
    """Metadata-only records for the files under ECM's OWN uploads/logos dir."""
    records: list[dict] = []
    for meta in metadata.get("logos") or []:
        rel = meta.get("filename")
        if not isinstance(rel, str) or not rel:
            continue
        basename = posixpath.basename(rel)
        if not basename:
            continue
        record: dict = {
            # Decoder-parity shape: display name (correlated) or basename-stem.
            "name": basename.rsplit(".", 1)[0],
            "filename": basename,
            "size": meta.get("size_bytes"),
            _LOGO_REL_KEY: rel,
        }
        source_id = meta.get("id")
        if isinstance(source_id, int) and not isinstance(source_id, bool):
            record["id"] = source_id
        display_name = meta.get("name")
        if isinstance(display_name, str) and display_name.strip():
            record["name"] = display_name
        records.append(record)
    return records


def _hosted_logo_records(
    hosted_logos: list[dict], *, taken_filenames: set[str]
) -> list[dict]:
    """Metadata-only records for the DISPATCHARR-HOSTED logos (bead …-cfxml).

    A hosted logo's ``url`` names a path inside Dispatcharr's own volume, so its
    image bytes exist ONLY on the source instance and only Dispatcharr can
    supply them. Reuses the backup builder's judgement calls verbatim
    (:func:`routers.backup._dispatcharr_hosted_logos` selects the input,
    :func:`routers.backup._archived_logo_filename` /
    :func:`routers.backup._unique_logo_filename` for a filename the importer's
    own validator will accept) so producer and consumer cannot drift apart.

    Records stay METADATA-ONLY, exactly like the local ones: the bytes hydrate
    lazily per MISSED logo through :func:`_load_logo_content_b64`, which fetches
    them one at a time. No ``size`` is declared — it is not known until the
    fetch — which is fine: the importer's authoritative post-decode cap still
    applies.

    Args:
        hosted_logos: the Dispatcharr-HOSTED subset of the source logo rows.
        taken_filenames: filenames the local records already claim. Mutated.
    """
    records: list[dict] = []
    for logo in hosted_logos:
        logo_id = logo["id"]
        basename = backup_mod._archived_logo_filename(logo.get("url"))
        filename = (
            backup_mod._unique_logo_filename(basename, logo_id, taken_filenames)
            if basename is not None
            else None
        )
        if filename is None:
            # Never log the url: it is a path, and paths are a leak class here.
            logger.warning(
                "[SYNC] Logo id=%s has no usable filename; its image bytes were "
                "not gathered.", logo_id,
            )
            continue
        taken_filenames.add(filename)
        record: dict = {
            "name": filename.rsplit(".", 1)[0],
            "filename": filename,
            "id": logo_id,
            _LOGO_FETCH_ID_KEY: logo_id,
        }
        name = logo.get("name")
        if isinstance(name, str) and name.strip():
            record["name"] = name
        records.append(record)
    return records


def _remote_logo_records(
    source_logos: list[dict],
    *,
    mirrored_ids: set[int],
    known_secrets: frozenset,
    known_identities: frozenset,
) -> list[dict]:
    """Metadata-only records for the REMOTE-URL logos (bead …-sgrez).

    THE THIRD STORAGE SHAPE, and on a real XC-sourced instance the only one that
    matters. A provider hands over a ``tvg-logo`` address; Dispatcharr stores the
    URL and never the bytes. Such a logo is neither a file under ECM's own
    ``uploads/logos`` nor Dispatcharr-hosted, so before this bead it produced NO
    PLAN RECORD AT ALL — not a miss, not a failure, nothing. Measured on the
    documentation environment's source A on 2026-08-20: 59 of 60 logos.

    COPIED AS A URL, NOT FETCHED AND REHOSTED. Dispatcharr's Logo model IS
    ``{name, url}``, and the restore importer has re-created exactly this shape
    from exactly this field since bead …-dfkbn
    (:func:`~dbas.importers.logos._create_logo_from_url`); the backup builder
    deliberately does not archive these bytes for the same reason. Rehosting
    would also make B diverge FROM A rather than replicate it — A itself holds
    only the pointer, so if the origin disappears A loses the picture too — and
    it would spend 59 network fetches inside bead …-cfxml's 300s per-cycle
    budget on every unattended cycle, forever.

    CREDENTIAL-BEARING URLS ARE NOT COPIED. Bead …-msqf7 established that a real
    Xtream Codes provider puts the account's username and password in PATH
    SEGMENTS of the addresses it hands out; a logo url comes from the same
    provider on the same instances, so copying one verbatim would re-open that
    hole by a new route. Every candidate url is therefore put through the SAME
    :func:`~routers.backup._scrub_credential_urls` machinery, with the same
    harvested values, and a url the scrub TOUCHES AT ALL is dropped rather than
    carried in its scrubbed form: with its credential segments replaced by the
    sentinel the address no longer resolves, and handing B a logo that silently
    404s is what ``importers.logos`` already rules "strictly worse than an
    honest miss". The record still travels — without a ``url``, without a
    ``filename``, and therefore with no way back — so the importer reports it as
    a NAMED miss with its affected channels instead of the logo vanishing.

    Args:
        source_logos: the source Dispatcharr logo rows.
        mirrored_ids: source ids an ECM-LOCAL file record already claims. Those
            keep the local file (it holds real bytes, which is strictly more
            robust than a pointer); emitting a second record for the same id
            would put two records on one LOGO remap entry, and the loser would
            be skipped ``ALREADY_EXISTS_IDENTICAL`` — a claim of sameness about
            images that are not the same.
        known_secrets: the authenticating half of the harvested credentials.
        known_identities: the identifying half.

    Returns:
        Metadata-only records, each carrying the source ``id`` and display
        ``name``, and the ``url`` only when it is safe to hand to the replica.
    """
    records: list[dict] = []
    for logo in source_logos:
        logo_id = logo.get("id")
        if not isinstance(logo_id, int) or isinstance(logo_id, bool):
            continue
        url = logos_mod.remote_logo_url(logo)
        if url is None:
            continue  # ECM-local or Dispatcharr-hosted — the other two shapes.
        if logo_id in mirrored_ids:
            continue
        name = logo.get("name")
        record: dict = {
            "id": logo_id,
            "name": (
                name if isinstance(name, str) and name.strip()
                # Never the url or its basename — the label is operator-facing
                # and reaches B as the created row's name. The id is stable, so
                # the next cycle's tier-2 name match still finds it.
                else "logo %d" % logo_id
            ),
        }
        if backup_mod._scrub_credential_urls(url, known_secrets, known_identities):
            # Never log the url itself — it is the thing that carries the
            # credential. The name is the operator-facing identifier.
            logger.warning(
                "[SYNC] Logo '%s' has a credential-bearing address; it was NOT "
                "copied to the destination and is reported as a miss.",
                record["name"],
            )
        else:
            record["url"] = url
        records.append(record)
    return records


def _drop_superseded_local_logos(
    local_records: list[dict], hosted_source_ids: set[int]
) -> list[dict]:
    """Drop local records a Dispatcharr-hosted record supersedes.

    ``_build_source_logo_index`` correlates a file in ECM's
    ``/config/uploads/logos/`` to a Dispatcharr logo BY BASENAME and stamps that
    logo's ``id`` onto it. If the hosted record for the same id also travels,
    TWO records claim ONE source id: the first to be imported registers the LOGO
    remap, and the second resolves through it and is skipped
    ``ALREADY_EXISTS_IDENTICAL`` — a claim of sameness about bytes that are not
    the same.

    Dispatcharr is ECM's source of truth for logos (PO decision, 2026-08-04), so
    the hosted record wins and the ECM-local file — a mirror that on the live
    instance is months stale — is dropped. This mirrors the ruling
    :func:`routers.backup._drop_superseded_local_logos` makes for the artifact,
    with one deliberate difference: the backup drops only for ids a fetch
    ACTUALLY returned, and sync cannot know that at gather time because the
    fetch is lazy. A failed fetch therefore becomes an honest reported miss
    rather than a silent upload of stale bytes, which is the safe direction — a
    miss is visible to the operator and an unnoticed stale logo is not.
    """
    if not hosted_source_ids:
        return local_records
    kept = [
        record for record in local_records
        if not (
            isinstance(record.get("id"), int)
            and record["id"] in hosted_source_ids
        )
    ]
    dropped = len(local_records) - len(kept)
    if dropped:
        logger.info(
            "[SYNC] Dropped %d ECM-local logo file(s) superseded by the "
            "authoritative Dispatcharr bytes.", dropped,
        )
    return kept


async def _gather_live_logos(
    *,
    known_secrets: frozenset = frozenset(),
    known_identities: frozenset = frozenset(),
) -> list[dict]:
    """Gather source-A logos as METADATA-ONLY records (bead 7ipq2.1 — D8).

    THREE sources, which is every storage shape a Dispatcharr logo can have,
    reusing the backup builder's and the restore importer's seams rather than
    reimplementing them:

    * the files under ECM's OWN ``/config/uploads/logos/``
      (:func:`routers.backup._gather_logo_binary_subtree`), correlated to the
      source Dispatcharr logo ``id``/``name`` by URL basename, and
    * every DISPATCHARR-HOSTED logo (:func:`_hosted_logo_records`). Dispatcharr
      is ECM's source of truth for logos, so on a normal install this is where
      the real set lives and ECM's own upload dir holds at most a stale mirror.
      Before bead …-cfxml the sync gather read only the first source, so a
      replica received whatever happened to sit in A's upload directory — on the
      live instance, two files from March.
    * every REMOTE-URL logo (:func:`_remote_logo_records`, bead …-sgrez). The
      first two shapes are the two the ARTIFACT carries as bytes; a logo whose
      url is an absolute http(s) address is in neither, and the artifact carries
      it as an ADDRESS instead, which the importer re-creates from. The gather
      read only the byte-bearing halves, so on an XC-sourced instance — where
      this is 59 logos in 60 — the LOGO category was very nearly empty.

    One Dispatcharr listing serves both concerns (the id correlation and the
    hosted set), the same lifetime the backup builder gives them. A logo whose
    bytes both sources claim resolves to the hosted record
    (:func:`_drop_superseded_local_logos`).

    The records mirror the archive decoder's shape
    (``name``/``filename``/``size``/``id``) with ONE deliberate difference:
    **no** ``content_b64``. Bytes are hydrated lazily, one MISSED logo at a
    time, by :func:`_load_logo_content_b64` inside the importer loop —
    assembling every logo's base64 into the plan up front would hold the whole
    logo set in memory and defeat D8.

    Args:
        known_secrets: the authenticating half of the credential values
            harvested off the RAW gather (bead …-msqf7). A remote logo url is a
            provider-supplied address and can carry them, so it is scrubbed
            through the same machinery every other url on the wire is.
        known_identities: the identifying half.

    Returns:
        Metadata-only logo records; empty when no source yields anything.
    """
    try:
        source_logos = await backup_mod._fetch_source_logos()
    except Exception as exc:  # noqa: BLE001 - the listing is best-effort
        logger.warning("[SYNC] Could not list source logos: %s", type(exc).__name__)
        source_logos = []
    source_index = backup_mod._build_source_logo_index(source_logos)

    try:
        _entries, metadata, _url_mappings = backup_mod._gather_logo_binary_subtree(
            source_index
        )
    except Exception as exc:  # noqa: BLE001 - fail-soft: no logos rather than crash
        logger.warning("[SYNC] Could not enumerate source logos: %s", exc)
        metadata = {"logos": []}

    # Supersede FIRST, then name: a local file the hosted set replaces must not
    # still be holding the filename its own replacement wants, or the hosted
    # record ends up with an id-suffixed name that matches nothing on B (the
    # importer's tier-3 file match keys on the basename).
    hosted_logos = backup_mod._dispatcharr_hosted_logos(source_logos)
    local_records = _drop_superseded_local_logos(
        _local_logo_records(metadata), {logo["id"] for logo in hosted_logos}
    )
    taken = {record["filename"] for record in local_records}
    hosted_records = _hosted_logo_records(hosted_logos, taken_filenames=taken)
    # The remote set is the complement of the hosted one, so it cannot collide
    # with a hosted record; it CAN collide with an ECM-local file that
    # correlates to the same source id by basename, and the local file wins
    # there (see _remote_logo_records' ``mirrored_ids``).
    remote_records = _remote_logo_records(
        source_logos,
        mirrored_ids={
            record["id"] for record in local_records
            if isinstance(record.get("id"), int)
        },
        known_secrets=known_secrets,
        known_identities=known_identities,
    )
    records = local_records + hosted_records + remote_records

    logger.info(
        "[SYNC] Gathered %d source logo record(s) (metadata-only): %d local "
        "file(s), %d Dispatcharr-hosted, %d remote-url.",
        len(records), len(local_records), len(hosted_records),
        len(remote_records),
    )
    return records


async def _fetch_logo_content_b64(logo_id: int) -> Optional[str]:
    """Fetch ONE Dispatcharr-hosted logo's bytes and return them base64 (D8).

    The hosted half of :func:`_load_logo_content_b64`. Wall-clock bounded per
    fetch (:data:`_LOGO_FETCH_TIMEOUT_SECONDS`) because the client forwards
    ``timeout=None`` to httpx, which disables the timeout outright. Fails soft
    to ``None`` — the importer surfaces a per-logo, path-free VALIDATION_ERROR
    and counts the logo as a miss.
    """
    client = backup_mod._safe_get_client()
    if not client:
        logger.warning(
            "[SYNC] No Dispatcharr client; logo id=%s could not be hydrated.",
            logo_id,
        )
        return None
    try:
        data = await asyncio.wait_for(
            client.fetch_logo_image(logo_id), timeout=_LOGO_FETCH_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[SYNC] Timed out fetching image bytes for logo id=%s.", logo_id
        )
        return None
    except Exception as exc:  # noqa: BLE001 - one logo must never fail a cycle
        # Only the exception TYPE: an httpx error's text embeds the full URL.
        logger.warning(
            "[SYNC] Could not fetch image bytes for logo id=%s: %s",
            logo_id, type(exc).__name__,
        )
        return None
    if not data:
        return None
    try:
        return base64.b64encode(data).decode("ascii")
    finally:
        # Release the payload before the next logo is fetched (D8).
        data = None  # noqa: F841 - intentional release of the fetched payload


async def _load_logo_content_b64(record: dict) -> Optional[str]:
    """Lazily supply ONE logo's base64 payload (the D8 hydration seam).

    The ``content_provider`` handed to the reused logos importer: called only
    for a MISSED logo, immediately before validation+upload, so at most one
    logo's payload is ever live. A record names EITHER a file under ECM's own
    logos dir or a DISPATCHARR-HOSTED logo id, and this dispatches accordingly.

    The local branch is containment-guarded: the record's relative path must
    resolve INSIDE the logos dir (belt-and-braces — the rel paths come from our
    own enumeration, but the record travelled through the plan). Returns
    ``None`` on any failure (the importer surfaces a per-logo, path-free
    VALIDATION_ERROR).
    """
    rel = record.get(_LOGO_REL_KEY)
    if not isinstance(rel, str) or not rel:
        fetch_id = record.get(_LOGO_FETCH_ID_KEY)
        if isinstance(fetch_id, int) and not isinstance(fetch_id, bool):
            return await _fetch_logo_content_b64(fetch_id)
        return None
    logos_dir = _sync_logos_dir().resolve()
    try:
        path = (logos_dir / rel).resolve()
        path.relative_to(logos_dir)  # raises ValueError if it escaped
    except (ValueError, OSError):
        logger.warning(
            "[SYNC] Refused logo content read outside the logos dir for '%s'.",
            record.get("name") or "<unknown>",
        )
        return None
    try:
        data = await asyncio.to_thread(path.read_bytes)
    except OSError as exc:
        logger.warning(
            "[SYNC] Could not read logo content for '%s': %s",
            record.get("name") or "<unknown>",
            exc.__class__.__name__,  # class only — never the path-bearing message
        )
        return None
    return base64.b64encode(data).decode("ascii")


async def build_live_source_plan(*, include_logos: bool = False) -> ImportPlan:
    """Gather the LOCAL source-A config, redact it, and assemble an ImportPlan.

    Reuses the backup gather (:func:`_gather_dispatcharr_sections`, which reads
    the LOCAL ``get_client()`` itself) and the shared
    :func:`_redact_credentials_deep` deep redactor — the SAME topology-only
    pipeline the redacted backup artifact uses (Addendum D D2: no secrets on the
    wire). Each gathered section maps to its :class:`EntityType` via
    :data:`_SECTION_TO_ENTITY`.

    The plan's manifest carries ``schema_version = BACKUP_SCHEMA_VERSION`` — the
    orchestrator's pre-flight runs the SAME .17 schema-version gate as archive
    restore and refuses a plan without it (spike ``xp6mp`` empirical find).

    Args:
        include_logos: the per-target ``sync_logos`` opt-in (bead 7ipq2.1 —
            ADR-013 S9 exit path). ``True`` appends a METADATA-ONLY ``LOGO``
            category LAST covering BOTH logo sources — ECM's own upload dir and
            the Dispatcharr-hosted set (bead …-cfxml) — with no ``content_b64``
            in the plan (D8; bytes hydrate lazily per missed logo at import
            time). ``False`` (default) keeps logos out of the plan entirely.

    Returns:
        An :class:`ImportPlan` of the redacted config categories PLUS the channels
        category (with embedded streams, gathered separately — bead kcxie) PLUS,
        only when ``include_logos`` is set, the metadata-only logos category.
        The ``users`` category is NEVER present (D3).
    """
    # Gather ONLY the config categories (never users; never channels/streams/
    # logos — those are other beads). _gather_dispatcharr_sections owns the LOCAL
    # client lookup and returns a ``{"_warning": ...}`` dict (no config rows) when
    # the local Dispatcharr is unavailable — never a crash.
    sections = await _gather_dispatcharr_sections(set(SYNC_CONFIG_CATEGORIES))

    # Harvest the credential VALUES off the RAW gather, BEFORE anything is
    # redacted (bead …-msqf7). The key-name denylist cannot see a credential that
    # is a PATH SEGMENT of a stream url — a real Xtream Codes provider puts the
    # account's username and password there in every one of its stream URLs — but
    # the values are right here in ``m3u_accounts``, so they can be matched
    # LITERALLY rather than guessed at structurally.
    #
    # The union of every account's credentials is used, not the owning account's:
    # the FK association IS available at this point (``m3u_account`` is still on
    # each raw stream row; it is only dropped later, at payload-build time), but
    # depending on it would leave a stream whose FK is null or unresolvable
    # unprotected, and one provider's password leaking through another provider's
    # URL is the same defect.
    known_secrets, known_identities = _collect_credential_values(sections)

    # Redact to topology-only BEFORE the rows enter the plan — one shared denylist,
    # every category, no plaintext path (D2). preserve_keys is intentionally empty:
    # sync NEVER carries credentials, unlike the opt-in migration artifact (u81kh).
    redacted_sections = _redact_credentials_deep(
        sections,
        preserve_keys=frozenset(),
        known_secrets=known_secrets,
        known_identities=known_identities,
    )

    categories: list[PlanCategory] = []
    for section_key, entity_type in _SECTION_TO_ENTITY.items():
        if section_key not in SYNC_CONFIG_CATEGORIES:
            # Skip any decoder section outside this bead's config scope (the
            # decoder table may list more than we sync).
            continue
        _assert_no_never_sync(section_key)
        rows = redacted_sections.get(section_key) if isinstance(redacted_sections, dict) else None
        entities = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        categories.append(PlanCategory(entity_type=entity_type, entities=entities))

    # CHANNELS (bead kcxie) — gathered separately (not a config RESTORABLE_SECTION)
    # WITH embedded streams, then redacted through the SAME deep denylist (D2). The
    # redactor strips only secret-NAMED keys (password/token/...); stream ``url``
    # — the matcher's Tier-1 identity — is NOT a redact key and survives, so the
    # stream floor still works on the wire. The CHANNEL category is appended LAST
    # so it applies after every config dependency (groups/profiles/M3U) is created.
    #
    # ``known_secrets`` is what stops "survives" meaning "carries the provider's
    # username and password" for an XC account (bead …-msqf7): the credential
    # SEGMENTS of the url become the sentinel and the rest of the address — host,
    # kind marker, stream id — crosses intact, so the matcher keeps a usable
    # identity and the operator keeps a visible one.
    channels = await _gather_live_channels()
    redacted_channels = _redact_credentials_deep(
        {"channels": channels},
        preserve_keys=frozenset(),
        known_secrets=known_secrets,
        known_identities=known_identities,
    )
    channel_rows = redacted_channels.get("channels") if isinstance(redacted_channels, dict) else None
    channel_entities = (
        [c for c in channel_rows if isinstance(c, dict)] if isinstance(channel_rows, list) else []
    )
    categories.append(
        PlanCategory(entity_type=EntityType.CHANNEL, entities=channel_entities)
    )

    # LOGOS (bead 7ipq2.1) — OPT-IN per target, appended AFTER channels (the
    # same hard Phase-2 ordering the restore registries use: logos LAST, so the
    # CHANNEL remap is populated for the logo-miss affected-channel drill-down).
    # METADATA-ONLY records: no content_b64 ever enters the plan (D8) — bytes
    # hydrate lazily per missed logo via _load_logo_content_b64.
    #
    # The harvested credential values are threaded in because a REMOTE-URL logo
    # record carries an ADDRESS (bead …-sgrez), and a provider-supplied address
    # is exactly where bead …-msqf7 found this operator's username and password.
    # The gather scrubs each candidate url through the same machinery rather
    # than the records being redacted afterwards, because the right answer for a
    # credential-bearing logo url is to DROP it (a sentinel-bearing address 404s
    # on the replica), not to carry a scrubbed one.
    if include_logos:
        logo_records = await _gather_live_logos(
            known_secrets=known_secrets, known_identities=known_identities,
        )
        categories.append(
            PlanCategory(entity_type=EntityType.LOGO, entities=logo_records)
        )

    plan = ImportPlan(
        # The schema_version stamp is the load-bearing manifest field: pre-flight
        # refuses a plan without it (preflight.py -> validate_restore_schema_version).
        manifest={"schema_version": BACKUP_SCHEMA_VERSION},
        categories=categories,
    )
    logger.info(
        "[SYNC] Assembled redacted source plan: %s",
        ", ".join("%s=%d" % (c.entity_type.value, len(c.entities)) for c in categories),
    )
    return plan


# ---------------------------------------------------------------------------
# Source-side name-conflict tolerance — sync degrades PER-ITEM, unlike backup/
# restore's all-or-nothing preflight refusal.
# ---------------------------------------------------------------------------


def _split_name_conflicts(
    plan: ImportPlan,
) -> tuple[ImportPlan, dict[EntityType, list[dict]]]:
    """Dedup name-unique categories so a source-side duplicate degrades to a
    per-item CONFLICT instead of preflight's all-or-nothing plan refusal.

    ``dbas.preflight._validate_unique_names`` refuses the ENTIRE plan when ANY
    :data:`~dbas.preflight.NAME_UNIQUE_CATEGORIES` category carries two entities
    sharing a (trimmed, case-insensitive) name — correct for backup/restore,
    where a half-applied one-shot snapshot is worse than no restore at all
    (ADR / Dispatcharr has no DB transactions). Continuous cross-instance sync
    has the opposite failure mode: one duplicated name anywhere in the source
    (e.g. two channel groups both named "World Cup 2026") must not blank out
    every OTHER category's diff. This mirrors the per-item tolerance
    ``dbas/importers/channels.py`` already applies to an unrelated ambiguity
    (``_is_ambiguous_null_key`` — a name collision with a null channel_number on
    both sides is surfaced as a CONFLICT for that one channel, not a plan
    refusal) rather than inventing a second tolerance model.

    For each category in :data:`NAME_UNIQUE_CATEGORIES` — the SAME set
    preflight checks, imported directly so the two lists can never drift apart
    on which categories they cover — entities are scanned in archive order
    using preflight's EXACT normalization (``name.strip().lower()``; a missing,
    non-string, or empty name is left alone and never flagged, matching
    ``_validate_unique_names``). The first entity to claim a given name is kept;
    every later entity with the same name is removed from the returned plan and
    recorded in the excluded mapping so the caller can surface it as a CONFLICT
    once the (now preflight-safe) plan has been restored.

    Dropping a duplicate is not enough on its own: a CHANNEL entity elsewhere in
    the plan may carry a ``channel_group_id`` / ``stream_profile_id`` (the two
    fields :data:`~dbas.preflight.CHANNEL_FK_FIELDS` points at) that referenced
    the EXCLUDED duplicate's source id. Left alone, that reference would dangle
    and ``dbas.preflight._validate_fk_references`` would refuse the WHOLE
    (now-deduped) plan again — reproducing the exact all-or-nothing refusal this
    function exists to avoid, just via a different validator. So this function
    also builds a source-id -> kept-id remap for every FK-target category
    (:data:`~dbas.preflight.CHANNEL_FK_FIELDS` values) and rewrites any CHANNEL
    entity's matching FK field that pointed at an excluded id, so it now points
    at the surviving same-named entity instead.

    Args:
        plan: The freshly-assembled live-source plan, not yet preflighted.

    Returns:
        A tuple of ``(deduped_plan, excluded)`` where ``excluded`` maps each
        affected :class:`EntityType` to the list of archive entity dicts that
        were dropped. ``deduped_plan`` cannot trigger
        ``PreflightProblemKind.DUPLICATE_UNIQUE_NAME`` — every name-unique
        category now carries at most one entity per normalized name — and any
        CHANNEL FK that referenced a dropped duplicate has been remapped onto
        the entity that survived, so it cannot trigger
        ``PreflightProblemKind.UNRESOLVED_FK_REFERENCE`` either.
    """
    excluded: dict[EntityType, list[dict]] = {}
    # Excluded (dropped) source id -> surviving (kept, same-name) source id, per
    # FK-target entity type. Only populated for categories CHANNEL_FK_FIELDS can
    # point at (currently CHANNEL_GROUP / STREAM_PROFILE) — imported directly
    # from preflight so this can never drift from the FK fields preflight
    # actually validates.
    fk_remap: dict[EntityType, dict[int, int]] = {}
    new_categories: list[PlanCategory] = []
    for cat in plan.categories:
        if cat.entity_type not in NAME_UNIQUE_CATEGORIES:
            new_categories.append(cat)
            continue
        seen: set[str] = set()
        kept_id_by_name: dict[str, int] = {}
        kept: list[dict] = []
        for entity in cat.entities:
            raw = entity.get("name")
            if not isinstance(raw, str):
                kept.append(entity)
                continue
            key = raw.strip().lower()
            if not key:
                kept.append(entity)
                continue
            if key in seen:
                excluded.setdefault(cat.entity_type, []).append(entity)
                excluded_id = entity.get("id")
                kept_id = kept_id_by_name.get(key)
                if excluded_id is not None and kept_id is not None:
                    fk_remap.setdefault(cat.entity_type, {})[int(excluded_id)] = kept_id
                continue
            seen.add(key)
            kept.append(entity)
            kept_id = entity.get("id")
            if kept_id is not None:
                kept_id_by_name[key] = int(kept_id)
        new_categories.append(
            PlanCategory(
                entity_type=cat.entity_type, entities=kept, selected=cat.selected
            )
        )

    if fk_remap:
        for index, cat in enumerate(new_categories):
            if cat.entity_type != EntityType.CHANNEL:
                continue
            rewritten: list[dict] = []
            for channel in cat.entities:
                updated_channel = channel
                for field, target_type in CHANNEL_FK_FIELDS.items():
                    field_remap = fk_remap.get(target_type)
                    if not field_remap:
                        continue
                    ref = updated_channel.get(field)
                    if ref is None:
                        continue
                    try:
                        ref_id = int(ref)
                    except (TypeError, ValueError):
                        continue
                    if ref_id in field_remap:
                        if updated_channel is channel:
                            updated_channel = dict(channel)
                        updated_channel[field] = field_remap[ref_id]
                rewritten.append(updated_channel)
            new_categories[index] = PlanCategory(
                entity_type=cat.entity_type, entities=rewritten, selected=cat.selected
            )

    deduped_plan = plan.model_copy(update={"categories": new_categories})
    return deduped_plan, excluded


def _apply_name_conflict_details(
    report: RestoreReport, excluded: dict[EntityType, list[dict]]
) -> None:
    """Surface each entity :func:`_split_name_conflicts` dropped as a CONFLICT.

    Mirrors ``dbas/importers/channels.py``'s ambiguous-collision shape exactly
    (``cat.failed += 1`` + one :class:`FailureDetail` per entity) so the sync
    report's per-entity conflict UX is uniform regardless of which tolerance
    path produced it. Applied UNCONDITIONALLY — dry-run and apply alike — so a
    dry-run preview surfaces the conflict before an operator ever confirms
    apply, matching the channels.py precedent (no ``is_dry_run`` guard there
    either).

    Args:
        report: The :class:`RestoreReport` returned by :func:`~dbas.
            restore_orchestrator.run_restore` for the deduped plan.
        excluded: The mapping :func:`_split_name_conflicts` returned — entity
            type -> the archive entities it removed from the plan.
    """
    for entity_type, entities in excluded.items():
        cat = report.category(entity_type)
        for entity in entities:
            # Guaranteed a non-empty str by _split_name_conflicts (only entities
            # with a valid duplicate name are ever collected here).
            label = str(entity.get("name"))
            source_id = entity.get("id")
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.CONFLICT,
                    label=label,
                    message=(
                        "duplicate %s name in source archive: '%s' — a "
                        "same-named entity was kept and synced; this one was "
                        "skipped to avoid ambiguity." % (entity_type.value, label)
                    ),
                    source_export_id=int(source_id) if source_id is not None else None,
                )
            )
        logger.warning(
            "[SYNC] %d %s name-conflict(s) resolved: kept first, skipped %d "
            "duplicate(s).",
            len(entities),
            entity_type.value,
            len(entities),
        )
        report.notes.append(
            "%d %s name-conflict(s) resolved: kept first, skipped %d "
            "duplicate(s)." % (len(entities), entity_type.value, len(entities))
        )


# ---------------------------------------------------------------------------
# Config-only importer step registry — REUSE the orchestrator's builders.
# ---------------------------------------------------------------------------


def _sync_channels_step(*, allow_fuzzy_stream_match: bool) -> ImporterCallable:
    """Build the CHANNELS importer step for the sync path (bead kcxie).

    Unlike the orchestrator's shared ``_channels`` builder, this one threads the
    per-``SyncTarget`` ``allow_fuzzy_stream_match`` flag into ``import_channels``
    so the embedded-stream matcher is FLOORED at Tier-3 exact-normalized unless
    the target explicitly opted into fuzzy (spike ``xp6mp`` ruling 1b). The
    channel-row collision-safe floor (ruling 1a) is inside ``import_channels``
    itself, so it always applies regardless of this flag.
    """

    async def _channels(ctx: ApplyContext) -> list[dict] | None:
        cat = ctx.plan.category(EntityType.CHANNEL)
        await import_channels(
            archive_channels=list(cat.entities) if cat else [],
            client=ctx.client,
            selected=bool(cat.selected) if cat else False,
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            allow_fuzzy_stream_match=allow_fuzzy_stream_match,
            created_source_ids=ctx.created_channel_source_ids,
        )
        await reattach_epg_links(
            client=ctx.client,
            report=ctx.report,
            remap=ctx.remap,
            archive_channels=list(cat.entities) if cat else [],
            created_source_ids=ctx.created_channel_source_ids,
            mode=ChannelReattachMode.OVERWRITE,
            is_dry_run=ctx.is_dry_run,
            allow_channel_tvg_id_fallback=False,
        )
        # Channel-profile MEMBERSHIP (bead …-38c5a). Dispatcharr adds every new
        # channel to EVERY profile ENABLED (0.29.0
        # ``apps/channels/api_views.py`` — ``channel_profile_ids`` omitted means
        # "all profiles", and ``ChannelProfileMembership.enabled`` defaults
        # True), so a profile that exists to SHOW SIX CHANNELS AND HIDE
        # FIFTY-THREE arrives on the replica showing all fifty-nine unless the
        # source's selection is re-asserted here.
        #
        # This is the same pass the archive-restore registry runs
        # (``restore_orchestrator``); it was simply never wired into the sync
        # path, so the enablement was gathered (``ChannelProfileSerializer.
        # channels`` is the ENABLED-channel list on 0.28.2 AND 0.29.0) and then
        # dropped on the floor. Measured 2026-08-20 on 0.29.0: source
        # 'Kids & Family' 6/59 enabled, replica 59/59, from a cycle that
        # reported ``success, created 134, failed 0``.
        #
        # Gated on the CHANNEL_PROFILE category exactly as the restore registry
        # gates it: with profiles absent from the plan no archived profile
        # resolves through the remap, and re-asserting a selection this cycle
        # was never asked to touch would be the widening failure's mirror image.
        #
        # Runs on a DRY RUN too, PATCHing nothing — a preview that cannot say
        # "this cycle is about to expose 53 channels your profile hides" is
        # silent at the only point the operator can still act.
        profile_cat = ctx.plan.category(EntityType.CHANNEL_PROFILE)
        if profile_cat is not None and profile_cat.selected:
            await reattach_profile_memberships(
                client=ctx.client,
                report=ctx.report,
                remap=ctx.remap,
                archive_profiles=list(profile_cat.entities),
                archive_channels=list(cat.entities) if cat else [],
                created_source_ids=ctx.created_channel_source_ids,
                is_dry_run=ctx.is_dry_run,
            )
        return None

    return _channels


class _LogoFetchBudget:
    """Per-cycle wall-clock bound on the Dispatcharr logo-byte fetches.

    Sync is a SCHEDULED, unattended task, so "however long the logo set takes"
    is not an acceptable answer the way it is for an operator-initiated backup
    (whose own missing wall-clock bound is open bead …-sj32h). One budget is
    created per cycle by :func:`_sync_logos_step` and wraps the content
    provider; it starts when the FIRST fetch does, so a cycle whose logos all
    match on B never starts a clock at all.

    Only FETCHES are bounded. Reading a local file is not a network call and was
    never the unbounded risk.

    Spending the budget is not data loss. The logos already uploaded MATCH on
    the next cycle, so the next cycle spends its budget on the ones that are
    still missing and the target converges. A count cap would truncate the same
    tail every cycle instead, forever.
    """

    def __init__(self, seconds: Optional[float] = None) -> None:
        self._seconds = (
            _LOGO_FETCH_BUDGET_SECONDS if seconds is None else seconds
        )
        self._deadline: Optional[float] = None
        self._exhausted = False

    async def load(self, record: dict) -> Optional[str]:
        """The bounded ``content_provider`` — one logo's base64 payload."""
        if record.get(_LOGO_FETCH_ID_KEY) is not None:
            now = time.monotonic()
            if self._deadline is None:
                self._deadline = now + self._seconds
            elif now >= self._deadline:
                if not self._exhausted:
                    self._exhausted = True
                    logger.warning(
                        "[SYNC] Logo fetch budget (%.0fs) spent; the remaining "
                        "Dispatcharr-hosted logos are reported as misses this "
                        "cycle and retried on the next one.", self._seconds,
                    )
                return None
        return await _load_logo_content_b64(record)


def _sync_logos_step() -> ImporterCallable:
    """Build the LOGOS importer step for the sync path (bead 7ipq2.1).

    Two halves, and the replica needs both: ``import_logos`` puts the logo BYTES
    on B, and :func:`~dbas.channel_reattach.reattach_channel_logos` puts the
    channel-to-logo BINDING back (bead …-xgbjm) — without the second, B holds the
    right image files and every channel on it reads ``logo_id`` null.

    Reuses the UNCHANGED ``import_logos`` with two sync-specific bindings:

    * ``clear_existing=False`` — HARD-CODED, not a parameter. The destructive
      bulk-delete pre-step can never fire on the sync path (ADR-013 S9's core
      objection to per-cycle logos); B's existing logos are only ever matched
      against or added to, never cleared.
    * a budgeted ``content_provider`` — the D8 lazy-hydration seam: the plan's
      logo records are metadata-only, and each MISSED logo's bytes are read from
      the local source dir OR fetched from Dispatcharr (bead …-cfxml) one at a
      time inside the importer loop (a matched logo is never hydrated at all).
      The :class:`_LogoFetchBudget` wrapper is built HERE, once per cycle, so
      the wall-clock bound is per-cycle rather than global.

    When the plan carries no LOGO category (the target did not opt in), the
    step is a structural no-op — same single registry serves opted-in and
    opted-out targets, dry-run and apply alike (the kxcjf parity lesson: there
    is exactly ONE list to which a category can be added).
    """
    budget = _LogoFetchBudget()

    async def _logos(ctx: ApplyContext) -> list[dict] | None:
        cat = ctx.plan.category(EntityType.LOGO)
        if cat is None:
            return None  # target did not opt into logo sync — nothing to do.
        channel_cat = ctx.plan.category(EntityType.CHANNEL)
        logo_result = await import_logos(
            archive_logos=list(cat.entities),
            client=ctx.client,
            selected=bool(cat.selected),
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            clear_existing=False,  # NEVER destructive on the sync path.
            archive_channels=list(channel_cat.entities) if channel_cat else [],
            content_provider=budget.load,
        )
        # Channel -> LOGO BINDING (bead …-xgbjm). Bead …-cfxml got the logo
        # BYTES across; the binding stayed behind, so the replica held the right
        # image FILES with no channel using them — B's Logo Manager showed the
        # synced logo as UNUSED while every channel on B carried logo_id null.
        # It is the most VISIBLE difference between primary and replica: it is
        # what an operator sees first on opening B.
        #
        # ``logo_id`` is a SOURCE id, so ``importers/channels.py`` drops it from
        # the create payload (``_NON_REMAPPABLE_FK_KEYS``) — correctly, because
        # forwarding A's id would either 400 or silently bind an unrelated
        # destination row. This is the second half: re-derive the reference on B
        # and PATCH it back. Same pass the archive-restore registry already runs
        # (``restore_orchestrator._logos``), never wired into the sync path —
        # exactly the shape ``reattach_profile_memberships`` had before …-38c5a.
        #
        # WHY IT RUNS HERE, AND WHY THE LOGO STEP STAYS LAST. The pass needs BOTH
        # remap namespaces populated: CHANNEL (filled by the channels step,
        # earlier in this registry) and LOGO (filled by ``import_logos``, three
        # lines up). Last position is what makes that true. Moving LOGO ahead of
        # CHANNEL to bind at create time would break it twice over — the pass
        # would meet an empty CHANNEL remap, and so would the logo-miss
        # drill-down that names the affected channels per missed logo (bead
        # …-cm9bi, ``import_logos(archive_channels=...)``). The ordering is a
        # precondition of this fix, not an obstacle to it.
        #
        # SOURCE-WINS (``OVERWRITE``), matching the EPG-link pass on this same
        # path. ``sync_logos`` is opt-in and defaults OFF (bead …-8gnik owns the
        # control), so the realistic sequence is: cycles run, B gets its lineup,
        # THEN the flag goes on. By then every channel on B already exists and is
        # MATCHED rather than created, so under PRESERVE this pass would bind
        # nothing, on that cycle or any later one, and the new control would look
        # broken. A replica's branding is the source's by definition.
        #
        # Gated on the CHANNEL category as well as LOGO. The pass operates on
        # channels, so it needs the channel population to be meaningful: with
        # channels absent from the plan no archived channel resolves through the
        # remap and every one of them would be classified against an empty one.
        # That mismatch is a live defect on the RESTORE side (bead …-lngo5,
        # unreachable there today and left to that bead); this gate is what keeps
        # the sync path from becoming its second home.
        #
        # Runs on a DRY RUN too, PATCHing nothing and recording no miss — and it
        # counts the logos the preview knows the apply would CREATE (bead
        # …-dgnms): on a fresh replica nothing matches, so that set is the whole
        # population, and without it the preview reports 0 for an apply that
        # binds every channel.
        if (
            cat.selected
            and channel_cat is not None
            and channel_cat.selected
        ):
            await reattach_channel_logos(
                client=ctx.client,
                report=ctx.report,
                remap=ctx.remap,
                archive_channels=list(channel_cat.entities),
                created_source_ids=ctx.created_channel_source_ids,
                mode=ChannelReattachMode.OVERWRITE,
                is_dry_run=ctx.is_dry_run,
                # Coerced defensively: ``import_logos`` is stubbed in several
                # suites, and a stub's return value is not a LogoImportResult.
                would_create_logo_source_ids=_would_create_logo_ids(logo_result),
            )
        return None

    return _logos


def sync_config_importer_steps(
    *, allow_fuzzy_stream_match: bool = False
) -> list[ImporterStep]:
    """The step registry for a sync cycle — config categories + channels + logos.

    Reuses :func:`dbas.restore_orchestrator._importer_step_builders` (the SAME
    callables that back the archive apply + dry-run registries) for the config
    categories so there is no second importer path — including its USER_AGENT-
    first ordering, which both the M3U and stream-profile ``user_agent`` FKs
    depend on (…-9h6cv) — then appends the CHANNELS
    step (bead kcxie) after every config dependency — groups/profiles/M3U — and
    the LOGOS step (bead 7ipq2.1) LAST. The LOGOS step is a structural no-op
    unless the plan carries a LOGO category (the per-target ``sync_logos``
    opt-in), and can never bulk-delete (``clear_existing`` hard-disabled).
    Users are excluded permanently (D3).

    The CHANNELS step threads ``allow_fuzzy_stream_match`` (the per-``SyncTarget``
    ``fuzzy_stream_matching`` flag, default off) into ``import_channels`` so the
    embedded-stream matcher floors at Tier-3 exact-normalized for the sync path
    (spike ``xp6mp`` ruling 1b). The channel-row collision-safe floor (ruling 1a)
    is inside the importer and always applies.

    CRITICAL (ADR-013 S9): the M3U step is registered with ``defers=False`` and
    the orchestrator is given a deferred-apply no-op, so the per-cycle deferred
    auto-sync / EPG-download phase NEVER fires — re-triggering provider auto-sync
    on B every interval is exactly the behaviour S9 forbids. The M3U importer
    still returns its deferred settings; we simply never apply them.
    """
    s = _importer_step_builders()
    return [
        # USER AGENTS FIRST (…-9h6cv, mirroring the restore registry's ordering).
        # A user agent is a leaf — it resolves nothing through the remap — while
        # BOTH the M3U account and the stream profile carry a ``user_agent`` FK
        # that resolves through the USER_AGENT namespace. Running agents last
        # left that namespace empty: every custom-user-agent stream profile was
        # skipped DEPENDENCY_UNRESOLVED (…-hiacv), and an M3U account forwarded
        # A's raw pk, so B answered 400 "Invalid pk" and — M3U_ACCOUNT being a
        # FATAL failure category — the whole cycle rolled back (…-9h6cv).
        # ADR-013 S9 lists user agents in the per-cycle config set;
        # ``user_agents`` is in SYNC_CONFIG_CATEGORIES so the gather feeds this
        # step. Distinct from the USERS category, which stays never-sync (D3).
        ImporterStep(EntityType.USER_AGENT, s["user_agents"]),
        # M3U before EPG (EPG sources resolve their m3u_account FK through the
        # remap M3U writes). defers=False: the deferred auto-sync phase is
        # suppressed.
        ImporterStep(EntityType.M3U_ACCOUNT, s["m3u"], defers=False),
        ImporterStep(EntityType.EPG_SOURCE, s["epg"]),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        # CHANNELS (+ embedded streams) after every config dependency.
        ImporterStep(
            EntityType.CHANNEL,
            _sync_channels_step(allow_fuzzy_stream_match=allow_fuzzy_stream_match),
        ),
        # LOGOS LAST (restore-registry ordering parity). Last position is load
        # bearing in two directions: channels populate the CHANNEL remap that
        # BOTH the logo-miss drill-down (…-cm9bi) and the channel->logo binding
        # pass (…-xgbjm) read, and the binding pass additionally needs the LOGO
        # remap this step's own importer fills. A channel therefore cannot carry
        # a logo id at CREATE time — it is bound afterwards, here — and moving
        # LOGO ahead of CHANNEL to try would break both readers at once.
        # Structurally a no-op unless the plan carries a LOGO category
        # (per-target sync_logos opt-in).
        ImporterStep(EntityType.LOGO, _sync_logos_step()),
    ]


async def _no_deferred_apply(*, deferred: list[dict], client) -> list[dict]:
    """Deferred-apply no-op for the sync path (ADR-013 S9).

    The orchestrator only calls a deferred-apply fn when ``ctx.deferred`` is
    non-empty AND the run is a clean non-dry-run apply. The config step registry
    registers M3U with ``defers=False``, but the M3U importer still RETURNS its
    deferred settings; to guarantee S9 even if a future builder change starts
    collecting them, this fn drops them on the floor and logs.
    """
    if deferred:
        logger.info(
            "[SYNC] Suppressing %d deferred auto-sync setting(s) (ADR-013 S9 — "
            "per-cycle provider auto-sync is not re-triggered on B).",
            len(deferred),
        )
    return []


# ---------------------------------------------------------------------------
# Destination readability (bead …-jqfxm) — the preview must have READ B.
# ---------------------------------------------------------------------------

# The probe endpoint. It must be an AUTHENTICATED read (an unauthenticated
# liveness ping would answer "the box is up" to a question about credentials),
# and it must be cheap — channel groups are a handful of rows even on a large
# instance, and the plan reads them anyway a moment later.
_DESTINATION_PROBE = "get_channel_groups"

# Everything the destination is ASKED, as opposed to told. Only reads need
# watching: a failed WRITE already lands in its category's ``failed`` counter
# and drives the orchestrator's rollback, whereas a failed READ is swallowed by
# every importer's ``except Exception: existing = []`` fallback and silently
# becomes "the destination is empty".
_DESTINATION_READ_PREFIX = "get_"


def _describe_destination_error(exc: BaseException) -> str:
    """Turn a destination-read exception into a sanitized operator sentence.

    Credential hygiene (the same rule ``dispatcharr_client._request`` documents):
    an httpx error's own text can embed the request URL, and that URL can carry a
    token — so this NEVER interpolates ``str(exc)``. Only the HTTP status code
    and the exception's CLASS name reach the message, which is plenty to tell an
    operator whether to fix credentials, wait, or check the network.

    The 401/403 vs 429 split is load-bearing: B's Dispatcharr rate-limits
    ``/api/accounts/token/`` at 3/min per IP, so back-to-back cycles produce 429s
    that have nothing to do with the credentials. Reporting one as the other
    would send an operator to rotate perfectly good passwords (or to wait out a
    limiter that will never clear a genuinely wrong password).
    """
    if isinstance(exc, SSRFError):
        return (
            "the destination is blocked by this instance's outbound SSRF policy"
        )
    if isinstance(exc, HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return (
                "the destination rate-limited this request (HTTP 429) — this is "
                "NOT a credential problem; wait for its limit window to clear "
                "and retry"
            )
        if status in (401, 403):
            return (
                "authentication to the destination was rejected (HTTP %d) — "
                "check the sync target's credentials on this instance" % status
            )
        if status >= 500:
            return "the destination returned a server error (HTTP %d)" % status
        return "the destination returned HTTP %d" % status
    if isinstance(exc, TimeoutException):
        return "the destination did not respond in time (%s)" % type(exc).__name__
    if isinstance(exc, RequestError):
        # Connection refused, DNS failure and a TLS handshake refusal all arrive
        # here as some ConnectError flavour — the class name is the distinction
        # an operator can act on without leaking the URL.
        return "the destination could not be reached (%s)" % type(exc).__name__
    return "the destination could not be read (%s)" % type(exc).__name__


async def destination_read_reason(client) -> Optional[str]:
    """Probe the destination ONCE and return why it is unreadable, or ``None``.

    The fail-closed gate this bead exists for. Deliberately shaped like
    :func:`sync_freshness_reason` — a reason string aborts, ``None`` proceeds —
    because it is the same kind of gate: a precondition checked before any work,
    whose failure must stop the cycle rather than colour a result afterwards.

    Two things it buys beyond honesty:

    * **Fail-fast.** Without it, an unauthenticated cycle runs all seven config
      steps, each of which re-enters ``DispatcharrClient._login`` because no
      access token was ever obtained — seven ``POST /api/accounts/token/`` in a
      few seconds against an endpoint limited to 3/min. Live validation caught
      exactly that: seven 401/429s in B's log for one preview. One probe, one
      login attempt.
    * **No plan.** A cycle that cannot read B never gathers or redacts A's
      config, so an unreachable destination costs nothing.
    """
    probe = getattr(client, _DESTINATION_PROBE, None)
    if probe is None:  # pragma: no cover - defensive; every client has it
        return None
    try:
        await probe()
    except Exception as exc:  # noqa: BLE001 - every failure class is a refusal
        return _describe_destination_error(exc)
    return None


class _ReadObservingClient:
    """Wrap the dest-B client so a FAILED destination read cannot go unnoticed.

    :func:`destination_read_reason` proves the destination was readable when the
    cycle started. It cannot prove every read the cycle then makes succeeded —
    and each importer degrades its own failed read to ``existing = []``, which
    the report renders as "would create N" (a statement about the SOURCE wearing
    the destination's clothes). B restarting mid-cycle, one endpoint answering
    500, or a token expiring against a rate-limited refresh all land there.

    So the client handed to the orchestrator marks the REPORT the moment a read
    raises. Nothing is suppressed or retried — the importers' own fallbacks
    still run and the run still completes — but the report carries
    ``destination_unreadable`` from that moment on, which is what stops a
    preview built on a half-read destination from unlocking Apply.

    IT MARKS THE REPORT DURING THE RUN, NOT AFTER IT (bead ``…-bj442``). This
    used to collect the failures in a list that ``run_sync`` drained once
    ``run_restore`` had returned — which is to say, after ``compute_outcome``
    had already decided the run was a clean SUCCESS from counts that describe
    the SOURCE. Stamping at the moment of the failed read is what lets the ONE
    outcome decision see it, so the task result, the task-history
    ``details.outcome`` row, the ``sync_outbound`` journal row and the persisted
    ``sync_targets.last_outcome`` column all read the same verdict instead of
    needing a second correction each.

    A transparent proxy rather than a subclass: the client is constructed by
    :func:`make_remote_client` (Fernet decrypt + SSRF-pinned transport) and must
    not be rebuilt here, and every attribute other than the wrapped reads passes
    straight through.
    """

    def __init__(self, inner, report: RestoreReport) -> None:
        # Bypass __getattr__ for our own state (anything not set here routes to
        # the wrapped client).
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_report", report)

    def __getattr__(self, name: str):
        attr = getattr(self._inner, name)
        if not name.startswith(_DESTINATION_READ_PREFIX) or not callable(attr):
            return attr

        async def _observed_read(*args, **kwargs):
            try:
                result = attr(*args, **kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as exc:  # noqa: BLE001 - observe, never swallow
                _mark_destination_unread(
                    self._report,
                    "%s could not be read — %s" % (
                        name, _describe_destination_error(exc),
                    ),
                )
                raise

        return _observed_read


def _mark_destination_unread(report: RestoreReport, reason: str) -> None:
    """Stamp the "I never read the destination" marker and say so in the notes.

    One writer so the marker and the operator-facing note can never disagree,
    and so a second failure never overwrites the first (the first refusal is the
    one that explains the rest).
    """
    if report.destination_unreadable is None:
        report.destination_unreadable = reason
    report.notes.append("destination not read: %s" % reason)


# ---------------------------------------------------------------------------
# run_sync — the engine entrypoint.
# ---------------------------------------------------------------------------


def _journal_sync_run(
    target,
    report: Optional[RestoreReport],
    *,
    confirm_apply: bool,
    aborted_reason: Optional[str] = None,
) -> None:
    """Write the per-run ``sync_outbound`` audit row (Addendum D D9).

    Records target id, the config categories + their counts, the result, and the
    redaction mode. Best-effort — a journal failure must not crash the sync. Only
    SAFE fields (names, counts, outcome) are logged; never a credential.
    """
    try:
        if aborted_reason is not None:
            description = "Cross-instance sync ABORTED: %s" % aborted_reason
            counts = {}
            result = "aborted"
        else:
            counts = {
                cat.entity_type.value: {
                    "created": cat.created,
                    "would_create": cat.would_create,
                    "skipped": cat.skipped,
                    "failed": cat.failed,
                }
                for cat in (report.categories if report else [])
            }
            outcome = report.outcome.value if (report and report.outcome) else None
            result = (
                "dry_run" if (report and report.is_dry_run) else (outcome or "unknown")
            )
            # Report the categories THIS run actually processed (includes the
            # opt-in logos slice when the target enabled it); fall back to the
            # unconditional set when the report carries no categories.
            ran_categories = sorted(counts) if counts else sorted(SYNC_ALL_CATEGORIES)
            description = (
                "Cross-instance sync run (mode=%s, redaction_mode=topology_only, "
                "categories=%s)" % (result, ran_categories)
            )
        journal.log_entry(
            category="sync_outbound",
            action_type="sync_run",
            entity_name=getattr(target, "name", None) or ("sync target %s" % getattr(target, "id", "?")),
            entity_id=getattr(target, "id", None),
            description=description,
            # after_value carries the structured run record (no secrets — only
            # category names, counts, and the result/redaction mode).
            after_value={
                "confirm_apply": confirm_apply,
                "redaction_mode": "topology_only",
                "result": result,
                "counts": counts,
            },
            user_initiated=False,
        )
    except Exception as exc:  # pragma: no cover — journal best-effort
        logger.warning("[SYNC] Failed to journal sync run: %s", exc)


async def run_sync(
    sync_target,
    *,
    confirm_apply: bool = False,
    session=None,
    captured_version: Optional[int] = None,
    ledger_dir: Optional[Path] = None,
) -> RestoreReport:
    """Run one cross-instance config sync cycle for ``sync_target`` (A → B).

    The engine entrypoint:

    1. **Freshness gate (D5)** — :func:`sync_freshness_reason` re-reads the target
       FRESH. A non-None reason ABORTS the cycle fail-closed: no remote client is
       built, no writes happen, the abort is journalled (``sync_outbound``), and a
       report carrying the reason in ``notes`` (``outcome=None``) is returned.
    2. **Remote client** — :func:`make_remote_client` builds an SSRF-guarded
       dest-B client from the target row.
    3. **Live-source plan** — :func:`build_live_source_plan` gathers + redacts A's
       config categories (D2) into an :class:`ImportPlan` (never users — D3).
    4. **Restore** — the UNCHANGED :func:`run_restore` runs the config-only step
       registry against dest-B. ``confirm_apply=False`` (the DEFAULT) produces a
       counts-only dry-run (would-create) with ZERO writes; ``confirm_apply=True``
       applies source-wins (A overwrites B; match→skip-or-create idempotent).
    5. **Audit (D9)** — the run is journalled (categories, counts, result,
       redaction_mode).

    Args:
        sync_target: a ``SyncTarget`` ORM row (or any object exposing ``id`` /
            ``name`` / ``base_url`` / ``credentials`` / ``enabled`` /
            ``token_revoked_at`` / ``credential_version`` / ``insecure`` /
            ``fuzzy_stream_matching`` / ``sync_logos``).
        confirm_apply: opt-IN to MUTATE B. ``False`` (default) is a counts-only
            dry-run (no writes); ``True`` applies source-wins.
        session: an open DB session for the freshness re-read. The caller owns its
            lifecycle. Optional only so the dry-run preview can run without one;
            when ``None`` the freshness gate is skipped (the scheduled wrapper
            bead ``5gzg5`` always passes one).
        captured_version: the ``credential_version`` captured at enqueue, threaded
            to the freshness gate (D5). ``None`` skips the version check.
        ledger_dir: override the durable rollback-ledger directory (tests).

    Returns:
        The :class:`RestoreReport` — dry-run (``is_dry_run=True``, ``outcome=None``)
        or a realized apply with the tri-state ``outcome``. On a freshness abort,
        a report with the reason in ``notes`` and ``outcome=None``.
    """
    target_label = getattr(sync_target, "name", None) or (
        "sync target %s" % getattr(sync_target, "id", "?")
    )

    # --- 1. Freshness gate (D5) — abort fail-closed on stale/revoked/disabled. ---
    if session is not None:
        reason = sync_freshness_reason(
            session, getattr(sync_target, "id", None), captured_version
        )
        if reason is not None:
            logger.warning("[SYNC] Aborting sync for %s: %s", target_label, reason)
            report = RestoreReport(is_dry_run=not confirm_apply)
            report.notes.append("sync aborted: %s" % reason)
            # This cycle stopped BEFORE a client existed, so it read nothing of
            # the destination. Without the marker the aborted preview reaches
            # the task wrapper as an ordinary dry run — is_dry_run=True,
            # outcome=None — which that wrapper reads as a success and the
            # Settings card reads as "Apply is now safe" (bead …-jqfxm). In
            # production the task's own fire-time gate catches this first; this
            # is the defence-in-depth copy, and it must not be the honest one's
            # weak twin.
            _mark_destination_unread(
                report, "the cycle aborted before reading it — %s" % reason
            )
            _journal_sync_run(
                sync_target, report, confirm_apply=confirm_apply, aborted_reason=reason
            )
            return report

    # --- 2. Remote dest-B client (SSRF-guarded). ---
    client = make_remote_client(sync_target)

    # --- 2b. Destination-readback gate (…-jqfxm) — fail-closed BEFORE any
    # work. Every count this run will publish is a claim about the destination,
    # so the destination has to answer one authenticated question first. A
    # refusal aborts exactly like the freshness gate: no plan gathered, no
    # writes, journalled, and a report that can never read as success. ---
    unread_reason = await destination_read_reason(client)
    if unread_reason is not None:
        logger.warning(
            "[SYNC] Aborting sync for %s — destination unreadable: %s",
            target_label, unread_reason,
        )
        report = RestoreReport(is_dry_run=not confirm_apply)
        report.notes.append("sync aborted: %s" % unread_reason)
        _mark_destination_unread(report, unread_reason)
        _journal_sync_run(
            sync_target,
            report,
            confirm_apply=confirm_apply,
            aborted_reason=unread_reason,
        )
        return report

    # From here on the orchestrator talks to the destination through a wrapper
    # that NOTICES a failed read (the importers' own fallbacks turn one into
    # "the destination is empty"). Reads still behave exactly as before; the
    # wrapper marks the report the moment one raises, so the marker is in place
    # BEFORE run_restore decides the outcome (bead …-bj442) — which is why the
    # report is built here rather than beside the ledger below.
    report = RestoreReport(is_dry_run=not confirm_apply)
    client = _ReadObservingClient(client, report)

    # --- 3. Redacted live-source plan (config categories, never users). The
    # logos slice is per-target OPT-IN (sync_logos, default off — 7ipq2.1);
    # when on, the plan gains a METADATA-ONLY logo category (bytes hydrate
    # lazily at import time, misses only — D8). ---
    include_logos = bool(getattr(sync_target, "sync_logos", False))
    plan = await build_live_source_plan(include_logos=include_logos)

    # --- 3b. Degrade a source-side duplicate name to a per-item CONFLICT ---
    # instead of inheriting preflight's all-or-nothing plan refusal (see
    # _split_name_conflicts) — a single duplicated group/profile/M3U name must
    # not blank out every other category's diff.
    plan, excluded_name_conflicts = _split_name_conflicts(plan)

    # --- 4. Restore (reused orchestrator) — dry-run default, source-wins apply. ---
    # The per-target fuzzy-stream-matching opt-in (default off) threads into the
    # channels step AND into the orchestrator, which runs a SECOND matcher pass
    # (the post-create placeholder rebind) after the importers finish. Both must
    # get it: passing it only to the step left the rebind on its own default and
    # a target with the flag OFF was still fuzzy-rebound onto a wrong-but-similar
    # stream, reported as SUCCESS (bead …-efvyg). Off => the stream matcher
    # floors at Tier-3 exact, everywhere in the cycle (ruling 1b).
    allow_fuzzy = bool(getattr(sync_target, "fuzzy_stream_matching", False))
    ledger = RollbackLedger(restore_id=new_restore_id())
    result = await run_restore(
        plan=plan,
        client=client,
        steps=sync_config_importer_steps(allow_fuzzy_stream_match=allow_fuzzy),
        report=report,
        ledger=ledger,
        remap=IdRemapTable(),
        confirm_apply=confirm_apply,
        deferred_apply_fn=_no_deferred_apply,  # ADR-013 S9 — suppress per-cycle defer.
        ledger_dir=ledger_dir,
        allow_fuzzy_stream_match=allow_fuzzy,
    )

    # --- 4b. Surface each deduped-out duplicate name as a per-item CONFLICT. ---
    _apply_name_conflict_details(result, excluded_name_conflicts)

    # --- 4c. A read that failed AFTER the gate still means the report describes
    # a destination it did not fully read (…-jqfxm). The importer that hit it
    # already carried on with "existing = []", so the counts for that category
    # are the source's, not the destination's. THERE IS NO POST-RUN DRAIN HERE:
    # _ReadObservingClient marks the report at the moment of the failed read, so
    # the marker is already in place when run_restore's compute_outcome runs and
    # the outcome that reaches the journal row below, the persisted
    # last_outcome/last_full_sync_at stamp, and the task's details.outcome is
    # the SAME one — one decision, every surface (bead …-bj442). Draining the
    # failures here instead is exactly what let outcome=success be recorded for
    # a cycle that never read its destination. ---

    # The conflict details below land AFTER run_restore computed the tri-state
    # outcome, so without this re-check an APPLY with source-side name
    # conflicts would report outcome=SUCCESS alongside failed>0 — violating
    # the ratified "NEVER SUCCESS on mixed state" invariant (ADR-013 S8) that
    # the task wrapper's failed-count contract depends on (live-validation
    # finding, bead 7ipq2.2). Mirror compute_outcome's no-rollback branch: a
    # per-item conflict with no rollback is FAILED_ROLLBACK_INCOMPLETE, exactly
    # what the channels importer's in-run CONFLICT path already yields. Only a
    # clean SUCCESS is ever downgraded — a rolled-back outcome stays as
    # computed (the rollback verdict is already correct for it).
    if (
        excluded_name_conflicts
        and not result.is_dry_run
        and result.outcome == RestoreOutcome.SUCCESS
    ):
        result.outcome = RestoreOutcome.FAILED_ROLLBACK_INCOMPLETE

    # --- 5. Audit the run (D9). ---
    _journal_sync_run(sync_target, result, confirm_apply=confirm_apply)

    # --- 5b. Stamp the persisted per-target sync state (DBA ruling, spike
    # xp6mp / migration 0024): last_outcome on every REALIZED apply, and
    # last_full_sync_at only on a FULL success — the staleness/status surface
    # must never read a mixed apply (or a dry-run preview) as "B was current
    # as of this time". Live-validation finding (bead 7ipq2.2): these columns
    # existed but nothing ever wrote them. Best-effort: a stamp failure must
    # not fail an otherwise-completed sync. last_source_fingerprint stays
    # unwritten (semantics unratified — follow-up bead). ---
    if session is not None and not result.is_dry_run and result.outcome is not None:
        try:
            from datetime import datetime, timezone

            sync_target.last_outcome = result.outcome.value
            if result.outcome == RestoreOutcome.SUCCESS:
                sync_target.last_full_sync_at = datetime.now(timezone.utc)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - stamping is best-effort
            logger.warning("[SYNC] Failed to stamp persisted sync state: %s", exc)

    logger.info(
        "[SYNC] Sync cycle for %s complete (mode=%s, outcome=%s).",
        target_label,
        "dry_run" if result.is_dry_run else "apply",
        result.outcome.value if result.outcome else "none",
    )
    return result
