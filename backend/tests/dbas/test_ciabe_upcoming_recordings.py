"""The ``upcoming_recordings`` backup category (bead enhancedchannelmanager-ciabe).

Before this category, NOTHING in a DBAS backup carried a DVR recording. The
``dvr_rules`` category carries recurring recording RULES
(``/api/channels/recurring-rules/``); the recording INSTANCES
(``/api/channels/recordings/``) were excluded wholesale as instance-state, so a
restore silently produced a replica with no scheduled recordings at all.

THE INVARIANT these tests are written against. Every assertion below is an
example of it, never its specification::

    The ``upcoming_recordings`` category carries exactly those Dispatcharr
    recording instances that (a) have not started yet at gather time and
    (b) no other backup category regenerates.

    On restore it never creates a recording the destination already holds,
    never creates one whose scheduled start has already passed, and never
    sends a source-instance id upstream: an unresolvable reference is NAMED
    and COUNTED as a blocked entity rather than silently dropped.

    Everything the category does NOT carry is stated to the operator.

MEASURED, NOT ASSUMED. The contract facts these tests encode were captured live
against ``ghcr.io/dispatcharr/dispatcharr:latest`` == **0.29.0** and are recorded
in ``tests/fixtures/dispatcharr_recordings_recorded.json``. Two of them would
have shipped as defects if taken from the model source alone:

* the destination REFUSES a create whose ``end_time`` has passed
  (``400 {"non_field_errors":["End time must be in the future."]}``), so the
  restore-side staleness gate is mandatory, not cosmetic;
* the destination MUTATES ``custom_properties`` after the create (it wrote its
  own ``poster_logo_id`` between the 201 and the very next GET), so
  ``custom_properties`` can never be part of the collision identity — and the
  server may re-serialize ``start_time`` to a different STRING than the one
  posted, so timestamp identity compares parsed instants, never text.

Conventions: ``docs/pytest_conventions.md``.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import routers.backup as backup_mod
from dbas.importers.settings_agents import import_upcoming_recordings
from dbas.preflight import ImportPlan, PlanCategory
from dbas.restore_artifact import _SECTION_TO_ENTITY
from dbas.restore_contracts import (
    EntityType,
    FailureReason,
    IdRemapTable,
    RestoreReport,
    RollbackLedger,
    SkipReason,
)
from dbas.restore_orchestrator import (
    NON_FATAL_FAILURE_CATEGORIES,
    _delete_dispatch,
    default_importer_steps,
    dry_run_importer_steps,
)

_RECORDED = json.loads(
    (
        Path(__file__).parent.parent
        / "fixtures"
        / "dispatcharr_recordings_recorded.json"
    ).read_text()
)

SECTION = "upcoming_recordings"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rec(rec_id: int, *, channel: int, starts_in: timedelta, length_hours: int = 1,
         **extra) -> dict:
    """An archived recording row shaped exactly like the recorded live rows."""
    start = _now() + starts_in
    row = {
        "id": rec_id,
        "channel": channel,
        "start_time": _iso(start),
        "end_time": _iso(start + timedelta(hours=length_hours)),
        "task_id": "dvr-recording-%d" % rec_id,
        "custom_properties": {},
    }
    row.update(extra)
    return row


def _scoped_remap(*selected: EntityType, channels: dict[int, int] | None = None):
    remap = IdRemapTable()
    remap.record_run_scope(selected)
    for source, dest in (channels or {}).items():
        remap.add(EntityType.CHANNEL, source, dest)
    return remap


def _ledger() -> RollbackLedger:
    return RollbackLedger(restore_id="ciabe-test")


def _reasons(report: RestoreReport) -> list[SkipReason]:
    return [d.reason for d in report.category(EntityType.UPCOMING_RECORDING).skip_details]


class _Dest:
    """A destination that remembers the recordings it was given."""

    def __init__(self, existing: list[dict] | None = None, reject: bool = False):
        self.rows = list(existing or [])
        self.created: list[dict] = []
        self.deleted: list[int] = []
        self.reject = reject
        self._next = 900

    async def get_recordings(self) -> list:
        return list(self.rows)

    async def create_recording(self, data: dict) -> dict:
        if self.reject:
            raise RuntimeError("End time must be in the future.")
        self.created.append(dict(data))
        self._next += 1
        row = dict(data)
        row["id"] = self._next
        row["task_id"] = "dvr-recording-%d" % self._next
        self.rows.append(row)
        return row

    async def delete_recording(self, recording_id: int) -> None:
        self.deleted.append(recording_id)


async def _run_import(
    *,
    archive: list[dict],
    dest: _Dest,
    remap: IdRemapTable,
    selected: bool = True,
    is_dry_run: bool = False,
    report: RestoreReport | None = None,
    ledger: RollbackLedger | None = None,
) -> RestoreReport:
    report = report or RestoreReport(is_dry_run=is_dry_run)
    await import_upcoming_recordings(
        archive_recordings=archive,
        client=dest,
        selected=selected,
        report=report,
        ledger=ledger or _ledger(),
        remap=remap,
        is_dry_run=is_dry_run,
    )
    return report


# ===========================================================================
# PRODUCER — what the category carries
# ===========================================================================


def test_the_category_is_registered_as_an_artifact_only_dispatcharr_section():
    """The category exists at all, and is artifact-only like its DVR sibling."""
    info = backup_mod.RESTORABLE_SECTIONS[SECTION]
    assert info["dispatcharr"] is True
    assert info["artifact_only"] is True
    assert "Upcoming Recordings" == info["label"]


@pytest.mark.asyncio
async def test_only_recordings_that_have_not_started_are_archived():
    """(a) of the invariant. The discriminator is ``start_time > now`` — the same
    predicate Dispatcharr's own bulk-delete-upcoming view uses to mean upcoming.
    An in-progress recording is NOT upcoming: it is already writing a file on the
    source's disk, and restoring it would start a partial capture on the
    destination."""
    rows = [
        _rec(1, channel=5, starts_in=timedelta(hours=6)),            # upcoming
        _rec(2, channel=5, starts_in=timedelta(minutes=-10)),         # in progress
        _rec(3, channel=5, starts_in=timedelta(days=-2)),             # finished
    ]
    client = AsyncMock()
    client.get_recordings = AsyncMock(return_value=rows)
    with patch.object(backup_mod, "get_client", return_value=client):
        out = await backup_mod._gather_dispatcharr_sections({SECTION})
    assert [r["id"] for r in out[SECTION]] == [1]


@pytest.mark.asyncio
async def test_a_recording_a_recurring_rule_regenerates_is_not_archived():
    """(b) of the invariant. A recording carrying ``custom_properties.rule.id`` is
    OUTPUT of the ``dvr_rules`` category, not independent state: restoring the
    rule makes the destination's own hourly ``maintain_recurring_recordings``
    beat regenerate it (Dispatcharr ``dispatcharr/settings.py`` schedules it at
    3600s; ``sync_recurring_rule_impl`` recreates the next 14 days). Archiving it
    too applies the same state twice — and the maintainer's own de-dup checks
    ``custom_properties__rule__id`` against the DESTINATION rule id while
    recomputing ``start_time`` in the DESTINATION's timezone, so a replayed row
    duplicates rather than merges."""
    rows = [
        _rec(1, channel=5, starts_in=timedelta(hours=6)),
        _rec(
            2,
            channel=5,
            starts_in=timedelta(hours=7),
            custom_properties={"rule": {"type": "recurring", "id": 41}},
        ),
    ]
    client = AsyncMock()
    client.get_recordings = AsyncMock(return_value=rows)
    with patch.object(backup_mod, "get_client", return_value=client):
        out = await backup_mod._gather_dispatcharr_sections({SECTION})
    assert [r["id"] for r in out[SECTION]] == [1]


@pytest.mark.asyncio
async def test_a_recording_with_an_unreadable_start_time_is_never_archived():
    """Fail-safe. A row ECM cannot classify is not proven upcoming, and the cost
    of guessing wrong is a phantom recording on the replica."""
    rows = [
        _rec(1, channel=5, starts_in=timedelta(hours=6)),
        {"id": 2, "channel": 5, "start_time": None, "end_time": None},
        {"id": 3, "channel": 5, "start_time": "not-a-timestamp"},
    ]
    client = AsyncMock()
    client.get_recordings = AsyncMock(return_value=rows)
    with patch.object(backup_mod, "get_client", return_value=client):
        out = await backup_mod._gather_dispatcharr_sections({SECTION})
    assert [r["id"] for r in out[SECTION]] == [1]


@pytest.mark.asyncio
async def test_the_gather_survives_a_recordings_endpoint_that_errors():
    """Per-fetch isolation: a failing recordings endpoint degrades ONLY this
    category, exactly like every other Dispatcharr-backed section."""
    client = AsyncMock()
    client.get_recordings = AsyncMock(side_effect=RuntimeError("boom"))
    with patch.object(backup_mod, "get_client", return_value=client):
        out = await backup_mod._gather_dispatcharr_sections({SECTION})
    assert "_warning" in out[SECTION]


# ===========================================================================
# PRODUCER — what the category does NOT carry is stated, not silent
# ===========================================================================


@pytest.mark.asyncio
async def test_the_excluded_recordings_are_counted_for_the_operator():
    """Third clause of the invariant. ADR-013: an exclusion must be VISIBLE.
    A silent filter and a named one are indistinguishable in the artifact, so the
    census is what makes the difference to the operator reading the run report."""
    backup_mod._RECORDINGS_EXCLUDED.set(None)
    rows = [
        _rec(1, channel=5, starts_in=timedelta(hours=6)),
        _rec(2, channel=5, starts_in=timedelta(minutes=-10)),
        _rec(3, channel=5, starts_in=timedelta(days=-2)),
        _rec(4, channel=5, starts_in=timedelta(days=-9)),
        _rec(
            5,
            channel=5,
            starts_in=timedelta(hours=8),
            custom_properties={"rule": {"id": 41}},
        ),
    ]
    client = AsyncMock()
    client.get_recordings = AsyncMock(return_value=rows)
    with patch.object(backup_mod, "get_client", return_value=client):
        await backup_mod._gather_dispatcharr_sections({SECTION})
    census = backup_mod._RECORDINGS_EXCLUDED.get()
    assert census["already_started"] == 3
    assert census["regenerated_by_a_rule"] == 1


def test_the_backup_artifact_carries_the_exclusion_census():
    """The count has to reach the operator, so it has to leave the builder."""
    artifact = backup_mod.BackupArtifact(
        zip_path=Path("/tmp/x.zip"),
        sidecar_path=Path("/tmp/x.zip.sha256"),
        schema_version="1",
        sha256="0" * 64,
        file_count=1,
        recordings_excluded_already_started=4,
    )
    assert artifact.recordings_excluded_already_started == 4


def test_the_run_report_names_the_completed_exclusion_and_the_manual_action():
    """A count with no remedy is not an exclusion notice. The operator has to be
    told what to do by hand, in the run report, not only in the docs."""
    from tasks.dbas_backup import describe_recording_exclusions

    line = describe_recording_exclusions(4)
    assert "4" in line
    lowered = line.lower()
    assert "already started" in lowered or "completed" in lowered
    # The manual action: the media files live on the source instance's disk.
    assert "copy" in lowered
    assert "" != line

    assert describe_recording_exclusions(0) == ""


def test_the_user_guide_names_both_recording_exclusions():
    """ADR-013: visible in ``backup-overview``'s category list, not only in code."""
    doc = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "user_guide"
        / "backup-restore"
        / "backup-overview.md"
    ).read_text()
    lowered = doc.lower()
    # The category itself is listed (the doc's table is sentence-case).
    assert "upcoming recordings" in lowered
    # …and BOTH exclusions are named, with the manual action for the one that
    # has one. A category list that adds the new row and stays silent about what
    # it leaves out is exactly the gap ADR-013's principle forbids.
    assert "already started or finished" in lowered
    assert "copy them" in lowered
    assert "recurring rule" in lowered


