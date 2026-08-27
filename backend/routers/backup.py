"""
Backup & Restore router — create and restore ECM configuration backups.

Backs up: settings.json, journal.db, uploads/logos/ (tls/ and m3u_uploads/
are deliberately excluded from the plaintext artifact; see BACKUP_DIRS)
YAML export: settings + DB tables + Dispatcharr state in a single file.
"""
import asyncio
import contextvars
import hashlib
import io
import json
import logging
import os
import re
import shutil
import sqlite3
import stat
import string
import tempfile
import time
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import BinaryIO, Optional
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

import yaml
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from auth import (
    RequireAdminIfEnabled,
    RequireHumanAdminIfEnabled,
    ResolveIsMcpServicePrincipalIfEnabled,
)
from auth.dependencies import (
    get_current_user,
    get_token_from_request,
    instance_has_operator_identity,
    is_mcp_service_principal,
)
from config import (
    ADMIN_ONLY_READ_REDACTED_FIELDS,
    CONFIG_DIR,
    CONFIG_FILE,
    DispatcharrSettings,
    get_settings,
    prepare_settings_data,
    save_settings,
    clear_settings_cache,
)
from credential_sentinel import REDACTION_SENTINEL, strip_redaction_sentinels
from dbas import artifact_crypto
from dbas.archive_keys import (
    ARCHIVE_EPG_TVG_ID_KEY,
    EPG_INDEX_MAX_ROWS,
    as_instant,
    as_int,
)
from dbas.importers.logos import MAX_LOGO_BYTES, remote_logo_url, safe_logo_basename
from dbas.restore_contracts import ChannelReattachMode
from dbas.importers.settings_agents import is_safe_setting_key
from database import close_db, get_engine, get_session, init_db, JOURNAL_DB_FILE
from dispatcharr_client import get_client, reset_client
from models import (
    ChannelPipelineRule,
    DummyEPGProfile,
    DummyEPGChannelAssignment,
    EventSyncExclusion,
    EventSyncReview,
    FFmpegProfile,
    NormalizationRuleGroup,
    NormalizationRule,
    ScheduledTask,
    TaskSchedule,
    TagGroup,
    Tag,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/backup", tags=["Backup"])


def _resolve_backup_normalization_group_ids(item: dict, session) -> str | None:
    """Resolve normalization_group_ids from backup data, with backward compat."""
    norm_ids = item.get("normalization_group_ids")
    if norm_ids is not None:
        return json.dumps(norm_ids) if norm_ids else None
    if item.get("normalize_names"):
        from models import NormalizationRuleGroup
        groups = session.query(NormalizationRuleGroup.id).filter(
            NormalizationRuleGroup.enabled == True
        ).order_by(NormalizationRuleGroup.priority).all()
        return json.dumps([g.id for g in groups]) if groups else None
    return None

# Plaintext legacy ZIPs carry only files that are safe under the backup
# confidentiality policy. ``tls`` contains private keys and ``m3u_uploads`` can
# contain live provider credentials in stream URLs, so neither may enter an
# unencrypted artifact. DBAS captures the restorable provider configuration in
# its redacted categories; encrypted credential-bearing migration artifacts use
# that DBAS path rather than this legacy file-copy path.
BACKUP_DIRS = ["uploads/logos"]

# Restore accepts what OLDER ECM builds produced. A legacy artifact taken before
# the confidentiality policy above still carries ``tls`` and ``m3u_uploads``, and
# silently discarding them would turn an upgrade into unannounced data loss for
# an operator whose only copy of a certificate lives in that ZIP. Restoring the
# operator's own material is not a confidentiality question; producing new
# plaintext copies of it is, and that is what BACKUP_DIRS governs.
LEGACY_RESTORE_DIRS = ["uploads/logos", "tls", "m3u_uploads"]

# App version for manifest (imported at call time to avoid circular imports).
#
# IMPORTANT (versioning.md touchpoint): APP_VERSION is one of three version
# literals that must agree with frontend/package.json and backend/main.py.
# This is a CONVENTION, not an enforced guarantee: the CI job and
# scripts/check_version_consistency.py that used to fail the PR on divergence
# were removed. Do NOT rename it, change its shape, or repurpose it. It is an INFORMATIONAL human-readable string ("which
# ECM build produced this artifact") — it is NOT a compatibility gate.
APP_VERSION = "0.18.1-0160"

# DBAS backup-artifact schema version (ADR-008 D1 / ADR-012 D1). This is a
# DEDICATED, MONOTONIC INTEGER that is DISTINCT from the human-readable
# APP_VERSION string above. Restore gates on THIS value (manifest_version <=
# BACKUP_SCHEMA_VERSION accepted; a newer artifact is refused with the
# user-facing "Unsupported backup version"). Bump by +1 only on a
# backward-incompatible artifact-format change; never tie it to APP_VERSION.
# Starts at 1 for the first v0.18.0 DBAS artifact (0i2vt.7).
BACKUP_SCHEMA_VERSION = 1

# The redaction placeholder. Defined once in the ``credential_sentinel`` leaf
# module so the restore side can recognize what this side wrote (bead …-6pilh):
# the DBAS importers strip it rather than writing it into a destination
# credential field, and ``credential_is_present`` refuses to read it as a
# configured credential. ``REDACTED`` is kept as the local name because the
# whole module (and the shipped artifact format) is written against it.
REDACTED = REDACTION_SENTINEL

# Fields in DispatcharrSettings that must never appear raw in an exported
# backup. Mirrors the YAML export contract for parity (bd-l0nhi).
# bd-jmi1c (GH #273): both ``dispatcharr_api_key`` (canonical) and the
# legacy ``api_key`` are listed so the back-compat mirror in
# ``config.save_settings`` doesn't accidentally leak a value the canonical
# redaction would have caught.
# Back-compat: drop 'api_key' from this tuple in v0.19.0 (bd-ewm4h) when
# the legacy field is removed from the model. The debug-bundle redactor in
# routers/channel_pipeline.py imports this tuple, so a single edit there
# propagates everywhere.
#
# READ-PARITY WITH GET /api/settings (bead …-9kwzp.9). The trailing entries are
# DERIVED from ``config.ADMIN_ONLY_READ_REDACTED_FIELDS``, not restated, and
# that is the fix rather than an implementation detail. Bead 9ej7f made GET
# /api/settings withhold that partition from every caller
# ``routers.settings._resolve_settings_admin`` classifies as non-admin —
# including the MCP service principal. But GET /api/backup/create, /export and
# /saved/{filename} carry ``RequireAdminIfEnabled``, which ADMITS that
# principal (``_build_mcp_service_principal`` sets ``is_admin=True``), so while
# this tuple was a hand-maintained literal it silently defeated two thirds of
# 9ej7f: ``telegram_bot_token`` happened to be listed, ``discord_webhook_url``
# and ``telegram_chat_id`` were not, and neither is matched by
# ``_ALERT_METHOD_CREDENTIAL_KEYS`` (that set matches keys INSIDE
# ``alert_methods.config`` JSON, not top-level settings fields). The principal
# just refused those values on the settings endpoint read them out of a
# standard backup artifact instead.
#
# Adding two strings would have fixed the two fields and left the class open:
# every future addition to the settings read-redaction partition would leak the
# same way until someone remembered this second list. Deriving closes it — one
# edit in ``config`` now moves both surfaces. Do NOT re-inline these names.
# ``dict.fromkeys`` dedupes the overlap (``telegram_bot_token`` is in both
# halves) while keeping the historical order stable for the artifact contract.
#
# THIRD-PARTY BEARER CREDENTIALS THE EXACT-MATCH DENYLIST MISSED (bead …-gi4zn).
# ``_REDACT_KEYS`` matches key names EXACTLY, which is the right RUNTIME rule —
# a substring rule on "url" would rewrite the boolean ``show_stream_urls`` to a
# string sentinel. Its cost is that a newly added ``<vendor>_api_key`` ships in
# clear until someone remembers this list, and three of them had:
# ``emby_api_key`` and ``jellyfin_api_key`` are admin API keys for the
# operator's media server, and ``plex_token`` is a bearer credential for a Plex
# ACCOUNT. All three were exported verbatim in every standard artifact.
# ``smtp_user`` is the identity half of a pair whose secret half
# (``smtp_password``) was already listed — the same asymmetry the M3U username
# had. The class is closed by
# ``tests/routers/test_gi4zn_standard_artifact_full_redaction.py::
# test_no_credential_shaped_settings_field_is_left_unredacted``, which reads the
# live settings model, so the NEXT vendor field fails a required check rather
# than a drill.
_SETTINGS_CREDENTIAL_FIELDS: tuple[str, ...] = tuple(dict.fromkeys((
    "password",
    "dispatcharr_api_key",
    "api_key",
    "smtp_password",
    "smtp_user",
    "telegram_bot_token",
    "mcp_api_key",
    "emby_api_key",
    "jellyfin_api_key",
    "plex_token",
    *sorted(ADMIN_ONLY_READ_REDACTED_FIELDS),
)))

# Credential-class keys that may live inside alert_methods.config JSON. Matches
# the masking set in AlertMethod.to_dict (models.py) so backup redaction stays
# in lock-step with the API-response masking.
#
# CORRECTION (bead enhancedchannelmanager-9kwzp.13): this comment used to end
# "...the API-response masking ALREADY SHIPPED TO CLIENTS", and that clause was
# false when it was written. The masking existed on the model, but the two read
# routes in routers/alert_methods.py hand-rolled their response dicts and
# emitted alert_methods.config VERBATIM, so no API response was masked at all.
# The false claim is why the hole stayed invisible: a reader working out where
# alert credentials can leak got "the API already masks these" from here and
# stopped. Both read routes now serialize through
# AlertMethod.to_dict(include_sensitive=False), so the lock-step claim is true
# as written; if you ever make one of those handlers build its own dict again,
# this comment goes false again and this tuple is the thing that silently
# drifts.
_ALERT_METHOD_CREDENTIAL_KEYS = ("password", "bot_token", "webhook_url", "api_key")

# The IDENTITY half of the same alert-method config blobs (bead …-gi4zn). An
# SMTP alert method stores ``username`` beside its ``password`` and a Telegram
# one stores ``chat_id`` beside its ``bot_token``; only the secret halves were
# scrubbed, so the standard artifact's journal.db carried the relay account name
# and the chat capability in clear. ``telegram_chat_id`` is already treated as a
# bearer capability at the SETTINGS level
# (``config.ADMIN_ONLY_READ_REDACTED_FIELDS``) — the identical value nested
# inside alert_methods.config was not, which is the same
# protected-beside-unprotected asymmetry the M3U username had.
#
# DELIBERATELY SEPARATE from :data:`_ALERT_METHOD_CREDENTIAL_KEYS` rather than
# appended to it, for two reasons. That tuple's docstring asserts lock-step with
# ``AlertMethod.to_dict``'s masking set, and that claim must stay literally true;
# changing the API-response masking is a different decision on a different
# surface. And it feeds :data:`_REDACT_KEYS`, which is matched against dict keys
# in EVERY category — a global ``chat_id`` entry would be scope this bead did not
# establish. These keys are therefore applied only where they were found
# leaking: the journal.db alert_methods scrub, and its restore-side merge-back.
_ALERT_METHOD_IDENTITY_KEYS = ("username", "chat_id")

# Every alert_methods.config key the backup scrubs and the restore merges back.
# One tuple so the two halves of that round-trip cannot drift: a key scrubbed on
# the way out but not merged on the way back in would be silently DESTROYED by a
# legacy-ZIP restore.
_ALERT_METHOD_PROTECTED_KEYS = tuple(
    dict.fromkeys(_ALERT_METHOD_CREDENTIAL_KEYS + _ALERT_METHOD_IDENTITY_KEYS)
)

# journal.db tables holding ECM's OWN authentication and identity state, emptied
# out of the standard artifact (bead …-gi4zn, external security review findings
# A-1 / A-2). Until this landed, the scrub visited ``alert_methods`` and nothing
# else, so a standard artifact — the DEFAULT artifact, the one an operator
# attaches to a support ticket — handed over, read straight out of the archived
# ``journal.db`` with sqlite3:
#
#   * ``users.password_hash``   — the operator's own bcrypt admin hash, which is
#     offline-crackable at the attacker's leisure. ``password_hash`` was never in
#     :data:`_REDACT_KEYS` and no key denylist covers a DB COLUMN anyway.
#   * ``users.username`` / ``email`` — the account names to crack it against.
#   * ``user_sessions.refresh_token_hash`` / ``prior_refresh_token_hash`` — live
#     session material; ``ip_address`` / ``user_agent`` — forensic metadata about
#     where and with what the operator administers the instance.
#   * ``password_reset_tokens.token_hash`` — an account-recovery credential.
#   * ``user_identities.provider`` / ``external_id`` / ``identifier`` — the ECM
#     admin correlated to their OIDC / SAML / LDAP identity at a THIRD-PARTY IdP.
#     That is third-party identity, squarely inside this bead's own property, and
#     the sixth instance of the protected-secret-beside-unprotected-identity
#     asymmetry the rest of this bead removed.
#
# THE ROWS ARE DELETED, NOT MASKED, and that is a deliberate availability
# decision rather than a maximalist one. ECM's first-run setup keys on
# ``session.query(User).count() == 0`` (``auth/routes.py`` -> ``/auth/setup-
# required`` and ``/auth/setup``, surfaced by ``ProtectedRoute.tsx``). A users
# table left populated but stripped of usable ``password_hash`` values is
# therefore the ONE state that is both unauthenticatable AND ineligible for the
# setup wizard — a restored instance nobody can log into, which is a worse
# outcome than the leak. Empty, the shipped first-run path takes over and the
# operator creates a new admin. What the operator re-establishes on a
# fresh-instance restore is exactly: their ECM account(s). Everything else in the
# artifact restores unchanged.
#
# The DESTINATION's own accounts are never collateral: a restore into an instance
# that already has users re-asserts them over the artifact
# (:func:`_capture_existing_auth_rows` / :func:`_reassert_auth_rows_after_restore`),
# so an admin restoring a backup is not logged out of their own instance.
#
# The ENCRYPTED cred-carrying artifact (``include_credentials``) is unaffected —
# it returns before the scrub runs at all — so a migration still carries the
# operator's accounts and restores login without re-entry.
#
# Order is DELETE order: dependents before ``users``. SQLite does not enforce the
# FKs unless ``PRAGMA foreign_keys`` is on, but ordering it correctly means the
# purge does not depend on that pragma being off.
_AUTH_IDENTITY_TABLES: tuple[str, ...] = (
    "user_sessions",
    "password_reset_tokens",
    "user_identities",
    "users",
)


# ---------------------------------------------------------------------------
# THE STANDARD ARTIFACT'S journal.db TABLE ALLOWLIST (bead …-gi4zn, round 3)
#
# WHY THIS IS AN ALLOWLIST. Three review rounds each found more tables that
# should not ship, and each round fixed the tables it had found:
# ``alert_methods`` first, then :data:`_AUTH_IDENTITY_TABLES`, then
# ``session_telemetry`` (Emby / Plex / Jellyfin account names),
# ``unique_client_connections`` (viewer IPs and usernames) and
# ``m3u_digest_settings.email_recipients``. That is the same failure shape as
# ``_REDACT_KEYS`` matching key names exactly, which is why ``emby_api_key``
# shipped in cleartext: a denylist has to be COMPLETE, and it is maintained by
# people who keep discovering it isn't.
#
# So the direction is inverted. This dict enumerates what a standard artifact is
# ALLOWED to carry out of journal.db; :func:`_scrub_journal_db_in_place` DROPS
# every other table. A table added to the schema later ships NOTHING until
# someone deliberately permits it — absent by construction, not by remembering to
# add it to a removal list. Same make-it-impossible shape the project already
# uses for ``OperationLedger`` being the only writer of the operation counters.
#
# The measured cost of the old direction, from a real live database: it carried
# EIGHT tables that no current model declares at all — ``services``,
# ``health_checks``, ``incidents``, ``incident_updates``, ``maintenance_windows``,
# ``service_alert_rules``, ``service_alert_history`` (the removed pre-v0.13
# health-monitor subsystem, see docs/database_migrations.md) and
# ``popularity_rules`` (removed in v0.11.0-0005). ``services.health_endpoint`` is
# an operator URL and ``incidents.created_by`` / ``service_alert_history.
# acknowledged_by`` are account names. No denylist maintained by reading
# ``models.py`` could ever have seen them, because they are not in models.py.
#
# THE SELECTION RULE. A standard artifact is the redacted, shareable,
# disaster-recovery artifact, and for the LEGACY ``_create_backup_zip`` path it is
# the ONLY carrier of ECM's own configuration (that ZIP is settings.json +
# journal.db; it has no categories/*.yaml). So a table is permitted when it is
# CONFIGURATION the operator authored and a restore needs, and dropped when it is
# history, telemetry, transient workflow state, credential material, or anything
# whose contents are unbounded free text. The default is drop; each entry below
# is a decision with its reason attached.
#
# Values are the reason the table is permitted. Keep them specific — this dict is
# the documentation of record for what a shared artifact contains.
_STANDARD_ARTIFACT_TABLES: dict[str, str] = {
    "alembic_version": (
        "Schema revision marker (one opaque revision hash). Dropping it makes a "
        "restored database look like a legacy install to _bootstrap_alembic, "
        "which then stamps or migrates against the wrong baseline. Carries no "
        "operator or third-party value."
    ),
    "ecm_oneshot_migrations": (
        "One-shot data-migration bookkeeping (name + applied_at). Dropping it "
        "re-runs already-completed one-shot migrations against restored data. "
        "Two opaque columns, no operator or third-party value."
    ),
    "alert_methods": (
        "The operator's notification channels — configuration a restore needs. "
        "The credential AND identity keys inside the config JSON are scrubbed "
        "separately (_ALERT_METHOD_PROTECTED_KEYS), and a config that cannot be "
        "parsed loses its whole blob to the sentinel."
    ),
    "auto_creation_rules": (
        "Channel Pipeline rules — the most substantial hand-authored "
        "configuration in ECM and the primary thing a disaster-recovery restore "
        "exists to bring back. Columns are rule logic (conditions, actions, "
        "regex, sort order); no credential or identity column."
    ),
    "normalization_rule_groups": "Normalization rule groups — operator-authored configuration.",
    "normalization_rules": "Normalization rules — operator-authored configuration.",
    "tag_groups": "Tag groups — the normalization vocabulary, operator-authored configuration.",
    "tags": "Tags — the normalization vocabulary, operator-authored configuration.",
    "ffmpeg_profiles": "FFmpeg profiles — operator-authored configuration (name + config).",
    "dummy_epg_profiles": (
        "Dummy EPG profiles — substantial hand-authored template and pattern "
        "configuration. Its URL-template columns are free text, so the "
        "value-level URL credential scrub applies (see "
        ":func:`_scrub_permitted_table_cells`)."
    ),
    "dummy_epg_channel_assignments": (
        "Binds Dummy EPG profiles to channels. The profiles are not usable "
        "without it and it carries no identity — channel id/name and a tvg-id "
        "override."
    ),
    "scheduled_tasks": (
        "Task enable/schedule/alert configuration. The config JSON goes through "
        "the same deep credential redaction the YAML categories get."
    ),
    "task_schedules": (
        "The live schedule rows (task, cadence, parameters). Same deep "
        "redaction over the parameters JSON."
    ),
    "hidden_channel_groups": (
        "A small operator display preference (group id, group name, hidden_at). "
        "No identity, no credential, and it is annoying to re-establish by hand."
    ),
}

# Every other table a model declares, with the reason a STANDARD artifact drops
# it. This dict changes NOTHING about what ships — the allowlist above is the
# only thing the scrub reads, so an unclassified table is dropped either way.
# Its job is to force a DECISION: ``test_every_journal_db_table_is_classified``
# fails when a model declares a table that appears in neither dict, so a new
# table cannot reach production unclassified. Safe-by-default AND deliberate.
#
# Grouped by the reason, which is also the argument for the grouping:
_STANDARD_ARTIFACT_EXCLUDED: dict[str, str] = {
    # (a) ECM's OWN authentication and identity state (findings A-1 / A-2).
    "users": "Operator account rows — bcrypt password hash, username, email.",
    "user_sessions": "Live session material plus the IP and user agent the operator administers from.",
    # Value on a continuation line, not because it is long but because
    # ``KeywordDetector`` matches PER LINE: a denylisted keyword in the key
    # beside a quoted value on the same line was a detect-secrets finding
    # while ``scripts/check_secrets.py`` existed, and that guard deliberately
    # disabled the inline ``allowlist secret`` pragma. The guard is gone; the
    # shape below is kept because the reasoning still holds for a human
    # reader.
    "password_reset_tokens": (
        "An account-recovery credential (token_hash), and the rows are useless "
        "without the accounts this artifact also does not carry."
    ),
    "user_identities": (
        "The ECM admin correlated to their OIDC / SAML / LDAP identity at a "
        "THIRD-PARTY IdP (provider, external_id, identifier)."
    ),
    # (b) Third-party and viewer identity.
    "session_telemetry": (
        "Third-party media-server account names and ids — emby_user_name, "
        "plex_user_name, jellyfin_user_name, plus dispatcharr_username and "
        "user_id. Squarely inside this bead's stated property."
    ),
    "session_telemetry_user_daily": "Per-viewer watch aggregates keyed by user_id.",
    "unique_client_connections": (
        "Viewer identities and network addresses — ip_address, username, user_id."
    ),
    # (c) Credential material of the operator's own.
    "cloud_storage_targets": (
        "A credential store. The credentials column is Fernet ciphertext at rest "
        "(ADR-012 D3), and ciphertext is still credential material in an artifact "
        "whose whole purpose is being safe to share. Re-establish after restore."
    ),
    "sync_targets": (
        "Same: a credentials column plus the target base_url. Re-establish after "
        "restore."
    ),
    # (d) Personal data of people who are not the operator.
    "m3u_digest_settings": (
        "email_recipients is a JSON list of personal email addresses. The rest of "
        "the table is a handful of toggles and two pattern lists, which is a "
        "cheaper thing to re-enter than a leak is to undo. Whole-table decisions "
        "only — a per-column carve-out here would rebuild the denylist this "
        "allowlist replaced."
    ),
    # (e) Unbounded free text, which can quote anything including credentials.
    "journal_entries": (
        "ECM's audit log. before_value / after_value are arbitrary JSON snapshots "
        "of whatever entity was mutated, so no static reading of the schema can "
        "bound what they contain. History, not configuration: a restored instance "
        "is fully usable without it."
    ),
    "notifications": "Transient in-app feed; message / action_url / extra_data are free text.",
    "task_executions": "Run history; error / message / details quote upstream failures verbatim.",
    "auto_creation_executions": (
        "Channel Pipeline run history; execution_log, error_message and the "
        "created/modified entity blobs are unbounded."
    ),
    "auto_creation_snapshots": "Rollback snapshots of channel state tied to executions — history.",
    "auto_creation_conflicts": "Per-execution conflict detail — history.",
    "stream_stats": (
        "Probe results. error_message quotes the probed URL, and an Xtream Codes "
        "stream URL carries its credential in PATH segments "
        "(/live/<user>/<pass>/<id>.ts) where no query-string rule can see it. "
        "Derived data that re-probes."
    ),
    "rule_lint_findings": "Derived analyzer output; recomputed on demand.",
    # (f) Derived or recomputable telemetry with no disaster-recovery value.
    "bandwidth_daily": (
        "Daily bandwidth aggregates. Observed history rather than configuration; "
        "a restored instance starts accumulating its own."
    ),
    "channel_bandwidth": (
        "Per-channel, per-day bandwidth and viewer counts. Observed history, not "
        "configuration."
    ),
    "channel_popularity_scores": (
        "Derived ranking scores, recomputed from telemetry the artifact also does "
        "not carry."
    ),
    "channel_watch_stats": (
        "Legacy per-channel watch counters, superseded by the session_telemetry "
        "rollups. Observed history, not configuration."
    ),
    "session_telemetry_provider_daily": (
        "Per-provider daily telemetry rollup. Observed history, and it is derived "
        "from session_telemetry, which is dropped for carrying viewer identity."
    ),
    "telemetry_rollup_state": (
        "Rollup cursor and last_run_error bookkeeping. Meaningless without the "
        "telemetry tables it tracks, and it rebuilds itself on the next run."
    ),
    "m3u_snapshots": (
        "Point-in-time snapshots of provider group and stream listings — history, "
        "refetched wholesale on the next M3U refresh."
    ),
    "m3u_change_logs": (
        "Provider add/remove change history, including stream name lists. Observed "
        "history, not configuration."
    ),
    # (g) Transient workflow state that regenerates.
    "pending_merges": "The merge review queue — transient, regenerates on the next run.",
    "pending_merge_journal": "Merge review action history, keyed by actor_token_id.",
    "event_sync_reviews": "The event-sync review queue — transient, regenerates.",
    "event_sync_exclusions": (
        "Operator never-attach decisions. Dropped rather than kept because the "
        "evidence column is an unbounded blob and the rows are keyed by "
        "actor_token_id; losing them re-surfaces a suppressed stream in the "
        "review queue, which is recoverable rather than destructive."
    ),
}


class BackupScrubError(RuntimeError):
    """The journal.db scrub could not run to completion, so nothing ships.

    Bead …-gi4zn, external security review finding A-3: every failure path in
    :func:`_scrub_journal_db_to_temp` used to fail OPEN — an unopenable database,
    an unreadable table and an unparseable ``alert_methods.config`` row each fell
    back to shipping the RAW bytes. The reviewer seeded a truncated config blob
    and read its SMTP secret straight out of the built artifact while valid rows
    in the same database were correctly redacted.

    A redactor that cannot redact must not ship. Raising fails the whole backup
    rather than emitting a journal.db-less artifact, because a backup that
    silently drops the operator's entire ECM state is data loss wearing a success
    response; a loud failure is recoverable and cannot leak.
    ``build_backup_artifact`` already unlinks its partial ZIP and sidecar on any
    exception, and :func:`_create_backup_zip`'s caller turns this into a 500.
    """


# SINGLE shared credential-key denylist for the DBAS artifact (0i2vt.7, ADR-012
# D1 redact-by-default). Used by the NON-BYPASSABLE deep redactor
# (_redact_credentials_deep) that runs over EVERY category — including
# Dispatcharr-sourced sections (M3U / EPG accounts), which the shipped YAML
# export does NOT scrub on its own.
#
# CORRECTION (bead …-6pilh, verified against Dispatcharr 0.28.2 source): this
# comment previously claimed Dispatcharr never returns the M3U password. It
# does. ``M3UAccountSerializer`` marks ``password`` ``write_only``, but its
# ``to_representation`` then RE-ADDS it (``data["password"] = instance.password
# or ""``) for any caller with ``user_level >= 10`` — which ECM always is. The
# value is stored and returned in CLEARTEXT; the SHA1-at-fetch note in
# docs/dispatcharr_api.md is about the SCHEDULES-DIRECT (EPG) password, which
# genuinely is write-only with no admin re-add, and does not transfer to M3U.
# So this redactor is not merely defense-in-depth for M3U accounts — it is the
# only thing keeping a live provider password out of the artifact. Correctness
# on the restore side is the matching half: dbas/importers strip the sentinel
# rather than writing it into the destination credential field.
#
# This union folds in the settings + alert-method denylists so there is exactly
# one list to maintain. Matched case-insensitively against dict keys.
_REDACT_KEYS = frozenset(
    {k.lower() for k in _SETTINGS_CREDENTIAL_FIELDS}
    | {k.lower() for k in _ALERT_METHOD_CREDENTIAL_KEYS}
    | {
        # Dispatcharr / cloud-target credential-class keys that can ride along
        # in gathered sections. Keep additive — never remove without confirming
        # the field is not credential-bearing.
        "password",
        "passwd",
        "secret",
        "secret_key",
        "access_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "private_key",
        "auth_token",
        "bearer_token",
        # No current writer puts a passphrase into alert_methods.config or
        # task_schedules.parameters — DbasBackupTask.get_config deliberately
        # omits it — so this closes the class against the NEXT writer rather
        # than a measured exposure (bead …-04c0u.13 review).
        "passphrase",
    }
)

# Stream-record keys that are credential-class for an EMBEDDED channel stream and
# must NEVER be carried in the channels producer (7i8rf). A Dispatcharr/IPTV
# stream URL embeds provider credentials in its path/query
# (``.../<user>/<pass>/<id>``); ``stream_hash`` / ``custom_url`` are equivalent
# leak vectors. The channels producer embeds each stream as ID + the SAFE match
# fields the restore matcher uses (name + m3u_account) ONLY — see
# ``_safe_embedded_stream``. ``url`` is intentionally NOT added to the global
# ``_REDACT_KEYS`` denylist because the M3U/EPG/settings categories legitimately
# carry an operator-typed instance ``url`` that the restore needs; URL handling
# for streams is therefore scoped to the producer that emits them.
#
# THE ARTIFACT IS NOT THE ONLY PRODUCER, and assuming it was is how bead …-msqf7
# happened. Cross-instance SYNC gathers channels WITH their embedded streams
# (``tasks.dbas_sync_engine._gather_live_channels``) and carries the stream
# ``url`` deliberately — it is the stream matcher's Tier-1 identity. That
# producer is covered by the VALUE rule instead
# (:func:`_rewrite_known_credential_segments`), not by this field set.
_STREAM_CREDENTIAL_FIELDS = frozenset({"url", "custom_url", "stream_hash"})

# The IDENTITY half of a THIRD-PARTY provider credential (bead …-gi4zn).
#
# WHY A USERNAME IS A CREDENTIAL. The 2026-08-05 drill (run 3, finding F4) found
# an Xtream Codes account's ``username`` in clear in a standard artifact beside a
# correctly-redacted ``password``, in BOTH places it appears — the account row
# and ``profiles[].custom_properties.user_info``. For an XC provider the username
# is half the credential pair and the half that identifies the SUBSCRIPTION: ECM
# renders XC stream URLs that contain it. A standard artifact is the DEFAULT
# artifact, is described to operators simply as "redacted", and is what gets
# attached to support tickets and forum posts. So the PO's 2026-08-05 decision is
# that the standard artifact is FULLY redacted: it carries no value that
# identifies OR authenticates against a third-party service.
#
# SEPARATE FROM :data:`_REDACT_KEYS` because ``username`` is the one key whose
# meaning depends on WHOSE service it names — see
# :data:`_IDENTITY_EXEMPT_CATEGORIES`. Every other consumer of the deep redactor
# gets identity redaction by DEFAULT (fail-closed); a caller must name its
# exemption to opt out.
_PROVIDER_IDENTITY_KEYS = frozenset({"username"})

# The ONLY categories exempt from :data:`_PROVIDER_IDENTITY_KEYS`.
#
# ``dispatcharr_users`` is the operator's OWN Dispatcharr instance's account
# list, not a third-party subscription, so it is outside the property this bead
# establishes. It is also load-bearing: ``dbas.importers.users`` CREATES each
# user by ``username`` and uses it for the destination-collision check, so a
# sentinel there would not merely degrade the category — it would delete the
# restore path (every user would collide or be created named ``***REDACTED***``).
#
# Kept as a closed set rather than a per-call flag so the exemption is auditable
# in one place, and pinned by
# ``tests/routers/test_gi4zn_standard_artifact_full_redaction.py::
# test_identity_exemption_is_exactly_one_named_category`` so it cannot quietly
# grow into a general escape hatch.
_IDENTITY_EXEMPT_CATEGORIES: frozenset[str] = frozenset({"dispatcharr_users"})

# Query-parameter names that make a URL credential-bearing. Providers routinely
# put the whole credential in the query string (``get.php?username=…&password=…``
# for a plain-M3U account, ``xmltv.php?username=…&password=…`` for an XC-derived
# EPG source), where NO credential-named dict key carries it and a key denylist
# is therefore blind. Short aliases are included because provider URLs use them.
_URL_CREDENTIAL_QUERY_KEYS = frozenset(
    _REDACT_KEYS
    | _PROVIDER_IDENTITY_KEYS
    | {"user", "pass", "pwd", "token", "apikey", "auth", "key", "sig", "signature"}
)

# Finds the ``://`` separator and the URL body after it. Needed because a
# credential-bearing URL is not always the WHOLE value: Dispatcharr echoes
# upstream failures into ``last_message``, and an upstream error body can quote
# the request URL it failed on.
#
# The SCHEME is deliberately NOT matched here — :func:`_find_urls_in_text` walks
# backwards for it instead. Matching it forward, as this did until CodeQL alert
# #1879 (``py/polynomial-redos``, HIGH)::
#
#     re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s\"'<>]+")
#
# is quadratic: the scheme repetition is unbounded over a class that excludes
# ``:``, so a long run of scheme-legal characters NOT followed by ``://`` is
# re-scanned from every position inside it. Measured on the pattern above, 4x
# the input cost 16x the time — 128k characters took 10.2 s. That input is
# operator-controlled and unbounded: this scrub visits every string cell of
# every table :data:`_STANDARD_ARTIFACT_TABLES` permits, and
# ``ffmpeg_profiles.config`` and ``dummy_epg_profiles.description_template`` are
# ``Column(Text)`` with no ``max_length`` on their request models.
#
# This pattern has a literal prefix and one repetition over a negated class, so
# it carries no such ambiguity. Pinned by
# ``tests/routers/test_gi4zn_standard_artifact_full_redaction.py::
# test_the_url_scan_cost_grows_linearly_with_the_input_length``.
_URL_TAIL_IN_TEXT_RE = re.compile(r"://[^\s\"'<>]+")

# RFC 3986 ``scheme = ALPHA *( ALPHA / DIGIT / "+" / "-" / "." )``.
_SCHEME_CHARS = frozenset(string.ascii_letters + string.digits + "+-.")


def _find_urls_in_text(value: str) -> list[str]:
    """Return every URL-shaped substring of ``value``, in the order they appear.

    Equivalent to the forward-matching regex this replaced — same substrings, in
    the same order — but linear rather than quadratic in ``len(value)``. Each
    ``://`` is located by a literal scan, the scheme is recovered by walking
    BACK over the scheme-legal run in front of it, and the URL body is taken
    only once a scheme is actually there. Equivalence was checked by
    differential fuzzing against the old pattern over 1,030,000 random strings
    drawn from a URL-flavoured alphabet, and is pinned shape-by-shape by
    ``tests/routers/test_gi4zn_standard_artifact_full_redaction.py::
    test_every_url_shape_the_scrub_caught_before_is_still_caught``.

    Three details carry that equivalence, and getting any of them wrong is a
    silent credential leak or a return to quadratic cost rather than a visible
    failure:

    * **Skip forward to the first ASCII LETTER of the run.** The old pattern's
      leftmost match began there, not at the run's first character, because RFC
      3986 — and :func:`urlsplit`, which decides the outcome downstream —
      require a scheme to start on a letter. ``1https://<username>:<password>@host``
      yielded ``https://<username>:<password>@host`` and was scrubbed. Anchoring
      at the run's first
      CHARACTER instead hands :func:`urlsplit` a scheme starting on a digit,
      gets an empty scheme back, and silently stops scrubbing that URL — a data
      leak traded for a linear scan.
    * **A scheme-less ``://`` hides nothing.**
      ``+://https://<username>:<password>@host`` has a ``://`` with no scheme in
      front of it, and the real URL sits INSIDE
      what a greedy body match from that first separator would swallow. So a
      separator that fails resumes the search one character later rather than
      past the body it would have taken.
    * **The body is only measured after the scheme is found.** Measuring it
      first would re-scan the same span at every failing separator, which is the
      quadratic behaviour this function exists to remove (``"://" * n``).

    Cost is O(len(value)): ``str.find`` advances monotonically, accepted bodies
    are disjoint by construction, and two separators can never share a scheme
    run because neither ``:`` nor ``/`` is scheme-legal.

    Args:
        value: Any string value from a gathered payload.

    Returns:
        The URL-shaped substrings, in the order they appear.
    """
    urls: list[str] = []
    pos = 0
    while True:
        separator = value.find("://", pos)
        if separator < 0:
            return urls
        start = separator
        while start > 0 and value[start - 1] in _SCHEME_CHARS:
            start -= 1
        # Skip the leading digits / ``+`` / ``-`` / ``.`` a scheme may not start
        # on. A run of nothing but those carries no scheme and is not a URL.
        while start < separator and not (
            "a" <= value[start] <= "z" or "A" <= value[start] <= "Z"
        ):
            start += 1
        body = (
            None if start == separator else _URL_TAIL_IN_TEXT_RE.match(value, separator)
        )
        if body is None:
            pos = separator + 1
            continue
        urls.append(value[start:body.end()])
        pos = body.end()


def _collect_credential_values(obj) -> tuple[frozenset[str], frozenset[str]]:
    """Harvest the credential VALUES a RAW payload holds, split by which half.

    The key-name rules (:data:`_REDACT_KEYS` / :data:`_PROVIDER_IDENTITY_KEYS`)
    already know which keys carry a secret. Reading their VALUES off the payload
    BEFORE it is redacted turns a key-name denylist into a set of literal strings
    that can then be recognized wherever else they appear — which is what makes
    the path-segment rule below a literal match rather than a guess (bead
    …-msqf7).

    Harvesting by the SAME key sets the redactor uses is deliberate: a new
    credential-bearing key added to either denylist starts contributing its value
    here automatically, so the two rules cannot drift apart on what counts as a
    credential.

    Args:
        obj: A RAW (un-redacted) gathered payload — dict, list or scalar.

    Returns:
        ``(secrets, identities)``. ``secrets`` are the AUTHENTICATING half
        (:data:`_REDACT_KEYS` — password, token, …); ``identities`` are the
        IDENTIFYING half (:data:`_PROVIDER_IDENTITY_KEYS` — username). Empty,
        blank and sentinel values are excluded: an empty secret would match every
        empty path segment, and the sentinel is already the replacement.
    """
    secrets: set[str] = set()
    identities: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                klower = key.lower() if isinstance(key, str) else None
                if isinstance(value, str) and value and value != REDACTED:
                    if klower in _REDACT_KEYS:
                        secrets.add(value)
                    elif klower in _PROVIDER_IDENTITY_KEYS:
                        identities.add(value)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return frozenset(secrets), frozenset(identities - secrets)


def _url_carries_credentials(
    candidate: str, secrets: frozenset = frozenset()
) -> bool:
    """True when a URL embeds a credential in its userinfo or query string.

    A URL with neither is left ALONE — the restore needs the address to recreate
    the account or source at all, and blanket-redacting URL fields would leave
    every restored provider with nowhere to point.

    Two rules, one by NAME and one by VALUE. The name rule
    (:data:`_URL_CREDENTIAL_QUERY_KEYS`) catches the conventional
    ``?username=…&password=…`` shape. The value rule catches a provider that
    names its parameters something the denylist does not list (``?u=…&p=…``) by
    recognizing the credential VALUE itself — available because the source
    instance knows its own credentials (:func:`_collect_credential_values`).

    PATH-SEGMENT credentials are NOT this function's job — a URL that carries one
    is REWRITTEN rather than replaced whole, so it is handled by
    :func:`_rewrite_known_credential_segments`. Returning True here means "this
    address cannot be carried at all", which for a stream URL would cost the
    replica the stream.

    Args:
        candidate: A single URL-shaped string.
        secrets: Known credential values, from
            :func:`_collect_credential_values`. Empty (the default) leaves only
            the name rule, which is the pre-existing behaviour.

    Returns:
        True when the URL carries a credential and must not enter a standard
        artifact.
    """
    try:
        parts = urlsplit(candidate)
    except ValueError:
        # Unparseable is not a URL; other rules still apply to the raw string.
        return False
    if not parts.scheme or not parts.netloc:
        return False
    if "@" in parts.netloc:
        # RFC 3986 userinfo: everything before the ``@`` in the authority.
        return True
    # Blank values are dropped (parse_qsl default): ``?username=`` with nothing
    # after it carries no credential and must not cost the operator the address.
    return any(
        key.lower() in _URL_CREDENTIAL_QUERY_KEYS or value in secrets
        for key, value in parse_qsl(parts.query)
    )


def _rewrite_known_credential_segments(
    candidate: str, secrets: frozenset, identities: frozenset
) -> Optional[str]:
    """Replace path segments that LITERALLY carry a known credential.

    THE SHAPE THIS EXISTS FOR (bead …-msqf7, measured against a real Xtream Codes
    provider on 2026-08-20). Every one of the 1,409,363 stream URLs in that
    provider's playlist put the account's username and password in path
    segments — ``/live/<user>/<pass>/<id>.ts`` and the ``movie`` / ``series``
    variants — while the SAME provider authenticated its guide endpoint by query
    string. Neither the key-name denylist (a stream's key is ``url``) nor
    :func:`_url_carries_credentials` could see it, so the pair crossed to a sync
    destination intact, on every scheduled cycle, while the run reported that
    credentials had been stripped.

    WHY THIS IS NOT THE GUESSING THE OLD DOCSTRING REFUSED. It is still true that
    no GENERAL rule separates ``/live/u/p/1.ts`` from an ordinary path. This rule
    is not general: it compares each segment against the values the source
    instance ACTUALLY holds, raw and percent-decoded. A path is rewritten because
    it contains this operator's password, not because it looks like it might
    contain someone's.

    THE PASSWORD IS THE GATE, and that is the whole of the false-positive
    defence. A URL is only rewritten once one of its segments carries a known
    SECRET; only then is the IDENTITY half redacted alongside it. Without that
    gate an operator whose XC username happened to be a structural path word
    (``live``, ``movie``, ``news``) would have every URL on the instance mangled,
    including credential-free ones belonging to other providers. With it, a
    username collision can only occur inside a URL already proven to carry the
    password — where the address needs the operator's attention regardless, so
    the extra sentinel costs nothing.

    WHAT THE REPLICA GETS. The scheme, host, port, kind marker and stream id all
    survive; only the credential segments become the sentinel. Blanking the value
    instead would reproduce bead …-v7d37's outcome one layer down — a replica
    holding streams with nowhere to point and no record of where they pointed.
    The destination reports the rewrite as a post-restore action item
    (``RestoreReport.stream_urls_redacted``) rather than absorbing it silently.

    Args:
        candidate: A single URL-shaped string.
        secrets: Known credential values (the authenticating half).
        identities: Known identity values (the identifying half).

    Returns:
        The rewritten URL, or ``None`` when no segment matched and the value must
        be left byte-identical.
    """
    if not secrets:
        return None
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc or "/" not in parts.path:
        return None

    def carries(segment: str, values: frozenset) -> bool:
        # CONTAINMENT, not equality, and the whole segment then goes. A provider
        # that decorates the credential segment (``/<pass>-hd/``) is still
        # handing over the password, so equality would let it through; and a
        # segment that carries the password is not an address component worth
        # preserving byte-for-byte, so there is nothing to gain from splicing
        # only the matched run out of it. Whole-segment replacement is also the
        # only well-defined answer for a percent-encoded match, where the
        # matched span exists in the DECODED string and has no single
        # corresponding span in the encoded one.
        #
        # ``unquote`` never raises. A credential holding URL-reserved characters
        # arrives percent-encoded, and ``p%40ss`` is the same secret as ``p@ss``,
        # so an escape must not be a way through.
        return any(v in segment or v in unquote(segment) for v in values)

    segments = parts.path.split("/")
    if not any(carries(seg, secrets) for seg in segments):
        return None
    rewritten = [
        REDACTED if carries(seg, secrets) or carries(seg, identities) else seg
        for seg in segments
    ]
    if rewritten == segments:
        return None
    return urlunsplit(
        (parts.scheme, parts.netloc, "/".join(rewritten), parts.query, parts.fragment)
    )


def _scrub_credential_urls(
    value: str,
    secrets: frozenset = frozenset(),
    identities: frozenset = frozenset(),
):
    """Remove credentials embedded in URL VALUES, or return ``None`` if clean.

    Three shapes, because the right restore-side behaviour differs:

    * The value IS a credential-bearing URL (an M3U ``server_url``, an EPG
      ``url``). The WHOLE value becomes the sentinel, so the restore recognizes
      it, leaves the field unset rather than writing a half-URL that silently
      fails to authenticate, and names the field in
      ``credential_reentry_details``.
    * The value CONTAINS one (a status message quoting a failed request). Only
      the URL substring is replaced, so the operator keeps the diagnostic.
    * The value carries a KNOWN credential in a PATH SEGMENT (an Xtream Codes
      stream url). Only those segments are replaced
      (:func:`_rewrite_known_credential_segments`) — the address survives,
      because a stream url that cannot be carried at all costs the replica the
      stream (bead …-msqf7).

    THE WHOLE-VALUE RULE WINS. A URL carrying credentials in BOTH its query and
    its path loses the whole value: it is replaced by the sentinel, and the path
    half can never survive on the coat-tails of a partial rewrite. That was the
    exact regression bead …-v7d37 feared from relaxing the whole-value rule, and
    it is pinned by
    ``tests/tasks/test_msqf7_stream_url_credential_leak.py::
    test_a_url_carrying_the_credential_in_both_query_and_path_loses_the_whole_value``.
    The ``url in dirty`` skip below states that precedence at the point it is
    decided; it is not load-bearing on its own, because the whole-value
    substitution runs first and leaves a rewrite nothing to match.

    Args:
        value: Any string value from a gathered payload.
        secrets: Known credential values, from
            :func:`_collect_credential_values`. Empty (the default) disables the
            path-segment rule entirely, so every existing caller is unchanged.
        identities: Known identity values, redacted only inside a URL the
            ``secrets`` gate has already opened.

    Returns:
        The scrubbed string, or ``None`` when the value carries no URL
        credential and must be left byte-identical.
    """
    if "://" not in value:
        return None
    found = _find_urls_in_text(value)
    dirty = [url for url in found if _url_carries_credentials(url, secrets)]
    rewrites: dict[str, str] = {}
    for url in found:
        if url in dirty or url in rewrites:
            continue
        rewritten = _rewrite_known_credential_segments(url, secrets, identities)
        if rewritten is not None:
            rewrites[url] = rewritten
    if not dirty and not rewrites:
        return None
    if not rewrites and len(dirty) == 1 and value.strip() == dirty[0]:
        return REDACTED
    scrubbed = value
    for url in dirty:
        scrubbed = scrubbed.replace(url, REDACTED)
    for url, rewritten in rewrites.items():
        scrubbed = scrubbed.replace(url, rewritten)
    return scrubbed


def _redact_credentials_deep(
    obj,
    preserve_keys: frozenset = frozenset(),
    exempt_identity_keys: frozenset = frozenset(),
    scrub_credential_urls: bool = True,
    known_secrets: frozenset = frozenset(),
    known_identities: frozenset = frozenset(),
):
    """Recursively replace any value whose key (case-insensitive) is in the
    shared :data:`_REDACT_KEYS` denylist with the REDACTED sentinel.

    NON-BYPASSABLE artifact-pipeline stage (0i2vt.7): there is no plaintext
    switch. Walks dicts and lists in place-safe fashion (returns a new
    structure) so credential-class values never enter the archive regardless of
    which category/source produced them. Non-credential values are untouched.

    Three things are redacted, and the second and third exist because the first
    is not sufficient on its own (bead …-gi4zn):

    1. A value under a :data:`_REDACT_KEYS` key — the credential itself.
    2. A value under a :data:`_PROVIDER_IDENTITY_KEYS` key — the IDENTITY half of
       a third-party credential pair. Applied by DEFAULT; ``exempt_identity_keys``
       is how a caller names an exemption (see
       :data:`_IDENTITY_EXEMPT_CATEGORIES`), so a new caller fails closed.
    3. A credential embedded in a URL VALUE, under any key at all
       (:func:`_scrub_credential_urls`). A plain-M3U account's whole provider
       credential lives in ``server_url``'s query string, where rules 1 and 2 —
       which look only at KEY names — cannot see it.

    ``preserve_keys`` is the opt-in ``include_credentials`` re-injection
    allowlist (ADR-012 D12 / u81kh): a key in this set is NOT redacted — its real
    value is carried so a cross-instance migration does not have to re-enter it.
    This does NOT bypass redaction: redaction still runs over every key; only the
    explicitly-approved migration creds are preserved, and the artifact is then
    whole-passphrase-encrypted (the only context in which ``preserve_keys`` is
    ever non-empty — see :func:`build_backup_artifact`). Keys NOT in this set
    (and never approved — e.g. ``password_hash``, never in :data:`_REDACT_KEYS`)
    stay redacted regardless.

    ``scrub_credential_urls`` is the URL-rule counterpart of ``preserve_keys``
    and is set False on that SAME cred-carrying path: an encrypted migration
    artifact is the one artifact allowed to carry credentials, and a provider URL
    with its query-string credential intact is exactly what makes it restorable
    without re-entry. It is a separate parameter rather than derived from
    ``preserve_keys`` because rule 3 does not key off names at all, so there is
    no key set that could express it.

    ``known_secrets`` / ``known_identities`` extend rule 3 to the one credential
    carrier neither a key name nor a URL's structure can reveal: a PATH SEGMENT
    (bead …-msqf7). They are the literal values the caller harvested off the RAW
    payload with :func:`_collect_credential_values`, so the match is against what
    this instance actually holds rather than against a pattern. Both default to
    empty, which makes the rule a no-op — every caller that does not opt in keeps
    byte-identical behaviour. See
    :func:`_rewrite_known_credential_segments` for why the secret half gates the
    identity half.
    """
    identity_keys = _PROVIDER_IDENTITY_KEYS - exempt_identity_keys
    denied = _REDACT_KEYS | identity_keys
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            klower = key.lower() if isinstance(key, str) else key
            if isinstance(key, str) and klower in denied and klower not in preserve_keys:
                # Only redact truthy values — preserve None/"" so restore-side
                # preserve-on-omit semantics still distinguish "unset".
                out[key] = REDACTED if value not in (None, "") else value
            elif isinstance(key, str) and klower in preserve_keys:
                # Approved migration cred — carried as-is (no recursion needed;
                # a credential value is a scalar, not a nested structure).
                out[key] = value
            else:
                out[key] = _redact_credentials_deep(
                    value,
                    preserve_keys,
                    exempt_identity_keys,
                    scrub_credential_urls,
                    known_secrets,
                    known_identities,
                )
        return out
    if isinstance(obj, list):
        return [
            _redact_credentials_deep(
                item,
                preserve_keys,
                exempt_identity_keys,
                scrub_credential_urls,
                known_secrets,
                known_identities,
            )
            for item in obj
        ]
    if scrub_credential_urls and isinstance(obj, str):
        scrubbed = _scrub_credential_urls(obj, known_secrets, known_identities)
        if scrubbed is not None:
            return scrubbed
    return obj


def _get_backup_filename() -> str:
    """Generate a timestamped backup filename."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    return f"ecm-backup-{now}.zip"


def _open_private_binary(path: Path):
    """Open a local backup artifact for write and enforce owner-only access.

    ``O_NOFOLLOW`` is part of the guarantee, not decoration: without it a symlink
    planted at the artifact path is FOLLOWED — measured — so the open truncates,
    overwrites and ``fchmod(0600)``s the link's target instead of the artifact.
    It requires prior local write access to the backups directory, so this is
    hardening rather than a live path, but the flag is free and the failure it
    prevents is silent.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        return os.fdopen(fd, "wb")
    except BaseException:
        os.close(fd)
        raise


def _write_private_bytes(path: Path, data: bytes) -> None:
    """Write a complete local backup artifact with mode ``0600``."""
    with _open_private_binary(path) as fh:
        fh.write(data)


def _write_private_text(path: Path, data: str) -> None:
    """Write a UTF-8 backup sidecar with mode ``0600``."""
    with _open_private_binary(path) as fh:
        fh.write(data.encode("utf-8"))


def _build_manifest(files: list[str]) -> dict:
    """Build backup manifest with version and file list."""
    return {
        "version": APP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }


def _scrub_journal_db_to_temp(src: Path, include_credentials: bool = False) -> Path:
    """Copy journal.db to a temp file and scrub it. Caller must unlink the path.

    Three things happen, and ALL of them happen or the copy is destroyed and the
    backup fails (:class:`BackupScrubError` — no fail-open path survives):

    1. Every table that is not a key of :data:`_STANDARD_ARTIFACT_TABLES` is
       DROPPED. That allowlist, not a list of tables to remove, is what decides
       what ships: ECM's own account tables, third-party and viewer identity,
       credential stores, history and telemetry are all absent by construction
       rather than by having been enumerated. See that dict's comment.
    2. Every string cell of every PERMITTED table goes through the JSON
       deep-redaction and URL-credential rules
       (:func:`_scrub_permitted_table_cells`), because the property applies to
       the tables that are kept as much as to the ones that are dropped.
    3. The credential- and identity-class keys inside ``alert_methods.config``
       JSON (bd-l0nhi: PR #163 began storing the SMTP password there, so the live
       DB cannot be zipped raw). A row whose config does not parse as a JSON
       OBJECT has its whole ``config`` value replaced with the sentinel — see
       :func:`_scrub_journal_db_in_place`.

    ``include_credentials`` (ADR-012 D12 / u81kh) is the approved cred-carrying
    migration path and returns the byte-for-byte copy: that artifact is
    whole-passphrase-encrypted and its entire value is that a migration restores
    every credential — including the operator's accounts — without re-entry. It
    is only ever True from :func:`build_backup_artifact` when a passphrase is
    set, so the cleartext-on-disk default copy is always scrubbed. NOTE: any
    CloudStorageTarget / SyncTarget credential columns in journal.db remain
    Fernet-ciphertext at rest (ADR-012 D3) regardless of this flag; they are
    usable on the target only with the same export key, else treated as absent on
    restore (checklist 19).
    """
    fd, tmp_name = tempfile.mkstemp(prefix="ecm-backup-journal-", suffix=".db")
    os.close(fd)
    tmp_path = Path(tmp_name)
    shutil.copyfile(src, tmp_path)
    if include_credentials:
        # Approved cred-carrying migration path: do NOT scrub.
        return tmp_path

    try:
        _scrub_journal_db_in_place(tmp_path)
    except BaseException:
        # This temp is an UNSCRUBBED copy of the live database sitting in the
        # system temp dir. A failed scrub must not leave it there — the caller
        # only unlinks paths this function RETURNED, and it is about to receive
        # an exception instead. BaseException, not Exception: a cancellation or
        # a KeyboardInterrupt mid-scrub leaves exactly the same file behind.
        try:
            tmp_path.unlink()
        except OSError as e:
            logger.warning(
                "[BACKUP] Could not remove the unscrubbed journal.db temp copy "
                "%s after a failed scrub: %s",
                tmp_path, e,
            )
        raise
    return tmp_path


def _scrub_cell_value(value: str) -> Optional[str]:
    """Scrub one TEXT cell of a PERMITTED table, or return ``None`` if it is clean.

    Invariant 2 of this bead applies to the tables the allowlist KEEPS, not only
    to the ones it drops, so the kept tables get the same two VALUE-level rules
    the YAML categories get — and they are applied to every string cell rather
    than to a list of columns, because a column list is the denylist shape this
    round exists to remove.

    * A cell that parses as a JSON object or array goes through
      :func:`_redact_credentials_deep`, so a credential- or identity-named key
      nested anywhere inside it is redacted exactly as it would be in a category
      YAML.
    * Any other string goes through :func:`_scrub_credential_urls`, which catches
      a credential embedded in a URL value under any key at all.

    A JSON cell is rewritten only when the redacted STRUCTURE differs from the
    parsed one — never merely because ``json.dumps`` would re-space it — so a
    clean cell stays byte-identical and the artifact stays diffable.

    Args:
        value: The raw string held in the cell.

    Returns:
        The replacement string, or ``None`` when the cell carries nothing this
        rule redacts and must be left exactly as it is.
    """
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed = None
    if isinstance(parsed, (dict, list)):
        redacted = _redact_credentials_deep(parsed)
        return json.dumps(redacted) if redacted != parsed else None
    return _scrub_credential_urls(value)


def _scrub_permitted_table_cells(cur, table: str) -> int:
    """Apply :func:`_scrub_cell_value` to every string cell of one kept table.

    Every column is visited and the decision is made on the PYTHON type of each
    value rather than on the column's declared type. SQLite is dynamically typed
    — a TEXT value can sit in a column declared INTEGER — so ``isinstance(v,
    str)`` is the complete test and a declared-type filter would not be.

    Addressed by ``rowid``, which every table here has (none is WITHOUT ROWID),
    so this does not depend on any table having an integer primary key.

    Raises:
        BackupScrubError: if the table cannot be read or rewritten. Fails closed
            like every other step — a cell that could not be scrubbed must not
            ship.

    Returns:
        The number of cells rewritten.
    """
    try:
        columns = [row[1] for row in cur.execute('PRAGMA table_info("%s")' % table)]
    except sqlite3.DatabaseError as e:
        raise BackupScrubError(
            "could not inspect the permitted table %s: %s" % (table, e)
        ) from e
    if not columns:
        return 0
    selected = ", ".join('"%s"' % c for c in columns)
    try:
        rows = cur.execute(
            'SELECT rowid, %s FROM "%s"' % (selected, table)  # noqa: S608 — names read from PRAGMA table_info
        ).fetchall()
    except sqlite3.DatabaseError as e:
        raise BackupScrubError(
            "could not read the permitted table %s: %s" % (table, e)
        ) from e

    changed_cells = 0
    for row in rows:
        rowid = row[0]
        updates: dict[str, str] = {}
        for column, value in zip(columns, row[1:]):
            if not isinstance(value, str):
                continue
            scrubbed = _scrub_cell_value(value)
            if scrubbed is not None:
                updates[column] = scrubbed
        if not updates:
            continue
        statement = 'UPDATE "%s" SET %s WHERE rowid=?' % (  # noqa: S608 — same
            table,
            ", ".join('"%s"=?' % c for c in updates),
        )
        try:
            cur.execute(statement, (*updates.values(), rowid))
        except sqlite3.DatabaseError as e:
            raise BackupScrubError(
                "could not rewrite a scrubbed cell in %s rowid=%s: %s"
                % (table, rowid, e)
            ) from e
        changed_cells += len(updates)
    return changed_cells


def _scrub_journal_db_in_place(tmp_path: Path) -> None:
    """Scrub a temp COPY of journal.db in place, or raise :class:`BackupScrubError`.

    FAILS CLOSED at every step (bead …-gi4zn, review finding A-3). Each of the
    three ``return tmp_path`` fallbacks this replaced shipped the raw database:
    an unopenable file, an unreadable table, and an unparseable
    ``alert_methods.config`` row. The reviewer proved the third by seeding a
    truncated blob and reading its SMTP secret out of a built artifact while
    valid rows in the SAME database were correctly redacted.

    The two decisions inside that fail-closed rule:

    * **A row whose ``config`` is not a JSON object loses the whole blob** to the
      sentinel, rather than being dropped. Dropping the row would delete the
      operator's alert method outright — its name, type and enabled flag are not
      credentials and the restore wants them — and it would do so invisibly,
      since nothing downstream can report a row that is not there. Replacing the
      blob keeps the row present and visibly needing re-entry, and it cannot ship
      a byte that was never parsed. The restore side treats a whole-blob
      sentinel the same way it treats a per-key one (see
      :func:`_merge_alert_method_creds_after_restore`), so a restore into an
      instance that still holds the real config repairs it.
    * **A missing table is not a failure.** A freshly bootstrapped database has
      no ``alert_methods`` and may predate the auth tables; nothing to scrub is
      not the same as a scrub that could not run. Failing to LIST the tables IS
      a failure, because then we do not know what is in there.

    THE TABLE PASS IS AN ALLOWLIST (bead …-gi4zn round 3). Every table that is
    not a key of :data:`_STANDARD_ARTIFACT_TABLES` is DROPPED, so a table added
    to the schema later carries nothing until someone permits it. Read that
    dict's comment for why the direction is inverted and for the per-table
    reasons. Three consequences worth stating here:

    * **DROP, not DELETE.** The property is "the artifact contains exactly the
      permitted tables", which is mechanically checkable; "the artifact contains
      these tables and they are empty" is not the same statement and does not
      close the class. ``init_db()`` recreates every model-declared table empty
      via ``Base.metadata.create_all`` before anything queries it, so a dropped
      table heals on restore — see the RESTORE BEHAVIOUR notes in
      ``tests/routers/test_gi4zn_standard_artifact_full_redaction.py``.
    * **``sqlite_%`` internal tables are neither dropped nor required to be
      permitted.** ``sqlite_sequence`` is SQLite's own AUTOINCREMENT bookkeeping,
      cannot be dropped, and holds table names and counters rather than data.
    * **VIEWS ARE LEFT ALONE, deliberately.** ``channel_watch_stats_v`` is a
      saved query over ``session_telemetry``, which this allowlist drops. A view
      whose backing table is missing is resolved LAZILY by SQLite, and nothing
      queries it between the restore and ``init_db()``'s ``create_all``, which
      puts the table back. Dropping the view instead would be permanent: it is
      created by an Alembic revision, and a restored database stamped at head
      re-runs no revisions, so nothing would ever recreate it.

    Kept tables are not merely trusted. Every string cell of every permitted
    table goes through :func:`_scrub_permitted_table_cells`, which applies the
    same JSON deep-redaction and URL-credential rules the YAML categories get.
    That is what makes the allowlist safe for tables like
    ``dummy_epg_profiles``, whose URL templates are operator free text.

    VACUUM at the end is load-bearing, not hygiene: SQLite's ``DELETE`` unlinks
    cells into the freelist and leaves their bytes in the page file, so a purged
    password hash is still recoverable with a substring scan of the shipped
    member. ``PRAGMA secure_delete`` zeroes freed content as it goes and the
    VACUUM rebuilds the file from live rows only. Pinned by the whole-archive
    decompressed byte scan in
    ``tests/routers/test_gi4zn_standard_artifact_full_redaction.py``, which reads
    the archived bytes rather than the query results and so fails if either is
    dropped.
    """
    try:
        conn = sqlite3.connect(str(tmp_path))
    except sqlite3.Error as e:
        raise BackupScrubError(
            "could not open the journal.db copy to scrub it: %s" % e
        ) from e

    try:
        cur = conn.cursor()
        try:
            # secure_delete BEFORE any DELETE so freed pages are zeroed rather
            # than merely unlinked.
            cur.execute("PRAGMA secure_delete = ON")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            present = {row[0] for row in cur.fetchall()}
        except sqlite3.DatabaseError as e:
            raise BackupScrubError(
                "could not list the tables in the journal.db copy: %s" % e
            ) from e

        # Everything not explicitly permitted leaves the copy entirely. Sorted so
        # the security log line below is stable and diffable between runs.
        dropped: dict[str, int] = {}
        for table in sorted(present):
            if table in _STANDARD_ARTIFACT_TABLES or table.startswith("sqlite_"):
                continue
            try:
                # COUNT BEFORE DROP. This is the one record that says the
                # operator's accounts and telemetry left the artifact, and a
                # security log line that under-reports is worse than one that is
                # absent because it reads as proof nothing was there.
                before = cur.execute(
                    'SELECT COUNT(*) FROM "%s"' % table  # noqa: S608 — name read from sqlite_master
                ).fetchone()[0]
                cur.execute('DROP TABLE "%s"' % table)  # noqa: S608 — same
            except sqlite3.DatabaseError as e:
                raise BackupScrubError(
                    "could not drop the non-permitted table %s from the "
                    "journal.db copy: %s" % (table, e)
                ) from e
            dropped[table] = before

        scrubbed_cells = 0
        for table in sorted(present & set(_STANDARD_ARTIFACT_TABLES)):
            scrubbed_cells += _scrub_permitted_table_cells(cur, table)

        scrubbed_rows = 0
        if "alert_methods" in present:
            try:
                cur.execute("SELECT id, config FROM alert_methods")
                rows = cur.fetchall()
            except sqlite3.DatabaseError as e:
                raise BackupScrubError(
                    "could not read alert_methods from the journal.db copy: %s" % e
                ) from e

            for row_id, raw_config in rows:
                if not raw_config:
                    continue
                try:
                    cfg = json.loads(raw_config)
                except (json.JSONDecodeError, TypeError, ValueError):
                    cfg = None
                if not isinstance(cfg, dict):
                    # FAIL CLOSED. Nothing here was parsed, so nothing here can
                    # be shown to be free of credentials.
                    new_config = REDACTED
                    logger.warning(
                        "[BACKUP] alert_methods row id=%s has a config that is "
                        "not a JSON object; replacing the whole blob with the "
                        "redaction sentinel rather than shipping it unread",
                        row_id,
                    )
                else:
                    changed = False
                    for key in _ALERT_METHOD_PROTECTED_KEYS:
                        if key in cfg and cfg[key]:
                            cfg[key] = REDACTED
                            changed = True
                    new_config = json.dumps(cfg) if changed else None
                if new_config is None:
                    continue
                try:
                    cur.execute(
                        "UPDATE alert_methods SET config=? WHERE id=?",
                        (new_config, row_id),
                    )
                except sqlite3.DatabaseError as e:
                    raise BackupScrubError(
                        "could not rewrite alert_methods row id=%s: %s" % (row_id, e)
                    ) from e
                scrubbed_rows += 1

        try:
            conn.commit()
            # VACUUM cannot run inside a transaction; the commit above closes the
            # implicit one sqlite3 opened for the DML.
            cur.execute("VACUUM")
        except sqlite3.DatabaseError as e:
            raise BackupScrubError(
                "could not commit and compact the scrubbed journal.db copy: %s" % e
            ) from e

        # Only when rows actually left. A table that was already empty says
        # nothing, and a WARNING that fires on every backup regardless is a
        # WARNING nobody reads by the time it is true.
        non_empty = {t: n for t, n in dropped.items() if n}
        if non_empty:
            logger.warning(
                "[BACKUP] Dropped %d non-permitted table(s) carrying %d row(s) "
                "from the standard artifact's journal.db (%s). A standard "
                "artifact carries only the configuration tables in "
                "_STANDARD_ARTIFACT_TABLES; account state, telemetry and history "
                "do not travel in it. A restore onto an instance with no accounts "
                "leaves first-run setup required; the operator re-creates their "
                "ECM login. Use an encrypted backup with credentials included to "
                "migrate accounts.",
                len(non_empty),
                sum(non_empty.values()),
                ", ".join("%s=%d rows" % (t, n) for t, n in sorted(non_empty.items())),
            )
        logger.info(
            "[BACKUP] Standard artifact journal.db: kept %d permitted table(s), "
            "dropped %d, scrubbed %d cell(s) and alert_methods.config in %d row(s)",
            len(present & set(_STANDARD_ARTIFACT_TABLES)),
            len(dropped),
            scrubbed_cells,
            scrubbed_rows,
        )
    finally:
        conn.close()


def _create_backup_zip() -> io.BytesIO:
    """Create a zip file containing all ECM config data."""
    # Flush SQLite WAL so journal.db is self-contained before we zip it.
    # WAL mode is enabled by the engine-connect PRAGMA listener in
    # database.py, so the checkpoint is meaningful: without it, recent
    # writes would still live in journal.db-wal and be lost from the backup.
    try:
        engine = get_engine()
        with engine.connect() as conn:
            # PRAGMA wal_checkpoint(TRUNCATE) returns ``(busy, log,
            # checkpointed)``. ``busy=1`` means SQLite could not acquire
            # the exclusive WAL lock and the WAL was NOT fully truncated,
            # so the zipped journal.db may not contain the most recent
            # writes (they still live in the un-truncated WAL on disk and
            # are not part of the backup). Surface that as WARN — matches
            # the database.py startup checkpoint pattern (bd-ej995 polish).
            row = conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
            conn.commit()
        busy = row[0] if row else 0
        if busy:
            logger.warning("[BACKUP] WAL checkpoint completed (incomplete -- WAL busy)")
        else:
            logger.info("[BACKUP] WAL checkpoint completed")
    except Exception as e:
        logger.warning("[BACKUP] WAL checkpoint failed (non-fatal): %s", e)

    buf = io.BytesIO()
    files_added = []
    scrubbed_db_path: Optional[Path] = None

    try:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add settings.json — written from the redacted dict so every field
            # in _SETTINGS_CREDENTIAL_FIELDS (the credential-class fields plus
            # the GET /api/settings read-redaction partition derived from
            # config.ADMIN_ONLY_READ_REDACTED_FIELDS) never hits the archive raw.
            if CONFIG_FILE.exists():
                redacted = _gather_settings()
                zf.writestr("settings.json", json.dumps(redacted, indent=2))
                files_added.append("settings.json")
                logger.info("[BACKUP] Added settings.json (redacted)")

            # Add journal.db — copied to a temp file and scrubbed of
            # alert_methods.config credential-class keys before zipping.
            if JOURNAL_DB_FILE.exists():
                scrubbed_db_path = _scrub_journal_db_to_temp(JOURNAL_DB_FILE)
                zf.write(scrubbed_db_path, "journal.db")
                files_added.append("journal.db")
                logger.info(
                    "[BACKUP] Added journal.db (%d bytes, scrubbed)",
                    scrubbed_db_path.stat().st_size,
                )

            # Add directories
            for dir_rel in BACKUP_DIRS:
                dir_path = CONFIG_DIR / dir_rel
                if dir_path.exists() and dir_path.is_dir():
                    for file_path in dir_path.rglob("*"):
                        if file_path.is_file():
                            arcname = str(file_path.relative_to(CONFIG_DIR))
                            zf.write(file_path, arcname)
                            files_added.append(arcname)
                    if any(1 for _ in dir_path.rglob("*") if _.is_file()):
                        logger.info("[BACKUP] Added directory %s", dir_rel)

            # Add manifest
            manifest = _build_manifest(files_added)
            zf.writestr("ecm_backup.json", json.dumps(manifest, indent=2))
    finally:
        if scrubbed_db_path is not None:
            try:
                scrubbed_db_path.unlink()
            except OSError as e:
                logger.warning("[BACKUP] Failed to unlink scrubbed journal temp %s: %s", scrubbed_db_path, e)

    buf.seek(0)
    logger.info("[BACKUP] Backup created with %d files", len(files_added))
    return buf


# ---------------------------------------------------------------------------
# DBAS backup artifact builder (0i2vt.7)
#
# The NEW v0.18.0 DBAS artifact format. Distinct from the legacy
# ``_create_backup_zip`` above (which the shipped GET /create + POST /save +
# restore paths still use). The new artifact is a ZIP containing:
#
#   manifest.json                 — schema_version (int) + app_version (str) +
#                                   created_at + per-file SHA-256 + redacted flag.
#                                   This is the CLEARTEXT HEADER: schema_version
#                                   is readable WITHOUT decrypting (encryption seam
#                                   for u81kh — a future wrapper encrypts the whole
#                                   ZIP file, but the schema_version must remain
#                                   discoverable from the manifest before decrypt).
#   categories/<name>.yaml        — per-category redacted config (reuses
#                                   build_yaml_export / _gather_* — single source).
#   journal.db                    — scrubbed via _scrub_journal_db_to_temp.
#   binary/metadata.json          — logo inventory.
#   binary/url-mappings.json      — logo-file -> source-URL map.
#   binary/logos/<file>           — per-image logo files (streamed, not buffered).
#
# A SHA-256 checksum SIDECAR file is written ALONGSIDE the ZIP (ADR-012 D1):
# ``<artifact>.sha256``, computed by STREAMING the finished ZIP file (never by
# hashing an in-RAM blob). Redaction is a NON-BYPASSABLE pipeline stage that
# runs BEFORE any bytes enter the archive: there is no "ship plaintext" switch.
# ---------------------------------------------------------------------------

# Path layout inside the new artifact ZIP.
ARTIFACT_MANIFEST_NAME = "manifest.json"
ARTIFACT_CATEGORY_DIR = "categories"
ARTIFACT_BINARY_DIR = "binary"
ARTIFACT_LOGO_DIR = "binary/logos"
ARTIFACT_BINARY_METADATA = "binary/metadata.json"
ARTIFACT_BINARY_URL_MAPPINGS = "binary/url-mappings.json"

# Streaming chunk size for SHA-256 computation over the finished artifact.
_SHA256_CHUNK = 1024 * 1024  # 1 MiB

# Restore-upload streaming chunk size — the uploaded artifact is streamed to a
# temp file ONE chunk at a time (never read whole-in-RAM, mirrors the .7/.15
# streaming discipline; ADR-008 D8). 1 MiB chunks keep the per-read buffer small.
_RESTORE_UPLOAD_CHUNK = 1024 * 1024  # 1 MiB

# Hard cap on an uploaded restore artifact (the binary logo subtree can be large,
# but a multi-GB upload is an abuse signal / DoS surface). The stream loop aborts
# and cleans up the moment cumulative bytes exceed this — it never buffers the
# whole upload to discover the size. 2 GiB is generous headroom over a realistic
# redacted artifact while still bounding the temp-file write.
_RESTORE_MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB

# --- Decompression-bomb (D2) caps. -----------------------------------------
# The 2 GiB upload cap above bounds only the COMPRESSED bytes. A small ZIP with a
# high compression ratio can still expand to gigabytes on zf.read(), OOMing the
# single-process container — reachable even on the admin dry-run path. These caps
# implement the threat-model D2 control (docs/security/threat_model_dbas_import.md
# §3.5 D2 / checklist 5): they are enforced by iterating zf.infolist() BEFORE any
# zf.read(), so the bomb member is never decompressed. Values mirror the
# checklist's ratified defaults (A4): 100x per-entry ratio, 1 GiB cumulative
# uncompressed, 10,000 entries.
_ARTIFACT_MAX_ENTRIES = 10_000
_ARTIFACT_MAX_MANIFEST_BYTES = 1 * 1024 * 1024  # 1 MiB
_MAX_DBAS_MANIFEST_BYTES = _ARTIFACT_MAX_MANIFEST_BYTES
_ARTIFACT_MAX_MEMBER_UNCOMPRESSED = 1 * 1024 * 1024 * 1024  # 1 GiB per member
_ARTIFACT_MAX_TOTAL_UNCOMPRESSED = 1 * 1024 * 1024 * 1024  # 1 GiB cumulative
_ARTIFACT_MAX_ENTRY_RATIO = 100  # max decompressed:compressed per entry
_ARTIFACT_HASH_CHUNK_BYTES = 1024 * 1024
# A small stored entry (e.g. a 12-byte manifest) has a degenerate ratio; only
# entries whose compressed size exceeds this floor are ratio-checked, so a tiny
# stored file is not falsely flagged. The cumulative + per-entry-size caps still
# bound everything below the floor.
_ARTIFACT_RATIO_MIN_COMPRESSED = 1024  # 1 KiB

# JSON control members are parsed in memory and therefore need limits far below
# the generic 1 GiB data-member ceiling. A manifest is metadata, so 1 MiB is
# ample even for thousands of hash entries. settings.json can contain sizable
# operator-authored rule/tag configuration; 8 MiB preserves useful headroom
# while bounding both validation and the unauthenticated first-run restore.
_MAX_LEGACY_MANIFEST_BYTES = 1 * 1024 * 1024
_MAX_LEGACY_SETTINGS_BYTES = 8 * 1024 * 1024

# Legacy ZIPs produced by ECM can hold its SQLite database and uploaded M3U
# files with a 292x--650x DEFLATE ratio. Keep the DBAS 100x policy unchanged,
# but accept only those known legacy data members up to 1000x: the demonstrated
# journal.db bomb is ~1028x (261,180 compressed -> 268,435,456 declared bytes),
# so it remains rejected before a member is read.
_LEGACY_COMPRESSIBLE_MEMBER_NAMES = ("journal.db",)
_LEGACY_COMPRESSIBLE_MEMBER_PREFIXES = ("m3u_uploads/",)
_LEGACY_MAX_ENTRY_RATIO = 1000

# Headroom multiplier for the pre-build free-disk check. The redacted source
# (logos + journal.db) is read once into a compressed ZIP; we conservatively
# require free space >= estimated_source_bytes (the ZIP is typically smaller,
# but DEFLATE on already-compressed PNG/JPG logos barely shrinks them, so we do
# not discount). A clear failure here beats filling /config and corrupting
# journal.db mid-write (D8 / grooming note).
_DISK_HEADROOM_BYTES = 64 * 1024 * 1024  # 64 MiB absolute floor on top of estimate


class BackupArtifact:
    """Result of :func:`build_backup_artifact`.

    Attributes:
        zip_path: Path to the sealed (redacted) ZIP artifact on disk.
        sidecar_path: Path to the ``<zip>.sha256`` checksum sidecar.
        schema_version: The integer schema version stamped in the manifest.
        sha256: Hex SHA-256 of the final artifact bytes (== sidecar contents).
            For an encrypted artifact this is over the ENCRYPTED envelope bytes
            (the bytes actually on disk).
        file_count: Number of member files written into the ZIP.
        encrypted: True when the artifact is whole-passphrase-encrypted
            (ADR-012 D12 / u81kh); the manifest/schema_version then live INSIDE
            the ciphertext and only the envelope ``format_version`` is readable
            pre-decrypt.
        gathered_categories: How many ``RESTORABLE_SECTIONS`` categories this
            artifact gathered — the DENOMINATOR that makes
            ``degraded_categories`` a count of items rather than a boolean
            (bead …-fexq1). ``degraded_categories`` is always a subset of
            these, so ``gathered_categories - len(degraded_categories)`` is
            how many archived cleanly.
            :class:`tasks.dbas_backup.DbasBackupTask` reports the pair as the
            run's ``success_count`` / ``failed_count``; without it, a backup
            that archived fifteen categories and stubbed one reported
            "0 ok, 1 failed" — a real, restorable artifact described as a
            total loss in the Journal and the task-history row.
        degraded_categories: Sorted list of ``RESTORABLE_SECTIONS`` category
            keys whose Dispatcharr gather failed and were written into the
            artifact as a ``{"_warning": ...}`` stub instead of real data
            (enhancedchannelmanager-zt3kf). Empty when every category gathered
            cleanly. The caller (:class:`tasks.dbas_backup.DbasBackupTask`)
            uses this to report a degraded-but-built backup as a WARNING
            rather than a silent clean success. ``"logos"`` also appears here
            when the builder could not archive the image bytes of one or more
            Dispatcharr-hosted logos (bead …-xb58a): the category's YAML is
            intact, but the artifact is missing payload the operator expects it
            to carry, which is the same "gathered less than it should" shape.
        unarchived_logo_bytes: How many Dispatcharr-hosted logos went into the
            artifact WITHOUT their image bytes. Non-zero means a restore will
            report that many logo misses. Reported alongside
            ``degraded_categories`` so the count is visible, not just the fact.
        unresolved_epg_links: How many archived channels carry an ``epg_data_id``
            whose guide row the producer could not resolve to a ``tvg_id`` (bead
            …-dfkbn, PR review W2). Those links cannot be reattached on restore
            and will be named in ``epg_link_miss_details``. Deliberately
            INFORMATIONAL: a dangling FK is common and largely unactionable, so a
            non-zero value does NOT make the run a WARNING and does NOT join
            ``degraded_categories``, unlike a failed category fetch or missing
            logo bytes, which are ECM failing to gather what it could have.
        epg_index_truncated: True when the source guide read came back at the
            :data:`dbas.archive_keys.EPG_INDEX_MAX_ROWS` ceiling, so the tvg_id
            index may be incomplete. Reported DISTINCTLY from the count above
            because it is a different diagnosis with a different remedy: some of
            those links may be perfectly good references the read never saw.
        recordings_excluded_already_started: How many DVR recordings were NOT
            archived because they had already started or finished (bead
            …-ciabe). ADR-013 requires every exclusion to be VISIBLE, so this
            number exists to be read out to the operator together with the
            manual action — the media files sit on the source instance's disk
            and only the operator can copy them across. Like
            ``unresolved_epg_links`` it is INFORMATIONAL: a finished recording is
            a named technical impossibility, not ECM failing to gather what it
            could have, so it never sets ``failed_count`` and never joins
            ``degraded_categories``.
        recordings_excluded_regenerated_by_a_rule: How many upcoming recordings
            were NOT archived because a recurring rule owns them and the
            destination's own hourly maintainer recreates them from the
            ``dvr_rules`` category. Reported DISTINCTLY from the count above
            because it needs no operator action at all — nothing is lost — while
            the other one does.
    """

    __slots__ = (
        "zip_path", "sidecar_path", "schema_version", "sha256", "file_count",
        "encrypted", "gathered_categories", "degraded_categories",
        "unarchived_logo_bytes", "unresolved_epg_links", "epg_index_truncated",
        "recordings_excluded_already_started",
        "recordings_excluded_regenerated_by_a_rule",
    )

    def __init__(self, zip_path, sidecar_path, schema_version, sha256, file_count,
                 encrypted=False, degraded_categories=None,
                 unarchived_logo_bytes=0, unresolved_epg_links=0,
                 epg_index_truncated=False, gathered_categories=0,
                 recordings_excluded_already_started=0,
                 recordings_excluded_regenerated_by_a_rule=0):
        self.zip_path = zip_path
        self.sidecar_path = sidecar_path
        self.schema_version = schema_version
        self.sha256 = sha256
        self.file_count = file_count
        self.encrypted = encrypted
        self.gathered_categories = int(gathered_categories or 0)
        self.degraded_categories = list(degraded_categories or [])
        self.unarchived_logo_bytes = int(unarchived_logo_bytes or 0)
        self.unresolved_epg_links = int(unresolved_epg_links or 0)
        self.epg_index_truncated = bool(epg_index_truncated)
        self.recordings_excluded_already_started = int(
            recordings_excluded_already_started or 0
        )
        self.recordings_excluded_regenerated_by_a_rule = int(
            recordings_excluded_regenerated_by_a_rule or 0
        )


def _recording_exclusion_kwargs() -> dict[str, int]:
    """Read this run's recordings census into :class:`BackupArtifact` kwargs.

    A run that never gathered the category (``None``) reports zeros — the same
    thing an operator sees for a run with nothing to exclude, because there is
    nothing to tell them either way.
    """
    census = _RECORDINGS_EXCLUDED.get() or {}
    return {
        "recordings_excluded_already_started": census.get("already_started", 0),
        "recordings_excluded_regenerated_by_a_rule": census.get(
            "regenerated_by_a_rule", 0
        ),
    }


def _compute_sha256_streaming(path: Path) -> str:
    """Compute the SHA-256 of a file by streaming it in chunks.

    Never reads the whole file into RAM — the artifact can be multi-GB (D8).
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_SHA256_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _estimate_artifact_source_bytes() -> int:
    """Estimate the on-disk byte cost of the artifact before building.

    Sums journal.db plus every file under ``BACKUP_DIRS``. DEFLATE rarely
    shrinks already-compressed logo images, so we treat the raw source size as
    the floor for the free-disk pre-check.
    """
    total = 0
    if JOURNAL_DB_FILE.exists():
        try:
            total += JOURNAL_DB_FILE.stat().st_size
        except OSError:
            pass
    for dir_rel in BACKUP_DIRS:
        dir_path = CONFIG_DIR / dir_rel
        if not (dir_path.exists() and dir_path.is_dir()):
            continue
        for file_path in dir_path.rglob("*"):
            try:
                if file_path.is_file():
                    total += file_path.stat().st_size
            except OSError:
                continue
    return total


def _check_free_disk(target_dir: Path, required_bytes: int) -> None:
    """Raise RuntimeError if ``target_dir``'s partition lacks ``required_bytes``
    of free space (plus a fixed headroom floor).

    A giant artifact can fill the /config partition and break the live
    journal.db; failing loudly BEFORE we start writing is the safe behavior
    (grooming note / D8).
    """
    needed = required_bytes + _DISK_HEADROOM_BYTES
    try:
        usage = shutil.disk_usage(str(target_dir))
    except OSError as e:
        # If we cannot stat the partition, do not block the backup outright —
        # log and proceed; the write itself will fail loudly if truly full.
        logger.warning("[BACKUP] Could not check free disk on %s: %s", target_dir, e)
        return
    if usage.free < needed:
        raise RuntimeError(
            "Insufficient free disk to build backup artifact: need ~%d bytes "
            "(estimate %d + headroom %d), have %d free on %s"
            % (needed, required_bytes, _DISK_HEADROOM_BYTES, usage.free, target_dir)
        )


def _build_artifact_manifest(
    schema_version: int,
    file_hashes: dict[str, str],
    redacted: bool = True,
) -> dict:
    """Build the new-format artifact manifest (cleartext header).

    ``schema_version`` is a dedicated integer, DISTINCT from ``app_version``
    (the human-readable APP_VERSION string). Both are kept: ``app_version`` for
    operator info, ``schema_version`` for the restore compatibility gate.
    ``files`` carries a per-member SHA-256 so an unpacked member can be
    integrity-checked independently of the whole-artifact sidecar.
    """
    return {
        "schema_version": schema_version,
        "app_version": APP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "redacted": redacted,
        "files": [
            {"path": path, "sha256": sha}
            for path, sha in sorted(file_hashes.items())
        ],
    }


def _count_unresolved_epg_links(channels) -> int:
    """Archived channels that HAVE an EPG link but no resolvable natural key.

    Bead ``…-dfkbn``, PR review W2. Read off the artifact's OWN channel records
    rather than reported up from the producer, so the number is a statement about
    what the artifact actually carries: exactly the channels whose links the
    restore will report in ``epg_link_miss_details``. Zero is the normal case.

    A dangling ``epg_data_id`` (the guide row was deleted after the link was
    made) is common and largely unactionable, so this count is INFORMATIONAL: it
    does not make the run a WARNING and does not join ``degraded_categories``.
    The operator can see it; it does not cry wolf.
    """
    if not isinstance(channels, list):
        return 0
    return sum(
        1
        for ch in channels
        if isinstance(ch, dict)
        and as_int(ch.get("epg_data_id")) is not None
        and not ch.get(ARCHIVE_EPG_TVG_ID_KEY)
    )


async def _gather_redacted_categories(
    include_credentials: bool = False,
) -> tuple[dict[str, str], list[str], int]:
    """Produce the per-category redacted YAML payloads for the artifact.

    REUSES build_yaml_export / _gather_settings / _gather_db_tables /
    _gather_dispatcharr_sections — the SAME gather + redaction pipeline the
    shipped YAML export uses. There is no second gather and no divergent
    redaction list: settings credentials are masked by _gather_settings via the
    shared _SETTINGS_CREDENTIAL_FIELDS denylist before any byte is emitted.

    Returns a ``(categories, degraded, unresolved_epg_links)`` triple:

    * ``categories`` — a mapping of ``<category-name>.yaml`` -> YAML text. Each
      restorable section is emitted as its own file so a future selective
      restore (Phase 2) can read one category without parsing the whole
      archive.
    * ``degraded`` — sorted list of category keys whose Dispatcharr gather
      produced a ``{"_warning": ...}`` stub instead of real data
      (enhancedchannelmanager-zt3kf). This function calls
      :func:`_gather_dispatcharr_sections` ONE KEY AT A TIME (below), so each
      category's stub-or-not outcome is independent by construction — the
      per-fetch isolation contract documented on
      :func:`_gather_dispatcharr_sections` is what makes that true for the
      MULTI-section ``/export?sections=...`` caller too, not just this
      one-key-per-call pattern.

      Detection has to recognize BOTH stub shapes
      :func:`_gather_dispatcharr_sections` can return for a requested
      dispatcharr-backed key: the per-fetch stub nested under the key itself
      (``{key: {"_warning": ...}}``, one failing endpoint) AND the
      total-client-unavailability stub, which is the WHOLE blob
      (``{"_warning": ...}``, no per-key nesting at all — there was no
      per-key attempt to isolate because the client itself was unusable
      before any fetch). PR #770 review: the first cut here only checked the
      nested shape, so ``get_client()`` returning falsy or raising —
      degrading EVERY requested dispatcharr category at once — silently
      produced an EMPTY ``degraded`` list and a clean-success TaskResult on
      the worst possible input. The un-nested shape is intentionally
      preserved on the wire (it is the long-standing artifact/export shape
      the restore-side decoder already tolerates —
      ``tests/dbas/test_restore_artifact_decode.py`` — so this is a
      DETECTION-side fix, not a producer-side format change).

    * ``unresolved_epg_links``: how many archived channels carry an
      ``epg_data_id`` whose guide row the producer could not resolve to a
      ``tvg_id`` (bead …-dfkbn, PR review W2). Counted HERE because this is the
      one place that holds the redacted channel records the artifact is about to
      be built from, so the number describes the artifact rather than an
      intermediate. Informational only: it never adds a degraded category.
    """
    # include_credentials (D12) preserves the approved migration-cred allowlist
    # (== _REDACT_KEYS plus the provider IDENTITY keys; password_hash is in
    # neither and so is never carried). Redaction STILL runs over every key —
    # only the explicitly approved creds are preserved — so this is
    # re-injection, not a redaction bypass (checklist 28). preserve_keys is
    # empty unless the caller opted in AND set a passphrase (enforced in
    # build_backup_artifact).
    #
    # The identity keys MUST be in the preserve set (bead …-gi4zn): the whole
    # point of the encrypted cred-carrying artifact is that a migration does not
    # have to re-enter the provider credential, and half a credential pair is
    # not a credential. Omitting them here would have widened redaction into the
    # one artifact the constraint says must be left alone.
    preserve_keys = (
        (_REDACT_KEYS | _PROVIDER_IDENTITY_KEYS) if include_credentials else frozenset()
    )
    out: dict[str, str] = {}
    degraded: list[str] = []
    unresolved_epg_links = 0
    for key in RESTORABLE_SECTIONS:
        # build_yaml_export routes settings/db/dispatcharr correctly and applies
        # the settings-field redaction. That is NOT sufficient on its own:
        # Dispatcharr-sourced sections (M3U / EPG accounts) can carry
        # credential-class fields the settings redactor never touches. So every
        # category's gathered payload passes through the shared NON-BYPASSABLE
        # deep redactor before it is serialized into the archive — one denylist,
        # every category, no plaintext path.
        #
        # ``exempt_identity_keys`` is per-CATEGORY because ``username`` is the
        # one key whose meaning depends on whose service it names (see
        # :data:`_IDENTITY_EXEMPT_CATEGORIES`). Absent from the map means "no
        # exemption", so a category added later is redacted by default.
        exempt = (
            _PROVIDER_IDENTITY_KEYS if key in _IDENTITY_EXEMPT_CATEGORIES else frozenset()
        )
        yaml_text = await build_yaml_export(
            {key},
            include_credentials=include_credentials,
            exempt_identity_keys=exempt,
        )
        parsed = yaml.safe_load(yaml_text)
        redacted = _redact_credentials_deep(
            parsed,
            preserve_keys,
            exempt_identity_keys=exempt,
            scrub_credential_urls=not include_credentials,
        )
        dispatcharr_blob = redacted.get("dispatcharr") if isinstance(redacted, dict) else None
        if isinstance(dispatcharr_blob, dict):
            section_value = dispatcharr_blob.get(key)
            if isinstance(section_value, dict) and "_warning" in section_value:
                # Per-fetch stub: THIS key's own upstream call failed while
                # nested under its own key (isolation contract intact).
                degraded.append(key)
            elif (
                key not in dispatcharr_blob
                and "_warning" in dispatcharr_blob
                and RESTORABLE_SECTIONS[key].get("dispatcharr")
            ):
                # Total-client-unavailability stub (PR #770 review): the
                # WHOLE blob is a single un-nested {"_warning": ...} — there
                # was no per-key fetch attempt at all, so THIS
                # dispatcharr-backed key never got real data either. Since
                # this function always requests exactly ONE key per call,
                # this shape unambiguously means "key" is degraded.
                degraded.append(key)
            if key == "channels":
                unresolved_epg_links = _count_unresolved_epg_links(section_value)
        out["%s.yaml" % key] = yaml.dump(
            redacted, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
    return out, sorted(degraded), unresolved_epg_links


def _logo_basename_key(value) -> str | None:
    """Lowercased basename of a logo url/path, the producer↔importer join key.

    Mirrors ``dbas.importers.logos._basename_key`` (the importer's tier-3 file
    match) so the producer-side source-id correlation and the restore-side file
    match agree on what "same file" means.
    """
    if not isinstance(value, str) or not value:
        return None
    last = value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    last = last.strip().lower()
    return last or None


async def _fetch_source_logos(client=None) -> list[dict]:
    """The SOURCE Dispatcharr logo rows (``id`` / ``name`` / ``url``).

    One listing serves both logo concerns of the artifact builder: the id
    correlation carried in ``binary/metadata.json``
    (:func:`_build_source_logo_index`) and the byte fetch for
    Dispatcharr-hosted logos (:func:`_gather_dispatcharr_logo_payloads`).
    Best-effort: an unavailable client or a listing failure degrades to an empty
    list, never a build failure.

    Args:
        client: An already-resolved Dispatcharr client. The builder resolves one
            for the whole logo pass and passes it here so the listing and the
            byte fetch share a lifetime. ``None`` resolves one internally.
    """
    client = client or _safe_get_client()
    if not client:
        return []
    try:
        logos = await client.get_all_logos_paginated()
    except Exception as e:  # noqa: BLE001 - the logo listing is best-effort
        # Type only: an httpx error's text embeds the full request URL, the same
        # hygiene rule the neighbouring logo helpers follow.
        logger.warning(
            "[BACKUP] Could not list source logos: %s", type(e).__name__
        )
        return []
    return [logo for logo in (logos or []) if isinstance(logo, dict)]


def _build_source_logo_index(logos: list[dict]) -> dict[str, dict]:
    """Index SOURCE Dispatcharr logo rows by URL basename.

    PR #743 review item 1 (cm9bi): the restore importer's affected-channel
    drill-down keys on the SOURCE logo id (archive channels reference logos via
    ``logo_id``), but an on-disk logo file carries no id. This index joins each
    archived file to its Dispatcharr logo record by URL basename so the builder
    can preserve the id in ``binary/metadata.json``. A logo the index cannot
    resolve carries no ``id`` (never fabricated). On a basename collision the
    lowest id wins, the same tie-break the importer's file match uses.
    """
    index: dict[str, dict] = {}
    for logo in logos or []:
        if not isinstance(logo, dict):
            continue
        logo_id = logo.get("id")
        if not isinstance(logo_id, int) or isinstance(logo_id, bool):
            continue
        key = _logo_basename_key(logo.get("url")) or _logo_basename_key(logo.get("filename"))
        if key is None:
            continue
        existing = index.get(key)
        if existing is None or logo_id < existing["id"]:
            entry: dict = {"id": logo_id}
            name = logo.get("name")
            if isinstance(name, str) and name.strip():
                entry["name"] = name
            index[key] = entry
    return index


def _gather_logo_binary_subtree(
    source_logo_index: Optional[dict] = None,
) -> tuple[list[tuple[Path, str]], dict, dict]:
    """Enumerate logo files for the binary subtree without reading them.

    Returns ``(entries, metadata, url_mappings)`` where:
      - ``entries`` is a list of ``(source_path, arcname)`` to stream into the
        ZIP one file at a time (D8 streaming-upload model — the builder writes
        each via zf.write(), which streams from disk, never buffering all logos
        in RAM).
      - ``metadata`` is the inventory written to binary/metadata.json. When
        ``source_logo_index`` (see :func:`_build_source_logo_index`) resolves a
        file's basename, the entry also carries the SOURCE Dispatcharr logo
        ``id`` (+ display ``name``) — the correlation the restore decoder
        attaches to each logo record so the importer's affected-channel lookup
        works on genuine artifacts (PR #743 item 1). An uncorrelated file
        carries no ``id`` (never fabricated).
      - ``url_mappings`` maps each archived logo filename to its (best-effort)
        source reference for restore-side re-hosting.
    """
    entries: list[tuple[Path, str]] = []
    files_meta: list[dict] = []
    url_mappings: dict[str, str] = {}
    logo_index = source_logo_index or {}

    logos_dir = CONFIG_DIR / "uploads" / "logos"
    if logos_dir.exists() and logos_dir.is_dir():
        for file_path in sorted(logos_dir.rglob("*")):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(logos_dir).as_posix()
            arcname = "%s/%s" % (ARTIFACT_LOGO_DIR, rel)
            entries.append((file_path, arcname))
            try:
                size = file_path.stat().st_size
            except OSError:
                size = None
            file_meta: dict = {"filename": rel, "size_bytes": size}
            correlated = logo_index.get(_logo_basename_key(rel) or "")
            if correlated is not None:
                file_meta["id"] = correlated["id"]
                if correlated.get("name"):
                    file_meta["name"] = correlated["name"]
            files_meta.append(file_meta)
            # Local logos are referenced by their on-disk relative path; the
            # restore importer (Phase 2, 0i2vt.15) re-hosts them. Remote logo
            # URL reconstruction is a restore-side concern and out of scope for
            # the builder — record the local path so the mapping is complete.
            url_mappings[rel] = "uploads/logos/%s" % rel

    metadata = {
        "logo_count": len(files_meta),
        "logos": files_meta,
    }
    return entries, metadata, url_mappings


# ---------------------------------------------------------------------------
# Dispatcharr-hosted logo bytes (bead enhancedchannelmanager-xb58a)
# ---------------------------------------------------------------------------
#
# Dispatcharr is ECM's source of truth for logos (PO decision, 2026-08-04): a
# logo uploaded through ECM's own Logo Manager is written to DISPATCHARR's
# ``/data/logos/``, and ECM's ``/config/uploads/logos/`` (what
# :func:`_gather_logo_binary_subtree` collects) is empty on a normal install.
# The backup therefore FETCHES those bytes from Dispatcharr at gather time, over
# the same API with the same key it already uses for every other category. Logos
# were the one category where ECM read the metadata and not the payload.
#
# Only Dispatcharr-HOSTED logos are fetched. A logo whose ``url`` is an absolute
# http(s) CDN address restores byte-identically from the archived URL alone
# (verified 10 of 10 in run 2026-08-04-run2), so archiving its bytes would be
# waste, and the restore keeps its URL re-create path for exactly those.
#
# Everything here FAILS SOFT. A logo whose bytes cannot be fetched, spooled, or
# safely named degrades to the pre-fix behaviour: it is a counted miss the
# restore reports honestly, never a failed backup.

# FLAT ceiling on the logo bytes ONE artifact may fetch. This is only the LAST
# of the three limits :func:`_logo_byte_budget` applies; the binding ones are
# usually the artifact's remaining uncompressed headroom and the free disk
# measured at fetch time. It exists to bound a pathological logo set on a large,
# empty disk.
_MAX_FETCHED_LOGO_TOTAL_BYTES = 512 * 1024 * 1024  # 512 MiB

# Cap on how many fetched logo files the binary subtree may carry. The
# restore-side guard refuses an artifact with more than _ARTIFACT_MAX_ENTRIES
# members, and the categories, journal.db, manifest and on-disk logo subtree
# draw on that same budget. Half the entry cap leaves ample room for all of
# them, and a builder that quietly produced an artifact ECM itself would refuse
# would be writing a silently unrestorable backup.
_MAX_FETCHED_LOGO_COUNT = _ARTIFACT_MAX_ENTRIES // 2

# Network bounds for Dispatcharr-hosted logo bytes. A backup may encounter
# thousands of logos, so the per-request timeout alone is not enough: repeated
# slow responses could still occupy an unattended scheduled run for hours.
_LOGO_FETCH_TIMEOUT_SECONDS = 30.0
_LOGO_FETCH_BUDGET_SECONDS = 300.0

# Spool dirs older than this are orphans from a build that was killed before its
# cleanup ran (the normal path removes the dir in build_backup_artifact's
# finally). Nothing else owns them: retention's _BACKUP_ZIP_FILENAME_RE
# allowlist matches only ``ecm-backup-*.zip``, so without this sweep a container
# kill mid-backup would leave hundreds of MB in /config forever. The age floor
# keeps a CONCURRENT build's live spool safe.
_LOGO_SPOOL_PREFIX = "ecm-logo-bytes-"
_LOGO_SPOOL_ORPHAN_AGE_SECONDS = 6 * 60 * 60  # 6 hours


def _safe_get_client():
    """``get_client()`` that returns ``None`` instead of raising.

    ``get_client`` reads settings and can throw. Every logo path in the builder
    is best-effort by contract: a Dispatcharr problem degrades the logo bytes,
    it does not fail the operator's backup. Resolving the client through this
    helper is what makes that contract true rather than merely documented.
    """
    try:
        return get_client()
    except Exception as e:  # noqa: BLE001 - client resolution is best-effort
        # Type only: a settings/transport error's text can carry a URL or token.
        logger.warning(
            "[BACKUP] Dispatcharr client unavailable: %s", type(e).__name__
        )
        return None


def _sweep_orphaned_logo_spools(dest_dir: Path) -> None:
    """Remove logo spool dirs left behind by a build that never finished.

    Best-effort and never fatal: a backup must not fail because a stale temp dir
    could not be removed.
    """
    cutoff = time.time() - _LOGO_SPOOL_ORPHAN_AGE_SECONDS
    try:
        candidates = list(dest_dir.glob(_LOGO_SPOOL_PREFIX + "*"))
    except OSError:
        return
    for path in candidates:
        try:
            if not path.is_dir() or path.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        logger.info("[BACKUP] Swept orphaned logo spool dir %s", path.name)


def _archived_logo_filename(url: str) -> Optional[str]:
    """The binary-subtree filename for a Dispatcharr-hosted logo's ``url``.

    Takes the last path segment of the logo's local path (``/data/logos/x.png``
    becomes ``x.png``) and validates it with the RESTORE-side validator itself,
    so a name this builder archives is a name the importer will accept. Query
    and fragment are stripped first; the segment is deliberately NOT
    percent-decoded, because decoding could reintroduce a path separator into
    something that had already been reduced to a basename.

    Returns the validated basename, or ``None`` when the url yields nothing
    usable (the caller then leaves the logo's bytes unarchived and counts it).
    """
    if not isinstance(url, str):
        return None
    path = url.split("?", 1)[0].split("#", 1)[0]
    last = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return safe_logo_basename(last)


def _unique_logo_filename(basename: str, logo_id: int, taken: set[str]) -> Optional[str]:
    """A binary-subtree filename for this logo that no other member has claimed.

    ``binary/metadata.json`` is keyed by filename, so two logos that resolve to
    the same basename would collide and the restore would see only one of them.
    The tie-break appends the SOURCE logo id.

    Returns ``None`` when the id-suffixed name is ALSO taken (reachable: an
    on-disk logo literally named ``abc-44.png`` alongside a hosted ``abc.png``
    with id 44). The caller then counts a miss rather than silently overwriting
    an archived logo, which is the safe direction: an unarchived logo is
    reported, an overwritten one is not.
    """
    if basename not in taken:
        return basename
    stem, dot, ext = basename.rpartition(".")
    candidate = "%s-%d.%s" % (stem, logo_id, ext) if dot else "%s-%d" % (basename, logo_id)
    return candidate if candidate not in taken else None


def _dispatcharr_hosted_logos(source_logos: list[dict]) -> list[dict]:
    """The SOURCE logos whose image bytes only Dispatcharr can supply.

    A logo qualifies when it carries a usable integer id and its ``url`` is NOT
    an absolute ``http(s)`` address, i.e. it names a path inside Dispatcharr's
    own volume (``/data/logos/x.png``, what ECM's Logo Manager writes) rather
    than a CDN a restore can point at again.
    """
    return [
        logo for logo in source_logos
        if isinstance(logo.get("id"), int) and not isinstance(logo.get("id"), bool)
        and remote_logo_url(logo) is None
    ]


def _drop_superseded_local_logos(
    entries: list[tuple[Path, str]],
    metadata: dict,
    url_mappings: dict[str, str],
    fetched_source_ids: set[int],
) -> int:
    """Remove on-disk logo files that a fetched Dispatcharr payload supersedes.

    ``_build_source_logo_index`` correlates a file in ECM's
    ``/config/uploads/logos/`` to a Dispatcharr logo BY BASENAME and stamps that
    logo's ``id`` onto its metadata entry. When the byte fetch then archives the
    same source id, the artifact would carry TWO entries claiming ONE source id
    and the restore has no way to tell them apart:

    * ``_merge_logo_records`` joins the URL inventory on source id and keeps the
      FIRST record it saw, and
    * the importer's tier-1 match resolves the second record through the remap
      the first one registered, so it is skipped as
      ``ALREADY_EXISTS_IDENTICAL``, a claim of sameness about bytes that are
      not the same.

    On-disk members are written to the ZIP before fetched ones, so the loser is
    always the authoritative copy: the operator's channel silently ends up on a
    stale ECM-local image with no failure, no miss, and nothing in the report.

    Dispatcharr is ECM's source of truth for logos (PO decision, 2026-08-04), so
    the fetched bytes win and the local copy is dropped. This runs only for ids
    a fetch ACTUALLY returned, so a failed fetch still falls back to whatever
    local copy exists.

    Mutates ``entries``, ``metadata["logos"]`` and ``url_mappings`` in place.
    Returns how many local files were dropped.
    """
    if not fetched_source_ids:
        return 0
    superseded = {
        entry["filename"] for entry in metadata["logos"]
        if isinstance(entry.get("id"), int) and entry["id"] in fetched_source_ids
    }
    if not superseded:
        return 0
    dropped_arcnames = {"%s/%s" % (ARTIFACT_LOGO_DIR, name) for name in superseded}
    entries[:] = [e for e in entries if e[1] not in dropped_arcnames]
    metadata["logos"] = [
        entry for entry in metadata["logos"] if entry["filename"] not in superseded
    ]
    for name in superseded:
        url_mappings.pop(name, None)
    logger.info(
        "[BACKUP] Dropped %d ECM-local logo file(s) superseded by the "
        "authoritative Dispatcharr bytes.", len(superseded),
    )
    return len(superseded)


def _committed_artifact_bytes(
    categories: dict[str, str], local_logo_entries: list[tuple[Path, str]]
) -> int:
    """Uncompressed bytes the artifact's NON-fetched members already spend.

    Feeds :func:`_logo_byte_budget`, which subtracts this from the restore-side
    cumulative cap. Counts the per-category YAML, the journal.db, and the
    on-disk logo files. Best-effort on sizes: an unstattable file contributes
    zero, which only ever makes the budget more generous by that file's size,
    and the flat ceiling plus the free-disk limit still bound the total.
    """
    total = sum(len(text.encode("utf-8")) for text in categories.values())
    try:
        if JOURNAL_DB_FILE.exists():
            total += JOURNAL_DB_FILE.stat().st_size
    except OSError:
        pass
    for src_path, _arcname in local_logo_entries:
        try:
            total += src_path.stat().st_size
        except OSError:
            continue
    return total


def _logo_byte_budget(spool_dir: Path, committed_bytes: int) -> int:
    """How many bytes of fetched logo payload this artifact may actually take.

    The lowest of three real limits, not a flat constant. A budget that ignores
    any of them produces a backup that LOOKS clean and is not:

    1. **Remaining artifact headroom.** ``_ARTIFACT_MAX_TOTAL_UNCOMPRESSED`` is
       CUMULATIVE over every member, so the journal.db, the category YAML and
       the on-disk logo subtree already spend part of it. Exceeding what is left
       builds an artifact :func:`guard_artifact_against_zip_bomb` refuses on the
       way back in: a silently unrestorable backup.
    2. **Free disk, measured NOW.** The pre-build ``_check_free_disk`` estimate
       is computed from journal.db + ``BACKUP_DIRS``, which by this bead's whole
       premise hold no logo bytes at all. The fetched payloads land on the same
       partition TWICE (spooled, then compressed into the ZIP), so only half the
       free space minus the standing headroom is spendable.
    3. **The flat ceiling** :data:`_MAX_FETCHED_LOGO_TOTAL_BYTES`, which bounds
       a pathological logo set on a large disk.

    Returns a non-negative byte budget; ``0`` means archive no bytes at all,
    which the caller reports as unarchived logos rather than as a failure.
    """
    headroom = _ARTIFACT_MAX_TOTAL_UNCOMPRESSED - committed_bytes
    try:
        free = shutil.disk_usage(str(spool_dir)).free
    except OSError as e:
        logger.warning(
            "[BACKUP] Could not measure free disk for the logo spool: %s",
            type(e).__name__,
        )
        free = 0
    spendable_disk = (free - _DISK_HEADROOM_BYTES) // 2
    return max(0, min(_MAX_FETCHED_LOGO_TOTAL_BYTES, headroom, spendable_disk))


async def _gather_dispatcharr_logo_payloads(
    source_logos: list[dict],
    *,
    client,
    spool_dir: Path,
    taken_filenames: set[str],
    committed_bytes: int = 0,
) -> tuple[list[tuple[Path, str]], list[dict], dict[str, str], int]:
    """Fetch the bytes of every DISPATCHARR-HOSTED logo into the binary subtree.

    Each fetched payload is spooled to its own file under ``spool_dir`` and
    handed back as a ``(path, arcname)`` entry, so the builder streams it into
    the ZIP exactly the way it streams an on-disk logo. Only one payload is ever
    live in memory at a time, which is the same D8 streaming guarantee the rest
    of the artifact pipeline keeps.

    Args:
        source_logos: The SOURCE Dispatcharr logo rows from
            :func:`_fetch_source_logos`.
        client: The Dispatcharr client, resolved ONCE by the caller. Passed in
            rather than resolved here so this function cannot raise: the caller
            already owns the listing's client lifetime, and ``get_client()``
            reads settings and can throw. ``None`` means every hosted logo is
            reported unarchived.
        spool_dir: A caller-owned temp dir for the fetched payloads. The caller
            removes it after the ZIP is sealed.
        taken_filenames: Binary-subtree filenames already claimed (the on-disk
            logos :func:`_gather_logo_binary_subtree` collected). Mutated: each
            filename this function claims is added.
        committed_bytes: Uncompressed bytes the artifact's other members already
            spend, so the byte budget can be derived from what is LEFT of the
            restore-side cumulative cap (see :func:`_logo_byte_budget`).

    Returns:
        ``(entries, metadata_entries, url_mappings, unarchived_count)``.
        ``entries`` and ``metadata_entries`` extend the on-disk gather's;
        ``unarchived_count`` is how many Dispatcharr-hosted logos could not be
        archived and will therefore restore only as far as the pre-fix behaviour
        allows. The caller MUST surface a non-zero count: a backup missing logo
        bytes is not a clean success.
    """
    entries: list[tuple[Path, str]] = []
    files_meta: list[dict] = []
    url_mappings: dict[str, str] = {}
    misses = 0

    hosted = _dispatcharr_hosted_logos(source_logos)
    if not hosted:
        return entries, files_meta, url_mappings, misses

    if not client:
        logger.warning(
            "[BACKUP] No Dispatcharr client; %d Dispatcharr-hosted logo(s) "
            "were archived without their image bytes.",
            len(hosted),
        )
        return entries, files_meta, url_mappings, len(hosted)

    budget = _logo_byte_budget(spool_dir, committed_bytes)
    if budget <= 0:
        logger.warning(
            "[BACKUP] No headroom for logo image bytes; %d Dispatcharr-hosted "
            "logo(s) were archived without them.", len(hosted),
        )
        return entries, files_meta, url_mappings, len(hosted)

    fetch_deadline: Optional[float] = None
    for index, logo in enumerate(hosted):
        if len(entries) >= _MAX_FETCHED_LOGO_COUNT:
            # Every logo from here on is unarchived, so count them all.
            misses += len(hosted) - index
            logger.warning(
                "[BACKUP] Logo file budget (%d files) reached; the remaining "
                "Dispatcharr-hosted logos were archived without their image "
                "bytes.", _MAX_FETCHED_LOGO_COUNT,
            )
            break
        logo_id = logo["id"]
        basename = _archived_logo_filename(logo.get("url"))
        filename = (
            _unique_logo_filename(basename, logo_id, taken_filenames)
            if basename is not None
            else None
        )
        if filename is None:
            misses += 1
            # Never log the url: it is a path, and paths are a leak class here.
            logger.warning(
                "[BACKUP] Logo id=%s has no usable archive filename; its image "
                "bytes were not archived.", logo_id,
            )
            continue

        now = time.monotonic()
        if fetch_deadline is None:
            fetch_deadline = now + _LOGO_FETCH_BUDGET_SECONDS
        remaining_fetch_seconds = fetch_deadline - now
        if remaining_fetch_seconds <= 0:
            misses += len(hosted) - index
            logger.warning(
                "[BACKUP] Logo fetch budget (%.0fs) spent; the remaining "
                "Dispatcharr-hosted logos were archived without their image bytes.",
                _LOGO_FETCH_BUDGET_SECONDS,
            )
            break

        fetch_timeout = min(
            _LOGO_FETCH_TIMEOUT_SECONDS,
            remaining_fetch_seconds,
        )
        try:
            data = await client.fetch_logo_image(
                logo_id,
                timeout=fetch_timeout,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if fetch_timeout >= remaining_fetch_seconds:
                misses += len(hosted) - index
                logger.warning(
                    "[BACKUP] Logo fetch budget (%.0fs) spent while fetching "
                    "logo id=%s; the remaining Dispatcharr-hosted logos were "
                    "archived without their image bytes.",
                    _LOGO_FETCH_BUDGET_SECONDS,
                    logo_id,
                )
                break
            misses += 1
            logger.warning(
                "[BACKUP] Timed out fetching image bytes for logo id=%s after %.0fs.",
                logo_id,
                _LOGO_FETCH_TIMEOUT_SECONDS,
            )
            continue
        except Exception as e:  # noqa: BLE001 - one logo must never fail a backup
            # Only the exception TYPE: an httpx error's text embeds the full URL.
            logger.warning(
                "[BACKUP] Could not fetch image bytes for logo id=%s: %s",
                logo_id, type(e).__name__,
            )
            data = None
        if not data:
            misses += 1
            continue

        size = len(data)
        if size > MAX_LOGO_BYTES:
            misses += 1
            logger.warning(
                "[BACKUP] Logo id=%s is %d bytes, over the per-logo cap; its "
                "image bytes were not archived.", logo_id, size,
            )
            continue
        if size > budget:
            # This logo and every one after it stays unarchived.
            misses += len(hosted) - index
            logger.warning(
                "[BACKUP] Logo byte budget exhausted after %d logo(s) "
                "(%d bytes remaining); the rest were archived without their "
                "image bytes.", len(entries), budget,
            )
            break

        spool_path = spool_dir / str(logo_id)
        try:
            await asyncio.to_thread(spool_path.write_bytes, data)
        except OSError as e:
            misses += 1
            logger.warning(
                "[BACKUP] Could not spool image bytes for logo id=%s: %s",
                logo_id, type(e).__name__,
            )
            continue
        finally:
            # Release the payload before the next logo is fetched (D8).
            data = None

        budget -= size
        taken_filenames.add(filename)
        entries.append((spool_path, "%s/%s" % (ARTIFACT_LOGO_DIR, filename)))
        meta: dict = {"filename": filename, "size_bytes": size, "id": logo_id}
        name = logo.get("name")
        if isinstance(name, str) and name.strip():
            meta["name"] = name
        files_meta.append(meta)
        # The source reference for this archived file is the logo's own
        # Dispatcharr url, the same role the local gather's relative path plays.
        source_url = logo.get("url")
        if isinstance(source_url, str) and source_url:
            url_mappings[filename] = source_url

    logger.info(
        "[BACKUP] Archived image bytes for %d of %d Dispatcharr-hosted logo(s).",
        len(entries), len(hosted),
    )
    return entries, files_meta, url_mappings, misses


async def build_backup_artifact(
    dest_dir: Optional[Path] = None,
    *,
    passphrase: Optional[str] = None,
    include_credentials: bool = False,
    acknowledge_unrecoverable: bool = False,
) -> BackupArtifact:
    """Build the new-format DBAS backup artifact (0i2vt.7 + u81kh).

    Streams a redacted, sealed ZIP to a temp file under ``dest_dir`` (defaults
    to a temp dir on the CONFIG partition), then writes a SHA-256 sidecar
    computed by streaming the finished file. Returns a :class:`BackupArtifact`.

    Redaction is non-bypassable: there is no plaintext switch. The redacted
    bytes are produced as a clean stream.

    Optional whole-artifact passphrase encryption (ADR-012 D12 / u81kh):

    * ``passphrase`` — when set, the sealed ZIP is encrypted off the event loop
      via :mod:`dbas.artifact_crypto` (scrypt + chunked AEAD) and the artifact
      on disk is the encrypted envelope (its ``format_version`` is readable
      pre-decrypt; the backup ``schema_version`` then lives inside the
      ciphertext). Requires ``acknowledge_unrecoverable=True`` (lost passphrase
      = permanently unrecoverable, checklist 34) and a passphrase of at least
      :data:`dbas.artifact_crypto.MIN_PASSPHRASE_LENGTH` chars (checklist 29).
    * ``include_credentials`` — the explicit "include credentials for migration"
      opt-in (checklist 27). It re-injects the approved migration-cred allowlist
      before encryption; redaction still runs (structural redact-then-encrypt,
      checklist 28). It REQUIRES ``passphrase`` — there is no switch that ships
      unredacted creds without one.

    On ANY failure, partial temp artifacts are cleaned up.
    """
    encrypt = passphrase is not None
    if include_credentials and not encrypt:
        # No unredacted-creds-without-a-passphrase path (checklist 27/28).
        raise ValueError("include_credentials requires a passphrase")
    if encrypt:
        if not acknowledge_unrecoverable:
            raise ValueError(
                "Encrypted backup requires acknowledge_unrecoverable: a lost "
                "passphrase makes the artifact permanently unrecoverable"
            )
        if len(passphrase) < artifact_crypto.MIN_PASSPHRASE_LENGTH:
            raise ValueError(
                "Passphrase must be at least %d characters"
                % artifact_crypto.MIN_PASSPHRASE_LENGTH
            )
    # Flush WAL so journal.db is self-contained (same rationale as the legacy
    # builder — see _create_backup_zip).
    try:
        engine = get_engine()
        with engine.connect() as conn:
            row = conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)")).fetchone()
            conn.commit()
        if row and row[0]:
            logger.warning("[BACKUP] WAL checkpoint completed (incomplete -- WAL busy)")
        else:
            logger.info("[BACKUP] WAL checkpoint completed")
    except Exception as e:
        logger.warning("[BACKUP] WAL checkpoint failed (non-fatal): %s", e)

    # Pre-build free-disk check on the partition we will write to.
    if dest_dir is None:
        dest_dir = CONFIG_DIR
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Reclaim any logo spool a previously killed build left behind BEFORE the
    # free-disk check, so an orphan cannot fail the run it did not belong to.
    _sweep_orphaned_logo_spools(dest_dir)
    _check_free_disk(dest_dir, _estimate_artifact_source_bytes())

    # Re-arm the per-run EPG truncation flag so this build can never inherit a
    # previous one's value (see _EPG_INDEX_TRUNCATED). Same for the recordings
    # exclusion census, which re-arms to None — "this run gathered no recordings
    # category", distinct from "it gathered one and excluded nothing".
    _EPG_INDEX_TRUNCATED.set(False)
    _RECORDINGS_EXCLUDED.set(None)

    # Gather redacted payloads BEFORE opening the archive so a gather failure
    # never leaves a half-written ZIP on disk. include_credentials only ever
    # re-injects the approved migration creds (and only with a passphrase set,
    # validated above); redaction still runs over everything else.
    (
        categories,
        degraded_categories,
        unresolved_epg_links,
    ) = await _gather_redacted_categories(include_credentials=include_credentials)
    # Source-logo id correlation (PR #743 item 1) — best-effort join of each
    # on-disk logo file to its Dispatcharr logo record, carried in metadata.json.
    # ONE client resolution serves the listing and the byte fetch below.
    logo_client = _safe_get_client()
    source_logos = await _fetch_source_logos(logo_client)
    source_logo_index = _build_source_logo_index(source_logos)
    logo_entries, logo_metadata, url_mappings = _gather_logo_binary_subtree(
        source_logo_index=source_logo_index
    )

    # e0r3h — the producer owns the CANONICAL timestamped name
    # ``ecm-backup-<UTC ts>.zip`` (no post-build rename in the task layer). This is
    # the name retention's ``_BACKUP_ZIP_FILENAME_RE`` allowlist + filename
    # timestamp-sort require. ``_get_backup_filename`` is the single source of that
    # shape. On the rare same-second collision (two runs in the same UTC second)
    # we suffix a short uniquifier so we never clobber an existing artifact; the
    # base name still matches the retention regex's ``\d{6}`` second field is the
    # canonical case, and the collision fallback degrades retention discoverability
    # of the SECOND file only (same trade-off the old rename made).
    zip_path = dest_dir / _get_backup_filename()
    if zip_path.exists():
        fd, tmp_zip_name = tempfile.mkstemp(
            prefix="ecm-backup-", suffix=".zip", dir=str(dest_dir)
        )
        os.close(fd)
        zip_path = Path(tmp_zip_name)
    sidecar_path = Path(str(zip_path) + ".sha256")
    scrubbed_db_path: Optional[Path] = None
    logo_spool_dir: Optional[Path] = None
    file_hashes: dict[str, str] = {}

    def _writestr_hashed(zf: zipfile.ZipFile, arcname: str, data: bytes) -> None:
        zf.writestr(arcname, data)
        file_hashes[arcname] = hashlib.sha256(data).hexdigest()

    def _write_hashed(zf: zipfile.ZipFile, src: Path, arcname: str) -> None:
        # Stream the file into the ZIP AND hash it in the same single pass over
        # the bytes (no second read, no whole-file buffer).
        zinfo = zipfile.ZipInfo(arcname)
        zinfo.compress_type = zipfile.ZIP_DEFLATED
        h = hashlib.sha256()
        with open(src, "rb") as fsrc, zf.open(zinfo, "w") as fdst:
            for chunk in iter(lambda: fsrc.read(_SHA256_CHUNK), b""):
                fdst.write(chunk)
                h.update(chunk)
        file_hashes[arcname] = h.hexdigest()

    try:
        # Dispatcharr-hosted logo bytes (bead …-xb58a). Fetched into a spool dir
        # BEFORE the ZIP is opened, so a fetch problem never leaves a
        # half-written artifact, and streamed into the ZIP by the same
        # _write_hashed path an on-disk logo takes. The spool dir is removed in
        # the finally below.
        try:
            logo_spool_dir = Path(
                tempfile.mkdtemp(prefix=_LOGO_SPOOL_PREFIX, dir=str(dest_dir))
            )
        except OSError as e:
            # Best-effort like every other logo path: no spool means no archived
            # bytes, reported as such, NOT a failed backup.
            logo_spool_dir = None
            logger.warning(
                "[BACKUP] Could not create the logo spool dir: %s",
                type(e).__name__,
            )

        # An on-disk logo file whose basename correlates to a Dispatcharr logo is
        # a candidate for supersession, so its filename is NOT reserved: if the
        # fetch succeeds the local copy is dropped and the authoritative bytes
        # take the clean name; if the fetch fails the local copy stays and the
        # name was never contested.
        hosted_ids = {logo["id"] for logo in _dispatcharr_hosted_logos(source_logos)}
        reserved = {
            m["filename"] for m in logo_metadata["logos"]
            if not (isinstance(m.get("id"), int) and m["id"] in hosted_ids)
        }
        if logo_spool_dir is not None:
            (
                fetched_entries,
                fetched_meta,
                fetched_mappings,
                unarchived_logos,
            ) = await _gather_dispatcharr_logo_payloads(
                source_logos,
                client=logo_client,
                spool_dir=logo_spool_dir,
                taken_filenames=reserved,
                committed_bytes=_committed_artifact_bytes(categories, logo_entries),
            )
        else:
            fetched_entries, fetched_meta, fetched_mappings = [], [], {}
            unarchived_logos = len(hosted_ids)

        # Dispatcharr is the source of truth: where both a fetched payload and an
        # ECM-local file claim the SAME source logo id, the local copy goes.
        _drop_superseded_local_logos(
            logo_entries, logo_metadata, url_mappings,
            {m["id"] for m in fetched_meta},
        )
        logo_entries.extend(fetched_entries)
        logo_metadata["logos"].extend(fetched_meta)
        logo_metadata["logo_count"] = len(logo_metadata["logos"])
        url_mappings.update(fetched_mappings)
        if unarchived_logos:
            # zt3kf rule: a backup that gathered less than it should is a
            # WARNING-level run, never a silent clean success. "logos" is a real
            # RESTORABLE_SECTIONS key, so it threads straight through
            # tasks.dbas_backup into details, the task message, and the
            # completion notification.
            if "logos" not in degraded_categories:
                degraded_categories = sorted(degraded_categories + ["logos"])
            logger.warning(
                "[BACKUP] %d Dispatcharr-hosted logo(s) were archived without "
                "their image bytes; a restore will report them as logo misses.",
                unarchived_logos,
            )

        # Open the ZIP on a writable FILE HANDLE (NamedTemporaryFile-class temp
        # path), NOT io.BytesIO — the artifact is streamed to disk (D8).
        with _open_private_binary(zip_path) as zfh:
            with zipfile.ZipFile(zfh, "w", zipfile.ZIP_DEFLATED) as zf:
                # Per-category redacted YAML.
                for name, yaml_text in categories.items():
                    _writestr_hashed(
                        zf,
                        "%s/%s" % (ARTIFACT_CATEGORY_DIR, name),
                        yaml_text.encode("utf-8"),
                    )

                # journal.db — scrubbed copy (alert_methods.config creds redacted,
                # unless the cred-carrying migration opt-in preserves them).
                if JOURNAL_DB_FILE.exists():
                    scrubbed_db_path = _scrub_journal_db_to_temp(
                        JOURNAL_DB_FILE, include_credentials=include_credentials
                    )
                    _write_hashed(zf, scrubbed_db_path, "journal.db")

                # Binary subtree: metadata + url-mappings + per-image logo files.
                _writestr_hashed(
                    zf,
                    ARTIFACT_BINARY_METADATA,
                    json.dumps(logo_metadata, indent=2).encode("utf-8"),
                )
                _writestr_hashed(
                    zf,
                    ARTIFACT_BINARY_URL_MAPPINGS,
                    json.dumps(url_mappings, indent=2).encode("utf-8"),
                )
                for src_path, arcname in logo_entries:
                    _write_hashed(zf, src_path, arcname)

                # Manifest LAST so it can carry every member's hash. For a
                # PLAINTEXT artifact this is the cleartext header (schema_version
                # readable pre-decrypt); for an ENCRYPTED artifact it is sealed
                # inside the ciphertext, and the envelope's format_version is the
                # pre-decrypt version gate instead (checklist 30).
                manifest = _build_artifact_manifest(
                    BACKUP_SCHEMA_VERSION, file_hashes, redacted=not include_credentials
                )
                # The manifest itself is not in file_hashes (it hashes the others).
                zf.writestr(ARTIFACT_MANIFEST_NAME, json.dumps(manifest, indent=2))

        # Optional whole-artifact passphrase encryption (ADR-012 D12 / u81kh).
        # The sealed plaintext ZIP is encrypted OFF the event loop to a sibling
        # temp, then atomically swapped into zip_path so the artifact on disk is
        # the encrypted envelope. The plaintext is destroyed by the replace.
        if encrypt:
            enc_path = Path(str(zip_path) + ".enc")
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(
                    None,
                    artifact_crypto.encrypt_file,
                    zip_path, passphrase, enc_path,
                )
                os.replace(enc_path, zip_path)  # plaintext ZIP -> encrypted bytes
            except Exception:
                # encrypt_file already unlinks its own partial output; clear any
                # straggler so the outer cleanup sees a consistent state.
                try:
                    if enc_path.exists():
                        enc_path.unlink()
                except OSError:
                    pass
                raise

        # SHA-256 of the FINISHED artifact (encrypted bytes if encrypted),
        # computed by streaming the file.
        artifact_sha = _compute_sha256_streaming(zip_path)
        _write_private_text(
            sidecar_path, "%s  %s\n" % (artifact_sha, zip_path.name)
        )

        logger.info(
            "[BACKUP] Built artifact %s (schema_version=%d, %d members, "
            "encrypted=%s, include_credentials=%s, sha256=%s)",
            zip_path.name, BACKUP_SCHEMA_VERSION, len(file_hashes),
            encrypt, include_credentials, artifact_sha,
        )
        return BackupArtifact(
            zip_path=zip_path,
            sidecar_path=sidecar_path,
            schema_version=BACKUP_SCHEMA_VERSION,
            sha256=artifact_sha,
            file_count=len(file_hashes),
            encrypted=encrypt,
            # The denominator behind degraded_categories (bead …-fexq1): one
            # entry per category the gather emitted, which is exactly the set
            # degraded_categories draws from.
            gathered_categories=len(categories),
            degraded_categories=degraded_categories,
            unarchived_logo_bytes=unarchived_logos,
            unresolved_epg_links=unresolved_epg_links,
            epg_index_truncated=_EPG_INDEX_TRUNCATED.get(),
            **_recording_exclusion_kwargs(),
        )
    except Exception:
        # Clean up partial temp artifacts on ANY failure.
        for p in (zip_path, sidecar_path):
            try:
                if p.exists():
                    p.unlink()
            except OSError as e:
                logger.warning("[BACKUP] Failed to clean up partial artifact %s: %s", p, e)
        raise
    finally:
        if scrubbed_db_path is not None:
            try:
                scrubbed_db_path.unlink()
            except OSError as e:
                logger.warning(
                    "[BACKUP] Failed to unlink scrubbed journal temp %s: %s",
                    scrubbed_db_path, e,
                )
        if logo_spool_dir is not None:
            # The fetched bytes live only until they are inside the ZIP.
            shutil.rmtree(logo_spool_dir, ignore_errors=True)


def verify_artifact_sha256(zip_path: Path, sidecar_path: Path) -> bool:
    """Verify a built artifact against its SHA-256 sidecar.

    Streams the artifact (no whole-file buffer) and compares against the hash in
    the sidecar. Returns True on match, False on mismatch or unreadable sidecar.
    """
    try:
        sidecar_text = Path(sidecar_path).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    expected = sidecar_text.split()[0] if sidecar_text else ""
    if not expected:
        return False
    actual = _compute_sha256_streaming(Path(zip_path))
    return actual == expected


# ---------------------------------------------------------------------------
# Restore-ingest schema_version gate (0i2vt.17, ADR-008 D1 + S4)
#
# The new-format DBAS artifact (build_backup_artifact) carries a CLEARTEXT
# manifest.json whose dedicated integer ``schema_version`` is the restore
# compatibility gate. On restore we MUST refuse an artifact built by a NEWER
# ECM (schema_version > BACKUP_SCHEMA_VERSION) BEFORE any mutation — a v0.19
# archive restored on a v0.18 build would otherwise silently partial-restore
# and corrupt state. The rule (mirrors build_backup_artifact's contract):
# manifest schema_version <= BACKUP_SCHEMA_VERSION is accepted; anything newer
# (or missing/malformed) is refused.
#
# SECURITY (D1 + S4 — no schema-internals leakage): the user-facing message is
# EXACTLY "Unsupported backup version" with NO version numbers and NO schema
# internals. The actual detail (got X, support up to Y) is logged SERVER-SIDE
# only for operator troubleshooting.
#
# NOTE: the manifest ``schema_version`` and the embedded journal.db
# alembic_version are TWO DISTINCT axes. This gate is ONLY the manifest
# schema_version.
# ---------------------------------------------------------------------------

# The ONLY user-facing string for a version refusal. No interpolation: it must
# never carry a version number or any schema internal.
UNSUPPORTED_BACKUP_VERSION_MESSAGE = "Unsupported backup version"


class UnsupportedBackupVersionError(Exception):
    """Raised when a restore artifact's manifest schema_version is unsupported.

    ``str(err)`` is EXACTLY :data:`UNSUPPORTED_BACKUP_VERSION_MESSAGE` — the
    user-facing message — and carries NO version numbers or schema internals
    (ADR-008 D1 + S4). The actual version detail is logged server-side by the
    raiser before this is raised.
    """

    def __init__(self, message: str = UNSUPPORTED_BACKUP_VERSION_MESSAGE):
        super().__init__(message)


def validate_restore_schema_version(manifest) -> None:
    """Refuse a restore artifact whose manifest schema_version is unsupported.

    Reusable version comparator for the restore-ingest chokepoint. Applies the
    same rule build_backup_artifact stamps: ``schema_version <=
    BACKUP_SCHEMA_VERSION`` is accepted; a NEWER artifact (or one with a
    missing/malformed schema_version) is REFUSED.

    Raises :class:`UnsupportedBackupVersionError` whose message is EXACTLY
    :data:`UNSUPPORTED_BACKUP_VERSION_MESSAGE` (no version leak). The actual
    detail is logged server-side (lazy %%-formatting) BEFORE raising.

    Args:
        manifest: The parsed manifest dict. A non-dict, a missing
            ``schema_version``, or a non-int (bool excluded) value is treated as
            unknown/invalid and refused — never accepted by default.

    Returns:
        None on an accepted (supported) version.
    """
    version = manifest.get("schema_version") if isinstance(manifest, dict) else None

    # bool is an int subclass; reject it explicitly so True/False can't pass.
    if not isinstance(version, int) or isinstance(version, bool):
        logger.warning(
            "[BACKUP] Refusing restore: manifest schema_version missing or "
            "malformed (got %r); this build supports up to %d",
            version, BACKUP_SCHEMA_VERSION,
        )
        raise UnsupportedBackupVersionError()

    if version > BACKUP_SCHEMA_VERSION:
        logger.warning(
            "[BACKUP] Refusing restore: artifact schema_version=%d is newer "
            "than supported (this build supports up to %d). Refuse before any "
            "mutation to avoid a silent partial restore.",
            version, BACKUP_SCHEMA_VERSION,
        )
        raise UnsupportedBackupVersionError()

    logger.debug(
        "[BACKUP] Restore artifact schema_version=%d accepted (supported up to %d)",
        version, BACKUP_SCHEMA_VERSION,
    )


def guard_artifact_against_zip_bomb(
    zf: zipfile.ZipFile, *, legacy_compatibility: bool = False
) -> None:
    """Refuse a decompression-bomb archive BEFORE any member is ``zf.read()``.

    Implements the threat-model D2 control
    (``docs/security/threat_model_dbas_import.md`` §3.5 / checklist 5). The 2 GiB
    upload cap bounds only the COMPRESSED bytes; a small high-ratio ZIP can still
    expand to gigabytes and OOM the single-process container. This guard iterates
    ``zf.infolist()`` (header metadata only — it never decompresses) and refuses
    the archive if any of the D2 caps is exceeded:

    * entry count   > :data:`_ARTIFACT_MAX_ENTRIES`
    * per-entry declared uncompressed size >
      :data:`_ARTIFACT_MAX_MEMBER_UNCOMPRESSED`,
    * per-entry decompressed:compressed ratio > :data:`_ARTIFACT_MAX_ENTRY_RATIO`
      (only for entries whose compressed size exceeds
      :data:`_ARTIFACT_RATIO_MIN_COMPRESSED`, so a tiny stored file is not
      falsely flagged), and
    * cumulative declared uncompressed size > :data:`_ARTIFACT_MAX_TOTAL_UNCOMPRESSED`.

    This is the shared DBAS guard called at the start of validation
    (:func:`validate_artifact_manifest`) AND at the start of decode
    (:func:`dbas.restore_artifact.decode_artifact_to_plan`) so both read sites are
    protected from one place. The refusal message is GENERIC — it leaks no sizes,
    ratios, or member names to the caller; the specifics are logged server-side.

    Note: ``ZipInfo.file_size`` is the archive's own DECLARED uncompressed size and
    is attacker-controlled, but that is exactly the point — a bomb DECLARES a huge
    size, so refusing on the declared size stops the read before CPython would
    decompress to discover the real size. A liar that under-declares to slip past
    the ratio/cumulative check is still bounded by the per-entry write loop in the
    importers (D8 one-at-a-time decode) and the 2 GiB compressed cap.
    """
    infos = zf.infolist()
    if len(infos) > _ARTIFACT_MAX_ENTRIES:
        logger.warning(
            "[BACKUP] Refusing restore: archive has %d entries (max %d)",
            len(infos), _ARTIFACT_MAX_ENTRIES,
        )
        raise HTTPException(status_code=400, detail="Backup archive rejected")

    total_uncompressed = 0
    for info in infos:
        uncompressed = info.file_size
        compressed = info.compress_size
        if (
            info.filename.startswith(ARTIFACT_LOGO_DIR + "/")
            and uncompressed > MAX_LOGO_BYTES
        ):
            logger.warning(
                "[BACKUP] Refusing restore: logo member %s declared size exceeds %d bytes",
                info.filename, MAX_LOGO_BYTES,
            )
            raise HTTPException(status_code=400, detail="Backup archive rejected")
        if uncompressed > _ARTIFACT_MAX_MEMBER_UNCOMPRESSED:
            logger.warning(
                "[BACKUP] Refusing restore: member %s declared size exceeds %d bytes",
                info.filename, _ARTIFACT_MAX_MEMBER_UNCOMPRESSED,
            )
            raise HTTPException(status_code=400, detail="Backup archive rejected")
        total_uncompressed += uncompressed
        if total_uncompressed > _ARTIFACT_MAX_TOTAL_UNCOMPRESSED:
            logger.warning(
                "[BACKUP] Refusing restore: cumulative uncompressed size exceeds "
                "%d bytes (member %s)",
                _ARTIFACT_MAX_TOTAL_UNCOMPRESSED, info.filename,
            )
            raise HTTPException(status_code=400, detail="Backup archive rejected")
        if compressed > _ARTIFACT_RATIO_MIN_COMPRESSED:
            ratio = uncompressed / compressed
            ratio_limit = _ARTIFACT_MAX_ENTRY_RATIO
            if legacy_compatibility and (
                info.filename in _LEGACY_COMPRESSIBLE_MEMBER_NAMES
                or info.filename.startswith(_LEGACY_COMPRESSIBLE_MEMBER_PREFIXES)
            ):
                ratio_limit = _LEGACY_MAX_ENTRY_RATIO
            if ratio > ratio_limit:
                logger.warning(
                    "[BACKUP] Refusing restore: member %s compression ratio %.1f "
                    "exceeds %dx (%d -> %d bytes)",
                    info.filename, ratio, ratio_limit,
                    compressed, uncompressed,
                )
                raise HTTPException(status_code=400, detail="Backup archive rejected")


def _is_unambiguous_artifact_member_name(name: str, *, directory: bool) -> bool:
    """Accept one canonical relative POSIX member spelling."""
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    path = name[:-1] if directory and name.endswith("/") else name
    if not path or (directory != name.endswith("/")):
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


def _read_artifact_manifest(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> dict:
    """Read and parse the manifest under its dedicated small-object bound."""
    if info.is_dir() or info.file_size > _MAX_DBAS_MANIFEST_BYTES:
        logger.warning("[BACKUP] Refusing restore: artifact manifest exceeds its size limit")
        raise HTTPException(status_code=400, detail="Invalid backup manifest")
    try:
        with zf.open(info, "r") as source:
            raw = source.read(_MAX_DBAS_MANIFEST_BYTES + 1)
        if len(raw) > _MAX_DBAS_MANIFEST_BYTES:
            raise ValueError("manifest exceeds bounded read")
        manifest = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, KeyError) as exc:
        logger.warning("[BACKUP] Refusing restore: unreadable artifact manifest: %s", exc)
        raise HTTPException(status_code=400, detail="Invalid backup manifest")
    if not isinstance(manifest, dict):
        logger.warning("[BACKUP] Refusing restore: artifact manifest is not an object")
        raise HTTPException(status_code=400, detail="Invalid backup manifest")
    return manifest


def _verify_artifact_member_integrity(
    zf: zipfile.ZipFile, manifest: dict, infos: list[zipfile.ZipInfo]
) -> None:
    """Verify each manifest-listed member's SHA-256 against the ZIP bytes.

    Pairs with the version gate at the same chokepoint (grooming: validate
    version + integrity together BEFORE mutation). A member whose bytes do not
    match the manifest hash, or a manifest member absent from the ZIP, refuses
    the restore with a generic integrity message that leaks NO schema internals
    (no schema_version numbers). The detail (which member, hash mismatch) is
    logged server-side.

    NOTE: the whole-artifact SHA-256 sidecar (verify_artifact_sha256) lives
    next to the file on disk and is not present inside an uploaded ZIP; this
    per-member check is the integrity guarantee available at the ingest
    chokepoint from the ZIP alone.
    """
    files = manifest.get("files")
    if not isinstance(files, list):
        logger.warning("[BACKUP] Refusing restore: manifest has no per-file hash list")
        raise HTTPException(status_code=400, detail="Backup integrity check failed")

    members_by_name = {
        info.filename: info
        for info in infos
        if not info.is_dir() and info.filename != ARTIFACT_MANIFEST_NAME
    }
    manifest_paths: set[str] = set()
    entries_to_hash: list[tuple[str, str]] = []
    for entry in files:
        if not isinstance(entry, dict):
            logger.warning("[BACKUP] Refusing restore: malformed manifest file entry %r", entry)
            raise HTTPException(status_code=400, detail="Backup integrity check failed")
        path = entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(path, str) or not isinstance(expected, str):
            logger.warning("[BACKUP] Refusing restore: malformed manifest file entry %r", entry)
            raise HTTPException(status_code=400, detail="Backup integrity check failed")
        if (
            path == ARTIFACT_MANIFEST_NAME
            or not _is_unambiguous_artifact_member_name(path, directory=False)
            or path in manifest_paths
        ):
            logger.warning("[BACKUP] Refusing restore: duplicate or ambiguous manifest path %r", path)
            raise HTTPException(status_code=400, detail="Backup integrity check failed")
        manifest_paths.add(path)
        if path not in members_by_name:
            logger.warning("[BACKUP] Refusing restore: manifest member %s absent from artifact", path)
            raise HTTPException(status_code=400, detail="Backup integrity check failed")
        entries_to_hash.append((path, expected))

    member_paths = set(members_by_name)
    if manifest_paths != member_paths:
        logger.warning(
            "[BACKUP] Refusing restore: manifest membership differs from archive "
            "(unlisted=%r, missing=%r)",
            sorted(member_paths - manifest_paths), sorted(manifest_paths - member_paths),
        )
        raise HTTPException(status_code=400, detail="Backup integrity check failed")

    for path, expected in entries_to_hash:
        digest = hashlib.sha256()
        with zf.open(members_by_name[path], "r") as member:
            while chunk := member.read(_ARTIFACT_HASH_CHUNK_BYTES):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            logger.warning(
                "[BACKUP] Refusing restore: integrity mismatch on member %s "
                "(expected %s, got %s)", path, expected, actual,
            )
            raise HTTPException(status_code=400, detail="Backup integrity check failed")


def _read_zip_member_bounded(
    zf: zipfile.ZipFile,
    name: str,
    max_bytes: int,
    *,
    detail: str,
) -> bytes:
    """Read one small control member without an unbounded ``read()`` call."""
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise HTTPException(status_code=400, detail=detail)
    if info.file_size > max_bytes:
        logger.warning(
            "[BACKUP] Refusing restore: %s declares %d bytes (member cap %d)",
            name,
            info.file_size,
            max_bytes,
        )
        raise HTTPException(status_code=400, detail=detail)

    chunks: list[bytes] = []
    total = 0
    with zf.open(info) as member:
        while True:
            chunk = member.read(min(_RESTORE_UPLOAD_CHUNK, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                logger.warning(
                    "[BACKUP] Refusing restore: %s expanded beyond member cap %d",
                    name,
                    max_bytes,
                )
                raise HTTPException(status_code=400, detail=detail)
    return b"".join(chunks)


def validate_artifact_manifest(zf: zipfile.ZipFile) -> dict:
    """Validate a new-format DBAS artifact at the restore-ingest chokepoint.

    Runs BEFORE any restore mutation, in this order:

    1. reject duplicate or ambiguous ZIP member names from central-directory
       metadata,
    2. bounded-parse the cleartext ``manifest.json`` header,
    3. **version gate** — refuse a newer/unknown schema_version (the highest
       priority: an incompatible artifact is rejected before we even trust its
       integrity claims), then
    4. **integrity** — require one manifest row per non-directory payload and
       verify each member's SHA-256.

    Returns the parsed manifest on success. Refusals raise ``HTTPException(400)``
    with a user-facing message that leaks NO schema internals; the version
    refusal message is EXACTLY :data:`UNSUPPORTED_BACKUP_VERSION_MESSAGE`. All
    detail is logged server-side.
    """
    # D2 zip-bomb guard FIRST — before any zf.read(), including the manifest read
    # below. A high-ratio member must be refused before it can be decompressed.
    guard_artifact_against_zip_bomb(zf)

    infos = zf.infolist()
    seen_names: set[str] = set()
    for info in infos:
        if info.filename in seen_names:
            logger.warning("[BACKUP] Refusing restore: duplicate archive member %r", info.filename)
            raise HTTPException(status_code=400, detail="Backup integrity check failed")
        seen_names.add(info.filename)
        if not _is_unambiguous_artifact_member_name(info.filename, directory=info.is_dir()):
            logger.warning("[BACKUP] Refusing restore: ambiguous archive member %r", info.filename)
            raise HTTPException(status_code=400, detail="Backup integrity check failed")

    if ARTIFACT_MANIFEST_NAME not in seen_names:
        logger.warning("[BACKUP] Refusing restore: artifact missing %s", ARTIFACT_MANIFEST_NAME)
        raise HTTPException(status_code=400, detail="Not a valid ECM backup artifact")

    manifest = _read_artifact_manifest(zf, zf.getinfo(ARTIFACT_MANIFEST_NAME))

    # 2. Version gate FIRST — refuse an incompatible artifact before trusting
    #    anything else about it. Translate the internal exception into the
    #    HTTP error WITHOUT adding any version detail to the body.
    try:
        validate_restore_schema_version(manifest)
    except UnsupportedBackupVersionError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 3. Integrity AFTER the version is known-supported.
    _verify_artifact_member_integrity(zf, manifest, infos)

    return manifest


class _ValidatedLegacyBackup(dict):
    """Validated metadata plus retained, private member inodes for installation."""

    def __init__(self, manifest: dict):
        super().__init__(manifest)
        self._workspace = Path(tempfile.mkdtemp(prefix="ecm-legacy-restore-"))
        os.chmod(self._workspace, stat.S_IRWXU)
        self._files: dict[str, BinaryIO] = {}
        self.staged_paths: dict[str, Path] = {}
        self.staged_inodes: dict[str, int] = {}

    def stage(self, zf: zipfile.ZipFile, name: str) -> None:
        path = self._workspace / ("member-%d" % len(self._files))
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        staged = os.fdopen(fd, "w+b")
        try:
            with zf.open(name) as member:
                while chunk := member.read(_RESTORE_UPLOAD_CHUNK):
                    staged.write(chunk)
            staged.flush()
            staged.seek(0)
        except Exception:
            staged.close()
            raise
        self._files[name] = staged
        self.staged_paths[name] = path
        self.staged_inodes[name] = os.fstat(staged.fileno()).st_ino

    def file(self, name: str):
        staged = self._files[name]
        staged.seek(0)
        return staged

    def load_json(self, name: str, max_bytes: int):
        staged = self.file(name)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = staged.read(min(_RESTORE_UPLOAD_CHUNK, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("staged JSON member exceeds its limit")
        return json.loads(b"".join(chunks))

    def close(self) -> None:
        for staged in self._files.values():
            staged.close()
        self._files.clear()
        shutil.rmtree(self._workspace, ignore_errors=True)

    def __del__(self):
        self.close()


def _validate_backup_zip(zf: zipfile.ZipFile) -> _ValidatedLegacyBackup:
    """Validate a backup zip file and return its manifest."""
    # Bound metadata before reading the legacy manifest. Only ECM's historical
    # SQLite/M3U members receive the documented 1000x compatibility ceiling;
    # DBAS artifacts still call this guard with its default 100x policy.
    guard_artifact_against_zip_bomb(zf, legacy_compatibility=True)

    # Must contain manifest
    if "ecm_backup.json" not in zf.namelist():
        raise HTTPException(status_code=400, detail="Not a valid ECM backup: missing ecm_backup.json manifest")

    # Parse manifest
    try:
        manifest = json.loads(
            _read_zip_member_bounded(
                zf,
                "ecm_backup.json",
                _MAX_LEGACY_MANIFEST_BYTES,
                detail="Invalid backup manifest",
            )
        )
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError) as e:
        raise HTTPException(status_code=400, detail="Invalid backup manifest: %s" % str(e))

    if not isinstance(manifest, dict) or "version" not in manifest:
        raise HTTPException(status_code=400, detail="Invalid backup manifest: missing version")

    # Check path safety before materializing any member.
    for name in zf.namelist():
        if name.startswith("/") or ".." in name:
            raise HTTPException(status_code=400, detail="Backup contains unsafe file paths")
        resolved = (CONFIG_DIR / name).resolve()
        if not str(resolved).startswith(str(CONFIG_DIR.resolve())):
            raise HTTPException(status_code=400, detail="Backup contains unsafe file paths")

    plan = _ValidatedLegacyBackup(manifest)
    try:
        restorable = {
            name
            for name in zf.namelist()
            if name in {"settings.json", "journal.db"}
            or any(name.startswith(directory + "/") for directory in LEGACY_RESTORE_DIRS)
        }
        if "settings.json" in restorable:
            info = zf.getinfo("settings.json")
            if info.file_size > _MAX_LEGACY_SETTINGS_BYTES:
                logger.warning(
                    "[BACKUP] Refusing restore: settings.json declares %d bytes "
                    "(member cap %d)",
                    info.file_size,
                    _MAX_LEGACY_SETTINGS_BYTES,
                )
                raise ValueError("settings member exceeds its limit")
        for name in sorted(restorable):
            if not name.endswith("/"):
                plan.stage(zf, name)

        # Validate settings using the same historical migrations and null
        # sanitation as config.load_settings().
        if "settings.json" in plan.staged_paths:
            settings = plan.load_json("settings.json", _MAX_LEGACY_SETTINGS_BYTES)
            if not isinstance(settings, dict):
                raise ValueError("settings must be an object")
            DispatcharrSettings.model_validate(prepare_settings_data(settings))

        if "journal.db" in plan.staged_paths:
            _validate_legacy_journal_db(plan)
    except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError):
        plan.close()
        raise HTTPException(status_code=400, detail="Backup contains invalid settings.json")
    except Exception:
        plan.close()
        raise

    return plan


_LEGACY_JOURNAL_BASELINE_SCHEMA = {
    "journal_entries": frozenset(
        {"id", "timestamp", "category", "action_type", "entity_name", "description"}
    ),
    "scheduled_tasks": frozenset(
        {"id", "task_id", "task_name", "enabled", "schedule_type"}
    ),
    "auto_creation_rules": frozenset(
        {"id", "name", "enabled", "priority", "conditions", "actions"}
    ),
}

# Standard ZIP production now drops journal_entries because it is unbounded
# audit history, not restorable configuration. Admission accepts either the
# original historical profile above or this producer profile; both still
# require the two configuration tables and their load-bearing columns.
_CURRENT_REDACTED_JOURNAL_SCHEMA = {
    table: columns
    for table, columns in _LEGACY_JOURNAL_BASELINE_SCHEMA.items()
    if table != "journal_entries"
}


def _validate_legacy_journal_db(plan: _ValidatedLegacyBackup) -> None:
    """Validate the retained journal inode without touching the live database."""
    staged = plan.file("journal.db")
    tmp_path = plan.staged_paths["journal.db"]
    connection = None
    try:
        if not staged.read(16).startswith(b"SQLite format 3"):
            raise HTTPException(
                status_code=400,
                detail="Backup contains invalid journal.db (not a SQLite database)",
            )
        staged.seek(0)

        # The workspace is 0700 and the member is 0600. Confirm the pathname
        # SQLite opens still names the retained descriptor's inode.
        if os.fstat(staged.fileno()).st_ino != tmp_path.stat().st_ino:
            raise HTTPException(status_code=400, detail="Backup contains invalid journal.db")
        connection = sqlite3.connect(f"{tmp_path.resolve().as_uri()}?mode=ro", uri=True)
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise HTTPException(status_code=400, detail="Backup contains invalid journal.db")

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        def matches(schema: dict[str, frozenset[str]]) -> bool:
            return all(
                required_columns.issubset(
                    {
                        row[1]
                        for row in connection.execute(
                            'PRAGMA table_info("%s")' % table
                        ).fetchall()
                    }
                )
                for table, required_columns in schema.items()
            )

        compatible = matches(_LEGACY_JOURNAL_BASELINE_SCHEMA) or (
            "journal_entries" not in tables
            and matches(_CURRENT_REDACTED_JOURNAL_SCHEMA)
        )
        if not compatible:
            raise HTTPException(
                status_code=400, detail="Backup contains incompatible journal.db"
            )
    except HTTPException:
        raise
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        logger.warning("[BACKUP] Refusing invalid legacy journal.db: %s", exc)
        raise HTTPException(status_code=400, detail="Backup contains invalid journal.db") from exc
    finally:
        if connection is not None:
            connection.close()


def _merge_settings_preserving_redacted(zip_settings_bytes: bytes) -> bytes:
    """Apply restored settings.json on top of existing settings, dropping
    REDACTED sentinels and the instance-bound MCP key.

    Mirrors the YAML restore semantics in _restore_settings (lines below) so
    a redacted ZIP behaves the same as a redacted YAML export. Backward-compat:
    Legacy non-redacted ZIP values remain restorable except for mcp_api_key.
    """
    try:
        zipped = json.loads(zip_settings_bytes)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Backup contains invalid settings.json") from exc
    if not isinstance(zipped, dict):
        raise HTTPException(status_code=400, detail="Backup contains invalid settings.json")

    if CONFIG_FILE.exists():
        try:
            existing = json.loads(CONFIG_FILE.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (OSError, json.JSONDecodeError):
            existing = {}
    else:
        existing = {}

    merged = dict(existing)
    skipped = []
    for key, value in zipped.items():
        if key == "mcp_api_key" or value == REDACTED:
            skipped.append(key)
            continue
        merged[key] = value
    if skipped:
        logger.info("[BACKUP] Preserved existing protected settings: %s", skipped)
    try:
        validated = DispatcharrSettings.model_validate(prepare_settings_data(merged))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail="Backup contains invalid settings.json") from exc
    return json.dumps(validated.model_dump(mode="json"), indent=2).encode("utf-8")


def _capture_existing_alert_method_configs(
    journal_path: Optional[Path] = None,
) -> dict[int, dict]:
    """Read existing alert_methods rows directly from journal.db so we can
    re-merge non-redacted credential fields after the restored DB is written.

    Returns {id: parsed_config_dict}. Rows with malformed JSON or missing
    table are skipped silently — the caller treats absent ids as 'no merge'.
    """
    journal_path = journal_path or JOURNAL_DB_FILE
    if not journal_path.exists():
        return {}
    out: dict[int, dict] = {}
    try:
        conn = sqlite3.connect(str(journal_path))
    except sqlite3.Error as e:
        logger.warning("[BACKUP] Could not open journal.db for pre-restore capture: %s", e)
        return {}
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_methods'"
            )
            if cur.fetchone() is None:
                return {}
            cur.execute("SELECT id, config FROM alert_methods")
            rows = cur.fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("[BACKUP] Could not read alert_methods for pre-restore capture: %s", e)
            return {}
        for row_id, raw in rows:
            if not raw:
                continue
            try:
                cfg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(cfg, dict):
                out[row_id] = cfg
    finally:
        conn.close()
    return out


def _merge_alert_method_creds_after_restore(
    prior: dict[int, dict], journal_path: Optional[Path] = None
) -> None:
    """For each alert_methods row in the restored DB, restore non-redacted
    credential-class values from the prior snapshot when the restored value
    is the REDACTED sentinel. Match by row id.

    A restored ``config`` that is the sentinel AS A WHOLE (rather than a JSON
    object with sentinel values inside it) is the producer's fail-closed
    treatment of a row it could not parse — see
    :func:`_scrub_journal_db_in_place`. It is merged the same way, wholesale: the
    destination's own config for that row id is authoritative and is restored
    intact. Without this branch the fail-closed producer change would DESTROY a
    working alert method on every restore, which is exactly the round-trip
    asymmetry :data:`_ALERT_METHOD_PROTECTED_KEYS` exists to prevent.

    Backward-compat: legacy non-redacted ZIPs carry no sentinel — every value
    survives the merge unchanged.
    """
    journal_path = journal_path or JOURNAL_DB_FILE
    if not journal_path.exists():
        return
    try:
        conn = sqlite3.connect(str(journal_path))
    except sqlite3.Error as e:
        logger.warning("[BACKUP] Could not open restored journal.db for cred merge: %s", e)
        return
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_methods'"
            )
            if cur.fetchone() is None:
                return
            cur.execute("SELECT id, config FROM alert_methods")
            rows = cur.fetchall()
        except sqlite3.DatabaseError as e:
            logger.warning("[BACKUP] Could not read alert_methods after restore for merge: %s", e)
            return
        merged_count = 0
        for row_id, raw in rows:
            if not raw:
                continue
            if raw == REDACTED:
                # Whole-blob sentinel: the producer could not parse this row and
                # refused to ship it. Reinstate the destination's own config.
                prior_cfg = prior.get(row_id)
                if prior_cfg:
                    cur.execute(
                        "UPDATE alert_methods SET config=? WHERE id=?",
                        (json.dumps(prior_cfg), row_id),
                    )
                    merged_count += 1
                continue
            try:
                cfg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(cfg, dict):
                continue
            prior_cfg = prior.get(row_id, {})
            changed = False
            for key in _ALERT_METHOD_PROTECTED_KEYS:
                if cfg.get(key) == REDACTED and prior_cfg.get(key) not in (None, "", REDACTED):
                    cfg[key] = prior_cfg[key]
                    changed = True
            if changed:
                cur.execute(
                    "UPDATE alert_methods SET config=? WHERE id=?",
                    (json.dumps(cfg), row_id),
                )
                merged_count += 1
        conn.commit()
        if merged_count:
            logger.info(
                "[BACKUP] Re-merged credentials into %d alert_methods rows after restore",
                merged_count,
            )
    finally:
        conn.close()


# The operator-facing half of the account purge (bead …-gi4zn, finding A-1).
# ``restored_files`` alone reports what landed, never what an artifact could not
# carry, and an operator whose disaster-recovery restore returns 200 and then
# shows a first-run setup wizard has to guess whether that is correct.
FIRST_RUN_SETUP_NOTICE = (
    "This instance has no ECM user account. A standard (non-encrypted) backup "
    "carries no account credentials by design, so accounts are not restored from "
    "one — create your admin account through first-run setup. To migrate accounts "
    "between instances instead, take an encrypted backup with credentials included."
)


def _capture_existing_auth_rows(
    journal_path: Optional[Path] = None,
) -> dict[str, tuple[list[str], list[tuple]]]:
    """Snapshot this instance's OWN account rows before journal.db is replaced.

    Returns ``{table: (column_names, rows)}`` for :data:`_AUTH_IDENTITY_TABLES`.

    A standard artifact carries these tables EMPTY (see that tuple's comment), so
    without this snapshot a restore would log the admin out of their own live
    instance and drop them at the setup wizard — an availability regression the
    redaction fix does not need and must not cause. Paired with
    :func:`_reassert_auth_rows_after_restore`; same capture-then-merge shape as
    :func:`_capture_existing_alert_method_configs`.

    Best-effort by design: an unreadable live database yields an empty snapshot,
    which degrades to the restored artifact's own contents rather than failing the
    restore. That is the safe direction here — the worst case is the operator
    running first-run setup, and unlike the PRODUCER side (where an unrunnable
    scrub means a leak) nothing confidential turns on it.
    """
    journal_path = journal_path or JOURNAL_DB_FILE
    if not journal_path.exists():
        return {}
    out: dict[str, tuple[list[str], list[tuple]]] = {}
    try:
        conn = sqlite3.connect(str(journal_path))
    except sqlite3.Error as e:
        logger.warning("[BACKUP] Could not open journal.db to capture accounts: %s", e)
        return {}
    try:
        cur = conn.cursor()
        for table in _AUTH_IDENTITY_TABLES:
            try:
                cur.execute("SELECT * FROM %s" % table)  # noqa: S608 — name from a module-level literal tuple
                rows = cur.fetchall()
            except sqlite3.DatabaseError as e:
                # Missing on a pre-auth-schema database; unreadable is logged and
                # skipped for the same reason the docstring gives.
                logger.warning(
                    "[BACKUP] Could not capture %s before restore: %s", table, e
                )
                continue
            if not rows:
                continue
            out[table] = ([d[0] for d in cur.description], rows)
    finally:
        conn.close()
    return out


def _create_missing_auth_table(cur, table: str) -> list[str]:
    """Recreate one auth table in the restored journal.db from its MODEL.

    Needed because a standard artifact drops these tables outright, and
    :func:`_reassert_auth_rows_after_restore` has to put the destination's own
    accounts back BEFORE ``init_db()`` runs ``create_all``.

    Compiling ``CreateTable`` against the SQLite dialect keeps the recreated
    table in lock-step with the model that produced the captured rows, so this
    cannot drift the way hand-written DDL would.

    Args:
        cur: An open cursor on the restored journal.db.
        table: The table name, always one of :data:`_AUTH_IDENTITY_TABLES`.

    Returns:
        The created table's column names, or ``[]`` if it could not be created —
        best-effort like the rest of the restore side, where the worst case is
        the operator running first-run setup.
    """
    try:
        from sqlalchemy.dialects import sqlite as sqlite_dialect
        from sqlalchemy.schema import CreateTable

        from models import Base

        model_table = Base.metadata.tables[table]
        cur.execute(str(CreateTable(model_table).compile(dialect=sqlite_dialect.dialect())))
    except Exception as e:  # noqa: BLE001 — best-effort; the fallback is first-run setup
        logger.warning(
            "[BACKUP] Could not recreate the %s table to reinstate this "
            "instance's accounts: %s", table, e,
        )
        return []
    logger.info(
        "[BACKUP] Recreated the %s table, which the restored artifact did not "
        "carry, to reinstate this instance's own accounts", table,
    )
    return [row[1] for row in cur.execute("PRAGMA table_info(%s)" % table)]


def _reassert_auth_rows_after_restore(
    prior: dict[str, tuple[list[str], list[tuple]]],
    journal_path: Optional[Path] = None,
) -> None:
    """Put this instance's own account rows back over the restored journal.db.

    Runs ONLY when the destination actually had accounts. That condition is what
    keeps a legacy (pre-…-gi4zn) ZIP's disaster-recovery path intact: restoring
    an old artifact that still carries ``users`` onto an EMPTY instance installs
    those users exactly as it always did, because there is nothing here to
    re-assert. Restoring onto an OWNED instance, by contrast, keeps the owner —
    which is both the availability guarantee and a tightening, since an artifact's
    ``users`` table can no longer silently replace the live one.

    Columns are intersected with the restored schema so a snapshot taken on a
    different ECM version cannot fail the insert on a column that moved.

    THE TABLE IS CREATED IF THE ARTIFACT DID NOT SHIP IT (bead …-gi4zn round 3).
    Since the allowlist DROPS the auth tables rather than emptying them, a
    standard artifact no longer contains ``users`` at all, and this function runs
    BEFORE ``init_db()`` — so without this step ``PRAGMA table_info`` would come
    back empty, the re-assert would silently skip, and an admin restoring a backup
    onto their OWN instance would be logged out and dropped at the setup wizard.
    That is precisely the availability regression the capture/re-assert pair
    exists to prevent, and it would have failed silently behind a 200.

    The DDL is compiled from the model rather than hand-written, so the recreated
    table cannot drift from the columns the snapshot holds.
    """
    if not prior.get("users"):
        return
    journal_path = journal_path or JOURNAL_DB_FILE
    if not journal_path.exists():
        return
    try:
        conn = sqlite3.connect(str(journal_path))
    except sqlite3.Error as e:
        logger.warning(
            "[BACKUP] Could not open the restored journal.db to reinstate "
            "accounts: %s", e,
        )
        return
    try:
        cur = conn.cursor()
        # Reverse of the DELETE order: parents before dependents.
        for table in reversed(_AUTH_IDENTITY_TABLES):
            captured = prior.get(table)
            try:
                cur.execute("PRAGMA table_info(%s)" % table)
                dest_columns = [row[1] for row in cur.fetchall()]
            except sqlite3.DatabaseError as e:
                logger.warning(
                    "[BACKUP] Could not inspect restored %s: %s", table, e
                )
                continue
            if not dest_columns and captured:
                dest_columns = _create_missing_auth_table(cur, table)
            if not dest_columns:
                continue
            try:
                cur.execute("DELETE FROM %s" % table)  # noqa: S608 — name from a module-level literal tuple
            except sqlite3.DatabaseError as e:
                logger.warning(
                    "[BACKUP] Could not clear restored %s before reinstating "
                    "this instance's accounts: %s", table, e,
                )
                continue
            if not captured:
                continue
            columns, rows = captured
            shared = [c for c in columns if c in dest_columns]
            if not shared:
                continue
            index = [columns.index(c) for c in shared]
            statement = "INSERT INTO %s (%s) VALUES (%s)" % (  # noqa: S608 — same
                table,
                ", ".join('"%s"' % c for c in shared),
                ", ".join("?" for _ in shared),
            )
            try:
                cur.executemany(statement, [tuple(r[i] for i in index) for r in rows])
            except sqlite3.DatabaseError as e:
                logger.warning(
                    "[BACKUP] Could not reinstate %d %s row(s) after restore: %s",
                    len(rows), table, e,
                )
        conn.commit()
        logger.info(
            "[BACKUP] Reinstated this instance's own account rows over the "
            "restored journal.db (%d users)", len(prior["users"][1]),
        )
    finally:
        conn.close()


# Operator-configured surfaces that a STANDARD artifact does not carry, mapped to
# what the operator has to do about it (bead …-gi4zn round 3, invariant 4). Only
# tables whose loss needs an ACTION belong here — the allowlist also drops history
# and telemetry, and telling an operator to "re-establish" their audit log would
# be noise that trains them to ignore the notice.
_REESTABLISH_ON_RESTORE: dict[str, str] = {
    "cloud_storage_targets": "cloud storage target(s) (Settings -> Export/Publish)",
    "sync_targets": "sync target(s) (Settings -> Export/Publish)",
    "m3u_digest_settings": "M3U digest settings, including the email recipient list",
    "event_sync_exclusions": "event-sync never-attach exclusion(s)",
}

# Set by :func:`_restore_from_zip`, read and cleared by
# :func:`_post_restore_account_notices`.
#
# A module-level handoff rather than a return value because the three restore
# endpoints call those two functions independently, and widening
# ``_restore_from_zip``'s return arity would break silently in the tests that
# patch it with a ``MagicMock``. It is written at the START of every restore and
# CLEARED when read, so a failed restore cannot leak a notice into the next one.
# Restores hold the database closed and are human-admin gated, so they do not
# overlap.
_LAST_RESTORE_CONFIG_LOSSES: dict[str, int] = {}


def _count_reestablish_rows(journal_path: Optional[Path] = None) -> dict[str, int]:
    """Row counts for :data:`_REESTABLISH_ON_RESTORE`, read off the live database.

    Used on BOTH sides of the file swap so the notice names only what this
    instance ACTUALLY lost. Counting after the restore alone would tell an
    operator who never configured cloud storage to go re-establish it, and a
    notice that cries wolf is one nobody reads by the time it is true.

    Best-effort: a table that cannot be read is omitted rather than guessed at.
    """
    journal_path = journal_path or JOURNAL_DB_FILE
    if not journal_path.exists():
        return {}
    try:
        conn = sqlite3.connect(str(journal_path))
    except sqlite3.Error:
        return {}
    counts: dict[str, int] = {}
    try:
        for table in _REESTABLISH_ON_RESTORE:
            try:
                counts[table] = conn.execute(
                    'SELECT COUNT(*) FROM "%s"' % table  # noqa: S608 — name from a module-level literal dict
                ).fetchone()[0]
            except sqlite3.DatabaseError:
                # Absent on this schema version — nothing to lose, nothing to say.
                continue
    finally:
        conn.close()
    return counts


def _post_restore_account_notices() -> list[str]:
    """Notices for the restore response, read off the LIVE post-restore database.

    Derived from the instance's actual state rather than predicted from what the
    artifact contained, so it cannot claim a lockout that did not happen or miss
    one that did. Empty in the ordinary case (accounts present, nothing lost).

    Two notices, both live-derived:

    1. The first-run-setup notice, when the instance ends up with no accounts.
    2. The re-establish notice, when a configured surface that a standard
       artifact does not carry HAD rows before the restore and has none after
       (:data:`_REESTABLISH_ON_RESTORE`). This is the operator-facing half of
       the allowlist: ``restored_files`` reports what landed and structurally
       cannot report what the artifact could not carry.
    """
    notices: list[str] = []

    lost = {t: n for t, n in _LAST_RESTORE_CONFIG_LOSSES.items() if n}
    _LAST_RESTORE_CONFIG_LOSSES.clear()
    if lost:
        detail = "; ".join(
            "%d %s" % (n, _REESTABLISH_ON_RESTORE[t]) for t, n in sorted(lost.items())
        )
        notice = (
            "This backup did not carry every configured surface, because a "
            "standard (non-encrypted) backup omits credential stores and "
            "personal data by design. Re-establish: %s. To carry these between "
            "instances instead, take an encrypted backup with credentials "
            "included." % detail
        )
        logger.warning("[BACKUP] %s", notice)
        notices.append(notice)

    if not JOURNAL_DB_FILE.exists():
        return notices
    try:
        conn = sqlite3.connect(str(JOURNAL_DB_FILE))
    except sqlite3.Error:
        return notices
    try:
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    except sqlite3.DatabaseError:
        # No users table at all (pre-auth-schema database) — the setup wizard is
        # this instance's normal state, not a notice-worthy outcome.
        return notices
    finally:
        conn.close()
    if count:
        return notices
    logger.warning("[BACKUP] %s", FIRST_RUN_SETUP_NOTICE)
    notices.append(FIRST_RUN_SETUP_NOTICE)
    return notices


# Directory trees a legacy restore recreates that must NOT be world-readable, and
# the modes they are recreated with. ``tls`` holds a private key; the loop below
# does rmtree -> mkdir -> write_bytes at the process UMASK, so it CHOOSES the
# mode rather than inheriting an existing one, and at the container's umask 002
# that choice was 0775/0664. These are the same modes ``backend/tls/storage.py``
# (``ensure_directory`` / ``save_certificate``) enforces when ECM writes the
# tree itself, so a restored key is no more exposed than a freshly issued one.
_RESTORED_DIR_MODES: dict[str, tuple[int, int]] = {
    "tls": (0o700, 0o600),
}


def _apply_restored_directory_modes(dir_rel: str, dir_path: Path, names: list[str]) -> None:
    """Tighten the permissions of a directory tree the restore just recreated.

    A tree with no entry in :data:`_RESTORED_DIR_MODES` is left at the process
    umask, which is correct for ``uploads/logos`` and ``m3u_uploads`` — those are
    served content, not key material.

    Args:
        dir_rel: The tree's path relative to ``CONFIG_DIR``.
        dir_path: The absolute directory just recreated.
        names: The archive member names written into it.
    """
    modes = _RESTORED_DIR_MODES.get(dir_rel)
    if modes is None:
        return
    dir_mode, file_mode = modes
    try:
        os.chmod(dir_path, dir_mode)
        for name in names:
            os.chmod(CONFIG_DIR / name, file_mode)
    except OSError as e:
        # Non-fatal: the files are restored and usable. Surface it loudly —
        # material that should be owner-only is not.
        logger.warning(
            "[BACKUP] Could not tighten permissions on restored %s: %s", dir_rel, e
        )


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _random_absent_sibling(path: Path, label: str) -> Path:
    candidate = Path(tempfile.mkdtemp(prefix=f".{path.name}.{label}-", dir=path.parent))
    candidate.rmdir()
    return candidate


def _fsync_directory_best_effort(path: Path) -> None:
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(descriptor)
    except OSError as exc:
        logger.warning("[BACKUP] Could not fsync restore directory %s: %s", path, exc)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _stage_restore_file(
    destination: Path,
    *,
    content: Optional[bytes] = None,
    source: Optional[BinaryIO] = None,
) -> Path:
    """Fully write and fsync a file beside its destination before shutdown."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{destination.name}.restore-stage-", dir=destination.parent
    )
    staged = Path(raw_path)
    try:
        with os.fdopen(descriptor, "w+b") as output:
            os.fchmod(output.fileno(), 0o600)
            if content is not None:
                output.write(content)
            elif source is not None:
                source.seek(0)
                shutil.copyfileobj(source, output, _RESTORE_UPLOAD_CHUNK)
            output.flush()
            os.fsync(output.fileno())
        _fsync_directory_best_effort(destination.parent)
        return staged
    except BaseException:
        try:
            staged.unlink()
        except OSError:
            pass
        raise


def _stage_restore_directory(
    dir_rel: str,
    names: list[str],
    manifest: _ValidatedLegacyBackup,
) -> Path:
    """Materialize and fsync a complete tree beside the destination tree."""
    destination = CONFIG_DIR / dir_rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.restore-stage-", dir=destination.parent
        )
    )
    try:
        for name in names:
            relative = Path(name).relative_to(dir_rel)
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w+b") as output:
                manifest.file(name).seek(0)
                shutil.copyfileobj(
                    manifest.file(name), output, _RESTORE_UPLOAD_CHUNK
                )
                output.flush()
                os.fsync(output.fileno())

        modes = _RESTORED_DIR_MODES.get(dir_rel)
        if modes is not None:
            dir_mode, file_mode = modes
            for directory in [staged, *[p for p in staged.rglob("*") if p.is_dir()]]:
                os.chmod(directory, dir_mode)
            for file_path in (p for p in staged.rglob("*") if p.is_file()):
                os.chmod(file_path, file_mode)

        directories = [staged, *[p for p in staged.rglob("*") if p.is_dir()]]
        for directory in reversed(directories):
            _fsync_directory_best_effort(directory)
        _fsync_directory_best_effort(destination.parent)
        return staged
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise


def _remove_restore_path(path: Path) -> None:
    if not _path_exists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _rollback_restore_swaps(records: list[dict]) -> None:
    """Compensate completed same-filesystem swaps and verify prior inodes."""
    errors: list[str] = []
    for record in reversed(records):
        target = record["target"]
        backup = record["backup"]
        discarded = None
        try:
            if record["installed"] and _path_exists(target):
                discarded = _random_absent_sibling(target, "rollback-discard")
                os.replace(target, discarded)
            if backup is not None and _path_exists(backup):
                os.replace(backup, target)

            if record["identity"] is None:
                if _path_exists(target):
                    raise RuntimeError("target should be absent")
            else:
                current = os.lstat(target)
                identity = (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode))
                if identity != record["identity"]:
                    raise RuntimeError("prior inode was not restored")
        except BaseException as exc:
            errors.append(f"{target}: {exc}")
        finally:
            if discarded is not None:
                try:
                    _remove_restore_path(discarded)
                except OSError as exc:
                    logger.warning(
                        "[BACKUP] Could not remove failed restore artifact %s: %s",
                        discarded,
                        exc,
                    )
            _fsync_directory_best_effort(target.parent)
    if errors:
        raise RuntimeError("Restore rollback could not be verified: " + "; ".join(errors))


