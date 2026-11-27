"""The browser fixture e2e/fixtures/p3-sync-task-results.json is generated here.

Each TaskResult comes from the real sync engine and the real task mapper. The
destination is the in-memory test harness, so no live instance is involved. The
test fails whenever the committed fixture drifts from what the mapper produces.

Regenerate from backend/ with:

    python -m tests.tasks.test_p3_sync_e2e_fixture
"""

import asyncio
import json
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tasks.dbas_sync import DbasSyncTask

FIXTURE = Path(__file__).parents[3] / "e2e" / "fixtures" / "p3-sync-task-results.json"
_FIELDS = (
    "success",
    "message",
    "total_items",
    "success_count",
    "failed_count",
    "skipped_count",
    "error",
)


async def generate(ledger_dir: Path) -> dict:
    """Build the two TaskResults the browser spec replays."""
    from tests.tasks.test_dbas_sync_destination_readable import (
        _http_error,
        _preview,
        _refusing_dest_client,
    )
    from tests.tasks.test_dbas_sync_engine import _empty_dest_client, _sync_target

    preview = await _preview(_sync_target(), _empty_dest_client(), ledger_dir)
    aborted = await _preview(
        _sync_target(), _refusing_dest_client(_http_error(401)), ledger_dir,
        confirm_apply=True,
    )
    results = {}
    for name, report in (("preview", preview), ("abortedApply", aborted)):
        result = DbasSyncTask()._result_from_report(datetime.now(UTC), report)
        results[name] = {field: getattr(result, field) for field in _FIELDS}
    return results


def render(results: dict) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False) + "\n"


@pytest.mark.asyncio
async def test_committed_browser_fixture_matches_the_mapper(tmp_path):
    generated = await generate(tmp_path)
    assert generated["preview"]["success"] is True
    assert "stream: not predicted" in generated["preview"]["message"]
    assert generated["abortedApply"]["error"] == "SYNC_DESTINATION_UNREADABLE"
    assert FIXTURE.read_text() == render(generated)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as ledger:
        FIXTURE.write_text(render(asyncio.run(generate(Path(ledger)))))
