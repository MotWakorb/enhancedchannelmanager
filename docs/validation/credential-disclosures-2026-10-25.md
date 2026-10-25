# Credential-disclosure validation — 2026-10-25

## Result

**Both unsanitized exception paths are reproduced. Credential exposure is
conditional, not an automatic consequence of an ordinary Dispatcharr failure.**

- `enhancedchannelmanager-5e3r3`: actual profile-reconcile and sibling-save
  warnings disclose a secret-bearing exception to JSON console and ring-buffer
  handlers. The tested save responses and persistent JSON log remain scrubbed.
- `enhancedchannelmanager-ej4um`: actual bulk POST/background job/terminal GET
  handling returns secret-bearing operation, outer-run and supervisor errors in
  ordinary JSON responses. Accurate landed-write/failure counts do not remove
  that disclosure.
- Normal Dispatcharr API keys travel in `X-API-Key`, not the URL. A real-client
  control with an ordinary upstream rejection does **not** expose that header
  key. The earlier triage's P1 escalation did not sufficiently account for this
  prerequisite or the access controls. Both records are corrected to **P2**,
  remain open, and still need remediation. This is a corrected assessment, not a
  waiver or acceptance of a demonstrated High vulnerability.

This supersedes the P1 recommendations for these two records in the earlier
`backlog-triage-2026-10-25.md` snapshot. Other findings' priorities are unchanged.

No product behavior was changed. No deployed ECM, real Dispatcharr, provider,
production credential, real customer data, or external network target was used.

## Source and environment

| Target | Exact revision | State inspected |
|---|---|---|
| Current remote `dev` | `0bc126006d96c03637cb9e1b707ec04aca2d5804` | Clean application source in a dedicated validation worktree |
| Open PR #1047 | `a0c85e2afcb5203ff1d45e18ca4b507c9e70246b` | Clean candidate worktree, unchanged by validation |

The application source at the first revision is unchanged by this report and
probe. PR #1047 repairs different response sources (`jnw1v`/`oaoel`); it does not
claim to repair these service logs or generic bulk-error sources. The same
characterizations reproduced on both revisions.

Interpreter: existing `/tmp/opencode/ecm-p2-backup/.venv/bin/python`, Python
3.12.12, pytest 9.1.1, pytest-asyncio 1.4.0, FastAPI 0.136.1, HTTPX 0.28.1,
SQLAlchemy 2.0.52 and Pydantic 2.13.4. Installed versions matched the assessed
`backend/requirements.txt` exact pins. No dependencies were installed.

Each process owned its own temporary auth-disabled test configuration, SQLite
fixture database and pytest directory. Middleware denial controls explicitly
enabled authentication. The two concrete malformed-header cases used only an
ephemeral `127.0.0.1` listener and the real HTTPX/httpcore/h11 transport. Other
upstream calls used mocks/HTTPX MockTransport. Journaling was mocked; no operator
journal or destination records were changed.

## Evidence and falsifiable checks

The retained executable probe is
[`credential_disclosure_probe_20261025.py`](credential_disclosure_probe_20261025.py).
It is outside automatic backend test discovery. Its default mode is explicitly
**characterization of unsafe behavior**, not a passing security regression.

| Run | Tests | Result | What it proves |
|---|---:|---|---|
| Clean dev characterization | 26 | 26 passed | Reported sinks and controls behave as characterized |
| PR1047 characterization | 26 | 26 passed | These residual sources remain in that candidate |
| Clean dev with `ECM_DISCLOSURE_EXPECT_SAFE=1` | 26 | **21 failed, 5 passed**, zero setup errors | Every disclosure assertion actually rejects the current unsafe behavior; the five clean/auth controls still pass |
| Adjacent baseline suites | 144 | 144 passed | Existing reconcile wiring, service outcomes, bulk accounting, and the named persistent-file/debug-bundle scrub control remain intact |

The 21 expected RED results are specifically `credential sentinel reached ...`
assertions, not unrelated failures. No remediation has been applied, so there is
no RED-to-fixed-GREEN claim. No full backend gate, browser validation, released
image verification or live-deployment proof is claimed.

### 5e3r3 — actual save → service → logging

Ten cases exercise five stages with two secret-bearing error types:

- post-lock settings revalidation;
- profile-universe fetch;
- selected-profile enable;
- unselected-profile disable;
- sibling clear/propagation after one sibling succeeds and another fails.

`RuntimeError` text and constructed HTTPX status-error URLs deliberately contain
the synthetic sentinel. Those constructed URLs demonstrate the conditional sink;
they are **not** evidence that ECM normally places its API key in request URLs.

