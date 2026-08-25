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
2. REDACTS it via the shared ``_REDACT_KEYS`` deep redactor — with ONE deliberate
   exception, the provider credential fields, which cross on every cycle under the
   PO's 2026-08-22 ruling (see :data:`PROVIDER_CREDENTIAL_SECTIONS`). ECM's own
   secrets, alert-method secrets and target credentials do not,
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
``channel_groups``, ``channel_profiles``, ``stream_profiles``, plus the two
FK-OWNER categories their dependents resolve through — ``user_agents`` (bead
``…-hiacv``) and ``server_groups`` (bead ``…-tyrg1``) — and ``core_settings``
(bead ``…-10wnq``), which S9 and S3 have listed since this ADR was written and
which appeared nowhere in this engine until that bead. Core settings are
key/value BLOBS rather than an entity list, so they are absent from
``_SECTION_TO_ENTITY`` by design and the plan assembler gives them their own
branch; WHICH blobs cross is the per-blob register
(:data:`SYNC_CORE_SETTINGS_BLOBS` / :data:`NEVER_SYNC_CORE_SETTINGS_BLOBS`).

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

LOGOS are SUB-INTERVALLED per target, not per-cycle-unconditional (ADR-013 S9):
the logos importer carries a DESTRUCTIVE ``clear_existing`` bulk-delete plus a
per-logo streaming-upload cost that does not belong in the default per-cycle
slice. The guarded slice this engine ships is exactly the S9 exit path — but the
exit is now taken through the interval rather than through the toggle.
``SyncTarget.sync_logos`` DEFAULTS ON (bead ``…-2yq19``): it shipped OFF under
``7ipq2.1``, and because the recorded reason was COST rather than correctness,
ADR-013's faithful-copy principle rules an OFF default a silent omission — a
replica arriving with no artwork at all is the second half of what epic
``f5a5j`` is named for. The cost is answered by ``logo_sync_interval_hours``
(default 24, ``0`` = every cycle) and ``last_logo_sync_at``; see
:func:`logo_slice_is_due`. When the slice runs the LOGO
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
    NON_FATAL_FAILURE_CATEGORIES,
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
from dbas.importers.m3u_accounts import import_m3u_accounts
from credential_sentinel import credential_is_present, strip_redaction_sentinels
from routers import backup as backup_mod
from routers.backup import (
    BACKUP_SCHEMA_VERSION,
    _collect_credential_values,
    _gather_dispatcharr_sections,
    _PROVIDER_IDENTITY_KEYS,
    _REDACT_KEYS,
    _redact_credentials_deep,
    _url_carries_credentials,
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
# The gathered section name for Dispatcharr's core-settings blobs. Declared
# ahead of SYNC_CONFIG_CATEGORIES because that set references it; the per-blob
# register that decides which blobs actually cross is below the set.
_CORE_SETTINGS_SECTION = "core_settings"

SYNC_CONFIG_CATEGORIES: frozenset[str] = frozenset(
    {
        "m3u_accounts",
        "epg_sources",
        "channel_groups",
        "channel_profiles",
        "user_agents",
        "stream_profiles",
        # …-tyrg1: the Dispatcharr ServerGroup an M3U account's ``server_group``
        # FK points at. Gathered so the SERVER_GROUP step ordered ahead of
        # M3U_ACCOUNT has rows to create and a namespace to fill; without it the
        # account's FK could only be dropped and the replica lost the grouping
        # that makes its accounts share a provider connection limit.
        "server_groups",
        # …-10wnq: the core-settings BLOBS ADR-013 S9 has always listed and the
        # engine never carried. Present here so the GATHER fetches them; the
        # plan assembler gives them their own branch, because they are key/value
        # blobs rather than an entity list and are therefore absent from
        # ``_SECTION_TO_ENTITY`` by design. See SYNC_CORE_SETTINGS_BLOBS for the
        # per-blob register and NEVER_SYNC_CORE_SETTINGS_BLOBS for the one
        # exclusion.
        _CORE_SETTINGS_SECTION,
    }
)

# ---------------------------------------------------------------------------
# CORE SETTINGS (bead ``…-10wnq``) — the per-blob register.
# ---------------------------------------------------------------------------
#
# ADR-013 S9 and S3 have listed "core settings" in the per-cycle set since the
# ADR was written, and until this bead ``core_settings`` appeared NOWHERE in
# this engine: not in the category set, not in the step registry, not anywhere.
# It was the last S9 category still missing after bead ``…-hiacv`` added user
# agents.
#
# THE TRAP THIS AVOIDS, named on the bead: core settings are key/value BLOBS and
# are deliberately absent from ``_SECTION_TO_ENTITY`` (which maps sections to
# ENTITY-LIST categories). The plan assembler below iterates that table, so
# adding a category key alone would have been INERT — wired-looking and moving
# nothing. The SETTINGS category is therefore assembled by its own branch, in
# the ``{"section", "values"}`` shape the orchestrator's settings step consumes.
#
# THE SEVEN BLOBS, read off dispatcharr:latest (0.29.0 ``core/models.py``) on
# 2026-08-23 rather than from the recorded fixture, because the fixture is a
# 0.28.2 shape capture with the values stripped. Each entry below states what
# the blob actually contains and why it is in or out.

# REPLICATED. Each was either ruled SYNC by the PO on 2026-08-21 and never
# built, or re-examined here against the 2026-08-22 faithful-copy principle.
SYNC_CORE_SETTINGS_BLOBS: frozenset[str] = frozenset(
    {
        # PO-ruled SYNC 2026-08-21, never built until now.
        #
        # ``stream_settings`` — default_user_agent, default_stream_profile,
        # m3u_hash_key, default_output_format, hdhr_output_profile_id. THREE of
        # those five are instance-local FOREIGN KEY IDS, which the 2026-08-21
        # ruling did not account for and which a blob PATCH would carry
        # SILENTLY: unlike an entity create, Dispatcharr does not validate them,
        # so a raw source id is simply stored and B's defaults quietly point at
        # whichever rows happen to hold those numbers. That is the ``…-9h6cv`` /
        # ``…-g8tyd`` defect class without the 400 that made those two visible.
        # :func:`_remap_stream_settings_fks` handles them.
        "stream_settings",
        # ``dvr_settings`` — recording path templates, comskip flags, pre/post
        # offsets, and ``series_rules``. Operator policy throughout. Note this
        # also makes an existing claim TRUE: ``RESTORABLE_SECTIONS``' dvr_rules
        # comment says SERIES rules are excluded from that category because
        # "core_settings already carries" them — which was not so while
        # core_settings synced nowhere.
        "dvr_settings",
        # ``system_settings`` — time_zone, max_system_events, preferred_region,
        # auto_import_mapped_files, enable_ip_lookup, catchup_enabled. Operator
        # policy throughout, no ids, no addresses.
        "system_settings",
        # TENSION RESOLVED **TOWARD REPLICATION**, and the measurement gap the
        # bead flagged is now CLOSED. ``user_limit_settings`` was excluded on
        # 2026-08-21 for ADJACENCY to the never-sync ``users`` category, with the
        # ADR recording that the real harm ("limits keyed to user accounts that
        # never cross") was UNVERIFIED because the blob is empty on the live
        # instance. It is resolved from the SOURCE rather than from that empty
        # case, exactly as the bead required: 0.29.0 ``core/models.py:756``
        # declares the blob as four GLOBAL BOOLEANS —
        # ``terminate_on_limit_exceeded``, ``prioritize_single_client_channels``,
        # ``ignore_same_channel_connections``, ``terminate_oldest`` — and its
        # only consumers (``apps/proxy/utils.py:148-151, 308-309, 358``) read
        # them as proxy behaviour when a connection limit is breached. There is
        # no user reference in the blob at any depth. The suspected harm does not
        # exist, so under the principle there is nothing left to exclude on.
        "user_limit_settings",
        # TENSION RESOLVED **TOWARD REPLICATION, WITH A PER-TARGET OPT-OUT**.
        # ``proxy_settings`` is buffering_timeout/speed, redis_chunk_ttl,
        # channel_shutdown_delay, channel_init_grace_period,
        # channel_client_wait_period, new_client_behind_seconds. The recorded
        # 2026-08-21 reason was "may legitimately differ if the replica has
        # different hardware" — a PREFERENCE, which the principle rejects as a
        # class. But a real functional risk does exist behind it: tuning copied
        # onto slower hardware can degrade playback on B. The ADR's own reading
        # is that this argues for an opt-out rather than omission, so it
        # replicates by default and an operator who needs B tuned differently
        # names it in ``SyncTarget.core_settings_excluded``.
        "proxy_settings",
        # TENSION RESOLVED **TOWARD REPLICATION, WITH A PER-TARGET OPT-OUT** —
        # and this is the closest call of the three. ``backup_settings`` is
        # schedule_enabled / schedule_frequency / schedule_time /
        # schedule_day_of_week / schedule_cron_expression / retention_count
        # (0.29.0 ``apps/backups/scheduler.py``), and unlike the other two it
        # DOES name a specific harm: both instances then run their backup job on
        # the same schedule, and B's retention count is bounded by B's storage,
        # not A's. Against that: a standby with no backup schedule at all is a
        # replica missing settings, and it is missing them silently. The ADR
        # names two ways out — replicate-with-offset, or a per-target opt-out —
        # and this takes the opt-out, because an OFFSET would have ECM inventing
        # a schedule neither instance was configured with, which is a third
        # state rather than a faithful copy. An operator who does not want B
        # backing itself up names the blob in the same exclusion list.
        "backup_settings",
    }
)

# NEVER REPLICATED — the one exclusion that survives the principle intact.
#
# ``network_access`` is Dispatcharr's per-endpoint CIDR ALLOWLIST
# (0.29.0 ``core/models.py:714``), and ``dispatcharr.utils.network_access_allowed``
# gates every surface it has: the UI, the stream proxy, the Xtream Codes API and
# the M3U/EPG output (measured across ``apps/accounts``, ``apps/timeshift``,
# ``apps/output`` and ``apps/proxy``). Replicating A's allowlist onto a replica
# that sits somewhere else on the network either LOCKS THE OPERATOR OUT OF B or
# OPENS B UP, depending on which way the two differ. That is a specific,
# named harm of the same class as ``dispatcharr_users``, not an unease, and it
# is the ONLY core-settings blob that keeps its exclusion.
#
# Code-enforced rather than merely omitted: :func:`select_core_settings_blobs`
# subtracts this set unconditionally, so a per-target setting cannot opt INTO it.
NEVER_SYNC_CORE_SETTINGS_BLOBS: frozenset[str] = frozenset({"network_access"})


# The failure categories that must NOT roll a REPLICA back (bead …-10wnq).
#
# ``run_restore``'s module default stays exactly as the PO ruled it on
# 2026-08-03 (bead ``…-zt3kf``): on a ONE-SHOT ARCHIVE RESTORE a settings-key
# ``DEPENDENCY_UNRESOLVED`` aborts the whole run and rolls back. That ruling was
# made about an operator action a human watches and can retry.
#
# CONTINUOUS SYNC IS THE OPPOSITE CONTEXT, and this constant is the whole of the
# difference. It runs unattended, forever, on a schedule, with nobody reading
# the result. Three facts make a rollback there strictly destructive:
#
# * A setting is NEVER LEDGERED — ``_delete_dispatch`` has no SETTINGS
#   compensator by design — so the rollback cannot undo the settings. It only
#   deletes the M3U accounts, EPG sources, groups, profiles and channels the
#   cycle successfully created. It fixes nothing and costs everything.
# * The trigger is as small as ONE unreadable ``GET /api/core/settings/`` on the
#   destination, or one blob key a version-skewed B does not have. That is the
#   ``…-d0agi`` trade — a whole replica for a cosmetic config defect — in a
#   category that cannot even be compensated.
# * ADR-013 S8 makes "just retry next interval" the recovery mechanism for this
#   engine. A rollback is precisely what destroys the state that makes retry
#   converge.
#
# The failures are still COUNTED, still in ``failure_details``, and still forbid
# a SUCCESS outcome. Nothing goes silent; the replica just survives.
#
# This became REACHABLE when this bead built the core-settings category — before
# it, ``core_settings`` synced nowhere and the step had nothing to fail on.
SYNC_NON_FATAL_CATEGORIES: frozenset[EntityType] = (
    NON_FATAL_FAILURE_CATEGORIES | frozenset({EntityType.SETTINGS})
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

# Logos are per-SyncTarget and SUB-INTERVALLED (``sync_logos``, default ON since
# bead …-2yq19; ``logo_sync_interval_hours``, default 24 — the ADR-013 S9 exit
# path). Deliberately NOT part of SYNC_ALL_CATEGORIES: the UNCONDITIONAL
# per-cycle set stays exactly what S9 ratified, and the logo slice runs on its
# own slower clock (:func:`logo_slice_is_due`). What changed in …-2yq19 is only
# the DEFAULT — the mechanism decision that kept logos off the every-cycle set
# is untouched, because it is about cost and the principle does not overrule
# cost, only silent omission. When it runs it is NEVER destructive
# (clear_existing is hard-disabled in the sync logos step).
SYNC_LOGO_CATEGORIES: frozenset[str] = frozenset({"logos"})


# ---------------------------------------------------------------------------
# PROVIDER CREDENTIALS CROSS ON EVERY CYCLE (PO ruling 2026-08-22, ADR-013 S3 /
# S12 as amended). This constant is the whole of the exception.
# ---------------------------------------------------------------------------
#
# THE RULING, verbatim: "We should be sending credentials every time so that we
# don't need the user to deal with needing to re-type anything. Any update
# happens as soon as the next scheduled sync occurs." The operator owns both
# instances; a replica whose provider credential is absent or stale is not a
# replica, and the re-typing that closed the gap is the thing being removed.
#
# THIS SUPERSEDES the one-time provisioning action (bead ``wd20y``, PR #908) and
# the setup-only and change-detected cascades considered the same day. There is
# no separate action, no marker column, no version gate and no change detector:
# the credential fields are simply part of what the cycle writes, so rotation on
# A reaches B on the next cycle by construction rather than by mechanism.
#
# WHAT BEAD ``msqf7`` ACTUALLY FORBIDS, and it is not this. That bead was not
# about transmitting a credential; it was about ECM TELLING THE OPERATOR
# credentials were stripped while transmitting them anyway, implicitly, inside
# stream-URL path segments that nothing inspected. Deliberate transmission with
# the product's own words matching the behaviour is the opposite of that defect.
# The operator-facing text was corrected in the same commit as this constant —
# ``docs/user_guide/backup-restore/cross-instance-sync.md``, the sync journal
# row, and the SyncTargets card all now say credentials cross on every cycle.
#
# THE SCOPE IS THE PROVIDER SECTIONS ONLY, and this is the precise half. The
# sections named here are pure third-party provider configuration:
# ``m3u_accounts`` and ``epg_sources``. Everything ELSE the redactor covers is
# redacted exactly as before — ECM's own settings secrets, alert-method secrets,
# cloud-target and sync-target credentials, and ``dispatcharr_users`` (which is
# never synced at all, D3). A new gathered category does NOT inherit this
# exception; it has to be added here, deliberately.
PROVIDER_CREDENTIAL_SECTIONS: frozenset[str] = frozenset(
    {"m3u_accounts", "epg_sources"}
)

# The field names Dispatcharr ACTUALLY exposes for PROVIDER AUTHENTICATION on
# an M3U account or an EPG source (bead ``…-fmtg0``).
#
# READ OFF DISPATCHARR, NOT GUESSED FROM KEY NAMES. Against 0.29.0 source:
# ``apps/m3u/serializers.py`` — ``M3UAccountSerializer.Meta.fields`` carries
# exactly ``username`` and ``password``; ``apps/epg/serializers.py`` —
# ``EPGSourceSerializer.Meta.fields`` carries exactly the same two. Neither
# ``apps/m3u/models.py`` nor ``apps/epg/models.py`` declares an ``api_key``, a
# token or a secret field, and no such name occurs anywhere in either app
# outside its test fixtures. The previous comment here asserted that "an EPG
# source given a URL but no ``api_key`` fetches nothing"; there is no
# ``api_key`` on an EPG source to give it.
#
# THIS IS A LITERAL BECAUSE IT IS A FACT ABOUT DISPATCHARR, not about ECM's
# idea of what a credential is. It changes when Dispatcharr's serializers
# change, which is a deliberate act with a diff to read — not when ECM adds a
# secret of its own. It is pinned in both directions by
# ``tests/tasks/test_fmtg0_provider_credential_key_scope.py``.
_PROVIDER_AUTH_FIELD_NAMES: frozenset[str] = frozenset({"username", "password"})

# The credential-class KEYS carried verbatim inside those sections.
#
# THE INVARIANT: only a key that authenticates to a THIRD-PARTY PROVIDER crosses
# in cleartext; a key that authenticates to ECM itself, or to a service the
# OPERATOR runs downstream, never does — in any section, at any nesting depth.
#
# DERIVED FROM THE REDACTOR'S OWN VOCABULARY, then INTERSECTED with the fields
# that can actually occur on one of these two entities. The derivation is what
# keeps the two rules from drifting apart about what a credential IS: a name
# preserved here has to be one the deep redactor would otherwise sentinel, or
# it is not being redacted anywhere else either. The intersection is what keeps
# the exception from being wider than the ruling: without it the set was 25
# keys — ``smtp_password``, ``plex_token``, ``mcp_api_key``,
# ``dispatcharr_api_key``, ``telegram_bot_token``, ``discord_webhook_url``,
# ``private_key`` and the rest — and none of those authenticates to an IPTV
# provider.
#
# THAT WIDTH WAS REACHABLE, not theoretical. ``preserve_keys`` matches by key
# name at EVERY depth, and bead ``…-vn63c`` measured that Dispatcharr stores
# the provider's ``player_api`` reply VERBATIM in
# ``profiles[].custom_properties`` — a nested blob whose key names ECM does not
# choose. Anything credential-shaped landing in one of those blobs crossed
# preserved rather than sentinelled.
#
# WHAT IS NOT IN HERE AND STILL CROSSES: a plain-M3U account's whole
# ``server_url`` and an authenticated XMLTV source's ``url`` cross because
# :func:`_redact_sync_sections` disables the URL rule for these sections, which
# is a separate mechanism from ``preserve_keys``; and the Schedules Direct
# password is written onto ``password`` AFTER redaction by
# :func:`_inject_schedules_direct_password`.
PROVIDER_CREDENTIAL_KEYS: frozenset[str] = (
    _REDACT_KEYS | _PROVIDER_IDENTITY_KEYS
) & _PROVIDER_AUTH_FIELD_NAMES

# The Dispatcharr EPG ``source_type`` whose password Dispatcharr never returns.
#
# Every other credential on this instance can be read back off A's own records,
# so it needs no operator input at all. Schedules Direct cannot: the serializer
# marks the password write-only, with no admin re-add, and SHA1-hashes it at
# fetch, so the value never enters ECM's process. Absence is UNREADABLE, not
# unset. The operator therefore supplies it ONCE, on the sync target
# (``SyncTarget.schedules_direct_password``, Fernet-encrypted at rest like the
# target's own credentials), and it cascades on every cycle with everything else
# — which is exactly the re-typing the ruling removes.
SCHEDULES_DIRECT_SOURCE_TYPE = "schedules_direct"
SCHEDULES_DIRECT_PASSWORD_FIELD = "password"


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
# applies the same unattended-work bound at its own gather seam. Spending the
# budget is not data loss: the
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
    source_logos: list[dict], *, mirrored_ids: set[int]
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

    CREDENTIAL-BEARING URLS ARE NOW COPIED VERBATIM (PO ruling 2026-08-22 — see
    :data:`PROVIDER_CREDENTIAL_SECTIONS`). Bead …-msqf7 established that a real
    Xtream Codes provider puts the account's username and password in PATH
    SEGMENTS of the addresses it hands out, and a logo url comes from the same
    provider; this gather used to DROP such a url and report the logo as a named
    miss. Under per-cycle credential transmission that drop bought nothing and
    cost the replica its branding — the replica holds the same provider
    credential now, so the address it is handed is one it can actually fetch.
    The scrub is gone rather than neutered, because a scrub whose inputs are
    always empty is a rule that reads as enforcement and enforces nothing.

    Args:
        source_logos: the source Dispatcharr logo rows.
        mirrored_ids: source ids an ECM-LOCAL file record already claims. Those
            keep the local file (it holds real bytes, which is strictly more
            robust than a pointer); emitting a second record for the same id
            would put two records on one LOGO remap entry, and the loser would
            be skipped ``ALREADY_EXISTS_IDENTICAL`` — a claim of sameness about
            images that are not the same.

    Returns:
        Metadata-only records, each carrying the source ``id``, display ``name``
        and ``url``.
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


async def _gather_live_logos() -> list[dict]:
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


def _redact_sync_sections(sections: dict) -> dict:
    """Redact a gathered payload for the wire, PROVIDER CREDENTIALS EXCEPTED.

    TWO PASSES, and the split is the whole of the precision the PO's ruling
    requires — "be precise about which is which, in code and in prose".

    * The PROVIDER sections (:data:`PROVIDER_CREDENTIAL_SECTIONS`) are redacted
      with :data:`PROVIDER_CREDENTIAL_KEYS` preserved AND with the URL-credential
      rule disabled, because a plain-M3U account has no password field at all —
      its whole credential lives inside ``server_url``'s query string, and an
      account that receives a sentinelled ``server_url`` authenticates against
      nothing. The same is true of an authenticated XMLTV EPG ``url``. Every
      other redaction rule still runs over these sections.
    * EVERY OTHER section is redacted exactly as it was before this ruling:
      the full denylist, the identity half, and the URL rule. ECM's own settings
      secrets, alert-method secrets and cloud/sync-target credentials are not
      provider credentials and do not cross.

    THE PATH-SEGMENT RULE IS OFF FOR THE PROVIDER SECTIONS AND UNCHANGED
    EVERYWHERE ELSE. That harvest (bead ``…-msqf7``) exists to find a credential
    hiding in a URL PATH SEGMENT so it can be replaced. In the provider sections
    the credential is carried on purpose, so running it there would sentinel the
    very values the replica needs. Everywhere else it stays exactly as armed as
    it was — note the harvest reads the WHOLE gather, not just the sections it
    is applied to, because the values it looks for LIVE in the provider sections
    and a provider password quoted inside a stream profile's command string is
    still a leak.

    Args:
        sections: the RAW gather, keyed by category.

    Returns:
        A new dict, same keys, redacted per the split above.
    """
    if not isinstance(sections, dict):
        return sections
    provider = {
        key: value
        for key, value in sections.items()
        if key in PROVIDER_CREDENTIAL_SECTIONS
    }
    rest = {
        key: value
        for key, value in sections.items()
        if key not in PROVIDER_CREDENTIAL_SECTIONS
    }
    # Harvested from the WHOLE gather: the credential VALUES live in the
    # provider sections, and narrowing the harvest to ``rest`` would quietly
    # disarm the path-segment rule for every other section by giving it nothing
    # to match against.
    known_secrets, known_identities = _collect_credential_values(sections)
    out = dict(
        _redact_credentials_deep(
            rest,
            preserve_keys=frozenset(),
            known_secrets=known_secrets,
            known_identities=known_identities,
        )
    )
    out.update(
        _redact_credentials_deep(
            provider,
            preserve_keys=PROVIDER_CREDENTIAL_KEYS,
            scrub_credential_urls=False,
        )
    )
    return out


def _inject_schedules_direct_password(sections: dict, password: Optional[str]) -> None:
    """Write the target's stored Schedules Direct password onto its SD sources.

    IN PLACE, on the already-redacted sections, because there is nothing on this
    instance to harvest: Dispatcharr marks the SD password write-only, never
    returns it, and SHA1-hashes it at fetch. It is the ONE credential the
    operator supplies, once, on the sync target — see
    :data:`SCHEDULES_DIRECT_SOURCE_TYPE`.

    Driven by ``source_type``, never by a presence check. A presence check cannot
    work for a field that is never in the gather: absence here means UNREADABLE,
    not unset, and a presence-driven writer would silently skip every SD source
    forever.

    A ``None``/empty password writes nothing at all — omitting the key leaves
    whatever the replica already holds rather than clearing it to empty, which
    would take a working SD source down on the first cycle after an operator
    upgraded without filling the new field in.

    Args:
        sections: the redacted gather; mutated in place.
        password: the target's decrypted SD password, or ``None``.
    """
    if not password:
        return
    rows = sections.get("epg_sources") if isinstance(sections, dict) else None
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("source_type") != SCHEDULES_DIRECT_SOURCE_TYPE:
            continue
        row[SCHEDULES_DIRECT_PASSWORD_FIELD] = password


def credential_bearing_records(plan: ImportPlan) -> list[str]:
    """The operator-facing LABELS of the records this plan carries a credential on.

    The audit half of the ruling (bead ``gad2p``'s surviving invariant): under
    per-cycle transmission the journal row is the only record of what moved, so
    every cycle states WHICH provider records carried a credential field and
    which FIELD NAMES they carried. Names and labels only — no value, no
    fragment of a value, no masked tail of a value.

    Read off the ASSEMBLED PLAN rather than off the sections dict it came from,
    deliberately: the plan is what the orchestrator will actually send, so a
    later assembly step that dropped or rewrote a row cannot leave the audit row
    claiming a credential crossed that did not.

    Args:
        plan: the assembled :class:`ImportPlan`.

    Returns:
        ``["<label> (<field>, <field>)", …]`` in category then document order,
        empty when the cycle carried no credential at all.
    """
    out: list[str] = []
    wanted = (EntityType.M3U_ACCOUNT, EntityType.EPG_SOURCE)
    for category in getattr(plan, "categories", []) or []:
        if getattr(category, "entity_type", None) not in wanted:
            continue
        for row in getattr(category, "entities", []) or []:
            if not isinstance(row, dict):
                continue
            fields = [
                key
                for key in row
                if isinstance(key, str)
                and key.lower() in PROVIDER_CREDENTIAL_KEYS
                and credential_is_present(row.get(key))
            ]
            # A plain-M3U account has no password field at all — its credential
            # is inside the address — so a credential-bearing URL counts as a
            # carried credential in its own right, or the count would report
            # zero for the very account type no field name can describe.
            for url_key in ("server_url", "url"):
                value = row.get(url_key)
                if isinstance(value, str) and _url_carries_credentials(value):
                    fields.append(url_key)
            if not fields:
                continue
            label = row.get("name") or "<unnamed>"
            out.append("%s (%s)" % (label, ", ".join(sorted(set(fields)))))
    return out


def target_schedules_direct_password(sync_target) -> Optional[str]:
    """Decrypt this target's stored Schedules Direct password, or ``None``.

    Fail-soft on a decryption failure (a rotated ``FERNET_KEY``): the cycle
    proceeds and leaves the replica's SD password untouched rather than aborting
    an otherwise-healthy sync over one optional field. The miss is logged.
    """
    stored = getattr(sync_target, "schedules_direct_password", None)
    if not stored:
        return None
    try:
        from cloud_storage.crypto import decrypt_credentials

        decrypted = decrypt_credentials(stored)
    except Exception as exc:  # noqa: BLE001 — an optional field must not abort a cycle
        logger.warning(
            "[SYNC] Could not decrypt the Schedules Direct password for target "
            "%s: %s", getattr(sync_target, "id", "?"), exc.__class__.__name__,
        )
        return None
    if not isinstance(decrypted, dict):
        return None
    value = decrypted.get(SCHEDULES_DIRECT_PASSWORD_FIELD)
    return value if isinstance(value, str) and value else None


def target_excluded_core_settings(sync_target) -> frozenset[str]:
    """The core-settings blobs THIS target's operator opted out of (…-10wnq).

    The per-target opt-out the ADR's reading of ``proxy_settings`` and
    ``backup_settings`` calls for: both replicate by default, and an operator
    with a real reason (B on slower hardware; B should not run its own backup
    job) names the blob instead of losing every other setting with it.

    Stored as a JSON list on ``SyncTarget.core_settings_excluded``. Unreadable or
    absent means "exclude nothing", which is the direction the faithful-copy
    principle points when a value cannot be trusted — the opposite default would
    let a corrupt column silently stop settings replicating.
    """
    raw = getattr(sync_target, "core_settings_excluded", None)
    if not raw:
        return frozenset()
    if isinstance(raw, (list, tuple, set, frozenset)):
        names = raw
    elif isinstance(raw, str):
        import json

        try:
            names = json.loads(raw)
        except (ValueError, TypeError):
            logger.warning(
                "[SYNC] Target %s has an unreadable core_settings_excluded "
                "value; excluding nothing.", getattr(sync_target, "id", "?"),
            )
            return frozenset()
        if not isinstance(names, list):
            return frozenset()
    else:
        return frozenset()
    return frozenset(n for n in names if isinstance(n, str) and n)


def select_core_settings_blobs(
    blob: dict, excluded: frozenset[str] = frozenset()
) -> dict:
    """Narrow a gathered ``core_settings`` map to the blobs that may cross.

    THE CHOKEPOINT. Three rules, applied in this order, and the first two are
    code-enforced rather than conventional:

    1. :data:`NEVER_SYNC_CORE_SETTINGS_BLOBS` is subtracted UNCONDITIONALLY, so
       a per-target setting cannot opt back INTO ``network_access``. Its harm
       (locking the operator out of B, or opening B up) does not become
       acceptable because someone ticked a box.
    2. Only :data:`SYNC_CORE_SETTINGS_BLOBS` members survive. A blob Dispatcharr
       adds in a future release therefore does NOT cross until someone has read
       it and made a decision — the one place in this engine where the
       faithful-copy default is deliberately inverted, because a settings blob's
       content is unknowable in advance and the register is the ADR's whole
       mechanism for "every exclusion is named".
    3. The target's own opt-outs are subtracted last.

    Args:
        blob: the gathered ``core_settings`` map (``{blob key: value}``).
        excluded: this target's opt-outs (:func:`target_excluded_core_settings`).

    Returns:
        A new dict carrying only the blobs that may cross. Empty when the gather
        degraded (a ``{"_warning": ...}`` stub carries no known blob key).
    """
    if not isinstance(blob, dict):
        return {}
    allowed = (SYNC_CORE_SETTINGS_BLOBS - NEVER_SYNC_CORE_SETTINGS_BLOBS) - excluded
    return {key: value for key, value in blob.items() if key in allowed}


# The ``stream_settings`` members that are INSTANCE-LOCAL FOREIGN KEY IDS, and
# the remap namespace each resolves through. Read off dispatcharr:latest
# (0.29.0) on 2026-08-23, not guessed from the key names:
#
# * ``default_user_agent`` -> ``core/models.py:437`` reads it as a ``UserAgent``
#   pk (``UserAgent.objects.get(id=int(ua_id))`` at ``:450``).
# * ``default_stream_profile`` -> ``core/models.py:507``, compared against
#   ``StreamProfile`` ids at ``:512`` / ``:555``.
_STREAM_SETTINGS_FK_FIELDS: dict[str, EntityType] = {
    "default_user_agent": EntityType.USER_AGENT,
    "default_stream_profile": EntityType.STREAM_PROFILE,
}

# The ``stream_settings`` FK with NO namespace to resolve through.
#
# ``hdhr_output_profile_id`` addresses a ``core.models.OutputProfile``
# (0.29.0 ``apps/hdhr/api_views.py:108`` resolves it as one). ECM's DBAS has no
# OutputProfile entity category, so there is nothing on the destination that
# corresponds to A's pk — exactly the position ``server_group`` was in before
# bead ``…-tyrg1``, and it gets the same disposition: DROPPED and REPORTED,
# never forwarded. Dispatcharr treats an unresolvable id as "serve without
# transcoding" (``api_views.py:115``), so an absent value is a valid state.
_STREAM_SETTINGS_UNRESOLVABLE_FK = "hdhr_output_profile_id"


def logo_slice_is_due(sync_target) -> bool:
    """Whether THIS cycle carries the logo slice (bead ``…-2yq19``).

    THE CHANGE THIS IMPLEMENTS. ``sync_logos`` shipped default OFF (bead
    ``7ipq2.1``), so a replica silently arrived with no artwork unless an
    operator found and flipped the toggle — the second half of the failure epic
    ``f5a5j`` is named for. ADR-013's governing principle forbids exactly that
    shape of silent omission, and the recorded reason for OFF was COST, not
    correctness. So the default is ON and the cost is answered here, by a
    throttle, rather than by leaving the replica unbranded.

    WHAT IS AND IS NOT THROTTLED. Only the expensive slice. The config and
    channel categories still run every cycle; the logos importer is the one that
    carries a per-logo streaming upload, and logos are not high-churn state —
    an operator adds artwork occasionally, and a replica that picks it up within
    the sub-interval is faithful in every sense an operator can observe.

    NULL MEANS DUE, deliberately. A freshly-created target has never run the
    slice, so its first cycle carries logos immediately. Making a new replica
    wait out a sub-interval with no artwork at all would reintroduce the very
    window this bead exists to close, just shorter.

    An interval of ``0`` (or negative) means EVERY CYCLE — the pre-throttle
    behaviour, still available to an operator who wants it.

    Args:
        sync_target: the ``SyncTarget`` row (or any object exposing
            ``sync_logos`` / ``logo_sync_interval_hours`` / ``last_logo_sync_at``).

    Returns:
        ``True`` when the slice should be gathered and applied this cycle.
    """
    if not bool(getattr(sync_target, "sync_logos", False)):
        return False
    try:
        interval_hours = int(getattr(sync_target, "logo_sync_interval_hours", 0) or 0)
    except (TypeError, ValueError):
        # An unreadable interval must not silently mean "never". Falling back to
        # every-cycle keeps the replica faithful, which is the direction the
        # principle points when a value cannot be trusted.
        interval_hours = 0
    if interval_hours <= 0:
        return True
    last_run = getattr(sync_target, "last_logo_sync_at", None)
    if last_run is None:
        return True
    from datetime import datetime, timedelta, timezone

    if not isinstance(last_run, datetime):
        return True
    # The column is naive UTC (``datetime.utcnow``-shaped, like every other
    # timestamp on this row), so compare in UTC and tolerate either flavour
    # rather than raising on a tz-aware value some other writer stamped.
    now = datetime.now(timezone.utc)
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)
    return (now - last_run) >= timedelta(hours=interval_hours)


def insecure_transmission_warning(
    sync_target, *, carrying_credentials: bool
) -> Optional[str]:
    """The warning a credential-carrying cycle over unverified TLS produces.

    WHAT THIS REPLACED, and why it is a warning rather than a refusal. Until the
    2026-08-22 ruling this combination was a 409 at the service layer: a target
    with ``insecure=true`` could not be provisioned, and a provisioned target
    could not be set insecure. The PO removed it in their own terms — "I know the
    security risks. That's on the user to mitigate, not us." The operator owns
    both instances; ECM states the exposure plainly and does not block them.

    So the exposure is NAMED rather than prevented, and it is named on every
    cycle that actually carries a credential — not once at setup, because under
    per-cycle transmission the exposure recurs on every cycle too.

    Returns:
        The operator-facing warning, or ``None`` when TLS verification is on or
        this cycle carried no credential.
    """
    if not carrying_credentials:
        return None
    if not bool(getattr(sync_target, "insecure", False)):
        return None
    name = getattr(sync_target, "name", None) or "this sync target"
    return (
        "TLS verification is DISABLED for sync target '%s' (insecure=true) and "
        "this cycle carried your provider credentials to it in clear. Every "
        "cycle carries them, so this exposure repeats on the schedule, not "
        "once. ECM does not block it — that is your call — but the remedy is "
        "one setting: install a valid certificate on the replica and turn TLS "
        "verification back on." % name
    )


async def build_live_source_plan(
    *,
    include_logos: bool = False,
    schedules_direct_password: Optional[str] = None,
    excluded_core_settings: frozenset[str] = frozenset(),
) -> ImportPlan:
    """Gather the LOCAL source-A config, redact it, and assemble an ImportPlan.

    Reuses the backup gather (:func:`_gather_dispatcharr_sections`, which reads
    the LOCAL ``get_client()`` itself) and the shared
    :func:`_redact_credentials_deep` deep redactor. Each gathered section maps to
    its :class:`EntityType` via :data:`_SECTION_TO_ENTITY`.

    **PROVIDER CREDENTIALS CROSS** (PO ruling 2026-08-22 — see
    :data:`PROVIDER_CREDENTIAL_SECTIONS` for the ruling and its exact scope). The
    M3U account and EPG source rows carry their real ``username`` / ``password``
    and their real credential-bearing addresses, and stream URLs carry the
    provider credential their path segments hold, so the replica authenticates
    and serves on the SAME cycle rather than after an operator action. Everything
    else the redactor covers is still redacted before a byte leaves this process.

    The plan's manifest carries ``schema_version = BACKUP_SCHEMA_VERSION`` — the
    orchestrator's pre-flight runs the SAME .17 schema-version gate as archive
    restore and refuses a plan without it (spike ``xp6mp`` empirical find).

    Args:
        include_logos: whether the LOGO slice is due this cycle
            (:func:`logo_slice_is_due` — the per-target ``sync_logos`` flag,
            default ON since bead …-2yq19, gated on
            ``logo_sync_interval_hours``). ``True`` appends a METADATA-ONLY ``LOGO``
            category LAST covering BOTH logo sources — ECM's own upload dir and
            the Dispatcharr-hosted set (bead …-cfxml) — with no ``content_b64``
            in the plan (D8; bytes hydrate lazily per missed logo at import
            time). ``False`` (default) keeps logos out of the plan entirely.
        schedules_direct_password: the target's stored Schedules Direct password,
            already decrypted. ``None`` leaves every SD source's password
            untouched on the replica.
        excluded_core_settings: the core-settings blobs THIS target's operator
            opted out of (bead …-10wnq — the ADR's answer to the
            ``proxy_settings`` / ``backup_settings`` tensions). Subtracted on top
            of the code-enforced :data:`NEVER_SYNC_CORE_SETTINGS_BLOBS`, which
            it can never override.

    Returns:
        An :class:`ImportPlan` of the config categories PLUS the channels
        category (with embedded streams, gathered separately — bead kcxie) PLUS,
        only when ``include_logos`` is set, the metadata-only logos category.
        The ``users`` category is NEVER present (D3). Pass it to
        :func:`credential_bearing_records` for the audit row's account list.
    """
    # Gather ONLY the config categories (never users; never channels/streams/
    # logos — those are other beads). _gather_dispatcharr_sections owns the LOCAL
    # client lookup and returns a ``{"_warning": ...}`` dict (no config rows) when
    # the local Dispatcharr is unavailable — never a crash.
    sections = await _gather_dispatcharr_sections(set(SYNC_CONFIG_CATEGORIES))

    # Redact for the wire, provider credentials excepted (see the helper).
    redacted_sections = _redact_sync_sections(sections)
    _inject_schedules_direct_password(redacted_sections, schedules_direct_password)

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

    # CORE SETTINGS (bead …-10wnq) — a SEPARATE BRANCH, and that is the whole
    # trap the bead named. Settings are key/value BLOBS, so they are absent from
    # ``_SECTION_TO_ENTITY`` by design and the loop above cannot see them: a
    # category key added without this branch would look wired and move nothing.
    #
    # The record shape is ``{"section": "core_settings", "values": {...}}`` —
    # the contract ``restore_orchestrator._settings`` already consumes, so the
    # REUSED settings importer runs unchanged (S1). The category is emitted even
    # when empty, so a fully opted-out target still reports the category rather
    # than silently having none.
    categories.append(
        PlanCategory(
            entity_type=EntityType.SETTINGS,
            entities=[
                {
                    "section": _CORE_SETTINGS_SECTION,
                    "values": select_core_settings_blobs(
                        redacted_sections.get(_CORE_SETTINGS_SECTION) or {},
                        excluded_core_settings,
                    ),
                }
            ],
        )
    )

    # CHANNELS (bead kcxie) — gathered separately (not a config RESTORABLE_SECTION)
    # WITH embedded streams, then redacted through the SAME deep denylist. The
    # CHANNEL category is appended LAST so it applies after every config
    # dependency (groups/profiles/M3U) is created.
    #
    # THE STREAM URL NOW CROSSES WHOLE, and that is what makes the replica serve
    # on this cycle rather than the one after next (beads ``…-2jvvb`` /
    # ``…-5bib5``). A real Xtream Codes provider puts the account's username and
    # password in every stream URL's path segments; bead ``…-msqf7`` replaced
    # those segments with the sentinel, so the replica's channels bound to
    # addresses that resolved to nothing, its own refresh later fetched the real
    # streams as ORPHANS, and only a FURTHER ECM cycle rebound them. Carrying the
    # address intact removes both halves: the channel is bound to a working URL
    # the moment the cycle applies it.
    #
    # ``known_secrets`` is therefore deliberately NOT threaded here, and
    # ``scrub_credential_urls`` is off: both exist to remove the provider
    # credential from an address, which is the value the replica needs. Every
    # KEY-NAMED redaction still runs over the channel rows — a channel or stream
    # record carrying some other secret-named key is still redacted.
    channels = await _gather_live_channels()
    redacted_channels = _redact_credentials_deep(
        {"channels": channels},
        preserve_keys=frozenset(),
        scrub_credential_urls=False,
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
    # A REMOTE-URL logo record carries an ADDRESS (bead …-sgrez) which, on an
    # XC-sourced instance, is where bead …-msqf7 found this operator's username
    # and password. Under the 2026-08-22 ruling that address crosses verbatim:
    # the replica holds the same provider credential, so a url it can fetch is
    # worth more than a named miss.
    if include_logos:
        logo_records = await _gather_live_logos()
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


def remap_stream_settings_fks(
    values: dict, remap: IdRemapTable
) -> tuple[dict, list[str]]:
    """Rewrite ``stream_settings``' instance-local FK ids for the replica (…-10wnq).

    THE SILENT HALF OF THIS BEAD, and the reason its "just sync the blob" ruling
    could not be implemented as written. Three of ``stream_settings``' five
    members are FOREIGN KEY IDS the destination assigns itself:
    ``default_user_agent`` and ``default_stream_profile`` (both remappable —
    ECM syncs those categories) and ``hdhr_output_profile_id`` (not — ECM has no
    ``OutputProfile`` category).

    IT IS SILENT WHERE ITS SIBLINGS WERE LOUD. When bead ``…-9h6cv`` forwarded a
    raw ``user_agent`` pk on an M3U account, B answered
    ``400 {"user_agent": ["Invalid pk \\"4\\" ..."]}`` and the whole apply rolled
    back — painful, but the defect announced itself. A settings blob is a JSON
    value: Dispatcharr stores whatever integer it is handed and validates
    nothing, so forwarding A's ids here would simply point B's default user agent
    and default stream profile at whichever rows happen to hold those numbers.
    No error, no counter, no report — precisely the failure mode this epic keeps
    removing.

    SENTINELS ARE STRIPPED, NOT WRITTEN. The gather runs the deep redactor over
    every non-provider section, so a nested member whose key name looks
    credential-class arrives as the redaction placeholder. Writing that through
    would replace B's real value with the literal string ``***REDACTED***`` —
    the ``…-6pilh`` defect, one layer down. A sentinel-valued member is DROPPED
    instead, so B keeps what it has.

    Args:
        values: the ``stream_settings`` blob as gathered.
        remap: the shared remap table. The SETTINGS step is ordered after
            USER_AGENT and STREAM_PROFILE precisely so both namespaces are
            populated when this runs.

    Returns:
        ``(values, dropped)`` — a NEW blob safe to apply, and the member NAMES
        that were dropped (never their values) so the caller can report the
        degradation.
    """
    if not isinstance(values, dict):
        return {}, []
    cleaned, _sentinels = strip_redaction_sentinels(dict(values))
    dropped: list[str] = []
    for field, namespace in _STREAM_SETTINGS_FK_FIELDS.items():
        if field not in cleaned:
            continue
        source_value = cleaned[field]
        if source_value is None or source_value == "":
            # An explicitly-unset default is a meaningful value, not an id.
            continue
        try:
            source_id = int(source_value)
        except (TypeError, ValueError):
            cleaned.pop(field)
            dropped.append(field)
            continue
        dest_id = remap.resolve(namespace, source_id)
        if dest_id is None:
            cleaned.pop(field)
            dropped.append(field)
            continue
        cleaned[field] = dest_id
    if cleaned.get(_STREAM_SETTINGS_UNRESOLVABLE_FK) not in (None, ""):
        # No OutputProfile category exists to remap through, so this can only be
        # dropped — the ``…-g8tyd`` disposition, reported rather than silent.
        # Dispatcharr treats an unresolvable id as "serve without transcoding",
        # so an absent value is a valid state on B.
        cleaned.pop(_STREAM_SETTINGS_UNRESOLVABLE_FK)
        dropped.append(_STREAM_SETTINGS_UNRESOLVABLE_FK)
    return cleaned, dropped


def _sync_core_settings_step() -> ImporterCallable:
    """Build the SETTINGS importer step for the sync path (bead ``…-10wnq``).

    Reuses ``import_core_settings`` unchanged (S1) and adds exactly one thing the
    archive-restore path does not need: the ``stream_settings`` FK rewrite (see
    :func:`remap_stream_settings_fks`). Archive restore applies a snapshot of the
    SAME instance, where those ids are already correct; cross-instance sync is
    the only caller for which they are not.

    Ordered AFTER USER_AGENT and STREAM_PROFILE in the registry, which is what
    makes the two remap namespaces populated when this runs — the same
    FK-owner-before-dependent rule beads ``…-9h6cv`` and ``…-tyrg1`` follow.
    """

    async def _settings(ctx: ApplyContext) -> list[dict] | None:
        from dbas.importers.settings_agents import (
            CoreSettingIdResolver,
            import_core_settings,
        )

        cat = ctx.plan.category(EntityType.SETTINGS)
        if cat is None:
            return None
        resolver = CoreSettingIdResolver(ctx.client)
        for record in list(cat.entities) or []:
            if not isinstance(record, dict):
                continue
            if record.get("section") != _CORE_SETTINGS_SECTION:
                continue
            values = dict(record.get("values") or {})
            if "stream_settings" in values:
                remapped, dropped = remap_stream_settings_fks(
                    values["stream_settings"], ctx.remap
                )
                values["stream_settings"] = remapped
                for field in dropped:
                    # Field NAMES only. Reported on the preview AND the apply so
                    # the two agree about what the replica will not receive.
                    ctx.report.notes.append(
                        "Stream settings: '%s' points at a row this replica does "
                        "not have, so it was left unset there rather than pointed "
                        "at an unrelated row. Set it on the replica if it "
                        "matters." % field
                    )
            await import_core_settings(
                archive_core_settings=values,
                client=ctx.client,
                selected=bool(cat.selected),
                report=ctx.report,
                ledger=ctx.ledger,
                is_dry_run=ctx.is_dry_run,
                id_resolver=resolver,
            )
        return None

    return _settings


def _sync_m3u_step() -> ImporterCallable:
    """Build the M3U_ACCOUNT importer step for the sync path (bead ``…-zszjd``).

    Identical to the orchestrator's shared ``_m3u`` builder except that it turns
    FIELD CONVERGENCE on: an account that already exists on the replica has its
    fields written toward the source instead of being left frozen at whatever
    they were on the cycle that first created it.

    THE INVARIANT THIS DELIVERS, and it is a property rather than the case that
    exposed it: **a field set on A is the field set on B after the next cycle,
    for every field except those in
    :data:`dbas.importers.m3u_accounts.NEVER_CONVERGE_FIELDS`**, each of which
    names a specific harm or a specific impossibility. Spike ``xp6mp`` ruled an
    existing account ``ALREADY_EXISTS_IDENTICAL`` and never overwritten, which
    covered every field on the row — ``server_url``, ``max_streams``,
    ``user_agent``, ``refresh_interval``, ``custom_properties``, the credential
    fields and the four preference booleans alike. ADR-013 S5 says source-wins.
    The two disagreed, and this step is the reconciliation the bead asked for:
    S5 governs, and every field xp6mp froze is now either converged or carries a
    written exclusion.

    WHY THE FLAG RATHER THAN A UNIFORM CHANGE. The archive-restore registries
    keep the old behaviour, on the same reasoning bead ``…-avrix`` used for its
    ``created: False`` flag. Continuous sync is what turns a frozen field into
    permanent silent divergence — it runs unattended, forever, and nobody looks.
    A one-shot restore onto a populated instance is an operator action with a
    different blast radius and a different question behind it, and answering it
    is not this bead's to decide.

    IT DOES NOT DUPLICATE THE PER-CYCLE CREDENTIAL CASCADE — it COMPLETES it.
    Measured on this branch's base: the cascade puts the real ``username`` /
    ``password`` / credential-bearing address into the PLAN, and the CREATE path
    writes them, but the existing-account branch issued no write at all, so a
    credential rotated on A never reached a replica that already had the
    account. There is exactly one writer for the row, here, and the credential
    fields ride it like every other field.
    """

    async def _m3u(ctx: ApplyContext) -> list[dict] | None:
        cat = ctx.plan.category(EntityType.M3U_ACCOUNT)
        result = await import_m3u_accounts(
            archive_accounts=list(cat.entities) if cat else [],
            client=ctx.client,
            selected=bool(cat.selected) if cat else False,
            report=ctx.report,
            ledger=ctx.ledger,
            remap=ctx.remap,
            is_dry_run=ctx.is_dry_run,
            converge_existing=True,
        )
        return result.deferred_auto_sync_settings or None

    return _m3u


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
    is not an acceptable answer. One budget is
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
        # path. The slice runs on its own sub-interval (bead …-2yq19), and the
        # flag can still be turned off and back on (bead …-8gnik owns the
        # control), so the realistic sequence is unchanged: config cycles run, B
        # gets its lineup, THEN a logo pass comes due. By then every channel on B
        # already exists and is
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

    CRITICAL (ADR-013 S9): the per-cycle provider AUTO-SYNC is never
    re-triggered on B — that is exactly the behaviour S9 forbids. The
    orchestrator is therefore given :func:`_apply_group_selection_only` rather
    than the restore path's ``apply_deferred_auto_sync``: it writes the source
    account's per-group ENABLE selection to B (a pure destination-side upsert)
    and performs none of the three provider-touching steps that follow it there
    — is_active toggle, refresh trigger, stream-count poll.

    This used to be a no-op that dropped the deferred settings on the floor, and
    bead ``…-avrix`` measured what that produced: a replica whose XC account
    held ZERO group rows against the source's 777, which on its own refresh
    ingests either nothing or the provider's whole catalogue depending on a flag
    it inherits. S9 is about not touching the PROVIDER; B's own stored settings
    are not the provider.
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
        # SERVER GROUPS BEFORE M3U ACCOUNTS (…-tyrg1) — the same
        # FK-owner-before-dependent rule the user agents above follow, in the
        # same position the two archive-restore registries put it (…-efvyg: all
        # three registries move together). ``server_groups`` is in
        # SYNC_CONFIG_CATEGORIES so the gather feeds this step.
        ImporterStep(EntityType.SERVER_GROUP, s["server_groups"]),
        # M3U before EPG (EPG sources resolve their m3u_account FK through the
        # remap M3U writes). defers=True: the step DOES return settings for the
        # final phase — but the fn that consumes them there is the
        # group-selection-only apply, never the provider refresh (S9).
        # …-zszjd: the sync path's OWN M3U step, so an account that already
        # exists on the replica converges instead of staying frozen at its
        # first-sync values. See _sync_m3u_step for the invariant and for why
        # the archive-restore registries keep the old behaviour.
        ImporterStep(EntityType.M3U_ACCOUNT, _sync_m3u_step(), defers=True),
        ImporterStep(EntityType.EPG_SOURCE, s["epg"]),
        ImporterStep(EntityType.CHANNEL_GROUP, s["channel_groups"]),
        ImporterStep(EntityType.CHANNEL_PROFILE, s["channel_profiles"]),
        ImporterStep(EntityType.STREAM_PROFILE, s["stream_profiles"]),
        # CORE SETTINGS AFTER THE TWO CATEGORIES ITS FKs RESOLVE THROUGH
        # (…-10wnq). ``stream_settings`` carries ``default_user_agent`` and
        # ``default_stream_profile``, both instance-local pks, so this step has
        # to run once USER_AGENT and STREAM_PROFILE have filled their namespaces
        # — the same FK-owner-before-dependent rule …-9h6cv and …-tyrg1 follow.
        # Unlike those two the failure would be SILENT: Dispatcharr stores a
        # settings blob's integers without validating them.
        ImporterStep(EntityType.SETTINGS, _sync_core_settings_step()),
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


async def _apply_group_selection_only(
    *, deferred: list[dict], client, remap=None, report=None
) -> list[dict]:
    """Deferred-apply for the sync path: the GROUP SELECTION, never the refresh.

    ADR-013 S9 forbids re-triggering the destination's provider auto-sync every
    cycle. It does NOT forbid writing the destination's own stored settings, and
    this fn is the line between the two: it applies the source account's
    per-group ENABLE selection (a pure destination-side upsert — see
    :func:`dbas.importers.m3u_accounts._apply_one_group_selection`) and performs
    none of the three provider-touching steps the restore path's
    ``apply_deferred_auto_sync`` goes on to do (is_active toggle, refresh
    trigger, stream-count poll).

    WHY THIS REPLACED A NO-OP (bead ``…-avrix``). Dropping the settings on the
    floor was measured live on 2026-08-21: the replica's XC account held ZERO
    ``ChannelGroupM3UAccount`` rows against the source's 777 (2 enabled), and
    ``channel_group_drift`` reported ``0`` throughout because it measures which
    group a CHANNEL sits in, not which of an ACCOUNT's groups are switched on.
    Given credentials, the replica's own refresh then answered ``Filtered 0
    streams from 0 enabled categories`` and aborted — 0 streams against the
    source's 316 — and with ``auto_enable_new_groups_live`` at Dispatcharr's own
    default of ``True`` the same empty selection enabled 777 of 777 categories
    instead, i.e. the provider's entire 53,661-stream catalogue. The invariant
    this restores: a replica ingests the same provider content the source
    ingests, or the run says plainly that it will not.

    RETURNS ``[]`` DELIBERATELY. The orchestrator reads this fn's return value
    as "how many accounts had their deferred AUTO-SYNC applied" and renders it
    as ``deferred auto-sync applied for N account(s)``. On this path the honest
    answer is still none — no auto-sync ran. What DID happen is reported by the
    apply itself, through ``report.record_provider_group_selection`` and the
    action-item clause it drives, so nothing is hidden by returning ``[]``.
    """
    if not deferred:
        return []
    from dbas.importers.m3u_accounts import apply_group_selection

    summaries = await apply_group_selection(
        deferred=deferred, client=client, remap=remap, report=report
    )
    logger.info(
        "[SYNC] Applied the provider group selection for %d account(s) "
        "(%d selection(s), %d enabled); no provider refresh was triggered "
        "(ADR-013 S9).",
        len(summaries),
        sum(s.get("groups_applied", 0) for s in summaries),
        sum(s.get("groups_enabled", 0) for s in summaries),
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

    Records target id, the config categories + their counts, the result, the
    redaction mode, AND what provider credentials this cycle carried. Best-effort
    — a journal failure must not crash the sync. Only SAFE fields (names, counts,
    outcome, FIELD names) are logged; never a credential value.

    THE REDACTION MODE IS NOW ``topology_plus_provider_credentials``, and the
    rename is not cosmetic. This row said ``topology_only`` while bead
    ``…-msqf7`` was live and the cycle was carrying the provider's username and
    password inside every stream URL — the audit row asserting the exact thing
    that was false. Under the 2026-08-22 ruling the cycle carries them
    deliberately, so the row says so, on every run, with the affected records
    named. That is the whole of what ``msqf7`` requires of this feature: the
    product's words match the behaviour.

    ``credential_records`` / ``credential_count`` are the surviving form of bead
    ``…-gad2p``'s invariant — no credential-carrying attempt terminates without
    an audit row. The two failure routes that bead measured (a gate refusal with
    no row, a de-provision against an unreachable destination with no row) do not
    exist any more: the gate and the de-provision action were both deleted. What
    replaced them is that EVERY terminal route of a cycle writes this row — the
    freshness abort, the destination-unreadable abort, the completed apply and
    the completed dry run — and each one states what it carried.
    """
    try:
        credential_records = list(
            getattr(report, "provider_credential_transmission_details", []) or []
        )
        tls_verified = not bool(getattr(target, "insecure", False))
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
                "Cross-instance sync run (mode=%s, "
                "redaction_mode=topology_plus_provider_credentials, "
                "categories=%s, provider_credentials_transmitted=%d, "
                "tls_verified=%s)" % (
                    result, ran_categories, len(credential_records), tls_verified,
                )
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
                "redaction_mode": "topology_plus_provider_credentials",
                "result": result,
                "counts": counts,
                # What moved, named. Labels and FIELD names only.
                "provider_credentials_transmitted": len(credential_records),
                "provider_credential_records": credential_records,
                "tls_verified": tls_verified,
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
    # logos slice is per-target (sync_logos, default ON since …-2yq19) and runs
    # on its own SUB-INTERVAL rather than every cycle; when it runs, the plan
    # gains a METADATA-ONLY logo category (bytes hydrate lazily at import time,
    # misses only — D8). ---
    include_logos = logo_slice_is_due(sync_target)
    # The provider credential crosses on THIS cycle and every cycle (PO ruling
    # 2026-08-22 — PROVIDER_CREDENTIAL_SECTIONS). The one value that cannot be
    # harvested off A is the Schedules Direct password, which the operator
    # supplied once on the target and which is decrypted here, per run, and
    # never held anywhere else.
    plan = await build_live_source_plan(
        include_logos=include_logos,
        schedules_direct_password=target_schedules_direct_password(sync_target),
        # …-10wnq: the per-target core-settings opt-out. It narrows an
        # already-narrowed set — NEVER_SYNC_CORE_SETTINGS_BLOBS is subtracted
        # first and unconditionally, so this can never opt INTO network_access.
        excluded_core_settings=target_excluded_core_settings(sync_target),
    )
    credential_records = credential_bearing_records(plan)
    for detail in credential_records:
        report.record_provider_credential_transmission(detail)
    insecure_warning = insecure_transmission_warning(
        sync_target, carrying_credentials=bool(credential_records)
    )
    if insecure_warning:
        logger.warning("[SYNC] %s", insecure_warning)
        report.notes.append(insecure_warning)

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
        # ADR-013 S9 — the destination's group SELECTION is applied; its
        # provider refresh is not triggered. See _apply_group_selection_only.
        deferred_apply_fn=_apply_group_selection_only,
        ledger_dir=ledger_dir,
        allow_fuzzy_stream_match=allow_fuzzy,
        # …-10wnq: a SETTINGS failure must not roll the replica back on the sync
        # path. See SYNC_NON_FATAL_CATEGORIES for the reasoning and for why the
        # archive-restore path deliberately keeps the PO's zt3kf ruling.
        non_fatal_categories=SYNC_NON_FATAL_CATEGORIES,
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
            # The logo sub-interval clock (bead …-2yq19) starts when the slice
            # ACTUALLY RAN, not when a cycle merely happened. Stamped on any
            # realized apply that carried the slice, including a degraded one:
            # the expensive work was paid, and re-paying it on the very next
            # cycle because one unrelated category failed is the cost this
            # throttle exists to avoid. Logo MISSES are already their own
            # reported counter (``logo_misses``), so nothing goes silent here —
            # and the next scheduled pass retries them, because the importer
            # matches what is already on B and hydrates only what is not.
            if include_logos:
                sync_target.last_logo_sync_at = datetime.now(timezone.utc)
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
