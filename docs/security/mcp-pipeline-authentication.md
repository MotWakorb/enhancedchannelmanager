# MCP pipeline preparation and authentication

Tracks #1033 and `enhancedchannelmanager-rtst2.12`.

The sidecar's uniform destructive-tool wrapper resolves the mutation-free target
preview before invoking the underlying tool handler. That resolution can call
`POST /api/channel-pipeline/run/prepare`. It needs the same private backend
credential and request-bound claim as a tool handler. Previously it ran outside
the claim context, causing the reported 401 even when the projection was valid.

The fix scopes target resolution inside `claim_context(..., confirmed=False)`.
This authenticates preparation; it does not validate a confirmation token or
authorize execution. Existing token validation, target/content binding, hard
caps and single-use consumption still gate the mutating handler. The projection
and inbound public MCP client credential remain distinct.

No backend dependency or capability-matrix relaxation is required. On the tested
`dev` base, `RequireAdminIfEnabled` already recognizes the private service
principal. Merely adding an identity-resolver argument to the two routes would
not fix the unsigned sidecar request. The legacy one-shot execution route stays
human-only when authentication is enabled. Authentication-disabled behavior is
not changed by this patch.

## Verification

- `mcp-server/tests/test_pipeline_prepare_auth_claim.py` exercises the registered
  canonical and deprecated tools with the real HTTPX authentication hook. Both
  preview-signing regressions fail without the fix.
- `backend/tests/integration/test_mcp_pipeline_authenticated_execution.py`
  exercises real authentication middleware/dependencies, generated disposable
  credentials, plan binding, HTTP replay and persisted execution state. It also
  runs the production sidecar client/signer in a separate process against a
  private loopback backend, avoiding backend/sidecar module-name collisions.
- `docs/validation/test_mcp_pipeline_tool_auth.py` is an explicit SDK-level
  check outside automatic backend discovery. It calls the registered MCP tool,
  receives a confirmation token, confirms it, and crosses real backend HTTP to
  replay a write against a scripted disposable upstream. It reproduced the 401
  before this fix and completes afterwards.

Run the last check from `backend/`, with a backend interpreter matching backend
pins and `ECM_MCP_VALIDATION_PYTHON` pointing to a separate interpreter matching
`mcp-server/requirements.txt`:

```bash
ECM_MCP_VALIDATION_PYTHON=/path/to/mcp-venv/bin/python \
  ECM_TEST_CONFIG_ROOT=/path/to/disposable-config \
  /path/to/backend-venv/bin/python -m pytest -c pytest.ini -p tests.conftest \
  --no-cov ../docs/validation/test_mcp_pipeline_tool_auth.py
```

These are isolated integration checks, not verification of a reporter's running
container or permission to deploy. Decision computation and upstream transport
are scripted; production credentials and upstream services are not accessed.
For a running installation that still returns 401, confirm the backend and
sidecar image revisions and their shared owner-only credential projection before
changing any authentication policy. Do not replace the private backend key with
the public client key or disable authentication to work around the error.
