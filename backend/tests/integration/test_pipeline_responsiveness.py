"""GH1031: actual pipeline runs must share the loop with health and polling."""

import asyncio
import json
import threading
from datetime import UTC, datetime
from itertools import count
from time import monotonic
from unittest.mock import AsyncMock, MagicMock

import channel_pipeline_engine as pipeline
import journal
import pytest
from channel_pipeline_evaluator import ConditionEvaluator
from channel_pipeline_executor import ActionExecutor
from config import DispatcharrSettings
from models import ChannelPipelineExecution, ChannelPipelineRule, JournalEntry
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pipeline_workload(test_session, test_engine, monkeypatch):
    """Real rules, evaluator, engine, SQLite lifecycle; only upstream HTTP is fake."""
    # Expire the cooperative time slice deterministically. asyncio scheduling
    # and HTTP middleware remain real; CPU speed cannot decide whether a short
    # provider-skip phase ends before the health response traverses middleware.
    ticks = count()
    monkeypatch.setattr(pipeline, "monotonic", lambda: next(ticks) / 1000)
    settings = DispatcharrSettings()
    settings.max_auto_creation_log_entries = 0
    monkeypatch.setattr(pipeline, "get_settings", lambda: settings)
    owner_thread = threading.get_ident()
    sessions = []
    factory = sessionmaker(bind=test_engine, expire_on_commit=False)

    def get_session():
        assert threading.get_ident() == owner_thread
        session = factory()
        sessions.append(session)
        event.listen(session, "after_begin", assert_owner)
        return session

    def assert_owner(*args):
        assert threading.get_ident() == owner_thread

    monkeypatch.setattr(pipeline, "get_session", get_session)
    client = MagicMock()
    client.get_channels = AsyncMock(return_value={"count": 0, "results": []})
    client.get_channel_groups = AsyncMock(return_value=[])
    client.get_m3u_accounts = AsyncMock(return_value=[{"id": 1, "name": "Test"}])
    engine = pipeline.ChannelPipelineEngine(client)

    def prepare(kind="unmatched", stream_count=1200, rule_count=330):
        rules = []
        for index in range(rule_count):
            rule = ChannelPipelineRule(
                name=f"Rule {index}",
                priority=index,
                enabled=True,
                m3u_account_id=2 if kind == "provider_skipped" else None,
                stop_on_first_match=False,
                sort_field="stream_name",
                sort_order="desc",
            )
            rule.set_conditions(
                [
                    {"type": "stream_name_matches", "value": "^Keep"},
                    {"type": "stream_name_matches", "value": "[0-9]+$"},
                    {"type": "stream_name_matches", "value": "^(Keep|Other)"},
                ]
            )
            rule.set_actions([{"type": "skip"}, {"type": "stop_processing"}])
            rules.append(rule)
        test_session.add_all(rules)
        execution = ChannelPipelineExecution(
            mode="execute",
            triggered_by="api",
            status="running",
            started_at=datetime.now(UTC).replace(tzinfo=None),
        )
        test_session.add(execution)
        test_session.commit()
        streams = [
            {"id": index + 1, "name": f"Other {index:04d}"}
            for index in range(stream_count)
        ]
        client.get_streams = AsyncMock(
            side_effect=lambda **kw: {
                "count": len(streams),
                "results": streams[(kw["page"] - 1) * 1000 : kw["page"] * 1000],
            }
        )
        return execution, rules, streams

    return engine, client, prepare, sessions, settings