The real ASGI PATCH, reconcile service, record factory, JSON console formatter,
ring handler and persistent-file handler run unchanged. In every case:

- the console and ring contain the synthetic secret;
- the HTTP save response does not contain it or the synthetic internal host;
- the persistent log contains real records and a redaction marker, but no secret;
- degraded/partial status, applied profile counts, failed IDs and successful
  sibling-only journal accounting retain their expected meanings.

A further concrete producer test configures a synthetic API key with a trailing
newline directly on a disposable `DispatcharrClient`. The real HTTP transport
raises `LocalProtocolError: Illegal header value ...` containing that value.
The actual reconcile warning then reaches console/ring output. This demonstrates
a real library-generated secret-bearing exception, **under a deliberately
malformed configuration**. It does not prove that a normally valid key leaks,
or that the operator settings UI accepts/preserves that malformed input.

Affected sources at assessed dev:

- `backend/services/profile_reconcile.py:436-445,568-574,727-732,763-768`;
- sibling warning: `backend/routers/m3u.py:1245-1249`;
- newline-only record protection: `backend/log_utils.py:29-55`;
- raw ring formatting: `backend/log_utils.py:62-81`;
- JSON message/traceback formatting: `backend/observability.py:174-197`.

### ej4um — actual POST → background execution → terminal HTTP JSON

Eight cases cross the real HTTP and background-job seam, using the two
secret-bearing error types above:

- operation rejected before any create: `applied=0`, `failed=1`;
- one landed create beside one rejection: `applied=1`, `failed=1`, partial true;
- outer-run finalization fault after a create: `applied=1`, `failed=0`, success
  false, setup error retained;
- supervisor fault before worker setup: terminal `failed` with raw `job.error`.

The first three can be returned under terminal **`completed`** despite their
failure information. The supervisor returns terminal **`failed`**. Neither
ordinary JSON envelope passes through the HTTP-500 exception sanitizer.

Two additional producer tests cross the real `DispatcharrClient` boundary:

1. A local MockTransport supplies an upstream response body already containing a
   synthetic secret. Real `create_channel` embeds that body in its exception,
   and the actual terminal GET returns it. This is an upstream-body injection,
   not evidence that ordinary Dispatcharr bodies echo authentication headers.
2. The malformed synthetic key described above produces a real HTTPX protocol
   exception; real channel creation, bulk accounting and polling forward it.

A successful bulk-create control contains no error or invented secret. Another
real-client control verifies that an ordinary upstream 400 does not reveal the
header API key, although channel creation does include the ordinary error body.

Affected sources:

- operation error storage: `backend/routers/channels.py:4180-4186`;
- outer-run error storage: `backend/routers/channels.py:4390-4417`;
- supervisor storage: `backend/routers/channels.py:2479-2483`;
- polling serialization: `backend/routers/channels.py:2503-2528`;
- upstream body interpolation: `backend/dispatcharr_client.py:750-762`.

## Access boundaries and existing mitigations

Three dynamic negative controls verify that anonymous requests to the M3U save,
bulk POST and bulk GET are refused with **401** when auth is enabled and setup
complete; mocked upstream clients are not invoked. The following finer-grained
permissions were reviewed in source, not all exercised dynamically:

| Surface | Auth enabled/setup complete | Additional prerequisite |
|---|---|---|
| M3U group-settings save | Active authenticated user; no route admin dependency | Trigger an error containing secret material; console access needed to read the logged value |
| Bulk submit | Admin, or the explicitly allowed MCP backend principal | Trigger a secret-bearing error |
| Bulk polling | Active authenticated user knowing the job ID; no ownership/admin check in the handler | Possession of the UUID4 job ID; completed jobs are removed after retrieval |
| Console/ring logs | No direct raw-log download route identified in this review | Host/container/log-collector access or another log consumer |
| Debug bundles | Human-admin creation and owner-restricted retrieval | Additional bundle scrubbing applies |

When auth is disabled or setup incomplete, the operational API middleware does
not require a human login. Network exposure then matters materially. The public
MCP client credential is not a backend credential; separate capability/claim
controls apply to the private backend principal.

Source: `backend/main.py:606-720`,
`backend/auth/dependencies.py:299-319,499-505,533-553`,
`backend/auth/mcp_capabilities.py`, and the operational routes cited above.

