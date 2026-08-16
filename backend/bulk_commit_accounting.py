"""The bulk-commit envelope's accounting invariant, written down and executable.

Bead ``enhancedchannelmanager-e9e5o``. Three review rounds on this bead found
the same class of defect: a state that should be explicit was implicitly
defaulted, and an accounting rule that everybody assumed was never enforced
anywhere. Each round fixed the reproduction it was given. This module exists so
the *property* is enforced instead, in one place, over every operation type.

The property, stated as an invariant rather than as a reproduction:

    For every operation in a bulk commit, the envelope's accounting is
    internally consistent and matches reality.

    1. ``operationsApplied + operationsFailed`` equals the number of operations
       the executor ATTEMPTED, and the executor attempts every submitted
       operation unless it aborted early (``continueOnError=false``) or the run
       never reached Phase 2 at all (a setup failure — see
       :meth:`OperationLedger.record_setup_failure`).
    2. Every operation resolves to exactly one outcome. Not zero — an operation
       type nothing handles used to be silently counted as neither. Not two —
       a branch that incremented ``operationsApplied`` mid-way and then raised
       used to be counted as both.
    3. An operation whose upstream write LANDED is never reported as a total
       failure. Reporting one as a failure is what makes an integrator retry
       and create the entity a second time.
    4. ``success`` is true only when nothing failed and nothing applied
       incompletely; ``partial`` is true exactly when something applied and
       something else did not go cleanly.
    5. Every ``normalizationFailures`` entry names an operation counted as
       applied — the list is rendered by the MCP tool as channels "which were
       created", so an entry for an operation that failed is a claim about a
       channel that does not exist.
    6. Every entry in ``errors`` is accounted for. An entry that names a
       submitted operation is counted in ``operationsFailed`` (or carries
       ``applied: True``); an entry that names something OUTSIDE the operation
       list — group creation in Phase 1, the journal flush in Phase 3 — is
       counted as a setup/bookkeeping failure. Neither may be silently
       uncounted, which is how a run that created a group and then bailed
       reported ``success: false`` with ``operationsFailed: 0`` and no entry
       anywhere saying what had failed.

Enforcement, not convention: :class:`OperationLedger` is the ONLY thing that
writes the counters, and :func:`finalize_bulk_commit_result` derives ``success``
and ``partial`` from it. A branch in ``routers/channels.py`` cannot get the
counters wrong by forgetting to increment, because it has nothing to increment.
:func:`bulk_commit_accounting_violations` is the belt-and-braces audit of the
finished envelope, and it raises rather than logging, so a violation cannot ship
as a quiet log line.

Enforced by ``backend/tests/routers/test_e9e5o_bulk_commit_accounting.py``,
which generates the scenario matrix (normalization succeeded/failed x create
succeeded/threw/returned malformed x first/middle/last in batch) and asserts
this invariant over every cell.

KNOWN LIMIT, deliberately not papered over: an operation with more than one
upstream side effect can land the first and fail the second — the clearest case
is ``deleteChannelGroup``, which reparents the group's channels and then deletes
the group. The envelope has one outcome per operation and cannot express "the
channels moved but the group is still there", so such an operation is reported
as a failure and its partial side effect is visible only in the journal. Marking
it applied would be the worse lie, because the group still exists. Recorded as a
follow-up rather than fixed here.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "BulkCommitAccountingError",
    "OperationLedger",
    "bulk_commit_accounting_violations",
    "finalize_bulk_commit_result",
]


class BulkCommitAccountingError(RuntimeError):
    """The bulk-commit envelope's own accounting contradicts itself.

    Raised rather than logged. A contradictory envelope is what sends an
    integrator into a retry that duplicates data, so it must not be able to
    leave the executor looking like a normal result.
    """


class OperationLedger:
    """Records exactly one outcome per bulk-commit operation.

    The counters used to be incremented from inside each of the thirteen
    operation branches, at whatever point in the branch happened to be
    convenient. That arrangement had three failure modes and all three were
    live: a branch could increment and then raise (counted twice), an operation
    type no branch claimed was counted zero times, and an operation whose
    upstream write had already landed was counted as a failure.

    The ledger removes the ability to make any of them. The executor opens an
    operation with :meth:`begin`, tells the ledger the moment the upstream write
    lands with :meth:`record_persisted`, and closes it with exactly one of
    :meth:`record_applied` / :meth:`record_failed`. Opening twice without
    closing, or closing twice, raises.
    """

    __slots__ = (
        "total_operations",
        "applied",
        "failed",
        "incomplete",
        "setup_failures",
        "aborted",
        "applied_create_temp_ids",
        "_open",
        "_persisted",
        "_pending_create_temp_id",
    )

    def __init__(self, total_operations: int) -> None:
        self.total_operations = total_operations
        self.applied = 0
        self.failed = 0
        #: Failures that are NOT one of the submitted operations: Phase 1 group
        #: creation, which runs before any operation, and the Phase 3 journal
        #: flush, which runs after all of them. Counting these in ``failed``
        #: would break rule 1 (``applied + failed`` counts operations); leaving
        #: them uncounted is what let a run report ``success: false`` with
        #: ``operationsFailed: 0``.
        self.setup_failures = 0
        #: Operations that APPLIED upstream but whose bookkeeping did not
        #: complete — e.g. a create Dispatcharr accepted but answered without a
        #: usable id, so the temp id cannot be mapped. Counted in ``applied``
        #: (the entity exists), but they keep ``success`` false.
        self.incomplete = 0
        self.aborted = False
        #: ``tempId`` of every createChannel operation counted as applied. The
        #: audit uses it to check that ``normalizationFailures`` only ever names
        #: a channel that exists.
        self.applied_create_temp_ids: set[int] = set()
        self._open = False
        self._persisted = False
        self._pending_create_temp_id: Optional[int] = None

    # -- reading -----------------------------------------------------------

    @property
    def attempted(self) -> int:
        """Operations the executor resolved to an outcome."""
        return self.applied + self.failed

    @property
    def skipped(self) -> int:
        """Operations never reached, because the executor aborted early."""
        return self.total_operations - self.attempted

    @property
    def persisted(self) -> bool:
        """Whether the OPEN operation's upstream write has already landed."""
        return self._persisted

    # -- writing -----------------------------------------------------------

    def begin(self) -> None:
        """Open an operation. Raises if the previous one was never closed."""
        if self._open:
            raise BulkCommitAccountingError(
                "a bulk-commit operation was opened while the previous one was "
                "still unresolved"
            )
        self._open = True
        self._persisted = False
        self._pending_create_temp_id = None

    def record_persisted(self, *, create_temp_id: Optional[int] = None) -> None:
        """Note that this operation's upstream write has LANDED.

        Called immediately after the upstream call returns, before any of ECM's
        own bookkeeping. Everything after this point can fail without making the
        operation a total failure — the entity exists either way, and telling
        the caller otherwise is what produces a duplicate on retry.
        """
        self._persisted = True
        if create_temp_id is not None:
            self._pending_create_temp_id = create_temp_id

    def record_applied(self, *, incomplete: bool = False) -> None:
        """Close the open operation as applied."""
        self._close()
        self.applied += 1
        if incomplete:
            self.incomplete += 1
        if self._pending_create_temp_id is not None:
            self.applied_create_temp_ids.add(self._pending_create_temp_id)
            self._pending_create_temp_id = None

    def record_failed(self) -> None:
        """Close the open operation as failed."""
        self._close()
        self.failed += 1
        self._pending_create_temp_id = None

    def abort_remaining(self) -> None:
        """Note that the executor stopped before reaching every operation."""
        self.aborted = True

    def record_setup_failure(self, *, aborted_run: bool = True) -> None:
        """Record a failure that is not one of the submitted operations.

        Two callers, at the two ends of the run:

        * Phase 1 group creation, which happens before any operation is
          attempted and aborts the run when it fails. ``aborted_run`` is true:
          no operation was reached, so ``applied + failed`` is legitimately
          less than the number submitted.
        * The Phase 3 journal flush, which happens after every operation has
          already resolved. ``aborted_run`` is false there — the operations
          were all attempted, and their counts stay exactly as they were.

        Either way ``success`` becomes false and the audit expects one more
        ``errors`` entry, so the envelope names what went wrong instead of
        contradicting itself.
        """
        self.setup_failures += 1
        if aborted_run:
            self.aborted = True

    def _close(self) -> None:
        if not self._open:
            raise BulkCommitAccountingError(
                "a bulk-commit operation outcome was recorded twice, or "
                "recorded without the operation being opened"
            )
        self._open = False


