# Direct probe capacity (GH1032)

## PO decision: temporary fail-closed restriction (2026-11-08)

The PO explicitly selected **fail closed**, superseding the earlier partial-only
disposition. Direct probes are deferred on capacity-limited or unverified routes,
even when a status snapshot shows free slots. They never open a provider media
connection, mutate measured health, auto-reorder, or widen successful probe scope.
Audit/history/notification records describing deferral are intentional. Other
explicit channel-pipeline actions and their ordering remain authorized/unchanged.

Admission requires a fresh, route-validated account with integer `max_streams: 0`
on the account **and every profile**, plus explicit `server_group: null`. Zero is
unlimited, not unknown. Any finite sibling/default/selected profile blocks the
account; choosing an unlimited alternate does not bypass a declared restriction.
Missing/null/bool/string limits, failed/degraded reads, accountless capacity and
grouped credentials without a verified unlimited contract fail closed. No runtime
override, proxy route, preemption policy or upstream fork is introduced.

Recorded survey: all 32 fixture JSON files contain only two capacity-bearing
accounts/two profiles, with four integer-zero limits. The 0.27.2 account 11/profile
12 has `server_group: null`; 0.28.2 account/profile 4 has integer FK `server_group:
20`. No recorded group response proves shared-credential capacity. Finite and bad
metadata cases are explicitly synthetic variations, never claimed live captures.

Allowed explicit-unlimited probes retain direct ffprobe/resdet/bitrate/black-screen
measurements, SSRF controls, fresh status validation and owned cleanup. Standalone
black-screen scans use the same gate/ownership in black-screen-only mode, not the
old direct-decoder bypass. Scheduled and pipeline probes share this admission.

### Deferral wire contract

Per-stream admission refusal has `probe_status: "skipped"`, an operator-safe
`error_message`, and fixed `deferred_reason`:

> Probe deferred: provider capacity is limited or unverified; shared viewer coordination is unavailable

A batch with deferred/skipped admissions terminates `status: "deferred"`, not
completed. Its result/progress/history expose `deferred_reason`; progress/history
use null outside deferral. Existing success/failed/skipped counters retain their
meaning. Accepted cancellation still wins, then aborting failure, then deferral,
then completion. Deferred runs do not auto-reorder or publish successful scope.
Scheduled/standalone-scan task reports use `PROBE_DEFERRED` and the same reason;
pipeline probe-action logs show the reason without suppressing other rule actions.

This resolves the limited-provider viewer-arrival safety case **by restriction**:
there is no admitted probe connection for a later viewer to collide with. It does
not implement leases or restore limited-provider probing. Future functionality is
tracked separately by the parent as `enhancedchannelmanager-88yso`.

## Shared route invariant

Before **any media**, the supplied URL must identify the same validated stream,
account and profile route(s) for which admission reserves capacity. A stale URL,
cached stream row, rewrite fallback or malformed default classification must
not transfer a permit to another route. Explicit accountless streams still need
URL/stream identity proof; a missing URL keeps the existing failed-result path
and opens no media. Admission must skip an unprovable route, not replace its URL.

`_validated_probe_stream` reads/validates the current stream by its requested ID
even when a caller supplied a cached row. It validates scalar types and checks
cached identity/account/original-URL consistency before retaining a selected
profile ID as a hint, not authoritative classification. `_probe_route_profiles`
then checks the original URL association, strict boolean profile classifications
and exactly one default before deriving a selected route or accepting an
unselected/accountless route. Capacity reservation uses only its validated
profiles. This adds a bounded control-plane stream read per probe, not a new
routing/viewer policy. The cancellation/shield/join/reap implementation is
unchanged by this route repair.

The tests `test_shared_route_identity_matrix` and
`test_shared_profile_classification_matrix` cover fresh/cached/mismatched stream
identities, selected/unselected/accountless URL equality, and malformed,
duplicate or missing classifications on original/default and alternate profiles.
`test_pipeline_stale_url_cannot_borrow_reassigned_stream_capacity` crosses both
actual pipeline callers with provider/accountless reassignments; its HTTP
integration counterpart checks zero provider reads and retained persisted health.
`test_missing_url_preserves_failed_result_without_route_reads` pins the no-URL
exception to admission. These augment the rewrite and cancellation regressions.

## Retained route and inner local-accounting defenses

