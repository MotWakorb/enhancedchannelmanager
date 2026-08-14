"""The ``dispatcharr_users`` restore importer — crown-jewel restore.

Bead ``enhancedchannelmanager-l1p4p``. This is the most SECURITY-SENSITIVE
importer in the Phase-2 epic: Dispatcharr user accounts are a
privilege-escalation surface, so this module is conservative-by-default and
fails closed.

Naming: this restores the DISPATCHARR users category (Django auth, pbkdf2),
labelled ``dispatcharr_users`` everywhere it surfaces. It is DISTINCT from ECM's
own users table (bcrypt, ``models.py``); do not conflate them.

MANDATORY security policy (spike ``tsfv0``, live-confirmed vs Dispatcharr
0.26.0 — DO NOT deviate):

1. **No ARCHIVE password/hash ever crosses the boundary.** Dispatcharr's User
   serializer exposes ``password`` as a WRITE-ONLY plaintext field; there is no
   ``password_hash`` field and a GET never returns a hash. The source instance's
   password is unrecoverable and is never carried, derived, or rehashed — the
   archive's ``password`` / ``password_hash`` keys are dropped and never read.

   Each restored user is instead created with a **freshly generated random
   password** (:func:`_generate_password`, :mod:`secrets`) that ECM discards
   immediately: it is never logged, never written to the report, never stored.
   The account is therefore still unusable until the operator sets a password
   out-of-band, and is flagged **force-reset** in the report notes.

   Why send one at all: Dispatcharr 0.28.2's user-create serializer reads
   ``validated_data['password']`` unconditionally, so a create without the key
   raises an uncaught ``KeyError`` and returns HTTP 500. That 500 aborted the
   entire restore on every real disaster-recovery rebuild — the archived
   superuser's name never matches the rebuilt instance's, so the category tries
   to CREATE rather than skip (bead ``enhancedchannelmanager-y65si``).

2. **Privilege flags are the real escalation surface** (``is_superuser``,
   ``is_staff``, ``user_level`` — all writable). We restore them
   CONSERVATIVELY: every restored user is forced NON-privileged regardless of
   what the archive claims. The archive's superuser/staff/level bits are dropped.

3. **The current operator is NEVER overwritten/deleted/disabled/demoted.** The
   operator is identified by AUTH SUBJECT — ``client.get_current_user()`` reads
   ``/api/accounts/users/me/`` and returns whoever the credentials authenticate
   as — NOT by a username or id from the archive (a cross-instance restore
   remaps ids, and the archive may carry a colliding username). A restored user
   that collides with the operator's identity is SKIPPED with
   ``SkipReason.CURRENT_ADMIN_PRESERVED``; the operator row is never touched.

4. **Username uniqueness** — a non-operator collision with an existing
   destination user is skipped ``ALREADY_EXISTS_IDENTICAL`` (never overwritten);
   a create that races into a conflict is failed ``CONFLICT``.

5. **Opt-in** — the category does nothing unless the operator selected it
   (``selected``).

6. **Capability check fails CLOSED.** Before creating anything, the importer
   reads the User-create write fields from ``/api/schema/``. If a
   ``password_hash`` (or similar pre-hashed-password) write field ever appears —
   a future Dispatcharr that accepts pre-hashed passwords — the importer raises
   ``UsersCapabilityError`` and creates nothing, rather than silently begin
   writing hashes. An unparseable schema is also treated as "cannot confirm
   safety" and fails closed.

7. **Audit/report carries usernames ONLY** — never a hash, never a password.

8. **api_key auth mode** — the importer issues only the calls above, which work
   under api_key mode (Dispatcharr 0.23.0+ throttles password-mode login at
   3/min IP-shared, breaking multi-call password-mode restore).

Integration with the restore contracts (bead ``kxuj2``): results go into the
shared :class:`~dbas.restore_contracts.RestoreReport`
(``EntityType.USER`` category), and every created user is recorded in the
:class:`~dbas.restore_contracts.RollbackLedger` so a later failure compensates
by deleting them. This importer imports the contracts module READ-ONLY.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable

from dbas.restore_contracts import (
    EntityType,
    FailureDetail,
    FailureReason,
    RestoreReport,
    RollbackLedger,
    SkipDetail,
    SkipReason,
)
from dispatcharr_client import DispatcharrClient

logger = logging.getLogger(__name__)

# The operator-facing label for this category. Distinct from ECM's own users.
CATEGORY_LABEL = "dispatcharr_users"

# Write fields that, if present in the User-create schema, mean Dispatcharr would
# accept a pre-computed/pre-hashed password. Their appearance fails the category
# closed (policy item 6) — we must never silently start writing a hash.
_FORBIDDEN_SCHEMA_WRITE_FIELDS = frozenset(
    {"password_hash", "hashed_password", "password_hashes"}
)

# Keys never forwarded to create_user: secret material (defense in depth — the
# client also strips these) and the archive's privilege bits, which we override
# conservatively rather than trust (policy item 2).
_DROPPED_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "hashed_password",
        "is_superuser",
        "is_staff",
        "is_active",
        "user_level",
        # Archive-source identifiers that the destination assigns itself.
        "id",
        "pk",
        "last_login",
        "date_joined",
    }
)

# Conservative, NON-privileged defaults forced onto every restored user. These
# OVERRIDE whatever the archive claimed (policy item 2). ``user_level`` 0 is the
# lowest Dispatcharr privilege level (standard user).
_CONSERVATIVE_PRIVILEGE_DEFAULTS = {
    "is_superuser": False,
    "is_staff": False,
    "user_level": 0,
}


# Bytes of entropy per generated password. 32 bytes -> a 43-character
# URL-safe token; far beyond anything Django's password validators reject and
# beyond brute force. The value is generated, sent, and forgotten.
_GENERATED_PASSWORD_BYTES = 32


def _generate_password() -> str:
    """Return a fresh, cryptographically random password for ONE restored user.

    Policy item 1 (bead ``…-y65si``): Dispatcharr's serializer requires a
    ``password`` key, so ECM supplies one that it does not keep. The value is
    never derived from archive material, never reused across users, never
    logged, and never recorded in the report or the ledger — the operator resets
    the account out-of-band.
    """
    return secrets.token_urlsafe(_GENERATED_PASSWORD_BYTES)


class UsersCapabilityError(RuntimeError):
    """Raised when the destination's User schema is unsafe for restore.

    Carries a sanitized message naming the offending capability (e.g. the field
    name) but NEVER any secret material. When raised, the importer has created
    nothing — the category fails CLOSED.
    """


def _sanitize(message: str) -> str:
    """Best-effort scrub of a message destined for an operator-facing surface.

    The importer never deliberately puts a secret in a message, but upstream
    error bodies can echo a field value. This keeps the report free of obvious
    secret markers; usernames pass through unchanged.
    """
    return (message or "").strip()


def _build_create_payload(archive_user: dict, write_fields: set[str]) -> dict:
    """Build the create_user payload from an archive user record (ALLOWLIST).

    Keys are admitted only when they are BOTH (a) in the destination's parsed
    User-create write-field set (``write_fields`` from ``/api/schema/``) AND
    (b) not in ``_DROPPED_KEYS`` (secret/privilege/source-id material, dropped as
    defense-in-depth even if the schema would accept them). An unknown archive
    key the schema does not list is silently dropped rather than forwarded — an
    allowlist intersected with the schema's write-fields, not a denylist of known
    bad keys (bead l1p4p, follow-up 2). The privilege fields are then forced to
    the conservative non-privileged defaults regardless of the archive.

    The result NEVER carries a password, a hash, or an elevated privilege flag.

    Args:
        archive_user: One user record from the export archive.
        write_fields: The destination's writable User-create field names.

    Returns:
        The create payload — schema-known, non-secret keys plus the forced
        conservative privilege defaults.
    """
    payload = {
        k: v
        for k, v in archive_user.items()
        if k in write_fields and k not in _DROPPED_KEYS
    }
    payload.update(_CONSERVATIVE_PRIVILEGE_DEFAULTS)
    return payload


async def _assert_capability(client: DispatcharrClient) -> set[str]:
    """Fail CLOSED unless the destination User-create schema is safe.

    Reads the writable User-create fields from ``/api/schema/``. Raises
    :class:`UsersCapabilityError` if a forbidden pre-hashed-password write field
    is present, OR if no write fields could be parsed at all (cannot confirm
    safety). The message names the issue but carries no secret.

    Returns:
        The set of writable User-create field names — the allowlist
        :func:`_build_create_payload` intersects the archive keys against.
    """
    try:
        write_fields = await client.get_user_schema_write_fields()
    except Exception as exc:  # pragma: no cover - defensive; schema fetch error
        logger.warning("[DBAS-USERS] Could not read User schema for capability check: %s", exc)
        raise UsersCapabilityError(
            "Cannot confirm Dispatcharr User schema is safe for restore "
            "(schema unavailable); failing the dispatcharr_users category closed."
        ) from exc

    write_fields = set(write_fields)
    forbidden = sorted(_FORBIDDEN_SCHEMA_WRITE_FIELDS & write_fields)
    if forbidden:
        logger.error(
            "[DBAS-USERS] Capability check FAILED CLOSED: forbidden write field(s) %s "
            "in User schema — refusing to restore dispatcharr_users.",
            forbidden,
        )
        raise UsersCapabilityError(
            "Dispatcharr User schema exposes a pre-hashed-password write field "
            f"({', '.join(forbidden)}); failing the dispatcharr_users category "
            "closed to avoid writing password material."
        )

    if not write_fields:
        logger.error(
            "[DBAS-USERS] Capability check FAILED CLOSED: User-create schema had no "
            "parseable write fields — cannot confirm safety."
        )
        raise UsersCapabilityError(
            "Could not parse the Dispatcharr User-create schema; failing the "
            "dispatcharr_users category closed (cannot confirm safety)."
        )

    return write_fields


def _operator_username(operator: dict) -> str | None:
    """Return the auth subject's username, or None if unknowable.

    The username reported by ``/api/accounts/users/me/`` is the stable
    cross-instance identifier for the current operator. (The id is NOT — it is
    remapped per instance.)
    """
    username = operator.get("username")
    return username if isinstance(username, str) and username else None


async def import_users(
    *,
    archive_users: list[dict],
    client: DispatcharrClient,
    selected: bool,
    report: RestoreReport,
    ledger: RollbackLedger,
    is_dry_run: bool = False,
    persist_ledger: Callable[[], None] | None = None,
) -> None:
    """Restore the ``dispatcharr_users`` category into the destination instance.

    Args:
        archive_users: The user records from the export archive. Each is a dict;
            any password/hash/privilege fields are dropped, never trusted.
        client: The Dispatcharr API client (used in api_key mode).
        selected: The per-category opt-in flag. When ``False`` the entire
            category is skipped (policy item 5) — no schema check, no creates.
        report: The shared :class:`RestoreReport`; results land in the
            ``EntityType.USER`` category.
        ledger: The shared :class:`RollbackLedger`; each created user is recorded
            for compensating deletes.
        is_dry_run: When ``True``, no user is created — the importer only reports
            ``would_create`` / ``would_skip`` so the operator sees the plan.
        persist_ledger: Optional durable-flush callback (wired by the
            orchestrator to ``persist_ledger``). Called IMMEDIATELY after each
            created user is recorded in the ledger and BEFORE the next create, so
            a mid-category crash leaves a recoverable record of every user
            created so far (RollbackLedger durability contract — bead l1p4p). On
            a dry-run or when ``None`` it is not invoked.

    Raises:
        UsersCapabilityError: when the destination User schema is unsafe
            (policy item 6). Nothing is created when this is raised.
    """
    cat = report.category(EntityType.USER)

    # Policy 5 — OPT-IN. Off unless the operator selected dispatcharr_users.
    if not selected:
        logger.info("[DBAS-USERS] Category not selected; skipping dispatcharr_users.")
        for archive_user in archive_users:
            label = str(archive_user.get("username", "<unknown>"))
            _skip(cat, SkipReason.EXCLUDED_BY_OPERATOR, label, archive_user.get("id"), is_dry_run)
        return

    # Policy 6 — capability check FAILS CLOSED before anything is created. It
    # also returns the schema write-field allowlist the payload builder uses.
    write_fields = await _assert_capability(client)

    # Policy 3 — identify the current operator by AUTH SUBJECT, not archive data.
    operator = await client.get_current_user()
    operator_username = _operator_username(operator)
    logger.info(
        "[DBAS-USERS] Restoring dispatcharr_users (dry_run=%s); operator subject=%s",
        is_dry_run,
        operator_username,
    )

    # Policy 4 — pre-fetch existing usernames to detect non-operator collisions.
    try:
        existing = await client.get_users()
    except Exception as exc:
        logger.warning("[DBAS-USERS] Could not list existing users: %s", exc)
        existing = []
    existing_usernames = {
        u.get("username")
        for u in existing
        if isinstance(u, dict) and u.get("username")
    }

    for archive_user in archive_users:
        username = archive_user.get("username")
        label = str(username) if username else "<unknown>"

        # Policy 3 — never touch the operator row. Match by auth-subject username.
        if operator_username is not None and username == operator_username:
            logger.info(
                "[DBAS-USERS] Skipping archive user '%s' — collides with current "
                "operator (preserved by auth subject).",
                label,
            )
            _skip(cat, SkipReason.CURRENT_ADMIN_PRESERVED, label, archive_user.get("id"), is_dry_run)
            continue

        # Policy 4 — non-operator username already on the destination: never
        # overwrite; skip already-exists.
        if username in existing_usernames:
            _skip(cat, SkipReason.ALREADY_EXISTS_IDENTICAL, label, archive_user.get("id"), is_dry_run)
            continue

        if is_dry_run:
            cat.would_create += 1
            continue

        # Policy 1 + 2 — payload is an allowlist (schema write-fields ∩ archive),
        # omits the archive's password/hash, forces non-privileged. The password
        # travels as a separate argument so it can never be confused with, or
        # sourced from, archive material.
        payload = _build_create_payload(archive_user, write_fields)
        generated_password = _generate_password()
        try:
            created = await client.create_user(payload, password=generated_password)
        except Exception as exc:
            reason = _failure_reason_for(exc)
            cat.failed += 1
            cat.failure_details.append(
                FailureDetail(
                    reason=reason,
                    label=label,
                    message=_sanitize_failure(exc, generated_password),
                    source_export_id=archive_user.get("id"),
                )
            )
            logger.warning("[DBAS-USERS] Failed to restore user '%s': %s", label, reason.value)
            continue

        dest_id = created.get("id") if isinstance(created, dict) else None
        cat.created += 1
        if dest_id is not None:
            ledger.record_created(EntityType.USER, int(dest_id), label)
            # Durability contract (bead l1p4p): flush the ledger to disk NOW,
            # before the next create is issued, so a mid-category crash leaves a
            # recoverable record of every user created so far — not only those
            # from completed steps. No-op on dry-run / when unwired.
            if persist_ledger is not None:
                persist_ledger()
        # Policy 1 — flag force-reset (usernames only; no secret). The generated
        # password is discarded here: ECM never recorded it, so nobody — operator
        # or attacker — can log into the restored account until it is reset.
        report.notes.append(
            f"{CATEGORY_LABEL}: '{label}' restored with a randomly generated "
            "password that ECM does not record — force-reset required (the "
            "operator must set a new password out-of-band before this account "
            "can be used)."
        )
        logger.info("[DBAS-USERS] Restored user '%s' (id=%s); flagged force-reset.", label, dest_id)


def _skip(
    cat,
    reason: SkipReason,
    label: str,
    source_export_id,
    is_dry_run: bool,
) -> None:
    """Record a skip in both the count and the reasoned detail list."""
    if is_dry_run:
        cat.would_skip += 1
    else:
        cat.skipped += 1
    cat.skip_details.append(
        SkipDetail(reason=reason, label=label, source_export_id=source_export_id)
    )


def _failure_reason_for(exc: Exception) -> FailureReason:
    """Classify a create_user failure into a restore-contract FailureReason.

    A username/uniqueness conflict (the create_user error body echoing
    "already exists" / "unique") maps to ``CONFLICT``; everything else is an
    upstream API error.
    """
    text = str(exc).lower()
    if (
        "already exists" in text
        or "unique" in text
        or ("username" in text and "exists" in text)
    ):
        return FailureReason.CONFLICT
    return FailureReason.UPSTREAM_API_ERROR


def _sanitize_failure(exc: Exception, generated_password: str | None = None) -> str:
    """Produce a sanitized, operator-facing failure message with no secrets.

    A Dispatcharr error CAN echo request-body material (e.g. an Authorization
    header, a token, an api_key field) verbatim. Route the message through the
    shared credential masker so any such echoed secret is redacted before it
    reaches the operator-facing :class:`FailureDetail` (bead l1p4p, follow-up 3 —
    defense in depth).

    ``generated_password`` is the value this create sent (bead ``…-y65si``). A
    DRF validation error normally echoes field NAMES, not values, but a
    high-entropy random token is not something the generic masker can pattern-
    match — so it is removed by exact substring before anything else runs. It
    must never reach the report: nobody is meant to be able to recover it.
    """
    # Lazy import: keep the dbas importer free of a cloud_storage dependency at
    # module-import time (same pattern as tasks.dbas_backup). redact_secrets is the
    # canonical credential masker (precompiled patterns; never raises).
    from cloud_storage.upload_security import redact_secrets

    text = _sanitize(str(exc))
    if generated_password:
        text = text.replace(generated_password, "***")
    return redact_secrets(text) or "Upstream rejected the user creation request."
