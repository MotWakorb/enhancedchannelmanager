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

* **dvr_rules** (:func:`import_dvr_rules`) -> ``EntityType.DVR_RULE``. The
  upstream resource is Dispatcharr's RECURRING RECORDING RULE
  (``/api/channels/recurring-rules/`` — channel + weekly schedule); the category
  was retargeted there by ``…-lsa0s`` after its original ``/api/dvr/rules/``
  guess turned out to have no route at all. Identity is by name/title with a
  SCHEDULE fallback for unnamed rules (``name`` is optional upstream). Its
  ``channel`` FK is remapped through the IdRemapTable; an unresolvable FK is
  failed ``DEPENDENCY_UNRESOLVED`` — identically on apply and on dry-run, per the
  y6zg6 preview-parity rule — and never sent upstream with a dangling source id.
  Dispatcharr's other two DVR surfaces are deliberately out of this category:
  SERIES rules live inside the ``dvr_settings`` core-setting the ``core_settings``
  category already carries, and RECORDINGS are per-instance state, not rules.

SETTINGS categories — APPLY key/value config; NOT entity-create, NOT
id-remapped, NOT ledgered as creates:

* **core_settings** (:func:`import_core_settings`) — apply the archived core/
  global settings to the destination one row at a time
  (``client.update_core_setting``). The archive carries key->value only, and
  Dispatcharr's detail route is keyed by an integer pk, so each key is first
  resolved to the DESTINATION's row id by the run-scoped
  :class:`CoreSettingIdResolver` — one ``GET /api/core/settings/`` per run,
  apply OR dry-run (bead ``…-q6xjl``; the previous key-string URL 404'd on every
  key). A key that resolves is ``updated`` (or ``would_update`` on dry-run); a
  key with no row on the destination is ``failed`` DEPENDENCY_UNRESOLVED on
  BOTH apply and dry-run (bead ``…-y6zg6``) — the preview resolves against the
  SAME destination the apply would, so a dry-run cannot certify WOULD-UPDATE for
  a key the apply then fails on. Reported on the ``EntityType.SETTINGS`` report
  category — never ``created``, never ledgered.
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


class CoreSettingNotFoundError(LookupError):
    """A settings key in the archive has no row on the DESTINATION instance.

    Carries NO payload deliberately: the only caller
    (:func:`_apply_settings_blob`) already holds the key name and builds the
    operator-facing message from its own variable, so the key name can never
    reach a logging sink via this exception's text.
    """


class CoreSettingsUnavailableError(RuntimeError):
    """The destination's core-settings list could not be read at all.

    Distinct from :class:`CoreSettingNotFoundError`: nothing about the archive is
    wrong, the destination just did not answer. Every key in the run fails
    ``UPSTREAM_API_ERROR``. Payload-free for the same reason.
    """


class CoreSettingIdResolver:
    """Resolve a core-setting KEY to the destination's row id, fetching ONCE.

    Dispatcharr's core-settings detail route is ``/api/core/settings/{id}/`` with
    an integer pk — a key-string URL matches no route and 404s
    (enhancedchannelmanager-q6xjl: 7/7 settings failed exactly this way on a
    same-instance round-trip). Row ids are per-instance and are NOT carried in a
    backup artifact (the producer flattens core settings to key->value), so they
    must be resolved against the destination at apply time.

    One instance is shared for the whole apply run — core_settings and comskip
    live in the SAME Dispatcharr settings namespace, so both blobs resolve
    against a single ``GET /api/core/settings/``. The fetch is LAZY (a dry-run or
    an opted-out category costs no request) and its outcome is memoized including
    the failure case, so a dead destination cannot turn into a per-key fetch
    storm.
    """

    def __init__(self, client: DispatcharrClient):
        self._client = client
        self._id_map: dict | None = None
        self._fetch_failed = False

    async def resolve(self, setting_name: str) -> int:
        """Return the destination row id for ``setting_name``.

        Raises:
            CoreSettingsUnavailableError: the settings list could not be read.
            CoreSettingNotFoundError: the destination has no row for this key.
        """
        if self._id_map is None and not self._fetch_failed:
            try:
                self._id_map = await self._client.get_core_setting_id_map()
            except Exception as exc:
                self._fetch_failed = True
                # Exception TYPE name only -- never the exception's message text
                # (no-key-names/no-URLs hygiene: the message could echo a
                # request URL or upstream body). The type alone is enough for an
                # operator to distinguish timeout vs 5xx vs unreachable at 3 AM.
                logger.warning(
                    "[DBAS-SETTINGS] Could not read the destination core-settings "
                    "list (%s); no setting can be resolved this run.",
                    type(exc).__name__,
                )
        if self._fetch_failed:
            # Payload-free and unchained: an upstream error's text can echo the
            # request URL or a response body carrying setting VALUES.
            raise CoreSettingsUnavailableError(
                "Destination core-settings list unavailable"
            ) from None

        setting_id = self._id_map.get(setting_name)
        if not isinstance(setting_id, int):
            raise CoreSettingNotFoundError from None
        return setting_id

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
#
# Upstream resource: Dispatcharr's recurring-recording-rules ViewSet
# (``dispatcharr_client._DVR_RECURRING_RULES_PATH``). A rule is a channel + a
# weekly schedule; ``name`` is OPTIONAL upstream, which is why identity below is
# name-first with a schedule fallback (enhancedchannelmanager-lsa0s).
# ===========================================================================