def observe_evaluation(monkeypatch):
    """Wake the concurrent caller from inside Pass 1, never before it starts."""
    started = asyncio.Event()
    counts = {"conditions": 0, "rules": 0}
    original_info = pipeline.logger.info
    original_evaluate = ConditionEvaluator.evaluate
    original_provider_ids = pipeline._required_provider_ids

    def info(message, *args, **kwargs):
        if message == "[AUTO-CREATE-ENGINE] Evaluating %s streams against %s rules":
            started.set()
        original_info(message, *args, **kwargs)

    def evaluate(self, *args, **kwargs):
        counts["conditions"] += 1
        return original_evaluate(self, *args, **kwargs)

    def provider_ids(rule):
        if started.is_set():
            counts["rules"] += 1
        return original_provider_ids(rule)

    monkeypatch.setattr(pipeline.logger, "info", info)
    monkeypatch.setattr(ConditionEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(pipeline, "_required_provider_ids", provider_ids)
    return started, counts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "logical_clock"),
    [("unmatched", True), ("provider_skipped", True), ("unmatched", False)],
    ids=["unmatched", "provider-skipped", "real-clock"],
)
async def test_health_and_execution_poll_during_actual_evaluation(
    async_client,
    pipeline_workload,
    monkeypatch,
    kind,
    logical_clock,
):
    if not logical_clock:
        monkeypatch.setattr(pipeline, "monotonic", monotonic)
    engine, client, prepare, sessions, _settings = pipeline_workload
    execution, rules, streams = prepare(kind)
    started, counts = observe_evaluation(monkeypatch)
    task = asyncio.create_task(
        engine.run_pipeline(
            execution_id=execution.id,
            m3u_account_ids=[1],
            triggered_by="api",
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=30)
        health = await async_client.get("/api/health")
        poll = await async_client.get(f"/api/auto-creation/executions/{execution.id}")
        during = dict(counts)
        still_running = not task.done()
        result = await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert health.status_code == 200
    assert health.json()["status"] == "healthy"
    assert poll.status_code == 200
    assert still_running, "HTTP responses were serialized behind the full evaluation"
    assert poll.json()["status"] == "running"
    assert 0 < during["rules"] < len(streams) * len(rules)
    assert result["streams_evaluated"] == len(streams)
    assert result["streams_matched"] == 0
    assert result["status"] == "completed"
    assert counts["conditions"] == (
        0 if kind == "provider_skipped" else len(streams) * len(rules) * 3
    )
    client.create_channel.assert_not_called()
    assert sessions and all(not session.in_transaction() for session in sessions)
    final = await async_client.get(f"/api/auto-creation/executions/{execution.id}")
    assert final.json()["status"] == "completed"
    assert final.json()["streams_evaluated"] == len(streams)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["filters", "excluded", "sort", "actions", "struck"])
