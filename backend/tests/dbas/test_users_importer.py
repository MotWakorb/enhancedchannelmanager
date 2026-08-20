"""Tests for the dispatcharr_users restore importer
(enhancedchannelmanager-l1p4p — crown-jewel restore, the most
security-sensitive bead in the Phase-2 epic).

Security policy under test (spike tsfv0, live-confirmed vs Dispatcharr 0.26.0):

1. No ARCHIVE password/hash ever crosses the boundary — the source instance's
   secret material is dropped, never carried, never rehashed. ECM sends a
   freshly generated random password instead (bead …-y65si: Dispatcharr's
   serializer 500s on a missing ``password`` key) which is never recorded
   anywhere, so the account still needs an operator-driven reset.
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
    IdRemapTable,
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
    # create_user returns a created body with an assigned id by default. The
    # ``password`` KEYWORD is the ECM-generated one (bead
    # …-y65si) — it is deliberately NOT echoed back into the created body, which
    # is what a real Dispatcharr does (the field is write-only).
    created_counter = {"n": 100}

    async def _default_create(payload, *, password=None):
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


def _remap(channel_profiles: dict[int, int] | None = None) -> IdRemapTable:
    """An IdRemapTable, optionally pre-loaded with CHANNEL_PROFILE mappings.

    Bead …-if05f. The USER step runs AFTER the CHANNEL_PROFILE step in both
    registries, so by the time this importer runs the namespace holds every
    profile the run created or adopted. These tests hand it in directly.
    """
    remap = IdRemapTable()
    for source_id, dest_id in (channel_profiles or {}).items():
        remap.add(EntityType.CHANNEL_PROFILE, source_id, dest_id)
    return remap


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
        remap=_remap(),
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
        remap=_remap(),
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
        remap=_remap(),
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
        remap=_remap(),
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
        remap=_remap(),
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
        remap=_remap(),
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
        remap=_remap(),
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
# MANDATED TEST: no archive password/hash ever crosses the boundary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_password_and_hash_never_forwarded():
    """The create PAYLOAD never carries the archive's password or hash.

    ECM sends a freshly generated password as a separate argument (bead
    ``…-y65si`` — Dispatcharr's serializer 500s without one), but the SOURCE
    instance's secret material is dropped, never carried, never rehashed.
    """
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
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
    )

    payload = client.create_user.await_args.args[0]
    assert "password" not in payload
    assert "password_hash" not in payload
    # …and the generated password is nothing to do with the archive's.
    generated = client.create_user.await_args.kwargs["password"]
    assert generated != "hunter2"
    assert "pbkdf2_sha256" not in generated


# ---------------------------------------------------------------------------
# y65si — a GENERATED password IS sent, because upstream requires the key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_sends_a_generated_password():
    """Every user create carries a strong, freshly generated password.

    Dispatcharr 0.28.2's user-create serializer reads ``validated_data
    ['password']`` unconditionally; omitting the key raises an uncaught
    ``KeyError`` that surfaces as a 500 and used to abort the whole restore.
    """
    client = _client()

    await import_users(
        archive_users=[{"id": 5, "username": "alice"}, {"id": 6, "username": "bob"}],
        client=client,
        remap=_remap(),
        selected=True,
        report=_report(),
        ledger=_ledger(),
    )

    passwords = [call.kwargs["password"] for call in client.create_user.await_args_list]
    assert len(passwords) == 2
    for password in passwords:
        assert isinstance(password, str)
        # Long enough that it is not brute-forceable, and clearly not a constant.
        assert len(password) >= 24
    # Distinct per user — never one shared secret across the restored accounts.
    assert passwords[0] != passwords[1]


@pytest.mark.asyncio
async def test_generated_password_never_reaches_the_report_or_the_ledger():
    """The generated password is unrecoverable by design — it is never recorded.

    The restored account is not meant to be logged into with a known password;
    the operator resets it out of band, so the value must not survive anywhere
    an operator (or an attacker reading a report) could find it.
    """
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "alice"}],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
    )

    generated = client.create_user.await_args.kwargs["password"]
    blob = report.model_dump_json() + ledger.model_dump_json()
    assert generated not in blob
    # The operator IS told the account needs a password reset.
    notes_blob = " ".join(report.notes).lower()
    assert "reset" in notes_blob


@pytest.mark.asyncio
async def test_generated_password_never_logged(caplog):
    """The generated password never appears in a log record either."""
    import logging

    client = _client()
    with caplog.at_level(logging.DEBUG, logger="dbas.importers.users"):
        await import_users(
            archive_users=[{"id": 5, "username": "alice"}],
            client=client,
        remap=_remap(),
            selected=True,
            report=_report(),
            ledger=_ledger(),
        )

    generated = client.create_user.await_args.kwargs["password"]
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert generated not in blob


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
        remap=_remap(),
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
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
    )

    client.create_user.assert_not_awaited()
    cat = report.category(EntityType.USER)
    assert cat.skipped == 1
    assert cat.skip_details[0].reason == SkipReason.ALREADY_EXISTS_IDENTICAL


# ---------------------------------------------------------------------------
# l1p4p follow-up 1 (MEDIUM data-integrity): per-create ledger flush.
# The RollbackLedger durability contract requires the ledger be flushed to disk
# IMMEDIATELY after each created user is recorded and BEFORE the next create —
# otherwise a mid-category crash orphans every created user with no recoverable
# record. The importer accepts a persist_ledger callback (wired by the
# orchestrator) and must invoke it after each record_created, before the next
# upstream create.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ledger_flushed_to_disk_before_each_subsequent_create():
    """The persist callback fires after each create, and the entry for user N is
    durable BEFORE user N+1's create is issued."""
    client = _client()
    report = _report()
    ledger = _ledger()

    events = []  # ordered log of (kind, detail)

    original_create = client.create_user.side_effect

    async def _tracked_create(payload, *, password=None):
        # At the moment we issue this create, snapshot how many entries are
        # already durably flushed (proxied by the flush call count) and how many
        # are in the in-memory ledger.
        events.append(("create", payload.get("username"), flush_calls["n"], len(ledger.entries)))
        return await original_create(payload, password=password)

    client.create_user.side_effect = _tracked_create

    flush_calls = {"n": 0}

    def _persist():
        # Each flush corresponds to a durable write of the current ledger state.
        flush_calls["n"] += 1
        events.append(("flush", len(ledger.entries)))

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice"},
            {"id": 6, "username": "bob"},
            {"id": 7, "username": "carol"},
        ],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
        persist_ledger=_persist,
    )

    # One flush per created user.
    assert flush_calls["n"] == 3
    # Before the 2nd and 3rd create is issued, the prior creation has already
    # been flushed: the create event for bob/carol carries flush_count >= the
    # number of prior creates.
    create_events = [e for e in events if e[0] == "create"]
    # alice: 0 prior flushes; bob: >=1 prior flush; carol: >=2 prior flushes.
    assert create_events[0][2] == 0
    assert create_events[1][2] >= 1
    assert create_events[2][2] >= 2
    # The ledger ends with all three entries recorded.
    assert len(ledger.entries) == 3


