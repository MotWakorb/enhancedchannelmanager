# Profile and bulk exception boundaries

Fixes: `enhancedchannelmanager-5e3r3`, `enhancedchannelmanager-ej4um`.

Profile-reconcile and sibling-propagation warnings now use the same pattern and
process-lifetime registered-value redaction policy as persistent JSON logs.
`log_utils.redact_log_diagnostic` reuses that value policy and adds the existing
URL-aware obfuscation policy for scoped diagnostics (including unregistered
`?token=` values). Persistence keeps its existing separate policy. Registered
retired/encoded credential spellings remain covered. Unknown unlabelled non-URL
secrets are not guaranteed to be recognized.

The diagnostic sweep of registered values skips spellings shorter than four
characters, the same minimum the debug-bundle literal sweep uses; such a value
is still caught by the structural URL rules but not in free text. The pattern
layer masks JSON and repr'd-dict credential keys, `token` and API-key header
forms, masking the whole value: a quoted value to its matching unescaped quote
and a list to its closing bracket, each bounded so the scan stays linear. A
registered credential equal to a channel number or id can still appear as that
number in the unscrubbed numbering guidance, and a non-numeric server number is
rendered there as "an unreadable channel number" (the `concurrentNumberChanges`
payload keeps the value as read). The URL layer matches any scheme (so `rtmp://`, `ftp://` and `redis://`
userinfo is destroyed), bracketed IPv6 authorities, extensionless and timeshift
Xtream Codes paths. Journal-write failures log the scrubbed exception chain and
the scrubbed row, and a Dispatcharr login failure logs only its exception type.

URL query vocabulary and known-credential path rewriting live in the leaf
`credential_sentinel` module, shared by backup and diagnostic obfuscation.
The logging path does not import the backup router to obtain these rules.

`redact_exception` formats and scrubs the exception chain before it is passed as
a normal logging argument. Bulk-job catches must not use
`logger.exception`/raw `exc_info` alongside that argument: doing so
would append the original unsanitized traceback again. The helper fails closed
if formatting or scrubbing fails. Existing file and debug-bundle protections
remain in place; raw ring capture alone is not proof of bundle disclosure.
The low-level Dispatcharr transport boundary retains only method and exception
type, without exception text or traceback, preserving its existing log contract.

Foreign bulk group-creation, operation, outer-run and supervisor errors now use
fixed public messages. Explicitly typed local validation/recovery failures retain
their scrubbed actionable guidance; arbitrary upstream `ValueError` text is not
trusted. Numbering-baseline refusals are local operator guidance and keep
their exact text: a missing `expectedNumber`/`expectedNumbers`, an unreadable
lineup, and a stale baseline (with its `concurrentNumberChanges` payload). The
terminal envelope's per-operation `error` is what the MCP `bulk_commit_channels`
tool prints verbatim. Each operator-safe error class declares whether its message is scrubbed.
Classes whose every message is ECM text plus ids and channel numbers (the
stale, unverifiable and missing-baseline numbering refusals, and a missing
stream id) pass through unscrubbed, so a short registered credential cannot
rewrite the numbers the operator must act on. Classes that interpolate names or
upstream data (a malformed create response, an unresolved group or channel
label, a reorder refusal built from upstream stream lists) stay scrubbed. A
malformed create id is reported only by its type. A `numberingRecovery` entry keeps its exact remaining
step but carries a fixed reason instead of the upstream error text, and the
range read-back warning is scrubbed like the other bulk diagnostics. Entity
IDs/context and the existing accounting/recovery fields remain authoritative. A terminal `completed` job can still carry failed
operations; `failed` represents a supervisor failure. Neither state promises
that no upstream writes landed. Check reported results/current state before
retrying; this patch does not change cancellation, compensation or mutation order.

No authentication or polling-ownership policy changes are included. Existing
normalization/apply exception-source work in PR #1047 remains separate. This is
not a blanket credential-safety claim for every error in these routers or the
entire application. No schema/dependency migration is required.

## Verification

`backend/tests/security/test_credential_error_boundaries.py` crosses ASGI save,
real profile service, bulk POST/background/terminal GET, real console/ring/file
handlers, and a private loopback HTTPX/httpcore/h11 malformed-header path.
Synthetic sentinels only; no production credentials or destination mutations.
The original 20 HTTP/log regression cases were confirmed failing before the
fix. Tests assert sanitized diagnostics are actually emitted and retain
degraded/partial outcomes, failed IDs and successful-neighbor accounting.
`backend/tests/unit/test_credential_redaction_dependencies.py` additionally
checks cold-process URL scrubbing without router imports and shared-rule identity.

This is API/service/transport integration verification, not a browser screenshot,
live production test, release or proof of deployed remediation. Deployment
requires the usual reviewed integration/build; historical logs are not rewritten
by this change. If a real credential was already logged, an operator should
rotate it and handle retained logs according to their access/retention policy.