async def test_other_catalog_phases_share_the_loop(
    async_client,
    pipeline_workload,
    test_session,
    monkeypatch,
    phase,
):
    engine, _client, prepare, _sessions, settings = pipeline_workload
    # Keep all 1200 streams and the 330 exclusion terms below. One rule is
    # enough after filtering (and for sort/actions/struck); repeating Pass 1's
    # full rule matrix here adds coverage cost without testing this phase.
    execution, rules, streams = prepare(rule_count=1)
    started = asyncio.Event()
    finished = asyncio.Event()
    count_seen = 0

    if phase in {"filters", "excluded"}:
        # These cases own the stream x exclusion-term workload, not another
        # stream x rule evaluation (covered above, including the real clock).
        _, evaluations = observe_evaluation(monkeypatch)
        settings.auto_creation_excluded_terms = [f"Forbidden {i}" for i in range(330)]
        if phase == "excluded":
            settings.auto_creation_excluded_terms.append("Other")
        original = engine._apply_global_filters

        async def filter_streams(*args):
            started.set()
            try:
                return await original(*args)
            finally:
                finished.set()

        monkeypatch.setattr(engine, "_apply_global_filters", filter_streams)
    else:
        for stream in streams:
            stream["name"] = stream["name"].replace("Other", "Keep")
        rules[0].stop_on_first_match = True
        if phase == "sort":
            rules[0].sort_field = "stream_name_regex"
            rules[0].sort_regex = r"(\d+)$"
            original = pipeline._sort_key

            def sort_key(*args):
                nonlocal count_seen
                started.set()
                count_seen += 1
                result = original(*args)
                if count_seen == len(streams):
                    finished.set()
                return result

            monkeypatch.setattr(pipeline, "_sort_key", sort_key)
        elif phase == "actions":
            original = ActionExecutor.execute

            async def execute(self, *args, **kwargs):
                nonlocal count_seen
                started.set()
                result = await original(self, *args, **kwargs)
                count_seen += 1
                if count_seen == 2 * len(streams):
                    finished.set()
                return result

            monkeypatch.setattr(ActionExecutor, "execute", execute)
        else:
            rules[0].skip_struck_streams = True

            class StruckStreams(set):
                def __contains__(self, stream_id):
                    nonlocal count_seen
                    started.set()
                    count_seen += 1
                    if count_seen == len(streams):
                        finished.set()
                    return True

            original = engine._load_stream_stats

            async def load_stats():
                await original()
                engine._struck_stream_ids = StruckStreams()

            monkeypatch.setattr(engine, "_load_stream_stats", load_stats)
        test_session.commit()

    task = asyncio.create_task(
        engine.run_pipeline(
            execution_id=execution.id,
            m3u_account_ids=[1],
            triggered_by="api",
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=30)
        health = await async_client.get("/api/health")
        responded_during_phase = not finished.is_set()
        result = await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert health.status_code == 200
    assert responded_during_phase, f"{phase} monopolized the loop"
    assert finished.is_set()
    assert result["status"] == "completed"
    if phase == "excluded":
        assert result["streams_excluded"] == len(streams)
        assert result["streams_evaluated"] == 0
        assert evaluations["conditions"] == 0
    elif phase == "filters":
        assert result["streams_excluded"] == 0
        assert result["streams_evaluated"] == len(streams)
        assert evaluations["conditions"] == len(streams) * 3
    elif phase == "struck":
        assert result["streams_skipped_struck"] == len(streams)
        assert result["streams_matched"] == 0
    else:
        assert result["streams_matched"] == len(streams)
        assert result["streams_skipped"] == len(streams)
        assert [entry["stream_id"] for entry in result["execution_log"]] == list(
            range(len(streams), 0, -1)
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supervised", [False, True], ids=["engine", "background-worker"]
)
async def test_cancel_actual_run_during_evaluation(
    async_client,
    pipeline_workload,
    monkeypatch,
    supervised,
):
    from routers.channel_pipeline import _supervise_background_pipeline

    engine, client, prepare, sessions, _settings = pipeline_workload
    execution, rules, streams = prepare()
    started, counts = observe_evaluation(monkeypatch)
    coro = engine.run_pipeline(
        execution_id=execution.id,
        m3u_account_ids=[1],
        triggered_by="api",
    )
    task = (
        _supervise_background_pipeline(coro, execution_id=execution.id, label="test")
        if supervised
        else asyncio.create_task(coro)
    )
    await asyncio.wait_for(started.wait(), timeout=30)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)
    assert 0 < counts["conditions"] < len(streams) * len(rules) * 3
    stopped_at = dict(counts)
    await asyncio.sleep(0)
    assert counts == stopped_at  # No orphaned worker continues after cancellation.
    client.create_channel.assert_not_called()
    assert all(not session.in_transaction() for session in sessions)
    poll = await async_client.get(f"/api/auto-creation/executions/{execution.id}")
    assert poll.status_code == 200
    if supervised:
        assert poll.json()["status"] == "failed"
        assert "Background task cancelled" in poll.json()["error_message"]
    else:
        assert poll.json()["status"] == "running"


