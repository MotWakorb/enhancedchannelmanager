/**
 * The terminal-status predicate two restore modals poll on (bead fexq1).
 *
 * The failure this pins is a silent one: when a new terminal status appeared,
 * an `=== 'completed' || === 'failed'` check stopped matching it and the modal
 * polled until its retries ran out, then told the operator the restore had
 * failed — for a restore that completed and rolled nothing back.
 */
import { describe, it, expect } from 'vitest';
import { isTerminalExecutionStatus } from './taskExecutionStatus';

describe('isTerminalExecutionStatus', () => {
  it('treats a warning-level run as terminal', () => {
    expect(isTerminalExecutionStatus('completed_with_warnings')).toBe(true);
  });

  it.each(['completed', 'failed', 'cancelled'])('treats %s as terminal', (status) => {
    expect(isTerminalExecutionStatus(status)).toBe(true);
  });

  it('does not treat a running row as terminal', () => {
    expect(isTerminalExecutionStatus('running')).toBe(false);
  });

  it('does not treat a missing status as terminal', () => {
    // The row can be absent entirely while the engine is still writing it;
    // treating that as terminal would read a report that is not there yet.
    expect(isTerminalExecutionStatus(undefined)).toBe(false);
    expect(isTerminalExecutionStatus(null)).toBe(false);
  });
});