def _swap_staged_restore(staged_items: list[tuple[Path, Path]]) -> list[dict]:
    """Swap adjacent staged artifacts into place, compensating on any failure."""
    records: list[dict] = []
    try:
        for target, staged in staged_items:
            prior = os.lstat(target) if _path_exists(target) else None
            backup = (
                _random_absent_sibling(target, "restore-backup")
                if prior is not None
                else None
            )
            record = {
                "target": target,
                "backup": backup,
                "identity": (
                    (prior.st_dev, prior.st_ino, stat.S_IFMT(prior.st_mode))
                    if prior is not None
                    else None
                ),
                "installed": False,
            }
            records.append(record)
            if backup is not None:
                os.replace(target, backup)
            os.replace(staged, target)
            record["installed"] = True
            _fsync_directory_best_effort(target.parent)
        return records
    except BaseException:
        _rollback_restore_swaps(records)
        raise


def _discard_restore_backups(records: list[dict]) -> None:
    for record in records:
        backup = record["backup"]
        if backup is not None:
            try:
                _remove_restore_path(backup)
            except OSError as exc:
                # The new state is already live and init_db succeeded. Once any
                # prior backup has been deleted, cleanup is not rollback-safe;
                # retain and report a residue rather than risk removing live data.
                logger.warning(
                    "[BACKUP] Could not remove prior restore artifact %s: %s",
                    backup,
                    exc,
                )
            _fsync_directory_best_effort(record["target"].parent)


