import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import DispatcharrSettings
from dispatcharr_client import DispatcharrClient


FIXTURE = Path(__file__).parents[1] / "fixtures" / "dispatcharr_epg_grid_recorded.json"


@pytest.mark.asyncio
async def test_get_epg_grid_parses_recorded_dispatcharr_response():
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))
    response = MagicMock()
    response.json.return_value = recorded["response"]
    response.raise_for_status.return_value = None
    client = DispatcharrClient(DispatcharrSettings(url="http://dispatcharr.test"))
    client._request = AsyncMock(return_value=response)

    try:
        programs = await client.get_epg_grid()
    finally:
        await client.close()

    assert recorded["capture"]["source"].startswith("GET /api/epg/grid/")
    assert programs == recorded["response"]["data"]
    assert programs[0]["tvg_id"] == "fixture.channel"
    assert programs[0]["start_time"].endswith("Z")