Persistent JSON logs have pattern/registered-value scrubbing and private-file
controls (`backend/log_utils.py:193-272`). A separately executed existing test,
`test_n9ix6_debug_bundle_logs.py::test_actual_file_is_scrubbed_before_persistence_and_bundle_stays_clean`,
verifies that its known credentials are absent from both persisted output and
the archive even when the raw ring contains them. **Raw ring capture is not proof
that the generated debug bundle exposes those credentials.** That control does
not establish safety for every unknown/unregistered secret format.

The generic HTTPException handler scrubs HTTP 500 failures
(`backend/main.py:1161-1173`); it does not scrub successful bulk-job envelopes.
Failed-job retention is bounded by pruning on new submissions, not immediate
destruction. UUID entropy is a mitigation, not an ownership check.

## Risk assessment and follow-up

Scores below are CVSS-inspired contextual judgments, **not formal CVSS vectors**
or findings of an actual compromise. Category: error/log information disclosure,
OWASP A09:2021/CWE-532 (logs), CWE-209 (HTTP error detail).

| Bead | Conditional technical severity | Effective risk / assumptions | Scheduling |
|---|---|---|---|
| `5e3r3` | Medium, **4.0/10** | Low–Medium; requires a secret-bearing exception plus access to unsanitized logs. Private persistent files and scrubbed bundles reduce propagation; shared collectors increase exposure. | **P2** |
| `ej4um` | Medium, **4.3/10** | Low–Medium; requires secret-bearing error content and submission permission or a known job ID. Ordinary header authentication is not itself serialized. Lower-trust users/auth-disabled exposure increase risk. | **P2** |

Impact is disclosure of whatever credential/internal detail occurs in an error;
the usefulness and privileges of that credential determine downstream impact.
There is no evidence here of secret theft, remote code execution, unauthenticated
exploitation on an auth-enabled deployment, or leakage of every valid API key.
Likelihood on the actual installation remains unmeasured because it was not
inspected. A concrete secret-bearing error format and reachable lower-trust
reader on a deployed system would justify reassessing priority upward.

Recommended remediation, **not executed**:

1. `5e3r3`: use the established redaction/classification policy for relevant
   service/sibling messages **and exception tracebacks**; preserve actionable
   context, profile outcome accounting and retry semantics. Scope is low to
   medium implementation complexity, plus cross-handler regression work.
2. `ej4um`: use stable operator-safe terminal errors and scrubbed server
   diagnostics across operation, outer-run and supervisor catches. Preserve
   landed writes, partial outcomes and rejected-versus-unknown semantics.
   Separately disposition polling authorization/ownership rather than silently
   expanding this validation into an access-control redesign. Medium complexity.
3. Retain no-secret assertions as actual regression tests when repaired. The
   current default characterization assertions intentionally demonstrate leakage
   and must not be described as safe behavior or copied as permanent safety tests.

Both beads remain **open for remediation**, with the validated conditions and
limits recorded. PR1047, production credentials, other worktrees and pre-existing
uncommitted application changes were not modified.

## Reproduction and receipts

Run from the target worktree's `backend/` using an existing matching interpreter:

```bash
PYTHONDONTWRITEBYTECODE=1 \
ECM_TEST_CONFIG_ROOT=/tmp/opencode/disclosure-replay-config \
TMPDIR=/tmp/opencode \
"$PYTHON" -m pytest -c pytest.ini -p tests.conftest \
  -o junit_family=legacy --no-cov -p no:cacheprovider \
  --basetemp=/tmp/opencode/disclosure-replay-tmp \
  "$PROBE" --junitxml=/tmp/opencode/disclosure-replay.xml
```

`PYTHON` is the interpreter named above; `PROBE` is the absolute path to the
retained probe. Use a distinct config/temp/output name per concurrent run.
To demand safe behavior instead of characterizing current leakage, add
`ECM_DISCLOSURE_EXPECT_SAFE=1`; expected on the assessed dev revision: exit 1,
21 disclosure-assertion failures and five passing controls.

Final raw receipts (synthetic data only) are under `/tmp/opencode/`:

- `disclosure-validation-dev-final2.xml` / `.log`;
- `disclosure-validation-pr1047-final2.xml` / `.log`;
- `disclosure-validation-safety-final2.xml` / `.log`;
- `disclosure-validation-controls.xml` / `.log`.

The committed JSON receipt records exact source/probe/artifact hashes, counts and
per-case properties without publishing local filesystem paths from tracebacks.
Initial shorter exploratory runs are superseded by these final receipts. The
19-case xunit2 warning was a receipt-format issue; final runs explicitly use
legacy JUnit and have no such warnings. Optional Ruff was unavailable in the
selected environment; AST syntax, diff checks and the explicit executions are
the validation for the probe, not a claim of a full lint/typecheck/build gate.