def _restore_from_zip(zf: zipfile.ZipFile, manifest: dict) -> list[str]:
    """Restore with destination-local atomic swaps and verified compensation.

    This is not a cross-filesystem transaction. Each file/tree is staged beside
    its own destination so its individual ``os.replace`` is atomic; if a later
    swap or database initialization fails, prior inodes are renamed back and
    verified before the old database is reinitialized.
    """
    restored: list[str] = []
    staged_items: list[tuple[Path, Path]] = []
    records: list[dict] = []
    database_closed = False
    failure_reinitialized = False

    # Finish settings validation and normalization before any database shutdown
    # or live write. Credential authority is reloaded later, at commit time.
    restored_settings = None
    if "settings.json" in manifest.staged_paths:
        settings = manifest.load_json("settings.json", _MAX_LEGACY_SETTINGS_BYTES)
        restored_settings = DispatcharrSettings.model_validate_json(
            _merge_settings_preserving_redacted(
                json.dumps(settings).encode("utf-8")
            )
        )

    # Capture existing alert_methods.config BEFORE we close/replace the DB so
    # we can merge real creds back where the restored ZIP has REDACTED.
    prior_alert_configs = _capture_existing_alert_method_configs()
    # Same, for this instance's own account rows: a standard artifact carries
    # those tables EMPTY (bead …-gi4zn), and a restore must not log the operator
    # out of the instance they are restoring.
    prior_auth_rows = _capture_existing_auth_rows()
    # Row counts for the configured surfaces a standard artifact cannot carry, so
    # the restore response can name what THIS instance actually lost rather than
    # what an artifact might not have held. Cleared first: a previous failed
    # restore must not leak its losses into this one's notices.
    _LAST_RESTORE_CONFIG_LOSSES.clear()
    prior_reestablish_counts = _count_reestablish_rows()

    try:
        # Complete every potentially failing copy before closing SQLite or
        # touching a live artifact. Every stage resides on its target filesystem.
        if "journal.db" in manifest.staged_paths:
            staged_journal = _stage_restore_file(
                JOURNAL_DB_FILE, source=manifest.file("journal.db")
            )
            _merge_alert_method_creds_after_restore(
                prior_alert_configs, staged_journal
            )
            _reassert_auth_rows_after_restore(prior_auth_rows, staged_journal)
            with staged_journal.open("rb") as staged_db:
                os.fsync(staged_db.fileno())
            staged_items.append((JOURNAL_DB_FILE, staged_journal))
            restored.append("journal.db")

        for dir_rel in LEGACY_RESTORE_DIRS:
            dir_path = CONFIG_DIR / dir_rel
            prefix = dir_rel + "/"
            dir_files = [
                name for name in manifest.staged_paths if name.startswith(prefix)
            ]
            if dir_files:
                staged_items.append(
                    (dir_path, _stage_restore_directory(dir_rel, dir_files, manifest))
                )
                restored.extend(dir_files)

        close_db()
        database_closed = True
        logger.info("[BACKUP] Database closed for restore")
        records = _swap_staged_restore(staged_items)
        try:
            init_db()
            if restored_settings is not None:
                # The generic saver reloads credential authority under the
                # lifecycle lock. Archived and pre-restore snapshots therefore
                # cannot overwrite a rotation that committed during staging.
                save_settings(restored_settings, settings_file=CONFIG_FILE)
                restored.append("settings.json")
        except BaseException:
            # init_db may have opened connections or partially migrated the new
            # journal. Close those before putting the prior inode back.
            close_db()
            rollback_records = records
            records = []
            _rollback_restore_swaps(rollback_records)
            init_db()
            failure_reinitialized = True
            logger.info("[BACKUP] Prior database reinitialized after restore rollback")
            raise
        _discard_restore_backups(records)
        records = []
        logger.info("[BACKUP] Database reinitialized after restore")
    except BaseException:
        # A staging failure occurs before close_db and has no live compensation.
        # A swap failure compensates inside _swap_staged_restore, then the prior
        # database still needs to be made available again.
        if records:
            _rollback_restore_swaps(records)
            records = []
        if database_closed and not failure_reinitialized:
            try:
                init_db()
            except BaseException as init_exc:
                raise RuntimeError(
                    "Restore failed and the prior database could not be reinitialized"
                ) from init_exc
        raise
    finally:
        for _, staged in staged_items:
            try:
                _remove_restore_path(staged)
            except OSError as exc:
                logger.warning("[BACKUP] Could not remove restore stage %s: %s", staged, exc)

    for name in restored:
        logger.info("[BACKUP] Restored %s", name)

    # Compare the same live counts across the swap. init_db() has already run, so
    # every model-declared table the artifact dropped is back and empty — a table
    # that went from N rows to 0 is a real loss this operator has to act on.
    post_counts = _count_reestablish_rows()
    _LAST_RESTORE_CONFIG_LOSSES.update(
        {
            table: before
            for table, before in prior_reestablish_counts.items()
            if before and not post_counts.get(table, 0)
        }
    )

    # Clear settings cache and reset client
    clear_settings_cache()
    try:
        reset_client()
    except Exception as e:
        logger.warning("[BACKUP] Failed to reset Dispatcharr client (non-fatal): %s", e)

    return restored