# ===========================================================================
# CONTRACTS
# ===========================================================================


def test_a_refused_recording_never_rolls_back_the_restore():
    """Nothing in the restore holds an FK into a recording — the admission test
    for :data:`NON_FATAL_FAILURE_CATEGORIES`. One recording Dispatcharr will not
    schedule must not cost the operator their channels."""
    assert EntityType.UPCOMING_RECORDING in NON_FATAL_FAILURE_CATEGORIES


def test_the_artifact_section_decodes_to_the_category():
    assert _SECTION_TO_ENTITY[SECTION] is EntityType.UPCOMING_RECORDING


def test_the_category_is_wired_into_both_registries_in_the_same_place():
    """Dry-run parity from day one: the preview runs the same importer, over the
    same category, in the same position as the apply."""
    apply_order = [s.entity_type for s in default_importer_steps()]
    preview_order = [s.entity_type for s in dry_run_importer_steps()]
    assert EntityType.UPCOMING_RECORDING in apply_order
    assert apply_order.index(EntityType.UPCOMING_RECORDING) > apply_order.index(
        EntityType.CHANNEL
    )
    assert apply_order == preview_order
    assert all(s.importer is not None for s in default_importer_steps())
    assert all(s.importer is not None for s in dry_run_importer_steps())


def test_a_created_recording_can_be_compensated():
    """A created row must be deletable, or the rollback ledger cannot undo it."""
    client = AsyncMock()
    assert EntityType.UPCOMING_RECORDING in _delete_dispatch(client)


