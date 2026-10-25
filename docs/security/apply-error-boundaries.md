# Operator-safe apply failure messages

Beads: `enhancedchannelmanager-jnw1v`, `enhancedchannelmanager-oaoel`.

The normalization failure sources in single and bulk channel creation now return
the stable message “Normalization failed; the original channel name was used.”
The create still reports its actual landed outcome, raw name used and explicit
normalization failure indicator. A rejected create is not listed as successfully
created with an unnormalized name. No global response/status handler changed.

M3U group-settings apply catches return stable retry instructions with the same
status/group identity. Account-list propagation failures no longer interpolate
exception text into successful-save warnings or no-write HTTP 503 detail. Existing
partial sibling-clear journals, primary-preservation rules, per-group continuation
and safe service-produced degraded/partial outcome categories remain intact.
The post-refresh note also uses stable prose instead of the caught exception.

Changed router diagnostic catches use the existing `redact_secrets` policy on
stringified exceptions, followed by the existing log-injection sanitation. Their
internal host/diagnostic detail remains server-side, not in JSON responses.
Tests in `test_apply_error_safety.py` cross in-process HTTP serialization and bulk
terminal polling with distinct internal-host/API-key sentinels; existing disclosure,
reconcile hooks/service and accounting suites preserve outcome behavior.

## Scope limits and follow-up

This is not universal endpoint/log safety. Independent review confirmed two
**pre-existing, different sources**, tracked separately:

- `enhancedchannelmanager-5e3r3`: unchanged reconcile-service/sibling catches can
  still expose credentials to console/ring logs; persistent-log scrubbing is not
  equivalent to sanitizing those handlers.
- `enhancedchannelmanager-ej4um`: generic upstream bulk-operation/supervisor
  failures still return raw exception text, distinct from the normalization
  exception now removed. Do not rewrite truthful partial accounting to hide it.

CodeQL alerts 1877/1812 were open on default-branch `main` at the assessment.
PR-scoped code/tests do not establish closure there. Verify alert state after
approved merge and analysis of the intended branch; do not dismiss findings to
manufacture closure. No live Dispatcharr mutation or browser rendering is claimed.