def bulk_commit_accounting_violations(
    result: Mapping[str, Any],
    *,
    total_operations: int,
    aborted: bool,
    applied_create_temp_ids: frozenset[int] | set[int],
    setup_failures: int = 0,
) -> list[str]:
    """Return every way ``result`` contradicts itself, as readable sentences.

    Empty list means the envelope is internally consistent. This is the audit,
    not the mechanism: the ledger is what makes the counters right, and this
    exists so that a future edit which goes around the ledger cannot ship
    quietly.
    """
    violations: list[str] = []

    applied = result.get("operationsApplied")
    failed = result.get("operationsFailed")
    errors = result.get("errors") or []
    normalization_failures = result.get("normalizationFailures") or []

    if not isinstance(applied, int) or isinstance(applied, bool) or applied < 0:
        violations.append(f"operationsApplied is not a non-negative int: {applied!r}")
        return violations
    if not isinstance(failed, int) or isinstance(failed, bool) or failed < 0:
        violations.append(f"operationsFailed is not a non-negative int: {failed!r}")
        return violations

    # An error entry carries ``applied: True`` when the operation it names DID
    # land upstream and only ECM's bookkeeping after it failed. Every other
    # entry names an operation counted in ``operationsFailed``.
    applied_errors = [e for e in errors if e.get("applied") is True]
    unapplied_errors = [e for e in errors if e.get("applied") is not True]

    attempted = applied + failed
    if aborted:
        if attempted > total_operations:
            violations.append(
                f"attempted {attempted} operations but only {total_operations} "
                "were submitted"
            )
    elif attempted != total_operations:
        violations.append(
            f"operationsApplied ({applied}) + operationsFailed ({failed}) = "
            f"{attempted}, but {total_operations} operations were submitted and "
            "the run did not abort early"
        )

    # Setup failures (Phase 1 group creation, the Phase 3 journal flush) are
    # not operations, so they are not in ``operationsFailed`` — but each still
    # contributes an ``errors`` entry, and every entry has to be accounted for
    # by exactly one counter.
    if failed + setup_failures != len(unapplied_errors):
        violations.append(
            f"operationsFailed is {failed} and {setup_failures} setup "
            f"failure(s) were recorded, but {len(unapplied_errors)} error "
            "entries describe something that did not apply"
        )

    expected_success = failed == 0 and not applied_errors and setup_failures == 0
    if result.get("success") is not expected_success:
        violations.append(
            f"success is {result.get('success')!r} but "
            f"{failed} operation(s) failed, {len(applied_errors)} applied "
            f"incompletely and {setup_failures} setup step(s) failed"
        )

    expected_partial = applied > 0 and (failed > 0 or bool(applied_errors))
    if result.get("partial") is not expected_partial:
        violations.append(
            f"partial is {result.get('partial')!r} but {applied} applied, "
            f"{failed} failed and {len(applied_errors)} applied incompletely"
        )

    for entry in normalization_failures:
        temp_id = entry.get("tempId")
        if temp_id not in applied_create_temp_ids:
            violations.append(
                f"normalizationFailures names tempId {temp_id!r}, which is not a "
                "createChannel operation counted as applied — the list is read "
                "as channels that were created"
            )

    return violations


