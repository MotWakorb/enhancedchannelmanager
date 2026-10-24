# PR #1039 CodeQL blocker review — 2026-10-24

Exact first analyzed merge: `b1a95d65a8ede7bfb216449ac84e244caf6eda76`,
run `40089538099`, Python analysis `2018796782`. Raw findings remain retained;
no query, severity or workflow gate is disabled.

## Alert 2045 — production validation repair required

`py/partial-ssrf`, scanner **Critical / 9.1**, includes two unvalidated string
inputs in the stats stop routes. Although representative flows name
framework-validated integer IDs, the full SARIF's related sources 60/61 are
`stop_channel(channel_id: str)` and `stop_client(channel_id: str)`.

An offline reproduction using the real route AST/client methods, ASGITransport
and MockTransport shows encoded query/fragment input changes the requested URL
component while retaining the synthetic API credential. The configured origin
remains fixed in that reproduction; harmful upstream effects were not tested.
This is a real input-validation defect, not grounds for dismissing the grouped
alert or claiming the separate stream-SSRF sanitizer covers this request path.

Repair is implemented under `enhancedchannelmanager-74gis`: validate a safe ASCII
single-segment identifier at both route and client boundaries, preserve numeric,
UUID and legacy-token inputs, and reject invalid IDs before any upstream call.
The new regressions first failed (84 failures, 18 positive controls passed), then
the complete focused stats/client/contract subset passed 200 tests. Require fresh
CodeQL before disposition of any
remaining modeled integer-only paths. No scanner-severity downgrade is applied.

### Fresh residual record after the repair

Python analysis `2018890474` on merge
`7509715dad8c3021942df3003c930e7edc5298ef` still grouped 64 numbered sources into
alert 2045. Independent review checked every fresh SARIF source: 60 are actual
FastAPI integer path arguments, two are Pydantic-parsed integer batch models,
and two are the now-explicitly validated string stop identifiers. The four
representative traces all use framework-parsed group/logo integers. Python type
annotations alone were not accepted as enforcement.

`backend/tests/unit/test_dispatcharr_codeql_input_boundaries.py` exercises the
real application/routers: malformed integer path and batch IDs return specific
422 `int_parsing` errors before client lookup or request dispatch; the positive
`0042` path dispatches integer `42`. Its 37 cases and 102 existing stop-boundary
cases pass together. These new framework regressions passed first because that
framework behavior was already secure; they do not replace the original
test-first stop-ID production repair.

The remaining **2045 record only** qualifies as a false positive under ADR-005:
the data-flow model does not recognize these tested framework/model/fullmatch
boundaries. Its disposition comment references the tracked boundary tests.
The scanner's Critical/9.1 classification is retained in the evidence; no query,
global severity rule, unknown flow or future validator change is waived. Fresh
CI and candidate attestation remain mandatory on the final head.

## Alert 2046 — specific test-only disposition

`py/insecure-protocol`, scanner **High / 7.5**, points to the synthetic TLS server
in `tests/native_image/runtime_checks.py`, not the production TLS listener.
`server()` binds literal `127.0.0.1:0`; `tls_context()` generates short-lived
fixture credentials. Only the verified-HTTPS positive check and untrusted-CA
negative check use it, and the harness closes its listener and thread.

`scripts/native_image_gate.py` runs this read-only mounted harness under
`--network none`, without published ports and with the isolated absolute Python
entrypoint. The image copies `backend/` into the application, not
`tests/native_image/`. Boundary regression:
`backend/tests/unit/test_native_image_gate.py::test_fixture_success_requires_runtime_and_both_bootstrap_lifecycles`;
failure control: `test_fixture_failures_never_emit_success`.

Independent security review confirmed the exact source flow and GitHub's `test`
classification. Alert **2046 only** was dismissed with reason **used in tests**
and a comment naming the fixture and boundary regression, under
[ADR-005's Dismiss-With-Comment Policy](../adr/ADR-005-code-security-gating-strategy.md#dismiss-with-comment-policy).
This is not a production TLS waiver, a dismissal of alert 2045, or a native-CVE
scan exception. Reassess if this listener is ever shipped or exposed differently.

## Evidence

`/tmp/opencode/ecm-native-remediation-20261023/pr1039-codeql-review/` retains raw
alert/instance/SARIF responses, exact merge-source comparisons, isolated dataflows,
offline reproduction, the independent report and SHA-256 manifest.
`parent-alert-2046-before.json` and `parent-alert-2046-dismissal.json` retain the
actual specific disposition. No live provider/exploit request was performed.