ECM keeps its existing **direct-to-provider measurement route**. This change
does not enable the playback probe proposed in PR #1035, route probes through
Dispatcharr's proxy, add a reserved-slot setting, or silently reserve an extra
viewer slot.

`StreamProber.probe_stream` performs shared admission for single, bulk, pipeline
and probe-all calls. The new restriction is checked on the same fresh account
snapshot inside the admission lock, before live-status/check/reserve or media.
The retained inner accounting implementation also requires both:

```
account live channels + local account probes < account max_streams
profile live channels + local profile probes < profile max_streams
```

A configured zero means unlimited for that individual limit; the new outer policy
requires every account/profile limit to be zero. Inner account counts aggregate the current profiles of live channels, including
inactive profiles still referenced by status. One channel counts once even when
several clients share it; `client_count` and stored `current_viewers` are not the
provider connection count. Known connections on another account do not consume
this account's allowance.

Direct single/bulk/pipeline URLs retain their default M3U profile. Admission does
not select a spare profile or rewrite a URL. Probe-all retains its existing
profile distribution, URL rewriting, ramp-up and global concurrency scheduling;
its selected profile is checked again at shared admission. Unknown/inactive or
ambiguous target profiles are refused instead of falling back to another route.

Admission also checks the **actual media URL** against a successful match and
rewrite using the fresh selected profile. Missing/nonmatching patterns, regex
timeouts/oversize/compile errors, invalid replacement templates and a mismatch
between the computed and supplied media URLs refuse admission. The legacy URL
helper may still return its original input on errors, but that fallback is not
evidence authorizing a spare profile. No alternate URL is silently selected.
`test_scheduler_rewrite_fallback_cannot_charge_spare_profile` crosses the real
scheduler → rewrite → admission seam in sequential and parallel modes.

A legitimate matching identity rewrite is distinct from an error. Because its
media still uses the original/default URL, admission checks and reserves both
the selected and default profiles. For this ambiguous alias it conservatively
uses the entire account's live/local count against each profile limit, rather
than claiming independent headroom on the spare profile. This can refuse some
otherwise usable capacity; it does not infer new credentials or routing policy.
`test_scheduler_successful_rewrite_reserves_actual_media_route` checks identity
and changed-URL cases, including reservation keys; the saturated identity case
is covered by the fallback regression above.

Missing/malformed stream/account/profile metadata, unknown channel attribution,
and account/status fetch errors or ten-second read timeouts fail closed. Even
an unlimited account must have verifiable metadata/status; a missing limit is
not interpreted as unlimited. The scheduler's five-second status cache and its
fail-open error fallback are not used for admission. Null `m3u_account` remains
distinct from an absent field for route validation, but neither proves unlimited
capacity under the selected policy, so neither admits media.

The caller receives `probe_status: "skipped"` and a fixed, credential-free
capacity reason. Batch results/history count it as skipped, not failed; pipeline
logs include the skipped count. Previous persisted stream health is retained.
There is no hidden retry queue: retry occurs only on another explicit probe or a
later scheduled task invocation. Saturated probe-all scheduling terminates with
a skip rather than waiting indefinitely on a zero ramp limit.

The permit lasts through metadata, optional resdet, retries, bitrate and optional
black-screen sampling. Release follows media context/subprocess cleanup on
success, failure, timeout or cancellation. Reservations are separate from
per-run scheduler state and are not reset when a bulk/probe-all run starts.
Probe-all cancellation also awaits its child probes' cleanup.

Cancellation requests are idempotent per task. Each admitted probe owns a
separate media task; its owner shields and joins cancellation cleanup before
releasing reservations. Scheduler/bulk/shutdown joins likewise defer repeated
parent cancellation until children finish, then preserve `CancelledError`.
`test_two_scheduler_children_single_cancel_keeps_permits_through_delayed_cleanup`
pins the single-API-cancel/multiple-child case. The parameterized
`test_repeated_parent_cancellation_joins_real_process_reap` runs the actual
ffprobe lifecycle methods with disposable Python subprocesses and proves
single/probe-all/bulk owners retain permits through delayed wait completion,
then leave no child process alive. This is OS process-lifecycle evidence, not
native ffprobe decoding evidence.

### Cancellation and service replacement

