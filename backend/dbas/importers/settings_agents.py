"""The settings/agents DBAS restore importer (Phase-2 bulk importer).

Bead ``enhancedchannelmanager-0i2vt.13``. Restores FOUR net-new categories that
split into TWO shapes. PLUGINS are EXCLUDED (ADR-012 D10 — RCE-vs-config
unresolved). USERS are restored by a SEPARATE crown-jewel importer
(``importers/users.py`` — bead ``…-l1p4p``); they are NOT touched here.

----------------------------------------------------------------------------
TWO SHAPES
----------------------------------------------------------------------------

ENTITY categories — create rows, remappable, ledger-tracked (mirror
``importers/m3u_accounts.py``):

* **user_agents** (:func:`import_user_agents`) -> ``EntityType.USER_AGENT``.
  Identity by name. Create + register source->dest in the
  :class:`~dbas.restore_contracts.IdRemapTable` + record in the
  :class:`~dbas.restore_contracts.RollbackLedger`. A name collision is skipped
  ``ALREADY_EXISTS_IDENTICAL`` and remapped to the existing destination id so a
  DVR rule (or channel) that references it still resolves.

* **dvr_rules** (:func:`import_dvr_rules`) -> ``EntityType.DVR_RULE`` (a.k.a.
  recording / series rules). Identity by name/title. Its ``channel`` FK is
  remapped through the IdRemapTable; an unresolvable FK is failed
  ``DEPENDENCY_UNRESOLVED`` (or skipped ``DEPENDENCY_UNRESOLVED`` on dry-run) and
  never sent upstream with a dangling source id.

SETTINGS categories — APPLY key/value config; NOT entity-create, NOT
id-remapped, NOT ledgered as creates:

* **core_settings** (:func:`import_core_settings`) — apply the archived core/
  global settings to the destination via PER-KEY PATCH
  (``client.update_core_setting``). Reported as ``updated`` / ``skipped`` on the
  ``EntityType.SETTINGS`` report category — never ``created``, never ledgered.
* **comskip** (:func:`import_comskip`) — same shape; a comskip config blob applied
  conservatively.

----------------------------------------------------------------------------
SETTINGS SAFETY — the conservative denylist (the highest-risk decision)
----------------------------------------------------------------------------

Core settings + comskip can carry credential / auth / instance-identity material
(API keys, SMTP passwords, signing secrets, the operator's own auth config).
Blindly PUTting the whole settings blob could reconfigure or offline the LIVE
instance mid-restore and clobber the operator's credentials with the archive
source's.

Policy — **DENYLIST, fail-safe**: a setting key is applied ONLY if it does NOT
match any marker in :data:`DANGEROUS_SETTING_KEY_MARKERS` (case-insensitive
substring match — see :func:`is_safe_setting_key`). A matching key is SKIPPED
and reported (by NAME only — never its value). The denylist errs toward NOT
applying: a key whose name contains any credential/auth/secret/identity marker is
left untouched, so the operator's live credentials are never overwritten by the
archive. Keys we do not recognize but that look benign (no dangerous marker) are
applied — these are config like ``default_user_agent`` / ``ui_theme`` /
``comskip_ini`` that are safe to carry across instances.

ROLLBACK OF SETTINGS IS OUT OF SCOPE (known limitation): a settings change is not
a created ENTITY, so there is nothing to compensate-delete. The ledger records
only created entities; applied settings are NOT ledgered and are NOT undone on a
later failure. This is a deliberate, documented limitation for v0.18.0.

----------------------------------------------------------------------------
CREDENTIAL HYGIENE (the bead .8 + l1p4p lesson)
----------------------------------------------------------------------------

A setting VALUE may be a secret. This module NEVER logs a setting value and
NEVER puts a setting value into the :class:`RestoreReport`. The only things that
surface are SAFE: the setting KEY name (for the skipped-key audit), counts, and
status. Setting values are passed straight to the client and never echoed. The
entity importers carry no credentials (a user agent is a benign UA string; a DVR
rule is benign config), but failure messages are still sanitized defensively.

This module imports the contracts module READ-ONLY.
"""

