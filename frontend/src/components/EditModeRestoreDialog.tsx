import type { StagedOperation } from '../types';
import type { DroppedLedgerOperation } from '../utils/stagedLedgerStorage';
import './EditMode.css';

export interface EditModeRestoreDialogProps {
  isOpen: boolean;
  /** When the ledger was last written, from the persisted record. */
  savedAt: number;
  /** Operations that are still applicable, from `planLedgerRestore`. */
  restorable: StagedOperation[];
  /** Operations that are not, with the reason each one moved. */
  dropped: DroppedLedgerOperation[];
  onRestore: () => void;
  onDiscard: () => void;
}

/**
 * The offer an operator gets on returning to a tab that still holds staged
 * Edit Mode work from a session that died (epic enhancedchannelmanager-r93hq).
 *
 * WHY AN OFFER RATHER THAN AN AUTOMATIC RESTORE. Staged operations name
 * channel ids, group ids and stream ids, and any of them can have moved while
 * the session was dead — a channel deleted, a group renamed away, numbers
 * reassigned by another operator or by a pipeline run. Some of the ledger may
 * therefore not be applicable at all. Restoring silently would drop the
 * operator back into Edit Mode holding a ledger quietly smaller than the one
 * they left, with no way to see which changes went or why, and the next thing
 * they do is press Apply. A partial restore is fine; a partial restore nobody
 * was told about is not.
 *
 * SO THE ACCOUNT IS NOT COLLAPSIBLE AND NOT TRUNCATED. Every dropped operation
 * gets its own line naming what it was and what moved.
 *
 * NEITHER BUTTON IS A DEFAULT ESCAPE. There is no Escape handler and no
 * click-away: both would destroy staged work by accident, which is the exact
 * failure the epic's exit guard exists to stop.
 */
export function EditModeRestoreDialog({
  isOpen,
  savedAt,
  restorable,
  dropped,
  onRestore,
  onDiscard,
}: EditModeRestoreDialogProps) {
  if (!isOpen) return null;

  const staged = new Date(savedAt);
  const restorableCount = restorable.length;
  const droppedCount = dropped.length;

  return (
    <div className="edit-mode-dialog-overlay">
      <div
        className="edit-mode-dialog"
        data-testid="edit-mode-restore-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="edit-mode-restore-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="edit-mode-dialog-header">
          <h2 id="edit-mode-restore-title">Unsaved Changes From Your Previous Session</h2>
        </div>

        <div className="edit-mode-dialog-content">
          <p>
            Your session ended before these Edit Mode changes were applied. They were
            last staged{' '}
            <time data-testid="edit-mode-restore-when" dateTime={staged.toISOString()}>
              {staged.toLocaleString()}
            </time>
            .
          </p>

          {restorableCount > 0 ? (
            <p className="edit-mode-restore-headline">
              <strong>
                {restorableCount} change{restorableCount !== 1 ? 's' : ''}
              </strong>{' '}
              can be restored and staged again for you to review before applying.
            </p>
          ) : (
            <p className="edit-mode-restore-headline" role="alert">
              <strong>None of your staged changes can be restored.</strong> Everything
              they referred to has changed since you staged them.
            </p>
          )}

          {droppedCount > 0 && (
            <div className="edit-mode-restore-dropped">
              <p role="alert">
                <span className="material-icons" aria-hidden="true">error_outline</span>
                {droppedCount} change{droppedCount !== 1 ? 's' : ''} can no longer be
                applied and will not be restored:
              </p>
              <ul data-testid="edit-mode-restore-dropped">
                {dropped.map((entry) => (
                  <li key={entry.id}>
                    <strong>{entry.description}</strong> — {entry.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="edit-mode-dialog-question">
            Nothing has been written to Dispatcharr. Restoring re-stages the changes;
            discarding throws them away for good.
          </p>
        </div>

        <div className="edit-mode-dialog-actions">
          <button className="edit-mode-dialog-btn" onClick={onDiscard}>
            Discard {restorableCount > 0 ? 'Them' : 'and Continue'}
          </button>
          {restorableCount > 0 && (
            <button className="edit-mode-dialog-btn primary" onClick={onRestore} autoFocus>
              Restore {restorableCount} Change{restorableCount !== 1 ? 's' : ''}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
