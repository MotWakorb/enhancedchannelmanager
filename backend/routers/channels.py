"""
Channels router — channel CRUD, logos, CSV import/export, stream management,
number assignment, bulk-commit, and clear-auto-created endpoints.

Extracted from main.py (Phase 2 of v0.13.0 backend refactor).
"""
import asyncio
import logging
import os
import re
import time
import uuid
from datetime import date
from typing import Any, Optional, Literal, Sequence, Union
from urllib.parse import parse_qs, quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, PositiveInt, field_validator
from pydantic_core import PydanticCustomError

from auth import RequireAdminIfEnabled
from bulk_commit_accounting import (
    OperationLedger,
    finalize_bulk_commit_result,
    nothing_to_journal,
)
from channel_number import (
    CHANNEL_NUMBER_RULE_MESSAGE,
    ChannelNumber,
    InvalidChannelNumberError,
    format_channel_number,
    parse_channel_number_text,
    validate_channel_number_in_payload,
)
from channel_number_apply import (
    NumberingCompensator,
    NumberingWrite,
    order_numbering_writes,
    same_channel_number,
)
from channel_number_plan import evaluate_final_numbering
from channel_group_reparent import (
    UNGROUPED_TARGET_GROUP_NAME,
    reparent_group_channels,
)
from concurrency import run_cpu_bound
from config import get_settings
from csv_handler import parse_csv, generate_csv, generate_template, CSVParseError
from database import get_session
from dispatcharr_client import get_client, upstream_http_exception
from match_fold import fold_match_key
from normalization_engine import get_normalization_engine
import journal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/channels", tags=["Channels"])


#: Channel fields the journal describes in prose, in the order a description
#: lists them. ``label`` receives the NEW value and returns the phrase; a
#: falsy new value takes ``cleared_label`` instead when one is given.
#: Shared by the single-channel PATCH handler and the bulk-commit executor so
#: the two paths cannot drift into describing the same edit differently
#: (bead enhancedchannelmanager-r9py9).
_CHANNEL_CHANGE_DESCRIBERS: tuple[tuple[str, object, Optional[str]], ...] = (
    ("name", lambda v: f"name to '{v}'", None),
    ("channel_number", lambda v: f"number to {format_channel_number(v)}", None),
    ("tvg_id", lambda v: f"EPG mapping to '{v}'", "cleared EPG mapping"),
    ("logo_id", lambda _v: "logo", "cleared logo"),
    ("channel_group_id", lambda v: f"group to {v}", "cleared group"),
    ("epg_data_id", lambda v: f"EPG source to {v}", "cleared EPG source"),
    ("stream_profile_id", lambda v: f"stream profile to {v}", "cleared stream profile"),
    ("tvc_guide_stationid", lambda v: f"Gracenote ID to '{v}'", "cleared Gracenote ID"),
)

#: The describers' coverage, as a set. Everything NOT in here is described
#: generically rather than skipped — see :func:`describe_channel_update`.
_CHANNEL_DESCRIBED_FIELDS: frozenset[str] = frozenset(
    field for field, _label, _cleared in _CHANNEL_CHANGE_DESCRIBERS
)

#: Reserved key in a journal row's ``before_value``, listing the fields whose
#: before-state ECM could not read at all — a channel created earlier in the
#: same batch, a catalog read that failed, a payload field Dispatcharr does not
#: return. Named rather than defaulted, because ``before_channel.get(field)``
#: answered ``None`` for both "it held null" and "I never saw it", and those are
#: different facts (bead ``enhancedchannelmanager-kz089``, fix round 5). The
#: double-underscore form is reserved: no Dispatcharr channel field uses it, so
#: it cannot collide with a real key in the same dict.
BEFORE_STATE_UNKNOWN_KEY = "__before_state_unknown__"


def describe_channel_update(
    before_channel: dict, data: dict
) -> tuple[list[str], dict, dict]:
    """Reduce a channel PATCH payload to ``(changes, before_value, after_value)``.

    ``changes`` is the human-readable phrase list a journal description joins
    with ", "; the two dicts carry only the fields that actually moved, so an
    expanded journal row shows the edit rather than the whole record.

    Both the ``PATCH /api/channels/{id}`` handler (the path an MCP agent takes,
    and the one that has always written per-channel rows) and the Edit Mode
    bulk-commit executor call this. Before bead enhancedchannelmanager-r9py9
    only the former journaled at all, so a channel's history was traceable by
    name for AI-sourced edits and invisible for UI-sourced ones.

    TOTAL OVER THE PAYLOAD, which is the property fix round 4 of bead
    ``enhancedchannelmanager-kz089`` had to establish. Both callers PATCH a
    free-form ``data`` bag upstream WHOLE and then ask this function whether a
    row is owed; an empty ``changes`` is what lets the bulk executor say
    ``nothing_to_journal``. While the loop only knew the eight described
    fields, ``data={"streams": [7]}`` changed channel 42 upstream and was
    reported as describing no change — a landed mutation with no journal row,
    which is the exact defect the required-``journal_row`` argument was
    supposed to have made unreachable. A field the describers have no prose for
    is therefore still a change, named generically, with its values carried in
    ``before_value`` / ``after_value``. Coverage is total by construction, so a
    field nobody has invented yet cannot reopen this.

    Empty ``changes`` now means one thing only: every field in ``data`` was
    already holding that value. ``before_channel`` may be ``{}``, or may simply
    not carry a field, when the before-state is unknown — a channel created
    earlier in the same batch, a catalog read that failed. "I cannot see what
    it was" is not "it did not change", so an unknown before-state reads as a
    change on both arms rather than as silence.

    AN UNKNOWN BEFORE-STATE IS NAMED, not defaulted (fix round 5). Round 4
    established that it counts as a change and then recorded it with
    ``before_channel.get(field)``, which is ``None`` — the same serialisation
    an explicitly-null before-state produces. ``before_channel = {}`` with
    ``data = {"custom_prop": None}`` therefore wrote
    ``{"before": {"custom_prop": null}, "after": {"custom_prop": null}}``:
    evidence that something changed, and no evidence of what. A field whose
    before-state ``before_channel`` does not carry is listed under
    :data:`BEFORE_STATE_UNKNOWN_KEY` instead of being given a value ECM never
    read, so a row always distinguishes "it held null" from "I could not see
    it" — which is the difference between an operator being able to reconcile
    the edit and merely being told one happened.
    """
    changes: list[str] = []
    before_value: dict = {}
    after_value: dict = {}

    def unchanged(field: str, new_value: object) -> bool:
        return field in before_channel and new_value == before_channel[field]

    def note(field: str, phrase: str) -> None:
        changes.append(phrase)
        if field in before_channel:
            before_value[field] = before_channel[field]
        else:
            before_value.setdefault(BEFORE_STATE_UNKNOWN_KEY, []).append(field)
        after_value[field] = data[field]

    for field, label, cleared_label in _CHANNEL_CHANGE_DESCRIBERS:
        if field not in data:
            continue
        new_value = data[field]
        if unchanged(field, new_value):
            continue
        if not new_value and cleared_label is not None:
            note(field, cleared_label)
        else:
            note(field, label(new_value))

    # The rest of the bag, in payload order. Generic prose because there is no
    # per-field vocabulary to draw on — the values are what an operator
    # reconciles from, and they are in the two dicts.
    for field in data:
        if field in _CHANNEL_DESCRIBED_FIELDS:
            continue
        new_value = data[field]
        if unchanged(field, new_value):
            continue
        note(field, f"set {field}" if new_value else f"cleared {field}")

    return changes, before_value, after_value


def write_journal_rows(
    rows: list[dict],
    *,
    batch_id: Optional[str] = None,
    log_tag: str = "CHANNELS",
) -> int:
    """Write ``rows``; return how many could NOT be written.

    ``journal.log_entries`` writes N rows in ONE transaction, which is what
    keeps a several-hundred-channel Apply All from becoming several hundred
    transactions — and it reports failure by RETURNING ``False``, not by
    raising, so ignoring its return value loses the whole batch's audit trail
    in silence. ``journal.log_entry`` reports the same failure by returning
    ``None``. Both return values are checked here.

    When the batch write fails, the rows are retried one at a time: the
    realistic failure is a single unwritable row, and a batch write lets that
    one row take every other row's audit trail with it. Anything still
    unwritable after that is logged at ERROR with its full content, because an
    upstream mutation with no journal row is exactly what an operator later has
    to reconstruct by hand.

    THE ONE MECHANISM, shared by both surfaces (fix round 5). This lived inside
    the bulk-commit executor's closure, so ``PATCH /api/channels/{id}`` — the
    path an MCP agent takes — still called ``journal.log_entry`` for its effect
    and discarded the result. That is the round-2 defect verbatim, one endpoint
    over: a read-only or unavailable journal database produced a landed
    Dispatcharr change, no row, and a ``200`` that mentioned neither. A second
    implementation would have been a second thing to keep in step, so there is
    one, at module scope, taking the batch correlation id and the log tag its
    caller writes under.

    NOTHING IS LOST TO A ``BaseException``. Neither retry above catches one —
    ``SystemExit``, ``KeyboardInterrupt``, or a ``CancelledError`` raised by a
    synchronous dependency are not ``Exception`` — and the bulk caller has
    already DRAINED these rows off the ledger by the time this runs, so there
    is no queue left to retry them from and no second flush to do it. Every row
    this call has not yet resolved is therefore logged before the
    ``BaseException`` is allowed to continue, which is the same promise the
    per-row failure path already makes for an unwritable row.
    """
    if not rows:
        return 0

    # Rows this call has neither written nor already reported. Kept as a
    # separate list purely so the BaseException guard below knows what is
    # still owed without re-reporting anything.
    pending = list(rows)
    try:
        try:
            if journal.log_entries(rows) is not False:
                return 0
            logger.error(
                "[%s] Batch journal write failed for %s row(s) "
                "(batch=%s); retrying one at a time", log_tag, len(rows), batch_id,
            )
        except Exception as batch_err:
            logger.exception(
                "[%s] Batch journal write raised for %s row(s) "
                "(batch=%s); retrying one at a time: %s",
                log_tag, len(rows), batch_id, batch_err,
            )

        unwritten = 0
        while pending:
            row = pending[0]
            try:
                written = journal.log_entry(**row) is not None
            except Exception as row_err:
                logger.exception(
                    "[%s] Journal row raised (batch=%s): %s",
                    log_tag, batch_id, row_err,
                )
                written = False
            # Resolved either way: written, or about to be reported below.
            pending.pop(0)
            if written:
                continue
            unwritten += 1
            logger.error(
                "[%s] UNJOURNALLED MUTATION (batch=%s): %s", log_tag, batch_id, row,
            )
        return unwritten
    except BaseException:
        for row in pending:
            logger.error(
                "[%s] UNJOURNALLED MUTATION (batch=%s): %s", log_tag, batch_id, row,
            )
        raise


def validate_stream_permutation(
    current_stream_ids: list[int], new_stream_ids: list[int]
) -> Optional[str]:
    """Validate that ``new_stream_ids`` is a true permutation of the channel's
    current streams (a pure reorder), guarding against the replace-semantics
    data loss of Dispatcharr's ``streams`` field.

    Updating a channel's ``streams`` to a partial list silently DETACHES any
    omitted streams. A reorder must therefore contain exactly the same set of
    stream IDs as the channel currently has — no missing, unknown, or duplicate
    IDs (bd-1wq7z.3 single-channel reorder; bd-1wq7z.25 bulk-commit reorder).

    Returns ``None`` if ``new_stream_ids`` is a valid permutation, otherwise a
    human-readable message describing the problem (suitable for an HTTP 400
    detail or a per-op bulk error).
    """
    current = list(current_stream_ids)
    new = list(new_stream_ids)

    if len(new) != len(set(new)):
        seen: set[int] = set()
        dupes = sorted({sid for sid in new if sid in seen or seen.add(sid)})
        return f"streamIds contains duplicate ids: {dupes}"

    current_set = set(current)
    new_set = set(new)

    missing = sorted(current_set - new_set)  # would be detached
    unknown = sorted(new_set - current_set)  # not attached to this channel

    if missing or unknown:
        parts = []
        if missing:
            parts.append(f"would detach attached streams {missing}")
        if unknown:
            parts.append(f"includes streams not on the channel {unknown}")
        return (
            "streamIds is not a permutation of the channel's current streams "
            f"({'; '.join(parts)})"
        )

    return None


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateChannelRequest(BaseModel):
    name: str
    # `ChannelNumber` is the canonical domain (bead enhancedchannelmanager-ic884.1):
    # non-negative, at most one decimal place. Every channel-number field in this
    # module uses it so no entry point re-implements the check.
    channel_number: Optional[ChannelNumber] = None
    channel_group_id: Optional[int] = None
    logo_id: Optional[int] = None
    tvg_id: Optional[str] = None
    normalize: Optional[bool] = False  # Apply normalization rules to channel name


class CreateLogoRequest(BaseModel):
    name: str
    url: str


class AddStreamRequest(BaseModel):
    stream_id: int


class AddStreamsRequest(BaseModel):
    stream_ids: list[int]


class RemoveStreamRequest(BaseModel):
    stream_id: int


class ReorderStreamsRequest(BaseModel):
    stream_ids: list[int]


class AssignNumbersRequest(BaseModel):
    channel_ids: list[int]
    starting_number: Optional[ChannelNumber] = None


class MergeChannelsRequest(BaseModel):
    source_channel_ids: list[int]
    target_name: str
    target_channel_number: Optional[ChannelNumber] = None
    target_channel_group_id: Optional[int] = None
    target_logo_id: Optional[int] = None
    target_tvg_id: Optional[str] = None
    target_epg_data_id: Optional[int] = None
    target_stream_profile_id: Optional[int] = None


class ClearAutoCreatedRequest(BaseModel):
    group_ids: list[int]


class FindDuplicatesRequest(BaseModel):
    """Optional scope for POST /channels/find-duplicates (enhancedchannelmanager-uahp6).

    ``channel_ids`` absent — or the request body omitted entirely — scans all
    channels (global, backward-compatible default for MCP/script callers).
    When present, the scan is restricted to exactly those channel ids (used
    by the frontend's checkbox-selection-scoped "Find Duplicates" action).
    An explicit empty list is a valid scope of "no channels": it returns an
    empty result (0 groups) rather than silently falling back to a global
    scan — a caller that asked to scope to nothing should not get everything.

    ``fold_match_key`` (GH #645 / bead 0vao3): when True, channels are
    grouped by the shared canonical fold key — casefold + strip ALL
    whitespace (``match_fold.fold_match_key``) applied to the normalized
    name — so "Eurosport 2" and "Eurosport2" land in one duplicate group,
    matching what an auto-creation rule with its own ``fold_match_key``
    flag would merge. Default False preserves the existing grouping
    (normalized name, case-insensitive). Comparison key only — displayed
    names are never altered.
    """
    channel_ids: Optional[list[int]] = None
    fold_match_key: bool = False


class BulkMergeItem(BaseModel):
    """A single merge operation: keep target, absorb sources."""
    target_channel_id: int
    source_channel_ids: list[int]


class BulkMergeRequest(BaseModel):
    merges: list[BulkMergeItem]


class NormalizePreviewBatchItem(BaseModel):
    """Single row in a batch normalize-preview request.

    The frontend already knows the current channel name from the list
    response — passing it here avoids an extra Dispatcharr roundtrip per
    row. bd-eio04.13.
    """
    channel_id: int
    name: str


class NormalizePreviewBatchRequest(BaseModel):
    """bd-eio04.13 — batch preview of would_normalize for currently-visible channel rows.

    Accepts either:
      - `channels`: list of {channel_id, name} pairs (preferred — no
        Dispatcharr roundtrip, O(rules × M) instead of O(rules × M) +
        M HTTP calls).
      - `channel_ids`: list of ids (fallback — the backend fetches each
        name from Dispatcharr). Retained for deep-link scenarios where
        the caller only has IDs.
    Exactly one of the two must be populated.
    """
    channels: Optional[list[NormalizePreviewBatchItem]] = None
    channel_ids: Optional[list[int]] = None


# Upper bound on a single batch to keep per-request cost O(rules × 100).
# Frontend pages above this if more rows are visible. See bd-eio04.13.
NORMALIZE_PREVIEW_BATCH_MAX = 100


class AcknowledgedDuplicate(BaseModel):
    """The caller's recorded consent to ONE specific channel-number collision.

    Bead ``enhancedchannelmanager-vdxbx``. ECM's own bookkeeping, never
    forwarded to Dispatcharr: it rides beside an operation's ``data`` rather
    than in it precisely because ``data`` is the PATCH body. The final-state
    preflight reads it to tell a deliberate duplicate from an accidental one.

    THE OCCUPANTS ARE PART OF THE CONSENT, not decoration. An operator shown
    "102 is used by Bravo — use it anyway?" agreed to share 102 WITH BRAVO. If
    Bravo moves off 102 and Delta moves on, the collision the commit would
    create is one nobody was shown, and an acknowledgement carrying only the
    number would authorise it regardless. Both halves in one object makes a
    half-recorded acknowledgement unrepresentable rather than merely
    discouraged, and it is why ``occupantChannelIds`` has no default: a caller
    that means "nobody was there" has to say so.

    ``occupantChannelIds`` excludes the channel being placed — a channel never
    collides with itself — and may name a negative temp id for a channel
    created earlier in the same request.
    """

    number: ChannelNumber
    occupantChannelIds: list[int]


class ExpectedChannelNumber(BaseModel):
    """The channel number the caller believes this channel is on RIGHT NOW.

    Bead ``enhancedchannelmanager-ic884.4``. ECM's own bookkeeping, never
    forwarded to Dispatcharr, and beside ``data`` rather than in it for the same
    reason :class:`AcknowledgedDuplicate` is: ``data`` is the PATCH body.

    A WRAPPER RATHER THAN A BARE ``Optional[float]``, because ``null`` is a
    legitimate expectation — "I believe this channel has no number" — and a bare
    optional cannot tell that apart from "I am not making a claim". The object's
    presence is the claim; its ``number`` is the value.

    IT IS A CHECK AND NOT A GUARANTEE, and the difference is measured rather
    than assumed. The live Dispatcharr 0.28.x schema
    (``GET /api/schema/?format=json``, HTTP 200, ~717KB, fetched 2026-08-15)
    contains ZERO occurrences of ``If-Match``, ``If-None-Match``,
    ``If-Unmodified-Since``, ``ETag`` or ``412``, and neither ``Channel`` nor
    ``PatchedChannel`` carries a version or modified-at field. There is no
    conditional update to send, so the executor compares against the lineup IT
    read at the start of the run and a change landing between that read and the
    PATCH is still lost. What this closes is the much wider window between a
    browser reading the lineup and this executor writing to it, and it is the
    only half of the check that exists at all for a caller that never touches
    the UI.
    """

    number: Optional[ChannelNumber] = None


# Bulk commit operation types
class BulkUpdateChannelOp(BaseModel):
    type: Literal["updateChannel"] = "updateChannel"
    channelId: int
    data: dict
    #: See :class:`AcknowledgedDuplicate`.
    acknowledgedDuplicate: Optional[AcknowledgedDuplicate] = None
    #: See :class:`ExpectedChannelNumber`. Only meaningful when ``data``
    #: carries ``channel_number``; ignored otherwise, because an operation that
    #: does not write the number cannot overwrite anybody's change to it.
    expectedNumber: Optional[ExpectedChannelNumber] = None

    @field_validator("data")
    @classmethod
    def _check_channel_number(cls, value: dict) -> dict:
        """`data` is a free-form field bag, so the contract is applied by key.

        Absent means "not changing the number"; explicit `None` means "clear
        it". Anything else must be in contract (bead
        enhancedchannelmanager-ic884.1).
        """
        try:
            validate_channel_number_in_payload(value)
        except InvalidChannelNumberError:
            raise PydanticCustomError("channel_number", CHANNEL_NUMBER_RULE_MESSAGE) from None
        return value


class BulkAddStreamOp(BaseModel):
    type: Literal["addStreamToChannel"] = "addStreamToChannel"
    channelId: int
    streamId: int


class BulkRemoveStreamOp(BaseModel):
    type: Literal["removeStreamFromChannel"] = "removeStreamFromChannel"
    channelId: int
    streamId: int


class BulkReorderStreamsOp(BaseModel):
    type: Literal["reorderChannelStreams"] = "reorderChannelStreams"
    channelId: int
    streamIds: list[int]


class BulkAssignNumbersOp(BaseModel):
    type: Literal["bulkAssignChannelNumbers"] = "bulkAssignChannelNumbers"
    channelIds: list[int]
    startingNumber: Optional[ChannelNumber] = None


class BulkCreateChannelOp(BaseModel):
    type: Literal["createChannel"] = "createChannel"
    tempId: int  # Negative temp ID from frontend
    name: str
    channelNumber: Optional[ChannelNumber] = None
    groupId: Optional[int] = None
    newGroupName: Optional[str] = None
    logoId: Optional[int] = None
    logoUrl: Optional[str] = None
    tvgId: Optional[str] = None
    tvcGuideStationId: Optional[str] = None  # Gracenote ID from M3U tvc-guide-stationid
    normalize: Optional[bool] = False  # Apply normalization rules to channel name
    #: See :class:`AcknowledgedDuplicate`. A created channel can land on an
    #: occupied number just as an edited one can.
    acknowledgedDuplicate: Optional[AcknowledgedDuplicate] = None


class BulkDeleteChannelOp(BaseModel):
    type: Literal["deleteChannel"] = "deleteChannel"
    channelId: int


class BulkCreateGroupOp(BaseModel):
    type: Literal["createGroup"] = "createGroup"
    name: str


class BulkDeleteGroupOp(BaseModel):
    type: Literal["deleteChannelGroup"] = "deleteChannelGroup"
    groupId: int


class BulkRenameGroupOp(BaseModel):
    type: Literal["renameChannelGroup"] = "renameChannelGroup"
    groupId: int
    newName: str


# --------------------------------------------------------------------------
# Operations added so Edit Mode can stage what it used to write immediately
# (bead enhancedchannelmanager-kz089)
#
# Edit Mode presents itself as a staging area, and these three actions sat in
# its toolbars writing straight through it: an operator who set profile
# visibility for a selection, restored a hidden group, or cleared a stream's
# probe stats and then hit Discard had already changed the server. They stage
# now, which means they need a wire representation here.
# --------------------------------------------------------------------------

class BulkSetProfileMembershipOp(BaseModel):
    """Enable or disable one channel in one channel profile.

    ``profileId`` is a real Dispatcharr id: Edit Mode has no staged-profile
    concept (creating a profile stays immediate, per the PO's 2026-08-15
    decision), so unlike a group id there is never a negative placeholder here.
    ``channelId`` MAY be negative — that is the frontend's temp id for a channel
    created earlier in the same batch, resolved through ``tempIdMap`` — and the
    executor rejects one that never resolves rather than sending it upstream.
    """
    type: Literal["setProfileMembership"] = "setProfileMembership"
    profileId: PositiveInt
    channelId: int
    enabled: bool


class BulkRestoreGroupOp(BaseModel):
    """Un-hide a channel group ECM previously hid (ECM-local state).

    The group is a real Dispatcharr group ECM keeps a local hidden-marker row
    for, so the id is always positive; a staged group has never been hidden.
    """
    type: Literal["restoreChannelGroup"] = "restoreChannelGroup"
    groupId: PositiveInt


class BulkClearStreamStatsOp(BaseModel):
    """Delete probe stats for streams, returning them to 'never probed'.

    The ids go straight into a ``DELETE ... WHERE stream_id IN (...)``, so they
    are held to the same shape the oldest operations are: real positive ids, at
    least one of them, and no duplicates. An empty list used to be accepted and
    counted as an applied operation that did nothing.
    """
    type: Literal["clearStreamStats"] = "clearStreamStats"
    streamIds: list[PositiveInt] = Field(min_length=1)

    @field_validator("streamIds")
    @classmethod
    def _reject_duplicates(cls, value: list[int]) -> list[int]:
        if len(set(value)) != len(value):
            raise PydanticCustomError(
                "duplicate_stream_ids",
                "streamIds must not contain duplicates",
            )
        return value


