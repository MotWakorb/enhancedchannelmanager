"""The ECM-settings restore importer — ECM's OWN ``settings.json``.

Bead ``enhancedchannelmanager-dfkbn`` item 4.

----------------------------------------------------------------------------
WHAT WENT MISSING AND WHY
----------------------------------------------------------------------------

The drill (run 2026-08-04-run1) set two non-default ECM settings through the
Settings UI, backed up, wiped, and restored::

    user_timezone:        'America/Chicago'  ->  ''   (default)
    stats_poll_interval:  37                 ->  10   (default)

The restore report showed ``settings updated=7`` — which is why this reads as a
paradox until you know that category is
:data:`~dbas.restore_contracts.EntityType.SETTINGS`, DISPATCHARR's core-settings
namespace (``dbas.importers.settings_agents``). ECM's own settings are a
different thing entirely: the artifact builder DOES emit them
(``categories/settings.yaml``, via ``routers.backup._gather_settings``), but
``dbas.restore_artifact._SECTION_TO_ENTITY`` had no row for the section, so the
decoder dropped the whole blob on the floor and no importer ever existed. The
settings were not mis-applied; they were never read.

This module is the missing importer, and
:data:`~dbas.restore_contracts.EntityType.ECM_SETTINGS` is the category that
distinguishes it from Dispatcharr's.

----------------------------------------------------------------------------
WHAT IT WILL NOT WRITE (and why each exclusion is not optional)
----------------------------------------------------------------------------

1. **The live Dispatcharr connection** (:data:`CONNECTION_KEYS`). The restore is
   RUNNING against that connection: the orchestrator holds an authenticated
   client, the deferred phase is about to trigger an M3U refresh through it, and
   the placeholder rebind after that reads streams back over it. Repointing
   ``url`` / ``auth_method`` / ``username`` / ``password`` /
   ``dispatcharr_api_key`` mid-run would break the run that is applying them, and
   on a migrate-to-a-new-install restore it would overwrite the connection the
   operator just configured with the dead one from the old host. The operator
   sets this during setup; the archive does not get to move it.

2. **A redaction sentinel.** A STANDARD (redact-by-default) artifact carries
   ``***REDACTED***`` in place of every credential-class value. Writing that
   through would replace a WORKING secret with a placeholder that authenticates
   nowhere — strictly worse than leaving the value alone. Sentinel-valued keys
   are skipped and reported as a credential-re-entry action item, reusing the
   ``…-6pilh`` machinery (:func:`credential_sentinel.strip_redaction_sentinels`).

3. **Keys this build does not have.** An archive from a newer ECM can carry a
   setting this version's model does not define. It is dropped with a note
   rather than failing the category — an unknown key is a version difference,
   not a corrupt archive.

4. **Internal bookkeeping** (:data:`INTERNAL_KEYS`) — one-time heal markers that
   describe what has run on THIS install, not operator intent.

----------------------------------------------------------------------------
NOT LEDGERED, NOT ROLLED BACK
----------------------------------------------------------------------------

A setting is config, not a created entity: there is nothing to compensate-delete,
so nothing is written to the :class:`~dbas.restore_contracts.RollbackLedger` and
results land as ``updated`` / ``skipped``, never ``created``. This mirrors
``dbas.importers.settings_agents`` exactly — including the consequence, which the
orchestrator already surfaces: applied settings are NOT undone by a rollback.

Conventions (``docs/style_guide.md`` / ``backend/CLAUDE.md``): ``snake_case``;
Google-style docstrings; lazy ``%`` logging with a ``[DBAS-ECM-SETTINGS]``
prefix; a setting VALUE is never logged (several are credentials).
"""

from __future__ import annotations

import logging

from config import get_settings, save_settings
from credential_sentinel import is_redaction_sentinel
from dbas.restore_contracts import (
    EntityType,
    RestoreReport,
    SkipDetail,
    SkipReason,
)

logger = logging.getLogger(__name__)

# The LIVE Dispatcharr connection — never restored from an archive. See the
# module docstring, exclusion 1: the restore is running THROUGH this connection.
# ``api_key`` is the legacy alias of ``dispatcharr_api_key`` (bd-jmi1c) and is
# excluded with it so a stale legacy value cannot be mirrored back in on save.
CONNECTION_KEYS: frozenset[str] = frozenset(
    {
        "url",
        "auth_method",
        "username",
        "password",
        "dispatcharr_api_key",
        "api_key",
    }
)

# Install-local bookkeeping — records what has already run on THIS install, not
# operator intent. Restoring one would re-arm or suppress a one-time heal.
INTERNAL_KEYS: frozenset[str] = frozenset({"league_delimiter_heal_applied"})

