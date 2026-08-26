# ECM API Reference

Interactive API documentation is available at `/api/docs` (Swagger UI) and `/api/redoc` (ReDoc). `/swagger` also redirects to `/api/docs` for convenience.

All API endpoints require JWT Bearer token authentication. To authenticate in the Swagger UI:

1. Call `POST /api/auth/login` with `{"username": "...", "password": "..."}`
2. Copy the `access_token` from the response
3. Click the **Authorize** button in the Swagger UI and enter the token

## Channels

| Endpoint | Description |
|-|-|
| `GET /api/channels` | List channels (paginated, searchable, filterable) |
| `POST /api/channels` | Create channel |
| `GET /api/channels/{id}` | Get channel details |
| `GET /api/channels/{id}/streams` | Get streams for a channel |
| `PATCH /api/channels/{id}` | Update channel |
| `DELETE /api/channels/{id}` | Delete channel |
| `POST /api/channels/{id}/add-stream` | Add stream to channel |
| `POST /api/channels/{id}/add-streams` | Add multiple streams to a channel in one Dispatcharr roundtrip (dedup, order preserved) |
| `POST /api/channels/{id}/remove-stream` | Remove stream from channel |
| `POST /api/channels/{id}/reorder-streams` | Reorder channel streams |
| `POST /api/channels/assign-numbers` | Bulk assign channel numbers |
| `POST /api/channels/bulk-commit` | Batch multiple channel operations in one request |
| `POST /api/channels/merge` | Merge duplicate channels |
| `POST /api/channels/bulk-merge` | Merge several groups of duplicate channels in one request |
| `POST /api/channels/clear-auto-created` | Clear auto-created flag from channels |
| `GET /api/channels/csv-template` | Download CSV template for channel import |
| `GET /api/channels/export-csv` | Export all channels to CSV |
| `POST /api/channels/import-csv` | Import channels from CSV file |
| `POST /api/channels/preview-csv` | Preview and validate CSV before import |

### `POST /api/channels`: the `normalization` disclosure block

`normalize` (bool, default `false`) asks ECM to run the channel name through the normalization rule set before creating the channel. When normalization is requested, the `200` body carries a `normalization` object alongside the Dispatcharr channel fields:

```json
{
  "id": 412,
  "name": "CNN",
  "normalization": {
    "requested": true,
    "applied": true,
    "nameApplied": "CNN",
    "error": null
  }
}
```

| Field | Type | Meaning |
|-|-|-|
| `requested` | bool | Always `true` when the block is present |
| `applied` | bool | `false` means the engine did not run and the channel carries the raw name |
| `nameApplied` | str | The name the channel was actually created with |
| `error` | str \| null | The engine failure, when `applied` is `false` |

**A failed normalization does not fail the create.** The channel is created under the raw name and the call still returns `200`, which is exactly why the block exists: without it the response was observationally identical to `normalize: false`, so a caller that asked for normalization and silently did not get it had no way to find out (bead `enhancedchannelmanager-e9e5o`).

Two properties this contract is built on, both of which callers can rely on:

- **The block is present when and only when `normalize` was requested.** A caller that never sends `normalize` gets a byte-identical response body to the one it got before this field existed. Nothing to migrate.
- **Detection is a positive signal, not a missing key.** Branch on `applied === false`, not on the presence or absence of `normalization`. Probing for absence conflates "normalization succeeded" with "I never asked".

The MCP `create_channel` tool renders the failure as a `WARNING:` line naming the error and the name the channel was created with.

### `POST /api/channels/{id}/add-streams`