@router.get("/create")
async def create_backup(_admin=RequireAdminIfEnabled):
    """Create and download a backup zip of all ECM configuration. Admin only."""
    logger.info("[BACKUP] Creating backup")

    try:
        buf = _create_backup_zip()
        filename = _get_backup_filename()

        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as e:
        logger.exception("[BACKUP] Failed to create backup: %s", e)
        raise HTTPException(status_code=500, detail="Failed to create backup: %s" % str(e))


@router.post("/restore")
async def restore_backup(file: UploadFile = File(...), _admin=RequireHumanAdminIfEnabled):
    """Restore ECM configuration from an uploaded backup zip. Human-admin only.

    kgz3k / bead 6n76m: gated with ``RequireHumanAdminIfEnabled`` (NOT the plain
    ``RequireAdminIfEnabled``) so the static MCP service principal is rejected.
    Restore rewrites the settings blob wholesale via ``_restore_from_zip`` ->
    ``_merge_settings_preserving_redacted``, which would otherwise let the MCP
    key flip every admin-only field (and restore non-redacted credentials from a
    legacy ZIP) — bypassing the field-level gate ``_resolve_settings_admin``
    enforces on POST /api/settings.
    """
    logger.info("[BACKUP] Restore requested, filename=%s", file.filename)

    # Stream to a bounded 0600 temp file. UploadFile itself may spool to disk,
    # but reading it wholesale here created a second unbounded in-memory copy.
    tmp_path = await _stream_upload_to_temp(file, _DBAS_RESTORE_TMP_DIR)
    try:
        try:
            zf = zipfile.ZipFile(tmp_path, "r")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")

        with zf:
            manifest = _validate_backup_zip(zf)
            try:
                restored = _restore_from_zip(zf, manifest)
            finally:
                manifest.close()
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("[BACKUP] Failed to remove legacy restore temp file: %s", exc)

    logger.info("[BACKUP] Restore complete, %d files restored", len(restored))
    return {
        "status": "ok",
        "backup_version": manifest.get("version", "unknown"),
        "backup_date": manifest.get("created_at", "unknown"),
        "restored_files": restored,
        # Additive (bead …-gi4zn): names what a standard artifact could NOT
        # carry, which restored_files structurally cannot.
        "notices": _post_restore_account_notices(),
    }