def finalize_bulk_commit_result(result: dict, ledger: OperationLedger) -> None:
    """Write the counters and the derived flags, then audit the envelope.

    ``success`` and ``partial`` are DERIVED here rather than assigned by the
    executor, so they cannot drift from the counts they describe. Raises
    :class:`BulkCommitAccountingError` if the finished envelope still
    contradicts itself.
    """
    result["operationsApplied"] = ledger.applied
    result["operationsFailed"] = ledger.failed

    # A failed operation is a failure whatever ``continueOnError`` says (bead
    # …-ayfn9). ``incomplete`` joins it: an operation that landed upstream but
    # left ECM unable to record the result is not a clean commit either, and
    # the caller has to reconcile rather than assume.
    result["success"] = (
        ledger.failed == 0
        and ledger.incomplete == 0
        and ledger.setup_failures == 0
    )

    # ``partial`` is the flag that tells the frontend to render "X applied, Y
    # failed" and the operator to reconcile via ``tempIdMap`` instead of
    # blindly retrying and piling up duplicates (bd-5xciq). An incomplete
    # create is precisely that situation, so it counts here too.
    result["partial"] = ledger.applied > 0 and (
        ledger.failed > 0 or ledger.incomplete > 0
    )

    violations = bulk_commit_accounting_violations(
        result,
        total_operations=ledger.total_operations,
        aborted=ledger.aborted,
        applied_create_temp_ids=ledger.applied_create_temp_ids,
        setup_failures=ledger.setup_failures,
    )
    if violations:
        message = "; ".join(violations)
        logger.error(
            "[CHANNELS-BULK] Envelope accounting invariant violated: %s", message
        )
        raise BulkCommitAccountingError(message)
