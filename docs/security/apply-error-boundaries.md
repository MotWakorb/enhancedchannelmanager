# Operator-safe apply failure messages

Beads: `enhancedchannelmanager-jnw1v`, `enhancedchannelmanager-oaoel`.

This builds on the diagnostic scrubber described in
[credential-error-boundaries.md](credential-error-boundaries.md):
`log_utils.redact_log_diagnostic` for a stringified exception and
`log_utils.redact_exception` for a full exception chain. A handler that used
`logger.exception` now passes `redact_exception(e)` as an ordinary argument to
`logger.error`, because raw `exc_info` would append the unscrubbed traceback.

## Normalization apply

The normalization failure sources in single and bulk channel creation return
the stable message "Normalization failed; the original channel name was used."
The create still reports its actual landed outcome, the raw name used and the
explicit normalization failure indicator. A rejected create is not listed as
successfully created with an unnormalized name. The MCP `create_channel` and
`bulk_commit_channels` tools print that message where they used to print the
engine's exception text. Single create and single-channel PATCH failure logs
(both the mapped upstream rejection and the 500 path) are scrubbed.

## M3U router

Profile-apply summary entries (`ecm_profile_apply[].error`) carry fixed
lower-case reasons that end in the recovery step, because the UI renders them
as "Saved, but <reason>". The setup read, the per-group reconcile and the outer
reconcile each have their own reason, with the same status and group identity
as before. The propagation warning starts lower case for the same reason. Account-list failures no longer interpolate exception text into the
propagation warning or the clear's 503 detail. Existing partial sibling-clear
journals, primary-preservation rules, per-group continuation and service-produced
degraded/partial categories remain intact. The post-refresh note also uses fixed
prose.

Other M3U changes:

- Every logged exception in `routers/m3u.py` is scrubbed: refresh polling,
  change capture, watermark, placeholder rebind, digest, account CRUD (mapped
  and unmapped), profiles, enforced-global, group settings and the guided
  auto-sync toggle.
- Stream metadata: the fetched M3U URL can carry Xtream Codes credentials,
  and httpx error text quotes the URL. The 502 detail now names only the
  provider's HTTP status, or says the provider could not be reached. An
  upstream 4xx on that route's account read maps through
  `upstream_http_exception` (a missing account answers 404), other account
  read failures answer the generic 500 instead of a misleading "Failed to
  fetch M3U file" 502, and the "no server URL" 400 is no longer swallowed into
  a 500. The log test uses unregistered Xtream Codes credentials, so it proves
  the URL rules rather than the registered-value sweep.
- Profile get/update/delete and upload answered `detail=str(e)` on a 500. The
  app-wide 500 handler already replaced that text on the wire; the source now
  states "Internal server error" directly.
- Account delete: `failed_groups[].error` (response and journal) uses the
  shared `channel_group_delete.group_delete_failure_detail`, which keeps the
  known Dispatcharr validation reasons as fixed prose and otherwise gives fixed
  guidance, instead of the upstream text.
- A failed refresh trigger's notification message and metadata no longer
  carry the exception text.
- The guided auto-sync toggle no longer raises `None` (a TypeError) when an
  upstream status error is not a 4xx; it falls through to the logged 500.

## Scope limits

Mapped upstream 4xx responses (`upstream_http_exception`) still pass the
upstream validation detail through, by design. Only their log line is scrubbed
here. Other raw-exception log sites in `routers/channels.py` outside
normalization, single create and single PATCH are unchanged.

## Verification

`backend/tests/security/test_apply_error_boundaries.py` drives each route or
background helper with a registered synthetic credential in the exception and
its cause, then checks the response, notification or summary and the real
console, ring and persistent handlers. It reuses the fixtures of
`test_credential_error_boundaries.py`. `backend/tests/routers/test_apply_error_safety.py`
keeps the original response-boundary cases. The tests were run red against the
merged tree before the fix; the cases that tree already passed were proved by
reintroducing the raw `e` in the log call and the raw text in the response.

CodeQL alerts 1877 and 1812 close only after analysis of the merged default
branch. This is not a browser rendering, a live Dispatcharr test or a
deployment claim.
