import pytest

from services.mutation_plan_store import PLAN_MAX_BYTES, MutationPlanStore


def test_plan_is_content_bound_and_single_use():
    store = MutationPlanStore()
    plan = store.create("logos", {"ids": ["a"]}, "state")
    assert store.consume(plan.plan_id, "logos", plan.payload_hash) == plan
    with pytest.raises(ValueError, match="already consumed"):
        store.consume(plan.plan_id, "logos", plan.payload_hash)


def test_wrong_hash_consumes_plan_fail_closed():
    store = MutationPlanStore()
    plan = store.create("logos", {"ids": ["a"]}, "state")
    with pytest.raises(ValueError, match="does not match"):
        store.consume(plan.plan_id, "logos", "wrong")
    with pytest.raises(ValueError):
        store.consume(plan.plan_id, "logos", plan.payload_hash)


def test_oversized_plan_is_rejected_without_storage():
    store = MutationPlanStore()
    with pytest.raises(ValueError, match="exceeds"):
        store.create("huge", {"value": "x" * PLAN_MAX_BYTES}, "state")