@pytest.mark.asyncio
async def test_no_persist_callback_does_not_crash():
    """The callback is optional — omitting it (e.g. a direct unit call) creates
    users in-memory without error (durability is the orchestrator's concern)."""
    client = _client()
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "alice"}],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
        # persist_ledger omitted
    )
    assert report.category(EntityType.USER).created == 1
    assert len(ledger.entries) == 1


# ---------------------------------------------------------------------------
# l1p4p follow-up 2 (LOW): allowlist payload, intersected with schema fields.
# Unknown archive keys the destination schema does not list as write-fields are
# DROPPED, not forwarded. This is an allowlist (schema ∩ archive), not a denylist
# of known-bad keys.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_drops_keys_not_in_schema_write_fields():
    """An archive key absent from the destination's User-create write-fields is
    NOT forwarded — even though it is not a known secret/privilege key."""
    client = _client(schema_fields={"username", "email"})
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {
                "id": 5,
                "username": "alice",
                "email": "alice@example.com",
                # Not in the schema write-field set -> must be dropped.
                "some_future_field": "x",
                "totally_unknown": {"nested": 1},
            }
        ],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
    )

    payload = client.create_user.await_args.args[0]
    assert payload["username"] == "alice"
    assert payload["email"] == "alice@example.com"
    assert "some_future_field" not in payload
    assert "totally_unknown" not in payload


