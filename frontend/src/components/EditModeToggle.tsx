import type { EditModeToggleProps } from '../types';
import './EditMode.css';

export function EditModeToggle({
  isEditMode,
  stagedCount,
  onEnter,
  onExit,
  disabled = false,
}: EditModeToggleProps) {
  return (
    <button
      className={`edit-mode-toggle ${isEditMode ? 'active' : ''}`}
      onClick={isEditMode ? onExit : onEnter}
      disabled={disabled}
      title={isEditMode ? 'Exit Edit Mode' : 'Enter Edit Mode'}
    >
      <span className="material-icons edit-mode-icon">{isEditMode ? 'check' : 'edit'}</span>
      <span className="edit-mode-label">
        {isEditMode ? 'Done' : 'Edit Mode'}
      </span>
      {isEditMode && stagedCount > 0 && (
        <span className="edit-mode-count">{stagedCount}</span>
      )}
    </button>
  );
}
