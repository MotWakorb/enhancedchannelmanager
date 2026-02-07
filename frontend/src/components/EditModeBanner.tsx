import { useState, useEffect } from 'react';
import './EditMode.css';

interface EditModeBannerProps {
  stagedCount: number;
  duration: number | null;
  onCancel: () => void;
}

function formatDuration(ms: number): string {
  const seconds = Math.floor(ms / 1000);
  const minutes = Math.floor(seconds / 60);
  const hours = Math.floor(minutes / 60);

  if (hours > 0) {
    return `${hours}h ${minutes % 60}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds % 60}s`;
  }
  return `${seconds}s`;
}

export function EditModeBanner({
  stagedCount,
  duration,
  onCancel,
}: EditModeBannerProps) {
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const showWarning = duration !== null && duration > 10 * 60 * 1000; // 10 minutes

  useEffect(() => {
    if (!showCancelConfirm) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowCancelConfirm(false);
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [showCancelConfirm]);

  const handleCancelClick = () => {
    if (stagedCount > 0) {
      setShowCancelConfirm(true);
    } else {
      onCancel();
    }
  };

  const handleConfirmCancel = () => {
    setShowCancelConfirm(false);
    onCancel();
  };

  return (
    <div className={`edit-mode-banner ${showWarning ? 'warning' : ''}`}>
      <div className="edit-mode-banner-content">
        <span className="material-icons edit-mode-banner-icon">edit</span>
        <span className="edit-mode-banner-text">
          Edit Mode Active
          {stagedCount > 0 && (
            <span className="edit-mode-banner-count">
              {' '}- {stagedCount} pending change{stagedCount !== 1 ? 's' : ''}
            </span>
          )}
          {duration !== null && (
            <span className="edit-mode-banner-duration">
              {' '}({formatDuration(duration)})
            </span>
          )}
        </span>
        {showWarning && (
          <span className="edit-mode-banner-warning">
            Long session - consider applying changes
          </span>
        )}
      </div>
      <div className="edit-mode-banner-actions">
        <button
          className="edit-mode-banner-btn discard"
          onClick={handleCancelClick}
          title="Cancel Edit Mode"
        >
          <span className="material-icons" style={{ fontSize: '16px', marginRight: '4px' }}>close</span>
          Cancel
        </button>
      </div>

      {showCancelConfirm && (
        <div className="edit-mode-dialog-overlay">
          <div className="edit-mode-dialog" onClick={(e) => e.stopPropagation()}>
            <div className="edit-mode-dialog-header">
              <h2>Discard Changes?</h2>
            </div>
            <div className="edit-mode-dialog-content">
              <p className="edit-mode-dialog-summary-intro">
                You have <strong>{stagedCount}</strong> pending change{stagedCount !== 1 ? 's' : ''} that will be lost.
              </p>
              <p className="edit-mode-dialog-question">
                Are you sure you want to cancel Edit Mode and discard all changes?
              </p>
            </div>
            <div className="edit-mode-dialog-actions">
              <button
                className="edit-mode-dialog-btn secondary"
                onClick={() => setShowCancelConfirm(false)}
              >
                Keep Editing
              </button>
              <button
                className="edit-mode-dialog-btn danger"
                onClick={handleConfirmCancel}
              >
                Discard Changes
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