Bulk variant of `/add-stream`: fetches the channel once, appends every requested stream that isn't already on it (in request order), and PUTs once. That's one Dispatcharr roundtrip total, regardless of batch size. The MCP `bulk_add_streams_to_channel` tool calls this instead of looping the single-add endpoint, which timed out on slow hardware for batches of ~10 streams (bd-02xjj / GH #223).

**Request body:**

```json
{ "stream_ids": [101, 102, 103] }
```

**Response: `200 OK`**

```json
{
  "channel": { "id": 12, "name": "ESPN", "streams": [5, 101, 102, 103] },
  "added": [101, 102, 103],
  "skipped": [],
  "total_streams": 4,
  "journalRowsUnwritten": 0
}
```

`added` are the IDs actually appended; `skipped` are IDs already present on the channel. When every requested stream was already present, `channel` is the unmodified channel, `added` is `[]`, and no Dispatcharr write is performed. `journalRowsUnwritten` is present on both exits, including that no-op one, where it is `0` because no row was owed. See [`journalRowsUnwritten`](#journalrowsunwritten-the-write-landed-the-audit-trail-did-not).

### `POST /api/channels/bulk-commit`: operation schema

`operations` is a list of discriminated objects; the `type` string selects the shape. Unknown types or missing/mistyped fields return `422 Unprocessable Entity` with FastAPI's standard `detail` list: each entry's `loc` is `["body", "operations", <index>, "<field>"]`, so the response pinpoints the bad operation and field (the MCP `bulk_commit_channels` tool now surfaces this `detail` rather than a bare "HTTP 422", per bd-mjtxn / GH #224).

| `type` | Fields | Notes |
|-|-|-|
| `createChannel` | `tempId` (int, negative), `name` (str), `channelNumber` (float, opt), `groupId` (int, opt), `newGroupName` (str, opt), `logoId` (int, opt), `logoUrl` (str, opt), `tvgId` (str, opt), `tvcGuideStationId` (str, opt), `normalize` (bool, default `false`) | `tempId` is echoed back in `tempIdMap` → real id. Use `groupId` for an existing group or `newGroupName` to reference a group created in `groupsToCreate`. |
| `updateChannel` | `channelId` (int), `data` (dict), `acknowledgedDuplicate` (obj, opt), `expectedNumber` (obj, opt) | `data` is an **unvalidated field bag** forwarded to Dispatcharr wholesale. See [The `data` field bag](#the-data-field-bag-is-unvalidated). |
| `deleteChannel` | `channelId` (int) | |
| `addStreamToChannel` | `channelId` (int), `streamId` (int) | `channelId` may be a negative temp id from a `createChannel` in the same request. The create must precede the assignment. If the create fails or returns no usable id, the assignment fails without querying Dispatcharr with the negative id; its error retains the submitted temp id and uses the intended create name when available. Stream existence is validated for both real and temp channel ids. |
| `removeStreamFromChannel` | `channelId` (int), `streamId` (int) | |
| `reorderChannelStreams` | `channelId` (int), `streamIds` (list[int]) | New stream order; first = highest priority. |
| `bulkAssignChannelNumbers` | `channelIds` (list[int]), `startingNumber` (float, opt) | See [Range assignment defaults](#range-assignment-defaults) for what an omitted `startingNumber`, an explicit `0`, and an empty `channelIds` each do. |
| `createGroup` | `name` (str) | Group name → real id appears in `groupIdMap`. |
| `deleteChannelGroup` | `groupId` (int) | |
| `renameChannelGroup` | `groupId` (int), `newName` (str) | |
| `setProfileMembership` | `profileId` (int, `> 0`), `channelId` (int), `enabled` (bool) | All three required, no defaults. `channelId` may be a **negative temp id** from a `createChannel` earlier in the same batch; one that never resolves becomes a per-operation error, not a `422`. A `profileId` that names no profile is an `invalid_operation` validation issue at **error** severity. |
| `restoreChannelGroup` | `groupId` (int, `> 0`) | Un-hides a group ECM is hiding. A group that is **not** hidden is a **warning**, not an error: the executor treats it as a no-op and says so rather than failing. |
| `clearStreamStats` | `streamIds` (list[int], `min_length=1`, all `> 0`, no duplicates) | Duplicates are refused at schema level with the custom error `type` `duplicate_stream_ids`. Stream ids that no longer exist are **warnings** only, deliberately, so orphaned probe stats stay clearable. |

Request-level fields: `operations` (required list), `groupsToCreate` (opt list of `{name, ...}` dicts to create before processing), `validateOnly` (bool, default `false`: return `validationIssues` without applying), `continueOnError` (bool, default `false`), `consolidate` (bool, default `false`: collapse redundant ops first).

`groupIdMap` maps a group **name** to a real id. It is not a list of groups this batch created: Phase 1 also puts groups it resolved by name into the same map.

#### The response is a job handle, not the envelope

`POST /api/channels/bulk-commit` has **two response shapes**, and the default is the one that does not carry the result:

| Request | Status | Body |
|-|-|-|
| `validateOnly: true` | `200` | The full envelope, synchronously. Validation runs against ECM-cached lookups plus a single Dispatcharr page fetch, so it fits the request budget. |
| `validateOnly: false` (**the default**) | `202` | `{ "job_id": str, "status": "running", "message": str }`. The work runs in a supervised background task. |

Poll `GET /api/channels/bulk-commit/{job_id}` until the status is terminal:

- `{ job_id, status: "running" }`
- `{ job_id, status: "failed", error: str }`
- `{ job_id, status: "completed", result: <envelope> }`

**The job is evicted the moment a terminal read succeeds**, so a second poll for the same id returns `404`. Read the result once and keep it. Abandoned jobs are pruned after 30 minutes.

This shape exists because an Apply All over a large lineup does not fit inside the 30-second gateway budget. It predates the numbering work and is easy to miss, because everything below describes the **envelope**, which on the default path arrives under `result`, not as the POST body.

#### The envelope

`{ success, operationsApplied, operationsFailed, operationsPartiallyApplied, errors, tempIdMap, groupIdMap, validationIssues, validationPassed, partial, normalizationFailures, journalRowsUnwritten, numberingRecovery }`.

All thirteen keys are initialised unconditionally, so **every one is always present** on every path that returns an envelope. Branch on values, never on key presence. Pre-validation (missing referenced channels or streams) surfaces in `validationIssues` on a success status. Only schema-shape failures produce a `422`.

**Do not read `BulkCommitResponse` in `backend/routers/channels.py` as the schema.** That Pydantic model declares nine of these thirteen fields and is not wired as a `response_model` anywhere, because the handler returns a plain dict. It looks authoritative and is not.

**`normalizationFailures`** is the batch counterpart of the [`normalization` block on `POST /api/channels`](#post-apichannels-the-normalization-disclosure-block). It is **always present** and is an empty list on a clean batch, so check its length rather than probing for the key. It carries one entry per `createChannel` operation that set `normalize: true` and did not get it:

```json
{
  "normalizationFailures": [
    {
      "tempId": -3,
      "name": "US: CNN",
      "nameApplied": "US: CNN",
      "error": "normalization engine unavailable"
    }
  ]
}
```

Those operations **applied**. They appear nowhere in `errors`, `operationsFailed` does not count them, and `success` stays `true`: the channel exists, it simply carries the raw name. That is the whole reason the list is needed, since the outcome is otherwise indistinguishable from `normalize: false`. The MCP `bulk_commit_channels` tool renders the list (first 10 entries) below any validation issues.

Both fields are additive (bead `enhancedchannelmanager-e9e5o`): a caller that never sends `normalize` on any operation sees an empty list and an otherwise unchanged envelope.

`normalize` on `createChannel` is still accepted, but **ECM's own Edit Mode no longer sends it.** The Create Channels dialog resolves the final name client-side before staging, precisely so no backend pass can normalize an already-normalized name a second time (the staged name carries the channel-number and country prefixes applied on top of the engine's output). In practice `normalizationFailures` therefore reports on third-party REST and MCP callers, not on the ECM UI.

### `errors`: an entry may say the write already landed

The `errors` array is **heterogeneous**. Most entries describe one submitted operation; four kinds describe something outside the operation list and carry only `operationId` and `error`:

| `operationId` | Means |
|-|-|
| `create-group-{name}` | A Phase 1 group creation failed |
| `bulk-commit-journal` | The journal flush failed. See [`journalRowsUnwritten`](#journalrowsunwritten-the-write-landed-the-audit-trail-did-not) |
| `bulk-commit-numbering-recovery` | A half-applied numbering plan could not be fully put back. See [`numberingRecovery`](#numberingrecovery-what-to-do-by-hand) |
| `bulk-commit` | The run crashed outside the per-operation handler |

A per-operation entry carries `operationId` (`op-{index}-{type}`), `operationType`, `error`, and whichever of `channelId`, `channelName`, `streamId`, `streamName` and `entityName` apply. Note `channelId` here is the id **as submitted**, so for a staged create it is the negative temp id, not the resolved one.

One field on that entry changes what a caller must do:

```json
{
  "operationId": "op-4-createChannel",
  "operationType": "createChannel",
  "error": "journal row could not be written",
  "applied": true
}
```

**`applied: true` means the upstream write LANDED and only ECM's own bookkeeping afterwards failed. Do not retry this operation.** Retrying is what creates the entity a second time, which is the whole reason the flag exists.

Such an operation **is counted in `operationsApplied` and is never counted in `operationsFailed`.** That is not a convention. `backend/bulk_commit_accounting.py` makes `OperationLedger` the only writer of both counters, and `bulk_commit_accounting_violations` audits the finished envelope by partitioning `errors` on exactly this flag: an entry without `applied: true` must correspond to something counted in `operationsFailed` or to a setup failure. A mismatch raises `BulkCommitAccountingError` rather than logging, so it cannot ship as a quiet log line.

An `applied: true` entry does still force `success: false`, and sets `partial: true` whenever anything else in the batch applied cleanly.

### `operationsPartiallyApplied`: the operation failed, but writes of its own landed

`operationsPartiallyApplied` (int, always present, `0` normally) counts operations that are **already counted in `operationsFailed`** and that made upstream writes of their own before failing. It is a subset of the failures, never an extra category, so `operationsApplied + operationsFailed` still equals the number of operations submitted. A consumer that adds all three together will overcount.

One operation type reaches this today. `deleteChannelGroup` has two upstream side effects in sequence: it reparents the group's member channels to the fallback group, then deletes the now-empty group. If the reparent lands and the delete fails, the operation has genuinely failed (the group is still there) while the channels really did move, and they stay moved. Before bead `enhancedchannelmanager-1e4at` the envelope had one outcome per operation and could only say "failed", which reads as "nothing happened" and sends an integrator back to retry against a membership that has already changed underneath them.

The count says how many; `errors[].sideEffectsLanded` says which:

```json
{
  "operationsPartiallyApplied": 1,
  "errors": [
    {
      "operationId": "op-2-deleteChannelGroup",
      "operationType": "deleteChannelGroup",
      "error": "409 Conflict",
      "sideEffectsLanded": true
    }
  ]
}
```

Four properties this contract holds, all audited by `bulk_commit_accounting_violations` rather than left to convention. A mismatch raises `BulkCommitAccountingError` instead of logging, so it cannot ship as a quiet log line:

- **Every partially-applied operation names itself.** The number of `errors` entries carrying `sideEffectsLanded: true` must equal `operationsPartiallyApplied`. A count with nothing naming the operation would say work was left behind somewhere without saying where, which is most of the value gone.
- **`sideEffectsLanded` and `applied` are different claims and never co-occur.** `applied: true` means the operation's own outcome landed and only ECM bookkeeping failed afterwards. `sideEffectsLanded: true` means the outcome did **not** land and something else did. The audit partitions `errors` on `applied`, so a `sideEffectsLanded` entry is one of the genuine failures.
- **`operationsPartiallyApplied` can never exceed `operationsFailed`.**
- **`partial` now counts it as landed work.** A run whose only operation failed after landing a write reports `operationsApplied: 0` and still returns `partial: true`, because the caller has something to reconcile. Reading `partial` as "some operation succeeded" was never quite right and is now definitely wrong; read it as "state changed and the run did not finish cleanly".

**What to do with a non-zero count: reconcile before retrying, do not retry blindly.** The operation is safe to retry only once you know what its landed writes did. For `deleteChannelGroup` that means checking where the group's channels are now; the Journal records one row per moved channel under the run's batch id. The MCP `bulk_commit_channels` tool renders these entries in their own **PARTIALLY APPLIED** block, separate from both the applied-incomplete list and the plain failures, for exactly this reason.

### `journalRowsUnwritten`: the write landed, the audit trail did not

`journalRowsUnwritten` (int, always present, `0` normally) counts this run's journal rows that could not be written, including the batch summary row.

**Non-zero means the mutations landed and their record did not, so the operations must not be retried.** It is accompanied by a `bulk-commit-journal` entry in `errors` and forces `success: false`, but it does **not** inflate `operationsFailed`, because nothing upstream failed.

**The same field, with the same meaning, now rides on every single-mutation channel endpoint** (bead `enhancedchannelmanager-ftidn`). `journal.log_entry` reports a failed write by returning `None` and never raises, so an endpoint that called it for its effect and discarded the result could not tell a journalled mutation from an unjournalled one. A read-only, unavailable or full journal database produced a landed Dispatcharr change, no row, and a `200` that mentioned neither.

Eleven endpoints in `backend/routers/channels.py` carry it, plus `DELETE /api/channel-groups/{id}`:

| Endpoint | Where the field lands |
|-|-|
| `POST /api/channels` | On the returned Dispatcharr channel object |
| `PATCH /api/channels/{id}` | On the returned Dispatcharr channel object |
| `DELETE /api/channels/{id}` | On `{success, journalRowsUnwritten}` |
| `POST /api/channels/{id}/add-stream` | On the returned Dispatcharr channel object |
| `POST /api/channels/{id}/add-streams` | On the ECM wrapper alongside `channel`, `added`, `skipped`, `total_streams` |
| `POST /api/channels/{id}/remove-stream` | On the returned Dispatcharr channel object |
| `POST /api/channels/{id}/reorder-streams` | On the returned Dispatcharr channel object |
| `POST /api/channels/assign-numbers` | On the returned assignment result object |
| `POST /api/channels/merge` | On the returned Dispatcharr channel object |
| `POST /api/channels/bulk-merge` | On the ECM wrapper alongside `merged`, `failed`, `results` |
| `POST /api/channels/clear-auto-created` | On the ECM wrapper alongside `status`, `updated_count` and the rest |
| `DELETE /api/channel-groups/{id}` | On the `deleted` outcome only. See [`DELETE /api/channel-groups/{id}`](#delete-apichannel-groupsid) |

Three properties worth knowing before you write a client against it:

- **It rides on the `2xx`, never as a `5xx`.** The mutation landed. Telling a caller otherwise is what makes an integrator retry a change that already applied.
- **The advisory can be dropped when the response is not an object.** Seven of these endpoints return whatever Dispatcharr answered with. When that is not a JSON object there is nowhere to hang the field, so ECM **omits it** and logs the lost rows rather than inventing a wrapper. A caller cannot distinguish that case from an older build by inspecting the body. Dispatcharr answers with an object on every observed path, so this is a defensive branch rather than an expected one.
- **The no-op branches report `0` rather than nothing.** `add-stream`, `add-streams` and `remove-stream` return early when the requested state already holds. No write happened, so no row was owed, and the field is still set to `0` so a caller does not have to know which exit it got.

**Sixty-five other journal call sites across twenty-one modules still discard the return value.** That is the deliberate residue of bead `enhancedchannelmanager-ftidn`, not an oversight: the remainder sit on paths with no envelope to extend, paths returning bare lists, and fire-and-forget task-engine paths with no synchronous caller to tell, and each needs its own decision about where the advisory belongs. What has been fixed is the highest-traffic operator surface listed above plus the bulk-commit and group-delete paths. **Do not read a `journalRowsUnwritten: 0` from one of these endpoints as a statement about the journal's reliability generally.** On any endpoint not in the table above, a missing journal row is still silent.

### `numberingRecovery`: what to do by hand

`numberingRecovery` (list, always present, empty normally) is populated only when a numbering plan was half-applied **and** at least one compensating write also failed. Each entry names one channel and the exact step that fixes it:

```json
{
  "numberingRecovery": [
    {
      "channelId": 812,
      "channelName": "BBC One",
      "currentNumber": 204.0,
      "targetNumber": 101.0,
      "step": "Set \"BBC One\" (channel 812) back to channel number 101; this run left it on 204.",
      "error": "502 Bad Gateway"
    }
  ]
}
```

`currentNumber` and `targetNumber` are `float | null`. The `bulk-commit-numbering-recovery` entry in `errors` carries no per-channel detail of its own; it points here and says not to retry the batch until these are fixed.

**Be clear about the guarantee.** ECM guarantees a compensating write is *attempted* for every numbering write that landed, replayed newest-first. It does **not** guarantee the compensating write succeeds, and it cannot: Dispatcharr 0.28.x offers no conditional update, so this is a best-effort repair rather than a rollback. Where the repair fails, `numberingRecovery` is the operator's instruction list. Two further limits worth knowing: compensation runs only when the plan is genuinely half-applied, and it does not run at all on the crash or cancellation paths, which sit outside the block that performs it.

### Validation issues

`validationIssues` entries always carry `type`, `severity` and `message`. Six `type` values exist:

| `type` | Severity | Extra fields |
|-|-|-|
| `missing_channel` | error | `operationIndex`, `channelId`, sometimes `channelName` / `streamId` |
| `missing_stream` | error, or **warning** from `clearStreamStats` | `operationIndex`, `streamId`, and on the error form `channelId` / `channelName` |
| `invalid_operation` | error (unknown profile), or **warning** (`restoreChannelGroup` on a group that is not hidden) | `operationIndex`, and `channelId` on the error form |
| `numbering_preflight_unavailable` | error | none |
| `duplicate_channel_number` | error | `channelNumber`, `channelIds`, `operationIndex`, `operationIndexes`, `channelId` |
| `invalid_channel_number` | error | same set as above |

The frontend's own `ValidationIssue` union names only three of the six, so do not take it as the list.

On the last two, `operationIndex` is simply the first element of `operationIndexes`, and `channelId` the first of `channelIds`. They are scalars kept for schema compatibility, not independent facts.

**`numbering_preflight_unavailable` is the one to handle deliberately.** It is raised when the batch places a channel but ECM could not read the current lineup to check the resulting final state against. Under the **default `continueOnError: false` it refuses the commit**: nothing is executed, and because execution never started there is no journal trace at all. Under `continueOnError: true` the run proceeds unchecked, which is what ECM's own Edit Mode sends, because by that point the browser has already run its own preflight against a lineup it fetched itself.

A batch that places nobody is never refused on this ground. "Places a channel" means a create carrying an explicit number, an `updateChannel` whose `data` contains `channel_number`, or a `bulkAssignChannelNumbers` with a non-empty `channelIds`.

### `acknowledgedDuplicate`: consent to a specific collision

Accepted on `updateChannel` and `createChannel` only. Both fields are **required**, with no defaults:

```json
{ "acknowledgedDuplicate": { "number": 102, "occupantChannelIds": [57] } }
```

It is ECM bookkeeping and is never forwarded to Dispatcharr. It tells the final-state preflight that this collision is deliberate, so it is not reported as a duplicate.

Three properties that are easy to get wrong:

- **The occupants are load-bearing, not decoration.** Consent is checked as a subset test against who actually stands on the number. Consenting to share `102` with `{57}` while `{57, 91}` really stand there is **refused**, because the operator was never shown 91. The reverse (consenting to `{57, 91}` while only `{57}` stands) is accepted. The same subset test (not equality) governs the third place an acknowledgement is judged, `planLedgerRestore` in `frontend/src/utils/stagedLedgerStorage.ts`, which decides whether a confirmation survives a dead session. All three agree deliberately: a restore stricter than the Apply it precedes re-interrogates the operator about a collision Apply then accepts unasked.
- **A caller meaning "nobody was there" must send `[]` explicitly.** There is no default, precisely so that omission cannot be read as consent.
- **It replaces, it does not accumulate.** A later placement of the same channel that carries no acknowledgement clears the earlier one.

### `expectedNumber`: refuse to overwrite a change you have not seen

On `updateChannel` only. It is a **wrapper object, not a bare number**:

```json
{ "expectedNumber": { "number": 101 } }
```

The wrapper exists because `null` is a legitimate expectation, meaning "I believe this channel has no number", and a bare optional cannot tell that apart from "I am making no claim". **The object's presence is the claim; its `number` is the value.** Document and send it as `{number: number | null}`, never as `expectedNumber: 101`.

Semantics:

- Ignored entirely unless `data` carries `channel_number`. An operation that does not write the number cannot overwrite anyone's change to it.
- Compared against the lineup snapshot taken **at the start of the run**, not the running working copy, so an earlier operation in the same request moving the channel is not reported as a conflict.
- On mismatch the PATCH is **not sent**. The operation becomes an ordinary per-operation error entry and increments `operationsFailed`; it is not an HTTP `409` and carries no `applied` flag, because nothing landed.
- If the lineup could not be read at all, the operation is refused rather than attempted.

**It is a check, not a guarantee**, and that was measured rather than assumed: the live Dispatcharr 0.28.x schema contains no `If-Match`, `If-None-Match`, `If-Unmodified-Since`, `ETag` or `412`, and neither `Channel` nor `PatchedChannel` carries a version or modified-at field. A change landing between ECM's read and its PATCH is still lost. What it closes is the much wider window between a browser reading the lineup and the executor writing to it.

Range assignments deliberately send no `expectedNumber`, so a `bulkAssignChannelNumbers` has no server-side concurrency guard. That is a decision, not an oversight; the browser-side check is the only one covering it.

### Range assignment defaults

`bulkAssignChannelNumbers` has three edge cases worth stating exactly:

- **`startingNumber` omitted defaults to `1`** in consolidation, in the final-state materialiser, and in the executor's journal and compensation bookkeeping. There is one gap: on the `consolidate: false` path the executor forwards the raw `None` upstream and lets Dispatcharr choose, while journaling as though the start were `1`. ECM's own UI always sends `consolidate: true`, which rewrites the range with an explicit `startingNumber`, so this is not reachable from the interface. Send `startingNumber` explicitly if you are not consolidating.
- **An explicit `0` is honoured.** Zero is in contract, and an earlier implementation that collapsed "omitted" and "explicitly zero" into one branch was fixed deliberately.
- **An empty `channelIds` is a no-op only under `consolidate: true`.** With `consolidate: false` the executor still issues one upstream assign call with an empty id list and records the operation as applied. It changes no channel, but calling it a pure no-op overstates it.

### What `consolidate` guarantees

`consolidate` defaults to `false` and none of this runs unless it is set. ECM's own Edit Mode always sets it.

Consolidation collapses redundant operations, and the property it exists to hold is:

**Submitted last-write-wins on `channel_number` is preserved across operation kinds, and exactly one emitted operation writes any given channel's number.**

Both halves matter. Ownership of a channel's number is decided by **submitted position** across every kind that places a channel (`createChannel`, `updateChannel`, `bulkAssignChannelNumbers`), not per-kind. When a later operation owns the number:

- a superseded `createChannel` is **still emitted, without its number and without its `acknowledgedDuplicate`**, because every operation naming its temp id depends on it existing;
- a superseded `updateChannel` loses `channel_number`, `acknowledgedDuplicate` and `expectedNumber`, and is dropped entirely if nothing else remains to PATCH;
- a range assignment is filtered down to the channels it still owns.

The one-writer property is enforced as a property rather than observed as an outcome: the unit suite asserts it over generated mixed-kind operation lists, with an anti-vacuity control confirming the same check **fails** on the unconsolidated list.

One consequence that surprises people: a `createChannel` records itself as its own number's owner **even when it carries no number**. So a range assignment sent *before* the create it names places nothing.

### The `data` field bag is unvalidated

`updateChannel`'s `data`, and the body of `PATCH /api/channels/{id}`, are **free-form dicts forwarded to Dispatcharr wholesale.** There is no allowlist and no schema. Whatever keys you send are what Dispatcharr receives.

Two key-level checks exist, and they are the only ones:

- `channel_number`, on both paths, is held to ECM's canonical channel-number contract.
- `channel_group_id`, on the **bulk path only**, is rejected if it is still a negative staging placeholder.

This is a documented residual rather than an unexamined default, tracked as open bead `enhancedchannelmanager-t683u`. The reasoning for leaving it open is recorded there: an allowlist guessed too narrow would silently refuse an Apply All that used to work. The mitigating change already made is that the journal's change describer is now total over the payload, so the surface is auditable even though it is unconstrained.

### `POST /api/channels/bulk-merge`: `sources_failed` and the incomplete-merge action type

A bulk merge moves each group's combined streams onto the target channel and then deletes that group's source channels. **Those are separate Dispatcharr calls, and the deletions can fail while the stream move succeeds.** A per-group result therefore reports both halves (bead `enhancedchannelmanager-ftidn`):

```json
{
  "target_channel_id": 812,
  "target_name": "BBC One",
  "sources_deleted": 1,
  "sources_failed": 2,
  "total_streams": 6,
  "success": true
}
```

`sources_failed` (int, always present) is the number of source channels named for this group that are **still in Dispatcharr** when the group finishes: it counts the deletions that errored and the ones the request never reached, because both leave the channel there. It is always present, so a caller checks a number rather than probing for a key. `success: true` on the group means the group was processed, not that it completed: read `sources_failed` for that.

**The Journal row for the group carries the same fact under its own action type.** A group whose streams moved and whose source channels are all gone writes a `bulk_merge` row. Anything else writes **`bulk_merge_incomplete`**, and its `after_value.undeleted_ids` names the channels that are still upstream. The row's action and prose are finalised from the outcome as each fact lands rather than asserted when the group starts, so a run cancelled mid-flight still leaves a row that describes what actually happened. Filter the Journal on the action type to find the merges that need attention instead of reading every row's description. The envelope and the Journal are built from the same counts, so they cannot tell a caller and an operator different things about one group.

### `POST /api/channels/assign-numbers`: omitting `starting_number` costs N extra reads

`starting_number` is optional, and omitting it is a distinct request rather than an edge case: it asks **Dispatcharr** to choose the numbers. Dispatcharr's assign endpoint declares no response body beyond a confirmation string, so the numbers it chose are not in the response and cannot be inferred from the request either. ECM therefore issues one read-back `GET` per channel on that path and finalises each Journal row from what it observes.

Two consequences for a caller:

- **The path where you supply `starting_number` pays none of that cost.** ECM already knows those numbers, so it does not spend N extra round-trips on a several-hundred-channel renumber. Supply the starting number when you can.
- **A read-back that fails does not invent a number.** The assignment landed; only the read failed. That channel's Journal row says the number has not been read back rather than naming one nobody observed, and the failure is logged at WARN. The row is still written.

## Channel Groups

| Endpoint | Description |
|-|-|
| `GET /api/channel-groups` | List all groups |
| `POST /api/channel-groups` | Create group |
| `PATCH /api/channel-groups/{id}` | Update group |
| `DELETE /api/channel-groups/{id}` | Delete group |
| `GET /api/channel-groups/orphaned` | List orphaned groups (no streams, channels, or M3U association) |
| `DELETE /api/channel-groups/orphaned` | Delete orphaned groups (optionally specify group IDs) |
| `GET /api/channel-groups/hidden` | List hidden channel groups |
| `POST /api/channel-groups/{id}/restore` | Restore a hidden channel group |
| `GET /api/channel-groups/auto-created` | List groups with auto-created channels |
| `GET /api/channel-groups/with-streams` | List groups that have channels with streams |

### `DELETE /api/channel-groups/{id}`

Two outcomes, distinguished by `status`, and only one of them deletes anything.

**A group with M3U sync settings is hidden, not deleted**, so that the sync keeps working:

```json
{ "status": "hidden", "message": "Group hidden (M3U sync active)" }
```

**A group without M3U sync is deleted.** Dispatcharr refuses to delete a group that still holds channels and refuses a null `channel_group_id`, so ECM first moves the members to the fallback group, `Default Group` (`UNGROUPED_TARGET_GROUP_NAME` in `backend/channel_group_reparent.py`), and then deletes the empty group:

```json
{ "status": "deleted", "channels_moved": 3, "journalRowsUnwritten": 0 }
```

| Field | Type | Meaning |
|-|-|-|
| `status` | str | `deleted` or `hidden` |
| `channels_moved` | int | Members reparented to the fallback group before the delete. `deleted` outcome only |
| `journalRowsUnwritten` | int | Journal rows this request could not write. Always present on the `deleted` outcome, `0` normally. See [`journalRowsUnwritten`](#journalrowsunwritten-the-write-landed-the-audit-trail-did-not) |

**The `hidden` outcome carries neither `channels_moved` nor `journalRowsUnwritten`, and writes no journal row.** It performs no Dispatcharr write and moves no channels; it records an ECM-side `HiddenChannelGroup` row. Branch on `status` before reading either field.

**This route now leaves a journal trail (bead `enhancedchannelmanager-jd3kn`).** It previously wrote nothing at all, neither for the channels it reparents nor for the deletion itself, while the Edit Mode bulk commit wrote both. The same operator-visible action left a full trail through one route and silence through the other, and the silent one is the route the MCP `delete_channel_group` tool and any direct API client take. A successful delete now writes one `update` row per moved channel plus one `group_delete` row, all under one batch id, and each move is recorded as it lands rather than summarised after the delete succeeds.

**When the delete fails after the reparent landed, the error says so.** The channels really did move and they stay moved. An upstream `4xx` keeps its status and its own detail text; anything else answers `500`. Either way, when at least one channel had already been reparented the detail names the count and points at the Journal:

> 409 Conflict — the group still exists, but 3 of its channel(s) had already been moved to 'Default Group' before the delete failed and they stay moved. Check the Journal for which ones before retrying. <!-- em-dash-ok: verbatim quote of the detail string the handler returns -->

Two limits on that advisory, both real:

- **A `500` never carries it to the caller.** `main.sanitized_http_exception_handler` replaces the detail of every `500` to keep internals off the wire, so on a genuine server fault the container log is the only place the advisory reaches a human. It is logged at `ERROR` for exactly that reason.
- **The MCP `delete_channel_group` tool does not relay it.** The tool reports hidden-versus-deleted and performs a read-back, but it does not surface `channels_moved` or `journalRowsUnwritten`. An agent that needs them must read the REST response.

**`DELETE /api/channel-groups/orphaned` is not covered by any of this.** It still writes a single summary `cleanup` row and discards the result, so a failed write there is silent. It is one of the sixty-five call sites bead `enhancedchannelmanager-ftidn` deliberately leaves open.

## Channel Merges (Stream Deduplication)

The `/api/channel-merges/*` family is the API surface for the v0.17.1 interactive stream-to-channel deduplication feature (ADR-008). It exposes the pending merges queue, the synchronous candidate lookup, and the accept/dismiss decision endpoints.

See [`docs/user_guide/channels-streams/stream-dedup.md`](user_guide/channels-streams/stream-dedup.md) for the operator-facing workflow.

| Endpoint | Description |
|-|-|
| `GET /api/channel-merges/candidates` | Synchronous candidate lookup: find the best matching channel for an incoming stream name |
| `GET /api/channel-merges` | List pending (or resolved) merge rows, paginated |
| `GET /api/channel-merges/snapshot` | Read one coherent, bounded snapshot of the complete pending queue |
| `POST /api/channel-merges/{id}/accept` | Accept the dedup candidate: merge the stream into the candidate channel |
| `POST /api/channel-merges/{id}/dismiss` | Dismiss the dedup candidate: signal that a new channel should be created |

All endpoints require JWT Bearer token authentication. `GET /api/channel-merges` requires `RequireAuthIfEnabled`. The candidate lookup, complete snapshot, and `POST` mutation endpoints require `RequireAdminIfEnabled`.

---

### `GET /api/channel-merges/candidates`

Synchronous lookup: given an incoming stream name and optional group scope, returns the best matching candidate channel from Dispatcharr. Used by the interactive drag-drop and "Add Stream" surfaces to decide whether to show the dedup modal.

**Query parameters:**

| Parameter | Type | Required | Description |
|-|-|-|-|
| `stream_name` | string | Yes | The incoming stream name to score against existing channels |
| `group_id` | integer | No | Dispatcharr group ID; restricts the candidate pool to channels in this group |
| `page` | integer | No | Page number (default: 1) |
| `page_size` | integer | No | Results per page (default: 50) |

ECM fetches channels from Dispatcharr, runs them through the dedup matcher with the operator-configured `dedup_threshold` (clamped to the ADR-008 §D2 hard floor of 60%), and returns the top-1 candidate or an empty list if no candidate meets the threshold.

**Response: `200 OK`**

```json
{
  "stream_name": "ESPN HD",
  "candidates": [
    {
      "channel_id": "a1b2c3d4-e5f6-...",
      "channel_name": "ESPN",
      "confidence": 0.87
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50,
  "total_pages": 1
}
```

`candidates` contains at most one entry: the best match above the threshold. An empty `candidates` list means no channel met the threshold; the caller should proceed with creating a new channel. Confidence is expressed as a decimal (0.0–1.0); the configured `dedup_threshold` is the minimum value that will appear.

**Metric emitted:** `ecm_dedup_candidate_lookup_duration_seconds` Histogram (SLO-10a).

**Example:**

```bash
curl -X GET "http://localhost:6100/api/channel-merges/candidates?stream_name=ESPN+HD&group_id=12" \
  -H "Authorization: Bearer TOKEN"
```

---

### `GET /api/channel-merges`

Returns the paginated list of channel merge rows. Use the `status` query parameter to view the live queue (`pending`), accepted rows (`merged`), or dismissed rows (`dismissed`).

**Query parameters:**

| Parameter | Type | Required | Description |
|-|-|-|-|
| `status` | string | No | Filter by row state: `pending` (default), `merged`, or `dismissed` |
| `group_id` | integer | No | Filter by Dispatcharr group ID |
| `page` | integer | No | Page number (default: 1) |
| `page_size` | integer | No | Results per page (default: 50) |

**Response: `200 OK`**

```json
{
  "merges": [
    {
      "id": 42,
      "stream_name": "ESPN HD",
      "group_id": 12,
      "candidate_channel_id": "a1b2c3d4-e5f6-...",
      "confidence": 0.87,
      "status": "pending",
      "trigger_context": "m3u_refresh",
      "created_at": 1747497600000,
      "resolved_at": null,
      "resolution_source": null,
      "unapplied_reason": null
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50,
  "total_pages": 1
}
```

`trigger_context` is one of `drag_drop`, `add_stream`, `m3u_refresh`, `mcp_tool`. `created_at` and `resolved_at` are epoch milliseconds (UTC). Terminal-state rows (`merged`, `dismissed`) have `resolved_at` populated and `resolution_source` set to `operator`, `auto`, `bulk_m3u_hook`, or `mcp_tool`.

**`unapplied_reason` (str | null) is how a `pending` row says it has already been accepted once.** It carries, in operator-actionable prose, why the last accept on that row could not be applied to Dispatcharr; `null` means no accept on this row has failed to apply. A row with `status: "pending"` **and** a non-null `unapplied_reason` is a merge the operator accepted that ECM could not carry out: it stays in the queue on purpose, `resolved_at` and `resolution_source` stay `null` because the row never left, and re-accepting it is an ordinary accept rather than an idempotent replay. A retry that lands clears the column. See [`POST /api/channel-merges/{id}/accept`](#post-apichannel-mergesidaccept) for how the flag is set and cleared. A client that renders the queue should treat it as a per-row flag, not as a fourth `status` value: `status` still takes exactly `pending`, `merged`, `dismissed`.

---

### `GET /api/channel-merges/snapshot`

Returns one deterministic snapshot of the complete `pending` queue for Select
all, selected Refresh, and queue-wide confirmation. The route is read-only but
**requires admin authorization** because it exposes the complete destructive
action target set. Optional `group_id` scopes the snapshot to pending records
in one channel group, matching the list endpoint's group filter. Clients must
forward the active list scope so a bulk action cannot target another group.

The database record set is read by one ordered query (`created_at DESC`,
`id DESC`). Candidate name, number, and group enrichment follows the paginated
list serializer; unresolved candidates retain their ID and return null
enrichment fields.

**Response: `200 OK`**

```json
{
  "merges": [
    {
      "id": 42,
      "stream_name": "ESPN HD",
      "group_id": 12,
      "candidate_channel_id": "a1b2c3d4-e5f6-...",
      "candidate_channel_name": "ESPN",
      "candidate_channel_number": 101,
      "candidate_channel_group_name": "Sports",
      "confidence": 0.87,
      "status": "pending",
      "trigger_context": "m3u_refresh",
      "created_at": 1747497600000,
      "resolved_at": null,
      "resolution_source": null,
      "unapplied_reason": null
    }
  ],
  "total": 1
}
```

`unapplied_reason` carries the same meaning here as on the paginated list: a non-null value on a `pending` row marks a merge the operator already accepted that ECM could not apply. Because the snapshot is what a bulk action targets, those rows are in the target set like any other pending row, and re-accepting one is a retry.

The safety ceiling is 20,000 pending records. If the queue exceeds it, ECM
returns **`409 Conflict`** with `detail` stating the limit and that nothing was
changed; it never returns a partial snapshot.

---

### `POST /api/channel-merges/{id}/accept`

Accept the dedup candidate: merge the incoming stream into the candidate channel in Dispatcharr. Writes an audit row to `pending_merge_journal` (ADR-008 §D6). The `id` is the `pending_merges.id` integer from the list endpoint.

**Authentication:** `RequireAdminIfEnabled`

**Path parameter:** `id` (integer), the pending merge row ID.

**Request body:** none.

**Response: `200 OK`**, flat outcome envelope.

```json
{
  "merged_into_channel_id": "a1b2c3d4-e5f6-...",
  "journal_entry_id": 307,
  "source_stream_id": "s9k2m1p7-...",
  "confidence": 0.87,
  "status": "merged",
  "dispatcharr_updated": true,
  "unapplied_reason": null,
  "journal_rows_unwritten": 0
}
```

The same envelope for an accept ECM could not apply. Note `status`, which is the queue row's real state and not a constant:

```json
{
  "merged_into_channel_id": "a1b2c3d4-e5f6-...",
  "journal_entry_id": 308,
  "source_stream_id": "ESPN HD",
  "confidence": 0.87,
  "status": "pending",
  "dispatcharr_updated": false,
  "unapplied_reason": "No Dispatcharr stream is named \"ESPN HD\", so this merge was recorded but NOT applied upstream. The stream may have been renamed or removed since it was queued.",
  "journal_rows_unwritten": 0
}
```

`source_stream_id` is the resolved Dispatcharr stream ID when the name lookup is unambiguous; falls back to the raw `stream_name` string when the lookup is ambiguous (audit-first contract per ADR-008 §D6). `journal_entry_id` is the `pending_merge_journal` row ID.

#### `status` describes the queue row, and it is not a constant

This is the distinction the whole envelope turns on. `status` is the `pending_merges` row's own state machine (§D3), **not a claim that Dispatcharr was updated**. Until bead `enhancedchannelmanager-i5ic0` it was hardcoded to `merged` and was the only outcome field the response carried, so an accept whose stream-name lookup matched nothing at all returned exactly the same body as one that added the stream. Four fields now carry the outcome, and `status` is one of them.

| Field | Type | Meaning |
|-|-|-|
| `status` | `"merged"` \| `"pending"` | The queue row's state after this request. `"merged"` when the merge was applied and the row left the queue; `"pending"` when ECM could not apply it and the row stayed |
| `dispatcharr_updated` | bool \| null | Whether the candidate channel ends this request holding the stream |
| `unapplied_reason` | str \| null | Operator-actionable prose for anything other than a clean apply. `null` when applied |
| `journal_rows_unwritten` | int | Operator-journal rows this request could not write. Always present |

**`dispatcharr_updated` is three-valued, and `!== false` is not a test for success:**

- **`true`** means the channel holds the stream. This covers both the case where this call sent the PATCH and the case where the stream was already on the channel, because the requested end state holds either way. Only the first writes a journal row; nothing changed in the second, so there is nothing to trace.
- **`false`** means no upstream write happened. Five paths reach it: the lookup matched nothing, it matched several streams, it matched exactly one that carries no usable id, the lookup itself failed, or the search filled its single page. That last one is the subtle one. **A truncated search cannot establish uniqueness even when exactly one exact match is visible on the page it saw**, because further matches may sit on pages nobody asked for, and adding one of several possible streams is not the merge that was requested.
- **`null`** means an idempotent replay: the row was already `merged`, so this request made no Dispatcharr call and has **no evidence either way** about what the original one did. Reporting `true` there would be the same false success claim one branch over. `unapplied_reason` on this path says so and points at the journal against the candidate channel.

**The three values map one-to-one onto the queue state** (PO decision 2026-08-16), rather than varying independently of it:

| `dispatcharr_updated` | `status` | `pending_merges.unapplied_reason` | What the row did |
|-|-|-|-|
| `true` | `"merged"` | cleared | Left the queue. `resolved_at` and `resolution_source` are set |
| `false` | `"pending"` | set to the same prose the response carries | Stayed in the queue, flagged. `resolved_at` and `resolution_source` stay `null` |
| `null` | `"merged"` | untouched | Nothing. This request only replayed an earlier one |

Read the table in both directions. A row deliberately still queued can never answer `null`, because **only an already-terminal row produces a replay**; that is what keeps "this request obtained no upstream evidence" and "still queued on purpose" from collapsing into one value.

A `dispatcharr_updated` of `false` or `null` still returns `200`. Nothing failed: the request succeeded and was recorded. What did not happen is the upstream write, and only the caller can finish it.

**Finding these afterwards.** A merge ECM could not apply **keeps its queue row**, so a `status=pending` reload returns it with `unapplied_reason` populated, and the ECM UI flags it in place as **Not applied**. Re-accepting it is the retry. The outcome is *also* recorded in the operator-facing Journal alongside `pending_merge_journal`, so it survives the row being resolved later: an accept that did not apply writes a `merge_unapplied` row under the **Channel** category naming the stream, the channel and the reason, with `after_value.pending_merge_status` recording the queue state that outcome left behind. See [Stream Deduplication](user_guide/channels-streams/stream-dedup.md#what-happens-when-a-merge-is-recorded-but-not-applied).

**Idempotency, and what is not idempotent.** This endpoint is idempotent on the `merged` terminal state: calling `/accept` on a row already in `merged` returns `200` with the prior outcome envelope, and `dispatcharr_updated: null`. **A flagged `pending` row is not terminal, so calling `/accept` on it is a real retry, not a replay.** It re-runs the stream lookup, may send the PATCH, and clears `unapplied_reason` if it lands. A client that treated any second accept as a no-op would skip the one call that finishes the work. Calling `/accept` on a `dismissed` row returns `409 INVALID_STATE`.

**Metric note for operators scraping Prometheus.** An accept ECM could not apply increments `ecm_dedup_merge_requests_total{status="unapplied"}`, a label value added with the same PO decision. It is deliberately not counted as `success`, because SLI-10b counts terminal transitions out of the queue and a flagged row makes none. See [`docs/sre/slos.md`](sre/slos.md) SLO-10.

**Audit fields:** the `pending_merge_journal` row records `actor_token_id` (the JWT session's underlying API token ID), `action_type='merge_confirmed'`, `trigger_context` carried from the queue row, and `confidence_score` captured at action time.

**Error responses:**

| Status | Code | Description | When |
|-|-|-|-|
| 404 | `TARGET_NOT_FOUND` | Candidate channel no longer exists in Dispatcharr | The candidate channel was deleted after the pending row was queued; dismiss this row and re-run the original trigger |
| 409 | `INVALID_STATE` | Row is in a terminal state that cannot accept this transition | Calling `/accept` on a `dismissed` row |

**Example:**

```bash
curl -X POST "http://localhost:6100/api/channel-merges/42/accept" \
  -H "Authorization: Bearer TOKEN"
```

---

### `POST /api/channel-merges/{id}/dismiss`

Dismiss the dedup candidate: signal that a new channel should be created for this stream. Writes an audit row to `pending_merge_journal`. Does not call Dispatcharr. This is a pure ECM-side state flip.

**Authentication:** `RequireAdminIfEnabled`

**Path parameter:** `id` (integer), the pending merge row ID.

**Request body:** none.

**Response: `200 OK`**, flat outcome envelope.

```json
{
  "journal_entry_id": 308,
  "status": "dismissed"
}
```

This endpoint is **idempotent** on the `dismissed` terminal state: calling `/dismiss` on a row already in `dismissed` returns `200`. Calling `/dismiss` on a `merged` row returns `409 INVALID_STATE`.

**Error responses:**

| Status | Code | Description | When |
|-|-|-|-|
| 404 | Not Found | Row ID does not exist | Invalid or already-purged row ID |
| 409 | `INVALID_STATE` | Row is in a terminal state that cannot accept this transition | Calling `/dismiss` on a `merged` row |

**Example:**

```bash
curl -X POST "http://localhost:6100/api/channel-merges/42/dismiss" \
  -H "Authorization: Bearer TOKEN"
```

---

### Error codes

| Code | HTTP status | Description |
|-|-|-|
| `TARGET_NOT_FOUND` | 404 | The candidate channel no longer exists in Dispatcharr. The operator path is to dismiss this pending merge row and re-run the original trigger (drag-drop, Add Stream, or M3U refresh). The refreshed run will find a current candidate if one exists, or fall through to new-channel creation if none does. |
| `INVALID_STATE` | 409 | The row is already in a terminal state that makes the requested transition invalid: `/accept` on a `dismissed` row, or `/dismiss` on a `merged` row. Both terminal states are idempotent for their own action (accept-on-merged → 200 with prior envelope; dismiss-on-dismissed → 200). |

---

## Event Sync Reviews

The `/api/event-sync-reviews/*` family (bead ti939.3.2) is the review queue for ambiguous Event Sync matches: ambiguous-band scores and contested ties enqueue here instead of being silently skipped. Rows key on **content fingerprints**: `(rule_id, provider_id, stream_name_hash, event_key)`, never channel/stream IDs, so decisions survive Dispatcharr refreshes and re-apply on every future run. Feature guide: [`docs/event_sync.md`](event_sync.md) → "Reviewing ambiguous matches"; fingerprint semantics: `backend/services/event_sync_review.py`.

| Endpoint | Description |
|-|-|
| `GET /api/event-sync-reviews` | Paginated list. Query: `status` (`pending` default \| `accepted` \| `rejected` \| `superseded`), `rule_id`, `page`, `page_size` (≤200). Rows carry the fingerprint columns, state-machine fields, and a parsed display-only `evidence` snapshot (both raw names, parsed titles/starts, score/band/team-verdict/time-delta, snapshot ids). `RequireAuthIfEnabled`. |
| `POST /api/event-sync-reviews/{id}/accept` | Accept a pairing: records the durable fingerprint decision (future runs auto-attach it), supersedes sibling pending pairings for the same stream fingerprint, then best-effort attaches immediately. Snapshot channel/stream ids are re-verified against live Dispatcharr (channel name must still parse to the row's `event_key`; stream name must still hash to `stream_name_hash`), and a failed verification defers the attach to the next run (`attach_deferred_reason`). Response: `{status: "accepted", attached, already_attached, attach_deferred_reason, superseded_siblings}`. Idempotent on `accepted`; `409` on `rejected`/`superseded`; `404` if missing. `RequireAdminIfEnabled`. |
| `POST /api/event-sync-reviews/{id}/reject` | Reject a pairing: durable fingerprint suppression. Future runs neither attach nor re-ask. No Dispatcharr call. Response: `{status: "rejected"}`. Idempotent on `rejected`; `409` on `accepted`/`superseded`. `RequireAdminIfEnabled`. |

Audit: accepts/rejects write `journal_entries` rows (category `event_sync`, action `review_accept`/`review_reject`); an immediate attach writes the standard `merge_stream` entry with `after_value.match.attach_source = "review_queue"` (threshold attaches carry `"threshold"`), keeping queue-driven attaches distinguishable and covered by the journal-driven surgical unmerge.

---

## Event Sync Exclusions

The `/api/event-sync-exclusions/*` family (bead ti939.3.5) is the operator "never attach this pairing" surface: a durable standing order the shared resolver (`backend/services/event_sync_resolver.py`) filters out on **every** future run and preview, before the attach band is even honored. It closes the loop a stateless recompute otherwise can't: a false-positive attach the operator manually detaches would keep re-attaching on every subsequent run. Rows key on the same **content fingerprint** as review rows: `(rule_id, provider_id, stream_name_hash, event_key)`, never channel/stream IDs, so an exclusion survives Dispatcharr refreshes and stream-ID churn. An exclusion **outranks** a prior review-queue accept for the same fingerprint. Feature guide: [`docs/event_sync.md`](event_sync.md) → "Never-attach exclusions"; fingerprint semantics: `backend/services/event_sync_review.py`.

| Endpoint | Description |
|-|-|
| `GET /api/event-sync-exclusions` | Paginated list, newest first. Query: `rule_id` (optional filter), `page`, `page_size` (≤200, `400` if out of range). Rows carry the fingerprint columns plus a parsed display-only `evidence` snapshot. `RequireAuthIfEnabled`. |
| `POST /api/event-sync-exclusions` | Create a standing exclusion from the fingerprint components (body shape below). **Idempotent on the fingerprint**: a repeat POST for an already-excluded pairing returns the existing row (`already_existed: true`) rather than creating a duplicate, and refreshes the stored `note` if a new one is supplied. `404` if `rule_id` doesn't reference an existing rule. `RequireAdminIfEnabled`. |
| `DELETE /api/event-sync-exclusions/{id}` | Remove the standing order. The pairing becomes matchable again on the next run/preview. The delete itself re-attaches nothing (the idempotent run is the applier, same posture as a review-queue accept). `404` if the id doesn't exist. `RequireAdminIfEnabled`. |

### `POST /api/event-sync-exclusions`: fingerprint body shape

```json
{
  "rule_id": 12,
  "provider_id": 7,
  "stream_name_hash": "5f2c1a...e91a",
  "event_key": "mercury vs. aces|2026-07-11T22:00:00+00:00",
  "note": "Wrong venue, provider always mislabels this slot",
  "evidence": {
    "stream_name": "Peacock 14: Mercury vs. Aces @ 11 Jul 06:00 PM ET",
    "master_channel_name": "Mercury vs. Aces",
    "rule_name": "Live Events (multi-provider)",
    "provider": "Provider B"
  }
}
```

| Field | Type | Required | Description |
|-|-|-|-|
| `rule_id` | integer | Yes | The owning event_sync rule. Must reference an existing `ChannelPipelineRule` (`404` otherwise). |
| `provider_id` | integer (≥0) | Yes | The secondary stream's M3U account id: `0` is the documented unknown-provider sentinel. |
| `stream_name_hash` | string | Yes | SHA-256 hex of the secondary stream's LOCALS-cleaned raw name (`services.dedup_matcher.clean_name`). Copy it verbatim from a review row or a preview candidate; do not compute it client-side. |
| `event_key` | string | Yes | The master side's parsed event identity: `<LOCALS-cleaned parsed title>\|<parsed start as UTC ISO-8601>`. |
| `note` | string, ≤2000 chars | No | Free-text operator annotation ("why never"). |
| `evidence` | object | No | Display-only snapshot (raw names etc.) for the exclusions-list UI. Never identity-authoritative; never re-verified against Dispatcharr. |

The four fingerprint fields are never derived from channel/stream IDs. They're supplied verbatim, exactly as they appear on a review-queue row (`GET /api/event-sync-reviews`) or a preview response's candidate context.

**Response: `200 OK`**, `EventSyncExclusionRecord`:

```json
{
  "id": 4,
  "rule_id": 12,
  "provider_id": 7,
  "stream_name_hash": "5f2c1a...e91a",
  "event_key": "mercury vs. aces|2026-07-11T22:00:00+00:00",
  "created_at": 1752278400000,
  "note": "Wrong venue, provider always mislabels this slot",
  "evidence": { "stream_name": "...", "master_channel_name": "...", "rule_name": "...", "provider": "..." },
  "already_existed": false
}
```

`GET /api/event-sync-exclusions` wraps rows in the standard paginated envelope: `{exclusions: [EventSyncExclusionRecord, ...], total, page, page_size, total_pages}`.

Audit: create/delete write `journal_entries` rows (category `event_sync`, action `exclusion_create` / `exclusion_delete`).

**Example:**

```bash
curl -X POST "http://localhost:6100/api/event-sync-exclusions" \
  -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"rule_id": 12, "provider_id": 7, "stream_name_hash": "5f2c1a...e91a", "event_key": "mercury vs. aces|2026-07-11T22:00:00+00:00"}'
```

MCP mirror: `list_event_sync_exclusions` / `create_event_sync_exclusion` / `delete_event_sync_exclusion` (`mcp-server/tools/event_sync_exclusions.py`). Delete is two-step (`confirm=False` previews, `confirm=True` removes), mirroring other MCP delete tools.

---

## Logos

| Endpoint | Description |
|-|-|
| `GET /api/channels/logos` | List logos (paginated, searchable) |
| `GET /api/channels/logos/{id}` | Get a single logo |
| `POST /api/channels/logos` | Create logo from URL |
| `POST /api/channels/logos/upload` | Upload logo image file |
| `PATCH /api/channels/logos/{id}` | Update logo |
| `DELETE /api/channels/logos/{id}` | Delete logo |

## Streams

| Endpoint | Description |
|-|-|
| `GET /api/streams` | List streams (paginated, searchable, filterable) |
| `POST /api/streams/by-ids` | Get streams by specific IDs |
| `GET /api/stream-groups` | List stream groups with stream counts |

## M3U

| Endpoint | Description |
|-|-|
| `GET /api/m3u/accounts/{id}` | Get M3U account details |
| `GET /api/m3u/accounts/{id}/stream-metadata` | Get stream metadata (tvg-id mappings) |
| `POST /api/m3u/accounts` | Create M3U account |
| `PUT /api/m3u/accounts/{id}` | Update M3U account (full) |
| `PATCH /api/m3u/accounts/{id}` | Partially update M3U account |
| `DELETE /api/m3u/accounts/{id}` | Delete M3U account |
| `POST /api/m3u/upload` | Upload M3U file |
| `POST /api/m3u/refresh` | Refresh all active M3U accounts |
| `POST /api/m3u/refresh/{id}` | Refresh a single M3U account |
| `POST /api/m3u/accounts/{id}/refresh-vod` | Refresh VOD content (XtreamCodes) |
| `GET /api/m3u/accounts/{id}/filters` | List filters for an account |
| `POST /api/m3u/accounts/{id}/filters` | Create filter for an account |
| `PUT /api/m3u/accounts/{id}/filters/{fid}` | Update a filter |
| `DELETE /api/m3u/accounts/{id}/filters/{fid}` | Delete a filter |
| `GET /api/m3u/accounts/{id}/profiles/` | List profiles for an account |
| `POST /api/m3u/accounts/{id}/profiles/` | Create profile for an account |
| `GET /api/m3u/accounts/{id}/profiles/{pid}/` | Get a specific profile |
| `PATCH /api/m3u/accounts/{id}/profiles/{pid}/` | Update a profile |
| `DELETE /api/m3u/accounts/{id}/profiles/{pid}/` | Delete a profile |
| `PATCH /api/m3u/accounts/{id}/group-settings` | Update group settings for an account. Since GH #720 Part B this also performs downstream **channel-profile reconcile** for every edited group that carries a `custom_properties.channel_profile_ids` selection: the group's channels are made members of EXACTLY the selected profiles (subtractive, one bulk write per profile). Best-effort: a reconcile failure NEVER fails the PATCH. Guardrails: an absent/empty selection is a **no-op** (ECM stops managing that group's profiles and leaves memberships unchanged, never "disable everywhere"); a fully-stale selection (all selected profiles deleted) leaves channels untouched; a universe-fetch failure degrades to enable-only. Selection is **enforced globally per channel-group**: on save the selection is PROPAGATED (cascade-written) to every M3U account row carrying that channel-group so it takes effect regardless of which account it was made on. The primary write + sibling cascade are serialized under a per-effective-group lock **within the process** (single-operator assumption). When a TLS request subprocess is running it forwards exactly the lock-participating paths (this save's cascade, the single-account M3U refresh, the Channel Pipeline / Auto-Create run endpoints and their `/api/auto-creation` alias, and every background task run AND cancel) to the main process so main stays the sole writer of those paths; the forward imposes no timeout ceiling (main's request-timeout budget is authoritative, so a long task run is not cut to a false error). Direct channel-profile membership endpoints are NOT part of this forwarding (separate pre-existing concern, bead `nq3ed`). The cascade is a sequence of independent remote PATCHes (not one atomic transaction); an incomplete propagation is surfaced (named accounts) and the every-pass normalize converges non-empty divergence. **CLEAR ordering:** clearing a selection first reads a FRESH account list under the lock (if that read is unavailable, malformed, empty, or omits the account being edited the clear FAILS CLOSED with zero writes; it never clears the authoritative primary on an unverified/incomplete enumeration, which could otherwise strand real siblings that resurrect the selection on the next sweep), then clears the sibling rows and the authoritative primary last. If any sibling clear fails the whole clear ABORTS before the primary is touched (prior selection preserved, no resurrection). A partial clear (some siblings cleared before one failed) reports the TRUTHFUL outcome: the 503 detail names both the cleared and the failed accounts, and the accounts that ACTUALLY changed are journaled so the operator can re-save to complete. A collapse resolves a defensive winner for legacy rows (precedence: `auto_channel_sync` ON, then a row that HAS a selection, then lowest `m3u_account_id`) and flags a residual conflict. Channels set by a Channel Pipeline `assign_channel_profile` rule are excluded until that rule is disabled/deleted (ownership handoff). **Response envelope:** a healthy/field-only save returns **200** with `ecm_profile_apply: [{status, group_id, failed_profile_ids, conflict, error, ...}]` per group (`status` ∈ `no_selection`, `no_channels`, `stale_selection`, `reconciled`, `partial_failure`, `degraded`, `error`) so the UI can warn on an incomplete apply. The two failure classes are distinct by status: a reconcile that fails AFTER the selection was safely written returns **200** with the warning in `ecm_profile_apply` (the save stuck; the sweep retries), whereas a **pre-write safety failure** (the group-settings fetch (lock key) was unavailable, or a CLEAR could not read a valid account list / aborted before the primary write) is NOT written and returns **HTTP 503** with `{detail: "…NOT saved…/…NOT fully cleared… retry"}` (naming affected accounts), so it never reads as success. Non-integer `channel_profile_ids` are rejected with **422**. |
| `POST /api/m3u/accounts/{id}/group-auto-sync-toggle` | Guided-setup toggle of ONE group's `auto_channel_sync` (bead ti939.3.4). Admin-gated; body `{channel_group_id, auto_channel_sync, confirm: true}`: `confirm: true` is REQUIRED (400 otherwise; the toggle is an explicit operator action, never a side effect). Journaled per toggle; snapshot restore does NOT revert Dispatcharr group settings. The journal entry is the recovery breadcrumb. See `docs/event_sync.md`. |
| `GET /api/m3u/accounts/{id}/changes` | Get change history for an account |
| `GET /api/m3u/snapshots` | List M3U snapshots |
| `GET /api/m3u/server-groups` | List server groups |
| `POST /api/m3u/server-groups` | Create server group |
| `PATCH /api/m3u/server-groups/{id}` | Update server group |
| `DELETE /api/m3u/server-groups/{id}` | Delete server group |

## M3U Digest

| Endpoint | Description |
|-|-|
| `GET /api/m3u/changes` | Get M3U change history (paginated, filterable) |
| `GET /api/m3u/changes/summary` | Get change summary for a time period |
| `GET /api/m3u/digest/settings` | Get digest email settings |
| `PUT /api/m3u/digest/settings` | Update digest email settings |
| `POST /api/m3u/digest/test` | Send a test digest email, over the stored SMTP credentials and Discord webhook. Admin-only when auth is enabled; the MCP service key is refused (bead 9kwzp.6). |

**Auth-disabled instances (bead `enhancedchannelmanager-2u4e0`):** `POST /api/m3u/digest/test` is one of twelve connection-test routes that also require an authenticated human admin while `require_auth` is false, on any instance that already holds an operator identity. Only an instance that never created a user reaches them anonymously. See `docs/auth_middleware.md` → "What `require_auth: false` permits".

## EPG

| Endpoint | Description |
|-|-|
| `GET /api/epg/sources` | List EPG sources |
| `GET /api/epg/sources/{id}` | Get EPG source details |
| `POST /api/epg/sources` | Create EPG source (including dummy sources) |
| `PATCH /api/epg/sources/{id}` | Update EPG source |
| `DELETE /api/epg/sources/{id}` | Delete EPG source |
| `POST /api/epg/sources/{id}/refresh` | Refresh EPG source |
| `GET /api/epg/sources/{id}/sd-lineups` | List a Schedules Direct source's active lineups |
| `POST /api/epg/sources/{id}/sd-lineups` | Add a Schedules Direct lineup (`{"lineup": "..."}`) |
| `DELETE /api/epg/sources/{id}/sd-lineups` | Remove a Schedules Direct lineup (`{"lineup": "..."}`) |
| `POST /api/epg/sources/{id}/sd-lineups/search` | Search SD lineups by location (`{"country", "postalcode"}`) |
| `GET /api/epg/programs/{id}/poster` | Proxy a Schedules Direct program poster image |
| `POST /api/epg/import` | Trigger EPG import |
| `GET /api/epg/data` | Search EPG data (paginated) |
| `GET /api/epg/data/{id}` | Get individual EPG data entry |
| `GET /api/epg/grid` | Get EPG program grid for guide view |
| `GET /api/epg/lcn` | Get LCN (Logical Channel Number) for a TVG-ID |
| `POST /api/epg/lcn/batch` | Batch LCN lookup for multiple TVG-IDs |
| `POST /api/epg/migration/preview` | Build a signed, non-mutating XMLTV/Schedules Direct migration preview |
| `POST /api/epg/migration/apply` | Accept a signed preview for asynchronous application (`202`) |
| `GET /api/epg/migration/apply/{batch_id}` | Poll migration progress and per-channel outcomes |

### Guide migration

`POST /api/epg/migration/preview` accepts
`{"target_epg_source_id": 20}` and returns every channel classification, status
counts, and a five-minute signed `preview_token`. Only `ready` rows may be sent
to apply. A row is ready only when its LCN/station identifier resolves to
exactly one target EPG row.

`POST /api/epg/migration/apply` accepts the target source, preview token, and
the exact ready-row identities returned by preview. A valid request returns
`202 Accepted`:

```json
{
  "batch_id": "0123456789abcdef0123456789abcdef",
  "status": "running",
  "total": 2,
  "poll_url": "/api/epg/migration/apply/0123456789abcdef0123456789abcdef"
}
```

`batch_id` is 128 random bits rendered as 32 lowercase hexadecimal characters.
Poll the supplied URL until `status` is `completed` or `failed`. Running and
terminal responses include `processed`, `total`, and a `result` envelope with
the current counters (`mutated`, `updated`, `audit_failed`, `skipped`,
`failed`) plus all per-channel results produced so far. Result statuses are
`updated`, `updated_audit_failed`, `ambiguous_target`, `unsupported_origin`,
`semantic_drift`, `changed_since_preview`, or `failed`.

The ECM dialog polls until a terminal response while it remains open; it does
not impose a client-side wall-clock cutoff. Closing the dialog aborts client
polling but does not cancel the accepted server job. Transient network and
server errors retain the known batch ID and last partial result and are
retried. A not-found poll can indicate a server restart; the dialog preserves
the batch ID and directs the operator to build a fresh preview and reconcile
the affected channels in Dispatcharr.

Only one migration apply may run at a time; another POST receives
`409 Conflict`. Invalid, expired, reordered, or tampered preview identities
also receive `409` and must be previewed again. Job polling state is
process-local, is never pruned while running, and is retained for 30 minutes
measured from terminal completion. Status reads also perform expiry cleanup.
Poll access is bound to the immutable provider-kind and numeric/synthetic ID of
the administrator who accepted the job (renaming a user does not break polling;
the static MCP principal has its stable synthetic ID; auth-disabled mode
intentionally treats operators as equivalent). Authorization is checked at
acceptance and polling, not continuously during execution. Batch IDs must be
exactly 32 lowercase hexadecimal characters. Malformed, expired, unknown, and
foreign-owned IDs all return the same not-found response.

The process-local envelope and active-job marker are lost on restart.
Dispatcharr PATCH and ECM Journal commit are separate operations: cancellation,
restart, an indeterminate HTTP response, or failure between PATCH and Journal
can leave a changed channel without a corresponding Journal row. After any
interruption, do not infer upstream state from the missing job or Journal
alone. Build a fresh preview, verify affected channels directly in Dispatcharr,
and reconcile before retrying. Fatal polls intentionally contain only the
fixed `Guide migration failed.` message; detailed exceptions remain server-side.

Before any mutation, apply rebuilds one bounded source/target snapshot and
requires every signed target to remain the exact sole candidate. It then
refetches each current/target EPG row and channel immediately before PATCH.
Dispatcharr exposes neither a source-mapping revision nor compare-and-swap for
the channel update, so it cannot eliminate a non-migration writer changing
mapping state after the snapshot or the channel between the final GET and
PATCH. This accepted TOCTOU limitation is fail-closed wherever Dispatcharr
provides a revalidation point; rerun Preview after any skipped or uncertain
result.

## Channel Profiles

| Endpoint | Description |
|-|-|
| `GET /api/channel-profiles` | List all channel profiles |
| `POST /api/channel-profiles` | Create channel profile |
| `GET /api/channel-profiles/{id}` | Get channel profile |
| `PATCH /api/channel-profiles/{id}` | Update channel profile |
| `DELETE /api/channel-profiles/{id}` | Delete channel profile |
| `PATCH /api/channel-profiles/{id}/channels/bulk-update` | Bulk enable/disable channels for a profile |
| `PATCH /api/channel-profiles/{id}/channels/{cid}` | Enable/disable a single channel for a profile |

## Stream Profiles

| Endpoint | Description |
|-|-|
| `GET /api/stream-profiles` | List available stream profiles |

## Providers

| Endpoint | Description |
|-|-|
| `GET /api/providers` | List M3U accounts (legacy) |
| `GET /api/providers/group-settings` | Get provider group settings |

## Settings

| Endpoint | Description |
|-|-|
| `GET /api/settings` | Get current settings. Secrets are never returned: API keys, tokens and passwords are reduced to `*_configured` booleans for every caller, and the shared Discord webhook URL plus the Telegram bot token and chat id are additionally withheld (empty string) from any caller not allowed to write them (an ordinary non-admin, or the MCP service key; bead 9ej7f). `discord_configured` / `telegram_configured` still report the integration state. |
| `POST /api/settings` | Update settings. Admin-only fields are gated per field; a non-admin echoing the redacted (empty) notification credentials back keeps the stored values. `public_base_url` is admin-only, validated as a bare `scheme://host[:port]` origin (400 otherwise) and normalized before storage; omitting the field keeps the stored value, an explicit empty string clears it. |
| `POST /api/settings/test` | Test Dispatcharr connection. Admin-only when auth is enabled; the MCP service key is refused (bead i4qrp). |
| `POST /api/settings/test-smtp` | Test SMTP connection. Same gate as `/test`. |
| `POST /api/settings/test-discord` | Test Discord webhook. Same gate as `/test`. |
| `POST /api/settings/test-telegram` | Test Telegram bot. Same gate as `/test`. |
| `POST /api/settings/emby/test-connection` | Test an Emby server with operator-supplied credentials. Same gate as `/test` (bead 9kwzp.7). |
| `POST /api/settings/plex/test-connection` | Test a Plex server with operator-supplied credentials. Same gate as `/test`. |
| `POST /api/settings/jellyfin/test-connection` | Test a Jellyfin server with operator-supplied credentials. Same gate as `/test`. |
| `POST /api/settings/restart-services` | Restart background services. Admin-only when auth is enabled; the MCP service key is still admitted, because this rebuilds the tracker/prober from already-saved settings and reaches no caller-named host (bead 9kwzp.6). |
| `POST /api/settings/reset-stats` | Reset all statistics. Admin-only when auth is enabled; the MCP service key is refused, because the wipe is irreversible and has no rollback (bead 9kwzp.12). |

**Auth-disabled instances (bead `enhancedchannelmanager-2u4e0`):** the seven test routes above (`/test`, `/test-smtp`, `/test-discord`, `/test-telegram`, and the three `*/test-connection` routes) also require an authenticated human admin while `require_auth` is false, on any instance that already holds an operator identity. They reach the network with credentials the instance already stores and report the upstream verdict back. Only an instance that never created a user reaches them anonymously; `/restart-services` and `/reset-stats` are not affected. See `docs/auth_middleware.md` → "What `require_auth: false` permits".

## Event Sync Team Aliases

Operator team-alias dictionary for the Event Sync matcher's team-token layer (bead ti939.4.2): groups of known-equivalent team spellings (`Man Utd == Manchester United == MUFC`) that raise recall on abbreviation-heavy providers without lowering the fuzzy threshold. Stored as a JSON setting (no DB table); consulted on BOTH the team hard-reject and boost paths; strictly monotonic (an alias can never create a conflict). Aliases are corpus-gated by policy. See [`docs/event_sync.md`](event_sync.md) → "Team aliases (operator dictionary)".

| Endpoint | Description |
|-|-|
| `GET /api/event-sync/team-aliases` | Get the alias dictionary: `{groups: [{terms: [...], note}]}`. Empty by default. |
| `PUT /api/event-sync/team-aliases` | Full-replace write. Validates each term against the matcher's own team normalization (≥2 terms per group, no blank/identity-free terms, a term may appear in only one group; ≤200 groups, ≤50 terms/group, ≤100 chars/term). Journals before/after under category `event_sync`. |

## Stream Stats

| Endpoint | Description |
|-|-|
| `GET /api/stream-stats` | Get all stream probe statistics |
| `GET /api/stream-stats/summary` | Get probe statistics summary |
| `GET /api/stream-stats/{id}` | Get probe stats for a specific stream |
| `POST /api/stream-stats/by-ids` | Get probe stats for multiple streams |
| `POST /api/stream-stats/probe/{id}` | Probe a single stream |
| `POST /api/stream-stats/probe/bulk` | Probe multiple streams |
| `POST /api/stream-stats/probe/all` | Probe all streams (background task) |
| `GET /api/stream-stats/probe/progress` | Get probe progress |
| `GET /api/stream-stats/probe/results` | Get results of last probe-all operation |
| `GET /api/stream-stats/probe/history` | Get probe run history |
| `POST /api/stream-stats/probe/cancel` | Cancel running probe |
| `POST /api/stream-stats/probe/reset` | Force reset stuck probe state |
| `POST /api/stream-stats/dismiss` | Dismiss probe failures for streams |
| `GET /api/stream-stats/dismissed` | Get list of dismissed stream IDs |
| `POST /api/stream-stats/clear` | Clear probe stats for specific streams |
| `POST /api/stream-stats/clear-all` | Clear all probe stats |
| `GET /api/stream-stats/struck-out` | List struck-out streams (exceeding failure threshold) |
| `POST /api/stream-stats/struck-out/remove` | Bulk remove struck-out streams from all channels |
| `GET /api/stream-stats/stale?days=7` | List stale streams: not probed by ECM in `days` days (or never), OR flagged `is_stale` by Dispatcharr's own M3U refresh, each tagged with which `reasons` fired |
| `POST /api/stream-stats/compute-sort` | Compute sort scores for streams (resolution, bitrate, framerate, video codec, M3U priority, audio channels) |

## Enhanced Stats

| Endpoint | Description |
|-|-|
| `GET /api/stats/bandwidth` | Get bandwidth summary with in/out breakdown |
| `GET /api/stats/channels` | Get status of all active channels |
| `GET /api/stats/channels/{id}` | Get detailed stats for a channel |
| `GET /api/stats/activity` | Get system activity events |
| `POST /api/stats/channels/{id}/stop` | Stop a channel |
| `POST /api/stats/channels/{id}/stop-client` | Stop a specific client connection |
| `GET /api/stats/top-watched` | Get top watched channels |
| `GET /api/stats/unique-viewers` | Get unique viewer summary for period |
| `GET /api/stats/channel-bandwidth` | Get per-channel bandwidth stats |
| `GET /api/stats/unique-viewers-by-channel` | Get unique viewers per channel |
| `GET /api/stats/watch-history` | Get watch history log (paginated, filterable by channel/IP/days, includes user attribution) |

**Per-channel attribution fields**:

Each channel object, and each entry in `channel.clients[]`, carries
per-source attribution fields when an integration is enabled and the
session matches. Attribution is networking-agnostic (per-channel set
reconciliation, not an IP join); see
[Architecture § User Attribution Pipeline](architecture.md#user-attribution-pipeline)
for the model.

| Field | Type | Description |
|-------|------|-------------|
| `emby_viewers` | `[{user_id, user_name}] \| null` | Emby users on this channel/client. At channel level: the full distinct set. At client level: that connection's assigned user(s), either one for a 1:1 match, or the full set for a server-proxy connection carrying multiple viewers. Null if Emby disabled or no match. |
| `plex_viewers` | `[{user_id, user_name}] \| null` | Plex users on this channel/client (same shape as `emby_viewers`). `user_id` is `null` for Plex: `/status/sessions` exposes no stable id. Null if Plex disabled or no match. |
| `jellyfin_viewers` | `[{user_id, user_name}] \| null` | Jellyfin users on this channel/client (same shape as `emby_viewers`). Null if Jellyfin disabled or no match. |
| `emby_user_name` | `string \| null` | Singular display name. Usually the assigned (or most-recent, at channel level) Emby user's name. For a connection in a genuinely-ambiguous group it is instead the Option-B rollup label `"N viewers: a, b, …"` (and `emby_viewers` is left empty for that client so the UI renders the label verbatim rather than confident names). Provided for back-compat; prefer `emby_viewers`. |
| `plex_user_name` | `string \| null` | Singular Plex display name (same semantics as `emby_user_name`, including the Option-B rollup label). Prefer `plex_viewers`. |
| `jellyfin_user_name` | `string \| null` | Singular Jellyfin display name (same semantics, including the Option-B rollup label). Prefer `jellyfin_viewers`. |
| `attribution_source` | `'emby' \| 'plex' \| 'jellyfin' \| 'dispatcharr' \| null` | The source that wins display precedence (Emby > Plex > Jellyfin > Dispatcharr). |

Operator setup: see [`docs/user_guide/integrations/index.md`](user_guide/integrations/index.md).

## Popularity

| Endpoint | Description |
|-|-|
| `GET /api/stats/popularity/rankings` | Get channel popularity rankings (paginated) |
| `GET /api/stats/popularity/channel/{id}` | Get popularity score for specific channel |
| `GET /api/stats/popularity/trending` | Get trending channels (up or down) |
| `POST /api/stats/popularity/calculate` | Trigger popularity score calculation |

## Normalization

| Endpoint | Description |
|-|-|
| `GET /api/normalization/rules` | Get all rules organized by group |
| `GET /api/normalization/rules/{id}` | Get a specific rule |
| `POST /api/normalization/rules` | Create rule |
| `PATCH /api/normalization/rules/{id}` | Update rule |
| `DELETE /api/normalization/rules/{id}` | Delete rule |
| `GET /api/normalization/groups` | List rule groups |
| `POST /api/normalization/groups` | Create rule group |
| `GET /api/normalization/groups/{id}` | Get rule group |
| `PATCH /api/normalization/groups/{id}` | Update rule group |
| `DELETE /api/normalization/groups/{id}` | Delete rule group and all its rules |
| `POST /api/normalization/groups/reorder` | Reorder rule groups |
| `POST /api/normalization/groups/{id}/rules/reorder` | Reorder rules within a group |
| `POST /api/normalization/test` | Test a rule against sample text |
| `POST /api/normalization/test-batch` | Test all enabled rules against multiple texts |
| `POST /api/normalization/normalize` | Normalize text using all enabled rules |
| `POST /api/normalization/apply-to-channels` | Apply enabled rules to existing channels: admin-gated, rate-limited 5/minute, `dry_run=true` by default (see note below) |
| `GET /api/normalization/rule-stats` | Get stream match statistics per rule |
| `GET /api/normalization/lint-findings` | Read-only view of saved normalization rules that fail the current write-time linter (bd-eio04.7) |
| `GET /api/normalization/export` | Export normalization rules |
| `POST /api/normalization/import` | Import normalization rules |
| `GET /api/normalization/migration/status` | Get migration status |
| `POST /api/normalization/migration/run` | Run demo rules migration |

`POST /api/normalization/apply-to-channels` computes a diff of "what would change if we applied the current rule set to every existing channel" and, in execute mode, renames or merges per-row according to the caller-supplied `actions[]` array. Guarantees:

- **Admin-gated**: protected by `RequireAdminIfEnabled`; non-admin callers see HTTP 403 when auth is enabled.
- **Rate-limited**: 5 requests/minute per remote address (slowapi) to prevent runaway bulk-apply loops.
- **Dry-run by default**: `dry_run=true` returns `{dry_run, diffs, channels_with_changes}` without mutating. `dry_run=false` requires an explicit `actions[]` body; unspecified channels default to `skip`.
- **Single-flight execute**: only one concurrent execute run is allowed; a second caller sees HTTP 409.
- **Journaled**: every rename and merge writes a journal entry with the `rule_set_hash` captured at execute time for audit and undo.

See [`docs/normalization.md` §Re-normalize existing channels](normalization.md#re-normalize-existing-channels) for the operator workflow.

## Tags

| Endpoint | Description |
|-|-|
| `GET /api/tags/groups` | List all tag groups with counts |
| `POST /api/tags/groups` | Create tag group |
| `GET /api/tags/groups/{id}` | Get tag group with all tags |
| `PATCH /api/tags/groups/{id}` | Update tag group |
| `DELETE /api/tags/groups/{id}` | Delete tag group and all tags |
| `POST /api/tags/groups/{id}/tags` | Add tags to a group |
| `PATCH /api/tags/groups/{gid}/tags/{tid}` | Update a tag |
| `DELETE /api/tags/groups/{gid}/tags/{tid}` | Delete a tag |
| `POST /api/tags/test` | Test text against a tag group |
| `GET /api/tags/export` | Export all tag groups and tags |
| `POST /api/tags/import` | Import tag groups and tags |

## Stream Preview

| Endpoint | Description |
|-|-|
| `GET /api/stream-preview/{id}` | Preview a stream (proxy with optional transcoding) |
| `GET /api/channel-preview/{id}` | Preview a channel (proxy with optional transcoding) |

## Journal

| Endpoint | Description |
|-|-|
| `GET /api/journal` | Get journal entries (paginated, filterable) |
| `GET /api/journal/stats` | Get journal statistics |
| `DELETE /api/journal/purge` | Purge old journal entries |

`GET /api/journal` accepts `page` (>= 1), `page_size` (1-250), `category`, `action_type`, `date_from`, `date_to`, `search`, `user_initiated`, and `batch_id`. Out-of-range `page`/`page_size` values return `422` rather than being silently clamped or passed through. Each result row carries `batch_id` in the response body: bulk operations (e.g. `POST /api/channel-pipeline/rules/bulk-update`, channel renumber) write **N per-entity rows sharing one `batch_id`** so callers can stitch a forensic view of a single batch. The `batch_id` query parameter (added in bd-s4sph) is an exact-match filter that hits `idx_journal_batch_id` directly. Pass the 8-character `batch_id` returned by a bulk handler to retrieve only that batch's rows. An unknown `batch_id` returns an empty result set (not `422`); the parameter is purely a filter. See the Channel Pipeline `bulk-update` notes above for a worked example.

Two `action_type` values under the `channel` category name outcomes that used to be indistinguishable from their successful counterparts, and both are worth filtering on: **`merge_unapplied`** for a pending merge the operator accepted that ECM could not apply to Dispatcharr (see [`POST /api/channel-merges/{id}/accept`](#post-apichannel-mergesidaccept)), and **`bulk_merge_incomplete`** for a bulk-merge group whose source channels are not all gone (see [`POST /api/channels/bulk-merge`](#post-apichannelsbulk-merge-sources_failed-and-the-incomplete-merge-action-type)). Both are in the ECM UI's Action dropdown, as **Merge Not Applied** and **Bulk Merge Incomplete**, and both are filterable through this endpoint.

## Notifications

| Endpoint | Description |
|-|-|
| `GET /api/notifications` | Get notifications (paginated, filterable by read status) |
| `POST /api/notifications` | Create a notification |
| `PATCH /api/notifications/{id}` | Update notification (mark as read) |
| `DELETE /api/notifications/{id}` | Delete notification |
| `PATCH /api/notifications/mark-all-read` | Mark all notifications as read |
| `DELETE /api/notifications` | Clear notifications (read only or all) |
| `DELETE /api/notifications/by-source` | Delete notifications by source |

## Alert Methods

| Endpoint | Description |
|-|-|
| `GET /api/alert-methods` | List all alert methods. Admin-only when auth is enabled; the MCP service key is **admitted**, so the shipped `list_alert_methods` tool keeps working. **The response `config` is MASKED**: `password`, `bot_token`, `webhook_url` and `api_key` come back as `********` for every caller, so no credential value is disclosed. Non-credential config keys are returned as stored, which for Telegram includes the destination `chat_id` and for SMTP the recipient list. Until build 0096 this handler returned `config` verbatim (beads 9kwzp.10, 9kwzp.13). |
| `GET /api/alert-methods/types` | Get available alert method types. Admin-only when auth is enabled; the MCP service key is admitted, since the catalogue is static and holds no install data (bead 9kwzp.10). |
| `POST /api/alert-methods` | Create alert method. Admin-only when auth is enabled; the MCP service key is refused, because an alert method's `config` carries the webhook URL, bot token and SMTP password (bead 9kwzp.10). |
| `GET /api/alert-methods/{id}` | Get alert method details. **The response `config` is MASKED** on the same terms as the list route. Admin-only when auth is enabled; the MCP service key is refused, because no MCP tool calls this route. Note the refusal withholds nothing, since `GET /api/alert-methods` returns the same masked fields for every method (beads 9kwzp.10, 9kwzp.13). |
| `PATCH /api/alert-methods/{id}` | Update alert method. Admin-only when auth is enabled; the MCP service key is refused, because an alert method's `config` carries the webhook URL, bot token and SMTP password (bead 9kwzp.10). |
| `DELETE /api/alert-methods/{id}` | Delete alert method. Admin-only when auth is enabled; the MCP service key is refused, because an alert method's `config` carries the webhook URL, bot token and SMTP password (bead 9kwzp.10). |
| `POST /api/alert-methods/{id}/test` | Send test notification, using the method's stored credentials. Admin-only when auth is enabled; the MCP service key is refused (bead 9kwzp.6). Also requires an authenticated human admin while `require_auth` is false, on any instance that already holds an operator identity (bead 2u4e0). |

An **alert method** is one configured channel (Discord webhook, Telegram bot, SMTP recipient list) that ECM uses to notify operators about scheduled-task results, probe failures, M3U/EPG refresh outcomes, and other system events. Each method carries its own per-type `config` blob, four per-severity opt-in flags (`notify_info`, `notify_success`, `notify_warning`, `notify_error`), and an optional granular `alert_sources` filter for per-EPG-source / per-M3U-account routing. **`method_type` uniqueness is NOT enforced**: multiple SMTP methods (or multiple Discord webhooks) can coexist, each with its own recipient set, severity opt-ins, and source filter; this is intentional so operators can route different alert categories to different recipients without collapsing them onto one row.

`GET /api/alert-methods` returns an array of alert-method records. Each record carries:

```json
{
  "id": 7,
  "name": "Ops Email",
  "method_type": "smtp",
  "enabled": true,
  "config": { "to_emails": ["alice@example.com", "bob@example.com"] },
  "notify_info": false,
  "notify_success": true,
  "notify_warning": true,
  "notify_error": true,
  "alert_sources": null,
  "last_sent_at": "2026-04-25T14:30:12Z",
  "created_at": "2026-04-01T10:00:00Z",
  "updated_at": "2026-04-25T14:30:12Z"
}
```

`GET /api/alert-methods/{id}` returns one record of the same shape.

`config` shape varies by `method_type`, as **stored**:
- **`discord`**: `{ "webhook_url": "https://discord.com/api/webhooks/..." }`
- **`telegram`**: `{ "bot_token": "...", "chat_id": "..." }`
- **`smtp`**: `{ "to_emails": ["alice@example.com", "bob@example.com"] }` (recipient list only; shared SMTP server settings live under `/api/settings`, see `smtp_*` fields)

**Reads mask the credential keys (bead 9kwzp.13).** Both read routes serialize through `AlertMethod.to_dict(include_sensitive=False)`, which replaces `password`, `bot_token`, `webhook_url` and `api_key` with the literal `********` (or `null` when the stored value is empty). So a Discord method reads back as `{ "webhook_url": "********" }` and a Telegram method as `{ "bot_token": "********", "chat_id": "-1001234567890" }`. There is **no** API path that returns the unmasked values: no query parameter, header, or caller tier reaches `include_sensitive=True`. Keys outside that set, including the Telegram `chat_id` and the SMTP `to_emails`, are returned as stored. **Do not echo a read response back into a write**: `********` is not treated as a sentinel on the write side, so sending it in a `config` would overwrite the live credential with that literal. Send only the fields you intend to change.

`alert_sources` is either `null` (send for every event) or a structured filter object documented under the per-section keys `epg_refresh`, `m3u_refresh`, and `probe_failures` (each with `enabled`, `filter_mode` ∈ `{all, only_selected, all_except}`, and a per-section ID list or `min_failures` threshold).

`POST /api/alert-methods` accepts:

```json
{
  "name": "Ops Email",
  "method_type": "smtp",
  "config": { "to_emails": ["alice@example.com", "bob@example.com"] },
  "enabled": true,
  "notify_info": false,
  "notify_success": true,
  "notify_warning": true,
  "notify_error": true,
  "alert_sources": null
}
```

`name`, `method_type`, and `config` are required; the four `notify_*` flags and `enabled` default per the table above; `alert_sources` defaults to `null` (send everything). The handler rejects unknown `method_type` values with `400`. Per-type `config` is run through that type's `validate_config()`: for SMTP, every entry in `to_emails` must pass an HTML5-style email regex and is rejected if it contains any of `\r \n < > :` (defense-in-depth against header injection at the SMTP sink, bd-6e8gv). The response is the abbreviated form `{ id, name, method_type, enabled }`; round-trip via `GET /api/alert-methods/{id}` for the full record.

**SMTP `to_emails` shape (bd-9vz32):** the canonical write shape is `list[str]`. The route accepts either `list[str]` or a legacy comma-joined `str` on POST/PATCH and normalizes string input to a list **before** persistence, so reads from rows written after bd-9vz32 always return `list[str]`. This is a **write-strict / read-tolerant** contract: pre-bd-9vz32 rows that were stored as a `str` continue to load (the SMTP runtime path coerces both shapes via `_coerce_to_emails_to_list`), so no Alembic migration is needed for the JSON-blob field. Writers should send `list[str]`; readers should expect `list[str]` for any row created or last-updated after bd-9vz32 and tolerate `str` for older rows.

`PATCH /api/alert-methods/{id}` is a partial update: every field on the body is `Optional`, and only fields present on the wire are touched. The common shape since PR #163 is **config-only** (e.g. `{"config": {"to_emails": [...]}}`), used by the Settings → Email Alerts panel to push recipient changes without re-sending the unchanged severity flags. The handler validates the same per-type `validate_config()` and applies the same SMTP `to_emails` canonicalization on PATCH as on POST. `404` if the method doesn't exist; `200` with `{"success": true}` on success.

**`config` is REPLACED, not merged.** The partial-update semantics are per top-level field: sending `config` overwrites the whole stored blob with what you sent, so any key you omit is dropped. The `validate_config()` call is what keeps this honest, since a `config` missing that type's required fields is rejected with `400` rather than persisted; a Discord method cannot silently lose its `webhook_url` this way. The current in-tree callers are unaffected: the only config-writing UI path is the SMTP recipient save, and `to_emails` is the entire config an SMTP method has. Treat `config` as a whole-object write when adding a caller for a type with more than one key.

`DELETE /api/alert-methods/{id}` removes the row and unloads the method from the in-memory `AlertMethodManager`. `404` if the method doesn't exist; `200` with `{"success": true}` on success. Deletion is unconditional: alerts in flight at deletion time are not buffered or re-routed.

`POST /api/alert-methods/{id}/test` invokes the method's `test_connection()` (Discord: posts a test webhook payload; Telegram: sends a test message to the configured chat; SMTP: sends a test email through the shared SMTP settings to the configured `to_emails`). Returns `{"success": <bool>, "message": <str>}` describing the outcome. `404` if the method doesn't exist; `200` with `success: false` if the method exists but the test failed (network error, bad credentials, SMTP not configured, etc.). Failed tests are **not** modeled as `5xx`.

`GET /api/alert-methods/types` returns the registry of available method types with their required and optional config fields:

```json
[
  { "type": "discord", "display_name": "Discord", "required_fields": ["webhook_url"], "optional_fields": {} },
  { "type": "telegram", "display_name": "Telegram", "required_fields": ["bot_token", "chat_id"], "optional_fields": {} },
  { "type": "smtp", "display_name": "Email", "required_fields": ["to_emails"], "optional_fields": {} }
]
```

The frontend uses this to drive the "add alert method" form so new method types appear automatically once registered server-side.

## Scheduled Tasks

| Endpoint | Description |
|-|-|
| `GET /api/tasks` | List all tasks with status |
| `GET /api/tasks/{id}` | Get task details with schedules |
| `PATCH /api/tasks/{id}` | Update task configuration |
| `POST /api/tasks/{id}/run` | Run task immediately |
| `POST /api/tasks/{id}/cancel` | Cancel running task |
| `GET /api/tasks/{id}/history` | Get task execution history |
| `GET /api/tasks/engine/status` | Get task engine status |
| `GET /api/tasks/history/all` | Get execution history for all tasks |
| `GET /api/tasks/{id}/parameter-schema` | Get parameter schema for a task type |
| `GET /api/tasks/parameter-schemas` | Get all task parameter schemas |
| `GET /api/tasks/{id}/schedules` | Get task schedules |
| `POST /api/tasks/{id}/schedules` | Add schedule to task |
| `PATCH /api/tasks/{id}/schedules/{sid}` | Update schedule |
| `DELETE /api/tasks/{id}/schedules/{sid}` | Delete schedule |

Each row from `GET /api/tasks/{id}/history` carries a `status` from a closed
set: `running`, `completed`, `completed_with_warnings`, `failed`, `cancelled`.
`completed_with_warnings` is a run that reached the end and left real, kept
state without being clean — some items failed, or the task reported itself
degraded (a DBAS restore that applied every row and left a channel with no
playable stream). **It is not a failure**, its `success` field is `true`, and
the run's notification for the same event is a `warning`. A consumer that
branches on `status` should treat it alongside `completed`; rows written before
build `0.18.1-0036` only ever carry `completed` or `failed`.

`GET /api/tasks/{id}/parameter-schema` returns two lists. `parameters` are
schedule-configurable — the schedule editor renders them and they are persisted
with the schedule. `run_parameters`, when present, are ad-hoc parameters the task
honours **only** inside the `parameters` object of `POST /api/tasks/{id}/run`,
and are never persisted to a schedule. `dbas_backup` declares its encryption
parameters (`passphrase`, `include_credentials`, `acknowledge_unrecoverable`)
this way: a passphrase applies to one manual run and there is nowhere safe to
keep it at rest for an unattended one. Sent at the top level of the run request
instead of inside `parameters`, they are ignored and a plain artifact is
produced.

## Channel Pipeline

> **Deprecated alias:** every endpoint below is also reachable at the old `/api/auto-creation/...` prefix. The alias forwards to the same handler and continues to work, but is hidden from the OpenAPI schema and should not be used in new integrations. Use the canonical `/api/channel-pipeline/...` paths shown here.

| Endpoint | Description |
|-|-|
| `GET /api/channel-pipeline/rules` | List all rules sorted by priority |
| `GET /api/channel-pipeline/rules/{id}` | Get rule details |
| `POST /api/channel-pipeline/rules` | Create rule |
| `PUT /api/channel-pipeline/rules/{id}` | Update rule |
| `DELETE /api/channel-pipeline/rules/{id}` | Delete rule |
| `POST /api/channel-pipeline/rules/bulk-update` | Apply the same scalar field changes to multiple rules; rejects `conditions`/`actions` (see notes below) |
| `POST /api/channel-pipeline/rules/reorder` | Reorder rules by priority |
| `POST /api/channel-pipeline/rules/{id}/toggle` | Toggle rule enabled state |
| `POST /api/channel-pipeline/rules/{id}/duplicate` | Duplicate a rule |
| `POST /api/channel-pipeline/rules/{id}/run` | Run a single rule (supports dry_run) |
| `POST /api/channel-pipeline/run` | Run the full pipeline (execute or dry_run) |
| `GET /api/channel-pipeline/executions` | Get execution history (paginated) |
| `GET /api/channel-pipeline/executions/{id}` | Get execution details (optional log/entities) |
| `POST /api/channel-pipeline/executions/{id}/rollback` | Rollback an execution. With a pre-run snapshot it requires `confirm=true` (409 otherwise); once confirmed, an event_sync attach run whose journal fully covers its attaches is reverted SURGICALLY (only the run-added stream ids removed, post-run Dispatcharr churn preserved; response carries `surgical_unmerge: true`), otherwise it delegates to the full snapshot restore |
| `POST /api/channel-pipeline/validate` | Validate a rule definition |
| `GET /api/channel-pipeline/export/yaml` | Export all rules as YAML |
| `POST /api/channel-pipeline/import/yaml` | Import rules from YAML |
| `GET /api/channel-pipeline/schema/conditions` | Get available condition types |
| `GET /api/channel-pipeline/schema/actions` | Get available action types |
| `GET /api/channel-pipeline/schema/template-variables` | Get available template variables |
| `GET /api/channel-pipeline/lint-findings` | Read-only view of saved Channel Pipeline rules that fail the current write-time linter (bd-eio04.7) |
| `POST /api/channel-pipeline/rules/analyze` | Run the advisory rule analyzer over the rules currently in the DB; returns warnings only (saves are never blocked) |
| `POST /api/channel-pipeline/rules/analyze/from-bundle` | Run the analyzer over `rules.yaml` inside an uploaded debug-bundle `tar.gz`; never touches the DB, so it is safe for support diagnosis of any user's bundle. See `docs/channel_pipeline_rule_analyzer.md` |
| `POST /api/channel-pipeline/debug-bundle` | Start a diagnostic-bundle build; returns `{job_id, status: "running"}` immediately and dispatches a supervised background task |
| `GET /api/channel-pipeline/debug-bundle/{job_id}` | Poll a bundle build: JSON status while running, JSON `{status: "failed", error}` on failure, or the `tar.gz` (`application/gzip`) attachment when ready (obfuscated channels, rules, normalization rules, streams, probe stats, settings, task schedules, logs). Job is evicted on successful read; abandoned jobs pruned after 30 min |
| `GET /api/channel-pipeline/fuzzy-preview` | Paginated, write-free scored fuzzy match preview across given channel groups (bead jnzst, v0.17.3-0006). Admin-gated. See notes below. |
| `POST /api/channel-pipeline/event-sync-preview` | Event Sync dry-run: match secondary-group streams against live master channels with ZERO writes (bead ti939.1.4). Admin-gated. See notes below and `docs/event_sync.md`. |

---

### `GET /api/channel-pipeline/fuzzy-preview`

Returns scored `(stream, channel)` pairs for the given channel groups using the same backend scoring core and admission policy used by `merge_streams` rules with `loose_name_match + min_score`. Zero writes: inspection only.

**Authentication:** `RequireAdminIfEnabled` (admin token required when auth is enabled).

**Query parameters:**

| Parameter | Type | Required | Default | Description |
|-|-|-|-|-|
| `group_ids` | list of integers | Yes | none | Channel-group IDs to scope the preview to. Non-empty; no duplicates; no negatives; max 25 groups. An empty list is rejected (`400`). |
| `min_score` | float [0.0–1.0] | Yes | none | Minimum score to include a triple. May be below the `CONFIDENCE_FLOOR` (0.60). The preview deliberately exposes sub-floor scores for inspection. An M1 callsign `conflict` is never returned regardless of `min_score`. |
| `allow_no_callsign` | boolean | No | `false` | Q1 opt-in. When `true`, a no-callsign (`"absent"`) pair is admissible at score ≥ 0.90 (`NO_CALLSIGN_FLOOR`). Default `false` requires a parseable callsign on both sides. |
| `page` | integer ≥ 1 | No | `1` | Page number. |
| `page_size` | integer 1–200 | No | `50` | Results per page. |

**DoS ceilings:** the N×M scoring pass is bounded. The endpoint processes at most 2,000 streams and 2,000 channels total across all requested groups. When a ceiling is hit the response includes `"truncated": true`.

**Response: `200 OK`**

```json
{
  "triples": [
    {
      "stream_id": 1042,
      "stream_name": "WI | WBAY CBS Green Bay HD",
      "channel_id": "a1b2c3d4-e5f6-...",
      "channel_name": "WBAY",
      "score": 0.9412,
      "callsign_verdict": "match",
      "signal": "fuzzy-with-callsign"
    }
  ],
  "total": 47,
  "page": 1,
  "page_size": 50,
  "total_pages": 1,
  "min_score": 0.7,
  "truncated": false
}
```

Each `triples` entry is a `ScoredTriple`:

| Field | Type | Description |
|-|-|-|
| `stream_id` | integer | ECM stream ID |
| `stream_name` | string | Raw stream name (not the LOCALS-cleaned form) |
| `channel_id` | string | Dispatcharr channel UUID |
| `channel_name` | string | Raw channel name (not the cleaned form) |
| `score` | float | Normalized score in [0.0, 1.0], rounded to 4 decimal places |
| `callsign_verdict` | string | `"match"`: both sides parsed a callsign and they agree; `"absent"`: at least one side had no parseable callsign |
| `signal` | string | Which scoring rung produced the score: `"callsign-exact"`, `"tvg_id-override"`, `"fuzzy-with-callsign"`, or `"fuzzy-no-callsign-floor"` |

Triples are sorted highest score first; ties break on `stream_id` then `channel_id` (deterministic).

**Admission policy.** The endpoint applies the shared `is_admissible` policy from `services.dedup_matcher`:

- `"conflict"` verdict (M1 callsign hard-reject): never returned, even at `min_score == 0`.
- `"absent"` verdict: returned only when `allow_no_callsign=true` and `score >= 0.90`.
- `"match"` verdict: returned when `score >= min_score`.

This is the same policy the `merge_streams` rule executor applies, so the preview shows exactly what a rule would do.

**Example:**

```bash
curl -X GET \
  "http://localhost:6100/api/channel-pipeline/fuzzy-preview?group_ids=14&group_ids=22&min_score=0.7&allow_no_callsign=false&page=1&page_size=50" \
  -H "Authorization: Bearer TOKEN"
```

---

### `POST /api/channel-pipeline/event-sync-preview`

Event Sync (epic ti939) dry-run: parses and scores every secondary-group stream against the master group's live channels using the exact resolver the attach path uses (`backend/services/event_sync_resolver.py` → `backend/services/event_sync_matcher.py`), so preview scoring and attach scoring cannot diverge. **Zero writes**: no merges, no channel mutations, and Dispatcharr group settings are never toggled. Feature guide: `docs/event_sync.md`.

**Authentication:** `RequireAdminIfEnabled` (admin token required when auth is enabled).

**Request body: exactly one of:**

| Field | Type | Meaning |
|-|-|-|
| `rule_id` | integer | Preview a saved event_sync rule (`404` if missing, `400` if the rule has no `event_sync_config`). |
| `event_sync_config` | object | Preview an inline config before saving (validated by the same `validate_event_sync_config` rail set as rule save; validation errors return `400` with teaching messages). |

**Response: `200 OK`.** A pre-flight failure does NOT fail the preview; the operator must see the misconfiguration alongside the match results.

| Key | Contents |
|-|-|
| `preflight` | `{ok, failures[]}` from the read-only group-settings check (master auto-sync ON, secondaries OFF, groups exist). |
| `summary` | `secondary_streams`, `would_attach`, `ambiguous_skipped`, `unmatched`, `parse_failed` (the four dispositions sum to `secondary_streams` and reconcile exactly with `streams`), plus `master_channels` / `master_channels_unparsed`, plus the ti939.3.2 review-queue context: `would_attach_via_review` (subset of `would_attach` reached via a prior review accept) and `candidates_pending_review` (rendered candidate pairings currently awaiting review). |
| `streams` | One row per secondary stream: raw name, provider, group, parsed title + start, disposition, `ambiguous_reason` (`contested_top_candidates`, the ti939.2.1 contested rail, or `top_candidate_ambiguous_band`; `null` otherwise), `attach_source` (`"threshold"` \| `"review_queue"` when the disposition is `would_attach`, else `null`, ti939.3.2), `would_attach_master` (name + current channel id, re-resolved this call), and up to 10 scored candidates (master name/id, parsed title/start, score, band, team-token verdict, time delta, machine-readable reject reason, and `review_status`: `pending`/`accepted`/`rejected`/`null` queue marker for that exact pairing, populated only when previewing a saved rule). |
| `unmatched_streams` | Streams with no master in the time window (the master-as-ceiling visibility hedge). On promotion-enabled configs (bead ti939.4.1) each row is annotated with `would_promote`, `promote_action` (`create` \| `attach_existing`), `promote_channel_name`, and `promote_capped`. |
| `parse_failures` | Failures grouped by `(group, reason)` with counts and sample names. A silently broken pattern is loud here. |
| `unparsed_master_channels` | Master channel names with no complete parsed identity (they can never be attach targets). |
| `truncated` | `true` when the fetch ceilings (2,000 streams / 2,000 channels) were hit. |
| `promotion` | **Present ONLY when the config carries `promote_unmatched: true`** (bead ti939.4.1, absent otherwise, and `summary` then also carries `would_promote` / `would_promote_streams`): the promotion plan `{enabled, target_group_id, would_promote, would_promote_streams, would_create, would_attach_existing, cap, capped, cap_overage, units[]}` where each unit is `{channel_name, action, event_key, dateless, existing_channel_id, streams[]}`. Computed by the SAME planner a live run executes (`services/event_sync_promote.py`), so preview equals run on unchanged data. See `docs/event_sync.md` → "Promoting unmatched events". |

**Example:**

```bash
curl -X POST "http://localhost:6100/api/channel-pipeline/event-sync-preview" \
  -H "Authorization: Bearer TOKEN" -H "Content-Type: application/json" \
  -d '{"event_sync_config": {"master_group_id": 12, "secondary_group_ids": [34, 56]}}'
```

MCP mirror: the `preview_event_sync` tool (`mcp-server/tools/channel_pipeline.py`) wraps this endpoint for headless use.

---

`POST /api/channel-pipeline/rules/bulk-update` applies the same partial update to every rule in `rule_ids` in a single transaction. Send only the fields you want to change; omitted fields are left as-is per rule.

**Request body:**

```json
{
  "rule_ids": [12, 14, 17],
  "enabled": true,
  "priority": 5,
  "merge_streams_remove_non_matching": true
}
```

- `rule_ids` (required): `1..500` distinct rule IDs. Empty list, missing list, or duplicates return `400`.
- Scalar fields accepted (any subset): `name`, `description`, `enabled`, `priority`, `m3u_account_id`, `target_group_id`, `run_on_refresh`, `stop_on_first_match`, `sort_field`, `sort_order`, `probe_on_sort`, `sort_regex`, `stream_sort_field`, `stream_sort_order`, `quality_tie_break_order`, `quality_m3u_tie_break_enabled`, `normalization_group_ids`, `skip_struck_streams`, `orphan_action`, `match_scope_target_group`.
- `merge_streams_remove_non_matching` (bulk-only convenience field): when set, every `merge_streams` action on every targeted rule is rewritten with this `remove_non_matching` flag. Rules with no `merge_streams` action are unaffected.
- **Rejected fields (`422 Unprocessable Entity`):** `conditions`, `actions`. Per-rule logic edits must go through `PUT /api/channel-pipeline/rules/{id}` so silent payload drops can't lose intent at scale (bd-gjoe5). The error message names the offending field.
- At least one mutating field is required alongside `rule_ids`; otherwise `400 "No fields to update"`.
- If any `rule_ids` entry doesn't exist, the entire batch aborts with `404 "Rules not found: [...]"` and no rows are written.
- `sort_regex` is run through the Channel Pipeline regex linter before any DB work (bd-eio04.7); a failing pattern returns `400` with the linter findings.

**Response: `200 OK`**

```json
{
  "rules": [
    { "id": 12, "name": "...", "enabled": true, "priority": 5, "...": "..." },
    { "id": 14, "name": "...", "enabled": true, "priority": 5, "...": "..." },
    { "id": 17, "name": "...", "enabled": true, "priority": 5, "...": "..." }
  ],
  "updated_count": 3
}
```

`rules` is the full post-update `to_dict()` for every rule in `rule_ids` (in input order), built directly from the in-memory ORM instances after `commit()`: no per-rule round-trip. `updated_count` always equals `len(rule_ids)` on success, including rules where the requested values matched the current state (no-op rules are still returned but do not emit a journal entry; see below).

**Performance contract (bd-bh1hh):** the handler issues a single `SELECT ... WHERE id IN (rule_ids)` rather than N per-id queries, and skips per-rule `session.refresh()` after commit because the affected scalar columns have no DB-side defaults or triggers. At `max_length=500` this collapses what was previously ~1000 round trips into 2 (1 SELECT + 1 commit).

**Audit trail / `batch_id` correlation contract (bd-91mcq):** every bulk-update writes **N per-entity journal rows**, one row per rule whose state actually changed, all sharing a single 8-character `batch_id` (UUID4 prefix). Rules where no scalar column changed and `merge_streams_remove_non_matching` was either omitted or already at the requested value are skipped (no-op rules emit no journal row). Each row uses `category="auto_creation"`, `action_type="bulk_update"`, and carries the per-rule before/after diff in `before_value`/`after_value`.

To reconstruct one batch:

- **Preferred:** call `GET /api/journal?batch_id=<id>` (added in bd-s4sph). The handler applies an exact-match filter against `JournalEntry.batch_id`, hitting `idx_journal_batch_id` (added in bd-dmu8w) for an indexed lookup. The response is the standard paginated journal payload: every row will carry the same `batch_id`. An unknown `batch_id` returns an empty result set (not `422`); the parameter is purely a filter.
- For ad-hoc forensic queries directly against the database, the same index is reachable from SQL:
  ```sql
  SELECT id, timestamp, entity_id, entity_name, before_value, after_value
  FROM journal_entries
  WHERE batch_id = '1a2b3c4d'
  ORDER BY timestamp;
  ```
- Every journal row returned by `GET /api/journal` already includes `batch_id` in its body, so client-side grouping by `batch_id` from a broader query is also supported (pagination caveats apply on large windows).
- The `search` parameter does an `ILIKE %term%` on `entity_name` and `description` and can complement `batch_id` (e.g., narrow a batch to rules whose name matches a substring). The two filters compose with `AND` semantics.

**Normalization interaction:** `normalization_group_ids` is an accepted scalar field, so bulk-update can reassign normalization groups across many rules in one call. The list is stored as-is (deduplicated and sorted): IDs are **not** verified against `NormalizationRuleGroup` at write time, matching the behavior of `PUT /api/channel-pipeline/rules/{id}`. See [`docs/normalization.md`](normalization.md) for the full normalization model and how groups feed the Channel Pipeline.

## Cache

| Endpoint | Description |
|-|-|
| `POST /api/cache/invalidate` | Invalidate cached data (optional prefix filter) |
| `GET /api/cache/stats` | Get cache statistics |

## TLS

| Endpoint | Description |
|-|-|
| `GET /api/tls/status` | Get TLS configuration status |
| `GET /api/tls/settings` | Get TLS settings |
| `POST /api/tls/configure` | Configure TLS settings |
| `POST /api/tls/request-cert` | Request Let's Encrypt certificate (DNS-01 challenge) |
| `POST /api/tls/complete-challenge` | Complete pending DNS challenge |
| `POST /api/tls/upload-cert` | Upload custom certificate and key |
| `POST /api/tls/renew` | Manually trigger certificate renewal |
| `DELETE /api/tls/certificate` | Delete certificate and disable TLS |
| `POST /api/tls/test-dns-provider` | Test DNS provider credentials |
| `POST /api/tls/https/start` | Start HTTPS server |
| `POST /api/tls/https/stop` | Stop HTTPS server |
| `POST /api/tls/https/restart` | Restart HTTPS server |
| `GET /api/tls/https/status` | Get HTTPS server status |

**Session transport (bead `enhancedchannelmanager-04c0u.9`).** When ECM terminates
TLS, browser authentication cookies are `Secure`, `HttpOnly` and `SameSite=Lax`, and
the HTTP listener does not establish or refresh authenticated browser sessions:
`POST /api/auth/login`, `POST /api/auth/dispatcharr/login` and `POST /api/auth/refresh`
answer `403` there with a message naming the HTTPS address and both recovery options.
The refusal is scoped to exactly that configuration: ECM terminating TLS, break-glass
closed, a cleartext request, no `https://` `public_base_url`, and positive evidence of
an HTTPS listener (`cert.pem`/`key.pem` present, or the listener running). A
reverse-proxy deployment is unaffected, and neither is an instance whose
`tls_settings.json` has become unreadable without ever having had a certificate.
Cookies still carry `Secure` there, but sign-in is not refused, so a filesystem fault
cannot lock every account out.

**Activation revokes pre-activation sessions.** Whenever an instance goes from not
terminating TLS to terminating it, every existing `UserSession` is revoked and every
user's `auth_epoch` is bumped, so a browser holding a pre-activation non-`Secure`
`refresh_token` cannot keep replaying it. This is a one-time forced sign-out and it
applies to every route that can cause the transition: `POST /api/tls/configure`
(switching `enabled` on with a certificate already present), `POST /api/tls/request-cert`
and `POST /api/tls/complete-challenge` (landing the certificate an already-enabled
instance was waiting for), `POST /api/tls/upload-cert` (which does both at once) and
`POST /api/tls/renew` (which will issue a first certificate if none exists). Enabling
TLS before any certificate exists is not an activation and does not revoke anything.

**Break-glass.** `allow_http_session_cookies` is an explicit setting exposed by
`GET /api/tls/settings` and `POST /api/tls/configure`; a locked-out operator can
instead set `ECM_ALLOW_HTTP_SESSION_COOKIES` to `1`/`true`/`yes`/`on` for one recovery
restart (any other value, including the `false` that `docker-compose.yml` ships, leaves
protection on). Both work regardless of `public_base_url`, and both permit plaintext
session theft, so they must be disabled once HTTPS is repaired. Both are reported by
`GET /api/tls/status` as `allow_http_session_cookies`,
`http_session_cookies_env_override` and `session_cookies_plaintext`, and ECM logs a
warning at startup, on the first cookie the hatch downgrades, and whenever
`POST /api/tls/configure` changes the stored flag. `session_cookies_plaintext` is true
whenever either hatch is open **and** something would otherwise have protected the
cookies (ECM's own TLS or an `https://` `public_base_url`). That is the same condition
the downgrade warning is emitted on, so the log and the API cannot disagree.
It stays false on a plain-HTTP install with no proxy and no TLS, where the hatch costs
nothing.

On `POST /api/tls/configure`, `allow_http_session_cookies` is **preserve-on-omit**: a
request that does not carry the field leaves the stored value unchanged. A client that
does not know the field cannot turn the hatch on, so it must not be able to turn it off
either.

**Header trust.** ECM trusts a configured HTTPS `public_base_url` for reverse-proxy
cookie policy. ECM's own policy code does not consult `X-Forwarded-Proto`,
`X-Forwarded-Host` or `Forwarded`; note that uvicorn's `ProxyHeadersMiddleware` is
enabled by default and, for clients within `FORWARDED_ALLOW_IPS` (default
`127.0.0.1`), may itself rewrite `scope['scheme']` before any ECM code runs.

**HSTS** is emitted only by ECM's direct HTTPS listener, as
`max-age=31536000` with no `includeSubDomains` and no `preload`. Per RFC 6797 §8.3 the
pin is host-scoped and port-agnostic, so visiting the HTTPS listener also force-upgrades
the plaintext listener on that hostname for a year; see the recovery notes in
`README.md` (Port Configuration) for how to reach the HTTP port once a pin exists. ECM
does not redirect the always-on HTTP port because the HTTPS listener may use a
different port or be unavailable during recovery.

**Authorization (bead `enhancedchannelmanager-9kwzp.11`):** every route above requires an admin when authentication is enabled. `GET /api/tls/status` and `GET /api/tls/https/status` disclose no credential material and accept the static MCP API key. The other eleven refuse it with `403`, because they manage certificate and private-key material, the DNS-provider credentials that issue it, the HTTPS listener that serves it, or (for `GET /api/tls/settings`) a response carrying masked credential fragments. Use an operator admin JWT for those.

**Auth-disabled instances (beads `enhancedchannelmanager-jy006` and `enhancedchannelmanager-2u4e0`):** this paragraph used to end "all of these gates no-op while `require_auth` is false or setup is incomplete, so first-run and auth-disabled instances are unaffected." That is no longer true of eleven of the thirteen routes. Everything above except `GET /api/tls/status` and `GET /api/tls/https/status` requires an authenticated human admin even while `require_auth` is false, on any instance that already holds an operator identity: the ten certificate/key-material routes under `jy006`, and `POST /api/tls/test-dns-provider` under `2u4e0`, which closed the whole twelve-route connection-test family on the credential-oracle axis. An instance with no operator identity (genuine first run, or a headless auth-disabled deployment that never created a user) still reaches all thirteen anonymously. See `docs/auth_middleware.md` → "What `require_auth: false` permits" for the full rule and the other surfaces it covers.

## Cron

| Endpoint | Description |
|-|-|
| `GET /api/cron/presets` | List cron schedule presets |
| `POST /api/cron/validate` | Validate a cron expression |

## Dummy EPG

| Endpoint | Description |
|-|-|
| `GET /api/dummy-epg/profiles` | List dummy EPG profiles |
| `POST /api/dummy-epg/profiles` | Create dummy EPG profile |
| `GET /api/dummy-epg/profiles/{id}` | Get dummy EPG profile |
| `PATCH /api/dummy-epg/profiles/{id}` | Update dummy EPG profile |
| `DELETE /api/dummy-epg/profiles/{id}` | Delete dummy EPG profile |
| `POST /api/dummy-epg/generate` | Generate dummy EPG data |
| `POST /api/dummy-epg/preview` | Preview dummy EPG output |
| `POST /api/dummy-epg/preview/batch` | Batch preview dummy EPG (zero-write). Each result also carries `event_sync_start_valid`: true only when the Event Sync matcher would build a real start time from the captured groups (valid month, hour ≤ 23, real calendar date; never guessed) |
| `GET /api/dummy-epg/xmltv` | Get combined XMLTV output |
| `GET /api/dummy-epg/xmltv/{id}` | Get XMLTV output for a profile |
| `GET /api/dummy-epg/profiles/export/yaml` | Export profiles as YAML |
| `POST /api/dummy-epg/profiles/import/yaml` | Import profiles from YAML |
| `GET /api/dummy-epg/lint-findings` | Read-only view of saved dummy-EPG templates that fail the current write-time linter (bd-eio04.7) |

`POST /api/dummy-epg/preview` accepts the full profile config plus:

- `include_trace: bool`. When true, the response carries a `traces` dict keyed by template field (`title_template`, `description_template`, …). Trace entries describe literals, placeholders (with per-pipe input/output), and conditionals (taken/skipped + branch kind).

Both preview endpoints are zero-write **and** zero-read: the request carries
everything the engine needs.

> **Removed: `/api/lookup-tables` (bead `enhancedchannelmanager-70u0r.1`).** The
> five CRUD endpoints, the `inline_lookups` / `global_lookup_ids` preview
> request fields, and the `{key|lookup:<name>}` template pipe are all gone, as
> is the `lookup_tables` table (Alembic revision `0041`, destructive). The
> preview endpoints ignore the two retired fields rather than rejecting them, so
> a stale cached client keeps working. See
> [Lookup Tables retired](user_guide/epg/lookup-tables-retired.md).

## Backup & Restore

The Backup & Restore subsystem (v0.18.0, ADR-012) exposes two tiers of endpoints: the **DBAS artifact** path (new-format v0.18.0, full 12-category round-trip) and the **legacy ZIP/YAML** path (pre-v0.18.0, ECM-config-only). All endpoints require admin authentication unless noted.

### DBAS artifact endpoints (v0.18.0+)

These endpoints operate on the new-format `.zip` artifacts produced by the `dbas_backup` task. They cover the full 12-category Dispatcharr + ECM configuration.

A **standard** artifact (the default, unencrypted one) is fully redacted: it carries no value that identifies or authenticates against a third-party service, and no ECM authentication state. Three rules run in one place over every category, and all three are needed because none is complete alone:

1. **Credential-class key names** (`_REDACT_KEYS`), matched case-insensitively against dict keys.
2. **Provider identity keys** (`username`), applied by default so a new caller fails closed. Exactly one category is exempt, `dispatcharr_users`, whose username names the operator's own Dispatcharr instance and is the natural key its importer creates and collision-checks on.
3. **A value-level URL credential scrub**, catching credentials in a URL's userinfo or query string wherever they appear. A URL carrying no credential is left intact, because the restore needs the address.

The artifact's `journal.db` member additionally carries only an allowlist of configuration tables; every other table is dropped and the file is `VACUUM`ed. The cred-carrying **encrypted** artifact (`include_credentials`, ADR-012 D12) is unaffected by all of this: the identity keys join its preserve set, the URL scrub is off on that path, and its `journal.db` is a byte-for-byte copy. Encryption alone does not carry credentials; `include_credentials` does, and it requires a passphrase.

| Endpoint | Description |
|-|-|
| `POST /api/backup/restore-dbas` | Upload and restore a DBAS artifact (streaming, max 2 GiB). Validates integrity, schema version, and decompression-bomb checks before any mutation. Runs a **dry-run by default** (`confirm_apply=false`); pass `confirm_apply=true` to apply. Returns a `RestoreReport` with per-category `created/updated/skipped/failed` counts and `outcome` (`success`, `completed_with_failures`, `partial_failed_rolled_back`, `failed_rollback_incomplete`).  Admin-only when auth is enabled; the MCP service key is refused, since the restore writes ECM's own settings blob wholesale (bead 9kwzp.10).|
| `POST /api/backup/restore-dbas-saved` | Restore a saved DBAS artifact by filename (artifact must be in `/config/backups/`). Same dry-run/apply semantics as `/restore-dbas`. The saved file is not deleted.  Admin-only when auth is enabled. The MCP service key may run the counts-only preview and is refused the apply (`confirm_apply=true`), since applying writes ECM's own settings blob wholesale (bead 9kwzp.10).|
| `GET /api/backup/saved` | List saved DBAS backup artifacts under `/config/backups/`. Returns filename, size, and creation time. |
| `GET /api/backup/saved/{filename}` | Download a saved backup artifact (streamed). |
| `DELETE /api/backup/saved/{filename}` | Delete a saved backup artifact. |

#### Key restore parameters (`POST /api/backup/restore-dbas`)

| Parameter | Type | Default | Description |
|-|-|-|-|
| `file` | multipart file | required | The `.zip` backup artifact (plain or encrypted). |
| `confirm_apply` | bool | `false` | Set `true` to apply mutations. `false` (default) runs a counts-only dry-run; no changes are made. |
| `passphrase` | string | none | Required when the artifact is encrypted (detected from the file header). **Never logged or echoed.** |
| `selected_categories` | string (JSON array) | all categories | Comma-separated or JSON list of category keys to restore (e.g. `["m3u_accounts","channels"]`). Omit to restore all. |

#### RestoreReport response shape

```json
{
  "is_dry_run": true,
  "outcome": null,
  "categories": [
    {
      "entity_type": "m3u_account",
      "created": 0, "updated": 0, "skipped": 0, "failed": 0,
      "would_create": 3, "would_update": 0, "would_skip": 1,
      "skip_details": [
        { "reason": "already_exists_identical", "label": "My Provider", "source_export_id": 42 }
      ],
      "failure_details": []
    }
  ],
  "logo_misses": 0,
  "started_at": "2026-06-28T12:00:00Z",
  "completed_at": "2026-06-28T12:00:05Z",
  "notes": ["apply not confirmed — produced a counts-only dry-run; no mutation performed."]
}
```

- `is_dry_run: true` → `would_*` counts are populated; `created/updated/skipped/failed` are zero.
- `is_dry_run: false` → `created/updated/skipped/failed` counts are populated.
- `outcome` is `null` on a dry-run (a plan has no realized outcome). On an apply: `success`, `completed_with_failures`, `partial_failed_rolled_back`, or `failed_rollback_incomplete`.
- `completed_with_failures` means the restore ran to completion and **nothing was rolled back**, but the result is not clean. Two independent triggers: at least one row in a non-fatal category failed (only `dispatcharr_users` is non-fatal; the failed rows are counted in their category's `failed` / `failure_details`), **or** an apply produced a replica **missing something the source had** — any of `channels_with_no_playable_stream`, `stream_urls_redacted`, `epg_links_unrestored`, `logo_misses` or `entities_blocked_by_dependency` above zero. Every row can succeed and every count read clean while the replica has lost its guide data, its branding, or the ability to play at all, which is exactly what a `success … failed 0` was measured describing. Either way the applied state stands. A **dry run** never downgrades: a preview that predicts a shortfall predicted it. The set and the reasoning for each inclusion and exclusion are in `docs/dbas_restore_contracts.md` → "The delivery-shortfall set".
- `failed_rollback_incomplete` means the run is **indeterminate**. Either a fatal failure occurred and the rollback could not fully undo it, **or** the run could not read the destination it describes — `destination_unreadable` is non-null — so its counts came from the "the destination is empty" fallback and describe the source. That second trigger overrides every other reading of the counts, and it is never `completed_with_failures`: nothing was lost, the cycle never read what it describes, so it is an error rather than a degraded warning.
- `logo_misses` is an aggregate count of logos that could not be matched or applied.

#### Error responses

| Status | Detail | When |
|-|-|-|
| 400 | `"Unsupported backup version"` | Artifact `schema_version` is newer than this ECM build supports. |
| 400 | `"Backup integrity check failed"` | A member's SHA-256 does not match the manifest. |
| 400 | `"Backup archive rejected"` | Decompression-bomb check failed (too many entries, too high a ratio, or excessive uncompressed size). |
| 400 | `"Not a valid ECM backup artifact"` | The artifact is missing `manifest.json`. |
| 400 | `"Could not decrypt artifact: wrong passphrase or corrupted artifact"` | Passphrase is wrong, or the encrypted artifact is corrupted. The same message is returned for both cases (no oracle). |

### Legacy ZIP/YAML endpoints (pre-v0.18.0 compatibility)

These endpoints operate on the pre-v0.18.0 format (ECM settings + `journal.db` only, no full Dispatcharr configuration round-trip). They remain available for compatibility and are used by the legacy restore-on-first-run wizard.

| Endpoint | Description |
|-|-|
| `GET /api/backup/create` | Download a legacy ZIP backup (`settings.json`, `journal.db`, and the `uploads/logos` directory). Its `journal.db` goes through the same allowlist scrub as a standard artifact, so it carries no ECM accounts, and its `settings.json` masks credential-class fields by name and scrubs credential-bearing URL values. The `tls` and `m3u_uploads` directories are **not** included (they hold private keys and playlists with credential-bearing stream URLs); restore still reads both so an artifact from an older build is reinstated in full. The archive is still not a redacted artifact: `settings.json` keeps `username`, and operator-authored free text travels verbatim. Treat the file as a secret. Returns 500 if the `journal.db` scrub cannot complete (see [Backup failure is fail-closed](#backup-failure-is-fail-closed)). |
| `POST /api/backup/restore` | Restore from an uploaded legacy ZIP backup. Returns `notices` (see [Restore notices](#restore-notices)). |
| `POST /api/backup/restore-initial` | Restore from a legacy backup during first-run setup. Serves an instance that has no user accounts yet, so no credentials are needed there; once the instance holds an operator identity it requires an authenticated human admin, exactly like `POST /api/backup/restore`. Returns `notices` (see [Restore notices](#restore-notices)). |
| `GET /api/backup/export-sections` | List available YAML export sections. |
| `GET /api/backup/export` | Export selected sections as a YAML file. Optional `?sections=` query parameter selects sections; omit for all. Redaction now runs through the same gather as the DBAS artifact, so this export is covered by the same three rules. |
| `POST /api/backup/validate` | Validate a YAML export file and return section item counts. |
| `POST /api/backup/restore-yaml` | Restore from a YAML export (selective-section restore). Strips the `***REDACTED***` sentinel rather than writing it into a destination credential column, and reports the affected fields for re-entry. |
| `POST /api/backup/save` | Save a legacy ZIP backup to `/config/backups/`. |
| `POST /api/backup/restore-saved` | Restore from a saved legacy ZIP backup by filename. Returns `notices` (see [Restore notices](#restore-notices)). |

#### Restore notices

The three legacy-ZIP restore endpoints above (`/restore`, `/restore-initial`, `/restore-saved`) return an additive `notices: string[]` field alongside `restored_files`. It reports what the artifact could **not** carry, which `restored_files` structurally cannot: that list names what landed.

```json
{
  "status": "ok",
  "backup_version": "0.18.1",
  "backup_date": "2026-08-17T11:00:00Z",
  "restored_files": ["settings.json", "journal.db"],
  "notices": [
    "This instance has no ECM user account. A standard (non-encrypted) backup carries no account credentials by design, so accounts are not restored from one — create your admin account through first-run setup. To migrate accounts between instances instead, take an encrypted backup with credentials included."
  ]
}
```

Two notice types, both derived from the **live post-restore database** rather than predicted from the artifact, so a notice cannot claim a lockout that did not happen or miss one that did:

| Notice | Emitted when |
|-|-|
| First-run setup required | The instance holds no rows in `users` after the restore. Expected when restoring a standard artifact onto an instance that had no accounts. |
| Re-establish configured surfaces | A table in the re-establish set (`cloud_storage_targets`, `sync_targets`, `m3u_digest_settings`, `event_sync_exclusions`) had rows before the restore and none after. Row counts are compared on both sides of the file swap, so the notice names only what **this** instance lost. |

`notices` is absent from a response served by a build predating it, and is an empty list in the ordinary case. Clients must tolerate both.

Restoring a legacy ZIP no longer replaces the destination's own ECM accounts: `users`, `user_sessions`, `user_identities` and `password_reset_tokens` are snapshotted before `journal.db` is written and reinstated afterwards, including recreating a table the artifact did not ship. An instance that genuinely has no accounts reinstates nothing, which preserves the disaster-recovery path.

The `POST /api/backup/restore-dbas` and `/restore-dbas-saved` endpoints do **not** return `notices` and do not write `journal.db` at all: they restore the YAML categories and ECM's settings blob, so a DBAS restore never touches ECM accounts in either direction.

#### Backup failure is fail-closed

Both backup producers reduce their copy of `journal.db` to an allowlist of tables and redact the surviving cells before any bytes enter the archive. Every failure path in that scrub aborts the backup:

| Path | Behaviour on scrub failure |
|-|-|
| `GET /api/backup/create` | `500` with `"Failed to create backup: ..."`. No ZIP is returned. |
| `dbas_backup` task (`POST /api/backup/restore-dbas` consumes its output) | The task run fails and no artifact or `.sha256` sidecar is written. |

An unopenable database, an unreadable or un-rewritable table, and a `journal.db` that is not a SQLite database at all are all failures rather than fallbacks. Earlier builds fell back to shipping the raw database behind a `200`. A `500` here is the control working, not a broken backup subsystem; the unscrubbed temporary copy is destroyed on the way out.

A row in `alert_methods` whose `config` does not parse as a JSON object loses its whole `config` value to the `***REDACTED***` sentinel rather than shipping unparsed. The restore side merges a whole-value sentinel the same way it merges a per-key one, reinstating the destination's own config for that row, so the fail-closed producer cannot destroy a working alert method.

### Cloud destination endpoints

| Endpoint | Description |
|-|-|
| `GET /api/cloud-targets` | List configured cloud storage targets (credentials masked). Admin-only when auth is enabled; the MCP service key is admitted, since every credential in the response is masked (bead 9kwzp.10). |
| `POST /api/cloud-targets` | Create a cloud storage target. Admin-only when auth is enabled; the MCP service key is admitted, because bead jcj0f ships a `create_cloud_target` tool over this route. Note a write repoints scheduled credential-bearing uploads and sets the `insecure` TLS-verification flag (bead 9kwzp.10). |
| `GET /api/cloud-targets/{id}` | Get a cloud storage target. |
| `PATCH /api/cloud-targets/{id}` | Update a cloud storage target. Admin-only when auth is enabled; the MCP service key is admitted, because bead jcj0f ships an `update_cloud_target` tool over this route. Same residual as `POST` (bead 9kwzp.10). |
| `DELETE /api/cloud-targets/{id}` | Delete a cloud storage target. Admin-only when auth is enabled; the MCP service key is admitted, because bead jcj0f ships a `delete_cloud_target` tool over this route (bead 9kwzp.10). |
| `POST /api/cloud-targets/{id}/test` | Test connectivity to a saved cloud storage target, using its stored credentials. Admin-only when auth is enabled; the MCP service key is refused (bead 9kwzp.6). Also requires an authenticated human admin while `require_auth` is false, on any instance that already holds an operator identity (bead 2u4e0). |
| `POST /api/cloud-targets/test` | Test connectivity with inline (not-yet-saved) credentials. Same gate as `/{id}/test`. |

**Supported provider types in v0.18.0:** `s3` (AWS S3, MinIO, Backblaze B2), `gdrive` (Google Drive), `webdav`. Adapters for `onedrive` and `dropbox` exist in the codebase but are deferred. A configured target of a deferred provider type produces a per-target failure on each backup run.

See the [user guide](user_guide/backup-restore/configure-cloud-destinations.md) for per-provider credential fields.

## Authentication

| Endpoint | Description |
|-|-|
| `GET /api/auth/status` | Get authentication status and configuration |
| `GET /api/auth/setup-required` | Check if first-run setup is needed |
| `POST /api/auth/setup` | Complete first-run setup (create admin account) |
| `POST /api/auth/login` | Login with username/password |
| `POST /api/auth/logout` | Logout and clear session |
| `POST /api/auth/refresh` | Refresh access token. Rotates the refresh token on every accepted call. The **immediately-prior** refresh token stays acceptable until its successor is actually used (rotation confirmation, bead `upkp1`): presenting it returns a fresh access-token cookie and NO refresh cookie, never rotates, and never extends the session's `expires_at`. Exactly one generation is retained, so a token two rotations old is refused. Refusals return 401 and are logged with a reason code. |
| `GET /api/auth/me` | Get current user info |
| `PUT /api/auth/me` | Update current user profile |
| `POST /api/auth/change-password` | Change current user's password |
| `POST /api/auth/forgot-password` | Request a password reset email. Always returns the same success response for known and unknown addresses. Limited to 5 requests/minute per client; one unused link is retained per local account and repeat email for that account is suppressed for 5 minutes. The link's origin comes from the `public_base_url` setting when it is set; otherwise it falls back to the caller-supplied `X-Forwarded-Host` / `Host` headers, which is why the setting exists (beads qsqfv, 04c0u.2). |
| `POST /api/auth/reset-password` | Reset a password with a one-hour, single-use token. Limited to 10 requests/minute per client and 10 password-validation attempts per token. Success revokes the account's existing sessions (bead 04c0u.2). |
| `GET /api/auth/providers` | List available auth providers |
| `POST /api/auth/dispatcharr/login` | Login via Dispatcharr credentials |
| `GET /api/auth/identities` | List linked auth identities for current user |
| `POST /api/auth/identities/link` | Link a new auth identity to current user |
| `DELETE /api/auth/identities/{id}` | Unlink an auth identity |
| `GET /api/auth/admin/settings` | Get auth settings (admin) |
| `PUT /api/auth/admin/settings` | Update auth settings (admin) |
| `GET /api/auth/admin/users` | List users (admin) |
| `GET /api/auth/admin/users/{id}` | Get user details (admin) |
| `PUT /api/auth/admin/users/{id}` | Update user (admin) |
| `DELETE /api/auth/admin/users/{id}` | Delete user (admin) |

## User Management (Admin)

| Endpoint | Description |
|-|-|
| `GET /api/admin/users` | List all users (paginated, searchable) |
| `POST /api/admin/users` | Create new user |
| `GET /api/admin/users/{id}` | Get user details |
| `PATCH /api/admin/users/{id}` | Update user |
| `DELETE /api/admin/users/{id}` | Delete (deactivate) user |

## Health

| Endpoint | Description |
|-|-|
| `GET /api/health` | Health check |
| `GET /api/debug/request-rates` | Request rate statistics (diagnostics) |
