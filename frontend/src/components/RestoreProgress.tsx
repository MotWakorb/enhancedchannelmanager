/**
 * Reusable progress surface for a DBAS Phase-2 restore or dry-run.
 *
 * Renders a stage indicator ("Stage N of M" + current category + per-category
 * item progress) plus an overall progress bar, driven by the normalized view
 * from {@link useRestoreProgress}. The SAME component serves both modes — a
 * `mode` prop ("restore" | "dry-run") only switches the labels; the stage and
 * progress machinery is identical (dry-run is a counts-only pre-pass, bead .16).
 *
 * Error status renders an explicit error state (not a frozen spinner) so an
 * operator never stares at a spinner that will never resolve. Completion renders
 * a done state.
 *
 * Styling reuses the shared `modal-progress*` classes from `ModalBase.css`; the
 * stage-specific layout lives in `RestoreProgress.css`.
 */
import type { RestoreProgressView } from '../hooks/useRestoreProgress';
import type { RestoreProgressMode } from '../utils/restoreStages';
import './RestoreProgress.css';

interface RestoreProgressProps {
  /** Normalized view from `useRestoreProgress`. */
  view: RestoreProgressView;
  /** Label mode — switches restore vs dry-run wording. The machinery is shared. */
  mode?: RestoreProgressMode;
}

const MODE_LABELS: Record<RestoreProgressMode, { running: string; complete: string; error: string }> = {
  restore: {
    running: 'Restoring',
    complete: 'Restore complete',
    error: 'Restore failed',
  },
  'dry-run': {
    running: 'Computing dry-run',
    complete: 'Dry-run complete',
    error: 'Dry-run failed',
  },
};

export function RestoreProgress({ view, mode = 'restore' }: RestoreProgressProps) {
  const labels = MODE_LABELS[mode];
  const {
    stageNumber,
    totalStages,
    stageLabel,
    itemCurrent,
    itemTotal,
    percentage,
    isError,
    isComplete,
    error,
  } = view;

  const pct = Math.max(0, Math.min(100, percentage));

  return (
    <div className="restore-progress" data-testid="restore-progress" data-mode={mode}>
      <div className="restore-progress-header">
        {isError ? (
          <span className="material-icons restore-progress-icon is-error">error</span>
        ) : isComplete ? (
          <span className="material-icons restore-progress-icon is-complete">check_circle</span>
        ) : (
          <span className="material-icons spinning restore-progress-icon">sync</span>
        )}
        <span className="restore-progress-title">
          {isError ? labels.error : isComplete ? labels.complete : labels.running}
        </span>
      </div>

      {!isComplete && !isError && (
        <>
          <div className="restore-progress-stage">
            <span className="restore-progress-stage-counter">
              Stage {stageNumber} of {totalStages}
            </span>
            <span className="restore-progress-stage-name">{stageLabel}</span>
          </div>

          {itemTotal > 0 && (
            <div className="restore-progress-items" data-testid="restore-progress-items">
              {itemCurrent} of {itemTotal} items
            </div>
          )}

          <div className="modal-progress">
            <div className="modal-progress-bar">
              <div
                className="modal-progress-fill"
                style={{ width: `${pct}%` }}
                role="progressbar"
                aria-valuenow={Math.round(pct)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label={`${labels.running}: stage ${stageNumber} of ${totalStages}`}
              />
            </div>
            <div className="modal-progress-detail">{Math.round(pct)}%</div>
          </div>
        </>
      )}

      {isError && (
        <div className="restore-progress-error" data-testid="restore-progress-error">
          {error || 'The operation ended in an error state. No further progress will be reported.'}
        </div>
      )}
    </div>
  );
}