# FK fields on a DVR rule that point at another restored entity and must be
# remapped before the rule is sent upstream.
_DVR_FK_FIELDS = (("channel", EntityType.CHANNEL),)

# The date/time fields that make an UNNAMED DVR rule what it is. Dispatcharr's
# RecurringRecordingRule leaves ``name`` blank-able (lsa0s recorded fixture:
# ``name`` is not in the schema's ``required`` set), so a name-only identity
# would re-create every unnamed rule on every restore of the same artifact. Two
# rules on the same destination channel, for the same weekdays, over the same
# clock window and the same date range, in the same ``enabled`` state, produce
# the same recordings — treating those as the same rule is what keeps a repeated
# restore idempotent.
#
# ``enabled`` is part of the key but not of this tuple: it needs its own
# normalization (see :func:`_norm_enabled`) rather than the stringify below, and
# it is NOT cosmetic — it decides whether the rule records at all, so an archived
# ENABLED rule must never be satisfied by a disabled one already sitting in the
# same slot on the destination (PR #768 review, Warn 1).
_DVR_SCHEDULE_FIELDS = ("start_time", "end_time", "start_date", "end_date")


def _as_int(value) -> int | None:
    """Coerce an archive/upstream id to ``int``, or ``None`` when it is not one.

    Mirrors the tolerance of :func:`_remap_dvr_fks` (which accepts a stringified
    id) so the identity lookup and the FK rewrite agree on what a usable id is.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _norm_enabled(value) -> bool:
    """Normalize a DVR rule's ``enabled`` flag for the identity key.

    An ABSENT flag normalizes to ``True``: that is the upstream model default, so
    it is also what a create built from such a record would end up with — the key
    therefore compares the state the rule would actually have, not the state the
    archive happened to serialize.
    """
    if value is None:
        return True
    return bool(value)


def _norm_days_of_week(value) -> tuple:
    """Order-insensitive, duplicate-free weekday key; ``()`` when unusable.

    Dispatcharr's serializer stores ``sorted(set(...))`` of ints, but an archive
    row is whatever the source instance serialized at capture time, so the key
    normalizes rather than trusting the order.
    """
    if not isinstance(value, list):
        return ()
    days = set()
    for entry in value:
        try:
            days.add(int(entry))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(days))


def _dvr_schedule_key(record: dict, channel_id) -> tuple | None:
    """Identity key for an unnamed DVR rule, or None when it cannot be formed.

    ``channel_id`` must ALWAYS be a DESTINATION channel id — the archive side
    passes its remapped id, the existing-destination side passes the id the
    destination already reports. Without a channel there is nothing to be
    identical to, so the caller falls back to creating the rule.

    The key spans the destination channel, the weekdays, the ``enabled`` state
    and the date/time fields in :data:`_DVR_SCHEDULE_FIELDS` — everything that
    decides WHETHER and WHEN the rule records.
    """
    if channel_id is None:
        return None
    try:
        channel_key = int(channel_id)
    except (TypeError, ValueError):
        return None
    schedule = tuple(
        str(record.get(field)) if record.get(field) is not None else None
        for field in _DVR_SCHEDULE_FIELDS
    )
    return (
        channel_key,
        _norm_days_of_week(record.get("days_of_week")),
        _norm_enabled(record.get("enabled")),
    ) + schedule


def _index_existing_dvr_by_schedule(records: list[dict]) -> dict[tuple, dict]:
    """Index destination DVR rules by their schedule key (see above).

    NAMED destination rules are indexed too: a name is a label, not a behaviour,
    so an unnamed archive rule that matches a named destination rule's schedule
    is still the same recording and must not be duplicated.
    """
    index: dict[tuple, dict] = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        key = _dvr_schedule_key(rec, rec.get("channel"))
        if key is not None and key not in index:
            index[key] = rec
    return index


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

    Identity is by name/title, falling back to the rule's SCHEDULE
    (:func:`_dvr_schedule_key`) when the archived rule has no name — Dispatcharr
    leaves ``name`` optional on a recurring recording rule, and a name-only
    identity would duplicate every unnamed rule each time the same artifact is
    restored (enhancedchannelmanager-lsa0s). A collision is skipped
    ``ALREADY_EXISTS_IDENTICAL`` and remapped to the existing destination id.

    An unresolvable FK (e.g. ``channel``) fails the rule
    ``DEPENDENCY_UNRESOLVED`` and never sends a dangling source id upstream. That
    outcome is reported IDENTICALLY on dry-run and on apply — same reason, same
    message — because whether the FK resolves is a fact about the run's remap
    state, not about the mode: the channels importer registers a provisional
    remap for every would-create channel, so a dry-run resolves exactly what the
    apply would. (Previously the dry-run reported a would_skip while the apply
    reported a failure, which is the preview-lies defect y6zg6 closed for the
    settings category.) Opt-in via ``selected``.
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
    # Both indexes are a SNAPSHOT of the destination taken once, before the loop,
    # and are never updated as this run creates rows — the same pre-existing
    # limitation ``import_user_agents`` has. Two consequences, neither new here
    # (PR #768 review nits 3/4): two identical rules within ONE archive are both
    # created, and a rule RENAMED on the destination is not matched by name (the
    # schedule fallback only runs for archive rules that have no name of their
    # own). Both duplicate rather than clobber, which is the safe direction.
    existing_by_name = _index_existing_by_name(existing)
    existing_by_schedule = _index_existing_dvr_by_schedule(existing)

    for rec in archive_dvr_rules:
        label = _label(rec, "name", "title")
        source_id = rec.get("id")
        name_key = _norm_name(rec.get("name")) or _norm_name(rec.get("title"))

        if name_key:
            existing_rec = existing_by_name.get(name_key)
        else:
            # Unnamed: match on the schedule, keyed by the DESTINATION channel
            # the rule would be created against. An unresolvable channel yields
            # no key, so the rule falls through to the FK check below and is
            # reported DEPENDENCY_UNRESOLVED rather than guessed at.
            source_channel = _as_int(rec.get("channel"))
            dest_channel = (
                remap.resolve(EntityType.CHANNEL, source_channel)
                if source_channel is not None
                else None
            )
            schedule_key = _dvr_schedule_key(rec, dest_channel)
            existing_rec = (
                existing_by_schedule.get(schedule_key) if schedule_key else None
            )
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
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.DEPENDENCY_UNRESOLVED,
                    label=label,
                    message=(
                        f"DVR rule references a '{unresolved_fk}' that is not "
                        "present on the destination Dispatcharr; nothing was "
                        "created for it."
                    ),
                    source_export_id=source_id,
                )
            )
            logger.warning(
                "[DBAS-SETTINGS] DVR rule '%s' has unresolved FK '%s'; not created.",
                label, unresolved_fk
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
    id_resolver: CoreSettingIdResolver,
) -> None:
    """Apply a key/value settings blob conservatively (core_settings / comskip).

    Safe keys (:func:`is_safe_setting_key`) are PATCHed one row at a time, at the
    destination row id ``id_resolver`` resolves for that key. Dangerous keys are
    skipped (reported by NAME only — never their value). Results land on the
    ``EntityType.SETTINGS`` report category as ``updated`` / ``skipped`` /
    ``failed`` on apply — or ``would_update`` / ``would_skip`` on dry-run.

    ``failed`` / ``failure_details`` are populated on BOTH apply AND dry-run
    (enhancedchannelmanager-y6zg6): whether a key resolves to a destination row
    id is a FACT about the destination, true regardless of whether this run
    actually applies, so the dry-run branch resolves each safe key through the
    SAME ``id_resolver`` the apply branch uses before deciding would-update vs
    would-fail. There is no separate ``would_fail`` counter — this mirrors the
    existing convention for per-item conflicts that are facts about the source
    data (see ``dbas/importers/channels.py``'s ambiguous-null-key collision and
    ``tasks/dbas_sync.py``'s ``_counts_from_report`` docstring). Before this fix,
    the dry-run branch never contacted upstream at all and unconditionally
    reported would_update, which is what let a preview certify "Settings 7 WILL
    UPDATE / 0 FAILED" for an apply that then failed 7/7 (q6xjl). NEVER ledgered
    (settings are config, not created entities — rollback of settings is out of
    scope). NEVER logs/echoes a setting VALUE.
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

        # Resolve the DESTINATION row id for this key BEFORE deciding
        # would-update vs would-fail — the detail route is keyed by integer pk,
        # and an archive carries no ids (enhancedchannelmanager-q6xjl). This
        # runs on dry-run too (enhancedchannelmanager-y6zg6): whether the key
        # resolves is a fact about the destination the preview must surface, not
        # something only the apply run discovers. The resolver fetches the list
        # once per run (dry-run OR apply) and serves every later lookup —
        # including the comskip blob's — from memory.
        try:
            setting_id = await id_resolver.resolve(setting_name)
        except CoreSettingNotFoundError:
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.DEPENDENCY_UNRESOLVED,
                    label=f"{source_label}:{setting_name}",
                    message=(
                        f"Setting key '{setting_name}' is not present on the "
                        "destination Dispatcharr; nothing was applied for it."
                    ),
                    source_export_id=None,
                )
            )
            logger.warning(
                "[DBAS-SETTINGS] %s setting %s has no row on the destination; "
                "not applied.",
                source_label,
                _safe_key_label(setting_index, setting_name),
            )
            continue
        except Exception:
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=FailureReason.UPSTREAM_API_ERROR,
                    label=f"{source_label}:{setting_name}",
                    message=(
                        "Could not read the destination Dispatcharr settings list, "
                        f"so setting '{setting_name}' could not be resolved/applied."
                    ),
                    source_export_id=None,
                )
            )
            logger.warning(
                "[DBAS-SETTINGS] Could not resolve %s setting %s.",
                source_label,
                _safe_key_label(setting_index, setting_name),
            )
            continue

        if is_dry_run:
            cat.would_update += 1
            continue

        try:
            await client.update_core_setting(setting_id, setting_value)
        except Exception:
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
    id_resolver: CoreSettingIdResolver | None = None,
) -> None:
    """Apply archived core/global settings conservatively (denylist; per-row PATCH).

    See module docstring: dangerous keys are skipped (by name), safe keys applied,
    reported updated/skipped on ``EntityType.SETTINGS``, NEVER created, NEVER
    ledgered (settings rollback is out of scope). NEVER logs a setting value.

    On ``is_dry_run=True`` a safe key is still resolved against the destination
    (bead ``…-y6zg6``): a key that resolves reports ``would_update``; a key with
    no destination row reports ``failed`` DEPENDENCY_UNRESOLVED — the SAME
    reason/wording the apply path uses — so the preview cannot certify
    WOULD-UPDATE for a key the apply run then fails on.

    Args:
        id_resolver: The run-scoped key->row-id resolver. Pass the SAME instance
            used for :func:`import_comskip` so one run (apply OR dry-run) costs
            one ``GET /api/core/settings/``; omitted, a private one is created
            (still one GET, just not shared).
    """
    await _apply_settings_blob(
        archive_settings=archive_core_settings,
        client=client,
        selected=selected,
        report=report,
        is_dry_run=is_dry_run,
        source_label="core_settings",
        id_resolver=id_resolver or CoreSettingIdResolver(client),
    )