Stopping a prober permanently retires that instance before awaiting anything.
Every direct probe owner is registered before metadata lookup or admission-lock
waits, and batch owners remain registered through preparation, media cleanup and
finalization. `stop()` cancels and joins these owners using the same idempotent,
shielded cleanup path. Settings rebuild cannot publish a replacement with empty
reservations while old media is still cleaning up. A stale reference receives an
explicit skipped/cancelled result without control-plane or media work; `start()`,
force-reset and later batch calls cannot revive a retired instance.

API batch cancellation targets only that batch's children, including sequential
probe-all children, not unrelated single/pipeline probes. A cancelled probe-all
joins cleanup before recording cancelled progress/history, skips automatic
reordering not yet started, and retains the previous successful reprobe scope.
Cancellation during an already-started reorder cannot undo completed upstream
writes; its cancelled history retains the returned reorder results rather than
claiming rollback or publishing a successful scope.

`backend/tests/unit/test_stream_probe_lifecycle.py` invokes production probe,
pipeline and settings rebuild methods. It covers delayed cleanup, admission
waiters, all six consumer lanes, repeated rebuild cancellation, batch preparation,
retired references, and cancellation-tail/scope behavior in both probe-all modes.
Control APIs, media and background tracker work are substituted; configuration
and any database state use the private test harness, never provider services.
These local lifecycle guarantees do not close the external coordination gap below.

### Honest background start acknowledgements

Both `/api/stream-stats/probe/all` and `/probe/bulk` synchronously claim one batch
owner through `StreamProber.schedule_probe` before returning `status: "started"`.
That status acknowledges an accepted background job, not a completed measurement.
The worker adopts only its own claim; same-tick/concurrent API requests and direct
batch calls cannot steal a queued owner's slot. Both endpoints return the existing
`already_running` response/message while another run is active or still draining
after force-reset. A start request never implicitly resets or cancels that run.

Retired references return a cancelled/stopped response, never `started`. Queued
reset/cancel/stop and task-creation failure cannot leave a permanent busy claim or
revive cancelled work before its first instruction. An identity-checked completion
callback releases unstarted workers without clearing a successor's ownership.

Bulk and probe-all both recompute their terminal status after an awaited reorder.
Accepted cancellation stays cancelled in result, progress and history; returned
reorder results are retained because already-landed upstream writes are not rolled
back. Router regressions in `backend/tests/routers/test_probe_start_lifecycle.py`
exercise real endpoints/probers, including HTTP busy/reset/stopped responses and
controlled pre-worker scheduling edges. They do not probe production providers.

### Terminal precedence and retained diagnostics

Both batch paths and history use one terminal-precedence rule: **accepted
cancellation wins over an aborting failure, then normal completion**. A diagnostic
does not turn cancellation into failure, and an aborting exception with an empty
message still fails an uncancelled run. Existing error fields and diagnostic logs
are retained; this does not add a new raw-error payload or logging surface.

An aborting probe-all reorder/late-preparation error therefore remains recorded
with cancelled result/progress/history if cancellation was accepted during the
operation. Without cancellation, an aborting error remains failed. Normal success
and returned completed reorder writes remain unchanged; no rollback is claimed.
Bulk's optional reorder is still **best-effort**: its swallowed lookup/update
errors remain logged and do not independently fail an uncancelled probe run.
Actual batch-aborting preparation errors still fail bulk unless cancelled.

`backend/tests/unit/test_stream_probe_terminal_status.py` covers cancel/no-cancel
and success/error matrices through the real reorder and preparation methods,
SQLite-backed smart sort, returned reorder writes, empty exception diagnostics,
and history's diagnostic/status precedence. Only external service/media boundaries
are substituted. Start ownership, cancellation joining, subprocess reaping and
route/SSRF methods are unchanged by this terminal-status repair.

## Restriction boundary and future functionality

No upstream atomic reservation is claimed. The former late-viewer race is avoided
for limited/unverified providers because no probe is admitted, not because local
reservations became shared with Dispatcharr. The disposable integration test
`test_real_http_free_slot_then_viewer_arrival_never_opens_limited_media` observes
headroom, queues concurrent probe work, admits a later viewer, and verifies zero
provider media reads and retained SQLite health. Its explicit-unlimited positive
control still performs direct, SSRF-checked HTTP measurement.

