# Direct probe capacity (GH1032)

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

## Bounded policy

ECM keeps its existing **direct-to-provider measurement route**. This change
does not enable the playback probe proposed in PR #1035, route probes through
Dispatcharr's proxy, add a reserved-slot setting, or silently reserve an extra
viewer slot.

`StreamProber.probe_stream` performs shared admission for single, bulk, pipeline
and probe-all calls. On the singleton prober, a lock serializes fresh account
and live-status reads with check/reserve. A probe is admitted only when both:

```
account live channels + local account probes < account max_streams
profile live channels + local profile probes < profile max_streams
```

A configured zero means unlimited for that limit, independently of the other
limit. Account counts aggregate the current profiles of live channels, including
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
fail-open error fallback are not used for admission. A null `m3u_account` is an
explicit accountless stream and needs no account reservation; an absent field is
not the same thing. Accountless streams still require verified stream ID and
exact original URL identity; accountless reassignment cannot authorize an older
provider URL.

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

## What this cannot guarantee

**This is local admission, not an upstream atomic reservation.** After the status
read (or during an already admitted probe), Dispatcharr can start another channel.
It does not know that a direct ECM probe occupies the provider slot. Therefore
the viewer-arrival reproduction in GH1032 can still exceed the provider limit
even though the initial snapshot had headroom. The integration test
`test_real_http_one_free_slot_and_later_viewer_arrival` deliberately demonstrates
this boundary: a viewer arrives while direct media is in progress; only the next
probe is refused. Media substeps/retries reuse the permit, not new upstream
reservations. Polling faster or clamping subsequent admissions cannot make the
in-flight direct connection viewer-safe.

Reservations coordinate one singleton prober in one ECM process, not multiple
ECM processes/replicas, other external clients, or different accounts sharing
provider credentials. Counts reflect what the live-status API reports; a valid
but incomplete upstream snapshot cannot be detected locally. In the inspected
upstream implementation, some internal Redis failures can even be represented
as an empty channel list.

Stronger guarantees need an approved product/routing decision and verified
upstream enforcement: e.g. a genuinely shared atomic reservation, or a proxy
probe route whose connection-limit behavior is proven for raw-stream probes.
Leaving one slot free is only headroom, not a guarantee against arbitrary viewer
arrivals. Neither that policy change nor new routing/auth settings is introduced
here. Do not describe this bounded fix as complete protection of new viewers or
as a full solution to both GH1032 reproductions.

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
  lifecycle, and permit cleanup with substituted media work.
- `backend/tests/integration/test_probe_capacity.py`: disposable loopback HTTP
  control API/provider, actual Dispatcharr client, real SQLite, ASGI single
  probe/stat serialization, bulk and both pipeline consumers, and actual
  SSRF-checked HTTP bitrate reads. Native ffprobe decoding is substituted with
  the existing generated-local-media JSON fixture; no ffprobe executable was
  available on this test host. This is not a live Dispatcharr/provider E2E test.
- Existing SSRF, downgrade, relay cleanup/retry, resdet, User-Agent, media and
  probe envelope suites remain regression checks; their synthetic accountless
  media cases now identify `m3u_account: null` explicitly instead of bypassing
  admission with incomplete metadata.

The parent lane owns the full backend gate and final integration/merge decision.