class _JournalShutdown(BaseException):
    """Exercise cleanup BaseException handling without stopping pytest itself."""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("journal_fault", "cancel_run"),
    [
        (None, True),
        ("commit", True),
        ("session", True),
        ("shutdown", True),
        ("session", False),
        ("shutdown", False),
    ],
    ids=[
        "persist-cancel",
        "commit-cancel",
        "session-cancel",
        "shutdown-cancel",
        "session-clean",
        "shutdown-clean",
    ],
)
async def test_pass2_merge_journal_cleanup_preserves_primary_exit(
    async_client,
    pipeline_workload,
    test_session,
    monkeypatch,
    caplog,
    journal_fault,
    cancel_run,
):
    engine, client, prepare, sessions, _settings = pipeline_workload
    execution, rules, streams = prepare(stream_count=3, rule_count=1)
    for stream in streams:
        stream["name"] = stream["name"].replace("Other", "Keep")
    rules[0].set_actions(
        [
            {
                "type": "merge_streams",
                "target": "existing_channel",
                "find_channel_by": "name_exact",
                "find_channel_value": "Merge target",
            }
        ]
    )
    test_session.commit()
    destination = {
        "id": 42,
        "name": "Merge target",
        "streams": [900],
        "channel_number": 42,
        "auto_created": True,
    }
    client.get_channels.return_value = {"count": 1, "results": [dict(destination)]}

    async def update_channel(channel_id, data):
        assert channel_id == destination["id"]
        destination["streams"] = list(data["streams"])
        return dict(destination)

    client.update_channel = AsyncMock(side_effect=update_channel)
    executors = []

    def build_executor(*args, **kwargs):
        executor = ActionExecutor(*args, **kwargs)
        executors.append(executor)
        return executor

    monkeypatch.setattr(pipeline, "ActionExecutor", build_executor)
    session_factory = pipeline.get_session
    flush_error = (
        _JournalShutdown("synthetic journal shutdown")
        if journal_fault == "shutdown"
        else OSError("synthetic journal session unavailable")
    )

    def journal_session():
        if journal_fault in {"session", "shutdown"}:
            raise flush_error
        session = session_factory()
        if journal_fault == "commit":

            def fail_commit(_session):
                raise RuntimeError("synthetic journal commit failed")

            event.listen(session, "before_commit", fail_commit)
        return session

    monkeypatch.setattr(journal, "get_session", journal_session)
    original_checkpoint = pipeline._PipelineWorkBudget.checkpoint
    checkpoint_reached = False

    async def cancel_after_buffered_merge(budget):
        nonlocal checkpoint_reached
        if cancel_run and executors and executors[0]._journal_buffer:
            checkpoint_reached = True
            assert destination["streams"] == [900, 3]
            assert (
                0
                < len(executors[0]._journal_buffer)
                < executors[0]._journal_flush_threshold
            )
            proof = session_factory()
            try:
                assert (
                    proof.query(JournalEntry)
                    .filter_by(batch_id=str(execution.id))
                    .count()
                    == 0
                )
            finally:
                proof.close()
            # Request cancellation at the next stream's REAL cooperative yield,
            # after the executor has queued the first merge, below its threshold.
            budget._deadline = 0
            task = asyncio.current_task()
            asyncio.get_running_loop().call_soon(
                task.cancel, "cancelled at next Pass 2 checkpoint"
            )
        await original_checkpoint(budget)

    monkeypatch.setattr(
        pipeline._PipelineWorkBudget, "checkpoint", cancel_after_buffered_merge
    )

    async def run():
        return await engine.run_pipeline(
            execution_id=execution.id,
            m3u_account_ids=[1],
            triggered_by="api",
        )

    async def run_from_exception_handler():
        # A caller's handled exception is not this run's primary exit. In
        # particular, sys.exception() alone cannot distinguish the two.
        try:
            raise LookupError("unrelated caller failure")
        except LookupError:
            return await run()

    task = asyncio.create_task(run() if cancel_run else run_from_exception_handler())
    expected_error = asyncio.CancelledError if cancel_run else type(flush_error)
    with pytest.raises(expected_error) as caught:
        await asyncio.wait_for(task, timeout=30)
    if cancel_run:
        assert caught.value.args == ("cancelled at next Pass 2 checkpoint",)
        assert checkpoint_reached and task.cancelled()
        client.update_channel.assert_awaited_once_with(42, {"streams": [900, 3]})
    else:
        assert caught.value is flush_error  # Cleanup failures still escape a clean run.
        assert not checkpoint_reached and not task.cancelled()
        assert client.update_channel.await_count == 3
    write_count = client.update_channel.await_count
    await asyncio.sleep(0)
    assert client.update_channel.await_count == write_count  # No continuing actions.
    proof = session_factory()
    try:
        rows = proof.query(JournalEntry).filter_by(batch_id=str(execution.id)).all()
        if journal_fault is None:
            assert len(rows) == 1, "landed merge lost its buffered recovery journal"
            row = rows[0]
            assert row.action_type == "merge_stream" and row.entity_id == 42
            assert row.mutation_source == "auto_creation"
            assert json.loads(row.before_value) == {"stream_ids": [900]}
            assert json.loads(row.after_value) == {"stream_ids": [900, 3]}
            assert executors[0]._journal_buffer == []
        else:
            assert rows == []
            assert len(executors[0]._journal_buffer) == write_count
            assert "Failed to flush" in caplog.text
    finally:
        proof.close()
    assert all(not session.in_transaction() for session in sessions)


