import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import { useModalFocusLifecycle } from '../hooks/useModalFocusLifecycle';
import type {
  NumberingConflict,
  ReconcileChoice,
  ReconcileDecision,
} from '../utils/channelNumberConcurrency';
import './EditMode.css';

/**
 * The per-channel keep-mine / take-theirs choice for every channel number
 * somebody else changed while this session was staging (bead
 * `enhancedchannelmanager-ic884.4`).
 *
 * IT DOES NOT BLOCK THE WHOLE APPLY, which is the PO's decision on this bead:
 * every staged change that does NOT overlap a server-side change is applied as
 * soon as the operator has answered for the ones that do. The dialog therefore
 * lists only the overlaps, and says so, rather than presenting the session as
 * broken.
 *
 * IT ALSO DOES NOT DECIDE FOR THE OPERATOR. There is no default selection and
 * Apply stays disabled until every row has an answer: a pre-ticked "keep mine"
 * is a silent overwrite wearing a checkbox, which is the exact outcome this
 * whole bead exists to prevent.
 *
 * IT IS A REAL MODAL TO ASSISTIVE TECHNOLOGY, AND TO THE INVENTORY THAT PROVES
 * IT. `role="dialog"` + `aria-modal` + a name from its own heading, its own
 * backdrop marked `data-modal-overlay`, and the shared focus lifecycle for
 * initial focus and Tab containment. Those markers are also what
 * `frontend/src/devHarness/discoverDialogs.ts` scans for, so this dialog is
 * catalogued as `numbering-conflict` and measured every run by
 * `e2e/visual/modal-typography-inventory.spec.ts` rather than being invisible
 * to it. It keeps its OWN Escape handler (Escape means Keep Editing, and is
 * suppressed mid-commit) rather than composing `ModalOverlay`, because the
 * suppression is the point: Escape must not abandon an Apply in flight.
 *
 * INITIAL FOCUS IS "KEEP EDITING", NOT "APPLY". Apply is disabled until every
 * row is answered, so the focus lifecycle skips it and lands on the control
 * that costs nothing to press. Once the operator has answered, Apply is a
 * deliberate act, not the thing their first keystroke happens to hit.
 */
export interface NumberingConflictDialogProps {
  isOpen: boolean;
  conflicts: NumberingConflict[];
  /** Apply the answers and continue. */
  onReconcile: (decisions: ReconcileDecision[]) => void;
  /** Abandon the Apply and go back to editing, keeping every staged change. */
  onKeepEditing: () => void;
  isCommitting?: boolean;
}

/**
 * A channel number as an operator writes it, or the words for having none.
 *
 * `String` is already the right renderer: a JavaScript number has no memory of
 * a trailing zero, so `10.0` is `10` and `10.1` is `10.1`. The named function
 * exists for the `null` arm — "no number" rather than an empty gap in a
 * sentence the operator has to make sense of.
 */
function describeNumber(value: number | null): string {
  return value === null ? 'no number' : String(value);
}