# Every key this importer refuses to write, for one reason or another.
EXCLUDED_KEYS: frozenset[str] = CONNECTION_KEYS | INTERNAL_KEYS


async def import_ecm_settings(
    *,
    archive_settings: dict,
    selected: bool,
    report: RestoreReport,
    is_dry_run: bool = False,
) -> int:
    """Restore ECM's own settings from the archive's ``settings`` blob.

    Applies every archived key that (a) exists on this build's settings model,
    (b) is not excluded (:data:`EXCLUDED_KEYS`), (c) is not a redaction sentinel,
    and (d) actually DIFFERS from the current value — an identical value is a
    skip, so the ``updated`` count means "settings this restore changed", not
    "settings this restore looked at".

    The whole blob is written in ONE ``save_settings`` call at the end: a
    partially-written ``settings.json`` is the one state that would be worse than
    not restoring at all.

    Args:
        archive_settings: The ``settings`` mapping decoded from the artifact.
        selected: The per-category opt-in flag. ``False`` -> nothing is written.
        report: The shared :class:`RestoreReport`; results land in the
            ``EntityType.ECM_SETTINGS`` category, and any sentinel-valued
            credential is recorded as a re-entry action item.
        is_dry_run: ``True`` -> nothing is written; the importer only reports
            ``would_update`` / ``would_skip``.

    Returns:
        The number of settings applied (0 on a dry-run).
    """
    cat = report.category(EntityType.ECM_SETTINGS)

    if not isinstance(archive_settings, dict) or not archive_settings:
        logger.info("[DBAS-ECM-SETTINGS] Archive carries no ECM settings; nothing to do.")
        return 0

    # OPT-IN. Off unless the operator selected the ECM-settings category.
    if not selected:
        logger.info("[DBAS-ECM-SETTINGS] Category not selected; skipping ECM settings.")
        for key in archive_settings:
            _skip(cat, SkipReason.EXCLUDED_BY_OPERATOR, str(key), is_dry_run)
        return 0

    settings = get_settings()
    known_keys = set(type(settings).model_fields)

    to_apply: dict = {}
    redacted_fields: list[str] = []

    for key, value in archive_settings.items():
        key = str(key)
        if key in EXCLUDED_KEYS:
            _skip(cat, SkipReason.EXCLUDED_BY_OPERATOR, key, is_dry_run)
            continue
        if key not in known_keys:
            _skip(cat, SkipReason.UNSUPPORTED_IN_THIS_VERSION, key, is_dry_run)
            continue
        if is_redaction_sentinel(value):
            # Never overwrite a working secret with the placeholder — leave the
            # current value and make it an operator action item instead.
            redacted_fields.append(key)
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, key, is_dry_run)
            continue
        if getattr(settings, key, None) == value:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, key, is_dry_run)
            continue
        to_apply[key] = value

    if redacted_fields:
        # The archive could not carry these; the current values are untouched but
        # they no longer match what was backed up. Same shape as the M3U/EPG
        # credential action item (…-6pilh) — field NAMES only, never values.
        report.record_credential_reentry(
            EntityType.ECM_SETTINGS,
            "ECM settings",
            sorted(redacted_fields),
        )

    if is_dry_run:
        cat.would_update += len(to_apply)
        logger.info(
            "[DBAS-ECM-SETTINGS] Dry-run: would update %d ECM setting(s).",
            len(to_apply),
        )
        return 0

    if not to_apply:
        logger.info("[DBAS-ECM-SETTINGS] Every archived ECM setting already matches.")
        return 0

    applied = 0
    for key, value in to_apply.items():
        try:
            setattr(settings, key, value)
        except Exception as exc:  # noqa: BLE001 - a bad value is a per-key skip
            # Pydantic validation rejected the archived value (a type/enum drift
            # across versions). Log the KEY only — several values are credentials.
            logger.warning(
                "[DBAS-ECM-SETTINGS] Archived value for '%s' was rejected by this "
                "build's settings model (%s); left unchanged.",
                key, type(exc).__name__,
            )
            _skip(cat, SkipReason.UNSUPPORTED_IN_THIS_VERSION, key, is_dry_run)
            continue
        applied += 1

    if applied:
        # ONE write for the whole blob — never a half-applied settings.json.
        save_settings(settings)
        cat.updated += applied
        logger.info("[DBAS-ECM-SETTINGS] Applied %d ECM setting(s).", applied)
    return applied


def _skip(cat, reason: SkipReason, label: str, is_dry_run: bool) -> None:
    """Record a skip in both the count and the reasoned detail list."""
    if is_dry_run:
        cat.would_skip += 1
    else:
        cat.skipped += 1
    cat.skip_details.append(SkipDetail(reason=reason, label=label))