# ---------------------------------------------------------------------------
# restore-initial identity gate (bead enhancedchannelmanager-lf29s)
#
# POST /api/backup/restore-initial has to be reachable before any credentials
# exist — that is its entire purpose — but it rewrites journal.db wholesale,
# and journal.db holds the ``users`` table. Its historical guard was
# ``settings.is_configured()``, which answers "does a Dispatcharr URL and
# credential exist?". That is a Dispatcharr-configuration question standing in
# for an authentication question, and the two are unrelated: any instance whose
# Dispatcharr connection was not yet configured accepted an anonymous ZIP that
# replaced every admin password hash and the tls/ directory.
#
# The gate below keys on INSTANCE STATE (does a user row exist?) rather than on
# ``auth_settings.setup_complete``. The user row is durable instance state and
# remains authoritative if configuration persistence is interrupted or
# damaged. The gate therefore cannot delegate to
# ``auth.dependencies.require_admin_if_enabled``, which intentionally
# short-circuits to "anonymous is fine" whenever setup_complete is False.
# ---------------------------------------------------------------------------

_INITIAL_RESTORE_DENIED_DETAIL = (
    "This instance already has an operator account. Sign in as an admin and use "
    "/api/backup/restore instead."
)


# The ownership predicate this gate keys on lives in ``auth.dependencies`` as
# :func:`instance_has_operator_identity` and is imported above. It was a
# private copy here until bead jy006 gave the same rule a second caller (the
# ``enforce_when_auth_disabled`` branch of ``require_admin_if_enabled``, which
# gates the mcp-api-key and TLS-material routes). Two copies of one fail-closed
# security predicate is the drift defect bead 9kwzp.9 is about, and these two
# in particular MUST agree: they are the same question ("is this instance
# owned?") asked by the two halves of the same auth-disabled posture.


