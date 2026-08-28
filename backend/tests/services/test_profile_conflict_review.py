from __future__ import annotations

import json
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.orm import sessionmaker

from models import Notification, ProfileConflictReview
from services.profile_conflict_review import (
    REVIEW_STATUS_ACCEPTED,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_SUPERSEDED,
    canonical_conflict_evidence,
    choice_key,
    conflict_fingerprint,
    ensure_profile_conflict_review,
    harmonize_review_sources,
    reconcile_profile_conflict_reviews,
    _sync_notification,
)


def _shape(extra_source: bool = False) -> dict:
    sources = [
        {"source_group_id": 823, "m3u_account_id": 1, "m3u_account_name": "Stryker"},
        {"source_group_id": 825, "m3u_account_id": 1, "m3u_account_name": "Stryker"},
    ]
    if extra_source:
        sources.append({"source_group_id": 824, "m3u_account_id": 4, "m3u_account_name": "New"})
    return {
        "effective_group_id": 665,
        "source_group_ids": [2866, 823, 825],
        "choices": [
            {"profile_ids": [14], "sources": [
                {"source_group_id": 2866, "m3u_account_id": 2, "m3u_account_name": "Strong"},
            ]},
            {"profile_ids": [7, 6], "sources": sources},
        ],
    }


def test_fingerprint_is_order_stable_and_shape_sensitive():
    evidence_a = canonical_conflict_evidence(
        _shape(), {665: "NBA", 823: "A", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong only"},
    )
    reversed_shape = _shape()
    reversed_shape["choices"].reverse()
    reversed_shape["choices"][1]["sources"].reverse()
    evidence_b = canonical_conflict_evidence(
        reversed_shape, {2866: "C", 825: "B", 823: "A", 665: "NBA"},
        {14: "Strong only", 7: "Family", 6: "Sports"},
    )
    evidence_changed = canonical_conflict_evidence(
        _shape(extra_source=True), {665: "NBA", 823: "A", 824: "D", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong only"},
    )

    assert conflict_fingerprint(evidence_a) == conflict_fingerprint(evidence_b)
    assert conflict_fingerprint(evidence_a) != conflict_fingerprint(evidence_changed)
    assert evidence_a["target"]["name"] == "NBA"
    assert evidence_a["choices"][0]["profile_names"]


@pytest.mark.asyncio
async def test_queue_dedupes_same_shape_and_supersedes_changed_shape(test_session):
    client = AsyncMock()
    client.get_channel_groups.return_value = [
        {"id": 665, "name": "NBA"}, {"id": 823, "name": "A"},
        {"id": 825, "name": "B"}, {"id": 2866, "name": "C"},
    ]
    client.get_channel_profiles.return_value = [
        {"id": 6, "name": "Sports"}, {"id": 7, "name": "Family"},
        {"id": 14, "name": "Strong only"},
    ]
    settings = {823: {"_ecm_profile_conflict_shape": _shape()}}

    with patch("services.profile_conflict_review.get_session", return_value=test_session), \
         patch("services.profile_conflict_review.create_notification_internal", new=AsyncMock(return_value={"id": 1})), \
         patch("services.profile_conflict_review.journal.log_entry", return_value=object()):
        first = await ensure_profile_conflict_review(client, settings, 665)
        second = await ensure_profile_conflict_review(client, settings, 665)
        settings[823]["_ecm_profile_conflict_shape"] = _shape(extra_source=True)
        changed = await ensure_profile_conflict_review(client, settings, 665)
        settings[823]["_ecm_profile_conflict_shape"] = _shape()
        reopened = await ensure_profile_conflict_review(client, settings, 665)

    assert first.id == second.id
    assert changed.id != first.id
    assert reopened.id == first.id
    assert test_session.get(ProfileConflictReview, first.id).status == REVIEW_STATUS_PENDING
    assert test_session.get(ProfileConflictReview, changed.id).status == REVIEW_STATUS_SUPERSEDED


@pytest.mark.asyncio
async def test_exact_accepted_fingerprint_auto_converges_if_it_recurs(test_session):
    client = AsyncMock()
    client.get_channel_groups.return_value = [
        {"id": 665, "name": "NBA"}, {"id": 823, "name": "A"},
        {"id": 825, "name": "B"}, {"id": 2866, "name": "C"},
    ]
    client.get_channel_profiles.return_value = [
        {"id": 6, "name": "Sports"}, {"id": 7, "name": "Family"},
        {"id": 14, "name": "Strong only"},
    ]
    rows = [
        {"id": 10, "source_group_id": 823, "m3u_account_id": 1,
         "channel_group": 823, "custom_properties": {"channel_profile_ids": [6, 7]}},
        {"id": 11, "source_group_id": 825, "m3u_account_id": 1,
         "channel_group": 825, "custom_properties": {"channel_profile_ids": [6, 7]}},
        {"id": 20, "source_group_id": 2866, "m3u_account_id": 2,
         "channel_group": 2866, "custom_properties": {"channel_profile_ids": [14]}},
    ]
    settings = {
        823: {
            "_ecm_profile_conflict_shape": _shape(),
            "_ecm_profile_source_rows": rows,
        }
    }
    with patch("services.profile_conflict_review.get_session", return_value=test_session), \
         patch("services.profile_conflict_review.create_notification_internal", new=AsyncMock(return_value={"id": 1})), \
         patch("services.profile_conflict_review.journal.log_entry", return_value=object()):
        row = await ensure_profile_conflict_review(client, settings, 665)
        row = test_session.get(ProfileConflictReview, row.id)
        evidence = json.loads(row.evidence)
        row.status = REVIEW_STATUS_ACCEPTED
        row.accepted_choice_key = evidence["choices"][0]["choice_key"]
        row.accepted_profile_ids = json.dumps(evidence["choices"][0]["profile_ids"])
        row.applied_at = 1
        test_session.commit()
        recurred = await ensure_profile_conflict_review(client, settings, 665)

    assert recurred.id == row.id
    assert client.update_m3u_group_settings.await_count == 1
    assert recurred.applied_at is not None


@pytest.mark.asyncio
async def test_notification_is_not_recreated_after_operator_deletes_it(test_session):
    client = AsyncMock()
    client.get_channel_groups.return_value = [{"id": 665, "name": "NBA"}]
    client.get_channel_profiles.return_value = [{"id": 6, "name": "Sports"}, {"id": 14, "name": "Strong"}]
    settings = {823: {"_ecm_profile_conflict_shape": _shape()}}
    create = AsyncMock(return_value={"id": 9})

    with patch("services.profile_conflict_review.get_session", return_value=test_session), \
         patch("services.profile_conflict_review.create_notification_internal", new=create):
        row = await ensure_profile_conflict_review(client, settings, 665)
        assert row.notified_at is not None
        test_session.query(Notification).delete()
        test_session.commit()
        await ensure_profile_conflict_review(client, settings, 665)

    assert create.await_count == 1


@pytest.mark.asyncio
async def test_notification_creation_is_claimed_once_across_concurrent_sessions(
    test_engine, test_session,
):
    evidence = canonical_conflict_evidence(
        _shape(), {665: "NBA", 823: "A", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong"},
    )
    row = ProfileConflictReview(
        fingerprint=conflict_fingerprint(evidence), fingerprint_version=1,
        effective_group_id=665, status=REVIEW_STATUS_PENDING,
        evidence=json.dumps(evidence), created_at=1, last_seen_at=1,
    )
    test_session.add(row)
    test_session.commit()
    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)
    first_db = SessionLocal()
    second_db = SessionLocal()
    first_row = first_db.get(ProfileConflictReview, row.id)
    second_row = second_db.get(ProfileConflictReview, row.id)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def create_once(**_kwargs):
        entered.set()
        await release.wait()
        return {"id": 1}

    create = AsyncMock(side_effect=create_once)
    try:
        with patch("services.profile_conflict_review.create_notification_internal", new=create):
            first = asyncio.create_task(_sync_notification(first_db, first_row, evidence))
            await entered.wait()
            second = asyncio.create_task(
                _sync_notification(second_db, second_row, evidence)
            )
            await asyncio.sleep(0)
            assert create.await_count == 1
            release.set()
            await asyncio.gather(first, second)
    finally:
        first_db.close()
        second_db.close()

    assert create.await_count == 1


@pytest.mark.asyncio
async def test_notification_creation_is_serialized_across_changed_same_group_reviews(
    test_engine, test_session,
):
    evidence = canonical_conflict_evidence(
        _shape(), {665: "NBA", 823: "A", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong"},
    )
    first_row = ProfileConflictReview(
        fingerprint=conflict_fingerprint(evidence), fingerprint_version=1,
        effective_group_id=665, status=REVIEW_STATUS_PENDING,
        evidence=json.dumps(evidence), created_at=1, last_seen_at=1,
    )
    changed_evidence = {**evidence, "fingerprint_version": 2}
    second_row = ProfileConflictReview(
        fingerprint=conflict_fingerprint(changed_evidence), fingerprint_version=2,
        effective_group_id=665, status=REVIEW_STATUS_PENDING,
        evidence=json.dumps(changed_evidence), created_at=2, last_seen_at=2,
    )
    test_session.add_all([first_row, second_row])
    test_session.commit()
    SessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)
    first_db = SessionLocal()
    second_db = SessionLocal()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def create_once(**kwargs):
        entered.set()
        await release.wait()
        notification = Notification(
            type=kwargs["notification_type"], title=kwargs["title"],
            message=kwargs["message"], source=kwargs["source"],
            source_id=kwargs["source_id"], extra_data=json.dumps(kwargs["metadata"]),
        )
        test_session.add(notification)
        test_session.commit()
        return {"id": notification.id}

    create = AsyncMock(side_effect=create_once)
    try:
        with patch("services.profile_conflict_review.create_notification_internal", new=create):
            first = asyncio.create_task(_sync_notification(
                first_db, first_db.get(ProfileConflictReview, first_row.id), evidence
            ))
            await entered.wait()
            second = asyncio.create_task(_sync_notification(
                second_db, second_db.get(ProfileConflictReview, second_row.id),
                changed_evidence,
            ))
            await asyncio.sleep(0)
            assert create.await_count == 1
            release.set()
            await asyncio.gather(first, second)
    finally:
        first_db.close()
        second_db.close()

    assert create.await_count == 1
    assert test_session.query(Notification).filter_by(
        source="profile_reconcile", source_id="conflict:665"
    ).count() == 1


@pytest.mark.asyncio
async def test_external_notification_text_never_includes_untrusted_names(test_session):
    client = AsyncMock()
    client.get_channel_groups.return_value = [{
        "id": 665, "name": "<@everyone>\nsecond forged line",
    }]
    client.get_channel_profiles.return_value = [{"id": 6, "name": "Sports"}]
    create = AsyncMock(return_value={"id": 1})

    with patch("services.profile_conflict_review.get_session", return_value=test_session), \
         patch("services.profile_conflict_review.create_notification_internal", new=create):
        await ensure_profile_conflict_review(
            client, {823: {"_ecm_profile_conflict_shape": _shape()}}, 665
        )

    message = create.await_args.kwargs["message"]
    assert "everyone" not in message
    assert "\n" not in message


@pytest.mark.asyncio
async def test_harmonize_preserves_unrelated_custom_properties_and_retries_partial_accounts():
    selected = [6, 7]
    evidence = canonical_conflict_evidence(
        _shape(), {665: "NBA", 823: "A", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong"},
    )
    client = AsyncMock()
    client.update_m3u_group_settings.side_effect = [RuntimeError("account 1 down"), {"ok": True}]
    rows = {
        (1, 823): {"id": 10, "channel_group": 823, "enabled": True, "auto_channel_sync": True,
                   "custom_properties": {"channel_profile_ids": [14], "keep": "yes"}},
        (1, 825): {"id": 11, "channel_group": 825, "enabled": True, "auto_channel_sync": True,
                   "custom_properties": {"channel_profile_ids": [14], "other": 3}},
        (2, 2866): {"id": 12, "channel_group": 2866, "enabled": True, "auto_channel_sync": True,
                    "custom_properties": {"channel_profile_ids": [14], "keep": "strong"}},
    }

    outcome = await harmonize_review_sources(client, evidence, selected, rows)

    assert outcome["failed_account_ids"] == [1]
    assert outcome["updated_account_ids"] == [2]
    payload = client.update_m3u_group_settings.await_args_list[1].args[1]
    assert payload["group_settings"][0]["custom_properties"] == {
        "channel_profile_ids": selected, "keep": "strong",
    }
    assert not hasattr(client, "bulk_update_profile_channels") or not client.bulk_update_profile_channels.await_count


@pytest.mark.asyncio
async def test_harmonize_skips_accounts_whose_participating_rows_already_match():
    shape = _shape()
    shape["choices"].append({
        "profile_ids": [],
        "sources": [{
            "source_group_id": 824,
            "m3u_account_id": 3,
            "m3u_account_name": "Unset",
        }],
    })
    evidence = canonical_conflict_evidence(
        shape, {665: "NBA", 823: "A", 824: "Unset", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong"},
    )
    client = AsyncMock()
    rows = {
        (1, 823): {"id": 10, "channel_group": 823,
                   "custom_properties": {"channel_profile_ids": [6, 7], "keep": "a"}},
        (1, 825): {"id": 11, "channel_group": 825,
                   "custom_properties": {"channel_profile_ids": [7, 6], "keep": "b"}},
        (2, 2866): {"id": 12, "channel_group": 2866,
                    "custom_properties": {"channel_profile_ids": [14], "keep": "c"}},
        (3, 824): {"id": 13, "channel_group": 824,
                   "custom_properties": {"keep": "unset"}},
    }

    outcome = await harmonize_review_sources(client, evidence, [6, 7], rows)

    assert outcome["updated_account_ids"] == [2, 3]
    assert outcome["failed_account_ids"] == []
    assert [call.args[0] for call in client.update_m3u_group_settings.await_args_list] == [2, 3]


@pytest.mark.asyncio
async def test_queue_sweep_supersedes_pending_review_when_conflict_disappears(test_session):
    evidence = canonical_conflict_evidence(
        _shape(), {665: "NBA", 823: "A", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong"},
    )
    row = ProfileConflictReview(
        fingerprint=conflict_fingerprint(evidence), fingerprint_version=1,
        effective_group_id=665, status=REVIEW_STATUS_PENDING,
        evidence=json.dumps(evidence), created_at=1, last_seen_at=1,
    )
    test_session.add(row)
    test_session.flush()
    test_session.add(Notification(
        type="warning", title="Review", message="Review",
        source="profile_reconcile", source_id="conflict:665",
    ))
    test_session.commit()

    with patch("services.profile_conflict_review.get_session", return_value=test_session):
        await reconcile_profile_conflict_reviews(AsyncMock(), {})

    assert test_session.get(ProfileConflictReview, row.id).status == REVIEW_STATUS_SUPERSEDED
    assert test_session.query(Notification).filter_by(source_id="conflict:665").count() == 0


@pytest.mark.asyncio
async def test_queue_sweep_heals_accepted_review_after_conflict_disappears(test_session):
    evidence = canonical_conflict_evidence(
        _shape(), {665: "NBA", 823: "A", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong"},
    )
    selected = evidence["choices"][0]
    row = ProfileConflictReview(
        fingerprint=conflict_fingerprint(evidence), fingerprint_version=1,
        effective_group_id=665, status=REVIEW_STATUS_ACCEPTED,
        accepted_choice_key=selected["choice_key"],
        accepted_profile_ids=json.dumps(selected["profile_ids"]),
        actor_token_id="7", evidence=json.dumps(evidence),
        created_at=1, last_seen_at=1, resolved_at=1,
    )
    test_session.add(row)
    test_session.commit()
    settings = {
        823: {"_ecm_profile_source_rows": [
            {"id": 10, "source_group_id": 823, "m3u_account_id": 1,
             "channel_group": 823, "custom_properties": {"channel_profile_ids": [6, 7]}},
            {"id": 11, "source_group_id": 825, "m3u_account_id": 1,
             "channel_group": 825, "custom_properties": {"channel_profile_ids": [6, 7]}},
            {"id": 12, "source_group_id": 2866, "m3u_account_id": 2,
             "channel_group": 2866, "custom_properties": {"channel_profile_ids": [6, 7]}},
        ]},
    }
    client = AsyncMock()
    client.get_all_m3u_group_settings.return_value = settings

    with patch("services.profile_conflict_review.get_session", return_value=test_session), \
         patch("services.profile_conflict_review.journal.log_entry", return_value=object()):
        await reconcile_profile_conflict_reviews(client, settings)

    persisted = test_session.get(ProfileConflictReview, row.id)
    assert persisted.applied_at is not None
    client.update_m3u_group_settings.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_compatibility_rejects_recycled_profile_id_with_new_name(test_session):
    client = AsyncMock()
    client.get_channel_groups.return_value = [
        {"id": 665, "name": "NBA"}, {"id": 823, "name": "A"},
        {"id": 825, "name": "B"}, {"id": 2866, "name": "C"},
    ]
    client.get_channel_profiles.return_value = [
        {"id": 6, "name": "Recycled Sports"}, {"id": 7, "name": "Family"},
        {"id": 14, "name": "Strong"},
    ]
    original = canonical_conflict_evidence(
        _shape(), {665: "NBA", 823: "A", 825: "B", 2866: "C"},
        {6: "Sports", 7: "Family", 14: "Strong"},
    )
    selected = original["choices"][0]
    accepted = ProfileConflictReview(
        fingerprint=conflict_fingerprint(original), fingerprint_version=1,
        effective_group_id=665, status=REVIEW_STATUS_ACCEPTED,
        accepted_choice_key=selected["choice_key"],
        accepted_profile_ids=json.dumps(selected["profile_ids"]),
        evidence=json.dumps(original), created_at=1, last_seen_at=1, resolved_at=1,
    )
    test_session.add(accepted)
    test_session.commit()
    settings = {823: {"_ecm_profile_conflict_shape": _shape()}}

    with patch("services.profile_conflict_review.get_session", return_value=test_session), \
         patch("services.profile_conflict_review.create_notification_internal", new=AsyncMock(return_value={"id": 2})):
        current = await ensure_profile_conflict_review(client, settings, 665)

    assert test_session.get(ProfileConflictReview, accepted.id).status == REVIEW_STATUS_SUPERSEDED
    assert current.id != accepted.id
    assert current.status == REVIEW_STATUS_PENDING
    client.update_m3u_group_settings.assert_not_awaited()


def test_choice_key_is_opaque_and_order_stable():
    assert choice_key([7, 6]) == choice_key([6, 7])
    assert choice_key([6, 7]) != choice_key([14])
    assert len(choice_key([6, 7])) == 64
