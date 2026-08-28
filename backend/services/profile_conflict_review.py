"""Durable review queue for effective-group profile-selection conflicts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

import journal
from database import get_session
from models import Notification, ProfileConflictReview
from services.notification_service import create_notification_internal

logger = logging.getLogger(__name__)

FINGERPRINT_VERSION = 1
REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_ACCEPTED = "accepted"
REVIEW_STATUS_SUPERSEDED = "superseded"
NOTIFICATION_SOURCE = "profile_reconcile"
_notification_locks: dict[tuple[object, int], asyncio.Lock] = {}


class ProfileConflictReviewError(Exception):
    pass


class ReviewNotFound(ProfileConflictReviewError):
    pass


class StaleReview(ProfileConflictReviewError):
    pass


class ParticipatingSourceMissing(StaleReview):
    pass


class InvalidChoice(ProfileConflictReviewError):
    pass


def now_epoch_ms() -> int:
    return int(time.time() * 1000)


def _canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def choice_key(profile_ids) -> str:
    payload = {"version": 1, "profile_ids": sorted(set(profile_ids))}
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def canonical_conflict_evidence(
    shape: dict, group_names: dict[int, str], profile_names: dict[int, str]
) -> dict:
    effective_gid = int(shape["effective_group_id"])
    choices = []
    for raw_choice in shape.get("choices", []):
        profile_ids = sorted(set(int(pid) for pid in raw_choice.get("profile_ids", [])))
        sources = []
        for source in raw_choice.get("sources", []):
            gid = int(source["source_group_id"])
            aid = source.get("m3u_account_id")
            sources.append({
                "source_group_id": gid,
                "source_group_name": group_names.get(gid, f"Group {gid}"),
                "m3u_account_id": int(aid) if aid is not None else None,
                "m3u_account_name": source.get("m3u_account_name") or (
                    f"M3U account {aid}" if aid is not None else "Unknown M3U account"
                ),
            })
        sources.sort(key=lambda item: (
            item["source_group_id"], item["m3u_account_id"] or 0,
            item["m3u_account_name"],
        ))
        choices.append({
            "choice_key": choice_key(profile_ids),
            "profile_ids": profile_ids,
            "profile_names": [profile_names.get(pid, f"Profile {pid}") for pid in profile_ids],
            "sources": sources,
        })
    choices.sort(key=lambda item: (item["profile_ids"], item["choice_key"]))
    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "target": {
            "effective_group_id": effective_gid,
            "name": group_names.get(effective_gid, f"Group {effective_gid}"),
        },
        "choices": choices,
    }


def conflict_fingerprint(evidence: dict) -> str:
    envelope = {"kind": "profile-conflict", "version": FINGERPRINT_VERSION, "evidence": evidence}
    return hashlib.sha256(_canonical_json(envelope).encode("utf-8")).hexdigest()


def _shape_for_effective(all_settings: dict, effective_gid: int) -> dict | None:
    for setting in all_settings.values():
        if not isinstance(setting, dict):
            continue
        shape = setting.get("_ecm_profile_conflict_shape")
        if isinstance(shape, dict) and shape.get("effective_group_id") == effective_gid:
            return shape
    return None


def _current_source_rows(all_settings: dict) -> dict[tuple[int, int], dict]:
    rows = {}
    for setting in all_settings.values():
        if not isinstance(setting, dict):
            continue
        for row in setting.get("_ecm_profile_source_rows", []):
            aid = row.get("m3u_account_id")
            gid = row.get("source_group_id")
            if aid is not None and gid is not None:
                rows[(int(aid), int(gid))] = row
    return rows


def _source_identity(evidence: dict) -> set[tuple]:
    return {
        (
            source.get("source_group_id"), source.get("source_group_name"),
            source.get("m3u_account_id"), source.get("m3u_account_name"),
        )
        for choice in evidence.get("choices", [])
        for source in choice.get("sources", [])
    }


def _retry_compatible(original: dict, current: dict, accepted_ids: list[int]) -> bool:
    if original.get("target") != current.get("target"):
        return False
    if _source_identity(original) != _source_identity(current):
        return False
    allowed = {
        tuple(choice.get("profile_ids", [])): tuple(choice.get("profile_names", []))
        for choice in original.get("choices", [])
    }
    accepted_key = tuple(sorted(accepted_ids))
    if accepted_key not in allowed:
        return False
    return all(
        allowed.get(tuple(choice.get("profile_ids", [])))
        == tuple(choice.get("profile_names", []))
        for choice in current.get("choices", [])
    )


async def _build_evidence(client, shape: dict) -> dict:
    groups = await client.get_channel_groups()
    profiles = await client.get_channel_profiles()
    group_names = {
        int(row["id"]): str(row.get("name") or f"Group {row['id']}")
        for row in (groups or []) if isinstance(row, dict) and row.get("id") is not None
    }
    profile_names = {
        int(row["id"]): str(row.get("name") or f"Profile {row['id']}")
        for row in (profiles or []) if isinstance(row, dict) and row.get("id") is not None
    }
    return canonical_conflict_evidence(shape, group_names, profile_names)


def _notification_lock(effective_group_id: int) -> asyncio.Lock:
    key = (asyncio.get_running_loop(), effective_group_id)
    lock = _notification_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _notification_locks[key] = lock
    return lock


async def _sync_notification(db, row: ProfileConflictReview, evidence: dict) -> None:
    async with _notification_lock(row.effective_group_id):
        await _sync_notification_locked(db, row, evidence)


async def _sync_notification_locked(
    db, row: ProfileConflictReview, evidence: dict
) -> None:
    source_id = f"conflict:{row.effective_group_id}"
    existing = db.query(Notification).filter(
        Notification.source == NOTIFICATION_SOURCE,
        Notification.source_id == source_id,
    ).order_by(Notification.id.desc()).first()
    # Names originate upstream and can contain mention/markup syntax. Keep
    # external notification text static; named provenance remains in structured
    # review evidence and the operator journal.
    message = (
        "Channel-profile membership is frozen because source groups disagree. "
        "Review the competing selections."
    )
    metadata = {"review_id": row.id, "fingerprint": row.fingerprint}
    if existing is not None:
        existing.type = "warning"
        existing.title = "Channel profile conflict needs review"
        existing.message = message
        existing.action_label = "Review choices"
        existing.action_url = None
        existing.extra_data = json.dumps(metadata)
        existing.read = False
        db.commit()
        if row.notified_at is None:
            row.notified_at = now_epoch_ms()
            db.commit()
        return
    # Atomically claim notification creation before the await. Two concurrent
    # sessions can both observe no Notification row, but only one can move this
    # durable marker from NULL. Deletion remains an operator dismissal and does
    # not permit the five-minute sweep to recreate the same notice.
    if row.notified_at is not None:
        return
    claimed_at = now_epoch_ms()
    claimed = db.query(ProfileConflictReview).filter(
        ProfileConflictReview.id == row.id,
        ProfileConflictReview.notified_at.is_(None),
    ).update(
        {ProfileConflictReview.notified_at: claimed_at},
        synchronize_session=False,
    )
    db.commit()
    if not claimed:
        db.refresh(row)
        return
    previously_notified = db.query(ProfileConflictReview).filter(
        ProfileConflictReview.effective_group_id == row.effective_group_id,
        ProfileConflictReview.notified_at.isnot(None),
        ProfileConflictReview.id != row.id,
    ).first() is not None
    try:
        created = await create_notification_internal(
            notification_type="warning",
            title="Channel profile conflict needs review",
            message=message,
            source=NOTIFICATION_SOURCE,
            source_id=source_id,
            action_label="Review choices",
            action_url=None,
            metadata=metadata,
            send_alerts=not previously_notified,
        )
    except Exception:
        created = None
    if created is None:
        db.query(ProfileConflictReview).filter(
            ProfileConflictReview.id == row.id,
            ProfileConflictReview.notified_at == claimed_at,
        ).update(
            {ProfileConflictReview.notified_at: None},
            synchronize_session=False,
        )
        db.commit()
        row.notified_at = None
    else:
        row.notified_at = claimed_at


def _finish_notification(db, row: ProfileConflictReview, outcome: dict) -> None:
    notification = db.query(Notification).filter(
        Notification.source == NOTIFICATION_SOURCE,
        Notification.source_id == f"conflict:{row.effective_group_id}",
    ).order_by(Notification.id.desc()).first()
    if notification is None:
        return
    if row.applied_at is None:
        notification.type = "warning"
        notification.title = "Channel profile choice saved; retry pending"
        notification.message = (
            "The choice is saved, but convergence or its operator audit is still "
            "pending. ECM will retry."
        )
        notification.read = False
    else:
        db.delete(notification)
    db.commit()


def _selected_choice(evidence: dict, accepted_ids: list[int]) -> dict:
    selected = tuple(sorted(accepted_ids))
    for choice in evidence.get("choices", []):
        if tuple(choice.get("profile_ids", [])) == selected:
            return choice
    return {"profile_ids": list(selected), "profile_names": [], "sources": []}


def _journal_harmonization(
    row: ProfileConflictReview,
    evidence: dict,
    actor: str,
    outcome: dict,
    *,
    retry: bool,
) -> bool:
    accepted_ids = json.loads(row.accepted_profile_ids or "[]")
    return journal.log_entry(
        category="m3u",
        action_type="profile_conflict_retry" if retry else "profile_conflict_accept",
        entity_id=row.effective_group_id,
        entity_name=evidence.get("target", {}).get("name", str(row.effective_group_id)),
        description=(
            ("Retried" if retry else "Accepted")
            + " channel-profile selection for effective group "
            + f"{row.effective_group_id}; source settings harmonized where reachable"
        ),
        before_value={
            "status": REVIEW_STATUS_ACCEPTED if retry else REVIEW_STATUS_PENDING,
            "fingerprint": row.fingerprint,
        },
        after_value={
            "status": REVIEW_STATUS_ACCEPTED,
            "choice_key": row.accepted_choice_key,
            "profile_ids": accepted_ids,
            "target": evidence.get("target", {}),
            "selected_choice": _selected_choice(evidence, accepted_ids),
            "actor_token_id": actor,
            **outcome,
        },
        user_initiated=True,
    ) is not None


def _record_harmonization(
    db,
    row: ProfileConflictReview,
    evidence: dict,
    actor: str,
    outcome: dict,
    *,
    retry: bool,
) -> None:
    audit_failed = False
    if row.accept_journaled_at is None:
        accepted_actor = row.actor_token_id or actor
        if _journal_harmonization(
            row, evidence, accepted_actor, outcome, retry=False
        ):
            row.accept_journaled_at = now_epoch_ms()
        else:
            audit_failed = True
    if retry and not _journal_harmonization(row, evidence, actor, outcome, retry=True):
        audit_failed = True
    errors = [outcome["retry_error"]] if outcome["retry_error"] else []
    if audit_failed:
        errors.append("Operator audit entry pending; ECM will retry")
    row.retry_error = "; ".join(errors) if errors else None
    row.applied_at = (
        None if outcome["failed_account_ids"] or audit_failed else now_epoch_ms()
    )
    db.commit()


async def ensure_profile_conflict_review(
    client, all_settings: dict, effective_gid: int
) -> ProfileConflictReview | None:
    shape = _shape_for_effective(all_settings, effective_gid)
    if shape is None:
        return None
    evidence = await _build_evidence(client, shape)
    fingerprint = conflict_fingerprint(evidence)
    now_ms = now_epoch_ms()
    db = get_session()
    try:
        row = db.query(ProfileConflictReview).filter(
            ProfileConflictReview.fingerprint == fingerprint
        ).first()
        retry_row = db.query(ProfileConflictReview).filter(
            ProfileConflictReview.effective_group_id == effective_gid,
            ProfileConflictReview.status == REVIEW_STATUS_ACCEPTED,
            ProfileConflictReview.applied_at.is_(None),
        ).order_by(ProfileConflictReview.id.desc()).first()
        if row is not None and row.status == REVIEW_STATUS_ACCEPTED:
            accepted_ids = json.loads(row.accepted_profile_ids or "[]")
            outcome = await _retry_harmonize_review_sources(
                client, row.effective_group_id, json.loads(row.evidence), accepted_ids
            )
            row.last_seen_at = now_ms
            _record_harmonization(
                db, row, json.loads(row.evidence), row.actor_token_id or "unknown",
                outcome, retry=True,
            )
            _finish_notification(db, row, outcome)
            return row
        if row is None and retry_row is not None:
            original = json.loads(retry_row.evidence)
            accepted_ids = json.loads(retry_row.accepted_profile_ids or "[]")
            if _retry_compatible(original, evidence, accepted_ids):
                outcome = await _retry_harmonize_review_sources(
                    client, retry_row.effective_group_id, original, accepted_ids
                )
                retry_row.last_seen_at = now_ms
                _record_harmonization(
                    db, retry_row, original, retry_row.actor_token_id or "unknown",
                    outcome, retry=True,
                )
                _finish_notification(db, retry_row, outcome)
                return retry_row
            retry_row.status = REVIEW_STATUS_SUPERSEDED
            retry_row.resolved_at = now_ms
            retry_row.retry_error = "Conflict shape changed before all source rows converged"
            db.commit()
        if row is None or row.status == REVIEW_STATUS_SUPERSEDED:
            pending = db.query(ProfileConflictReview).filter(
                ProfileConflictReview.effective_group_id == effective_gid,
                ProfileConflictReview.status == REVIEW_STATUS_PENDING,
            ).all()
            for old in pending:
                old.status = REVIEW_STATUS_SUPERSEDED
                old.resolved_at = now_ms
            if row is None:
                row = ProfileConflictReview(
                    fingerprint=fingerprint,
                    fingerprint_version=FINGERPRINT_VERSION,
                    effective_group_id=effective_gid,
                    status=REVIEW_STATUS_PENDING,
                    evidence=json.dumps(evidence, sort_keys=True),
                    created_at=now_ms,
                    last_seen_at=now_ms,
                )
                db.add(row)
            else:
                row.status = REVIEW_STATUS_PENDING
                row.evidence = json.dumps(evidence, sort_keys=True)
                row.last_seen_at = now_ms
                row.resolved_at = None
                row.accepted_choice_key = None
                row.accepted_profile_ids = None
                row.actor_token_id = None
                row.retry_error = None
                row.applied_at = None
                row.notified_at = None
                row.accept_journaled_at = None
        elif row.status == REVIEW_STATUS_PENDING:
            row.last_seen_at = now_ms
            row.evidence = json.dumps(evidence, sort_keys=True)
        db.commit()
        db.refresh(row)
        if row.status == REVIEW_STATUS_PENDING:
            await _sync_notification(db, row, evidence)
        return row
    finally:
        db.close()


async def harmonize_review_sources(
    client, evidence: dict, selected_profile_ids: list[int], current_rows: dict
) -> dict:
    """PATCH divergent participating rows using current full rows; no membership write."""
    from routers.m3u import merge_group_settings_row
    from services.profile_reconcile import coerce_profile_id

    selected = sorted(set(int(pid) for pid in selected_profile_ids))
    source_pairs = {
        (source.get("m3u_account_id"), source["source_group_id"])
        for choice in evidence.get("choices", [])
        for source in choice.get("sources", [])
    }
    by_account: dict[int, list[dict]] = {}
    unavailable_accounts: set[int] = set()
    for aid, gid in sorted(source_pairs):
        if aid is None:
            continue
        current = current_rows.get((aid, gid))
        if not isinstance(current, dict):
            unavailable_accounts.add(aid)
            continue
        cp = dict(current.get("custom_properties") or {})
        raw = cp.get("channel_profile_ids")
        current_selected = []
        if isinstance(raw, list):
            current_selected = sorted({
                coerced for value in raw
                if (coerced := coerce_profile_id(value)) is not None
            })
        if current_selected == selected:
            continue
        if selected:
            cp["channel_profile_ids"] = selected
        else:
            cp.pop("channel_profile_ids", None)
        cp.pop("_ecm_channel_profile_conflict", None)
        by_account.setdefault(aid, []).append(merge_group_settings_row(
            current, {"channel_group": gid, "custom_properties": cp}
        ))
    updated = []
    failed = sorted(unavailable_accounts)
    errors = [f"account {aid}: participating source row unavailable" for aid in failed]
    for aid, rows in sorted(by_account.items()):
        try:
            await client.update_m3u_group_settings(aid, {"group_settings": rows})
            updated.append(aid)
        except Exception as exc:  # noqa: BLE001 - durable retry records each account
            if aid not in failed:
                failed.append(aid)
            errors.append(f"account {aid}: {exc}")
    return {
        "updated_account_ids": updated,
        "failed_account_ids": sorted(failed),
        "retry_error": "; ".join(errors) if errors else None,
    }


async def _retry_harmonize_review_sources(
    client, effective_group_id: int, evidence: dict, selected_profile_ids: list[int]
) -> dict:
    from services.profile_reconcile import acquire_effective_group_locks

    async with acquire_effective_group_locks([effective_group_id]):
        all_settings = await client.get_all_m3u_group_settings()
        return await harmonize_review_sources(
            client,
            evidence,
            selected_profile_ids,
            _current_source_rows(all_settings),
        )


async def accept_profile_conflict_review(
    db, client, review_id: int, selected_choice_key: str, actor: str
) -> dict:
    """Validate the live question under its lock, persist, then harmonize rows."""
    from services.profile_reconcile import acquire_effective_group_locks

    row = db.query(ProfileConflictReview).filter(ProfileConflictReview.id == review_id).first()
    if row is None:
        raise ReviewNotFound()
    async with acquire_effective_group_locks([row.effective_group_id]):
        # The request may have waited behind another accept for this effective
        # group. Discard its pre-lock identity-map snapshot before deciding which
        # state transition is still valid.
        db.expire_all()
        row = db.query(ProfileConflictReview).filter(
            ProfileConflictReview.id == review_id
        ).first()
        if row is None:
            raise ReviewNotFound()
        if row.status == REVIEW_STATUS_SUPERSEDED:
            raise StaleReview()
        if row.status == REVIEW_STATUS_ACCEPTED and row.applied_at is not None:
            if selected_choice_key != row.accepted_choice_key:
                raise InvalidChoice()
            return {
                "status": REVIEW_STATUS_ACCEPTED,
                "applied": True,
                "updated_account_ids": [],
                "failed_account_ids": [],
                "retry_error": None,
            }

        all_settings = await client.get_all_m3u_group_settings()
        shape = _shape_for_effective(all_settings, row.effective_group_id)
        original_evidence = json.loads(row.evidence)
        current_rows = _current_source_rows(all_settings)
        original_pairs = {
            (source.get("m3u_account_id"), source.get("source_group_id"))
            for choice in original_evidence.get("choices", [])
            for source in choice.get("sources", [])
            if source.get("m3u_account_id") is not None
        }
        if not original_pairs.issubset(current_rows):
            raise ParticipatingSourceMissing()

        if row.status == REVIEW_STATUS_ACCEPTED:
            if selected_choice_key != row.accepted_choice_key:
                raise InvalidChoice()
            accepted_ids = json.loads(row.accepted_profile_ids or "[]")
            if shape is not None:
                current_evidence = await _build_evidence(client, shape)
                if not _retry_compatible(
                    original_evidence, current_evidence, accepted_ids
                ):
                    raise StaleReview()
            # A prior partial write may have removed the conflict entirely. The
            # persisted decision still owns retries for the same source rows.
            current_evidence = original_evidence
            choice = None
        else:
            if shape is None:
                raise StaleReview()
            current_evidence = await _build_evidence(client, shape)
            if conflict_fingerprint(current_evidence) != row.fingerprint:
                raise StaleReview()
            choice = next(
                (
                    item for item in current_evidence.get("choices", [])
                    if item.get("choice_key") == selected_choice_key
                ),
                None,
            )
            if choice is None:
                raise InvalidChoice()

        now_ms = now_epoch_ms()
        retry = row.status == REVIEW_STATUS_ACCEPTED
        if not retry:
            row.status = REVIEW_STATUS_ACCEPTED
            row.accepted_choice_key = selected_choice_key
            row.accepted_profile_ids = json.dumps(choice["profile_ids"])
            row.actor_token_id = actor
            row.resolved_at = now_ms
            row.retry_error = None
            db.commit()  # Decision is durable before any remote mutation.

        accepted_ids = json.loads(row.accepted_profile_ids or "[]")
        outcome = await harmonize_review_sources(
            client, current_evidence, accepted_ids, current_rows
        )
        _record_harmonization(
            db, row, original_evidence, actor, outcome, retry=retry
        )
        _finish_notification(db, row, outcome)
        return {
            "status": REVIEW_STATUS_ACCEPTED,
            "applied": row.applied_at is not None,
            **outcome,
            "retry_error": row.retry_error,
        }


async def reconcile_profile_conflict_reviews(client, all_settings: dict) -> dict:
    """Converge the durable review queue against one full settings snapshot."""
    active: dict[int, dict] = {}
    for setting in all_settings.values():
        if not isinstance(setting, dict):
            continue
        shape = setting.get("_ecm_profile_conflict_shape")
        if isinstance(shape, dict) and shape.get("effective_group_id") is not None:
            active[int(shape["effective_group_id"])] = shape

    for effective_gid in sorted(active):
        await ensure_profile_conflict_review(client, all_settings, effective_gid)

    now_ms = now_epoch_ms()
    db = get_session()
    try:
        pending = db.query(ProfileConflictReview).filter(
            ProfileConflictReview.status == REVIEW_STATUS_PENDING,
        ).all()
        retired = 0
        for row in pending:
            if row.effective_group_id in active:
                continue
            row.status = REVIEW_STATUS_SUPERSEDED
            row.resolved_at = now_ms
            notification = db.query(Notification).filter(
                Notification.source == NOTIFICATION_SOURCE,
                Notification.source_id == f"conflict:{row.effective_group_id}",
            ).first()
            if notification is not None:
                db.delete(notification)
            retired += 1
        retry_ids = [
            row.id for row in db.query(ProfileConflictReview).filter(
                ProfileConflictReview.status == REVIEW_STATUS_ACCEPTED,
                ProfileConflictReview.applied_at.is_(None),
            ).all()
            if row.effective_group_id not in active
        ]
        db.commit()
    finally:
        db.close()

    retried = 0
    for review_id in retry_ids:
        retry_db = get_session()
        try:
            row = retry_db.query(ProfileConflictReview).filter(
                ProfileConflictReview.id == review_id
            ).first()
            if row is None:
                continue
            await accept_profile_conflict_review(
                retry_db, client, row.id, row.accepted_choice_key,
                row.actor_token_id or "unknown",
            )
            retried += 1
        except ParticipatingSourceMissing:
            retry_db.rollback()
            row = retry_db.query(ProfileConflictReview).filter(
                ProfileConflictReview.id == review_id,
                ProfileConflictReview.status == REVIEW_STATUS_ACCEPTED,
                ProfileConflictReview.applied_at.is_(None),
            ).first()
            if row is not None:
                row.status = REVIEW_STATUS_SUPERSEDED
                row.resolved_at = now_epoch_ms()
                row.retry_error = (
                    "Participating source provenance disappeared before convergence"
                )
                notification = retry_db.query(Notification).filter(
                    Notification.source == NOTIFICATION_SOURCE,
                    Notification.source_id == f"conflict:{row.effective_group_id}",
                ).first()
                if notification is not None:
                    retry_db.delete(notification)
                retry_db.commit()
                retired += 1
        except (ReviewNotFound, StaleReview, InvalidChoice):
            retry_db.rollback()
        finally:
            retry_db.close()
    return {"active": len(active), "retired": retired, "retried": retried}