@pytest.mark.asyncio
@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "preview"])
@pytest.mark.parametrize("equal_keys", [False, True], ids=["descending", "stable-ties"])
async def test_matching_action_and_ordering_parity(
    async_client,
    pipeline_workload,
    test_session,
    dry_run,
    equal_keys,
):
    engine, client, prepare, _sessions, _settings = pipeline_workload
    execution, rules, streams = prepare(stream_count=48, rule_count=4)
    # Two matching rules retain conflict/winner precedence; STOP_PROCESSING must
    # not drop later streams or the winner's second action.
    for stream in streams:
        stream["name"] = stream["name"].replace("Other", "Keep")
    for rule in rules[2:]:
        rule.set_conditions([{"type": "never"}])
    rules[0].target_group_id = 5
    rules[0].set_actions(
        [
            {"type": "create_channel"},
            {"type": "skip"},
            {"type": "stop_processing"},
        ]
    )
    if equal_keys:
        rules[0].sort_field = "stream_name_regex"
        rules[0].sort_regex = "(Keep)"
    client.get_channel_groups.return_value = [{"id": 5, "name": "Test group"}]
    channel_ids = count(100)
    owner_thread = threading.get_ident()

    async def create_channel(data):
        assert threading.get_ident() == owner_thread
        return {"id": next(channel_ids), **data}

    client.create_channel = AsyncMock(side_effect=create_channel)
    test_session.commit()
    result = await engine.run_pipeline(
        execution_id=execution.id,
        m3u_account_ids=[1],
        triggered_by="api",
        dry_run=dry_run,
    )
    assert result["streams_matched"] == 48
    assert result["streams_skipped"] == 48
    assert result["channels_created"] == 48
    assert result["failed_actions"] == []
    assert result["rule_match_counts"] == {rules[0].id: 48}
    assert len(result["conflicts"]) == 48
    assert all(
        conflict["losing_rule_ids"] == [rules[1].id] for conflict in result["conflicts"]
    )
    logs = [entry for entry in result["execution_log"] if entry.get("stream_id")]
    expected_ids = list(range(1, 49)) if equal_keys else list(range(48, 0, -1))
    assert [entry["stream_id"] for entry in logs] == expected_ids
    assert all(
        [action["type"] for action in entry["actions_executed"]]
        == [
            "create_channel",
            "skip",
            "stop_processing",
        ]
        for entry in logs
    )
    if dry_run:
        client.create_channel.assert_not_called()
    else:
        assert [
            call.args[0]["streams"][0] for call in client.create_channel.await_args_list
        ] == expected_ids
    persisted = await async_client.get(f"/api/auto-creation/executions/{execution.id}")
    assert persisted.json()["channels_created"] == 48


@pytest.mark.asyncio
async def test_work_budget_yields_only_when_slice_expires(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(pipeline, "monotonic", lambda: now[0])
    budget = pipeline._PipelineWorkBudget()
    observed = []
    loop = asyncio.get_running_loop()
    loop.call_soon(observed.append, "first")
    now[0] = 0.009
    await budget.checkpoint()
    assert observed == []
    now[0] = 0.011
    await budget.checkpoint()
    assert observed == ["first"]
    loop.call_soon(observed.append, "second")
    now[0] = 0.020
    await budget.checkpoint()
    assert observed == ["first"]
    now[0] = 0.022
    await budget.checkpoint()
    assert observed == ["first", "second"]
