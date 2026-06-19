"""Tests for the Phase-2 DBAS restore PRE-FLIGHT validator
(enhancedchannelmanager-0i2vt.18, part 1 of 2).

Scope under test:

1. A plan with an UNRESOLVABLE FK (a channel pointing at a channel_group that is
   neither in the archive nor on the destination) → pre-flight FAILS.
2. A plan with a DUPLICATE required-unique name → pre-flight FAILS.
3. A plan with a category count OUT OF BOUNDS → pre-flight FAILS.
4. An UNSUPPORTED schema_version → pre-flight FAILS (the .17 gate at the
   orchestration entry).
5. A clean plan → pre-flight PASSES.
6. FK satisfaction by in-archive entity AND by the existing destination remap.
7. Pre-flight is PURE — it accumulates ALL problems in one pass.

Pre-flight is synchronous and client-free, so no mocks are needed.
"""

from dbas.preflight import (
    ImportPlan,
    PlanCategory,
    PreflightProblemKind,
    run_preflight,
)
from dbas.restore_contracts import EntityType, IdRemapTable

# A manifest whose schema_version this build supports (BACKUP_SCHEMA_VERSION == 1).
_GOOD_MANIFEST = {"schema_version": 1}


def _plan(*categories, manifest=None, existing_remap=None):
    return ImportPlan(
        manifest=manifest if manifest is not None else dict(_GOOD_MANIFEST),
        categories=list(categories),
        existing_remap=existing_remap or IdRemapTable(),
    )


def _cat(entity_type, entities, selected=True):
    return PlanCategory(entity_type=entity_type, entities=entities, selected=selected)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_plan_passes():
    plan = _plan(
        _cat(EntityType.CHANNEL_GROUP, [{"id": 10, "name": "Sports"}]),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN", "channel_group_id": 10}]),
    )
    result = run_preflight(plan)
    assert result.passed is True
    assert result.problems == []


# ---------------------------------------------------------------------------
# 1. Unresolvable FK
# ---------------------------------------------------------------------------


def test_unresolvable_fk_fails():
    # Channel references group 99, which is neither in the archive nor on dest.
    plan = _plan(
        _cat(EntityType.CHANNEL_GROUP, [{"id": 10, "name": "Sports"}]),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN", "channel_group_id": 99}]),
    )
    result = run_preflight(plan)
    assert result.passed is False
    kinds = {p.kind for p in result.problems}
    assert PreflightProblemKind.UNRESOLVED_FK_REFERENCE in kinds


def test_fk_satisfied_by_archived_entity():
    plan = _plan(
        _cat(EntityType.CHANNEL_GROUP, [{"id": 10, "name": "Sports"}]),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN", "channel_group_id": 10}]),
    )
    assert run_preflight(plan).passed is True


def test_fk_satisfied_by_existing_destination_remap():
    # Group 10 is NOT in the archive, but the destination already has it (remap).
    remap = IdRemapTable()
    remap.add(EntityType.CHANNEL_GROUP, 10, 110)
    plan = _plan(
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN", "channel_group_id": 10}]),
        existing_remap=remap,
    )
    assert run_preflight(plan).passed is True


def test_fk_to_unselected_category_fails():
    # The group category exists but the operator did NOT select it, so it will not
    # be created — the channel's FK is therefore unresolved.
    plan = _plan(
        _cat(EntityType.CHANNEL_GROUP, [{"id": 10, "name": "Sports"}], selected=False),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN", "channel_group_id": 10}]),
    )
    result = run_preflight(plan)
    assert result.passed is False
    assert any(p.kind == PreflightProblemKind.UNRESOLVED_FK_REFERENCE for p in result.problems)


# ---------------------------------------------------------------------------
# 2. Duplicate required-unique name
# ---------------------------------------------------------------------------


def test_duplicate_unique_name_fails():
    plan = _plan(
        _cat(
            EntityType.M3U_ACCOUNT,
            [{"id": 1, "name": "Provider"}, {"id": 2, "name": "provider"}],  # case-insensitive dup
        ),
    )
    result = run_preflight(plan)
    assert result.passed is False
    assert any(p.kind == PreflightProblemKind.DUPLICATE_UNIQUE_NAME for p in result.problems)


def test_unique_names_distinct_passes():
    plan = _plan(
        _cat(
            EntityType.CHANNEL_GROUP,
            [{"id": 1, "name": "Sports"}, {"id": 2, "name": "News"}],
        ),
    )
    assert run_preflight(plan).passed is True


# ---------------------------------------------------------------------------
# 3. Count out of bounds
# ---------------------------------------------------------------------------


def test_count_out_of_bounds_fails():
    entities = [{"id": i, "name": f"g{i}"} for i in range(10)]
    plan = _plan(_cat(EntityType.CHANNEL_GROUP, entities))
    result = run_preflight(plan, max_entities_per_category=5)
    assert result.passed is False
    assert any(p.kind == PreflightProblemKind.COUNT_OUT_OF_BOUNDS for p in result.problems)


def test_count_within_bounds_passes():
    entities = [{"id": i, "name": f"g{i}"} for i in range(3)]
    plan = _plan(_cat(EntityType.CHANNEL_GROUP, entities))
    assert run_preflight(plan, max_entities_per_category=5).passed is True


# ---------------------------------------------------------------------------
# 4. Unsupported schema version (.17 gate)
# ---------------------------------------------------------------------------


def test_unsupported_schema_version_fails():
    plan = _plan(
        _cat(EntityType.CHANNEL_GROUP, [{"id": 10, "name": "Sports"}]),
        manifest={"schema_version": 9999},
    )
    result = run_preflight(plan)
    assert result.passed is False
    assert any(p.kind == PreflightProblemKind.UNSUPPORTED_SCHEMA_VERSION for p in result.problems)


def test_missing_schema_version_fails():
    plan = _plan(
        _cat(EntityType.CHANNEL_GROUP, [{"id": 10, "name": "Sports"}]),
        manifest={},  # no schema_version
    )
    result = run_preflight(plan)
    assert result.passed is False
    assert any(p.kind == PreflightProblemKind.UNSUPPORTED_SCHEMA_VERSION for p in result.problems)


# ---------------------------------------------------------------------------
# 7. Accumulates ALL problems in one pass
# ---------------------------------------------------------------------------


def test_accumulates_all_problems():
    plan = _plan(
        _cat(
            EntityType.M3U_ACCOUNT,
            [{"id": 1, "name": "Dup"}, {"id": 2, "name": "Dup"}],  # duplicate name
        ),
        _cat(EntityType.CHANNEL, [{"id": 1, "name": "ESPN", "channel_group_id": 77}]),  # bad FK
        manifest={"schema_version": 9999},  # bad version
    )
    result = run_preflight(plan)
    assert result.passed is False
    kinds = {p.kind for p in result.problems}
    assert PreflightProblemKind.DUPLICATE_UNIQUE_NAME in kinds
    assert PreflightProblemKind.UNRESOLVED_FK_REFERENCE in kinds
    assert PreflightProblemKind.UNSUPPORTED_SCHEMA_VERSION in kinds


def test_problem_messages_carry_no_secrets():
    # Even if an archive entity carried credential-looking fields, the problem
    # message is built from the safe label (name) only.
    plan = _plan(
        _cat(
            EntityType.M3U_ACCOUNT,
            [
                {"id": 1, "name": "Prov", "password": "hunter2"},
                {"id": 2, "name": "Prov", "password": "hunter2"},
            ],
        ),
    )
    result = run_preflight(plan)
    for problem in result.problems:
        assert "hunter2" not in problem.message