# Union type for all bulk operations
BulkOperation = Union[
    BulkUpdateChannelOp,
    BulkAddStreamOp,
    BulkRemoveStreamOp,
    BulkReorderStreamsOp,
    BulkAssignNumbersOp,
    BulkCreateChannelOp,
    BulkDeleteChannelOp,
    BulkCreateGroupOp,
    BulkDeleteGroupOp,
    BulkRenameGroupOp,
    BulkSetProfileMembershipOp,
    BulkRestoreGroupOp,
    BulkClearStreamStatsOp,
]


class BulkCommitRequest(BaseModel):
    operations: list[BulkOperation]
    # Groups to create before processing operations (name -> temp group ID mapping)
    groupsToCreate: Optional[list[dict]] = None
    # If true, only validate without executing (returns validation issues)
    validateOnly: Optional[bool] = False
    # If true, continue processing even when individual operations fail
    continueOnError: Optional[bool] = False
    # If true, consolidate redundant operations before executing
    consolidate: Optional[bool] = False


class ValidationIssue(BaseModel):
    """Represents a validation issue found during pre-validation"""
    type: str  # 'missing_channel', 'missing_stream', 'invalid_operation', etc.
    severity: str  # 'error', 'warning'
    message: str
    operationIndex: Optional[int] = None
    channelId: Optional[int] = None
    channelName: Optional[str] = None
    streamId: Optional[int] = None
    streamName: Optional[str] = None


class BulkCommitResponse(BaseModel):
    success: bool
    operationsApplied: int
    operationsFailed: int
    errors: list[dict]
    # Map of temp channel IDs to real IDs
    tempIdMap: dict[int, int]
    # Map of group names to real IDs
    groupIdMap: dict[str, int]
    # Validation issues found during pre-validation
    validationIssues: Optional[list[dict]] = None
    # Whether validation passed (no errors, may have warnings)
    validationPassed: Optional[bool] = None


# ---------------------------------------------------------------------------
# Channel list / create
# ---------------------------------------------------------------------------

@router.get("")
async def get_channels(
    # Bounds enforced here (bead 1a5mf): page<1 / page_size<1 were passed
    # straight to the upstream Dispatcharr client, which raised and surfaced as
    # a 500. FastAPI Query validation now returns 422 for out-of-range values.
    # Upper bound is generous — App.tsx legitimately requests page_size=5000.
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(100, ge=1, le=10000, description="Results per page"),
    search: Optional[str] = None,
    channel_group: Optional[int] = None,
):
    """List channels with pagination, search, and group filtering."""
    start_time = time.time()
    logger.debug(
        "[CHANNELS] Fetching channels - page=%s, page_size=%s, "
        "search=%s, group=%s",
        page, page_size, search, channel_group
    )
    client = get_client()
    try:
        fetch_start = time.time()
        result = await client.get_channels(
            page=page,
            page_size=page_size,
            search=search,
            channel_group=channel_group,
        )
        fetch_time = (time.time() - fetch_start) * 1000
        total_time = (time.time() - start_time) * 1000
        result_count = len(result.get('results', []))
        total_count = result.get('count', 0)

        # Debug logging: count channels per group_id on first page of unfiltered requests
        if page == 1 and not search and not channel_group:
            channels = result.get('results', [])
            group_counts: dict = {}
            for ch in channels:
                group_id = ch.get('channel_group_id')
                group_name = ch.get('channel_group_name', 'Unknown')
                key = f"{group_id}:{group_name}"
                if key not in group_counts:
                    group_counts[key] = {'count': 0, 'sample_channels': []}
                group_counts[key]['count'] += 1
                # Keep first 3 sample channel names per group for debugging
                if len(group_counts[key]['sample_channels']) < 3:
                    group_counts[key]['sample_channels'].append(
                        f"#{ch.get('channel_number')} {ch.get('name', 'unnamed')}"
                    )

            logger.debug("[CHANNELS] Page 1 stats: %s channels returned, API total=%s", result_count, total_count)
            logger.debug("[CHANNELS] Channels per group_id (page 1 only):")
            for key, data in sorted(group_counts.items(), key=lambda x: -x[1]['count']):
                logger.debug("  %s: %s channels (samples: %s)", key, data['count'], data['sample_channels'])

        logger.debug(
            "[CHANNELS] Fetched %s channels (total=%s, page=%s) "
            "- fetch=%.1fms, total=%.1fms",
            result_count, total_count, page, fetch_time, total_time
        )
        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to retrieve channels: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("")
