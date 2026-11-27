# Direct probes and later Dispatcharr viewers

Coordination boundary for #1032 / `enhancedchannelmanager-0m06f.6.2` and the
best-effort restoration in `enhancedchannelmanager-88yso`.

ECM can serialize its own direct-probe reservations against freshly observed
Dispatcharr occupancy. It cannot atomically reserve a provider connection on
behalf of a direct probe against a viewer admitted later by another process.
On 2026-11-08 the PO selected a temporary **fail-closed restriction** for limited
providers. On 2026-11-15 the PO superseded it with **best-effort capacity checks**,
explicitly accepting the last-slot viewer-arrival race. Finite ungrouped profiles
can now be probed with verified headroom, bounded waiting and a bounded status
snapshot fallback. Unknown shared capacity still defers. This does not implement
leases or guaranteed viewer priority. Direct measurement and SSRF/ownership
controls remain in place; no proxy, preemption or reserved-slot setting is added.

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

The selected current policy is documented in [the operator contract](../probe-capacity.md).
Finite profile limits are checked against live occupancy plus local probes;
account Max Streams mirrors the default login rather than limiting independent
secondary logins in aggregate. Grouped or missing capacity declarations cannot
be blessed from an unrecorded/guessed grouping schema. Null-account routes remain
unverified. Identical-URL profile aliases are not extra capacity.

`enhancedchannelmanager-88yso` implements the PO-selected best-effort behavior,
not shared leases. Guaranteed viewer priority would still require a supported
shared lease/revocation/provider-close-ack contract and an explicit expiry/partition
policy; it is not a prerequisite for the accepted best-effort behavior.

Proxying changes the measured network leg, buffering/output processing, profile
selection and potentially source IP/VPN/reachability. It also needs a reviewed
trusted origin, safe stream-hash construction, auth/network permissions and
redirect/SSRF controls; broad private-network exemptions are not a safe shortcut.
Following a provider redirect can recreate the unaccounted direct connection.

Proxy/preemption/upstream-fork alternatives remain unauthorized. Preserving raw
direct measurement on limited accounts while guaranteeing protection for later
viewers still requires coordination beyond the demonstrated local contracts.

[allocator]: https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/m3u/connection_pool.py#L261-L313
[stream]: https://github.com/Dispatcharr/Dispatcharr/blob/cd3f6c509d74ad7618d870e5673476928d528de9/apps/channels/models.py#L219-L286
[priority]: https://github.com/Dispatcharr/Dispatcharr/blob/8d7a02efb05c3253e490c058604accbf9e17a4e3/apps/channels/models.py#L1038-L1094
