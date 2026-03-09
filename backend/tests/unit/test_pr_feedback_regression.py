"""
Regression tests addressing Round 3 maintainer feedback.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from auto_creation_engine import AutoCreationEngine
from auto_creation_evaluator import StreamContext, ConditionEvaluator
from auto_creation_executor import ActionExecutor, _parse_epg_date
from auto_creation_schema import Condition, ConditionType

@pytest.mark.asyncio
async def test_epg_state_reset_between_rules():
    """Verify epg_match state is reset between rules to prevent bleeding (Must-Fix #5/Item 10B)."""
    client = MagicMock()
    engine = AutoCreationEngine(client)
    
    prog = {"title": "EPG Match", "source": 1}
    stream = StreamContext(stream_id=1, stream_name="Stream", epg_programs=[prog])
    
    # Rule 1 matches by EPG
    rule1 = MagicMock()
    rule1.id = 1
    rule1.m3u_account_id = None
    rule1.get_conditions.return_value = [{"type": "epg_title_contains", "value": "EPG"}]
    rule1.get_actions.return_value = [{"type": "skip"}]
    rule1.stop_on_first_match = False
    
    # Rule 2 does NOT match by EPG (should not see Rule 1's match)
    # We use a custom evaluator to verify the state reset
    rule2 = MagicMock()
    rule2.id = 2
    rule2.m3u_account_id = None
    rule2.get_conditions.return_value = [{"type": "stream_name_contains", "value": "Stream"}]
    rule2.get_actions.return_value = [{"type": "skip"}]
    rule2.stop_on_first_match = False
    
    execution = MagicMock()
    
    with patch("auto_creation_engine.get_session"), \
         patch("auto_creation_engine.get_settings"):
        # We need to catch the moment Rule 2 is evaluated
        original_evaluate = ConditionEvaluator.evaluate
        
        def mock_evaluate(self, condition, context):
            if getattr(condition, 'type', None) == 'stream_name_contains':
                # Rule 2 evaluation: check if Rule 1's EPG state was reset
                assert context.epg_match is None
                assert context.matched_by_epg is False
            return original_evaluate(self, condition, context)
            
        with patch.object(ConditionEvaluator, 'evaluate', autospec=True, side_effect=mock_evaluate):
            results = await engine._process_streams([stream], [rule1, rule2], execution, dry_run=True)
        
        log_entry = results["execution_log"][0]
        # Rule 1 should be matched
        assert log_entry["rules_evaluated"][0]["matched"] is True
        # Rule 2 should be matched
        assert log_entry["rules_evaluated"][1]["matched"] is True

def test_parse_epg_date_iso():
    """Item 10C: Test ISO format."""
    assert _parse_epg_date("2026-02-24T21:30:00Z") == "21:30"

def test_parse_epg_date_iso_with_offset():
    """Item 10C: Test ISO format with offset."""
    assert _parse_epg_date("2026-02-24T21:30:00+00:00") == "21:30"

def test_parse_epg_date_xmltv():
    """Item 10C: Test XMLTV format."""
    assert _parse_epg_date("20260224213000 +0000") == "21:30"

def test_parse_epg_date_empty():
    """Item 10C: Test empty/None cases."""
    assert _parse_epg_date("") == ""
    assert _parse_epg_date(None) == ""

def test_filter_channels_by_group():
    """Must-Fix #7 regression."""
    client = MagicMock()
    executor = ActionExecutor(client, existing_channels=[], existing_groups=[])
    
    channels = [
        {"id": 1, "name": "CH1", "channel_group": 10},
        {"id": 2, "name": "CH2", "channel_group": 20}
    ]
    
    # Filter by group
    assert executor._filter_channels_by_group(channels, 10)["id"] == 1
    assert executor._filter_channels_by_group(channels, 20)["id"] == 2
    
    # No match
    assert executor._filter_channels_by_group(channels, 30) is None
    
    # No group filter (returns first)
    assert executor._filter_channels_by_group(channels, None)["id"] == 1

def test_channel_lookups_with_groups():
    """Must-Fix #7 scoped lookups regression."""
    client = MagicMock()
    channels = [
        {"id": 1, "name": "Sports", "channel_group": 1},
        {"id": 2, "name": "Sports", "channel_group": 2},
        {"id": 3, "name": "News", "channel_group": 1}
    ]
    executor = ActionExecutor(client, existing_channels=channels, existing_groups=[])
    
    # Find by name scoped to group
    assert executor._find_channel_by_name("Sports", group_id=1)["id"] == 1
    assert executor._find_channel_by_name("Sports", group_id=2)["id"] == 2
    assert executor._find_channel_by_name("Sports", group_id=3) is None