async def create_channel(request: CreateChannelRequest, _admin=RequireAdminIfEnabled):
    """Create a new channel. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS] POST /channels - name=%s number=%s normalize=%s", request.name, request.channel_number, request.normalize)
    client = get_client()
    try:
        # Apply normalization if requested.
        #
        # A failure here used to be swallowed: a log warning, the raw name, and
        # a 200 that was observationally IDENTICAL to `normalize=false`. The
        # caller asked for a capability, did not get it, and had no way to tell
        # (bead enhancedchannelmanager-e9e5o). The create still succeeds — a
        # channel that exists must not start reporting as a failure, and the
        # affected callers are third-party MCP/REST clients we cannot see — but
        # the response now SAYS what happened. See the `normalization` block
        # below the create call.
        channel_name = request.name
        normalization_error: Optional[str] = None
        if request.normalize:
            try:
                with get_session() as db:
                    engine = get_normalization_engine(db)
                    # Offload normalization off event loop (bd-w3z4h)
                    norm_result = await run_cpu_bound(engine.normalize, request.name)
                    channel_name = norm_result.normalized
                    if channel_name != request.name:
                        logger.debug("[CHANNELS] Normalized channel name: '%s' -> '%s'", request.name, channel_name)
            except Exception as norm_err:
                normalization_error = str(norm_err)
                logger.warning("[CHANNELS] Failed to normalize channel name '%s': %s", request.name, norm_err)
                # Continue with the original name, and disclose it below.

        data = {"name": channel_name}
        if request.channel_number is not None:
            data["channel_number"] = request.channel_number
        if request.channel_group_id is not None:
            data["channel_group_id"] = request.channel_group_id
        if request.logo_id is not None:
            data["logo_id"] = request.logo_id
        if request.tvg_id is not None:
            data["tvg_id"] = request.tvg_id
        start = time.time()
        result = await client.create_channel(data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Created channel via API in %.1fms", elapsed_ms)
        logger.info("[CHANNELS] Created channel id=%s name=%s number=%s", result.get('id'), result.get('name'), result.get('channel_number'))

        # Say whether the normalization the caller asked for actually ran
        # (bead enhancedchannelmanager-e9e5o). Present ONLY when `normalize`
        # was requested, so a caller that never asked for it sees exactly the
        # response body it saw before. `applied` is the flag to branch on:
        # False means the engine did not run and `nameApplied` is the raw name.
        if request.normalize and isinstance(result, dict):
            result["normalization"] = {
                "requested": True,
                "applied": normalization_error is None,
                "nameApplied": result.get("name", channel_name),
                "error": normalization_error,
            }

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="create",
            entity_id=result.get("id"),
            entity_name=result.get("name", "Unknown"),
            description=f"Created channel '{result.get('name')}'" + (f" with number {result.get('channel_number')}" if result.get('channel_number') else ""),
            after_value={"channel_number": result.get("channel_number"), "name": result.get("name")},
        )

        return result
    except HTTPException:
        raise
    except Exception as e:
        # Surface actionable Dispatcharr 4xx (e.g. non-existent channel_group_id)
        # instead of masking it as a generic 500 (bd-1wq7z.22).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Channel creation rejected by Dispatcharr: %s", e)
            raise mapped
        logger.exception("[CHANNELS] Channel creation failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Logos — MUST be defined before /api/channels/{channel_id} routes
# ---------------------------------------------------------------------------

# Fallback browser-cache policy for the logo image proxy when Dispatcharr
# sends no Cache-Control of its own (live-observed it sends 3600s for
# remote-origin logos / 14400s for local files). Long max-age is safe because
# the rewritten cache_url preserves Dispatcharr's ?v=<hash> cache-buster, so
# a replaced logo gets a new URL. Without browser caching every channel-list
# render would round-trip ECM -> Dispatcharr once per logo.
_LOGO_IMAGE_DEFAULT_CACHE_CONTROL = "public, max-age=86400"


def _ecm_origin(request: Request) -> str:
    """Browser-facing origin (``scheme://host[:port]``) for absolute ECM URLs.

    Honors ``X-Forwarded-Proto`` / ``X-Forwarded-Host`` (first value of a
    comma list wins) so reverse-proxied deployments produce the public
    scheme/host — mirroring main.py's existing X-Forwarded-For convention —
    and otherwise falls back to the request's own scheme and Host header.
    The Host fallback is browser-reachable by construction: it is the host
    the browser itself dialed. Always yields an http(s) URL, which matters
    because LogoModal.tsx's preview sanitizer silently drops any other
    scheme (GH #662 / bead enhancedchannelmanager-hhmat).
    """
    proto = request.headers.get("x-forwarded-proto")
    scheme = (proto.split(",")[0].strip() if proto else request.url.scheme) or "http"
    if scheme not in ("http", "https"):
        scheme = "http"
    fwd_host = request.headers.get("x-forwarded-host")
    host = fwd_host.split(",")[0].strip() if fwd_host else request.url.netloc
    return f"{scheme}://{host}"


def _rewrite_logo_cache_url(logo, origin: str):
    """Point a logo dict's ``cache_url`` at ECM's same-origin image proxy.

    Dispatcharr builds ``cache_url`` from the Host header it saw on ECM's
    server-side request — often a docker-internal hostname or LAN IP the
    operator's browser cannot resolve, which breaks every logo (GH #662).
    Rewriting to ``{origin}/api/channels/logos/{id}/image`` keeps the bytes
    flowing through ECM's authenticated upstream client instead.

    Preserves Dispatcharr's ``?v=<hash>`` cache-buster so long browser
    caching stays correct across logo replacements. Leaves a falsy
    ``cache_url`` untouched (the frontend then falls back to ``logo.url``,
    which may be a browser-reachable external URL) and never touches
    ``url`` itself. Mutates and returns ``logo``.
    """
    if not isinstance(logo, dict):
        return logo
    logo_id = logo.get("id")
    cache_url = logo.get("cache_url")
    if logo_id is None or not cache_url:
        return logo
    version = parse_qs(urlsplit(str(cache_url)).query).get("v", [None])[0]
    suffix = f"?v={quote(version, safe='')}" if version else ""
    logo["cache_url"] = f"{origin}/api/channels/logos/{logo_id}/image{suffix}"
    return logo


@router.get("/logos")
async def get_logos(
    request: Request,
    # Bounds enforced here (bead enhancedchannelmanager-g4z2h, systemic sibling
    # of 1a5mf): page<1 / page_size<1 were passed straight to the upstream
    # Dispatcharr client, which raised and surfaced as a 500. Upper bound
    # matches sibling get_channels (channels.py) — App.tsx's getAllLogos()
    # direct calls legitimately request page_size=10000 (services/api.ts).
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    page_size: int = Query(100, ge=1, le=10000, description="Results per page"),
    search: Optional[str] = None,
    sort_by: Optional[Literal["name", "channel_count"]] = Query(
        None, description="Column to sort by. Requires ECM-side aggregation (see below)."
    ),
    sort_order: Literal["asc", "desc"] = Query("asc", description="Sort direction"),
    unused_only: bool = Query(False, description="Only return logos with channel_count == 0"),
):
    """List logos with pagination, search, sort, and an unused-only filter.

    Emits a single-line INFO diagnostic per request (bd-nh50y) so operators
    can grep one log line per request and see page/page_size/search/result
    count/elapsed/next-flag without enabling DEBUG. Per-row logging is
    intentionally avoided — at page_size=500 this is the channel-edit-modal
    fetch path and per-row would flood the log.

    Sort/filter (bead enhancedchannelmanager-09x38.13): Dispatcharr's
    LogoViewSet has no ordering support and its ``search`` query param is a
    no-op (only ``name``/``used``/``ids`` are read — confirmed by reading
    apps/channels/api_views.py in the live dispatcharr container). So when
    ``sort_by``, ``unused_only``, or a non-empty ``search`` is requested,
    this endpoint fetches the COMPLETE logo list from Dispatcharr in one
    call (client.get_all_logos_raw(), via Dispatcharr's `no_pagination=true`
    escape hatch) and sorts/filters/paginates it locally in Python — this
    is what makes sort and the unused-only filter truthful across pages.
    Requests using none of those three params take the original zero-
    overhead single-Dispatcharr-page passthrough (unchanged, backward
    compatible with LogoModal's picker, AutoSyncSettingsModal, GuideTab,
    and getAllLogos()).
    """
    logger.debug("[CHANNELS-LOGO] GET /channels/logos - page=%s search=%s", page, search)
    client = get_client()
    try:
        start = time.time()
        if sort_by is not None or unused_only or search:
            all_logos = await client.get_all_logos_raw()
            if search:
                search_lower = search.lower()
                all_logos = [
                    logo for logo in all_logos
                    if search_lower in (logo.get("name") or "").lower()
                ]
            if unused_only:
                all_logos = [logo for logo in all_logos if (logo.get("channel_count") or 0) == 0]

            reverse = sort_order == "desc"
            if sort_by == "channel_count":
                all_logos.sort(key=lambda logo: logo.get("channel_count") or 0, reverse=reverse)
            else:
                all_logos.sort(key=lambda logo: (logo.get("name") or "").lower(), reverse=reverse)

            total = len(all_logos)
            start_idx = (page - 1) * page_size
            page_items = all_logos[start_idx:start_idx + page_size]
            result = {
                "count": total,
                "next": "true" if start_idx + page_size < total else None,
                "previous": "true" if page > 1 else None,
                "results": page_items,
            }
        else:
            result = await client.get_logos(page=page, page_size=page_size, search=search)
        # Same-origin logo proxy rewrite (GH #662 / bead hhmat): see
        # _rewrite_logo_cache_url. Applied to both branches so the
        # passthrough and aggregate-and-sort paths stay consistent.
        origin = _ecm_origin(request)
        if isinstance(result, dict):
            for logo in result.get("results", []):
                _rewrite_logo_cache_url(logo, origin)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-LOGO] Fetched logos in %.1fms", elapsed_ms)
        # Single-line INFO diagnostic for the operator-grepable trace (bd-nh50y).
        # Pulls result-count from `results` (DRF paginated shape) and `next` from
        # the paginated envelope. Both can be missing in unusual responses, so
        # default defensively rather than KeyError on a malformed payload.
        results_count = (
            len(result.get("results", []))
            if isinstance(result, dict)
            else 0
        )
        has_next = bool(result.get("next")) if isinstance(result, dict) else False
        logger.info(
            "[CHANNELS-LOGO] GET /logos page=%s page_size=%s search=%s "
            "returned %s logos in %.1fms next=%s",
            page,
            page_size,
            search,
            results_count,
            elapsed_ms,
            "true" if has_next else "false",
        )
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to fetch logos: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logos/{logo_id}")
async def get_logo(logo_id: int, request: Request):
    """Get a single logo by ID."""
    logger.debug("[CHANNELS-LOGO] GET /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_logo(logo_id)
        _rewrite_logo_cache_url(result, _ecm_origin(request))
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-LOGO] Fetched logo id=%s in %.1fms", logo_id, elapsed_ms)
        return result
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to fetch logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/logos/{logo_id}/image")
async def get_logo_image(logo_id: int, request: Request):
    """Proxy the logo image bytes from Dispatcharr's cache endpoint.

    Browsers frequently cannot reach the host in Dispatcharr's ``cache_url``
    (GH #662 / bead enhancedchannelmanager-hhmat): Dispatcharr builds it from
    the Host header it saw on ECM's server-side request, which is often a
    docker-internal hostname or LAN IP. get_logos/get_logo rewrite
    ``cache_url`` to point here; this endpoint fetches the bytes server-side
    over ECM's authenticated Dispatcharr client and serves them same-origin.

    Live-observed upstream behavior (2026-07-18, against the deployed
    Dispatcharr): ``/api/channels/logos/{id}/cache/`` returns 200 + bytes for
    BOTH local-file and external-URL logos (it downloads and caches remote
    originals server-side), 404 JSON for unknown ids, emits Cache-Control
    (3600s remote / 14400s local) and no ETag, and never redirects.
    Cache-Control and any future ETag pass through; ``If-None-Match``
    forwards upstream so a 304 can short-circuit.

    Auth: intentionally NOT in main.py's AUTH_EXEMPT_PATHS — same-origin
    ``<img>`` tags send the httpOnly ``access_token`` cookie, so the global
    auth middleware admits real browser sessions without any frontend change.
    """
    logger.debug("[CHANNELS-LOGO] GET /channels/logos/%s/image", logo_id)
    client = get_client()
    upstream_headers = {}
    if_none_match = request.headers.get("if-none-match")
    if if_none_match:
        upstream_headers["If-None-Match"] = if_none_match
    try:
        upstream = await client._request(
            "GET",
            f"/api/channels/logos/{logo_id}/cache/",
            headers=upstream_headers,
        )
    except Exception as e:
        # Log only the exception TYPE — httpx error text can embed the full
        # request URL (same hygiene rule as dispatcharr_client._request).
        logger.warning(
            "[CHANNELS-LOGO] Logo image proxy transport failure id=%s: %s",
            logo_id,
            type(e).__name__,
        )
        raise HTTPException(
            status_code=502, detail="Failed to fetch logo image from Dispatcharr"
        )

    if upstream.status_code == 304:
        return Response(status_code=304)
    if upstream.status_code == 404:
        raise HTTPException(status_code=404, detail="Logo image not found")
    if upstream.status_code >= 400:
        logger.warning(
            "[CHANNELS-LOGO] Logo image upstream error id=%s status=%s",
            logo_id,
            upstream.status_code,
        )
        raise HTTPException(status_code=502, detail="Upstream logo fetch failed")

    headers = {
        "Cache-Control": upstream.headers.get("cache-control")
        or _LOGO_IMAGE_DEFAULT_CACHE_CONTROL,
    }
    etag = upstream.headers.get("etag")
    if etag:
        headers["ETag"] = etag
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type") or "application/octet-stream",
        headers=headers,
    )


@router.post("/logos")
async def create_logo(
    request: CreateLogoRequest, http_request: Request, _admin=RequireAdminIfEnabled
):
    """Create a logo from a URL. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS-LOGO] POST /channels/logos - name=%s", request.name)
    client = get_client()
    try:
        start = time.time()
        result = await client.create_logo({"name": request.name, "url": request.url})
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Created logo id=%s name=%s in %.1fms", result.get('id'), result.get('name'), elapsed_ms)
        # Rewrite so a just-created logo renders immediately — ChannelsPane
        # and AutoSyncSettingsModal consume this response object directly
        # (GH #662 / bead hhmat).
        return _rewrite_logo_cache_url(result, _ecm_origin(http_request))
    except Exception as e:
        error_str = str(e)
        # Check if this is a "logo already exists" error from Dispatcharr
        if "logo with this url already exists" in error_str.lower() or "400" in error_str:
            try:
                existing_logo = await client.find_logo_by_url(request.url)
                if existing_logo:
                    logger.info("[CHANNELS-LOGO] Found existing logo id=%s name=%s", existing_logo.get('id'), existing_logo.get('name'))
                    return _rewrite_logo_cache_url(
                        existing_logo, _ecm_origin(http_request)
                    )
                else:
                    logger.warning("[CHANNELS-LOGO] Logo exists but could not find it by URL: %s", request.url)
            except Exception as search_err:
                logger.error("[CHANNELS-LOGO] Error searching for existing logo: %s", search_err)
        logger.exception("[CHANNELS-LOGO] Logo creation failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/logos/upload")
async def upload_logo(request: Request, file: UploadFile = File(...), _admin=RequireAdminIfEnabled):
    """Upload a logo image file directly to Dispatcharr. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS-LOGO] POST /channels/logos/upload - filename=%s", file.filename)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    contents = await file.read()
    name = os.path.splitext(file.filename or "logo")[0]
    client = get_client()
    try:
        start = time.time()
        result = await client.upload_logo_file(
            name=name,
            filename=file.filename or "logo.png",
            content=contents,
            content_type=file.content_type,
        )
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Uploaded logo id=%s name=%s in %.1fms", result.get('id'), name, elapsed_ms)
        # Rewrite so a just-uploaded logo renders immediately (GH #662).
        return _rewrite_logo_cache_url(result, _ecm_origin(request))
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Logo upload failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/logos/{logo_id}")
async def update_logo(
    logo_id: int, data: dict, request: Request, _admin=RequireAdminIfEnabled
):
    """Update a logo. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS-LOGO] PATCH /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.update_logo(logo_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Updated logo id=%s in %.1fms", logo_id, elapsed_ms)
        # Rewrite so the edited logo renders immediately (GH #662).
        return _rewrite_logo_cache_url(result, _ecm_origin(request))
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to update logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/logos/{logo_id}")
async def delete_logo(logo_id: int, _admin=RequireAdminIfEnabled):
    """Delete a logo. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS-LOGO] DELETE /channels/logos/%s", logo_id)
    client = get_client()
    try:
        start = time.time()
        await client.delete_logo(logo_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-LOGO] Deleted logo id=%s in %.1fms", logo_id, elapsed_ms)
        return {"success": True}
    except Exception as e:
        logger.exception("[CHANNELS-LOGO] Failed to delete logo id=%s", logo_id)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# CSV Import/Export — MUST be defined before /api/channels/{channel_id} routes
# ---------------------------------------------------------------------------

@router.get("/csv-template")
async def get_csv_template():
    """Download CSV template for channel import."""
    logger.debug("[CHANNELS-CSV] GET /channels/csv-template")
    template_content = generate_template()
    return Response(
        content=template_content,
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=channel-import-template.csv"
        }
    )


@router.get("/export-csv")
async def export_channels_csv():
    """Export all channels to CSV format."""
    logger.debug("[CHANNELS-CSV] GET /channels/export-csv")
    client = get_client()
    try:
        # Fetch channel groups to build ID -> name lookup
        start = time.time()
        groups = await client.get_channel_groups()
        group_lookup = {g.get("id"): g.get("name", "") for g in groups}

        # Fetch all channels (handle pagination)
        all_channels = []
        page = 1
        page_size = 100
        while True:
            result = await client.get_channels(page=page, page_size=page_size)
            channels = result.get("results", [])
            all_channels.extend(channels)
            if not result.get("next"):
                break
            page += 1

        # Filter out auto-created channels and sort by channel number ascending
        manual_channels = [ch for ch in all_channels if not ch.get("auto_created", False)]
        manual_channels.sort(key=lambda ch: ch.get("channel_number", 0) or 0)

        # Collect all stream IDs from channels
        all_stream_ids = set()
        for ch in manual_channels:
            stream_ids = ch.get("streams", [])
            all_stream_ids.update(stream_ids)

        # Fetch stream details to get URLs (batch by 100)
        stream_url_lookup = {}
        stream_ids_list = list(all_stream_ids)
        for i in range(0, len(stream_ids_list), 100):
            batch = stream_ids_list[i:i+100]
            if batch:
                try:
                    streams = await client.get_streams_by_ids(batch)
                    for s in streams:
                        stream_url_lookup[s.get("id")] = s.get("url", "")
                except Exception as e:
                    logger.warning("[CHANNELS-CSV] Failed to fetch stream batch: %s", e)

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-CSV] Fetched export data (%s channels, %s streams) in %.1fms", len(all_channels), len(stream_url_lookup), elapsed_ms)

        # Transform channel data for CSV export
        csv_channels = []
        for ch in manual_channels:
            group_id = ch.get("channel_group_id")
            group_name = group_lookup.get(group_id, "") if group_id else ""

            # Get stream URLs for this channel
            stream_ids = ch.get("streams", [])
            stream_urls = [stream_url_lookup.get(sid, "") for sid in stream_ids if stream_url_lookup.get(sid)]
            stream_urls_str = ";".join(stream_urls) if stream_urls else ""

            csv_channels.append({
                "channel_number": ch.get("channel_number"),
                "name": ch.get("name", ""),
                "group_name": group_name,
                "tvg_id": ch.get("tvg_id", ""),
                "gracenote_id": ch.get("tvc_guide_stationid", ""),
                "logo_url": ch.get("logo_url", ""),
                "stream_urls": stream_urls_str
            })

        csv_content = generate_csv(csv_channels)
        filename = f"channels-export-{date.today().isoformat()}.csv"

        return Response(
            content=csv_content,
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename={filename}"
            }
        )
    except Exception as e:
        logger.exception("[CHANNELS-CSV] CSV export failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/import-csv")
async def import_channels_csv(file: UploadFile = File(...), _admin=RequireAdminIfEnabled):
    """Import channels from CSV file. Admin only (bulk operator op, bd-um30y)."""
    logger.debug("[CHANNELS-CSV] POST /channels/import-csv - filename=%s", file.filename)
    client = get_client()

    # Read and decode the file
    try:
        content = await file.read()
        csv_content = content.decode("utf-8")
    except Exception as e:
        logger.warning("[CHANNELS-CSV] Failed to read uploaded file: %s", e)
        raise HTTPException(status_code=400, detail="Failed to read file")

    # Parse CSV
    try:
        rows, parse_errors = parse_csv(csv_content)
    except CSVParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not rows and not parse_errors:
        # Empty file or header only
        return {
            "success": True,
            "channels_created": 0,
            "groups_created": 0,
            "errors": [],
            "warnings": []
        }

    # Get existing channel groups for matching
    try:
        start = time.time()
        existing_groups = await client.get_channel_groups()
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS-CSV] Fetched channel groups in %.1fms", elapsed_ms)
        group_map = {g["name"].lower(): g for g in existing_groups}
    except Exception as e:
        logger.error("[CHANNELS-CSV] Failed to fetch channel groups: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch channel groups")

    # Build URL -> stream ID lookup for stream linking
    stream_url_to_id = {}
    try:
        start = time.time()
        page = 1
        page_size = 500
        while True:
            result = await client.get_streams(page=page, page_size=page_size)
            streams = result.get("results", [])
            for s in streams:
                url = s.get("url", "")
                if url:
                    stream_url_to_id[url] = s.get("id")
            if not result.get("next"):
                break
            page += 1
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-CSV] Built stream URL lookup with %s streams in %.1fms", len(stream_url_to_id), elapsed_ms)
    except Exception as e:
        logger.warning("[CHANNELS-CSV] Failed to fetch streams for URL lookup: %s", e)

    # Build EPG tvg_id -> icon_url lookup for logo assignment
    epg_tvg_id_to_icon = {}
    epg_name_to_icon = {}
    try:
        start = time.time()
        epg_data = await client.get_epg_data()
        for entry in epg_data:
            tvg_id = entry.get("tvg_id", "")
            icon_url = entry.get("icon_url", "")
            name = entry.get("name", "")
            if tvg_id and icon_url:
                epg_tvg_id_to_icon[tvg_id.lower()] = icon_url
            if name and icon_url:
                # Normalize name for matching (lowercase, strip common suffixes)
                normalized_name = name.lower().strip()
                for suffix in [" hd", " sd", " (hd)", " (sd)"]:
                    if normalized_name.endswith(suffix):
                        normalized_name = normalized_name[:-len(suffix)]
                epg_name_to_icon[normalized_name] = icon_url
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS-CSV] Built EPG logo lookup with %s tvg_id entries and %s name entries in %.1fms", len(epg_tvg_id_to_icon), len(epg_name_to_icon), elapsed_ms)
    except Exception as e:
        logger.warning("[CHANNELS-CSV] Failed to fetch EPG data for logo lookup: %s", e)

    channels_created = 0
    groups_created = 0
    streams_linked = 0
    logos_from_epg = 0
    errors = parse_errors.copy()
    warnings = []

    # Process each valid row
    for i, row in enumerate(rows):
        row_num = i + 2  # Account for header row

        try:
            # Handle group creation/lookup
            group_id = None
            group_name = row.get("group_name", "").strip()
            if group_name:
                group_key = group_name.lower()
                if group_key in group_map:
                    group_id = group_map[group_key]["id"]
                else:
                    # Create new group
                    try:
                        new_group = await client.create_channel_group(group_name)
                        group_id = new_group["id"]
                        group_map[group_key] = new_group
                        groups_created += 1
                        logger.info("[CHANNELS-CSV] Created channel group: %s", group_name)
                    except Exception as ge:
                        logger.warning("[CHANNELS-CSV] Row %s: Failed to create group '%s': %s", row_num, group_name, ge)
                        warnings.append(f"Row {row_num}: Failed to create group '{group_name}'")

            # Build channel data
            channel_data = {
                "name": row["name"],
            }

            # Add optional fields
            channel_number = row.get("channel_number", "").strip()
            if channel_number:
                # `validate_channel_row` already rejected out-of-contract values
                # before the row reached this loop, so this parse re-uses the
                # same canonical function rather than a second, looser `float()`
                # (bead enhancedchannelmanager-ic884.1).
                parsed_number = parse_channel_number_text(channel_number)
                if parsed_number is not None:
                    channel_data["channel_number"] = parsed_number

            if group_id:
                channel_data["channel_group_id"] = group_id

            tvg_id = row.get("tvg_id", "").strip()
            if tvg_id:
                channel_data["tvg_id"] = tvg_id

            gracenote_id = row.get("gracenote_id", "").strip()
            if gracenote_id:
                channel_data["tvc_guide_stationid"] = gracenote_id

            logo_url = row.get("logo_url", "").strip()
            if logo_url:
                channel_data["logo_url"] = logo_url

            # Create the channel
            created_channel = await client.create_channel(channel_data)
            channels_created += 1

            # If no logo_url provided, try to get one from EPG data
            if not logo_url and created_channel:
                epg_icon_url = None
                # First try tvg_id match
                if tvg_id:
                    epg_icon_url = epg_tvg_id_to_icon.get(tvg_id.lower())
                # Fall back to name match
                if not epg_icon_url:
                    channel_name = row["name"].lower().strip()
                    # Try exact match first
                    epg_icon_url = epg_name_to_icon.get(channel_name)
                    # Try without HD/SD suffix
                    if not epg_icon_url:
                        for suffix in [" hd", " sd", " (hd)", " (sd)"]:
                            if channel_name.endswith(suffix):
                                channel_name = channel_name[:-len(suffix)]
                                epg_icon_url = epg_name_to_icon.get(channel_name)
                                break

                if epg_icon_url:
                    try:
                        channel_id = created_channel.get("id")
                        channel_name_for_logo = row["name"]
                        # Find existing logo by URL or create new one
                        existing_logo = await client.find_logo_by_url(epg_icon_url)
                        if existing_logo:
                            logo_id = existing_logo["id"]
                            logger.debug("[CHANNELS-CSV] Row %s: Found existing logo ID %s for EPG icon", row_num, logo_id)
                        else:
                            new_logo = await client.create_logo({"name": channel_name_for_logo, "url": epg_icon_url})
                            logo_id = new_logo["id"]
                            logger.debug("[CHANNELS-CSV] Row %s: Created new logo ID %s for EPG icon", row_num, logo_id)
                        # Update channel with logo_id
                        await client.update_channel(channel_id, {"logo_id": logo_id})
                        logos_from_epg += 1
                        logger.debug("[CHANNELS-CSV] Row %s: Assigned EPG logo to channel '%s'", row_num, row['name'])
                    except Exception as le:
                        logger.warning("[CHANNELS-CSV] Row %s: Failed to assign EPG logo: %s", row_num, le)
                        warnings.append(f"Row {row_num}: Failed to assign EPG logo")

            # Handle stream linking if stream_urls provided
            stream_urls_str = row.get("stream_urls", "").strip()
            if stream_urls_str and created_channel:
                stream_urls = [url.strip() for url in stream_urls_str.split(";") if url.strip()]
                stream_ids = []
                for url in stream_urls:
                    stream_id = stream_url_to_id.get(url)
                    if stream_id:
                        stream_ids.append(stream_id)
                    else:
                        warnings.append(f"Row {row_num}: Stream URL not found: {url[:50]}...")

                if stream_ids:
                    try:
                        channel_id = created_channel.get("id")
                        await client.update_channel(channel_id, {"streams": stream_ids})
                        streams_linked += len(stream_ids)
                    except Exception as se:
                        logger.warning("[CHANNELS-CSV] Row %s: Failed to link streams: %s", row_num, se)
                        warnings.append(f"Row {row_num}: Failed to link streams")

        except Exception as e:
            logger.warning("[CHANNELS-CSV] Row %s import error: %s", row_num, e)
            errors.append({"row": row_num, "error": "Failed to import row"})

    # Log the import
    logger.info("[CHANNELS-CSV] Import completed: %s channels created, %s groups created, %s streams linked, %s logos from EPG, %s errors", channels_created, groups_created, streams_linked, logos_from_epg, len(errors))

    return {
        "success": len(errors) == 0,
        "channels_created": channels_created,
        "groups_created": groups_created,
        "streams_linked": streams_linked,
        "logos_from_epg": logos_from_epg,
        "errors": errors,
        "warnings": warnings
    }


@router.post("/preview-csv")
async def preview_csv(data: dict):
    """Preview CSV content and validate before import."""
    logger.debug("[CHANNELS-CSV] POST /channels/preview-csv")
    content = data.get("content", "")
    if not content:
        return {"rows": [], "errors": []}

    try:
        rows, errors = parse_csv(content)
        # Convert rows to list of dicts for JSON response
        return {
            "rows": rows,
            "errors": errors
        }
    except CSVParseError as e:
        logger.warning("[CHANNELS-CSV] CSV parse error: %s", e)
        return {
            "rows": [],
            "errors": [{"row": 1, "error": "Failed to parse CSV"}]
        }


# ---------------------------------------------------------------------------
# Static bulk routes — MUST be defined before /api/channels/{channel_id}
# ---------------------------------------------------------------------------

@router.post("/assign-numbers")
async def assign_channel_numbers(request: AssignNumbersRequest, _admin=RequireAdminIfEnabled):
    """Bulk assign channel numbers. Admin only (bulk operator op, bd-um30y)."""
    logger.debug("[CHANNELS] POST /channels/assign-numbers - %s channels starting_number=%s", len(request.channel_ids), request.starting_number)
    client = get_client()
    settings = get_settings()

    try:
        # Get current channel data for all affected channels (needed for journal and auto-rename)
        start = time.time()
        batch_id = str(uuid.uuid4())[:8]
        channels_before = {}
        name_updates = {}

        for idx, channel_id in enumerate(request.channel_ids):
            channel = await client.get_channel(channel_id)
            channels_before[channel_id] = {
                "name": channel.get("name", ""),
                "channel_number": channel.get("channel_number"),
            }

            # If auto-rename is enabled, calculate name updates
            if settings.auto_rename_channel_number and request.starting_number is not None:
                old_number = channel.get("channel_number")
                new_number = request.starting_number + idx
                channel_name = channel.get("name", "")

                if old_number is not None and old_number != new_number and channel_name:
                    # Check if channel name contains the old number
                    old_number_str = str(int(old_number) if old_number == int(old_number) else old_number)
                    new_number_str = str(int(new_number) if new_number == int(new_number) else new_number)
                    # Match the number as a standalone value (not part of a larger number)
                    pattern = re.compile(r'(^|[^0-9])' + re.escape(old_number_str) + r'([^0-9]|$)')
                    if pattern.search(channel_name):
                        new_name = pattern.sub(r'\g<1>' + new_number_str + r'\g<2>', channel_name)
                        if new_name != channel_name:
                            name_updates[channel_id] = new_name

        # Call the bulk assign API
        result = await client.assign_channel_numbers(
            request.channel_ids, request.starting_number
        )

        # Apply name updates if any
        for channel_id, new_name in name_updates.items():
            try:
                await client.update_channel(channel_id, {"name": new_name})
            except Exception as e:
                logger.warning("[CHANNELS] Failed to update name for channel %s: %s", channel_id, e)

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Assigned numbers to %s channels in %.1fms", len(request.channel_ids), elapsed_ms)

        # Log individual journal entries for each channel
        for idx, channel_id in enumerate(request.channel_ids):
            before_data = channels_before.get(channel_id, {})
            old_number = before_data.get("channel_number")
            new_number = request.starting_number + idx
            channel_name = before_data.get("name", f"Channel {channel_id}")
            new_name = name_updates.get(channel_id, channel_name)

            journal.log_entry(
                category="channel",
                action_type="reorder",
                entity_id=channel_id,
                entity_name=channel_name,
                description=(
                    f"Changed channel number from {format_channel_number(old_number)} "
                    f"to {format_channel_number(new_number)}"
                ),
                before_value={"channel_number": old_number, "name": channel_name},
                after_value={"channel_number": new_number, "name": new_name},
                batch_id=batch_id,
            )

        return result
    except Exception as e:
        logger.exception("[CHANNELS] Failed to assign channel numbers: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


#: The fields of :class:`BulkUpdateChannelOp` that describe the
#: ``channel_number`` write and NOTHING else, so they must be dropped along
#: with it when consolidation finds that a later operation of another kind owns
#: the channel's final number.
#:
#: Named, and pinned against the model's own field list by
#: ``test_consolidate_operations``, because the alternative is a literal pair of
#: attribute names inside a branch — and this function has already lost a field
#: twice by having to remember one. A field added to the model now has to be
#: classified as number-scoped or not, rather than silently defaulting to
#: "rides through", which for a number-scoped field would mean carrying consent
#: for a placement that no longer happens.
_NUMBER_SCOPED_UPDATE_FIELDS = ("acknowledgedDuplicate", "expectedNumber")


def _consolidate_operations(operations: list[BulkOperation]) -> list[BulkOperation]:
    """Consolidate redundant operations to minimize API calls.

    Optimizations:
    - Multiple updateChannel for same channel -> single update with merged data
    - Multiple bulkAssignChannelNumbers -> single call with final positions
    - Add then remove same stream -> both cancelled
    - Multiple reorderChannelStreams for same channel -> only final order kept
    - Operations targeting channels to be deleted are removed
    - Create + delete of same temp channel cancel out

    LAST WRITE WINS, AND "LAST" IS BY SUBMITTED POSITION RATHER THAN BY KIND.
    The output groups operations by kind — merged updates, then range
    assignments — so the order they are EMITTED in says nothing about the order
    they were SENT in. That destroyed the caller's ordering between kinds:
    ``bulkAssignChannelNumbers([1], 10)`` followed by
    ``updateChannel(1, {"channel_number": 20})`` emitted the update first and
    the range last, so a plan whose final state is 20 — which is what both
    materialisers in ``channel_number_plan.py`` and ``channelNumberPlan.ts``
    preview, and therefore what the operator was shown — was validated and
    applied as 10.

    So the channel's final number is resolved ONCE here, by submitted index,
    across every kind that sets one, and the losing operation has the number
    taken off it. Exactly one operation in the output writes any given
    channel's number, which is what makes the emission order unable to change
    the answer.
    """
    start = time.time()
    original_count = len(operations)

    # First pass: find channels to be deleted and temp channels to be created.
    # Both sets are computed up front so that create+delete cancellation is
    # order-independent (a deleteChannel may precede its matching createChannel).
    channels_to_delete: set[int] = set()
    channels_to_create: set[int] = set()
    for op in operations:
        if op.type == "deleteChannel":
            channels_to_delete.add(op.channelId)
        elif op.type == "createChannel":
            channels_to_create.add(op.tempId)

    # Track final state for each operation type
    channel_final_updates: dict[int, dict] = {}  # channelId -> merged data
    # The op each merged update is REBUILT FROM, so that everything on it other
    # than ``data`` rides through untouched.
    #
    # This function has now lost two different things by rebuilding from parts:
    # whole operation types it had not enumerated, and then
    # ``acknowledgedDuplicate``, which meant every duplicate an operator
    # explicitly confirmed reached the preflight looking accidental — on the
    # default path, because the frontend always sends ``consolidate: true``. A
    # constructor call has to remember every field and forgets in silence, so
    # the merged op is a COPY of a real one with ``data`` replaced.
    #
    # Which one to copy is a semantic choice, not an arbitrary one. Merging
    # takes ``channel_number`` from the last operation that set it, so the
    # acknowledgement that belongs beside it is that operation's: consent is to
    # one placement, and a later name-only edit neither grants nor withdraws
    # it. Hence the number-setting op wins, and the last op is only the
    # fallback for a channel whose number nothing touched.
    channel_update_last: dict[int, BulkUpdateChannelOp] = {}
    channel_update_number_source: dict[int, BulkUpdateChannelOp] = {}
    channel_final_numbers: dict[int, float] = {}  # channelId -> final number
    # WHICH KIND OF OPERATION OWNS EACH CHANNEL'S FINAL NUMBER: channelId ->
    # the operation type of the LAST submitted operation that set it. Written
    # by every kind that places a channel on a number, so the winner is decided
    # by position in the caller's list rather than by the order this function
    # happens to emit its groups in. See the note in the docstring.
    #
    # ``createChannel`` records itself even when it carries no number, because
    # both materialisers treat an operation naming a channel that does not
    # exist YET as a no-op: a range assignment sent before the create it names
    # places nothing, and consolidation must not turn it into a placement.
    channel_number_owner: dict[int, str] = {}
    channel_final_stream_order: dict[int, list[int]] = {}  # channelId -> final stream IDs
    stream_ops: dict[str, dict] = {}  # "channelId:streamId" -> {added: op, removed: op}
    ordered_ops: list[BulkOperation] = []  # create/delete ops in order

    for op in operations:
        if op.type == "bulkAssignChannelNumbers":
            # An omitted start is 1, which is what the frontend materialiser,
            # the backend materialiser and the executor all already say. It was
            # 0 here — via ``or``, which also collapsed an explicit 0 into the
            # same branch — so an omitted start previewed as 1 in the browser
            # and validated and applied as 0. An EXPLICIT 0 is a real request
            # and is honoured: ic884.1 settled channel numbers as non-negative,
            # so zero is in contract.
            start_num = 1 if op.startingNumber is None else op.startingNumber
            for i, cid in enumerate(op.channelIds):
                if cid not in channels_to_delete:
                    channel_final_numbers[cid] = start_num + i
                    channel_number_owner[cid] = "bulkAssignChannelNumbers"

        elif op.type == "updateChannel":
            if op.channelId not in channels_to_delete:
                existing = channel_final_updates.get(op.channelId, {})
                existing.update(op.data)
                channel_final_updates[op.channelId] = existing
                channel_update_last[op.channelId] = op
                if "channel_number" in op.data:
                    channel_update_number_source[op.channelId] = op
                    channel_number_owner[op.channelId] = "updateChannel"

        elif op.type == "reorderChannelStreams":
            if op.channelId not in channels_to_delete:
                channel_final_stream_order[op.channelId] = op.streamIds

        elif op.type == "addStreamToChannel":
            if op.channelId not in channels_to_delete:
                key = f"{op.channelId}:{op.streamId}"
                entry = stream_ops.setdefault(key, {"added": None, "removed": None})
                entry["added"] = op

        elif op.type == "removeStreamFromChannel":
            if op.channelId not in channels_to_delete:
                key = f"{op.channelId}:{op.streamId}"
                entry = stream_ops.setdefault(key, {"added": None, "removed": None})
                entry["removed"] = op

        elif op.type == "createChannel":
            # Create + delete of the same temp channel cancel out.
            if op.tempId not in channels_to_delete:
                ordered_ops.append(op)
                channel_number_owner[op.tempId] = "createChannel"

        elif op.type == "deleteChannel":
            if op.channelId < 0 and op.channelId in channels_to_create:
                pass  # Create + delete cancel out (order-independent)
            else:
                ordered_ops.append(op)

        else:
            # Everything with nothing to fold — group ops, and the operations
            # added so Edit Mode can stage profile visibility, hidden-group
            # restore and stream-stat clears (bead
            # enhancedchannelmanager-kz089) — passes through in order.
            #
            # This is deliberately a catch-all rather than another explicit
            # tuple. The frontend always sends `consolidate: true`, so an op
            # type this function did not enumerate was DROPPED here: the
            # operator saw it staged, saw it counted, saw the commit succeed,
            # and the change never left the browser. A pass-through default
            # cannot lose an operation; the worst it can do is fail to
            # optimise one.
            ordered_ops.append(op)

    # Build consolidated list
    consolidated: list[BulkOperation] = list(ordered_ops)

    # Merged updateChannel ops. Copied from a real operation, never rebuilt —
    # see the note beside `channel_update_last`.
    for cid, data in channel_final_updates.items():
        merged = dict(data)
        overrides: dict = {}
        if "channel_number" in merged and channel_number_owner.get(cid) != "updateChannel":
            # A LATER operation of a different kind places this channel, so
            # this update's number is superseded and must not be written. The
            # bookkeeping that describes that write goes with it: an
            # acknowledgement is consent to one placement, and the placement it
            # consented to is not the one that happens.
            merged.pop("channel_number")
            overrides = dict.fromkeys(_NUMBER_SCOPED_UPDATE_FIELDS)
        if not merged:
            # Nothing left to PATCH. The number this operation existed to write
            # is written by another operation in this same list.
            continue
        template = channel_update_number_source.get(cid) or channel_update_last[cid]
        consolidated.append(template.model_copy(update={"data": merged, **overrides}))

    # Consolidated bulkAssign: group into consecutive ranges. This is the one
    # arm that genuinely cannot copy a single input op — it regroups several
    # into consecutive ranges — and it stays safe only while
    # `BulkAssignNumbersOp` carries no per-operation bookkeeping. A test pins
    # that model's field list so adding one has to be a decision.
    #
    # A channel a later operation of another kind places is left out entirely,
    # rather than assigned here and overwritten afterwards: two writes to one
    # channel's number is what "consolidate" exists to avoid, and only one of
    # them is the number the caller asked for.
    channel_final_numbers = {
        cid: number
        for cid, number in channel_final_numbers.items()
        if channel_number_owner.get(cid) == "bulkAssignChannelNumbers"
    }
    if channel_final_numbers:
        entries = sorted(channel_final_numbers.items(), key=lambda e: e[1])
        i = 0
        while i < len(entries):
            start_num = entries[i][1]
            j = i
            while j + 1 < len(entries) and entries[j + 1][1] == entries[j][1] + 1:
                j += 1
            ids = [e[0] for e in entries[i:j + 1]]
            consolidated.append(BulkAssignNumbersOp(channelIds=ids, startingNumber=start_num))
            i = j + 1

    # Consolidated reorder ops
    for cid, stream_ids in channel_final_stream_order.items():
        consolidated.append(BulkReorderStreamsOp(channelId=cid, streamIds=stream_ids))

    # Stream add/remove (cancelled pairs excluded)
    for entry in stream_ops.values():
        if entry["added"] and entry["removed"]:
            continue  # Cancel out
        if entry["added"]:
            consolidated.append(entry["added"])
        if entry["removed"]:
            consolidated.append(entry["removed"])

    elapsed = (time.time() - start) * 1000
    logger.info(
        "[CHANNELS-BULK] Consolidated %d -> %d operations in %.1fms",
        original_count, len(consolidated), elapsed,
    )
    return consolidated


def _same_channel_number_or_both_unset(a, b) -> bool:
    """Is ``a`` the same recorded VALUE as ``b``, unassigned included?

    Deliberately not :func:`same_channel_number`, which calls ``None``
    different from everything including itself — correct when asking "do these
    two channels collide?", wrong when asking "is this the value I recorded?".
    Mirrors ``numbersAgree`` in ``frontend/src/utils/channelNumberConcurrency.ts``.
    """
    if a is None or b is None:
        return a is None and b is None
    return same_channel_number(a, b)


def _numbering_execution_order(
    operations: Sequence[BulkOperation],
    existing_channels: dict,
) -> list[tuple[int, BulkOperation]]:
    """Pair every operation with its SUBMITTED index, resequencing renumbers.

    Bead ``enhancedchannelmanager-ic884.3``. A run of channel-number edits is
    sent in whatever order the operator happened to make them, so a plan that
    is perfectly legal as a final state can move a channel onto a number that
    another channel in the same run has not left yet. ``order_numbering_writes``
    picks an order where that does not happen, and names the cycles — the
    two-channel swap being the smallest — where no such order exists.

    THE RESEQUENCING IS DELIBERATELY NARROW, because reordering operations is
    the kind of change that breaks things nobody was looking at:

    * only a maximal run of CONSECUTIVE ``updateChannel`` operations that carry
      ``channel_number`` is considered, so no numbering write can be moved
      across a create, a delete, a stream edit or a group operation;
    * every channel in the run must be distinct and its id already real, so the
      final state is identical whatever order the run is written in — an
      absolute number assigned to distinct channels does not depend on order —
      and no operation can overtake the ``createChannel`` that gives it its id;
    * the SUBMITTED index rides along, so error ids, journal rows and
      validation issues keep naming the operation the caller sent.

    A caller who sends nothing but ordinary edits gets its list back with the
    indexes it sent, which is what keeps the existing behaviour of every other
    operation type untouched.
    """
    plan: list[tuple[int, BulkOperation]] = []
    run: list[tuple[int, BulkOperation]] = []

    def flush_run() -> None:
        if not run:
            return
        if len(run) < 2:
            plan.extend(run)
            run.clear()
            return
        by_channel = {op.channelId: index for index, op in run}
        writes = [
            NumberingWrite(
                channel_id=op.channelId,
                name=(existing_channels.get(op.channelId) or {}).get("name")
                or f"Channel {op.channelId}",
                before=(existing_channels.get(op.channelId) or {}).get("channel_number"),
                after=op.data.get("channel_number"),
            )
            for _index, op in run
        ]
        order = order_numbering_writes(writes)
        if order.cycles:
            logger.info(
                "[CHANNELS-BULK] Numbering run contains %s cycle(s) no order can "
                "avoid; each shares a channel number for one write: %s",
                len(order.cycles), order.cycles,
            )
        op_by_index = {index: op for index, op in run}
        plan.extend(
            (by_channel[write.channel_id], op_by_index[by_channel[write.channel_id]])
            for write in order.writes
        )
        run.clear()

    seen_in_run: set[int] = set()
    for index, op in enumerate(operations):
        is_numbering_edit = (
            op.type == "updateChannel"
            and isinstance(op.data, dict)
            and "channel_number" in op.data
            # A negative id is a staging placeholder resolved from this same
            # batch's creates; it has no lineup entry to order against and its
            # create is outside the run.
            and op.channelId >= 0
            and op.channelId not in seen_in_run
        )
        if is_numbering_edit:
            run.append((index, op))
            seen_in_run.add(op.channelId)
            continue
        flush_run()
        seen_in_run.clear()
        plan.append((index, op))
    flush_run()
    return plan


# -----------------------------------------------------------------------------
# Bulk-commit background jobs (bd-ggxks)
# -----------------------------------------------------------------------------
# Each Dispatcharr write inside a bulk commit is a sequential HTTP call
# (~0.7s/channel for createChannel). A 441-op SiriusXM batch easily exceeds
# the 30s ECM_REQUEST_TIMEOUT_SECONDS middleware budget, returning 504 to the
# operator while the handler keeps running in the background. The operator
# retries, piling duplicates into Dispatcharr.
#
# Architecture (matches bd-cns7j debug-bundle and bd-enfsy /auto-creation/run):
#   POST /bulk-commit           → 202 + {job_id, status: "running"}; supervised
#                                 background task does the work. validateOnly
#                                 stays SYNCHRONOUS — pre-validation is fast
#                                 and the frontend uses it for instant feedback
#                                 before commit, where a poll round-trip would
#                                 add latency for no gain.
#   GET  /bulk-commit/{job_id}  → JSON status: running | failed (with error) |
#                                 completed (with the full BulkCommitResponse
#                                 envelope under ``result``). Completed jobs
#                                 are evicted on first read so RAM is freed.
#
# Job state lives in-memory because the result envelope is small (a few KB
# even for 1000+ ops) and the bulk commit is operator-triggered. TTL prune
# on every new POST keeps the dict bounded if a client abandons polling.

_BULK_COMMIT_JOB_TTL_SECONDS = 1800  # 30 min — matches debug-bundle TTL
_BULK_COMMIT_BACKGROUND_TASKS: set[asyncio.Task] = set()


class _BulkCommitJob:
    """In-memory state for one bulk-commit run (bd-ggxks)."""

    __slots__ = ("status", "created_at", "completed_at", "error", "result")

    def __init__(self) -> None:
        self.status: str = "running"  # running | completed | failed
        self.created_at: float = time.time()
        self.completed_at: Optional[float] = None
        self.error: Optional[str] = None
        self.result: Optional[dict] = None


_BULK_COMMIT_JOBS: dict[str, _BulkCommitJob] = {}


def _prune_old_bulk_commit_jobs() -> None:
    """Drop bulk-commit jobs older than the TTL so the dict can't grow unbounded."""
    cutoff = time.time() - _BULK_COMMIT_JOB_TTL_SECONDS
    stale = [jid for jid, job in _BULK_COMMIT_JOBS.items() if job.created_at < cutoff]
    for jid in stale:
        _BULK_COMMIT_JOBS.pop(jid, None)
    if stale:
        logger.debug("[CHANNELS-BULK] Pruned %s expired bulk-commit jobs", len(stale))


@router.post("/bulk-commit")
async def bulk_commit_operations(request: BulkCommitRequest, _admin=RequireAdminIfEnabled):
    """
    Process multiple channel operations. Admin only (bulk operator op, bd-um30y).

    Two response shapes (bd-ggxks):

    - ``validateOnly: true`` → **200 sync** with the full BulkCommitResponse.
      Validation runs entirely against ECM-cached lookups + a single
      Dispatcharr page fetch, so it fits comfortably inside the 30s request
      budget and the frontend uses it for instant pre-commit feedback.
    - ``validateOnly: false`` (default) → **202** with
      ``{job_id, status: "running"}``. The actual work runs in a supervised
      background task; the client polls
      ``GET /api/channels/bulk-commit/{job_id}`` until the status is terminal
      (``completed`` carries the full BulkCommitResponse under ``result``;
      ``failed`` carries an ``error`` string).

    Options:
    - validateOnly: If true, only validate without executing
    - continueOnError: If true, continue processing even when operations fail
    - consolidate: If true, server-side dedup of redundant ops
    """
    # One Apply All is several bulk-commit requests: a create phase, then
    # batches of 200. A client that sends the same X-ECM-Batch-Id on all of
    # them gets every journal row of that session under one correlatable batch
    # (bead enhancedchannelmanager-r9py9). Read here, in the request, rather
    # than in the background task, so nothing depends on contextvar copying.
    request_batch_id = journal.get_request_batch_id()

    # Validate-only is fast — keep it sync so the frontend gets pre-commit
    # feedback in one round-trip instead of POST+poll.
    if request.validateOnly:
        return await _run_bulk_commit(request, batch_id=request_batch_id)

    # Enqueue the actual commit as a supervised background task.
    _prune_old_bulk_commit_jobs()
    job_id = uuid.uuid4().hex
    _BULK_COMMIT_JOBS[job_id] = _BulkCommitJob()
    op_count = len(request.operations)

    async def _runner() -> None:
        job = _BULK_COMMIT_JOBS.get(job_id)
        if job is None:
            logger.warning("[CHANNELS-BULK] Job %s missing before start", job_id)
            return
        try:
            result = await _run_bulk_commit(request, batch_id=request_batch_id)
            job.result = result
            job.status = "completed"
            job.completed_at = time.time()
            logger.info(
                "[CHANNELS-BULK] Job %s completed: applied=%s failed=%s",
                job_id, result.get("operationsApplied"), result.get("operationsFailed"),
            )
        except asyncio.CancelledError:
            job.status = "failed"
            job.error = "Background task cancelled"
            job.completed_at = time.time()
            logger.warning("[CHANNELS-BULK] Job %s cancelled", job_id)
            raise
        except Exception as e:  # noqa: BLE001 — supervisor must catch broadly
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            job.completed_at = time.time()
            logger.exception("[CHANNELS-BULK] Job %s failed: %s", job_id, e)

    task = asyncio.create_task(_runner(), name=f"bulk-commit-{job_id}")
    _BULK_COMMIT_BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BULK_COMMIT_BACKGROUND_TASKS.discard)

    logger.info("[CHANNELS-BULK] Job %s enqueued (%s operations)", job_id, op_count)
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "running",
            "message": (
                f"Bulk commit started; poll /api/channels/bulk-commit/{job_id} "
                "for status"
            ),
        },
    )