async def _caller_is_human_admin(request: Request, session: Session) -> bool:
    """Resolve the caller to a human admin without consulting ``setup_complete``.

    Mirrors ``RequireHumanAdminIfEnabled`` (used by POST /api/backup/restore)
    in rejecting the static MCP service principal — restore rewrites the
    settings blob wholesale, so it must be driven by an operator (bead 6n76m).
    Any token that fails validation makes the caller anonymous, never admin.
    """
    if not get_token_from_request(request):
        return False

    try:
        user = await get_current_user(request, session)
    except HTTPException:
        return False

    return bool(user.is_admin) and not is_mcp_service_principal(user)


async def _guard_initial_restore(request: Request, session: Session) -> None:
    """Refuse the anonymous first-run restore once the instance has an owner.

    ``require_auth`` IS NOT CONSULTED HERE, and that is the bead jy006 fix.
    This guard used to return early — serving the anonymous restore — whenever
    the operator had turned authentication off, on the reasoning that
    ``RequireAdminIfEnabled`` already serves anonymous callers on such an
    instance so refusing only here closed nothing. The PO decided that question
    on 2026-08-13 the other way: ``require_auth: false`` stays open for
    ordinary data and configuration routes, but this route is one of three
    IDENTITY PRIMITIVES that stay admin-only in that mode, because the ZIP it
    accepts replaces ``journal.db`` wholesale — the ``users`` table and every
    admin password hash with it. An anonymous LAN caller who lands one on an
    auth-disabled instance owns the instance afterwards, including after the
    operator turns authentication back on. That is categorically unlike POST
    /api/settings, which is merely open while the mode is on.

    The genuine-first-run carve-out above is unchanged and is what keeps this
    from being a lockout; see :func:`auth.dependencies.instance_has_operator_identity`
    and the ``enforce_when_auth_disabled`` docstring in
    ``auth.dependencies.require_admin_if_enabled``, which applies the identical
    rule to the other two primitives.
    """
    if not instance_has_operator_identity(session):
        # Genuine first run — nothing exists to protect, and serving this case
        # is the endpoint's reason to exist (fresh container, or a
        # disaster-recovery rebuild sitting empty waiting for its restore).
        return

    if await _caller_is_human_admin(request, session):
        return

    logger.warning(
        "[BACKUP] Refused initial restore: instance already has an operator "
        "identity and the caller is not an authenticated admin"
    )
    raise HTTPException(status_code=403, detail=_INITIAL_RESTORE_DENIED_DETAIL)


@router.post("/restore-initial")
async def restore_backup_initial(
    request: Request,
    file: UploadFile = File(...),
    # COUPLED TO ``database.py``'s ``poolclass=StaticPool`` (bead …-9kwzp.5).
    # FastAPI holds this session — and its live SQLite read transaction — open
    # for the whole handler, which spans ``_restore_from_zip``'s ``close_db()``
    # -> ``JOURNAL_DB_FILE.write_bytes()`` -> ``init_db()`` sequence.
    # StaticPool's ``dispose()`` closes its single shared connection regardless
    # of checkout state, so the pre-restore WAL is gone before the new bytes
    # land. Under the DEFAULT QueuePool a checked-out connection SURVIVES
    # ``dispose()`` and its stale WAL replays over the restored database — the
    # lf29s security review reproduced a 200 + ``integrity_check=ok`` response
    # on an instance that had silently reverted to its pre-restore data.
    # This endpoint is therefore correct today because of a pooling choice made
    # elsewhere for unrelated reasons. If ECM ever moves off StaticPool, this
    # dependency must go first: resolve the identity gate with a short-lived
    # session opened and closed inside ``_guard_initial_restore`` instead of
    # one held across the file swap.
    # Pinned by ``tests/unit/test_9kwzp5_staticpool_restore_coupling.py``.
    session: Session = Depends(get_session),
):
    """Restore from backup during initial setup.

    Serves the first-run case, where no credentials exist yet. Refused once the
    instance is Dispatcharr-configured, and — independently of that — refused
    for anyone but an authenticated human admin once the instance holds an
    operator identity. See ``_guard_initial_restore`` and bead
    enhancedchannelmanager-lf29s.
    """
    settings = get_settings()
    if settings.is_configured():
        raise HTTPException(
            status_code=403,
            detail="App is already configured. Use /api/backup/restore instead.",
        )

    await _guard_initial_restore(request, session)

    logger.info("[BACKUP] Initial restore requested, filename=%s", file.filename)

    # Use the same bounded streaming path as authenticated and DBAS restores.
    tmp_path = await _stream_upload_to_temp(file, _DBAS_RESTORE_TMP_DIR)
    try:
        try:
            zf = zipfile.ZipFile(tmp_path, "r")
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid zip archive")

        with zf:
            manifest = _validate_backup_zip(zf)
            try:
                restored = _restore_from_zip(zf, manifest)
            finally:
                manifest.close()
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("[BACKUP] Failed to remove initial restore temp file: %s", exc)

    logger.info("[BACKUP] Initial restore complete, %d files restored", len(restored))
    return {
        "status": "ok",
        "backup_version": manifest.get("version", "unknown"),
        "backup_date": manifest.get("created_at", "unknown"),
        "restored_files": restored,
        # The disaster-recovery case this notice exists for (bead …-gi4zn).
        "notices": _post_restore_account_notices(),
    }


# ---------------------------------------------------------------------------
# DBAS async restore-trigger endpoint (bead enhancedchannelmanager-o8tbv)
#
# The new-format DBAS artifact restore — the async, progress-emitting path that
# makes restore user-triggerable. UNTRUSTED-ARTIFACT-UPLOAD surface:
#   * admin-auth only (RequireAdminIfEnabled, like every restore endpoint here),
#   * the upload is STREAMED to a temp file on the CONFIG partition one chunk at
#     a time (never read whole-in-RAM — ADR-008 D8), mode 0600,
#   * a hard size cap aborts + cleans up an oversize upload mid-stream,
#   * validation (.17 version + integrity) runs INSIDE the task BEFORE any
#     mutation, and the orchestrator's default-ON dry-run guardrail means APPLY
#     requires an explicit confirm flag.
# The endpoint kicks the DbasRestoreTask in the background and returns its
# task id immediately; the frontend polls /api/tasks/{id} for per-stage progress.
# ---------------------------------------------------------------------------

DBAS_RESTORE_TASK_ID = "dbas_restore"
_DBAS_RESTORE_TMP_DIR = CONFIG_DIR / "dbas" / "restore_uploads"


# ---------------------------------------------------------------------------
# bead 9kwzp.10 item 2 (PR #855 review) — the DBAS APPLY carve-out.
# ---------------------------------------------------------------------------
# The MCP service principal is refused the DBAS restore because bead …-dfkbn
# item 4 added ``dbas/importers/ecm_settings.py`` and the restore now writes
# ECM's own settings blob wholesale, which is the kgz3k bypass bead 6n76m
# closed on the three legacy /restore* endpoints. That reasoning covers the
# APPLY. It does not cover the PREVIEW, and the two DBAS routes are NOT
# symmetric, so they are gated differently on purpose:
#
# * ``POST /restore-dbas`` takes a caller-supplied UPLOAD. The artifact is
#   streamed to the config partition (up to 2 GiB) and decoded before anything
#   examines ``confirm_apply``, so even a dry run hands this principal a parser
#   and a disk-consumption surface with an artifact of its own choosing. The
#   sidecar exposes no tool for it either (there is no file upload over MCP),
#   so a blanket denial removes no capability. It keeps
#   ``RequireHumanAdminIfEnabled``.
#
# * ``POST /restore-dbas-saved`` names an artifact ALREADY on the server, which
#   only an admin could have put there (``POST /api/backup/save`` and the saved
#   listing are both admin-gated). There is no attacker-supplied artifact on
#   this path, and the sidecar's ``restore_dbas_backup_saved`` tool documents
#   ``confirm_apply=False`` as its primary safe mode: a counts-only preview.
#   ``dbas.restore_orchestrator`` forces ``report.is_dry_run = True`` whenever
#   ``confirm_apply`` is false and treats that as the single choke point a
#   caller can never opt out of, so the zero-mutation claim is structural
#   rather than a flag we trust. Refusing it would have been an unrelated,
#   undocumented capability removal, so the refusal is conditional instead.
#
# The conditional half is enforced in the HANDLER rather than in the route
# dependency, following the kgz3k precedent in ``routers/settings.py``
# (``_resolve_settings_admin`` resolves a bool; ``_assert_admin_for_changed_
# fields`` raises on the specific attempt). A dependency cannot see
# ``confirm_apply`` here without consuming the request body first.
# ``tests/test_admin_gate_inventory.py`` records the route under
# ``_DBAS_PREVIEW_ADMITTED_APPLY_DENIED_IN_HANDLER`` so the split is not
# invisible to the inventory.
_DBAS_APPLY_MCP_DENIAL = (
    "The MCP service principal cannot APPLY a DBAS restore. Applying rewrites "
    "ECM's own settings blob wholesale, including the outbound base URLs, the "
    "notification credentials and the outbound-policy mode, so it must be "
    "driven by a human operator admin. The counts-only preview "
    "(confirm_apply=false) remains available."
)


# The predicate itself is ``auth.ResolveIsMcpServicePrincipalIfEnabled``, a
# sibling of ``resolve_is_admin_if_enabled``. It lives in ``auth/dependencies``
# rather than here so its setup-mode early return is the SAME one the gate
# stacked above it makes; a private copy in this router would be one refactor
# away from drifting from the gate it qualifies.

# Age after which an abandoned restore temp is swept (O8TBV-4). The DbasRestoreTask
# normally deletes its own temp in a finally; this only catches temps orphaned
# when the fire-and-forget coroutine returns BEFORE execute() runs (task-not-found
# or an ALREADY_RUNNING concurrency reject — neither reaches the task's finally).
# A few hours is comfortably longer than the longest realistic restore, so the
# sweep never races a live run that still owns its temp.
_DBAS_RESTORE_TMP_MAX_AGE_SECONDS = 6 * 60 * 60  # 6 hours


def _sweep_stale_restore_temps(dest_dir: Path) -> None:
    """Best-effort removal of abandoned restore temp artifacts (O8TBV-4).

    The DbasRestoreTask owns teardown of its own temp in a ``finally`` block, so
    the common path leaves nothing behind. But the trigger endpoint schedules the
    task fire-and-forget via ``asyncio.create_task``; if that coroutine returns
    before ``execute()`` ever runs — ``run_task`` returns ``None`` (task id not
    registered) or an ``ALREADY_RUNNING`` result for a concurrent run — the task's
    ``finally`` never fires and the 0600 temp ZIP is orphaned. This sweep, run at
    the START of each restore trigger, removes temps older than
    :data:`_DBAS_RESTORE_TMP_MAX_AGE_SECONDS` so an orphan cannot accumulate.

    It never deletes a fresh temp (a live run still owns it — the age floor is far
    longer than any realistic restore) and never double-deletes (a finished task
    already unlinked its own). Any error is swallowed with a WARN — a sweep
    failure must never block a legitimate restore.
    """
    if not dest_dir.exists():
        return
    cutoff = time.time() - _DBAS_RESTORE_TMP_MAX_AGE_SECONDS
    removed = 0
    try:
        candidates = list(dest_dir.glob("ecm-restore-*.zip"))
    except OSError as exc:
        logger.warning("[BACKUP] Could not list restore temp dir for sweep: %s", exc)
        return
    for candidate in candidates:
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                removed += 1
        except FileNotFoundError:
            # Already gone (raced with a task's own finally) — fine.
            continue
        except OSError as exc:
            logger.warning(
                "[BACKUP] Failed to sweep stale restore temp %s: %s", candidate, exc
            )
    if removed:
        logger.info("[BACKUP] Swept %d stale restore temp artifact(s)", removed)


