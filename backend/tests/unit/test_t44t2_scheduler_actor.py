"""W3-follow-up A (enhancedchannelmanager-t44t2): scheduled-task journal
attribution.

Scheduled/background tasks run with NO HTTP request, so the actor-source
middleware (``main.py``) never fires and their ``journal.log_entry`` rows would
land with ``mutation_source=NULL``. The task engine therefore stamps the
``"scheduler"`` actor at its single execution chokepoint
(``TaskEngine._execute_task``) for runs whose ``triggered_by == "scheduled"``,
via the same contextvar the middleware uses, so EVERY journal call made inside a
scheduled run picks it up automatically.

These tests drive the chokepoint directly (no real task registry / DB) and
assert:

* (scheduler)    a journal call made inside a scheduled run records "scheduler"
* (explicit-wins) an explicit ``mutation_source="auto_creation"`` call inside a
  scheduled run still records "auto_creation" — explicit beats the contextvar
* (no-context)   outside any scheduled run the source stays None (legacy/NULL)
* (no-leak)      the contextvar is reset after the job, even on exception
* (manual)       a ``triggered_by="manual"`` run does NOT stamp "scheduler"
  (the HTTP middleware owns ui/mcp_ai attribution for those)
"""
import asyncio

import journal
from task_engine import _scheduler_mutation_source_scope


def _capture_resolved_source():
    """Resolve what ``journal.log_entry`` (no explicit source) would persist."""
    return journal._resolve_mutation_source(None)


class TestSchedulerMutationSourceScope:
    def test_scheduled_run_stamps_scheduler(self):
        # Inside the scope opened for a scheduled run, an implicit journal call
        # resolves to "scheduler".
        captured = {}

        async def body():
            captured["inside"] = _capture_resolved_source()

        async def run():
            with _scheduler_mutation_source_scope("scheduled"):
                await body()

        asyncio.run(run())
        assert captured["inside"] == journal.MUTATION_SOURCE_SCHEDULER

    def test_explicit_auto_creation_wins_over_scheduler(self):
        # An auto-creation run triggered BY the scheduler journals its channel
        # mutations with an explicit "auto_creation" source; explicit must win
        # over the scheduler contextvar.
        captured = {}

        async def run():
            with _scheduler_mutation_source_scope("scheduled"):
                captured["explicit"] = journal._resolve_mutation_source(
                    journal.MUTATION_SOURCE_AUTO_CREATION
                )
                captured["implicit"] = journal._resolve_mutation_source(None)

        asyncio.run(run())
        assert captured["explicit"] == journal.MUTATION_SOURCE_AUTO_CREATION
        assert captured["implicit"] == journal.MUTATION_SOURCE_SCHEDULER

    def test_outside_any_run_source_is_none(self):
        # No scheduled scope, no HTTP request -> NULL (legacy/unknown actor).
        assert _capture_resolved_source() is None

    def test_contextvar_reset_after_scope(self):
        async def run():
            with _scheduler_mutation_source_scope("scheduled"):
                pass
            return _capture_resolved_source()

        assert asyncio.run(run()) is None

    def test_contextvar_reset_after_scope_on_exception(self):
        async def run():
            try:
                with _scheduler_mutation_source_scope("scheduled"):
                    raise RuntimeError("boom")
            except RuntimeError:
                pass
            return _capture_resolved_source()

        assert asyncio.run(run()) is None

    def test_manual_run_does_not_stamp_scheduler(self):
        # Manual/API runs carry an HTTP request whose middleware already set
        # ui/mcp_ai; the engine must NOT override that with "scheduler".
        captured = {}

        async def run():
            with _scheduler_mutation_source_scope("manual"):
                captured["inside"] = _capture_resolved_source()

        asyncio.run(run())
        assert captured["inside"] is None

    def test_api_run_does_not_stamp_scheduler(self):
        captured = {}

        async def run():
            with _scheduler_mutation_source_scope("api"):
                captured["inside"] = _capture_resolved_source()

        asyncio.run(run())
        assert captured["inside"] is None

    def test_manual_run_preserves_existing_source(self):
        # If a request already stamped "ui" (middleware), a manual run inside it
        # leaves it intact.
        token = journal.set_mutation_source(journal.MUTATION_SOURCE_UI)
        try:
            captured = {}

            async def run():
                with _scheduler_mutation_source_scope("manual"):
                    captured["inside"] = _capture_resolved_source()

            asyncio.run(run())
            assert captured["inside"] == journal.MUTATION_SOURCE_UI
        finally:
            journal.reset_mutation_source(token)
