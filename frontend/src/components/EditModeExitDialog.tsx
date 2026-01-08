import { useState } from 'react';
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
}: EditModeExitDialogProps) {
  const [showDetails, setShowDetails] = useState(false);

  if (!isOpen) return null;

  const hasChanges = summary.totalOperations > 0;

  // Calculate progress percentage
  const progressPercent = commitProgress
    ? Math.round((commitProgress.current / commitProgress.total) * 100)
    : 0;

  return (
    <div className="edit-mode-dialog-overlay" onClick={isCommitting ? undefined : onKeepEditing}>
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
                You have {summary.totalOperations} pending change{summary.totalOperations !== 1 ? 's' : ''}:
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