@router.get("/bulk-commit/{job_id}")
async def get_bulk_commit_status(job_id: str):
    """Poll a bulk-commit job (bd-ggxks).

    - ``running``   → ``{job_id, status: "running"}``
    - ``failed``    → ``{job_id, status: "failed", error}`` (job stays in the
      dict until TTL prune so the operator can re-poll and see the error)
    - ``completed`` → ``{job_id, status: "completed", result: <BulkCommitResponse>}``
      and the job is evicted on read (single-shot retrieval).
    - missing job   → 404
    """
    job = _BULK_COMMIT_JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bulk commit job not found")
    if job.status == "running":
        return {"job_id": job_id, "status": "running"}
    if job.status == "failed":
        return {
            "job_id": job_id,
            "status": "failed",
            "error": job.error or "unknown error",
        }
    # completed — drop on read so RAM is freed.
    result = job.result or {}
    _BULK_COMMIT_JOBS.pop(job_id, None)
    return {"job_id": job_id, "status": "completed", "result": result}


async def _run_bulk_commit(
    request: BulkCommitRequest, batch_id: Optional[str] = None
) -> dict:
    """Execute a bulk-commit request and return the result envelope.

    Pure work function — no HTTP / endpoint awareness. Invoked synchronously by
    POST /bulk-commit when ``validateOnly=true``, and from the supervised
    background task dispatched by POST /bulk-commit otherwise (bd-ggxks).

    ``batch_id`` correlates this run's journal rows. The caller passes the
    request's ``X-ECM-Batch-Id`` when the client sent one, so the several
    bulk-commit requests one Apply All fans out into land under a single batch
    (bead enhancedchannelmanager-r9py9); otherwise a fresh id is minted here.
    """
    client = get_client()
    batch_id = batch_id or str(uuid.uuid4())[:8]

    # Phase 1 onwards. Until this flips, nothing has been written anywhere, so
    # the two pre-execution early returns (validateOnly, validation failed with
    # continueOnError=false) must leave no journal trace at all — a dry run and
    # a refused run are not commits.
    execution_started = False
    # `flush_journal` is called from the single exit helper AND from the outer
    # exception handler, so it has to be idempotent.
    journal_flushed = False
    # Whether a BaseException — a CancelledError from application shutdown,
    # SystemExit, KeyboardInterrupt — is already on its way out of this
    # function. Set by the `except BaseException` clause at the bottom, read by
    # the `finally` beside it, which must not let the flush replace it (fix
    # round 5).
    unwinding_base_exception = False

    def journal_row(
        action_type: str,
        entity_id: Optional[int],
        entity_name: str,
        description: str,
        before_value: Optional[dict] = None,
        after_value: Optional[dict] = None,
    ) -> dict:
        """Build ONE per-entity journal row. Does not queue it.

        Queueing is the ledger's job, and only ever happens as part of saying
        that an upstream write landed (``ledger.record_write`` /
        ``ledger.record_persisted``). This used to be an ``add_journal_row``
        that appended to a list in this closure, which meant a branch could
        write upstream and then fail before calling it — three of the five
        findings in fix round 3 were exactly that (bead
        ``enhancedchannelmanager-kz089``). A builder cannot be called at the
        wrong time, because calling it is not what records anything.

        The rows mirror what the single-channel endpoints already write,
        because those are the rows an MCP agent produces and a channel's
        history must read the same whichever surface made the change (bead
        ``enhancedchannelmanager-r9py9``); before that bead this path wrote
        only the Bulk Commit summary.
        """
        return {
            "category": "channel",
            "action_type": action_type,
            "entity_id": entity_id,
            "entity_name": entity_name,
            "description": description,
            "before_value": before_value,
            "after_value": after_value,
            "batch_id": batch_id,
        }

    # Count operation types for logging
    op_counts = {}
    for op in request.operations:
        op_counts[op.type] = op_counts.get(op.type, 0) + 1
    op_summary = ", ".join(f"{count} {op_type}" for op_type, count in sorted(op_counts.items()))

    logger.debug("[CHANNELS-BULK] Starting bulk commit (batch=%s): %s operations (%s)", batch_id, len(request.operations), op_summary)
    logger.debug("[CHANNELS-BULK] Options: validateOnly=%s, continueOnError=%s, consolidate=%s", request.validateOnly, request.continueOnError, request.consolidate)

    # Consolidate operations if requested
    operations = request.operations
    if request.consolidate:
        operations = _consolidate_operations(operations)
        request = request.model_copy(update={"operations": operations})
    if request.groupsToCreate:
        logger.debug("[CHANNELS-BULK] Groups to create: %s", [g.get('name') for g in request.groupsToCreate])

    result = {
        "success": True,
        "operationsApplied": 0,
        "operationsFailed": 0,
        "errors": [],
        "tempIdMap": {},  # temp channel ID -> real ID
        "groupIdMap": {},  # group name -> real ID
        "validationIssues": [],
        "validationPassed": True,
        "partial": False,  # bd-5xciq: some-applied-some-failed outcome
        # createChannel ops that ASKED for normalization and did not get it
        # (bead enhancedchannelmanager-e9e5o). Always present, so a caller
        # checks its length rather than probing for a key that may not exist.
        # A normalization failure does NOT fail the op or the batch: the
        # channel was created, just under the raw name — which is precisely
        # why the envelope has to say so, since the result is otherwise
        # indistinguishable from `normalize=false`.
        "normalizationFailures": [],
        # Per-entity journal rows this run could not write. Always present so a
        # caller checks the number rather than probing for a key. Non-zero means
        # the mutations LANDED and their audit trail did not — the operations
        # must not be retried, and the container log carries the lost rows
        # (bead enhancedchannelmanager-kz089, fix round 2).
        "journalRowsUnwritten": 0,
        # Channels this run left on a number they should not be on, with the
        # exact step that puts each one right (bead
        # enhancedchannelmanager-ic884.3). Always present, so a caller checks
        # its length rather than probing for a key. Non-empty means a numbering
        # plan stopped part way AND the compensating write that would have put
        # the channel back failed too — the one case where neither the previous
        # state nor the proposed one is what the operator is left with, and the
        # only honest answer is to say precisely what is where.
        "numberingRecovery": [],
    }

    # Counters the summary row reports. Kept as locals rather than derived from
    # the id maps at write time (bead enhancedchannelmanager-r9py9):
    # ``len(tempIdMap)`` misses a createChannel whose temp id was not negative,
    # and ``len(groupIdMap)`` is plain wrong for "created" because that map also
    # collects PRE-EXISTING groups resolved by name in Phase 1. A counter that
    # only ever increments where the thing actually happens cannot drift.
    channels_created = 0
    groups_created = 0

    # Helper to resolve temp IDs to real IDs
    def resolve_id(channel_id: int) -> int:
        return result["tempIdMap"].get(channel_id, channel_id)

    # Helper to resolve group ID (could be temp or real, or from new group name)
    def resolve_group_id(group_id: Optional[int], new_group_name: Optional[str]) -> Optional[int]:
        if new_group_name and new_group_name in result["groupIdMap"]:
            return result["groupIdMap"][new_group_name]
        return group_id

    # One outcome per operation, and the only thing that writes the counters.
    # Constructed before the try so the outer handler can still report what the
    # run managed to do (bead enhancedchannelmanager-e9e5o, fix round 4).
    ledger = OperationLedger(len(request.operations))

    # What this run has actually done to channel numbers, so a plan that stops
    # part way can be written back (bead enhancedchannelmanager-ic884.3). It is
    # a record of writes that landed, not a transaction: there is no conditional
    # update in Dispatcharr 0.28.x to build one on. See
    # ``backend/channel_number_apply.py``.
    compensator = NumberingCompensator()

    class MalformedCreateResponseError(Exception):
        """Dispatcharr accepted a create but answered without a usable id.

        The channel EXISTS. This is raised so the operation stops early and is
        reported, but the ledger has already recorded the write as persisted, so
        the operation is counted as applied-but-incomplete rather than failed.
        Reporting it as a failure is what made an integrator retry and create
        the channel twice.
        """

    class UnresolvedGroupError(Exception):
        """A group id that was never resolved to a real Dispatcharr group.

        Negative ids are the frontend's staging placeholders. Dispatcharr
        answers one with ``400 {"channel_group_id": ["Invalid pk \\"-1000\\" -
        object does not exist."]}``, and drill run 2026-08-09-run18 lost a
        channel to exactly that (bead ``enhancedchannelmanager-udq1j``). The
        frontend now resolves them by name before posting; this is the
        backstop for an older or scripted client that does not, so the failure
        is named in ECM's own error rather than relayed as an opaque upstream
        400.
        """

    def reject_unresolved_group(group_id: Optional[int], label: str) -> Optional[int]:
        """Return ``group_id``, or raise if it is still a staging placeholder."""
        if group_id is not None and group_id < 0:
            raise UnresolvedGroupError(
                f"Channel group {group_id} does not exist. A new group must be sent in "
                f"groupsToCreate and referenced by name ({label})."
            )
        return group_id

    class UnresolvedChannelError(Exception):
        """A channel id that is still a frontend staging placeholder.

        ``resolve_id`` maps a negative temp id onto the real id its
        ``createChannel`` produced. A negative id that survives that lookup
        names a channel this batch never created, and sending it upstream as a
        path segment is how a caller-supplied id reaches Dispatcharr unchecked.
        """

    class ConcurrentChannelNumberChangeError(Exception):
        """The channel is not on the number the caller said it was on.

        Bead ``enhancedchannelmanager-ic884.4``. Raised INSTEAD of the PATCH, so
        the operation fails without writing — which is what makes the staged
        change unable to overwrite work the caller has not seen. The rest of the
        run's numbering is then put back by the compensation pass, because a
        numbering plan that stopped part way is exactly what this is.
        """

    class UnverifiableChannelNumberError(Exception):
        """The caller asked for a check the executor could not run.

        The lineup read failed, so there is nothing to compare the caller's
        expectation against. Refusing is the only honest answer: a caller that
        sent an expectation asked NOT to overwrite blindly, and proceeding
        anyway would do the one thing they asked for protection from. This is
        not the "a failed lookup never accuses" rule — nothing is being accused
        of not existing; the check itself is being reported as unavailable.
        """

    def reject_unresolved_channel(channel_id: int, label: str) -> int:
        """Return ``channel_id``, or raise if it is still a staging placeholder."""
        if channel_id < 0:
            raise UnresolvedChannelError(
                f"Channel {channel_id} does not exist. A temp channel id must be "
                f"created by a createChannel operation in the same batch ({label})."
            )
        return channel_id

    def flush_journal() -> None:
        """Write this run's journal rows and its summary row. Never raises.

        MUST STAY SYNCHRONOUS. It runs from the outer ``finally`` while a
        ``CancelledError`` is unwinding (fix round 4), and a coroutine that
        awaited anything there would simply be cancelled again at the first
        await — reopening the hole this call is closing. Nothing it touches is
        async: :func:`write_journal_rows` and ``journal.log_entry`` are both
        blocking calls. It does not follow that it never raises a
        ``BaseException``: a synchronous dependency can, and the outer
        ``finally`` is written so that one cannot replace the cancellation it
        is unwinding (fix round 5).

        Called from :func:`finish`, which is the only way out of this function
        by RETURN once execution has started — every early return, every
        partial batch failure and the outer exception handler all go through
        it — and from the outer ``finally``, which covers the ways out that are
        not returns. Before bead …-kz089 fix round 2 the journal writes were
        the last statements of the happy path, so a Phase 1 group-create
        failure returned with group A already created upstream and no row
        saying so, and an exception anywhere after Phase 1 did the same for
        every operation that had landed.

        A journal failure is recorded on the ledger as a setup failure rather
        than swallowed: the mutations DID land, so nothing may be reported as
        failed, but the envelope has to say the audit trail is incomplete
        instead of looking like a clean commit.

        The rows come from the LEDGER, which queued each one as its write
        landed. Round 2 made this the single flush and left the rows in a list
        the branches appended to whenever they got round to it; a flush that
        cannot be skipped still writes nothing if the row was never built (fix
        round 3).
        """
        nonlocal journal_flushed
        if journal_flushed or not execution_started:
            return
        journal_flushed = True

        rows = ledger.drain_journal_rows()
        unwritten = write_journal_rows(
            rows, batch_id=batch_id, log_tag="CHANNELS-BULK"
        )

        # The summary reads last so it closes the batch. Counters come from the
        # ledger rather than from `result`, because this runs on paths where
        # `finalize_bulk_commit_result` has not written them yet.
        try:
            summary = journal.log_entry(
                category="channel",
                action_type="bulk_commit",
                entity_id=None,
                entity_name="Bulk Commit",
                description=f"Applied {ledger.applied} operations in bulk commit" +
                            (f" ({ledger.failed} failed)" if ledger.failed > 0 else ""),
                after_value={
                    "operations_applied": ledger.applied,
                    "operations_failed": ledger.failed,
                    "channels_created": channels_created,
                    "groups_created": groups_created,
                    "entity_rows_written": len(rows) - unwritten,
                    "validation_issues": len(result["validationIssues"]),
                    "continue_on_error": request.continueOnError,
                },
                batch_id=batch_id,
            )
        except Exception as summary_err:
            logger.exception(
                "[CHANNELS-BULK] Summary journal row raised (batch=%s): %s",
                batch_id, summary_err,
            )
            summary = None
        if summary is None:
            unwritten += 1
            logger.error(
                "[CHANNELS-BULK] UNJOURNALLED bulk-commit summary (batch=%s)", batch_id
            )

        if unwritten:
            result["journalRowsUnwritten"] = unwritten
            result["errors"].append({
                "operationId": "bulk-commit-journal",
                "error": (
                    f"{unwritten} journal row(s) could not be written. The "
                    "operations themselves applied — do NOT retry them; the "
                    "container log carries the unwritten rows."
                ),
            })
            # Not an operation failure: every operation resolved exactly as the
            # ledger already recorded. This is the bookkeeping after them.
            ledger.record_setup_failure(aborted_run=False)

    def finish() -> dict:
        """The single exit once execution has started.

        Journal first, then the accounting, so the envelope's own audit sees
        every error entry — including one the journal flush just added.
        """
        flush_journal()
        finalize_bulk_commit_result(result, ledger)
        logger.info(
            "[CHANNELS-BULK] Completed (batch=%s): success=%s, applied=%s, failed=%s%s",
            batch_id, result["success"], result["operationsApplied"],
            result["operationsFailed"],
            (", validation_issues=%s" % len(result["validationIssues"]))
            if result["validationIssues"] else "",
        )
        return result

    try:
        # Phase 0: Pre-validation - check that referenced entities exist
        logger.debug("[CHANNELS-BULK] Phase 0: Starting pre-validation")

        # Collect all channel IDs that are referenced (not created) in operations
        referenced_channel_ids = set()
        referenced_stream_ids = set()
        channels_to_create = set()  # Temp IDs that will be created
        # The three operations Edit Mode added in bead …-kz089 were enumerated
        # by neither of the two loops below, so they reached their mutation with
        # nothing resolved: the profile and channel ids went straight upstream,
        # the group id straight into a local DELETE, the stream ids straight
        # into another. They resolve like every other operation now (fix round
        # 2). Profiles and hidden groups need their own lookups because neither
        # is a channel or a stream.
        referenced_profile_ids = set()
        referenced_hidden_group_ids = set()
        # A createChannel names no existing channel, so a batch of nothing but
        # creates used to fetch no lineup at all — and the final-state check
        # (bead enhancedchannelmanager-ic884.2) then had nothing to detect a
        # collision AGAINST. That batch is not hypothetical: Edit Mode's Apply
        # sends its creates in their own request, ahead of everything else. A
        # create carrying an explicit number therefore asks for the lineup too.
        # A create with no number does not: Dispatcharr picks that number, so
        # there is nothing here to check.
        numbering_needs_lineup = False
        # Whether the final-state numbering check has anything to check. It is
        # what decides whether a lineup that would not load is REPORTED (fix
        # round 2 -- see the check itself, below). Broader than
        # ``numbering_needs_lineup``, which only asks whether this batch would
        # otherwise fetch no lineup at all.
        numbering_places_a_channel = False

        for idx, op in enumerate(request.operations):
            if op.type == "createChannel":
                # This creates a channel, track its temp ID
                channels_to_create.add(op.tempId)
                if op.channelNumber is not None:
                    numbering_needs_lineup = True
                    numbering_places_a_channel = True
            elif op.type in ("updateChannel", "deleteChannel"):
                if op.channelId >= 0:  # Only real IDs need validation
                    referenced_channel_ids.add(op.channelId)
                if op.type == "updateChannel" and "channel_number" in (op.data or {}):
                    numbering_places_a_channel = True
            elif op.type == "addStreamToChannel":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                referenced_stream_ids.add(op.streamId)
            elif op.type == "removeStreamFromChannel":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                referenced_stream_ids.add(op.streamId)
            elif op.type == "reorderChannelStreams":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                for sid in op.streamIds:
                    referenced_stream_ids.add(sid)
            elif op.type == "bulkAssignChannelNumbers":
                # ONLY IF IT NAMES A CHANNEL. The model permits an empty
                # ``channelIds``, and a range over no channels puts nobody on
                # any number — so it must not make the preflight report itself
                # unverifiable, which under the default ``continueOnError``
                # REFUSES a request that would have mutated nothing.
                if op.channelIds:
                    numbering_places_a_channel = True
                for cid in op.channelIds:
                    if cid >= 0:
                        referenced_channel_ids.add(cid)
            elif op.type == "setProfileMembership":
                if op.channelId >= 0:
                    referenced_channel_ids.add(op.channelId)
                referenced_profile_ids.add(op.profileId)
            elif op.type == "clearStreamStats":
                referenced_stream_ids.update(op.streamIds)
            elif op.type == "restoreChannelGroup":
                referenced_hidden_group_ids.add(op.groupId)

        # Fetch existing channels and streams to validate
        existing_channels = {}  # id -> channel dict
        existing_streams = {}   # id -> stream dict
        existing_profile_ids: set[int] = set()
        hidden_group_ids: set[int] = set()
        # Only validate against a lookup that actually SUCCEEDED. An upstream
        # failure here must not turn every referenced entity into a reported
        # "does not exist" — that would be the lookup's failure wearing the
        # operation's name, and under `continueOnError=false` it refuses the
        # whole run on the deliberately traceless pre-execution path.
        #
        # Fix round 2 gave the profile and hidden-group lookups this guard and
        # left the two OLDEST ones — channels and streams — reading their own
        # emptiness as proof of absence, which is the asymmetry fix round 3
        # closes. All four lookups are `except Exception` around an upstream or
        # database read, so all four can be empty for a reason that is not
        # "the entity does not exist".
        channels_resolved = False
        streams_resolved = False
        profiles_resolved = False
        hidden_groups_resolved = False

        logger.debug("[CHANNELS-BULK] Referenced entities: %s channels, %s streams", len(referenced_channel_ids), len(referenced_stream_ids))
        logger.debug("[CHANNELS-BULK] Channels to create: %s (temp IDs: %s)", len(channels_to_create), sorted(channels_to_create))
        if referenced_channel_ids:
            # Log a sample of referenced channel IDs (first 20)
            sample_ids = sorted(referenced_channel_ids)[:20]
            logger.debug("[CHANNELS-BULK] Referenced channel IDs (sample): %s%s", sample_ids, '...' if len(referenced_channel_ids) > 20 else '')

        if referenced_channel_ids or numbering_needs_lineup:
            try:
                logger.debug("[CHANNELS-BULK] Fetching existing channels for validation...")
                # Fetch all pages of channels to build lookup
                page = 1
                while True:
                    response = await client.get_channels(page=page, page_size=500)
                    for ch in response.get("results", []):
                        existing_channels[ch["id"]] = ch
                    if not response.get("next"):
                        break
                    page += 1
                channels_resolved = True
                logger.debug("[CHANNELS-BULK] Loaded %s existing channels", len(existing_channels))
                # Check which referenced channels don't exist
                missing_channels = referenced_channel_ids - set(existing_channels.keys())
                if missing_channels:
                    logger.warning("[CHANNELS-BULK] Missing channels detected: %s (%s total)", sorted(missing_channels), len(missing_channels))
                else:
                    logger.debug("[CHANNELS-BULK] All %s referenced channels exist", len(referenced_channel_ids))
            except Exception as e:
                logger.warning("[CHANNELS-BULK] Failed to fetch channels for validation: %s", e)

        if referenced_stream_ids:
            try:
                logger.debug("[CHANNELS-BULK] Fetching %s referenced streams for validation...", len(referenced_stream_ids))
                # Fetch only the specific streams that are referenced (not all streams)
                streams = await client.get_streams_by_ids(list(referenced_stream_ids))
                for s in streams:
                    existing_streams[s["id"]] = s
                streams_resolved = True
                logger.debug("[CHANNELS-BULK] Loaded %s of %s referenced streams", len(existing_streams), len(referenced_stream_ids))
            except Exception as e:
                logger.warning("[CHANNELS-BULK] Failed to fetch streams for validation: %s", e)

        if referenced_profile_ids:
            try:
                logger.debug("[CHANNELS-BULK] Fetching channel profiles for validation...")
                profiles = await client.get_channel_profiles()
                existing_profile_ids = {p["id"] for p in profiles if "id" in p}
                profiles_resolved = True
                logger.debug("[CHANNELS-BULK] Loaded %s channel profiles", len(existing_profile_ids))
            except Exception as e:
                logger.warning("[CHANNELS-BULK] Failed to fetch channel profiles for validation: %s", e)

        if referenced_hidden_group_ids:
            try:
                from models import HiddenChannelGroup
                with get_session() as db:
                    hidden_group_ids = {
                        row.group_id
                        for row in db.query(HiddenChannelGroup.group_id).filter(
                            HiddenChannelGroup.group_id.in_(referenced_hidden_group_ids)
                        )
                    }
                hidden_groups_resolved = True
                logger.debug("[CHANNELS-BULK] %s of %s referenced groups are hidden", len(hidden_group_ids), len(referenced_hidden_group_ids))
            except Exception as e:
                logger.warning("[CHANNELS-BULK] Failed to read hidden groups for validation: %s", e)

        def channel_is_missing(channel_id: int) -> bool:
            """Is this channel KNOWN to be absent from Dispatcharr?

            False when the catalog read failed, which is not the same fact and
            must never be reported as one (fix round 3). Negative ids are the
            frontend's staging placeholders and are resolved by `resolve_id`,
            not looked up here.
            """
            return (
                channels_resolved
                and channel_id >= 0
                and channel_id not in existing_channels
            )

        def stream_is_missing(stream_id: int) -> bool:
            """Is this stream KNOWN to be absent? Same rule as channels."""
            return streams_resolved and stream_id not in existing_streams

        # Validate each operation
        for idx, op in enumerate(request.operations):
            if op.type == "updateChannel":
                if channel_is_missing(op.channelId):
                    ch_name = f"Channel {op.channelId}"
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Channel {op.channelId} does not exist in Dispatcharr",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "channelName": ch_name,
                    })
                    result["validationPassed"] = False
            elif op.type == "deleteChannel":
                if channel_is_missing(op.channelId):
                    # Deleting a channel that doesn't exist is a no-op, not an error
                    logger.debug("[CHANNELS-BULK] deleteChannel: channel %s already gone, skipping", op.channelId)

            elif op.type == "addStreamToChannel":
                if channel_is_missing(op.channelId):
                    ch_name = f"Channel {op.channelId}"
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot add stream to channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "channelName": ch_name,
                        "streamId": op.streamId,
                    })
                    result["validationPassed"] = False
                elif op.channelId >= 0:
                    # `.get`, not `[]`: a failed catalog read leaves this empty
                    # while the branch above deliberately does not fire.
                    ch_name = existing_channels.get(op.channelId, {}).get(
                        "name", f"Channel {op.channelId}"
                    )
                    # Check stream exists
                    if stream_is_missing(op.streamId):
                        result["validationIssues"].append({
                            "type": "missing_stream",
                            "severity": "error",
                            "message": f"Stream {op.streamId} does not exist",
                            "operationIndex": idx,
                            "channelId": op.channelId,
                            "channelName": ch_name,
                            "streamId": op.streamId,
                        })
                        result["validationPassed"] = False

            elif op.type == "removeStreamFromChannel":
                if channel_is_missing(op.channelId):
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot remove stream from channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "streamId": op.streamId,
                    })
                    result["validationPassed"] = False

            elif op.type == "reorderChannelStreams":
                if channel_is_missing(op.channelId):
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": f"Cannot reorder streams for channel {op.channelId}: channel does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                    })
                    result["validationPassed"] = False

            elif op.type == "bulkAssignChannelNumbers":
                for cid in op.channelIds:
                    if channel_is_missing(cid):
                        result["validationIssues"].append({
                            "type": "missing_channel",
                            "severity": "error",
                            "message": f"Cannot assign number to channel {cid}: channel does not exist",
                            "operationIndex": idx,
                            "channelId": cid,
                        })
                        result["validationPassed"] = False

            elif op.type == "setProfileMembership":
                # Both ids are sent upstream as path segments, so both are
                # resolved here exactly as updateChannel's channel id is. An
                # error, not a warning: writing a membership for a channel or
                # profile that does not exist cannot do what was asked.
                if channel_is_missing(op.channelId):
                    result["validationIssues"].append({
                        "type": "missing_channel",
                        "severity": "error",
                        "message": (
                            f"Cannot set profile membership for channel {op.channelId}: "
                            "channel does not exist"
                        ),
                        "operationIndex": idx,
                        "channelId": op.channelId,
                        "channelName": f"Channel {op.channelId}",
                    })
                    result["validationPassed"] = False
                if profiles_resolved and op.profileId not in existing_profile_ids:
                    result["validationIssues"].append({
                        "type": "invalid_operation",
                        "severity": "error",
                        "message": f"Channel profile {op.profileId} does not exist",
                        "operationIndex": idx,
                        "channelId": op.channelId,
                    })
                    result["validationPassed"] = False

            elif op.type == "restoreChannelGroup":
                # A warning, not an error: the executor already treats a group
                # that is no longer hidden as a no-op rather than a failure,
                # because another session restoring it first is a race, not a
                # mistake. The issue exists so an unresolvable id is visible
                # instead of silently deleting nothing.
                if hidden_groups_resolved and op.groupId not in hidden_group_ids:
                    result["validationIssues"].append({
                        "type": "invalid_operation",
                        "severity": "warning",
                        "message": (
                            f"Channel group {op.groupId} is not hidden; the restore "
                            "will do nothing"
                        ),
                        "operationIndex": idx,
                    })

            elif op.type == "clearStreamStats":
                # A warning, not an error: probe stats outlive the stream row
                # upstream, and clearing orphaned stats is a thing an operator
                # legitimately wants to do. Erroring here would make the only
                # way to remove them impossible.
                for sid in op.streamIds:
                    if stream_is_missing(sid):
                        result["validationIssues"].append({
                            "type": "missing_stream",
                            "severity": "warning",
                            "message": (
                                f"Stream {sid} does not exist; clearing its probe "
                                "stats will do nothing"
                            ),
                            "operationIndex": idx,
                            "streamId": sid,
                        })

        # The COMBINED final state, checked once, after every operation has had
        # its own say (bead enhancedchannelmanager-ic884.2).
        #
        # The loop above asks "is this operation possible?" one operation at a
        # time, and that question cannot see a collision three legal operations
        # make between them. This asks "is the lineup they leave behind legal?"
        # — the same question with the operations composed — and it is the only
        # check here whose answer can change when an unrelated operation is
        # added or removed.
        #
        # A CHECK WHOSE INPUT DID NOT LOAD REPORTS THAT, AND NEVER "NO PROBLEM"
        # (fix round 2). This used to run against whatever `existing_channels`
        # happened to hold, on the reasoning that an incomplete lineup "can only
        # make this MISS a conflict, never invent one". True, and beside the
        # point: for a caller that never touches the UI this preflight IS the
        # safety check, so a miss is the entire failure rather than a mild one.
        # The paginated fetch above swallows its exception, so an upstream
        # outage produced an empty lineup, an empty lineup produced no
        # occupants, and the commit went ahead reporting a clean bill of health
        # it had no evidence for.
        #
        # REPORTED AS AN ERROR, NOT AS A REFUSAL OF ITS OWN, so it obeys the
        # `continueOnError` contract every other validation issue on this
        # endpoint already obeys rather than inventing a second one. That lands
        # in the right place on both sides: the default (`continueOnError`
        # false) is what a non-UI caller gets, and it refuses the commit,
        # because an unverifiable safety check is not a passed one. Edit Mode's
        # Apply sends `continueOnError: true` and proceeds, which is correct
        # there for the reason recorded on `TestPreflightAndContinueOnError` —
        # the BROWSER holds the whole plan and has already validated it against
        # the lineup it loaded, so refusing an Apply over a transient upstream
        # hiccup would cost the operator their work and buy no safety.
        #
        # Gated on the check having something to check: a batch that puts no
        # channel on any number is not made unverifiable by a failed lookup it
        # never needed.
        if numbering_places_a_channel and not channels_resolved:
            result["validationIssues"].append({
                "type": "numbering_preflight_unavailable",
                "severity": "error",
                "message": (
                    "The channel lineup could not be read, so the check for duplicate and "
                    "out-of-contract channel numbers could not run. No channel numbering was "
                    "verified. Try again once Dispatcharr is reachable."
                ),
            })
            result["validationPassed"] = False

        # The COMBINED final state still runs: the collisions it can see
        # BETWEEN the request's own operations need no lineup at all, and
        # reporting them alongside the notice above is strictly more
        # information than reporting neither.
        for numbering_issue in evaluate_final_numbering(
            existing_channels.values(), request.operations
        ):
            result["validationIssues"].append(numbering_issue.as_validation_issue())
            result["validationPassed"] = False

        # Log validation summary
        logger.debug("[CHANNELS-BULK] Validation complete: passed=%s, issues=%s", result['validationPassed'], len(result['validationIssues']))
        if result['validationIssues']:
            logger.warning("[CHANNELS-BULK] === VALIDATION ISSUES DETAIL ===")
            for i, issue in enumerate(result['validationIssues'][:10]):  # Show first 10
                op_idx = issue.get('operationIndex', '?')
                ch_id = issue.get('channelId', '?')
                stream_id = issue.get('streamId', '?')
                # Get the actual operation for more context
                if op_idx != '?' and op_idx < len(request.operations):
                    op = request.operations[op_idx]
                    logger.warning("[CHANNELS-BULK]   Issue %s: %s - %s", i+1, issue['type'], issue['message'])
                    # Every attribute read through getattr: not every operation
                    # type carries a channelId, and this line raised
                    # AttributeError for the ones that do not the moment a
                    # validation issue was raised against them.
                    logger.warning("[CHANNELS-BULK]     Operation[%s]: type=%s, channelId=%s, streamId=%s", op_idx, op.type, getattr(op, 'channelId', None), getattr(op, 'streamId', None))
                    if op.type == "updateChannel" and op.data:
                        logger.warning("[CHANNELS-BULK]     Update data: name=%s, number=%s", op.data.get('name'), op.data.get('channel_number'))
                else:
                    logger.warning("[CHANNELS-BULK]   Issue %s: %s - %s (channelId=%s, streamId=%s)", i+1, issue['type'], issue['message'], ch_id, stream_id)
            if len(result['validationIssues']) > 10:
                logger.warning("[CHANNELS-BULK]   ... and %s more issues", len(result['validationIssues']) - 10)
            logger.warning("[CHANNELS-BULK] === END VALIDATION ISSUES ===")

        # If validateOnly, return now without executing
        if request.validateOnly:
            logger.info("[CHANNELS-BULK] Validation only mode: %s issues found, returning without executing", len(result['validationIssues']))
            result["success"] = result["validationPassed"]
            return result

        # If validation failed and continueOnError is false, return without executing
        if not result["validationPassed"] and not request.continueOnError:
            logger.warning("[CHANNELS-BULK] Validation failed with %s issues, aborting (continueOnError=false)", len(result['validationIssues']))
            logger.warning("[CHANNELS-BULK] No operations will be executed. Total operations that would have been attempted: %s", len(request.operations))
            # Log a hint about the issue
            if result['validationIssues']:
                first_issue = result['validationIssues'][0]
                logger.warning("[CHANNELS-BULK] First issue: %s", first_issue.get('message', 'Unknown'))
                if first_issue.get('type') == 'missing_channel':
                    logger.warning("[CHANNELS-BULK] Hint: Channel %s may have been deleted from Dispatcharr. Try refreshing the page to sync.", first_issue.get('channelId'))
            result["success"] = False
            return result

        # Log if continuing despite validation issues
        if not result["validationPassed"] and request.continueOnError:
            logger.warning("[CHANNELS-BULK] Continuing despite %s validation issues (continueOnError=true)", len(result['validationIssues']))

        # Everything from here on can write upstream, so every exit from here
        # on goes through `finish()` and leaves a journal trace.
        execution_started = True

        # Phase 1: Create groups first (if any)
        if request.groupsToCreate:
            logger.debug("[CHANNELS-BULK] Phase 1: Creating %s groups", len(request.groupsToCreate))
            for group_info in request.groupsToCreate:
                group_name = group_info.get("name")
                if not group_name:
                    logger.debug("[CHANNELS-BULK] Skipping group with no name")
                    continue
                try:
                    logger.debug("[CHANNELS-BULK] Creating group: '%s'", group_name)
                    # Try to create the group
                    new_group = await client.create_channel_group(group_name)
                    # The group EXISTS from here on, so its row is queued before
                    # anything that can raise — `new_group["id"]` below is one
                    # such thing, and `.get` here is why the row survives a
                    # response shape that has no id.
                    created_group_id = (
                        new_group.get("id") if isinstance(new_group, dict) else None
                    )
                    ledger.record_write(journal_row=journal_row(
                        action_type="group_create",
                        entity_id=created_group_id,
                        entity_name=group_name,
                        description=f"Created channel group '{group_name}'",
                        after_value={"name": group_name},
                    ))
                    result["groupIdMap"][group_name] = new_group["id"]
                    groups_created += 1
                    logger.debug("[CHANNELS-BULK] Created group '%s' -> ID %s", group_name, new_group['id'])
                except Exception as e:
                    error_str = str(e)
                    # If group already exists, try to find it
                    if "400" in error_str or "already exists" in error_str.lower():
                        logger.debug("[CHANNELS-BULK] Group '%s' may already exist, searching...", group_name)
                        try:
                            groups = await client.get_channel_groups()
                            for g in groups:
                                if g.get("name") == group_name:
                                    result["groupIdMap"][group_name] = g["id"]
                                    logger.debug("[CHANNELS-BULK] Found existing group '%s' -> ID %s", group_name, g['id'])
                                    break
                        except Exception as find_err:
                            logger.debug("[CHANNELS-BULK] Failed to search for existing group: %s", find_err)
                    else:
                        # Non-duplicate error - abort the run.
                        #
                        # This return used to skip the journal writes and the
                        # accounting entirely: with groupsToCreate=[A, B], A was
                        # created upstream, B failed, and the response carried
                        # `success: false, operationsFailed: 0` with group A
                        # existing and no row anywhere saying it had been made.
                        # `finish()` writes A's row; `record_setup_failure`
                        # makes the envelope say a step failed without claiming
                        # an operation did — none was attempted
                        # (bead enhancedchannelmanager-kz089, fix round 2).
                        logger.error("[CHANNELS-BULK] Failed to create group '%s': %s", group_name, e)
                        result["errors"].append({
                            "operationId": f"create-group-{group_name}",
                            "error": str(e)
                        })
                        ledger.record_setup_failure()
                        return finish()
            logger.debug("[CHANNELS-BULK] Group creation complete: %s groups mapped", len(result['groupIdMap']))

        # Per-run logo index (bd-raehx). Previously every createChannel op with
        # a logoUrl + no logoId called find_logo_by_url(), which re-paginated
        # the ENTIRE Dispatcharr logo catalog (~25 pages for large installs).
        # That made the createChannel path O(channels * catalog_pages) — 859
        # logo GETs for a 113-channel batch — exhausting the request budget.
        #
        # Instead we paginate the catalog ONCE, lazily (only the first time a
        # createChannel op actually needs a logo lookup, so validateOnly and
        # logo-free batches pay nothing), and build a {url -> logo} index that
        # every subsequent op reuses. New logos created mid-batch are inserted
        # into the index so a later op sharing the same logoUrl reuses them
        # instead of creating a duplicate.
        #
        # The index is per-run state (closure-local) by design — a long-lived
        # process cache would go stale against Dispatcharr.
        logo_index: Optional[dict[str, dict]] = None

        async def resolve_logo_id(logo_url: str, logo_name: str) -> Optional[int]:
            """Return a logo id for ``logo_url``, building the per-run index on
            first use and creating (and caching) a new logo when absent.

            Raises on a hard failure (pagination error, create error) so the
            caller's try/except can preserve the existing "create the channel
            without a logo" fallthrough behavior.

            A logo CREATED here is an upstream mutation inside an operation that
            may still fail as a whole — `create_channel` answering 500 leaves
            the logo in Dispatcharr's catalog with the channel non-existent. The
            settled product decision that catalog logo additions are immediate
            and additive is not in question; being invisible to the journal was
            the defect (bead ``enhancedchannelmanager-kz089``, fix round 3). It
            is a `record_write` and not a `record_persisted` because a logo
            existing is not the channel existing: this write must not stop the
            operation being reported as the failure it is.
            """
            nonlocal logo_index
            if logo_index is None:
                # First need this run — paginate the full catalog once.
                logo_index = {}
                page = 1
                page_size = 500
                while True:
                    page_result = await client.get_logos(page=page, page_size=page_size)
                    for logo in page_result.get("results", []):
                        url = logo.get("url")
                        # First occurrence of a url wins (matches find_logo_by_url,
                        # which returned the first match).
                        if url and url not in logo_index:
                            logo_index[url] = logo
                    if not page_result.get("next"):
                        break
                    page += 1
                logger.debug("[CHANNELS-BULK] Built logo index: %s logos across %s page(s)", len(logo_index), page)

            existing = logo_index.get(logo_url)
            if existing is not None:
                return existing["id"]

            new_logo = await client.create_logo({"name": logo_name, "url": logo_url})
            created_logo_id = (
                new_logo.get("id") if isinstance(new_logo, dict) else None
            )
            ledger.record_write(journal_row=journal_row(
                action_type="logo_create",
                entity_id=created_logo_id,
                entity_name=logo_name,
                description=f"Created logo '{logo_name}' from {logo_url}",
                after_value={"name": logo_name, "url": logo_url},
            ))
            # Cache by url so a later op with the same logoUrl reuses it
            # (fixes a latent duplicate-logo bug too).
            logo_index[logo_url] = new_logo
            return new_logo["id"]

        # Phase 2: Process operations sequentially
        logger.debug("[CHANNELS-BULK] Phase 2: Processing %s operations", len(request.operations))

        def channel_name_of(cid: int) -> str:
            """Best available display name for a journal row's Entity column.

            ``existing_channels`` is the Phase 0 catalog fetch, and
            ``createChannel`` adds to it below, so a channel created earlier in
            the same batch is nameable too. The ``Channel {id}`` fallback keeps
            a row writable rather than dropping it, matching how the error
            builder names channels it cannot resolve.
            """
            return existing_channels.get(cid, {}).get("name") or f"Channel {cid}"

        # The lineup as it stood when THIS run started, frozen before Phase 2
        # begins to move `existing_channels` around (bead
        # enhancedchannelmanager-ic884.4).
        #
        # This, and not the running copy, is what an `expectedNumber` is checked
        # against. The question the caller is asking is "has anybody else moved
        # this channel since I looked?", and an earlier operation in this same
        # request moving it is not somebody else — comparing against the running
        # copy would report the caller's own plan back to them as a conflict.
        numbers_at_run_start: dict[int, Any] = {
            channel_id: channel.get("channel_number")
            for channel_id, channel in existing_channels.items()
        }

        def check_expected_number(op, channel_id: int) -> None:
            """Refuse to write a number over a change the caller has not seen."""
            expected = getattr(op, "expectedNumber", None)
            if expected is None or "channel_number" not in (op.data or {}):
                return
            if not channels_resolved:
                raise UnverifiableChannelNumberError(
                    f"Channel {channel_id} was not renumbered: the channel lineup "
                    "could not be read, so ECM could not check whether anybody else "
                    "changed its number first. Nothing about this channel was changed."
                )
            current = numbers_at_run_start.get(channel_id)
            if _same_channel_number_or_both_unset(current, expected.number):
                return
            raise ConcurrentChannelNumberChangeError(
                f"Channel {channel_id} was not renumbered: it is on channel number "
                f"{format_channel_number(current)}, and this request expected "
                f"{format_channel_number(expected.number)}. Somebody else changed it. "
                "Re-read the channel and decide before sending this again."
            )

        # Renumbers run in an order where a channel is moved onto a number only
        # once its previous holder has left it; everything else keeps the order
        # it was sent in, and every operation keeps the INDEX it was sent under
        # (bead enhancedchannelmanager-ic884.3).
        for idx, op in _numbering_execution_order(request.operations, existing_channels):
            op_id = f"op-{idx}-{op.type}"
            # Whether THIS operation's channel-number write has already landed.
            # Read by the failure handler below, which must not report a
            # numbering change as un-made when the PATCH went through and only
            # ECM's bookkeeping after it did not.
            numbering_landed = False
            # Exactly one outcome per operation, recorded by this loop rather
            # than by the branches. See bulk_commit_accounting.OperationLedger:
            # branches used to increment `operationsApplied` themselves, at
            # whatever point suited them, which let an operation be counted
            # twice (increment, then raise), zero times (a type no branch
            # claimed), or as a failure after its upstream write had landed.
            ledger.begin()
            try:
                if op.type == "updateChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] updateChannel: channel_id=%s, data=%s", idx+1, len(request.operations), channel_id, op.data)
                    if "channel_group_id" in op.data:
                        reject_unresolved_group(
                            op.data["channel_group_id"],
                            f"updateChannel on channel {channel_id}",
                        )
                    # Before the PATCH, and before anything else that could
                    # make this look like a partial write: a refused expectation
                    # must leave the channel exactly as it was.
                    check_expected_number(op, channel_id)
                    before_channel = existing_channels.get(channel_id, {})
                    changes, before_value, after_value = describe_channel_update(
                        before_channel, op.data
                    )
                    await client.update_channel(channel_id, op.data)
                    # The channel number moved, and it moved HERE. Recorded
                    # before anything else that could raise, for the same
                    # reason the journal row is: a write that landed and was
                    # not recorded is a write nothing can put back.
                    if "channel_number" in op.data:
                        compensator.record_landed(
                            channel_id=channel_id,
                            name=channel_name_of(channel_id),
                            before=before_channel.get("channel_number"),
                            after=op.data["channel_number"],
                        )
                        numbering_landed = True
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="update",
                        entity_id=channel_id,
                        entity_name=channel_name_of(channel_id),
                        description=f"Updated channel: {', '.join(changes)}",
                        before_value=before_value,
                        after_value=after_value,
                    ) if changes else nothing_to_journal(
                        f"every field the PATCH on channel {channel_id} carried "
                        "was already holding that value"
                    ))
                    if changes:
                        # Keep the local catalog current so a later op in the
                        # same batch names this channel by its NEW name.
                        if channel_id in existing_channels:
                            existing_channels[channel_id] = {
                                **existing_channels[channel_id], **after_value
                            }

                elif op.type == "addStreamToChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] addStreamToChannel: channel_id=%s, stream_id=%s", idx+1, len(request.operations), channel_id, op.streamId)
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    if op.streamId not in current_streams:
                        before_streams = list(current_streams)
                        current_streams.append(op.streamId)
                        channel_name = channel.get("name") or channel_name_of(channel_id)
                        await client.update_channel(channel_id, {"streams": current_streams})
                        ledger.record_persisted(journal_row=journal_row(
                            action_type="stream_add",
                            entity_id=channel_id,
                            entity_name=channel_name,
                            description=f"Added stream to channel '{channel_name}'",
                            before_value={"streams": before_streams},
                            after_value={"streams": list(current_streams)},
                        ))
                        logger.debug("[CHANNELS-BULK] Added stream %s to channel %s", op.streamId, channel_id)
                    else:
                        # No write happened, so no row — the single-channel
                        # endpoint returns early here for the same reason.
                        logger.debug("[CHANNELS-BULK] Stream %s already in channel %s, skipping", op.streamId, channel_id)

                elif op.type == "removeStreamFromChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] removeStreamFromChannel: channel_id=%s, stream_id=%s", idx+1, len(request.operations), channel_id, op.streamId)
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    if op.streamId in current_streams:
                        before_streams = list(current_streams)
                        current_streams.remove(op.streamId)
                        channel_name = channel.get("name") or channel_name_of(channel_id)
                        await client.update_channel(channel_id, {"streams": current_streams})
                        ledger.record_persisted(journal_row=journal_row(
                            action_type="stream_remove",
                            entity_id=channel_id,
                            entity_name=channel_name,
                            description=f"Removed stream from channel '{channel_name}'",
                            before_value={"streams": before_streams},
                            after_value={"streams": list(current_streams)},
                        ))
                        logger.debug("[CHANNELS-BULK] Removed stream %s from channel %s", op.streamId, channel_id)
                    else:
                        logger.debug("[CHANNELS-BULK] Stream %s not in channel %s, skipping", op.streamId, channel_id)

                elif op.type == "reorderChannelStreams":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] reorderChannelStreams: channel_id=%s, streams=%s", idx+1, len(request.operations), channel_id, op.streamIds)
                    # Guard against silent stream detachment (bd-1wq7z.25):
                    # Dispatcharr's ``streams`` field uses replace-semantics, so
                    # a partial streamIds list would detach the omitted streams.
                    # Require a true permutation of the channel's current set.
                    channel = await client.get_channel(channel_id)
                    current_streams = channel.get("streams", [])
                    perm_error = validate_stream_permutation(current_streams, op.streamIds)
                    if perm_error is not None:
                        logger.warning(
                            "[CHANNELS-BULK] reorderChannelStreams rejected for channel %s: %s",
                            channel_id, perm_error,
                        )
                        raise ValueError(
                            f"Cannot reorder streams for channel {channel_id}: {perm_error}"
                        )
                    channel_name = channel.get("name") or channel_name_of(channel_id)
                    await client.update_channel(channel_id, {"streams": op.streamIds})
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="stream_reorder",
                        entity_id=channel_id,
                        entity_name=channel_name,
                        description=f"Reordered streams in channel '{channel_name}'",
                        before_value={"streams": list(current_streams)},
                        after_value={"streams": list(op.streamIds)},
                    ))

                elif op.type == "bulkAssignChannelNumbers":
                    resolved_ids = [resolve_id(cid) for cid in op.channelIds]
                    logger.debug("[CHANNELS-BULK] [%s/%s] bulkAssignChannelNumbers: %s channels starting at %s", idx+1, len(request.operations), len(resolved_ids), op.startingNumber)
                    # One row per channel, matching POST /assign-numbers, which
                    # is the in-repo precedent for "renumbering is N per-channel
                    # facts, not one aggregate". Numbering is sequential from
                    # startingNumber in list order, mirroring the working copy
                    # the operator was shown. Every row is built from the state
                    # BEFORE the write, so they are all in hand the moment it
                    # lands.
                    assign_start = op.startingNumber if op.startingNumber is not None else 1
                    assigned_rows: list[dict] = []
                    renumbered: dict[int, int] = {}
                    for offset, assigned_id in enumerate(resolved_ids):
                        old_number = existing_channels.get(assigned_id, {}).get("channel_number")
                        new_number = assign_start + offset
                        if old_number == new_number:
                            continue
                        assigned_name = channel_name_of(assigned_id)
                        renumbered[assigned_id] = new_number
                        assigned_rows.append(journal_row(
                            action_type="reorder",
                            entity_id=assigned_id,
                            entity_name=assigned_name,
                            description=(
                                f"Changed channel number from {format_channel_number(old_number)} "
                                f"to {format_channel_number(new_number)}"
                            ),
                            before_value={"channel_number": old_number, "name": assigned_name},
                            after_value={"channel_number": new_number, "name": assigned_name},
                        ))
                    await client.assign_channel_numbers(resolved_ids, op.startingNumber)
                    # One upstream call, N channel numbers changed. Each is a
                    # separate fact to put back, exactly as each is a separate
                    # journal row.
                    for assigned_id, new_number in renumbered.items():
                        compensator.record_landed(
                            channel_id=assigned_id,
                            name=channel_name_of(assigned_id),
                            before=existing_channels.get(assigned_id, {}).get("channel_number"),
                            after=new_number,
                        )
                    numbering_landed = True
                    ledger.record_persisted(journal_row=assigned_rows or nothing_to_journal(
                        "every channel in the range already carried its target number"
                    ))
                    for assigned_id, new_number in renumbered.items():
                        if assigned_id in existing_channels:
                            existing_channels[assigned_id] = {
                                **existing_channels[assigned_id], "channel_number": new_number
                            }

                elif op.type == "createChannel":
                    logger.debug("[CHANNELS-BULK] [%s/%s] createChannel: name='%s', tempId=%s, groupId=%s, newGroupName=%s, normalize=%s", idx+1, len(request.operations), op.name, op.tempId, op.groupId, op.newGroupName, op.normalize)
                    # Resolve group ID
                    group_id = reject_unresolved_group(
                        resolve_group_id(op.groupId, op.newGroupName),
                        f"createChannel '{op.name}'",
                    )

                    # Apply normalization if requested. Same contract as the
                    # single-create path above: the op still applies, but a
                    # swallowed failure would leave the caller unable to tell
                    # this apart from `normalize=false`, so it is recorded in
                    # `normalizationFailures` (bead enhancedchannelmanager-e9e5o).
                    #
                    # The record is HELD until the create has actually
                    # persisted. It used to be appended here, before the create
                    # was attempted, so a create Dispatcharr then rejected
                    # appeared in `errors`/`operationsFailed` AND stayed in
                    # `normalizationFailures` with a `nameApplied` — and the
                    # MCP tool renders that list as channels "which were
                    # created with the name as given". The envelope contradicted
                    # itself about a channel that does not exist. Every entry in
                    # `normalizationFailures` names a channel that exists;
                    # `create_channel` raising below discards this local and the
                    # op is reported as a failure and nothing else.
                    channel_name = op.name
                    pending_normalization_failure: Optional[dict] = None
                    if op.normalize:
                        try:
                            with get_session() as db:
                                engine = get_normalization_engine(db)
                                # Offload normalization off event loop (bd-w3z4h)
                                norm_result = await run_cpu_bound(engine.normalize, op.name)
                                channel_name = norm_result.normalized
                                if channel_name != op.name:
                                    logger.debug("[CHANNELS-BULK] Normalized channel name: '%s' -> '%s'", op.name, channel_name)
                        except Exception as norm_err:
                            logger.warning("[CHANNELS-BULK] Failed to normalize channel name '%s': %s", op.name, norm_err)
                            # Continue with the original name, and disclose it
                            # once the channel exists.
                            pending_normalization_failure = {
                                "tempId": op.tempId,
                                "name": op.name,
                                "nameApplied": op.name,
                                "error": str(norm_err),
                            }

                    # Handle logo - if logoUrl provided but no logoId, resolve
                    # via the per-run logo index (bd-raehx) instead of
                    # re-paginating the whole catalog per channel.
                    logo_id = op.logoId
                    if not logo_id and op.logoUrl:
                        try:
                            logger.debug("[CHANNELS-BULK] Resolving logo by URL for channel '%s'", op.name)
                            logo_id = await resolve_logo_id(op.logoUrl, channel_name)
                            logger.debug("[CHANNELS-BULK] Resolved logo ID %s for channel '%s'", logo_id, op.name)
                        except Exception as logo_err:
                            logger.warning("[CHANNELS-BULK] Failed to create/find logo for channel '%s': %s", channel_name, logo_err)
                            # Continue without logo

                    # Create the channel
                    channel_data = {"name": channel_name}
                    if op.channelNumber is not None:
                        channel_data["channel_number"] = op.channelNumber
                    if group_id is not None:
                        channel_data["channel_group_id"] = group_id
                    if logo_id is not None:
                        channel_data["logo_id"] = logo_id
                    if op.tvgId is not None:
                        channel_data["tvg_id"] = op.tvgId
                    if op.tvcGuideStationId is not None:
                        channel_data["tvc_guide_stationid"] = op.tvcGuideStationId

                    logger.debug("[CHANNELS-BULK] op.tvgId=%s, op.tvcGuideStationId=%s", op.tvgId, op.tvcGuideStationId)
                    logger.debug("[CHANNELS-BULK] Creating channel with data: %s", channel_data)
                    new_channel = await client.create_channel(channel_data)

                    # Dispatcharr answered the POST without raising, so the
                    # channel EXISTS. Everything below this line is ECM's own
                    # bookkeeping, and none of it may turn a channel that exists
                    # into a reported total failure — an integrator retrying an
                    # apparent total failure creates the channel a second time
                    # (bead enhancedchannelmanager-e9e5o, fix round 4).
                    #
                    # The journal row goes WITH that statement, not after the
                    # bookkeeping (bead …-kz089, fix round 3). The malformed-id
                    # raise below used to sit between them, so a channel that
                    # existed was correctly reported as applied-but-incomplete
                    # and had no row anywhere — which is exactly the case where
                    # an operator has to reconcile by hand and needs one most.
                    # Everything the row needs comes from the response, read
                    # defensively because "malformed" is the case being served.
                    created_body = new_channel if isinstance(new_channel, dict) else {}
                    created_id = created_body.get("id")
                    created_name = created_body.get("name") or channel_name
                    created_number = created_body.get("channel_number", op.channelNumber)
                    ledger.record_persisted(
                        create_temp_id=op.tempId,
                        journal_row=journal_row(
                            action_type="create",
                            entity_id=created_id if isinstance(created_id, int)
                            and not isinstance(created_id, bool) else None,
                            entity_name=created_name,
                            description=(
                                f"Created channel '{created_name}'"
                                + (f" with number {format_channel_number(created_number)}"
                                   if created_number else "")
                            ),
                            after_value={
                                "channel_number": created_number,
                                "name": created_name,
                            },
                        ),
                    )
                    channels_created += 1

                    # The channel exists, so the raw name it carries is now a
                    # fact a caller can act on (bead enhancedchannelmanager-e9e5o).
                    if pending_normalization_failure is not None:
                        result["normalizationFailures"].append(pending_normalization_failure)

                    # A success body without a usable id. The channel is there
                    # and ECM cannot name it, which is a real problem — but it
                    # is an INCOMPLETE apply, not a failure. This used to be an
                    # unhandled `KeyError` on `new_channel["id"]` raised AFTER
                    # the normalization failure had been recorded, so the
                    # envelope reported the op as failed, listed it in `errors`,
                    # and listed it in `normalizationFailures` as a channel that
                    # had been created. `{"id": null}` did not even raise: it
                    # mapped the temp id to null and the frontend then posted
                    # `channelId: null` on every follow-up operation.
                    if not isinstance(created_id, int) or isinstance(created_id, bool):
                        raise MalformedCreateResponseError(
                            f"Dispatcharr accepted the create for '{channel_name}' but returned "
                            f"no usable channel id ({created_id!r}). The channel exists; ECM "
                            f"cannot map temp id {op.tempId} to it, so any operation in this "
                            "batch that referenced it will fail. Do not retry the create — "
                            "reconcile against Dispatcharr instead."
                        )

                    # Track temp ID -> real ID mapping
                    if op.tempId < 0:
                        result["tempIdMap"][op.tempId] = created_id

                    # Nameable by later ops in this same batch.
                    existing_channels[created_id] = new_channel
                    logger.debug("[CHANNELS-BULK] Created channel '%s' (temp: %s -> real: %s)", channel_name, op.tempId, created_id)

                elif op.type == "deleteChannel":
                    channel_id = resolve_id(op.channelId)
                    logger.debug("[CHANNELS-BULK] [%s/%s] deleteChannel: channel_id=%s", idx+1, len(request.operations), channel_id)
                    deleted_before = existing_channels.get(channel_id)
                    really_deleted = True
                    try:
                        await client.delete_channel(channel_id)
                        logger.debug("[CHANNELS-BULK] Deleted channel %s", channel_id)
                    except Exception as del_err:
                        if "404" in str(del_err) or "not found" in str(del_err).lower():
                            # Already gone: the op succeeds, but nothing changed
                            # here, so there is nothing to journal.
                            really_deleted = False
                            logger.debug("[CHANNELS-BULK] Channel %s already deleted, skipping", channel_id)
                        else:
                            raise
                    deleted_name = channel_name_of(channel_id)
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="delete",
                        entity_id=channel_id,
                        entity_name=deleted_name,
                        description=f"Deleted channel '{deleted_name}'",
                        before_value={
                            "name": deleted_name,
                            "channel_number": (deleted_before or {}).get("channel_number"),
                        },
                    ) if really_deleted else nothing_to_journal(
                        f"Dispatcharr answered 404 for channel {channel_id}: it was "
                        "already gone, so this run deleted nothing"
                    ))

                elif op.type == "createGroup":
                    logger.debug("[CHANNELS-BULK] [%s/%s] createGroup: name='%s'", idx+1, len(request.operations), op.name)
                    # Groups should be created in Phase 1, but handle here if needed
                    if op.name not in result["groupIdMap"]:
                        new_group = await client.create_channel_group(op.name)
                        # Same shape as Phase 1: the row is queued off the
                        # response before `new_group["id"]` can raise on one
                        # that carries no id.
                        ledger.record_persisted(journal_row=journal_row(
                            action_type="group_create",
                            entity_id=new_group.get("id") if isinstance(new_group, dict) else None,
                            entity_name=op.name,
                            description=f"Created channel group '{op.name}'",
                            after_value={"name": op.name},
                        ))
                        result["groupIdMap"][op.name] = new_group["id"]
                        groups_created += 1
                        logger.debug("[CHANNELS-BULK] Created group '%s' -> ID %s", op.name, new_group['id'])
                    else:
                        logger.debug("[CHANNELS-BULK] Group '%s' already exists with ID %s", op.name, result['groupIdMap'][op.name])

                elif op.type == "deleteChannelGroup":
                    logger.debug("[CHANNELS-BULK] [%s/%s] deleteChannelGroup: groupId=%s", idx+1, len(request.operations), op.groupId)
                    # Each reparent is an independent write that can be the last
                    # one this operation lands, so each is journalled as it
                    # happens rather than summarised after the delete succeeds
                    # (bead …-kz089, fix round 3). `record_write`, not
                    # `record_persisted`: a moved channel is not a deleted
                    # group, and the operation must stay reportable as the
                    # failure it is when the group survives.
                    deleted_group_id = op.groupId

                    def journal_moved_channel(
                        channel_id: int, channel_name: str, target_group: dict,
                        _group_id: int = deleted_group_id,
                    ) -> None:
                        target_name = target_group.get("name") or UNGROUPED_TARGET_GROUP_NAME
                        ledger.record_write(journal_row=journal_row(
                            action_type="update",
                            entity_id=channel_id,
                            entity_name=channel_name,
                            description=(
                                f"Moved channel '{channel_name}' to '{target_name}' "
                                f"before channel group {_group_id} was deleted"
                            ),
                            before_value={"channel_group_id": _group_id},
                            after_value={"channel_group_id": target_group.get("id")},
                        ))

                    moved = await reparent_group_channels(
                        client, op.groupId, log_prefix="[CHANNELS-BULK]",
                        on_channel_moved=journal_moved_channel,
                    )
                    await client.delete_channel_group(op.groupId)
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="group_delete",
                        entity_id=op.groupId,
                        entity_name=f"Group {op.groupId}",
                        description=(
                            f"Deleted channel group {op.groupId}"
                            + (f" (moved {moved} channel(s) to '{UNGROUPED_TARGET_GROUP_NAME}')" if moved else "")
                        ),
                        before_value={"group_id": op.groupId, "channels_moved": moved},
                    ))
                    logger.debug("[CHANNELS-BULK] Deleted group %s (moved %s channel(s) to '%s')", op.groupId, moved, UNGROUPED_TARGET_GROUP_NAME)

                elif op.type == "setProfileMembership":
                    channel_id = reject_unresolved_channel(
                        resolve_id(op.channelId),
                        f"setProfileMembership on profile {op.profileId}",
                    )
                    logger.debug("[CHANNELS-BULK] [%s/%s] setProfileMembership: profile=%s channel=%s enabled=%s", idx+1, len(request.operations), op.profileId, channel_id, op.enabled)
                    membership_name = channel_name_of(channel_id)
                    verb = "Enabled" if op.enabled else "Disabled"
                    await client.update_profile_channel(
                        op.profileId, channel_id, {"enabled": op.enabled}
                    )
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="profile_membership",
                        entity_id=channel_id,
                        entity_name=membership_name,
                        description=(
                            f"{verb} channel '{membership_name}' in channel profile {op.profileId}"
                        ),
                        after_value={"profile_id": op.profileId, "enabled": op.enabled},
                    ))

                elif op.type == "restoreChannelGroup":
                    logger.debug("[CHANNELS-BULK] [%s/%s] restoreChannelGroup: groupId=%s", idx+1, len(request.operations), op.groupId)
                    from models import HiddenChannelGroup
                    restored_name = None
                    with get_session() as db:
                        hidden = db.query(HiddenChannelGroup).filter_by(
                            group_id=op.groupId
                        ).first()
                        if hidden is not None:
                            restored_name = hidden.group_name
                            db.delete(hidden)
                            db.commit()
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="group_restore",
                        entity_id=op.groupId,
                        entity_name=restored_name,
                        description=f"Restored hidden channel group '{restored_name}'",
                        after_value={"group_id": op.groupId, "name": restored_name},
                    ) if restored_name is not None else nothing_to_journal(
                        # Not hidden any more: the op is a no-op, not a failure
                        # (another session may have restored it first), and no
                        # row was deleted.
                        f"channel group {op.groupId} was not hidden, so nothing "
                        "was restored"
                    ))

                elif op.type == "clearStreamStats":
                    logger.debug("[CHANNELS-BULK] [%s/%s] clearStreamStats: %s streams", idx+1, len(request.operations), len(op.streamIds))
                    from models import StreamStats
                    cleared = 0
                    if op.streamIds:
                        with get_session() as db:
                            cleared = db.query(StreamStats).filter(
                                StreamStats.stream_id.in_(op.streamIds)
                            ).delete(synchronize_session=False)
                            db.commit()
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="stream_stats_clear",
                        entity_id=None,
                        entity_name="Stream Stats",
                        description=(
                            f"Cleared probe stats for {cleared} stream(s)"
                        ),
                        before_value={"stream_ids": list(op.streamIds)},
                    ) if cleared else nothing_to_journal(
                        "none of the named streams had probe stats to clear"
                    ))

                elif op.type == "renameChannelGroup":
                    logger.debug("[CHANNELS-BULK] [%s/%s] renameChannelGroup: groupId=%s, newName='%s'", idx+1, len(request.operations), op.groupId, op.newName)
                    await client.update_channel_group(op.groupId, {"name": op.newName})
                    ledger.record_persisted(journal_row=journal_row(
                        action_type="group_rename",
                        entity_id=op.groupId,
                        entity_name=op.newName,
                        description=f"Renamed channel group {op.groupId} to '{op.newName}'",
                        after_value={"name": op.newName},
                    ))
                    logger.debug("[CHANNELS-BULK] Renamed group %s to '%s'", op.groupId, op.newName)

                else:
                    # No branch claimed this type. Counting it as applied would
                    # report work that never happened, and falling through
                    # silently — which is what used to happen — counted it as
                    # neither, so `applied + failed` quietly stopped equalling
                    # the batch. Pydantic's discriminated union makes this
                    # unreachable from the wire; it is the backstop for a new
                    # operation model added without a branch.
                    raise ValueError(
                        f"Unsupported bulk-commit operation type '{op.type}'"
                    )

                ledger.record_applied()

            except Exception as e:
                # Build detailed error info with channel/stream names
                error_details = {
                    "operationId": op_id,
                    "operationType": op.type,
                    "error": str(e),
                }

                # Add context based on operation type
                if hasattr(op, 'channelId'):
                    error_details["channelId"] = op.channelId
                    # Try to get channel name from our lookup
                    if op.channelId in existing_channels:
                        error_details["channelName"] = existing_channels[op.channelId].get("name", f"Channel {op.channelId}")
                    else:
                        error_details["channelName"] = f"Channel {op.channelId}"

                if hasattr(op, 'streamId'):
                    error_details["streamId"] = op.streamId
                    # Try to get stream name from our lookup
                    if op.streamId in existing_streams:
                        error_details["streamName"] = existing_streams[op.streamId].get("name", f"Stream {op.streamId}")
                    else:
                        error_details["streamName"] = f"Stream {op.streamId}"

                if hasattr(op, 'name'):
                    error_details["entityName"] = op.name

                # A channel number this run meant to change and did not. Paired
                # with the ones that DID change, this is what tells the
                # compensation pass below that the plan stopped part way rather
                # than finishing or never starting (bead
                # enhancedchannelmanager-ic884.3).
                if not numbering_landed:
                    if op.type == "updateChannel" and "channel_number" in (op.data or {}):
                        failed_channel_id = resolve_id(op.channelId)
                        compensator.record_failed(
                            channel_id=failed_channel_id,
                            name=channel_name_of(failed_channel_id),
                            intended=op.data["channel_number"],
                        )
                    elif op.type == "bulkAssignChannelNumbers":
                        failed_start = (
                            op.startingNumber if op.startingNumber is not None else 1
                        )
                        for offset, failed_id in enumerate(
                            resolve_id(cid) for cid in op.channelIds
                        ):
                            compensator.record_failed(
                                channel_id=failed_id,
                                name=channel_name_of(failed_id),
                                intended=failed_start + offset,
                            )

                # Log with detailed context
                channel_info = f" (channel: {error_details.get('channelName', 'N/A')})" if 'channelName' in error_details else ""
                stream_info = f" (stream: {error_details.get('streamName', 'N/A')})" if 'streamName' in error_details else ""
                logger.exception("[CHANNELS-BULK] Operation %s failed%s%s: %s", op_id, channel_info, stream_info, e)

                if ledger.persisted:
                    # The upstream write LANDED and only ECM's bookkeeping after
                    # it failed. Reporting this as a total failure is what makes
                    # an integrator retry and duplicate the entity, so it counts
                    # as applied and carries `applied: true` — the marker that
                    # tells a caller "this happened, and something about it is
                    # wrong" rather than "this did not happen". `success` is
                    # still false and `partial` still true, so nobody reads the
                    # batch as clean (bead enhancedchannelmanager-e9e5o).
                    error_details["applied"] = True
                    ledger.record_applied(incomplete=True)
                    logger.error(
                        "[CHANNELS-BULK] Operation %s APPLIED upstream but could not be "
                        "recorded; do not retry it: %s", op_id, e,
                    )
                else:
                    ledger.record_failed()
                result["errors"].append(error_details)

                # If continueOnError, keep processing; otherwise stop
                if not request.continueOnError:
                    logger.debug("[CHANNELS-BULK] Stopping due to error (continueOnError=false)")
                    # The remaining operations are never attempted, so neither
                    # counter may claim them. `abort_remaining` is what lets the
                    # accounting audit accept `applied + failed < len(operations)`
                    # here and nowhere else.
                    ledger.abort_remaining()
                    break
                else:
                    logger.debug("[CHANNELS-BULK] Continuing despite error (continueOnError=true)")
                # If continuing, keep going — but the batch is no longer a
                # success, and `partial` below is what records that some of it
                # still landed.

        logger.debug("[CHANNELS-BULK] Phase 2 complete: %s applied, %s failed", ledger.applied, ledger.failed)
        logger.debug("[CHANNELS-BULK] ID mappings: %s channels, %s groups", len(result['tempIdMap']), len(result['groupIdMap']))

        # Phase 2b: the numbering plan stopped part way, so write back what it
        # managed to change (bead enhancedchannelmanager-ic884.3).
        #
        # WHAT THIS IS AND IS NOT. It is a compensating write per landed change,
        # newest first, made with the same unguarded PATCH as the write it
        # undoes. There is no conditional update in Dispatcharr 0.28.x to build
        # anything stronger on (measured — see channel_number_apply.py), so a
        # change another client makes between the failure and this pass is
        # neither seen nor preserved. What it buys is that the operator is left
        # with the numbering they had rather than half of the numbering they
        # asked for, which is a state they can act on.
        #
        # A compensating write that fails ends up in `numberingRecovery`, which
        # names the channel, where it is, where it should be, and the single
        # step that closes the gap. That is the substitute for a guarantee, and
        # it is deliberately prescriptive: an unexplained middle is the one
        # outcome this whole pass exists to prevent.
        if compensator.half_applied:
            steps = compensator.compensation_steps()
            logger.warning(
                "[CHANNELS-BULK] Numbering stopped part way (batch=%s): %s landed, "
                "%s did not; writing %s channel(s) back",
                batch_id, len(compensator.landed), len(compensator.failed), len(steps),
            )
            unrepaired: list[tuple[NumberingWrite, Exception]] = []
            for step in steps:
                try:
                    await client.update_channel(
                        step.channel_id, {"channel_number": step.after}
                    )
                except Exception as comp_err:  # noqa: BLE001 — every failure is reportable
                    logger.exception(
                        "[CHANNELS-BULK] Could not put channel %s back on %s: %s",
                        step.channel_id, format_channel_number(step.after), comp_err,
                    )
                    unrepaired.append((step, comp_err))
                    continue
                # The write LANDED, so its row is queued now, exactly as every
                # other landed write's is. `record_write` and not
                # `record_persisted`: this is not any operation's outcome — the
                # operation it belongs to has already been counted as failed,
                # and counting it again would break the ledger's one-outcome
                # rule.
                ledger.record_write(journal_row=journal_row(
                    action_type="reorder",
                    entity_id=step.channel_id,
                    entity_name=step.name,
                    description=(
                        "Put channel number back to "
                        f"{format_channel_number(step.after)} after this batch's "
                        "numbering changes stopped part way"
                    ),
                    before_value={"channel_number": step.before, "name": step.name},
                    after_value={"channel_number": step.after, "name": step.name},
                ))
                if step.channel_id in existing_channels:
                    existing_channels[step.channel_id] = {
                        **existing_channels[step.channel_id],
                        "channel_number": step.after,
                    }
            if unrepaired:
                result["numberingRecovery"] = compensator.recovery_steps(unrepaired)
                result["errors"].append({
                    "operationId": "bulk-commit-numbering-recovery",
                    "error": (
                        f"{len(unrepaired)} channel(s) could not be put back on the "
                        "channel number they had before this batch. Each one is named "
                        "in numberingRecovery with the exact step that fixes it. Do "
                        "not retry the batch until they are fixed."
                    ),
                })
                # Not an operation failure: every operation already resolved.
                # This is the repair after them, and it has to be counted
                # somewhere or the envelope's own audit rejects the extra error
                # entry.
                ledger.record_setup_failure(aborted_run=False)

        # `finish()` writes the journal and THEN the accounting. Both used to be
        # inline here, at the very end of the happy path, which is precisely why
        # neither happened on any other exit.
        #
        # The accounting half derives `success` / `partial` from the ledger
        # rather than letting a branch assign them. A failed operation is a
        # failure whatever `continueOnError` says (bead …-ayfn9): that flag
        # answers "keep going after one fails?", NOT "call the batch a win if
        # anything landed" — and the old `failed == 0 or applied > 0` reading
        # meant a single successful op could launder every failure beside it
        # into `success=True`. Drill run 2026-08-08-run17: Delete Group raised
        # 400 server-side, the operator was told it worked, and the only trace
        # was an ERROR in the container log. `partial` still distinguishes
        # "some of it landed" from "none of it did", which the frontend renders
        # as "X succeeded, Y failed" rather than as a flat failure.
        # `backend/bulk_commit_accounting.py` states the whole invariant and
        # RAISES rather than returning an envelope that contradicts itself
        # (bead enhancedchannelmanager-e9e5o, fix round 4).
        return finish()

    except Exception as e:
        logger.exception("[CHANNELS-BULK] Unexpected error (batch=%s): %s", batch_id, e)
        result["errors"].append({
            "operationId": "bulk-commit",
            "error": str(e)
        })
        # Not an operation failure — the run itself fell over, possibly with
        # every operation already applied. `aborted_run` relaxes the
        # applied+failed == submitted check, because whatever was left in the
        # loop was never attempted.
        ledger.record_setup_failure(aborted_run=True)
        try:
            # Still the single exit: anything that landed upstream before the
            # crash gets its journal row here, which is the whole point of the
            # invariant. `finish` can itself raise if the envelope contradicts
            # the ledger, and an operator with a crashed batch needs the
            # envelope more than the audit, so that raise falls back to the raw
            # counts rather than propagating.
            return finish()
        except Exception as finish_err:
            logger.exception(
                "[CHANNELS-BULK] Could not finalize a crashed batch (batch=%s): %s",
                batch_id, finish_err,
            )
            result["operationsApplied"] = ledger.applied
            result["operationsFailed"] = ledger.failed
            result["success"] = False
            return result

    except BaseException:
        # NOT an Exception, so the handler above never sees it: a
        # `CancelledError` from application shutdown, `SystemExit`,
        # `KeyboardInterrupt`. Nothing to record and no envelope to return —
        # this clause exists only to tell the `finally` below that something is
        # already unwinding, so that whatever the flush hits there cannot take
        # its place (fix round 5). The `raise` is what keeps a cancelled task
        # cancelled.
        unwinding_base_exception = True
        raise

    finally:
        # The ways out that are NOT returns. `asyncio.CancelledError` inherits
        # from BaseException, so the handler above never saw it: a run that
        # created group A and was cancelled while awaiting group B left A
        # upstream with its row queued and never drained — and application
        # shutdown, which cancels this task, is the ordinary way that happens
        # rather than an exotic one (fix round 4). SystemExit and
        # KeyboardInterrupt take the same route for the same reason.
        #
        # `finally` rather than a wider `except`, because the cancellation must
        # keep propagating: catching it here to reach the flush would leave the
        # caller believing a cancelled task ran to completion, and `_runner`
        # re-raises precisely so a cancelled job says so. `flush_journal` is
        # idempotent, so this is a no-op on every path that already returned
        # through `finish()`, and it is SYNCHRONOUS, so it cannot be cancelled
        # a second time part-way through.
        #
        # The accounting half of `finish()` is deliberately NOT run here: there
        # is no envelope to return on this path, and `finalize_bulk_commit_result`
        # raising inside a `finally` would replace the CancelledError.
        #
        # The recovery below caught `Exception` and nothing else, which reopened
        # the same hole from the other side (fix round 5): a synchronous
        # dependency raising a BaseException AFTER `drain_journal_rows()` had
        # emptied the queue escaped the `finally`, replaced the CancelledError —
        # so `task.cancelled()` was no longer true — and carried the drained rows
        # with it. `write_journal_rows` now logs every row it has not resolved
        # before letting a BaseException past, and this clause refuses to raise
        # while one is already unwinding. That is the whole reason
        # `unwinding_base_exception` exists rather than a bare `except
        # BaseException: pass`: swallowing unconditionally would mean a flush
        # that raised a CancelledError on the ORDINARY return path silently
        # uncancelled the task, which is the same bug pointing the other way.
        try:
            flush_journal()
        except Exception as flush_err:  # noqa: BLE001 — must not mask the unwind
            logger.exception(
                "[CHANNELS-BULK] Journal flush failed while unwinding "
                "(batch=%s): %s", batch_id, flush_err,
            )
        except BaseException as flush_base:
            logger.exception(
                "[CHANNELS-BULK] Journal flush raised a BaseException "
                "(batch=%s): %s", batch_id, flush_base,
            )
            if not unwinding_base_exception:
                raise