@pytest.mark.asyncio
async def test_payload_still_drops_secret_keys_present_in_schema():
    """Defense-in-depth: even if the schema lists a secret/privilege key as
    writable, the importer still drops it (the drop list overrides the
    allowlist) and forces conservative privilege defaults."""
    client = _client(
        schema_fields={"username", "password", "is_superuser", "is_staff", "user_level"},
    )
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "password": "hunter2", "is_superuser": True},
        ],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
    )

    payload = client.create_user.await_args.args[0]
    assert "password" not in payload
    assert payload["is_superuser"] is False


# ---------------------------------------------------------------------------
# l1p4p follow-up 3 (LOW): _sanitize_failure masks echoed secrets.
# If Dispatcharr echoes request-body material (a token, an Authorization
# header) back in an error, the operator-facing failure message must be masked.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failure_message_masks_echoed_secret():
    """A create_user error whose text carries a secret-looking token is masked
    in the operator-facing FailureDetail.message — no raw secret survives."""
    secret = "AKIAIOSFODNN7EXAMPLE"  # AWS access-key shape masked by redact_secrets

    async def _raise_with_secret(payload, *, password=None):
        raise RuntimeError("upstream rejected; aws_secret_access_key=%s echoed" % secret)

    client = _client(create_side_effect=_raise_with_secret)
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "alice"}],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=ledger,
    )

    cat = report.category(EntityType.USER)
    assert cat.failed == 1
    message = cat.failure_details[0].message
    assert secret not in message
    assert "REDACTED" in message


@pytest.mark.asyncio
async def test_create_conflict_recorded_as_failure_conflict():
    """If create_user raises a CONFLICT-shaped upstream error (race: username
    appeared between the pre-check and the create), it is recorded as a
    FailureReason.CONFLICT with a sanitized message and no secret."""
    async def _conflict(payload, *, password=None):
        raise Exception(
            'User creation failed: 400 - {"username": ["already exists."]}'
        )

    client = _client(create_side_effect=_conflict)
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[{"id": 5, "username": "racy"}],
        client=client,
        remap=_remap(),
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
        remap=_remap(),
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
        remap=_remap(),
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
        remap=_remap(),
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


# ---------------------------------------------------------------------------
# 9. THE ``channel_profiles`` LIST-VALUED FK (bead …-if05f)
#
# ``channel_profiles`` is a LIST of ChannelProfile primary keys, WRITABLE on
# User create (confirmed against a live Dispatcharr 0.28.2 ``/api/schema/``:
# ``{"type": "array", "items": {"type": "integer"}}``, and
# ``UserSerializer.channel_profiles = PrimaryKeyRelatedField(many=True)``). The
# destination assigns its own profile ids, so forwarding the source's raw pks
# bound the restored user to whatever profiles happened to occupy those ids —
# a SILENT WRONG BINDING, not the loud 400 the M3U account's scalar FKs gave.
#
# EVERY test below asserts on the RESULTING BINDING (the exact list in the
# create payload), never on the absence of an error: the broken code raised
# nothing. Source pks are deliberately chosen OUTSIDE the destination's id
# range, or pointing at a differently-NAMED destination profile, so a
# pass-through implementation cannot alias its way to a green (…-9h6cv).
# ---------------------------------------------------------------------------


_PROFILE_SCHEMA_FIELDS = {"username", "email", "channel_profiles"}


def _sent_payload(client):
    """The payload dict handed to ``create_user`` on the first (only) create."""
    return client.create_user.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_channel_profiles_are_remapped_to_destination_ids():
    """The list is rewritten pk-by-pk through the CHANNEL_PROFILE namespace.

    Source pks 901/902 exist on NO destination (B's profile ids here are 4 and
    7), so a pass-through would either 400 or bind nothing — but the bug's real
    shape is the aliasing case in the next test. Here we pin the happy path: the
    payload carries the DESTINATION ids, in the archive's order.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)
    report = _report()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "channel_profiles": [901, 902]}
        ],
        client=client,
        remap=_remap({901: 4, 902: 7}),
        selected=True,
        report=report,
        ledger=_ledger(),
    )

    payload = _sent_payload(client)
    assert payload["channel_profiles"] == [4, 7]
    assert 901 not in payload["channel_profiles"]
    assert 902 not in payload["channel_profiles"]
    assert report.category(EntityType.USER).created == 1


@pytest.mark.asyncio
async def test_a_source_pk_that_aliases_a_different_destination_profile_is_never_sent():
    """The FALSE-GREEN trap, made explicit (…-9h6cv).

    The archive user is limited to SOURCE profile 3 ("Kids"). The destination
    also HAS a profile with id 3 — but it is "Adults", an unrelated row; "Kids"
    landed on the destination as id 11. Forwarding the raw pk therefore succeeds
    upstream and binds the user to the WRONG profile, with the report still
    saying ``created``. Only an assertion on the resulting binding catches it.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)

    await import_users(
        archive_users=[{"id": 5, "username": "alice", "channel_profiles": [3]}],
        client=client,
        # source "Kids" id 3 -> destination "Kids" id 11. Destination id 3 is
        # "Adults" and must never be bound.
        remap=_remap({3: 11}),
        selected=True,
        report=_report(),
        ledger=_ledger(),
    )

    assert _sent_payload(client)["channel_profiles"] == [11]