# ===========================================================================
# IMPORTER
# ===========================================================================


@pytest.mark.asyncio
async def test_a_recording_whose_start_has_passed_is_never_created():
    """Second clause of the invariant, and the measured reason it is mandatory:
    the destination answers 400 "End time must be in the future." to a stale
    create (recorded fixture, create_responses[2]). Skipping is not politeness —
    replaying an archive taken last week would otherwise fail every row upstream.
    """
    assert _RECORDED["create_responses"][2]["status_code"] == 400

    dest = _Dest()
    report = await _run_import(
        archive=[
            _rec(1, channel=5, starts_in=timedelta(days=-3)),
            _rec(2, channel=5, starts_in=timedelta(hours=6)),
        ],
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, channels={5: 55}
        ),
    )
    cat = report.category(EntityType.UPCOMING_RECORDING)
    assert cat.created == 1
    assert cat.failed == 0
    assert SkipReason.SCHEDULE_ALREADY_PAST in _reasons(report)
    assert [c["channel"] for c in dest.created] == [55]
    # A faithful absence, never a delivery shortfall: the destination cannot
    # record a programme that has already aired.
    assert report.entities_blocked_by_dependency == 0


@pytest.mark.asyncio
async def test_an_unresolvable_channel_is_named_and_counted_as_a_blocked_entity():
    """Third clause. Bead 4mkoe's recorder, not a bespoke one: the count, the
    per-entity reason and the drill-down are written by the same call."""
    dest = _Dest()
    report = await _run_import(
        archive=[_rec(1, channel=404, starts_in=timedelta(hours=6))],
        dest=dest,
        remap=_scoped_remap(EntityType.CHANNEL, EntityType.UPCOMING_RECORDING),
    )
    assert report.entities_blocked_by_dependency == 1
    assert _reasons(report) == [SkipReason.DEPENDENCY_UNRESOLVED]
    assert report.category(EntityType.UPCOMING_RECORDING).failed == 0
    assert dest.created == []