async def import_comskip(
    *,
    archive_comskip: dict,
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,  # accepted for symmetry; comskip settings NEVER ledger
    is_dry_run: bool = False,
    id_resolver: CoreSettingIdResolver | None = None,
) -> None:
    """Apply archived comskip config conservatively — same shape as core settings.

    Dangerous keys skipped (by name), safe keys applied; reported updated/skipped
    on apply, would_update/failed(DEPENDENCY_UNRESOLVED) on dry-run per key
    resolution (bead ``…-y6zg6`` — see :func:`import_core_settings`); never
    created, never ledgered. NEVER logs a setting value.

    Comskip config lives in the SAME Dispatcharr core-settings namespace (there is
    no comskip endpoint), so ``id_resolver`` is the same run-scoped resolver the
    core_settings blob uses — see :func:`import_core_settings`.
    """
    await _apply_settings_blob(
        archive_settings=archive_comskip,
        client=client,
        selected=selected,
        report=report,
        is_dry_run=is_dry_run,
        source_label="comskip",
        id_resolver=id_resolver or CoreSettingIdResolver(client),
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
    # ONE resolver for both settings blobs — they share Dispatcharr's single
    # core-settings namespace, so the run costs one GET (q6xjl).
    id_resolver = CoreSettingIdResolver(client)
    await import_core_settings(
        archive_core_settings=archive.get("core_settings") or {},
        client=client,
        selected=select_core_settings,
        report=report,
        ledger=ledger,
        is_dry_run=is_dry_run,
        id_resolver=id_resolver,
    )
    await import_comskip(
        archive_comskip=archive.get("comskip") or {},
        client=client,
        selected=select_comskip,
        report=report,
        ledger=ledger,
        is_dry_run=is_dry_run,
        id_resolver=id_resolver,
    )
