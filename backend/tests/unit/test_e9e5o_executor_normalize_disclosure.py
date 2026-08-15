"""The auto-create pipeline says when normalization did not run.

Bead `enhancedchannelmanager-e9e5o`. The same swallow the channels router had
also lives in the pipeline executor: a rule with normalization groups selected
wraps the engine call in `try/except`, logs a warning, and creates the channel
with the un-normalized name. The run reports the action as a plain success, so
the operator reading the execution log cannot tell a rule whose normalization
ran and changed nothing from a rule whose normalization never ran at all.

`action_details` is the channel that already exists for exactly this (see the
`_last_name_transform_error` line beside it, bead `…-3gigl`), so the failure
goes there rather than becoming a new response field.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from channel_pipeline_executor import ActionExecutor, ExecutionContext, StreamContext


def _stream_ctx() -> StreamContext:
    return StreamContext(
        stream_id=77,
        stream_name="US: CNN",
        m3u_account_id=1,
        m3u_account_name="Provider",
        group_name="US",
        tvg_id=None,
        resolution_height=None,
        logo_url=None,
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestExecutorNormalizationDisclosure:
    def test_engine_failure_is_named_in_the_execution_log(self):
        """The channel is still created, under the raw name — and the run
        report says the normalization the rule asked for did not happen."""
        client = MagicMock()
        client.create_channel = AsyncMock(return_value={"id": 5, "name": "US: CNN"})
        client.update_channel = AsyncMock(return_value={})

        engine = MagicMock()
        engine.normalize.side_effect = RuntimeError("engine offline")
        engine.extract_core_name.side_effect = lambda n: n
        engine.extract_call_sign.return_value = None

        executor = ActionExecutor(client, existing_channels=[], normalization_engine=engine)

        result = _run(
            executor.execute(
                {"type": "create_channel", "name_template": "{stream_name}", "if_exists": "skip"},
                _stream_ctx(),
                ExecutionContext(),
                normalization_group_ids=[1],
            )
        )

        assert result.success is True
        assert client.create_channel.call_args[0][0]["name"] == "US: CNN"
        assert any(
            "normaliz" in detail.lower() and "engine offline" in detail
            for detail in result.details
        ), result.details

    def test_clean_run_adds_no_failure_line(self):
        """Pin: a normalization that runs cleanly must not look like a failure."""
        client = MagicMock()
        client.create_channel = AsyncMock(return_value={"id": 6, "name": "CNN"})
        client.update_channel = AsyncMock(return_value={})

        def _normalize(name, *args, **kwargs):
            out = MagicMock()
            out.normalized = "CNN"
            out.transformations = []
            return out

        engine = MagicMock()
        engine.normalize.side_effect = _normalize
        engine.extract_core_name.side_effect = lambda n: n
        engine.extract_call_sign.return_value = None

        executor = ActionExecutor(client, existing_channels=[], normalization_engine=engine)

        result = _run(
            executor.execute(
                {"type": "create_channel", "name_template": "{stream_name}", "if_exists": "skip"},
                _stream_ctx(),
                ExecutionContext(),
                normalization_group_ids=[1],
            )
        )

        assert result.success is True
        assert client.create_channel.call_args[0][0]["name"] == "CNN"
        assert not any("did not run" in detail.lower() for detail in result.details), result.details
