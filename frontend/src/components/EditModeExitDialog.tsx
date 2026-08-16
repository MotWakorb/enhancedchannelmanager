import { useState, useEffect } from 'react';
import type { EditModeExitDialogProps } from '../types';
import './EditMode.css';

export function EditModeExitDialog({
  isOpen,
  summary,
  onApply,
  onDiscard,
  onKeepEditing,
  isCommitting = false,
  commitProgress = null,
  commitFailure = null,
  onAcknowledgeFailure,
}: EditModeExitDialogProps) {
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    if (!isOpen || isCommitting || commitFailure) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onKeepEditing();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [isOpen, isCommitting, commitFailure, onKeepEditing]);

  if (!isOpen) return null;

  // A commit that did not fully apply takes over the dialog: the operator has
  // to read and acknowledge it before Edit Mode closes. Escape does not
  // dismiss it (bead enhancedchannelmanager-udq1j).
  if (commitFailure) {
    return (
      <div className="edit-mode-dialog-overlay">
        <div className="edit-mode-dialog" onClick={(e) => e.stopPropagation()}>
          <div className="edit-mode-dialog-header">
            <h2>
              {commitFailure.applied > 0 ? 'Some Changes Were Not Applied' : 'Changes Were Not Applied'}
            </h2>
          </div>

          <div className="edit-mode-dialog-content">
            <p className="edit-mode-dialog-commit-failure-intro" role="alert">
              <span className="material-icons" aria-hidden="true">error_outline</span>
              {commitFailure.applied} operation{commitFailure.applied !== 1 ? 's' : ''} applied,{' '}
              <strong>
                {commitFailure.failed} failed
              </strong>
              .
            </p>
            {commitFailure.messages.length > 0 && (
              <ul className="edit-mode-dialog-commit-failure-list">
                {commitFailure.messages.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
            )}
            <p className="edit-mode-dialog-question">
              Check the channel list before making further changes — the failed operations were
              not retried.
            </p>
          </div>

          <div className="edit-mode-dialog-actions">
            <button
              className="edit-mode-dialog-btn primary"
              onClick={onAcknowledgeFailure}
              autoFocus
            >
              Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  const hasChanges = summary.totalChanges > 0;

  // Calculate progress percentage
  const progressPercent = commitProgress
    ? Math.round((commitProgress.current / commitProgress.total) * 100)
    : 0;

  return (
    <div className="edit-mode-dialog-overlay">
      <div className="edit-mode-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="edit-mode-dialog-header">
          <h2>{isCommitting ? 'Applying Changes' : 'Exit Edit Mode'}</h2>
        </div>

        <div className="edit-mode-dialog-content">
          {isCommitting && commitProgress ? (
            <div className="commit-progress-section">
              <div className="commit-progress-bar-container">
                <div
                  className="commit-progress-bar"
                  style={{ width: `${progressPercent}%` }}
                />
              </div>
              <div className="commit-progress-info">
                <span className="commit-progress-count">
                  {commitProgress.current} / {commitProgress.total}
                </span>
                <span className="commit-progress-percent">{progressPercent}%</span>
              </div>
              <div className="commit-progress-operation">
                {commitProgress.currentOperation}
              </div>
            </div>
          ) : hasChanges ? (
            <>
              <p className="edit-mode-dialog-summary-intro">
                You have {summary.totalChanges} pending change{summary.totalChanges !== 1 ? 's' : ''}:
              </p>
              <ul className="edit-mode-dialog-summary">
                {summary.channelNumberChanges > 0 && (
                  <li>
                    {summary.channelNumberChanges} channel number change{summary.channelNumberChanges !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.channelNameChanges > 0 && (
                  <li>
                    {summary.channelNameChanges} channel name change{summary.channelNameChanges !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.streamsAdded > 0 && (
                  <li>
                    {summary.streamsAdded} stream{summary.streamsAdded !== 1 ? 's' : ''} added
                  </li>
                )}
                {summary.streamsRemoved > 0 && (
                  <li>
                    {summary.streamsRemoved} stream{summary.streamsRemoved !== 1 ? 's' : ''} removed
                  </li>
                )}
                {summary.streamsReordered > 0 && (
                  <li>
                    {summary.streamsReordered} stream reorder{summary.streamsReordered !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.epgChanges > 0 && (
                  <li>
                    {summary.epgChanges} EPG assignment{summary.epgChanges !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.gracenoteIdChanges > 0 && (
                  <li>
                    {summary.gracenoteIdChanges} Gracenote ID{summary.gracenoteIdChanges !== 1 ? 's' : ''} assigned
                  </li>
                )}
                {summary.logoChanges > 0 && (
                  <li>
                    {summary.logoChanges} logo change{summary.logoChanges !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.streamProfileChanges > 0 && (
                  <li>
                    {summary.streamProfileChanges} stream profile change{summary.streamProfileChanges !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.groupMoves > 0 && (
                  <li>
                    {summary.groupMoves} channel{summary.groupMoves !== 1 ? 's' : ''} moved to another group
                  </li>
                )}
                {summary.otherChannelChanges > 0 && (
                  <li>
                    {summary.otherChannelChanges} other channel change{summary.otherChannelChanges !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.newChannels > 0 && (
                  <li>
                    {summary.newChannels} new channel{summary.newChannels !== 1 ? 's' : ''} created
                  </li>
                )}
                {summary.newGroups > 0 && (
                  <li>
                    {summary.newGroups} new group{summary.newGroups !== 1 ? 's' : ''} created
                  </li>
                )}
                {summary.deletedChannels > 0 && (
                  <li>
                    {summary.deletedChannels} channel{summary.deletedChannels !== 1 ? 's' : ''} deleted
                  </li>
                )}
                {summary.deletedGroups > 0 && (
                  <li>
                    {summary.deletedGroups} group{summary.deletedGroups !== 1 ? 's' : ''} deleted
                  </li>
                )}
                {summary.renamedGroups > 0 && (
                  <li>
                    {summary.renamedGroups} group{summary.renamedGroups !== 1 ? 's' : ''} renamed
                  </li>
                )}
                {/* The actions that used to bypass this dialog entirely, because
                    they had already been applied by the time it opened
                    (bead enhancedchannelmanager-kz089). `totalChanges` sums
                    every bucket, so a line missing here is a headline that
                    disagrees with its own list. */}
                {summary.profileVisibilityChanges > 0 && (
                  <li>
                    {summary.profileVisibilityChanges} profile visibility change
                    {summary.profileVisibilityChanges !== 1 ? 's' : ''}
                  </li>
                )}
                {summary.restoredGroups > 0 && (
                  <li>
                    {summary.restoredGroups} hidden group{summary.restoredGroups !== 1 ? 's' : ''} restored
                  </li>
                )}
                {summary.clearedStreamStats > 0 && (
                  <li>
                    Probe stats cleared for {summary.clearedStreamStats} stream
                    {summary.clearedStreamStats !== 1 ? 's' : ''}
                  </li>
                )}
              </ul>

              {/* Toggle for detailed change list */}
              <button
                className="edit-mode-dialog-toggle"
                onClick={() => setShowDetails(!showDetails)}
                type="button"
              >
                <span className="material-icons toggle-icon">
                  {showDetails ? 'expand_less' : 'expand_more'}
                </span>
                {showDetails ? 'Hide details' : 'Show details'}
              </button>

              {/* Detailed change list */}
              {showDetails && summary.operationDetails && (
                <div className="edit-mode-dialog-details">
                  {summary.operationDetails.map((op, index) => (
                    <div key={op.id} className="operation-detail">
                      <span className="operation-number">{index + 1}.</span>
                      <span className="operation-description">{op.description}</span>
                    </div>
                  ))}
                </div>
              )}

              <p className="edit-mode-dialog-question">
                Would you like to apply these changes?
              </p>
            </>
          ) : (
            <p className="edit-mode-dialog-no-changes">
              No changes were made during this edit session.
            </p>
          )}
        </div>

        {!isCommitting && (
          <div className="edit-mode-dialog-actions">
            <button
              className="edit-mode-dialog-btn secondary"
              onClick={onKeepEditing}
            >
              Keep Editing
            </button>
            <button
              className="edit-mode-dialog-btn danger"
              onClick={onDiscard}
            >
              Discard
            </button>
            {hasChanges && (
              <button
                className="edit-mode-dialog-btn primary"
                onClick={onApply}
              >
                Apply All
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
