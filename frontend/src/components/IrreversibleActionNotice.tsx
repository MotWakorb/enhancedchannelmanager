import { memo, useId } from 'react';
import './IrreversibleActionNotice.css';

export interface IrreversibleActionNoticeProps {
  /** What the action does, in the operator's terms, e.g. "This merge". */
  what: string;
  /** True once the operator has ticked the acknowledgement. */
  acknowledged: boolean;
  onAcknowledgedChange: (acknowledged: boolean) => void;
  /** Extra sentence naming the specific loss, e.g. that sources are deleted. */
  consequence?: string;
}

/**
 * Point-of-action notice for an operation Edit Mode cannot stage
 * (bead enhancedchannelmanager-kz089).
 *
 * Edit Mode presents itself as a staging area, and for most of what it offers
 * that is true. Merge (from the selection toolbar and from Find Duplicates) and
 * Import CSV are the exceptions the PO accepted as genuine: a merge reconciles
 * records across providers and would need server-side support to be
 * representable as a reversible diff, and that work is explicitly not in scope.
 *
 * The defect was never that they are immediate. It was that nothing on screen
 * said so, while a Bulk Delete dialog two clicks away promised changes could be
 * undone in edit mode. So this component does the one thing the mode owed the
 * operator: it says, at the point of action and before the button works, that
 * this one applies now and Discard will not reach it.
 *
 * The acknowledgement is a checkbox rather than prose alone because prose beside
 * an already-live button is what the operator has been reading past all along.
 */
export const IrreversibleActionNotice = memo(function IrreversibleActionNotice({
  what,
  acknowledged,
  onAcknowledgedChange,
  consequence,
}: IrreversibleActionNoticeProps) {
  const checkboxId = useId();

  return (
    <div className="irreversible-action-notice" role="alert" data-testid="irreversible-action-notice">
      <span className="material-icons irreversible-action-notice-icon" aria-hidden="true">
        report_problem
      </span>
      <div className="irreversible-action-notice-body">
        <strong>{what} applies immediately and is NOT staged.</strong>
        <p>
          Unlike the rest of Edit Mode, it cannot be undone by Discard, Cancel or Undo.
          {consequence ? ` ${consequence}` : ''}
        </p>
        <label className="irreversible-action-notice-ack" htmlFor={checkboxId}>
          <input
            id={checkboxId}
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => onAcknowledgedChange(e.target.checked)}
          />
          <span>I understand this cannot be discarded</span>
        </label>
      </div>
    </div>
  );
});