from __future__ import annotations

import logging

from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipDetail,
    SkipReason,
)
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# The report category that carries core_settings + comskip results. Settings are
# applied (updated/skipped), never created and never ledgered — this is a
# report-only key, not a remap/ledger namespace.
SETTINGS_CATEGORY_TYPE = EntityType.SETTINGS

# Archive-source identifiers the destination assigns itself, never forwarded.
_SOURCE_ID_KEYS = frozenset({"id", "pk"})

# Read-only / derived keys a GET echoes back that are not part of a create.
_NON_CREATE_KEYS = frozenset({"created_at", "updated_at"})

_DROPPED_CREATE_KEYS = _SOURCE_ID_KEYS | _NON_CREATE_KEYS

# ---------------------------------------------------------------------------
# SETTINGS SAFETY DENYLIST
# ---------------------------------------------------------------------------
#
# Case-insensitive SUBSTRING markers. A setting key matching ANY of these is
# treated as credential/auth/instance-identity and is NEVER applied — it is
# skipped (reported by name only). Substring match is deliberate: it catches
# ``dispatcharr_api_key``, ``smtp_password``, ``jwt_signing_key``,
# ``comskip_upload_token`` etc. without needing to enumerate every exact key a
# present-or-future Dispatcharr version might expose.
DANGEROUS_SETTING_KEY_MARKERS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "credential",
        "auth",          # auth_token, auth_config, basic_auth, ...
        "private_key",
        "privatekey",
        "signing_key",
        "ssh_key",
        "cert",          # certificate / private cert material
        "key",           # last-resort catch-all for *_key (signing/encryption keys)
        "license",       # instance-identity / paid-license keys
        "salt",
        "session",       # session secrets / cookies
        "webhook",       # webhook URLs commonly embed tokens
    }
)


def is_safe_setting_key(key: str) -> bool:
    """Return True iff a settings key is SAFE to apply (denylist, fail-safe).

    A key is SAFE only when its lower-cased form contains NONE of the
    :data:`DANGEROUS_SETTING_KEY_MARKERS`. This errs toward NOT applying: any key
    whose name hints at credential/auth/secret/instance-identity material is
    rejected so the live instance's own credentials are never clobbered by the
    archive's.

    Args:
        key: The settings key name.

    Returns:
        True if the key may be applied; False if it must be skipped.
    """
    if not isinstance(key, str) or not key:
        return False
    lowered = key.lower()
    return not any(marker in lowered for marker in DANGEROUS_SETTING_KEY_MARKERS)


# ---------------------------------------------------------------------------
# Shared small helpers
# ---------------------------------------------------------------------------


def _norm_name(value) -> str | None:
    """Case-insensitive, trimmed key for an identity name; None if absent/blank."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip().lower()
    return trimmed or None


def _label(record: dict, *keys: str) -> str:
    """Operator-facing identifier — first present of ``keys`` (name/title)."""
    for k in keys:
        v = record.get(k)
        if v:
            return str(v)
    return "<unknown>"


def _build_create_payload(archive_record: dict) -> dict:
    """Strip archive-source ids and read-only fields from a create payload."""
    return {
        k: v for k, v in archive_record.items() if k not in _DROPPED_CREATE_KEYS
    }


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create failure: name/uniqueness conflict -> CONFLICT, else
    UPSTREAM_API_ERROR. (Inspects only the exception text, never a credential.)"""
    text = str(exc).lower()
    if "already exists" in text or "unique" in text or "conflict" in text:
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _sanitize_failure(exc: Exception, generic: str) -> str:
    """Sanitized operator-facing failure message — no URLs/secrets.

    An upstream error body can echo a value; if the text mentions a URL or a
    credential marker, fall back to a generic message rather than risk a leak.
    """
    text = (str(exc) or "").strip()
    lowered = text.lower()
    if "http" in lowered or any(m in lowered for m in DANGEROUS_SETTING_KEY_MARKERS):
        return generic
    return text or generic


