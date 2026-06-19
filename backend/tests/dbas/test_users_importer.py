"""Tests for the dispatcharr_users restore importer
(enhancedchannelmanager-l1p4p — crown-jewel restore, the most
security-sensitive bead in the Phase-2 epic).

Security policy under test (spike tsfv0, live-confirmed vs Dispatcharr 0.26.0):

1. No password/hash ever crosses the boundary — create with password omitted ->
   unusable password; flag force-reset. Never fabricate/derive/rehash.
2. Privilege flags (is_superuser/is_staff/user_level) restored CONSERVATIVELY:
   default non-privileged; the archive's superuser bit is NEVER trusted.
3. The current operator is identified by AUTH SUBJECT (/api/accounts/users/me/),
   never by username/id. A restored user that collides with the operator's
   identity is SKIPPED with SkipReason.CURRENT_ADMIN_PRESERVED — the operator
   row is never touched.
4. Username collisions (other than the operator) are skipped
   already_exists_identical / failed CONFLICT per the restore contract.
5. The category is OPT-IN — off unless the operator selects dispatcharr_users.
6. Capability check: if a password_hash WRITE field ever appears in the User
   schema, fail the category CLOSED.
7. Audit/report rows carry usernames ONLY — never hashes, never passwords.
8. Rollback ledger records every created user for compensation.

The Dispatcharr client is mocked at the importer module level
(``dbas.importers.users``); the importer is exercised with an AsyncMock client.
"""
import pytest
from unittest.mock import AsyncMock

from dbas.importers.users import import_users, UsersCapabilityError
from dbas.restore_contracts import (
    EntityType,
    FailureReason,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client(
    *,
    me=None,
    existing_users=None,
    schema_fields=None,
    create_side_effect=None,
):
    """Build an AsyncMock Dispatcharr client with the methods the importer uses."""
    client = AsyncMock()
    client.get_current_user = AsyncMock(
        return_value=me or {"id": 1, "username": "operator"}
    )
    client.get_users = AsyncMock(return_value=existing_users or [])
    client.get_user_schema_write_fields = AsyncMock(
        return_value=set(schema_fields) if schema_fields is not None
        else {"username", "email", "is_superuser", "is_staff", "user_level"}
    )
    # create_user returns a created body with an assigned id by default.
    created_counter = {"n": 100}

    async def _default_create(payload):
        created_counter["n"] += 1
        return {"id": created_counter["n"], **payload}

    client.create_user = AsyncMock(
        side_effect=create_side_effect or _default_create
    )
    client.delete_user = AsyncMock(return_value=None)
    return client


def _report():
    return RestoreReport(is_dry_run=False)


def _ledger():
    return RollbackLedger(restore_id="test-restore")


# ---------------------------------------------------------------------------
# Opt-in gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_category_skipped_when_not_selected():
    """OPT-IN: when the operator did not select dispatcharr_users, the whole
    category is skipped — no schema check, no create, nothing touched."""
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "alice", "is_superuser": True}],
        client=client,
        selected=False,
        report=report,
        ledger=ledger,
    )

    client.create_user.assert_not_awaited()
    client.get_current_user.assert_not_awaited()
    cat = report.category(EntityType.USER)
    assert cat.created == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.EXCLUDED_BY_OPERATOR
    assert cat.skip_details[0].label == "alice"
    assert ledger.entries == []


# ---------------------------------------------------------------------------
# Capability check — fail CLOSED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_check_fails_closed_when_password_hash_write_field_present():
    """If a password_hash WRITE field appears in the User schema, the category
    fails CLOSED: no users are created, and the failure carries no secret."""
    client = _client(schema_fields={"username", "password_hash"})
    report = _report()
    ledger = _ledger()

    with pytest.raises(UsersCapabilityError) as exc_info:
        await import_users(
            archive_users=[{"id": 5, "username": "alice"}],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
        )

    client.create_user.assert_not_awaited()
    # The error message names the offending capability but carries no secret
    # material (no hash value, no password).
    msg = str(exc_info.value).lower()
    assert "password_hash" in msg
    assert "pbkdf2" not in msg