@router.post("/normalize-preview-batch")
async def normalize_preview_batch(request: NormalizePreviewBatchRequest):
    """bd-eio04.13 — batch would-normalize preview for channel list rows.

    Per-row result:

        {
          "channel_id": int,
          "current_name": str,
          "proposed_name": str,
          "would_change": bool,
          "transformations": [{rule_id, before, after}, ...],
        }

    Prefers the `channels` payload ({channel_id, name}) when provided —
    no Dispatcharr roundtrip, O(rules × M) total cost. Falls back to
    `channel_ids` for callers that only have IDs (e.g. deep-link flows);
    in that case the backend fetches each name from Dispatcharr and
    silently skips rows it can't resolve.

    Batch size is capped at NORMALIZE_PREVIEW_BATCH_MAX (100) rows. The
    frontend is responsible for paging above that window.
    """
    if request.channels is not None and request.channel_ids is not None:
        raise HTTPException(
            status_code=400,
            detail="Provide exactly one of 'channels' or 'channel_ids', not both.",
        )

    rows = request.channels or []
    ids_only: list[int] = list(request.channel_ids or [])
    total = len(rows) + len(ids_only)

    if total > NORMALIZE_PREVIEW_BATCH_MAX:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many rows ({total}); maximum "
                f"{NORMALIZE_PREVIEW_BATCH_MAX} per batch."
            ),
        )

    if total == 0:
        return {"results": []}

    logger.debug(
        "[CHANNELS] POST /normalize-preview-batch - rows=%s ids_only=%s",
        len(rows), len(ids_only),
    )

    session = get_session()
    try:
        engine = get_normalization_engine(session)
        results: list[dict] = []
        start = time.time()

        # Fast path: names already supplied by caller.
        for row in rows:
            current_name = row.name or ""
            preview = engine.normalize(current_name)
            proposed = preview.normalized
            results.append({
                "channel_id": row.channel_id,
                "current_name": current_name,
                "proposed_name": proposed,
                "would_change": proposed != current_name,
                "transformations": [
                    {"rule_id": t[0], "before": t[1], "after": t[2]}
                    for t in (preview.transformations or [])
                ],
            })

        # Fallback path: fetch names for bare IDs via Dispatcharr.
        if ids_only:
            client = get_client()
            for cid in ids_only:
                try:
                    channel = await client.get_channel(cid)
                except Exception as exc:
                    logger.debug("[CHANNELS] normalize-preview: skipped id=%s: %s", cid, exc)
                    continue
                current_name = (channel or {}).get("name") or ""
                preview = engine.normalize(current_name)
                proposed = preview.normalized
                results.append({
                    "channel_id": cid,
                    "current_name": current_name,
                    "proposed_name": proposed,
                    "would_change": proposed != current_name,
                    "transformations": [
                        {"rule_id": t[0], "before": t[1], "after": t[2]}
                        for t in (preview.transformations or [])
                    ],
                })

        elapsed_ms = (time.time() - start) * 1000
        logger.debug(
            "[CHANNELS] normalize-preview-batch: resolved %d/%d in %.1fms",
            len(results), total, elapsed_ms,
        )
        return {"results": results}
    finally:
        session.close()


