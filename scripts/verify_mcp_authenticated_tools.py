"""Initialize an authenticated MCP session and require a non-empty tool list.

Used by ``scripts/test_mcp_container_hardening.sh`` to prove that a fully
locked-down sidecar container (non-root, read-only rootfs, dropped
capabilities, credential-projection-only mount) still serves its tools, and
that a rotated key takes effect without a restart.

The credential goes in the ``Authorization: Bearer`` header — query-string
credentials are rejected by the sidecar (enhancedchannelmanager-04c0u.3).
Exits non-zero on any failure so the caller can assert both success and the
expected failure after rotation.
"""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> None:
    url = os.environ["MCP_TEST_URL"]
    headers = {"Authorization": f"Bearer {os.environ['MCP_TEST_KEY']}"}
    async with streamablehttp_client(url, headers=headers) as (reader, writer, _):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools = await session.list_tools()
            if not tools.tools:
                raise RuntimeError("authenticated MCP session returned no tools")
            print(f"authenticated MCP session listed {len(tools.tools)} tools")


try:
    asyncio.run(main())
except Exception as error:  # noqa: BLE001 - surfaced verbatim to the caller
    print(f"MCP session verification failed: {error!r}", file=sys.stderr)
    raise SystemExit(1)