@pytest.mark.asyncio
async def test_capability_check_fails_closed_when_schema_unparseable():
    """An empty/unparseable schema (no recognizable write fields) is treated as
    'cannot confirm safety' and also fails the category closed."""
    client = _client(schema_fields=set())
    report = _report()
    ledger = _ledger()

    with pytest.raises(UsersCapabilityError):
        await import_users(
            archive_users=[{"id": 5, "username": "alice"}],
            client=client,
            selected=True,
            report=report,
            ledger=ledger,
        )
    client.create_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_capability_check_passes_with_normal_schema():
    """A normal schema (username + privilege flags, no hash field) passes the
    capability gate and allows the restore to proceed."""
    client = _client()  # default schema has no password_hash
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "alice"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )
    client.create_user.assert_awaited()
    assert report.category(EntityType.USER).created == 1


# ---------------------------------------------------------------------------
# MANDATED TEST: colliding-username-does-not-touch-operator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_colliding_username_does_not_touch_operator():
    """The archive carries a user whose username == the current operator's, but
    with a DIFFERENT (remapped) id. The operator is matched by auth subject
    (/users/me/), so the colliding archive user is SKIPPED with
    CURRENT_ADMIN_PRESERVED — and the operator row is never created/updated/
    deleted (no create_user, no delete_user)."""
    operator = {"id": 1, "username": "admin", "is_superuser": True}
    client = _client(me=operator)
    report = _report()
    ledger = _ledger()

    # Archive's "admin" has a different source id (cross-instance remap) and even
    # claims superuser — must be ignored entirely.
    await import_users(
        archive_users=[{"id": 999, "username": "admin", "is_superuser": True}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    client.create_user.assert_not_awaited()
    client.delete_user.assert_not_awaited()
    cat = report.category(EntityType.USER)
    assert cat.created == 0
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.CURRENT_ADMIN_PRESERVED
    assert cat.skip_details[0].label == "admin"
    assert ledger.entries == []


@pytest.mark.asyncio
async def test_operator_matched_by_subject_not_by_archive_id():
    """Even if an archive user shares the operator's *id* but a different
    username, OR shares the username but not the id, the operator is preserved.
    Matching is by username-of-the-auth-subject (the stable cross-instance
    identifier returned by /users/me/), never by the archive's remappable id."""
    operator = {"id": 1, "username": "operator"}
    client = _client(me=operator)
    report = _report()
    ledger = _ledger()

    await import_users(
        # id 1 collides with operator's id but username differs -> NOT the operator
        # (id is remappable); this user is a normal restore.
        archive_users=[
            {"id": 1, "username": "someone_else"},
            {"id": 50, "username": "operator"},  # username match -> preserved
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    cat = report.category(EntityType.USER)
    # someone_else created; operator-username collision skipped.
    assert cat.created == 1
    created_payload = client.create_user.await_args_list[0].args[0]
    assert created_payload["username"] == "someone_else"
    preserved = [
        d for d in cat.skip_details if d.reason == SkipReason.CURRENT_ADMIN_PRESERVED
    ]
    assert len(preserved) == 1
    assert preserved[0].label == "operator"


# ---------------------------------------------------------------------------
# MANDATED TEST: archive-superuser-bit-not-trusted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_superuser_bit_not_trusted():
    """An archive user with is_superuser=true (and is_staff=true, elevated
    user_level) is created NON-privileged. The archive's privilege bits are
    dropped; conservative defaults are forced."""
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {
                "id": 5,
                "username": "wannabe_admin",
                "is_superuser": True,
                "is_staff": True,
                "user_level": 10,
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    payload = client.create_user.await_args.args[0]
    assert payload["is_superuser"] is False
    assert payload["is_staff"] is False
    # user_level is forced to the lowest/non-privileged value, never the
    # archive's elevated 10.
    assert payload["user_level"] != 10
    assert report.category(EntityType.USER).created == 1


# ---------------------------------------------------------------------------
# MANDATED TEST: no-password-set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_password_set():
    """The create payload omits password entirely (and any hash field), so the
    restored user ends with an unusable Django password."""
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {
                "id": 5,
                "username": "alice",
                "password": "hunter2",
                "password_hash": "pbkdf2_sha256$...",
            }
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    payload = client.create_user.await_args.args[0]
    assert "password" not in payload
    assert "password_hash" not in payload


# ---------------------------------------------------------------------------
# MANDATED TEST: force-reset-flagged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_force_reset_flagged():
    """Each restored user is flagged force-reset (operator must set a new password
    out-of-band). The flag is surfaced in the report notes, keyed by username."""
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice"},
            {"id": 6, "username": "bob"},
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    notes_blob = " ".join(report.notes).lower()
    assert "force-reset" in notes_blob or "force reset" in notes_blob
    assert "alice" in notes_blob
    assert "bob" in notes_blob


# ---------------------------------------------------------------------------
# Username uniqueness (non-operator collisions)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_existing_non_operator_username_skipped_already_exists():
    """A restored user whose username already exists on the destination (and is
    NOT the operator) is skipped already_exists_identical — never overwritten."""
    client = _client(
        me={"id": 1, "username": "operator"},
        existing_users=[{"id": 30, "username": "existing_user"}],
    )
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "existing_user"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    client.create_user.assert_not_awaited()
    cat = report.category(EntityType.USER)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL


@pytest.mark.asyncio
async def test_create_conflict_recorded_as_failure_conflict():
    """If create_user raises a CONFLICT-shaped upstream error (race: username
    appeared between the pre-check and the create), it is recorded as a
    FailureReason.CONFLICT with a sanitized message and no secret."""
    async def _conflict(payload):
        raise Exception(
            'User creation failed: 400 - {"username": ["already exists."]}'
        )

    client = _client(create_side_effect=_conflict)
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "racy"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    cat = report.category(EntityType.USER)
    assert cat.failed == 1
    assert cat.failure_details[0].reason == FailureReason.CONFLICT
    assert cat.failure_details[0].label == "racy"


# ---------------------------------------------------------------------------
# Rollback ledger
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_ledger_records_each_created_user():
    """Each successfully created user is recorded in the rollback ledger with its
    destination id and username label, so a later failure can compensate-delete."""
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice"},
            {"id": 6, "username": "bob"},
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    assert len(ledger.entries) == 2
    for entry in ledger.entries:
        assert entry.entity_type == EntityType.USER
        assert entry.destination_id is not None
        assert entry.label in ("alice", "bob")
    # id remap recorded created destination ids back into the ledger labels only;
    # no secret material on any entry.
    labels = {e.label for e in ledger.entries}
    assert labels == {"alice", "bob"}


# ---------------------------------------------------------------------------
# Audit/report carries usernames only — never hashes/passwords
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_and_ledger_carry_no_secret_material():
    """Across every report surface (skip_details, failure_details, notes) and the
    ledger, only usernames appear — never a password or hash substring."""
    client = _client(
        me={"id": 1, "username": "operator"},
        existing_users=[{"id": 9, "username": "dup_user"}],
    )
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "password": "SECRET_PW", "password_hash": "SECRET_HASH"},
            {"id": 6, "username": "operator", "password": "SECRET_PW"},  # preserved
            {"id": 7, "username": "dup_user", "password_hash": "SECRET_HASH"},  # exists
        ],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
    )

    blob = report.model_dump_json() + ledger.model_dump_json()
    assert "SECRET_PW" not in blob
    assert "SECRET_HASH" not in blob


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_does_not_create_but_reports_would_create():
    """A dry-run issues no create_user calls but populates would_create so the
    operator sees the plan."""
    client = _client()
    report = RestoreReport(is_dry_run=True)
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "alice"}],
        client=client,
        selected=True,
        report=report,
        ledger=ledger,
        is_dry_run=True,
    )

    client.create_user.assert_not_awaited()
    cat = report.category(EntityType.USER)
    assert cat.would_create == 1
    assert cat.created == 0
    assert ledger.entries == []
