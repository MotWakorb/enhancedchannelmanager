"""Bounded, restart-fail-closed storage for short-lived mutation plans."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any


PLAN_TTL_SECONDS = 300
PLAN_MAX_ENTRIES = 128
PLAN_MAX_BYTES = 2 * 1024 * 1024


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class MutationPlan:
    plan_id: str
    operation: str
    payload: dict[str, Any]
    payload_hash: str
    state_hash: str
    principal: str
    created_at: float
    expires_at: float


class MutationPlanStore:
    """Process-local plans. A restart intentionally invalidates every plan."""

    def __init__(self) -> None:
        self._plans: dict[str, MutationPlan] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        self._plans = {key: plan for key, plan in self._plans.items() if plan.expires_at > now}
        if len(self._plans) >= PLAN_MAX_ENTRIES:
            oldest = sorted(self._plans.values(), key=lambda plan: plan.created_at)
            for plan in oldest[: len(self._plans) - PLAN_MAX_ENTRIES + 1]:
                self._plans.pop(plan.plan_id, None)

    def create(self, operation: str, payload: dict[str, Any], state_hash: str, principal: str = "api") -> MutationPlan:
        now = time.time()
        encoded_size = len(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
        if encoded_size > PLAN_MAX_BYTES:
            raise ValueError(f"mutation plan exceeds {PLAN_MAX_BYTES} byte limit")
        plan = MutationPlan(
            plan_id=uuid.uuid4().hex,
            operation=operation,
            payload=payload,
            payload_hash=canonical_hash(payload),
            state_hash=state_hash,
            principal=principal,
            created_at=now,
            expires_at=now + PLAN_TTL_SECONDS,
        )
        with self._lock:
            self._prune(now)
            self._plans[plan.plan_id] = plan
        return plan

    def consume(self, plan_id: str, operation: str, payload_hash: str) -> MutationPlan:
        now = time.time()
        with self._lock:
            self._prune(now)
            plan = self._plans.pop(plan_id, None)
        if plan is None:
            raise ValueError("plan is expired, unknown, or already consumed")
        if plan.operation != operation or plan.payload_hash != payload_hash:
            raise ValueError("plan operation or hash does not match")
        return plan


mutation_plan_store = MutationPlanStore()