def _safe_key_label(index: int, name) -> str:
    """Non-sensitive, taint-free label for a settings key in LOG lines only.

    A settings key NAME can itself be credential-bearing (e.g. a denylisted key
    literally named ``dispatcharr_api_key``), and CodeQL's
    py/clear-text-logging-sensitive-data correctly taints any value derived from
    the archive's keys as a secret. To both (a) avoid echoing a possibly
    sensitive key name to the log stream and (b) break the taint flow into the
    ``logger.*`` sinks below, we emit ONLY non-sensitive, synthetic metadata:
    the loop INDEX and the key's character LENGTH. No character of the original
    key name flows into the returned string, so nothing CodeQL classifies as a
    secret reaches the log sink.

    Operators correlate this label with the FULL ``source_label:setting_name``
    identifier via the structured report's ``skip_details`` / ``failure_details``
    (those are operator-facing report fields, not log sinks, and are already
    value-free). The log line is for triage volume/sequencing only.
    """
    length = len(name) if isinstance(name, str) else 0
    return f"key#{index} (len={length})"


def _skip(cat, reason: SkipReason, label: str, source_export_id, is_dry_run: bool) -> None:
    """Record a skip in both the count and the reasoned detail list."""
    if is_dry_run:
        cat.would_skip += 1
    else:
        cat.skipped += 1
    cat.skip_details.append(
        SkipDetail(reason=reason, label=label, source_export_id=source_export_id)
    )


def _index_existing_by_name(records: list[dict]) -> dict[str, dict]:
    """Index existing destination records by their normalized name/title."""
    index: dict[str, dict] = {}
    for rec in records or []:
        if isinstance(rec, dict):
            key = _norm_name(rec.get("name")) or _norm_name(rec.get("title"))
            if key is not None and key not in index:
                index[key] = rec
    return index


# ===========================================================================
# USER AGENTS (entity)
# ===========================================================================


