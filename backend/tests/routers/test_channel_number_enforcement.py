"""Every channel-number entry point rejects out-of-contract values.

Bead ``enhancedchannelmanager-ic884.1``. The canonical domain lives in
``backend/channel_number.py`` and its own tests are in
``backend/tests/unit/test_channel_number.py``; this module proves that each
write path CONSUMES it, and that the operator-facing sentence comes back.

``1.05`` is the fixture value throughout because it is the boundary that
matters: it sits exactly between two in-contract tenths, so a rounding
implementation would accept it and quietly pick one. The contract rejects it.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from channel_number import CHANNEL_NUMBER_RULE_MESSAGE

OUT_OF_CONTRACT = 1.05


def _messages(response) -> str:
    """Flatten a FastAPI error body so the canonical sentence can be asserted.

    A 400 raised by a handler carries ``detail`` as a string; a 422 raised by
    request-model validation carries a list of ``{loc, msg, ...}`` entries.
    """
    detail = response.json().get("detail")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return " ".join(str(entry.get("msg", entry)) for entry in detail)
    return str(detail)


def _assert_rejected(response) -> None:
    assert response.status_code in (400, 422), response.text
    assert CHANNEL_NUMBER_RULE_MESSAGE in _messages(response)


class TestCreateChannel:
    """POST /api/channels."""

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_number(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels", json={"name": "ESPN", "channel_number": OUT_OF_CONTRACT}
            )
        _assert_rejected(response)
        mock_client.create_channel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [-1, -0.1])
    async def test_rejects_negative_number(self, async_client, value):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels", json={"name": "ESPN", "channel_number": value}
            )
        _assert_rejected(response)
        mock_client.create_channel.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [0, 1, 1.1, 999.9])
    async def test_accepts_in_contract_number(self, async_client, value):
        mock_client = AsyncMock()
        mock_client.create_channel.return_value = {"id": 1, "name": "ESPN"}
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels", json={"name": "ESPN", "channel_number": value}
            )
        assert response.status_code == 200, response.text
        mock_client.create_channel.assert_called_once()


class TestUpdateChannel:
    """PATCH /api/channels/{id} takes an untyped field bag."""

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_number(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.patch(
                "/api/channels/7", json={"channel_number": OUT_OF_CONTRACT}
            )
        _assert_rejected(response)
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_allows_clearing_the_number(self, async_client):
        """An explicit null means unassigned, which is a valid state."""
        mock_client = AsyncMock()
        mock_client.get_channel.return_value = {"id": 7, "name": "ESPN", "channel_number": 5}
        mock_client.update_channel.return_value = {"id": 7, "name": "ESPN"}
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.patch("/api/channels/7", json={"channel_number": None})
        assert response.status_code == 200, response.text


class TestMergeChannels:
    """POST /api/channels/merge."""

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_target_number(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/merge",
                json={
                    "source_channel_ids": [1, 2],
                    "target_name": "ESPN",
                    "target_channel_number": OUT_OF_CONTRACT,
                },
            )
        _assert_rejected(response)
        mock_client.create_channel.assert_not_called()


class TestAssignNumbers:
    """POST /api/channels/assign-numbers."""

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_starting_number(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/assign-numbers",
                json={"channel_ids": [1, 2], "starting_number": OUT_OF_CONTRACT},
            )
        _assert_rejected(response)
        mock_client.assign_channel_numbers.assert_not_called()


class TestBulkCommit:
    """POST /api/channels/bulk-commit covers the edit-mode staged operations."""

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_number_on_create_op(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/bulk-commit",
                json={
                    "operations": [
                        {
                            "type": "createChannel",
                            "tempId": -1,
                            "name": "ESPN",
                            "channelNumber": OUT_OF_CONTRACT,
                        }
                    ]
                },
            )
        _assert_rejected(response)
        mock_client.create_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_number_on_update_op(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/bulk-commit",
                json={
                    "operations": [
                        {
                            "type": "updateChannel",
                            "channelId": 7,
                            "data": {"channel_number": OUT_OF_CONTRACT},
                        }
                    ]
                },
            )
        _assert_rejected(response)
        mock_client.update_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejects_out_of_contract_starting_number_on_assign_op(self, async_client):
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/bulk-commit",
                json={
                    "operations": [
                        {
                            "type": "bulkAssignChannelNumbers",
                            "channelIds": [1, 2],
                            "startingNumber": OUT_OF_CONTRACT,
                        }
                    ]
                },
            )
        _assert_rejected(response)
        mock_client.assign_channel_numbers.assert_not_called()

    @pytest.mark.asyncio
    async def test_accepts_an_in_contract_update_op(self, async_client):
        """The guard rejects the contract breach, not the operation shape."""
        mock_client = AsyncMock()
        with patch("routers.channels.get_client", return_value=mock_client):
            response = await async_client.post(
                "/api/channels/bulk-commit",
                json={
                    "operations": [
                        {"type": "updateChannel", "channelId": 7, "data": {"channel_number": 1.1}}
                    ]
                },
            )
        assert response.status_code not in (400, 422), response.text


class TestCsvImport:
    """POST /api/channels/preview-csv and /import-csv share `validate_channel_row`."""

    @pytest.mark.asyncio
    async def test_preview_reports_the_rule_for_an_out_of_contract_row(self, async_client):
        csv_content = "channel_number,name\n1.05,ESPN\n"
        response = await async_client.post("/api/channels/preview-csv", json={"content": csv_content})
        assert response.status_code == 200, response.text
        body = response.json()
        errors = " ".join(str(e) for e in body.get("errors", []))
        assert CHANNEL_NUMBER_RULE_MESSAGE in errors
        assert body.get("rows") == []

    @pytest.mark.asyncio
    async def test_preview_accepts_an_in_contract_row(self, async_client):
        csv_content = "channel_number,name\n1.1,ESPN\n"
        response = await async_client.post("/api/channels/preview-csv", json={"content": csv_content})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("errors") == []
        assert len(body.get("rows", [])) == 1


class TestPipelineRuleSpec:
    """A stored rule cannot carry an out-of-contract literal number."""

    def test_set_channel_number_rejects_an_out_of_contract_literal(self):
        from channel_pipeline_schema import Action, ActionType

        action = Action(type=ActionType.SET_CHANNEL_NUMBER, params={"value": OUT_OF_CONTRACT})
        errors = action.validate()
        assert any(CHANNEL_NUMBER_RULE_MESSAGE in e for e in errors), errors

    @pytest.mark.parametrize(
        "value",
        ["auto", "100-99999", "{auto}", "{provider_channel_number}", 101, "101"],
    )
    def test_set_channel_number_accepts_the_existing_spec_vocabulary(self, value):
        """Narrowing the spec vocabulary is a different question, so it is not narrowed."""
        from channel_pipeline_schema import Action, ActionType

        action = Action(type=ActionType.SET_CHANNEL_NUMBER, params={"value": value})
        assert action.validate() == []

    @pytest.mark.parametrize("value", ["1.1-1.9", "800.0-899", "1e3", ".5", "+7", "7."])
    def test_rejects_a_literal_the_executor_cannot_honour(self, value):
        """The executor assigns an UNRELATED number for every one of these.

        ``ActionExecutor._get_next_channel_number`` reads a literal as plain
        digits carrying at most one decimal place, and a range as two whole
        numbers. Anything else, including a range naming a tenth, falls through
        to automatic numbering. Accepting these at the schema would bless a
        silent wrong result, so they are rejected here instead. Bead
        ``enhancedchannelmanager-ay3iq``.
        """
        from channel_pipeline_schema import (
            Action,
            ActionType,
            PIPELINE_CHANNEL_NUMBER_RULE_MESSAGE,
        )

        for action in (
            Action(type=ActionType.SET_CHANNEL_NUMBER, params={"value": value}),
            Action(
                type=ActionType.CREATE_CHANNEL,
                params={"name_template": "{stream_name}", "channel_number": value},
            ),
        ):
            errors = action.validate()
            assert any(
                PIPELINE_CHANNEL_NUMBER_RULE_MESSAGE in e for e in errors
            ), (action.type, errors)

    @pytest.mark.parametrize("value", ["1.1-1.9", "800.0-899", "1e3", ".5", "+7", "7."])
    def test_the_rejected_literals_are_the_ones_the_executor_renumbers(self, value):
        """The rejection list is read off the executor, not guessed.

        Every literal the schema rejects is one the executor answers with a
        DIFFERENT number, which is the whole reason for rejecting it. This test
        calls the executor directly, so if the executor ever learns to honour
        one of these the mismatch shows up here.
        """
        from channel_pipeline_executor import ActionExecutor

        # A lineup already holding 1 and 2, so automatic numbering lands on 3.
        # Without occupied numbers the fallback happens to return 1 and a spec
        # of "1.0" would look honoured by coincidence.
        executor = ActionExecutor(
            MagicMock(),
            existing_channels=[
                {"id": 1, "name": "CH1", "channel_number": 1},
                {"id": 2, "name": "CH2", "channel_number": 2},
            ],
        )
        assigned = executor._get_next_channel_number(value)
        assert assigned == 3, (value, assigned)

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (101, 101),
            ("101", 101),
            (800, 800),
            # Honoured as written since bead enhancedchannelmanager-ay3iq: a
            # rule may name a tenth, and the number it names is the number the
            # channel gets.
            (1.1, 1.1),
            ("1.1", 1.1),
            (800.5, 800.5),
            # A whole number written as a float is still a whole number.
            (1.0, 1),
            ("1.0", 1),
            (800.0, 800),
        ],
    )
    def test_the_accepted_literals_are_the_ones_the_executor_honours(self, value, expected):
        """The mirror of the rejection list: what passes the schema is honoured."""
        from channel_pipeline_executor import ActionExecutor
        from channel_pipeline_schema import validate_channel_number_spec

        executor = ActionExecutor(MagicMock())
        assert validate_channel_number_spec(value, "probe") == []
        assert executor._get_next_channel_number(value) == expected

    @pytest.mark.parametrize("value", [1.1, "1.1", 1.0, "1.0", 800.0, "800.5"])
    def test_a_fractional_literal_is_storable_on_a_rule(self, value):
        """These were rejected provisionally while the executor was whole-number only."""
        from channel_pipeline_schema import Action, ActionType

        for action in (
            Action(type=ActionType.SET_CHANNEL_NUMBER, params={"value": value}),
            Action(
                type=ActionType.CREATE_CHANNEL,
                params={"name_template": "{stream_name}", "channel_number": value},
            ),
        ):
            assert action.validate() == [], (action.type, value)

    def test_create_channel_rejects_an_out_of_contract_literal(self):
        from channel_pipeline_schema import Action, ActionType

        action = Action(
            type=ActionType.CREATE_CHANNEL,
            params={"name_template": "{stream_name}", "channel_number": OUT_OF_CONTRACT},
        )
        errors = action.validate()
        assert any(CHANNEL_NUMBER_RULE_MESSAGE in e for e in errors), errors

    @pytest.mark.parametrize("value", ["auto", "10000-99999", "800-99999", 800, None])
    def test_create_channel_accepts_the_specs_real_rules_use(self, value):
        """The range strings are taken from the PO's own stored rules."""
        from channel_pipeline_schema import Action, ActionType

        action = Action(
            type=ActionType.CREATE_CHANNEL,
            params={"name_template": "{stream_name}", "channel_number": value},
        )
        assert action.validate() == []

    def test_create_channel_accepts_provider_channel_number(self):
        from channel_pipeline_schema import Action, ActionType

        action = Action(
            type=ActionType.CREATE_CHANNEL,
            params={
                "name_template": "{stream_name}",
                "channel_number": "{provider_channel_number}",
            },
        )

        assert action.validate() == []