@router.post("/clear-auto-created")
async def clear_auto_created_flag(request: ClearAutoCreatedRequest, _admin=RequireAdminIfEnabled):
    """Clear the auto_created flag from all channels in the specified groups.

    This converts auto_created channels to manual channels by setting
    auto_created=False and auto_created_by=None.

    Admin only (destructive bulk operator op, bd-um30y).
    """
    logger.debug("[CHANNELS] POST /channels/clear-auto-created - group_ids=%s", request.group_ids)
    client = get_client()
    group_ids = set(request.group_ids)

    if not group_ids:
        raise HTTPException(status_code=400, detail="No group IDs provided")

    try:
        # Fetch all channels and find auto_created ones in the specified groups
        start = time.time()
        channels_to_update = []
        page = 1

        while True:
            result = await client.get_channels(page=page, page_size=500)
            page_channels = result.get("results", [])

            for channel in page_channels:
                if channel.get("auto_created") and channel.get("channel_group_id") in group_ids:
                    channels_to_update.append({
                        "id": channel.get("id"),
                        "name": channel.get("name"),
                        "channel_number": channel.get("channel_number"),
                        "channel_group_id": channel.get("channel_group_id"),
                    })

            if not result.get("next"):
                break
            page += 1
            if page > 50:  # Safety limit
                break

        if not channels_to_update:
            return {
                "status": "ok",
                "message": "No auto_created channels found in the specified groups",
                "updated_count": 0,
                "updated_channels": [],
                "failed_channels": [],
            }

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Fetched channels for auto_created clearing in %.1fms", elapsed_ms)
        logger.info("[CHANNELS] Clearing auto_created flag from %s channels in groups %s", len(channels_to_update), group_ids)

        # Update each channel via Dispatcharr API
        start = time.time()
        updated_channels = []
        failed_channels = []

        for channel in channels_to_update:
            channel_id = channel["id"]
            try:
                await client.update_channel(channel_id, {
                    "auto_created": False,
                    "auto_created_by": None,
                })
                updated_channels.append(channel)
                logger.debug("[CHANNELS] Cleared auto_created flag from channel %s (%s)", channel_id, channel['name'])
            except Exception as update_err:
                failed_channels.append({**channel, "error": str(update_err)})
                logger.error("[CHANNELS] Failed to clear auto_created flag from channel %s: %s", channel_id, update_err)

        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Updated %s channels (cleared auto_created) in %.1fms", len(updated_channels), elapsed_ms)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="bulk_update",
            entity_id=None,
            entity_name="Clear Auto-Created Flag",
            description=f"Cleared auto_created flag from {len(updated_channels)} channels in {len(group_ids)} group(s)",
            after_value={
                "group_ids": list(group_ids),
                "updated_count": len(updated_channels),
                "failed_count": len(failed_channels),
            },
        )

        return {
            "status": "ok",
            "message": f"Cleared auto_created flag from {len(updated_channels)} channel(s)",
            "updated_count": len(updated_channels),
            "updated_channels": updated_channels[:20],  # Limit response size
            "failed_channels": failed_channels,
        }
    except Exception as e:
        logger.exception("[CHANNELS] Failed to clear auto_created flags: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# Channel by ID routes — must come after all static routes
# ---------------------------------------------------------------------------

@router.get("/{channel_id}")
async def get_channel(channel_id: int):
    """Get channel details by ID."""
    logger.debug("[CHANNELS] GET /channels/%s", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Fetched channel id=%s in %.1fms", channel_id, elapsed_ms)
        return result
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — return 404, not 500
        # (bd-1wq7z.22).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Fetch channel id=%s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to fetch channel id=%s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{channel_id}/normalize-preview")
async def normalize_preview_single(channel_id: int):
    """bd-eio04.13 — would-normalize preview for a single channel.

    Returns `{channel_id, current_name, proposed_name, would_change,
    transformations}` by fetching the channel from Dispatcharr and running
    the active NormalizationEngine against its current name.
    """
    logger.debug("[CHANNELS] GET /channels/%s/normalize-preview", channel_id)
    client = get_client()
    try:
        start = time.time()
        channel = await client.get_channel(channel_id)
        current_name = (channel or {}).get("name") or ""

        session = get_session()
        try:
            engine = get_normalization_engine(session)
            preview = engine.normalize(current_name)
        finally:
            session.close()

        proposed = preview.normalized
        elapsed_ms = (time.time() - start) * 1000
        logger.debug(
            "[CHANNELS] normalize-preview id=%s '%s' -> '%s' in %.1fms",
            channel_id, current_name, proposed, elapsed_ms,
        )
        return {
            "channel_id": channel_id,
            "current_name": current_name,
            "proposed_name": proposed,
            "would_change": proposed != current_name,
            "transformations": [
                {"rule_id": t[0], "before": t[1], "after": t[2]}
                for t in (preview.transformations or [])
            ],
        }
    except Exception as e:
        logger.exception("[CHANNELS] Failed normalize-preview for id=%s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/{channel_id}/streams")
async def get_channel_streams(channel_id: int):
    """Get streams assigned to a channel."""
    logger.debug("[CHANNELS] GET /channels/%s/streams", channel_id)
    client = get_client()
    try:
        start = time.time()
        result = await client.get_channel_streams(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Fetched streams for channel id=%s in %.1fms", channel_id, elapsed_ms)
        return result
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — return 404, not 500
        # (bd-8w1ba). Common trigger: callers pass a channel NUMBER (shown in the
        # UI) rather than the internal channel id.
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning(
                "[CHANNELS] Upstream error fetching streams for channel id=%s: %s",
                channel_id, e,
            )
            raise mapped
        logger.exception("[CHANNELS] Failed to fetch streams for channel id=%s", channel_id)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.patch("/{channel_id}")
async def update_channel(channel_id: int, data: dict, _admin=RequireAdminIfEnabled):
    """Update a channel. Admin only (operator-only write, bd-v7n9f).

    Answers with Dispatcharr's updated channel plus ``journalRowsUnwritten``:
    the number of this request's journal rows that could NOT be written, always
    present so a caller checks the number rather than probing for a key. It is
    the same advisory the bulk-commit envelope carries, and it is an advisory on
    a ``200`` rather than a ``5xx`` for the same reason — the PATCH LANDED, and
    reporting a failure to a caller whose change already applied is what makes
    an integrator retry it (bead ``enhancedchannelmanager-kz089``, fix round 5).
    """
    logger.debug("[CHANNELS] PATCH /channels/%s - data=%s", channel_id, data)
    # The body is an untyped field bag, so the canonical channel-number contract
    # is applied by key rather than by field type (bead
    # enhancedchannelmanager-ic884.1).
    try:
        validate_channel_number_in_payload(data)
    except InvalidChannelNumberError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    client = get_client()
    try:
        # Get before state for logging
        start = time.time()
        before_channel = await client.get_channel(channel_id)

        result = await client.update_channel(channel_id, data)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Updated channel %s via API in %.1fms", channel_id, elapsed_ms)

        # Determine what changed for description and build before/after values.
        # Shared with the bulk-commit executor so Edit Mode's Apply All and this
        # handler describe the same edit identically (bead
        # enhancedchannelmanager-r9py9).
        changes, before_value, after_value = describe_channel_update(before_channel, data)

        unwritten = 0
        if changes:
            logger.info("[CHANNELS] Updated channel id=%s: %s", channel_id, ', '.join(changes))
            # Through the shared writer, which CHECKS the return value. Round 2
            # gave the bulk path this treatment because `journal.log_entries`
            # reports failure by returning `False`; `journal.log_entry` reports
            # it by returning `None`, and this call site discarded that, so a
            # journal database that was read-only, unavailable or full produced
            # a landed Dispatcharr change with no row and a 200 that said
            # nothing (fix round 5).
            unwritten = write_journal_rows([{
                "category": "channel",
                "action_type": "update",
                "entity_id": channel_id,
                "entity_name": result.get("name", before_channel.get("name", "Unknown")),
                "description": f"Updated channel: {', '.join(changes)}",
                "before_value": before_value,
                "after_value": after_value,
            }])
        else:
            logger.debug("[CHANNELS] No changes detected for channel %s", channel_id)

        if isinstance(result, dict):
            result["journalRowsUnwritten"] = unwritten
        elif unwritten:
            # Dispatcharr answered with something that is not an object, so
            # there is nowhere to hang the advisory. `write_journal_rows` has
            # already logged the row itself; this names why the caller will not
            # be told, rather than leaving the omission to be inferred.
            logger.error(
                "[CHANNELS] %s journal row(s) unwritten for channel %s and the "
                "upstream response is not an object, so the caller cannot be "
                "told: %r", unwritten, channel_id, type(result).__name__,
            )

        return result
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id (or bad group/logo/etc.) surfaces as an upstream
        # 4xx — map it to a clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Update channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to update channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/merge")
async def merge_channels(request: "MergeChannelsRequest", _admin=RequireAdminIfEnabled):
    """Merge multiple channels into a single new channel.

    Creates a new channel with the specified metadata, moves all streams
    from the source channels into it (preserving order, deduplicating),
    then deletes the source channels.

    Admin only (destructive operator op — deletes channels, bd-um30y).
    """
    logger.debug("[CHANNELS] POST /channels/merge - sources=%s target_name=%s",
                 request.source_channel_ids, request.target_name)

    if len(request.source_channel_ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 source channels are required")

    client = get_client()
    new_channel = None
    try:
        # 1. Fetch all source channels and collect their streams (ordered, deduplicated).
        #    If any source ID no longer exists upstream (e.g., the operator's UI
        #    held a stale reference after a previous merge), surface 422 with a
        #    refresh hint instead of falling through to the catch-all 500. The
        #    UI fix in useEditMode keeps these stale IDs from accumulating in the
        #    first place; this is defense in depth for any other stale-state path.
        source_channels = []
        all_streams: list[int] = []
        seen_streams: set[int] = set()
        missing_ids: list[int] = []
        for cid in request.source_channel_ids:
            try:
                channel = await client.get_channel(cid)
            except httpx.HTTPStatusError as fetch_err:
                if fetch_err.response.status_code == 404:
                    missing_ids.append(cid)
                    continue
                raise
            source_channels.append(channel)
            for sid in channel.get("streams", []):
                if sid not in seen_streams:
                    all_streams.append(sid)
                    seen_streams.add(sid)

        if missing_ids:
            logger.warning("[CHANNELS] Merge rejected: stale source IDs %s no longer exist", missing_ids)
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Source channels {missing_ids} no longer exist — "
                    "refresh the channels list and try again"
                ),
            )

        # 2. Create the new merged channel
        create_data = {"name": request.target_name}
        if request.target_channel_number is not None:
            create_data["channel_number"] = request.target_channel_number
        if request.target_channel_group_id is not None:
            create_data["channel_group_id"] = request.target_channel_group_id
        if request.target_logo_id is not None:
            create_data["logo_id"] = request.target_logo_id
        if request.target_tvg_id is not None:
            create_data["tvg_id"] = request.target_tvg_id

        new_channel = await client.create_channel(create_data)
        new_channel_id = new_channel["id"]
        logger.info("[CHANNELS] Created merged channel id=%s name=%s", new_channel_id, request.target_name)

        # 3. Assign streams to the new channel
        if all_streams:
            await client.update_channel(new_channel_id, {"streams": all_streams})
            logger.info("[CHANNELS] Assigned %d streams to merged channel %s", len(all_streams), new_channel_id)

        # 4. Update EPG data if specified
        update_data = {}
        if request.target_epg_data_id is not None:
            update_data["epg_data_id"] = request.target_epg_data_id
        if request.target_stream_profile_id is not None:
            update_data["stream_profile_id"] = request.target_stream_profile_id
        if update_data:
            await client.update_channel(new_channel_id, update_data)

        # 5. Delete source channels
        deleted_ids = []
        for cid in request.source_channel_ids:
            try:
                await client.delete_channel(cid)
                deleted_ids.append(cid)
                logger.debug("[CHANNELS] Deleted source channel %s during merge", cid)
            except Exception as del_err:
                logger.warning("[CHANNELS] Failed to delete source channel %s during merge: %s", cid, del_err)

        # 6. Fetch the final state of the merged channel
        result = await client.get_channel(new_channel_id)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="merge",
            entity_id=new_channel_id,
            entity_name=request.target_name,
            description=f"Merged {len(source_channels)} channels into '{request.target_name}'",
            before_value={"source_channels": [{"id": ch.get("id"), "name": ch.get("name")} for ch in source_channels]},
            after_value={"merged_channel_id": new_channel_id, "stream_count": len(all_streams), "deleted_source_ids": deleted_ids},
        )

        logger.info("[CHANNELS] Merge complete: %d channels -> '%s' (id=%s, %d streams)",
                     len(source_channels), request.target_name, new_channel_id, len(all_streams))

        return result

    except HTTPException:
        raise
    except Exception as e:
        # Rollback: delete the new channel if it was created
        if new_channel:
            try:
                await client.delete_channel(new_channel["id"])
                logger.info("[CHANNELS] Rolled back merged channel %s after error", new_channel["id"])
            except Exception:
                logger.warning("[CHANNELS] Failed to rollback merged channel %s", new_channel["id"])
        # Map an upstream 4xx (e.g. a bad target group/logo id) to a clean 4xx
        # instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Channel merge rejected by Dispatcharr: %s", e)
            raise mapped
        logger.exception("[CHANNELS] Channel merge failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/{channel_id}")
async def delete_channel(channel_id: int, _admin=RequireAdminIfEnabled):
    """Delete a channel. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS] DELETE /channels/%s", channel_id)
    client = get_client()
    try:
        # Get channel info before deleting for logging
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")

        await client.delete_channel(channel_id)
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Deleted channel %s via API in %.1fms", channel_id, elapsed_ms)
        logger.info("[CHANNELS] Deleted channel id=%s name=%s", channel_id, channel_name)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="delete",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Deleted channel '{channel_name}'",
            before_value={"name": channel_name, "channel_number": channel.get("channel_number")},
        )

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — return 404, not 500
        # (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Delete channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to delete channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/add-stream")
async def add_stream_to_channel(channel_id: int, request: AddStreamRequest, _admin=RequireAdminIfEnabled):
    """Add a stream to a channel. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS] POST /channels/%s/add-stream - stream_id=%s", channel_id, request.stream_id)
    client = get_client()
    try:
        # Get current channel
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = channel.get("streams", [])

        # Add stream if not already present
        if request.stream_id not in current_streams:
            before_streams = list(current_streams)
            current_streams.append(request.stream_id)
            result = await client.update_channel(channel_id, {"streams": current_streams})
            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[CHANNELS] Added stream to channel %s via API in %.1fms", channel_id, elapsed_ms)
            logger.info("[CHANNELS] Added stream %s to channel id=%s name=%s", request.stream_id, channel_id, channel_name)

            # Log to journal
            journal.log_entry(
                category="channel",
                action_type="stream_add",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Added stream to channel '{channel_name}'",
                before_value={"streams": before_streams},
                after_value={"streams": current_streams},
            )

            return result
        logger.debug("[CHANNELS] Stream %s already in channel %s", request.stream_id, channel_id)
        return channel
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel/stream id surfaces as an upstream 4xx — map it to a
        # clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Add stream %s to channel %s rejected by Dispatcharr: %s", request.stream_id, channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to add stream %s to channel %s: %s", request.stream_id, channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/add-streams")