async def _stream_upload_to_temp(file: UploadFile, dest_dir: Path) -> Path:
    """Stream an uploaded artifact to a 0600 temp file, chunk by chunk.

    NEVER reads the whole upload into RAM (ADR-008 D8) — it copies
    ``_RESTORE_UPLOAD_CHUNK`` bytes at a time and enforces
    :data:`_RESTORE_MAX_UPLOAD_BYTES`, aborting + unlinking the partial temp the
    moment the cumulative size exceeds the cap (so an oversize upload can never
    fill the partition). The temp file is created mode 0600 (owner-only) because
    the artifact may carry credential-bearing material (journal.db) even though
    it is redacted-by-default.

    Returns the temp file path on success. Raises ``HTTPException(413)`` on
    oversize and ``HTTPException(400)`` on a read error — the partial temp is
    cleaned up in both cases.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="ecm-restore-", suffix=".zip", dir=str(dest_dir))
    tmp_path = Path(tmp_name)
    # Owner read/write only — the artifact may carry sensitive (if redacted) data.
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:  # pragma: no cover — platform without fchmod
        pass

    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                try:
                    chunk = await file.read(_RESTORE_UPLOAD_CHUNK)
                except Exception as exc:  # noqa: BLE001 - any read error is a 400
                    raise HTTPException(status_code=400, detail="Failed to read uploaded artifact") from exc
                if not chunk:
                    break
                total += len(chunk)
                if total > _RESTORE_MAX_UPLOAD_BYTES:
                    logger.warning(
                        "[BACKUP] Refusing restore: upload exceeded size cap (%d bytes max)",
                        _RESTORE_MAX_UPLOAD_BYTES,
                    )
                    raise HTTPException(
                        status_code=413, detail="Uploaded artifact is too large"
                    )
                out.write(chunk)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError as exc:
            logger.warning("[BACKUP] Failed to clean up partial restore upload: %s", exc)
        raise

    if total == 0:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Uploaded artifact is empty")

    logger.info("[BACKUP] Streamed restore artifact to temp (%d bytes)", total)
    return tmp_path


@router.post("/restore-dbas")
async def restore_dbas_artifact(
    file: UploadFile = File(...),
    confirm_apply: bool = Query(
        default=False,
        description="False (default) runs a counts-only dry-run; True runs the apply.",
    ),
    passphrase: Optional[str] = Form(
        default=None,
        description=(
            "Operator passphrase for an encrypted artifact (ADR-012 D12). Omit "
            "for a plain artifact. Sent as a form field, never a query string, "
            "so it does not land in access logs."
        ),
    ),
    channel_reattach_mode: str = Query(
        default=ChannelReattachMode.PRESERVE.value,
        description=(
            "What to do about channels this restore did not create. 'preserve' "
            "(default) leaves their EPG link and logo exactly as they are; "
            "'overwrite' applies the archive's. Anything unrecognised, including "
            "an absent value from an older client, resolves to 'preserve'."
        ),
    ),
    _admin=RequireHumanAdminIfEnabled,
):
    """Trigger an async DBAS artifact restore. Human-admin only.

    bead 9kwzp.10 item 2: moved off the PLAIN admin tier onto the same gate the
    three legacy ``/restore*`` endpoints have carried since bead 6n76m. The
    plain tier was CORRECT when it was written, and the history says so in
    three commits:

    * ``21f93e683`` (2026-06-19) shipped this endpoint with the plain admin
      tier. At that point a DBAS restore applied denylist-filtered settings to
      the DISPATCHARR upstream and never touched ECM's own ``settings.json``.
    * ``e83d31b1`` (2026-07-08, bead 6n76m) human-gated the legacy ``/restore``,
      ``/restore-saved`` and ``/restore-yaml`` paths, which already DID write
      ECM's settings blob, and deliberately left DBAS on the plain tier for
      exactly the distinction above.
    * ``fd63235d`` (2026-08-04, bead …-dfkbn item 4) added
      ``dbas/importers/ecm_settings.py``: a generic ``setattr`` loop over the
      archive's settings mapping followed by ``save_settings``. That is what
      made the distinction stale.

    The importer excludes only the live Dispatcharr connection, install-local
    bookkeeping and redaction sentinels — NOT ``emby_base_url`` /
    ``plex_base_url`` / ``jellyfin_base_url``, the notification credentials,
    the GH #473 safety caps or ``ssrf_outbound_mode``. So a caller supplying
    its own artifact could set every admin-only field the kgz3k field-level
    gate on POST /api/settings refuses it, which is the bypass 6n76m closed on
    the other three endpoints. The gate went stale when the capability grew
    rather than being wrong when it was written. It no-ops while
    ``require_auth`` is false or setup is incomplete, as it already did for the
    legacy trio.

    THE DENIAL HERE IS BLANKET, INCLUDING THE DRY RUN, and that is deliberate
    rather than an oversight: this route takes a caller-supplied UPLOAD, which
    is streamed to the config partition and decoded before anything examines
    ``confirm_apply``, so even a preview hands the principal a parser and a
    disk-consumption surface with an artifact of its own choosing. The sidecar
    exposes no tool for it (there is no file upload over MCP), so nothing is
    lost. Its saved-artifact sibling :func:`restore_dbas_saved` IS split by
    ``confirm_apply``, because there the artifact was already put on disk by an
    admin; see the comment above ``_DBAS_APPLY_MCP_DENIAL``.

    Streams the uploaded artifact to a temp file on the CONFIG partition, then
    kicks the :class:`tasks.dbas_restore.DbasRestoreTask` in the background and
    returns its ``task_id`` so the frontend can poll ``/api/tasks/{task_id}`` for
    per-stage progress and the terminal ``RestoreReport``.

    DRY-RUN is default-ON: without ``confirm_apply=True`` the run is a counts-only
    plan that makes ZERO mutation (the orchestrator's .16 guardrail enforces this
    even if this flag were bypassed). Validation (.17 version + integrity) runs
    inside the task BEFORE any decode or importer.
    """
    reattach_mode = ChannelReattachMode.coerce(channel_reattach_mode)
    logger.info(
        "[BACKUP] DBAS restore requested (filename=%s, confirm_apply=%s, "
        "channel_reattach_mode=%s)",
        file.filename, confirm_apply, reattach_mode.value,
    )

    # Sweep any temp orphaned by a previous fire-and-forget run that returned
    # before its task's finally could clean up (task-not-found / ALREADY_RUNNING).
    _sweep_stale_restore_temps(_DBAS_RESTORE_TMP_DIR)

    tmp_path = await _stream_upload_to_temp(file, _DBAS_RESTORE_TMP_DIR)

    # Configure + kick the restore task. The task owns temp-artifact teardown
    # (cleanup_artifact=True) so the file never outlives the run.
    parameters = {
        "artifact_path": str(tmp_path),
        "confirm_apply": bool(confirm_apply),
        "cleanup_artifact": True,
        "channel_reattach_mode": reattach_mode.value,
    }
    # Forward the passphrase only when present (encrypted artifact). The task
    # excludes it from get_config so it is never persisted or logged.
    if passphrase:
        parameters["passphrase"] = passphrase

    try:
        from task_engine import get_engine

        engine = get_engine()
        # Fire-and-forget: run_task awaits to completion, so schedule it as a
        # background asyncio task and return the task id immediately. The
        # frontend polls /api/tasks/{id} for live progress. The task's own
        # finally-block cleans up the temp artifact on success AND failure.
        asyncio.create_task(
            engine.run_task(DBAS_RESTORE_TASK_ID, parameters=parameters)
        )
    except Exception as exc:
        logger.exception("[BACKUP] Failed to schedule DBAS restore task: %s", exc)
        # Scheduling failed before the task could own cleanup — remove the temp.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="Failed to start restore")

    return {
        "status": "started",
        "task_id": DBAS_RESTORE_TASK_ID,
        "is_dry_run": not confirm_apply,
        "channel_reattach_mode": reattach_mode.value,
    }


def _gather_settings(include_credentials: bool = False) -> dict:
    """Read settings.json and return as dict (excluding sensitive fields).

    Redacts every name in :data:`_SETTINGS_CREDENTIAL_FIELDS` — the
    credential-class fields plus the GET /api/settings read-redaction partition
    (``config.ADMIN_ONLY_READ_REDACTED_FIELDS``), which is folded in there so a
    caller admitted by ``RequireAdminIfEnabled`` but classified non-admin by
    ``routers.settings._resolve_settings_admin`` — the MCP service principal —
    cannot read out of an artifact what the settings endpoint withholds
    (bead …-9kwzp.9).

    ``include_credentials`` (ADR-012 D12 / u81kh) preserves the settings-class
    credentials (SMTP password, API keys, bot tokens) instead of redacting them,
    for the opt-in passphrase-encrypted cred-carrying migration path. It is only
    ever True inside :func:`build_backup_artifact` when a passphrase is set; the
    review/portability YAML export path always redacts.

    THE NAME MASK IS NOT SUFFICIENT ON ITS OWN (bead …-04c0u.13 review). It
    matches FIELD NAMES, so a credential riding inside a URL VALUE under a name
    no denylist covers went out in clear: ``url`` (the Dispatcharr address, whose
    authority component accepts RFC 3986 userinfo), and equally ``emby_base_url`` /
    ``jellyfin_base_url`` / ``plex_base_url`` / ``public_base_url``, any of which
    an operator may have typed with a credential in it. :func:`build_yaml_export`
    already ran :func:`_redact_credentials_deep` — and therefore
    :func:`_scrub_credential_urls` — over its copy of this dict, so the LEGACY
    ZIP producer (:func:`_create_backup_zip`) was the only caller reaching an
    archive without it. Applying it here closes that one gap at the source rather
    than at one producer, and is idempotent for the YAML path.

    Restore is unaffected for the shape that matters:
    :func:`_scrub_credential_urls` returns the WHOLE-VALUE sentinel when the
    value IS the credential-bearing URL, which is the shape of every settings
    field above, and :func:`_merge_settings_preserving_redacted` skips exactly
    that value — so a restore keeps the working on-disk address instead of
    overwriting it with a half-URL. Pinned by
    ``tests/routers/test_04c0u13_backup_confidentiality.py::
    test_a_restore_preserves_the_working_url_the_producer_redacted``.
    """
    settings = get_settings()
    data = settings.model_dump()
    if not include_credentials:
        # Redact credentials — the export is for review/portability, not secret storage
        for key in _SETTINGS_CREDENTIAL_FIELDS:
            if key in data:
                data[key] = REDACTED
        for key, value in list(data.items()):
            if isinstance(value, str):
                scrubbed = _scrub_credential_urls(value)
                if scrubbed is not None:
                    data[key] = scrubbed
    return data


def _gather_db_tables() -> dict:
    """Export key DB tables as lists of dicts."""
    session = get_session()
    try:
        sections = {}

        # Scheduled tasks
        tasks = session.query(ScheduledTask).all()
        sections["scheduled_tasks"] = [t.to_dict() for t in tasks]

        # Task schedules
        schedules = session.query(TaskSchedule).all()
        sections["task_schedules"] = [
            {
                "task_id": s.task_id,
                "name": s.name,
                "enabled": s.enabled,
                "schedule_type": s.schedule_type,
                "interval_seconds": s.interval_seconds,
                "schedule_time": s.schedule_time,
                "timezone": s.timezone,
                "days_of_week": s.days_of_week,
                "day_of_month": s.day_of_month,
                "week_parity": s.week_parity,
                "parameters": json.loads(s.parameters) if s.parameters else None,
            }
            for s in schedules
        ]

        # Normalization rules
        groups = session.query(NormalizationRuleGroup).all()
        norm_groups = []
        for g in groups:
            rules = session.query(NormalizationRule).filter_by(group_id=g.id).order_by(NormalizationRule.priority).all()
            norm_groups.append({
                **g.to_dict(),
                "rules": [
                    {
                        "name": r.name,
                        "enabled": r.enabled,
                        "priority": r.priority,
                        "condition_type": r.condition_type,
                        "condition_value": r.condition_value,
                        "conditions": json.loads(r.conditions) if r.conditions else None,
                        "condition_logic": r.condition_logic,
                        "action_type": r.action_type,
                        "action_value": r.action_value,
                        "else_action_type": r.else_action_type,
                        "else_action_value": r.else_action_value,
                        "stop_processing": r.stop_processing,
                        "is_builtin": r.is_builtin,
                    }
                    for r in rules
                ],
            })
        sections["normalization_rule_groups"] = norm_groups

        # Tag groups
        tag_groups = session.query(TagGroup).all()
        tag_groups_out = []
        for tg in tag_groups:
            tags = session.query(Tag).filter_by(group_id=tg.id).all()
            tag_groups_out.append({
                **tg.to_dict(),
                "tags": [t.to_dict() for t in tags],
            })
        sections["tag_groups"] = tag_groups_out

        # Auto-creation rules
        ac_rules = session.query(ChannelPipelineRule).all()
        sections["auto_creation_rules"] = [r.to_dict() for r in ac_rules]

        # FFmpeg profiles
        profiles = session.query(FFmpegProfile).all()
        sections["ffmpeg_profiles"] = [p.to_dict() for p in profiles]

        # Dummy EPG profiles
        depg = session.query(DummyEPGProfile).all()
        depg_out = []
        for d in depg:
            assignments = session.query(DummyEPGChannelAssignment).filter_by(profile_id=d.id).all()
            depg_out.append({
                **d.to_dict(),
                "channel_assignments": [a.to_dict() for a in assignments],
            })
        sections["dummy_epg_profiles"] = depg_out

        return sections
    finally:
        session.close()


# Channel-list pagination cap for the channels producer. Dispatcharr's channel
# list is paginated; the producer walks every page so the backup carries the FULL
# channel set (a partial channel export would silently lose channels on restore).
_CHANNELS_PAGE_SIZE = 1000
_CHANNELS_MAX_PAGES = 1000  # hard stop so a misbehaving upstream cannot loop forever


def _safe_embedded_stream(stream: dict) -> dict:
    """Reduce a Dispatcharr stream record to the SAFE fields a channel embeds.

    The DBAS round-trip restore (``dbas/importers/channels.py``) matches each
    embedded stream against the destination's streams using the 4-tier matcher
    (``dbas/stream_matcher.py``): name + provider (``m3u_account``) on Tiers 2-4.
    Tier 1 (exact URL) is deliberately UNavailable here — a stream URL embeds
    provider credentials (``_STREAM_CREDENTIAL_FIELDS``) and is NEVER carried in
    the artifact (7i8rf redaction contract). We emit ONLY the stream id (for the
    operator-facing label / ordering) and the credential-free match fields. The
    non-bypassable deep redactor still runs over the result as defense in depth.
    """
    out: dict = {}
    sid = stream.get("id")
    if sid is not None:
        out["id"] = sid
    name = stream.get("name")
    if name is not None:
        out["name"] = name
    # ``m3u_account`` is an integer FK (the provider id), not a credential — it is
    # the matcher's "same provider" signal (Tier 2). Carried for match fidelity.
    if "m3u_account" in stream:
        out["m3u_account"] = stream.get("m3u_account")
    return out


# Whether the last EPG-data read inside THIS backup run came back at the row
# ceiling (PR review W2). A ContextVar rather than a threaded return value
# because the producer that learns it sits four call frames below
# :func:`build_backup_artifact` (gather -> sections -> yaml export -> categories),
# and widening four signatures to carry one bit is worse than an explicitly
# per-run ambient flag. asyncio gives each Task its own context, so two
# concurrent backups cannot read each other's value; ``build_backup_artifact``
# re-arms it to False on entry so a value never survives into a later run.
_EPG_INDEX_TRUNCATED: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ecm_backup_epg_index_truncated", default=False
)

# What the ``upcoming_recordings`` gather LEFT BEHIND on this run, by reason
# (bead …-ciabe). Same ContextVar mechanism, and the same justification, as
# ``_EPG_INDEX_TRUNCATED`` above: the producer that learns it sits four call
# frames below :func:`build_backup_artifact`, asyncio gives each Task its own
# context so two concurrent backups cannot read each other's value, and
# ``build_backup_artifact`` re-arms it on entry so a value never survives into a
# later run.
#
# WHY A CENSUS AND NOT A BOOLEAN. ADR-013's principle is that every exclusion is
# "named, individually justified and VISIBLE". A disclaimer that prints on every
# run whether or not anything was excluded is wallpaper — the operator stops
# reading it, which is the same end state as not printing it. A COUNT is the
# difference between "some things are not backed up" and "4 finished recordings
# were not backed up, and here is what to do about them".
#
# ``None`` (the default) means the category was never gathered on this run and is
# reported as nothing at all — distinct from a gather that ran and excluded zero,
# which is the operator's cue that the category is clean.
_RECORDINGS_EXCLUDED: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "ecm_backup_recordings_excluded", default=None
)

# Where a recurring rule stamps its own id on the recordings it generates
# (Dispatcharr ``apps/channels/tasks.py`` -> ``sync_recurring_rule_impl``:
# ``custom_properties = {"rule": {"type": "recurring", "id": rule.id, ...}, ...}``).
_RECORDING_RULE_KEY = "rule"


def _recording_is_regenerated_by_a_rule(record: dict) -> bool:
    """True when a recurring rule OWNS this recording and will recreate it.

    Dispatcharr's ``maintain_recurring_recordings`` beat runs hourly on every
    instance (``dispatcharr/settings.py`` schedules it at 3600s) and, for each
    enabled ``RecurringRecordingRule``, materializes the next 14 days of
    recordings that do not already exist. So a rule-generated recording is not
    independent state: it is OUTPUT of the ``dvr_rules`` category, and restoring
    the rule is what restores it.

    Archiving it as well would apply the same state twice — the identical
    reasoning that already keeps SERIES rules out of ``dvr_rules`` (they live
    inside the ``dvr_settings`` core setting ``core_settings`` carries). Here it
    is worse than redundant, because the two copies would not merge:

    * the maintainer de-duplicates on ``custom_properties__rule__id`` against the
      DESTINATION rule's id, which an archived row cannot carry; and
    * it recomputes each ``start_time`` from the rule's naive ``start_time``
      field in the DESTINATION's own timezone, so a replica in a different zone
      computes a different absolute instant than the one archived.

    Either mismatch alone turns "restore the rule and its recordings" into two
    recordings per occurrence. Carrying only the rule cannot produce that.
    """
    props = record.get("custom_properties")
    if not isinstance(props, dict):
        return False
    rule = props.get(_RECORDING_RULE_KEY)
    if isinstance(rule, dict):
        return as_int(rule.get("id")) is not None
    return as_int(rule) is not None


def _partition_upcoming_recordings(rows) -> tuple[list[dict], dict[str, int]]:
    """Split Dispatcharr's recordings into the portable ones and a census of the rest.

    THE FILTER, and why it is ``start_time > now`` and nothing else. Dispatcharr's
    ``Recording`` model has NO status column (measured on 0.29.0 — the whole
    serializer is ``{id, start_time, end_time, task_id, custom_properties,
    channel}``). ``custom_properties["status"]`` exists but is a free-form
    JSONField key the DVR pipeline writes and is absent on a manually created
    row, so it cannot be the discriminator. The absolute ``start_time`` is the
    only always-present one — and it is the same predicate Dispatcharr itself
    uses to mean "upcoming" in ``BulkDeleteUpcomingRecordingsAPIView``
    (``Recording.objects.filter(start_time__gt=now)``).

    An IN-PROGRESS recording is therefore excluded alongside the finished ones,
    and deliberately: it is already writing a file on the source's disk, and
    scheduling it on a replica would start a partial capture of a programme
    that is half over.

    Returns:
        ``(upcoming, census)``. ``census`` counts what was left behind, keyed by
        the reason, and is what the run report turns into an operator-facing
        line. Its keys are stable — they are read by ``tasks.dbas_backup``.
    """
    now = datetime.now(timezone.utc)
    upcoming: list[dict] = []
    census = {"already_started": 0, "regenerated_by_a_rule": 0, "unreadable_schedule": 0}
    for record in rows or []:
        if not isinstance(record, dict):
            census["unreadable_schedule"] += 1
            continue
        start = as_instant(record.get("start_time"))
        if start is None:
            # Fail-safe. A row ECM cannot place in time is not PROVEN upcoming,
            # and the cost of guessing wrong is a phantom recording on the
            # replica — worse than the missing one, because it records.
            census["unreadable_schedule"] += 1
            continue
        if start <= now:
            census["already_started"] += 1
            continue
        if _recording_is_regenerated_by_a_rule(record):
            census["regenerated_by_a_rule"] += 1
            continue
        upcoming.append(record)
    return upcoming, census


def _epg_link_id(channel: dict) -> int | None:
    """The channel's ``epg_data_id`` as an int, or ``None`` when it has no link.

    Coercion is :func:`dbas.archive_keys.as_int`, the SAME rule the restore side
    matches with (PR review W3). It used to be a second, character-for-character
    copy of that rule living here; two implementations of one rule is exactly the
    producer/consumer disagreement this bead exists to fix, because hardening one
    copy would make the consumer count a miss the producer refused to stamp.
    """
    return as_int(channel.get("epg_data_id"))


async def _resolve_epg_link_natural_keys(
    client, channels: list[dict], *, allow_truncated: bool = True
) -> int:
    """Stamp each EPG-linked channel with the tvg_id of the row it points at.

    Bead ``enhancedchannelmanager-dfkbn``, drill run 2026-08-04-run2. ``epg_data_id``
    is a SOURCE row id that cannot round-trip (the destination re-downloads its
    own guide and mints new ids), so ``dbas/channel_reattach.py`` correctly
    relinks by ``tvg_id`` instead. But the channel's OWN ``tvg_id`` field is not
    the link, and it is routinely null on a linked channel: ECM's own channel
    PATCH sets ``epg_data_id`` and leaves ``tvg_id`` alone. All 7 of the drill's
    linked channels therefore reached the restore with nothing to match on, and
    every link was dropped. The link's natural key lives on the EPG ROW, and this is where
    it is still readable: at backup time, against the source instance.

    ONE bounded fetch for the whole export builds an ``epg_data_id -> tvg_id``
    index (a real guide is tens of thousands of rows; the drill's was 14,668), so
    the cost does not scale with the channel count. Mutates ``channels`` in place,
    adding :data:`ARCHIVE_EPG_TVG_ID_KEY` ONLY where the lookup resolved. The
    channel's own ``tvg_id`` is its own field and is never overwritten.

    Fails soft in every direction: an unreachable EPG endpoint, an unindexed row,
    or a blank tvg_id leaves the channel exactly as it was, which degrades to the
    pre-fix behaviour (an unrestored link that the restore report COUNTS and
    NAMES in ``epg_link_miss_details``). A backup must never fail over this.

    Args:
        client: The Dispatcharr API client.
        channels: The gathered channel records. Mutated in place.
        allow_truncated: Preserve legacy backup behavior when the bounded guide
            inventory hits its ceiling. Live sync passes ``False`` because an
            incomplete source inventory cannot prove link provenance safely.

    Returns:
        The number of channels whose link natural key was resolved.
    """
    linked = [ch for ch in channels if _epg_link_id(ch) is not None]
    if not linked:
        return 0

    try:
        rows = await client.get_epg_data(max_results=EPG_INDEX_MAX_ROWS)
    except Exception as e:  # noqa: BLE001 - one fetch must never fail a backup
        # Type only: an httpx error's text embeds the full upstream URL.
        logger.warning(
            "[BACKUP] Could not list EPG data to resolve %d channel EPG link(s); "
            "they are archived without their guide natural key: %s",
            len(linked), type(e).__name__,
        )
        return 0

    # A fetch that came back at EXACTLY the ceiling was almost certainly cut
    # short: this is a bounded read, and a guide that happens to hold precisely
    # 200,000 rows is far less likely than one that holds more. The two causes of
    # an unresolved link look identical in the artifact, so name TRUNCATION here
    # rather than let the operator read a truncated index as 200,000 dangling
    # references (PR review W2).
    truncated = len(rows or []) >= EPG_INDEX_MAX_ROWS
    if truncated:
        _EPG_INDEX_TRUNCATED.set(True)
        logger.warning(
            "[BACKUP] The source guide hit the %d-row read ceiling, so its "
            "tvg_id index may be INCOMPLETE. Any EPG link reported unresolved "
            "below may be a truncation artifact, not a dangling reference.",
            EPG_INDEX_MAX_ROWS,
        )
        if not allow_truncated:
            return 0

    index: dict[int, str] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row_id = as_int(row.get("id"))
        tvg_id = row.get("tvg_id")
        if row_id is None:
            continue
        if not isinstance(tvg_id, str) or not tvg_id.strip():
            continue
        index[row_id] = tvg_id.strip()

    resolved = 0
    for channel in linked:
        tvg_id = index.get(_epg_link_id(channel))
        if tvg_id is None:
            continue
        channel[ARCHIVE_EPG_TVG_ID_KEY] = tvg_id
        resolved += 1

    if resolved < len(linked):
        logger.warning(
            "[BACKUP] %d of %d EPG-linked channel(s) had no resolvable guide row; "
            "their links will be reported unrestored on restore.",
            len(linked) - resolved, len(linked),
        )
    logger.info(
        "[BACKUP] Resolved the EPG natural key for %d channel(s).", resolved
    )
    return resolved


async def _gather_channels_with_streams(client) -> list[dict]:
    """Fetch every channel with its embedded streams reduced to SAFE fields.

    A Dispatcharr channel's ``streams`` field is a list of stream IDs. For the
    round-trip restore matcher to do better than a blind custom-stream synthesis,
    each embedded stream is enriched to ``{id, name, m3u_account}`` (NEVER the
    URL — see :func:`_safe_embedded_stream`) by joining against the stream records
    fetched once for the whole export. A channel whose streams cannot be enriched
    still carries its ordered ``[{id}, ...]`` so ordering and count survive.

    Each EPG-LINKED channel is additionally stamped with the natural key of the
    guide row it points at (:func:`_resolve_epg_link_natural_keys`, bead
    ``…-dfkbn``), which is the only form of the link that survives a restore.
    """
    # 1) Walk all channel pages.
    channels: list[dict] = []
    page = 1
    while page <= _CHANNELS_MAX_PAGES:
        resp = await client.get_channels(page=page, page_size=_CHANNELS_PAGE_SIZE)
        if isinstance(resp, dict):
            results = resp.get("results", []) or []
            channels.extend(r for r in results if isinstance(r, dict))
            if not resp.get("next"):
                break
        elif isinstance(resp, list):
            channels.extend(r for r in resp if isinstance(r, dict))
            break
        else:
            break
        page += 1

    if not channels:
        return []

    # 2) Build a stream-id -> safe-record index from the full stream list (one
    #    paginated walk; the matcher only needs name + provider).
    stream_index: dict = {}
    spage = 1
    while spage <= _CHANNELS_MAX_PAGES:
        sresp = await client.get_streams(page=spage, page_size=_CHANNELS_PAGE_SIZE)
        if isinstance(sresp, dict):
            sresults = sresp.get("results", []) or []
        elif isinstance(sresp, list):
            sresults = sresp
        else:
            sresults = []
        for s in sresults:
            if isinstance(s, dict) and s.get("id") is not None:
                stream_index[s["id"]] = _safe_embedded_stream(s)
        if not (isinstance(sresp, dict) and sresp.get("next")):
            break
        spage += 1

    # 3) Replace each channel's stream-id list with the enriched safe records,
    #    preserving order. An id absent from the index degrades to {"id": id}.
    enriched: list[dict] = []
    for ch in channels:
        out = dict(ch)
        raw_streams = ch.get("streams")
        if isinstance(raw_streams, list):
            embedded = []
            for sid in raw_streams:
                if isinstance(sid, dict):
                    # Already an object (some endpoints embed); reduce to safe.
                    embedded.append(_safe_embedded_stream(sid))
                else:
                    embedded.append(stream_index.get(sid, {"id": sid}))
            out["streams"] = embedded
        enriched.append(out)

    # 4) Resolve each EPG link to its natural key. Best-effort by contract: a
    #    failure here must not cost the operator the CHANNELS section, which is
    #    what raising into the caller's per-section try/except would do.
    try:
        await _resolve_epg_link_natural_keys(client, enriched)
    except Exception as e:  # noqa: BLE001 - never fail the channels gather
        logger.warning(
            "[BACKUP] Could not resolve channel EPG natural keys: %s",
            type(e).__name__,
        )
    return enriched


# Core-settings keys whose lower-cased name starts with this prefix belong to
# the ``comskip`` artifact section, not ``core_settings``. Comskip CONFIG VALUES
# live in the same GET /api/core/settings/ namespace the settings importer
# PATCHes per-key (see dispatcharr_client.get_core_settings /
# update_core_setting), so the producer fetches once and SPLITS by this prefix.
# The split is disjoint — no key can be applied twice on restore.
#
# CORRECTION (enhancedchannelmanager-lsa0s): this note used to claim Dispatcharr
# has NO separate comskip endpoint. It does — ``/api/channels/dvr/comskip-config/``
# — but that endpoint is not a backup source and does not change the split above:
# its GET returns only ``{"path", "exists"}`` (never the comskip.ini CONTENT) and
# its POST takes a multipart ``.ini`` upload, so there is nothing to export from
# it and nothing to restore into it. Both facts are pinned by
# ``tests/fixtures/dispatcharr_dvr_recurring_rules_recorded.json``.
_COMSKIP_KEY_PREFIX = "comskip"


def _normalize_core_settings(raw) -> dict:
    """Normalize the GET /api/core/settings/ payload into a flat key->value map.

    Dispatcharr serializes core settings either as a mapping or as a list of
    ``{key|name, value}`` records; the client deliberately returns the raw
    payload and callers normalize (see ``dispatcharr_client.get_core_settings``).
    Rows without a usable string key are dropped rather than guessed at.
    """
    if isinstance(raw, dict):
        return dict(raw)
    out: dict = {}
    if isinstance(raw, list):
        for row in raw:
            if not isinstance(row, dict):
                continue
            key = row.get("key")
            if not isinstance(key, str) or not key:
                key = row.get("name")
            if isinstance(key, str) and key:
                out[key] = row.get("value")
    return out


def _split_comskip_settings(settings: dict) -> tuple[dict, dict]:
    """Split a normalized core-settings map into (core_settings, comskip) blobs.

    A key whose lower-cased name starts with :data:`_COMSKIP_KEY_PREFIX` goes to
    the comskip blob; everything else stays in core_settings. Disjoint by
    construction, preserving iteration order within each blob.
    """
    core: dict = {}
    comskip: dict = {}
    for key, value in settings.items():
        if isinstance(key, str) and key.lower().startswith(_COMSKIP_KEY_PREFIX):
            comskip[key] = value
        else:
            core[key] = value
    return core, comskip


def _redact_marked_setting_values(blob: dict) -> dict:
    """Redact the VALUE of any setting whose KEY the restore importer denylists.

    Mirrors the importer-side conservative denylist (lc6zu): the settings
    importer (``dbas.importers.settings_agents``) unconditionally SKIPS any key
    failing :func:`is_safe_setting_key` — the SAME predicate imported here, so
    the two sides can never drift. Because such a key is never applied on
    restore, carrying its real value in the artifact is pure leak risk with
    zero utility: the value is replaced with the REDACTED sentinel ALWAYS, even
    on a cred-carrying (``include_credentials``) migration artifact. The key
    NAME survives so the restore report can still surface the skip by name.
    Falsy None/"" values are preserved (same rule as the deep redactor) so
    "unset" stays distinguishable.
    """
    out: dict = {}
    for key, value in blob.items():
        if isinstance(key, str) and not is_safe_setting_key(key):
            out[key] = REDACTED if value not in (None, "") else value
        else:
            out[key] = value
    return out


def _degraded_section(key: str, exc: Exception) -> dict:
    """Build a per-section ``{"_warning": ...}`` stub for one failed fetch.

    A fresh dict per call (never a shared/reused reference) — two degraded
    sections in the same gather (e.g. ``core_settings`` + ``comskip`` sharing
    one upstream call) must not alias the same mutable object.
    """
    return {"_warning": "Failed to fetch %s — %s" % (key, exc)}


async def _gather_dispatcharr_sections(selected: set[str]) -> dict:
    """Fetch full Dispatcharr data for selected sections.

    Returns a dict keyed by section name with full data suitable for restore.
    Only fetches sections that are in the selected set.

    Isolation contract (routed finding from lsa0s, enhancedchannelmanager-zt3kf):
    each requested section's upstream fetch is wrapped in ITS OWN try/except —
    a failure fetching one section (e.g. ``get_epg_sources()`` raises) degrades
    ONLY that section's value to ``{"_warning": ...}`` and never prevents the
    OTHER sections requested in the SAME call from being fetched normally. This
    is an explicit per-fetch contract, not an accident of a caller happening to
    invoke this function once per category — the artifact builder
    (:func:`_gather_redacted_categories`) does call it that way today, but a
    direct multi-section call (``build_yaml_export({"epg_sources",
    "m3u_accounts"})`` via the legacy ``/export?sections=...`` path) gets the
    SAME per-section isolation, not a shared blast radius.

    The one case that is deliberately NOT per-section is total client
    unavailability: when ``get_client()`` returns falsy (or raises) BEFORE any
    per-section fetch is attempted, every requested section is equally
    unreachable — there is nothing section-specific to isolate — so the whole
    Dispatcharr-managed blob degrades together under a single top-level
    ``{"_warning": ...}`` (the long-standing shape callers already handle).
    """
    dispatcharr_keys = {k for k, v in RESTORABLE_SECTIONS.items() if v.get("dispatcharr")}
    needed = selected & dispatcharr_keys
    if not needed:
        return {}

    try:
        client = get_client()
    except Exception as e:
        logger.warning("[BACKUP] Failed to obtain Dispatcharr client: %s", e)
        return {"_warning": "Dispatcharr not connected — %s" % str(e)}

    if not client:
        return {"_warning": "Dispatcharr not connected — Dispatcharr sections skipped"}

    result: dict = {}

    if "m3u_accounts" in needed:
        try:
            accounts = await client.get_m3u_accounts()
            result["m3u_accounts"] = accounts or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch m3u_accounts: %s", e)
            result["m3u_accounts"] = _degraded_section("m3u_accounts", e)

    if "epg_sources" in needed:
        try:
            sources = await client.get_epg_sources()
            result["epg_sources"] = sources or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch epg_sources: %s", e)
            result["epg_sources"] = _degraded_section("epg_sources", e)

    if "channel_groups" in needed:
        try:
            groups = await client.get_channel_groups()
            result["channel_groups"] = groups or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch channel_groups: %s", e)
            result["channel_groups"] = _degraded_section("channel_groups", e)

    if "channel_profiles" in needed:
        try:
            profiles = await client.get_channel_profiles()
            result["channel_profiles"] = profiles or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch channel_profiles: %s", e)
            result["channel_profiles"] = _degraded_section("channel_profiles", e)

    if "stream_profiles" in needed:
        try:
            profiles = await client.get_stream_profiles()
            result["stream_profiles"] = profiles or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch stream_profiles: %s", e)
            result["stream_profiles"] = _degraded_section("stream_profiles", e)

    if "channels" in needed:
        # Channels carry embedded streams reduced to credential-free match
        # fields (7i8rf). This is the producer the restore channels importer
        # (dbas/importers/channels.py) consumes.
        try:
            result["channels"] = await _gather_channels_with_streams(client)
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch channels: %s", e)
            result["channels"] = _degraded_section("channels", e)

    if "logos" in needed:
        # dfkbn item 1 — the logo INVENTORY (id + name + url), not the bytes.
        # This is what lets a remotely-hosted logo round-trip at all: the
        # binary subtree carries only ECM's own uploads dir, which is empty on a
        # real install because ECM's Logo Manager writes into Dispatcharr's
        # volume. Logo records are ``{id, name, url}`` — no credential class —
        # and the deep redactor still runs over them as defense in depth.
        try:
            logos = await client.get_all_logos_paginated()
            result["logos"] = logos or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch logos: %s", e)
            result["logos"] = _degraded_section("logos", e)

    if "dispatcharr_users" in needed:
        # Dispatcharr user accounts (Django auth). A GET never returns a
        # password/hash (see dbas/importers/users.py policy 1); the deep
        # redactor scrubs any credential-class field as a backstop.
        try:
            users = await client.get_users()
            result["dispatcharr_users"] = users or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch dispatcharr_users: %s", e)
            result["dispatcharr_users"] = _degraded_section("dispatcharr_users", e)

    # lc6zu — the settings/agents producer set consumed by the Phase-2
    # settings_agents importer. User agents and DVR rules (Dispatcharr
    # recurring recording rules — lsa0s) are benign entity lists; the deep
    # redactor still runs over them as defense in depth.
    if "user_agents" in needed:
        try:
            agents = await client.get_user_agents()
            result["user_agents"] = agents or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch user_agents: %s", e)
            result["user_agents"] = _degraded_section("user_agents", e)

    if "dvr_rules" in needed:
        try:
            rules = await client.get_dvr_rules()
            result["dvr_rules"] = rules or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch dvr_rules: %s", e)
            result["dvr_rules"] = _degraded_section("dvr_rules", e)

    if "server_groups" in needed:
        # tyrg1 — a bare {id, name} list; no credential class at any depth. The
        # deep redactor still runs over it as defense in depth.
        try:
            groups = await client.get_server_groups()
            result["server_groups"] = groups or []
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch server_groups: %s", e)
            result["server_groups"] = _degraded_section("server_groups", e)

    if "upcoming_recordings" in needed:
        # …-ciabe. The ONE fetch returns every recording instance; the split into
        # what replicates and what cannot is ECM's, and the half left behind is
        # counted rather than silently dropped (see _RECORDINGS_EXCLUDED).
        try:
            recordings = await client.get_recordings()
            upcoming, census = _partition_upcoming_recordings(recordings)
            result["upcoming_recordings"] = upcoming
            _RECORDINGS_EXCLUDED.set(census)
            logger.info(
                "[BACKUP] Archived %d upcoming recording(s); left behind "
                "%d already started, %d regenerated by a recurring rule, "
                "%d with an unreadable schedule.",
                len(upcoming),
                census["already_started"],
                census["regenerated_by_a_rule"],
                census["unreadable_schedule"],
            )
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch upcoming_recordings: %s", e)
            result["upcoming_recordings"] = _degraded_section("upcoming_recordings", e)

    if "core_settings" in needed or "comskip" in needed:
        # ONE fetch backs both sections (no comskip endpoint exists — see
        # _COMSKIP_KEY_PREFIX). Dangerous-marked setting VALUES are redacted
        # here at the gather chokepoint so every downstream serialization
        # (artifact category YAML, explicit ?sections= export) is covered. A
        # failure of this ONE shared fetch degrades BOTH requested sections
        # (they have no independent upstream call to isolate between) — each
        # gets its OWN stub dict, never a shared/aliased reference.
        try:
            raw_settings = await client.get_core_settings()
            core_blob, comskip_blob = _split_comskip_settings(
                _normalize_core_settings(raw_settings)
            )
            if "core_settings" in needed:
                result["core_settings"] = _redact_marked_setting_values(core_blob)
            if "comskip" in needed:
                result["comskip"] = _redact_marked_setting_values(comskip_blob)
        except Exception as e:
            logger.warning("[BACKUP] Failed to fetch core settings: %s", e)
            if "core_settings" in needed:
                result["core_settings"] = _degraded_section("core_settings", e)
            if "comskip" in needed:
                result["comskip"] = _degraded_section("comskip", e)

    return result


async def build_yaml_export(
    sections: Optional[set[str]] = None,
    include_credentials: bool = False,
    exempt_identity_keys: frozenset = frozenset(),
) -> str:
    """Build a YAML export string, optionally limited to specific sections.

    If sections is None, all sections are included. Otherwise only the
    specified section keys (from RESTORABLE_SECTIONS) are included.

    ``include_credentials`` (ADR-012 D12 / u81kh) flows down to
    :func:`_gather_settings` to preserve settings-class creds for the opt-in
    passphrase-encrypted migration path; it is only ever True from
    :func:`build_backup_artifact`. The user-facing ``/export`` endpoint never
    sets it (always redacts).

    WHY THE DEEP REDACTOR RUNS HERE and not only in the artifact builder (bead
    …-gi4zn). This function is the shared gather for TWO operator-shareable
    artifacts: the DBAS artifact (via :func:`_gather_redacted_categories`) and
    the legacy ``GET /api/backup/export`` YAML. Only the first passed its
    payload through :func:`_redact_credentials_deep`, so the second scrubbed
    settings-class fields and nothing else — a Dispatcharr-sourced M3U or EPG
    record went out with whatever the upstream returned. Redacting at the gather
    makes ONE authority cover both surfaces; the artifact builder still runs the
    deep redactor afterwards (idempotent: a sentinel re-redacts to itself) so its
    NON-BYPASSABLE-stage property is unchanged even if a future caller reaches
    the gather another way.

    ``exempt_identity_keys`` names the provider-identity keys this particular
    gather must NOT redact — see :data:`_IDENTITY_EXEMPT_CATEGORIES`. Defaulting
    to empty means a caller that says nothing gets full redaction.

    The default set (``sections=None``) excludes ``artifact_only`` categories
    (channels / dispatcharr_users — 7i8rf): those are restorable only via the
    DBAS artifact path, not the legacy YAML export/restore, so the user-facing
    full YAML export keeps its pre-7i8rf shape. The artifact builder
    (:func:`_gather_redacted_categories`) requests each category by its EXPLICIT
    key, so it still emits the artifact_only producers.
    """
    legacy_keys = {
        k for k, v in RESTORABLE_SECTIONS.items() if not v.get("artifact_only")
    }
    selected = sections if sections else legacy_keys

    export_data: dict = {
        "ecm_export": {
            "version": APP_VERSION,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "sections_included": sorted(selected),
        },
    }

    if "settings" in selected:
        export_data["settings"] = _gather_settings(include_credentials=include_credentials)

    # ECM database sections
    db_sections = _gather_db_tables()
    filtered_db = {k: v for k, v in db_sections.items() if k in selected}
    if filtered_db:
        export_data["database"] = filtered_db

    # Dispatcharr-managed sections
    dispatcharr_data = await _gather_dispatcharr_sections(selected)
    if dispatcharr_data:
        export_data["dispatcharr"] = dispatcharr_data

    if not include_credentials:
        export_data = _redact_credentials_deep(
            export_data, exempt_identity_keys=exempt_identity_keys
        )

    return yaml.dump(export_data, default_flow_style=False, sort_keys=False, allow_unicode=True)


@router.get("/export-sections")
async def get_export_sections(_admin=RequireAdminIfEnabled):
    """Return available section keys and labels for selective export.

    ``artifact_only`` categories (channels / dispatcharr_users — 7i8rf) are
    omitted: they are restorable only through the DBAS artifact path, not the
    legacy per-section YAML restore this list drives.
    """
    return [
        {"key": key, "label": info["label"]}
        for key, info in RESTORABLE_SECTIONS.items()
        if not info.get("artifact_only")
    ]


@router.get("/export")
async def export_yaml(
    sections: Optional[str] = Query(None, description="Comma-separated section keys to include"),
    _admin=RequireAdminIfEnabled,
):
    """Export ECM configuration as a YAML file download.

    Optionally pass ?sections=settings,tag_groups,... to include only
    specific sections. If omitted, all sections are exported.
    """
    logger.info("[BACKUP] YAML export requested, sections=%s", sections)

    selected = None
    if sections:
        selected = {s.strip() for s in sections.split(",") if s.strip()}
        invalid = selected - set(RESTORABLE_SECTIONS.keys())
        if invalid:
            raise HTTPException(status_code=400, detail="Unknown sections: %s" % ", ".join(sorted(invalid)))

    yaml_str = await build_yaml_export(selected)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    filename = f"ecm-export-{now}.yaml"

    logger.info("[BACKUP] YAML export complete, %d bytes", len(yaml_str))
    return PlainTextResponse(
        content=yaml_str,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# YAML Validate & Selective Restore
# ---------------------------------------------------------------------------

# Sections that can be selectively restored from a YAML export.
# Keys map to the YAML structure paths; "db_key" is the key under "database".
RESTORABLE_SECTIONS = {
    "settings": {"label": "Settings"},
    "scheduled_tasks": {"label": "Task Settings & Alerts", "db_key": "scheduled_tasks"},
    "task_schedules": {"label": "Task Run Schedules", "db_key": "task_schedules"},
    "normalization_rule_groups": {"label": "Normalization Rules", "db_key": "normalization_rule_groups"},
    "tag_groups": {"label": "Tag Groups", "db_key": "tag_groups"},
    "auto_creation_rules": {"label": "Auto-Creation Rules", "db_key": "auto_creation_rules"},
    "ffmpeg_profiles": {"label": "FFmpeg Profiles", "db_key": "ffmpeg_profiles"},
    "dummy_epg_profiles": {"label": "Dummy EPG Profiles", "db_key": "dummy_epg_profiles"},
    # Dispatcharr-managed sections (restored via Dispatcharr API)
    "m3u_accounts": {"label": "M3U Accounts", "dispatcharr": True},
    "epg_sources": {"label": "EPG Sources", "dispatcharr": True},
    "channel_groups": {"label": "Channel Groups", "dispatcharr": True},
    "channel_profiles": {"label": "Channel Profiles", "dispatcharr": True},
    "stream_profiles": {"label": "Stream Profiles", "dispatcharr": True},
    # 7i8rf — the v0.18.0 round-trip producers. The restore importers
    # (dbas/importers/channels.py + users.py) existed but the builder did not
    # emit these categories, so restoring channels/users was a no-op against a
    # real backup. ``channels`` carries embedded streams reduced to
    # credential-free match fields (id + name + m3u_account, NEVER the URL).
    # ``dispatcharr_users`` is the Dispatcharr (Django) user category — distinct
    # from ECM's own users; a GET never returns a password/hash.
    #
    # ``artifact_only`` (7i8rf): these categories are PRODUCED into the DBAS
    # artifact (consumed by the Phase-2 restore importers via
    # decode_artifact_to_plan -> orchestrator) but are NOT restorable through the
    # LEGACY per-section YAML restore endpoint (/restore-yaml), which has no
    # channel/user restorer. They are therefore hidden from the legacy
    # export-sections / validate UI so an operator cannot select a section the
    # legacy path cannot apply. The artifact builder still emits them (the gather
    # pipeline iterates every RESTORABLE_SECTIONS key).
    "channels": {"label": "Channels", "dispatcharr": True, "artifact_only": True},
    "dispatcharr_users": {
        "label": "Dispatcharr Users", "dispatcharr": True, "artifact_only": True,
    },
    # dfkbn item 1: the Dispatcharr LOGO INVENTORY (id + name + url). This is the
    # METADATA half of the logo round-trip; the BYTES half is the
    # ``binary/logos`` subtree (see :func:`_gather_dispatcharr_logo_payloads`).
    # A logo whose ``url`` is an absolute http(s) CDN address restores from this
    # inventory alone: Dispatcharr's Logo model is exactly ``{name, url}``
    # (0.28.2 apps/channels/models.py), so re-creating the row restores the image
    # byte-identically and archiving the bytes would be waste. A logo whose
    # ``url`` is a Dispatcharr-LOCAL path (``/data/logos/x.png``, which is what
    # ECM's own Logo Manager writes) needs its bytes archived too, and the
    # builder fetches them from Dispatcharr at gather time (bead …-xb58a).
    # ``artifact_only`` for the same reason as its neighbours.
    "logos": {"label": "Logos", "dispatcharr": True, "artifact_only": True},
    # lc6zu — the settings/agents producer set completing coverage of all 12
    # categories in the v0.18 scope (plugins remain excluded per ADR-012
    # D10). Same ``artifact_only`` rationale as channels /
    # dispatcharr_users: produced into the DBAS artifact and consumed by the
    # Phase-2 settings_agents importer; the legacy per-section YAML path has no
    # restorer for them. ``core_settings`` + ``comskip`` are gathered from ONE
    # endpoint (GET /api/core/settings/ — see _COMSKIP_KEY_PREFIX for why
    # Dispatcharr's comskip-config endpoint is not a backup source; the importer
    # applies both via per-key PATCH on that same namespace) and split by the
    # ``comskip`` key prefix.
    #
    # ``dvr_rules`` (lsa0s) carries Dispatcharr's RECURRING RECORDING RULES
    # (client.get_dvr_rules -> /api/channels/recurring-rules/). SERIES rules are
    # deliberately NOT in this category: Dispatcharr stores them inside the
    # ``dvr_settings`` row that ``core_settings`` already carries, so routing
    # them here as well would apply the same state twice.
    #
    # ``upcoming_recordings`` (…-ciabe) carries the recording INSTANCES
    # (client.get_recordings -> /api/channels/recordings/) that have not started
    # yet. This used to be excluded wholesale as "per-instance state", which left
    # NO category covering recordings at all — a restore produced a replica whose
    # scheduled recordings had silently vanished. ADR-013's governing principle
    # splits the population three ways, and every exclusion below is named,
    # justified and reported to the operator (``_partition_upcoming_recordings``
    # + ``_RECORDINGS_EXCLUDED`` -> the run report):
    #
    #   * NOT STARTED YET -> replicates. Portable: an absolute start time and one
    #     channel FK, nothing else. Losing it is a silently missed recording.
    #   * ALREADY STARTED OR FINISHED -> technically impossible. The row points at
    #     a media file on the SOURCE instance's disk, which no API can carry, and
    #     Dispatcharr refuses the create outright (``400 "End time must be in the
    #     future."``, measured on 0.29.0). The operator copies those files across
    #     by hand if they want them; the run report says so.
    #   * GENERATED BY A RECURRING RULE -> already replicated, by ``dvr_rules``.
    #     The destination's own hourly maintainer recreates them from the rule.
    #     See :func:`_recording_is_regenerated_by_a_rule` for why carrying them
    #     too would DUPLICATE rather than merge.
    "user_agents": {"label": "User Agents", "dispatcharr": True, "artifact_only": True},
    # tyrg1 — Dispatcharr SERVER GROUPS (client.get_server_groups ->
    # /api/m3u/server-groups/). A ServerGroup groups M3U accounts that share
    # provider credentials so they share a credential-scoped connection
    # counter; measured on 0.29.0 it carries EXACTLY ONE field, a unique name.
    # It is in this producer set because it is the FK target an M3U account's
    # ``server_group`` resolves through — the restore/sync importers order it
    # BEFORE M3U_ACCOUNT for that reason. ``artifact_only`` for the same reason
    # as its neighbours: the legacy per-section YAML path has no restorer.
    "server_groups": {
        "label": "Server Groups", "dispatcharr": True, "artifact_only": True,
    },
    "dvr_rules": {"label": "DVR Rules", "dispatcharr": True, "artifact_only": True},
    "upcoming_recordings": {
        "label": "Upcoming Recordings", "dispatcharr": True, "artifact_only": True,
    },
    "core_settings": {
        "label": "Core Settings", "dispatcharr": True, "artifact_only": True,
    },
    "comskip": {
        "label": "Comskip Settings", "dispatcharr": True, "artifact_only": True,
    },
}


def _parse_yaml_export(content: bytes) -> dict:
    """Parse and validate a YAML export file. Raises HTTPException on failure."""
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        raise HTTPException(status_code=400, detail="Invalid YAML: %s" % str(e))

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid YAML export: expected a mapping at top level")

    if "ecm_export" not in data:
        raise HTTPException(status_code=400, detail="Not a valid ECM YAML export: missing ecm_export header")

    return data


def _count_section_items(data: dict, section_key: str) -> int:
    """Count the number of items in a section of the parsed YAML."""
    if section_key == "settings":
        settings = data.get("settings")
        return len(settings) if isinstance(settings, dict) else 0

    # Check database sections
    db = data.get("database", {})
    if section_key in db:
        items = db[section_key]
        return len(items) if isinstance(items, list) else 0

    # Check dispatcharr sections
    dispatcharr = data.get("dispatcharr", {})
    if section_key in dispatcharr:
        items = dispatcharr[section_key]
        return len(items) if isinstance(items, list) else 0

    return 0


@router.post("/validate")
async def validate_yaml_export(file: UploadFile = File(...), _admin=RequireAdminIfEnabled):
    """Parse a YAML export and return section metadata with item counts.

    Used by the frontend to show which sections are available for selective restore.
    """
    logger.info("[BACKUP] YAML validate requested, filename=%s", file.filename)

    content = await file.read()
    data = _parse_yaml_export(content)

    export_meta = data.get("ecm_export", {})
    sections = []
    for key, info in RESTORABLE_SECTIONS.items():
        # artifact_only categories (channels / dispatcharr_users — 7i8rf) are not
        # restorable via the legacy YAML path this validate drives; hide them.
        if info.get("artifact_only"):
            continue
        count = _count_section_items(data, key)
        sections.append({
            "key": key,
            "label": info["label"],
            "item_count": count,
            "available": count > 0,
        })

    return {
        "valid": True,
        "version": export_meta.get("version"),
        "exported_at": export_meta.get("exported_at"),
        "sections": sections,
    }


class YamlRestoreRequest(BaseModel):
    sections: list[str]


@router.post("/restore-yaml")
async def restore_from_yaml(
    file: UploadFile = File(...),
    sections: str = Body(..., description="JSON array of section keys to restore"),
    _admin=RequireHumanAdminIfEnabled,
):
    """Selectively restore ECM configuration from a YAML export. Human-admin only.

    kgz3k / bead 6n76m: uses ``RequireHumanAdminIfEnabled`` so the MCP service
    principal is rejected. The ``settings`` section restore path
    (``_restore_settings`` -> ``save_settings``) writes the settings blob
    wholesale, the same admin-only-field-bypass surface as the ZIP restores.

    Accepts a YAML file and a list of section keys. Each section is restored
    independently; partial failures are reported without aborting other sections.
    Restore semantics: delete existing → recreate from YAML (replace all).
    """
    logger.info("[BACKUP] YAML restore requested, filename=%s", file.filename)

    # Parse sections list from form field
    try:
        selected_sections = json.loads(sections)
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid sections parameter: expected JSON array")

    if not isinstance(selected_sections, list) or not selected_sections:
        raise HTTPException(status_code=400, detail="Must select at least one section to restore")

    # Validate section keys
    invalid = [s for s in selected_sections if s not in RESTORABLE_SECTIONS]
    if invalid:
        raise HTTPException(status_code=400, detail="Unknown sections: %s" % ", ".join(invalid))

    # artifact_only categories (channels / dispatcharr_users — 7i8rf) have no
    # legacy per-section restorer; they are restorable only via the DBAS artifact
    # path. Reject them here rather than letting _restore_section raise.
    artifact_only = [
        s for s in selected_sections
        if RESTORABLE_SECTIONS[s].get("artifact_only")
    ]
    if artifact_only:
        raise HTTPException(
            status_code=400,
            detail="Sections not restorable via YAML (use a DBAS backup): %s"
            % ", ".join(artifact_only),
        )

    content = await file.read()
    data = _parse_yaml_export(content)

    sections_restored = []
    sections_failed = []
    warnings = []
    errors = []

    for section_key in selected_sections:
        try:
            result = await _restore_section(data, section_key)
            sections_restored.append(section_key)
            if result.get("warnings"):
                warnings.extend(result["warnings"])
            logger.info("[BACKUP] Restored section: %s", section_key)
        except Exception as e:
            sections_failed.append(section_key)
            # CodeQL py/stack-trace-exposure (#1412): do NOT include str(e) in
            # the response. The full exception is logged with type and trace
            # via logger.exception so operators can correlate via X-Request-ID;
            # the client receives only the section key + exception class so
            # internal paths/values do not leak. Restore is admin-only, but
            # ADR-005 disallows "won't fix" dismissal — this is the real fix.
            errors.append("%s: %s" % (section_key, type(e).__name__))
            logger.exception(
                "[BACKUP] Failed to restore section %s", section_key
            )

    success = len(sections_failed) == 0

    logger.info(
        "[BACKUP] YAML restore complete: %d restored, %d failed",
        len(sections_restored), len(sections_failed),
    )
    return {
        "success": success,
        "sections_restored": sections_restored,
        "sections_failed": sections_failed,
        "warnings": warnings,
        "errors": errors,
    }


async def _restore_section(data: dict, section_key: str) -> dict:
    """Restore a single section from parsed YAML. Returns {warnings: [...]}."""
    if section_key == "settings":
        return _restore_settings(data.get("settings", {}))

    # Check DB sections
    db_data = data.get("database", {})
    if section_key in _SECTION_RESTORERS:
        items = db_data.get(section_key, [])
        return _SECTION_RESTORERS[section_key](items)

    # Check Dispatcharr sections
    if section_key in _DISPATCHARR_RESTORERS:
        dispatcharr_data = data.get("dispatcharr", {})
        items = dispatcharr_data.get(section_key, [])
        return await _DISPATCHARR_RESTORERS[section_key](items)

    raise ValueError("No restore handler for section: %s" % section_key)


def _restore_settings(settings_data: dict) -> dict:
    """Restore settings from YAML, preserving redacted credential fields."""
    warnings = []
    current = get_settings()
    merged = current.model_dump()

    for key, value in settings_data.items():
        if key == "mcp_api_key":
            warnings.append("Skipped instance-bound field: mcp_api_key (kept existing value)")
            continue
        if value == REDACTED:
            warnings.append("Skipped redacted field: %s (kept existing value)" % key)
            continue
        merged[key] = value

    new_settings = DispatcharrSettings(**merged)
    save_settings(new_settings)
    clear_settings_cache()
    return {"warnings": warnings}


def _restore_scheduled_tasks(items: list) -> dict:
    """Delete all scheduled tasks and recreate from YAML."""
    session = get_session()
    try:
        session.query(ScheduledTask).delete()
        for item in items:
            task = ScheduledTask(
                task_id=item["task_id"],
                task_name=item["task_name"],
                description=item.get("description"),
                enabled=item.get("enabled", True),
                schedule_type=item.get("schedule_type", "manual"),
                interval_seconds=item.get("interval_seconds"),
                cron_expression=item.get("cron_expression"),
                schedule_time=item.get("schedule_time"),
                timezone=item.get("timezone"),
                config=json.dumps(item["config"]) if item.get("config") else None,
                send_alerts=item.get("send_alerts", True),
                alert_on_success=item.get("alert_on_success", True),
                alert_on_warning=item.get("alert_on_warning", True),
                alert_on_error=item.get("alert_on_error", True),
                alert_on_info=item.get("alert_on_info", False),
                send_to_email=item.get("send_to_email", True),
                send_to_discord=item.get("send_to_discord", True),
                send_to_telegram=item.get("send_to_telegram", True),
                show_notifications=item.get("show_notifications", True),
            )
            session.add(task)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_task_schedules(items: list) -> dict:
    """Delete all task schedules and recreate from YAML."""
    session = get_session()
    try:
        session.query(TaskSchedule).delete()
        for item in items:
            schedule = TaskSchedule(
                task_id=item["task_id"],
                name=item.get("name"),
                enabled=item.get("enabled", True),
                schedule_type=item["schedule_type"],
                interval_seconds=item.get("interval_seconds"),
                schedule_time=item.get("schedule_time"),
                timezone=item.get("timezone"),
                days_of_week=item.get("days_of_week"),
                day_of_month=item.get("day_of_month"),
                week_parity=item.get("week_parity"),
                parameters=json.dumps(item["parameters"]) if item.get("parameters") else None,
            )
            session.add(schedule)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_normalization_rule_groups(items: list) -> dict:
    """Delete all normalization groups+rules and recreate from YAML."""
    session = get_session()
    try:
        session.query(NormalizationRule).delete()
        session.query(NormalizationRuleGroup).delete()
        for grp_data in items:
            group = NormalizationRuleGroup(
                name=grp_data["name"],
                description=grp_data.get("description"),
                enabled=grp_data.get("enabled", True),
                priority=grp_data.get("priority", 0),
                is_builtin=grp_data.get("is_builtin", False),
            )
            session.add(group)
            session.flush()  # get group.id

            for rule_data in grp_data.get("rules", []):
                rule = NormalizationRule(
                    group_id=group.id,
                    name=rule_data["name"],
                    enabled=rule_data.get("enabled", True),
                    priority=rule_data.get("priority", 0),
                    condition_type=rule_data.get("condition_type"),
                    condition_value=rule_data.get("condition_value"),
                    conditions=json.dumps(rule_data["conditions"]) if rule_data.get("conditions") else None,
                    condition_logic=rule_data.get("condition_logic", "AND"),
                    action_type=rule_data["action_type"],
                    action_value=rule_data.get("action_value"),
                    else_action_type=rule_data.get("else_action_type"),
                    else_action_value=rule_data.get("else_action_value"),
                    stop_processing=rule_data.get("stop_processing", False),
                    is_builtin=rule_data.get("is_builtin", False),
                )
                session.add(rule)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_tag_groups(items: list) -> dict:
    """Delete all tag groups+tags and recreate from YAML."""
    session = get_session()
    try:
        session.query(Tag).delete()
        session.query(TagGroup).delete()
        for tg_data in items:
            group = TagGroup(
                name=tg_data["name"],
                description=tg_data.get("description"),
                is_builtin=tg_data.get("is_builtin", False),
            )
            session.add(group)
            session.flush()

            for tag_data in tg_data.get("tags", []):
                tag = Tag(
                    group_id=group.id,
                    value=tag_data["value"],
                    case_sensitive=tag_data.get("case_sensitive", False),
                    enabled=tag_data.get("enabled", True),
                    is_builtin=tag_data.get("is_builtin", False),
                )
                session.add(tag)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_auto_creation_rules(items: list) -> dict:
    """Delete all auto-creation rules and recreate from YAML."""
    # ti939.1.3 (PR #612 review): validate restored event_sync configs but
    # DOWNGRADE failures to warnings — restore is delete-all-and-recreate,
    # so refusing the row would destroy the rule outright. Restoring the
    # config as-is is the fail-safe direction: the KIND comes from the raw
    # column (models.ChannelPipelineRule.is_event_sync), so even an invalid
    # config keeps the rule excluded from pipeline execution.
    from channel_pipeline_schema import validate_event_sync_config

    session = get_session()
    warnings: list[str] = []
    # bead 8fq6x: the delete-all below CASCADEs to event_sync_reviews, dropping
    # every review row — including ANSWERED accept/reject decisions. Preserve
    # them across the delete+recreate and re-key onto the restored rule by
    # NAME (fingerprints are content-based and survive; only the rule_id FK
    # breaks). Captured BEFORE the delete because the CASCADE fires on delete.
    _REVIEW_FIELDS = (
        "provider_id", "stream_name_hash", "event_key", "status",
        "created_at", "last_seen_at", "resolved_at", "resolution_source",
        "actor_token_id", "evidence",
    )
    # ti939.3.5: operator never-attach exclusions have the same CASCADE
    # exposure as review decisions — preserve/re-key them identically.
    _EXCLUSION_FIELDS = (
        "provider_id", "stream_name_hash", "event_key",
        "created_at", "note", "actor_token_id", "evidence",
    )
    try:
        id_to_name = {
            rid: name
            for rid, name in session.query(
                ChannelPipelineRule.id, ChannelPipelineRule.name
            )
        }
        preserved_reviews: list[dict] = []
        for rv in session.query(EventSyncReview).all():
            rule_name = id_to_name.get(rv.rule_id)
            if rule_name is None:
                continue
            preserved_reviews.append({
                "rule_name": rule_name,
                **{f: getattr(rv, f) for f in _REVIEW_FIELDS},
            })
        preserved_exclusions: list[dict] = []
        for ex in session.query(EventSyncExclusion).all():
            rule_name = id_to_name.get(ex.rule_id)
            if rule_name is None:
                continue
            preserved_exclusions.append({
                "rule_name": rule_name,
                **{f: getattr(ex, f) for f in _EXCLUSION_FIELDS},
            })

        session.query(ChannelPipelineRule).delete()
        # Clear the review table explicitly rather than relying on the FK
        # CASCADE — deterministic regardless of the connection's
        # foreign_keys pragma, and the captured rows above are re-inserted
        # with the restored rules' new ids below.
        session.query(EventSyncReview).delete()
        session.query(EventSyncExclusion).delete()
        for item in items:
            # ti939.1.3: the export (to_dict) carries event_sync_config as a
            # parsed dict — re-serialize for the Text column. Dropping it
            # here would resurrect the rule as a STANDARD rule whose dormant
            # conditions/actions execute on the next run.
            event_sync_config = item.get("event_sync_config")
            if event_sync_config is not None:
                es_errors = validate_event_sync_config(event_sync_config)
                if es_errors:
                    warnings.append(
                        f"Rule '{item.get('name')}': event_sync_config failed "
                        f"validation ({len(es_errors)} error(s)); restored "
                        f"as-is — the rule keeps the event_sync kind and "
                        f"stays excluded from pipeline execution. First "
                        f"error: {es_errors[0]}"
                    )
            rule = ChannelPipelineRule(
                name=item["name"],
                description=item.get("description"),
                enabled=item.get("enabled", True),
                priority=item.get("priority", 0),
                active_from=(date.fromisoformat(item["active_from"])
                             if item.get("active_from") else None),
                active_until=(date.fromisoformat(item["active_until"])
                              if item.get("active_until") else None),
                m3u_account_id=item.get("m3u_account_id"),
                target_group_id=item.get("target_group_id"),
                conditions=json.dumps(item["conditions"]) if item.get("conditions") else "[]",
                actions=json.dumps(item["actions"]) if item.get("actions") else "[]",
                run_on_refresh=item.get("run_on_refresh", False),
                stop_on_first_match=item.get("stop_on_first_match", True),
                sort_field=item.get("sort_field"),
                sort_order=item.get("sort_order", "asc"),
                probe_on_sort=item.get("probe_on_sort", False),
                sort_regex=item.get("sort_regex"),
                stream_sort_field=item.get("stream_sort_field"),
                stream_sort_order=item.get("stream_sort_order", "asc"),
                quality_tie_break_order=item.get("quality_tie_break_order", "desc"),
                quality_m3u_tie_break_enabled=item.get("quality_m3u_tie_break_enabled", True),
                normalization_group_ids=_resolve_backup_normalization_group_ids(item, session),
                skip_struck_streams=item.get("skip_struck_streams", False),
                orphan_action=item.get("orphan_action", "delete"),
                # bd-p6ko9: restore the stored per-rule value; ECM-generated
                # backups always include this field (via to_dict). An ancient
                # backup that omits it inherits the new-rule default (True).
                match_scope_target_group=item.get("match_scope_target_group", True),
                # GH #298 (bd-kncun): None = "Auto" (preserves prior behavior).
                # Backups predating this column omit it and inherit None.
                match_scope_group_id=item.get("match_scope_group_id"),
                # enhancedchannelmanager-orzck (W1): default False protects
                # manual channels. Backups predating this column inherit False.
                allow_manual_channel_merge=item.get("allow_manual_channel_merge", False),
                fold_match_key=item.get("fold_match_key", False),
                # ti939.1.3: keep the event_sync KIND across backup/restore.
                # Backups predating this column omit it and inherit None
                # (standard kind).
                event_sync_config=(
                    json.dumps(event_sync_config)
                    if event_sync_config else None
                ),
            )
            session.add(rule)
        session.flush()  # assign ids to the recreated rules

        # bead 8fq6x: re-attach the preserved review decisions to the restored
        # rule by NAME. Rows whose rule is not in the restore set are dropped
        # (warned). Dedup on (new_rule_id, fingerprint) so duplicate rule
        # names can't collapse two rules' rows onto one id and violate the
        # unique-fingerprint constraint.
        name_to_new_id: dict[str, int] = {}
        for rid, name in (
            session.query(ChannelPipelineRule.id, ChannelPipelineRule.name)
            .order_by(ChannelPipelineRule.id)
        ):
            name_to_new_id.setdefault(name, rid)  # lowest id wins
        seen: set = set()
        rekeyed = 0
        orphaned = 0
        for pr in preserved_reviews:
            new_id = name_to_new_id.get(pr["rule_name"])
            if new_id is None:
                orphaned += 1
                continue
            key = (
                new_id, pr["provider_id"], pr["stream_name_hash"],
                pr["event_key"],
            )
            if key in seen:
                continue
            seen.add(key)
            session.add(EventSyncReview(
                rule_id=new_id, **{f: pr[f] for f in _REVIEW_FIELDS}
            ))
            rekeyed += 1
        if orphaned:
            warnings.append(
                f"{orphaned} Event Sync review decision(s) dropped on restore: "
                f"their rule is not in the restored set."
            )
        if rekeyed:
            logger.info(
                "[BACKUP] Re-keyed %s Event Sync review decision(s) onto "
                "restored rules by name", rekeyed,
            )

        # ti939.3.5: same re-key for the never-attach exclusions.
        seen_ex: set = set()
        ex_rekeyed = 0
        ex_orphaned = 0
        for pe in preserved_exclusions:
            new_id = name_to_new_id.get(pe["rule_name"])
            if new_id is None:
                ex_orphaned += 1
                continue
            key = (
                new_id, pe["provider_id"], pe["stream_name_hash"],
                pe["event_key"],
            )
            if key in seen_ex:
                continue
            seen_ex.add(key)
            session.add(EventSyncExclusion(
                rule_id=new_id, **{f: pe[f] for f in _EXCLUSION_FIELDS}
            ))
            ex_rekeyed += 1
        if ex_orphaned:
            warnings.append(
                f"{ex_orphaned} Event Sync never-attach exclusion(s) dropped "
                f"on restore: their rule is not in the restored set."
            )
        if ex_rekeyed:
            logger.info(
                "[BACKUP] Re-keyed %s Event Sync exclusion(s) onto restored "
                "rules by name", ex_rekeyed,
            )
        session.commit()
        return {"warnings": warnings}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_ffmpeg_profiles(items: list) -> dict:
    """Delete all FFmpeg profiles and recreate from YAML."""
    session = get_session()
    try:
        session.query(FFmpegProfile).delete()
        for item in items:
            profile = FFmpegProfile(
                name=item["name"],
                config=json.dumps(item["config"]) if item.get("config") else "{}",
            )
            session.add(profile)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _restore_dummy_epg_profiles(items: list) -> dict:
    """Delete all dummy EPG profiles+assignments and recreate from YAML."""
    session = get_session()
    try:
        session.query(DummyEPGChannelAssignment).delete()
        session.query(DummyEPGProfile).delete()
        for item in items:
            profile = DummyEPGProfile(
                name=item["name"],
                enabled=item.get("enabled", True),
                name_source=item.get("name_source", "channel"),
                stream_index=item.get("stream_index", 1),
                title_pattern=item.get("title_pattern"),
                time_pattern=item.get("time_pattern"),
                date_pattern=item.get("date_pattern"),
                substitution_pairs=json.dumps(item["substitution_pairs"]) if item.get("substitution_pairs") else None,
                title_template=item.get("title_template"),
                description_template=item.get("description_template"),
                upcoming_title_template=item.get("upcoming_title_template"),
                upcoming_description_template=item.get("upcoming_description_template"),
                ended_title_template=item.get("ended_title_template"),
                ended_description_template=item.get("ended_description_template"),
                fallback_title_template=item.get("fallback_title_template"),
                fallback_description_template=item.get("fallback_description_template"),
                event_timezone=item.get("event_timezone", "US/Eastern"),
                output_timezone=item.get("output_timezone"),
                program_duration=item.get("program_duration", 180),
                categories=item.get("categories"),
                channel_logo_url_template=item.get("channel_logo_url_template"),
                program_poster_url_template=item.get("program_poster_url_template"),
                tvg_id_template=item.get("tvg_id_template", "ecm-{channel_number}"),
                include_date_tag=item.get("include_date_tag", False),
                include_live_tag=item.get("include_live_tag", False),
                include_new_tag=item.get("include_new_tag", False),
                pattern_builder_examples=item.get("pattern_builder_examples"),
                pattern_variants=json.dumps(item["pattern_variants"]) if item.get("pattern_variants") else None,
                channel_group_ids=json.dumps(item["channel_group_ids"]) if item.get("channel_group_ids") else None,
            )
            session.add(profile)
            session.flush()

            for assignment in item.get("channel_assignments", []):
                a = DummyEPGChannelAssignment(
                    profile_id=profile.id,
                    channel_id=assignment["channel_id"],
                    channel_name=assignment["channel_name"],
                    tvg_id_override=assignment.get("tvg_id_override"),
                )
                session.add(a)
        session.commit()
        return {"warnings": []}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# Registry mapping section keys to their restore functions
_SECTION_RESTORERS = {
    "scheduled_tasks": _restore_scheduled_tasks,
    "task_schedules": _restore_task_schedules,
    "normalization_rule_groups": _restore_normalization_rule_groups,
    "tag_groups": _restore_tag_groups,
    "auto_creation_rules": _restore_auto_creation_rules,
    "ffmpeg_profiles": _restore_ffmpeg_profiles,
    "dummy_epg_profiles": _restore_dummy_epg_profiles,
}


# ---------------------------------------------------------------------------
# Dispatcharr section restore functions (async — use Dispatcharr API)
# ---------------------------------------------------------------------------

def _warn_credential_reentry(entity: str, label, removed: list[str]) -> list[str]:
    """The legacy YAML restore's credential-re-entry warning — field NAMES only.

    The DBAS restore reports this structurally (``credential_reentry_details``);
    this path has only a warnings list, so it says the same thing in prose. Bead
    …-gi4zn: silence here is what lets an operator believe a restored provider is
    configured when it cannot authenticate.
    """
    if not removed:
        return []
    return [
        "%s '%s': %s could not be carried by a redacted export and must be "
        "re-entered before it will refresh." % (entity, label, ", ".join(removed))
    ]


async def _restore_m3u_accounts(items: list) -> dict:
    """Delete all M3U accounts and recreate from YAML via Dispatcharr API."""
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped M3U accounts restore"]}
    warnings = []
    # Delete existing
    existing = await client.get_m3u_accounts() or []
    for acct in existing:
        try:
            await client.delete_m3u_account(acct["id"])
        except Exception as e:
            warnings.append("Failed to delete M3U account %s: %s" % (acct.get("name"), e))
    # Recreate
    for item in items:
        create_data = {k: v for k, v in item.items() if k not in ("id", "channel_groups", "streams_count")}
        # Never write ECM's own placeholder into a destination credential field
        # (bead …-6pilh, applied to this legacy path by …-gi4zn). A sentinel
        # written through produces an account that LOOKS configured — the field
        # is populated and every truthiness probe says yes — and fails at the
        # provider, which is strictly worse than a visibly-unset field. The DBAS
        # importer has stripped sentinels since 6pilh; this path did not, and
        # became reachable for the IDENTITY half once the export redacted it.
        create_data, removed = strip_redaction_sentinels(create_data)
        try:
            await client.create_m3u_account(create_data)
        except Exception as e:
            warnings.append("Failed to create M3U account %s: %s" % (item.get("name"), e))
            continue
        warnings.extend(
            _warn_credential_reentry("M3U account", item.get("name"), removed)
        )
    return {"warnings": warnings}


async def _restore_epg_sources(items: list) -> dict:
    """Delete all EPG sources and recreate from YAML via Dispatcharr API."""
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped EPG sources restore"]}
    warnings = []
    existing = await client.get_epg_sources() or []
    for src in existing:
        try:
            await client.delete_epg_source(src["id"])
        except Exception as e:
            warnings.append("Failed to delete EPG source %s: %s" % (src.get("name"), e))
    for item in items:
        create_data = {k: v for k, v in item.items() if k not in ("id",)}
        # Same rule as the M3U path above — see its comment.
        create_data, removed = strip_redaction_sentinels(create_data)
        try:
            await client.create_epg_source(create_data)
        except Exception as e:
            warnings.append("Failed to create EPG source %s: %s" % (item.get("name"), e))
            continue
        warnings.extend(
            _warn_credential_reentry("EPG source", item.get("name"), removed)
        )
    return {"warnings": warnings}


async def _restore_channel_groups(items: list) -> dict:
    """Upsert channel groups by name via Dispatcharr API.

    Channel groups are referenced by ID from channels and streams. Deleting and
    recreating them would orphan those references, so we only create groups that
    don't already exist (matched by name) and leave existing groups intact.
    """
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped channel groups restore"]}
    warnings = []
    existing = await client.get_channel_groups() or []
    existing_names = {g.get("name") for g in existing}
    created = 0
    for item in items:
        name = item.get("name")
        if not name or name in existing_names:
            continue
        try:
            await client.create_channel_group(name)
            existing_names.add(name)
            created += 1
        except Exception as e:
            warnings.append("Failed to create channel group %s: %s" % (name, e))
    logger.info("[BACKUP] Channel groups restore: created %d new groups, kept %d existing", created, len(existing))
    return {"warnings": warnings}


async def _restore_channel_profiles(items: list) -> dict:
    """Delete all channel profiles and recreate from YAML via Dispatcharr API."""
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped channel profiles restore"]}
    warnings = []
    existing = await client.get_channel_profiles() or []
    for prof in existing:
        try:
            await client.delete_channel_profile(prof["id"])
        except Exception as e:
            warnings.append("Failed to delete channel profile %s: %s" % (prof.get("name"), e))
    for item in items:
        create_data = {k: v for k, v in item.items() if k not in ("id",)}
        try:
            await client.create_channel_profile(create_data)
        except Exception as e:
            warnings.append("Failed to create channel profile %s: %s" % (item.get("name"), e))
    return {"warnings": warnings}


async def _restore_stream_profiles(items: list) -> dict:
    """Recreate stream profiles from YAML via Dispatcharr API.

    Note: Dispatcharr stream profiles cannot be deleted via API,
    so we only create missing ones.
    """
    client = get_client()
    if not client:
        return {"warnings": ["Dispatcharr not connected — skipped stream profiles restore"]}
    warnings = []
    existing = await client.get_stream_profiles() or []
    existing_names = {p.get("name") for p in existing}
    for item in items:
        if item.get("name") in existing_names:
            continue  # Skip already existing
        create_data = {k: v for k, v in item.items() if k not in ("id",)}
        try:
            await client.create_stream_profile(create_data)
        except Exception as e:
            warnings.append("Failed to create stream profile %s: %s" % (item.get("name"), e))
    if existing_names:
        warnings.append("Existing stream profiles kept (cannot be deleted via API)")
    return {"warnings": warnings}


# Registry for async Dispatcharr restore functions
_DISPATCHARR_RESTORERS = {
    "m3u_accounts": _restore_m3u_accounts,
    "epg_sources": _restore_epg_sources,
    "channel_groups": _restore_channel_groups,
    "channel_profiles": _restore_channel_profiles,
    "stream_profiles": _restore_stream_profiles,
}


# ---------------------------------------------------------------------------
# Saved Backups (on-disk YAML files from scheduled task)
# ---------------------------------------------------------------------------

BACKUPS_DIR = CONFIG_DIR / "backups"
# Strict allowlist for the on-disk filename shape. ``.yaml`` = scheduled YAML
# export; ``.zip`` = on-demand full backup persisted by POST /save (bd-0hjrk.5).
# Both download_saved_backup and delete_saved_backup accept either extension;
# restore-saved further restricts to ``.zip`` (the full-archive restore path).
_BACKUP_FILENAME_RE = re.compile(r"^ecm-backup-\d{4}-\d{2}-\d{2}_\d{6}\.(yaml|zip)$")
# Zip-only allowlist for the full-archive restore path (restore-saved) and the
# on-demand save path. YAML section-import is a different path
# (POST /restore-yaml) and out of scope here.
_BACKUP_ZIP_FILENAME_RE = re.compile(r"^ecm-backup-\d{4}-\d{2}-\d{2}_\d{6}\.zip$")

# SECURITY (CodeQL py/path-injection, CWE-22/23/36/73/99): the two-layer guard
# (strict regex allowlist + canonicalize-and-verify containment under
# BACKUPS_DIR) is INLINED at each filename-addressed endpoint below rather than
# factored into a shared helper. CodeQL's dataflow tracker does NOT follow the
# `relative_to` containment barrier across a function-return boundary, so a
# helper that validates-then-returns a Path is still treated as user-tainted at
# every downstream file-op sink. Keeping the barrier and the file op in the
# SAME function body is the proven-passing pattern (matches origin/dev). The
# allowlist uses re.fullmatch (not .match) so a trailing newline cannot slip
# past the `$` anchor (SEC-1 hardening).


@router.get("/saved")
async def list_saved_backups(_admin=RequireAdminIfEnabled):
    """List saved backup files on disk (YAML exports + on-demand ZIP archives),
    newest first. Each entry carries a ``type`` field: "yaml" (scheduled YAML
    export) or "zip" (full on-demand backup persisted by POST /save)."""
    if not BACKUPS_DIR.exists():
        return []
    files = sorted(
        list(BACKUPS_DIR.glob("ecm-backup-*.yaml"))
        + list(BACKUPS_DIR.glob("ecm-backup-*.zip")),
        key=lambda f: f.name,
        reverse=True,
    )
    return [
        {
            "filename": f.name,
            "size_bytes": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).isoformat(),
            "type": "zip" if f.suffix == ".zip" else "yaml",
        }
        for f in files
    ]


@router.post("/save")
async def save_backup(_admin=RequireAdminIfEnabled):
    """Create a full backup ZIP and PERSIST it to BACKUPS_DIR. Admin only.

    Unlike GET /create (which streams the ZIP to the HTTP client and persists
    nothing), this writes the same ``_create_backup_zip()`` artifact to disk as
    ``ecm-backup-<UTC ts>.zip`` so it is discoverable via GET /saved and
    restorable via POST /restore-saved (bd-0hjrk.5). The persisted ZIP is the
    same redacted artifact GET /create produces — redaction is unchanged.
    """
    logger.info("[BACKUP] Saving backup to disk")
    try:
        buf = _create_backup_zip()
        data = buf.getvalue()
        filename = _get_backup_filename()  # ecm-backup-<UTC ts>.zip
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        # Two-layer guard, inlined (CodeQL py/path-injection, CWE-22/23/36/73/99
        # — cross-function barrier not tracked; keep barrier+sink in one body).
        # Layer 1 (defense in depth): strict zip-only regex allowlist (fullmatch
        # so a trailing newline cannot pass the anchor).
        if not _BACKUP_ZIP_FILENAME_RE.fullmatch(filename):
            raise HTTPException(status_code=400, detail="Invalid filename")
        # Layer 2: canonicalize + verify containment under BACKUPS_DIR.
        try:
            safe_root = BACKUPS_DIR.resolve()
            path = (BACKUPS_DIR / filename).resolve()
            path.relative_to(safe_root)
        except (ValueError, OSError):
            raise HTTPException(status_code=400, detail="Invalid filename")
        _write_private_bytes(path, data)
        logger.info("[BACKUP] Saved backup %s (%d bytes)", filename, len(data))
        return {
            "filename": filename,
            "size_bytes": len(data),
            "created_at": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("[BACKUP] Failed to save backup: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save backup: %s" % str(e))


class RestoreSavedRequest(BaseModel):
    filename: str


@router.post("/restore-saved")
async def restore_saved_backup(req: RestoreSavedRequest, _admin=RequireHumanAdminIfEnabled):
    """Restore ECM configuration from an on-disk saved backup ZIP. Human-admin only.

    kgz3k / bead 6n76m: uses ``RequireHumanAdminIfEnabled`` so the MCP service
    principal is rejected — this reuses the EXACT ``_restore_from_zip`` settings-
    blob write path as POST /restore, so it carries the same admin-field-bypass
    risk. The shipped MCP ``restore_backup`` tool now receives a clean 403 here.

    Takes ``{"filename": "ecm-backup-<ts>.zip"}``, selects it from the trusted
    direct-child listing of BACKUPS_DIR, then restores from the retained file
    descriptor reusing the EXACT same validate + restore code path as the
    uploaded-ZIP POST /restore (``_validate_backup_zip`` +
    ``_restore_from_zip``). YAML artifacts are rejected — section-import is a
    different path (POST /restore-yaml), out of scope here (bd-0hjrk.5).

    WARNING: this OVERWRITES current ECM state (settings, database, logos).
    """
    logger.info("[BACKUP] Restore-from-saved requested, filename=%s", req.filename)
    filename = req.filename
    # Keep the strict allowlist, then break request taint by selecting a Path
    # originating from BACKUPS_DIR's trusted direct-child enumeration.
    if not _BACKUP_ZIP_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")

    saved_backups = {}
    try:
        for entry in BACKUPS_DIR.iterdir():
            saved_backups[entry.name] = entry
    except OSError:
        raise HTTPException(status_code=404, detail="Backup not found")
    path = saved_backups.get(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Backup not found")

    # O_NOFOLLOW and fstat bind type validation and restore to the same opened
    # object. O_NONBLOCK prevents a planted FIFO from blocking this request.
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise HTTPException(status_code=404, detail="Backup not found")
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise HTTPException(status_code=404, detail="Backup not found")
        archive = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise

    # Validate + restore via the SAME path the uploaded-ZIP restore uses.
    try:
        zf = zipfile.ZipFile(archive, "r")
    except zipfile.BadZipFile:
        archive.close()
        raise HTTPException(status_code=400, detail="Saved file is not a valid zip archive")
    except BaseException:
        archive.close()
        raise

    with archive, zf:
        manifest = _validate_backup_zip(zf)
        try:
            restored = _restore_from_zip(zf, manifest)
        finally:
            manifest.close()

    logger.info("[BACKUP] Restore-from-saved complete, %d files restored", len(restored))
    return {
        "status": "ok",
        "filename": req.filename,
        "backup_version": manifest.get("version", "unknown"),
        "backup_date": manifest.get("created_at", "unknown"),
        "restored_files": restored,
        "notices": _post_restore_account_notices(),
    }


class RestoreDbasSavedRequest(BaseModel):
    filename: str
    confirm_apply: bool = False
    # What the post-create reattach passes do to channels this restore did NOT
    # create (bead …-dfkbn, PR review W1). Typed as an OPTIONAL plain str rather
    # than the enum so neither an unrecognised value nor an explicit JSON ``null``
    # 422s the whole restore: both are coerced to the SAFE default. A client that
    # serializes an unset field as ``null`` is asking for the default, not for a
    # validation error.
    channel_reattach_mode: Optional[str] = ChannelReattachMode.PRESERVE.value
    # Operator passphrase for an encrypted artifact (ADR-012 D12 / u81kh). Omit
    # for a plain artifact. Travels in the JSON body of this admin-only endpoint,
    # never a query string, so it does not land in access logs. It is forwarded
    # to the restore task (which excludes it from get_config) and is NEVER logged
    # or echoed back in the response by this endpoint.
    passphrase: Optional[str] = None


@router.post("/restore-dbas-saved")
async def restore_dbas_saved(
    req: RestoreDbasSavedRequest,
    _admin=RequireAdminIfEnabled,
    caller_is_mcp: bool = ResolveIsMcpServicePrincipalIfEnabled,
):
    """Trigger an async DBAS restore from an on-disk SAVED artifact.

    Admin required. The MCP service principal is admitted for the counts-only
    PREVIEW and refused for the APPLY.

    bead 9kwzp.10 item 2, as revised by the PR #855 review. The APPLY half is
    refused for the reason :func:`restore_dbas_artifact` records: bead …-dfkbn
    item 4 taught the DBAS restore to write ECM's own ``settings.json``, which
    is the kgz3k bypass bead 6n76m closed on the legacy ``/restore*`` trio. The
    PREVIEW half is NOT, because that reasoning does not reach a run that
    writes nothing: ``dbas.restore_orchestrator`` forces
    ``report.is_dry_run = True`` whenever ``confirm_apply`` is false, as the
    single choke point a caller can never opt out of. This route also names an
    artifact ALREADY on the server, which only an admin could have saved there,
    so nothing here is caller-supplied except the filename. Blanket-denying it
    would have removed the documented safe mode of the sidecar's
    ``restore_dbas_backup_saved`` tool as an unremarked side effect.

    The upload sibling ``POST /restore-dbas`` is deliberately NOT split this
    way; see the module-level comment above ``_DBAS_APPLY_MCP_DENIAL``.


    Takes ``{"filename": "ecm-backup-<ts>.zip", "confirm_apply": false,
    "passphrase": null}``, resolves the filename to its saved
    ``/config/backups/`` path through the strict regex + containment guard, then
    kicks :class:`tasks.dbas_restore.DbasRestoreTask` in the background (the SAME
    fire-and-forget pattern as POST /restore-dbas) and returns its ``task_id`` so
    the caller can poll ``/api/tasks/{task_id}`` for the terminal RestoreReport.

    This is the SAVED-file analogue of the upload-based POST /restore-dbas, and
    handles the v0.18.0 DBAS artifact format (incl. encrypted artifacts via
    ``passphrase``) — unlike the LEGACY POST /restore-saved, which only restores
    old-format ZIPs.

    DRY-RUN is default-ON: without ``confirm_apply=True`` the run is a counts-only
    plan that makes ZERO mutation. ``cleanup_artifact`` is DELIBERATELY False
    here — the artifact is the operator's SAVED backup, NOT a throwaway temp, so
    it MUST survive the restore.
    """
    # The APPLY carve-out, raised BEFORE the filename is resolved so a refused
    # caller learns nothing about which artifacts exist on disk.
    if req.confirm_apply and caller_is_mcp:
        logger.warning(
            "[BACKUP] Refused DBAS restore APPLY for the MCP service principal"
        )
        raise HTTPException(status_code=403, detail=_DBAS_APPLY_MCP_DENIAL)

    filename = req.filename
    reattach_mode = ChannelReattachMode.coerce(req.channel_reattach_mode)
    logger.info(
        "[BACKUP] DBAS restore-from-saved requested (filename=%s, "
        "confirm_apply=%s, channel_reattach_mode=%s)",
        filename, req.confirm_apply, reattach_mode.value,
    )
    # NB: req.passphrase is intentionally NOT logged here (and is excluded from
    # the task's get_config) — it must never surface in a log line or response.

    # Path resolution by TRUSTED ENUMERATION (CodeQL py/path-injection,
    # CWE-22/23/36/73/99). Unlike restore_saved_backup — whose validated path is
    # consumed in-function — this path ESCAPES as the dbas_restore task's
    # ``artifact_path`` (used to open the file in another function), so an
    # in-function containment barrier is not tracked to that sink. Instead of
    # building a path FROM the request, we enumerate the real saved backups (a
    # trusted filesystem source) and select the matching one: the path we use
    # then originates from ``iterdir()`` — a direct-child listing of BACKUPS_DIR,
    # so no traversal is representable and the user value never reaches the sink.
    # Layer 1 (defense in depth): strict zip-only regex allowlist (fullmatch).
    if not _BACKUP_ZIP_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Layer 2: select from the trusted directory listing (breaks the taint flow —
    # the chosen Path comes from iterdir, not from the request body).
    saved_backups = {}
    try:
        for entry in BACKUPS_DIR.iterdir():
            if entry.is_file():
                saved_backups[entry.name] = entry
    except OSError:
        raise HTTPException(status_code=404, detail="Backup not found")
    path = saved_backups.get(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Backup not found")

    # Kick the restore task against the SAVED path. cleanup_artifact=False so the
    # operator's saved backup is NOT deleted after the restore.
    parameters = {
        "artifact_path": str(path),
        "confirm_apply": bool(req.confirm_apply),
        "cleanup_artifact": False,
        "channel_reattach_mode": reattach_mode.value,
    }
    # Forward the passphrase only when present (encrypted artifact). The task
    # excludes it from get_config so it is never persisted or logged.
    if req.passphrase:
        parameters["passphrase"] = req.passphrase

    try:
        from task_engine import get_engine

        engine = get_engine()
        # Fire-and-forget: schedule as a background asyncio task and return the
        # task id immediately. The caller polls /api/tasks/{id} for progress.
        asyncio.create_task(
            engine.run_task(DBAS_RESTORE_TASK_ID, parameters=parameters)
        )
    except Exception as exc:
        logger.exception("[BACKUP] Failed to schedule DBAS restore-from-saved task: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to start restore")

    return {
        "status": "started",
        "task_id": DBAS_RESTORE_TASK_ID,
        "is_dry_run": not req.confirm_apply,
        "channel_reattach_mode": reattach_mode.value,
    }


@router.get("/saved/{filename}")
async def download_saved_backup(filename: str, _admin=RequireAdminIfEnabled):
    """Download a saved backup file (YAML export or ZIP archive)."""
    # Two-layer guard, inlined (CodeQL py/path-injection, CWE-22/23/36/73/99 —
    # the containment barrier is not tracked across a function-return boundary,
    # so the barrier and the read_bytes/read_text sinks must live in this same
    # function). Mirrors origin/dev's proven-passing inline pattern.
    # Layer 1 (defense in depth): strict regex allowlist (fullmatch so a trailing
    # newline cannot pass the anchor).
    if not _BACKUP_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Layer 2: canonicalize + verify containment under BACKUPS_DIR.
    try:
        safe_root = BACKUPS_DIR.resolve()
        path = (BACKUPS_DIR / filename).resolve()
        path.relative_to(safe_root)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    if filename.endswith(".zip"):
        return StreamingResponse(
            io.BytesIO(path.read_bytes()),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    content = path.read_text()
    return PlainTextResponse(
        content=content,
        media_type="text/yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/saved/{filename}", status_code=200)
async def delete_saved_backup(filename: str, _admin=RequireAdminIfEnabled):
    """Delete a saved backup file (YAML export or ZIP archive)."""
    # Two-layer guard, inlined (CodeQL py/path-injection, CWE-22/23/36/73/99 —
    # the containment barrier is not tracked across a function-return boundary,
    # so the barrier and the unlink sink must live in this same function). See
    # download_saved_backup above for the rationale.
    # Layer 1 (defense in depth): strict regex allowlist (fullmatch so a trailing
    # newline cannot pass the anchor).
    if not _BACKUP_FILENAME_RE.fullmatch(filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    # Layer 2: canonicalize + verify containment under BACKUPS_DIR.
    try:
        safe_root = BACKUPS_DIR.resolve()
        path = (BACKUPS_DIR / filename).resolve()
        path.relative_to(safe_root)
    except (ValueError, OSError):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    path.unlink()
    logger.info("[BACKUP] Deleted saved backup: %s", filename)
    return {"status": "ok", "deleted": filename}
