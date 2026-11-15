# Direct probe capacity

## Best-effort admission (88yso, PO decision 2026-11-15)

ECM probes finite providers when verified capacity is available. This supersedes
the temporary explicit-unlimited-only restriction introduced for GH1032 in
`v0.18.2-0065`. It is **best effort, not a shared viewer reservation**: a viewer
starting after ECM's check can compete with a probe for the last provider slot.
The PO explicitly accepted this race. No reserved viewer slot, viewer preemption,
Dispatcharr proxy rerouting, private Redis access, or upstream fork is introduced.

All consumers use the same admission: manual single/bulk, probe-all, scheduled
probes, pipeline probes, failed-stream/event reprobes, and standalone black-screen
scans. Actual media is bounded across the singleton prober by **Max concurrent
probes**; disabling parallel probing limits it to one media operation. Capacity
waiters do not consume these media slots, so a full provider does not prevent
another provider from making progress, even in sequential mode.

## Profiles and limits

Before media, ECM validates the current stream ID, original URL, account, profiles,
and supplied route hint. A stale URL or reassigned stream cannot borrow another
route's capacity. Every used profile must be active and have a verified rewrite.
No-match, invalid, oversized, or timed-out patterns never authorize fallback to
the default credentials. JavaScript-style `$1` and `$<name>` references are
supported, matching Dispatcharr's profile transforms. All resulting media still
passes the existing SSRF/network/redirect protections.

Dispatcharr's account **Max Streams** is copied to the **default profile**. Its
allocator enforces per-profile allowances; the account field is not a separate
aggregate cap across independent logins. ECM therefore checks each eligible
profile's live connections **plus ECM-owned probes** against that profile's
limit. If the account/default declarations disagree, ECM conservatively honors
the narrower nonzero limit for the default login, not for an independently
rewritten secondary login. Integer zero means unlimited; missing/null/bool/string
or negative limits are not treated as unlimited.

**Fill First**, **Round Robin**, and **Least Loaded** select among eligible
profiles at shared admission, including single and bulk probes. A secondary
profile can be used when the default is full. Identical-URL profile aliases are
not independent headroom: ECM reserves their profiles together and conservatively
checks account occupancy against their limits. HDHomeRun routes retain the
separate two-tuner ceiling. Probe-all retains provider ramp-up and overload holds.

Accounts with a non-null or missing `server_group` still defer: recorded API
responses do not prove shared credential-pool usage. Accountless streams also
have unverified capacity. Do not remove a real grouping or mark a finite login
unlimited to bypass this protection. Undeclared credential sharing across accounts
or external clients cannot be inferred; configure independent profile logins
truthfully. ECM does not claim to enforce a hidden provider-wide cap.

## Waiting and status outages

**Settings → Maintenance → Stream Probing → Capacity wait timeout (seconds)**
defaults to **300 seconds** (five minutes), with a range of 0–3600. Zero makes one
bounded admission attempt without waiting for capacity. This is distinct from
the media probe timeout. Capacity waiting rechecks stream identity and fresh
account/profile metadata; cancellation and service shutdown cancel queued work.
Pausing a batch prevents queued probes from acquiring media and pauses their
capacity-wait budget. Once the budget expires, the stream is skipped/deferred,
not measured as failed. Another explicit or scheduled run is needed to retry it.

Each admission attempts a fresh `/proxy/ts/status` read. One channel consumes one
provider connection even when several clients watch it; `client_count` and stored
`current_viewers` are not provider occupancy. Counts must be attributable to known
profiles. On a failed/unverifiable live read, ECM can use its last **successfully
validated** snapshot for at most **30 seconds**, only with the same profile/account
mapping. It still adds current local probes. A failed read never refreshes the
snapshot's age. No snapshot, changed attribution, or older counts cause waiting,
then deferral. This fallback increases the accepted race risk; it is not a claim
that cached counts are current. Metadata/route validation has no stale fallback.
Malformed or mismatched routes and unsupported shared-capacity metadata defer
immediately: waiting for viewers to leave cannot prove an invalid route.

## Results and lifecycle

Per-stream deferral returns `probe_status: "skipped"`; `error_message` and
`deferred_reason` describe the specific capacity, status, backoff, or metadata
reason without credentials. Select the skipped count to see those reasons.
Previous persisted health is retained. A batch with skipped admissions ends as
`deferred`, not successful; its summary directs operators to per-stream reasons.
Deferred runs do not auto-reorder or publish a wider successful reprobe scope.
Cancellation takes precedence over aborting failure, then deferral/completion.

Reservations remain owned through metadata, retries, optional resdet, bitrate and
black-screen measurements, and through shielded media/subprocess cancellation
cleanup. Stop/rebuild retires the old instance and joins its owners before a new
instance can admit media. API batch cancellation targets its own children, not
unrelated single probes. Queued batch start acknowledgements and existing
history/notification ownership remain unchanged. Local reservations cover one
ECM process, not multiple replicas or external provider clients.

## Evidence and validation

This implementation uses source analysis and recorded fixtures, **not live
deployment/provider observations**. Recorded account responses are
`backend/tests/fixtures/bd_igqcy/dispatcharr_v0272_m3u_account.json` and
`backend/tests/fixtures/bd_g8tyd/dispatcharr_v0282_m3u_account_server_group.json`.
Their recorded account/profile limits are zero. Finite limits, multiple logins,
capacity faults and status rows in tests are explicitly synthetic variations.

Pinned upstream source (`cd3f6c509d74ad7618d870e5673476928d528de9`):

- [Account/default synchronization](https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/m3u/models.py)
- [Profile/credential allocator](https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/m3u/connection_pool.py)
- [Live route and JS-style transforms](https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/proxy/live_proxy/url_utils.py)

`test_probe_best_effort.py` covers finite admission, spare logins, global limits,
downshifting, waiting/deadlines, status-cache boundaries and cancellation.
`test_probe_failclosed_policy.py` retains unknown/full-provider negative controls
and verifies spare-login admission through every consumer. Route identity/rewrite,
SSRF, native disposable-process reaping, settings and batch lifecycle suites remain
regression checks. `tests/integration/test_probe_capacity.py` uses disposable HTTP
services, actual client/SSRF/bitrate paths and SQLite; generated metadata substitutes
native ffprobe decoding. Its late-viewer control explicitly demonstrates the
accepted race rather than claiming zero limited-provider media.

Guaranteed viewer priority remains a different coordination problem; see
[the coordination review](design/direct-probe-coordination-limits.md).