async def add_streams_to_channel(channel_id: int, request: AddStreamsRequest, _admin=RequireAdminIfEnabled):
    """Add multiple streams to a channel in a single Dispatcharr roundtrip.

    Admin only (operator-only write, bd-v7n9f).

    Mirrors the single-add semantics (dedup against streams already on the
    channel, append in request order) but fetches the channel once and PUTs
    once instead of once per stream — the MCP ``bulk_add_streams_to_channel``
    tool used to loop the single-add endpoint, which times out on slow
    hardware for batches of ~10 streams (bd-02xjj / GH #223).
    """
    logger.debug("[CHANNELS] POST /channels/%s/add-streams - %d stream_ids", channel_id, len(request.stream_ids))
    client = get_client()
    try:
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = list(channel.get("streams", []))
        before_streams = list(current_streams)
        existing = set(current_streams)

        added: list[int] = []
        skipped: list[int] = []
        for sid in request.stream_ids:
            if sid in existing:
                skipped.append(sid)
            else:
                current_streams.append(sid)
                existing.add(sid)
                added.append(sid)

        if not added:
            logger.debug("[CHANNELS] No new streams to add to channel %s (all %d already present)",
                         channel_id, len(request.stream_ids))
            return {"channel": channel, "added": [], "skipped": skipped, "total_streams": len(current_streams)}

        result = await client.update_channel(channel_id, {"streams": current_streams})
        elapsed_ms = (time.time() - start) * 1000
        logger.info("[CHANNELS] Added %d stream(s) to channel id=%s name=%s (%d skipped) in %.1fms",
                    len(added), channel_id, channel_name, len(skipped), elapsed_ms)

        journal.log_entry(
            category="channel",
            action_type="stream_add",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Added {len(added)} stream(s) to channel '{channel_name}'",
            before_value={"streams": before_streams},
            after_value={"streams": current_streams},
        )

        return {"channel": result, "added": added, "skipped": skipped, "total_streams": len(current_streams)}
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel/stream id surfaces as an upstream 4xx — map it to a
        # clean 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Add streams to channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to add streams to channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/remove-stream")
