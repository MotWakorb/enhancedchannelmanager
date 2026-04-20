import { useState, memo } from 'react';
import './ModalBase.css';
import './DeleteOrphanedGroupsModal.css';
import { ModalOverlay } from './ModalOverlay';

interface OrphanedGroup {
  id: number;
  name: string;
  reason?: string;
}

interface DeleteOrphanedGroupsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (selectedGroupIds: number[]) => void;
  groups: OrphanedGroup[];
}

// Render nothing when closed so reopening gives a fresh mount (state naturally resets).
// This avoids needing an effect to seed selection from `groups` on open.
function DeleteOrphanedGroupsModalInner({
  onClose,
  onConfirm,
  groups,
}: Omit<DeleteOrphanedGroupsModalProps, 'isOpen'>) {
  // Default = all selected. Only track user's explicit changes from that default.
  const [selectedGroups, setSelectedGroups] = useState<Set<number>>(
    () => new Set(groups.map(g => g.id))
  );

  const handleToggle = (groupId: number) => {
    const newSelection = new Set(selectedGroups);
    if (newSelection.has(groupId)) {
      newSelection.delete(groupId);
    } else {
      newSelection.add(groupId);
    }
    setSelectedGroups(newSelection);
  };

  const handleSelectAll = () => {
    setSelectedGroups(new Set(groups.map(g => g.id)));
  };

  const handleSelectNone = () => {
    setSelectedGroups(new Set());
  };

  const handleConfirm = () => {
    onConfirm(Array.from(selectedGroups));
    onClose();
  };

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-md delete-orphaned-modal">
        <div className="modal-header">
          <h2>Delete Orphaned Channel Groups</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          <p className="modal-description">
            Select the channel groups you want to delete. This action cannot be undone.
          </p>

          <div className="modal-toolbar-row selection-controls">
            <div className="modal-toolbar-actions">
              <button
                type="button"
                className="modal-btn-small"
                onClick={handleSelectAll}
              >
                Select All
              </button>
              <button
                type="button"
                className="modal-btn-small"
                onClick={handleSelectNone}
              >
                Select None
              </button>
            </div>
            <span className="modal-toolbar-count">
              {selectedGroups.size} of {groups.length} selected
            </span>
          </div>

          <div className="groups-list">
            {groups.map(group => (
              <label key={group.id} className="group-item">
                <input
                  type="checkbox"
                  checked={selectedGroups.has(group.id)}
                  onChange={() => handleToggle(group.id)}
                />
                <div className="group-info">
                  <div className="group-name">{group.name}</div>
                  <div className="group-details">
                    ID: {group.id}
                    {group.reason && (
                      <span className="group-reason"> • {group.reason}</span>
                    )}
                  </div>
                </div>
              </label>
            ))}
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="modal-btn modal-btn-danger"
            onClick={handleConfirm}
            disabled={selectedGroups.size === 0}
          >
            <span className="material-icons">delete_forever</span>
            Delete {selectedGroups.size} Group{selectedGroups.size !== 1 ? 's' : ''}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}

export const DeleteOrphanedGroupsModal = memo(function DeleteOrphanedGroupsModal(
  props: DeleteOrphanedGroupsModalProps,
) {
  if (!props.isOpen) return null;
  // Conditional render + Inner component means each open is a fresh mount, so the
  // "default = all selected" state is seeded by useState() rather than a setState
  // effect fighting with user interaction.
  return (
    <DeleteOrphanedGroupsModalInner
      onClose={props.onClose}
      onConfirm={props.onConfirm}
      groups={props.groups}
    />
  );
});
