import pytest

from services.mutation_plan_store import PLAN_MAX_BYTES, MutationPlanStore


def test_plan_is_content_bound_and_single_use():
    store = MutationPlanStore()
    plan = store.create("logos", {"ids": ["a"]}, "state")
    assert store.consume(plan.plan_id, "logos", plan.payload_hash, principal="api") == plan
    with pytest.raises(ValueError, match="already consumed"):
        store.consume(plan.plan_id, "logos", plan.payload_hash, principal="api")


def test_wrong_hash_does_not_destroy_plan():
    store = MutationPlanStore()
    plan = store.create("logos", {"ids": ["a"]}, "state")
    with pytest.raises(ValueError, match="does not match"):
        store.consume(plan.plan_id, "logos", "wrong", principal="api")
    assert store.consume(
        plan.plan_id, "logos", plan.payload_hash, principal="api"
    ) == plan


def test_invalid_claims_do_not_consume_legitimate_plan():
    store = MutationPlanStore()
    plan = store.create("pipeline", {"writes": [1]}, "state", principal="mcp-a")
    for operation, digest, principal in [
        ("other", plan.payload_hash, "mcp-a"),
        ("pipeline", "wrong", "mcp-a"),
        ("pipeline", plan.payload_hash, "mcp-b"),
    ]:
        with pytest.raises(ValueError):
            store.consume(plan.plan_id, operation, digest, principal=principal)
    assert store.consume(
        plan.plan_id, "pipeline", plan.payload_hash, principal="mcp-a"
    ) == plan


def test_oversized_plan_is_rejected_without_storage():
    store = MutationPlanStore()
    with pytest.raises(ValueError, match="exceeds"):
        store.create("huge", {"value": "x" * PLAN_MAX_BYTES}, "state")
