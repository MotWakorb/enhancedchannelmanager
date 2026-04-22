"""
Nightly normalization canary harness (bd-eio04.9).

Runs every fixture in ``backend/tests/fixtures/unicode_fixtures.py``
through BOTH normalization code paths and asserts byte-identical output.
Exits with status 1 on any divergence so the scheduling workflow
(`.github/workflows/normalization-canary.yml`) goes red.

Paths under test
----------------

Path 1 — HTTP preview (Test Rules):
    ``POST /api/normalization/test-batch`` via FastAPI TestClient. This
    proves the full request boundary — URL routing, JSON encoding
    (especially for Unicode), middleware, offload-to-thread-pool — does
    not mutate input or output on the way through.

Path 2 — direct executor call:
    ``NormalizationEngine.normalize(input)`` invoked in-process, mirroring
    exactly what ``auto_creation_executor.ActionExecutor`` does when
    creating channels.

Both paths share the same ``NormalizationPolicy`` by construction
(bd-eio04.1). Any observable divergence in this canary means that
contract broke.

What counts as divergence
-------------------------

1. ``normalized`` output byte-mismatch.
2. ``rules_applied`` / ``transformations`` rule-id list mismatch.

Any single fixture failing either check aborts the run with a non-zero
exit code and prints a compact report to stdout. The workflow then:
  * increments ``ecm_normalization_canary_divergence_total`` (SLI source).
  * fires a Slack alert if the webhook secret is set.
  * falls back to opening a GH issue so there's always a durable
    record of the breach.

Running locally
---------------

    cd backend
    python -m scripts.normalization_canary

The harness is deterministic — the same fixture bank with the same
``NormalizationPolicy`` yields the same outcome on every run. There is
no randomness, no network, no real database. It uses the in-memory
SQLite fixture stack the rest of the test suite relies on.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# Make backend/ importable whether the harness is invoked as
# ``python -m scripts.normalization_canary`` or directly.
_THIS_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _THIS_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Ensure the test config dir exists before any backend module reads it.
os.environ.setdefault("CONFIG_DIR", "/tmp/ecm_canary_config")
os.environ.setdefault("RATE_LIMIT_ENABLED", "0")
Path(os.environ["CONFIG_DIR"]).mkdir(parents=True, exist_ok=True)

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402  — registers tables
from normalization_engine import (  # noqa: E402
    NormalizationEngine,
    get_default_policy,
)
from tests.fixtures.unicode_fixtures import ALL_FIXTURES, NormalizationFixture  # noqa: E402


@dataclass
class CanaryDivergence:
    """One recorded mismatch between Test Rules and Auto-Create."""
    fixture_name: str
    input: str
    http_output: str
    executor_output: str
    http_rules: list
    executor_rules: list
    reason: str


def _build_in_memory_engine() -> tuple[Any, NormalizationEngine]:
    """Return a session + engine bound to a fresh in-memory SQLite DB.

    The canary does NOT seed any user rules — policy-level preprocessing
    (NFC, Cf-stripping, superscript conversion) runs unconditionally via
    NormalizationPolicy and is what we're actually validating. If per-rule
    divergence becomes a concern, a follow-up bead should extend this
    harness with a seeded rule pack.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    database.Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine, expire_on_commit=False
    )
    session = SessionLocal()
    return session, NormalizationEngine(session)


def _run_executor_path(engine: NormalizationEngine, fixture: NormalizationFixture) -> dict:
    """Path 2 — direct ``engine.normalize(input)`` call."""
    result = engine.normalize(fixture.input)
    return {
        "normalized": result.normalized,
        "rules_applied": list(result.rules_applied),
    }


def _run_http_path(client, fixture: NormalizationFixture) -> dict:
    """Path 1 — ``POST /api/normalization/test-batch`` via TestClient.

    The endpoint returns ``{"results": [{"original", "normalized",
    "rules_applied", "transformations"}]}`` so we flatten the first
    result to the same shape as the executor path.
    """
    response = client.post(
        "/api/normalization/test-batch",
        json={"texts": [fixture.input]},
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"test-batch HTTP {response.status_code}: {response.text[:200]}"
        )
    body = response.json()
    results = body.get("results") or []
    if not results:
        raise RuntimeError("test-batch returned empty results array")
    first = results[0]
    return {
        "normalized": first.get("normalized", ""),
        "rules_applied": [
            t.get("rule_id")
            for t in (first.get("transformations") or [])
            if t.get("rule_id") is not None
        ],
    }