@pytest.mark.asyncio
async def test_duplicate_source_profiles_collapse_to_one_destination_id():
    """Two source profiles adopted by the same destination row bind once.

    Deterministic and order-stable — the payload is not allowed to depend on how
    many source rows happened to map onto a shared destination profile.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "channel_profiles": [901, 902, 903]}
        ],
        client=client,
        remap=_remap({901: 4, 902: 4, 903: 7}),
        selected=True,
        report=_report(),
        ledger=_ledger(),
    )

    assert _sent_payload(client)["channel_profiles"] == [4, 7]


@pytest.mark.asyncio
async def test_unresolvable_profile_entry_is_dropped_and_the_user_is_still_created():
    """PARTIAL loss: send the resolved subset, drop the rest, report it.

    Dropping an entry can only NARROW what the restored user can see (the
    remaining list is a subset of the archive's grant), which is the safe
    direction for this security-sensitive category. The user is still worth
    creating; the degradation is reported, never silent.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)
    report = _report()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "channel_profiles": [901, 902]}
        ],
        client=client,
        remap=_remap({901: 4}),  # 902 is not on the destination at all
        selected=True,
        report=report,
        ledger=_ledger(),
    )

    payload = _sent_payload(client)
    assert payload["channel_profiles"] == [4]
    assert 902 not in payload["channel_profiles"]
    cat = report.category(EntityType.USER)
    assert cat.created == 1
    assert cat.skipped == 0
    # Structure: this degradation is a report.notes entry (the established idiom
    # for a "created, but less than the archive asked for" outcome — …-9h6cv /
    # …-g8tyd), NOT a skip_detail and NOT a failure_detail.
    assert cat.skip_details == []
    assert cat.failure_details == []
    assert any(
        "alice" in note and "channel profile" in note for note in report.notes
    )


@pytest.mark.asyncio
async def test_user_is_skipped_when_none_of_its_channel_profiles_resolve():
    """TOTAL loss FAILS CLOSED — the user is not created at all.

    Dispatcharr 0.28.2 reads an EMPTY ``channel_profiles`` as UNRESTRICTED, not
    as "no access": five paths (``apps/output/views.py`` M3U + group + XC
    listings, ``apps/output/epg.py``, ``apps/timeshift/views.py``,
    ``apps/proxy/live_proxy/views.py``) branch on
    ``user.channel_profiles.count() == 0`` and drop profile filtering entirely.
    So creating this user with the empty list that is all we could resolve would
    turn a profile-scoped archive user into an UNSCOPED destination user — a
    privilege widening, which policy item 2 forbids. Skip instead.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)
    report = _report()
    ledger = _ledger()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "channel_profiles": [901, 902]}
        ],
        client=client,
        remap=_remap(),  # the CHANNEL_PROFILE namespace is empty
        selected=True,
        report=report,
        ledger=ledger,
    )

    client.create_user.assert_not_awaited()
    cat = report.category(EntityType.USER)
    assert cat.created == 0
    assert cat.skipped == 1
    # Structure: a SkipDetail with SkipReason.DEPENDENCY_UNRESOLVED, plus a
    # report.notes line. Not a FailureDetail — nothing was attempted upstream.
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED
    assert cat.skip_details[0].label == "alice"
    assert cat.skip_details[0].source_export_id == 5
    assert cat.failure_details == []
    assert ledger.entries == []
    assert any("alice" in note and "unrestricted" in note for note in report.notes)


@pytest.mark.asyncio
async def test_an_archive_user_with_no_profiles_is_restored_unrestricted_and_unreported():
    """An EMPTY archive list is faithful, not a degradation.

    The archive said "unrestricted"; sending an empty list reproduces exactly
    that. Nothing was lost, so nothing is reported and nothing is skipped.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)
    report = _report()

    await import_users(
        archive_users=[{"id": 5, "username": "alice", "channel_profiles": []}],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=_ledger(),
    )

    assert _sent_payload(client)["channel_profiles"] == []
    cat = report.category(EntityType.USER)
    assert cat.created == 1
    assert cat.skipped == 0
    assert not any("channel profile" in note for note in report.notes)


