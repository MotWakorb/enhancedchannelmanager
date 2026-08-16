import { memo } from 'react';
import './ImmediateActionNote.css';

export interface ImmediateActionNoteProps {
  /** What applies immediately, in the operator's terms, e.g. "Probing". */
  what: string;
  /** Optional second sentence naming why it cannot be staged. */
  detail?: string;
  /** Compact single-line layout, for use inside a menu or a toolbar. */
  compact?: boolean;
  /** Test hook, so a surface can assert on its own note. */
  testId?: string;
}

/**
 * Point-of-action statement for an Edit Mode action that applies immediately
 * and is NOT dangerous enough to gate (bead enhancedchannelmanager-kz089, fix
 * round 2).
 *
 * Edit Mode's rule is: an action either stages, or says plainly at the point of
 * action that it applies immediately. {@link IrreversibleActionNotice} is the
 * other half of that rule, for Merge and Import CSV — it holds the button dead
 * until a checkbox is ticked, which is right for an action that destroys
 * records.
 *
 * This one is for actions where a mandatory tick would be fatiguing and buys
 * nothing. Per the PO's 2026-08-15 decision: probing is a routine diagnostic
 * operators run constantly, and there is no meaningful "staged probe" because
 * the value does not exist until the probe runs — a stale probe result applied
 * forty minutes later would be worse than none. Profile create / rename /
 * delete is admin-object management, and a staged membership operation needs a
 * real profile id to reference, so those stay immediate too. Neither destroys
 * operator work; both were simply unlabelled.
 */
export const ImmediateActionNote = memo(function ImmediateActionNote({
  what,
  detail,
  compact = false,
  testId,
}: ImmediateActionNoteProps) {
  return (
    <div
      className={`immediate-action-note${compact ? ' immediate-action-note--compact' : ''}`}
      role="note"
      data-testid={testId ?? 'immediate-action-note'}
    >
      <span className="material-icons immediate-action-note-icon" aria-hidden="true">
        info
      </span>
      <span className="immediate-action-note-body">
        <strong>{what} applies immediately</strong> and is not part of Edit Mode
        staging — Discard will not undo it.
        {detail ? ` ${detail}` : ''}
      </span>
    </div>
  );
});