async def import_user_agents(
    *,
    archive_user_agents: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the USER_AGENT category: create agents; remap + ledger each create.

    Identity is by name. A name collision is skipped ``ALREADY_EXISTS_IDENTICAL``
    and remapped to the existing destination id. Opt-in via ``selected``.
    """
    cat = report.category(EntityType.USER_AGENT)

    if not selected:
        logger.info("[DBAS-SETTINGS] User agents not selected; skipping category.")
        for rec in archive_user_agents:
            _skip(cat, SkipReason.EXCLUDED_BY_OPERATOR, _label(rec, "name"), rec.get("id"), is_dry_run)
        return

    logger.info(
        "[DBAS-SETTINGS] Restoring user agents (dry_run=%s); %d archived.",
        is_dry_run,
        len(archive_user_agents),
    )
    try:
        existing = await client.get_user_agents()
    except Exception as exc:
        logger.warning("[DBAS-SETTINGS] Could not list existing user agents: %s", exc)
        existing = []
    existing_by_name = _index_existing_by_name(existing)

    for rec in archive_user_agents:
        label = _label(rec, "name")
        source_id = rec.get("id")
        name_key = _norm_name(rec.get("name"))

        existing_rec = existing_by_name.get(name_key) if name_key else None
        if existing_rec is not None:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            existing_id = existing_rec.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(EntityType.USER_AGENT, int(source_id), int(existing_id))
            logger.info("[DBAS-SETTINGS] User agent '%s' already exists (id=%s); skipped.", label, existing_id)
            continue

        if is_dry_run:
            cat.would_create += 1
            continue

        payload = _build_create_payload(rec)
        try:
            created = await client.create_user_agent(payload)
        except Exception as exc:
            reason = _failure_reason_for(exc)
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=reason,
                    label=label,
                    message=_sanitize_failure(exc, "Upstream rejected the user-agent create."),
                    source_export_id=source_id,
                )
            )
            logger.warning("[DBAS-SETTINGS] Failed to restore user agent '%s': %s", label, reason.value)
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            dest_id = int(dest_id)
            if source_id is not None:
                remap.add(EntityType.USER_AGENT, int(source_id), dest_id)
            ledger.record_created(EntityType.USER_AGENT, dest_id, label)
        logger.info("[DBAS-SETTINGS] Restored user agent '%s' (id=%s).", label, dest_id)


# ===========================================================================
# DVR RULES (entity, FK remap)
# ===========================================================================

# FK fields on a DVR rule that point at another restored entity and must be
# remapped before the rule is sent upstream.
_DVR_FK_FIELDS = (("channel", EntityType.CHANNEL),)


def _remap_dvr_fks(
    payload: dict,
    remap: IdRemapTable,
) -> str | None:
    """Rewrite a DVR rule's FK references to destination ids, in place.

    Returns the name of the FIRST FK that could not be resolved (caller treats it
    as DEPENDENCY_UNRESOLVED), or None when every present FK resolved.
    """
    for field, entity_type in _DVR_FK_FIELDS:
        source_ref = payload.get(field)
        if source_ref is None:
            continue
        try:
            source_ref_int = int(source_ref)
        except (TypeError, ValueError):
            return field
        dest_ref = remap.resolve(entity_type, source_ref_int)
        if dest_ref is None:
            return field
        payload[field] = dest_ref
    return None


async def import_dvr_rules(
    *,
    archive_dvr_rules: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    is_dry_run: bool = False,
) -> None:
    """Restore the DVR_RULE category: remap FKs, create, remap + ledger.

    Identity is by name/title. An unresolvable FK (e.g. ``channel``) fails the
    rule ``DEPENDENCY_UNRESOLVED`` (skip ``DEPENDENCY_UNRESOLVED`` on dry-run) and
    never sends a dangling source id upstream. A name collision is skipped
    ``ALREADY_EXISTS_IDENTICAL`` and remapped. Opt-in via ``selected``.
    """
    cat = report.category(EntityType.DVR_RULE)

    if not selected:
        logger.info("[DBAS-SETTINGS] DVR rules not selected; skipping category.")
        for rec in archive_dvr_rules:
            _skip(cat, SkipReason.EXCLUDED_BY_OPERATOR, _label(rec, "name", "title"), rec.get("id"), is_dry_run)
        return

    logger.info(
        "[DBAS-SETTINGS] Restoring DVR rules (dry_run=%s); %d archived.",
        is_dry_run,
        len(archive_dvr_rules),
    )
    try:
        existing = await client.get_dvr_rules()
    except Exception as exc:
        logger.warning("[DBAS-SETTINGS] Could not list existing DVR rules: %s", exc)
        existing = []
    existing_by_name = _index_existing_by_name(existing)

    for rec in archive_dvr_rules:
        label = _label(rec, "name", "title")
        source_id = rec.get("id")
        name_key = _norm_name(rec.get("name")) or _norm_name(rec.get("title"))

        existing_rec = existing_by_name.get(name_key) if name_key else None
        if existing_rec is not None:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, source_id, is_dry_run)
            existing_id = existing_rec.get("id")
            if source_id is not None and existing_id is not None:
                remap.add(EntityType.DVR_RULE, int(source_id), int(existing_id))
            logger.info("[DBAS-SETTINGS] DVR rule '%s' already exists (id=%s); skipped.", label, existing_id)
            continue

        payload = _build_create_payload(rec)
        unresolved_fk = _remap_dvr_fks(payload, remap)
        if unresolved_fk is not None:
            if is_dry_run:
                _skip(cat, SkipReason.DEPENDENCY_UNRESOLVED, label, source_id, is_dry_run)
            else:
                cat.failed += 1
                cat.failure_details.append(
                    FailureDetail(
                        reason=FailureReason.DEPENDENCY_UNRESOLVED,
                        label=label,
                        message=f"DVR rule references an unresolved {unresolved_fk}; not restored.",
                        source_export_id=source_id,
                    )
                )
            logger.warning(
                "[DBAS-SETTINGS] DVR rule '%s' has unresolved FK '%s'; skipped.", label, unresolved_fk
            )
            continue

        if is_dry_run:
            cat.would_create += 1
            continue

        try:
            created = await client.create_dvr_rule(payload)
        except Exception as exc:
            reason = _failure_reason_for(exc)
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=reason,
                    label=label,
                    message=_sanitize_failure(exc, "Upstream rejected the DVR-rule create."),
                    source_export_id=source_id,
                )
            )
            logger.warning("[DBAS-SETTINGS] Failed to restore DVR rule '%s': %s", label, reason.value)
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            dest_id = int(dest_id)
            if source_id is not None:
                remap.add(EntityType.DVR_RULE, int(source_id), dest_id)
            ledger.record_created(EntityType.DVR_RULE, dest_id, label)
        logger.info("[DBAS-SETTINGS] Restored DVR rule '%s' (id=%s).", label, dest_id)


# ===========================================================================
# SETTINGS (core settings + comskip — apply key/value, conservative)
# ===========================================================================


async def _apply_settings_blob(
    *,
    archive_settings: dict,
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    is_dry_run: bool,
    source_label: str,
) -> None:
    """Apply a key/value settings blob conservatively (core_settings / comskip).

    Safe keys (:func:`is_safe_setting_key`) are PATCHed per-key. Dangerous keys
    are skipped (reported by NAME only — never their value). Results land on the
    ``EntityType.SETTINGS`` report category as ``updated`` / ``skipped`` (or
    ``would_update`` / ``would_skip`` on dry-run). NEVER ledgered (settings are
    config, not created entities — rollback of settings is out of scope). NEVER
    logs/echoes a setting VALUE.
    """
    cat = report.category(SETTINGS_CATEGORY_TYPE)

    if not selected:
        logger.info("[DBAS-SETTINGS] %s not selected; skipping.", source_label)
        # Opt-out is silent on the per-key level (the count would be misleading vs
        # the entity categories); the orchestrator records the opt-out at the
        # category level. We simply apply nothing.
        return

    if not isinstance(archive_settings, dict):
        logger.warning("[DBAS-SETTINGS] %s blob is not a mapping; nothing to apply.", source_label)
        return

    logger.info(
        "[DBAS-SETTINGS] Applying %s (dry_run=%s); %d key(s).",
        source_label,
        is_dry_run,
        len(archive_settings),
    )

    # NOTE (clear-text-logging hygiene, bead 0i2vt.13): a settings key NAME can
    # itself be credential-bearing (a denylisted key may be literally named
    # ``dispatcharr_api_key``), and CodeQL's py/clear-text-logging-sensitive-data
    # correctly taints any value derived from the archive's keys as a secret.
    # The structured report (``skip_details`` / ``failure_details``) still
    # carries the full ``source_label:setting_name`` label for operators — those
    # are report fields, not log sinks. The LOG lines below emit ONLY synthetic,
    # taint-free metadata via :func:`_safe_key_label` (loop index + key length),
    # never ``setting_name`` and never ``setting_value``.
    for setting_index, (setting_name, setting_value) in enumerate(archive_settings.items()):
        if not is_safe_setting_key(setting_name):
            # SKIP a dangerous setting. Report the NAME only — never the value.
            if is_dry_run:
                cat.would_skip += 1
            else:
                cat.skipped += 1
            cat.skip_details.append(
                SkipDetail(
                    reason=SkipReason.EXCLUDED_BY_OPERATOR,
                    label=f"{source_label}:{setting_name}",
                    source_export_id=None,
                )
            )
            # Log taint-free metadata ONLY — never the key name or value.
            logger.info(
                "[DBAS-SETTINGS] %s setting %s looks credential/instance-identity; "
                "NOT applied (conservative skip).",
                source_label,
                _safe_key_label(setting_index, setting_name),
            )
            continue

        if is_dry_run:
            cat.would_update += 1
            continue

        try:
            await client.update_core_setting(setting_name, setting_value)
        except Exception as exc:
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.UPSTREAM_API_ERROR,
                    label=f"{source_label}:{setting_name}",
                    # Generic message — the exception text could echo the value.
                    message=f"Upstream rejected applying setting '{setting_name}'.",
                    source_export_id=None,
                )
            )
            logger.warning(
                "[DBAS-SETTINGS] Failed to apply %s setting %s.",
                source_label,
                _safe_key_label(setting_index, setting_name),
            )
            continue

        cat.updated += 1
        # Log taint-free metadata ONLY — never the key name or value.
        logger.info(
            "[DBAS-SETTINGS] Applied %s setting %s.",
            source_label,
            _safe_key_label(setting_index, setting_name),
        )


async def import_core_settings(
    *,
    archive_core_settings: dict,
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,  # accepted for entry-point symmetry; settings NEVER ledger
    is_dry_run: bool = False,
) -> None:
    """Apply archived core/global settings conservatively (denylist; per-key PATCH).

    See module docstring: dangerous keys are skipped (by name), safe keys applied,
    reported updated/skipped on ``EntityType.SETTINGS``, NEVER created, NEVER
    ledgered (settings rollback is out of scope). NEVER logs a setting value.
    """
    await _apply_settings_blob(
        archive_settings=archive_core_settings,
        client=client,
        selected=selected,
        report=report,
        is_dry_run=is_dry_run,
        source_label="core_settings",
    )


async def import_comskip(
    *,
    archive_comskip: dict,
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,  # accepted for symmetry; comskip settings NEVER ledger
    is_dry_run: bool = False,
) -> None:
    """Apply archived comskip config conservatively — same shape as core settings.

    Dangerous keys skipped (by name), safe keys applied; reported updated/skipped;
    never created, never ledgered. NEVER logs a setting value.
    """
    await _apply_settings_blob(
        archive_settings=archive_comskip,
        client=client,
        selected=selected,
        report=report,
        is_dry_run=is_dry_run,
        source_label="comskip",
    )


# ===========================================================================
# BULK ENTRY (per-category opt-in)
# ===========================================================================


async def import_settings_agents(
    *,
    archive: dict,
    client: DispatcharrClient,
    report: RestoreReport,
    ledger: RollbackLedger,
    remap: IdRemapTable,
    select_user_agents: bool = False,
    select_dvr_rules: bool = False,
    select_core_settings: bool = False,
    select_comskip: bool = False,
    is_dry_run: bool = False,
) -> None:
    """Drive all four categories with independent per-category opt-in flags.

    The orchestrator (bead ``…-0i2vt.18``) calls this (or the single-category
    entries directly). Ordering relative to other importers (after groups/
    profiles, around channels) is the ORCHESTRATOR's job — this just restores/
    applies and reports. The four categories are independent here; each off
    category creates/applies nothing.

    Args:
        archive: The export archive's settings/agents section. Reads the keys
            ``user_agents`` (list), ``dvr_rules`` (list), ``core_settings``
            (dict), ``comskip`` (dict). Missing keys default to empty.
        client: The Dispatcharr API client.
        report: The shared :class:`RestoreReport`.
        ledger: The shared :class:`RollbackLedger` (entity creates only).
        remap: The shared :class:`IdRemapTable` (entity categories).
        select_user_agents / select_dvr_rules / select_core_settings /
            select_comskip: Per-category opt-in flags.
        is_dry_run: When True, nothing is created/applied.
    """
    await import_user_agents(
        archive_user_agents=archive.get("user_agents") or [],
        client=client,
        selected=select_user_agents,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
    )
    await import_dvr_rules(
        archive_dvr_rules=archive.get("dvr_rules") or [],
        client=client,
        selected=select_dvr_rules,
        report=report,
        ledger=ledger,
        remap=remap,
        is_dry_run=is_dry_run,
    )
    await import_core_settings(
        archive_core_settings=archive.get("core_settings") or {},
        client=client,
        selected=select_core_settings,
        report=report,
        ledger=ledger,
        is_dry_run=is_dry_run,
    )
    await import_comskip(
        archive_comskip=archive.get("comskip") or {},
        client=client,
        selected=select_comskip,
        report=report,
        ledger=ledger,
        is_dry_run=is_dry_run,
    )
