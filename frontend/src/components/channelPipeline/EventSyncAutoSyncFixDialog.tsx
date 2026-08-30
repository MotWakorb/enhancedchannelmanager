/**
 * Guided-setup confirmation dialog for the one-click auto_channel_sync fix
 * (bead ti939.3.4).
 *
 * Hard constraint (security, locked at planning): the toggle is an
 * EXPLICIT, separately confirmed operator action. This dialog is the ONLY
 * surface that calls the toggle API — it states exactly what will change
 * and why, including the consequence for existing auto-created channels
 * and the recovery note (snapshot restore does NOT revert Dispatcharr
 * group settings; the journal entry is the recovery breadcrumb). Never a
 * side effect of saving a rule or running the pipeline.
 */
import { useId } from 'react';
import { ModalOverlay } from '../ModalOverlay';
import '../ModalBase.css';
import './EventSyncAutoSyncFixDialog.css';

export interface AutoSyncFixTarget {
  groupId: number;
  groupName: string;
  accountId: number;
  accountName: string;
  /** true = turn auto-sync ON (master); false = turn it OFF (secondary). */
  enable: boolean;
}

interface EventSyncAutoSyncFixDialogProps {
  target: AutoSyncFixTarget;
  /** True while the confirmed toggle is in flight — disables buttons. */
  busy?: boolean;
  /** Error from a failed toggle attempt, shown inline. */
  error?: string | null;
  onCancel: () => void;
  onConfirm: () => void;
}

export function EventSyncAutoSyncFixDialog({
  target,
  busy = false,
  error = null,
  onCancel,
  onConfirm,
}: EventSyncAutoSyncFixDialogProps) {
  const titleId = `${useId()}-title`;
  const { groupName, accountName, enable } = target;
  const direction = enable ? 'ON' : 'OFF';

  return (
    <ModalOverlay
      onClose={busy ? () => {} : onCancel}
      data-testid="autosync-fix-dialog"
    >
      <div className="modal-container modal-sm event-sync-autosync-fix-dialog" role="alertdialog" aria-modal="true" aria-labelledby={titleId}>
        <div className="modal-header">
          <h3 id={titleId} className="modal-title">
            Turn auto-sync {direction} for &lsquo;{groupName}&rsquo;?
          </h3>
          {!busy && (
            <button
              className="modal-close-btn"
              onClick={onCancel}
              aria-label="Close"
              title="Close"
            >
              <span className="material-icons" aria-hidden="true">close</span>
            </button>
          )}
        </div>

        <div className="modal-body">
          <p>
            {enable ? (
              <>
                Turn <strong>ON</strong> <code>auto_channel_sync</code> for{' '}
                <strong>&lsquo;{groupName}&rsquo;</strong> ({accountName})?
                Dispatcharr will begin creating and managing channels from
                this group — required for it to serve as the Event Sync
                master group.
              </>
            ) : (
              <>
                Turn <strong>OFF</strong> <code>auto_channel_sync</code> for{' '}
                <strong>&lsquo;{groupName}&rsquo;</strong> ({accountName})?
                Dispatcharr will stop creating duplicate channels from this
                group; existing auto-created channels from it may be removed
                by Dispatcharr.
              </>
            )}
          </p>
          <p className="form-hint">
            This change is written to Dispatcharr and journaled. Snapshot
            restore does <strong>NOT</strong> revert Dispatcharr group
            settings — the journal entry is the recovery breadcrumb if you
            need to undo it.
          </p>
          {error && (
            <p className="autosync-fix-error" role="alert">{error}</p>
          )}
        </div>

        <div className="modal-footer">
          <button
            className="modal-btn modal-btn-secondary"
            onClick={onCancel}
            disabled={busy}
          >
            Cancel
          </button>
          <button
            className="modal-btn modal-btn-danger"
            onClick={onConfirm}
            disabled={busy}
            data-testid="autosync-fix-confirm"
          >
            {busy ? 'Applying…' : `Turn auto-sync ${direction}`}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
