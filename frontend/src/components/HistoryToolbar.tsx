import { useState, useRef, useEffect } from 'react';
import type { ChangeRecord, SavePoint } from '../types';
import { ModalOverlay } from './ModalOverlay';
import './HistoryToolbar.css';

interface HistoryToolbarProps {
  canUndo: boolean;
  canRedo: boolean;
  undoCount: number;
  redoCount: number;
  lastChange: ChangeRecord | null;
  savePoints: SavePoint[];
  hasUnsavedChanges: boolean;
  isOperationPending: boolean;
  onUndo: () => void;
  onRedo: () => void;
  onCreateSavePoint: (name?: string) => void;
  onRevertToSavePoint: (id: string) => void;
  onDeleteSavePoint: (id: string) => void;
  isEditMode?: boolean;
}

export function HistoryToolbar({
  canUndo,
  canRedo,
  undoCount,
  redoCount,
  lastChange,
  savePoints,
  hasUnsavedChanges,
  isOperationPending,
  onUndo,
  onRedo,
  onCreateSavePoint,
  onRevertToSavePoint,
  onDeleteSavePoint,
  isEditMode = false,
}: HistoryToolbarProps) {
  const [savePointDropdownOpen, setSavePointDropdownOpen] = useState(false);
  const [showNameModal, setShowNameModal] = useState(false);
  const [checkpointName, setCheckpointName] = useState('');
  const dropdownRef = useRef<HTMLDivElement>(null);
  const nameInputRef = useRef<HTMLInputElement>(null);

  // Close dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setSavePointDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Focus input when modal opens
  useEffect(() => {
    if (showNameModal && nameInputRef.current) {
      nameInputRef.current.focus();
    }
  }, [showNameModal]);

  const handleCreateSavePoint = () => {
    setCheckpointName('');
    setShowNameModal(true);
  };

  const handleConfirmCheckpoint = () => {
    const name = checkpointName.trim() || `Checkpoint ${savePoints.length + 1}`;
    onCreateSavePoint(name);
    setShowNameModal(false);
    setCheckpointName('');
  };

  const handleCancelCheckpoint = () => {
    setShowNameModal(false);
    setCheckpointName('');
  };

  const handleNameKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleConfirmCheckpoint();
    } else if (e.key === 'Escape') {
      handleCancelCheckpoint();
    }
  };

  const formatTime = (timestamp: number) => {
    return new Date(timestamp).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  const undoTitle = isEditMode
    ? `Undo staged change (Cmd+Z)`
    : lastChange
      ? `Undo: ${lastChange.description} (Cmd+Z)`
      : 'Undo (Cmd+Z)';

  const redoTitle = isEditMode
    ? 'Redo staged change (Cmd+Shift+Z)'
    : 'Redo (Cmd+Shift+Z)';

  return (
    <div className="history-toolbar">
      {/* Undo Button */}
      <button
        className="history-btn"
        onClick={onUndo}
        disabled={!canUndo || isOperationPending}
        title={undoTitle}
      >
        <span className="material-icons history-icon">undo</span>
        {undoCount > 0 && <span className="history-count">{undoCount}</span>}
      </button>

      {/* Redo Button */}
      <button
        className="history-btn"
        onClick={onRedo}
        disabled={!canRedo || isOperationPending}
        title={redoTitle}
      >
        <span className="material-icons history-icon">redo</span>
        {redoCount > 0 && <span className="history-count">{redoCount}</span>}
      </button>

      <div className="history-divider" />

      {/* Save Point Button */}
      <button
        className="history-btn save-point-btn"
        onClick={handleCreateSavePoint}
        disabled={isOperationPending}
        title="Create checkpoint"
      >
        <span className="material-icons save-point-icon">bookmark_add</span>
      </button>

      {/* Save Points Dropdown */}
      {savePoints.length > 0 && (
        <div className="save-points-dropdown" ref={dropdownRef}>
          <button
            className="history-btn dropdown-trigger"
            onClick={() => setSavePointDropdownOpen(!savePointDropdownOpen)}
            disabled={isOperationPending}
          >
            <span>Checkpoints ({savePoints.length})</span>
            <span className="dropdown-arrow">
              {savePointDropdownOpen ? '\u25B2' : '\u25BC'}
            </span>
          </button>

          {savePointDropdownOpen && (
            <div className="save-points-menu">
              {savePoints.map((sp) => (
                <div key={sp.id} className="save-point-item">
                  <div className="save-point-info">
                    <span className="save-point-name">{sp.name}</span>
                    <span className="save-point-time">{formatTime(sp.timestamp)}</span>
                  </div>
                  <div className="save-point-actions">
                    <button
                      className="save-point-action revert"
                      onClick={() => {
                        onRevertToSavePoint(sp.id);
                        setSavePointDropdownOpen(false);
                      }}
                      title="Revert to this checkpoint"
                    >
                      Revert
                    </button>
                    <button
                      className="save-point-action delete"
                      onClick={() => onDeleteSavePoint(sp.id)}
                      title="Delete checkpoint"
                    >
                      <span className="material-icons">close</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Unsaved Changes Indicator */}
      {hasUnsavedChanges && (
        <span className="material-icons unsaved-indicator" title="Unsaved changes since last checkpoint">
          fiber_manual_record
        </span>
      )}

      {/* Operation Pending Indicator */}
      {isOperationPending && <span className="pending-indicator">...</span>}

      {/* Checkpoint Name Modal */}
      {showNameModal && (
        <ModalOverlay onClose={handleCancelCheckpoint} className="checkpoint-name-modal-overlay">
          <div className="checkpoint-name-modal">
            <h4>Create Checkpoint</h4>
            <input
              ref={nameInputRef}
              type="text"
              value={checkpointName}
              onChange={(e) => setCheckpointName(e.target.value)}
              onKeyDown={handleNameKeyDown}
              placeholder={`Checkpoint ${savePoints.length + 1}`}
              className="checkpoint-name-input"
            />
            <div className="checkpoint-name-actions">
              <button className="checkpoint-name-btn cancel" onClick={handleCancelCheckpoint}>
                Cancel
              </button>
              <button className="checkpoint-name-btn confirm" onClick={handleConfirmCheckpoint}>
                Create
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}