@pytest.mark.asyncio
async def test_the_preview_reports_the_same_blocked_entity_as_the_apply():
    """Preview parity (y6zg6). Whether the channel resolves is a fact about the
    run's remap state, not about the mode — a dry-run that certified a
    would-create here would be a preview that lies."""
    archive = [_rec(1, channel=404, starts_in=timedelta(hours=6))]
    preview = await _run_import(
        archive=archive,
        dest=_Dest(),
        remap=_scoped_remap(EntityType.CHANNEL, EntityType.UPCOMING_RECORDING),
        is_dry_run=True,
    )
    applied = await _run_import(
        archive=archive,
        dest=_Dest(),
        remap=_scoped_remap(EntityType.CHANNEL, EntityType.UPCOMING_RECORDING),
    )
    assert _reasons(preview) == _reasons(applied) == [SkipReason.DEPENDENCY_UNRESOLVED]
    assert preview.entities_blocked_by_dependency == 1
    assert preview.category(EntityType.UPCOMING_RECORDING).would_skip == 1
    assert preview.category(EntityType.UPCOMING_RECORDING).would_create == 0


@pytest.mark.asyncio
async def test_restoring_the_same_archive_twice_creates_one_recording():
    """First clause. Identity is (destination channel, start, end) — NOT
    ``custom_properties``, which the recorded capture proves the destination
    rewrites of its own accord between the 201 and the next GET."""
    assert "poster_logo_id" in (
        _RECORDED["populated_list_response"]["body"][0]["custom_properties"]
    )
    assert _RECORDED["create_responses"][0]["body"]["custom_properties"] == {}

    archive = [_rec(1, channel=5, starts_in=timedelta(hours=6))]
    remap_kwargs = dict(channels={5: 55})
    dest = _Dest()
    await _run_import(
        archive=archive,
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, **remap_kwargs
        ),
    )
    # The destination has now decorated the row exactly as 0.29.0 does.
    dest.rows[0]["custom_properties"] = {"poster_logo_id": 316}

    second = await _run_import(
        archive=archive,
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, **remap_kwargs
        ),
    )
    assert len(dest.created) == 1
    assert second.category(EntityType.UPCOMING_RECORDING).created == 0
    assert SkipReason.ALREADY_EXISTS_IDENTICAL in _reasons(second)