def _compare_outputs(
    fixture: NormalizationFixture,
    http_out: dict,
    executor_out: dict,
) -> Optional[CanaryDivergence]:
    """Return a CanaryDivergence if the two paths disagree, else None."""
    http_norm = http_out["normalized"]
    exec_norm = executor_out["normalized"]
    http_rules = http_out["rules_applied"]
    exec_rules = executor_out["rules_applied"]

    if http_norm != exec_norm:
        return CanaryDivergence(
            fixture_name=fixture.name,
            input=fixture.input,
            http_output=http_norm,
            executor_output=exec_norm,
            http_rules=http_rules,
            executor_rules=exec_rules,
            reason="normalized output byte-mismatch",
        )
    if http_rules != exec_rules:
        return CanaryDivergence(
            fixture_name=fixture.name,
            input=fixture.input,
            http_output=http_norm,
            executor_output=exec_norm,
            http_rules=http_rules,
            executor_rules=exec_rules,
            reason="matched_rule_ids mismatch",
        )
    return None


def run_canary(verbose: bool = True) -> list[CanaryDivergence]:
    """Execute the canary sweep. Returns the list of divergences (empty on pass)."""
    # Late-import TestClient / main so a missing FastAPI install fails
    # loudly in the harness rather than at module import time.
    from fastapi.testclient import TestClient

    import main as main_module

    session, engine_instance = _build_in_memory_engine()
    # Bind the in-memory session to the FastAPI dependency injector so
    # test-batch uses the same DB the executor path uses. ``main.app``
    # plus ``database.get_session`` override is the standard harness
    # pattern in this codebase.
    from database import get_session as real_get_session  # noqa: F401

    def _override_get_session():
        return session

    main_module.app.dependency_overrides[
        database.get_session
    ] = _override_get_session

    divergences: list[CanaryDivergence] = []
    try:
        with TestClient(main_module.app) as client:
            policy = get_default_policy()
            if verbose:
                print(
                    f"[canary] fixtures={len(ALL_FIXTURES)} policy_version="
                    f"{'unified-v1' if policy.unified_enabled else 'legacy'}"
                )
            for fixture in ALL_FIXTURES:
                try:
                    http_out = _run_http_path(client, fixture)
                    executor_out = _run_executor_path(engine_instance, fixture)
                except Exception as exc:
                    divergences.append(
                        CanaryDivergence(
                            fixture_name=fixture.name,
                            input=fixture.input,
                            http_output="<exception>",
                            executor_output="<exception>",
                            http_rules=[],
                            executor_rules=[],
                            reason=f"harness error: {exc}",
                        )
                    )
                    continue
                mismatch = _compare_outputs(fixture, http_out, executor_out)
                if mismatch is not None:
                    divergences.append(mismatch)
                    if verbose:
                        print(f"[canary] DIVERGE {fixture.name}: {mismatch.reason}")
                elif verbose:
                    print(f"[canary] ok      {fixture.name}")
    finally:
        main_module.app.dependency_overrides.pop(database.get_session, None)
        session.close()

    return divergences


def emit_metric_on_divergence(count: int) -> None:
    """Increment ``ecm_normalization_canary_divergence_total`` once per failed run.

    The counter represents **runs that failed**, not **individual fixtures
    that diverged**, so any non-zero divergence list counts as exactly
    one breach per the SLO-5 error budget policy.
    """
    if count <= 0:
        return
    try:
        from observability import get_metric, install_metrics

        install_metrics()
        get_metric("normalization_canary_divergence_total").inc()
    except Exception as exc:  # pragma: no cover
        print(f"[canary] metric emission failed: {exc}", file=sys.stderr)


def main() -> int:
    divergences = run_canary(verbose=True)
    if not divergences:
        print(f"[canary] PASS — all {len(ALL_FIXTURES)} fixtures match across paths.")
        return 0

    emit_metric_on_divergence(len(divergences))
    print(f"[canary] FAIL — {len(divergences)} divergence(s) detected:")
    report = {
        "fixture_count": len(ALL_FIXTURES),
        "divergence_count": len(divergences),
        "divergences": [
            {
                "fixture": d.fixture_name,
                "input": d.input,
                "http_output": d.http_output,
                "executor_output": d.executor_output,
                "http_rules": d.http_rules,
                "executor_rules": d.executor_rules,
                "reason": d.reason,
            }
            for d in divergences
        ],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