Reservations coordinate one singleton prober in one ECM process, not multiple
ECM processes/replicas, other external clients, or different accounts sharing
provider credentials. Counts reflect what the live-status API reports; a valid
but incomplete upstream snapshot cannot be detected locally. In the inspected
upstream implementation, some internal Redis failures can even be represented
as an empty channel list.

Restoring probing on finite/shared providers requires a verified coordination
contract (future 88yso), not a headroom heuristic or hidden unsafe override. The
current P1 safety restriction does not depend on an upstream merge. As with any
configured limit, admission relies on truthful upstream declarations; it cannot
infer hidden provider caps, undeclared credential aliases or changes made after
an unlimited route was admitted.

The bounded [upstream coordination review](design/direct-probe-coordination-limits.md)
explains why the existing proxy route is not a demonstrated drop-in viewer-priority
reservation API, and names the routing/measurement decisions still required.

## Contract evidence and test layers

Recorded account responses already in this repository:

- `backend/tests/fixtures/bd_igqcy/dispatcharr_v0272_m3u_account.json`: integer
  account/profile IDs 11/12, account/profile `max_streams: 0`, active default
  profile, stored `current_viewers: 0`.
- `backend/tests/fixtures/bd_g8tyd/dispatcharr_v0282_m3u_account_server_group.json`:
  IDs 4/4 and the same zero-limit/default-profile shape (including an inactive
  account with an active default profile).

`dispatcharr_stream_provider_channel_number.json` supplies the recorded stream
shape (`id` integer, `url` string, `m3u_account` integer). Its identity/admission
test joins it to the recorded account shape using an explicitly synthetic ID
association. Null-account and malformed values in the matrix are labeled test
variations, not newly recorded upstream responses.

No new live production capture or credentials were used. The tests parse the
first recorded response; finite limits, extra profiles, failures and status
rows are explicitly synthetic test variations, not fabricated live fixtures.

Status/profile semantics are source-derived from Dispatcharr commit
`cd3f6c509d74ad7618d870e5673476928d528de9` (also cited by the existing
[API contract](dispatcharr_api.md#stream-user-agent)):

- [`apps/proxy/live_proxy/channel_status.py`](https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/proxy/live_proxy/channel_status.py):
  `get_basic_channel_info` reads the current `M3U_PROFILE` metadata into integer
  `m3u_profile_id`; `build_live_channel_stats_data` emits `{channels, count}`.
- [`apps/m3u/models.py`](https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/m3u/models.py):
  account and profile limits are non-negative integers with zero unlimited;
  account updates synchronize the default profile's limit. `current_viewers` is
  a stored profile field, not the fresh status snapshot.
- ECM's existing `DispatcharrClient.get_channel_stats` reads `/proxy/ts/status`;
  its existing profile-aware scheduler also uses `m3u_profile_id`.

Verification is split rather than presented as a production guarantee:

- `backend/tests/unit/test_stream_probe_capacity.py`: entrypoint wiring,
  snapshot/account/profile logic, local concurrency, pause and cancellation
  lifecycle, and permit cleanup with substituted media work. The finite
  `capacity_probe` fixture explicitly mocks only the outer restriction for inner
  accounting/route regressions; it is not policy acceptance evidence or a product
  bypass. Separate `unlimited_probe` controls run the actual policy for lifecycle,
  cancellation and real disposable-process reaping. No prior cases were dropped.
- `backend/tests/unit/test_probe_failclosed_policy.py`: real-policy free-slot,
  unknown/grouping and all-consumer negative/positive controls with zero media
  and no health/reorder/scope mutation when deferred.
- `backend/tests/integration/test_probe_capacity.py`: disposable loopback HTTP
  control API/provider, actual Dispatcharr client, real SQLite, ASGI single
  probe/stat serialization, bulk and both pipeline consumers, and actual
  SSRF-checked HTTP bitrate reads. Native ffprobe decoding is substituted with
  the existing generated-local-media JSON fixture; no ffprobe executable was
  available on this test host. This is not a live Dispatcharr/provider E2E test.
- Existing SSRF, downgrade, relay cleanup/retry, resdet, User-Agent, media and
  probe envelope suites remain regression checks. Their media fixtures now use
  the recorded explicit-unlimited account rather than accountless metadata; the
  real restriction is not mocked in these suites.

The parent lane owns the full backend gate and final integration/merge decision.