@pytest.mark.asyncio
async def test_a_timestamp_the_server_reserialized_is_still_the_same_recording():
    """The recorded capture posted ``...23:06:58Z`` and got ``...23:16:58.511980Z``
    back. A string comparison would have duplicated every such row on the next
    restore; identity compares parsed instants."""
    start = _now() + timedelta(hours=6)
    archive = [
        {
            "id": 1,
            "channel": 5,
            "start_time": _iso(start),
            "end_time": _iso(start + timedelta(hours=1)),
        }
    ]
    existing = [
        {
            "id": 900,
            "channel": 55,
            # Same instant, different serialization: microseconds + offset form.
            "start_time": start.isoformat(),
            "end_time": (start + timedelta(hours=1)).isoformat(),
        }
    ]
    dest = _Dest(existing=existing)
    report = await _run_import(
        archive=archive,
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, channels={5: 55}
        ),
    )
    assert dest.created == []
    assert report.category(EntityType.UPCOMING_RECORDING).created == 0
    assert SkipReason.ALREADY_EXISTS_IDENTICAL in _reasons(report)


@pytest.mark.asyncio
async def test_no_source_instance_id_is_ever_sent_upstream():
    """Third clause. ``id`` and the server-assigned read-only ``task_id`` are the
    source instance's own, and the recorded schema marks both readOnly."""
    assert _RECORDED["openapi_slice"]["components"]["schemas"]["Recording"][
        "properties"
    ]["task_id"]["readOnly"] is True

    dest = _Dest()
    await _run_import(
        archive=[_rec(1, channel=5, starts_in=timedelta(hours=6))],
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, channels={5: 55}
        ),
    )
    sent = dest.created[0]
    assert "id" not in sent
    assert "task_id" not in sent
    assert sent["channel"] == 55


@pytest.mark.asyncio
async def test_the_preview_creates_nothing():
    dest = _Dest()
    report = await _run_import(
        archive=[_rec(1, channel=5, starts_in=timedelta(hours=6))],
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, channels={5: 55}
        ),
        is_dry_run=True,
    )
    assert dest.created == []
    assert report.category(EntityType.UPCOMING_RECORDING).would_create == 1
    assert report.category(EntityType.UPCOMING_RECORDING).created == 0


@pytest.mark.asyncio
async def test_a_deselected_category_creates_nothing_and_says_why():
    dest = _Dest()
    report = await _run_import(
        archive=[_rec(1, channel=5, starts_in=timedelta(hours=6))],
        dest=dest,
        remap=_scoped_remap(EntityType.CHANNEL, channels={5: 55}),
        selected=False,
    )
    assert dest.created == []
    assert _reasons(report) == [SkipReason.EXCLUDED_BY_OPERATOR]
    assert report.entities_blocked_by_dependency == 0


@pytest.mark.asyncio
async def test_an_upstream_refusal_is_a_counted_failure_not_a_silent_drop():
    dest = _Dest(reject=True)
    ledger = _ledger()
    report = await _run_import(
        archive=[_rec(1, channel=5, starts_in=timedelta(hours=6))],
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, channels={5: 55}
        ),
        ledger=ledger,
    )
    cat = report.category(EntityType.UPCOMING_RECORDING)
    assert cat.failed == 1
    assert cat.created == 0
    assert cat.failure_details[0].reason in (
        FailureReason.UPSTREAM_API_ERROR,
        FailureReason.CONFLICT,
    )


@pytest.mark.asyncio
async def test_a_created_recording_is_ledgered_for_rollback():
    dest = _Dest()
    ledger = _ledger()
    await _run_import(
        archive=[_rec(1, channel=5, starts_in=timedelta(hours=6))],
        dest=dest,
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, channels={5: 55}
        ),
        ledger=ledger,
    )
    assert any(
        e.entity_type == EntityType.UPCOMING_RECORDING for e in ledger.entries
    )


@pytest.mark.asyncio
async def test_the_plan_slice_reaches_the_importer_through_the_orchestrator():
    """The wiring is real, not just registered: a plan carrying the category
    reaches the importer and produces a create."""
    from dbas.restore_orchestrator import ApplyContext

    plan = ImportPlan(
        categories=[
            PlanCategory(
                entity_type=EntityType.UPCOMING_RECORDING,
                entities=[_rec(1, channel=5, starts_in=timedelta(hours=6))],
                selected=True,
            )
        ]
    )
    dest = _Dest()
    ctx = ApplyContext(
        plan=plan,
        client=dest,
        report=RestoreReport(is_dry_run=False),
        ledger=_ledger(),
        remap=_scoped_remap(
            EntityType.CHANNEL, EntityType.UPCOMING_RECORDING, channels={5: 55}
        ),
        is_dry_run=False,
    )
    step = next(
        s
        for s in default_importer_steps()
        if s.entity_type is EntityType.UPCOMING_RECORDING
    )
    await step.importer(ctx)
    assert [c["channel"] for c in dest.created] == [55]