@pytest.mark.asyncio
async def test_channel_profiles_is_never_sent_when_the_destination_does_not_accept_it():
    """A destination whose User-create schema has no ``channel_profiles``.

    The allowlist already drops the key, so there is no binding to get wrong and
    no reason to skip the user over a field the destination would ignore.
    """
    client = _client(schema_fields={"username", "email"})
    report = _report()

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "channel_profiles": [901, 902]}
        ],
        client=client,
        remap=_remap(),
        selected=True,
        report=report,
        ledger=_ledger(),
    )

    payload = _sent_payload(client)
    assert "channel_profiles" not in payload
    cat = report.category(EntityType.USER)
    assert cat.created == 1
    assert cat.skipped == 0
    assert not any("channel profile" in note for note in report.notes)


@pytest.mark.asyncio
async def test_dry_run_previews_the_same_skip_the_apply_performs():
    """Dry-run / apply parity for the total-loss skip.

    The preview must not promise a user the apply will refuse to create. The
    resolution is pure, so it runs on both sides of the branch.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)
    report = RestoreReport(is_dry_run=True)

    await import_users(
        archive_users=[
            {"id": 5, "username": "alice", "channel_profiles": [901]},
            {"id": 6, "username": "bob", "channel_profiles": [902]},
        ],
        client=client,
        remap=_remap({902: 7}),
        selected=True,
        report=report,
        ledger=_ledger(),
        is_dry_run=True,
    )

    client.create_user.assert_not_awaited()
    cat = report.category(EntityType.USER)
    assert cat.would_create == 1  # bob
    assert cat.would_skip == 1  # alice — her only profile is missing
    assert cat.skip_details[0].label == "alice"
    assert cat.skip_details[0].reason == SkipReason.DEPENDENCY_UNRESOLVED


@pytest.mark.asyncio
async def test_a_non_integer_profile_entry_is_treated_as_unresolvable():
    """Junk in the list never becomes a binding, and never crashes the category.

    ``True`` is deliberately included: ``int(True)`` is ``1``, so a coercing
    implementation would silently bind destination profile 1.
    """
    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)
    report = _report()

    await import_users(
        archive_users=[
            {
                "id": 5,
                "username": "alice",
                "channel_profiles": [901, True, None, "abc", {"id": 902}],
            }
        ],
        client=client,
        remap=_remap({901: 4, 1: 1}),
        selected=True,
        report=report,
        ledger=_ledger(),
    )

    assert _sent_payload(client)["channel_profiles"] == [4]
    assert report.category(EntityType.USER).created == 1
    assert any("alice" in note for note in report.notes)


@pytest.mark.asyncio
async def test_the_orchestrator_users_step_hands_the_importer_the_shared_remap():
    """WIRING: drive the orchestrator's OWN users step, not the importer directly.

    A remap resolved perfectly inside the importer proves nothing if the step
    registry never passes one (the dead-code failure mode this branch has hit
    before). This goes through ``_importer_step_builders()`` — the exact
    callable both the apply and the dry-run registries are built from — with the
    CHANNEL_PROFILE namespace populated the way the step ahead of it would leave
    it, and asserts the resulting BINDING.
    """
    from dbas.preflight import ImportPlan, PlanCategory
    from dbas.restore_orchestrator import ApplyContext, _importer_step_builders

    client = _client(schema_fields=_PROFILE_SCHEMA_FIELDS)
    remap = _remap({901: 4, 902: 7})
    ctx = ApplyContext(
        plan=ImportPlan(
            manifest={"schema_version": 1},
            categories=[
                PlanCategory(
                    entity_type=EntityType.USER,
                    entities=[
                        {
                            "id": 5,
                            "username": "alice",
                            "channel_profiles": [901, 902],
                        }
                    ],
                    selected=True,
                )
            ],
        ),
        client=client,
        report=_report(),
        ledger=_ledger(),
        remap=remap,
        is_dry_run=False,
    )

    await _importer_step_builders()["users"](ctx)

    assert _sent_payload(client)["channel_profiles"] == [4, 7]