async def remove_stream_from_channel(channel_id: int, request: RemoveStreamRequest, _admin=RequireAdminIfEnabled):
    """Remove a stream from a channel. Admin only (operator-only write, bd-v7n9f)."""
    logger.debug("[CHANNELS] POST /channels/%s/remove-stream - stream_id=%s", channel_id, request.stream_id)
    client = get_client()
    try:
        # Get current channel
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        current_streams = channel.get("streams", [])

        # Remove stream if present
        if request.stream_id in current_streams:
            before_streams = list(current_streams)
            current_streams.remove(request.stream_id)
            result = await client.update_channel(channel_id, {"streams": current_streams})
            elapsed_ms = (time.time() - start) * 1000
            logger.debug("[CHANNELS] Removed stream from channel %s via API in %.1fms", channel_id, elapsed_ms)
            logger.info("[CHANNELS] Removed stream %s from channel id=%s name=%s", request.stream_id, channel_id, channel_name)

            # Log to journal
            journal.log_entry(
                category="channel",
                action_type="stream_remove",
                entity_id=channel_id,
                entity_name=channel_name,
                description=f"Removed stream from channel '{channel_name}'",
                before_value={"streams": before_streams},
                after_value={"streams": current_streams},
            )

            return result
        logger.debug("[CHANNELS] Stream %s not in channel %s", request.stream_id, channel_id)
        return channel
    except HTTPException:
        raise
    except Exception as e:
        # A missing channel id surfaces as an upstream 404 — map it to a clean
        # 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Remove stream %s from channel %s rejected by Dispatcharr: %s", request.stream_id, channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to remove stream %s from channel %s: %s", request.stream_id, channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/{channel_id}/reorder-streams")
async def reorder_channel_streams(channel_id: int, request: ReorderStreamsRequest, _admin=RequireAdminIfEnabled):
    """Reorder streams within a channel.

    Admin only (operator-only write, bd-v7n9f).

    This endpoint REPLACES the channel's stream set with the supplied list, so
    the list must be a true reorder — a permutation of the channel's *current*
    streams. A partial list would silently DETACH the omitted streams (data
    loss). To guard against that (bd-1wq7z.3) we validate that the request is a
    permutation before writing: same set of ids, no missing, no unknown, no
    duplicates. Use add-stream / remove-stream to change membership.
    """
    logger.debug("[CHANNELS] POST /channels/%s/reorder-streams - stream_ids=%s", channel_id, request.stream_ids)
    client = get_client()
    try:
        # Get before state
        start = time.time()
        channel = await client.get_channel(channel_id)
        channel_name = channel.get("name", "Unknown")
        before_streams = channel.get("streams", [])

        # Streams normally come back as bare ints, but defensively handle the
        # case where upstream returns stream objects ({"id": ...}).
        before_ids = [
            (s.get("id") if isinstance(s, dict) else s)
            for s in before_streams
        ]

        # --- Permutation guard (data-loss protection) ---
        requested = request.stream_ids
        requested_set = set(requested)
        before_set = set(before_ids)

        duplicate_ids = sorted({sid for sid in requested if requested.count(sid) > 1})
        missing_ids = sorted(before_set - requested_set)      # on channel, absent from list -> would detach
        unknown_ids = sorted(requested_set - before_set)      # in list, not on channel

        if duplicate_ids or missing_ids or unknown_ids:
            problems = []
            if missing_ids:
                problems.append(f"Missing (would be detached): {missing_ids}.")
            if unknown_ids:
                problems.append(f"Unknown (not on this channel): {unknown_ids}.")
            if duplicate_ids:
                problems.append(f"Duplicated: {duplicate_ids}.")
            detail = (
                "reorder-streams expects all of the channel's current streams in "
                "the desired order. " + " ".join(problems) + " Use "
                "remove_stream_from_channel to detach, or add_stream_to_channel "
                "to add."
            )
            logger.warning(
                "[CHANNELS] Rejected reorder-streams for channel %s (not a permutation): %s",
                channel_id, detail,
            )
            raise HTTPException(status_code=400, detail=detail)

        result = await client.update_channel(channel_id, {"streams": request.stream_ids})
        elapsed_ms = (time.time() - start) * 1000
        logger.debug("[CHANNELS] Reordered streams for channel %s via API in %.1fms", channel_id, elapsed_ms)

        # Log to journal
        journal.log_entry(
            category="channel",
            action_type="stream_reorder",
            entity_id=channel_id,
            entity_name=channel_name,
            description=f"Reordered streams in channel '{channel_name}'",
            before_value={"streams": before_streams},
            after_value={"streams": request.stream_ids},
        )

        return result
    except HTTPException:
        # Validation rejections (e.g. the permutation guard above) are
        # intentional client errors — let them propagate unchanged rather than
        # masking them as a 500.
        raise
    except Exception as e:
        # A missing channel id (or a stream id not on the channel that slips past
        # the permutation guard) surfaces as an upstream 4xx — map it to a clean
        # 4xx instead of an opaque 500 (bd-lq38l.4).
        mapped = upstream_http_exception(e)
        if mapped is not None:
            logger.warning("[CHANNELS] Reorder streams for channel %s rejected by Dispatcharr: %s", channel_id, e)
            raise mapped
        logger.exception("[CHANNELS] Failed to reorder streams for channel %s: %s", channel_id, e)
        raise HTTPException(status_code=500, detail="Internal server error")


# =============================================================================
# Find & Merge Duplicate Channels
# =============================================================================


@router.post("/find-duplicates")
async def find_duplicate_channels(request: Optional[FindDuplicatesRequest] = None):
    """Scan channels and find duplicates by normalized name.

    Applies the user's normalization rules to every channel name,
    then groups channels that resolve to the same normalized name.
    Returns only groups with 2+ channels.

    Scope (enhancedchannelmanager-uahp6): when ``request.channel_ids`` is
    provided, the scan is restricted to those channel ids. Absent body /
    absent field = global scan across all channels (backward compatible —
    MCP and script callers that never send a body keep working unchanged).
    An explicit empty list scopes the scan to nothing (see
    ``FindDuplicatesRequest`` docstring).
    """
    scoped_ids: Optional[set[int]] = None
    if request is not None and request.channel_ids is not None:
        scoped_ids = set(request.channel_ids)
    # GH #645 / bead 0vao3: opt-in whitespace/case fold on the grouping key —
    # same canonicalization the auto-creation fold_match_key rule flag uses.
    fold = bool(request.fold_match_key) if request is not None else False

    logger.debug("[CHANNELS] POST /channels/find-duplicates scoped=%s fold=%s",
                 scoped_ids is not None, fold)
    from normalization_engine import get_normalization_engine

    client = get_client()
    session = get_session()

    try:
        engine = get_normalization_engine(session)

        # Fetch channels (paginated). Dispatcharr's list endpoint has no
        # id-filter, so a scoped scan still has to walk pages — but it filters
        # each page down to the requested ids and stops early once all of
        # them have been found, instead of walking the whole install for a
        # handful of selected channels.
        all_channels = []
        if scoped_ids is None:
            page = 1
            while True:
                result = await client.get_channels(page=page, page_size=500)
                batch = result.get("results", [])
                if not batch:
                    break
                all_channels.extend(batch)
                if not result.get("next"):
                    break
                page += 1
        elif scoped_ids:
            remaining = set(scoped_ids)
            page = 1
            while remaining:
                result = await client.get_channels(page=page, page_size=500)
                batch = result.get("results", [])
                if not batch:
                    break
                matched = [ch for ch in batch if ch.get("id") in remaining]
                all_channels.extend(matched)
                remaining -= {ch.get("id") for ch in matched}
                if not result.get("next"):
                    break
                page += 1
        # else: scoped_ids == set() — explicit empty scope, nothing to fetch.

        if scoped_ids is not None:
            logger.info(
                "[CHANNELS] find-duplicates: scanning %d channels (scoped to %d selected)",
                len(all_channels), len(scoped_ids),
            )
        else:
            logger.info("[CHANNELS] find-duplicates: scanning %d channels", len(all_channels))

        # Group by normalized name
        groups: dict[str, list[dict]] = {}
        for ch in all_channels:
            name = ch.get("name", "")
            norm_result = engine.normalize(name)
            normalized = norm_result.normalized.strip()
            if not normalized:
                continue

            key = fold_match_key(normalized) if fold else normalized.lower()
            if key not in groups:
                groups[key] = []
            groups[key].append({
                "id": ch.get("id"),
                "name": name,
                "normalized_name": normalized,
                "channel_number": ch.get("channel_number"),
                "stream_count": len(ch.get("streams", [])),
                "channel_group_id": ch.get("channel_group_id"),
                "channel_group_name": ch.get("channel_group_name", ""),
            })

        # Filter to groups with duplicates
        duplicates = [
            {
                "normalized_name": channels[0]["normalized_name"],
                "channels": sorted(channels, key=lambda c: -(c["stream_count"] or 0)),
            }
            for channels in groups.values()
            if len(channels) >= 2
        ]

        # Sort by number of duplicates (worst first)
        duplicates.sort(key=lambda g: -len(g["channels"]))

        total_dup_channels = sum(len(g["channels"]) for g in duplicates)
        logger.info("[CHANNELS] find-duplicates: found %d duplicate groups (%d channels)",
                    len(duplicates), total_dup_channels)

        return {
            "groups": duplicates,
            "total_groups": len(duplicates),
            "total_duplicate_channels": total_dup_channels,
        }

    except Exception as e:
        logger.exception("[CHANNELS] find-duplicates failed: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
    finally:
        session.close()


@router.post("/bulk-merge")
async def bulk_merge_channels(request: BulkMergeRequest, _admin=RequireAdminIfEnabled):
    """Merge multiple groups of duplicate channels.

    For each merge item, keeps the target channel and moves all streams
    from source channels into it, then deletes the sources.

    Admin only (destructive bulk operator op — deletes channels, bd-um30y).
    """
    logger.debug("[CHANNELS] POST /channels/bulk-merge - %d merge groups", len(request.merges))

    client = get_client()
    results = []
    merged_count = 0
    failed_count = 0

    for item in request.merges:
        try:
            # Pre-validate the target ID the same way source IDs are validated
            # below: if the target no longer exists upstream (e.g., a stale
            # reference after a previous merge), surface 422 with the same
            # refresh hint instead of falling through to the per-item catch-all
            # (which returns 200 + a failed count). Consistent with the
            # source-ID path added in bd-ozhkf (bd-4xxax).
            try:
                target = await client.get_channel(item.target_channel_id)
            except httpx.HTTPStatusError as fetch_err:
                if fetch_err.response.status_code == 404:
                    logger.warning(
                        "[CHANNELS] bulk-merge: rejected — stale target ID %s no longer exists",
                        item.target_channel_id,
                    )
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"Target channel {item.target_channel_id} no longer exists — "
                            "refresh the channels list and try again"
                        ),
                    )
                raise
            target_name = target.get("name", f"Channel {item.target_channel_id}")

            # Collect all streams from target + sources (deduplicated, target first).
            # Pre-validate source IDs before mutating anything: if any source no longer
            # exists upstream (e.g., stale ID from a previous merge), surface 422 with a
            # refresh hint instead of silently calling DELETE on a ghost row and producing
            # [DISPATCHARR] API request failed: DELETE 404 noise.  Mirror of the same
            # pattern in merge_channels (bd-ct9wl); applied here for the bulk path
            # (bd-ozhkf).
            all_streams: list[int] = []
            seen: set[int] = set()
            for sid in target.get("streams", []):
                if sid not in seen:
                    all_streams.append(sid)
                    seen.add(sid)

            source_names = []
            missing_ids: list[int] = []
            for src_id in item.source_channel_ids:
                try:
                    src = await client.get_channel(src_id)
                    source_names.append(src.get("name", f"Channel {src_id}"))
                    for sid in src.get("streams", []):
                        if sid not in seen:
                            all_streams.append(sid)
                            seen.add(sid)
                except httpx.HTTPStatusError as fetch_err:
                    if fetch_err.response.status_code == 404:
                        missing_ids.append(src_id)
                        # Keep source_names complete — one entry per requested
                        # source ID, in order. Harmless today because the 422
                        # branch below raises before the journal entry is
                        # written, but prevents a silently misaligned audit
                        # record if this ever becomes a partial-success batch
                        # (bd-4xxax).
                        source_names.append(f"Channel {src_id}")
                    else:
                        raise

            if missing_ids:
                logger.warning(
                    "[CHANNELS] bulk-merge: rejected — stale source IDs %s no longer exist",
                    missing_ids,
                )
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Source channels {missing_ids} no longer exist — "
                        "refresh the channels list and try again"
                    ),
                )

            # Update target with combined streams
            if all_streams:
                await client.update_channel(item.target_channel_id, {"streams": all_streams})

            # Delete source channels
            deleted = []
            for src_id in item.source_channel_ids:
                try:
                    await client.delete_channel(src_id)
                    deleted.append(src_id)
                except Exception as e:
                    logger.warning("[CHANNELS] bulk-merge: failed to delete source %s: %s", src_id, e)

            journal.log_entry(
                category="channel",
                action_type="bulk_merge",
                entity_id=item.target_channel_id,
                entity_name=target_name,
                description=f"Merged {len(item.source_channel_ids)} channels into '{target_name}'",
                before_value={"source_names": source_names},
                after_value={"stream_count": len(all_streams), "deleted_ids": deleted},
            )

            merged_count += 1
            results.append({
                "target_channel_id": item.target_channel_id,
                "target_name": target_name,
                "sources_deleted": len(deleted),
                "total_streams": len(all_streams),
                "success": True,
            })

        except HTTPException:
            raise
        except Exception as e:
            failed_count += 1
            # If the per-item failure is an upstream client error (e.g. a bad
            # target/source id), surface the actionable upstream detail so the
            # caller can tell "does not exist" from a real server fault, instead
            # of the bare exception type (bd-lq38l.4). For genuine server faults
            # we keep CodeQL py/stack-trace-exposure (#1413) hygiene: log the
            # full trace but only return the exception type name to the client.
            logger.exception(
                "[CHANNELS] bulk-merge: group failed (target=%s)",
                item.target_channel_id,
            )
            mapped = upstream_http_exception(e)
            error_detail = mapped.detail if mapped is not None else type(e).__name__
            results.append({
                "target_channel_id": item.target_channel_id,
                "success": False,
                "error": error_detail,
            })

    logger.info("[CHANNELS] bulk-merge complete: %d merged, %d failed", merged_count, failed_count)
    return {
        "merged": merged_count,
        "failed": failed_count,
        "results": results,
    }
