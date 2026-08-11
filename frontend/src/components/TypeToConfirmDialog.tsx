import { useId, useState } from 'react';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './TypeToConfirmDialog.css';

interface TypeToConfirmDialogProps {
  /** Modal title, e.g. "Restore Backup". */
  title: string;
  /** Body copy explaining the danger of the action. */
  message: React.ReactNode;
  /** The exact text the operator must type to enable the confirm button. */
  confirmText: string;
  /** Label for the confirm button (defaults to "Confirm"). */
  confirmLabel?: string;
  /** True while the confirmed action is in flight — disables inputs/buttons. */
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * Reusable type-to-confirm dialog for danger-tier destructive actions
 * (enhancedchannelmanager-rzhid). No prior type-to-confirm pattern existed in
 * the codebase — other destructive flows use a plain `window.confirm()` or a
 * warning-banner modal. This is the first, built to be reused by any future
 * danger-tier action (restore, purge, etc.) rather than one-off duplicated.
 */
export function TypeToConfirmDialog({
  title,
  message,
  confirmText,
  confirmLabel = 'Confirm',
  busy = false,
  onCancel,
  onConfirm,
}: TypeToConfirmDialogProps) {
  const [typed, setTyped] = useState('');
  const canConfirm = typed === confirmText && !busy;
  const instanceId = useId();
  const titleId = `${instanceId}-title`;
  const inputId = `${instanceId}-confirmation`;

  return (
    <ModalOverlay
      onClose={busy ? () => {} : onCancel}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div className="modal-container modal-sm type-to-confirm-dialog">
        <div className="modal-header">
          <h3 id={titleId} className="modal-title">
            {title}
          </h3>
          {!busy && (
            <button className="modal-close-btn" onClick={onCancel} aria-label="Close" title="Close">
              <span className="material-icons" aria-hidden="true">close</span>
            </button>
          )}
        </div>

        <div className="modal-body">
          <div className="type-to-confirm-message">{message}</div>
          <label className="type-to-confirm-label" htmlFor={inputId}>
            Type <strong>{confirmText}</strong> to confirm
          </label>
          <input
            id={inputId}
            type="text"
            className="type-to-confirm-input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            disabled={busy}
            autoFocus
            autoComplete="off"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && canConfirm) onConfirm();
            }}
          />
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn-danger"
            onClick={onConfirm}
            disabled={!canConfirm}
          >
            {busy ? 'Working…' : confirmLabel}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
