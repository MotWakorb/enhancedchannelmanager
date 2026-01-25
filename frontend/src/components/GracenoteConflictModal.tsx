/**
 * Gracenote Conflict Resolution Modal
 *
 * Shown when attempting to assign Gracenote IDs to channels that already
 * have different Gracenote IDs. Allows user to skip, overwrite, or choose
 * individually which channels to update.
 */

import { useState, useMemo, memo } from 'react';
import './ModalBase.css';
import './GracenoteConflictModal.css';

export interface GracenoteConflict {
  channelId: number;
  channelName: string;
  oldGracenoteId: string;
  newGracenoteId: string;
}

interface GracenoteConflictModalProps {
  isOpen: boolean;
  conflicts: GracenoteConflict[];
  onResolve: (channelsToUpdate: number[]) => void;
  onCancel: () => void;
}

export const GracenoteConflictModal = memo(function GracenoteConflictModal({
  isOpen,
  conflicts,
  onResolve,
  onCancel,
}: GracenoteConflictModalProps) {
  // Track which conflicts to overwrite (by channel ID)
  const [selectedForOverwrite, setSelectedForOverwrite] = useState<Set<number>>(new Set());

  // Count of selected conflicts
  const selectedCount = useMemo(() => selectedForOverwrite.size, [selectedForOverwrite]);

  // Handle select/deselect individual conflict
  const toggleSelection = (channelId: number) => {
    const newSet = new Set(selectedForOverwrite);
    if (newSet.has(channelId)) {
      newSet.delete(channelId);
    } else {
      newSet.add(channelId);
    }
    setSelectedForOverwrite(newSet);
  };

  // Handle select all
  const selectAll = () => {
    setSelectedForOverwrite(new Set(conflicts.map(c => c.channelId)));
  };

  // Handle deselect all (skip all)
  const deselectAll = () => {
    setSelectedForOverwrite(new Set());
  };

  // Handle overwrite selected
  const handleOverwrite = () => {
    onResolve(Array.from(selectedForOverwrite));
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-container gracenote-conflict-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Gracenote ID Conflicts</h2>
          <button className="modal-close-btn" onClick={onCancel}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          <div className="conflict-info">
            <span className="material-icons info-icon">info</span>
            <p>
              {conflicts.length} channel{conflicts.length !== 1 ? 's' : ''} already{conflicts.length === 1 ? '' : ' each'} have a Gracenote ID.
              Select which channels to overwrite with the new ID.
            </p>
          </div>

          <div className="conflict-actions">
            <button className="modal-btn modal-btn-secondary" onClick={selectAll}>
              Select All
            </button>
            <button className="modal-btn modal-btn-secondary" onClick={deselectAll}>
              Deselect All
            </button>
          </div>

          <div className="conflicts-list">
            {conflicts.map(conflict => (
              <div key={conflict.channelId} className="conflict-item">
                <input
                  type="checkbox"
                  checked={selectedForOverwrite.has(conflict.channelId)}
                  onChange={() => toggleSelection(conflict.channelId)}
                  id={`conflict-${conflict.channelId}`}
                />
                <label htmlFor={`conflict-${conflict.channelId}`} className="conflict-item-content">
                  <div className="conflict-channel-name">{conflict.channelName}</div>
                  <div className="conflict-ids">
                    <span className="old-id">
                      <span className="id-label">Current:</span>
                      <span className="id-value">{conflict.oldGracenoteId}</span>
                    </span>
                    <span className="material-icons arrow">arrow_forward</span>
                    <span className="new-id">
                      <span className="id-label">New:</span>
                      <span className="id-value">{conflict.newGracenoteId}</span>
                    </span>
                  </div>
                </label>
              </div>
            ))}
          </div>
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn-primary"
            onClick={handleOverwrite}
            disabled={selectedCount === 0}
          >
            Overwrite {selectedCount} Channel{selectedCount !== 1 ? 's' : ''}
          </button>
        </div>
      </div>
    </div>
  );
});
