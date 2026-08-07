/**
 * The closed set of task-execution statuses, and what they mean (bead fexq1).
 *
 * A pure module rather than a member of `services/api` deliberately: the two
 * DBAS restore modals that need this predicate mock the whole API client in
 * their tests, and a pure string predicate should not have to be stubbed.
 *
 * `completed_with_warnings` is the terminal status for a run that reached the
 * end and left real, kept state without being clean — some items failed, or the
 * task declared itself degraded. It is NOT a failure. A DBAS restore that
 * applied every row and left a channel with no playable stream lands here, and
 * before this status existed it was stored as `failed` while its own
 * notification said "Task Completed with Warnings".
 */
import type { TaskExecution } from '../services/api';

export type TaskExecutionStatus = TaskExecution['status'];

/**
 * True once a task-execution row has stopped moving.
 *
 * Both restore modals poll this row to know when their report is readable, and
 * both used to test `status === 'completed' || status === 'failed'`. That
 * silently stopped matching when `completed_with_warnings` was introduced: a
 * degraded restore would poll until its retries ran out and then report
 * "Restore failed" for a restore that completed and rolled nothing back.
 */
export function isTerminalExecutionStatus(status: string | undefined | null): boolean {
  return (
    status === 'completed'
    || status === 'completed_with_warnings'
    || status === 'failed'
    || status === 'cancelled'
  );
}
