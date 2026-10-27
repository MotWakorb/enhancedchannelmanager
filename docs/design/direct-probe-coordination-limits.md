# Direct probes and later Dispatcharr viewers

Remaining acceptance boundary for #1032 / `enhancedchannelmanager-0m06f.6.2`.

ECM can serialize its own direct-probe reservations against freshly observed
Dispatcharr occupancy. It cannot atomically reserve a provider connection on
behalf of a direct probe against a viewer admitted later by another process.
The local admission repair is therefore partial, not a claim that the late-viewer
scenario is solved. No routing or reserved-slot setting is introduced implicitly.

## Inspected upstream contracts

This is bounded source analysis, not live deployment proof. The inspected
Dispatcharr revisions are v0.30.0
`cd3f6c509d74ad7618d870e5673476928d528de9` and official main
`8d7a02efb05c3253e490c058604accbf9e17a4e3`.

- The [profile allocator][allocator] uses Redis INCR-first admission and rolls
  back an excess finite-profile reservation. Its optional shared-credential
  counter is reserved separately, not in one account/profile/provider transaction.
  Zero is unlimited. The account's limit is copied to its default profile;
  this is not a separate atomic account-total reservation for external probes.
- The pinned [stream-hash preview path][stream] reuses an existing
  `stream_profile:<id>` assignment without taking another slot. Preview and live
  assignment isolation differs on newer main. Do not assume every preview is an
  independently counted provider connection on all deployed versions.
- Current [channel admission][priority] can identify and log a preemption
  candidate, but the inspected caller does not execute a probe-priority handoff.
  No supported external viewer-priority lease/reservation contract was found in
  the inspected route inventories and relevant actions. An internal allocator
  helper is not an external reservation API.

Consequently, routing through Dispatcharr is not a verified drop-in answer.
Even with correctly counted proxy admission, a probe occupying the last slot can
make a later viewer wait, use another profile/source, or be refused. That is not
viewer priority. Stopping a proxy client also does not by itself prove the
provider connection has closed before capacity becomes reusable.

## Product boundary

Two possible directions require an explicit disposition:

1. Keep direct measurements with shared local admission, while retaining the
   late-viewer case as an unresolved upstream coordination requirement. Do not
   close #1032 or describe the entire P1 as fixed on this evidence.
2. Review a proxy/routing policy against a pinned supported Dispatcharr version,
   explicitly deciding whether a probe may deny a later viewer and whether
   measuring Dispatcharr output instead of the raw provider route is acceptable.
   Verify preview/channel isolation and provider-close-before-slot-reuse ordering
   against an actual disposable upstream before claiming the stronger behavior.

Proxying changes the measured network leg, buffering/output processing, profile
selection and potentially source IP/VPN/reachability. It also needs a reviewed
trusted origin, safe stream-hash construction, auth/network permissions and
redirect/SSRF controls; broad private-network exemptions are not a safe shortcut.
Following a provider redirect can recreate the unaccounted direct connection.

Neither direction is implicitly authorized by the bounded local admission fix.
Preserving both raw direct measurement and guaranteed protection for later viewers
requires coordination beyond the demonstrated local contracts.

[allocator]: https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/m3u/connection_pool.py#L261-L313
[stream]: https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/channels/models.py#L219-L286
[priority]: https://github.com/Dispatcharr/Dispatcharr/blob/8d7a02efb05c3253e490c058604accbf9e17a4e3/apps/channels/models.py#L1038-L1094