export function NumberingConflictDialog({
  isOpen,
  conflicts,
  onReconcile,
  onKeepEditing,
  isCommitting = false,
}: NumberingConflictDialogProps) {
  const [choices, setChoices] = useState<Record<number, ReconcileChoice>>({});
  const titleId = `${useId()}-numbering-conflict-title`;
  const dialogRef = useRef<HTMLDivElement>(null);
  const applyButtonRef = useRef<HTMLButtonElement>(null);

  // A fresh set of conflicts is a fresh set of questions. Carrying an answer
  // over from a previous read would be answering about a value that has since
  // moved, which is the thing the acknowledgement binding exists to stop.
  const signature = useMemo(
    () => conflicts
      .map((conflict) => `${conflict.channelId}:${conflict.baselineNumber}:${conflict.serverNumber}`)
      .join('|'),
    [conflicts],
  );
  useEffect(() => {
    setChoices({});
  }, [signature]);

  useEffect(() => {
    if (!isOpen || isCommitting) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onKeepEditing();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, isCommitting, onKeepEditing]);

  useModalFocusLifecycle({
    containerRef: dialogRef,
    initialFocusRef: applyButtonRef,
    active: isOpen && conflicts.length > 0,
  });

  const answered = conflicts.every((conflict) => choices[conflict.channelId] !== undefined);

  const handleReconcile = useCallback(() => {
    onReconcile(conflicts.map((conflict) => ({
      channelId: conflict.channelId,
      choice: choices[conflict.channelId] ?? 'take-theirs',
      baselineNumber: conflict.baselineNumber,
      serverNumber: conflict.serverNumber,
    })));
  }, [conflicts, choices, onReconcile]);

  if (!isOpen || conflicts.length === 0) return null;

  return (
    <div className="edit-mode-dialog-overlay" data-modal-overlay>
      <div
        className="edit-mode-dialog numbering-conflict-dialog"
        ref={dialogRef}
        data-testid="numbering-conflict-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="edit-mode-dialog-header">
          <h2 id={titleId}>Channel Numbers Changed While You Were Editing</h2>
        </div>

        <div className="edit-mode-dialog-content">
          <p className="edit-mode-dialog-commit-failure-intro" role="alert">
            <span className="material-icons" aria-hidden="true">sync_problem</span>
            Nothing has been applied yet. {conflicts.length} channel
            {conflicts.length !== 1 ? 's' : ''} moved on the server since you started editing.
            Choose what to do with each one; everything else you staged will be applied
            unchanged.
          </p>

          <ul className="numbering-conflict-list">
            {conflicts.map((conflict) => {
              const choice = choices[conflict.channelId];
              const groupName = `numbering-conflict-${conflict.channelId}`;
              return (
                <li key={conflict.channelId} className="numbering-conflict-row">
                  <div className="numbering-conflict-summary">
                    <strong>{conflict.name}</strong>
                    {conflict.kind === 'channel-deleted' ? (
                      <span className="numbering-conflict-detail">
                        {' '}was deleted on the server. It was on{' '}
                        {describeNumber(conflict.baselineNumber)} when you started.
                      </span>
                    ) : (
                      <span className="numbering-conflict-detail">
                        {' '}was on {describeNumber(conflict.baselineNumber)} when you started, is
                        now on {describeNumber(conflict.serverNumber)}, and your change would put
                        it on {describeNumber(conflict.proposedNumber)}.
                      </span>
                    )}
                    {conflict.operationDescriptions.length > 0 && (
                      <span className="numbering-conflict-origin">
                        {' '}From: {conflict.operationDescriptions.join('; ')}.
                      </span>
                    )}
                    {conflict.fromRangeAssignment && (
                      <span className="numbering-conflict-origin">
                        {' '}Keeping the server’s number here drops the whole renumbering, because
                        its numbers run in sequence.
                      </span>
                    )}
                  </div>
                  <fieldset className="numbering-conflict-choices">
                    <legend className="visually-hidden">What to do with {conflict.name}</legend>
                    <label>
                      <input
                        type="radio"
                        name={groupName}
                        value="keep-mine"
                        checked={choice === 'keep-mine'}
                        disabled={isCommitting}
                        onChange={() => setChoices((prev) => ({
                          ...prev,
                          [conflict.channelId]: 'keep-mine',
                        }))}
                      />
                      Use my number ({describeNumber(conflict.proposedNumber)})
                    </label>
                    <label>
                      <input
                        type="radio"
                        name={groupName}
                        value="take-theirs"
                        checked={choice === 'take-theirs'}
                        disabled={isCommitting}
                        onChange={() => setChoices((prev) => ({
                          ...prev,
                          [conflict.channelId]: 'take-theirs',
                        }))}
                      />
                      Keep the server’s ({describeNumber(conflict.serverNumber)})
                    </label>
                  </fieldset>
                </li>
              );
            })}
          </ul>

          <p className="edit-mode-dialog-question">
            {answered
              ? 'Your other staged changes are unaffected and will be applied.'
              : 'Choose an option for every channel above to continue.'}
          </p>
        </div>

        <div className="edit-mode-dialog-actions">
          <button
            className="edit-mode-dialog-btn secondary"
            onClick={onKeepEditing}
            disabled={isCommitting}
          >
            Keep Editing
          </button>
          <button
            className="edit-mode-dialog-btn primary"
            ref={applyButtonRef}
            onClick={handleReconcile}
            disabled={!answered || isCommitting}
          >
            Apply With These Choices
          </button>
        </div>
      </div>
    </div>
  );
}
